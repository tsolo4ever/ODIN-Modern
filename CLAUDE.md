# ODIN Project Rules

## Project Overview
Windows disk imaging and backup utility (C++17, WTL 10.0, VS2022 v145 toolset, x64-only).
Solution file: `ODIN.sln` — 7 projects (ODIN, ODINC, OdinM, zlib, libz2, ODINHelp, ODINTest).

## Build Command
```
"C:/Program Files/Microsoft Visual Studio/18/Community/MSBuild/Current/Bin/MSBuild.exe" ODIN.sln /p:Configuration=Debug /p:Platform=x64 /m /nologo
```
- `msbuild` is not on PATH — use the full path above
- `/m` enables parallel build; `/nologo` suppresses banner
- Run after **every** file change
- Fix all errors before moving to the next file
- Warnings are acceptable; errors are not
- Build must pass before committing

### Build a single project (faster)
```
"C:/Program Files/Microsoft Visual Studio/18/Community/MSBuild/Current/Bin/MSBuild.exe" ODIN.sln /p:Configuration=Debug /p:Platform=x64 /t:ODIN /m /nologo
```
Replace `/t:ODIN` with `/t:OdinM`, `/t:ODINTest`, etc. as needed.

## C++ Rules
- **C++17 throughout** — MSVC v143 toolset
- `std::unique_ptr` only — no raw `new`/`delete`
- `std::wstring` — no raw `wchar_t` arrays
- No `memset` on structs with STL members
- No `Sleep()` on the UI thread — use timers instead
- RAII everywhere — no manual resource management
- Never mix pre-2026 patterns with modern C++ in the same file

## Backup Policy
- Before editing any file: `cp file.cpp file.cpp.bak`
- If rewrite exceeds 50% of a file: backup first, then rewrite from scratch
- Delete `.bak` only after the build verifies success

## Git Rules
- Never push without explicit user approval
- Build must pass before every commit
- Conventional commit message examples for this project:
  - `feat: add LZ4/ZSTD compression support`
  - `fix: remove Sleep() from UI thread`
  - `refactor: migrate OdinManager to smart pointers`
  - `chore: remove legacy zlib 1.2.3`
  - `docs: update MODERNIZATION_CHECKLIST`

## Code Style
- Match the style of the file being edited
- Prefer editing existing files over creating new ones
- Keep files under 1000 lines; plan a refactor when approaching 1500
- No comments or docstrings added to code that wasn't changed

## Allowed Commands
- File reads
- File edits
- Build commands (msbuild, cmake)
- Git operations (status, diff, log)

## Never
- Commit broken code
- Mix old/new patterns in the same file
- Use `Sleep()` on the UI thread
- Use `memset` on structs with STL members
- Use raw `new`/`delete` (use smart pointers)
- Push without explicit user permission
