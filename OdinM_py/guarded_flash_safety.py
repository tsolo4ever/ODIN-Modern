"""Fail-closed inventory and write gates for Guarded Single Flash mode."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from drive_manager import get_path_disk_number, is_disk_removable


CREATE_NO_WINDOW = 0x08000000
STORE_SCHEMA_VERSION = 1
STORE_FILE = "guarded_protected_hardware.json"
RECOVERY_GPT_TYPE = "{de94bba4-06d1-4d40-a16a-bfd50179d6ac}"
VIRTUAL_BUS_TYPES = {14, 15, 16}
UNUSABLE_IDENTIFIERS = {
    "",
    "0",
    "00000000",
    "none",
    "null",
    "unknown",
    "default string",
    "to be filled by o.e.m.",
}


class StorageInventoryError(RuntimeError):
    """Windows storage inventory could not be proven complete."""


class ProtectedStoreError(RuntimeError):
    """The protected-hardware file could not be trusted."""


@dataclass(frozen=True)
class DiskIdentity:
    disk_number: int
    unique_id: str
    serial: str
    model: str
    manufacturer: str
    size_bytes: int
    bus_type: int
    bus_name: str
    device_path: str
    location: str
    mounted_volumes: tuple[str, ...] = ()
    removable: bool = False
    system_reasons: tuple[str, ...] = ()
    classification_complete: bool = True

    @property
    def raw_device_path(self) -> str:
        return rf"\\.\PhysicalDrive{self.disk_number}"

    @property
    def stable_key(self) -> str | None:
        unique_id = _usable_identifier(self.unique_id)
        if unique_id:
            return f"unique:{unique_id}"
        serial = _usable_identifier(self.serial)
        model = _clean(self.model)
        if serial and model and self.size_bytes > 0:
            return f"serial:{serial}|model:{model}|size:{self.size_bytes}"
        return None

    @property
    def descriptor_key(self) -> str:
        descriptor = "|".join(
            (
                _clean(self.manufacturer),
                _clean(self.model),
                str(self.size_bytes),
                str(self.bus_type),
                _clean(self.location),
                _clean(self.device_path),
            )
        )
        return "descriptor:" + hashlib.sha256(descriptor.encode("utf-8")).hexdigest()

    @property
    def is_virtual(self) -> bool:
        text = f"{self.bus_name} {self.model} {self.device_path}".casefold()
        return self.bus_type in VIRTUAL_BUS_TYPES or "virtual" in text

    @property
    def description(self) -> str:
        volumes = ", ".join(self.mounted_volumes) or "no mounted volumes"
        name = " ".join(part for part in (self.manufacturer, self.model) if part).strip()
        return (
            f"Disk {self.disk_number}: {name or 'unknown model'}, "
            f"{self.size_bytes} bytes, {self.bus_name or 'unknown bus'}, {volumes}"
        )

    @property
    def revalidation_tuple(self) -> tuple[Any, ...]:
        return (
            self.disk_number,
            self.stable_key,
            self.descriptor_key,
            self.size_bytes,
            self.bus_type,
            self.device_path.casefold(),
            self.location.casefold(),
            self.mounted_volumes,
            self.removable,
            self.system_reasons,
            self.classification_complete,
        )


@dataclass(frozen=True)
class ProtectedRecord:
    record_id: str
    stable_key: str | None
    descriptor_key: str
    first_seen_utc: str
    last_seen_utc: str
    description: str
    disk_snapshot: dict[str, Any]

    @classmethod
    def from_disk(cls, disk: DiskIdentity, scanned_at: str) -> ProtectedRecord:
        record_id = disk.stable_key or disk.descriptor_key
        return cls(
            record_id=record_id,
            stable_key=disk.stable_key,
            descriptor_key=disk.descriptor_key,
            first_seen_utc=scanned_at,
            last_seen_utc=scanned_at,
            description=disk.description,
            disk_snapshot=asdict(disk),
        )


@dataclass(frozen=True)
class EligibilityDecision:
    disk: DiskIdentity
    eligible: bool
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class BaselineScanResult:
    scanned_at_utc: str
    disks: tuple[DiskIdentity, ...]
    added_record_ids: tuple[str, ...]
    protected_records: tuple[ProtectedRecord, ...]


@dataclass
class ProtectedHardwareStore:
    path: Path = field(default_factory=lambda: default_store_path())

    def load(self) -> list[ProtectedRecord]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProtectedStoreError(f"cannot read protected hardware: {exc}") from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != STORE_SCHEMA_VERSION:
            raise ProtectedStoreError("protected hardware has an unsupported schema")
        raw_records = payload.get("records")
        if not isinstance(raw_records, list):
            raise ProtectedStoreError("protected hardware records are invalid")
        records = []
        try:
            for item in raw_records:
                if not isinstance(item, dict):
                    raise TypeError("record is not an object")
                record = ProtectedRecord(**item)
                if not record.record_id or not record.descriptor_key:
                    raise ValueError("record identity is empty")
                records.append(record)
        except (TypeError, ValueError) as exc:
            raise ProtectedStoreError(f"protected hardware record is invalid: {exc}") from exc
        if len({record.record_id for record in records}) != len(records):
            raise ProtectedStoreError("protected hardware contains duplicate records")
        return records

    def save(self, records: Iterable[ProtectedRecord]) -> None:
        ordered = sorted(records, key=lambda record: record.record_id)
        payload = {
            "schema_version": STORE_SCHEMA_VERSION,
            "records": [asdict(record) for record in ordered],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                newline="\n",
                dir=self.path.parent,
                prefix=self.path.name + ".",
                suffix=".tmp",
                delete=False,
            ) as stream:
                json.dump(payload, stream, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
                temporary_path = Path(stream.name)
            os.replace(temporary_path, self.path)
        except OSError as exc:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise ProtectedStoreError(f"cannot save protected hardware: {exc}") from exc


def default_store_path() -> Path:
    base = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    return base / STORE_FILE


def _clean(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _usable_identifier(value: Any) -> str | None:
    cleaned = _clean(value).strip("{}")
    compact = cleaned.replace("-", "").replace("_", "").replace(" ", "")
    if cleaned in UNUSABLE_IDENTIFIERS or not compact or set(compact) == {"0"}:
        return None
    return cleaned


def _powershell_executable() -> str | None:
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    windows_powershell = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if windows_powershell.is_file():
        return str(windows_powershell)
    return shutil.which("pwsh.exe") or shutil.which("pwsh")


_INVENTORY_SCRIPT = r"""
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[System.Text.Encoding]::UTF8
$ns='root/Microsoft/Windows/Storage'
$disks=@(Get-CimInstance -Namespace $ns -ClassName MSFT_Disk)
$parts=@(Get-CimInstance -Namespace $ns -ClassName MSFT_Partition)
$systemDrive=([string]$env:SystemDrive).TrimEnd(':').ToUpperInvariant()
$pageDrives=@(Get-CimInstance -ClassName Win32_PageFileUsage | ForEach-Object {
    ([IO.Path]::GetPathRoot([string]$_.Name)).TrimEnd('\').TrimEnd(':').ToUpperInvariant()
})
$crashDrives=@()
$crash=Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\CrashControl' -ErrorAction SilentlyContinue
if($null -ne $crash -and $crash.DedicatedDumpFile){
    $dump=[Environment]::ExpandEnvironmentVariables([string]$crash.DedicatedDumpFile)
    $root=[IO.Path]::GetPathRoot($dump)
    if($root){$crashDrives+=($root.TrimEnd('\').TrimEnd(':').ToUpperInvariant())}
}
$items=foreach($disk in $disks){
    $diskParts=@($parts | Where-Object {$_.DiskNumber -eq $disk.Number})
    $reasons=[System.Collections.Generic.List[string]]::new()
    if($disk.IsBoot){$reasons.Add('Windows boot disk')}
    if($disk.IsSystem){$reasons.Add('Windows system disk')}
    foreach($part in $diskParts){
        if($part.IsBoot){$reasons.Add('Windows boot partition')}
        if($part.IsSystem){$reasons.Add('Windows system partition')}
        if(([string]$part.GptType).ToLowerInvariant() -eq '{de94bba4-06d1-4d40-a16a-bfd50179d6ac}' -or $part.MbrType -eq 39){$reasons.Add('Windows recovery partition')}
        $letter=([string]$part.DriveLetter).TrimEnd(':').ToUpperInvariant()
        if($letter -and $letter -eq $systemDrive){$reasons.Add('active Windows volume')}
        if($letter -and $pageDrives -contains $letter){$reasons.Add('page-file volume')}
        if($letter -and $crashDrives -contains $letter){$reasons.Add('crash-dump volume')}
    }
    $volumes=@($diskParts | ForEach-Object {$_.AccessPaths} | Where-Object {$_} | Sort-Object -Unique)
    [pscustomobject]@{
        Number=$disk.Number; UniqueId=$disk.UniqueId; SerialNumber=$disk.SerialNumber
        Model=$disk.Model; Manufacturer=$disk.Manufacturer; FriendlyName=$disk.FriendlyName
        Size=$disk.Size; BusType=$disk.BusType; Path=$disk.Path; Location=$disk.Location
        Volumes=$volumes; SystemReasons=@($reasons | Sort-Object -Unique)
    }
}
ConvertTo-Json -InputObject @($items) -Depth 6 -Compress
""".strip()


BUS_NAMES = {
    0: "Unknown", 1: "SCSI", 2: "ATAPI", 3: "ATA", 4: "IEEE 1394",
    5: "SSA", 6: "Fibre Channel", 7: "USB", 8: "RAID", 9: "iSCSI",
    10: "SAS", 11: "SATA", 12: "SD", 13: "MMC", 14: "Virtual",
    15: "File-backed virtual", 16: "Storage Spaces", 17: "NVMe",
}


def query_windows_storage_inventory(
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    removable_provider: Callable[[int], bool] = is_disk_removable,
    powershell: str | None = None,
    timeout_seconds: float = 20.0,
) -> list[DiskIdentity]:
    """Return a complete Windows disk/system-storage snapshot or raise."""
    executable = powershell or _powershell_executable()
    if os.name != "nt" or not executable:
        raise StorageInventoryError("Windows PowerShell storage inventory is unavailable")
    try:
        completed = runner(
            [executable, "-NoProfile", "-NonInteractive", "-Command", _INVENTORY_SCRIPT],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            creationflags=CREATE_NO_WINDOW,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise StorageInventoryError(f"Windows storage inventory failed: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "unknown error").strip()
        raise StorageInventoryError(f"Windows storage inventory failed: {detail}")
    try:
        payload = json.loads(completed.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise StorageInventoryError("Windows storage inventory returned invalid JSON") from exc
    if not isinstance(payload, list):
        raise StorageInventoryError("Windows storage inventory was incomplete")
    disks = []
    try:
        for item in payload:
            number = int(item["Number"])
            size = int(item["Size"])
            bus_type = int(item.get("BusType", 0) or 0)
            volumes = tuple(sorted(str(value) for value in (item.get("Volumes") or []) if value))
            reasons = tuple(sorted(str(value) for value in (item.get("SystemReasons") or []) if value))
            model = str(item.get("Model") or item.get("FriendlyName") or "").strip()
            if number < 0 or size <= 0:
                raise ValueError("invalid disk number or capacity")
            disks.append(
                DiskIdentity(
                    disk_number=number,
                    unique_id=str(item.get("UniqueId") or "").strip(),
                    serial=str(item.get("SerialNumber") or "").strip(),
                    model=model,
                    manufacturer=str(item.get("Manufacturer") or "").strip(),
                    size_bytes=size,
                    bus_type=bus_type,
                    bus_name=BUS_NAMES.get(bus_type, f"Bus {bus_type}"),
                    device_path=str(item.get("Path") or "").strip(),
                    location=str(item.get("Location") or "").strip(),
                    mounted_volumes=volumes,
                    removable=bool(removable_provider(number)),
                    system_reasons=reasons,
                )
            )
    except (KeyError, TypeError, ValueError) as exc:
        raise StorageInventoryError(f"Windows storage inventory contained invalid data: {exc}") from exc
    if len({disk.disk_number for disk in disks}) != len(disks):
        raise StorageInventoryError("Windows storage inventory returned duplicate disk numbers")
    return sorted(disks, key=lambda disk: disk.disk_number)


def scan_system_hardware(
    store: ProtectedHardwareStore,
    *,
    inventory_provider: Callable[[], list[DiskIdentity]] = query_windows_storage_inventory,
    now_provider: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> BaselineScanResult:
    disks = tuple(inventory_provider())
    if not disks:
        raise StorageInventoryError("Windows storage inventory returned no physical disks")
    scanned_at = now_provider().astimezone(UTC).isoformat()
    existing = {record.record_id: record for record in store.load()}
    added = []
    for disk in disks:
        candidate = ProtectedRecord.from_disk(disk, scanned_at)
        previous = existing.get(candidate.record_id)
        if previous is None:
            existing[candidate.record_id] = candidate
            added.append(candidate.record_id)
        else:
            existing[candidate.record_id] = ProtectedRecord(
                record_id=previous.record_id,
                stable_key=candidate.stable_key or previous.stable_key,
                descriptor_key=candidate.descriptor_key,
                first_seen_utc=previous.first_seen_utc,
                last_seen_utc=scanned_at,
                description=candidate.description,
                disk_snapshot=candidate.disk_snapshot,
            )
    records = tuple(sorted(existing.values(), key=lambda record: record.record_id))
    store.save(records)
    return BaselineScanResult(scanned_at, disks, tuple(added), records)


def _protected_match(disk: DiskIdentity, records: Iterable[ProtectedRecord]) -> bool:
    return any(
        (disk.stable_key is not None and record.stable_key == disk.stable_key)
        or record.descriptor_key == disk.descriptor_key
        for record in records
    )


def evaluate_inventory(
    disks: Iterable[DiskIdentity],
    records: Iterable[ProtectedRecord],
    *,
    image_disk_number: int = -1,
) -> list[EligibilityDecision]:
    disk_list = list(disks)
    protected = tuple(records)
    stable_counts = Counter(disk.stable_key for disk in disk_list if disk.stable_key)
    decisions = []
    for disk in disk_list:
        reasons = []
        if not disk.classification_complete:
            reasons.append("Windows system-storage classification is incomplete")
        if disk.system_reasons:
            reasons.append("protected Windows storage: " + ", ".join(disk.system_reasons))
        if disk.is_virtual or not disk.device_path:
            reasons.append("target is virtual or cannot be proven as a local physical device")
        if disk.stable_key is None:
            reasons.append("stable hardware identity is missing")
        elif stable_counts[disk.stable_key] != 1:
            reasons.append("stable hardware identity is ambiguous")
        if _protected_match(disk, protected):
            reasons.append("hardware is in the protected system baseline")
        if image_disk_number >= 0 and disk.disk_number == image_disk_number:
            reasons.append("selected image is stored on this disk")
        decisions.append(EligibilityDecision(disk, not reasons, tuple(reasons)))
    return decisions


def list_guarded_candidates(
    store: ProtectedHardwareStore,
    *,
    image_path: str = "",
    inventory_provider: Callable[[], list[DiskIdentity]] = query_windows_storage_inventory,
    image_disk_provider: Callable[[str], int] = get_path_disk_number,
) -> list[EligibilityDecision]:
    records = store.load()
    image_disk = image_disk_provider(image_path) if image_path else -1
    return evaluate_inventory(inventory_provider(), records, image_disk_number=image_disk)


def revalidate_target(
    expected: DiskIdentity,
    store: ProtectedHardwareStore,
    *,
    image_path: str,
    inventory_provider: Callable[[], list[DiskIdentity]] = query_windows_storage_inventory,
    image_disk_provider: Callable[[str], int] = get_path_disk_number,
) -> EligibilityDecision:
    disks = inventory_provider()
    matches = [disk for disk in disks if disk.disk_number == expected.disk_number]
    if len(matches) != 1:
        return EligibilityDecision(expected, False, ("selected disk is missing or duplicated",))
    current = matches[0]
    decision = evaluate_inventory(
        disks,
        store.load(),
        image_disk_number=image_disk_provider(image_path),
    )[disks.index(current)]
    reasons = list(decision.reasons)
    if current.revalidation_tuple != expected.revalidation_tuple:
        reasons.insert(0, "selected disk identity or classification changed")
    return EligibilityDecision(current, not reasons, tuple(reasons))


def remove_protected_record(
    store: ProtectedHardwareStore,
    record_id: str,
    *,
    inventory_provider: Callable[[], list[DiskIdentity]] = query_windows_storage_inventory,
) -> None:
    records = store.load()
    selected = next((record for record in records if record.record_id == record_id), None)
    if selected is None:
        raise ProtectedStoreError("protected hardware record was not found")
    for disk in inventory_provider():
        if _protected_match(disk, (selected,)) and disk.system_reasons:
            raise ProtectedStoreError("cannot remove hardware that currently hosts Windows storage")
    store.save(record for record in records if record.record_id != record_id)
