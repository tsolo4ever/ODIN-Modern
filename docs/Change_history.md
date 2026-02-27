# ODIN Change History

---

## Version 0.4.1 (2026-02-27)

Patch release with OdinM_py improvements and minor C++ fixes.

### OdinM_py
- Rolling 6-sample speed window — transfer rate no longer diluted by slow startup ramp
- VSS snapshot option added to Make Image backup options (`-makeSnapshot`)
- PyInstaller spec + `build_exe.bat` for standalone `OdinM_py.exe` release build
- OdinM.ico applied to `OdinM_py.exe`

### ODIN / About Dialog
- Copyright year updated to 2008-2026
- Credits added for Claude (Anthropic) and Tsolo4ever

---

## Version 0.4.0 (2026-02-27)

First release of the modernized codebase. Migrated from VS2008/x86 to VS2026/x64,
with a new Python-based multi-drive cloning UI (OdinM_py) and numerous core fixes.

### Build System
- Migrated solution to Visual Studio 2026, v143/v145 toolset, Windows 11 SDK, C++17
- Removed legacy ATL 3.0 external dependency (`c:\devtools\atl30`)
- Updated to WTL 10.0 (`ATL::CString` throughout)
- Updated to zlib 1.3.2; removed legacy `src/zlib.1.2.3` source tree
- Added `scripts/build.bat` for Debug/Release command-line builds
- Added x64 support throughout (previously x86 only)

### Core Bug Fixes
- **BufferQueue race condition** — `GetChunk()` now uses 5-minute timeout with proper
  `WAIT_ABANDONED`/`WAIT_FAILED` handling instead of infinite wait
- **Memory leak in exception paths** — `WaitToCompleteOperation()` uses
  `std::unique_ptr<HANDLE[]>` instead of raw `new HANDLE[]`
- **Integer overflow** — 64-to-32-bit casts in `ReadThread`/`WriteThread` guarded with
  explicit checks; throws `integerOverflow` exception on violation
- **Null pointer dereferences** — All `GetChunk()` return values checked in thread files
- **Boot sector validation** — `CalculateFATExtraOffset()` validates 0xAA55 signature,
  power-of-2 `BytesPerSector`/`SectorsPerCluster`; new exception codes added
- **VSS shadow copy IOCTL** — Skip `IOCTL_DISK_GET_PARTITION_INFO_EX/INFO` for
  `HarddiskVolumeShadowCopy` devices (both IOCTLs fail on virtual shadow volumes)
- **MakeSnapshot arrays** — `pContainedVolumes`/`mountPoints` raw `new[]` to `std::vector`;
  fixed `delete`/`delete[]` mismatch UB; fixed uninitialized `mountPoints[0]`
- **Reset() wstring safety** — Prevents crash on empty error message
- **GPT drive list** — Fixed `Size:0/Type:Unknown` on all modern GPT drives

### C++ Modernization (Smart Pointers)
- `COdinManager`: 12 raw pointers to `std::unique_ptr`
  (`fReadThread`, `fWriteThread`, `fCompDecompThread`, `fSourceImage`, `fTargetImage`,
  4x buffer queues, `fSplitCallback`, `fVSS`, `fDriveList`)
- `CCommandLineProcessor`: `fOdinManager`, `fSplitCB`, `fFeedback` to `unique_ptr`
- `ODINDlg`: `new wchar_t[]`/`new CDriveInfo*[]` to `std::vector`
- `ImageStream.cpp`: `malloc`/`memset`/`free` in `StoreVolumeBitmap()` to `std::vector<BYTE>`
- Dead code removed: XP VSS path (`runsOnWinXP`, `IVssBackupComponentsXP`, `vsbackup_xp.h`)
- Magic numbers to named constants: `kBufferWaitTimeoutMs`, `kDoCopyBufferCount`

### New Features
- **LZ4, LZ4HC, ZSTD compression** — Full compress/decompress support added to ODINC
  (`-compression=lz4`, `-compression=lz4hc`, `-compression=zstd`)
- **Auto-flash mode** — Automatically restores image to any inserted removable drive
  matching configured size; configurable with one-time warning dialog
- **CRC32 slice-by-8** — 5-8x speedup over per-byte loop; eliminates CPU bottleneck
  on large images
- **DPI v2 manifest** — `PerMonitorV2` DPI awareness
- **Common Controls v6** — `comctl32.dll` v6 activation context; `LVS_EX_DOUBLEBUFFER`
  eliminates ListView flicker
- **`-output` flag** — Write drive list to file (`odinc -list -output=drives.txt`)
- **Updated icons** — Flat-design ODIN.ico with transparent background

### Console / ODINC Output Fixes
- Fixed stdout/pipe wiring so ODINC output reaches parent process when launched as
  subprocess (`FILE_TYPE_PIPE` detection in `InitConsole()`)
- Fixed wide-char encoding (`_O_U8TEXT` replaces `_O_U16TEXT`; eliminates
  `I n d e x :  0` spacing artifact)
- Added `wcout.flush()` in `ReportFeedback()` for immediate pipe delivery
- Progress reporting changed from 10% to 5% intervals
- `GetTotalBytesToProcess()` restore fix — uses `fSourceImage->GetSize()` (volume size
  from image header) instead of `fTargetImage->GetSize()` (disk opened for writing = 0)

### OdinM_py (New — Python multi-drive cloning UI)
- Multi-slot UI with per-slot progress bar, transfer speed, and ETA
- FIFO queue system with configurable max-concurrent limit
- Dynamic slot rows — only Slot 1 shown at startup; rows added as drives are inserted
- Queued badge + Cancel button; drain-on-done scheduling
- Image file size display in image bar
- Partition hash verification (configure_hash_dialog)
- Make Image dialog for creating backups
- Auto-clone on device insertion option
- Verify hash after clone option
- OSError handling in pipe read loop and drive poll (Windows handle close events)

### Unit Tests
- 86 tests passing (ODINTest, cppunit via vcpkg)
- New: `testIntegerOverflowException`, `testBootSectorException`,
  `testLZ4ExceptionCode`, `testZSTDExceptionCode`

---

## Version 0.33 (2011-02-20)

- Fix crash in command line version when stdin or stdout are redirected to a file
- Compress/decompress stream performance fix by using a separate compression thread;
  also fixes high memory consumption for compression
- Fix crash in image file header when save all blocks is not set

## Version 0.32 (2009-07-13)

- Fix exception when trying to read VSS snapshot on Vista and later
- Fix crash in drive list management under Vista and later
- Add bzip2 read support (write not included — performance too low)
- Fix memory leak in reading bzip2 compressed files

## Version 0.31 (2009-05-19)

- Fix GUI resize bug in Windows 7
- Fix drives not being listed in some cases
- Fix crash when reading GPT style disks
- Fix crash when a drive letter without a file system is listed
- Fix crash when reading partitions
- Various code cleanups

## Version 0.3 (2009-05-15)

- Read and write support for NTFS, FAT, FAT32 volumes
- Read and write all blocks mode (bypass file system, copy all sectors)
- 64-bit version
- New GUI
- Progress display
- CRC32 verification after read and write
- Volume snapshot support (VSS)
- Split file support

## Version 0.22 (2009-05-03)

- Use gzip for compression (was zlib raw stream before)
- Fix reading of split files from command line

## Version 0.21 (2009-03-05)

- Fix command line parsing bug that caused first argument to be ignored

## Version 0.2 (2009-01-16)

- Add compression support (zlib)
- Add support for multiple volumes on one physical drive
- Add support for writing to physical disk (restore partition)
- Add support for reading/writing to whole physical disk
- Add progress display for command line version

## Version 0.12 (2008-11-10)

- Fix output file names when writing split files
- Fix reading of split files from command line

## Version 0.11 (2008-10-24)

- Fix -restore command line parameter
- Fix reading of split files in GUI version
- Use generated build number in version info

## Version 0.1 (2008-10-01)

- Initial release
