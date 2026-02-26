# OdinM Implementation Guide

## Current Progress Summary

**Status:** All source files complete — build setup remaining

### ✅ Completed Files
```
src/ODINM/
├── stdafx.h/cpp          ✅ Precompiled headers (ATL + WTL 10.0)
├── resource.h            ✅ Resource ID definitions
├── OdinM.rc              ✅ Dialog resources (main + hash config)
├── OdinM.cpp             ✅ Application entry point
├── OdinMDlg.h/cpp        ✅ Main dialog — full implementation
├── DriveSlot.h/cpp       ✅ Drive slot class
├── HashCalculator.h/cpp  ✅ SHA-1 / SHA-256 via CryptoAPI
└── HashConfigDlg.h/cpp   ✅ Hash configuration dialog
```

### ❌ Still Needed (Build Setup Only)
```
OdinM.vcxproj             ❌ Visual Studio project file
ODIN.sln entry            ❌ Add OdinM to solution
res/OdinM.ico             ❌ Application icon
```

---

## Step 1: Create Visual Studio Project

Since all source files exist, the fastest path is:

1. Open `ODIN.sln` in Visual Studio
2. **Right-click solution → Add → New Project**
3. Choose **Empty C++ Project**, name it `OdinM`
4. **Right-click OdinM project → Add → Existing Item**
5. Add ALL files from `src/ODINM/` (`.cpp`, `.h`, `.rc`)

---

## Step 2: Configure Project Properties

Set these in **Project Properties** (all configurations):

### C/C++ → General
| Property | Value |
|----------|-------|
| Additional Include Directories | `$(SolutionDir)lib\WTL10\Include;%(AdditionalIncludeDirectories)` |
| Character Set | Use Unicode Character Set |

### C/C++ → Precompiled Headers
| Property | Value |
|----------|-------|
| Precompiled Header | Use (`/Yu`) |
| Precompiled Header File | `stdafx.h` |

> For `stdafx.cpp` only: set Precompiled Header to **Create** (`/Yc`)

### Linker → System
| Property | Value |
|----------|-------|
| SubSystem | Windows (`/SUBSYSTEM:WINDOWS`) |
| Entry Point | `wWinMainCRTStartup` |

### Linker → Input
```
shlwapi.lib
crypt32.lib
version.lib
```

### Manifest Tool → Input and Output
| Property | Value |
|----------|-------|
| UAC Execution Level | requireAdministrator |

---

## Step 3: Add Application Icon

Option A — Copy ODIN icon:
```
copy src\ODIN\res\ODIN.ico src\ODINM\res\OdinM.ico
```

Then add to `OdinM.rc`:
```rc
IDR_MAINFRAME   ICON   "res\\OdinM.ico"
```

Option B — Skip icon for now (app will use default)

---

## Step 4: Verify ODINC.exe Arguments

`OdinMDlg.cpp` builds the ODINC command as:
```
"<dir>\ODINC.exe" --source "<imagepath>" --target <driveletter>
```

**Before testing**, verify this matches the actual `ODINC.exe` command-line syntax.
Check `src/ODINC/ODINC.cpp` and `src/ODIN/CommandLineProcessor.cpp` for accepted flags.

If the format differs, update `StartClone()` in `OdinMDlg.cpp`:
```cpp
std::wstring cmd = L"\"" + std::wstring(exe) + L"\\ODINC.exe\""
    + L" --source \"" + m_imagePath + L"\" --target " + slot->GetDriveLetter();
```

---

## Step 5: Build and Test

### Phase 1 — Dialog appears
1. Build in Debug x64
2. Run OdinM.exe
3. Verify main dialog appears with 5-row list view

### Phase 2 — Drive detection
1. Insert a USB drive
2. Verify it appears in the list (auto-detect on WM_DEVICECHANGE)
3. Verify drive name and size show correctly

### Phase 3 — Clone
1. Select an ODIN image with Browse
2. Click Start All
3. Verify ODINC.exe launches (check Task Manager)
4. Verify status changes to "Cloning"
5. Wait for completion → status changes to "Complete" or "Verifying"

### Phase 4 — Hash verification
1. Open Configure Hashes, calculate hashes from image
2. Save configuration
3. Run another clone
4. Verify hash verification runs and PASS/FAIL shows in list

---

## Implementation Notes

### Timer-Based Process Monitoring
`COdinMDlg::OnTimer` fires every 2 seconds and calls `GetExitCodeProcess` on each active ODINC.exe PID.  
When exit code ≠ `STILL_ACTIVE`: triggers `VerifyDrive` (if enabled) or marks slot Complete.

### Hash Verification Scope
`VerifyDrive` currently hashes the **entire drive device** (`\\.\F:`).  
For partition-specific hashing, you would need to:
1. Parse the ODIN image file header to determine partition offsets/sizes
2. Pass the correct `offset` and `size` to `CHashCalculator::CalculateBothHashes`

### Hash Config Sidecar Format
Simple key=value text stored at `<imagepath>.hashcfg`:
```
SHA1=FD218079E7D01CF746042EE08F05F7BD7DA2A8E2
SHA256=5A2C8F9E...
```
Loaded by `LoadHashConfig()`, saved by `SaveHashConfig()`.

### Settings Storage
`OdinM.ini` lives next to `OdinM.exe` (determined at runtime via `GetModuleFileNameW`).
```ini
[Settings]
ImagePath=C:\images\sentinel.img
MaxConcurrent=2
AutoClone=0
```

---

## Troubleshooting

| Problem | Likely Cause | Fix |
|---------|-------------|-----|
| WTL headers not found | Missing include path | Add `lib\WTL10\Include` to project |
| `crypt32.lib` linker error | Missing library | Add to Linker → Additional Dependencies |
| Dialog does not appear | Wrong SubSystem | Ensure `/SUBSYSTEM:WINDOWS` and `wWinMainCRTStartup` |
| ODINC not found | Path issue | Ensure ODINC.exe is in same directory as OdinM.exe |
| Hash always fails | Case mismatch | Both sides normalised to uppercase on save — check `.hashcfg` |
| Drive not detected | Not DRIVE_REMOVABLE | `RefreshDrives` uses `GetDriveTypeW` — only picks up removable drives |
| Access denied on drive open | Not admin | Set UAC level to `requireAdministrator` in manifest |

---

## Key Source Code Locations

| Feature | File | Function |
|---------|------|----------|
| Drive detection | `OdinMDlg.cpp` | `RefreshDrives()`, `DetectNewDrives()` |
| Clone launch | `OdinMDlg.cpp` | `StartClone(int idx)` |
| Process monitoring | `OdinMDlg.cpp` | `OnTimer()` |
| Hash verification | `OdinMDlg.cpp` | `VerifyDrive(int idx)` |
| Hash calculation | `HashCalculator.cpp` | `CalculateBothHashes()` |
| Hash config UI | `HashConfigDlg.cpp` | `OnCalculateAll()`, `OnLoadFile()` |
| Config persistence | `OdinMDlg.cpp` | `LoadSettings()`, `SaveSettings()` |
| Hash persistence | `OdinMDlg.cpp` | `LoadHashConfig()`, `SaveHashConfig()` |

---

*Last Updated: 2026-02-22 — All source files complete*
