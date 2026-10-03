"""Measured geometry warnings from a rendered figure PDF (no model calls).

The PDF holds what was actually drawn, however the TeX computed it: stroked segments,
circles and arcs (Bezier runs), small filled marks and label glyphs. Near-miss checks only,
in printed mm because fitted figures render at their print width:
- a line that nearly touches a circle without being tangent;
- a solid line end that stops just short of, or just past, another line or a circle;
- two circles that almost touch.
Clear crossings and clear gaps are drawings, not slips, and are never reported.
"""
import functools, math, threading

# PyMuPDF's glyph-height switch (restoration_labels._tight_glyphs) is process-wide, and renders of several
# pages now run side by side in one MCP server: every text extraction holds this lock.
PDF_TEXT = threading.RLock()


def _one_at_a_time(fn):
    @functools.wraps(fn)
    def held(*args, **kwargs):
        with PDF_TEXT: return fn(*args, **kwargs)
    return held


MM = 25.4 / 72
MIN_SEGMENT_MM = 3.0      # shorter strokes are tick, right-angle and arrow marks
MIN_RADIUS_MM = 2.5       # smaller arcs are angle marks and point dots
TANGENT_BAND = (0.02, 0.10)  # |distance - r| / r: tangent slips; past 10% a line clearly cuts or misses
GAP_MM = (0.3, 1.5)       # end-point slips: below shows as touching, above reads as an intended gap
CIRCLE_GAP_SHARE = 0.5    # circles closer than half the smaller radius were meant to touch
MAX_ENDPOINT_FINDINGS = 2  # a 5-finding tower drawing was all decoration in a real run


def _bezier_point(p, t):
    a, b, c, d = p
    u = 1 - t
    return (u**3 * a[0] + 3 * u * u * t * b[0] + 3 * u * t * t * c[0] + t**3 * d[0],
            u**3 * a[1] + 3 * u * u * t * b[1] + 3 * u * t * t * c[1] + t**3 * d[1])


def _circle3(a, b, c):
    ax, ay = a; bx, by = b; cx, cy = c
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-9: return None
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / d
    return (ux, uy), math.dist((ux, uy), a)


@_one_at_a_time
def extract(pdf_path):
    """Stroked segments and circular arcs in mm, small filled marks, and glyph centres."""
    import fitz
    segments, arcs, glyphs, marks = [], [], [], []
    with fitz.open(pdf_path) as doc:
        page = doc[0]
        for drawing in page.get_drawings():
            box = drawing['rect']
            if drawing.get('type') == 'f':
                # Fills are dots, arrow tips and shading; small ones mark where a line may end.
                if max(box.width, box.height) * MM <= 3: marks.append(((box.x0 + box.x1) / 2 * MM, (box.y0 + box.y1) / 2 * MM))
                continue
            if max(box.width, box.height) * MM <= 2.5 and any(item[0] == 'c' for item in drawing['items']):
                # Open stroked arrow tips (TikZ ->) are tiny curved strokes.
                marks.append(((box.x0 + box.x1) / 2 * MM, (box.y0 + box.y1) / 2 * MM)); continue
            dashed = bool(drawing.get('dashes') and drawing['dashes'] not in ('[] 0', '[] 0.0'))
            # Small closed outlines are symbols (hollow arrows, boxes): their corners are not slips.
            symbol = bool(drawing.get('closePath') and max(box.width, box.height) * MM <= 6)
            run, first, last = [], None, None
            for item in drawing['items']:
                if item[0] == 'l':
                    p, q = (item[1].x * MM, item[1].y * MM), (item[2].x * MM, item[2].y * MM)
                    first = first or p; last = q
                    if math.dist(p, q) >= 1e-6: segments.append({'p': p, 'q': q, 'dashed': dashed, 'symbol': symbol})
                elif item[0] == 'c':
                    run.append([(pt.x * MM, pt.y * MM) for pt in item[1:5]])
                elif item[0] == 're':
                    r = item[1]; corners = [(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1)]
                    for i in range(4):
                        p, q = corners[i], corners[(i + 1) % 4]
                        segments.append({'p': (p[0] * MM, p[1] * MM), 'q': (q[0] * MM, q[1] * MM), 'dashed': dashed, 'symbol': False})
            if drawing.get('closePath') and first and last and math.dist(first, last) >= 1e-6:
                segments.append({'p': last, 'q': first, 'dashed': dashed, 'symbol': symbol})  # the implicit closing edge
            arcs.extend(_arcs(run, dashed))
        for block in page.get_text('rawdict')['blocks']:
            for line in block.get('lines', []):
                for span in line['spans']:
                    for ch in span['chars']:
                        x0, y0, x1, y1 = ch['bbox']
                        glyphs.append({'c': ch['c'], 'at': ((x0 + x1) / 2 * MM, (y0 + y1) / 2 * MM),
                                       'box': (x0 * MM, y0 * MM, x1 * MM, y1 * MM)})
    # Fraction bars, radical bars and overlines are short horizontal rules hugging glyphs: label text, not figure lines.
    segments = [s for s in segments if not _text_rule(s, glyphs)]
    return {'segments': segments, 'arcs': arcs, 'glyphs': glyphs, 'marks': marks}


def _text_rule(s, glyphs):
    (x0, y0), (x1, y1) = s['p'], s['q']
    if abs(y1 - y0) > .05 or abs(x1 - x0) > 10: return False
    lo, hi = min(x0, x1), max(x0, x1)
    return any(g['box'][0] < hi and g['box'][2] > lo and min(abs(y0 - g['box'][1]), abs(y0 - g['box'][3])) < 1.2
               for g in glyphs if g['c'].strip())


def _arcs(beziers, dashed):
    """Group consecutive Bezier pieces lying on one circle; non-circular curves are skipped."""
    found, current = [], None
    for bz in beziers:
        fit = _circle3(bz[0], _bezier_point(bz, .5), bz[3])
        if not fit: current = None; continue
        centre, r = fit
        if any(abs(math.dist(centre, _bezier_point(bz, t)) - r) > max(.05, r * .01) for t in (.25, .75)):
            current = None; continue
        a0 = math.atan2(bz[0][1] - centre[1], bz[0][0] - centre[0])
        sweep = _sweep(centre, bz)
        if current and math.dist(current['c'], centre) < max(.1, r * .01) and abs(current['r'] - r) < max(.1, r * .01):
            current['sweep'] += sweep
        else:
            current = {'c': centre, 'r': r, 'start': a0, 'sweep': sweep, 'dashed': dashed}; found.append(current)
    return found


def _sweep(centre, bz):
    angles = [math.atan2(p[1] - centre[1], p[0] - centre[0]) for p in (bz[0], _bezier_point(bz, .5), bz[3])]
    total = 0.0
    for a, b in zip(angles, angles[1:]):
        d = b - a
        while d <= -math.pi: d += 2 * math.pi
        while d > math.pi: d -= 2 * math.pi
        total += d
    return total


def _on_arc(arc, point):
    if abs(arc['sweep']) >= 2 * math.pi - 1e-3: return True
    a = math.atan2(point[1] - arc['c'][1], point[0] - arc['c'][0]) - arc['start']
    if arc['sweep'] < 0: a = -a
    a %= 2 * math.pi
    return a <= abs(arc['sweep']) + 1e-3


def _foot(p, q, x):
    dx, dy = q[0] - p[0], q[1] - p[1]
    t = ((x[0] - p[0]) * dx + (x[1] - p[1]) * dy) / (dx * dx + dy * dy)
    return t, (p[0] + t * dx, p[1] + t * dy)


def _segment_distance(p, q, x):
    t, f = _foot(p, q, x)
    t = min(1, max(0, t))
    return math.dist(x, (p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])))


def _angle(s, o):
    a = math.atan2(s['q'][1] - s['p'][1], s['q'][0] - s['p'][0]); b = math.atan2(o['q'][1] - o['p'][1], o['q'][0] - o['p'][0])
    d = abs(a - b) % math.pi
    return min(d, math.pi - d)


def _near_label(glyphs, point, reach=4.0):
    best = min(((math.dist(g['at'], point), g['c']) for g in glyphs if g['c'].strip() and g['c'].isalpha()), default=None)
    return best[1] if best and best[0] <= reach else None


def _where(geo, point):
    label = _near_label(geo['glyphs'], point)
    return f'점 {label} 부근' if label else f'({point[0]:.0f}mm, {point[1]:.0f}mm) 부근'


def _figure_circles(geo):
    """Solid circles and wide arcs. Dashed or shallow curves are length marks drawn with bend."""
    return [a for a in geo['arcs'] if a['r'] >= MIN_RADIUS_MM and not a['dashed'] and abs(a['sweep']) >= math.pi / 2]


def tangent_warnings(geo):
    out = []
    for s in geo['segments']:
        if s['symbol'] or math.dist(s['p'], s['q']) < MIN_SEGMENT_MM: continue
        for arc in _figure_circles(geo):
            ends = [abs(math.dist(e, arc['c']) - arc['r']) for e in (s['p'], s['q'])]
            if max(ends) < GAP_MM[0]: continue  # a chord between two points of the circle
            t, foot = _foot(s['p'], s['q'], arc['c'])
            on = [e for e, gap in zip((s['p'], s['q']), ends) if gap < GAP_MM[0]]
            if on and math.dist(on[0], foot) > .1 * arc['r']: continue  # leaves the circle from a point on it: a secant
            if not -.02 <= t <= 1.02 or not _on_arc(arc, foot): continue
            d = math.dist(foot, arc['c']); slip = abs(d - arc['r'])
            # Below GAP_MM[0] a slip does not show in print (a 0.2mm miss was reported in a real run).
            if max(TANGENT_BAND[0] * arc['r'], GAP_MM[0]) < slip < TANGENT_BAND[1] * arc['r']:
                kind = '원을 살짝 파고듭니다' if d < arc['r'] else '원에 닿지 않습니다'
                out.append({'kind': 'tangent', 'at': foot, 'text': f"접선 어긋남: {_where(geo, foot)}의 선이 반지름 {arc['r']:.1f}mm 원에 {slip:.1f}mm 차이로 {kind}. "
                           "접선이면 접점을 계산하세요(예: \\coordinate (T) at (tangent cs:node=c,point={(P)},solution=1); "
                           "또는 원 중심에서 수선의 발). 원과 만나는 선이면 무시하세요."})
    return out


def endpoint_warnings(geo):
    """Solid line ends against solid lines and circles. Dashed lines are dimension and guide
    lines that stop short by design; nearly parallel neighbours are double strokes."""
    out = []
    segs = [s for s in geo['segments'] if not s['symbol'] and math.dist(s['p'], s['q']) >= MIN_SEGMENT_MM]
    others = [s for s in geo['segments'] if not s['dashed']]
    for s in segs:
        for end in (s['p'], s['q']):
            if any(math.dist(end, m) < 1.5 for m in geo['marks']): continue  # arrow tip or point dot
            gaps = []
            for o in others:
                if o is s: continue
                if min(math.dist(end, o['p']), math.dist(end, o['q'])) < GAP_MM[0]: gaps = None; break  # shares a vertex
                if _angle(s, o) < math.radians(20): continue
                # Lines only tell a line end that touches something. Ends near other lines were all drawings
                # (axes, extended lines, towers, taps) in 157 calibration figures and three real runs; slips were all at circles.
                if _segment_distance(o['p'], o['q'], end) < GAP_MM[0]: gaps = None; break  # ends on a line
            if gaps is None: continue
            for arc in _figure_circles(geo):
                if not _on_arc(arc, end) and abs(math.dist(end, arc['c']) - arc['r']) < GAP_MM[1]:
                    continue  # off the drawn part of an arc: not a slip against it
                if abs(math.dist(_foot(s['p'], s['q'], arc['c'])[1], arc['c']) - arc['r']) < GAP_MM[0]:
                    continue  # a tangent drawn a little past its point of contact
                gaps.append((abs(math.dist(end, arc['c']) - arc['r']), '원'))
            if not gaps: continue
            gap, what = min(gaps)
            if GAP_MM[0] <= gap < GAP_MM[1]:
                out.append({'kind': 'endpoint', 'at': end, 'text': f"끝점 어긋남: {_where(geo, end)}에서 선 끝이 {what}과 {gap:.1f}mm 떨어져 있습니다. "
                           "닿아야 하는 점이면 교점·원 위의 점을 계산해 그 이름으로 끝내세요."})
    return out


def circle_warnings(geo):
    """Two whole circles that almost touch: tangent circles (coins, inscribed circles) placed by eye."""
    out = []
    full = [a for a in _figure_circles(geo) if abs(a['sweep']) >= 2 * math.pi - .1]
    for i, a in enumerate(full):
        for b in full[i + 1:]:
            d = math.dist(a['c'], b['c']); small = min(a['r'], b['r'])
            if d < GAP_MM[0]: continue  # concentric
            for gap, kind in ((d - a['r'] - b['r'], '밖에서'), (abs(a['r'] - b['r']) - d, '안에서')):
                if GAP_MM[0] < abs(gap) < CIRCLE_GAP_SHARE * small:
                    w = a['r'] + b['r']
                    at = ((a['c'][0] * b['r'] + b['c'][0] * a['r']) / w, (a['c'][1] * b['r'] + b['c'][1] * a['r']) / w)
                    state = '떨어져' if gap > 0 else '겹쳐'
                    out.append({'kind': 'circles', 'at': at, 'text': f"원 접촉 어긋남: {_where(geo, at)}에서 두 원이 {kind} {abs(gap):.1f}mm {state} 있습니다. "
                               "원본에서 서로 접하면 중심 사이 거리를 반지름의 합(안에서 접하면 차)으로 계산하세요."})
    return out


@_one_at_a_time
def labels_collide(pdf_path, share=0.3):
    """True when glyphs of different labels overlap: geometry was shrunk under its fixed-size labels."""
    import fitz
    boxes = []
    with fitz.open(pdf_path) as doc:
        for block in doc[0].get_text('rawdict')['blocks']:
            for li, line in enumerate(block.get('lines', [])):
                for si, span in enumerate(line['spans']):
                    for ci, ch in enumerate(span['chars']):
                        if ch['c'].strip(): boxes.append(((id(block), li, si), ci, fitz.Rect(ch['bbox'])))
    for i, (sa, ca, a) in enumerate(boxes):
        for sb, cb, b in boxes[i + 1:]:
            if sa == sb and abs(ca - cb) <= 2: continue  # neighbours in one label (primes, sub/superscripts)
            inter = a & b
            if inter.is_empty: continue
            if inter.get_area() > share * min(a.get_area(), b.get_area()): return True
    return False


def geometry_findings(pdf_path):
    geo = extract(pdf_path)
    ends = endpoint_warnings(geo)
    # Slips are isolated; many near-touching ends are an illustration's detail (towers, taps), not construction.
    if len(ends) > MAX_ENDPOINT_FINDINGS: ends = []
    found, seen = [], set()
    for f in tangent_warnings(geo) + ends + circle_warnings(geo):
        if f['text'] not in seen: seen.add(f['text']); found.append(f)
    return found


def geometry_warnings(pdf_path):
    try:
        return [f['text'] for f in geometry_findings(pdf_path)][:6]
    except Exception:
        return []  # a measuring aid; the render itself already succeeded
