"""
app.py
OdinMApp — wires config, drive monitor, clone workers, and UI together.
"""

import os
import time
from collections import deque

import ttkbootstrap as ttk
from ttkbootstrap.constants import *

from clone_worker import CloneStatus, CloneWorker
from config_manager import ConfigManager
from drive_manager import DriveInfo, DriveMonitor, is_removable
from hash_worker import HashStatus, HashWorker
from partition_reader import get_image_hash_region
from ui.flash_status_window import FlashStatusWindow
from ui.main_window import MainWindow, NUM_SLOTS

APP_TITLE = "OdinM — Multi-Drive Clone Tool (Python)"


def _fmt_eta(seconds: float) -> str:
    s = int(seconds)
    if s >= 3600:
        return f"~{s // 3600}h {(s % 3600) // 60}m"
    if s >= 60:
        return f"~{s // 60}m {s % 60:02d}s"
    return f"~{s}s"


def _fmt_speed(bps: float) -> str:
    if bps >= 1 << 30:
        return f"{bps / (1 << 30):.1f} GB/s"
    if bps >= 1 << 20:
        return f"{bps / (1 << 20):.1f} MB/s"
    if bps >= 1 << 10:
        return f"{bps / (1 << 10):.0f} KB/s"
    return f"{bps:.0f} B/s"


MIN_WIDTH = 780
MIN_HEIGHT = 560


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
            os.path.dirname(os.path.abspath(__file__)),
            "assets",
            "OdinM.ico",
        )
        if os.path.isfile(_icon):
            self._root.iconbitmap(_icon)

        self._window = MainWindow(self._root, config)
        self._wire_callbacks()

        # Drive slots: letter → slot index mapping
        self._drives: list[DriveInfo | None] = [None] * NUM_SLOTS
        self._workers: dict[int, CloneWorker] = {}
        self._queue: list[int] = []  # slot indices waiting to start
        # slot → deque of (timestamp, pct) samples for rolling speed window
        self._speed_samples: dict[int, deque] = {}
        self._verify_workers: dict[int, HashWorker] = {}
        # disk numbers that just finished cloning — cleared when drive is removed
        # so the same physical card does not trigger a second auto-clone
        self._finished_disk_nums: set = set()
        self._flash_widget: FlashStatusWindow | None = None
        if self._config.get_show_flash_widget():
            self._show_flash_widget()

        self._monitor = DriveMonitor(self._root, self._on_drives_changed)

    def run(self):
        self._monitor.start()
        self._root.mainloop()

    # ── callback wiring ───────────────────────────────────────────────────────

    def _wire_callbacks(self):
        self._window.on_start_slot = self._start_slot
        self._window.on_stop_slot = self._stop_slot
        self._window.on_start_all = self._start_all
        self._window.on_stop_all = self._stop_all
        self._window.on_verify_image = self._verify_image
        self._window.on_configure_hashes = self._configure_hashes
        self._window.on_verify_stored = self._verify_stored
        self._window.on_make_image = self._make_image
        self._window.on_flash_widget_toggle = self._on_flash_widget_toggle

    # ── drive monitor callback ────────────────────────────────────────────────

    def _on_drives_changed(self, drives: list[DriveInfo]):
        # Apply max drive size filter — keeps oversized drives (e.g. a dev USB stick)
        # out of slots and auto-clone entirely. 0 = no limit.
        max_gb = self._config.get_max_drive_gb()
        max_bytes = max_gb * (1 << 30) if max_gb > 0 else 0
        drives = [
            d for d in drives if max_bytes == 0 or d.size_bytes == 0 or d.size_bytes <= max_bytes
        ]

        # Build a lookup of previously seen drives by disk_number.
        prev_by_disk: dict = {d.disk_number: d.hw_serial for d in self._drives if d is not None}

        current_disk_nums = {d.disk_number for d in drives}

        # When a drive is physically removed, clear it from the finished set so
        # the next insertion is treated as a new card and can auto-clone again.
        self._finished_disk_nums -= self._finished_disk_nums - current_disk_nums

        # Handle drives that were just removed.
        for i, prev in enumerate(self._drives):
            if prev is None or prev.disk_number in current_disk_nums:
                continue
            w = self._workers.get(i)
            if w is not None and w.status == CloneStatus.RUNNING:
                # Drive pulled mid-flash — stop the worker and report failed.
                self._window.log(f"[Slot {i + 1}] Drive removed during flash — aborting.")
                self._speed_samples.pop(i, None)
                w.stop()
            else:
                # Idle or finished — just clear the slot UI.
                self._window.set_slot_ready(i, "")
                self._window.set_slot_status(i, CloneStatus.IDLE)
                self._window.set_slot_progress(i, 0)
                self._flash_set_status(i, CloneStatus.IDLE)

        # Rebuild slot→drive mapping
        self._drives = [None] * NUM_SLOTS
        for i, d in enumerate(drives[:NUM_SLOTS]):
            self._drives[i] = d

        # Drop queued slots whose drives were removed
        self._queue = [i for i in self._queue if self._drives[i] is not None]

        self._window.update_drives(drives[:NUM_SLOTS])
        self._window.log(f"[Drives] {len(drives)} removable drive(s) detected")
        self._mirror_drives_to_flash()

        # Auto-clone newly inserted drives if the setting is enabled
        if self._config.get_auto_clone():
            image = self._window.image_path
            if not image or not os.path.isfile(image):
                return
            for i, drive in enumerate(self._drives):
                if drive is None:
                    continue
                prev_serial = prev_by_disk.get(drive.disk_number)
                if prev_serial is not None:
                    # Same disk index seen before.
                    # Only treat as a new physical device if BOTH serials are
                    # known (non-empty) and differ — avoids false triggers when
                    # the serial temporarily reads as "" while the device is
                    # locked by an active write (ODINC flash in progress).
                    if not (drive.hw_serial and prev_serial and drive.hw_serial != prev_serial):
                        continue
                # Don't re-clone a card that just finished — wait for it to be
                # physically removed first (cleared from _finished_disk_nums).
                if drive.disk_number in self._finished_disk_nums:
                    continue
                w = self._workers.get(i)
                if w is None or w.status != CloneStatus.RUNNING:
                    self._window.log(f"[Auto] New drive in slot {i + 1} — starting clone")
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
                f"[Error] Slot {idx + 1} ({drive.first_letter}) is not a removable drive — aborted."
            )
            return

        max_conc = self._config.get_max_concurrent()
        if self._running_count() >= max_conc:
            if idx not in self._queue:
                self._queue.append(idx)
                self._window.set_slot_status(idx, CloneStatus.QUEUED)
                self._flash_set_status(idx, CloneStatus.QUEUED)
                self._window.log(
                    f"[Slot {idx + 1}] Queued — waiting for a free slot (max {max_conc} concurrent)"
                )
            return

        self._launch(idx)

    def _launch(self, idx: int):
        drive = self._drives[idx]
        if drive is None:
            return
        image = self._window.image_path
        odinc = self._config.get_odinc_path()
        size_bytes = drive.size_bytes
        self._speed_samples[idx] = deque(maxlen=6)  # 6 points = 5 intervals

        def _on_progress(pct: int, i: int = idx, sz: int = size_bytes):
            self._window.set_slot_progress(i, pct)
            self._flash_set_progress(i, pct)
            if pct > 0 and sz > 0:
                samples = self._speed_samples.get(i)
                if samples is not None:
                    samples.append((time.time(), pct))
                    if len(samples) >= 2:
                        t0, p0 = samples[0]
                        t1, p1 = samples[-1]
                        dt = t1 - t0
                        if dt > 0 and p1 > p0:
                            bps = (p1 - p0) / 100.0 * sz / dt
                            spd = _fmt_speed(bps)
                            eta = _fmt_eta((100 - p1) / 100.0 * sz / bps)
                            self._window.set_slot_speed(i, spd)
                            self._window.set_slot_eta(i, eta)
                            self._flash_set_speed(i, spd)
                            self._flash_set_eta(i, eta)

        worker = CloneWorker(
            root=self._root,
            odinc_path=odinc,
            image_path=image,
            drive_letter=drive.target_path,  # \Device\HarddiskN\Partition0 — whole disk
            on_progress=_on_progress,
            on_log=lambda line, i=idx: self._window.log(f"[Slot {i + 1}] {line}"),  # type: ignore[misc]
            on_done=lambda status, i=idx: self._on_worker_done(i, status),  # type: ignore[misc]
        )
        self._workers[idx] = worker
        self._window.set_slot_status(idx, CloneStatus.RUNNING)
        self._flash_set_status(idx, CloneStatus.RUNNING)
        self._window.log(
            f"[Slot {idx + 1}] Starting clone → {drive.target_path}  ({drive.display})"
        )
        worker.start()

    def _running_count(self) -> int:
        return sum(1 for w in self._workers.values() if w.status == CloneStatus.RUNNING)

    def _drain_queue(self):
        max_conc = self._config.get_max_concurrent()
        while self._queue and self._running_count() < max_conc:
            next_idx = self._queue.pop(0)
            if self._drives[next_idx] is None:
                continue  # drive removed while queued
            self._window.log(f"[Slot {next_idx + 1}] Starting from queue")
            self._launch(next_idx)

    def _stop_slot(self, idx: int):
        if idx in self._queue:
            self._queue.remove(idx)
            self._window.log(f"[Slot {idx + 1}] Removed from queue.")
            drive = self._drives[idx]
            if drive:
                self._window.set_slot_ready(idx, drive.display)
                self._flash_set_drive(idx, drive.display)
            return
        worker = self._workers.get(idx)
        if worker:
            self._speed_samples.pop(idx, None)
            worker.stop()
            verifier = self._verify_workers.get(idx)
            if verifier:
                verifier.stop()
            self._window.log(f"[Slot {idx + 1}] Stop requested.")

    def _start_all(self):
        for i in range(NUM_SLOTS):
            if self._drives[i] is not None:
                w = self._workers.get(i)
                already_active = i in self._queue or (
                    w is not None and w.status == CloneStatus.RUNNING
                )
                if not already_active:
                    self._start_slot(i)

    def _stop_all(self):
        for i, worker in self._workers.items():
            if worker.status == CloneStatus.RUNNING:
                self._stop_slot(i)
        for verifier in self._verify_workers.values():
            if verifier.status == HashStatus.RUNNING:
                verifier.stop()

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
        # Mark disk as recently finished (only on success) so auto-clone doesn't
        # re-trigger until the card is physically removed and reinserted.
        # Failed clones remain eligible for retry on reseat.
        drive = self._drives[idx]
        if drive is not None and status == CloneStatus.DONE:
            self._finished_disk_nums.add(drive.disk_number)
        self._window.set_slot_status(idx, status)
        self._flash_set_status(idx, status)
        label = {
            CloneStatus.DONE: "Complete",
            CloneStatus.FAILED: "FAILED",
            CloneStatus.STOPPED: "Stopped by user",
        }.get(status, str(status))
        self._window.log(f"[Slot {idx + 1}] {label}")
        if status == CloneStatus.DONE:
            self._window.set_slot_progress(idx, 100)
            self._flash_set_progress(idx, 100)
            if self._config.get_verify_after_clone():
                drain_now = not self._start_target_verify(idx)
            else:
                drain_now = True
        else:
            drain_now = True
        self._speed_samples.pop(idx, None)
        self._window.set_slot_speed(idx, "")
        self._window.set_slot_eta(idx, "")
        self._flash_set_speed(idx, "")
        self._flash_set_eta(idx, "")
        if drain_now:
            self._drain_queue()

    def _start_target_verify(self, idx: int) -> bool:
        image = self._window.image_path
        if not image:
            self._verify_failed(idx, "No image selected for target verification.")
            return False

        drive = self._drives[idx]
        if drive is None:
            self._verify_failed(idx, "Drive was removed before verification.")
            return False

        from hash_config import HashConfig

        cfg = HashConfig().get_partition(image, 0)
        if not (
            (cfg.get("sha1_enabled") and cfg.get("sha1_value"))
            or (cfg.get("sha256_enabled") and cfg.get("sha256_value"))
        ):
            self._verify_failed(idx, "No disk-level hash is configured for this image.")
            return False

        region = get_image_hash_region(image)
        if region is None:
            self._verify_failed(idx, "Image header is not readable.")
            return False
        if not region.is_disk_verifiable:
            if region.compression_scheme != 0:
                msg = f"Compressed ODIN image (scheme {region.compression_scheme}) — target disk verify not supported."
            else:
                msg = (
                    "Used-blocks image — target disk verify not supported "
                    "(image stores packed clusters, not raw sectors)."
                )
            self._verify_failed(idx, msg)
            return False

        self._window.log(f"[Slot {idx + 1}] Verifying flashed disk hash ({region.size} bytes)…")

        def _on_verify_progress(pct: int, i: int = idx) -> None:
            self._window.set_slot_progress(i, pct)
            self._flash_set_progress(i, pct)

        worker = HashWorker(
            root=self._root,
            file_path=drive.raw_device_path,
            on_progress=_on_verify_progress,
            on_done=lambda status, sha256, sha1, i=idx, c=cfg: self._on_target_verify_done(
                i, c, status, sha256, sha1
            ),
            offset=0,
            byte_count=region.size,
        )
        self._verify_workers[idx] = worker
        worker.start()
        return True

    def _on_target_verify_done(
        self, idx: int, cfg: dict, status: HashStatus, sha256: str, sha1: str
    ):
        self._verify_workers.pop(idx, None)
        if status != HashStatus.DONE:
            self._verify_failed(idx, "Target disk hash failed or was cancelled.")
            self._drain_queue()
            return

        checked = False
        failed = False
        if cfg.get("sha1_enabled") and cfg.get("sha1_value"):
            checked = True
            if sha1 == cfg["sha1_value"].lower():
                self._window.log(f"[Slot {idx + 1}] Target SHA-1: pass.")
            else:
                self._window.log(f"[Slot {idx + 1}] Target SHA-1: MISMATCH.")
                failed = True

        if cfg.get("sha256_enabled") and cfg.get("sha256_value"):
            checked = True
            if sha256 == cfg["sha256_value"].lower():
                self._window.log(f"[Slot {idx + 1}] Target SHA-256: pass.")
            else:
                self._window.log(f"[Slot {idx + 1}] Target SHA-256: MISMATCH.")
                failed = True

        if not checked:
            self._verify_failed(idx, "No enabled disk-level hash values were found.")
        elif failed:
            self._verify_failed(idx, "Target hash mismatch.")
        else:
            self._window.log(f"[Slot {idx + 1}] Target verification passed — pull card now.")
            self._flash_set_status(idx, CloneStatus.DONE)
        self._drain_queue()

    def _verify_failed(self, idx: int, message: str):
        self._window.log(f"[Slot {idx + 1}] [Verify] {message}")
        self._flash_set_status(idx, CloneStatus.FAILED)
        if self._config.get_stop_on_verify_fail():
            self._window.log("[Verify] Stop-on-fail: halting queued/running clone work.")
            self._queue.clear()
            self._stop_all()

    def _show_flash_widget(self):
        if self._flash_widget is None or not self._flash_widget.winfo_exists():
            self._flash_widget = FlashStatusWindow(
                self._root, self._mark_pulled, on_lock_change=self._mirror_drives_to_flash
            )
            self._mirror_drives_to_flash()
        else:
            self._flash_widget.deiconify()
            self._flash_widget.lift()

    def _on_flash_widget_toggle(self, enabled: bool):
        if enabled:
            self._show_flash_widget()
        elif self._flash_widget is not None and self._flash_widget.winfo_exists():
            self._flash_widget.withdraw()

    def _mirror_drives_to_flash(self):
        """Sync current drive/worker state to the flash widget."""
        if self._flash_widget is None or not self._flash_widget.winfo_exists():
            return
        for idx in range(NUM_SLOTS):
            drive = self._drives[idx]
            if drive is None:
                self._flash_widget.reset(idx)
            else:
                self._flash_widget.set_drive(idx, drive.display)
                w = self._workers.get(idx)
                if w is not None and w.status != CloneStatus.IDLE:
                    self._flash_widget.set_status(idx, w.status)

    # ── flash widget pass-through helpers ─────────────────────────────────────

    def _flash_set_drive(self, idx: int, display: str):
        if self._flash_widget and self._flash_widget.winfo_exists():
            self._flash_widget.set_drive(idx, display)

    def _flash_set_status(self, idx: int, status: CloneStatus):
        if self._flash_widget and self._flash_widget.winfo_exists():
            self._flash_widget.set_status(idx, status)

    def _flash_set_progress(self, idx: int, pct: int):
        if self._flash_widget and self._flash_widget.winfo_exists():
            self._flash_widget.set_progress(idx, pct)

    def _flash_set_speed(self, idx: int, speed: str):
        if self._flash_widget and self._flash_widget.winfo_exists():
            self._flash_widget.set_speed(idx, speed)

    def _flash_set_eta(self, idx: int, eta: str):
        if self._flash_widget and self._flash_widget.winfo_exists():
            self._flash_widget.set_eta(idx, eta)

    def _mark_pulled(self, idx: int):
        self._window.log(f"[Slot {idx + 1}] Pull acknowledged.")
        drive = self._drives[idx]
        if drive and self._flash_widget:
            self._flash_widget.set_drive(idx, drive.display)
