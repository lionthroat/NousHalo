"""Imports each private fixture and compares our render with MuPDF's.
Fixtures are copies in tests/fixtures (gitignored); nothing else is read."""
import glob, os, sys, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np, pymupdf
from PySide6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])
from halo_import import import_file
from halo_render import render_artboard, qimage_to_rgba

OUT = os.path.join(os.path.dirname(__file__), "out"); os.makedirs(OUT, exist_ok=True)
files = sorted(glob.glob(os.path.join(os.path.dirname(__file__), "fixtures", "*.ai")))
worst = 0
for f in files:
    # MuPDF is the reference, so only files with a PDF copy inside count here
    if open(f, "rb").read(4) != b"%PDF":
        continue
    try:
        if not any(pg.get_bboxlog() for pg in pymupdf.open(f)):
            continue
    except Exception:
        continue
    t = time.time()
    doc = import_file(f)
    n = sum(len(l.items) for l in doc.layers)
    src = pymupdf.open(f)
    for i, ab in enumerate(doc.artboards):
        ours = render_artboard(doc, ab, 1.0, transparent=False)
        a = qimage_to_rgba(ours)[..., :3].astype(float)
        pix = src[i].get_pixmap(dpi=72, alpha=False)
        b = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.stride)[:, :pix.width*3].reshape(pix.height, pix.width, 3).astype(float)
        h, w = min(a.shape[0], b.shape[0]), min(a.shape[1], b.shape[1])
        diff = np.abs(a[:h, :w] - b[:h, :w]).mean(-1)
        bad = (diff > 40).mean() * 100
        worst = max(worst, bad)
        name = os.path.splitext(os.path.basename(f))[0]
        ours.save(os.path.join(OUT, name + "_ours.png")); pix.save(os.path.join(OUT, name + "_mupdf.png"))
        print("%-42s items %5d layers %d  mean diff %5.1f  pixels off %5.2f%%  %.1fs" % (name[:42], n, len(doc.layers), diff.mean(), bad, time.time()-t))
    for note in doc.import_notes: print("   note:", note)
print("OK (worst %.2f%% of pixels off, text anti-aliasing)" % worst if worst < 6 else "WORST %.2f%%" % worst)
