"""
configure_hash_dialog.py
Per-partition hash configuration dialog.
Matches the "Configure Hash Verification" layout.
Used for Missouri Gaming Commission compliance — each partition can have
independent SHA-1 / SHA-256 expected values with enable / fail-on-mismatch flags.
"""

import os
from tkinter import filedialog
from typing import Dict, List, Optional

import ttkbootstrap as ttk

from ttkbootstrap.constants import *

from hash_config import HashConfig, NUM_PARTITIONS, blank_partition
from hash_worker import HashStatus, HashWorker
from partition_reader import PartitionInfo, read_mbr_partitions


class ConfigureHashDialog(ttk.Toplevel):
    """
    Modal dialog for setting expected hash values per partition.
    Each partition (1–5) is independently configured.
    """

    def __init__(self, parent, image_path: str):
        super().__init__(parent)
        self.title("Configure Hash Verification")
        self.resizable(False, False)
        self.grab_set()

        self._parent     = parent
        self._path       = image_path
        self._hcfg       = HashConfig()
        self._worker: Optional[HashWorker] = None
        self._partition  = 1
        # keyed by 1-based partition number
        self._partitions: Dict[int, PartitionInfo] = {}

        self._build()
        self._load_partition(0)
        self._auto_detect_partitions()

        self.update_idletasks()
        px = parent.winfo_rootx() + (parent.winfo_width()  - self.winfo_width())  // 2
        py = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{px}+{py}")

        self.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self.wait_window()

    # ── build ─────────────────────────────────────────────────────────────────

    def _build(self):
        outer = ttk.Frame(self, padding=16)
        outer.pack(fill=BOTH, expand=YES)

        # Image label
        ttk.Label(outer, text="Image:", anchor=W).grid(row=0, column=0, sticky=W, pady=(0, 4))
        ttk.Label(outer, text=os.path.basename(self._path),
                  bootstyle="info", anchor=W).grid(
            row=0, column=1, columnspan=2, sticky=W, padx=(8, 0))

        # Partition selector
        ttk.Label(outer, text="Partition:", anchor=W).grid(
            row=1, column=0, sticky=W, pady=(6, 8))
        self._part_str_var = ttk.StringVar(value="Partition 0")
        self._part_cb = ttk.Combobox(
            outer,
            textvariable=self._part_str_var,
            state="readonly",
            values=["Partition 0"],
            width=16,
        )
        self._part_cb.grid(row=1, column=1, sticky=W, padx=(8, 0))
        self._part_cb.bind("<<ComboboxSelected>>", self._on_partition_change)

        # ── Expected Hash Values ──────────────────────────────────────────────
        hf = ttk.LabelFrame(outer, text="Expected Hash Values")
        hf.grid(row=2, column=0, columnspan=3, sticky=EW, pady=(0, 8))
        hf.columnconfigure(0, weight=1)

        # SHA-1
        ttk.Label(hf, text="SHA-1 (40 hex chars):", anchor=W).grid(
            row=0, column=0, columnspan=3, sticky=W, padx=8, pady=(8, 2))
        self._sha1_var = ttk.StringVar()
        ttk.Entry(hf, textvariable=self._sha1_var).grid(
            row=1, column=0, columnspan=3, sticky=EW, padx=8, pady=(0, 4))

        self._sha1_enable_var = ttk.BooleanVar(value=False)
        self._sha1_fail_var   = ttk.BooleanVar(value=False)
        ttk.Checkbutton(hf, text="Enable", variable=self._sha1_enable_var,
                        bootstyle="round-toggle").grid(row=2, column=0, sticky=W, padx=8)
        ttk.Checkbutton(hf, text="Fail on mismatch", variable=self._sha1_fail_var,
                        bootstyle="round-toggle").grid(row=2, column=1, sticky=W, padx=4)
        self._sha1_status = ttk.Label(hf, text="Not configured",
                                       bootstyle="secondary", anchor=W)
        self._sha1_status.grid(row=2, column=2, sticky=W, padx=(12, 8), pady=(0, 4))

        ttk.Separator(hf, orient=HORIZONTAL).grid(
            row=3, column=0, columnspan=3, sticky=EW, padx=8, pady=4)

        # SHA-256
        ttk.Label(hf, text="SHA-256 (64 hex chars):", anchor=W).grid(
            row=4, column=0, columnspan=3, sticky=W, padx=8, pady=(4, 2))
        self._sha256_var = ttk.StringVar()
        ttk.Entry(hf, textvariable=self._sha256_var).grid(
            row=5, column=0, columnspan=3, sticky=EW, padx=8, pady=(0, 4))

        self._sha256_enable_var = ttk.BooleanVar(value=False)
        self._sha256_fail_var   = ttk.BooleanVar(value=False)
        ttk.Checkbutton(hf, text="Enable", variable=self._sha256_enable_var,
                        bootstyle="round-toggle").grid(row=6, column=0, sticky=W, padx=8)
        ttk.Checkbutton(hf, text="Fail on mismatch", variable=self._sha256_fail_var,
                        bootstyle="round-toggle").grid(row=6, column=1, sticky=W, padx=4)
        self._sha256_status = ttk.Label(hf, text="Not configured",
                                         bootstyle="secondary", anchor=W)
        self._sha256_status.grid(row=6, column=2, sticky=W, padx=(12, 8), pady=(0, 8))

        # Update status labels on any hash entry change
        self._sha1_var.trace_add("write", lambda *_: self._refresh_status())
        self._sha256_var.trace_add("write", lambda *_: self._refresh_status())

        # ── Calculate from Image ──────────────────────────────────────────────
        cf = ttk.LabelFrame(outer, text="Calculate from Image")
        cf.grid(row=3, column=0, columnspan=3, sticky=EW, pady=(0, 12))

        # Partition detection row
        det_row = ttk.Frame(cf)
        det_row.pack(fill=X, padx=8, pady=(8, 2))
        ttk.Button(det_row, text="Detect Partitions",
                   bootstyle="secondary-outline",
                   command=self._detect_partitions).pack(side=LEFT, padx=(0, 8))
        self._part_info_var = ttk.StringVar(value="")
        ttk.Label(det_row, textvariable=self._part_info_var,
                  bootstyle="secondary", anchor=W).pack(side=LEFT, fill=X, expand=YES)

        btn_row = ttk.Frame(cf)
        btn_row.pack(fill=X, padx=8, pady=(4, 8))
        ttk.Button(btn_row, text="Calculate All",
                   command=lambda: self._calculate("both")).pack(side=LEFT, padx=(0, 6))
        ttk.Button(btn_row, text="SHA-1 Only",
                   command=lambda: self._calculate("sha1")).pack(side=LEFT, padx=(0, 6))
        ttk.Button(btn_row, text="SHA-256 Only",
                   command=lambda: self._calculate("sha256")).pack(side=LEFT, padx=(0, 6))
        ttk.Button(btn_row, text="Load from File",
                   command=self._load_from_file).pack(side=LEFT, padx=(0, 6))

        self._calc_pbar_var = ttk.IntVar(value=0)
        ttk.Progressbar(cf, variable=self._calc_pbar_var, maximum=100,
                        bootstyle="info-striped").pack(fill=X, padx=8, pady=(0, 4))
        self._calc_status_var = ttk.StringVar(value="")
        ttk.Label(cf, textvariable=self._calc_status_var,
                  bootstyle="secondary").pack(anchor=W, padx=8, pady=(0, 6))

        # ── Save / Cancel ─────────────────────────────────────────────────────
        btn_frame = ttk.Frame(outer)
        btn_frame.grid(row=4, column=0, columnspan=3, sticky=EW)
        ttk.Button(btn_frame, text="Save", bootstyle="success",
                   command=self._on_save).pack(side=LEFT, padx=(0, 6))
        ttk.Button(btn_frame, text="Cancel", bootstyle="outline",
                   command=self._on_cancel).pack(side=RIGHT)

    # ── partition switching ───────────────────────────────────────────────────

    def _on_partition_change(self, _event=None):
        self._save_current_partition()
        self._partition = int(self._part_str_var.get().split()[-1])
        self._load_partition(self._partition)
        self._refresh_part_info()

    def _load_partition(self, n: int):
        cfg = self._hcfg.get_partition(self._path, n)
        self._sha1_var.set(cfg["sha1_value"])
        self._sha1_enable_var.set(cfg["sha1_enabled"])
        self._sha1_fail_var.set(cfg["sha1_fail"])
        self._sha256_var.set(cfg["sha256_value"])
        self._sha256_enable_var.set(cfg["sha256_enabled"])
        self._sha256_fail_var.set(cfg["sha256_fail"])
        self._refresh_status()

    def _save_current_partition(self):
        cfg = {
            "sha1_value":     self._sha1_var.get().strip().lower(),
            "sha1_enabled":   bool(self._sha1_enable_var.get()),
            "sha1_fail":      bool(self._sha1_fail_var.get()),
            "sha256_value":   self._sha256_var.get().strip().lower(),
            "sha256_enabled": bool(self._sha256_enable_var.get()),
            "sha256_fail":    bool(self._sha256_fail_var.get()),
        }
        self._hcfg.save_partition(self._path, self._partition, cfg)

    def _refresh_status(self):
        sha1 = self._sha1_var.get().strip()
        ok1  = len(sha1) == 40
        self._sha1_status.configure(
            text="Configured" if ok1 else "Not configured",
            bootstyle="success" if ok1 else "secondary")

        sha256 = self._sha256_var.get().strip()
        ok256  = len(sha256) == 64
        self._sha256_status.configure(
            text="Configured" if ok256 else "Not configured",
            bootstyle="success" if ok256 else "secondary")

    # ── partition detection ───────────────────────────────────────────────────

    def _update_partition_dropdown(self):
        """Rebuild combobox to show only Partition 0 + detected partitions."""
        values = ["Partition 0"] + [
            f"Partition {n}" for n in sorted(self._partitions)
        ]
        self._part_cb.configure(values=values)
        if self._part_str_var.get() not in values:
            self._part_str_var.set("Partition 0")
            self._partition = 0

    def _auto_detect_partitions(self):
        """Called on open — silently attempt to read the partition table."""
        found = read_mbr_partitions(self._path)
        self._partitions = {p.number: p for p in found}
        self._update_partition_dropdown()
        self._refresh_part_info()

    def _detect_partitions(self):
        """Manual button — detect and report results."""
        found = read_mbr_partitions(self._path)
        self._partitions = {p.number: p for p in found}
        self._update_partition_dropdown()
        if found:
            self._calc_status_var.set(
                f"Found {len(found)} partition(s): "
                + "  ".join(f"P{p.number}={p.size_str}" for p in found))
        else:
            self._calc_status_var.set(
                "No MBR partition table found — will hash entire file.")
        self._refresh_part_info()

    def _refresh_part_info(self):
        """Update the partition info label for the currently selected partition."""
        p = self._partitions.get(self._partition)
        if p:
            self._part_info_var.set(
                f"Partition {p.number}: {p.summary}  "
                f"(offset {p.offset // (1 << 20):.1f} MB, "
                f"will hash partition only)")
        else:
            self._part_info_var.set(
                "Partition not detected — will hash entire file")

    # ── calculate ─────────────────────────────────────────────────────────────

    def _calculate(self, mode: str):
        if self._worker is not None:
            return
        self._calc_pbar_var.set(0)

        p = self._partitions.get(self._partition)
        if p:
            self._calc_status_var.set(
                f"Computing hash of partition {p.number} ({p.size_str})…")
            self._worker = HashWorker(
                root=self._parent,
                file_path=self._path,
                on_progress=self._on_calc_progress,
                on_done=lambda s, sha256, sha1: self._on_calc_done(s, sha256, sha1, mode),
                offset=p.offset,
                byte_count=p.size,
            )
        else:
            self._calc_status_var.set("Computing hash of entire file…")
            self._worker = HashWorker(
                root=self._parent,
                file_path=self._path,
                on_progress=self._on_calc_progress,
                on_done=lambda s, sha256, sha1: self._on_calc_done(s, sha256, sha1, mode),
            )
        self._worker.start()

    def _on_calc_progress(self, pct: int):
        if self.winfo_exists():
            self._calc_pbar_var.set(pct)

    def _on_calc_done(self, status: HashStatus, sha256: str, sha1: str, mode: str):
        self._worker = None
        if not self.winfo_exists():
            return
        self._calc_pbar_var.set(100)
        if status == HashStatus.DONE:
            if mode in ("both", "sha1"):
                self._sha1_var.set(sha1)
                self._sha1_enable_var.set(True)
            if mode in ("both", "sha256"):
                self._sha256_var.set(sha256)
                self._sha256_enable_var.set(True)
            self._calc_status_var.set("Done — values populated above")
        else:
            self._calc_status_var.set("Failed — check file path")

    def _load_from_file(self):
        path = filedialog.askopenfilename(
            title="Select hash manifest file",
            filetypes=[
                ("Hash files", "*.sha256 *.sha1 *.md5 *.txt *.hash"),
                ("All files", "*.*"),
            ],
            parent=self,
        )
        if not path:
            return
        loaded = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    h = line.split()[0].lower()
                    if len(h) == 64:
                        self._sha256_var.set(h)
                        self._sha256_enable_var.set(True)
                        loaded.append("SHA-256")
                    elif len(h) == 40:
                        self._sha1_var.set(h)
                        self._sha1_enable_var.set(True)
                        loaded.append("SHA-1")
            self._calc_status_var.set(
                f"Loaded: {', '.join(loaded)}" if loaded else "No recognised hashes found in file")
        except OSError as e:
            self._calc_status_var.set(f"Load failed: {e}")

    # ── save / cancel ─────────────────────────────────────────────────────────

    def _on_save(self):
        self._save_current_partition()
        if self._worker:
            self._worker.stop()
        self.destroy()

    def _on_cancel(self):
        if self._worker:
            self._worker.stop()
        self.destroy()
