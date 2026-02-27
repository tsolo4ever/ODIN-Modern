"""
image_options_dialog.py
Popup dialog for ODINC backup flags: disk mode, compression, file splitting.
"""

from typing import List, Optional

import ttkbootstrap as ttk
from ttkbootstrap.constants import *


_DISK_MODES = [
    ("All blocks",        "-allBlocks"),
    ("Used blocks only",  "-usedBlocks"),
]

_COMPRESSIONS = [
    ("No compression",        "none"),
    ("GZip (zlib)",           "gzip"),
    ("LZ4 (fast)",            "lz4"),
    ("LZ4 High Compression",  "lz4hc"),
    ("Zstandard (zstd)",      "zstd"),
]

print("Using ttk from:", ttk.__file__)

def _flags_to_state(flags: List[str]) -> dict:
    state = {"disk_mode": "-allBlocks", "compression": "none", "split_mb": 0}
    for f in flags:
        if f in ("-allBlocks", "-usedBlocks"):
            state["disk_mode"] = f
        elif f.startswith("-compression="):
            state["compression"] = f.split("=", 1)[1]
        elif f.startswith("-split="):
            try:
                state["split_mb"] = int(f.split("=", 1)[1])
            except ValueError:
                pass
    return state


class ImageOptionsDialog(ttk.Toplevel):
    """
    Modal dialog for ODINC backup flags.
    self.result is the flag list after OK, or None if cancelled.
    """

    def __init__(self, parent, current_flags: List[str]):
        super().__init__(parent)
        self.title("Backup Options")
        self.resizable(False, False)
        self.grab_set()

        self.result: Optional[List[str]] = None
        self._state = _flags_to_state(current_flags)

        self._build()

        self.update_idletasks()
        px = parent.winfo_rootx() + (parent.winfo_width()  - self.winfo_width())  // 2
        py = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{px}+{py}")

        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.wait_window()

    # ── build ─────────────────────────────────────────────────────────────────

    def _build(self):
        outer = ttk.Frame(self, padding=16)
        outer.pack(fill=BOTH, expand=YES)

        # Disk Mode
        dm_frame = ttk.LabelFrame(outer, text="Disk Mode")
        dm_frame.pack(fill=X, pady=(0, 8), ipadx=8, ipady=4)
        self._disk_var = ttk.StringVar(value=self._state["disk_mode"])
        for label, value in _DISK_MODES:
            ttk.Radiobutton(
                dm_frame, text=label,
                variable=self._disk_var, value=value,
            ).pack(anchor=W, pady=1)

        # Compression
        comp_frame = ttk.LabelFrame(outer, text="Compression")
        comp_frame.pack(fill=X, pady=(0, 8), ipadx=8, ipady=4)
        self._comp_var = ttk.StringVar(value=self._state["compression"])
        for label, value in _COMPRESSIONS:
            ttk.Radiobutton(
                comp_frame, text=label,
                variable=self._comp_var, value=value,
            ).pack(anchor=W, pady=1)

        # File Splitting
        split_frame = ttk.LabelFrame(outer, text="File Splitting")
        split_frame.pack(fill=X, pady=(0, 12), ipadx=8, ipady=4)

        self._split_var = ttk.IntVar(value=0 if self._state["split_mb"] == 0 else 1)
        ttk.Radiobutton(
            split_frame, text="Single file",
            variable=self._split_var, value=0,
            command=self._on_split_toggle,
        ).pack(anchor=W, pady=1)

        split_row = ttk.Frame(split_frame)
        split_row.pack(anchor=W, pady=1)
        ttk.Radiobutton(
            split_row, text="Split into chunks",
            variable=self._split_var, value=1,
            command=self._on_split_toggle,
        ).pack(side=LEFT)
        self._chunk_var = ttk.IntVar(value=max(self._state["split_mb"], 700))
        self._chunk_spin = ttk.Spinbox(
            split_row, textvariable=self._chunk_var,
            from_=100, to=99999, increment=100, width=7,
        )
        self._chunk_spin.pack(side=LEFT, padx=(6, 4))
        ttk.Label(split_row, text="MB").pack(side=LEFT)

        self._on_split_toggle()

        # Buttons
        btn_frame = ttk.Frame(outer)
        btn_frame.pack(fill=X)
        ttk.Button(btn_frame, text="OK", bootstyle="success",
                   command=self._ok).pack(side=LEFT, padx=(0, 6))
        ttk.Button(btn_frame, text="Cancel", bootstyle="outline",
                   command=self._cancel).pack(side=RIGHT)

    # ── helpers ───────────────────────────────────────────────────────────────

    def _on_split_toggle(self):
        self._chunk_spin.configure(
            state=NORMAL if self._split_var.get() == 1 else DISABLED)

    def _ok(self):
        flags = [
            self._disk_var.get(),
            f"-compression={self._comp_var.get()}",
        ]
        if self._split_var.get() == 1:
            flags.append(f"-split={max(int(self._chunk_var.get()), 100)}")
        self.result = flags
        self.destroy()

    def _cancel(self):
        self.destroy()
