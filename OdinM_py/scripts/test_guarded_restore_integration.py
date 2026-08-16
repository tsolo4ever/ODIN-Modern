"""Headless worker, coordinator, and guarded confirmation integration checks."""

from __future__ import annotations

import hashlib
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ttkbootstrap as ttk  # noqa: E402

import guarded_restore as restore_module  # noqa: E402
from clone_worker import CloneStatus  # noqa: E402
from guarded_flash_safety import DiskIdentity, ProtectedHardwareStore  # noqa: E402
from guarded_restore import (  # noqa: E402
    GuardedImagePlan,
    GuardedRestoreCoordinator,
    GuardedRestoreError,
    GuardedRestoreResult,
    GuardedRestoreWorker,
)
from ui.guarded_single_flash import GuardedSingleFlashFrame  # noqa: E402


def _disk() -> DiskIdentity:
    return DiskIdentity(
        3,
        "TARGET-3",
        "SERIAL-3",
        "FIXED TEST SSD",
        "TEST",
        4096,
        11,
        "SATA",
        r"\\?\scsi#disk3",
        "Port 3",
        ("E:\\",),
    )


def _plan(path: Path, *, temporary: bool = False) -> GuardedImagePlan:
    path.write_bytes(b"X" * 512)
    return GuardedImagePlan(path, path, "raw", 512, 512, "a" * 64, temporary)


_UI_ROOT = None


def _ui_root():
    global _UI_ROOT
    if _UI_ROOT is None:
        _UI_ROOT = ttk.Window(themename="darkly")
        _UI_ROOT.withdraw()
    return _UI_ROOT


class ImmediateRoot:
    def after(self, _delay, callback, *args):
        callback(*args)


def test_worker_success_marshals_progress_and_cleans_temporary_source():
    with tempfile.TemporaryDirectory() as folder:
        source = Path(folder) / "spooled.raw"
        plan = _plan(source, temporary=True)
        progress = []
        done = []

        def provider(*_args, on_progress, on_log, **_kwargs):
            on_progress("write", 256, 512)
            on_log("simulated write")
            return GuardedRestoreResult(512, "a" * 64, "a" * 64, True, False, False, 0.1)

        worker = GuardedRestoreWorker(
            ImmediateRoot(),
            plan,
            _disk(),
            ProtectedHardwareStore(Path(folder) / "protected.json"),
            lambda phase, percent: progress.append((phase, percent)),
            lambda line: progress.append(("log", line)),
            done.append,
            restore_provider=provider,
        )
        worker.start()
        worker._thread.join(timeout=2)
        assert progress == [("write", 50), ("log", "simulated write")]
        assert done == [CloneStatus.DONE]
        assert not source.exists()


def test_worker_failure_preserves_the_untrusted_target_signal():
    with tempfile.TemporaryDirectory() as folder:
        source = Path(folder) / "raw.img"
        plan = _plan(source)

        def provider(*_args, **_kwargs):
            raise GuardedRestoreError("simulated short write", target_not_trusted=True)

        worker = GuardedRestoreWorker(
            ImmediateRoot(), plan, _disk(), ProtectedHardwareStore(Path(folder) / "p.json"),
            lambda *_args: None, lambda *_args: None, lambda *_args: None,
            restore_provider=provider,
        )
        worker.start()
        worker._thread.join(timeout=2)
        assert worker.status == CloneStatus.FAILED
        assert isinstance(worker.error, GuardedRestoreError)
        assert worker.error.target_not_trusted


def test_configured_whole_image_policy_hash_runs_after_mandatory_verify():
    with tempfile.TemporaryDirectory() as folder:
        source = Path(folder) / "raw.img"
        plan = _plan(source)
        data = source.read_bytes()

        class PolicyDisk:
            def __init__(self):
                self.position = 0

            def seek(self, offset):
                self.position = offset

            def read(self, count):
                block = data[self.position : self.position + count]
                self.position += len(block)
                return block

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

        expected = hashlib.sha256(data).hexdigest()

        class Config:
            def __init__(self, value):
                self.value = value

            def get_enabled_partitions(self, _path):
                return {
                    0: {
                        "sha1_enabled": False,
                        "sha1_value": "",
                        "sha256_enabled": True,
                        "sha256_value": self.value,
                    }
                }

        logs = []
        assert restore_module._configured_policy_checks(
            plan,
            r"\\.\PhysicalDrive3",
            disk_factory=lambda _path: PolicyDisk(),
            hash_config_provider=lambda: Config(expected),
            should_cancel=None,
            on_progress=lambda *_args: None,
            on_log=logs.append,
        )
        assert "passed" in logs[-1]
        try:
            restore_module._configured_policy_checks(
                plan,
                r"\\.\PhysicalDrive3",
                disk_factory=lambda _path: PolicyDisk(),
                hash_config_provider=lambda: Config("0" * 64),
                should_cancel=None,
                on_progress=lambda *_args: None,
                on_log=lambda _line: None,
            )
        except GuardedRestoreError as exc:
            assert exc.target_not_trusted
        else:
            raise AssertionError("configured policy mismatch was accepted")


class FakeWindow:
    def __init__(self):
        self.logs = []
        self.finishes = []

    def guarded_log(self, text, warning=False):
        self.logs.append((text, warning))

    def guarded_set_progress(self, *_args):
        pass

    def guarded_finish_attempt(self, text, warning=False):
        self.finishes.append((text, warning))


def _worker_factory(status, *, error=None, partial=False):
    class FakeWorker:
        def __init__(self, _root, plan, disk, _store, _progress, _log, done):
            self.plan = plan
            self.disk = disk
            self._done = done
            self.status = CloneStatus.IDLE
            self.error = error
            self.result = GuardedRestoreResult(
                512, "a" * 64, "a" * 64, status == CloneStatus.DONE,
                status == CloneStatus.STOPPED, partial, 0.1,
            )
            self.stopped = False

        def start(self):
            self.status = status
            self._done(status)

        def stop(self):
            self.stopped = True

    return FakeWorker


def test_coordinator_reports_only_verified_done_as_success():
    with tempfile.TemporaryDirectory() as folder:
        window = FakeWindow()
        coordinator = GuardedRestoreCoordinator(
            ImmediateRoot(), window, store=ProtectedHardwareStore(Path(folder) / "p.json"),
            worker_factory=_worker_factory(CloneStatus.DONE),
        )
        coordinator.prepare(_disk(), _plan(Path(folder) / "raw.img"))
        assert "SUCCESS" in window.finishes[0][0]
        assert window.finishes[0][1] is False
        assert coordinator.worker is None


def test_coordinator_failure_and_cancellation_warn_about_untrusted_targets():
    with tempfile.TemporaryDirectory() as folder:
        for status, error in (
            (CloneStatus.STOPPED, None),
            (CloneStatus.FAILED, GuardedRestoreError("bad verify", target_not_trusted=True)),
        ):
            window = FakeWindow()
            coordinator = GuardedRestoreCoordinator(
                ImmediateRoot(), window,
                store=ProtectedHardwareStore(Path(folder) / f"{status}.json"),
                worker_factory=_worker_factory(status, error=error, partial=True),
            )
            coordinator.prepare(_disk(), _plan(Path(folder) / f"{status}.img"))
            assert window.finishes[0][1] is True
            assert "reflash" in window.finishes[0][0].casefold()


def test_guarded_ui_dispatches_only_after_typed_confirmation():
    with tempfile.TemporaryDirectory() as folder:
        root = _ui_root()
        dispatched = []
        frame = GuardedSingleFlashFrame(
            root,
            store=ProtectedHardwareStore(Path(folder) / "p.json"),
            on_prepare_flash=lambda disk, plan: dispatched.append((disk, plan)),
        )
        frame.state.enter()
        frame.confirm_disk_number = lambda _disk, _summary: True
        plan = _plan(Path(folder) / "raw.img")
        frame._confirm_preflight(_disk(), plan)
        root.update_idletasks()
        assert dispatched == [(_disk(), plan)]
        assert frame.busy
        plan.cleanup()
        frame.destroy()


def test_guarded_ui_confirmation_cancel_cleans_preflight_and_image_state():
    with tempfile.TemporaryDirectory() as folder:
        root = _ui_root()
        frame = GuardedSingleFlashFrame(root, store=ProtectedHardwareStore(Path(folder) / "p.json"))
        frame.state.enter()
        frame.state.image_path = r"D:\roulette.img"
        frame.confirm_disk_number = lambda _disk, _summary: False
        source = Path(folder) / "spooled.raw"
        plan = _plan(source, temporary=True)
        frame._confirm_preflight(_disk(), plan)
        root.update_idletasks()
        assert not source.exists()
        assert frame.image_path == ""
        assert not frame.busy
        frame.destroy()


def _run_direct() -> int:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_") and callable(value)]
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
