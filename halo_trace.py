"""Image Trace: a scanned drawing becomes filled vector shapes.

Outline tracing, the way Illustrator's Image Trace does line art: the ink is
separated from the paper by a threshold, its edges are followed pixel by
pixel, specks are dropped, sharp corners are found, and smooth cubic Bezier
curves are fitted between them. Each separate piece of ink becomes one shape,
with its holes (the inside of an O, the gap in a loop) cut out.

Pure numpy; no OpenCV or potrace needed.
"""
import math

import numpy as np

# ---------------------------------------------------------------- thresholding


def to_gray(rgba):
    """Luminance 0..255 with transparency treated as white paper."""
    rgb = rgba[..., :3].astype(np.float32)
    a = rgba[..., 3:4].astype(np.float32) / 255.0
    rgb = rgb * a + 255.0 * (1 - a)
    return 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]


def otsu(gray):
    """The threshold that best splits ink from paper."""
    hist = np.bincount(np.clip(gray, 0, 255).astype(np.uint8).ravel(), minlength=256).astype(np.float64)
    total = hist.sum()
    if total == 0:
        return 128
    levels = np.arange(256)
    w0 = np.cumsum(hist)
    w1 = total - w0
    s0 = np.cumsum(hist * levels)
    m0 = s0 / np.maximum(w0, 1)
    m1 = (s0[-1] - s0) / np.maximum(w1, 1)
    between = w0 * w1 * (m0 - m1) ** 2
    return int(np.argmax(between))


# ---------------------------------------------------------------- outlines


def trace_loops(mask):
    """Closed outlines of the True pixels, following pixel edges.

    Returns a list of (N, 2) int arrays of grid corners (x, y). Every edge
    keeps the ink on its right-hand side, so outer outlines and holes run in
    opposite directions. Where two ink pixels touch only at a corner, the
    outline turns so they stay joined (thin diagonal lines don't break)."""
    h, w = mask.shape
    m = np.zeros((h + 2, w + 2), dtype=bool)
    m[1:-1, 1:-1] = mask
    W = w + 3  # vertex grid is (h+3) x (w+3) in padded coordinates

    # horizontal edges: between rows r-1 and r, columns c
    up, dn = m[:-1, :], m[1:, :]
    # ink below, paper above: run left -> right along y = r
    r, c = np.nonzero(dn & ~up)
    starts = [(r + 1) * W + c]
    ends = [(r + 1) * W + c + 1]
    dirs = [np.zeros_like(r)]           # 0 = +x
    r, c = np.nonzero(up & ~dn)
    starts.append((r + 1) * W + c + 1)
    ends.append((r + 1) * W + c)
    dirs.append(np.full_like(r, 2))     # 2 = -x
    # vertical edges: between columns c-1 and c, rows r
    lf, rt = m[:, :-1], m[:, 1:]
    r, c = np.nonzero(lf & ~rt)         # ink left: run down
    starts.append(r * W + c + 1)
    ends.append((r + 1) * W + c + 1)
    dirs.append(np.ones_like(r))        # 1 = +y
    r, c = np.nonzero(rt & ~lf)         # ink right: run up
    starts.append((r + 1) * W + c + 1)
    ends.append(r * W + c + 1)
    dirs.append(np.full_like(r, 3))     # 3 = -y
    s = np.concatenate(starts)
    e = np.concatenate(ends)
    d = np.concatenate(dirs)
    if s.size == 0:
        return []
    order = np.argsort(s, kind="stable")
    s, e, d = s[order], e[order], d[order]
    first = np.searchsorted(s, s)  # index of the first edge out of each start
    count = np.bincount(s)[s]      # how many edges leave that vertex (1 or 2)
    used = np.zeros(s.size, dtype=bool)
    s_l, e_l, d_l = s.tolist(), e.tolist(), d.tolist()
    first_l, count_l = first.tolist(), count.tolist()
    # out-edges by start vertex: (index of first, count)
    lookup = {}
    for i in range(s.size):
        if s_l[i] not in lookup:
            lookup[s_l[i]] = (first_l[i], count_l[i])
    loops = []
    for i0 in range(s.size):
        if used[i0]:
            continue
        pts = []
        i = i0
        while True:
            used[i] = True
            pts.append(s_l[i])
            v = e_l[i]
            if v == s_l[i0]:
                break
            f, n = lookup[v]
            if n == 1:
                nxt = f
            else:
                # two ways on: take the right turn (keeps diagonal ink joined)
                want = (d_l[i] + 1) % 4
                a, b = f, f + 1
                if used[a]:
                    nxt = b
                elif used[b]:
                    nxt = a
                else:
                    nxt = a if d_l[a] == want else b
            if used[nxt]:
                break
            i = nxt
        arr = np.array(pts, dtype=np.int64)
        loops.append(np.stack([arr % W - 1, arr // W - 1], axis=1))
    return loops


def signed_area(poly):
    x, y = poly[:, 0].astype(np.float64), poly[:, 1].astype(np.float64)
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y))


def point_in_poly(px, py, poly):
    x, y = poly[:, 0], poly[:, 1]
    x2, y2 = np.roll(x, -1), np.roll(y, -1)
    cond = (y > py) != (y2 > py)
    with np.errstate(divide="ignore", invalid="ignore"):
        xs = x + (py - y) * (x2 - x) / (y2 - y)
    return bool(np.count_nonzero(cond & (px < xs)) % 2)


# ---------------------------------------------------------------- smoothing and curves


def _circ_smooth(pts, sigma):
    if sigma <= 0 or len(pts) < 5:
        return pts.astype(np.float64)
    r = max(1, int(math.ceil(sigma * 3)))
    r = min(r, len(pts) // 2 - 1) if len(pts) > 4 else 1
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    k /= k.sum()
    out = np.empty_like(pts, dtype=np.float64)
    for c in (0, 1):
        v = np.concatenate([pts[-r:, c], pts[:, c], pts[:r, c]]).astype(np.float64)
        out[:, c] = np.convolve(v, k, mode="valid")
    return out


def _densify(loop):
    """Pixel-edge corners -> midpoints of every unit edge (an even 1 px spacing
    that rounds off the stair-steps)."""
    a = loop.astype(np.float64)
    b = np.roll(a, -1, axis=0)
    seg = b - a
    lens = np.abs(seg).sum(axis=1).astype(int)
    pts = []
    for p, sv, n in zip(a, seg, lens):
        if n <= 0:
            continue
        step = sv / n
        t = np.arange(n)[:, None] + 0.5
        pts.append(p + step * t)
    return np.concatenate(pts) if pts else a


def _corners(pts, angle_deg, k=4):
    """Indices where the outline turns sharply (local maxima of the turn)."""
    n = len(pts)
    if n < 2 * k + 3:
        return []
    a = pts - np.roll(pts, k, axis=0)
    b = np.roll(pts, -k, axis=0) - pts
    na = np.linalg.norm(a, axis=1)
    nb = np.linalg.norm(b, axis=1)
    cos = np.einsum("ij,ij->i", a, b) / np.maximum(na * nb, 1e-9)
    turn = np.degrees(np.arccos(np.clip(cos, -1, 1)))
    cand = np.nonzero(turn > angle_deg)[0]
    out = []
    for i in cand:
        lo, hi = i - k, i + k + 1
        window = turn[np.arange(lo, hi) % n]
        if turn[i] >= window.max() - 1e-9:
            if not out or (i - out[-1]) > k:
                out.append(int(i))
    if len(out) > 1 and (out[0] + n - out[-1]) <= k:
        out.pop()
    return out


def _bezier(ctrl, t):
    mt = 1 - t
    return (mt ** 3)[:, None] * ctrl[0] + (3 * mt * mt * t)[:, None] * ctrl[1] + \
        (3 * mt * t * t)[:, None] * ctrl[2] + (t ** 3)[:, None] * ctrl[3]


def _chord_params(pts):
    d = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(pts, axis=0), axis=1))])
    return d / d[-1] if d[-1] > 0 else np.linspace(0, 1, len(pts))


def _fit_one(pts, t, th1, th2):
    """Least-squares cubic through pts with fixed end tangents (Schneider)."""
    p0, p3 = pts[0], pts[-1]
    mt = 1 - t
    A1 = (3 * mt * mt * t)[:, None] * th1
    A2 = (3 * mt * t * t)[:, None] * th2
    C00 = (A1 * A1).sum()
    C01 = (A1 * A2).sum()
    C11 = (A2 * A2).sum()
    base = (mt ** 3 + 3 * mt * mt * t)[:, None] * p0 + (3 * mt * t * t + t ** 3)[:, None] * p3
    tmp = pts - base
    X0 = (A1 * tmp).sum()
    X1 = (A2 * tmp).sum()
    det = C00 * C11 - C01 * C01
    seg = np.linalg.norm(p3 - p0)
    if abs(det) > 1e-12:
        a1 = (X0 * C11 - X1 * C01) / det
        a2 = (C00 * X1 - C01 * X0) / det
    else:
        a1 = a2 = seg / 3
    if a1 < 1e-6 * seg or a2 < 1e-6 * seg or a1 > 3 * seg or a2 > 3 * seg:
        a1 = a2 = seg / 3
    return np.array([p0, p0 + th1 * a1, p3 + th2 * a2, p3])


def _reparam(ctrl, pts, t):
    q = _bezier(ctrl, t)
    mt = 1 - t
    d1 = (3 * mt * mt)[:, None] * (ctrl[1] - ctrl[0]) + (6 * mt * t)[:, None] * (ctrl[2] - ctrl[1]) + \
        (3 * t * t)[:, None] * (ctrl[3] - ctrl[2])
    d2 = (6 * mt)[:, None] * (ctrl[2] - 2 * ctrl[1] + ctrl[0]) + (6 * t)[:, None] * (ctrl[3] - 2 * ctrl[2] + ctrl[1])
    diff = q - pts
    num = (diff * d1).sum(axis=1)
    den = (d1 * d1).sum(axis=1) + (diff * d2).sum(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        nt = np.where(np.abs(den) > 1e-12, t - num / den, t)
    return np.clip(nt, 0, 1)


def _unit(v):
    n = np.linalg.norm(v)
    return v / n if n > 1e-12 else np.array([1.0, 0.0])


def fit_curves(pts, err, th1=None, th2=None, depth=0):
    """Cubic Beziers through an open run of points: [[p0, c1, c2, p3], ...]."""
    n = len(pts)
    if n < 2:
        return []
    if th1 is None:
        th1 = _unit(pts[min(2, n - 1)] - pts[0])
    if th2 is None:
        th2 = _unit(pts[max(n - 3, 0)] - pts[-1])
    if n == 2:
        seg = np.linalg.norm(pts[1] - pts[0]) / 3
        return [np.array([pts[0], pts[0] + th1 * seg, pts[1] + th2 * seg, pts[1]])]
    t = _chord_params(pts)
    ctrl = _fit_one(pts, t, th1, th2)
    for it in range(5):
        dist = np.linalg.norm(_bezier(ctrl, t) - pts, axis=1)
        worst = int(np.argmax(dist))
        if dist[worst] <= err:
            return [ctrl]
        if dist[worst] > err * 4 or it == 4:
            break
        t = _reparam(ctrl, pts, t)
        ctrl = _fit_one(pts, t, th1, th2)
    dist = np.linalg.norm(_bezier(ctrl, t) - pts, axis=1)
    split = int(np.argmax(dist[1:-1])) + 1 if n > 2 else 1
    if depth > 40 or split <= 0 or split >= n - 1:
        return [ctrl]
    tc = _unit(pts[max(split - 1, 0)] - pts[min(split + 1, n - 1)])
    return fit_curves(pts[:split + 1], err, th1, tc, depth + 1) + \
        fit_curves(pts[split:], err, -tc, th2, depth + 1)


def loop_to_nodes(loop, smooth, corner_deg, err):
    """One pixel outline -> node list for a closed subpath."""
    dense = _densify(loop)
    if len(dense) < 8:
        p = dense
        return [[x, y, x, y, x, y] for x, y in p]
    light = _circ_smooth(dense, 0.8)
    pts = _circ_smooth(dense, smooth)
    cs = _corners(light, corner_deg)
    n = len(pts)
    if cs:
        for c in cs:
            pts[c] = light[c]  # corners stay sharp
        # rotate so the outline starts at a corner
        start = cs[0]
        pts = np.roll(pts, -start, axis=0)
        cs = [(c - start) % n for c in cs] + [n]
        runs = []
        for a, b in zip(cs, cs[1:]):
            idx = np.arange(a, b + 1) % n
            runs.append(pts[idx])
        curves = []
        for run in runs:
            curves += fit_curves(run, err)
    else:
        # smooth all the way round: split in two and keep the joins smooth
        h = n // 2
        t0 = _unit(pts[1] - pts[-1])
        th = _unit(pts[h + 1] - pts[h - 1])
        a = pts[np.arange(0, h + 1)]
        b = pts[np.arange(h, n + 1) % n]
        curves = fit_curves(a, err, t0, -th) + fit_curves(b, err, th, -t0)
    nodes = []
    for cv in curves:
        if nodes:
            nodes[-1][4], nodes[-1][5] = cv[1]
        else:
            nodes.append([cv[0][0], cv[0][1], cv[0][0], cv[0][1], cv[1][0], cv[1][1]])
        nodes.append([cv[3][0], cv[3][1], cv[2][0], cv[2][1], cv[3][0], cv[3][1]])
    # the last node is the first one again: fold its in-handle in
    if len(nodes) > 1:
        last = nodes.pop()
        nodes[0][2], nodes[0][3] = last[2], last[3]
    return nodes


# ---------------------------------------------------------------- the whole trace


class TraceOptions:
    def __init__(self):
        self.threshold = None      # None = automatic
        self.invert = False        # trace light marks on a dark ground
        self.speck = 8             # ignore ink (or holes) smaller than this many pixels
        self.smooth = 1.5          # 0.5 crisp .. 4 very smooth
        self.corners = 70.0        # turns sharper than this (degrees) stay corners
        self.tolerance = 0.6       # how far curves may stray from the pixels


def trace(rgba, opts):
    """rgba (H, W, 4) uint8 -> list of shapes; each shape is a list of node
    subpaths (first the outline, then its holes) in pixel coordinates."""
    gray = to_gray(rgba)
    thr = opts.threshold if opts.threshold is not None else otsu(gray)
    mask = gray > thr if opts.invert else gray <= thr
    loops = trace_loops(mask)
    outers, holes = [], []
    for lp in loops:
        a = signed_area(lp)
        if abs(a) < opts.speck:
            continue
        # ink on the right: outer outlines run one way, holes the other
        (outers if a > 0 else holes).append(lp)
    shapes = [[o] for o in outers]
    if holes and outers:
        boxes = [(o[:, 0].min(), o[:, 1].min(), o[:, 0].max(), o[:, 1].max(), abs(signed_area(o))) for o in outers]
        for hl in holes:
            px, py = hl[0, 0] + 0.5, hl[0, 1] + 0.5
            hx0, hy0, hx1, hy1 = hl[:, 0].min(), hl[:, 1].min(), hl[:, 0].max(), hl[:, 1].max()
            best, best_area = None, None
            for k, (x0, y0, x1, y1, area) in enumerate(boxes):
                if x0 <= hx0 and y0 <= hy0 and x1 >= hx1 and y1 >= hy1 and \
                        (best_area is None or area < best_area) and point_in_poly(px, py, outers[k].astype(float)):
                    best, best_area = k, area
            if best is not None:
                shapes[best].append(hl)
    err = opts.tolerance * (0.5 + opts.smooth / 2)
    out = []
    for loops_ in shapes:
        subs = []
        for lp in loops_:
            nodes = loop_to_nodes(lp, opts.smooth, opts.corners, err)
            if len(nodes) >= 2:
                subs.append({"nodes": nodes, "closed": True})
        if subs:
            out.append(subs)
    return out, thr
