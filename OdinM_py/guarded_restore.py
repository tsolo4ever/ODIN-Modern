"""Strict image preflight and mandatory-verify restore for guarded fixed disks."""

from __future__ import annotations

import ctypes
import gzip
import hashlib
import json
import os
import re
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from compact_image import (
    EXT4_MANIFEST_FORMAT,
    EXT4_MANIFEST_SCHEMA,
    MANIFEST_FORMAT,
    MANIFEST_SCHEMA,
    compact_manifest_path,
    minimum_target_bytes,
    parse_mbr_layout,
)
from clone_worker import CloneStatus
from guarded_flash_safety import (
    DiskIdentity,
    EligibilityDecision,
    ProtectedHardwareStore,
    query_windows_storage_inventory,
    revalidate_target,
)
from partition_reader import PartitionReadError, read_mbr_partitions_strict
from hash_config import HashConfig
from scripts import pyimager


CHUNK_BYTES = 8 << 20
MIN_SECTOR_BYTES = 512
HEX_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
UUID_TEXT = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


class GuardedImageError(ValueError):
    pass


class GuardedRestoreError(RuntimeError):
    def __init__(self, message: str, *, target_not_trusted: bool = False):
        super().__init__(message)
        self.target_not_trusted = target_not_trusted


@dataclass(frozen=True)
class GuardedImagePlan:
    original_path: Path
    source_path: Path
    image_format: str
    write_bytes: int
    required_capacity: int
    sha256: str
    temporary_source: bool = False
    manifest_path: Path | None = None

    def cleanup(self) -> None:
        if self.temporary_source:
            self.source_path.unlink(missing_ok=True)

    @property
    def summary(self) -> str:
        return (
            f"{self.original_path.name} [{self.image_format}], {self.write_bytes} bytes, "
            f"SHA-256 {self.sha256}"
        )


@dataclass(frozen=True)
class GuardedRestoreResult:
    bytes_written: int
    source_sha256: str
    target_sha256: str
    verified: bool
    cancelled: bool
    target_not_trusted: bool
    elapsed_seconds: float


def _configured_policy_checks(
    plan: GuardedImagePlan,
    raw_path: str,
    *,
    disk_factory: Callable[..., Any],
    hash_config_provider: Callable[[], Any],
    should_cancel: Callable[[], bool] | None,
    on_progress: Callable[[str, int, int], None],
    on_log: Callable[[str], None],
) -> bool:
    enabled = hash_config_provider().get_enabled_partitions(str(plan.original_path))
    if not enabled:
        on_log("No additional configured partition hashes apply to this image.")
        return True
    partition_configs = [(number, cfg) for number, cfg in sorted(enabled.items()) if number > 0]
    if enabled.get(0) is not None and partition_configs:
        raise GuardedRestoreError(
            "whole-disk and partition-specific hashes are both enabled",
            target_not_trusted=True,
        )
    if partition_configs:
        partitions = {item.number: item for item in read_mbr_partitions_strict(raw_path)}
        regions = []
        for number, config in partition_configs:
            partition = partitions.get(number)
            if partition is None:
                raise GuardedRestoreError(
                    f"configured target partition {number} was not found",
                    target_not_trusted=True,
                )
            regions.append((f"partition {number}", config, partition.offset, partition.size))
    else:
        regions = [("whole written image", enabled[0], 0, plan.write_bytes)]

    with disk_factory(raw_path) as target:
        for label, config, offset, length in regions:
            checks = {
                "SHA-1": (bool(config.get("sha1_enabled")), str(config.get("sha1_value") or "").lower()),
                "SHA-256": (
                    bool(config.get("sha256_enabled")),
                    str(config.get("sha256_value") or "").lower(),
                ),
            }
            if not any(active and expected for active, expected in checks.values()):
                raise GuardedRestoreError(
                    f"no enabled hash value exists for {label}", target_not_trusted=True
                )
            sha1 = hashlib.sha1()
            sha256 = hashlib.sha256()
            done = 0
            target.seek(offset)
            while done < length:
                if should_cancel is not None and should_cancel():
                    return False
                block = target.read(min(CHUNK_BYTES, length - done))
                if not block:
                    raise GuardedRestoreError(
                        f"short read while checking {label}", target_not_trusted=True
                    )
                sha1.update(block)
                sha256.update(block)
                done += len(block)
                on_progress("policy", done, length)
            actual = {"SHA-1": sha1.hexdigest(), "SHA-256": sha256.hexdigest()}
            for algorithm, (active, expected) in checks.items():
                if active:
                    if not expected or actual[algorithm] != expected:
                        raise GuardedRestoreError(
                            f"configured {label} {algorithm} mismatch",
                            target_not_trusted=True,
                        )
                    on_log(f"Configured {label} {algorithm} passed.")
    return True


def _hash_stream(
    stream,
    *,
    limit: int | None = None,
    should_cancel: Callable[[], bool] | None = None,
    on_progress: Callable[[str, int, int], None] | None = None,
    phase: str = "preflight",
) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    while limit is None or total < limit:
        if should_cancel is not None and should_cancel():
            raise GuardedImageError("image preflight was cancelled")
        wanted = CHUNK_BYTES if limit is None else min(CHUNK_BYTES, limit - total)
        block = stream.read(wanted)
        if not block:
            break
        digest.update(block)
        total += len(block)
        if on_progress:
            on_progress(phase, total, limit or 0)
    if limit is not None and total != limit:
        raise GuardedImageError(f"short image read: expected {limit} bytes, read {total}")
    return digest.hexdigest(), total


def _require_object(value: Any, label: str, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise GuardedImageError(f"compact manifest {label} schema is invalid")
    return value


def _positive_int(value: Any, label: str, *, allow_zero: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise GuardedImageError(f"compact manifest {label} must be an integer")
    if value < 0 or (value == 0 and not allow_zero):
        raise GuardedImageError(f"compact manifest {label} is out of range")
    return value


def _validate_partition_record(value: Any, label: str) -> dict[str, Any]:
    record = _require_object(
        value,
        label,
        {
            "number", "kind", "type_code", "start_lba", "sector_count",
            "table_lba", "bootable", "type",
        },
    )
    _positive_int(record["number"], f"{label}.number")
    _positive_int(record["start_lba"], f"{label}.start_lba")
    _positive_int(record["sector_count"], f"{label}.sector_count")
    _positive_int(record["table_lba"], f"{label}.table_lba", allow_zero=True)
    _positive_int(record["type_code"], f"{label}.type_code")
    if record["kind"] not in ("primary", "logical") or not isinstance(record["bootable"], bool):
        raise GuardedImageError(f"compact manifest {label} partition schema is invalid")
    if record["type"] != f"0x{record['type_code']:02X}":
        raise GuardedImageError(f"compact manifest {label} partition type is inconsistent")
    return record


def _validate_ext4_records(
    root: dict[str, Any], disk_size: int, sector_size: int, capture_length: int
) -> int:
    source_layout = _require_object(
        root["source_layout"],
        "source_layout",
        {"partition_style", "extended_start_lba", "extended_sector_count", "partitions"},
    )
    filesystem = _require_object(
        root["filesystem"],
        "filesystem",
        {
            "partition_number", "uuid", "block_size", "original_start_lba",
            "original_sector_count", "compact_sector_count", "minimum_blocks",
            "buffer_bytes", "prefix_bytes", "type",
        },
    )
    expansion = _require_object(
        root["expansion"],
        "expansion",
        {
            "root_uuid", "swap_uuid", "swap_sector_count", "alignment_sectors",
            "minimum_target_bytes", "installed_script_sha256", "armed",
        },
    )
    omitted = root["omitted_partitions"]
    if not isinstance(omitted, list) or len(omitted) != 1:
        raise GuardedImageError("compact manifest omitted swap schema is invalid")
    swap = _require_object(
        omitted[0],
        "omitted_partitions[0]",
        {"partition_number", "uuid", "original_start_lba", "sector_count", "type"},
    )
    if source_layout["partition_style"] != "MBR" or not isinstance(
        source_layout["partitions"], list
    ):
        raise GuardedImageError("compact manifest source layout is invalid")
    source_partitions = [
        _validate_partition_record(item, f"source_layout.partitions[{index}]")
        for index, item in enumerate(source_layout["partitions"])
    ]
    if len(source_partitions) != 2 or len(
        {item["number"] for item in source_partitions}
    ) != 2:
        raise GuardedImageError(
            "compact manifest source must contain exactly one root and one swap partition"
        )
    image_partitions = root["layout"]["partitions"]
    if not isinstance(image_partitions, list) or len(image_partitions) != 1:
        raise GuardedImageError("ext4 compact image must contain exactly one partition")
    if (
        root["layout"]["extended_start_lba"] is not None
        or root["layout"]["extended_sector_count"] is not None
    ):
        raise GuardedImageError("ext4 compact image must not contain an extended partition")
    image_root = _validate_partition_record(image_partitions[0], "layout.partitions[0]")
    for key in (
        "partition_number", "block_size", "original_start_lba",
        "original_sector_count", "compact_sector_count", "minimum_blocks",
        "buffer_bytes", "prefix_bytes",
    ):
        _positive_int(filesystem[key], f"filesystem.{key}")
    for key in (
        "partition_number", "original_start_lba", "sector_count"
    ):
        _positive_int(swap[key], f"omitted_partitions[0].{key}")
    for key in (
        "swap_sector_count", "alignment_sectors", "minimum_target_bytes"
    ):
        _positive_int(expansion[key], f"expansion.{key}")
    if filesystem["type"] != "ext4" or swap["type"] != "linux-swap":
        raise GuardedImageError("compact manifest filesystem types are invalid")
    if not UUID_TEXT.fullmatch(str(filesystem["uuid"])) or not UUID_TEXT.fullmatch(
        str(swap["uuid"])
    ):
        raise GuardedImageError("compact manifest filesystem UUID is invalid")
    if expansion["armed"] is not True or not HEX_SHA256.fullmatch(
        str(expansion["installed_script_sha256"])
    ):
        raise GuardedImageError("compact manifest expansion hook is not valid")
    if filesystem["buffer_bytes"] != 64 << 20:
        raise GuardedImageError("compact manifest ext4 buffer is unsupported")
    if filesystem["block_size"] not in (1024, 2048, 4096):
        raise GuardedImageError("compact manifest ext4 block size is unsupported")
    if filesystem["buffer_bytes"] % filesystem["block_size"]:
        raise GuardedImageError("compact manifest ext4 buffer is not block aligned")
    compact_bytes = filesystem["compact_sector_count"] * sector_size
    expected_compact_bytes = (
        filesystem["minimum_blocks"] * filesystem["block_size"]
        + filesystem["buffer_bytes"]
    )
    if compact_bytes != expected_compact_bytes:
        raise GuardedImageError("compact manifest ext4 minimum and buffer are inconsistent")
    if filesystem["compact_sector_count"] > filesystem["original_sector_count"]:
        raise GuardedImageError("compact manifest ext4 size exceeds the source filesystem")
    if filesystem["prefix_bytes"] != filesystem["original_start_lba"] * sector_size:
        raise GuardedImageError("compact manifest prefix length is inconsistent")
    if filesystem["partition_number"] != 1 or image_root["number"] != 1:
        raise GuardedImageError("compact manifest root must be partition 1")
    if image_root["type_code"] != 0x83 or not image_root["bootable"]:
        raise GuardedImageError("compact manifest root partition is not bootable ext4")
    if (
        image_root["start_lba"] != filesystem["original_start_lba"]
        or image_root["sector_count"] != filesystem["compact_sector_count"]
        or capture_length != image_root["start_lba"] * sector_size
        + image_root["sector_count"] * sector_size
    ):
        raise GuardedImageError("compact manifest ext4 geometry is inconsistent")
    source_root = next(
        (item for item in source_partitions if item["number"] == filesystem["partition_number"]),
        None,
    )
    source_swap = next(
        (item for item in source_partitions if item["number"] == swap["partition_number"]),
        None,
    )
    if source_root is None or source_swap is None:
        raise GuardedImageError("compact manifest source partitions are incomplete")
    if (
        source_root["type_code"] != 0x83
        or source_root["kind"] != "primary"
        or not source_root["bootable"]
        or source_root["start_lba"] != filesystem["original_start_lba"]
        or source_root["sector_count"] != filesystem["original_sector_count"]
        or source_swap["type_code"] != 0x82
        or source_swap["start_lba"] != swap["original_start_lba"]
        or source_swap["sector_count"] != swap["sector_count"]
    ):
        raise GuardedImageError("compact manifest source geometry is inconsistent")
    disk_sectors = disk_size // sector_size
    if any(
        item["start_lba"] + item["sector_count"] > disk_sectors
        for item in source_partitions
    ):
        raise GuardedImageError("compact manifest source partition exceeds the source disk")
    if source_swap["kind"] == "logical":
        extended_start = source_layout["extended_start_lba"]
        extended_count = source_layout["extended_sector_count"]
        if (
            not isinstance(extended_start, int)
            or not isinstance(extended_count, int)
            or extended_start <= 0
            or extended_count <= 0
            or source_swap["start_lba"] < extended_start
            or source_swap["start_lba"] + source_swap["sector_count"]
            > extended_start + extended_count
        ):
            raise GuardedImageError("compact manifest source extended geometry is invalid")
    elif (
        source_layout["extended_start_lba"] is not None
        or source_layout["extended_sector_count"] is not None
    ):
        raise GuardedImageError("compact manifest source extended geometry is unexpected")
    if (
        str(expansion["root_uuid"]).casefold() != str(filesystem["uuid"]).casefold()
        or str(expansion["swap_uuid"]).casefold() != str(swap["uuid"]).casefold()
        or expansion["swap_sector_count"] != swap["sector_count"]
    ):
        raise GuardedImageError("compact manifest expansion identity is inconsistent")
    required = minimum_target_bytes(
        image_root["start_lba"],
        image_root["sector_count"],
        swap["sector_count"],
        expansion["alignment_sectors"],
        sector_size,
    )
    if expansion["minimum_target_bytes"] != required or required > disk_size:
        raise GuardedImageError("compact manifest minimum target capacity is inconsistent")
    return required


def _load_compact_manifest(path: Path) -> tuple[dict[str, Any], int, int, str, int]:
    manifest_path = compact_manifest_path(path)
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise GuardedImageError(f"matching compact manifest is missing: {manifest_path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise GuardedImageError(f"compact manifest is unreadable: {exc}") from exc
    if not isinstance(payload, dict):
        raise GuardedImageError("compact manifest root schema is invalid")
    legacy = (
        payload.get("schema_version") == MANIFEST_SCHEMA
        and payload.get("format") == MANIFEST_FORMAT
    )
    ext4_compact = (
        payload.get("schema_version") == EXT4_MANIFEST_SCHEMA
        and payload.get("format") == EXT4_MANIFEST_FORMAT
    )
    if legacy:
        root_keys = {"schema_version", "format", "source", "capture", "layout"}
    elif ext4_compact:
        root_keys = {
            "schema_version", "format", "source", "capture", "layout",
            "source_layout", "filesystem", "omitted_partitions", "expansion",
        }
    else:
        raise GuardedImageError("compact manifest version or format is unsupported")
    root = _require_object(payload, "root", root_keys)
    source = _require_object(
        root["source"],
        "source",
        {"disk", "disk_size", "sector_size", "disk_signature", "vendor", "product", "serial", "removable"},
    )
    capture = _require_object(
        root["capture"],
        "capture",
        {
            "offset", "length", "saved_trailing_bytes", "bytes_written",
            "bad_sector_count", "digests", "started_utc", "finished_utc",
        },
    )
    layout = _require_object(
        root["layout"],
        "layout",
        {"partition_style", "extended_start_lba", "extended_sector_count", "partitions"},
    )
    disk_size = _positive_int(source["disk_size"], "source.disk_size")
    sector_size = _positive_int(source["sector_size"], "source.sector_size")
    capture_length = _positive_int(capture["length"], "capture.length")
    bytes_written = _positive_int(capture["bytes_written"], "capture.bytes_written")
    saved = _positive_int(capture["saved_trailing_bytes"], "capture.saved_trailing_bytes", allow_zero=True)
    bad_sectors = _positive_int(capture["bad_sector_count"], "capture.bad_sector_count", allow_zero=True)
    if sector_size not in (512, 1024, 2048, 4096):
        raise GuardedImageError("compact manifest sector size is unsupported")
    if capture["offset"] != 0 or capture_length != bytes_written:
        raise GuardedImageError("compact manifest capture length is inconsistent")
    if capture_length + saved != disk_size or capture_length % sector_size:
        raise GuardedImageError("compact manifest capacity accounting is inconsistent")
    if bad_sectors:
        raise GuardedImageError("compact image contains recovered bad-sector substitutions")
    signature = str(source["disk_signature"])
    if not re.fullmatch(r"[0-9a-fA-F]{8}", signature):
        raise GuardedImageError("compact manifest disk signature is invalid")
    digests = capture["digests"]
    if (
        not isinstance(digests, dict)
        or "sha256" not in digests
        or not set(digests).issubset({"sha256", "sha1"})
    ):
        raise GuardedImageError("compact manifest digest schema is invalid")
    sha256 = str(digests.get("sha256") or "")
    if not HEX_SHA256.fullmatch(sha256):
        raise GuardedImageError("compact manifest SHA-256 is missing or invalid")
    if layout["partition_style"] != "MBR" or not isinstance(layout["partitions"], list):
        raise GuardedImageError("compact manifest partition layout is invalid")
    required_capacity = capture_length
    if ext4_compact:
        required_capacity = _validate_ext4_records(
            root, disk_size, sector_size, capture_length
        )
    return root, disk_size, capture_length, sha256.lower(), required_capacity


def _validate_compact_layout(path: Path, manifest: dict[str, Any], disk_size: int) -> None:
    source = manifest["source"]
    capture = manifest["capture"]
    layout_record = manifest["layout"]
    sector_size = source["sector_size"]
    with path.open("rb") as stream:
        actual = parse_mbr_layout(stream, disk_size=disk_size, sector_size=sector_size)
    if actual.capture_bytes != capture["length"]:
        raise GuardedImageError("compact image layout does not match the recorded capture length")
    if actual.disk_signature.casefold() != source["disk_signature"].casefold():
        raise GuardedImageError("compact image disk signature does not match its manifest")
    if actual.extended_start_lba != layout_record["extended_start_lba"]:
        raise GuardedImageError("compact image extended-partition start does not match")
    if actual.extended_sector_count != layout_record["extended_sector_count"]:
        raise GuardedImageError("compact image extended-partition size does not match")
    expected_partitions = layout_record["partitions"]
    actual_partitions = [partition.manifest_dict() for partition in actual.partitions]
    if actual_partitions != expected_partitions:
        raise GuardedImageError("compact image partitions do not match the manifest")


def preflight_image(
    image_path: str | os.PathLike[str],
    target_capacity: int,
    *,
    should_cancel: Callable[[], bool] | None = None,
    on_progress: Callable[[str, int, int], None] | None = None,
    on_log: Callable[[str], None] | None = None,
) -> GuardedImagePlan:
    path = Path(image_path).resolve()
    if not path.is_file():
        raise GuardedImageError(f"image does not exist: {path}")
    if target_capacity <= 0:
        raise GuardedImageError("target capacity is unavailable")
    log = on_log or (lambda _line: None)
    lower_name = path.name.casefold()

    if lower_name.endswith(".compact.img"):
        manifest, disk_size, capture_length, recorded_sha, required_capacity = (
            _load_compact_manifest(path)
        )
        if path.stat().st_size != capture_length:
            raise GuardedImageError("compact image length does not match its manifest")
        if target_capacity < required_capacity:
            requirement = (
                "required compact root-plus-swap capacity"
                if manifest["schema_version"] == EXT4_MANIFEST_SCHEMA
                else "captured layout"
            )
            raise GuardedImageError(
                f"target capacity {target_capacity} is smaller than "
                f"{requirement} {required_capacity}"
            )
        with path.open("rb") as stream:
            digest, read_bytes = _hash_stream(
                stream, limit=capture_length, should_cancel=should_cancel,
                on_progress=on_progress, phase="preflight",
            )
        if digest != recorded_sha:
            raise GuardedImageError("compact image SHA-256 does not match its manifest")
        _validate_compact_layout(path, manifest, disk_size)
        log(f"Compact image validated: {read_bytes} bytes, SHA-256 {digest}")
        return GuardedImagePlan(
            path, path, "compact", capture_length, required_capacity, digest,
            manifest_path=compact_manifest_path(path),
        )

    if lower_name.endswith(".gz"):
        descriptor, temporary_name = tempfile.mkstemp(prefix="odinm-guarded-", suffix=".raw")
        os.close(descriptor)
        temporary_path = Path(temporary_name)
        digest = hashlib.sha256()
        written = 0
        try:
            with path.open("rb") as stored, gzip.GzipFile(fileobj=stored) as source, temporary_path.open("wb") as output:
                while True:
                    if should_cancel is not None and should_cancel():
                        raise GuardedImageError("image preflight was cancelled")
                    block = source.read(CHUNK_BYTES)
                    if not block:
                        break
                    written += len(block)
                    if written > target_capacity:
                        raise GuardedImageError("decompressed image is larger than the selected target")
                    output.write(block)
                    digest.update(block)
                    if on_progress:
                        on_progress("preflight", stored.tell(), path.stat().st_size)
                output.flush()
                os.fsync(output.fileno())
        except (OSError, EOFError, gzip.BadGzipFile, GuardedImageError) as exc:
            temporary_path.unlink(missing_ok=True)
            if isinstance(exc, GuardedImageError):
                raise
            raise GuardedImageError(f"compressed image preflight failed: {exc}") from exc
        if written <= 0 or written % MIN_SECTOR_BYTES:
            temporary_path.unlink(missing_ok=True)
            raise GuardedImageError("decompressed image length is empty or not 512-byte aligned")
        result = GuardedImagePlan(
            path, temporary_path, "gzip", written, written, digest.hexdigest(), True
        )
        log(f"Compressed image fully preflighted: {result.summary}")
        return result

    size = path.stat().st_size
    if size <= 0 or size % MIN_SECTOR_BYTES:
        raise GuardedImageError("raw image length is empty or not 512-byte aligned")
    if size > target_capacity:
        raise GuardedImageError(f"image ({size} bytes) is larger than target ({target_capacity} bytes)")
    with path.open("rb") as stream:
        digest, _ = _hash_stream(
            stream, limit=size, should_cancel=should_cancel,
            on_progress=on_progress, phase="preflight",
        )
    log(f"Raw image preflighted: {size} bytes, SHA-256 {digest}")
    return GuardedImagePlan(path, path, "raw", size, size, digest)


def validate_source_unchanged(plan: GuardedImagePlan) -> None:
    if not plan.source_path.is_file() or plan.source_path.stat().st_size != plan.write_bytes:
        raise GuardedImageError("preflighted image source changed or disappeared")
    with plan.source_path.open("rb") as stream:
        digest, _ = _hash_stream(stream, limit=plan.write_bytes)
    if digest != plan.sha256:
        raise GuardedImageError("preflighted image source changed after validation")


def validate_ready_to_write(
    plan: GuardedImagePlan,
    expected: DiskIdentity,
    store: ProtectedHardwareStore,
    *,
    inventory_provider: Callable[[], list[DiskIdentity]] = query_windows_storage_inventory,
    image_disk_provider: Callable[[str], int] | None = None,
) -> EligibilityDecision:
    validate_source_unchanged(plan)
    kwargs = {"inventory_provider": inventory_provider}
    if image_disk_provider is not None:
        kwargs["image_disk_provider"] = image_disk_provider
    decision = revalidate_target(
        expected, store, image_path=str(plan.original_path), **kwargs
    )
    if not decision.eligible:
        raise GuardedRestoreError("target revalidation failed: " + "; ".join(decision.reasons))
    if decision.disk.size_bytes < plan.required_capacity:
        raise GuardedRestoreError("target no longer satisfies the image capacity requirement")
    return decision


def _flush_disk(disk) -> None:
    if os.name != "nt" or not ctypes.windll.kernel32.FlushFileBuffers(disk.h):
        raise OSError("FlushFileBuffers failed")


def _wait_for_partition_table(path: str, timeout_seconds: float = 12.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error = "partition table was not readable"
    while True:
        try:
            read_mbr_partitions_strict(path)
            return
        except PartitionReadError as exc:
            last_error = str(exc)
        if time.monotonic() >= deadline:
            raise GuardedRestoreError(last_error, target_not_trusted=True)
        time.sleep(0.5)


def _drive_letters(volumes: tuple[str, ...]) -> list[str]:
    return sorted({value[0].upper() for value in volumes if re.match(r"^[A-Za-z]:\\", value)})


def restore_and_verify(
    plan: GuardedImagePlan,
    expected: DiskIdentity,
    store: ProtectedHardwareStore,
    *,
    confirmed_disk_number: int,
    inventory_provider: Callable[[], list[DiskIdentity]] = query_windows_storage_inventory,
    image_disk_provider: Callable[[str], int] | None = None,
    disk_factory: Callable[..., Any] = pyimager.Win32Disk,
    volume_locker: Callable[..., list[Any]] = pyimager.lock_and_dismount_volumes,
    flush_disk: Callable[[Any], None] = _flush_disk,
    partition_waiter: Callable[[str], None] = _wait_for_partition_table,
    hash_config_provider: Callable[[], Any] = HashConfig,
    should_cancel: Callable[[], bool] | None = None,
    on_progress: Callable[[str, int, int], None] | None = None,
    on_log: Callable[[str], None] | None = None,
) -> GuardedRestoreResult:
    started = time.monotonic()
    if confirmed_disk_number != expected.disk_number:
        raise GuardedRestoreError("typed confirmation does not match the selected disk number")
    decision = validate_ready_to_write(
        plan, expected, store, inventory_provider=inventory_provider,
        image_disk_provider=image_disk_provider,
    )
    current = decision.disk
    log = on_log or (lambda _line: None)
    progress = on_progress or (lambda _phase, _done, _total: None)
    raw_path = current.raw_device_path

    with disk_factory(raw_path) as probe:
        if probe.size != current.size_bytes or probe.size < plan.required_capacity:
            raise GuardedRestoreError("target capacity changed before write access")
        if plan.write_bytes % probe.sector_size:
            raise GuardedRestoreError("image length is not aligned to the target sector size")

    locked = []
    bytes_written = 0
    cancelled = False
    try:
        locked = volume_locker(_drive_letters(current.mounted_volumes), on_log=log)
        with plan.source_path.open("rb") as source, disk_factory(raw_path, write=True) as target:
            target.seek(0)
            while bytes_written < plan.write_bytes:
                if should_cancel is not None and should_cancel():
                    cancelled = True
                    break
                block = source.read(min(CHUNK_BYTES, plan.write_bytes - bytes_written))
                if not block:
                    raise GuardedRestoreError("short source read during write", target_not_trusted=True)
                written = target.write(block)
                if written != len(block):
                    raise GuardedRestoreError("short target write", target_not_trusted=True)
                bytes_written += written
                progress("write", bytes_written, plan.write_bytes)
            flush_disk(target)
            if not cancelled and not target.update_properties():
                raise GuardedRestoreError("Windows disk-property refresh failed", target_not_trusted=True)
    except GuardedRestoreError:
        raise
    except Exception as exc:
        raise GuardedRestoreError(str(exc), target_not_trusted=bytes_written > 0) from exc
    finally:
        for volume in locked:
            volume.unlock()
            volume.close()

    if cancelled:
        return GuardedRestoreResult(
            bytes_written, plan.sha256, "", False, True, bytes_written > 0,
            time.monotonic() - started,
        )
    if bytes_written != plan.write_bytes:
        raise GuardedRestoreError("write length was incomplete", target_not_trusted=True)

    try:
        partition_waiter(raw_path)
    except GuardedRestoreError:
        raise
    except Exception as exc:
        raise GuardedRestoreError(
            f"partition table did not become readable: {exc}", target_not_trusted=True
        ) from exc
    target_hash = hashlib.sha256()
    verified_bytes = 0
    with disk_factory(raw_path) as target:
        target.seek(0)
        while verified_bytes < plan.write_bytes:
            if should_cancel is not None and should_cancel():
                return GuardedRestoreResult(
                    bytes_written, plan.sha256, target_hash.hexdigest(), False, True, True,
                    time.monotonic() - started,
                )
            block = target.read(min(CHUNK_BYTES, plan.write_bytes - verified_bytes))
            if not block:
                raise GuardedRestoreError("short target read during verification", target_not_trusted=True)
            target_hash.update(block)
            verified_bytes += len(block)
            progress("verify", verified_bytes, plan.write_bytes)
    target_digest = target_hash.hexdigest()
    if verified_bytes != plan.write_bytes or target_digest != plan.sha256:
        raise GuardedRestoreError("mandatory target read-back SHA-256 mismatch", target_not_trusted=True)
    log(f"Mandatory read-back verification passed: SHA-256 {target_digest}")
    if not _configured_policy_checks(
        plan,
        raw_path,
        disk_factory=disk_factory,
        hash_config_provider=hash_config_provider,
        should_cancel=should_cancel,
        on_progress=progress,
        on_log=log,
    ):
        return GuardedRestoreResult(
            bytes_written, plan.sha256, target_digest, True, True, True,
            time.monotonic() - started,
        )
    return GuardedRestoreResult(
        bytes_written, plan.sha256, target_digest, True, False, False,
        time.monotonic() - started,
    )


class GuardedRestoreWorker:
    def __init__(
        self,
        root,
        plan: GuardedImagePlan,
        disk: DiskIdentity,
        store: ProtectedHardwareStore,
        on_progress: Callable[[str, int], None],
        on_log: Callable[[str], None],
        on_done: Callable[[CloneStatus], None],
        *,
        restore_provider: Callable[..., GuardedRestoreResult] = restore_and_verify,
    ):
        self._root = root
        self.plan = plan
        self.disk = disk
        self.store = store
        self._on_progress = on_progress
        self._on_log = on_log
        self._on_done = on_done
        self._restore_provider = restore_provider
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self.status = CloneStatus.IDLE
        self.result: GuardedRestoreResult | None = None
        self.error: Exception | None = None

    def start(self) -> None:
        if self.status == CloneStatus.RUNNING:
            return
        self.status = CloneStatus.RUNNING
        self._cancel.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._cancel.set()

    def _run(self) -> None:
        try:
            self.result = self._restore_provider(
                self.plan,
                self.disk,
                self.store,
                confirmed_disk_number=self.disk.disk_number,
                should_cancel=self._cancel.is_set,
                on_progress=self._progress,
                on_log=lambda line: self._call(self._on_log, line),
            )
            status = CloneStatus.STOPPED if self.result.cancelled else CloneStatus.DONE
        except Exception as exc:  # surfaced verbatim in the guarded copyable log
            self.error = exc
            status = CloneStatus.FAILED
        finally:
            self.plan.cleanup()
        self.status = status
        self._call(self._on_done, status)

    def _progress(self, phase: str, done: int, total: int) -> None:
        percent = int(done * 100 / total) if total else 0
        self._call(self._on_progress, phase, percent)

    def _call(self, callback, *args) -> None:
        try:
            self._root.after(0, callback, *args)
        except Exception:
            pass


class GuardedRestoreCoordinator:
    def __init__(
        self,
        root,
        window,
        *,
        store: ProtectedHardwareStore | None = None,
        worker_factory: Callable[..., GuardedRestoreWorker] = GuardedRestoreWorker,
    ):
        self._root = root
        self._window = window
        self._store = store or ProtectedHardwareStore()
        self._worker_factory = worker_factory
        self.worker: GuardedRestoreWorker | None = None

    @property
    def busy(self) -> bool:
        return self.worker is not None and self.worker.status == CloneStatus.RUNNING

    def prepare(self, disk: DiskIdentity, plan: GuardedImagePlan) -> None:
        if self.busy:
            plan.cleanup()
            self._window.guarded_log("[Guarded] A restore is already active.", warning=True)
            return
        worker = self._worker_factory(
            self._root,
            plan,
            disk,
            self._store,
            self._window.guarded_set_progress,
            lambda line: self._window.guarded_log(f"[PyImager] {line}"),
            self._done,
        )
        self.worker = worker
        self._window.guarded_log(f"[Guarded] Starting verified restore to Disk {disk.disk_number}.")
        worker.start()

    def stop(self) -> None:
        if self.busy and self.worker is not None:
            self._window.guarded_log("[Guarded] Stop requested; waiting for the chunk boundary.")
            self.worker.stop()

    def _done(self, status: CloneStatus) -> None:
        worker = self.worker
        if worker is None:
            return
        if status == CloneStatus.DONE and worker.result is not None and worker.result.verified:
            message = (
                f"[Guarded] SUCCESS: wrote and verified {worker.result.bytes_written} bytes "
                f"on Disk {worker.disk.disk_number}."
            )
            self._window.guarded_finish_attempt(message)
        elif status == CloneStatus.STOPPED:
            partial = bool(worker.result and worker.result.target_not_trusted)
            message = (
                "[Guarded] CANCELLED: target is incomplete or not fully verified; "
                "recover it or perform a complete reflash."
                if partial
                else "[Guarded] Cancelled before the target was changed."
            )
            self._window.guarded_finish_attempt(message, warning=partial)
        else:
            error = worker.error or GuardedRestoreError("guarded restore failed")
            unsafe = bool(getattr(error, "target_not_trusted", True))
            message = f"[Guarded] FAILED: {error}"
            if unsafe:
                message += " Target may be partial or unverified; recover it or reflash completely."
            self._window.guarded_finish_attempt(message, warning=True)
        self.worker = None
