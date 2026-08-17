#!/bin/sh

set -u

PATH=/sbin:/bin:/usr/sbin:/usr/bin
export PATH

EXPECTED_ROOT_UUID="e4059dde-ca92-4c9a-99d7-bc75247a9a64"
SWAP_UUID="dc05c11c-afd3-417d-adf6-2c327b67b968"
EXPECTED_ROOT_START=2048
EXPECTED_ROOT_SECTORS=7317504
SWAP_SECTORS=8142848
ALIGNMENT_SECTORS=2048
MAX_MBR_SECTORS=4294967295
INSTALL_PATH="/usr/local/sbin/roulette-expand-storage"
STATE_PATH="/var/lib/roulette-storage-expand"

TEST_MODE=0
NO_REBOOT=0
ROOT_DIR="/"
ROOT_PART=""
DISK=""

root_path() {
    if [ "$ROOT_DIR" = "/" ]; then
        printf '%s\n' "$1"
    else
        printf '%s%s\n' "$ROOT_DIR" "$1"
    fi
}

timestamp() {
    date -u '+%Y-%m-%dT%H:%M:%SZ'
}

log() {
    roulette_log_line="$(timestamp) $*"
    printf '%s\n' "$roulette_log_line"
    if [ -n "${LOG_FILE:-}" ]; then
        printf '%s\n' "$roulette_log_line" >> "$LOG_FILE"
    fi
}

fail() {
    log "FAILED: $*"
    exit 1
}

is_uint() {
    case "$1" in
        ''|*[!0-9]*) return 1 ;;
        *) return 0 ;;
    esac
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "required command is missing: $1"
}

partition_path() {
    roulette_partition_number="$1"
    case "${DISK##*/}" in
        *[0-9]) printf '%sp%s\n' "$DISK" "$roulette_partition_number" ;;
        *) printf '%s%s\n' "$DISK" "$roulette_partition_number" ;;
    esac
}

partition_field() {
    roulette_dump_file="$1"
    roulette_partition="$2"
    roulette_field="$3"
    roulette_line=$(grep "^${roulette_partition}[[:space:]]*:" "$roulette_dump_file" | head -n 1 || true)
    printf '%s\n' "$roulette_line" |
        sed -n "s/.*${roulette_field}=[[:space:]]*\([0-9][0-9]*\).*/\1/p"
}

partition_id() {
    roulette_dump_file="$1"
    roulette_partition="$2"
    roulette_line=$(grep "^${roulette_partition}[[:space:]]*:" "$roulette_dump_file" | head -n 1 || true)
    printf '%s\n' "$roulette_line" |
        sed -n \
            -e 's/.*Id=[[:space:]]*\([0-9A-Fa-f][0-9A-Fa-f]*\).*/\1/p' \
            -e 's/.*type=[[:space:]]*\([0-9A-Fa-f][0-9A-Fa-f]*\).*/\1/p' |
        tr 'A-F' 'a-f'
}

partition_size_or_zero() {
    roulette_size=$(partition_field "$1" "$2" size)
    if [ -z "$roulette_size" ]; then
        printf '0\n'
    else
        printf '%s\n' "$roulette_size"
    fi
}

write_dump() {
    roulette_dump_target="$1"
    if ! sfdisk -d "$DISK" > "$roulette_dump_target" 2>> "$LOG_FILE"; then
        fail "could not read the MBR partition table from $DISK"
    fi
}

verify_expected_layout() {
    roulette_dump_file="$1"
    roulette_expected_root_size="$2"
    roulette_expected_extended_start="$3"
    roulette_expected_extended_size="$4"
    roulette_expected_swap_start="$5"
    roulette_expected_swap_size="$6"

    roulette_p1=$(partition_path 1)
    roulette_p2=$(partition_path 2)
    roulette_p3=$(partition_path 3)
    roulette_p4=$(partition_path 4)
    roulette_p5=$(partition_path 5)

    [ "$(partition_field "$roulette_dump_file" "$roulette_p1" start)" = "$EXPECTED_ROOT_START" ] || return 1
    [ "$(partition_field "$roulette_dump_file" "$roulette_p1" size)" = "$roulette_expected_root_size" ] || return 1
    [ "$(partition_id "$roulette_dump_file" "$roulette_p1")" = "83" ] || return 1
    grep "^${roulette_p1}[[:space:]]*:.*bootable" "$roulette_dump_file" >/dev/null 2>&1 || return 1

    [ "$(partition_field "$roulette_dump_file" "$roulette_p2" start)" = "$roulette_expected_extended_start" ] || return 1
    [ "$(partition_field "$roulette_dump_file" "$roulette_p2" size)" = "$roulette_expected_extended_size" ] || return 1
    [ "$(partition_id "$roulette_dump_file" "$roulette_p2")" = "5" ] || return 1
    [ "$(partition_size_or_zero "$roulette_dump_file" "$roulette_p3")" = "0" ] || return 1
    [ "$(partition_size_or_zero "$roulette_dump_file" "$roulette_p4")" = "0" ] || return 1

    [ "$(partition_field "$roulette_dump_file" "$roulette_p5" start)" = "$roulette_expected_swap_start" ] || return 1
    [ "$(partition_field "$roulette_dump_file" "$roulette_p5" size)" = "$roulette_expected_swap_size" ] || return 1
    [ "$(partition_id "$roulette_dump_file" "$roulette_p5")" = "82" ] || return 1
}

validate_root_device() {
    [ "$(id -u)" = "0" ] || fail "must run as root"
    [ -b "$ROOT_PART" ] || fail "root partition is not a block device: $ROOT_PART"
    [ -b "$DISK" ] || fail "root disk is not a block device: $DISK"

    roulette_root_name=${ROOT_PART##*/}
    roulette_disk_name=${DISK##*/}
    roulette_root_sys=$(readlink -f "/sys/class/block/$roulette_root_name" 2>/dev/null || true)
    [ -n "$roulette_root_sys" ] || fail "cannot resolve sysfs identity for $ROOT_PART"
    roulette_parent_name=$(basename "$(dirname "$roulette_root_sys")")
    [ "$roulette_parent_name" = "$roulette_disk_name" ] ||
        fail "$ROOT_PART is not partition 1 on $DISK"

    roulette_expected_p1=$(partition_path 1)
    [ "$ROOT_PART" = "$roulette_expected_p1" ] ||
        fail "root must be partition 1; found $ROOT_PART"

    roulette_sector_size=$(cat "/sys/class/block/$roulette_disk_name/queue/logical_block_size" 2>/dev/null || true)
    [ "$roulette_sector_size" = "512" ] ||
        fail "logical sector size must be 512 bytes; found ${roulette_sector_size:-unknown}"

    roulette_root_type=$(blkid -s TYPE -o value "$ROOT_PART" 2>/dev/null || true)
    [ "$roulette_root_type" = "ext4" ] ||
        fail "root partition must be ext4; found ${roulette_root_type:-unknown}"
    roulette_root_uuid=$(blkid -s UUID -o value "$ROOT_PART" 2>/dev/null || true)
    [ "$roulette_root_uuid" = "$EXPECTED_ROOT_UUID" ] ||
        fail "unexpected root filesystem UUID: ${roulette_root_uuid:-unknown}"
}

detect_production_root() {
    require_command findmnt
    roulette_source=$(findmnt -n -o SOURCE / 2>/dev/null || true)
    [ -n "$roulette_source" ] || fail "could not identify the mounted root device"
    ROOT_PART=$(readlink -f "$roulette_source" 2>/dev/null || true)
    [ -n "$ROOT_PART" ] || fail "could not resolve the mounted root device"

    roulette_root_name=${ROOT_PART##*/}
    case "$roulette_root_name" in
        *p1) roulette_disk_name=${roulette_root_name%p1} ;;
        *1) roulette_disk_name=${roulette_root_name%1} ;;
        *) fail "root is not partition 1: $ROOT_PART" ;;
    esac
    DISK="/dev/$roulette_disk_name"
}

read_state_value() {
    roulette_state_key="$1"
    sed -n "s/^${roulette_state_key}=//p" "$STAGE2_FILE" | head -n 1
}

load_stage2_state() {
    STATE_DISK_NAME=$(read_state_value disk_name)
    STATE_ROOT_SIZE=$(read_state_value root_size)
    STATE_EXTENDED_START=$(read_state_value extended_start)
    STATE_EXTENDED_SIZE=$(read_state_value extended_size)
    STATE_SWAP_START=$(read_state_value swap_start)
    STATE_SWAP_SIZE=$(read_state_value swap_size)

    [ "$STATE_DISK_NAME" = "${DISK##*/}" ] ||
        fail "stage-2 disk identity does not match the current root disk"
    for roulette_value in "$STATE_ROOT_SIZE" "$STATE_EXTENDED_START" \
        "$STATE_EXTENDED_SIZE" "$STATE_SWAP_START" "$STATE_SWAP_SIZE"; do
        is_uint "$roulette_value" || fail "stage-2 geometry state is invalid"
    done
}

install_into_root() {
    ROOT_DIR="$1"
    [ "$(id -u)" = "0" ] || fail "installer must run as root"
    [ "$ROOT_DIR" != "/" ] || fail "installer requires an offline mounted image root"
    [ -d "$ROOT_DIR/etc" ] || fail "mounted root does not contain /etc"
    [ -f "$ROOT_DIR/etc/os-release" ] || fail "mounted root has no os-release"
    grep '^VERSION_ID="12\.04"$' "$ROOT_DIR/etc/os-release" >/dev/null 2>&1 ||
        fail "mounted root is not Ubuntu 12.04"

    roulette_installed="$ROOT_DIR$INSTALL_PATH"
    roulette_state="$ROOT_DIR$STATE_PATH"
    roulette_fstab="$ROOT_DIR/etc/fstab"
    roulette_rc_local="$ROOT_DIR/etc/rc.local"
    [ -f "$roulette_fstab" ] || fail "mounted root has no /etc/fstab"
    [ -f "$roulette_rc_local" ] || fail "mounted root has no /etc/rc.local"

    mkdir -p "$(dirname "$roulette_installed")" "$roulette_state"
    chmod 700 "$roulette_state"
    cp "$0" "$roulette_installed"
    chmod 755 "$roulette_installed"

    if [ ! -f "$roulette_state/fstab.before-install" ]; then
        cp "$roulette_fstab" "$roulette_state/fstab.before-install"
    fi
    if [ ! -f "$roulette_state/rc.local.before-install" ]; then
        cp "$roulette_rc_local" "$roulette_state/rc.local.before-install"
    fi

    roulette_tmp="$roulette_fstab.roulette.$$"
    awk -v uuid="$SWAP_UUID" '
        index($0, "UUID=" uuid) && $0 !~ /^[[:space:]]*#/ && $0 ~ /[[:space:]]swap[[:space:]]/ {
            print "# roulette-expand pending: " $0
            next
        }
        { print }
    ' "$roulette_fstab" > "$roulette_tmp"
    chmod 644 "$roulette_tmp"
    mv "$roulette_tmp" "$roulette_fstab"

    if ! grep '^# roulette-storage-expand begin$' "$roulette_rc_local" >/dev/null 2>&1; then
        roulette_tmp="$roulette_rc_local.roulette.$$"
        awk '
            !inserted && $0 == "exit 0" {
                print "# roulette-storage-expand begin"
                print "if [ -x /usr/local/sbin/roulette-expand-storage ]; then"
                print "    /usr/local/sbin/roulette-expand-storage"
                print "fi"
                print "# roulette-storage-expand end"
                print ""
                inserted=1
            }
            { print }
            END {
                if (!inserted) {
                    print ""
                    print "# roulette-storage-expand begin"
                    print "if [ -x /usr/local/sbin/roulette-expand-storage ]; then"
                    print "    /usr/local/sbin/roulette-expand-storage"
                    print "fi"
                    print "# roulette-storage-expand end"
                }
            }
        ' "$roulette_rc_local" > "$roulette_tmp"
        chmod 755 "$roulette_tmp"
        mv "$roulette_tmp" "$roulette_rc_local"
    fi

    sync
    printf 'Installed %s into %s\n' "$INSTALL_PATH" "$ROOT_DIR"
}

stage1_partition_disk() {
    roulette_root_name=${ROOT_PART##*/}
    roulette_disk_name=${DISK##*/}
    roulette_root_start=$(cat "/sys/class/block/$roulette_root_name/start" 2>/dev/null || true)
    roulette_current_root_size=$(cat "/sys/class/block/$roulette_root_name/size" 2>/dev/null || true)
    [ "$roulette_root_start" = "$EXPECTED_ROOT_START" ] ||
        fail "root start LBA changed: ${roulette_root_start:-unknown}"
    [ "$roulette_current_root_size" = "$EXPECTED_ROOT_SECTORS" ] ||
        fail "root partition size is not the captured no-swap size"

    write_dump "$CURRENT_DUMP"
    roulette_p1=$(partition_path 1)
    roulette_p2=$(partition_path 2)
    roulette_p3=$(partition_path 3)
    roulette_p4=$(partition_path 4)
    roulette_p5=$(partition_path 5)
    [ "$(partition_field "$CURRENT_DUMP" "$roulette_p1" start)" = "$EXPECTED_ROOT_START" ] ||
        fail "partition-table root start does not match the capture"
    [ "$(partition_field "$CURRENT_DUMP" "$roulette_p1" size)" = "$EXPECTED_ROOT_SECTORS" ] ||
        fail "partition-table root size does not match the capture"
    [ "$(partition_id "$CURRENT_DUMP" "$roulette_p1")" = "83" ] ||
        fail "partition 1 is not Linux type 0x83"
    grep "^${roulette_p1}[[:space:]]*:.*bootable" "$CURRENT_DUMP" >/dev/null 2>&1 ||
        fail "partition 1 is not bootable"
    for roulette_extra in "$roulette_p2" "$roulette_p3" "$roulette_p4" "$roulette_p5"; do
        [ "$(partition_size_or_zero "$CURRENT_DUMP" "$roulette_extra")" = "0" ] ||
            fail "an unexpected partition already exists: $roulette_extra"
    done

    roulette_total_sectors=$(cat "/sys/class/block/$roulette_disk_name/size" 2>/dev/null || true)
    is_uint "$roulette_total_sectors" || fail "target sector count is unavailable"
    [ "$roulette_total_sectors" -le "$MAX_MBR_SECTORS" ] ||
        fail "target exceeds the safe MBR sector limit"

    roulette_swap_start=$((roulette_total_sectors - SWAP_SECTORS))
    roulette_swap_start=$((roulette_swap_start / ALIGNMENT_SECTORS * ALIGNMENT_SECTORS))
    roulette_extended_start=$((roulette_swap_start - ALIGNMENT_SECTORS))
    roulette_new_root_size=$((roulette_extended_start - EXPECTED_ROOT_START))
    roulette_extended_size=$((roulette_total_sectors - roulette_extended_start))
    roulette_swap_size=$((roulette_total_sectors - roulette_swap_start))

    [ "$roulette_new_root_size" -ge "$EXPECTED_ROOT_SECTORS" ] ||
        fail "target is too small for the captured root plus recreated swap"
    [ "$roulette_extended_start" -gt $((EXPECTED_ROOT_START + EXPECTED_ROOT_SECTORS - 1)) ] ||
        fail "calculated extended partition overlaps the captured root filesystem"

    cat > "$LAYOUT_FILE" <<EOF
unit: sectors

$roulette_p1 : start= $EXPECTED_ROOT_START, size= $roulette_new_root_size, Id=83, bootable
$roulette_p2 : start= $roulette_extended_start, size= $roulette_extended_size, Id= 5
$roulette_p3 : start= 0, size= 0, Id= 0
$roulette_p4 : start= 0, size= 0, Id= 0
$roulette_p5 : start= $roulette_swap_start, size= $roulette_swap_size, Id=82
EOF

    cat > "$STAGE2_FILE" <<EOF
disk_name=$roulette_disk_name
root_size=$roulette_new_root_size
extended_start=$roulette_extended_start
extended_size=$roulette_extended_size
swap_start=$roulette_swap_start
swap_size=$roulette_swap_size
EOF
    chmod 600 "$STAGE2_FILE"

    dd if="$DISK" of="$STATE_DIR/mbr-before.bin" bs=512 count=1 2>> "$LOG_FILE" ||
        fail "could not back up the source MBR"
    dd if="$DISK" of="$STATE_DIR/extended-start-before.bin" bs=512 \
        skip="$roulette_extended_start" count=1 2>> "$LOG_FILE" ||
        fail "could not back up the future extended-partition sector"
    sync

    log "Stage 1: expanding partition 1 and reserving $roulette_swap_size sectors for swap."
    roulette_sfdisk_status=0
    sfdisk --force --no-reread --Linux "$DISK" < "$LAYOUT_FILE" >> "$LOG_FILE" 2>&1 ||
        roulette_sfdisk_status=$?
    dd if="$STATE_DIR/mbr-before.bin" of="$DISK" bs=1 skip=440 seek=440 \
        count=6 conv=notrunc 2>> "$LOG_FILE" ||
        fail "could not restore the original MBR disk identifier"
    sync
    write_dump "$POST_WRITE_DUMP"
    if ! verify_expected_layout "$POST_WRITE_DUMP" "$roulette_new_root_size" \
        "$roulette_extended_start" "$roulette_extended_size" \
        "$roulette_swap_start" "$roulette_swap_size"; then
        rm -f "$STAGE2_FILE"
        fail "partition-table write did not produce the exact planned layout"
    fi
    if [ "$roulette_sfdisk_status" -ne 0 ]; then
        log "sfdisk returned $roulette_sfdisk_status, but raw read-back proved the planned layout."
    fi
    sync
    log "Stage 1 complete; the new partition table requires a reboot."
    if [ "$NO_REBOOT" = "1" ]; then
        return 0
    fi
    reboot
    return 0
}

restore_swap_fstab_entry() {
    roulette_fstab=$(root_path /etc/fstab)
    [ -f "$roulette_fstab" ] || fail "/etc/fstab is missing"
    roulette_tmp="$roulette_fstab.roulette.$$"
    awk -v uuid="$SWAP_UUID" '
        index($0, "UUID=" uuid) { next }
        { print }
    ' "$roulette_fstab" > "$roulette_tmp"
    printf 'UUID=%s none            swap    sw              0       0\n' "$SWAP_UUID" >> "$roulette_tmp"
    chmod 644 "$roulette_tmp"
    mv "$roulette_tmp" "$roulette_fstab"
    roulette_swap_lines=$(grep -c "^UUID=${SWAP_UUID}[[:space:]]" "$roulette_fstab" || true)
    [ "$roulette_swap_lines" = "1" ] || fail "fstab swap entry was not restored exactly once"
}

stage2_create_filesystems() {
    load_stage2_state
    roulette_root_name=${ROOT_PART##*/}
    roulette_swap_part=$(partition_path 5)
    roulette_swap_name=${roulette_swap_part##*/}

    roulette_root_start=$(cat "/sys/class/block/$roulette_root_name/start" 2>/dev/null || true)
    roulette_root_size=$(cat "/sys/class/block/$roulette_root_name/size" 2>/dev/null || true)
    roulette_swap_start=$(cat "/sys/class/block/$roulette_swap_name/start" 2>/dev/null || true)
    roulette_swap_size=$(cat "/sys/class/block/$roulette_swap_name/size" 2>/dev/null || true)
    [ "$roulette_root_start" = "$EXPECTED_ROOT_START" ] || fail "stage-2 root start mismatch"
    [ "$roulette_root_size" = "$STATE_ROOT_SIZE" ] ||
        fail "kernel has not loaded the expanded partition-1 size; reboot is still required"
    [ "$roulette_swap_start" = "$STATE_SWAP_START" ] || fail "stage-2 swap start mismatch"
    [ "$roulette_swap_size" = "$STATE_SWAP_SIZE" ] || fail "stage-2 swap size mismatch"

    write_dump "$CURRENT_DUMP"
    if ! verify_expected_layout "$CURRENT_DUMP" "$STATE_ROOT_SIZE" \
        "$STATE_EXTENDED_START" "$STATE_EXTENDED_SIZE" \
        "$STATE_SWAP_START" "$STATE_SWAP_SIZE"; then
        fail "stage-2 partition layout does not match the recorded plan"
    fi

    log "Stage 2: growing ext4 on $ROOT_PART."
    resize2fs "$ROOT_PART" >> "$LOG_FILE" 2>&1 || fail "resize2fs failed"
    log "Stage 2: recreating swap on $roulette_swap_part."
    mkswap -U "$SWAP_UUID" "$roulette_swap_part" >> "$LOG_FILE" 2>&1 ||
        fail "mkswap failed"
    roulette_type=$(blkid -s TYPE -o value "$roulette_swap_part" 2>/dev/null || true)
    roulette_uuid=$(blkid -s UUID -o value "$roulette_swap_part" 2>/dev/null || true)
    [ "$roulette_type" = "swap" ] || fail "partition 5 did not probe as swap"
    [ "$roulette_uuid" = "$SWAP_UUID" ] || fail "partition 5 swap UUID mismatch"

    restore_swap_fstab_entry
    if [ "$TEST_MODE" = "0" ]; then
        swapon "$roulette_swap_part" >> "$LOG_FILE" 2>&1 || fail "swapon failed"
        grep "^${roulette_swap_part}[[:space:]]" /proc/swaps >/dev/null 2>&1 ||
            fail "swap was not listed as active after swapon"
    else
        log "Test mode: swap activation skipped."
    fi

    cat > "$COMPLETE_FILE" <<EOF
completed_at=$(timestamp)
disk=$DISK
root_partition=$ROOT_PART
root_sectors=$STATE_ROOT_SIZE
swap_partition=$roulette_swap_part
swap_sectors=$STATE_SWAP_SIZE
swap_uuid=$SWAP_UUID
EOF
    chmod 600 "$COMPLETE_FILE"
    roulette_installed=$(root_path "$INSTALL_PATH")
    if [ -f "$roulette_installed" ]; then
        chmod a-x "$roulette_installed"
    fi
    sync
    log "Storage expansion complete; the boot hook is now inert."
}

case "${1:-}" in
    --install)
        [ "$#" = "2" ] || fail "usage: $0 --install <mounted-root>"
        install_into_root "$2"
        exit 0
        ;;
    --test)
        [ "$#" = "4" ] || fail "usage: $0 --test <root-partition> <disk> <mounted-root>"
        TEST_MODE=1
        NO_REBOOT=1
        ROOT_PART="$2"
        DISK="$3"
        ROOT_DIR="$4"
        case "$DISK" in
            /dev/loop[0-9]*) ;;
            *) fail "test mode only accepts a loop device" ;;
        esac
        [ "$ROOT_DIR" != "/" ] || fail "test mode requires an isolated mounted root"
        ;;
    '')
        detect_production_root
        ;;
    *)
        fail "unsupported argument: $1"
        ;;
esac

for roulette_command in awk blkid cat chmod cp date dd grep head id mkdir mv \
    readlink resize2fs sed sfdisk sync tr; do
    require_command "$roulette_command"
done

validate_root_device

STATE_DIR=$(root_path "$STATE_PATH")
mkdir -p "$STATE_DIR"
chmod 700 "$STATE_DIR"
LOG_FILE="$STATE_DIR/expand.log"
STAGE2_FILE="$STATE_DIR/stage2.state"
COMPLETE_FILE="$STATE_DIR/complete"
CURRENT_DUMP="$STATE_DIR/current.sfdisk"
POST_WRITE_DUMP="$STATE_DIR/post-write.sfdisk"
LAYOUT_FILE="$STATE_DIR/planned.sfdisk"

if [ -f "$COMPLETE_FILE" ]; then
    log "Storage expansion was already completed; no action taken."
    roulette_installed=$(root_path "$INSTALL_PATH")
    [ ! -f "$roulette_installed" ] || chmod a-x "$roulette_installed"
    exit 0
fi

if [ -f "$STAGE2_FILE" ]; then
    for roulette_command in mkswap; do
        require_command "$roulette_command"
    done
    if [ "$TEST_MODE" = "0" ]; then
        require_command swapon
    fi
    stage2_create_filesystems
else
    stage1_partition_disk
fi
