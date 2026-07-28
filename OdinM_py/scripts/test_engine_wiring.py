"""Smoke-test MakeImageDialog's engine wiring without showing a window."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ttkbootstrap as ttk  # noqa: E402

import ui.make_image_dialog as mid  # noqa: E402
from config_manager import ENGINE_ODIN, ENGINE_PYIMAGER  # noqa: E402

# wait_window() would block; grab_set() needs a viewable window.
mid.MakeImageDialog.wait_window = lambda self, *a, **k: None
mid.MakeImageDialog.grab_set = lambda self, *a, **k: None


class _FakeConfig:
    """In-memory stand-in for ConfigManager - never touches odinm_py.ini."""

    def __init__(self, engine=ENGINE_ODIN):
        self._engine = engine

    def get_engine(self):
        return self._engine

    def use_pyimager(self):
        return self._engine == ENGINE_PYIMAGER

    def set_engine(self, value):
        self._engine = value


app = ttk.Window(themename="darkly")
app.withdraw()

cfg = _FakeConfig()
dlg = mid.MakeImageDialog(app, "C:/nonexistent/ODINC.exe", cfg)
dlg.withdraw()

print("engine values:")
for v in dlg._engine_cb.cget("values"):
    print(f"   - {v}")

checks = []


def check(name, got, want):
    ok = got == want
    checks.append(ok)
    print(f"  [{'ok ' if ok else 'FAIL'}] {name}: {got!r}"
          + ("" if ok else f"  (expected {want!r})"))


print("\ndefault engine (ODINC):")
check("use_pyimager", dlg._use_pyimager, False)
check("Options enabled", str(dlg._options_btn.cget("state")), "normal")

print("\nswitch to pyimager raw:")
dlg._engine_var.set(mid.ENGINE_PY)
dlg._output_var.set("D:/cards/demo.img")
dlg._on_engine_change()
check("use_pyimager", dlg._use_pyimager, True)
check("Options disabled", str(dlg._options_btn.cget("state")), "disabled")
check("output unchanged", dlg._output_var.get(), "D:/cards/demo.img")
check("config persisted", cfg.get_engine(), ENGINE_PYIMAGER)

print("\nswitch to pyimager gzip (extension should follow):")
dlg._engine_var.set(mid.ENGINE_PY_GZ)
dlg._on_engine_change()
check("output gz", dlg._output_var.get(), "D:/cards/demo.img.gz")

print("\nswitch back to raw (extension should revert):")
dlg._engine_var.set(mid.ENGINE_PY)
dlg._on_engine_change()
check("output raw", dlg._output_var.get(), "D:/cards/demo.img")

print("\nback to ODINC:")
dlg._engine_var.set(mid.ENGINE_ODINC)
dlg._on_engine_change()
check("Options re-enabled", str(dlg._options_btn.cget("state")), "normal")
check("hint set", bool(dlg._engine_hint.cget("text")), True)
check("config persisted", cfg.get_engine(), ENGINE_ODIN)

print("\nreopen with pyimager already the app-wide setting:")
dlg.destroy()
cfg2 = _FakeConfig(engine=ENGINE_PYIMAGER)
dlg = mid.MakeImageDialog(app, "C:/nonexistent/ODINC.exe", cfg2)
dlg.withdraw()
check("defaults to pyimager", dlg._use_pyimager, True)

# Grid sanity: nothing should share a cell in column 0.
rows = {}
clash = []
for child in dlg.winfo_children()[0].winfo_children():
    info = child.grid_info()
    if not info:
        continue
    key = (info["row"], info["column"])
    if key in rows:
        clash.append((key, rows[key], str(child)))
    rows[key] = str(child)
check("no overlapping grid cells", clash, [])

dlg.destroy()
app.destroy()

print(f"\n{sum(checks)}/{len(checks)} checks passed")
sys.exit(0 if all(checks) else 1)
