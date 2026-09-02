import os
import sys
import configparser

CONFIG_FILE = "odinm_py.ini"

DEFAULTS = {
    "odinc_path": "",  # resolved at runtime if blank
    "theme": "darkly",
    "max_concurrent": "3",
    "auto_clone": "false",
    "last_image": "",
    "last_output_dir": "",
    "verify_after_clone": "false",
    "stop_on_verify_fail": "false",
    "show_flash_widget": "true",
    "keep_completed_disks_locked": "true",
    "max_drive_gb": "8",
    # How many removable disks get considered for a slot at all, per
    # refresh - beyond this, extras are excluded the same way an oversized
    # drive already is. 0 = no cap (falls back to NUM_SLOTS naturally).
    "max_disks": "5",
    # Imaging/flashing engine: "odin" drives ODINC.exe, "pyimager" uses the
    # built-in Python imager (scripts/pyimager.py).
    "engine": "pyimager",
}

ENGINE_ODIN = "odin"
ENGINE_PYIMAGER = "pyimager"
ENGINES = (ENGINE_ODIN, ENGINE_PYIMAGER)


def _config_path() -> str:
    """Config file lives next to main.py (or the frozen exe)."""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, CONFIG_FILE)


def _default_odinc() -> str:
    """ODINC.exe is expected next to main.py / the exe."""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "ODINC.exe")


class ConfigManager:
    def __init__(self):
        self._path = _config_path()
        self._cfg = configparser.ConfigParser()
        self._cfg["settings"] = dict(DEFAULTS)
        self._cfg.read(self._path)
        if "settings" not in self._cfg:
            self._cfg["settings"] = dict(DEFAULTS)

    # ── getters ──────────────────────────────────────────────────────────────

    def get_odinc_path(self) -> str:
        val = self._cfg["settings"].get("odinc_path", "").strip()
        return val if val else _default_odinc()

    def get_theme(self) -> str:
        return self._cfg["settings"].get("theme", "darkly")

    def get_max_concurrent(self) -> int:
        try:
            return int(self._cfg["settings"].get("max_concurrent", "3"))
        except ValueError:
            return 3

    def get_auto_clone(self) -> bool:
        return self._cfg["settings"].get("auto_clone", "false").lower() == "true"

    def get_last_image(self) -> str:
        return self._cfg["settings"].get("last_image", "")

    def get_last_output_dir(self) -> str:
        return self._cfg["settings"].get("last_output_dir", "").strip()

    def get_verify_after_clone(self) -> bool:
        return self._cfg["settings"].get("verify_after_clone", "false").lower() == "true"

    def get_stop_on_verify_fail(self) -> bool:
        return self._cfg["settings"].get("stop_on_verify_fail", "false").lower() == "true"

    def get_show_flash_widget(self) -> bool:
        return self._cfg["settings"].get("show_flash_widget", "true").lower() == "true"

    def get_keep_completed_disks_locked(self) -> bool:
        return (
            self._cfg["settings"].get("keep_completed_disks_locked", "true").lower()
            == "true"
        )

    def get_max_drive_gb(self) -> int:
        try:
            return int(self._cfg["settings"].get("max_drive_gb", "8"))
        except ValueError:
            return 8

    def get_max_disks(self) -> int:
        """How many removable disks get considered for a slot at all, per
        refresh. 0 = no cap."""
        try:
            return int(self._cfg["settings"].get("max_disks", "5"))
        except ValueError:
            return 5

    def get_engine(self) -> str:
        """Imaging engine: ENGINE_ODIN or ENGINE_PYIMAGER."""
        val = self._cfg["settings"].get("engine", ENGINE_ODIN).strip().lower()
        return val if val in ENGINES else ENGINE_ODIN

    def use_pyimager(self) -> bool:
        return self.get_engine() == ENGINE_PYIMAGER

    # ── setters ──────────────────────────────────────────────────────────────

    def set_odinc_path(self, path: str):
        self._cfg["settings"]["odinc_path"] = path
        self._save()

    def set_theme(self, theme: str):
        self._cfg["settings"]["theme"] = theme
        self._save()

    def set_max_concurrent(self, n: int):
        self._cfg["settings"]["max_concurrent"] = str(n)
        self._save()

    def set_auto_clone(self, enabled: bool):
        self._cfg["settings"]["auto_clone"] = "true" if enabled else "false"
        self._save()

    def set_last_image(self, path: str):
        self._cfg["settings"]["last_image"] = path
        self._save()

    def set_last_output_dir(self, path: str):
        self._cfg["settings"]["last_output_dir"] = path
        self._save()

    def set_verify_after_clone(self, v: bool):
        self._cfg["settings"]["verify_after_clone"] = "true" if v else "false"
        self._save()

    def set_stop_on_verify_fail(self, v: bool):
        self._cfg["settings"]["stop_on_verify_fail"] = "true" if v else "false"
        self._save()

    def set_show_flash_widget(self, v: bool):
        self._cfg["settings"]["show_flash_widget"] = "true" if v else "false"
        self._save()

    def set_keep_completed_disks_locked(self, v: bool):
        self._cfg["settings"]["keep_completed_disks_locked"] = "true" if v else "false"
        self._save()

    def set_engine(self, value: str):
        if value not in ENGINES:
            raise ValueError(f"unknown engine {value!r}; must be one of {ENGINES}")
        self._cfg["settings"]["engine"] = value
        self._save()

    def set_max_disks(self, n: int):
        self._cfg["settings"]["max_disks"] = str(n)
        self._save()

    def _save(self):
        with open(self._path, "w") as f:
            self._cfg.write(f)
