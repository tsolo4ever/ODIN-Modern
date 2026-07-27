"""Dump named files out of the ext4 partition on F: (PhysicalDrive3), read-only."""

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent))
import ext4
from raw_disk import RawDiskReader

DISK, PART_OFF = 3, 1082130432
OUTDIR = Path(__file__).parent / "extracted"
TARGETS = [
    "/etc/network/interfaces",
]

OUTDIR.mkdir(exist_ok=True)
f = RawDiskReader(DISK)
try:
    vol = ext4.Volume(f, offset=PART_OFF)
    for path in TARGETS:
        print(f"\n{'=' * 72}\n{path}\n{'=' * 72}")
        try:
            inode = vol.inode_at(path)
            data = inode.open().read(inode.i_size)
        except Exception as e:
            print(f"  ERROR: {type(e).__name__}: {e}")
            continue

        dest = OUTDIR / Path(path).name
        dest.write_bytes(data)
        print(f"[{len(data)} bytes -> {dest}]\n")
        try:
            print(data.decode("utf-8"))
        except UnicodeDecodeError:
            print(data.decode("latin-1"))
finally:
    f.close()
