"""Headless schema, range, and guarded-preflight checks for used-block archives."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import guarded_restore  # noqa: E402
import used_block_archive as archive  # noqa: E402


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _archive(
    path: Path,
    *,
    alter_member: bool = False,
    compression: int = zipfile.ZIP_STORED,
) -> dict:
    boot_data = bytearray(512)
    boot_data[440:444] = bytes.fromhex("54455354")
    boot_data[510:512] = b"\x55\xaa"
    boot = bytes(boot_data)
    image = b"partclone-image"
    domain = b"# Mapfile. Created by Partclone\n0x200 0x200 +\n"
    range_record = {
        "member": "partition-1.ntfs.pcl",
        "offset": 512,
        "length": 512,
        "start_lba": 1,
        "sector_count": 1,
        "sector_size": 512,
        "byte_count": 512,
        "sha256": _sha(b"R" * 512),
    }
    boot_region = {
        "member": archive.BOOT_MEMBER,
        "member_offset": 0,
        "offset": 0,
        "length": 512,
        "start_lba": 0,
        "sector_count": 1,
        "sector_size": 512,
        "byte_count": 512,
        "sha256": _sha(boot),
    }
    all_ranges = [boot_region, range_record]
    manifest = {
        "schema_version": archive.ARCHIVE_SCHEMA,
        "format": archive.ARCHIVE_FORMAT,
        "created_utc": "2026-08-29T00:00:00+00:00",
        "source": {
            "disk_number": 7,
            "disk_size": 4096,
            "sector_size": 512,
            "disk_signature": "54455354",
            "mbr_sha256": _sha(boot),
            "serial": "TEST-7",
            "model": "TEST DISK",
        },
        "tools": {"partclone": "Partclone : v0.3.40"},
        "boot": {
            "member": archive.BOOT_MEMBER,
            "member_sha256": _sha(boot),
            "regions": [boot_region],
        },
        "partitions": [
            {
                "number": 1,
                "kind": "primary",
                "type_code": "0x07",
                "start_lba": 1,
                "sector_count": 1,
                "filesystem": "ntfs",
                "filesystem_version": "3.1",
                "uuid": "2A1B-TEST-SERIAL",
                "label": "DATA",
                "action": "restore",
                "adapter": "partclone.ntfs",
                "member": "partition-1.ntfs.pcl",
                "member_sha256": _sha(image),
                "domain_member": "partition-1.domain.map",
                "domain_sha256": _sha(domain),
                "ranges": [range_record],
            },
            {
                "number": 2,
                "kind": "primary",
                "type_code": "0x82",
                "start_lba": 2,
                "sector_count": 2,
                "filesystem": "swap",
                "filesystem_version": "1",
                "uuid": "dc05c11c-afd3-417d-adf6-2c327b67b968",
                "label": "swap0",
                "action": "recreate",
            },
        ],
        "range_hashes": {
            "algorithm": "sha256",
            "max_range_bytes": archive.MAX_RANGE_BYTES,
            "canonical_sha256": archive.canonical_range_digest(all_ranges),
        },
    }
    with zipfile.ZipFile(path, "w", compression=compression) as output:
        output.writestr(archive.MANIFEST_MEMBER, json.dumps(manifest))
        output.writestr(archive.BOOT_MEMBER, boot)
        output.writestr("partition-1.ntfs.pcl", image + (b"X" if alter_member else b""))
        output.writestr("partition-1.domain.map", domain)
    return manifest


def test_domain_ranges_are_absolute_bounded_and_split():
    text = "# domain\n0x1000 0x600 +\n0x1800 0x200 ?\n"
    values = archive.parse_domain_ranges(
        text,
        partition_offset=0x1000,
        partition_length=0x1000,
        max_range_bytes=0x200,
    )
    assert values == [(0x1000, 0x200), (0x1200, 0x200), (0x1400, 0x200)]


def test_approved_gaming_answer_always_redirects_to_raw():
    assert archive.gaming_answer_action(True) == "raw"
    assert archive.gaming_answer_action(False) == "general"
    assert archive.gaming_answer_action(None) == "cancel"


def test_domain_range_outside_partition_is_rejected():
    try:
        archive.parse_domain_ranges("0x0 0x400 +\n", partition_offset=0x200, partition_length=0x400)
    except archive.UsedBlockArchiveError as exc:
        assert "outside" in str(exc)
    else:
        raise AssertionError("out-of-partition domain range was accepted")


def test_archive_preflight_validates_every_member_and_capacity():
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "test.odin-archive"
        manifest = _archive(path)
        loaded, digest, required = archive.load_archive(path, 4096)
        assert loaded == manifest
        assert digest == _sha(path.read_bytes())
        assert required == 4096
        plan = guarded_restore.preflight_image(path, 4096)
        assert plan.image_format == "used-block-archive"
        assert plan.required_capacity == 4096
        assert plan.source_file_bytes == path.stat().st_size
        try:
            archive.load_archive(path, 4095)
        except archive.UsedBlockArchiveError as exc:
            assert "smaller" in str(exc)
        else:
            raise AssertionError("undersized target was accepted")


def test_archive_member_tampering_is_rejected():
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "bad.odin-archive"
        _archive(path, alter_member=True)
        try:
            archive.load_archive(path, 4096)
        except archive.UsedBlockArchiveError as exc:
            assert "SHA-256" in str(exc)
        else:
            raise AssertionError("tampered Partclone member was accepted")


def test_compressed_zip_members_are_rejected_before_extraction():
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "compressed.odin-archive"
        _archive(path, compression=zipfile.ZIP_DEFLATED)
        try:
            archive.load_archive(path, 4096)
        except archive.UsedBlockArchiveError as exc:
            assert "without ZIP compression" in str(exc)
        else:
            raise AssertionError("compressed archive member was accepted")


def test_unknown_manifest_keys_and_nonstandard_swap_are_rejected():
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "test.odin-archive"
        manifest = _archive(path)
        manifest["unexpected"] = True
        try:
            archive.validate_manifest(manifest)
        except archive.UsedBlockArchiveError as exc:
            assert "unknown" in str(exc)
        else:
            raise AssertionError("unknown manifest key was accepted")
        manifest.pop("unexpected")
        manifest["partitions"][1]["filesystem_version"] = "2"
        try:
            archive.validate_manifest(manifest)
        except archive.UsedBlockArchiveError as exc:
            assert "Swap" in str(exc)
        else:
            raise AssertionError("nonstandard swap was accepted")


def _run_direct() -> int:
    tests = [
        value
        for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"PASS {test.__name__}")
        except Exception as exc:
            failures += 1
            print(f"FAIL {test.__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_run_direct())
