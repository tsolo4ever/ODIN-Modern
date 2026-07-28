"""
main_window.py
Top-level UI layout:
  - Image file bar (path + Browse button)
  - 5 drive slot rows
  - Start All / Stop All controls
  - Scrolled log panel
"""

import os
import tkinter as tk
from collections.abc import Callable
from tkinter import filedialog, messagebox

import ttkbootstrap as ttk
from ttkbootstrap.constants import *
from tkinter.scrolledtext import ScrolledText

from clone_worker import CloneStatus
from config_manager import ENGINE_ODIN, ENGINE_PYIMAGER
from drive_manager import DriveInfo
from ui.slot_widget import SlotWidget

NUM_SLOTS = 5

_ENGINE_DISPLAY = {ENGINE_ODIN: "ODIN (ODINC.exe)", ENGINE_PYIMAGER: "pyimager (built-in)"}
_ENGINE_FROM_DISPLAY = {v: k for k, v in _ENGINE_DISPLAY.items()}


class MainWindow(ttk.Frame):
    """
    Wired by app.py. Exposes:
      on_start_slot(idx)    — override to launch clone
      on_stop_slot(idx)     — override to stop clone
      on_start_all()        — override to start all ready slots
      on_stop_all()         — override to stop all running slots
      image_path property   — current selected image file
    """

    def __init__(self, parent, config, **kwargs):
        super().__init__(parent, padding=12, **kwargs)
        self._config = config
        self._root_win = parent  # root ttk.Window — needed for the menu bar
        self._slots: list[SlotWidget] = []
        # slot index -> disk_number currently shown there (None if empty).
        # Lets update_drives() skip slots whose occupant hasn't actually
        # changed, so a blip in an UNRELATED slot doesn't wipe this one's
        # live progress/speed/eta (SlotWidget.set_drive() resets all of that).
        self._displayed_disk_nums: dict[int, int | None] = {}
        self._image_var = ttk.StringVar(value=config.get_last_image())
        self._init_callbacks()
        self._build()

    # ── callbacks — wired by app.py ──────────────────────────────────────────

    on_start_slot: Callable[[int], None]
    on_stop_slot: Callable[[int], None]
    on_confirm_slot: Callable[[int], None]
    on_start_all: Callable[[], None]
    on_stop_all: Callable[[], None]
    on_refresh_disks: Callable[[], None]
    on_verify_image: Callable[[], None]
    on_configure_hashes: Callable[[], None]
    on_verify_stored: Callable[[], None]
    on_make_image: Callable[[], None]
    on_flash_widget_toggle: Callable[[bool], None]

    def _init_callbacks(self):
        self.on_start_slot = lambda idx: None
        self.on_stop_slot = lambda idx: None
        self.on_confirm_slot = lambda idx: None
        self.on_start_all = lambda: None
        self.on_stop_all = lambda: None
        self.on_refresh_disks = lambda: None
        self.on_verify_image = lambda: None
        self.on_configure_hashes = lambda: None
        self.on_verify_stored = lambda: None
        self.on_make_image = lambda: None
        self.on_flash_widget_toggle = lambda enabled: None

    # ── public API ────────────────────────────────────────────────────────────

    @property
    def image_path(self) -> str:
        return self._image_var.get().strip()

    def update_drives(self, drives: list, locked: dict):
        """Called by app.py whenever its slot->drive mapping changes.

        `drives` is indexed BY SLOT (length NUM_SLOTS, None for an empty
        slot) - the same sticky mapping app.py uses for launching/verifying,
        never a repacked list of only currently-present drives. A slot's
        widget is only touched (set_drive/reset/set_awaiting_confirm) when
        its disk_number actually changes since the last call - otherwise a
        blip affecting some OTHER slot would re-render this one and wipe its
        live progress/speed/eta for no reason.

        `locked` is app.py's slot_index -> confirmed disk_number map. A
        newly-appearing disk that matches its slot's locked entry (a
        persisted confirmation within the 15-minute grace after removal)
        goes straight to the normal ready state; otherwise it shows the
        "Confirm" prompt and waits for the operator.
        """
        occupied = max((i for i, d in enumerate(drives) if d is not None), default=-1)
        # Keep one empty slot visible beyond the highest occupied one (min 1, max NUM_SLOTS)
        target = max(1, min(NUM_SLOTS, occupied + 2))
        while len(self._slots) < target:
            idx = len(self._slots)
            sw = SlotWidget(
                self._slots_frame,
                slot_index=idx,
                on_start=lambda i: self.on_start_slot(i),
                on_stop=lambda i: self.on_stop_slot(i),
                on_confirm=lambda i: self.on_confirm_slot(i),
            )
            sw.grid(row=idx, column=0, sticky=EW, padx=4, pady=2)
            self._slots.append(sw)

        for i, slot in enumerate(self._slots):
            d = drives[i] if i < len(drives) else None
            disk_num = d.disk_number if d is not None else None
            if self._displayed_disk_nums.get(i) == disk_num:
                continue  # unchanged - don't disturb live progress/speed/eta
            self._displayed_disk_nums[i] = disk_num
            if d is None:
                slot.reset()
            elif locked.get(i) == disk_num:
                slot.set_drive(d.display)
            else:
                slot.set_awaiting_confirm(d.display)

    def confirm_slot(self, idx: int):
        """Called directly by app.py once a Confirm click is validated and
        the lock recorded - an explicit one-off transition, not something
        update_drives()'s disk_number diff would ever trigger on its own
        since the slot's occupant doesn't change when it gets confirmed."""
        self._slots[idx].confirm()

    def set_slot_progress(self, idx: int, pct: int):
        self._slots[idx].set_progress(pct)

    def set_slot_status(self, idx: int, status: CloneStatus):
        self._slots[idx].set_status(status)

    def set_slot_ready(self, idx: int, display: str):
        """Reset a slot to ready state (drive present, Start enabled)."""
        self._slots[idx].set_drive(display)

    def set_slot_speed(self, idx: int, speed_str: str):
        self._slots[idx].set_speed(speed_str)

    def set_slot_eta(self, idx: int, eta_str: str):
        self._slots[idx].set_eta(eta_str)

    def log(self, text: str):
        self._log_box.configure(state=NORMAL)  # type: ignore[arg-type]
        self._log_box.insert(END, text + "\n")
        self._log_box.see(END)
        self._log_box.configure(state=DISABLED)  # type: ignore[arg-type]

    def clear_log(self):
        self._log_box.configure(state=NORMAL)  # type: ignore[arg-type]
        self._log_box.delete("1.0", END)
        self._log_box.configure(state=DISABLED)  # type: ignore[arg-type]

    # ── build ─────────────────────────────────────────────────────────────────

    def _build(self):
        self.pack(fill=BOTH, expand=YES)
        self.columnconfigure(0, weight=1)

        self._build_menu()
        self._build_image_bar()
        self._build_slots()
        self._build_controls()
        self._build_settings()
        self._build_log()

    def _build_menu(self):
        menubar = tk.Menu(self._root_win)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Exit", command=self._root_win.destroy)
        menubar.add_cascade(label="File", menu=file_menu)

        options_menu = tk.Menu(menubar, tearoff=0)
        options_menu.add_command(label="Make Image…", command=lambda: self.on_make_image())
        options_menu.add_separator()
        options_menu.add_command(label="ODINC Path…", command=self._show_odinc_settings)
        options_menu.add_command(
            label="Configure Hashes…", command=lambda: self.on_configure_hashes()
        )
        options_menu.add_separator()
        options_menu.add_command(
            label="Verify Stored Hash…", command=lambda: self.on_verify_stored()
        )
        options_menu.add_separator()

        # Max Concurrent submenu
        self._max_concurrent_var = tk.IntVar(value=self._config.get_max_concurrent())
        conc_menu = tk.Menu(options_menu, tearoff=0)
        for n in range(1, 6):
            conc_menu.add_radiobutton(
                label=str(n),
                variable=self._max_concurrent_var,
                value=n,
                command=lambda v=n: self._config.set_max_concurrent(v),  # type: ignore[misc]
            )
        options_menu.add_cascade(label="Max Concurrent", menu=conc_menu)

        # Max Disks submenu - how many removable disks get considered per
        # refresh at all (separate from Max Concurrent, which caps active
        # flashes). 0 = no cap.
        self._max_disks_var = tk.IntVar(value=self._config.get_max_disks())
        disks_menu = tk.Menu(options_menu, tearoff=0)
        for n in [0, 1, 2, 3, 4, 5, 6, 8, 10]:
            disks_menu.add_radiobutton(
                label="No Cap" if n == 0 else str(n),
                variable=self._max_disks_var,
                value=n,
                command=lambda v=n: self._config.set_max_disks(v),  # type: ignore[misc]
            )
        options_menu.add_cascade(label="Max Disks", menu=disks_menu)
        options_menu.add_separator()

        options_menu.add_command(label="Theme: Dark", command=lambda: self._set_theme("darkly"))
        options_menu.add_command(label="Theme: Light", command=lambda: self._set_theme("flatly"))
        menubar.add_cascade(label="Options", menu=options_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="About OdinM_py", command=self._show_about)
        menubar.add_cascade(label="Help", menu=help_menu)

        self._root_win.configure(menu=menubar)

    def _build_image_bar(self):
        frame = ttk.LabelFrame(self, text="Image File")  # type: ignore[attr-defined]
        frame.grid(row=0, column=0, sticky=EW, pady=(0, 8))
        frame.columnconfigure(0, weight=1)

        ttk.Entry(frame, textvariable=self._image_var).grid(
            row=0, column=0, sticky=EW, padx=6, pady=4
        )
        ttk.Button(frame, text="Browse…", width=9, command=self._browse_image).grid(
            row=0, column=1, padx=(0, 6), pady=4
        )
        ttk.Button(frame, text="Hash…", width=8, command=lambda: self.on_verify_image()).grid(
            row=0, column=2, padx=(0, 6), pady=4
        )

        self._img_size_var = ttk.StringVar(value="")
        ttk.Label(
            frame, textvariable=self._img_size_var, width=10, bootstyle="secondary", anchor=W
        ).grid(row=0, column=3, padx=(0, 6), pady=4)
        self._refresh_image_size()

    def _build_slots(self):
        frame = ttk.LabelFrame(self, text="Drive Slots")  # type: ignore[attr-defined]
        frame.grid(row=1, column=0, sticky=EW, pady=(0, 8))
        frame.columnconfigure(0, weight=1)
        self._slots_frame = frame

        # Only Slot 1 (index 0) is visible at startup; more appear as drives arrive
        sw = SlotWidget(
            frame,
            slot_index=0,
            on_start=lambda idx: self.on_start_slot(idx),
            on_stop=lambda idx: self.on_stop_slot(idx),
            on_confirm=lambda idx: self.on_confirm_slot(idx),
        )
        sw.grid(row=0, column=0, sticky=EW, padx=4, pady=2)
        self._slots.append(sw)

    def _build_controls(self):
        frame = ttk.Frame(self)
        frame.grid(row=2, column=0, sticky=EW, pady=(0, 8))

        ttk.Button(
            frame, text="Start All", bootstyle="success", command=lambda: self.on_start_all()
        ).pack(side=LEFT, padx=(0, 6))
        ttk.Button(
            frame, text="Stop All", bootstyle="danger", command=lambda: self.on_stop_all()
        ).pack(side=LEFT, padx=(0, 6))
        ttk.Button(
            frame, text="Refresh Disks", bootstyle="warning-outline",
            command=lambda: self.on_refresh_disks(),
        ).pack(side=LEFT, padx=(0, 6))

        ttk.Separator(frame, orient=VERTICAL).pack(side=LEFT, fill=Y, padx=(0, 12), pady=2)

        ttk.Label(frame, text="Flash engine:").pack(side=LEFT, padx=(0, 6))
        self._engine_var = ttk.StringVar(value=_ENGINE_DISPLAY[self._config.get_engine()])
        engine_cb = ttk.Combobox(
            frame,
            textvariable=self._engine_var,
            state="readonly",
            values=list(_ENGINE_DISPLAY.values()),
            width=20,
        )
        engine_cb.pack(side=LEFT, padx=(0, 6))
        engine_cb.bind("<<ComboboxSelected>>", self._on_engine_change)

        ttk.Button(
            frame, text="Clear Log", bootstyle="secondary-outline", command=self.clear_log
        ).pack(side=RIGHT)

    def _build_settings(self):
        frame = ttk.LabelFrame(self, text="Settings")  # type: ignore[attr-defined]
        frame.grid(row=3, column=0, sticky=EW, pady=(0, 4))

        self._auto_clone_var = ttk.BooleanVar(value=self._config.get_auto_clone())
        ttk.Checkbutton(
            frame,
            text="Auto-clone on device insertion",
            variable=self._auto_clone_var,
            bootstyle="round-toggle",
            command=lambda: self._config.set_auto_clone(bool(self._auto_clone_var.get())),
        ).pack(side=LEFT, padx=(8, 12), pady=6)

        self._verify_after_var = ttk.BooleanVar(value=self._config.get_verify_after_clone())
        ttk.Checkbutton(
            frame,
            text="Verify target disk hash after clone",
            variable=self._verify_after_var,
            bootstyle="round-toggle",
            command=lambda: self._config.set_verify_after_clone(bool(self._verify_after_var.get())),
        ).pack(side=LEFT, padx=(0, 12), pady=6)

        self._stop_on_fail_var = ttk.BooleanVar(value=self._config.get_stop_on_verify_fail())
        ttk.Checkbutton(
            frame,
            text="Stop all on verification failure",
            variable=self._stop_on_fail_var,
            bootstyle="round-toggle",
            command=lambda: self._config.set_stop_on_verify_fail(
                bool(self._stop_on_fail_var.get())
            ),
        ).pack(side=LEFT, padx=(0, 12), pady=6)

        self._flash_widget_var = ttk.BooleanVar(value=self._config.get_show_flash_widget())
        ttk.Checkbutton(
            frame,
            text="Show flash widget",
            variable=self._flash_widget_var,
            bootstyle="round-toggle",
            command=self._toggle_flash_widget,
        ).pack(side=LEFT, padx=(0, 12), pady=6)

    def _build_log(self):
        frame = ttk.LabelFrame(self, text="Log")  # type: ignore[attr-defined]
        frame.grid(row=4, column=0, sticky=NSEW, pady=(0, 4))
        self.rowconfigure(4, weight=1)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        self._log_box = ScrolledText(frame, height=10, state=tk.DISABLED, wrap=tk.WORD)
        self._log_box.grid(row=0, column=0, sticky=NSEW)

    # ── private ───────────────────────────────────────────────────────────────

    def _show_odinc_settings(self):
        dlg = ttk.Toplevel(self._root_win)
        dlg.title("ODINC Path")
        dlg.resizable(False, False)
        dlg.grab_set()

        frame = ttk.Frame(dlg, padding=16)
        frame.pack(fill=BOTH, expand=YES)

        ttk.Label(frame, text="ODINC.exe path:").grid(row=0, column=0, sticky=W, pady=(0, 4))
        path_var = ttk.StringVar(value=self._config.get_odinc_path())
        ttk.Entry(frame, textvariable=path_var, width=50).grid(
            row=1, column=0, sticky=EW, padx=(0, 6)
        )

        def browse():
            p = filedialog.askopenfilename(
                title="Select ODINC.exe",
                filetypes=[("Executable", "*.exe"), ("All files", "*.*")],
            )
            if p:
                path_var.set(p)

        ttk.Button(frame, text="Browse…", command=browse).grid(row=1, column=1)

        def save():
            self._config.set_odinc_path(path_var.get().strip())
            dlg.destroy()

        btn_f = ttk.Frame(frame)
        btn_f.grid(row=2, column=0, columnspan=2, sticky=EW, pady=(12, 0))
        ttk.Button(btn_f, text="Save", bootstyle="success", command=save).pack(side=LEFT)
        ttk.Button(btn_f, text="Cancel", command=dlg.destroy).pack(side=RIGHT)

        dlg.wait_window()

    def _toggle_flash_widget(self):
        enabled = bool(self._flash_widget_var.get())
        self._config.set_show_flash_widget(enabled)
        self.on_flash_widget_toggle(enabled)

    def _on_engine_change(self, _event=None):
        value = _ENGINE_FROM_DISPLAY.get(self._engine_var.get(), ENGINE_ODIN)
        self._config.set_engine(value)

    def _set_theme(self, theme: str):
        self._config.set_theme(theme)
        try:
            self._root_win.style.theme_use(theme)
        except Exception:
            pass

    def _show_about(self):
        messagebox.showinfo(
            "About OdinM_py",
            "OdinM_py v2\nPython UI for multi-drive cloning (ODIN or the "
            "built-in pyimager engine).\n\nBuilt with ttkbootstrap.",
            parent=self._root_win,
        )

    def _browse_image(self):
        initial = self._image_var.get()
        initial_dir = os.path.dirname(initial) if initial else ""
        path = filedialog.askopenfilename(
            title="Select image file",
            initialdir=initial_dir or None,
            filetypes=[
                ("Disk image", "*.img *.odin *.bin *.img.gz *.gz"),
                ("ODIN image", "*.img *.odin *.bin"),
                ("Gzipped image", "*.img.gz *.gz"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        if not self._validate_image(path):
            return
        self._image_var.set(path)
        self._config.set_last_image(path)
        self._refresh_image_size()

    def _validate_image(self, path: str) -> bool:
        """Confirm the file really holds a disk image before accepting it.

        A .gz is only useful if there is an actual image inside, and an ODIN
        header whose capture was aborted looks fine by size alone - both are
        worth catching here rather than at flash time.
        """
        try:
            import sys
            from pathlib import Path

            scripts = Path(__file__).resolve().parent.parent / "scripts"
            if str(scripts) not in sys.path:
                sys.path.insert(0, str(scripts))
            from pyimager import validate_image_file
        except ImportError:
            return True  # validator unavailable - don't block the user

        result = validate_image_file(path)
        if result["ok"]:
            return True
        return messagebox.askyesno(
            "Image may not be usable",
            f"{os.path.basename(path)}\n\n{result['reason']}\n\n"
            "Use it anyway?",
            parent=self._root_win,
            icon="warning",
        )

    def _refresh_image_size(self):
        path = self._image_var.get().strip()
        if path and os.path.isfile(path):
            n = os.path.getsize(path)
            for unit, thresh in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
                if n >= thresh:
                    self._img_size_var.set(f"{n / thresh:.1f} {unit}")
                    return
            self._img_size_var.set(f"{n} B")
        else:
            self._img_size_var.set("")
