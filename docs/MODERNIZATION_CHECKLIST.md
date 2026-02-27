# ODIN Modernization Checklist

**Created:** 2026-02-21
**Updated:** 2026-02-23
**Updated:** 2026-02-26
**Status:** ✅ Phase 1 Complete | ✅ Phase 2 Complete | ✅ Phase 3 Complete (3.3/3.4 deferred by design) | ✅ Phase 4 Complete | ✅ LZ4/ZSTD Compression Added | ✅ `-list` confirmed working | ✅ XP dead code removed | ✅ VSS IOCTL fixed
**See also:** Map.md, First_CODE_REVIEW.md

---

### 3.3 Modern Threading ⏸ DEFERRED
Reason: Windows threading model is too tightly coupled to migrate safely.
- `CREATE_SUSPENDED` start — no `std::thread` equivalent
- `WaitForMultipleObjects` on N thread handles — no `std::thread` equivalent
- `TerminateThread` hard-kill used — no `std::thread` equivalent
- `SetThreadPriority` — not exposed by `std::thread`
- Would require full queue/sync architecture redesign → Phase 5+

### 3.4 String Handling ⏸ DEFERRED (incremental)
- `ATL::CString` intentionally kept in UI/dialog code (ODINDlg) — appropriate for `LoadString`/`FormatMessage`
- `std::wstring` already used throughout core logic (CommandLineProcessor, OdinManager, threads)
- `std::filesystem::path` — apply incrementally as path-manipulation files are touched (FileNameUtil, etc.)

---

## 🧪 Phase 5: Testing (Ongoing)

### 5.1 Unit Test Expansion
- [x] **Review existing tests** — 82 tests passing (ODINTest, cppunit via vcpkg)
- [x] **Add new tests** (ExceptionTest.cpp)
  - ~~Buffer queue thread safety~~ — non-deterministic; fix already in place (kBufferWaitTimeoutMs)
  - [x] Integer overflow exception — `testIntegerOverflowException`
  - [x] Boot sector exception — `testBootSectorException`
  - [x] LZ4 exception code — `testLZ4ExceptionCode`
  - [x] ZSTD exception code — `testZSTDExceptionCode`
  - ~~Auto-flash detection~~ — requires WM_DEVICECHANGE mock; manual test only

### 5.2 Integration Testing <need to be at work for this one>
- [ ] **Create test images**
- [ ] **Test scenarios**
  - [ ] Backup → Restore cycle (full round-trip)
  - [ ] All compression modes (gzip, bzip2 read, lz4, lz4hc, zstd)
  - [ ] Split file handling
  - [ ] Verify operation
  - [ ] Console output: `odinc -list` and `odinc -list -output=file.txt` (spawn process, capture stdout)

### 5.3 Stress Testing
- [ ] Long-running operations <need to be at work for this one>
- [ ] Error injection
- [ ] **Memory profiler** — Multiple flash operations; verify flat memory profile (single op already verified: ~18MB flat for 2GB image)

---

## 🎯 Phase 7: Release (Pending)

### 7.1 Code Cleanup
- [x] **Remove XP VSS dead code** — `runsOnWinXP`, `IVssBackupComponentsXP`, `vsbackup_xp.h` removed (commit 731e727)
- [x] **Magic numbers → named constants** — `kBufferWaitTimeoutMs` in BufferQueue, `kDoCopyBufferCount` in OdinManager (commit c3e4e42)
- [ ] Remove remaining dead code / commented sections
- [ ] Format code consistently
- [ ] Update copyright years

### 7.2 Performance
- [x] **CRC32 slice-by-8** — Major bottleneck resolved (Phase 4.4)
- ~~Profile remaining hot paths~~ — no measured bottleneck remaining; not worth speculative profiling

### 7.3 Release Preparation
- [ ] **Update version to 0.4.0** — bump version in all strings (ODIN.rc, resource.h, PrintUsage(), any About dialog)
- [ ] **Create release notes**
- [ ] **Clean Release x64 build** — `msbuild ODIN.sln /p:Configuration=Release /p:Platform=x64`
- [ ] **Test release binaries**
- [ ] **Git tag:** `git tag v0.4.0 && git push origin v0.4.0`

---

## ✅ Completion Criteria
- [x] All critical bugs fixed (Phase 1 — 6/6)
- [x] Builds successfully in VS2022/2026 (Debug x64, 0 errors)
- [x] ODINC PowerShell output works
- [x] Auto-flash feature works
- [x] CRC32 performance optimized
- [x] UI modernized (DPI, manifest, icons, snapshot tooltip)
- [ ] Smart pointers migration complete (Phase 3)
- [ ] Tests pass (Phase 5)
- [ ] Documentation complete (Phase 6)
- [ ] Release tagged (Phase 7)

---

## 🆘 Rollback Plan

```bash
# Return to last known good commit
git log --oneline
git reset --soft HEAD~1

# Return to origin state
git checkout origin/modernization -- <file>

# Nuclear option
git checkout 72aa6f8  # Initial commit
```

---

## 📊 Commit Summary (modernization branch)

| Commit | Type | Description |
|--------|------|-------------|
| c25276e | fix | Skip partition IOCTLs for VSS shadow copy devices |
| 50ed30c | fix | MakeSnapshot: raw new[]/delete[] → vector, fix uninit mountPoints[0] |
| c3e4e42 | refactor | Magic numbers → named constants (kBufferWaitTimeoutMs, kDoCopyBufferCount) |
| 731e727 | refactor | Remove XP VSS dead code (runsOnWinXP, IVssBackupComponentsXP, vsbackup_xp.h) |
| da95404 | fix | DriveList GPT support — fixes Size:0/Type:Unknown on all modern drives |
| f340c57 | fix | Switch _O_U16TEXT→_O_U8TEXT — fix space-between-chars output |
| 936094d | fix | Replace _open_osfhandle with freopen(CONOUT$) in InitConsole |
| 680df8d | fix | Add _setmode(_O_U16TEXT) to InitConsole (superseded by 936094d) |
| bfb5731 | refactor | CommandLineProcessor + ODINDlg: raw ptrs → unique_ptr (Phase 3.1) |
| a894723 | refactor | ImageStream: malloc→vector (Phase 3.2); threading/strings deferred |
| f6e56cf | chore | Create scripts/build.bat |
| 9f5ee84 | chore | Remove legacy zlib 1.2.3 source tree |
| 97cc408 | docs | MODERNIZATION_CHECKLIST Phase 3 OdinManager complete |
| cfdddbc | refactor | COdinManager: 12 raw ptrs → unique_ptr (Phase 3) |
| 42b8e9e | fix | ___chkstk_ms linker stub for MinGW LZ4/ZSTD libs |
| 63b843c | feat | LZ4, LZ4HC, ZSTD compression/decompression support |
| 145b603 | ui | Disable snapshot button + tooltip |
| e94b31f | perf | CRC32 slice-by-8 |
| e694f43 | fix | Null checks in Read/WriteThread |
| c14e91f | fix | Reset() wstring, sync_with_stdio, intptr_t |
| 6d4a277 | fix | UTF-8 output file encoding |
| 11c7d54 | fix | ParseAndProcess on CLI args |
| 2f91129 | feat | DPI v2, Common Controls v6, LVS_EX_DOUBLEBUFFER |
| f513378 | feat | One-time auto-flash warning |
| 7e40342 | feat | Auto-flash configurable size |
| 6b19efb | feat | Auto-flash for 8GB removable |
| 1bb6b1d | fix | zlib 1.3.2 include paths |
| a244783 | fix | WTL 10.0 migration, ATL::CString |
| 97c93da | fix | VS2026 compilation errors |
| 7d0cc94 | feat | Migrate to VS2026 + WTL 10 |
| c2f0db9 | fix | Boot sector validation (Phase 1.6) |
| 179053a | fix | Exception handling in thread Execute() |
| a6ae812 | fix | Integer overflow protection |
| f7d809b | fix | Memory leak in WaitToCompleteOperation |
| 2ce5612 | fix | BufferQueue race condition |
| 10641da | fix | ODINC PowerShell piping |

---

*Last updated: 2026-02-26. XP dead code removed, magic numbers → constants, MakeSnapshot vectors, VSS IOCTL fixed. Next: Phase 6 docs (PrintUsage + README), Phase 7 release prep (v0.4.0).*
