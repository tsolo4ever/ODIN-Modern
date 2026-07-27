"""Read a dot-corrupted ODIN image as if the spurious bytes were never there.

ODIN wrote ASCII '.' progress bytes into the image stream, so every byte after
the first dot is displaced. This module finds the dot offsets and exposes a
file-like view that skips them, restoring the original byte stream.

Dots are only *directly* detectable where they landed in zero-filled space, so
the result is validated against ext4 backup superblocks: between two anchors
the number of removed dots must exactly equal the measured drift difference.
"""

import bisect
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from odin_img import read_header, read_partitions  # noqa: E402

DOT = 0x2E
CTX = 24


def find_dots(path, start=0, end=None, ctx=CTX):
    """Return file offsets of isolated 0x2E bytes surrounded by `ctx` zeros."""
    path = Path(path)
    end = end if end is not None else path.stat().st_size
    out = []
    CH = 1 << 24
    with path.open("rb") as f:
        f.seek(max(0, start - ctx))
        pos = max(0, start - ctx)
        prev = b""
        while pos < end:
            buf = f.read(min(CH, end + ctx - pos))
            if not buf:
                break
            scan = prev + buf
            base = pos - len(prev)
            i = scan.find(DOT, len(prev))
            while i >= 0:
                lo, hi = i - ctx, i + ctx + 1
                if lo >= 0 and hi <= len(scan) and \
                        scan[lo:i] == bytes(ctx) and scan[i + 1:hi] == bytes(ctx):
                    off = base + i
                    if start <= off < end:
                        out.append(off)
                i = scan.find(DOT, i + 1)
            prev = scan[-(2 * ctx + 2):]
            pos += len(buf)
    return out


class DedottedWindow:
    """File-like view of [start, ...) with `dots` (absolute file offsets) removed.

    Logical offset 0 == file offset `start`. Length is `length` logical bytes.
    """

    def __init__(self, path, start, length, dots):
        self._f = open(path, "rb")
        self._start = start
        self._len = length
        self._dots = sorted(d for d in dots if d >= start)
        self._pos = 0

    @property
    def size(self):
        return self._len

    def _file_offset(self, logical):
        """File offset holding the byte at `logical`, accounting for removals."""
        # f = start + logical + (dots in [start, f)) - solved by iteration;
        # converges immediately because dots are sparse.
        f = self._start + logical
        while True:
            n = bisect.bisect_left(self._dots, f + 1)
            cand = self._start + logical + n
            if cand == f:
                return f
            f = cand

    def seek(self, offset, whence=0):
        if whence == 1:
            offset += self._pos
        elif whence == 2:
            offset += self._len
        self._pos = offset
        return self._pos

    def tell(self):
        return self._pos

    def read(self, size=-1):
        if size < 0:
            size = max(0, self._len - self._pos)
        size = max(0, min(size, self._len - self._pos))
        if size == 0:
            return b""
        f0 = self._file_offset(self._pos)
        # how many dots fall inside the span we are about to read
        i0 = bisect.bisect_left(self._dots, f0)
        i1 = bisect.bisect_left(self._dots, f0 + size)
        extra = i1 - i0
        # re-widen until the raw span covers `size` real bytes
        while True:
            raw = size + extra
            i1b = bisect.bisect_left(self._dots, f0 + raw)
            if i1b - i0 == extra:
                break
            extra = i1b - i0
        self._f.seek(f0)
        buf = self._f.read(size + extra)
        if extra:
            keep = bytearray()
            prev = 0
            for d in self._dots[i0:i0 + extra]:
                rel = d - f0
                if rel < 0 or rel >= len(buf):
                    continue
                keep += buf[prev:rel]
                prev = rel + 1
            keep += buf[prev:]
            buf = bytes(keep[:size])
        else:
            buf = buf[:size]
        self._pos += len(buf)
        return buf

    def peek(self, size):
        saved = self._pos
        try:
            return self.read(size)
        finally:
            self._pos = saved

    def close(self):
        self._f.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _read_sb_fields(blob):
    if len(blob) < 1024 or blob[0x38:0x3A] != b"\x53\xef":
        return None
    return {
        "inodes": struct.unpack_from("<I", blob, 0x00)[0],
        "blocks": struct.unpack_from("<I", blob, 0x04)[0],
        "log_bs": struct.unpack_from("<I", blob, 0x18)[0],
        "bpg": struct.unpack_from("<I", blob, 0x20)[0],
        "ipg": struct.unpack_from("<I", blob, 0x28)[0],
        "grp_nr": struct.unpack_from("<H", blob, 0x5A)[0],
        "uuid": blob[0x68:0x78].hex(),
    }


def locate_ext4(path, verbose=True):
    """Return (partition_start_file_offset, size, drift) for the ext4 partition.

    The MBR gives the intended offset; the real filesystem may be displaced by
    dots written earlier in the stream, so the superblock is searched for.
    """
    hdr = read_header(path)
    ext = next(p for p in read_partitions(path, hdr) if p.ptype == 0x83)
    nominal = ext.file_offset(hdr)
    with open(path, "rb") as f:
        for d in range(-2048, 65536):
            f.seek(nominal + 1024 + d)
            sb = _read_sb_fields(f.read(1024))
            if sb and sb["grp_nr"] == 0 and sb["log_bs"] <= 6 and sb["bpg"]:
                if verbose:
                    print(f"  ext4 primary superblock drift: {d:+d}")
                return nominal + d, ext.size, d, sb
    raise SystemExit("ext4 primary superblock not found")


def _anchors(path, start, sb):
    """Backup-superblock positions: [(group, actual file offset, drift)].

    A backup superblock sits at the START of its group's first block; only the
    primary is at +1024.
    """
    bsize = 1024 << sb["log_bs"]
    ngroups = (sb["blocks"] + sb["bpg"] - 1) // sb["bpg"]
    groups = {1}
    for pr in (3, 5, 7):
        k = pr
        while k < ngroups:
            groups.add(k)
            k *= pr
    out = [(0, start + 1024, 0)]
    with open(path, "rb") as f:
        for g in sorted(groups):
            expected = start + g * sb["bpg"] * bsize
            for d in range(-64, 16384):
                f.seek(expected + d)
                c = _read_sb_fields(f.read(1024))
                if c and c["grp_nr"] == g and c["uuid"] == sb["uuid"] \
                        and c["blocks"] == sb["blocks"]:
                    out.append((g, expected + d, d))
                    break
    return out


# Dots land every ~37-41 MiB, but the observed schedule steps in whole MiB of
# data; 38 MiB + the dot itself is by far the most common stride.
STRIDE = 38 * (1 << 20) + 1


def infer_hidden_dots(path, start, sb, visible, verbose=True):
    """Add dots that fell inside dense metadata and so are invisible.

    Each span between backup superblocks must contain exactly as many dots as
    its drift increased by. Where we are short, the missing dot is placed at
    prev_dot + STRIDE inside the oversized gap, and the choice is only accepted
    if a literal 0x2E byte is actually sitting there.
    """
    anchors = _anchors(path, start, sb)
    extra = []
    with open(path, "rb") as f:
        prev_g, prev_off, prev_d = anchors[0]
        for g, off, d in anchors[1:]:
            required = d - prev_d
            seen = sum(1 for x in visible if prev_off <= x < off)
            for _ in range(required - seen):
                known = sorted([prev_off] + [x for x in visible + extra
                                             if prev_off <= x < off])
                placed = False
                for lo in reversed(known):
                    cand = lo + STRIDE
                    if not (lo < cand < off) or cand in visible or cand in extra:
                        continue
                    f.seek(cand)
                    if f.read(1) == b"\x2e":
                        extra.append(cand)
                        placed = True
                        if verbose:
                            print(f"  inferred hidden dot @{cand} "
                                  f"(span grp {prev_g}->{g})")
                        break
                if not placed and verbose:
                    print(f"  WARNING: could not place hidden dot in span "
                          f"grp {prev_g}->{g} ({prev_off}..{off})")
            prev_g, prev_off, prev_d = g, off, d
    return sorted(extra)


def build(path, verbose=True):
    """Return a validated DedottedWindow over the ext4 partition."""
    path = Path(path)
    start, size, drift, sb = locate_ext4(path, verbose)
    fsize = path.stat().st_size
    dots = find_dots(path, start, fsize)
    if verbose:
        print(f"  visible dots at/after ext4 start: {len(dots)}")
    hidden = infer_hidden_dots(path, start, sb, dots, verbose)
    dots = sorted(dots + hidden)
    if verbose:
        print(f"  total dots removed: {len(dots)} "
              f"({len(hidden)} inferred inside dense data)")
    win = DedottedWindow(path, start, size, dots)
    return win, sb, dots, start, drift


def validate(path, win, sb, verbose=True):
    """Check every sparse-group backup superblock lands exactly where it should."""
    bsize = 1024 << sb["log_bs"]
    ngroups = (sb["blocks"] + sb["bpg"] - 1) // sb["bpg"]
    groups = {1}
    for p in (3, 5, 7):
        k = p
        while k < ngroups:
            groups.add(k)
            k *= p
    ok, bad = 0, []
    for g in sorted(groups):
        win.seek(g * sb["bpg"] * bsize)
        cand = _read_sb_fields(win.read(1024))
        if cand and cand["grp_nr"] == g and cand["uuid"] == sb["uuid"] \
                and cand["blocks"] == sb["blocks"]:
            ok += 1
        else:
            bad.append(g)
    if verbose:
        print(f"  backup superblocks aligned: {ok}/{len(groups)}"
              + (f"   MISALIGNED: {bad}" if bad else "   (all exact)"))
    return not bad


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    p = sys.argv[1]
    print(f"=== {Path(p).name} ===")
    win, sb, dots, start, drift = build(p)
    print(f"  partition start file offset: {start}  size {win.size}")
    print(f"  label uuid {sb['uuid']}  blocks {sb['blocks']} x "
          f"{1024 << sb['log_bs']}")
    validate(p, win, sb)
    win.close()
