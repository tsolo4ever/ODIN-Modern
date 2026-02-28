"""
hash_config.py
Per-image, per-partition expected hash configuration.
Stores SHA-1 and SHA-256 expected values, enable flags, and fail-on-mismatch flags.
Used for Missouri Gaming Commission compliance verification.
"""

import json
import os
import sys
from typing import Dict, Optional

CONFIG_FILE    = "odinm_hash_config.json"
NUM_PARTITIONS = 5


def _config_path() -> str:
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, CONFIG_FILE)


def blank_partition() -> dict:
    # TODO(v0.5): add MD5 support — some gaming jurisdictions require it.
    # Pattern: add md5_value/md5_enabled/md5_fail here, update has_any_enabled()
    # and get_enabled_partitions(), add hashlib.md5 branch in hash_worker.py,
    # and add an MD5 row in configure_hash_dialog.py.
    return {
        "sha1_value":     "",
        "sha1_enabled":   False,
        "sha1_fail":      False,
        "sha256_value":   "",
        "sha256_enabled": False,
        "sha256_fail":    False,
    }


class HashConfig:
    def __init__(self):
        self._path = _config_path()
        self._data: Dict[str, dict] = {}
        self._load()

    def get_partition(self, filepath: str, partition: int) -> dict:
        """Return config for a partition (1-based). Returns blank dict if not configured."""
        key = os.path.normcase(filepath)
        partitions = self._data.get(key, {}).get("partitions", {})
        return dict(partitions.get(str(partition), blank_partition()))

    def save_partition(self, filepath: str, partition: int, cfg: dict):
        """Persist config for a specific partition."""
        key = os.path.normcase(filepath)
        if key not in self._data:
            self._data[key] = {
                "filename": os.path.basename(filepath),
                "partitions": {},
            }
        self._data[key]["partitions"][str(partition)] = dict(cfg)
        self._save()

    def has_any_enabled(self, filepath: str) -> bool:
        """True if any partition has at least one algorithm enabled."""
        key = os.path.normcase(filepath)
        for part_cfg in self._data.get(key, {}).get("partitions", {}).values():
            if part_cfg.get("sha1_enabled") or part_cfg.get("sha256_enabled"):
                return True
        return False

    def get_enabled_partitions(self, filepath: str) -> Dict[int, dict]:
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
            with open(self._path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self._data = {}

    def _save(self):
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
        except OSError:
            pass
