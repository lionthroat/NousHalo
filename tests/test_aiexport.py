"""Export as .ai, open the result again, and compare: pixels, layers, live text."""
from helpers import *
import glob

import numpy as np
import pymupdf
from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage, QTransform

from halo_aiexport import export_ai
from halo_import import import_file
from halo_model import Document, Layer, PathItem, RasterItem, TextItem, ellipse_subpaths, rect_subpaths
from halo_render import qimage_to_rgba, render_artboard

fails = []


def check(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        fails.append(msg)


def compare(a_doc, b_doc, label, tol=2.0):
    worst = 0
    for i, ab in enumerate(a_doc.artboards):
        a = qimage_to_rgba(render_artboard(a_doc, ab, 1.0, transparent=False)).astype(int)
        b = qimage_to_rgba(render_artboard(b_doc, b_doc.artboards[i], 1.0, transparent=False)).astype(int)
        h, w = min(a.shape[0], b.shape[0]), min(a.shape[1], b.shape[1])
        d = np.abs(a[:h, :w, :3] - b[:h, :w, :3]).mean()
        worst = max(worst, d)
    check(worst < tol, "%s looks the same after .ai round trip (mean diff %.2f)" % (label, worst))


# a document with every kind of object
d = Document.blank(400, 300)
d.layers[0].name = "Back"
p = PathItem(rect_subpaths(QRectF(20, 20, 200, 120)), "#bf616a", "#2e3440", 4)
p.join = 1
d.layers[0].items.append(p)
hole = PathItem(rect_subpaths(QRectF(240, 20, 140, 140)) + ellipse_subpaths(QRectF(270, 50, 80, 80)), "#5e81ac")
hole.even_odd = True
d.layers[0].items.append(hole)
front = Layer("Front")
front.tag = "#ff4f6d"
d.layers.append(front)
c = PathItem(ellipse_subpaths(QRectF(60, 150, 120, 120)), "#a3be8c", None)
c.opacity = 0.5
c.clip = rect_subpaths(QRectF(60, 150, 120, 60))
front.items.append(c)
t = TextItem("Halo text", "Arial", 36, "#2e3440")
t.xf = QTransform.fromTranslate(200, 240)
front.items.append(t)
img = QImage(40, 30, QImage.Format_ARGB32_Premultiplied)
img.fill(0xffd08770)
r = RasterItem(img)
r.xf = QTransform.fromScale(2, 2) * QTransform.fromTranslate(300, 200)
front.items.append(r)
hidden = Layer("Hidden")
hidden.visible = False
hidden.items.append(PathItem(rect_subpaths(QRectF(0, 0, 50, 50)), "#000000"))
d.layers.append(hidden)

out = os.path.join(TMP, "made.ai")
export_ai(d, out)
src = pymupdf.open(out)
check(src.is_pdf and src.page_count == 1, "the .ai is a PDF-compatible file with one page per artboard")
names = sorted(v["name"] for v in src.get_ocgs().values())
check(names == ["Back", "Front", "Hidden"], "layers are written as PDF layers %s" % names)
check("Halo text" in src[0].get_text().replace("\u00a0", " "), "text is live, searchable text")
back = import_file(out)
check([l.name for l in back.layers] == ["Back", "Front", "Hidden"], "layers come back in order")
check(not [l for l in back.layers if l.name == "Hidden"][0].visible, "hidden layer stays hidden")
texts = [it for _, it in back.all_items() if isinstance(it, TextItem)]
check(len(texts) == 1 and texts[0].text == "Halo text" and texts[0].family == "Arial", "text comes back editable in Arial")
compare(d, back, "test document")
print("   file size %d KB" % (os.path.getsize(out) // 1024))

# every PDF-route fixture: import, export as .ai, import again
for f in sorted(glob.glob(os.path.join(FIX, "*.ai"))):
    doc = import_file(fixture(os.path.basename(f)))
    o = os.path.join(TMP, "rt.ai")
    export_ai(doc, o)
    again = import_file(o)
    check(len(again.artboards) == len(doc.artboards), "%s keeps %d artboard(s)" % (os.path.basename(f), len(doc.artboards)))
    compare(doc, again, os.path.basename(f), tol=3.0)
print("FAILED: %d" % len(fails) if fails else "ALL OK")
