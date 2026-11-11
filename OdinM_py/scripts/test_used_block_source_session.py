"""Mocked source ownership, transport, identity, and archive publication tests."""

from __future__ import annotations

import copy
import hashlib
import io
import sys
import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]

import used_block_archive as archive  # noqa: E402
import used_block_source_session as handoff  # noqa: E402
from pyimager_worker import PyImagerWorker  # noqa: E402


MBR = "a" * 64
VOLUME = "\\\\?\\Volume{source}\\"
IDENTITY = {"pnp": "USBSTOR\\selected", "unique": "reader:card", "size": 4096}


class World:
    def __init__(self, route="usbipd", *, offline=False, bound=True, mounted=True):
        self.row = {
            **IDENTITY,
            "number": 2,
            "bus": 7 if route == "usbipd" else 17,
            "media": "Removable Media" if route == "usbipd" else "Fixed hard disk media",
            "offline": offline,
            "readonly": False,
            "system": False,
            "boot": False,
            "paths": [VOLUME, "E:\\"] if mounted else [],
        }
        self.reader = {
            "InstanceId": "USB\\reader",
            "BusId": "5-4",
            "PersistedGuid": "existing" if bound else None,
            "ClientIPAddress": None,
        }
        self.commands = []
        self.states = []
        self.attached = False
        self.cancel = False
        self.fail = None
        self.fail_after_attach = False

    def run(self, args, **_kwargs):
        self.commands.append(args)
        if self.fail == args[1]:
            raise handoff.SourceHandoffError("injected " + args[1] + " failure")
        if args[1] == "bind":
            self.reader["PersistedGuid"] = "created"
        if args[1] in ("attach", "--mount"):
            self.attached = True
            if args[0] == "usbipd":
                self.reader["ClientIPAddress"] = "172.0.0.2"
            if self.fail_after_attach:
                raise handoff.SourceHandoffError("attach changed state then failed")
        if args[1] in ("detach", "--unmount"):
            self.attached = False
            self.reader["ClientIPAddress"] = None
            if args[0] == "usbipd":
                self.row["number"] = 7
        if args[1] == "unbind":
            self.reader["PersistedGuid"] = None
        return ""

    def offline(self, identity, value):
        assert handoff._same_disk(self.row, identity)
        self.states.append(value)
        self.row["offline"] = value

    @contextmanager
    def active(self):
        with ExitStack() as stack:
            stack.enter_context(
                patch.object(
                    handoff, "_windows_disks", side_effect=lambda: [copy.deepcopy(self.row)]
                )
            )
            stack.enter_context(patch.object(handoff, "_check_paths"))
            stack.enter_context(
                patch.object(
                    handoff, "_reader_for_disk", side_effect=lambda *_: copy.deepcopy(self.reader)
                )
            )
            stack.enter_context(
                patch.object(
                    handoff, "_usb_devices", side_effect=lambda: [copy.deepcopy(self.reader)]
                )
            )
            stack.enter_context(patch.object(handoff, "_lock_volumes", return_value=[]))
            stack.enter_context(patch.object(handoff, "_set_offline", side_effect=self.offline))
            stack.enter_context(patch.object(handoff, "_run", side_effect=self.run))
            stack.enter_context(
                patch.object(
                    handoff,
                    "_wsl_disks",
                    side_effect=lambda: {"/dev/sdz": 4096} if self.attached else {},
                )
            )
            stack.enter_context(patch.object(handoff, "_wsl_mbr_sha256", return_value=MBR))
            stack.enter_context(
                patch.object(handoff, "_wait_for_attached_disk", return_value="/dev/sdz")
            )
            stack.enter_context(patch.object(handoff, "_set_source_read_only"))
            stack.enter_context(
                patch.object(
                    handoff,
                    "_wsl_partitions",
                    return_value={
                        1: SimpleNamespace(path="/dev/sdz1", mounted=False),
                    },
                )
            )
            stack.enter_context(
                patch.object(
                    handoff,
                    "_run_wsl",
                    side_effect=lambda args: "1" if "--getro" in args else "4096",
                )
            )
            yield self

    def session(self, **kwargs):
        return handoff.SourceSession(2, should_cancel=lambda: self.cancel, **kwargs)


class SessionTests(unittest.TestCase):
    def test_both_routes_and_restoration(self):
        for route in ("physical", "usbipd"):
            for mounted in (False, True):
                with (
                    self.subTest(route=route, mounted=mounted),
                    World(route, mounted=mounted).active() as world,
                ):
                    session = world.session()
                    self.assertEqual(session.route, route)
                    session.attach(MBR)
                    session.verify()
                    session.close()
                    session.close()
                    self.assertFalse(world.attached)
                    self.assertEqual(world.states, [True, False] if route == "physical" else [])
                    self.assertEqual(session.disk_number, 2 if route == "physical" else 7)
                    self.assertEqual(world.reader["PersistedGuid"], "existing")
                    self.assertEqual(
                        len([c for c in world.commands if c[1] in ("detach", "--unmount")]), 1
                    )

    def test_fixed_usb_enclosure_keeps_physical_path(self):
        with World("physical").active() as world:
            world.row["bus"] = 7
            self.assertEqual(world.session().route, "physical")

    def test_original_offline_state_retained(self):
        with World("physical", offline=True, mounted=False).active() as world:
            session = world.session()
            session.attach(MBR)
            session.close()
            self.assertEqual(world.states, [])
            self.assertTrue(world.row["offline"])

    def test_only_new_binding_is_removed(self):
        with World(bound=False).active() as world:
            session = world.session()
            session.attach(MBR)
            session.close()
            self.assertEqual([c[1] for c in world.commands], ["bind", "attach", "detach", "unbind"])
            self.assertIsNone(world.reader["PersistedGuid"])

    def test_renumbered_source_resolves_by_identity(self):
        with World().active() as world:
            world.row["number"] = 9
            session = world.session(expected_source={**IDENTITY, "mbr_sha256": MBR})
            self.assertEqual(session.disk_number, 9)

    def test_missing_or_duplicate_identity_is_rejected(self):
        with World().active() as world:
            with self.assertRaisesRegex(handoff.SourceHandoffError, "missing"):
                world.session(expected_source={**IDENTITY, "unique": "other"})
            with patch.object(handoff, "_windows_disks", return_value=[world.row, world.row]):
                with self.assertRaisesRegex(handoff.SourceHandoffError, "ambiguous"):
                    world.session(expected_source=IDENTITY)
            self.assertEqual(world.commands, [])

    def test_changed_boot_or_logical_layout_rejected_before_attach(self):
        with World().active() as world:
            session = world.session(expected_source={**IDENTITY, "mbr_sha256": "b" * 64})
            with self.assertRaisesRegex(handoff.SourceHandoffError, "boot/layout"):
                session.attach(MBR)
            session.close()
            session = world.session(
                expected_source={**IDENTITY, "layout": [[5, 0x83, 100, 20, 99]]}
            )
            with self.assertRaisesRegex(handoff.SourceHandoffError, "partition layout"):
                session.check_layout(
                    [
                        SimpleNamespace(
                            number=5, type_code=0x83, start_lba=101, sector_count=20, table_lba=99
                        )
                    ]
                )
            self.assertEqual(world.commands, [])

    def test_system_disk_rejected(self):
        with World().active() as world:
            world.row["system"] = True
            with self.assertRaisesRegex(handoff.SourceHandoffError, "system/boot"):
                world.session()

    def test_preexisting_wsl_attachment_is_not_owned(self):
        with World("physical").active() as world:
            world.attached = True
            session = world.session()
            with self.assertRaisesRegex(handoff.SourceHandoffError, "already in WSL"):
                session.attach(MBR)
            session.close()
            self.assertEqual(world.commands, [])
            self.assertTrue(world.attached)

    def test_attach_failure_with_and_without_partial_attachment(self):
        for route in ("physical", "usbipd"):
            for partial in (False, True):
                with (
                    self.subTest(route=route, partial=partial),
                    World(route, bound=False).active() as world,
                ):
                    session = world.session()
                    world.fail_after_attach = partial
                    world.fail = None if partial else ("attach" if route == "usbipd" else "--mount")
                    with self.assertRaises(handoff.SourceHandoffError):
                        session.attach(MBR)
                    session.close()
                    self.assertFalse(world.attached)
                    self.assertFalse(world.row["offline"])
                    if route == "usbipd":
                        self.assertIsNone(world.reader["PersistedGuid"])

    def test_cancel_before_and_after_attach(self):
        for after in (False, True):
            with self.subTest(after=after), World("physical").active() as world:
                session = world.session()
                world.cancel = not after
                with patch.object(
                    handoff,
                    "_set_source_read_only",
                    side_effect=lambda _device: setattr(world, "cancel", True),
                ):
                    with self.assertRaises(handoff.Ext4CompactCaptureCancelled):
                        session.attach(MBR)
                session.close()
                self.assertFalse(world.attached)
                self.assertFalse(world.row["offline"])

    def test_readonly_or_discovery_failure_still_detaches(self):
        for name in ("_set_source_read_only", "_wait_for_attached_disk"):
            with self.subTest(name=name), World().active() as world:
                session = world.session()
                with patch.object(
                    handoff,
                    name,
                    side_effect=handoff.SourceHandoffError("ambiguous or read-only failure"),
                ):
                    with self.assertRaises(handoff.SourceHandoffError):
                        session.attach(MBR)
                session.close()
                self.assertFalse(world.attached)

    def test_detach_failure_is_visible_and_does_not_online_attached_disk(self):
        with World("physical").active() as world:
            session = world.session()
            session.attach(MBR)
            world.fail = "--unmount"
            with self.assertRaisesRegex(handoff.SourceHandoffError, "cleanup failed"):
                session.close()
            self.assertEqual(world.states, [True])
            self.assertTrue(world.attached)

    def test_missing_windows_mount_after_detach_is_not_success(self):
        with World().active() as world:
            session = world.session()
            session.attach(MBR)
            world.row["paths"] = []
            with patch.object(handoff.time, "monotonic", side_effect=[0, 100]):
                with self.assertRaisesRegex(handoff.SourceHandoffError, "availability"):
                    session.close()

    def test_readonly_change_stops_verification(self):
        with World().active() as world:
            session = world.session()
            session.attach(MBR)
            with patch.object(handoff, "_run_wsl", return_value="0"):
                with self.assertRaisesRegex(handoff.SourceHandoffError, "read-only"):
                    session.verify()
            session.close()


class ReleaseAndMappingTests(unittest.TestCase):
    def test_busy_second_volume_never_forces_dismount(self):
        first, second = Mock(), Mock()
        first.lock.return_value, second.lock.return_value = True, False
        with patch.object(handoff.pyimager, "Win32Disk", side_effect=[first, second]):
            with self.assertRaisesRegex(handoff.SourceHandoffError, "busy"):
                handoff._lock_volumes({"paths": [VOLUME, VOLUME.replace("source", "second")]})
        first.dismount.assert_not_called()
        second.dismount.assert_not_called()
        first.close.assert_called_once()
        second.close.assert_called_once()

    def test_all_locks_precede_dismount_and_handles_close(self):
        events = []
        handles = [Mock(), Mock()]
        for index, handle in enumerate(handles):
            handle.lock.side_effect = lambda i=index: events.append(("lock", i)) or True
            handle.dismount.side_effect = lambda i=index: events.append(("dismount", i)) or True
        with patch.object(handoff.pyimager, "Win32Disk", side_effect=handles):
            owned = handoff._lock_volumes({"paths": [VOLUME, VOLUME.replace("source", "second")]})
            self.assertEqual([e[0] for e in events], ["lock", "lock", "dismount", "dismount"])
            handoff._release_volumes(owned)
        for handle in handles:
            handle.close.assert_called_once()

    def test_destination_or_junction_on_source_is_rejected(self):
        with patch.object(handoff, "_volume_for_path", return_value=VOLUME.casefold()):
            with self.assertRaisesRegex(handoff.SourceHandoffError, "on the selected source"):
                handoff._check_paths({"paths": [VOLUME]}, [Path("D:/junction/output.img")])

    def test_usb_mapping_requires_unique_single_disk_reader(self):
        world = World()
        with (
            patch.object(handoff, "_usb_devices", return_value=[world.reader]),
            patch.object(
                handoff,
                "_pnp_ancestors",
                return_value={"usb\\reader"},
            ),
        ):
            self.assertEqual(handoff._reader_for_disk(world.row, [world.row]), world.reader)
            other = {**world.row, "pnp": "other", "unique": "other"}
            with self.assertRaisesRegex(handoff.SourceHandoffError, "multiple disks"):
                handoff._reader_for_disk(world.row, [world.row, other])
            world.reader["ClientIPAddress"] = "other-client"
            with self.assertRaisesRegex(handoff.SourceHandoffError, "already attached"):
                handoff._reader_for_disk(world.row, [world.row])

    def test_unmatched_usb_mapping_rejected(self):
        with (
            patch.object(handoff, "_usb_devices", return_value=[]),
            patch.object(
                handoff,
                "_pnp_ancestors",
                return_value={"usb\\reader"},
            ),
        ):
            with self.assertRaisesRegex(handoff.SourceHandoffError, "uniquely"):
                handoff._reader_for_disk(IDENTITY, [])


class HashTests(unittest.TestCase):
    def test_hash_range_offsets_digest_progress_and_cancellation(self):
        progress = []
        with patch.object(handoff, "_run_wsl_script", return_value=MBR + "  -\n") as run:
            result = handoff.hash_source_ranges(
                "/dev/mock", [(512, 1024)], should_cancel=lambda: False, on_progress=progress.append
            )
            self.assertEqual(result, [{"offset": 512, "length": 1024, "sha256": MBR}])
            self.assertEqual(run.call_args.kwargs["args"], ["/dev/mock", "512", "1024"])
            self.assertEqual(progress, [100])
            with self.assertRaises(handoff.Ext4CompactCaptureCancelled):
                handoff.hash_source_ranges(
                    "/dev/mock", [(0, 512)], should_cancel=lambda: True, on_progress=progress.append
                )
            self.assertEqual(run.call_count, 1)

    def test_invalid_or_failed_hash_is_rejected(self):
        for output in ("", "not-a-sha256"):
            with patch.object(handoff, "_run_wsl_script", return_value=output):
                with self.assertRaisesRegex(handoff.SourceHandoffError, "digest"):
                    handoff.hash_source_ranges(
                        "/dev/mock",
                        [(0, 512)],
                        should_cancel=lambda: False,
                        on_progress=lambda _: None,
                    )


@contextmanager
def archive_world(*, cleanup_failure=False, capture_failure=False):
    data = b"B" * 512 + b"P" * 1024 + b"\0" * 2560
    events = []
    partition = SimpleNamespace(
        number=1, kind="primary", type_code=0x0C, start_lba=1, sector_count=2, table_lba=0
    )
    layout = SimpleNamespace(partitions=[partition], disk_signature="0000007b")
    actual = SimpleNamespace(
        path="/dev/mock1",
        mounted=False,
        start_lba=1,
        sector_count=2,
        filesystem="vfat",
        filesystem_version="FAT32",
        uuid="1234-ABCD",
    )

    class Disk(io.BytesIO):
        size = len(data)
        sector_size = 512

        def device_info(self):
            return {"serial": "test", "model": "mock"}

    class Session:
        def __init__(self, *_args, **_kwargs):
            self.disk_number, self.physical_path = 7, "mock-source"
            self.identity = {**IDENTITY, "mbr_sha256": hashlib.sha256(data[:512]).hexdigest()}
            self.closed = False

        def check_layout(self, _partitions):
            pass

        def attach(self, _mbr):
            events.append("attach")
            return "/dev/mock"

        def verify(self):
            events.append("verify")

        def close(self):
            if self.closed:
                return
            self.closed = True
            events.append("close")
            if cleanup_failure:
                raise handoff.SourceHandoffError("cleanup failed")

    def run(args, **_kwargs):
        if args[0].startswith("partclone.") and "-c" in args:
            if capture_failure:
                raise handoff.Ext4CompactCaptureError("capture failed")
            Path(args[args.index("-o") + 1]).write_bytes(b"partclone-member")
        if args[0].startswith("partclone.") and "-D" in args:
            Path(args[args.index("-o") + 1]).write_text("0x200 0x400 +\n", encoding="utf-8")
        return ""

    def hashes(_device, ranges, **_kwargs):
        events.append("hash")
        return [
            {"offset": o, "length": n, "sha256": hashlib.sha256(data[o : o + n]).hexdigest()}
            for o, n in ranges
        ]

    original_publish = archive._archive_members

    def publish(*args):
        events.append("publish")
        return original_publish(*args)

    with ExitStack() as stack:
        for name, value in {
            "check_prerequisites": Mock(return_value="test-partclone"),
            "SourceSession": Session,
            "parse_mbr_layout": Mock(return_value=layout),
            "_wsl_partitions": Mock(return_value={1: actual}),
            "_verify_signature_metadata": Mock(return_value={}),
            "_wsl_path": str,
            "_run_wsl": run,
            "_run_wsl_script": Mock(return_value=""),
            "hash_source_ranges": hashes,
            "_archive_members": publish,
        }.items():
            stack.enter_context(patch.object(archive, name, value))
        opened = stack.enter_context(
            patch.object(archive.pyimager, "Win32Disk", side_effect=lambda _: Disk(data))
        )
        yield events, opened


class CaptureIntegrationTests(unittest.TestCase):
    def capture(self, output):
        return archive.capture_used_block_archive(
            2,
            output,
            expected_size=4096,
            expected_serial="test",
            expected_source=IDENTITY,
            should_cancel=lambda: False,
            on_progress=lambda _: None,
            on_log=lambda _: None,
        )

    def test_archive_hashes_before_returning_source_and_publishing(self):
        with tempfile.TemporaryDirectory() as folder, archive_world() as (events, opened):
            output = Path(folder) / "test.odin-archive"
            meta, manifest = self.capture(output)
            self.assertTrue(output.exists())
            self.assertFalse(meta["cancelled"])
            self.assertEqual(manifest["source"]["disk_number"], 7)
            self.assertEqual(opened.call_count, 1)  # Never reopen a stale PhysicalDrive number.
            self.assertLess(events.index("hash"), events.index("close"))
            self.assertLess(events.index("close"), events.index("publish"))

    def test_discovery_returns_identity_after_source_cleanup(self):
        with (
            archive_world() as (events, opened),
            patch.object(
                archive,
                "_run_wsl",
                side_effect=lambda args: "0x200 0x400 +\n" if args[0] == "cat" else "",
            ),
        ):
            discovered = archive.discover_used_block_source(
                2,
                expected_size=4096,
                expected_serial="test",
            )
        self.assertEqual(discovered["disk_number"], 7)
        self.assertEqual(discovered["source_identity"]["pnp"], IDENTITY["pnp"])
        self.assertEqual(events[-1], "close")
        self.assertEqual(opened.call_count, 1)

    def test_cleanup_failure_prevents_publish(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            archive_world(cleanup_failure=True) as (events, _),
        ):
            output = Path(folder) / "test.odin-archive"
            with self.assertRaisesRegex(archive.UsedBlockArchiveError, "cleanup failed"):
                self.capture(output)
            self.assertNotIn("publish", events)
            self.assertFalse(output.exists())

    def test_capture_failure_returns_source(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            archive_world(capture_failure=True) as (events, _),
        ):
            with self.assertRaisesRegex(archive.UsedBlockArchiveError, "capture failed"):
                self.capture(Path(folder) / "test.odin-archive")
            self.assertIn("close", events)
            self.assertNotIn("publish", events)

    def test_worker_passes_identity_and_preserves_failure_text(self):
        worker = PyImagerWorker(
            Mock(),
            2,
            "D:/test.odin-archive",
            Mock(),
            Mock(),
            Mock(),
            used_block_archive=True,
            expected_source=IDENTITY,
        )
        with patch(
            "pyimager_worker.capture_used_block_archive",
            side_effect=archive.UsedBlockArchiveError("cleanup failed"),
        ) as capture:
            worker._run()
        self.assertEqual(capture.call_args.kwargs["expected_source"], IDENTITY)
        self.assertIn("cleanup failed", worker.error)


if __name__ == "__main__":
    unittest.main()
