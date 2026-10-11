"""Palettes: the saved library, and reading/writing swatch files.

Reads Adobe .ase (so Illustrator swatch libraries come along), GIMP .gpl,
.hex (one colour per line, the Lospec format), .txt/.json. Writes .ase,
.gpl, .hex and a PNG strip (one pixel per colour, handy in Godot).
"""
import json
import os
import re
import struct

from halo_color import hex_to_rgb, norm_hex, rgb_to_hex


class Palette:
    def __init__(self, name="Untitled palette", colors=None, names=None):
        self.name = name
        self.colors = [norm_hex(c) for c in (colors or [])]
        self.names = list(names or [""] * len(self.colors))
        while len(self.names) < len(self.colors):
            self.names.append("")

    def add(self, hexcol, name=""):
        h = norm_hex(hexcol)
        if h in self.colors:
            return False
        self.colors.append(h)
        self.names.append(name)
        return True

    def remove(self, i):
        del self.colors[i]
        del self.names[i]

    def to_dict(self):
        return {"name": self.name, "colors": self.colors, "names": self.names}

    @staticmethod
    def from_dict(d):
        return Palette(d.get("name", "Palette"), d.get("colors", []), d.get("names"))

    def copy(self):
        return Palette(self.name, list(self.colors), list(self.names))


# ---------------------------------------------------------------- library

def library_dir():
    d = os.environ.get("NOUSHALO_DATA")
    if not d:
        base = os.environ.get("APPDATA") or os.path.expanduser("~/.local/share")
        d = os.path.join(base, "NousHalo")
    p = os.path.join(d, "palettes")
    os.makedirs(p, exist_ok=True)
    return p


def _safe_name(name):
    s = re.sub(r'[<>:"/\\|?*]+', "_", name).strip() or "palette"
    return s[:80]


def load_library():
    out = []
    d = library_dir()
    for fn in sorted(os.listdir(d)):
        if fn.lower().endswith(".json"):
            try:
                with open(os.path.join(d, fn), encoding="utf-8") as f:
                    out.append(Palette.from_dict(json.load(f)))
            except (OSError, ValueError):
                pass
    if not out:
        out = starter_palettes()
        for p in out:
            save_to_library(p)
    return out


def save_to_library(p, old_name=None):
    d = library_dir()
    if old_name and old_name != p.name:
        try:
            os.remove(os.path.join(d, _safe_name(old_name) + ".json"))
        except OSError:
            pass
    with open(os.path.join(d, _safe_name(p.name) + ".json"), "w", encoding="utf-8") as f:
        json.dump(p.to_dict(), f, indent=1)


def delete_from_library(name):
    try:
        os.remove(os.path.join(library_dir(), _safe_name(name) + ".json"))
    except OSError:
        pass


def starter_palettes():
    return [
        Palette("Nord", ["#2e3440", "#3b4252", "#434c5e", "#4c566a", "#d8dee9", "#e5e9f0",
                         "#eceff4", "#8fbcbb", "#88c0d0", "#81a1c1", "#5e81ac", "#bf616a",
                         "#d08770", "#ebcb8b", "#a3be8c", "#b48ead"]),
        Palette("Dracula", ["#282a36", "#44475a", "#f8f8f2", "#6272a4", "#8be9fd", "#50fa7b",
                            "#ffb86c", "#ff79c6", "#bd93f9", "#ff5555", "#f1fa8c"]),
    ]


# ---------------------------------------------------------------- swatch files

def read_palette_file(path):
    ext = os.path.splitext(path)[1].lower()
    name = os.path.splitext(os.path.basename(path))[0]
    if ext == ".ase":
        return read_ase(path, name)
    with open(path, "rb") as f:
        raw = f.read()
    text = raw.decode("utf-8", errors="replace")
    if ext == ".gpl" or text.startswith("GIMP Palette"):
        return read_gpl(text, name)
    if ext == ".json":
        d = json.loads(text)
        if isinstance(d, dict) and "colors" in d:
            return Palette.from_dict(d)
        if isinstance(d, list):
            return Palette(name, [c for c in d if isinstance(c, str)])
    cols = re.findall(r"#?\b([0-9a-fA-F]{6})\b", text)
    return Palette(name, ["#" + c for c in cols])


def read_gpl(text, name):
    p = Palette(name)
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("Name:"):
            p.name = line[5:].strip() or name
            continue
        m = re.match(r"^(\d+)\s+(\d+)\s+(\d+)\s*(.*)$", line)
        if m:
            p.add(rgb_to_hex([int(m.group(i)) / 255.0 for i in (1, 2, 3)]), m.group(4).strip())
    return p


def _lab_to_rgb(L, a, b):
    # CIE Lab (D50 in ASE files, close enough treated as D65) to sRGB
    fy = (L + 16) / 116.0
    fx = fy + a / 500.0
    fz = fy - b / 200.0

    def f(t):
        return t ** 3 if t ** 3 > 0.008856 else (t - 16 / 116.0) / 7.787
    X, Y, Z = 0.95047 * f(fx), 1.0 * f(fy), 1.08883 * f(fz)
    r = 3.2406 * X - 1.5372 * Y - 0.4986 * Z
    g = -0.9689 * X + 1.8758 * Y + 0.0415 * Z
    bb = 0.0557 * X - 0.2040 * Y + 1.0570 * Z

    def gam(c):
        c = max(0.0, min(1.0, c))
        return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055
    return gam(r), gam(g), gam(bb)


def read_ase(path, name):
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] != b"ASEF":
        raise ValueError("Not an Adobe Swatch Exchange file")
    count = struct.unpack(">I", data[8:12])[0]
    pos = 12
    p = Palette(name)
    group = None
    for _ in range(count):
        if pos + 6 > len(data):
            break
        btype, blen = struct.unpack(">HI", data[pos:pos + 6])
        body = data[pos + 6:pos + 6 + blen]
        pos += 6 + blen
        if btype == 0xC001:
            n = struct.unpack(">H", body[:2])[0]
            group = body[2:2 + n * 2].decode("utf-16-be").rstrip("\x00")
            if len(p.colors) == 0 and group:
                p.name = group
        elif btype == 0xC002:
            group = None
        elif btype == 0x0001:
            n = struct.unpack(">H", body[:2])[0]
            cname = body[2:2 + n * 2].decode("utf-16-be").rstrip("\x00")
            o = 2 + n * 2
            model = body[o:o + 4]
            o += 4
            if model == b"RGB ":
                r, g, b = struct.unpack(">3f", body[o:o + 12])
                rgb = (r, g, b)
            elif model == b"CMYK":
                c, m, y, k = struct.unpack(">4f", body[o:o + 16])
                rgb = ((1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))
            elif model == b"LAB ":
                L, a, b = struct.unpack(">3f", body[o:o + 12])
                rgb = _lab_to_rgb(L * 100, a, b)
            elif model == b"Gray":
                gval = struct.unpack(">f", body[o:o + 4])[0]
                rgb = (gval, gval, gval)
            else:
                continue
            p.add(rgb_to_hex(rgb), cname)
    return p


def write_palette_file(p, path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".ase":
        return write_ase(p, path)
    if ext == ".gpl":
        lines = ["GIMP Palette", "Name: " + p.name, "Columns: 8", "#"]
        for c, n in zip(p.colors, p.names):
            r, g, b = (int(round(v * 255)) for v in hex_to_rgb(c))
            lines.append("%3d %3d %3d\t%s" % (r, g, b, n or c))
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return
    if ext == ".png":
        from PySide6.QtGui import QImage, QColor
        img = QImage(max(1, len(p.colors)), 1, QImage.Format_RGB32)
        for i, c in enumerate(p.colors):
            img.setPixelColor(i, 0, QColor(c))
        img.save(path)
        return
    if ext == ".json":
        with open(path, "w", encoding="utf-8") as f:
            json.dump(p.to_dict(), f, indent=1)
        return
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(c.lstrip("#") for c in p.colors) + "\n")


def write_ase(p, path):
    blocks = []

    def ustr(s):
        s = s + "\x00"
        return struct.pack(">H", len(s)) + s.encode("utf-16-be")
    blocks.append((0xC001, ustr(p.name)))
    for c, n in zip(p.colors, p.names):
        r, g, b = hex_to_rgb(c)
        body = ustr(n or c) + b"RGB " + struct.pack(">3f", r, g, b) + struct.pack(">H", 2)
        blocks.append((0x0001, body))
    blocks.append((0xC002, b""))
    out = b"ASEF" + struct.pack(">HHI", 1, 0, len(blocks))
    for t, body in blocks:
        out += struct.pack(">HI", t, len(body)) + body
    with open(path, "wb") as f:
        f.write(out)
