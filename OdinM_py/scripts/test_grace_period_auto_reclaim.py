"""Regression test for automatic reclaim during the confirm-lock grace period.

Gap found in this session: after a locked slot's disk went missing,
DriveMonitor.watch_slot() stopped polling entirely (its whole point is to
fire on_missing() once, then get out of the way) - the disk_number stayed
locked for CONFIRM_LOCK_GRACE_MS as designed, but nothing was actively
looking for that disk to come back. Reclaiming only happened if the
operator clicked "Refresh Disks" again. Fixed by starting
DriveMonitor.watch_for_return() (narrow, single-disk, mirrors watch_slot())
the moment a locked slot goes missing, so the slot keeps looking for its
OWN disk_number - and only its own - without any manual action, and stops
looking (unwatch_slot()) once the lock genuinely expires.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config_manager import ENGINE_ODIN, ENGINE_PYIMAGER  # noqa: E402
from drive_manager import DriveInfo  # noqa: E402

import app as app_module  # noqa: E402
import drive_manager as dm_module  # noqa: E402


class _FakeConfig:
    def __init__(self):
        self._engine = ENGINE_ODIN

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
app = app_module.OdinMApp(_FakeConfig())
app._root.withdraw()

app_module.is_disk_removable = lambda disk_number: True

_disk_present = {2: True}
dm_module.is_disk_removable = lambda n: _disk_present.get(n, False)

scheduled = {}


def fake_after(ms, fn):
    job_id = f"fake-{len(scheduled) + 1}"
    scheduled[job_id] = fn
    return job_id


app._root.after = fake_after
app._root.after_cancel = lambda job_id: scheduled.pop(job_id, None)


def fire_watch(slot_idx):
    state = app._monitor._watches.get(slot_idx)
    if state is None:
        return
    fn = scheduled.get(state["job"])
    if fn is not None:
        fn()


print("baseline: disk 2 connects and gets confirmed into slot 0")
app._on_drives_changed([drive(2, "E:")])
app._confirm_slot(0)
check("slot0 locked to disk2", app._locked_disk_nums.get(0), 2)

print("\ndisk 2 is genuinely removed - miss_threshold=2 consecutive checks")
_disk_present[2] = False
fire_watch(0)
fire_watch(0)
check("slot0 cleared", app._drives[0], None)
check("lock stays (grace period, not an immediate release)",
      app._locked_disk_nums.get(0), 2)
check("a return-watch is now active for slot 0",
      0 in app._monitor._watches, True)
check("return-watch is tracking disk 2 specifically",
      app._monitor._watches[0]["disk_number"], 2)

print("\ndisk 2 reappears - the slot's OWN watch must notice, no manual refresh")
_disk_present[2] = True
app_module.get_removable_drives = lambda: [drive(2, "F:")]
fire_watch(0)
check("slot0 reclaimed automatically", app._drives[0].disk_number if app._drives[0] else None, 2)
check("still locked to disk 2 (no re-confirm needed)",
      app._locked_disk_nums.get(0), 2)
check("watch_slot restarted for disk 2 (watching for removal again)",
      app._monitor._watches.get(0, {}).get("disk_number"), 2)

print("\na DIFFERENT slot's removal/return must never touch slot 0")
app._on_drives_changed([drive(2, "F:"), drive(3, "H:")])
app._confirm_slot(1)
_disk_present[3] = False
fire_watch(1)
fire_watch(1)
check("slot0 completely untouched by slot1's removal",
      app._locked_disk_nums.get(0), 2)
check("slot0's drive record untouched", app._drives[0].disk_number, 2)

print("\nonce the grace period genuinely expires, the return-watch stops")
expire_jobs = dict(scheduled.items())
# Find and fire slot 1's scheduled _expire_lock call directly.
app._expire_lock(1, 3)
check("slot1's lock released", app._locked_disk_nums.get(1), None)
check("slot1's return-watch cancelled on expiry", 1 in app._monitor._watches, False)

app._root.destroy()

print(f"\n{sum(checks)}/{len(checks)} checks passed")
sys.exit(0 if all(checks) else 1)
