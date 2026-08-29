# General Used-Block Archive Profile

## Status

Approved and implemented through Phases 1-3 on 2026-08-29. Synthetic archive,
tamper, range, capacity, gaming-gate, guarded restore, compact-image, and target
safety regressions pass. Phase 4 remains operator-attended and is not complete.

The current Ubuntu WSL instance does not contain the required Partclone
adapters, so live FAT/NTFS/Ext adapter capture and disposable-disk restoration
remain blocked by the documented prerequisite. The application fails closed
before source attachment when those tools are absent and never downloads them
during imaging.

## Goal

Add a guarded **General used blocks** Make Image profile that captures only
allocated filesystem data for offline FAT16/FAT32, NTFS, and Ext2/Ext3/Ext4
partitions while preserving the disk layout required for restoration. Standard,
inactive, unencrypted Linux swap may be omitted only after its version, UUID,
and exact geometry are verified and recorded for deterministic recreation.

This profile is for repair, migration, and archive images. It is not a
byte-for-byte approved gaming-firmware profile and must not silently replace
the existing raw/all-block workflow. Any image identified by the operator as
approved gaming software is ineligible for this profile.

## Current evidence

- Native ODIN `-usedBlocks` already creates an MBR plus per-partition image set
  and obtains Windows allocation maps through `FSCTL_GET_VOLUME_BITMAP`.
- That native allocation-map boundary is suitable for Windows-supported FAT
  and NTFS volumes; it does not establish Ext-family support.
- OdinM's separate `pyimager (ext4 used blocks, omit swap)` profile is a
  specialized Roulette workflow. It requires exactly one Ext4 root and one
  Linux swap partition, shrinks a staged Ext4 filesystem, records omitted swap,
  and installs a first-boot expansion path.
- The general profile must remain distinct from that specialized profile.
- The existing specialized profile already proves the safety boundary needed
  for standard swap: validate a Linux swap v1 header and UUID, record its exact
  sector count, omit its disposable payload, and recreate the same UUID during
  guarded restore. The general profile may reuse that principle without
  inheriting Roulette-specific root shrinking or first-boot expansion.
- Read-only `fdisk` inspection of
  `D:\cards\PAA-New-eth1-default-ntp-fix.img` found the intended initial test
  layout: DOS/MBR, 512-byte sectors, a bootable 6 GiB EFI/FAT partition, a
  53.6 GiB Linux `0x83` partition, and no swap.
- The local Ubuntu WSL environment has the proven e2fsprogs tools used by the
  existing compact engine. A unified FAT/NTFS/Ext adapter such as Partclone is
  not currently installed and must be treated as an explicit prerequisite or
  packaged dependency, not downloaded during an imaging operation.

## Operator behavior

1. Before enabling the profile, ask **Is this approved gaming software?**
   - **Yes:** disable **General used blocks**, require **raw/all blocks**, and
     explain that approved software must retain a byte-for-byte disk image.
   - **No:** allow the profile preflight to continue. This answer does not
     bypass filesystem, identity, adapter, or source-safety checks.
   - Never infer or remember the answer from a filename, folder, prior job, or
     previous disk; require it for each new capture job.
2. Add a clearly named **General used blocks (FAT/NTFS/Ext)** profile.
3. Before confirmation, perform read-only disk and filesystem discovery and
   display every partition, detected filesystem, capacity, and selected capture
   adapter.
4. Allow only FAT16, FAT32, NTFS, Ext2, Ext3, and Ext4 data partitions.
5. If swap is found, inspect it read-only before deciding:
   - require an inactive, unencrypted Linux swap v1 header;
   - require a valid UUID and exact partition start and sector count;
   - require the partition metadata and swap signature to agree;
   - when all checks pass, show the UUID and size and explain that swap contents
     will not be copied and the same standard swap will be recreated; and
   - when any check fails, block Start and direct the operator to **raw/all
     blocks** because the swap cannot be reproduced safely.
6. For the proven Roulette Ext4-plus-swap layout, explain that the specialized
   **ext4 used blocks, omit swap** profile remains the correct choice when root
   shrinking and first-boot expansion are wanted. The general profile preserves
   original partition geometry and does not add an expansion hook.
7. Reject unknown filesystems, ambiguous signatures, encrypted containers,
   LVM, dirty/hibernated filesystems, unsupported sector sizes, mounted source
   volumes that cannot be safely locked, or identity drift.
8. Keep Auto hash/configure disabled because the output is a manifest-backed
   archive set rather than one raw disk image. Integrity belongs to the archive
   workflow: its manifest records hashes for exact sector-addressed source
   ranges as well as hashes for the encoded archive members.
9. Require one final summary confirmation listing the source disk identity,
   partition adapters, output set, and the fact that this is not an approved
   byte-for-byte firmware image.

## Output contract

Publish atomically only after every member validates:

- one versioned JSON manifest selected for restore;
- exact source disk identity, capacity, logical sector size, and MBR geometry;
- a hash-protected copy of the MBR and required pre-partition boot gaps;
- one filesystem-aware image per supported partition;
- partition number, start LBA, sector count, filesystem, UUID/serial when
  available, adapter format/version, uncompressed byte accounting, and SHA-256
  for every encoded member;
- an ordered sector-range index for every source byte the archive claims it can
  restore, with member identity, absolute start LBA, sector count, logical
  sector size, uncompressed byte count, and SHA-256 over the exact uncompressed
  sector bytes;
- bounded hash ranges that never cross a partition, boot-region, omitted hole,
  or adapter-member boundary; large allocated extents are split into a fixed
  schema-defined maximum range rather than creating one manifest record per
  individual sector;
- for every omitted standard swap partition, its partition number, MBR type,
  start LBA, sector count, byte size, swap version, UUID, optional label, header
  evidence, and a declaration that no swap payload member exists; and
- a whole-set manifest digest or equivalent strict member inventory.

Unallocated filesystem space and omitted swap payload have no sector-range hash
and make no byte-for-byte preservation claim. Recreated swap is validated by its
recorded partition geometry, version, UUID, optional label, and header checks.

The first implementation is MBR-only because that matches the new PAA and the
existing ODIN multi-partition archive boundary. GPT, dynamic disks, LVM,
encryption, and RAID remain fail-closed follow-up work.

## Capture design

1. Revalidate the selected physical disk immediately before discovery.
2. Read the MBR layout and identify filesystems from filesystem signatures and
   trusted metadata, not from partition type alone.
3. Detect swap by both partition metadata and signature. Prove that it is
   inactive and unencrypted, validate a standard Linux swap v1 header, require
   a valid UUID, and cross-check its exact geometry. If validation fails, stop
   with the raw/all-block redirect before creating output files.
4. Record each validated swap partition for recreation but do not capture its
   disposable payload. Re-read its UUID, version, and geometry before publish.
5. Prove the required filesystem adapters and their versions before capture.
6. Attach or open the source read-only, with no source filesystem mounted
   writable.
7. Capture only allocated blocks for each supported filesystem using one
   proven adapter contract. The implementation may reuse native ODIN for
   Windows filesystems only if its output can be strictly represented and
   restored by the new manifest; Ext-family capture must use a validated
   filesystem-aware Linux tool rather than raw truncation.
8. While capturing, calculate SHA-256 over each exact uncompressed source
   sector range before adapter encoding. Require the range index to be ordered,
   non-overlapping, and complete for every byte the adapter says it captured.
9. Hash every staged encoded member, re-read the source identity and layout,
   then publish the complete set and manifest together.
10. Remove unpublished staging files and release all WSL/device ownership on
   success, cancellation, or failure.

## Guarded restore design

1. Restore begins from the manifest, not from a guessed sibling filename.
2. Strictly validate schema, member list, encoded-member hashes, the ordered
   sector-range index, adapter versions, partition geometry, and required target
   capacity before selecting a target.
3. Revalidate the target with the existing guarded disk identity controls.
4. Restore the MBR/layout and each partition through its recorded adapter.
5. For each validated omitted-swap record, prove the target partition number,
   start LBA, sector count, and target-disk identity, then create standard swap
   with the recorded UUID and optional label. Verify the resulting type, UUID,
   and size before continuing. Never operate on an active swap partition.
6. Never create, omit, resize, or relocate a partition unless the manifest
   explicitly describes and the profile supports that operation.
7. After adapter restore, read every recorded sector range back from the target
   at its exact absolute LBA and compare its SHA-256 with the manifest. A
   missing, moved, overlapping, truncated, or mismatched range is a failed
   restore, even when the adapter reports success.
8. Verify each restored filesystem or adapter image and all readable identity
   fields. Record unsupported post-restore filesystem checks as failure, not a
   warning-only success.
9. Retain the existing full/raw profile as the fallback for every unsupported
   or ambiguous layout.

## Phased implementation

### Phase 1 - discovery and profile gate

- Add the per-job **Is this approved gaming software?** gate. A **Yes** answer
  makes the general used-block profile unavailable and requires raw/all blocks.
- Add the profile wording and read-only preflight for a **No** answer.
- Recognize the supported filesystem set and swap.
- Accept only standard, inactive, unencrypted swap with a verified v1 header,
  UUID, and exact geometry; show the raw-profile redirect for every other swap
  case.
- Do not expose Start as enabled until the required capture adapters are proven.

### Phase 2 - manifest and capture engine

- Finalize a strict schema and atomic archive-set publication.
- Implement the FAT16/FAT32, NTFS, and Ext2/3/4 adapter boundary.
- Record verified standard swap metadata without copying swap payload bytes.
- Add cancellation, source-identity revalidation, encoded-member hashes,
  sector-addressed uncompressed range hashes, and cleanup.

### Phase 3 - guarded restore

- Add manifest-first preflight, encoded-member verification, and exact target
  sector-range readback verification.
- Restore the exact MBR layout and all supported partitions.
- Recreate each recorded standard swap partition with the same UUID, optional
  label, and exact geometry, then independently verify it.
- Require post-restore filesystem/identity validation before success.

### Phase 4 - physical acceptance

- Capture the current new-PAA FAT-plus-Ext disk.
- Restore to one explicitly disposable target of sufficient capacity.
- Compare layout, filesystem UUID/serial values, file-level evidence, and boot
  behavior against the source.
- Keep the raw NTP-fixed image as the rollback reference until this profile has
  passed capture, restore, and one supervised PAA boot.

## Implementation result

- Added one `.odin-archive` file format whose first member is a strict JSON
  manifest and whose remaining top-level members must exactly match the
  manifest inventory.
- Added per-job approved-gaming-software routing: **Yes** changes the engine to
  raw and stops; **No** continues through every read-only preflight gate;
  cancel stops without changing the selected profile.
- Added read-only WSL discovery that verifies MBR geometry, source identity,
  512-byte sectors, filesystem signature/metadata agreement, clean adapter
  readability, and inactive standard swap v1 before final confirmation.
- Added atomic Partclone capture for FAT16/FAT32, NTFS, and Ext2/3/4, including
  encoded-member SHA-256, Partclone ddrescue-domain range discovery, bounded
  exact source-range hashes, and cleanup on cancellation or failure.
- Added deterministic standard-swap recreation with UUID and optional label
  verification. Nonstandard, active, ambiguous, or encrypted swap fails closed
  to raw/all blocks.
- Preserved the complete pre-partition boot prefix and every later EBR sector,
  allowing supported logical partitions without losing the extended-partition
  chain.
- Added manifest-first Guarded Single Flash restore, existing target identity
  revalidation, exact partition geometry checks, adapter-version matching,
  member extraction validation, MBR/EBR restore, Partclone filesystem restore,
  post-restore allocation-map validation, and exact target range read-back.
- Removed the ambiguous native ODINC used-block and VSS choices from Backup
  Options; the guarded general profile is now the operator-facing archive path.
- Added focused synthetic tests and `Map.md` durable project status.

## Expected repository scope

Implementation will affect more than six paths and requires explicit approval:

1. `ui/make_image_dialog.py` - profile selection, preflight summary, warnings,
   progress, and output-set handling.
2. `ui/image_options_dialog.py` - retire or redirect the ambiguous native
   used-block selection so operators cannot bypass preflight.
3. `used_block_archive.py` - new discovery, schema, capture adapters, atomic
   publication, and cleanup boundary.
4. `pyimager_worker.py` or a focused worker module - background operation and
   cancellation wiring.
5. `guarded_restore.py` - manifest-first restore preflight and target sizing.
6. `partition_reader.py` - only the bounded filesystem/layout metadata needed
   by read-only discovery.
7. Focused unit and synthetic integration tests.
8. Packaging/prerequisite files only after the adapter proof establishes the
   exact required binaries and versions.
9. This plan and `Map.md` for durable status.

Any native C++ changes discovered during adapter proof will be proposed as a
separate approved scope and must satisfy the ODIN solution build requirement.

## Validation gates

- Synthetic FAT16, FAT32, NTFS, Ext2, Ext3, and Ext4 partitions with allocated
  data deliberately spread across their original extents.
- Mixed FAT-plus-Ext MBR layout matching the new PAA.
- Per-job approved-gaming-software fixtures proving **Yes** always disables the
  profile and names raw/all blocks, while **No** continues into every remaining
  preflight gate without weakening one.
- Standard Linux swap v1 fixtures proving UUID/size capture, payload omission,
  exact recreation, and post-restore verification.
- Active swap, encrypted swap, missing/invalid UUID, unsupported header,
  metadata/signature disagreement, geometry drift, and swap-like partition
  fixtures that prove Start is blocked and raw/all-block is named.
- Unknown, dirty, encrypted, mounted, identity-drift, missing-tool,
  cancellation, truncated-member, hash-tamper, and geometry-tamper failures.
- Sector-range fixtures proving exact absolute-LBA hashing, deterministic
  splitting of large allocated extents, full captured-byte coverage, and
  rejection of reordered, overlapping, missing, moved, truncated, or
  hash-mismatched ranges.
- Restore readback proving each recorded target range matches the source hash;
  deliberately altered allocated sectors must fail even when the encoded
  member hash and adapter completion result appear valid.
- Byte and file-level comparison after restore for every supported filesystem.
- Existing raw, gzip, specialized Ext4-plus-swap, and guarded-restore
  regressions remain green.
- One user-attended disposable-disk round trip and supervised new-PAA boot.

## Approval decision

The operator approved implementation of Phases 1 through 3 on 2026-08-29. The
approval applied to this file's registered revision 2
(`plan_revision-7b613af170143cb93e0fba23a5477380`). Physical Phase 4 remains a
separate operator-attended gate and does not authorize writes to any currently
attached disk.
