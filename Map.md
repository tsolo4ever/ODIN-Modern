# ODIN Project Map

## Current State

### ✅ Completed Features
- **WTL 10.0 Upgrade** - Migrated from WTL 7.5 to WTL 10.0
- **Auto-Flash Feature** - Automatic detection and restore of removable drives
  - Configurable target size (default 8GB, ±10% tolerance)
  - One-time warning on enable
  - Auto-triggers restore on device insertion
  - Works with CF cards, SD cards, USB drives
- **UI Improvements**
  - Dialog size increased to 320x380 (was 275x350)
  - Auto-flash controls relocated to bottom right
  - Removed window resize capability
  - Input box for configurable size

### 🔧 Working Components
- Core backup/restore functionality
- Volume shadow copy (VSS) support
- Compression (GZip, BZip2)
- Split file support
- Command-line interface (ODINC.exe)

## Key File Locations

### Main Dialog
- **src/ODIN/ODINDlg.h** - Dialog class declaration
- **src/ODIN/ODINDlg.cpp** - Dialog implementation, auto-flash logic
- **src/ODIN/ODIN.rc** - Resource file (dialog layout, controls)
- **src/ODIN/resource.h** - Resource IDs

### Core Functionality
- **src/ODIN/OdinManager.cpp** - Main operation manager
- **src/ODIN/DriveList.cpp** - Drive detection and enumeration
- **src/ODIN/ImageStream.cpp** - Image file I/O
- **src/ODIN/CompressionThread.cpp** - Compression worker
- **src/ODIN/DecompressionThread.cpp** - Decompression worker
- **src/ODIN/ReadThread.cpp** - Disk read operations
- **src/ODIN/WriteThread.cpp** - Disk write operations

### Command Line
- **src/ODINC/ODINC.cpp** - Console application entry point

### Configuration
- **src/ODIN/Config.h** - Configuration system (DECLARE_ENTRY macros)
- **ODIN.ini** - User settings (created at runtime)

## Important Configuration Keys

### Auto-Flash Settings (in ODIN.ini)
- `AutoFlashEnabled` - Enable/disable auto-flash (bool)
- `AutoFlashTargetSizeGB` - Target drive size in GB (int, default 8)
- `AutoFlashWarningShown` - Tracks if warning was displayed (bool)

## Next Steps - OdinM Multi-Drive Flash

### 🎯 Project Goal
Create OdinM application to handle up to 5 simultaneous flash operations by spawning multiple ODINC.exe processes.

### Architecture Overview
- **OdinM** - New overlay GUI application
- Detects matching removable drives (reuse existing detection)
- Auto-starts new ODINC.exe process for each detected drive
- Tracks up to 5 concurrent processes
- Displays progress in list view

### Implementation Plan (Estimated: 1-2 days)

#### Phase 1: Core Multi-Process (4-6 hours)
- [ ] Create OdinM WTL application skeleton
- [ ] Copy/adapt drive detection from ODIN
- [ ] Implement ODINC command builder: `ODINC.exe -restore -source <image> -target <drive>`
- [ ] Process spawning with CreateProcess()
- [ ] Track up to 5 process handles
- [ ] Handle WM_DEVICECHANGE for auto-start

#### Phase 2: Progress Tracking (3-4 hours)
- [ ] Create list view UI (5 rows for drives)
- [ ] Columns: [Drive Name] [Status] [Progress %]
- [ ] Redirect ODINC stdout/stderr (pipe)
- [ ] Parse console output for progress
- [ ] Update UI from process output
- [ ] Handle process completion/errors
- [ ] Show success/failure status

#### Phase 3: Polish & Config (1-2 hours)
- [ ] Image file selection dialog
- [ ] Enable/disable auto-flash checkbox
- [ ] Max concurrent limit setting (1-5)
- [ ] Persist settings to OdinM.ini
- [ ] Error handling and logging
- [ ] Testing with multiple drives

### Technical Notes

**Process Command Format:**
```
ODINC.exe -restore -source "C:\path\to\image.img" -target 0
```

**Progress Parsing:**
- Monitor ODINC stdout for progress messages
- Parse percentage from console output
- Update corresponding UI row

**Device Detection:**
- Reuse `DetectCFCard()` logic from ODINDlg.cpp
- Check for drives not already being flashed
- Auto-start when slot available (< 5 active)

**Advantages:**
- No ODIN core refactoring needed
- Each ODINC.exe process is isolated
- OS handles resource management
- Simple implementation
- Easy to test and debug

## Known Issues
- None currently

## Important Patterns

### Drive Detection
```cpp
// Detect removable hard disks with size matching
const unsigned __int64 targetSize = (unsigned __int64)fAutoFlashTargetSizeGB * 1024 * 1024 * 1024;
const unsigned __int64 tolerance = targetSize / 10; // 10% tolerance

if (di->IsCompleteHardDisk() && 
    di->GetDriveType() == driveRemovable &&
    driveSize within tolerance)
```

### Configuration Persistence
```cpp
DECLARE_SECTION()
DECLARE_ENTRY(bool, fAutoFlashEnabled)
DECLARE_ENTRY(int, fAutoFlashTargetSizeGB)
```

### Device Change Notification
```cpp
LRESULT OnDeviceChanged(UINT, WPARAM nEventType, LPARAM lParam, BOOL&)
{
    if (nEventType == DBT_DEVICEARRIVAL) {
        // Detect and trigger auto-flash
    }
}
```

## Git Status
- Branch: `modernization`
- Last commits:
  - `d1b8e7a` - ui: Increase main dialog window size
  - `f513378` - feat: Add one-time warning when enabling auto-flash
  - `7e40342` - feat: Improve auto-flash UI with configurable size
  - `6b19efb` - feat: Add auto-flash feature for 8GB removable disks

## Build Information
- IDE: Visual Studio 2022+
- Platform: Windows x64
- Dependencies: WTL 10.0, ATL, bzip2, zlib

---
*Last Updated: 2026-02-22*
