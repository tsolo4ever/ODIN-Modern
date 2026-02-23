# OdinM - Multi-Drive Clone Tool

## Overview
OdinM is a Windows GUI application that clones a single ODIN disk image to up to **5 USB drives simultaneously**, with optional SHA-1 / SHA-256 hash verification after each clone.

## Current Status
**Source Complete** — All `.cpp` / `.h` / `.rc` files implemented.  
**Remaining:** Add Visual Studio project file (`OdinM.vcxproj`) to the solution.

---

## Files in This Directory

| File | Status | Purpose |
|------|--------|---------|
| `stdafx.h/cpp` | ✅ | Precompiled headers (ATL/WTL 10.0) |
| `resource.h` | ✅ | Resource ID definitions |
| `OdinM.rc` | ✅ | Dialog resources — main window + hash config popup |
| `OdinM.cpp` | ✅ | Application entry point (`_tWinMain`) |
| `OdinMDlg.h/cpp` | ✅ | Main dialog implementation |
| `DriveSlot.h/cpp` | ✅ | Per-slot drive state tracking |
| `HashCalculator.h/cpp` | ✅ | SHA-1 / SHA-256 via Windows CryptoAPI |
| `HashConfigDlg.h/cpp` | ✅ | Hash configuration popup dialog |

---

## Features
- Clone image to up to **5 drives** concurrently (configurable max)
- **SHA-1 and SHA-256** hash verification post-clone
- **Auto-clone** on device insertion (WM_DEVICECHANGE)
- Hash values stored in `<image>.hashcfg` sidecar file
- **Activity log** with timestamps
- **Export results** to CSV
- Settings persisted to `OdinM.ini` (next to executable)

---

## Architecture

```
OdinM.exe
    │
    ├─ COdinMDlg          Main dialog
    │    ├─ CDriveSlot×5  Per-slot state (drive letter, status, PID, verify result)
    │    ├─ HashConfig     Expected SHA-1 / SHA-256 values
    │    └─ WM_TIMER       Polls every 2 sec — checks process exit, detect new drives
    │
    ├─ CHashConfigDlg     Modal popup — set/calculate expected hashes
    │
    └─ CHashCalculator    Static helper — CalculateSHA1, CalculateSHA256, CalculateBothHashes
```

### Clone Workflow
1. User selects image file → loads `.hashcfg` sidecar if present
2. Removable drives detected → assigned to slots
3. **Start All** → spawns `ODINC.exe --source <img> --target <drive>` per slot
4. Timer polls `GetExitCodeProcess` every 2 seconds
5. On clone exit(0) → runs `VerifyDrive` (reads drive device, calculates hashes)
6. Compares with expected values → marks slot Complete or Failed
7. Stop-on-fail option halts remaining clones on first mismatch

---

## Build Requirements

### Project Setup (in Visual Studio)
1. **Right-click solution → Add → New Project → Empty C++ Project**, name: `OdinM`
2. Add all `.cpp` files from this directory
3. Set project properties:

| Property | Value |
|----------|-------|
| C/C++ → Additional Include Directories | `$(SolutionDir)lib\WTL10\Include` |
| C/C++ → Precompiled Header | Use — `stdafx.h` |
| Linker → SubSystem | Windows (`/SUBSYSTEM:WINDOWS`) |
| Linker → Additional Dependencies | `shlwapi.lib; crypt32.lib; version.lib` |
| Manifest → UAC Execution Level | `requireAdministrator` |
| Character Set | Use Unicode Character Set |

### Dependencies
- **WTL 10.0** — `lib/WTL10/Include/` (already in repo)
- **Windows SDK** — ATL, CryptoAPI, `winioctl.h`
- **ODINC.exe** — must be in same directory as `OdinM.exe`

---

## Hash Config File Format
Stored as `<imagepath>.hashcfg` (plain text):
```
SHA1=FD218079E7D01CF746042EE08F05F7BD7DA2A8E2
SHA256=5A2C8F9E3D7B1A4C6E2F8D9C2B4A6E1F3C5D7B9A...
```
Values are stored uppercase; comparison is case-sensitive (both sides normalised to uppercase on save).

---

## Known Limitations
- **Hash of whole drive** — `VerifyDrive` currently hashes the entire drive device.  
  For partition-specific hashing, ODIN file header parsing is needed to determine offset/size.
- **ODINC command-line format** — assumed to be `--source` / `--target`.  
  Verify against actual `ODINC.exe` argument handling before use.
- **No progress bar during clone** — would require piping ODINC stdout; currently shows "Cloning" until process exits.

---

## License
GPL v3 (same as ODIN)
