"""Groups and sublayers for normal .ai files.

The PDF copy inside an .ai is the most faithful picture of the artwork, so
Nous Halo draws from it, but PDF has no idea about Illustrator's groups or
sublayers. Illustrator's own (native) data does. This module reads that
data (Zstandard or zlib compressed), finds each PDF shape's twin in it by
size and position, and rebuilds the group tree around the PDF objects.
If too few shapes can be matched on a page, that page stays ungrouped.
"""
import re
import zlib

from PySide6.QtCore import QRectF

from halo_model import GroupItem, Layer, PathItem


def native_from_ai_pdf(src):
    """Illustrator's native data from a PDF-compatible .ai, or None."""
    try:
        kind, val = src.xref_get_key(src[0].xref, "PieceInfo/Illustrator/Private")
        if kind != "xref":
            return None
        priv = int(val.split()[0])
        n = int(src.xref_get_key(priv, "NumBlock")[1])
        parts = []
        for i in range(1, n + 1):
            k, v = src.xref_get_key(priv, "AIPrivateData%d" % i)
            if k == "xref":
                parts.append(src.xref_stream(int(v.split()[0])) or b"")
        data = b"".join(parts)
    except Exception:
        return None
    for tag, how in ((b"%AI24_ZStandard_Data", "zstd"), (b"%AI12_CompressedData", "zlib")):
        i = data.find(tag)
        if i >= 0:
            body = data[i + len(tag):]
            try:
                if how == "zstd":
                    from compression import zstd  # Python 3.14+
                    return zstd.decompress(body)
                d = zlib.decompressobj()
                return d.decompress(body)
            except Exception:
                return None
    return data if b"%AI5_BeginLayer" in data else None


class NativeTree:
    """The native layer/group tree, with every path's place in it."""

    def __init__(self, native):
        from halo_ainative import NativeParser, text_encoding
        start = native.find(b"%%EndSetup")
        end = native.find(b"%%PageTrailer")
        art = native[start if start >= 0 else 0:end if end > 0 else len(native)]
        p = NativeParser([0.0, 0.0, 0.0, 0.0], text_encoding(native))
        p.run(art)
        self.layers = p.layers
        # per layer name: [(path item, (group, group, ...))] in drawing order
        self.leaves = {}
        for l in self.layers:
            out = []
            self._walk(l.items, (), out)
            self.leaves.setdefault(l.name, []).extend(out)

    def _walk(self, items, path, out):
        for it in items:
            if isinstance(it, GroupItem):
                self._walk(it.children, path + (it,), out)
            elif isinstance(it, PathItem):
                out.append((it, path))


def _size_key(r):
    return (round(r.width()), round(r.height()))


def _close(a, b, tol):
    return (abs(a.left() - b.left()) <= tol and abs(a.right() - b.right()) <= tol and
            abs(a.top() - b.top()) <= tol and abs(a.bottom() - b.bottom()) <= tol)


def page_offset(pdf_items, tree):
    """Where native coordinates land on this PDF page: vote on the shift
    between shapes of the same size, biggest (most distinctive) shapes first."""
    by_size = {}
    for leaves in tree.leaves.values():
        for it, _ in leaves:
            b = it.geom_bounds()
            if b.width() + b.height() < 1:
                continue
            by_size.setdefault(_fine_key(b), []).append(b)
    paths = [it for it in pdf_items if isinstance(it, PathItem)]
    paths = [it for it in paths if it.geom_bounds().width() + it.geom_bounds().height() >= 1]
    paths.sort(key=lambda it: -(it.geom_bounds().width() * it.geom_bounds().height()))
    votes = {}
    tried = 0
    for it in paths[:200]:
        b = it.geom_bounds()
        tried += 1
        w, h = _fine_key(b)
        seen = set()
        for dw in (-1, 0, 1):
            for dh in (-1, 0, 1):
                for nb in by_size.get((w + dw, h + dh), []):
                    if abs(nb.width() - b.width()) < 0.5 and abs(nb.height() - b.height()) < 0.5:
                        d = (round((b.center().x() - nb.center().x()) * 2) / 2,
                             round((b.center().y() - nb.center().y()) * 2) / 2)
                        if d not in seen:  # one vote per shape per shift
                            seen.add(d)
                            votes[d] = votes.get(d, 0) + 1
    if not votes:
        return None
    best = max(votes.items(), key=lambda kv: kv[1])
    if best[1] < max(1, tried * 0.2):
        return None
    return best[0]


def _fine_key(r):
    return (round(r.width() * 2), round(r.height() * 2))


def group_page(placed, tree, layers_by_name):
    """placed: [(seq, layer, item)] for one page, in drawing order.
    Returns {layer id: [top-level objects with groups rebuilt]} or None
    when the page can't be matched well enough."""
    items = [it for _, _, it in placed]
    if not any(isinstance(it, PathItem) for it in items):
        # nothing to match by: keep the page as it is
        out = {}
        for _, layer, it in placed:
            out.setdefault(layer.id, []).append(it)
        return out
    off = page_offset(items, tree)
    if off is None:
        return None
    dx, dy = off

    def shifted(r):
        return r.translated(dx, dy)

    per_layer = {}
    for _, layer, it in placed:
        per_layer.setdefault(layer.id, (layer, []))[1].append(it)
    result = {}
    total = matched = 0
    for lid, (layer, lits) in per_layer.items():
        leaves = tree.leaves.get(layer.name, [])
        if not leaves:
            # no native layer of that name: try them all
            leaves = [x for ls in tree.leaves.values() for x in ls]
        index = {}
        for k, (nit, _) in enumerate(leaves):
            index.setdefault(_size_key(nit.geom_bounds()), []).append(k)
        j = 0
        cur = ()
        paths = []
        for it in lits:
            path = None
            if isinstance(it, PathItem):
                total += 1
                b = it.geom_bounds()
                tol = max(0.75, 0.003 * max(b.width(), b.height()))
                found = None
                for k in range(j, min(len(leaves), j + 80)):
                    if _close(shifted(leaves[k][0].geom_bounds()), b, tol):
                        found = k
                        break
                if found is None:
                    w, h = _size_key(b)
                    ks = []
                    for dw in (-1, 0, 1):
                        for dh in (-1, 0, 1):
                            ks += index.get((w + dw, h + dh), [])
                    ks = sorted(k for k in ks if _close(shifted(leaves[k][0].geom_bounds()), b, tol))
                    later = [k for k in ks if k >= j]
                    found = later[0] if later else (ks[0] if ks else None)
                if found is not None:
                    matched += 1
                    j = found
                    path = leaves[found][1]
            if path is None:
                # text, images and unmatched shapes join the open group if
                # they sit inside it
                path = cur
                b = it.geom_bounds()
                while path:
                    gb = shifted(path[-1].geom_bounds()).adjusted(-2, -2, 2, 2)
                    if gb.contains(b):
                        break
                    path = path[:-1]
            cur = path
            paths.append((it, path))
        result[lid] = paths
    if total and matched < 0.3 * total:
        return None
    # build the trees
    out = {}
    for lid, paths in result.items():
        top = []
        stack = []  # (native group, our group)
        for it, path in paths:
            # close groups that this object isn't in
            k = 0
            while k < len(stack) and k < len(path) and stack[k][0] is path[k]:
                k += 1
            del stack[k:]
            for ng in path[k:]:
                g = GroupItem(name=ng.name)
                g.sublayer = ng.sublayer
                g.visible = ng.visible
                g.opacity = ng.opacity
                if ng.clip:
                    from halo_model import map_subpaths
                    from PySide6.QtGui import QTransform
                    g.clip = map_subpaths(ng.clip, QTransform.fromTranslate(dx, dy))
                (stack[-1][1].children if stack else top).append(g)
                stack.append((ng, g))
            (stack[-1][1].children if stack else top).append(it)
        _clean_clips(top)
        out[lid] = top
    return out


def _clean_clips(items):
    """Children of a clipping group already carry the same clip from the PDF;
    drop those copies so releasing the mask works the Illustrator way."""
    for it in items:
        if isinstance(it, GroupItem):
            if it.clip:
                cb = it.clip_qpath().boundingRect()
                for c in it.children:
                    if c.clip:
                        ccb = c.clip_qpath().boundingRect()
                        if _close(cb, ccb, 1.0):
                            c.clip = None
                            c.touch()
            _clean_clips(it.children)
            it.touch()
