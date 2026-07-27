"""Build a per-file SHA-1 manifest of the ext4 partition inside an ODIN .img.

Walks every inode, hashes file contents, records mtime/size/uid/gid/mode, and
extracts each file to disk so the after-image can be diffed byte-for-byte.
Also whole-partition SHA-256 so "nothing changed at all" is provable in one line.

Usage:
    python ext4_manifest.py <image.img> <label> [--no-extract]

Writes  manifests/<label>.json  and  extracted/<label>/...
Read-only with respect to the image.
"""

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import ext4  # noqa: E402
import dedot  # noqa: E402
from odin_img import ImageWindow, read_header, read_partitions  # noqa: E402

CHUNK = 1 << 20
# 10 hex digits, not part of a longer hex run — the employee-card ID shape.
HEX10 = re.compile(rb"(?<![0-9A-Fa-f])[0-9A-Fa-f]{10}(?![0-9A-Fa-f])")
# Decimal card numbers are worth catching too; JCM/ncompass IDs show up both ways.
DEC10 = re.compile(rb"(?<![0-9])[0-9]{8,12}(?![0-9])")

FT = ext4.EXT4_FT


def iso(ts):
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(ts, timezone.utc).isoformat()
    except (OSError, OverflowError, ValueError):
        return f"raw:{ts}"


def ext4_partition(path):
    """Return (window, offset, size, repair_info) for the ext4 partition.

    ODIN corrupted some images by writing progress dots into the byte stream.
    When that is detected the window transparently skips them, so the caller
    sees the original filesystem.
    """
    hdr = read_header(path)
    parts = read_partitions(path, hdr)
    linux = [p for p in parts if p.ptype == 0x83]
    if not linux:
        raise SystemExit(f"{path}: no 0x83 Linux partition found")
    p = linux[0]
    off = p.file_offset(hdr)

    plain = ImageWindow(path, off, p.size)
    if plain.peek(2048)[1024 + 0x38:1024 + 0x3A] == b"\x53\xef":
        return plain, off, p.size, {"dot_corrupted": False}
    plain.close()

    print("  primary superblock not at the nominal offset - "
          "checking for ODIN progress-dot corruption")
    win, sb, dots, start, drift = dedot.build(path)
    if not dedot.validate(path, win, sb):
        raise SystemExit("de-dotted view failed backup-superblock validation; "
                         "refusing to produce a manifest from it")
    return win, start, p.size, {
        "dot_corrupted": True,
        "dots_removed": len(dots),
        "primary_drift": drift,
        "partition_start_file_offset": start,
    }


def walk(vol):
    """Yield (path, dirent, inode, file_type) for every entry, depth-first."""
    stack, seen = [("", vol.root)], set()
    while stack:
        prefix, node = stack.pop()
        if node.i_no in seen:
            continue
        seen.add(node.i_no)
        try:
            entries = list(node.opendir())
        except Exception as e:
            print(f"  warn: cannot read dir {prefix or '/'}: "
                  f"{type(e).__name__}: {e}", file=sys.stderr)
            continue
        for dirent, ft in entries:
            name = dirent.name_str
            if name in (".", ".."):
                continue
            path = f"{prefix}/{name}"
            try:
                inode = vol.inodes[dirent.inode]
            except Exception as e:
                print(f"  warn: bad inode for {path}: {type(e).__name__}",
                      file=sys.stderr)
                continue
            yield path, dirent, inode, ft
            if ft == FT.DIR:
                stack.append((path, inode))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("label", help="short name, e.g. 'baseline' or 'after'")
    ap.add_argument("--no-extract", action="store_true")
    ap.add_argument("--skip-partition-hash", action="store_true",
                    help="skip the 6.5 GiB whole-partition SHA-256")
    args = ap.parse_args()

    img = Path(args.image)
    outdir = HERE / "extracted" / args.label
    mandir = HERE / "manifests"
    mandir.mkdir(exist_ok=True)

    win, part_off, part_size, repair = ext4_partition(img)
    print(f"image     : {img}")
    print(f"ext4 part : file offset {part_off}, {part_size} bytes "
          f"({part_size / (1 << 30):.2f} GiB)")

    manifest = {
        "label": args.label,
        "image": str(img),
        "image_size": img.stat().st_size,
        "image_mtime": iso(img.stat().st_mtime),
        "partition_file_offset": part_off,
        "partition_size": part_size,
        "repair": repair,
        "files": {},
        "dirs": [],
        "other": {},
    }

    try:
        vol = ext4.Volume(win, offset=0)
        sb = vol.superblock
        manifest["fs"] = {
            "uuid": bytes(sb.s_uuid).hex(),
            "volume_name": bytes(sb.s_volume_name).rstrip(b"\0").decode(
                "ascii", "replace"),
            "mount_count": sb.s_mnt_count,
            "last_mounted": iso(sb.s_mtime),
            "last_written": iso(sb.s_wtime),
            "last_checked": iso(sb.s_lastcheck),
            "block_size": vol.block_size,
            "blocks_count": sb.s_blocks_count_lo,
            "free_blocks": sb.s_free_blocks_count_lo,
            "inodes_count": sb.s_inodes_count,
            "free_inodes": sb.s_free_inodes_count,
        }
        print(f"fs uuid   : {manifest['fs']['uuid']}")
        print(f"last write: {manifest['fs']['last_written']}   "
              f"mounts: {manifest['fs']['mount_count']}")
        print()

        tokens_hex, tokens_dec = {}, {}
        nfiles = 0
        for path, dirent, inode, ft in walk(vol):
            if ft == FT.DIR:
                manifest["dirs"].append(path)
                continue
            if ft != FT.REG_FILE:
                manifest["other"][path] = {
                    "type": getattr(ft, "name", str(ft)),
                    "inode": dirent.inode,
                }
                continue

            size = inode.i_size
            try:
                data = inode.open().read(size)
            except Exception as e:
                manifest["files"][path] = {
                    "error": f"{type(e).__name__}: {e}", "size": size,
                    "inode": dirent.inode,
                }
                print(f"  !! {path}: unreadable ({type(e).__name__})")
                continue

            entry = {
                "size": len(data),
                "declared_size": size,
                "sha1": hashlib.sha1(data).hexdigest(),
                "inode": dirent.inode,
                "mode": f"0o{inode.i_mode & 0o7777:04o}",
                "uid": inode.i_uid,
                "gid": inode.i_gid,
                "mtime": iso(inode.i_mtime),
                "ctime": iso(inode.i_ctime),
                "links": inode.i_links_count,
            }
            manifest["files"][path] = entry
            nfiles += 1

            for tok in {m.group(0).decode() for m in HEX10.finditer(data)}:
                tokens_hex.setdefault(tok, []).append(path)
            for tok in {m.group(0).decode() for m in DEC10.finditer(data)}:
                tokens_dec.setdefault(tok, []).append(path)

            if not args.no_extract:
                dest = outdir / path.lstrip("/")
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)

            print(f"  f {path:52s} {len(data):>9d} B  {entry['sha1'][:12]}  "
                  f"{entry['mtime']}")

        manifest["tokens_hex10"] = {k: sorted(set(v)) for k, v in tokens_hex.items()}
        manifest["tokens_dec"] = {k: sorted(set(v)) for k, v in tokens_dec.items()}

        if not args.skip_partition_hash:
            print(f"\nhashing whole partition ({part_size / (1 << 30):.2f} GiB)...")
            h = hashlib.sha256()
            win.seek(0)
            done = 0
            while done < part_size:
                buf = win.read(min(CHUNK, part_size - done))
                if not buf:
                    break
                h.update(buf)
                done += len(buf)
                if done % (512 << 20) == 0:
                    print(f"  {done / (1 << 30):.1f} GiB", file=sys.stderr)
            manifest["partition_sha256"] = h.hexdigest()
            manifest["partition_hashed_bytes"] = done
            print(f"partition sha256: {manifest['partition_sha256']}")
    finally:
        win.close()

    out = mandir / f"{args.label}.json"
    out.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\n{nfiles} files, {len(manifest['dirs'])} dirs, "
          f"{len(manifest['other'])} other")
    print(f"distinct 10-hex tokens: {len(manifest['tokens_hex10'])}   "
          f"decimal tokens: {len(manifest['tokens_dec'])}")
    print(f"manifest -> {out}")
    if not args.no_extract:
        print(f"extracted -> {outdir}")


if __name__ == "__main__":
    main()
