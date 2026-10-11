"""Exports: artboards as PNG or SVG, animations as PNG frames and sprite sheets."""
import base64
import html
import json
import math
import os

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QImage, QPainter

from halo_model import GroupItem, PathItem, RasterItem, TextItem, image_to_png
from halo_render import render_artboard
from halo_svgpath import qpath_to_d, subpaths_to_d


def safe(name):
    out = "".join(ch if ch.isalnum() or ch in " _-." else "_" for ch in name).strip()
    return out or "artboard"


def export_png(doc, artboard, path, scale=1.0, transparent=True, frame=None):
    img = render_artboard(doc, artboard, scale, frame=frame, transparent=transparent)
    if not img.save(path, "PNG"):
        raise IOError("Couldn't write %s" % path)
    return path


def export_artboards(doc, artboards, folder, scale=1.0, transparent=True, frame=None):
    out = []
    used = set()
    for ab in artboards:
        base = safe(ab.name)
        name, k = base, 2
        while name.lower() in used:
            name = "%s %d" % (base, k)
            k += 1
        used.add(name.lower())
        out.append(export_png(doc, ab, os.path.join(folder, name + ".png"), scale, transparent, frame))
    return out


def export_animation(doc, artboard, folder, base, scale=1.0, sequence=True, sheet=True,
                     columns=0, padding=0, transparent=True):
    """PNG per frame and/or a sprite sheet + JSON (frame rects and durations).
    Returns the files written."""
    frames = doc.frames
    if not frames:
        raise ValueError("No frames to export")
    images = [render_artboard(doc, artboard, scale, frame=f, transparent=transparent) for f in frames]
    files = []
    digits = max(3, len(str(len(frames))))
    if sequence:
        for i, img in enumerate(images):
            p = os.path.join(folder, "%s_%0*d.png" % (base, digits, i))
            img.save(p, "PNG")
            files.append(p)
    if sheet:
        w, h = images[0].width(), images[0].height()
        n = len(images)
        cols = columns if columns and columns > 0 else math.ceil(math.sqrt(n))
        cols = max(1, min(cols, n))
        rows = math.ceil(n / cols)
        sw = cols * w + (cols - 1) * padding
        sh = rows * h + (rows - 1) * padding
        out = QImage(sw, sh, QImage.Format_ARGB32_Premultiplied)
        out.fill(Qt.transparent)
        p = QPainter(out)
        meta = []
        for i, img in enumerate(images):
            r, c = divmod(i, cols)
            x, y = c * (w + padding), r * (h + padding)
            p.drawImage(x, y, img)
            meta.append({"x": x, "y": y, "w": w, "h": h, "duration_ms": frames[i].delay})
        p.end()
        sp = os.path.join(folder, base + "_sheet.png")
        out.save(sp, "PNG")
        files.append(sp)
        jp = os.path.join(folder, base + "_sheet.json")
        with open(jp, "w", encoding="utf-8") as f:
            json.dump({"image": os.path.basename(sp), "frame_width": w, "frame_height": h,
                       "columns": cols, "rows": rows, "padding": padding, "count": n,
                       "loop": doc.loop, "frames": meta,
                       "godot": {"hframes": cols, "vframes": rows,
                                 "note": "Sprite2D: set hframes/vframes; frame durations are in 'frames'"}},
                      f, indent=1)
        files.append(jp)
    return files


# ---------------------------------------------------------------- SVG

def _num(v):
    return ("%.3f" % v).rstrip("0").rstrip(".")


def _matrix(t):
    return "matrix(%s)" % " ".join(_num(v) for v in (t.m11(), t.m12(), t.m21(), t.m22(), t.dx(), t.dy()))


def export_svg(doc, artboard, path):
    r = artboard.rect
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
           'width="%s" height="%s" viewBox="%s %s %s %s">' % (_num(r.width()), _num(r.height()), _num(r.x()),
                                                             _num(r.y()), _num(r.width()), _num(r.height()))]
    defs = []
    body = []
    if artboard.background:
        body.append('<rect x="%s" y="%s" width="%s" height="%s" fill="%s"/>' % (
            _num(r.x()), _num(r.y()), _num(r.width()), _num(r.height()), artboard.background))
    ids = set()

    def svg_id(name):
        base = html.escape(safe(name).replace(" ", "_"), quote=True) or "g"
        k, cand = 2, base
        while cand in ids:
            cand = "%s_%d" % (base, k)
            k += 1
        ids.add(cand)
        return cand

    def common(it):
        extra = []
        if it.opacity < 1:
            extra.append('opacity="%s"' % _num(it.opacity))
        if it.clip:
            cid = "c%d" % (len(defs) + 1)
            defs.append('<clipPath id="%s"><path d="%s"/></clipPath>' % (cid, subpaths_to_d(it.clip)))
            extra.append('clip-path="url(#%s)"' % cid)
        return (" " + " ".join(extra)) if extra else ""

    def emit(it):
        if not it.visible or not r.intersects(it.bounds()):
            return
        ex = common(it)
        if isinstance(it, GroupItem):
            name = (' id="%s"' % svg_id(it.name)) if it.name else ""
            body.append("<g%s%s>" % (name, ex))
            for c in it.children:
                emit(c)
            body.append("</g>")
        elif isinstance(it, PathItem):
            a = ['d="%s"' % subpaths_to_d(it.subpaths)]
            a.append('fill="%s"' % (it.fill or "none"))
            if it.even_odd:
                a.append('fill-rule="evenodd"')
            if it.stroke and it.width > 0:
                a.append('stroke="%s" stroke-width="%s"' % (it.stroke, _num(it.width)))
                a.append('stroke-linecap="%s"' % ["butt", "round", "square"][it.cap])
                a.append('stroke-linejoin="%s"' % ["miter", "round", "bevel"][it.join])
                if it.dashes:
                    a.append('stroke-dasharray="%s"' % " ".join(_num(d) for d in it.dashes))
            body.append("<path %s%s/>" % (" ".join(a), ex))
        elif isinstance(it, TextItem):
            if it.uses_outline():
                body.append('<path d="%s" fill="%s"%s/>' % (qpath_to_d(it.qpath()), it.color or "none", ex))
                return
            lines = it.text.split("\n")
            spans = "".join('<tspan x="0" dy="%s">%s</tspan>' % (
                "0" if i == 0 else _num(it.size * it.leading), html.escape(l)) for i, l in enumerate(lines))
            anchor = {"left": "start", "center": "middle", "right": "end"}[it.align]
            a = ['transform="%s"' % _matrix(it.xf), 'font-family="%s"' % html.escape(it.family, quote=True),
                 'font-size="%s"' % _num(it.size), 'fill="%s"' % (it.color or "none")]
            if it.bold:
                a.append('font-weight="bold"')
            if it.italic:
                a.append('font-style="italic"')
            if it.tracking:
                a.append('letter-spacing="%s"' % _num(it.tracking))
            if anchor != "start":
                a.append('text-anchor="%s"' % anchor)
            if it.stroke and it.stroke_width:
                a.append('stroke="%s" stroke-width="%s"' % (it.stroke, _num(it.stroke_width)))
            body.append("<text %s%s>%s</text>" % (" ".join(a), ex, spans))
        elif isinstance(it, RasterItem):
            data = base64.b64encode(image_to_png(it.image)).decode("ascii")
            body.append('<image transform="%s" width="%d" height="%d" xlink:href="data:image/png;base64,%s"%s/>'
                        % (_matrix(it.xf), it.image.width(), it.image.height(), data, ex))

    for layer in doc.layers:
        attrs = ['id="%s"' % svg_id(layer.name)]
        if not layer.visible:
            attrs.append('display="none"')
        if layer.opacity < 1:
            attrs.append('opacity="%s"' % _num(layer.opacity))
        body.append("<g %s>" % " ".join(attrs))
        for it in layer.items:
            emit(it)
        body.append("</g>")
    if defs:
        out.append("<defs>" + "".join(defs) + "</defs>")
    out += body
    out.append("</svg>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    return path
