"""
hash_log.py
Persists hash results keyed by file path.
Lets the dialog warn when a file hasn't been verified in > 30 days.
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict

LOG_FILE   = "odinm_hash_log.json"
STALE_DAYS = 30


def _log_path() -> str:
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, LOG_FILE)


class HashLog:
    def __init__(self):
        self._path = _log_path()
        self._data: Dict[str, dict] = {}
        self._load()

    def get_entry(self, filepath: str) -> Optional[dict]:
        """Return stored entry for filepath, or None if not recorded."""
        return self._data.get(os.path.normcase(filepath))

    def save_entry(self, filepath: str, sha256: str, sha1: str):
        """Record a successful hash result with current UTC timestamp."""
        key = os.path.normcase(filepath)
        self._data[key] = {
            "filename": os.path.basename(filepath),
            "sha256":   sha256,
            "sha1":     sha1,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._save()

    def days_since(self, filepath: str) -> Optional[int]:
        """Days since the last recorded hash, or None if never recorded."""
        entry = self.get_entry(filepath)
        if not entry or "timestamp" not in entry:
            return None
        try:
            ts = datetime.fromisoformat(entry["timestamp"])
            return (datetime.now(timezone.utc) - ts).days
        except Exception:
            return None

    def is_stale(self, filepath: str) -> bool:
        days = self.days_since(filepath)
        return days is not None and days >= STALE_DAYS

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
