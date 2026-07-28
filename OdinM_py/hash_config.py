"""
hash_config.py
Per-image, per-partition expected hash configuration.
Stores SHA-1 and SHA-256 expected values, enable flags, and fail-on-mismatch flags.
Used for Missouri Gaming Commission compliance verification.
"""

import json
import os
import sys

CONFIG_FILE = "odinm_hash_config.json"


def _config_path() -> str:
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, CONFIG_FILE)


def blank_partition() -> dict:
    return {
        "sha1_value": "",
        "sha1_enabled": False,
        "sha1_fail": False,
        "sha256_value": "",
        "sha256_enabled": False,
        "sha256_fail": False,
    }


class HashConfig:
    def __init__(self):
        self._path = _config_path()
        self._data: dict[str, dict] = {}
        self._load()

    def get_partition(self, filepath: str, partition: int) -> dict:
        """Return config for a partition (1-based). Returns blank dict if not configured."""
        key = os.path.normcase(filepath)
        partitions = self._data.get(key, {}).get("partitions", {})
        return dict(partitions.get(str(partition), blank_partition()))

    def save_partition(self, filepath: str, partition: int, cfg: dict) -> bool:
        """Persist config and keep whole-disk/partition checks exclusive."""
        key = os.path.normcase(filepath)
        if key not in self._data:
            self._data[key] = {
                "filename": os.path.basename(filepath),
                "partitions": {},
            }
        partitions = self._data[key]["partitions"]
        partitions[str(partition)] = dict(cfg)

        if cfg.get("sha1_enabled") or cfg.get("sha256_enabled"):
            if partition == 0:
                conflicting = (
                    other_cfg for part_num, other_cfg in partitions.items() if int(part_num) > 0
                )
            else:
                disk_cfg = partitions.get("0")
                conflicting = (disk_cfg,) if disk_cfg is not None else ()
            for other_cfg in conflicting:
                other_cfg["sha1_enabled"] = False
                other_cfg["sha256_enabled"] = False
        return self._save()

    def get_enabled_partitions(self, filepath: str) -> dict[int, dict]:
        """Return {partition_number: cfg} for all partitions with any algo enabled."""
        key = os.path.normcase(filepath)
        result = {}
        for part_str, cfg in self._data.get(key, {}).get("partitions", {}).items():
            if cfg.get("sha1_enabled") or cfg.get("sha256_enabled"):
                result[int(part_str)] = dict(cfg)
        return result

    # ── private ──────────────────────────────────────────────────────────────

    def _load(self):
        try:
            with open(self._path, encoding="utf-8") as f:
                self._data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self._data = {}

    def _save(self) -> bool:
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
            return True
        except OSError:
            return False
