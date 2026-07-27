"""Audit a live attached card: partition table, FAT32 root, ext4 file SHA-1s.

The counterpart to audit_images.py, reading \\.\PhysicalDriveN instead of an
image file. Opens GENERIC_READ only - nothing here can write to the card.

Usage:
    python audit_disk.py 2 [3 ...]
"""

import hashlib
import struct
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import ext4  # noqa: E402
from raw_disk import RawDiskReader  # noqa: E402

SECTOR = 512
FT = ext4.EXT4_FT
TYPE_NAMES = {0x0B: "FAT32", 0x0C: "FAT32 LBA", 0x83: "Linux",
              0x05: "Extended", 0x07: "NTFS/exFAT", 0xEE: "GPT protective"}


def human(n):
    for unit in ("B", "KiB", "MiB", "GiB"):
        if abs(n) < 1024 or unit == "GiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1024
    return f"{n} B"


def partitions(f):
    f.seek(0)
    mbr = f.read(SECTOR)
    if mbr[510:512] != b"\x55\xaa":
        return []
    out = []
    for i in range(4):
        e = mbr[446 + i * 16:462 + i * 16]
        if e[4] == 0:
            continue
        lba, cnt = struct.unpack("<II", e[8:16])
        if cnt:
            out.append((i + 1, e[4], lba, cnt))
    return out


def fat_root(f, base):
    f.seek(base)
    bs = f.read(512)
    bps = struct.unpack_from("<H", bs, 11)[0] or 512
    spc = bs[13] or 1
    rsvd = struct.unpack_from("<H", bs, 14)[0]
    nfats = bs[16]
    spf = struct.unpack_from("<I", bs, 36)[0]
    root_clus = struct.unpack_from("<I", bs, 44)[0]
    label = bs[71:82].decode("ascii", "replace").strip()
    if not spf or not root_clus:
        print("    (not FAT32)")
        return
    print(f"    label {label!r}  oem {bs[3:11].decode('ascii', 'replace')!r}")
    data_start = base + (rsvd + nfats * spf) * bps
    clus = spc * bps
    fat_off = base + rsvd * bps

    def nxt(c):
        f.seek(fat_off + c * 4)
        return struct.unpack("<I", f.read(4))[0] & 0x0FFFFFFF

    chain, c = [], root_clus
    while 2 <= c < 0x0FFFFFF8 and len(chain) < 4096:
        chain.append(c)
        c = nxt(c)
    lfn, total, n = [], 0, 0
    for cl in chain:
        f.seek(data_start + (cl - 2) * clus)
        blob = f.read(clus)
        for i in range(0, len(blob), 32):
            e = blob[i:i + 32]
            if len(e) < 32 or e[0] == 0:
                break
            if e[0] == 0xE5:
                continue
            if e[11] == 0x0F:
                lfn.insert(0, (e[1:11] + e[14:26] + e[28:32]).decode(
                    "utf-16-le", "replace"))
                continue
            nm = ("".join(lfn).split("￿")[0].rstrip("\x00") if lfn
                  else e[0:8].decode("ascii", "replace").strip())
            lfn = []
            if e[11] & 0x08:
                continue
            sz = struct.unpack_from("<I", e, 28)[0]
            isdir = bool(e[11] & 0x10)
            if not isdir:
                total += sz
            n += 1
            print(f"    {'DIR ' if isdir else 'file'} {nm:<34} "
                  f"{'' if isdir else human(sz)}")
    print(f"    {n} entries, {human(total)} of file data")


def ext4_tree(f, base):
    vol = ext4.Volume(f, offset=base)
    s = vol.superblock
    print(f"    uuid {bytes(s.s_uuid).hex()}  "
          f"label {bytes(s.s_volume_name).rstrip(bytes(1)).decode('ascii', 'replace')!r}"
          f"  mounts {s.s_mnt_count}")
    print(f"    {s.s_blocks_count_lo} blocks x {vol.block_size} "
          f"({human(s.s_blocks_count_lo * vol.block_size)}), "
          f"{s.s_free_blocks_count_lo} free")
    stack, seen, rows = [("", vol.root)], set(), []
    while stack:
        prefix, node = stack.pop()
        if node.i_no in seen:
            continue
        seen.add(node.i_no)
        try:
            entries = list(node.opendir())
        except Exception:
            continue
        for dirent, ft in entries:
            nm = dirent.name_str
            if nm in (".", ".."):
                continue
            fp = f"{prefix}/{nm}"
            try:
                ino = vol.inodes[dirent.inode]
            except Exception:
                continue
            if ft == FT.DIR:
                rows.append(("D", fp, 0, ""))
                stack.append((fp, ino))
            elif ft == FT.REG_FILE:
                try:
                    data = ino.open().read(ino.i_size)
                    h = hashlib.sha1(data).hexdigest()
                except Exception as e:
                    data, h = b"", f"<unreadable {type(e).__name__}>"
                rows.append(("f", fp, len(data), h))
    nf = nd = 0
    for kind, fp, sz, h in sorted(rows, key=lambda r: r[1]):
        if kind == "D":
            nd += 1
            print(f"    D {fp}")
        else:
            nf += 1
            print(f"    f {fp:<44} {sz:>9} B  {h}")
    print(f"    {nf} files, {nd} dirs")


def audit(n):
    print("=" * 78)
    print(f"PhysicalDrive{n}")
    print("=" * 78)
    try:
        f = RawDiskReader(n)
    except OSError as e:
        print(f"  cannot open: {e}")
        return
    try:
        print(f"  size        : {f.size} ({human(f.size)})")
        parts = partitions(f)
        print(f"  partitions  : {len(parts)}")
        for i, t, lba, cnt in parts:
            print(f"    [{i}] type 0x{t:02X} ({TYPE_NAMES.get(t, '?')}) "
                  f"lba {lba} sectors {cnt} ({human(cnt * SECTOR)}) "
                  f"byte_off {lba * SECTOR}")
        for i, t, lba, cnt in parts:
            if t in (0x0B, 0x0C):
                print(f"  FAT32 partition {i}:")
                fat_root(f, lba * SECTOR)
        for i, t, lba, cnt in parts:
            if t == 0x83:
                print(f"  ext4 partition {i}:")
                try:
                    ext4_tree(f, lba * SECTOR)
                except Exception as e:
                    print(f"    walk failed: {type(e).__name__}: {e}")
    finally:
        f.close()


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        audit(int(arg))
        print()
