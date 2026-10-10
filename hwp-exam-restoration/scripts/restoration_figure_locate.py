"""Where a rendered figure sits on the source page (numpy and PIL, no model calls).

A producer sets source_bbox_px by eye on a downscaled page. Of 160 recent figures 55 had less than 90% of
the figure inside that box, and the box is what the compare image, the reviewer's sheet and the note
picture are cut from. The first render is a copy of the source figure, so its ink is slid over the page
around the producer's box, at nine sizes, and the place where the two inks cover each other best is taken.

The score at a place is the harmonic mean of two shares, both read with strokes thickened so that a
thin render line still meets a thick scanned one:
- render ink that lies on source ink,
- source ink under the render's box that lies on render ink (question text beside the figure lowers it).

A found place is not trusted on its score alone: of 57 placements looked at, three cut a side of the
figure that the producer's box had kept, at scores of 0.46 to 0.59. `settled` therefore lets a side that
still cuts ink go back to the producer's side, and the caller keeps the producer's box when nothing was
found. Without numpy nothing is found.
"""
WORK = 220            # the render's ink box is read at this width for the final place and score
COARSE = 88           # and at this width to search the whole window (the full width took 4.5 s a figure)
REFINED = 3           # sizes carried from the search to the final reading
SCALES = (0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.25, 1.4, 1.6)   # sizes tried, times the size that fits the producer's box
RENDER_DARK = 140
SOURCE_DARK = 150
JOIN = 3              # pixels a stroke is thickened each way at WORK width
MIN_SCORE = 0.45      # below this the two failures of the sample sat (0.40, 0.41)
SURE_SCORE = 0.6      # from here a box larger than the producer's is taken too
LARGER = 1.25         # ... larger by this factor in area
MIN_SIDE = 20
MARGIN = 0.08         # paper kept around the found box, as a share of its side


def _on_white(im):
    """Transparent renders count as white paper."""
    from PIL import Image
    if im.mode in ('RGBA', 'LA', 'P'):
        paper = Image.new('RGBA', im.size, 'white'); paper.alpha_composite(im.convert('RGBA')); return paper
    return im


def _fast(n):
    """Next size whose only prime factors are 2, 3 and 5 (the transform is slow on others)."""
    while True:
        m = n
        for p in (2, 3, 5):
            while m % p == 0: m //= p
        if m == 1: return n
        n += 1


def _mask(np, im):
    return (np.frombuffer(im.tobytes(), dtype=np.uint8).reshape(im.height, im.width) > 0).astype(np.float32)


def _thick(np, m, r):
    """Mask with every stroke grown `r` pixels each way (a disc)."""
    out = m.copy(); H, W = m.shape
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if (dx or dy) and dx * dx + dy * dy <= r * r + 1:
                a = out[max(0, dy):H + min(0, dy), max(0, dx):W + min(0, dx)]
                np.maximum(a, m[max(0, -dy):H - max(0, dy), max(0, -dx):W - max(0, dx)], out=a)
    return out


def _scores(np, src, t, join):
    """Score of the render ink `t` at every place of the page ink `src` where it lies fully inside (PIL masks); None when it does not fit."""
    h, w = t.height, t.width; H, W = src.height, src.width
    if H <= h or W <= w: return None
    shape = (_fast(H), _fast(W))

    def slide(big, small):
        """Sum of big*small with small's corner at each place."""
        out = np.fft.irfft2(np.fft.rfft2(big, s=shape) * np.conj(np.fft.rfft2(small, s=shape)), s=shape)
        return out[:H - h + 1, :W - w + 1]
    tm, sm = _mask(np, t), _mask(np, src)
    c1 = slide(_thick(np, sm, join), tm) / (float(tm.sum()) or 1.0)
    sums = np.zeros((H + 1, W + 1), dtype=np.float32); sums[1:, 1:] = sm.cumsum(0).cumsum(1)
    under = sums[h:, w:] - sums[:-h, w:] - sums[h:, :-w] + sums[:-h, :-w]
    c2 = slide(sm, _thick(np, tm, join)) / (under + 1.0)
    c1, c2 = np.maximum(c1, 0), np.maximum(c2, 0)       # the transform leaves sums a hair below zero
    return 2 * c1 * c2 / (c1 + c2 + 1e-6)


def locate(page, box, render):
    """(x, y, width, height, score) of the render's ink box on the page, in page pixels; None when it cannot be read.
    `page` and `render` are PIL images, `box` the producer's (left, top, width, height)."""
    try: import numpy as np
    except ImportError: return None
    from PIL import Image
    left, top, bw, bh = box
    if bw < MIN_SIDE or bh < MIN_SIDE: return None
    ink = _on_white(render).convert('L').point(lambda v: 255 if v < RENDER_DARK else 0)
    found = ink.getbbox()
    if not found: return None
    ink = ink.crop(found); tw, th = ink.size
    x0, y0 = max(0, int(left - bw)), max(0, int(top - bh))
    x1, y1 = min(page.width, int(left + 2 * bw)), min(page.height, int(top + 2 * bh))
    if x1 - x0 < MIN_SIDE or y1 - y0 < MIN_SIDE: return None
    window = page.crop((x0, y0, x1, y1)).convert('L')
    dark = window.point(lambda v: 255 if v < SOURCE_DARK else 0)
    base = min(bw / tw, bh / th)

    def best(s, width, join, region):
        """Best place of the render at size `s` inside `region` of the window, read with the render `width` pixels wide."""
        scale = base * s; f = width / (tw * scale)        # render pixels to page pixels; page pixels to working pixels
        t = ink.resize((width, max(4, round(th * scale * f))), Image.BOX).point(lambda v: 255 if v > 40 else 0)
        size = (max(4, round((region[2] - region[0]) * f)), max(4, round((region[3] - region[1]) * f)))
        if width == WORK: src = window.crop(region).resize(size, Image.BOX).point(lambda v: 255 if v < SOURCE_DARK else 0)
        else: src = dark.crop(region).resize(size, Image.BOX).point(lambda v: 255 if v > 40 else 0)   # thin strokes survive the shrink
        score = _scores(np, src, t, join)
        if score is None: return None
        y, x = np.unravel_index(int(score.argmax()), score.shape)
        return float(score[y, x]), x / f + region[0], y / f + region[1], tw * scale, th * scale, f
    whole = (0, 0, window.width, window.height)
    rough = sorted(filter(None, (best(s, COARSE, 1, whole) for s in SCALES)), reverse=True)[:REFINED]
    fine = []
    for _, x, y, w, h, f in rough:
        pad = 2 / f + 1                                   # the rough place is known to a working pixel or two
        region = (max(0, int(x - pad)), max(0, int(y - pad)), min(window.width, int(x + w + pad) + 1), min(window.height, int(y + h + pad) + 1))
        fine.append(best(w / (tw * base), WORK, JOIN, region))
    fine = [v for v in fine if v]
    if not fine: return None
    score, x, y, w, h, _ = max(fine)
    return x + x0, y + y0, w, h, score


def settled(page, box, grown, found, grow):
    """Pixel box (left, top, right, bottom) to cut from the page for this figure, or None to keep `grown`.

    `grown` is the producer's box after the engine widened it; `found` is what `locate` returned;
    `grow(page, sides, reach)` moves the four sides outward while they cut ink, each no further than `reach`.
    The found box with a margin is the start. A side that lies inside the producer's box and still cuts ink
    goes back as far as the producer's side, so nothing that box showed is cut through; a side that lies
    outside it (the producer's box cut the figure there) grows by 15% at most.
    A box that ends up clearly larger than the producer's is taken only from a sure placement: pencil and
    question text around a misplaced box widen every side."""
    if not found or found[4] < MIN_SCORE: return None
    x, y, w, h = found[:4]
    if x >= box[0] + box[2] or x + w <= box[0] or y >= box[1] + box[3] or y + h <= box[1]: return None  # another figure
    import math
    l, t = max(0, math.floor(x - w * MARGIN)), max(0, math.floor(y - h * MARGIN))
    r, b = min(page.width, math.ceil(x + w * (1 + MARGIN))), min(page.height, math.ceil(y + h * (1 + MARGIN)))
    if r - l < 8 or b - t < 8: return None
    more_x, more_y = round((r - l) * .15), round((b - t) * .15)
    reach = (grown[0] if l >= grown[0] else l - more_x, grown[1] if t >= grown[1] else t - more_y,
             grown[2] if r <= grown[2] else r + more_x, grown[3] if b <= grown[3] else b + more_y)
    l, t, r, b = grow(page, (l, t, r, b), reach)
    if found[4] < SURE_SCORE and (r - l) * (b - t) > LARGER * (grown[2] - grown[0]) * (grown[3] - grown[1]): return None
    return l, t, r, b
