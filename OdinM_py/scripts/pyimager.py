#!/usr/bin/env python3
"""pyimager - a dependency-free raw disk imager for Windows.

Writes plain dd-style images: the output file contains the disk's bytes and
nothing else. Progress goes to stderr, metadata goes to a sidecar .json, and
hashes go to a sidecar .sha256 - never into the image stream. (ODIN wrote its
ASCII '.' progress marks into the image itself, displacing every byte after the
first one; this tool exists partly so that cannot happen.)

Commands
    list                              show physical disks
    image  <disk#> <out.img>          read disk -> file, hash while reading
    verify <disk#> <img>              re-read disk and compare against the file
    restore <img> <disk#>             write file -> disk  (guarded, destructive)

Examples
    python pyimager.py list
    python pyimager.py image 2 D:\\cards\\before.img
    python pyimager.py verify 2 D:\\cards\\before.img
    python pyimager.py image 2 D:\\cards\\part2.img --partition 2

Only `restore` opens a disk for writing; every other command opens GENERIC_READ.
"""

from __future__ import annotations

import argparse
import ctypes
import gzip
import hashlib
import json
import struct
import sys
import time
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x1
FILE_SHARE_WRITE = 0x2
OPEN_EXISTING = 3
FILE_FLAG_SEQUENTIAL_SCAN = 0x08000000
INVALID_HANDLE = ctypes.c_void_p(-1).value
FILE_BEGIN = 0
SECTOR = 512

IOCTL_DISK_GET_LENGTH_INFO = 0x0007405C
IOCTL_DISK_GET_DRIVE_GEOMETRY_EX = 0x000700A0
IOCTL_STORAGE_QUERY_PROPERTY = 0x002D1400
IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS = 0x00560000
IOCTL_DISK_UPDATE_PROPERTIES = 0x00070140
FSCTL_LOCK_VOLUME = 0x00090018
FSCTL_UNLOCK_VOLUME = 0x0009001C
FSCTL_DISMOUNT_VOLUME = 0x00090020

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
# HANDLE is pointer-sized; without this ctypes truncates it to a signed 32-bit
# int and every later call fails with ERROR_INVALID_HANDLE.
_k32.CreateFileW.restype = wintypes.HANDLE
_k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                             wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                             wintypes.HANDLE]
_k32.SetFilePointerEx.argtypes = [wintypes.HANDLE, ctypes.c_longlong,
                                  ctypes.POINTER(ctypes.c_longlong),
                                  wintypes.DWORD]
_k32.ReadFile.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                          ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
_k32.WriteFile.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                           ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
_k32.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                 wintypes.LPVOID, wintypes.DWORD,
                                 wintypes.LPVOID, wintypes.DWORD,
                                 ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
_k32.CloseHandle.argtypes = [wintypes.HANDLE]


def _err(msg):
    e = ctypes.get_last_error()
    return OSError(f"{msg} (err={e}: {ctypes.FormatError(e)})")


class Win32Disk:
    """Raw block device. Opens read-only unless `write=True` is passed."""

    def __init__(self, path: str, write: bool = False):
        access = GENERIC_READ | (GENERIC_WRITE if write else 0)
        self.path = path
        self.writable = write
        self.h = _k32.CreateFileW(path, access,
                                  FILE_SHARE_READ | FILE_SHARE_WRITE, None,
                                  OPEN_EXISTING, FILE_FLAG_SEQUENTIAL_SCAN, None)
        if self.h in (None, INVALID_HANDLE):
            raise _err(f"cannot open {path}"
                       + ("" if write else " for reading"))

    # -- ioctls ------------------------------------------------------------
    def _ioctl(self, code, outbuf=None, inbuf=None):
        returned = wintypes.DWORD(0)
        ok = _k32.DeviceIoControl(
            self.h, code,
            ctypes.byref(inbuf) if inbuf is not None else None,
            ctypes.sizeof(inbuf) if inbuf is not None else 0,
            ctypes.byref(outbuf) if outbuf is not None else None,
            ctypes.sizeof(outbuf) if outbuf is not None else 0,
            ctypes.byref(returned), None)
        return bool(ok), returned.value

    @property
    def size(self) -> int:
        out = ctypes.c_ulonglong(0)
        ok, _ = self._ioctl(IOCTL_DISK_GET_LENGTH_INFO, out)
        if not ok:
            raise _err("IOCTL_DISK_GET_LENGTH_INFO failed")
        return out.value

    @property
    def sector_size(self) -> int:
        buf = ctypes.create_string_buffer(64)
        ok, _ = self._ioctl(IOCTL_DISK_GET_DRIVE_GEOMETRY_EX, buf)
        if not ok:
            return 512
        # DISK_GEOMETRY: LARGE_INTEGER Cylinders; MEDIA_TYPE; DWORD Tracks,
        # Sectors, BytesPerSector -> BytesPerSector at offset 20
        bps = struct.unpack_from("<I", buf.raw, 20)[0]
        return bps if bps in (512, 1024, 2048, 4096) else 512

    @property
    def media_type(self) -> int:
        buf = ctypes.create_string_buffer(64)
        ok, _ = self._ioctl(IOCTL_DISK_GET_DRIVE_GEOMETRY_EX, buf)
        return struct.unpack_from("<I", buf.raw, 8)[0] if ok else -1

    @property
    def removable(self) -> bool:
        # MEDIA_TYPE 11 == RemovableMedia
        return self.media_type == 11

    def device_info(self) -> dict:
        """Vendor / product / serial, best effort."""
        query = ctypes.create_string_buffer(struct.pack("<III", 0, 0, 0))
        buf = ctypes.create_string_buffer(1024)
        ok, n = self._ioctl(IOCTL_STORAGE_QUERY_PROPERTY, buf, query)
        if not ok or n < 40:
            return {}
        raw = buf.raw
        removable = raw[10] != 0

        def s(off):
            o = struct.unpack_from("<I", raw, off)[0]
            if not o or o >= len(raw):
                return ""
            end = raw.find(b"\0", o)
            return raw[o:end if end >= 0 else len(raw)].decode(
                "ascii", "replace").strip()

        return {"vendor": s(12), "product": s(16), "revision": s(20),
                "serial": s(24), "removable": removable}

    # -- io ----------------------------------------------------------------
    def seek(self, offset: int):
        if not _k32.SetFilePointerEx(self.h, offset, None, FILE_BEGIN):
            raise _err(f"seek to {offset} failed")

    def read(self, n: int) -> bytes:
        buf = ctypes.create_string_buffer(n)
        got = wintypes.DWORD(0)
        if not _k32.ReadFile(self.h, buf, n, ctypes.byref(got), None):
            raise _err(f"read of {n} bytes failed")
        return buf.raw[:got.value]

    def write(self, data: bytes) -> int:
        if not self.writable:
            raise RuntimeError("disk opened read-only")
        put = wintypes.DWORD(0)
        if not _k32.WriteFile(self.h, data, len(data), ctypes.byref(put), None):
            raise _err(f"write of {len(data)} bytes failed")
        return put.value

    def lock(self):
        return self._ioctl(FSCTL_LOCK_VOLUME)[0]

    def dismount(self):
        return self._ioctl(FSCTL_DISMOUNT_VOLUME)[0]

    def unlock(self):
        return self._ioctl(FSCTL_UNLOCK_VOLUME)[0]

    def update_properties(self):
        return self._ioctl(IOCTL_DISK_UPDATE_PROPERTIES)[0]

    def close(self):
        if self.h not in (None, INVALID_HANDLE):
            _k32.CloseHandle(self.h)
            self.h = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def volumes_on_disk(disk_no: int):
    """Drive letters whose extents live on the given physical disk."""
    out = []
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        try:
            v = Win32Disk(rf"\\.\{letter}:")
        except OSError:
            continue
        try:
            buf = ctypes.create_string_buffer(4096)
            ok, n = v._ioctl(IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS, buf)
            if ok and n >= 8:
                count = struct.unpack_from("<I", buf.raw, 0)[0]
                for i in range(count):
                    # DISK_EXTENT { DWORD DiskNumber; LARGE_INTEGER Start, Len }
                    dn = struct.unpack_from("<I", buf.raw, 8 + i * 24)[0]
                    if dn == disk_no:
                        out.append(letter)
                        break
        finally:
            v.close()
    return out


def human(n: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(n) < 1024 or unit == "TiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1024
    return f"{n} B"


def read_partitions(first_sectors: bytes, sector: int = 512):
    """Primary MBR entries as (index, type, lba_start, sectors)."""
    if first_sectors[510:512] != b"\x55\xaa":
        return []
    out = []
    for i in range(4):
        e = first_sectors[446 + i * 16: 462 + i * 16]
        if e[4] == 0:
            continue
        lba, cnt = struct.unpack("<II", e[8:16])
        if cnt:
            out.append((i + 1, e[4], lba, cnt))
    return out


class Progress:
    """Progress reporting - stderr only, never the output stream."""

    def __init__(self, total, quiet=False, interval=0.5):
        self.total, self.quiet, self.interval = total, quiet, interval
        self.t0 = time.monotonic()
        self.last = 0.0

    def update(self, done, force=False):
        if self.quiet:
            return
        now = time.monotonic()
        if not force and now - self.last < self.interval:
            return
        self.last = now
        el = now - self.t0
        rate = done / el if el > 0 else 0
        pct = 100.0 * done / self.total if self.total else 0
        eta = (self.total - done) / rate if rate > 0 else 0
        sys.stderr.write(
            f"\r  {pct:6.2f}%  {human(done)} / {human(self.total)}  "
            f"{human(rate)}/s  elapsed {el:6.0f}s  eta {eta:6.0f}s   ")
        sys.stderr.flush()

    def done(self, done):
        if self.quiet:
            return
        self.update(done, force=True)
        sys.stderr.write("\n")
        sys.stderr.flush()


def copy_stream(disk, out_fh, start, length, sector, chunk, hashers,
                on_progress=None, should_cancel=None, retries=2):
    """Copy `length` bytes from disk@start to out_fh, hashing as we go.

    On a read error the chunk is retried, then split to sector granularity so a
    single bad sector costs one sector, not one chunk. Unreadable sectors are
    written as zeros and reported - never silently skipped, because skipping
    would shift every following byte.

    `on_progress(done, length)` is called after each chunk; `should_cancel()`
    aborts the copy between chunks. Returns (bytes_copied, bad_sectors,
    cancelled).
    """
    done = 0
    bad = []
    cancelled = False
    disk.seek(start)
    while done < length:
        if should_cancel is not None and should_cancel():
            cancelled = True
            break
        want = min(chunk, length - done)
        want -= want % sector or 0
        if want == 0:
            want = min(sector, length - done)
        try:
            disk.seek(start + done)
            buf = disk.read(want)
            if len(buf) < want:
                raise OSError(f"short read {len(buf)}/{want}")
        except OSError:
            buf = b""
            for off in range(0, want, sector):
                sec = None
                for _ in range(retries + 1):
                    try:
                        disk.seek(start + done + off)
                        sec = disk.read(sector)
                        if len(sec) == sector:
                            break
                        sec = None
                    except OSError:
                        sec = None
                if sec is None:
                    bad.append((start + done + off) // sector)
                    sec = bytes(sector)
                buf += sec
            buf = buf[:want]
        for h in hashers:
            h.update(buf)
        if out_fh is not None:
            out_fh.write(buf)
        done += len(buf)
        if on_progress is not None:
            on_progress(done, length)
    return done, bad, cancelled


def cmd_list(args):
    print(f"{'disk':>5}  {'size':>12}  {'sector':>6}  {'rem':>3}  "
          f"{'volumes':<10}  device")
    print("-" * 88)
    found = 0
    for n in range(args.max_disks):
        try:
            d = Win32Disk(rf"\\.\PhysicalDrive{n}")
        except OSError:
            continue
        try:
            size = d.size
            sec = d.sector_size
            info = d.device_info()
            vols = ",".join(v + ":" for v in volumes_on_disk(n)) or "-"
            name = " ".join(x for x in (info.get("vendor"), info.get("product"))
                            if x) or "?"
            serial = info.get("serial") or ""
            rem = "yes" if info.get("removable") or d.removable else "no"
            print(f"{n:>5}  {human(size):>12}  {sec:>6}  {rem:>3}  "
                  f"{vols:<10}  {name}" + (f"  [sn {serial}]" if serial else ""))
            found += 1
        except OSError as e:
            print(f"{n:>5}  <error: {e}>")
        finally:
            d.close()
    if not found:
        print("no disks readable - run this from an elevated shell")


def _sidecars(out_path: Path, meta: dict, digests: dict):
    out_path.with_suffix(out_path.suffix + ".json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8")
    lines = [f"{v}  {out_path.name}\n" for k, v in digests.items()
             if k == "sha256"]
    if lines:
        out_path.with_suffix(out_path.suffix + ".sha256").write_text(
            "".join(lines), encoding="utf-8")


def is_gz_path(path) -> bool:
    return str(path).lower().endswith(".gz")


ODIN_MAGIC = bytes.fromhex("737b4d1d01fae140b0945267d8fa0be7")


def validate_image_file(path, probe_bytes=1 << 20) -> dict:
    """Check that `path` really holds a disk image, decompressing .gz if needed.

    A .gz is only useful to us if there is an actual disk image inside it, so
    this decompresses the first megabyte and looks for either an ODIN header or
    an MBR boot signature, then reads the partition table.

    Returns a dict with `ok`, `reason`, `container`, `inner_format`,
    `partitions` and `data_offset`.
    """
    out = {"ok": False, "reason": "", "container": "raw", "inner_format": None,
           "partitions": [], "data_offset": 0, "path": str(path)}
    p = Path(path)
    if not p.exists():
        out["reason"] = "file does not exist"
        return out
    if p.stat().st_size == 0:
        out["reason"] = "file is empty"
        return out

    try:
        if is_gz_path(p):
            out["container"] = "gzip"
            with gzip.open(p, "rb") as f:
                head = f.read(probe_bytes)
            if not head:
                out["reason"] = "gzip archive is empty"
                return out
        else:
            with p.open("rb") as f:
                head = f.read(probe_bytes)
    except (OSError, EOFError, gzip.BadGzipFile) as e:
        out["reason"] = f"cannot read: {type(e).__name__}: {e}"
        return out

    base = 0
    if head[:16] == ODIN_MAGIC:
        out["inner_format"] = "odin"
        if len(head) < 128:
            out["reason"] = "truncated ODIN header"
            return out
        base = struct.unpack_from("<Q", head, 0x58)[0]  # dataOffset
        out["data_offset"] = base
        data_size = struct.unpack_from("<Q", head, 0x60)[0]
        if data_size == 0:
            out["reason"] = ("ODIN header present but dataSize is 0 - the "
                             "capture was aborted, there is no disk image "
                             "inside")
            return out
    else:
        out["inner_format"] = "raw"

    if len(head) < base + 512:
        out["reason"] = "file is too small to contain a partition table"
        return out
    if head[base + 510:base + 512] != b"\x55\xaa":
        out["reason"] = ("no MBR boot signature - this does not look like a "
                         "disk image")
        return out

    parts = read_partitions(head[base:base + 512])
    if not parts:
        out["reason"] = "MBR signature present but no partitions defined"
        return out
    out["partitions"] = [{"index": i, "type": f"0x{t:02X}", "lba_start": l,
                          "sectors": c, "size": c * SECTOR}
                         for i, t, l, c in parts]
    out["ok"] = True
    kind = "ODIN" if out["inner_format"] == "odin" else "raw"
    out["reason"] = (f"valid {kind} disk image"
                     + (" inside gzip" if out["container"] == "gzip" else "")
                     + f", {len(parts)} partition(s)")
    return out


def cmd_validate(args):
    rc = 0
    for path in args.images:
        r = validate_image_file(path)
        mark = "OK  " if r["ok"] else "BAD "
        print(f"{mark} {path}")
        print(f"      container {r['container']}, inner {r['inner_format']}")
        print(f"      {r['reason']}")
        for p in r["partitions"]:
            print(f"        part {p['index']}: type {p['type']} "
                  f"lba {p['lba_start']} ({human(p['size'])})")
        if not r["ok"]:
            rc = 1
    return rc


def image_disk(disk_number, output, *, partition=None, offset=0, length=0,
               chunk=8 << 20, sha1=False, gzip_level=None, force=False,
               on_progress=None, on_log=None, should_cancel=None,
               write_sidecars=True):
    """Image a disk (or a region of it) to a raw file. Library entry point.

    `output` ending in .gz is gzip-compressed on the fly; the digests always
    describe the UNCOMPRESSED disk bytes, so they stay comparable to a plain
    image and to a live disk read.

    `on_progress(done, total)` and `on_log(str)` are optional callbacks.
    `should_cancel()` returning True aborts the copy.

    Returns the metadata dict (also written as a .json sidecar), with
    ``cancelled`` set if the copy was stopped early.
    """
    out = Path(output)
    if out.exists() and not force:
        raise FileExistsError(f"{out} already exists")

    def log(msg):
        if on_log:
            on_log(msg)

    with Win32Disk(rf"\\.\PhysicalDrive{disk_number}") as d:
        sector = d.sector_size
        disk_size = d.size
        info = d.device_info()
        d.seek(0)
        parts = read_partitions(d.read(max(sector, 512)), sector)

        start, span, what = 0, disk_size, "whole disk"
        if partition:
            match = [p for p in parts if p[0] == partition]
            if not match:
                raise ValueError(
                    f"disk {disk_number} has no partition {partition} "
                    f"(found {[p[0] for p in parts] or 'none'})")
            _, ptype, lba, cnt = match[0]
            start, span = lba * sector, cnt * sector
            what = f"partition {partition} (type 0x{ptype:02X})"
        if offset or length:
            if partition:
                raise ValueError("offset/length cannot be combined with "
                                 "partition")
            start = offset or 0
            span = length or (disk_size - start)
            if start % sector:
                raise ValueError(f"offset must be a multiple of the sector "
                                 f"size ({sector})")
            if start + span > disk_size:
                raise ValueError(f"region {start}+{span} runs past the end of "
                                 f"the disk ({disk_size})")
            what = f"bytes {start}..{start + span}"

        name = " ".join(x for x in (info.get("vendor"), info.get("product"))
                        if x) or "?"
        log(f"source: PhysicalDrive{disk_number} {name}")
        if info.get("serial"):
            log(f"serial: {info['serial']}")
        log(f"disk {disk_size} bytes ({human(disk_size)}), sector {sector}, "
            f"{len(parts)} partition(s)")
        log(f"reading {what}: {span} bytes ({human(span)})")
        log(f"output: {out}" + (" (gzip)" if is_gz_path(out) else ""))

        hashers = {"sha256": hashlib.sha256()}
        if sha1:
            hashers["sha1"] = hashlib.sha1()
        t0 = datetime.now(timezone.utc)
        # Binary mode - no newline translation, no text encoding; nothing but
        # payload is ever written to this handle.
        if is_gz_path(out):
            fh = gzip.GzipFile(
                filename=str(out), mode="wb",
                compresslevel=6 if gzip_level is None else gzip_level)
        else:
            fh = out.open("wb")
        try:
            copied, bad, cancelled = copy_stream(
                d, fh, start, span, sector, chunk, hashers.values(),
                on_progress=on_progress, should_cancel=should_cancel)
        finally:
            fh.close()
        t1 = datetime.now(timezone.utc)

    digests = {k: h.hexdigest() for k, h in hashers.items()}
    meta = {
        "tool": "pyimager",
        "format": "raw.gz" if is_gz_path(out) else "raw",
        "source": f"PhysicalDrive{disk_number}", "device": info,
        "disk_size": disk_size, "sector_size": sector,
        "region": what, "region_offset": start, "region_length": span,
        "bytes_written": copied,
        "stored_bytes": out.stat().st_size if out.exists() else 0,
        "partitions": [{"index": i, "type": f"0x{t:02X}", "lba_start": l,
                        "sectors": c} for i, t, l, c in parts],
        "started_utc": t0.isoformat(), "finished_utc": t1.isoformat(),
        "duration_s": round((t1 - t0).total_seconds(), 1),
        "digests": digests,
        "bad_sectors": bad[:1000], "bad_sector_count": len(bad),
        "cancelled": cancelled,
    }
    if write_sidecars and not cancelled:
        _sidecars(out, meta, digests)
    return meta


def cmd_image(args):
    out = Path(args.output)
    prog = Progress(0, args.quiet)

    def on_progress(done, total):
        if prog.total != total:
            prog.total = total
        prog.update(done)

    try:
        meta = image_disk(
            args.disk, out, partition=args.partition, offset=args.offset,
            length=args.length, chunk=args.chunk, sha1=args.sha1,
            gzip_level=args.gzip_level, force=args.force,
            on_progress=on_progress,
            on_log=lambda m: print(m, file=sys.stderr))
    except (FileExistsError, ValueError) as e:
        sys.exit(str(e))
    prog.done(meta["bytes_written"])

    copied, length = meta["bytes_written"], meta["region_length"]
    print(f"\nwrote {copied} bytes to {out}", file=sys.stderr)
    if meta["format"] == "raw.gz":
        stored = meta["stored_bytes"]
        ratio = (100.0 * stored / copied) if copied else 0
        print(f"compressed: {stored} bytes on disk ({human(stored)}, "
              f"{ratio:.1f}% of raw)", file=sys.stderr)
    if copied != length:
        print(f"WARNING: expected {length} bytes", file=sys.stderr)
    for k, v in meta["digests"].items():
        print(f"{k:<8}: {v}   (of the uncompressed disk bytes)"
              if meta["format"] == "raw.gz" else f"{k:<8}: {v}",
              file=sys.stderr)
    bad = meta["bad_sectors"]
    if bad:
        print(f"WARNING: {meta['bad_sector_count']} unreadable sector(s), "
              f"zero-filled; first few: {bad[:8]}", file=sys.stderr)
    else:
        print("no read errors", file=sys.stderr)
    print(f"sidecar   : {out.name}.json / {out.name}.sha256", file=sys.stderr)


def cmd_verify(args):
    img = Path(args.image)
    if not img.exists():
        sys.exit(f"{img} not found")
    meta_path = img.with_suffix(img.suffix + ".json")
    meta = json.loads(meta_path.read_text(encoding="utf-8")) \
        if meta_path.exists() else {}
    start = meta.get("region_offset", 0)
    length = meta.get("region_length", img.stat().st_size)
    if length != img.stat().st_size:
        print(f"note: image is {img.stat().st_size} bytes but metadata says "
              f"{length}; comparing {min(length, img.stat().st_size)}",
              file=sys.stderr)
        length = min(length, img.stat().st_size)

    with Win32Disk(rf"\\.\PhysicalDrive{args.disk}") as d:
        sector = d.sector_size
        print(f"comparing PhysicalDrive{args.disk} @{start} against {img.name}",
              file=sys.stderr)
        prog = Progress(length, args.quiet)
        h_disk, h_file = hashlib.sha256(), hashlib.sha256()
        mismatches = []
        done = 0
        with img.open("rb") as fh:
            while done < length:
                want = min(args.chunk, length - done)
                d.seek(start + done)
                try:
                    a = d.read(want)
                except OSError:
                    a = b""
                b = fh.read(want)
                if len(a) < len(b):
                    a += bytes(len(b) - len(a))
                h_disk.update(a)
                h_file.update(b)
                if a != b:
                    for i in range(0, len(b), sector):
                        if a[i:i + sector] != b[i:i + sector]:
                            mismatches.append((start + done + i) // sector)
                            if len(mismatches) > 10000:
                                break
                done += len(b)
                prog.update(done)
        prog.done(done)

    print(f"disk sha256: {h_disk.hexdigest()}", file=sys.stderr)
    print(f"file sha256: {h_file.hexdigest()}", file=sys.stderr)
    recorded = (meta.get("digests") or {}).get("sha256")
    if recorded:
        print(f"recorded   : {recorded}  "
              f"{'MATCH' if recorded == h_file.hexdigest() else 'MISMATCH'}",
              file=sys.stderr)
    if h_disk.hexdigest() == h_file.hexdigest():
        print("VERIFY OK - disk and image are byte-identical", file=sys.stderr)
        return 0
    print(f"VERIFY FAILED - {len(mismatches)} differing sector(s); "
          f"first few: {mismatches[:8]}", file=sys.stderr)
    return 1


def cmd_restore(args):
    img = Path(args.image)
    if not img.exists():
        sys.exit(f"{img} not found")
    if args.confirm != args.disk:
        sys.exit("refusing to write: pass --confirm <disk#> matching the "
                 "target, e.g. --confirm " + str(args.disk))

    with Win32Disk(rf"\\.\PhysicalDrive{args.disk}") as probe:
        size, sector = probe.size, probe.sector_size
        info = probe.device_info()
        removable = info.get("removable") or probe.removable
    vols = volumes_on_disk(args.disk)

    print(f"TARGET     : PhysicalDrive{args.disk}  "
          f"{info.get('vendor', '')} {info.get('product', '')}".rstrip(),
          file=sys.stderr)
    print(f"size       : {size} bytes ({human(size)})", file=sys.stderr)
    print(f"mounted as : {', '.join(v + ':' for v in vols) or 'none'}",
          file=sys.stderr)
    print(f"source     : {img} ({human(img.stat().st_size)})", file=sys.stderr)
    if not removable and not args.allow_fixed:
        sys.exit("target is not removable media - pass --allow-fixed if you "
                 "are certain")
    if img.stat().st_size > size:
        sys.exit(f"image ({img.stat().st_size}) is larger than the disk ({size})")

    locked = []
    try:
        for letter in vols:
            v = Win32Disk(rf"\\.\{letter}:", write=True)
            if v.lock() and v.dismount():
                locked.append(v)
                print(f"locked and dismounted {letter}:", file=sys.stderr)
            else:
                v.close()
                sys.exit(f"could not lock {letter}: - close anything using it")

        length = img.stat().st_size
        prog = Progress(length, args.quiet)
        h = hashlib.sha256()
        with Win32Disk(rf"\\.\PhysicalDrive{args.disk}", write=True) as d, \
                img.open("rb") as fh:
            d.seek(0)
            done = 0
            while done < length:
                buf = fh.read(args.chunk)
                if not buf:
                    break
                if len(buf) % sector:
                    buf += bytes(sector - len(buf) % sector)
                h.update(buf)
                done += d.write(buf)
                prog.update(min(done, length))
            prog.done(min(done, length))
            d.update_properties()
        print(f"\nwrote {done} bytes; source sha256 {h.hexdigest()}",
              file=sys.stderr)
    finally:
        for v in locked:
            v.unlock()
            v.close()
    print("restore complete - run `verify` to confirm", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(
        description="Dependency-free raw disk imager for Windows.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Commands", 1)[1])
    ap.add_argument("--chunk", type=lambda s: int(s, 0), default=8 << 20,
                    help="I/O chunk size in bytes (default 8 MiB)")
    ap.add_argument("--quiet", action="store_true", help="no progress output")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="show physical disks")
    p.add_argument("--max-disks", type=int, default=16)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("image", help="read a disk into a raw .img")
    p.add_argument("disk", type=int)
    p.add_argument("output")
    p.add_argument("--partition", type=int,
                   help="image only this MBR partition (1-4)")
    p.add_argument("--offset", type=lambda s: int(s, 0), default=0,
                   help="start byte offset (must be sector aligned)")
    p.add_argument("--length", type=lambda s: int(s, 0), default=0,
                   help="number of bytes to read (default: to end of disk)")
    p.add_argument("--sha1", action="store_true", help="also compute SHA-1")
    p.add_argument("--force", action="store_true", help="overwrite output")
    p.add_argument("--gzip-level", type=int, default=None, choices=range(1, 10),
                   metavar="1-9",
                   help="gzip compression level; an output path ending in .gz "
                        "is compressed automatically (default level 6)")
    p.set_defaults(func=cmd_image)

    p = sub.add_parser("validate",
                       help="check a file really contains a disk image")
    p.add_argument("images", nargs="+")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("verify", help="compare a disk against an image")
    p.add_argument("disk", type=int)
    p.add_argument("image")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("restore", help="write an image to a disk (DESTRUCTIVE)")
    p.add_argument("image")
    p.add_argument("disk", type=int)
    p.add_argument("--confirm", type=int, required=True,
                   help="must equal the target disk number")
    p.add_argument("--allow-fixed", action="store_true",
                   help="permit writing to non-removable media")
    p.set_defaults(func=cmd_restore)

    args = ap.parse_args()
    if sys.platform != "win32":
        sys.exit("pyimager targets Windows physical drives")
    sys.exit(args.func(args) or 0)


if __name__ == "__main__":
    main()
