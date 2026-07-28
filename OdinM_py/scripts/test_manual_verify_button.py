"""Regression test for the manual "Verify" button.

Real-world finding: with verify-after-clone OFF, a completed flash's log
said "restore complete - run `verify` to confirm" but the slot's button
just went back to "Start" - no way to actually trigger that verify from the
UI. Fixed by offering "Verify" instead of "Start" on a DONE slot whenever
auto-verify is off; clicking it runs the same target-hash check the
auto-verify path uses (_start_target_verify -> _on_target_verify_done),
including fixing the disk signature afterward on success - deferred until
then rather than done immediately, since randomize_disk_signature()
scrambles bytes a hash check would otherwise still be comparing.
"""

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
        self._verify_after_clone = False

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
        return self._verify_after_clone

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

app._drives[0] = DriveInfo(
    disk_number=2, first_letter="E:", all_letters=["E:"],
    label="PT-FIRMWARE", size_bytes=7969177600, hw_serial="000000000819",
)
app._locked_disk_nums[0] = 2

fix_sig_calls = []
app._fix_disk_signature = lambda idx: fix_sig_calls.append(idx)

verify_calls = []
app._start_target_verify = lambda idx: verify_calls.append(idx) or True

print("flash completes, verify-after-clone is OFF")
app._on_worker_done(0, CloneStatus.DONE)
check("button offers Verify instead of Start",
      app._window._slots[0]._btn.cget("text"), "Verify")
check("signature fix is NOT run immediately (would break a later verify)",
      fix_sig_calls, [])
check("verify was NOT auto-started", verify_calls, [])

print("\noperator clicks Verify")
app._window._slots[0]._on_verify(0)
check("button flips back to Start (no double-click race)",
      app._window._slots[0]._btn.cget("text"), "Start")
check("target verify was triggered", verify_calls, [0])

print("\nsame scenario, but verify-after-clone is ON - no Verify button offered")
app2_drives_reset_idx = 0
app._locked_disk_nums[0] = 2
app._drives[0] = DriveInfo(
    disk_number=2, first_letter="E:", all_letters=["E:"],
    label="PT-FIRMWARE", size_bytes=7969177600, hw_serial="000000000819",
)
app._config._verify_after_clone = True
verify_calls.clear()
app._on_worker_done(0, CloneStatus.DONE)
check("button is plain Start when auto-verify already covers it",
      app._window._slots[0]._btn.cget("text"), "Start")
check("verify WAS auto-started this time", verify_calls, [0])

print("\na FAILED flash never offers Verify (nothing valid to check)")
app._config._verify_after_clone = False
app._drives[0] = DriveInfo(
    disk_number=2, first_letter="E:", all_letters=["E:"],
    label="PT-FIRMWARE", size_bytes=7969177600, hw_serial="000000000819",
)
app._on_worker_done(0, CloneStatus.FAILED)
check("failed flash keeps the plain Start button",
      app._window._slots[0]._btn.cget("text"), "Start")

app._root.destroy()
import os
os.remove(tmp_img.name)

print(f"\n{sum(checks)}/{len(checks)} checks passed")
sys.exit(0 if all(checks) else 1)
