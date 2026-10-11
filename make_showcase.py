"""Artwork for the website screenshot and the CI sample file, drawn in code.

  python make_showcase.py --ci-sample    writes tests/ci-sample.ai
  python make_showcase.py --screenshot   opens Nous Halo with it and saves
                                         site/public/screenshot.png

A dusk landscape, coloured from a palette it's locked to, with layers,
groups and a few animation frames, so the screenshot shows the Halo wheel,
the Layers panel and the Timeline doing their thing.
"""
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

DUSK = ["#2b1d4a", "#4a2a6b", "#7a3b7f", "#b4517f", "#e07a6f", "#f4a96a", "#ffd9a0",
        "#5b3a75", "#3a2556", "#22183a", "#2f1f4a", "#3b2c62"]


def smooth(points, closed=True, k=0.5):
    """Catmull-Rom through points -> node list with Bezier handles."""
    n = len(points)
    nodes = []
    for i, (x, y) in enumerate(points):
        if not closed and (i == 0 or i == n - 1):
            nodes.append([x, y, x, y, x, y])
            continue
        px, py = points[(i - 1) % n]
        nx, ny = points[(i + 1) % n]
        tx, ty = (nx - px) * k / 3, (ny - py) * k / 3
        nodes.append([x, y, x - tx, y - ty, x + tx, y + ty])
    return {"nodes": nodes, "closed": closed}


def ridge(points, bottom):
    """A smooth skyline over the points, closed straight down to `bottom`
    at the artboard's edges."""
    sp = smooth(points, closed=False)
    (x0, _), (x1, _) = points[0], points[-1]
    sp["nodes"] += [[x1, bottom, x1, bottom, x1, bottom], [x0, bottom, x0, bottom, x0, bottom]]
    sp["closed"] = True
    return sp


def build():
    from PySide6.QtCore import QRectF
    from halo_model import (Document, Frame, GroupItem, Layer, PathItem, corner,
                            ellipse_subpaths, rect_subpaths)
    from halo_palette import Palette
    W, H = 960, 600
    d = Document.blank(W, H)
    d.artboards[0].name = "Dusk"
    sky = d.layers[0]
    sky.name = "Sky"
    bands = [(0, "#2b1d4a"), (95, "#4a2a6b"), (180, "#7a3b7f"), (255, "#b4517f"), (318, "#e07a6f"), (362, "#f4a96a")]
    for i, (y, col) in enumerate(bands):
        y2 = bands[i + 1][0] if i + 1 < len(bands) else 400
        p = PathItem(rect_subpaths(QRectF(0, y, W, y2 - y + 1)), col)
        p.name = "Sky band %d" % (i + 1)
        sky.items.append(p)

    sun_l = Layer("Sun", "#ffaa33")
    d.layers.append(sun_l)
    glow = []
    for r, op in ((150, 0.12), (105, 0.2), (72, 0.32)):
        g = PathItem(ellipse_subpaths(QRectF(620 - r, 360 - r, 2 * r, 2 * r)), "#ffd9a0")
        g.opacity = op
        glow.append(g)
    disc = PathItem(ellipse_subpaths(QRectF(575, 315, 90, 90)), "#ffd9a0")
    disc.name = "Sun disc"
    sun = GroupItem(glow + [disc], name="Sun")
    sun_l.items.append(sun)

    mt = Layer("Mountains", "#b65cff")
    d.layers.append(mt)
    far = PathItem([ridge([(0, 285), (60, 300), (150, 270), (230, 310), (330, 240), (420, 290),
                           (520, 250), (640, 330), (760, 260), (860, 300), (960, 262)], 400)], "#5b3a75")
    far.name = "Far ridge"
    near = PathItem([ridge([(0, 352), (40, 345), (120, 320), (200, 360), (300, 330), (380, 372),
                            (470, 350), (560, 395), (700, 360), (800, 330), (900, 365), (960, 348)], 410)],
                    "#3a2556")
    near.name = "Near ridge"
    mt.items.append(GroupItem([far, near], name="Ranges"))

    trees_l = Layer("Trees", "#3fc46b")
    d.layers.append(trees_l)
    trees = []
    for x, s in ((60, 1.15), (110, 0.9), (150, 1.3), (205, 0.85), (820, 1.2), (870, 0.95), (915, 1.35)):
        tiers = []
        base = 412
        for k in range(3):
            w = 34 * s * (1 - k * 0.22)
            top = base - 46 * s
            tiers.append(PathItem([{"nodes": [corner(x - w, base), corner(x, top), corner(x + w, base)],
                                    "closed": True}], "#22183a" if k % 2 == 0 else "#2f1f4a"))
            base -= 24 * s
        trunk = PathItem(rect_subpaths(QRectF(x - 4 * s, 410, 8 * s, 14 * s)), "#22183a")
        trees.append(GroupItem([trunk] + tiers, name="Pine"))
    trees_l.items.append(GroupItem(trees, name="Forest"))

    lake_l = Layer("Lake", "#2fd1d1")
    d.layers.append(lake_l)
    lake_l.items.append(PathItem(rect_subpaths(QRectF(0, 405, W, H - 405)), "#3b2c62"))
    refl = []
    for i, (w, op) in enumerate(((150, 0.75), (120, 0.6), (96, 0.5), (70, 0.4), (46, 0.32), (28, 0.25))):
        y = 425 + i * 24
        r = PathItem(rect_subpaths(QRectF(620 - w / 2, y, w, 7)), "#f4a96a" if i % 2 else "#ffd9a0")
        r.opacity = op
        refl.append(r)
    lake_l.items.append(GroupItem(refl, name="Reflection"))

    birds_l = Layer("Birds", "#ff66c4")
    d.layers.append(birds_l)
    birds = []
    for x, y, s in ((300, 150, 1.0), (340, 128, 0.8), (372, 160, 0.9), (410, 138, 0.7), (262, 176, 0.75)):
        wing = 16 * s
        p = PathItem([{"nodes": [[x - wing, y - 4 * s, x - wing, y - 4 * s, x - wing * 0.5, y - 9 * s],
                                 [x, y, x - wing * 0.25, y - 6 * s, x + wing * 0.25, y - 6 * s],
                                 [x + wing, y - 4 * s, x + wing * 0.5, y - 9 * s, x + wing, y - 4 * s]],
                       "closed": False}], None, "#22183a", 2.4 * s)
        p.cap = 1
        p.join = 1
        birds.append(p)
    birds_l.items.append(GroupItem(birds, name="Flock"))

    pal = Palette("Dusk", DUSK)
    d.palettes.append(pal)
    d.lock_palette = "Dusk"
    for k in range(5):
        f = Frame(120)
        for l in d.layers:
            f.set_state(l, v=True)
        f.set_state(birds_l, dx=k * 26.0, dy=-k * 6.0)
        f.set_state(sun_l, hue=0.0)
        d.frames.append(f)
    return d


def ci_sample():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv[:1])
    from halo_aiexport import export_ai
    out = os.path.join(HERE, "tests", "ci-sample.ai")
    export_ai(build(), out)
    print("wrote", out, os.path.getsize(out) // 1024, "KB")


def screenshot():
    """Opens a real window (so fonts look as they do for people), with its own
    settings and palette library, and saves a grab of it."""
    import tempfile
    os.environ["NOUSHALO_SETTINGS"] = "NousHaloShowcase"
    os.environ["NOUSHALO_DATA"] = tempfile.mkdtemp(prefix="noushalo_shot_")
    from PySide6.QtCore import QSettings, QTimer, Qt
    from PySide6.QtWidgets import QApplication
    QSettings("NousHalo", "NousHaloShowcase").clear()
    import noushalo
    app = QApplication.instance() or QApplication(sys.argv[:1])
    noushalo.apply_theme(app)
    w = noushalo.MainWindow()
    w.ed.set_doc(build())
    w._untitled = "Dusk.halo"
    w.palettes.reload_combo("Dusk")
    w.halo.refresh_palettes()
    w.halo.select_palette("Dusk")
    w.resize(1440, 940)
    w.show()
    w.resizeDocks([w.docks["halo"], w.docks["layers"]], [560, 300], Qt.Vertical)
    w.resizeDocks([w.docks["timeline"]], [170], Qt.Vertical)
    out = os.path.join(HERE, "site", "public", "screenshot.png")

    def stage1():
        w.canvas.fit_artboard()
        w.ed.go_frame(2)
        lp = w.layers
        for l in w.ed.doc.layers:
            lp._expanded.add(l.id)
        trees = [l for l in w.ed.doc.layers if l.name == "Trees"][0]
        lp._expanded.add(trees.items[0].id)
        w.ed.current_layer_id = trees.id
        w.ed.select([trees.items[0].children[2].id])
        w.timeline.refresh_thumbs()
        w.halo.scope.setCurrentIndex(0)
        w.halo.rebuild(force=True)
        w.update_title()
        QTimer.singleShot(1200, stage2)

    def stage2():
        w.layers.refresh()
        w.halo._update_lock_ui()
        app.processEvents()
        img = w.grab().toImage()
        img.save(out)
        print("wrote", out, img.width(), "x", img.height())
        w.ed.dirty = False
        w.close()
        app.quit()
    QTimer.singleShot(400, stage1)
    app.exec()
    QSettings("NousHalo", "NousHaloShowcase").clear()


if __name__ == "__main__":
    if "--ci-sample" in sys.argv:
        ci_sample()
    if "--screenshot" in sys.argv:
        screenshot()
