"""
main.py
Entry point for OdinM_py.
Requests admin elevation (required for raw disk writes via ODINC.exe),
then launches the app.
"""

import ctypes
import sys
import os


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def request_elevation():
    """Re-launch this script with admin rights via UAC prompt."""
    script = os.path.abspath(sys.argv[0])
    params = " ".join(f'"{a}"' for a in sys.argv[1:])
    ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, f'"{script}" {params}', None, 1
    )


def main():
    if not is_admin():
        request_elevation()
        sys.exit(0)

    from config_manager import ConfigManager
    from app import OdinMApp

    config = ConfigManager()
    app = OdinMApp(config)
    app.run()


if __name__ == "__main__":
    main()
