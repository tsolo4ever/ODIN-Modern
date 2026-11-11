"""Owned Windows-to-WSL handoff for general used-block source capture.

Physical disks use wsl --mount; removable USB readers use usbipd. Disk
numbers and USB bus IDs are addresses only, never persistent identities.
"""

from __future__ import annotations

import base64
import ctypes
import json
import os
import re
import sys
import tempfile
import time
from collections.abc import Callable, Iterable
from pathlib import Path

from ext4_compact_capture import (
    Ext4CompactCaptureCancelled,
    Ext4CompactCaptureError,
    _run,
    _run_wsl,
    _run_wsl_script,
    _set_source_read_only,
    _wait_for_attached_disk,
    _wsl_disks,
    _wsl_mbr_sha256,
    _wsl_partitions,
)
from scripts import pyimager


class SourceHandoffError(Ext4CompactCaptureError):
    """The selected source cannot be handed over or returned safely."""


def _powershell(script: str) -> str:
    prefix = (
        "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; "
        "[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); "
    )
    encoded = base64.b64encode((prefix + script).encode("utf-16-le")).decode("ascii")
    executable = Path(os.environ.get("SystemRoot", "C:/Windows")) / (
        "System32/WindowsPowerShell/v1.0/powershell.exe"
    )
    return _run([str(executable), "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded])


def _windows_disks() -> list[dict]:
    # CIM avoids an unavailable Storage PowerShell module on some workstations.
    result = json.loads(
        _powershell("""
$disks=@(Get-CimInstance -Namespace root/Microsoft/Windows/Storage -ClassName MSFT_Disk)
$parts=@(Get-CimInstance -Namespace root/Microsoft/Windows/Storage -ClassName MSFT_Partition)
$rows=@(foreach($w in @(Get-CimInstance Win32_DiskDrive)) {
    $d=@($disks | Where-Object Number -eq $w.Index)
    if($d.Count -ne 1) { continue }
    [pscustomobject]@{
        number=[int]$w.Index; size=[long]$d[0].Size
        media=[string]$w.MediaType; bus=[int]$d[0].BusType
        pnp=[string]$w.PNPDeviceID; unique=[string]$d[0].UniqueId
        offline=[bool]$d[0].IsOffline; readonly=[bool]$d[0].IsReadOnly
        system=[bool]$d[0].IsSystem; boot=[bool]$d[0].IsBoot
        paths=@($parts | Where-Object DiskNumber -eq $w.Index | ForEach-Object { $_.AccessPaths })
    }
})
ConvertTo-Json -InputObject $rows -Depth 4 -Compress
""")
    )
    if not isinstance(result, list):
        raise SourceHandoffError("Windows returned an invalid disk inventory.")
    return result


def _same_disk(row: dict, identity: dict) -> bool:
    return all(
        str(row.get(key, "")).casefold() == str(identity.get(key, "")).casefold()
        for key in ("pnp", "unique", "size")
    )


def _find_disk(identity: dict, rows: list[dict] | None = None) -> dict:
    matches = [
        row for row in (_windows_disks() if rows is None else rows) if _same_disk(row, identity)
    ]
    if len(matches) != 1:
        raise SourceHandoffError(
            "The selected source is missing or its Windows identity is ambiguous."
        )
    return matches[0]


def _set_offline(identity: dict, offline: bool) -> None:
    current = _find_disk(identity)
    if current["system"] or current["boot"]:
        raise SourceHandoffError("Windows identifies the selected source as a system/boot disk.")
    # Recheck inside the same CIM operation; never mutate a recycled disk number.
    token = base64.b64encode(json.dumps(current).encode("utf-8")).decode("ascii")
    method = "Offline" if offline else "Online"
    _powershell(f"""
$i=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{token}')) | ConvertFrom-Json
$d=@(Get-CimInstance -Namespace root/Microsoft/Windows/Storage -ClassName MSFT_Disk |
    Where-Object {{ $_.Number -eq $i.number -and $_.UniqueId -eq $i.unique -and $_.Size -eq $i.size }})
$w=@(Get-CimInstance Win32_DiskDrive |
    Where-Object {{ $_.Index -eq $i.number -and $_.PNPDeviceID -eq $i.pnp }})
if($d.Count -ne 1 -or $w.Count -ne 1 -or $d[0].IsSystem -or $d[0].IsBoot) {{
    throw 'Source identity changed before disk state transition.'
}}
$r=Invoke-CimMethod -InputObject $d[0] -MethodName {method}
if($r.ReturnValue -ne 0) {{ throw ('{method} failed: ' + $r.ReturnValue + ' ' + $r.ExtendedStatus) }}
""")
    if bool(_find_disk(identity)["offline"]) != offline:
        raise SourceHandoffError(f"Windows did not confirm the source {method.lower()} state.")


def _pnp_ancestors(instance: str) -> set[str]:
    api = ctypes.WinDLL("cfgmgr32")
    ulong = ctypes.c_ulong
    api.CM_Locate_DevNodeW.argtypes = [ctypes.POINTER(ulong), ctypes.c_wchar_p, ulong]
    api.CM_Get_Parent.argtypes = [ctypes.POINTER(ulong), ulong, ulong]
    api.CM_Get_Device_IDW.argtypes = [ulong, ctypes.c_wchar_p, ulong, ulong]
    for name in ("CM_Locate_DevNodeW", "CM_Get_Parent", "CM_Get_Device_IDW"):
        getattr(api, name).restype = ulong
    node = ulong()
    if api.CM_Locate_DevNodeW(ctypes.byref(node), instance, 0):
        raise SourceHandoffError("Cannot resolve the selected disk's USB ancestry.")
    result: set[str] = set()
    for _ in range(64):
        buffer = ctypes.create_unicode_buffer(1024)
        if api.CM_Get_Device_IDW(node, buffer, len(buffer), 0):
            raise SourceHandoffError("Cannot read the selected disk's PnP identity.")
        result.add(buffer.value.casefold())
        parent = ulong()
        status = api.CM_Get_Parent(ctypes.byref(parent), node, 0)
        if status == 0x0D:  # CR_NO_SUCH_DEVNODE: reached the root.
            return result
        if status:
            raise SourceHandoffError(f"Cannot read USB parent identity (ConfigMgr {status}).")
        node = parent
    raise SourceHandoffError("USB ancestry did not terminate.")


def _usb_devices() -> list[dict]:
    result = json.loads(_run(["usbipd", "state"]))
    rows = result.get("Devices")
    if not isinstance(rows, list):
        raise SourceHandoffError("usbipd did not return a valid device inventory.")
    return rows


def _usb_current(instance: str) -> dict:
    matches = [
        row
        for row in _usb_devices()
        if str(row.get("InstanceId", "")).casefold() == instance.casefold()
    ]
    if len(matches) != 1:
        raise SourceHandoffError("The selected USB reader is missing or ambiguous.")
    return matches[0]


def _reader_for_disk(identity: dict, rows: list[dict]) -> dict:
    ancestors = _pnp_ancestors(identity["pnp"])
    matches = [
        row
        for row in _usb_devices()
        if str(row.get("InstanceId", "")).casefold() in ancestors and row.get("BusId")
    ]
    if len(matches) != 1:
        raise SourceHandoffError("Cannot uniquely match this Windows disk to a usbipd reader.")
    reader = matches[0]
    instance = reader["InstanceId"].casefold()
    for row in rows:
        if not _same_disk(row, identity) and instance in _pnp_ancestors(row["pnp"]):
            raise SourceHandoffError(
                "This USB reader exposes multiple disks; select a single-disk reader."
            )
    if reader.get("ClientIPAddress"):
        raise SourceHandoffError("The USB reader is already attached. Return it to Windows first.")
    return reader


def _volume_for_path(path: Path) -> str:
    # Resolve junctions before asking Windows, including destinations not yet created.
    resolved = Path(os.path.realpath(path))
    while not resolved.exists() and resolved != resolved.parent:
        resolved = resolved.parent
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetVolumePathNameW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_ulong]
    kernel.GetVolumeNameForVolumeMountPointW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_ulong,
    ]
    kernel.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
    buffer = ctypes.create_unicode_buffer(32768)
    if not kernel.GetVolumePathNameW(str(resolved), buffer, len(buffer)):
        raise ctypes.WinError(ctypes.get_last_error())
    if kernel.GetDriveTypeW(buffer.value) == 4:  # Remote share, not a local source volume.
        return ""
    volume = ctypes.create_unicode_buffer(1024)
    if not kernel.GetVolumeNameForVolumeMountPointW(buffer.value, volume, len(volume)):
        raise ctypes.WinError(ctypes.get_last_error())
    return volume.value.casefold()


def _check_paths(identity: dict, paths: Iterable[Path]) -> None:
    source_volumes = {str(path).casefold() for path in identity["paths"] if path}
    for path in paths:
        if _volume_for_path(path) in source_volumes:
            raise SourceHandoffError(
                f"The output, staging area, or running application is on the selected source: {path}"
            )


def _lock_volumes(identity: dict) -> list:
    volumes = sorted(
        {
            str(p).rstrip("\\")
            for p in identity["paths"]
            if p and str(p).casefold().startswith("\\\\?\\volume{")
        }
    )
    handles = []
    try:
        for path in volumes:
            handle = pyimager.Win32Disk(path, write=True)
            handles.append(handle)
            if not handle.lock():
                raise SourceHandoffError(
                    f"Source volume is busy: {path}. Close applications using it."
                )
        # Do not dismount even the first volume until all locks have succeeded.
        for handle in handles:
            if not handle.dismount():
                raise SourceHandoffError("Windows could not dismount a locked source volume.")
        return handles
    except BaseException:
        _release_volumes(handles)
        raise


def _release_volumes(handles: list) -> None:
    for handle in reversed(handles):
        try:
            handle.unlock()
        finally:
            handle.close()
    handles.clear()


class SourceSession:
    """Own only the temporary state changed by this discovery/capture attempt."""

    def __init__(
        self,
        disk_number: int,
        *,
        expected_source: dict | None = None,
        paths: Iterable[Path] = (),
        should_cancel: Callable[[], bool] = lambda: False,
        on_log: Callable[[str], None] = lambda _message: None,
    ):
        rows = _windows_disks()
        if expected_source is not None:
            current = _find_disk(expected_source, rows)
        else:
            matches = [row for row in rows if row["number"] == disk_number]
            if len(matches) != 1:
                raise SourceHandoffError("The selected Windows disk is missing or ambiguous.")
            current = matches[0]
        if not current["pnp"] or not current["unique"]:
            raise SourceHandoffError("Windows did not provide a stable source identity.")
        if current["system"] or current["boot"]:
            raise SourceHandoffError(
                "General used-block handoff cannot release a Windows system/boot disk."
            )
        self.identity = {key: current[key] for key in ("pnp", "unique", "size")}
        self.expected_mbr = (expected_source or {}).get("mbr_sha256")
        self.expected_layout = (expected_source or {}).get("layout")
        self.original = current
        self.disk_number = current["number"]
        self.physical_path = rf"\\.\PhysicalDrive{self.disk_number}"
        self.route = (
            "usbipd"
            if current["bus"] == 7 and "removable" in current["media"].casefold()
            else "physical"
        )
        self.reader = _reader_for_disk(current, rows) if self.route == "usbipd" else None
        _check_paths(
            current, (*paths, Path(tempfile.gettempdir()), Path(sys.executable), Path(__file__))
        )
        self.should_cancel = should_cancel
        self.on_log = on_log
        self.device = ""
        self.mount_attempted = False
        self.attached = False
        self.offline_attempted = False
        self.bind_attempted = False
        self.closed = False
        self.before: dict[str, int] = {}

    def _cancelled(self) -> None:
        if self.should_cancel():
            raise Ext4CompactCaptureCancelled("Used-block source handoff was cancelled.")

    def check_layout(self, partitions: Iterable) -> None:
        geometry = [
            [p.number, p.type_code, p.start_lba, p.sector_count, p.table_lba]
            for p in sorted(partitions, key=lambda item: item.number)
        ]
        if self.expected_layout is not None and geometry != self.expected_layout:
            raise SourceHandoffError("Source partition layout changed since preflight.")
        self.identity["layout"] = geometry

    def attach(self, mbr_sha256: str) -> str:
        self._cancelled()
        if self.expected_mbr and mbr_sha256 != self.expected_mbr:
            raise SourceHandoffError("Source boot/layout identity changed since preflight.")
        self.identity["mbr_sha256"] = mbr_sha256
        self.before = _wsl_disks()
        for device, size in self.before.items():
            if size == self.identity["size"] and _wsl_mbr_sha256(device) == mbr_sha256:
                raise SourceHandoffError(
                    "A matching source is already in WSL; ODIN will not detach it."
                )
        current = _find_disk(self.identity)
        if current["number"] != self.disk_number or current["offline"] != self.original["offline"]:
            raise SourceHandoffError(
                "Source availability changed before handoff. Refresh the source list."
            )
        handles = _lock_volumes(current)
        try:
            self._cancelled()
            if self.route == "physical" and not self.original["offline"]:
                self.offline_attempted = True
                _set_offline(self.identity, True)
        finally:
            _release_volumes(handles)
        self._cancelled()
        self.on_log(
            f"Attaching source through {'usbipd (USB reader)' if self.reader else 'WSL physical disk'}."
        )
        if self.reader:
            reader = _usb_current(self.reader["InstanceId"])
            if reader.get("ClientIPAddress"):
                raise SourceHandoffError("Another session attached the selected USB reader.")
            if not reader.get("PersistedGuid"):
                self.bind_attempted = True
                _run(["usbipd", "bind", "--busid", reader["BusId"]])
                reader = _usb_current(self.reader["InstanceId"])
            self.mount_attempted = True
            _run(["usbipd", "attach", "--wsl", "--busid", reader["BusId"]])
        else:
            self.mount_attempted = True
            _run(["wsl.exe", "--mount", self.physical_path, "--bare"])
        self.attached = True
        self.device = _wait_for_attached_disk(self.before, self.identity["size"], mbr_sha256)
        _set_source_read_only(self.device)
        for partition in _wsl_partitions(self.device, 512).values():
            if partition.mounted:
                raise SourceHandoffError(
                    "A source partition mounted automatically in WSL; capture stopped."
                )
            _set_source_read_only(partition.path)
        self._cancelled()
        return self.device

    def verify(self) -> None:
        if (
            _run_wsl(["blockdev", "--getro", self.device]).strip() != "1"
            or int(_run_wsl(["blockdev", "--getsize64", self.device]).strip())
            != self.identity["size"]
            or _wsl_mbr_sha256(self.device) != self.identity["mbr_sha256"]
        ):
            raise SourceHandoffError("The WSL source identity or read-only state changed.")
        if any(part.mounted for part in _wsl_partitions(self.device, 512).values()):
            raise SourceHandoffError("A source partition became mounted during capture.")

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        errors = []
        detached = not self.mount_attempted
        try:
            if self.reader and (self.mount_attempted or self.bind_attempted):
                reader = _usb_current(self.reader["InstanceId"])
                if self.mount_attempted and reader.get("ClientIPAddress"):
                    _run(["usbipd", "detach", "--busid", reader["BusId"]])
                detached = True
                if self.bind_attempted:
                    reader = _usb_current(self.reader["InstanceId"])
                    if reader.get("PersistedGuid"):
                        _run(["usbipd", "unbind", "--guid", reader["PersistedGuid"]])
            elif self.mount_attempted:
                # A failed attach may still have changed WSL; inspect before deciding.
                if self.attached or any(
                    path not in self.before
                    and size == self.identity["size"]
                    and _wsl_mbr_sha256(path) == self.identity["mbr_sha256"]
                    for path, size in _wsl_disks().items()
                ):
                    _run(["wsl.exe", "--unmount", self.physical_path])
                detached = True
        except Exception as exc:
            errors.append(str(exc))
        try:
            if detached and self.offline_attempted and not self.original["offline"]:
                _set_offline(self.identity, False)
            if detached and (self.mount_attempted or self.offline_attempted):
                deadline = time.monotonic() + 15
                while True:
                    try:
                        current = _find_disk(self.identity)
                        prior_paths = {str(p).casefold() for p in self.original["paths"] if p}
                        current_paths = {str(p).casefold() for p in current["paths"] if p}
                        if (
                            current["offline"] != self.original["offline"]
                            or current["readonly"] != self.original["readonly"]
                            or not prior_paths.issubset(current_paths)
                        ):
                            raise SourceHandoffError(
                                "Original Windows source availability has not returned."
                            )
                        self.disk_number = current["number"]
                        break
                    except SourceHandoffError:
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(0.25)
        except Exception as exc:
            errors.append(str(exc))
        if errors:
            original_error = sys.exception()
            context = f" Operation error: {original_error}." if original_error else ""
            raise SourceHandoffError(
                "Source cleanup failed; check Windows/WSL before retrying: "
                + "; ".join(errors)
                + context
            )
        if self.mount_attempted:
            self.on_log(f"Source returned to Windows as Disk {self.disk_number}.")


def hash_source_ranges(
    device: str,
    ranges: Iterable[tuple[int, int]],
    *,
    should_cancel: Callable[[], bool],
    on_progress: Callable[[int], None],
) -> list[dict]:
    """Stream exact ranges in WSL, including boot prefixes larger than 64 MiB."""
    specs = list(ranges)
    total = sum(length for _, length in specs)
    done = 0
    records = []
    for offset, length in specs:
        if should_cancel():
            raise Ext4CompactCaptureCancelled("Used-block source verification was cancelled.")
        if offset < 0 or length <= 0:
            raise SourceHandoffError("Source hash range has invalid bounds.")
        result = _run_wsl_script(
            "set -e\n"
            "work=$(mktemp -d /tmp/odinm-range.XXXXXX)\n"
            "trap 'kill ${hash_pid:-} ${count_pid:-} 2>/dev/null || :; rm -rf \"$work\"' EXIT HUP INT TERM\n"
            'mkfifo "$work/hash" "$work/count"\n'
            'sha256sum < "$work/hash" > "$work/digest" & hash_pid=$!\n'
            'wc -c < "$work/count" > "$work/size" & count_pid=$!\n'
            '(dd if="$1" bs=4M skip="$2" count="$3" iflag=skip_bytes,count_bytes status=none '
            '&& echo ok > "$work/status") | tee "$work/count" > "$work/hash"\n'
            'wait "$hash_pid"; wait "$count_pid"\n'
            'test -f "$work/status"\n'
            'test "$(cat "$work/size")" = "$3"\n'
            'cat "$work/digest"\n',
            args=[device, str(offset), str(length)],
            should_cancel=should_cancel,
        )
        digest = result.split()[0] if result.split() else ""
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise SourceHandoffError("WSL returned an invalid source-range digest.")
        records.append({"offset": offset, "length": length, "sha256": digest})
        done += length
        on_progress(int(100 * done / max(total, 1)))
    return records
