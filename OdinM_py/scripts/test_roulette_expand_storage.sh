#!/bin/bash

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
EXPAND_SCRIPT="$SCRIPT_DIR/roulette_expand_storage.sh"
WORK_DIR=$(mktemp -d /tmp/roulette-expand-test.XXXXXX)
ROOT_MOUNT="$WORK_DIR/root"
DISK_IMAGE="$WORK_DIR/disk.img"
LOOP_DEVICE=""

cleanup() {
    set +e
    if mountpoint -q "$ROOT_MOUNT"; then
        umount "$ROOT_MOUNT"
    fi
    if [[ -n "$LOOP_DEVICE" ]]; then
        losetup -d "$LOOP_DEVICE" 2>/dev/null || true
    fi
    case "$WORK_DIR" in
        /tmp/roulette-expand-test.*) rm -rf -- "$WORK_DIR" ;;
        *) printf 'Refusing to remove unexpected test path: %s\n' "$WORK_DIR" >&2 ;;
    esac
}
trap cleanup EXIT

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

partition_path() {
    local disk=$1
    local number=$2
    if [[ ${disk##*/} =~ [0-9]$ ]]; then
        printf '%sp%s\n' "$disk" "$number"
    else
        printf '%s%s\n' "$disk" "$number"
    fi
}

partition_field() {
    local dump=$1
    local partition=$2
    local field=$3
    grep "^${partition}[[:space:]]*:" "$dump" |
        sed -n "s/.*${field}=[[:space:]]*\([0-9][0-9]*\).*/\1/p"
}

make_initial_disk() {
    local image=$1
    truncate -s 10G "$image"
    printf '2048,7317504,83,*\n' | sfdisk "$image" >/dev/null
}

prepare_root_files() {
    local mount_path=$1
    mkdir -p "$mount_path/etc"
    printf '%s\n' \
        'NAME="Ubuntu"' \
        'VERSION="12.04.4 LTS, Precise Pangolin"' \
        'ID=ubuntu' \
        'VERSION_ID="12.04"' > "$mount_path/etc/os-release"
    cat > "$mount_path/etc/fstab" <<'EOF'
UUID=e4059dde-ca92-4c9a-99d7-bc75247a9a64 / ext4 defaults 0 1
UUID=dc05c11c-afd3-417d-adf6-2c327b67b968 none swap sw 0 0
EOF
    cat > "$mount_path/etc/rc.local" <<'EOF'
#!/bin/sh -e

exit 0
EOF
    chmod 755 "$mount_path/etc/rc.local"
}

[[ $(id -u) == 0 ]] || fail "run this test as root"
for command in awk blkid dash dd dumpe2fs e2fsck grep losetup mkfs.ext4 mount \
    mountpoint mkswap readlink resize2fs sed sfdisk sha256sum truncate udevadm umount; do
    command -v "$command" >/dev/null || fail "missing test command: $command"
done

dash -n "$EXPAND_SCRIPT"
mkdir -p "$ROOT_MOUNT"
make_initial_disk "$DISK_IMAGE"

LOOP_DEVICE=$(losetup --find --show --partscan "$DISK_IMAGE")
ROOT_PART=$(partition_path "$LOOP_DEVICE" 1)
mkfs.ext4 -q -F -U e4059dde-ca92-4c9a-99d7-bc75247a9a64 "$ROOT_PART"
mount "$ROOT_PART" "$ROOT_MOUNT"
prepare_root_files "$ROOT_MOUNT"

"$EXPAND_SCRIPT" --install "$ROOT_MOUNT"
INSTALLED_SCRIPT="$ROOT_MOUNT/usr/local/sbin/roulette-expand-storage"
[[ -x "$INSTALLED_SCRIPT" ]] || fail "installer did not create an executable script"
grep '^# roulette-expand pending: UUID=dc05c11c-afd3-417d-adf6-2c327b67b968 ' \
    "$ROOT_MOUNT/etc/fstab" >/dev/null || fail "installer did not defer the missing swap entry"
if grep '^UUID=dc05c11c-afd3-417d-adf6-2c327b67b968 ' "$ROOT_MOUNT/etc/fstab" >/dev/null; then
    fail "installer left the missing swap entry active"
fi
grep '^# roulette-storage-expand begin$' "$ROOT_MOUNT/etc/rc.local" >/dev/null ||
    fail "installer did not add the boot hook"

MBR_ID_BEFORE=$(dd if="$LOOP_DEVICE" bs=1 skip=440 count=4 status=none | sha256sum)
"$INSTALLED_SCRIPT" --test "$ROOT_PART" "$LOOP_DEVICE" "$ROOT_MOUNT"
[[ -f "$ROOT_MOUNT/var/lib/roulette-storage-expand/stage2.state" ]] ||
    fail "stage 1 did not persist stage-2 geometry"

umount "$ROOT_MOUNT"
losetup -d "$LOOP_DEVICE"
losetup --partscan "$LOOP_DEVICE" "$DISK_IMAGE"
udevadm settle
ROOT_PART=$(partition_path "$LOOP_DEVICE" 1)
SWAP_PART=$(partition_path "$LOOP_DEVICE" 5)
[[ -b "$SWAP_PART" ]] || fail "stage 1 did not create partition 5"
mount "$ROOT_PART" "$ROOT_MOUNT"
INSTALLED_SCRIPT="$ROOT_MOUNT/usr/local/sbin/roulette-expand-storage"

"$INSTALLED_SCRIPT" --test "$ROOT_PART" "$LOOP_DEVICE" "$ROOT_MOUNT"
[[ -f "$ROOT_MOUNT/var/lib/roulette-storage-expand/complete" ]] ||
    fail "stage 2 did not record completion"
[[ ! -x "$INSTALLED_SCRIPT" ]] || fail "stage 2 did not disable the installed script"
[[ $(blkid -s TYPE -o value "$SWAP_PART") == swap ]] || fail "partition 5 is not swap"
[[ $(blkid -s UUID -o value "$SWAP_PART") == dc05c11c-afd3-417d-adf6-2c327b67b968 ]] ||
    fail "partition 5 has the wrong swap UUID"
[[ $(grep -c '^UUID=dc05c11c-afd3-417d-adf6-2c327b67b968 none[[:space:]]*swap' \
    "$ROOT_MOUNT/etc/fstab") == 1 ]] || fail "fstab does not contain one active swap entry"

PARTITION_DUMP_BEFORE="$WORK_DIR/layout-before-rerun.txt"
PARTITION_DUMP_AFTER="$WORK_DIR/layout-after-rerun.txt"
sfdisk -d "$LOOP_DEVICE" > "$PARTITION_DUMP_BEFORE"
sh "$INSTALLED_SCRIPT" --test "$ROOT_PART" "$LOOP_DEVICE" "$ROOT_MOUNT"
sfdisk -d "$LOOP_DEVICE" > "$PARTITION_DUMP_AFTER"
cmp "$PARTITION_DUMP_BEFORE" "$PARTITION_DUMP_AFTER" || fail "completed rerun changed the layout"

ROOT_SECTORS=$(partition_field "$PARTITION_DUMP_AFTER" "$ROOT_PART" size)
SWAP_SECTORS=$(partition_field "$PARTITION_DUMP_AFTER" "$SWAP_PART" size)
[[ $ROOT_SECTORS -gt 7317504 ]] || fail "partition 1 did not grow"
[[ $SWAP_SECTORS -ge 8142848 ]] || fail "recreated swap is smaller than the original"
MBR_ID_AFTER=$(dd if="$LOOP_DEVICE" bs=1 skip=440 count=4 status=none | sha256sum)
[[ $MBR_ID_BEFORE == "$MBR_ID_AFTER" ]] || fail "disk identifier changed"

BLOCK_COUNT=$(dumpe2fs -h "$ROOT_PART" 2>/dev/null | awk -F: '/^Block count:/ {gsub(/ /, "", $2); print $2}')
BLOCK_SIZE=$(dumpe2fs -h "$ROOT_PART" 2>/dev/null | awk -F: '/^Block size:/ {gsub(/ /, "", $2); print $2}')
[[ $((BLOCK_COUNT * BLOCK_SIZE)) == $((ROOT_SECTORS * 512)) ]] ||
    fail "ext4 did not grow to the partition-1 boundary"

umount "$ROOT_MOUNT"
e2fsck -f -n "$ROOT_PART" >/dev/null

losetup -d "$LOOP_DEVICE"
LOOP_DEVICE=""
INVALID_IMAGE="$WORK_DIR/invalid.img"
truncate -s 10G "$INVALID_IMAGE"
sfdisk "$INVALID_IMAGE" >/dev/null <<'EOF'
2048,7317504,83,*
7319552,2048,83
EOF
LOOP_DEVICE=$(losetup --find --show --partscan "$INVALID_IMAGE")
ROOT_PART=$(partition_path "$LOOP_DEVICE" 1)
mkfs.ext4 -q -F -U e4059dde-ca92-4c9a-99d7-bc75247a9a64 "$ROOT_PART"
mount "$ROOT_PART" "$ROOT_MOUNT"
INVALID_MBR_BEFORE=$(dd if="$LOOP_DEVICE" bs=512 count=1 status=none | sha256sum)
set +e
"$EXPAND_SCRIPT" --test "$ROOT_PART" "$LOOP_DEVICE" "$ROOT_MOUNT" >/dev/null 2>&1
INVALID_STATUS=$?
set -e
[[ $INVALID_STATUS -ne 0 ]] || fail "unexpected partition layout was accepted"
INVALID_MBR_AFTER=$(dd if="$LOOP_DEVICE" bs=512 count=1 status=none | sha256sum)
[[ $INVALID_MBR_BEFORE == "$INVALID_MBR_AFTER" ]] ||
    fail "rejected layout changed the MBR"
grep 'unexpected partition already exists' \
    "$ROOT_MOUNT/var/lib/roulette-storage-expand/expand.log" >/dev/null ||
    fail "rejected layout did not record its reason"
umount "$ROOT_MOUNT"

printf 'PASS: root expanded to %s sectors; swap recreated at %s sectors; unexpected layouts remain unchanged.\n' \
    "$ROOT_SECTORS" "$SWAP_SECTORS"
