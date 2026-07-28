# Target Partition Verification Plan

**Project:** `OdinM_py`
**Status:** Implemented and validated
**Date:** 2026-07-28

## Goal

Make post-flash target verification honor the partition-specific hashes already
configured in the existing hash dialog. This lets multi-partition images verify
the meaningful partition without including the target disk's randomized MBR
signature in a whole-disk comparison.

## Confirmed Current Behavior

- `app.py::_start_target_verify()` always loads partition `0` and hashes the
  full raw-disk region.
- `HashConfig.get_enabled_partitions()` already exposes enabled per-partition
  configurations.
- `partition_reader.read_mbr_partitions()` already returns byte offsets and
  sizes and can read a raw target device path.
- Disk-signature randomization is correctly deferred until verification
  succeeds.
- The existing manual-verify regression script cannot run in the current
  environment: the repository venv lacks `ttkbootstrap`, while the system
  Python installation has `ttkbootstrap` but its Tcl/Tk installation cannot
  locate `init.tcl`.

## Assumptions

- Whole-disk partition `0` and partitions numbered `1` or higher are mutually
  exclusive verification scopes. <user_note> this will work for any partion just need to make thos switches exclusive you either do the whole or you this the singles you want to check no limit on the number of configured singles just not whole and single verify </user_note>
- If multiple partition-specific hashes are enabled, verify them sequentially.
- A missing configured partition, unreadable target partition table, hash read
  failure, or hash mismatch fails target verification.
- Partition `0` runs only when it is explicitly enabled. If no verification
  scope is enabled, fail and direct the operator to configure hashes.<user_note>no fall back if none are enabled the we do what we have now no configured hash and make them set it up</user_note>
- The existing `sha1_fail` and `sha256_fail` policy is outside this fix; target
  verification continues treating any enabled expected-hash mismatch as a
  verification failure.
- No hash-dialog or partition-reader UI changes are needed.

## Implementation

1. Update `OdinM_py/app.py`.
   - Import and use `read_mbr_partitions()`.
   - Reject stale configurations that enable both whole-disk and
     partition-specific scopes.
   - Resolve each configured partition against the target disk's own partition
     table and use its target offset and size.
   - Run configured partition checks sequentially, labeling log output by
     partition.
   - Run whole-disk verification only when partition `0` is explicitly enabled.
   - Fail with a setup instruction when no hashes are enabled.
   - Run `_fix_disk_signature()` only after every selected check passes.

2. Update `OdinM_py/hash_config.py`.
   - Enabling partition `0` disables all individual partition switches while
     retaining their saved hash values.
   - Enabling any individual partition disables partition `0`.
   - Multiple individual partitions can remain enabled together.

3. Add `OdinM_py/scripts/test_partition_target_verify.py`.
   - Exercise the verification flow without constructing the Tk window.
   - Prove target offsets and sizes are passed to `HashWorker`.
   - Prove multiple configured partitions run sequentially.
   - Prove mixed whole-disk/partition configurations fail safely.
   - Prove no enabled hashes requires operator configuration.
   - Prove saved scope switches are mutually exclusive.
   - Prove a missing target partition fails safely.
   - Prove explicitly enabled partition `0` remains available.
   - Prove signature randomization occurs only after all selected checks pass.

4. Validate.
   - Run the focused headless regression test.
   - Run Ruff lint and format checks for the changed Python files.
   - Run MyPy for the affected project if the installed environment supports it.
   - Report the existing GUI regression script's Tcl/Tk environment blocker
     rather than claiming it passed.

## File Scope

- `OdinM_py/app.py`
- `OdinM_py/hash_config.py`
- `OdinM_py/scripts/test_partition_target_verify.py`
- `OdinM_py/Implementations/Target_Partition_Verification_Plan_20260728.md`

## Validation Results

- Focused direct regression: `7/7 checks passed`.
- Focused pytest regression: `7 passed`.
- Python compilation: passed for `app.py` and the focused regression.
- Scoped Ruff lint: passed for the changed files after excluding the project's
  existing `F403`/`F405` star-import allowance and unrelated pre-existing
  `B905` finding in `app.py`.
- Ruff format: passed for the new regression file. Whole-file formatting of
  `app.py` remains blocked by unrelated pre-existing formatting differences;
  those lines were deliberately left unchanged.
- MyPy ran but the project baseline is not clean. Reported issues are in
  existing code paths and test-double assignments, not the new verification
  flow.
- Existing `test_manual_verify_button.py`: still blocked before execution
  because the available Python installation cannot locate Tcl's `init.tcl`;
  the repository venv also lacks `ttkbootstrap`.
