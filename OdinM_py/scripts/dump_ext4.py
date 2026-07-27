"""Dump the contents of every ext4 file on a live card, with ID hunting.

Prints each file as text when it is printable and as a hex dump otherwise, then
lists every 10-hex-digit and long-decimal token found - the shapes an employee
card number takes on these units.

Usage:
    python dump_ext4.py 2 [--max-bytes 4096]
"""

import argparse
import hashlib
import re
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
HEX10 = re.compile(rb"(?<![0-9A-Fa-f])[0-9A-Fa-f]{10}(?![0-9A-Fa-f])")
DEC = re.compile(rb"(?<![0-9])[0-9]{8,12}(?![0-9])")


def hexdump(data, limit):
    for off in range(0, min(len(data), limit), 16):
        chunk = data[off:off + 16]
        hexs = " ".join(f"{b:02x}" for b in chunk).ljust(47)
        text = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        print(f"      {off:08x}  {hexs}  |{text}|")
    if len(data) > limit:
        print(f"      ... {len(data) - limit} more bytes")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("disk", type=int)
    ap.add_argument("--max-bytes", type=int, default=4096)
    args = ap.parse_args()

    f = RawDiskReader(args.disk)
    try:
        f.seek(0)
        mbr = f.read(SECTOR)
        part = None
        for i in range(4):
            e = mbr[446 + i * 16:462 + i * 16]
            if e[4] == 0x83:
                part = struct.unpack("<II", e[8:16])[0] * SECTOR
                break
        if part is None:
            sys.exit("no ext4 partition")
        vol = ext4.Volume(f, offset=part)

        stack, seen, files = [("", vol.root)], set(), []
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
                    stack.append((fp, ino))
                elif ft == FT.REG_FILE:
                    files.append((fp, ino))

        tokens_hex, tokens_dec = {}, {}
        for fp, ino in sorted(files):
            try:
                data = ino.open().read(ino.i_size)
            except Exception as e:
                print(f"\n=== {fp} :: unreadable ({type(e).__name__}) ===")
                continue
            print(f"\n=== {fp}  ({len(data)} B, sha1 "
                  f"{hashlib.sha1(data).hexdigest()}) ===")
            printable = data.count(0) == 0 and all(
                9 <= b <= 13 or 32 <= b < 127 for b in data)
            if printable:
                text = data.decode("utf-8", "replace")
                for line in text.splitlines()[:80]:
                    print(f"      {line}")
                extra = len(text.splitlines()) - 80
                if extra > 0:
                    print(f"      ... {extra} more lines")
            else:
                hexdump(data, args.max_bytes)
            for t in {m.group(0).decode() for m in HEX10.finditer(data)}:
                tokens_hex.setdefault(t, []).append(fp)
            for t in {m.group(0).decode() for m in DEC.finditer(data)}:
                tokens_dec.setdefault(t, []).append(fp)

        print(f"\n{'=' * 70}\nID-shaped tokens\n{'=' * 70}")
        print(f"10-hex-digit ({len(tokens_hex)}):")
        for t, where in sorted(tokens_hex.items()):
            print(f"  {t}   {', '.join(where)}")
        print(f"decimal 8-12 digits ({len(tokens_dec)}):")
        for t, where in sorted(tokens_dec.items()):
            print(f"  {t}   {', '.join(where)}")
    finally:
        f.close()


if __name__ == "__main__":
    main()
