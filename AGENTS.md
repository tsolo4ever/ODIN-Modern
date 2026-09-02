# Agent guidance — ODIN-Modern

Primary project rules are in `CLAUDE.md`. This file supplements them for
agent sessions.

## Available agents

Two specialized agents are defined in `CLAUDE.md`:

- `/code-mentor` — write, review, or debate code; explains reasoning and
  enforces ODIN C++ rules (`unique_ptr`, no `Sleep()` on UI thread, etc.)
- `/plan-implementer` — execute an already-approved plan step-by-step,
  running the build after each file change

## Build requirement

Every C++ change must pass the build before being considered complete:

```
"C:/Program Files/Microsoft Visual Studio/18/Community/MSBuild/Current/Bin/MSBuild.exe" ODIN.sln /p:Configuration=Debug /p:Platform=x64 /m /nologo
```

Zero errors required. Warnings are acceptable.

## Project structure

| Path | Language | Purpose |
|---|---|---|
| `src/ODIN/` | C++17 / WTL | Core imaging engine + GUI |
| `src/ODINC/` | C++ | Thin console launcher — re-invokes odin.exe |
| `OdinM_py/` | Python 3.12 | Multi-drive flash UI (ttkbootstrap) |
| `src/zlib-1.3.2/` | C | zlib built as part of solution |
| `src/libbz2/` | C | bzip2 built as part of solution |
| `lib/lz4_win64_v1_10_0/` | — | Pre-built LZ4 static lib |
| `lib/zstd-v1.5.7-win64/` | — | Pre-built ZSTD static lib |
| `lib/WTL10/` | — | WTL 10 headers |

## Python environment

- Read the component's declared Python version before installing dependencies,
  testing, or packaging. Do not let the system-default `py` or `python`
  silently choose a different interpreter.
- `OdinM_py` uses a repository-local `.venv` created explicitly with Python
  3.12: `py -3.12 -m venv .venv`.
- Verify `.venv\Scripts\python.exe --version` before using the environment. If
  an existing `.venv` uses the wrong interpreter, get permission before
  replacing it, then reinstall the component's declared runtime and build
  dependencies.

## Key rules

- Back up any file before editing (`cp file.cpp file.cpp.bak`)
- Never push without explicit user approval
- Never mix old/new C++ patterns in the same file
- `std::unique_ptr` only — no raw `new`/`delete`
- No `Sleep()` on the UI thread
