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
from tkinter import filedialog, messagebox
from typing import Callable, List, Optional

import ttkbootstrap as ttk
from ttkbootstrap.constants import *
from tkinter.scrolledtext import ScrolledText

from clone_worker import CloneStatus
from drive_manager import DriveInfo
from ui.slot_widget import SlotWidget

NUM_SLOTS = 5


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
        self._config   = config
        self._root_win = parent   # root ttk.Window — needed for the menu bar
        self._slots: List[SlotWidget] = []
        self._image_var = ttk.StringVar(value=config.get_last_image())
        self._build()

    # ── callbacks to be overridden by app.py ─────────────────────────────────

    def on_start_slot(self, idx: int):  pass
    def on_stop_slot(self, idx: int):   pass
    def on_start_all(self):             pass
    def on_stop_all(self):              pass
    def on_verify_image(self):          pass
    def on_configure_hashes(self):      pass
    def on_verify_stored(self):         pass
    def on_make_image(self):            pass

    # ── public API ────────────────────────────────────────────────────────────

    @property
    def image_path(self) -> str:
        return self._image_var.get().strip()

    def update_drives(self, drives: List[DriveInfo]):
        """Called by DriveMonitor when the drive list changes."""
        # Map drives to slots in order
        for i, slot in enumerate(self._slots):
            if i < len(drives):
                slot.set_drive(drives[i].display)
            else:
                slot.reset()

    def set_slot_progress(self, idx: int, pct: int):
        self._slots[idx].set_progress(pct)

    def set_slot_status(self, idx: int, status: CloneStatus):
        self._slots[idx].set_status(status)

    def log(self, text: str):
        self._log_box.configure(state=NORMAL)
        self._log_box.insert(END, text + "\n")
        self._log_box.see(END)
        self._log_box.configure(state=DISABLED)

    def clear_log(self):
        self._log_box.configure(state=NORMAL)
        self._log_box.delete("1.0", END)
        self._log_box.configure(state=DISABLED)

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
        options_menu.add_command(label="Make Image…",
                                  command=lambda: self.on_make_image())
        options_menu.add_separator()
        options_menu.add_command(label="ODINC Path…", command=self._show_odinc_settings)
        options_menu.add_command(label="Configure Hashes…",
                                  command=lambda: self.on_configure_hashes())
        options_menu.add_separator()
        options_menu.add_command(label="Verify Stored Hash…",
                                  command=lambda: self.on_verify_stored())
        options_menu.add_separator()

        # Max Concurrent submenu
        self._max_concurrent_var = tk.IntVar(value=self._config.get_max_concurrent())
        conc_menu = tk.Menu(options_menu, tearoff=0)
        for n in range(1, 6):
            conc_menu.add_radiobutton(
                label=str(n),
                variable=self._max_concurrent_var,
                value=n,
                command=lambda v=n: self._config.set_max_concurrent(v),
            )
        options_menu.add_cascade(label="Max Concurrent", menu=conc_menu)
        options_menu.add_separator()

        options_menu.add_command(label="Theme: Dark",  command=lambda: self._set_theme("darkly"))
        options_menu.add_command(label="Theme: Light", command=lambda: self._set_theme("flatly"))
        menubar.add_cascade(label="Options", menu=options_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="About OdinM_py", command=self._show_about)
        menubar.add_cascade(label="Help", menu=help_menu)

        self._root_win.configure(menu=menubar)

    def _build_image_bar(self):
        frame = ttk.LabelFrame(self, text="Image File")
        frame.grid(row=0, column=0, sticky=EW, pady=(0, 8))
        frame.columnconfigure(0, weight=1)

        ttk.Entry(frame, textvariable=self._image_var).grid(
            row=0, column=0, sticky=EW, padx=6, pady=4)
        ttk.Button(frame, text="Browse…", width=9,
                   command=self._browse_image).grid(row=0, column=1, padx=(0, 6), pady=4)
        ttk.Button(frame, text="Hash…", width=8,
                   command=lambda: self.on_verify_image()).grid(row=0, column=2, padx=(0, 6), pady=4)

    def _build_slots(self):
        frame = ttk.LabelFrame(self, text="Drive Slots")
        frame.grid(row=1, column=0, sticky=EW, pady=(0, 8))
        frame.columnconfigure(0, weight=1)

        for i in range(NUM_SLOTS):
            sw = SlotWidget(frame, slot_index=i,
                            on_start=lambda idx: self.on_start_slot(idx),
                            on_stop=lambda idx: self.on_stop_slot(idx))
            sw.grid(row=i, column=0, sticky=EW, padx=4, pady=2)
            self._slots.append(sw)

    def _build_controls(self):
        frame = ttk.Frame(self)
        frame.grid(row=2, column=0, sticky=EW, pady=(0, 8))

        ttk.Button(frame, text="Start All", bootstyle="success",
                   command=lambda: self.on_start_all()).pack(side=LEFT, padx=(0, 6))
        ttk.Button(frame, text="Stop All", bootstyle="danger",
                   command=lambda: self.on_stop_all()).pack(side=LEFT, padx=(0, 6))
        ttk.Button(frame, text="Clear Log", bootstyle="secondary-outline",
                   command=self.clear_log).pack(side=RIGHT)

    def _build_settings(self):
        frame = ttk.LabelFrame(self, text="Settings")
        frame.grid(row=3, column=0, sticky=EW, pady=(0, 4))

        self._auto_clone_var = ttk.BooleanVar(value=self._config.get_auto_clone())
        ttk.Checkbutton(frame, text="Auto-clone on device insertion",
                        variable=self._auto_clone_var, bootstyle="round-toggle",
                        command=lambda: self._config.set_auto_clone(
                            bool(self._auto_clone_var.get()))).pack(
            side=LEFT, padx=(8, 12), pady=6)

        self._verify_after_var = ttk.BooleanVar(value=self._config.get_verify_after_clone())
        ttk.Checkbutton(frame, text="Verify hash after clone",
                        variable=self._verify_after_var, bootstyle="round-toggle",
                        command=lambda: self._config.set_verify_after_clone(
                            bool(self._verify_after_var.get()))).pack(
            side=LEFT, padx=(0, 12), pady=6)

        self._stop_on_fail_var = ttk.BooleanVar(value=self._config.get_stop_on_verify_fail())
        ttk.Checkbutton(frame, text="Stop all on verification failure",
                        variable=self._stop_on_fail_var, bootstyle="round-toggle",
                        command=lambda: self._config.set_stop_on_verify_fail(
                            bool(self._stop_on_fail_var.get()))).pack(
            side=LEFT, padx=(0, 12), pady=6)

    def _build_log(self):
        frame = ttk.LabelFrame(self, text="Log")
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
            row=1, column=0, sticky=EW, padx=(0, 6))

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

    def _set_theme(self, theme: str):
        self._config.set_theme(theme)
        try:
            self._root_win.style.theme_use(theme)
        except Exception:
            pass

    def _show_about(self):
        messagebox.showinfo(
            "About OdinM_py",
            "OdinM_py\nPython UI for ODINC multi-drive cloning.\n\nBuilt with ttkbootstrap.",
            parent=self._root_win,
        )

    def _browse_image(self):
        initial = self._image_var.get()
        initial_dir = os.path.dirname(initial) if initial else ""
        path = filedialog.askopenfilename(
            title="Select ODIN image file",
            initialdir=initial_dir or None,
            filetypes=[("ODIN image", "*.img *.odin *.bin"), ("All files", "*.*")],
        )
        if path:
            self._image_var.set(path)
            self._config.set_last_image(path)
