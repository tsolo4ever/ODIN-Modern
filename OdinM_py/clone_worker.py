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
from collections.abc import Callable

from partition_reader import get_image_compression_flag


class CloneStatus(Enum):
    IDLE = auto()
    QUEUED = auto()
    RUNNING = auto()
    DONE = auto()
    FAILED = auto()
    STOPPED = auto()


# ODINC exit codes
_EC_OK = 0
_EC_ERR = 1
_EC_CRASH = -1

# ODIN image file magic (GUID in Windows mixed-endian byte order)
_ODIN_MAGIC = bytes(
    [
        0x73,
        0x7B,
        0x4D,
        0x1D,
        0x01,
        0xFA,
        0xE1,
        0x40,
        0xB0,
        0x94,
        0x52,
        0x67,
        0xD8,
        0xFA,
        0x0B,
        0xE7,
    ]
)
# PartitionInfoMgr sidecar file magic (CPartitionInfoMgr::sMagicFileHeaderGUID)
_ODIN_MAGIC_PART = bytes(
    [
        0x26,
        0xFF,
        0xA6,
        0x45,
        0x51,
        0x9C,
        0xA4,
        0x43,
        0xB4,
        0x7E,
        0xA8,
        0x5D,
        0x09,
        0x3D,
        0xB4,
        0x87,
    ]
)


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
        extra_flags: list | None = None,
    ):
        self._root = root
        self._odinc = odinc_path
        self._image = image_path
        self._drive = drive_letter
        self._mode = mode  # "restore" or "backup"
        self._extra_flags = extra_flags  # None → defaults applied in _run()
        self._on_progress = on_progress
        self._on_log = on_log
        self._on_done = on_done

        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
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
        temp_img = None
        if self._mode == "backup":
            flags = (
                self._extra_flags
                if self._extra_flags is not None
                else ["-allBlocks", "-compression=none"]
            )
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
            source_path = self._image
            if self._image.lower().endswith(".gz"):
                if self._gz_content_is_odin():
                    # ODIN image inside gz — decompress to temp, then ODINC
                    temp_img = self._decompress_gz(self._image)
                    if temp_img is None:
                        self._fire_done(CloneStatus.FAILED)
                        return
                    source_path = temp_img
                else:
                    # Raw disk image inside gz — stream-flash directly
                    self._run_raw_flash(self._image, gz=True)
                    return
            elif not self._is_odin_image(self._image):
                # Raw disk image (non-gz, non-ODIN) — flash directly
                self._run_raw_flash(self._image, gz=False)
                return
            compression = get_image_compression_flag(source_path)
            cmd = [
                self._odinc,
                "-restore",
                f"-source={source_path}",
                f"-target={self._drive}",
                "-force",
                f"-compression={compression}",
            ]
        try:
            self._fire_log("CMD: " + " ".join(cmd))
            env = os.environ.copy()
            env["ODINM_PROGRESS_PIPE"] = self._pipe_name

            # Create the pipe server BEFORE launching ODINC.  ODINC calls
            # CreateFileW(OPEN_EXISTING) very early in ProcessCommandLine() — if the
            # server doesn't exist yet the connection fails silently and no progress
            # is ever sent (percentages no longer go to stdout as of the pipe commit).
            pipe = self._create_pipe_server()

            try:
                self._proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.DEVNULL,  # prevent wcin.getline from blocking
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,  # merge stderr into stdout
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    env=env,
                )
            except FileNotFoundError:
                if pipe is not None:
                    ctypes.windll.kernel32.CloseHandle(pipe)
                self._fire_log(f"ERROR: ODINC.exe not found at {self._odinc}")
                self._fire_done(CloneStatus.FAILED)
                return
            except Exception as exc:
                if pipe is not None:
                    ctypes.windll.kernel32.CloseHandle(pipe)
                self._fire_log(f"ERROR launching ODINC: {exc}")
                self._fire_done(CloneStatus.FAILED)
                return

            self._fire_log(f"Progress pipe: {self._pipe_name}")
            io_thread = threading.Thread(target=self._poll_io_progress, args=(pipe,), daemon=True)
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
        finally:
            if temp_img is not None:
                try:
                    os.remove(temp_img)
                    self._fire_log(f"Removed temp file: {os.path.basename(temp_img)}")
                except OSError:
                    pass

    def _is_odin_image(self, path: str) -> bool:
        try:
            with open(path, "rb") as f:
                magic = f.read(16)
            return magic in (_ODIN_MAGIC, _ODIN_MAGIC_PART)
        except OSError:
            return False

    def _gz_content_is_odin(self) -> bool:
        import gzip

        try:
            with gzip.open(self._image, "rb") as f:
                magic = f.read(16)
            return magic in (_ODIN_MAGIC, _ODIN_MAGIC_PART)
        except Exception:
            return False

    def _get_physical_drive_path(self) -> str:
        m = re.search(r"Harddisk(\d+)", self._drive, re.IGNORECASE)
        if m:
            return f"\\\\.\\PhysicalDrive{m.group(1)}"
        return self._drive

    def _run_raw_flash(self, path: str, gz: bool) -> None:
        """Write raw disk image to the physical drive.
        gz=True: stream-decompress path on the fly (no temp file).
        gz=False: read path directly."""
        import gzip

        phys = self._get_physical_drive_path()
        self._fire_log(f"Raw flash: {os.path.basename(path)} -> {phys}")
        k32 = ctypes.windll.kernel32
        GENERIC_WRITE = 0x40000000
        FILE_SHARE_RW = 0x00000001 | 0x00000002
        OPEN_EXISTING = 3
        INVALID_HANDLE = ctypes.c_void_p(-1).value
        CHUNK = 1 * 1024 * 1024  # 1 MB — always sector-aligned

        handle = k32.CreateFileW(phys, GENERIC_WRITE, FILE_SHARE_RW, None, OPEN_EXISTING, 0, None)
        if handle == INVALID_HANDLE:
            self._fire_log(f"ERROR: Cannot open {phys} for writing (err={k32.GetLastError()})")
            self._fire_done(CloneStatus.FAILED)
            return

        gz_size = os.path.getsize(path) if gz else 0
        raw_size = 0 if gz else os.path.getsize(path)
        bytes_done = 0
        success = False
        try:
            raw_f = open(path, "rb")
            f_in = gzip.GzipFile(fileobj=raw_f) if gz else raw_f
            try:
                while True:
                    if self.status == CloneStatus.STOPPED:
                        break
                    chunk = f_in.read(CHUNK)
                    if not chunk:
                        success = True
                        break
                    rem = len(chunk) % 512
                    if rem:
                        chunk += b"\x00" * (512 - rem)
                    written = ctypes.c_ulong(0)
                    ok = k32.WriteFile(handle, chunk, len(chunk), ctypes.byref(written), None)
                    if not ok or written.value != len(chunk):
                        self._fire_log(
                            f"ERROR: Write failed at"
                            f" {bytes_done // (1024 * 1024)} MB"
                            f" (err={k32.GetLastError()})"
                        )
                        break
                    bytes_done += written.value
                    if gz and gz_size:
                        pct = min(int(raw_f.tell() * 99 // gz_size), 99)
                        self._fire_progress(pct)
                    elif raw_size:
                        pct = min(int(bytes_done * 99 // raw_size), 99)
                        self._fire_progress(pct)
                    prev = bytes_done - written.value
                    if bytes_done // (64 * 1024 * 1024) > prev // (64 * 1024 * 1024):
                        self._fire_log(f"  Written {bytes_done // (1024 * 1024)} MB")
            finally:
                f_in.close()
                if gz:
                    raw_f.close()
        finally:
            k32.CloseHandle(handle)

        if self.status == CloneStatus.STOPPED:
            self._fire_done(CloneStatus.STOPPED)
        elif success:
            self._fire_log(f"Raw flash done: {bytes_done // (1024 * 1024)} MB written")
            self._fire_progress(100)
            self._fire_done(CloneStatus.DONE)
        else:
            self._fire_done(CloneStatus.FAILED)

    def _decompress_gz(self, gz_path: str) -> str | None:
        """Decompress a .gz image to a .img file in the same directory.
        Returns the output path on success, or None on failure."""
        import gzip

        out_path = gz_path[:-3]  # strip .gz
        self._fire_log(
            f"Decompressing {os.path.basename(gz_path)} -> {os.path.basename(out_path)} ..."
        )
        try:
            chunk_size = 4 * 1024 * 1024  # 4 MB
            bytes_done = 0
            with gzip.open(gz_path, "rb") as gz_in, open(out_path, "wb") as img_out:
                while True:
                    if self.status == CloneStatus.STOPPED:
                        break
                    chunk = gz_in.read(chunk_size)
                    if not chunk:
                        break
                    img_out.write(chunk)
                    bytes_done += len(chunk)
                    if (bytes_done // chunk_size) % 256 == 0:
                        self._fire_log(f"  Decompressed {bytes_done // (1024 * 1024)} MB")
            if self.status == CloneStatus.STOPPED:
                try:
                    os.remove(out_path)
                except OSError:
                    pass
                return None
            self._fire_log(f"Decompression done: {bytes_done // (1024 * 1024)} MB")
            return out_path
        except Exception as exc:
            self._fire_log(f"ERROR decompressing: {exc}")
            try:
                os.remove(out_path)
            except OSError:
                pass
            return None

    def _create_pipe_server(self):
        """Create the named pipe server. Returns the pipe handle, or None on failure."""
        PIPE_ACCESS_INBOUND = 0x00000001
        PIPE_TYPE_BYTE = 0x00000000
        PIPE_READMODE_BYTE = 0x00000000
        PIPE_WAIT = 0x00000000
        FILE_FLAG_OVERLAPPED = 0x40000000
        INVALID_HANDLE = ctypes.c_void_p(-1).value
        k32 = ctypes.windll.kernel32

        pipe = k32.CreateNamedPipeW(
            self._pipe_name,
            PIPE_ACCESS_INBOUND | FILE_FLAG_OVERLAPPED,
            PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT,
            1,
            4096,
            4096,
            0,  # NMPWAIT_USE_DEFAULT_WAIT
            None,
        )
        if pipe == INVALID_HANDLE:
            self._fire_log("Progress pipe: CreateNamedPipeW failed")
            return None
        return pipe

    def _poll_io_progress(self, pipe):
        """
        Named pipe server — odin.exe connects as a client and writes
        progress percentage lines ("45\\n") from its ReportFeedback timer.
        Pipe handle is created before Popen so ODINC's CreateFileW(OPEN_EXISTING)
        finds the server immediately.
        """
        if pipe is None:
            return

        k32 = ctypes.windll.kernel32

        class OVERLAPPED(ctypes.Structure):
            _fields_ = [
                ("Internal", ctypes.c_size_t),
                ("InternalHigh", ctypes.c_size_t),
                ("Offset", ctypes.c_ulong),
                ("OffsetHigh", ctypes.c_ulong),
                ("hEvent", ctypes.c_void_p),
            ]

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
                leftover += buf.raw[: n.value].decode("ascii", errors="ignore")
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
        proc_stdout = self._proc.stdout

        try:
            for raw in iter(lambda: proc_stdout.read(1), b""):
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
                    buf = buf[m.end() :]  # consume up to and including the match
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
