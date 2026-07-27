"""Prove or disprove progressive byte-drift in an ODIN image.

A 2-byte magic can match by chance, so every candidate ext4 backup superblock
is fully validated: s_magic, s_inodes_count, s_blocks_count, s_inodes_per_group
and s_blocks_per_group must all equal the primary, and s_block_group_nr must
equal the group we are probing. Only then is its position used to measure drift.
"""

import struct
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent))
from odin_img import read_header, read_partitions  # noqa: E402

img = Path(sys.argv[1])
hdr = read_header(img)
parts = read_partitions(img, hdr)
ext = next(p for p in parts if p.ptype == 0x83)
f = img.open("rb")
base = ext.file_offset(hdr)
fsize = img.stat().st_size

print(f"=== {img.name} ===")
print(f"dataOffset={hdr.data_offset}  fileSize(hdr)={hdr.raw['fileSize']}  "
      f"actual={fsize}  trailing={fsize - hdr.raw['fileSize']}")


def read_sb(off):
    f.seek(off)
    b = f.read(1024)
    if len(b) < 1024 or b[0x38:0x3A] != b"\x53\xef":
        return None
    return {
        "inodes": struct.unpack_from("<I", b, 0x00)[0],
        "blocks": struct.unpack_from("<I", b, 0x04)[0],
        "log_bs": struct.unpack_from("<I", b, 0x18)[0],
        "bpg": struct.unpack_from("<I", b, 0x20)[0],
        "ipg": struct.unpack_from("<I", b, 0x28)[0],
        "grp_nr": struct.unpack_from("<H", b, 0x5A)[0],
        "state": struct.unpack_from("<H", b, 0x3A)[0],
        "uuid": b[0x68:0x78].hex(),
        "label": b[0x78:0x88].rstrip(b"\0").decode("ascii", "replace"),
        "last_mounted": b[0x88:0xC8].rstrip(b"\0").decode("ascii", "replace"),
    }


# Find the primary superblock: scan a window around partition start + 1024.
prim, prim_delta = None, None
for d in range(-2048, 4096):
    sb = read_sb(base + 1024 + d)
    if sb and sb["grp_nr"] == 0 and sb["log_bs"] <= 6 and sb["bpg"]:
        prim, prim_delta = sb, d
        break
if not prim:
    print("  no primary superblock found")
    sys.exit(1)

bsize = 1024 << prim["log_bs"]
print(f"\nprimary superblock: delta {prim_delta:+d}")
print(f"  label={prim['label']!r} uuid={prim['uuid']}")
print(f"  last_mounted={prim['last_mounted']!r}")
print(f"  blocks={prim['blocks']} x {bsize} = "
      f"{prim['blocks'] * bsize / (1 << 30):.2f} GiB   bpg={prim['bpg']}  "
      f"ipg={prim['ipg']}  state={prim['state']}")


def sparse_groups(n):
    out = {1}
    for p in (3, 5, 7):
        k = p
        while k < n:
            out.add(k)
            k *= p
    return sorted(g for g in out if g < n)


ngroups = (prim["blocks"] + prim["bpg"] - 1) // prim["bpg"]
print(f"  groups={ngroups}")
print(f"\n{'group':>7} {'expected off':>14} {'delta':>7}  validated")
print("-" * 60)
print(f"{0:>7} {base + 1024:>14} {prim_delta:>+7}  yes (primary)")

points = [(base + 1024 - base, prim_delta)]
for g in sparse_groups(ngroups):
    expected = base + g * prim["bpg"] * bsize
    hit = None
    # widen the window as we go deeper - drift accumulates
    for d in range(-256, 8192):
        sb = read_sb(expected + d)
        if not sb:
            continue
        if (sb["grp_nr"] == g and sb["inodes"] == prim["inodes"]
                and sb["blocks"] == prim["blocks"] and sb["bpg"] == prim["bpg"]
                and sb["ipg"] == prim["ipg"] and sb["uuid"] == prim["uuid"]):
            hit = d
            break
    if hit is None:
        print(f"{g:>7} {expected:>14} {'--':>7}  not found/validated")
    else:
        print(f"{g:>7} {expected:>14} {hit:>+7}  yes")
        points.append((expected - base, hit))

f.close()

print("\ndrift model (delta vs absolute file offset):")
for rel, d in points:
    absoff = base + rel
    rate = absoff / d if d else float("inf")
    print(f"  file off {absoff:>12}  delta {d:>+5}   "
          + (f"1 byte per {rate / (1 << 20):.2f} MiB" if d else "clean"))

if len(points) >= 2 and points[-1][1]:
    last_abs = base + points[-1][0]
    projected = points[-1][1] * (hdr.raw["fileSize"] / last_abs)
    print(f"\nprojected drift at end of payload: {projected:.1f} bytes")
    print(f"actual trailing bytes in file      : {fsize - hdr.raw['fileSize']}")
