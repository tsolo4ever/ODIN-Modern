"""Regression test for the auto-clone settle delay.

Real-world finding: auto-clone was starting to write to a newly-inserted
card the INSTANT it was detected - no time for Windows/the reader to settle
after insertion. Fixed by scheduling the actual start AUTO_CLONE_DELAY_MS
later via root.after() (app.py's _auto_clone_pending / _auto_clone_delayed),
re-validating everything once the delay elapses instead of firing blindly -
the card may have been pulled or swapped, auto-clone may have been turned
off, or the image may have been deselected during the wait.
"""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config_manager import ENGINE_ODIN, ENGINE_PYIMAGER  # noqa: E402
from drive_manager import DriveInfo  # noqa: E402

import app as app_module  # noqa: E402


class _FakeConfig:
    def __init__(self):
        self._engine = ENGINE_ODIN
        self._auto_clone = True

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

started = []
app._start_slot = lambda idx: started.append(idx)

scheduled = []


def fake_after(ms, fn):
    scheduled.append((ms, fn))
    return f"fake-id-{len(scheduled)}"


cancelled = []
orig_after = app._root.after
orig_after_cancel = app._root.after_cancel
app._root.after = fake_after
app._root.after_cancel = lambda job_id: cancelled.append(job_id)

print("new card connects with auto-clone on")
app._on_drives_changed([drive(2, "E:")])
check("does NOT start immediately", started, [])
check("schedules exactly one delayed call", len(scheduled), 1)
check("delay matches AUTO_CLONE_DELAY_MS", scheduled[0][0], app_module.AUTO_CLONE_DELAY_MS)
check("disk tracked as pending", 2 in app._auto_clone_pending, True)

print("\nredundant change event for the same card before the delay fires")
app._on_drives_changed([drive(2, "E:")])
check("still only one scheduled call (no double-scheduling)", len(scheduled), 1)

print("\ndelay elapses - fire the captured callback (not confirmed yet)")
_, fn = scheduled[0]
fn()
check("does NOT start without confirmation", started, [])
check("pending entry cleared after firing", 2 in app._auto_clone_pending, False)

print("\noperator confirms - THAT is what triggers the auto-clone start")
app._confirm_slot(0)
check("starts exactly the right slot once confirmed", started, [0])
check("slot is now locked to its disk", app._locked_disk_nums.get(0), 2)

print("\na second card connects, then gets pulled before its delay fires")
started.clear()
scheduled.clear()
app._on_drives_changed([drive(2, "E:"), drive(3, "H:")])
check("new disk (3) schedules its own delayed call", len(scheduled), 1)
# Two consecutive misses to confirm a real removal (matches the drive-slot
# stability fix's debounce).
app._on_drives_changed([drive(2, "E:")])
app._on_drives_changed([drive(2, "E:")])
check("pending entry for disk 3 cleared on confirmed removal",
      3 in app._auto_clone_pending, False)
check("after_cancel was called", len(cancelled) >= 1, True)
# Firing the stale callback now must be a no-op - the card is gone.
_, fn3 = scheduled[0]
fn3()
check("stale callback does not start a clone for a removed card", started, [])

app._root.after = orig_after
app._root.after_cancel = orig_after_cancel
app._root.destroy()
os.remove(tmp_img.name)

print(f"\n{sum(checks)}/{len(checks)} checks passed")
sys.exit(0 if all(checks) else 1)
