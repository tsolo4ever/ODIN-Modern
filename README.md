# ODIN - Open Disk Imager in a Nutshell

**Enhanced Fork with Auto-Flash Support**

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Platform: Windows](https://img.shields.io/badge/Platform-Windows-blue.svg)](https://www.microsoft.com/windows)

---

## 📖 About This Fork

This is an enhanced fork of the original [ODIN disk imaging tool](http://sourceforge.net/projects/odin-win) with modernization improvements and new features for automated CompactFlash card imaging.

### Original Project

**ODIN (Open Disk Imager in a Nutshell)** was originally created in 2008-2009 as a free, open-source disk imaging solution for Windows. The original project can be found at:
- **Original SourceForge Project:** http://sourceforge.net/projects/odin-win
- **Original License:** GPL v3
- **Original Author(s):** See [doc/license.html](doc/license.html)

### What's New in This Fork

This fork adds:
- ✨ **Auto-Flash Mode** - Automatically detect and flash CompactFlash cards on insertion
- 🔧 **PowerShell Support** - Fixed `odinc -list` output redirection for automation
- 🛡️ **Security Fixes** - Critical bug fixes from comprehensive code review
- 📊 **Enhanced Output** - JSON/CSV/table formats for scripting (planned)
- 🎯 **Configurable Settings** - Flexible CF card size matching and behavior options

---

## 🎯 Features

### Core Functionality (Original)
- ✅ Backup and restore entire drives or partitions
- ✅ Compression support (gzip, bzip2)
- ✅ Volume Shadow Copy (VSS) integration for live backups
- ✅ Split large images across multiple files
- ✅ CRC32 verification
- ✅ Both GUI and command-line interfaces
- ✅ Support for FAT, NTFS file systems
- ✅ Backup only used blocks (saves space and time)

### New Features (This Fork)
- 🆕 **Auto-Flash Mode** - Automatically restore images to CF cards when inserted
  - Configurable card size matching (±10% tolerance)
  - Optional volume label verification
  - Confirmation dialog option
  - Auto-eject when complete
  - Status notifications

- 🆕 **Enhanced CLI Output** - Fixed PowerShell piping and redirection
  ```powershell
  odinc -list > drives.txt
  odinc -list | Select-String "Drive"
  ```

- 🆕 **Improved Stability** - Critical bug fixes for production use

---

## 🚀 Quick Start

### Prerequisites

- Windows 7 or later (Windows 10/11 recommended)
- Administrator privileges (required for disk access)
- Visual Studio 2008 (current) or VS2022 (after modernization)

### Download

```bash
git clone https://github.com/tsolo4ever/odin-win-code-r71-trunk.git
cd odin-win-code-r71-trunk
```

### Build Instructions

**Current Build (VS2008):**
1. Install Visual Studio 2008 (see [doc/HowToBuild.txt](doc/HowToBuild.txt))
2. Download and install dependencies (ATL 3.0, WTL 8.0, VSS SDK 7.2)
3. Open `ODIN.sln` in Visual Studio 2008
4. Build → Build Solution

**After Modernization (VS2022 - Coming Soon):**
1. Install Visual Studio 2022 Community (free)
2. Open `ODIN.sln` - conversion will happen automatically
3. Build → Build Solution

### Usage

**GUI Mode:**
```cmd
ODIN.exe
```

**Command Line:**
```cmd
# List available drives
odinc -list

# Backup partition
odinc -backup -source=1 -target=C:\backup.img -compression=gzip

# Restore partition
odinc -restore -source=C:\backup.img -target=1

# Verify image
odinc -verify -source=C:\backup.img
```

**Auto-Flash Mode (GUI):**
1. Launch ODIN.exe
2. Check "Enable Auto-Flash Mode"
3. Click "Configure..." to set:
   - Image file path
   - CF card size (e.g., 8192 MB for 8GB)
   - Optional volume label
   - Confirmation and eject preferences
4. Insert CF card - auto-flashing starts automatically!

---

## 📚 Documentation

- **[Map.md](Map.md)** - Project overview, current state, and navigation guide
- **[CODE_REVIEW.md](CODE_REVIEW.md)** - Comprehensive security and code analysis
- **[MODERNIZATION_CHECKLIST.md](MODERNIZATION_CHECKLIST.md)** - Implementation roadmap
- **[doc/HowToBuild.txt](doc/HowToBuild.txt)** - Original build instructions
- **[doc/readme.txt](doc/readme.txt)** - Original project notes

---

## 🔧 Configuration

### Auto-Flash Settings

Configuration is stored in `odin.ini` in the same directory as ODIN.exe:

```ini
[Options]
AutoFlashEnabled=1
AutoFlashImagePath=C:\Images\cf_system.img
AutoFlashCardSizeMB=8192
AutoFlashCardLabel=
AutoFlashShowConfirmation=1
AutoFlashEjectWhenDone=1
```

### Command Line Options

```
odinc [operation] [options] -source=[name] -target=[name]

Operations:
  -backup       Create image from disk/volume to file
  -restore      Restore image from file to disk/volume  
  -verify       Check image integrity
  -list         List available drives

Options:
  -compression=[gzip|bzip|none]  Compression format
  -makeSnapshot                  Use VSS snapshot (implies -usedBlocks)
  -usedBlocks                    Backup only used blocks
  -allBlocks                     Backup entire volume
  -split=[nnn]                   Split into nnn MB chunks
  -comment=[text]                Add comment to image
  -force                         Skip warnings (dangerous!)
```

---

## 🛠️ Development

### Project Structure

```
odin-win-code-r71-trunk/
├── src/
│   ├── ODIN/           # Main application (GUI + core)
│   ├── ODINC/          # CLI wrapper
│   ├── zlib.1.2.3/     # Compression library
│   └── bzip2-1.0.5/    # Compression library
├── testsrc/            # Unit tests (CppUnit)
├── doc/                # Documentation
├── lib/                # External libraries
├── Map.md              # Project navigation
├── CODE_REVIEW.md      # Security analysis
└── MODERNIZATION_CHECKLIST.md  # Roadmap
```

### Building from Source

See [Map.md](Map.md) for detailed development setup and [MODERNIZATION_CHECKLIST.md](MODERNIZATION_CHECKLIST.md) for the modernization roadmap.

### Contributing

Contributions are welcome! This is a fork focused on:
1. Bug fixes and security improvements
2. Auto-flash functionality for embedded systems
3. Modernization to current C++ standards
4. Enhanced automation features

Please ensure:
- Code follows existing style
- Critical changes include tests
- Security-sensitive code is reviewed
- Changes are documented

---

## 🐛 Known Issues

See [CODE_REVIEW.md](CODE_REVIEW.md) for comprehensive analysis of current issues and planned fixes.

### Critical (Being Fixed)
- Race condition in buffer queue management
- Memory leaks in exception paths
- Integer overflow risks on large disks

### High Priority
- PowerShell piping partially broken (fixed in this fork)
- Boot sector validation needed

### Workarounds
- Use `odinc.exe` wrapper for command-line operations
- Avoid operations on drives >2TB until overflow fixes complete
- Run with admin privileges to avoid VSS failures

---

## ⚠️ Safety Warnings

**IMPORTANT:** ODIN operates at the raw disk level and can:
- ✅ Save you from data loss
- ❌ Destroy your entire disk if used incorrectly

**Best Practices:**
1. ✅ **Always verify** your source and target before operations
2. ✅ **Test restores** on non-critical hardware first
3. ✅ **Keep backups** of important data elsewhere
4. ✅ **Use verify** command after creating images
5. ❌ **Never restore** without triple-checking the target drive
6. ❌ **Don't use** on production systems without testing

**Auto-Flash Mode Safety:**
- Uses size and optional label matching to prevent wrong-target flashing
- Shows confirmation dialog by default
- Always verify settings before enabling auto-flash
- Test with non-critical CF cards first

---

## 📊 Roadmap

### Version 0.4.0 (Current Development)
- [x] Code review and security audit
- [x] Project documentation (Map.md, etc.)
- [ ] Fix PowerShell output redirection
- [ ] Implement auto-flash mode
- [ ] Critical bug fixes
- [ ] Enhanced exception handling

### Version 0.5.0 (Planned)
- [ ] Migrate to Visual Studio 2022
- [ ] Update to C++17 standard
- [ ] Smart pointer migration
- [ ] Updated zlib/bzip2 libraries
- [ ] Expanded test coverage

### Version 1.0.0 (Future)
- [ ] Full modernization complete
- [ ] JSON/CSV output formats
- [ ] Incremental backup support
- [ ] Enhanced VSS integration
- [ ] Cross-platform compatibility layer

---

## 📄 License

This project maintains the original GPL v3 license.

```
Copyright (C) 2008-2009 Original ODIN Authors
Copyright (C) 2026 Fork Contributors

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, version 3 of the License.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.
```

See [doc/license.html](doc/license.html) for full license text.

---

## 🙏 Credits

### Original ODIN Project
- **Original Authors:** See [doc/license.html](doc/license.html)
- **Original Project:** http://sourceforge.net/projects/odin-win
- **License:** GPL v3

### This Fork
- **Maintainer:** [@tsolo4ever](https://github.com/tsolo4ever)
- **Purpose:** Auto-flash functionality for embedded CF card imaging
- **Repository:** https://github.com/tsolo4ever/odin-win-code-r71-trunk

### Dependencies
- **zlib** - Jean-loup Gailly and Mark Adler (http://www.zlib.net/)
- **bzip2** - Julian Seward (http://www.bzip.org/)
- **WTL** - Microsoft/Community (https://sourceforge.net/projects/wtl/)
- **CppUnit** - CppUnit team (https://sourceforge.net/projects/cppunit/)

---

## 💬 Support

- **Issues:** Please report bugs via GitHub Issues
- **Questions:** Check existing documentation first (Map.md, CODE_REVIEW.md)
- **Original Project:** For original ODIN questions, see SourceForge project

---

## 🔗 Related Projects

- **Clonezilla** - Another excellent open-source disk imaging solution
- **dd** - Classic Unix disk imaging tool (available on Windows via WSL)
- **Macrium Reflect** - Commercial alternative with free edition

---

**⭐ If this fork helps you, please star the repository!**

Last Updated: 2026-02-21
