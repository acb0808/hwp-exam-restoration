"""Warnings that were wrong or wasteful in ten runs of one exam: where they stay quiet now, and one new one."""
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import restoration_single as single
from restoration_figure_geometry import length_line_findings
from restoration_figure_lint import circle_point_warnings
from restoration_montage import MAX_WIDTH, side_by_side


class CirclePointExceptionTests(unittest.TestCase):
    def test_points_of_the_other_concentric_circle_are_left_alone(self):
        tex = (r'\coordinate (O) at (0,0.6); \coordinate (A) at (-2.3,-0.38); \coordinate (B) at (-1.65,-0.38);'
               r'\draw (O) circle (2.5); \draw (O) circle (1.921); \draw (A) -- (B);'
               r'\node[below] at (A) {$A$}; \node[below] at (B) {$B$};')
        self.assertEqual(circle_point_warnings(tex), [])       # A is on the large circle, B on the small one

    def test_the_corner_of_a_shape_around_an_inscribed_circle_is_left_alone(self):
        tex = (r'\coordinate (A) at (0,3.6); \coordinate (B) at (0,0); \coordinate (C) at (5.4,0); \coordinate (D) at (2.7,3.6);'
               r'\coordinate (O) at (1.8,1.8); \draw (A) -- (B) -- (C) -- (D) -- cycle; \draw (O) circle (1.8);'
               r'\node[above] at (D) {$D$}; \node[above] at (A) {$A$};')
        self.assertEqual(circle_point_warnings(tex), [])       # D is 0.21 off the circle, at the end of the tangent side AD

    def test_a_point_typed_near_the_circle_on_a_crossing_line_is_still_flagged(self):
        tex = (r'\coordinate (O) at (0,0); \coordinate (P) at (1.7,0.2); \coordinate (Q) at (-3,-1);'
               r'\draw (O) circle (2); \draw (Q) -- (P); \node[right] at (P) {$P$};')
        self.assertEqual(len(circle_point_warnings(tex)), 1)   # the line QP runs through the circle: P was meant to be on it


def geo(dashed_line, curve=True, text='4cm', at=(4.0, 18.0)):
    """A dashed line from (10,10) to (10,30) mm with a label beside its middle, and a dashed arc elsewhere."""
    glyphs = [{'c': c, 'at': (at[0] + 1.6 * i, at[1]), 'box': (at[0] + 1.6 * i - .8, at[1] - 1, at[0] + 1.6 * i + .8, at[1] + 1)} for i, c in enumerate(text)]
    arc = [(30 + i, 5 + (i - 5) ** 2 / 10) for i in range(11)]
    return {'segments': [{'p': (10, 10), 'q': (10, 30), 'dashed': dashed_line, 'symbol': False}],
            'curves': [{'points': arc, 'dashed': True, 'only': True}] if curve else [], 'glyphs': glyphs, 'arcs': [], 'marks': []}


class LengthOnDashedLineTests(unittest.TestCase):
    def test_a_length_on_a_straight_dashed_line_is_said(self):
        [found] = length_line_findings(geo(True))
        self.assertEqual(found['kind'], 'length_line')
        self.assertTrue(found['text'].startswith('직선 점선 위의 길이: 4cm'))
        self.assertIn('원본도 직선 점선이면 그대로 둡니다', found['text'])

    def test_quiet_for_a_solid_line_a_bare_number_or_a_label_at_the_end(self):
        self.assertEqual(length_line_findings(geo(False)), [])
        self.assertEqual(length_line_findings(geo(True, text='4')), [])
        self.assertEqual(length_line_findings(geo(True, at=(4.0, 11.0))), [])     # beside an end, not the middle

    def test_quiet_without_any_dashed_curve_where_the_whole_figure_advice_speaks(self):
        self.assertEqual(length_line_findings(geo(True, curve=False)), [])

    def test_quiet_when_a_dashed_curve_joins_the_same_ends(self):
        g = geo(True); g['curves'].append({'points': [(10, 10), (7, 20), (10, 30)], 'dashed': True, 'only': True})
        self.assertEqual(length_line_findings(g), [])

    def test_it_is_said_once_at_the_render_where_it_first_shows(self):
        folder = Path(tempfile.mkdtemp()); (folder / 'diagram.pdf').write_bytes(b'')
        items = [{'id': 'q1-figure-1', 'status': 'pending_review', 'png': str(folder / 'diagram.png')}]
        import restoration_figure_geometry as geometry
        from unittest.mock import patch
        items[0]['question_id'] = 'q1'
        state = {'geometry_rounds': {'5/q1-figure-1': 1}}                          # the first render already had its round
        said = '직선 점선 위의 길이: 4cm 라벨이 직선 점선 가운데에 있습니다. 원본에서 …'
        with patch.object(geometry, 'length_line_warnings', return_value=[said]):
            lint = {}; single.length_line_slips(state, 5, items, lint); self.assertEqual(len(lint['q1-figure-1']), 1)
            lint = {}; single.length_line_slips(state, 5, items, lint); self.assertEqual(lint, {})
        # Kept by the producer: the human checker reads it in the notes.
        self.assertEqual(state['geometry_notes']['5']['q1-figure-1'], ['도형: q1 자동 측정 — 직선 점선 위의 길이: 4cm 라벨이 직선 점선 가운데에 있습니다(원본이 점선 호인지 확인)'])
        state['geometry_notes']['5'].clear()                                       # measured_slips clears a figure it finds clean
        with patch.object(geometry, 'length_line_warnings', return_value=[]):
            single.length_line_slips(state, 5, items, {}); self.assertEqual(state['geometry_notes']['5'], {})


class ResponseSizeTests(unittest.TestCase):
    ADVICE = '길이 표시 호 없음: 단위가 붙은 길이 라벨(%s)이 있는데 점선 호가 하나도 없습니다. ' + '원본에서 그 길이에 점선 호가 걸려 있으면 그립니다. ' * 4

    def test_the_same_advice_for_a_second_figure_keeps_its_first_sentence(self):
        lint = {'a': [self.ADVICE % '10cm', '(D) is 0.21 off the circle'], 'b': [self.ADVICE % '14cm']}
        single.brief_repeats(lint)
        self.assertEqual(lint['a'][0], self.ADVICE % '10cm')
        self.assertEqual(lint['b'], ['길이 표시 호 없음: 단위가 붙은 길이 라벨(14cm)이 있는데 점선 호가 하나도 없습니다. (안내는 위 도형과 같습니다.)'])
        self.assertEqual(lint['a'][1], '(D) is 0.21 off the circle')


class PairWidthTests(unittest.TestCase):
    def test_a_one_row_table_render_is_not_wider_than_the_room_beside_its_crop(self):
        folder = Path(tempfile.mkdtemp())
        Image.new('RGB', (786, 185), 'white').save(folder / 'a.png'); Image.new('RGB', (1460, 200), 'white').save(folder / 'b.png')
        with Image.open(side_by_side(folder / 'a.png', folder / 'b.png', folder / 'c.png')) as im:
            self.assertLessEqual(im.width, max(MAX_WIDTH, 2 * 786 + 18))
        Image.new('RGB', (400, 400), 'white').save(folder / 'a.png'); Image.new('RGB', (600, 600), 'white').save(folder / 'b.png')
        with Image.open(side_by_side(folder / 'a.png', folder / 'b.png', folder / 'c.png')) as im:
            self.assertEqual(im.size, (400 + 400 + 18, 400 + 16 + 12))                # ordinary pairs are unchanged


class FigureCheckSheetTests(unittest.TestCase):
    def test_every_page_with_figures_gets_a_sheet_beside_the_output(self):
        from unittest.mock import patch
        folder = Path(tempfile.mkdtemp()); sheet = folder / 'pairs.png'; Image.new('RGB', (40, 30), 'white').save(sheet)
        output = folder / 'out' / '복원.hwpx'; output.parent.mkdir()
        with patch.object(single, 'review_figure_sheet', side_effect=lambda root, state, n: sheet if n != 4 else None):
            saved = single.figure_check_sheets(folder, {}, [3, 4, 5], str(output))
        self.assertEqual([Path(s).name for s in saved], ['복원-도형대조-3쪽.png', '복원-도형대조-5쪽.png'])
        self.assertTrue(all(Path(s).is_file() for s in saved))
        self.assertEqual(single.figure_check_sheets(folder, {}, [3], None), [])


if __name__ == '__main__':
    unittest.main()
