"""Drawing a document with QPainter: the canvas, PNG export and frames all
come through here, so what you see is what gets exported."""
import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPen

from halo_color import Recolor
from halo_model import GroupItem, PathItem, RasterItem, TextItem


class RenderOptions:
    def __init__(self):
        self.frame = None            # Frame, or None for the plain layers
        self.recolor = None          # Recolor previewed from the Halo wheel
        self.recolor_ids = None      # ids it touches (None = everything)
        self.raster_previews = {}    # item id -> recoloured preview QImage
        self.hidden_ids = set()      # e.g. text being edited
        self.show_hidden_layers = False
        self.isolate = None          # group being edited on its own: others dim


# ---------------------------------------------------------------- image <-> numpy

def qimage_to_rgba(img):
    """Straight-alpha (H, W, 4) uint8 copy of a QImage."""
    im = img.convertToFormat(QImage.Format_RGBA8888)
    w, h = im.width(), im.height()
    ptr = im.constBits()
    arr = np.frombuffer(ptr, dtype=np.uint8, count=im.bytesPerLine() * h).reshape(h, im.bytesPerLine())
    return arr[:, :w * 4].reshape(h, w, 4).copy()


def rgba_to_qimage(arr):
    arr = np.ascontiguousarray(arr, dtype=np.uint8)
    h, w = arr.shape[:2]
    im = QImage(arr.data, w, h, w * 4, QImage.Format_RGBA8888).copy()
    return im.convertToFormat(QImage.Format_ARGB32_Premultiplied)


_hue_cache = {}


def hue_shifted_image(item, recolor, key):
    """A recoloured copy of a raster, cached while the image is unchanged."""
    ck = (item.id, item.image.cacheKey(), key)
    im = _hue_cache.get(ck)
    if im is None:
        from halo_color import recolor_pixels
        im = rgba_to_qimage(recolor_pixels(qimage_to_rgba(item.image), recolor))
        if len(_hue_cache) > 64:
            _hue_cache.clear()
        _hue_cache[ck] = im
    return im


# ---------------------------------------------------------------- drawing

class _ColorMapper:
    def __init__(self, recolor):
        self.r = recolor
        self.cache = {}

    def __call__(self, h):
        if h is None or self.r is None:
            return h
        v = self.cache.get(h)
        if v is None:
            v = self.r.map_hex(h)
            self.cache[h] = v
        return v


def draw_item(p, it, cmap=None, raster_img=None):
    cm = cmap or (lambda c: c)
    clip = it.clip_qpath()
    if clip is not None:
        p.save()
        p.setClipPath(clip, Qt.IntersectClip)
    op = p.opacity()
    p.setOpacity(op * it.opacity)
    if isinstance(it, PathItem):
        path = it.qpath()
        if it.fill:
            p.fillPath(path, QColor(cm(it.fill)))
        if it.stroke and it.width >= 0:
            pen = it.pen()
            pen.setColor(QColor(cm(it.stroke)))
            p.strokePath(path, pen)
    elif isinstance(it, TextItem):
        path = it.qpath()
        if it.color:
            p.fillPath(path, QColor(cm(it.color)))
        if it.stroke and it.stroke_width > 0:
            p.strokePath(path, QPen(QColor(cm(it.stroke)), it.stroke_width))
    elif isinstance(it, GroupItem):
        for c in it.children:
            if c.visible:
                draw_item(p, c, cmap)
    elif isinstance(it, RasterItem):
        p.save()
        p.setTransform(it.xf, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, it.pixel_scale() * p.transform().m11() < 1.5)
        img = raster_img if raster_img is not None else it.image
        p.drawImage(it.local_rect(), img, QRectF(0, 0, img.width(), img.height()))
        p.restore()
    p.setOpacity(op)
    if clip is not None:
        p.restore()


def render_layers(p, doc, opts=None, only_layer=None, overlay=None):
    """Paint every visible layer. overlay(layer) may draw extra content above
    a layer's items (the brush stroke in progress)."""
    opts = opts or RenderOptions()
    main_map = _ColorMapper(opts.recolor) if opts.recolor and not opts.recolor.is_identity() else None
    for layer in doc.layers:
        if only_layer is not None and layer is not only_layer:
            continue
        st = opts.frame.state(layer) if opts.frame is not None else None
        visible = st["v"] if st is not None else layer.visible
        if not visible and not opts.show_hidden_layers:
            continue
        p.save()
        p.setOpacity(p.opacity() * layer.opacity * (st["op"] if st else 1.0))
        if st and (st["dx"] or st["dy"]):
            p.translate(st["dx"], st["dy"])
        hue = st["hue"] if st else 0.0
        frame_rc = None
        if abs(hue) > 1e-6:
            frame_rc = Recolor("perceptual")
            frame_rc.spin = hue
        frame_map = _ColorMapper(frame_rc) if frame_rc else None
        ctx = (opts, main_map, frame_map, frame_rc, hue)
        if opts.isolate is not None:
            _draw_isolated(p, layer.items, ctx, False)
        else:
            _draw_list(p, layer.items, ctx)
        if overlay is not None:
            overlay(layer)
        p.restore()


def _draw_list(p, items, ctx):
    for it in items:
        _draw_one(p, it, ctx)


def _draw_one(p, it, ctx):
    opts, main_map, frame_map, frame_rc, hue = ctx
    if not it.visible or it.id in opts.hidden_ids:
        return
    if isinstance(it, GroupItem):
        clip = it.clip_qpath()
        p.save()
        if clip is not None:
            p.setClipPath(clip, Qt.IntersectClip)
        p.setOpacity(p.opacity() * it.opacity)
        _draw_list(p, it.children, ctx)
        p.restore()
        return
    use_main = main_map is not None and (opts.recolor_ids is None or it.id in opts.recolor_ids)
    if use_main and frame_map:
        cmap = lambda c, a=main_map, b=frame_map: b(a(c))
    elif use_main:
        cmap = main_map
    else:
        cmap = frame_map
    raster_img = None
    if isinstance(it, RasterItem):
        if use_main and it.id in opts.raster_previews:
            raster_img = opts.raster_previews[it.id]
        if frame_rc is not None:
            src_item = it
            if raster_img is not None:
                class _Tmp:  # shift the preview, not the original
                    pass
                src_item = _Tmp()
                src_item.id, src_item.image = it.id + "~pv", raster_img
            raster_img = hue_shifted_image(src_item, frame_rc, ("hue", round(hue, 2)))
    draw_item(p, it, cmap, raster_img)


def _contains(group, target_id):
    return any(c.id == target_id or (isinstance(c, GroupItem) and _contains(c, target_id))
               for c in group.children)


def _draw_isolated(p, items, ctx, inside):
    """Isolation mode: the group being edited draws normally, the rest faintly."""
    iso = ctx[0].isolate
    for it in items:
        if inside or it.id == iso:
            _draw_one(p, it, ctx)
        elif isinstance(it, GroupItem) and _contains(it, iso):
            if not it.visible:
                continue
            clip = it.clip_qpath()
            p.save()
            if clip is not None:
                p.setClipPath(clip, Qt.IntersectClip)
            p.setOpacity(p.opacity() * it.opacity)
            _draw_isolated(p, it.children, ctx, False)
            p.restore()
        else:
            p.save()
            p.setOpacity(p.opacity() * 0.3)
            _draw_one(p, it, ctx)
            p.restore()


def render_artboard(doc, artboard, scale=1.0, frame=None, transparent=True, opts=None):
    """An artboard as an image (export, frame thumbnails)."""
    r = artboard.rect
    w = max(1, int(round(r.width() * scale)))
    h = max(1, int(round(r.height() * scale)))
    img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    if artboard.background:
        img.fill(QColor(artboard.background))
    elif transparent:
        img.fill(Qt.transparent)
    else:
        img.fill(Qt.white)
    p = QPainter(img)
    p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform | QPainter.TextAntialiasing)
    p.scale(w / r.width(), h / r.height())
    p.translate(-r.x(), -r.y())
    p.setClipRect(r)
    o = opts or RenderOptions()
    if frame is not None:
        o.frame = frame
    render_layers(p, doc, o)
    p.end()
    return img
