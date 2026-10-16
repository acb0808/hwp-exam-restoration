"""Figure style the user chose on 2026-10-14: thinner lines, smaller labels, an angle arc clear of the right angle
it holds, and value arrows that end on the middle of their arc. Measured on rendered PDFs, no model calls."""
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import restoration_single as single
import restoration_labels as labels
import restoration_figure_lint as lint
from test_label_placement import HAS_TEX, render

MM = 25.4 / 72


def arrow_heads(pdf):
    import fitz
    with fitz.open(pdf) as doc:
        return [{'rect': d['rect'], 'points': [p for it in d['items'] for p in it[1:] if hasattr(p, 'x')]}
                for d in doc[0].get_drawings() if d.get('type') in ('f', 'fs') and max(d['rect'].width, d['rect'].height) < 6]


def drawn_arc(pdf, centre):
    """Radius in mm of the stroked arc around centre: a curve piece whose two ends stand equally far from it."""
    import fitz
    with fitz.open(pdf) as doc:
        for d in doc[0].get_drawings():
            for it in d['items']:
                if it[0] != 'c': continue
                a, b = math.dist((it[1].x * MM, it[1].y * MM), centre), math.dist((it[4].x * MM, it[4].y * MM), centre)
                if 1 < a < 6 and abs(a - b) < 0.05: return a
    raise AssertionError('no arc')


def direction(a, b):
    return math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))


def turn(a, b):
    return abs((a - b + 180) % 360 - 180)


@unittest.skipUnless(HAS_TEX, 'no TeX engine')
class FigureStyleTests(unittest.TestCase):
    def test_labels_print_at_085_and_lines_at_03_pt(self):
        import fitz
        pdf = render(r'\draw (-2,0)--(2,0);\node[above] at (0,0) {$M$};')
        with fitz.open(pdf) as doc:
            sizes = [s['size'] for b in doc[0].get_text('dict')['blocks'] for l in b.get('lines', []) for s in l['spans']]
            widths = [d['width'] for d in doc[0].get_drawings() if d.get('width')]
        self.assertAlmostEqual(max(sizes), 10 * single.LABEL_SCALE, delta=0.3)
        self.assertAlmostEqual(max(widths), single.LINE_WIDTH_PT, delta=0.03)

    def test_an_angle_arc_holding_a_right_angle_runs_outside_its_square(self):
        # 중학교 시험지 D q9: the 120 degree arc at O was drawn at the size of the right angle inside it.
        base = (r'\coordinate (O) at (0,0);\coordinate (B) at (2,0);\coordinate (C) at (0,-2);\coordinate (E) at (30:2);'
                r'\draw (B)--(O)--(C);\draw (O)--(E);')
        alone, held = [], []
        labels.logged(render(base + r'\ExamAngle{C}{O}{E}{}'), marks=alone)
        labels.logged(render(base + r'\ExamRightAngle{B}{O}{C}\ExamAngle{C}{O}{E}{}'), marks=held)
        self.assertLess(alone[0]['r'], 2.0 * 1.42)                  # on its own the arc would cross the square
        self.assertGreater(held[0]['r'], 2.0 * 1.42 + 0.5)          # held: clear of the square's far corner

    def test_a_right_angle_leaves_the_holding_value_its_whole_span(self):
        # The square carries no value: the 120 degree value was pushed into the 30 degrees between OB and OE, onto M.
        import fitz
        pdf = render(r'\coordinate (O) at (0,0);\coordinate (B) at (2,0);\coordinate (C) at (0,-2);\coordinate (E) at (30:2);'
                     r'\draw (B)--(O)--(C);\draw (O)--(E);\ExamRightAngle{B}{O}{C}\ExamAngle{E}{O}{C}{$120^\circ$}')
        pts = labels.points(pdf)
        with fitz.open(pdf) as doc:
            word = next(w for w in doc[0].get_text('words') if '120' in w[4])
        self.assertGreater(direction(pts['O'], ((word[0] + word[2]) / 2 * MM, (word[1] + word[3]) / 2 * MM)), 5)   # below OB

    def test_a_value_arrow_ends_on_the_middle_of_its_arc(self):
        # q25 B: the tip stopped 12 degrees inside the arm and read as pointing at the arc's end.
        for spread in (40, 55, 65, 120):
            body = (r'\coordinate (B) at (0,0);\coordinate (A) at (2.5,0);\coordinate (C) at (%d:2.5);\draw (A)--(B)--(C);'
                    r'\ExamAngle[arrow]{A}{B}{C}{$%d^\circ$}' % (spread, spread))
            pdf = render(body); pts = labels.points(pdf); heads = arrow_heads(pdf); marks = []
            labels.logged(pdf, marks=marks)
            self.assertEqual(len(heads), 1, spread)
            tip = min(((p.x * MM, p.y * MM) for p in heads[0]['points']), key=lambda q: math.dist(q, pts['B']))
            middle = (direction(pts['B'], pts['A']) + direction(pts['B'], pts['C'])) / 2
            self.assertLess(turn(direction(pts['B'], tip), middle), 3, spread)
            self.assertLess(abs(math.dist(tip, pts['B']) - marks[0]['r']), 0.3, spread)   # on the arc, not short of it

    def test_the_arrow_ends_on_the_drawn_arc_whatever_the_picture_scale(self):
        # Antigravity q12 (2026-10-15): under the producer's [scale=0.6] the tip stopped at 0.6 of the radius, inside the arc.
        from test_label_placement import render_document
        seen = []
        for scale in (0.6, 1, 1.6):
            body = (r'\begin{tikzpicture}[scale=%s]\path (-3/%s,-2/%s) rectangle (3/%s,2/%s);' % ((scale,) * 5) +
                    r'\coordinate (B) at (0,0);\coordinate (A) at (2.5/%s,0);\coordinate (C) at (30:2.5/%s);\draw (A)--(B)--(C);'
                    % (scale, scale) + r'\ExamAngle[arrow]{A}{B}{C}{$x^\circ$}\end{tikzpicture}')
            pdf = render_document(single.diagram_document(body, 50)); pts = labels.points(pdf); heads = arrow_heads(pdf)
            tip = min(((p.x * MM, p.y * MM) for p in heads[0]['points']), key=lambda q: math.dist(q, pts['B']))
            arc = drawn_arc(pdf, pts['B'])
            self.assertLess(abs(math.dist(tip, pts['B']) - arc), 0.3, scale)
            seen.append(arc)
        self.assertLess(max(seen) - min(seen), 0.1)   # the arc prints at one size

    def test_the_value_goes_beside_the_arm_the_source_names(self):
        # q25: the source prints 26 degrees above AB; the engine had chosen the side beyond BP.
        import fitz
        base = r'\coordinate (B) at (0,0);\coordinate (A) at (2.5,0);\coordinate (C) at (40:2.5);\draw (A)--(B)--(C);'
        seen = {}
        for arm in ('A', 'C'):
            pdf = render(base + r'\ExamAngle[arrow=%s]{A}{B}{C}{$40^\circ$}' % arm); pts = labels.points(pdf)
            with fitz.open(pdf) as doc:
                word = next(w for w in doc[0].get_text('words') if '40' in w[4])
            seen[arm] = direction(pts['B'], ((word[0] + word[2]) / 2 * MM, (word[1] + word[3]) / 2 * MM))
        self.assertGreater(seen['A'], 0)            # PDF y runs down: below arm BA
        self.assertLess(seen['C'], -40)             # beyond arm BC, outside the angle


@unittest.skipUnless(HAS_TEX, 'no TeX engine')
class FarLengthTests(unittest.TestCase):
    BASE = r'\coordinate (A) at (-2,0);\coordinate (B) at (2,0);\coordinate (C) at (0,1.5);\draw (A)--(B)--(C)--cycle;'

    def far(self, body):
        text = r'\begin{tikzpicture}' + self.BASE + body + r'\end{tikzpicture}'
        return labels.far_lengths(render(self.BASE + body), text)

    def test_a_length_far_from_its_segment_is_told(self):
        # Beside BC but meant for AB: the segment named in the TeX is the one measured, not the nearest line.
        found = self.far(r'\path (A) -- node[above=12mm] {$4$} (B);')
        self.assertEqual([(t, n) for t, _, n in found], [('4', 'AB')])

    def test_the_producer_is_told_once_in_one_short_line(self):
        body = r'\path (A) -- node[above=12mm] {$4$} (B);'
        pdf = render(self.BASE + body)
        item = {'id': 'q3-figure-1', 'question_id': 'q3', 'status': 'rendered', 'png': str(pdf.with_name('diagram.png'))}
        originals = {'q3-figure-1': r'\begin{tikzpicture}' + self.BASE + body + r'\end{tikzpicture}'}
        state = {}; first = {}; single.far_length_warnings(state, 2, [item], first, originals)
        self.assertEqual(len(first['q3-figure-1']), 1)
        self.assertIn('길이 "4"가 선분 AB에서', first['q3-figure-1'][0])
        self.assertLessEqual(len(first['q3-figure-1'][0].encode('utf-8')), 200)
        again = {}; single.far_length_warnings(state, 2, [item], again, originals)
        self.assertEqual(again, {})

    def test_a_length_beside_its_segment_or_on_its_arc_is_not(self):
        self.assertEqual(self.far(r'\path (A) -- node[below] {$4$} (B);'), [])
        self.assertEqual(self.far(r'\ExamLengthArc{A}{B}{$4$}'), [])


class AngleArrowLintTests(unittest.TestCase):
    def test_a_hand_drawn_value_arrow_is_told_once(self):
        # Sonnet q25: \ExamAngle{A}{B}{P}{} with a separate value node and \draw[->,>=stealth] to the arc's end.
        hand = (r'\begin{tikzpicture}\ExamAngle{A}{B}{P}{}\node at ($(B)+(-0.7,0.38)$) {$26^\circ$};'
                r'\draw[->,>=stealth] ($(B)+(-0.7,0.15)$) to[out=-70,in=110] ($(B)+(-0.36,-0.05)$);\end{tikzpicture}')
        self.assertEqual(len(lint.angle_arrow_warnings(hand)), 1)
        self.assertIn(r'\ExamAngle[arrow]', lint.angle_arrow_warnings(hand)[0])

    def test_a_hand_drawn_unknown_angle_is_told(self):
        # Flash, 2026-10-15: x for the corner OBI as its own node and \draw[->], the arrow across side BA.
        hand = (r'\begin{tikzpicture}\ExamAngle{I}{B}{O}{}\coordinate (X) at (-0.4,0.45);\node at (X) {$x$};'
                r'\draw[->] (X) to[bend left=15] (0.5,0.4);\end{tikzpicture}')
        self.assertIn(r'\ExamAngle[arrow]{A}{B}{C}{$x$}', lint.angle_arrow_warnings(hand)[0])
        # A point named beside a bare angle, or x on an axis, is not.
        self.assertEqual(lint.angle_arrow_warnings(hand.replace(r'\draw[->] (X) to[bend left=15] (0.5,0.4);', '')), [])
        self.assertEqual(lint.angle_arrow_warnings(r'\begin{tikzpicture}\ExamAngle{A}{B}{C}{$x$}\end{tikzpicture}'), [])

    def test_engine_arrows_and_axes_are_not_told(self):
        engine = r'\begin{tikzpicture}\ExamAngle[arrow]{A}{B}{P}{$26^\circ$}\end{tikzpicture}'
        axes = r'\begin{tikzpicture}\draw[->] (0,0)--(3,0) node[right] {$x$};\node at (1,1) {$y=2x$};\end{tikzpicture}'
        self.assertEqual(lint.angle_arrow_warnings(engine), [])
        self.assertEqual(lint.angle_arrow_warnings(axes), [])


if __name__ == '__main__':
    unittest.main()
