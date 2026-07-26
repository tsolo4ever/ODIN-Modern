"""
partition_reader.py
Reads the primary MBR partition table from a disk image file.
Handles both raw images and ODIN .img files (which have a binary header
before the raw disk data — the MBR is at dataOffset+510, not at byte 510).
Returns byte offset and size for each partition so HashWorker can
hash individual partitions rather than the whole file.
"""

import os
import struct
from dataclasses import dataclass

SECTOR_SIZE = 512
MBR_SIG_OFFSET = 510
MBR_PART_OFFSET = 446  # start of 4 x 16-byte partition entries

# ODIN .img file header constants
# GUID {1d4d7b73-fa01-40e1-b094-5267d8fa0be7} in Windows mixed-endian byte order
_ODIN_MAGIC = bytes(
    [
        0x73,
        0x7B,
        0x4D,
        0x1D,  # Data1 LE
        0x01,
        0xFA,  # Data2 LE
        0xE1,
        0x40,  # Data3 LE
        0xB0,
        0x94,
        0x52,
        0x67,
        0xD8,
        0xFA,
        0x0B,
        0xE7,  # Data4
    ]
)
# TDiskImageFileHeader layout (MSVC /Zp8, 128 bytes total):
#   GUID(16) + WORD(2) + WORD(2) + DWORD*8(32) + pad(4) + UINT64*9(72) = 128
# dataOffset is the 5th UINT64 field (index [15] after unpack)
_ODIN_HDR_FMT = "<16sHHIIIIIIII4xQQQQQQQQQ"
_ODIN_HDR_SIZE = struct.calcsize(_ODIN_HDR_FMT)  # 128
_ODIN_NO_COMPRESSION = 0
_ODIN_NO_BITMAP = 0  # volumeBitmapEncodingScheme == 0 → all-blocks (raw sectors)
# == 1 → used-blocks (packed cluster data, not raw sectors)

# Maps compressionScheme header value → -compression= CLI flag
_COMPRESSION_FLAG: dict[int, str] = {
    0: "none",
    1: "gzip",
    2: "bzip",
    3: "lz4",
    4: "lz4hc",
    5: "zstd",
}


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
    number: int  # 1-based (matches Configure Hashes partition numbers)
    part_type: int  # partition type byte
    offset: int  # byte offset into image FILE (includes ODIN header if present)
    size: int  # byte count
    active: bool  # bootable flag
    type_label: str = ""

    @property
    def type_name(self) -> str:
        if self.type_label:
            return self.type_label
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


@dataclass
class ImageHashRegion:
    offset: int
    size: int
    is_odin: bool
    compression_scheme: int = _ODIN_NO_COMPRESSION
    volume_bitmap_scheme: int = _ODIN_NO_BITMAP

    @property
    def is_raw_supported(self) -> bool:
        """True when the image file can be hashed (uncompressed at file level)."""
        return not self.is_odin or self.compression_scheme == _ODIN_NO_COMPRESSION

    @property
    def is_disk_verifiable(self) -> bool:
        """True only for uncompressed all-blocks images.
        Used-blocks images store packed cluster data, not raw sequential sectors,
        so their bytes cannot be compared directly against a raw disk read."""
        return not self.is_odin or (
            self.compression_scheme == _ODIN_NO_COMPRESSION
            and self.volume_bitmap_scheme == _ODIN_NO_BITMAP
        )


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


def get_image_hash_region(image_path: str) -> ImageHashRegion | None:
    """Return the raw disk byte region to hash for disk-level verification.

    Raw files hash the entire file. ODIN images hash only the raw data payload.
    Compressed ODIN images are reported but not considered raw-supported.
    """
    try:
        file_size = os.path.getsize(image_path)
        with open(image_path, "rb") as f:
            header = f.read(_ODIN_HDR_SIZE)
    except OSError:
        return None

    if len(header) < _ODIN_HDR_SIZE or header[:16] != _ODIN_MAGIC:
        return ImageHashRegion(offset=0, size=file_size, is_odin=False)

    fields = struct.unpack_from(_ODIN_HDR_FMT, header)
    compression_scheme = int(fields[3])
    volume_bitmap_scheme = int(fields[5])  # 0=no bitmap (all-blocks), 1=used-blocks
    data_offset = int(fields[15])
    data_size = int(fields[16])
    used_size = int(fields[17])
    volume_size = int(fields[18])

    if data_offset < 0 or data_offset > file_size:
        return None
    remaining = file_size - data_offset
    if compression_scheme != _ODIN_NO_COMPRESSION:
        size = min(max(data_size, 0), remaining)
    else:
        candidates = [n for n in (volume_size, used_size, data_size) if n > 0]
        size = min(candidates[0] if candidates else remaining, remaining)
    return ImageHashRegion(
        offset=data_offset,
        size=size,
        is_odin=True,
        compression_scheme=compression_scheme,
        volume_bitmap_scheme=volume_bitmap_scheme,
    )


def _read_gpt_partitions(f, data_offset: int) -> list[PartitionInfo]:
    f.seek(data_offset + SECTOR_SIZE)
    header = f.read(SECTOR_SIZE)
    if len(header) < 92 or header[:8] != b"EFI PART":
        return []

    entries_lba = struct.unpack_from("<Q", header, 72)[0]
    entry_count = struct.unpack_from("<I", header, 80)[0]
    entry_size = struct.unpack_from("<I", header, 84)[0]
    if entry_count == 0 or entry_size < 56 or entry_size > 4096:
        return []

    entries: list[PartitionInfo] = []
    f.seek(data_offset + entries_lba * SECTOR_SIZE)
    zero_guid = b"\x00" * 16
    for _ in range(min(entry_count, 128)):
        raw = f.read(entry_size)
        if len(raw) < entry_size:
            break
        if raw[:16] == zero_guid:
            continue
        first_lba = struct.unpack_from("<Q", raw, 32)[0]
        last_lba = struct.unpack_from("<Q", raw, 40)[0]
        if last_lba < first_lba:
            continue
        entries.append(
            PartitionInfo(
                number=len(entries) + 1,
                part_type=0xEE,
                offset=data_offset + first_lba * SECTOR_SIZE,
                size=(last_lba - first_lba + 1) * SECTOR_SIZE,
                active=False,
                type_label="GPT partition",
            )
        )
    return entries


def get_image_compression_flag(image_path: str) -> str:
    """Return the -compression= flag value for the given image file.
    Reads the ODIN header and maps compressionScheme to a CLI flag.
    Returns 'none' for raw images, unreadable files, or unknown schemes."""
    try:
        with open(image_path, "rb") as f:
            header = f.read(_ODIN_HDR_SIZE)
        if len(header) < _ODIN_HDR_SIZE or header[:16] != _ODIN_MAGIC:
            return "none"
        fields = struct.unpack_from(_ODIN_HDR_FMT, header)
        return _COMPRESSION_FLAG.get(int(fields[3]), "none")
    except OSError:
        return "none"


def read_mbr_partitions(image_path: str) -> list[PartitionInfo]:
    """
    Parse the primary MBR partition table from image_path.
    Handles ODIN .img files (binary header) and raw disk images.
    Returns non-empty entries only (up to 4 primary partitions).
    Returns [] if the file is not accessible or has no valid MBR signature.
    PartitionInfo.offset is always a file-absolute byte offset ready for
    use directly in HashWorker(offset=...).
    """
    data_offset = _odin_data_offset(image_path)
    entries: list[PartitionInfo] = []
    try:
        with open(image_path, "rb") as f:
            f.seek(data_offset + MBR_SIG_OFFSET)
            if f.read(2) != b"\x55\xaa":
                return []
            f.seek(data_offset + MBR_PART_OFFSET)
            for i in range(4):
                raw = f.read(16)
                if len(raw) < 16:
                    break
                status = raw[0]
                part_type = raw[4]
                lba_start = struct.unpack_from("<I", raw, 8)[0]
                lba_size = struct.unpack_from("<I", raw, 12)[0]
                if lba_size == 0 or part_type == 0x00:
                    continue
                if part_type == 0xEE:
                    resume_pos = f.tell()
                    gpt_entries = _read_gpt_partitions(f, data_offset)
                    if gpt_entries:
                        return gpt_entries
                    f.seek(resume_pos)
                entries.append(
                    PartitionInfo(
                        number=i + 1,
                        part_type=part_type,
                        offset=data_offset + lba_start * SECTOR_SIZE,
                        size=lba_size * SECTOR_SIZE,
                        active=status == 0x80,
                    )
                )
    except OSError:
        return []
    return entries
