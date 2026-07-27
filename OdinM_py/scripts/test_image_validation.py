"""Check main_window's image validation accepts good images and flags bad ones."""

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ttkbootstrap as ttk  # noqa: E402

import ui.main_window as mw  # noqa: E402

CASES = [
    ("D:/cards/E-working-2026-07-27.img", True, "raw image from pyimager"),
    ("C:/Users/BV Shop/OneDrive/Desktop/cds/OS1.3.44_Sentinel15.2.3.37.img.gz",
     True, "manufacturer gzip master"),
    ("C:/Users/BV Shop/OneDrive/Desktop/cds/test.img", True, "ODIN image"),
    ("C:/Users/BV Shop/OneDrive/Desktop/cds/img/roulette.img", False,
     "aborted ODIN capture"),
    ("C:/Users/BV Shop/OneDrive/Desktop/cds/nCompass Flash tool/ODIN.ini",
     False, "not an image at all"),
]

app = ttk.Window(themename="darkly")
app.withdraw()

# Build just enough of MainWindow to call the method under test.
win = mw.MainWindow.__new__(mw.MainWindow)
win._root_win = app

checks = []
for path, should_pass, note in CASES:
    if not Path(path).exists():
        print(f"  [skip] {note}: missing")
        continue
    # askyesno stands in for the user declining the "use it anyway?" prompt.
    with patch.object(mw.messagebox, "askyesno", return_value=False) as prompt:
        accepted = win._validate_image(path)
    prompted = prompt.called
    ok = (accepted == should_pass) and (prompted != should_pass)
    checks.append(ok)
    print(f"  [{'ok ' if ok else 'FAIL'}] {note}: "
          f"accepted={accepted} prompted={prompted}")

# And confirm the warning prompt is honoured when the user says yes.
bad = "C:/Users/BV Shop/OneDrive/Desktop/cds/img/roulette.img"
if Path(bad).exists():
    with patch.object(mw.messagebox, "askyesno", return_value=True):
        accepted = win._validate_image(bad)
    checks.append(accepted is True)
    print(f"  [{'ok ' if accepted else 'FAIL'}] user overrides warning: "
          f"accepted={accepted}")

app.destroy()
print(f"\n{sum(checks)}/{len(checks)} checks passed")
sys.exit(0 if all(checks) else 1)
