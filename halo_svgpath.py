"""SVG path data <-> QPainterPath, for glyph outlines and SVG export."""
import math
import re

from PySide6.QtGui import QPainterPath

_tok = re.compile(r"[MmLlHhVvCcSsQqTtZzAa]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def parse_d(d, path=None):
    p = path if path is not None else QPainterPath()
    toks = _tok.findall(d)
    i = 0
    cmd = None
    x = y = sx = sy = 0.0
    lcx = lcy = None  # last control point (for S / T)
    prev = None

    def num():
        nonlocal i
        v = float(toks[i])
        i += 1
        return v

    while i < len(toks):
        t = toks[i]
        if t.isalpha():
            cmd = t
            i += 1
            if cmd in "Zz":
                p.closeSubpath()
                x, y = sx, sy
                prev = cmd
                lcx = lcy = None
                continue
        if cmd is None:
            i += 1
            continue
        rel = cmd.islower()
        c = cmd.upper()
        ox, oy = (x, y) if rel else (0.0, 0.0)
        if c == "M":
            x, y = num() + ox, num() + oy
            p.moveTo(x, y)
            sx, sy = x, y
            cmd = "l" if rel else "L"
            lcx = lcy = None
        elif c == "L":
            x, y = num() + ox, num() + oy
            p.lineTo(x, y)
            lcx = lcy = None
        elif c == "H":
            x = num() + (x if rel else 0.0)
            p.lineTo(x, y)
            lcx = lcy = None
        elif c == "V":
            y = num() + (y if rel else 0.0)
            p.lineTo(x, y)
            lcx = lcy = None
        elif c == "C":
            x1, y1 = num() + ox, num() + oy
            x2, y2 = num() + ox, num() + oy
            x, y = num() + ox, num() + oy
            p.cubicTo(x1, y1, x2, y2, x, y)
            lcx, lcy = x2, y2
        elif c == "S":
            x1, y1 = (2 * x - lcx, 2 * y - lcy) if lcx is not None else (x, y)
            x2, y2 = num() + ox, num() + oy
            x, y = num() + ox, num() + oy
            p.cubicTo(x1, y1, x2, y2, x, y)
            lcx, lcy = x2, y2
        elif c == "Q":
            x1, y1 = num() + ox, num() + oy
            x, y = num() + ox, num() + oy
            p.quadTo(x1, y1, x, y)
            lcx, lcy = x1, y1
        elif c == "T":
            x1, y1 = (2 * x - lcx, 2 * y - lcy) if lcx is not None else (x, y)
            x, y = num() + ox, num() + oy
            p.quadTo(x1, y1, x, y)
            lcx, lcy = x1, y1
        elif c == "A":
            # Rare in our sources; approximate the arc with a line
            for _ in range(5):
                num()
            x, y = num() + ox, num() + oy
            p.lineTo(x, y)
            lcx = lcy = None
        prev = cmd
    return p


def qpath_to_d(path, fmt="%.3f"):
    out = []
    n = path.elementCount()
    i = 0
    while i < n:
        e = path.elementAt(i)
        if e.type == QPainterPath.MoveToElement:
            out.append("M" + fmt % e.x + " " + fmt % e.y)
            i += 1
        elif e.type == QPainterPath.LineToElement:
            out.append("L" + fmt % e.x + " " + fmt % e.y)
            i += 1
        elif e.type == QPainterPath.CurveToElement:
            c1 = e
            c2 = path.elementAt(i + 1)
            end = path.elementAt(i + 2)
            out.append("C" + " ".join(fmt % v for v in (c1.x, c1.y, c2.x, c2.y, end.x, end.y)))
            i += 3
        else:
            i += 1
    return "".join(out)


def subpaths_to_d(subpaths, fmt="%.3f"):
    out = []
    for sp in subpaths:
        nodes = sp["nodes"]
        if not nodes:
            continue
        out.append("M" + fmt % nodes[0][0] + " " + fmt % nodes[0][1])
        segs = list(zip(nodes, nodes[1:]))
        if sp.get("closed") and len(nodes) > 1:
            segs.append((nodes[-1], nodes[0]))
        for a, b in segs:
            if a[4] == a[0] and a[5] == a[1] and b[2] == b[0] and b[3] == b[1]:
                out.append("L" + fmt % b[0] + " " + fmt % b[1])
            else:
                out.append("C" + " ".join(fmt % v for v in (a[4], a[5], b[2], b[3], b[0], b[1])))
        if sp.get("closed"):
            out.append("Z")
    return "".join(out)
