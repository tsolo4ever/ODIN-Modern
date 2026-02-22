# ODIN Project Map

## Current State

### ✅ Completed Features
- **WTL 10.0 Upgrade** - Migrated from WTL 7.5 to WTL 10.0
- **Auto-Flash Feature** - Automatic detection and restore of removable drives
  - Configurable target size (default 8GB, ±10% tolerance)
  - One-time warning on enable
  - Auto-triggers restore on device insertion
- **UI Improvements** - Dialog enlarged, controls reorganised
- **OdinM Multi-Drive Clone Tool** - All source files implemented ✅
  - Up to 5 simultaneous clones via ODINC.exe spawning
  - SHA-1 and SHA-256 hash verification per drive
  - Auto-clone on device insertion
  - Hash config saved as sidecar `.hashcfg` file
  - CSV export of results
  - Activity log with timestamps

### 🔧 Working Components
- Core backup/restore functionality (ODIN.exe / ODINC.exe)
- Volume shadow copy (VSS) support
- Compression (GZip, BZip2), split file support

---

## Key File Locations

### ODIN Main Application
| File | Purpose |
|------|---------|
| `src/ODIN/ODINDlg.h/cpp` | Main dialog (GUI + auto-flash) |
| `src/ODIN/ODIN.rc` | Resource file |
| `src/ODIN/resource.h` | Resource IDs |
| `src/ODIN/OdinManager.cpp` | Core operation manager |
| `src/ODIN/DriveList.cpp` | Drive detection |
| `src/ODIN/ImageStream.cpp` | Image file I/O |
| `src/ODIN/CommandLineProcessor.cpp` | Console mode |
| `src/ODINC/ODINC.cpp` | Console wrapper (SubSystem=Console) |

### OdinM Multi-Drive Tool
| File | Purpose |
|------|---------|
| `src/ODINM/OdinM.cpp` | Application entry point |
| `src/ODINM/OdinM.rc` | Dialog resources (main + hash config) |
| `src/ODINM/resource.h` | Resource ID definitions |
| `src/ODINM/stdafx.h/cpp` | Precompiled headers (WTL 10.0 / ATL) |
| `src/ODINM/OdinMDlg.h/cpp` | Main dialog — drive list, cloning, log |
| `src/ODINM/DriveSlot.h/cpp` | Per-slot drive + clone state |
| `src/ODINM/HashCalculator.h/cpp` | SHA-1 / SHA-256 via Windows CryptoAPI |
| `src/ODINM/HashConfigDlg.h/cpp` | Hash configuration popup dialog |

### Configuration
| File | Purpose |
|------|---------|
| `src/ODIN/Config.h` | DECLARE_ENTRY macro system |
| `OdinM.ini` | OdinM settings (created at runtime, next to exe) |
| `<image>.hashcfg` | Per-image hash config sidecar file |

---

## OdinM Architecture

```
OdinM.exe (GUI)
    ↓  manages
5 × CDriveSlot objects
    ↓  each spawns
ODINC.exe --source <image> --target <drive>
    ↓  on completion
CHashCalculator reads drive device → SHA-1 / SHA-256
    ↓  compares with
<image>.hashcfg  (SHA1=...\r\nSHA256=...\r\n)
    ↓  result shown in
ListView + Activity Log
```

### Key Classes
- `COdinMDlg` — main dialog, WM_TIMER polls every 2 sec, WM_DEVICECHANGE for hot-plug
- `CDriveSlot` — tracks letter, name, size, status (Empty/Ready/Cloning/Verifying/Complete/Failed/Stopped), PID
- `CHashCalculator` — static methods: `CalculateSHA1`, `CalculateSHA256`, `CalculateBothHashes`
- `CHashConfigDlg` — takes `HashConfig&` ref, edits in-place, saves via `COdinMDlg::SaveHashConfig()`

### HashConfig Struct (in OdinMDlg.h)
```cpp
struct HashConfig {
    int partitionNumber = 1;
    bool sha1Enabled = true;   bool sha256Enabled = false;
    std::wstring sha1Expected; std::wstring sha256Expected;
    bool failOnSha1Mismatch = true; bool failOnSha256Mismatch = false;
    std::wstring imagePath;
};
```

---

## What Still Needs Doing — OdinM

### 🔴 Not Yet Done (Build Setup)
- [ ] **OdinM.vcxproj** — Visual Studio project file (add to solution in VS)
- [ ] **OdinM.sln entry** — Add OdinM project to ODIN.sln
- [ ] **res/OdinM.ico** — Application icon (can copy ODIN icon)
- [ ] **WTL include path** — Set `lib/WTL10/Include` in project Additional Include Directories
- [ ] **Linker settings** — Add `crypt32.lib`, set SubSystem=Windows, UAC=requireAdministrator

### 🟡 Known Limitations / Future Work
- `VerifyDrive` hashes the entire drive; partition-specific offset needs ODIN file header parsing
- ODINC.exe command-line format assumed (`--source` / `--target`); verify actual args
- Progress % during clone not tracked (would need stdout pipe from ODINC)

---

## ODIN vs ODINC Reminder

| Executable | SubSystem | Use Case |
|---|---|---|
| ODIN.exe | Windows (no console) | GUI mode |
| ODINC.exe | Console | Command-line / OdinM subprocess |

OdinM spawns `ODINC.exe` with `CREATE_NO_WINDOW` for silent background cloning.

---

## Important Patterns

### Auto-Flash Drive Detection (ODIN)
```cpp
if (di->IsCompleteHardDisk() &&
    di->GetDriveType() == driveRemovable &&
    driveSize within 10% of targetSize)
```

### INI Settings (OdinM)
Stored in `OdinM.ini` next to `OdinM.exe`:
```ini
[Settings]
ImagePath=C:\images\sentinel.img
MaxConcurrent=2
AutoClone=0
```

### Hash Config Sidecar
Stored as `<imagepath>.hashcfg`:
```
SHA1=FD218079E7D01CF746042EE08F05F7BD7DA2A8E2
SHA256=5A2C8F9E...
```

---

## Git Status
- Branch: `modernization`
- Recent commits:
  - `c898822` — feat: Implement OdinM source files (HashCalculator, HashConfigDlg, OdinMDlg, OdinM.rc)
  - `104a138` — feat: Add OdinM multi-drive clone tool project structure
  - `38b6bff` — feat: Add -output flag to write drive list to file
  - `f513378` — feat: Add one-time warning when enabling auto-flash

## Build Information
- IDE: Visual Studio 2022+ (project files exist for ODIN/ODINC; OdinM.vcxproj TBD)
- Platform: Windows x64
- Dependencies: WTL 10.0 (`lib/WTL10/`), ATL, bzip2, zlib, Windows CryptoAPI

---
*Last Updated: 2026-02-22 — OdinM source files complete, build setup remaining*
