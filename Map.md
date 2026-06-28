# ODIN Project Map

**Updated:** 2026-06-02

## Current State

| Project | Status | Notes |
|---------|--------|-------|
| zlib | ✅ | Static lib from src/zlib-1.3.2/ |
| libz2 | ✅ | bzip2 static lib |
| ODIN | ✅ | Main GUI app builds and runs |
| ODINC | ✅ | Console version |
| ODINHelp | ✅ | CHM built successfully |
| ODINTest | ✅ | cppunit restored through vcpkg manifest |
| OdinM_py | ✅ | Python multi-drive clone UI |

Native `OdinM.exe` was removed from the solution because it is abandoned and superseded by `OdinM_py`.

---

## Phase Status

| Phase | Status |
|-------|--------|
| Phase 1 — Critical bug fixes | ✅ Complete (6/6) |
| Phase 2 — Build system / VS2026 migration | ✅ Complete |
| Phase 3 — C++ modernization | ✅ Complete (3.3/3.4 deferred by design) |
| Phase 4 — Feature additions | ✅ Complete |
| Phase 5 — Testing | ⏳ Pending |
| Phase 6 — Documentation | ⏳ Partial |
| Phase 7 — Release | ⏳ Pending |

### Phase 3 — Done
All smart pointer and malloc/free work complete. Deferred by design:
- **3.3 Threading** — `CREATE_SUSPENDED`/`WaitForMultipleObjects`/`TerminateThread` have no `std::thread` equivalents without full architecture redesign
- **3.4 Strings** — `ATL::CString` kept in UI code (appropriate for `LoadString`/`FormatMessage`); `std::wstring` already used in core

---

## Known Issues

| Issue | Severity | Notes |
|-------|----------|-------|
| ~~`-list` shows `Size: 0.000B` and `Type: Unknown`~~ | ~~Low~~ | Fixed da95404 — GPT partition style was resetting all geometry to 0 |
| Hardware ODINManager tests disabled | Low | Expected by `ODINTest.ini`; they require a separate formatted drive |

---

## Key File Locations

| File | Purpose |
|------|---------|
| `ODIN.sln` | Solution — 6 native projects |
| `ODIN.vcxproj` | Main GUI application |
| `ODINC.vcxproj` | Console version |
| `OdinM_py/` | Python multi-drive clone tool |
| `ODINTest.vcxproj` | Unit tests |
| `zlib.vcxproj` | zlib 1.3.2 static library |
| `libz2.vcxproj` | bzip2 static library |
| `lib/WTL10/Include/` | WTL 10.0 headers |
| `src/ODIN/` | Main ODIN source |
| `src/zlib-1.3.2/` | zlib 1.3.2 sources |
| `CLAUDE.md` | Project rules for Claude Code |
| `docs/MODERNIZATION_CHECKLIST.md` | Detailed per-task progress |

---

## OdinM_py Feature Summary
- Clone one ODIN image to up to **5 USB drives simultaneously**
- **SHA-1 and SHA-256** hash verification post-clone
- Auto-clone on device insertion
- Activity log with timestamps + CSV export
- Settings persisted to `odinm_py.ini`
- Uses `ODINC.exe` for restore/backup operations

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
| `OdinM_py/assets/OdinM.ico` | OdinM_py | 5 coloured SD cards (= 5 slots) | Active ✅ |
| `src/ODIN/res/ODIN2.ico` | — | Dark SD card + HDD variant | Archived |
| `src/ODIN/res/odinc.png` | — | Terminal `>_` style — future use | PNG only |

---

## Recent Git Commits (modernization branch)

| Commit | Type | Description |
|--------|------|-------------|
| f340c57 | fix | _O_U8TEXT — fix space-between-chars in -list output |
| 936094d | fix | freopen(CONOUT$) — fix wcout silent-drop over inherited handles |
| bfb5731 | refactor | CommandLineProcessor + ODINDlg: raw ptrs → unique_ptr |
| a894723 | refactor | ImageStream: malloc→vector; threading/strings deferred |
| f6e56cf | chore | scripts/build.bat |
| 9f5ee84 | chore | Remove legacy zlib 1.2.3 source tree |
| 67a883b | feat | Expand TCompressionFormat enum for LZ4/LZ4HC/ZSTD |
| cfdddbc | refactor | COdinManager: 12 raw ptrs → unique_ptr (Phase 3) |
| 63b843c | feat | Add LZ4/LZ4HC/ZSTD compression support |
| e94b31f | perf | CRC32 slice-by-8 (~5-8x faster) |

---

## Important Context
- VS2026 = Version 18, MSBuild at `C:\Program Files\Microsoft Visual Studio\18\...`
- Output dirs: `Debug-x64\`, `Debug-Win32\`, `Release-x64\`, `Release-Win32\`
- ODINC.exe may trigger AV false positives (Gen:Variant.Fugrafa) — add output folder to AV exclusions
- WTL path in all projects: `$(SolutionDir)lib\WTL10\Include`
- Native `OdinM.exe` is removed; use `OdinM_py` for multi-drive flashing
- Build command: `msbuild ODIN.sln /p:Configuration=Debug;Platform=x64`

---

## TODO List

### Priority 2 — Do Soon
- [ ] **Dark mode (Win10 1809+)** — Partially implemented (title bar, menu, controls, brushes) then **disabled**. Blocker: `SysHeader32` text stays dark. Root cause: the ListView handles header `NM_CUSTOMDRAW` internally — it never reaches the dialog message map. `AllowDarkModeForWindow` + `SetWindowTheme` on the header changes background but not text color. Fix options: (a) `SetWindowSubclass` on the ListView to intercept header `WM_NOTIFY`; (b) set `HDF_OWNERDRAW` per item + handle `WM_DRAWITEM` from within a ListView subclass. All dark mode code is in `ODINDlg.cpp::ApplyDarkMode` — re-enable by removing the early `return;` at the top of that function.

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
