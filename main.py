#!/usr/bin/env python3
"""news-image-20261119 -- "One Day on Earth".

Sums up the world's news of Thursday 19 November 2026 in one 1024x1024
newspaper page, drawn from scratch in pure standard-library Python.

Deterministic by construction: a fixed random seed, a fixed embedded font
blob (font.bin, rasterized at build time from Liberation fonts), and a
fixed PNG encoder. Two runs write byte-identical files.

Writes /output/entry.png, or $MAKEFUN_OUTPUT_DIR/entry.png when set.
"""
import math
import os
import random
import struct
import zlib

W, H = 1024, 1024
SEED = 20261119

# ---------------------------------------------------------------- palette
PAPER = (247, 243, 234)
PAPER2 = (240, 234, 220)
INK = (24, 24, 24)
INK2 = (78, 74, 66)
RED = (176, 32, 24)
NAVY = (30, 56, 92)
RULE = (186, 176, 154)
PANEL_LINE = (205, 196, 172)
SKY = (214, 228, 238)
SEA = (203, 222, 233)
LAND = (196, 188, 152)
DOT_LAND = (176, 164, 120)
WHITE = (255, 255, 255)

# Region colors for the map pins (and the matching region labels).
REGION_COLORS = {
    "EUROPE": (40, 90, 180),
    "AMERICAS": (185, 45, 40),
    "ASIA-PACIFIC": (0, 140, 120),
    "MIDDLE EAST": (150, 85, 165),
    "OCEANIA": (35, 125, 75),
}

# ---------------------------------------------------------------- fonts
# font.bin format: magic "NFNT1" + zlib payload.
# Payload: u16 variant count, then per variant:
#   u8 name length, name bytes, u8 size, i16 ascent, i16 descent,
#   u16 glyph count, then per glyph:
#   u32 codepoint, i8 bx, i8 by, u8 width, u8 height, u8 advance,
#   u16 data length, data bytes (4-bit coverage, two pixels per byte,
#   row-major across the whole glyph, high nibble first).
# bx/by: bitmap top-left relative to the pen origin on the baseline.


class Font:
    __slots__ = ("name", "size", "ascent", "descent", "glyphs")

    def __init__(self, name, size, ascent, descent, glyphs):
        self.name = name
        self.size = size
        self.ascent = ascent
        self.descent = descent
        self.glyphs = glyphs


def load_fonts(path):
    with open(path, "rb") as fh:
        raw = fh.read()
    if raw[:5] != b"NFNT1":
        raise ValueError("bad font blob")
    data = zlib.decompress(raw[5:])
    off = 0
    (nvar,) = struct.unpack_from(">H", data, off)
    off += 2
    fonts = {}
    for _ in range(nvar):
        (nlen,) = struct.unpack_from(">B", data, off)
        off += 1
        name = data[off:off + nlen].decode("ascii")
        off += nlen
        size, ascent, descent, ng = struct.unpack_from(">BhhH", data, off)
        off += 7
        glyphs = {}
        for _ in range(ng):
            cp, bx, by, gw, gh, adv, dlen = struct.unpack_from(
                ">IbbBBBH", data, off)
            off += 11
            glyphs[cp] = (bx, by, gw, gh, adv, data[off:off + dlen])
            off += dlen
        fonts[name] = Font(name, size, ascent, descent, glyphs)
    return fonts


FONTS = {}


def text_width(fname, s):
    f = FONTS[fname]
    g = f.glyphs
    q = g[63]
    return sum(g.get(ord(c), q)[4] for c in s)


def draw_text(cv, x, y_base, s, fname, rgb):
    """Draw one line with its baseline at y_base. Returns end x."""
    f = FONTS[fname]
    g = f.glyphs
    q = g[63]
    cx = x
    for ch in s:
        bx, by, gw, gh, adv, data = g.get(ord(ch), q)
        if gw and gh:
            gx, gy = cx + bx, y_base + by
            for yy in range(gh):
                base = yy * gw
                for xx in range(gw):
                    idx = base + xx
                    byte = data[idx >> 1]
                    a = (byte >> 4) if (idx & 1) == 0 else (byte & 15)
                    if a:
                        cv.blend_px(gx + xx, gy + yy, rgb, a)
        cx += adv
    return cx


def draw_text_centered(cv, cx, y_base, s, fname, rgb):
    draw_text(cv, cx - text_width(fname, s) // 2, y_base, s, fname, rgb)


def draw_text_right(cv, rx, y_base, s, fname, rgb):
    draw_text(cv, rx - text_width(fname, s), y_base, s, fname, rgb)


def wrap_segments(segments, width):
    """Word-wrap styled segments into lines.

    segments: list of (text, fontname, color). Returns a list of lines;
    each line is a list of (word, fontname, color, word_width).
    """
    words = []
    for text, fname, color in segments:
        for w in text.split(" "):
            if w:
                words.append((w, fname, color, text_width(fname, w)))
    lines = []
    cur = []
    cur_w = 0
    for w, fname, color, ww in words:
        sw = FONTS[fname].glyphs[32][4]
        add = ww if not cur else sw + ww
        if cur and cur_w + add > width:
            lines.append(cur)
            cur = [(w, fname, color, ww)]
            cur_w = ww
        else:
            cur.append((w, fname, color, ww))
            cur_w += add
    if cur:
        lines.append(cur)
    return lines


def draw_para(cv, x, y_top, width, segments, line_h):
    """Draw a wrapped paragraph; returns the height used."""
    lines = wrap_segments(segments, width)
    y = y_top
    for line in lines:
        asc = max(FONTS[fname].ascent for _w, fname, _c, _ww in line)
        cx = x
        for w, fname, color, ww in line:
            draw_text(cv, cx, y + asc, w, fname, color)
            cx += ww + FONTS[fname].glyphs[32][4]
        y += line_h
    return len(lines) * line_h


def para_lines(segments, width):
    return wrap_segments(segments, width)


# ---------------------------------------------------------------- canvas
class Canvas:
    def __init__(self, w, h, bg):
        self.w = w
        self.h = h
        self.buf = bytearray(bytes(bg) * (w * h))
        self.clip = None  # (x0, y0, x1, y1) or None

    def set_clip(self, x, y, w, h):
        self.clip = (x, y, x + w, y + h)

    def clear_clip(self):
        self.clip = None

    def rect(self, x, y, w, h, rgb):
        x0 = max(0, x)
        y0 = max(0, y)
        x1 = min(self.w, x + w)
        y1 = min(self.h, y + h)
        if self.clip:
            cx0, cy0, cx1, cy1 = self.clip
            x0 = max(x0, cx0)
            y0 = max(y0, cy0)
            x1 = min(x1, cx1)
            y1 = min(y1, cy1)
        if x1 <= x0 or y1 <= y0:
            return
        row = bytes(rgb) * (x1 - x0)
        n = len(row)
        for yy in range(y0, y1):
            i = (yy * self.w + x0) * 3
            self.buf[i:i + n] = row

    def blend_px(self, x, y, rgb, a):
        # a: coverage 0..15
        if a <= 0 or x < 0 or y < 0 or x >= self.w or y >= self.h:
            return
        if self.clip:
            cx0, cy0, cx1, cy1 = self.clip
            if not (cx0 <= x < cx1 and cy0 <= y < cy1):
                return
        i = (y * self.w + x) * 3
        b = self.buf
        if a >= 15:
            b[i] = rgb[0]
            b[i + 1] = rgb[1]
            b[i + 2] = rgb[2]
            return
        b[i] = (rgb[0] * a + b[i] * (15 - a)) // 15
        b[i + 1] = (rgb[1] * a + b[i + 1] * (15 - a)) // 15
        b[i + 2] = (rgb[2] * a + b[i + 2] * (15 - a)) // 15

    def hline(self, x, y, w, rgb):
        self.rect(x, y, w, 1, rgb)

    def vline(self, x, y, h, rgb):
        self.rect(x, y, 1, h, rgb)

    def line(self, x0, y0, x1, y1, rgb):
        dx = abs(x1 - x0)
        dy = -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        while True:
            self.blend_px(x0, y0, rgb, 15)
            if x0 == x1 and y0 == y1:
                return
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy

    def circle(self, cx, cy, r, rgb):
        for dy in range(-r, r + 1):
            dx = math.isqrt(max(0, r * r - dy * dy))
            self.rect(cx - dx, cy + dy, 2 * dx + 1, 1, rgb)

    def ring(self, cx, cy, r, rgb):
        # 1px outline circle
        for dy in range(-r, r + 1):
            dx = math.isqrt(max(0, r * r - dy * dy))
            self.blend_px(cx - dx, cy + dy, rgb, 15)
            self.blend_px(cx + dx, cy + dy, rgb, 15)

    def ellipse(self, cx, cy, rx, ry, rgb):
        for dy in range(-ry, ry + 1):
            t = 1.0 - (dy * dy) / float(ry * ry)
            dx = int(rx * math.sqrt(max(0.0, t)))
            self.rect(cx - dx, cy + dy, 2 * dx + 1, 1, rgb)

    def poly(self, points, rgb):
        # Scanline fill for a simple polygon.
        if len(points) < 3:
            return
        ys = [p[1] for p in points]
        y0, y1 = max(0, min(ys)), min(self.h - 1, max(ys))
        n = len(points)
        for y in range(y0, y1 + 1):
            xs = []
            for i in range(n):
                x_a, y_a = points[i]
                x_b, y_b = points[(i + 1) % n]
                if (y_a <= y < y_b) or (y_b <= y < y_a):
                    t = (y - y_a) / float(y_b - y_a)
                    xs.append(x_a + t * (x_b - x_a))
            xs.sort()
            for j in range(0, len(xs) - 1, 2):
                xa, xb = int(math.ceil(xs[j])), int(xs[j + 1])
                if xb > xa:
                    self.rect(xa, y, xb - xa, 1, rgb)


def vgrad(cv, x, y, w, h, top, bottom):
    for i in range(h):
        t = i / float(max(1, h - 1))
        c = tuple(int(top[k] + (bottom[k] - top[k]) * t) for k in range(3))
        cv.rect(x, y + i, w, 1, c)


def skyline(cv, x, y_base, w, h_max, color, seed, windows=None):
    """Deterministic building silhouettes along a baseline."""
    r = random.Random(seed)
    cx = x
    while cx < x + w - 6:
        bw = r.randint(12, 30)
        bh = r.randint(max(6, h_max // 3), h_max)
        cv.rect(cx, y_base - bh, bw, bh, color)
        if windows:
            wr = random.Random(seed + cx)
            for wy in range(y_base - bh + 3, y_base - 3, 6):
                for wx in range(cx + 2, cx + bw - 2, 5):
                    if wr.random() < 0.35:
                        cv.rect(wx, wy, 2, 3, windows)
        cx += bw + r.randint(2, 6)

# ---------------------------------------------------------------- scenes
# Every scene is drawn from primitives with a fixed seed: no photos,
# no external art, byte-identical every run.


def star_points(cx, cy, r_out, r_in, n=5):
    pts = []
    for i in range(2 * n):
        r = r_out if i % 2 == 0 else r_in
        a = math.pi * i / n - math.pi / 2
        pts.append((int(cx + r * math.cos(a)), int(cy + r * math.sin(a))))
    return pts


def rain(cv, x, y, w, h, color, seed, n):
    r = random.Random(seed)
    for _ in range(n):
        rx = x + r.randint(0, w - 1)
        ry = y + r.randint(0, h - 1)
        cv.line(rx, ry, rx - 3, ry + 7, color)


def scene_summit(cv, x, y, w, h):
    """Shenzhen trilateral: dusk skyline, three flags, one table."""
    vgrad(cv, x, y, w, h, (32, 50, 88), (226, 176, 116))
    cv.circle(x + w - 46, y + 22, 11, (240, 214, 150))
    skyline(cv, x, y + h - 12, w, 34, (26, 36, 60), 101,
            windows=(250, 210, 130))
    cv.rect(x, y + h - 12, w, 12, (22, 28, 46))
    # round table
    cv.ellipse(x + w // 2, y + h - 14, 34, 7, (122, 74, 42))
    cv.rect(x + w // 2 - 3, y + h - 10, 6, 8, (90, 54, 30))
    # three flags: US, China, Russia
    poles = [x + 34, x + w // 2, x + w - 34]
    tops = y + h - 52
    for px in poles:
        cv.vline(px, tops, 42, (220, 220, 220))
    # US flag
    fx, fy = poles[0] + 1, tops + 1
    cv.rect(fx, fy, 26, 15, WHITE)
    for i in range(4):
        cv.rect(fx, fy + i * 4, 26, 2, (178, 34, 52))
    cv.rect(fx, fy, 11, 8, (60, 59, 110))
    for dy in (1, 4, 6):
        for dx in (1, 5, 9):
            cv.blend_px(fx + dx, fy + dy, WHITE, 15)
    # China flag
    fx, fy = poles[1] + 1, tops + 1
    cv.rect(fx, fy, 26, 15, (222, 41, 16))
    cv.poly(star_points(fx + 6, fy + 5, 4, 2), (255, 222, 0))
    for sx, sy in ((12, 2), (15, 4), (15, 7), (12, 9)):
        cv.poly(star_points(fx + sx, fy + sy, 2, 1), (255, 222, 0))
    # Russia flag
    fx, fy = poles[2] - 27, tops + 1
    cv.rect(fx, fy, 26, 5, WHITE)
    cv.rect(fx, fy + 5, 26, 5, (0, 57, 166))
    cv.rect(fx, fy + 10, 26, 5, (213, 0, 50))


def scene_gta(cv, x, y, w, h):
    """GTA VI: finished Vice City sunset — striped sun, two skylines,
    neon windows, palms, ocean reflection. No text."""
    top, bottom = (88, 36, 110), (255, 140, 80)
    vgrad(cv, x, y, w, h, top, bottom)
    # stars in the upper sky
    r = random.Random(202)
    for _ in range(16):
        sx = x + r.randint(4, w - 4)
        sy = y + r.randint(2, 18)
        cv.blend_px(sx, sy, (255, 240, 220), 12)
    # striped synthwave sun, centre-right; slits match the sky gradient
    sx, sy, sr = x + w - 98, y + 34, 17
    cv.circle(sx, sy, sr, (255, 222, 150))
    for dy in (3, 8, 13):
        t = (sy + dy - y) / float(h - 1)
        slit = tuple(int(top[k] + (bottom[k] - top[k]) * t)
                     for k in range(3))
        cv.rect(sx - sr - 1, sy + dy, 2 * sr + 2, 2, slit)
    # far skyline (lighter), near skyline (dark) with neon windows
    skyline(cv, x, y + h - 16, w, 20, (120, 66, 140), 203)
    skyline(cv, x, y + h - 12, w, 26, (30, 14, 44), 204,
            windows=(255, 120, 200))
    # ocean strip with the sun's reflection
    cv.rect(x, y + h - 12, w, 12, (38, 18, 66))
    for i, rw in enumerate((26, 20, 14, 10)):
        cv.rect(sx - rw // 2, y + h - 10 + i * 3, rw, 1, (255, 170, 110))
    # birds
    for bx, by in ((x + 60, y + 12), (x + 84, y + 18), (x + 110, y + 10)):
        cv.line(bx - 4, by, bx, by - 3, (40, 16, 50))
        cv.line(bx, by - 3, bx + 4, by, (40, 16, 50))
    # palms: a cluster left, one right
    for px, lean in ((x + 22, -6), (x + 34, 4), (x + w - 26, 6)):
        top_y = y + 18
        cv.line(px, y + h - 12, px + lean, top_y, (22, 8, 30))
        cv.line(px + 1, y + h - 12, px + lean + 1, top_y, (22, 8, 30))
        for ang in (-160, -125, -90, -55, -20, 8):
            a = math.radians(ang)
            ex = int(px + lean + 17 * math.cos(a))
            ey = int(top_y + 9 * math.sin(a) + 8)
            mx = int(px + lean + 9 * math.cos(a))
            my = int(top_y + 4 * math.sin(a) + 2)
            cv.line(px + lean, top_y, mx, my, (22, 8, 30))
            cv.line(mx, my, ex, ey, (22, 8, 30))


def scene_ipo(cv, x, y, w, h):
    """Anthropic IPO: opening bell and a steeply rising chart."""
    cv.rect(x, y, w, h, (16, 30, 54))
    for gy in range(1, 4):
        cv.hline(x + 6, y + gy * h // 4, w - 12, (40, 58, 88))
    # rising chart with filled area
    pts = [(0.0, 0.78), (0.15, 0.72), (0.30, 0.74), (0.45, 0.58),
           (0.60, 0.60), (0.75, 0.38), (0.88, 0.32), (1.0, 0.14)]
    xy = [(x + int(px * (w - 12)) + 6, y + int(py * (h - 10)) + 5)
          for px, py in pts]
    area = xy + [(xy[-1][0], y + h - 4), (xy[0][0], y + h - 4)]
    cv.poly(area, (26, 88, 58))
    for i in range(len(xy) - 1):
        cv.line(xy[i][0], xy[i][1], xy[i + 1][0], xy[i + 1][1],
                (86, 220, 140))
        cv.line(xy[i][0], xy[i][1] + 1, xy[i + 1][0], xy[i + 1][1] + 1,
                (86, 220, 140))
    # arrow head
    ax, ay = xy[-1]
    cv.poly([(ax, ay - 6), (ax + 7, ay + 1), (ax, ay + 1)], (86, 220, 140))
    # bell, top-left
    bx, by = x + 16, y + 12
    cv.circle(bx, by + 6, 9, (232, 184, 64))
    cv.rect(bx - 9, by + 6, 18, 5, (232, 184, 64))
    cv.rect(bx - 11, by + 11, 22, 3, (198, 150, 44))
    cv.circle(bx, by + 16, 2, (198, 150, 44))
    draw_text(cv, x + 34, y + 20, "ANT $83", "sansb12", WHITE)


def scene_court(cv, x, y, w, h):
    """ICJ: courthouse columns and pediment."""
    vgrad(cv, x, y, w, h, (196, 214, 228), (228, 232, 236))
    cv.rect(x, y + h - 6, w, 6, (150, 158, 140))
    bx, by, bw = x + w // 2 - 52, y + h - 10, 104
    cv.rect(bx - 4, by, bw + 8, 4, (120, 112, 96))
    cv.poly([(bx - 8, by - 12), (bx + bw + 8, by - 12),
             (bx + bw // 2, by - 24)], (96, 104, 116))
    cv.rect(bx - 8, by - 12, bw + 16, 3, (96, 104, 116))
    for i in range(5):
        cx = bx + 6 + i * 20
        cv.rect(cx, by - 11, 7, 11, (238, 234, 222))
    cv.rect(bx + bw // 2 - 6, by - 11, 12, 11, (70, 78, 88))


def scene_apec(cv, x, y, w, h):
    """APEC: 21 economies in a glowing ring over the Shenzhen skyline."""
    vgrad(cv, x, y, w, h, (16, 36, 70), (30, 62, 108))
    # stars
    r = random.Random(303)
    for _ in range(10):
        sx = x + r.randint(4, w - 4)
        sy = y + r.randint(2, 12)
        cv.blend_px(sx, sy, (255, 240, 200), 12)
    # skyline with lit windows + one tall spire tower
    skyline(cv, x, y + h - 2, w, 12, (10, 22, 44), 304,
            windows=(255, 214, 90))
    tx = x + w // 2 + 52
    cv.rect(tx, y + h - 26, 7, 24, (10, 22, 44))
    cv.line(tx + 3, y + h - 26, tx + 3, y + h - 32, (255, 214, 90))
    # faint dotted ellipse, then the glowing ring of 21 dots
    cx, cy = x + w // 2, y + h // 2
    for i in range(72):
        a = 2 * math.pi * i / 72 - math.pi / 2
        px = int(cx + 58 * math.cos(a) * 1.35)
        py = int(cy + 15 * math.sin(a))
        cv.blend_px(px, py, (255, 214, 90), 6)
    for i in range(21):
        a = 2 * math.pi * i / 21 - math.pi / 2
        px = int(cx + 58 * math.cos(a) * 1.35)
        py = int(cy + 15 * math.sin(a))
        cv.circle(px, py, 3, (200, 160, 60))
        cv.circle(px, py, 2, (255, 214, 90))
    # central star with rays
    cv.line(cx - 14, cy, cx + 14, cy, (255, 214, 90))
    cv.line(cx, cy - 8, cx, cy + 8, (255, 214, 90))
    cv.poly(star_points(cx, cy, 9, 4), (255, 214, 90))


def scene_bridge(cv, x, y, w, h):
    """Kerch Bridge: broken span, smoke over the strait."""
    vgrad(cv, x, y, w, h, (200, 212, 222), (226, 230, 232))
    cv.rect(x, y + h - 12, w, 12, (70, 120, 160))
    for wx in range(x + 4, x + w - 8, 14):
        cv.hline(wx, y + h - 8, 8, (150, 200, 225))
        cv.hline(wx + 4, y + h - 4, 8, (150, 200, 225))
    deck_y = y + h - 22
    # left span (intact), right span (dropped at the break)
    cv.rect(x + 4, deck_y, 78, 4, (90, 96, 104))
    cv.poly([(x + 110, deck_y), (x + 188, deck_y + 2),
             (x + 188, deck_y + 6), (x + 110, deck_y + 4)], (90, 96, 104))
    for px in (x + 16, x + 52, x + 130, x + 168):
        cv.rect(px, deck_y + 4, 4, 8, (70, 76, 84))
    # smoke + flash at the break (kept inside the scene)
    for sx, sy, sr in ((96, -6, 5), (102, -11, 6), (92, -12, 4)):
        cv.circle(x + sx, deck_y + sy, sr, (120, 120, 118))
    cv.poly(star_points(x + 97, deck_y - 4, 5, 2), (255, 160, 40))


def scene_cyclone(cv, x, y, w, h):
    """Cyclone Akvan: spiral storm over the coast."""
    vgrad(cv, x, y, w, h, (52, 66, 84), (120, 134, 150))
    cx, cy = x + w // 2 - 8, y + h // 2 - 2
    # spiral arms
    for arm in range(3):
        prev = None
        for t in range(0, 101):
            ang = arm * 2.094 + t * 0.11
            r = 3 + t * 0.16
            px = int(cx + r * math.cos(ang))
            py = int(cy + r * 0.62 * math.sin(ang))
            if prev:
                cv.line(prev[0], prev[1], px, py, (232, 236, 240))
            prev = (px, py)
    cv.circle(cx, cy, 3, (232, 236, 240))
    rain(cv, x, y, w, h, (200, 210, 220), 404, 26)
    cv.rect(x, y + h - 7, w, 7, (96, 110, 78))
    for hx in (x + 12, x + 40, x + w - 34):
        cv.rect(hx, y + h - 13, 12, 6, (150, 140, 120))
        cv.poly([(hx - 1, y + h - 13), (hx + 13, y + h - 13),
                 (hx + 6, y + h - 18)], (140, 60, 50))


def scene_bangladesh(cv, x, y, w, h):
    """Bangladesh: street crowd, a building on fire."""
    vgrad(cv, x, y, w, h, (226, 150, 96), (240, 200, 150))
    cv.rect(x, y + h - 10, w, 10, (64, 58, 52))
    # burning building, right (roof at y+14, flame tip y+5: inside scene)
    bx = x + w - 52
    cv.rect(bx, y + 14, 40, h - 24, (120, 100, 84))
    for wy in range(y + 18, y + h - 14, 8):
        for wx in range(bx + 4, bx + 36, 9):
            cv.rect(wx, wy, 5, 5, (60, 52, 44))
    # flame
    cv.poly([(bx + 8, y + 14), (bx + 20, y + 5), (bx + 32, y + 14),
             (bx + 26, y + 10), (bx + 20, y + 15), (bx + 14, y + 10)],
            (255, 140, 30))
    cv.poly([(bx + 14, y + 14), (bx + 20, y + 8), (bx + 26, y + 14)],
            (255, 210, 80))
    for sx, sy, sr in ((bx + 20, y + 3, 3), (bx + 26, y + 1, 2)):
        cv.circle(sx, sy, sr, (110, 110, 108))
    # crowd silhouettes
    r = random.Random(505)
    for _ in range(12):
        fx = x + 8 + r.randint(0, w - 70)
        fh = r.randint(10, 14)
        cv.circle(fx + 2, y + h - 10 - fh + 2, 2, (40, 34, 30))
        cv.rect(fx, y + h - 10 - fh + 4, 5, fh - 4, (40, 34, 30))


def scene_cop(cv, x, y, w, h):
    """COP31: globe with clouds, ticked thermometer, flags of nations."""
    vgrad(cv, x, y, w, h, (186, 220, 236), (232, 240, 244))
    # sun (kept inside the scene)
    cv.circle(x + 18, y + 14, 6, (255, 200, 60))
    for a in range(0, 360, 45):
        ar = math.radians(a)
        cv.line(x + 18 + int(9 * math.cos(ar)), y + 14 + int(9 * math.sin(ar)),
                x + 18 + int(13 * math.cos(ar)), y + 14 + int(13 * math.sin(ar)),
                (255, 200, 60))
    # ground strip + flags of the negotiating nations
    cv.rect(x, y + h - 3, w, 3, (206, 218, 202))
    flag_cols = [(210, 60, 50), (40, 90, 180), (40, 140, 90),
                 (230, 170, 40), (140, 85, 165)]
    for fx, fc in zip((x + 8, x + 28, x + 48, x + 122, x + 142), flag_cols):
        cv.vline(fx, y + h - 13, 10, (90, 90, 90))
        cv.rect(fx + 1, y + h - 13, 9, 6, fc)
    # globe with continents, clouds and a highlight
    gx, gy = x + w // 2 - 14, y + h // 2 + 1
    cv.circle(gx, gy, 15, (66, 130, 190))
    cv.ellipse(gx - 6, gy - 5, 6, 4, (96, 170, 100))
    cv.ellipse(gx + 4, gy + 4, 7, 4, (96, 170, 100))
    cv.ellipse(gx + 2, gy - 8, 4, 3, (96, 170, 100))
    cv.ellipse(gx - 9, gy - 9, 5, 3, (150, 200, 235))
    for cx, cy, cr in ((gx - 3, gy + 6, 3), (gx + 1, gy + 6, 4),
                       (gx + 8, gy - 2, 2), (gx + 11, gy - 2, 3)):
        cv.circle(cx, cy, cr, (240, 248, 252))
    cv.ring(gx, gy, 15, (255, 255, 255))
    # thermometer with scale ticks
    tx = x + w - 26
    cv.rect(tx, y + 6, 8, h - 16, WHITE)
    cv.rect(tx + 1, y + 6, 6, h - 16, (220, 220, 220))
    for ty in (y + 10, y + 17, y + 24):
        cv.hline(tx - 4, ty, 3, (120, 120, 120))
    cv.rect(tx + 2, y + h - 22, 4, 8, (210, 50, 40))
    cv.circle(tx + 4, y + h - 11, 6, (210, 50, 40))
    cv.circle(tx + 4, y + h - 11, 3, (240, 90, 70))


def scene_florida(cv, x, y, w, h):
    """Florida floods: houses in standing water, rain still falling."""
    vgrad(cv, x, y, w, h, (120, 140, 160), (170, 186, 198))
    for cx in (x + 26, x + 70, x + 120):
        cv.circle(cx, y + 11, 10, (96, 112, 130))
        cv.circle(cx + 10, y + 13, 8, (96, 112, 130))
    # houses
    for hx in (x + 16, x + 96):
        cv.rect(hx, y + 18, 44, 16, (232, 222, 200))
        cv.poly([(hx - 3, y + 18), (hx + 47, y + 18), (hx + 22, y + 8)],
                (150, 70, 60))
        cv.rect(hx + 18, y + 26, 8, 8, (90, 80, 66))
    # flood water
    cv.rect(x, y + h - 12, w, 12, (64, 110, 150))
    for wx in range(x + 4, x + w - 8, 16):
        cv.hline(wx, y + h - 8, 9, (170, 210, 230))
        cv.hline(wx + 5, y + h - 4, 9, (170, 210, 230))
    rain(cv, x, y, w, h - 10, (210, 220, 230), 606, 30)


def scene_gulf(cv, x, y, w, h):
    """Gulf all-clear: skyline, derrick, big green check."""
    vgrad(cv, x, y, w, h, (214, 226, 236), (238, 240, 242))
    skyline(cv, x, y + h - 8, w, 20, (70, 84, 104), 707)
    cv.rect(x, y + h - 8, w, 8, (120, 118, 104))
    # derrick
    dx = x + 18
    cv.line(dx, y + h - 8, dx + 8, y + 6, (60, 60, 66))
    cv.line(dx + 16, y + h - 8, dx + 8, y + 6, (60, 60, 66))
    for yy in (y + 14, y + 22, y + 30):
        cv.hline(dx + 3, yy, 10, (60, 60, 66))
    # green check badge
    cx, cy = x + w - 34, y + h // 2
    cv.circle(cx, cy, 13, (34, 150, 84))
    cv.line(cx - 7, cy, cx - 2, cy + 6, WHITE)
    cv.line(cx - 2, cy + 6, cx + 8, cy - 6, WHITE)
    cv.line(cx - 7, cy + 1, cx - 2, cy + 7, WHITE)
    cv.line(cx - 2, cy + 7, cx + 8, cy - 5, WHITE)


def scene_france(cv, x, y, w, h):
    """France: Eiffel Tower silhouette and a ballot box."""
    vgrad(cv, x, y, w, h, (196, 212, 228), (238, 238, 240))
    tx, ty = x + 46, y + h - 8
    # tower legs and tiers
    cv.line(tx - 16, ty, tx, y + 4, (58, 58, 68))
    cv.line(tx + 16, ty, tx, y + 4, (58, 58, 68))
    cv.line(tx - 15, ty, tx, y + 4, (58, 58, 68))
    cv.line(tx + 15, ty, tx, y + 4, (58, 58, 68))
    cv.hline(tx - 12, y + h - 18, 24, (58, 58, 68))
    cv.hline(tx - 8, y + h - 26, 16, (58, 58, 68))
    cv.hline(tx - 4, y + h - 33, 8, (58, 58, 68))
    cv.vline(tx, y + 2, 4, (58, 58, 68))
    # tricolor
    cv.rect(x + w - 58, y + h - 16, 10, 8, (0, 85, 164))
    cv.rect(x + w - 48, y + h - 16, 10, 8, WHITE)
    cv.rect(x + w - 38, y + h - 16, 10, 8, (239, 65, 53))
    # ballot box
    bx = x + w - 34
    cv.rect(bx, y + 12, 22, 16, (240, 236, 224))
    cv.rect(bx - 1, y + 10, 24, 3, (120, 110, 94))
    cv.rect(bx + 8, y + 10, 7, 2, INK)
    cv.rect(bx + 6, y + 4, 10, 7, WHITE)
    cv.rect(bx + 7, y + 5, 8, 5, (210, 60, 50))


def scene_california(cv, x, y, w, h):
    """California: rain over LA — layered ridges, lit skyline, wet street."""
    vgrad(cv, x, y, w, h, (132, 152, 172), (198, 206, 210))
    # distant ridge (pale blue-grey)
    cv.poly([(x, y + h - 14), (x + 40, y + h - 23), (x + 90, y + h - 18),
             (x + 140, y + h - 24), (x + w, y + h - 19), (x + w, y + h - 12),
             (x, y + h - 12)], (158, 168, 178))
    # mid hills (olive-brown), two ridges
    cv.poly([(x, y + h), (x, y + h - 12), (x + 50, y + h - 19),
             (x + 110, y + h - 11), (x + 150, y + h - 7), (x + w, y + h - 3),
             (x + w, y + h)], (150, 128, 90))
    cv.poly([(x, y + h), (x + 80, y + h - 9), (x + 140, y + h - 17),
             (x + w, y + h - 11), (x + w, y + h)], (132, 110, 76))
    skyline(cv, x, y + h - 10, w, 20, (74, 84, 96), 808,
            windows=(255, 220, 150))
    # wet street with reflected light streaks
    cv.rect(x, y + h - 6, w, 6, (96, 84, 66))
    r = random.Random(810)
    for _ in range(8):
        sx = x + r.randint(4, w - 16)
        cv.hline(sx, y + h - 4, r.randint(6, 12), (255, 240, 180))
    rain(cv, x, y, w, h - 4, (220, 228, 236), 909, 26)

# ---------------------------------------------------------------- world map
# Simplified continent outlines in (lon, lat). Hand-drawn from a standard
# equirectangular world map; precise enough to read at a glance.
NORTH_AMERICA = [
    (-168, 66), (-158, 71), (-140, 70), (-128, 70), (-115, 68), (-110, 66),
    (-95, 69), (-85, 68), (-80, 73), (-70, 62), (-65, 60), (-60, 52),
    (-65, 49), (-70, 45), (-75, 39), (-80, 32), (-81, 26), (-84, 30),
    (-90, 29), (-97, 26), (-97, 22), (-94, 17), (-90, 14), (-87, 13),
    (-83, 9), (-79, 9), (-83, 10), (-86, 12), (-90, 14), (-92, 15),
    (-97, 16), (-105, 20), (-110, 23), (-114, 29), (-117, 33), (-122, 37),
    (-124, 42), (-124, 48), (-130, 54), (-135, 57), (-145, 60), (-155, 58),
    (-162, 58),
]
GREENLAND = [
    (-45, 60), (-40, 64), (-32, 68), (-25, 70), (-30, 76), (-45, 80),
    (-58, 76), (-55, 70), (-50, 64),
]
SOUTH_AMERICA = [
    (-79, 9), (-75, 11), (-72, 12), (-64, 10), (-60, 8), (-52, 5),
    (-50, 0), (-44, -3), (-35, -8), (-38, -13), (-40, -20), (-48, -27),
    (-52, -32), (-58, -38), (-62, -41), (-65, -45), (-68, -50), (-68, -54),
    (-70, -53), (-73, -50), (-73, -44), (-72, -37), (-70, -30), (-70, -23),
    (-70, -18), (-75, -15), (-77, -12), (-80, -6), (-81, -3), (-80, 1),
    (-79, 6),
]
AFRICA = [
    (-17, 15), (-16, 16), (-10, 14), (-13, 8), (-8, 5), (-4, 5), (3, 6),
    (6, 4), (9, 4), (9, -1), (12, -6), (13, -10), (12, -15), (14, -22),
    (16, -28), (19, -34), (25, -34), (31, -29), (32, -26), (35, -23),
    (36, -18), (40, -15), (40, -10), (42, -2), (46, 1), (51, 11), (51, 12),
    (45, 11), (43, 12), (40, 15), (38, 18), (36, 21), (34, 27), (32, 31),
    (30, 31), (25, 32), (20, 32), (15, 32), (10, 34), (10, 37), (3, 37),
    (-2, 35), (-5, 36), (-9, 31), (-10, 29), (-15, 27), (-17, 21),
]
EURASIA = [
    (-9, 37), (-9, 43), (-2, 48), (-4, 48), (0, 46), (2, 51), (4, 52),
    (7, 54), (8, 57), (10, 57), (18, 56), (21, 55), (24, 58), (28, 59),
    (30, 60), (28, 66), (32, 69), (40, 68), (45, 68), (55, 69), (60, 69),
    (68, 73), (75, 73), (80, 73), (90, 75), (100, 77), (110, 74),
    (120, 73), (130, 72), (140, 72), (150, 70), (160, 69), (170, 67),
    (178, 65), (178, 62), (170, 60), (162, 60), (158, 52), (156, 51),
    (150, 59), (142, 59), (142, 54), (140, 40), (139, 35), (141, 39),
    (142, 44), (137, 35), (132, 33), (130, 33), (127, 35), (126, 38),
    (122, 38), (120, 32), (121, 28), (118, 25), (110, 21), (109, 18),
    (106, 20), (105, 9), (103, 8), (100, 7), (98, 10), (97, 16), (94, 17),
    (92, 21), (89, 22), (86, 20), (82, 16), (80, 13), (77, 8), (75, 12),
    (73, 16), (72, 20), (70, 21), (68, 23), (66, 25), (62, 25), (58, 26),
    (56, 27), (54, 26), (52, 26), (50, 29), (48, 30), (48, 29), (44, 29),
    (43, 27), (40, 31), (36, 36), (33, 35), (30, 36), (27, 37), (23, 36),
    (20, 40), (19, 42), (16, 40), (16, 38), (15, 40), (12, 44), (10, 44),
    (8, 44), (6, 43), (3, 43), (0, 39), (-2, 37), (-6, 36),
]
BRITAIN = [(-5, 50), (-3, 53), (-5, 56), (-3, 58), (0, 53), (1, 51)]
JAPAN = [
    (130, 31), (132, 33), (135, 34), (138, 35), (140, 36), (141, 40),
    (142, 44), (145, 44), (141, 39), (139, 37), (136, 34), (133, 34),
    (130, 33),
]
SUMATRA = [(95, 5), (98, 2), (101, -1), (104, -4), (106, -6), (104, -3),
           (100, 0), (96, 4)]
BORNEO = [(109, 1), (111, 3), (114, 4), (117, 6), (119, 1), (117, -2),
          (113, -3), (110, -1)]
NEW_GUINEA = [(131, -1), (136, -2), (141, -3), (146, -6), (150, -10),
              (147, -9), (143, -8), (138, -7), (134, -4)]
JAVA = [(105, -6), (108, -7), (111, -7), (114, -8), (112, -8), (108, -8),
        (105, -7)]
AUSTRALIA = [
    (114, -22), (114, -28), (115, -33), (118, -35), (124, -33), (129, -32),
    (132, -32), (135, -35), (138, -35), (140, -38), (145, -38), (147, -38),
    (150, -37), (153, -30), (153, -27), (150, -23), (146, -19), (142, -15),
    (142, -11), (140, -17), (139, -18), (136, -15), (135, -12), (132, -11),
    (130, -13), (126, -14), (122, -17), (118, -20),
]
NEW_ZEALAND = [(167, -45), (170, -42), (173, -39), (175, -38), (178, -38),
               (176, -40), (173, -41), (169, -46)]
MADAGASCAR = [(44, -16), (47, -15), (50, -16), (49, -20), (47, -24),
              (45, -25), (44, -20)]

CONTINENTS = [NORTH_AMERICA, GREENLAND, SOUTH_AMERICA, AFRICA, EURASIA,
              BRITAIN, JAPAN, SUMATRA, BORNEO, NEW_GUINEA, JAVA,
              AUSTRALIA, NEW_ZEALAND, MADAGASCAR]

# Numbered pins for the 13 top stories: (lon, lat, number, dx, dy, region).
# dx/dy shift a pin in pixels (with a leader line to the true spot) when
# two pins would collide at radius 10; region sets the pin color.
PINS = [
    (114.0, 22.5, "1", 0, 0, "ASIA-PACIFIC"),    # Shenzhen trilateral
    (174.8, -36.9, "2", 0, 0, "OCEANIA"),        # GTA VI first (New Zealand)
    (-74.0, 40.7, "3", 0, 0, "AMERICAS"),        # Anthropic lists on Nasdaq
    (4.3, 52.1, "4", 0, 0, "EUROPE"),            # ICJ, The Hague
    (114.0, 22.5, "5", 19, -16, "ASIA-PACIFIC"), # APEC Shenzhen (same city)
    (36.5, 45.3, "6", 0, 0, "EUROPE"),           # Kerch Bridge
    (85.5, 20.0, "7", 0, 0, "ASIA-PACIFIC"),     # Cyclone Akvan, Odisha
    (90.4, 23.8, "8", 15, -12, "ASIA-PACIFIC"),  # Bangladesh, Dhaka
    (30.7, 36.9, "9", -16, 10, "MIDDLE EAST"),   # COP31, Antalya
    (-80.2, 26.1, "10", -12, 9, "AMERICAS"),     # Florida floods
    (46.7, 24.6, "11", 0, 0, "MIDDLE EAST"),     # Riyadh all-clear
    (2.3, 48.9, "12", 24, 14, "EUROPE"),         # Paris / Mélenchon
    (-118.2, 34.0, "13", 0, 0, "AMERICAS"),      # Los Angeles storm
]

LAT_TOP, LAT_BOT = 72.0, -58.0


def _point_in_poly(px, py, poly):
    """Ray-casting point-in-polygon test (for the dotted map)."""
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > py) != (y2 > py):
            xin = x1 + (py - y1) * (x2 - x1) / (y2 - y1)
            if px < xin:
                inside = not inside
    return inside


def draw_map(cv, x, y, w, h):
    cv.rect(x, y, w, h, SEA)

    def px(lon):
        return x + int((lon + 180.0) / 360.0 * w)

    def py(lat):
        return y + int((LAT_TOP - lat) / (LAT_TOP - LAT_BOT) * h)

    # graticule
    for lon in range(-150, 181, 30):
        cv.vline(px(lon), y, h, (216, 230, 238))
    for lat in range(-30, 61, 30):
        cv.hline(x, py(lat), w, (216, 230, 238))
    # land as a dot grid (dotted-continent style)
    polys = [[(px(lon), py(lat)) for lon, lat in poly_lonlat]
             for poly_lonlat in CONTINENTS]
    bboxes = [(min(p[0] for p in poly), min(p[1] for p in poly),
               max(p[0] for p in poly), max(p[1] for p in poly))
              for poly in polys]
    for gy in range(y + 2, y + h - 1, 5):
        for gx in range(x + 2, x + w - 1, 5):
            for poly, (bx0, by0, bx1, by1) in zip(polys, bboxes):
                if bx0 <= gx <= bx1 and by0 <= gy <= by1 and \
                        _point_in_poly(gx, gy, poly):
                    cv.rect(gx, gy, 2, 2, DOT_LAND)
                    break
    # pins: bigger, color-coded by region, white ring + bold number
    for lon, lat, num, dx, dy, region in PINS:
        cx, cy = px(lon), py(lat)
        tx, ty = cx + dx, cy + dy
        if dx or dy:
            cv.line(cx, cy, tx, ty, INK2)
            cv.circle(cx, cy, 2, INK2)
        cv.circle(tx, ty, 10, REGION_COLORS[region])
        cv.ring(tx, ty, 10, WHITE)
        tw = text_width("sansb11", num)
        draw_text(cv, tx - tw // 2, ty + 4, num, "sansb11", WHITE)
    cv.rect(x, y, w, 1, PANEL_LINE)
    cv.rect(x, y + h - 1, w, 1, PANEL_LINE)
    cv.rect(x, y, 1, h, PANEL_LINE)
    cv.rect(x + w - 1, y, 1, h, PANEL_LINE)

# ---------------------------------------------------------------- content
# Every fact below comes from news-brief-20261119.md (verified 19 Nov 2026).
# Decks and briefs are trimmed only to fit their boxes; no fact is added.

HEROES = [
    {
        "num": "1",
        "kicker": "SUMMIT \u00b7 SHENZHEN, CHINA",
        "head": "Three nuclear powers, one table",
        "deck": "First-ever three-way summit at APEC in Shenzhen: Trump, Xi "
                "and Putin agree a 4-point declaration \u2014 UN reform, more "
                "refined-petroleum output, \u2018nuclear war cannot be won\u2019, "
                "ICC condemned. Ukraine left out.",
        "scene": scene_summit,
    },
    {
        "num": "2",
        "kicker": "GAMING \u00b7 WORLDWIDE",
        "head": "GTA VI is here \u2014 after 13 years",
        "deck": "Grand Theft Auto VI goes on sale at midnight local, New "
                "Zealand first \u2014 PS5 and Xbox Series X|S only, no PC; "
                "$79.99 ($99.99 Ultimate). Sensor Tower counts 17.07m "
                "pre-orders worth $1.65bn, 74% on PlayStation.",
        "scene": scene_gta,
    },
    {
        "num": "3",
        "kicker": "MARKETS \u00b7 NASDAQ",
        "head": "Anthropic\u2019s $81bn debut",
        "deck": "Shares start trading as ANT after pricing at $83 \u2014 above "
                "the $70\u201378 range. 980m shares sold for $81.34bn, the "
                "largest IPO on record; a $26bn private placement takes total "
                "proceeds to about $104.8bn.",
        "scene": scene_ipo,
    },
]

GRID = [
    {
        "num": "4",
        "head": "The world court calls it genocide",
        "deck": "ICJ rules 9\u20136: Myanmar committed genocide against the "
                "Rohingya \u2014 the first state ever found responsible.",
        "scene": scene_court,
    },
    {
        "num": "5",
        "head": "APEC adopts the Shenzhen Vision",
        "deck": "All 21 economies agree a roadmap for trade, connectivity, "
                "innovation. Xi pledges $20m.",
        "scene": scene_apec,
    },
    {
        "num": "6",
        "head": "Third strike on the Kerch Bridge",
        "deck": "Overnight drones halt rail traffic \u2014 the third strike in "
                "four days. Tuesday\u2019s blast felled a 227m road span.",
        "scene": scene_bridge,
    },
    {
        "num": "7",
        "head": "80,000 flee Cyclone Akvan",
        "deck": "Odisha evacuates 80,000+ to 7,900 shelters as the storm "
                "nears landfall by Friday noon; winds to 110 km/h.",
        "scene": scene_cyclone,
    },
    {
        "num": "8",
        "head": "Bangladesh shuts down",
        "deck": "Awami League lockdown paralyses the country; Yunus\u2019s home "
                "torched; 4 killed; violence in 28 districts.",
        "scene": scene_bangladesh,
    },
    {
        "num": "9",
        "head": "COP31 enters its final 48 hours",
        "deck": "Ministers take over the draft in Antalya; phase-out wording "
                "and a $120bn-a-year adaptation fund still undecided.",
        "scene": scene_cop,
    },
    {
        "num": "10",
        "head": "Flooded Florida braces for more",
        "deck": "After 15 inches of rain in Broward, schools stay shut "
                "Thursday and a flood watch runs to 7pm.",
        "scene": scene_florida,
    },
    {
        "num": "11",
        "head": "Riyadh gives the all-clear",
        "deck": "Saudi civil defence says the danger has passed as Gulf "
                "leaders press to stop a wider war.",
        "scene": scene_gulf,
    },
    {
        "num": "12",
        "head": "Greens open the door to M\u00e9lenchon",
        "deck": "Green leader Marine Tondelier says she could back hard-left "
                "leader M\u00e9lenchon for president; she polls 2\u20134%.",
        "scene": scene_france,
    },
    {
        "num": "13",
        "head": "Storm moves south to Los Angeles",
        "deck": "A day after record rain in San Francisco, the storm reaches "
                "Southern California; evacuation warnings for burn scars.",
        "scene": scene_california,
    },
]

# (kicker, text) -- 34 primary briefs (v2 dropped the 2 alternates
# so the briefs could grow to 11pt; 47 stories total).
BRIEFS = [
    ("GAZA", "Israel ends its tank raid beyond the Yellow Line; the "
             "Salah al-Din road reopens, but residents say it is still unsafe."),
    ("WEST BANK", "Israel delays E1 settlement contracts after UK sanctions; "
                  "a settler kills a 72-year-old woman near Jericho."),
    ("UKRAINE AID", "The EU proposes a \u20ac45bn loan for Ukraine as allies "
                    "meet in Berlin with US envoys Kushner and Witkoff."),
    ("IRAN CLAIM", "Trump says Iran is exploring chemical and biological "
                   "weapons."),
    ("HORN OF AFRICA", "Ethiopia and Eritrea mass forces along their border."),
    ("NORTH KOREA", "The IAEA says Pyongyang expanded uranium enrichment at "
                    "Yongbyon."),
    ("MIGRATION", "Five EU nations plan to send asylum seekers to Rwanda."),
    ("SUDAN", "Aid reaches the displaced at El Dabba as drone strikes drive "
              "the civilian toll."),
    ("TURKEY", "A court sentences Istanbul mayor Imamoglu and eight others "
               "in the CHP case."),
    ("ARGENTINA", "Milei\u2019s bill to suspend primaries advances."),
    ("BRAZIL", "An isolated Awa group makes rare contact in the Amazon."),
    ("HAITI", "A pastor is convicted over the 2021 assassination of "
              "President Mo\u00efse."),
    ("PACIFIC", "Fiji and Tonga declare emergencies over El Ni\u00f1o drought."),
    ("CHILDREN", "589m children \u2014 nearly 1 in 4 \u2014 live in conflict "
                 "zones, Save the Children says."),
    ("MARKETS", "Stocks fall as AI worries weigh; Fed minutes flag AI "
                "investment risks; Hang Seng \u22122.8%, FTSE \u22121.3%; "
                "Brent about $89."),
    ("EU\u2013INDONESIA", "EU governments approve a trade deal with Indonesia."),
    ("AUSTRALIA", "Unemployment rises to 4.9%."),
    ("EGYPT", "The IMF reaches a deal to unlock $2.3bn."),
    ("HEALTH", "The US loses measles elimination status."),
    ("EBOLA", "Strain-specific Ebola vaccines to be trialled in DR Congo."),
    ("SPACE", "Webb confirms a rare merger of a supergiant and a sun-like "
              "star."),
    ("SPACE", "NASA sets coverage for the Northrop Grumman CRS-25 launch."),
    ("TECH", "Waymo pulls driverless taxis from US freeways."),
    ("OCEANS", "Antarctic blue whales up tenfold since whaling ended."),
    ("RELIGION", "Pew projects Islam to pass Christianity by 2035."),
    ("EDUCATION", "South Korea\u2019s college exam held under AI cheating curbs."),
    ("WORDS", "Cambridge Dictionary names \u2018human\u2019 its word of the year."),
    ("BOOKS", "Hannah Lillith Assadi wins the National Book Award for "
              "Paradiso 17."),
    ("MUSIC", "Ella Langley sweeps the top CMA Awards."),
    ("FOOD", "A two-year-old Comt\u00e9 is named the world\u2019s best cheese."),
    ("FOOTBALL", "Pochettino starts as Manchester United manager."),
    ("FOOTBALL", "Chelsea beat PSG 1-0 in the Women\u2019s Champions League."),
    ("TENNIS", "Alcaraz beats Medvedev at the ATP Finals in Turin."),
    ("DARTS", "Wattimena beats Littler at the Grand Slam of Darts."),
]

NUMBERS = [
    ("17.07m", "GTA VI PRE-ORDERS"),
    ("$81.34bn", "ANTHROPIC IPO \u2014 LARGEST EVER"),
    ("80,000+", "EVACUATED AHEAD OF CYCLONE AKVAN"),
    ("9\u20136", "ICJ: MYANMAR GUILTY OF GENOCIDE"),
    ("589m", "CHILDREN IN CONFLICT ZONES"),
]

# Region counts for the map header, drawn color-coded to match the pins
# (regions with no top-13 pin stay in INK2). Sums to 47 stories.
REGION_SEGMENTS = [
    ("EUROPE 11", "EUROPE"),
    ("AMERICAS 11", "AMERICAS"),
    ("ASIA-PACIFIC 7", "ASIA-PACIFIC"),
    ("MIDDLE EAST 4", "MIDDLE EAST"),
    ("AFRICA 4", "AFRICA"),
    ("OCEANIA 2", "OCEANIA"),
    ("WORLD 8", "WORLD"),
]

DATE_CHECK = (
    "Voyager 1 one light-day \u2014 Wed 18 Nov \u00b7 Van Gogh\u2019s "
    "$190.4m sale \u2014 Wed 18 Nov \u00b7 Hegseth\u2019s US\u2013Venezuela deal "
    "\u2014 Wed 18 Nov \u00b7 Peltola\u2019s Alaska win \u2014 Tue 17 Nov \u00b7 "
    "Duckett\u2019s 133, England\u2019s ODI win \u2014 Wed 18 Nov \u00b7 Libya "
    "seizure in Derna \u2014 Tue 17 Nov \u00b7 Nature\u2019s Great Unconformity "
    "study \u2014 4 Nov \u00b7 Gulf strikes (17\u201318 Nov) & Florida rain "
    "(Tue\u2013Wed): Thursday angles only."
)

SOURCES = ("Sources: Reuters \u00b7 AP \u00b7 BBC \u00b7 USA Today \u00b7 "
           "The Times \u00b7 NPR \u00b7 Al Jazeera \u00b7 IMD \u00b7 NWS \u00b7 "
           "Xinhua \u00b7 Amnesty \u00b7 One Turn")

# ---------------------------------------------------------------- layout
M = 16            # side margin
CW = 992          # content width
WARNINGS = []


def badge(cv, x, y, num, size=15):
    cv.rect(x, y, size, size, RED)
    tw = text_width("sansb9", num)
    draw_text(cv, x + (size - tw) // 2, y + size - 4, num, "sansb9", WHITE)


def render():
    cv = Canvas(W, H, PAPER)

    # ---- masthead
    cv.rect(M, 8, CW, 2, INK)
    cv.rect(M, 11, CW, 1, INK)
    draw_text_centered(cv, W // 2, 50, "ONE DAY ON EARTH", "serifb44", INK)
    draw_text_centered(cv, W // 2, 74, "THURSDAY, 19 NOVEMBER 2026",
                       "sansb15", INK)
    draw_text_centered(
        cv, W // 2, 88,
        "47 STORIES \u00b7 SEVEN REGIONS \u00b7 EVERY EVENT HAPPENED OR WAS "
        "FIRST REPORTED ON THIS DAY", "sans10", INK2)
    cv.rect(M, 92, CW, 1, INK)

    # ---- number strip
    bounds = [M + round(i * CW / 5.0) for i in range(6)]
    for i, (num, label) in enumerate(NUMBERS):
        cx = (bounds[i] + bounds[i + 1]) // 2
        draw_text_centered(cv, cx, 120, num, "sansb24", NAVY)
        draw_text_centered(cv, cx, 131, label, "sans9", INK2)
        if i:
            cv.vline(bounds[i], 100, 28, RULE)
    cv.rect(M, 136, CW, 1, RULE)

    # ---- heroes
    hero_x = [M, M + 330, M + 660]
    for hx, hero in zip(hero_x, HEROES):
        cv.set_clip(hx, 140, 322, 74)
        hero["scene"](cv, hx, 140, 322, 74)
        cv.clear_clip()
        badge(cv, hx, 140, hero["num"], 15)
        draw_text(cv, hx, 226, hero["kicker"], "sansb10", RED)
        if text_width("sansb20", hero["head"]) > 322:
            WARNINGS.append("hero headline too wide: %r" % hero["head"])
        draw_text(cv, hx, 252, hero["head"], "sansb20", INK)
        lines = para_lines([(hero["deck"], "sans12", INK)], 322)
        if len(lines) > 4:
            WARNINGS.append("hero deck overflows (%d lines): %s"
                            % (len(lines), hero["num"]))
        draw_para(cv, hx, 259, 322, [(hero["deck"], "sans12", INK)], 15)
    for div_x in (M + 326, M + 656):
        cv.vline(div_x, 140, 177, PANEL_LINE)

    # ---- grid (5 x 2)
    for row_i in range(2):
        ry = 324 + row_i * 112
        for col_i in range(5):
            card = GRID[row_i * 5 + col_i]
            gx = M + col_i * 200
            cv.set_clip(gx, ry, 192, 38)
            card["scene"](cv, gx, ry, 192, 38)
            cv.clear_clip()
            badge(cv, gx, ry, card["num"], 13)
            hlines = para_lines([(card["head"], "sansb15", INK)], 192)
            if len(hlines) > 2:
                WARNINGS.append("grid headline overflows: %r"
                                % card["head"])
            draw_para(cv, gx, ry + 42, 192,
                      [(card["head"], "sansb15", INK)], 16)
            dlines = para_lines([(card["deck"], "sans10", INK)], 192)
            if len(dlines) > 3:
                WARNINGS.append("grid deck overflows (%d lines): %s"
                                % (len(dlines), card["num"]))
            draw_para(cv, gx, ry + 76, 192,
                      [(card["deck"], "sans10", INK)], 11)
            if col_i:
                cv.vline(gx - 4, ry, 104, PANEL_LINE)
        if row_i == 0:
            cv.hline(M, ry + 111, CW, PANEL_LINE)

    # ---- map
    draw_text(cv, M, 560, "WHERE IT HAPPENED \u2014 THE TOP 13 STORIES",
              "sansb11", INK)
    sep = " \u00b7 "
    seg_w = [text_width("sans10", t) for t, _r in REGION_SEGMENTS]
    total = sum(seg_w) + text_width("sans10", sep) * (len(seg_w) - 1)
    rx = W - M - total
    for i, ((text, region), sw) in enumerate(zip(REGION_SEGMENTS, seg_w)):
        draw_text(cv, rx, 560, text, "sans10",
                  REGION_COLORS.get(region, INK2))
        rx += sw
        if i < len(REGION_SEGMENTS) - 1:
            draw_text(cv, rx, 560, sep, "sans10", INK2)
            rx += text_width("sans10", sep)
    draw_map(cv, M, 564, CW, 92)

    # ---- briefs (3 balanced columns)
    draw_text(cv, M, 676, "IN BRIEF \u2014 34 MORE STORIES", "sansb13", INK)
    draw_text_right(cv, W - M, 676,
                    "EVERY ITEM VERIFIED FOR 19 NOVEMBER 2026",
                    "sansb10", RED)
    cv.rect(M, 680, CW, 1, INK)
    col_x = [M, M + 333, M + 666]
    col_w = 326
    brief_heights = []
    for kicker, text in BRIEFS:
        segs = [(kicker + " \u2014 ", "sansb11", INK),
                (text, "sans11", INK)]
        nlines = len(para_lines(segs, col_w))
        brief_heights.append((segs, nlines * 13 + 1))
    columns = [[], [], []]
    col_h = [0, 0, 0]
    for segs, bh in brief_heights:
        j = col_h.index(min(col_h))
        columns[j].append((segs, bh))
        col_h[j] += bh
    for j, col in enumerate(columns):
        y = 686
        for segs, bh in col:
            draw_para(cv, col_x[j], y, col_w, segs, 13)
            y += bh
    if max(col_h) > 276:
        WARNINGS.append("brief columns overflow: max height %d (budget 276)"
                        % max(col_h))
    WARNINGS.append("brief column heights: %s" % (col_h,))

    # ---- date check + sources
    cv.rect(M, 968, CW, 1, INK)
    draw_text(cv, M, 982,
              "DATE CHECK \u2014 DELIBERATELY LEFT OUT: NOT 19 NOVEMBER",
              "sansb11", RED)
    dlines = para_lines([(DATE_CHECK, "sans10", INK2)], CW)
    if len(dlines) > 2:
        WARNINGS.append("date-check overflows (%d lines)" % len(dlines))
    draw_para(cv, M, 986, CW, [(DATE_CHECK, "sans10", INK2)], 12)
    draw_text_centered(cv, W // 2, 1019, SOURCES, "sans9", INK2)

    return cv


# ---------------------------------------------------------------- PNG output
def _chunk(typ, data):
    return (struct.pack(">I", len(data)) + typ + data +
            struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF))


def _paeth_row(prev, cur):
    out = bytearray(len(cur))
    for i in range(len(cur)):
        a = cur[i - 3] if i >= 3 else 0
        b = prev[i]
        c = prev[i - 3] if i >= 3 else 0
        p = a + b - c
        pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
        pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
        out[i] = (cur[i] - pr) & 0xFF
    return bytes(out)


def encode_png(w, h, buf):
    stride = w * 3
    raw0 = bytearray()
    for yy in range(h):
        raw0.append(0)
        raw0 += buf[yy * stride:(yy + 1) * stride]
    comp0 = zlib.compress(bytes(raw0), 9)
    rawp = bytearray()
    prev = bytes(stride)
    for yy in range(h):
        cur = bytes(buf[yy * stride:(yy + 1) * stride])
        rawp.append(4)
        rawp += _paeth_row(prev, cur)
        prev = cur
    compp = zlib.compress(bytes(rawp), 9)
    comp = comp0 if len(comp0) <= len(compp) else compp
    png = b"\x89PNG\r\n\x1a\n"
    png += _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += _chunk(b"IDAT", comp)
    png += _chunk(b"IEND", b"")
    return png


def main():
    global FONTS
    here = os.path.dirname(os.path.abspath(__file__))
    FONTS = load_fonts(os.path.join(here, "font.bin"))
    cv = render()
    png = encode_png(W, H, cv.buf)
    outdir = os.environ.get("MAKEFUN_OUTPUT_DIR", "/output")
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "entry.png"), "wb") as fh:
        fh.write(png)
    import sys
    n_stories = len(HEROES) + len(GRID) + len(BRIEFS)
    print("stories: %d (heroes %d, grid %d, briefs %d)"
          % (n_stories, len(HEROES), len(GRID), len(BRIEFS)),
          file=sys.stderr)
    print("png bytes: %d" % len(png), file=sys.stderr)
    for wmsg in WARNINGS:
        print("WARN: %s" % wmsg, file=sys.stderr)


if __name__ == "__main__":
    main()
