"""Headless checks for the ODIN/WSL Bridge runtime interlock."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app  # noqa: E402
from runtime_interlock import (  # noqa: E402
    ODIN_RUNTIME_MARKER,
    RuntimeMarker,
    odin_running,
    wsl_bridge_running,
)


def test_packaged_process_names_are_detected_case_insensitively() -> None:
    def no_marker(_name: str) -> bool:
        return False

    assert odin_running(
        process_names=["ODINM_PY V2.EXE"],
        marker_probe=no_marker,
    )
    assert wsl_bridge_running(
        process_names=["wsl-bridge.exe"],
        marker_probe=no_marker,
    )
    assert not wsl_bridge_running(
        process_names=["explorer.exe", "python.exe"],
        marker_probe=no_marker,
    )


def test_named_marker_takes_precedence_over_process_names() -> None:
    observed: list[str] = []

    def marker_probe(name: str) -> bool:
        observed.append(name)
        return True

    assert odin_running(process_names=[], marker_probe=marker_probe)
    assert observed == [ODIN_RUNTIME_MARKER]


def test_runtime_marker_context_closes_cleanly_off_windows() -> None:
    marker = RuntimeMarker(None)
    with marker:
        pass
    marker.close()


def test_odin_blocks_disk_operation_and_warns_once_per_conflict() -> None:
    instance = app.OdinMApp.__new__(app.OdinMApp)
    instance._root = object()
    instance._wsl_bridge_warning_shown = False
    messages: list[str] = []
    instance._window = type("Window", (), {"log": lambda _self, text: messages.append(text)})()

    with (
        patch.object(app, "wsl_bridge_running", return_value=True),
        patch.object(app.messagebox, "showwarning") as warning,
    ):
        assert instance._wsl_bridge_blocks("clone")
        assert instance._wsl_bridge_blocks("target verification")
        assert warning.call_count == 1

    assert "was not started" in messages[0]
    with patch.object(app, "wsl_bridge_running", return_value=False):
        assert not instance._wsl_bridge_blocks("clone")
    assert not instance._wsl_bridge_warning_shown


if __name__ == "__main__":
    tests = [
        test_packaged_process_names_are_detected_case_insensitively,
        test_named_marker_takes_precedence_over_process_names,
        test_runtime_marker_context_closes_cleanly_off_windows,
        test_odin_blocks_disk_operation_and_warns_once_per_conflict,
    ]
    for test in tests:
        test()
        print(f"[ok] {test.__name__}")
    print(f"\n{len(tests)}/{len(tests)} checks passed")
