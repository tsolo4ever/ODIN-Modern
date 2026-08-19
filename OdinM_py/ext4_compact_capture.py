"""Filesystem-aware ext4 compact capture through WSL e2fsprogs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from compact_image import (
    EXT4_PARTITION_TYPE,
    SWAP_PARTITION_TYPE,
    CleanupRecord,
    CompactImageError,
    ExpansionRecord,
    Ext4FilesystemRecord,
    OmittedSwapRecord,
    PartitionExtent,
    build_ext4_manifest,
    make_ext4_only_layout,
    minimum_target_bytes,
    parse_cleanup_installer_record,
    parse_mbr_layout,
    patch_ext4_only_mbr,
)

import pyimager


BUFFER_BYTES = 64 << 20
ALIGNMENT_SECTORS = 2048
COPY_CHUNK_BYTES = 8 << 20
UUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
WORK_PATTERN = re.compile(r"^/tmp/odinm-ext4-[0-9a-f]{32}$")


class Ext4CompactCaptureError(RuntimeError):
    """The source cannot be compacted without violating a safety gate."""


class Ext4CompactCaptureCancelled(Ext4CompactCaptureError):
    """The operator cancelled compact capture."""


@dataclass(frozen=True)
class _WslPartition:
    path: str
    number: int
    start_lba: int
    sector_count: int
    filesystem: str
    filesystem_version: str
    uuid: str
    mounted: bool


@dataclass(frozen=True)
class _Ext4Metadata:
    uuid: str
    block_size: int
    block_count: int
    state: str


def _creation_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def _run(
    args: list[str],
    *,
    should_cancel: Callable[[], bool] | None = None,
    allowed_codes: set[int] | None = None,
) -> str:
    allowed = allowed_codes or {0}
    process = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_creation_flags(),
    )
    while True:
        try:
            output, _ = process.communicate(timeout=0.25)
            break
        except subprocess.TimeoutExpired:
            if should_cancel is not None and should_cancel():
                process.terminate()
                try:
                    process.communicate(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
                raise Ext4CompactCaptureCancelled("Compact capture was cancelled.") from None
    if process.returncode not in allowed:
        detail = output.strip() or f"exit code {process.returncode}"
        raise Ext4CompactCaptureError(f"Command failed: {detail}")
    return output


def _run_wsl(
    args: list[str],
    *,
    should_cancel: Callable[[], bool] | None = None,
) -> str:
    return _run(
        ["wsl.exe", "-u", "root", "--", *args],
        should_cancel=should_cancel,
    )


def check_prerequisites() -> None:
    if os.name != "nt":
        raise Ext4CompactCaptureError("Ext4 compact capture is available only on Windows.")
    try:
        _run_wsl_script(
            "for tool in e2image e2fsck resize2fs dumpe2fs blkid lsblk mount "
            'umount truncate sha256sum blockdev; do command -v "$tool" >/dev/null || '
            "exit 20; done\n"
        )
    except FileNotFoundError as exc:
        raise Ext4CompactCaptureError("WSL is not installed or wsl.exe is unavailable.") from exc
    except Ext4CompactCaptureError as exc:
        raise Ext4CompactCaptureError(
            "WSL must provide e2image, e2fsck, resize2fs, and the standard Linux disk tools."
        ) from exc


def _asset_path(name: str) -> Path:
    roots: list[Path] = []
    packaged_root = getattr(sys, "_MEIPASS", None)
    if packaged_root:
        roots.append(Path(packaged_root))
    roots.append(Path(__file__).resolve().parent)
    for root in roots:
        candidate = root / "scripts" / name
        if candidate.is_file():
            return candidate
    raise Ext4CompactCaptureError(f"Required compact-capture asset is missing: {name}")


def parse_cleanup_installer(path: str | os.PathLike[str]) -> CleanupRecord:
    try:
        return parse_cleanup_installer_record(path)
    except CompactImageError as exc:
        raise Ext4CompactCaptureError(str(exc)) from exc


def _wsl_path(path: Path) -> str:
    windows_path = str(path.resolve()).replace("\\", "/")
    return _run_wsl(["wslpath", "-a", "-u", windows_path]).strip()


def _run_wsl_script(
    source: str,
    args: list[str] | None = None,
    *,
    should_cancel: Callable[[], bool] | None = None,
) -> str:
    descriptor, name = tempfile.mkstemp(prefix="odinm-wsl-", suffix=".sh")
    os.close(descriptor)
    path = Path(name)
    try:
        path.write_text("#!/bin/sh\nset -u\n" + source, encoding="utf-8", newline="\n")
        return _run_wsl(
            ["sh", _wsl_path(path), *(args or [])],
            should_cancel=should_cancel,
        )
    finally:
        path.unlink(missing_ok=True)


def _wsl_disks() -> dict[str, int]:
    payload = json.loads(
        _run_wsl(["lsblk", "--json", "--bytes", "--nodeps", "--output", "PATH,SIZE,TYPE"])
    )
    disks: dict[str, int] = {}
    for item in payload.get("blockdevices") or []:
        if str(item.get("type") or "") == "disk":
            disks[str(item.get("path") or "")] = int(item.get("size") or 0)
    return disks


def _wsl_mbr_sha256(device_path: str) -> str:
    output = _run_wsl_script(
        'dd if="$1" bs=512 count=1 status=none | sha256sum\n',
        [device_path],
    )
    return output.split()[0].lower()


def _wait_for_attached_disk(before: dict[str, int], disk_size: int, mbr_sha256: str) -> str:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        after = _wsl_disks()
        candidates = [
            path for path, size in after.items() if path not in before and size == disk_size
        ]
        matches = [path for path in candidates if _wsl_mbr_sha256(path) == mbr_sha256]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise Ext4CompactCaptureError("WSL exposed more than one matching source disk.")
        time.sleep(0.25)
    raise Ext4CompactCaptureError("WSL did not expose the selected physical disk.")


def _set_source_read_only(device_path: str) -> None:
    _run_wsl_script(
        'blockdev --setro "$1"\n[ "$(blockdev --getro "$1")" = "1" ]\n',
        [device_path],
    )


def _flatten_devices(items: list[dict]) -> list[dict]:
    flattened: list[dict] = []
    for item in items:
        flattened.append(item)
        flattened.extend(_flatten_devices(item.get("children") or []))
    return flattened


def _wsl_partitions(disk_path: str, sector_size: int) -> dict[int, _WslPartition]:
    payload = json.loads(
        _run_wsl(
            [
                "lsblk",
                "--json",
                "--bytes",
                "--output",
                "PATH,TYPE,PARTN,START,SIZE,FSTYPE,FSVER,UUID,MOUNTPOINTS",
                disk_path,
            ]
        )
    )
    partitions: dict[int, _WslPartition] = {}
    for item in _flatten_devices(payload.get("blockdevices") or []):
        if str(item.get("type") or "") != "part":
            continue
        number = int(item.get("partn") or 0)
        mounts = item.get("mountpoints") or []
        if isinstance(mounts, str):
            mounts = [mounts]
        partitions[number] = _WslPartition(
            path=str(item.get("path") or ""),
            number=number,
            start_lba=int(item.get("start") or 0),
            sector_count=int(item.get("size") or 0) // sector_size,
            filesystem=str(item.get("fstype") or ""),
            filesystem_version=str(item.get("fsver") or ""),
            uuid=str(item.get("uuid") or ""),
            mounted=any(str(value or "").strip() for value in mounts),
        )
    return partitions


def _select_source_layout(layout) -> tuple[PartitionExtent, PartitionExtent]:
    roots = [item for item in layout.partitions if item.type_code == EXT4_PARTITION_TYPE]
    swaps = [item for item in layout.partitions if item.type_code == SWAP_PARTITION_TYPE]
    others = [
        item
        for item in layout.partitions
        if item.type_code not in (EXT4_PARTITION_TYPE, SWAP_PARTITION_TYPE)
    ]
    if len(roots) != 1 or len(swaps) != 1 or others:
        raise Ext4CompactCaptureError(
            "Compact capture requires exactly one ext4 root and one Linux swap partition."
        )
    root, swap = roots[0], swaps[0]
    if root.number != 1 or root.kind != "primary" or not root.bootable:
        raise Ext4CompactCaptureError("Compact capture requires bootable primary ext4 partition 1.")
    return root, swap


def _validate_wsl_partition(
    actual: _WslPartition | None,
    expected: PartitionExtent,
    filesystem: str,
) -> _WslPartition:
    if actual is None:
        raise Ext4CompactCaptureError(f"WSL did not expose source partition {expected.number}.")
    if actual.mounted:
        raise Ext4CompactCaptureError(
            f"WSL mounted source partition {expected.number}; capture was stopped."
        )
    if actual.start_lba != expected.start_lba or actual.sector_count != expected.sector_count:
        raise Ext4CompactCaptureError(
            f"WSL partition {expected.number} geometry does not match the source MBR."
        )
    if actual.filesystem != filesystem or not UUID_PATTERN.fullmatch(actual.uuid):
        raise Ext4CompactCaptureError(
            f"Source partition {expected.number} has no valid {filesystem} UUID."
        )
    expected_version = "1" if filesystem == "swap" else "1.0"
    if actual.filesystem_version != expected_version:
        raise Ext4CompactCaptureError(
            f"Source partition {expected.number} is {filesystem} version "
            f"{actual.filesystem_version or 'unknown'}, expected {expected_version}."
        )
    return actual


def _ext4_metadata(image_path: str) -> _Ext4Metadata:
    output = _run_wsl_script(
        'set -e\nh=$(dumpe2fs -h "$1" 2>/dev/null)\n'
        "uuid=$(printf '%s\\n' \"$h\" | sed -n 's/^Filesystem UUID:[[:space:]]*//p')\n"
        "blocks=$(printf '%s\\n' \"$h\" | sed -n 's/^Block count:[[:space:]]*//p')\n"
        "bsize=$(printf '%s\\n' \"$h\" | sed -n 's/^Block size:[[:space:]]*//p')\n"
        "state=$(printf '%s\\n' \"$h\" | sed -n 's/^Filesystem state:[[:space:]]*//p')\n"
        'printf \'%s|%s|%s|%s\\n\' "$uuid" "$blocks" "$bsize" "$state"\n',
        [image_path],
    ).strip()
    fields = output.split("|", 3)
    if len(fields) != 4 or not UUID_PATTERN.fullmatch(fields[0]):
        raise Ext4CompactCaptureError("Could not read staged ext4 metadata.")
    return _Ext4Metadata(fields[0], int(fields[2]), int(fields[1]), fields[3])


def _render_expansion_script(
    root_uuid: str,
    swap_uuid: str,
    root_start_lba: int,
    root_sector_count: int,
    swap_sector_count: int,
) -> tuple[Path, str]:
    source = _asset_path("roulette_expand_storage.sh").read_text(encoding="utf-8")
    replacements = {
        "EXPECTED_ROOT_UUID": f'"{root_uuid}"',
        "SWAP_UUID": f'"{swap_uuid}"',
        "EXPECTED_ROOT_START": str(root_start_lba),
        "EXPECTED_ROOT_SECTORS": str(root_sector_count),
        "SWAP_SECTORS": str(swap_sector_count),
        "ALIGNMENT_SECTORS": str(ALIGNMENT_SECTORS),
    }
    for name, value in replacements.items():
        source, count = re.subn(
            rf"^{name}=.*$", f"{name}={value}", source, count=1, flags=re.MULTILINE
        )
        if count != 1:
            raise Ext4CompactCaptureError(f"Expansion template is missing {name}.")
    descriptor, name = tempfile.mkstemp(prefix="odinm-expand-", suffix=".sh")
    os.close(descriptor)
    path = Path(name)
    path.write_text(source, encoding="utf-8", newline="\n")
    return path, hashlib.sha256(source.encode("utf-8")).hexdigest()


def _install_expansion(
    stage_path: str,
    work_path: str,
    script_path: Path,
    script_sha256: str,
    cleanup_script_path: Path | None,
    cleanup: CleanupRecord | None,
) -> None:
    if (cleanup_script_path is None) != (cleanup is None):
        raise Ext4CompactCaptureError("Cleanup installer selection is inconsistent.")
    mount_path = f"{work_path}/mount"
    script_wsl = _wsl_path(script_path)
    cleanup_wsl = _wsl_path(cleanup_script_path) if cleanup_script_path else ""
    cleanup_sha256 = cleanup.source_sha256 if cleanup else ""
    clock_config = _wsl_path(_asset_path("roulette_e2fsck.conf"))
    _run_wsl_script(
        "set -e\n"
        "stage=$1\nmountpoint=$2\nscript=$3\ncleanup_enabled=$4\n"
        "cleanup_script=$5\nclock=$6\nexpected=$7\ncleanup_expected=$8\n"
        'mkdir -p "$mountpoint"\n'
        'cleanup() { umount "$mountpoint" >/dev/null 2>&1 || true; }\n'
        "trap cleanup EXIT INT TERM\n"
        'mount -o loop,rw "$stage" "$mountpoint"\n'
        '/bin/sh "$script" --install "$mountpoint"\n'
        'if [ "$cleanup_enabled" = "1" ]; then\n'
        '  /bin/sh "$cleanup_script" --install "$mountpoint"\n'
        '  cleanup_actual=$(sha256sum '
        '"$mountpoint/usr/local/sbin/roulette-profile-cleanup" | '
        "awk '{print $1}')\n"
        '  [ "$cleanup_actual" = "$cleanup_expected" ]\n'
        '  [ "$(stat -c \'%u:%g:%a\' '
        '"$mountpoint/usr/local/sbin/roulette-profile-cleanup")" = "0:0:755" ]\n'
        '  [ "$(stat -c \'%u:%g:%a\' '
        '"$mountpoint/etc/cron.d/roulette-profile-cleanup")" = "0:0:644" ]\n'
        '  grep -F "/usr/local/sbin/roulette-profile-cleanup --scheduled" '
        '"$mountpoint/etc/cron.d/roulette-profile-cleanup" >/dev/null\n'
        'else\n'
        '  rm -f "$mountpoint/usr/local/sbin/roulette-profile-cleanup" '
        '"$mountpoint/etc/cron.d/roulette-profile-cleanup"\n'
        'fi\n'
        'install -o root -g root -m 0644 "$clock" "$mountpoint/etc/e2fsck.conf"\n'
        'actual=$(sha256sum "$mountpoint/usr/local/sbin/roulette-expand-storage" | '
        "awk '{print $1}')\n"
        '[ "$actual" = "$expected" ]\n'
        'sync\numount "$mountpoint"\ntrap - EXIT INT TERM\nrmdir "$mountpoint"\n',
        [
            stage_path,
            mount_path,
            script_wsl,
            "1" if cleanup else "0",
            cleanup_wsl,
            clock_config,
            script_sha256,
            cleanup_sha256,
        ],
    )


def _check_and_repair(stage_path: str, should_cancel: Callable[[], bool]) -> None:
    _run_wsl_script(
        'set +e\ne2fsck -fy "$1"\ncode=$?\n[ "$code" -le 1 ]\n',
        [stage_path],
        should_cancel=should_cancel,
    )


def _hash_file(
    path: Path,
    include_sha1: bool,
    should_cancel: Callable[[], bool],
    on_progress: Callable[[int], None],
) -> dict[str, str]:
    size = path.stat().st_size
    sha256 = hashlib.sha256()
    sha1 = hashlib.sha1() if include_sha1 else None
    done = 0
    with path.open("rb") as stream:
        while True:
            if should_cancel():
                raise Ext4CompactCaptureCancelled("Compact capture was cancelled.")
            block = stream.read(COPY_CHUNK_BYTES)
            if not block:
                break
            sha256.update(block)
            if sha1 is not None:
                sha1.update(block)
            done += len(block)
            on_progress(85 + int(15 * done / size) if size else 100)
    digests = {"sha256": sha256.hexdigest()}
    if sha1 is not None:
        digests["sha1"] = sha1.hexdigest()
    return digests


def capture_ext4_compact(
    disk_number: int,
    output_path: Path,
    *,
    expected_size: int,
    expected_serial: str,
    include_sha1: bool,
    should_cancel: Callable[[], bool],
    on_progress: Callable[[int], None],
    on_log: Callable[[str], None],
    cleanup_script_path: Path | None = None,
) -> tuple[dict, dict]:
    cleanup_path = Path(cleanup_script_path) if cleanup_script_path else None
    cleanup = parse_cleanup_installer(cleanup_path) if cleanup_path else None
    check_prerequisites()
    started = datetime.now(UTC)
    physical_path = rf"\\.\PhysicalDrive{disk_number}"
    work_path = f"/tmp/odinm-ext4-{uuid.uuid4().hex}"
    if not WORK_PATTERN.fullmatch(work_path):
        raise Ext4CompactCaptureError("Generated WSL work path is invalid.")
    attached = False
    source_device = ""
    source_info: dict = {}

    try:
        if pyimager.volumes_on_disk(disk_number):
            raise Ext4CompactCaptureError(
                "Compact capture requires a source with no Windows-mounted volumes."
            )
        with pyimager.Win32Disk(physical_path) as disk:
            source_size = disk.size
            sector_size = disk.sector_size
            source_info = disk.device_info()
            serial = str(source_info.get("serial") or "").strip()
            if expected_size and source_size != expected_size:
                raise Ext4CompactCaptureError("Source disk capacity changed before capture.")
            if expected_serial and serial and serial.casefold() != expected_serial.casefold():
                raise Ext4CompactCaptureError("Source disk identity changed before capture.")
            if sector_size != 512:
                raise Ext4CompactCaptureError(
                    f"Compact ext4 capture requires 512-byte sectors; found {sector_size}."
                )
            source_layout = parse_mbr_layout(
                disk, source_size, sector_size, require_trailing_space=False
            )
            root, swap = _select_source_layout(source_layout)
            disk.seek(0)
            source_mbr = disk.read(512)
            if len(source_mbr) != 512:
                raise Ext4CompactCaptureError("Could not read the source MBR.")
            mbr_sha256 = hashlib.sha256(source_mbr).hexdigest()
            prefix_bytes = root.start_lba * sector_size
            on_log(f"Preserving {prefix_bytes} bytes from LBA 0 through the ext4 start.")
            with output_path.open("wb") as output:
                copied, bad_sectors, cancelled = pyimager.copy_stream(
                    disk,
                    output,
                    0,
                    prefix_bytes,
                    sector_size,
                    COPY_CHUNK_BYTES,
                    [],
                    on_progress=lambda done, total: on_progress(
                        int(10 * done / total) if total else 10
                    ),
                    should_cancel=should_cancel,
                    on_log=on_log,
                )
            if cancelled:
                raise Ext4CompactCaptureCancelled("Compact capture was cancelled.")
            if copied != prefix_bytes or bad_sectors:
                raise Ext4CompactCaptureError(
                    "The pre-ext4 disk prefix could not be read without substitutions."
                )

        before = _wsl_disks()
        _run(["wsl.exe", "--mount", physical_path, "--bare"])
        attached = True
        source_device = _wait_for_attached_disk(before, source_size, mbr_sha256)
        _set_source_read_only(source_device)
        partitions = _wsl_partitions(source_device, sector_size)
        root_info = _validate_wsl_partition(partitions.get(root.number), root, "ext4")
        swap_info = _validate_wsl_partition(partitions.get(swap.number), swap, "swap")
        _run_wsl(["mkdir", "-m", "700", work_path])
        stage_path = f"{work_path}/root.ext4"
        on_progress(10)
        on_log(f"Source ext4 UUID {root_info.uuid}; omitting swap UUID {swap_info.uuid}.")
        if cleanup is not None:
            on_log(
                f"Cleanup installer selected: {cleanup.source_filename} "
                f"({cleanup.installer_id})."
            )
        _run_wsl(
            ["e2image", "-raf", root_info.path, stage_path],
            should_cancel=should_cancel,
        )
        on_progress(55)
        _run(["wsl.exe", "--unmount", physical_path])
        attached = False

        _check_and_repair(stage_path, should_cancel)
        provisional_script, provisional_sha256 = _render_expansion_script(
            root_info.uuid, swap_info.uuid, root.start_lba, 1, swap.sector_count
        )
        try:
            _install_expansion(
                stage_path,
                work_path,
                provisional_script,
                provisional_sha256,
                cleanup_path,
                cleanup,
            )
        finally:
            provisional_script.unlink(missing_ok=True)
        _check_and_repair(stage_path, should_cancel)
        on_log("Shrinking the staged ext4 filesystem to its safe minimum.")
        _run_wsl(["resize2fs", "-M", stage_path], should_cancel=should_cancel)
        minimum = _ext4_metadata(stage_path)
        if minimum.uuid.casefold() != root_info.uuid.casefold():
            raise Ext4CompactCaptureError("Staged ext4 UUID changed during compaction.")
        buffer_blocks = (BUFFER_BYTES + minimum.block_size - 1) // minimum.block_size
        final_blocks = minimum.block_count + buffer_blocks
        _run_wsl(["resize2fs", stage_path, str(final_blocks)], should_cancel=should_cancel)
        compact = _ext4_metadata(stage_path)
        compact_bytes = compact.block_count * compact.block_size
        if compact_bytes % sector_size:
            raise Ext4CompactCaptureError("Compacted ext4 size is not sector aligned.")
        compact_sectors = compact_bytes // sector_size

        final_script, script_sha256 = _render_expansion_script(
            root_info.uuid,
            swap_info.uuid,
            root.start_lba,
            compact_sectors,
            swap.sector_count,
        )
        try:
            _install_expansion(
                stage_path,
                work_path,
                final_script,
                script_sha256,
                cleanup_path,
                cleanup,
            )
        finally:
            final_script.unlink(missing_ok=True)
        _check_and_repair(stage_path, should_cancel)
        _run_wsl(["truncate", "-s", str(compact_bytes), stage_path])
        _run_wsl(["e2fsck", "-fn", stage_path], should_cancel=should_cancel)
        verified = _ext4_metadata(stage_path)
        if verified.state.casefold() != "clean":
            raise Ext4CompactCaptureError(
                f"Compacted ext4 filesystem is not clean: {verified.state or 'unknown'}"
            )
        on_progress(75)

        with output_path.open("r+b") as output:
            current_mbr = output.read(512)
            output.seek(0)
            output.write(patch_ext4_only_mbr(current_mbr, root, compact_sectors))
            output.flush()
            os.fsync(output.fileno())
        output_wsl = _wsl_path(output_path)
        _run_wsl_script(
            'cat "$1" >> "$2"\nsync "$2"\n',
            [stage_path, output_wsl],
            should_cancel=should_cancel,
        )
        on_progress(85)

        image_layout = make_ext4_only_layout(source_layout, root, compact_sectors)
        if output_path.stat().st_size != image_layout.capture_bytes:
            raise Ext4CompactCaptureError("Published compact image length is incorrect.")
        with output_path.open("rb") as stream:
            parsed = parse_mbr_layout(stream, source_size, sector_size)
        if parsed != image_layout:
            raise Ext4CompactCaptureError("Published compact MBR layout failed read-back.")

        with pyimager.Win32Disk(physical_path) as disk:
            current_info = disk.device_info()
            current_serial = str(current_info.get("serial") or "").strip()
            disk.seek(0)
            current_mbr = disk.read(512)
            if disk.size != source_size or hashlib.sha256(current_mbr).hexdigest() != mbr_sha256:
                raise Ext4CompactCaptureError("Source disk changed during capture.")
            if (
                expected_serial
                and current_serial
                and current_serial.casefold() != expected_serial.casefold()
            ):
                raise Ext4CompactCaptureError("Source disk identity changed during capture.")

        digests = _hash_file(output_path, include_sha1, should_cancel, on_progress)
        finished = datetime.now(UTC)
        meta = {
            "format": "ext4_compact",
            "source": f"PhysicalDrive{disk_number}",
            "device": source_info,
            "disk_size": source_size,
            "sector_size": sector_size,
            "region_length": image_layout.capture_bytes,
            "bytes_written": image_layout.capture_bytes,
            "stored_bytes": image_layout.capture_bytes,
            "started_utc": started.isoformat(),
            "finished_utc": finished.isoformat(),
            "duration_s": round((finished - started).total_seconds(), 3),
            "digests": digests,
            "bad_sectors": [],
            "bad_sector_count": 0,
            "cancelled": False,
            "saved_trailing_bytes": image_layout.saved_bytes,
            "ext4_uuid": root_info.uuid,
            "swap_uuid": swap_info.uuid,
            "buffer_bytes": BUFFER_BYTES,
            "cleanup_installed": cleanup is not None,
        }
        filesystem = Ext4FilesystemRecord(
            partition_number=root.number,
            uuid=root_info.uuid,
            block_size=verified.block_size,
            original_start_lba=root.start_lba,
            original_sector_count=root.sector_count,
            compact_sector_count=compact_sectors,
            minimum_blocks=minimum.block_count,
            buffer_bytes=BUFFER_BYTES,
            prefix_bytes=prefix_bytes,
        )
        omitted_swap = OmittedSwapRecord(
            partition_number=swap.number,
            uuid=swap_info.uuid,
            original_start_lba=swap.start_lba,
            sector_count=swap.sector_count,
        )
        required_target = minimum_target_bytes(
            root.start_lba,
            compact_sectors,
            swap.sector_count,
            ALIGNMENT_SECTORS,
            sector_size,
        )
        expansion = ExpansionRecord(
            root_uuid=root_info.uuid,
            swap_uuid=swap_info.uuid,
            swap_sector_count=swap.sector_count,
            alignment_sectors=ALIGNMENT_SECTORS,
            minimum_target_bytes=required_target,
            installed_script_sha256=script_sha256,
        )
        manifest = build_ext4_manifest(
            image_layout,
            source_layout,
            meta,
            filesystem,
            omitted_swap,
            expansion,
            cleanup,
        )
        on_progress(100)
        return meta, manifest
    except (CompactImageError, OSError, ValueError) as exc:
        raise Ext4CompactCaptureError(str(exc)) from exc
    finally:
        if attached:
            try:
                _run(["wsl.exe", "--unmount", physical_path])
            except Exception:
                pass
        if WORK_PATTERN.fullmatch(work_path):
            try:
                _run_wsl(["rm", "-rf", "--", work_path])
            except Exception:
                pass
