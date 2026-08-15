"""Headless regression checks for the inline per-slot Verify Disk action."""

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as app_module  # noqa: E402
from clone_worker import CloneStatus  # noqa: E402
from hash_worker import HashStatus  # noqa: E402
from ui.slot_widget import SlotWidget  # noqa: E402


class _FakeControl:
    def __init__(self, **values):
        self.values = values

    def configure(self, **values):
        self.values.update(values)

    def cget(self, name):
        return self.values.get(name, "")


class _FakeVar:
    def __init__(self):
        self.value = None

    def set(self, value):
        self.value = value


def _make_slot():
    starts = []
    stops = []
    verifies = []
    slot = object.__new__(SlotWidget)
    slot._idx = 0
    slot._on_start = starts.append
    slot._on_stop = stops.append
    slot._on_verify = verifies.append
    slot._awaiting_confirm = False
    slot._info_var = _FakeVar()
    slot._status_lbl = _FakeControl()
    slot._progress_var = _FakeVar()
    slot._pct_var = _FakeVar()
    slot._speed_var = _FakeVar()
    slot._eta_var = _FakeVar()
    slot._btn = _FakeControl()
    slot._verify_btn = _FakeControl()
    return slot, starts, stops, verifies


def test_verify_disk_button_is_independent_and_retryable():
    slot, starts, stops, verifies = _make_slot()

    slot.set_drive("[Disk 2]")
    assert slot._btn.cget("text") == "Start"
    assert slot._btn.cget("state") == "normal"
    assert slot._verify_btn.cget("text") == "Verify Disk"
    assert slot._verify_btn.cget("state") == "normal"

    slot._on_verify_btn_click()
    assert verifies == [0]
    assert starts == []

    slot.set_verifying()
    assert slot._btn.cget("state") == "disabled"
    assert slot._verify_btn.cget("text") == "Stop"
    slot._on_verify_btn_click()
    assert stops == [0]

    slot.set_status(CloneStatus.FAILED)
    assert slot._btn.cget("text") == "Start"
    assert slot._verify_btn.cget("text") == "Verify Disk"
    assert slot._verify_btn.cget("state") == "normal"


def test_clone_states_disable_disk_verification():
    slot, _starts, _stops, _verifies = _make_slot()
    slot.set_drive("[Disk 2]")

    slot.set_status(CloneStatus.QUEUED)
    assert slot._verify_btn.cget("state") == "disabled"

    slot.set_status(CloneStatus.RUNNING)
    assert slot._verify_btn.cget("state") == "disabled"

    slot.set_status(CloneStatus.DONE)
    assert slot._verify_btn.cget("state") == "normal"


class _FakeWindow:
    def __init__(self):
        self.logs = []

    def log(self, message):
        self.logs.append(message)


def _make_app():
    app = object.__new__(app_module.OdinMApp)
    app._window = _FakeWindow()
    app._drives = [SimpleNamespace(disk_number=2)]
    app._locked_disk_nums = {0: 2}
    app._workers = {}
    app._verify_workers = {}
    app._partition_waiters = {}
    app._queue = []
    app._speed_samples = {}
    app._verify_calls = []
    app._start_target_verify = lambda idx: app._verify_calls.append(idx) or True
    return app


def test_verify_disk_uses_normal_target_verifier():
    app_module.is_disk_removable = lambda _disk_number: True
    app = _make_app()

    app._verify_slot(0)
    assert app._verify_calls == [0]


def test_verify_disk_refuses_active_clone_or_duplicate_verify():
    app_module.is_disk_removable = lambda _disk_number: True
    app = _make_app()
    app._workers[0] = SimpleNamespace(status=CloneStatus.RUNNING)

    app._verify_slot(0)
    assert app._verify_calls == []
    assert any("cloning or queued" in line for line in app._window.logs)

    app._workers.clear()
    app._verify_workers[0] = SimpleNamespace(status=HashStatus.RUNNING)
    app._verify_slot(0)
    assert app._verify_calls == []
    assert any("already being verified" in line for line in app._window.logs)


def test_stop_button_stops_standalone_verifier():
    app = _make_app()
    verifier = SimpleNamespace(status=HashStatus.RUNNING, stopped=False)
    verifier.stop = lambda: setattr(verifier, "stopped", True)
    app._verify_workers[0] = verifier

    app._stop_slot(0)
    assert verifier.stopped
    assert any("Stop requested" in line for line in app._window.logs)


if __name__ == "__main__":
    tests = [
        test_verify_disk_button_is_independent_and_retryable,
        test_clone_states_disable_disk_verification,
        test_verify_disk_uses_normal_target_verifier,
        test_verify_disk_refuses_active_clone_or_duplicate_verify,
        test_stop_button_stops_standalone_verifier,
    ]
    for test in tests:
        test()
        print(f"[ok] {test.__name__}")
    print(f"\n{len(tests)}/{len(tests)} checks passed")
