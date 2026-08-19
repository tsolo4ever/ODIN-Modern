"""Validated MBR/EBR layout support for bounded raw disk images."""

from __future__ import annotations

import hashlib
import json
import os
import re
import struct
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import BinaryIO


MBR_SIGNATURE = b"\x55\xaa"
PARTITION_TABLE_OFFSET = 446
PARTITION_ENTRY_SIZE = 16
EXTENDED_TYPES = {0x05, 0x0F, 0x85}
GPT_PROTECTIVE_TYPE = 0xEE
MAX_LOGICAL_PARTITIONS = 128
MANIFEST_SCHEMA = 1
MANIFEST_FORMAT = "odinm-bounded-raw"
EXT4_MANIFEST_SCHEMA = 2
EXT4_MANIFEST_FORMAT = "odinm-ext4-compact"
EXT4_PARTITION_TYPE = 0x83
SWAP_PARTITION_TYPE = 0x82
CLEANUP_METADATA_BEGIN = "# ODINM-CLEANUP-METADATA-BEGIN"
CLEANUP_METADATA_END = "# ODINM-CLEANUP-METADATA-END"
CLEANUP_METADATA_FORMAT = "odinm-cleanup-installer-v1"
CLEANUP_INSTALL_CONTRACT = "sh-install-root-v1"
CLEANUP_INSTALLED_PATH = "/usr/local/sbin/roulette-profile-cleanup"
CLEANUP_SCHEDULE_PATH = "/etc/cron.d/roulette-profile-cleanup"
CURRENT_LINUX_ID = "ubuntu"
CURRENT_LINUX_VERSION = "12.04"
MAX_CLEANUP_INSTALLER_BYTES = 1 << 20
CLEANUP_KEY = re.compile(r"^[a-z][a-z0-9_]*$")
CLEANUP_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
LINUX_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,31}$")
LINUX_VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+)*$")
CLEANUP_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,127}$")


class CompactImageError(ValueError):
    """The disk layout cannot be captured safely as a bounded raw image."""


@dataclass(frozen=True)
class PartitionExtent:
    number: int
    kind: str
    type_code: int
    start_lba: int
    sector_count: int
    table_lba: int
    bootable: bool = False

    @property
    def end_lba(self) -> int:
        return self.start_lba + self.sector_count

    def manifest_dict(self) -> dict:
        data = asdict(self)
        data["type"] = f"0x{self.type_code:02X}"
        return data


@dataclass(frozen=True)
class CompactLayout:
    disk_size: int
    sector_size: int
    disk_signature: str
    partitions: tuple[PartitionExtent, ...]
    capture_bytes: int
    extended_start_lba: int | None = None
    extended_sector_count: int | None = None

    @property
    def saved_bytes(self) -> int:
        return self.disk_size - self.capture_bytes

    def manifest_dict(self) -> dict:
        return {
            "partition_style": "MBR",
            "extended_start_lba": self.extended_start_lba,
            "extended_sector_count": self.extended_sector_count,
            "partitions": [item.manifest_dict() for item in self.partitions],
        }


@dataclass(frozen=True)
class Ext4FilesystemRecord:
    partition_number: int
    uuid: str
    block_size: int
    original_start_lba: int
    original_sector_count: int
    compact_sector_count: int
    minimum_blocks: int
    buffer_bytes: int
    prefix_bytes: int

    def manifest_dict(self) -> dict:
        data = asdict(self)
        data["type"] = "ext4"
        return data


@dataclass(frozen=True)
class OmittedSwapRecord:
    partition_number: int
    uuid: str
    original_start_lba: int
    sector_count: int

    def manifest_dict(self) -> dict:
        data = asdict(self)
        data["type"] = "linux-swap"
        return data


@dataclass(frozen=True)
class ExpansionRecord:
    root_uuid: str
    swap_uuid: str
    swap_sector_count: int
    alignment_sectors: int
    minimum_target_bytes: int
    installed_script_sha256: str

    def manifest_dict(self) -> dict:
        data = asdict(self)
        data["armed"] = True
        return data


@dataclass(frozen=True)
class CleanupRecord:
    installer_id: str
    source_filename: str
    source_sha256: str
    linux_id: str
    linux_versions: tuple[str, ...]
    install_contract: str
    installed_path: str
    schedule_path: str

    def manifest_dict(self) -> dict:
        data = asdict(self)
        data["linux_versions"] = list(self.linux_versions)
        data["format"] = "odinm-cleanup-installer-v1"
        data["installed"] = True
        return data


def parse_cleanup_installer_record(path: str | os.PathLike[str]) -> CleanupRecord:
    source_path = Path(path)
    try:
        payload = source_path.read_bytes()
    except OSError as exc:
        raise CompactImageError(f"Cleanup installer is unreadable: {exc}") from exc
    if not source_path.is_file() or not payload or len(payload) > MAX_CLEANUP_INSTALLER_BYTES:
        raise CompactImageError(
            "Cleanup installer must be a nonempty file no larger than 1 MiB."
        )
    if b"\x00" in payload:
        raise CompactImageError("Cleanup installer contains binary data.")
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise CompactImageError("Cleanup installer must be UTF-8 text.") from exc

    begins = [index for index, line in enumerate(lines) if line == CLEANUP_METADATA_BEGIN]
    ends = [index for index, line in enumerate(lines) if line == CLEANUP_METADATA_END]
    if len(begins) != 1 or len(ends) != 1 or ends[0] <= begins[0] + 1:
        raise CompactImageError(
            "Cleanup installer must contain one complete ODINM metadata block."
        )

    values: dict[str, str] = {}
    for line in lines[begins[0] + 1:ends[0]]:
        if not line.startswith("# ") or "=" not in line:
            raise CompactImageError("Cleanup installer metadata is malformed.")
        key, value = line[2:].split("=", 1)
        if not CLEANUP_KEY.fullmatch(key) or not value or key in values:
            raise CompactImageError("Cleanup installer metadata is malformed or duplicated.")
        values[key] = value

    expected_keys = {
        "format",
        "installer_id",
        "install_contract",
        "linux_id",
        "linux_versions",
        "installed_path",
        "schedule_path",
    }
    if set(values) != expected_keys:
        raise CompactImageError("Cleanup installer metadata keys are incomplete or unknown.")
    versions = tuple(values["linux_versions"].split(","))
    if (
        values["format"] != CLEANUP_METADATA_FORMAT
        or not CLEANUP_ID.fullmatch(values["installer_id"])
        or values["install_contract"] != CLEANUP_INSTALL_CONTRACT
        or not LINUX_ID.fullmatch(values["linux_id"])
        or not versions
        or len(set(versions)) != len(versions)
        or any(not LINUX_VERSION.fullmatch(version) for version in versions)
        or values["installed_path"] != CLEANUP_INSTALLED_PATH
        or values["schedule_path"] != CLEANUP_SCHEDULE_PATH
        or not CLEANUP_FILENAME.fullmatch(source_path.name)
    ):
        raise CompactImageError("Cleanup installer metadata values are invalid.")
    if values["linux_id"] != CURRENT_LINUX_ID or CURRENT_LINUX_VERSION not in versions:
        raise CompactImageError(
            "Cleanup installer does not declare Ubuntu 12.04 support. "
            "Newer Linux expansion adapters are not implemented yet."
        )
    return CleanupRecord(
        installer_id=values["installer_id"],
        source_filename=source_path.name,
        source_sha256=hashlib.sha256(payload).hexdigest(),
        linux_id=values["linux_id"],
        linux_versions=versions,
        install_contract=values["install_contract"],
        installed_path=values["installed_path"],
        schedule_path=values["schedule_path"],
    )


@dataclass(frozen=True)
class _RawEntry:
    status: int
    type_code: int
    start_lba: int
    sector_count: int

    @property
    def empty(self) -> bool:
        return self.type_code == 0 and self.start_lba == 0 and self.sector_count == 0


def _read_sector(reader: BinaryIO, lba: int, sector_size: int) -> bytes:
    reader.seek(lba * sector_size)
    data = reader.read(sector_size)
    if len(data) != sector_size:
        raise CompactImageError(
            f"Short read at LBA {lba}: expected {sector_size} bytes, received {len(data)}."
        )
    return data


def _entries(sector: bytes) -> list[_RawEntry]:
    if len(sector) < 512:
        raise CompactImageError("Partition-table sector is shorter than 512 bytes.")
    entries = []
    for index in range(4):
        offset = PARTITION_TABLE_OFFSET + index * PARTITION_ENTRY_SIZE
        status = sector[offset]
        type_code = sector[offset + 4]
        start_lba, sector_count = struct.unpack_from("<II", sector, offset + 8)
        entries.append(_RawEntry(status, type_code, start_lba, sector_count))
    return entries


def _validate_entry(entry: _RawEntry, label: str) -> None:
    if entry.empty:
        return
    if entry.status not in (0x00, 0x80):
        raise CompactImageError(f"{label} has invalid boot status 0x{entry.status:02X}.")
    if entry.type_code == 0 or entry.start_lba == 0 or entry.sector_count == 0:
        raise CompactImageError(f"{label} is only partially defined.")


def _validate_extent(start: int, count: int, disk_sectors: int, label: str) -> None:
    if start <= 0 or count <= 0:
        raise CompactImageError(f"{label} has an empty or invalid extent.")
    end = start + count
    if end <= start or end > disk_sectors:
        raise CompactImageError(
            f"{label} runs outside the disk: LBA {start} + {count}, "
            f"disk sectors {disk_sectors}."
        )


def _overlap(left: PartitionExtent, right: PartitionExtent) -> bool:
    return left.start_lba < right.end_lba and right.start_lba < left.end_lba


def parse_mbr_layout(
    reader: BinaryIO,
    disk_size: int,
    sector_size: int = 512,
    *,
    require_trailing_space: bool = True,
) -> CompactLayout:
    """Parse one MBR disk and return the safe raw-prefix capture boundary."""
    if sector_size < 512 or disk_size < sector_size or disk_size % sector_size:
        raise CompactImageError(
            f"Unsupported disk geometry: {disk_size} bytes at {sector_size} bytes/sector."
        )
    disk_sectors = disk_size // sector_size
    mbr = _read_sector(reader, 0, sector_size)
    if mbr[510:512] != MBR_SIGNATURE:
        raise CompactImageError("Disk does not contain a valid MBR signature.")

    primary_entries = _entries(mbr)
    partitions: list[PartitionExtent] = []
    extended: tuple[int, int] | None = None
    for index, entry in enumerate(primary_entries, start=1):
        _validate_entry(entry, f"Primary entry {index}")
        if entry.empty:
            continue
        if entry.type_code == GPT_PROTECTIVE_TYPE:
            raise CompactImageError(
                "GPT disks are not supported by bounded raw imaging version 1."
            )
        _validate_extent(
            entry.start_lba, entry.sector_count, disk_sectors, f"Primary entry {index}"
        )
        if entry.type_code in EXTENDED_TYPES:
            if extended is not None:
                raise CompactImageError("More than one extended partition is defined.")
            extended = (entry.start_lba, entry.sector_count)
            continue
        partitions.append(
            PartitionExtent(
                number=index,
                kind="primary",
                type_code=entry.type_code,
                start_lba=entry.start_lba,
                sector_count=entry.sector_count,
                table_lba=0,
                bootable=entry.status == 0x80,
            )
        )

    if extended is not None:
        extended_start, extended_count = extended
        extended_end = extended_start + extended_count
        for partition in partitions:
            if partition.start_lba < extended_end and extended_start < partition.end_lba:
                raise CompactImageError(
                    f"Primary partition {partition.number} overlaps the extended partition."
                )

        ebr_lba = extended_start
        visited: set[int] = set()
        logical_number = 5
        while True:
            if ebr_lba in visited:
                raise CompactImageError(f"Cyclic EBR chain at LBA {ebr_lba}.")
            if len(visited) >= MAX_LOGICAL_PARTITIONS:
                raise CompactImageError("EBR chain exceeds the supported safety limit.")
            if ebr_lba < extended_start or ebr_lba >= extended_end:
                raise CompactImageError(f"EBR LBA {ebr_lba} is outside its container.")
            visited.add(ebr_lba)

            ebr = _read_sector(reader, ebr_lba, sector_size)
            if ebr[510:512] != MBR_SIGNATURE:
                raise CompactImageError(f"EBR at LBA {ebr_lba} has no valid signature.")
            entries = _entries(ebr)
            logical, link = entries[0], entries[1]
            _validate_entry(logical, f"Logical entry at EBR {ebr_lba}")
            _validate_entry(link, f"EBR link at LBA {ebr_lba}")
            if not entries[2].empty or not entries[3].empty:
                raise CompactImageError(
                    f"EBR at LBA {ebr_lba} contains unexpected extra entries."
                )
            if logical.empty or logical.type_code in EXTENDED_TYPES:
                raise CompactImageError(f"EBR at LBA {ebr_lba} has no data partition.")

            logical_start = ebr_lba + logical.start_lba
            _validate_extent(
                logical_start,
                logical.sector_count,
                disk_sectors,
                f"Logical partition {logical_number}",
            )
            if logical_start < extended_start or logical_start + logical.sector_count > extended_end:
                raise CompactImageError(
                    f"Logical partition {logical_number} is outside its extended container."
                )
            partitions.append(
                PartitionExtent(
                    number=logical_number,
                    kind="logical",
                    type_code=logical.type_code,
                    start_lba=logical_start,
                    sector_count=logical.sector_count,
                    table_lba=ebr_lba,
                    bootable=logical.status == 0x80,
                )
            )
            logical_number += 1

            if link.empty:
                break
            if link.type_code not in EXTENDED_TYPES:
                raise CompactImageError(
                    f"EBR link at LBA {ebr_lba} has type 0x{link.type_code:02X}."
                )
            next_ebr = extended_start + link.start_lba
            if next_ebr < extended_start or next_ebr >= extended_end:
                raise CompactImageError(
                    f"Next EBR LBA {next_ebr} is outside its extended container."
                )
            ebr_lba = next_ebr

    if not partitions:
        raise CompactImageError("No data-bearing partitions were found.")

    ordered = sorted(partitions, key=lambda item: item.start_lba)
    for left, right in zip(ordered, ordered[1:], strict=False):
        if _overlap(left, right):
            raise CompactImageError(
                f"Partitions {left.number} and {right.number} overlap."
            )
    capture_bytes = max(item.end_lba for item in ordered) * sector_size
    if require_trailing_space and capture_bytes >= disk_size:
        raise CompactImageError("Disk has no trailing unallocated space to omit.")

    return CompactLayout(
        disk_size=disk_size,
        sector_size=sector_size,
        disk_signature=mbr[440:444].hex().upper(),
        partitions=tuple(sorted(partitions, key=lambda item: item.number)),
        capture_bytes=capture_bytes,
        extended_start_lba=extended[0] if extended else None,
        extended_sector_count=extended[1] if extended else None,
    )


def compact_manifest_path(image_path: str | os.PathLike[str]) -> Path:
    path = Path(image_path)
    name = path.name
    if name.lower().endswith(".compact.img"):
        return path.with_name(name[:-4] + ".json")
    return path.with_suffix(path.suffix + ".json")


def build_manifest(layout: CompactLayout, capture_meta: dict) -> dict:
    device = capture_meta.get("device") or {}
    return {
        "schema_version": MANIFEST_SCHEMA,
        "format": MANIFEST_FORMAT,
        "source": {
            "disk": capture_meta.get("source", ""),
            "disk_size": layout.disk_size,
            "sector_size": layout.sector_size,
            "disk_signature": layout.disk_signature,
            "vendor": device.get("vendor", ""),
            "product": device.get("product", ""),
            "serial": device.get("serial", ""),
            "removable": bool(device.get("removable", False)),
        },
        "capture": {
            "offset": 0,
            "length": layout.capture_bytes,
            "saved_trailing_bytes": layout.saved_bytes,
            "bytes_written": capture_meta.get("bytes_written", 0),
            "bad_sector_count": capture_meta.get("bad_sector_count", 0),
            "digests": capture_meta.get("digests") or {},
            "started_utc": capture_meta.get("started_utc", ""),
            "finished_utc": capture_meta.get("finished_utc", ""),
        },
        "layout": layout.manifest_dict(),
    }


def make_ext4_only_layout(
    source_layout: CompactLayout,
    root_partition: PartitionExtent,
    compact_sector_count: int,
) -> CompactLayout:
    if root_partition.number != 1 or root_partition.kind != "primary":
        raise CompactImageError("Compact ext4 capture requires primary partition 1.")
    if root_partition.type_code != EXT4_PARTITION_TYPE:
        raise CompactImageError("Compact root partition is not Linux type 0x83.")
    if compact_sector_count <= 0 or compact_sector_count > root_partition.sector_count:
        raise CompactImageError("Compact ext4 sector count is out of range.")
    compact_root = PartitionExtent(
        number=1,
        kind="primary",
        type_code=EXT4_PARTITION_TYPE,
        start_lba=root_partition.start_lba,
        sector_count=compact_sector_count,
        table_lba=0,
        bootable=root_partition.bootable,
    )
    capture_bytes = compact_root.end_lba * source_layout.sector_size
    if capture_bytes >= source_layout.disk_size:
        raise CompactImageError("Compacted ext4 layout does not fit inside the source capacity.")
    return CompactLayout(
        disk_size=source_layout.disk_size,
        sector_size=source_layout.sector_size,
        disk_signature=source_layout.disk_signature,
        partitions=(compact_root,),
        capture_bytes=capture_bytes,
    )


def patch_ext4_only_prefix(
    prefix: bytes,
    root_partition: PartitionExtent,
    compact_sector_count: int,
) -> bytes:
    if len(prefix) < 512 or root_partition.start_lba * 512 != len(prefix):
        raise CompactImageError("Captured prefix does not end at the ext4 partition start.")
    if root_partition.number != 1 or root_partition.kind != "primary":
        raise CompactImageError("Compact ext4 capture requires primary partition 1.")
    if compact_sector_count <= 0 or compact_sector_count > 0xFFFFFFFF:
        raise CompactImageError("Compact ext4 sector count is not MBR-compatible.")
    patched = bytearray(prefix)
    patched[:512] = patch_ext4_only_mbr(
        bytes(patched[:512]), root_partition, compact_sector_count
    )
    return bytes(patched)


def patch_ext4_only_mbr(
    mbr: bytes,
    root_partition: PartitionExtent,
    compact_sector_count: int,
) -> bytes:
    if len(mbr) != 512 or mbr[510:512] != MBR_SIGNATURE:
        raise CompactImageError("Captured MBR sector is invalid.")
    if root_partition.number != 1 or root_partition.kind != "primary":
        raise CompactImageError("Compact ext4 capture requires primary partition 1.")
    if compact_sector_count <= 0 or compact_sector_count > 0xFFFFFFFF:
        raise CompactImageError("Compact ext4 sector count is not MBR-compatible.")
    patched = bytearray(mbr)
    root_offset = PARTITION_TABLE_OFFSET
    original_root = bytes(patched[root_offset:root_offset + PARTITION_ENTRY_SIZE])
    patched[PARTITION_TABLE_OFFSET:PARTITION_TABLE_OFFSET + 4 * PARTITION_ENTRY_SIZE] = bytes(
        4 * PARTITION_ENTRY_SIZE
    )
    patched[root_offset:root_offset + PARTITION_ENTRY_SIZE] = original_root
    struct.pack_into("<I", patched, root_offset + 12, compact_sector_count)
    return bytes(patched)


def minimum_target_bytes(
    root_start_lba: int,
    root_sector_count: int,
    swap_sector_count: int,
    alignment_sectors: int,
    sector_size: int,
) -> int:
    for value, label in (
        (root_start_lba, "root start"),
        (root_sector_count, "root size"),
        (swap_sector_count, "swap size"),
        (alignment_sectors, "alignment"),
        (sector_size, "sector size"),
    ):
        if value <= 0:
            raise CompactImageError(f"{label} must be positive.")
    root_end = root_start_lba + root_sector_count
    required_swap_start = root_end + alignment_sectors
    swap_start = (
        (required_swap_start + alignment_sectors - 1) // alignment_sectors
    ) * alignment_sectors
    return (swap_start + swap_sector_count) * sector_size


def build_ext4_manifest(
    image_layout: CompactLayout,
    source_layout: CompactLayout,
    capture_meta: dict,
    filesystem: Ext4FilesystemRecord,
    omitted_swap: OmittedSwapRecord,
    expansion: ExpansionRecord,
    cleanup: CleanupRecord | None = None,
) -> dict:
    device = capture_meta.get("device") or {}
    manifest = {
        "schema_version": EXT4_MANIFEST_SCHEMA,
        "format": EXT4_MANIFEST_FORMAT,
        "source": {
            "disk": capture_meta.get("source", ""),
            "disk_size": source_layout.disk_size,
            "sector_size": source_layout.sector_size,
            "disk_signature": source_layout.disk_signature,
            "vendor": device.get("vendor", ""),
            "product": device.get("product", ""),
            "serial": device.get("serial", ""),
            "removable": bool(device.get("removable", False)),
        },
        "capture": {
            "offset": 0,
            "length": image_layout.capture_bytes,
            "saved_trailing_bytes": image_layout.saved_bytes,
            "bytes_written": capture_meta.get("bytes_written", 0),
            "bad_sector_count": capture_meta.get("bad_sector_count", 0),
            "digests": capture_meta.get("digests") or {},
            "started_utc": capture_meta.get("started_utc", ""),
            "finished_utc": capture_meta.get("finished_utc", ""),
        },
        "layout": image_layout.manifest_dict(),
        "source_layout": source_layout.manifest_dict(),
        "filesystem": filesystem.manifest_dict(),
        "omitted_partitions": [omitted_swap.manifest_dict()],
        "expansion": expansion.manifest_dict(),
    }
    if cleanup is not None:
        manifest["cleanup"] = cleanup.manifest_dict()
    return manifest


def write_manifest(path: str | os.PathLike[str], manifest: dict) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
