"""Run ODIN's bounded, read-only check against one explicit physical disk."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) in sys.path:
    sys.path.remove(str(ROOT))
sys.path.insert(0, str(ROOT))

from disk_health import (  # noqa: E402
    format_report,
    get_stable_physical_drive,
    quick_disk_check,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only SMART/prediction metadata plus bounded sampled reads."
    )
    parser.add_argument("--disk", type=int, required=True, help="Windows physical disk number")
    parser.add_argument("--no-smartctl", action="store_true", help="skip optional smartctl query")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.disk < 0:
        raise SystemExit("disk must be zero or greater")
    drive = get_stable_physical_drive(args.disk)
    if drive is None:
        print(f"Disk {args.disk} is not readable.")
        return 2
    print("ODIN Quick Disk Check - READ ONLY")
    print(drive.source_display)
    print(f"Target: {drive.raw_device_path}")
    print("Do not run while another program is using either bay of the shared dock.\n")

    last_bucket = -1

    def progress(done: int, total: int) -> None:
        nonlocal last_bucket
        bucket = done * 8 // max(1, total)
        if bucket != last_bucket or done == total:
            last_bucket = bucket
            print(f"Sample progress: {done}/{total}")

    try:
        result = quick_disk_check(
            args.disk,
            use_smartctl=not args.no_smartctl,
            on_progress=progress,
        )
    except KeyboardInterrupt:
        print("\nCancelled between sampled reads.")
        return 130
    report = format_report(result)
    print("\n" + report)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
