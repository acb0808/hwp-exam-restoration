"""Check a rendered figure against the position relations its question states (no model calls).

restoration_relations reads "BC의 중점 M" from the question; restoration_labels logs where the named
points of the picture ended up, in printed mm; restoration_figure_geometry measures the circles that
were drawn. A relation whose points are all found is measured, and one that is off by more than the
tolerance is reported with the calc expression that would have placed it.

Points are matched by their TikZ names (Op, OPrime for O'). A point the producer did not name is
taken from the vertex or crossing its printed label clearly sits next to; if two candidates are about
as near, the relation is not measured. For such label-matched points a deviation beyond FAR_SHARE of
the figure's scale is taken to be a wrong match, not a slip, and is left alone.
"""
import math

TOL_MM = 0.3        # below this a slip does not show in print
TOL_SHARE = 0.02    # ... or 2% of the length involved, whichever is larger
FAR_SHARE = 0.45    # label-matched points only: beyond this share of the scale the match is probably wrong
RIVAL_RATIO = 2.0   # a second candidate nearer than twice the nearest makes a label ambiguous (1.5 matched a line end for a crossing)
PRIMES = {"'": ('p', 'prime', 'Prime'), "''": ('pp', 'dprime', 'DoublePrime', 'Pprime', 'PrimePrime')}
MIN_CIRCLE_MM = 2.5
LABEL_REACH_MM = 4.5  # a point with no TikZ name is taken from the vertex its printed label sits next to
MAX_TEXT = 120
# Where a point sits (on a circle, on a line, touching) shows in any source, drawn to scale or not.
# The other kinds are lengths and angles; exam figures are often not to scale, so a render that copies
# the source faithfully may differ from the text there.
POSITION_KINDS = frozenset(('center', 'on_circle', 'diameter', 'tangent', 'tangent_from', 'tangent_side', 'incircle',
                            'intersection', 'on_segment', 'on_extension', 'on_line', 'collinear'))
AS_DRAWN = ' 원본도 그렇게 그려졌으면 그대로 둔다.'


def _sub(a, b): return (a[0] - b[0], a[1] - b[1])


def _line_distance(p, a, b):
    d = math.dist(a, b)
    return abs((b[0] - a[0]) * (a[1] - p[1]) - (a[0] - p[0]) * (b[1] - a[1])) / d if d > 1e-9 else math.dist(p, a)


def _foot(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]; n = dx * dx + dy * dy
    if n < 1e-12: return a
    t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / n
    return (a[0] + t * dx, a[1] + t * dy)


def _meet(a, b, c, d):
    den = (a[0] - b[0]) * (c[1] - d[1]) - (a[1] - b[1]) * (c[0] - d[0])
    if abs(den) < 1e-9: return None
    t = ((a[0] - c[0]) * (c[1] - d[1]) - (a[1] - c[1]) * (c[0] - d[0])) / den
    return (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))


def _cos(a, b, c, d):
    u, v = _sub(b, a), _sub(d, c); n = math.hypot(*u) * math.hypot(*v)
    return (u[0] * v[0] + u[1] * v[1]) / n if n > 1e-9 else 0.0


def _degrees_off_right(a, b, c, d): return abs(90 - math.degrees(math.acos(max(-1, min(1, _cos(a, b, c, d))))))


def _degrees_off_parallel(a, b, c, d):
    t = math.degrees(math.acos(max(-1, min(1, abs(_cos(a, b, c, d))))))
    return t


class Figure:
    """Named points and drawn circles of one rendered figure, in printed mm (y down)."""

    def __init__(self, points, circles, labels=None, vertices=None):
        self.points = dict(points); self.circles = list(circles)
        self.labels = dict(labels or {}); self.vertices = list(vertices or [])

    @classmethod
    def from_pdf(cls, pdf):
        from restoration_labels import labels, logged
        geo, found = labels(pdf)
        _, points = logged(pdf)
        circles = [{'c': a['c'], 'r': a['r'], 'sweep': abs(a['sweep'])} for a in geo['arcs']
                   if a['r'] >= MIN_CIRCLE_MM and abs(a['sweep']) >= math.pi / 2 - 1e-3]
        text = {l['text'].replace('′', "'").replace('″', "''"): ((l['ink'][0] + l['ink'][2]) / 2, (l['ink'][1] + l['ink'][3]) / 2) for l in found.values()}
        segments = [(s['p'], s['q']) for s in geo['segments'] if not s['symbol']]
        vertices = [end for s in segments for end in s] + list(geo['marks']) + [c['c'] for c in circles] + _crossings(segments, geo['arcs'])
        return cls(points, circles, text, vertices)

    def exact(self, name):
        """The TikZ point of that name; O' may be written Op, Oprime or OPrime."""
        if name in self.points: return self.points[name]
        base = name.rstrip("'"); marks = name[len(base):]
        return next((self.points[base + s] for s in PRIMES.get(marks, ()) if base + s in self.points), None)

    def point(self, name):
        """A TikZ point of that name, else the vertex its printed label clearly belongs to."""
        named = self.exact(name)
        if named is not None: return named
        at = self.labels.get(name)
        if at is None: return None
        near = sorted(self.vertices, key=lambda v: math.dist(v, at))
        if not near or math.dist(near[0], at) > LABEL_REACH_MM: return None
        rivals = [v for v in near[1:] if math.dist(v, near[0]) > TOL_MM]
        if rivals and math.dist(rivals[0], at) < RIVAL_RATIO * math.dist(near[0], at): return None  # two candidates about as near: unclear
        return near[0]

    def circle(self, fit, name=None, full=True):
        """The circle a relation is about: the one centred at the named point if drawn, else the best fit."""
        pool = [c for c in self.circles if not full or c['sweep'] >= math.pi - 1e-3] or self.circles
        centre = self.point(name) if name else None
        if centre is not None:
            named = [c for c in self.circles if math.dist(c['c'], centre) <= max(TOL_MM, TOL_SHARE * c['r'])]
            if named: pool = named
        return min(pool, key=fit) if pool else None


def _crossings(segments, arcs):
    """Where drawn strokes cross: a labelled point is often such a crossing, not a line end."""
    out = []
    for i, (a, b) in enumerate(segments):
        for c, d in segments[i + 1:]:
            x = _meet(a, b, c, d)
            if x and all(min(u[k], v[k]) - 1e-6 <= x[k] <= max(u[k], v[k]) + 1e-6 for u, v in ((a, b), (c, d)) for k in (0, 1)): out.append(x)
        for arc in arcs:
            if arc['r'] < MIN_CIRCLE_MM: continue
            foot = _foot(arc['c'], a, b); h = math.dist(foot, arc['c'])
            if h > arc['r']: continue
            half = math.sqrt(arc['r'] ** 2 - h * h); n = math.dist(a, b)
            if n < 1e-9: continue
            ux, uy = (b[0] - a[0]) / n, (b[1] - a[1]) / n
            for sign in (-1, 1):
                x = (foot[0] + sign * half * ux, foot[1] + sign * half * uy)
                on_segment = -1e-6 <= (x[0] - a[0]) * ux + (x[1] - a[1]) * uy <= n + 1e-6
                turn = math.atan2(x[1] - arc['c'][1], x[0] - arc['c'][0]) - arc['start']
                if arc['sweep'] < 0: turn = -turn
                if on_segment and (abs(arc['sweep']) >= 2 * math.pi - 1e-3 or turn % (2 * math.pi) <= abs(arc['sweep']) + 1e-3): out.append(x)
    return out


def _measure(kind, p, fig, circle_name):
    """(deviation mm, scale mm, hint) or None when the relation cannot be measured on this figure."""
    seg = math.dist
    if kind == 'center':
        c = fig.circle(lambda c: seg(c['c'], p[0]), full=False)
        return c and (seg(c['c'], p[0]), c['r'], '원을 그 점에서 그리기: \\draw (O) circle (r);')
    if kind == 'on_circle':
        c = fig.circle(lambda c: abs(seg(c['c'], p[0]) - c['r']), circle_name, full=False)
        return c and (abs(seg(c['c'], p[0]) - c['r']), c['r'], '원 위의 점은 ($(O)+(각도:반지름)$) 또는 name intersections로 놓기')
    if kind == 'diameter':
        mid = ((p[0][0] + p[1][0]) / 2, (p[0][1] + p[1][1]) / 2); half = seg(p[0], p[1]) / 2
        c = fig.circle(lambda c: max(seg(c['c'], mid), abs(c['r'] - half)), circle_name, full=False)
        return c and (max(seg(c['c'], mid), abs(c['r'] - half)), c['r'], '지름의 끝은 ($(O)!-1!(A)$)처럼 중심 반대편에 놓기')
    if kind in ('tangent', 'tangent_side'):
        c = fig.circle(lambda c: abs(_line_distance(c['c'], p[0], p[1]) - c['r']), circle_name, full=False)
        if not c: return None
        dev = abs(_line_distance(c['c'], p[0], p[1]) - c['r'])
        if len(p) > 2: dev = max(dev, seg(p[2], _foot(c['c'], p[0], p[1])))
        return dev, c['r'], '접점 T는 OT가 그 선과 수직이 되게 계산: ($(A)!(O)!(B)$)'
    if kind == 'tangent_from':
        c = fig.circle(lambda c: abs(seg(c['c'], p[1]) - c['r']), full=False)
        if not c: return None
        return max(abs(seg(c['c'], p[1]) - c['r']), abs(_line_distance(c['c'], p[0], p[1]) - c['r'])), c['r'], '접점 T는 OT⊥PT가 되게 계산'
    if kind == 'incircle':
        sides = [(p[0], p[1]), (p[1], p[2]), (p[2], p[0])]
        fit = lambda c: max(abs(_line_distance(c['c'], a, b) - c['r']) for a, b in sides)
        c = fig.circle(fit)
        return c and (fit(c), c['r'], '내접원은 내심에서 한 변까지의 거리를 반지름으로 그리기')
    if kind == 'incenter':
        d = [_line_distance(p[0], a, b) for a, b in ((p[1], p[2]), (p[2], p[3]), (p[3], p[1]))]
        return max(d) - min(d), max(d), '내심은 두 각의 이등분선의 교점으로 계산'
    if kind == 'circumcenter':
        d = [seg(p[0], q) for q in p[1:4]]
        return max(d) - min(d), max(d), '외심은 두 변의 수직이등분선의 교점으로 계산'
    if kind == 'midpoint':
        mid = ((p[1][0] + p[2][0]) / 2, (p[1][1] + p[2][1]) / 2)
        return seg(p[0], mid), seg(p[1], p[2]), '중점은 ($(A)!0.5!(B)$)'
    if kind in ('arc_midpoint', 'on_bisector'):
        ab = seg(p[1], p[2])
        return abs(seg(p[0], p[1]) ** 2 - seg(p[0], p[2]) ** 2) / (2 * ab) if ab > 1e-9 else None, ab, '두 끝점에서 같은 거리에 놓기'
    if kind == 'foot':
        return seg(p[0], _foot(p[1], p[2], p[3])), max(seg(p[2], p[3]), seg(p[1], p[0])), '수선의 발은 ($(B)!(A)!(C)$)'
    if kind == 'intersection':
        x = _meet(p[1], p[2], p[3], p[4])
        return x and (seg(p[0], x), max(seg(p[1], p[2]), seg(p[3], p[4])), '교점은 (intersection of A--B and C--D)')
    if kind in ('on_segment', 'on_extension', 'on_line'):
        return _line_distance(p[0], p[1], p[2]), seg(p[1], p[2]), '선 위의 점은 ($(A)!0.4!(B)$)처럼 계산'
    if kind == 'collinear':
        a, b = max(((x, y) for x in p for y in p), key=lambda q: seg(*q))
        return max(_line_distance(x, a, b) for x in p), seg(a, b), '같은 직선 위의 점은 ($(A)!t!(B)$)로 계산'
    if kind == 'equal_length':
        l1, l2 = seg(p[0], p[1]), seg(p[2], p[3])
        return abs(l1 - l2), max(l1, l2), f'지금 {l1:.1f}mm와 {l2:.1f}mm'
    if kind in ('right_angle', 'perpendicular'):
        a, b, c, d = (p[1], p[0], p[1], p[2]) if kind == 'right_angle' else p
        short = min(seg(a, b), seg(c, d))
        return abs(_cos(a, b, c, d)) * short, short, f'지금 직각에서 {_degrees_off_right(a, b, c, d):.0f}° 어긋남'
    if kind == 'right_triangle':
        best = min((abs(_cos(p[i], p[(i + 1) % 3], p[i], p[(i + 2) % 3])) * min(seg(p[i], p[(i + 1) % 3]), seg(p[i], p[(i + 2) % 3])),
                    min(seg(p[i], p[(i + 1) % 3]), seg(p[i], p[(i + 2) % 3]))) for i in range(3))
        return best[0], best[1], '한 각을 직각으로 그리기'
    if kind == 'parallel':
        short = min(seg(p[0], p[1]), seg(p[2], p[3]))
        return math.sqrt(max(0, 1 - _cos(*p) ** 2)) * short, short, f'지금 {_degrees_off_parallel(*p):.0f}° 벌어짐'
    if kind in ('square', 'rectangle', 'parallelogram', 'rhombus', 'equilateral'):
        n = len(p); sides = [seg(p[i], p[(i + 1) % n]) for i in range(n)]; dev = 0.0
        if kind in ('square', 'rhombus', 'equilateral'): dev = max(dev, max(sides) - min(sides))
        if kind in ('square', 'rectangle'):
            dev = max(dev, max(abs(_cos(p[i], p[i - 1], p[i], p[(i + 1) % 4])) * min(sides[i - 1], sides[i]) for i in range(4)))
        if kind == 'parallelogram':
            dev = seg(_sub(p[1], p[0]), _sub(p[2], p[3]))
        return dev, max(sides), '변의 길이와 각을 계산으로 맞추기'
    return None


NAMES = {  # how a relation is quoted back; no particles, so any point letter reads well
    'center': '원의 중심 {0}', 'on_circle': '원 위의 점 {0}', 'diameter': '지름 {0}{1}', 'tangent': '접선 {0}{1}',
    'tangent_side': '{0}{1}에 접하는 원', 'tangent_from': '접선 {0}{1}', 'incircle': '삼각형 {0}{1}{2}의 내접원',
    'incenter': '내심 {0}', 'circumcenter': '외심 {0}', 'midpoint': '{1}{2}의 중점 {0}', 'arc_midpoint': '호 {1}{2}의 중점 {0}',
    'on_bisector': '{1}{2}의 수직이등분선 위의 점 {0}', 'foot': '{1}에서 {2}{3}에 내린 수선의 발 {0}', 'intersection': '{1}{2}와 {3}{4}의 교점 {0}',
    'on_segment': '{1}{2} 위의 점 {0}', 'on_extension': '{1}{2}의 연장선 위의 점 {0}', 'on_line': '직선 {1}{2} 위의 점 {0}', 'collinear': '한 직선 위의 점 {0}, {1}, {2}',
    'equal_length': '{0}{1}={2}{3}', 'right_angle': '∠{0}{1}{2}=90°', 'right_triangle': '직각삼각형 {0}{1}{2}',
    'perpendicular': '{0}{1}⊥{2}{3}', 'parallel': '{0}{1}∥{2}{3}', 'square': '정사각형 {0}{1}{2}{3}', 'rectangle': '직사각형 {0}{1}{2}{3}',
    'parallelogram': '평행사변형 {0}{1}{2}{3}', 'rhombus': '마름모 {0}{1}{2}{3}', 'equilateral': '정삼각형 {0}{1}{2}',
}


def check(relations, fig):
    """Findings for relations that are measurably off: [{'kind', 'points', 'deviation_mm', 'text'}], worst first."""
    out = []
    for r in relations:
        pts = [fig.point(n) for n in r['points']]
        if any(q is None for q in pts): continue
        try: got = _measure(r['kind'], pts, fig, r.get('circle'))
        except (ValueError, ZeroDivisionError, IndexError): got = None
        if not got or got[0] is None: continue
        dev, scale, hint = got
        if dev <= max(TOL_MM, TOL_SHARE * scale): continue
        if dev > FAR_SHARE * scale and any(fig.exact(n) is None for n in r['points']): continue
        said = NAMES[r['kind']].format(*r['points'])
        text = f'글의 조건 "{said}": 그림에서 {dev:.1f}mm 어긋남. {hint}'
        if r['kind'] not in POSITION_KINDS: text = text[:MAX_TEXT - len(AS_DRAWN)].rstrip('.') + '.' + AS_DRAWN
        out.append({'kind': r['kind'], 'points': r['points'], 'deviation_mm': round(dev, 2), 'text': text[:MAX_TEXT]})
    return sorted(out, key=lambda f: -f['deviation_mm'])


def relation_findings(pdf, relations):
    """Slips of one rendered figure, worst first; empty when nothing is stated, found or off."""
    if not relations: return []
    try: return check(relations, Figure.from_pdf(pdf))
    except Exception: return []  # a measuring aid; the render itself already succeeded


def relation_warnings(pdf, relations, limit=3):
    """Texts of the worst slips of one rendered figure."""
    return [f['text'] for f in relation_findings(pdf, relations)][:limit]
