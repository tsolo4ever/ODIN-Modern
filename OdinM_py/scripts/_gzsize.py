"""Report a gzip member's declared uncompressed size (ISIZE, mod 2^32)."""

import struct
import sys
from pathlib import Path

p = Path(sys.argv[1])
size = p.stat().st_size
with p.open("rb") as f:
    f.seek(-8, 2)
    crc32, isize = struct.unpack("<II", f.read(8))

print(f"{p.name}")
print(f"  compressed   : {size} ({size / (1 << 20):.1f} MiB)")
print(f"  gzip CRC32   : 0x{crc32:08x}")
print(f"  ISIZE field  : {isize}  (uncompressed size mod 2^32)")
for k in range(4):
    cand = isize + k * (1 << 32)
    print(f"    if {k} wrap(s): {cand} bytes ({cand / (1 << 30):.3f} GiB)")

target = int(sys.argv[2]) if len(sys.argv) > 2 else None
if target is not None:
    print(f"\n  target disk  : {target} bytes ({target / (1 << 30):.3f} GiB)")
    print(f"  target mod 2^32 = {target % (1 << 32)}")
    print(f"  ISIZE matches target: {target % (1 << 32) == isize}")
