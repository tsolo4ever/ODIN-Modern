"""Raw flash failure/cancellation ownership tests using in-memory disks only."""

import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(1, str(Path(__file__).resolve().parent))

import app as app_module  # noqa: E402
import guarded_restore as restore  # noqa: E402
from clone_worker import CloneStatus  # noqa: E402
from test_guarded_restore import FakeDisk, FakeVolume, _disk, _raw, _store  # noqa: E402


class ImmediateRoot:
    def after(self, _delay, callback, *args):
        callback(*args)


class RetainedFailureTests(unittest.TestCase):
    def attempt(self, folder, *, retain, failure=None, cancel_phase=None):
        image = Path(folder) / "raw.img"
        _raw(image)
        plan = restore.preflight_image(image, 4096)
        disk = _disk()
        storage = bytearray(disk.size_bytes)
        self.events = []
        opens = 0
        phase = "source_check"

        def factory(_path, write=False):
            nonlocal opens
            opens += 1
            if opens == 3:
                if failure == "mismatch":
                    storage[0] ^= 0xFF
                if failure == "read open":
                    raise OSError("simulated read open failure")
            target = FakeDisk(storage, write, short_write=failure == "short write" and write)
            if failure == "refresh":
                target.update_properties = lambda: False
            if failure == "short read" and opens == 3:
                target.read = lambda _count: b""
            return target

        def flush(_target):
            if failure == "flush":
                raise OSError("simulated flush failure")

        def wait(_path):
            nonlocal phase
            phase = "verify"
            if failure == "partition":
                raise RuntimeError("simulated partition failure")

        def progress(current, _done, _total):
            nonlocal phase
            phase = current

        def cancelled():
            return (cancel_phase == "write" and phase == "source_check") or (
                cancel_phase == "verify" and phase == "verify"
            )

        return restore.restore_and_verify(
            plan,
            disk,
            _store(folder),
            confirmed_disk_number=disk.disk_number,
            inventory_provider=lambda: [disk],
            image_disk_provider=lambda _path: 0,
            disk_factory=factory,
            volume_locker=lambda *_args, **_kwargs: [FakeVolume(self.events)],
            flush_disk=flush,
            partition_waiter=wait,
            on_progress=progress,
            should_cancel=cancelled,
            retain_volume_locks=retain,
        )

    def test_failures_retain_locks_only_when_requested(self):
        for failure in (
            "mismatch",
            "short write",
            "flush",
            "refresh",
            "partition",
            "read open",
            "short read",
        ):
            for retain in (False, True):
                with self.subTest(failure=failure, retain=retain):
                    with tempfile.TemporaryDirectory() as folder:
                        with self.assertRaises(restore.GuardedRestoreError) as caught:
                            self.attempt(folder, retain=retain, failure=failure)
                    error = caught.exception
                    self.assertTrue(error.target_not_trusted)
                    self.assertEqual(len(error.retained_volume_locks), int(retain))
                    self.assertEqual(self.events, [] if retain else ["unlock", "close"])
                    if retain:
                        restore.release_volume_locks(error.retained_volume_locks)
                        self.assertEqual(self.events, ["unlock", "close"])
                    if failure == "mismatch":
                        self.assertIn("mandatory target read-back SHA-256 mismatch", str(error))

    def test_cancelled_write_and_verify_retain_acquired_locks(self):
        for phase in ("write", "verify"):
            for retain in (False, True):
                with self.subTest(phase=phase, retain=retain):
                    with tempfile.TemporaryDirectory() as folder:
                        result = self.attempt(folder, retain=retain, cancel_phase=phase)
                    self.assertTrue(result.cancelled)
                    self.assertFalse(result.verified)
                    self.assertEqual(result.target_not_trusted, phase == "verify")
                    self.assertEqual(len(result.retained_volume_locks), int(retain))
                    self.assertEqual(self.events, [] if retain else ["unlock", "close"])
                    restore.release_volume_locks(result.retained_volume_locks)
                    self.assertEqual(self.events, ["unlock", "close"])

    def test_worker_owns_failure_and_stopped_handles_before_done_callback(self):
        for stopped in (False, True):
            with self.subTest(stopped=stopped), tempfile.TemporaryDirectory() as folder:
                events = []
                volume = FakeVolume(events)
                disk = replace(_disk(), removable=True, bus_type=7, bus_name="USB")
                image = Path(folder) / "raw.img"
                _raw(image)
                plan = restore.preflight_image(image, disk.size_bytes)
                error = restore.GuardedRestoreError(
                    "mandatory mismatch",
                    target_not_trusted=True,
                    retained_volume_locks=(volume,),
                )

                def provider(
                    *_args, stopped=stopped, plan=plan, volume=volume, error=error, **_kwargs
                ):
                    if stopped:
                        return restore.GuardedRestoreResult(
                            512,
                            plan.sha256,
                            "",
                            False,
                            True,
                            True,
                            0.1,
                            retained_volume_locks=(volume,),
                        )
                    raise error

                callbacks = []
                holder = []
                worker = restore.MultiFlashNativeRestoreWorker(
                    ImmediateRoot(),
                    disk.disk_number,
                    str(image),
                    lambda *_args: None,
                    lambda *_args: None,
                    lambda status, callbacks=callbacks, holder=holder: callbacks.append(
                        (status, holder[0].has_retained_locks)
                    ),
                    target_provider=lambda *_args, disk=disk, **_kwargs: disk,
                    partition_policy_provider=lambda _path: (1,),
                    preflight_provider=lambda *_args, plan=plan, **_kwargs: plan,
                    restore_provider=provider,
                    retain_volume_locks=True,
                )
                holder.append(worker)
                worker.start()
                worker._thread.join(timeout=2)
                self.assertFalse(worker._thread.is_alive())
                status = CloneStatus.STOPPED if stopped else CloneStatus.FAILED
                self.assertEqual(callbacks, [(status, True)])
                if not stopped:
                    self.assertIs(worker.error, error)
                self.assertEqual(events, [])
                worker.release_retained_locks()
                worker.release_retained_locks()
                self.assertFalse(worker.has_retained_locks)
                self.assertEqual(events, ["unlock", "close"])

    def make_app(self):
        app = object.__new__(app_module.OdinMApp)
        app.logs = []
        app.events = []
        app._window = SimpleNamespace(
            multi_flash_active=True,
            image_path=__file__,
            log=app.logs.append,
            set_slot_status=lambda *_args, **_kwargs: app.events.append("status"),
            set_slot_speed=lambda *_args: None,
            set_slot_eta=lambda *_args: None,
        )
        app._drives = [SimpleNamespace(disk_number=3)]
        app._locked_disk_nums = {0: 3}
        app._queue = []
        app._speed_samples = {}
        app._finished_disk_nums = set()
        app._config = SimpleNamespace(get_verify_after_clone=lambda: False)
        app._flash_set_status = lambda *_args: None
        app._flash_set_speed = lambda *_args: None
        app._flash_set_eta = lambda *_args: None
        app._drain_queue = lambda: app.events.append("drain")
        app._start_target_verify = lambda *_args: app.events.append("verify")
        app._wsl_bridge_blocks = lambda _operation: False
        worker = object.__new__(restore.MultiFlashNativeRestoreWorker)
        worker._disk_number = 3
        worker._retained_volume_locks = [FakeVolume(app.events)]
        worker.result = None
        worker.status = CloneStatus.FAILED
        app._workers = {0: worker}
        return app, worker

    def test_failure_ui_keeps_lock_and_blocks_retry_and_verify(self):
        app, worker = self.make_app()
        app._on_worker_done(0, CloneStatus.FAILED)
        self.assertTrue(worker.has_retained_locks)
        self.assertTrue(any("Unverified disk remains locked" in line for line in app.logs))
        self.assertEqual(app._finished_disk_nums, set())
        app._verify_slot(0)
        app._start_slot(0)
        self.assertNotIn("verify", app.events)
        self.assertNotIn("unlock", app.events)
        self.assertTrue(any("before starting another flash" in line for line in app.logs))

    def test_removal_before_done_handoff_releases_stale_handles(self):
        for drive in (None, SimpleNamespace(disk_number=4)):
            app, worker = self.make_app()
            app._drives[0] = drive
            app._on_worker_done(0, CloneStatus.FAILED)
            self.assertFalse(worker.has_retained_locks)
            self.assertEqual(app.events, ["unlock", "close", "drain"])


if __name__ == "__main__":
    unittest.main()
