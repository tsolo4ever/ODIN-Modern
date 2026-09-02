"""
flash_status_window.py
Always-on-top overlay that mirrors the main window's drive slot section.
Shows the same status/progress/speed/ETA data — no separate data path needed.
"""

import ctypes
import sys
from collections.abc import Callable

import ttkbootstrap as ttk
from ttkbootstrap.constants import *

from clone_worker import CloneStatus

_GWL_EXSTYLE = -20
_WS_EX_LAYERED = 0x00080000
_WS_EX_TRANSPARENT = 0x00000020
_LWA_ALPHA = 0x00000002
_RDW_INVALIDATE = 0x0001
_RDW_ERASE = 0x0004
_RDW_ALLCHILDREN = 0x0080
_RDW_UPDATENOW = 0x0100
_GA_ROOT = 2


def _set_alpha(hwnd: int, alpha: float) -> None:
    """Set window alpha via Win32 directly — avoids tkinter recreating the window.
    Uses GetAncestor(GA_ROOT) to target the outer frame HWND, not the inner content HWND."""
    u32 = ctypes.windll.user32
    root = u32.GetAncestor(hwnd, _GA_ROOT) or hwnd
    style = u32.GetWindowLongW(root, _GWL_EXSTYLE)
    u32.SetWindowLongW(root, _GWL_EXSTYLE, style | _WS_EX_LAYERED)
    u32.SetLayeredWindowAttributes(root, 0, int(alpha * 255), _LWA_ALPHA)


_STATUS_STYLE = {
    CloneStatus.IDLE: ("secondary", "Empty"),
    CloneStatus.QUEUED: ("warning", "Queued"),
    CloneStatus.RUNNING: ("info", "Cloning"),
    CloneStatus.DONE: ("success", "Done"),
    CloneStatus.FAILED: ("danger", "Failed"),
    CloneStatus.STOPPED: ("warning", "Stopped"),
}


def _set_click_through(hwnd: int, enable: bool) -> None:
    u32 = ctypes.windll.user32
    style = u32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
    if enable:
        u32.SetWindowLongW(hwnd, _GWL_EXSTYLE, style | _WS_EX_LAYERED | _WS_EX_TRANSPARENT)
    else:
        u32.SetWindowLongW(hwnd, _GWL_EXSTYLE, style & ~_WS_EX_TRANSPARENT)


class FlashStatusWindow(ttk.Toplevel):
    def __init__(
        self,
        parent,
        on_pulled: Callable[[int], None],
        on_lock_change: Callable[[], None] | None = None,
    ):
        super().__init__(parent)
        self.title("Auto Flash Status")
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self._on_pulled = on_pulled
        self._on_lock_change = on_lock_change
        self._rows: list[dict] = []
        self._locked = False
        self._build()
        self.protocol("WM_DELETE_WINDOW", self.withdraw)

    # ── public mirror API — call these exactly like the MainWindow equivalents ──

    def set_drive(self, idx: int, display: str):
        self._ensure_row(idx)
        row = self._rows[idx]
        row["drive"].configure(text=display or "—")
        self._set_status(idx, CloneStatus.IDLE)
        row["pct"].configure(text="")
        row["speed"].configure(text="")
        row["eta"].configure(text="")
        row["button"].configure(state=DISABLED)
        self._show_row(idx)

    def reset(self, idx: int):
        self._ensure_row(idx)
        row = self._rows[idx]
        row["drive"].configure(text="—")
        self._set_status(idx, CloneStatus.IDLE)
        row["pct"].configure(text="")
        row["speed"].configure(text="")
        row["eta"].configure(text="")
        row["button"].configure(state=DISABLED)
        self._hide_row(idx)

    def set_status(self, idx: int, status: CloneStatus):
        self._ensure_row(idx)
        self._set_status(idx, status)
        row = self._rows[idx]
        can_pull = status == CloneStatus.DONE
        row["button"].configure(state=NORMAL if can_pull else DISABLED)

    def set_progress(self, idx: int, pct: int):
        self._ensure_row(idx)
        self._rows[idx]["pct"].configure(text=f"{pct}%")

    def set_phase(self, idx: int, phase: str):
        self._ensure_row(idx)
        labels = {
            "preflight": "Preflight",
            "source_check": "Source Check",
            "write": "Writing",
            "verify": "Verifying",
            "policy": "Part Hash",
        }
        self._rows[idx]["status"].configure(
            text=labels.get(phase, phase.title()[:9]), bootstyle="info-inverse"
        )

    def set_speed(self, idx: int, speed_str: str):
        self._ensure_row(idx)
        self._rows[idx]["speed"].configure(text=speed_str)

    def set_eta(self, idx: int, eta_str: str):
        self._ensure_row(idx)
        self._rows[idx]["eta"].configure(text=eta_str)

    # ── lock toggle ───────────────────────────────────────────────────────────

    def _toggle_lock(self, _event=None) -> None:
        self._locked = not self._locked
        hwnd = self.winfo_id()
        if self._locked:
            self._lock_btn.configure(text="Unlock")
            self.title("Auto Flash Status [LOCKED — Esc to unlock]")
            self.bind("<Escape>", self._toggle_lock)
            self.update_idletasks()
            if sys.platform == "win32":
                _set_alpha(hwnd, 0.6)
        else:
            if sys.platform == "win32":
                _set_alpha(hwnd, 1.0)
            self._lock_btn.configure(text="Lock")
            self.title("Auto Flash Status")
            self.unbind("<Escape>")
        if self._on_lock_change:
            self.after(0, self._on_lock_change)
        if self._locked:
            self._repaint_loop()

    def _repaint_loop(self):
        if not self._locked or not self.winfo_exists():
            return
        if sys.platform == "win32":
            ctypes.windll.user32.RedrawWindow(
                self.winfo_id(), None, None,
                _RDW_INVALIDATE | _RDW_ERASE | _RDW_ALLCHILDREN | _RDW_UPDATENOW,
            )
        self.after(100, self._repaint_loop)

    # ── build ─────────────────────────────────────────────────────────────────

    def _build(self):
        outer = ttk.Frame(self, padding=10)
        outer.pack(fill=BOTH, expand=YES)

        # Header row with column labels + Lock button
        hdr = ttk.Frame(outer)
        hdr.grid(row=0, column=0, columnspan=7, sticky=EW, pady=(0, 4))
        ttk.Label(hdr, text="Slot", width=5).pack(side=LEFT)
        ttk.Label(hdr, text="Status", width=9).pack(side=LEFT, padx=(0, 6))
        ttk.Label(hdr, text="Drive", width=28).pack(side=LEFT, padx=(0, 6))
        ttk.Label(hdr, text="%", width=5).pack(side=LEFT, padx=(0, 4))
        ttk.Label(hdr, text="Speed", width=9).pack(side=LEFT, padx=(0, 4))
        ttk.Label(hdr, text="ETA", width=8).pack(side=LEFT, padx=(0, 4))
        self._lock_btn = ttk.Button(
            hdr,
            text="Lock",
            width=7,
            bootstyle="light-outline",
            command=self._toggle_lock,
        )
        self._lock_btn.pack(side=RIGHT)

        self._body = outer

    def _ensure_row(self, idx: int):
        while len(self._rows) <= idx:
            i = len(self._rows)
            gr = i + 1

            slot_lbl = ttk.Label(self._body, text=f"Slot {i + 1}", width=5, anchor=W)
            status_lbl = ttk.Label(
                self._body,
                text="Empty",
                width=9,
                bootstyle="secondary-inverse",
                anchor=CENTER,
            )
            drive_lbl = ttk.Label(self._body, text="—", width=28, anchor=W)
            pct_lbl = ttk.Label(self._body, text="", width=5, anchor=E)
            speed_lbl = ttk.Label(self._body, text="", width=9, anchor=W)
            eta_lbl = ttk.Label(self._body, text="", width=8, anchor=W)
            btn = ttk.Button(
                self._body,
                text="Pulled",
                width=7,
                bootstyle="success",
                command=lambda i=i: self._on_pulled(i),
                state=DISABLED,
            )

            slot_lbl.grid(row=gr, column=0, sticky=W, pady=2)
            status_lbl.grid(row=gr, column=1, sticky=W, padx=(2, 6), pady=2)
            drive_lbl.grid(row=gr, column=2, sticky=W, padx=(0, 6), pady=2)
            pct_lbl.grid(row=gr, column=3, sticky=E, padx=(0, 4), pady=2)
            speed_lbl.grid(row=gr, column=4, sticky=W, padx=(0, 4), pady=2)
            eta_lbl.grid(row=gr, column=5, sticky=W, padx=(0, 6), pady=2)
            btn.grid(row=gr, column=6, sticky=W, pady=2)

            self._rows.append(
                {
                    "slot": slot_lbl,
                    "status": status_lbl,
                    "drive": drive_lbl,
                    "pct": pct_lbl,
                    "speed": speed_lbl,
                    "eta": eta_lbl,
                    "button": btn,
                }
            )
            self._hide_row(i)

    def _show_row(self, idx: int):
        row = self._rows[idx]
        gr = idx + 1
        row["slot"].grid(row=gr, column=0, sticky=W, pady=2)
        row["status"].grid(row=gr, column=1, sticky=W, padx=(2, 6), pady=2)
        row["drive"].grid(row=gr, column=2, sticky=W, padx=(0, 6), pady=2)
        row["pct"].grid(row=gr, column=3, sticky=E, padx=(0, 4), pady=2)
        row["speed"].grid(row=gr, column=4, sticky=W, padx=(0, 4), pady=2)
        row["eta"].grid(row=gr, column=5, sticky=W, padx=(0, 6), pady=2)
        row["button"].grid(row=gr, column=6, sticky=W, pady=2)

    def _hide_row(self, idx: int):
        row = self._rows[idx]
        for widget in row.values():
            widget.grid_remove()

    def _set_status(self, idx: int, status: CloneStatus):
        style, text = _STATUS_STYLE.get(status, ("secondary", "—"))
        self._rows[idx]["status"].configure(text=text, bootstyle=f"{style}-inverse")
