"""Image Trace on a made-up scan: shapes, holes, thin diagonals, specks."""
from helpers import *
import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QTransform

import noushalo
import halo_trace as ht
from halo_model import Document, GroupItem, PathItem, RasterItem, nodes_to_qpath
from halo_render import qimage_to_rgba, render_artboard
from halo_tracedlg import TraceDialog

fails = []


def check(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        fails.append(msg)


# a fake pencil scan: grey ink on off-white paper with grain and dust
W, H = 1200, 900
img = QImage(W, H, QImage.Format_ARGB32)
img.fill(QColor("#f1eee8"))
p = QPainter(img)
p.setRenderHint(QPainter.Antialiasing)
p.setBrush(QColor("#3a3a3a"))
p.setPen(Qt.NoPen)
p.drawEllipse(QRectF(80, 80, 360, 360))
p.setBrush(QColor("#f1eee8"))
p.drawEllipse(QRectF(170, 170, 180, 180))         # a hole
p.setPen(QPen(QColor("#3a3a3a"), 5))
p.drawLine(QPointF(520, 100), QPointF(1100, 820))  # a thin diagonal
p.setPen(QColor("#3a3a3a"))
f = QFont("Arial")
f.setPixelSize(160)
f.setBold(True)
p.setFont(f)
p.drawText(QPointF(560, 760), "Ao")
p.end()
arr = qimage_to_rgba(img).astype(np.int16)
rng = np.random.default_rng(3)
arr[..., :3] += rng.normal(0, 12, arr[..., :3].shape).astype(np.int16)
for _ in range(60):  # dust specks
    y, x = rng.integers(10, H - 10), rng.integers(10, W - 10)
    arr[y:y + 2, x:x + 2, :3] = 40
scan = np.clip(arr, 0, 255).astype(np.uint8)
ink_mask = qimage_to_rgba(img)[..., 0] < 128

shapes, thr = ht.trace(scan, ht.TraceOptions())
holes = [s for s in shapes if len(s) > 1]
check(60 < thr < 230, "automatic threshold sits between ink and paper (%d)" % thr)
check(len(holes) >= 2, "the ring and the letters keep their holes (%d shapes with holes)" % len(holes))
check(len(shapes) <= 6, "dust specks are ignored (%d shapes)" % len(shapes))

o = ht.TraceOptions()
o.speck = 0
check(len(ht.trace(scan, o)[0]) > len(shapes) + 30, "with speck size 0, the dust comes through")

# put it in a document as a placed image at half scale, offset
noushalo.apply_theme(app)
w = noushalo.MainWindow()
w.resize(1300, 850)
w.show()
pump()
ed = w.ed
ed.set_doc(Document.blank(800, 600))
r = RasterItem(qimage_to_rgba_img := img)
from halo_render import rgba_to_qimage
r.image = rgba_to_qimage(scan)
r.xf = QTransform.fromScale(0.5, 0.5) * QTransform.fromTranslate(100, 80)
r.name = "scan.png"
ed.add_item(r)
pump()
dlg = TraceDialog(ed, r, w.color_choices, w)
dlg.resize(1100, 680)
dlg.show()
pump(5)
check(len(dlg.shapes) >= 3, "the dialog previews the trace (%d shapes)" % len(dlg.shapes))
dlg.grab().save(os.path.join(OUT, "trace_dialog.png"))
dlg.fill.set_color("#5e81ac")
g = dlg.run()
dlg.close()
pump()
check(isinstance(g, GroupItem) and len(g.children) >= 3 and g.name == "Traced scan.png", "the trace lands as a named group")
check(all(isinstance(c, PathItem) and c.fill == "#5e81ac" and c.even_odd for c in g.children), "shapes use the chosen fill, holes cut out")
_, cont, _ = ed.doc.locate(r.id)
check(cont.index(g) == cont.index(r) + 1 and not r.visible, "it sits just above the image, which is hidden")
gb = g.geom_bounds()
check(abs(gb.left() - (100 + 80 * 0.5)) < 2 and abs(gb.top() - (80 + 80 * 0.5)) < 2,
      "shapes are placed where the image is (%.1f, %.1f)" % (gb.left(), gb.top()))
# coverage: the traced shapes cover the same ink as the drawing
doc_img = render_artboard(ed.doc, ed.doc.artboards[0], 2.0, transparent=False)
traced = qimage_to_rgba(doc_img)[..., 2] > 150  # blue-ish fill over white
traced &= qimage_to_rgba(doc_img)[..., 0] < 150
ref = np.zeros_like(traced)
ys, xs = np.nonzero(ink_mask)
X = ((100 + xs * 0.5) * 2).astype(int)
Y = ((80 + ys * 0.5) * 2).astype(int)
ok = (X < ref.shape[1]) & (Y < ref.shape[0])
ref[Y[ok], X[ok]] = True
iou = (traced & ref).sum() / max(1, (traced | ref).sum())
check(iou > 0.93, "traced shapes match the ink (overlap %.3f)" % iou)
w.grab().save(os.path.join(OUT, "trace_window.png"))
ed.undo()
check(ed.doc.find(g.id)[1] is None and ed.doc.find(r.id)[1].visible, "one undo takes the whole trace back")
ed.dirty = False
w.close()
print("FAILED: %d" % len(fails) if fails else "ALL OK")
