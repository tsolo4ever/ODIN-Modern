"""
app.py
OdinMApp — wires config, drive monitor, clone workers, and UI together.
"""

import os
import time
from typing import Dict, List, Optional

import ttkbootstrap as ttk
from ttkbootstrap.constants import *

from clone_worker import CloneStatus, CloneWorker
from config_manager import ConfigManager
from drive_manager import DriveInfo, DriveMonitor, is_removable
from ui.main_window import MainWindow, NUM_SLOTS

APP_TITLE   = "OdinM — Multi-Drive Clone Tool (Python)"


def _fmt_speed(bps: float) -> str:
    if bps >= 1 << 30:
        return f"{bps / (1 << 30):.1f} GB/s"
    if bps >= 1 << 20:
        return f"{bps / (1 << 20):.1f} MB/s"
    if bps >= 1 << 10:
        return f"{bps / (1 << 10):.0f} KB/s"
    return f"{bps:.0f} B/s"
MIN_WIDTH   = 780
MIN_HEIGHT  = 560


class OdinMApp:
    def __init__(self, config: ConfigManager):
        self._config = config

        self._root = ttk.Window(
            title=APP_TITLE,
            themename=config.get_theme(),
            minsize=(MIN_WIDTH, MIN_HEIGHT),
        )
        self._root.geometry(f"{MIN_WIDTH}x{MIN_HEIGHT}")

        _icon = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "src", "ODINM", "res", "OdinM.ico",
        )
        if os.path.isfile(_icon):
            self._root.iconbitmap(_icon)

        self._window = MainWindow(self._root, config)
        self._wire_callbacks()

        # Drive slots: letter → slot index mapping
        self._drives: List[Optional[DriveInfo]] = [None] * NUM_SLOTS
        self._workers: Dict[int, CloneWorker] = {}
        self._queue: List[int] = []          # slot indices waiting to start
        self._start_times: Dict[int, float] = {}  # slot → clone start timestamp

        self._monitor = DriveMonitor(self._root, self._on_drives_changed)

    def run(self):
        self._monitor.start()
        self._root.mainloop()

    # ── callback wiring ───────────────────────────────────────────────────────

    def _wire_callbacks(self):
        self._window.on_start_slot      = self._start_slot
        self._window.on_stop_slot       = self._stop_slot
        self._window.on_start_all       = self._start_all
        self._window.on_stop_all        = self._stop_all
        self._window.on_verify_image    = self._verify_image
        self._window.on_configure_hashes = self._configure_hashes
        self._window.on_verify_stored   = self._verify_stored
        self._window.on_make_image      = self._make_image

    # ── drive monitor callback ────────────────────────────────────────────────

    def _on_drives_changed(self, drives: List[DriveInfo]):
        # Snapshot previous disk numbers so we can detect newly inserted drives
        prev_disk_nums = {d.disk_number for d in self._drives if d is not None}

        # Rebuild slot→drive mapping
        self._drives = [None] * NUM_SLOTS
        for i, d in enumerate(drives[:NUM_SLOTS]):
            self._drives[i] = d

        # Drop queued slots whose drives were removed
        self._queue = [i for i in self._queue if self._drives[i] is not None]

        self._window.update_drives(drives[:NUM_SLOTS])
        self._window.log(f"[Drives] {len(drives)} removable drive(s) detected")

        # Auto-clone newly inserted drives if the setting is enabled
        if self._config.get_auto_clone():
            image = self._window.image_path
            if not image or not os.path.isfile(image):
                return
            for i, drive in enumerate(self._drives):
                if drive is None:
                    continue
                if drive.disk_number in prev_disk_nums:
                    continue  # not new
                w = self._workers.get(i)
                if w is None or w.status != CloneStatus.RUNNING:
                    self._window.log(
                        f"[Auto] New drive in slot {i+1} — starting clone")
                    self._start_slot(i)

    # ── start / stop ──────────────────────────────────────────────────────────

    def _start_slot(self, idx: int):
        image = self._window.image_path
        if not image:
            self._window.log("[Error] No image file selected.")
            return
        if not os.path.isfile(image):
            self._window.log(f"[Error] Image not found: {image}")
            return
        drive = self._drives[idx]
        if drive is None:
            self._window.log(f"[Error] Slot {idx + 1} has no drive.")
            return
        if not is_removable(drive.first_letter):
            self._window.log(
                f"[Error] Slot {idx + 1} ({drive.first_letter}) is not a removable drive — aborted.")
            return

        max_conc = self._config.get_max_concurrent()
        if self._running_count() >= max_conc:
            if idx not in self._queue:
                self._queue.append(idx)
                self._window.set_slot_status(idx, CloneStatus.QUEUED)
                self._window.log(
                    f"[Slot {idx+1}] Queued — waiting for a free slot "
                    f"(max {max_conc} concurrent)")
            return

        self._launch(idx)

    def _launch(self, idx: int):
        drive = self._drives[idx]
        image = self._window.image_path
        odinc = self._config.get_odinc_path()
        size_bytes = drive.size_bytes
        self._start_times[idx] = time.time()

        def _on_progress(pct: int, i: int = idx, sz: int = size_bytes):
            self._window.set_slot_progress(i, pct)
            if pct > 0 and sz > 0:
                elapsed = time.time() - self._start_times.get(i, time.time())
                if elapsed > 0:
                    self._window.set_slot_speed(i, _fmt_speed(pct / 100.0 * sz / elapsed))

        worker = CloneWorker(
            root=self._root,
            odinc_path=odinc,
            image_path=image,
            drive_letter=drive.target_path,   # \Device\HarddiskN\Partition0 — whole disk
            on_progress=_on_progress,
            on_log=lambda line, i=idx: self._window.log(f"[Slot {i+1}] {line}"),
            on_done=lambda status, i=idx: self._on_worker_done(i, status),
        )
        self._workers[idx] = worker
        self._window.set_slot_status(idx, CloneStatus.RUNNING)
        self._window.log(f"[Slot {idx+1}] Starting clone → {drive.target_path}  ({drive.display})")
        worker.start()

    def _running_count(self) -> int:
        return sum(1 for w in self._workers.values() if w.status == CloneStatus.RUNNING)

    def _drain_queue(self):
        max_conc = self._config.get_max_concurrent()
        while self._queue and self._running_count() < max_conc:
            next_idx = self._queue.pop(0)
            if self._drives[next_idx] is None:
                continue  # drive removed while queued
            self._window.log(f"[Slot {next_idx+1}] Starting from queue")
            self._launch(next_idx)

    def _stop_slot(self, idx: int):
        if idx in self._queue:
            self._queue.remove(idx)
            self._window.log(f"[Slot {idx+1}] Removed from queue.")
            drive = self._drives[idx]
            if drive:
                self._window.set_slot_ready(idx, drive.display)
            return
        worker = self._workers.get(idx)
        if worker:
            worker.stop()
            self._window.log(f"[Slot {idx+1}] Stop requested.")

    def _start_all(self):
        for i in range(NUM_SLOTS):
            if self._drives[i] is not None:
                w = self._workers.get(i)
                already_active = (
                    i in self._queue or
                    (w is not None and w.status == CloneStatus.RUNNING)
                )
                if not already_active:
                    self._start_slot(i)

    def _stop_all(self):
        for i, worker in self._workers.items():
            if worker.status == CloneStatus.RUNNING:
                self._stop_slot(i)

    def _verify_image(self):
        image = self._window.image_path
        if not image:
            self._window.log("[Error] No image file selected.")
            return
        if not os.path.isfile(image):
            self._window.log(f"[Error] Image not found: {image}")
            return
        from ui.hash_dialog import HashDialog
        self._window.log(f"[Hash] Computing hash for {os.path.basename(image)}")
        HashDialog(self._root, image)

    def _configure_hashes(self):
        image = self._window.image_path
        if not image:
            self._window.log("[Error] No image file selected.")
            return
        from ui.configure_hash_dialog import ConfigureHashDialog
        ConfigureHashDialog(self._root, image)

    def _verify_stored(self):
        image = self._window.image_path
        if not image:
            self._window.log("[Error] No image file selected.")
            return
        from ui.hash_dialog import StoredHashDialog
        self._window.log(f"[Verify] Checking stored hash for {os.path.basename(image)}")
        StoredHashDialog(self._root, image)

    def _make_image(self):
        from ui.make_image_dialog import MakeImageDialog
        MakeImageDialog(self._root, self._config.get_odinc_path())

    def _on_worker_done(self, idx: int, status: CloneStatus):
        self._window.set_slot_status(idx, status)
        label = {
            CloneStatus.DONE:    "Complete",
            CloneStatus.FAILED:  "FAILED",
            CloneStatus.STOPPED: "Stopped by user",
        }.get(status, str(status))
        self._window.log(f"[Slot {idx+1}] {label}")
        if status == CloneStatus.DONE:
            self._window.set_slot_progress(idx, 100)
        self._window.set_slot_speed(idx, "")
        self._drain_queue()
