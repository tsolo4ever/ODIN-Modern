Author: Codex (GPT-6) — 2026-11-21
Status: Completed — 2026-11-21
Comments status: Closed — 2026-11-21

# Confirm isolation and unconfirmed refresh

The user requests fresh metadata on manual Refresh Disks for unconfirmed slots,
and Confirm to isolate the selected removable target from Windows filesystem use.

1. Replace unconfirmed occupied-slot snapshots from the current scan and redraw
   awaiting-confirm labels even when disk numbers are unchanged. Confirmed slot
   data, workers, progress and watcher ownership remain untouched.
2. Confirm must revalidate selected removable hardware and protected/source-disk
   exclusions, acquire exclusive locks and dismount every Windows volume by GUID
   (including volumes without letters), then remove only that target's mounted
   drive-letter/folder paths. Check actual handle disk ownership before mutation.
   Fail closed before marking ready; restore removed mounts on partial failure.
3. Run isolation outside the UI thread. Track pending requests and acquired
   handles through cancellation, removal, reconnect and app shutdown. Reconnect
   must isolate again before regaining ready state. Preserve grace authorization.
4. Keep the isolation lease in app ownership and lend it to native raw/all-block
   restore; do not relock or release it on success, cancellation or failure.
   Retain image/hash and physical target identity checks. WSL used-block restore
   cannot run while Windows volume ownership is held and must fail explicitly.
   Confirmed isolation takes precedence over the optional post-flash lock toggle.
5. Validate with fake disks/volume APIs and fake confirmation managers; migrate
   existing synthetic UI tests so their Confirm calls cannot touch real media.
   Verify refresh state/progress, no-letter targets, failure cleanup, stale async
   callbacks, shared lease restore ownership, reconnect and normal shutdown.
   Build a separate test executable with auto-flash disabled; no live isolation,
   disk writes, installed app replacement or push.

Isolation blocks normal filesystem access, not administrator raw-device access
or physical Windows device discovery. Removing mount paths is persistent like the
operator's manual /D test; closing Odin releases handles, and the existing
automount guard restores the prior global policy. These are separate operations.
Partition refresh can expose new volume objects after a changed disk layout;
offline tests cannot prove complete Windows write exclusion across that event.
This limitation must remain visible in hardware acceptance rather than claiming
security-grade detachment. Used-block/WSL isolation is outside this change.

## Completion evidence

Implemented fresh scan metadata and labels for unconfirmed slots while preserving
confirmed progress and watcher ownership. Confirm acquires GUID volume locks,
dismounts and removes target mount paths before ready. It checks physical volume
ownership, protected/source exclusions and target identity, rolls back partial
mount removal, runs outside Tk and cancels pending ownership on removal/close.
Native restore borrows the lease; selected legacy paths receive no stale letters
while the app holds the lease. Failure/cancellation retain isolation. Reconnect
isolates again before ready. A previous flash must stop before its slot accepts
a new confirmation. Failed delivery restores Confirm and disables Start.

205 checks across 18 scripts passed on Python 3.12.10, including 16 new isolation
and refresh checks. Synthetic UI regressions inject fake confirmation managers
and cannot lock connected cards. Scoped Ruff lint (15 Python files), compilation,
new-file formatting, changed-source whitespace and x64 Debug builds passed.
Existing app.py (1197 lines) and guarded_restore.py (1799 lines) remain oversized;
confirmed_disk.py is 232 lines and main_window.py is 649 lines.

Separate executable: `dist/OdinM_py.exe`, 22,138,102 bytes,
SHA-256 `dcb9686356ca2b5652edf74a84532a244713b55795fcaa373d5f7caba3a9163f`.
Archived main, app, confirmed_disk, ui.main_window, guarded_restore and
automount_guard code objects match current compiled source. Test settings, hash
rules and protected hardware were copied from the prior test folder, with
auto-flash off and post-flash locking on. No installed/running exe was replaced;
no live isolation, mount changes or disk writes were performed by the agent.

Close the prior Odin before opening this build. Hardware acceptance remains:
Refresh unconfirmed rows, Confirm one disposable target, verify mount removal
and normal filesystem exclusion, then test mandatory read-back. Check failure
stays locked and removal/normal close release handles. A rewritten layout can
create new volume objects; offline tests do not prove full Windows write
exclusion after partition refresh.

References: [Windows volume locks](https://learn.microsoft.com/en-us/windows/win32/api/winioctl/ni-winioctl-fsctl_lock_volume)
and [persistent letter removal](https://learn.microsoft.com/en-us/windows/win32/fileio/editing-drive-letter-assignments).

## Comments

Codex (2026-11-21): Closed: scope follows the user's Confirm request. Retain
isolation through all terminal outcomes; do not silently unlock on an unchecked
post-flash toggle. Physical acceptance remains separate from offline validation.
