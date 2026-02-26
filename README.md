# ODIN - Open Disk Imager in a Nutshell

**Enhanced Fork — Modernized for Windows 10/11**

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Platform: Windows x64](https://img.shields.io/badge/Platform-Windows%20x64-blue.svg)](https://www.microsoft.com/windows)

---

## About

ODIN is a free, open-source disk imaging tool for Windows originally created in 2008.
This fork modernizes the codebase and adds new features for automated CompactFlash card imaging.

**Original project:** http://sourceforge.net/projects/odin-win
**Original authors:** See [doc/license.html](doc/license.html)

### What's New in This Fork

- **Auto-Flash Mode** — Automatically detect and restore images to CF cards on insertion
- **Fast compression** — LZ4, LZ4HC, and ZSTD in addition to gzip/bzip2
- **PowerShell support** — Fixed `odinc -list` output for scripting and automation
- **Bug fixes** — Buffer queue race condition, memory leaks, integer overflow protection
- **Modernized build** — Visual Studio 2026, C++17, Windows 11 SDK, x64-only

---

## Features

### Core (Original)
- Backup and restore entire drives or partitions
- Multiple compression formats (gzip, bzip2, lz4, lz4hc, zstd)
- Volume Shadow Copy (VSS) for live backups of running volumes
- Split large images across multiple files
- CRC32 integrity verification
- GUI (ODIN.exe) and command-line (odinc.exe) interfaces
- Backup only used blocks (faster, smaller images)

### Auto-Flash Mode
Designed for production environments flashing multiple CF cards with the same image:
- Detects removable drives matching a configured size (default: 8GB ±10%)
- Automatically triggers restore when a matching card is inserted
- Configurable size threshold; one-time safety warning on first enable
- Batch workflow: insert → flash → remove → repeat

---

## Build Requirements

- Windows 10 or later (x64)
- Visual Studio 2022 or 2026 Community with:
  - Desktop development with C++
  - Windows 11 SDK
  - C++ ATL for v143/v144 build tools
- Administrator privileges to run (required for raw disk access)

### Build

```cmd
"C:\Program Files\Microsoft Visual Studio\18\Community\MSBuild\Current\Bin\MSBuild.exe" ^
  ODIN.sln /p:Configuration=Release /p:Platform=x64 /m
```

Output: `x64\Release\ODIN.exe`, `x64\Release\ODINC.exe`

---

## Usage

### GUI

```cmd
ODIN.exe
```

### Command Line

```cmd
# List available drives
odinc -list

# Write list to file
odinc -list -output=drives.txt

# Backup a partition (drive index 1)
odinc -backup -source=1 -target=C:\backup.img.gz -compression=gzip

# Backup with fast compression
odinc -backup -source=1 -target=C:\backup.img.zst -compression=zstd

# Restore a partition
odinc -restore -source=C:\backup.img.gz -target=1

# Verify image integrity
odinc -verify -source=C:\backup.img.gz

# VSS snapshot (live system volume backup)
odinc -backup -source=1 -target=C:\backup.img -makeSnapshot
```

### Compression Options

| Flag | Format | Notes |
|------|--------|-------|
| `none` | Raw | Fastest write, largest file |
| `gzip` | gzip | Compatible with standard tools |
| `lz4` | LZ4 | Very fast, moderate compression |
| `lz4hc` | LZ4 HC | Slower write, better ratio than lz4 |
| `zstd` | ZSTD | Best ratio at reasonable speed |
| `bzip` | bzip2 | Read-only (decompress existing images) |

### Full CLI Reference

```
odinc [operation] [options] -source=[name] -target=[name]

Operations:
  -backup       Create image from disk/volume to file
  -restore      Restore image from file to disk/volume
  -verify       Check image integrity
  -list         List available drives

Options:
  -compression=[none|gzip|lz4|lz4hc|zstd|bzip]
  -makeSnapshot          Use VSS shadow copy (live backup)
  -usedBlocks            Backup only used filesystem blocks
  -allBlocks             Backup entire volume including free space
  -split=[nnn]           Split into nnn MB chunks
  -comment=[text]        Embed comment in image header
  -output=[file]         Write -list results to file
  -force                 Skip confirmation prompts
```

---

## Auto-Flash Configuration

Settings are stored in `ODIN.ini` in the same directory as `ODIN.exe`:

```ini
[MainWindow]
AutoFlashEnabled=1
AutoFlashSizeMB=8192
```

See [docs/AUTO_FLASH_FEATURE.md](docs/AUTO_FLASH_FEATURE.md) for full documentation.

---

## Project Structure

```
odin-win-code-r71-trunk/
├── src/
│   ├── ODIN/           # GUI application + core engine
│   ├── ODINC/          # CLI launcher
│   ├── ODINM/          # Legacy C++ UI (kept, not primary)
│   └── zlib-1.3.2/     # zlib source
├── lib/
│   ├── lz4_win64_v1_10_0/   # LZ4 prebuilt libs
│   └── zstd-v1.5.7-win64/   # ZSTD prebuilt libs
├── OdinM_py/           # Multi-drive UI (Python/ttkbootstrap) — primary
├── testsrc/            # Unit tests (CppUnit, 82 passing)
├── docs/               # Documentation
└── scripts/            # Build scripts
```

---

## Safety

ODIN writes directly to raw disk. Wrong target = data loss.

- Always verify source and target drive indices before operations
- Use `-verify` after creating images
- Test restores on non-critical hardware first
- Auto-Flash mode shows a one-time warning before first use

---

## Documentation

- [docs/Map.md](docs/Map.md) — Project overview and navigation
- [docs/MODERNIZATION_CHECKLIST.md](docs/MODERNIZATION_CHECKLIST.md) — Development roadmap
- [docs/AUTO_FLASH_FEATURE.md](docs/AUTO_FLASH_FEATURE.md) — Auto-flash feature guide
- [doc/readme.txt](doc/readme.txt) — Original project notes

---

## License

GPL v3. See [doc/license.html](doc/license.html) for full text.

```
Copyright (C) 2008-2009 Original ODIN Authors
Copyright (C) 2026 Fork Contributors
```

---

## Dependencies

- **zlib 1.3.2** — Jean-loup Gailly and Mark Adler
- **bzip2 1.0.5** — Julian Seward
- **LZ4 1.10.0** — Yann Collet
- **ZSTD 1.5.7** — Meta Platforms
- **WTL 10.0** — Microsoft/Community
- **CppUnit** — CppUnit team
