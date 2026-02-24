# ODIN Project Map

**Updated:** 2026-02-23

## Current State (6/7 builds succeed)

| Project | Status | Notes |
|---------|--------|-------|
| zlib | ✅ | Static lib from src/zlib-1.3.2/ |
| libz2 | ✅ | bzip2 static lib |
| ODIN | ✅ | Main GUI app builds and runs |
| ODINC | ✅ | Console version |
| ODINHelp | ✅ | CHM built successfully |
| OdinM | ✅ | Multi-drive clone tool (Debug-x64) |
| ODINTest | ❌ | 33 errors — cppunit ABI mismatch (VS2008 libs vs VS2026) |

**ODINTest fix:** `vcpkg install cppunit:x64-windows` or replace with Catch2/Google Test (header-only)

---

## Phase Status

| Phase | Status |
|-------|--------|
| Phase 1 — Critical bug fixes | ✅ Complete (6/6) |
| Phase 2 — Build system / VS2026 migration | ✅ Complete |
| Phase 3 — C++ modernization | 🔄 Partial |
| Phase 4 — Feature additions | ✅ Complete |
| Phase 5 — Testing | ⏳ Pending |
| Phase 6 — Documentation | ⏳ Partial |
| Phase 7 — Release | ⏳ Pending |

### Phase 3 Remaining Work
- [ ] `CommandLineProcessor.h/cpp` — raw pointer audit
- [ ] `ODINDlg.h/cpp` — raw pointer audit
- [ ] `SplitManager.h/cpp` — raw pointer audit
- [ ] `ImageStream.cpp::StoreVolumeBitmap()` — replace `malloc` with `std::vector`

---

## Key File Locations

| File | Purpose |
|------|---------|
| `ODIN.sln` | Solution — 7 projects |
| `ODIN.vcxproj` | Main GUI application |
| `ODINC.vcxproj` | Console version |
| `OdinM.vcxproj` | Multi-drive clone tool |
| `ODINTest.vcxproj` | Unit tests — blocked on cppunit ABI |
| `zlib.vcxproj` | zlib 1.3.2 static library |
| `libz2.vcxproj` | bzip2 static library |
| `lib/WTL10/Include/` | WTL 10.0 headers |
| `src/ODIN/` | Main ODIN source |
| `src/ODINM/` | OdinM source |
| `src/zlib-1.3.2/` | zlib 1.3.2 sources |
| `CLAUDE.md` | Project rules for Claude Code |
| `docs/MODERNIZATION_CHECKLIST.md` | Detailed per-task progress |

---

## OdinM Feature Summary
- Clone one ODIN image to up to **5 USB drives simultaneously**
- **SHA-1 and SHA-256** hash verification post-clone
- Auto-clone on device insertion (WM_DEVICECHANGE)
- Activity log with timestamps + CSV export
- Settings persisted to `OdinM.ini` (next to executable)
- ODINC.exe must be in same folder as OdinM.exe

---

## ODIN Dialog Layout
- Dialog width: 285 dialog units
- **TODO:** Make window resizable — CDialogResize partially coded in ODINDlg.h
  - Need: WS_THICKFRAME in ODIN.rc, uncomment CDialogResize inheritance,
    complete anchor map for all ~30 controls, add WM_GETMINMAXINFO

---

## Icon Assets

| File | EXE | Description | Status |
|------|-----|-------------|--------|
| `src/ODIN/res/ODIN.ico` | ODIN.exe | Blue floppy + HDD + arrow | Active ✅ |
| `src/ODINM/res/OdinM.ico` | OdinM.exe | 5 coloured SD cards (= 5 slots) | Active ✅ |
| `src/ODIN/res/ODIN2.ico` | — | Dark SD card + HDD variant | Archived |
| `src/ODIN/res/odinc.png` | — | Terminal `>_` style — future use | PNG only |

---

## Recent Git Commits (modernization branch)

| Commit | Type | Description |
|--------|------|-------------|
| 9f5ee84 | chore | Remove legacy zlib 1.2.3 source tree |
| 67a883b | feat | Expand TCompressionFormat enum for LZ4/LZ4HC/ZSTD |
| 6358b8e | fix | C4244 wchar_t→char narrowing in SaveHashConfig |
| cfdddbc | refactor | COdinManager: 12 raw ptrs → unique_ptr (Phase 3) |
| 42b8e9e | fix | ___chkstk_ms linker error from MinGW LZ4/ZSTD libs |
| 63b843c | feat | Add LZ4/LZ4HC/ZSTD compression support |
| 145b603 | ui | Disable snapshot button + tooltip |
| e94b31f | perf | CRC32 slice-by-8 (~5-8x faster) |
| 2f91129 | feat | DPI v2, Common Controls v6, LVS_EX_DOUBLEBUFFER |

---

## Important Context
- VS2026 = Version 18, MSBuild at `C:\Program Files\Microsoft Visual Studio\18\...`
- Output dirs: `Debug-x64\`, `Debug-Win32\`, `Release-x64\`, `Release-Win32\`
- ODINC.exe may trigger AV false positives (Gen:Variant.Fugrafa) — add output folder to AV exclusions
- WTL path in all projects: `$(SolutionDir)lib\WTL10\Include`
- OdinM: no `_ATL_NO_AUTOMATIC_NAMESPACE` (breaks CDialogImpl); IDC_STATIC needs `#ifndef` guard
- Build command: `msbuild ODIN.sln /p:Configuration=Debug;Platform=x64`

---

## TODO List

### Priority 2 — Do Soon
- [ ] **Dark mode (Win10 1809+)** — `DwmSetWindowAttribute(DWMWA_USE_IMMERSIVE_DARK_MODE)` on WM_CREATE and theme change; add `dwmapi.lib` to ODIN.vcxproj and OdinM.vcxproj
- [ ] **OdinM: Inline progress bar in grid cell** — Custom draw replacing progress text column
- [ ] **OdinM: Status color badges** — NM_CUSTOMDRAW, alternating rows, color by status (blue=Cloning, green=Done, red=Failed)
- [ ] **OdinM: Gray placeholder in empty cells** — Replace plain `-` with styled text

### Priority 3 — Nice to Have
- [ ] **System accent color** — Read from `HKCU\Software\Microsoft\Windows\DWM\AccentColor`
- [ ] **Drive type icons** — `SHGetFileInfo(SHGFI_ICON)` for removable/fixed drives in list
- [ ] **Button hover states** — Custom draw with highlight on WM_MOUSEMOVE
- [ ] **Font** — Segoe UI Variable if available, fallback to Segoe UI
- [ ] **Resizable main window** — Enable CDialogResize in ODINDlg.h (partially coded), WS_THICKFRAME in ODIN.rc, anchor map for ~30 controls, WM_GETMINMAXINFO

### What NOT to Do
- ✗ Migrate to WinUI — massive effort, wrong tool
- ✗ Add .NET — breaks lightweight native nature
- ✗ Redesign layout — users know where things are
- ✗ Add animations — this is a serious data tool
- ✗ Remove log window — power users rely on it
