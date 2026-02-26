"""
partition_reader.py
Reads the primary MBR partition table from a disk image file.
Handles both raw images and ODIN .img files (which have a binary header
before the raw disk data — the MBR is at dataOffset+510, not at byte 510).
Returns byte offset and size for each partition so HashWorker can
hash individual partitions rather than the whole file.
"""

import struct
from dataclasses import dataclass
from typing import List

SECTOR_SIZE     = 512
MBR_SIG_OFFSET  = 510
MBR_PART_OFFSET = 446   # start of 4 x 16-byte partition entries

# ODIN .img file header constants
# GUID {1d4d7b73-fa01-40e1-b094-5267d8fa0be7} in Windows mixed-endian byte order
_ODIN_MAGIC = bytes([
    0x73, 0x7b, 0x4d, 0x1d,  # Data1 LE
    0x01, 0xfa,               # Data2 LE
    0xe1, 0x40,               # Data3 LE
    0xb0, 0x94, 0x52, 0x67, 0xd8, 0xfa, 0x0b, 0xe7,  # Data4
])
# TDiskImageFileHeader layout (MSVC /Zp8, 128 bytes total):
#   GUID(16) + WORD(2) + WORD(2) + DWORD*8(32) + pad(4) + UINT64*9(72) = 128
# dataOffset is the 5th UINT64 field (index [15] after unpack)
_ODIN_HDR_FMT  = "<16sHHIIIIIIII4xQQQQQQQQQ"
_ODIN_HDR_SIZE = struct.calcsize(_ODIN_HDR_FMT)   # 128


_TYPE_NAMES = {
    0x01: "FAT12",
    0x04: "FAT16 <32M",
    0x05: "Extended",
    0x06: "FAT16",
    0x07: "NTFS/exFAT",
    0x0B: "FAT32",
    0x0C: "FAT32 LBA",
    0x0E: "FAT16 LBA",
    0x0F: "Extended LBA",
    0x82: "Linux swap",
    0x83: "Linux ext4",
    0x8E: "Linux LVM",
    0xEE: "GPT protective",
    0xEF: "EFI System",
}


@dataclass
class PartitionInfo:
    number:    int   # 1-based (matches Configure Hashes partition numbers)
    part_type: int   # partition type byte
    offset:    int   # byte offset into image FILE (includes ODIN header if present)
    size:      int   # byte count
    active:    bool  # bootable flag

    @property
    def type_name(self) -> str:
        return _TYPE_NAMES.get(self.part_type, f"Type 0x{self.part_type:02X}")

    @property
    def size_str(self) -> str:
        n = self.size
        for unit, thresh in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
            if n >= thresh:
                return f"{n / thresh:.1f} {unit}"
        return f"{n} B"

    @property
    def summary(self) -> str:
        boot = " [boot]" if self.active else ""
        return f"{self.type_name}, {self.size_str}{boot}"


def _odin_data_offset(image_path: str) -> int:
    """Return the byte offset where raw disk data begins in an ODIN .img file.
    Returns 0 if the file is not an ODIN image (treat as raw disk image)."""
    try:
        with open(image_path, "rb") as f:
            header = f.read(_ODIN_HDR_SIZE)
        if len(header) < _ODIN_HDR_SIZE:
            return 0
        if header[:16] != _ODIN_MAGIC:
            return 0
        fields = struct.unpack_from(_ODIN_HDR_FMT, header)
        return int(fields[15])  # dataOffset
    except OSError:
        return 0


def read_mbr_partitions(image_path: str) -> List[PartitionInfo]:
    """
    Parse the primary MBR partition table from image_path.
    Handles ODIN .img files (binary header) and raw disk images.
    Returns non-empty entries only (up to 4 primary partitions).
    Returns [] if the file is not accessible or has no valid MBR signature.
    PartitionInfo.offset is always a file-absolute byte offset ready for
    use directly in HashWorker(offset=...).
    """
    data_offset = _odin_data_offset(image_path)
    entries: List[PartitionInfo] = []
    try:
        with open(image_path, "rb") as f:
            f.seek(data_offset + MBR_SIG_OFFSET)
            if f.read(2) != b"\x55\xAA":
                return []
            f.seek(data_offset + MBR_PART_OFFSET)
            for i in range(4):
                raw = f.read(16)
                if len(raw) < 16:
                    break
                status    = raw[0]
                part_type = raw[4]
                lba_start = struct.unpack_from("<I", raw, 8)[0]
                lba_size  = struct.unpack_from("<I", raw, 12)[0]
                if lba_size == 0 or part_type == 0x00:
                    continue
                entries.append(PartitionInfo(
                    number    = i + 1,
                    part_type = part_type,
                    offset    = data_offset + lba_start * SECTOR_SIZE,
                    size      = lba_size * SECTOR_SIZE,
                    active    = status == 0x80,
                ))
    except OSError:
        return []
    return entries
