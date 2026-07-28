"""Regression test for the confirm-to-lock workflow.

New requirement: since disks are now detected at the physical-disk level
(see test_disk_level_detection.py) rather than by Windows drive letter, a
detected disk might be offline, unformatted, or otherwise unidentifiable to
the operator - so nothing may flash (neither a manual Start click nor
auto-clone) until the operator explicitly clicks "Confirm" to lock a slot
onto its currently-detected disk number.

The confirmation persists for CONFIRM_LOCK_GRACE_MS (15 min) after the disk
is confirmed removed - if the same disk number reappears within that
window, it's still treated as confirmed (no re-click needed, auto-clone can
proceed immediately). Only after 15 min with it still gone does the lock
actually expire.
"""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clone_worker import CloneStatus  # noqa: E402
from config_manager import ENGINE_ODIN, ENGINE_PYIMAGER  # noqa: E402
from drive_manager import DriveInfo  # noqa: E402

import app as app_module  # noqa: E402


class _FakeConfig:
    def __init__(self):
        self._engine = ENGINE_ODIN
        self._auto_clone = False

    def get_last_image(self):
        return ""

    def get_theme(self):
        return "darkly"

    def get_max_concurrent(self):
        return 5

    def get_max_drive_gb(self):
        return 0

    def get_max_disks(self):
        return 0  # no cap - not what this test is exercising

    def get_auto_clone(self):
        return self._auto_clone

    def get_verify_after_clone(self):
        return False

    def get_stop_on_verify_fail(self):
        return False

    def get_show_flash_widget(self):
        return False

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


class _FakeWorker:
    def __init__(self, status=CloneStatus.RUNNING):
        self.status = status
        self.stopped = False

    def stop(self):
        self.stopped = True
        self.status = CloneStatus.STOPPED


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

tmp_img = tempfile.NamedTemporaryFile(delete=False, suffix=".img")
tmp_img.write(b"x" * 1024)
tmp_img.close()
app._window._image_var.set(tmp_img.name)

# is_disk_removable would need real hardware - stub it True for this test,
# which is specifically about the confirm/lock bookkeeping, not that guard.
app_module.is_disk_removable = lambda disk_number: True

# _launch() would spawn a REAL CloneWorker/PyImagerRestoreWorker thread that
# tries to write to an actual physical disk - never let a test reach it.
# Replacing it lets _start_slot()'s guards run for real while observing
# whether it got far enough to launch, with zero hardware touched.
launched = []
app._launch = lambda idx: launched.append(idx)

scheduled = {}


def fake_after(ms, fn):
    job_id = f"fake-{len(scheduled) + 1}"
    scheduled[job_id] = fn
    return job_id


cancelled = []
app._root.after = fake_after
app._root.after_cancel = lambda job_id: cancelled.append(job_id)

# DriveMonitor.watch_slot() (defined in drive_manager.py) calls
# is_disk_removable() as a bare name resolved in ITS OWN module's globals -
# patching app_module.is_disk_removable (used by _start_slot's safety
# check) does not affect that lookup, so it needs its own controllable fake.
import drive_manager as dm_module  # noqa: E402

_disk_present = {2: True}
dm_module.is_disk_removable = lambda n: _disk_present.get(n, True)


def fire_watch_tick(slot_idx):
    """Simulate one interval tick of this slot's DriveMonitor.watch_slot()."""
    state = app._monitor._watches.get(slot_idx)
    if state is None:
        return
    fn = scheduled.get(state["job"])
    if fn is not None:
        fn()

print("new disk connects - must show as awaiting confirm, Start refused")
app._on_drives_changed([drive(2, "E:")])
check("slot not locked yet", app._locked_disk_nums.get(0), None)
check("widget shows awaiting-confirm", app._window._slots[0]._awaiting_confirm, True)
check("status badge reads Confirm",
      app._window._slots[0]._status_lbl.cget("text"), "Confirm")

app._start_slot(0)
check("Start refuses to reach _launch() on an unconfirmed slot", launched, [])

print("\noperator clicks Confirm")
app._confirm_slot(0)
check("slot now locked to disk 2", app._locked_disk_nums.get(0), 2)
check("widget no longer awaiting confirm", app._window._slots[0]._awaiting_confirm, False)
check("Start button enabled", str(app._window._slots[0]._btn.cget("state")), "normal")

app._start_slot(0)
check("Start reaches _launch() once confirmed", launched, [0])
launched.clear()

print("\nconfirmed disk is now RUNNING (simulated, as _launch() would set up)")
app._workers[0] = _FakeWorker(CloneStatus.RUNNING)

print("\ndisk confirmed-removed (2 consecutive watch misses) - lock must NOT clear immediately")
_disk_present[2] = False
fire_watch_tick(0)
fire_watch_tick(0)
check("worker stopped (genuine removal)", app._workers[0].stopped, True)
check("lock is still remembered (grace period, not cleared)",
      app._locked_disk_nums.get(0), 2)
check("an expiry timer was scheduled", 0 in app._lock_expiry_jobs, True)

print("\nsame disk reappears within the grace period")
_disk_present[2] = True
app._on_drives_changed([drive(2, "E:")])
check("expiry job cancelled", 0 not in app._lock_expiry_jobs, True)
check("slot goes STRAIGHT to confirmed, no re-click needed",
      app._window._slots[0]._awaiting_confirm, False)
check("still locked to disk 2", app._locked_disk_nums.get(0), 2)
check("watch was resumed for the reclaimed slot", 0 in app._monitor._watches, True)

del app._workers[0]
app._start_slot(0)
check("Start works without re-confirming (persisted lock)", launched, [0])
launched.clear()

print("\ndisk removed again, and this time the grace period actually expires")
_disk_present[2] = False
fire_watch_tick(0)
fire_watch_tick(0)
check("expiry scheduled again", 0 in app._lock_expiry_jobs, True)
expiry_job_id = app._lock_expiry_jobs[0]
scheduled[expiry_job_id]()  # simulate the 15-minute timer firing
check("lock cleared after expiry", app._locked_disk_nums.get(0), None)

print("\na different disk now needs a fresh confirm even in the same slot")
app._on_drives_changed([drive(2, "E:")])
check("slot is awaiting confirm again (lock genuinely expired)",
      app._window._slots[0]._awaiting_confirm, True)

app._root.destroy()
os.remove(tmp_img.name)

print(f"\n{sum(checks)}/{len(checks)} checks passed")
sys.exit(0 if all(checks) else 1)
