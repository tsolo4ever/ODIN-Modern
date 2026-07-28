"""
pyimager_worker.py
Runs scripts/pyimager.py's image_disk() on a background thread, exposing the
same interface as CloneWorker so MakeImageDialog can swap engines freely.

Unlike the ODINC path this is in-process: progress is a real byte count rather
than parsed console dots, and the output is a plain dd-style image with no
container header (optionally gzip-compressed).
"""

import sys
import threading
from collections.abc import Callable
from pathlib import Path

from clone_worker import CloneStatus

# pyimager lives in scripts/ alongside the other card tooling.
_SCRIPTS = Path(__file__).resolve().parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import pyimager  # noqa: E402


def randomize_disk_signature(disk_number: int, volumes=None) -> bytes:
    """Re-exported from pyimager - see its docstring. Lets app.py fix up a
    freshly-cloned disk's Windows mountability without reaching into
    scripts/ directly, regardless of which engine (ODIN or pyimager) did
    the actual flash.

    Pass `volumes` (the disk's already-known bare drive letters, e.g. from
    DriveInfo.all_letters) whenever other slots might still be flashing
    concurrently - see pyimager.restore_disk()'s docstring for why."""
    return pyimager.randomize_disk_signature(disk_number, volumes=volumes)


class PyImagerWorker:
    """Image a physical disk to a file using the built-in Python imager.

    Callbacks fire on the Tk thread via root.after(), matching CloneWorker.

        on_progress(pct: int)          0-100
        on_log(line: str)              human-readable progress/status text
        on_done(status: CloneStatus)
    """

    def __init__(
        self,
        root,
        disk_number: int,
        image_path: str,
        on_progress: Callable[[int], None],
        on_log: Callable[[str], None],
        on_done: Callable[[CloneStatus], None],
        partition: int | None = None,
        sha1: bool = True,
        gzip_level: int | None = None,
    ):
        self._root = root
        self._disk = disk_number
        self._image = image_path
        self._partition = partition
        self._sha1 = sha1
        self._gzip_level = gzip_level
        self._on_progress = on_progress
        self._on_log = on_log
        self._on_done = on_done

        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._last_pct = -1
        self.status = CloneStatus.IDLE
        self.result: dict | None = None

    # ── public ───────────────────────────────────────────────────────────────

    def start(self):
        if self.status == CloneStatus.RUNNING:
            return
        self.status = CloneStatus.RUNNING
        self._cancel.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        """Ask the copy to stop; it aborts at the next chunk boundary."""
        self._cancel.set()

    # ── thread body ──────────────────────────────────────────────────────────

    def _run(self):
        try:
            meta = pyimager.image_disk(
                self._disk,
                self._image,
                partition=self._partition,
                sha1=self._sha1,
                gzip_level=self._gzip_level,
                force=True,  # dialog already confirmed any overwrite
                on_progress=self._progress,
                on_log=self._fire_log,
                should_cancel=self._cancel.is_set,
            )
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI
            self._fire_log(f"pyimager failed: {type(exc).__name__}: {exc}")
            self._finish(CloneStatus.FAILED)
            return

        self.result = meta
        if meta.get("cancelled"):
            self._fire_log("Stopped by user - partial image left on disk.")
            self._finish(CloneStatus.STOPPED)
            return

        if meta["bytes_written"] != meta["region_length"]:
            self._fire_log(
                f"Short read: {meta['bytes_written']} of "
                f"{meta['region_length']} bytes")
            self._finish(CloneStatus.FAILED)
            return

        if meta["bad_sector_count"]:
            # Not fatal - the image is complete and correctly aligned, but the
            # bad sectors were zero-filled, so say so loudly.
            self._fire_log(
                f"WARNING: {meta['bad_sector_count']} unreadable sector(s) "
                f"were zero-filled")

        digest = meta["digests"].get("sha256", "")
        extra = ""
        if meta["format"] == "raw.gz" and meta["bytes_written"]:
            pct = 100.0 * meta["stored_bytes"] / meta["bytes_written"]
            extra = f", gzip {pct:.0f}% of raw"
        self._fire_log(
            f"Imaged {meta['bytes_written']} bytes in {meta['duration_s']}s"
            f"{extra}  sha256 {digest[:16]}…")
        self._finish(CloneStatus.DONE)

    # ── callbacks (marshalled onto the Tk thread) ────────────────────────────

    def _progress(self, done: int, total: int):
        pct = int(done * 100 / total) if total else 0
        if pct != self._last_pct:
            self._last_pct = pct
            self._call(self._on_progress, pct)

    def _fire_log(self, line: str):
        self._call(self._on_log, line)

    def _finish(self, status: CloneStatus):
        self.status = status
        self._call(self._on_done, status)

    def _call(self, fn, *args):
        try:
            self._root.after(0, fn, *args)
        except Exception:
            pass  # window torn down mid-run


class PyImagerRestoreWorker:
    """Flash a disk image to a physical disk using the built-in Python imager.

    Mirrors CloneWorker's callback interface for the restore/flash direction
    (not the capture direction PyImagerWorker above handles), so app.py can
    swap engines per slot by choosing which class to construct.

        on_progress(pct: int)          0-100
        on_log(line: str)              human-readable progress/status text
        on_done(status: CloneStatus)

    `image_path` ending in .gz is decompressed on the fly - see
    pyimager.restore_disk(). `confirm` is the disk number itself: the
    caller already gated this on a validated, removable drive slot (the
    same point CloneWorker's ODIN restore path is gated), so there is no
    separate confirmation step here.

    Pass `volumes` (e.g. DriveInfo.all_letters, bare letters without the
    colon) whenever the caller already knows which drive letters live on
    this disk - restore_disk() then never falls back to scanning all 26
    letters, which is what let one slot's flash transiently interfere with
    a sibling slot's lock/write when multiple slots ran concurrently
    (confirmed on real hardware: "could not lock <letter>:" on one slot,
    "Access is denied" mid-write on another, both from the same run).
    """

    def __init__(
        self,
        root,
        disk_number: int,
        image_path: str,
        on_progress: Callable[[int], None],
        on_log: Callable[[str], None],
        on_done: Callable[[CloneStatus], None],
        allow_fixed: bool = False,
        volumes=None,
    ):
        self._root = root
        self._disk = disk_number
        self._image = image_path
        self._allow_fixed = allow_fixed
        self._volumes = volumes
        self._on_progress = on_progress
        self._on_log = on_log
        self._on_done = on_done

        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._last_pct = -1
        self.status = CloneStatus.IDLE
        self.result: dict | None = None

    # ── public ───────────────────────────────────────────────────────────────

    def start(self):
        if self.status == CloneStatus.RUNNING:
            return
        self.status = CloneStatus.RUNNING
        self._cancel.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        """Ask the write to stop; it aborts at the next chunk boundary."""
        self._cancel.set()

    # ── thread body ──────────────────────────────────────────────────────────

    def _run(self):
        try:
            meta = pyimager.restore_disk(
                self._disk,
                self._image,
                confirm=self._disk,
                allow_fixed=self._allow_fixed,
                on_progress=self._progress,
                on_log=self._fire_log,
                should_cancel=self._cancel.is_set,
                volumes=self._volumes,
            )
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI
            self._fire_log(f"pyimager restore failed: {type(exc).__name__}: {exc}")
            self._finish(CloneStatus.FAILED)
            return

        self.result = meta
        if meta.get("cancelled"):
            self._fire_log("Stopped by user - partial write left on disk.")
            self._finish(CloneStatus.STOPPED)
            return

        digest = meta["digests"].get("sha256", "")
        self._fire_log(
            f"Flashed {meta['bytes_written']} bytes in {meta['duration_s']}s "
            f"sha256 {digest[:16]}…")
        self._finish(CloneStatus.DONE)

    # ── callbacks (marshalled onto the Tk thread) ────────────────────────────

    def _progress(self, done: int, total: int):
        pct = int(done * 100 / total) if total else 0
        if pct != self._last_pct:
            self._last_pct = pct
            self._call(self._on_progress, pct)

    def _fire_log(self, line: str):
        self._call(self._on_log, line)

    def _finish(self, status: CloneStatus):
        self.status = status
        self._call(self._on_done, status)

    def _call(self, fn, *args):
        try:
            self._root.after(0, fn, *args)
        except Exception:
            pass  # window torn down mid-run
