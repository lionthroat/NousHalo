"""The Nous Halo document: artboards, layers, objects, palettes, frames.

Coordinates are document units (points, like Illustrator); one unit is one
pixel at 100% zoom and in a 1x export.

Objects
  PathItem    bezier subpaths; nodes are [ax, ay, inx, iny, outx, outy]
              (anchor, handle coming in, handle going out), stored in
              document coordinates, so transforms are baked in.
  TextItem    editable text; drawn from the font's outlines. Its local
              origin is the first baseline's left end; `xf` places it.
  RasterItem  a pixel image; `xf` maps pixel space into the document.
Every object can carry a clipping path (document coordinates).

Files are .halo: a zip with document.json, rasters/*.png, fonts/*.
"""
import copy
import io
import itertools
import json
import math
import zipfile

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QFontMetricsF, QImage,
                           QPainterPath, QPainterPathStroker, QPen, QTransform)

from halo_palette import Palette

_ids = itertools.count(1)
_revs = itertools.count(1)


def new_id(prefix="o"):
    return "%s%d" % (prefix, next(_ids))


def _bump_ids(existing):
    """After loading, make sure fresh ids never collide with loaded ones."""
    global _ids
    hi = 0
    for i in existing:
        digits = "".join(ch for ch in i if ch.isdigit())
        if digits:
            hi = max(hi, int(digits))
    cur = next(_ids)
    _ids = itertools.count(max(cur, hi + 1))


def xf_to_list(t):
    return [t.m11(), t.m12(), t.m21(), t.m22(), t.dx(), t.dy()]


def xf_from_list(v):
    return QTransform(v[0], v[1], v[2], v[3], v[4], v[5])


def nodes_to_qpath(subpaths, even_odd=False):
    p = QPainterPath()
    p.setFillRule(Qt.OddEvenFill if even_odd else Qt.WindingFill)
    for sp in subpaths:
        nodes = sp["nodes"]
        if not nodes:
            continue
        p.moveTo(nodes[0][0], nodes[0][1])
        segs = list(zip(nodes, nodes[1:]))
        if sp.get("closed") and len(nodes) > 1:
            segs.append((nodes[-1], nodes[0]))
        for a, b in segs:
            if a[4] == a[0] and a[5] == a[1] and b[2] == b[0] and b[3] == b[1]:
                p.lineTo(b[0], b[1])
            else:
                p.cubicTo(a[4], a[5], b[2], b[3], b[0], b[1])
        if sp.get("closed"):
            p.closeSubpath()
    return p


def map_subpaths(subpaths, t):
    out = []
    for sp in subpaths:
        nn = []
        for n in sp["nodes"]:
            a = t.map(QPointF(n[0], n[1]))
            i = t.map(QPointF(n[2], n[3]))
            o = t.map(QPointF(n[4], n[5]))
            nn.append([a.x(), a.y(), i.x(), i.y(), o.x(), o.y()])
        out.append({"nodes": nn, "closed": sp.get("closed", False)})
    return out


def qpath_to_subpaths(path):
    """QPainterPath -> node subpaths (closing where the path closes)."""
    subs = []
    cur = None
    i, n = 0, path.elementCount()
    while i < n:
        e = path.elementAt(i)
        if e.type == QPainterPath.MoveToElement:
            cur = {"nodes": [[e.x, e.y, e.x, e.y, e.x, e.y]], "closed": False}
            subs.append(cur)
            i += 1
        elif e.type == QPainterPath.LineToElement:
            cur["nodes"].append([e.x, e.y, e.x, e.y, e.x, e.y])
            i += 1
        elif e.type == QPainterPath.CurveToElement:
            c1, c2, end = e, path.elementAt(i + 1), path.elementAt(i + 2)
            last = cur["nodes"][-1]
            last[4], last[5] = c1.x, c1.y
            cur["nodes"].append([end.x, end.y, c2.x, c2.y, end.x, end.y])
            i += 3
        else:
            i += 1
    for sp in subs:
        nodes = sp["nodes"]
        if len(nodes) > 2 and abs(nodes[0][0] - nodes[-1][0]) < 1e-6 and abs(nodes[0][1] - nodes[-1][1]) < 1e-6:
            nodes[0][2], nodes[0][3] = nodes[-1][2], nodes[-1][3]
            nodes.pop()
            sp["closed"] = True
    return [s for s in subs if len(s["nodes"]) > 1]


def _is_corner(n):
    return n[2] == n[0] and n[3] == n[1] and n[4] == n[0] and n[5] == n[1]


def path_shape_name(subpaths):
    """What a path looks like, for the Layers panel: Rectangle, Ellipse..."""
    subs = [s for s in subpaths if s["nodes"]]
    if not subs:
        return "Empty path"
    if len(subs) > 1:
        return "Compound path"
    sp = subs[0]
    nodes = sp["nodes"]
    closed = sp.get("closed")
    if not closed:
        if len(nodes) == 2:
            return "Line" if all(_is_corner(n) for n in nodes) else "Curve"
        return "Open path"
    if len(nodes) == 4 and all(_is_corner(n) for n in nodes):
        # four corners with right angles
        ok = True
        for i in range(4):
            a, b, c = nodes[i - 1], nodes[i], nodes[(i + 1) % 4]
            v1 = (a[0] - b[0], a[1] - b[1])
            v2 = (c[0] - b[0], c[1] - b[1])
            l1, l2 = math.hypot(*v1), math.hypot(*v2)
            if l1 < 1e-9 or l2 < 1e-9 or abs(v1[0] * v2[0] + v1[1] * v2[1]) / (l1 * l2) > 0.02:
                ok = False
        if ok:
            w = math.hypot(nodes[1][0] - nodes[0][0], nodes[1][1] - nodes[0][1])
            h = math.hypot(nodes[2][0] - nodes[1][0], nodes[2][1] - nodes[1][1])
            return "Square" if abs(w - h) < 0.01 * max(w, h) else "Rectangle"
        return "Quadrilateral"
    if len(nodes) == 4 and not any(_is_corner(n) for n in nodes):
        xs = [n[0] for n in nodes]
        ys = [n[1] for n in nodes]
        w, h = max(xs) - min(xs), max(ys) - min(ys)
        return "Circle" if abs(w - h) < 0.01 * max(w, h, 1e-9) else "Ellipse"
    if len(nodes) == 3 and all(_is_corner(n) for n in nodes):
        return "Triangle"
    if all(_is_corner(n) for n in nodes):
        return "Polygon"
    return "Path"


def corner(x, y):
    return [x, y, x, y, x, y]


def rect_subpaths(r):
    return [{"nodes": [corner(r.left(), r.top()), corner(r.right(), r.top()),
                       corner(r.right(), r.bottom()), corner(r.left(), r.bottom())],
             "closed": True}]


def ellipse_subpaths(r):
    k = 0.5522847498
    cx, cy = r.center().x(), r.center().y()
    rx, ry = r.width() / 2, r.height() / 2
    pts = [(cx, cy - ry, -1, 0), (cx + rx, cy, 0, -1), (cx, cy + ry, 1, 0), (cx - rx, cy, 0, 1)]
    nodes = []
    for x, y, dx, dy in pts:
        hx, hy = dx * rx * k, dy * ry * k
        nodes.append([x, y, x + hx, y + hy, x - hx, y - hy])
    return [{"nodes": nodes, "closed": True}]


# ---------------------------------------------------------------- objects

class Item:
    kind = "item"

    def __init__(self):
        self.id = new_id()
        self.name = ""
        self.visible = True
        self.locked = False
        self.opacity = 1.0
        self.clip = None  # list of subpaths in document coordinates
        self._cache = {}
        self.rev = next(_revs)

    def touch(self):
        self._cache = {}
        self.rev = next(_revs)  # thumbnails redraw when this changes

    def clip_qpath(self):
        if not self.clip:
            return None
        if "clip" not in self._cache:
            self._cache["clip"] = nodes_to_qpath(self.clip)
        return self._cache["clip"]

    def base_dict(self):
        return {"kind": self.kind, "id": self.id, "name": self.name, "visible": self.visible,
                "locked": self.locked, "opacity": self.opacity, "clip": self.clip}

    def load_base(self, d):
        self.id = d.get("id", self.id)
        self.name = d.get("name", "")
        self.visible = d.get("visible", True)
        self.locked = d.get("locked", False)
        self.opacity = d.get("opacity", 1.0)
        self.clip = d.get("clip")

    def apply_transform(self, t):
        if self.clip:
            self.clip = map_subpaths(self.clip, t)
        self.touch()

    def colors(self):
        return []

    def clone(self, keep_id=True):
        c = copy.copy(self)
        c.clip = copy.deepcopy(self.clip)
        c._cache = {}
        if not keep_id:
            c.id = new_id()
        return c

    def label(self):
        return self.name or self.kind_label()

    def kind_label(self):
        return self.kind


class PathItem(Item):
    kind = "path"

    def __init__(self, subpaths=None, fill=None, stroke=None, width=1.0):
        super().__init__()
        self.subpaths = subpaths or []
        self.fill = fill
        self.stroke = stroke
        self.width = width
        self.cap = 0      # 0 butt, 1 round, 2 square
        self.join = 0     # 0 miter, 1 round, 2 bevel
        self.dashes = None
        self.even_odd = False

    def qpath(self):
        if "path" not in self._cache:
            self._cache["path"] = nodes_to_qpath(self.subpaths, self.even_odd)
        return self._cache["path"]

    def pen(self):
        pen = QPen(QColor(self.stroke), max(self.width, 0.0))
        pen.setCapStyle([Qt.FlatCap, Qt.RoundCap, Qt.SquareCap][self.cap])
        pen.setJoinStyle([Qt.MiterJoin, Qt.RoundJoin, Qt.BevelJoin][self.join])
        pen.setMiterLimit(10)
        if self.dashes and self.width > 0:
            pen.setDashPattern([max(d / max(self.width, 0.01), 0.01) for d in self.dashes])
        if self.width <= 0:
            pen.setCosmetic(True)
        return pen

    def bounds(self):
        if "bounds" not in self._cache:
            r = self.qpath().boundingRect() if self.subpaths else QRectF()
            if self.stroke and self.width > 0:
                w = self.width / 2
                r = r.adjusted(-w, -w, w, w)
            self._cache["bounds"] = r
        return self._cache["bounds"]

    def geom_bounds(self):
        return self.qpath().boundingRect()

    def hit(self, p, tol):
        b = self.bounds().adjusted(-tol, -tol, tol, tol)
        if not b.contains(p):
            return False
        path = self.qpath()
        if self.fill and path.contains(p):
            return True
        if "stroker" not in self._cache:
            s = QPainterPathStroker()
            s.setWidth(max(self.width if self.stroke else 0, 0) + 2 * tol)
            self._cache["stroker"] = s.createStroke(path)
            self._cache["stroker_tol"] = tol
        elif abs(self._cache.get("stroker_tol", tol) - tol) > 0.01:
            s = QPainterPathStroker()
            s.setWidth(max(self.width if self.stroke else 0, 0) + 2 * tol)
            self._cache["stroker"] = s.createStroke(path)
            self._cache["stroker_tol"] = tol
        return self._cache["stroker"].contains(p)

    def apply_transform(self, t):
        self.subpaths = map_subpaths(self.subpaths, t)
        sx = math.hypot(t.m11(), t.m12())
        sy = math.hypot(t.m21(), t.m22())
        if self.stroke and abs(sx - 1) > 1e-6:
            self.width *= math.sqrt(abs(sx * sy))
        super().apply_transform(t)

    def colors(self):
        return [c for c in (self.fill, self.stroke) if c]

    def to_dict(self):
        d = self.base_dict()
        d.update(subpaths=self.subpaths, fill=self.fill, stroke=self.stroke, width=self.width,
                 cap=self.cap, join=self.join, dashes=self.dashes, even_odd=self.even_odd)
        return d

    @staticmethod
    def from_dict(d):
        it = PathItem(d.get("subpaths", []), d.get("fill"), d.get("stroke"), d.get("width", 1.0))
        it.load_base(d)
        it.cap = d.get("cap", 0)
        it.join = d.get("join", 0)
        it.dashes = d.get("dashes")
        it.even_odd = d.get("even_odd", False)
        return it

    def clone(self, keep_id=True):
        c = super().clone(keep_id)
        c.subpaths = copy.deepcopy(self.subpaths)
        c.dashes = list(self.dashes) if self.dashes else None
        return c

    def kind_label(self):
        return path_shape_name(self.subpaths)


TEXT_UNIT = 100.0  # text outlines are built at this size, then scaled


class TextItem(Item):
    kind = "text"

    def __init__(self, text="", family="Arial", size=24.0, color="#000000"):
        super().__init__()
        self.text = text
        self.family = family
        self.size = size
        self.color = color
        self.bold = False
        self.italic = False
        self.tracking = 0.0     # extra space between letters, document units
        self.leading = 1.2      # line height as a multiple of size
        self.align = "left"
        self.stroke = None
        self.stroke_width = 0.0
        self.xf = QTransform()
        # Original glyph shapes from an imported file, used while the text is
        # unedited and its font isn't installed: [[svg d, [a,b,c,d,e,f]], ...]
        self.outline = None
        self.outline_text = None
        self.missing_font = None

    def uses_outline(self):
        return bool(self.outline) and self.text == self.outline_text

    def font(self):
        f = QFont(self.family)
        f.setPixelSize(int(TEXT_UNIT))
        f.setHintingPreference(QFont.PreferNoHinting)  # exact widths: outlines get scaled anyway
        f.setBold(self.bold)
        f.setItalic(self.italic)
        if self.tracking:
            f.setLetterSpacing(QFont.AbsoluteSpacing, self.tracking * TEXT_UNIT / max(self.size, 0.01))
        return f

    def local_path(self):
        if "lpath" not in self._cache and self.uses_outline():
            from halo_svgpath import parse_d
            p = QPainterPath()
            for d, m in self.outline:
                p.addPath(xf_from_list(m).map(parse_d(d)))
            self._cache["lpath"] = p
            box = p.boundingRect()
            asc = self.size * 0.75
            box = box.united(QRectF(0, -asc, box.right(), asc + self.size * 0.25))
            self._cache["lbox"] = box
        if "lpath" not in self._cache:
            f = self.font()
            fm = QFontMetricsF(f)
            p = QPainterPath()
            lines = self.text.split("\n")
            widths = [fm.horizontalAdvance(l) for l in lines]
            wmax = max(widths) if widths else 0
            for i, line in enumerate(lines):
                x = 0.0
                if self.align == "center":
                    x = (wmax - widths[i]) / 2
                elif self.align == "right":
                    x = wmax - widths[i]
                p.addText(QPointF(x, i * TEXT_UNIT * self.leading), f, line)
            s = self.size / TEXT_UNIT
            p = QTransform.fromScale(s, s).map(p)
            self._cache["lpath"] = p
            # A box for picking and the selection frame, even for spaces
            asc, desc = fm.ascent() * s, fm.descent() * s
            box = QRectF(0, -asc, max(wmax * s, self.size * 0.3),
                         asc + desc + (len(lines) - 1) * self.size * self.leading)
            self._cache["lbox"] = box
        return self._cache["lpath"]

    def local_box(self):
        self.local_path()
        return self._cache["lbox"]

    def qpath(self):
        if "path" not in self._cache:
            self._cache["path"] = self.xf.map(self.local_path())
        return self._cache["path"]

    def bounds(self):
        return self.xf.mapRect(self.local_box())

    geom_bounds = bounds

    def hit(self, p, tol):
        inv, ok = self.xf.inverted()
        if not ok:
            return False
        lp = inv.map(p)
        return self.local_box().adjusted(-tol, -tol, tol, tol).contains(lp)

    def apply_transform(self, t):
        self.xf = self.xf * t
        super().apply_transform(t)

    def colors(self):
        return [c for c in (self.color, self.stroke) if c]

    def to_dict(self):
        d = self.base_dict()
        d.update(text=self.text, family=self.family, size=self.size, color=self.color,
                 bold=self.bold, italic=self.italic, tracking=self.tracking,
                 leading=self.leading, align=self.align, stroke=self.stroke,
                 stroke_width=self.stroke_width, xf=xf_to_list(self.xf),
                 outline=self.outline, outline_text=self.outline_text,
                 missing_font=self.missing_font)
        return d

    @staticmethod
    def from_dict(d):
        it = TextItem(d.get("text", ""), d.get("family", "Arial"), d.get("size", 24.0),
                      d.get("color", "#000000"))
        it.load_base(d)
        for k in ("bold", "italic", "tracking", "leading", "align", "stroke", "stroke_width",
                  "outline", "outline_text", "missing_font"):
            if k in d:
                setattr(it, k, d[k])
        it.xf = xf_from_list(d.get("xf", [1, 0, 0, 1, 0, 0]))
        return it

    def clone(self, keep_id=True):
        c = super().clone(keep_id)
        c.xf = QTransform(self.xf)
        return c

    def kind_label(self):
        t = self.text.replace("\n", " ")
        return "Text: " + (t[:24] + "..." if len(t) > 24 else t)


class RasterItem(Item):
    kind = "raster"

    def __init__(self, image=None):
        super().__init__()
        self.image = image if image is not None else QImage(1, 1, QImage.Format_ARGB32_Premultiplied)
        if self.image.format() != QImage.Format_ARGB32_Premultiplied:
            self.image = self.image.convertToFormat(QImage.Format_ARGB32_Premultiplied)
        self.xf = QTransform()

    def local_rect(self):
        return QRectF(0, 0, self.image.width(), self.image.height())

    def bounds(self):
        return self.xf.mapRect(self.local_rect())

    geom_bounds = bounds

    def qpath(self):
        p = QPainterPath()
        p.addRect(self.local_rect())
        return self.xf.map(p)

    def hit(self, p, tol):
        inv, ok = self.xf.inverted()
        if not ok:
            return False
        lp = inv.map(p)
        x, y = int(lp.x()), int(lp.y())
        if not (0 <= x < self.image.width() and 0 <= y < self.image.height()):
            return False
        # Only painted pixels count, so empty paint layers don't swallow clicks
        r = max(1, int(tol / max(math.hypot(self.xf.m11(), self.xf.m12()), 1e-6)))
        for yy in range(max(0, y - r), min(self.image.height(), y + r + 1), max(1, r)):
            for xx in range(max(0, x - r), min(self.image.width(), x + r + 1), max(1, r)):
                if (self.image.pixel(xx, yy) >> 24) & 0xFF > 8:
                    return True
        return False

    def apply_transform(self, t):
        self.xf = self.xf * t
        super().apply_transform(t)

    def to_dict(self):
        d = self.base_dict()
        d.update(xf=xf_to_list(self.xf), w=self.image.width(), h=self.image.height())
        return d

    @staticmethod
    def from_dict(d, image):
        it = RasterItem(image)
        it.load_base(d)
        it.xf = xf_from_list(d.get("xf", [1, 0, 0, 1, 0, 0]))
        return it

    def clone(self, keep_id=True):
        c = super().clone(keep_id)
        c.image = QImage(self.image)  # implicitly shared; copies on first write
        c.xf = QTransform(self.xf)
        return c

    def kind_label(self):
        return "Image %dx%d" % (self.image.width(), self.image.height())

    def pixel_scale(self):
        return math.hypot(self.xf.m11(), self.xf.m12())


class GroupItem(Item):
    """Objects kept together. A clip (from Item) makes it a clipping group,
    the way Illustrator's clipping masks work. `sublayer` groups show and
    behave like nested layers in the Layers panel."""
    kind = "group"

    def __init__(self, children=None, name=""):
        super().__init__()
        self.children = list(children or [])
        self.name = name
        self.sublayer = False

    def leaves(self):
        for c in self.children:
            if isinstance(c, GroupItem):
                yield from c.leaves()
            else:
                yield c

    def _union(self, attr):
        r = QRectF()
        for c in self.children:
            if c.visible:
                b = getattr(c, attr)()
                if not b.isNull():
                    r = r.united(b)
        clip = self.clip_qpath()
        if clip is not None and not r.isNull():
            r = r.intersected(clip.boundingRect())
        return r

    def bounds(self):
        return self._union("bounds")

    def geom_bounds(self):
        return self._union("geom_bounds")

    def qpath(self):
        p = QPainterPath()
        p.addRect(self.geom_bounds())
        return p

    def hit(self, p, tol):
        clip = self.clip_qpath()
        if clip is not None and not clip.boundingRect().adjusted(-tol, -tol, tol, tol).contains(p):
            return False
        if clip is not None and not clip.contains(p):
            return False
        return any(c.visible and c.hit(p, tol) for c in reversed(self.children))

    def apply_transform(self, t):
        for c in self.children:
            c.apply_transform(t)
        super().apply_transform(t)

    def colors(self):
        out = []
        for c in self.children:
            out += c.colors()
        return out

    def to_dict(self):
        d = self.base_dict()
        d.update(sublayer=self.sublayer, children=[c.to_dict() for c in self.children])
        return d

    def clone(self, keep_id=True):
        c = super().clone(keep_id)
        c.children = [k.clone(keep_id) for k in self.children]
        return c

    def kind_label(self):
        if self.sublayer:
            return "Sublayer"
        n = len(self.children)
        if self.clip:
            return "Clipping group (%d)" % n
        return "Group (%d)" % n

    def deep_rev(self):
        """Changes anywhere inside, for thumbnails."""
        return (id(self), self.rev, self.visible,
                tuple(c.deep_rev() if isinstance(c, GroupItem) else (id(c), c.rev, c.visible)
                      for c in self.children))


def item_from_dict(d, read_raster):
    """Rebuild any object from its saved form; read_raster(id) -> QImage."""
    k = d.get("kind")
    if k == "path":
        return PathItem.from_dict(d)
    if k == "text":
        return TextItem.from_dict(d)
    if k == "raster":
        return RasterItem.from_dict(d, read_raster(d["id"]))
    if k == "group":
        g = GroupItem()
        g.load_base(d)
        g.sublayer = d.get("sublayer", False)
        for cd in d.get("children", []):
            c = item_from_dict(cd, read_raster)
            if c is not None:
                g.children.append(c)
        return g
    return None


def walk_items(items):
    """Every object in a list, groups before their contents: (container, item)."""
    for it in items:
        yield items, it
        if isinstance(it, GroupItem):
            yield from walk_items(it.children)


def leaf_items(items):
    for it in items:
        if isinstance(it, GroupItem):
            yield from it.leaves()
        else:
            yield it


# ---------------------------------------------------------------- containers

LAYER_TAGS = ["#4f8dff", "#ff4f6d", "#3fc46b", "#ffaa33", "#b65cff", "#2fd1d1", "#ff66c4", "#c8c84a"]


class Layer:
    def __init__(self, name="Layer", tag=None):
        self.id = new_id("L")
        self.name = name
        self.visible = True
        self.locked = False
        self.opacity = 1.0
        self.items = []
        self.tag = tag or LAYER_TAGS[0]

    def to_dict(self, rasters):
        out = [it.to_dict() for it in self.items]
        for _, it in walk_items(self.items):
            if isinstance(it, RasterItem):
                rasters[it.id] = it.image
        return {"id": self.id, "name": self.name, "visible": self.visible, "locked": self.locked,
                "opacity": self.opacity, "tag": self.tag, "items": out}

    def clone(self, keep_ids=True):
        l = Layer(self.name, self.tag)
        if keep_ids:
            l.id = self.id
        l.visible, l.locked, l.opacity = self.visible, self.locked, self.opacity
        l.items = [it.clone(keep_ids) for it in self.items]
        return l


class Artboard:
    def __init__(self, name="Artboard 1", x=0.0, y=0.0, w=1024.0, h=1024.0):
        self.id = new_id("A")
        self.name = name
        self.rect = QRectF(x, y, w, h)
        self.background = None  # None = transparent (white on screen)

    def to_dict(self):
        r = self.rect
        return {"id": self.id, "name": self.name, "rect": [r.x(), r.y(), r.width(), r.height()],
                "background": self.background}

    @staticmethod
    def from_dict(d):
        a = Artboard(d.get("name", "Artboard"), *d.get("rect", [0, 0, 1024, 1024]))
        a.id = d.get("id", a.id)
        a.background = d.get("background")
        return a

    def clone(self):
        a = Artboard(self.name, self.rect.x(), self.rect.y(), self.rect.width(), self.rect.height())
        a.id = self.id
        a.background = self.background
        return a


class Frame:
    """One animation frame: how long it shows and each layer's state in it.
    A layer missing from `states` shows as the layer itself (visible as set
    in the Layers panel, no offset)."""

    def __init__(self, delay=100):
        self.delay = delay
        self.states = {}  # layer id -> {"v": bool, "dx", "dy", "op", "hue"}

    def state(self, layer):
        s = self.states.get(layer.id)
        if s is None:
            return {"v": layer.visible, "dx": 0.0, "dy": 0.0, "op": 1.0, "hue": 0.0}
        return {"v": s.get("v", layer.visible), "dx": s.get("dx", 0.0), "dy": s.get("dy", 0.0),
                "op": s.get("op", 1.0), "hue": s.get("hue", 0.0)}

    def set_state(self, layer, **kw):
        s = self.state(layer)
        s.update(kw)
        self.states[layer.id] = s

    def to_dict(self):
        return {"delay": self.delay, "states": self.states}

    @staticmethod
    def from_dict(d):
        f = Frame(d.get("delay", 100))
        f.states = {k: dict(v) for k, v in d.get("states", {}).items()}
        return f

    def clone(self):
        f = Frame(self.delay)
        f.states = {k: dict(v) for k, v in self.states.items()}
        return f


class Document:
    def __init__(self):
        self.artboards = []
        self.layers = []
        self.palettes = []          # palettes saved inside this document
        self.lock_palette = None    # name of the palette the piece is locked to
        self.frames = []
        self.loop = True
        self.guides = []            # [{"o": "h" | "v", "pos": document coordinate}]
        self.fonts = {}             # file name -> bytes (fonts carried from imports)
        self.import_notes = []     # only things that didn't come across
        self.import_info = ""

    @staticmethod
    def blank(w=1024, h=1024):
        d = Document()
        d.artboards.append(Artboard("Artboard 1", 0, 0, w, h))
        d.layers.append(Layer("Layer 1", LAYER_TAGS[0]))
        return d

    # -------------------------------------------------- lookups
    def all_items(self):
        """Every drawable object (not groups themselves), with its layer."""
        for l in self.layers:
            for it in leaf_items(l.items):
                yield l, it

    def walk(self):
        """Every object including groups: (layer, container list, item)."""
        for l in self.layers:
            for cont, it in walk_items(l.items):
                yield l, cont, it

    def find(self, item_id):
        for l, cont, it in self.walk():
            if it.id == item_id:
                return l, it
        return None, None

    def locate(self, item_id):
        """(layer, the list holding it, item), or Nones."""
        for l, cont, it in self.walk():
            if it.id == item_id:
                return l, cont, it
        return None, None, None

    def parent_of(self, item_id):
        """The group directly holding an object (None if it sits on a layer)."""
        for l, cont, it in self.walk():
            if isinstance(it, GroupItem) and any(c.id == item_id for c in it.children):
                return it
        return None

    def paint_order(self):
        """{id: position} in drawing order, bottom first."""
        return {it.id: i for i, (_, _, it) in enumerate(self.walk())}

    def layer_by_id(self, lid):
        for l in self.layers:
            if l.id == lid:
                return l
        return None

    def palette_by_name(self, name):
        for p in self.palettes:
            if p.name == name:
                return p
        return None

    def bounds(self):
        r = QRectF()
        for a in self.artboards:
            r = r.united(a.rect)
        return r

    def next_layer_tag(self):
        return LAYER_TAGS[len(self.layers) % len(LAYER_TAGS)]

    def used_colors(self, items=None):
        """{hex: count} over vector colours."""
        counts = {}
        src = items if items is not None else [it for _, it in self.all_items()]
        for it in src:
            for c in it.colors():
                counts[c] = counts.get(c, 0) + 1
        return counts

    # -------------------------------------------------- undo snapshots
    def snapshot(self):
        return {
            "artboards": [a.clone() for a in self.artboards],
            "layers": [l.clone() for l in self.layers],
            "palettes": [p.copy() for p in self.palettes],
            "lock_palette": self.lock_palette,
            "frames": [f.clone() for f in self.frames],
            "loop": self.loop,
            "guides": [dict(g) for g in self.guides],
        }

    def restore(self, s):
        self.artboards = [a.clone() for a in s["artboards"]]
        self.layers = [l.clone() for l in s["layers"]]
        self.palettes = [p.copy() for p in s["palettes"]]
        self.lock_palette = s["lock_palette"]
        self.frames = [f.clone() for f in s["frames"]]
        self.loop = s["loop"]
        self.guides = [dict(g) for g in s.get("guides", [])]

    # -------------------------------------------------- files
    def save(self, path):
        rasters = {}
        doc = {
            "app": "Nous Halo", "version": 1,
            "artboards": [a.to_dict() for a in self.artboards],
            "layers": [l.to_dict(rasters) for l in self.layers],
            "palettes": [p.to_dict() for p in self.palettes],
            "lock_palette": self.lock_palette,
            "frames": [f.to_dict() for f in self.frames],
            "loop": self.loop,
            "guides": self.guides,
            "fonts": sorted(self.fonts),
        }
        tmp = path + ".saving"
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("document.json", json.dumps(doc))
            for rid, img in rasters.items():
                z.writestr("rasters/%s.png" % rid, image_to_png(img), compress_type=zipfile.ZIP_STORED)
            for fn, data in self.fonts.items():
                z.writestr("fonts/" + fn, data)
        import os
        os.replace(tmp, path)

    @staticmethod
    def load(path):
        with zipfile.ZipFile(path) as z:
            d = json.loads(z.read("document.json"))
            doc = Document()
            for fn in d.get("fonts", []):
                try:
                    doc.fonts[fn] = z.read("fonts/" + fn)
                except KeyError:
                    pass
            register_fonts(doc.fonts)
            doc.artboards = [Artboard.from_dict(a) for a in d.get("artboards", [])]
            ids = [a.id for a in doc.artboards]
            for ld in d.get("layers", []):
                l = Layer(ld.get("name", "Layer"), ld.get("tag"))
                l.id = ld.get("id", l.id)
                l.visible = ld.get("visible", True)
                l.locked = ld.get("locked", False)
                l.opacity = ld.get("opacity", 1.0)
                ids.append(l.id)
                def read_raster(rid):
                    img = QImage()
                    img.loadFromData(z.read("rasters/%s.png" % rid), "PNG")
                    return img
                for idd in ld.get("items", []):
                    it = item_from_dict(idd, read_raster)
                    if it is not None:
                        l.items.append(it)
                ids += [it.id for _, it in walk_items(l.items)]
                doc.layers.append(l)
            doc.palettes = [Palette.from_dict(p) for p in d.get("palettes", [])]
            doc.lock_palette = d.get("lock_palette")
            doc.frames = [Frame.from_dict(f) for f in d.get("frames", [])]
            doc.loop = d.get("loop", True)
            doc.guides = [g for g in d.get("guides", []) if g.get("o") in ("h", "v")]
        _bump_ids(ids)
        if not doc.artboards:
            doc.artboards.append(Artboard())
        if not doc.layers:
            doc.layers.append(Layer("Layer 1"))
        return doc


def image_to_png(img):
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(ba)


_registered = {}


def register_fonts(fonts):
    """Load fonts carried in a document into Qt. Returns {file: family}."""
    out = {}
    for fn, data in fonts.items():
        if fn in _registered:
            out[fn] = _registered[fn]
            continue
        fid = QFontDatabase.addApplicationFontFromData(QByteArray(data))
        fams = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
        _registered[fn] = fams[0] if fams else None
        out[fn] = _registered[fn]
    return out
