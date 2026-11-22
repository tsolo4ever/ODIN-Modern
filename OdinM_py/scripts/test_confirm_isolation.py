"""Confirm ownership and refresh regressions using synthetic disks/volume APIs."""

import configparser
import sys
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app as app_module  # noqa: E402
import confirmed_disk as isolation  # noqa: E402
import drive_manager  # noqa: E402
import guarded_restore as restore  # noqa: E402
from _confirmation_test_support import FakeConfirmationManager  # noqa: E402
from clone_worker import CloneStatus  # noqa: E402
from config_manager import ConfigManager, DEFAULTS  # noqa: E402
from drive_manager import DriveInfo  # noqa: E402
from test_guarded_restore import FakeDisk, FakeVolume, _disk, _raw, _store  # noqa: E402

GUID = "\\\\?\\Volume{12345678-1234-1234-1234-123456789abc}\\"


def disk():
    return replace(
        _disk(), removable=True, bus_type=7, bus_name="USB", mounted_volumes=("E:\\", GUID)
    )


def drive(number=3, letter="E:", label="Old label"):
    return DriveInfo(
        number,
        letter,
        [letter] if letter else [],
        label=label,
        size_bytes=4096,
        hw_serial="SERIAL-3",
    )


class Paths:
    def __init__(self, events):
        self.events = events
        self.bindings = {"E:\\": GUID, "F:\\": GUID}
        self.fail_remove = None

    def volume_name(self, path):
        return path if path == GUID else self.bindings[path]

    def remove(self, path):
        if path == self.fail_remove:
            raise OSError("remove failed")
        self.events.append("remove " + path)
        del self.bindings[path]

    def restore(self, path, guid):
        self.events.append("restore " + path)
        self.bindings[path] = guid


class Volume(FakeVolume):
    def lock(self):
        self.events.append("lock")
        return True

    def dismount(self):
        self.events.append("dismount")
        return True


class Root:
    def __init__(self):
        self.jobs = []

    def after(self, _delay, callback, *args):
        self.jobs.append((callback, args))

    def tick(self):
        callback, args = self.jobs.pop(0)
        callback(*args)


class IsolationTests(unittest.TestCase):
    def acquire(self, *, target=None, factory=None, owner=3, paths=None, bridge=False):
        self.events = [] if paths is None else paths.events
        self.paths = paths or Paths(self.events)
        return isolation.isolate_disk(
            drive(),
            "",
            target_provider=target or (lambda *_args, **_kwargs: disk()),
            paths=self.paths,
            volume_factory=factory or (lambda _path, write: Volume(self.events)),
            disk_number_provider=lambda _volume: owner,
            bridge_probe=lambda: bridge,
        )

    def test_guid_and_letter_are_one_locked_volume_before_mount_removal(self):
        lease = self.acquire()
        self.assertEqual(self.events, ["lock", "dismount", "remove E:\\"])
        self.assertTrue(lease.active)
        lease.validate(replace(disk(), mounted_volumes=(GUID,)))
        lease.close()
        lease.close()
        self.assertEqual(self.events[-2:], ["unlock", "close"])

    def test_volume_without_a_letter_is_still_locked(self):
        lease = self.acquire(
            target=lambda *_args, **_kwargs: replace(disk(), mounted_volumes=(GUID,))
        )
        self.assertEqual(self.events, ["lock", "dismount"])
        lease.close()

    def test_wrong_disk_volume_is_rejected_before_lock_or_mount_mutation(self):
        with self.assertRaisesRegex(isolation.IsolationError, "different physical disk"):
            self.acquire(owner=4)
        self.assertEqual(self.events, ["unlock", "close"])

    def test_in_use_and_dismount_failure_never_confirm(self):
        for method in ("lock", "dismount"):

            def factory(_path, write, method=method):
                volume = Volume(self.events)
                setattr(volume, method, lambda: False)
                return volume

            with self.subTest(method=method), self.assertRaises(isolation.IsolationError):
                self.acquire(factory=factory)
            self.assertFalse(any(e.startswith("remove") for e in self.events))
            self.assertEqual(self.events[-2:], ["unlock", "close"])

    def test_partial_mount_removal_is_rolled_back(self):
        paths = Paths([])
        paths.fail_remove = "F:\\"
        with self.assertRaisesRegex(isolation.IsolationError, "remove failed"):
            self.acquire(
                paths=paths,
                target=lambda *_args, **_kwargs: replace(
                    disk(), mounted_volumes=("E:\\", "F:\\", GUID)
                ),
            )
        self.assertEqual(paths.bindings["E:\\"], GUID)
        self.assertEqual(paths.events[-3:], ["restore E:\\", "unlock", "close"])

    def test_changed_identity_is_rejected_before_paths_are_removed(self):
        calls = []

        def target(*_args, **_kwargs):
            calls.append(True)
            return disk() if len(calls) == 1 else replace(disk(), device_path="replacement")

        with self.assertRaisesRegex(isolation.IsolationError, "target changed"):
            self.acquire(target=target)
        self.assertEqual(self.events, ["lock", "dismount", "unlock", "close"])

    def test_nonremovable_and_bridge_targets_fail_without_volume_access(self):
        with self.assertRaisesRegex(isolation.IsolationError, "removable"):
            self.acquire(target=lambda *_args, **_kwargs: replace(disk(), removable=False))
        self.assertEqual(self.events, [])
        with self.assertRaisesRegex(isolation.IsolationError, "WSL Bridge"):
            self.acquire(bridge=True)

    def test_protected_or_source_disk_rejection_propagates_without_mutation(self):
        def rejected(*_args, **_kwargs):
            raise restore.GuardedRestoreError("protected or image source disk")

        with self.assertRaisesRegex(restore.GuardedRestoreError, "protected"):
            self.acquire(target=rejected)
        self.assertEqual(self.events, [])

    def test_raw_restore_borrows_confirm_locks_on_success_and_mismatch(self):
        for mismatch in (False, True):
            with self.subTest(mismatch=mismatch), tempfile.TemporaryDirectory() as folder:
                image = Path(folder) / "raw.img"
                _raw(image)
                plan = restore.preflight_image(image, 4096)
                events = []
                lease = isolation.ConfirmedDiskIsolation(disk(), [Volume(events)])
                storage = bytearray(4096)
                opens = []

                def factory(_path, write=False, mismatch=mismatch, storage=storage, opens=opens):
                    opens.append(write)
                    if len(opens) == 3 and mismatch:
                        storage[0] ^= 0xFF
                    return FakeDisk(storage, write)

                def run(plan=plan, lease=lease, folder=folder, factory=factory):
                    return restore.restore_and_verify(
                        plan,
                        disk(),
                        _store(folder),
                        confirmed_disk_number=3,
                        inventory_provider=lambda: [disk()],
                        image_disk_provider=lambda _p: 0,
                        disk_factory=factory,
                        volume_locker=lambda *_args, **_kwargs: self.fail("relocked volume"),
                        flush_disk=lambda _target: None,
                        partition_waiter=lambda _p: None,
                        isolation=lease,
                    )

                if mismatch:
                    with self.assertRaisesRegex(restore.GuardedRestoreError, "mismatch"):
                        run()
                else:
                    self.assertTrue(run().verified)
                self.assertTrue(lease.active)
                self.assertEqual(events, [])
                lease.close()
                self.assertEqual(events, ["unlock", "close"])

    def test_isolated_restore_verifies_then_sets_and_checks_signature_before_refresh(self):
        for failure in (None, "raw", "policy", "cancel", "signature", "refresh"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as folder:
                self._check_restore_order(folder, failure)

    def _check_restore_order(self, folder, failure):
        image = Path(folder) / "raw.img"
        source = _raw(image)
        plan = restore.preflight_image(image, 4096)
        storage = bytearray(4096)
        events = []
        lease = isolation.ConfirmedDiskIsolation(disk(), [Volume(events)])
        source_signature = source[440:444]
        attached_signature = b"\x10\x20\x30\x40"
        used_signature = b"\x11\x21\x31\x41"
        new_signature = b"\x12\x22\x32\x42"
        attached_disk = replace(
            disk(),
            disk_number=6,
            unique_id="ATTACHED-DISK-6",
            mbr_signature=int.from_bytes(attached_signature, "little"),
        )

        class InstrumentedDisk(FakeDisk):
            def write(self, data):
                events.append("write image" if len(data) == len(source) else "write signature")
                return super().write(data)

            def read(self, count):
                if "write signature" in events:
                    events.append("read signature")
                    if failure == "signature":
                        storage[440:444] = source_signature
                elif not self.writable:
                    events.append("read image")
                    if failure == "raw":
                        storage[444] ^= 1
                return super().read(count)

            def update_properties(self):
                events.append("refresh")
                return failure != "refresh"

        def log(message):
            if message.startswith("Mandatory read-back verification passed"):
                events.append("SHA-256 passed")

        def policy(*_args, **_kwargs):
            self.assertIn("SHA-256 passed", events)
            self.assertNotIn("refresh", events)
            self.assertEqual(storage[440:444], source_signature)
            if failure == "policy":
                raise restore.GuardedRestoreError(
                    "configured hash mismatch", target_not_trusted=True
                )
            events.append("policy checked")
            return failure != "cancel"

        def run():
            return restore.restore_and_verify(
                plan,
                disk(),
                _store(folder),
                confirmed_disk_number=3,
                inventory_provider=lambda: [disk(), attached_disk],
                image_disk_provider=lambda _path: 0,
                disk_factory=lambda _path, write=False: InstrumentedDisk(storage, write),
                flush_disk=lambda _target: events.append("flush"),
                partition_waiter=lambda _path: None,
                on_log=log,
                randomize_signature=True,
                retain_volume_locks=True,
                isolation=lease,
            )

        with (
            patch.object(restore, "_configured_policy_checks", side_effect=policy),
            patch.object(restore, "_issued_signatures", {int.from_bytes(used_signature, "little")}),
            patch.object(
                restore.os,
                "urandom",
                side_effect=(source_signature, attached_signature, used_signature, new_signature),
            ) as draw,
        ):
            if failure in (None, "cancel"):
                result = run()
                self.assertEqual(result.cancelled, failure == "cancel")
                self.assertEqual(result.signature_randomized, failure is None)
            else:
                with self.assertRaises(restore.GuardedRestoreError) as error:
                    run()
                self.assertTrue(error.exception.target_not_trusted)

            if failure in (None, "signature", "refresh"):
                self.assertEqual(draw.call_count, 4)
                self.assertLess(events.index("policy checked"), events.index("write signature"))
                self.assertGreater(events.index("read signature"), events.index("write signature"))
            else:
                draw.assert_not_called()
                self.assertNotIn("write signature", events)

        if failure in (None, "refresh"):
            self.assertEqual(events[-4:], ["write signature", "flush", "read signature", "refresh"])
            self.assertEqual(storage[440:444], new_signature)
            self.assertEqual(storage[:440], source[:440])
            self.assertEqual(storage[444 : len(source)], source[444:])
        else:
            self.assertNotIn("refresh", events)
        self.assertTrue(lease.active)
        self.assertNotIn("unlock", events)
        self.assertNotIn("close", events)
        lease.close()
        self.assertEqual(events[-2:], ["unlock", "close"])

    def test_pending_isolation_is_not_ready_until_ui_delivery(self):
        root = Root()
        lease = isolation.ConfirmedDiskIsolation(disk(), [])
        manager = isolation.ConfirmationManager(root, provider=lambda *_args: lease)
        ready = []
        try:
            manager.request(0, drive(), "", ready.append, self.fail)
            manager._pending[0].result(timeout=2)
            self.assertEqual(ready, [])
            self.assertTrue(manager.busy)
            root.tick()
            self.assertEqual(ready, [lease])
            self.assertIs(manager.get(0), lease)
        finally:
            manager.close()
        self.assertFalse(lease.active)

    def test_worker_failure_keeps_borrowed_lease_until_explicit_release(self):
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "raw.img"
            _raw(image)
            plan = restore.preflight_image(image, 4096)
            events, done = [], []
            lease = isolation.ConfirmedDiskIsolation(disk(), [Volume(events)])

            def fail(*_args, **kwargs):
                self.assertIs(kwargs["isolation"], lease)
                raise restore.GuardedRestoreError("read-back mismatch", target_not_trusted=True)

            root = SimpleNamespace(after=lambda _delay, callback, *args: callback(*args))
            worker = restore.MultiFlashNativeRestoreWorker(
                root,
                3,
                str(image),
                lambda *_args: None,
                lambda *_args: None,
                done.append,
                isolation=lease,
                retain_volume_locks=True,
                target_provider=lambda *_args, **_kwargs: disk(),
                partition_policy_provider=lambda _path: (1,),
                preflight_provider=lambda *_args, **_kwargs: plan,
                restore_provider=fail,
            )
            worker.start()
            worker._thread.join(timeout=2)
            self.assertEqual(done, [CloneStatus.FAILED])
            self.assertTrue(worker.has_retained_locks)
            self.assertTrue(lease.active)
            self.assertEqual(events, [])
            worker.release_retained_locks()
            worker.release_retained_locks()
            self.assertEqual(events, ["unlock", "close"])

    def test_removal_cancels_pending_result_without_late_confirmation(self):
        root = Root()
        started, finish = threading.Event(), threading.Event()
        lease = isolation.ConfirmedDiskIsolation(disk(), [])

        def provider(*_args):
            started.set()
            finish.wait(2)
            return lease

        manager = isolation.ConfirmationManager(root, provider=provider)
        ready = []
        manager.request(0, drive(), "", ready.append, self.fail)
        self.assertTrue(started.wait(2))
        manager.release(0)
        finish.set()
        manager.close()
        root.tick()
        self.assertEqual(ready, [])
        self.assertFalse(lease.active)

    def test_background_failure_leaves_slot_unconfirmed(self):
        root = Root()

        def fail(*_args):
            raise isolation.IsolationError("busy volume")

        manager = isolation.ConfirmationManager(root, provider=fail)
        failed = []
        try:
            manager.request(
                0, drive(), "", lambda _lease: self.fail("unexpected ready"), failed.append
            )
            try:
                manager._pending[0].result(timeout=2)
            except isolation.IsolationError:
                pass
            root.tick()
            self.assertEqual(failed, ["busy volume"])
            self.assertIsNone(manager.get(0))
        finally:
            manager.close()


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        config = object.__new__(ConfigManager)
        config._path = str(Path(self.folder.name) / "test.ini")
        config._cfg = configparser.ConfigParser()
        config._cfg["settings"] = dict(DEFAULTS, auto_clone="false", show_flash_widget="false")
        with patch.object(drive_manager.DriveMonitor, "refresh", lambda _self: None):
            self.app = app_module.OdinMApp(
                config, confirmation_manager_factory=FakeConfirmationManager
            )
        self.app._root.withdraw()
        self.app._wsl_bridge_blocks = lambda _operation: False

    def tearDown(self):
        self.app._confirmation.close()
        self.app._root.destroy()
        self.folder.cleanup()

    def test_refresh_changes_only_unconfirmed_metadata_and_keeps_prompt(self):
        app = self.app
        mirrored = []
        app._flash_widget = SimpleNamespace(
            winfo_exists=lambda: True,
            reset=lambda idx: mirrored.append((idx, "reset")),
            set_drive=lambda idx, display: mirrored.append((idx, display)),
            set_status=lambda *_args: None,
        )
        app._on_drives_changed([drive(3), drive(4, "F:")])
        app._confirm_slot(1)
        confirmed = app._drives[1]
        app._window.set_slot_status(1, CloneStatus.RUNNING)
        app._window.set_slot_progress(1, 37)
        app._window.set_slot_speed(1, "12 MB/s")
        app._window.set_slot_eta(1, "~1m")
        fresh = drive(3, "", "New label")
        mirrored.clear()
        app._monitor.refresh = lambda: app._on_drives_changed([fresh, drive(4, "G:")])
        with patch.object(app_module, "debug_probe_disks", return_value=[]):
            app._refresh_disks()
        self.assertIs(app._drives[0], fresh)
        self.assertEqual(app._window._slots[0]._info_var.get(), fresh.display)
        self.assertTrue(app._window._slots[0]._awaiting_confirm)
        self.assertEqual(str(app._window._slots[0]._btn.cget("state")), "disabled")
        self.assertIs(app._drives[1], confirmed)
        self.assertEqual(app._window._slots[1]._pct_var.get(), "37%")
        self.assertEqual(app._window._slots[1]._speed_var.get(), "12 MB/s")
        self.assertEqual(app._window._slots[1]._eta_var.get(), "~1m")
        self.assertEqual(mirrored, [(0, fresh.display)])

    def test_confirm_updates_main_label_and_owns_isolation(self):
        app = self.app
        app._on_drives_changed([drive()])
        app._confirm_slot(0)
        self.assertTrue(app._confirmation.get(0).active)
        self.assertEqual(app._drives[0].all_letters, [])
        self.assertIn("no drive letter", app._window._slots[0]._info_var.get())
        self.assertFalse(app._window._slots[0]._awaiting_confirm)

    def test_failed_confirmation_delivery_returns_slot_to_confirm_prompt(self):
        app = self.app
        app._on_drives_changed([drive()])
        app._confirm_slot(0)
        app._confirmation.release(0)
        app._confirmation_failed(0, "delivery failed")
        self.assertNotIn(0, app._locked_disk_nums)
        self.assertTrue(app._window._slots[0]._awaiting_confirm)
        self.assertEqual(str(app._window._slots[0]._btn.cget("state")), "disabled")


if __name__ == "__main__":
    unittest.main()
