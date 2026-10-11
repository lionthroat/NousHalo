"""Layers panel: shape names, thumbnails, eye/lock columns, drag and drop."""
from helpers import *
from PySide6.QtCore import QRectF, Qt
from PySide6.QtWidgets import QAbstractItemView

import noushalo
from halo_model import Document, Layer, PathItem, corner, ellipse_subpaths, rect_subpaths

fails = []


def check(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        fails.append(msg)


noushalo.apply_theme(app)
w = noushalo.MainWindow()
w.resize(1300, 850)
w.show()
pump()
ed = w.ed
d = Document.blank(400, 300)
L1 = d.layers[0]
L1.items += [PathItem(ellipse_subpaths(QRectF(10, 10, 80, 80)), "#bf616a"),
             PathItem(ellipse_subpaths(QRectF(110, 10, 120, 60)), "#a3be8c"),
             PathItem(rect_subpaths(QRectF(10, 120, 80, 80)), "#5e81ac"),
             PathItem(rect_subpaths(QRectF(110, 120, 140, 60)), "#ebcb8b"),
             PathItem([{"nodes": [corner(0, 250), corner(300, 260)], "closed": False}], None, "#2e3440", 3),
             PathItem([{"nodes": [corner(300, 20), corner(380, 20), corner(340, 90)], "closed": True}], "#b48ead")]
L2 = Layer("Layer 2", "#ff4f6d")
d.layers.append(L2)
ed.set_doc(d)
pump(20)
names = [it.label() for it in L1.items]
check(names == ["Circle", "Ellipse", "Square", "Rectangle", "Line", "Triangle"], "shape names %s" % names)

lp = w.layers
w._show_dock("layers")
lp.refresh()
pump(5)
tree = lp.tree
top = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
check([t.text(2) for t in top] == ["Layer 2", "Layer 1"], "layers listed top first")
l1row = top[1]
l1row.setExpanded(True)
pump(10)
lp.refresh()
pump(5)
l1row = tree.topLevelItem(1)
kids = [l1row.child(i) for i in range(l1row.childCount())]
check(all(not k.icon(2).isNull() for k in kids), "every object row has a thumbnail")
check(not l1row.icon(2).isNull(), "layer row has a thumbnail")
# the eye sits in the same unindented column for layers and objects
er_layer = tree.visualItemRect(l1row)
ix_obj = tree.indexFromItem(kids[0], 0)
ix_lay = tree.indexFromItem(l1row, 0)
r_obj, r_lay = tree.visualRect(ix_obj), tree.visualRect(ix_lay)
check(r_obj.x() == r_lay.x() and r_obj.width() >= 20, "eye column lines up and isn't cut off (%s vs %s)" % (r_obj, r_lay))
name_obj, name_lay = tree.visualRect(tree.indexFromItem(kids[0], 2)), tree.visualRect(tree.indexFromItem(l1row, 2))
check(name_obj.x() > name_lay.x(), "objects indent in the name column")
w.grab().save(os.path.join(OUT, "layers_panel.png"))
lp.grab().save(os.path.join(OUT, "layers_panel_only.png"))

# eye click on an object hides it
it0 = L1.items[-1]
lp._clicked(kids[0], 0)
check(not ed.doc.find(it0.id)[1].visible, "eye on an object row hides it")
ed.undo()

DIV = QAbstractItemView.DropIndicatorPosition
# drag the circle onto Layer 2
lp.refresh()
pump(3)
circle = ed.doc.layers[0].items[0]
row = None
for i in range(tree.topLevelItemCount()):
    t = tree.topLevelItem(i)
    for k in range(t.childCount()):
        if t.child(k).data(2, Qt.UserRole) == circle.id:
            row = t.child(k)
lp.handle_drop([row], tree.topLevelItem(0), DIV.OnItem)
l2 = ed.doc.layer_by_id(L2.id)
check([i.id for i in l2.items] == [circle.id], "object dragged onto another layer moves there")
check(circle.id not in [i.id for i in ed.doc.layers[0].items], "and leaves its old layer")
ed.undo()
check(circle.id in [i.id for i in ed.doc.layers[0].items], "undo puts it back")
# reorder within a layer: drag the circle above the triangle (top of the stack)
lp.refresh()
pump(3)
l1row = tree.topLevelItem(1)
rows = {l1row.child(k).data(2, Qt.UserRole): l1row.child(k) for k in range(l1row.childCount())}
tri = ed.doc.layers[0].items[-1]
lp.handle_drop([rows[circle.id]], rows[tri.id], DIV.AboveItem)
check(ed.doc.layers[0].items[-1].id == circle.id, "dropping above the top object brings it to the front")
# reorder layers: drag Layer 1 above Layer 2
lp.refresh()
pump(3)
lp.handle_drop([tree.topLevelItem(1)], tree.topLevelItem(0), DIV.AboveItem)
check([l.name for l in ed.doc.layers] == ["Layer 2", "Layer 1"], "layer dragged above another moves up")
ed.dirty = False
w.close()
print("FAILED: %d" % len(fails) if fails else "ALL OK")
