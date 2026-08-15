"""Read-only, bounded health checks for one explicitly selected physical disk."""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import struct
import subprocess
import sys
import time
from collections.abc import Callable, Iterable
from ctypes import wintypes
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from drive_manager import DriveInfo, get_physical_drive
from raw_disk import Win32RawDiskReader


DEFAULT_SAMPLE_COUNT = 256
DEFAULT_SAMPLE_BYTES = 64 * 1024
FALLBACK_READ_BYTES = 4096
DEFAULT_SECTOR_BYTES = 512
DEFAULT_FAILURE_LIMIT = 8
IOCTL_STORAGE_PREDICT_FAILURE = 0x002D1100
IOCTL_DISK_GET_DRIVE_GEOMETRY = 0x00070000
GENERIC_READ = 0x80000000
FILE_SHARE_READ_WRITE = 0x00000001 | 0x00000002
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
CREATE_NO_WINDOW = 0x08000000
MAX_CIM_CYLINDER_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class StoragePrediction:
    state: str
    message: str


@dataclass(frozen=True)
class WindowsDiskIdentity:
    disk_number: int
    device_id: str
    size_bytes: int
    serial: str
    model: str
    total_cylinders: int = 0
    tracks_per_cylinder: int = 0
    sectors_per_track: int = 0
    bytes_per_sector: int = 0


@dataclass(frozen=True)
class SmartctlSnapshot:
    available: bool
    message: str
    exit_status: int | None = None
    command: tuple[str, ...] = ()
    data: dict[str, Any] | None = None
    raw_output: str = ""
    used_sat_retry: bool = False

    @property
    def health_bits(self) -> int:
        return (self.exit_status or 0) & 0xF8


@dataclass(frozen=True)
class SampleFailure:
    offset: int
    length: int
    error: str


@dataclass
class SampleSummary:
    requested_regions: int
    attempted_regions: int = 0
    successful_regions: int = 0
    bytes_read: int = 0
    small_read_fallbacks: int = 0
    failures: list[SampleFailure] = field(default_factory=list)
    cancelled: bool = False
    open_error: str = ""
    logical_sector_bytes: int = DEFAULT_SECTOR_BYTES
    stopped_after_failure_limit: bool = False


@dataclass
class QuickDiskCheckResult:
    disk_number: int
    before: DriveInfo | None
    after: DriveInfo | None
    smartctl: SmartctlSnapshot
    prediction: StoragePrediction
    samples: SampleSummary
    elapsed_seconds: float
    identity_changed: bool = False

    @property
    def exit_code(self) -> int:
        if self.samples.cancelled:
            return 130
        if self.before is None or self.samples.open_error or self.identity_changed:
            return 2
        if (
            self.prediction.state == "failure_predicted"
            or self.smartctl.health_bits
            or self.samples.failures
        ):
            return 1
        return 0

    @property
    def conclusion(self) -> str:
        if self.samples.cancelled:
            return "INCOMPLETE: cancelled between sampled reads."
        if self.before is None:
            return f"INCOMPLETE: Disk {self.disk_number} was not readable."
        if self.identity_changed:
            return "INCOMPLETE: selected disk identity changed during the check."
        if self.samples.open_error:
            return f"INCOMPLETE: {self.samples.open_error}"
        if self.prediction.state == "failure_predicted":
            return "PROBLEMS FOUND: Windows reports predicted storage failure."
        if self.smartctl.health_bits:
            return "PROBLEMS FOUND: smartctl reported health or error-history bits."
        if self.samples.failures:
            return "PROBLEMS FOUND: one or more sampled regions could not be read."
        return "No errors were found in the sampled regions. This is not a full surface test."


def _unique_paths(paths: Iterable[Path]) -> list[Path]:
    seen: set[str] = set()
    result = []
    for path in paths:
        key = os.path.normcase(os.path.abspath(path))
        if key not in seen:
            seen.add(key)
            result.append(path)
    return result


def find_smartctl(extra_candidates: Iterable[str | os.PathLike[str]] = ()) -> str | None:
    """Return an existing smartctl.exe without installing or modifying anything."""
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    chocolatey = os.environ.get("ChocolateyInstall", r"C:\ProgramData\chocolatey")
    user_profile = os.environ.get("USERPROFILE", "")
    candidates = [Path(item) for item in extra_candidates]
    candidates.extend(
        [
            Path(sys.executable).resolve().parent / "smartctl.exe",
            Path(__file__).resolve().parent / "smartctl.exe",
            Path(program_files) / "smartmontools" / "bin" / "smartctl.exe",
            Path(program_files_x86) / "smartmontools" / "bin" / "smartctl.exe",
            Path(chocolatey) / "bin" / "smartctl.exe",
        ]
    )
    if user_profile:
        candidates.append(Path(user_profile) / "scoop" / "shims" / "smartctl.exe")
    on_path = shutil.which("smartctl.exe") or shutil.which("smartctl")
    if on_path:
        candidates.insert(2, Path(on_path))
    for candidate in _unique_paths(candidates):
        if candidate.is_file():
            return str(candidate)
    return None


def query_windows_disk_identity(
    disk_number: int,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    timeout_seconds: float = 10.0,
) -> WindowsDiskIdentity | None:
    """Query one exact Win32_DiskDrive identity without opening other disks."""
    if os.name != "nt" or disk_number < 0:
        return None
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    windows_powershell = (
        Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    )
    powershell = str(windows_powershell) if windows_powershell.is_file() else None
    powershell = powershell or shutil.which("pwsh.exe") or shutil.which("pwsh")
    if not powershell:
        return None
    script = (
        "$ErrorActionPreference='Stop';"
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
        "$d=Get-CimInstance -ClassName Win32_DiskDrive "
        f"-Filter 'Index = {disk_number}';"
        "if($null -eq $d){exit 3};"
        "$d | Select-Object Index,DeviceID,SerialNumber,Size,Model,"
        "TotalCylinders,TracksPerCylinder,SectorsPerTrack,BytesPerSector "
        "| ConvertTo-Json -Compress"
    )
    try:
        completed = runner(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            creationflags=CREATE_NO_WINDOW,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0 or not (completed.stdout or "").strip():
        return None
    try:
        data = json.loads(completed.stdout)
        found_number = int(data.get("Index", -1))
        size_bytes = int(data.get("Size", 0))
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
        return None
    device_id = str(data.get("DeviceID") or "").strip()
    expected_device_id = f"\\\\.\\PHYSICALDRIVE{disk_number}"
    if (
        found_number != disk_number
        or size_bytes <= 0
        or device_id.casefold() != expected_device_id.casefold()
    ):
        return None

    def optional_int(name: str) -> int:
        try:
            return int(data.get(name, 0) or 0)
        except (TypeError, ValueError):
            return 0

    return WindowsDiskIdentity(
        disk_number=found_number,
        device_id=device_id,
        size_bytes=size_bytes,
        serial=str(data.get("SerialNumber") or "").strip(),
        model=str(data.get("Model") or "").strip(),
        total_cylinders=optional_int("TotalCylinders"),
        tracks_per_cylinder=optional_int("TracksPerCylinder"),
        sectors_per_track=optional_int("SectorsPerTrack"),
        bytes_per_sector=optional_int("BytesPerSector"),
    )


def _windows_identity_matches_drive(
    disk_number: int,
    drive: DriveInfo,
    windows_identity: WindowsDiskIdentity,
) -> bool:
    expected_device_id = rf"\\.\PHYSICALDRIVE{disk_number}"
    if (
        windows_identity.disk_number != disk_number
        or windows_identity.device_id.casefold() != expected_device_id.casefold()
    ):
        return False
    if windows_identity.size_bytes == drive.size_bytes:
        return True
    if windows_identity.size_bytes <= 0 or windows_identity.size_bytes >= drive.size_bytes:
        return False

    bytes_per_sector = windows_identity.bytes_per_sector
    if (
        bytes_per_sector < 512
        or bytes_per_sector > 65536
        or bytes_per_sector & (bytes_per_sector - 1)
    ):
        return False
    geometry_values = (
        windows_identity.total_cylinders,
        windows_identity.tracks_per_cylinder,
        windows_identity.sectors_per_track,
    )
    if any(value <= 0 for value in geometry_values):
        return False
    cylinder_bytes = (
        windows_identity.tracks_per_cylinder * windows_identity.sectors_per_track * bytes_per_sector
    )
    if cylinder_bytes <= 0 or cylinder_bytes > MAX_CIM_CYLINDER_BYTES:
        return False
    return (
        windows_identity.size_bytes == windows_identity.total_cylinders * cylinder_bytes
        and windows_identity.size_bytes == (drive.size_bytes // cylinder_bytes) * cylinder_bytes
        and 0 < drive.size_bytes - windows_identity.size_bytes < cylinder_bytes
    )


def get_stable_physical_drive(
    disk_number: int,
    *,
    base_provider: Callable[[int], DriveInfo | None] = get_physical_drive,
    windows_identity_provider: Callable[[int], WindowsDiskIdentity | None] = (
        query_windows_disk_identity
    ),
) -> DriveInfo | None:
    """Fill descriptor identity from the exact Windows disk when a dock hides it."""
    drive = base_provider(disk_number)
    if drive is None:
        return None
    if (drive.hw_serial or "").strip() and (drive.model or "").strip():
        return drive
    windows_identity = windows_identity_provider(disk_number)
    if windows_identity is None or not _windows_identity_matches_drive(
        disk_number, drive, windows_identity
    ):
        return drive
    descriptor_serial = (drive.hw_serial or "").strip()
    descriptor_model = (drive.model or "").strip()
    return replace(
        drive,
        hw_serial=descriptor_serial or windows_identity.serial.strip(),
        model=descriptor_model or windows_identity.model.strip(),
    )


def _smartctl_command(executable: str, disk_number: int, device_type: str) -> list[str]:
    return [
        executable,
        "--json=c",
        "--all",
        f"--device={device_type}",
        f"/dev/pd{disk_number}",
    ]


def _parse_smartctl_output(
    completed: subprocess.CompletedProcess[str],
) -> tuple[dict[str, Any] | None, str]:
    raw = (completed.stdout or "").strip()
    if not raw:
        return None, (completed.stderr or "").strip()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None, "\n".join(part for part in (raw, completed.stderr.strip()) if part)
    return value if isinstance(value, dict) else None, raw


def _smartctl_message(data: dict[str, Any] | None, exit_status: int, fallback: str) -> str:
    if data:
        passed = (data.get("smart_status") or {}).get("passed")
        if passed is True:
            return "SMART overall-health status: PASSED."
        if passed is False:
            return "SMART overall-health status: FAILED."
        if exit_status & 0x07:
            return "smartctl could not fully query this device or USB bridge."
        return "smartctl returned device data without an overall-health value."
    return fallback or "smartctl returned no usable output."


def run_smartctl(
    executable: str | None,
    disk_number: int,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    timeout_seconds: float = 20.0,
) -> SmartctlSnapshot:
    if not executable:
        return SmartctlSnapshot(False, "smartctl.exe was not found; no SMART attributes were read.")

    def execute(device_type: str) -> tuple[list[str], subprocess.CompletedProcess[str]]:
        command = _smartctl_command(executable, disk_number, device_type)
        completed = runner(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
            creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        return command, completed

    try:
        command, completed = execute("auto")
        data, raw = _parse_smartctl_output(completed)
        combined = "\n".join(
            part for part in (completed.stdout, completed.stderr) if part
        ).casefold()
        used_sat_retry = False
        if completed.returncode & 0x07 and (
            "unknown usb bridge" in combined or "unsupported usb bridge" in combined
        ):
            command, completed = execute("sat")
            data, raw = _parse_smartctl_output(completed)
            used_sat_retry = True
        status = int((data or {}).get("smartctl", {}).get("exit_status", completed.returncode))
        return SmartctlSnapshot(
            True,
            _smartctl_message(data, status, raw),
            exit_status=status,
            command=tuple(command),
            data=data,
            raw_output=raw,
            used_sat_retry=used_sat_retry,
        )
    except subprocess.TimeoutExpired:
        return SmartctlSnapshot(True, f"smartctl timed out after {timeout_seconds:g} seconds.")
    except OSError as exc:
        return SmartctlSnapshot(True, f"smartctl could not be started: {exc}")


def query_storage_prediction(path: str) -> StoragePrediction:
    """Query the read-only Windows failure-prediction IOCTL as a tri-state."""
    if os.name != "nt":
        return StoragePrediction("unavailable", "Windows failure prediction is unavailable.")
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.DeviceIoControl.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    path_handle = k32.CreateFileW(
        path,
        GENERIC_READ,
        FILE_SHARE_READ_WRITE,
        None,
        OPEN_EXISTING,
        0,
        None,
    )
    if path_handle == INVALID_HANDLE_VALUE:
        error = ctypes.get_last_error()
        return StoragePrediction(
            "unavailable",
            f"Windows failure prediction unavailable: {ctypes.FormatError(error).strip()}",
        )
    try:
        output = ctypes.create_string_buffer(516)
        returned = wintypes.DWORD()
        ok = k32.DeviceIoControl(
            path_handle,
            IOCTL_STORAGE_PREDICT_FAILURE,
            None,
            0,
            output,
            len(output),
            ctypes.byref(returned),
            None,
        )
        if not ok or returned.value < 4:
            error = ctypes.get_last_error()
            return StoragePrediction(
                "unavailable",
                f"Windows failure prediction not exposed by this device/bridge "
                f"(error {error}: {ctypes.FormatError(error).strip()}).",
            )
        predicted = struct.unpack_from("<I", output.raw)[0] != 0
        if predicted:
            return StoragePrediction("failure_predicted", "Windows predicts storage failure.")
        return StoragePrediction(
            "no_failure_predicted",
            "Windows does not currently predict failure; this is not a surface test.",
        )
    finally:
        k32.CloseHandle(path_handle)


def query_logical_sector_size(path: str) -> tuple[int | None, str]:
    """Return the physical disk's logical bytes per sector without modifying it."""
    if os.name != "nt":
        return None, "Windows disk geometry is unavailable."
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.DeviceIoControl.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    path_handle = k32.CreateFileW(
        path,
        GENERIC_READ,
        FILE_SHARE_READ_WRITE,
        None,
        OPEN_EXISTING,
        0,
        None,
    )
    if path_handle == INVALID_HANDLE_VALUE:
        error = ctypes.get_last_error()
        return None, f"could not open disk geometry: {ctypes.FormatError(error).strip()}"
    try:
        geometry = ctypes.create_string_buffer(24)
        returned = wintypes.DWORD()
        ok = k32.DeviceIoControl(
            path_handle,
            IOCTL_DISK_GET_DRIVE_GEOMETRY,
            None,
            0,
            geometry,
            len(geometry),
            ctypes.byref(returned),
            None,
        )
        if not ok or returned.value < 24:
            error = ctypes.get_last_error()
            return None, (
                "logical sector size unavailable "
                f"(error {error}: {ctypes.FormatError(error).strip()})"
            )
        sector_bytes = struct.unpack_from("<I", geometry.raw, 20)[0]
        if (
            sector_bytes < DEFAULT_SECTOR_BYTES
            or sector_bytes > DEFAULT_SAMPLE_BYTES
            or sector_bytes & (sector_bytes - 1)
            or DEFAULT_SAMPLE_BYTES % sector_bytes
        ):
            return None, f"unsupported logical sector size reported: {sector_bytes} bytes"
        return sector_bytes, f"logical sector size: {sector_bytes} bytes"
    finally:
        k32.CloseHandle(path_handle)


def build_sample_offsets(
    disk_size: int,
    sample_bytes: int = DEFAULT_SAMPLE_BYTES,
    sample_count: int = DEFAULT_SAMPLE_COUNT,
    alignment: int = DEFAULT_SECTOR_BYTES,
) -> list[int]:
    if disk_size <= 0:
        raise ValueError("disk_size must be positive")
    if sample_bytes <= 0 or sample_count <= 0 or alignment <= 0:
        raise ValueError("sample size, count, and alignment must be positive")
    if disk_size % alignment:
        raise ValueError("disk_size must be aligned to the logical sector size")
    if sample_bytes % alignment:
        raise ValueError("sample_bytes must be aligned to the logical sector size")
    actual_bytes = min(sample_bytes, disk_size)
    max_offset = max(0, disk_size - actual_bytes)
    if sample_count == 1 or max_offset == 0:
        return [0]
    offsets = []
    for index in range(sample_count):
        raw = (max_offset * index) // (sample_count - 1)
        aligned = raw - (raw % alignment)
        if aligned not in offsets:
            offsets.append(aligned)
    if max_offset not in offsets:
        offsets.append(max_offset)
    return offsets


def _read_at(reader: Any, offset: int, length: int) -> bytes:
    reader.seek(offset)
    data = reader.read(length)
    if len(data) != length:
        raise OSError(f"short read: expected {length} bytes, received {len(data)}")
    return data


def sample_disk_regions(
    path: str,
    disk_size: int,
    *,
    sample_count: int = DEFAULT_SAMPLE_COUNT,
    sample_bytes: int = DEFAULT_SAMPLE_BYTES,
    logical_sector_bytes: int = DEFAULT_SECTOR_BYTES,
    failure_limit: int = DEFAULT_FAILURE_LIMIT,
    reader_factory: Callable[[str], Any] = Win32RawDiskReader,
    should_cancel: Callable[[], bool] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> SampleSummary:
    if failure_limit <= 0:
        raise ValueError("failure_limit must be positive")
    offsets = build_sample_offsets(
        disk_size, sample_bytes, sample_count, alignment=logical_sector_bytes
    )
    summary = SampleSummary(
        requested_regions=len(offsets), logical_sector_bytes=logical_sector_bytes
    )
    should_cancel = should_cancel or (lambda: False)
    try:
        reader = reader_factory(path)
    except OSError as exc:
        summary.open_error = f"could not open {path} read-only: {exc}"
        return summary
    try:
        for index, offset in enumerate(offsets, start=1):
            if should_cancel():
                summary.cancelled = True
                break
            request = min(sample_bytes, disk_size - offset)
            summary.attempted_regions += 1
            try:
                data = _read_at(reader, offset, request)
                summary.successful_regions += 1
                summary.bytes_read += len(data)
            except OSError as large_error:
                recovered_bytes = 0
                fallback_error: OSError | None = None
                failure_offset = offset
                fallback_read_bytes = max(FALLBACK_READ_BYTES, logical_sector_bytes)
                failure_length = min(fallback_read_bytes, request)
                relative = 0
                while relative < request:
                    if should_cancel():
                        summary.cancelled = True
                        break
                    fallback = min(fallback_read_bytes, request - relative)
                    fallback_offset = offset + relative
                    try:
                        data = _read_at(reader, fallback_offset, fallback)
                    except OSError as small_error:
                        fallback_error = small_error
                        failure_offset = fallback_offset
                        failure_length = fallback
                        break
                    recovered_bytes += len(data)
                    relative += fallback
                summary.bytes_read += recovered_bytes
                if summary.cancelled:
                    break
                if should_cancel():
                    summary.cancelled = True
                    break
                if fallback_error is None:
                    summary.successful_regions += 1
                    summary.small_read_fallbacks += 1
                else:
                    summary.failures.append(
                        SampleFailure(
                            failure_offset,
                            failure_length,
                            f"64 KiB request: {large_error}; "
                            f"{fallback_read_bytes}-byte subread: {fallback_error}",
                        )
                    )
                    if len(summary.failures) >= failure_limit:
                        summary.stopped_after_failure_limit = index < len(offsets)
            if on_progress:
                on_progress(index, len(offsets))
            if len(summary.failures) >= failure_limit:
                break
    finally:
        reader.close()
    return summary


def _identity_changed(before: DriveInfo, after: DriveInfo | None) -> bool:
    if after is None or before.disk_number != after.disk_number:
        return True
    if before.size_bytes != after.size_bytes:
        return True
    before_serial = (before.hw_serial or "").strip().casefold()
    after_serial = (after.hw_serial or "").strip().casefold()
    if not before_serial or not after_serial or before_serial != after_serial:
        return True
    before_model = (before.model or "").strip().casefold()
    after_model = (after.model or "").strip().casefold()
    return bool(before_model and (not after_model or before_model != after_model))


def quick_disk_check(
    disk_number: int,
    *,
    sample_count: int = DEFAULT_SAMPLE_COUNT,
    sample_bytes: int = DEFAULT_SAMPLE_BYTES,
    failure_limit: int = DEFAULT_FAILURE_LIMIT,
    smartctl_executable: str | None = None,
    use_smartctl: bool = True,
    identity_provider: Callable[[int], DriveInfo | None] = get_stable_physical_drive,
    predictor: Callable[[str], StoragePrediction] = query_storage_prediction,
    sector_size_provider: Callable[[str], tuple[int | None, str]] = (query_logical_sector_size),
    reader_factory: Callable[[str], Any] = Win32RawDiskReader,
    smartctl_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    should_cancel: Callable[[], bool] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> QuickDiskCheckResult:
    started = time.monotonic()
    before = identity_provider(disk_number)
    if before is None:
        unavailable = SmartctlSnapshot(False, "Disk was not readable; smartctl was not run.")
        prediction = StoragePrediction("unavailable", "Disk was not readable.")
        samples = SampleSummary(
            sample_count, open_error="disk was not readable", logical_sector_bytes=0
        )
        return QuickDiskCheckResult(
            disk_number,
            None,
            None,
            unavailable,
            prediction,
            samples,
            time.monotonic() - started,
        )

    if not (before.hw_serial or "").strip():
        message = (
            "stable device/bridge identity is not exposed; refusing a "
            "hot-swappable-dock scan without a same-session identity"
        )
        unavailable = SmartctlSnapshot(False, message + "; smartctl was not run.")
        prediction = StoragePrediction("unavailable", message + ".")
        samples = SampleSummary(sample_count, open_error=message, logical_sector_bytes=0)
        return QuickDiskCheckResult(
            disk_number,
            before,
            before,
            unavailable,
            prediction,
            samples,
            time.monotonic() - started,
        )

    logical_sector_bytes, sector_message = sector_size_provider(before.raw_device_path)
    if logical_sector_bytes is None:
        message = f"cannot safely align sampled reads: {sector_message}"
        unavailable = SmartctlSnapshot(False, message + "; smartctl was not run.")
        prediction = StoragePrediction("unavailable", message + ".")
        samples = SampleSummary(sample_count, open_error=message, logical_sector_bytes=0)
        return QuickDiskCheckResult(
            disk_number,
            before,
            before,
            unavailable,
            prediction,
            samples,
            time.monotonic() - started,
        )

    executable = smartctl_executable
    if use_smartctl and executable is None:
        executable = find_smartctl()
    smartctl = (
        run_smartctl(executable, disk_number, runner=smartctl_runner)
        if use_smartctl
        else SmartctlSnapshot(False, "smartctl query disabled for this run.")
    )
    prediction = predictor(before.raw_device_path)
    samples = sample_disk_regions(
        before.raw_device_path,
        before.size_bytes,
        sample_count=sample_count,
        sample_bytes=sample_bytes,
        logical_sector_bytes=logical_sector_bytes,
        failure_limit=failure_limit,
        reader_factory=reader_factory,
        should_cancel=should_cancel,
        on_progress=on_progress,
    )
    after = identity_provider(disk_number)
    return QuickDiskCheckResult(
        disk_number,
        before,
        after,
        smartctl,
        prediction,
        samples,
        time.monotonic() - started,
        identity_changed=_identity_changed(before, after),
    )


def _drive_line(drive: DriveInfo | None, disk_number: int) -> str:
    if drive is None:
        return f"Disk {disk_number}: not readable"
    letters = ", ".join(drive.all_letters) if drive.all_letters else "none"
    serial = drive.hw_serial or "not exposed"
    model = drive.model or drive.label or "unknown model"
    return (
        f"Disk {disk_number}: {model}; {drive.size_str}; "
        f"device/bridge identity {serial}; "
        f"letters {letters}; system disk {'yes' if drive.is_system else 'no'}"
    )


def format_report(result: QuickDiskCheckResult, *, include_smartctl_raw: bool = True) -> str:
    logical_sector_bytes = result.samples.logical_sector_bytes
    sector_label = f"{logical_sector_bytes} bytes" if logical_sector_bytes else "unavailable"
    lines = [
        "ODIN Quick Disk Check (READ ONLY)",
        "=" * 36,
        _drive_line(result.before, result.disk_number),
        f"Path: \\\\.\\PhysicalDrive{result.disk_number}",
        "No write, repair, lock, dismount, or SMART self-test was issued.",
        "",
        f"smartctl: {result.smartctl.message}",
        f"Windows prediction: {result.prediction.message}",
        f"Logical sector size: {sector_label}",
    ]
    if result.smartctl.command:
        lines.append("smartctl command: " + " ".join(result.smartctl.command))
    if result.smartctl.exit_status is not None:
        lines.append(f"smartctl exit bitmask: 0x{result.smartctl.exit_status:02X}")
    samples = result.samples
    lines.extend(
        [
            "",
            f"Sampled reads: {samples.successful_regions}/{samples.attempted_regions} "
            f"regions readable; attempted {samples.attempted_regions}/"
            f"{samples.requested_regions}; {samples.bytes_read / (1 << 20):.2f} MiB read",
            f"64 KiB requests fully verified with 4 KiB subreads: {samples.small_read_fallbacks}",
        ]
    )
    if samples.stopped_after_failure_limit:
        skipped = samples.requested_regions - samples.attempted_regions
        lines.append(
            f"Sampling stopped at the read-failure limit; {skipped} requested regions "
            "were not attempted."
        )
    if samples.open_error:
        lines.append("Raw-read access: " + samples.open_error)
    for failure in samples.failures:
        lba = str(failure.offset // logical_sector_bytes) if logical_sector_bytes else "unavailable"
        lines.append(
            f"READ ERROR offset {failure.offset} (LBA {lba}), "
            f"length {failure.length}: {failure.error}"
        )
    lines.extend(
        [
            "",
            "Result: " + result.conclusion,
            f"Elapsed: {result.elapsed_seconds:.2f} seconds",
            "A sampled-read failure implicates the current drive/dock/cable/power path; "
            "it does not identify the failed component.",
        ]
    )
    if include_smartctl_raw and result.smartctl.raw_output:
        lines.extend(["", "smartctl raw JSON/output:", result.smartctl.raw_output])
    return "\n".join(lines)
