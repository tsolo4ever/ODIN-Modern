"""
drive_manager.py
Polls for removable drives every 2 seconds using Win32 ctypes calls.
Groups partitions by physical disk so a USB with multiple partitions
appears as a single slot. The ODINC target is \\Device\\HarddiskN\\Partition0 (whole disk).
"""

import ctypes
import struct
from ctypes import wintypes
from dataclasses import dataclass
from collections.abc import Callable

DRIVE_REMOVABLE = 2
POLL_INTERVAL_MS = 2000
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
IOCTL_STORAGE_GET_DEVICE_NUM = 0x2D1080  # DeviceIoControl code
IOCTL_STORAGE_QUERY_PROPERTY = 0x002D1400
# Plain (non-EX) geometry ioctl - matches src/ODIN/DriveList.cpp's
# CDriveInfo::Refresh() exactly, ODIN's own proven removable-media check
# (DISK_GEOMETRY.MediaType == RemovableMedia). The _EX variant returns a
# larger DISK_GEOMETRY_EX struct that needs a bigger output buffer to
# succeed at all; this one only needs sizeof(DISK_GEOMETRY) = 24 bytes.
IOCTL_DISK_GET_DRIVE_GEOMETRY = 0x00070000
FILE_SHARE_READ_WRITE = 0x1 | 0x2
OPEN_EXISTING = 3
MAX_PHYSICAL_DISKS = 16  # highest \\.\PhysicalDriveN scanned

# use_last_error=True is required for GetLastError() to be reliably
# populated after these calls, and explicit argtypes/restype are required
# because HANDLE is pointer-sized: without them ctypes assumes CreateFileW
# returns a 32-bit int, silently truncating a real (64-bit) handle value AND
# a genuine failure (-1) so it never equals the correctly 64-bit
# INVALID_HANDLE_VALUE below - every open failure was going undetected and
# falling through to a DeviceIoControl call on a garbage handle instead.
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateFileW.restype = wintypes.HANDLE
_k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                             wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                             wintypes.HANDLE]
_k32.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                 wintypes.LPVOID, wintypes.DWORD,
                                 wintypes.LPVOID, wintypes.DWORD,
                                 ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]


def _err(msg: str) -> str:
    e = ctypes.get_last_error()
    return f"{msg} (err={e}: {ctypes.FormatError(e)})"


@dataclass
class DriveInfo:
    disk_number: int  # physical disk index (e.g. 3)
    first_letter: str  # first partition letter found, e.g. "E:"
    all_letters: list[str]  # all partition letters on this disk
    label: str = ""
    size_bytes: int = 0
    hw_serial: str = ""  # device firmware serial — stable across repartitions

    @property
    def target_path(self) -> str:
        """ODINC -target argument — whole physical disk.
        ODIN's PreprocessSourceAndTarget matches the Device prefix to look
        up the device index; Partition0 = the entire disk (not a volume)."""
        return f"\\Device\\Harddisk{self.disk_number}\\Partition0"

    @property
    def raw_device_path(self) -> str:
        """Win32 path used by Python to read the whole physical disk."""
        return f"\\\\.\\PhysicalDrive{self.disk_number}"

    @property
    def size_str(self) -> str:
        if self.size_bytes <= 0:
            return "?"
        for unit, thresh in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
            if self.size_bytes >= thresh:
                return f"{self.size_bytes / thresh:.1f} {unit}"
        return f"{self.size_bytes} B"

    @property
    def display(self) -> str:
        letters = ", ".join(self.all_letters) if self.all_letters else "(no drive letter)"
        label = self.label or "Removable"
        return f"[Disk {self.disk_number}]  {letters}  {label}  ({self.size_str})"


# ── Win32 helpers ──────────────────────────────────────────────────────────────


def _get_volume_label(root: str) -> str:
    buf = ctypes.create_unicode_buffer(256)
    try:
        ctypes.windll.kernel32.GetVolumeInformationW(root, buf, 256, None, None, None, None, 0)
        return buf.value
    except Exception:
        return ""


GENERIC_READ = 0x80000000  # required for IOCTL_DISK_GET_LENGTH_INFO


def _get_physical_disk_size(disk_number: int) -> int:
    """Return total size of PhysicalDriveN via IOCTL_DISK_GET_LENGTH_INFO."""
    path = f"\\\\.\\PhysicalDrive{disk_number}"
    h = _k32.CreateFileW(
        path, GENERIC_READ, FILE_SHARE_READ_WRITE, None, OPEN_EXISTING, 0, None
    )
    if h == INVALID_HANDLE_VALUE:
        return 0
    try:
        buf = ctypes.create_string_buffer(8)  # GET_LENGTH_INFORMATION = LARGE_INTEGER
        returned = ctypes.c_ulong(0)
        ok = _k32.DeviceIoControl(
            h, 0x7405C, None, 0, buf, 8, ctypes.byref(returned), None
        )
        if not ok:
            return 0
        return struct.unpack_from("<Q", buf.raw)[0]
    except OSError:
        return 0
    finally:
        _k32.CloseHandle(h)


def _get_device_serial(disk_number: int) -> str:
    """
    Return the device firmware serial number for PhysicalDriveN via
    IOCTL_STORAGE_QUERY_PROPERTY / StorageDeviceProperty.
    Returns "" if the drive doesn't expose one (some cheap controllers don't).
    The hardware serial is stable across repartition/reformat, so it uniquely
    identifies a physical card even when Windows reuses the same disk index.
    """
    path = f"\\\\.\\PhysicalDrive{disk_number}"
    h = _k32.CreateFileW(
        path, GENERIC_READ, FILE_SHARE_READ_WRITE, None, OPEN_EXISTING, 0, None
    )
    if h == INVALID_HANDLE_VALUE:
        return ""
    try:
        # STORAGE_PROPERTY_QUERY: PropertyId=0 (StorageDeviceProperty), QueryType=0
        query = ctypes.create_string_buffer(8)
        buf = ctypes.create_string_buffer(1024)
        returned = ctypes.c_ulong(0)
        ok = _k32.DeviceIoControl(
            h,
            IOCTL_STORAGE_QUERY_PROPERTY,
            query,
            8,
            buf,
            1024,
            ctypes.byref(returned),
            None,
        )
        if not ok or returned.value < 28:
            return ""
        # STORAGE_DEVICE_DESCRIPTOR: SerialNumberOffset is ULONG at byte 24
        serial_offset = struct.unpack_from("<I", buf.raw, 24)[0]
        if serial_offset == 0 or serial_offset >= returned.value:
            return ""
        end = buf.raw.find(b"\x00", serial_offset)
        end = end if end != -1 else int(returned.value)
        return buf.raw[serial_offset:end].decode("ascii", errors="ignore").strip()
    except Exception:
        return ""
    finally:
        _k32.CloseHandle(h)


def _get_physical_disk_number(drive_letter: str) -> int:
    """
    Return the physical disk number for a drive letter like 'E:'.
    Uses IOCTL_STORAGE_GET_DEVICE_NUMBER.
    Returns -1 on failure.
    """
    path = "\\\\.\\" + drive_letter.rstrip("\\")
    h = _k32.CreateFileW(
        path, 0, FILE_SHARE_READ_WRITE, None, OPEN_EXISTING, 0, None
    )
    if h == INVALID_HANDLE_VALUE:
        return -1

    # STORAGE_DEVICE_NUMBER: DeviceType(DWORD), DeviceNumber(DWORD), PartitionNumber(DWORD)
    try:
        buf = ctypes.create_string_buffer(12)
        returned = ctypes.c_ulong(0)
        ok = _k32.DeviceIoControl(
            h, IOCTL_STORAGE_GET_DEVICE_NUM, None, 0, buf, 12, ctypes.byref(returned), None
        )
    except OSError:
        return -1
    finally:
        _k32.CloseHandle(h)

    if not ok:
        return -1
    _device_type, device_number, _partition = struct.unpack("III", buf.raw)
    return device_number


def is_removable(drive_letter: str) -> bool:
    """Return True only if drive_letter is still a removable drive.

    Only meaningful for a drive that already has a letter. A disk with no
    mounted volume (offline, unformatted, or mid partition-table refresh)
    will never have one to check - use is_disk_removable() instead for the
    actual flash-safety gate, since that works at the disk level."""
    root = drive_letter.rstrip("\\") + "\\"
    return ctypes.windll.kernel32.GetDriveTypeW(root) == DRIVE_REMOVABLE


def is_disk_removable(disk_number: int) -> bool:
    """Return True if PhysicalDriveN reports itself as removable media.

    Checked entirely at the disk level via two independent WinAPI signals
    (some devices only set one of the two) - so it works even with no
    mounted volume at all: offline (e.g. after an MBR signature collision),
    factory-blank/unformatted, or mid partition-table refresh. This is the
    check that actually gates whether a disk is safe to flash; a fixed
    internal disk (the OS drive, an internal SSD) reports removable=False
    from both signals and is never mistaken for a flashable card.
    """
    path = f"\\\\.\\PhysicalDrive{disk_number}"
    h = _k32.CreateFileW(
        path, GENERIC_READ, FILE_SHARE_READ_WRITE, None, OPEN_EXISTING, 0, None
    )
    if h == INVALID_HANDLE_VALUE:
        return False
    try:
        # STORAGE_DEVICE_DESCRIPTOR.RemovableMedia (BOOLEAN at byte offset 10)
        query = ctypes.create_string_buffer(struct.pack("<III", 0, 0, 0))
        buf = ctypes.create_string_buffer(1024)
        returned = wintypes.DWORD(0)
        ok = _k32.DeviceIoControl(
            h, IOCTL_STORAGE_QUERY_PROPERTY,
            ctypes.byref(query), ctypes.sizeof(query),
            ctypes.byref(buf), ctypes.sizeof(buf),
            ctypes.byref(returned), None,
        )
        if ok and returned.value >= 11 and buf.raw[10] != 0:
            return True
        # Fallback: DISK_GEOMETRY.MediaType == 11 (RemovableMedia) - some
        # controllers only report removability here, not in the descriptor
        # above. This is ODIN's own check (src/ODIN/DriveList.cpp,
        # CDriveInfo::Refresh): IOCTL_DISK_GET_DRIVE_GEOMETRY (not the _EX
        # variant) into a plain 24-byte DISK_GEOMETRY struct, proven across
        # years of production use.
        geom = ctypes.create_string_buffer(24)
        ok2 = _k32.DeviceIoControl(
            h, IOCTL_DISK_GET_DRIVE_GEOMETRY, None, 0,
            ctypes.byref(geom), ctypes.sizeof(geom),
            ctypes.byref(returned), None,
        )
        return bool(ok2) and struct.unpack_from("<I", geom.raw, 8)[0] == 11
    except OSError:
        return False
    finally:
        _k32.CloseHandle(h)


def debug_probe_disks(
    max_disks: int = MAX_PHYSICAL_DISKS, removable_limit: int = 0
) -> list[str]:
    r"""One human-readable diagnostic line per \\.\PhysicalDriveN, showing
    exactly why get_removable_drives() did or didn't include it: whether
    the disk could even be opened (with the GetLastError detail if not),
    its removable-media result, and its size.

    If removable_limit is positive, stop after that many removable disks
    have been found. Errors encountered before reaching the limit remain
    visible. A zero limit scans the full configured physical-disk range.

    For manually troubleshooting a "0 removable drive(s) detected" report
    when Explorer clearly shows a card connected - opening a physical disk
    device requires Administrator privileges even for read-only access
    (unlike the old drive-letter approach this replaced), so ERROR_ACCESS_
    DENIED (5) here almost always means the app itself isn't elevated.
    """
    lines = []
    removable_found = 0
    for n in range(max_disks):
        path = f"\\\\.\\PhysicalDrive{n}"
        h = _k32.CreateFileW(path, GENERIC_READ, FILE_SHARE_READ_WRITE,
                             None, OPEN_EXISTING, 0, None)
        if h == INVALID_HANDLE_VALUE:
            err = ctypes.get_last_error()
            lines.append(f"disk {n}: cannot open — err={err} "
                        f"({ctypes.FormatError(err)})")
            continue
        _k32.CloseHandle(h)
        removable = is_disk_removable(n)
        size = _get_physical_disk_size(n)
        lines.append(f"disk {n}: open OK — removable={removable}, "
                    f"size={size} bytes")
        if removable:
            removable_found += 1
            if removable_limit > 0 and removable_found >= removable_limit:
                break
    return lines


def _letters_by_disk() -> dict[int, list[str]]:
    r"""Map disk_number -> currently-mounted removable drive letters.

    Display-only (label, "E:, F:") - a disk with no entry here is still a
    fully valid, flashable slot, since flashing writes to \\.\PhysicalDriveN
    directly and never needs a mounted volume."""
    out: dict[int, list[str]] = {}
    bitmask = ctypes.windll.kernel32.GetLogicalDrives()
    for i in range(26):
        if not (bitmask & (1 << i)):
            continue
        letter = chr(65 + i) + ":"
        if ctypes.windll.kernel32.GetDriveTypeW(letter + "\\") != DRIVE_REMOVABLE:
            continue
        disk_num = _get_physical_disk_number(letter)
        if disk_num >= 0:
            out.setdefault(disk_num, []).append(letter)
    return out


def get_removable_drives() -> list[DriveInfo]:
    r"""
    Return one DriveInfo per physical removable disk.

    Detected by probing \\.\PhysicalDriveN directly (0..MAX_PHYSICAL_DISKS),
    NOT by walking Windows drive letters - so a disk shows up here even with
    no mounted volume at all: offline (e.g. after an MBR signature
    collision - see pyimager.randomize_disk_signature), factory-blank/
    unformatted, or mid partition-table refresh. None of that affects
    whether the disk can be flashed, since restore only ever writes to
    \\.\PhysicalDriveN directly. Letters and volume label are still
    gathered for display, when a mounted volume happens to exist.
    """
    letters_by_disk = _letters_by_disk()
    drives = []
    for n in range(MAX_PHYSICAL_DISKS):
        if not is_disk_removable(n):
            continue
        size = _get_physical_disk_size(n)
        if size == 0:
            continue  # disk doesn't actually exist at this number
        letters = sorted(letters_by_disk.get(n, []))
        label = _get_volume_label(letters[0] + "\\") if letters else ""
        drives.append(DriveInfo(
            disk_number=n,
            first_letter=letters[0] if letters else "",
            all_letters=letters,
            label=label,
            size_bytes=size,
            hw_serial=_get_device_serial(n),
        ))
    return sorted(drives, key=lambda d: d.disk_number)


# ── Monitor ────────────────────────────────────────────────────────────────────


class DriveMonitor:
    """
    Drive detection is on-demand, not continuously polled:

    - refresh() is a one-shot manual scan - the only way a new disk gets
      discovered to fill an empty slot. Meant to be triggered by an explicit
      "Refresh Disks" action, as the first step of the operator's setup
      flow (refresh, then Confirm each slot, then Start).
    - watch_slot() starts narrow, single-disk polling for exactly one disk
      number, only once a slot has been confirmed/locked. This is what
      catches a card being pulled mid-flash, or feeds the 15-minute
      confirm-lock grace period - nothing is ever polled before a slot is
      locked, so leaving the app open with no confirmed slots does no
      background work and never touches the hardware on its own.

    Uses tkinter root.after() — start after the root window exists.
    """

    def __init__(self, root, on_drives_changed: Callable[[list[DriveInfo]], None]):
        self._root = root
        self._on_changed = on_drives_changed
        self._known_serials: dict[int, str] = {}
        # slot_idx -> {"disk_number", "miss_streak", "job"}
        self._watches: dict[int, dict] = {}

    def refresh(self):
        """One-shot manual scan - fires on_drives_changed with whatever is
        currently present. The sole way a new disk gets discovered."""
        try:
            current = get_removable_drives()
            for d in current:
                if d.hw_serial:
                    self._known_serials[d.disk_number] = d.hw_serial
            self._on_changed(current)
        except Exception:
            pass  # never let a bad scan crash the caller

    def watch_slot(self, slot_idx: int, disk_number: int,
                   on_missing: Callable[[], None],
                   interval_ms: int = POLL_INTERVAL_MS, miss_threshold: int = 2):
        """Start narrow per-disk polling for exactly one disk number.

        Calls on_missing() once the disk has been absent for
        `miss_threshold` consecutive checks - a transient blip (e.g.
        update_properties() briefly dropping a letter) doesn't immediately
        read as a removal. Completely independent of every other slot: this
        watch only ever looks at `disk_number`, so nothing happening to any
        other disk can affect it. Replaces any existing watch on this slot.
        """
        self.unwatch_slot(slot_idx)
        state = {"disk_number": disk_number, "miss_streak": 0, "job": None}
        self._watches[slot_idx] = state

        def _check():
            if self._watches.get(slot_idx) is not state:
                return  # this watch was cancelled/replaced - stale callback
            if is_disk_removable(disk_number):
                state["miss_streak"] = 0
            else:
                state["miss_streak"] += 1
                if state["miss_streak"] >= miss_threshold:
                    del self._watches[slot_idx]
                    on_missing()
                    return
            state["job"] = self._root.after(interval_ms, _check)

        state["job"] = self._root.after(interval_ms, _check)

    def watch_for_return(self, slot_idx: int, disk_number: int,
                         on_return: Callable[[], None],
                         interval_ms: int = POLL_INTERVAL_MS):
        """Start narrow per-disk polling for exactly one disk number to
        REAPPEAR - the mirror of watch_slot(), used during a locked slot's
        15-minute confirm grace period so the slot keeps looking for its own
        disk_number without requiring a manual Refresh Disks click. Fires
        on_return() once the disk is seen present, then stops (one-shot).
        Completely independent of every other slot, same as watch_slot().
        Replaces any existing watch on this slot.
        """
        self.unwatch_slot(slot_idx)
        state = {"disk_number": disk_number, "miss_streak": 0, "job": None}
        self._watches[slot_idx] = state

        def _check():
            if self._watches.get(slot_idx) is not state:
                return  # this watch was cancelled/replaced - stale callback
            if is_disk_removable(disk_number):
                del self._watches[slot_idx]
                on_return()
                return
            state["job"] = self._root.after(interval_ms, _check)

        state["job"] = self._root.after(interval_ms, _check)

    def unwatch_slot(self, slot_idx: int):
        """Stop watching a slot, e.g. because it's being reassigned."""
        state = self._watches.pop(slot_idx, None)
        if state is not None and state["job"] is not None:
            self._root.after_cancel(state["job"])
