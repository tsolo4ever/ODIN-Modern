"""Headless regression checks for partition-aware target verification."""

import ctypes
import ctypes.wintypes as wintypes
import io
import sys
import tempfile
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as app_module  # noqa: E402
import hash_config as hash_config_module  # noqa: E402
import partition_reader as partition_reader_module  # noqa: E402
import raw_disk as raw_disk_module  # noqa: E402
from clone_worker import CloneStatus  # noqa: E402
from hash_worker import HashStatus  # noqa: E402
from partition_reader import (  # noqa: E402
    ImageHashRegion,
    PartitionInfo,
    PartitionReadError,
    read_mbr_partitions_strict,
)

_REAL_HASH_CONFIG = hash_config_module.HashConfig


class _FakeRawKernel:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.position = 0
        self.reads: list[tuple[int, int]] = []

    def SetFilePointerEx(self, _handle, offset, new_position, _method):
        self.position = int(offset.value)
        if new_position:
            ctypes.cast(
                new_position, ctypes.POINTER(ctypes.c_longlong)
            ).contents.value = self.position
        return True

    def ReadFile(self, _handle, buffer, size, bytes_read, _overlapped):
        self.reads.append((self.position, size))
        data = self.payload[self.position : self.position + size]
        ctypes.memmove(buffer, data, len(data))
        ctypes.cast(bytes_read, ctypes.POINTER(wintypes.DWORD)).contents.value = len(data)
        self.position += len(data)
        return True


def _raw_reader(payload: bytes, *, sector_size: int = 512):
    reader = raw_disk_module.Win32RawDiskReader.__new__(
        raw_disk_module.Win32RawDiskReader
    )
    reader.path = r"\\.\PhysicalDrive2"
    reader._k32 = _FakeRawKernel(payload)
    reader._handle = 1
    reader._sector_size = sector_size
    reader._position = 0
    return reader


def _cfg(*, sha1: str = "", sha256: str = "") -> dict:
    return {
        "sha1_value": sha1,
        "sha1_enabled": bool(sha1),
        "sha1_fail": True,
        "sha256_value": sha256,
        "sha256_enabled": bool(sha256),
        "sha256_fail": True,
    }


class _FakeHashConfig:
    def __init__(self, enabled: dict[int, dict]):
        self.enabled = enabled

    def get_enabled_partitions(self, _image: str) -> dict[int, dict]:
        return self.enabled


class _FakeWindow:
    def __init__(self):
        self.image_path = r"E:\working.img"
        self.logs: list[str] = []
        self.slot_statuses: list[CloneStatus] = []
        self.verifying: list[int] = []
        self.progress: list[int] = []

    def log(self, message: str):
        self.logs.append(message)

    def set_slot_status(self, _idx: int, status: CloneStatus):
        self.slot_statuses.append(status)

    def set_slot_progress(self, _idx: int, pct: int):
        self.progress.append(pct)

    def set_slot_verifying(self, idx: int):
        self.verifying.append(idx)


class _FakeWorker:
    instances: list["_FakeWorker"] = []

    def __init__(
        self,
        *,
        root,
        file_path: str,
        on_progress,
        on_done,
        offset: int,
        byte_count: int,
    ):
        self.root = root
        self.file_path = file_path
        self.on_progress = on_progress
        self.on_done = on_done
        self.offset = offset
        self.byte_count = byte_count
        self.started = False
        self.instances.append(self)

    def start(self):
        self.started = True

    def finish(self, *, sha256: str = "", sha1: str = "", status=HashStatus.DONE):
        self.on_done(status, sha256, sha1)


_WAIT_PARTITIONS: list[PartitionInfo] = []


class _FakeWaiter:
    def __init__(self, *, root, disk_path, on_ready, on_failed, **_kwargs):
        self.on_ready = on_ready
        self.on_failed = on_failed
        self.stopped = False

    def start(self):
        if _WAIT_PARTITIONS:
            self.on_ready(_WAIT_PARTITIONS)
        else:
            self.on_failed("test partition table was not ready")

    def stop(self):
        self.stopped = True


class _FakeConfig:
    def get_stop_on_verify_fail(self) -> bool:
        return False


app_module.HashWorker = _FakeWorker
app_module.PartitionTableWaiter = _FakeWaiter


def _make_app():
    app = object.__new__(app_module.OdinMApp)
    app._root = SimpleNamespace()
    app._window = _FakeWindow()
    app._drives = [SimpleNamespace(disk_number=2, raw_device_path=r"\\.\PhysicalDrive2")]
    app._verify_workers = {}
    app._partition_waiters = {}
    app._finished_disk_nums = set()
    app._config = _FakeConfig()
    app._flash_statuses = []
    app._flash_progress = []
    app._signature_fixes = []
    app._drains = []
    app._flash_set_status = lambda _idx, status: app._flash_statuses.append(status)
    app._flash_set_progress = lambda _idx, pct: app._flash_progress.append(pct)
    app._fix_disk_signature = lambda idx: app._signature_fixes.append(idx)
    app._drain_queue = lambda: app._drains.append(True)
    return app


def _use_hash_config(config: _FakeHashConfig):
    hash_config_module.HashConfig = lambda: config


def test_multiple_partition_checks_run_in_order():
    global _WAIT_PARTITIONS
    part1_cfg = _cfg(sha256="a" * 64)
    part2_cfg = _cfg(sha1="b" * 40)
    config = _FakeHashConfig({1: part1_cfg, 2: part2_cfg})
    _use_hash_config(config)
    _WAIT_PARTITIONS = [
        PartitionInfo(1, 0x0B, 1_048_576, 4_096, True),
        PartitionInfo(2, 0x83, 5_242_880, 8_192, False),
    ]
    app_module.get_image_hash_region = lambda _path: (_ for _ in ()).throw(
        AssertionError("disk-level fallback must not run")
    )
    _FakeWorker.instances.clear()
    app = _make_app()

    assert app._start_target_verify(0)
    first = _FakeWorker.instances[0]
    assert (first.offset, first.byte_count) == (1_048_576, 4_096)
    assert first.file_path == r"\\.\PhysicalDrive2"

    first.finish(sha256="a" * 64)
    assert app._signature_fixes == []
    assert len(_FakeWorker.instances) == 2
    second = _FakeWorker.instances[1]
    assert (second.offset, second.byte_count) == (5_242_880, 8_192)

    second.finish(sha1="b" * 40)
    assert app._signature_fixes == [0]
    assert app._finished_disk_nums == {2}
    assert app._drains == [True]
    assert any("Target partition 1 SHA-256: pass." in line for line in app._window.logs)
    assert any("Target partition 2 SHA-1: pass." in line for line in app._window.logs)


def test_mixed_whole_disk_and_partition_scopes_are_rejected():
    config = _FakeHashConfig({0: _cfg(sha256="d" * 64), 1: _cfg(sha256="a" * 64)})
    _use_hash_config(config)
    app_module.read_mbr_partitions = lambda _path: (_ for _ in ()).throw(
        AssertionError("mixed scope must fail before reading the target")
    )
    _FakeWorker.instances.clear()
    app = _make_app()

    assert not app._start_target_verify(0)
    assert _FakeWorker.instances == []
    assert app._window.slot_statuses == [CloneStatus.FAILED]
    assert any("both enabled" in line for line in app._window.logs)


def test_no_enabled_hashes_requires_configuration():
    _use_hash_config(_FakeHashConfig({}))
    app_module.read_mbr_partitions = lambda _path: (_ for _ in ()).throw(
        AssertionError("no scope must fail before reading the target")
    )
    app_module.get_image_hash_region = lambda _path: (_ for _ in ()).throw(
        AssertionError("no scope must not fall back to whole-disk verification")
    )
    _FakeWorker.instances.clear()
    app = _make_app()

    assert not app._start_target_verify(0)
    assert _FakeWorker.instances == []
    assert app._window.slot_statuses == [CloneStatus.FAILED]
    assert any("Configure hash verification first" in line for line in app._window.logs)


def test_missing_target_partition_fails_without_hashing():
    global _WAIT_PARTITIONS
    config = _FakeHashConfig({1: _cfg(sha256="a" * 64)})
    _use_hash_config(config)
    _WAIT_PARTITIONS = [PartitionInfo(2, 0x83, 1_048_576, 4_096, False)]
    _FakeWorker.instances.clear()
    app = _make_app()

    assert app._start_target_verify(0)
    assert _FakeWorker.instances == []
    assert app._window.slot_statuses == [CloneStatus.FAILED]
    assert any("Partition 1 was not found" in line for line in app._window.logs)


def test_partition_mismatch_never_randomizes_signature():
    global _WAIT_PARTITIONS
    config = _FakeHashConfig({1: _cfg(sha256="a" * 64)})
    _use_hash_config(config)
    _WAIT_PARTITIONS = [PartitionInfo(1, 0x0B, 1_048_576, 4_096, True)]
    _FakeWorker.instances.clear()
    app = _make_app()

    assert app._start_target_verify(0)
    _FakeWorker.instances[0].finish(sha256="f" * 64)
    assert app._signature_fixes == []
    assert app._window.slot_statuses == [CloneStatus.FAILED]
    assert app._drains == [True]


def test_explicit_disk_level_verification_is_preserved():
    disk_cfg = _cfg(sha256="d" * 64)
    config = _FakeHashConfig({0: disk_cfg})
    _use_hash_config(config)
    app_module.read_mbr_partitions = lambda _path: (_ for _ in ()).throw(
        AssertionError("target partition table must not be read for disk fallback")
    )
    app_module.get_image_hash_region = lambda _path: ImageHashRegion(
        offset=128, size=16_384, is_odin=True
    )
    _FakeWorker.instances.clear()
    app = _make_app()

    assert app._start_target_verify(0)
    worker = _FakeWorker.instances[0]
    assert (worker.offset, worker.byte_count) == (0, 16_384)

    worker.finish(sha256="d" * 64)
    assert app._signature_fixes == [0]
    assert app._drains == [True]


def test_saving_hash_scopes_keeps_whole_disk_and_partitions_exclusive():
    config = object.__new__(_REAL_HASH_CONFIG)
    config._path = ""
    config._data = {}
    config._save = lambda: True
    image = r"E:\working.img"
    part1_cfg = _cfg(sha256="a" * 64)
    part2_cfg = _cfg(sha1="b" * 40)
    disk_cfg = _cfg(sha256="d" * 64)

    assert config.save_partition(image, 1, part1_cfg)
    assert config.save_partition(image, 2, part2_cfg)
    assert sorted(config.get_enabled_partitions(image)) == [1, 2]

    assert config.save_partition(image, 0, disk_cfg)
    assert sorted(config.get_enabled_partitions(image)) == [0]
    assert config.get_partition(image, 1)["sha256_value"] == "a" * 64
    assert config.get_partition(image, 2)["sha1_value"] == "b" * 40

    assert config.save_partition(image, 1, part1_cfg)
    assert sorted(config.get_enabled_partitions(image)) == [1]
    assert config.save_partition(image, 2, part2_cfg)
    assert sorted(config.get_enabled_partitions(image)) == [1, 2]


def test_strict_partition_reader_preserves_invalid_mbr_reason():
    with tempfile.TemporaryDirectory() as temp_dir:
        image = Path(temp_dir) / "invalid.img"
        image.write_bytes(b"\x00" * 512)
        try:
            read_mbr_partitions_strict(str(image))
        except PartitionReadError as exc:
            assert "MBR signature" in str(exc)
        else:
            raise AssertionError("invalid MBR should raise PartitionReadError")


def test_strict_partition_reader_treats_physical_device_path_as_raw():
    payload = bytearray(512)
    payload[446] = 0x80
    payload[450] = 0x0B
    payload[454:458] = (2048).to_bytes(4, "little")
    payload[458:462] = (4096).to_bytes(4, "little")
    payload[510:512] = b"\x55\xaa"
    real_open = partition_reader_module.open_binary_reader
    real_header = partition_reader_module.read_odin_header
    partition_reader_module.open_binary_reader = lambda _path: nullcontext(
        io.BytesIO(payload)
    )
    partition_reader_module.read_odin_header = lambda _path: (_ for _ in ()).throw(
        AssertionError("physical devices must skip ODIN header detection")
    )
    try:
        partitions = partition_reader_module.read_mbr_partitions_strict(
            r"\\.\PhysicalDrive3"
        )
    finally:
        partition_reader_module.open_binary_reader = real_open
        partition_reader_module.read_odin_header = real_header
    assert len(partitions) == 1
    assert partitions[0].offset == 2048 * 512
    assert partitions[0].size == 4096 * 512


def test_raw_physical_reader_aligns_small_partition_reads():
    payload = bytes(index % 251 for index in range(8192))
    reader = _raw_reader(payload, sector_size=4096)

    reader.seek(510)
    assert reader.read(2) == payload[510:512]
    assert reader.tell() == 512
    assert reader._k32.reads == [(0, 4096)]

    reader.seek(4090)
    assert reader.read(16) == payload[4090:4106]
    assert reader.tell() == 4106
    assert reader._k32.reads[-1] == (0, 8192)

if __name__ == "__main__":
    tests = [
        test_multiple_partition_checks_run_in_order,
        test_mixed_whole_disk_and_partition_scopes_are_rejected,
        test_no_enabled_hashes_requires_configuration,
        test_missing_target_partition_fails_without_hashing,
        test_partition_mismatch_never_randomizes_signature,
        test_explicit_disk_level_verification_is_preserved,
        test_saving_hash_scopes_keeps_whole_disk_and_partitions_exclusive,
        test_strict_partition_reader_preserves_invalid_mbr_reason,
        test_strict_partition_reader_treats_physical_device_path_as_raw,
        test_raw_physical_reader_aligns_small_partition_reads,
    ]
    for test in tests:
        test()
        print(f"[ok] {test.__name__}")
    print(f"\n{len(tests)}/{len(tests)} checks passed")
