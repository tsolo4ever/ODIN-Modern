"""Cross-process warning markers for ODIN and WSL Bridge."""

from __future__ import annotations

import ctypes
import os
from collections.abc import Callable, Iterable
from ctypes import wintypes

ODIN_RUNTIME_MARKER = r"Local\BV.RawDiskTools.OdinM_py"
WSL_BRIDGE_RUNTIME_MARKER = r"Local\BV.RawDiskTools.WSLBridge"

_SYNCHRONIZE = 0x00100000
_TH32CS_SNAPPROCESS = 0x00000002
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_ODIN_EXECUTABLES = {"odinm_py.exe", "odinm_py v2.exe"}
_WSL_BRIDGE_EXECUTABLES = {"wsl-bridge.exe", "wsl bridge.exe", "bv-wsl-bridge.exe"}


class _ProcessEntry(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


def _kernel32():
    library = ctypes.WinDLL("kernel32", use_last_error=True)
    library.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    library.CreateMutexW.restype = wintypes.HANDLE
    library.OpenMutexW.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
    library.OpenMutexW.restype = wintypes.HANDLE
    library.CloseHandle.argtypes = (wintypes.HANDLE,)
    library.CloseHandle.restype = wintypes.BOOL
    library.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    library.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    library.Process32FirstW.argtypes = (wintypes.HANDLE, ctypes.POINTER(_ProcessEntry))
    library.Process32FirstW.restype = wintypes.BOOL
    library.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.POINTER(_ProcessEntry))
    library.Process32NextW.restype = wintypes.BOOL
    return library


class RuntimeMarker:
    def __init__(self, handle: int | None):
        self._handle = handle

    def close(self) -> None:
        if self._handle and os.name == "nt":
            _kernel32().CloseHandle(self._handle)
            self._handle = None

    def __enter__(self) -> RuntimeMarker:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def _register(marker_name: str) -> RuntimeMarker:
    if os.name != "nt":
        return RuntimeMarker(None)
    handle = _kernel32().CreateMutexW(None, False, marker_name)
    return RuntimeMarker(handle)


def _marker_exists(marker_name: str) -> bool:
    if os.name != "nt":
        return False
    handle = _kernel32().OpenMutexW(_SYNCHRONIZE, False, marker_name)
    if not handle:
        return False
    _kernel32().CloseHandle(handle)
    return True


def _process_names() -> tuple[str, ...]:
    if os.name != "nt":
        return ()
    kernel32 = _kernel32()
    snapshot = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if snapshot == _INVALID_HANDLE_VALUE:
        return ()
    names: list[str] = []
    try:
        entry = _ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        found = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while found:
            names.append(entry.szExeFile)
            found = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return tuple(names)


def _is_running(
    marker_name: str,
    executable_names: set[str],
    *,
    process_names: Iterable[str] | None = None,
    marker_probe: Callable[[str], bool] | None = None,
) -> bool:
    probe = _marker_exists if marker_probe is None else marker_probe
    if probe(marker_name):
        return True
    names = _process_names() if process_names is None else tuple(process_names)
    return any(name.casefold() in executable_names for name in names)


def register_odin_runtime() -> RuntimeMarker:
    return _register(ODIN_RUNTIME_MARKER)


def register_wsl_bridge_runtime() -> RuntimeMarker:
    return _register(WSL_BRIDGE_RUNTIME_MARKER)


def odin_running(
    *,
    process_names: Iterable[str] | None = None,
    marker_probe: Callable[[str], bool] | None = None,
) -> bool:
    return _is_running(
        ODIN_RUNTIME_MARKER,
        _ODIN_EXECUTABLES,
        process_names=process_names,
        marker_probe=marker_probe,
    )


def wsl_bridge_running(
    *,
    process_names: Iterable[str] | None = None,
    marker_probe: Callable[[str], bool] | None = None,
) -> bool:
    return _is_running(
        WSL_BRIDGE_RUNTIME_MARKER,
        _WSL_BRIDGE_EXECUTABLES,
        process_names=process_names,
        marker_probe=marker_probe,
    )
