"""Focused checks for guarded fixed-disk protection and revalidation."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import guarded_flash_safety as safety  # noqa: E402


def _disk(number: int = 3, unique_id: str = "UID-3", **changes):
    disk = safety.DiskIdentity(
        disk_number=number,
        unique_id=unique_id,
        serial=f"SERIAL-{number}",
        model="TEST SSD",
        manufacturer="TEST",
        size_bytes=8 << 30,
        bus_type=11,
        bus_name="SATA",
        device_path=rf"\\?\scsi#disk{number}",
        location=f"Port {number}",
        mounted_volumes=(f"{chr(68 + number)}:\\",),
    )
    return replace(disk, **changes)


def _store(folder: str) -> safety.ProtectedHardwareStore:
    return safety.ProtectedHardwareStore(Path(folder) / "protected.json")


def test_stable_key_prefers_unique_id_and_rejects_placeholder_ids():
    assert _disk(unique_id="UID-3").stable_key == "unique:uid-3"
    fallback = _disk(unique_id="00000000", serial="REAL SERIAL")
    assert fallback.stable_key.startswith("serial:real serial|")
    assert _disk(unique_id="unknown", serial="unknown").stable_key is None


def test_baseline_scan_is_additive_and_persistent():
    with tempfile.TemporaryDirectory() as folder:
        store = _store(folder)
        moments = iter(
            (
                datetime(2026, 8, 16, 1, tzinfo=UTC),
                datetime(2026, 8, 16, 2, tzinfo=UTC),
            )
        )
        first = safety.scan_system_hardware(
            store, inventory_provider=lambda: [_disk(0, "SYSTEM")], now_provider=lambda: next(moments)
        )
        second = safety.scan_system_hardware(
            store,
            inventory_provider=lambda: [_disk(0, "SYSTEM"), _disk(2, "DATA")],
            now_provider=lambda: next(moments),
        )
        records = store.load()
        assert len(first.added_record_ids) == 1
        assert len(second.added_record_ids) == 1
        assert len(records) == 2
        system_record = next(record for record in records if record.stable_key == "unique:system")
        assert system_record.first_seen_utc != system_record.last_seen_utc


def test_baseline_preserves_unidentified_hardware():
    with tempfile.TemporaryDirectory() as folder:
        store = _store(folder)
        result = safety.scan_system_hardware(
            store,
            inventory_provider=lambda: [_disk(unique_id="", serial="", model="Mystery")],
        )
        assert len(result.protected_records) == 1
        assert result.protected_records[0].stable_key is None
        assert result.protected_records[0].record_id.startswith("descriptor:")


def test_corrupt_baseline_fails_closed():
    with tempfile.TemporaryDirectory() as folder:
        store = _store(folder)
        store.path.write_text("not json", encoding="utf-8")
        try:
            safety.list_guarded_candidates(store, inventory_provider=lambda: [_disk()])
        except safety.ProtectedStoreError:
            pass
        else:
            raise AssertionError("corrupt protection store was accepted")


def test_live_windows_storage_is_rejected_without_a_baseline():
    disk = _disk(system_reasons=("page-file volume",))
    decision = safety.evaluate_inventory([disk], [])[0]
    assert not decision.eligible
    assert "page-file" in " ".join(decision.reasons)


def test_protected_disk_is_rejected_by_stable_or_descriptor_match():
    disk = _disk()
    record = safety.ProtectedRecord.from_disk(disk, "2026-08-16T00:00:00+00:00")
    assert not safety.evaluate_inventory([disk], [record])[0].eligible
    newly_exposed_id = replace(disk, unique_id="NEWLY-EXPOSED", serial="NEW")
    descriptor_record = replace(record, stable_key=None)
    assert not safety.evaluate_inventory([newly_exposed_id], [descriptor_record])[0].eligible


def test_missing_and_duplicate_stable_identity_are_rejected():
    missing = _disk(unique_id="", serial="")
    assert not safety.evaluate_inventory([missing], [])[0].eligible
    duplicate = [_disk(2, "SAME"), _disk(3, "SAME")]
    assert all(not item.eligible for item in safety.evaluate_inventory(duplicate, []))


def test_removable_virtual_and_image_host_disks_are_rejected():
    removable = _disk(removable=True)
    virtual = _disk(bus_type=15, bus_name="File-backed virtual")
    image_host = _disk()
    assert not safety.evaluate_inventory([removable], [])[0].eligible
    assert not safety.evaluate_inventory([virtual], [])[0].eligible
    assert not safety.evaluate_inventory([image_host], [], image_disk_number=3)[0].eligible


def test_normal_fixed_disk_is_eligible():
    decision = safety.evaluate_inventory([_disk()], [])[0]
    assert decision.eligible
    assert not decision.reasons


def test_revalidation_rejects_disk_number_reuse_and_volume_change():
    with tempfile.TemporaryDirectory() as folder:
        store = _store(folder)
        expected = _disk()
        reused = _disk(unique_id="OTHER")
        changed_volume = replace(expected, mounted_volumes=("Z:\\",))
        for current in (reused, changed_volume):
            decision = safety.revalidate_target(
                expected,
                store,
                image_path=r"D:\image.img",
                inventory_provider=lambda current=current: [current],
                image_disk_provider=lambda _path: 1,
            )
            assert not decision.eligible
            assert "changed" in decision.reasons[0]


def test_revalidation_accepts_unchanged_eligible_target():
    with tempfile.TemporaryDirectory() as folder:
        disk = _disk()
        decision = safety.revalidate_target(
            disk,
            _store(folder),
            image_path=r"D:\image.img",
            inventory_provider=lambda: [disk],
            image_disk_provider=lambda _path: 1,
        )
        assert decision.eligible


def test_current_windows_disk_record_cannot_be_removed():
    with tempfile.TemporaryDirectory() as folder:
        store = _store(folder)
        disk = _disk()
        record = safety.ProtectedRecord.from_disk(disk, "2026-08-16T00:00:00+00:00")
        store.save([record])
        current = replace(disk, system_reasons=("Windows system disk",))
        try:
            safety.remove_protected_record(store, record.record_id, inventory_provider=lambda: [current])
        except safety.ProtectedStoreError:
            pass
        else:
            raise AssertionError("current Windows disk protection was removed")
        assert len(store.load()) == 1


def test_windows_inventory_parser_populates_protection_and_bus_data():
    payload = [
        {
            "Number": 0,
            "UniqueId": "SYSTEM-0",
            "SerialNumber": "SERIAL-0",
            "Model": "NVME",
            "Manufacturer": "TEST",
            "Size": 1000,
            "BusType": 17,
            "Path": r"\\?\scsi#disk0",
            "Location": "Slot 0",
            "Volumes": ["C:\\"],
            "SystemReasons": ["active Windows volume", "page-file volume"],
        }
    ]

    def runner(command, **_kwargs):
        assert "MSFT_Disk" in command[-1]
        assert "MSFT_Partition" in command[-1]
        assert "Win32_PageFileUsage" in command[-1]
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    disks = safety.query_windows_storage_inventory(
        runner=runner, removable_provider=lambda _number: False, powershell="powershell.exe"
    )
    assert disks[0].bus_name == "NVMe"
    assert disks[0].mounted_volumes == ("C:\\",)
    assert "page-file volume" in disks[0].system_reasons


def test_failed_windows_inventory_does_not_return_candidates():
    def runner(command, **_kwargs):
        return subprocess.CompletedProcess(command, 1, "", "simulated CIM failure")

    try:
        safety.query_windows_storage_inventory(
            runner=runner, removable_provider=lambda _number: False, powershell="powershell.exe"
        )
    except safety.StorageInventoryError as exc:
        assert "simulated CIM failure" in str(exc)
    else:
        raise AssertionError("failed Windows classification was accepted")


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
    print(f"\n{len(tests) - failures}/{len(tests)} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_run_direct())
