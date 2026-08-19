"""
make_image_dialog.py
Modal dialog — automated image setup workflow:
  Step 1: Backup drive  (ODINC -backup -allBlocks -compression=none)
  Step 2: Compute hash  (SHA-256 + SHA-1 via HashWorker)
  Step 3: Save config   (HashLog + HashConfig partition 1, both algos enabled+fail)
After completing, per-partition verify is ready to use.
"""

import glob
import os
from tkinter import filedialog, messagebox

_MB = 1_048_576

import ttkbootstrap as ttk
from ttkbootstrap.constants import *

from clone_worker import CloneStatus, CloneWorker
from compact_image import compact_manifest_path
from config_manager import ENGINE_ODIN, ENGINE_PYIMAGER
from drive_manager import (
    DriveInfo,
    get_all_readable_drives,
    get_path_disk_number,
    get_physical_drive,
)
from ext4_compact_capture import (
    Ext4CompactCaptureError,
    check_prerequisites,
    parse_cleanup_installer,
)
from hash_config import HashConfig
from hash_log import HashLog
from hash_worker import HashStatus, HashWorker
from partition_reader import get_image_hash_region
from pyimager_worker import PyImagerWorker

ENGINE_ODINC = "ODINC.exe  (ODIN container image)"
ENGINE_PY_COMPACT = "pyimager  (ext4 used blocks, omit swap)"
ENGINE_PY = "pyimager  (raw .img, built in)"
ENGINE_PY_GZ = "pyimager  (gzip-compressed .img.gz)"
ENGINE_LABELS = [ENGINE_PY_COMPACT, ENGINE_PY, ENGINE_PY_GZ, ENGINE_ODINC]

ENGINE_HINTS = {
    ENGINE_ODINC: "ODIN container format. Options… sets backup flags.",
    ENGINE_PY_COMPACT: "MBR Linux disks only. Preserves the boot prefix, compacts "
                       "ext4 with 64 MiB free, and records omitted swap metadata.",
    ENGINE_PY: "Plain dd-style image, no header. Real progress, per-sector "
               "retry, SHA-256 while reading.",
    ENGINE_PY_GZ: "Same as raw but gzip-compressed; hashes still describe the "
                  "uncompressed disk bytes.",
}


class MakeImageDialog(ttk.Toplevel):
    def __init__(self, parent, odinc_path: str, config):
        super().__init__(parent)
        self.title("Make Image")
        self.resizable(False, False)
        self.grab_set()

        self._parent = parent
        self._odinc = odinc_path
        self._config = config
        self._drives = get_all_readable_drives()
        self._worker: CloneWorker | PyImagerWorker | None = None
        self._hasher: HashWorker | None = None
        self._output_path = ""
        self._backup_flags = ["-allBlocks", "-compression=none"]
        self._backup_polling = False  # set True while file-size poll is active
        self._poll_ticks = 0  # seconds since backup started
        self._poll_last_size = -1  # last seen file size (bytes)
        self._poll_stall_ticks = 0  # consecutive ticks with same size
        self._backup_drive_size = 0  # total drive bytes — used for progress %

        self._build()

        self.update_idletasks()
        px = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
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
            row=0, column=0, sticky=W, pady=(0, 4)
        )
        drive_labels = [d.source_display for d in self._drives]
        self._drive_var = ttk.StringVar(
            value=drive_labels[0] if drive_labels else "No readable physical disks found"
        )
        self._drive_cb = ttk.Combobox(
            outer, textvariable=self._drive_var, state="readonly", values=drive_labels, width=80
        )
        self._drive_cb.grid(row=0, column=1, columnspan=2, sticky=EW, padx=(8, 0), pady=(0, 4))

        # Imaging engine — defaults from the app-wide engine setting so this
        # dialog and the flash slots agree on which engine is "current".
        default_engine = ENGINE_PY if self._config.use_pyimager() else ENGINE_ODINC
        ttk.Label(outer, text="Engine:", anchor=W).grid(row=1, column=0, sticky=W, pady=(0, 4))
        self._engine_var = ttk.StringVar(value=default_engine)
        self._engine_cb = ttk.Combobox(
            outer,
            textvariable=self._engine_var,
            state="readonly",
            values=ENGINE_LABELS,
            width=46,
        )
        self._engine_cb.grid(row=1, column=1, columnspan=2, sticky=EW, padx=(8, 0), pady=(0, 4))
        self._engine_cb.bind("<<ComboboxSelected>>", self._on_engine_change)

        # Output file
        ttk.Label(outer, text="Output file:", anchor=W).grid(row=2, column=0, sticky=W, pady=(0, 4))
        self._output_var = ttk.StringVar()
        ttk.Entry(outer, textvariable=self._output_var).grid(
            row=2, column=1, sticky=EW, padx=(8, 4), pady=(0, 4)
        )
        ttk.Button(outer, text="Browse…", width=9, command=self._browse_output).grid(
            row=2, column=2, pady=(0, 4)
        )

        self._engine_hint = ttk.Label(outer, text="", anchor=W, bootstyle="secondary")
        self._engine_hint.grid(row=3, column=1, columnspan=2, sticky=W, padx=(8, 0))

        option_frame = ttk.Frame(outer)
        option_frame.grid(row=4, column=0, columnspan=3, sticky=EW, pady=(4, 8))
        option_frame.columnconfigure(0, weight=1)

        self._cleanup_frame = ttk.Frame(option_frame)
        self._cleanup_frame.grid(row=0, column=0, sticky=EW, pady=(0, 4))
        self._cleanup_frame.columnconfigure(1, weight=1)
        self._cleanup_var = ttk.BooleanVar(value=False)
        self._cleanup_check = ttk.Checkbutton(
            self._cleanup_frame,
            text="Install cleanup",
            variable=self._cleanup_var,
            command=self._on_cleanup_toggle,
            bootstyle="round-toggle",
        )
        self._cleanup_check.grid(row=0, column=0, sticky=W)
        self._cleanup_path_var = ttk.StringVar()
        ttk.Entry(
            self._cleanup_frame,
            textvariable=self._cleanup_path_var,
            state="readonly",
        ).grid(row=0, column=1, sticky=EW, padx=(8, 4))
        self._cleanup_browse_btn = ttk.Button(
            self._cleanup_frame,
            text="Browse…",
            width=9,
            command=self._browse_cleanup,
            state=DISABLED,
        )
        self._cleanup_browse_btn.grid(row=0, column=2)

        # Auto-workflow toggle
        self._auto_var = ttk.BooleanVar(value=True)
        self._auto_check = ttk.Checkbutton(
            option_frame,
            text="Auto hash & configure after backup",
            variable=self._auto_var,
            bootstyle="round-toggle",
        )
        self._auto_check.grid(row=1, column=0, sticky=W)

        ttk.Separator(outer, orient=HORIZONTAL).grid(
            row=5, column=0, columnspan=3, sticky=EW, pady=(0, 8)
        )

        # Step indicators
        step_frame = ttk.Frame(outer)
        step_frame.grid(row=6, column=0, columnspan=3, sticky=EW, pady=(0, 6))
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
            lbl = ttk.Label(step_frame, text=text, anchor=W, bootstyle="secondary")
            lbl.grid(row=i, column=1, sticky=W, padx=(4, 0))
            self._step_lbls.append((icon, lbl))

        ttk.Separator(outer, orient=HORIZONTAL).grid(
            row=7, column=0, columnspan=3, sticky=EW, pady=(6, 8)
        )

        # Progress + status
        self._progress_var = ttk.IntVar(value=0)
        self._pbar = ttk.Progressbar(
            outer, variable=self._progress_var, maximum=100, length=420, bootstyle="info-striped"
        )
        self._pbar.grid(row=8, column=0, columnspan=3, sticky=EW, pady=(0, 4))

        self._status_lbl = ttk.Label(outer, text="Ready", bootstyle="secondary")
        self._status_lbl.grid(row=9, column=0, columnspan=3, sticky=W, pady=(0, 8))

        # Buttons
        btn_frame = ttk.Frame(outer)
        btn_frame.grid(row=10, column=0, columnspan=3, sticky=EW)

        self._start_btn = ttk.Button(
            btn_frame, text="Start", bootstyle="success", command=self._start
        )
        self._start_btn.pack(side=LEFT, padx=(0, 6))
        self._stop_btn = ttk.Button(
            btn_frame, text="Stop", bootstyle="danger-outline", command=self._stop, state=DISABLED
        )
        self._stop_btn.pack(side=LEFT, padx=(0, 6))
        self._options_btn = ttk.Button(
            btn_frame, text="Options…", bootstyle="secondary-outline", command=self._open_options
        )
        self._options_btn.pack(side=LEFT, padx=(0, 6))
        ttk.Button(btn_frame, text="Close", bootstyle="outline", command=self._on_close).pack(
            side=RIGHT
        )

        self._on_engine_change()

    # ── step indicator helpers ────────────────────────────────────────────────

    def _set_step(self, idx: int, state: str):
        """state: 'pending' | 'running' | 'done' | 'failed'"""
        if not self.winfo_exists():
            return
        icon, lbl = self._step_lbls[idx]
        styles = {
            "pending": ("○", "secondary"),
            "running": ("▶", "info"),
            "done": ("✓", "success"),
            "failed": ("✗", "danger"),
        }
        sym, style = styles.get(state, ("○", "secondary"))
        icon.configure(text=sym, bootstyle=style)
        lbl.configure(bootstyle=style)

    # ── actions ───────────────────────────────────────────────────────────────

    # ── engine ────────────────────────────────────────────────────────────────

    @property
    def _engine(self) -> str:
        return self._engine_var.get()

    @property
    def _use_pyimager(self) -> bool:
        return self._engine in (ENGINE_PY_COMPACT, ENGINE_PY, ENGINE_PY_GZ)

    @property
    def _compact_mode(self) -> bool:
        return self._engine == ENGINE_PY_COMPACT

    def _on_engine_change(self, _event=None):
        """Keep the hint, the Options button and the output extension in step."""
        self._engine_hint.configure(text=ENGINE_HINTS.get(self._engine, ""))
        # ODINC backup flags are meaningless for the built-in imager.
        self._options_btn.configure(state=DISABLED if self._use_pyimager else NORMAL)
        # Raw vs gzip is a pyimager-only detail with no equivalent in the
        # app-wide setting - both map back to the same "pyimager" engine.
        self._config.set_engine(ENGINE_PYIMAGER if self._use_pyimager else ENGINE_ODIN)
        if self._compact_mode:
            self._auto_var.set(False)
            self._auto_check.configure(state=DISABLED)
            self._cleanup_frame.grid()
        elif self._use_pyimager:
            self._auto_check.configure(state=NORMAL)
            self._clear_cleanup_selection()
            self._cleanup_frame.grid_remove()
        else:
            self._clear_cleanup_selection()
            self._cleanup_frame.grid_remove()
            self._sync_backup_mode()

        # Nudge the extension so the chosen engine and the filename agree.
        path = self._output_var.get().strip()
        if not path:
            return
        if self._compact_mode:
            if not path.lower().endswith(".compact.img"):
                root, _ext = os.path.splitext(path)
                self._output_var.set(root + ".compact.img")
            return
        if path.lower().endswith(".compact.img"):
            path = path[:-len(".compact.img")] + ".img"
            self._output_var.set(path)
        wants_gz = self._engine == ENGINE_PY_GZ
        has_gz = path.lower().endswith(".gz")
        if wants_gz and not has_gz:
            self._output_var.set(path + ".gz")
        elif not wants_gz and has_gz:
            self._output_var.set(path[:-3])

    def _browse_output(self):
        if self._compact_mode:
            default_ext = ".compact.img"
            types = [("Bounded raw image", "*.compact.img"), ("All files", "*.*")]
        elif self._engine == ENGINE_PY_GZ:
            default_ext = ".img.gz"
            types = [("Gzipped raw image", "*.img.gz *.gz"), ("All files", "*.*")]
        elif self._engine == ENGINE_PY:
            default_ext = ".img"
            types = [("Raw disk image", "*.img *.bin"),
                     ("Gzipped raw image", "*.img.gz *.gz"),
                     ("All files", "*.*")]
        else:
            default_ext = ".img"
            types = [("ODIN image", "*.img *.odin *.bin"),
                     ("All files", "*.*")]
        last_output_dir = self._config.get_last_output_dir()
        path = filedialog.asksaveasfilename(
            title="Save image as",
            defaultextension=default_ext,
            filetypes=types,
            parent=self,
            initialdir=last_output_dir if os.path.isdir(last_output_dir) else None,
        )
        if not path:
            return
        path = os.path.abspath(path)
        if self._compact_mode and not path.lower().endswith(".compact.img"):
            root, _ext = os.path.splitext(path)
            path = root + ".compact.img"
        self._output_var.set(path)
        self._config.set_last_output_dir(os.path.dirname(path))
        # Picking a .gz by hand implies the compressing engine, and vice versa.
        if path.lower().endswith(".gz") and self._engine == ENGINE_PY:
            self._engine_var.set(ENGINE_PY_GZ)
            self._on_engine_change()
        elif not path.lower().endswith(".gz") and self._engine == ENGINE_PY_GZ:
            self._engine_var.set(ENGINE_PY)
            self._on_engine_change()

    def _on_cleanup_toggle(self):
        if not self._cleanup_var.get():
            self._clear_cleanup_selection()
            return
        self._cleanup_browse_btn.configure(state=NORMAL)
        if not self._browse_cleanup():
            self._clear_cleanup_selection()

    def _clear_cleanup_selection(self):
        self._cleanup_var.set(False)
        self._cleanup_path_var.set("")
        self._cleanup_browse_btn.configure(state=DISABLED)

    def _browse_cleanup(self) -> bool:
        selected = self._cleanup_path_var.get().strip()
        if selected:
            initial_dir = os.path.dirname(selected)
        else:
            initial_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"
            )
        path = filedialog.askopenfilename(
            title="Select ODIN cleanup installer",
            filetypes=[("Shell scripts", "*.sh"), ("All files", "*.*")],
            parent=self,
            initialdir=initial_dir if os.path.isdir(initial_dir) else None,
        )
        if not path:
            return False
        path = os.path.abspath(path)
        try:
            descriptor = parse_cleanup_installer(path)
        except Ext4CompactCaptureError as exc:
            messagebox.showerror("Invalid cleanup installer", str(exc), parent=self)
            return False
        self._cleanup_path_var.set(path)
        self._cleanup_var.set(True)
        self._cleanup_browse_btn.configure(state=NORMAL)
        self._status(
            f"Cleanup selected: {descriptor.installer_id} ({descriptor.source_filename})",
            "info",
        )
        return True

    def _open_options(self):
        from ui.image_options_dialog import ImageOptionsDialog

        dlg = ImageOptionsDialog(self, self._backup_flags)
        if dlg.result is not None:
            self._backup_flags = dlg.result
            self._sync_backup_mode()

    @property
    def _used_block_mode(self) -> bool:
        return any(flag in self._backup_flags for flag in ("-usedBlocks", "-makeSnapshot"))

    def _sync_backup_mode(self):
        if self._used_block_mode:
            self._auto_var.set(False)
            self._auto_check.configure(state=DISABLED)
            self._engine_hint.configure(
                text="Repair/archive set: MBR + partition images; no raw-disk hash config."
            )
        else:
            self._auto_check.configure(state=NORMAL)
            self._engine_hint.configure(text=ENGINE_HINTS.get(self._engine, ""))

    @staticmethod
    def _used_block_files_for(output_path: str) -> list[str]:
        root, _ext = os.path.splitext(output_path)
        paths = [root + ".mbr"]
        paths.extend(glob.glob(root + "-Partition*.img*"))
        return [path for path in paths if os.path.isfile(path)]

    def _used_block_files(self) -> list[str]:
        return self._used_block_files_for(self._output_path)

    def _start(self):
        idx = self._drive_cb.current()
        if idx < 0 or idx >= len(self._drives):
            self._status("No drive selected.", "danger")
            return
        selected: DriveInfo = self._drives[idx]
        drive = get_physical_drive(selected.disk_number)
        if drive is None:
            self._status(f"Disk {selected.disk_number} is no longer readable.", "danger")
            return
        if drive.size_bytes != selected.size_bytes or (
            selected.hw_serial
            and drive.hw_serial
            and selected.hw_serial.casefold() != drive.hw_serial.casefold()
        ):
            self._status(
                f"Disk {selected.disk_number} changed after the source list was loaded. "
                "Close and reopen Make Image.",
                "danger",
            )
            return
        output = os.path.expanduser(os.path.expandvars(self._output_var.get().strip()))
        if not output:
            self._status("Choose an output file first.", "danger")
            return
        if not os.path.isabs(output):
            last_output_dir = self._config.get_last_output_dir()
            if not last_output_dir:
                self._status(
                    "Choose an output folder with Browse before entering only a filename.",
                    "danger",
                )
                return
            output = os.path.join(last_output_dir, output)
        output = os.path.abspath(output)
        if self._compact_mode and not output.lower().endswith(".compact.img"):
            root, _ext = os.path.splitext(output)
            output = root + ".compact.img"
        self._output_var.set(output)
        self._config.set_last_output_dir(os.path.dirname(output))
        cleanup_script = None
        if self._compact_mode and self._cleanup_var.get():
            cleanup_script = os.path.abspath(
                os.path.expanduser(
                    os.path.expandvars(self._cleanup_path_var.get().strip())
                )
            )
            try:
                parse_cleanup_installer(cleanup_script)
            except Ext4CompactCaptureError as exc:
                self._status(str(exc), "danger")
                return
            self._cleanup_path_var.set(cleanup_script)
        output_disk = get_path_disk_number(output)
        if output_disk == drive.disk_number:
            self._status(
                f"Output is on source Disk {drive.disk_number}. Choose another disk or a "
                "network location.",
                "danger",
            )
            return
        if drive.is_system and not messagebox.askyesno(
            "Back up the Windows system disk?",
            f"Disk {drive.disk_number} is the active Windows system disk.\n\n"
            f"{drive.source_display}\n\n"
            "Make Image opens the source read-only and writes only to the selected "
            "output path. Continue?",
            parent=self,
        ):
            self._status("Backup cancelled.", "warning")
            return
        if self._compact_mode and not messagebox.askyesno(
            "Create ext4 compact image?",
            "This MBR-only mode preserves the complete boot prefix, copies allocated "
            "ext4 blocks into a staging image, leaves 64 MiB free, and omits swap "
            "data while recording its UUID and size in the matching JSON. The source "
            "disk is never mounted or changed."
            + (
                f"\n\nCleanup installer: {os.path.basename(cleanup_script)}"
                if cleanup_script
                else "\n\nNo cleanup installer will be added."
            )
            + "\n\nContinue?",
            parent=self,
        ):
            self._status("Backup cancelled.", "warning")
            return
        if self._compact_mode:
            try:
                check_prerequisites()
            except Ext4CompactCaptureError as exc:
                self._status(str(exc), "danger")
                return
        if self._used_block_mode and not messagebox.askyesno(
            "Create repair/archive image set?",
            "This mode creates an MBR plus partition-image set. It is not a "
            "byte-for-byte approved gaming firmware image. Keep every generated "
            "file together. Continue?",
            parent=self,
        ):
            self._status("Backup cancelled.", "warning")
            return
        existing = self._used_block_files_for(output) if self._used_block_mode else []
        if self._compact_mode:
            manifest = compact_manifest_path(output)
            existing = [str(path) for path in (output, manifest) if os.path.exists(path)]
        if os.path.exists(output) or existing:
            conflict_text = (
                "\n".join(os.path.basename(path) for path in existing)
                if existing
                else output
            )
            overwrite = messagebox.askyesno(
                "Overwrite image?",
                f"Existing output will be replaced:\n\n{conflict_text}\n\nOverwrite it?",
                parent=self,
            )
            if not overwrite:
                self._status("Backup cancelled — output file already exists.", "warning")
                return

        self._output_path = output
        self._backup_drive_size = drive.size_bytes
        self._progress_var.set(0)
        self._start_btn.configure(state=DISABLED)
        self._stop_btn.configure(state=NORMAL)

        # reset step indicators
        for i in range(3):
            self._set_step(i, "pending")
        self._set_step(0, "running")
        self._status("Step 1/3: Backing up drive…", "info")

        if self._use_pyimager:
            # In-process imager: real byte-count progress, so no animated bar
            # and no file-size poller needed.
            self._pbar.configure(mode="determinate")
            self._worker = PyImagerWorker(
                root=self._parent,
                disk_number=drive.disk_number,
                image_path=output,
                on_progress=self._on_backup_progress,
                on_log=lambda line: self._status(line, "secondary"),
                on_done=self._on_backup_done,
                sha1=True,
                compact=self._compact_mode,
                expected_size=drive.size_bytes,
                expected_serial=drive.hw_serial,
                cleanup_script=cleanup_script,
            )
            self._worker.start()
            return

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
        self._backup_polling = True
        self._poll_ticks = 0
        self._poll_last_size = -1
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
        self._backup_polling = False  # stop file-size poller
        self._pbar.stop()
        self._pbar.configure(mode="determinate")
        if status == CloneStatus.DONE:
            self._set_step(0, "done")
            if self._compact_mode:
                manifest = compact_manifest_path(self._output_path)
                if not os.path.isfile(self._output_path) or not manifest.is_file():
                    self._set_step(0, "failed")
                    self._status("Compact capture finished without its manifest.", "danger")
                else:
                    result = getattr(self._worker, "result", None) or {}
                    saved = int(result.get("saved_trailing_bytes", 0)) / (1 << 30)
                    self._progress_var.set(100)
                    self._status(
                        f"Ext4 compact image complete; omitted swap and saved "
                        f"{saved:.2f} GiB. Keep the image and manifest together.",
                        "success",
                    )
                self._finish_buttons()
                return
            if self._used_block_mode:
                files = self._used_block_files()
                if len(files) < 2:
                    self._set_step(0, "failed")
                    self._status(
                        "Backup reported complete, but the MBR/partition image set is incomplete.",
                        "danger",
                    )
                else:
                    self._progress_var.set(100)
                    self._status(
                        f"Repair/archive set complete ({len(files)} files). Select the .mbr to restore; keep the set together.",
                        "success",
                    )
                self._finish_buttons()
                return
            if not self._auto_var.get():
                self._progress_var.set(100)
                self._status("Backup complete.", "success")
                self._finish_buttons()
                return
            # pyimager hashed the disk bytes as it read them, so there is
            # nothing to re-read. This is also the only correct source of a
            # hash for a .gz, where the file bytes are compressed.
            digests = getattr(self._worker, "result", None) or {}
            digests = digests.get("digests") or {}
            if self._use_pyimager and digests.get("sha256"):
                self._progress_var.set(100)
                self._set_step(1, "done")
                self._status("Hash taken during read — no re-read needed.", "info")
                self._save_config(digests["sha256"], digests.get("sha1", ""))
            else:
                self._start_hash()
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
        self._status("Step 2/3: Computing disk data hash…", "info")

        region = get_image_hash_region(self._output_path)
        if region is None:
            self._set_step(1, "failed")
            self._status("Hash setup failed — image header is not readable.", "danger")
            self._finish_buttons()
            return
        if not region.is_raw_supported:
            self._set_step(1, "failed")
            self._status(
                "Hash setup failed — compressed ODIN images cannot be raw-drive verified.",
                "danger",
            )
            self._finish_buttons()
            return

        self._hasher = HashWorker(
            root=self._parent,
            file_path=self._output_path,
            on_progress=self._on_hash_progress,
            on_done=self._on_hash_done,
            offset=region.offset,
            byte_count=region.size,
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
            if not HashLog().save_entry(self._output_path, sha256, sha1):
                raise OSError("could not write hash log")

            # Save to partition 0 (whole-disk/image hash), both algos enabled+fail
            saved = HashConfig().save_partition(
                self._output_path,
                0,
                {
                    "sha1_value": sha1,
                    "sha1_enabled": True,
                    "sha1_fail": True,
                    "sha256_value": sha256,
                    "sha256_enabled": True,
                    "sha256_fail": True,
                },
            )
            if not saved:
                raise OSError("could not write hash config")

            self._set_step(2, "done")
            self._status(
                "Setup complete — disk-level hash saved (partition 0). "
                "Use Options → Configure Hashes to add per-partition hashes.",
                "success",
            )
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
            files = self._used_block_files() if self._used_block_mode else []
            size = sum(os.path.getsize(item) for item in files) if files else os.path.getsize(path)
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
            mb = size / _MB
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
