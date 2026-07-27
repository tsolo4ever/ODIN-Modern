"""Read-only access to an ODIN .img file as if it were a raw disk.

ODIN prepends a 128-byte TDiskImageFileHeader (plus, here, a 4-byte CRC32) so
the MBR lives at dataOffset+0, not byte 0. This module parses that header and
exposes a file-like window over any partition, with the peek() that
ext4.Volume expects.

Opens the image 'rb' only.
"""

import struct
import sys
from dataclasses import dataclass
from pathlib import Path

SECTOR = 512
_ODIN_MAGIC = bytes.fromhex("737b4d1d01fae140b0945267d8fa0be7")
# GUID(16) WORD*2 DWORD*8 pad(4) UINT64*9 = 128 bytes under MSVC /Zp8
_HDR_FMT = "<16sHHIIIIIIII4xQQQQQQQQQ"
_HDR_SIZE = struct.calcsize(_HDR_FMT)
assert _HDR_SIZE == 128, _HDR_SIZE

_FIELDS = [
    "guid", "versionMajor", "versionMinor", "compressionScheme", "verifyScheme",
    "volumeBitmapEncodingScheme", "volumeType", "fileCount", "clusterSize",
    "verifyLength", "commentLength", "volumeBitmapOffset", "volumeBitmapLength",
    "verifyOffset", "commentOffset", "dataOffset", "dataSize", "usedSize",
    "volumeSize", "fileSize",
]

_TYPE_NAMES = {
    0x00: "empty", 0x05: "Extended", 0x0B: "FAT32", 0x0C: "FAT32 LBA",
    0x07: "NTFS/exFAT", 0x82: "Linux swap", 0x83: "Linux", 0xEE: "GPT protective",
}


@dataclass
class OdinHeader:
    data_offset: int
    data_size: int
    volume_size: int
    compression: int
    bitmap_scheme: int
    version: tuple
    raw: dict

    @property
    def is_raw_sectors(self) -> bool:
        """True only when the payload is uncompressed, all-blocks sector data."""
        return self.compression == 0 and self.bitmap_scheme == 0


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
        return (hdr.data_offset if hdr else 0) + self.lba_start * SECTOR


def read_header(path) -> OdinHeader | None:
    """Parse the ODIN header, or None if the file is a plain raw image."""
    with open(path, "rb") as f:
        blob = f.read(_HDR_SIZE)
    if len(blob) < _HDR_SIZE or blob[:16] != _ODIN_MAGIC:
        return None
    vals = struct.unpack(_HDR_FMT, blob)
    raw = dict(zip(_FIELDS, vals))
    return OdinHeader(
        data_offset=raw["dataOffset"],
        data_size=raw["dataSize"],
        volume_size=raw["volumeSize"],
        compression=raw["compressionScheme"],
        bitmap_scheme=raw["volumeBitmapEncodingScheme"],
        version=(raw["versionMajor"], raw["versionMinor"]),
        raw=raw,
    )


def read_partitions(path, hdr: OdinHeader | None) -> list[Partition]:
    base = hdr.data_offset if hdr else 0
    with open(path, "rb") as f:
        f.seek(base)
        mbr = f.read(SECTOR)
    if mbr[510:512] != b"\x55\xaa":
        raise ValueError(f"no MBR signature at file offset {base} "
                         f"(got {mbr[510:512].hex()})")
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
    fsize = path.stat().st_size
    hdr = read_header(path)
    print(f"{path}")
    print(f"  file size      : {fsize} ({fsize / (1 << 30):.3f} GiB)")
    if hdr is None:
        print("  format         : raw (no ODIN header)")
    else:
        print(f"  format         : ODIN v{hdr.version[0]}.{hdr.version[1]}")
        print(f"  compression    : {hdr.compression} "
              f"({'none' if hdr.compression == 0 else 'COMPRESSED'})")
        print(f"  bitmap scheme  : {hdr.bitmap_scheme} "
              f"({'all-blocks / raw sectors' if hdr.bitmap_scheme == 0 else 'USED-BLOCKS (packed!)'})")
        print(f"  dataOffset     : {hdr.data_offset}")
        print(f"  dataSize       : {hdr.data_size}")
        print(f"  volumeSize     : {hdr.volume_size} "
              f"({hdr.volume_size // SECTOR} sectors)")
        print(f"  header fileSize: {hdr.raw['fileSize']}  "
              f"(actual {fsize}, trailing {fsize - hdr.raw['fileSize']} B)")
        print(f"  raw sectors ok : {hdr.is_raw_sectors}")
    parts = read_partitions(path, hdr)
    print(f"  partitions     : {len(parts)}")
    for p in parts:
        print(f"    [{p.index}] type=0x{p.ptype:02X} ({p.type_name:12s}) "
              f"lba={p.lba_start:<10d} sectors={p.sectors:<11d} "
              f"size={p.size / (1 << 20):8.1f} MiB  file_off={p.file_offset(hdr)}"
              + ("  [boot]" if p.bootable else ""))
    return hdr, parts


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for arg in sys.argv[1:]:
        describe(arg)
        print()
