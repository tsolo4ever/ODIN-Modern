# scripts/

Standalone tools for capturing and analysing SD card images (JCM / ncompass
Sentinel work). Everything here is read-only with respect to disks and images
except `pyimager.py restore`, which is the only command that ever opens a disk
for writing.

Requires Python 3.11+. `pyimager.py` has **no dependencies**; the ext4 tools
need `pip install ext4`. Raw disk access needs an elevated shell.

## Capture

| tool | what it does |
| --- | --- |
| `pyimager.py` | dependency-free raw disk imager — `list`, `image`, `verify`, `restore` |

```bash
python pyimager.py list
python pyimager.py image 2 D:\cards\before.img          # --partition N, --offset/--length
python pyimager.py image 2 D:\cards\before.img.gz       # .gz output is compressed on the fly
python pyimager.py validate D:\cards\before.img.gz      # is there really an image in there?
python pyimager.py verify 2 D:\cards\before.img
python pyimager.py restore before.img 2 --confirm 2     # DESTRUCTIVE, untested on hardware
```

Writes plain dd-style images — no container header — plus a `.json` sidecar
(device model/serial, partition table, timings, digests, bad sectors) and a
`.sha256`. Progress goes to **stderr only**; nothing but payload is ever written
to the image handle. Unreadable sectors are retried per-sector, then zero-filled
and reported, never skipped — skipping would shift every following byte.

An output path ending in `.gz` is gzip-compressed as it is written
(`--gzip-level 1-9`, default 6). The digests always describe the **uncompressed**
disk bytes, so a `.gz` image's hash still compares directly against a plain image
or a live disk read — verified equal on a 32 MiB region.

`validate` answers "does this file actually contain a disk image?" — it
decompresses the first MiB of a `.gz`, looks for an ODIN header or an MBR
signature, and reads the partition table. It catches an aborted ODIN capture
(header present, `dataSize 0`) that looks plausible from its size alone.

### Use as a library

```python
from pyimager import image_disk, validate_image_file

meta = image_disk(2, "D:/cards/card.img.gz", sha1=True,
                  on_progress=lambda done, total: ...,
                  should_cancel=stop_event.is_set)
```

`image_disk()` is what the OdinM GUI drives — see "GUI integration" below.

## Analyse

| tool | what it does |
| --- | --- |
| `audit_images.py` | run the whole toolkit over one or more images and report |
| `odin_img.py` | ODIN header + MBR parsing, `ImageWindow` (library) |
| `ext4_manifest.py` | per-file SHA-1 manifest of an image's ext4 partition |
| `manifest_diff.py` | diff two manifests: added/removed/changed + new card-ID tokens |
| `fat_ls.py` | FAT32 root listing from an image |
| `zero_extents.py` | non-zero extent map and SHA-256 of a partition region |

```bash
python audit_images.py C:\cds\*.img
python ext4_manifest.py C:\cds\before.img baseline
python ext4_manifest.py C:\cds\after.img  after
python manifest_diff.py manifests/baseline.json manifests/after.json
```

## Repairing ODIN dot corruption

ODIN writes its progress indicator with
`WriteFile(hOut, ".", 1, ...)` where `hOut = GetStdHandle(STD_OUTPUT_HANDLE)`
(`src/ODIN/CommandLineProcessor.cpp:744`). If ODIN runs with stdout pointed at
the image file, those dots land **in the payload**, inserting one byte each time
the progress timer fires and displacing everything after the first one.

Observed on `cds/test.img` (2026-07-27): 195 inserted bytes, one roughly every
37–41 MiB (a ~1 Hz timer at ~39 MiB/s), drift `+25` at the ext4 partition start
growing to `+188` near the end.

It hides well — the MBR and the first partition still parse perfectly because
they precede the first dot. The cheap detector is in every `audit_images.py`
report:

```
actual file size - header fileSize (offset 0x78)  ==  number of inserted bytes
```

`dedot.py` repairs it. Most dots land in zero-filled space and are found
directly; the few that land inside dense metadata are inferred from the drift
recorded by each ext4 backup superblock, and the result is only trusted if
**every** backup superblock then lands exactly where it belongs. `test.img`
repaired cleanly: 170 dots inside the ext4 partition, 8/8 backups exact.

Note the corruption is in the **image file**, not on the card.

| tool | what it does |
| --- | --- |
| `dedot.py` | locate the dots and expose a corrected read-through view |
| `verify_drift.py` | measure drift at each backup superblock (proof/diagnosis) |

## Live cards

These read attached cards rather than images, via `\\.\PhysicalDriveN`. They
have drive numbers hardcoded in a `DISKS` constant — edit it before use.

| tool | what it does |
| --- | --- |
| `raw_disk.py` | read-only `\\.\PhysicalDriveN` wrapper (library) |
| `diff_ext4.py` | filesystem-level diff of the ext4 partitions on two cards |
| `find_id.py` | extract every ext4 file and hunt for a card ID |
| `cat_files.py` | dump selected files off a card |

## GUI integration

`pyimager` is selectable from **Make Image → Engine**, alongside ODINC:

| engine | output |
| --- | --- |
| `ODINC.exe` | ODIN container image (Options… sets backup flags) |
| `pyimager` | plain raw `.img` |
| `pyimager` gzip | `.img.gz`, compressed while reading |

Picking a `.gz` filename switches to the gzip engine and vice versa, so the
extension and the engine can't disagree. The Options… button is disabled for
pyimager because ODINC backup flags don't apply.

Choosing pyimager also skips the separate hash pass: the digests are computed
during the read, so a 7.4 GiB image isn't read twice. That's not just a speed
win — for a `.gz` it's the only correct hash, since hashing the file itself
would hash compressed bytes rather than disk bytes.

The main window's image picker accepts `.gz` and runs `validate_image_file()`
on the selection, warning (with an override) if the file isn't a usable image.

Wiring lives in `../pyimager_worker.py` (`PyImagerWorker` mirrors
`CloneWorker`'s interface, so the dialog swaps engines with one branch).

```bash
python scripts/test_engine_wiring.py       # dialog engine switching, grid layout
python scripts/test_image_validation.py    # accept/reject/override on real images
```

## Data

`manifests/` and `extracted/` hold analysis output, not code. Card data should
not be committed — see `.gitignore` in this folder.
