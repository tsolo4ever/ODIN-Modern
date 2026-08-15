"""Nonblocking readiness wait for a physical disk partition table."""

import threading
import time
from collections.abc import Callable

from partition_reader import PartitionReadError, read_mbr_partitions_strict


class PartitionTableWaiter:
    def __init__(
        self,
        root,
        disk_path: str,
        on_ready: Callable,
        on_failed: Callable[[str], None],
        timeout_s: float = 10.0,
        interval_s: float = 0.5,
    ):
        self._root = root
        self._path = disk_path
        self._on_ready = on_ready
        self._on_failed = on_failed
        self._timeout = timeout_s
        self._interval = interval_s
        self._stopped = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self) -> None:
        self._stopped.set()

    def _run(self) -> None:
        deadline = time.monotonic() + self._timeout
        last_error = "Partition table was not ready."
        while not self._stopped.is_set():
            try:
                partitions = read_mbr_partitions_strict(self._path)
                self._root.after(0, self._on_ready, partitions)
                return
            except PartitionReadError as exc:
                last_error = str(exc)
            if time.monotonic() >= deadline:
                self._root.after(0, self._on_failed, last_error)
                return
            self._stopped.wait(self._interval)
