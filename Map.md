# ODIN Project Map

**Last Updated:** 2026-02-21  
**Current Status:** Planning modernization and auto-flash feature

---

## 🎯 Project Overview

**ODIN** = Open Disk Imager in a Nutshell  
A Windows disk imaging tool for backup/restore of partitions and drives.

**Current Version:** 0.3.x (2008-era codebase)  
**Target:** Modernize to VS2022/C++17 + Add CF card auto-flash feature

---

## 📍 Current State

### ✅ What's Working
- Complete backup/restore functionality
- Compression (gzip, bzip2)
- VSS snapshot integration
- Split file support
- GUI (WTL/ATL) and CLI (ODINC.exe) interfaces
- Multi-threaded I/O pipeline

### ⚠️ Known Issues (from CODE_REVIEW.md)
- Race condition in buffer queue management
- Memory leaks in exception paths
- Integer overflow risks in size conversions
- Unchecked pointer dereferences
- Security vulnerabilities (boot sector validation)
- PowerShell piping doesn't work with ODINC.exe

### 🚧 In Progress
- Code review completed (see CODE_REVIEW.md)
- Modernization roadmap created
- Planning auto-flash feature for CF cards

---

## 📁 Key Files & Locations

### Core Application
- **GUI Entry Point:** `src/ODIN/ODIN.cpp` (WinMain)
- **CLI Wrapper:** `src/ODINC/ODINC.cpp` (spawns ODIN.exe)
- **Main Dialog:** `src/ODIN/ODINDlg.{h,cpp}` - GUI implementation
- **Manager:** `src/ODIN/OdinManager.{h,cpp}` - Core business logic

### Threading & I/O
- **Read Thread:** `src/ODIN/ReadThread.{h,cpp}`
- **Write Thread:** `src/ODIN/WriteThread.{h,cpp}`
- **Compression:** `src/ODIN/CompressionThread.{h,cpp}`
- **Buffer Queue:** `src/ODIN/BufferQueue.{h,cpp}` ⚠️ HAS RACE CONDITION

### Disk Operations
- **Image Stream:** `src/ODIN/ImageStream.{h,cpp}` - File & disk I/O
- **Drive List:** `src/ODIN/DriveList.{h,cpp}` - Enumerate drives
- **File Header:** `src/ODIN/FileHeader.{h,cpp}` - Image file format

### CLI Interface
- **Command Processor:** `src/ODIN/CommandLineProcessor.{h,cpp}`
- **List Drives:** `CommandLineProcessor::ListDrives()` method

### Configuration
- **Config System:** `src/ODIN/Config.{h,cpp}` - INI file wrapper
- **INI Wrapper:** `src/ODIN/IniWrapper.{h,cpp}` - Low-level INI access

### External Libraries
- **zlib:** `src/zlib.1.2.3/` (outdated - needs update to 1.3.1)
- **bzip2:** `src/bzip2-1.0.5/` (outdated - needs update to 1.0.8)

---

## 🎯 Immediate Goals

### Goal 1: Fix PowerShell Output (Quick Win)
**File:** `src/ODINC/ODINC.cpp`  
**Issue:** Output from `odinc -list` can't be piped/redirected  
**Fix:** Add proper handle inheritance in CreateProcess call  
**Effort:** 30 minutes

### Goal 2: Add Auto-Flash Feature
**Target Files:**
- `src/ODIN/ODINDlg.{h,cpp}` - Add detection & UI
- `src/ODIN/Config.h` - Add config entries
- `src/ODIN/ODIN.rc` - Add UI elements
- `src/ODIN/resource.h` - Add control IDs

**Features Needed:**
- Monitor WM_DEVICECHANGE for CF card insertion
- Detect CF card by size/label
- Auto-trigger restore operation
- Optional confirmation dialog
- Auto-eject when done

### Goal 3: Critical Bug Fixes
**See:** CODE_REVIEW.md Section 2 (Critical Issues)
- Fix buffer queue race condition
- Fix memory leaks
- Add overflow protection

---

## 📋 Next Steps

### Option A: Quick Path (CF Auto-Flash Only)
1. Fix ODINC.cpp for PowerShell (30 min)
2. Add auto-flash to GUI (6-8 hours)
3. Test and deploy

### Option B: Full Modernization
1. Setup VS2022 environment
2. Fix critical bugs (Phase 1)
3. Update build system (Phase 2)
4. Modernize C++ code (Phase 3)
5. Add auto-flash feature (Phase 4)
6. Testing & docs (Phases 5-7)

**Total Effort:** 32-53 hours (~1 work week)

**See:** MODERNIZATION_CHECKLIST.md for detailed task breakdown

---

## 🔑 Important Decisions Needed

1. **Auto-Flash UI Style:**
   - [ ] Full settings dialog (professional)
   - [ ] Simple checkbox + config file (quick)
   - [ ] Hidden hotkey (quickest)

2. **CF Card Specs:**
   - [ ] Size: _____ MB
   - [ ] Label: _____
   - [ ] Multiple sizes or fixed?

3. **Behavior:**
   - [ ] Show confirmation before flashing?
   - [ ] Auto-eject when complete?
   - [ ] Sound notification?

4. **Scope:**
   - [ ] Quick auto-flash only?
   - [ ] Full modernization?
   - [ ] Hybrid approach?

---

## 🐛 Bug Tracking

### Critical (Fix Before Release)
- [ ] Buffer queue race condition (`BufferQueue.cpp:GetChunk()`)
- [ ] Memory leak in `OdinManager.cpp:WaitToCompleteOperation()`
- [ ] Integer overflow in size calculations
- [ ] Unchecked pointers after `GetChunk()`

### High Priority
- [ ] Boot sector validation (security)
- [ ] Exception handling incomplete (catch(...))
- [ ] ODINC.cpp handle inheritance

### Medium Priority
- [ ] Silent volume resize failures
- [ ] Path traversal validation
- [ ] Mixed malloc/new usage

---

## 📚 Documentation

- ✅ **CODE_REVIEW.md** - Comprehensive code analysis
- ✅ **Map.md** (this file) - Project state & navigation
- ⏳ **MODERNIZATION_CHECKLIST.md** - Detailed implementation plan
- 📖 **doc/readme.txt** - Original docs (minimal)
- 📖 **doc/HowToBuild.txt** - VS2008 build instructions (outdated)

---

## 💡 Quick Reference

### Build Commands (VS2008 - Current)
```
Open ODIN.sln in Visual C++ 2008
Build -> Build Solution
```

### Build Commands (VS2022 - After Migration)
```
Open ODIN.sln in Visual Studio 2022
Build -> Build Solution
```

### Run Commands
```powershell
# GUI
.\ODIN.exe

# CLI (current - broken piping)
.\odinc.exe -list

# CLI (after fix - working piping)
.\odinc.exe -list > drives.txt
.\odinc.exe -list | Select-String "Drive"
```

---

## 🔄 Workflow Reminders

1. **Always check Map.md** at start of new session
2. **Update Map.md** when making significant changes
3. **Commit frequently** (see git rules)
4. **Work in small groups** (see rules)
5. **Test after each phase**

---

## 📞 Contact & Resources

- **Original Project:** http://sourceforge.net/projects/odin-win
- **License:** GPL v3
- **Last Active:** ~2009 (we're reviving it!)

---

*This Map.md file should be updated whenever project state changes significantly.*
