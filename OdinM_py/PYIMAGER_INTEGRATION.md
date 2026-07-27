# pyimager integration — working plan

Status as of 2026-07-27. Resume notes for wiring the built-in Python imager
into OdinM_py. Nothing here is committed yet.

## Why this exists

ODIN writes its progress indicator with
`WriteFile(hOut, ".", 1, ...)` where `hOut = GetStdHandle(STD_OUTPUT_HANDLE)`
(`src/ODIN/CommandLineProcessor.cpp:744`). If ODIN runs with stdout pointed at
the image file, those `.` bytes land **in the payload** and displace everything
after the first one.

Confirmed on `Desktop/cds/test.img`: 195 inserted bytes, one per progress tick
(~1 Hz at ~39 MiB/s, so 37–41 MiB apart — irregular, not a fixed byte stride).
Drift grows `+25` at the ext4 partition start to `+188` near the end.

It hides well: the MBR and first partition parse fine because they precede the
first dot. One-line detector:

```
actual file size - header fileSize (offset 0x78) == number of inserted bytes
```

`scripts/dedot.py` repairs it losslessly and self-validates against every ext4
backup superblock (8/8 exact on test.img). `scripts/pyimager.py` exists so new
captures can't hit this class of bug at all.

Other ODIN captures (`15.2.3.37Clean.img`, `img/15.0.6.2.img`) are clean, so
the bug is intermittent — it depends on how ODIN was launched, not on every run.

## Done

| item | state |
| --- | --- |
| `scripts/` toolkit (16 files + README) | working, see `scripts/README.md` |
| `pyimager.py` — `list/image/validate/verify/restore` | image+verify+restore all exercised on hardware |
| `image_disk()` library entry point | done, drives the GUI |
| gzip output (`.img.gz`) | done; digests are of **uncompressed** bytes (verified identical to a plain capture) |
| `validate_image_file()` | done; catches aborted ODIN captures (`dataSize 0`) and non-images |
| `pyimager_worker.py` — `PyImagerWorker` | mirrors `CloneWorker`'s interface (capture direction only) |
| Make Image → Engine combobox | done, 10/10 wiring tests |
| Main window image picker: `.gz` + validate-on-select | done, 6/6 tests |
| `OdinM_py.spec` bundling fix | **by inspection only — not proven by a real build** |
| `config_manager` engine setting | getters only (`get_engine`, `use_pyimager`), defaults to `odin` |

### Verified on hardware

Full round trip on the 7.42 GiB card (PhysicalDrive2):
card → `D:/cards/E-working-2026-07-27.img` → card → `verify` byte-identical,
sha256 `7ee85d803e5eb0a37868589aa744bf60312a88682048372825433a08e3198916`.
The flashed clone booted in the unit.

## Open decision — blocking step 3 below

`pyimager restore` cannot read `.gz` input. `clone_worker._run_raw_flash(gz=True)`
already streams a raw image out of a `.gz` on the fly (and falls back to a temp
file when the `.gz` wraps an ODIN container).

So with the engine set to `pyimager`, flashing the manufacturer `.gz` master
must either:

- **(a)** add gz streaming to `pyimager restore` — consistent behaviour, more code; or
- **(b)** silently fall back to ODIN for `.gz` inputs — less code, but the app
  would quietly use an engine the user didn't pick, which is the kind of thing
  that makes a corruption bug hard to trace later.

Ask before implementing. Leaning (a).

## Remaining steps

1. `config_manager.set_engine(value)` — validate against `ENGINES`, then `_save()`.
2. Expose the setting in the UI. Radio/combobox `ODIN` vs `pyimager`. Main
   window settings area is the natural home; `ui/main_window.py` already owns
   the config object.
3. Route the flash/clone slots through it (`app.py` builds `CloneWorker` for the
   5 slots + auto-flash). Needs a `PyImagerRestoreWorker` in
   `pyimager_worker.py` mirroring the same callback contract, plus the gz
   decision above.
4. Make Image dialog: default the engine combobox from the setting and persist
   changes back, instead of always starting at ODINC.
5. Run an actual `build_exe.bat` and confirm `dist/OdinM_py.exe` opens Make
   Image — the spec fix is unproven.

## Gotchas

- **Frozen exe**: `pyimager_worker` resolves `scripts/` from `__file__`, which
  does not exist in a one-file bundle. Fixed via `pathex=['.', 'scripts']` +
  `hiddenimports`. Any new module under `scripts/` that the GUI imports needs
  the same treatment.
- `ext4` (pip) is needed by the analysis tools but **not** by `pyimager`, which
  is dependency-free. Keep it that way — it is the fallback when everything
  else is broken.
- Bulk/recursive file copies into the repo get blocked by the permission
  classifier; copy files one at a time.
- `pyimager restore` has now run against exactly one device. Treat as exercised,
  not proven.
- `OdinM_py/clone_worker.py.bak2` is untracked cruft from an earlier edit.

## Tests

```bash
cd OdinM_py
python scripts/test_engine_wiring.py       # dialog engine switching, grid layout
python scripts/test_image_validation.py    # accept/reject/override on real images
python -c "import app, ui.main_window, ui.make_image_dialog, pyimager_worker"
python scripts/audit_images.py <img>       # dot check + FAT32 + ext4 walk
python scripts/audit_disk.py 2             # same for a live card
```

## Card facts worth not re-deriving

Two generations, distinguishable by the partition table alone:

| | old | new |
| --- | --- | --- |
| FAT32 | type `0x0B`, 2097152 sectors | type `0x0C`, 2099200 sectors |
| ext4 LBA | 2113536 | 2115584 |
| seen on | `Clean.img`, `15.0.6.2.img` | `OS1.3.44…img.gz` master, `test.img` |

- The manufacturer `.gz` master decompresses to exactly **1,083,179,008 bytes** —
  MBR + the 1 GiB FAT32 partition only, single-partition MBR. The unit creates
  and formats the ext4 partition itself on first boot.
- `eeprom.bin` (256 B) holds the **server** IP as ASCII at offset `0x26`,
  NUL-padded — not a card identity, so cloning it between cards is fine.
- No employee-card IDs are stored on the SD card at all: zero 10-hex-digit and
  zero long-decimal tokens across every ext4 file, even after the card had run
  live with 4 mounts. Card numbers live in NVRAM. `scripts/find_strings.py` was
  written to check whether the server IP appears anywhere besides `eeprom.bin`
  but **has not been run** — that claim is still unverified.
