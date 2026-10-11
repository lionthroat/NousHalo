"""Colour maths for Nous Halo: hue spins, palette snapping, raster remaps.

Two colour models are offered everywhere a hue moves:
  "perceptual"  OKLCH, so a hue spin keeps how light a colour looks
  "classic"     HSB, the way Illustrator's wheel behaves
Every function works on numpy arrays of shape (..., 3), floats 0..1 sRGB.
"""
import numpy as np

MODES = ("perceptual", "classic")
# OKLCH chroma that counts as the wheel's rim (sRGB tops out near 0.32)
CHROMA_RIM = 0.33


# ---------------------------------------------------------------- hex helpers

def hex_to_rgb(h):
    h = h.lstrip("#")
    return (int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0, int(h[4:6], 16) / 255.0)


def rgb_to_hex(rgb):
    r, g, b = (max(0, min(255, int(round(float(c) * 255)))) for c in rgb[:3])
    return "#%02x%02x%02x" % (r, g, b)


def norm_hex(h):
    return rgb_to_hex(hex_to_rgb(h))


# ---------------------------------------------------------------- OKLab

def _to_linear(c):
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _to_srgb(c):
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1 / 2.4) - 0.055)


def rgb_to_oklab(rgb):
    rgb = np.asarray(rgb, dtype=np.float64)
    lin = _to_linear(rgb)
    r, g, b = lin[..., 0], lin[..., 1], lin[..., 2]
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l, m, s = np.cbrt(l), np.cbrt(m), np.cbrt(s)
    return np.stack([0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
                     1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
                     0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s], axis=-1)


def oklab_to_rgb(lab, clip=True):
    lab = np.asarray(lab, dtype=np.float64)
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
    lin = np.stack([4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
                    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
                    -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s], axis=-1)
    if not clip:
        return lin
    return _to_srgb(lin)


def _in_gamut_lin(lin, eps=1e-4):
    return np.all((lin >= -eps) & (lin <= 1 + eps), axis=-1)


# ---------------------------------------------------------------- HSB

def rgb_to_hsv(rgb):
    rgb = np.asarray(rgb, dtype=np.float64)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    mx = np.max(rgb, axis=-1)
    mn = np.min(rgb, axis=-1)
    d = mx - mn
    safe = np.where(d == 0, 1, d)
    h = np.where(mx == r, ((g - b) / safe) % 6,
                 np.where(mx == g, (b - r) / safe + 2, (r - g) / safe + 4))
    h = np.where(d == 0, 0.0, h * 60.0)
    s = np.where(mx == 0, 0.0, d / np.where(mx == 0, 1, mx))
    return np.stack([h % 360.0, s, mx], axis=-1)


def hsv_to_rgb(hsv):
    hsv = np.asarray(hsv, dtype=np.float64)
    h, s, v = hsv[..., 0] % 360.0, np.clip(hsv[..., 1], 0, 1), np.clip(hsv[..., 2], 0, 1)
    c = v * s
    hp = h / 60.0
    x = c * (1 - np.abs(hp % 2 - 1))
    z = np.zeros_like(h)
    i = np.floor(hp).astype(int) % 6
    r = np.choose(i, [c, x, z, z, x, c])
    g = np.choose(i, [x, c, c, x, z, z])
    b = np.choose(i, [z, z, x, c, c, x])
    m = v - c
    return np.stack([r + m, g + m, b + m], axis=-1)


# ---------------------------------------------------------------- wheel coords
# A colour on the wheel is (hue degrees, radius 0..1, lightness 0..1).

def rgb_to_wheel(rgb, mode):
    rgb = np.asarray(rgb, dtype=np.float64)
    if mode == "classic":
        hsv = rgb_to_hsv(rgb)
        return np.stack([hsv[..., 0], hsv[..., 1], hsv[..., 2]], axis=-1)
    lab = rgb_to_oklab(rgb)
    C = np.hypot(lab[..., 1], lab[..., 2])
    H = np.degrees(np.arctan2(lab[..., 2], lab[..., 1])) % 360.0
    return np.stack([H, np.clip(C / CHROMA_RIM, 0, 1.5), lab[..., 0]], axis=-1)


def wheel_to_rgb(whl, mode, fit_gamut=False):
    whl = np.asarray(whl, dtype=np.float64)
    if mode == "classic":
        return np.clip(hsv_to_rgb(whl), 0, 1)
    H = np.radians(whl[..., 0])
    C = np.maximum(whl[..., 1], 0) * CHROMA_RIM
    L = np.clip(whl[..., 2], 0, 1)
    if fit_gamut:
        # Pull chroma in until the colour fits sRGB (scalar-ish use, a few colours)
        lo = np.zeros_like(C)
        hi = C.copy()
        lab = np.stack([L, hi * np.cos(H), hi * np.sin(H)], axis=-1)
        ok = _in_gamut_lin(oklab_to_rgb(lab, clip=False))
        if not np.all(ok):
            for _ in range(18):
                mid = (lo + hi) / 2
                lab = np.stack([L, mid * np.cos(H), mid * np.sin(H)], axis=-1)
                good = _in_gamut_lin(oklab_to_rgb(lab, clip=False))
                lo = np.where(good, mid, lo)
                hi = np.where(good, hi, mid)
            C = np.where(ok, C, lo)
    lab = np.stack([L, C * np.cos(H), C * np.sin(H)], axis=-1)
    return np.clip(oklab_to_rgb(lab), 0, 1)


def hex_wheel(h, mode):
    return tuple(float(v) for v in rgb_to_wheel(np.array(hex_to_rgb(h)), mode))


def wheel_hex(w, mode):
    return rgb_to_hex(wheel_to_rgb(np.array(w, dtype=np.float64), mode, fit_gamut=True))


def hue_dist(a, b):
    """Signed shortest angle from a to b, degrees."""
    return (np.asarray(b) - np.asarray(a) + 180.0) % 360.0 - 180.0


# ---------------------------------------------------------------- recolour plan
# A Recolor describes what the Halo wheel wants done:
#   moves: {src_hex: (hue, radius, lightness)}  absolute target for that colour
#   spin: degrees added to every hue afterwards
#   sat: multiplier on every radius afterwards
#   light: added to every lightness afterwards
#   mode: "perceptual" | "classic"
#   palette: list of hex to snap the result to (palette lock), or None
#   tolerance: hue width (degrees) a raster move reaches at full strength
#   falloff: extra degrees over which a raster move fades out

class Recolor:
    def __init__(self, mode="perceptual"):
        self.mode = mode
        self.moves = {}
        self.spin = 0.0
        self.sat = 1.0
        self.light = 0.0
        self.palette = None
        self.tolerance = 20.0
        self.falloff = 25.0

    def is_identity(self):
        return not self.moves and abs(self.spin) < 1e-9 and abs(self.sat - 1) < 1e-9 \
            and abs(self.light) < 1e-9 and not self.palette

    def _post(self, w):
        w = np.array(w, dtype=np.float64)
        w[..., 0] = (w[..., 0] + self.spin) % 360.0
        w[..., 1] = w[..., 1] * self.sat
        w[..., 2] = np.clip(w[..., 2] + self.light, 0, 1)
        return w

    def map_hex(self, h):
        """Final colour for one vector colour."""
        h = norm_hex(h)
        if h in self.moves:
            w = np.array(self.moves[h], dtype=np.float64)
        else:
            w = np.array(hex_wheel(h, self.mode))
        if h not in self.moves and abs(self.spin) < 1e-9 and abs(self.sat - 1) < 1e-9 \
                and abs(self.light) < 1e-9:
            out = h
        else:
            out = wheel_hex(self._post(w), self.mode)
        if self.palette:
            out = nearest_hex(out, self.palette)
        return out

    def apply_rgb_array(self, rgb):
        """Recolour an (N, 3) float array of raster pixels."""
        if not self.moves and abs(self.spin) < 1e-9 and abs(self.sat - 1) < 1e-9 \
                and abs(self.light) < 1e-9:
            return rgb
        w = rgb_to_wheel(rgb, self.mode)
        H, R, L = w[..., 0].copy(), w[..., 1].copy(), w[..., 2].copy()
        dH = np.zeros_like(H)
        rmul = np.ones_like(R)
        dL = np.zeros_like(L)
        # Greys have no hue to speak of; moves fade in with radius
        grey_w = np.clip((R - 0.02) / 0.10, 0, 1)
        for src, (th, tr, tl) in self.moves.items():
            sh, sr, sl = hex_wheel(src, self.mode)
            d = np.abs(hue_dist(H, sh))
            wt = np.clip(1 - (d - self.tolerance) / max(self.falloff, 1e-6), 0, 1)
            wt = np.where(d <= self.tolerance, 1.0, wt) * grey_w
            dH += wt * float(hue_dist(sh, th))
            ratio = tr / sr if sr > 1e-3 else 1.0
            rmul *= 1 + wt * (ratio - 1)
            dL += wt * (tl - sl)
        out = np.stack([H + dH, R * rmul, L + dL], axis=-1)
        out = self._post(out)
        return wheel_to_rgb(out, self.mode)


# ---------------------------------------------------------------- palettes

def nearest_index(rgb, palette_rgb):
    """For (N,3) rgb and (P,3) palette, index of the nearest palette colour (OKLab)."""
    lab = rgb_to_oklab(rgb)
    plab = rgb_to_oklab(palette_rgb)
    out = np.empty(lab.shape[0], dtype=np.int64)
    step = 262144
    for i in range(0, lab.shape[0], step):
        chunk = lab[i:i + step]
        d = ((chunk[:, None, :] - plab[None, :, :]) ** 2).sum(-1)
        out[i:i + step] = np.argmin(d, axis=1)
    return out


def nearest_hex(h, palette):
    if not palette:
        return h
    prgb = np.array([hex_to_rgb(p) for p in palette])
    i = nearest_index(np.array([hex_to_rgb(h)]), prgb)[0]
    return norm_hex(palette[int(i)])


def color_distance(a, b):
    la = rgb_to_oklab(np.array(hex_to_rgb(a)))
    lb = rgb_to_oklab(np.array(hex_to_rgb(b)))
    return float(np.sqrt(((la - lb) ** 2).sum()))


_BAYER8 = np.array([
    [0, 32, 8, 40, 2, 34, 10, 42], [48, 16, 56, 24, 50, 18, 58, 26],
    [12, 44, 4, 36, 14, 46, 6, 38], [60, 28, 52, 20, 62, 30, 54, 22],
    [3, 35, 11, 43, 1, 33, 9, 41], [51, 19, 59, 27, 49, 17, 57, 25],
    [15, 47, 7, 39, 13, 45, 5, 37], [63, 31, 55, 23, 61, 29, 53, 21]]) / 64.0 - 0.5


def remap_pixels(rgba, palette, dither=0.0):
    """rgba: (H, W, 4) uint8 straight alpha. Returns a new array snapped to palette.

    dither 0..1 is ordered (Bayer) dithering strength, so a soft painting keeps
    the feel of its gradients without leaving the palette."""
    h, w = rgba.shape[:2]
    rgb = rgba[..., :3].astype(np.float64) / 255.0
    if dither > 0:
        tile = np.tile(_BAYER8, (h // 8 + 1, w // 8 + 1))[:h, :w]
        spread = dither * 0.22 / max(1.0, len(palette) ** (1 / 3))
        rgb = np.clip(rgb + tile[..., None] * spread * 3, 0, 1)
    prgb = np.array([hex_to_rgb(p) for p in palette])
    flat = rgb.reshape(-1, 3)
    visible = rgba[..., 3].reshape(-1) > 0
    idx = np.zeros(flat.shape[0], dtype=np.int64)
    if visible.any():
        idx[visible] = nearest_index(flat[visible], prgb)
    out = rgba.copy()
    p8 = np.round(prgb * 255).astype(np.uint8)
    newrgb = p8[idx].reshape(h, w, 3)
    out[..., :3] = np.where(rgba[..., 3:4] > 0, newrgb, rgba[..., :3])
    return out


def recolor_pixels(rgba, recolor):
    """Apply a Recolor (hue moves/spin) to (H, W, 4) uint8 straight alpha pixels."""
    if recolor.is_identity():
        return rgba
    h, w = rgba.shape[:2]
    out = rgba.copy()
    flat = rgba[..., :3].reshape(-1, 3)
    vis = rgba[..., 3].reshape(-1) > 0
    res = flat.copy()
    if vis.any():
        step = 1 << 20
        idx = np.nonzero(vis)[0]
        for i in range(0, idx.size, step):
            part = idx[i:i + step]
            rgb = flat[part].astype(np.float64) / 255.0
            res[part] = np.round(np.clip(recolor.apply_rgb_array(rgb), 0, 1) * 255).astype(np.uint8)
    out[..., :3] = res.reshape(h, w, 3)
    if recolor.palette:
        out = remap_pixels(out, recolor.palette)
    return out


def extract_palette(rgba, k=8, seed=7):
    """Dominant colours of an image (k-means in OKLab on a sample)."""
    flat = rgba.reshape(-1, 4)
    flat = flat[flat[:, 3] > 127][:, :3]
    if flat.shape[0] == 0:
        return []
    rng = np.random.default_rng(seed)
    if flat.shape[0] > 20000:
        flat = flat[rng.choice(flat.shape[0], 20000, replace=False)]
    lab = rgb_to_oklab(flat.astype(np.float64) / 255.0)
    k = max(1, min(k, lab.shape[0]))
    # k-means++ start
    centers = [lab[rng.integers(lab.shape[0])]]
    for _ in range(1, k):
        d = np.min(((lab[:, None, :] - np.array(centers)[None]) ** 2).sum(-1), axis=1)
        if d.sum() <= 0:
            break
        centers.append(lab[rng.choice(lab.shape[0], p=d / d.sum())])
    centers = np.array(centers)
    for _ in range(14):
        lbl = np.argmin(((lab[:, None, :] - centers[None]) ** 2).sum(-1), axis=1)
        for j in range(centers.shape[0]):
            pts = lab[lbl == j]
            if len(pts):
                centers[j] = pts.mean(0)
    counts = np.bincount(lbl, minlength=centers.shape[0])
    order = np.argsort(-counts)
    rgb = oklab_to_rgb(centers[order])
    return [rgb_to_hex(c) for c, n in zip(rgb, counts[order]) if n > 0]


# ---------------------------------------------------------------- harmonies

HARMONIES = {
    "Complementary": [0, 180],
    "Analogous": [-30, 0, 30],
    "Triad": [0, 120, 240],
    "Split complementary": [0, 150, 210],
    "Square": [0, 90, 180, 270],
    "Hue-shifted ramp": None,
    "Monochrome": None,
}


def harmony(base_hex, name, mode="perceptual", steps=5, shift=25.0):
    """Colours built from base_hex. A hue-shifted ramp darkens toward cool
    (blue) and lightens toward warm (yellow), the way painters shade."""
    H, R, L = hex_wheel(base_hex, mode)
    offs = HARMONIES.get(name)
    if offs is not None:
        return [wheel_hex(((H + o) % 360, R, L), mode) for o in offs]
    out = []
    steps = max(2, steps)
    for i in range(steps):
        t = i / (steps - 1)  # 0 dark .. 1 light
        if name == "Monochrome":
            out.append(wheel_hex((H, R, 0.15 + 0.8 * t), mode))
            continue
        # warm target ~ 90 deg (yellow) in OKLCH, ~55 in HSB; cool ~ 265 / 240
        warm = 90.0 if mode == "perceptual" else 55.0
        cool = 265.0 if mode == "perceptual" else 240.0
        target = warm if t >= 0.5 else cool
        amt = abs(t - 0.5) * 2 * shift
        d = float(hue_dist(H, target))
        h = (H + max(-amt, min(amt, d))) % 360
        light = 0.18 + 0.74 * t if mode == "perceptual" else 0.25 + 0.72 * t
        rad = R * (1 - 0.45 * abs(t - 0.5) * 2) if mode == "perceptual" else R * (1 - 0.35 * t)
        out.append(wheel_hex((h, rad, light), mode))
    return out


def sort_colors(colors, key="hue"):
    def k(h):
        H, R, L = hex_wheel(h, "perceptual")
        if key == "lightness":
            return (L,)
        if R < 0.06:  # greys last, by lightness
            return (1, 0, L)
        return (0, H, L)
    return sorted(colors, key=k)
