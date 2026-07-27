"""Find every non-zero region inside a partition of an ODIN image.

For an unformatted ext4 partition this answers: is it genuinely blank, or is
there residual data from a previous filesystem that could still be carved?
Also hashes the region so the after-image can be compared in one line.
"""

import hashlib
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent))
from odin_img import read_header, read_partitions  # noqa: E402

img = Path(sys.argv[1])
want_type = int(sys.argv[2], 0) if len(sys.argv) > 2 else 0x83
BLOCK = 1 << 20

hdr = read_header(img)
parts = read_partitions(img, hdr)
p = next(x for x in parts if x.ptype == want_type)
base, size = p.file_offset(hdr), p.size
print(f"{img.name} partition type 0x{p.ptype:02X}: file off {base}, "
      f"{size} bytes ({size / (1 << 30):.2f} GiB)")

f = img.open("rb")
f.seek(base)
h = hashlib.sha256()
zero_block = bytes(BLOCK)
nonzero_runs = []  # (start_rel, end_rel, nonzero_byte_count)
cur = None
done = 0
total_nz = 0

while done < size:
    n = min(BLOCK, size - done)
    buf = f.read(n)
    if not buf:
        break
    h.update(buf)
    is_zero = (buf == zero_block[:len(buf)])
    if not is_zero:
        nz = len(buf) - buf.count(0)
        total_nz += nz
        if cur is None:
            cur = [done, done + len(buf), nz]
        else:
            cur[1] = done + len(buf)
            cur[2] += nz
    elif cur is not None:
        nonzero_runs.append(tuple(cur))
        cur = None
    done += len(buf)

if cur is not None:
    nonzero_runs.append(tuple(cur))
f.close()

print(f"read {done} bytes")
print(f"sha256 : {h.hexdigest()}")
print(f"total non-zero bytes: {total_nz}")
print(f"non-zero runs: {len(nonzero_runs)}")
for s, e, nz in nonzero_runs[:60]:
    print(f"  rel {s:>12d} .. {e:<12d} ({(e - s) / (1 << 20):8.2f} MiB)  "
          f"{nz} non-zero bytes   file off {base + s}")
if len(nonzero_runs) > 60:
    print(f"  ... {len(nonzero_runs) - 60} more runs")

if nonzero_runs:
    s, e, _ = nonzero_runs[0]
    f = img.open("rb")
    f.seek(base + s)
    blob = f.read(min(4096, e - s))
    f.close()
    print("\nfirst non-zero run, first non-zero 256 bytes:")
    first_nz = next((i for i, b in enumerate(blob) if b), 0)
    start = max(0, first_nz - 16)
    for off in range(start, min(len(blob), start + 256), 16):
        chunk = blob[off : off + 16]
        hexs = " ".join(f"{b:02x}" for b in chunk).ljust(47)
        text = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        print(f"  rel {s + off:>12d}  {hexs}  |{text}|")
