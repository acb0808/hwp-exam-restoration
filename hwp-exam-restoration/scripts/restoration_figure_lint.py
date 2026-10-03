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


def circle_point_warnings(tex):
    """Labelled points that sit close to a drawn circle but not on it (a hand-placed intersection)."""
    body = _uncommented(tex); labelled = _labelled(body)
    # A point that is itself the centre of a circle (coins side by side: O, O', O'') is not a point on its neighbour.
    centres = {name for _, _, name, r1, r2 in CIRCLE.findall(body) if name and float(r1 or r2) >= DOT_RADIUS}
    points = {k: v for k, v in _points(body).items() if k in labelled and k not in centres}; warnings = []
    for x, y, name, r1, r2 in CIRCLE.findall(body):
        r = float(r1 or r2)
        if r < DOT_RADIUS: continue
        if name:
            centers = _points(body)
            if name not in centers: continue
            center = centers[name]
        else:
            center = (float(x), float(y))
        for label, (px, py) in points.items():
            if label == name: continue
            gap = abs(math.hypot(px - center[0], py - center[1]) - r)
            if 0.03 * r < gap <= 0.35 * r:
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
        if body.startswith('{', i): found.append(_brace(body, i))
    for m in re.finditer(r'\\ExamLabel\s*(?:\[[^\]]*\])?\s*\{[^{}]*\}\s*(?=\{)', body):
        found.append(_brace(body, m.end()))
    for m in re.finditer(r'\\ExamLengthArc\s*(?:\[[^\]]*\])?\s*\{[^{}]*\}\s*\{[^{}]*\}\s*(?=\{)', body):
        found.append(_brace(body, m.end()))
    found = [cell for x in found for cell in _cells(x)]
    return sorted({normalise_label(x) for x in found} - {''} - ARROWS)


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


def figure_warnings(tex):
    return circle_point_warnings(tex) + label_warnings(tex)
