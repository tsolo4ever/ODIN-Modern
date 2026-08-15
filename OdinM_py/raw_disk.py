"""Binary reader that uses Win32 handles for physical disks on Windows."""

import ctypes
import ctypes.wintypes as wintypes
import os
from contextlib import contextmanager


_PHYSICAL_DRIVE_PREFIX = "\\\\.\\PhysicalDrive"
_GENERIC_READ = 0x80000000
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_OPEN_EXISTING = 3
_FILE_BEGIN = 0
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


def is_physical_drive_path(path: str) -> bool:
    return path.lower().startswith(_PHYSICAL_DRIVE_PREFIX.lower())


class Win32RawDiskReader:
    r"""Small seek/read adapter for ``\\.\PhysicalDriveN`` handles."""

    def __init__(self, path: str):
        if os.name != "nt":
            raise OSError(f"Win32 physical-disk access is unavailable for {path}")
        self.path = path
        self._k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._configure_functions()
        self._handle = self._k32.CreateFileW(
            path,
            _GENERIC_READ,
            _FILE_SHARE_READ | _FILE_SHARE_WRITE,
            None,
            _OPEN_EXISTING,
            0,
            None,
        )
        if self._handle == _INVALID_HANDLE_VALUE:
            self._raise_last_error("open")

    def _configure_functions(self) -> None:
        self._k32.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        self._k32.CreateFileW.restype = wintypes.HANDLE
        self._k32.ReadFile.argtypes = [
            wintypes.HANDLE,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        ]
        self._k32.ReadFile.restype = wintypes.BOOL
        self._k32.SetFilePointerEx.argtypes = [
            wintypes.HANDLE,
            ctypes.c_longlong,
            ctypes.POINTER(ctypes.c_longlong),
            wintypes.DWORD,
        ]
        self._k32.SetFilePointerEx.restype = wintypes.BOOL
        self._k32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._k32.CloseHandle.restype = wintypes.BOOL

    def _raise_last_error(self, operation: str) -> None:
        error = ctypes.get_last_error()
        detail = ctypes.FormatError(error).strip()
        raise OSError(error, f"Could not {operation} {self.path}: {detail}", self.path)

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        if whence != os.SEEK_SET:
            raise ValueError("Physical-disk reader supports absolute seeks only")
        position = ctypes.c_longlong()
        if not self._k32.SetFilePointerEx(
            self._handle, ctypes.c_longlong(offset), ctypes.byref(position), _FILE_BEGIN
        ):
            self._raise_last_error("seek")
        return int(position.value)

    def read(self, size: int) -> bytes:
        if size <= 0:
            return b""
        buffer = ctypes.create_string_buffer(size)
        read = wintypes.DWORD()
        if not self._k32.ReadFile(
            self._handle, buffer, size, ctypes.byref(read), None
        ):
            self._raise_last_error("read")
        return buffer.raw[: read.value]

    def close(self) -> None:
        if self._handle not in (None, _INVALID_HANDLE_VALUE):
            self._k32.CloseHandle(self._handle)
            self._handle = None

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        self.close()


@contextmanager
def open_binary_reader(path: str):
    """Open a file or physical disk for shared, read-only binary access."""
    reader = Win32RawDiskReader(path) if is_physical_drive_path(path) else open(path, "rb")
    try:
        yield reader
    finally:
        reader.close()
