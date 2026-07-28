r"""Regression test for drive_manager's disk-level detection.

Real-world finding: an SD card that goes offline (e.g. due to an MBR
signature collision - see pyimager.randomize_disk_signature) has no mounted
volume and thus no drive letter, so the OLD get_removable_drives() (which
walked GetLogicalDrives()) could never see it at all - the app had no way to
even detect it existed, let alone flash it. Since flashing writes to
\\.\PhysicalDriveN directly and never needs a mounted volume, this is fixed
by detecting disks by probing PhysicalDriveN directly, independent of
Windows' online/offline/mount state.

This test mocks the module-level WinAPI-calling functions to check the
integration logic in isolation - no real hardware touched.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import drive_manager as dm  # noqa: E402

checks = []


def check(name, got, want):
    ok = got == want
    checks.append(ok)
    print(f"  [{'ok ' if ok else 'FAIL'}] {name}: {got!r}"
          + ("" if ok else f"  (expected {want!r})"))


print("disk 4 has no drive letter (offline) but is removable and sized - "
      "must still show up")

orig_is_disk_removable = dm.is_disk_removable
orig_get_size = dm._get_physical_disk_size
orig_get_serial = dm._get_device_serial
orig_letters = dm._letters_by_disk
orig_max = dm.MAX_PHYSICAL_DISKS

# disk 0/1: exist but fixed (not removable) - must never appear.
# disk 2: removable, sized, has a mounted letter (the normal case).
# disk 4: removable, sized, but NO mounted letter at all (the offline/
#         signature-collision/unformatted case this fix targets).
# disk 3: doesn't exist (size 0) - must never appear even if "removable".
FAKE_REMOVABLE = {0: False, 1: False, 2: True, 3: True, 4: True}
FAKE_SIZES = {2: 7969177600, 4: 7969177600}  # disk 3 deliberately absent -> size 0
FAKE_SERIALS = {2: "SN-E", 4: "SN-OFFLINE"}
FAKE_LETTERS = {2: ["E:"]}  # disk 4 deliberately has none


def fake_is_disk_removable(n):
    return FAKE_REMOVABLE.get(n, False)


def fake_get_size(n):
    return FAKE_SIZES.get(n, 0)


def fake_get_serial(n):
    return FAKE_SERIALS.get(n, "")


def fake_letters_by_disk():
    return dict(FAKE_LETTERS)


dm.is_disk_removable = fake_is_disk_removable
dm._get_physical_disk_size = fake_get_size
dm._get_device_serial = fake_get_serial
dm._letters_by_disk = fake_letters_by_disk
dm.MAX_PHYSICAL_DISKS = 5  # only disks 0-4 considered

try:
    drives = dm.get_removable_drives()
finally:
    dm.is_disk_removable = orig_is_disk_removable
    dm._get_physical_disk_size = orig_get_size
    dm._get_device_serial = orig_get_serial
    dm._letters_by_disk = orig_letters
    dm.MAX_PHYSICAL_DISKS = orig_max

by_disk = {d.disk_number: d for d in drives}
check("exactly disks 2 and 4 detected (0/1 fixed, 3 doesn't exist)",
      sorted(by_disk.keys()), [2, 4])
check("disk 2 has its mounted letter", by_disk[2].all_letters, ["E:"])
check("disk 4 (offline) has NO letters but is still present",
      by_disk[4].all_letters, [])
check("disk 4's display shows the no-letter case cleanly",
      "(no drive letter)" in by_disk[4].display, True)
check("disk 4's serial still comes through despite no mounted volume",
      by_disk[4].hw_serial, "SN-OFFLINE")
check("disk 2's raw_device_path is disk-number based, unaffected",
      by_disk[2].raw_device_path, r"\\.\PhysicalDrive2")
check("disk 4's raw_device_path works identically with no letter",
      by_disk[4].raw_device_path, r"\\.\PhysicalDrive4")

print(f"\n{sum(checks)}/{len(checks)} checks passed")
sys.exit(0 if all(checks) else 1)
