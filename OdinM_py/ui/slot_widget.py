"""
slot_widget.py
One row representing a single drive slot (up to 5 slots total).
Shows: slot number, drive letter, label/size, status badge, progress bar, start/stop button.
"""

import ttkbootstrap as ttk
from ttkbootstrap.constants import *

from clone_worker import CloneStatus

# Status → (ttkbootstrap bootstyle, label text)
STATUS_STYLE = {
    CloneStatus.IDLE:    ("secondary", "Empty"),
    CloneStatus.RUNNING: ("info",      "Cloning"),
    CloneStatus.DONE:    ("success",   "Done"),
    CloneStatus.FAILED:  ("danger",    "Failed"),
    CloneStatus.STOPPED: ("warning",   "Stopped"),
}


class SlotWidget(ttk.Frame):
    """
    A single drive-slot row.

    Callbacks:
      on_start(slot_index)  — user clicked Start
      on_stop(slot_index)   — user clicked Stop
    """

    def __init__(self, parent, slot_index: int, on_start, on_stop, **kwargs):
        super().__init__(parent, **kwargs)
        self._idx = slot_index
        self._on_start = on_start
        self._on_stop  = on_stop
        self._build()
        self.reset()

    # ── build ─────────────────────────────────────────────────────────────────

    def _build(self):
        self.columnconfigure(2, weight=1)  # drive info column expands

        # Slot number label
        ttk.Label(self, text=f"Slot {self._idx + 1}", width=6,
                  anchor=W).grid(row=0, column=0, padx=(0, 6), pady=4, sticky=W)

        # Status badge
        self._status_lbl = ttk.Label(self, text="Empty", width=9,
                                      bootstyle="secondary-inverse", anchor=CENTER)
        self._status_lbl.grid(row=0, column=1, padx=(0, 8), pady=4)

        # Drive info (letter + label + size)
        self._info_var = ttk.StringVar(value="—")
        ttk.Label(self, textvariable=self._info_var, anchor=W).grid(
            row=0, column=2, padx=(0, 8), pady=4, sticky=EW)

        # Progress bar
        self._progress_var = ttk.IntVar(value=0)
        self._pbar = ttk.Progressbar(self, variable=self._progress_var,
                                      maximum=100, length=160,
                                      bootstyle="info-striped")
        self._pbar.grid(row=0, column=3, padx=(0, 8), pady=4)

        # Percentage label
        self._pct_var = ttk.StringVar(value="")
        ttk.Label(self, textvariable=self._pct_var, width=5,
                  anchor=E).grid(row=0, column=4, padx=(0, 8), pady=4)

        # Start / Stop button
        self._btn = ttk.Button(self, text="Start", width=7,
                                bootstyle="outline",
                                command=self._on_btn_click)
        self._btn.grid(row=0, column=5, pady=4)

    # ── public API ────────────────────────────────────────────────────────────

    def reset(self):
        """Clear slot — no drive present."""
        self._info_var.set("—")
        self._set_status(CloneStatus.IDLE)
        self._progress_var.set(0)
        self._pct_var.set("")
        self._btn.configure(text="Start", state=DISABLED, bootstyle="outline")

    def set_drive(self, display: str):
        """Drive detected — show info, enable Start."""
        self._info_var.set(display)
        self._set_status(CloneStatus.IDLE)
        self._progress_var.set(0)
        self._pct_var.set("0%")
        self._btn.configure(text="Start", state=NORMAL, bootstyle="success-outline")

    def set_progress(self, pct: int):
        self._progress_var.set(pct)
        self._pct_var.set(f"{pct}%")

    def set_status(self, status: CloneStatus):
        self._set_status(status)
        if status == CloneStatus.RUNNING:
            self._btn.configure(text="Stop", state=NORMAL, bootstyle="danger-outline")
        elif status in (CloneStatus.DONE, CloneStatus.FAILED, CloneStatus.STOPPED):
            self._btn.configure(text="Start", state=NORMAL, bootstyle="success-outline")

    # ── private ───────────────────────────────────────────────────────────────

    def _set_status(self, status: CloneStatus):
        style, text = STATUS_STYLE.get(status, ("secondary", "—"))
        self._status_lbl.configure(text=text, bootstyle=f"{style}-inverse")

    def _on_btn_click(self):
        text = self._btn.cget("text")
        if text == "Start":
            self._on_start(self._idx)
        else:
            self._on_stop(self._idx)
