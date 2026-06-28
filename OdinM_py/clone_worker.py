"""
clone_worker.py
Wraps a single ODINC.exe process. Parses stdout for progress dots/percentages.
Runs the reader loop on a daemon thread; delivers callbacks on the tkinter thread
via root.after(0, ...).
"""

import ctypes
import ctypes.wintypes
import os
import re
import subprocess
import threading
from enum import Enum, auto
from typing import Callable, Optional


class CloneStatus(Enum):
    IDLE      = auto()
    QUEUED    = auto()
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
        self._saw_error = False  # set to True if ODINC output contains "Error:"
        self._pipe_name = f"\\\\.\\pipe\\odinm_{id(self)}"

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
            flags = self._extra_flags if self._extra_flags is not None \
                else ["-allBlocks", "-compression=none"]
            flags = list(flags)
            if "-force" not in flags:
                flags.append("-force")
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
        env = os.environ.copy()
        env["ODINM_PROGRESS_PIPE"] = self._pipe_name
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,   # prevent wcin.getline from blocking
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,   # merge stderr into stdout
                creationflags=subprocess.CREATE_NO_WINDOW,
                env=env,
            )
        except FileNotFoundError:
            self._fire_log(f"ERROR: ODINC.exe not found at {self._odinc}")
            self._fire_done(CloneStatus.FAILED)
            return
        except Exception as exc:
            self._fire_log(f"ERROR launching ODINC: {exc}")
            self._fire_done(CloneStatus.FAILED)
            return

        self._fire_log(f"Progress pipe: {self._pipe_name}")
        io_thread = threading.Thread(target=self._poll_io_progress, daemon=True)
        io_thread.start()

        self._read_output()

        ret = self._proc.wait()
        if self.status == CloneStatus.STOPPED:
            self._fire_done(CloneStatus.STOPPED)
        elif ret == _EC_OK and not self._saw_error:
            self._fire_progress(100)
            self._fire_done(CloneStatus.DONE)
        else:
            if ret != _EC_OK:
                self._fire_log(f"ODINC exited with code {ret}")
            else:
                self._fire_log("ODINC reported an error despite exit code 0")
            self._fire_done(CloneStatus.FAILED)

    def _poll_io_progress(self):
        """
        Named pipe server — odin.exe connects as a client and writes
        progress percentage lines ("45\\n") from its ReportFeedback timer.
        Completely bypasses stdout buffering.
        """
        PIPE_ACCESS_INBOUND    = 0x00000001
        PIPE_TYPE_BYTE         = 0x00000000
        PIPE_READMODE_BYTE     = 0x00000000
        PIPE_WAIT              = 0x00000000
        NMPWAIT_USE_DEFAULT_WAIT = 0
        INVALID_HANDLE         = ctypes.c_void_p(-1).value
        k32 = ctypes.windll.kernel32

        FILE_FLAG_OVERLAPPED = 0x40000000
        pipe = k32.CreateNamedPipeW(
            self._pipe_name,
            PIPE_ACCESS_INBOUND | FILE_FLAG_OVERLAPPED,
            PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT,
            1, 4096, 4096,
            NMPWAIT_USE_DEFAULT_WAIT,
            None,
        )
        if pipe == INVALID_HANDLE:
            return

        # Create an event for overlapped ConnectNamedPipe so we can time out
        # cleanly if odin.exe exits before ever connecting to the pipe.
        class OVERLAPPED(ctypes.Structure):
            _fields_ = [("Internal",     ctypes.c_size_t),   # ULONG_PTR — 8 bytes on x64
                        ("InternalHigh", ctypes.c_size_t),
                        ("Offset",       ctypes.c_ulong),
                        ("OffsetHigh",   ctypes.c_ulong),
                        ("hEvent",       ctypes.c_void_p)]

        import time
        try:
            evt = k32.CreateEventW(None, True, False, None)
            ov = OVERLAPPED()
            ov.hEvent = evt
            k32.ConnectNamedPipe(pipe, ctypes.byref(ov))  # returns immediately (overlapped)

            connected = False
            deadline = time.time() + 30
            while time.time() < deadline:
                if self._proc is None or self._proc.poll() is not None:
                    break
                res = k32.WaitForSingleObject(evt, 200)  # 200 ms poll
                if res == 0:  # WAIT_OBJECT_0
                    connected = True
                    break
            k32.CloseHandle(evt)

            if not connected:
                self._fire_log("Progress pipe: no client connected within 30s")
                return

            self._fire_log("Progress pipe: odin.exe connected")
            buf = ctypes.create_string_buffer(256)
            leftover = ""
            while self._proc and self._proc.poll() is None:
                n = ctypes.c_ulong(0)
                ok = k32.ReadFile(pipe, buf, 255, ctypes.byref(n), None)
                if not ok or n.value == 0:
                    break
                leftover += buf.raw[:n.value].decode("ascii", errors="ignore")
                while "\n" in leftover:
                    line, leftover = leftover.split("\n", 1)
                    line = line.strip()
                    self._fire_log(f"Progress pipe rx: '{line}'")
                    if line.isdigit():
                        pct = min(int(line), 99)
                        self._fire_progress(pct)
            self._fire_log("Progress pipe: closed")
        finally:
            k32.CloseHandle(pipe)

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
        dot_count = 0
        last_dot_pct = 0
        assert self._proc is not None
        assert self._proc.stdout is not None

        try:
            for raw in iter(lambda: self._proc.stdout.read(1), b""):
                if self.status == CloneStatus.STOPPED:
                    break
                ch = raw.decode("utf-8", errors="replace")
                buf += ch
                line_buf += ch

                # Flush log line on newline
                if ch == "\n":
                    stripped = line_buf.rstrip()
                    self._fire_log(stripped)
                    # Detect error messages so we can fail the clone even if
                    # ODINC exits 0 (e.g. "Error: The available disk space...")
                    if "Error:" in stripped:
                        self._saw_error = True
                    line_buf = ""

                # Parse explicit progress percentage
                m = re.search(r"(\d{1,3})%", buf)
                if m:
                    pct = min(int(m.group(1)), 100)
                    self._fire_progress(pct)
                    buf = buf[m.end():]  # consume up to and including the match
                    dot_count = 0
                    last_dot_pct = pct
                elif ch == ".":
                    # nCompass ODINC outputs dots without percentage markers.
                    # Assume ~250 dots for a full restore and fake a progress value.
                    dot_count += 1
                    est = min(last_dot_pct + int(dot_count / 3.0), 99)
                    if est > last_dot_pct:
                        self._fire_progress(est)
                        last_dot_pct = est
        except OSError:
            pass  # pipe closed when process exits — normal on Windows

    # ── thread-safe callbacks via root.after ─────────────────────────────────

    def _fire_progress(self, pct: int):
        self._root.after(0, self._on_progress, pct)

    def _fire_log(self, line: str):
        self._root.after(0, self._on_log, line)

    def _fire_done(self, status: CloneStatus):
        self.status = status
        self._root.after(0, self._on_done, status)
