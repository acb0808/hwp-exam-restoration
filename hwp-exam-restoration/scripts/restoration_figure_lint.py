"""Deterministic TikZ checks for the two things vision models do worst: exact incidence and counting.

Warnings only. They point the producer at a figure; they never pass or fail one.
"""
import math
import re

NUM = r'-?\d+(?:\.\d+)?'
POINT = re.compile(r'\\coordinate\s*\((\w+)\)\s*at\s*\(\s*(' + NUM + r')\s*(,|:)\s*(' + NUM + r')\s*\)')
CIRCLE = re.compile(r'\((?:(' + NUM + r')\s*,\s*(' + NUM + r')|(\w+))\)\s*circle\s*(?:\(\s*(' + NUM + r')\s*(?:cm)?\s*\)'
                    r'|\[[^\]]*?radius\s*=\s*(' + NUM + r')\s*(?:cm)?[^\]]*\])')
DOT_RADIUS = 0.15  # smaller circles are point marks, not curves points lie on
MAX_CIRCLE_WARNINGS = 3
ARROWS = {'Rightarrow', 'rightarrow', 'Leftarrow', 'leftarrow', 'Leftrightarrow', 'implies', 'mapsto', '⇒', '→', '⇨'}  # connectors, not labels
MARKS = {'bullet', 'times', 'circ', 'cdot', 'star', '•', '×', '○', '·'}  # equal-angle marks set as an angle's label, not labels to list
GREEK = {'α': 'alpha', 'β': 'beta', 'γ': 'gamma', 'θ': 'theta', 'π': 'pi', '°': '°'}


def _uncommented(tex):
    return re.sub(r'(?<!\\)%[^\n]*', '', tex)


def _points(tex):
    points = {}
    for name, a, sep, b in POINT.findall(tex):
        a, b = float(a), float(b)
        points[name] = (b * math.cos(math.radians(a)), b * math.sin(math.radians(a))) if sep == ':' else (a, b)
    return points


def _labelled(body):
    """Points that carry a printed label: construction points (e.g. where a fold meets a tangent edge)
    may legitimately sit near a circle, printed points such as P and Q are the ones that must be exact."""
    names = set(re.findall(r'node\s*(?:\[[^\]]*\])?\s*at\s*\((\w+)\)', body))
    names |= set(re.findall(r'\\ExamLabel\s*(?:\[[^\]]*\])?\s*\{(\w+)\}', body))
    names |= set(re.findall(r'\((\w+)\)\s*node\b', body))
    return names


def _segments(body, points):
    """Straight pieces drawn between two points given as numbers: [(name, name)]."""
    found = []
    for path in re.findall(r'\\(?:draw|path|filldraw|fill)\b[^;]*;', body):
        pieces = path.split('--'); names = []
        for i, piece in enumerate(pieces):
            m = re.search(r'\((\w+)\)[^()]*$', piece) if i == 0 else re.match(r'[^()]*\((\w+)\)', piece)
            names.append(m.group(1) if m and m.group(1) in points else 'cycle' if i and re.match(r'\s*cycle\b', piece) else None)
        if names and names[-1] == 'cycle': names[-1] = names[0]
        found += [(a, b) for a, b in zip(names, names[1:]) if a and b and a != b]
    return found


def _line_gap(a, b, c):
    """Distance from c to the line through a and b."""
    return abs((b[0] - a[0]) * (a[1] - c[1]) - (a[0] - c[0]) * (b[1] - a[1])) / math.hypot(b[0] - a[0], b[1] - a[1])


def circle_point_warnings(tex):
    """Labelled points that sit close to a drawn circle but not on it (a hand-placed intersection).
    A point is left alone where the figure itself says why it is off this circle: it lies on another circle
    (concentric circles), or it ends a line drawn tangent to this one (the corner of a shape around an
    inscribed circle). These two made all 63 warnings of ten runs of one exam, none of them acted on."""
    body = _uncommented(tex); labelled = _labelled(body)
    # A point that is itself the centre of a circle (coins side by side: O, O', O'') is not a point on its neighbour.
    centres = {name for _, _, name, r1, r2 in CIRCLE.findall(body) if name and float(r1 or r2) >= DOT_RADIUS}
    every = _points(body); points = {k: v for k, v in every.items() if k in labelled and k not in centres}; warnings = []
    circles = []
    for x, y, name, r1, r2 in CIRCLE.findall(body):
        r = float(r1 or r2)
        if r < DOT_RADIUS or (name and name not in every): continue
        circles.append((name, every[name] if name else (float(x), float(y)), r))
    segments = _segments(body, every)
    for n, (name, center, r) in enumerate(circles):
        for label, (px, py) in points.items():
            if label == name: continue
            gap = abs(math.hypot(px - center[0], py - center[1]) - r)
            if 0.03 * r < gap <= 0.35 * r:
                if any(k != n and abs(math.hypot(px - c[0], py - c[1]) - rr) <= 0.03 * rr for k, (_, c, rr) in enumerate(circles)): continue
                if any(label in s and abs(_line_gap(every[s[0]], every[s[1]], center) - r) <= 0.05 * r for s in segments): continue
                warnings.append(f'({label}) is {gap:.2f} off the circle at ({center[0]:g},{center[1]:g}) r={r:g}; '
                                'if it lies on the circle, compute it (name intersections, polar or calc) instead of estimating')
    return list(dict.fromkeys(warnings))[:MAX_CIRCLE_WARNINGS]


def _brace(text, start):
    depth = 0
    for i in range(start, len(text)):
        depth += {'{': 1, '}': -1}.get(text[i], 0)
        if depth == 0: return text[start + 1:i]
    return ''


def normalise_label(text):
    # A degree sign is part of how a number is printed, not a different label: 60 and 60° match.
    s = re.sub(r'\^\s*\{?\s*\\circ\s*\}?|\\degree|°|(?<=\d)\s*deg\b', '', text)
    s = re.sub(r'\\(?:small|footnotesize|scriptsize|tiny|large|Large|normalsize|bfseries|itshape|mathrm|textrm|text|mathit)\b', '', s)
    s = re.sub(r'[\s${}]', '', s).replace('\\', '')
    for glyph, name in GREEK.items(): s = s.replace(glyph, name)
    s = s.replace('sqrt', '√')  # the header is written from the print (√10), the node in TeX (\sqrt{10})
    return s


def drawn_labels(tex):
    """Text printed by nodes, \\ExamLabel and \\ExamLengthArc, normalised."""
    body = _uncommented(tex); found = []
    for m in re.finditer(r'node\s*(?:\[[^\]]*\])?\s*(?:\([^)]*\))?\s*', body):
        i = m.end()
        if re.match(r'at\s*\(', body[i:]):
            # The position may nest parentheses: at ($(A)+(0.55,0.25)$)
            i = body.index('(', i); depth = 0
            for j in range(i, len(body)):
                depth += {'(': 1, ')': -1}.get(body[j], 0)
                if depth == 0: break
            i = j + 1
            while i < len(body) and body[i].isspace(): i += 1
            if body.startswith('[', i):  # options written after the position: node at (P) [above left] {$2$}
                i = body.find(']', i) + 1 or len(body)
                while i < len(body) and body[i].isspace(): i += 1
        if body.startswith('{', i): found.append(_brace(body, i))
    for m in re.finditer(r'\\ExamLabel\s*(?:\[[^\]]*\])?\s*\{[^{}]*\}\s*(?=\{)', body):
        found.append(_brace(body, m.end()))
    for m in re.finditer(r'\\ExamLengthArc\s*(?:\[[^\]]*\])?\s*\{[^{}]*\}\s*\{[^{}]*\}\s*(?=\{)', body):
        found.append(_brace(body, m.end()))
    for m in re.finditer(r'\\ExamAngle\s*(?:\[[^\]]*\])?\s*\{[^{}]*\}\s*\{[^{}]*\}\s*\{[^{}]*\}\s*(?=\{)', body):
        found.append(_brace(body, m.end()))
    found += re.findall(r'\\pic\s*\[[^\]]*?"([^"]*)"', body)  # \pic[draw, "$x$", ...] {angle=A--B--C}
    found = [cell for x in found for cell in _cells(x)]
    return sorted({normalise_label(x) for x in found} - {''} - ARROWS - MARKS)


def _cells(text):
    """A node holding a table prints one label per cell."""
    if '\\begin{tabular}' not in text: return [text]
    s = re.sub(r'\\begin\{tabular\}\s*(?:\[[^\]]*\])?\s*\{[^{}]*\}|\\end\{tabular\}|\\hline|\\cline\{[^{}]*\}', ' ', text)
    return re.split(r'&|\\\\', s)


def declared_labels(tex):
    """Labels the producer listed from the source before drawing: % labels=A,B,P,8,60°,[그림1]"""
    for line in tex.splitlines()[:5]:
        m = re.match(r'\s*%.*?\blabels\s*=\s*(.+)$', line)
        if m: return sorted({normalise_label(x) for x in m.group(1).split(',')} - {''})
    return None


def label_warnings(tex):
    declared = declared_labels(tex)
    if declared is None: return []
    drawn = set(drawn_labels(tex)); declared = set(declared)
    missing, extra = sorted(declared - drawn), sorted(drawn - declared)
    out = []
    if missing: out.append('labels listed from the source but not drawn: ' + ', '.join(missing))
    if extra: out.append('labels drawn but not listed from the source: ' + ', '.join(extra))
    return out


ANGLE_ARC_RADIUS = 1.0  # hand-drawn arcs below this radius were angle marks in 259 archived arc lines; larger ones are figure curves
HAND_ARC = re.compile(r'\barc\s*(?:\(\s*[^:()]+:[^:()]+:\s*(' + NUM + r')\s*(mm|cm)?\s*\)'
                      r'|\[[^\]]*?radius\s*=\s*(' + NUM + r')\s*(mm|cm)?[^\]]*\])')


def angle_arc_warnings(tex):
    """Angle marks drawn by hand as small arcs: start and end angles computed by the producer put some of them
    on the wrong side or across a line in real runs. Advice, said once per figure."""
    body = _uncommented(tex); count = 0
    for r1, u1, r2, u2 in HAND_ARC.findall(body):
        radius = float(r1 or r2) / (10 if (u1 or u2) == 'mm' else 1)
        count += radius < ANGLE_ARC_RADIUS
    if not count: return []
    return [f'각 표시 호 {count}곳을 시작·끝 각도로 직접 그렸습니다. 렌더에서 호가 다른 각에 걸치거나 라벨이 선에 겹치면 '
            r'\ExamAngle{A}{B}{C}{라벨}(B가 꼭짓점, 겹호는 [double])로 바꾸세요. 항상 작은 쪽 각에 그려집니다. 원본과 같게 보이면 그대로 둡니다.']


def figure_warnings(tex):
    return circle_point_warnings(tex) + label_warnings(tex) + angle_arc_warnings(tex)
