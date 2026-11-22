Author: Codex (GPT-6) — 2026-11-21
Status: Completed — 2026-11-21
Comments status: Closed — 2026-11-21

# Flash mount protection

The user requires retained locks on failed flashes and `mountvol /N` on
OdinM_py startup with the previous automount setting restored on close.

1. Snapshot Windows automount policy before changing it; execute the system
   mountvol executable without a shell. Restore enabled policy only if Odin
   changed it. Refuse startup if protection cannot be established. Report
   restoration failures visibly. Use an exclusive global mutex so overlapping
   new Odin instances cannot prematurely restore policy.
2. Surround configuration, UI construction and mainloop with the guard, including
   exception cleanup. Stop and wait for known flash workers before restoration.
3. Transfer acquired volume locks to raw restore errors and cancelled results
   when retention is enabled. The native worker owns those handles until removal
   or process exit. Keep failure status and exact SHA-256 verification unchanged.
   Block retries and independent verification while handles are retained.
4. Test failure, cancellation, cleanup, prior-disabled policy, startup exceptions,
   subprocess failure, overlapping instances and UI ownership without touching
   actual disks or changing live automount policy. Build x64 Debug and package
   a separate Python 3.12 test executable; do not replace the running executable.

The automount command affects the whole PC. It does not dismount existing
volumes, erase remembered mount points, or guarantee Windows cannot access a
volume. Hard process termination/power loss cannot run normal close cleanup;
if Odin changed enabled policy, an administrator can recover with `mountvol /E`.
Do not enable automount automatically if it was disabled before Odin opened.
Existing physical-disk discovery supports targets without drive letters.
Used-block/WSL restore ownership rules remain unchanged.

## Completion evidence

Implemented the automount lifetime guard, failed/cancelled raw-restore lock
handoff, worker ownership, UI retry/verification guards, removal-race cleanup,
and shutdown waits before automount restoration. All 151 checks across 13
focused scripts passed on Python 3.12.10. Scoped Ruff lint, formatting of new
modules/tests and main.py, compilation, changed-source whitespace, and x64
Debug builds passed. Existing oversized app.py (1147 lines) and
guarded_restore.py (1780 lines) were not broadly refactored or reformatted.

Separate PyInstaller test executable: `dist/OdinM_py.exe` (21,843,728 bytes),
SHA-256 `66b24fac5a96a5edda62f915237adf4f0e3cc85d22d1665aab64f3c67cb3d671`.
Its archived main, automount_guard, app, guarded_restore, config_manager and
runtime_interlock code objects match the current source compiled under the
same interpreter. Installed settings, hash rules and protected-hardware store
were copied into the ignored test folder; only that copy has auto-flash forced
off. No installed files were replaced, no flash was triggered and no live
automount command was run during offline validation.

## Operator test

Close the currently installed Odin before launching the separate test build.
The startup log must report disabled automount. Confirm the intended removable
target and run the same image manually. Record whether mandatory read-back
passes. A failed/stopped target must remain locked and refuse retry/Verify Disk
until removed. Pull test media before closing Odin to avoid normal Windows
access after policy restoration. Check automount returns to its prior state on
normal close. Physical acceptance remains an operator test, not an offline
validation claim.

Microsoft references: [mountvol command](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/mountvol)
and [NoAutoMount policy semantics](https://learn.microsoft.com/en-us/windows/win32/api/vds/ne-vds-vds_san_policy).

## Comments

Codex (2026-11-21): Closed: no external comments. User explicitly requested
the global automount command. Cancellation retains acquired locks as well,
because a stopped write can leave the same partial/unverified target as failure.
Codex (2026-11-21): Closed: completion review posted to Agent Collab. No open
implementation comments. Hardware outcome is intentionally outside offline
acceptance; source implementation and separate test artifact are ready.
