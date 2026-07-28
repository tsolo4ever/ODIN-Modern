"""Headless regression checks for Max Disks manual-probe behavior."""

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as app_module  # noqa: E402
import drive_manager as dm  # noqa: E402


class _FakeKernel32:
    def __init__(self, errors: dict[int, int]):
        self.errors = errors
        self.last_error = 0
        self.probed: list[int] = []

    def CreateFileW(self, path, *_args):
        disk_number = int(path.rsplit("PhysicalDrive", 1)[1])
        self.probed.append(disk_number)
        self.last_error = self.errors.get(disk_number, 0)
        if self.last_error:
            return dm.INVALID_HANDLE_VALUE
        return 100 + disk_number

    def CloseHandle(self, _handle):
        pass


def _run_probe(
    *,
    errors: dict[int, int],
    removable: set[int],
    sizes: dict[int, int],
    max_disks: int,
    removable_limit: int,
):
    fake_k32 = _FakeKernel32(errors)
    original_k32 = dm._k32
    original_get_last_error = dm.ctypes.get_last_error
    original_format_error = dm.ctypes.FormatError
    original_is_removable = dm.is_disk_removable
    original_get_size = dm._get_physical_disk_size

    dm._k32 = fake_k32
    dm.ctypes.get_last_error = lambda: fake_k32.last_error
    dm.ctypes.FormatError = lambda err: f"error {err}"
    dm.is_disk_removable = lambda disk_number: disk_number in removable
    dm._get_physical_disk_size = lambda disk_number: sizes.get(disk_number, 0)

    try:
        lines = dm.debug_probe_disks(max_disks=max_disks, removable_limit=removable_limit)
    finally:
        dm._k32 = original_k32
        dm.ctypes.get_last_error = original_get_last_error
        dm.ctypes.FormatError = original_format_error
        dm.is_disk_removable = original_is_removable
        dm._get_physical_disk_size = original_get_size
    return lines, fake_k32.probed


def test_max_two_stops_after_second_removable_disk():
    lines, probed = _run_probe(
        errors={4: 2, 5: 2},
        removable={2, 3},
        sizes={
            0: 128_035_676_160,
            1: 256_060_514_304,
            2: 7_969_177_600,
            3: 7_969_177_600,
        },
        max_disks=16,
        removable_limit=2,
    )

    assert probed == [0, 1, 2, 3]
    assert lines == [
        "disk 0: open OK — removable=False, size=128035676160 bytes",
        "disk 1: open OK — removable=False, size=256060514304 bytes",
        "disk 2: open OK — removable=True, size=7969177600 bytes",
        "disk 3: open OK — removable=True, size=7969177600 bytes",
    ]


def test_errors_before_limit_remain_visible():
    lines, probed = _run_probe(
        errors={0: 5, 2: 2},
        removable={1, 3},
        sizes={1: 7_969_177_600, 3: 7_969_177_600},
        max_disks=8,
        removable_limit=2,
    )

    assert probed == [0, 1, 2, 3]
    assert lines == [
        "disk 0: cannot open — err=5 (error 5)",
        "disk 1: open OK — removable=True, size=7969177600 bytes",
        "disk 2: cannot open — err=2 (error 2)",
        "disk 3: open OK — removable=True, size=7969177600 bytes",
    ]


def test_no_cap_probes_the_full_requested_range():
    lines, probed = _run_probe(
        errors={2: 2, 3: 2},
        removable={1},
        sizes={0: 128_035_676_160, 1: 7_969_177_600},
        max_disks=4,
        removable_limit=0,
    )

    assert probed == [0, 1, 2, 3]
    assert len(lines) == 4


def test_app_passes_configured_max_disks_to_probe():
    app = object.__new__(app_module.OdinMApp)
    app._config = SimpleNamespace(get_max_disks=lambda: 2)
    app._window = SimpleNamespace(log=lambda _message: None)
    refreshed = []
    app._monitor = SimpleNamespace(refresh=lambda: refreshed.append(True))
    received_limits = []
    original_probe = app_module.debug_probe_disks
    app_module.debug_probe_disks = lambda *, removable_limit: (
        received_limits.append(removable_limit) or []
    )

    try:
        app._refresh_disks()
    finally:
        app_module.debug_probe_disks = original_probe

    assert received_limits == [2]
    assert refreshed == [True]


if __name__ == "__main__":
    tests = [
        test_max_two_stops_after_second_removable_disk,
        test_errors_before_limit_remain_visible,
        test_no_cap_probes_the_full_requested_range,
        test_app_passes_configured_max_disks_to_probe,
    ]
    for test in tests:
        test()
        print(f"[ok] {test.__name__}")
    print(f"\n{len(tests)}/{len(tests)} checks passed")
