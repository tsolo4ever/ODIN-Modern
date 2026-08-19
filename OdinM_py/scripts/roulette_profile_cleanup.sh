#!/bin/sh

# ODINM-CLEANUP-METADATA-BEGIN
# format=odinm-cleanup-installer-v1
# installer_id=roulette-firefox-profile-backups
# install_contract=sh-install-root-v1
# linux_id=ubuntu
# linux_versions=12.04
# installed_path=/usr/local/sbin/roulette-profile-cleanup
# schedule_path=/etc/cron.d/roulette-profile-cleanup
# ODINM-CLEANUP-METADATA-END

# Reference contract for a future maintainer or coding model:
#
# - Keep the metadata block machine-readable: one unique key=value comment per
#   line, the exact begin/end markers, and no undeclared keys. linux_versions is
#   a comma-separated list of versions actually tested with the installer.
# - `script --install ROOT` is the only installation entry point. ROOT is an
#   offline ext4 staging mount, never the running Windows or Linux root. Install
#   the exact selected script at installed_path as root:root mode 0755 and the
#   schedule at schedule_path as root:root mode 0644.
# - Odin hashes the selected source before installation, mounts only its staged
#   filesystem read/write, invokes --install, then requires the installed copy
#   to have the same SHA-256 and the declared permissions. A changed or partial
#   installer must fail capture instead of publishing an image.
# - Normal execution must fail closed until every persistent path and symlink
#   invariant is proven. Dry run must never remove files or append to the log.
#   Scheduled mode must independently verify its calendar window.
# - Cleanup targets must be complete, exact names below a fixed profile root.
#   Never broaden the glob, follow cleanup-target symlinks, or remove persistent
#   backups. Keep locking and per-target size logging copy/pasteable.
# - A newer Linux release needs a tested expansion boot adapter before adding
#   its version here. Metadata parsing exists now; systemd expansion support is
#   deliberately not implied by this Ubuntu 12.04 cron/rc.local example.

set -u

PATH=/sbin:/bin:/usr/sbin:/usr/bin
export PATH

INSTALL_PATH="/usr/local/sbin/roulette-profile-cleanup"
CRON_PATH="/etc/cron.d/roulette-profile-cleanup"
PROFILE_ROOT=${ROULETTE_PROFILE_ROOT:-/home/huxley/.mozilla/firefox}
LOG_FILE=${ROULETTE_CLEANUP_LOG:-/var/log/roulette-profile-cleanup.log}
LOCK_DIR=${ROULETTE_CLEANUP_LOCK:-/var/run/roulette-profile-cleanup.lock}

DRY_RUN=0
SCHEDULED=0
INSTALL_ROOT=""

timestamp() {
    date -u '+%Y-%m-%dT%H:%M:%SZ'
}

log() {
    roulette_cleanup_line="$(timestamp) $*"
    printf '%s\n' "$roulette_cleanup_line"
    if [ "$DRY_RUN" -eq 0 ]; then
        printf '%s\n' "$roulette_cleanup_line" >> "$LOG_FILE"
    fi
}

fail() {
    log "FAILED: $*"
    exit 1
}

usage() {
    cat <<'EOF'
Usage:
  roulette-profile-cleanup [--dry-run] [--scheduled]
  roulette-profile-cleanup --install ROOT

Removes only timestamped Firefox crash-recovery profile backups after
validating the persistent Roulette profile layout. --scheduled exits without
cleanup unless the system date is the first Wednesday of the month.
EOF
}

root_path() {
    if [ "$INSTALL_ROOT" = "/" ]; then
        printf '%s\n' "$1"
    else
        printf '%s%s\n' "$INSTALL_ROOT" "$1"
    fi
}

install_cleanup() {
    [ -n "$INSTALL_ROOT" ] || fail "install root is missing"
    [ -d "$INSTALL_ROOT" ] || fail "install root does not exist: $INSTALL_ROOT"

    roulette_install_path=$(root_path "$INSTALL_PATH")
    roulette_cron_path=$(root_path "$CRON_PATH")
    mkdir -p "${roulette_install_path%/*}" "${roulette_cron_path%/*}" ||
        fail "could not create install directories"

    install -o root -g root -m 0755 "$0" "$roulette_install_path" ||
        fail "could not install $INSTALL_PATH"

    roulette_cron_tmp="${roulette_cron_path}.tmp.$$"
    umask 022
    {
        printf '%s\n' 'SHELL=/bin/sh'
        printf '%s\n' 'PATH=/sbin:/bin:/usr/sbin:/usr/bin'
        printf '%s\n' '0 5 1-7 * * root [ "$(date +\%u)" = "3" ] && /usr/local/sbin/roulette-profile-cleanup --scheduled'
    } > "$roulette_cron_tmp" || fail "could not write cron configuration"
    chown root:root "$roulette_cron_tmp" || fail "could not set cron ownership"
    chmod 0644 "$roulette_cron_tmp" || fail "could not set cron permissions"
    mv "$roulette_cron_tmp" "$roulette_cron_path" ||
        fail "could not publish cron configuration"

    printf 'Installed %s and %s into %s\n' "$INSTALL_PATH" "$CRON_PATH" "$INSTALL_ROOT"
}

scheduled_date_is_due() {
    if [ "${ROULETTE_CLEANUP_TEST_MODE:-0}" = "1" ] &&
       [ -n "${ROULETTE_CLEANUP_TEST_DATE:-}" ]; then
        roulette_day=${ROULETTE_CLEANUP_TEST_DATE%% *}
        roulette_weekday=${ROULETTE_CLEANUP_TEST_DATE#* }
    else
        roulette_day=$(date '+%d')
        roulette_weekday=$(date '+%u')
    fi

    roulette_day=${roulette_day#0}
    [ -n "$roulette_day" ] || roulette_day=0
    case "$roulette_day" in
        *[!0-9]*) return 1 ;;
    esac
    [ "$roulette_weekday" = "3" ] && [ "$roulette_day" -ge 1 ] &&
        [ "$roulette_day" -le 7 ]
}

validate_profile_layout() {
    [ -d "$PROFILE_ROOT" ] || fail "Firefox profile root is missing: $PROFILE_ROOT"
    [ -f "$PROFILE_ROOT/profiles.ini" ] || fail "profiles.ini is missing"
    grep -Fx 'Path=default' "$PROFILE_ROOT/profiles.ini" >/dev/null 2>&1 ||
        fail "profiles.ini does not contain Path=default"
    grep -Fx 'Path=touchscreen' "$PROFILE_ROOT/profiles.ini" >/dev/null 2>&1 ||
        fail "profiles.ini does not contain Path=touchscreen"

    for roulette_profile in default touchscreen; do
        roulette_link="$PROFILE_ROOT/$roulette_profile"
        [ -L "$roulette_link" ] || fail "$roulette_profile is not a symbolic link"
    done
    [ "$(readlink "$PROFILE_ROOT/default")" = "/run/shm/huxley-firefox-default" ] ||
        fail "default profile link target changed"
    [ "$(readlink "$PROFILE_ROOT/touchscreen")" = "/run/shm/huxley-firefox-touchscreen" ] ||
        fail "touchscreen profile link target changed"

    for roulette_profile in default-backup touchscreen-backup; do
        roulette_backup="$PROFILE_ROOT/$roulette_profile"
        [ -d "$roulette_backup" ] && [ ! -L "$roulette_backup" ] ||
            fail "$roulette_profile is not a persistent directory"
        roulette_first_file=$(find "$roulette_backup" -type f -print -quit 2>/dev/null)
        [ -n "$roulette_first_file" ] || fail "$roulette_profile is empty"
    done
}

is_cleanup_name() {
    printf '%s\n' "$1" |
        grep -Eq '^(default|touchscreen)-backup-crashrecovery-[0-9]{8}_[0-9]{6}$'
}

validate_candidates() {
    for roulette_candidate in \
        "$PROFILE_ROOT"/default-backup-crashrecovery-* \
        "$PROFILE_ROOT"/touchscreen-backup-crashrecovery-*
    do
        [ -e "$roulette_candidate" ] || [ -L "$roulette_candidate" ] || continue
        roulette_name=${roulette_candidate##*/}
        is_cleanup_name "$roulette_name" || continue
        [ -d "$roulette_candidate" ] && [ ! -L "$roulette_candidate" ] ||
            fail "cleanup target is not a real directory: $roulette_name"
    done
}

cleanup_candidates() {
    roulette_count=0
    roulette_total_bytes=0
    for roulette_candidate in \
        "$PROFILE_ROOT"/default-backup-crashrecovery-* \
        "$PROFILE_ROOT"/touchscreen-backup-crashrecovery-*
    do
        [ -d "$roulette_candidate" ] && [ ! -L "$roulette_candidate" ] || continue
        roulette_name=${roulette_candidate##*/}
        is_cleanup_name "$roulette_name" || continue
        roulette_kib=$(du -sk "$roulette_candidate" 2>/dev/null | awk '{print $1}')
        case "$roulette_kib" in
            ''|*[!0-9]*) fail "could not measure ${roulette_candidate##*/}" ;;
        esac
        roulette_bytes=$((roulette_kib * 1024))
        roulette_count=$((roulette_count + 1))
        roulette_total_bytes=$((roulette_total_bytes + roulette_bytes))

        if [ "$DRY_RUN" -eq 1 ]; then
            log "DRY RUN: would remove ${roulette_candidate##*/} (${roulette_bytes} bytes)"
        else
            rm -rf -- "$roulette_candidate" ||
                fail "could not remove ${roulette_candidate##*/}"
            [ ! -e "$roulette_candidate" ] ||
                fail "cleanup target remains: ${roulette_candidate##*/}"
            log "removed ${roulette_candidate##*/} (${roulette_bytes} bytes)"
        fi
    done

    if [ "$roulette_count" -eq 0 ]; then
        log "no timestamped Firefox crash-recovery backups found"
    elif [ "$DRY_RUN" -eq 1 ]; then
        log "DRY RUN: ${roulette_count} directories, ${roulette_total_bytes} bytes total"
    else
        log "completed: removed ${roulette_count} directories, ${roulette_total_bytes} bytes total"
    fi
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --dry-run)
            DRY_RUN=1
            ;;
        --scheduled)
            SCHEDULED=1
            ;;
        --install)
            shift
            [ "$#" -gt 0 ] || fail "--install requires a root path"
            INSTALL_ROOT=$1
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            usage >&2
            exit 2
            ;;
    esac
    shift
done

if [ -n "$INSTALL_ROOT" ]; then
    [ "$DRY_RUN" -eq 0 ] && [ "$SCHEDULED" -eq 0 ] ||
        fail "--install cannot be combined with cleanup options"
    install_cleanup
    exit 0
fi

if [ "$SCHEDULED" -eq 1 ] && ! scheduled_date_is_due; then
    exit 0
fi

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    fail "another cleanup is running or the lock is stale: $LOCK_DIR"
fi
release_lock() {
    rmdir "$LOCK_DIR" 2>/dev/null || true
}
trap release_lock EXIT
trap 'exit 1' HUP INT TERM

validate_profile_layout
validate_candidates
cleanup_candidates
