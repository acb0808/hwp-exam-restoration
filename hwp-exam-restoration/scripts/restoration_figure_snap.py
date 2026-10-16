"""Fit a producer's rough crop box to the figure's own ink (job option figures=crop).

Ported from hwp-problem-picker (hwpick/layout.py `analyze`, hwpick/figures.py `snap`; MIT, acb0808). The model says
roughly where the figure is; the engine decides where to cut: the large strokes the box mostly covers (the figure's
frame, a table grid), the short labels next to them, and none of the body or choice lines. Producer boxes are often
off by 10 to 20 % of the figure, so a crop taken as given clips vertex names or brings the next line along.

Exam scans carry pencil (circles, working) that touches the printed lines and joins them into one stroke, so the ink
here is read twice: strictly (pencil mostly drops out) and as the picker does. The strict fit is used unless it lost
most of the box, which happens when light print breaks into pieces.

Needs OpenCV; `fit_box` returns None without it and the caller keeps its own box handling."""
from __future__ import annotations

from dataclasses import dataclass, field

INK_DIFF = 32           # picker: darker than the background by this much is ink (light gray print included)
STRICT_INK_DIFF = 130   # pencil on 2024 grade 8 scans stayed under this; 170 already broke printed strokes
RULE_LEN_EM = 3.5       # a straight run longer than this is a rule, not a letter
SEED_MIN_EM = 1.8       # a figure's frame (one joined stroke) is larger than this both ways
LABEL_REACH_EM = 0.9    # short glyph groups this close to the frame are its labels
LABEL_MAX_EM = 5.0      # widest glyph group that can be a label
ROW_LABEL_MAX_EM = 12.0  # a text row that overlaps the frame up to this width is its label (ax+by+c=0)
AREA_MARGIN_EM = 2.0    # strokes are read this far around the producer's box (the picker reads the question box);
                        # 30 % of the box instead fitted as well on four held-out exams and caught more pencil
STRICT_KEEPS = 0.3      # strict fit kept while at least this share of the picker-ink fit: pencil inflated that fit
                        # to 1.5-2x on grade 8 scans, broken print shrank the strict one to 0.1-0.2 of it


def _cv():
    try:
        import cv2, numpy as np
        return cv2, np
    except ImportError:
        return None


@dataclass
class Blob:
    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def h(self) -> int:
        return self.y1 - self.y0


@dataclass
class Layout:
    ink: object
    rules_h: object
    rules_v: object
    em: float
    blobs: list = field(default_factory=list)


def analyze(img, ink_diff=INK_DIFF) -> Layout:
    """Ink, rules, body letter height and word blobs of a page image (BGR)."""
    cv2, np = _cv()
    m = img.min(axis=2)
    k = max(31, (max(img.shape[:2]) // 40) | 1)
    bg = cv2.morphologyEx(m, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    ink = (cv2.subtract(bg, m) > ink_diff).astype(np.uint8)
    em0 = max(img.shape[:2]) / 90.0
    n = max(30, int(RULE_LEN_EM * em0))
    rules_h = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (n, 1)))
    gap = max(3, int(0.3 * em0)) | 1
    joined = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (1, gap)))
    rules_v = cv2.morphologyEx(joined, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, 2 * n)))
    rules_v |= cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, n)))
    rules = cv2.dilate(rules_h | rules_v, np.ones((3, 3), np.uint8))
    text = ink & (1 - rules)
    edge = max(4, int(0.5 * em0))
    text[:, :edge] = 0; text[:, -edge:] = 0; text[:edge, :] = 0; text[-edge:, :] = 0
    _, labels, cc, _ = cv2.connectedComponentsWithStats(text, connectivity=8)
    keep = cc[:, cv2.CC_STAT_AREA] >= 4
    keep[0] = False
    text = keep[labels].astype(np.uint8)

    def word_stats(em):
        kw = max(3, int(round(0.3 * em))) | 1
        merged = cv2.dilate(text, cv2.getStructuringElement(cv2.MORPH_RECT, (kw, 3)))
        nn, _, stats, _ = cv2.connectedComponentsWithStats(merged, connectivity=8)
        stats = stats[1:nn].copy()
        stats[:, cv2.CC_STAT_LEFT] += kw // 2
        stats[:, cv2.CC_STAT_WIDTH] = np.maximum(1, stats[:, cv2.CC_STAT_WIDTH] - 2 * (kw // 2))
        stats[:, cv2.CC_STAT_TOP] += 1
        stats[:, cv2.CC_STAT_HEIGHT] = np.maximum(1, stats[:, cv2.CC_STAT_HEIGHT] - 2)
        return stats

    stats = word_stats(em0)
    wide = stats[(stats[:, cv2.CC_STAT_WIDTH] > 1.5 * stats[:, cv2.CC_STAT_HEIGHT])
                 & (stats[:, cv2.CC_STAT_HEIGHT] > 0.4 * em0) & (stats[:, cv2.CC_STAT_HEIGHT] < 2.5 * em0)]
    em = float(np.median(wide[:, cv2.CC_STAT_HEIGHT])) if len(wide) >= 5 else em0
    if abs(em - em0) > 0.25 * em0:
        stats = word_stats(em)
    blobs = [Blob(int(s[0]), int(s[1]), int(s[0] + s[2]), int(s[1] + s[3]))
             for s in stats if s[cv2.CC_STAT_AREA] >= 12 and s[3] >= 0.3 * em]
    return Layout(ink=ink, rules_h=rules_h, rules_v=rules_v, em=em, blobs=blobs)


def _hollow(comp, em) -> bool:
    """An empty rectangular frame: ink on the four sides only."""
    h, w = comp.shape
    m = max(2, int(0.3 * em))
    if h <= 3 * m or w <= 3 * m:
        return False
    total = int(comp.sum())
    if total == 0 or int(comp[m:h - m, m:w - m].sum()) > 0.12 * total:
        return False
    sides = [comp[:m].any(axis=0).mean(), comp[h - m:].any(axis=0).mean(),
             comp[:, :m].any(axis=1).mean(), comp[:, w - m:].any(axis=1).mean()]
    return min(sides) > 0.6


def _kind(comp, rules, em, has_text) -> str:
    """'box' (a frame holding text), 'blank' (an answer blank) or 'figure' for one joined stroke."""
    h, w = comp.shape
    total = int(comp.sum())
    if total == 0:
        return 'figure'
    if not _hollow(comp, em):
        if (comp & rules).sum() < 0.8 * total:
            return 'figure'               # curves or slants: a drawing
        m = max(3, int(0.6 * em))
        top = max(m, int(1.8 * em))
        inner = comp[top:h - m, m:w - m]
        if inner.size and ((inner.sum(axis=0) >= 0.6 * inner.shape[0]).any()
                           or (inner.sum(axis=1) >= 0.6 * inner.shape[1]).any()):
            return 'figure'               # a table
    if has_text:
        return 'box'
    return 'blank' if min(h, w) <= 3.2 * em else 'figure'


class _Scan:
    """The ink inside one area, read once. Coordinates are relative to the widened area's top left (x0, y0)."""

    def __init__(self, lay: Layout, box):
        cv2, np = _cv()
        em = self.em = lay.em
        H0, W0 = lay.ink.shape
        grow = int(0.4 * em)
        x0, y0 = self.x0, self.y0 = max(0, box[0] - grow), max(0, box[1] - grow)
        x1, y1 = min(W0, box[2] + grow), min(H0, box[3] + grow)
        ink = lay.ink[y0:y1, x0:x1]
        self.shape = ink.shape
        n, labels, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
        rules = cv2.dilate((lay.rules_h | lay.rules_v)[y0:y1, x0:x1], np.ones((3, 3), np.uint8))
        words = [(b.x0 - x0, b.y0 - y0, b.x1 - x0, b.y1 - y0) for b in lay.blobs
                 if b.x0 >= x0 and b.x1 <= x1 and b.y0 >= y0 and b.y1 <= y1 and 0.5 * em <= b.h <= 2.2 * em]
        self.strokes = []
        for i in range(1, n):
            sx, sy, sw, sh, _ = stats[i]
            if min(sw, sh) < SEED_MIN_EM * em:
                continue
            rect = [int(sx), int(sy), int(sx + sw), int(sy + sh)]
            inside = sum(b[2] - b[0] for b in words
                         if b[0] >= sx and b[2] <= sx + sw and b[1] >= sy and b[3] <= sy + sh)
            comp = (labels[sy:sy + sh, sx:sx + sw] == i).astype(np.uint8)
            self.strokes.append((rect, _kind(comp, rules[sy:sy + sh, sx:sx + sw], em, inside >= 0.3 * sw)))
        self.pieces = [(int(c[0]), int(c[1]), int(c[0] + c[2]), int(c[1] + c[3])) for c in stats[1:n] if c[4] >= 3]
        # Single glyphs near the frame (vertex names, lengths, dash pieces) belong to the figure.
        self.glyphs = [p for p in self.pieces if max(p[2] - p[0], p[3] - p[1]) <= 2.2 * em]
        chains = []
        for g in sorted(self.glyphs, key=lambda g: g[0]):
            for c in chains:
                last = c[-1]
                if g[0] - last[2] < 0.5 * em \
                        and min(g[3], last[3]) - max(g[1], last[1]) > 0.4 * min(g[3] - g[1], last[3] - last[1]):
                    c.append(g)
                    break
            else:
                chains.append([g])
        near = [(min(g[0] for g in c), min(g[1] for g in c), max(g[2] for g in c), max(g[3] for g in c)) for c in chains]
        # Glyph groups joined by word spaces form a row (a sentence); a long row is body text, not labels.
        rows, row_of = [], []
        for r in near:
            if r[3] - r[1] < 0.45 * em:
                row_of.append(None)
                continue
            for k, row in enumerate(rows):
                if 0 <= r[0] - row[2] < 0.8 * em \
                        and min(r[3], row[3]) - max(r[1], row[1]) > 0.4 * min(r[3] - r[1], row[3] - row[1]):
                    row[1], row[2], row[3] = min(row[1], r[1]), r[2], max(row[3], r[3])
                    row_of.append(k)
                    break
            else:
                rows.append(list(r))
                row_of.append(len(rows) - 1)

        def marker(chain) -> bool:
            """A short bracketed group such as '(1)' or '(가)' (a sub-question number)."""
            ends = (chain[0], chain[-1])
            return 3 <= len(chain) <= 4 and all(g[3] - g[1] >= 0.7 * em and g[2] - g[0] <= 0.5 * (g[3] - g[1]) for g in ends)

        keep = [k for k, r in enumerate(near) if r[2] - r[0] <= LABEL_MAX_EM * em]
        self.labels = [near[k] for k in keep]
        self.not_label = [marker(chains[k]) or (row_of[k] is not None
                                               and rows[row_of[k]][2] - rows[row_of[k]][0] > LABEL_MAX_EM * em) for k in keep]
        self.sentences = [tuple(r) for r in rows if r[2] - r[0] > LABEL_MAX_EM * em]

    def absorb_labels(self, regions, strict=False):
        reach = LABEL_REACH_EM * self.em
        taken = [strict and s for s in self.not_label]
        for _ in range(2):  # frame -> dotted arc -> the length beyond it; more rounds spread into body rows
            base = [list(r) for r in regions]
            for li, (gx0, gy0, gx1, gy1) in enumerate(self.labels):
                if taken[li]:
                    continue
                for r, o in zip(regions, base):
                    if gx0 < o[2] + reach and gx1 > o[0] - reach and gy0 < o[3] + reach and gy1 > o[1] - reach:
                        r[0], r[1], r[2], r[3] = min(r[0], gx0), min(r[1], gy0), max(r[2], gx1), max(r[3], gy1)
                        taken[li] = True
                        break

    def absorb_rows(self, regions):
        """Text rows lying across the frame are its labels: body text wraps around a figure, never over it."""
        base = [list(r) for r in regions]
        for row in self.sentences:
            width, height = row[2] - row[0], row[3] - row[1]
            if width > ROW_LABEL_MAX_EM * self.em:
                continue
            for r, o in zip(regions, base):
                w, h = min(row[2], o[2]) - max(row[0], o[0]), min(row[3], o[3]) - max(row[1], o[1])
                if h > 0.5 * height and w >= 0.25 * width:
                    r[0], r[1], r[2], r[3] = min(r[0], row[0]), min(r[1], row[1]), max(r[2], row[2]), max(r[3], row[3])
                    break

    def clean_edges(self, regions):
        """An edge never halves a glyph: a glyph it crosses comes in whole (each side moves a little at most)."""
        limit = int(1.2 * self.em)
        for m in regions:
            o = list(m)
            for g in self.glyphs:
                w, h = min(o[2], g[2]) - max(o[0], g[0]), min(o[3], g[3]) - max(o[1], g[1])
                inside = g[0] >= o[0] and g[1] >= o[1] and g[2] <= o[2] and g[3] <= o[3]
                if w > 0 and h > 0 and not inside and w * h >= 0.3 * (g[2] - g[0]) * (g[3] - g[1]):
                    m[0], m[1] = max(o[0] - limit, min(m[0], g[0])), max(o[1] - limit, min(m[1], g[1]))
                    m[2], m[3] = min(o[2] + limit, max(m[2], g[2])), min(o[3] + limit, max(m[3], g[3]))

    def to_page(self, regions):
        pad = int(0.25 * self.em)
        H, W = self.shape
        return [(self.x0 + max(0, m[0] - pad), self.y0 + max(0, m[1] - pad),
                 self.x0 + min(W, m[2] + pad), self.y0 + min(H, m[3] + pad)) for m in regions]


def snap(lay: Layout, box, want):
    """`want` (page pixels, x0 y0 x1 y1) fitted to the ink: the strokes it mostly covers plus their labels, or None."""
    scan = _Scan(lay, box)
    em = scan.em
    wx0, wy0, wx1, wy1 = want[0] - scan.x0, want[1] - scan.y0, want[2] - scan.x0, want[3] - scan.y0
    tol = max(em, 0.06 * max(wx1 - wx0, wy1 - wy0))
    wx0, wy0, wx1, wy1 = wx0 - tol, wy0 - tol, wx1 + tol, wy1 + tol

    def share(r):
        w, h = min(wx1, r[2]) - max(wx0, r[0]), min(wy1, r[3]) - max(wy0, r[1])
        return max(0.0, w) * max(0.0, h) / max(1.0, (r[2] - r[0]) * (r[3] - r[1]))

    picked = [(list(r), kind) for r, kind in scan.strokes if share(r) >= 0.5]
    chosen = [r for r, kind in picked if kind != 'blank']
    # An answer blank belongs to the figure only when it sits right beside it.
    chosen += [r for r, kind in picked if kind == 'blank' and (not chosen or any(
        r[0] < o[2] + em and r[2] > o[0] - em and r[1] < o[3] + em and r[3] > o[1] - em for o in chosen))]
    if chosen:
        scan.absorb_labels(chosen, strict=True)
        scan.absorb_rows(chosen)
    else:
        # No large stroke (a figure of small pieces): gather the ink in the box, but not the glyphs of a long row
        # that runs in from outside it (body text passing beside the figure).
        def body(p):
            return any((s[0] < wx0 - em or s[2] > wx1 + em) and p[0] >= s[0] and p[2] <= s[2]
                       and min(p[3], s[3]) - max(p[1], s[1]) > 0.5 * (p[3] - p[1]) for s in scan.sentences)
        chosen = [list(p) for p in scan.pieces if share(p) >= 0.7 and not body(p)]
        if not chosen:
            return None
    union = [min(r[0] for r in chosen), min(r[1] for r in chosen), max(r[2] for r in chosen), max(r[3] for r in chosen)]
    scan.clean_edges([union])
    return scan.to_page([union])[0]


def _area(r):
    return max(0, r[2] - r[0]) * max(0, r[3] - r[1])


_CACHE = {}


def _layouts(image_path):
    """(strict, picker) layouts of a page image, kept for the page last read (a render fits its figures one by one)."""
    import os
    cv2, np = _cv()
    key = (str(image_path), os.stat(image_path).st_mtime_ns)
    if key not in _CACHE:
        img = cv2.imdecode(np.fromfile(str(image_path), np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return None
        _CACHE.clear()
        _CACHE[key] = (analyze(img, STRICT_INK_DIFF), analyze(img, INK_DIFF))
    return _CACHE[key]


def fit_boxes(image_path, boxes):
    """{id: [x, y, w, h]} rough boxes on one page image -> {id: fitted [x, y, w, h]}; ids that fit nothing are left out.
    Empty when OpenCV is missing. The page is analysed once per ink level, not per figure."""
    lib = _cv()
    if lib is None or not boxes:
        return {}
    layouts = _layouts(image_path)
    if layouts is None:
        return {}
    strict_lay, loose_lay = layouts
    out = {}
    for fid, b in boxes.items():
        want = (int(b[0]), int(b[1]), int(b[0] + b[2]), int(b[1] + b[3]))
        if want[2] - want[0] < 8 or want[3] - want[1] < 8:
            continue
        margin = int(AREA_MARGIN_EM * strict_lay.em)
        area = (max(0, want[0] - margin), max(0, want[1] - margin), want[2] + margin, want[3] + margin)
        got, loose = snap(strict_lay, area, want), snap(loose_lay, area, want)
        if got is None or (loose is not None and _area(got) < STRICT_KEEPS * _area(loose)):
            got = loose  # light print fell apart at the strict level
        if got is not None:
            out[fid] = [got[0], got[1], got[2] - got[0], got[3] - got[1]]
    return out
