# OdinM Quick Start Guide

## What You Have Now

### ✅ Completed Files in `src/ODINM/`
1. **stdafx.h/cpp** - Precompiled headers with all necessary includes
2. **resource.h** - Resource IDs (simplified for SHA-1/SHA-256 only)
3. **DriveSlot.h/cpp** - Class for managing individual drive operations
4. **OdinMDlg.h** - Main dialog class declaration
5. **README.md** - Project overview

### ✅ Documentation Created
1. **ODINM_SPECIFICATION.md** - Full specification (updated for SHA-only)
2. **ODINM_IMPLEMENTATION_GUIDE.md** - Complete implementation guide with code samples

## What You Need to Do at Home

### Step-by-Step Checklist

#### 1. Create Missing Source Files (Copy from Implementation Guide)
- [ ] `src/ODINM/HashCalculator.h` - SHA hash calculation header
- [ ] `src/ODINM/HashCalculator.cpp` - SHA hash implementation
- [ ] `src/ODINM/HashConfigDlg.h` - Hash config dialog header
- [ ] `src/ODINM/HashConfigDlg.cpp` - Hash config dialog implementation  
- [ ] `src/ODINM/OdinMDlg.cpp` - Main dialog implementation (largest file)
- [ ] `src/ODINM/OdinM.cpp` - Application entry point (simple)

#### 2. Create Resources
- [ ] `src/ODINM/OdinM.rc` - Resource script with dialogs
  - Use Visual Studio Resource Editor OR
  - Copy template from implementation guide
- [ ] `src/ODINM/res/OdinM.ico` - Application icon
  - Copy from `src/ODIN/res/ODIN.ico` or create new one

#### 3. Create Visual Studio Project
- [ ] Open `ODIN.sln` in Visual Studio
- [ ] Right-click solution → Add → New Project
- [ ] Choose "Windows Desktop Application" or "Empty Project"
- [ ] Name it "OdinM"
- [ ] Delete any auto-generated files
- [ ] Add all files from `src/ODINM/` to project
- [ ] Configure project properties (see below)

#### 4. Configure Project Properties
- [ ] C/C++ → General → Additional Include Directories:
  - `$(ProjectDir)lib\WTL10\Include`
- [ ] C/C++ → Precompiled Headers:
  - Use: `/Yu` (Use Precompiled Header)
  - File: `stdafx.h`
  - Set `stdafx.cpp` to Create `/Yc`
- [ ] Linker → System → SubSystem: `Windows (/SUBSYSTEM:WINDOWS)`
- [ ] Linker → Manifest → UAC Execution Level: `requireAdministrator`
- [ ] Linker → Input → Additional Dependencies:
  - Add: `shlwapi.lib;version.lib;crypt32.lib`

#### 5. Build and Test
- [ ] Build in Debug x64 configuration
- [ ] Fix any compilation errors
- [ ] Run and verify dialog appears
- [ ] Test basic functionality incrementally

## Time Estimate
- Creating files: 2-3 hours
- Implementing main dialog logic: 2-3 hours
- Creating resources: 1 hour
- Project setup and configuration: 1 hour
- Testing and debugging: 1-2 hours

**Total: 6-10 hours**

## Fastest Path to Working App

### Option 1: Minimal Version First
1. Create just `OdinM.cpp` with basic dialog
2. Get it to compile and show a window
3. Then add features incrementally

### Option 2: Copy All Code from Guide
1. Open `ODINM_IMPLEMENTATION_GUIDE.md`
2. Copy all code samples for each file
3. Create files and paste code
4. Build and test

## Key Code Locations in Implementation Guide

All the code you need is in `ODINM_IMPLEMENTATION_GUIDE.md`:

- **Step 1**: HashCalculator.h/cpp (complete code provided)
- **Step 2**: HashConfigDlg.h (complete code provided)
- **Step 3**: OdinM.cpp (complete code provided)
- **Step 4**: OdinM.rc (template provided)

For `OdinMDlg.cpp` (not provided), you can:
1. Start with minimal implementation that just shows dialog
2. Add drive detection (copy from ODIN ODINDlg.cpp)
3. Add ODINC spawning logic
4. Add hash verification
5. Add all UI features

## Reusable Code from ODIN

You can copy/adapt these from existing ODIN code:

### Drive Detection
From `src/ODIN/ODINDlg.cpp`:
- `RefreshDriveList()` - Scan for drives
- `FillDriveList()` - Populate list view
- `OnDeviceChanged()` - Device insertion handling

### Configuration
From `src/ODIN/Config.h`:
- `DECLARE_SECTION()` / `DECLARE_ENTRY()` macros
- INI file persistence

### Process Spawning
Pattern from `src/ODIN/OdinManager.cpp`:
- CreateProcess() examples
- Handle management
- Process monitoring

## Testing Without Real Drives

For initial development:
1. Use image files instead of real drives
2. Test hash calculation on partitions in image files
3. Mock the drive detection UI
4. Test ODINC spawning with dummy commands

## Troubleshooting

### Common Issues

**"Cannot find WTL headers"**
- Check include path: `$(ProjectDir)lib\WTL10\Include`
- Verify WTL is in `lib/WTL10/Include/`

**"Undefined CryptAcquireContext"**
- Add `crypt32.lib` to linker dependencies
- Add `#pragma comment(lib, "crypt32.lib")` to stdafx.h

**"Cannot open precompiled header file"**
- Set `stdafx.cpp` → C/C++ → Precompiled Headers → Create `/Yc`
- Set all other .cpp files → Use `/Yu`

**"Administrator rights required"**
- Set UAC execution level to requireAdministrator in manifest
- Run Visual Studio as administrator during development

## Next Steps After Building

Once you have a working build:

1. **Test drive detection**
   - Insert USB drive
   - Verify it appears in list view

2. **Test ODINC spawning**
   - Select image file
   - Click clone
   - Verify ODINC.exe starts

3. **Test hash calculation**
   - Configure SHA hashes
   - Run verification
   - Check results

4. **Add remaining features**
   - Export to CSV
   - Concurrent process management
   - Progress tracking
   - Error handling

## Files Summary

**What exists (6 files):**
```
src/ODINM/
├── stdafx.h ✅
├── stdafx.cpp ✅
├── resource.h ✅
├── DriveSlot.h ✅
├── DriveSlot.cpp ✅
├── OdinMDlg.h ✅
└── README.md ✅
```

**What you need to create (7 files + project):**
```
src/ODINM/
├── OdinMDlg.cpp ❌ (main work)
├── HashCalculator.h ❌ (copy from guide)
├── HashCalculator.cpp ❌ (copy from guide)
├── HashConfigDlg.h ❌ (copy from guide)
├── HashConfigDlg.cpp ❌ (implement)
├── OdinM.cpp ❌ (copy from guide)
├── OdinM.rc ❌ (create with VS or copy template)
└── res/
    └── OdinM.ico ❌ (copy from ODIN)

Plus:
- OdinM.vcxproj ❌ (create with VS)
- Update ODIN.sln ❌ (add OdinM project)
```

## Remember

- 📖 All code samples are in `ODINM_IMPLEMENTATION_GUIDE.md`
- 📋 Full specification is in `ODINM_SPECIFICATION.md`
- 🎯 Focus on getting it to compile first, features second
- ✅ Test incrementally
- 🔄 Commit to git after each working phase

**Good luck! You've got this! 🚀**
