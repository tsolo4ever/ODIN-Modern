# Native Python ODIN Container Compatibility and Compression Plan

Date: 2026-08-29

Status: Draft complete; implementation requires human approval.

Board request: replace the remaining `ODINC.exe` dependency with native Python
support that can read original ODIN images, restore them safely, and create
ODIN-compatible images with selectable compression.

## Outcome

The finished Python application will be able to:

- inspect and validate ODIN v1.x image containers without starting ODIN or
  `ODINC.exe`;
- restore supported original ODIN images, including compressed and legacy
  used-block images, through the existing guarded disk workflow;
- create ODIN v1.0 all-block images with None, ODIN zlib, LZ4, LZ4-HC, or Zstd
  compression;
- read legacy BZip2 images, while continuing to treat BZip2 creation as
  retired/read-only;
- read and create ODIN split image sets;
- verify the stored ODIN CRC32 and perform target read-back appropriate to the
  image layout before reporting success; and
- remove the normal runtime dependency on `ODINC.exe` after the compatibility
  matrix is proven.

This plan deliberately does not add another used-block writer. The new
`.odin-archive` profile owns general repair/archive capture, and the existing
Roulette compact profile owns that specialized workflow. Native ODIN writing
will remain the byte-for-byte, all-block compatibility option.

## Current implementation status - 2026-08-29

Phase 1 implementation is complete and Phase 2 has not started:

- `odin_container.py` now owns strict v1.x header parsing/packing, numbered
  split-set reads, the exact four-token allocation-map codec, bounded streaming
  readers for None/zlib/BZip2/LZ4/LZ4-HC/Zstd, logical reads, and stored CRC32
  verification.
- `scripts/odin_img.py` and `partition_reader.py` delegate ODIN header handling
  to that production parser. Direct file-offset consumers now reject
  compressed, used-block, and split layouts instead of treating them as raw.
- `scripts/test_odin_container.py` covers all six legacy compression IDs,
  all-block and used-block layouts, the C++ run-length byte oracle, CRC/no-CRC,
  UTF-16LE comments, numbered split sets, early EOF, trailing codec data,
  malformed headers, bitmap bounds, and the existing inspection adapters.
- The real `D:\cards\Old img\15.0.6.2.img` was read without writes: its MBR and
  declared layout validated, and a full 7,969,177,600-byte stream produced CRC32
  `71d3220f`, exactly matching the stored value.
- Guarded-restore, integration, partition-target, and engine-wiring regression
  suites remain green.

One cross-implementation acceptance item remains deliberately operator-attended:
run current `ODINC.exe -verify` against temporary Python fixtures, and retain a
C++-generated fixture for each readable codec. ODINC initializes Windows drive
discovery even for verify, so that check was not launched while attached test
hardware may be active. Phase 2 must not start until this final oracle check is
accepted or explicitly deferred by the operator.

## Current evidence

### Existing Python support

- `scripts/odin_img.py` and `partition_reader.py` parse the 128-byte ODIN
  header and can directly inspect only uncompressed, all-block payloads.
- `clone_worker.py` restores raw and external-gzip raw images in Python, but
  sends ODIN containers to `ODINC.exe`.
- `guarded_restore.py` supports raw, external gzip, compact images, and the new
  general archive. It does not yet decode an ODIN payload.
- `ui/image_options_dialog.py` already exposes the ODIN compression names and
  split size, but those values are currently command-line flags for
  `ODINC.exe`.
- Compressed ODIN images cannot currently be disk-verified by the Python hash
  workflow because it cannot produce their logical uncompressed byte stream.

### Legacy format facts proven from repository source

- The format GUID is `{1D4D7B73-FA01-40E1-B094-5267D8FA0BE7}` and the current
  header version is 1.0.
- The on-disk header is the 128-byte MSVC `/Zp8`
  `TDiskImageFileHeader` defined in `src/ODIN/FileHeader.h`.
- The header records the compression and volume-bitmap schemes, CRC/comment
  regions, logical data offset, stored data size, uncompressed used size,
  original volume size, cluster size, file count, and total logical file size.
- Compression IDs are 0 None, 1 ODIN "GZip", 2 BZip2, 3 LZ4, 4 LZ4-HC, and
  5 Zstd. The legacy "GZip" implementation calls zlib `deflateInit`, so it is
  a zlib stream rather than a `.gz` file wrapper.
- IDs 3 and 4 use the LZ4 frame format with independent 64 KiB blocks and a
  content checksum; only the compression level differs.
- BZip2 is intentionally read-only in the current C++ product.
- Used-block images store a compact run-length allocation map followed by
  packed allocated-cluster bytes. The CRC covers the uncompressed stored byte
  stream, not skipped free clusters.
- Split image sets are one logical byte stream cut into numbered `0000`,
  `0001`, and later files. Metadata offsets are logical-stream offsets.

### Available real fixture

`D:\cards\Old img\15.0.6.2.img` is a clean local ODIN v1.0, uncompressed,
all-block image:

- `dataOffset`: 200
- `dataSize`: 7,969,177,600 bytes
- `volumeSize`: 7,969,177,600 bytes
- partitions: FAT32 plus Linux
- actual file length exactly matches the header `fileSize`

This local operational image is evidence and a read-only acceptance fixture;
it must not be copied into Git. Compressed, used-block, and split fixtures must
be added as small deterministic test fixtures or generated from known test
payloads before those matrix cells can be marked proven.

## Compatibility boundary

### Read and restore

The reader must support every valid combination allowed by the v1 header:

| Area | Required support |
| --- | --- |
| Version | major 1; preserve and report minor version |
| Volume type | complete disk and individual partition |
| Allocation | all blocks and simple compressed run-length bitmap |
| Compression | None, zlib, ~~BZip2~~read only, LZ4, LZ4-HC, Zstd |
| Integrity | no checksum and stored CRC32 |
| Comment | empty or UTF-16LE comment bytes |
| Storage | single file and numbered split set |

Unknown IDs, unsupported major versions, overlapping metadata, truncated
members, integer overflow, data beyond declared bounds, surplus codec data,
and inconsistent split sets must fail closed before write access.

### Create

The writer will create:

- ODIN v1.0 complete-disk, all-block containers;
- stored CRC32 plus a sidecar SHA-256 of the logical disk bytes;
- optional UTF-16LE operator comment;
- None, ODIN zlib, LZ4, LZ4-HC, or Zstd payloads; and
- single-file or numbered split output.

The writer will not create BZip2, used-block, VSS, or multi-partition archive
sets. Those are either retired or already have safer project-specific
profiles.

## Architecture

### `odin_container.py`

Add one production module, separate from the inspection scripts, that owns:

- immutable header and split-set data models;
- exact little-endian pack/unpack of the 128-byte header;
- strict offset, length, enum, alignment, and file-set validation;
- a seekable logical reader across numbered split members;
- bounded streaming decompression adapters for all six compression IDs;
- a decoder for the ODIN four-value compressed run-length bitmap tuples;
- reconstruction of an all-block logical stream or allocated target ranges;
- stored CRC32 verification;
- streaming ODIN v1.0 all-block writing;
- compression adapters for None, zlib, LZ4/LZ4-HC, and Zstd;
- atomic publication of one file or an entire split set; and
- cancellation cleanup that never leaves an apparently complete image.

The module must not open a physical disk. It accepts ordinary binary sources
and sinks so format tests cannot accidentally touch hardware.

### Worker integration

Add a native ODIN worker, or refactor the existing Python worker behind a
common byte-stream interface, so capture and restore share:

- cancellation;
- selectable/copyable progress and error logging;
- exact byte counters;
- source/target identity revalidation;
- volume lock and dismount ownership;
- flush and Windows disk-property refresh; and
- explicit partial-target reporting after any write begins.

Do not add the format logic to `clone_worker.py`; that file is already an
ODINC process wrapper plus a separate raw writer. Native code needs a cohesive
module boundary rather than a third path in that class.

### Guarded restore integration

`guarded_restore.py` remains the authority for target eligibility and typed
confirmation. ODIN preflight will return a `GuardedImagePlan` containing:

- validated header and split-member identities;
- source-file sizes and hashes;
- required target capacity;
- logical all-block byte count or used-block range plan;
- compression/bitmap/CRC metadata; and
- the exact verification strategy.

Immediately before write access, every source member and target identity must
be revalidated. After restore:

- all-block images require a complete target read-back SHA-256 match; and
- used-block images require stored-stream CRC agreement plus read-back of
  every reconstructed allocated range in bitmap order.

Free ranges in a legacy used-block image are intentionally not claimed to be
zero or byte-identical because the ODIN format never stored them.

### Make Image integration

Replace the ODINC engine label with a native `ODIN container` profile. The
existing options dialog becomes a native model instead of a command-line flag
builder:

- All blocks (fixed for this profile)
- None
- ODIN zlib
- LZ4 fast
- LZ4 high compression
- Zstandard
- single file or split size

Keep the existing raw `.img.gz` engine distinct. An external gzip wrapper and
ODIN's internal zlib payload are different formats and must not share a label
or extension rule.

### Dependencies and packaging

- Use Python `zlib` and `bz2` for legacy IDs 1 and 2.
- Add pinned-compatible `lz4` and `zstandard` runtime packages.
- Include both packages and their native binaries in `OdinM_py.spec`.
- Add a startup/self-test that proves every advertised codec is importable.
- A missing codec must disable only the affected create choice and must reject
  a matching input before disk write; it must never silently substitute a
  different codec.

## Phased implementation

Implementation touches more than six paths and must proceed one approved phase
at a time.

### Phase 1 - Format library and conformance fixtures

Expected paths:

- `OdinM_py/odin_container.py` (new)
- `OdinM_py/scripts/test_odin_container.py` (new)
- `OdinM_py/scripts/odin_img.py`
- `OdinM_py/partition_reader.py`

Work:

1. Port header validation and packing into the production module.
2. Implement logical split-set reading and deterministic split naming.
3. Port the exact C++ bitmap token encoding/decoding with bounds on token
   count, cluster count, and reconstructed volume position.
4. Add streaming codec readers and reject early EOF, trailing codec members,
   expansion beyond `usedSize`, and output-length mismatch.
5. Verify the stored CRC32 against the uncompressed stored stream.
6. Make the inspection tools and partition reader delegate to the production
   parser so there is one interpretation of the format.
7. Add deterministic tiny golden fixtures for every compression ID, both
   allocation modes, CRC/no-CRC, comments, split sets, and malformed cases.

Exit gate:

- Python reads every golden fixture to the expected logical bytes/ranges.
- Current ODIN/ODINC reads Python-generated temporary all-block fixtures for
  each writable codec during development.
- Python reads C++-generated fixtures for all readable codecs and bitmap mode.
- The real `15.0.6.2.img` header, partitions, CRC, and logical bytes validate
  without writing to a disk.

### Phase 2 - Native guarded restore

Expected paths:

- `OdinM_py/guarded_restore.py`
- `OdinM_py/ui/guarded_single_flash.py`
- `OdinM_py/odin_worker.py` (new, final name may follow existing worker naming)
- `OdinM_py/scripts/test_guarded_restore.py`
- `OdinM_py/scripts/test_guarded_restore_integration.py`

Work:

1. Recognize single and split ODIN containers in guarded preflight.
2. Spool/validate the complete logical stream before confirmation when needed;
   never discover a codec or split failure after write starts.
3. Restore all-block images sequentially and used-block images by validated
   allocated ranges.
4. Preserve the existing target inventory, protected-hardware, capacity,
   typed-confirmation, lock, flush, refresh, cancellation, and untrusted-target
   rules.
5. Add mandatory target read-back using the strategy recorded at preflight.
6. Add synthetic tests for every codec, used-block seeks, split boundaries,
   source changes, cancellation, short writes, and verification mismatch.

Exit gate:

- All automated safety and compatibility tests pass.
- A user-attended restore of the real uncompressed ODIN fixture to an explicit
  disposable target passes full SHA-256 read-back.
- At least one small compressed all-block fixture and one used-block fixture
  pass disposable-target restore and range verification.

### Phase 3A - Native ODIN writer and codecs

Expected paths:

- `OdinM_py/odin_container.py`
- `OdinM_py/odin_worker.py`
- `OdinM_py/pyimager_worker.py` or a small shared worker primitive
- `OdinM_py/requirements.txt`
- `OdinM_py/OdinM_py.spec`
- `OdinM_py/scripts/test_odin_container.py`

Work:

1. Stream raw disk bytes into an ODIN v1.0 all-block container.
2. Reserve metadata, calculate CRC32/SHA-256 over logical input bytes, finalize
   header fields, flush, and atomically publish.
3. Implement None, zlib, LZ4, LZ4-HC, and Zstd output with exact legacy frame
   settings.
4. Implement atomic split-set creation; cancellation or failure removes every
   temporary member.
5. Revalidate source identity immediately before capture and before publish.
6. Package and self-test `lz4` and `zstandard` in the frozen executable.

Exit gate:

- Each Python-created codec fixture is readable by both Python and current
  ODINC during development.
- Decompressed logical bytes, CRC32, SHA-256, header sizes, and split-member
  concatenation all match the input exactly.
- Frozen executable imports every codec without launching a disk operation.

### Phase 3B - Operator workflow

Expected paths:

- `OdinM_py/ui/make_image_dialog.py`
- `OdinM_py/ui/image_options_dialog.py`
- `OdinM_py/ui/main_window.py`
- `OdinM_py/config_manager.py`
- `OdinM_py/scripts/test_engine_wiring.py`
- `OdinM_py/scripts/README.md`
- `OdinM_py/Map.md`

Work:

1. Replace ODINC flag handling with typed native options.
2. Show estimated required space, compression choice, split naming, and the
   distinction from external `.img.gz` before confirmation.
3. Dispatch the native writer and display real logical/stored byte progress.
4. Make native ODIN restore selectable in normal and guarded workflows.
5. Retain `ODINC.exe` only as an explicitly labeled compatibility fallback
   until the Phase 4 matrix is approved.

Exit gate:

- Headless UI tests prove engine/extension/options consistency.
- Operator logs are selectable and show the codec, logical bytes, stored
  bytes, CRC32, SHA-256, split members, and atomic publication result.
- No option can silently route to `ODINC.exe`.

### Phase 4 - Compatibility acceptance and ODINC retirement

Required matrix:

| Producer | Allocation | Compression | Storage | Python inspect | Python restore | Target verify |
| --- | --- | --- | --- | --- | --- | --- |
| legacy ODIN | all | None | single | pass | pass | full SHA-256 |
| legacy ODIN | all | zlib | single | pass | pass | full SHA-256 |
| legacy ODIN | all | BZip2 | single | pass | pass | full SHA-256 |
| legacy/current ODIN | all | LZ4/LZ4-HC/Zstd | single | pass | pass | full SHA-256 |
| legacy ODIN | used | each available legacy codec | single | pass | pass | allocated ranges + CRC |
| legacy ODIN | all/used | representative codec | split | pass | pass | mode appropriate |
| Python | all | None/zlib/LZ4/LZ4-HC/Zstd | single/split | pass | pass | full SHA-256 |

Only after the applicable rows are proven and the user approves retirement:

- change the default engine to native ODIN or raw according to the existing
  app-wide setting;
- remove the normal ODINC path setting and process-launch path;
- retain a separate diagnostic compatibility tool only if a real unsupported
  field image requires it; and
- update the project map and move this plan to `Implementations/Done/` after
  human acceptance.

## Fail-closed rules

- Parse and validate every byte-range relationship before decompression or
  disk access.
- Cap decompressed output at the declared `usedSize`/`volumeSize` and reject
  expansion beyond it.
- Require complete codec end-of-stream and reject concatenated/trailing codec
  members unless the format evidence explicitly proves they are valid.
- Require the bitmap to terminate exactly at the declared cluster count and
  the payload to terminate exactly at the sum of allocated bytes.
- Treat a missing, duplicate, reordered, resized, or changed split member as a
  source-change failure.
- Detect the known progress-dot corruption signature and direct the operator
  to the existing repair workflow; do not silently repair while restoring.
- Never download codecs or dependencies during an imaging job.
- Never publish a completed output name until header, stream, CRC32, SHA-256,
  flush, and close all succeed.
- Once target writes begin, any error or cancellation reports the target as
  untrusted until it is reflashed or independently verified.

## Validation commands

The implementation phase must run, as applicable:

```powershell
python -m py_compile odin_container.py odin_worker.py
ruff format --check <changed Python paths>
ruff check <changed Python paths>
mypy --follow-imports=skip odin_container.py odin_worker.py
python scripts\test_odin_container.py
python scripts\test_guarded_restore.py
python scripts\test_guarded_restore_integration.py
python scripts\test_engine_wiring.py
python scripts\test_image_validation.py
pyinstaller --noconfirm --clean OdinM_py.spec
```

The application must not be launched and no physical target may be written
during automated validation. Physical-media acceptance remains explicit,
operator-attended Phase 4 work.

## Approval decision requested

Approve this sequence:

1. Phase 1 format/conformance library.
2. Phase 2 guarded restore.
3. Phase 3A writer/codecs.
4. Phase 3B UI workflow.
5. Phase 4 physical compatibility acceptance, then ODINC retirement.

Implementation must stop after each phase for validation and scope review.
