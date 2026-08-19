"""Headless checks for compact ext4 layout, capture, and source discovery."""

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
import ext4_compact_capture  # noqa: E402
import guarded_restore  # noqa: E402
import pyimager_worker  # noqa: E402
from clone_worker import CloneStatus  # noqa: E402
from ui import make_image_dialog  # noqa: E402


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

full_layout = compact_image.parse_mbr_layout(
    disk_image(10000, full), 10000 * SECTOR, require_trailing_space=False
)
check("full source layout allowed for compaction", full_layout.capture_bytes, 10000 * SECTOR)

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

print("\next4-only layout and prefix:")
source_mbr = table_sector([
    entry(0x80, 0x83, 2048, 6000),
    entry(0x00, 0x0F, 8048, 1500),
])
swap_ebr = table_sector([entry(0x00, 0x82, 1, 1400)])
source_layout = compact_image.parse_mbr_layout(
    disk_image(12000, source_mbr, {8048: swap_ebr}),
    12000 * SECTOR,
    require_trailing_space=False,
)
root_partition = source_layout.partitions[0]
compact_layout = compact_image.make_ext4_only_layout(
    source_layout, root_partition, 3000
)
check("compact layout partition count", len(compact_layout.partitions), 1)
check("compact layout capture", compact_layout.capture_bytes, 5048 * SECTOR)
prefix = bytearray(2048 * SECTOR)
prefix[:SECTOR] = source_mbr
prefix[SECTOR:] = b"P" * (len(prefix) - SECTOR)
patched = compact_image.patch_ext4_only_prefix(bytes(prefix), root_partition, 3000)
patched_entries = compact_image._entries(patched[:SECTOR])
check("patched root size", patched_entries[0].sector_count, 3000)
check("patched extended entry cleared", patched_entries[1].empty, True)
check("pre-ext4 boot gap preserved", patched[SECTOR:] == prefix[SECTOR:], True)
check(
    "minimum target includes aligned EBR and swap",
    compact_image.minimum_target_bytes(2048, 3000, 1400, 2048, SECTOR),
    9592 * SECTOR,
)

filesystem = compact_image.Ext4FilesystemRecord(
    partition_number=1,
    uuid="11111111-2222-3333-4444-555555555555",
    block_size=4096,
    original_start_lba=2048,
    original_sector_count=6000,
    compact_sector_count=3000,
    minimum_blocks=311,
    buffer_bytes=64 << 20,
    prefix_bytes=2048 * SECTOR,
)
omitted = compact_image.OmittedSwapRecord(
    partition_number=5,
    uuid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    original_start_lba=8049,
    sector_count=1400,
)
minimum_target = compact_image.minimum_target_bytes(2048, 3000, 1400, 2048, SECTOR)
expansion = compact_image.ExpansionRecord(
    root_uuid=filesystem.uuid,
    swap_uuid=omitted.uuid,
    swap_sector_count=omitted.sector_count,
    alignment_sectors=2048,
    minimum_target_bytes=minimum_target,
    installed_script_sha256="A" * 64,
)
manifest = compact_image.build_ext4_manifest(
    compact_layout,
    source_layout,
    {
        "source": "PhysicalDrive3",
        "device": {"serial": "SER1"},
        "bytes_written": compact_layout.capture_bytes,
        "digests": {"sha256": "B" * 64},
    },
    filesystem,
    omitted,
    expansion,
)
check("ext4 manifest schema", manifest["schema_version"], compact_image.EXT4_MANIFEST_SCHEMA)
check("ext4 manifest format", manifest["format"], compact_image.EXT4_MANIFEST_FORMAT)
check("ext4 manifest swap UUID", manifest["omitted_partitions"][0]["uuid"], omitted.uuid)
check("ext4 manifest buffer", manifest["filesystem"]["buffer_bytes"], 64 << 20)
check("cleanup metadata remains optional", "cleanup" in manifest, False)

print("\ncleanup installer metadata:")
cleanup_source = ROOT / "scripts" / "roulette_profile_cleanup.sh"
cleanup = ext4_compact_capture.parse_cleanup_installer(cleanup_source)
check("cleanup installer id", cleanup.installer_id, "roulette-firefox-profile-backups")
check("cleanup Linux versions", cleanup.linux_versions, ("12.04",))
check("cleanup source hash length", len(cleanup.source_sha256), 64)
cleanup_manifest = compact_image.build_ext4_manifest(
    compact_layout,
    source_layout,
    {
        "source": "PhysicalDrive3",
        "device": {"serial": "SER1"},
        "bytes_written": compact_layout.capture_bytes,
        "digests": {"sha256": "B" * 64},
    },
    filesystem,
    omitted,
    expansion,
    cleanup,
)
check("cleanup manifest source", cleanup_manifest["cleanup"]["source_filename"], cleanup_source.name)
check("cleanup manifest installed", cleanup_manifest["cleanup"]["installed"], True)
validated_cleanup = guarded_restore._validate_cleanup_record(cleanup_manifest["cleanup"])
check("guarded cleanup metadata accepted", validated_cleanup["installer_id"], cleanup.installer_id)

bad_cleanup = dict(cleanup_manifest["cleanup"])
bad_cleanup["linux_versions"] = ["22.04"]
try:
    guarded_restore._validate_cleanup_record(bad_cleanup)
except guarded_restore.GuardedImageError as exc:
    check("unsupported cleanup manifest rejected", "unsupported" in str(exc), True)
else:
    check("unsupported cleanup manifest rejected", False, True)

bad_cleanup_types = dict(cleanup_manifest["cleanup"])
bad_cleanup_types["linux_versions"] = [12]
try:
    guarded_restore._validate_cleanup_record(bad_cleanup_types)
except guarded_restore.GuardedImageError as exc:
    check("non-text cleanup metadata rejected", "schema is invalid" in str(exc), True)
else:
    check("non-text cleanup metadata rejected", False, True)

with tempfile.TemporaryDirectory() as folder:
    folder_path = Path(folder)
    incompatible = folder_path / "future-cleanup.sh"
    incompatible.write_text(
        cleanup_source.read_text(encoding="utf-8").replace(
            "# linux_versions=12.04", "# linux_versions=22.04", 1
        ),
        encoding="utf-8",
    )
    try:
        ext4_compact_capture.parse_cleanup_installer(incompatible)
    except ext4_compact_capture.Ext4CompactCaptureError as exc:
        check("future Linux installer fails closed", "not implemented" in str(exc), True)
    else:
        check("future Linux installer fails closed", False, True)

    malformed = folder_path / "malformed.sh"
    malformed.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    try:
        ext4_compact_capture.parse_cleanup_installer(malformed)
    except ext4_compact_capture.Ext4CompactCaptureError as exc:
        check("missing cleanup metadata rejected", "metadata block" in str(exc), True)
    else:
        check("missing cleanup metadata rejected", False, True)

    try:
        ext4_compact_capture.parse_cleanup_installer(folder_path / "missing.sh")
    except ext4_compact_capture.Ext4CompactCaptureError as exc:
        check("missing cleanup file rejected", "unreadable" in str(exc), True)
    else:
        check("missing cleanup file rejected", False, True)

print("\ncleanup picker behavior:")


class FakeVariable:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class FakeButton:
    def __init__(self):
        self.state = None

    def configure(self, *, state):
        self.state = state


class FakeCleanupDialog:
    def __init__(self, selected=""):
        self._cleanup_var = FakeVariable(True)
        self._cleanup_path_var = FakeVariable(selected)
        self._cleanup_browse_btn = FakeButton()
        self._clear_cleanup_selection = lambda: (
            make_image_dialog.MakeImageDialog._clear_cleanup_selection(self)
        )
        self._browse_cleanup = lambda: (
            make_image_dialog.MakeImageDialog._browse_cleanup(self)
        )
        self.statuses = []

    def _status(self, message, style):
        self.statuses.append((message, style))


real_cleanup_picker = make_image_dialog.filedialog.askopenfilename
real_cleanup_error = make_image_dialog.messagebox.showerror
picker_errors = []
make_image_dialog.messagebox.showerror = (
    lambda title, message, **_kwargs: picker_errors.append((title, message))
)
try:
    cancelled_initial = FakeCleanupDialog()
    make_image_dialog.filedialog.askopenfilename = lambda **_kwargs: ""
    make_image_dialog.MakeImageDialog._on_cleanup_toggle(cancelled_initial)
    check("initial picker cancel unchecks cleanup", cancelled_initial._cleanup_var.get(), False)
    check("initial picker cancel clears path", cancelled_initial._cleanup_path_var.get(), "")

    cancelled_replacement = FakeCleanupDialog(str(cleanup_source))
    replaced = make_image_dialog.MakeImageDialog._browse_cleanup(cancelled_replacement)
    check("replacement picker cancel reports no change", replaced, False)
    check(
        "replacement picker cancel preserves path",
        cancelled_replacement._cleanup_path_var.get(),
        str(cleanup_source),
    )

    invalid_initial = FakeCleanupDialog()
    make_image_dialog.filedialog.askopenfilename = lambda **_kwargs: str(malformed)
    make_image_dialog.MakeImageDialog._on_cleanup_toggle(invalid_initial)
    check("invalid initial installer unchecks cleanup", invalid_initial._cleanup_var.get(), False)
    check("invalid initial installer reports error", len(picker_errors), 1)

    valid_initial = FakeCleanupDialog()
    make_image_dialog.filedialog.askopenfilename = lambda **_kwargs: str(cleanup_source)
    make_image_dialog.MakeImageDialog._on_cleanup_toggle(valid_initial)
    check("valid initial installer remains checked", valid_initial._cleanup_var.get(), True)
    check("valid initial installer path displayed", valid_initial._cleanup_path_var.get(), str(cleanup_source))
finally:
    make_image_dialog.filedialog.askopenfilename = real_cleanup_picker
    make_image_dialog.messagebox.showerror = real_cleanup_error

print("\next4 source safety gates:")
selected_root, selected_swap = ext4_compact_capture._select_source_layout(source_layout)
check("logical swap selected", selected_swap.number, 5)
check("bootable ext4 root selected", selected_root.number, 1)
try:
    ext4_compact_capture._select_source_layout(compact_layout)
except ext4_compact_capture.Ext4CompactCaptureError as exc:
    check("missing swap rejected", "one Linux swap" in str(exc), True)
else:
    check("missing swap rejected", False, True)

valid_swap = ext4_compact_capture._WslPartition(
    "/dev/sdz5", 5, 8049, 1400, "swap", "1", omitted.uuid, False
)
validated_swap = ext4_compact_capture._validate_wsl_partition(
    valid_swap, selected_swap, "swap"
)
check("swap v1 identity accepted", validated_swap.uuid, omitted.uuid)
invalid_swap = ext4_compact_capture._WslPartition(
    "/dev/sdz5", 5, 8049, 1400, "swap", "0", omitted.uuid, False
)
try:
    ext4_compact_capture._validate_wsl_partition(invalid_swap, selected_swap, "swap")
except ext4_compact_capture.Ext4CompactCaptureError as exc:
    check("non-v1 swap rejected", "expected 1" in str(exc), True)
else:
    check("non-v1 swap rejected", False, True)

rendered_script, rendered_hash = ext4_compact_capture._render_expansion_script(
    filesystem.uuid, omitted.uuid, 2048, 3000, 1400
)
try:
    rendered = rendered_script.read_text(encoding="utf-8")
    check("expansion root UUID rendered", f'EXPECTED_ROOT_UUID="{filesystem.uuid}"' in rendered, True)
    check("expansion swap sectors rendered", "SWAP_SECTORS=1400" in rendered, True)
    check("expansion script hash recorded", len(rendered_hash), 64)
finally:
    rendered_script.unlink(missing_ok=True)

print("\nconditional cleanup installation:")
install_calls = []
real_wsl_path = ext4_compact_capture._wsl_path
real_install_run = ext4_compact_capture._run_wsl_script
ext4_compact_capture._wsl_path = lambda path: str(path)
ext4_compact_capture._run_wsl_script = (
    lambda source, args=None, **_kwargs: install_calls.append((source, args)) or ""
)
try:
    ext4_compact_capture._install_expansion(
        "stage", "work", cleanup_source, "A" * 64, cleanup_source, cleanup
    )
    ext4_compact_capture._install_expansion(
        "stage", "work", cleanup_source, "A" * 64, None, None
    )
finally:
    ext4_compact_capture._wsl_path = real_wsl_path
    ext4_compact_capture._run_wsl_script = real_install_run
check("selected cleanup enabled", install_calls[0][1][3], "1")
check("selected cleanup hash forwarded", install_calls[0][1][7], cleanup.source_sha256)
check("unchecked cleanup disabled", install_calls[1][1][3], "0")
check("unchecked cleanup removes managed artifacts", "rm -f" in install_calls[1][0], True)

real_run_wsl_script = ext4_compact_capture._run_wsl_script
read_only_scripts = []
try:
    ext4_compact_capture._run_wsl_script = (
        lambda source, args=None, **_kwargs: read_only_scripts.append((source, args)) or ""
    )
    ext4_compact_capture._set_source_read_only("/dev/sdz")
finally:
    ext4_compact_capture._run_wsl_script = real_run_wsl_script
check("WSL source forced read-only", "blockdev --setro" in read_only_scripts[0][0], True)

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


real_capture = pyimager_worker.capture_ext4_compact
try:
    with tempfile.TemporaryDirectory() as folder:
        output = Path(folder) / "test.compact.img"

        capture_kwargs = {}

        def fake_capture(_disk, path, **_kwargs):
            capture_kwargs.update(_kwargs)
            Path(path).write_bytes(small_bytes[:2 * SECTOR])
            meta = {
                "format": "ext4_compact",
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
            layout = compact_image.parse_mbr_layout(
                io.BytesIO(small_bytes), len(small_bytes)
            )
            fs = compact_image.Ext4FilesystemRecord(
                1, "11111111-2222-3333-4444-555555555555", 4096,
                1, 1, 1, 1, 64 << 20, SECTOR,
            )
            swap = compact_image.OmittedSwapRecord(
                5, "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", 2, 1
            )
            expansion = compact_image.ExpansionRecord(
                fs.uuid, swap.uuid, 1, 2048, 4097 * SECTOR, "C" * 64
            )
            return meta, compact_image.build_ext4_manifest(
                layout, layout, meta, fs, swap, expansion
            )

        pyimager_worker.capture_ext4_compact = fake_capture
        statuses = []
        worker = pyimager_worker.PyImagerWorker(
            FakeRoot(), 3, str(output), lambda _pct: None, lambda _line: None,
            statuses.append, compact=True, expected_size=len(small_bytes),
            expected_serial="SER1", cleanup_script=str(cleanup_source),
        )
        worker._run()
        manifest = compact_image.compact_manifest_path(output)
        check("compact worker completed", statuses[-1], CloneStatus.DONE)
        check("compact image published", output.stat().st_size, 2 * SECTOR)
        check("compact manifest published", manifest.is_file(), True)
        check("worker forwards cleanup selection", capture_kwargs["cleanup_script_path"], cleanup_source)
        saved = json.loads(manifest.read_text(encoding="utf-8"))
        check("manifest format", saved["format"], compact_image.EXT4_MANIFEST_FORMAT)

        def cancelled_capture(_disk, _path, **_kwargs):
            raise pyimager_worker.Ext4CompactCaptureCancelled("cancelled")

        pyimager_worker.capture_ext4_compact = cancelled_capture
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
    pyimager_worker.capture_ext4_compact = real_capture

print(f"\n{sum(checks)}/{len(checks)} checks passed")
sys.exit(0 if all(checks) else 1)
