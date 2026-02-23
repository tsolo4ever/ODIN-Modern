# ODIN Project Map

## Current State (Build 4 results: 5 succeeded, 1 failed)

### ✅ What Builds
| Project | Status | Notes |
|---------|--------|-------|
| zlib | ✅ | Static lib from src/zlib-1.3.2/ |
| libz2 | ✅ | bzip2 static lib |
| ODIN | ✅ | **Main GUI app builds and links!** |
| ODINC | ✅ | Console version |
| ODINHelp | ✅ | CHM built successfully |
| ODINTest | ❌ | 33 errors from cppunit ABI incompatibility (see below) |

### ❌ ODINTest — Two categories of errors:
1. **FIXED** (12 errors): Missing `zlib.lib`/`libz2.lib` — added to linker in latest commit
2. **Needs rebuild** (33 errors): `cppunitud.lib`/`cppunitu.lib` compiled with VS2008 (old debug STL ABI)
   - Symbols no longer exist in VS2022+: `_Container_base_secure`, `_Has_debug_it`, dllimport `std::string` functions
   - **Root cause**: `lib/cppunit-1.12.1/lib32/` and `lib64/` are pre-built VS2008 binaries
   - **Fix required**: Rebuild CppUnit 1.12.1 from source using VS2022/2026

## How to Fix ODINTest (CppUnit rebuild)
Options, in order of effort:
1. **vcpkg** (easiest): `vcpkg install cppunit:x64-windows` then point ODINTest at vcpkg libs
2. **Download source**: Get CppUnit 1.12.1 from SourceForge, add a `cppunit.vcxproj` to the solution
3. **Switch test framework**: Replace CppUnit with Catch2 or Google Test (header-only, no prebuilt libs needed)

## Key File Locations

| File | Purpose |
|------|---------|
| `ODIN.sln` | Solution — 6 projects (zlib, libz2, ODIN, ODINC, ODINTest, ODINHelp) |
| `zlib.vcxproj` | zlib 1.3.2 static library |
| `libz2.vcxproj` | bzip2 static library |
| `ODIN.vcxproj` | Main GUI application |
| `ODINC.vcxproj` | Console version |
| `ODINTest.vcxproj` | Unit tests (CppUnit) — blocked on cppunit ABI |
| `ODINHelp.vcxproj` | CHM help file |
| `lib/WTL10/Include/` | WTL 10.0 headers |
| `lib/cppunit-1.12.1/` | CppUnit headers + **VS2008 pre-built libs** (need rebuild) |
| `src/zlib-1.3.2/` | zlib 1.3.2 sources |
| `src/bzip2-1.0.5/` | bzip2 sources |
| `src/ODIN/` | Main ODIN source |

## Git Commits (recent, on `modernization` branch)
- `068e151` — ODINTest: add zlib.lib+libz2.lib; remove C:\devtools path
- `(prev)` — zlib.vcxproj, ODIN.sln, ODIN.vcxproj, ODINTest.vcxproj, src/zlib-1.3.2/
- `8733985` — 5 .vcproj fixes (compiler errors + WTL80 paths)
- `2103aea` — ODINHelp full path to hhc.exe

## Important Context
- VS2026 = Version 18.3.1, uses `.vcxproj` format
- Output dirs: `Debug-x64\`, `Debug-Win32\`, `Release-x64\`, `Release-Win32\`
- ODIN links: `$(OutDir)zlib.lib;$(OutDir)libz2.lib;version.lib;shlwapi.lib`
- WTL path in all projects: `$(SolutionDir)lib\WTL10\Include`
- zlib output: `$(OutDir)zlib.lib` (configured in `<Lib>` element of zlib.vcxproj)
- Branch: `modernization`, 6 commits ahead of origin
