Author: Codex (GPT-6) — 2026-10-03

# Used-block source handoff defect

Status: Implemented and software-validated — physical acceptance pending

## Problem and evidence

The operator's General used-block capture stops with "General used-block
capture requires a source with no Windows-mounted volumes." The check runs
both in discover_used_block_source and capture_used_block_archive, before
Windows source attachment can be prepared.

The approved General_Used_Block_Archive_Profile_20260828.md says to reject
mounted volumes that cannot be safely locked, but the implementation rejects
all lettered sources. Both entry points then use only `wsl --mount --bare`.
The existing pyimager lock helper is designed for Windows raw I/O; retaining
those handles during USB handoff is not itself a proven WSL attachment method.

Microsoft documents that `wsl --mount` cannot attach an in-use disk and does
not support USB flash drives/SD-card readers. USB devices use usbipd instead.
The user's reader was successfully attached with usbipd earlier in this
session for the NTP edit; that observation does not prove ODIN capture support.

References:
- https://learn.microsoft.com/en-us/windows/wsl/wsl2-mount-disk
- https://learn.microsoft.com/en-us/windows/wsl/connect-usb
- used_block_archive.py: discover_used_block_source / capture_used_block_archive
- scripts/pyimager.py: volumes_on_disk / lock_and_dismount_volumes

## Approved bounded implementation

1. Add a source-session helper for identity, Windows volume release, attachment
   ownership, WSL read-only enforcement, and deterministic cleanup. Keep disk
   identity checks already required by ODIN. Do not use a historical disk number
   or a cached USB bus ID as identity.
2. Select the supported attachment route: existing physical-disk WSL attachment
   where supported; usbipd for an unambiguously identified USB reader. Verify
   capacity, boot/layout identity, and partition geometry after attachment.
   A multi-slot reader or an uncertain Windows-disk-to-USB mapping must present
   a precise issue for review rather than attach the wrong device.
3. Ask the operator to authorize temporary release of the selected source's
   Windows volumes before discovery changes their availability. Never force
   dismount an in-use source. Detect output/staging paths located on the source
   before releasing it. Do not change global automount policy or unrelated disks.
4. Use the helper in both discovery and capture. Revalidate after the gap
   between discovery/confirmation and background capture. Keep source
   filesystems unmounted and source block devices read-only while in WSL.
5. Track which state ODIN changed; on success, failure, and cancellation, detach
   only ODIN-owned attachments and restore the prior Windows availability.
   Never detach an operator-owned pre-existing WSL attachment. Cleanup failures
   must be visible and must not turn into an unqualified success message.
6. Retain existing Partclone, filesystem eligibility, manifest, and integrity
   checks. Raw/all-block and guarded restore remain outside this defect.

## Intended files and boundaries

- New OdinM_py/used_block_source_session.py
- OdinM_py/used_block_archive.py (integration and extraction only)
- OdinM_py/ui/make_image_dialog.py (source-release wording and approval wiring)
- OdinM_py/pyimager_worker.py (verified source identity handoff and failure detail)
- New OdinM_py/scripts/test_used_block_source_session.py
- This plan and validation record

The operator approved the initial five paths and explicitly approved adding
pyimager_worker.py as the sixth path to carry source identity into capture.
used_block_archive.py is currently 1,333 lines and make_image_dialog.py is
989 lines; put lifecycle logic in the helper rather than enlarging either
file substantially. Existing user build-number/configuration changes must
remain untouched. Back up existing files before edits per CLAUDE.md.

## Validation

- Mock Windows and WSL operations: mounted/unmounted sources, device mismatch,
  multiple candidates, attachment failures, cancellation, restoration failures,
  operator-owned attachments, and destination-on-source checks.
- Run declared Python 3.12 environment checks and existing archive/engine tests.
- Follow the repository build requirement; keep unrelated generated build-number
  changes separate from this task and do not revert user-owned changes.
- A separate operator-authorized hardware test verifies capture, cancellation,
  and Windows reappearance on an identified source. No restore or disk writes
  are implied by permission to test source capture.

## Comments

Codex (2026-10-03): The full-image copy is complete and the app is closed.
This draft scopes the source handoff defect; no application code, USB state,
volume mount points, or source contents were changed during investigation.

## Implementation and validation — 2026-10-03

The operator approved implementation and emphasized keeping both attachment
paths. Fixed physical disks, including NVMe and fixed USB enclosures, retain
WSL physical-disk attachment. Removable USB readers use usbipd after a unique
PnP ancestry match; readers exposing multiple disks are rejected explicitly.
The current reader mapped to bus 5-4 in a read-only inventory check. No bus ID
or disk number is stored as persistent device identity.

The source session locks every Windows source volume before dismounting any,
never forces a busy volume, checks destination/staging/application volume
locations (including junction resolution), and rejects Windows system/boot
sources for this handoff. The dialog asks before releasing Windows access.
A source's PnP identity, Windows unique ID, capacity, MBR hash, and primary/logical
partition geometry carry from preflight to the worker. The worker resolves
that identity again instead of relying on the old physical disk number.

Capture and exact-range hashing now remain within the read-only WSL session.
Boot/EBR bytes copied before release are compared again after handoff. Hashing
streams through WSL FIFOs and checks byte counts and command success; it does
not stage each range in RAM or reopen an old Windows disk number. Temporary
attachment/binding/offline state is restored only for this session's changes;
original mount paths and disk availability are checked before publishing the
archive. Cleanup failures are reported, including the original operation error
when present, and do not produce a success/archive publication. Status text is
selectable and copyable in the existing dialog.

Validation passed using repository .venv Python 3.12.10:

- 27 mocked source-session/integration tests: both routes, fixed USB routing,
  mounted/unmounted sources, identity/geometry mismatch, disk renumbering,
  busy volume refusal, existing attachments/bindings, cancellation, partial
  attach failures, failed detach, missing Windows mount paths, and archive
  publication ordering. Includes the original discovery/preflight path.
- Existing archive checks: 8/8; engine/UI wiring: 18/18; guarded disk safety: 14/14.
- Actual WSL hashing of a synthetic local file matched Python SHA-256, rejected
  a short read, and rejected a nonexistent input. No physical media used.
- Focused Ruff checks and Python compilation passed. The required Debug/x64
  solution build passed after changes (existing compiler warnings remain).

Source-session logic lives in its own helper. The archive module becomes
smaller; the dialog remains below 1,000 lines. Raw/all-block capture, legacy
ext4 compact capture, and guarded restore behavior were not modified. Existing
operator settings, protected-hardware records, and build-number changes were
preserved. No commit, push, deployment, or live source attachment was performed.

## Remaining physical acceptance

With separate operator authorization, validate General used-block preflight,
capture, cancellation, and restored Windows access on one identified NVMe/fixed
physical source and one identified USB SD reader. Confirm original letters and
binding/offline state return on each path. Mocked transitions and synthetic-file
hashing are software validation, not physical acceptance or restore approval.
