"""Graph commands the producer writes instead of hand-drawn axes, curves and axis labels.

\\ExamAxes[x=$t$,y=$x(t)$,O=]{xmin}{xmax}{ymin}{ymax}: both axes with arrowheads, the axis names where workbooks
print them (x right of and just under its arrow, y left of and just under its arrow) and O below left of the origin
(O= leaves it out).
\\ExamCurve[style]{(x0,y0) (x1,y1) ...}: the graph of a function through the points read from the source (ends,
intercepts, peaks and valleys, in increasing x). Drawn as a monotone cubic (Fritsch-Carlson): it does not overshoot
between points, so a peak or valley is exactly at the listed point with a level tangent. Hand-drawn Bezier control
points made kinks and wrong peaks in real runs (a derivative graph judged "not a smooth curve", 2026-10-14).
\\ExamXLabel{a}{$a$} / \\ExamYLabel{b}{$b$}: a label at x=a on the x-axis (y=b on the y-axis), on the side the curves
of the picture leave free: below by default, below right where a curve crosses going up, below left where it crosses
going down, above where a curve lies just under the axis. An intercept label set under the crossing sat on the curve.

Expanded to plain TikZ before typesetting; plain Python, no numpy.
"""
import math
import re

NUM = r'[-+]?(?:\d+\.?\d*|\.\d+)'
POINT = re.compile(r'\(\s*(' + NUM + r')\s*,\s*(' + NUM + r')\s*\)')
# Labels print at 0.85 x 10 pt: a letter is about 1.5 mm wide and 2.2 mm from top to descender; inner sep 1 pt.
LABEL_H_MM, LABEL_CHAR_MM, LABEL_SEP_MM, LABEL_SHIFT_MM, LABEL_CLEAR_MM = 2.2, 1.5, 0.35, 0.4, 0.3
ARROW = '-{Stealth[length=1.4mm,width=1mm]}'


def _braced(text, i):
    """The {...} group starting at text[i] (after spaces): (content, end index)."""
    while i < len(text) and text[i] in ' \t\r\n': i += 1
    if i >= len(text) or text[i] != '{': raise ValueError('graph_tool_syntax: braces expected')
    depth = 0
    for j in range(i, len(text)):
        depth += {'{': 1, '}': -1}.get(text[j], 0)
        if depth == 0: return text[i + 1:j], j + 1
    raise ValueError('graph_tool_syntax: unbalanced braces')


def _options(text, i):
    while i < len(text) and text[i] in ' \t': i += 1
    if i < len(text) and text[i] == '[':
        depth = 0
        for j in range(i, len(text)):
            depth += {'[': 1, ']': -1, '{': 1, '}': -1}.get(text[j], 0)
            if depth == 0: return text[i + 1:j], j + 1
        raise ValueError('graph_tool_syntax: unbalanced brackets')
    return '', i


def _split_options(opts):
    parts, depth, cur = [], 0, ''
    for ch in opts:
        if ch == ',' and depth == 0: parts.append(cur); cur = ''; continue
        depth += {'{': 1, '}': -1, '$': 0}.get(ch, 0); cur += ch
    parts.append(cur)
    return [p.strip() for p in parts if p.strip()]


def _calls(text, name, args):
    """Every \\name[opts]{arg}...: list of (start, end, opts, [args])."""
    out = []
    for m in re.finditer(r'\\' + name + r'(?![A-Za-z])', text):
        opts, i = _options(text, m.end()); got = []
        for _ in range(args):
            a, i = _braced(text, i); got.append(a)
        out.append((m.start(), i, opts, got))
    return out


def _num(value, what):
    try: return float(value)
    except ValueError: raise ValueError(f'graph_tool_number_required: {what}={value.strip()}') from None


def slopes(xs, ys):
    """Fritsch-Carlson tangents (as in PCHIP): zero where the data turn, so peaks stay at the listed points."""
    n = len(xs); h = [xs[k + 1] - xs[k] for k in range(n - 1)]; d = [(ys[k + 1] - ys[k]) / h[k] for k in range(n - 1)]
    if n == 2: return [d[0], d[0]]
    m = [0.0] * n
    for k in range(1, n - 1):
        if d[k - 1] * d[k] > 0:
            w1, w2 = 2 * h[k] + h[k - 1], h[k] + 2 * h[k - 1]
            m[k] = (w1 + w2) / (w1 / d[k - 1] + w2 / d[k])
    def end(h0, h1, d0, d1):
        e = ((2 * h0 + h1) * d0 - h0 * d1) / (h0 + h1)
        if e * d0 <= 0: return 0.0
        if d0 * d1 <= 0 and abs(e) > 3 * abs(d0): return 3 * d0
        return e
    m[0] = end(h[0], h[1], d[0], d[1]); m[-1] = end(h[-1], h[-2], d[-1], d[-2])
    return m


def curve_segments(points):
    """Cubic Bezier pieces (p0, c1, c2, p1) of the monotone curve through the points."""
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    if len(points) < 2: raise ValueError('graph_curve_needs_two_points')
    if any(b <= a for a, b in zip(xs, xs[1:])): raise ValueError('graph_curve_x_must_increase: list the points left to right, one y for each x')
    m = slopes(xs, ys); out = []
    for k in range(len(points) - 1):
        h = xs[k + 1] - xs[k]
        out.append(((xs[k], ys[k]), (xs[k] + h / 3, ys[k] + m[k] * h / 3), (xs[k + 1] - h / 3, ys[k + 1] - m[k + 1] * h / 3), (xs[k + 1], ys[k + 1])))
    return out


def _bezier(seg, t):
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = seg; u = 1 - t
    return (u**3 * x0 + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t**3 * x3, u**3 * y0 + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t**3 * y3)


def _f(v): return f'{v:.4f}'.rstrip('0').rstrip('.') if abs(v) > 1e-9 else '0'


def _label_place(at, text, axis, ink, unit):
    """The side of an axis label the curves leave free. at: point on the axis; unit: picture units per printed mm."""
    chars = len(re.sub(r'\\[A-Za-z]+', 'x', re.sub(r'[{}$^_ ]', '', text))) or 1
    w, h = LABEL_CHAR_MM * min(chars, 6) * unit, LABEL_H_MM * unit      # the printed glyphs
    s, d = LABEL_SEP_MM * unit, LABEL_SHIFT_MM * unit                    # inner sep; the sideways step of a corner place
    x, y = at
    # Each place: (anchor, shift in mm, the glyph box it gives), the glyphs one inner sep inside the node.
    if axis == 'x':
        places = {'below': ('north', (0, 0), (x - w / 2, y - s - h, x + w / 2, y - s)),
                  'below right': ('north west', (LABEL_SHIFT_MM, 0), (x + d + s, y - s - h, x + d + s + w, y - s)),
                  'below left': ('north east', (-LABEL_SHIFT_MM, 0), (x - d - s - w, y - s - h, x - d - s, y - s)),
                  'above': ('south', (0, 0), (x - w / 2, y + s, x + w / 2, y + s + h)),
                  'above right': ('south west', (LABEL_SHIFT_MM, 0), (x + d + s, y + s, x + d + s + w, y + s + h)),
                  'above left': ('south east', (-LABEL_SHIFT_MM, 0), (x - d - s - w, y + s, x - d - s, y + s + h))}
    else:
        places = {'left': ('east', (0, 0), (x - s - w, y - h / 2, x - s, y + h / 2)),
                  'below left': ('north east', (0, -LABEL_SHIFT_MM), (x - s - w, y - d - s - h, x - s, y - d - s)),
                  'above left': ('south east', (0, LABEL_SHIFT_MM), (x - s - w, y + d + s, x - s, y + d + s + h)),
                  'right': ('west', (0, 0), (x + s, y - h / 2, x + s + w, y + h / 2)),
                  'below right': ('north west', (0, -LABEL_SHIFT_MM), (x + s, y - d - s - h, x + s + w, y - d - s)),
                  'above right': ('south west', (0, LABEL_SHIFT_MM), (x + s, y + d + s, x + s + w, y + d + s + h))}
    # A curve through the point leaves two opposite corners free: the label takes the one on its own side of the
    # axis, so it reads as outside the curve (an intercept where the curve comes down is labelled below left).
    near = [(px - x, py - y) for px, py in ink if math.hypot(px - x, py - y) < 3 * unit]
    if axis == 'y': near = [(dy, dx) for dx, dy in near]                 # along the axis, then across it
    before = [b for a, b in near if a < -0.3 * unit]; after = [b for a, b in near if a > 0.3 * unit]
    lean = 0.0
    if before and after:
        lo, hi = sum(before) / len(before), sum(after) / len(after)
        if lo * hi < 0: lean = hi - lo                                   # it crosses here; touching leaves both corners
    elif before or after:
        side = before or after; lean = (sum(side) / len(side)) * (1 if after else -1)
    if axis == 'x':
        order = (['below left', 'below', 'above right', 'below right', 'above', 'above left'] if lean < -1e-12 * unit else
                 ['below right', 'below', 'above left', 'below left', 'above', 'above right'] if lean > 1e-12 * unit else
                 ['below', 'below left', 'below right', 'above', 'above left', 'above right'])
    else:
        order = (['below left', 'left', 'above right', 'above left', 'right', 'below right'] if lean < -1e-12 * unit else
                 ['above left', 'left', 'below right', 'below left', 'right', 'above right'] if lean > 1e-12 * unit else
                 ['left', 'below left', 'above left', 'right', 'below right', 'above right'])

    def clearance(box):
        x0, y0, x1, y1 = box
        return min((math.hypot(max(x0 - px, 0, px - x1), max(y0 - py, 0, py - y1)) for px, py in ink), default=9e9)
    found = [(clearance(places[name][2]), rank, name) for rank, name in enumerate(order)]
    clear = [f for f in found if f[0] >= LABEL_CLEAR_MM * unit]
    anchor, (sx, sy), _ = places[min(clear, key=lambda f: f[1])[2] if clear else max(found)[2]]
    return f'anchor={anchor}' + (f',xshift={sx}mm' if sx else '') + (f',yshift={sy}mm' if sy else '')


def expand_graph_tools(text, width_mm=None):
    if not re.search(r'\\Exam(?:Axes|Curve|XLabel|YLabel)(?![A-Za-z])', text): return text
    curves = _calls(text, 'ExamCurve', 1); axes = _calls(text, 'ExamAxes', 4)
    xl, yl = _calls(text, 'ExamXLabel', 2), _calls(text, 'ExamYLabel', 2)
    segs_of = {}
    for start, end, opts, (body,) in curves:
        pts = [(float(a), float(b)) for a, b in POINT.findall(body)]
        if len(pts) != len(re.findall(r'\(', body)): raise ValueError('graph_curve_points: write (x,y) pairs of numbers')
        segs_of[start] = curve_segments(pts)
    ink = [_bezier(s, i / 60) for segs in segs_of.values() for s in segs for i in range(61)]
    xs = [p[0] for p in ink]; ys = [p[1] for p in ink]
    for _, _, _, a in axes:
        x0, x1, y0, y1 = (_num(v, k) for v, k in zip(a, ('xmin', 'xmax', 'ymin', 'ymax')))
        xs += [x0, x1]; ys += [y0, y1]
        ink += [(x0 + (x1 - x0) * i / 200, 0.0) for i in range(201)] + [(0.0, y0 + (y1 - y0) * i / 200) for i in range(201)]
    span = (max(xs) - min(xs)) if xs else 4.0
    unit = span / (width_mm or 50.0) if span > 0 else 0.1
    out, last = [], 0
    for start, end, opts, args in sorted(curves + axes + xl + yl):
        out.append(text[last:start]); last = end
        if start in segs_of:
            segs = segs_of[start]
            path = f'({_f(segs[0][0][0])},{_f(segs[0][0][1])})' + ''.join(
                f' .. controls ({_f(c1[0])},{_f(c1[1])}) and ({_f(c2[0])},{_f(c2[1])}) .. ({_f(p1[0])},{_f(p1[1])})' for _, c1, c2, p1 in segs)
            out.append(f'\\draw[{opts}] {path};' if opts else f'\\draw {path};')
        elif any(start == s for s, *_ in axes):
            x0, x1, y0, y1 = (_num(v, k) for v, k in zip(args, ('xmin', 'xmax', 'ymin', 'ymax')))
            names = {'x': '$x$', 'y': '$y$', 'O': '$O$'}
            for part in _split_options(opts):
                k, _, v = part.partition('=')
                if k.strip() not in names: raise ValueError('graph_axes_option: x=, y=, O= (empty hides O)')
                names[k.strip()] = v.strip()
            rows = [f'\\draw[{ARROW}] ({_f(x0)},0) -- ({_f(x1)},0);', f'\\draw[{ARROW}] (0,{_f(y0)}) -- (0,{_f(y1)});',
                    f'\\node[anchor=north west,inner sep=1pt] at ({_f(x1)},0) {{{names["x"]}}};',
                    f'\\node[anchor=north east,inner sep=1pt] at (0,{_f(y1)}) {{{names["y"]}}};', '\\coordinate (O) at (0,0);']
            if names['O']: rows.append(f'\\node[anchor=north east,inner sep=1.5pt] at (0,0) {{{names["O"]}}};')
            out.append('\n'.join(rows))
        else:
            axis = 'x' if any(start == s for s, *_ in xl) else 'y'
            v = _num(args[0], 'position'); at = (v, 0.0) if axis == 'x' else (0.0, v)
            near = [(px, py) for px, py in ink if abs(px - at[0]) > 1e-9 or abs(py - at[1]) > 1e-9]
            axis_line = [(px, py) for px, py in near if (py == 0.0 if axis == 'x' else px == 0.0)]
            others = [p for p in near if p not in axis_line]
            place = opts.strip() or _label_place(at, args[1], axis, others, unit)
            out.append(f'\\node[{place},inner sep=1pt] at ({_f(at[0])},{_f(at[1])}) {{{args[1]}}};')
    out.append(text[last:])
    return ''.join(out)
