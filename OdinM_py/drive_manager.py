"""
drive_manager.py
Polls for removable drives every 2 seconds using Win32 ctypes calls.
Groups partitions by physical disk so a USB with multiple partitions
appears as a single slot. The ODINC target is \\Device\\HarddiskN\\Partition0 (whole disk).
"""

import ctypes
import struct
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

DRIVE_REMOVABLE               = 2
POLL_INTERVAL_MS              = 2000
INVALID_HANDLE_VALUE          = ctypes.c_void_p(-1).value
IOCTL_STORAGE_GET_DEVICE_NUM  = 0x2D1080  # DeviceIoControl code
IOCTL_STORAGE_QUERY_PROPERTY  = 0x002D1400
FILE_SHARE_READ_WRITE         = 0x1 | 0x2
OPEN_EXISTING                 = 3


@dataclass
class DriveInfo:
    disk_number: int          # physical disk index (e.g. 3)
    first_letter: str         # first partition letter found, e.g. "E:"
    all_letters: List[str]    # all partition letters on this disk
    label: str = ""
    size_bytes: int = 0
    hw_serial: str = ""       # device firmware serial — stable across repartitions

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
        letters = ", ".join(self.all_letters)
        label = self.label or "Removable"
        return f"[Disk {self.disk_number}]  {letters}  {label}  ({self.size_str})"


# ── Win32 helpers ──────────────────────────────────────────────────────────────

def _get_volume_label(root: str) -> str:
    buf = ctypes.create_unicode_buffer(256)
    try:
        ctypes.windll.kernel32.GetVolumeInformationW(
            root, buf, 256, None, None, None, None, 0
        )
        return buf.value
    except Exception:
        return ""


GENERIC_READ = 0x80000000   # required for IOCTL_DISK_GET_LENGTH_INFO


def _get_physical_disk_size(disk_number: int) -> int:
    """Return total size of PhysicalDriveN via IOCTL_DISK_GET_LENGTH_INFO."""
    path = f"\\\\.\\PhysicalDrive{disk_number}"
    h = ctypes.windll.kernel32.CreateFileW(
        path, GENERIC_READ, FILE_SHARE_READ_WRITE, None, OPEN_EXISTING, 0, None
    )
    if h == INVALID_HANDLE_VALUE:
        return 0
    try:
        buf      = ctypes.create_string_buffer(8)   # GET_LENGTH_INFORMATION = LARGE_INTEGER
        returned = ctypes.c_ulong(0)
        ok = ctypes.windll.kernel32.DeviceIoControl(
            h, 0x7405C, None, 0, buf, 8, ctypes.byref(returned), None
        )
        if not ok:
            return 0
        return struct.unpack_from("<Q", buf.raw)[0]
    except OSError:
        return 0
    finally:
        ctypes.windll.kernel32.CloseHandle(h)


def _get_device_serial(disk_number: int) -> str:
    """
    Return the device firmware serial number for PhysicalDriveN via
    IOCTL_STORAGE_QUERY_PROPERTY / StorageDeviceProperty.
    Returns "" if the drive doesn't expose one (some cheap controllers don't).
    The hardware serial is stable across repartition/reformat, so it uniquely
    identifies a physical card even when Windows reuses the same disk index.
    """
    path = f"\\\\.\\PhysicalDrive{disk_number}"
    h = ctypes.windll.kernel32.CreateFileW(
        path, GENERIC_READ, FILE_SHARE_READ_WRITE, None, OPEN_EXISTING, 0, None
    )
    if h == INVALID_HANDLE_VALUE:
        return ""
    try:
        # STORAGE_PROPERTY_QUERY: PropertyId=0 (StorageDeviceProperty), QueryType=0
        query = ctypes.create_string_buffer(8)
        buf = ctypes.create_string_buffer(1024)
        returned = ctypes.c_ulong(0)
        ok = ctypes.windll.kernel32.DeviceIoControl(
            h, IOCTL_STORAGE_QUERY_PROPERTY,
            query, 8, buf, 1024, ctypes.byref(returned), None,
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
        ctypes.windll.kernel32.CloseHandle(h)


def _get_physical_disk_number(drive_letter: str) -> int:
    """
    Return the physical disk number for a drive letter like 'E:'.
    Uses IOCTL_STORAGE_GET_DEVICE_NUMBER.
    Returns -1 on failure.
    """
    path = f"\\\\.\\" + drive_letter.rstrip("\\")
    h = ctypes.windll.kernel32.CreateFileW(
        path, 0, FILE_SHARE_READ_WRITE, None, OPEN_EXISTING, 0, None
    )
    if h == INVALID_HANDLE_VALUE:
        return -1

    # STORAGE_DEVICE_NUMBER: DeviceType(DWORD), DeviceNumber(DWORD), PartitionNumber(DWORD)
    try:
        buf = ctypes.create_string_buffer(12)
        returned = ctypes.c_ulong(0)
        ok = ctypes.windll.kernel32.DeviceIoControl(
            h, IOCTL_STORAGE_GET_DEVICE_NUM, None, 0,
            buf, 12, ctypes.byref(returned), None
        )
    except OSError:
        return -1
    finally:
        ctypes.windll.kernel32.CloseHandle(h)

    if not ok:
        return -1
    _device_type, device_number, _partition = struct.unpack("III", buf.raw)
    return device_number


def is_removable(drive_letter: str) -> bool:
    """Return True only if drive_letter is still a removable drive."""
    root = drive_letter.rstrip("\\") + "\\"
    return ctypes.windll.kernel32.GetDriveTypeW(root) == DRIVE_REMOVABLE


def get_removable_drives() -> List[DriveInfo]:
    """
    Return one DriveInfo per physical removable disk.
    Multiple partition letters on the same disk are merged into one entry.
    """
    bitmask = ctypes.windll.kernel32.GetLogicalDrives()
    # disk_number → DriveInfo
    disks: Dict[int, DriveInfo] = {}

    for i in range(26):
        if not (bitmask & (1 << i)):
            continue
        letter = chr(65 + i) + ":"
        root   = letter + "\\"
        if ctypes.windll.kernel32.GetDriveTypeW(root) != DRIVE_REMOVABLE:
            continue

        disk_num = _get_physical_disk_number(letter)
        if disk_num < 0:
            continue  # couldn't determine disk — skip

        if disk_num not in disks:
            label     = _get_volume_label(root)
            size      = _get_physical_disk_size(disk_num)
            hw_serial = _get_device_serial(disk_num)
            disks[disk_num] = DriveInfo(
                disk_number=disk_num,
                first_letter=letter,
                all_letters=[letter],
                label=label,
                size_bytes=size,
                hw_serial=hw_serial,
            )
        else:
            disks[disk_num].all_letters.append(letter)

    return sorted(disks.values(), key=lambda d: d.disk_number)


# ── Monitor ────────────────────────────────────────────────────────────────────

class DriveMonitor:
    """
    Polls for removable drive changes and fires on_drives_changed
    when the set of physical disks changes.
    Uses tkinter root.after() — start after the root window exists.

    Change detection uses (disk_number, hw_serial) pairs so that a card swap
    that reuses the same Windows disk index is still detected as a new device.
    """

    def __init__(self, root, on_drives_changed: Callable[[List[DriveInfo]], None]):
        self._root       = root
        self._on_changed = on_drives_changed
        # Each entry is (disk_number, hw_serial) — hw_serial="" if unavailable
        self._last: List[tuple] = []
        # Last successfully read serial per disk_number.  Used to fill in ""
        # when the device is locked by an active write so we don't misread a
        # temporary read failure as a card swap.
        self._known_serials: Dict[int, str] = {}
        self._running = False

    def start(self):
        self._running = True
        self._poll()

    def stop(self):
        self._running = False

    def _poll(self):
        if not self._running:
            return
        try:
            current = get_removable_drives()
            # Update cached serials for any drive that returned a readable value.
            for d in current:
                if d.hw_serial:
                    self._known_serials[d.disk_number] = d.hw_serial
            # Use the last known serial when the device is temporarily
            # unreadable (locked by an active flash write), so a momentary ""
            # does not look like a card swap and trigger a spurious auto-clone.
            current_keys = [
                (d.disk_number,
                 d.hw_serial or self._known_serials.get(d.disk_number, ""))
                for d in current
            ]
            if current_keys != self._last:
                self._on_changed(current)   # update _last only after success
                self._last = current_keys
        except Exception:
            pass  # swallow all errors — keeps polling alive even if callback throws
        finally:
            self._root.after(POLL_INTERVAL_MS, self._poll)
