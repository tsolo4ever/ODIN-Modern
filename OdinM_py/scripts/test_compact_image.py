"""Headless checks for bounded raw layout, capture, and source discovery."""

import io
import json
import struct
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import compact_image  # noqa: E402
import drive_manager  # noqa: E402
import pyimager_worker  # noqa: E402
from clone_worker import CloneStatus  # noqa: E402


SECTOR = 512
checks: list[bool] = []


def check(name, got, want):
    ok = got == want
    checks.append(ok)
    print(
        f"  [{'ok ' if ok else 'FAIL'}] {name}: {got!r}"
        + ("" if ok else f" (expected {want!r})")
    )


def entry(status, type_code, start, count):
    raw = bytearray(16)
    raw[0] = status
    raw[4] = type_code
    struct.pack_into("<II", raw, 8, start, count)
    return raw


def table_sector(entries, *, signature=True, disk_signature=b"TEST"):
    raw = bytearray(SECTOR)
    raw[440:444] = disk_signature
    for index, item in enumerate(entries):
        start = 446 + index * 16
        raw[start:start + 16] = item
    if signature:
        raw[510:512] = b"\x55\xaa"
    return raw


def disk_image(sectors, mbr, extras=None):
    raw = bytearray(sectors * SECTOR)
    raw[:SECTOR] = mbr
    for lba, sector in (extras or {}).items():
        raw[lba * SECTOR:(lba + 1) * SECTOR] = sector
    return io.BytesIO(raw)


print("primary layout:")
mbr = table_sector([entry(0x80, 0x83, 2048, 1000)])
layout = compact_image.parse_mbr_layout(disk_image(5000, mbr), 5000 * SECTOR)
check("primary capture boundary", layout.capture_bytes, 3048 * SECTOR)
check("primary saved trailing", layout.saved_bytes, 1952 * SECTOR)
check("disk signature", layout.disk_signature, "54455354")

print("\nextended/logical layout:")
mbr = table_sector([
    entry(0x80, 0x83, 2048, 1000),
    entry(0x00, 0x0F, 4096, 4000),
])
ebr1 = table_sector([
    entry(0x00, 0x82, 63, 500),
    entry(0x00, 0x0F, 1000, 3000),
])
ebr2 = table_sector([entry(0x00, 0x83, 63, 700)])
layout = compact_image.parse_mbr_layout(
    disk_image(10000, mbr, {4096: ebr1, 5096: ebr2}), 10000 * SECTOR
)
check("partition numbers", [item.number for item in layout.partitions], [1, 5, 6])
check("logical starts", [item.start_lba for item in layout.partitions[1:]], [4159, 5159])
check("extended capture boundary", layout.capture_bytes, 5859 * SECTOR)
check("trailing extended free space omitted", layout.saved_bytes, 4141 * SECTOR)

print("\nunsafe layouts:")
gpt = table_sector([entry(0x00, 0xEE, 1, 9999)])
try:
    compact_image.parse_mbr_layout(disk_image(10000, gpt), 10000 * SECTOR)
except compact_image.CompactImageError as exc:
    check("GPT rejected", "GPT" in str(exc), True)
else:
    check("GPT rejected", False, True)

full = table_sector([entry(0x00, 0x83, 1, 9999)])
try:
    compact_image.parse_mbr_layout(disk_image(10000, full), 10000 * SECTOR)
except compact_image.CompactImageError as exc:
    check("no trailing space rejected", "no trailing" in str(exc).lower(), True)
else:
    check("no trailing space rejected", False, True)

cycle_mbr = table_sector([entry(0x00, 0x0F, 100, 800)])
cycle_ebr = table_sector([
    entry(0x00, 0x83, 1, 20),
    entry(0x00, 0x0F, 0, 800),
])
try:
    compact_image.parse_mbr_layout(
        disk_image(1000, cycle_mbr, {100: cycle_ebr}), 1000 * SECTOR
    )
except compact_image.CompactImageError as exc:
    check("bad EBR link rejected", "partially defined" in str(exc).lower(), True)
else:
    check("bad EBR link rejected", False, True)

print("\nall-drive discovery:")
originals = {
    name: getattr(drive_manager, name)
    for name in (
        "MAX_PHYSICAL_DISKS",
        "MAX_IMAGE_SOURCE_DISKS",
        "_all_letters_by_disk",
        "_get_physical_disk_size",
        "_get_volume_label",
        "_get_device_serial",
        "_get_device_model",
        "is_disk_removable",
        "get_system_disk_number",
        "_get_physical_disk_number",
    )
}
try:
    drive_manager.MAX_IMAGE_SOURCE_DISKS = 4
    drive_manager._all_letters_by_disk = lambda: {0: ["C:"], 3: ["E:"]}
    drive_manager._get_physical_disk_size = lambda n: {0: 1000, 3: 2000}.get(n, 0)
    drive_manager._get_volume_label = lambda root: {"C:\\": "Windows", "E:\\": "Linux"}.get(root, "")
    drive_manager._get_device_serial = lambda n: f"SERIAL{n}"
    drive_manager._get_device_model = lambda n: f"MODEL{n}"
    drive_manager.is_disk_removable = lambda n: n == 3
    drive_manager.get_system_disk_number = lambda: 0
    drives = drive_manager.get_all_readable_drives()
    check("fixed and removable discovered", [item.disk_number for item in drives], [0, 3])
    check("system source labeled", drives[0].is_system, True)
    check("removable source labeled", drives[1].removable, True)
    drive_manager._get_physical_disk_number = lambda drive: 3 if drive == "E:" else 0
    check("output disk resolved", drive_manager.get_path_disk_number(r"E:\images\x.img"), 3)
finally:
    for name, value in originals.items():
        setattr(drive_manager, name, value)

print("\ncompact worker publish and cancellation:")
small_mbr = table_sector([entry(0x80, 0x83, 1, 1)])
small_bytes = bytes(small_mbr) + bytes(3 * SECTOR)


class FakeDisk(io.BytesIO):
    size = len(small_bytes)
    sector_size = SECTOR

    def __init__(self, _path):
        super().__init__(small_bytes)

    def device_info(self):
        return {"vendor": "TEST", "product": "DISK", "serial": "SER1", "removable": True}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class FakeRoot:
    @staticmethod
    def after(_delay, fn, *args):
        fn(*args)


real_disk = pyimager_worker.pyimager.Win32Disk
real_image = pyimager_worker.pyimager.image_disk
try:
    pyimager_worker.pyimager.Win32Disk = FakeDisk
    with tempfile.TemporaryDirectory() as folder:
        output = Path(folder) / "test.compact.img"

        def fake_image(_disk, path, **_kwargs):
            Path(path).write_bytes(small_bytes[:2 * SECTOR])
            return {
                "format": "raw",
                "source": "PhysicalDrive3",
                "device": FakeDisk("").device_info(),
                "disk_size": len(small_bytes),
                "sector_size": SECTOR,
                "region_length": 2 * SECTOR,
                "bytes_written": 2 * SECTOR,
                "stored_bytes": 2 * SECTOR,
                "started_utc": "start",
                "finished_utc": "finish",
                "duration_s": 0.1,
                "digests": {"sha256": "A" * 64, "sha1": "B" * 40},
                "bad_sectors": [],
                "bad_sector_count": 0,
                "cancelled": False,
            }

        pyimager_worker.pyimager.image_disk = fake_image
        statuses = []
        worker = pyimager_worker.PyImagerWorker(
            FakeRoot(), 3, str(output), lambda _pct: None, lambda _line: None,
            statuses.append, compact=True, expected_size=len(small_bytes),
            expected_serial="SER1",
        )
        worker._run()
        manifest = compact_image.compact_manifest_path(output)
        check("compact worker completed", statuses[-1], CloneStatus.DONE)
        check("compact image published", output.stat().st_size, 2 * SECTOR)
        check("compact manifest published", manifest.is_file(), True)
        saved = json.loads(manifest.read_text(encoding="utf-8"))
        check("manifest format", saved["format"], compact_image.MANIFEST_FORMAT)

        def cancelled_image(_disk, path, **_kwargs):
            Path(path).write_bytes(b"partial")
            return {
                "cancelled": True,
                "bytes_written": 7,
                "region_length": 2 * SECTOR,
                "bad_sector_count": 0,
                "digests": {},
            }

        pyimager_worker.pyimager.image_disk = cancelled_image
        stopped = []
        cancelled_output = Path(folder) / "cancel.compact.img"
        worker = pyimager_worker.PyImagerWorker(
            FakeRoot(), 3, str(cancelled_output), lambda _pct: None, lambda _line: None,
            stopped.append, compact=True, expected_size=len(small_bytes),
            expected_serial="SER1",
        )
        worker._run()
        check("cancel reports stopped", stopped[-1], CloneStatus.STOPPED)
        check("cancel removes temporary image", Path(str(cancelled_output) + ".partial").exists(), False)
        check("cancel does not publish image", cancelled_output.exists(), False)
finally:
    pyimager_worker.pyimager.Win32Disk = real_disk
    pyimager_worker.pyimager.image_disk = real_image

print(f"\n{sum(checks)}/{len(checks)} checks passed")
sys.exit(0 if all(checks) else 1)
