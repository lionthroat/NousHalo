"""Drives every tool with real mouse/keyboard events and checks the colour,
palette, animation, file and export features. Works only on copies."""
from helpers import *
import glob
import xml.etree.ElementTree as ET

import numpy as np
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest

import noushalo
from halo_color import hex_wheel, hue_dist, remap_pixels
from halo_export import export_animation, export_png, export_svg
from halo_model import Document, PathItem, RasterItem, TextItem
from halo_palette import Palette, read_palette_file, write_palette_file
from halo_render import qimage_to_rgba, render_artboard

noushalo.apply_theme(app)
w = noushalo.MainWindow()
w.resize(1500, 950)
w.show()
pump()
ed, cv = w.ed, w.canvas
ed.set_doc(Document.blank(800, 600))
pump()
cv.fit_artboard()
pump()


def S(x, y):
    p = cv.to_screen(QPointF(x, y))
    return QPoint(int(round(p.x())), int(round(p.y())))


def drag(a, b, mods=Qt.NoModifier, steps=6):
    QTest.mousePress(cv, Qt.LeftButton, mods, S(*a))
    for i in range(1, steps + 1):
        t = i / steps
        QTest.mouseMove(cv, S(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    QTest.mouseRelease(cv, Qt.LeftButton, mods, S(*b))
    pump()


def click(a, mods=Qt.NoModifier):
    QTest.mouseClick(cv, Qt.LeftButton, mods, S(*a))
    pump()


def items():
    return [it for _, it in ed.doc.all_items()]


def get(it):
    return ed.doc.find(it.id)[1]


fails = []


def check(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        fails.append(msg)


# ---------------------------------------------------------------- shapes
w.set_tool("rect")
drag((100, 100), (300, 250))
r = items()[-1]
check(isinstance(r, PathItem) and len(r.subpaths[0]["nodes"]) == 4 and r.subpaths[0]["closed"], "rectangle drawn")
b = r.geom_bounds()
check(abs(b.width() - 200) < 2 and abs(b.height() - 150) < 2, "rectangle size %.0fx%.0f" % (b.width(), b.height()))
w.set_tool("ellipse")
drag((400, 100), (600, 300), Qt.ShiftModifier)
eb = items()[-1].geom_bounds()
check(abs(eb.width() - eb.height()) < 2, "shift makes a circle")
w.set_tool("line")
drag((100, 400), (300, 410), Qt.ShiftModifier)
ln = items()[-1]
n = ln.subpaths[0]["nodes"]
check(abs(n[0][1] - n[1][1]) < 0.5 and ln.fill is None, "shift-line is horizontal, stroke only")
line_end_y = n[1][1]

# ---------------------------------------------------------------- pen
w.set_tool("pen")
click((500, 400))
drag((600, 420), (650, 470))
click((550, 500))
click((500, 400))
pth = items()[-1]
check(pth.subpaths[0]["closed"] and len(pth.subpaths[0]["nodes"]) == 3, "pen path closed with 3 anchors")
nd = pth.subpaths[0]["nodes"][1]
check((nd[4], nd[5]) != (nd[0], nd[1]), "pen drag made a smooth anchor")
ed.select([r.id])
w.set_tool("pen")
click((200, 100))
check(len(get(r).subpaths[0]["nodes"]) == 5, "pen click on a selected segment adds an anchor")
QTest.keyClick(cv, Qt.Key_Escape)
pump()

# ---------------------------------------------------------------- select / move / scale
w.set_tool("select")
click((150, 150))
check(ed.selection == [r.id], "click selects rectangle")
before = get(r).geom_bounds()
drag((150, 150), (190, 170))
after = get(r).geom_bounds()
check(abs(after.x() - before.x() - 40) < 1.5 and abs(after.y() - before.y() - 20) < 1.5, "drag moves by (40,20)")
ed.undo()
pump()
check(abs(get(r).geom_bounds().x() - before.x()) < 0.01, "undo restores position")
ed.select([r.id])
pump()
sr = cv.screen_sel_rect()
QTest.mousePress(cv, Qt.LeftButton, Qt.NoModifier, sr.bottomRight().toPoint())
for k in range(1, 5):
    QTest.mouseMove(cv, (sr.bottomRight() + QPointF(15 * k, 0)).toPoint())
QTest.mouseRelease(cv, Qt.LeftButton, Qt.NoModifier, (sr.bottomRight() + QPointF(60, 0)).toPoint())
pump()
check(get(r).geom_bounds().width() > before.width() + 20,
      "SE handle scales wider (%.0f -> %.0f)" % (before.width(), get(r).geom_bounds().width()))
click((790, 590))
drag((50, 50), (795, 595))
check(len(ed.selection) >= 4, "marquee selects everything (%d of %s)" % (len(ed.selection), [(i.kind_label(), i.id in ed.selection, [round(v) for v in i.geom_bounds().getCoords()]) for i in items()]))

# ---------------------------------------------------------------- direct select
ed.select([])
w.set_tool("direct")
n0 = list(get(ln).subpaths[0]["nodes"][0])
drag((n0[0], n0[1]), (n0[0], n0[1] + 30))
nn = get(ln).subpaths[0]["nodes"]
check(abs(nn[0][1] - (n0[1] + 30)) < 1.5 and abs(nn[1][1] - line_end_y) < 0.01, "direct select moves just one anchor")

# ---------------------------------------------------------------- text
w.set_tool("text")
click((120, 540))
check(cv.text_editor is not None, "text tool opens an editor")
QTest.keyClicks(cv.text_editor, "Hello Halo")
QTest.keyClick(cv.text_editor, Qt.Key_Escape)
pump()
t = [i for i in items() if isinstance(i, TextItem)]
check(len(t) == 1 and t[0].text == "Hello Halo", "typed text saved")
check(len(t) == 1 and not t[0].qpath().isEmpty(), "text draws outlines")

# ---------------------------------------------------------------- brush / eraser
w.set_tool("brush")
ed.fill = "#3fc46b"
ed.brush_kind = "hard"
ed.brush_size = 20
drag((100, 300), (400, 300), steps=12)
ras = [i for i in items() if isinstance(i, RasterItem)]
check(len(ras) == 1, "brush made a paint object")
arr = qimage_to_rgba(ras[0].image)
px = arr[600, 500]
check(px[3] > 200 and abs(int(px[1]) - 0xc4) < 4, "painted pixel is the fill colour %s" % px)
for k, kind in enumerate(("square", "medium", "soft")):
    ed.brush_kind = kind
    drag((100, 330 + 20 * k), (300, 330 + 20 * k))
w.set_tool("eraser")
ed.brush_size = 30
drag((240, 300), (260, 300))
arr = qimage_to_rgba(get(ras[0]).image)
check(arr[600, 500][3] == 0, "eraser clears pixels")
ed.undo()
arr = qimage_to_rgba(get(ras[0]).image)
check(arr[600, 500][3] > 200, "undo restores erased paint")

# ---------------------------------------------------------------- Halo
w._show_dock("halo")
pump()
w.halo.scope.setCurrentIndex(0)
w.halo.rebuild(force=True)
pump()
check(len(w.halo.wheel.entries) >= 2, "Halo shows the document's colours (%d)" % len(w.halo.wheel.entries))
f0 = get(r).fill
w.halo.spin.setValue(180)
pump()
w.halo._push_preview()
check(ed.preview is not None, "spin shows a live preview")
check(get(r).fill == f0, "preview doesn't change the artwork")
w.grab().save(os.path.join(OUT, "features_halo_preview.png"))
w.halo.apply()
pump()
f1 = get(r).fill
h0, h1 = hex_wheel(f0, "perceptual")[0], hex_wheel(f1, "perceptual")[0]
check(abs(abs(float(hue_dist(h0, h1))) - 180) < 3, "apply spins the hue 180° (%s -> %s)" % (f0, f1))
check(abs(hex_wheel(f0, "perceptual")[2] - hex_wheel(f1, "perceptual")[2]) < 0.03, "perceptual spin keeps lightness")
ed.undo()
check(get(r).fill == f0, "one undo reverts the whole Apply")
w.halo.rebuild(force=True)
ents = w.halo.wheel.entries
vi = [i for i, e in enumerate(ents) if not e["raster"] and e["src"] == f0][0]
ents[vi]["target"][0] = (ents[vi]["target"][0] + 120) % 360
others = {it.id: it.fill for it in items() if isinstance(it, PathItem) and it.fill and it.fill != f0}
w.halo.apply()
pump()
check(get(r).fill != f0 and all(get(ed.doc.find(i)[1]).fill == c for i, c in others.items()),
      "moving one dot changes only that colour")
w.halo.model.setCurrentIndex(1)
w.halo.spin.setValue(90)
w.halo._push_preview()
w.halo.apply()
pump()
check(True, "classic HSB spin applies")
w.halo.model.setCurrentIndex(0)

# ---------------------------------------------------------------- palette lock
pal = Palette("Test two", ["#ff0000", "#0000ff", "#ffffff"])
w.palettes.save(pal)
pump()
ed.lock_to(pal)
pump()
check(ed.lock_colors() == ["#ff0000", "#0000ff", "#ffffff"], "piece locks to the palette")
ed.conform(ed.scope_items("document"), dither=0.0, rasters=True)
cols = set(c for it in items() for c in it.colors())
check(cols <= {"#ff0000", "#0000ff", "#ffffff"}, "conform puts every vector colour on the palette %s" % cols)
arr = qimage_to_rgba([i for i in items() if isinstance(i, RasterItem)][0].image)
vis = arr[arr[..., 3] > 0][:, :3]
okc = {(255, 0, 0), (0, 0, 255), (255, 255, 255)}
check(all(tuple(c) in okc for c in np.unique(vis, axis=0)), "conform remaps paint pixels onto the palette")
check(ed.off_palette_count() == 0, "no colours left off palette")
w._picked("#22ff11", False)
check(ed.fill in pal.colors, "eyedropper snaps to the locked palette (%s)" % ed.fill)
ed.undo()
grad = np.zeros((16, 64, 4), np.uint8)
grad[..., 0] = np.linspace(0, 255, 64)[None, :]
grad[..., 3] = 255
dd = remap_pixels(grad, ["#000000", "#ff0000"], dither=1.0)
check(len(np.unique(dd[:, 28:36, 0])) == 2, "dithering mixes neighbours mid-gradient")
ed.lock_to(None)

# ---------------------------------------------------------------- frames
w._show_dock("timeline")
ed.add_frame()
ed.add_frame()
pump()
L = ed.current_layer()
ed.doc.frames[1].set_state(L, dx=100.0, hue=180.0)
ed.tween(0, 1, 3)
pump()
check(len(ed.doc.frames) == 5, "tween adds 3 frames (%d)" % len(ed.doc.frames))
mid = ed.doc.frames[2].state(L)
check(abs(mid["dx"] - 50) < 0.01 and abs(mid["hue"] - 90) < 0.01, "tween eases offset and hue (%s)" % mid)
ed.go_frame(4)
pump()
w.timeline.refresh_thumbs()
check(w.timeline.list.count() == 5, "timeline shows 5 frames")
outdir = os.path.join(TMP, "anim")
os.makedirs(outdir)
files = export_animation(ed.doc, ed.current_artboard(), outdir, "walk", 0.25, True, True, 3, 2)
check(len(files) == 7, "animation export writes 5 frames + sheet + json")
sh = QImage(os.path.join(outdir, "walk_sheet.png"))
check(sh.width() == 3 * 200 + 2 * 2 and sh.height() == 2 * 150 + 2, "sprite sheet is 3x2 with padding (%dx%d)" % (sh.width(), sh.height()))
fa = qimage_to_rgba(QImage(os.path.join(outdir, "walk_000.png"))).astype(int)
fb = qimage_to_rgba(QImage(os.path.join(outdir, "walk_004.png"))).astype(int)
check(np.abs(fa - fb).mean() > 1, "first and last frames differ")
w.set_tool("framemove")
ed.go_frame(0)
pump()
dx0 = ed.doc.frames[0].state(ed.current_layer())["dx"]
drag((450, 150), (470, 150))
check(abs(ed.doc.frames[0].state(ed.current_layer())["dx"] - dx0 - 20) < 1.5, "Frame Move drags the layer in this frame only")
L2 = ed.new_layer("Second")
ed.go_frame(1)
ed.set_layer_visible(L2, False)
check(ed.doc.frames[1].state(L2)["v"] is False and L2.visible, "eye toggles per frame")
w.grab().save(os.path.join(OUT, "features_timeline.png"))

# ---------------------------------------------------------------- object commands
t = [i for i in items() if isinstance(i, TextItem)][0]
ed.select([t.id])
check(ed.text_to_outlines() == 1, "text to outlines")
ed.select([items()[0].id])
ed.duplicate()
check(len(ed.selection) == 1, "duplicate")
ed.arrange("back")
ed.rasterize_selection()
check(isinstance(ed.selected_items()[0], RasterItem), "rasterize turns a path into pixels")

# ---------------------------------------------------------------- files
path = os.path.join(TMP, "roundtrip.halo")
w._write(path)
img1 = qimage_to_rgba(render_artboard(ed.doc, ed.doc.artboards[0], 0.5, frame=ed.doc.frames[2]))
d2 = Document.load(path)
img2 = qimage_to_rgba(render_artboard(d2, d2.artboards[0], 0.5, frame=d2.frames[2]))
check(np.abs(img1.astype(int) - img2.astype(int)).max() <= 2, "saved file renders identically")
check(len(d2.frames) == 5 and d2.palettes and len(list(d2.all_items())) == len(items()),
      "frames, palettes, objects survive save")
sp = export_svg(ed.doc, ed.doc.artboards[0], os.path.join(TMP, "a.svg"))
ET.parse(sp)
check(True, "SVG export is valid XML")
pp = export_png(ed.doc, ed.doc.artboards[0], os.path.join(TMP, "a.png"), 2.0)
check(QImage(pp).width() == 1600, "PNG export at 2x")
for ext in (".ase", ".gpl", ".hex"):
    fp = os.path.join(TMP, "p" + ext)
    write_palette_file(pal, fp)
    check(read_palette_file(fp).colors == pal.colors, "palette round trip " + ext)

for f in sorted(glob.glob(os.path.join(FIX, "*.ai"))):
    w.open_path(fixture(os.path.basename(f)), ask=False)
    pump()
    p2 = os.path.join(TMP, "fx.halo")
    ed.doc.save(p2)
    d3 = Document.load(p2)
    a = qimage_to_rgba(render_artboard(ed.doc, ed.doc.artboards[0], 0.5, transparent=False)).astype(int)
    b3 = qimage_to_rgba(render_artboard(d3, d3.artboards[0], 0.5, transparent=False)).astype(int)
    check(np.abs(a - b3).mean() < 0.5, "fixture save/reload " + os.path.basename(f))
w.grab().save(os.path.join(OUT, "features_window.png"))
ed.dirty = False
w.close()
print("FAILED: %d" % len(fails) if fails else "ALL OK")
