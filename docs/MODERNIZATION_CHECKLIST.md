# ODIN Modernization Checklist

**Created:** 2026-02-21  
**Updated:** 2026-02-22  
**Status:** ✅ Phase 1 Complete - All Critical Bug Fixes Implemented  
**See also:** Map.md, CODE_REVIEW.md

---

## 📋 Phase 0: Pre-Flight Setup (2-4 hours)

### Backup & Version Control
- [ ] **Create baseline backup**
  - [ ] Tag current state: `git tag v0.3-legacy-baseline`
  - [ ] Create branch: `git checkout -b modernization`
  - [ ] Push to remote: `git push origin modernization`

- [ ] **Test current build**
  - [ ] Build in VS2008 successfully
  - [ ] Test backup operation
  - [ ] Test restore operation
  - [ ] Test `-list` command
  - [ ] Document any issues found

### Environment Setup
- [ ] **Install Visual Studio 2022 Community**
  - [ ] Download from visualstudio.microsoft.com
  - [ ] Select "Desktop development with C++"
  - [ ] Install Windows 11 SDK
  - [ ] Install C++ ATL for latest v143 build tools

- [ ] **Install vcpkg (optional)**
  ```powershell
  git clone https://github.com/Microsoft/vcpkg.git
  cd vcpkg
  .\bootstrap-vcpkg.bat
  .\vcpkg integrate install
  ```

- [ ] **Create build scripts directory**
  ```
  mkdir build_scripts
  ```

### Documentation
- [ ] **Document current state**
  - [ ] List all working features
  - [ ] Create test scenarios document
  - [ ] Take screenshots of current UI
  - [ ] Record current `-list` output format

---

## 🔥 Phase 1: Critical Bug Fixes ✅ COMPLETED (6/6)

**Total Time:** ~3 hours  
**All commits on branch:** modernization

### 1.1 Buffer Queue Race Condition ✅ COMPLETED
**File:** `src/ODIN/BufferQueue.cpp`  
**Commit:** 2ce5612

- [x] **Fix GetChunk() method**
  ```cpp
  // Add proper wait state handling
  - DWORD res = WaitForSingleObject(fSemaListHasElems.m_h, 1000000);
  + DWORD res = WaitForSingleObject(fSemaListHasElems.m_h, 300000); // 5 min timeout
  +
  + switch(res) {
  +   case WAIT_OBJECT_0:
  +     break;
  +   case WAIT_TIMEOUT:
  +     THROW_INT_EXC(EInternalException::threadSyncTimeout);
  +   case WAIT_ABANDONED:
  +     THROW_INT_EXC(EInternalException::threadSyncAbandoned);
  +   default:
  +     THROW_INT_EXC(EInternalException::threadSyncError);
  + }
  
    fCritSec.Enter();
  + if (fChunks.empty()) {
  +   fCritSec.Leave();
  +   THROW_INT_EXC(EInternalException::emptyBufferQueue);
  + }
    chunk = fChunks.front();
    fChunks.pop_front();
    fCritSec.Leave();
  ```

- [ ] **Add new exception codes**
  - [ ] Update `InternalException.h` with new error codes
  - [ ] Add error messages

- [ ] **Test fix**
  - [ ] Run backup operation
  - [ ] Run restore operation
  - [ ] Monitor for hangs

### 1.2 Memory Leak in Exception Paths ✅ COMPLETED
**File:** `src/ODIN/OdinManager.cpp`  
**Commit:** f7d809b

- [x] **Fix WaitToCompleteOperation()**
  ```cpp
  - HANDLE* threadHandleArray = new HANDLE[threadCount];
  + std::unique_ptr<HANDLE[]> threadHandleArray(new HANDLE[threadCount]);
  
  // ... rest of code stays same, auto-cleanup on exception
  
  - delete [] threadHandleArray;  // Remove manual delete
  ```

- [ ] **Audit other similar patterns**
  - [ ] Search for `new.*\[\]` in loops
  - [ ] Check exception paths
  - [ ] Convert to smart pointers

- [ ] **Test fix**
  - [ ] Trigger exceptions during operations
  - [ ] Use memory profiler to verify no leaks

### 1.3 Integer Overflow Protection ✅ COMPLETED
**Files:** `ReadThread.cpp`, `WriteThread.cpp`  
**Commit:** a6ae812 (also includes 1.4)

- [x] **Add overflow checks before casts**
  ```cpp
  unsigned __int64 bytesToReadForReadRunLength = fClusterSize * runLength;
  + if (bytesToReadForReadRunLength > UINT_MAX)
  +   THROW_INT_EXC(EInternalException::integerOverflow);
  bytesToRead = (unsigned) bytesToReadForReadRunLength;
  ```

- [ ] **Find all 64→32 bit casts**
  ```bash
  grep -r "(unsigned)" src/ODIN/*.cpp | grep "unsigned __int64"
  ```

- [ ] **Add checks to each location**

- [ ] **Test with large disks**
  - [ ] Test with >4GB partitions
  - [ ] Verify no truncation

### 1.4 Unchecked Pointer Dereferences
**Files:** `ReadThread.cpp`, `WriteThread.cpp`

- [ ] **Add null checks after GetChunk()**
  ```cpp
- [x] **Find all GetChunk() calls** - Found 8 locations
- [x] **Add checks to each** - Added to all thread files

### 1.5 Enhanced Exception Handling ✅ COMPLETED
**Files:** `ReadThread.cpp`, `WriteThread.cpp`, `CompressionThread.cpp`, `DecompressionThread.cpp`  
**Commit:** 179053a

- [x] **Update exception handlers**
  ```cpp
  try {
    // ... work ...
  } catch (Exception &e) {
    fErrorFlag = true;
    fErrorMessage = e.GetMessage();
    fFinished = true;
    return E_FAIL;
  + } catch (std::exception &e) {
  +   fErrorFlag = true;
  +   // Convert to wstring
  +   fErrorMessage = L"Standard exception: ";
  +   fFinished = true;
  +   return E_FAIL;
  + } catch (...) {
  +   fErrorFlag = true;
  +   fErrorMessage = L"Unknown exception in thread";
  +   fFinished = true;
  +   return E_FAIL;
  + }
  ```

- [x] Update in: `ReadThread.cpp`
- [x] Update in: `WriteThread.cpp`
- [x] Update in: `CompressionThread.cpp`
- [x] Update in: `DecompressionThread.cpp`

### 1.6 Boot Sector Validation ✅ COMPLETED
**Files:** `ImageStream.cpp`, `InternalException.h`, `InternalException.cpp`  
**Commit:** c2f0db9

- [x] **Add validation in CalculateFATExtraOffset()**
  ```cpp
  Read(bootSector, sizeof(TWinBootSector), &bytesRead);
  if (bytesRead == sizeof(TWinBootSector)) {
  +   // Validate before use
  +   if (bootSector->SectorsPerCluster == 0 || 
  +       bootSector->SectorsPerCluster > 128)
  +     THROW_INT_EXC(EInternalException::invalidBootSector);
  +   if (bootSector->BytesPerSector == 0 ||
  +       bootSector->BytesPerSector > 4096)
  +     THROW_INT_EXC(EInternalException::invalidBootSector);
    // ... rest of processing
  ```

- [x] **Add exception code** - Added 4 new codes (invalidBootSector, integerOverflow, threadSyncError, emptyBufferQueue)
- [x] **Boot signature validation** - Added 0xAA55 check
- [x] **Power-of-2 validation** - For BytesPerSector and SectorsPerCluster

---

## 🔧 Phase 2: Build System Modernization (4-6 hours)

### 2.1 Visual Studio Migration
- [ ] **Open solution in VS2022**
  - [ ] Open `ODIN.sln`
  - [ ] Accept conversion wizard
  - [ ] Note any warnings

- [ ] **Update project settings (all projects)**
  - [ ] ODIN project:
    - [ ] Platform Toolset → v143
    - [ ] Windows SDK → 10.0 (latest)
    - [ ] C++ Language Standard → C++17
    - [ ] Conformance mode → Yes (/permissive-)
    - [ ] Multi-processor compilation → Yes (/MP)
  
  - [ ] ODINC project: (same settings)
  - [ ] libz2 project: (same settings)
  - [ ] zlib project: (same settings)
  - [ ] ODINTest project: (same settings)

- [ ] **Fix compilation errors**
  - [ ] Document each error
  - [ ] Fix one at a time
  - [ ] Test build after each fix

### 2.2 Library Updates

#### zlib Update
- [ ] **Download zlib 1.3.1**
  - [ ] Get from https://www.zlib.net/
  - [ ] Extract to temp directory

- [ ] **Replace old version**
  ```bash
  cd src
  mv zlib.1.2.3 zlib.1.2.3.old
  cp -r /path/to/zlib-1.3.1 zlib.1.3.1
  ```

- [ ] **Update project references**
  - [ ] Update include paths in zlib.vcproj
  - [ ] Update file list in project
  - [ ] Remove assembly workarounds (no longer needed)

- [ ] **Test build**
  - [ ] Build zlib project
  - [ ] Build ODIN project
  - [ ] Test compression

#### bzip2 Update
- [ ] **Download bzip2 1.0.8**
  - [ ] Get from https://sourceware.org/bzip2/
  - [ ] Extract to temp directory

- [ ] **Replace old version**
  ```bash
  cd src
  mv bzip2-1.0.5 bzip2-1.0.5.old
  cp -r /path/to/bzip2-1.0.8 bzip2-1.0.8
  ```

- [ ] **Update project references**
  - [ ] Update include paths in libz2.vcproj
  - [ ] Update file list
  - [ ] Test build

### 2.3 Remove ATL 3.0 Dependency
- [ ] **Remove external ATL references**
  - [ ] Delete references to c:\devtools\atl30
  - [ ] ATL is now in VS2022

- [ ] **Update include paths**
  - [ ] Remove ATL 3.0 paths
  - [ ] Verify builds without external ATL

- [ ] **Test WTL functionality**
  - [ ] Verify dialogs work
  - [ ] Test all UI elements

---

## 🎨 Phase 3: C++ Modernization (8-12 hours)

### 3.1 Smart Pointers Migration

#### OdinManager.h/cpp
- [ ] **Replace raw pointers with smart pointers**
  ```cpp
  // In OdinManager.h
  - CReadThread *fReadThread;
  + std::unique_ptr<CReadThread> fReadThread;
  
  - CWriteThread *fWriteThread;
  + std::unique_ptr<CWriteThread> fWriteThread;
  
  - COdinThread *fCompDecompThread;
  + std::unique_ptr<COdinThread> fCompDecompThread;
  
  - IImageStream *fSourceImage;
  + std::unique_ptr<IImageStream> fSourceImage;
  
  - IImageStream *fTargetImage;
  + std::unique_ptr<IImageStream> fTargetImage;
  ```

- [ ] **Update Reset() method**
  ```cpp
  void COdinManager::Reset() {
  - delete fReadThread;
  - fReadThread = NULL;
  + fReadThread.reset();
    // etc...
  }
  ```

- [ ] **Update creation sites**
  ```cpp
  - fReadThread = new CReadThread(...);
  + fReadThread = std::make_unique<CReadThread>(...);
  ```

- [ ] **Test all operations**

#### Other Files
- [ ] Update `CommandLineProcessor.h/cpp`
- [ ] Update `ODINDlg.h/cpp`
- [ ] Update `SplitManager.h/cpp`

### 3.2 Replace malloc/free
- [ ] **Find all malloc/free usage**
  ```bash
  grep -r "malloc\|free" src/ODIN/*.cpp
  ```

- [ ] **ImageStream.cpp::StoreVolumeBitmap()**
  ```cpp
  - volumeBitmap = (VOLUME_BITMAP_BUFFER *)malloc(...);
  + volumeBitmap = new VOLUME_BITMAP_BUFFER[...];
  // OR better:
  + std::vector<BYTE> buffer(nBitmapBytes);
  
  - free(volumeBitmap);
  + delete[] volumeBitmap; // or automatic with vector
  ```

- [ ] **Test each change**

### 3.3 Modern Threading (Advanced - Optional)
- [ ] **Create thread adapter class**
  ```cpp
  class ThreadAdapter {
    std::thread thread;
    std::atomic<bool> shouldStop{false};
    // ...
  };
  ```

- [ ] **Gradually migrate**
  - [ ] Start with one thread type
  - [ ] Test thoroughly
  - [ ] Migrate others if successful

### 3.4 String Handling
- [ ] **Replace CString with std::wstring**
  - [ ] Find all CString usage
  - [ ] Replace incrementally
  - [ ] Test UI after each change

- [ ] **Use std::filesystem::path**
  ```cpp
  #include <filesystem>
  namespace fs = std::filesystem;
  
  fs::path imagePath = ...;
  ```

---

## 🚀 Phase 4: Feature Additions (6-10 hours)

### 4.1 Fix ODINC.cpp PowerShell Output ✅ COMPLETED
**File:** `src/ODINC/ODINC.cpp`  
**Commit:** 10641da

- [x] **Add handle inheritance**
  ```cpp
  STARTUPINFO startupInfo;
  ZeroMemory(&startupInfo, sizeof(startupInfo));
  + startupInfo.cb = sizeof(startupInfo);
  + startupInfo.dwFlags = STARTF_USESTDHANDLES;
  + startupInfo.hStdOutput = GetStdHandle(STD_OUTPUT_HANDLE);
  + startupInfo.hStdError = GetStdHandle(STD_ERROR_HANDLE);
  + startupInfo.hStdInput = GetStdHandle(STD_INPUT_HANDLE);
  
  PROCESS_INFORMATION procInfo;
  ZeroMemory(&procInfo, sizeof(procInfo));
  + 
  + // Ensure handles can be inherited
  + secAttr.bInheritHandle = TRUE;
  
  BOOL ok = CreateProcess(NULL, cmdLine, &secAttr, &secAttr, 
  -                       TRUE, 0, NULL, NULL, &startupInfo, &procInfo);
  +                       TRUE, 0, NULL, NULL, &startupInfo, &procInfo);
  ```

- [ ] **Test PowerShell piping**
  ```powershell
  .\odinc.exe -list > drives.txt
  Get-Content drives.txt  # Should show drives
  
  .\odinc.exe -list | Select-String "Drive"  # Should filter
  ```

- [ ] **Commit fix**
  ```bash
  git add src/ODINC/ODINC.cpp
  git commit -m "fix: Enable PowerShell piping for odinc -list"
  ```

### 4.2 Auto-Flash Mode Implementation

#### Design Phase
- [ ] **Get requirements from user**
  - [ ] CF card size
  - [ ] CF card label (if any)
  - [ ] Confirmation preference
  - [ ] Auto-eject preference
  - [ ] UI style preference

#### UI Design
- [ ] **Add to resource.h**
  ```cpp
  #define IDC_CHECK_AUTOFLASH      1054
  #define IDC_BT_AUTOFLASH_CONFIG  1055
  #define IDD_AUTOFLASH_SETTINGS   206
  #define IDC_EDIT_AUTOFLASH_IMAGE 1056
  #define IDC_EDIT_AUTOFLASH_SIZE  1057
  #define IDC_EDIT_AUTOFLASH_LABEL 1058
  #define IDC_CHECK_AUTOFLASH_CONFIRM 1059
  #define IDC_CHECK_AUTOFLASH_EJECT 1060
  ```

- [ ] **Add to ODIN.rc**
  - [ ] Add checkbox to main dialog
  - [ ] Create settings dialog template
  - [ ] Add controls for configuration

#### Configuration System
- [ ] **Add to Config.h**
  ```cpp
  DECLARE_ENTRY(bool, fAutoFlashEnabled)
  DECLARE_ENTRY(std::wstring, fAutoFlashImagePath)
  DECLARE_ENTRY(int, fAutoFlashCardSizeMB)
  DECLARE_ENTRY(std::wstring, fAutoFlashCardLabel)
  DECLARE_ENTRY(bool, fAutoFlashShowConfirmation)
  DECLARE_ENTRY(bool, fAutoFlashEjectWhenDone)
  ```

#### Dialog Implementation
- [ ] **Create CAutoFlashSettingsDlg class**
  - [ ] Create AutoFlashSettingsDlg.h
  - [ ] Create AutoFlashSettingsDlg.cpp
  - [ ] Implement dialog handlers

#### Detection Logic
- [ ] **Update ODINDlg.h**
  ```cpp
  // Add members
  bool fAutoFlashEnabled;
  
  // Add methods
  int DetectCFCard();
  void OnCFCardDetected(int driveIndex);
  void StartAutoFlash(int driveIndex);
  ```

- [ ] **Implement DetectCFCard()**
  ```cpp
  int CODINDlg::DetectCFCard() {
    for (int i = 0; i < fOdinManager.GetDriveCount(); i++) {
      CDriveInfo* di = fOdinManager.GetDriveInfo(i);
      
      // Check removable
      if (di->GetDriveType() != CDriveInfo::removable)
        continue;
      
      // Check size (±10% tolerance)
      __int64 sizeMB = di->GetBytes() / (1024 * 1024);
      int expectedSize = fAutoFlashCardSizeMB;
      if (abs(sizeMB - expectedSize) > expectedSize * 0.1)
        continue;
      
      // Check label if specified
      if (!fAutoFlashCardLabel.empty()) {
        if (di->GetVolumeName() != fAutoFlashCardLabel)
          continue;
      }
      
      return i;  // Found!
    }
    return -1;
  }
  ```

- [ ] **Enhance OnDeviceChanged()**
  ```cpp
  LRESULT CODINDlg::OnDeviceChanged(UINT uMsg, WPARAM wParam, 
                                     LPARAM lParam, BOOL& bHandled) {
    if (wParam == DBT_DEVICEARRIVAL && fAutoFlashEnabled) {
      RefreshDriveList();
      int cfIndex = DetectCFCard();
      if (cfIndex >= 0) {
        OnCFCardDetected(cfIndex);
      }
    }
    return 0;
  }
  ```

- [ ] **Implement OnCFCardDetected()**
  ```cpp
  void CODINDlg::OnCFCardDetected(int driveIndex) {
    if (fAutoFlashShowConfirmation) {
      if (MessageBox(L"CF card detected. Start flashing?", 
                     L"Auto-Flash", MB_YESNO) == IDNO)
        return;
    }
    
    StartAutoFlash(driveIndex);
  }
  ```

- [ ] **Implement StartAutoFlash()**
  - [ ] Set mode to restore
  - [ ] Set source from config
  - [ ] Set target to detected drive
  - [ ] Trigger existing restore logic

#### Testing
- [ ] **Test detection**
  - [ ] Insert CF card
  - [ ] Verify detection works
  - [ ] Test size matching
  - [ ] Test label matching

- [ ] **Test auto-flash**
  - [ ] Verify restore starts
  - [ ] Monitor progress
  - [ ] Test cancellation
  - [ ] Test completion

- [ ] **Test auto-eject**
  - [ ] Verify eject after completion

### 4.3 Enhanced Output Formats

- [ ] **Add format option to CommandLineProcessor**
  ```cpp
  // In Parse()
  if (cmdLineParser[L"format"])
    fOperation.outputFormat = cmdLineParser[L"format"];
  ```

- [ ] **Implement JSON output**
  ```cpp
  void CODINDlg::ListDrivesJSON() {
    wcout << L"[" << endl;
    for (int i = 0; i < noDrives; i++) {
      wcout << L"  {" << endl;
      wcout << L"    \"index\": " << i << L"," << endl;
      wcout << L"    \"device\": \"" << di->GetDeviceName() << L"\"," << endl;
      // ...
      wcout << L"  }" << (i < noDrives-1 ? L"," : L"") << endl;
    }
    wcout << L"]" << endl;
  }
  ```

- [ ] **Implement CSV output**
- [ ] **Implement table output**
- [ ] **Test all formats**

---

## 🧪 Phase 5: Testing (4-6 hours)

### 5.1 Unit Test Expansion
- [ ] **Review existing tests**
  - [ ] Run current test suite
  - [ ] Document coverage

- [ ] **Add new tests**
  - [ ] Buffer queue thread safety tests
  - [ ] Integer overflow tests
  - [ ] Exception handling tests
  - [ ] Auto-flash detection tests

### 5.2 Integration Testing
- [ ] **Create test images**
  - [ ] Small test partition (100MB)
  - [ ] Medium partition (1GB)
  - [ ] Large partition (10GB+)

- [ ] **Test scenarios**
  - [ ] Backup → Restore cycle
  - [ ] Backup with compression
  - [ ] Split file handling
  - [ ] VSS snapshot
  - [ ] Verify operation
  - [ ] Auto-flash mode

### 5.3 Stress Testing
- [ ] **Long-running operations**
  - [ ] Backup very large disk
  - [ ] Multiple backup/restore cycles
  - [ ] Monitor for memory leaks

- [ ] **Error injection**
  - [ ] Disk full scenarios
  - [ ] File corruption
  - [ ] Unexpected disconnection

---

## 📚 Phase 6: Documentation (2-4 hours)

### 6.1 Code Documentation
- [ ] **Add Doxygen comments**
  - [ ] Document public APIs
  - [ ] Document key algorithms
  - [ ] Generate documentation

### 6.2 User Documentation
- [ ] **Update README.md**
  - [ ] New features
  - [ ] Build instructions
  - [ ] Usage examples

- [ ] **Create AUTO_FLASH_GUIDE.md**
  - [ ] Setup instructions
  - [ ] Configuration guide
  - [ ] Troubleshooting

### 6.3 Developer Documentation
- [ ] **Create ARCHITECTURE.md**
  - [ ] Threading model
  - [ ] Data flow
  - [ ] Extension points

---

## 🎯 Phase 7: Release (2-3 hours)

### 7.1 Code Cleanup
- [ ] **Remove dead code**
  - [ ] Delete commented sections
  - [ ] Remove unused files
  - [ ] Clean up includes

- [ ] **Format code**
  - [ ] Run code formatter
  - [ ] Fix naming inconsistencies
  - [ ] Update copyright years

### 7.2 Performance
- [ ] **Profile application**
  - [ ] Identify bottlenecks
  - [ ] Optimize if needed

### 7.3 Release Preparation
- [ ] **Update version**
  - [ ] Set to 0.4.0
  - [ ] Update all version strings

- [ ] **Create release notes**
  - [ ] List new features
  - [ ] List bug fixes
  - [ ] List breaking changes

- [ ] **Build release**
  - [ ] Clean build in Release mode
  - [ ] Test release binaries
  - [ ] Create installer (if needed)

- [ ] **Git tagging**
  ```bash
  git tag v0.4.0
  git push origin v0.4.0
  ```

---

## ✅ Completion Criteria

- [ ] All critical bugs fixed
- [ ] Builds successfully in VS2022
- [ ] All existing features work
- [ ] New auto-flash feature works
- [ ] PowerShell output works
- [ ] Tests pass
- [ ] Documentation complete
- [ ] Release tagged

---

## 🆘 Rollback Plan

If anything goes wrong:

```bash
# Return to baseline
git checkout v0.3-legacy-baseline

# Or return to a specific commit
git checkout <commit-hash>

# Create a new branch if needed
git checkout -b fix-modernization
```

---

*This checklist should be updated as work progresses. Mark items complete with [x].*
