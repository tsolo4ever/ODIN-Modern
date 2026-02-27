# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for OdinM_py
# Run from the OdinM_py directory:
#   .venv\Scripts\pyinstaller.exe --clean OdinM_py.spec
# Output: dist\OdinM_py.exe  (single-file, UAC elevation built-in)

from PyInstaller.utils.hooks import collect_data_files

# ttkbootstrap ships theme JSON/CSS data files that must travel with the exe
datas = collect_data_files('ttkbootstrap')

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=datas,
    hiddenimports=[
        # pywin32 modules loaded at runtime via ctypes / dynamic import
        'win32api',
        'win32con',
        'win32file',
        'win32security',
        'winerror',
        'pywintypes',
        'win32gui',
        # tkinter sub-modules not always auto-detected
        'tkinter',
        'tkinter.scrolledtext',
        'tkinter.filedialog',
        'tkinter.messagebox',
        # ui sub-package — listed explicitly in case static analysis misses them
        'ui',
        'ui.main_window',
        'ui.slot_widget',
        'ui.configure_hash_dialog',
        'ui.hash_dialog',
        'ui.image_options_dialog',
        'ui.make_image_dialog',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='OdinM_py',
    icon=r'..\src\ODINM\res\OdinM.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,           # no terminal window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    uac_admin=True,          # OS-level UAC elevation at launch (requireAdministrator)
)
