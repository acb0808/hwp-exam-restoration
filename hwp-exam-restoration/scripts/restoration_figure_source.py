"""What the source page shows where a render draws a stroke (numpy, PIL and the PDF reader; no model calls).

A producer that reads a solid radius with a dotted length arc beside it as "a dashed radius" keeps that
reading when asked: the same figure came out with a dashed radius in 8 of 11 runs, and the one producer that
was told "a length sits on a straight dashed line" answered that the source has a dashed line too. So the
engine reads the page itself. Nothing is searched for: each reading is taken where the render says a stroke is.

1. The render's strokes (restoration_figure_geometry.extract, in mm) are put on the page at the place
   restoration_figure_locate found, then moved and stretched a little (each axis on its own) until the solid
   strokes lie on source ink.
2. Each stroke may slide a few pixels and turn a few degrees on its own, and the page is read along it:
   one row of ink / no ink.
3. Ink under a solid stroke is taken out first. A dashed line that runs beside a solid one (the hidden edge
   of a cube behind a face diagonal) was otherwise read off its neighbour.
4. A dashed straight line whose row is one unbroken run of ink is solid in the source.

On 414 delivered figures of five exams this said "solid" 15 times and was right 14 times (the miss: a pencil
or fold line 3 px beside a dashed guide of a graph). It does not find every such line: of six renders of
another figure with the same slip, three were said. Without numpy nothing is said.
"""
import math

MM = 25.4 / 72
MIN_MM = 5.0                  # shorter strokes are marks
SHIFT = 14                    # page pixels the whole figure may move, each way
SCALES = (0.90, 0.93, 0.96, 0.98, 1.0, 1.02, 1.04, 1.07, 1.10)   # and stretch, each axis
SETTLE_STEP = 6               # every sixth pixel of a stroke is enough to place the figure (every second took 3 s)
OWN_SHIFT = 5                 # page pixels one stroke may slide
OWN_TURN = (-3, -1.5, 0, 1.5, 3)   # degrees a straight stroke may turn about its middle
CLAIM = 3                     # page pixels of ink around a solid stroke that belong to it
SOLID_RUN = 0.75              # solid in the source: one unbroken run of ink over this share of the line
SOLID_COVER = 0.9             # ... and ink under this share of it
BESIDE = (-10, -7, 7, 10)     # rows read beside the line, in pixels
SHADED = 0.25                 # ink share beside the line above which it lies in stipple or text and is not judged
DARK_OVER = 10                # ink is darker than the crop's own threshold plus this (thin scanned lines are pale)


def _threshold(np, gray):
    """Otsu's threshold of a grey crop."""
    hist = np.bincount(gray.ravel(), minlength=256).astype(np.float64); total = hist.sum()
    w = np.cumsum(hist); m = np.cumsum(hist * np.arange(256)); mt = m[-1]
    with np.errstate(divide='ignore', invalid='ignore'):
        var = (mt * w / total - m) ** 2 / (w * (total - w))
    return int(np.nanargmax(var[1:-1])) + 1


def _resample(np, pts, step=1.0):
    seg = np.hypot(*np.diff(pts, axis=0).T); total = seg.sum()
    if total < 1e-6: return pts[:1]
    d = np.concatenate([[0], np.cumsum(seg)]); t = np.arange(0, total + 1e-6, step)
    return np.stack([np.interp(t, d, pts[:, 0]), np.interp(t, d, pts[:, 1])], axis=1)


def _normals(np, pts):
    d = np.gradient(pts, axis=0); n = np.hypot(d[:, 0], d[:, 1]); n[n == 0] = 1
    return np.stack([-d[:, 1] / n, d[:, 0] / n], axis=1)


def _lookup(np, mask, xy):
    x = np.rint(xy[:, 0]).astype(int); y = np.rint(xy[:, 1]).astype(int)
    ok = (x >= 0) & (y >= 0) & (x < mask.shape[1]) & (y < mask.shape[0])
    out = np.zeros(len(xy), bool); out[ok] = mask[y[ok], x[ok]]
    return out


def _longest(np, row):
    """Longest run of True in a boolean row."""
    if not len(row): return 0
    edges = np.flatnonzero(np.diff(row.astype(np.int8))) + 1
    starts = np.concatenate([[0], edges]); ends = np.concatenate([edges, [len(row)]])
    return max((int(e - s) for s, e in zip(starts, ends) if row[s]), default=0)


def _strokes(np, geo):
    """[{line, dashed, pts (mm), ends}] for the strokes of a figure that are long enough to read."""
    out = []
    for s in geo['segments']:
        if s['symbol'] or math.dist(s['p'], s['q']) < MIN_MM: continue
        out.append({'line': True, 'dashed': s['dashed'], 'pts': np.array([s['p'], s['q']], float), 'ends': (s['p'], s['q'])})
    for c in geo['curves']:
        pts = np.array(c['points'], float)
        if float(np.hypot(*np.diff(pts, axis=0).T).sum()) < MIN_MM: continue
        out.append({'line': False, 'dashed': c['dashed'], 'pts': pts, 'ends': (tuple(pts[0]), tuple(pts[-1]))})
    return out


class _Reading:
    def __init__(self, np, page, render, pdf, found, geo, pad=40):
        from PIL import Image, ImageFilter
        import fitz
        from restoration_figure_locate import _on_white, RENDER_DARK
        self.np = np
        with Image.open(render) as im:
            box = _on_white(im).convert('L').point(lambda v: 255 if v < RENDER_DARK else 0).getbbox(); width = im.width
        with fitz.open(pdf) as doc: sheet_mm = doc[0].rect.width * MM
        k = found[2] / (box[2] - box[0])                           # render pixels -> page pixels
        self.per_mm = k * width / sheet_mm                         # mm -> page pixels
        x0 = max(0, int(found[0] - pad - found[2] * .15)); y0 = max(0, int(found[1] - pad - found[3] * .15))
        x1 = min(page.width, int(found[0] + found[2] * 1.15 + pad)); y1 = min(page.height, int(found[1] + found[3] * 1.15 + pad))
        gray = np.asarray(page.crop((x0, y0, x1, y1)).convert('L'))
        self.ink = gray < min(200, max(120, _threshold(np, gray) + DARK_OVER))
        self.fat = np.asarray(Image.fromarray((self.ink * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(5))) > 0
        origin = np.array([found[0] - box[0] * k - x0, found[1] - box[1] * k - y0])
        self.strokes = _strokes(np, geo)
        for s in self.strokes: s['px'] = _resample(np, s['pts'] * self.per_mm + origin)
        self.centre = np.array([found[0] + found[2] / 2 - x0, found[1] + found[3] / 2 - y0])
        self.shift = (0, 0); self.scale = (1.0, 1.0); self.support = None
        self._settle()

    def _placed(self, px):
        return (px - self.centre) * self.np.array(self.scale) + self.centre + self.np.array(self.shift)

    def _settle(self):
        """Move and stretch the figure so that its solid strokes lie on (thickened) source ink."""
        np = self.np; solid = [s['px'] for s in self.strokes if not s['dashed']]
        if not solid: return
        pts = np.concatenate(solid); few = np.concatenate([p[::SETTLE_STEP] for p in solid]); best = (-1.0, (0, 0), (1.0, 1.0))
        shifts = np.array([(dx, dy) for dx in range(-SHIFT, SHIFT + 1, 2) for dy in range(-SHIFT, SHIFT + 1, 2)], float)
        for sx in SCALES:
            for sy in SCALES:
                base = (few - self.centre) * np.array([sx, sy]) + self.centre
                share = self._shares(self.fat, base, shifts); i = int(share.argmax())
                if share[i] > best[0] + 1e-9: best = (float(share[i]), tuple(int(v) for v in shifts[i]), (sx, sy))
        _, (dx, dy), scale = best
        base = (pts - self.centre) * np.array(scale) + self.centre
        self.support, self.shift = max((float(_lookup(np, self.fat, base + np.array([dx + a, dy + b])).mean()), (dx + a, dy + b)) for a in (-1, 0, 1) for b in (-1, 0, 1))
        self.scale = scale

    def _shares(self, mask, pts, shifts):
        """Share of the points on the mask, for each shift at once."""
        np = self.np; moved = pts[None, :, :] + shifts[:, None, :]
        return _lookup(np, mask, moved.reshape(-1, 2)).reshape(len(shifts), len(pts)).mean(axis=1)

    def _row(self, stroke, ink):
        """The stroke where it meets most ink, and the page read along it there: (row, points)."""
        np = self.np; base = self._placed(stroke['px']); mid = base.mean(axis=0); best = None
        shifts = np.array([(dx, dy) for dx in range(-OWN_SHIFT, OWN_SHIFT + 1) for dy in range(-OWN_SHIFT, OWN_SHIFT + 1)], float)
        cost = 0.004 * np.abs(shifts).sum(axis=1)
        for turn in (OWN_TURN if stroke['line'] else (0,)):
            a = math.radians(turn); R = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
            turned = (base - mid) @ R.T + mid; nrm = _normals(np, turned)
            moved = (turned[None, :, :] + shifts[:, None, :]).reshape(-1, 2); side = np.tile(nrm, (len(shifts), 1))
            rows = (_lookup(np, ink, moved) | _lookup(np, ink, moved + side) | _lookup(np, ink, moved - side)).reshape(len(shifts), len(turned))
            score = rows.mean(axis=1) - cost - 0.003 * abs(turn); i = int(score.argmax())
            if best is None or score[i] > best[0]: best = (float(score[i]), rows[i], turned + shifts[i])
        return best[1], best[2]

    def solid_where_dashed(self):
        np = self.np; claimed = np.zeros_like(self.ink); H, W = claimed.shape
        for s in self.strokes:
            if s['dashed']: continue
            _, q = self._row(s, self.ink)
            for dx in range(-CLAIM, CLAIM + 1):
                for dy in range(-CLAIM, CLAIM + 1):
                    x = np.rint(q[:, 0] + dx).astype(int); y = np.rint(q[:, 1] + dy).astype(int)
                    ok = (x >= 0) & (y >= 0) & (x < W) & (y < H); claimed[y[ok], x[ok]] = True
        free = self.ink & ~claimed; out = []
        for s in self.strokes:
            if not (s['dashed'] and s['line']): continue
            row, q = self._row(s, free)
            if not len(row) or row.mean() < SOLID_COVER or _longest(np, row) < SOLID_RUN * len(row): continue
            nrm = _normals(np, q)
            if float(np.median([_lookup(np, self.ink, q + nrm * k).mean() for k in BESIDE])) > SHADED: continue
            out.append({'ends': s['ends'], 'mm': round(len(row) / self.per_mm, 1)})
        return out


def solid_where_dashed(page, render, pdf, found, geo=None):
    """Dashed straight lines of a render that are solid lines on the source page: [{'ends': (p, q) in mm, 'mm': length}].
    `page` is the page image, `found` the place (x, y, w, h, ...) from restoration_figure_locate.locate.
    None when it cannot be read (no numpy, nothing to read)."""
    try:
        import numpy as np
    except ImportError:
        return None
    if geo is None:
        from restoration_figure_geometry import extract
        geo = extract(pdf)
    if not any(s['dashed'] and not s['symbol'] and math.dist(s['p'], s['q']) >= MIN_MM for s in geo['segments']): return []
    return _Reading(np, page, render, pdf, found, geo).solid_where_dashed()


def source_line_findings(page, render, pdf, found):
    """One sentence for the producer: what was measured and what to draw instead. Every line is named: told of two
    of its three dashed radii, a producer made those two solid and left the third."""
    from restoration_figure_geometry import extract, _near_label, length_line_findings
    geo = extract(pdf); lines = solid_where_dashed(page, render, pdf, found, geo)
    if not lines: return []
    names = []
    for line in lines[:6]:
        a, b = (_near_label(geo['glyphs'], p, 5.0) for p in line['ends'])
        names.append(f'{a}–{b}' if a and b else f"길이 {line['mm']:g}mm인 선")
    advice = ('실선으로 바꾸고, 그 선에 붙인 길이 라벨은 원본처럼 옆의 점선 길이 호(\\ExamLengthArc)에 답니다.' if length_line_findings(geo)
              else '실선으로 바꿉니다.')
    return [f"원본은 실선: 점선으로 그린 {', '.join(names)} 자리를 엔진이 원본에서 재니 {'모두 ' if len(names) > 1 else ''}끊기지 않은 실선입니다(추정이 아니라 측정). {advice}"]
