# Python Bounded Raw Imaging Plan

Status: Phase 1 implemented; automated validation passed; live elevated disk
enumeration and hardware capture pending.

## Goal

Add a Python-controlled imaging mode that creates a bootable raw image while
omitting only trailing unallocated disk space. The immediate target is the
Linux sign disk currently laid out as approximately 3.49 GiB ext4, 3.88 GiB
Linux swap, and 52.25 GiB trailing unallocated space on a 64 GB device.

The Make Image workflow must enumerate all readable physical disks, including
fixed disks that Windows does not classify as removable. This broader source
list applies only to read-only image creation; flash targets retain their
existing removable-device restrictions.

This is not filesystem-aware "used blocks" imaging. Every byte from sector 0
through the end of the final real partition is retained, including partition
contents, boot records, EBR metadata, and gaps between partitions.

## Why this path

- The legacy ODIN used-block operation did not successfully image this disk.
- Windows cannot obtain an allocation bitmap for an unmounted ext4 volume.
- WSL is unnecessary when the requirement is only to omit trailing
  unallocated space.
- A bounded raw prefix remains inspectable by the existing Python partition and
  hashing tools and is simpler to recover than a proprietary partition bundle.
- The design remains wholly inside the Python application and supports the
  longer-term goal of removing dependence on ODIN/ODINC.

## Image contract

The new mode produces two files that must remain together:

1. `<name>.compact.img` - raw sectors from LBA 0 through the final sector of
   the last data-bearing partition.
2. `<name>.compact.json` - versioned manifest containing the source disk size,
   sector size, partition style, capture length, discovered primary/logical
   partitions, source disk identity, and SHA-256 of the captured bytes.

The manifest distinguishes an intentional bounded image from an accidentally
truncated raw image. The image data itself stays raw and can be examined by
ordinary disk tools.

## Safety invariants

- Capture opens the selected physical disk read-only.
- The source must still resolve to the same physical disk identity immediately
  before reading begins.
- The Make Image picker labels every source as removable, fixed, and/or the
  current Windows system disk. Fixed and system disks are never silently
  excluded from this read-only source list.
- Selecting the Windows system disk requires an additional confirmation that
  shows its disk number, model, capacity, and mounted volumes.
- The output path cannot reside on the selected source disk. This avoids a
  growing image file changing the source during capture and eventually filling
  the disk being read.
- Version 1 supports MBR disks only. GPT is rejected because omitting the end of
  a GPT disk also omits its backup partition table and requires a separate
  reconstruction design.
- Extended partitions are containers, not data extents. The EBR chain must be
  traversed and validated to find every logical partition and the true final
  data-bearing sector.
- Invalid, cyclic, overlapping, out-of-range, or ambiguous partition layouts
  are rejected rather than guessed.
- Version 1 restores only to a target whose capacity is at least the recorded
  source-disk capacity. A smaller target is rejected even if the captured bytes
  would physically fit, because the original extended-partition entry may
  still advertise the larger source geometry.
- Restore requires the matching manifest and verifies the image SHA-256 before
  opening the target for writing.
- Restore uses the existing removable-disk identity and confirmation gates.
- Existing all-block raw, gzip, ODIN, verification, and post-success disk
  signature behavior remain unchanged.
- Operator logs remain selectable and copy/pasteable.

## Phase 1 - Layout parser and bounded capture

1. Add a Make Image-specific all-physical-disk enumeration path. Do not broaden
   the flash-slot target list or weaken its removable-device gate.
2. Add a focused Python module for MBR and EBR traversal, layout validation,
   manifest serialization, and calculation of the inclusive final capture LBA.
3. Add a bounded capture path to the Python imaging worker. Reuse its buffered
   raw-reader, progress, cancellation, and SHA-256 behavior, but stop at the
   validated capture boundary rather than the physical end of disk.
4. Write the image to a temporary name and publish the image and manifest only
   after the capture and hash complete. On cancellation or error, remove only
   the temporary outputs created by that run.
5. Add focused tests for all-disk source enumeration and source/output disk
   conflict rejection, plus synthetic-layout tests for normal primary partitions,
   extended/logical partitions, internal gaps, trailing unallocated space,
   malformed EBR chains, and a disk with no safe truncation point.

Exit criteria:

- The 64 GB sign disk calculates a boundary near the end of its swap partition,
  not the end of the physical disk.
- The resulting raw file contains an unchanged MBR, EBR chain, ext4 partition,
  and swap partition.
- The manifest hash equals a direct hash of the captured source range.

## Phase 2 - Guarded restore

1. Recognize `.compact.img` only when its matching manifest is present and
   valid.
2. Validate image length, manifest schema, SHA-256, source layout, target
   removability, target identity, and target capacity before any write.
3. Restore the raw prefix using the Python engine, flush it, refresh Windows'
   disk view, and wait for the partition table to become readable.
4. Run the existing configured partition verification when applicable. Preserve
   the rule that disk-signature randomization occurs only after verification
   succeeds.
5. Add tests proving that a missing manifest, altered image, undersized target,
   wrong target, malformed layout, cancellation, or refresh failure cannot be
   reported as success.

Exit criteria:

- A disposable 64 GB target boots after capture and restore.
- Partition contents match the source, and the trailing target region is not
  required to exist in the image.
- An 8 GB target is intentionally rejected in version 1, even if the captured
  prefix is below 8 GB.

## Phase 3 - User interface and operational validation

1. Add `Bounded raw - skip trailing unallocated` to the Make Image engine or
   mode selection with plain-language output expectations.
2. Populate Make Image from all readable physical disks and show disk number,
   model, capacity, removable/fixed classification, system-disk warning, and
   mounted volumes. Keep flash-slot detection unchanged.
3. Show the physical source size, calculated capture size, and saved-space
   estimate before starting.
4. Clearly label the output as a paired image and manifest and prevent automatic
   handling that assumes a full-disk raw image.
5. Add a restore summary that states the recorded source capacity and minimum
   accepted target capacity before confirmation.
6. Exercise capture, cancellation, corrupted-manifest rejection, restore, disk
   refresh, verification, and boot on disposable hardware.

## Later extension - filesystem-aware Linux imaging

After bounded raw capture and restore are proven, add an optional Linux sparse
backend behind the same Python UI:

- Detect WSL and the required Linux imaging utility without installing or
  modifying WSL automatically.
- Prove the Windows physical-disk identity maps to the intended WSL block device
  before any operation.
- Use a filesystem-aware tool for ext-family partitions and retain full-copy or
  recreate semantics for swap and unknown partition types.
- Keep the manifest and restore safety model engine-neutral so WSL is an
  implementation backend, not a separate operator workflow.
- Fall back to bounded raw or all-block raw when WSL or filesystem support is
  unavailable.

This extension is deliberately outside the first implementation phase. It
removes unused blocks inside ext4; it is not required merely to omit the 52.25
GiB trailing unallocated region.

## Expected implementation scope

Likely production areas:

- a new compact-image/layout module under `OdinM_py/`;
- `OdinM_py/drive_manager.py`, with a separate all-source enumeration API that
  does not alter removable flash-target discovery;
- `OdinM_py/pyimager_worker.py`;
- `OdinM_py/ui/make_image_dialog.py`;
- the Python restore/selection path in `OdinM_py/app.py` and/or
  `OdinM_py/clone_worker.py`;
- `OdinM_py/partition_reader.py` only if shared EBR-aware parsing can be used
  without destabilizing existing raw/ODIN verification;
- focused tests under `OdinM_py/scripts/`.

Implementation will be split by phase and exact paths will be confirmed before
touching six or more files. Existing dirty-worktree changes must be preserved.

## Validation

- Focused unit-style scripts for MBR/EBR parsing, boundary calculation,
  manifest validation, capture cancellation, and restore rejection paths.
- Existing engine wiring, image validation, drive-slot stability, manual verify,
  and partition-target verification checks.
- Ruff/static checks used by the Python project.
- PyInstaller build/import smoke test.
- Live capture and restore on disposable hardware before the mode is described
  as proven.

## Approval gate

Approval of this plan authorizes Phase 1 only. Restore and UI integration will
be reviewed against Phase 1 evidence before proceeding to the next phase.

## Phase 1 implementation record

Implemented on 2026-08-15:

- Make Image now has a separate all-readable-physical-disk source list. Fixed
  and system disks are labeled; removable-only flash-target discovery was not
  broadened.
- Added the `pyimager (skip trailing unallocated space)` capture engine and
  paired `.compact.img` / `.compact.json` output.
- Added guarded MBR/EBR traversal, GPT rejection, malformed-layout rejection,
  source identity revalidation, source/output physical-disk conflict blocking,
  extra system-disk confirmation, temporary-file cleanup, and atomic publish.
- Compact capture reuses the Python raw reader and stops at the validated final
  data-bearing partition. Restore remains disabled for this format pending
  Phase 2.

Validation evidence:

- compact-image focused checks: 21/21 passed;
- engine wiring: 13/13 passed;
- image validation: 4/4 passed, with two unavailable fixture checks skipped;
- drive-slot stability: 17/17 passed;
- partition target verification: 8/8 passed;
- manual verify button: 5/5 passed;
- Ruff and Python compilation checks passed for all Phase 1 paths;
- elevated PyInstaller test build completed at
  `OdinM_py/dist_phase1/OdinM_py.exe`.

The remaining Phase 1 exit evidence is a live capture of the Linux sign disk,
confirmation that the calculated boundary is near the end of the swap
partition, and a direct hash/layout inspection of the resulting pair.

## Live capture follow-up - adaptive read recovery

Status: implemented; automated validation passed; live hardware retest
pending. The original slow capture was stopped before rebuilding the
executable.

The first full-disk Roulette capture exposed a transport-specific failure mode:
an 8 MiB read error immediately falls back to 512-byte reads for the entire
chunk. That creates 16,384 serialized reads and repeatedly concatenates an
ever-growing immutable byte string, making a readable disk appear stalled.

Implementation scope:

1. Recover a failed chunk by splitting it into smaller sector-aligned ranges,
   reaching single-sector reads only for ranges that still fail.
2. Fill one preallocated chunk buffer so recovery remains linear rather than
   repeatedly copying accumulated bytes.
3. Check cancellation throughout recovery and never hash or write a partially
   recovered current chunk.
4. Keep the existing invariant that only a truly unreadable sector is replaced
   by one zero sector and reported by its physical sector number.
5. Surface recovery activity through the existing selectable operator log.
   Keep progress tied to fully recovered, hashed, and written chunks.
6. Add fake-reader regression checks for large-read rejection, one bad sector,
   cancellation during recovery, and recovery progress/logging.

Validation evidence:

- adaptive recovery checks: 29/29 passed;
- compact-image checks: 21/21 passed;
- engine wiring: 13/13 passed;
- manual verification: 5/5 passed;
- Python compilation passed;
- focused Ruff passed with the existing unrelated E741 and UP017 findings in
  pyimager.py excluded;
- the standalone OdinM_py.exe PyInstaller build completed successfully.

Live exit evidence remains a quick capture from a small known-good spare drive,
followed by a retry of the Roulette disk that exposed the oversized-read
failure.
