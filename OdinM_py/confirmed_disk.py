"""Per-target filesystem isolation, acquired before a slot becomes confirmed."""

from __future__ import annotations

import ctypes
import re
import struct
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes
from dataclasses import replace

from guarded_flash_safety import ProtectedHardwareStore
from runtime_interlock import wsl_bridge_running
from scripts import pyimager

_GUID_PATH = re.compile(r"^\\\\\?\\Volume\{[0-9a-f-]{36}\}\\$", re.IGNORECASE)


class IsolationError(RuntimeError):
    pass


def _identity(disk):
    # Removing Windows access paths is deliberate; volume GUIDs still identify
    # the media behind readers that report the same serial for every slot.
    guids = tuple(sorted(p.casefold() for p in disk.mounted_volumes if _GUID_PATH.fullmatch(p)))
    return replace(disk, mounted_volumes=guids).revalidation_tuple


class ConfirmedDiskIsolation:
    def __init__(self, disk, handles):
        self.disk = disk
        self._handles = list(handles)
        self.active = True

    def validate(self, current):
        if not self.active or _identity(current) != _identity(self.disk):
            raise IsolationError("confirmed target changed or its isolation was released")

    def close(self):
        self.active = False
        handles, self._handles = self._handles, []
        for handle in handles:
            try:
                handle.unlock()
            except OSError:
                pass
            finally:
                try:
                    handle.close()
                except OSError:
                    pass


class WindowsMountPaths:
    def __init__(self):
        self._k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._k32.GetVolumeNameForVolumeMountPointW.argtypes = (
            wintypes.LPCWSTR,
            wintypes.LPWSTR,
            wintypes.DWORD,
        )
        self._k32.GetVolumeNameForVolumeMountPointW.restype = wintypes.BOOL
        self._k32.DeleteVolumeMountPointW.argtypes = (wintypes.LPCWSTR,)
        self._k32.DeleteVolumeMountPointW.restype = wintypes.BOOL
        self._k32.SetVolumeMountPointW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
        self._k32.SetVolumeMountPointW.restype = wintypes.BOOL

    def volume_name(self, path):
        if _GUID_PATH.fullmatch(path):
            return path
        buffer = ctypes.create_unicode_buffer(1024)
        if not self._k32.GetVolumeNameForVolumeMountPointW(path, buffer, len(buffer)):
            raise ctypes.WinError(ctypes.get_last_error())
        return buffer.value

    def remove(self, path):
        if not self._k32.DeleteVolumeMountPointW(path):
            raise ctypes.WinError(ctypes.get_last_error())

    def restore(self, path, guid):
        if not self._k32.SetVolumeMountPointW(path, guid):
            raise ctypes.WinError(ctypes.get_last_error())


def _handle_disk_number(volume):
    buffer = ctypes.create_string_buffer(12)
    ok, count = volume._ioctl(0x002D1080, buffer)  # IOCTL_STORAGE_GET_DEVICE_NUMBER
    if not ok or count < 12:
        raise IsolationError("cannot establish volume physical-disk ownership")
    return struct.unpack_from("<III", buffer.raw)[1]


def isolate_disk(
    drive,
    image_path,
    *,
    target_provider=None,
    paths=None,
    volume_factory=pyimager.Win32Disk,
    disk_number_provider=_handle_disk_number,
    bridge_probe=wsl_bridge_running,
):
    if bridge_probe():
        raise IsolationError("Close WSL Bridge before isolating a flash target.")
    if target_provider is None:
        from guarded_restore import resolve_multi_flash_target

        target_provider = resolve_multi_flash_target
    paths = paths or WindowsMountPaths()
    store = ProtectedHardwareStore()

    def target():
        return target_provider(
            drive.disk_number,
            image_path,
            store,
            expected_size=drive.size_bytes,
            expected_serial=drive.hw_serial,
            expected_model=drive.model,
        )

    disk = target()
    if not disk.removable:
        raise IsolationError("Confirm isolation requires a removable target")
    handles = []
    removed = []
    try:
        volumes = {}
        for path in disk.mounted_volumes:
            guid = paths.volume_name(path)
            if not _GUID_PATH.fullmatch(guid):
                raise IsolationError("inventory did not resolve to a Windows volume GUID")
            entry = volumes.setdefault(guid.casefold(), (guid, []))
            if not _GUID_PATH.fullmatch(path):
                entry[1].append(path)
        for guid, _mounts in volumes.values():
            volume = volume_factory(guid.rstrip("\\"), write=True)
            handles.append(volume)
            if disk_number_provider(volume) != disk.disk_number:
                raise IsolationError("volume belongs to a different physical disk")
            if not volume.lock():
                raise IsolationError("volume is in use; close applications using the card")
            if not volume.dismount():
                raise IsolationError("Windows could not dismount the locked target volume")
        lease = ConfirmedDiskIsolation(disk, handles)
        lease.validate(target())
        for guid, mounts in volumes.values():
            for path in mounts:
                # Check again immediately before deleting each persistent binding.
                if paths.volume_name(path).casefold() != guid.casefold():
                    raise IsolationError("mount assignment changed during confirmation")
                paths.remove(path)
                removed.append((path, guid))
        lease.validate(target())
        return lease
    except Exception as exc:
        rollback_errors = []
        for path, guid in reversed(removed):
            try:
                paths.restore(path, guid)
            except OSError as rollback:
                rollback_errors.append(f"{path}: {rollback}")
        ConfirmedDiskIsolation(disk, handles).close()
        detail = (
            f"; mount restoration failed: {'; '.join(rollback_errors)}" if rollback_errors else ""
        )
        raise IsolationError(f"Target was not confirmed: {exc}{detail}") from exc


class ConfirmationManager:
    """Own pending isolation jobs and leases without Tk calls from worker threads."""

    def __init__(self, root, *, provider=isolate_disk):
        self._root = root
        self._provider = provider
        self._pool = ThreadPoolExecutor(max_workers=5, thread_name_prefix="OdinConfirm")
        self._pending = {}
        self._leases = {}
        self._closing = False

    @property
    def busy(self):
        return bool(self._pending)

    def get(self, idx):
        return self._leases.get(idx)

    def request(self, idx, drive, image_path, ready, failed):
        if self._closing or idx in self._pending:
            return
        self.release(idx)
        future = self._pool.submit(self._provider, drive, image_path)
        self._pending[idx] = future
        self._root.after(50, self._poll, idx, future, ready, failed)

    def _poll(self, idx, future, ready, failed):
        if self._pending.get(idx) is not future:
            return
        if not future.done():
            self._root.after(50, self._poll, idx, future, ready, failed)
            return
        del self._pending[idx]
        try:
            lease = future.result()
            self._leases[idx] = lease
            ready(lease)
        except Exception as exc:
            self.release(idx)
            failed(str(exc))

    @staticmethod
    def _discard(future):
        try:
            future.result().close()
        except Exception:
            pass

    def release(self, idx):
        future = self._pending.pop(idx, None)
        if future is not None:
            future.add_done_callback(self._discard)
            future.cancel()
        lease = self._leases.pop(idx, None)
        if lease is not None:
            lease.close()

    def close(self):
        self._closing = True
        for idx in set(self._pending) | set(self._leases):
            self.release(idx)
        self._pool.shutdown(wait=True, cancel_futures=True)
