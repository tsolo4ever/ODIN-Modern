# Roulette First-Boot Storage Expansion

## Status

Implemented and validated in disposable loop-device tests on 2026-08-17. The
operator-attended cabinet test expanded the disposable 64-GB target and booted
the game successfully. The corrected image still requires one unattended
cabinet boot before this plan can move to `Implementations/Done/`.

## Validated source image

- Image: `D:\cards\Roulette No Swap.img.compact.img`
- Manifest: `D:\cards\Roulette No Swap.img.compact.json`
- Image length: 3,747,610,624 bytes
- SHA-256: `0ff69f453cf9eae68da779ef18a72492cdbb8d161ab8f0b9c547b0a5724da499`
- Partition table: MBR with one bootable Linux partition
- Root partition: number 1, start LBA 2,048, 7,317,504 sectors, ext4
- Root UUID: `e4059dde-ca92-4c9a-99d7-bc75247a9a64`
- Filesystem check: all five `e2fsck -f -n` passes completed without structural
  errors, but the captured source superblock carried an inherited
  `clean with errors` state
- Guest OS: Ubuntu 12.04.4 LTS
- Guest partition tooling: util-linux `sfdisk` 2.20.1, `partprobe`,
  `resize2fs`, `mkswap`, and `swapon`

The source image remains unchanged. Installation and destructive validation use
a derived copy.

## Target layout

The first boot reserves the original swap capacity and expands root into the
space between the captured root partition and that reserved tail.

| Partition | Type | Placement |
| --- | --- | --- |
| 1 | `0x83` bootable ext4 root | LBA 2,048 through the aligned swap tail |
| 2 | `0x05` extended | Final swap region plus one MiB for its EBR |
| 5 | `0x82` Linux swap | Final 8,142,848 or slightly more aligned sectors |

The recreated swap uses UUID `dc05c11c-afd3-417d-adf6-2c327b67b968` so it
matches the original cabinet configuration.

## Two-stage boot flow

### Stage 1 - partition table

1. Prove the root filesystem is partition 1 on the disk being changed.
2. Require a 512-byte logical sector, MBR layout, root start LBA 2,048, exact
   captured root size, and no partition other than partition 1.
3. Require enough target sectors for the captured root, a one-MiB EBR gap, and
   the original swap capacity.
4. Align the swap start down to a 2,048-sector boundary.
5. Back up every sector `sfdisk` will overwrite.
6. Rewrite only the partition layout, persist the expected stage-2 geometry,
   sync, and reboot. The filesystem is not resized in this stage.

### Stage 2 - filesystems

1. Prove partitions 1 and 5 match the geometry recorded by stage 1.
2. Grow the mounted ext4 root filesystem with `resize2fs`.
3. Create swap version 1 on partition 5 with the original UUID.
4. Restore the UUID-based swap entry in `/etc/fstab` and activate it.
5. Record completion and remove the script's executable bit so the boot hook is
   inert on later boots.

The original swap entry is commented in the derived image before its first
boot so Ubuntu 12.04 does not wait for a swap partition that has not yet been
recreated.

## Safety behavior

- Any identity, geometry, partition-count, capacity, tool, or state mismatch
  stops before a partition-table write.
- Stage 2 refuses to format a partition unless its start and size match the
  state written by stage 1.
- Logs and the pre-change partition-table backup remain under
  `/var/lib/roulette-storage-expand/`.
- Failure leaves the script executable so the reason remains visible and a
  guarded retry is possible; successful completion disables it.
- The script does not support GPT, non-512-byte logical sectors, an unexpected
  root partition, or a source layout containing extra partitions.

## Validation

1. Run `dash -n` against the script.
2. Use a disposable sparse MBR disk with the exact captured partition-1
   geometry to exercise both stages.
3. Confirm partition 1 grows, partition 5 is swap version 1 with the expected
   UUID, and the ext4 filesystem grows to partition 1.
4. Confirm rerunning a completed installation is inert and invalid layouts are
   rejected without alteration.
5. Install into a derived compact image, update its manifest digest, rerun
   compact-image preflight, and run `e2fsck -f -n` read-only.
6. Perform the first cabinet test only on a disposable, known-good drive while
   retaining the original verified image as recovery media.

## Non-goals

- No Odin UI or automatic post-flash integration in this phase.
- No Windows-side ext4 resizing.
- No attempt to make Ubuntu 12.04 boot from NVMe hardware it cannot already
  recognize.

## Implementation result

- Boot script: `scripts/roulette_expand_storage.sh`
- Disposable integration test: `scripts/test_roulette_expand_storage.sh`
- Derived test image: `D:\cards\Roulette Auto Expand TEST.compact.img`
- Derived manifest: `D:\cards\Roulette Auto Expand TEST.compact.json`
- Broken-clock configuration: `scripts/roulette_e2fsck.conf`
- Derived image SHA-1: `2fda253b290818c999f4d197c915f01e3c45c68c`
- Derived image SHA-256: `edf291e76ed3b1d9689cf2e0a788a8c88aee94f537e9e3bf85e9f935871f6c73`

The integration test proved both stages using a disposable 10-GiB sparse disk,
preserved the MBR disk identifier, grew ext4 to the new partition-1 boundary,
created swap version 1 with the original UUID, restored one canonical fstab
entry, disabled completed reruns, and rejected an unexpected extra partition
without changing its MBR. A separate chroot run completed the same two stages
with the image's actual Ubuntu 12.04 `sfdisk` 2.20.1, `resize2fs`, and `mkswap`.

Odin compact preflight accepted the final derived image and its updated
manifest. `e2fsck -f -n` completed all five passes without errors, and Ubuntu
12.04 `dash -n` accepted the exact script embedded in the image.

## Cabinet validation and fsck correction

The first physical cabinet boot stopped when Ubuntu 12.04 automatic fsck
returned status 4. An operator-approved forced repair cleared the condition;
the expansion then completed both stages and the game booted. Post-test
inspection proved the exact planned partition geometry, preserved root and
swap UUIDs, preserved the MBR disk identifier, one canonical swap entry,
successful completion state, and an inert `0644` boot script. The expanded
root passed all five offline `e2fsck -f -n` passes with exit code 0.

Both the untouched No Swap source and the initial derived image had the ext4
superblock state `clean with errors`, while reporting no structural errors.
The derived image also has filesystem timestamps newer than the cabinet's
2016 real-time clock. The corrected derived image now:

- has a clean ext4 superblock state;
- contains `/etc/e2fsck.conf` as root-owned mode `0644` with
  `broken_system_clock = true`;
- passes the image's own Ubuntu 12.04 `e2fsck` 1.42 with its effective time
  forced back to the cabinet's 2016 date; and
- passes Odin compact-image preflight against the updated manifest digest.

The pre-correction image and manifest are retained as adjacent
`.pre-fsck-fix-20260818.bak` files. The original No Swap source remains
unchanged.
