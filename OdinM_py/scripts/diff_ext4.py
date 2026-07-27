"""Read-only filesystem-level diff of the ext4 partitions on two physical disks.

Walks both directory trees and reports what exists on F: but not on E: (i.e. what
the system added), plus removals and size changes. Opens the disks GENERIC_READ
only — nothing here can write to either drive.
"""

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent))
import ext4
from raw_disk import RawDiskReader

DISKS = [(2, "E:", 1083179008), (3, "F:", 1082130432)]
FT_DIR = 2
MAX_LIST = 400  # cap per section so output stays readable


def walk(vol, label):
    """Return {path: (is_dir, size)} for every entry in the volume."""
    tree = {}
    seen_dirs = set()
    stack = [("", vol.root)]
    n = 0
    while stack:
        prefix, node = stack.pop()
        if node.i_no in seen_dirs:
            continue
        seen_dirs.add(node.i_no)

        try:
            entries = list(node.opendir())
        except Exception as e:
            tree[prefix + "/<unreadable>"] = (False, -1)
            print(f"  [{label}] warn: cannot read {prefix or '/'}: {type(e).__name__}: {e}",
                  file=sys.stderr)
            continue

        for dirent, file_type in entries:
            name = dirent.name_str
            if name in (".", ".."):
                continue
            path = f"{prefix}/{name}"
            is_dir = file_type == ext4.EXT4_FT.DIR

            inode = None
            try:
                inode = vol.inodes[dirent.inode]
                # NB: inode.size is sizeof(struct); i_size is the file length
                size = 0 if is_dir else inode.i_size
            except Exception:
                size = -1
            tree[path] = (is_dir, size)
            n += 1
            if n % 5000 == 0:
                print(f"  [{label}] {n} entries...", file=sys.stderr)
            if is_dir and inode is not None:
                stack.append((path, inode))
    return tree


def human(n):
    if n < 0:
        return "?"
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024 or unit == "GiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


trees = {}
for disk_num, letter, part_off in DISKS:
    print(f"walking {letter} ...", file=sys.stderr)
    f = RawDiskReader(disk_num)
    try:
        vol = ext4.Volume(f, offset=part_off)
        trees[letter] = walk(vol, letter)
        print(f"  {letter}: {len(trees[letter])} entries", file=sys.stderr)
    finally:
        f.close()

for letter, tree in trees.items():
    print(f"\n{'=' * 70}\nFULL TREE {letter}  ({len(tree)} entries)\n{'=' * 70}")
    for p in sorted(tree):
        is_dir, sz = tree[p]
        print(f"  {'D' if is_dir else 'f'} {p}" + ("" if is_dir else f"  ({sz} B)"))

e, fdisk = trees["E:"], trees["F:"]
only_f = sorted(set(fdisk) - set(e))
only_e = sorted(set(e) - set(fdisk))
changed = sorted(
    p for p in set(e) & set(fdisk)
    if not e[p][0] and not fdisk[p][0] and e[p][1] != fdisk[p][1]
)


def section(title, paths, fmt):
    print(f"\n{'=' * 70}\n{title}  ({len(paths)})\n{'=' * 70}")
    for p in paths[:MAX_LIST]:
        print(fmt(p))
    if len(paths) > MAX_LIST:
        print(f"... and {len(paths) - MAX_LIST} more (raise MAX_LIST to see all)")


section("ONLY ON F: — added by the system", only_f,
        lambda p: f"  {'D' if fdisk[p][0] else 'f'} {p}"
                  + ("" if fdisk[p][0] else f"  ({human(fdisk[p][1])})"))
section("ONLY ON E: — missing from F:", only_e,
        lambda p: f"  {'D' if e[p][0] else 'f'} {p}"
                  + ("" if e[p][0] else f"  ({human(e[p][1])})"))
section("IN BOTH, DIFFERENT SIZE", changed,
        lambda p: f"  {p}  {human(e[p][1])} -> {human(fdisk[p][1])}")

print(f"\nsummary: E:={len(e)} entries  F:={len(fdisk)} entries  "
      f"+{len(only_f)} added  -{len(only_e)} removed  ~{len(changed)} modified")
