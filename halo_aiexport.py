"""Export as a PDF-compatible .ai: the same kind of file Illustrator saves by
default (a PDF inside an .ai name). Illustrator, Acrobat, Affinity, Inkscape
and Nous Halo itself open it.

Each artboard is a page. Layers become PDF layers (optional content), text
stays live text in its embedded font, and paths keep fills, strokes, dashes,
fill rules, opacity and clipping masks. Text whose font isn't installed is
written as outlines (it keeps its look). Halo-only things, like animation
frames and the palette lock, don't go into the file.
"""
import os
import struct

import pymupdf
from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QFont, QFontDatabase, QTransform

from halo_color import hex_to_rgb
from halo_model import GroupItem, PathItem, RasterItem, TextItem, image_to_png, leaf_items


def _n(v):
    s = ("%.4f" % v).rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _rgb(hexcol):
    return " ".join(_n(c) for c in hex_to_rgb(hexcol))


def _path_ops(subpaths):
    out = []
    for sp in subpaths:
        nodes = sp["nodes"]
        if not nodes:
            continue
        out.append("%s %s m" % (_n(nodes[0][0]), _n(nodes[0][1])))
        segs = list(zip(nodes, nodes[1:]))
        if sp.get("closed") and len(nodes) > 1:
            segs.append((nodes[-1], nodes[0]))
        for a, b in segs:
            if a[4] == a[0] and a[5] == a[1] and b[2] == b[0] and b[3] == b[1]:
                out.append("%s %s l" % (_n(b[0]), _n(b[1])))
            else:
                out.append(" ".join(_n(v) for v in (a[4], a[5], b[2], b[3], b[0], b[1])) + " c")
        if sp.get("closed"):
            out.append("h")
    return "\n".join(out)


def _qpath_ops(path):
    from halo_model import qpath_to_subpaths
    return _path_ops(qpath_to_subpaths(path))


def _cm(t):
    return " ".join(_n(v) for v in (t.m11(), t.m12(), t.m21(), t.m22(), t.dx(), t.dy())) + " cm"


# ---------------------------------------------------------------- font files

_font_index = None


def _name_table(data, offset=0):
    """(family, subfamily) from a TrueType/OpenType font's name table."""
    num = struct.unpack(">H", data[offset + 4:offset + 6])[0]
    tables = {}
    for i in range(num):
        rec = data[offset + 12 + 16 * i: offset + 28 + 16 * i]
        tag = rec[:4]
        _, off, ln = struct.unpack(">III", rec[4:16])
        tables[tag] = (off, ln)
    if b"name" not in tables:
        return None, None
    off, _ = tables[b"name"]
    count, sto = struct.unpack(">HH", data[off + 2:off + 6])
    names = {}
    for i in range(count):
        pid, eid, lid, nid, ln, so = struct.unpack(">HHHHHH", data[off + 6 + 12 * i: off + 18 + 12 * i])
        if nid not in (1, 2, 16, 17):
            continue
        raw = data[off + sto + so: off + sto + so + ln]
        if pid == 3:
            if lid not in (0x409, 0):
                if nid in names:
                    continue
            try:
                names[nid] = raw.decode("utf-16-be")
            except UnicodeDecodeError:
                pass
        elif pid == 1 and nid not in names:
            names[nid] = raw.decode("latin1")
    return names.get(16) or names.get(1), names.get(17) or names.get(2)


def font_index():
    """{family lower: [(subfamily lower, path, collection index)]} for installed fonts."""
    global _font_index
    if _font_index is not None:
        return _font_index
    idx = {}
    dirs = [os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts"),
            "/usr/share/fonts", os.path.expanduser("~/.fonts"), "/Library/Fonts",
            os.path.expanduser("~/Library/Fonts"), "/System/Library/Fonts"]
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        for root, _, files in os.walk(d):
            for fn in files:
                if not fn.lower().endswith((".ttf", ".otf", ".ttc")):
                    continue
                path = os.path.join(root, fn)
                try:
                    with open(path, "rb") as f:
                        head = f.read(12)
                        if head[:4] == b"ttcf":
                            n = struct.unpack(">I", head[8:12])[0]
                            f.seek(0)
                            data = f.read()
                            offs = struct.unpack(">%dI" % n, data[12:12 + 4 * n])
                        else:
                            f.seek(0)
                            data = f.read()
                            offs = [0]
                    for k, o in enumerate(offs):
                        fam, sub = _name_table(data, o)
                        if fam:
                            idx.setdefault(fam.lower(), []).append(((sub or "").lower(), path, k))
                except (OSError, struct.error, IndexError):
                    continue
    _font_index = idx
    return idx


def find_font_bytes(item, doc):
    """Bytes of the font file for a text object, or None."""
    fam = item.family.lower()
    # fonts carried in from an imported file
    from halo_model import register_fonts
    for fn, regfam in register_fonts(doc.fonts).items():
        if regfam and regfam.lower() == fam:
            return doc.fonts[fn]
    cands = font_index().get(fam)
    if not cands:
        return None
    want_b, want_i = item.bold, item.italic

    def score(c):
        sub = c[0]
        b = any(w in sub for w in ("bold", "black", "heavy", "semibold"))
        i = any(w in sub for w in ("italic", "oblique"))
        return (b == want_b) * 2 + (i == want_i) * 2 + (sub in ("regular", "roman", "book", "normal")) * 0.5
    sub, path, k = max(cands, key=score)
    if k != 0:
        return None  # second+ face of a .ttc collection: written as outlines
    with open(path, "rb") as f:
        return f.read()


# ---------------------------------------------------------------- export

class _Page:
    def __init__(self, doc, page):
        self.doc = doc
        self.page = page
        self.ops = []
        self.gstates = {}
        self.props = {}
        self.fonts = {}   # key -> (resource name, pymupdf.Font)

    def gs(self, ca, CA):
        key = (round(ca, 3), round(CA, 3))
        if key not in self.gstates:
            self.gstates[key] = "GS%d" % len(self.gstates)
        return self.gstates[key]


def export_ai(hdoc, path, artboards=None):
    """Write `hdoc` as a PDF-compatible .ai. Returns the path."""
    out = pymupdf.open()
    ocgs = {}
    for layer in hdoc.layers:
        ocgs[layer.id] = out.add_ocg(layer.name, on=layer.visible)
    font_cache = {}
    for ab in (artboards or hdoc.artboards):
        r = ab.rect
        page = out.new_page(width=r.width(), height=r.height())
        pg = _Page(out, page)
        images = {}
        for layer in hdoc.layers:
            for it in leaf_items(layer.items):
                if isinstance(it, RasterItem) and it.visible and r.intersects(it.bounds()):
                    x = page.insert_image(pymupdf.Rect(0, 0, 1, 1), stream=image_to_png(it.image))
                    name = [im[7] for im in page.get_images(full=True) if im[0] == x][0]
                    images[it.id] = name
        ops = pg.ops
        # PDF space is y-up from the page's bottom-left; flip into ours once
        ops.append("1 0 0 -1 %s %s cm" % (_n(-r.x()), _n(r.y() + r.height())))
        if ab.background:
            ops.append("%s rg %s %s %s %s re f" % (_rgb(ab.background), _n(r.x()), _n(r.y()),
                                                  _n(r.width()), _n(r.height())))
        for layer in hdoc.layers:
            items = [it for it in layer.items if it.visible and r.intersects(it.bounds())]
            if not items:
                continue
            mc = "MC%d" % len(pg.props)
            pg.props[mc] = ocgs[layer.id]
            ops.append("/OC /%s BDC" % mc)
            if layer.opacity < 1:
                ops.append("q /%s gs" % pg.gs(layer.opacity, layer.opacity))
            for it in items:
                _item_ops(pg, hdoc, it, images, font_cache)
            if layer.opacity < 1:
                ops.append("Q")
            ops.append("EMC")
        _finish_page(pg)
    out.set_metadata({"creator": "Nous Halo", "producer": "Nous Halo (PyMuPDF)",
                      "title": os.path.splitext(os.path.basename(path))[0]})
    try:
        out.subset_fonts()
    except Exception:
        pass
    tmp = path + ".saving"
    out.save(tmp, garbage=3, deflate=True)
    out.close()
    os.replace(tmp, path)
    return path


def _item_ops(pg, hdoc, it, images, font_cache):
    ops = pg.ops
    ops.append("q")
    if it.clip:
        ops.append(_path_ops(it.clip))
        ops.append("W n")
    if it.opacity < 1:
        ops.append("/%s gs" % pg.gs(it.opacity, it.opacity))
    if isinstance(it, GroupItem):
        for c in it.children:
            if c.visible:
                _item_ops(pg, hdoc, c, images, font_cache)
    elif isinstance(it, PathItem):
        body = _path_ops(it.subpaths)
        fill, stroke = it.fill, it.stroke if it.width > 0 else None
        if fill:
            ops.append("%s rg" % _rgb(fill))
        if stroke:
            ops.append("%s RG %s w %d J %d j 10 M" % (_rgb(stroke), _n(it.width), it.cap, it.join))
            if it.dashes:
                ops.append("[%s] 0 d" % " ".join(_n(d) for d in it.dashes))
        star = "*" if it.even_odd else ""
        if fill and stroke:
            op = "B" + star
        elif fill:
            op = "f" + star
        elif stroke:
            op = "S"
        else:
            op = "n"
        ops.append(body)
        ops.append(op)
    elif isinstance(it, RasterItem) and it.id in images:
        w, h = it.image.width(), it.image.height()
        # image space: unit square, row 0 at the top (t = 1)
        t = QTransform(w, 0, 0, -h, 0, h) * it.xf
        ops.append(_cm(t))
        ops.append("/%s Do" % images[it.id])
    elif isinstance(it, TextItem):
        _text_ops(pg, hdoc, it, font_cache)
    ops.append("Q")


def _text_ops(pg, hdoc, it, font_cache):
    ops = pg.ops
    buf = None if it.uses_outline() else find_font_bytes(it, hdoc)
    if buf is None:
        # no font file to embed (or original glyph shapes): keep the look as outlines
        if it.color:
            ops.append("%s rg" % _rgb(it.color))
        ops.append(_qpath_ops(it.qpath()))
        ops.append("f" if it.color else "n")
        return
    key = id(buf) if not isinstance(buf, (bytes, bytearray)) else hash(bytes(buf[:4096])) ^ len(buf)
    if key not in pg.fonts:
        name = "HF%d" % len(pg.fonts)
        fx = pg.page.insert_font(fontname=name, fontbuffer=buf)
        _rename_font(pg.doc, fx, it)
        if key not in font_cache:
            font_cache[key] = pymupdf.Font(fontbuffer=buf)
        pg.fonts[key] = (name, font_cache[key])
    name, font = pg.fonts[key]
    lines = it.text.split("\n")
    # letter positions in ems as Qt lays them out (kerning, no tracking)
    from PySide6.QtGui import QFontMetricsF
    from halo_model import TEXT_UNIT
    qf = it.font()
    qf.setLetterSpacing(QFont.AbsoluteSpacing, 0)
    fm = QFontMetricsF(qf)
    pos = [[fm.horizontalAdvance(l[:k]) / TEXT_UNIT for k in range(len(l) + 1)] for l in lines]
    widths = [pp[-1] * it.size + it.tracking * max(0, len(l) - 1) for pp, l in zip(pos, lines)]
    wmax = max(widths) if widths else 0
    mode = 0
    if it.color and it.stroke and it.stroke_width:
        mode = 2
    elif it.stroke and it.stroke_width and not it.color:
        mode = 1
    if it.color:
        ops.append("%s rg" % _rgb(it.color))
    if it.stroke and it.stroke_width:
        ops.append("%s RG %s w" % (_rgb(it.stroke), _n(it.stroke_width / max(it.size, 0.01))))
    ops.append("BT")
    ops.append("/%s 1 Tf %d Tr" % (name, mode))
    if it.tracking:
        ops.append("%s Tc" % _n(it.tracking / max(it.size, 0.01)))
    for i, line in enumerate(lines):
        dx = 0.0
        if it.align == "center":
            dx = (wmax - widths[i]) / 2
        elif it.align == "right":
            dx = wmax - widths[i]
        # text space is y-up at 1 unit = 1 em; our local space is y-down
        m = QTransform(it.size, 0, 0, -it.size, dx, i * it.size * it.leading) * it.xf
        ops.append(" ".join(_n(v) for v in (m.m11(), m.m12(), m.m21(), m.m22(), m.dx(), m.dy())) + " Tm")
        # Place every letter where Qt draws it on the canvas (kerning
        # included): glyph, then the nudge to the next letter's start
        parts = []
        for k, ch in enumerate(line):
            g = font.has_glyph(ord(ch)) or font.has_glyph(ord("?")) or 0
            parts.append("<%04x>" % g)
            if k < len(line) - 1:
                adj = (pos[i][k] + font.glyph_advance(ord(ch)) - pos[i][k + 1]) * 1000
                if abs(adj) > 0.5:
                    parts.append(_n(adj))
        ops.append("[%s] TJ" % " ".join(parts))
    ops.append("ET")


def _rename_font(doc, fx, it):
    """PostScript-style font name (FamilyName-Style): what Illustrator and
    other readers look up, and short enough not to get cut off."""
    style = ("Bold" if it.bold else "") + ("Italic" if it.italic else "") or "Regular"
    ps = "".join(ch for ch in it.family if ch.isalnum()) + "-" + style
    try:
        doc.xref_set_key(fx, "BaseFont", "/" + ps)
        kind, val = doc.xref_get_key(fx, "DescendantFonts")
        if kind == "array":
            dx = int(val.strip("[]").split()[0])
            doc.xref_set_key(dx, "BaseFont", "/" + ps)
            k2, fd = doc.xref_get_key(dx, "FontDescriptor")
            if k2 == "xref":
                doc.xref_set_key(int(fd.split()[0]), "FontName", "/" + ps)
    except Exception:
        pass


def _finish_page(pg):
    doc, page = pg.doc, pg.page
    content = "\n".join(pg.ops).encode("latin1")
    x = doc.get_new_xref()
    doc.update_object(x, "<<>>")
    doc.update_stream(x, content)
    doc.xref_set_key(page.xref, "Contents", "%d 0 R" % x)
    kind, val = doc.xref_get_key(page.xref, "Resources")
    if kind == "xref":
        res = int(val.split()[0])
    else:
        res = doc.get_new_xref()
        doc.update_object(res, val if kind == "dict" else "<<>>")
        doc.xref_set_key(page.xref, "Resources", "%d 0 R" % res)
    if pg.gstates:
        parts = []
        for (ca, CA), name in pg.gstates.items():
            parts.append("/%s <</Type/ExtGState/ca %s/CA %s>>" % (name, _n(ca), _n(CA)))
        doc.xref_set_key(res, "ExtGState", "<<%s>>" % "".join(parts))
    if pg.props:
        doc.xref_set_key(res, "Properties", "<<%s>>" % "".join("/%s %d 0 R" % (k, v) for k, v in pg.props.items()))
