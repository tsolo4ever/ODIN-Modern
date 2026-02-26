"""
clone_worker.py
Wraps a single ODINC.exe process. Parses stdout for progress dots/percentages.
Runs the reader loop on a daemon thread; delivers callbacks on the tkinter thread
via root.after(0, ...).
"""

import re
import subprocess
import threading
from enum import Enum, auto
from typing import Callable, Optional


class CloneStatus(Enum):
    IDLE      = auto()
    RUNNING   = auto()
    DONE      = auto()
    FAILED    = auto()
    STOPPED   = auto()


# ODINC exit codes
_EC_OK    =  0
_EC_ERR   =  1
_EC_CRASH = -1


class CloneWorker:
    """
    One worker per drive slot.

    Callbacks (all called on the tkinter main thread via root.after):
      on_progress(pct: int)          0-100
      on_log(line: str)              raw text from ODINC stdout/stderr
      on_done(status: CloneStatus)   DONE or FAILED or STOPPED
    """

    def __init__(
        self,
        root,
        odinc_path: str,
        image_path: str,
        drive_letter: str,
        on_progress: Callable[[int], None],
        on_log: Callable[[str], None],
        on_done: Callable[[CloneStatus], None],
        mode: str = "restore",
        extra_flags: Optional[list] = None,
    ):
        self._root       = root
        self._odinc      = odinc_path
        self._image      = image_path
        self._drive      = drive_letter
        self._mode       = mode          # "restore" or "backup"
        self._extra_flags = extra_flags  # None → defaults applied in _run()
        self._on_progress = on_progress
        self._on_log      = on_log
        self._on_done     = on_done

        self._proc: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self.status = CloneStatus.IDLE

    # ── public ───────────────────────────────────────────────────────────────

    def start(self):
        if self.status == CloneStatus.RUNNING:
            return
        self.status = CloneStatus.RUNNING
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self.status = CloneStatus.STOPPED
        if self._proc and self._proc.poll() is None:
            # Kill the whole process tree — ODINC.exe spawns odin.exe as a
            # child and waits on it; terminate() only kills the parent.
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(self._proc.pid)],
                capture_output=True,
            )

    # ── private ──────────────────────────────────────────────────────────────

    def _run(self):
        if self._mode == "backup":
            # Remove existing output file so ODINC doesn't prompt "overwrite? (y/n)"
            # (its stdin is DEVNULL, so the prompt blocks forever)
            try:
                import os as _os
                if _os.path.exists(self._image):
                    _os.remove(self._image)
                    self._fire_log(f"Removed existing file: {self._image}")
            except OSError as e:
                self._fire_log(f"WARNING: could not remove existing file: {e}")
            flags = self._extra_flags if self._extra_flags is not None \
                else ["-allBlocks", "-compression=none"]
            cmd = [
                self._odinc,
                "-backup",
                *flags,
                f"-source={self._drive}",
                f"-target={self._image}",
            ]
        else:
            cmd = [
                self._odinc,
                "-restore",
                f"-source={self._image}",
                f"-target={self._drive}",
                "-force",
            ]
        self._fire_log("CMD: " + " ".join(cmd))
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,   # prevent wcin.getline from blocking
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,   # merge stderr into stdout
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except FileNotFoundError:
            self._fire_log(f"ERROR: ODINC.exe not found at {self._odinc}")
            self._fire_done(CloneStatus.FAILED)
            return
        except Exception as exc:
            self._fire_log(f"ERROR launching ODINC: {exc}")
            self._fire_done(CloneStatus.FAILED)
            return

        self._read_output()

        ret = self._proc.wait()
        if self.status == CloneStatus.STOPPED:
            self._fire_done(CloneStatus.STOPPED)
        elif ret == _EC_OK:
            self._fire_progress(100)
            self._fire_done(CloneStatus.DONE)
        else:
            self._fire_log(f"ODINC exited with code {ret}")
            self._fire_done(CloneStatus.FAILED)

    def _read_output(self):
        """
        ODINC emits progress as dots + percentages, e.g.:
            Restoring E: from file: image.img
            ......10%......20%......
        We read byte-by-byte so we catch "XX%" as soon as it arrives,
        and buffer complete lines for the log.
        """
        buf = ""
        line_buf = ""
        assert self._proc is not None
        assert self._proc.stdout is not None

        for raw in iter(lambda: self._proc.stdout.read(1), b""):
            if self.status == CloneStatus.STOPPED:
                break
            ch = raw.decode("utf-8", errors="replace")
            buf += ch
            line_buf += ch

            # Flush log line on newline
            if ch == "\n":
                self._fire_log(line_buf.rstrip())
                line_buf = ""

            # Parse progress percentage
            m = re.search(r"(\d{1,3})%", buf)
            if m:
                pct = min(int(m.group(1)), 100)
                self._fire_progress(pct)
                buf = buf[m.end():]  # consume up to and including the match

    # ── thread-safe callbacks via root.after ─────────────────────────────────

    def _fire_progress(self, pct: int):
        self._root.after(0, self._on_progress, pct)

    def _fire_log(self, line: str):
        self._root.after(0, self._on_log, line)

    def _fire_done(self, status: CloneStatus):
        self.status = status
        self._root.after(0, self._on_done, status)
