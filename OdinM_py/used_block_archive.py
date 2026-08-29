"""Manifest-backed used-block archive capture and restore helpers.

The format is intentionally narrow: 512-byte MBR disks containing only
FAT16/FAT32, NTFS, Ext2/3/4, and optional inactive Linux swap v1.  Filesystem
payloads use Partclone images.  Every stored member and every exact source
range that Partclone reports as allocated is protected by SHA-256.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import time
import uuid
import zipfile
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from compact_image import CompactImageError, parse_mbr_layout
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
    _wsl_path,
    _wsl_partitions,
)
from scripts import pyimager


ARCHIVE_SUFFIX = ".odin-archive"
ARCHIVE_FORMAT = "odinm-used-block-archive"
ARCHIVE_SCHEMA = 1
MANIFEST_MEMBER = "manifest.json"
BOOT_MEMBER = "boot-prefix.bin"
MAX_RANGE_BYTES = 64 << 20
COPY_CHUNK_BYTES = 8 << 20
MAX_MANIFEST_BYTES = 8 << 20
MAX_DOMAIN_BYTES = 64 << 20
HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
UUID_TEXT = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


class UsedBlockArchiveError(RuntimeError):
    """The archive operation cannot continue without breaking a safety gate."""


class UsedBlockArchiveCancelled(UsedBlockArchiveError):
    """The operator cancelled the archive operation."""


def gaming_answer_action(answer: bool | None) -> str:
    """Map the required per-job answer to a fail-closed profile action."""
    if answer is True:
        return "raw"
    if answer is False:
        return "general"
    return "cancel"


_FAT_TYPES = {0x04, 0x06, 0x0B, 0x0C, 0x0E}
_ADAPTERS = {
    "fat16": "partclone.fat",
    "fat32": "partclone.fat",
    "ntfs": "partclone.ntfs",
    "ext2": "partclone.extfs",
    "ext3": "partclone.extfs",
    "ext4": "partclone.extfs",
}


def archive_path(path: str | os.PathLike[str]) -> Path:
    value = Path(path)
    if str(value).casefold().endswith(ARCHIVE_SUFFIX):
        return value
    return value.with_name(value.name + ARCHIVE_SUFFIX)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(COPY_CHUNK_BYTES):
            digest.update(block)
    return digest.hexdigest()


def _partclone_version() -> str:
    output = _run(
        ["wsl.exe", "-u", "root", "--", "partclone.restore", "--version"],
        allowed_codes={0, 1},
    )
    line = next((item.strip() for item in output.splitlines() if item.strip()), "")
    if not line:
        raise UsedBlockArchiveError("Partclone did not report a version.")
    return line


def check_prerequisites(*, expected_version: str = "") -> str:
    if os.name != "nt":
        raise UsedBlockArchiveError("General used-block archives are available only on Windows.")
    script = (
        "set -e\n"
        "for tool in partclone.fat partclone.ntfs partclone.extfs partclone.restore "
        "partclone.chkimg blkid lsblk blockdev mkswap sync; do\n"
        '  command -v "$tool" >/dev/null\n'
        "done\n"
    )
    try:
        _run_wsl_script(script)
        version = _partclone_version()
    except FileNotFoundError as exc:
        raise UsedBlockArchiveError("WSL is unavailable.") from exc
    except Exception as exc:
        raise UsedBlockArchiveError(
            "WSL must provide Partclone FAT, NTFS, Ext, restore/check tools, and mkswap."
        ) from exc
    if expected_version and version != expected_version:
        raise UsedBlockArchiveError(
            f"Partclone version mismatch: archive requires {expected_version!r}, found {version!r}."
        )
    return version


def _filesystem_kind(part_type: int, fstype: str, fsver: str) -> str:
    fs = fstype.casefold()
    version = fsver.casefold()
    if part_type in _FAT_TYPES and fs in {"vfat", "fat", "fat16", "fat32"}:
        if "32" in version or part_type in {0x0B, 0x0C}:
            return "fat32"
        return "fat16"
    if part_type == 0x07 and fs == "ntfs":
        return "ntfs"
    if part_type == 0x83 and fs in {"ext2", "ext3", "ext4"}:
        return fs
    if part_type == 0x82 and fs == "swap":
        return "swap"
    raise UsedBlockArchiveError(
        f"Unsupported partition/filesystem combination 0x{part_type:02X}/{fstype or 'unknown'}."
    )


def _blkid_probe(path: str) -> dict[str, str]:
    output = _run_wsl(["blkid", "-p", "-o", "export", path])
    values: dict[str, str] = {}
    for line in output.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in values:
            raise UsedBlockArchiveError(f"Duplicate blkid field {key} on {path}.")
        values[key] = value
    return values


def _verify_signature_metadata(actual: Any, kind: str) -> dict[str, str]:
    signature = _blkid_probe(actual.path)
    expected_type = "vfat" if kind in {"fat16", "fat32"} else kind
    if signature.get("TYPE", "").casefold() != expected_type:
        raise UsedBlockArchiveError(
            f"Filesystem signature disagrees with metadata on partition {actual.number}."
        )
    if signature.get("UUID", "").casefold() != actual.uuid.casefold():
        raise UsedBlockArchiveError(
            f"Filesystem UUID disagrees with metadata on partition {actual.number}."
        )
    if kind == "swap" and signature.get("VERSION") != "1":
        raise UsedBlockArchiveError(
            f"Swap partition {actual.number} does not have a standard swap v1 signature."
        )
    return signature


def parse_domain_ranges(
    text: str,
    *,
    partition_offset: int,
    partition_length: int,
    max_range_bytes: int = MAX_RANGE_BYTES,
) -> list[tuple[int, int]]:
    """Parse Partclone's ddrescue domain map into bounded absolute byte ranges."""
    if partition_offset < 0 or partition_length <= 0 or max_range_bytes <= 0:
        raise UsedBlockArchiveError("Domain range bounds are invalid.")
    end = partition_offset + partition_length
    raw: list[tuple[int, int]] = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) != 3 or fields[0].startswith("#") or fields[2] != "+":
            continue
        try:
            start = int(fields[0], 0)
            length = int(fields[1], 0)
        except ValueError as exc:
            raise UsedBlockArchiveError("Partclone domain map contains an invalid range.") from exc
        if (
            length <= 0
            or start < partition_offset
            or start + length > end
            or start % 512
            or length % 512
        ):
            raise UsedBlockArchiveError("Partclone domain range is outside its partition.")
        raw.append((start, length))
    if not raw:
        raise UsedBlockArchiveError("Partclone domain map contains no allocated ranges.")
    raw.sort()
    previous_end = partition_offset
    result: list[tuple[int, int]] = []
    for start, length in raw:
        if start < previous_end:
            raise UsedBlockArchiveError("Partclone domain ranges overlap.")
        previous_end = start + length
        position = start
        remaining = length
        while remaining:
            count = min(remaining, max_range_bytes)
            result.append((position, count))
            position += count
            remaining -= count
    return result


def _hash_disk_ranges(
    disk: Any,
    ranges: Iterable[tuple[int, int]],
    *,
    should_cancel: Callable[[], bool],
    on_progress: Callable[[int], None],
) -> list[dict[str, Any]]:
    values = list(ranges)
    total = sum(length for _offset, length in values)
    done = 0
    records: list[dict[str, Any]] = []
    for offset, length in values:
        digest = hashlib.sha256()
        disk.seek(offset)
        remaining = length
        while remaining:
            if should_cancel():
                raise UsedBlockArchiveCancelled("Used-block archive capture was cancelled.")
            block = disk.read(min(COPY_CHUNK_BYTES, remaining))
            if not block:
                raise UsedBlockArchiveError("Short source read while hashing allocated ranges.")
            digest.update(block)
            remaining -= len(block)
            done += len(block)
            on_progress(done * 100 // total if total else 100)
        records.append({"offset": offset, "length": length, "sha256": digest.hexdigest()})
    return records


def canonical_range_digest(records: Iterable[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for item in sorted(records, key=lambda value: (value["offset"], value["length"])):
        digest.update(f"{item['offset']}:{item['length']}:{item['sha256']}\n".encode("ascii"))
    return digest.hexdigest()


def _address_range(record: dict[str, Any], member: str) -> dict[str, Any]:
    return {
        "member": member,
        "offset": record["offset"],
        "length": record["length"],
        "start_lba": record["offset"] // 512,
        "sector_count": record["length"] // 512,
        "sector_size": 512,
        "byte_count": record["length"],
        "sha256": record["sha256"],
    }


def _safe_member_name(name: str) -> str:
    if not name or name.startswith(("/", "\\")) or ".." in Path(name).parts:
        raise UsedBlockArchiveError("Archive member path is unsafe.")
    if Path(name).as_posix() != name or len(Path(name).parts) != 1:
        raise UsedBlockArchiveError("Archive members must be top-level files.")
    return name


def _copy_layout_regions(
    disk: Any, path: Path, regions: Iterable[tuple[int, int]]
) -> tuple[str, list[dict[str, Any]]]:
    member_digest = hashlib.sha256()
    records: list[dict[str, Any]] = []
    member_offset = 0
    with path.open("wb") as output:
        for offset, length in regions:
            digest = hashlib.sha256()
            remaining = length
            disk.seek(offset)
            while remaining:
                block = disk.read(min(COPY_CHUNK_BYTES, remaining))
                if not block:
                    raise UsedBlockArchiveError(
                        "Short source read while preserving an MBR layout region."
                    )
                output.write(block)
                member_digest.update(block)
                digest.update(block)
                remaining -= len(block)
            record = _address_range(
                {"offset": offset, "length": length, "sha256": digest.hexdigest()},
                BOOT_MEMBER,
            )
            record["member_offset"] = member_offset
            records.append(record)
            member_offset += length
        output.flush()
        os.fsync(output.fileno())
    return member_digest.hexdigest(), records


def _archive_members(stage: Path, manifest: dict[str, Any], output: Path) -> None:
    temp_output = output.with_name(output.name + ".partial")
    temp_output.unlink(missing_ok=True)
    try:
        with zipfile.ZipFile(
            temp_output, "w", compression=zipfile.ZIP_STORED, allowZip64=True
        ) as archive:
            archive.writestr(
                MANIFEST_MEMBER,
                json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"),
            )
            names = [manifest["boot"]["member"]]
            for partition in manifest["partitions"]:
                if partition["action"] == "restore":
                    names.extend([partition["member"], partition["domain_member"]])
            for name in names:
                archive.write(stage / _safe_member_name(name), arcname=name)
        os.replace(temp_output, output)
    finally:
        temp_output.unlink(missing_ok=True)


def discover_used_block_source(
    disk_number: int,
    *,
    expected_size: int,
    expected_serial: str,
) -> dict[str, Any]:
    """Run the complete read-only adapter and layout gate before confirmation."""
    version = check_prerequisites()
    if pyimager.volumes_on_disk(disk_number):
        raise UsedBlockArchiveError(
            "General used-block capture requires a source with no Windows-mounted volumes."
        )
    physical_path = rf"\\.\PhysicalDrive{disk_number}"
    attached = False
    source_size = 0
    sector_size = 0
    mbr_sha256 = ""
    layout = None
    source_info: dict[str, Any] = {}
    try:
        with pyimager.Win32Disk(physical_path) as disk:
            source_size = disk.size
            sector_size = disk.sector_size
            source_info = disk.device_info()
            serial = str(source_info.get("serial") or "").strip()
            if expected_size and source_size != expected_size:
                raise UsedBlockArchiveError("Source disk capacity changed before discovery.")
            if expected_serial and serial and serial.casefold() != expected_serial.casefold():
                raise UsedBlockArchiveError("Source disk identity changed before discovery.")
            if sector_size != 512:
                raise UsedBlockArchiveError("General used-block capture requires 512-byte sectors.")
            try:
                layout = parse_mbr_layout(
                    disk, source_size, sector_size, require_trailing_space=False
                )
            except CompactImageError as exc:
                raise UsedBlockArchiveError(f"Only a valid MBR layout is supported: {exc}") from exc
            disk.seek(0)
            mbr = disk.read(512)
            if len(mbr) != 512:
                raise UsedBlockArchiveError("Could not read the source MBR.")
            mbr_sha256 = hashlib.sha256(mbr).hexdigest()

        before = _wsl_disks()
        _run(["wsl.exe", "--mount", physical_path, "--bare"])
        attached = True
        source_device = _wait_for_attached_disk(before, source_size, mbr_sha256)
        _set_source_read_only(source_device)
        actual_partitions = _wsl_partitions(source_device, sector_size)
        active_swaps = {
            line.strip()
            for line in _run_wsl_script("awk 'NR>1 {print $1}' /proc/swaps\n").splitlines()
            if line.strip()
        }
        discovered: list[dict[str, Any]] = []
        for expected in sorted(layout.partitions, key=lambda item: item.start_lba):
            actual = actual_partitions.get(expected.number)
            if actual is None or actual.mounted:
                raise UsedBlockArchiveError(
                    f"Partition {expected.number} is missing or mounted in WSL."
                )
            if (
                actual.start_lba != expected.start_lba
                or actual.sector_count != expected.sector_count
            ):
                raise UsedBlockArchiveError(
                    f"Partition {expected.number} geometry changed during discovery."
                )
            kind = _filesystem_kind(
                expected.type_code, actual.filesystem, actual.filesystem_version
            )
            signature = _verify_signature_metadata(actual, kind)
            if kind == "swap":
                if (
                    actual.filesystem_version != "1"
                    or not UUID_TEXT.fullmatch(actual.uuid)
                    or actual.path in active_swaps
                ):
                    raise UsedBlockArchiveError(
                        f"Swap partition {expected.number} is not reproducible standard inactive swap v1. Use raw/all blocks."
                    )
                adapter = "recreate swap"
            else:
                if (
                    not actual.uuid
                    or len(actual.uuid) > 128
                    or any(ch.isspace() for ch in actual.uuid)
                ):
                    raise UsedBlockArchiveError(
                        f"Partition {expected.number} has no stable filesystem UUID."
                    )
                adapter = _ADAPTERS[kind]
                domain_path = f"/tmp/odinm-preflight-{uuid.uuid4().hex}.map"
                try:
                    _run_wsl(
                        [
                            adapter,
                            "-D",
                            "-s",
                            actual.path,
                            "-o",
                            domain_path,
                            f"--offset_domain={expected.start_lba * sector_size}",
                        ]
                    )
                    parse_domain_ranges(
                        _run_wsl(["cat", domain_path]),
                        partition_offset=expected.start_lba * sector_size,
                        partition_length=expected.sector_count * sector_size,
                    )
                finally:
                    try:
                        _run_wsl(["rm", "-f", "--", domain_path])
                    except Exception:
                        pass
            discovered.append(
                {
                    "number": expected.number,
                    "filesystem": kind,
                    "size_bytes": expected.sector_count * sector_size,
                    "uuid": actual.uuid,
                    "label": signature.get("LABEL", ""),
                    "adapter": adapter,
                }
            )
        return {
            "disk_number": disk_number,
            "disk_size": source_size,
            "serial": str(source_info.get("serial") or "").strip(),
            "model": str(source_info.get("model") or "").strip(),
            "partclone_version": version,
            "partitions": discovered,
        }
    except UsedBlockArchiveError:
        raise
    except Ext4CompactCaptureError as exc:
        raise UsedBlockArchiveError(str(exc)) from exc
    finally:
        if attached:
            try:
                _run(["wsl.exe", "--unmount", physical_path])
            except Exception:
                pass


def capture_used_block_archive(
    disk_number: int,
    output_path: Path,
    *,
    expected_size: int,
    expected_serial: str,
    should_cancel: Callable[[], bool],
    on_progress: Callable[[int], None],
    on_log: Callable[[str], None],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Capture a source disk without writing to or mounting it."""
    version = check_prerequisites()
    output = archive_path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    physical_path = rf"\\.\PhysicalDrive{disk_number}"
    attached = False
    source_device = ""
    before: dict[str, int] = {}
    started = datetime.now(UTC)
    stage_root = Path(tempfile.mkdtemp(prefix="odinm-used-block-"))
    source_size = 0
    sector_size = 0
    source_info: dict[str, Any] = {}
    layout = None
    mbr_sha256 = ""
    try:
        if pyimager.volumes_on_disk(disk_number):
            raise UsedBlockArchiveError(
                "General used-block capture requires a source with no Windows-mounted volumes."
            )
        with pyimager.Win32Disk(physical_path) as disk:
            source_size = disk.size
            sector_size = disk.sector_size
            source_info = disk.device_info()
            serial = str(source_info.get("serial") or "").strip()
            if expected_size and source_size != expected_size:
                raise UsedBlockArchiveError("Source disk capacity changed before capture.")
            if expected_serial and serial and serial.casefold() != expected_serial.casefold():
                raise UsedBlockArchiveError("Source disk identity changed before capture.")
            if sector_size != 512:
                raise UsedBlockArchiveError("General used-block capture requires 512-byte sectors.")
            try:
                layout = parse_mbr_layout(
                    disk, source_size, sector_size, require_trailing_space=False
                )
            except CompactImageError as exc:
                raise UsedBlockArchiveError(f"Only a valid MBR layout is supported: {exc}") from exc
            if not layout.partitions:
                raise UsedBlockArchiveError("The source MBR contains no supported partitions.")
            first_lba = min(item.start_lba for item in layout.partitions)
            prefix_bytes = first_lba * sector_size
            if prefix_bytes <= 0 or prefix_bytes > source_size:
                raise UsedBlockArchiveError("The MBR boot-prefix geometry is invalid.")
            table_offsets = sorted(
                {
                    item.table_lba * sector_size
                    for item in layout.partitions
                    if item.table_lba * sector_size >= prefix_bytes
                }
            )
            layout_regions = [
                (0, prefix_bytes),
                *[(offset, sector_size) for offset in table_offsets],
            ]
            boot_member_sha256, boot_regions = _copy_layout_regions(
                disk, stage_root / BOOT_MEMBER, layout_regions
            )
            disk.seek(0)
            mbr = disk.read(512)
            if len(mbr) != 512:
                raise UsedBlockArchiveError("Could not read the source MBR.")
            mbr_sha256 = hashlib.sha256(mbr).hexdigest()

        before = _wsl_disks()
        _run(["wsl.exe", "--mount", physical_path, "--bare"])
        attached = True
        source_device = _wait_for_attached_disk(before, source_size, mbr_sha256)
        _set_source_read_only(source_device)
        partitions = _wsl_partitions(source_device, sector_size)
        active_swaps = {
            line.strip()
            for line in _run_wsl_script("awk 'NR>1 {print $1}' /proc/swaps\n").splitlines()
            if line.strip()
        }
        records: list[dict[str, Any]] = []
        range_specs: dict[int, list[tuple[int, int]]] = {}
        ordered_partitions = sorted(layout.partitions, key=lambda item: item.start_lba)
        for index, expected in enumerate(ordered_partitions, start=1):
            actual = partitions.get(expected.number)
            if actual is None:
                raise UsedBlockArchiveError(f"WSL did not expose partition {expected.number}.")
            if actual.mounted:
                raise UsedBlockArchiveError(
                    f"Partition {expected.number} is mounted; capture stopped."
                )
            if (
                actual.start_lba != expected.start_lba
                or actual.sector_count != expected.sector_count
            ):
                raise UsedBlockArchiveError(
                    f"Partition {expected.number} geometry changed during discovery."
                )
            kind = _filesystem_kind(
                expected.type_code, actual.filesystem, actual.filesystem_version
            )
            signature = _verify_signature_metadata(actual, kind)
            base = {
                "number": expected.number,
                "kind": expected.kind,
                "type_code": f"0x{expected.type_code:02X}",
                "start_lba": expected.start_lba,
                "sector_count": expected.sector_count,
                "filesystem": kind,
                "filesystem_version": actual.filesystem_version,
                "uuid": actual.uuid,
                "label": signature.get("LABEL", ""),
            }
            if kind == "swap":
                if actual.filesystem_version != "1" or not UUID_TEXT.fullmatch(actual.uuid):
                    raise UsedBlockArchiveError(
                        f"Swap partition {expected.number} is not standard unencrypted swap v1."
                    )
                if actual.path in active_swaps:
                    raise UsedBlockArchiveError(
                        f"Swap partition {expected.number} is active; use raw/all blocks."
                    )
                records.append({**base, "action": "recreate"})
                continue
            if not actual.uuid or len(actual.uuid) > 128 or any(ch.isspace() for ch in actual.uuid):
                raise UsedBlockArchiveError(
                    f"Partition {expected.number} has no stable filesystem UUID."
                )
            adapter = _ADAPTERS[kind]
            member = f"partition-{expected.number}.{kind}.pcl"
            domain_member = f"partition-{expected.number}.domain.map"
            member_wsl = _wsl_path(stage_root / member)
            domain_wsl = _wsl_path(stage_root / domain_member)
            on_log(f"Capturing partition {expected.number} ({kind}) with {adapter}.")
            _run_wsl(
                [
                    adapter,
                    "-c",
                    "-s",
                    actual.path,
                    "-o",
                    member_wsl,
                    "-L",
                    "/tmp/odinm-partclone.log",
                ],
                should_cancel=should_cancel,
            )
            _run_wsl(["partclone.chkimg", "-s", member_wsl])
            offset = expected.start_lba * sector_size
            _run_wsl(
                [adapter, "-D", "-s", actual.path, "-o", domain_wsl, f"--offset_domain={offset}"],
                should_cancel=should_cancel,
            )
            domain_text = (stage_root / domain_member).read_text(encoding="utf-8")
            ranges = parse_domain_ranges(
                domain_text,
                partition_offset=offset,
                partition_length=expected.sector_count * sector_size,
            )
            range_specs[expected.number] = ranges
            records.append(
                {
                    **base,
                    "action": "restore",
                    "adapter": adapter,
                    "member": member,
                    "member_sha256": _sha256_file(stage_root / member),
                    "domain_member": domain_member,
                    "domain_sha256": _sha256_file(stage_root / domain_member),
                    "ranges": [],
                }
            )
            on_progress(min(55, 5 + int(50 * index / len(ordered_partitions))))

        _run(["wsl.exe", "--unmount", physical_path])
        attached = False
        all_range_records: list[dict[str, Any]] = list(boot_regions)
        with pyimager.Win32Disk(physical_path) as disk:
            current_info = disk.device_info()
            current_serial = str(current_info.get("serial") or "").strip()
            disk.seek(0)
            current_mbr = disk.read(512)
            if disk.size != source_size or hashlib.sha256(current_mbr).hexdigest() != mbr_sha256:
                raise UsedBlockArchiveError("Source disk changed during capture.")
            if (
                expected_serial
                and current_serial
                and current_serial.casefold() != expected_serial.casefold()
            ):
                raise UsedBlockArchiveError("Source disk identity changed during capture.")
            restorable = [item for item in records if item["action"] == "restore"]
            for index, record in enumerate(restorable, start=1):

                def range_progress(
                    pct: int, current_index: int = index, count: int = len(restorable)
                ) -> None:
                    on_progress(55 + int(35 * ((current_index - 1) + pct / 100) / max(count, 1)))

                hashed = _hash_disk_ranges(
                    disk,
                    range_specs[record["number"]],
                    should_cancel=should_cancel,
                    on_progress=range_progress,
                )
                addressed = [_address_range(item, record["member"]) for item in hashed]
                record["ranges"] = addressed
                all_range_records.extend(addressed)

        finished = datetime.now(UTC)
        manifest = {
            "schema_version": ARCHIVE_SCHEMA,
            "format": ARCHIVE_FORMAT,
            "created_utc": finished.isoformat(),
            "source": {
                "disk_number": disk_number,
                "disk_size": source_size,
                "sector_size": sector_size,
                "disk_signature": layout.disk_signature,
                "mbr_sha256": mbr_sha256,
                "serial": str(source_info.get("serial") or "").strip(),
                "model": str(source_info.get("model") or "").strip(),
            },
            "tools": {"partclone": version},
            "boot": {
                "member": BOOT_MEMBER,
                "member_sha256": boot_member_sha256,
                "regions": boot_regions,
            },
            "partitions": records,
            "range_hashes": {
                "algorithm": "sha256",
                "max_range_bytes": MAX_RANGE_BYTES,
                "canonical_sha256": canonical_range_digest(all_range_records),
            },
        }
        validate_manifest(manifest)
        _archive_members(stage_root, manifest, output)
        archive_sha = _sha256_file(output)
        stored_bytes = output.stat().st_size
        used_bytes = sum(item["length"] for item in all_range_records)
        meta = {
            "format": ARCHIVE_FORMAT,
            "source": f"PhysicalDrive{disk_number}",
            "region_length": used_bytes,
            "bytes_written": used_bytes,
            "stored_bytes": stored_bytes,
            "disk_size": source_size,
            "bad_sector_count": 0,
            "digests": {"sha256": archive_sha},
            "cancelled": False,
            "duration_s": round((finished - started).total_seconds(), 3),
            "archive": str(output),
        }
        on_progress(100)
        return meta, manifest
    except UsedBlockArchiveCancelled:
        raise
    except Ext4CompactCaptureCancelled as exc:
        raise UsedBlockArchiveCancelled("Used-block archive capture was cancelled.") from exc
    except (Ext4CompactCaptureError, OSError, ValueError, zipfile.BadZipFile) as exc:
        raise UsedBlockArchiveError(str(exc)) from exc
    finally:
        if attached:
            try:
                _run(["wsl.exe", "--unmount", physical_path])
            except Exception:
                pass
        shutil.rmtree(stage_root, ignore_errors=True)


def _require_exact_keys(value: Any, name: str, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise UsedBlockArchiveError(f"{name} schema is incomplete or contains unknown keys.")
    return value


def _positive_int(value: Any, name: str, *, zero: bool = False) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < (0 if zero else 1):
        raise UsedBlockArchiveError(f"{name} is invalid.")
    return value


def validate_manifest(manifest: Any) -> dict[str, Any]:
    root = _require_exact_keys(
        manifest,
        "archive manifest",
        {
            "schema_version",
            "format",
            "created_utc",
            "source",
            "tools",
            "boot",
            "partitions",
            "range_hashes",
        },
    )
    if root["schema_version"] != ARCHIVE_SCHEMA or root["format"] != ARCHIVE_FORMAT:
        raise UsedBlockArchiveError("Used-block archive version or format is unsupported.")
    source = _require_exact_keys(
        root["source"],
        "source",
        {
            "disk_number",
            "disk_size",
            "sector_size",
            "disk_signature",
            "mbr_sha256",
            "serial",
            "model",
        },
    )
    disk_size = _positive_int(source["disk_size"], "source.disk_size")
    if source["sector_size"] != 512 or not HEX_SHA256.fullmatch(str(source["mbr_sha256"])):
        raise UsedBlockArchiveError("Source sector size or MBR digest is invalid.")
    if not re.fullmatch(r"[0-9a-fA-F]{8}", str(source["disk_signature"])):
        raise UsedBlockArchiveError("Source disk signature is invalid.")
    tools = _require_exact_keys(root["tools"], "tools", {"partclone"})
    if not isinstance(tools["partclone"], str) or not tools["partclone"].strip():
        raise UsedBlockArchiveError("Partclone version is missing.")
    boot = _require_exact_keys(root["boot"], "boot", {"member", "member_sha256", "regions"})
    _safe_member_name(str(boot["member"]))
    if not HEX_SHA256.fullmatch(str(boot["member_sha256"])):
        raise UsedBlockArchiveError("MBR layout member SHA-256 is invalid.")
    if not isinstance(boot["regions"], list) or not boot["regions"]:
        raise UsedBlockArchiveError("MBR layout region list is empty or invalid.")
    boot_regions: list[dict[str, Any]] = []
    member_end = 0
    source_end = 0
    for value in boot["regions"]:
        region = _require_exact_keys(
            value,
            "boot region",
            {
                "member",
                "member_offset",
                "offset",
                "length",
                "start_lba",
                "sector_count",
                "sector_size",
                "byte_count",
                "sha256",
            },
        )
        offset = _positive_int(region["offset"], "boot region.offset", zero=True)
        length = _positive_int(region["length"], "boot region.length")
        if (
            region["member"] != boot["member"]
            or region["member_offset"] != member_end
            or region["start_lba"] != offset // 512
            or region["sector_count"] != length // 512
            or region["sector_size"] != 512
            or region["byte_count"] != length
            or offset % 512
            or length % 512
            or offset < source_end
            or offset + length > disk_size
            or not HEX_SHA256.fullmatch(str(region["sha256"]))
        ):
            raise UsedBlockArchiveError("MBR layout region is invalid or overlapping.")
        if not boot_regions and offset != 0:
            raise UsedBlockArchiveError("The first MBR layout region must begin at byte zero.")
        boot_regions.append(region)
        member_end += length
        source_end = offset + length
    if not isinstance(root["partitions"], list) or not root["partitions"]:
        raise UsedBlockArchiveError("Partition list is empty or invalid.")
    seen_numbers: set[int] = set()
    all_ranges = list(boot_regions)
    previous_end = 0
    first_partition_start = disk_size
    for partition in root["partitions"]:
        common = {
            "number",
            "kind",
            "type_code",
            "start_lba",
            "sector_count",
            "filesystem",
            "filesystem_version",
            "uuid",
            "label",
            "action",
        }
        action = partition.get("action") if isinstance(partition, dict) else None
        keys = (
            common
            if action == "recreate"
            else common
            | {"adapter", "member", "member_sha256", "domain_member", "domain_sha256", "ranges"}
        )
        item = _require_exact_keys(partition, "partition", keys)
        number = _positive_int(item["number"], "partition.number")
        if number in seen_numbers:
            raise UsedBlockArchiveError("Partition numbers are duplicated.")
        seen_numbers.add(number)
        start = _positive_int(item["start_lba"], "partition.start_lba") * 512
        length = _positive_int(item["sector_count"], "partition.sector_count") * 512
        first_partition_start = min(first_partition_start, start)
        if start < previous_end or start + length > disk_size:
            raise UsedBlockArchiveError("Partition geometry overlaps or exceeds the source disk.")
        previous_end = start + length
        fs = str(item["filesystem"])
        label = item["label"]
        if (
            not isinstance(label, str)
            or len(label.encode("utf-8")) > 127
            or any(ch in label for ch in ("\x00", "\r", "\n"))
        ):
            raise UsedBlockArchiveError("Partition label is invalid.")
        type_text = str(item["type_code"])
        if not re.fullmatch(r"0x[0-9A-F]{2}", type_text):
            raise UsedBlockArchiveError("Partition type code is invalid.")
        type_code = int(type_text, 16)
        if action == "recreate":
            if (
                type_code != 0x82
                or fs != "swap"
                or item["filesystem_version"] != "1"
                or not UUID_TEXT.fullmatch(str(item["uuid"]))
                or len(label.encode("utf-8")) > 16
            ):
                raise UsedBlockArchiveError("Swap recreation record is invalid.")
            continue
        type_matches = (
            (fs in {"fat16", "fat32"} and type_code in _FAT_TYPES)
            or (fs == "ntfs" and type_code == 0x07)
            or (fs in {"ext2", "ext3", "ext4"} and type_code == 0x83)
        )
        if (
            action != "restore"
            or not type_matches
            or fs not in _ADAPTERS
            or item["adapter"] != _ADAPTERS[fs]
        ):
            raise UsedBlockArchiveError("Filesystem restore adapter record is invalid.")
        for name_key, hash_key in (("member", "member_sha256"), ("domain_member", "domain_sha256")):
            _safe_member_name(str(item[name_key]))
            if not HEX_SHA256.fullmatch(str(item[hash_key])):
                raise UsedBlockArchiveError("Archive member SHA-256 is invalid.")
        ranges = item["ranges"]
        if not isinstance(ranges, list) or not ranges:
            raise UsedBlockArchiveError("Allocated range list is empty or invalid.")
        local_end = start
        for value in ranges:
            record = _require_exact_keys(
                value,
                "range",
                {
                    "member",
                    "offset",
                    "length",
                    "start_lba",
                    "sector_count",
                    "sector_size",
                    "byte_count",
                    "sha256",
                },
            )
            offset = _positive_int(record["offset"], "range.offset", zero=True)
            count = _positive_int(record["length"], "range.length")
            if (
                record["member"] != item["member"]
                or record["start_lba"] != offset // 512
                or record["sector_count"] != count // 512
                or record["sector_size"] != 512
                or record["byte_count"] != count
                or count > MAX_RANGE_BYTES
                or offset % 512
                or count % 512
                or offset < local_end
                or offset + count > start + length
            ):
                raise UsedBlockArchiveError("Allocated ranges overlap or exceed their partition.")
            if not HEX_SHA256.fullmatch(str(record["sha256"])):
                raise UsedBlockArchiveError("Allocated range SHA-256 is invalid.")
            local_end = offset + count
            all_ranges.append(record)
    if boot_regions[0]["length"] != first_partition_start:
        raise UsedBlockArchiveError("Boot prefix does not end at the first partition boundary.")
    partition_extents = [
        (item["start_lba"] * 512, (item["start_lba"] + item["sector_count"]) * 512)
        for item in root["partitions"]
    ]
    for region in boot_regions[1:]:
        if any(
            region["offset"] < end and start < region["offset"] + region["length"]
            for start, end in partition_extents
        ):
            raise UsedBlockArchiveError("An EBR layout region overlaps partition payload.")
    hashes = _require_exact_keys(
        root["range_hashes"], "range_hashes", {"algorithm", "max_range_bytes", "canonical_sha256"}
    )
    if (
        hashes["algorithm"] != "sha256"
        or hashes["max_range_bytes"] != MAX_RANGE_BYTES
        or hashes["canonical_sha256"] != canonical_range_digest(all_ranges)
    ):
        raise UsedBlockArchiveError("Canonical allocated-range digest is inconsistent.")
    return root


def load_archive(
    path: str | os.PathLike[str], target_capacity: int
) -> tuple[dict[str, Any], str, int]:
    archive_path_value = Path(path)
    if not archive_path_value.is_file():
        raise UsedBlockArchiveError(f"Archive does not exist: {archive_path_value}")
    try:
        with zipfile.ZipFile(archive_path_value, "r") as archive:
            names = archive.namelist()
            if not names or names[0] != MANIFEST_MEMBER or len(names) != len(set(names)):
                raise UsedBlockArchiveError(
                    "Archive manifest is missing, duplicated, or not first."
                )
            if any(_safe_member_name(name) != name for name in names):
                raise UsedBlockArchiveError("Archive contains an unsafe member path.")
            infos = {item.filename: item for item in archive.infolist()}
            if any(
                item.compress_type != zipfile.ZIP_STORED or item.flag_bits & 0x1
                for item in infos.values()
            ):
                raise UsedBlockArchiveError(
                    "Archive members must be unencrypted and stored without ZIP compression."
                )
            if infos[MANIFEST_MEMBER].file_size > MAX_MANIFEST_BYTES:
                raise UsedBlockArchiveError("Archive manifest exceeds the safety limit.")
            manifest = validate_manifest(json.loads(archive.read(MANIFEST_MEMBER)))
            expected = {MANIFEST_MEMBER, manifest["boot"]["member"]}
            member_hashes = {manifest["boot"]["member"]: manifest["boot"]["member_sha256"]}
            for partition in manifest["partitions"]:
                if partition["action"] == "restore":
                    expected.update({partition["member"], partition["domain_member"]})
                    member_hashes[partition["member"]] = partition["member_sha256"]
                    member_hashes[partition["domain_member"]] = partition["domain_sha256"]
            if set(names) != expected:
                raise UsedBlockArchiveError("Archive member set does not match its manifest.")
            if infos[manifest["boot"]["member"]].file_size != sum(
                item["length"] for item in manifest["boot"]["regions"]
            ):
                raise UsedBlockArchiveError("MBR layout member size is inconsistent.")
            for partition in manifest["partitions"]:
                if partition["action"] != "restore":
                    continue
                partition_bytes = partition["sector_count"] * 512
                if infos[partition["member"]].file_size > partition_bytes + MAX_DOMAIN_BYTES:
                    raise UsedBlockArchiveError(
                        f"Archive member {partition['member']} exceeds its safety limit."
                    )
                if infos[partition["domain_member"]].file_size > MAX_DOMAIN_BYTES:
                    raise UsedBlockArchiveError(
                        f"Archive member {partition['domain_member']} exceeds its safety limit."
                    )
            total_size = sum(item.file_size for item in infos.values())
            if total_size > manifest["source"]["disk_size"] + (2 * MAX_DOMAIN_BYTES):
                raise UsedBlockArchiveError("Archive expands beyond its recorded disk-size limit.")
            for name, expected_hash in member_hashes.items():
                digest = hashlib.sha256()
                with archive.open(name, "r") as stream:
                    while block := stream.read(COPY_CHUNK_BYTES):
                        digest.update(block)
                if digest.hexdigest() != expected_hash:
                    raise UsedBlockArchiveError(f"Archive member {name} failed SHA-256 validation.")
            boot_bytes = archive.read(manifest["boot"]["member"])
            expected_boot_length = sum(item["length"] for item in manifest["boot"]["regions"])
            if len(boot_bytes) != expected_boot_length:
                raise UsedBlockArchiveError("MBR layout member length is inconsistent.")
            for region in manifest["boot"]["regions"]:
                begin = region["member_offset"]
                end = begin + region["length"]
                if hashlib.sha256(boot_bytes[begin:end]).hexdigest() != region["sha256"]:
                    raise UsedBlockArchiveError("MBR layout region SHA-256 is inconsistent.")
            mbr = boot_bytes[:512]
            if (
                len(mbr) != 512
                or mbr[510:512] != b"\x55\xaa"
                or hashlib.sha256(mbr).hexdigest() != manifest["source"]["mbr_sha256"]
                or mbr[440:444].hex() != manifest["source"]["disk_signature"].casefold()
            ):
                raise UsedBlockArchiveError("Boot-prefix MBR identity is inconsistent.")
    except (OSError, json.JSONDecodeError, KeyError, zipfile.BadZipFile) as exc:
        if isinstance(exc, UsedBlockArchiveError):
            raise
        raise UsedBlockArchiveError(f"Used-block archive is unreadable: {exc}") from exc
    required = manifest["source"]["disk_size"]
    if target_capacity < required:
        raise UsedBlockArchiveError(
            f"Target capacity {target_capacity} is smaller than the recorded source disk {required}."
        )
    return manifest, _sha256_file(archive_path_value), required


def extract_archive(path: Path, destination: Path, manifest: dict[str, Any]) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(path, "r") as archive:
        for name in archive.namelist():
            _safe_member_name(name)
            if name == MANIFEST_MEMBER:
                continue
            target = destination / name
            with archive.open(name, "r") as source, target.open("wb") as output:
                shutil.copyfileobj(source, output, COPY_CHUNK_BYTES)
    for name, digest in [(manifest["boot"]["member"], manifest["boot"]["member_sha256"])]:
        if _sha256_file(destination / name) != digest:
            raise UsedBlockArchiveError(f"Extracted archive member {name} failed validation.")
    for partition in manifest["partitions"]:
        if partition["action"] != "restore":
            continue
        for name_key, hash_key in (("member", "member_sha256"), ("domain_member", "domain_sha256")):
            if _sha256_file(destination / partition[name_key]) != partition[hash_key]:
                raise UsedBlockArchiveError(
                    f"Extracted archive member {partition[name_key]} failed validation."
                )


def _validate_target_partitions(disk_path: str, manifest: dict[str, Any]) -> dict[int, Any]:
    partitions = _wsl_partitions(disk_path, 512)
    for expected in manifest["partitions"]:
        actual = partitions.get(expected["number"])
        if actual is None:
            raise UsedBlockArchiveError(
                f"WSL did not expose target partition {expected['number']}."
            )
        if actual.mounted:
            raise UsedBlockArchiveError(
                f"WSL mounted target partition {expected['number']}; restore stopped."
            )
        if (
            actual.start_lba != expected["start_lba"]
            or actual.sector_count != expected["sector_count"]
        ):
            raise UsedBlockArchiveError(
                f"Target partition {expected['number']} geometry does not match the archive."
            )
    return partitions


def _wait_for_target_disk(before: dict[str, int], required_capacity: int, mbr_sha256: str) -> str:
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        after = _wsl_disks()
        candidates = [
            path for path, size in after.items() if path not in before and size >= required_capacity
        ]
        matches = [path for path in candidates if _wsl_mbr_sha256(path) == mbr_sha256]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise UsedBlockArchiveError("WSL exposed more than one matching target disk.")
        time.sleep(0.25)
    raise UsedBlockArchiveError("WSL did not expose the selected target disk.")


def restore_partition_payloads(
    physical_path: str,
    manifest: dict[str, Any],
    extracted: Path,
    *,
    should_cancel: Callable[[], bool] | None,
    on_progress: Callable[[int], None],
    on_log: Callable[[str], None],
) -> None:
    """Restore Partclone members and recreate swap on an already-partitioned target."""
    check_prerequisites(expected_version=manifest["tools"]["partclone"])
    before = _wsl_disks()
    attached = False
    try:
        _run(["wsl.exe", "--mount", physical_path, "--bare"])
        attached = True
        target = _wait_for_target_disk(
            before,
            manifest["source"]["disk_size"],
            manifest["source"]["mbr_sha256"],
        )
        partitions = _validate_target_partitions(target, manifest)
        actions = manifest["partitions"]
        for index, record in enumerate(actions, start=1):
            actual = partitions[record["number"]]
            if record["action"] == "recreate":
                on_log(f"Recreating swap partition {record['number']} with its recorded UUID.")
                command = ["mkswap", "-U", record["uuid"]]
                if record["label"]:
                    command.extend(["-L", record["label"]])
                command.append(actual.path)
                _run_wsl(command, should_cancel=should_cancel)
                swap_type = _run_wsl(["blkid", "-o", "value", "-s", "TYPE", actual.path]).strip()
                swap_uuid = _run_wsl(["blkid", "-o", "value", "-s", "UUID", actual.path]).strip()
                swap_label = _run(
                    [
                        "wsl.exe",
                        "-u",
                        "root",
                        "--",
                        "blkid",
                        "-o",
                        "value",
                        "-s",
                        "LABEL",
                        actual.path,
                    ],
                    allowed_codes={0, 2},
                ).strip()
                if (
                    swap_type != "swap"
                    or swap_uuid.casefold() != record["uuid"].casefold()
                    or swap_label != record["label"]
                ):
                    raise UsedBlockArchiveError(
                        f"Swap partition {record['number']} identity verification failed."
                    )
            else:
                source = _wsl_path(extracted / record["member"])
                _run_wsl(["partclone.chkimg", "-s", source], should_cancel=should_cancel)
                on_log(f"Restoring partition {record['number']} ({record['filesystem']}).")
                _run_wsl(
                    [
                        record["adapter"],
                        "-r",
                        "-s",
                        source,
                        "-o",
                        actual.path,
                        "-L",
                        "/tmp/odinm-partclone.log",
                    ],
                    should_cancel=should_cancel,
                )
                validation_map = f"/tmp/odinm-verify-{record['number']}-{os.getpid()}.map"
                try:
                    _run_wsl(
                        [
                            record["adapter"],
                            "-D",
                            "-s",
                            actual.path,
                            "-o",
                            validation_map,
                            f"--offset_domain={record['start_lba'] * 512}",
                        ],
                        should_cancel=should_cancel,
                    )
                    observed = parse_domain_ranges(
                        _run_wsl(["cat", validation_map]),
                        partition_offset=record["start_lba"] * 512,
                        partition_length=record["sector_count"] * 512,
                    )
                    expected = [(item["offset"], item["length"]) for item in record["ranges"]]
                    if observed != expected:
                        raise UsedBlockArchiveError(
                            f"Partition {record['number']} allocated-range map changed after restore."
                        )
                finally:
                    try:
                        _run_wsl(["rm", "-f", "--", validation_map])
                    except Exception:
                        pass
            on_progress(int(100 * index / len(actions)))
        _run_wsl(["sync"])
    except Ext4CompactCaptureCancelled as exc:
        raise UsedBlockArchiveCancelled("Used-block archive restore was cancelled.") from exc
    finally:
        if attached:
            try:
                _run(["wsl.exe", "--unmount", physical_path])
            except Exception:
                pass


def verify_target_ranges(
    disk: Any,
    manifest: dict[str, Any],
    *,
    should_cancel: Callable[[], bool],
    on_progress: Callable[[int], None],
) -> tuple[str, int]:
    expected: list[dict[str, Any]] = list(manifest["boot"]["regions"])
    for partition in manifest["partitions"]:
        if partition["action"] == "restore":
            expected.extend(partition["ranges"])
    observed_raw = _hash_disk_ranges(
        disk,
        [(item["offset"], item["length"]) for item in expected],
        should_cancel=should_cancel,
        on_progress=on_progress,
    )
    observed = []
    for source, target in zip(expected, observed_raw, strict=True):
        if "member" in source:
            value = _address_range(target, source["member"])
            if "member_offset" in source:
                value["member_offset"] = source["member_offset"]
            observed.append(value)
        else:
            observed.append(target)
    for source, target in zip(expected, observed, strict=True):
        if source != target:
            raise UsedBlockArchiveError(
                f"Target range at byte {source['offset']} failed exact SHA-256 verification."
            )
    return canonical_range_digest(observed), sum(item["length"] for item in observed)
