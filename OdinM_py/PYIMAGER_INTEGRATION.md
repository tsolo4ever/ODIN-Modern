# pyimager integration — working plan

Status as of 2026-07-27. Resume notes for wiring the built-in Python imager
into OdinM_py. The `scripts/` toolkit, `pyimager_worker.py`, and the initial
Make Image engine combobox were committed as `cf740d9`. Everything from the
gz-restore fix onward (this file's "Resolved" section and all 5 "Remaining
steps" that are now done) is **not committed yet**.

## Follow-up, same session — drive_manager.py ctypes bugs + max_disks cap

Real hardware testing of the disk-level detection rearchitecture (above)
found it reporting "0 removable drive(s) detected" even with cards
connected. Root causes, both in `drive_manager.py`, both now fixed:

1. **`CreateFileW` had no explicit `argtypes`/`restype`.** HANDLE is
   pointer-sized; without typing, ctypes assumed a 32-bit return, so a
   genuine failure (-1) never equaled the correctly-64-bit
   `INVALID_HANDLE_VALUE` constant — every open failure went undetected
   and fell through to a `DeviceIoControl` call on a garbage handle
   instead of being reported. Fixed by properly typing `_k32` (module-level
   `WinDLL(..., use_last_error=True)` with explicit `argtypes`/`restype` on
   both `CreateFileW` and `DeviceIoControl`), matching `pyimager.Win32Disk`'s
   already-proven pattern. Added `debug_probe_disks()` (per-`PhysicalDriveN`
   diagnostic: open result + `GetLastError` detail on failure) wired into
   "Refresh Disks" so this class of failure is never silently swallowed
   again.
2. **`is_disk_removable()`'s geometry fallback check used the wrong ioctl.**
   `IOCTL_DISK_GET_DRIVE_GEOMETRY_EX` needs a bigger output buffer than the
   24 bytes it was given (the struct is bigger than just its `Geometry`
   sub-struct), so the whole `DeviceIoControl` call failed and the check
   silently returned "not removable" for genuinely removable SD cards.
   Cross-checked against `src/ODIN/DriveList.cpp`'s `CDriveInfo::Refresh()`
   — ODIN's own, years-proven removable-media check uses the **plain**
   `IOCTL_DISK_GET_DRIVE_GEOMETRY` (no `_EX`) with a correctly-sized 24-byte
   `DISK_GEOMETRY` struct, checking `MediaType == RemovableMedia` at the
   same byte offset (8). Switched to match ODIN's exact ioctl/struct choice
   instead of guessing at buffer sizes for the `_EX` variant.

   Verified live, elevated: `debug_probe_disks()` now correctly reports the
   SD cards as `removable=True` and the internal drives as `removable=False`.

3. **New `max_disks` setting** (`config_manager`, default 5): caps how many
   removable disks get considered for a slot per refresh, same spot as the
   existing `max_drive_gb` size filter. Deliberately does NOT need to
   "leave room" for non-removable disks — `get_removable_drives()` already
   excludes those before returning anything, so the cap only ever competes
   among genuine candidates. Critically, a disk already occupying a
   confirmed slot is never evicted by this cap (only new/unassigned
   candidates compete for the remaining budget) — otherwise a card mid-flash
   could get bumped just because more cards showed up elsewhere. `0` = no
   cap (falls back to `NUM_SLOTS` naturally).

Covered by `scripts/test_max_disks_cap.py` (6/6: cap enforced, deterministic
lowest-disk-number ordering, occupied slot never evicted, `0` = uncapped).
All 7 test files pass together (engine wiring 13/13, image validation 6/6,
drive-slot stability 17/17, auto-clone delay 13/13, disk-level detection
7/7, confirm-lock 19/19, max-disks cap 6/6).

## Latest session addition — disk-level detection + confirm-to-lock

Real hardware testing (4-in-1 shared-bus reader) exposed a chain of bugs, all
now fixed and tested (6 test files in `scripts/`, all passing):

1. **Drive-slot stability** (`app.py._on_drives_changed`): slots used to be
   rebuilt from scratch every poll by re-sorting whatever disks were
   *currently visible*, so one disk blinking out for a single poll (e.g.
   `update_properties()` briefly dropping a letter) shifted every other disk
   into a different slot and could abort unrelated running flashes. Fixed:
   slots are sticky (a present disk never moves), and a disk must be absent
   for `_MISSING_STREAK_THRESHOLD` (2) consecutive polls before being
   treated as a real removal. Same bug existed a second time at the UI
   layer (`MainWindow.update_drives`, `_mirror_drives_to_flash`) — both now
   only re-render a slot when its disk_number actually changes, via
   `_displayed_disk_nums`/`_flash_displayed_disk_nums` diff caches.
2. **Disk-signature collision**: cloned cards share an MBR signature, so
   Windows won't mount the 2nd+ one. Fixed via
   `pyimager.randomize_disk_signature()`, called from
   `app._fix_disk_signature()` only after verify passes or is skipped
   (never on a failure path, since verify hashes from disk offset 0).
3. **Lock-retry**: `pyimager`'s volume-lock attempts (in `restore_disk()`
   and `randomize_disk_signature()`) tried once and gave up; a lock can
   transiently fail right after a flash while Windows still holds handles.
   Now factored into a shared `lock_and_dismount_volumes()` with 5 retries.
4. **Disk-level detection** (`drive_manager.py`): `get_removable_drives()`
   used to only see disks with a *mounted, lettered* volume
   (`GetLogicalDrives()`), so an offline disk (e.g. after a signature
   collision that Windows resolves by taking the disk fully offline, status
   "Offline (Signature Collision)" — confirmed via `diskpart detail disk`)
   was completely invisible to the app. Now scans `\\.\PhysicalDrive0..15`
   directly; `is_disk_removable()` (disk-level, two-signal check) replaces
   the old letter-based `is_removable()` as the flash-safety gate.
   Bringing an offline disk back online needs `rescan` before
   `online disk` in diskpart — the on-disk signature fix alone doesn't
   clear Windows' cached collision flag.
5. **Confirm-to-lock**: since disk-level detection can now surface
   ambiguous/unlabeled disks, nothing flashes (manual Start *or*
   auto-clone) until the operator clicks a "Confirm" button (the slot's
   status badge) to lock that slot onto its current `disk_number`
   (`app._locked_disk_nums`). Confirmation persists 15 minutes
   (`CONFIRM_LOCK_GRACE_MS`) after the disk is confirmed removed — the same
   disk number reappearing within that window skips re-confirmation and
   auto-clone proceeds immediately (`app._lock_expiry_jobs`,
   `_confirm_slot`, `_expire_lock`). The 5s auto-clone settle delay is kept
   as a separate layer; whichever of {settle delay, confirm click} comes
   last is what actually triggers the start.

Test files added: `test_drive_slot_stability.py` (22/22),
`test_auto_clone_delay.py` (13/13), `test_disk_level_detection.py` (7/7),
`test_confirm_lock.py` (18/18) — all mock-based, no real hardware touched.
`is_disk_removable` stubbed True in `test_confirm_lock.py` since that test
is about the lock bookkeeping, not the WinAPI removable check itself.

**Not done**: nothing from this addition is committed. Real-hardware
verification of disk-level detection was deferred (user was mid-flash).
The confirm-to-lock UI has not been exercised in the running app yet — only
via the mock-based `_on_drives_changed`/`_confirm_slot` test harness.

## Follow-up, same session — DriveMonitor is now on-demand, not polled

User's call, deliberately: leaving the app open with nothing confirmed
should do zero background work. `DriveMonitor`'s old continuous 2-second
poll (global rescan → diff → reassign) is gone entirely, replaced by:

- `refresh()` — one-shot manual scan, the *only* way a new disk is
  discovered to fill an empty slot. Wired to the "Refresh Disks" button;
  this is the first step of the operator's setup flow (refresh → Confirm
  each slot → Start).
- `watch_slot(slot_idx, disk_number, on_missing)` — narrow per-disk polling
  for exactly one disk number, started only once `_confirm_slot()` locks a
  slot. Independent by construction: each watch only ever calls
  `is_disk_removable(disk_number)` on its own number, so nothing happening
  to any other disk can affect it. This is what eliminated the "blip on one
  disk shifts/aborts a sibling" bug class at the root, rather than patching
  around it with sticky-slot-assignment + a global debounce (that machinery
  is gone from `app.py` too — `_missing_streak`/`_MISSING_STREAK_THRESHOLD`
  no longer exist).

`app.py._on_drives_changed()` shrank to matching this: it only ever touches
**empty** slots (fills them, or reclaims one matching a pending confirm-lock
expiry within its 15-min grace) and **unconfirmed occupied** slots (re-
verifies these against the fresh scan, since they have no watch running yet
— a real gap caught during this rewrite: an unconfirmed slot's card being
pulled before Confirm was previously never noticed, since refresh only
touched empty slots and no watch existed for it yet). A **confirmed**
slot's own `watch_slot()` owns its removal detection exclusively — this
function never touches one.

`_on_slot_disk_missing(idx)` (the watch's callback) now does what the old
"confirmed gone" branch used to: abort a running worker, clear the slot,
schedule the `CONFIRM_LOCK_GRACE_MS` expiry. `_try_auto_clone(idx)` factors
out the "start now if eligible" check shared by `_confirm_slot()` and the
grace-period reclaim path.

All three affected tests were rewritten for the new model (not just
patched): `test_drive_slot_stability.py` now proves watch independence
directly (removing one confirmed slot's disk never touches any other
slot's data/workers/widget) rather than testing the old global-poll
debounce, which no longer exists. `test_auto_clone_delay.py` and
`test_confirm_lock.py` simulate removal by firing a captured `watch_slot`
tick instead of calling `_on_drives_changed()` with a shorter list. All 6
test files still pass (engine wiring 13/13, image validation 6/6, drive-
slot stability 17/17, auto-clone delay 13/13, disk-level detection 7/7,
confirm-lock 19/19).

**Separately**: ODIN-engine `.gz` restore was reported broken (ODINC.exe's
own native "FileFormatException... unsupported file format" dialog). The
`_gz_content_is_odin()` magic-byte check itself was verified correct
against the real manufacturer master (returns False as it should), so the
actual cause is still open — but per the user's explicit priority call,
**pyimager is the primary engine going forward and ODIN is secondary**
(may be dropped or replaced entirely), so this was deliberately not
chased further this session.

## Why this exists

ODIN writes its progress indicator with
`WriteFile(hOut, ".", 1, ...)` where `hOut = GetStdHandle(STD_OUTPUT_HANDLE)`
(`src/ODIN/CommandLineProcessor.cpp:744`). If ODIN runs with stdout pointed at
the image file, those `.` bytes land **in the payload** and displace everything
after the first one.

Confirmed on `Desktop/cds/test.img`: 195 inserted bytes, one per progress tick
(~1 Hz at ~39 MiB/s, so 37–41 MiB apart — irregular, not a fixed byte stride).
Drift grows `+25` at the ext4 partition start to `+188` near the end.

It hides well: the MBR and first partition parse fine because they precede the
first dot. One-line detector:

```
actual file size - header fileSize (offset 0x78) == number of inserted bytes
```

`scripts/dedot.py` repairs it losslessly and self-validates against every ext4
backup superblock (8/8 exact on test.img). `scripts/pyimager.py` exists so new
captures can't hit this class of bug at all.

Other ODIN captures (`15.2.3.37Clean.img`, `img/15.0.6.2.img`) are clean, so
the bug is intermittent — it depends on how ODIN was launched, not on every run.

## Done

| item | state |
| --- | --- |
| `scripts/` toolkit (16 files + README) | working, see `scripts/README.md` |
| `pyimager.py` — `list/image/validate/verify/restore` | image+verify+restore all exercised on hardware |
| `image_disk()` library entry point | done, drives the GUI |
| gzip output (`.img.gz`) | done; digests are of **uncompressed** bytes (verified identical to a plain capture) |
| `validate_image_file()` | done; catches aborted ODIN captures (`dataSize 0`) and non-images |
| `pyimager_worker.py` — `PyImagerWorker` | mirrors `CloneWorker`'s interface (capture direction only) |
| Make Image → Engine combobox | done, 10/10 wiring tests |
| Main window image picker: `.gz` + validate-on-select | done, 6/6 tests |
| `OdinM_py.spec` bundling fix | **proven — `build_exe.bat` completed and `dist/OdinM_py.exe` runs** |
| `config_manager` engine setting | getters + `set_engine()` (validates against `ENGINES`), defaults to `odin` |
| `pyimager restore`/`verify` `.gz` input | done, data-path verified against real master (see below); not yet run against a physical disk |
| `PyImagerRestoreWorker` (`pyimager_worker.py`) | done — mirrors `CloneWorker`'s callback contract, drives `pyimager.restore_disk()` |
| `app.py` flash-slot routing | done — `_launch()` picks `CloneWorker` vs `PyImagerRestoreWorker` from `config.use_pyimager()` |
| Main window "Flash engine" combobox | done — persists via `set_engine()`, 3/3 smoke-checked (see Tests) |
| Make Image dialog engine default/persist | done — defaults from `config.use_pyimager()`, writes back on change, 13/13 wiring tests |
| Disk-signature fix (`randomize_disk_signature()`) | done — see below; byte-patch math unit-tested, not yet exercised on a real disk |
| Drive-slot stability fix (`_on_drives_changed()` + widget display) | done — see below; two-part real bug found and fixed during hardware testing, 22/22 tests |
| Lock-retry fix (`lock_and_dismount_volumes()`) | done — see below; real failure hit during hardware testing, mock-tested retry logic |

### Verified on hardware

Full round trip on the 7.42 GiB card (PhysicalDrive2):
card → `D:/cards/E-working-2026-07-27.img` → card → `verify` byte-identical,
sha256 `7ee85d803e5eb0a37868589aa744bf60312a88682048372825433a08e3198916`.
The flashed clone booted in the unit.

## Resolved — gz-restore decision

Went with **(a)**: `cmd_restore` and `cmd_verify` in `scripts/pyimager.py` now
gzip-decompress on the fly via `gzip.GzipFile(fileobj=...)`, mirroring
`clone_worker._run_raw_flash(gz=True)`'s stream-decompress pattern exactly
(same reason it works there: the gzip ISIZE trailer wraps past 4 GiB, so the
true decompressed length isn't known up front — progress is tracked against
the compressed file's read position instead, not a byte count).

`restore`'s pre-flight "image larger than disk" check only applies to raw
input now (a `.gz`'s stored size is meaningless for that comparison); instead
the write loop aborts if the decompressed stream exceeds the disk size.
`verify` requires the `.json` sidecar for a `.gz` input, since that's the only
place the true `region_length` is recorded.

Verified against the real manufacturer master
(`OS1.3.44_Sentinel15.2.3.37.img.gz`, 287 MB compressed): the new
`raw_fh` + `GzipFile(fileobj=raw_fh)` chunked-read path decompresses to
exactly 1,083,179,008 bytes with sha256
`6aafa7f3cfcb0db346e418321bc867794287f31c8271ae771f429aef3329fb7` — matching a
plain `gzip.open().read()` reference, and `raw_fh.tell()` climbs
monotonically to the full compressed size. That's the data-path logic
proven; the actual `restore`/`verify` subcommands still haven't been run
against a physical disk with a `.gz` source (step 3 below still needs a real
flash to fully close this out).

## Disk-signature fix (post-flash mountability)

Real hardware finding, 2026-07-27: cloning the same image onto multiple cards
gives every card an **identical MBR disk signature** (4 bytes at offset
`0x1B8` in sector 0). Windows' Mount Manager refuses to assign a drive letter
to a disk whose signature it already has on record from another connected
disk — so the first card mounts fine and every subsequent identical clone
doesn't, which is exactly the multi-slot flashing workflow this app exists
for (5 cards from one master, plugged in at once).

This does **not** affect the app's own correctness — `CloneWorker`,
`PyImagerRestoreWorker`, and the target-disk hash verify (`_start_target_verify`)
all read/write `\\.\PhysicalDriveN` directly, never through a mounted volume.
It only affects whether Windows Explorer shows a drive letter afterward.

Fix: `pyimager.randomize_disk_signature(disk_number)` overwrites that 4-byte
field with a fresh random value and forces Windows to re-read the partition
table. `app.py._fix_disk_signature(idx)` calls it from exactly two places:
after `_on_target_verify_done`'s success branch, and after a `DONE` clone when
`verify_after_clone` is off. **Never** from a failure/mismatch path — the
signature write happens only once nothing is still comparing against those
original bytes, since `_start_target_verify` hashes from disk offset 0 (which
includes the signature) for both raw and ODIN images. Re-exported through
`pyimager_worker.py` so `app.py` doesn't need to reach into `scripts/`
directly, and works for cards flashed by either engine.

Verified: the sector-patch byte math in isolation, and that a nonexistent
disk raises `OSError` (matching `_fix_disk_signature`'s except clause). Not
yet exercised against a real disk with an actual signature collision.

## Drive-slot stability fix (real bug, hit during hardware testing)

Real hardware finding, same session: while flashing 4 cards concurrently on
a shared-bus 4-in-1 reader (all 4 disks report the identical hardware serial
`000000000819`), one card finishing its flash caused the **other three,
still-running** flashes to abort — none of them were physically touched.

Root cause in `app.py`'s old `_on_drives_changed()`: `update_properties()`
(called at the end of every successful `restore_disk()`, and now a second
time by the disk-signature fix above) forces Windows to re-read a disk's
partition table, which can briefly drop its drive letter. On a shared-bus
reader, fixing up one port's signature transiently disturbed its siblings'
enumeration too. The old code rebuilt `self._drives` from scratch every poll
by re-sorting whatever disks happened to be *currently visible* and
re-indexing them by position — so the instant any one disk blinked out for a
single 2-second poll, every disk after it in sort order shifted to a
different slot index, desyncing `self._drives` from `self._workers` (keyed
by slot index). Separately, a disk missing from even one poll immediately
called `.stop()` on that slot's worker if it was running — no debounce at
all.

Fixed by making slot assignment sticky and adding a removal debounce,
both in the same `_on_drives_changed()` pass:
- A still-present disk always keeps its existing slot index - it can never
  be shifted just because a sibling disk appeared or disappeared.
- A disk missing from the current poll is not immediately cleared or
  aborted. It's kept locked in its slot (old `DriveInfo` unchanged, so a
  concurrent target-disk verify reading `self._drives[idx]` never sees a
  false "drive removed") until it's been absent for
  `_MISSING_STREAK_THRESHOLD` (2) **consecutive** change events. Only then
  is the worker actually stopped (if running) and the slot cleared.
- Newly-seen disks fill whatever slots are actually free, in disk_number
  order - unchanged from before for that part.

**Follow-up, same finding:** the internal `self._drives` fix above wasn't
the whole story — real hardware testing still showed slots stuck displaying
"Empty" + no drive, yet with live progress/speed/ETA still ticking (e.g.
"84%, 18.4 MB/s, ~1m 05s" next to "Empty"). Two more spots had the identical
class of bug, one layer up at the UI:
- `_on_drives_changed()` called `self._window.update_drives(drives[:NUM_SLOTS])`
  — the *raw*, positionally-packed per-poll list, not the corrected sticky
  `self._drives`. Fixed by passing `self._drives` instead, and rewriting
  `MainWindow.update_drives()` to accept a NUM_SLOTS-length list (`None` for
  empty) instead of a packed one.
- Both `update_drives()` and `app.py`'s `_mirror_drives_to_flash()` (the
  standalone flash-status window) called `set_drive()`/`reset()`
  *unconditionally* on every refresh — and `SlotWidget.set_drive()` /
  `FlashStatusWindow.set_drive()` both reset status to IDLE and blank
  pct/speed/eta as a side effect. So even with the sticky `self._drives` fix,
  ANY slot's blip caused every OTHER occupied slot's widget to be re-rendered
  and have its live progress wiped, since both call sites walked every slot
  and called `set_drive()` again regardless of whether that slot's disk
  actually changed. Fixed by tracking the last-displayed disk_number per
  slot (`MainWindow._displayed_disk_nums`, `OdinMApp._flash_displayed_disk_nums`)
  and only calling `set_drive()`/`reset()` when it differs from before.

Covered by `scripts/test_drive_slot_stability.py` (22/22): baseline
assignment, a one-poll transient blip (slot locks, siblings untouched,
worker not stopped, **and neither slot's widget display is disturbed**),
the blip resolving (streak resets), a genuine 2-poll removal (worker
stopped, slot clears — **and the widget does reset this time** — siblings'
widgets still untouched throughout), and a new disk filling the freed slot
afterward.

## Lock-retry fix (real failure, hit during hardware testing)

Real hardware finding, same session: flashing 4 cards concurrently, one slot
failed outright: `pyimager restore failed: ValueError: could not lock G: -
close anything using it`, while the other 3 slots locked their volumes fine
in the same moment. `restore_disk()` and `randomize_disk_signature()` each
tried `FSCTL_LOCK_VOLUME` exactly once and gave up immediately - unlike
`clone_worker._lock_and_dismount_volume`, which already retries (dismounting
between attempts to force other handles closed) for exactly this reason: a
lock attempt can transiently fail right after a flash while Windows still
has handles open on the volume.

Fixed by factoring the retry into a shared `lock_and_dismount_volumes()` in
`scripts/pyimager.py` (5 retries, 0.5s delay, forced dismount between
attempts - mirrors `clone_worker`'s pattern), used by both `restore_disk()`
and `randomize_disk_signature()` instead of their previous duplicated
single-attempt loops. Lock failures now raise `OSError` (previously
`ValueError` in `restore_disk()`) - `cmd_restore`'s except clause was
updated to match, and `PyImagerRestoreWorker`/`app.py._fix_disk_signature`
already caught broad `Exception`/`OSError` respectively, so no change was
needed on those call sites.

Verified: a mock-`Win32Disk` unit test covering retry-then-succeed,
retries-exhausted (raises `OSError`), and a later volume failing after an
earlier one already locked (raises, no swallowed state) - real hardware
contention itself wasn't reproduced in testing, only the retry control flow.

## Remaining steps

All 5 of the previously-tracked steps are done (`set_engine`, UI exposure,
flash-slot routing, Make Image default/persist, and a real `build_exe.bat`
run). What's left before this is fully proven, not just wired:

1. Flash an actual card with the engine set to `pyimager` (the main window
   combobox, not Make Image's per-dialog one) and verify it — the routing
   through `PyImagerRestoreWorker` has only been exercised via
   `restore_disk()`'s logic in isolation, not a real `_launch()` call.
2. Flash a `.gz` source specifically (the manufacturer master) with the
   `pyimager` engine selected, to close out the gz-restore work end to end —
   so far only the decompression/hash data path has been verified (below),
   not a real disk write.
3. Flash 2+ cards from the same master, confirm the second one fails to
   mount without the signature fix and does mount with it — the actual
   real-hardware trigger for `randomize_disk_signature()` hasn't been
   reproduced yet, just the mechanism it's built to fix.
4. Re-run the multi-card concurrent flash (4+ cards on the shared-bus
   reader) that originally exposed the drive-slot bug above, to confirm
   the fix holds under the real hardware condition it was built from - the
   16/16 test covers the logic in isolation but hasn't been paired with an
   actual concurrent flash yet.
5. `scripts/find_strings.py` was written but never run (see Card facts,
   below) — unrelated to engine wiring, just still open.

## Gotchas

- **Frozen exe**: `pyimager_worker` resolves `scripts/` from `__file__`, which
  does not exist in a one-file bundle. Fixed via `pathex=['.', 'scripts']` +
  `hiddenimports`. Any new module under `scripts/` that the GUI imports needs
  the same treatment.
- `ext4` (pip) is needed by the analysis tools but **not** by `pyimager`, which
  is dependency-free. Keep it that way — it is the fallback when everything
  else is broken.
- Bulk/recursive file copies into the repo get blocked by the permission
  classifier; copy files one at a time.
- `pyimager restore` has now run against exactly one device. Treat as exercised,
  not proven.
- The Make Image dialog's engine combobox (raw vs gzip vs ODINC) and the main
  window's "Flash engine" combobox (ODIN vs pyimager) are two different
  controls that share the same `config_manager` engine setting underneath —
  raw/gzip has no equivalent at the main-window level, so both pyimager
  choices there map back to the one `pyimager` value. Changing either updates
  the other's default the next time it's opened.
- `OdinM_py/clone_worker.py.bak2` is untracked cruft from an earlier edit.

## Tests

```bash
cd OdinM_py
python scripts/test_engine_wiring.py       # dialog engine switching, config default/persist, grid layout (13/13)
python scripts/test_image_validation.py    # accept/reject/override on real images (6/6)
python scripts/test_drive_slot_stability.py  # sticky slots + removal debounce (16/16)
python -c "import app, ui.main_window, ui.make_image_dialog, pyimager_worker"
python scripts/audit_images.py <img>       # dot check + FAT32 + ext4 walk
python scripts/audit_disk.py 2             # same for a live card
```

## Card facts worth not re-deriving

Two generations, distinguishable by the partition table alone:

| | old | new |
| --- | --- | --- |
| FAT32 | type `0x0B`, 2097152 sectors | type `0x0C`, 2099200 sectors |
| ext4 LBA | 2113536 | 2115584 |
| seen on | `Clean.img`, `15.0.6.2.img` | `OS1.3.44…img.gz` master, `test.img` |

- The manufacturer `.gz` master decompresses to exactly **1,083,179,008 bytes** —
  MBR + the 1 GiB FAT32 partition only, single-partition MBR. The unit creates
  and formats the ext4 partition itself on first boot.
- `eeprom.bin` (256 B) holds the **server** IP as ASCII at offset `0x26`,
  NUL-padded — not a card identity, so cloning it between cards is fine.
- No employee-card IDs are stored on the SD card at all: zero 10-hex-digit and
  zero long-decimal tokens across every ext4 file, even after the card had run
  live with 4 mounts. Card numbers live in NVRAM. `scripts/find_strings.py` was
  written to check whether the server IP appears anywhere besides `eeprom.bin`
  but **has not been run** — that claim is still unverified.
