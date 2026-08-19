#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
SCRIPT="$SCRIPT_DIR/roulette_profile_cleanup.sh"
TEST_ROOT=$(mktemp -d)
trap 'rm -rf -- "$TEST_ROOT"' EXIT

ROOT="$TEST_ROOT/root"
PROFILE_ROOT="$ROOT/home/huxley/.mozilla/firefox"
LOG_FILE="$ROOT/var/log/roulette-profile-cleanup.log"
LOCK_DIR="$ROOT/var/run/roulette-profile-cleanup.lock"

mkdir -p "$PROFILE_ROOT/default-backup" "$PROFILE_ROOT/touchscreen-backup"
mkdir -p "$ROOT/etc/cron.d" "$ROOT/usr/local/sbin" "$ROOT/var/log" "$ROOT/var/run"
printf 'default persistent data\n' > "$PROFILE_ROOT/default-backup/prefs.js"
printf 'touchscreen persistent data\n' > "$PROFILE_ROOT/touchscreen-backup/prefs.js"
cat > "$PROFILE_ROOT/profiles.ini" <<'EOF'
[General]
StartWithLastProfile=1

[Profile0]
Name=touchscreen
IsRelative=1
Path=touchscreen
Default=1

[Profile1]
Name=default
IsRelative=1
Path=default
EOF
ln -s /run/shm/huxley-firefox-default "$PROFILE_ROOT/default"
ln -s /run/shm/huxley-firefox-touchscreen "$PROFILE_ROOT/touchscreen"

/bin/sh "$SCRIPT" --install "$ROOT"
cmp "$SCRIPT" "$ROOT/usr/local/sbin/roulette-profile-cleanup"
[[ $(stat -c '%a' "$ROOT/usr/local/sbin/roulette-profile-cleanup") == 755 ]]
[[ $(stat -c '%a' "$ROOT/etc/cron.d/roulette-profile-cleanup") == 644 ]]
grep -Fx '0 5 1-7 * * root [ "$(date +\%u)" = "3" ] && /usr/local/sbin/roulette-profile-cleanup --scheduled' \
    "$ROOT/etc/cron.d/roulette-profile-cleanup" >/dev/null

make_candidate() {
    local name=$1
    mkdir -p "$PROFILE_ROOT/$name"
    printf 'historical profile data\n' > "$PROFILE_ROOT/$name/places.sqlite"
}

run_cleanup() {
    ROULETTE_PROFILE_ROOT="$PROFILE_ROOT" \
    ROULETTE_CLEANUP_LOG="$LOG_FILE" \
    ROULETTE_CLEANUP_LOCK="$LOCK_DIR" \
        /bin/sh "$SCRIPT" "$@"
}

make_candidate default-backup-crashrecovery-20161122_131808
make_candidate touchscreen-backup-crashrecovery-20161122_131807
make_candidate default-backup-crashrecovery-bad
run_cleanup --dry-run
[[ -d "$PROFILE_ROOT/default-backup-crashrecovery-20161122_131808" ]]
[[ -d "$PROFILE_ROOT/touchscreen-backup-crashrecovery-20161122_131807" ]]
[[ ! -e "$LOG_FILE" ]]

run_cleanup
[[ ! -e "$PROFILE_ROOT/default-backup-crashrecovery-20161122_131808" ]]
[[ ! -e "$PROFILE_ROOT/touchscreen-backup-crashrecovery-20161122_131807" ]]
[[ -d "$PROFILE_ROOT/default-backup-crashrecovery-bad" ]]
[[ -d "$PROFILE_ROOT/default-backup" ]]
[[ -d "$PROFILE_ROOT/touchscreen-backup" ]]
[[ -L "$PROFILE_ROOT/default" ]]
[[ -L "$PROFILE_ROOT/touchscreen" ]]
grep -F 'completed: removed 2 directories' "$LOG_FILE" >/dev/null

make_candidate default-backup-crashrecovery-20170101_010101
ROULETTE_CLEANUP_TEST_MODE=1 ROULETTE_CLEANUP_TEST_DATE='02 2' run_cleanup --scheduled
[[ -d "$PROFILE_ROOT/default-backup-crashrecovery-20170101_010101" ]]
ROULETTE_CLEANUP_TEST_MODE=1 ROULETTE_CLEANUP_TEST_DATE='04 3' run_cleanup --scheduled
[[ ! -e "$PROFILE_ROOT/default-backup-crashrecovery-20170101_010101" ]]

make_candidate touchscreen-backup-crashrecovery-20170201_050000
mv "$PROFILE_ROOT/default-backup" "$PROFILE_ROOT/default-backup.missing"
if run_cleanup; then
    printf '%s\n' 'cleanup unexpectedly succeeded without default-backup' >&2
    exit 1
fi
[[ -d "$PROFILE_ROOT/touchscreen-backup-crashrecovery-20170201_050000" ]]
mv "$PROFILE_ROOT/default-backup.missing" "$PROFILE_ROOT/default-backup"

printf '%s\n' 'roulette profile cleanup tests passed'
