"""Rulers, guides and snapping, driven with real mouse events."""
from helpers import *
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtTest import QTest

import noushalo
from halo_canvas import RULER
from halo_model import Document, PathItem, rect_subpaths

fails = []


def check(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        fails.append(msg)


noushalo.apply_theme(app)
w = noushalo.MainWindow()
w.resize(1400, 900)
w.show()
pump()
ed, cv = w.ed, w.canvas
ed.set_doc(Document.blank(800, 600))
pump()
w.view_toggles["show_rulers"].setChecked(True)
w.view_toggles["snap_on"].setChecked(True)
w.view_toggles["show_guides"].setChecked(True)
cv.fit_artboard()
pump()


def S(x, y):
    p = cv.to_screen(QPointF(x, y))
    return QPoint(int(round(p.x())), int(round(p.y())))


def drag_screen(a, b, steps=6):
    QTest.mousePress(cv, Qt.LeftButton, Qt.NoModifier, a)
    for k in range(1, steps + 1):
        t = k / steps
        QTest.mouseMove(cv, QPoint(int(a.x() + (b.x() - a.x()) * t), int(a.y() + (b.y() - a.y()) * t)))
    QTest.mouseRelease(cv, Qt.LeftButton, Qt.NoModifier, b)
    pump()


def drag(p0, p1):
    drag_screen(S(*p0), S(*p1))


# guides out of the rulers
w.set_tool("select")
drag_screen(QPoint(400, 8), S(300, 200))
check(len(ed.doc.guides) == 1 and ed.doc.guides[0]["o"] == "h" and abs(ed.doc.guides[0]["pos"] - 200) <= 1,
      "dragging from the top ruler makes a horizontal guide at y=200 (%s)" % ed.doc.guides)
drag_screen(QPoint(8, 400), S(250, 300))
check(len(ed.doc.guides) == 2 and ed.doc.guides[1]["o"] == "v" and abs(ed.doc.guides[1]["pos"] - 250) <= 1,
      "dragging from the left ruler makes a vertical guide at x=250")
check(ed.doc.guides[0]["pos"] == round(ed.doc.guides[0]["pos"]), "guides land on whole units")
w.grab().save(os.path.join(OUT, "guides_window.png"))
ed.undo()
check(len(ed.doc.guides) == 1, "undo removes the last guide")
ed.redo()

# move a guide, then drag it back into the ruler to delete it
gy = ed.doc.guides[0]["pos"]
drag((600, gy), (600, 260))
check(abs(ed.doc.guides[0]["pos"] - 260) <= 1, "a guide can be dragged to a new place")
sp = S(600, 260)
drag_screen(sp, QPoint(sp.x(), 5))
check(len(ed.doc.guides) == 1 and ed.doc.guides[0]["o"] == "v", "dragging it back onto the ruler deletes it")

# snapping: draw a rectangle starting 3 units off the vertical guide at x=250
w.set_tool("rect")
drag((253, 100), (350, 180))
r = ed.selected_items()[0]
check(abs(r.geom_bounds().left() - 250) < 0.01, "a rectangle's corner snaps to the guide (left %.2f)" % r.geom_bounds().left())

# moving: drag the rectangle so its right edge comes near the guide
w.set_tool("select")
b0 = r.geom_bounds()
grab = (b0.center().x(), b0.center().y())
drag(grab, (grab[0] - 102.5, grab[1]))
b1 = ed.doc.find(r.id)[1].geom_bounds()
check(abs(b1.right() - 250) < 0.01, "moving snaps the object's edge to the guide (right %.2f)" % b1.right())

# artboard edges and centre snap too
drag((b1.center().x(), b1.center().y()), (403.0, b1.center().y() + 60))
b2 = ed.doc.find(r.id)[1].geom_bounds()
check(abs(b2.center().x() - 400) < 0.01,
      "objects snap to the artboard's centre line (%.2f..%.2f)" % (b2.left(), b2.right()))

# direct selection: drag one anchor near the guide
w.set_tool("direct")
ed.select([])
n = ed.doc.find(r.id)[1].subpaths[0]["nodes"][0]
drag((n[0], n[1]), (252, n[1] + 40))
n2 = ed.doc.find(r.id)[1].subpaths[0]["nodes"][0]
check(abs(n2[0] - 250) < 0.01, "a dragged anchor snaps to the guide (x %.2f)" % n2[0])

# pen anchors snap
w.set_tool("pen")
QTest.mouseClick(cv, Qt.LeftButton, Qt.NoModifier, S(248, 450))
pump()
QTest.mouseClick(cv, Qt.LeftButton, Qt.NoModifier, S(500, 455))
pump()
QTest.keyClick(cv, Qt.Key_Return)
pump()
pen = [it for _, it in ed.doc.all_items()][-1]
check(abs(pen.subpaths[0]["nodes"][0][0] - 250) < 0.01, "pen anchors snap to guides")

# snapping off
w.view_toggles["snap_on"].setChecked(False)
w.set_tool("rect")
drag((253, 500), (300, 560))
r2 = ed.selected_items()[0]
check(abs(r2.geom_bounds().left() - 253) <= 1, "with snapping off, nothing snaps (left %.2f)" % r2.geom_bounds().left())
w.view_toggles["snap_on"].setChecked(True)

# locked guides can't be dragged
w.set_tool("select")
w.view_toggles["lock_guides"].setChecked(True)
ed.select([])
drag((250, 300), (320, 300))
check(abs(ed.doc.guides[0]["pos"] - 250) < 0.01, "locked guides stay put")
w.view_toggles["lock_guides"].setChecked(False)

# guides save with the document
path = os.path.join(TMP, "guides.halo")
ed.doc.save(path)
d2 = Document.load(path)
check(d2.guides == ed.doc.guides, "guides are saved in the .halo")
w.grab().save(os.path.join(OUT, "guides_window2.png"))
ed.dirty = False
w.close()
print("FAILED: %d" % len(fails) if fails else "ALL OK")
