"""
hash_dialog.py
Modal dialog that computes and displays SHA-256 / SHA-1 of the selected image file.
- Checks hash_log.py for prior results and warns if > 30 days old.
- Saves result to log on completion.
- Inline copy buttons on each hash field.
"""

import os
import tkinter as tk
from typing import Optional

import ttkbootstrap as ttk
from ttkbootstrap.constants import *

from hash_config import HashConfig, NUM_PARTITIONS
from hash_log import HashLog
from hash_worker import HashStatus, HashWorker


class HashDialog(ttk.Toplevel):
    """
    Opens as a modal window over the given parent.
    Starts hashing immediately on open.
    """

    def __init__(self, parent, image_path: str):
        super().__init__(parent)
        self.title("Verify Image Hash")
        self.resizable(False, False)
        self.grab_set()

        self._parent = parent
        self._path   = image_path
        self._log    = HashLog()
        self._worker: Optional[HashWorker] = None

        self._build()
        self._start_hash()

        # Centre over parent
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

        # File info
        fname = os.path.basename(self._path)
        try:
            sz_str = _fmt_size(os.path.getsize(self._path))
        except OSError:
            sz_str = "?"

        ttk.Label(outer, text="File:", anchor=W).grid(row=0, column=0, sticky=W, pady=2)
        ttk.Label(outer, text=fname, bootstyle="info", anchor=W).grid(
            row=0, column=1, columnspan=2, sticky=W, padx=(8, 0), pady=2)

        ttk.Label(outer, text="Size:", anchor=W).grid(row=1, column=0, sticky=W, pady=2)
        ttk.Label(outer, text=sz_str, anchor=W).grid(
            row=1, column=1, columnspan=2, sticky=W, padx=(8, 0), pady=2)

        # Last verified age
        ttk.Label(outer, text="Last verified:", anchor=W).grid(row=2, column=0, sticky=W, pady=2)
        self._age_lbl = ttk.Label(outer, text=self._age_text(), anchor=W,
                                   bootstyle=self._age_style())
        self._age_lbl.grid(row=2, column=1, columnspan=2, sticky=W, padx=(8, 0), pady=2)

        # Progress
        ttk.Separator(outer, orient=HORIZONTAL).grid(
            row=3, column=0, columnspan=3, sticky=EW, pady=(8, 4))

        ttk.Label(outer, text="Progress:", anchor=W).grid(row=4, column=0, sticky=W, pady=2)
        self._progress_var = ttk.IntVar(value=0)
        ttk.Progressbar(outer, variable=self._progress_var, maximum=100,
                        length=360, bootstyle="info-striped").grid(
            row=4, column=1, columnspan=2, sticky=EW, padx=(8, 0), pady=2)

        self._status_lbl = ttk.Label(outer, text="Computing…", bootstyle="secondary")
        self._status_lbl.grid(row=5, column=1, columnspan=2, sticky=W, padx=(8, 0))

        # Hash results
        ttk.Separator(outer, orient=HORIZONTAL).grid(
            row=6, column=0, columnspan=3, sticky=EW, pady=(8, 4))

        ttk.Label(outer, text="SHA-256:", anchor=W).grid(row=7, column=0, sticky=W, pady=2)
        self._sha256_var = ttk.StringVar(value="—")
        ttk.Entry(outer, textvariable=self._sha256_var,
                  state=READONLY, bootstyle="secondary").grid(
            row=7, column=1, sticky=EW, padx=(8, 4), pady=2)
        ttk.Button(outer, text="Copy", width=5, bootstyle="secondary-outline",
                   command=lambda: self._copy(self._sha256_var)).grid(
            row=7, column=2, pady=2)

        ttk.Label(outer, text="SHA-1:", anchor=W).grid(row=8, column=0, sticky=W, pady=2)
        self._sha1_var = ttk.StringVar(value="—")
        ttk.Entry(outer, textvariable=self._sha1_var,
                  state=READONLY, bootstyle="secondary").grid(
            row=8, column=1, sticky=EW, padx=(8, 4), pady=2)
        ttk.Button(outer, text="Copy", width=5, bootstyle="secondary-outline",
                   command=lambda: self._copy(self._sha1_var)).grid(
            row=8, column=2, pady=2)

        # Expected hash comparison
        ttk.Separator(outer, orient=HORIZONTAL).grid(
            row=9, column=0, columnspan=3, sticky=EW, pady=(8, 4))

        ttk.Label(outer, text="Expected\nhash:", anchor=W, justify=LEFT).grid(
            row=10, column=0, sticky=W, pady=2)
        self._expected_var = ttk.StringVar()
        ttk.Entry(outer, textvariable=self._expected_var).grid(
            row=10, column=1, columnspan=2, sticky=EW, padx=(8, 0), pady=2)

        self._match_lbl = ttk.Label(outer, text="", anchor=W)
        self._match_lbl.grid(row=11, column=1, columnspan=2, sticky=W, padx=(8, 0))

        # Buttons
        btn_frame = ttk.Frame(outer)
        btn_frame.grid(row=12, column=0, columnspan=3, sticky=EW, pady=(12, 0))

        ttk.Button(btn_frame, text="Compare", command=self._compare,
                   bootstyle="info-outline").pack(side=LEFT, padx=(0, 6))
        ttk.Button(btn_frame, text="Close", command=self._on_close,
                   bootstyle="outline").pack(side=RIGHT)

    # ── age helpers ───────────────────────────────────────────────────────────

    def _age_text(self) -> str:
        text, _ = _age_info(self._log.days_since(self._path))
        return text

    def _age_style(self) -> str:
        _, style = _age_info(self._log.days_since(self._path))
        return style

    # ── hash worker callbacks ─────────────────────────────────────────────────

    def _start_hash(self):
        self._worker = HashWorker(
            root=self._parent,
            file_path=self._path,
            on_progress=self._on_progress,
            on_done=self._on_done,
        )
        self._worker.start()

    def _on_progress(self, pct: int):
        if self.winfo_exists():
            self._progress_var.set(pct)

    def _on_done(self, status: HashStatus, sha256: str, sha1: str):
        if not self.winfo_exists():
            return
        self._progress_var.set(100)
        if status == HashStatus.DONE:
            self._sha256_var.set(sha256)
            self._sha1_var.set(sha1)
            self._status_lbl.configure(text="Done", bootstyle="success")
            self._log.save_entry(self._path, sha256, sha1)
            self._age_lbl.configure(text=self._age_text(), bootstyle=self._age_style())
            if self._expected_var.get().strip():
                self._compare()
        elif status == HashStatus.STOPPED:
            self._status_lbl.configure(text="Cancelled", bootstyle="warning")
        else:
            self._status_lbl.configure(text="Failed — check file path", bootstyle="danger")

    # ── actions ───────────────────────────────────────────────────────────────

    def _compare(self):
        expected = self._expected_var.get().strip().lower()
        if not expected:
            self._match_lbl.configure(text="Enter expected hash above", bootstyle="secondary")
            return

        sha256 = self._sha256_var.get().strip().lower()
        sha1   = self._sha1_var.get().strip().lower()
        if sha256 in ("—", ""):
            self._match_lbl.configure(text="Hash not computed yet", bootstyle="warning")
            return

        length = len(expected)
        if length == 64:
            algo, actual = "SHA-256", sha256
        elif length == 40:
            algo, actual = "SHA-1", sha1
        else:
            self._match_lbl.configure(
                text=f"Unrecognised hash length ({length} chars; expected 40 or 64)",
                bootstyle="warning")
            return

        if expected == actual:
            self._match_lbl.configure(text=f"{algo}: Match — file is intact", bootstyle="success")
        else:
            self._match_lbl.configure(text=f"{algo}: Mismatch — file may be corrupt!", bootstyle="danger")

    def _copy(self, var: ttk.StringVar):
        _copy(self, var)

    def _on_close(self):
        if self._worker:
            self._worker.stop()
        self.destroy()


# ── StoredHashDialog ──────────────────────────────────────────────────────────

class StoredHashDialog(ttk.Toplevel):
    """
    Shows the last computed hashes from HashLog without recomputing.
    Compares against configured expected values from HashConfig (per partition).
    Use when you want a quick compliance check without re-hashing the image.
    """

    def __init__(self, parent, image_path: str):
        super().__init__(parent)
        self.title("Verify Stored Hash")
        self.resizable(False, False)
        self.grab_set()

        self._path  = image_path
        self._log   = HashLog()
        self._hcfg  = HashConfig()
        self._entry = self._log.get_entry(image_path)

        self._build()

        self.update_idletasks()
        px = parent.winfo_rootx() + (parent.winfo_width()  - self.winfo_width())  // 2
        py = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{px}+{py}")

        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.wait_window()

    def _build(self):
        outer = ttk.Frame(self, padding=16)
        outer.pack(fill=BOTH, expand=YES)
        outer.columnconfigure(1, weight=1)

        fname = os.path.basename(self._path)
        days  = self._log.days_since(self._path)

        ttk.Label(outer, text="File:", anchor=W).grid(row=0, column=0, sticky=W, pady=2)
        ttk.Label(outer, text=fname, bootstyle="info", anchor=W).grid(
            row=0, column=1, columnspan=2, sticky=W, padx=(8, 0), pady=2)

        ttk.Label(outer, text="Last verified:", anchor=W).grid(row=1, column=0, sticky=W, pady=2)
        age_text, age_style = _age_info(days)
        ttk.Label(outer, text=age_text, bootstyle=age_style, anchor=W).grid(
            row=1, column=1, columnspan=2, sticky=W, padx=(8, 0), pady=2)

        ttk.Separator(outer, orient=HORIZONTAL).grid(
            row=2, column=0, columnspan=3, sticky=EW, pady=(8, 4))

        if not self._entry or not self._entry.get("sha256"):
            ttk.Label(outer,
                      text="No stored hash found.\nUse the 'Hash' button to compute one first.",
                      bootstyle="warning", justify=LEFT).grid(
                row=3, column=0, columnspan=3, sticky=W, pady=8)
            ttk.Button(outer, text="Close", command=self.destroy,
                       bootstyle="outline").grid(row=4, column=2, sticky=E, pady=(8, 0))
            return

        sha256 = self._entry.get("sha256", "")
        sha1   = self._entry.get("sha1", "")

        # Stored hash display
        ttk.Label(outer, text="SHA-256:", anchor=W).grid(row=3, column=0, sticky=W, pady=2)
        sha256_var = ttk.StringVar(value=sha256)
        ttk.Entry(outer, textvariable=sha256_var, state=READONLY,
                  bootstyle="secondary").grid(row=3, column=1, sticky=EW, padx=(8, 4), pady=2)
        ttk.Button(outer, text="Copy", width=5, bootstyle="secondary-outline",
                   command=lambda: _copy(self, sha256_var)).grid(row=3, column=2, pady=2)

        ttk.Label(outer, text="SHA-1:", anchor=W).grid(row=4, column=0, sticky=W, pady=2)
        sha1_var = ttk.StringVar(value=sha1)
        ttk.Entry(outer, textvariable=sha1_var, state=READONLY,
                  bootstyle="secondary").grid(row=4, column=1, sticky=EW, padx=(8, 4), pady=2)
        ttk.Button(outer, text="Copy", width=5, bootstyle="secondary-outline",
                   command=lambda: _copy(self, sha1_var)).grid(row=4, column=2, pady=2)

        # Per-partition compliance check
        enabled = self._hcfg.get_enabled_partitions(self._path)
        if enabled:
            ttk.Separator(outer, orient=HORIZONTAL).grid(
                row=5, column=0, columnspan=3, sticky=EW, pady=(8, 4))
            ttk.Label(outer, text="Partition Compliance Check",
                      bootstyle="secondary", anchor=W).grid(
                row=6, column=0, columnspan=3, sticky=W, pady=(0, 4))

            result_row = 7
            all_pass = True
            for part_num in sorted(enabled):
                cfg = enabled[part_num]
                part_label = "Disk" if part_num == 0 else f"Partition {part_num}"
                results = []
                if cfg["sha1_enabled"] and cfg["sha1_value"]:
                    match = sha1.lower() == cfg["sha1_value"].lower()
                    results.append(("SHA-1", match, cfg["sha1_fail"]))
                    if not match:
                        all_pass = False
                if cfg["sha256_enabled"] and cfg["sha256_value"]:
                    match = sha256.lower() == cfg["sha256_value"].lower()
                    results.append(("SHA-256", match, cfg["sha256_fail"]))
                    if not match:
                        all_pass = False

                for algo, match, fail_flag in results:
                    if match:
                        text  = f"{part_label} {algo}: Pass"
                        style = "success"
                    else:
                        fail  = " [FAIL — stop flag set]" if fail_flag else " [mismatch]"
                        text  = f"{part_label} {algo}: MISMATCH{fail}"
                        style = "danger"
                    ttk.Label(outer, text=text, bootstyle=style, anchor=W).grid(
                        row=result_row, column=0, columnspan=3, sticky=W, padx=(8, 0))
                    result_row += 1

            summary_row = result_row
            if all_pass:
                ttk.Label(outer, text="All configured checks passed.",
                          bootstyle="success", anchor=W).grid(
                    row=summary_row, column=0, columnspan=3, sticky=W, pady=(4, 0))
            else:
                ttk.Label(outer, text="One or more checks FAILED.",
                          bootstyle="danger", anchor=W).grid(
                    row=summary_row, column=0, columnspan=3, sticky=W, pady=(4, 0))
            result_row = summary_row + 1
        else:
            result_row = 6
            ttk.Label(outer,
                      text="No partition hashes configured. Use Options → Configure Hashes.",
                      bootstyle="secondary", anchor=W).grid(
                row=5, column=0, columnspan=3, sticky=W, pady=(8, 0))

        ttk.Button(outer, text="Close", command=self.destroy,
                   bootstyle="outline").grid(row=result_row, column=2, sticky=E, pady=(12, 0))


# ── shared helpers ────────────────────────────────────────────────────────────

def _age_info(days: Optional[int]):
    if days is None:
        return "Never", "secondary"
    if days == 0:
        return "Today", "success"
    if days == 1:
        return "Yesterday", "success"
    if days >= 30:
        return f"{days} days ago  —  rehash recommended", "warning"
    return f"{days} days ago", "success"


def _copy(widget, var: ttk.StringVar):
    val = var.get()
    if val and val != "—":
        widget.clipboard_clear()
        widget.clipboard_append(val)


# ── helpers ───────────────────────────────────────────────────────────────────

def _fmt_size(n: int) -> str:
    for unit, thresh in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if n >= thresh:
            return f"{n / thresh:.1f} {unit}"
    return f"{n} B"
