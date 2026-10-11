"""Groups: make/undo, select as a unit, isolation, direct select inside,
clipping masks, Layers panel drops, save/load and exports."""
from helpers import *
import xml.etree.ElementTree as ET

import numpy as np
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractItemView

import noushalo
from halo_aiexport import export_ai
from halo_export import export_svg
from halo_import import import_file
from halo_model import Document, GroupItem, Layer, PathItem, ellipse_subpaths, rect_subpaths
from halo_render import qimage_to_rgba, render_artboard

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
d = Document.blank(600, 400)
L = d.layers[0]
a = PathItem(rect_subpaths(QRectF(50, 50, 100, 100)), "#bf616a")
b = PathItem(ellipse_subpaths(QRectF(200, 50, 100, 100)), "#a3be8c")
c = PathItem(rect_subpaths(QRectF(350, 50, 100, 100)), "#5e81ac")
L.items += [a, b, c]
ed.set_doc(d)
pump()
cv.fit_artboard()
pump()


def S(x, y):
    p = cv.to_screen(QPointF(x, y))
    return QPoint(int(round(p.x())), int(round(p.y())))


def click(x, y, mods=Qt.NoModifier):
    QTest.mouseClick(cv, Qt.LeftButton, mods, S(x, y))
    pump()


def dclick(x, y):
    QTest.mouseDClick(cv, Qt.LeftButton, Qt.NoModifier, S(x, y))
    pump()


def drag(p0, p1):
    QTest.mousePress(cv, Qt.LeftButton, Qt.NoModifier, S(*p0))
    for k in range(1, 6):
        t = k / 5
        QTest.mouseMove(cv, S(p0[0] + (p1[0] - p0[0]) * t, p0[1] + (p1[1] - p0[1]) * t))
    QTest.mouseRelease(cv, Qt.LeftButton, Qt.NoModifier, S(*p1))
    pump()


before = qimage_to_rgba(render_artboard(ed.doc, ed.doc.artboards[0])).astype(int)
ed.select([a.id, b.id])
g = ed.group_selection()
check(isinstance(g, GroupItem) and [x.id for x in g.children] == [a.id, b.id], "Ctrl+G groups in stacking order")
check([x.id for x in L.items] == [g.id, c.id], "the group sits where the top object was")
after = qimage_to_rgba(render_artboard(ed.doc, ed.doc.artboards[0])).astype(int)
check(np.abs(before - after).max() == 0, "grouping doesn't change the look")
check(g.label() == "Group (2)", "group named in the Layers panel: %s" % g.label())

w.set_tool("select")
ed.select([])
click(100, 100)
check(ed.selection == [g.id], "a click on a member selects the whole group")
drag((100, 100), (100, 130))
check(abs(ed.doc.find(a.id)[1].geom_bounds().y() - 80) < 1.5 and abs(ed.doc.find(b.id)[1].geom_bounds().y() - 80) < 1.5,
      "dragging moves every member")
ed.undo()

dclick(250, 100)
check(ed.isolation == g.id, "double-click enters the group")
check(ed.selection == [b.id], "and picks the member under the cursor")
w.grab().save(os.path.join(OUT, "groups_isolation.png"))
click(100, 100)
check(ed.selection == [a.id], "inside the group, clicks pick members")
w.set_tool("rect")
drag((60, 250), (120, 300))
newr = ed.doc.find(ed.selection[0])[1]
check(ed.doc.parent_of(newr.id) is ed.doc.find(g.id)[1], "drawing inside the group adds to the group")
w.set_tool("select")
QTest.keyClick(cv, Qt.Key_Escape)
pump()
check(ed.isolation is None and ed.selection == [g.id], "Esc leaves the group and selects it")

# direct select reaches inside without entering
ed.select([])
w.set_tool("direct")
click(150, 50)  # a corner of the red square
check(ed.selection == [a.id], "Direct Selection picks a path inside a group")

# fill applies to every member of a selected group
w.set_tool("select")
ed.select([g.id])
ed.set_fill("#ebcb8b")
check(all(x.fill == "#ebcb8b" for x in ed.doc.find(g.id)[1].leaves()), "fill colour applies to everything in the group")
ed.undo()

# ungroup
n_before = len(L.items)
ed.select([g.id])
ed.ungroup_selection()
L = ed.doc.layers[0]
check(not any(isinstance(x, GroupItem) for x in L.items) and len(L.items) == n_before + 2,
      "Ctrl+Shift+G ungroups in place")
ed.undo()
L = ed.doc.layers[0]
check(any(isinstance(x, GroupItem) for x in L.items), "undo regroups")

# clipping mask: the circle-ish c rect clips a new big rect
big = PathItem(rect_subpaths(QRectF(300, 20, 250, 250)), "#d08770")
ed.add_item(big)
mask = PathItem(ellipse_subpaths(QRectF(350, 50, 120, 120)), None, "#000000")
ed.add_item(mask)
ed.select([big.id, mask.id])
cg = ed.make_clipping_mask()
check(cg is not None and cg.clip and len(cg.children) == 1, "Ctrl+7 makes a clipping group")
img = qimage_to_rgba(render_artboard(ed.doc, ed.doc.artboards[0])).astype(int)
check(img[110, 410][3] > 200 and img[260, 540][3] == 0, "the mask clips (inside shows, outside hidden)")
ed.select([cg.id])
ed.release_clipping_mask()
img = qimage_to_rgba(render_artboard(ed.doc, ed.doc.artboards[0])).astype(int)
check(img[260, 540][3] > 200, "Ctrl+Alt+7 releases it")
ed.undo()

# Layers panel: drop an object onto a group row
lp = w.layers
lp.refresh()
pump()
tree = lp.tree
DIV = QAbstractItemView.DropIndicatorPosition


def row_for(oid):
    stack = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
    while stack:
        t = stack.pop()
        if t.data(2, Qt.UserRole) == oid:
            return t
        stack += [t.child(k) for k in range(t.childCount())]
lp._expanded.add(ed.doc.layers[0].id)
lp.refresh()
pump()
gid = [x for x in ed.doc.layers[0].items if isinstance(x, GroupItem) and not x.clip][0].id
lp.handle_drop([row_for(c.id)], row_for(gid), DIV.OnItem)
gg = ed.doc.find(gid)[1]
check(c.id in [x.id for x in gg.children], "dropping an object onto a group row puts it inside")
lp._expanded.add(gid)
lp.refresh()
pump()
check(row_for(c.id) is not None and row_for(c.id).parent() is row_for(gid), "the panel shows it nested")
lp.grab().save(os.path.join(OUT, "groups_layers_panel.png"))
lp.handle_drop([row_for(c.id)], row_for(ed.doc.layers[0].id), DIV.OnItem)
check(c.id in [x.id for x in ed.doc.layers[0].items], "dragging it onto the layer row takes it back out")

# save / load / exports keep groups
path = os.path.join(TMP, "groups.halo")
ed.doc.save(path)
d2 = Document.load(path)
kinds = [type(x).__name__ for x in d2.layers[0].items]
check("GroupItem" in kinds, "groups survive save and load %s" % kinds)
r1 = qimage_to_rgba(render_artboard(ed.doc, ed.doc.artboards[0])).astype(int)
r2 = qimage_to_rgba(render_artboard(d2, d2.artboards[0])).astype(int)
check(np.abs(r1 - r2).max() <= 1, "and look the same")
sv = export_svg(ed.doc, ed.doc.artboards[0], os.path.join(TMP, "g.svg"))
root = ET.parse(sv).getroot()
nested = [e for e in root.iter("{http://www.w3.org/2000/svg}g") if e.find("{http://www.w3.org/2000/svg}g") is not None]
check(len(nested) >= 1, "SVG export nests groups")
ai = export_ai(ed.doc, os.path.join(TMP, "g.ai"))
back = import_file(ai)
r3 = qimage_to_rgba(render_artboard(back, back.artboards[0])).astype(int)
check(np.abs(r1[..., :3] - r3[..., :3]).mean() < 1.5, ".ai export of grouped art looks the same")

# a real file: groups from the native data, same pixels
best = 0
src = None
for name in fixtures("pdf"):
    path = fixture(name)
    n = sum(1 for _, _, x in import_file(path).walk() if isinstance(x, GroupItem))
    if n > best:
        best, src = n, path
check(best >= 1, "a real .ai comes in with its groups (%d)" % best)
if src:
    w.open_path(src, ask=False)
pump(5)
w.layers.refresh()
pump()
w.grab().save(os.path.join(OUT, "groups_coffee.png"))
ed.dirty = False
w.close()
print("FAILED: %d" % len(fails) if fails else "ALL OK")
