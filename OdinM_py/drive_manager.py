"""
drive_manager.py
Polls for removable drives every 2 seconds using Win32 ctypes calls.
Groups partitions by physical disk so a USB with multiple partitions
appears as a single slot. The ODINC target is \\Device\\HarddiskN\\Partition0 (whole disk).

Drive detection enumerates PhysicalDriveN directly via IOCTL_STORAGE_QUERY_PROPERTY
so that drives without assigned letters (e.g. suppressed by Windows due to MBR
signature collision after cloning) are still detected.
"""

import ctypes
import os
import struct
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

DRIVE_REMOVABLE               = 2
POLL_INTERVAL_MS              = 2000
INVALID_HANDLE_VALUE          = ctypes.c_void_p(-1).value
IOCTL_STORAGE_QUERY_PROPERTY  = 0x2D1400  # queries STORAGE_DEVICE_DESCRIPTOR
FILE_SHARE_READ_WRITE         = 0x1 | 0x2
OPEN_EXISTING                 = 3
GENERIC_READ                  = 0x80000000
ERROR_FILE_NOT_FOUND          = 2


@dataclass
class DriveInfo:
    disk_number: int          # physical disk index (e.g. 3)
    size_bytes: int = 0

    @property
    def target_path(self) -> str:
        """ODINC -target argument — whole physical disk.
        ODIN's PreprocessSourceAndTarget matches the Device prefix to look
        up the device index; Partition0 = the entire disk (not a volume)."""
        return f"\\Device\\Harddisk{self.disk_number}\\Partition0"

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
        return f"[Disk {self.disk_number}]  ({self.size_str})"


# ── Win32 helpers ──────────────────────────────────────────────────────────────

def _check_physical_drive(disk_number: int) -> Tuple[Optional[bool], int]:
    """
    Open \\\\.\\PhysicalDriveN and query RemovableMedia flag and disk size.

    Returns:
        (None,  0)          drive does not exist (ERROR_FILE_NOT_FOUND)
        (False, 0)          drive exists but is not removable, or access denied
        (True,  size_bytes) drive is removable; size_bytes may be 0 on error
    """
    path = f"\\\\.\\PhysicalDrive{disk_number}"
    h = ctypes.windll.kernel32.CreateFileW(
        path, GENERIC_READ, FILE_SHARE_READ_WRITE, None, OPEN_EXISTING, 0, None
    )
    if h == INVALID_HANDLE_VALUE:
        err = ctypes.windll.kernel32.GetLastError()
        if err == ERROR_FILE_NOT_FOUND:
            return None, 0   # no drive at this index
        return False, 0      # access denied or other — drive exists but unreadable

    try:
        # ── RemovableMedia flag via IOCTL_STORAGE_QUERY_PROPERTY ──
        # Input: STORAGE_PROPERTY_QUERY {PropertyId=StorageDeviceProperty(0),
        #                                QueryType=PropertyStandardQuery(0),
        #                                AdditionalParameters[1]=0}
        in_data = struct.pack("<IIB", 0, 0, 0)   # 9 bytes
        in_buf  = ctypes.create_string_buffer(in_data, len(in_data))
        out_buf = ctypes.create_string_buffer(512)
        returned = ctypes.c_ulong(0)
        ok = ctypes.windll.kernel32.DeviceIoControl(
            h, IOCTL_STORAGE_QUERY_PROPERTY,
            in_buf, ctypes.sizeof(in_buf),
            out_buf, ctypes.sizeof(out_buf),
            ctypes.byref(returned), None,
        )
        # STORAGE_DEVICE_DESCRIPTOR.RemovableMedia is at byte offset 10
        if not ok or returned.value < 11 or not out_buf.raw[10]:
            return False, 0

        # ── Disk size via IOCTL_DISK_GET_LENGTH_INFO ──
        size_buf = ctypes.create_string_buffer(8)   # GET_LENGTH_INFORMATION = LARGE_INTEGER
        size_ret = ctypes.c_ulong(0)
        ok2 = ctypes.windll.kernel32.DeviceIoControl(
            h, 0x7405C, None, 0, size_buf, 8, ctypes.byref(size_ret), None
        )
        size = struct.unpack_from("<Q", size_buf.raw)[0] if ok2 else 0
        return True, size

    except OSError:
        return False, 0
    finally:
        ctypes.windll.kernel32.CloseHandle(h)


def randomize_disk_signature(disk_number: int) -> bool:
    """
    Write a random 4-byte disk signature to MBR offset 0x1B8 on PhysicalDriveN.
    Called after a successful flash so each cloned drive gets a unique ID,
    preventing Windows from suppressing drive letters on subsequent insertions.
    Returns True on success, False on any error.
    """
    GENERIC_WRITE = 0x40000000
    path = f"\\\\.\\PhysicalDrive{disk_number}"
    h = ctypes.windll.kernel32.CreateFileW(
        path, GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ_WRITE,
        None, OPEN_EXISTING, 0, None,
    )
    if h == INVALID_HANDLE_VALUE:
        return False
    try:
        buf     = ctypes.create_string_buffer(512)
        read_n  = ctypes.c_ulong(0)
        if not ctypes.windll.kernel32.ReadFile(h, buf, 512, ctypes.byref(read_n), None):
            return False
        if read_n.value != 512:
            return False

        # Patch disk signature (4 bytes at 0x1B8 in the MBR)
        buf[0x1B8:0x1BC] = os.urandom(4)

        # Seek back to sector 0 (FILE_BEGIN = 0)
        ctypes.windll.kernel32.SetFilePointer(h, 0, None, 0)

        written = ctypes.c_ulong(0)
        ok = ctypes.windll.kernel32.WriteFile(h, buf, 512, ctypes.byref(written), None)
        return bool(ok) and written.value == 512
    except OSError:
        return False
    finally:
        ctypes.windll.kernel32.CloseHandle(h)


def is_removable(disk_number: int) -> bool:
    """Return True if the physical disk is still present and removable."""
    result, _ = _check_physical_drive(disk_number)
    return result is True


def get_removable_drives() -> List[DriveInfo]:
    """
    Return one DriveInfo per physical removable disk, sorted by disk number.
    Enumerates \\\\.\\PhysicalDriveN directly so drives with no letter
    (suppressed by Windows due to MBR signature collision) are detected.
    """
    disks: Dict[int, DriveInfo] = {}

    for n in range(32):
        result, size = _check_physical_drive(n)
        if result is None:
            break      # ERROR_FILE_NOT_FOUND — no more drives at higher indices
        if not result:
            continue   # not removable or access denied
        disks[n] = DriveInfo(disk_number=n, size_bytes=size)

    return sorted(disks.values(), key=lambda d: d.disk_number)


# ── Monitor ────────────────────────────────────────────────────────────────────

# TODO(v0.5): Replace DriveMonitor polling with WM_DEVICECHANGE via
# RegisterDeviceNotification + ctypes WNDPROC binding on the tkinter HWND.
# This gives instant OS-level notification instead of a 2-second poll lag,
# and eliminates transient misses caused by IOCTL failures during active I/O.
# Reference: DBT_DEVICEARRIVAL / DBT_DEVICEREMOVECOMPLETE messages.

class DriveMonitor:
    """
    Polls for removable drive changes and fires on_drives_changed
    when the set of physical disks changes.
    Uses tkinter root.after() — start after the root window exists.
    """

    def __init__(self, root, on_drives_changed: Callable[[List[DriveInfo]], None]):
        self._root       = root
        self._on_changed = on_drives_changed
        self._last: List[int] = []   # last seen disk numbers
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
            current_nums = [d.disk_number for d in current]
            if current_nums != self._last:
                self._last = current_nums
                self._on_changed(current)
        except OSError:
            pass  # transient Win32 error during active disk I/O — skip this poll
        self._root.after(POLL_INTERVAL_MS, self._poll)
