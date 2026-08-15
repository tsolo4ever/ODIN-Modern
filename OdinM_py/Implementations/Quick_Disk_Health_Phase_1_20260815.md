# Quick Disk Health Phase 1

Status: implemented and live-validated; later UI integration remains deferred.

## Goal

Provide an honest, low-impact first look at one explicitly selected physical
disk before broader Disk Health UI and repair work. The live target was the
7.38 GB BIWIN SSD in a shared two-bay USB dock; Windows enumerated it as Disk 2
for the validated run.

## Phase 1 scope

1. Require an explicit physical-disk number. Never default to the first disk.
2. Require a nonempty same-session device identity, then re-read the selected
   disk identity before and after the check so a removal, replacement, degraded
   identity, or disk-number change cannot be reported against the old disk. If
   a USB dock hides the raw descriptor serial, use the exact matching
   `Win32_DiskDrive` index, path, geometry-derived size, and CIM serial. Require
   exact raw/CIM sizes unless CIM's own complete-cylinder geometry proves that
   its size is the raw length rounded down by less than one cylinder. A
   bridge-generated `RANDOM__...` value is only a connection identity, not an
   inventory or sticker serial. Display the CIM model/friendly name and include
   it in the before/after comparison when Windows exposes it; never treat a
   model name as unique by itself.
3. Use `smartctl` read-only metadata when `smartctl.exe` is already available.
   Query only `/dev/pdN`; do not scan every attached device. Treat its exit code
   as a bitmask and retain useful JSON even when health/history bits make the
   process return nonzero.
4. Query Windows `IOCTL_STORAGE_PREDICT_FAILURE` as a tri-state: failure
   predicted, no failure predicted, or unavailable. USB docks commonly hide
   this information, so unavailable is not healthy.
5. Query the disk's actual logical sector size, then read 256 evenly distributed
   64 KiB regions aligned to it, including the beginning and final sector. If a
   64 KiB request fails, verify the entire region with sector-aligned subreads
   of at least 4 KiB. A region is successful only when every subread succeeds,
   which distinguishes a request-size/bridge limitation without hiding a later
   bad sector.
6. Stop sampled reads after eight failed regions. Report exact byte offsets and
   LBAs without retrying or zero-filling them.
7. Print a copy/pasteable report and meaningful process exit code. Phase 1 has
   no report-file option, so the scanner cannot write onto its selected disk.

## Safety boundaries

- Every disk handle is opened `GENERIC_READ` with shared read/write access.
- No write, repair, lock, dismount, signature change, `chkdsk`, SMART self-test,
  or firmware/vendor command is allowed.
- This is not a full surface scan and must never report a disk as certified or
  healthy. The strongest successful result is "no errors in sampled regions."
- Do not run while Macrium, ODIN, or another program is using either bay of the
  shared two-slot dock.
- If data recovery matters, image first and diagnose afterward.
- A read error proves the current drive/dock/cable/power path is unreliable; it
  does not by itself identify which component failed.

## Files

- `OdinM_py/disk_health.py`
- `OdinM_py/scripts/quick_disk_health.py`
- `OdinM_py/scripts/test_disk_health.py`
- this plan

The implementation reuses the current read-only `raw_disk.py` helper. That
helper is part of the existing uncommitted reliability baseline, so this phase
is not independently committable until the baseline is landed.

## Validation

- Deterministic, in-range sample offsets including disk start and tail, with
  both 512-byte and 4 KiB logical-sector alignment covered.
- Successful samples, whole-region 64 KiB-to-4 KiB fallback, a bad later
  subread, short reads, read failures, failure cap, cancellation, missing or
  degraded serial identity, and post-scan identity mismatch.
- `smartctl` JSON parsing on nonzero health/history bitmasks and one guarded SAT
  retry for an explicitly unsupported USB bridge.
- Python compilation, focused Ruff, direct test runner, and pytest when
  available.
- Live elevated run on a known-good spare before Disk 3 when practical. If Disk
  3 is the only immediate target, preserve the printed identity and report.

## Later phase

After live behavior is proven, expose the backend through a non-blocking
`Quick Health` action with a selectable report in the Python UI. Repair and
full-surface recovery remain separate work.

## Implementation result

- Direct focused checks: 23/23 passed.
- Pytest: 23 passed.
- Python compilation and Ruff: passed.
- Mypy: passed for the two new runtime files with imported baseline modules
  skipped; the current `drive_manager.py` baseline has two unrelated existing
  type errors.
- Independent re-review found no remaining high- or medium-severity safety or
  correctness issue.
- Live elevated Disk 2 run identified `BIWIN SS D SCSI Disk Device` through
  the exact CIM index/path and geometry-proven whole-cylinder size relation.
  The bridge exposed neither SMART attributes nor Windows failure prediction.
  All 256 distributed 64 KiB regions were readable: 16.00 MiB sampled, zero
  fallback reads, and zero read errors in 1.51 seconds. This is not a full
  surface result and does not override failures observed during full imaging.
