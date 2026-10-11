"""Shared test setup: offscreen Qt, private settings and palette library,
modal dialogs stubbed so nothing can hang."""
import os, sys, shutil, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")
os.environ["NOUSHALO_SETTINGS"] = "NousHaloTest"
TMP = tempfile.mkdtemp(prefix="noushalo_test_")
os.environ["NOUSHALO_DATA"] = os.path.join(TMP, "data")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
FIX = os.path.join(ROOT, "tests", "fixtures")
OUT = os.path.join(ROOT, "tests", "out")
os.makedirs(OUT, exist_ok=True)

from PySide6.QtWidgets import QApplication, QMessageBox, QDialog
app = QApplication.instance() or QApplication(sys.argv)
from PySide6.QtCore import QSettings
QSettings("NousHalo", "NousHaloTest").clear()
QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
QMessageBox.warning = staticmethod(lambda *a, **k: (_ for _ in ()).throw(AssertionError("warning: %s" % (a[2] if len(a) > 2 else a))))
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Discard)
QDialog.exec = lambda self: QDialog.Rejected

def fixtures(kind=None):
    """Names of the private sample files. kind "pdf" = .ai files with a PDF
    copy inside, "native" = ones that only have Illustrator's own data."""
    import glob
    out = []
    for f in sorted(glob.glob(os.path.join(FIX, "*.ai"))):
        head = open(f, "rb").read(4)
        if kind is None:
            out.append(os.path.basename(f))
            continue
        is_pdf = head == b"%PDF"
        if is_pdf:
            import pymupdf
            try:
                is_pdf = any(pg.get_bboxlog() for pg in pymupdf.open(f))
            except Exception:
                is_pdf = False
        if (kind == "pdf") == is_pdf:
            out.append(os.path.basename(f))
    return out


def fixture(name):
    """A throwaway copy, so tests never touch even the fixture."""
    dst = os.path.join(TMP, name)
    shutil.copy(os.path.join(FIX, name), dst)
    return dst

def pump(n=5):
    for _ in range(n):
        app.processEvents()
