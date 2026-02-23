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

## TODO List

### ODIN / OdinM UI Upgrades
- [ ] **Resizable main window** — Enable CDialogResize in ODINDlg.h (already partially coded),
  add WS_THICKFRAME to ODIN.rc, complete anchor map for all ~30 controls, add WM_GETMINMAXINFO

- [ ] **Modern visual styles (app manifest)** — Add to `ODIN.exe.manifest` and create `OdinM.exe.manifest`:
  ```xml
  <dependency>
    <dependentAssembly>
      <assemblyIdentity type="win32"
        name="Microsoft.Windows.Common-Controls" version="6.0.0.0"
        processorArchitecture="*" publicKeyToken="6595b64144ccf1df" language="*"/>
    </dependentAssembly>
  </dependency>
  ```

- [ ] **DPI awareness (PerMonitorV2)** — Add to manifest:
  ```xml
  <dpiAware>True/PM</dpiAware>
  <dpiAwareness>PerMonitorV2</dpiAwareness>
  ```

- [ ] **Dark mode support (Win10 1809+)** — Add to `OdinMDlg.cpp` / `ODINDlg.cpp`:
  ```cpp
  void ApplyDarkMode(HWND hwnd) {
      BOOL darkMode = TRUE;
      DwmSetWindowAttribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE,
                            &darkMode, sizeof(darkMode));
  }
  bool IsSystemDarkMode() {
      DWORD value = 0, size = sizeof(value);
      RegGetValueW(HKEY_CURRENT_USER,
          L"Software\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize",
          L"AppsUseLightTheme", RRF_RT_REG_DWORD, nullptr, &value, &size);
      return value == 0;
  }
  ```
  Link: add `dwmapi.lib` to linker dependencies.

- [ ] **ListView improvements (OdinM drive list)** — In `OdinMDlg.cpp::InitializeDriveList()`:
  ```cpp
  // Eliminate flicker + better look
  ListView_SetExtendedListViewStyle(hList,
      LVS_EX_FULLROWSELECT | LVS_EX_DOUBLEBUFFER |
      LVS_EX_GRIDLINES     | LVS_EX_HEADERDRAGDROP);

  // NM_CUSTOMDRAW handler — alternating rows + status colors:
  case NM_CUSTOMDRAW:
      if (stage == CDDS_PREPAINT) return CDRF_NOTIFYITEMDRAW;
      if (stage == CDDS_ITEMPREPAINT) {
          if (nmcd->dwItemSpec % 2 == 0) nmcd->clrTextBk = RGB(245,245,250);
          SetStatusColor(nmcd); // blue=Cloning, green=Done, red=Failed
          return CDRF_NEWFONT;
      }

  // Draw inline progress bar inside COL_PROGRESS cell:
  void DrawProgressInCell(HDC hdc, RECT rc, int pct) {
      FillRect(hdc, &rc, GetSysColorBrush(COLOR_BTNFACE));
      RECT fill = rc;
      fill.right = rc.left + (rc.right - rc.left) * pct / 100;
      HBRUSH br = CreateSolidBrush(RGB(0, 120, 215)); // Win11 blue
      FillRect(hdc, &fill, br);
      DeleteObject(br);
      // DrawProgressText(hdc, rc, pct);  // e.g. "67%"
  }
  ```

### Build / Test
- [ ] **Fix ODINTest** — Rebuild CppUnit 1.12.1 with VS2026 (`vcpkg install cppunit:x64-windows`)
  or replace with Catch2/Google Test (header-only, no prebuilt libs)

## Build Optimization Notes — /Gm Removal
`/Gm` (Enable Minimal Rebuild) was deprecated and removed in newer MSVC.
All projects are already clean — no `<MinimalRebuild>` in any .vcxproj.

**Recommended build speed alternatives (no code changes needed):**
| Technique | How to enable |
|-----------|--------------|
| Incremental linking | Linker → General → Enable Incremental Linking (`/INCREMENTAL`) |
| Parallel builds | MSBuild `/m` flag or VS: Tools → Options → Build and Run → max parallel project builds |
| Precompiled headers | Already enabled in all projects (`stdafx.h` / PCH) |
| MSBuild incremental | Automatic — MSBuild detects changed files, no flag needed |
