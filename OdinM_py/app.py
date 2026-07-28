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
from drive_manager import (
    DriveInfo,
    DriveMonitor,
    debug_probe_disks,
    get_removable_drives,
    is_disk_removable,
)
from hash_worker import HashStatus, HashWorker
from partition_reader import get_image_hash_region
from pyimager_worker import PyImagerRestoreWorker, randomize_disk_signature
from ui.flash_status_window import FlashStatusWindow
from ui.main_window import MainWindow, NUM_SLOTS

APP_TITLE = "OdinM_py v2 — Multi-Drive Clone Tool"


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

# How long a newly-inserted drive must sit connected before auto-clone starts
# writing to it - gives Windows/the reader a moment to settle after insertion
# rather than writing the instant the card is first detected.
AUTO_CLONE_DELAY_MS = 5000

# How long a slot's confirmed lock survives after its disk is confirmed
# removed. The same disk number reappearing within this window is still
# treated as confirmed (no re-click needed, auto-clone can proceed) - only
# after this long with it still gone does the lock actually expire.
CONFIRM_LOCK_GRACE_MS = 15 * 60 * 1000


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
        self._workers: dict[int, CloneWorker | PyImagerRestoreWorker] = {}
        self._queue: list[int] = []  # slot indices waiting to start
        # slot → deque of (timestamp, pct) samples for rolling speed window
        self._speed_samples: dict[int, deque] = {}
        self._verify_workers: dict[int, HashWorker] = {}
        # disk numbers that just finished cloning — cleared when drive is removed
        # so the same physical card does not trigger a second auto-clone
        self._finished_disk_nums: set = set()
        # slot index -> disk_number currently shown in the flash widget
        # (None if empty) - see _mirror_drives_to_flash()
        self._flash_displayed_disk_nums: dict[int, int | None] = {}
        # disk_number -> pending after() job id for a delayed auto-clone
        # start, so a newly-inserted card gets a moment to settle before
        # auto-clone starts writing to it (see _on_drives_changed)
        self._auto_clone_pending: dict[int, str] = {}
        # slot index -> disk_number the operator has confirmed/locked in.
        # Nothing may flash (manual Start or auto-clone) until a slot's
        # current disk_number matches its entry here - see _confirm_slot().
        self._locked_disk_nums: dict[int, int] = {}
        # slot index -> pending after() job id for CONFIRM_LOCK_GRACE_MS,
        # started when a locked slot's disk is confirmed removed. Cancelled
        # if the same disk number comes back first.
        self._lock_expiry_jobs: dict[int, str] = {}
        self._flash_widget: FlashStatusWindow | None = None
        if self._config.get_show_flash_widget():
            self._show_flash_widget()

        self._monitor = DriveMonitor(self._root, self._on_drives_changed)
        self._monitor.refresh()  # populate the initial slot state on launch

    def run(self):
        self._root.mainloop()

    # ── callback wiring ───────────────────────────────────────────────────────

    def _wire_callbacks(self):
        self._window.on_start_slot = self._start_slot
        self._window.on_stop_slot = self._stop_slot
        self._window.on_confirm_slot = self._confirm_slot
        self._window.on_verify_slot = self._verify_slot
        self._window.on_start_all = self._start_all
        self._window.on_stop_all = self._stop_all
        self._window.on_refresh_disks = self._refresh_disks
        self._window.on_verify_image = self._verify_image
        self._window.on_configure_hashes = self._configure_hashes
        self._window.on_verify_stored = self._verify_stored
        self._window.on_make_image = self._make_image
        self._window.on_flash_widget_toggle = self._on_flash_widget_toggle

    # ── drive monitor callback ────────────────────────────────────────────────

    def _on_drives_changed(self, drives: list[DriveInfo]):
        """Called only from a manual refresh (DriveMonitor.refresh()) now -
        never a continuous poll. Fills empty slots with newly-discovered
        disks. A disk matching a slot's pending confirm-lock expiry
        (reconnected within its 15-minute grace) reclaims that exact slot,
        resumes being watched, and needs no fresh Confirm click. Already-
        occupied slots are left completely alone - once confirmed, that
        slot's own DriveMonitor.watch_slot() call is what notices a removal;
        this function never touches an occupied slot's contents.
        """
        # Apply max drive size filter — keeps oversized drives (e.g. a dev
        # USB stick) out of slots and auto-clone entirely. 0 = no limit.
        max_gb = self._config.get_max_drive_gb()
        max_bytes = max_gb * (1 << 30) if max_gb > 0 else 0
        drives = [
            d for d in drives if max_bytes == 0 or d.size_bytes == 0 or d.size_bytes <= max_bytes
        ]

        # Cap how many removable disks get considered at all - already-
        # occupied slots' disks are never excluded by this (only new/
        # unassigned candidates compete for the remaining budget), so an
        # active slot can never be dropped just because more cards showed
        # up elsewhere. Extras beyond the cap are excluded the same way an
        # oversized drive already is above - never even reach slot
        # assignment.
        max_disks = self._config.get_max_disks()
        if max_disks > 0 and len(drives) > max_disks:
            already_occupied_nums = {d.disk_number for d in self._drives if d is not None}
            kept = [d for d in drives if d.disk_number in already_occupied_nums]
            candidates = sorted(
                (d for d in drives if d.disk_number not in already_occupied_nums),
                key=lambda d: d.disk_number,
            )
            room = max(0, max_disks - len(kept))
            drives = kept + candidates[:room]

        # Re-verify UNCONFIRMED occupied slots - these have no active watch
        # yet (watch_slot only starts once a slot is confirmed), so a
        # manual refresh is the only thing that can notice one of these
        # disappeared before the operator got to Confirm it.
        current_disk_nums = {d.disk_number for d in drives}
        for i in range(NUM_SLOTS):
            prev = self._drives[i]
            if prev is None:
                continue
            if self._locked_disk_nums.get(i) == prev.disk_number:
                continue  # confirmed - its own watch_slot() owns this
            if prev.disk_number in current_disk_nums:
                continue  # still there
            self._drives[i] = None
            pending = self._auto_clone_pending.pop(prev.disk_number, None)
            if pending is not None:
                self._root.after_cancel(pending)
            self._window.log(
                f"[Slot {i + 1}] Disk no longer present — cleared (was awaiting confirm)."
            )

        occupied_disks = {d.disk_number for d in self._drives if d is not None}
        free_slots = [i for i in range(NUM_SLOTS) if self._drives[i] is None]
        newly_seen = sorted(
            (d for d in drives if d.disk_number not in occupied_disks),
            key=lambda d: d.disk_number,
        )

        still_unplaced = []
        for d in newly_seen:
            pending_slot = next(
                (i for i in free_slots
                 if i in self._lock_expiry_jobs
                 and self._locked_disk_nums.get(i) == d.disk_number),
                None,
            )
            if pending_slot is None:
                still_unplaced.append(d)
                continue
            free_slots.remove(pending_slot)
            self._reclaim_pending_slot(pending_slot, d)

        for slot, d in zip(free_slots, still_unplaced):
            self._drives[slot] = d
            # Not confirmed yet - just settle-delay bookkeeping for auto-
            # clone; _auto_clone_delayed() still waits on Confirm before
            # ever actually starting anything.
            if self._config.get_auto_clone() and d.disk_number not in self._auto_clone_pending:
                self._window.log(
                    f"[Auto] New drive in slot {slot + 1} — waiting "
                    f"{AUTO_CLONE_DELAY_MS // 1000}s for it to settle"
                )
                self._auto_clone_pending[d.disk_number] = self._root.after(
                    AUTO_CLONE_DELAY_MS,
                    lambda idx=slot, dn=d.disk_number: self._auto_clone_delayed(idx, dn),
                )

        self._queue = [i for i in self._queue if self._drives[i] is not None]
        self._window.update_drives(self._drives, self._locked_disk_nums)
        self._window.log(f"[Drives] {len(drives)} removable drive(s) detected")
        self._mirror_drives_to_flash()

    def _on_slot_disk_missing(self, idx: int):
        """Called by this slot's own DriveMonitor.watch_slot() once its
        locked disk has been confirmed absent (2 consecutive per-disk
        checks) - completely independent of every other slot, since the
        watch only ever looks at this one disk number."""
        drive = self._drives[idx]
        disk_number = drive.disk_number if drive is not None else self._locked_disk_nums.get(idx)
        self._drives[idx] = None
        if disk_number is not None:
            self._finished_disk_nums.discard(disk_number)
            pending = self._auto_clone_pending.pop(disk_number, None)
            if pending is not None:
                self._root.after_cancel(pending)
            if idx in self._locked_disk_nums and idx not in self._lock_expiry_jobs:
                self._lock_expiry_jobs[idx] = self._root.after(
                    CONFIRM_LOCK_GRACE_MS,
                    lambda i=idx, dn=disk_number: self._expire_lock(i, dn),
                )
                # Keep looking for THIS slot's disk number specifically, so
                # it reclaims automatically within the grace period instead
                # of needing a manual "Refresh Disks" click.
                self._monitor.watch_for_return(
                    idx, disk_number,
                    on_return=lambda i=idx, dn=disk_number: self._on_slot_disk_returned(i, dn),
                )
        w = self._workers.get(idx)
        if w is not None and w.status == CloneStatus.RUNNING:
            self._window.log(f"[Slot {idx + 1}] Drive removed during flash — aborting.")
            self._speed_samples.pop(idx, None)
            w.stop()
        else:
            self._window.set_slot_ready(idx, "")
            self._window.set_slot_status(idx, CloneStatus.IDLE)
            self._window.set_slot_progress(idx, 0)
            self._flash_set_status(idx, CloneStatus.IDLE)
        self._queue = [i for i in self._queue if self._drives[i] is not None]
        self._window.update_drives(self._drives, self._locked_disk_nums)
        self._window.log(f"[Slot {idx + 1}] Disk removed.")
        self._mirror_drives_to_flash()

    def _on_slot_disk_returned(self, idx: int, disk_number: int):
        """This slot's own DriveMonitor.watch_for_return() found its locked
        disk_number present again within the confirm grace period - reclaim
        it immediately, without waiting for a manual Refresh Disks click."""
        if self._locked_disk_nums.get(idx) != disk_number:
            return  # lock already superseded or expired - nothing to do
        if self._drives[idx] is not None:
            return  # already reclaimed some other way (e.g. manual refresh)
        for d in get_removable_drives():
            if d.disk_number == disk_number:
                self._reclaim_pending_slot(idx, d)
                return

    def _reclaim_pending_slot(self, idx: int, d: DriveInfo):
        """Slot `idx` was locked to d.disk_number and went missing within its
        grace period; d has just been found present again - restore it
        without requiring a fresh Confirm click. Shared by the manual-
        refresh reclaim path in _on_drives_changed() and the automatic
        watch_for_return() path above."""
        self._drives[idx] = d
        job = self._lock_expiry_jobs.pop(idx, None)
        if job is not None:
            self._root.after_cancel(job)
        self._window.log(
            f"[Slot {idx + 1}] Reconnected within its confirm "
            "grace period — still locked, no re-confirm needed."
        )
        self._monitor.watch_slot(
            idx, d.disk_number,
            on_missing=lambda i=idx: self._on_slot_disk_missing(i),
        )
        self._window.update_drives(self._drives, self._locked_disk_nums)
        self._try_auto_clone(idx)

    def _try_auto_clone(self, idx: int):
        """If auto-clone is on and this now-confirmed slot is otherwise
        eligible, start it. Shared by _confirm_slot() and the grace-period
        reclaim path in _on_drives_changed() - both represent a slot
        becoming confirmed without going through the fresh-disk settle-
        delay path that _auto_clone_delayed() handles."""
        if not self._config.get_auto_clone():
            return
        drive = self._drives[idx]
        if drive is None or drive.disk_number in self._finished_disk_nums:
            return
        w = self._workers.get(idx)
        if w is not None and w.status == CloneStatus.RUNNING:
            return
        image = self._window.image_path
        if not image or not os.path.isfile(image):
            return
        self._window.log(f"[Auto] Slot {idx + 1} confirmed — starting clone")
        self._start_slot(idx)

    def _auto_clone_delayed(self, idx: int, disk_number: int):
        """Start an auto-clone that was scheduled AUTO_CLONE_DELAY_MS ago,
        after re-checking everything is still valid - the card may have been
        pulled, swapped, or auto-clone/the image may have changed during the
        wait."""
        self._auto_clone_pending.pop(disk_number, None)
        if not self._config.get_auto_clone():
            return
        image = self._window.image_path
        if not image or not os.path.isfile(image):
            return
        drive = self._drives[idx]
        if drive is None or drive.disk_number != disk_number:
            return  # card was pulled or swapped during the settle delay
        if disk_number in self._finished_disk_nums:
            return
        w = self._workers.get(idx)
        if w is not None and w.status == CloneStatus.RUNNING:
            return
        if self._locked_disk_nums.get(idx) != disk_number:
            # Settled, but the operator hasn't clicked Confirm yet - whenever
            # they do, _confirm_slot() sees this delay already elapsed and
            # starts it then instead.
            self._window.log(
                f"[Auto] Slot {idx + 1} settled — waiting for Confirm before flashing"
            )
            return
        self._window.log(f"[Auto] Slot {idx + 1} settled — starting clone")
        self._start_slot(idx)

    def _confirm_slot(self, idx: int):
        """Operator clicked the "Confirm" status badge - lock this slot onto
        its currently-detected disk number and start watching that specific
        disk (independent of every other slot) for removal. Refuses
        (silently) if the drive already vanished before the click was
        processed."""
        drive = self._drives[idx]
        if drive is None:
            return
        self._locked_disk_nums[idx] = drive.disk_number
        expiry = self._lock_expiry_jobs.pop(idx, None)
        if expiry is not None:
            self._root.after_cancel(expiry)
        self._window.confirm_slot(idx)
        self._window.log(f"[Slot {idx + 1}] Confirmed — locked to Disk {drive.disk_number}.")
        self._monitor.watch_slot(
            idx, drive.disk_number,
            on_missing=lambda i=idx: self._on_slot_disk_missing(i),
        )

        if drive.disk_number in self._auto_clone_pending:
            return  # still waiting on its own settle delay; that will start it
        # Settle delay already elapsed while awaiting confirmation (or
        # auto-clone wasn't on until just now) - this click is what
        # finally triggers the auto-clone start, if eligible.
        self._try_auto_clone(idx)

    def _expire_lock(self, idx: int, disk_number: int):
        """A locked slot's disk stayed gone for CONFIRM_LOCK_GRACE_MS -
        release the lock so a fresh Confirm is required if something shows
        up in this slot again."""
        self._lock_expiry_jobs.pop(idx, None)
        if self._locked_disk_nums.get(idx) != disk_number:
            return  # superseded already - nothing to do
        drive = self._drives[idx]
        if drive is not None and drive.disk_number == disk_number:
            return  # it came back after all
        self._locked_disk_nums.pop(idx, None)
        self._monitor.unwatch_slot(idx)  # stop polling for a disk we no longer own
        self._window.log(
            f"[Slot {idx + 1}] Confirmation expired after "
            f"{CONFIRM_LOCK_GRACE_MS // 60000} min with no disk — re-confirm required."
        )

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
        if not is_disk_removable(drive.disk_number):
            self._window.log(
                f"[Error] Slot {idx + 1} (Disk {drive.disk_number}) is not a removable drive — aborted."
            )
            return
        if self._locked_disk_nums.get(idx) != drive.disk_number:
            self._window.log(
                f"[Error] Slot {idx + 1} is not confirmed — click Confirm before starting."
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

        def _on_log(line: str, i: int = idx):
            self._window.log(f"[Slot {i + 1}] {line}")

        def _on_done(status: CloneStatus, i: int = idx):
            self._on_worker_done(i, status)

        use_pyimager = self._config.use_pyimager()
        if use_pyimager:
            worker = PyImagerRestoreWorker(
                root=self._root,
                disk_number=drive.disk_number,
                image_path=image,
                on_progress=_on_progress,
                on_log=_on_log,
                on_done=_on_done,
                volumes=[letter.rstrip(":") for letter in drive.all_letters],
            )
        else:
            worker = CloneWorker(
                root=self._root,
                odinc_path=self._config.get_odinc_path(),
                image_path=image,
                drive_letter=drive.target_path,  # \Device\HarddiskN\Partition0 — whole disk
                on_progress=_on_progress,
                on_log=_on_log,
                on_done=_on_done,
            )
        self._workers[idx] = worker
        self._window.set_slot_status(idx, CloneStatus.RUNNING)
        self._flash_set_status(idx, CloneStatus.RUNNING)
        engine = "pyimager" if use_pyimager else "ODINC"
        self._window.log(
            f"[Slot {idx + 1}] Starting clone ({engine}) → {drive.target_path}  ({drive.display})"
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

    def _refresh_disks(self):
        """Force an immediate re-scan instead of waiting on the automatic
        2-second poll - e.g. after bringing a disk online externally via
        diskpart, or any other state change the poll hasn't caught yet.
        Always logs the full per-disk probe (debug_probe_disks()) since
        this is a manual, infrequent action - cheap to be verbose, and it's
        exactly what's needed to diagnose a "0 removable drives" report."""
        self._window.log("[Drives] Manual refresh requested.")
        for line in debug_probe_disks():
            self._window.log(f"[Drives]   {line}")
        self._monitor.refresh()

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

        MakeImageDialog(self._root, self._config.get_odinc_path(), self._config)

    def _on_worker_done(self, idx: int, status: CloneStatus):
        # Mark disk as recently finished (only on success) so auto-clone doesn't
        # re-trigger until the card is physically removed and reinserted.
        # Failed clones remain eligible for retry on reseat.
        drive = self._drives[idx]
        if drive is not None and status == CloneStatus.DONE:
            self._finished_disk_nums.add(drive.disk_number)
        auto_verify = self._config.get_verify_after_clone()
        offer_verify = status == CloneStatus.DONE and not auto_verify
        self._window.set_slot_status(idx, status, offer_verify=offer_verify)
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
            if auto_verify:
                drain_now = not self._start_target_verify(idx)
            else:
                # Signature fix is deferred until a verify actually runs (see
                # _fix_disk_signature()'s ordering warning) - the operator
                # can trigger one on demand via the slot's "Verify" button
                # (see _verify_slot()), or just pull the card and skip it.
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

    def _verify_slot(self, idx: int):
        """Operator clicked "Verify" - offered instead of "Start" right
        after a flash completes with verify-after-clone turned off, so a
        card can still be checked on demand without enabling it globally.
        Runs the exact same target-disk hash check the auto-verify path
        uses (_start_target_verify -> _on_target_verify_done), including
        fixing the disk signature afterward on success.

        Flips the button back to "Start" immediately (matching how the
        auto-verify path already looks throughout its own verify pass) so
        a second click can't launch a duplicate verify against the same
        drive.
        """
        if self._drives[idx] is None:
            return
        self._window.set_slot_status(idx, CloneStatus.DONE, offer_verify=False)
        self._start_target_verify(idx)

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
            self._fix_disk_signature(idx)
        self._drain_queue()

    def _fix_disk_signature(self, idx: int):
        """Give the flashed disk a fresh MBR signature so Windows mounts it
        instead of treating it as a duplicate of another card cloned from
        the same master.

        Must only be called after byte-for-byte verification has passed (or
        was intentionally skipped) - see randomize_disk_signature()'s
        docstring for why the ordering matters. Never called from a failure
        path, so a real corruption is never masked by this.
        """
        drive = self._drives[idx]
        if drive is None:
            return
        try:
            randomize_disk_signature(
                drive.disk_number,
                volumes=[letter.rstrip(":") for letter in drive.all_letters],
            )
        except OSError as exc:
            self._window.log(f"[Slot {idx + 1}] Could not fix disk signature: {exc}")
            return
        self._window.log(f"[Slot {idx + 1}] Disk signature randomized — card will mount normally.")

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
            # Brand-new widget instance has no rows yet - force a full
            # render regardless of what _mirror_drives_to_flash's diff
            # cache remembers from a previous (now-destroyed) instance.
            self._flash_displayed_disk_nums.clear()
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
        """Sync current drive/worker state to the flash widget.

        set_drive()/reset() both reset status to IDLE and clear pct/speed/eta
        - fine the first time a slot's disk appears, but calling either again
        for a slot whose disk hasn't actually changed would wipe an active
        flash's live progress every time ANY other slot's drive-list entry
        refreshes. Only re-render a slot when its disk_number actually
        changes; otherwise just let a running worker's real status show.
        """
        if self._flash_widget is None or not self._flash_widget.winfo_exists():
            return
        for idx in range(NUM_SLOTS):
            drive = self._drives[idx]
            disk_num = drive.disk_number if drive is not None else None
            if self._flash_displayed_disk_nums.get(idx) != disk_num:
                self._flash_displayed_disk_nums[idx] = disk_num
                if drive is None:
                    self._flash_widget.reset(idx)
                else:
                    self._flash_widget.set_drive(idx, drive.display)
            if drive is not None:
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
