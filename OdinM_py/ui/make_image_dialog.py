"""
make_image_dialog.py
Modal dialog — automated image setup workflow:
  Step 1: Backup drive  (ODINC -backup -allBlocks -compression=none)
  Step 2: Compute hash  (SHA-256 + SHA-1 via HashWorker)
  Step 3: Save config   (HashLog + HashConfig partition 1, both algos enabled+fail)
After completing, per-partition verify is ready to use.
"""

import os
from tkinter import filedialog
from typing import Optional

_MB = 1_048_576

import ttkbootstrap as ttk
from ttkbootstrap.constants import *

from clone_worker import CloneStatus, CloneWorker
from drive_manager import DriveInfo, get_removable_drives, is_removable
from hash_config import HashConfig
from hash_log import HashLog
from hash_worker import HashStatus, HashWorker


class MakeImageDialog(ttk.Toplevel):

    def __init__(self, parent, odinc_path: str):
        super().__init__(parent)
        self.title("Make Image")
        self.resizable(False, False)
        self.grab_set()

        self._parent     = parent
        self._odinc      = odinc_path
        self._drives     = get_removable_drives()
        self._worker: Optional[CloneWorker] = None
        self._hasher: Optional[HashWorker]  = None
        self._output_path  = ""
        self._backup_flags = ["-allBlocks", "-compression=none"]
        self._backup_polling    = False  # set True while file-size poll is active
        self._poll_ticks        = 0      # seconds since backup started
        self._poll_last_size    = -1     # last seen file size (bytes)
        self._poll_stall_ticks  = 0      # consecutive ticks with same size
        self._backup_drive_size = 0      # total drive bytes — used for progress %

        self._build()

        self.update_idletasks()
        px = parent.winfo_rootx() + (parent.winfo_width()  - self.winfo_width())  // 2
        py = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{px}+{py}")

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.wait_window()

    # ── build ─────────────────────────────────────────────────────────────────

    def _build(self):
        outer = ttk.Frame(self, padding=16)
        outer.pack(fill=BOTH, expand=YES)
        outer.columnconfigure(1, weight=1)

        # Source drive
        ttk.Label(outer, text="Source drive:", anchor=W).grid(
            row=0, column=0, sticky=W, pady=(0, 4))
        drive_labels = [d.display for d in self._drives]
        self._drive_var = ttk.StringVar(
            value=drive_labels[0] if drive_labels else "No removable drives found")
        self._drive_cb = ttk.Combobox(
            outer, textvariable=self._drive_var, state="readonly",
            values=drive_labels, width=46)
        self._drive_cb.grid(row=0, column=1, columnspan=2, sticky=EW,
                            padx=(8, 0), pady=(0, 4))

        # Output file
        ttk.Label(outer, text="Output file:", anchor=W).grid(
            row=1, column=0, sticky=W, pady=(0, 4))
        self._output_var = ttk.StringVar()
        ttk.Entry(outer, textvariable=self._output_var).grid(
            row=1, column=1, sticky=EW, padx=(8, 4), pady=(0, 4))
        ttk.Button(outer, text="Browse…", width=9,
                   command=self._browse_output).grid(row=1, column=2, pady=(0, 4))

        # Auto-workflow toggle
        self._auto_var = ttk.BooleanVar(value=True)
        ttk.Checkbutton(outer, text="Auto hash & configure after backup",
                        variable=self._auto_var,
                        bootstyle="round-toggle").grid(
            row=2, column=0, columnspan=3, sticky=W, pady=(4, 8))

        ttk.Separator(outer, orient=HORIZONTAL).grid(
            row=3, column=0, columnspan=3, sticky=EW, pady=(0, 8))

        # Step indicators
        step_frame = ttk.Frame(outer)
        step_frame.grid(row=4, column=0, columnspan=3, sticky=EW, pady=(0, 6))
        step_frame.columnconfigure(0, weight=0)
        step_frame.columnconfigure(1, weight=1)

        self._step_lbls = []
        step_texts = [
            "Step 1: Backup drive",
            "Step 2: Compute hash",
            "Step 3: Save config",
        ]
        for i, text in enumerate(step_texts):
            icon = ttk.Label(step_frame, text="○", width=2, bootstyle="secondary")
            icon.grid(row=i, column=0, sticky=W)
            lbl  = ttk.Label(step_frame, text=text, anchor=W, bootstyle="secondary")
            lbl.grid(row=i, column=1, sticky=W, padx=(4, 0))
            self._step_lbls.append((icon, lbl))

        ttk.Separator(outer, orient=HORIZONTAL).grid(
            row=5, column=0, columnspan=3, sticky=EW, pady=(6, 8))

        # Progress + status
        self._progress_var = ttk.IntVar(value=0)
        self._pbar = ttk.Progressbar(outer, variable=self._progress_var,
                                      maximum=100, length=420,
                                      bootstyle="info-striped")
        self._pbar.grid(row=6, column=0, columnspan=3, sticky=EW, pady=(0, 4))

        self._status_lbl = ttk.Label(outer, text="Ready", bootstyle="secondary")
        self._status_lbl.grid(row=7, column=0, columnspan=3, sticky=W, pady=(0, 8))

        # Buttons
        btn_frame = ttk.Frame(outer)
        btn_frame.grid(row=8, column=0, columnspan=3, sticky=EW)

        self._start_btn = ttk.Button(btn_frame, text="Start",
                                      bootstyle="success", command=self._start)
        self._start_btn.pack(side=LEFT, padx=(0, 6))
        self._stop_btn = ttk.Button(btn_frame, text="Stop",
                                     bootstyle="danger-outline", command=self._stop,
                                     state=DISABLED)
        self._stop_btn.pack(side=LEFT, padx=(0, 6))
        ttk.Button(btn_frame, text="Options…", bootstyle="secondary-outline",
                   command=self._open_options).pack(side=LEFT, padx=(0, 6))
        ttk.Button(btn_frame, text="Close", bootstyle="outline",
                   command=self._on_close).pack(side=RIGHT)

    # ── step indicator helpers ────────────────────────────────────────────────

    def _set_step(self, idx: int, state: str):
        """state: 'pending' | 'running' | 'done' | 'failed'"""
        if not self.winfo_exists():
            return
        icon, lbl = self._step_lbls[idx]
        styles = {
            "pending": ("○", "secondary"),
            "running": ("▶", "info"),
            "done":    ("✓", "success"),
            "failed":  ("✗", "danger"),
        }
        sym, style = styles.get(state, ("○", "secondary"))
        icon.configure(text=sym, bootstyle=style)
        lbl.configure(bootstyle=style)

    # ── actions ───────────────────────────────────────────────────────────────

    def _browse_output(self):
        path = filedialog.asksaveasfilename(
            title="Save image as",
            defaultextension=".img",
            filetypes=[("ODIN image", "*.img *.odin *.bin"), ("All files", "*.*")],
            parent=self,
        )
        if path:
            self._output_var.set(path)

    def _open_options(self):
        from ui.image_options_dialog import ImageOptionsDialog
        dlg = ImageOptionsDialog(self, self._backup_flags)
        if dlg.result is not None:
            self._backup_flags = dlg.result

    def _start(self):
        idx = self._drive_cb.current()
        if idx < 0 or idx >= len(self._drives):
            self._status("No drive selected.", "danger")
            return
        drive: DriveInfo = self._drives[idx]
        if not is_removable(drive.first_letter):
            self._status(f"{drive.first_letter} is not removable — aborted.", "danger")
            return
        output = self._output_var.get().strip()
        if not output:
            self._status("Choose an output file first.", "danger")
            return

        self._output_path       = output
        self._backup_drive_size = drive.size_bytes
        self._progress_var.set(0)
        self._start_btn.configure(state=DISABLED)
        self._stop_btn.configure(state=NORMAL)

        # reset step indicators
        for i in range(3):
            self._set_step(i, "pending")
        self._set_step(0, "running")
        self._status("Step 1/3: Backing up drive…", "info")

        # ODINC rebinds stdout to a console window internally, so
        # percentage output never reaches our pipe — use animated bar.
        self._pbar.configure(mode="indeterminate")
        self._pbar.start(15)

        self._worker = CloneWorker(
            root=self._parent,
            odinc_path=self._odinc,
            image_path=output,
            drive_letter=drive.target_path,
            on_progress=self._on_backup_progress,
            on_log=lambda line: self._status(line, "secondary"),
            on_done=self._on_backup_done,
            mode="backup",
            extra_flags=self._backup_flags,
        )
        self._worker.start()

        # Start file-size poller for error detection
        self._backup_polling   = True
        self._poll_ticks       = 0
        self._poll_last_size   = -1
        self._poll_stall_ticks = 0
        self.after(1000, self._poll_backup_file)

    def _stop(self):
        if self._worker and self._worker.status == CloneStatus.RUNNING:
            self._worker.stop()
        if self._hasher:
            self._hasher.stop()

    # ── backup callbacks ──────────────────────────────────────────────────────

    def _on_backup_progress(self, pct: int):
        if self.winfo_exists():
            self._progress_var.set(pct)

    def _on_backup_done(self, status: CloneStatus):
        if not self.winfo_exists():
            return
        self._backup_polling = False   # stop file-size poller
        self._pbar.stop()
        self._pbar.configure(mode="determinate")
        if status == CloneStatus.DONE:
            self._set_step(0, "done")
            if self._auto_var.get():
                self._start_hash()
            else:
                self._progress_var.set(100)
                self._status("Backup complete.", "success")
                self._finish_buttons()
        elif status == CloneStatus.STOPPED:
            self._progress_var.set(0)
            self._set_step(0, "failed")
            self._status("Stopped by user.", "warning")
            self._finish_buttons()
        else:
            self._progress_var.set(0)
            self._set_step(0, "failed")
            self._status("Backup FAILED.", "danger")
            self._finish_buttons()

    # ── hash step ─────────────────────────────────────────────────────────────

    def _start_hash(self):
        if not self.winfo_exists():
            return
        self._progress_var.set(0)
        self._set_step(1, "running")
        self._status("Step 2/3: Computing hash…", "info")

        self._hasher = HashWorker(
            root=self._parent,
            file_path=self._output_path,
            on_progress=self._on_hash_progress,
            on_done=self._on_hash_done,
        )
        self._hasher.start()

    def _on_hash_progress(self, pct: int):
        if self.winfo_exists():
            self._progress_var.set(pct)

    def _on_hash_done(self, status: HashStatus, sha256: str, sha1: str):
        if not self.winfo_exists():
            return
        if status == HashStatus.DONE:
            self._set_step(1, "done")
            self._save_config(sha256, sha1)
        else:
            self._set_step(1, "failed")
            self._status("Hash computation failed.", "danger")
            self._finish_buttons()

    # ── config step ───────────────────────────────────────────────────────────

    def _save_config(self, sha256: str, sha1: str):
        if not self.winfo_exists():
            return
        self._progress_var.set(100)
        self._set_step(2, "running")
        self._status("Step 3/3: Saving hash config…", "info")

        try:
            # Save to hash log (timestamp + values)
            HashLog().save_entry(self._output_path, sha256, sha1)

            # Save to partition 0 (whole-disk/image hash), both algos enabled+fail
            HashConfig().save_partition(self._output_path, 0, {
                "sha1_value":     sha1,
                "sha1_enabled":   True,
                "sha1_fail":      True,
                "sha256_value":   sha256,
                "sha256_enabled": True,
                "sha256_fail":    True,
            })

            self._set_step(2, "done")
            self._status(
                "Setup complete — disk-level hash saved (partition 0). "
                "Use Options → Configure Hashes to add per-partition hashes.",
                "success")
        except Exception as exc:
            self._set_step(2, "failed")
            self._status(f"Config save failed: {exc}", "danger")

        self._finish_buttons()

    # ── file-size poller ─────────────────────────────────────────────────────

    def _poll_backup_file(self):
        """Called every second while backup is running.
        Updates status label with current file size and detects silent failures."""
        if not self._backup_polling or not self.winfo_exists():
            return

        self._poll_ticks += 1
        path = self._output_path

        try:
            size = os.path.getsize(path)
        except OSError:
            size = -1

        if size < 0:
            # File not created yet
            if self._poll_ticks >= 8:
                self._status(
                    f"No output file after {self._poll_ticks}s — "
                    "check ODINC path, drive letter, or Bitdefender ATD.",
                    "warning",
                )
        else:
            # Switch from indeterminate to real progress on first file appearance
            if self._pbar.cget("mode") == "indeterminate":
                self._pbar.stop()
                self._pbar.configure(mode="determinate")

            if size == self._poll_last_size:
                self._poll_stall_ticks += 1
            else:
                self._poll_stall_ticks = 0

            self._poll_last_size = size
            mb  = size / _MB
            tot = self._backup_drive_size

            if tot > 0:
                pct = min(int(size * 100 / tot), 99)
                self._progress_var.set(pct)
                tot_mb = tot / _MB
                self._status(f"Writing: {mb:.1f} / {tot_mb:.0f} MB  ({pct}%)", "info")
            else:
                self._status(f"Writing: {mb:.1f} MB…", "info")

            if self._poll_stall_ticks >= 15:
                self._status(
                    f"Write stalled at {mb:.1f} MB for {self._poll_stall_ticks}s "
                    "— ODINC may have failed silently.",
                    "warning",
                )

        self.after(1000, self._poll_backup_file)

    # ── helpers ───────────────────────────────────────────────────────────────

    def _status(self, text: str, style: str = "secondary"):
        if self.winfo_exists():
            self._status_lbl.configure(text=text, bootstyle=style)

    def _finish_buttons(self):
        if self.winfo_exists():
            self._start_btn.configure(state=NORMAL)
            self._stop_btn.configure(state=DISABLED)

    def _on_close(self):
        if self._worker and self._worker.status == CloneStatus.RUNNING:
            self._worker.stop()
        if self._hasher:
            self._hasher.stop()
        self.destroy()
