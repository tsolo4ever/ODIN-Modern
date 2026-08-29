# OdinM Python Project Map

## Current state

- Raw and gzip imaging remain the byte-for-byte profiles.
- `ext4 used blocks, omit swap` remains the Roulette-specific compact profile
  with root shrinking and first-boot expansion.
- `general used-block archive` implements the approved FAT16/FAT32, NTFS, and
  Ext2/3/4 repair/archive workflow for 512-byte MBR disks.
- Approved gaming software is redirected to raw/all blocks for every capture
  job; the answer is not remembered.
- General archives are single `.odin-archive` ZIP containers with a strict
  manifest, Partclone members, MBR/EBR layout regions, standard-swap recreation
  metadata, encoded-member hashes, and exact allocated-range hashes.
- Guarded Single Flash recognizes the archive, reuses existing target identity
  and protected-hardware gates, restores through the recorded adapters, and
  requires exact target range read-back before success.

## Key files

- `used_block_archive.py` - discovery, prerequisite checks, schema, capture,
  atomic publication, extraction, filesystem restore, swap recreation, and
  range verification.
- `ui/make_image_dialog.py` - operator profile, gaming-software question,
  read-only preflight summary, and background capture dispatch.
- `pyimager_worker.py` - cancellable background capture wiring.
- `guarded_restore.py` - archive preflight and guarded restore orchestration.
- `ui/guarded_single_flash.py` - archive selection in the guarded workflow.
- `scripts/test_used_block_archive.py` - synthetic manifest, tamper, capacity,
  range, swap, and gaming-gate coverage.
- `Implementations/General_Used_Block_Archive_Profile_20260828.md` - approved
  design, implementation status, and physical acceptance boundary.

## Required environment

- Windows with WSL available.
- WSL Partclone adapters: `partclone.fat`, `partclone.ntfs`, `partclone.extfs`,
  `partclone.restore`, and `partclone.chkimg`.
- Standard Linux disk tools including `blkid`, `lsblk`, `blockdev`, `mkswap`,
  and `sync`.
- Imaging never downloads dependencies. Missing or version-mismatched tools
  fail closed before capture or restore.

## Next step

Phase 4 remains operator-attended: capture a representative PAA disk, restore
to an explicitly disposable target, compare layout/files/identities, and run a
supervised PAA boot while retaining the raw NTP-fixed rollback image.
