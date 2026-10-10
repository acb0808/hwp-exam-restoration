"""Engine-side label placement on fitted figures (restoration_labels): measured on rendered PDFs, no model calls."""
import importlib.util
import math
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import restoration_single as single
import restoration_labels as labels
import restoration_batch as batch
import test_figure_retry as retry

HAS_TEX = bool(shutil.which('xelatex'))
FRAME = r'\path (-3,-2) rectangle (3,2);'  # fixes the bounding box, so positions compare across renders


def render_document(document):
    spec = importlib.util.spec_from_file_location('tr', single.shared.SKILL / 'runtime/tikz_render.py')
    tr = importlib.util.module_from_spec(spec); spec.loader.exec_module(tr)
    d = Path(tempfile.mkdtemp())
    src = d / 'f.tex'; src.write_text(document, encoding='utf-8')
    tr.render(src, d / 'out')
    return d / 'out' / 'diagram.pdf'


def render(body, width=50, shifts=None):
    return render_document(single.diagram_document(r'\begin{tikzpicture}' + FRAME + body + r'\end{tikzpicture}', width, shifts=shifts))


def pixels(pdf):
    import fitz
    with fitz.open(pdf) as doc: return doc[0].get_pixmap(dpi=300).samples


def ink(pdf, text):
    return next(labels._centre(l['ink']) for l in labels.labels(pdf)[1].values() if l['text'] == text)


def placed(body):
    """Crossed labels before, the shift table, crossed labels after the second render."""
    pdf = render(body); shifts = labels.label_shifts(pdf)
    return labels.crossed_labels(pdf), shifts, labels.crossed_labels(render(body, shifts=shifts)) if shifts else None


@unittest.skipUnless(HAS_TEX, 'no TeX engine')
class RenderedPlacementTests(unittest.TestCase):
    def test_logging_hook_draws_nothing(self):
        picture = r'\begin{tikzpicture}\coordinate (A) at (0,0);\coordinate (B) at (4,0);\draw (A)--(B)--(2,3)--cycle;' \
                  r'\node[below] at (A) {$A$};\node at (2,0) {$M$};\end{tikzpicture}'
        hooked = single.diagram_document(picture, 50)
        plain = hooked.replace(labels.preamble(picture), '', 1)
        self.assertNotEqual(plain, hooked)
        self.assertEqual(pixels(render_document(hooked)), pixels(render_document(plain)))

    def test_label_on_a_line_moves_clear(self):
        # 중학교 시험지 A q23 style: a chord drawn through the letter.
        before, shifts, after = placed(r'\draw (-3,0)--(3,0);\node at (0,0) {$M$};')
        self.assertEqual(before, ['M'])
        self.assertEqual(len(shifts), 1)
        self.assertEqual(after, [])

    def test_clear_label_stays(self):
        before, shifts, _ = placed(r'\draw (-3,0)--(3,0);\node[above=2mm] at (0,0) {$M$};')
        self.assertEqual((before, shifts), ([], {}))

    def test_label_covering_a_point_mark_moves_off_it(self):
        before, shifts, after = placed(r'\fill (0,0) circle (1.2pt);\node at (0,0) {$O$};')
        self.assertEqual((before, after), (['O'], []))

    def test_overlapping_labels_are_separated(self):
        # 중학교 시험지 A q17 style: 0.84 and 0.77 printed on top of each other.
        before, shifts, after = placed(r'\node at (0,0) {$0.84$};\node at (0,0.12) {$0.77$};')
        self.assertEqual(len(before), 2)
        self.assertEqual(after, [])

    def test_white_backed_length_label_keeps_its_own_arc(self):
        before, shifts, _ = placed(r'\coordinate (A) at (-2,0);\coordinate (B) at (2,0);\ExamLengthArc{A}{B}{$8$}')
        self.assertEqual((before, shifts), ([], {}))

    def test_white_backed_label_hiding_a_solid_side_moves(self):
        # 중학교 시험지 A q21 style: "8 cm" sat on side AB and cut it.
        before, shifts, after = placed(r'\coordinate (A) at (-2,0);\coordinate (B) at (2,0);\draw (A)--(B);'
                                      r'\ExamLengthArc[bend left=4]{A}{B}{$8$}')
        self.assertEqual((before, after), (['8'], []))

    def test_angle_pic_value_is_measured_and_moved(self):
        # 고등학교 시험지 C q19: "$60^\circ$" written as the quote of an angle pic sat on side AB.
        before, shifts, after = placed(r'\coordinate (O) at (0,-1.5);\coordinate (A) at (-1,0.3);\coordinate (B) at (1,0.3);'
                                      r'\draw (A)--(O)--(B)--cycle;'
                                      r'\pic[draw,angle radius=6mm,angle eccentricity=2.4,"$60^\circ$"] {angle=B--O--A};')
        self.assertEqual(before, ['60◦'])
        self.assertEqual(after, [])

    def test_no_pointer_is_drawn_for_a_value_that_cannot_be_freed(self):
        # 중학교 시험지 D q9: 120° inside an angle that another radius runs through. The source prints no arrow there, so
        # neither does the engine: an arrow is in a figure only where its producer drew the source's own.
        body = (r'\coordinate (O) at (0,0);\coordinate (P) at (30:2.6);\coordinate (Q) at (0,-2.6);\draw (O) circle (2.6);'
                r'\draw (O)--(P);\draw (O)--(Q);\draw (O)--(-20:2.6);\draw (O)--(-40:2.6);\ExamAngle{P}{O}{Q}{$120^\circ$}')
        before, shifts, _ = placed(body)
        self.assertIn('120◦', before)
        self.assertEqual({len(v) for v in shifts.values()} - {2}, set())
        import fitz
        with fitz.open(render(body, shifts=shifts)) as doc:      # no filled arrow tip anywhere
            tips = [d for d in doc[0].get_drawings() if d.get('type') in ('f', 'fs') and max(d['rect'].width, d['rect'].height) < 6]
        self.assertFalse(tips)

    def test_a_length_arc_rises_as_in_print(self):
        # 0.12 of the length it spans, and no less than 2.2 mm for a short one.
        geo = labels.labels(render(r'\draw (-2,0)--(2,0);\draw (-2.5,1.5)--(-1.5,1.5);\ExamLengthArc{-2,0}{2,0}{$8$}\ExamLengthArc{-2.5,1.5}{-1.5,1.5}{$2$}', width=60))[0]
        rises = sorted(max(abs(p[1] - c['points'][0][1]) for p in c['points']) / max(1e-6, math.dist(c['points'][0], c['points'][-1])) for c in geo['curves'] if c['dashed'])
        self.assertAlmostEqual(rises[0], 0.12, delta=0.025)
        self.assertGreater(rises[1], 0.19)

    def test_text_in_a_white_cell_is_not_as_large_as_the_cell(self):
        # 중학교 시험지 A q12: a table drawn with white-filled cells. The cell was taken for the backing of its text.
        body = (r'\draw[fill=white] (-2,0) rectangle (0,0.8);\node at (-1,0.4) {$46^\circ$};'
                r'\draw[fill=white] (0,0) rectangle (2,0.8);\node at (1,0.4) {$0.7193$};')
        pdf = render(body, width=40)
        self.assertEqual(labels.label_shifts(pdf), {})
        self.assertEqual(labels.crossed_labels(pdf), [])

    def test_angle_mark_symbols_are_not_labels(self):
        # 중학교 시험지 B q11: an equal-angle dot must not leave its angle.
        before, shifts, _ = placed(r'\draw (-3,0)--(3,0);\node at (0,0) {$\cdot$};\node at (1,0) {$\times$};')
        self.assertEqual((before, shifts), ([], {}))

    def test_radical_sign_is_measured_by_its_digits(self):
        # 중학교 시험지 A q15: the font box of the radical sign reaches 2.5mm above the digits; a line there is not a crossing.
        before, shifts, _ = placed(r'\draw (-3,0.45)--(3,0.45);\node at (0,0) {$\sqrt{10}$};\node at (2,-1) {$\frac{1}{2}$};')
        self.assertEqual((before, shifts), ([], {}))

    def test_framed_text_stays_in_its_frame(self):
        before, shifts, _ = placed(r'\node[draw,inner sep=1pt] at (0,0) {$A$};')
        self.assertEqual((before, shifts), ([], {}))

    def test_shift_is_applied_as_requested(self):
        body = r'\node at (0,0) {$M$};'
        a = ink(render(body), 'M'); b = ink(render(body, shifts={1: (1.0, 0.5)}), 'M')
        self.assertAlmostEqual(b[0] - a[0], 1.0, delta=0.1)
        self.assertAlmostEqual(b[1] - a[1], -0.5, delta=0.1)  # PDF y runs down

    def test_named_points_are_logged_in_printed_mm(self):
        pdf = render(r'\coordinate (A) at (-2,0);\coordinate (B) at (2,1);\coordinate (M) at ($(A)!0.5!(B)$);'
                     r'\draw (A)--(B);\path[name path=l] (A)--(B);\path[name path=v] (1,-1)--(1,2);'
                     r'\path[name intersections={of=l and v,by={P},sort by=l}];')
        pts = labels.points(pdf)
        self.assertEqual(sorted(pts), ['A', 'B', 'M', 'P'])
        self.assertAlmostEqual(pts['M'][0], (pts['A'][0] + pts['B'][0]) / 2, delta=0.02)
        self.assertAlmostEqual(pts['M'][1], (pts['A'][1] + pts['B'][1]) / 2, delta=0.02)
        self.assertLess(pts['B'][1], pts['A'][1])  # B is higher on the page
        # 6 units of frame print at 50mm less the 2pt borders: A-B is 4 units across.
        self.assertAlmostEqual(pts['B'][0] - pts['A'][0], 4 / 6 * (50 - 4 * labels.PT_MM), delta=0.6)

    def test_labels_left_on_each_other_are_reported_once_per_figure(self):
        # The render before any shift stands in for one where the engine found no free spot.
        pdf = render(r'\node at (0,0) {$PQ$};\node at (0.15,0.05) {$37$};', width=60)
        self.assertEqual(labels.overlapping_labels(pdf), [('PQ', '37')])
        item = {'id': 'q3-figure-1', 'question_id': 'q3', 'status': 'rendered', 'png': str(pdf.with_name('diagram.png'))}
        state = {}; first = {}; single.crossed_label_warnings(state, 2, [item], first)
        self.assertEqual(len(first['q3-figure-1']), 1)
        self.assertIn('"PQ"와 "37"가 서로 겹쳐', first['q3-figure-1'][0])
        self.assertLessEqual(len(first['q3-figure-1'][0]), 120)
        again = {}; single.crossed_label_warnings(state, 2, [item], again)
        self.assertEqual(again, {})

    def test_label_left_on_a_line_is_not_reported(self):
        # No free spot within reach: lines every 1.5mm. The source often has the label there too.
        body = ''.join(rf'\draw (-3,{y / 10:.2f})--(3,{y / 10:.2f});' for y in range(-150, 151, 15)) + r'\node at (0,0) {$M$};'
        pdf = render(body, width=60)
        self.assertEqual(labels.crossed_labels(pdf), ['M'])
        item = {'id': 'q3-figure-1', 'question_id': 'q3', 'status': 'rendered', 'png': str(pdf.with_name('diagram.png'))}
        found = {}; single.crossed_label_warnings({}, 2, [item], found)
        self.assertEqual(found, {})

    def test_overlapping_labels_are_read_apart_and_separated(self):
        # A point label and an angle value written on the same side of the point.
        body = (r'\coordinate (B) at (-2,0);\draw (B)--(2,0);\draw (B)--(0,2.5);'
                r'\node[below left] at (B) {$B$};\node[below] at (B) {$60^\circ$};')
        pdf = render(body, width=40)
        texts = sorted(l['text'] for l in labels.labels(pdf)[1].values())
        self.assertEqual(texts, ['60◦', 'B'])
        if labels.overlapping_labels(pdf):
            shifts = labels.label_shifts(pdf)
            self.assertTrue(shifts)
            self.assertEqual(labels.overlapping_labels(render(body, width=40, shifts=shifts)), [])


def box(cx, cy, w=2.0, h=2.4): return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)


def line(p, q, dashed=False): return {'p': p, 'q': q, 'dashed': dashed, 'symbol': False}


class SolverTests(unittest.TestCase):
    """Synthetic geometry in PDF mm (y down); shifts come back in TikZ direction (y up)."""

    def solve(self, segments=(), marks=(), arcs=(), found=None):
        geo = {'segments': list(segments), 'arcs': list(arcs), 'marks': list(marks), 'glyphs': []}
        return labels.solve(geo, found or {1: {'ink': box(0, 0), 'backed': False, 'text': 'M'}})

    def test_nearest_free_spot_wins(self):
        shifts = self.solve([line((-10, 0.9), (10, 0.9))])  # crosses the lower part of the label
        self.assertGreater(shifts[1][1], 0)  # up, the short way out
        self.assertLessEqual(math.hypot(*shifts[1]), 2.6)

    def test_a_label_close_to_two_rules_is_not_moved_onto_one(self):
        # 중학교 시험지 A q12: text as high as its table cell. Both rules are inside the margin, neither touches the text.
        rules = [line((-10, -1.3), (10, -1.3)), line((-10, 1.3), (10, 1.3))]
        self.assertEqual(self.solve(rules, found={1: {'ink': box(0, 0, w=5), 'backed': False, 'text': '46◦'}}), {})

    def test_move_does_not_cross_a_line_the_label_was_clear_of(self):
        # A point mark under the label; clear lines just above and below: the only way out is sideways.
        shifts = self.solve([line((-10, -1.6), (10, -1.6)), line((-10, 1.6), (10, 1.6))], marks=[(0, 0)])
        self.assertLess(abs(shifts[1][1]), 0.3)
        self.assertGreaterEqual(abs(shifts[1][0]), 1.0)

    def test_angle_value_stays_inside_its_angle(self):
        # 중학교 시험지 E q2 / 고등학교 시험지 C q19: a value squeezed in a narrow angle is clipped by both sides at its edges.
        # Vertex below the label, sides rising left and right; the free space is further up, inside the angle.
        apex = (0, 4.0); sides = [line(apex, (-6.0, -6.0)), line(apex, (6.0, -6.0))]
        value = {1: {'ink': box(0, 1.4, w=2.4), 'backed': False, 'text': '60'}}
        shifts = self.solve(sides, found=value)
        cx, cy = shifts[1][0], 1.4 - shifts[1][1]
        self.assertLess(cy, 1.4)  # away from the vertex, up the page
        self.assertLess(abs(cx) + 1.2, 0.6 * (4.0 - cy))  # the whole label is between the two sides
        # With the far side of a triangle just above it there is no room: it may shuffle, but its centre stays inside.
        dx, dy = self.solve(sides + [line((-10, -0.3), (10, -0.3))], found=value).get(1, (0, 0))
        self.assertLess(abs(dx), 0.6 * (4.0 - (1.4 - dy)))
        self.assertGreater(1.4 - dy, -0.3)

    def test_angle_value_on_a_line_stays_on_its_side(self):
        # 고등학교 시험지 C q19: 60° printed over side AB, mostly below it. No room below either (another line).
        # A letter may hop over the side; the value names the angle it is printed in and stays there.
        lines = [line((-10, -0.5), (10, -0.5)), line((-10, 1.9), (10, 1.9))]
        self.assertGreater(self.solve(lines, found={1: {'ink': box(0, 0), 'backed': False, 'text': 'M'}})[1][1], 1.5)
        dx, dy = self.solve(lines, found={1: {'ink': box(0, 0), 'backed': False, 'text': '60◦'}}).get(1, (0, 0))
        self.assertTrue(-0.5 < -dy < 1.9)

    CROWD = [((-30, y), (30, y)) for y in (-1.3, 0.0, 1.3)]   # three lines through and beside the label, free paper beyond

    def test_a_crowded_value_is_never_led_out_with_a_pointer(self):
        # Each of these was led to free paper with an engine-drawn arrow for a day. The figure copies its source:
        # the value stays within the near reach, whatever is on it.
        far = [line((-30, -4.2), (30, -4.2)), line((-30, 4.2), (30, 4.2))]
        crowd = [line(p, q) for p, q in self.CROWD] + far
        corner = [line((0, 0), (30, 0)), line((0, 0), (29.3, -6.2)), line((11.5, 1), (11.5, -4))]          # a 12 degree angle
        through = [line((0, 0), (20, 0)), line((0, 0), (17, -10)), line((0, 0), (20, -5.4))]               # a third line through the angle
        arc = {'c': (0, 0), 'r': 3.5, 'start': -math.atan2(10, 17), 'sweep': math.atan2(10, 17), 'dashed': False}
        at = (9 * math.cos(-0.26), 9 * math.sin(-0.26))
        cases = [(crowd, [], box(0, 0, w=6), '12cm'), (crowd, [], box(0, 0), '60◦'), (corner, [], box(8.0, -0.85, w=3.4), 'x◦'),
                 (through, [arc], box(*at, w=3.4), '30◦')]
        for sides, arcs, ink_box, text in cases:
            with self.subTest(text=text):
                shift = self.solve(sides, arcs=arcs, found={1: {'ink': ink_box, 'backed': False, 'text': text}}).get(1, (0, 0))
                self.assertEqual(len(shift), 2)
                self.assertLessEqual(math.hypot(*shift), max(labels.RADII_MM) + 0.01)

    def test_label_stays_with_its_own_vertex(self):
        # The label names the corner at (0,1.5) and is crossed by a side; the free spot must not be nearer (4,1.5)'s corner.
        own, other = (0, 1.5), (2.6, 1.5)
        shifts = self.solve([line(own, (-10, 1.5)), line(own, (0, -10)), line(other, (2.6, 12))],
                            found={1: {'ink': box(0.6, 0.4), 'backed': False, 'text': 'A'}})
        c = (0.6 + shifts[1][0], 0.4 - shifts[1][1])
        self.assertLess(math.dist(c, own), math.dist(c, other) / 0.9)

    def test_dashed_arc_under_a_white_backed_label_is_not_a_conflict(self):
        dashed = [line((-10, 0), (10, 0), dashed=True)]
        self.assertEqual(self.solve(dashed, found={1: {'ink': box(0, 0), 'backed': True, 'text': '8'}}), {})
        self.assertEqual(len(self.solve(dashed)), 1)

    def test_point_names(self):
        tex = (r'\coordinate (A) at (0,0); \node[above] (B1) at (1,1) {$B$}; \draw (0,0) -- (1,2) coordinate (C);'
               r"\path[name intersections={of=c and l,by={P,Q'},sort by=l}]; \node[name=R] at (0,0) {};"
               r'\foreach \i in {1,2} \coordinate (X\i) at (\i,0); % \coordinate (Z) at (9,9);')
        self.assertEqual(labels.point_names(tex), ['A', 'B1', 'C', 'R', 'P', "Q'"])

    def test_shift_table_is_part_of_the_document(self):
        picture = r'\begin{tikzpicture}\draw (0,0)--(1,0);\node at (0.5,0) {$M$};\end{tikzpicture}'
        plain = single.diagram_document(picture, 40)
        moved = single.diagram_document(picture, 40, shifts={1: (0.5, -1.25)})
        self.assertNotIn('ExamShift1', plain.replace(labels.NODE_HOOK, ''))
        self.assertIn(r'\expandafter\def\csname ExamShift1\endcsname{0.50mm,-1.25mm}', moved)
        self.assertEqual(single.diagram_document(picture, 40, shifts={}), plain)  # an empty table reuses the cached render
        self.assertNotIn('ExamLead', moved)  # the engine draws no pointer from a moved label
        self.assertNotIn('ExamNode', single.diagram_document(picture))  # unfitted documents are left alone


class RefitLoopTests(unittest.TestCase):
    """prepare_figures asks refit again after a refit, twice at most."""

    def renders(self, answers):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            root, engine, rows, calls, runtime = retry.FigureRetryTests().fixture(base)
            asked = []

            def refit(row, pdf):
                asked.append(Path(pdf).parent.name)
                n = sum(1 for a in asked if a.startswith(row['id']))
                return answers[n - 1] if row['id'] == 'f1' and n <= len(answers) else None
            with patch.object(batch.runpy, 'run_path', return_value=runtime):
                result = batch.prepare_figures(root, 1, rows[:1], base / 'batch', engine=engine, refit=refit)
            return len(calls), asked, Path(result['items'][0]['png']).parent.name

    def test_no_refit_renders_once(self):
        self.assertEqual(self.renders([None]), (1, ['f1'], 'f1'))

    def test_one_refit_renders_twice(self):
        again = r'\begin{tikzpicture}\draw (0,0)--(2,1);\end{tikzpicture}'
        self.assertEqual(self.renders([again, None]), (2, ['f1', 'f1-refit'], 'f1-refit'))

    def test_refits_stop_after_two(self):
        a = r'\begin{tikzpicture}\draw (0,0)--(2,1);\end{tikzpicture}'; b = a.replace('2,1', '3,1'); c = a.replace('2,1', '4,1')
        self.assertEqual(self.renders([a, b, c]), (3, ['f1', 'f1-refit'], 'f1-refit2'))


if __name__ == '__main__':
    unittest.main()
