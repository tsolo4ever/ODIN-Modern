"""Diff two ext4 manifests produced by ext4_manifest.py.

Reports files added / removed / changed by SHA-1, and every 10-hex-digit and
long-decimal token that is present in the AFTER image but absent from the
BASELINE - i.e. candidate employee-card IDs the unit wrote while on the floor.

Usage:
    python manifest_diff.py manifests/baseline.json manifests/after.json
"""

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def human(n):
    for unit in ("B", "KiB", "MiB", "GiB"):
        if abs(n) < 1024 or unit == "GiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


def section(title):
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")


a, b = load(sys.argv[1]), load(sys.argv[2])
print(f"BASELINE : {a['label']}  {a['image']}")
print(f"           {a.get('image_mtime')}  fs last write "
      f"{a.get('fs', {}).get('last_written')}")
print(f"AFTER    : {b['label']}  {b['image']}")
print(f"           {b.get('image_mtime')}  fs last write "
      f"{b.get('fs', {}).get('last_written')}")

for m, name in ((a, "baseline"), (b, "after")):
    r = m.get("repair") or {}
    if r.get("dot_corrupted"):
        print(f"  note: {name} image was ODIN dot-corrupted; "
              f"{r.get('dots_removed')} inserted bytes were removed before "
              f"reading (validated against backup superblocks)")

if a.get("fs", {}).get("uuid") != b.get("fs", {}).get("uuid"):
    print("\n*** WARNING: filesystem UUIDs differ - these are different "
          "filesystems (card was reformatted or this is another card) ***")
    print(f"    baseline {a.get('fs', {}).get('uuid')}")
    print(f"    after    {b.get('fs', {}).get('uuid')}")

pa, pb = a.get("partition_sha256"), b.get("partition_sha256")
if pa and pb:
    print(f"\nwhole-partition sha256 "
          f"{'IDENTICAL - nothing changed at all' if pa == pb else 'DIFFERS'}")
    if pa != pb:
        print(f"  baseline {pa}\n  after    {pb}")

fa, fb = a["files"], b["files"]
added = sorted(set(fb) - set(fa))
removed = sorted(set(fa) - set(fb))
changed = sorted(p for p in set(fa) & set(fb)
                 if fa[p].get("sha1") != fb[p].get("sha1"))
same = len(set(fa) & set(fb)) - len(changed)

section(f"ADDED - present only in {b['label']}  ({len(added)})")
for p in added:
    e = fb[p]
    print(f"  + {p}  {human(e.get('size', 0))}  sha1 {e.get('sha1')}  "
          f"mtime {e.get('mtime')}")

section(f"REMOVED - present only in {a['label']}  ({len(removed)})")
for p in removed:
    e = fa[p]
    print(f"  - {p}  {human(e.get('size', 0))}  sha1 {e.get('sha1')}")

section(f"CHANGED - different SHA-1  ({len(changed)})")
for p in changed:
    x, y = fa[p], fb[p]
    print(f"  ~ {p}")
    print(f"      size  {x.get('size')} -> {y.get('size')}")
    print(f"      sha1  {x.get('sha1')}")
    print(f"         -> {y.get('sha1')}")
    print(f"      mtime {x.get('mtime')} -> {y.get('mtime')}")

da, db = set(a.get("dirs", [])), set(b.get("dirs", []))
if da != db:
    section("DIRECTORIES")
    for d in sorted(db - da):
        print(f"  + {d}")
    for d in sorted(da - db):
        print(f"  - {d}")

# The point of the exercise: IDs that only exist after the card hit the floor.
for key, label in (("tokens_hex10", "10-hex-digit"), ("tokens_dec", "decimal")):
    ta, tb = a.get(key, {}), b.get(key, {})
    new = sorted(set(tb) - set(ta))
    section(f"NEW {label} tokens - in {b['label']} but not {a['label']}  "
            f"({len(new)})")
    if not new:
        print("  (none)")
    for t in new:
        print(f"  {t}   in: {', '.join(tb[t])}")

print(f"\nsummary: {len(added)} added, {len(removed)} removed, "
      f"{len(changed)} changed, {same} identical")
