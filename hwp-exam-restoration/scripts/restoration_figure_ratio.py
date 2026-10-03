"""Width:height of a figure's main shape, source crop against render (no model calls, PIL only).

The producer's source_bbox_px is generous: question text, choices and pencil marks sit inside it, so the
ink box of the whole crop says little. The main shape is the largest connected group of dark pixels
(the triangle, the circle with its chords); lines of text and loose labels are separate groups.

The measure is skipped where it cannot be trusted:
- the source shape runs into the edge of the crop (a pencil loop, or the box cut the figure),
- the source shape is mostly filled (a photo or a table, not a line drawing),
- the render is made of several parts with none clearly the main one (choice panels).
On past scans this left about four figures in ten measured.
"""
import math

SIDE = 240            # both images are read at this long side, so one pixel is about 0.2mm of a figure
SOURCE_DARK = 140     # printed strokes in a scan; most pencil is lighter
RENDER_DARK = 200
JOIN = 1              # pixels grown before grouping, so dashes and scan gaps stay one shape
LIMIT = 1.25          # width:height may differ by this factor (past figures that passed: median 1.12)
EDGE_SHARE = 0.012
PHOTO_FILL = 0.38     # line drawings fill up to 0.33 of their box at this size; photos and tables 0.43 and more
MAIN_SHARE = 0.5      # the render's main shape must cover this share of all its ink
MIN_PIXELS = 6


def components(im, dark, side=SIDE, join=JOIN):
    """Connected dark shapes as (x0, y0, x1, y1, pixels), in pixels of the image shrunk to `side`; and that size."""
    from PIL import Image, ImageFilter
    g = im.convert('L').point(lambda v: 255 if v < dark else 0)
    k = side / max(g.size); w, h = max(1, round(g.width * k)), max(1, round(g.height * k))
    g = g.resize((w, h), Image.BOX).point(lambda v: 255 if v >= 30 else 0)
    if join: g = g.filter(ImageFilter.MaxFilter(2 * join + 1))
    px = g.tobytes(); parent = []; boxes = []; above = []

    def find(i):
        while parent[i] != i: parent[i] = parent[parent[i]]; i = parent[i]
        return i
    for y in range(h):
        row = px[y * w:(y + 1) * w]; runs = []; x = row.find(b'\xff')
        while x >= 0:
            e = x
            while e < w and row[e]: e += 1
            n = len(parent); parent.append(n); boxes.append([x, y, e, y + 1, e - x])
            for a, b, m in above:  # runs of the row above that touch this one, corners included
                if a <= e and b >= x:
                    ra, rb = find(m), find(n)
                    if ra != rb:
                        parent[rb] = ra; p, q = boxes[ra], boxes[rb]
                        boxes[ra] = [min(p[0], q[0]), min(p[1], q[1]), max(p[2], q[2]), max(p[3], q[3]), p[4] + q[4]]
            runs.append((x, e, n)); x = row.find(b'\xff', e)
        above = runs
    return [tuple(b) for i, b in enumerate(boxes) if parent[i] == i], (w, h)


def main_shape(im, dark):
    """The largest connected shape: its width:height in the image's own pixels, and what makes it hard to trust."""
    found, (w, h) = components(im, dark)
    found = [c for c in found if c[4] >= MIN_PIXELS]
    if not found: return None
    area = lambda c: (c[2] - c[0]) * (c[3] - c[1])
    m = max(found, key=area)
    ink = (max(c[2] for c in found) - min(c[0] for c in found)) * (max(c[3] for c in found) - min(c[1] for c in found))
    kx, ky = im.width / w, im.height / h
    return {'ratio': (m[2] - m[0]) * kx / ((m[3] - m[1]) * ky), 'share': area(m) / max(1, ink), 'fill': m[4] / max(1, area(m)),
            'edge': min(m[0] / w, m[1] / h, 1 - m[2] / w, 1 - m[3] / h) <= EDGE_SHARE}


def ratio_gap(source, render):
    """Factor between the two width:height ratios (source over render), or None where it cannot be read."""
    src, ren = main_shape(source, SOURCE_DARK), main_shape(render, RENDER_DARK)
    if not src or not ren or ren['share'] < MAIN_SHARE or src['edge'] or src['fill'] > PHOTO_FILL: return None
    return src['ratio'] / ren['ratio'], src['ratio'], ren['ratio']


def _said(ratio): return f'{ratio:.2f}:1' if ratio >= 1 else f'1:{1 / ratio:.2f}'


def ratio_warning(source_crop, render_png):
    """One line when the render's main shape is clearly wider or taller than the source's; else None."""
    try:
        from PIL import Image
        with Image.open(source_crop) as source, Image.open(render_png) as render:
            if render.mode in ('RGBA', 'LA'):  # transparent renders count as white paper
                paper = Image.new('RGBA', render.size, 'white'); paper.alpha_composite(render.convert('RGBA')); render = paper
            found = ratio_gap(source, render)
    except Exception: return None  # a measuring aid; the render itself already succeeded
    if not found or abs(math.log(found[0])) <= math.log(LIMIT): return None
    return f'비율: 원본 그림은 가로:세로 {_said(found[1])}, 렌더는 {_said(found[2])}. 글의 수치에 맞춘 것이 아니면 원본 모양을 따른다.'
