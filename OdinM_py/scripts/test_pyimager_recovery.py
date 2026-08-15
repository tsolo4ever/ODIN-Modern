"""Headless checks for adaptive pyimager read recovery."""

import hashlib
import io
import sys
import threading
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import pyimager  # noqa: E402


SECTOR = 512
checks: list[bool] = []


def check(name, got, want):
    ok = got == want
    checks.append(ok)
    def describe(value):
        if isinstance(value, (bytes, bytearray)):
            digest = hashlib.sha256(value).hexdigest()[:12]
            return f"<{len(value)} bytes sha256={digest}>"
        return repr(value)

    print(
        f"  [{'ok ' if ok else 'FAIL'}] {name}: {describe(got)}"
        + ("" if ok else f" (expected {describe(want)})")
    )


class MemoryDisk:
    def __init__(self, data):
        self.data = data
        self.position = 0
        self.requests = []

    def seek(self, offset):
        self.position = offset

    def _read(self, size):
        start = self.position
        data = self.data[start:start + size]
        self.position += len(data)
        return data


class LimitedRequestDisk(MemoryDisk):
    def __init__(self, data, maximum):
        super().__init__(data)
        self.maximum = maximum

    def read(self, size):
        self.requests.append((self.position, size))
        if size > self.maximum:
            raise OSError("request too large")
        return self._read(size)


class BadSectorDisk(MemoryDisk):
    def __init__(self, data, bad_sector):
        super().__init__(data)
        self.bad_start = bad_sector * SECTOR
        self.bad_end = self.bad_start + SECTOR

    def read(self, size):
        start = self.position
        self.requests.append((start, size))
        if start < self.bad_end and start + size > self.bad_start:
            raise OSError("unreadable sector")
        return self._read(size)


class SecondChunkLimitedDisk(MemoryDisk):
    def __init__(self, data, boundary, stop_event):
        super().__init__(data)
        self.boundary = boundary
        self.stop_event = stop_event
        self.recovered_sectors = 0

    def read(self, size):
        start = self.position
        self.requests.append((start, size))
        if start >= self.boundary and size > SECTOR:
            raise OSError("second chunk needs recovery")
        data = self._read(size)
        if start >= self.boundary and size == SECTOR:
            self.recovered_sectors += 1
            if self.recovered_sectors == self.boundary // SECTOR:
                self.stop_event.set()
        return data


class ShortReadDisk(LimitedRequestDisk):
    def read(self, size):
        self.requests.append((self.position, size))
        if size > self.maximum:
            return self._read(size - 1)
        return self._read(size)


def copy(disk, start, length, chunk, *, should_cancel=None):
    output = io.BytesIO()
    digest = hashlib.sha256()
    progress = []
    logs = []
    result = pyimager.copy_stream(
        disk,
        output,
        start,
        length,
        SECTOR,
        chunk,
        [digest],
        on_progress=lambda done, total: progress.append((done, total)),
        should_cancel=should_cancel,
        on_log=logs.append,
    )
    return result, output.getvalue(), digest.hexdigest(), progress, logs


print("large request rejection:")
source = bytes((index % 251 for index in range(256 * SECTOR)))
disk = LimitedRequestDisk(source, pyimager.RECOVERY_SCAN_BYTES)
result, output, digest, progress, logs = copy(
    disk, 0, len(source), len(source)
)
check("copy result", result, (len(source), [], False))
check("output exact", output, source)
check("digest exact", digest, hashlib.sha256(source).hexdigest())
check("no sector fallback", any(size == SECTOR for _, size in disk.requests), False)
check("adaptive read count", len(disk.requests) <= 3, True)
check("committed progress", progress, [(len(source), len(source))])
check("recovery logged", "adaptive recovery" in logs[0], True)

print("\nsector-only request ceiling:")
source = bytes((index % 241 for index in range(2048 * SECTOR)))
disk = LimitedRequestDisk(source, SECTOR)
result, output, digest, progress, logs = copy(
    disk, 0, len(source), len(source)
)
check("sector-only result", result, (len(source), [], False))
check("sector-only output", output, source)
check("sector-only digest", digest, hashlib.sha256(source).hexdigest())
check("sector-only request count bounded", len(disk.requests) <= 2100, True)
check("sector-only progress", progress, [(len(source), len(source))])

print("\none unreadable sector:")
source = bytes((index % 239 for index in range(14 * SECTOR)))
start = 2 * SECTOR
length = 8 * SECTOR
bad_sector = 6
disk = BadSectorDisk(source, bad_sector)
result, output, digest, progress, logs = copy(
    disk, start, length, length
)
expected = bytearray(source[start:start + length])
bad_relative = bad_sector * SECTOR - start
expected[bad_relative:bad_relative + SECTOR] = bytes(SECTOR)
expected = bytes(expected)
check("bad sector result", result, (length, [bad_sector], False))
check("only bad sector zero-filled", output, expected)
check(
    "following data not shifted",
    output[-SECTOR:],
    source[start + length - SECTOR:start + length],
)
check("zero-filled digest", digest, hashlib.sha256(expected).hexdigest())
check("zero fill logged", "zero-filled 1 unreadable sector" in logs[-1], True)
check("bad-sector progress", progress, [(length, length)])
bad_reads = [
    request for request in disk.requests
    if request == (bad_sector * SECTOR, SECTOR)
]
check("bad sector retry count", len(bad_reads), 3)

print("\ncancellation after final recovery read:")
chunk = 4 * SECTOR
source = bytes((index % 227 for index in range(2 * chunk)))
stop_event = threading.Event()
disk = SecondChunkLimitedDisk(source, chunk, stop_event)
result, output, digest, progress, logs = copy(
    disk, 0, len(source), chunk, should_cancel=stop_event.is_set
)
check("cancel result", result, (chunk, [], True))
check("unfinished chunk discarded", output, source[:chunk])
check("cancel digest", digest, hashlib.sha256(source[:chunk]).hexdigest())
check("cancel progress", progress, [(chunk, len(source))])
check("cancel occurred on final read", disk.recovered_sectors, chunk // SECTOR)

print("\nshort read recovery:")
source = bytes((index % 211 for index in range(8 * SECTOR)))
disk = ShortReadDisk(source, 2 * SECTOR)
result, output, digest, progress, logs = copy(
    disk, 0, len(source), len(source)
)
check("short-read result", result, (len(source), [], False))
check("short-read output", output, source)
check("short-read digest", digest, hashlib.sha256(source).hexdigest())
check("short-read logged", "short read" in logs[0], True)
check("short-read progress", progress, [(len(source), len(source))])

print(f"\n{sum(checks)}/{len(checks)} checks passed")
sys.exit(0 if all(checks) else 1)
