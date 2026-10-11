"""The drawing canvas and its tools."""
import math

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QCursor, QFont, QImage, QKeySequence, QPainter,
                           QPainterPath, QPen, QRadialGradient, QTextCursor, QTransform)
from PySide6.QtWidgets import QPlainTextEdit, QWidget

from halo_model import (GroupItem, PathItem, RasterItem, TextItem, Artboard, corner, ellipse_subpaths,
                        leaf_items, rect_subpaths)
from halo_render import RenderOptions, draw_item, render_layers

PASTEBOARD = QColor("#2b2f38")
RULER = 18          # ruler thickness, screen pixels
GUIDE_PICK = 4      # how close a click must be to grab a guide, screen pixels
SNAP_PX = 7         # snapping reach, screen pixels
GUIDE_COLOR = QColor("#2fd1d1")
HANDLE = 7          # handle size, screen pixels
PICK = 5            # pick tolerance, screen pixels


def _lerp(a, b, t):
    return QPointF(a.x() + (b.x() - a.x()) * t, a.y() + (b.y() - a.y()) * t)


def split_cubic(p0, p1, p2, p3, t):
    a, b, c = _lerp(p0, p1, t), _lerp(p1, p2, t), _lerp(p2, p3, t)
    d, e = _lerp(a, b, t), _lerp(b, c, t)
    f = _lerp(d, e, t)
    return (p0, a, d, f), (f, e, c, p3)


def segments(sp):
    nodes = sp["nodes"]
    out = [(i, i + 1) for i in range(len(nodes) - 1)]
    if sp.get("closed") and len(nodes) > 1:
        out.append((len(nodes) - 1, 0))
    return out


def seg_points(nodes, i, j):
    a, b = nodes[i], nodes[j]
    return (QPointF(a[0], a[1]), QPointF(a[4], a[5]), QPointF(b[2], b[3]), QPointF(b[0], b[1]))


def bez(p0, p1, p2, p3, t):
    u = 1 - t
    return QPointF(u ** 3 * p0.x() + 3 * u * u * t * p1.x() + 3 * u * t * t * p2.x() + t ** 3 * p3.x(),
                   u ** 3 * p0.y() + 3 * u * u * t * p1.y() + 3 * u * t * t * p2.y() + t ** 3 * p3.y())


def nearest_on_path(item, p, tol):
    """(subpath index, segment start, segment end, t, distance) nearest to p."""
    best = None
    for si, sp in enumerate(item.subpaths):
        for i, j in segments(sp):
            pts = seg_points(sp["nodes"], i, j)
            for k in range(0, 41):
                t = k / 40
                q = bez(*pts, t)
                d = math.hypot(q.x() - p.x(), q.y() - p.y())
                if best is None or d < best[4]:
                    best = (si, i, j, t, d)
    if best is None:
        return None
    # refine
    si, i, j, t, d = best
    pts = seg_points(item.subpaths[si]["nodes"], i, j)
    step = 1 / 40
    for _ in range(12):
        step /= 2
        for tt in (t - step, t + step):
            if 0 <= tt <= 1:
                q = bez(*pts, tt)
                dd = math.hypot(q.x() - p.x(), q.y() - p.y())
                if dd < d:
                    t, d = tt, dd
    return (si, i, j, t, d) if d <= tol else None


class Canvas(QWidget):
    status = Signal(str)
    zoomChanged = Signal(float)
    colorPicked = Signal(str, bool)  # hex, is_stroke
    toolRequested = Signal(str)

    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self.ed = editor
        self.zoom = 1.0
        self.pan = QPointF(40, 40)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self.setMinimumSize(200, 200)
        self._cache = None
        self._cache_key = None
        self.live = None          # (ids, QTransform) while dragging a selection
        self.split = None         # brush stroke: dict(item, layer, below, above)
        self.overlay_path = None  # rubber bands, previews (document coords)
        self.marquee = None
        self.space_down = False
        self._pan_from = None
        self.tools = {
            "select": SelectTool(self), "direct": DirectTool(self), "pen": PenTool(self),
            "rect": ShapeTool(self, "rect"), "ellipse": ShapeTool(self, "ellipse"),
            "line": ShapeTool(self, "line"), "text": TextTool(self), "brush": BrushTool(self),
            "eraser": BrushTool(self, erase=True), "eyedropper": EyedropperTool(self),
            "hand": HandTool(self), "zoom": ZoomTool(self), "framemove": FrameMoveTool(self),
            "artboard": ArtboardTool(self),
        }
        self.tool = self.tools["select"]
        self.text_editor = None
        self.guide_drag = None    # {"o", "pos", "index"} while dragging a guide
        self.snap_marks = []      # lines a drag just snapped to (drawn briefly)
        self.mouse_screen = None
        self.pressure = 1.0
        self._tablet = False
        editor.changed.connect(self._on_change)

    # ------------------------------------------------------------ view
    def to_doc(self, p):
        return QPointF((p.x() - self.pan.x()) / self.zoom, (p.y() - self.pan.y()) / self.zoom)

    def to_screen(self, p):
        return QPointF(p.x() * self.zoom + self.pan.x(), p.y() * self.zoom + self.pan.y())

    def view_xf(self):
        return QTransform(self.zoom, 0, 0, self.zoom, self.pan.x(), self.pan.y())

    def tol(self):
        return PICK / self.zoom

    def set_zoom(self, z, anchor=None):
        z = max(0.02, min(64.0, z))
        if anchor is None:
            anchor = QPointF(self.width() / 2, self.height() / 2)
        d = self.to_doc(anchor)
        self.zoom = z
        self.pan = QPointF(anchor.x() - d.x() * z, anchor.y() - d.y() * z)
        self.invalidate()
        self.zoomChanged.emit(z)
        if self.text_editor:
            self.text_editor.reposition()

    def fit_rect(self, r, margin=40):
        if r.isEmpty() or self.width() < 10:
            return
        z = min((self.width() - 2 * margin) / r.width(), (self.height() - 2 * margin) / r.height())
        self.zoom = max(0.02, min(64.0, z))
        c = r.center()
        self.pan = QPointF(self.width() / 2 - c.x() * self.zoom, self.height() / 2 - c.y() * self.zoom)
        self.invalidate()
        self.zoomChanged.emit(self.zoom)

    def fit_artboard(self):
        self.fit_rect(self.ed.current_artboard().rect)

    def fit_all(self):
        self.fit_rect(self.ed.doc.bounds())

    def invalidate(self):
        self._cache_key = None
        self.update()

    def _on_change(self, kind):
        if kind in ("doc", "frame", "preview", "file", "layers"):
            self.invalidate()
        else:
            self.update()

    def set_tool(self, name):
        if self.tool is self.tools.get(name):
            return
        self.tool.deactivate()
        self.tool = self.tools[name]
        self.tool.activate()
        self.setCursor(self.tool.cursor())
        self.update()

    # ------------------------------------------------------------ painting
    def render_opts(self):
        o = RenderOptions()
        o.frame = self.ed.frame()
        o.recolor = self.ed.preview
        o.recolor_ids = self.ed.preview_ids
        o.raster_previews = self.ed.raster_previews
        if self.text_editor:
            o.hidden_ids = {self.text_editor.item.id}
        o.isolate = self.ed.isolation
        return o

    def _scene(self, exclude=frozenset()):
        dpr = self.devicePixelRatioF()
        key = (self.ed.version, self.zoom, self.pan.x(), self.pan.y(), self.width(), self.height(),
               dpr, exclude, self.ed.frame_index, self.ed.onion, id(self.ed.preview),
               len(self.ed.raster_previews), self.text_editor is not None,
               id(self.split["item"]) if self.split else None)
        if self._cache is not None and self._cache_key == key:
            return self._cache
        img = QImage(int(self.width() * dpr), int(self.height() * dpr), QImage.Format_ARGB32_Premultiplied)
        img.setDevicePixelRatio(dpr)
        img.fill(PASTEBOARD)
        p = QPainter(img)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform | QPainter.TextAntialiasing)
        p.setTransform(self.view_xf())
        doc = self.ed.doc
        for ab in doc.artboards:
            r = ab.rect
            p.fillRect(r.translated(3 / self.zoom, 3 / self.zoom), QColor(0, 0, 0, 90))
            p.fillRect(r, QColor(ab.background) if ab.background else Qt.white)
        o = self.render_opts()
        o.hidden_ids = set(o.hidden_ids) | set(exclude)
        if self.ed.onion and doc.frames and self.ed.frame_index is not None:
            for k, alpha in ((-1, 0.28), (1, 0.14)):
                j = self.ed.frame_index + k
                if 0 <= j < len(doc.frames):
                    oo = RenderOptions()
                    oo.frame = doc.frames[j]
                    p.save()
                    p.setOpacity(alpha)
                    render_layers(p, doc, oo)
                    p.restore()
        if self.split:
            self._render_split(p, o)
        else:
            render_layers(p, doc, o)
        p.end()
        self._cache, self._cache_key = img, key
        return img

    def _render_split(self, p, o):
        """Brush stroke in progress: everything below the painted image and
        everything above it are cached; the image itself is drawn live."""
        target = self.split["item"]
        if "below" not in self.split:
            dpr = self.devicePixelRatioF()
            below = QImage(int(self.width() * dpr), int(self.height() * dpr), QImage.Format_ARGB32_Premultiplied)
            below.setDevicePixelRatio(dpr)
            above = QImage(below.size(), QImage.Format_ARGB32_Premultiplied)
            above.setDevicePixelRatio(dpr)
            below.fill(Qt.transparent)
            above.fill(Qt.transparent)
            ids_below, ids_above, seen = set(), set(), False
            for l in self.ed.doc.layers:
                for it in leaf_items(l.items):
                    if it is target:
                        seen = True
                    elif seen:
                        ids_above.add(it.id)
                    else:
                        ids_below.add(it.id)
            for img, hide in ((below, ids_above | {target.id}), (above, ids_below | {target.id})):
                q = QPainter(img)
                q.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
                q.setTransform(self.view_xf())
                oo = self.render_opts()
                oo.hidden_ids = set(o.hidden_ids) | hide
                render_layers(q, self.ed.doc, oo)
                q.end()
            self.split["below"], self.split["above"] = below, above
        p.save()
        p.resetTransform()
        p.drawImage(0, 0, self.split["below"])
        p.restore()

    def paintEvent(self, ev):
        p = QPainter(self)
        live_ids = frozenset(self.live[0]) if self.live else frozenset()
        scene = self._scene(live_ids)
        p.drawImage(0, 0, scene)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        if self.split:
            p.save()
            p.setTransform(self.view_xf())
            layer = self.split["layer"]
            off = self.ed.layer_offset(layer)
            p.translate(off)
            p.setOpacity(layer.opacity)
            draw_item(p, self.split["item"])
            p.restore()
            p.drawImage(0, 0, self.split["above"])
        if self.live:
            ids, t = self.live
            p.save()
            p.setTransform(t * self.view_xf())
            for i in ids:
                l, it = self.ed.doc.find(i)
                if it is not None:
                    p.save()
                    p.translate(self.ed.layer_offset(l))
                    draw_item(p, it)
                    p.restore()
            p.restore()
        self._paint_artboard_labels(p)
        self._paint_guides(p)
        self._paint_isolation_bar(p)
        self._paint_selection(p)
        self.tool.paint_overlay(p)
        self._paint_rulers(p)
        if self.marquee is not None:
            r = QRectF(self.to_screen(self.marquee.topLeft()), self.to_screen(self.marquee.bottomRight()))
            p.setPen(QPen(QColor("#88c0d0"), 1, Qt.DashLine))
            p.setBrush(QColor(136, 192, 208, 30))
            p.drawRect(r.normalized())
        p.end()

    def _paint_artboard_labels(self, p):
        f = QFont(self.font())
        f.setPointSizeF(8.5)
        p.setFont(f)
        cur = self.ed.current_artboard()
        for ab in self.ed.doc.artboards:
            tl = self.to_screen(ab.rect.topLeft())
            p.setPen(QColor("#eceff4") if ab is cur else QColor("#9aa3b5"))
            p.drawText(QPointF(tl.x(), tl.y() - 5), ab.name)
            if ab is cur and len(self.ed.doc.artboards) > 1:
                p.setPen(QPen(QColor("#88c0d0"), 1))
                p.setBrush(Qt.NoBrush)
                p.drawRect(QRectF(tl, self.to_screen(ab.rect.bottomRight())).adjusted(-1, -1, 1, 1))

    def _paint_isolation_bar(self, p):
        g = self.ed.isolated_group()
        if g is None:
            return
        trail = []
        node = g
        while node is not None:
            trail.append(node.label())
            node = self.ed.doc.parent_of(node.id)
        l, _ = self.ed.doc.find(g.id)
        trail.append(l.name if l else "")
        text = "  ›  ".join(reversed(trail)) + "      (Esc or double-click outside to leave)"
        f = QFont(self.font())
        f.setPointSizeF(9)
        p.save()
        p.setFont(f)
        r = QRectF(0, 0, self.width(), 24)
        p.fillRect(r, QColor(59, 66, 82, 235))
        p.setPen(QColor("#88c0d0"))
        p.drawText(r.adjusted(10, 0, -10, 0), Qt.AlignVCenter | Qt.AlignLeft, "Editing group:  " + text)
        p.restore()


    # ------------------------------------------------------------ rulers, guides, snapping
    def ruler_origin(self):
        return self.ed.current_artboard().rect.topLeft()

    def in_top_ruler(self, sp):
        return self.ed.show_rulers and sp.y() < RULER and sp.x() >= RULER

    def in_left_ruler(self, sp):
        return self.ed.show_rulers and sp.x() < RULER and sp.y() >= RULER

    def guide_at(self, sp):
        """Index of the guide under screen point sp, or None."""
        if not self.ed.show_guides or self.ed.lock_guides:
            return None
        best, bd = None, GUIDE_PICK + 0.01
        for i, g in enumerate(self.ed.doc.guides):
            if g["o"] == "v":
                d = abs(self.to_screen(QPointF(g["pos"], 0)).x() - sp.x())
            else:
                d = abs(self.to_screen(QPointF(0, g["pos"])).y() - sp.y())
            if d < bd:
                best, bd = i, d
        return best

    def snap_lines(self):
        """(xs, ys) the snapping pulls toward: guides, artboard edges and centres."""
        xs, ys = [], []
        if self.ed.show_guides:
            for g in self.ed.doc.guides:
                (xs if g["o"] == "v" else ys).append(g["pos"])
        for ab in self.ed.doc.artboards:
            r = ab.rect
            xs += [r.left(), r.center().x(), r.right()]
            ys += [r.top(), r.center().y(), r.bottom()]
        return xs, ys

    def _snap_axis(self, values, lines):
        """Smallest shift (within reach) that lands one of `values` on a line."""
        reach = SNAP_PX / self.zoom
        best = None
        for v in values:
            for ln in lines:
                d = ln - v
                if abs(d) <= reach and (best is None or abs(d) < abs(best[0])):
                    best = (d, ln)
        return best

    def snap(self, p):
        """A point pulled onto nearby guides / artboard edges (if snapping is on)."""
        self.snap_marks = []
        if not self.ed.snap_on:
            return p
        xs, ys = self.snap_lines()
        x, y = p.x(), p.y()
        bx = self._snap_axis([x], xs)
        by = self._snap_axis([y], ys)
        if bx:
            x += bx[0]
            self.snap_marks.append(("v", bx[1]))
        if by:
            y += by[0]
            self.snap_marks.append(("h", by[1]))
        return QPointF(x, y)

    def snap_rect_delta(self, r, d):
        """Shift d adjusted so the moved rectangle's edges or centre snap."""
        self.snap_marks = []
        if not self.ed.snap_on or r.isNull():
            return d
        xs, ys = self.snap_lines()
        m = r.translated(d)
        bx = self._snap_axis([m.left(), m.center().x(), m.right()], xs)
        by = self._snap_axis([m.top(), m.center().y(), m.bottom()], ys)
        dx, dy = d.x(), d.y()
        if bx:
            dx += bx[0]
            self.snap_marks.append(("v", bx[1]))
        if by:
            dy += by[0]
            self.snap_marks.append(("h", by[1]))
        return QPointF(dx, dy)

    def clear_snap(self):
        if self.snap_marks:
            self.snap_marks = []
            self.update()

    def _paint_guides(self, p):
        p.save()
        p.setRenderHint(QPainter.Antialiasing, False)
        if self.ed.show_guides:
            for i, g in enumerate(self.ed.doc.guides):
                col = QColor("#ff5fd2") if self.guide_drag and self.guide_drag.get("index") == i else GUIDE_COLOR
                p.setPen(QPen(col, 1))
                if g["o"] == "v":
                    x = round(self.to_screen(QPointF(g["pos"], 0)).x()) + 0.5
                    p.drawLine(QPointF(x, 0), QPointF(x, self.height()))
                else:
                    y = round(self.to_screen(QPointF(0, g["pos"])).y()) + 0.5
                    p.drawLine(QPointF(0, y), QPointF(self.width(), y))
        gd = self.guide_drag
        if gd is not None and gd.get("index") is None:
            p.setPen(QPen(QColor("#ff5fd2"), 1, Qt.DashLine))
            if gd["o"] == "v":
                x = self.to_screen(QPointF(gd["pos"], 0)).x()
                p.drawLine(QPointF(x, 0), QPointF(x, self.height()))
            else:
                y = self.to_screen(QPointF(0, gd["pos"])).y()
                p.drawLine(QPointF(0, y), QPointF(self.width(), y))
        for o, v in self.snap_marks:
            p.setPen(QPen(QColor("#ff5fd2"), 1, Qt.DotLine))
            if o == "v":
                x = self.to_screen(QPointF(v, 0)).x()
                p.drawLine(QPointF(x, 0), QPointF(x, self.height()))
            else:
                y = self.to_screen(QPointF(0, v)).y()
                p.drawLine(QPointF(0, y), QPointF(self.width(), y))
        p.restore()

    def _ruler_step(self):
        """Labelled tick spacing in document units: about 60+ px apart."""
        for base in (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000, 10000, 20000, 50000):
            if base * self.zoom >= 60:
                return base
        return 100000

    def _paint_rulers(self, p):
        if not self.ed.show_rulers:
            return
        p.save()
        p.setRenderHint(QPainter.Antialiasing, False)
        bg, fg, line = QColor("#2e3440"), QColor("#9aa3b5"), QColor("#4c566a")
        p.fillRect(QRectF(0, 0, self.width(), RULER), bg)
        p.fillRect(QRectF(0, 0, RULER, self.height()), bg)
        f = QFont(self.font())
        f.setPointSizeF(7)
        p.setFont(f)
        o = self.ruler_origin()
        step = self._ruler_step()
        minor = step / 10 if step * self.zoom >= 100 else step / 5
        # top ruler (x)
        x0 = self.to_doc(QPointF(RULER, 0)).x() - o.x()
        x1 = self.to_doc(QPointF(self.width(), 0)).x() - o.x()
        k = math.floor(x0 / minor)
        while k * minor <= x1:
            v = k * minor
            sx = round(self.to_screen(QPointF(v + o.x(), 0)).x()) + 0.5
            major = abs(v / step - round(v / step)) < 1e-6
            p.setPen(fg if major else line)
            p.drawLine(QPointF(sx, RULER - (RULER - 3 if major else 5)), QPointF(sx, RULER))
            if major:
                p.setPen(fg)
                p.drawText(QPointF(sx + 3, 9), "%g" % round(v, 3))
            k += 1
        # left ruler (y)
        y0 = self.to_doc(QPointF(0, RULER)).y() - o.y()
        y1 = self.to_doc(QPointF(0, self.height())).y() - o.y()
        k = math.floor(y0 / minor)
        while k * minor <= y1:
            v = k * minor
            sy = round(self.to_screen(QPointF(0, v + o.y())).y()) + 0.5
            major = abs(v / step - round(v / step)) < 1e-6
            p.setPen(fg if major else line)
            p.drawLine(QPointF(RULER - (RULER - 3 if major else 5), sy), QPointF(RULER, sy))
            if major:
                p.save()
                p.setPen(fg)
                p.translate(9, sy + 3)
                p.rotate(-90)
                p.drawText(QPointF(0, 0), "%g" % round(v, 3))
                p.restore()
            k += 1
        # where the cursor is
        if self.mouse_screen is not None:
            p.setPen(QColor("#88c0d0"))
            mx, my = self.mouse_screen.x(), self.mouse_screen.y()
            if mx >= RULER:
                p.drawLine(QPointF(mx, 0), QPointF(mx, RULER))
            if my >= RULER:
                p.drawLine(QPointF(0, my), QPointF(RULER, my))
        p.setPen(line)
        p.drawLine(QPointF(0, RULER - 0.5), QPointF(self.width(), RULER - 0.5))
        p.drawLine(QPointF(RULER - 0.5, 0), QPointF(RULER - 0.5, self.height()))
        p.fillRect(QRectF(0, 0, RULER, RULER), bg)
        p.restore()

    def _guide_press(self, ev):
        """Start dragging a new guide out of a ruler, or an existing one.
        Returns True when the press was taken."""
        sp = ev.position()
        if self.in_top_ruler(sp):
            self.guide_drag = {"o": "h", "pos": self.to_doc(sp).y(), "index": None}
            self.ed.show_guides = True
            return True
        if self.in_left_ruler(sp):
            self.guide_drag = {"o": "v", "pos": self.to_doc(sp).x(), "index": None}
            self.ed.show_guides = True
            return True
        if isinstance(self.tool, (SelectTool, DirectTool)):
            i = self.guide_at(sp)
            if i is not None:
                g = self.ed.doc.guides[i]
                self.ed.checkpoint()
                self.guide_drag = {"o": g["o"], "pos": g["pos"], "index": i}
                return True
        return False

    def _guide_move(self, ev):
        gd = self.guide_drag
        d = self.to_doc(ev.position())
        v = d.x() if gd["o"] == "v" else d.y()
        # guides land on whole units unless you're zoomed far in
        if self.zoom < 4:
            v = round(v)
        if self.ed.snap_on:
            xs, ys = [], []
            for ab in self.ed.doc.artboards:
                r = ab.rect
                xs += [r.left(), r.center().x(), r.right()]
                ys += [r.top(), r.center().y(), r.bottom()]
            hit = self._snap_axis([v], xs if gd["o"] == "v" else ys)
            if hit:
                v += hit[0]
        gd["pos"] = v
        if gd["index"] is not None:
            self.ed.doc.guides[gd["index"]]["pos"] = v
        self.status.emit(("x" if gd["o"] == "v" else "y") + " %g" % round(
            v - (self.ruler_origin().x() if gd["o"] == "v" else self.ruler_origin().y()), 2))
        self.update()

    def _guide_release(self, ev):
        gd = self.guide_drag
        self.guide_drag = None
        sp = ev.position()
        back_in_ruler = self.ed.show_rulers and ((gd["o"] == "h" and sp.y() < RULER) or
                                                (gd["o"] == "v" and sp.x() < RULER))
        if gd["index"] is None:
            if not back_in_ruler:
                self.ed.checkpoint()
                self.ed.doc.guides.append({"o": gd["o"], "pos": gd["pos"]})
                self.ed.notify("doc")
        else:
            if back_in_ruler or (not self.ed.show_rulers and not self.rect().contains(sp.toPoint())):
                del self.ed.doc.guides[gd["index"]]
            self.ed.notify("doc")
        self.update()

    def edit_guide(self, i):
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QPushButton
        g = self.ed.doc.guides[i]
        o = self.ruler_origin()
        base = o.x() if g["o"] == "v" else o.y()
        dlg = QDialog(self)
        dlg.setWindowTitle("Guide")
        f = QFormLayout(dlg)
        sb = QDoubleSpinBox()
        sb.setRange(-1e6, 1e6)
        sb.setDecimals(2)
        sb.setValue(g["pos"] - base)
        f.addRow("Vertical guide at x" if g["o"] == "v" else "Horizontal guide at y", sb)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        delete = QPushButton("Delete guide")
        bb.addButton(delete, QDialogButtonBox.DestructiveRole)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        delete.clicked.connect(lambda: dlg.done(2))
        f.addRow(bb)
        r = dlg.exec()
        if r == QDialog.Accepted:
            self.ed.checkpoint()
            g["pos"] = sb.value() + base
            self.ed.notify("doc")
        elif r == 2:
            self.ed.checkpoint()
            del self.ed.doc.guides[i]
            self.ed.notify("doc")

    def item_outline_screen(self, layer, it):
        off = self.ed.layer_offset(layer)
        t = QTransform.fromTranslate(off.x(), off.y())
        if self.live and it.id in self.live[0]:
            t = t * self.live[1]
        return (t * self.view_xf()).map(it.qpath())

    def _paint_selection(self, p):
        sel = self.ed.selected_with_layers()
        if not sel:
            return
        p.setBrush(Qt.NoBrush)
        for l, it in sel:
            p.setPen(QPen(QColor(l.tag), 1))
            if isinstance(it, PathItem) and len(sel) < 400:
                p.drawPath(self.item_outline_screen(l, it))
        if self.tool.shows_box():
            r = self.screen_sel_rect()
            if r is None:
                return
            tag = QColor(sel[-1][0].tag)
            p.setPen(QPen(tag, 1))
            p.drawRect(r)
            p.setBrush(Qt.white)
            for h in self.handle_points(r).values():
                p.drawRect(QRectF(h.x() - HANDLE / 2, h.y() - HANDLE / 2, HANDLE, HANDLE))
            rot = self.rotate_handle(r)
            p.drawLine(QPointF(r.center().x(), r.top()), rot)
            p.drawEllipse(rot, HANDLE / 2 + 1, HANDLE / 2 + 1)

    def screen_sel_rect(self):
        r = QRectF()
        for l, it in self.ed.selected_with_layers():
            b = it.geom_bounds().translated(self.ed.layer_offset(l))
            r = r.united(b)
        if r.isNull():
            return None
        if self.live:
            r = self.live[1].mapRect(r)
        return QRectF(self.to_screen(r.topLeft()), self.to_screen(r.bottomRight())).normalized()

    @staticmethod
    def handle_points(r):
        cx, cy = r.center().x(), r.center().y()
        return {"nw": r.topLeft(), "n": QPointF(cx, r.top()), "ne": r.topRight(),
                "e": QPointF(r.right(), cy), "se": r.bottomRight(), "s": QPointF(cx, r.bottom()),
                "sw": r.bottomLeft(), "w": QPointF(r.left(), cy)}

    @staticmethod
    def rotate_handle(r):
        return QPointF(r.center().x(), r.top() - 22)

    # ------------------------------------------------------------ hit testing
    def hit_item(self, p):
        """The object a click picks: top-level objects (a group as a whole),
        or the contents of the group being edited in isolation."""
        tol = self.tol()
        for l, lst in reversed(self.ed.context_lists()):
            off = self.ed.layer_offset(l)
            q = p - off
            for it in reversed(lst):
                if it.visible and not it.locked and it.hit(q, tol):
                    return l, it
        return None, None

    def hit_leaf(self, p, kinds=None):
        """The single drawn object under the cursor, however deep in groups."""
        tol = self.tol()

        def search(items, q):
            for it in reversed(items):
                if not it.visible or it.locked:
                    continue
                if isinstance(it, GroupItem):
                    clip = it.clip_qpath()
                    if clip is not None and not clip.contains(q):
                        continue
                    found = search(it.children, q)
                    if found is not None:
                        return found
                elif (kinds is None or isinstance(it, kinds)) and it.hit(q, tol):
                    return it
            return None
        g = self.ed.isolated_group()
        for l in reversed(self.ed.editable_layers()):
            if g is not None and self.ed.doc.find(g.id)[0] is not l:
                continue
            q = p - self.ed.layer_offset(l)
            it = search(g.children if g is not None else l.items, q)
            if it is not None:
                return l, it
        return None, None

    def items_in_rect(self, r):
        out = []
        for l, lst in self.ed.context_lists():
            off = self.ed.layer_offset(l)
            rr = r.translated(-off)
            for it in lst:
                if not it.visible or it.locked:
                    continue
                gb = it.geom_bounds()
                inside = (gb.left() >= rr.left() and gb.right() <= rr.right() and
                          gb.top() >= rr.top() and gb.bottom() <= rr.bottom())
                if inside:
                    out.append(it.id)
                    continue
                if not rr.intersects(gb.adjusted(-0.01, -0.01, 0.01, 0.01)):
                    continue
                if isinstance(it, PathItem):
                    # touching the outline (or the fill) counts, empty corners don't
                    if not it.qpath().intersects(rr):
                        from PySide6.QtGui import QPainterPathStroker
                        st = QPainterPathStroker()
                        st.setWidth(max(it.width, 1.0))
                        if not st.createStroke(it.qpath()).intersects(rr):
                            continue
                out.append(it.id)
        return out

    # ------------------------------------------------------------ events
    def _pos(self, ev):
        return ev.position() if hasattr(ev, "position") else QPointF(ev.pos())

    def mousePressEvent(self, ev):
        self.setFocus()
        if self.text_editor and not self.text_editor.geometry().contains(ev.position().toPoint()):
            self.finish_text()
            if isinstance(self.tool, TextTool):
                return
        if ev.button() == Qt.MiddleButton or (ev.button() == Qt.LeftButton and self.space_down):
            self._pan_from = (ev.position(), QPointF(self.pan))
            self.setCursor(Qt.ClosedHandCursor)
            return
        if ev.button() == Qt.LeftButton:
            if self._guide_press(ev):
                return
            if self.ed.show_rulers and (ev.position().x() < RULER and ev.position().y() < RULER):
                return  # the ruler corner
            if not self._tablet:
                self.pressure = 1.0
            self.tool.press(self.to_doc(ev.position()), ev)
        elif ev.button() == Qt.RightButton:
            self.tool.cancel()

    def mouseMoveEvent(self, ev):
        if self._pan_from:
            start, pan0 = self._pan_from
            self.pan = pan0 + (ev.position() - start)
            self.invalidate()
            if self.text_editor:
                self.text_editor.reposition()
            return
        self.mouse_screen = ev.position()
        if self.guide_drag is not None:
            self._guide_move(ev)
            return
        d = self.to_doc(ev.position())
        o = self.ruler_origin()
        self.status.emit("x %.1f   y %.1f" % (d.x() - o.x(), d.y() - o.y()))
        if self.ed.show_rulers:
            self.update(QRect(0, 0, self.width(), RULER))
            self.update(QRect(0, 0, RULER, self.height()))
        if ev.buttons() & Qt.LeftButton:
            self.tool.drag(d, ev)
        else:
            sp = ev.position()
            if self.in_top_ruler(sp) or self.in_left_ruler(sp):
                self.setCursor(Qt.SplitVCursor if self.in_top_ruler(sp) else Qt.SplitHCursor)
                return
            if isinstance(self.tool, (SelectTool, DirectTool)):
                i = self.guide_at(sp)
                if i is not None:
                    self.setCursor(Qt.SplitHCursor if self.ed.doc.guides[i]["o"] == "v" else Qt.SplitVCursor)
                    return
                self.setCursor(self.tool.cursor())
            self.tool.hover(d, ev)

    def mouseReleaseEvent(self, ev):
        if self._pan_from:
            self._pan_from = None
            self.setCursor(Qt.OpenHandCursor if self.space_down else self.tool.cursor())
            return
        if ev.button() == Qt.LeftButton:
            if self.guide_drag is not None:
                self._guide_release(ev)
                return
            self.tool.release(self.to_doc(ev.position()), ev)
            self.clear_snap()
            self._tablet = False

    def mouseDoubleClickEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            if isinstance(self.tool, (SelectTool, DirectTool)):
                i = self.guide_at(ev.position())
                if i is not None:
                    self.guide_drag = None
                    self.edit_guide(i)
                    return
            self.tool.double(self.to_doc(ev.position()), ev)

    def tabletEvent(self, ev):
        # Pen pressure feeds the brush; Qt then synthesises the mouse events
        self.pressure = max(0.05, ev.pressure())
        self._tablet = True
        ev.ignore()

    def wheelEvent(self, ev):
        dy = ev.angleDelta().y()
        dx = ev.angleDelta().x()
        if ev.modifiers() & Qt.ControlModifier or ev.modifiers() & Qt.AltModifier:
            f = 1.0015 ** (dy or dx)
            self.set_zoom(self.zoom * f, ev.position())
            return
        if ev.modifiers() & Qt.ShiftModifier:
            dx, dy = dy, 0
        self.pan += QPointF(dx, dy) * 0.6
        self.invalidate()
        if self.text_editor:
            self.text_editor.reposition()

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Space and not ev.isAutoRepeat():
            self.space_down = True
            self.setCursor(Qt.OpenHandCursor)
            return
        if self.tool.key(ev):
            return
        k = ev.key()
        if k == Qt.Key_Escape and self.ed.exit_isolation():
            return
        if k in (Qt.Key_Delete, Qt.Key_Backspace):
            self.ed.delete_selection()
            return
        if k in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down) and self.ed.selection:
            step = 10.0 if ev.modifiers() & Qt.ShiftModifier else 1.0
            dx = {Qt.Key_Left: -step, Qt.Key_Right: step}.get(k, 0)
            dy = {Qt.Key_Up: -step, Qt.Key_Down: step}.get(k, 0)
            self.ed.transform_selection(QTransform.fromTranslate(dx, dy))
            return
        if k == Qt.Key_BracketLeft:
            self.ed.brush_size = max(1.0, self.ed.brush_size / 1.2)
            self.ed.changed.emit("tool")
            self.update()
            return
        if k == Qt.Key_BracketRight:
            self.ed.brush_size = min(2000.0, self.ed.brush_size * 1.2)
            self.ed.changed.emit("tool")
            self.update()
            return
        super().keyPressEvent(ev)

    def keyReleaseEvent(self, ev):
        if ev.key() == Qt.Key_Space and not ev.isAutoRepeat():
            self.space_down = False
            self.setCursor(self.tool.cursor())
            return
        super().keyReleaseEvent(ev)

    def leaveEvent(self, ev):
        self.mouse_screen = None
        self.tool.leave()
        if self.ed.show_rulers:
            self.update()

    def resizeEvent(self, ev):
        self.invalidate()

    # ------------------------------------------------------------ text editing
    def edit_text(self, item, layer):
        self.finish_text()
        self.text_editor = InlineText(self, item, layer)
        self.text_editor.show()
        self.text_editor.setFocus()
        self.invalidate()

    def finish_text(self):
        te = self.text_editor
        if te is None:
            return
        self.text_editor = None
        txt = te.toPlainText()
        item, layer = te.item, te.layer
        te.hide()
        te.deleteLater()
        if self.ed.doc.find(item.id)[1] is item:
            if not txt.strip():
                if te.fresh and self.ed.undo_stack:
                    self.ed.undo_stack.pop()  # the empty box never happened
                else:
                    self.ed.checkpoint()
                self.ed.remove_item(item.id)
                self.ed.selection = [i for i in self.ed.selection if i != item.id]
            elif txt != item.text:
                if not te.fresh:
                    self.ed.checkpoint()
                item.text = txt
                item.touch()
        self.ed.notify("doc")
        self.ed.changed.emit("selection")
        self.setFocus()


class InlineText(QPlainTextEdit):
    def __init__(self, canvas, item, layer, fresh=False):
        super().__init__(canvas)
        self.c = canvas
        self.item = item
        self.layer = layer
        self.fresh = fresh
        self.setPlainText(item.text)
        self.setFrameStyle(0)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.textChanged.connect(self.reposition)
        self.reposition()
        self.moveCursor(QTextCursor.End)

    def reposition(self):
        it = self.item
        z = self.c.zoom * math.hypot(it.xf.m11(), it.xf.m12())
        f = QFont(it.family)
        f.setBold(it.bold)
        f.setItalic(it.italic)
        f.setPixelSize(max(4, int(it.size * z)))
        self.setFont(f)
        col = QColor(it.color or it.stroke or "#000000")
        bg = "rgba(255,255,255,40)" if col.lightness() < 160 else "rgba(0,0,0,60)"
        self.setStyleSheet("QPlainTextEdit{background:%s;color:%s;border:1px dashed #88c0d0;padding:0}"
                           % (bg, col.name()))
        self.document().setDocumentMargin(0)
        off = self.c.ed.layer_offset(self.layer)
        origin = self.c.to_screen(it.xf.map(QPointF(0, 0)) + off)
        fm = self.fontMetrics()
        lines = self.toPlainText().split("\n")
        w = max([fm.horizontalAdvance(l) for l in lines] + [fm.averageCharWidth() * 4]) + fm.averageCharWidth() * 2
        h = fm.lineSpacing() * max(1, len(lines)) + 6
        self.setGeometry(int(origin.x()) - 1, int(origin.y() - fm.ascent()) - 2, int(w), int(h))

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Escape or (ev.key() in (Qt.Key_Return, Qt.Key_Enter)
                                         and ev.modifiers() & Qt.ControlModifier):
            self.c.finish_text()
            return
        super().keyPressEvent(ev)

    def focusOutEvent(self, ev):
        super().focusOutEvent(ev)
        if ev.reason() in (Qt.ActiveWindowFocusReason, Qt.PopupFocusReason):
            return
        QTimer.singleShot(0, lambda: self.c.text_editor is self and self.c.finish_text())


# ================================================================ tools

class Tool:
    def __init__(self, canvas):
        self.c = canvas
        self.ed = canvas.ed

    def activate(self):
        pass

    def deactivate(self):
        pass

    def cursor(self):
        return Qt.ArrowCursor

    def shows_box(self):
        return False

    def press(self, p, ev):
        pass

    def drag(self, p, ev):
        pass

    def release(self, p, ev):
        pass

    def hover(self, p, ev):
        pass

    def double(self, p, ev):
        pass

    def key(self, ev):
        return False

    def cancel(self):
        pass

    def leave(self):
        pass

    def paint_overlay(self, p):
        pass


class SelectTool(Tool):
    def __init__(self, c):
        super().__init__(c)
        self.mode = None

    def shows_box(self):
        return True

    def press(self, p, ev):
        sp = ev.position()
        self.start = p
        self.mode = None
        r = self.c.screen_sel_rect()
        if r is not None:
            rot = Canvas.rotate_handle(r)
            if math.hypot(sp.x() - rot.x(), sp.y() - rot.y()) <= HANDLE:
                self.mode = "rotate"
                self.center = self.c.to_doc(r.center())
                return
            for name, h in Canvas.handle_points(r).items():
                if abs(sp.x() - h.x()) <= HANDLE and abs(sp.y() - h.y()) <= HANDLE:
                    self.mode = "scale"
                    self.handle = name
                    self.box = QRectF(self.c.to_doc(r.topLeft()), self.c.to_doc(r.bottomRight()))
                    return
        l, it = self.c.hit_item(p)
        shift = bool(ev.modifiers() & Qt.ShiftModifier)
        if it is not None:
            if shift:
                self.ed.select([it.id], toggle=True)
                self.mode = None
                return
            if it.id not in self.ed.selection:
                self.ed.select([it.id])
            self.mode = "move"
            self.dup = bool(ev.modifiers() & Qt.AltModifier)
            self.box0 = QRectF()
            for sel in self.ed.selected_items():
                self.box0 = self.box0.united(sel.geom_bounds())
            return
        if not shift:
            self.ed.select([])
        self.mode = "marquee"
        self.c.marquee = QRectF(p, p)

    def drag(self, p, ev):
        mods = ev.modifiers()
        if self.mode == "move":
            d = p - self.start
            lock = None
            if mods & Qt.ShiftModifier:
                if abs(d.x()) > abs(d.y()):
                    d.setY(0)
                    lock = "y"
                else:
                    d.setX(0)
                    lock = "x"
            d = self.c.snap_rect_delta(getattr(self, "box0", QRectF()), d)
            if lock == "y":
                d.setY(0)
            elif lock == "x":
                d.setX(0)
            self.c.live = (list(self.ed.selection), QTransform.fromTranslate(d.x(), d.y()))
            self.c.update()
        elif self.mode == "scale":
            self.c.live = (list(self.ed.selection), self._scale_xf(self.c.snap(p), mods))
            self.c.update()
        elif self.mode == "rotate":
            a0 = math.atan2(self.start.y() - self.center.y(), self.start.x() - self.center.x())
            a1 = math.atan2(p.y() - self.center.y(), p.x() - self.center.x())
            deg = math.degrees(a1 - a0)
            if mods & Qt.ShiftModifier:
                deg = round(deg / 15) * 15
            t = QTransform.fromTranslate(-self.center.x(), -self.center.y())
            t = t * QTransform().rotate(deg) * QTransform.fromTranslate(self.center.x(), self.center.y())
            self.c.live = (list(self.ed.selection), t)
            self.c.status.emit("Rotate %.1f°" % deg)
            self.c.update()
        elif self.mode == "marquee":
            self.c.marquee = QRectF(self.start, p).normalized()
            self.c.update()

    def _scale_xf(self, p, mods):
        b = self.box
        h = self.handle
        alt = bool(mods & Qt.AltModifier)
        sx = sy = 1.0
        if "e" in h or "w" in h:
            ox = b.left() if "w" in h else b.right()
            ax = b.center().x() if alt else (b.right() if "w" in h else b.left())
            if abs(ox - ax) > 1e-6:
                sx = (p.x() - ax) / (ox - ax)
        else:
            ax = b.center().x()
        if "n" in h or "s" in h:
            oy = b.top() if "n" in h else b.bottom()
            ay = b.center().y() if alt else (b.bottom() if "n" in h else b.top())
            if abs(oy - ay) > 1e-6:
                sy = (p.y() - ay) / (oy - ay)
        else:
            ay = b.center().y()
        if mods & Qt.ShiftModifier:
            if h in ("n", "s"):
                sx = abs(sy)
            elif h in ("e", "w"):
                sy = abs(sx)
            else:
                m = max(abs(sx), abs(sy))
                sx = math.copysign(m, sx)
                sy = math.copysign(m, sy)
        sx = sx if abs(sx) > 1e-4 else 1e-4
        sy = sy if abs(sy) > 1e-4 else 1e-4
        return QTransform.fromTranslate(-ax, -ay) * QTransform.fromScale(sx, sy) * QTransform.fromTranslate(ax, ay)

    def release(self, p, ev):
        if self.mode in ("move", "scale", "rotate") and self.c.live:
            ids, t = self.c.live
            self.c.live = None
            moved = not t.isIdentity()
            if moved:
                if self.mode == "move" and getattr(self, "dup", False):
                    self.ed.checkpoint()
                    new = []
                    for it in self.ed._outermost(self.ed.selected_items()):
                        c = it.clone(keep_id=False)
                        c.apply_transform(t)
                        _, cont, _ = self.ed.doc.locate(it.id)
                        cont.insert(cont.index(it) + 1, c)
                        new.append(c.id)
                    self.ed.notify("doc")
                    self.ed.select(new)
                else:
                    self.ed.transform_selection(t)
            self.c.invalidate()
        elif self.mode == "marquee":
            r = self.c.marquee
            self.c.marquee = None
            if r is not None and (r.width() > 0 or r.height() > 0):
                ids = self.c.items_in_rect(r)
                self.ed.select(ids, add=bool(ev.modifiers() & Qt.ShiftModifier))
            self.c.update()
        self.mode = None

    def double(self, p, ev):
        l, it = self.c.hit_item(p)
        if isinstance(it, GroupItem):
            # step inside the group; the object under the cursor is picked
            self.ed.enter_isolation(it)
            _, inner = self.c.hit_item(p)
            if inner is not None:
                self.ed.select([inner.id])
        elif it is None:
            while self.ed.exit_isolation():
                pass
        elif isinstance(it, TextItem):
            self.c.edit_text(it, l)
        elif isinstance(it, PathItem):
            self.c.toolRequested.emit("direct")

    def hover(self, p, ev):
        r = self.c.screen_sel_rect()
        sp = ev.position()
        cur = Qt.ArrowCursor
        if r is not None:
            rot = Canvas.rotate_handle(r)
            if math.hypot(sp.x() - rot.x(), sp.y() - rot.y()) <= HANDLE:
                cur = Qt.CrossCursor
            for name, h in Canvas.handle_points(r).items():
                if abs(sp.x() - h.x()) <= HANDLE and abs(sp.y() - h.y()) <= HANDLE:
                    cur = {"n": Qt.SizeVerCursor, "s": Qt.SizeVerCursor, "e": Qt.SizeHorCursor,
                           "w": Qt.SizeHorCursor, "nw": Qt.SizeFDiagCursor, "se": Qt.SizeFDiagCursor,
                           "ne": Qt.SizeBDiagCursor, "sw": Qt.SizeBDiagCursor}[name]
        self.c.setCursor(cur)

    def cancel(self):
        if self.c.live:
            self.c.live = None
            self.c.invalidate()
        self.mode = None


class DirectTool(Tool):
    """Move anchors and handles of paths."""

    def __init__(self, c):
        super().__init__(c)
        self.mode = None

    def cursor(self):
        return Qt.ArrowCursor

    def _path_items(self):
        return [(self.ed.doc.find(it.id)[0], it) for it in self.ed.selected_leaves() if isinstance(it, PathItem)]

    def find_point(self, sp):
        """Anchor or handle under screen point sp among selected paths."""
        for l, it in reversed(self._path_items()):
            off = self.ed.layer_offset(l)
            for si, sub in enumerate(it.subpaths):
                for ni, n in enumerate(sub["nodes"]):
                    for kind, (x, y) in (("anchor", (n[0], n[1])), ("in", (n[2], n[3])), ("out", (n[4], n[5]))):
                        if kind != "anchor" and not self._handle_shown(it, si, ni):
                            continue
                        q = self.c.to_screen(QPointF(x, y) + off)
                        if abs(q.x() - sp.x()) <= HANDLE and abs(q.y() - sp.y()) <= HANDLE:
                            if kind != "anchor" and (x, y) == (n[0], n[1]):
                                continue
                            return it, si, ni, kind
        return None

    def _handle_shown(self, it, si, ni):
        nodes = it.subpaths[si]["nodes"]
        for dn in (-1, 0, 1):
            j = ni + dn
            if it.subpaths[si].get("closed"):
                j %= len(nodes)
            if (it.id, si, j) in self.ed.node_sel:
                return True
        return False

    def press(self, p, ev):
        sp = ev.position()
        self.start = p
        self.mode = None
        shift = bool(ev.modifiers() & Qt.ShiftModifier)
        hit = self.find_point(sp)
        if hit is None:
            # anchors of any path under the cursor
            l, it = self.c.hit_leaf(p)
            if isinstance(it, PathItem):
                if it.id not in self.ed.selection:
                    self.ed.select([it.id], add=shift)
                hit = self.find_point(sp)
                if hit is None:
                    # clicked on the path body: select all its anchors, move it
                    self.ed.node_sel = {(it.id, si, ni) for si, s in enumerate(it.subpaths)
                                        for ni in range(len(s["nodes"]))}
                    self.anchor0 = None
                    self.box0 = it.geom_bounds()
                    self._begin_drag("anchors")
                    return
            elif it is not None:
                self.ed.select([it.id], add=shift)
                self.mode = "item"
                return
        if hit is None:
            if not shift:
                self.ed.select([])
                self.ed.node_sel = set()
            self.mode = "marquee"
            self.c.marquee = QRectF(p, p)
            self.c.update()
            return
        it, si, ni, kind = hit
        key = (it.id, si, ni)
        if kind == "anchor":
            if shift:
                self.ed.node_sel ^= {key}
            elif key not in self.ed.node_sel:
                self.ed.node_sel = {key}
            self.c.update()
            n = it.subpaths[si]["nodes"][ni]
            self.anchor0 = QPointF(n[0], n[1])
            self.box0 = None
            self._begin_drag("anchors")
        else:
            self.handle = (it, si, ni, kind)
            self._begin_drag("handle")
            self.break_handle = bool(ev.modifiers() & Qt.AltModifier)
            n = it.subpaths[si]["nodes"][ni]
            # smooth if the handles line up through the anchor
            ax, ay = n[0], n[1]
            v1 = (n[2] - ax, n[3] - ay)
            v2 = (n[4] - ax, n[5] - ay)
            cross = v1[0] * v2[1] - v1[1] * v2[0]
            l1, l2 = math.hypot(*v1), math.hypot(*v2)
            self.smooth = l1 > 0.01 and l2 > 0.01 and abs(cross) / (l1 * l2) < 0.02
            self.other_len = l2 if kind == "in" else l1

    def _begin_drag(self, mode):
        self.mode = mode
        self.ed.checkpoint()
        self.orig = {it.id: [[list(n) for n in s["nodes"]] for s in it.subpaths]
                     for _, it in self._path_items()}
        self.changed = False

    def drag(self, p, ev):
        if self.mode == "marquee":
            self.c.marquee = QRectF(self.start, p).normalized()
            self.c.update()
            return
        if self.mode not in ("anchors", "handle"):
            return
        d = p - self.start
        lock = None
        if ev.modifiers() & Qt.ShiftModifier and self.mode == "anchors":
            if abs(d.x()) > abs(d.y()):
                d.setY(0)
                lock = "y"
            else:
                d.setX(0)
                lock = "x"
        if self.mode == "anchors":
            if getattr(self, "anchor0", None) is not None:
                d = self.c.snap(self.anchor0 + d) - self.anchor0
            elif getattr(self, "box0", None) is not None:
                d = self.c.snap_rect_delta(self.box0, d)
            if lock == "y":
                d.setY(0)
            elif lock == "x":
                d.setX(0)
        self.changed = True
        for _, it in self._path_items():
            o = self.orig.get(it.id)
            if o is None:
                continue
            for si, s in enumerate(it.subpaths):
                for ni, n in enumerate(s["nodes"]):
                    n[:] = o[si][ni]
        if self.mode == "anchors":
            for _, it in self._path_items():
                for si, s in enumerate(it.subpaths):
                    for ni, n in enumerate(s["nodes"]):
                        if (it.id, si, ni) in self.ed.node_sel:
                            for k in (0, 2, 4):
                                n[k] += d.x()
                                n[k + 1] += d.y()
                it.touch()
        else:
            it, si, ni, kind = self.handle
            n = it.subpaths[si]["nodes"][ni]
            o = self.orig[it.id][si][ni]
            k = 2 if kind == "in" else 4
            n[k] = o[k] + d.x()
            n[k + 1] = o[k + 1] + d.y()
            if self.smooth and not self.break_handle:
                vx, vy = n[k] - n[0], n[k + 1] - n[1]
                L = math.hypot(vx, vy)
                if L > 1e-6:
                    ok = 4 if k == 2 else 2
                    n[ok] = n[0] - vx / L * self.other_len
                    n[ok + 1] = n[1] - vy / L * self.other_len
            it.touch()
        self.ed.notify("doc")

    def release(self, p, ev):
        if self.mode == "marquee":
            r = self.c.marquee
            self.c.marquee = None
            if r is not None and r.width() + r.height() > 0:
                shift = bool(ev.modifiers() & Qt.ShiftModifier)
                sel = set(self.ed.node_sel) if shift else set()
                ids = []
                for l in self.ed.editable_layers():
                    off = self.ed.layer_offset(l)
                    for it in leaf_items(l.items):
                        if not isinstance(it, PathItem) or not it.visible or it.locked:
                            continue
                        for si, s in enumerate(it.subpaths):
                            for ni, n in enumerate(s["nodes"]):
                                if r.contains(QPointF(n[0], n[1]) + off):
                                    sel.add((it.id, si, ni))
                                    if it.id not in ids:
                                        ids.append(it.id)
                self.ed.select(ids, add=shift)
                self.ed.node_sel = sel
            self.c.update()
        elif self.mode in ("anchors", "handle") and not self.changed:
            # nothing moved: drop the checkpoint we took
            if self.ed.undo_stack:
                self.ed.undo_stack.pop()
        self.mode = None

    def key(self, ev):
        if ev.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self.ed.node_sel:
            self.delete_nodes()
            return True
        return False

    def delete_nodes(self):
        self.ed.checkpoint()
        for l, it in list(self.ed.selected_with_layers()):
            if not isinstance(it, PathItem):
                continue
            new_subs = []
            for si, s in enumerate(it.subpaths):
                keep = [n for ni, n in enumerate(s["nodes"]) if (it.id, si, ni) not in self.ed.node_sel]
                if len(keep) >= 2:
                    new_subs.append({"nodes": keep, "closed": s.get("closed") and len(keep) > 2})
            it.subpaths = new_subs
            it.touch()
            if not new_subs:
                self.ed.remove_item(it.id)
        self.ed.node_sel = set()
        self.ed.notify("doc")
        self.ed.changed.emit("selection")

    def double(self, p, ev):
        l, it = self.c.hit_leaf(p, TextItem)
        if isinstance(it, TextItem):
            self.c.edit_text(it, l)

    def paint_overlay(self, p):
        p.save()
        for l, it in self._path_items():
            off = self.ed.layer_offset(l)
            col = QColor(l.tag)
            for si, s in enumerate(it.subpaths):
                for ni, n in enumerate(s["nodes"]):
                    a = self.c.to_screen(QPointF(n[0], n[1]) + off)
                    if self._handle_shown(it, si, ni):
                        p.setPen(QPen(col, 1))
                        for hx, hy in ((n[2], n[3]), (n[4], n[5])):
                            if (hx, hy) != (n[0], n[1]):
                                h = self.c.to_screen(QPointF(hx, hy) + off)
                                p.drawLine(a, h)
                                p.setBrush(col)
                                p.drawEllipse(h, 3, 3)
                    sel = (it.id, si, ni) in self.ed.node_sel
                    p.setPen(QPen(col, 1))
                    p.setBrush(col if sel else Qt.white)
                    p.drawRect(QRectF(a.x() - 3, a.y() - 3, 6, 6))
        p.restore()

    def cancel(self):
        self.mode = None


class PenTool(Tool):
    """Click for corners, drag for curves, click the first anchor to close.
    On a selected path: click a segment to add an anchor, click an anchor to
    remove it, Alt-click an anchor to toggle smooth/corner; click an open
    end to keep drawing from it."""

    def __init__(self, c):
        super().__init__(c)
        self.item = None      # path being drawn
        self.sub = 0
        self.dragging = False
        self.mouse = None

    def cursor(self):
        return Qt.CrossCursor

    def deactivate(self):
        self.finish()

    def finish(self):
        if self.item is not None:
            nodes = self.item.subpaths[self.sub]["nodes"] if self.item.subpaths else []
            if len(nodes) < 2:
                if self.ed.remove_item(self.item.id) is not None:
                    self.ed.selection = [i for i in self.ed.selection if i != self.item.id]
            self.item = None
            self.ed.notify("doc")
            self.ed.changed.emit("selection")
        self.c.update()

    def _screen_near(self, a, b, r=HANDLE):
        sa, sb = self.c.to_screen(a), self.c.to_screen(b)
        return abs(sa.x() - sb.x()) <= r and abs(sa.y() - sb.y()) <= r

    def press(self, p, ev):
        alt = bool(ev.modifiers() & Qt.AltModifier)
        if self.item is None:
            # edits on a selected path
            for l, it in self.ed.selected_with_layers():
                if not isinstance(it, PathItem):
                    continue
                off = self.ed.layer_offset(l)
                q = p - off
                for si, s in enumerate(it.subpaths):
                    nodes = s["nodes"]
                    for ni, n in enumerate(nodes):
                        if self._screen_near(QPointF(n[0], n[1]), q):
                            if not s.get("closed") and ni in (0, len(nodes) - 1) and not alt:
                                # continue drawing from this end
                                self.ed.checkpoint()
                                if ni == 0:
                                    s["nodes"] = [[m[0], m[1], m[4], m[5], m[2], m[3]] for m in reversed(nodes)]
                                self.item, self.sub = it, si
                                self.dragging = True
                                self.drag_node = s["nodes"][-1]
                                self.drag_fresh = False
                                return
                            self.ed.checkpoint()
                            if alt:
                                if (n[2], n[3]) == (n[0], n[1]) and (n[4], n[5]) == (n[0], n[1]):
                                    self._make_smooth(nodes, ni, s.get("closed"))
                                else:
                                    n[2], n[3], n[4], n[5] = n[0], n[1], n[0], n[1]
                            else:
                                del nodes[ni]
                                if len(nodes) < 3:
                                    s["closed"] = False
                                if len(nodes) < 2:
                                    it.subpaths.pop(si)
                                    if not it.subpaths:
                                        self.ed.remove_item(it.id)
                            it.touch()
                            self.ed.notify("doc")
                            return
                hit = nearest_on_path(it, q, self.c.tol() * 1.5)
                if hit is not None:
                    si, i, j, t, _ = hit
                    self.ed.checkpoint()
                    nodes = it.subpaths[si]["nodes"]
                    a, b = nodes[i], nodes[j]
                    left, right = split_cubic(*seg_points(nodes, i, j), t)
                    a[4], a[5] = left[1].x(), left[1].y()
                    b[2], b[3] = right[2].x(), right[2].y()
                    m = left[3]
                    new = [m.x(), m.y(), left[2].x(), left[2].y(), right[1].x(), right[1].y()]
                    nodes.insert(i + 1 if j > i else len(nodes), new)
                    it.touch()
                    self.ed.notify("doc")
                    return
            # start a new path
            self.ed.checkpoint()
            p = self.c.snap(p) - self.ed.layer_offset(self.ed.current_layer())
            it = PathItem([{"nodes": [corner(p.x(), p.y())], "closed": False}],
                          self.ed.fill, self.ed.stroke, self.ed.stroke_width)
            if it.fill is None and it.stroke is None:
                it.stroke = "#000000"
            self.ed.add_item(it, checkpoint=False)
            self.item, self.sub = it, 0
            self.dragging = True
            self.drag_node = it.subpaths[0]["nodes"][0]
            self.drag_fresh = True
            return
        nodes = self.item.subpaths[self.sub]["nodes"]
        off = self._offset()
        q = p - off
        if len(nodes) > 2 and self._screen_near(QPointF(nodes[0][0], nodes[0][1]), q):
            self.item.subpaths[self.sub]["closed"] = True
            self.item.touch()
            self.dragging = True
            self.drag_node = nodes[0]
            self.closing = True
            self.ed.notify("doc")
            return
        q = self.c.snap(p) - off
        nodes.append(corner(q.x(), q.y()))
        self.item.touch()
        self.dragging = True
        self.drag_node = nodes[-1]
        self.closing = False
        self.ed.notify("doc")

    def _offset(self):
        l, _ = self.ed.doc.find(self.item.id) if self.item else (None, None)
        return self.ed.layer_offset(l) if l is not None else QPointF()

    def _make_smooth(self, nodes, ni, closed):
        n = nodes[ni]
        prev = nodes[ni - 1] if ni > 0 or closed else None
        nxt = nodes[(ni + 1) % len(nodes)] if ni < len(nodes) - 1 or closed else None
        if prev is None or nxt is None:
            return
        vx, vy = nxt[0] - prev[0], nxt[1] - prev[1]
        L = math.hypot(vx, vy) or 1
        d1 = math.hypot(n[0] - prev[0], n[1] - prev[1]) / 3
        d2 = math.hypot(nxt[0] - n[0], nxt[1] - n[1]) / 3
        n[2], n[3] = n[0] - vx / L * d1, n[1] - vy / L * d1
        n[4], n[5] = n[0] + vx / L * d2, n[1] + vy / L * d2

    def drag(self, p, ev):
        self.mouse = p
        if not self.dragging or self.item is None:
            return
        n = self.drag_node
        q = p - self._offset()
        if ev.modifiers() & Qt.ShiftModifier:
            dx, dy = q.x() - n[0], q.y() - n[1]
            ang = round(math.atan2(dy, dx) / (math.pi / 4)) * (math.pi / 4)
            L = math.hypot(dx, dy)
            q = QPointF(n[0] + math.cos(ang) * L, n[1] + math.sin(ang) * L)
        if math.hypot(q.x() - n[0], q.y() - n[1]) * self.c.zoom < 2:
            return
        n[4], n[5] = q.x(), q.y()
        if not ev.modifiers() & Qt.AltModifier:
            n[2], n[3] = 2 * n[0] - q.x(), 2 * n[1] - q.y()
        self.item.touch()
        self.ed.notify("doc")

    def release(self, p, ev):
        self.dragging = False
        if self.item is not None and self.item.subpaths[self.sub].get("closed") and getattr(self, "closing", False):
            self.closing = False
            self.item = None
            self.ed.changed.emit("selection")
        self.c.update()

    def hover(self, p, ev):
        self.mouse = p
        self.c.update()

    def key(self, ev):
        if ev.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Escape) and self.item is not None:
            self.finish()
            return True
        return False

    def double(self, p, ev):
        pass

    def cancel(self):
        self.finish()

    def paint_overlay(self, p):
        if self.item is None or self.mouse is None or self.dragging:
            if self.item is None:
                # show anchors of the selected path so edits are findable
                p.save()
                for l, it in self.ed.selected_with_layers():
                    if isinstance(it, PathItem):
                        off = self.ed.layer_offset(l)
                        p.setPen(QPen(QColor(l.tag), 1))
                        p.setBrush(Qt.white)
                        for s in it.subpaths:
                            for n in s["nodes"]:
                                a = self.c.to_screen(QPointF(n[0], n[1]) + off)
                                p.drawRect(QRectF(a.x() - 3, a.y() - 3, 6, 6))
                p.restore()
            return
        nodes = self.item.subpaths[self.sub]["nodes"]
        if not nodes:
            return
        off = self._offset()
        last = nodes[-1]
        path = QPainterPath(QPointF(last[0], last[1]) + off)
        m = self.mouse
        path.cubicTo(QPointF(last[4], last[5]) + off, m, m)
        p.save()
        p.setPen(QPen(QColor("#88c0d0"), 1, Qt.DashLine))
        p.setBrush(Qt.NoBrush)
        p.drawPath(self.c.view_xf().map(path))
        for n in nodes:
            a = self.c.to_screen(QPointF(n[0], n[1]) + off)
            p.setPen(QPen(QColor("#88c0d0"), 1))
            p.setBrush(Qt.white)
            p.drawRect(QRectF(a.x() - 3, a.y() - 3, 6, 6))
        p.restore()


class ShapeTool(Tool):
    def __init__(self, c, kind):
        super().__init__(c)
        self.kind = kind
        self.start = None

    def cursor(self):
        return Qt.CrossCursor

    def press(self, p, ev):
        self.start = self.c.snap(p)
        self.cur = self.start

    def _rect(self, p, mods):
        s = self.start
        if self.kind == "line":
            if mods & Qt.ShiftModifier:
                dx, dy = p.x() - s.x(), p.y() - s.y()
                ang = round(math.atan2(dy, dx) / (math.pi / 4)) * (math.pi / 4)
                L = math.hypot(dx, dy)
                p = QPointF(s.x() + math.cos(ang) * L, s.y() + math.sin(ang) * L)
            return s, p
        w, h = p.x() - s.x(), p.y() - s.y()
        if mods & Qt.ShiftModifier:
            m = max(abs(w), abs(h))
            w, h = math.copysign(m, w), math.copysign(m, h)
        if mods & Qt.AltModifier:
            return QRectF(s.x() - w, s.y() - h, 2 * w, 2 * h).normalized()
        return QRectF(s.x(), s.y(), w, h).normalized()

    def drag(self, p, ev):
        if self.start is None:
            return
        self.cur = self.c.snap(p)
        self.mods = ev.modifiers()
        self.c.update()

    def release(self, p, ev):
        if self.start is None:
            return
        g = self._rect(self.c.snap(p), ev.modifiers())
        self.start = None
        self.c.update()
        if self.kind == "line":
            a, b = g
            if math.hypot(b.x() - a.x(), b.y() - a.y()) * self.c.zoom < 3:
                return
            it = PathItem([{"nodes": [corner(a.x(), a.y()), corner(b.x(), b.y())], "closed": False}],
                          None, self.ed.stroke or self.ed.fill or "#000000", max(self.ed.stroke_width, 0.5))
        else:
            if g.width() * self.c.zoom < 3 or g.height() * self.c.zoom < 3:
                return
            subs = rect_subpaths(g) if self.kind == "rect" else ellipse_subpaths(g)
            it = PathItem(subs, self.ed.fill, self.ed.stroke, self.ed.stroke_width)
            if it.fill is None and it.stroke is None:
                it.stroke = "#000000"
        off = self.ed.layer_offset(self.ed.current_layer())
        if off.x() or off.y():
            it.apply_transform(QTransform.fromTranslate(-off.x(), -off.y()))
        self.ed.add_item(it)

    def paint_overlay(self, p):
        if self.start is None:
            return
        g = self._rect(self.cur, getattr(self, "mods", Qt.NoModifier))
        p.save()
        p.setTransform(self.c.view_xf())
        p.setPen(QPen(QColor("#88c0d0"), 1 / self.c.zoom, Qt.DashLine))
        p.setBrush(Qt.NoBrush)
        if self.kind == "line":
            p.drawLine(g[0], g[1])
        elif self.kind == "rect":
            p.drawRect(g)
        else:
            p.drawEllipse(g)
        p.restore()

    def cancel(self):
        self.start = None
        self.c.update()


class TextTool(Tool):
    def cursor(self):
        return Qt.IBeamCursor

    def press(self, p, ev):
        l, it = self.c.hit_leaf(p, TextItem)
        if isinstance(it, TextItem):
            self.ed.select([it.id])
            self.c.edit_text(it, l)
            return
        layer = self.ed.current_layer()
        off = self.ed.layer_offset(layer)
        t = TextItem("", self.ed.font_family, self.ed.font_size, self.ed.fill or "#000000")
        q = self.c.snap(p) - off
        t.xf = QTransform.fromTranslate(q.x(), q.y() + self.ed.font_size * 0.35)
        self.ed.add_item(t)
        self.c.finish_text()
        self.c.text_editor = InlineText(self.c, t, layer, fresh=True)
        self.c.text_editor.show()
        self.c.text_editor.setFocus()
        self.c.invalidate()

    def deactivate(self):
        self.c.finish_text()


class BrushTool(Tool):
    """Paints into a raster object on the current layer (made if needed)."""

    def __init__(self, c, erase=False):
        super().__init__(c)
        self.erase = erase
        self.mouse = None
        self.target = None

    def cursor(self):
        return Qt.BlankCursor

    def _find_target(self, p):
        layer = self.ed.current_layer()
        if layer.locked or not self.ed.layer_visible(layer):
            self.c.status.emit("The current layer is hidden or locked")
            return None, None
        q = p - self.ed.layer_offset(layer)
        leaves = list(leaf_items(self.ed.target_list()))
        for it in self.ed.selected_leaves():
            if isinstance(it, RasterItem) and it in leaves and it.bounds().contains(q):
                return layer, it
        for it in reversed(leaves):
            if isinstance(it, RasterItem) and it.visible and not it.locked and it.bounds().contains(q):
                return layer, it
        if self.erase:
            return None, None
        ab = self.ed.artboard_at(q) or self.ed.current_artboard()
        it = self.ed.make_raster(layer, ab.rect)
        return layer, it

    def press(self, p, ev):
        self.ed.checkpoint()
        layer, it = self._find_target(p)
        if it is None:
            self.ed.undo_stack.pop()
            return
        self.ed.notify("doc")
        self.layer, self.target = layer, it
        inv, _ = it.xf.inverted()
        self.inv = inv
        self.orig = it.image.copy()
        self.mask = QImage(it.image.size(), QImage.Format_Grayscale8)
        self.mask.fill(0)
        self.color = QColor(self.ed.fill or "#000000")
        self.last = None
        self.residual = 0.0
        self.c.split = {"item": it, "layer": layer}
        self._dab_to(p)

    def _radius_px(self):
        scale = math.hypot(self.inv.m11(), self.inv.m12())
        return max(0.5, self.ed.brush_size * scale * self.c.pressure / 2)

    def _dab_to(self, p):
        q = self.inv.map(p - self.ed.layer_offset(self.layer))
        r = self._radius_px()
        pts = []
        if self.last is None:
            pts.append(q)
        else:
            dx, dy = q.x() - self.last.x(), q.y() - self.last.y()
            L = math.hypot(dx, dy)
            spacing = max(0.5, r * (0.12 if self.ed.brush_kind != "square" else 0.25))
            d = spacing - self.residual
            while d <= L:
                pts.append(QPointF(self.last.x() + dx * d / L, self.last.y() + dy * d / L))
                d += spacing
            self.residual = L - (d - spacing)
        self.last = q
        if not pts:
            return
        mp = QPainter(self.mask)
        mp.setCompositionMode(QPainter.CompositionMode_Lighten)
        kind = self.ed.brush_kind
        mp.setRenderHint(QPainter.Antialiasing, kind != "square")
        dirty = QRectF()
        for c in pts:
            box = QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r)
            dirty = dirty.united(box)
            if kind == "square":
                mp.fillRect(QRectF(round(c.x() - r), round(c.y() - r), round(2 * r), round(2 * r)), Qt.white)
            elif kind == "hard":
                mp.setPen(Qt.NoPen)
                mp.setBrush(Qt.white)
                mp.drawEllipse(c, r, r)
            else:
                g = QRadialGradient(c, r)
                if kind == "medium":
                    g.setColorAt(0, Qt.white)
                    g.setColorAt(0.55, Qt.white)
                    g.setColorAt(1, Qt.black)
                else:
                    for t, v in ((0, 255), (0.12, 255), (0.35, 215), (0.6, 120), (0.8, 45), (1, 0)):
                        g.setColorAt(t, QColor(v, v, v))
                mp.setPen(Qt.NoPen)
                mp.setBrush(QBrush(g))
                mp.drawEllipse(c, r, r)
        mp.end()
        self._compose(dirty.adjusted(-2, -2, 2, 2).toAlignedRect())

    def _compose(self, rect):
        img = self.target.image
        rect = rect.intersected(img.rect())
        if rect.isEmpty():
            return
        maskpart = self.mask.copy(rect)
        p = QPainter(img)
        p.setCompositionMode(QPainter.CompositionMode_Source)
        p.drawImage(rect.topLeft(), self.orig, rect)
        col = QImage(rect.size(), QImage.Format_ARGB32_Premultiplied)
        col.fill(self.color if not self.erase else Qt.black)
        col.setAlphaChannel(maskpart)
        p.setOpacity(self.ed.brush_opacity)
        p.setCompositionMode(QPainter.CompositionMode_DestinationOut if self.erase
                             else QPainter.CompositionMode_SourceOver)
        p.drawImage(rect.topLeft(), col)
        p.end()
        self.c.update()

    def drag(self, p, ev):
        self.mouse = p
        if self.target is not None:
            self._dab_to(p)
        else:
            self.c.update()

    def release(self, p, ev):
        if self.target is not None:
            self.target.touch()
            self.target = None
            self.c.split = None
            self.ed.notify("doc")
        self.c.invalidate()

    def hover(self, p, ev):
        self.mouse = p
        self.c.update()

    def leave(self):
        self.mouse = None
        self.c.update()

    def cancel(self):
        if self.target is not None:
            self.target.image = self.orig
            self.target = None
            self.c.split = None
            self.ed.notify("doc")

    def paint_overlay(self, p):
        if self.mouse is None:
            return
        s = self.c.to_screen(self.mouse)
        r = self.ed.brush_size * self.c.zoom / 2
        p.save()
        p.setBrush(Qt.NoBrush)
        for col, w in ((QColor(0, 0, 0, 160), 3), (QColor(255, 255, 255, 220), 1)):
            p.setPen(QPen(col, w))
            if self.ed.brush_kind == "square":
                p.drawRect(QRectF(s.x() - r, s.y() - r, 2 * r, 2 * r))
            else:
                p.drawEllipse(s, max(r, 1.5), max(r, 1.5))
        p.restore()


class EyedropperTool(Tool):
    def cursor(self):
        return Qt.CrossCursor

    def press(self, p, ev):
        for l in reversed(self.ed.doc.layers):
            if not self.ed.layer_visible(l):
                continue
            q = p - self.ed.layer_offset(l)
            for it in reversed(list(leaf_items(l.items))):
                if not it.visible or not it.hit(q, self.c.tol()):
                    continue
                col = None
                if isinstance(it, PathItem):
                    col = it.fill or it.stroke
                elif isinstance(it, TextItem):
                    col = it.color or it.stroke
                elif isinstance(it, RasterItem):
                    inv, _ = it.xf.inverted()
                    lp = inv.map(q)
                    c = it.image.pixelColor(int(lp.x()), int(lp.y()))
                    if c.alpha() > 0:
                        col = c.name()
                if col:
                    self.c.colorPicked.emit(col, bool(ev.modifiers() & Qt.ShiftModifier))
                    return


class HandTool(Tool):
    def cursor(self):
        return Qt.OpenHandCursor

    def press(self, p, ev):
        self.start = ev.position()
        self.pan0 = QPointF(self.c.pan)

    def drag(self, p, ev):
        self.c.pan = self.pan0 + (ev.position() - self.start)
        self.c.invalidate()


class ZoomTool(Tool):
    def cursor(self):
        return Qt.CrossCursor

    def press(self, p, ev):
        self.start = p
        self.c.marquee = QRectF(p, p)

    def drag(self, p, ev):
        self.c.marquee = QRectF(self.start, p).normalized()
        self.c.update()

    def release(self, p, ev):
        r = self.c.marquee
        self.c.marquee = None
        if r is not None and r.width() * self.c.zoom > 8 and r.height() * self.c.zoom > 8:
            self.c.fit_rect(r, 10)
            return
        f = 0.5 if ev.modifiers() & Qt.AltModifier else 2.0
        self.c.set_zoom(self.c.zoom * f, ev.position())


class FrameMoveTool(Tool):
    """Drag the current layer around in the current frame only."""

    def cursor(self):
        return Qt.SizeAllCursor

    def press(self, p, ev):
        self.layer = None
        f = self.ed.frame()
        if f is None:
            self.c.status.emit("Add a frame in the Timeline first; this tool moves a layer within one frame")
            return
        l, it = self.c.hit_item(p)
        self.layer = l or self.ed.current_layer()
        self.ed.current_layer_id = self.layer.id
        self.ed.checkpoint()
        s = f.state(self.layer)
        self.start = p
        self.d0 = (s["dx"], s["dy"])

    def drag(self, p, ev):
        if self.layer is None:
            return
        d = p - self.start
        if ev.modifiers() & Qt.ShiftModifier:
            if abs(d.x()) > abs(d.y()):
                d.setY(0)
            else:
                d.setX(0)
        self.ed.frame().set_state(self.layer, dx=self.d0[0] + d.x(), dy=self.d0[1] + d.y())
        self.ed.notify("frame")

    def release(self, p, ev):
        if self.layer is not None:
            self.ed.notify("doc")
        self.layer = None


class ArtboardTool(Tool):
    """Click an artboard to make it current, drag inside to move it (with
    its artwork), drag on the pasteboard to draw a new one, drag the
    current artboard's edges to resize."""

    def cursor(self):
        return Qt.CrossCursor

    def press(self, p, ev):
        self.mode = None
        self.start = p
        sp = ev.position()
        ab = self.ed.current_artboard()
        r = QRectF(self.c.to_screen(ab.rect.topLeft()), self.c.to_screen(ab.rect.bottomRight()))
        for name, h in Canvas.handle_points(r).items():
            if abs(sp.x() - h.x()) <= HANDLE and abs(sp.y() - h.y()) <= HANDLE:
                self.mode = "resize"
                self.handle = name
                self.orig = QRectF(ab.rect)
                self.ed.checkpoint()
                return
        hit = self.ed.artboard_at(p)
        if hit is not None:
            self.ed.current_artboard_id = hit.id
            self.ed.changed.emit("selection")
            self.mode = "move"
            self.orig = QRectF(hit.rect)
            self.ed.checkpoint()
            self.carry = [it for l in self.ed.doc.layers for it in l.items
                          if hit.rect.contains(it.bounds().center())]
            self.moved = QPointF(0, 0)
            self.c.update()
            return
        self.mode = "new"
        self.c.marquee = QRectF(p, p)

    def drag(self, p, ev):
        ab = self.ed.current_artboard()
        if self.mode == "move":
            d = p - self.start
            d = QPointF(round(d.x()), round(d.y()))
            step = d - self.moved
            ab.rect = self.orig.translated(d)
            if not ev.modifiers() & Qt.AltModifier:
                t = QTransform.fromTranslate(step.x(), step.y())
                for it in self.carry:
                    it.apply_transform(t)
            self.moved = d
            self.ed.notify("doc")
        elif self.mode == "resize":
            r = QRectF(self.orig)
            h = self.handle
            x, y = round(p.x()), round(p.y())
            if "w" in h:
                r.setLeft(min(x, r.right() - 1))
            if "e" in h:
                r.setRight(max(x, r.left() + 1))
            if "n" in h:
                r.setTop(min(y, r.bottom() - 1))
            if "s" in h:
                r.setBottom(max(y, r.top() + 1))
            ab.rect = r
            self.c.status.emit("%d × %d" % (r.width(), r.height()))
            self.ed.notify("doc")
        elif self.mode == "new":
            self.c.marquee = QRectF(self.start, p).normalized()
            self.c.update()

    def release(self, p, ev):
        if self.mode == "new":
            r = self.c.marquee
            self.c.marquee = None
            if r is not None and r.width() * self.c.zoom > 8 and r.height() * self.c.zoom > 8:
                self.ed.checkpoint()
                r = QRectF(round(r.x()), round(r.y()), round(r.width()), round(r.height()))
                ab = Artboard("Artboard %d" % (len(self.ed.doc.artboards) + 1), r.x(), r.y(), r.width(), r.height())
                self.ed.doc.artboards.append(ab)
                self.ed.current_artboard_id = ab.id
                self.ed.notify("doc")
            self.c.update()
        self.mode = None

    def paint_overlay(self, p):
        ab = self.ed.current_artboard()
        r = QRectF(self.c.to_screen(ab.rect.topLeft()), self.c.to_screen(ab.rect.bottomRight()))
        p.save()
        p.setPen(QPen(QColor("#88c0d0"), 1))
        p.setBrush(Qt.white)
        for h in Canvas.handle_points(r).values():
            p.drawRect(QRectF(h.x() - HANDLE / 2, h.y() - HANDLE / 2, HANDLE, HANDLE))
        p.restore()

    def key(self, ev):
        if ev.key() in (Qt.Key_Delete, Qt.Key_Backspace) and len(self.ed.doc.artboards) > 1:
            self.ed.checkpoint()
            ab = self.ed.current_artboard()
            self.ed.doc.artboards.remove(ab)
            self.ed.current_artboard_id = self.ed.doc.artboards[0].id
            self.ed.notify("doc")
            return True
        return False
