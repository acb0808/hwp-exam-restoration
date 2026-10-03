"""Position relations a question states about its figure, read from the question text (no model calls).

"BC의 중점 M", "원 O 위의 점 P", "A에서 BC에 내린 수선의 발 H": such givens fix where points lie,
whatever the drawing's scale. They are parsed from the stem only. The <보기> box, the choices and
the answer are statements to judge, not givens, and are skipped. Sizes (angles, lengths, ratios) are
left out: exam figures are often not to scale, positions are.

A relation is {'kind', 'points', 'circle', 'text'}; points are in the order given under KINDS.
"""
import re

KINDS = {
    'center': 'O: a circle is centred at O',
    'on_circle': 'P: on the circle (named in circle, or the only one that fits)',
    'diameter': 'A B: AB is a diameter',
    'tangent': 'A B [T]: line AB touches the circle, at T when named',
    'tangent_from': 'P T: PT touches a circle at T',
    'incircle': 'A B C: a circle touches the three sides',
    'tangent_side': 'A B: a circle touches side AB',
    'incenter': 'I A B C', 'circumcenter': 'O A B C',
    'midpoint': 'M A B', 'arc_midpoint': 'M A B: on the circle, equally far from A and B',
    'foot': 'H A B C: foot of the perpendicular from A to line BC',
    'intersection': 'X A B C D: lines AB and CD meet at X',
    'on_segment': 'X A B', 'on_extension': 'X A B: on line AB outside the segment',
    'on_line': 'X A B: where something meets line AB', 'collinear': 'A B C',
    'on_bisector': 'P A B: on the perpendicular bisector of AB',
    'equal_length': 'A B C D: AB = CD',
    'right_angle': 'A B C: the angle at B', 'right_triangle': 'A B C: one of its angles',
    'perpendicular': 'A B C D', 'parallel': 'A B C D',
    'square': 'A B C D', 'rectangle': 'A B C D', 'parallelogram': 'A B C D', 'rhombus': 'A B C D',
    'equilateral': 'A B C',
}

P = r"[A-Z](?:''|')?"
SEG = rf"(?<![A-Z'])(?:{P}){{2}}(?![A-Z'])"
PLIST = rf"{P}(?:\s*(?:,|와|과)\s*{P})*"
SEGLIST = rf"{SEG}(?:\s*(?:,|와|과)\s*(?:선분|변|현)?\s*{SEG})*"
LINE = r"(?:선분|변|현|직선|반직선|대각선)?\s*"
LEXT = rf"{LINE}({SEG})(?:의\s*연장선)?"  # a line named by a segment or by its extension
CIRCLE = r"(?:반원|사분원|원)"
COUNT = r"(?:한|두|세|네|다섯|여섯)?\s*"
EACH = r"(?:각각\s*)?"
MEET = rf"만나는\s*{COUNT}점"
CLAUSE = r"(?:[^.?,]|(?<=[A-Z']),\s*(?=[A-Z]))"  # stays inside one clause; commas only between point names
TANGENT_AT = rf"점\s*({P})(?:[을를]\s*접점으로\s*하는|에서의|에서\s*그은)\s*접선"
OBJ = r"[을를]"; TOPIC = r"[은는이가]"; AND = r"[와과]"


def question_stem(question):
    """The givens of one reading.md question: fenced blocks (보기, choices, answer, tables) and figure lines removed."""
    text = re.sub(r'(?ms)^:::.*?^:::\s*$', ' ', question)
    text = re.sub(r'(?m)^!\[[^\]]*\]\([^)]*\)\s*$', ' ', text)
    return re.sub(r'(?m)^#.*$', ' ', text)  # column and section markers


def normalise(text):
    """LaTeX to the plain wording the patterns read: \\overline{AB} -> AB, \\triangle -> 삼각형, O^{\\prime} -> O'."""
    s = text
    for _ in range(3):  # \overline{\mathrm{AB}} nests
        s = re.sub(r'\\(?:overline|overleftrightarrow|overrightarrow|mathrm|mathit|mathbf|text|textrm)\s*\{([^{}]*)\}', r'\1', s)
    s = re.sub(r'\\(?:widehat|wideparen|overarc)\s*\{([^{}]*)\}', r'호 \1', s)
    s = re.sub(r'\\(?:overset|stackrel)\s*\{\s*\\frown\s*\}\s*\{([^{}]*)\}', r'호 \1', s)
    s = re.sub(r'\^\s*\{?\s*\\prime\\prime\s*\}?|″', "''", s)
    s = re.sub(r'\^\s*\{?\s*\\prime\s*\}?|′', "'", s)
    s = re.sub(r'\^\s*\{?\s*\\circ\s*\}?', '°', s)
    for tex, word in ((r'\\triangle', '삼각형 '), (r'\\square', '사각형 '), (r'\\angle', '∠'), (r'\\perp', '⊥'),
                      (r'\\parallel|//|\\/\\/|∥', '∥'), (r'\\equiv', '≡'), (r'△', '삼각형 '), (r'□', '사각형 ')):
        s = re.sub(tex, word, s)
    s = re.sub(r'\\[,;:! ]|[${}]', ' ', s)
    s = re.sub(r"([A-Z])\s+(?=''?(?![A-Za-z]))", r'\1', s)  # O ' -> O'
    s = re.sub(r'\s+', ' ', s)
    # "$O$를", "$P$, $Q$는" leave gaps where the dollars were: close them so particles and commas sit on the letters.
    s = re.sub(r"(?<=[A-Z'])\s+(?=[가-힣,])", '', s)
    return re.sub(r'\s+,', ',', s)


def _points(text): return re.findall(P, text)


def _segments(text): return [_points(m) for m in re.findall(SEG, text)]


def key(relation):
    """Order-free identity of a relation, for de-duplication and for scoring against hand labels."""
    kind, p = relation['kind'], relation['points']
    pair = lambda a, b: '-'.join(sorted((a, b)))
    if kind in ('center', 'on_circle'): rest = p[0]
    elif kind in ('diameter', 'tangent_side'): rest = pair(*p[:2])
    elif kind == 'tangent': rest = pair(*p[:2])
    elif kind == 'tangent_from': rest = f'{p[0]} {p[1]}'
    elif kind in ('incircle', 'right_triangle', 'equilateral', 'square', 'rectangle', 'parallelogram', 'rhombus'): rest = ''.join(sorted(p))
    elif kind in ('incenter', 'circumcenter'): rest = f"{p[0]} {''.join(sorted(p[1:]))}"
    elif kind in ('midpoint', 'arc_midpoint', 'on_segment', 'on_extension', 'on_line', 'on_bisector'): rest = f'{p[0]} {pair(*p[1:3])}'
    elif kind == 'collinear': rest = ''.join(sorted(p))
    elif kind == 'foot': rest = f'{p[0]} {p[1]} {pair(*p[2:4])}'
    elif kind == 'intersection': rest = f"{p[0]} {' '.join(sorted((pair(*p[1:3]), pair(*p[3:5]))))}"
    elif kind in ('equal_length', 'perpendicular', 'parallel'): rest = ' '.join(sorted((pair(*p[:2]), pair(*p[2:4]))))
    elif kind == 'right_angle': rest = f'{p[1]} {pair(p[0], p[2])}'
    else: rest = ' '.join(p)
    return f'{kind} {rest}'


def relations(question):
    """Relations stated in one question (reading.md text of that question, or a bare stem)."""
    s = normalise(question_stem(question)); found = []; seen = set()
    arcs = []  # spans read as arcs: "호 AB의 중점", "호 PQ 위의 점" are not about the chord AB

    def on_arc(match): return any(a < match.end() and match.start() < b for a, b in arcs)

    def add(kind, points, match, circle=None):
        distinct = points[:2] if kind == 'tangent' else points  # a tangent's contact point may be one of its ends
        if len(set(distinct)) != len(distinct) and kind not in ('equal_length', 'perpendicular', 'parallel', 'intersection'): return
        r = {'kind': kind, 'points': list(points), 'circle': circle, 'text': match.group(0).strip()[:60]}
        k = key(r)
        if k not in seen: seen.add(k); found.append(r)

    def each(pattern): return re.finditer(pattern, s)

    # --- circles and what lies on them
    for m in each(rf"점\s*({P}){OBJ}\s*중심으로\s*하[는고]"): add('center', [m[1]], m)
    for m in each(rf"(?<![가-힣0-9]){CIRCLE}\s*({P}(?:\s*,\s*{P})*)(?![A-Z'])"):
        for p in _points(m[1]): add('center', [p], m)  # 원 O / 두 원 O, O'
    for m in each(rf"중심이\s*(?:점\s*)?({P})(?:인|로\s*같은)\s*{COUNT}{CIRCLE}"): add('center', [m[1]], m)
    for m in each(rf"중심{OBJ}\s*(?:각각\s*)?({PLIST})\s*(?:이?라|로)"):
        for p in _points(m[1]): add('center', [p], m)
    for m in each(rf"{CIRCLE}\s*({P})?\s*위의\s*{COUNT}점\s*({PLIST})"):
        for p in _points(m[2]): add('on_circle', [p], m, m[1])
    for m in each(rf"{CIRCLE}(?:의)?\s*(?:둘레|호)?\s*위(?:에|의)\s*(?:있는\s*)?{COUNT}점\s*({PLIST})"):
        for p in _points(m[1]): add('on_circle', [p], m)
    for m in each(rf"(?:내접원|외접원)\s*({P})(?![A-Z'])"): add('center', [m[1]], m)
    for m in each(rf"{COUNT}점\s*({PLIST}){TOPIC}\s*(?:한|큰|작은)?\s*{CIRCLE}\s*({P})?\s*위(?:의|에)?\s*(?:점|있)"):
        for p in _points(m[1]): add('on_circle', [p], m, m[2])
    for m in each(rf"원이\s*{COUNT}점\s*({PLIST})에서\s*만[나난날]"):  # 만나고 / 만난다 / 만날 때
        for p in _points(m[1]): add('on_circle', [p], m)
    for m in each(rf"(?:꼭짓점|점)\s*({PLIST}){OBJ}\s*지나는\s*{CIRCLE}(?:\s*({P})(?![A-Z']))?"):
        for p in _points(m[1]): add('on_circle', [p], m, m[2])
    for m in each(rf"점\s*({PLIST}){TOPIC}\s*(?:{CIRCLE}|내접원|외접원)\s*({P})?\s*{AND}\s*{MEET}"):
        for p in _points(m[1]): add('on_circle', [p], m, m[2])
    for m in each(rf"점\s*({PLIST}){TOPIC}\s*(?:[^.?]{{0,16}}?의\s*)?(?:그\s*)?접점"):
        for p in _points(m[1]): add('on_circle', [p], m)
    for m in list(each(rf"점\s*({P}){TOPIC}\s*호\s*{SEG}\s*위에")) + list(each(rf"점\s*({P}){OBJ}\s*호\s*{SEG}\s*위에\s*(?:잡|놓|찍|택)")):
        arcs.append(m.span()); add('on_circle', [m[1]], m)
    for m in each(rf"{CIRCLE}\s*({P})?\s*{AND}\s*{LINE}({SEGLIST}){TOPIC}\s*{MEET}{OBJ}\s*{EACH}({PLIST})"):
        for seg, p in zip(_segments(m[2]), _points(m[3])): add('on_circle', [p], m, m[1]); add('on_line', [p] + seg, m)
    for m in each(rf"호\s*{SEG}\s*위의\s*{COUNT}점\s*({PLIST})"):
        arcs.append(m.span())
        for p in _points(m[1]): add('on_circle', [p], m)
    for m in each(rf"{CIRCLE}\s*({P})?\s*에\s*내접하는\s*[가-힣]*형\s*((?:{P}){{3,6}})"):
        for p in _points(m[2]): add('on_circle', [p], m, m[1])
    for m in each(rf"((?:[가-힣]*형\s*(?:{P}){{3,6}}\s*{AND}?\s*)+){TOPIC}\s*{CIRCLE}\s*({P})?\s*에\s*내접"):
        for p in _points(m[1]): add('on_circle', [p], m, m[2])  # 삼각형 ABC가 원 O에 내접
    for m in each(rf"호\s*({SEGLIST})"):
        for a, b in _segments(m[1]): add('on_circle', [a], m); add('on_circle', [b], m)
    for m in each(rf"호\s*{SEG}{AND}\s*{MEET}{OBJ}\s*(?:점\s*)?({P})(?![A-Z'])"): add('on_circle', [m[1]], m)
    for m in each(rf"점\s*({PLIST}){TOPIC}\s*{COUNT}{CIRCLE}의\s*교점"):
        for p in _points(m[1]): add('on_circle', [p], m)
    for m in each(rf"{CIRCLE}과의\s*교점{OBJ}\s*(?:점\s*)?({P})(?![A-Z'])"): add('on_circle', [m[1]], m)
    for m in each(rf"원이\s*점\s*({P})에서\s*(?:만[나난날]|접[하한할])"): add('on_circle', [m[1]], m)
    for m in each(rf"삼각형\s*((?:{P}){{3}})의\s*외접원"):
        for p in _points(m[1]): add('on_circle', [p], m)
    for m in each(rf"(?:{CIRCLE}\s*({P})의\s*)?{COUNT}현\s*({SEGLIST})"):
        for a, b in _segments(m[2]): add('on_circle', [a], m, m[1]); add('on_circle', [b], m, m[1])
    for m in each(rf"{CIRCLE}{AND}\s*{MEET}{OBJ}\s*{EACH}({PLIST})"):
        for p in _points(m[1]): add('on_circle', [p], m)
    for m in each(rf"({SEG}){OBJ}\s*지름으로\s*하는(?:\s*{CIRCLE}\s*({P})(?![A-Z']))?"): add('diameter', _points(m[1]), m, m[2])
    for m in each(rf"(?<!반)지름인?\s*({SEG})"): add('diameter', _points(m[1]), m)
    centres = {r['points'][0] for r in found if r['kind'] == 'center'}
    for m in each(rf"반지름\s*({SEG})"):
        for end in set(_points(m[1])) - centres:
            if len(set(_points(m[1])) & centres) == 1: add('on_circle', [end], m)  # 반지름 OC: C is on the circle
    for m in each(rf"({SEG}){TOPIC}\s*(?:{CIRCLE}\s*({P})?\s*의\s*)?지름"): add('diameter', _points(m[1]), m, m[2])

    # --- tangents and inscribed circles
    for m in each(rf"({SEG}){TOPIC}\s*{CIRCLE}\s*({P})?\s*의\s*접선([^.?]{{0,24}}?점\s*({P}){TOPIC}\s*(?:그\s*)?접점)?"):
        add('tangent', _points(m[1]) + ([m[4]] if m[4] else []), m, m[2])
    for m in each(rf"({SEG}){TOPIC}\s*(?:점\s*({P})에서\s*접하는\s*)?{CLAUSE}{{0,20}}?접선이"):
        add('tangent', _points(m[1]) + ([m[2]] if m[2] else []), m)
    for m in each(rf"({SEG}){TOPIC}\s*점\s*({P})에서\s*[^.?,]{{0,12}}?{CIRCLE}\s*({P})?\s*(?:{AND}|에)\s*접(?:한다|하고|하며|할)"):
        add('tangent', _points(m[1]) + [m[2]], m, m[3])
    for m in each(rf"({SEGLIST}){TOPIC}\s*{COUNT}{CIRCLE}\s*({P})?\s*(?:{AND}|에)\s*접(?:한다|하고|하며|할)"):
        for seg in _segments(m[1]): add('tangent', seg, m, m[2])
    for m in each(rf"({SEGLIST}){TOPIC}\s*각각\s*점\s*({PLIST})에서\s*{CIRCLE}\s*({P})?\s*(?:{AND}|에)\s*접"):
        for seg, touch in zip(_segments(m[1]), _points(m[2])): add('tangent', seg + [touch], m, m[3])
    for m in each(rf"{CIRCLE}\s*({P})?{TOPIC}\s*(?:점\s*({P})에서\s*)?[^.?,]{{0,16}}?{LINE}({SEG})에\s*접"):
        add('tangent', _points(m[3]) + ([m[2]] if m[2] else []), m, m[1])
    for m in each(rf"{TANGENT_AT}(?:{AND}\s*{MEET}{OBJ}|{TOPIC}\s*{MEET}{OBJ}|의\s*교점(?:{OBJ}|이|은)?)\s*(?:점\s*)?({P})(?![A-Z'])"):
        add('tangent', [m[1], m[2], m[1]], m)
    for m in each(rf"{CIRCLE}\s*({P})?{TOPIC}\s*[가-힣]*형\s*((?:{P}){{3,4}})에\s*내접"):
        # a circle inside a polygon touches every side
        v = _points(m[2])
        if len(v) == 3: add('incircle', v, m, m[1])
        else:
            for a, b in zip(v, v[1:] + v[:1]): add('tangent_side', [a, b], m)
    for m in each(rf"점\s*({P})에서\s*[^.?]{{0,24}}?그은\s*{COUNT}접선[^.?]{{0,14}}?접점{OBJ}\s*{EACH}({PLIST})"):
        for t in _points(m[2]): add('tangent_from', [m[1], t], m)
    for m in each(rf"삼각형\s*((?:{P}){{3}})의\s*내접원(?:\s*({P})(?![A-Z']))?"): add('incircle', _points(m[1]), m, m[2])
    for m in each(rf"삼각형\s*((?:{P}){{3}})에서[^.?]{{0,30}}?내접원(?:\s*({P})(?![A-Z']))?"): add('incircle', _points(m[1]), m, m[2])
    for m in each(rf"{LINE}({SEGLIST})(?:{AND}|에)\s*접하는\s*{CIRCLE}"):
        for a, b in _segments(m[1]): add('tangent_side', [a, b], m)
    for m in each(rf"점\s*({P}),\s*({P}){TOPIC}\s*각각\s*삼각형\s*((?:{P}){{3}})의\s*(외심|내심){AND}\s*(외심|내심)"):
        for point, word in ((m[1], m[4]), (m[2], m[5])): add('circumcenter' if word == '외심' else 'incenter', [point] + _points(m[3]), m)
    for word, kind in (('내심', 'incenter'), ('외심', 'circumcenter')):
        for m in each(rf"삼각형\s*((?:{P}){{3}})의\s*{word}(?:[을이은는인]|이라)?\s*(?:점\s*)?({P})(?![A-Z'])"): add(kind, [m[2]] + _points(m[1]), m)
        for m in each(rf"점\s*({P}){TOPIC}\s*삼각형\s*((?:{P}){{3}})의\s*{word}"): add(kind, [m[1]] + _points(m[2]), m)

    triangles = [(q.start(), _points(q[1])) for q in each(rf"삼각형\s*((?:{P}){{3}})(?![A-Z'])")]
    for word, kind in (('내심', 'incenter'), ('외심', 'circumcenter')):
        for m in list(each(rf"{word}\s*({P})(?![A-Z'])")) + list(each(rf"점\s*({P}){TOPIC}\s*{word}")):
            before = [tri for at, tri in triangles if at < m.start()]
            if before and m[1] not in before[-1]: add(kind, [m[1]] + before[-1], m)

    # --- points on lines
    for m in each(rf"호\s*({SEGLIST})의\s*중점{OBJ}?\s*{EACH}(?:점\s*)?({PLIST})"):
        arcs.append(m.span())
        for (a, b), p in zip(_segments(m[1]), _points(m[2])): add('arc_midpoint', [p, a, b], m)
    for m in each(rf"{LINE}({SEGLIST})의\s*중점(?:{OBJ}|이|인)?\s*{EACH}(?:점\s*)?({PLIST})"):
        if on_arc(m): continue
        for (a, b), p in zip(_segments(m[1]), _points(m[2])): add('midpoint', [p, a, b], m)
    for m in each(rf"(?<![A-Z'])({PLIST}){TOPIC}\s*{EACH}{LINE}({SEGLIST})의\s*중점"):
        if on_arc(m) or re.search(r'호\s*$', s[:m.start(2)]): continue
        for point, seg in zip(_points(m[1]), _segments(m[2])): add('midpoint', [point] + seg, m)  # E, F는 각각 AD, CD의 중점
    for m in each(rf"{LINE}({SEGLIST})의\s*중점{TOPIC}\s*{EACH}(?:점\s*)?({PLIST})"):
        if not on_arc(m) and not re.search(r'호\s*$', s[:m.start(1)]):
            for seg, point in zip(_segments(m[1]), _points(m[2])): add('midpoint', [point] + seg, m)  # BC와 AC의 중점은 각각 D, E
    for m in each(rf"(?:꼭짓점|점)\s*({P})에서\s*{LINE}({SEGLIST})에\s*내린\s*수선의\s*발{OBJ}\s*{EACH}(?:점\s*)?({PLIST})"):
        for seg, foot in zip(_segments(m[2]), _points(m[3])): add('foot', [foot, m[1]] + seg, m)  # A에서 BC, CD에 내린 수선의 발 E, F
    for m in each(rf"({P}){OBJ}\s*지나고\s*{LINE}({SEG})에\s*평행한\s*직선{TOPIC}\s*{LINE}{SEGLIST}{AND}\s*{MEET}{OBJ}\s*{EACH}({P}),\s*({P})(?![A-Z'])"):
        add('parallel', [m[3], m[4]] + _points(m[2]), m); add('collinear', [m[3], m[1], m[4]], m)
    for m in each(rf"{LINE}({SEG})의\s*수직이등분선{TOPIC}\s*[^.?]{{0,24}}?{MEET}{OBJ}\s*{EACH}({PLIST})"):
        for point in _points(m[2]): add('on_bisector', [point] + _points(m[1]), m)
    for m in each(rf"{LINE}({SEG})의\s*삼등분점{OBJ}\s*{EACH}({PLIST})"):
        for point in _points(m[2]): add('on_segment', [point] + _points(m[1]), m)
    for m in each(rf"{LINE}({SEG}){AND}의\s*교점{OBJ}\s*(?:점\s*)?({P})(?![A-Z'])"): add('on_line', [m[2]] + _points(m[1]), m)
    for m in each(rf"(?:직선|반직선)\s*({SEG}){TOPIC}\s*[^.?]{{0,20}}?{MEET}{OBJ}\s*(?:점\s*)?({P})(?![A-Z'])"): add('on_line', [m[2]] + _points(m[1]), m)
    for m in each(rf"{LINE}({SEG}){TOPIC}\s*{CIRCLE}\s*({P})의\s*중심{OBJ}\s*지[나난날]"): add('on_line', [m[2]] + _points(m[1]), m)
    for m in each(rf"(?:꼭짓점|점)\s*({PLIST})에서\s*{LINE}({SEG})(?:\s*또는\s*그\s*연장선)?(?:의\s*연장선)?에\s*내린\s*수선의\s*발{OBJ}\s*{EACH}(?:점\s*)?({PLIST})"):
        for a, h in zip(_points(m[1]), _points(m[3])): add('foot', [h, a] + _points(m[2]), m)
    for m in each(rf"{LEXT}(?:{AND}|,)\s*{LEXT}의\s*교점(?:{OBJ}|은|이)?\s*(?:점\s*)?({P})(?![A-Z'])"): add('intersection', [m[3]] + _points(m[1]) + _points(m[2]), m)
    for m in each(rf"점\s*({P}){TOPIC}\s*{COUNT}{LEXT}(?:{AND}|,)\s*{LEXT}의\s*교점"): add('intersection', [m[1]] + _points(m[2]) + _points(m[3]), m)
    for m in each(rf"점\s*({PLIST}){TOPIC}\s*{EACH}{LINE}({SEGLIST})의\s*연장선{AND}\s*{LINE}({SEG})의\s*교점"):
        for point, seg in zip(_points(m[1]), _segments(m[2])): add('intersection', [point] + seg + _points(m[3]), m)
    quads = [(q.start(), _points(q[1])) for q in each(rf"(?:[가-힣]*형|마름모|사다리꼴)\s*((?:{P}){{4}})(?![A-Z'])")]
    for m in list(each(rf"대각선의\s*교점(?:{OBJ}|이|은)?\s*(?:점\s*)?({P})(?![A-Z'])")) + list(each(rf"점\s*({P}){TOPIC}\s*[^.?]{{0,16}}?대각선의\s*교점")):
        # The diagonals are those of the quadrilateral named in the phrase, else of the last one named before it.
        inside = [q for at, q in quads if m.start() <= at < m.end()]; before = [q for at, q in quads if at < m.start()]
        q = (inside or before[-1:] or [None])[0]
        if q and m[1] not in q: add('intersection', [m[1], q[0], q[2], q[1], q[3]], m)
    for m in each(rf"(?:이등분선|수선|직선|접선|선){AND}\s*{LINE}({SEG}){TOPIC}\s*{MEET}{OBJ}\s*(?:점\s*)?({P})(?![A-Z'])"): add('on_line', [m[2]] + _points(m[1]), m)
    for m in each(rf"(?:이등분선|수선|직선|접선|선){TOPIC}\s*{LINE}({SEGLIST}){AND}\s*{MEET}{OBJ}\s*{EACH}(?:점\s*)?({PLIST})"):
        for seg, point in zip(_segments(m[1]), _points(m[2])): add('on_line', [point] + seg, m)
    for m in each(rf"{LINE}({SEG})의\s*연장선(?:{TOPIC}|{AND})\s*{TANGENT_AT}(?:{AND}\s*{MEET}{OBJ}|{TOPIC}\s*{MEET}{OBJ}|의\s*교점(?:{OBJ}|이|은)?)\s*(?:점\s*)?({P})(?![A-Z'])"):
        add('on_extension', [m[3]] + _points(m[1]), m)
    for m in each(rf"{LINE}({SEG})의\s*연장선{TOPIC}\s*[^.?]{{0,30}}?{MEET}{OBJ}\s*(?:점\s*)?({P})(?![A-Z'])"): add('on_extension', [m[2]] + _points(m[1]), m)
    for m in each(rf"{LINE}({SEG})의\s*수직이등분선\s*(?:위의|위에\s*있는|위에)\s*{COUNT}점\s*({P})(?![A-Z'])"): add('on_bisector', [m[2]] + _points(m[1]), m)
    for m in each(rf"점\s*({P}){TOPIC}\s*{LINE}({SEG})\s*위에"):
        if not on_arc(m): add('on_segment', [m[1]] + _points(m[2]), m)
    for m in each(rf"점\s*({P}){OBJ}\s*{LINE}({SEG})\s*위에\s*(?:잡|놓|찍|택)"):
        if not on_arc(m): add('on_segment', [m[1]] + _points(m[2]), m)
    for m in each(rf"점\s*({PLIST}){TOPIC}\s*{EACH}{LINE}({SEGLIST})\s*위(?:의\s*점(?:이|일)|에\s*있)"):  # "점 D는 BC 위의 점이다", not "C가 AB 위의 점 D에 오도록"
        for point, seg in zip(_points(m[1]), _segments(m[2])): add('on_segment', [point] + seg, m)
    for m in list(each(rf"점\s*({PLIST}){TOPIC}\s*(?:한\s*(?:직선|선분)|일직선)\s*위에")) + list(each(rf"(?:그래프|직선\s*[a-z]?)\s*위의\s*{COUNT}점\s*({PLIST})")):
        if len(_points(m[1])) >= 3: add('collinear', _points(m[1]), m)
    for m in each(rf"점\s*({P}){OBJ}\s*지나는\s*직선{TOPIC}\s*[^.?]{{0,20}}?{MEET}{OBJ}\s*{EACH}({P}),\s*({P})(?![A-Z'])"): add('collinear', [m[2], m[1], m[3]], m)
    for m in each(rf"{LINE}({SEG}){AND}\s*{LINE}({SEG}){TOPIC}\s*{MEET}{OBJ}\s*(?:점\s*)?({P})(?![A-Z'])"): add('intersection', [m[3]] + _points(m[1]) + _points(m[2]), m)
    for m in each(rf"{LINE}({SEG}){TOPIC}\s*{LINE}({SEGLIST}){AND}\s*{MEET}{OBJ}\s*{EACH}({PLIST})"):
        for other, p in zip(_segments(m[2]), _points(m[3])): add('intersection', [p] + _points(m[1]) + other, m)
    for m in each(rf"{LINE}({SEG})의\s*연장선\s*(?:위의|위에\s*있는|위에)\s*{COUNT}점\s*({P})(?![A-Z'])"): add('on_extension', [m[2]] + _points(m[1]), m)
    for m in each(rf"{LINE}({SEG})\s*(?:위의|위에\s*있는|위에)\s*{COUNT}점\s*({P})(?![A-Z'])"):
        if not on_arc(m): add('on_segment', [m[2]] + _points(m[1]), m)

    # --- lengths and directions
    for m in each(rf"(?<![∠=A-Z'])({SEG}(?:\s*=\s*{SEG})+)(?!\s*[×÷+*/:-])"):  # AM=BM=4 cm still says AM=BM
        chain = _segments(m[1])
        for a, b in zip(chain, chain[1:]): add('equal_length', a + b, m)
    for m in each(rf"({SEG})\s*⊥\s*({SEG})"): add('perpendicular', _points(m[1]) + _points(m[2]), m)
    for m in each(rf"{LINE}({SEG}){AND}\s*{LINE}({SEG}){TOPIC}?\s*(?:서로\s*)?수직"): add('perpendicular', _points(m[1]) + _points(m[2]), m)
    for m in each(rf"점\s*({P})에서\s*{LINE}({SEG})에\s*수직(?:으로|인)\s*(?:그은\s*)?(?:선|직선|반직선)\s*위에[^.?]{{0,30}}?점\s*({P})(?![A-Z'])"):
        add('perpendicular', [m[1], m[3]] + _points(m[2]), m)
    for m in each(rf"{LINE}({SEG}){TOPIC}\s*(?:반지름|선분|변|현)?\s*({SEG})의\s*수직이등분선"): add('perpendicular', _points(m[1]) + _points(m[2]), m)
    for m in each(rf"({SEG})\s*∥\s*({SEG})"): add('parallel', _points(m[1]) + _points(m[2]), m)
    for m in each(rf"{LINE}({SEG}){AND}\s*{LINE}({SEG}){TOPIC}?\s*(?:서로\s*)?평행"): add('parallel', _points(m[1]) + _points(m[2]), m)
    for m in each(rf"((?:∠\s*(?:{P}){{3}}\s*=\s*)+)90\s*°"):  # ∠AGB=∠BHC=90°
        for name in re.findall(rf"(?:{P}){{3}}", m[1]): add('right_angle', _points(name), m)

    # --- named shapes
    explicit = {r['points'][1] for r in found if r['kind'] == 'right_angle'}
    for m in each(rf"직각삼각형\s*((?:{P}){{3}})(?![A-Z'])"):
        if not explicit & set(_points(m[1])): add('right_triangle', _points(m[1]), m)
    for m in each(rf"∠\s*({P})\s*=\s*90\s*°"):
        # ∠B = 90° names only the vertex: take the triangle it belongs to.
        tri = next((t for t in re.findall(rf"삼각형\s*((?:{P}){{3}})(?![A-Z'])", s) if m[1] in _points(t)), None)
        if tri:
            a, c = [p for p in _points(tri) if p != m[1]]
            found[:] = [r for r in found if not (r['kind'] == 'right_triangle' and set(r['points']) == set(_points(tri)))]
            add('right_angle', [a, m[1], c], m)
    for m in each(rf"직사각형[^?]{{0,60}}?꼭짓점{OBJ}\s*({P}),\s*({P}),\s*({P}),\s*({P})(?![A-Z'])"):
        add('rectangle', [m[1], m[2], m[3], m[4]], m)
    for word, kind, n in (('정사각형', 'square', 4), ('직사각형', 'rectangle', 4), ('평행사변형', 'parallelogram', 4),
                          ('마름모', 'rhombus', 4), ('정삼각형', 'equilateral', 3)):
        for m in each(rf"(?<![가-힣]){word}\s*((?:{P}){{{n}}}(?:\s*(?:,|와|과)\s*(?:{P}){{{n}}})*)(?![A-Z'])"):
            for name in re.findall(rf"(?:{P}){{{n}}}", m[1]): add(kind, _points(name), m)  # 두 정사각형 ABCD와 OEFG
    for word, kind, n in (('정사각형', 'square', 4), ('직사각형', 'rectangle', 4), ('평행사변형', 'parallelogram', 4),
                          ('마름모', 'rhombus', 4), ('정삼각형', 'equilateral', 3)):
        for m in each(rf"[가-힣]각형\s*((?:{P}){{{n}}}){TOPIC}\s*{word}(?:이고|이며|이다|일\s*때)"): add(kind, _points(m[1]), m)
    return found


def figure_relations(reading):
    """{figure id: relations} for a page's reading.md: each figure gets the givens of its own question."""
    out = {}
    for block in re.split(r'(?m)^###\s+', reading)[1:]:
        figures = re.findall(r'!\[[^\]]*\]\(figure:([^)\s]+)\)', question_stem_figures(block))
        if figures:
            found = relations(block)
            for f in figures: out[f] = found
    return out


def question_stem_figures(question):
    """Figure references outside the answer block (a figure inside the 보기 box still belongs to the question)."""
    return re.sub(r'(?ms)^:::\s*answer.*?^:::\s*$', ' ', question)
