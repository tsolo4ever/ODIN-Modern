"""Headless checks for guarded/multi mode exclusion and session state."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app  # noqa: E402
import ttkbootstrap as ttk  # noqa: E402
from clone_worker import CloneStatus  # noqa: E402
from drive_manager import DriveMonitor  # noqa: E402
from guarded_flash_safety import ProtectedHardwareStore  # noqa: E402
from hash_worker import HashStatus  # noqa: E402
from ui.guarded_single_flash import (  # noqa: E402
    IMAGE_PLACEHOLDER,
    MODE_GUARDED,
    MODE_MULTI,
    GuardedSingleFlashFrame,
    GuardedSessionState,
)
from ui.main_window import format_eta, format_speed, sync_flash_widget  # noqa: E402


class FakeWindow:
    def __init__(self, multi_flash_active: bool):
        self.multi_flash_active = multi_flash_active
        self.messages = []

    def log(self, text: str):
        self.messages.append(text)


def _bare_app(*, multi_active: bool = True):
    instance = app.OdinMApp.__new__(app.OdinMApp)
    instance._window = FakeWindow(multi_active)
    instance._workers = {}
    instance._verify_workers = {}
    instance._queue = []
    instance._partition_waiters = {}
    instance._auto_clone_pending = {}
    return instance


def test_mode_labels_are_explicit_and_guarded_placeholder_is_not_a_path():
    assert MODE_MULTI == "Multi Flash - Removable Drives"
    assert MODE_GUARDED == "Guarded Single Flash - Physical Drive"
    assert IMAGE_PLACEHOLDER == "Ex. Roulette HD"
    state = GuardedSessionState()
    state.enter()
    assert state.image_path == ""


def test_guarded_session_clears_target_and_image_on_every_boundary():
    state = GuardedSessionState()
    state.enter()
    state.image_path = r"D:\roulette.img"
    state.selected_disk = object()
    state.finish_attempt()
    assert state.image_path == ""
    assert state.selected_disk is None
    state.image_path = r"D:\another.img"
    state.leave()
    assert not state.active
    assert state.image_path == ""


def test_guarded_session_cannot_leave_while_busy():
    state = GuardedSessionState(active=True, busy=True)
    try:
        state.leave()
    except RuntimeError:
        pass
    else:
        raise AssertionError("busy guarded session allowed a mode switch")


def test_active_multi_work_or_queue_blocks_guarded_mode():
    cases = (
        ("_workers", {0: SimpleNamespace(status=CloneStatus.RUNNING)}),
        ("_verify_workers", {0: SimpleNamespace(status=HashStatus.RUNNING)}),
        ("_queue", [0]),
        ("_partition_waiters", {0: object()}),
    )
    for name, value in cases:
        instance = _bare_app()
        setattr(instance, name, value)
        allowed, reason = instance._can_enter_guarded_mode()
        assert not allowed
        assert "Stop all" in reason


def test_idle_multi_mode_allows_guarded_mode():
    assert _bare_app()._can_enter_guarded_mode() == (True, "")


def test_entering_guarded_cancels_auto_jobs_and_hides_flash_widget():
    instance = _bare_app()
    cancelled = []
    instance._root = SimpleNamespace(after_cancel=lambda job: cancelled.append(job))
    instance._auto_clone_pending = {2: "job-2", 3: "job-3"}
    instance._flash_widget = SimpleNamespace(
        winfo_exists=lambda: True,
        withdraw=lambda: cancelled.append("hidden"),
    )
    instance._monitor = SimpleNamespace(pause=lambda: cancelled.append("paused"))
    instance._enter_guarded_mode()
    assert cancelled == ["job-2", "job-3", "paused", "hidden"]
    assert not instance._auto_clone_pending


def test_multi_start_and_auto_start_are_blocked_behind_guarded_mode():
    instance = _bare_app(multi_active=False)
    instance._start_slot(0)
    assert "blocked" in instance._window.messages[-1]
    instance._config = SimpleNamespace(get_auto_clone=lambda: True)
    instance._drives = [SimpleNamespace(disk_number=3)]
    instance._finished_disk_nums = set()
    instance._try_auto_clone(0)
    assert len(instance._window.messages) == 1


def test_delayed_auto_job_is_discarded_in_guarded_mode():
    instance = _bare_app(multi_active=False)
    instance._config = SimpleNamespace(get_auto_clone=lambda: True)
    instance._auto_clone_pending = {3: "job-3"}
    instance._auto_clone_delayed(0, 3)
    assert not instance._auto_clone_pending


def test_return_to_multi_resumes_watches_then_refreshes():
    instance = _bare_app(multi_active=True)
    events = []
    instance._monitor = SimpleNamespace(
        resume=lambda: events.append("resume"),
        refresh=lambda: events.append("refresh"),
    )
    instance._config = SimpleNamespace(get_show_flash_widget=lambda: True)
    instance._show_flash_widget = lambda: events.append("widget")
    instance._return_to_multi_mode()
    assert events == ["resume", "refresh", "widget"]


def test_guarded_mode_pause_preserves_and_resumes_disk_watches():
    scheduled = {}
    cancelled = []
    next_job = 0

    class Root:
        def after(self, _delay, callback):
            nonlocal next_job
            next_job += 1
            job = f"job-{next_job}"
            scheduled[job] = callback
            return job

        def after_cancel(self, job):
            cancelled.append(job)
            scheduled.pop(job, None)

    monitor = DriveMonitor(Root(), lambda _drives: None)
    monitor.watch_slot(2, 7, lambda: None)
    state = monitor._watches[2]
    first_job = state["job"]
    monitor.pause()
    assert cancelled == [first_job]
    assert state["job"] is None
    assert 2 in monitor._watches
    monitor.resume()
    assert state["job"] in scheduled
    assert state["job"] != first_job


_UI_ROOT = None


def _ui_root():
    global _UI_ROOT
    if _UI_ROOT is None:
        _UI_ROOT = ttk.Window(themename="darkly")
        _UI_ROOT.withdraw()
    return _UI_ROOT


def test_existing_baseline_disables_the_scan_action():
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "protected.json"
        path.write_text(
            '{"schema_version":1,"records":[{"record_id":"test",'
            '"stable_key":null,"descriptor_key":"descriptor:test",'
            '"first_seen_utc":"2026-08-16T00:00:00+00:00",'
            '"last_seen_utc":"2026-08-16T00:00:00+00:00",'
            '"description":"test baseline","disk_snapshot":{}}]}',
            encoding="utf-8",
        )
        frame = GuardedSingleFlashFrame(
            _ui_root(), store=ProtectedHardwareStore(path)
        )
        refreshed = []
        frame.refresh_targets = lambda: refreshed.append(True)
        frame.activate()
        assert frame._protection_text.get() == "Protected baseline loaded."
        assert str(frame._scan_button.cget("state")) == "disabled"
        assert frame._scan_button.cget("text") == "Hardware Protected"
        assert refreshed == [True]
        frame.destroy()


def test_collapsing_guarded_log_shrinks_and_restores_window():
    def geometry_height(window) -> int:
        return int(window.geometry().split("x", 1)[1].split("+", 1)[0])

    root = _ui_root()
    root.geometry("780x700+10000+10000")
    root.deiconify()
    frame = GuardedSingleFlashFrame(root)
    frame.pack(fill="both", expand=True)
    root.update()
    expanded = geometry_height(root)
    frame._toggle_log()
    root.update()
    collapsed = geometry_height(root)
    assert collapsed < expanded
    frame._toggle_log()
    root.update()
    assert geometry_height(root) >= expanded
    frame.destroy()
    root.withdraw()


def test_moved_display_helpers_keep_existing_values():
    assert format_eta(65) == "~1m 05s"
    assert format_speed(2 << 20) == "2.0 MB/s"


def test_flash_widget_sync_does_not_reset_an_unchanged_running_slot():
    calls = []
    widget = SimpleNamespace(
        winfo_exists=lambda: True,
        reset=lambda idx: calls.append(("reset", idx)),
        set_drive=lambda idx, text: calls.append(("drive", idx, text)),
        set_status=lambda idx, status: calls.append(("status", idx, status)),
    )
    drives = [SimpleNamespace(disk_number=2, display="Disk 2")] + [None] * 4
    workers = {0: SimpleNamespace(status=CloneStatus.RUNNING)}
    displayed = {0: 2, 1: None, 2: None, 3: None, 4: None}
    sync_flash_widget(widget, drives, workers, displayed)
    assert calls == [("status", 0, CloneStatus.RUNNING)]


def _run_direct() -> int:
    tests = [
        value
        for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"PASS {test.__name__}")
        except Exception as exc:
            failures += 1
            print(f"FAIL {test.__name__}: {exc}")
    if _UI_ROOT is not None:
        _UI_ROOT.destroy()
    print(f"\n{len(tests) - failures}/{len(tests)} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_run_direct())
