"""Regression test for per-slot watch independence.

Real-hardware finding (2026-07-27): update_properties() - called after every
successful flash, and again by pyimager.randomize_disk_signature() - forces
Windows to re-read a disk's partition table, which can briefly drop its
drive letter. On a shared-bus multi-slot reader (several disks reporting the
same hardware serial), fixing up one port transiently disturbed its siblings
too. The OLD _on_drives_changed() rebuilt every slot from scratch each poll
by re-sorting whatever disks were currently visible, so one disk blinking
out shifted every OTHER disk into a different slot index and could abort
unrelated running flashes.

That whole mechanism is gone now (see test_confirm_lock.py for the current
confirm/watch/grace-period design): removal detection is per-slot
(DriveMonitor.watch_slot(), one disk number, own polling loop) and only
starts once a slot is confirmed. This test's remaining job is to prove
independence directly - that confirming/removing one slot's disk NEVER
touches any other slot's data, workers, or widget display, since each
watch only ever looks at its own disk number.
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
        return 0  # no cap - not what this test is exercising

    def get_auto_clone(self):
        return False

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
        hw_serial="000000000819",  # identical on purpose - shared-bus reader
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

app_module.is_disk_removable = lambda disk_number: True  # _start_slot's own gate

_disk_present = {2: True, 3: True, 4: True, 5: True}
dm_module.is_disk_removable = lambda n: _disk_present.get(n, True)

scheduled = {}


def fake_after(ms, fn):
    job_id = f"fake-{len(scheduled) + 1}"
    scheduled[job_id] = fn
    return job_id


app._root.after = fake_after
app._root.after_cancel = lambda job_id: scheduled.pop(job_id, None)


def fire_watch_tick(slot_idx):
    state = app._monitor._watches.get(slot_idx)
    if state is None:
        return
    fn = scheduled.get(state["job"])
    if fn is not None:
        fn()


print("baseline: 4 disks connect and get confirmed into slots 0-3")
app._on_drives_changed([drive(2, "E:"), drive(3, "H:"), drive(4, "G:"), drive(5, "F:")])
for i in range(4):
    app._confirm_slot(i)
check("slot0 -> disk2, confirmed", app._locked_disk_nums.get(0), 2)
check("slot1 -> disk3, confirmed", app._locked_disk_nums.get(1), 3)
check("slot2 -> disk4, confirmed", app._locked_disk_nums.get(2), 4)
check("slot3 -> disk5, confirmed", app._locked_disk_nums.get(3), 5)

# Simulate slot1 and slot2 actively flashing, with real-looking progress.
app._workers[1] = _FakeWorker(CloneStatus.RUNNING)
app._workers[2] = _FakeWorker(CloneStatus.RUNNING)
app._window.set_slot_progress(1, 37)
app._window.set_slot_speed(1, "13.9 MB/s")
app._window.set_slot_eta(1, "~7m 27s")
app._window.set_slot_progress(2, 84)
app._window.set_slot_speed(2, "18.4 MB/s")
app._window.set_slot_eta(2, "~1m 05s")

print("\nslot1's disk (3) is genuinely removed - only ITS watch should fire")
_disk_present[3] = False
fire_watch_tick(1)
fire_watch_tick(1)  # miss_threshold=2
check("slot1 worker stopped", app._workers[1].stopped, True)
check("slot1 now empty", app._drives[1], None)
check("slot1 widget resets", app._window._slots[1]._info_var.get(), "—")

check("slot0 (disk2) completely untouched", app._locked_disk_nums.get(0), 2)
check("slot2 (disk4) completely untouched", app._locked_disk_nums.get(2), 4)
check("slot3 (disk5) completely untouched", app._locked_disk_nums.get(3), 5)
check("slot2 worker never stopped", app._workers[2].stopped, False)
check("slot2 widget progress NOT wiped by slot1's removal",
      app._window._slots[2]._pct_var.get(), "84%")
check("slot2 widget speed NOT wiped by slot1's removal",
      app._window._slots[2]._speed_var.get(), "18.4 MB/s")
check("slot2 widget eta NOT wiped by slot1's removal",
      app._window._slots[2]._eta_var.get(), "~1m 05s")

print("\na genuinely new disk connects into the freed slot")
app._on_drives_changed([drive(2, "E:"), drive(4, "G:"), drive(5, "F:"), drive(6, "I:")])
check("new disk (6) fills the freed slot1, awaiting confirm",
      app._drives[1].disk_number, 6)
check("slot1 is NOT auto-confirmed - needs a fresh click",
      app._window._slots[1]._awaiting_confirm, True)
check("slot2 still completely untouched by the new arrival",
      app._window._slots[2]._pct_var.get(), "84%")

app._root.destroy()
os.remove(tmp_img.name)

print(f"\n{sum(checks)}/{len(checks)} checks passed")
sys.exit(0 if all(checks) else 1)
