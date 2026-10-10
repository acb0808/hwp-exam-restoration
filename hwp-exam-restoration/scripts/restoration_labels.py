"""Engine-side label placement for fitted figures (no model calls).

Agents place labels by guess, so a line often runs through a letter or two labels overlap.
The fitted document logs every node box and every named point at the end of the picture
(NODE_HOOK). After a render, the label ink is measured on the PDF against the strokes, point
marks and other labels; a crossed label is moved to the nearest free spot within a few mm and
the figure is rendered once more with the shift table. A move never
- carries a label nearer to another vertex than to its own,
- crosses a stroke that does not already run through the middle of the label (an angle value stays inside its angle),
- touches symbol-only nodes (angle dots, crosses), long text or framed nodes.
Labels that cannot be freed are reported so the producer hears about them once.
"""
import math, re
from contextlib import contextmanager
from pathlib import Path

PT_MM = 25.4 / 72.27        # TeX pt
BP_MM = 25.4 / 72           # PDF pt
MARGIN_MM = 0.25            # clearance a moved label keeps from strokes
TOUCH = 0.15                # measured overlap (mm of stroke, with the margin) that makes a label a candidate
CLEAR = 0.10                # overlap still counted as crossed after placement, without the margin
RADII_MM = (0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0)
DIRECTIONS = 16
TRAVEL = 0.08               # cost per mm moved: the nearest free spot wins
OWNER_REACH_MM = 3.5        # a label this close to a vertex names that vertex
CORE_SHARE = 0.25           # a stroke outside the middle half of a label leaves the label on one side of it
MAX_LABEL_MM = 15.0         # wider text is a caption, not a point label
RULE_MM = 0.25              # filled rules this thin are the bars of \sqrt and \frac (0.4pt = 0.14mm)
SIGN_ABOVE_MM, SIGN_BELOW_MM = 0.5, 0.2  # how far signs (radical bar, degree, prime) reach past the letters and digits
POINT_NAME = re.compile(r"[A-Za-z][A-Za-z0-9']{0,11}")
BACKING_WIDER_MM, BACKING_TALLER_MM = 4.5, 1.2   # a label's own white backing: wider than its glyphs by a radical sign (3.5 mm measured), taller by its padding (0.7 mm)

# Every node gets a running alias (xn1, xn2, ...) and an optional shift from \ExamShift<n>; the end of the
# picture logs node boxes, named points and the bounding box. Nothing is drawn: renders stay pixel-identical.
NODE_HOOK = r'''\makeatletter
\newcount\ExamNode
\def\ExamPoints{}
\def\ExamNodeDump{\ifnum\ExamNode>0 \foreach\exam@i in {1,...,\the\ExamNode}{\edef\exam@f{\csname ExamNodeName\exam@i\endcsname}%
  \pgfutil@ifundefined{pgf@sh@ns@\exam@f}{}{\edef\exam@s{\csname pgf@sh@ns@\exam@f\endcsname}\def\exam@c{coordinate}%
  \ifx\exam@s\exam@c\else
  \path let \p1=(\exam@f.south west), \p2=(\exam@f.north east) in \pgfextra{\typeout{EXAMNODE \exam@i:\x1,\y1,\x2,\y2}};\fi}}\fi
  \@for\exam@p:=\ExamPoints\do{\pgfutil@ifundefined{pgf@sh@ns@\exam@p}{}{\path let \p1=(\exam@p) in \pgfextra{\typeout{EXAMPT \exam@p:\x1,\y1}};}}%
  \path let \p1=(current bounding box.south west), \p2=(current bounding box.north east) in \pgfextra{\typeout{EXAMBB \x1,\y1,\x2,\y2}};}
\tikzset{every picture/.append style={execute at begin picture={\global\ExamNode=0\relax},execute at end picture={\ExamNodeDump}},
  every node/.append style={/exam/tag},
  /exam/tag/.code={\global\advance\ExamNode by1\relax\edef\exam@n{\the\ExamNode}%
    \expandafter\xdef\csname ExamNodeName\exam@n\endcsname{\tikz@pp@name{xn\exam@n}}% inside a pic the alias carries the pic's prefix
    \pgfkeysalso{/tikz/alias/.expanded=xn\exam@n}%
    \ifcsname ExamShift\exam@n\endcsname\pgfkeysalso{/tikz/shift/.expanded={(\csname ExamShift\exam@n\endcsname)}}\fi}}
\makeatother
'''


def point_names(text):
    """Named points of a picture: \\coordinate (A), \\node (A), by={P,Q}, name=A."""
    text = re.sub(r'(?<!\\)%[^\n]*', '', text)
    found = re.findall(r'(?:\\node|coordinate)\s*(?:\[[^\]]*\])?\s*\(([^()]+)\)', text)
    found += re.findall(r'\bname\s*=\s*([^,\]\s]+)', text)
    # "sort by=l" names a path, not a point.
    for group in re.findall(r'(?<!sort )\bby\s*=\s*\{([^{}]*)\}', text) + re.findall(r'(?<!sort )\bby\s*=\s*([^,\]{}\s]+)', text):
        found += [n.strip() for n in group.split(',')]
    names = []
    for n in found:
        if POINT_NAME.fullmatch(n) and n not in names: names.append(n)
    return names[:60]


def preamble(text, shifts=None):
    """Hook, the picture's point names and the shift table (node number -> (dx, dy) in mm, TikZ y up)."""
    table = ''.join('\\expandafter\\def\\csname ExamShift%d\\endcsname{%.2fmm,%.2fmm}\n' % (int(n), v[0], v[1])
                    for n, v in sorted((shifts or {}).items(), key=lambda x: int(x[0])))
    return NODE_HOOK + '\\def\\ExamPoints{' + ','.join(point_names(text)) + '}\n' + table


@contextmanager
def _tight_glyphs():
    """Glyph boxes at font size instead of full ascent/descent, for this measurement only."""
    import fitz
    from restoration_figure_geometry import PDF_TEXT  # the switch is process-wide
    with PDF_TEXT:
        before = fitz.TOOLS.set_small_glyph_heights()
        fitz.TOOLS.set_small_glyph_heights(True)
        try: yield
        finally: fitz.TOOLS.set_small_glyph_heights(bool(before))


def logged(pdf, border_pt=2):
    """Node boxes and named points of the final picture in PDF mm (y down), read from the render log.
    Logged values are in the picture's own units before the fit scale, so the page width gives the scale."""
    import fitz
    log = Path(pdf).with_name('compile.log').read_text(encoding='utf-8', errors='replace')
    parts = re.split(r'EXAMBB (\S+)', log)
    if len(parts) < 3: return {}, {}
    body, bb = parts[-3], [float(v[:-2]) for v in parts[-2].split(',')]
    with fitz.open(pdf) as doc: width = doc[0].rect.width * BP_MM
    border = border_pt * PT_MM
    if bb[2] - bb[0] <= 0: return {}, {}
    k = (width - 2 * border) / ((bb[2] - bb[0]) * PT_MM)
    def at(x, y): return ((x - bb[0]) * PT_MM * k + border, (bb[3] - y) * PT_MM * k + border)
    nodes, points = {}, {}
    for n, v in re.findall(r'EXAMNODE (\d+):(\S+)', body):
        x0, y0, x1, y1 = (float(t[:-2]) for t in v.split(','))
        (ax, by), (bx, ay) = at(x0, y0), at(x1, y1)
        nodes[int(n)] = (ax, ay, bx, by)
    for name, v in re.findall(r'EXAMPT (\S+?):(\S+)', body):
        x, y = (float(t[:-2]) for t in v.split(','))
        points[name] = at(x, y)
    return nodes, points


def points(pdf):
    """Named points of a rendered fitted figure in PDF mm, for relation checks."""
    try: return logged(pdf)[1]
    except Exception: return {}


def _clip(p, q, b):
    """Length of segment pq inside box b (Liang-Barsky)."""
    t0, t1 = 0.0, 1.0; dx, dy = q[0] - p[0], q[1] - p[1]
    for pp, qq in ((-dx, p[0] - b[0]), (dx, b[2] - p[0]), (-dy, p[1] - b[1]), (dy, b[3] - p[1])):
        if pp == 0:
            if qq < 0: return 0.0
        else:
            t = qq / pp
            if pp < 0: t0 = max(t0, t)
            else: t1 = min(t1, t)
            if t0 > t1: return 0.0
    return (t1 - t0) * math.hypot(dx, dy)


def _arc_in(a, b, n=96):
    step = abs(a['sweep']) * a['r'] / n; total = 0.0
    for i in range(n):
        t = a['start'] + a['sweep'] * (i + .5) / n
        x, y = a['c'][0] + a['r'] * math.cos(t), a['c'][1] + a['r'] * math.sin(t)
        if b[0] <= x <= b[2] and b[1] <= y <= b[3]: total += step
    return total


def _on_arc(a, pt):
    if abs(a['sweep']) >= 2 * math.pi - 1e-3: return True
    d = math.atan2(pt[1] - a['c'][1], pt[0] - a['c'][0]) - a['start']
    if a['sweep'] < 0: d = -d
    return d % (2 * math.pi) <= abs(a['sweep']) + 1e-3


def _overlap(a, b):
    return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))


def _centre(b): return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)


def _grow(b, m): return (b[0] - m, b[1] - m, b[2] + m, b[3] + m)


def _framed(box, geo, slack=0.3):
    """A node that draws its own border (boxed text, circled numbers): its text belongs inside."""
    def on_edge(p): return (min(abs(p[0] - box[0]), abs(p[0] - box[2])) <= slack and box[1] - slack <= p[1] <= box[3] + slack) or \
                           (min(abs(p[1] - box[1]), abs(p[1] - box[3])) <= slack and box[0] - slack <= p[0] <= box[2] + slack)
    side = min(box[2] - box[0], box[3] - box[1])
    if any(on_edge(s['p']) and on_edge(s['q']) and math.dist(s['p'], s['q']) >= .5 * side for s in geo['segments']): return True
    c = _centre(box); half = max(box[2] - box[0], box[3] - box[1]) / 2
    return any(math.dist(a['c'], c) <= slack and a['r'] <= half * 1.5 + slack and abs(a['sweep']) >= math.pi for a in geo['arcs'])


SHARED_LIMIT = 4096   # ways to share out the glyphs of overlapping nodes that are still tried one by one
EMPTY_NODE_MM = 2.5   # a node box at least this big on both sides holds text


def _owners(glyphs, nodes):
    """Glyphs per node. Where labels overlap a glyph lies in several node boxes; TikZ centres a node's text in
    its box, so the glyphs are shared out the way that leaves every node's text centred and inside its box.
    (Read into both, "B" and "60°" became two labels "B60°" with one ink box, and neither could move clear.)"""
    import itertools
    owned = {}; shared = []
    for g in glyphs:
        if not g['c'].strip(): continue
        holders = [n for n, box in nodes.items() if box[0] - .2 <= g['at'][0] <= box[2] + .2 and box[1] - .2 <= g['at'][1] <= box[3] + .2]
        if len(holders) == 1: owned.setdefault(holders[0], []).append(g)
        elif holders: shared.append((g, holders))
    if not shared: return owned

    def off(n, run):
        box = nodes[n]
        if not run: return 3.0 if min(box[2] - box[0], box[3] - box[1]) >= EMPTY_NODE_MM else 0.0
        ink = (min(g['box'][0] for g in run), min(g['box'][1] for g in run), max(g['box'][2] for g in run), max(g['box'][3] for g in run))
        over = max(0, box[0] - ink[0]) + max(0, ink[2] - box[2]) + max(0, box[1] - ink[1]) + max(0, ink[3] - box[3])
        c, k = _centre(ink), _centre(box)
        return abs(c[0] - k[0]) + .5 * abs(c[1] - k[1]) + 2 * over

    def deepest(g, holders):  # fallback: a node's own glyphs sit inside its padding
        c = _centre(g['box'])
        return max(holders, key=lambda n: min(c[0] - nodes[n][0], nodes[n][2] - c[0], c[1] - nodes[n][1], nodes[n][3] - c[1]))
    ways = 1
    for _, holders in shared: ways *= len(holders)
    if ways > SHARED_LIMIT:
        for g, holders in shared: owned.setdefault(deepest(g, holders), []).append(g)
        return owned
    involved = sorted({n for _, holders in shared for n in holders}); best = None
    for choice in itertools.product(*(holders for _, holders in shared)):
        runs = {n: list(owned.get(n, [])) for n in involved}
        for (g, _), n in zip(shared, choice): runs[n].append(g)
        cost = sum(off(n, run) for n, run in runs.items())
        if best is None or cost < best[0] - 1e-9: best = (cost, runs)
    owned.update(best[1])
    return owned


def labels(pdf):
    """Strokes of the figure and, per text node, the ink box of its glyphs."""
    import fitz
    from restoration_figure_geometry import extract
    nodes, _ = logged(pdf)
    with _tight_glyphs():
        geo = extract(pdf)
        fills, rules = [], []
        with fitz.open(pdf) as doc:
            for d in doc[0].get_drawings():
                if d.get('type') not in ('f', 'fs') or not d.get('fill'): continue
                r = d['rect']; f = (r.x0 * BP_MM, r.y0 * BP_MM, r.x1 * BP_MM, r.y1 * BP_MM)
                if min(d['fill']) > .95: fills.append(f)
                elif min(r.width, r.height) * BP_MM <= RULE_MM: rules.append(f)
    # A small white backing is paper and a short TeX rule is a radical or fraction bar: neither is a point mark.
    geo['marks'] = [m for m in geo['marks'] if not any(math.dist(m, _centre(f)) < .1 for f in fills + rules)]
    owned = _owners(geo['glyphs'], nodes)
    found = {}
    for n, box in nodes.items():
        inside = owned.get(n, [])
        if not any(g['c'].isalnum() for g in inside): continue  # empty nodes and mark symbols (dots, crosses)
        # Letters and digits set the height: the font box of a radical sign or a tall bracket is far larger than its ink.
        tall = [g['box'] for g in inside if g['c'].isalnum()]
        top, bottom = min(b[1] for b in tall) - SIGN_ABOVE_MM, max(b[3] for b in tall) + SIGN_BELOW_MM
        ink = (min(g['box'][0] for g in inside), max(top, min(g['box'][1] for g in inside)),
               max(g['box'][2] for g in inside), min(bottom, max(g['box'][3] for g in inside)))
        if ink[2] - ink[0] > MAX_LABEL_MM or _framed(box, geo): continue
        c = _centre(ink); area = (ink[2] - ink[0]) * (ink[3] - ink[1])
        # A white-backed label (\ExamLengthArc) hides its own dashed arc on purpose; only solid lines count against it.
        backing = next((f for f in fills if f[0] <= c[0] <= f[2] and f[1] <= c[1] <= f[3] and _overlap(f, ink) > .5 * area), None)
        # The white backing hides every stroke under it, and a radical sign is no glyph: the backing is the label's size.
        # Only a backing cut to the label: the white cell of a table took its whole cell for the label, and the
        # six angles of one table were pushed onto their rules.
        if backing and backing[2] - backing[0] <= ink[2] - ink[0] + BACKING_WIDER_MM and backing[3] - backing[1] <= ink[3] - ink[1] + BACKING_TALLER_MM:
            ink = (min(ink[0], backing[0]), min(ink[1], backing[1]), max(ink[2], backing[2]), max(ink[3], backing[3]))
        found[n] = {'ink': ink, 'backed': backing is not None, 'text': ''.join(g['c'] for g in sorted(inside, key=lambda g: g['at'][0]))}
    return geo, found


def _cost(box, geo, backed, others, margin):
    b = _grow(box, margin)
    c = sum(_clip(s['p'], s['q'], b) for s in geo['segments'] if not s['symbol'] and not (backed and s['dashed']))
    c += sum(_arc_in(a, b) for a in geo['arcs'] if not (backed and a['dashed']))
    c += sum(2.0 for m in geo['marks'] if b[0] <= m[0] <= b[2] and b[1] <= m[1] <= b[3])
    return c + sum(_overlap(b, o) * 4 for o in others)


def _crosses(c0, c1, segments, arcs, step=.25, half=.12):
    """Does the straight path of a label centre pass over one of these strokes?"""
    n = max(1, int(math.dist(c0, c1) / step))
    for k in range(n + 1):
        x, y = c0[0] + (c1[0] - c0[0]) * k / n, c0[1] + (c1[1] - c0[1]) * k / n
        b = (x - half, y - half, x + half, y + half)
        if any(_clip(s['p'], s['q'], b) for s in segments): return True
        if any(abs(math.dist((x, y), a['c']) - a['r']) <= half and _on_arc(a, (x, y)) for a in arcs): return True
    return False


def solve(geo, found):
    """Greedy: each crossed label goes to the cheapest nearby spot. Returns {node: (dx, dy)} in mm, TikZ y up.
    A label that finds no free spot within reach stays where it is. It is not led out with an arrow: the figure
    copies its source, and where the source fits a value without an arrow, a value that does not fit says the
    drawing differs from the source (the producer draws the arrows the source prints)."""
    vertices = [s['p'] for s in geo['segments']] + [s['q'] for s in geo['segments']] + list(geo['marks'])
    boxes = {n: l['ink'] for n, l in found.items()}; shifts = {}
    for n, l in found.items():
        others = [b for m, b in boxes.items() if m != n]; box = boxes[n]
        base = _cost(box, geo, l['backed'], others, MARGIN_MM)
        if base < TOUCH: continue
        c0 = _centre(box)
        owner = min(vertices, key=lambda v: math.dist(c0, v), default=None)
        if owner is not None and math.dist(c0, owner) > OWNER_REACH_MM: owner = None
        # A stroke through the middle of the label has no side yet, so either side is fine. One that only clips
        # an edge has: an angle value squeezed between its two sides must not leave the angle.
        core = _grow(box, -CORE_SHARE * min(box[2] - box[0], box[3] - box[1]))
        # An angle value belongs to the region it is printed in: it never changes side of a line, only of its arc.
        angle = l['text'][-1:] in '°◦' and l['text'][:-1].replace('.', '').isalnum()
        clear_segments = [s for s in geo['segments'] if not s['symbol'] and (angle or _clip(s['p'], s['q'], core) == 0)]
        clear_arcs = [a for a in geo['arcs'] if _arc_in(a, core) == 0]
        best = (base, 0.0, 0.0)
        # A label that only stands close to strokes (inside the margin) is never moved onto one: a table cell as
        # high as its text has a rule within the margin above and below, and half a millimetre up, where only one
        # rule is left in the margin, the text was on that rule (six angles of one table).
        real = _cost(box, geo, l['backed'], others, 0)
        for r in RADII_MM:
            for k in range(DIRECTIONS):
                dx, dy = r * math.cos(k * 2 * math.pi / DIRECTIONS), r * math.sin(k * 2 * math.pi / DIRECTIONS)
                moved = (box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy); c1 = _centre(moved)
                if _crosses(c0, c1, clear_segments, clear_arcs): continue
                if owner is not None:
                    d = math.dist(c1, owner)
                    if any(math.dist(c1, v) < .9 * d for v in vertices if math.dist(v, owner) > 1.0): continue
                cost = _cost(moved, geo, l['backed'], others, MARGIN_MM) + TRAVEL * r
                if cost < best[0] - 1e-9 and _cost(moved, geo, l['backed'], others, 0) <= real + 1e-9: best = (cost, dx, dy)
        if best[1] or best[2]:
            shifts[n] = (round(best[1], 2), round(-best[2], 2))  # PDF y runs down
            boxes[n] = (box[0] + best[1], box[1] + best[2], box[2] + best[1], box[3] + best[2])
    return shifts


def label_shifts(pdf):
    """Shift table for a rendered fitted figure; empty when no label is crossed (or nothing was logged)."""
    try:
        geo, found = labels(pdf)
        return solve(geo, found)
    except Exception:
        return {}  # a placement aid; the render itself already succeeded


def overlapping_labels(pdf):
    """Pairs of label texts whose ink still overlaps: text on text cannot be read, unlike a label on a line."""
    try:
        _, found = labels(pdf); rows = sorted(found.items())
        return [(a['text'], b['text']) for i, (_, a) in enumerate(rows) for _, b in rows[i + 1:] if _overlap(a['ink'], b['ink']) > CLEAR]
    except Exception:
        return []


def crossed_labels(pdf):
    """Texts of labels still crossed by a stroke, a mark or another label."""
    try:
        geo, found = labels(pdf)
        return [l['text'] for n, l in found.items()
                if _cost(l['ink'], geo, l['backed'], [m['ink'] for k, m in found.items() if k != n], 0) > CLEAR]
    except Exception:
        return []
