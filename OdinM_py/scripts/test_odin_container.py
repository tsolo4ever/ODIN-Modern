"""Headless conformance checks for the native ODIN v1.x reader."""

from __future__ import annotations

import bz2
import dataclasses
import math
import struct
import sys
import tempfile
import zlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import odin_container as odin  # noqa: E402
import odin_img  # noqa: E402
import partition_reader  # noqa: E402


def _rle(tokens: tuple[int, ...]) -> bytes:
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


def _compress(payload: bytes, scheme: odin.CompressionScheme) -> bytes:
    if scheme == odin.CompressionScheme.NONE:
        return payload
    if scheme == odin.CompressionScheme.ZLIB:
        return zlib.compress(payload)
    if scheme == odin.CompressionScheme.BZIP2:
        return bz2.compress(payload, compresslevel=9)
    if scheme in (odin.CompressionScheme.LZ4, odin.CompressionScheme.LZ4_HC):
        if odin._lz4_frame is None:
            raise AssertionError("lz4 is required for the ODIN conformance test")
        return odin._lz4_frame.compress(
            payload,
            block_size=odin._lz4_frame.BLOCKSIZE_MAX64KB,
            block_linked=False,
            compression_level=9 if scheme == odin.CompressionScheme.LZ4_HC else 0,
            content_checksum=True,
            block_checksum=False,
            store_size=False,
        )
    if odin._zstandard is None:
        raise AssertionError("zstandard is required for the ODIN conformance test")
    return odin._zstandard.ZstdCompressor(level=3).compress(payload)


def _packed_payload(logical: bytes, cluster_size: int, tokens: tuple[int, ...]) -> bytes:
    output = bytearray()
    cluster = 0
    for index, count in enumerate(tokens):
        if index % 2 == 0:
            start = cluster * cluster_size
            output.extend(logical[start : start + count * cluster_size])
        cluster += count
    return bytes(output)


def _fixture(
    path: Path,
    logical: bytes,
    *,
    scheme: odin.CompressionScheme = odin.CompressionScheme.NONE,
    tokens: tuple[int, ...] | None = None,
    cluster_size: int = 512,
    crc: bool = True,
    comment: str = "ODIN Python fixture",
    split_size: int = 0,
) -> tuple[Path, bytes]:
    bitmap = b""
    bitmap_scheme = odin.BitmapScheme.ALL_BLOCKS
    payload = logical
    if tokens is not None:
        assert sum(tokens) * cluster_size == len(logical)
        bitmap = _rle(tokens)
        bitmap_scheme = odin.BitmapScheme.SIMPLE_COMPRESSED_RUN_LENGTH
        payload = _packed_payload(logical, cluster_size, tokens)
    compressed = _compress(payload, scheme)
    verify = struct.pack("<I", zlib.crc32(payload) & 0xFFFFFFFF) if crc else b""
    comment_blob = comment.encode("utf-16-le")

    position = odin.HEADER_SIZE
    verify_offset = position if verify else 0
    position += len(verify)
    comment_offset = position if comment_blob else 0
    position += len(comment_blob)
    bitmap_offset = position if bitmap else 0
    position += len(bitmap)
    data_offset = position
    file_size = data_offset + len(compressed)
    file_count = math.ceil(file_size / split_size) if split_size else 0
    header = odin.OdinHeader(
        compression_scheme=int(scheme),
        verify_scheme=int(odin.VerifyScheme.CRC32 if crc else odin.VerifyScheme.NONE),
        volume_bitmap_encoding_scheme=int(bitmap_scheme),
        volume_type=int(odin.VolumeType.HARD_DISK),
        file_count=file_count,
        cluster_size=cluster_size,
        verify_length=len(verify),
        comment_length=len(comment_blob),
        volume_bitmap_offset=bitmap_offset,
        volume_bitmap_length=len(bitmap),
        verify_offset=verify_offset,
        comment_offset=comment_offset,
        data_offset=data_offset,
        data_size=len(compressed),
        used_size=len(payload),
        volume_size=len(logical),
        file_size=file_size,
    )
    container = header.pack() + verify + comment_blob + bitmap + compressed
    assert len(container) == file_size
    if split_size:
        for index in range(file_count):
            member = odin.split_member_path(path, index)
            member.write_bytes(container[index * split_size : (index + 1) * split_size])
        return path, payload
    path.write_bytes(container)
    return path, payload


def _mbr_payload(size: int = 4096) -> bytes:
    disk = bytearray((index * 37) % 251 for index in range(size))
    disk[446:510] = b"\x00" * 64
    disk[446:462] = b"\x80\x00\x00\x00\x0b\x00\x00\x00" + struct.pack("<II", 1, 2)
    disk[510:512] = b"\x55\xaa"
    return bytes(disk)


def _rewrite_header(path: Path, transform) -> None:
    blob = path.read_bytes()
    header = odin.OdinHeader.unpack(blob[: odin.HEADER_SIZE])
    changed = transform(header)
    path.write_bytes(changed.pack() + blob[odin.HEADER_SIZE :])


def _expect_format_error(action, contains: str) -> None:
    try:
        action()
    except odin.OdinFormatError as exc:
        assert contains.lower() in str(exc).lower(), str(exc)
    else:
        raise AssertionError("malformed ODIN input was accepted")


def test_header_binary_round_trip_and_legacy_names():
    header = odin.OdinHeader(
        cluster_size=512,
        data_size=512,
        used_size=512,
        volume_size=512,
        file_size=odin.HEADER_SIZE + 512,
    )
    blob = header.pack()
    assert len(blob) == 128
    assert blob[:16] == odin.ODIN_MAGIC
    loaded = odin.OdinHeader.unpack(blob)
    assert loaded == header
    assert loaded.version == (1, 0)
    assert loaded.raw["dataOffset"] == odin.HEADER_SIZE
    assert loaded.is_raw_sectors


def test_every_legacy_codec_reads_crc_comment_and_logical_bytes():
    logical = _mbr_payload()
    with tempfile.TemporaryDirectory() as folder:
        for scheme in odin.CompressionScheme:
            path = Path(folder) / f"codec-{int(scheme)}.img"
            _fixture(path, logical, scheme=scheme, comment=f"codec {int(scheme)}")
            with odin.OdinImage.open(path) as image:
                assert image.header.compression_scheme == scheme
                assert image.comment == f"codec {int(scheme)}"
                assert b"".join(image.iter_payload_chunks()) == logical
                assert image.read_logical(440, 72) == logical[440:512]
                assert image.verify_crc32() == zlib.crc32(logical) & 0xFFFFFFFF


def test_cpp_run_length_oracle_and_used_block_reconstruction():
    oracle = bytes([0, 36, 40, 28, 24, 5, 0, 5, 0, 5, 0, 0])
    tokens = (36, 40, 28, 24, 1280, 1280)
    assert _rle(tokens) == oracle
    assert odin.encode_run_lengths(tokens) == oracle
    assert odin.decode_run_lengths(oracle, sum(tokens)) == tokens
    logical = bytes(index % 251 for index in range(sum(tokens)))
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "used.img"
        _fixture(path, logical, tokens=tokens, cluster_size=1)
        with odin.OdinImage.open(path) as image:
            expected_ranges = ((0, 36), (76, 28), (128, 1280))
            assert image.allocated_ranges() == expected_ranges
            expected_payload = _packed_payload(logical, 1, tokens)
            assert b"".join(image.iter_payload_chunks()) == expected_payload
            assert image.read_logical(0, len(logical)) == bytes(
                logical[index]
                if any(start <= index < start + length for start, length in expected_ranges)
                else 0
                for index in range(len(logical))
            )
            assert image.verify_crc32() == zlib.crc32(expected_payload) & 0xFFFFFFFF


def test_bitmap_may_end_with_an_allocated_run_and_implicit_zero_free_run():
    logical = bytes(range(32))
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "all-used.img"
        _fixture(path, logical, tokens=(8, 0), cluster_size=4)
        with odin.OdinImage.open(path) as image:
            assert image.allocated_ranges() == ((0, 32),)
            assert image.read_logical(0, 32) == logical
            assert image.verify_crc32() == zlib.crc32(logical) & 0xFFFFFFFF

        path = Path(folder) / "all-free.img"
        _fixture(path, logical, tokens=(0, 8), cluster_size=4)
        with odin.OdinImage.open(path) as image:
            assert image.allocated_ranges() == ()
            assert image.read_logical(0, 32) == b"\x00" * 32
            assert image.verify_crc32() == 0


def test_no_crc_and_empty_comment_are_supported():
    logical = _mbr_payload()
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "plain.img"
        _fixture(path, logical, crc=False, comment="")
        with odin.OdinImage.open(path) as image:
            assert image.comment == ""
            assert image.stored_crc32 is None
            assert image.verify_crc32() is None


def test_split_set_is_one_seekable_logical_stream():
    logical = _mbr_payload(8192)
    with tempfile.TemporaryDirectory() as folder:
        base = Path(folder) / "split.img"
        _fixture(
            base,
            logical,
            scheme=odin.CompressionScheme.ZLIB,
            split_size=173,
        )
        assert not base.exists()
        assert odin.split_member_path(base, 0).exists()
        with odin.OdinImage.open(base) as image:
            assert len(image.members) == image.header.file_count
            assert (
                image.reader.read_at(160, 40)
                == b"".join(member.path.read_bytes() for member in image.members)[160:200]
            )
            assert image.read_logical(500, 700) == logical[500:1200]
            assert image.verify_crc32() == zlib.crc32(logical) & 0xFFFFFFFF
        with odin.OdinImage.open(odin.split_member_path(base, 2)) as image:
            assert image.read_logical(0, 512) == logical[:512]


def test_missing_and_extra_split_members_fail_closed():
    logical = _mbr_payload(8192)
    with tempfile.TemporaryDirectory() as folder:
        base = Path(folder) / "split.img"
        _fixture(base, logical, split_size=512)
        members = sorted(Path(folder).glob("split*.img"))
        missing = members[-1]
        missing.unlink()
        _expect_format_error(lambda: odin.OdinImage.open(base), "unavailable")

        for item in Path(folder).glob("split*.img"):
            item.unlink()
        _fixture(base, logical, split_size=512)
        with odin.OdinImage.open(base) as image:
            count = image.header.file_count
        odin.split_member_path(base, count).write_bytes(b"extra")
        _expect_format_error(lambda: odin.OdinImage.open(base), "extra")


def test_unknown_ids_overlaps_and_truncation_fail_closed():
    logical = _mbr_payload()
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "bad.img"
        _fixture(path, logical)
        blob = path.read_bytes()
        header = odin.OdinHeader.unpack(blob[: odin.HEADER_SIZE])

        unknown = dataclasses.replace(header, compression_scheme=99)
        path.write_bytes(
            struct.pack(
                odin.HEADER_FORMAT, *(getattr(unknown, name) for name in odin._HEADER_FIELDS)
            )
            + blob[odin.HEADER_SIZE :]
        )
        _expect_format_error(lambda: odin.OdinImage.open(path), "compression")

        path.write_bytes(blob)
        overlap = dataclasses.replace(header, comment_offset=header.verify_offset)
        path.write_bytes(
            struct.pack(
                odin.HEADER_FORMAT, *(getattr(overlap, name) for name in odin._HEADER_FIELDS)
            )
            + blob[odin.HEADER_SIZE :]
        )
        _expect_format_error(lambda: odin.OdinImage.open(path), "overlap")

        path.write_bytes(blob[:-1])
        _expect_format_error(lambda: odin.OdinImage.open(path), "declared")


def test_crc_mismatch_and_codec_trailing_data_fail_closed():
    logical = _mbr_payload()
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "crc.img"
        _fixture(path, logical, scheme=odin.CompressionScheme.ZLIB)
        blob = bytearray(path.read_bytes())
        header = odin.OdinHeader.unpack(blob[: odin.HEADER_SIZE])
        blob[header.verify_offset] ^= 0x80
        path.write_bytes(blob)
        with odin.OdinImage.open(path) as image:
            _expect_format_error(image.verify_crc32, "mismatch")

        _fixture(path, logical, scheme=odin.CompressionScheme.ZLIB)
        blob = path.read_bytes()
        header = odin.OdinHeader.unpack(blob[: odin.HEADER_SIZE])
        changed = dataclasses.replace(
            header, data_size=header.data_size + 1, file_size=header.file_size + 1
        )
        path.write_bytes(changed.pack() + blob[odin.HEADER_SIZE :] + b"X")
        with odin.OdinImage.open(path) as image:
            _expect_format_error(lambda: b"".join(image.iter_payload_chunks()), "trailing")


def test_each_compressed_codec_rejects_early_eof():
    logical = _mbr_payload()
    with tempfile.TemporaryDirectory() as folder:
        for scheme in tuple(odin.CompressionScheme)[1:]:
            path = Path(folder) / f"short-{int(scheme)}.img"
            _fixture(path, logical, scheme=scheme)
            blob = path.read_bytes()
            header = odin.OdinHeader.unpack(blob[: odin.HEADER_SIZE])
            changed = dataclasses.replace(
                header, data_size=header.data_size - 1, file_size=header.file_size - 1
            )
            path.write_bytes(changed.pack() + blob[odin.HEADER_SIZE : -1])
            with odin.OdinImage.open(path) as image:
                _expect_format_error(lambda: b"".join(image.iter_payload_chunks()), "stream")


def test_bitmap_bounds_and_declared_output_length_fail_closed():
    logical = bytes(range(32))
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "bitmap.img"
        _fixture(path, logical, tokens=(1, 2, 2, 3), cluster_size=4)
        blob = path.read_bytes()
        header = odin.OdinHeader.unpack(blob[: odin.HEADER_SIZE])
        oversized_bitmap = _rle((1, 2, 2, 4))
        assert len(oversized_bitmap) == header.volume_bitmap_length
        changed = bytearray(blob)
        start = header.volume_bitmap_offset
        changed[start : start + len(oversized_bitmap)] = oversized_bitmap
        path.write_bytes(changed)
        _expect_format_error(lambda: odin.OdinImage.open(path), "exceeds")

        path = Path(folder) / "length.img"
        _fixture(path, _mbr_payload(), scheme=odin.CompressionScheme.ZLIB)
        blob = path.read_bytes()
        header = odin.OdinHeader.unpack(blob[: odin.HEADER_SIZE])
        changed_header = dataclasses.replace(
            header, used_size=header.used_size - 512, volume_size=header.volume_size - 512
        )
        path.write_bytes(changed_header.pack() + blob[odin.HEADER_SIZE :])
        with odin.OdinImage.open(path) as image:
            _expect_format_error(lambda: b"".join(image.iter_payload_chunks()), "expanded beyond")


def test_real_uncompressed_fixture_header_and_mbr_when_present():
    path = Path(r"D:\cards\Old img\15.0.6.2.img")
    if not path.is_file():
        return
    with odin.OdinImage.open(path) as image:
        assert image.header.version == (1, 0)
        assert image.header.is_raw_sectors
        assert image.header.data_offset == 200
        assert image.header.data_size == 7_969_177_600
        assert image.header.volume_size == 7_969_177_600
        assert image.read_logical(510, 2) == b"\x55\xaa"


def test_existing_inspection_readers_delegate_to_validated_container_parser():
    logical = _mbr_payload()
    with tempfile.TemporaryDirectory() as folder:
        raw_path = Path(folder) / "direct.img"
        _fixture(raw_path, logical)
        header = odin_img.read_header(raw_path)
        assert header is not None and header.is_raw_sectors
        assert len(odin_img.read_partitions(raw_path, header)) == 1
        region = partition_reader.get_image_hash_region(str(raw_path))
        assert region is not None and region.is_disk_verifiable
        assert region.offset == header.data_offset
        assert region.size == len(logical)

        compressed_path = Path(folder) / "compressed.img"
        _fixture(compressed_path, logical, scheme=odin.CompressionScheme.ZSTD)
        header = odin_img.read_header(compressed_path)
        assert header is not None and not header.is_raw_sectors
        assert len(odin_img.read_partitions(compressed_path, header)) == 1
        assert partition_reader.get_image_compression_flag(str(compressed_path)) == "zstd"
        try:
            partition_reader.read_mbr_partitions_strict(str(compressed_path))
        except partition_reader.PartitionReadError as exc:
            assert "direct file offset" in str(exc)
        else:
            raise AssertionError("compressed ODIN input exposed a direct partition offset")

        split_base = Path(folder) / "split-direct.img"
        _fixture(split_base, logical, split_size=512)
        split_header = odin_img.read_header(split_base)
        assert split_header is not None and split_header.file_count > 0
        assert len(odin_img.read_partitions(split_base, split_header)) == 1
        split_region = partition_reader.get_image_hash_region(
            str(odin.split_member_path(split_base, 0))
        )
        assert split_region is not None
        assert split_region.split_member_count > 0
        assert not split_region.is_raw_supported
        assert not split_region.is_disk_verifiable


def _run_direct() -> int:
    tests = [
        value
        for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"PASS {test.__name__}")
        except Exception as exc:
            failures += 1
            print(f"FAIL {test.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_run_direct())
