"""Run the whole image toolkit over one or more disk images and report.

For each image: ODIN header, dot-corruption check, partition table, FAT32 root
listing, and an ext4 walk with per-file SHA-1. Handles raw images, ODIN images,
and .gz (streamed, header/partition inspection only unless --decompress-to is
given).

Usage:
    python audit_images.py IMAGE [IMAGE ...] [--manifest] [--decompress-to DIR]

Everything here is read-only with respect to the images.
"""

import argparse
import gzip
import hashlib
import struct
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import dedot  # noqa: E402
import ext4  # noqa: E402
from odin_img import (ImageWindow, read_header,  # noqa: E402
                      read_partitions, SECTOR)

FT = ext4.EXT4_FT


def human(n):
    for unit in ("B", "KiB", "MiB", "GiB"):
        if abs(n) < 1024 or unit == "GiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1024
    return f"{n} B"


def rule(ch="="):
    print(ch * 78)


def peek_gz(path, nbytes=1 << 20):
    with gzip.open(path, "rb") as f:
        return f.read(nbytes)


def audit_gz(path):
    print("  format        : gzip (compressed)")
    try:
        head = peek_gz(path, 1 << 20)
    except OSError as e:
        print(f"  ERROR reading gzip: {e}")
        return
    print(f"  decompressed head: {len(head)} bytes read")
    if head[:16] == bytes.fromhex("737b4d1d01fae140b0945267d8fa0be7"):
        print("  inner format  : ODIN image")
        base = struct.unpack_from("<Q", head, 0x58)[0]
    elif head[510:512] == b"\x55\xaa":
        print("  inner format  : raw disk image (MBR at byte 0)")
        base = 0
    else:
        print(f"  inner format  : unrecognised (first bytes {head[:16].hex()})")
        return
    if head[base + 510:base + 512] != b"\x55\xaa":
        print("  no MBR signature in decompressed stream")
        return
    for i in range(4):
        e = head[base + 446 + i * 16: base + 462 + i * 16]
        if e[4] == 0:
            continue
        lba, cnt = struct.unpack("<II", e[8:16])
        if cnt:
            print(f"    part {i + 1}: type 0x{e[4]:02X} lba {lba} "
                  f"sectors {cnt} ({human(cnt * SECTOR)})")
    print("  (full ext4 walk needs --decompress-to DIR)")


def fat_summary(path, hdr, part):
    base = part.file_offset(hdr)
    with open(path, "rb") as f:
        f.seek(base)
        bs = f.read(512)
        if bs[510:512] != b"\x55\xaa" and bs[:3] == b"\x00\x00\x00":
            print("    (no FAT boot sector here)")
            return
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
        lfn, files, total = [], [], 0
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
                total += sz
                files.append((nm, sz, bool(e[11] & 0x10)))
        for nm, sz, isdir in files:
            print(f"    {'DIR ' if isdir else 'file'} {nm:<32} "
                  f"{'' if isdir else human(sz)}")
        print(f"    {len(files)} entries, {human(total)} of file data")


def ext4_walk(path, want_manifest, label):
    """Walk the ext4 partition, repairing dot corruption if present."""
    hdr = read_header(path)
    parts = read_partitions(path, hdr)
    lin = [p for p in parts if p.ptype == 0x83]
    if not lin:
        print("    no 0x83 partition")
        return
    p = lin[0]
    off = p.file_offset(hdr)

    win = ImageWindow(path, off, p.size)
    if win.peek(2048)[1024 + 0x38:1024 + 0x3A] == b"\x53\xef":
        print("    superblock at the nominal offset (no displacement)")
    else:
        win.close()
        print("    superblock NOT at nominal offset -> checking dot corruption")
        try:
            win, sb, dots, start, drift = dedot.build(path, verbose=False)
        except SystemExit:
            print("    could not locate an ext4 superblock at all")
            return
        okv = dedot.validate(path, win, sb, verbose=False)
        print(f"    repaired: drift {drift:+d}, {len(dots)} dots removed, "
              f"backup-superblock validation {'PASSED' if okv else 'FAILED'}")
        if not okv:
            print("    refusing to trust the file list from this image")
            win.close()
            return
    try:
        vol = ext4.Volume(win, offset=0)
        s = vol.superblock
        print(f"    uuid {bytes(s.s_uuid).hex()}  label "
              f"{bytes(s.s_volume_name).rstrip(bytes(1)).decode('ascii', 'replace')!r}"
              f"  mounts {s.s_mnt_count}")
        print(f"    {s.s_blocks_count_lo} blocks x {vol.block_size} "
              f"({human(s.s_blocks_count_lo * vol.block_size)}), "
              f"{s.s_free_blocks_count_lo} free")
        stack, seen, nf, nd = [("", vol.root)], set(), 0, 0
        rows = []
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
                    nd += 1
                    rows.append(("D", fp, 0, ""))
                    stack.append((fp, ino))
                elif ft == FT.REG_FILE:
                    nf += 1
                    try:
                        data = ino.open().read(ino.i_size)
                        h = hashlib.sha1(data).hexdigest()
                    except Exception as e:
                        h = f"<unreadable {type(e).__name__}>"
                        data = b""
                    rows.append(("f", fp, len(data), h))
        for kind, fp, sz, h in sorted(rows, key=lambda r: r[1]):
            if kind == "D":
                print(f"    D {fp}")
            else:
                print(f"    f {fp:<44} {sz:>9} B  {h[:16]}")
        print(f"    {nf} files, {nd} dirs")
    except Exception as e:
        print(f"    ext4 walk failed: {type(e).__name__}: {e}")
    finally:
        win.close()


def audit(path, args):
    path = Path(path)
    rule()
    print(f"{path}")
    rule()
    if not path.exists():
        print("  MISSING")
        return
    size = path.stat().st_size
    print(f"  file size     : {size} ({human(size)})")

    if path.suffix.lower() == ".gz":
        audit_gz(path)
        return

    hdr = read_header(path)
    if hdr is None:
        print("  format        : raw (no ODIN header)")
    else:
        trailing = size - hdr.raw["fileSize"]
        print(f"  format        : ODIN v{hdr.version[0]}.{hdr.version[1]}  "
              f"dataOffset {hdr.data_offset}")
        print(f"  compression   : {hdr.compression} "
              f"({'none' if hdr.compression == 0 else 'COMPRESSED'})   "
              f"bitmap {hdr.bitmap_scheme} "
              f"({'all-blocks' if hdr.bitmap_scheme == 0 else 'USED-BLOCKS'})")
        print(f"  volumeSize    : {hdr.volume_size} ({human(hdr.volume_size)})"
              f"   dataSize {hdr.data_size}")
        print(f"  header fileSize {hdr.raw['fileSize']} vs actual {size}"
              f"  -> trailing {trailing}")
        # An aborted run leaves dataSize/fileSize at 0, so `trailing` is just
        # the header itself - that is not dot corruption.
        if hdr.raw["fileSize"] == 0 or hdr.data_size == 0:
            print("  DOT CHECK     : n/a - header says no data was written "
                  "(aborted or interrupted capture)")
        elif trailing == 0:
            print("  DOT CHECK     : clean (no inserted progress bytes)")
        elif trailing > 0:
            print(f"  DOT CHECK     : *** {trailing} EXTRA BYTES *** - "
                  f"likely ODIN progress dots written into the payload")
        else:
            print(f"  DOT CHECK     : file is {-trailing} bytes SHORT of the "
                  f"header's fileSize - truncated?")
        if size < hdr.data_offset + 512:
            print("  payload       : header only, no disk data present "
                  f"({size} bytes total, dataOffset {hdr.data_offset})")
            return

    try:
        parts = read_partitions(path, hdr)
    except ValueError as e:
        print(f"  partitions    : {e}")
        return
    print(f"  partitions    : {len(parts)}")
    for p in parts:
        print(f"    [{p.index}] type 0x{p.ptype:02X} ({p.type_name}) "
              f"lba {p.lba_start} sectors {p.sectors} "
              f"({human(p.size)}) file_off {p.file_offset(hdr)}"
              + ("  [boot]" if p.bootable else ""))

    for p in parts:
        if p.ptype in (0x0B, 0x0C):
            print(f"  FAT32 partition {p.index}:")
            fat_summary(path, hdr, p)
    if any(p.ptype == 0x83 for p in parts):
        print("  ext4 partition:")
        ext4_walk(path, args.manifest, path.stem)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("images", nargs="+")
    ap.add_argument("--manifest", action="store_true",
                    help="also write manifests/<name>.json")
    args = ap.parse_args()
    for img in args.images:
        audit(img, args)
        print()


if __name__ == "__main__":
    main()
