# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for OdinM_py
# Run from the OdinM_py directory:
#   .venv\Scripts\pyinstaller.exe --clean OdinM_py.spec
# Output: dist\OdinM_py.exe  (single-file, UAC elevation built-in)

import runpy
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.win32 import versioninfo

version = runpy.run_path(str(Path(SPECPATH) / 'odinm_version.py'))
file_version = (*version['VERSION'], 0)
version_text = version['VERSION_TEXT']
version_resource = versioninfo.VSVersionInfo(
    ffi=versioninfo.FixedFileInfo(filevers=file_version, prodvers=file_version),
    kids=[
        versioninfo.StringFileInfo([
            versioninfo.StringTable('040904B0', [
                versioninfo.StringStruct('FileDescription', 'OdinM_py Multi-Drive Clone Tool'),
                versioninfo.StringStruct('FileVersion', version_text),
                versioninfo.StringStruct('ProductName', 'OdinM_py'),
                versioninfo.StringStruct('ProductVersion', version_text),
                versioninfo.StringStruct('OriginalFilename', 'OdinM_py.exe'),
            ]),
        ]),
        versioninfo.VarFileInfo([versioninfo.VarStruct('Translation', [1033, 1200])]),
    ],
)

# ttkbootstrap ships theme JSON/CSS data files that must travel with the exe
datas = collect_data_files('ttkbootstrap')
datas += [
    ('scripts/roulette_expand_storage.sh', 'scripts'),
    ('scripts/roulette_profile_cleanup.sh', 'scripts'),
    ('scripts/roulette_e2fsck.conf', 'scripts'),
]

a = Analysis(
    ['main.py'],
    # scripts/ holds pyimager, the built-in imaging engine. It must be on
    # pathex or the frozen exe cannot import it (there is no scripts/ folder
    # next to a one-file bundle for the sys.path fallback to find).
    pathex=['.', 'scripts'],
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
        # built-in Python imaging engine (scripts/pyimager.py) and its worker
        'pyimager',
        'pyimager_worker',
        'ext4_compact_capture',
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
    version=version_resource,
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
