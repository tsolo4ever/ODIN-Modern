"""Main-window panel for one explicitly selected fixed-disk flash target."""

from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
from tkinter import filedialog, messagebox, simpledialog
from tkinter.scrolledtext import ScrolledText

import ttkbootstrap as ttk
from ttkbootstrap.constants import DISABLED, END, EW, LEFT, NORMAL, NSEW, RIGHT, W, X, YES

from guarded_flash_safety import (
    DiskIdentity,
    EligibilityDecision,
    ProtectedHardwareStore,
    ProtectedStoreError,
    list_guarded_candidates,
    revalidate_target,
    scan_system_hardware,
)
from guarded_restore import GuardedImagePlan, preflight_image


MODE_MULTI = "Multi Flash - Removable Drives"
MODE_GUARDED = "Guarded Single Flash - Fixed Drive"
IMAGE_PLACEHOLDER = "Ex. Roulette HD"


@dataclass
class GuardedSessionState:
    active: bool = False
    busy: bool = False
    image_path: str = ""
    selected_disk: DiskIdentity | None = None

    def enter(self) -> None:
        self.active = True
        self.busy = False
        self.image_path = ""
        self.selected_disk = None

    def leave(self) -> None:
        if self.busy:
            raise RuntimeError("guarded operation is still active")
        self.active = False
        self.image_path = ""
        self.selected_disk = None

    def finish_attempt(self) -> None:
        self.busy = False
        self.image_path = ""
        self.selected_disk = None


class GuardedSingleFlashFrame(ttk.Frame):
    """Owns guarded-only UI state; no image path is read from or saved to config."""

    def __init__(
        self,
        parent,
        *,
        store: ProtectedHardwareStore | None = None,
        on_prepare_flash: Callable[[DiskIdentity, GuardedImagePlan], None] | None = None,
        on_stop_flash: Callable[[], None] | None = None,
        on_busy_change: Callable[[bool], None] | None = None,
        **kwargs,
    ):
        super().__init__(parent, padding=8, **kwargs)
        self.state = GuardedSessionState()
        self._store = store or ProtectedHardwareStore()
        self._on_prepare_flash = on_prepare_flash or self._not_connected
        self._on_stop_flash = on_stop_flash or (lambda: None)
        self._on_busy_change = on_busy_change or (lambda _busy: None)
        self._job_results: queue.Queue[tuple[bool, object]] = queue.Queue(maxsize=1)
        self._target_by_display: dict[str, DiskIdentity] = {}
        self._placeholder_visible = False
        self._log_expanded = True
        self._baseline_exists = False
        self._baseline_ready = False
        self._expanded_window_height: int | None = None
        self._original_minsize: tuple[int, int] | None = None
        self._build()

    @property
    def busy(self) -> bool:
        return self.state.busy

    @property
    def image_path(self) -> str:
        return self.state.image_path

    def activate(self) -> None:
        self.state.enter()
        self._clear_image()
        self._clear_target()
        self.clear_log()
        self._set_status("Auto-Flash paused. Create or load the protected baseline.")
        self.append_log("[Guarded] Mode entered. Auto-Flash is paused.")
        self._baseline_exists = self._store.path.exists()
        try:
            baseline_ready = bool(self._store.load())
        except ProtectedStoreError as exc:
            self.append_log(f"[Protection] {exc}", warning=True)
            baseline_ready = False
        self._set_baseline_state(self._baseline_exists, baseline_ready)
        if baseline_ready:
            self.refresh_targets()
        else:
            self.append_log(
                "[Protection] No protected system-hardware baseline exists. "
                "Unplug every flash target, then select Scan System Hardware.",
                warning=True,
            )

    def deactivate(self) -> None:
        self.state.leave()
        self._clear_image()
        self._clear_target()
        self.clear_log()
        self._set_status("Guarded mode inactive.")

    def finish_attempt(self, message: str, *, warning: bool = False) -> None:
        self.state.finish_attempt()
        self._clear_image()
        self._clear_target()
        self._set_busy(False)
        self._stop_button.configure(state=DISABLED)
        self._progress_var.set(0)
        self.append_log(message, warning=warning)

    def set_progress(self, phase: str, percent: int) -> None:
        self._progress_var.set(max(0, min(100, percent)))
        label = {
            "write": "Writing validated image",
            "verify": "Mandatory read-back verification",
            "policy": "Configured partition verification",
        }.get(phase, phase.title())
        self._set_status(f"{label}: {percent}%")

    def confirm_disk_number(self, disk: DiskIdentity, summary: str) -> bool:
        entered = simpledialog.askstring(
            "Confirm Guarded Fixed-Disk Flash",
            summary
            + "\n\nThis will overwrite the selected fixed disk. "
            + f"Type its disk number ({disk.disk_number}) to continue:",
            parent=self.winfo_toplevel(),
        )
        return entered is not None and entered.strip() == str(disk.disk_number)

    def append_log(self, text: str, *, warning: bool = False) -> None:
        self._log_box.configure(state=NORMAL)
        self._log_box.insert(END, text + "\n")
        self._log_box.see(END)
        self._log_box.configure(state=DISABLED)
        if warning and not self._log_expanded:
            self._toggle_log()

    def clear_log(self) -> None:
        self._log_box.configure(state=NORMAL)
        self._log_box.delete("1.0", END)
        self._log_box.configure(state=DISABLED)

    def refresh_targets(self) -> None:
        if self.busy:
            return
        try:
            if not self._store.load():
                self.append_log("[Protection] Scan System Hardware before listing targets.", warning=True)
                return
        except ProtectedStoreError as exc:
            self.append_log(f"[Protection] {exc}", warning=True)
            return
        image_path = self.image_path
        self._start_job(
            "Refreshing guarded target inventory…",
            lambda: list_guarded_candidates(self._store, image_path=image_path),
            self._show_inventory,
        )

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(4, weight=1)

        ttk.Label(
            self,
            text="GUARDED SINGLE FLASH - AUTO-FLASH PAUSED",
            anchor="center",
            bootstyle="inverse-danger",
            font=("TkDefaultFont", 12, "bold"),
            padding=8,
        ).grid(row=0, column=0, sticky=EW, pady=(0, 8))

        protection = ttk.LabelFrame(self, text="Protected System Hardware")
        protection.grid(row=1, column=0, sticky=EW, pady=(0, 8))
        protection.columnconfigure(0, weight=1)
        self._protection_text = ttk.StringVar()
        ttk.Label(
            protection,
            textvariable=self._protection_text,
            justify=LEFT,
        ).grid(row=0, column=0, sticky=W, padx=8, pady=6)
        self._scan_button = ttk.Button(
            protection,
            text="Scan System Hardware",
            bootstyle="danger-outline",
            command=self._scan_system_hardware,
        )
        self._scan_button.grid(row=0, column=1, padx=8, pady=6)
        self._set_baseline_state(False, False)

        target = ttk.LabelFrame(self, text="One Fixed-Disk Target")
        target.grid(row=2, column=0, sticky=EW, pady=(0, 8))
        target.columnconfigure(1, weight=1)
        ttk.Label(target, text="Target:").grid(row=0, column=0, sticky=W, padx=(8, 4), pady=6)
        self._target_var = ttk.StringVar(value="")
        self._target_combo = ttk.Combobox(target, textvariable=self._target_var, state="readonly")
        self._target_combo.grid(row=0, column=1, sticky=EW, pady=6)
        self._target_combo.bind("<<ComboboxSelected>>", self._select_target)
        self._refresh_button = ttk.Button(
            target, text="Refresh Targets", bootstyle="warning-outline", command=self.refresh_targets
        )
        self._refresh_button.grid(row=0, column=2, padx=8, pady=6)
        self._target_detail = ttk.StringVar(value="No eligible fixed disk selected.")
        ttk.Label(
            target, textvariable=self._target_detail, justify=LEFT, wraplength=760
        ).grid(row=1, column=0, columnspan=3, sticky=EW, padx=8, pady=(0, 6))

        image = ttk.LabelFrame(self, text="Image File - required again for every attempt")
        image.grid(row=3, column=0, sticky=EW, pady=(0, 8))
        image.columnconfigure(0, weight=1)
        self._image_var = ttk.StringVar(value="")
        self._image_entry = ttk.Entry(image, textvariable=self._image_var)
        self._image_entry.grid(row=0, column=0, sticky=EW, padx=(8, 4), pady=6)
        self._image_entry.bind("<FocusIn>", self._on_image_focus_in)
        self._image_entry.bind("<FocusOut>", self._on_image_focus_out)
        self._image_entry.bind("<KeyRelease>", self._on_image_key)
        ttk.Button(image, text="Browse…", command=self._browse_image).grid(
            row=0, column=1, padx=(4, 8), pady=6
        )
        self._prepare_button = ttk.Button(
            image, text="Validate and Continue", bootstyle="danger", command=self._prepare_flash
        )
        self._prepare_button.grid(row=0, column=2, padx=(0, 8), pady=6)
        self._stop_button = ttk.Button(
            image, text="Stop", bootstyle="danger-outline", command=self._stop_restore,
            state=DISABLED,
        )
        self._stop_button.grid(row=0, column=3, padx=(0, 8), pady=6)
        self._progress_var = ttk.IntVar(value=0)
        ttk.Progressbar(image, variable=self._progress_var, maximum=100).grid(
            row=1, column=0, columnspan=4, sticky=EW, padx=8, pady=(0, 6)
        )

        log_frame = ttk.LabelFrame(self, text="Guarded Log")
        log_frame.grid(row=4, column=0, sticky=NSEW)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(1, weight=1)
        header = ttk.Frame(log_frame)
        header.grid(row=0, column=0, sticky=EW, padx=4, pady=4)
        self._status_var = ttk.StringVar(value="Guarded mode inactive.")
        ttk.Label(header, textvariable=self._status_var).pack(side=LEFT, fill=X, expand=YES)
        ttk.Button(header, text="Copy All", command=self._copy_log, width=9).pack(side=RIGHT, padx=4)
        self._log_toggle = ttk.Button(header, text="Collapse", command=self._toggle_log, width=9)
        self._log_toggle.pack(side=RIGHT)
        self._log_box = ScrolledText(log_frame, height=9, state=tk.DISABLED, wrap=tk.WORD)
        self._log_box.grid(row=1, column=0, sticky=NSEW, padx=4, pady=(0, 4))
        self._log_box.bind("<Control-a>", self._select_all_log)
        self._show_placeholder()

    def _scan_system_hardware(self) -> None:
        if self.busy:
            return
        if self._baseline_exists:
            self.append_log(
                "[Protection] Protected baseline already exists; scan is disabled.",
                warning=True,
            )
            return
        confirmed = messagebox.askokcancel(
            "Scan Protected System Hardware",
            "Before continuing, unplug EVERY flash target and all other removable storage.\n\n"
            "Every physical disk still attached will be permanently added to the protected "
            "baseline. Existing protected records will be preserved.\n\nContinue with the scan?",
            icon="warning",
            parent=self.winfo_toplevel(),
        )
        if confirmed:
            self._start_job(
                "Scanning and protecting attached hardware…",
                lambda: scan_system_hardware(self._store),
                self._baseline_complete,
            )

    def _baseline_complete(self, result: object) -> None:
        scan = result
        self._set_baseline_state(True, True)
        self.append_log(
            f"[Protection] Scan complete: {len(scan.disks)} disk(s), "
            f"{len(scan.added_record_ids)} newly protected."
        )
        for disk in scan.disks:
            self.append_log(f"[Protection] PROTECTED {disk.description}")
        self.refresh_targets()

    def _show_inventory(self, result: object) -> None:
        decisions = list(result)
        eligible = [decision.disk for decision in decisions if decision.eligible]
        self._target_by_display = {disk.description: disk for disk in eligible}
        values = list(self._target_by_display)
        self._target_combo.configure(values=values)
        self._clear_target()
        for decision in decisions:
            if decision.eligible:
                self.append_log(f"[Target] ELIGIBLE {decision.disk.description}")
            else:
                self.append_log(
                    f"[Target] EXCLUDED {decision.disk.description}: {'; '.join(decision.reasons)}"
                )
        self._set_status(f"{len(eligible)} eligible fixed-disk target(s). Select exactly one.")

    def _select_target(self, _event=None) -> None:
        disk = self._target_by_display.get(self._target_var.get())
        self.state.selected_disk = disk
        self._target_detail.set(disk.description if disk else "No eligible fixed disk selected.")

    def _prepare_flash(self) -> None:
        disk = self.state.selected_disk
        image_path = self.image_path
        if disk is None:
            self.append_log("[Guarded] Select one eligible fixed-disk target.", warning=True)
            return
        if not image_path or not os.path.isfile(image_path):
            self.append_log("[Guarded] Browse to an existing image for this attempt.", warning=True)
            return
        self._start_job(
            "Revalidating selected target…",
            lambda: revalidate_target(disk, self._store, image_path=image_path),
            lambda result: self._target_revalidated(result, image_path),
        )

    def _target_revalidated(self, result: object, image_path: str) -> None:
        decision: EligibilityDecision = result
        if not decision.eligible:
            self.append_log(
                f"[Guarded] Target rejected during revalidation: {'; '.join(decision.reasons)}",
                warning=True,
            )
            self._clear_target()
            return
        self.append_log(f"[Guarded] Target revalidated: {decision.disk.description}")
        self._start_job(
            "Hashing and validating the complete image before target access…",
            lambda: preflight_image(image_path, decision.disk.size_bytes),
            lambda plan: self._confirm_preflight(decision.disk, plan),
        )

    def _confirm_preflight(self, disk: DiskIdentity, result: object) -> None:
        plan: GuardedImagePlan = result
        self.append_log(f"[Image] Preflight passed: {plan.summary}")
        summary = (
            f"Image:\n{plan.summary}\n\nTarget:\n{disk.description}\n"
            f"Stable identity: {disk.stable_key}"
        )
        if not self.confirm_disk_number(disk, summary):
            plan.cleanup()
            self.finish_attempt("[Guarded] Cancelled at final disk-number confirmation.")
            return
        self.append_log(f"[Guarded] Disk {disk.disk_number} confirmation accepted.")
        self._set_restore_active()
        self._on_prepare_flash(disk, plan)

    def _start_job(self, status: str, operation: Callable[[], object], done: Callable[[object], None]) -> None:
        self._set_busy(True)
        self._set_status(status)

        def run() -> None:
            try:
                self._job_results.put((True, operation()))
            except Exception as exc:
                self._job_results.put((False, exc))

        threading.Thread(target=run, daemon=True).start()
        self.after(75, lambda: self._poll_job(done))

    def _poll_job(self, done: Callable[[object], None]) -> None:
        try:
            ok, result = self._job_results.get_nowait()
        except queue.Empty:
            self.after(75, lambda: self._poll_job(done))
            return
        self._set_busy(False)
        if ok:
            done(result)
        else:
            self.append_log(f"[Guarded] {result}", warning=True)
            self._set_status("Guarded operation failed. Review the expanded log.")

    def _set_busy(self, busy: bool) -> None:
        self.state.busy = busy
        widget_state = DISABLED if busy else NORMAL
        self._update_baseline_controls()
        self._refresh_button.configure(state=widget_state)
        self._prepare_button.configure(state=widget_state)
        self._target_combo.configure(state=DISABLED if busy else "readonly")
        self._on_busy_change(busy)

    def _set_restore_active(self) -> None:
        self._set_busy(True)
        self._stop_button.configure(state=NORMAL)
        self._progress_var.set(0)
        self._set_status("Guarded restore starting…")

    def _stop_restore(self) -> None:
        self._stop_button.configure(state=DISABLED)
        self._on_stop_flash()

    def _set_status(self, text: str) -> None:
        self._status_var.set(text)

    def _browse_image(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.winfo_toplevel(),
            title="Select image for this guarded attempt",
            filetypes=[
                ("Disk images", "*.img *.compact.img"),
                ("Compact images", "*.compact.img"),
                ("All files", "*.*"),
            ],
        )
        if path:
            self.state.image_path = path
            self._placeholder_visible = False
            self._image_var.set(path)
            self.refresh_targets()

    def _on_image_focus_in(self, _event=None) -> None:
        if self._placeholder_visible:
            self._placeholder_visible = False
            self._image_var.set("")

    def _on_image_focus_out(self, _event=None) -> None:
        if not self._image_var.get().strip():
            self._show_placeholder()

    def _on_image_key(self, _event=None) -> None:
        if not self._placeholder_visible:
            self.state.image_path = self._image_var.get().strip()

    def _show_placeholder(self) -> None:
        self.state.image_path = ""
        self._placeholder_visible = True
        self._image_var.set(IMAGE_PLACEHOLDER)

    def _clear_image(self) -> None:
        self._show_placeholder()

    def _clear_target(self) -> None:
        self.state.selected_disk = None
        self._target_var.set("")
        self._target_detail.set("No eligible fixed disk selected.")

    def _set_baseline_state(self, exists: bool, ready: bool) -> None:
        self._baseline_exists = exists
        self._baseline_ready = ready
        self._update_baseline_controls()

    def _update_baseline_controls(self) -> None:
        if self._baseline_ready:
            text = "Protected baseline loaded."
        elif self._baseline_exists:
            text = "Protected baseline file exists but could not be loaded."
        else:
            text = "Unplug all flash targets and extra drives, then scan once."
        self._protection_text.set(text)
        self._scan_button.configure(
            text="Hardware Protected" if self._baseline_exists else "Scan System Hardware",
            state=DISABLED if self.busy or self._baseline_exists else NORMAL,
        )

    def _toggle_log(self) -> None:
        window = self.winfo_toplevel()
        window.update_idletasks()
        self._log_expanded = not self._log_expanded
        if self._log_expanded:
            self._log_box.grid()
            self._log_toggle.configure(text="Collapse")
            window.update_idletasks()
            if self._original_minsize is not None:
                window.minsize(*self._original_minsize)
            target_height = max(
                self._expanded_window_height or 0,
                window.winfo_reqheight(),
                (self._original_minsize or (0, 0))[1],
            )
            window.geometry(f"{window.winfo_width()}x{target_height}")
        else:
            self._expanded_window_height = window.winfo_height()
            self._original_minsize = window.minsize()
            self._log_box.grid_remove()
            self._log_toggle.configure(text="Expand")
            window.update_idletasks()
            compact_height = window.winfo_reqheight()
            window.minsize(self._original_minsize[0], compact_height)
            window.geometry(f"{window.winfo_width()}x{compact_height}")

    def _select_all_log(self, _event=None):
        self._log_box.tag_add("sel", "1.0", "end-1c")
        return "break"

    def _copy_log(self) -> None:
        text = self._log_box.get("1.0", "end-1c")
        self.clipboard_clear()
        self.clipboard_append(text)

    def _not_connected(self, _disk: DiskIdentity, plan: GuardedImagePlan) -> None:
        plan.cleanup()
        self._set_busy(False)
        self.append_log(
            "[Guarded] Selection is safe, but the guarded restore engine is not connected yet.",
            warning=True,
        )
