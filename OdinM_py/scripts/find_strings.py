"""Scan a whole image (or disk region) for IP addresses and other literals.

Used to check whether a value like the nCompass server address appears
anywhere besides the place we expect. Reports every hit with its byte offset
and which partition it lands in.

Usage:
    python find_strings.py IMAGE [--ip] [--find TEXT ...]
"""

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from odin_img import read_header, read_partitions  # noqa: E402

CHUNK = 1 << 24
IPV4 = re.compile(
    rb"(?<![0-9.])((?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])\."
    rb"(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])\."
    rb"(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])\."
    rb"(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9]))(?![0-9.])")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--ip", action="store_true", help="scan for IPv4 literals")
    ap.add_argument("--find", action="append", default=[],
                    help="literal ASCII string to locate (repeatable)")
    ap.add_argument("--max-per-value", type=int, default=12)
    args = ap.parse_args()

    img = Path(args.image)
    size = img.stat().st_size
    hdr = read_header(img)
    try:
        parts = read_partitions(img, hdr)
    except ValueError:
        parts = []
    base = hdr.data_offset if hdr else 0

    def where(off):
        for p in parts:
            s = p.file_offset(hdr)
            if s <= off < s + p.size:
                return (f"part{p.index} 0x{p.ptype:02X} "
                        f"+{off - s}")
        return f"outside partitions (+{off - base} in payload)"

    needles = [s.encode() for s in args.find]
    hits = defaultdict(list)

    with img.open("rb") as f:
        pos = 0
        prev = b""
        while pos < size:
            buf = f.read(CHUNK)
            if not buf:
                break
            scan = prev + buf
            start = pos - len(prev)
            if args.ip:
                for m in IPV4.finditer(scan):
                    off = start + m.start()
                    if off >= pos - len(prev):
                        hits[m.group(1).decode()].append(off)
            for n in needles:
                i = scan.find(n)
                while i >= 0:
                    hits[n.decode()].append(start + i)
                    i = scan.find(n, i + 1)
            prev = scan[-64:]
            pos += len(buf)

    print(f"{img.name}  ({size} bytes)")
    for p in parts:
        print(f"  part{p.index} type 0x{p.ptype:02X} "
              f"file {p.file_offset(hdr)}..{p.file_offset(hdr) + p.size}")
    if not hits:
        print("\nno matches")
        return
    print(f"\n{len(hits)} distinct value(s):\n")
    for val, offs in sorted(hits.items(), key=lambda kv: -len(kv[1])):
        # de-dup offsets that overlap from chunk stitching
        uniq = sorted(set(offs))
        print(f"  {val!r}  x{len(uniq)}")
        for o in uniq[:args.max_per_value]:
            print(f"      @{o}  {where(o)}")
        if len(uniq) > args.max_per_value:
            print(f"      ... {len(uniq) - args.max_per_value} more")


if __name__ == "__main__":
    main()
