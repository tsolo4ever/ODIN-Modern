"""Minimal read-only file-like wrapper around \\\\.\\PhysicalDriveN, via ctypes.
Never opens for write — GENERIC_READ only. Handles the fact that raw disk I/O
on Windows requires both the file position AND the read length to be sector
(512-byte) aligned, by reading an aligned superset and slicing it down."""

import ctypes
from ctypes import wintypes

GENERIC_READ = 0x80000000
FILE_SHARE_RW = 0x1 | 0x2
OPEN_EXISTING = 3
INVALID_HANDLE = ctypes.c_void_p(-1).value
FILE_BEGIN = 0
SECTOR = 512

# use_last_error so GetLastError() isn't clobbered before we read it
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)

# HANDLE is pointer-sized; without an explicit restype ctypes truncates it to a
# signed 32-bit int and every subsequent call fails with ERROR_INVALID_HANDLE.
_k32.CreateFileW.restype = wintypes.HANDLE
_k32.CreateFileW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
    wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
]
_k32.SetFilePointerEx.restype = wintypes.BOOL
_k32.SetFilePointerEx.argtypes = [
    wintypes.HANDLE, ctypes.c_longlong,
    ctypes.POINTER(ctypes.c_longlong), wintypes.DWORD,
]
_k32.ReadFile.restype = wintypes.BOOL
_k32.ReadFile.argtypes = [
    wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID,
]
_k32.CloseHandle.restype = wintypes.BOOL
_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_k32.DeviceIoControl.restype = wintypes.BOOL
_k32.DeviceIoControl.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
    wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID,
]

IOCTL_DISK_GET_LENGTH_INFO = 0x0007405C


class RawDiskReader:
    def __init__(self, disk_number: int):
        path = rf"\\.\PhysicalDrive{disk_number}"
        self._handle = _k32.CreateFileW(
            path, GENERIC_READ, FILE_SHARE_RW, None, OPEN_EXISTING, 0, None
        )
        if self._handle is None or self._handle == INVALID_HANDLE:
            err = ctypes.get_last_error()
            raise OSError(f"Cannot open {path} for reading (err={err}: {ctypes.FormatError(err)})")
        self._pos = 0
        self._size = self._query_length()

    def _query_length(self) -> int:
        out = ctypes.c_ulonglong(0)
        returned = wintypes.DWORD(0)
        ok = _k32.DeviceIoControl(
            self._handle, IOCTL_DISK_GET_LENGTH_INFO, None, 0,
            ctypes.byref(out), ctypes.sizeof(out), ctypes.byref(returned), None,
        )
        if not ok:
            err = ctypes.get_last_error()
            raise OSError(f"IOCTL_DISK_GET_LENGTH_INFO failed (err={err}: {ctypes.FormatError(err)})")
        return out.value

    @property
    def size(self) -> int:
        return self._size

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 1:
            offset = self._pos + offset
        elif whence == 2:
            offset = self._size + offset
        self._pos = offset
        return self._pos

    def tell(self) -> int:
        return self._pos

    def _seek_raw(self, offset: int) -> None:
        if not _k32.SetFilePointerEx(self._handle, offset, None, FILE_BEGIN):
            err = ctypes.get_last_error()
            raise OSError(f"seek to {offset} failed (err={err}: {ctypes.FormatError(err)})")

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            raise NotImplementedError("read-all not supported on raw disk")
        if size == 0:
            return b""

        start = self._pos
        end = start + size
        aligned_start = (start // SECTOR) * SECTOR
        aligned_end = ((end + SECTOR - 1) // SECTOR) * SECTOR
        aligned_len = aligned_end - aligned_start

        self._seek_raw(aligned_start)
        buf = ctypes.create_string_buffer(aligned_len)
        got = wintypes.DWORD(0)
        if not _k32.ReadFile(self._handle, buf, aligned_len, ctypes.byref(got), None):
            err = ctypes.get_last_error()
            raise OSError(
                f"ReadFile failed at {aligned_start} len {aligned_len} "
                f"(err={err}: {ctypes.FormatError(err)})"
            )

        front_trim = start - aligned_start
        result = buf.raw[front_trim : front_trim + min(size, max(0, got.value - front_trim))]
        self._pos = start + len(result)
        return result

    def peek(self, size: int) -> bytes:
        """Read without advancing the logical position (ext4.Volume needs this)."""
        saved = self._pos
        try:
            return self.read(size)
        finally:
            self._pos = saved

    def close(self):
        if self._handle and self._handle != INVALID_HANDLE:
            _k32.CloseHandle(self._handle)
            self._handle = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
