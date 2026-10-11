"""Draws the app icon (a ring of hues around a halo) with Qt: icon.ico for
Windows and a 1024px icon.png. build.ps1 runs it; commit both files."""

import os
import sys

from PySide6.QtGui import QGuiApplication

app = QGuiApplication(sys.argv)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from halo_widgets import app_icon  # noqa: E402

here = os.path.dirname(os.path.abspath(__file__))
for name, size in (("icon.ico", 256), ("icon.png", 1024)):
    out = os.path.join(here, name)
    print("saved" if app_icon(size).toImage().save(out) else "FAILED", out)
