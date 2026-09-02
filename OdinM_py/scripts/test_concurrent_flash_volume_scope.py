"""Regression test for the concurrent-flash volume-scan race.

Real-hardware finding (2026-07-27): flashing two slots at once with the
pyimager engine caused one slot to fail locking its own volume ("could not
lock F: - close anything using it") and the other to fail mid-write with
"Access is denied" right after a clean lock+dismount. Root cause:
pyimager.restore_disk() (and randomize_disk_signature()) called
volumes_on_disk(), which scans ALL 26 drive letters to find which ones live
on the target disk - each restore running on its own thread, so one slot's
scan briefly touched a SIBLING slot's letter right as that sibling was
locking or writing it. clone_worker.py (the ODIN engine) never had this bug
because its _lock_and_dismount_volume() takes one already-known letter, no
scanning.

Fix: both pyimager functions accept an optional `volumes` list of bare
letters; when given, they never call volumes_on_disk() at all, so a
restore only ever touches its own disk's letters. app.py now passes
drive.all_letters (from the same DriveInfo the app already scanned) through
PyImagerRestoreWorker and into _fix_disk_signature's randomize_disk_signature
call.
"""

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import pyimager  # noqa: E402

from config_manager import ENGINE_PYIMAGER  # noqa: E402
from drive_manager import DriveInfo  # noqa: E402

import app as app_module  # noqa: E402


class FakeDisk:
    written = []

    def __init__(self, path, write=False):
        self.path = path
        self.writable = write

    @property
    def size(self):
        return 1 << 20

    @property
    def sector_size(self):
        return 512

    @property
    def removable(self):
        return True

    def device_info(self):
        return {"removable": True, "vendor": "Test", "product": "Card"}

    def seek(self, off):
        pass

    def read(self, n):
        return bytes(n)

    def write(self, data):
        FakeDisk.written.append((self.path, len(data)))
        return len(data)

    def lock(self):
        return True

    def dismount(self):
        return True

    def unlock(self):
        pass

    def update_properties(self):
        return True

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


pyimager.Win32Disk = FakeDisk

checks = []


def check(name, got, want):
    ok = got == want
    checks.append(ok)
    print(f"  [{'ok ' if ok else 'FAIL'}] {name}: {got!r}"
          + ("" if ok else f"  (expected {want!r})"))


tmp_img = tempfile.NamedTemporaryFile(delete=False, suffix=".img")
tmp_img.write(bytes(512))
tmp_img.close()

print("restore_disk(volumes=[...]) must never call volumes_on_disk()")


def _boom(disk_number):
    raise AssertionError("volumes_on_disk() must not be called when volumes= is given")


pyimager.volumes_on_disk = _boom
FakeDisk.written = []
meta = pyimager.restore_disk(2, tmp_img.name, confirm=2, volumes=["E"])
check("restore completed", meta["cancelled"], False)
check("bytes were written to the physical disk", len(FakeDisk.written) > 0, True)

print("\nrandomize_disk_signature(volumes=[...]) must never call volumes_on_disk()")
sig = pyimager.randomize_disk_signature(2, volumes=["E"])
check("returned a 4-byte signature", len(sig), 4)

print("\nvolumes=None (CLI usage) still falls back to volumes_on_disk()")
scan_calls = []
pyimager.volumes_on_disk = lambda n: scan_calls.append(n) or ["E"]
meta2 = pyimager.restore_disk(2, tmp_img.name, confirm=2)
check("fallback scan was invoked", scan_calls, [2])

print("\napp.py's _launch() passes drive.all_letters through as PyImagerRestoreWorker(volumes=...)")


class _FakeConfig:
    def __init__(self):
        self._engine = ENGINE_PYIMAGER

    def get_last_image(self):
        return ""

    def get_theme(self):
        return "darkly"

    def get_max_concurrent(self):
        return 5

    def get_max_drive_gb(self):
        return 0

    def get_max_disks(self):
        return 0

    def get_auto_clone(self):
        return False

    def get_verify_after_clone(self):
        return False

    def get_stop_on_verify_fail(self):
        return False

    def get_show_flash_widget(self):
        return False

    def get_keep_completed_disks_locked(self):
        return False

    def set_keep_completed_disks_locked(self, _value):
        pass

    def get_engine(self):
        return self._engine

    def use_pyimager(self):
        return self._engine == ENGINE_PYIMAGER

    def set_engine(self, v):
        self._engine = v

    def get_odinc_path(self):
        return ""

    def set_odinc_path(self, v):
        pass


captured = {}


class _FakePyImagerRestoreWorker:
    def __init__(self, **kwargs):
        captured.update(kwargs)
        self.status = None

    def start(self):
        pass


app_module.OdinMApp._show_flash_widget = lambda self: None
app_module.PyImagerRestoreWorker = _FakePyImagerRestoreWorker
app = app_module.OdinMApp(_FakeConfig())
app._root.withdraw()

tmp_img2 = tempfile.NamedTemporaryFile(delete=False, suffix=".img")
tmp_img2.write(bytes(512))
tmp_img2.close()
app._window._image_var.set(tmp_img2.name)

app._drives[0] = DriveInfo(
    disk_number=2,
    first_letter="E:",
    all_letters=["E:"],
    label="PT-FIRMWARE",
    size_bytes=7969177600,
    hw_serial="000000000819",
)
app._locked_disk_nums[0] = 2
app._launch(0)
check("PyImagerRestoreWorker received volumes stripped of the colon",
      captured.get("volumes"), ["E"])
check("disk_number passed through unchanged", captured.get("disk_number"), 2)

app._root.destroy()

print(f"\n{sum(checks)}/{len(checks)} checks passed")
sys.exit(0 if all(checks) else 1)
