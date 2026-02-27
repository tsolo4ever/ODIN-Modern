"""
hash_worker.py
Computes SHA-256 and SHA-1 of a file on a daemon thread.
Reports chunk-by-chunk progress and final digests via root.after() callbacks.
"""

import hashlib
import os
import threading
from enum import Enum, auto
from typing import Callable, Optional

CHUNK_SIZE = 4 << 20  # 4 MB — matches typical disk read granularity


class HashStatus(Enum):
    IDLE    = auto()
    RUNNING = auto()
    DONE    = auto()
    FAILED  = auto()
    STOPPED = auto()


class HashWorker:
    """
    Computes SHA-256 and SHA-1 of a file.

    Callbacks (all called on the tkinter main thread via root.after):
      on_progress(pct: int)                        0–100
      on_done(status, sha256: str, sha1: str)      hex strings, or "" on failure
    """

    def __init__(
        self,
        root,
        file_path: str,
        on_progress: Callable[[int], None],
        on_done: Callable,  # (HashStatus, sha256: str, sha1: str)
        offset: int = 0,
        byte_count: int = -1,   # -1 = hash to end of file
    ):
        self._root        = root
        self._path        = file_path
        self._on_progress = on_progress
        self._on_done     = on_done
        self._offset      = offset
        self._byte_count  = byte_count
        self._thread: Optional[threading.Thread] = None
        self.status = HashStatus.IDLE

    def start(self):
        if self.status == HashStatus.RUNNING:
            return
        self.status = HashStatus.RUNNING
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self.status = HashStatus.STOPPED

    # ── private ──────────────────────────────────────────────────────────────

    def _run(self):
        try:
            file_size = os.path.getsize(self._path)
        except OSError:
            self._fire_done(HashStatus.FAILED, "", "")
            return

        offset = self._offset
        total  = self._byte_count if self._byte_count >= 0 else file_size - offset
        total  = max(0, min(total, file_size - offset))

        sha256 = hashlib.sha256()
        sha1   = hashlib.sha1()
        done   = 0
        last_pct = -1

        try:
            with open(self._path, "rb") as f:
                f.seek(offset)
                remaining = total
                while remaining > 0:
                    if self.status == HashStatus.STOPPED:
                        self._fire_done(HashStatus.STOPPED, "", "")
                        return
                    chunk = f.read(min(CHUNK_SIZE, remaining))
                    if not chunk:
                        break
                    sha256.update(chunk)
                    sha1.update(chunk)
                    done      += len(chunk)
                    remaining -= len(chunk)
                    if total > 0:
                        pct = min(int(done * 100 / total), 99)
                        if pct != last_pct:
                            self._fire_progress(pct)
                            last_pct = pct
        except OSError:
            self._fire_done(HashStatus.FAILED, "", "")
            return

        self._fire_progress(100)
        self._fire_done(HashStatus.DONE, sha256.hexdigest(), sha1.hexdigest())

    def _fire_progress(self, pct: int):
        self._root.after(0, self._on_progress, pct)

    def _fire_done(self, status: HashStatus, sha256: str, sha1: str):
        self.status = status
        self._root.after(0, self._on_done, status, sha256, sha1)
