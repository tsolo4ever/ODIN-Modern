"""Focused checks for the bounded, read-only Quick Disk Health backend."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) in sys.path:
    sys.path.remove(str(ROOT))
sys.path.insert(0, str(ROOT))

import disk_health  # noqa: E402


def _drive(serial: str = "SERIAL-3", size: int = 8 << 30):
    return SimpleNamespace(
        disk_number=3,
        raw_device_path=r"\\.\PhysicalDrive3",
        size_bytes=size,
        hw_serial=serial,
        model="TEST SSD",
        label="",
        all_letters=["E:"],
        is_system=False,
        size_str="8.0 GB",
    )


def _real_drive(serial: str = "", size: int = 8 << 30) -> disk_health.DriveInfo:
    return disk_health.DriveInfo(
        disk_number=3,
        first_letter="E:",
        all_letters=["E:"],
        label="",
        size_bytes=size,
        hw_serial=serial,
        model="",
        removable=False,
        is_system=False,
    )


class FakeReader:
    def __init__(self, _path: str, *, fail_offsets=(), max_read_bytes=None, short_offsets=()):
        self.offset = 0
        self.fail_offsets = set(fail_offsets)
        self.max_read_bytes = max_read_bytes
        self.short_offsets = set(short_offsets)
        self.closed = False
        self.calls = []

    def seek(self, offset: int) -> int:
        self.offset = offset
        return offset

    def read(self, length: int) -> bytes:
        self.calls.append((self.offset, length))
        if self.max_read_bytes is not None and length > self.max_read_bytes:
            raise OSError("simulated request-size failure")
        if self.offset in self.fail_offsets:
            raise OSError("simulated read failure")
        if self.offset in self.short_offsets:
            return bytes(max(0, length - 1))
        return bytes(length)

    def close(self) -> None:
        self.closed = True


def test_sample_offsets_are_aligned_and_cover_start_and_tail():
    disk_size = (8 << 30) + 512
    offsets = disk_health.build_sample_offsets(disk_size, 64 << 10, 256)
    assert offsets[0] == 0
    assert offsets[-1] == disk_size - (64 << 10)
    assert offsets[-1] + (64 << 10) == disk_size
    assert len(offsets) == len(set(offsets))
    assert all(offset % 512 == 0 for offset in offsets)
    assert all(0 <= offset <= disk_size - (64 << 10) for offset in offsets)


def test_4kn_sample_offsets_are_aligned_and_cover_final_sector():
    disk_size = (8 << 30) + 4096
    offsets = disk_health.build_sample_offsets(disk_size, 64 << 10, 256, alignment=4096)
    assert offsets[0] == 0
    assert offsets[-1] + (64 << 10) == disk_size
    assert all(offset % 4096 == 0 for offset in offsets)


def test_successful_sample_is_bounded():
    readers = []

    def factory(path):
        reader = FakeReader(path)
        readers.append(reader)
        return reader

    result = disk_health.sample_disk_regions(
        r"\\.\PhysicalDrive3",
        8 << 30,
        sample_count=16,
        reader_factory=factory,
    )
    assert result.attempted_regions == 16
    assert result.successful_regions == 16
    assert result.bytes_read == 16 * (64 << 10)
    assert not result.failures
    assert readers[0].closed


def test_large_request_fallback_verifies_the_whole_region():
    readers = []

    def factory(path):
        reader = FakeReader(path, max_read_bytes=4096)
        readers.append(reader)
        return reader

    result = disk_health.sample_disk_regions(
        r"\\.\PhysicalDrive3",
        8 << 30,
        sample_count=1,
        reader_factory=factory,
    )
    assert result.successful_regions == 1
    assert result.small_read_fallbacks == 1
    assert result.bytes_read == 64 << 10
    assert not result.failures
    assert readers[0].calls[0] == (0, 64 << 10)
    assert readers[0].calls[1:] == [(offset, 4096) for offset in range(0, 64 << 10, 4096)]


def test_large_request_fallback_reports_a_later_bad_subregion():
    def factory(path):
        return FakeReader(path, fail_offsets={4096}, max_read_bytes=4096)

    result = disk_health.sample_disk_regions(
        r"\\.\PhysicalDrive3",
        8 << 30,
        sample_count=1,
        reader_factory=factory,
    )
    assert result.successful_regions == 0
    assert result.bytes_read == 4096
    assert len(result.failures) == 1
    assert result.failures[0].offset == 4096
    assert result.failures[0].length == 4096


def test_failed_region_is_reported_and_failure_cap_stops_scan():
    offsets = disk_health.build_sample_offsets(8 << 30, 64 << 10, 16)

    def factory(path):
        return FakeReader(path, fail_offsets=offsets)

    result = disk_health.sample_disk_regions(
        r"\\.\PhysicalDrive3",
        8 << 30,
        sample_count=16,
        failure_limit=3,
        reader_factory=factory,
    )
    assert result.attempted_regions == 3
    assert len(result.failures) == 3
    assert [failure.offset for failure in result.failures] == offsets[:3]
    assert result.stopped_after_failure_limit
    report = disk_health.format_report(
        disk_health.QuickDiskCheckResult(
            3,
            _drive(),
            _drive(),
            disk_health.SmartctlSnapshot(False, "smartctl unavailable"),
            disk_health.StoragePrediction("unavailable", "prediction unavailable"),
            result,
            0.25,
        )
    )
    assert "attempted 3/16" in report
    assert "13 requested regions were not attempted" in report


def test_short_read_is_not_accepted():
    def factory(path):
        return FakeReader(path, short_offsets={0})

    result = disk_health.sample_disk_regions(
        r"\\.\PhysicalDrive3",
        8 << 30,
        sample_count=1,
        reader_factory=factory,
    )
    assert result.successful_regions == 0
    assert len(result.failures) == 1
    assert "short read" in result.failures[0].error


def test_cancellation_happens_between_regions():
    checks = 0

    def cancel():
        nonlocal checks
        checks += 1
        return checks > 2

    result = disk_health.sample_disk_regions(
        r"\\.\PhysicalDrive3",
        8 << 30,
        sample_count=16,
        reader_factory=FakeReader,
        should_cancel=cancel,
    )
    assert result.cancelled
    assert result.attempted_regions == 2


def test_smartctl_nonzero_health_bits_still_parse_json():
    payload = {
        "smartctl": {"exit_status": 72},
        "smart_status": {"passed": False},
        "model_name": "TEST SSD",
    }

    def runner(command, **_kwargs):
        return subprocess.CompletedProcess(command, 72, json.dumps(payload), "")

    result = disk_health.run_smartctl("smartctl.exe", 3, runner=runner)
    assert result.available
    assert result.exit_status == 72
    assert result.health_bits == 72
    assert result.data == payload
    assert result.command[-1] == "/dev/pd3"
    assert result.command[-2] == "--device=auto"


def test_unknown_usb_bridge_gets_one_sat_retry():
    calls = []

    def runner(command, **_kwargs):
        calls.append(command)
        if len(calls) == 1:
            return subprocess.CompletedProcess(command, 2, "", "Unknown USB bridge")
        payload = {"smartctl": {"exit_status": 0}, "smart_status": {"passed": True}}
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    result = disk_health.run_smartctl("smartctl.exe", 3, runner=runner)
    assert result.used_sat_retry
    assert len(calls) == 2
    assert calls[0][-2] == "--device=auto"
    assert calls[1][-2] == "--device=sat"


def test_windows_identity_query_targets_one_exact_disk():
    calls = []
    payload = {
        "Index": 3,
        "DeviceID": r"\\.\PHYSICALDRIVE3",
        "SerialNumber": "RANDOM__TEST3",
        "Size": 8 << 30,
        "Model": "TEST USB DISK",
    }

    def runner(command, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    result = disk_health.query_windows_disk_identity(3, runner=runner)
    assert result is not None
    assert result.serial == "RANDOM__TEST3"
    assert result.size_bytes == 8 << 30
    assert len(calls) == 1
    assert "Index = 3" in calls[0][-1]
    assert "Win32_DiskDrive" in calls[0][-1]


def test_hidden_descriptor_identity_uses_size_matched_cim_fallback():
    drive = _real_drive()
    windows_identity = disk_health.WindowsDiskIdentity(
        3,
        r"\\.\PHYSICALDRIVE3",
        drive.size_bytes,
        "RANDOM__TEST3",
        "TEST USB DISK",
    )
    result = disk_health.get_stable_physical_drive(
        3,
        base_provider=lambda _number: drive,
        windows_identity_provider=lambda _number: windows_identity,
    )
    assert result is not None
    assert result.hw_serial == "RANDOM__TEST3"
    assert result.model == "TEST USB DISK"


def test_cim_identity_with_different_size_is_not_accepted():
    drive = _real_drive()
    windows_identity = disk_health.WindowsDiskIdentity(
        3,
        r"\\.\PHYSICALDRIVE3",
        drive.size_bytes + 512,
        "RANDOM__WRONG",
        "TEST USB DISK",
    )
    result = disk_health.get_stable_physical_drive(
        3,
        base_provider=lambda _number: drive,
        windows_identity_provider=lambda _number: windows_identity,
    )
    assert result is drive
    assert not result.hw_serial


def test_cim_geometry_round_down_matches_live_bridge_sizes():
    drive = _real_drive()
    drive.size_bytes = 7_918_460_928
    windows_identity = disk_health.WindowsDiskIdentity(
        3,
        r"\\.\PHYSICALDRIVE3",
        7_912_719_360,
        "RANDOM__TEST3",
        "BIWIN SS D SCSI Disk Device",
        total_cylinders=962,
        tracks_per_cylinder=255,
        sectors_per_track=63,
        bytes_per_sector=512,
    )
    result = disk_health.get_stable_physical_drive(
        3,
        base_provider=lambda _number: drive,
        windows_identity_provider=lambda _number: windows_identity,
    )
    assert result is not None
    assert result.hw_serial == "RANDOM__TEST3"
    assert result.model == "BIWIN SS D SCSI Disk Device"


def test_cim_size_mismatch_without_proving_geometry_is_not_accepted():
    drive = _real_drive()
    windows_identity = disk_health.WindowsDiskIdentity(
        3,
        r"\\.\PHYSICALDRIVE3",
        drive.size_bytes - 512,
        "RANDOM__WRONG",
        "TEST USB DISK",
    )
    result = disk_health.get_stable_physical_drive(
        3,
        base_provider=lambda _number: drive,
        windows_identity_provider=lambda _number: windows_identity,
    )
    assert result is drive
    assert not result.hw_serial


def test_cim_identity_for_another_path_is_not_accepted():
    drive = _real_drive()
    windows_identity = disk_health.WindowsDiskIdentity(
        3,
        r"\\.\PHYSICALDRIVE4",
        drive.size_bytes,
        "RANDOM__WRONG",
        "TEST USB DISK",
    )
    result = disk_health.get_stable_physical_drive(
        3,
        base_provider=lambda _number: drive,
        windows_identity_provider=lambda _number: windows_identity,
    )
    assert result is drive
    assert not result.hw_serial


def test_identity_change_makes_result_incomplete():
    identities = iter((_drive("FIRST"), _drive("SECOND")))

    def identity(_disk_number):
        return next(identities)

    result = disk_health.quick_disk_check(
        3,
        sample_count=1,
        use_smartctl=False,
        identity_provider=identity,
        sector_size_provider=lambda _path: (512, "512-byte sectors"),
        predictor=lambda _path: disk_health.StoragePrediction(
            "unavailable", "prediction unavailable"
        ),
        reader_factory=FakeReader,
    )
    assert result.identity_changed
    assert result.exit_code == 2
    assert "identity changed" in result.conclusion


def test_missing_initial_serial_refuses_scan_before_any_io():
    def should_not_run(*_args, **_kwargs):
        raise AssertionError("scanner performed I/O without a stable serial")

    result = disk_health.quick_disk_check(
        3,
        sample_count=1,
        use_smartctl=False,
        identity_provider=lambda _disk_number: _drive(""),
        sector_size_provider=should_not_run,
        predictor=should_not_run,
        reader_factory=should_not_run,
    )
    assert result.exit_code == 2
    assert "identity is not exposed" in result.samples.open_error
    assert result.samples.attempted_regions == 0
    assert result.samples.logical_sector_bytes == 0


def test_missing_sector_size_refuses_scan_before_metadata_or_raw_io():
    def should_not_run(*_args, **_kwargs):
        raise AssertionError("scanner performed I/O without a logical sector size")

    result = disk_health.quick_disk_check(
        3,
        sample_count=1,
        use_smartctl=False,
        identity_provider=lambda _disk_number: _drive(),
        sector_size_provider=lambda _path: (None, "simulated geometry failure"),
        predictor=should_not_run,
        reader_factory=should_not_run,
    )
    assert result.exit_code == 2
    assert "cannot safely align sampled reads" in result.samples.open_error
    assert result.samples.attempted_regions == 0


def test_serial_disappearing_after_scan_is_identity_change():
    identities = iter((_drive("FIRST"), _drive("")))

    result = disk_health.quick_disk_check(
        3,
        sample_count=1,
        use_smartctl=False,
        identity_provider=lambda _disk_number: next(identities),
        sector_size_provider=lambda _path: (512, "512-byte sectors"),
        predictor=lambda _path: disk_health.StoragePrediction(
            "unavailable", "prediction unavailable"
        ),
        reader_factory=FakeReader,
    )
    assert result.identity_changed
    assert result.exit_code == 2


def test_friendly_model_change_is_identity_change():
    before = _drive("SAME")
    after = _drive("SAME")
    after.model = "OTHER MODEL"
    assert disk_health._identity_changed(before, after)


def test_report_never_certifies_the_disk_healthy():
    drive = _drive()
    result = disk_health.QuickDiskCheckResult(
        3,
        drive,
        drive,
        disk_health.SmartctlSnapshot(False, "smartctl unavailable"),
        disk_health.StoragePrediction("unavailable", "prediction unavailable"),
        disk_health.SampleSummary(1, 1, 1, 64 << 10),
        0.25,
    )
    report = disk_health.format_report(result)
    assert "No errors were found in the sampled regions" in report
    assert "disk is healthy" not in report.casefold()
    assert "READ ONLY" in report


def test_report_uses_actual_logical_sector_size_for_lba():
    drive = _drive()
    samples = disk_health.SampleSummary(
        requested_regions=1,
        attempted_regions=1,
        failures=[disk_health.SampleFailure(4096, 4096, "simulated")],
        logical_sector_bytes=4096,
    )
    result = disk_health.QuickDiskCheckResult(
        3,
        drive,
        drive,
        disk_health.SmartctlSnapshot(False, "smartctl unavailable"),
        disk_health.StoragePrediction("unavailable", "prediction unavailable"),
        samples,
        0.25,
    )
    report = disk_health.format_report(result)
    assert "Logical sector size: 4096 bytes" in report
    assert "offset 4096 (LBA 1)" in report


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
