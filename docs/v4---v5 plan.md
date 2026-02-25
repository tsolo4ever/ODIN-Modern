# ODIN v0.4.0 → v0.5.0 Development Plan

```markdown
# ODIN Development Plan
**Created:** 2026-02-21  
**Updated:** 2026-02-24  
**Repository:** odin-win-code-r71-trunk  

---

## 🏆 v0.4.0 - Modernization Release
**Status:** 🔄 In Progress  
**Branch:** modernization  

### ✅ Completed

#### Performance
- [x] **Memory leak fix** `f7d809b`
      - `unique_ptr` for thread handle array
      - 15 min → 5 min flash speed (3x)
- [x] **CRC32 slice-by-8** `e94b31f`
      - Per-byte → slice-by-8 algorithm
      - 5-8x faster hash verification
- [x] **UI overhead reduction**
      - 74% → 6% CPU in UI updates
      - Profiler verified

#### Stability  
- [x] **Buffer queue race condition** `2ce5612`
      - Proper wait state handling
      - 16 min timeout → 5 min
      - Null chunk guard added
- [x] **Integer overflow protection** `a6ae812`
      - 64→32 bit cast guards
      - Prevents data corruption on large disks
- [x] **Exception handling in threads** `179053a`
      - Added std::exception catch
      - Added catch(...) fallback
      - All 4 thread types updated
- [x] **Boot sector validation** `c2f0db9`
      - SectorsPerCluster validation
      - BytesPerSector validation  
      - Boot signature (0xAA55) check
      - Power-of-2 validation
- [x] **Reset() memset bug**
      - memset was corrupting wstring members
      - Replaced with manual member reset
      - Fixed garbage filename in -output

#### Console/CLI
- [x] **PowerShell output fix** `10641da`
      - Handle inheritance for stdout/stderr
      - ODINC piping works correctly
- [x] **InitConsole gate removed**
      - ParseAndProcess always called
      - AllocConsole fallback added
      - ios::sync_with_stdio(true) fixed
      - wcout.flush()/clear() added

#### Build System
- [x] **VS2026 migration**
      - Platform toolset v143
      - Windows SDK 10.0
      - C++17 standard
- [x] **ATL 3.0 dependency removed**
- [x] **Common Controls v6 manifest**
- [x] **PerMonitorV2 DPI awareness**

---

### 🔄 In Progress

#### Compression
- [X] **Retire bzip2**
      - Remove from compression options
      - Keep decompress for old files
      - Update UI and CLI options
      - Files: `Compression.h/cpp`

- [X] **Add zstd** 
      - Headers added: `zstd.h`, `zdict.h`, `zstd_errors.h`
      - Static lib: `lib\x64\libzstd_static.lib`
      - [ ] Add to VS project settings
      - [ ] Add to compression enum
      - [ ] Implement compress/decompress
      - [ ] Add to CLI `-compression=zstd`
      - [ ] Add to UI dropdown
      - [ ] Test with CF card image
      - Files: `Compression.h/cpp`, `OdinManager.cpp`

- [X] **Add LZ4**
      - [ ] Download release package
      - [ ] Add headers to project
      - [ ] Add static lib x64/x86
      - [ ] Add to compression enum
      - [ ] Implement compress/decompress
      - [ ] Add LZ4HC variant
      - [ ] Add to CLI `-compression=lz4`
      - [ ] Add to UI dropdown
      - [ ] Test with CF card image
      - Files: `Compression.h/cpp`, `OdinManager.cpp`

#### UI Restructure - ODIN Main Dialog
- [ ] **Add menu bar**
      - [ ] Create `IDR_MAINMENU` in ODIN.rc
      - [ ] File menu
            - Browse Image...
            - Recent Images (submenu)
            - Exit
      - [ ] Operation menu
            - Backup (radio)
            - Restore (radio)
            - ─────────
            - Verify Image...
      - [ ] Settings menu
            - Auto-Flash (submenu)
            - Compression (submenu)
            - Backup Mode (submenu)
            - ─────────
            - Options...
      - [ ] Help menu
            - About...
            - Documentation

- [ ] **Remove buttons to menu**
      - [ ] Verify → Operation menu
      - [ ] Options → Settings menu
      - [ ] About → Help menu
      - [ ] Auto-Flash settings → Settings menu

- [ ] **Keep on dialog**
      - Backup/Restore radio buttons
      - Drive list
      - Image file path + browse
      - Progress area (cleaned up)
      - Status text
      - [Start] button
      - [Close] button

- [ ] **Clean up progress area**
      - [ ] Remove 6 separate text boxes
      - [ ] Add single progress bar
      - [ ] Add single status line:
            `Processed: 4.2GB/6.3GB  Speed: 82MB/s  ETA: 0:25`

#### UI Restructure - OdinM Dialog  
- [ ] **Add menu bar**
      - [ ] File menu
            - Browse Image...
            - Export Results
            - Exit
      - [ ] Settings menu
            - Auto-clone on insertion (checkable)
            - Verify hash after clone (checkable)
            - Stop all on failure (checkable)
            - ─────────
            - Configure Hashes...
            - Max Concurrent (submenu 1-5)
            - Compression (submenu)
      - [ ] Help menu
            - About...

- [ ] **Remove from dialog**
      - [ ] Settings group box
      - [ ] All checkboxes
      - [ ] Configure Hashes button
      - [ ] Max concurrent dropdown
      - [ ] Clear Log button → File menu
      - [ ] Export Results button → File menu

- [ ] **Keep on dialog**
      - Source image path + browse
      - Drive slot grid
      - Activity log
      - [Start All] button
      - [Stop All] button
      - [Close] button
      - Status bar (Active/Complete/Failed)

#### VSS Snapshot (Deferred)
- [x] **Grey out in UI**
      - Backup Mode → VSS option disabled
      - Error: IOCTL_DISK_GET_PARTITION_INFO_EX
        fails on shadow copy volumes
- [x] **CLI fallback**
      - `-makeSnapshot` warns and falls back
      - to `-usedBlocks` mode
- [ ] **Target: v0.5.0**

---

### 📋 Todo

#### Code Quality
- [x] **Smart pointers migration**
      - OdinManager raw pointers → unique_ptr
      - CommandLineProcessor raw pointers
      - Other files as found
      - Files: `OdinManager.h/cpp`

- [x] **Replace malloc/free**
      - `ImageStream.cpp::StoreVolumeBitmap()`
      - Use vector or unique_ptr instead

- [ ] **Remove dead code**
      - Commented out sections
      - XP compatibility code
        - `runsOnWinXP` flag
        - `IVssBackupComponentsXP`
        - All XP branches

- [ ] **Magic numbers → constants**
      - Buffer counts
      - Chunk sizes
      - Timeout values

#### Library Updates
- [x] **zlib 1.2.3 → 1.3.1**
      - Download from zlib.net
      - Replace old version
      - Remove assembly workarounds
      - Test compression

- [x] **Remove bzip2 project**
      - After bzip2 compress removed
      - Keep decompress inline
      - Remove libz2 VS project

#### Testing
- [ ] **Run existing test suite**
      - Fix any broken tests
      - Document coverage

- [ ] **Add new tests**
      - Buffer queue thread safety
      - Integer overflow paths
      - Exception handling paths
      - Console output paths

- [ ] **Integration tests**
      - Backup → Restore cycle
      - All compression modes
      - Split file handling
      - Verify operation

- [ ] **Memory profiler**
      - Multiple flash operations
      - Verify flat memory profile
      - ✓ Already verified for single ops

#### Documentation
- [ ] **Update README.md**
      - New compression options
      - Build instructions for VS2026
      - Usage examples

- [ ] **Update PrintUsage()**
      - New compression options
      - Mark VSS as unavailable
      - Add examples for new features

---

### 🚀 Release Tasks
- [ ] Version bump → 0.4.0
- [ ] Update all version strings
- [ ] Clean build Release x64
- [ ] Test release binaries
- [ ] Create release notes
- [ ] Git tag v0.4.0
      ```bash
      git tag v0.4.0
      git push origin v0.4.0
      ```

---

## 🔮 v0.5.0 - Feature Release
**Status:** Planning  
**Target:** After v0.4.0 stable  

### Planned Features

#### VSS Snapshot Fix
- [ ] **Diagnose IOCTL failure**
      - Check IsVSSVolume() detection
      - Skip partition query for snapshots
      - Files: `ImageStream.cpp`, 
               `PartitionInfoMgr.cpp`
- [ ] **Fix and re-enable in UI**
- [ ] **Test with live system volume**

#### OdinM Auto-Flash
- [ ] **WM_DEVICECHANGE handler**
      - Detect card insertion
      - Check size match (±10%)
      - Check label if configured
      - Start flash automatically

- [ ] **CF Card size matching**
      ```
      Known sizes with tolerances:
      4GB:  3600-4200 MB
      8GB:  7200-8400 MB
      16GB: 14400-16800 MB
      32GB: 28800-33600 MB
      ```

- [ ] **Completion notifications**
      - Green row = pass
      - Red row = fail
      - Beep on completion
      - Different tone for fail

- [ ] **Auto-eject option**
      - Eject card when done
      - Ready for next card

#### Performance
- [ ] **Named pipe for ODINC→ODIN**
      - Fix elevated process output
      - Proper PowerShell piping
      - Non-elevated PS support

- [ ] **Thread pool**
      - Reuse threads between operations
      - Faster start for sequential flashes

#### Drive Info Fix
- [X] **Size showing 0.000B**
      - Investigate GetBytes() for physical disks
      - Fix or document as intentional
- [X] **Type showing Unknown**  
      - GetDriveType() returning unknown
      - Fix or document as intentional

#### Win32 Build Decision
- [ ] **Evaluate if Win32 still needed**
      - Check user base
      - Drop if no 32-bit users
      - Simplify build configuration

---

## 🔧 Architecture Notes

### Threading Model
```
[Read Thread] → [Buffer Queue] → [Compression Thread] 
                                        ↓
                              [Buffer Queue] → [Write Thread]
```
- Fixed buffer pool = fixed memory regardless of image size
- Verified: 2GB backup uses only ~18MB RAM
- Producer-consumer pattern working correctly

### OdinM Architecture  
```
OdinM UI (WTL Dialog)
└── Spawns OdinC.exe instances
    └── Each instance → ODIN.exe
        └── One per card slot
        └── Independent operation
        └── 2 slots = 2 independent buses
            ├── Built-in slot → direct PCIe
            └── USB-C → dedicated controller
```

### Compression Pipeline (v0.4.0)
```
Current:          Planned v0.4.0:      Planned v0.5.0:
─────────────────────────────────────────────────────
gzip    ✓    →   gzip     ✓      →    gzip     ✓
bzip2   ✓    →   bzip2    read   →    bzip2    read
             →   lz4      ✓      →    lz4      ✓
             →   lz4hc    ✓      →    lz4hc    ✓
             →   zstd     ✓      →    zstd     ✓
```

### Known Issues
```
VSS Snapshot:
  IOCTL_DISK_GET_PARTITION_INFO_EX fails
  on shadow copy volumes
  → Greyed out until v0.5.0

Drive Size/Type:
  Physical disks show 0.000B / Unknown
  May be intentional safety measure
  → Investigate in v0.5.0

ODINC Console Output:
  Elevated process loses pipe to PS
  Named pipe solution planned
  → v0.5.0
```

---

## 📊 Performance Benchmarks
*Test system: Laptop with built-in + USB-C CF readers*

| Metric | Before | After | Improvement |
|--------|--------|-------|-------------|
| Flash time | 15 min | 5 min | 3x faster |
| CRC32 speed | baseline | 5-8x | slice-by-8 |
| UI CPU usage | 74% | 6% | 12x less |
| Peak RAM (2GB backup) | growing | ~18MB flat | leak fixed |

---

## 🆘 Rollback Plan
```bash
# Return to baseline
git checkout v0.3-legacy-baseline

# Return to specific commit  
git checkout <commit-hash>

# New branch if needed
git checkout -b fix-issue
```

---

## 📁 Key Files Reference
```
src/ODIN/
├── ODIN.cpp                  ← WinMain entry point
├── ODINDlg.h/cpp             ← Main dialog
├── OdinManager.h/cpp         ← Core engine
├── CommandLineProcessor.h/cpp ← CLI handling
├── BufferQueue.cpp           ← Thread buffers
├── ReadThread.cpp            ← Read pipeline
├── WriteThread.cpp           ← Write pipeline
├── ImageStream.cpp           ← Disk/file I/O
├── PartitionInfoMgr.h/cpp    ← Partition tables
├── Compression.h/cpp         ← Compression layer
├── DriveList.h/cpp           ← Drive enumeration
└── DriveInfo.h/cpp           ← Drive information

src/ODINC/
└── ODINC.cpp                 ← Console launcher

OdinM/                        ← New addon
└── OdinMDlg.h/cpp            ← Multi-drive UI
```

---

*Update this document as work progresses*
*See also: CODE_REVIEW.md, ARCHITECTURE.md*
```