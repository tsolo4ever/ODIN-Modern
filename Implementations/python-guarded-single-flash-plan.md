# Python Guarded Single Flash Plan

Status: Software implementation complete and automated validation passed.
Phase 4 disposable-hardware validation remains and requires the operator to be
present; the mode is not yet described as proven for production hardware.

## Goal

Add a separate Guarded Single Flash mode to `OdinM_py` for writing exactly one
non-removable physical drive with the Python imaging engine. Keep the existing
removable-drive Multi Flash workflow unchanged.

This mode is intended for work such as a Roulette HD where Windows may classify
the intended target as fixed storage. It must make the intended target obvious,
exclude known system hardware, and prevent Auto-Flash or another write from
running at the same time.

## Scope boundary

- Multi Flash remains removable-drive only.
- Guarded Single Flash handles one non-removable target and one image per
  attempt.
- A non-removable target is never added to the Multi Flash slots or queue.
- Guarded Single Flash uses PyImager only. Missing capabilities are added to
  the Python engine instead of routing this workflow through legacy ODIN/ODINC.
- Removing or migrating the legacy engine from Multi Flash is a separate future
  change.
- This plan provides the guarded restore path needed by Phase 2 of
  `python-bounded-raw-imaging-plan.md`. It supersedes that plan's removable-only
  target assumption for guarded single-drive restore; it does not weaken the
  existing Multi Flash target restrictions.

## Main-window modes

Add a prominent two-option mode selector to the main interface:

1. `Multi Flash - Removable Drives`
2. `Guarded Single Flash - Fixed Drive`

Selecting a mode swaps the main content frame. Do not create a separate window
and do not rebuild the application root. Only one flashing workflow is visible
and active at a time.

Mode behavior:

- Guarded Single Flash cannot open while any Multi Flash write or verification
  is active.
- Entering Guarded Single Flash pauses Auto-Flash before showing any targets.
- While Guarded Single Flash is active, the Auto-Flash watcher may refresh
  inventory but cannot queue or start a write.
- Mode switching is disabled during a guarded write, refresh, or verification.
- Returning to Multi Flash clears all guarded state, performs a fresh removable
  drive scan, and only then allows Auto-Flash to resume.
- Display an unmistakable `GUARDED SINGLE FLASH - AUTO-FLASH PAUSED` banner.

## Protected system-hardware baseline

Guarded Single Flash remains disabled until the operator creates a protected
hardware baseline.

Provide a `Scan System Hardware` action with instructions to unplug every
flash target and other removable storage before continuing. The scan records
all physical disks currently attached and marks them as protected targets.

The protected record must:

- persist across application restarts;
- store a scan timestamp and operator-readable disk description;
- use the strongest stable identifiers Windows exposes, such as device unique
  ID or serial together with model, capacity, and bus/device information;
- never rely on Windows disk number alone;
- preserve previous protected entries when rescanned, adding newly discovered
  hardware rather than silently replacing the list;
- require a deliberate protected-hardware management action to remove an old
  entry; and
- never permit removal of a disk that currently passes a live Windows
  system-disk protection check.

Show every disk found by the baseline scan in a selectable, copyable log. If an
external disk is accidentally present during the baseline, protecting it is a
safe failure: it will remain unavailable as a target until deliberately
reviewed.

## Live system-disk protection

The saved baseline is an additional safety layer, not the source of truth for
Windows system protection. Immediately before presenting a candidate and again
before opening it for writing, reject any physical disk that currently hosts
Windows system, boot, page-file, crash-dump, or recovery storage.

Also reject:

- any disk whose stable identity is in the protected baseline;
- virtual disks and other targets that cannot be proven to be the selected
  local physical device;
- a disk with missing or ambiguous stable identity;
- a target whose disk number now resolves to different hardware;
- a target containing the selected image file; and
- any second candidate once one target is selected.

These checks must exist in the controller/backend as write gates. Hiding a disk
from the interface is not sufficient protection.

## Target selection and confirmation

List only eligible non-removable physical targets. For the selected target,
show its disk number, model, stable ID or serial when available, capacity, bus,
and mounted volumes.

Before writing:

1. Snapshot the selected physical identity.
2. Validate and hash the selected image as required by its format.
3. Show a final summary containing image details and the complete target
   description.
4. Require an explicit confirmation tied to the displayed disk number.
5. Rescan immediately before the write handle is opened.
6. Reject the operation if identity, capacity, classification, mounted volumes,
   or system/protected status changed.

Confirmation never overrides a failed protection check.

## Image selection and format rules

Guarded Single Flash maintains image state separately from Multi Flash.

- Entering the mode always starts with an empty Image File field.
- The empty field displays faded placeholder text `Ex. Roulette HD`.
- The placeholder is never treated as a path or valid selection.
- The operator must browse for an image before every flash attempt.
- Clear the field after success, failure, or cancellation and when leaving the
  mode.
- Do not persist a guarded image path, show recent files, or inherit the Multi
  Flash image.

For ordinary PyImager raw images, establish the exact source byte length and a
source digest before writing. Any compressed format must be fully preflighted
far enough to prove its uncompressed size fits the target before the target is
opened for writing.

A `.compact.img` is never treated as an ordinary raw image. It requires its
matching `.compact.json` and must pass strict manifest schema, image length,
layout, source-capacity, and SHA-256 validation before target access. The target
must be large enough for the validated captured layout; the original donor
capacity remains provenance and accounting evidence rather than a minimum.

## PyImager write and verification contract

- PyImager is the only engine shown or accepted by Guarded Single Flash.
- Complete all possible read-only preflight work before locking, dismounting,
  or opening the target for write access.
- Write only the validated source byte range, flush the target, refresh the
  Windows disk view, and wait for the partition table to become readable.
- Perform mandatory read-back verification of the written byte range against
  the source digest.
- Run configured partition verification as an additional content-policy check
  when applicable.
- Preserve the rule that disk-signature randomization occurs only after every
  required verification succeeds or an existing documented workflow explicitly
  permits verification to be skipped.
- Never report success after cancellation, a short write, refresh failure,
  identity change, or verification failure.
- On cancellation or write failure, clearly report that the target may contain
  a partial image and requires recovery or a complete reflash.

## Log behavior

Use a collapsible log pane in Guarded Single Flash. The log must remain
selectable and copy/pasteable and must include protection decisions, identity
checks, image validation, write progress, refresh, and verification results.

When collapsed, retain a visible current-status/progress summary. Automatically
expand the log for warnings and failures. Collapsing the log must not suppress
or change any safety decision.

## Implementation phases

### Phase 1 - Protection inventory and backend gates

Implementation: `OdinM_py/guarded_flash_safety.py`, with focused coverage in
`OdinM_py/scripts/test_guarded_flash_safety.py`.

Completed validation:

- guarded safety checks: 14/14 passed;
- neighboring disk-health checks: 23/23 passed;
- disk-level detection checks: 7/7 passed;
- live read-only Windows inventory correctly classified the active boot/system,
  recovery, page-file disk and rejected the mounted file-backed virtual disk;
- no baseline file was created and no disk write was performed during testing.

1. Add stable physical-disk fingerprinting and live Windows system-storage
   classification.
2. Add persistent, additive protected-hardware baseline storage and the scan
   operation.
3. Add backend eligibility and identity revalidation APIs with focused tests.

Exit criteria:

- All disks present during the baseline are excluded on later launches.
- Windows system storage is rejected even with a missing or stale baseline.
- Disk-number reuse, ambiguous identity, and protected targets are rejected.

### Phase 2 - Main-window mode and single-target workflow

Implementation: `OdinM_py/ui/guarded_single_flash.py`, integrated through
`OdinM_py/ui/main_window.py` and the mutual-exclusion gates in `OdinM_py/app.py`.
The guarded image is session-only, the empty field displays `Ex. Roulette HD`,
the protected-hardware scan and candidate inventory run off the UI thread, and
the copyable guarded log collapses without hiding its current status.

Completed validation:

- guarded mode/state checks: 14/14 passed;
- hidden main-window build and multi/guarded/multi repaint smoke passed;
- Auto-Flash delay/confirmation checks: 13/13 passed;
- sticky drive-slot checks: 17/17 passed;
- engine wiring checks: 13/13 passed;
- `app.py` remains below the 1,000-line boundary at 996 lines.

1. Add the visible mode selector and mutually exclusive application state.
2. Add the Guarded Single Flash frame, banner, system scan, target summary,
   fresh image selection, confirmation, progress, and collapsible log.
3. Pause Auto-Flash while the guarded mode is active and prove neither workflow
   can start while the other is busy.

Exit criteria:

- Only one non-removable candidate can be selected.
- The guarded image path is never retained between attempts or modes.
- Auto-Flash cannot queue or write behind the guarded interface.

### Phase 3 - Guarded PyImager restore

Implementation: strict preflight and the guarded write/flush/refresh/mandatory
read-back engine are in `OdinM_py/guarded_restore.py`, integrated with the main
window through `OdinM_py/ui/guarded_single_flash.py`. Raw images are hashed
before target access, gzip images are fully validated into a temporary raw
source, and compact images require a matching strict manifest, layout, original
source capacity, length, and SHA-256. The worker repeats source and target
checks after typed disk-number confirmation, reports partial/unverified targets
on cancellation or failure, and runs configured whole-disk or partition hash
checks after mandatory read-back verification. New configurations default to
PyImager; an existing explicit engine choice remains unchanged.

Completed automated validation:

- guarded preflight/restore simulations: 12/12 passed;
- guarded worker/UI integration and configured-policy checks: 7/7 passed;
- guarded mode mutual-exclusion checks: 14/14 passed;
- engine/default wiring checks: 14/14 passed;
- the complete `OdinM_py/scripts/test_*.py` automated suite passed; the
  concurrent-volume harness imports `scripts.pyimager` without shadowing
  production `raw_disk.py`;
- live read-only Windows inventory classification passed; and
- no protected-hardware baseline was created and no physical disk was opened
  for writing during automated validation.

1. Add strict ordinary-image preflight and compact image/manifest loading.
2. Revalidate the target immediately before opening it for writing.
3. Write, flush, refresh, perform mandatory read-back verification, and then
   run configured partition checks and allowed post-verification handling.
4. Add cancellation and partial-target reporting.

Exit criteria:

- No validation or identity failure opens a target for writing.
- A successful run proves the restored byte range matches its source.
- Missing/altered manifests, undersized targets, hot-swapped targets, short
  writes, cancellation, refresh failures, and verification failures cannot be
  reported as success.

### Phase 4 - Disposable-hardware validation

Status: Pending user-attended testing with a known disposable fixed-classified
target. Do not substitute a production, system, project, or vault disk.

Hardware findings (2026-08-16): the first disposable Disk 2 attempt completed
the guarded write path but failed closed before verification when the shared
physical-disk reader issued the partition parser's two-byte MBR-signature read
directly at byte 510. Windows returned `ERROR_INVALID_PARAMETER` (87) because
raw-device transfers must be sector aligned. `OdinM_py/raw_disk.py` now aligns
the underlying transfer to the disk's reported logical sector size while
preserving the caller's requested logical byte range. The same Disk 2 then
wrote and passed mandatory SHA-256 read-back for 7,917,797,376 bytes.

An identical SSD failed read-back in the dock's Target bay, then passed in its
Source bay, isolating that mismatch to the dock path rather than the image or
guarded verifier. A later external wipe also exposed that confirmed Multi
Flash slot watches continued their two-second disk check-ins while Guarded mode
was active. Closing ODIN released the interference. Guarded mode now pauses
those watches without forgetting them and resumes them on return to Multi
Flash. The protected-baseline scan becomes unavailable once its file exists,
and collapsing the guarded log now reduces the actual main-window height.
Compact restore capacity is based on the strictly validated captured layout,
not the donor disk's unused trailing capacity. The original source size remains
manifest provenance and capacity-accounting evidence, while a smaller target is
accepted only when it can hold every captured partition byte.

A different 7.9 GB BIWIN SSD failed twice on the first aligned 8 MiB write with
Windows `ERROR_INVALID_FUNCTION` (1). Windows reported the disk online,
writable, and using 512-byte logical and physical sectors. The older proven raw
writer uses 1 MiB transfers, so Guarded Single Flash now uses that compatible
write size while retaining larger chunks for read-only hashing and verification.
The BIWIN target still requires a user-attended hardware retest.

1. Baseline a workstation with all flash targets unplugged.
2. Confirm every baseline and live Windows system disk remains unavailable.
3. Connect one disposable fixed-classified target and exercise selection,
   cancellation, failure, successful restore, refresh, and verification.
4. Restore a compact Roulette image to an appropriate disposable target and
   confirm its expected boot/runtime behavior.

The mode is not described as proven for production until the live hardware
checks pass.

## Expected implementation scope

Likely production areas include:

- `OdinM_py/app.py` for mutually exclusive mode and operation state;
- `OdinM_py/ui/main_window.py` for the mode selector and frame swap;
- a focused guarded single-flash UI module under `OdinM_py/ui/`;
- `OdinM_py/drive_manager.py` for stable identity and system-disk protection;
- a focused protected-hardware store under `OdinM_py/`;
- `OdinM_py/compact_image.py` for strict manifest loading and validation;
- `OdinM_py/pyimager_worker.py` and `OdinM_py/scripts/pyimager.py` for guarded
  preflight, write, refresh, and verification; and
- focused tests under `OdinM_py/scripts/`.

This is expected to affect six or more paths. Confirm the exact path list and
obtain explicit user approval before implementation. Work on one phase at a
time, preserve unrelated dirty-worktree files, and stage only approved paths.

## Validation

- Focused tests for baseline persistence, additive rescans, live system-disk
  rejection, target eligibility, identity changes, and disk-number reuse.
- UI/state tests proving Multi Flash, Auto-Flash, and Guarded Single Flash are
  mutually exclusive.
- Image-state tests proving the placeholder is not data and no guarded image
  survives an attempt or mode transition.
- Restore rejection tests for invalid formats/manifests, size and capacity
  failures, cancellation, short writes, refresh failures, and verification
  failures.
- Existing drive-slot, engine-wiring, image-validation, manual-verification,
  concurrent-volume-scope, and partition-target-verification checks.
- Python compilation, project Ruff checks, and a PyInstaller build/import smoke
  test.
- Live elevated validation using disposable hardware only.

## Approval gate

This document records the agreed design but does not authorize implementation.
Each implementation phase requires explicit user approval, starting with the
exact files for Phase 1. No fixed/non-removable disk write is considered safe
or complete until disposable-hardware validation passes.
