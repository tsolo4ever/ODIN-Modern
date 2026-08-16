"""Headless destructive-path simulations for guarded preflight and restore."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import struct
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import compact_image  # noqa: E402
import guarded_restore as restore  # noqa: E402
from guarded_flash_safety import (  # noqa: E402
    DiskIdentity,
    ProtectedHardwareStore,
    ProtectedRecord,
)


def _disk(size: int = 4096) -> DiskIdentity:
    return DiskIdentity(
        disk_number=3,
        unique_id="TARGET-3",
        serial="SERIAL-3",
        model="FIXED TEST SSD",
        manufacturer="TEST",
        size_bytes=size,
        bus_type=11,
        bus_name="SATA",
        device_path=r"\\?\scsi#disk3",
        location="Port 3",
        mounted_volumes=("E:\\",),
    )


def _store(folder: str) -> ProtectedHardwareStore:
    return ProtectedHardwareStore(Path(folder) / "protected.json")


def _raw(path: Path, size: int = 1024) -> bytes:
    data = bytes((index * 17) % 251 for index in range(size))
    path.write_bytes(data)
    return data


def _compact(path: Path, disk_size: int = 4096) -> tuple[bytes, Path]:
    disk = bytearray(disk_size)
    disk[440:444] = b"TEST"
    entry = bytearray(16)
    entry[4] = 0x07
    struct.pack_into("<I", entry, 8, 1)
    struct.pack_into("<I", entry, 12, 2)
    disk[446:462] = entry
    disk[510:512] = b"\x55\xaa"
    layout = compact_image.parse_mbr_layout(io.BytesIO(disk), disk_size=disk_size)
    captured = bytes(disk[: layout.capture_bytes])
    path.write_bytes(captured)
    digest = hashlib.sha256(captured).hexdigest()
    manifest = compact_image.build_manifest(
        layout,
        {
            "source": "PhysicalDrive7",
            "bytes_written": len(captured),
            "bad_sector_count": 0,
            "digests": {"sha256": digest},
        },
    )
    manifest_path = compact_image.compact_manifest_path(path)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return captured, manifest_path


class FakeDisk:
    def __init__(self, storage: bytearray, write: bool = False, *, short_write: bool = False):
        self.storage = storage
        self.writable = write
        self.position = 0
        self.short_write = short_write
        self.h = 1

    @property
    def size(self):
        return len(self.storage)

    @property
    def sector_size(self):
        return 512

    def seek(self, offset):
        self.position = offset

    def read(self, count):
        data = bytes(self.storage[self.position : self.position + count])
        self.position += len(data)
        return data

    def write(self, data):
        count = len(data) - 1 if self.short_write else len(data)
        self.storage[self.position : self.position + count] = data[:count]
        self.position += count
        return count

    def update_properties(self):
        return True

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


class FakeVolume:
    def __init__(self, events):
        self.events = events

    def unlock(self):
        self.events.append("unlock")

    def close(self):
        self.events.append("close")


def _restore(
    plan,
    folder,
    *,
    storage=None,
    short_write=False,
    cancel=None,
    protected=False,
    partition_waiter=None,
):
    disk = _disk()
    storage = storage if storage is not None else bytearray(disk.size_bytes)
    events = []
    store = _store(folder)
    if protected:
        store.save([ProtectedRecord.from_disk(disk, "2026-08-16T00:00:00+00:00")])

    def factory(_path, write=False):
        events.append("open-write" if write else "open-read")
        return FakeDisk(storage, write, short_write=short_write and write)

    result = restore.restore_and_verify(
        plan,
        disk,
        store,
        confirmed_disk_number=3,
        inventory_provider=lambda: [disk],
        image_disk_provider=lambda _path: 0,
        disk_factory=factory,
        volume_locker=lambda letters, on_log=None: [FakeVolume(events)],
        flush_disk=lambda _target: events.append("flush"),
        partition_waiter=partition_waiter or (lambda _path: events.append("partition-ready")),
        should_cancel=cancel,
    )
    return result, storage, events


def test_raw_preflight_hashes_exact_source_and_rejects_oversize():
    with tempfile.TemporaryDirectory() as folder:
        image = Path(folder) / "raw.img"
        data = _raw(image)
        plan = restore.preflight_image(image, 4096)
        assert plan.sha256 == hashlib.sha256(data).hexdigest()
        assert plan.write_bytes == len(data)
        try:
            restore.preflight_image(image, 512)
        except restore.GuardedImageError:
            pass
        else:
            raise AssertionError("oversized image was accepted")


def test_gzip_is_fully_spooled_and_cleanup_is_explicit():
    with tempfile.TemporaryDirectory() as folder:
        image = Path(folder) / "raw.img.gz"
        data = bytes(range(256)) * 4
        with gzip.open(image, "wb") as stream:
            stream.write(data)
        plan = restore.preflight_image(image, 4096)
        assert plan.temporary_source
        assert plan.source_path.read_bytes() == data
        assert plan.sha256 == hashlib.sha256(data).hexdigest()
        plan.cleanup()
        assert not plan.source_path.exists()


def test_compact_manifest_layout_hash_and_original_capacity_are_required():
    with tempfile.TemporaryDirectory() as folder:
        image = Path(folder) / "roulette.compact.img"
        data, _manifest = _compact(image)
        plan = restore.preflight_image(image, 4096)
        assert plan.image_format == "compact"
        assert plan.write_bytes == len(data)
        assert plan.required_capacity == 4096
        try:
            restore.preflight_image(image, 2048)
        except restore.GuardedImageError as exc:
            assert "original source capacity" in str(exc)
        else:
            raise AssertionError("undersized compact target was accepted")


def test_missing_or_altered_compact_pair_is_rejected():
    with tempfile.TemporaryDirectory() as folder:
        image = Path(folder) / "roulette.compact.img"
        _compact(image)
        manifest = compact_image.compact_manifest_path(image)
        manifest.unlink()
        try:
            restore.preflight_image(image, 4096)
        except restore.GuardedImageError:
            pass
        else:
            raise AssertionError("missing compact manifest was accepted")
        _compact(image)
        data = bytearray(image.read_bytes())
        data[-1] ^= 0xFF
        image.write_bytes(data)
        try:
            restore.preflight_image(image, 4096)
        except restore.GuardedImageError:
            pass
        else:
            raise AssertionError("altered compact image was accepted")


def test_source_change_after_preflight_is_rejected():
    with tempfile.TemporaryDirectory() as folder:
        image = Path(folder) / "raw.img"
        _raw(image)
        plan = restore.preflight_image(image, 4096)
        image.write_bytes(b"X" * 1024)
        try:
            restore.validate_source_unchanged(plan)
        except restore.GuardedImageError:
            pass
        else:
            raise AssertionError("changed preflight source was accepted")


def test_success_requires_flush_refresh_and_matching_readback():
    with tempfile.TemporaryDirectory() as folder:
        image = Path(folder) / "raw.img"
        data = _raw(image)
        plan = restore.preflight_image(image, 4096)
        result, storage, events = _restore(plan, folder)
        assert result.verified
        assert storage[: len(data)] == data
        assert events == ["open-read", "open-write", "flush", "unlock", "close", "partition-ready", "open-read"]


def test_bad_confirmation_and_protected_target_never_open_a_disk():
    with tempfile.TemporaryDirectory() as folder:
        image = Path(folder) / "raw.img"
        _raw(image)
        plan = restore.preflight_image(image, 4096)
        disk = _disk()
        opened = []
        try:
            restore.restore_and_verify(
                plan, disk, _store(folder), confirmed_disk_number=2,
                disk_factory=lambda *_args, **_kwargs: opened.append(True),
            )
        except restore.GuardedRestoreError:
            pass
        assert not opened
        try:
            _restore(plan, folder, protected=True)
        except restore.GuardedRestoreError:
            pass
        else:
            raise AssertionError("protected target was written")


def test_short_write_cannot_be_reported_as_success():
    with tempfile.TemporaryDirectory() as folder:
        image = Path(folder) / "raw.img"
        _raw(image)
        plan = restore.preflight_image(image, 4096)
        try:
            _restore(plan, folder, short_write=True)
        except restore.GuardedRestoreError as exc:
            assert exc.target_not_trusted
        else:
            raise AssertionError("short write was reported as success")


def test_partition_refresh_failure_marks_the_written_target_untrusted():
    with tempfile.TemporaryDirectory() as folder:
        image = Path(folder) / "raw.img"
        _raw(image)
        plan = restore.preflight_image(image, 4096)

        def fail_refresh(_path):
            raise OSError("simulated refresh timeout")

        try:
            _restore(plan, folder, partition_waiter=fail_refresh)
        except restore.GuardedRestoreError as exc:
            assert exc.target_not_trusted
            assert "partition table" in str(exc)
        else:
            raise AssertionError("partition refresh failure was reported as success")


def test_cancellation_reports_partial_target_and_never_verifies():
    with tempfile.TemporaryDirectory() as folder:
        image = Path(folder) / "raw.img"
        _raw(image, 1024)
        plan = restore.preflight_image(image, 4096)
        checks = 0

        def cancel():
            nonlocal checks
            checks += 1
            return checks > 1

        original_chunk = restore.CHUNK_BYTES
        restore.CHUNK_BYTES = 512
        try:
            result, _storage, events = _restore(plan, folder, cancel=cancel)
        finally:
            restore.CHUNK_BYTES = original_chunk
        assert result.cancelled
        assert result.target_not_trusted
        assert "partition-ready" not in events


def test_verification_mismatch_is_a_failure():
    with tempfile.TemporaryDirectory() as folder:
        image = Path(folder) / "raw.img"
        _raw(image)
        plan = restore.preflight_image(image, 4096)
        disk = _disk()
        storage = bytearray(disk.size_bytes)
        opens = 0

        def factory(_path, write=False):
            nonlocal opens
            opens += 1
            if opens == 3:
                storage[0] ^= 0xFF
            return FakeDisk(storage, write)

        try:
            restore.restore_and_verify(
                plan, disk, _store(folder), confirmed_disk_number=3,
                inventory_provider=lambda: [disk], image_disk_provider=lambda _path: 0,
                disk_factory=factory, volume_locker=lambda *_args, **_kwargs: [],
                flush_disk=lambda _target: None, partition_waiter=lambda _path: None,
            )
        except restore.GuardedRestoreError as exc:
            assert exc.target_not_trusted
            assert "mismatch" in str(exc)
        else:
            raise AssertionError("read-back mismatch was reported as success")


def _run_direct() -> int:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_") and callable(value)]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"PASS {test.__name__}")
        except Exception as exc:
            failures += 1
            print(f"FAIL {test.__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_run_direct())
