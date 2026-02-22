# OdinM - Multi-Drive Clone Tool

## Overview
OdinM is a Windows application for simultaneously cloning disk images to multiple USB drives with SHA hash verification.

## Current Status
**In Development** - Basic structure created, implementation in progress

## Features (Planned)
- Clone up to 5 drives simultaneously
- SHA-1 and SHA-256 hash verification
- Auto-clone on device insertion
- One-time hash configuration per image
- Export results to CSV
- Activity logging

## Files in This Directory

### Completed
- `stdafx.h/cpp` - Precompiled headers
- `resource.h` - Resource ID definitions
- `DriveSlot.h/cpp` - Drive slot management class
- `OdinMDlg.h` - Main dialog header
- `README.md` - This file

### To Be Created
- `OdinMDlg.cpp` - Main dialog implementation
- `HashCalculator.h/cpp` - SHA hash calculation
- `HashConfigDlg.h/cpp` - Hash configuration dialog
- `OdinM.cpp` - Application entry point
- `OdinM.rc` - Resource script
- `res/OdinM.ico` - Application icon

## Build Requirements
- Visual Studio 2026 (or compatible)
- WTL 10.0 (included in lib/WTL10/)
- Windows SDK
- Administrator privileges (for drive access)

## Dependencies
- Windows CryptoAPI (for SHA hashes)
- ODINC.exe (for actual cloning)
- WTL 10.0 (for GUI)

## Implementation Guide
See **`../../docs/ODINM_IMPLEMENTATION_GUIDE.md`** for detailed implementation steps, code samples, and troubleshooting.

## Quick Start for Development

1. **Review the specification:**
   - Read `../../docs/ODINM_SPECIFICATION.md`
   - Read `../../docs/ODINM_IMPLEMENTATION_GUIDE.md`

2. **Create remaining files:**
   - Follow the implementation guide step-by-step
   - Copy code samples provided in the guide

3. **Add to Visual Studio:**
   - Right-click solution → Add → New Project
   - Name: "OdinM"
   - Add all files from this directory
   - Configure project properties (see implementation guide)

4. **Build and test:**
   - Build in Debug configuration first
   - Test incrementally as you add features

## Simplified Implementation Strategy

If you want to get something working quickly:

1. **Phase 1:** Basic dialog that shows up
2. **Phase 2:** Drive detection and list view
3. **Phase 3:** Spawn ODINC.exe for cloning
4. **Phase 4:** Hash calculation and verification
5. **Phase 5:** Full UI with all features

Estimated time: 6-10 hours total

## Architecture

```
OdinM (GUI)
    ↓
Manages 5 DriveSlots
    ↓
Each slot spawns ODINC.exe process
    ↓
After clone: HashCalculator verifies partition
    ↓
Results displayed in UI
```

## Key Classes

- `COdinMDlg` - Main dialog, orchestrates everything
- `CDriveSlot` - Represents one of 5 drive slots
- `CHashCalculator` - SHA hash calculation utility
- `CHashConfigDlg` - Hash configuration dialog
- `HashConfig` - Struct holding hash configuration

## Notes

- Uses only SHA-1 and SHA-256 (proprietary hashes CDCK/Kobe4 removed per user request)
- Reuses ODIN code where possible (DriveList, DriveUtil, Config)
- Requires administrator rights for drive access
- Uses WTL for native Windows UI

## License
GPL v3 (same as ODIN)

## Contact
See main ODIN documentation for project information.
