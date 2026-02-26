# ODIN v0.4.0 → v0.5.0 Development Plan

```markdown
# ODIN Development Plan
**Created:** 2026-02-21  
**Updated:** 2026-02-26
**Repository:** odin-win-code-r71-trunk  

---

#### Testing / Documentation / Release
→ Migrated to `MODERNIZATION_CHECKLIST.md` (Phases 5, 6, 7)
→ See checklist for current task status

---

## 🔮 v0.5.0 - Feature Release
**Status:** Planning  
**Target:** After v0.4.0 stable  


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

### OdinM_py Architecture
```
OdinM_py (Python/ttkbootstrap — active)
└── Spawns ODINC.exe instances
    └── One per card slot
    └── Independent operation
    └── 2 slots = 2 independent buses
        ├── Built-in slot → direct PCIe
        └── USB-C → dedicated controller

OdinM (C++/WTL — kept in repo, no longer primary)
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
  ✓ FIXED (c25276e) — skip partition IOCTLs for HarddiskVolumeShadowCopy devices
  → Re-enable snapshot button + update tooltip (open task)

Drive Size/Type:
  ✓ FIXED (da95404) — GPT support added to DriveList; Size/Type now correct

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

OdinM_py/                     ← Active multi-drive UI (Python)
└── main.py, slot_widget.py, ...

src/ODINM/                    ← Legacy C++ UI (kept, not primary)
```

---

---

## 💡 Nice to Have (No Target Version)

- **Named pipe for ODINC→ODIN** — elevated ODINC loses stdout pipe to non-elevated PS; named pipe would fix it. Low priority: disk imaging requires admin anyway, so running PS as admin is the practical workaround.
- **Thread pool** — reuse threads between operations for faster sequential flash starts. Not really worth it: thread creation costs milliseconds, operations take minutes.

*Update this document as work progresses*
*See also: CODE_REVIEW.md, ARCHITECTURE.md*
```