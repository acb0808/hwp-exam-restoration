"""Stated position relations measured on the figure (restoration_relation_check) and how the slips are delivered."""
import importlib.util
import math
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import restoration_job as job
import restoration_single as single
import restoration_relation_check as rc
from restoration_relations import relations
import test_figure_retry as fixtures
import test_single_review_workflow as workflow

HAS_TEX = bool(shutil.which('xelatex'))


def figure(points, circles=(), labels=None, vertices=None):
    return rc.Figure(points, [{'c': c, 'r': r, 'sweep': 2 * math.pi} for c, r in circles], labels, vertices)


def slips(text, fig): return [(f['kind'], f['deviation_mm']) for f in rc.check(relations(text), fig)]


class MeasuredRelationTests(unittest.TestCase):
    """Points in printed mm, y down; sentences are made up."""

    def test_midpoint(self):
        text = r'$\overline{BC}$의 중점을 M이라 하자.'
        self.assertEqual(slips(text, figure({'B': (0, 0), 'C': (40, 0), 'M': (20, 0)})), [])
        self.assertEqual(slips(text, figure({'B': (0, 0), 'C': (40, 0), 'M': (22, 0)})), [('midpoint', 2.0)])
        self.assertEqual(slips(text, figure({'B': (0, 0), 'C': (40, 0), 'M': (20.2, 0)})), [])  # does not show in print

    def test_tolerance_grows_with_the_length_involved(self):
        text = r'$\overline{AB}=\overline{AC}$인 삼각형 ABC'
        self.assertEqual(slips(text, figure({'A': (0, 0), 'B': (50, 0), 'C': (0, 50.8)})), [])       # 0.8mm of 50mm: under 2%
        self.assertEqual(slips(text, figure({'A': (0, 0), 'B': (50, 0), 'C': (0, 52)})), [('equal_length', 2.0)])
        self.assertEqual(slips(text, figure({'A': (0, 0), 'B': (10, 0), 'C': (0, 10.8)})), [('equal_length', 0.8)])

    def test_point_on_a_circle(self):
        text = '원 O 위의 점 P'
        on = figure({'O': (20, 20), 'P': (30, 20)}, [((20, 20), 10)])
        self.assertEqual(slips(text, on), [])
        self.assertEqual(slips(text, figure({'O': (20, 20), 'P': (31, 20)}, [((20, 20), 10)])), [('on_circle', 1.0)])
        # the circle is the one centred at O, not the nearer small one
        two = figure({'O': (20, 20), 'P': (26, 20)}, [((20, 20), 10), ((20, 20), 5)])
        self.assertEqual(slips('원 위의 점 P', two), [('on_circle', 1.0)])

    def test_centre_diameter_and_tangent(self):
        fig = figure({'O': (20, 20), 'A': (10, 20), 'B': (30, 20), 'C': (0, 30), 'D': (40, 30)}, [((20, 20), 10)])
        self.assertEqual(slips(r'$\overline{AB}$를 지름으로 하는 원 O에 $\overline{CD}$는 접선이다.', fig), [])
        off = figure({'O': (21.5, 20), 'A': (10, 20), 'B': (30, 18), 'C': (0, 31.2), 'D': (40, 31.2)}, [((20, 20), 10)])
        self.assertEqual(sorted(k for k, _ in slips(r'$\overline{AB}$를 지름으로 하는 원 O에 $\overline{CD}$는 접선이다.', off)),
                         ['center', 'diameter', 'tangent'])

    def test_incircle_that_touches_one_side_only(self):
        # 광남중 q16 in one run: the circle sat on BC and missed the other two sides.
        tri = {'A': (10, 0), 'B': (0, 30), 'C': (40, 30)}
        self.assertEqual([k for k, _ in slips('삼각형 ABC의 내접원', figure(tri, [((17, 24), 6)]))], ['incircle'])

    def test_feet_intersections_and_points_on_lines(self):
        pts = {'A': (10, 0), 'B': (0, 20), 'C': (40, 20), 'H': (10, 20), 'D': (20, 20), 'E': (15, 10)}
        self.assertEqual(slips(r'꼭짓점 A에서 $\overline{BC}$에 내린 수선의 발을 H라 하자. $\overline{BC}$ 위의 점 D, $\overline{AD}$와 $\overline{BC}$의 교점 D', figure(pts)), [])
        pts.update(H=(12, 20), D=(20, 21.5))
        self.assertEqual(sorted(k for k, _ in slips(r'꼭짓점 A에서 $\overline{BC}$에 내린 수선의 발을 H라 하자. $\overline{BC}$ 위의 점 D', figure(pts))),
                         ['foot', 'on_segment'])

    def test_shapes_and_right_angles(self):
        square = {'A': (0, 0), 'B': (0, 20), 'C': (20, 20), 'D': (20, 0)}
        self.assertEqual(slips('정사각형 ABCD', figure(square)), [])
        self.assertEqual([k for k, _ in slips('정사각형 ABCD', figure({**square, 'C': (23, 20)}))], ['square'])
        self.assertEqual(slips(r'$\angle B=90^\circ$인 직각삼각형 ABC', figure({'A': (0, 0), 'B': (0, 20), 'C': (30, 20)})), [])
        self.assertEqual([k for k, _ in slips(r'$\angle B=90^\circ$인 직각삼각형 ABC', figure({'A': (3, 0), 'B': (0, 20), 'C': (30, 20)}))], ['right_angle'])
        self.assertEqual(slips('평행사변형 ABCD', figure({'A': (5, 0), 'B': (0, 20), 'C': (30, 20), 'D': (35, 0)})), [])

    def test_unknown_points_are_not_guessed(self):
        self.assertEqual(slips(r'$\overline{BC}$의 중점을 M이라 하자.', figure({'B': (0, 0), 'C': (40, 0)})), [])
        self.assertEqual(slips('원 O 위의 점 P', figure({'P': (30, 20)})), [])  # no circle drawn

    def test_prime_names(self):
        fig = figure({'O': (10, 10), 'Op': (40, 10), 'P': (47, 10)}, [((10, 10), 5), ((40, 10), 5)])
        self.assertEqual(slips("원 O' 위의 점 P", fig), [('on_circle', 2.0)])
        self.assertEqual(fig.exact("O''"), None)

    def test_point_taken_from_its_label_only_when_the_match_is_clear(self):
        text = '원 위의 점 P'
        circle = [((20, 20), 10)]
        clear = figure({}, circle, labels={'P': (33, 18)}, vertices=[(31, 20), (5, 5)])
        self.assertEqual(slips(text, clear), [('on_circle', 1.0)])
        # v2.7.5 진성고 q20: the label sat between a line end and the crossing that really was P
        unclear = figure({}, circle, labels={'P': (33, 18)}, vertices=[(31, 20), (30, 20)])
        self.assertEqual(slips(text, unclear), [])
        # a label match that is far off is a wrong match, an exact name that is far off is an error
        far = figure({}, circle, labels={'P': (33, 18)}, vertices=[(36, 18)])
        self.assertEqual(slips(text, far), [])
        self.assertEqual(slips(text, figure({'P': (36, 18)}, circle)), [('on_circle', 6.12)])

    def test_crossings_are_candidates_for_labelled_points(self):
        found = rc._crossings([((0, 0), (40, 0)), ((20, -10), (20, 10))], [{'c': (20, 0), 'r': 5, 'start': 0.0, 'sweep': 2 * math.pi}])
        self.assertIn((20.0, 0.0), [(round(x, 6), round(y, 6)) for x, y in found])
        self.assertEqual(sorted((round(x, 6), round(y, 6)) for x, y in found if abs(y) < 1e-6 and x != 20), [(15.0, 0.0), (25.0, 0.0)])

    def test_messages_fit_a_reply_line(self):
        found = rc.check(relations(r'꼭짓점 A에서 $\overline{BC}$에 내린 수선의 발을 H라 하자.'),
                         figure({'A': (10, 0), 'B': (0, 20), 'C': (40, 20), 'H': (13, 20)}))
        self.assertEqual(len(found), 1)
        self.assertLessEqual(len(found[0]['text']), 120)
        self.assertIn('A에서 BC에 내린 수선의 발 H', found[0]['text']); self.assertIn('3.0mm', found[0]['text'])
        self.assertIn('($(B)!(A)!(C)$)', found[0]['text'])


def render(picture, width=50):
    spec = importlib.util.spec_from_file_location('tr', single.shared.SKILL / 'runtime/tikz_render.py')
    tr = importlib.util.module_from_spec(spec); spec.loader.exec_module(tr)
    d = Path(tempfile.mkdtemp())
    (d / 'f.tex').write_text(single.diagram_document(picture, width), encoding='utf-8'); tr.render(d / 'f.tex', d / 'out')
    return d / 'out' / 'diagram.pdf'


TRIANGLE = (r'\begin{tikzpicture}\coordinate (A) at (1,3);\coordinate (B) at (0,0);\coordinate (C) at (4,0);\coordinate (M) at (MID);'
            r'\draw (A)--(B)--(C)--cycle;\draw (A)--(M);\node[above] at (A) {$A$};\node[below left] at (B) {$B$};'
            r'\node[below right] at (C) {$C$};\node[below] at (M) {$M$};\end{tikzpicture}')
STEM = r'삼각형 ABC에서 $\overline{BC}$의 중점을 M이라 하자.'
CIRCLE = (r'\begin{tikzpicture}\coordinate (O) at (0,0);\coordinate (P) at (WHERE);\draw (O) circle (2);\draw (O)--(P);'
          r'\fill (O) circle (1.2pt);\fill (P) circle (1.2pt);\node[below left] at (O) {$O$};\node[right=2pt] at (P) {$P$};\end{tikzpicture}')


@unittest.skipUnless(HAS_TEX, 'no TeX engine')
class RenderedRelationTests(unittest.TestCase):
    def test_eyeballed_midpoint_is_reported_and_computed_one_is_not(self):
        self.assertEqual(rc.relation_warnings(render(TRIANGLE.replace('MID', '$(B)!0.5!(C)$')), relations(STEM)), [])
        found = rc.relation_warnings(render(TRIANGLE.replace('MID', '2.3,0')), relations(STEM))
        self.assertEqual(len(found), 1)
        self.assertIn('BC의 중점 M', found[0])

    def test_unnamed_points_are_found_through_their_labels(self):
        picture = (r'\begin{tikzpicture}\draw (0,0) circle (2);\fill (0,0) circle (1.2pt);\draw (0,0)--(2.5,0);\fill (2.5,0) circle (1.2pt);'
                   r'\node[below left] at (0,0) {$O$};\node[right=2pt] at (2.5,0) {$P$};\end{tikzpicture}')
        found = rc.relation_warnings(render(picture), relations('원 O 위의 점 P'))
        self.assertEqual(len(found), 1); self.assertIn('원 위의 점 P', found[0])

    def test_position_shown_once_then_kept_as_note_until_fixed(self):
        bad = render(CIRCLE.replace('WHERE', '2.5,0')); good = render(CIRCLE.replace('WHERE', '2,0'))
        item = lambda pdf: {'id': 'q5-figure-1', 'question_id': 'q5', 'status': 'rendered', 'png': str(pdf.with_name('diagram.png'))}
        state = {'reviews': {'3': {'status': 'passed', 'issues': []}}, 'pages': {'3': {'figure_relations': {'q5-figure-1': relations('원 O 위의 점 P')}}}}
        first = {}; single.relation_slips(state, 3, [item(bad)], first)
        self.assertEqual(len(first['q5-figure-1']), 1)
        self.assertNotIn('원본도', first['q5-figure-1'][0])
        again = {}; single.relation_slips(state, 3, [item(bad)], again)
        self.assertEqual(again, {})
        notes = single.current_notes(state)
        self.assertTrue(notes[0]['issues'][0].startswith('도형: q5 글의 조건 "원 위의 점 P"'), notes)
        row = single.note_row(notes[0]['issues'][0], '3쪽', {'q5': '5번'})
        self.assertEqual((row['question'], row['kind']), ('5번', '도형'))
        single.relation_slips(state, 3, [item(good)], {})
        self.assertEqual(single.current_notes(state), [])

    def test_length_condition_is_said_once_and_leaves_no_note(self):
        """Sources are often not to scale: a midpoint drawn off may be a faithful copy."""
        bad = render(TRIANGLE.replace('MID', '2.3,0'))
        item = {'id': 'q5-figure-1', 'question_id': 'q5', 'status': 'rendered', 'png': str(bad.with_name('diagram.png'))}
        state = {'reviews': {'3': {'status': 'passed', 'issues': []}}, 'pages': {'3': {'figure_relations': {'q5-figure-1': relations(STEM)}}}}
        first = {}; single.relation_slips(state, 3, [item], first)
        self.assertEqual(len(first['q5-figure-1']), 1)
        self.assertIn('BC의 중점 M', first['q5-figure-1'][0]); self.assertTrue(first['q5-figure-1'][0].endswith('원본도 그렇게 그려졌으면 그대로 둔다.'))
        self.assertLessEqual(len(first['q5-figure-1'][0]), 120)
        again = {}; single.relation_slips(state, 3, [item], again)
        self.assertEqual(again, {}); self.assertEqual(single.current_notes(state), [])


class SubmissionTests(unittest.TestCase):
    """The relations of each figure are read once, when the page's reading is accepted."""

    def setUp(self):
        self.case = workflow.SingleReviewTests(); self.case.setUp(); self.addCleanup(self.case.doCleanups)
        self.root, self.call = self.case.root, self.case.call
        folder = Path(self.case.temp.name) / 'renderer'; folder.mkdir()
        runtime = fixtures.FigureRetryTests().fixture(folder)[4]
        patcher = patch.object(single.batch.runpy, 'run_path', return_value=runtime); patcher.start(); self.addCleanup(patcher.stop)

    def test_relations_are_stored_with_the_page(self):
        markdown = workflow.MD.replace('1. 값은 $x-1$?', '1. 선분 AB의 중점 M에 대하여 값은 $x-1$?\n\n![](figure:f1)')
        self.assertEqual(self.call('submit_reading', page=1, markdown=markdown)['status'], 'ready_for_figures')
        stored = job.load_json(Path(self.root) / 'mcp/state.json')['pages']['1']['figure_relations']
        self.assertEqual([(r['kind'], r['points']) for r in stored['f1']], [('midpoint', ['M', 'A', 'B'])])

    def test_render_without_stated_relations_is_untouched(self):
        self.call('submit_reading', page=1, markdown=workflow.MD.replace('1. 값은 $x-1$?', '1. 값은 $x-1$?\n\n![](figure:f1)'))
        tex = r'\begin{tikzpicture}\draw (0,0)--(1,1);\end{tikzpicture}'
        result = self.call('render_figures', page=1, figures=[{'id': 'f1', 'question_id': 'q1', 'latex': tex, 'width_mm': 40}])
        self.assertEqual(result['status'], 'pending_review', result)
        self.assertNotIn('relation_notes', job.load_json(Path(self.root) / 'mcp/state.json'))


if __name__ == '__main__':
    unittest.main()
