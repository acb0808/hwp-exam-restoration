"""v2.8.0 engine checks made without a model: the row plan estimated before the first export, the printed
answer table compared with the answer blocks, length curves bowed into the figure, hand-drawn angle arcs."""
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from restoration_answers import answer_table_check, table_rows
from restoration_figure_geometry import inward_length_curves, length_curve_findings, length_label_findings
from restoration_figure_lint import angle_arc_warnings, figure_warnings
from restoration_preplan import content_height_mm, plan_rows
import test_pdf2hwp_grid as grid_tests

HP = 'http://www.hancom.co.kr/hwpml/2011/paragraph'


def text_block(chars):
    return [{'id': 'body', 'kind': 'paragraph', 'runs': [{'kind': 'text', 'text': '가' * chars}]}]


def estimated(qid, content, rows, figures=0, row_mm=45.7):
    return {'block_id': qid, 'content_mm': content, 'available_mm': rows * row_mm - 1, 'cell_mm': rows * row_mm, 'figures_mm': figures, 'rows': rows}


class PreplanTests(unittest.TestCase):
    Q = {'font_pt': 8, 'font_family': '바탕'}

    def test_height_counts_wrapped_lines_and_figures(self):
        one = content_height_mm(text_block(10), self.Q, 90)
        self.assertAlmostEqual(content_height_mm(text_block(200), self.Q, 90) / one, 7, delta=1)   # 200 wide characters of 2.8mm in 90mm
        figure = [{'id': 'f', 'kind': 'paragraph', 'runs': [], 'figure': {'size_mm': [40, 50], 'offset_mm': [0, 0]}}]
        self.assertGreater(content_height_mm(figure, self.Q, 90), 51)
        box = [{'id': 'b', 'kind': 'box', 'title': '보기', 'padding_mm': [2, 1.5, 2, 1.5], 'content': text_block(10)}]
        self.assertGreater(content_height_mm(box, self.Q, 90), one + 3)

    def test_default_rows_stay_unless_a_question_certainly_overflows(self):
        self.assertIsNone(plan_rows([estimated('q1', 60, 3), estimated('q2', 100, 3)]))
        self.assertIsNone(plan_rows([estimated('q1', 60, 3), estimated('q2', 141, 3)]))  # over its room, but within the estimate's error
        self.assertEqual(plan_rows([estimated('q1', 60, 3), estimated('q2', 170, 3)]), [2, 4])

    def test_nothing_is_planned_when_no_split_fits(self):
        self.assertIsNone(plan_rows([estimated('q1', 160, 3), estimated('q2', 170, 3)]))

    def test_a_long_question_gets_its_rows_in_the_first_build(self):
        case = grid_tests.GridTests()
        page = case.page(3, 2)
        page['questions'][0]['content'] = text_block(900)
        with tempfile.TemporaryDirectory() as d:
            receipt, _ = case.build(page, d)
        grid = receipt['pages'][0]['objects'][0]
        left = [m['row_span'] for m in grid['merges'] if m['column'] == 0]
        self.assertGreater(left[0], 2)   # the default split gives each of the three questions two rows
        self.assertEqual(sum(left), 6)
        self.assertEqual([m['row_span'] for m in grid['merges'] if m['column'] == 1], [3, 3])
        self.assertEqual(grid['preplanned_columns'], ['L'])

    def test_a_measured_plan_is_not_replaced_by_the_estimate(self):
        case = grid_tests.GridTests()
        page = case.page(3, 2)
        page['questions'][0]['content'] = text_block(900)
        page['row_plan'] = {'L0': 4, 'L1': 1, 'L2': 1, 'R0': 3, 'R1': 3}
        with tempfile.TemporaryDirectory() as d:
            receipt, _ = case.build(page, d)
        grid = receipt['pages'][0]['objects'][0]
        self.assertEqual([m['row_span'] for m in grid['merges'] if m['column'] == 0], [4, 1, 1])
        self.assertNotIn('preplanned_columns', grid)


ROWS = [{'kind': 'choice', 'label': '1', 'answer': '③'}, {'kind': 'choice', 'label': '2', 'answer': '①'},
        {'kind': 'written', 'label': '서답형1', 'answer': r'$x=\frac{1}{2}$ 또는 $x=3$'}]


def table_file(path, rows, change=None, equation_width=3000):
    """A HWPX holding only the answer table, as question_flow writes it."""
    cells = []
    for index, text in enumerate(t for items, _ in table_rows(rows, 180) for t, *_ in items):
        text = change(index, text) if change else text
        runs = []
        for i, part in enumerate(text.split('$')):
            if i % 2: runs.append(f'<hp:equation><hp:sz width="{equation_width}" height="900"/><hp:script>{part.replace("\\", "")}</hp:script></hp:equation>')
            else: runs.append(f'<hp:t>{part}</hp:t>')
        cells.append('<hp:tr><hp:tc><hp:subList><hp:p><hp:run>' + ''.join(runs) + '</hp:run></hp:p></hp:subList>'
                     '<hp:cellSz width="9000" height="3000"/><hp:cellMargin left="567" right="567" top="567" bottom="567"/></hp:tc></hp:tr>')
    xml = f'<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" xmlns:hp="{HP}"><hp:p><hp:run><hp:tbl colCnt="8">' + ''.join(cells) + '</hp:tbl></hp:run></hp:p></hs:sec>'
    with zipfile.ZipFile(path, 'w') as z: z.writestr('Contents/section0.xml', xml)
    return path


class AnswerTableTests(unittest.TestCase):
    def check(self, **printed):
        with tempfile.TemporaryDirectory() as d:
            return answer_table_check(table_file(Path(d) / 'built.hwpx', ROWS), table_file(Path(d) / 'printed.hwpx', ROWS, **printed), ROWS)

    def test_table_matching_the_answer_blocks_is_verified(self):
        result = self.check()
        self.assertEqual((result['verified'], result['problems']), (True, []))
        self.assertEqual(result['cells'], 12)   # heading, four number/answer pairs, heading, number and answer

    def test_changed_answer_or_equation_is_reported(self):
        self.assertIn('text differs', self.check(change=lambda i, t: '④' if t == '③' else t)['problems'][0])
        self.assertIn('equation changed', self.check(change=lambda i, t: t.replace('frac{1}{2}', 'frac{1}{3}'))['problems'][0])

    def test_equation_wider_than_its_cell_is_reported(self):
        result = self.check(equation_width=9000)
        self.assertFalse(result['verified'])
        self.assertIn('wider than its cell', result['problems'][0])

    def test_missing_table_is_not_verified(self):
        with tempfile.TemporaryDirectory() as d:
            empty = Path(d) / 'empty.hwpx'
            with zipfile.ZipFile(empty, 'w') as z: z.writestr('Contents/section0.xml', f'<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" xmlns:hp="{HP}"/>')
            self.assertEqual(answer_table_check(empty, empty, ROWS), {'verified': False, 'problems': ['answer_table_not_found']})


def triangle(curve_top):
    """Triangle A(0,30) B(40,30) C(20,0) in page mm (y grows downwards) with a dashed curve from A to B through curve_top."""
    a, b, c = (0, 30), (40, 30), (20, 0)
    sides = [{'p': p, 'q': q, 'dashed': False, 'symbol': False} for p, q in ((a, b), (b, c), (c, a))]
    points = [a] + [(40 * t / 16, 30 + (curve_top - 30) * 4 * (t / 16) * (1 - t / 16)) for t in range(1, 16)] + [b]
    return {'segments': sides, 'arcs': [], 'glyphs': [], 'marks': [], 'curves': [{'points': points, 'dashed': True, 'only': True}]}


class LengthCurveTests(unittest.TestCase):
    def test_curve_bowed_into_the_figure_is_found(self):
        self.assertEqual(len(inward_length_curves(triangle(24))), 1)    # towards C
        self.assertEqual(inward_length_curves(triangle(36)), [])        # away from the figure

    def test_declared_inside_curve_is_not_reported(self):
        self.assertIn('안쪽으로', length_curve_findings(triangle(24))[0]['text'])
        self.assertEqual(length_curve_findings(triangle(24), declared_inside=1), [])

    def test_curve_between_inner_lines_is_left_alone(self):
        geo = triangle(24)
        geo['segments'].append({'p': (-5, 45), 'q': (45, 45), 'dashed': False, 'symbol': False})  # figure on the other side too
        self.assertEqual(inward_length_curves(geo), [])

    def test_hidden_edges_and_flat_lines_are_not_length_curves(self):
        geo = triangle(24)
        geo['curves'][0]['points'] = [(p[0], 30 + (p[1] - 30) * 4) for p in geo['curves'][0]['points']]   # a half ellipse
        self.assertEqual(inward_length_curves(geo), [])
        geo = triangle(24)
        geo['curves'][0]['points'] = [(p[0], p[1] + 8) for p in geo['curves'][0]['points']]              # ends off the side
        self.assertEqual(inward_length_curves(geo), [])


class LengthLabelTests(unittest.TestCase):
    """A length printed with its unit while the figure has no dashed curve: the source had one in all 25 archived cases."""
    TEX = (r'\draw (A)--(B)--(C)--cycle; \ExamLabel[left]{A}{$A$} \node at ($(A)!0.5!(B)$) [below] {$10\,\mathrm{cm}$};'
           r'\node[right] at (1,1) {$4 \mathrm{cm}$}; \draw[dashed] (O)--(E);')

    def plain(self):
        geo = triangle(36); geo['curves'] = []; return geo

    def test_unit_lengths_without_any_dashed_curve_are_reported_once(self):
        [found] = length_label_findings(self.plain(), self.TEX)
        self.assertEqual(found['kind'], 'length_label')
        self.assertTrue(found['text'].startswith('길이 표시 호 없음: 단위가 붙은 길이 라벨(10cm, 4cm)'))
        self.assertIn(r'\ExamLengthArc{A}{B}{라벨}', found['text'])

    def test_only_a_drawn_dashed_curve_silences_it(self):
        self.assertEqual(length_label_findings(triangle(36), self.TEX), [])   # some length arc is drawn
        # No comment switches it off: a producer offered one used it on a source that had the arc.
        self.assertEqual(len(length_label_findings(self.plain(), '% no-length-arc\n' + self.TEX)), 1)

    def test_labels_without_a_unit_and_missing_tex_are_left_alone(self):
        self.assertEqual(length_label_findings(self.plain(), r'\node at (1,1) {$6$}; \node at (2,1) {$30^\circ$}; \node at (0,0) {$m$};'), [])
        self.assertEqual(length_label_findings(self.plain(), r'% \node at (1,1) {$6\mathrm{cm}$};'), [])
        self.assertEqual(length_label_findings(self.plain(), None), [])


class NoteFoldTests(unittest.TestCase):
    """An engine-measured slip of a figure the reviewer noted too is one row of the notes table, not two."""
    ENGINE = '도형: q20 자동 측정 — 길이 표시 호 없음: 단위가 붙은 길이 라벨(14cm)이 있는데 점선 호가 하나도 없습니다'

    def notes(self, reviewer):
        import restoration_single as single
        state = {'reviews': {'5': {'status': 'passed_with_notes' if reviewer else 'passed', 'issues': reviewer}},
                 'geometry_notes': {'5': {'q20-figure-1': [self.ENGINE]}}}
        return single.current_notes(state)[0]['issues']

    def test_measured_slip_joins_the_reviewers_note_of_the_same_figure(self):
        self.assertEqual(self.notes(['도형: q20 원본 변 BC 점선 치수 곡선(14cm) / 출력 14cm 텍스트만', '도형: q21 각 호 위치']),
                         ['도형: q20 원본 변 BC 점선 치수 곡선(14cm) / 출력 14cm 텍스트만 (엔진 측정: 길이 표시 호 없음)', '도형: q21 각 호 위치'])

    def test_it_stays_its_own_row_without_a_figure_note_of_that_question(self):
        self.assertEqual(self.notes([]), [self.ENGINE])
        self.assertEqual(self.notes(['도형: q2 눈금 개수', '경미: q20 문구 차이']), ['도형: q2 눈금 개수', '경미: q20 문구 차이', self.ENGINE])


class NotesBuildTests(unittest.TestCase):
    """All pages passed with notes: hwp_finish_review starts the build of the notes page, the main agent does not ask."""
    NOTES = {'status': 'notes_build_required', 'review_notes': [{'page': 3, 'issues': ['도형: q9 눈금 개수']}]}

    def run_with(self, state, reply):
        from unittest import mock
        import restoration_job as job
        import restoration_single as single
        calls = []
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root / 'mcp').mkdir(); job.save_json(root / 'mcp/state.json', state)
            def serial(action, params, *rest): calls.append((action, params)); return reply
            with mock.patch.object(single, '_serial', serial):
                return single._notes_build(root, dict(self.NOTES), 0.0, 'single'), calls, str(root)

    def test_the_build_runs_with_the_parameters_of_the_last_build(self):
        out, calls, root = self.run_with({'build_params': {'output': 'C:/o.hwpx', 'title': '수학'}}, {'status': 'complete', 'artifacts': {'hwpx': 'x'}})
        self.assertEqual(calls, [('build', {'job': root, 'output': 'C:/o.hwpx', 'title': '수학'})])
        self.assertEqual((out['status'], out['review_notes'], out['artifacts']), ('complete', self.NOTES['review_notes'], {'hwpx': 'x'}))

    def test_the_caller_builds_when_it_cannot_start(self):
        self.assertEqual(self.run_with({}, {'status': 'complete'})[0], self.NOTES)                          # a job built by an earlier runtime
        self.assertEqual(self.run_with({'build_params': {'output': 'o'}}, {'status': 'failed', 'message': 'existing_hwp_session'})[0], self.NOTES)


class RenderWaitTests(unittest.TestCase):
    def test_a_render_may_wait_longer_than_a_build_but_under_the_client_limit(self):
        import os
        from unittest.mock import patch
        import restoration_single as single
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('HWP_MCP_WAIT_SECONDS', None)
            self.assertEqual(single.wait_seconds(), 30)
            self.assertEqual(single.wait_seconds(single.RENDER_WAIT_SECONDS), 45)   # opencode drops the server at 60 s
        with patch.dict(os.environ, {'HWP_MCP_WAIT_SECONDS': '12'}):
            self.assertEqual(single.wait_seconds(single.RENDER_WAIT_SECONDS), 12)   # a host setting rules both


class AngleArcLintTests(unittest.TestCase):
    def test_small_hand_drawn_arcs_are_counted_once(self):
        tex = (r'\draw ($(A)+(0:0.6)$) arc (0:40:0.6);' '\n' r'\draw (B) ++(90:4mm) arc[start angle=90, end angle=150, radius=4mm];'
               '\n' r'% \draw (0.5,0) arc (0:30:0.5);')
        [warning] = angle_arc_warnings(tex)
        self.assertIn('2곳', warning)
        self.assertIn(r'\ExamAngle', warning)
        self.assertIn(warning, figure_warnings(tex))

    def test_labels_set_by_the_angle_helper_count_as_drawn(self):
        from restoration_figure_lint import label_warnings
        tex = ('% width_mm=45 labels=A,B,C,47°,2\n'
               r'\draw (A)--(B)--(C)--cycle; \ExamLabel[left]{A}{$A$}\ExamLabel[right]{B}{$B$}\ExamLabel[above]{C}{$C$}'
               r'\ExamAngle{C}{A}{B}{$47^\circ$} \ExamAngle[angle eccentricity=.6]{A}{C}{B}{$\bullet$} \node at ($(A)!0.5!(B)$) [below] {$2$};')
        self.assertEqual(label_warnings(tex), [])
        self.assertEqual(label_warnings(tex.replace('47°,', '')), ['labels drawn but not listed from the source: 47'])

    def test_figure_curves_and_helper_marks_are_not_reported(self):
        self.assertEqual(angle_arc_warnings(r'\draw (C) arc (0:180:1.8); \draw (B) arc[start angle=0, end angle=180, radius=\R];'), [])
        self.assertEqual(angle_arc_warnings(r'\ExamAngle{A}{B}{C}{$30^\circ$}'), [])


if __name__ == '__main__':
    unittest.main()
