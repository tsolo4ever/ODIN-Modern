# ODIN Project Map

## Current State (All builds: 6/7 succeeded, 1 blocked)

### ✅ What Builds
| Project | Status | Notes |
|---------|--------|-------|
| zlib | ✅ | Static lib from src/zlib-1.3.2/ |
| libz2 | ✅ | bzip2 static lib |
| ODIN | ✅ | Main GUI app builds and runs |
| ODINC | ✅ | Console version |
| ODINHelp | ✅ | CHM built successfully |
| OdinM | ✅ | **NEW** — Multi-drive clone tool builds (Debug-x64) |
| ODINTest | ❌ | 33 errors from cppunit ABI incompatibility |

### ❌ ODINTest — Root Cause:
- `cppunitud.lib`/`cppunitu.lib` compiled with VS2008 (old debug STL ABI)
- Fix: `vcpkg install cppunit:x64-windows` or replace with Catch2/Google Test

## Key File Locations

| File | Purpose |
|------|---------|
| `ODIN.sln` | Solution — 7 projects (zlib, libz2, ODIN, ODINC, OdinM, ODINTest, ODINHelp) |
| `OdinM.vcxproj` | NEW — Multi-drive clone with SHA hash verification |
| `ODIN.vcxproj` | Main GUI application |
| `ODINC.vcxproj` | Console version |
| `zlib.vcxproj` | zlib 1.3.2 static library |
| `libz2.vcxproj` | bzip2 static library |
| `ODINTest.vcxproj` | Unit tests — blocked on cppunit ABI |
| `lib/WTL10/Include/` | WTL 10.0 headers |
| `src/ODINM/` | OdinM source (all files complete) |
| `src/ODIN/` | Main ODIN source |
| `src/zlib-1.3.2/` | zlib 1.3.2 sources |

## OdinM Feature Summary
- Clone one ODIN image to up to **5 USB drives simultaneously**
- **SHA-1 and SHA-256** hash verification post-clone
- Auto-clone on device insertion (WM_DEVICECHANGE)
- Activity log with timestamps + CSV export
- Settings persisted to `OdinM.ini` (next to executable)
- ODINC.exe must be in same folder as OdinM.exe

## ODIN Dialog Layout
- Dialog width changed from 320 → 285 dialog units
- **TODO**: Make window resizable (CDialogResize framework partially coded in ODINDlg.h)
  - Need: WS_THICKFRAME in ODIN.rc, uncomment CDialogResize inheritance,
    complete anchor map for all ~30 controls, add WM_GETMINMAXINFO

## Git Commits (recent, on `modernization` branch)
- `1de9b4f` — ODIN.rc: dialog width 320→285
- `3d30dbf` — feat: Add OdinM project (OdinM.vcxproj + sln entry + fixes)
- `1bb6b1d` — Fix zlib include paths in Compression/Decompression threads
- `068e151` — ODINTest: add zlib.lib+libz2.lib; remove C:\devtools path

## Important Context
- VS2026 = Version 18, MSBuild at `C:\Program Files\Microsoft Visual Studio\18\...`
- Output dirs: `Debug-x64\`, `Debug-Win32\`, `Release-x64\`, `Release-Win32\`
- ODINC.exe may trigger AV false positives (Gen:Variant.Fugrafa) — add output folder to exclusions
- WTL path in all projects: `$(SolutionDir)lib\WTL10\Include`
- OdinM: no `_ATL_NO_AUTOMATIC_NAMESPACE` (breaks CDialogImpl), IDC_STATIC needs #ifndef guard
