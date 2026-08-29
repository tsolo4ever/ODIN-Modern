"""Preflight and range primitives for guarded native ODIN restores."""

from __future__ import annotations

import hashlib
import os
import tempfile
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from odin_container import (
    BitmapScheme,
    CompressionScheme,
    OdinFormatError,
    OdinImage,
    SplitMember,
)


CHUNK_BYTES = 8 << 20
MIN_SECTOR_BYTES = 512


class OdinRestoreSourceError(ValueError):
    pass


class OdinRestoreOperationError(RuntimeError):
    def __init__(self, message: str, *, target_not_trusted: bool = False):
        super().__init__(message)
        self.target_not_trusted = target_not_trusted


@dataclass(frozen=True)
class OdinMemberSnapshot:
    path: Path
    size: int
    mtime_ns: int
    sha256: str


@dataclass(frozen=True)
class OdinRestoreSource:
    original_path: Path
    payload_path: Path
    logical_size: int
    payload_size: int
    payload_sha256: str
    allocated_ranges: tuple[tuple[int, int], ...]
    source_members: tuple[OdinMemberSnapshot, ...]
    compression_name: str
    verification_strategy: str
    used_block_layout: bool

    @property
    def used_blocks(self) -> bool:
        return self.used_block_layout

    def cleanup(self) -> None:
        self.payload_path.unlink(missing_ok=True)


def _cancelled(should_cancel: Callable[[], bool] | None) -> bool:
    return should_cancel is not None and should_cancel()


def _hash_file(
    path: Path,
    *,
    should_cancel: Callable[[], bool] | None = None,
) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            if _cancelled(should_cancel):
                raise OdinRestoreSourceError("ODIN image preflight was cancelled")
            block = stream.read(CHUNK_BYTES)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _snapshot_members(
    members: tuple[SplitMember, ...],
    *,
    should_cancel: Callable[[], bool] | None = None,
) -> tuple[OdinMemberSnapshot, ...]:
    return tuple(
        OdinMemberSnapshot(
            member.path,
            member.size,
            member.mtime_ns,
            _hash_file(member.path, should_cancel=should_cancel),
        )
        for member in members
    )


def preflight_odin_source(
    image_path: str | os.PathLike[str],
    target_capacity: int,
    *,
    should_cancel: Callable[[], bool] | None = None,
    on_progress: Callable[[str, int, int], None] | None = None,
    on_log: Callable[[str], None] | None = None,
) -> OdinRestoreSource:
    """Fully validate and spool one immutable packed ODIN payload."""

    original = Path(image_path).resolve()
    descriptor, temporary_name = tempfile.mkstemp(prefix="odinm-odin-", suffix=".payload")
    os.close(descriptor)
    payload_path = Path(temporary_name)
    digest = hashlib.sha256()
    crc32 = 0
    written = 0
    log = on_log or (lambda _line: None)
    try:
        with OdinImage.open(original) as image:
            header = image.header
            if header.volume_size <= 0 or header.volume_size % MIN_SECTOR_BYTES:
                raise OdinRestoreSourceError(
                    "ODIN logical volume length is empty or not 512-byte aligned"
                )
            if header.volume_size > target_capacity:
                raise OdinRestoreSourceError(
                    f"ODIN volume ({header.volume_size} bytes) is larger than "
                    f"target ({target_capacity} bytes)"
                )
            used_blocks = header.volume_bitmap_encoding_scheme != BitmapScheme.ALL_BLOCKS
            ranges = image.allocated_ranges() if used_blocks else ((0, header.volume_size),)
            if any(
                offset % MIN_SECTOR_BYTES or length <= 0 or length % MIN_SECTOR_BYTES
                for offset, length in ranges
            ):
                raise OdinRestoreSourceError(
                    "ODIN allocated ranges are not valid 512-byte disk-write regions"
                )
            if sum(length for _offset, length in ranges) != header.used_size:
                raise OdinRestoreSourceError(
                    "ODIN allocated ranges do not match the declared used size"
                )
            with payload_path.open("wb") as output:
                for block in image.iter_payload_chunks():
                    if _cancelled(should_cancel):
                        raise OdinRestoreSourceError("ODIN image preflight was cancelled")
                    output.write(block)
                    digest.update(block)
                    crc32 = zlib.crc32(block, crc32)
                    written += len(block)
                    if on_progress:
                        on_progress("preflight", written, header.used_size)
                output.flush()
                os.fsync(output.fileno())
            if written != header.used_size:
                raise OdinRestoreSourceError(
                    f"ODIN payload produced {written} bytes; expected {header.used_size}"
                )
            crc32 &= 0xFFFFFFFF
            if image.stored_crc32 is not None and crc32 != image.stored_crc32:
                raise OdinRestoreSourceError(
                    f"ODIN CRC32 mismatch: stored {image.stored_crc32:08x}, actual {crc32:08x}"
                )
            members = _snapshot_members(image.members, should_cancel=should_cancel)
            strategy = "allocated-range SHA-256" if used_blocks else "full logical-stream SHA-256"
            source = OdinRestoreSource(
                original,
                payload_path,
                header.volume_size,
                written,
                digest.hexdigest(),
                ranges,
                members,
                CompressionScheme(header.compression_scheme).name,
                strategy,
                used_blocks,
            )
        log(
            f"ODIN container fully validated: {source.payload_size} payload bytes, "
            f"{source.compression_name}, {source.verification_strategy}, "
            f"SHA-256 {source.payload_sha256}"
        )
        return source
    except (OdinFormatError, OSError, OdinRestoreSourceError) as exc:
        payload_path.unlink(missing_ok=True)
        if isinstance(exc, OdinRestoreSourceError):
            raise
        raise OdinRestoreSourceError(f"ODIN image preflight failed: {exc}") from exc


def validate_odin_source_unchanged(source: OdinRestoreSource) -> None:
    """Reopen and hash every source member before any target write."""

    try:
        with OdinImage.open(source.original_path) as image:
            current = image.members
            if len(current) != len(source.source_members):
                raise OdinRestoreSourceError("ODIN split member set changed after preflight")
            for member, expected in zip(current, source.source_members, strict=True):
                if (
                    member.path != expected.path
                    or member.size != expected.size
                    or member.mtime_ns != expected.mtime_ns
                    or _hash_file(member.path) != expected.sha256
                ):
                    raise OdinRestoreSourceError(
                        f"ODIN source member changed after preflight: {expected.path}"
                    )
    except OdinFormatError as exc:
        raise OdinRestoreSourceError(
            f"ODIN source changed or became invalid after preflight: {exc}"
        ) from exc


def write_payload_ranges(
    source: OdinRestoreSource,
    target: object,
    *,
    should_cancel: Callable[[], bool] | None = None,
    on_progress: Callable[[str, int, int], None] | None = None,
) -> tuple[int, bool]:
    """Write the preflighted packed payload into its logical target ranges."""

    written = 0
    try:
        with source.payload_path.open("rb") as payload:
            for offset, length in source.allocated_ranges:
                target.seek(offset)  # type: ignore[attr-defined]
                remaining = length
                while remaining:
                    if _cancelled(should_cancel):
                        return written, True
                    block = payload.read(min(CHUNK_BYTES, remaining))
                    if not block:
                        raise OdinRestoreOperationError(
                            "short ODIN payload read during write",
                            target_not_trusted=written > 0,
                        )
                    count = target.write(block)  # type: ignore[attr-defined]
                    if count != len(block):
                        raise OdinRestoreOperationError(
                            "short ODIN target write", target_not_trusted=True
                        )
                    written += count
                    remaining -= count
                    if on_progress:
                        on_progress("write", written, source.payload_size)
            if payload.read(1):
                raise OdinRestoreOperationError(
                    "ODIN payload contains bytes beyond its allocated ranges",
                    target_not_trusted=written > 0,
                )
    except OdinRestoreOperationError:
        raise
    except Exception as exc:
        raise OdinRestoreOperationError(str(exc), target_not_trusted=written > 0) from exc
    return written, False


def verify_payload_ranges(
    source: OdinRestoreSource,
    target: object,
    *,
    should_cancel: Callable[[], bool] | None = None,
    on_progress: Callable[[str, int, int], None] | None = None,
) -> tuple[str, int, bool]:
    """Hash target ranges in the same order used by the packed ODIN payload."""

    digest = hashlib.sha256()
    verified = 0
    try:
        for offset, length in source.allocated_ranges:
            target.seek(offset)  # type: ignore[attr-defined]
            remaining = length
            while remaining:
                if _cancelled(should_cancel):
                    return digest.hexdigest(), verified, True
                block = target.read(min(CHUNK_BYTES, remaining))  # type: ignore[attr-defined]
                if not block:
                    raise OdinRestoreOperationError(
                        "short ODIN target read during verification",
                        target_not_trusted=True,
                    )
                digest.update(block)
                verified += len(block)
                remaining -= len(block)
                if on_progress:
                    on_progress("verify", verified, source.payload_size)
    except OdinRestoreOperationError:
        raise
    except Exception as exc:
        raise OdinRestoreOperationError(str(exc), target_not_trusted=True) from exc
    return digest.hexdigest(), verified, False


__all__ = [
    "OdinRestoreOperationError",
    "OdinRestoreSource",
    "OdinRestoreSourceError",
    "preflight_odin_source",
    "validate_odin_source_unchanged",
    "verify_payload_ranges",
    "write_payload_ranges",
]
