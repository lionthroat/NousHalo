"""Illustrator's own (native) art format, for files with no PDF copy inside:

  * EPS files saved by Illustrator (Adobe Stock downloads are these, often
    with an .ai extension): the native art rides along, ASCII85 + zlib, in
    the %AI9_PrivateData block.
  * .ai files exported by Photopea: an empty PDF page plus the native art.
  * Illustrator 8-10 era .ai files, which are the native format outright.

The native format is PostScript-shaped: operands, then an operator.
  paths     x y m, x y l/L, x1 y1 x2 y2 x3 y3 c/C, v/V, y/Y
  painting  f F (fill), s S (stroke), b B (both), n N (none), h (close),
            W (this path is a clip)
  colour    g G (grey), k K (CMYK), x X / Xx XX (spot), Xa XA (RGB)
  stroke    w (width) j (join) J (cap) M (miter) d (dash)
  structure Lb/Ln/LB (layer), u/U (group), *u/*U (compound path),
            q/Q (clip group), To/TO (text), Xy (opacity), XR (fill rule)
Gradients and placed rasters inside native art aren't read (counted in the
import notes). y points up here; it's flipped into document coordinates.
"""
import base64
import re
import struct
import zlib

from halo_color import rgb_to_hex
from halo_model import Artboard, Document, GroupItem, Layer, PathItem, TextItem, corner

_NUM = re.compile(rb"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")


# ---------------------------------------------------------------- extraction

def eps_postscript(data):
    """The PostScript part of an EPS (DOS EPS files carry a binary header)."""
    if data[:4] == b"\xc5\xd0\xd3\xc6":
        off, ln = struct.unpack("<II", data[4:12])
        return data[off:off + ln]
    return data


def _decode_datastream(block):
    lines = re.split(rb"\r\n|\r|\n", block)
    body = b"".join(l[1:] for l in lines[1:] if l.startswith(b"%"))
    body = body.split(b"~>")[0]
    raw = base64.a85decode(body, ignorechars=b" \t\r\n")
    d = zlib.decompressobj()
    out = d.decompress(raw)
    try:
        out += d.flush()
    except zlib.error:
        pass
    return out


def native_from_eps(data):
    ps = eps_postscript(data)
    i = ps.find(b"%AI9_DataStream")
    if i >= 0:
        j = ps.find(b"%AI9_PrivateDataEnd", i)
        out = _decode_datastream(ps[i:j if j > 0 else len(ps)])
        if out:
            return out
    if b"%AI5_BeginLayer" in ps or b"%%EndSetup" in ps and b"%AI3_" in ps:
        return ps  # older Illustrator: the file is the native format
    return None


def native_from_pdf_private(doc):
    """Photopea-style .ai: a PDF whose Illustrator private data is plain text."""
    for x in range(1, doc.xref_length()):
        try:
            if not doc.xref_is_stream(x):
                continue
            s = doc.xref_stream(x)
        except Exception:
            continue
        if s and s.lstrip()[:4] == b"%!PS" and (b"%AI5_BeginLayer" in s or b"Lb" in s):
            return s
    return None


# ---------------------------------------------------------------- tokens

def strip_binary(data):
    """Drop %%BeginData: <n> blocks (embedded raster and other binary data),
    skipping exactly n bytes so nothing inside reads as an operator."""
    out = []
    pos = 0
    while True:
        i = data.find(b"%%BeginData:", pos)
        if i < 0:
            out.append(data[pos:])
            break
        out.append(data[pos:i])
        m = re.match(rb"%%BeginData:\s*(\d+)", data[i:i + 64])
        eol = data.find(b"\n", i)
        eol2 = data.find(b"\r", i)
        ends = [e for e in (eol, eol2) if e >= 0]
        line_end = min(ends) if ends else len(data)
        if data[line_end:line_end + 2] == b"\r\n":
            line_end += 2
        else:
            line_end += 1
        if m:
            pos = line_end + int(m.group(1))
        else:
            j = data.find(b"%%EndData", i)
            pos = j if j >= 0 else len(data)
        # carry on after %%EndData's line
        j = data.find(b"%%EndData", pos - 16 if pos > 16 else pos)
        if 0 <= j < pos + 4096:
            pos = j
    return b"".join(out)


def tokenize(text):
    """Yield tokens from native art: floats, ('s', str), ('n', name),
    ('a', list) and operator strings. Comment lines are skipped."""
    for line in re.split(rb"\r\n|\r|\n", text):
        if not line or line[:1] == b"%":
            continue
        i, n = 0, len(line)
        while i < n:
            ch = line[i:i + 1]
            if ch in b" \t\x00\x0c":
                i += 1
                continue
            if ch == b"%":
                break
            if ch == b"(":
                depth, j, buf = 1, i + 1, bytearray()
                while j < n and depth:
                    c = line[j:j + 1]
                    if c == b"\\" and j + 1 < n:
                        nx = line[j + 1:j + 2]
                        esc = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"(": b"(", b")": b")", b"\\": b"\\"}
                        if nx in esc:
                            buf += esc[nx]
                            j += 2
                            continue
                        if nx.isdigit():
                            k = j + 1
                            while k < n and k < j + 4 and line[k:k + 1].isdigit():
                                k += 1
                            buf.append(int(line[j + 1:k], 8) & 0xFF)
                            j = k
                            continue
                        j += 1
                        continue
                    if c == b"(":
                        depth += 1
                    elif c == b")":
                        depth -= 1
                        if depth == 0:
                            j += 1
                            break
                    buf += c
                    j += 1
                yield ("s", bytes(buf).decode("latin1"))
                i = j
                continue
            if ch == b"[":
                j = line.find(b"]", i)
                j = n if j < 0 else j
                vals = []
                for t in line[i + 1:j].split():
                    try:
                        vals.append(float(t))
                    except ValueError:
                        pass
                yield ("a", vals)
                i = j + 1
                continue
            if ch == b"<":
                j = line.find(b">", i)
                i = n if j < 0 else j + 1
                yield ("h", None)
                continue
            j = i
            while j < n and line[j:j + 1] not in b" \t()[]<>%":
                j += 1
            tok = line[i:j]
            if ch == b"/":
                yield ("n", tok[1:].decode("latin1"))
            elif _NUM.match(tok):
                yield float(tok)
            else:
                yield tok.decode("latin1")
            i = j if j > i else i + 1


def _cmyk(c, m, y, k):
    return rgb_to_hex(((1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k)))


def _tint(hexcol, tint):
    # tint 0 = the full colour, 1 = white
    from halo_color import hex_to_rgb
    r, g, b = hex_to_rgb(hexcol)
    t = max(0.0, min(1.0, tint))
    return rgb_to_hex((r + (1 - r) * t, g + (1 - g) * t, b + (1 - b) * t))


# ---------------------------------------------------------------- parser

_ENCODINGS = {"windows cyrillic": "cp1251", "macos cyrillic": "mac_cyrillic", "windows roman": "cp1252",
              "windows western": "cp1252", "macos roman": "mac_roman", "windows ce": "cp1250",
              "windows greek": "cp1253", "windows turkish": "cp1254", "macos ce": "mac_latin2",
              "macos greek": "mac_greek", "macos turkish": "mac_turkish"}


def text_encoding(data):
    """The code page an EPS says its strings are in (%ADO_DSC_Encoding)."""
    m = re.search(rb"%ADO_DSC_Encoding:\s*([^\r\n]+)", data[:4000])
    if m:
        return _ENCODINGS.get(m.group(1).decode("latin1").strip().lower(), "cp1252")
    return "cp1252"


class NativeParser:
    def __init__(self, box, encoding="cp1252"):
        """box: (x0, y0, x1, y1) of the artboard in native coordinates."""
        self.encoding = encoding
        self.box = box
        self.layers = []
        # open containers, innermost last: ("layer"|"sublayer"|"group"|"clip", Layer or GroupItem)
        self.stack = []
        self.fill = "#000000"
        self.stroke = None
        self.width = 1.0
        self.cap = 0
        self.join = 0
        self.dashes = None
        self.even_odd = False
        self.opacity = 1.0
        self.subs = []
        self.compound = None   # list of subpaths while inside *u ... *U
        self.compound_style = None
        self.clip_flag = False
        self.text = None
        self.skipped = {"images": 0, "gradients": 0}
        self.in_gradient = 0
        self.ops = []

    def pt(self, x, y):
        return x - self.box[0], self.box[3] - y

    def container(self):
        """The list new objects go into."""
        if not self.stack:
            l = Layer("Layer %d" % (len(self.layers) + 1))
            self.layers.append(l)
            self.stack.append(("layer", l))
        kind, obj = self.stack[-1]
        return obj.items if isinstance(obj, Layer) else obj.children

    def _open(self, kind, obj):
        self.container().append(obj)
        self.stack.append((kind, obj))

    def _close(self, kinds, stop=("layer", "sublayer")):
        """Pop the innermost container of one of `kinds`, closing anything
        left open inside it; never past a layer boundary in `stop`."""
        for k in range(len(self.stack) - 1, -1, -1):
            kind = self.stack[k][0]
            if kind in kinds:
                del self.stack[k:]
                return
            if kind in stop:
                return

    def _last(self):
        return self.subs[-1]["nodes"][-1] if self.subs and self.subs[-1]["nodes"] else None

    def _start(self, x, y):
        self.subs.append({"nodes": [corner(x, y)], "closed": False})

    def _ensure(self):
        if not self.subs:
            self._start(0, 0)

    def run(self, text):
        st = []
        for tok in tokenize(strip_binary(text)):
            if isinstance(tok, (float, tuple)):
                st.append(tok)
                continue
            try:
                self.op(tok, st)
            except (IndexError, TypeError, ValueError):
                pass
            st.clear()
        self.end_text()
        for l in self.layers:
            l.items = tidy_groups(l.items)

    def nums(self, st, n):
        vals = [v for v in st if isinstance(v, float)]
        return vals[-n:] if len(vals) >= n else None

    def op(self, op, st):
        if op == "m":
            a = self.nums(st, 2)
            if self.subs and not self.compound and self.subs[-1]["nodes"] and len(self.subs[-1]["nodes"]) > 0:
                pass  # another subpath of the same path (rare outside compounds)
            self._start(*self.pt(*a))
        elif op in ("l", "L"):
            a = self.nums(st, 2)
            self._ensure()
            self.subs[-1]["nodes"].append(corner(*self.pt(*a)))
        elif op in ("c", "C"):
            a = self.nums(st, 6)
            self._ensure()
            p1, p2, p3 = self.pt(a[0], a[1]), self.pt(a[2], a[3]), self.pt(a[4], a[5])
            last = self._last()
            last[4], last[5] = p1
            self.subs[-1]["nodes"].append([p3[0], p3[1], p2[0], p2[1], p3[0], p3[1]])
        elif op in ("v", "V"):
            a = self.nums(st, 4)
            self._ensure()
            p2, p3 = self.pt(a[0], a[1]), self.pt(a[2], a[3])
            self.subs[-1]["nodes"].append([p3[0], p3[1], p2[0], p2[1], p3[0], p3[1]])
        elif op in ("y", "Y"):
            a = self.nums(st, 4)
            self._ensure()
            p1, p3 = self.pt(a[0], a[1]), self.pt(a[2], a[3])
            last = self._last()
            last[4], last[5] = p1
            self.subs[-1]["nodes"].append(corner(*p3))
        elif op == "h":
            if self.subs:
                self.subs[-1]["closed"] = True
        elif op == "W":
            self.clip_flag = True
        elif op in ("f", "F", "s", "S", "b", "B", "n", "N", "H"):
            self.paint(op)
        # colours
        elif op in ("g", "G"):
            a = self.nums(st, 1)
            c = rgb_to_hex((a[0], a[0], a[0]))
            self._set_color(op == "g", c)
        elif op in ("k", "K"):
            a = self.nums(st, 4)
            self._set_color(op == "k", _cmyk(*a))
        elif op in ("x", "X"):
            vals = [v for v in st if isinstance(v, float)]
            if len(vals) >= 5:
                self._set_color(op == "x", _tint(_cmyk(*vals[:4]), vals[-1]))
        elif op in ("Xx", "XX"):
            vals = [v for v in st if isinstance(v, float)]
            if vals:
                typ, tint = vals[-1], vals[-2] if len(vals) >= 2 else 0
                col = vals[:-2]
                if typ == 1 and len(col) >= 3:
                    c = rgb_to_hex(col[-3:])
                elif len(col) >= 4:
                    c = _cmyk(*col[:4])
                else:
                    return
                self._set_color(op == "Xx", _tint(c, tint))
        elif op in ("Xa", "XA"):
            a = self.nums(st, 3)
            self._set_color(op == "Xa", rgb_to_hex(a))
        elif op in ("p", "P"):
            pass  # pattern fill: keep the current colour
        # stroke style
        elif op == "w":
            self.width = self.nums(st, 1)[0]
        elif op == "j":
            self.join = int(self.nums(st, 1)[0]) % 3
        elif op == "J":
            self.cap = int(self.nums(st, 1)[0]) % 3
        elif op == "d":
            arr = [t for t in st if isinstance(t, tuple) and t[0] == "a"]
            self.dashes = arr[0][1] if arr and arr[0][1] and any(arr[0][1]) else None
        elif op == "XR":
            self.even_odd = bool(self.nums(st, 1)[0])
        elif op == "Xy":
            vals = [v for v in st if isinstance(v, float)]
            if len(vals) >= 2:
                self.opacity = max(0.0, min(1.0, vals[1]))
        elif op == "u":
            self.opacity = 1.0
            self._open("group", GroupItem())
        elif op == "U":
            self.opacity = 1.0
            self._close(("group",))
        elif op == "XW":
            self.opacity = 1.0
        # structure
        elif op == "*u":
            self.compound = []
            self.compound_style = None
        elif op == "*U":
            self.end_compound()
        elif op == "q":
            g = GroupItem()
            g._clip_group = True
            self._open("clip", g)
        elif op == "Q":
            self._close(("clip",))
        elif op == "Lb":
            vals = [v for v in st if isinstance(v, float)]
            visible = bool(vals[0]) if vals else True
            if any(k in ("layer", "sublayer") for k, _ in self.stack):
                # a layer inside a layer: a sublayer
                g = GroupItem()
                g.sublayer = True
                g.visible = visible
                self._open("sublayer", g)
            else:
                self.stack = []
                l = Layer("Layer %d" % (len(self.layers) + 1))
                l.visible = visible
                if len(vals) >= 10:
                    l.tag = rgb_to_hex((vals[7] / 255, vals[8] / 255, vals[9] / 255))
                self.layers.append(l)
                self.stack.append(("layer", l))
        elif op == "Ln":
            s = [t[1] for t in st if isinstance(t, tuple) and t[0] == "s"]
            if s and self.stack and self.stack[-1][0] in ("layer", "sublayer"):
                self.stack[-1][1].name = self.decode(s[-1])
        elif op == "LB":
            self._close(("layer", "sublayer"), stop=())
        elif op in ("Bb", "Bg"):
            if op == "Bb":
                self.in_gradient += 1
                self.skipped["gradients"] += 1
        elif op == "BB":
            self.in_gradient = max(0, self.in_gradient - 1)
        elif op in ("XI", "XG"):
            self.skipped["images"] += 1
        # text
        elif op == "To":
            self.end_text()
            self.text = {"m": None, "font": "Arial", "size": 12.0, "chunks": [], "color": self.fill}
        elif op in ("Tp", "TP"):
            a = self.nums(st, 7)
            if self.text is not None and a and self.text["m"] is None:
                self.text["m"] = a[:6]
        elif op == "Tf":
            names = [t[1] for t in st if isinstance(t, tuple) and t[0] == "n"]
            vals = [v for v in st if isinstance(v, float)]
            if self.text is not None:
                if names:
                    self.text["font"] = names[-1].lstrip("_")
                if vals:
                    self.text["size"] = vals[0]
        elif op in ("Tx", "Tj", "TX"):
            s = [t[1] for t in st if isinstance(t, tuple) and t[0] == "s"]
            if self.text is not None and s:
                self.text["chunks"].append(self.decode(s[-1]))
                self.text["color"] = self.fill
        elif op == "T*":
            if self.text is not None:
                self.text["chunks"].append("\n")
        elif op == "TO":
            self.end_text()

    def decode(self, s):
        try:
            return s.encode("latin1").decode(self.encoding)
        except (UnicodeError, LookupError):
            return s

    def _set_color(self, fill, c):
        if fill:
            self.fill = c
        else:
            self.stroke = c

    def paint(self, op):
        subs = [s for s in self.subs if s["nodes"]]
        self.subs = []
        if not subs:
            self.clip_flag = False
            return
        if op in ("f", "s", "b", "n"):
            subs[-1]["closed"] = True
        for s in subs:
            nodes = s["nodes"]
            if len(nodes) > 2 and abs(nodes[0][0] - nodes[-1][0]) < 1e-3 and abs(nodes[0][1] - nodes[-1][1]) < 1e-3:
                nodes[0][2], nodes[0][3] = nodes[-1][2], nodes[-1][3]
                nodes.pop()
                s["closed"] = True
        if self.clip_flag:
            self.clip_flag = False
            for k in range(len(self.stack) - 1, -1, -1):
                kind, g = self.stack[k]
                if kind == "clip":
                    for sp in subs:
                        sp["closed"] = True
                    g.clip = (g.clip or []) + subs
                    break
            return
        fill = op in ("f", "F", "b", "B")
        stroke = op in ("s", "S", "b", "B")
        if self.compound is not None:
            self.compound.extend(subs)
            if self.compound_style is None and (fill or stroke):
                self.compound_style = (fill, stroke, self._style())
            return
        if not fill and not stroke:
            return
        self.emit(subs, fill, stroke, self._style())

    def _style(self):
        return {"fill": self.fill if not self.in_gradient else "#888888", "stroke": self.stroke,
                "width": self.width, "cap": self.cap, "join": self.join, "dashes": self.dashes,
                "even_odd": self.even_odd, "opacity": self.opacity}

    def end_compound(self):
        subs = self.compound or []
        style = self.compound_style
        self.compound = None
        self.compound_style = None
        if subs and style:
            fill, stroke, st = style
            st = dict(st)
            st["even_odd"] = True if not st["even_odd"] else st["even_odd"]
            self.emit(subs, fill, stroke, st)

    def emit(self, subs, fill, stroke, st):
        it = PathItem(subs, st["fill"] if fill else None, st["stroke"] if stroke else None, st["width"])
        it.cap, it.join, it.dashes = st["cap"], st["join"], st["dashes"]
        it.even_odd = st["even_odd"]
        it.opacity = st["opacity"]
        self.container().append(it)
        self.opacity = 1.0

    def end_text(self):
        t = self.text
        self.text = None
        if not t or not t["chunks"] or t["m"] is None:
            return
        from PySide6.QtGui import QTransform
        from halo_import import resolve_font
        text = "".join(t["chunks"]).replace("\r", "\n").rstrip("\n")
        if not text.strip():
            return
        fam, bold, italic, found = resolve_font(t["font"], [])
        it = TextItem(text, fam or "Arial", t["size"], t["color"] or "#000000")
        it.bold, it.italic = bold, italic
        if not found:
            it.missing_font = t["font"]
        a, b, c, d, e, f = t["m"]
        x, y = self.pt(e, f)
        # native y points up: flip the matrix's y axis
        it.xf = QTransform(a, -b, -c, d, x, y)
        self.container().append(it)
        self.skipped.setdefault("text", 0)
        self.skipped["text"] += 1


def tidy_groups(items):
    """Drop empty groups; a clipping group that never got its clip is a
    plain group."""
    out = []
    for it in items:
        if isinstance(it, GroupItem):
            it.children = tidy_groups(it.children)
            if not it.children and not it.sublayer:
                continue
            if getattr(it, "_clip_group", False):
                del it._clip_group
            it.touch()
        out.append(it)
    return out


def _bbox(native):
    for key in (rb"%%HiResBoundingBox:\s*([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)",
                rb"%%BoundingBox:\s*([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)"):
        m = re.search(key, native[:20000])
        if m:
            v = [float(g) for g in m.groups()]
            if v[2] > v[0] and v[3] > v[1]:
                return v
    return None


def parse_native(native, name, mediabox=None, doc=None, x_offset=0.0, encoding=None):
    """Parse native art into `doc` (a new Document if None), as one artboard."""
    box = _bbox(native)
    from_page = False
    if box is None and mediabox is not None:
        box = [0.0, 0.0, mediabox[0], mediabox[1]]
        from_page = True
    provisional = box is None
    if provisional:
        box = [0.0, 0.0, 1000.0, 1000.0]
    start = native.find(b"%%EndSetup")
    end = native.find(b"%%PageTrailer")
    if end < 0:
        end = native.find(b"%%Trailer")
    art = native[start if start >= 0 else 0:end if end > 0 else len(native)]
    p = NativeParser(box, encoding or text_encoding(native))
    p.run(art)
    new = doc is None
    if new:
        doc = Document()
    layers = [l for l in p.layers if l.items] or p.layers or [Layer("Layer 1")]
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QTransform
    w, h = box[2] - box[0], box[3] - box[1]
    if from_page:
        # Photopea writes its own canvas units, not the PDF page's: if the
        # art doesn't sit on the page, the art's bounds become the artboard
        r = QRectF()
        for l in layers:
            for it in l.items:
                r = r.united(it.bounds())
        if not QRectF(0, 0, w, h).adjusted(-w * 0.1, -h * 0.1, w * 0.1, h * 0.1).contains(r):
            provisional = True
    if provisional:
        r = QRectF()
        for l in layers:
            for it in l.items:
                r = r.united(it.bounds())
        if not r.isEmpty():
            shift = QTransform.fromTranslate(-r.x(), -r.y())
            for l in layers:
                for it in l.items:
                    it.apply_transform(shift)
            w, h = r.width(), r.height()
    if x_offset:
        t = QTransform.fromTranslate(x_offset, 0)
        for l in layers:
            for it in l.items:
                it.apply_transform(t)
    doc.artboards.append(Artboard(name, x_offset, 0, w, h))
    existing = {l.name: l for l in doc.layers}
    for l in layers:
        if l.name in existing and not new:
            existing[l.name].items.extend(l.items)
        else:
            doc.layers.append(l)
    notes = []
    if p.skipped["gradients"]:
        notes.append("%d gradient fill%s came in as flat grey (Nous Halo doesn't do gradients yet)." % (
            p.skipped["gradients"], "" if p.skipped["gradients"] == 1 else "s"))
    if p.skipped["images"]:
        notes.append("%d embedded image%s left out (that kind of image data isn't readable yet)." % (
            p.skipped["images"], "" if p.skipped["images"] == 1 else "s"))
    return doc, notes
