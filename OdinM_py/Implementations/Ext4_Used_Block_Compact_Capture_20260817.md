# Ext4 Used-Block Compact Capture

## Status

Implemented on 2026-08-17 with a 64 MiB free-space buffer. Automated and
synthetic-device validation passes; capture/restore on a disposable physical
SSD and the first unattended cabinet boot remain operator validation gates.

The optional **Install cleanup** selector and cleanup-installer metadata parser
were implemented on 2026-08-18. The bundled Ubuntu 12.04 installer, checked and
unchecked injection paths, picker cancellation/replacement behavior, strict
manifest validation, and existing ODIN Python and Linux script regressions pass.
Newer Linux/systemd expansion remains deliberately fail-closed until a separate
adapter has synthetic and physical boot validation.

## Goal

Create a single guarded `.compact.img` from an MBR Linux source disk while:

- copying the complete disk prefix from LBA 0 through the first ext4 byte;
- copying an offline ext4 root filesystem into a staging copy;
- shrinking only the staging copy to its safe minimum plus a configured buffer;
- omitting Linux swap bytes from the published image;
- preserving the ext4 UUID;
- recording the omitted swap UUID and sector count in the matching JSON;
- re-arming the first-boot expansion hook in the staged filesystem; and
- never writing to or mounting the physical source filesystem.

The approved buffer is 64 MiB so the Linux system has writable headroom while
it boots far enough to run the expansion hook.

## Why Raw Truncation Is Not Allowed

The ext4 used-block count is not a safe byte boundary. Allocated data and ext4
metadata can be distributed near the end of the partition, and the filesystem
superblock continues to describe the original size. Copying a raw prefix ending
at `used blocks + 64 MiB` can therefore create a corrupt filesystem even when
the byte count looks large enough.

The safe operation is:

1. preserve every source byte before the ext4 partition, beginning at LBA 0;
2. build a filesystem-aware staging copy from allocated ext4 blocks;
3. validate and shrink that copy with e2fsprogs;
4. enlarge the shrunken filesystem by the approved buffer;
5. construct a new bounded raw disk image around the verified result.

The preserved prefix includes more than the 512-byte MBR sector. It also keeps
the disk signature, boot code, alignment gap, and bootloader stages that may be
stored between the MBR and the ext4 partition start. Only partition-table
entries describing omitted swap are rewritten in the published copy.

## Supported Source Layout

Phase 1 fails closed unless all of these are true:

- MBR partition table with 512-byte logical sectors;
- exactly one Linux `0x83` ext4 data partition;
- exactly one Linux `0x82` swap partition, primary or logical;
- Ubuntu 12.04 root filesystem with the expected `rc.local` boot path;
- no other data-bearing partitions;
- a readable ext4 UUID and supported ext4 feature set;
- a readable swap v1 header and UUID when swap is present; and
- the selected source identity still matches immediately before and after
  capture.

GPT, LVM, encryption, multiple ext4 filesystems, hibernated filesystems,
mounted Windows volumes, unknown swap formats, and ambiguous partition layouts
are rejected rather than guessed.

## Proposed Manifest Version 2

Schema 1 bounded-raw images remain readable. New images use a distinct schema
version and record both the published image layout and the original source
geometry.

Proposed additional records:

```json
{
  "filesystem": {
    "partition_number": 1,
    "type": "ext4",
    "uuid": "...",
    "block_size": 4096,
    "original_sector_count": 0,
    "compact_sector_count": 0,
    "minimum_blocks": 0,
    "buffer_bytes": 67108864
  },
  "omitted_partitions": [
    {
      "partition_number": 5,
      "type": "linux-swap",
      "uuid": "...",
      "original_start_lba": 0,
      "sector_count": 0
    }
  ],
  "expansion": {
    "armed": true,
    "root_uuid": "...",
    "swap_uuid": "...",
    "swap_sector_count": 0
  }
}
```

Exact keys will be finalized with strict schema validation. The manifest must
never claim compaction or expansion readiness unless every filesystem and boot
hook validation passed.

## Capture Pipeline

1. Revalidate the source disk number, capacity, serial, MBR signature, and
   partition geometry.
2. Read ext4 and swap identity metadata without mounting either filesystem.
3. Copy the complete source prefix from LBA 0 to the ext4 start into the output
   staging image, then create a temporary sparse ext4 staging file populated
   from allocated blocks.
4. Run offline `e2fsck` on the staging copy.
5. Use `resize2fs` to determine and apply the minimum safe filesystem size,
   then add the approved free-space buffer.
6. Install or re-arm the guarded Roulette first-boot expansion hook in the
   staging copy. Its configuration must match the staged ext4 UUID and captured
   swap metadata.
7. Run `e2fsck` again and require a clean result.
8. Patch a copy of the captured MBR to describe only the compact ext4
   partition. Preserve all other bytes before the ext4 start, but do not copy
   the source swap partition, its data, or stale EBR sectors after ext4.
9. Publish the image and schema-2 JSON atomically only after SHA-256 and layout
   validation pass.
10. Delete temporary files and release all device handles on success, failure,
    or cancellation.

The first implementation will require e2fsprogs through a proven local tool
boundary. If the required tools are unavailable or their versions cannot be
validated, the UI must block this mode and explain the prerequisite.

## Guarded Restore Changes

- Continue accepting schema 1 bounded-raw images unchanged.
- Strictly validate schema 2 root, omitted-swap, and expansion records.
- Require target capacity for the compact ext4 layout plus the recorded swap
  reservation and alignment, not the original source-disk capacity.
- Verify the image MBR contains no swap partition and matches the compact ext4
  geometry in JSON.
- Preserve mandatory whole-image SHA-256 read-back verification.
- Do not report success unless the on-image expansion hook and manifest agree.

## Planned Repository Scope

Expected implementation touches more than six paths and therefore requires
explicit scope approval before code changes:

1. `compact_image.py` - schema 2 records and strict validation helpers.
2. `pyimager_worker.py` - staged ext4 compact capture orchestration.
3. A new focused ext4/swap inspection and staging module.
4. `guarded_restore.py` - backward-compatible schema 2 preflight.
5. `ui/make_image_dialog.py` - mode wording, prerequisites, and progress.
6. The first-boot expansion script/config format.
7. Compact-capture and guarded-restore tests.
8. Packaging/dependency files only if a new runtime component is required.

Files will be backed up before modification as required by repository policy,
and only this feature's paths will be staged for commit.

## Validation Gates

- Unit fixtures for primary and logical swap, missing/invalid swap UUID,
  unsupported layouts, ext4 metadata, 64 MiB alignment, cancellation, and
  schema-1 compatibility.
- A synthetic ext4 filesystem whose allocated blocks are deliberately spread
  across the original partition, proving no unsafe last-used-byte assumption.
- Offline `e2fsck -f -n` exit 0 on the published compact image.
- Manifest SHA-256, MBR geometry, ext4 UUID, omitted swap UUID, and swap sector
  count all match independently read values.
- The published image's bytes from LBA 0 through the ext4 start match the
  source exactly except for the explicitly rewritten partition-table entries.
- Guarded restore to a disposable SSD, mandatory read-back verification, and
  one unattended cabinet boot that expands root and recreates swap with the
  recorded UUID.

## Approved Decisions

1. Use a 64 MiB free-space buffer.
2. Replace the current `skip trailing unallocated space` compact option for
   supported Linux layouts while retaining schema-1 restore support.
3. Implement the expected repository scope above.

## Validation Completed

- All 20 `OdinM_py/scripts/test_*.py` regression scripts passed. The five
  site-specific image-validation fixtures were absent and reported as skips.
- Schema-2 tests prove the captured write length is distinct from the minimum
  root-plus-swap target capacity and reject UUID tampering.
- WSL e2fsprogs 1.47.2 successfully performed exact `e2image -raf`, offline
  `e2fsck`, minimum shrink, and regrowth on a synthetic ext4 filesystem.
- The Windows-to-WSL script boundary was exercised with real positional paths;
  the installed expansion-script SHA-256 matched the rendered script.
- The loop-device expansion test cleared stale completion state, expanded ext4,
  recreated swap with its recorded UUID, preserved the MBR identifier, and was
  inert on a completed rerun.

The remaining acceptance work is intentionally physical: create one image from
the disposable source SSD, guarded-restore it to a target SSD, and observe both
unattended cabinet boots through root expansion and swap recreation.

## Monthly Firefox Recovery-Backup Cleanup

Read-only inspection of the expanded Roulette source found 65 timestamped
Firefox crash-recovery profile directories consuming 754,724,864 allocated
bytes. The persistent `default-backup` and `touchscreen-backup` directories
must remain because the active profiles are RAM-backed symlinks. The newest
timestamped copies matched those persistent backups exactly.

The approved follow-up is a standalone POSIX shell script that:

1. Requires the known profile layout, nonempty persistent backups, exact
   `profiles.ini` entries, and exact RAM-profile symlink targets before removal.
2. Removes only directories whose complete names match the captured
   `default-backup-crashrecovery-YYYYMMDD_HHMMSS` or
   `touchscreen-backup-crashrecovery-YYYYMMDD_HHMMSS` patterns.
3. Uses a lock directory, logs each removal and its allocated size, and supports
   a non-mutating dry run.
4. Installs a root-owned cron entry that performs cleanup at 05:00 on the first
   Wednesday of each month. The script independently checks the calendar so a
   malformed cron invocation cannot run scheduled cleanup on another day.
5. Is installed into the staged compact image beside the expansion hook and is
   not executed during capture. The physical source remains read-only.

Validation covers installation, exact schedule text, dry-run behavior,
calendar guarding, exact-name filtering, persistent-backup preservation,
successful cleanup, and fail-closed behavior when required profile state is
missing.

## Optional Cleanup Selection

The compact capture engine remains responsible for mandatory first-boot ext4
expansion and swap recreation. Cleanup is site-specific and will become an
explicit operator-selected installer:

1. Add an **Install cleanup** checkbox to Make Image. It is shown only for
   `pyimager (ext4 used blocks, omit swap)` and defaults off for each dialog
   session.
2. Checking it immediately opens a shell-script file picker. Cancelling the
   picker leaves the option unchecked. The selected path is displayed with a
   Browse button so the operator can replace it before capture.
3. Require the selected script to implement the existing guarded installer
   contract: `script --install ROOT`. The installer must publish
   `/usr/local/sbin/roulette-profile-cleanup` and
   `/etc/cron.d/roulette-profile-cleanup`; capture verifies the installed
   script against the selected file's SHA-256 and verifies the schedule.
4. Pass the selected path through the image worker into compact capture without
   weakening any existing source-layout, filesystem, or expansion gate.
5. Always inject and validate the expansion script and broken-clock policy.
   Execute and validate the selected cleanup installer only when checked.
6. Record the cleanup source filename and SHA-256 in the schema-2 manifest so
   an image can be audited without mounting it. Existing schema-2 manifests
   without cleanup metadata remain readable.
7. Expand the header comments in `roulette_profile_cleanup.sh` into a reference
   contract written for a future maintainer or another coding model. It will
   explain the `--install ROOT` entry point, required installed paths and
   permissions, schedule ownership, fail-closed validation, dry-run behavior,
   logging, exact-target restrictions, and how Odin verifies the selected
   source hash. The operational code remains the working example.
8. Add focused tests for checked, unchecked, cancelled-browse, missing-file,
   and invalid-installer cases. Re-run the compact-image, guarded-restore, and
   safety suites.

The approved implementation scope covers these seven code/test paths:

1. `ui/make_image_dialog.py`
2. `pyimager_worker.py`
3. `ext4_compact_capture.py`
4. `compact_image.py`
5. `guarded_restore.py`
6. `scripts/roulette_profile_cleanup.sh`
7. `scripts/test_compact_image.py`

Restore keeps expansion metadata mandatory and accepts strictly validated,
optional cleanup metadata without weakening whole-image verification.

### Linux compatibility parser boundary

Cleanup installers carry a bounded comment-header manifest declaring their
format, identifier, install contract, installed paths, Linux family, and tested
version list. Odin parses that header without executing the script and rejects
missing, duplicate, malformed, or incompatible declarations. The bundled
Roulette installer documents and demonstrates the complete version-1 contract.

This phase fully supports the proven Ubuntu 12.04 plus `rc.local` expansion
path. Newer Linux releases are represented in the parser contract but remain a
fail-closed stub until their boot mechanism is implemented and tested. A future
systemd adapter must install and enable a one-shot expansion unit, prove the
required filesystem/partition tools exist in that image, and receive its own
synthetic and physical boot validation before its version is accepted.
