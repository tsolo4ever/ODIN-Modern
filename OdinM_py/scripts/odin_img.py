"""Inspect raw disks and validated ODIN v1.x image containers read-only."""

import struct
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from odin_container import (  # noqa: E402
    CompressionScheme,
    OdinHeader,
    OdinImage,
    read_header as _read_odin_header,
)

SECTOR = 512

_TYPE_NAMES = {
    0x00: "empty",
    0x05: "Extended",
    0x0B: "FAT32",
    0x0C: "FAT32 LBA",
    0x07: "NTFS/exFAT",
    0x82: "Linux swap",
    0x83: "Linux",
    0xEE: "GPT protective",
}


@dataclass
class Partition:
    index: int
    ptype: int
    lba_start: int
    sectors: int
    bootable: bool

    @property
    def type_name(self):
        return _TYPE_NAMES.get(self.ptype, f"0x{self.ptype:02X}")

    @property
    def size(self):
        return self.sectors * SECTOR

    def file_offset(self, hdr: "OdinHeader | None") -> int:
        """Byte offset in the FILE. `hdr` is None for raw (headerless) images."""
        if hdr is not None and (not hdr.is_raw_sectors or hdr.file_count > 0):
            raise ValueError(
                "compressed, used-block, or split ODIN partitions do not have one direct "
                "file offset"
            )
        return (hdr.data_offset if hdr else 0) + self.lba_start * SECTOR


def read_header(path) -> OdinHeader | None:
    """Return the production parser's validated header, or None for raw input."""
    return _read_odin_header(path)


def read_partitions(path, hdr: OdinHeader | None) -> list[Partition]:
    if hdr is not None and (not hdr.is_raw_sectors or hdr.file_count > 0):
        with OdinImage.open(path) as image:
            mbr = image.read_logical(0, SECTOR)
        location = "logical volume offset 0"
    else:
        base = hdr.data_offset if hdr else 0
        with open(path, "rb") as f:
            f.seek(base)
            mbr = f.read(SECTOR)
        location = f"file offset {base}"
    if mbr[510:512] != b"\x55\xaa":
        raise ValueError(f"no MBR signature at {location} (got {mbr[510:512].hex()})")
    parts = []
    for i in range(4):
        e = mbr[446 + i * 16 : 446 + (i + 1) * 16]
        if e[4] == 0:
            continue
        lba, n = struct.unpack("<II", e[8:16])
        if n == 0:
            continue
        parts.append(Partition(i, e[4], lba, n, e[0] == 0x80))
    return parts


class ImageWindow:
    """Read-only file-like view of [offset, offset+length) inside an image file.

    Positions are relative to the window start, so ext4.Volume can be handed
    this directly with offset=0.
    """

    def __init__(self, path, offset: int, length: int):
        header = read_header(path)
        if header is not None and (not header.is_raw_sectors or header.file_count > 0):
            raise ValueError(
                "random filesystem windows over compressed/used-block/split ODIN images "
                "require the later native restore/spool phase"
            )
        self._f = open(path, "rb")
        self._base = offset
        self._len = length
        self._pos = 0

    @property
    def size(self):
        return self._len

    def seek(self, offset, whence=0):
        if whence == 1:
            offset += self._pos
        elif whence == 2:
            offset += self._len
        self._pos = offset
        return self._pos

    def tell(self):
        return self._pos

    def read(self, size=-1):
        if size < 0:
            size = max(0, self._len - self._pos)
        size = max(0, min(size, self._len - self._pos))
        if size == 0:
            return b""
        self._f.seek(self._base + self._pos)
        data = self._f.read(size)
        self._pos += len(data)
        return data

    def peek(self, size):
        saved = self._pos
        try:
            return self.read(size)
        finally:
            self._pos = saved

    def close(self):
        self._f.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def describe(path):
    path = Path(path)
    hdr = read_header(path)
    fsize = hdr.file_size if hdr is not None else path.stat().st_size
    print(f"{path}")
    print(f"  file size      : {fsize} ({fsize / (1 << 30):.3f} GiB)")
    if hdr is None:
        print("  format         : raw (no ODIN header)")
    else:
        print(f"  format         : ODIN v{hdr.version[0]}.{hdr.version[1]}")
        compression_name = CompressionScheme(hdr.compression).name.lower()
        print(f"  compression    : {hdr.compression} ({compression_name})")
        bitmap_name = (
            "all-blocks / raw sectors" if hdr.bitmap_scheme == 0 else "USED-BLOCKS (packed!)"
        )
        print(f"  bitmap scheme  : {hdr.bitmap_scheme} ({bitmap_name})")
        print(f"  dataOffset     : {hdr.data_offset}")
        print(f"  dataSize       : {hdr.data_size}")
        print(f"  volumeSize     : {hdr.volume_size} ({hdr.volume_size // SECTOR} sectors)")
        print(f"  header fileSize: {hdr.raw['fileSize']} (validated logical size {fsize})")
        print(f"  raw sectors ok : {hdr.is_raw_sectors}")
    parts = read_partitions(path, hdr)
    print(f"  partitions     : {len(parts)}")
    for p in parts:
        location = (
            f"file_off={p.file_offset(hdr)}"
            if hdr is None or (hdr.is_raw_sectors and hdr.file_count == 0)
            else f"logical_off={p.lba_start * SECTOR}"
        )
        print(
            f"    [{p.index}] type=0x{p.ptype:02X} ({p.type_name:12s}) "
            f"lba={p.lba_start:<10d} sectors={p.sectors:<11d} "
            f"size={p.size / (1 << 20):8.1f} MiB  {location}" + ("  [boot]" if p.bootable else "")
        )
    return hdr, parts


if __name__ == "__main__":
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8")
    for arg in sys.argv[1:]:
        describe(arg)
        print()
