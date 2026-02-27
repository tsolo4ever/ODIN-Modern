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

---

## Agents

Two specialized sub-agents are available for this project. Use them by typing the command at the start of your message.

### `/code-mentor` — For writing, reviewing, and debating code
**When to use:** You want to write new code, review something you wrote, or have a back-and-forth technical discussion about an approach.

- Reviews code and explains *why* something is wrong, not just *what* to fix
- Engages in genuine debate — if you push back with a good argument, it will update its position
- Handles planning and complex review itself (Sonnet-level), delegates trivial edits internally to save cost
- Enforces all ODIN C++ rules: `unique_ptr`, no `Sleep()` on UI thread, no `memset` on STL structs, file size limits

**Example triggers:**
- "Review this function I wrote"
- "I used X approach instead of Y — am I wrong?"
- "Help me write a function to do..."
- "Is this the right pattern here?"

---

### `/plan-implementer` — For executing an already-approved plan
**When to use:** A plan has already been designed (by you, by Sonnet, or from a previous session) and you just want it implemented faithfully with no surprises.

- Executes each step exactly as written — does not redesign or improve on its own
- If it spots a bug or concern in the plan, it flags it in an `## Implementation Notes` section at the end but still implements as specified
- Runs the build after each file change and reports results
- Creates `.bak` backups before modifying any file

**Example triggers:**
- "Here is the plan we agreed on, implement it"
- "Implement step 3 from the plan"
- "Go ahead and make these changes: [list]"

---

**Tip:** If you forget, just describe what you want and the orchestrator will pick the right one — but naming the agent explicitly gets you there faster.
