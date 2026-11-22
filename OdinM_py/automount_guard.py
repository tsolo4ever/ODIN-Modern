"""Temporarily disable Windows automount for the lifetime of one Odin instance."""

from __future__ import annotations

import ctypes
import subprocess
import sys
import winreg
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path


class AutomountError(RuntimeError):
    pass


def automount_enabled() -> bool:
    key_path = r"SYSTEM\CurrentControlSet\Services\MountMgr"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
            try:
                value, kind = winreg.QueryValueEx(key, "NoAutoMount")
            except FileNotFoundError:
                return True
    except OSError as exc:
        raise AutomountError(f"Cannot read Windows automount policy: {exc}") from exc
    if kind != winreg.REG_DWORD:
        raise AutomountError("Windows NoAutoMount policy is not a DWORD.")
    return value == 0


def set_automount(enabled: bool) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetSystemDirectoryW.argtypes = (wintypes.LPWSTR, wintypes.UINT)
    kernel32.GetSystemDirectoryW.restype = wintypes.UINT
    buffer = ctypes.create_unicode_buffer(32768)
    length = kernel32.GetSystemDirectoryW(buffer, len(buffer))
    if not length or length >= len(buffer):
        raise AutomountError("Cannot locate the Windows system directory.")
    switch = "/E" if enabled else "/N"
    try:
        result = subprocess.run(
            [str(Path(buffer.value) / "mountvol.exe"), switch],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AutomountError(f"mountvol {switch} failed: {exc}") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise AutomountError(f"mountvol {switch} failed (exit {result.returncode}): {detail}")


@contextmanager
def _exclusive_ownership():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    ctypes.set_last_error(0)
    handle = kernel32.CreateMutexW(None, False, r"Global\BV.OdinM.AutomountGuard")
    error = ctypes.get_last_error()
    if not handle:
        raise AutomountError(f"Cannot claim automount ownership: {ctypes.WinError(error)}")
    try:
        if error == 183:  # ERROR_ALREADY_EXISTS
            raise AutomountError("Another Odin instance is already protecting automount.")
        yield
    finally:
        kernel32.CloseHandle(handle)


class AutomountGuard:
    def __init__(
        self,
        *,
        query_enabled=automount_enabled,
        set_enabled=set_automount,
        ownership=_exclusive_ownership,
    ):
        self._query = query_enabled
        self._set = set_enabled
        self._ownership_factory = ownership
        self._ownership = None
        self._restore_enabled = False
        self.was_enabled = False

    def __enter__(self):
        if self._ownership is not None:
            raise AutomountError("Automount guard is already active.")
        ownership = self._ownership_factory()
        ownership.__enter__()
        self._ownership = ownership
        try:
            self.was_enabled = self._query()
            # A failed command/timeout can still have changed the system policy.
            self._restore_enabled = self.was_enabled
            self._set(False)
            if self._query():
                raise AutomountError("Windows automount remained enabled after mountvol /N.")
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise
        return self

    def __exit__(self, *_exc):
        try:
            if self._restore_enabled:
                try:
                    self._set(True)
                    if not self._query():
                        raise AutomountError("Windows automount remained disabled.")
                except Exception as exc:
                    raise AutomountError(
                        "Could not restore Windows automount. Run mountvol /E as "
                        f"administrator to restore it. Details: {exc}"
                    ) from exc
                self._restore_enabled = False
        finally:
            if self._ownership is not None:
                ownership, self._ownership = self._ownership, None
                ownership.__exit__(*_exc)
