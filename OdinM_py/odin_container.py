"""Strict, read-only support for legacy ODIN v1.x image containers.

The module owns the on-disk format interpretation used by the Python tools.  It
never opens physical disks and never writes image containers.  Native writing
is intentionally reserved for the later writer phase.
"""

from __future__ import annotations

import bisect
import bz2
import enum
import os
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO
from collections.abc import Iterator

try:
    import lz4.frame as _lz4_frame
except ImportError:  # pragma: no cover - exercised by packaged startup checks later
    _lz4_frame = None

try:
    import zstandard as _zstandard
except ImportError:  # pragma: no cover - exercised by packaged startup checks later
    _zstandard = None


ODIN_MAGIC = bytes.fromhex("737b4d1d01fae140b0945267d8fa0be7")
HEADER_FORMAT = "<16sHHIIIIIIII4xQQQQQQQQQ"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
CRC32_SIZE = 4
DEFAULT_CHUNK_SIZE = 1024 * 1024
CODEC_INPUT_CHUNK_SIZE = 64 * 1024
MAX_SPLIT_MEMBERS = 10_000
MAX_LOGICAL_SIZE = (1 << 63) - 1

assert HEADER_SIZE == 128


class OdinFormatError(ValueError):
    """The input is not a complete, internally consistent ODIN container."""


class CompressionScheme(enum.IntEnum):
    NONE = 0
    ZLIB = 1
    BZIP2 = 2
    LZ4 = 3
    LZ4_HC = 4
    ZSTD = 5


class VerifyScheme(enum.IntEnum):
    NONE = 0
    CRC32 = 1


class BitmapScheme(enum.IntEnum):
    ALL_BLOCKS = 0
    SIMPLE_COMPRESSED_RUN_LENGTH = 1


class VolumeType(enum.IntEnum):
    HARD_DISK = 0
    PARTITION = 1


_HEADER_FIELDS = (
    "guid",
    "version_major",
    "version_minor",
    "compression_scheme",
    "verify_scheme",
    "volume_bitmap_encoding_scheme",
    "volume_type",
    "file_count",
    "cluster_size",
    "verify_length",
    "comment_length",
    "volume_bitmap_offset",
    "volume_bitmap_length",
    "verify_offset",
    "comment_offset",
    "data_offset",
    "data_size",
    "used_size",
    "volume_size",
    "file_size",
)


def _enum_value(enum_type: type[enum.IntEnum], value: int, label: str) -> enum.IntEnum:
    try:
        return enum_type(value)
    except ValueError as exc:
        raise OdinFormatError(f"unsupported ODIN {label} value {value}") from exc


def _checked_end(offset: int, length: int, label: str) -> int:
    if offset < 0 or length < 0:
        raise OdinFormatError(f"ODIN {label} offset/length cannot be negative")
    end = offset + length
    if end > MAX_LOGICAL_SIZE:
        raise OdinFormatError(f"ODIN {label} range overflows the supported file size")
    return end


@dataclass(frozen=True)
class OdinHeader:
    guid: bytes = ODIN_MAGIC
    version_major: int = 1
    version_minor: int = 0
    compression_scheme: int = int(CompressionScheme.NONE)
    verify_scheme: int = int(VerifyScheme.NONE)
    volume_bitmap_encoding_scheme: int = int(BitmapScheme.ALL_BLOCKS)
    volume_type: int = int(VolumeType.HARD_DISK)
    file_count: int = 0
    cluster_size: int = 0
    verify_length: int = 0
    comment_length: int = 0
    volume_bitmap_offset: int = 0
    volume_bitmap_length: int = 0
    verify_offset: int = 0
    comment_offset: int = 0
    data_offset: int = HEADER_SIZE
    data_size: int = 0
    used_size: int = 0
    volume_size: int = 0
    file_size: int = HEADER_SIZE

    @classmethod
    def unpack(cls, blob: bytes) -> OdinHeader:
        if len(blob) != HEADER_SIZE:
            raise OdinFormatError(
                f"ODIN header must be exactly {HEADER_SIZE} bytes; got {len(blob)}"
            )
        return cls(*struct.unpack(HEADER_FORMAT, blob))

    def pack(self) -> bytes:
        self.validate()
        return struct.pack(HEADER_FORMAT, *(getattr(self, name) for name in _HEADER_FIELDS))

    @property
    def compression(self) -> int:
        return self.compression_scheme

    @property
    def bitmap_scheme(self) -> int:
        return self.volume_bitmap_encoding_scheme

    @property
    def version(self) -> tuple[int, int]:
        return self.version_major, self.version_minor

    @property
    def is_raw_sectors(self) -> bool:
        return (
            self.compression_scheme == CompressionScheme.NONE
            and self.volume_bitmap_encoding_scheme == BitmapScheme.ALL_BLOCKS
        )

    @property
    def raw(self) -> dict[str, object]:
        legacy_names = (
            "guid",
            "versionMajor",
            "versionMinor",
            "compressionScheme",
            "verifyScheme",
            "volumeBitmapEncodingScheme",
            "volumeType",
            "fileCount",
            "clusterSize",
            "verifyLength",
            "commentLength",
            "volumeBitmapOffset",
            "volumeBitmapLength",
            "verifyOffset",
            "commentOffset",
            "dataOffset",
            "dataSize",
            "usedSize",
            "volumeSize",
            "fileSize",
        )
        return dict(
            zip(legacy_names, (getattr(self, name) for name in _HEADER_FIELDS), strict=True)
        )

    def validate(self, logical_size: int | None = None) -> None:
        if self.guid != ODIN_MAGIC:
            raise OdinFormatError("ODIN header magic does not match")
        if self.version_major != 1:
            raise OdinFormatError(
                f"unsupported ODIN major version {self.version_major}; only 1.x is supported"
            )
        _enum_value(CompressionScheme, self.compression_scheme, "compression scheme")
        verify = _enum_value(VerifyScheme, self.verify_scheme, "verification scheme")
        bitmap = _enum_value(BitmapScheme, self.volume_bitmap_encoding_scheme, "bitmap scheme")
        _enum_value(VolumeType, self.volume_type, "volume type")

        if self.file_count < 0 or self.file_count > MAX_SPLIT_MEMBERS:
            raise OdinFormatError(
                f"ODIN split member count {self.file_count} is outside the supported range"
            )
        if self.file_size < HEADER_SIZE or self.file_size > MAX_LOGICAL_SIZE:
            raise OdinFormatError("ODIN logical file size is invalid")
        if logical_size is not None and self.file_size != logical_size:
            raise OdinFormatError(
                f"ODIN header declares {self.file_size} bytes but the file set has "
                f"{logical_size} bytes"
            )

        regions: list[tuple[int, int, str]] = [(0, HEADER_SIZE, "header")]

        if verify == VerifyScheme.NONE:
            if self.verify_length != 0 or self.verify_offset != 0:
                raise OdinFormatError("ODIN verify-none metadata must have zero offset and length")
        else:
            if self.verify_length != CRC32_SIZE:
                raise OdinFormatError("ODIN CRC32 verification metadata must be exactly 4 bytes")
            regions.append(
                (
                    self.verify_offset,
                    _checked_end(self.verify_offset, self.verify_length, "CRC32"),
                    "CRC32",
                )
            )

        if self.comment_length == 0:
            if self.comment_offset != 0:
                raise OdinFormatError("empty ODIN comments must have a zero offset")
        else:
            if self.comment_length % 2:
                raise OdinFormatError("ODIN comment length must be even UTF-16LE bytes")
            regions.append(
                (
                    self.comment_offset,
                    _checked_end(self.comment_offset, self.comment_length, "comment"),
                    "comment",
                )
            )

        if bitmap == BitmapScheme.ALL_BLOCKS:
            if self.volume_bitmap_offset != 0 or self.volume_bitmap_length != 0:
                raise OdinFormatError("all-block ODIN images must not contain bitmap metadata")
            if self.used_size != self.volume_size:
                raise OdinFormatError("all-block ODIN used size must equal its volume size")
        else:
            if self.cluster_size <= 0:
                raise OdinFormatError("used-block ODIN images require a non-zero cluster size")
            if self.volume_bitmap_length <= 0:
                raise OdinFormatError("used-block ODIN images require an allocation bitmap")
            if self.volume_size % self.cluster_size:
                raise OdinFormatError("ODIN volume size is not an exact number of clusters")
            if self.used_size % self.cluster_size:
                raise OdinFormatError("ODIN used size is not cluster aligned")
            regions.append(
                (
                    self.volume_bitmap_offset,
                    _checked_end(
                        self.volume_bitmap_offset,
                        self.volume_bitmap_length,
                        "volume bitmap",
                    ),
                    "volume bitmap",
                )
            )

        if self.data_offset < HEADER_SIZE:
            raise OdinFormatError("ODIN data offset overlaps the header")
        if self.data_size < 0 or self.used_size < 0 or self.volume_size < 0:
            raise OdinFormatError("ODIN size fields cannot be negative")
        if self.used_size > self.volume_size:
            raise OdinFormatError("ODIN used size exceeds the original volume size")
        if self.compression_scheme == CompressionScheme.NONE and self.data_size != self.used_size:
            raise OdinFormatError("uncompressed ODIN data size must equal its used size")
        regions.append(
            (
                self.data_offset,
                _checked_end(self.data_offset, self.data_size, "data"),
                "data",
            )
        )

        for start, end, label in regions:
            if start < 0 or end > self.file_size:
                raise OdinFormatError(f"ODIN {label} range lies outside the declared file size")
        ordered = sorted(regions)
        for previous, current in zip(ordered, ordered[1:], strict=False):
            if current[0] < previous[1]:
                raise OdinFormatError(f"ODIN {current[2]} range overlaps the {previous[2]} range")


@dataclass(frozen=True)
class SplitMember:
    path: Path
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class AllocationRun:
    cluster_offset: int
    cluster_count: int
    allocated: bool

    def byte_offset(self, cluster_size: int) -> int:
        return self.cluster_offset * cluster_size

    def byte_length(self, cluster_size: int) -> int:
        return self.cluster_count * cluster_size


def split_member_path(base_path: str | os.PathLike[str], index: int) -> Path:
    if index < 0 or index > 9999:
        raise OdinFormatError(f"ODIN split member index {index} is outside 0000-9999")
    base = Path(base_path)
    return base.with_name(f"{base.stem}{index:04d}{base.suffix}")


def split_base_path(member_path: str | os.PathLike[str]) -> Path | None:
    member = Path(member_path)
    stem = member.stem
    if len(stem) < 4 or not stem[-4:].isdigit():
        return None
    return member.with_name(f"{stem[:-4]}{member.suffix}")


def _resolve_initial_path(path: Path) -> Path:
    if path.is_file():
        numbered_base = split_base_path(path)
        if numbered_base is not None:
            first = split_member_path(numbered_base, 0)
            if first.is_file():
                return first
        return path
    first = split_member_path(path, 0)
    if first.is_file():
        return first
    raise OdinFormatError(f"ODIN image does not exist: {path}")


def _member(path: Path) -> SplitMember:
    try:
        stat = path.stat()
    except OSError as exc:
        raise OdinFormatError(f"ODIN split member is unavailable: {path}") from exc
    if not path.is_file() or stat.st_size <= 0:
        raise OdinFormatError(f"ODIN split member is not a non-empty file: {path}")
    return SplitMember(path=path.resolve(), size=stat.st_size, mtime_ns=stat.st_mtime_ns)


class LogicalSplitReader:
    """Seekable read-only view over one ODIN file or its numbered split members."""

    def __init__(self, members: tuple[SplitMember, ...], logical_size: int):
        if not members:
            raise OdinFormatError("ODIN file set has no members")
        if sum(member.size for member in members) != logical_size:
            raise OdinFormatError("ODIN split member sizes do not equal the declared file size")
        self.members = members
        self.logical_size = logical_size
        starts: list[int] = []
        position = 0
        for member in members:
            starts.append(position)
            position += member.size
        self._starts = tuple(starts)
        self._files: list[BinaryIO] = [open(member.path, "rb") for member in members]
        self._position = 0

    @classmethod
    def open(cls, initial_path: Path, header: OdinHeader) -> LogicalSplitReader:
        members: tuple[SplitMember, ...]
        if header.file_count == 0:
            members = (_member(initial_path),)
        else:
            base = split_base_path(initial_path)
            if base is None:
                raise OdinFormatError("split ODIN header was not found in a numbered 0000 member")
            members = tuple(
                _member(split_member_path(base, index)) for index in range(header.file_count)
            )
            first_size = members[0].size
            if any(item.size != first_size for item in members[:-1]):
                raise OdinFormatError("non-final ODIN split members do not share one chunk size")
            if members[-1].size > first_size:
                raise OdinFormatError("final ODIN split member is larger than the first member")
            if split_member_path(base, header.file_count).exists():
                raise OdinFormatError("ODIN split set contains an undeclared extra member")
        return cls(members, header.file_size)

    def close(self) -> None:
        for stream in self._files:
            stream.close()

    def __enter__(self) -> LogicalSplitReader:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def tell(self) -> int:
        return self._position

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        if whence == os.SEEK_CUR:
            offset += self._position
        elif whence == os.SEEK_END:
            offset += self.logical_size
        elif whence != os.SEEK_SET:
            raise ValueError(f"unsupported seek mode {whence}")
        if offset < 0:
            raise ValueError("cannot seek before the logical file start")
        self._position = min(offset, self.logical_size)
        return self._position

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = self.logical_size - self._position
        size = min(size, self.logical_size - self._position)
        if size <= 0:
            return b""
        output = bytearray()
        while size:
            index = bisect.bisect_right(self._starts, self._position) - 1
            member = self.members[index]
            member_offset = self._position - self._starts[index]
            take = min(size, member.size - member_offset)
            stream = self._files[index]
            stream.seek(member_offset)
            data = stream.read(take)
            if len(data) != take:
                raise OdinFormatError(f"short read from ODIN member {member.path}")
            output.extend(data)
            self._position += take
            size -= take
        return bytes(output)

    def read_at(self, offset: int, length: int) -> bytes:
        if offset < 0 or length < 0 or offset + length > self.logical_size:
            raise OdinFormatError("ODIN logical read lies outside the file set")
        previous = self._position
        try:
            self.seek(offset)
            data = self.read(length)
            if len(data) != length:
                raise OdinFormatError("short read from ODIN logical file set")
            return data
        finally:
            self._position = previous

    def iter_range(
        self, offset: int, length: int, chunk_size: int = DEFAULT_CHUNK_SIZE
    ) -> Iterator[bytes]:
        if chunk_size <= 0:
            raise ValueError("chunk size must be positive")
        if offset < 0 or length < 0 or offset + length > self.logical_size:
            raise OdinFormatError("ODIN logical range lies outside the file set")
        self.seek(offset)
        remaining = length
        while remaining:
            data = self.read(min(chunk_size, remaining))
            if not data:
                raise OdinFormatError("ODIN logical range ended early")
            remaining -= len(data)
            yield data


def encode_run_lengths(tokens: tuple[int, ...]) -> bytes:
    """Encode alternating used/free run lengths using ODIN's four-token tuples."""
    if not tokens or len(tokens) % 2:
        raise OdinFormatError("ODIN run lengths must contain complete used/free pairs")
    if any(value < 0 or value > 0xFFFFFFFFFFFFFFFF for value in tokens):
        raise OdinFormatError("ODIN run length is outside the unsigned 64-bit range")
    if any(value == 0 for value in tokens[1:-1]):
        raise OdinFormatError("ODIN run lengths contain an interior zero-length run")

    values = list(tokens)
    output = bytearray()
    while values:
        group = values[:4]
        del values[:4]
        group.extend([0] * (4 - len(group)))
        header = 0
        encoded = bytearray()
        for index, value in enumerate(group):
            if value <= 0xFF:
                width, tag = 1, 0
            elif value <= 0xFFFF:
                width, tag = 2, 1
            elif value <= 0xFFFFFFFF:
                width, tag = 4, 2
            else:
                width, tag = 8, 3
            header |= tag << (index * 2)
            encoded.extend(value.to_bytes(width, "little"))
        output.append(header)
        output.extend(encoded)
    return bytes(output)


def decode_run_lengths(blob: bytes, expected_clusters: int) -> tuple[int, ...]:
    if not blob:
        raise OdinFormatError("ODIN allocation bitmap is empty")
    tokens: list[int] = []
    position = 0
    max_tokens = min(len(blob) * 4, expected_clusters * 2 + 4)
    running_clusters = 0
    while position < len(blob):
        header = blob[position]
        position += 1
        for index in range(4):
            width = (1, 2, 4, 8)[(header >> (index * 2)) & 0x03]
            end = position + width
            if end > len(blob):
                raise OdinFormatError("ODIN allocation bitmap token is truncated")
            value = int.from_bytes(blob[position:end], "little")
            position = end
            tokens.append(value)
            if len(tokens) > max_tokens:
                raise OdinFormatError("ODIN allocation bitmap contains too many tokens")

    while tokens and tokens[-1] == 0:
        tokens.pop()
    if not tokens:
        raise OdinFormatError("ODIN allocation bitmap contains no volume runs")
    if len(tokens) % 2:
        tokens.append(0)
    if len(tokens) < 2:
        raise OdinFormatError("ODIN allocation bitmap does not contain complete used/free pairs")
    if any(value == 0 for value in tokens[1:-1]):
        raise OdinFormatError("ODIN allocation bitmap contains an interior zero-length run")
    for value in tokens:
        running_clusters += value
        if running_clusters > expected_clusters:
            raise OdinFormatError("ODIN allocation bitmap exceeds the declared volume size")
    if running_clusters != expected_clusters:
        raise OdinFormatError(
            "ODIN allocation bitmap does not terminate at the declared cluster count"
        )
    return tuple(tokens)


def codec_available(scheme: int | CompressionScheme) -> bool:
    scheme = CompressionScheme(scheme)
    if scheme in (CompressionScheme.LZ4, CompressionScheme.LZ4_HC):
        return _lz4_frame is not None
    if scheme == CompressionScheme.ZSTD:
        return _zstandard is not None
    return True


class _PayloadReader:
    def __init__(self, chunks: Iterator[bytes]):
        self._chunks = chunks
        self._buffer = bytearray()
        self._eof = False

    def read(self, length: int) -> bytes:
        if length < 0:
            raise ValueError("payload read length cannot be negative")
        while len(self._buffer) < length and not self._eof:
            try:
                self._buffer.extend(next(self._chunks))
            except StopIteration:
                self._eof = True
        result = bytes(self._buffer[:length])
        del self._buffer[:length]
        if len(result) != length:
            raise OdinFormatError("ODIN uncompressed payload ended early")
        return result

    def skip(self, length: int) -> None:
        remaining = length
        while remaining:
            remaining -= len(self.read(min(remaining, DEFAULT_CHUNK_SIZE)))


class OdinImage:
    """Validated ODIN container with bounded, repeatable read operations."""

    def __init__(self, path: Path, header: OdinHeader, reader: LogicalSplitReader):
        self.path = path
        self.header = header
        self.reader = reader
        self.comment = self._read_comment()
        self.stored_crc32 = self._read_crc32()
        self._bitmap_tokens = self._read_bitmap_tokens()

    @classmethod
    def open(cls, path: str | os.PathLike[str]) -> OdinImage:
        initial = _resolve_initial_path(Path(path))
        try:
            with open(initial, "rb") as stream:
                blob = stream.read(HEADER_SIZE)
        except OSError as exc:
            raise OdinFormatError(f"cannot read ODIN image: {initial}") from exc
        if len(blob) != HEADER_SIZE:
            raise OdinFormatError("ODIN first member is shorter than its header")
        header = OdinHeader.unpack(blob)
        header.validate()
        reader = LogicalSplitReader.open(initial, header)
        try:
            header.validate(reader.logical_size)
            return cls(initial.resolve(), header, reader)
        except Exception:
            reader.close()
            raise

    def close(self) -> None:
        self.reader.close()

    def __enter__(self) -> OdinImage:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    @property
    def members(self) -> tuple[SplitMember, ...]:
        return self.reader.members

    def _read_comment(self) -> str:
        header = self.header
        if not header.comment_length:
            return ""
        blob = self.reader.read_at(header.comment_offset, header.comment_length)
        try:
            return blob.decode("utf-16-le", errors="strict")
        except UnicodeDecodeError as exc:
            raise OdinFormatError("ODIN comment is not valid UTF-16LE") from exc

    def _read_crc32(self) -> int | None:
        header = self.header
        if header.verify_scheme == VerifyScheme.NONE:
            return None
        return int.from_bytes(
            self.reader.read_at(header.verify_offset, header.verify_length), "little"
        )

    def _read_bitmap_tokens(self) -> tuple[int, ...]:
        header = self.header
        if header.volume_bitmap_encoding_scheme == BitmapScheme.ALL_BLOCKS:
            return ()
        blob = self.reader.read_at(header.volume_bitmap_offset, header.volume_bitmap_length)
        tokens = decode_run_lengths(blob, header.volume_size // header.cluster_size)
        allocated_clusters = sum(tokens[0::2])
        if allocated_clusters * header.cluster_size != header.used_size:
            raise OdinFormatError(
                "ODIN allocation bitmap used bytes do not match the declared used size"
            )
        return tokens

    def allocation_runs(self) -> tuple[AllocationRun, ...]:
        if not self._bitmap_tokens:
            clusters = (
                self.header.volume_size // self.header.cluster_size
                if self.header.cluster_size
                else 0
            )
            return (AllocationRun(0, clusters, True),) if clusters else ()
        result: list[AllocationRun] = []
        cluster_offset = 0
        for index, count in enumerate(self._bitmap_tokens):
            if count:
                result.append(AllocationRun(cluster_offset, count, index % 2 == 0))
            cluster_offset += count
        return tuple(result)

    def allocated_ranges(self) -> tuple[tuple[int, int], ...]:
        cluster_size = self.header.cluster_size
        return tuple(
            (run.byte_offset(cluster_size), run.byte_length(cluster_size))
            for run in self.allocation_runs()
            if run.allocated
        )

    def iter_stored_chunks(self, chunk_size: int = CODEC_INPUT_CHUNK_SIZE) -> Iterator[bytes]:
        yield from self.reader.iter_range(
            self.header.data_offset, self.header.data_size, chunk_size
        )

    def _checked_output(self, chunks: Iterator[bytes], expected_size: int) -> Iterator[bytes]:
        produced = 0
        for chunk in chunks:
            if not chunk:
                continue
            produced += len(chunk)
            if produced > expected_size:
                raise OdinFormatError("ODIN codec expanded beyond the declared used size")
            yield chunk
        if produced != expected_size:
            raise OdinFormatError(f"ODIN codec produced {produced} bytes; expected {expected_size}")

    def _decompress_zlib(self) -> Iterator[bytes]:
        decoder = zlib.decompressobj()
        source = iter(self.iter_stored_chunks())
        ended = False
        for compressed in source:
            pending = compressed
            while pending:
                output = decoder.decompress(pending, DEFAULT_CHUNK_SIZE)
                pending = decoder.unconsumed_tail
                if output:
                    yield output
                if decoder.eof:
                    if decoder.unused_data or pending or next(source, None) is not None:
                        raise OdinFormatError("ODIN zlib stream contains trailing data")
                    ended = True
                    break
                if not pending:
                    break
            if ended:
                break
        if not decoder.eof:
            raise OdinFormatError("ODIN zlib stream ended before its frame completed")
        flushed = decoder.flush()
        if flushed:
            yield flushed

    def _decompress_bounded(self, decoder: object, label: str) -> Iterator[bytes]:
        source = iter(self.iter_stored_chunks())
        ended = False
        for compressed in source:
            output = decoder.decompress(compressed, DEFAULT_CHUNK_SIZE)  # type: ignore[attr-defined]
            if output:
                yield output
            while not decoder.needs_input and not decoder.eof:  # type: ignore[attr-defined]
                output = decoder.decompress(b"", DEFAULT_CHUNK_SIZE)  # type: ignore[attr-defined]
                if output:
                    yield output
            if decoder.eof:  # type: ignore[attr-defined]
                if decoder.unused_data or next(source, None) is not None:  # type: ignore[attr-defined]
                    raise OdinFormatError(f"ODIN {label} stream contains trailing data")
                ended = True
                break
        if not ended:
            raise OdinFormatError(f"ODIN {label} stream ended before its frame completed")

    def _decompress_zstd(self) -> Iterator[bytes]:
        if _zstandard is None:
            raise OdinFormatError("ODIN Zstd support is unavailable; install zstandard")
        decoder = _zstandard.ZstdDecompressor().decompressobj()
        source = iter(self.iter_stored_chunks())
        ended = False
        for compressed in source:
            output = decoder.decompress(compressed)
            if output:
                yield output
            if decoder.eof:
                if decoder.unused_data or decoder.unconsumed_tail or next(source, None) is not None:
                    raise OdinFormatError("ODIN Zstd stream contains trailing data")
                ended = True
                break
        if not ended:
            raise OdinFormatError("ODIN Zstd stream ended before its frame completed")
        flushed = decoder.flush()
        if flushed:
            yield flushed

    def iter_payload_chunks(self) -> Iterator[bytes]:
        scheme = CompressionScheme(self.header.compression_scheme)
        try:
            if scheme == CompressionScheme.NONE:
                decoded = self.iter_stored_chunks(DEFAULT_CHUNK_SIZE)
            elif scheme == CompressionScheme.ZLIB:
                decoded = self._decompress_zlib()
            elif scheme == CompressionScheme.BZIP2:
                decoded = self._decompress_bounded(bz2.BZ2Decompressor(), "BZip2")
            elif scheme in (CompressionScheme.LZ4, CompressionScheme.LZ4_HC):
                if _lz4_frame is None:
                    raise OdinFormatError("ODIN LZ4 support is unavailable; install lz4")
                decoded = self._decompress_bounded(_lz4_frame.LZ4FrameDecompressor(), "LZ4")
            else:
                decoded = self._decompress_zstd()
            yield from self._checked_output(decoded, self.header.used_size)
        except OdinFormatError:
            raise
        except (EOFError, OSError, RuntimeError, zlib.error) as exc:
            raise OdinFormatError(
                f"ODIN {scheme.name} payload is not a valid complete stream"
            ) from exc
        except Exception as exc:
            if _zstandard is not None and isinstance(exc, _zstandard.ZstdError):
                raise OdinFormatError("ODIN ZSTD payload is not a valid complete stream") from exc
            raise

    def verify_crc32(self) -> int | None:
        if self.stored_crc32 is None:
            return None
        actual = 0
        for chunk in self.iter_payload_chunks():
            actual = zlib.crc32(chunk, actual)
        actual &= 0xFFFFFFFF
        if actual != self.stored_crc32:
            raise OdinFormatError(
                f"ODIN CRC32 mismatch: stored {self.stored_crc32:08x}, actual {actual:08x}"
            )
        return actual

    def read_logical(self, offset: int, length: int) -> bytes:
        """Read logical volume bytes, filling unstored free clusters with zeroes."""
        if offset < 0 or length < 0 or offset + length > self.header.volume_size:
            raise OdinFormatError("ODIN logical volume read lies outside the volume")
        if length == 0:
            return b""
        payload = _PayloadReader(iter(self.iter_payload_chunks()))
        end = offset + length
        result = bytearray(length)
        if self.header.volume_bitmap_encoding_scheme == BitmapScheme.ALL_BLOCKS:
            payload.skip(offset)
            return payload.read(length)

        for run_offset, run_length in self.allocated_ranges():
            run_end = run_offset + run_length
            if run_end <= offset:
                payload.skip(run_length)
                continue
            if run_offset >= end:
                break
            prefix = max(0, offset - run_offset)
            if prefix:
                payload.skip(prefix)
            copy_start = max(run_offset, offset)
            copy_end = min(run_end, end)
            copy_length = copy_end - copy_start
            result[copy_start - offset : copy_end - offset] = payload.read(copy_length)
            remaining = run_length - prefix - copy_length
            if remaining and run_end < end:
                payload.skip(remaining)
        return bytes(result)


def has_odin_magic(path: str | os.PathLike[str]) -> bool:
    candidate = Path(path)
    try:
        initial = _resolve_initial_path(candidate)
        with open(initial, "rb") as stream:
            return stream.read(len(ODIN_MAGIC)) == ODIN_MAGIC
    except (OSError, OdinFormatError):
        return False


def read_header(path: str | os.PathLike[str]) -> OdinHeader | None:
    """Return a validated ODIN header, or ``None`` for a plain/raw file."""
    if not has_odin_magic(path):
        return None
    with OdinImage.open(path) as image:
        return image.header


__all__ = [
    "AllocationRun",
    "BitmapScheme",
    "CompressionScheme",
    "HEADER_FORMAT",
    "HEADER_SIZE",
    "LogicalSplitReader",
    "ODIN_MAGIC",
    "OdinFormatError",
    "OdinHeader",
    "OdinImage",
    "SplitMember",
    "VerifyScheme",
    "VolumeType",
    "codec_available",
    "decode_run_lengths",
    "encode_run_lengths",
    "has_odin_magic",
    "read_header",
    "split_base_path",
    "split_member_path",
]
