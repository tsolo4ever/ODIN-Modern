"""Extract every file from both ext4 partitions and hunt for a 10-hex-digit ID.

Looks for the literal target as ASCII (either case), as packed 5-byte binary,
and reports every other 10-hex-digit token seen so near-misses are visible.
"""

import re
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent))
import ext4
from raw_disk import RawDiskReader

TARGET = "699DFF00EA"
DISKS = [(2, "E", 1083179008), (3, "F", 1082130432)]
OUTDIR = Path(__file__).parent / "extracted"
FT_DIR = ext4.EXT4_FT.DIR


def all_files(vol):
    """Yield (path, Inode) for every regular file in the volume."""
    stack, seen = [("", vol.root)], set()
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
            name = dirent.name_str
            if name in (".", ".."):
                continue
            path = f"{prefix}/{name}"
            try:
                inode = vol.inodes[dirent.inode]
            except Exception:
                continue
            if ft == FT_DIR:
                stack.append((path, inode))
            elif ft == ext4.EXT4_FT.REG_FILE:
                yield path, inode


needles = {
    "ASCII upper": TARGET.upper().encode(),
    "ASCII lower": TARGET.lower().encode(),
    "packed binary": bytes.fromhex(TARGET),
    "byte-reversed": bytes.fromhex(TARGET)[::-1],
}
hex_token = re.compile(rb"(?<![0-9A-Fa-f])[0-9A-Fa-f]{10}(?![0-9A-Fa-f])")

found_any = False
all_tokens = Counter()

for disk_num, letter, part_off in DISKS:
    print(f"\n{'=' * 72}\n{letter}: (PhysicalDrive{disk_num})\n{'=' * 72}")
    f = RawDiskReader(disk_num)
    try:
        vol = ext4.Volume(f, offset=part_off)
        for path, inode in all_files(vol):
            try:
                data = inode.open().read(inode.i_size)
            except Exception as e:
                print(f"  {path}: unreadable ({type(e).__name__})")
                continue

            dest = OUTDIR / letter / Path(path).relative_to("/")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)

            notes = []
            for label, needle in needles.items():
                cnt = data.count(needle)
                if cnt:
                    notes.append(f"*** {label} x{cnt} ***")
                    found_any = True
            toks = Counter(m.group(0).decode() for m in hex_token.finditer(data))
            all_tokens.update(toks)
            print(f"  {path:44s} {len(data):>8d} B  "
                  f"{' '.join(notes) if notes else ''}")
    finally:
        f.close()

print(f"\n{'=' * 72}")
print(f"literal {TARGET} found: {found_any}")
print(f"{'=' * 72}")
print(f"\nall 10-hex-digit tokens seen across every file "
      f"({len(all_tokens)} distinct):")
for tok, cnt in all_tokens.most_common(40):
    print(f"  {cnt:5d}x  {tok}")
