# Disk Imaging Reliability Plan

Status: implemented; live hardware confirmation remains pending.

## Goal

Make raw flashing, target verification, and ODIN used-block imaging reliable
without changing the verified-before-signature-randomization safety order.

## Current phase

1. Flush and refresh a raw target after writing, then wait for its physical-disk
   partition table to become readable before automatic verification starts.
2. Preserve the actual raw-disk failure reason instead of reporting every open,
   short-read, and invalid-MBR condition as an unreadable partition table.
3. Repair the ODIN whole-disk used-block memory-management defect without
   changing ownership or behavior.
4. Make snapshot mode actually enable used-block imaging.
5. Make whole-disk used-block backup explicit in the Python UI: untracked
   repair/archive use only, prohibited for approved gaming firmware, with the
   generated MBR and partition-image set kept together. Do not create a
   misleading raw target-disk hash for this packed multi-file format.
6. Make used-block restore prove that the target disk and all expected
   partitions have reappeared before dereferencing or restoring them.
7. Add focused Python and C++ regression coverage, then run the affected Python
   checks and the required x64 Debug solution build.

## Safety invariants

- Verification reads the physical disk directly and does not depend on a drive
  letter, so an MBR signature collision does not prevent verification.
- The target MBR signature is randomized only after every configured hash
  passes.
- Used-block images are never presented as byte-for-byte approved gaming
  firmware images.
- Existing raw and gzip image behavior remains unchanged.
- Unrelated worktree changes are preserved and excluded from any commit.

## Later roadmap

- Move useful ODIN/ODINC features into native Python implementations so the
  Python application can eventually operate without the legacy executables.
- Add a guarded **Disk Health** area after its detection and recovery rules are
  proven: diagnose first, then MBR/boot-record backup and repair, partition-table
  checks, and explicit recovery copies before any write. Keep repair actions
  distinct from normal imaging and flashing.

## Completion evidence

- Focused target-verification checks pass (8/8) and manual Verify Disk checks
  pass (5/5); Ruff passes for all touched Python source and tests.
- Command-line tests already cover snapshot parsing as used-block mode; the
  manager now applies that mode consistently.
- The complete x64 Debug solution builds with 0 errors after restoring its
  declared CppUnit dependency. The legacy full test executable still reports
  11 failures and 11 errors in pre-existing configuration/file-path and
  compressed-run-length tests; the changed command-line suite does not fail.
- The standalone `OdinM_py.exe` PyInstaller build completes successfully.
- Live confirmation remains required for the actual post-write refresh and a
  used-block backup/restore on disposable hardware.
