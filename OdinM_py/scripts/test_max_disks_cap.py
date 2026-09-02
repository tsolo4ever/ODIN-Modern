"""Regression test for the max_disks cap in app.py's _on_drives_changed().

get_removable_drives() already excludes non-removable disks (the OS drive,
internal SSDs) entirely before returning anything, so max_disks only ever
competes among genuinely removable candidates - it does not need to
"leave room" for fixed disks. Extras beyond the cap are excluded the same
way an oversized drive already is (max_drive_gb), and - critically - a
disk already occupying a slot is NEVER evicted by this cap, since capping
is meant to bound how many NEW cards get picked up, not punish something
already in progress.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config_manager import ENGINE_ODIN, ENGINE_PYIMAGER  # noqa: E402
from drive_manager import DriveInfo  # noqa: E402

import app as app_module  # noqa: E402


class _FakeConfig:
    def __init__(self, max_disks=5):
        self._engine = ENGINE_ODIN
        self._max_disks = max_disks

    def get_last_image(self):
        return ""

    def get_theme(self):
        return "darkly"

    def get_max_concurrent(self):
        return 5

    def get_max_drive_gb(self):
        return 0

    def get_max_disks(self):
        return self._max_disks

    def get_auto_clone(self):
        return False

    def get_verify_after_clone(self):
        return False

    def get_stop_on_verify_fail(self):
        return False

    def get_show_flash_widget(self):
        return False

    def get_keep_completed_disks_locked(self):
        return True

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


def drive(disk_number, letter):
    return DriveInfo(
        disk_number=disk_number,
        first_letter=letter,
        all_letters=[letter],
        label="PT-FIRMWARE",
        size_bytes=7969177600,
        hw_serial="000000000819",
    )


checks = []


def check(name, got, want):
    ok = got == want
    checks.append(ok)
    print(f"  [{'ok ' if ok else 'FAIL'}] {name}: {got!r}"
          + ("" if ok else f"  (expected {want!r})"))


app_module.OdinMApp._show_flash_widget = lambda self: None
app = app_module.OdinMApp(_FakeConfig(max_disks=2))
app._root.withdraw()

print("6 removable disks detected at once, max_disks=2 - only 2 get slots")
app._on_drives_changed([drive(n, chr(69 + n) + ":") for n in range(2, 8)])
occupied = [i for i in range(5) if app._drives[i] is not None]
check("exactly 2 slots filled", len(occupied), 2)
check("kept the two LOWEST disk numbers (deterministic ordering)",
      sorted(app._drives[i].disk_number for i in occupied), [2, 3])

print("\nconfirm slot for disk 2, then it must NEVER be evicted by the cap")
confirmed_idx = next(i for i in occupied if app._drives[i].disk_number == 2)
app._confirm_slot(confirmed_idx)
check("disk 2 is locked", app._locked_disk_nums.get(confirmed_idx), 2)

print("refresh again with disk 2 still there plus 5 OTHER new candidates")
app._on_drives_changed([drive(2, "E:")] + [drive(n, chr(69 + n) + ":") for n in range(10, 15)])
check("disk 2's slot is untouched by the cap despite many new candidates",
      app._locked_disk_nums.get(confirmed_idx), 2)
check("still exactly max_disks(2) slots filled",
      sum(1 for d in app._drives if d is not None), 2)

print("\nmax_disks=0 means no cap at all")
app._drives = [None] * app_module.NUM_SLOTS  # reset to a clean empty state
app._locked_disk_nums.clear()
app._config._max_disks = 0
app._on_drives_changed([drive(n, chr(69 + n) + ":") for n in range(2, 8)])
check("all 6 considered, but NUM_SLOTS(5) still caps actual slot count",
      sum(1 for d in app._drives if d is not None), 5)

app._root.destroy()

print(f"\n{sum(checks)}/{len(checks)} checks passed")
sys.exit(0 if all(checks) else 1)
