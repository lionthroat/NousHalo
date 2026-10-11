"""Bringing .ai (and .pdf / .svg / .eps-less images) into Nous Halo.

Illustrator saves a PDF copy of the artwork inside every .ai file (the
"Create PDF Compatible File" option, on by default). PyMuPDF reads that copy:
each page is an artboard, each optional-content group is a layer, and the
page's drawing operations become paths, text and images in their real
stacking order. Gradients and mesh fills have no vector equivalent here and
come in as flat colour (noted in the import report).
"""
import math
import os
import re

import pymupdf
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QFontDatabase, QFontMetricsF, QImage, QTransform

from halo_color import rgb_to_hex
from halo_model import (Artboard, Document, Layer, PathItem, RasterItem, TextItem,
                        corner, register_fonts)

ARTBOARD_GAP = 72.0
EPS = 0.01


class ImportError_(Exception):
    pass


def _col(c):
    if c is None:
        return None
    if len(c) == 1:
        c = (c[0], c[0], c[0])
    elif len(c) == 4:  # CMYK
        cc, m, y, k = c
        c = ((1 - cc) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))
    return rgb_to_hex(c)


def _pt(p, ox):
    return (p.x + ox, p.y)


def drawing_subpaths(d, ox=0.0):
    subs = []
    cur = None

    def start(x, y):
        nonlocal cur
        cur = {"nodes": [corner(x, y)], "closed": False}
        subs.append(cur)

    def last():
        return cur["nodes"][-1] if cur and cur["nodes"] else None

    for it in d["items"]:
        op = it[0]
        if op == "l":
            (x1, y1), (x2, y2) = _pt(it[1], ox), _pt(it[2], ox)
            n = last()
            if n is None or abs(n[0] - x1) > EPS or abs(n[1] - y1) > EPS:
                start(x1, y1)
            cur["nodes"].append(corner(x2, y2))
        elif op == "c":
            (x1, y1), (c1x, c1y), (c2x, c2y), (x2, y2) = (_pt(q, ox) for q in it[1:5])
            n = last()
            if n is None or abs(n[0] - x1) > EPS or abs(n[1] - y1) > EPS:
                start(x1, y1)
                n = last()
            n[4], n[5] = c1x, c1y
            cur["nodes"].append([x2, y2, c2x, c2y, x2, y2])
        elif op == "re":
            r = it[1]
            subs.append({"nodes": [corner(r.x0 + ox, r.y0), corner(r.x1 + ox, r.y0),
                                   corner(r.x1 + ox, r.y1), corner(r.x0 + ox, r.y1)],
                         "closed": True})
            cur = None
        elif op == "qu":
            q = it[1]
            subs.append({"nodes": [corner(*_pt(q.ul, ox)), corner(*_pt(q.ur, ox)),
                                   corner(*_pt(q.lr, ox)), corner(*_pt(q.ll, ox))],
                         "closed": True})
            cur = None
    # Fills always close; strokes close when PDF said so or the ends meet
    for sp in subs:
        nodes = sp["nodes"]
        if len(nodes) > 2:
            a, b = nodes[0], nodes[-1]
            if abs(a[0] - b[0]) < EPS and abs(a[1] - b[1]) < EPS:
                a[2], a[3] = b[2], b[3]
                nodes.pop()
                sp["closed"] = True
    if d.get("closePath") and subs:
        subs[-1]["closed"] = True
    if "f" in d.get("type", ""):
        for sp in subs:
            if len(sp["nodes"]) > 2:
                sp["closed"] = sp["closed"] or d.get("type") == "f"
    return [s for s in subs if s["nodes"]]


def _parse_dashes(s):
    if not s:
        return None
    m = re.match(r"\[\s*([^\]]*)\]", s)
    if not m or not m.group(1).strip():
        return None
    try:
        v = [float(x) for x in m.group(1).split()]
    except ValueError:
        return None
    return v if any(v) else None


# ---------------------------------------------------------------- fonts

def _norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


_family_index = None


def _families():
    global _family_index
    if _family_index is None:
        _family_index = {_norm(f): f for f in QFontDatabase.families()}
    return _family_index


STYLE_WORDS = ["bolditalic", "boldoblique", "semibold", "demibold", "extrabold", "ultrabold",
               "bold", "black", "heavy", "medium", "light", "thin", "regular", "roman", "book",
               "italic", "oblique", "condensed", "narrow", "mt", "ps", "std", "pro", "lt"]


def resolve_font(pdfname, embedded_families):
    """PDF font name ('ABCDEF+PalatinoLinotype-Roman') -> (family, bold, italic, found)."""
    name = pdfname.split("+", 1)[-1]
    base, _, style = name.partition("-")
    st = _norm(style + (" " + base if not style else ""))
    bold = any(w in _norm(name) for w in ("bold", "black", "heavy", "semibold", "demi"))
    italic = any(w in _norm(name) for w in ("italic", "oblique"))
    fams = _families()
    cands = [_norm(base), _norm(name)]
    b = _norm(base)
    for w in STYLE_WORDS:
        if b.endswith(w) and len(b) > len(w) + 2:
            cands.append(b[: -len(w)])
    for c in cands:
        if c in fams:
            return fams[c], bold, italic, True
    # prefix match, e.g. "zoojalightpro" vs "zooja light pro"
    for c in cands:
        for k, f in fams.items():
            if len(c) > 4 and (k.startswith(c) or c.startswith(k)) and abs(len(k) - len(c)) < 6:
                return f, bold, italic, True
    # names cut short or with the style glued on ("Palatino Linotype Regula"):
    # the longest installed family the name starts with
    best = None
    for k, f in fams.items():
        if len(k) >= 4 and _norm(name).startswith(k) and (best is None or len(k) > len(best[0])):
            best = (k, f)
    if best:
        return best[1], bold, italic, True
    for fam in embedded_families:
        if fam and _norm(fam) in cands:
            return fam, bold, italic, True
    return None, bold, italic, False


# ---------------------------------------------------------------- import

def open_artwork(path):
    try:
        src = pymupdf.open(path)
    except Exception as e:
        raise ImportError_("Couldn't read %s: %s" % (os.path.basename(path), e))
    if not src.is_pdf:
        # SVG and friends: let MuPDF turn them into a PDF page first
        try:
            src = pymupdf.open("pdf", src.convert_to_pdf())
        except Exception as e:
            raise ImportError_("Couldn't convert %s: %s" % (os.path.basename(path), e))
    if src.page_count == 1:
        t = src[0].get_text()[:400].lower()
        if "saved without pdf content" in t or "without pdf compatibility" in t:
            raise ImportError_(
                "This .ai file was saved without \"Create PDF Compatible File\", so it has no "
                "readable artwork. Re-save it from Illustrator with that option on.")
    return src


def _import_native(native, path, doc, mediabox=None, encoding=None):
    from halo_ainative import parse_native
    name = os.path.splitext(os.path.basename(path))[0]
    x0 = 0.0
    if doc is not None and doc.artboards:
        x0 = max(a.rect.right() for a in doc.artboards) + ARTBOARD_GAP
    doc, notes = parse_native(native, name, mediabox, doc, x0, encoding)
    if not doc.layers:
        doc.layers.append(Layer("Layer 1"))
    if not any(l.items for l in doc.layers):
        raise ImportError_("Found Illustrator data in %s, but no artwork Nous Halo can read." % os.path.basename(path))
    doc.import_notes = notes
    doc.import_info = "read from Illustrator's own drawing data"
    return doc


def import_file(path, doc=None):
    """Import into a new Document (or append artboards to `doc`)."""
    with open(path, "rb") as f:
        head = f.read(8)
    if head[:4] in (b"%!PS", b"\xc5\xd0\xd3\xc6"):
        # EPS, or an old/Adobe Stock .ai that is really EPS
        from halo_ainative import native_from_eps, text_encoding
        with open(path, "rb") as f:
            data = f.read()
        native = native_from_eps(data)
        if not native:
            raise ImportError_("%s is a PostScript (EPS) file without Illustrator's drawing data, which "
                               "Nous Halo can't read." % os.path.basename(path))
        from halo_ainative import eps_postscript
        return _import_native(native, path, doc, encoding=text_encoding(eps_postscript(data)))
    src = open_artwork(path)
    if src.is_pdf and src.page_count and all(not p.get_bboxlog() for p in src.pages()):
        # an empty PDF page: Photopea and friends keep the art in private data
        from halo_ainative import native_from_pdf_private
        native = native_from_pdf_private(src)
        if native:
            r = src[0].rect
            return _import_native(native, path, doc, (r.width, r.height))
    new = doc is None
    if new:
        doc = Document()
    notes = []

    # Hidden Illustrator layers are switched-off optional content; turn
    # everything on to read it, then hide those layers again.
    hidden = set()
    try:
        ocgs = src.get_ocgs()
        for xref, info in ocgs.items():
            if not info.get("on", True):
                hidden.add(info.get("name"))
        # (set_layer doesn't switch the live view; the layer UI calls do)
        for cfg in src.layer_ui_configs():
            if not cfg.get("on"):
                src.set_layer_ui_config(cfg["number"], action=0)
    except Exception:
        ocgs = {}

    # Fonts carried inside the file (TrueType/OpenType only; Qt can't load bare CFF)
    embedded_fams = []
    seen_font = set()
    for page in src.pages():
        for xref, ext, ftype, basefont, *_ in page.get_fonts(full=True):
            if xref in seen_font:
                continue
            seen_font.add(xref)
            try:
                fname, fext, _, buf = src.extract_font(xref)
            except Exception:
                continue
            if buf and fext in ("ttf", "otf"):
                # A subset copy of an installed font would shadow the real one
                # (missing letters, different spacing), so only keep fonts
                # this computer doesn't have
                try:
                    from halo_aiexport import _name_table
                    fam = _name_table(buf)[0]
                except Exception:
                    fam = None
                if fam and _norm(fam) in _families():
                    continue
                key = "%s.%s" % (re.sub(r"[^A-Za-z0-9_.+-]", "_", basefont), fext)
                doc.fonts[key] = buf
    for fam in register_fonts(doc.fonts).values():
        if fam:
            embedded_fams.append(fam)

    layers = {l.name: l for l in doc.layers}
    order_seen = []

    def layer_for(name):
        name = name or "Layer 1"
        if name not in layers:
            l = Layer(name, doc.next_layer_tag())
            l.visible = name not in hidden
            layers[name] = l
            doc.layers.append(l)
        if name not in order_seen:
            order_seen.append(name)
        return layers[name]

    # Illustrator's own data has the groups and sublayers the PDF copy lacks
    from halo_aigroups import NativeTree, group_page, native_from_ai_pdf
    tree = None
    ungrouped_pages = 0
    native = native_from_ai_pdf(src)
    if native:
        try:
            tree = NativeTree(native)
        except Exception:
            tree = None
    x0 = 0.0
    if doc.artboards:
        x0 = max(a.rect.right() for a in doc.artboards) + ARTBOARD_GAP
    missing_fonts = set()
    gradients = 0
    base = os.path.splitext(os.path.basename(path))[0]
    for pno, page in enumerate(src.pages()):
        pr = page.rect
        ab = Artboard(base if src.page_count == 1 else "%s %d" % (base, pno + 1),
                      x0, 0, pr.width, pr.height)
        doc.artboards.append(ab)
        ox = x0 - pr.x0
        placed = []  # (seqno, layer, item)

        # Paths, with their clipping and group-opacity context
        ext = page.get_drawings(extended=True)
        stack = []  # (level, kind, value)
        gap_clips = {}  # seqno of next drawing -> clips seen just before it
        draw_seqs = []
        pending = []
        for e in ext:
            lvl = e.get("level", 0)
            while stack and stack[-1][0] >= lvl:
                stack.pop()
            t = e["type"]
            if t == "clip":
                sp = drawing_subpaths(e, ox)
                stack.append((lvl, "clip", sp))
                pending.append((e.get("scissor"), sp, list(stack)))
                continue
            if t == "group":
                stack.append((lvl, "group", e.get("opacity", 1.0) or 1.0))
                continue
            seq = e.get("seqno", 0)
            draw_seqs.append(seq)
            if pending:
                gap_clips[seq] = pending
                pending = []
            subs = drawing_subpaths(e, ox)
            if not subs:
                continue
            it = PathItem(subs, _col(e.get("fill")) if "f" in t else None,
                          _col(e.get("color")) if "s" in t else None,
                          float(e.get("width") or 0) if "s" in t else 1.0)
            it.even_odd = bool(e.get("even_odd"))
            lc = e.get("lineCap")
            if isinstance(lc, (list, tuple)) and lc:
                it.cap = int(lc[0]) if 0 <= int(lc[0]) <= 2 else 0
            elif isinstance(lc, (int, float)):
                it.cap = int(lc)
            lj = e.get("lineJoin")
            if lj is not None:
                it.join = int(lj) if 0 <= int(lj) <= 2 else 0
            it.dashes = _parse_dashes(e.get("dashes"))
            op = 1.0
            if "f" in t and e.get("fill_opacity") is not None:
                op = e["fill_opacity"]
            elif "s" in t and e.get("stroke_opacity") is not None:
                op = e["stroke_opacity"]
            for _, kind, v in stack:
                if kind == "group":
                    op *= v
            it.opacity = max(0.0, min(1.0, op))
            clips = [v for _, kind, v in stack if kind == "clip"]
            it.clip = _combine_clips(clips, ab.rect)
            placed.append((seq, layer_for(e.get("layer")), it))
        trailing = pending

        # Bounding-box log: one entry per paint operation, in order. Images
        # and gradients are found here.
        blog = page.get_bboxlog(layers=True)
        images = page.get_image_info(xrefs=True)
        img_iter = iter(images)
        for seq, entry in enumerate(blog):
            kind = entry[0]
            lname = entry[2] if len(entry) > 2 else None
            if kind == "fill-image":
                info = next(img_iter, None)
                if info is None:
                    continue
                it = _image_item(src, page, info, ox)
                if it is None:
                    continue
                bb = QRectF(info["bbox"][0] + ox, info["bbox"][1],
                            info["bbox"][2] - info["bbox"][0], info["bbox"][3] - info["bbox"][1])
                cand = _clips_before(gap_clips, draw_seqs, seq, trailing)
                use = [sp for scissor, sp, _ in cand if scissor is not None and
                       QRectF(bb).adjusted(-1, -1, 1, 1).contains(
                           QRectF(scissor.x0 + ox, scissor.y0, scissor.width, scissor.height))]
                it.clip = _combine_clips(use, ab.rect)
                placed.append((seq, layer_for(lname), it))
            elif kind == "fill-shade":
                gradients += 1

        # Text, one object per run of the same font, size and colour
        spans = [sp for sp in page.get_texttrace() if sp.get("type") != 3 and sp.get("chars")]
        glyphs = None
        for span in spans:
            fam, _, _, found = resolve_font(span.get("font", ""), embedded_fams)
            if not found and glyphs is None:
                glyphs = page_glyphs(src, pno)
            for it in _text_items(span, ox, embedded_fams, missing_fonts, glyphs or {}):
                placed.append((span.get("seqno", 0), layer_for(span.get("layer")), it))

        placed.sort(key=lambda t: t[0])
        grouped = None
        if tree is not None and placed:
            try:
                grouped = group_page(placed, tree, layers)
            except Exception:
                grouped = None
            if grouped is None:
                ungrouped_pages += 1
        if grouped is not None:
            for layer in {id(l): l for _, l, _ in placed}.values():
                layer.items.extend(grouped.get(layer.id, []))
        else:
            for _, layer, it in placed:
                layer.items.append(it)
        x0 = ab.rect.right() + ARTBOARD_GAP

    # Layers draw bottom-to-top in the order the artwork first used them
    if new:
        doc.layers.sort(key=lambda l: order_seen.index(l.name) if l.name in order_seen else 999)
        if not doc.layers:
            doc.layers.append(Layer("Layer 1"))
    for name in hidden:
        if name in layers:
            layers[name].visible = False

    if missing_fonts:
        notes.append("Fonts not on this computer: " + ", ".join(sorted(missing_fonts)) + ". That text still "
                     "looks right and stays editable; it switches to a stand-in font only once you edit it "
                     "(you can pick the font in Properties).")
    if gradients:
        notes.append("%d gradient or mesh fill%s left out (Nous Halo doesn't do gradients yet), so "
                     "those shapes are missing." % (gradients, "" if gradients == 1 else "s"))
    if ungrouped_pages:
        notes.append("Groups couldn't be matched on %d artboard%s, so those objects came in ungrouped "
                      "(they look the same)." % (ungrouped_pages, "" if ungrouped_pages == 1 else "s"))
    doc.import_notes = notes
    doc.import_info = ""
    return doc


def _clips_before(gap_clips, draw_seqs, seq, trailing):
    """Clips opened between the drawing before `seq` and the one after it:
    the only ones that can be in force for an image painted at `seq`."""
    nxt = [k for k in draw_seqs if k > seq]
    if not nxt:
        return trailing
    return gap_clips.get(min(nxt), [])


def _combine_clips(clips, artboard_rect):
    """Keep the innermost clip that actually cuts something; skip clips that
    are just the artboard (export crops to the artboard anyway)."""
    useful = []
    for sp in clips:
        if len(sp) == 1 and len(sp[0]["nodes"]) == 4:
            xs = [n[0] for n in sp[0]["nodes"]]
            ys = [n[1] for n in sp[0]["nodes"]]
            r = QRectF(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))
            if r.adjusted(-1.5, -1.5, 1.5, 1.5).contains(artboard_rect):
                continue
        useful.append(sp)
    return useful[-1] if useful else None


def _image_item(src, page, info, ox):
    xref = info.get("xref", 0)
    pix = None
    try:
        if xref:
            pix = pymupdf.Pixmap(src, xref)
            smask = src.xref_get_key(xref, "SMask")
            if smask[0] == "xref":
                mask = pymupdf.Pixmap(src, int(smask[1].split()[0]))
                if pix.alpha:
                    pix = pymupdf.Pixmap(pix, 0)
                if pix.colorspace and pix.colorspace.n != 3:
                    pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
                pix = pymupdf.Pixmap(pix, mask)
    except Exception:
        pix = None
    if pix is None:
        # Inline image or something odd: rasterise that region of the page
        bb = pymupdf.Rect(info["bbox"])
        pix = page.get_pixmap(clip=bb, dpi=144, alpha=True)
        img = _pix_to_qimage(pix)
        it = RasterItem(img)
        it.xf = QTransform.fromScale(bb.width / img.width(), bb.height / img.height()) * \
            QTransform.fromTranslate(bb.x0 + ox, bb.y0)
        return it
    if pix.colorspace and pix.colorspace.n not in (1, 3):
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
    if pix.colorspace and pix.colorspace.n == 1:
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
    img = _pix_to_qimage(pix)
    w, h = img.width(), img.height()
    a, b, c, d, e, f = info["transform"]
    it = RasterItem(img)
    it.xf = QTransform(a / w, b / w, c / h, d / h, e + ox, f)
    return it


def _pix_to_qimage(pix):
    fmt = QImage.Format_RGBA8888 if pix.alpha else QImage.Format_RGB888
    img = QImage(pix.samples, pix.width, pix.height, pix.stride, fmt).copy()
    return img.convertToFormat(QImage.Format_ARGB32_Premultiplied)


def page_glyphs(src, pno):
    """Glyph outlines MuPDF draws for the page's text, keyed by origin."""
    try:
        tmp = pymupdf.open()
        tmp.insert_pdf(src, from_page=pno, to_page=pno)
        page = tmp[0]
        for info in page.get_images(full=True):
            try:
                page.delete_image(info[0])  # keep the SVG small; images aren't needed here
            except Exception:
                pass
        svg = page.get_svg_image(text_as_path=True)
    except Exception:
        return {}
    glyphs = dict(re.findall(r'<path id="(font_[^"]+)" d="([^"]*)"', svg))
    out = {}
    for tag in re.findall(r"<use [^>]*>", svg):
        href = re.search(r'xlink:href="#([^"]+)"', tag)
        mat = re.search(r'transform="matrix\(([^)]+)\)"', tag)
        if not href or not mat or href.group(1) not in glyphs:
            continue
        m = [float(v) for v in re.split(r"[ ,]+", mat.group(1).strip())]
        key = (round(m[4], 1), round(m[5], 1))
        out.setdefault(key, []).append((glyphs[href.group(1)], m))
    return out


def _text_items(span, ox, embedded_fams, missing, glyphs):
    chars = [c for c in span["chars"]]
    size = float(span.get("size", 12)) or 12.0
    cos, sin = span.get("dir", (1, 0))
    fam, bold, italic, found = resolve_font(span.get("font", ""), embedded_fams)
    pdf_font = span.get("font", "?").split("+", 1)[-1]
    if not found:
        missing.add(pdf_font)
        fam = "Arial"
    space_w = float(span.get("spacewidth") or 0) or size * 0.25

    def along(c):  # position along / across the baseline
        x, y = c[2]
        return x * cos + y * sin, -x * sin + y * cos

    def adv(c):
        b = c[3]
        return abs((b[2] - b[0]) * cos + (b[3] - b[1]) * sin)

    # Gaps between letters, in the file's own font metrics
    gaps = []
    for a, b in zip(chars, chars[1:]):
        (ua, va), (ub, vb) = along(a), along(b)
        if abs(vb - va) < size * 0.3 and ub > ua - size * 0.1:
            if chr(a[0]) != " " and chr(b[0]) != " ":
                gaps.append(ub - (ua + adv(a)))
    gaps.sort()
    track = gaps[len(gaps) // 2] if gaps else 0.0
    track = track if track > -size * 0.2 else 0.0

    runs = []
    cur = [[chars[0], ""]]
    for a, b in zip(chars, chars[1:]):
        (ua, va), (ub, vb) = along(a), along(b)
        gap = ub - (ua + adv(a))
        excess = gap - track
        if abs(vb - va) >= size * 0.3 or ub < ua - size * 0.1 or excess > size * 0.9:
            runs.append(cur)
            cur = [[b, ""]]
            continue
        pre = ""
        if excess > space_w * 0.6 and chr(a[0]) != " " and chr(b[0]) != " ":
            pre = " " * max(1, min(3, int(round(excess / max(space_w + track, 0.01)))))
        cur.append([b, pre])
    runs.append(cur)

    items = []
    for run in runs:
        text = ""
        starts = []  # (index in text, char) for letters that came from the file
        for c, pre in run:
            ch = chr(c[0]) if c[0] > 0 else "?"
            if ch == "�":
                ch = "’"  # an unmapped glyph in WinAnsi text is nearly always a curly apostrophe
            elif ch == " ":
                ch = " "  # many fonts share one glyph for space and no-break space
            text += pre
            starts.append((len(text), c))
            text += ch
        text = text.rstrip()
        if not text.strip():
            continue
        it = TextItem(text, fam, size, _col(span.get("color")) or "#000000")
        it.bold, it.italic = bold, italic
        it.opacity = float(span.get("opacity", 1.0) or 1.0)
        if span.get("type") == 1:  # stroked text
            it.stroke = it.color
            it.stroke_width = float(span.get("linewidth", 1) or 1)
            it.color = None
        x0, y0 = run[0][0][2]
        it.xf = QTransform(cos, sin, -sin, cos, x0 + ox, y0)
        # Letter spacing: where each letter starts in the file versus where
        # the font puts it (kerning included); fit one spacing to all of them
        fm = QFontMetricsF(it.font())
        u0 = along(run[0][0])[0]
        num = den = 0.0
        for k, c in starts:
            if k == 0 or k > len(text):
                continue
            off = (along(c)[0] - u0) - fm.horizontalAdvance(text[:k]) * size / 100.0
            num += k * off
            den += k * k
        extra = num / den if den else 0.0
        if abs(extra) > size * 0.01:
            it.tracking = round(extra, 3)
        if not found:
            it.missing_font = pdf_font
            inv, _ = it.xf.inverted()
            shapes = []
            for c, _ in run:
                key = (round(c[2][0], 1), round(c[2][1], 1))
                lst = glyphs.get(key)
                if lst:
                    d, m = lst.pop(0)
                    t = QTransform(*m) * QTransform.fromTranslate(ox, 0) * inv
                    shapes.append([d, [t.m11(), t.m12(), t.m21(), t.m22(), t.dx(), t.dy()]])
            if shapes:
                it.outline = shapes
                it.outline_text = text
            it.touch()
        items.append(it)
    return items


def import_image(path, doc, artboard):
    img = QImage(path)
    if img.isNull():
        raise ImportError_("Couldn't read the image %s" % os.path.basename(path))
    it = RasterItem(img)
    r = artboard.rect
    s = min(1.0, r.width() / img.width(), r.height() / img.height())
    it.xf = QTransform.fromScale(s, s) * QTransform.fromTranslate(
        r.x() + (r.width() - img.width() * s) / 2, r.y() + (r.height() - img.height() * s) / 2)
    it.name = os.path.basename(path)
    return it
