"""
hash_log.py
Persists hash results keyed by file path.
Lets the dialog warn when a file hasn't been verified in > 30 days.
"""

import json
import os
import sys
from datetime import datetime, UTC

LOG_FILE = "odinm_hash_log.json"


def _log_path() -> str:
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, LOG_FILE)


class HashLog:
    def __init__(self):
        self._path = _log_path()
        self._data: dict[str, dict] = {}
        self._load()

    def get_entry(self, filepath: str) -> dict | None:
        """Return stored entry for filepath, or None if not recorded."""
        return self._data.get(os.path.normcase(filepath))

    def save_entry(self, filepath: str, sha256: str, sha1: str) -> bool:
        """Record a successful hash result with current UTC timestamp."""
        key = os.path.normcase(filepath)
        self._data[key] = {
            "filename": os.path.basename(filepath),
            "sha256": sha256,
            "sha1": sha1,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        return self._save()

    def days_since(self, filepath: str) -> int | None:
        """Days since the last recorded hash, or None if never recorded."""
        entry = self.get_entry(filepath)
        if not entry or "timestamp" not in entry:
            return None
        try:
            ts = datetime.fromisoformat(entry["timestamp"])
            return (datetime.now(UTC) - ts).days
        except Exception:
            return None

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
