"""List the FAT32 root directory of an ODIN image, and re-scan for ext4
superblocks with correct (dataOffset-relative) alignment.

Pure-python FAT32 root walk — enough to identify which firmware a card carries.
"""

import struct
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent))
from odin_img import read_header, read_partitions  # noqa: E402

img = Path(sys.argv[1])
hdr = read_header(img)
parts = read_partitions(img, hdr)
f = img.open("rb")

print(f"=== {img.name} ===")

for p in parts:
    if p.ptype not in (0x0B, 0x0C):
        continue
    base = p.file_offset(hdr)
    f.seek(base)
    bs = f.read(512)
    bytes_per_sec = struct.unpack_from("<H", bs, 11)[0]
    sec_per_clus = bs[13]
    rsvd = struct.unpack_from("<H", bs, 14)[0]
    nfats = bs[16]
    sec_per_fat = struct.unpack_from("<I", bs, 36)[0]
    root_clus = struct.unpack_from("<I", bs, 44)[0]
    label = bs[71:82].decode("ascii", "replace").strip()
    tot_sec = struct.unpack_from("<I", bs, 32)[0]

    print(f"\nFAT32 partition[{p.index}] @ {base}")
    print(f"  label={label!r} bytes/sec={bytes_per_sec} sec/clus={sec_per_clus} "
          f"rsvd={rsvd} fats={nfats} sec/fat={sec_per_fat} root_clus={root_clus}")
    print(f"  total sectors={tot_sec} ({tot_sec * bytes_per_sec / (1 << 20):.1f} MiB)")

    data_start = base + (rsvd + nfats * sec_per_fat) * bytes_per_sec
    clus_bytes = sec_per_clus * bytes_per_sec

    def clus_off(c):
        return data_start + (c - 2) * clus_bytes

    # Follow the FAT chain for the root directory.
    fat_off = base + rsvd * bytes_per_sec

    def next_clus(c):
        f.seek(fat_off + c * 4)
        return struct.unpack("<I", f.read(4))[0] & 0x0FFFFFFF

    chain, c = [], root_clus
    while 2 <= c < 0x0FFFFFF8 and len(chain) < 4096:
        chain.append(c)
        c = next_clus(c)

    print(f"  root dir clusters: {len(chain)}")
    entries, lfn = [], []
    for c in chain:
        f.seek(clus_off(c))
        blob = f.read(clus_bytes)
        for i in range(0, len(blob), 32):
            e = blob[i : i + 32]
            if len(e) < 32 or e[0] == 0x00:
                break
            if e[0] == 0xE5:
                continue
            attr = e[11]
            if attr == 0x0F:  # long filename fragment
                part = (e[1:11] + e[14:26] + e[28:32]).decode("utf-16-le", "replace")
                lfn.insert(0, part.split("￿")[0].rstrip("\x00"))
                continue
            name = "".join(lfn).rstrip("\x00") if lfn else None
            lfn = []
            short = e[0:8].decode("ascii", "replace").strip()
            ext = e[8:11].decode("ascii", "replace").strip()
            shortname = f"{short}.{ext}" if ext else short
            size = struct.unpack_from("<I", e, 28)[0]
            first = (struct.unpack_from("<H", e, 20)[0] << 16) | \
                struct.unpack_from("<H", e, 26)[0]
            date = struct.unpack_from("<H", e, 24)[0]
            time_ = struct.unpack_from("<H", e, 22)[0]
            try:
                dt = datetime(1980 + (date >> 9), (date >> 5) & 0xF, date & 0x1F,
                              time_ >> 11, (time_ >> 5) & 0x3F,
                              (time_ & 0x1F) * 2).isoformat()
            except ValueError:
                dt = "?"
            entries.append((name or shortname, shortname, attr, size, first, dt))

    print(f"  root entries: {len(entries)}")
    total = 0
    for name, shortname, attr, size, first, dt in entries:
        kind = "DIR " if attr & 0x10 else ("VOL " if attr & 0x08 else "file")
        if attr & 0x08:
            print(f"    {kind} {name}")
            continue
        total += size
        print(f"    {kind} {name:34s} {size:>11d} B  clus={first:<8d} {dt}")
    print(f"  total file bytes in root: {total} ({total / (1 << 20):.1f} MiB)")

# Correctly-aligned ext4 superblock hunt: partitions are sector aligned within
# the PAYLOAD, so scan at dataOffset + n*512, not file offset n*512.
print("\n--- ext4 superblock scan, payload-aligned ---")
fsize = img.stat().st_size
CH = 8 << 20
hits = 0
pos = hdr.data_offset
f.seek(pos)
prev = b""
while pos < fsize:
    buf = f.read(CH)
    if not buf:
        break
    scan = prev + buf
    base_off = pos - len(prev)
    start = (-(base_off - hdr.data_offset)) % 512
    for i in range(start, max(0, len(scan) - 0x40), 512):
        if scan[i + 0x38 : i + 0x3A] != b"\x53\xef":
            continue
        blocks = int.from_bytes(scan[i + 0x04 : i + 0x08], "little")
        logbs = int.from_bytes(scan[i + 0x18 : i + 0x1C], "little")
        if logbs > 6 or blocks == 0:
            continue
        abs_off = base_off + i
        rel = abs_off - 1024 - hdr.data_offset
        print(f"  sb @file {abs_off} -> part start payload-rel {rel} "
              f"(lba {rel // 512}) blocks={blocks} bs={1024 << logbs}")
        hits += 1
        if hits >= 8:
            break
    if hits >= 8:
        break
    prev = scan[-64:]
    pos += len(buf)
if not hits:
    print("  none found")
f.close()
