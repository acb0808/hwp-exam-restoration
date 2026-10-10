"""v2.8.0: overflow refits made by the engine, short replies, reviewer submission, figure helpers."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import restoration_job as job
import restoration_single as single
from restoration_refit import column_plan, plans, refit
from restoration_reply import compact_reply, size
from test_review_notes import FIGURE_NOTE, ReviewNotesTests


def cell(qid, content, rows, figures=0, page=3, column=1, row_mm=45.7, continued=False):
    return {'page': page, 'block_id': qid, 'content_mm': content, 'available_mm': rows * row_mm - 1, 'cell_mm': rows * row_mm,
            'figures_mm': figures, 'continued': continued, 'rows': rows, 'column': column}


class RefitTests(unittest.TestCase):
    def test_every_plan_gives_each_question_a_row_and_six_together(self):
        for n in range(1, 7):
            found = plans(n)
            self.assertTrue(found)
            for rows in found:
                self.assertEqual((len(rows), sum(rows), min(rows) >= 1), (n, 6, True))
        self.assertEqual(len(plans(3)), 10)

    def test_overflowing_question_takes_a_row_from_the_emptiest_neighbour(self):
        cells = [cell('q11', 28.1, 2), cell('q12', 31.4, 2), cell('q13', 104.8, 2, figures=51.9, continued=True)]
        fit = {'question_cells': cells, 'issues': [{'page': 3, 'block_id': 'q13', 'code': 'question_cell_content_overflow'}]}
        result = refit(fit, {}, {})
        self.assertEqual(result['row_plans'], {'3': {'q11': 1, 'q12': 2, 'q13': 3}})
        self.assertEqual(result['figure_scales'], {})
        self.assertEqual(result['log'], ['3쪽 q11: 칸 2행→1행', '3쪽 q13: 칸 2행→3행'])

    def test_figures_shrink_only_when_no_row_split_fits_and_never_below_the_floor(self):
        rows, shrink = column_plan([cell('q1', 88, 2), cell('q2', 88, 2), cell('q3', 96, 2, figures=50)], {})
        self.assertEqual(rows, [2, 2, 2])
        self.assertEqual(list(shrink), ['q3'])
        self.assertTrue(.8 <= shrink['q3'] < 1)
        self.assertIsNone(column_plan([cell('q1', 88, 2), cell('q2', 88, 2), cell('q3', 130, 2, figures=50)], {}))
        self.assertIsNone(column_plan([cell('q1', 88, 2), cell('q2', 88, 2), cell('q3', 96, 2, figures=0)], {}))

    def test_the_same_plan_twice_is_not_tried_again(self):
        cells = [cell('q11', 28.1, 1), cell('q12', 31.4, 2), cell('q13', 100, 3, continued=True)]  # measured to fit, yet continued
        fit = {'question_cells': cells, 'issues': [{'page': 3, 'block_id': 'q13', 'code': 'question_cell_content_overflow'}]}
        self.assertIsNone(refit(fit, {'3': {'q11': 1, 'q12': 2, 'q13': 3}}, {}))

    def test_overflow_without_a_remembered_build_still_reports_the_question(self):
        with tempfile.TemporaryDirectory() as d:
            receipt = Path(d) / 'native.json'
            job.save_json(receipt, {'status': 'failed', 'error': 'native_page_count_differs_from_source', 'content_fit': {
                'question_cells': [], 'issues': [{'page': 3, 'block_id': 'q13', 'code': 'question_cell_content_overflow',
                                                  'actual_mm': 104.8, 'available_mm': 90.4}]}})
            result = single.collect_native(Path(d), {'native_run': {'receipt': str(receipt), 'returncode': 2, 'log': 'x.log'}})
        self.assertEqual((result['overflow_questions'], result['overflow_pages']), (['q13'], [3]))
        self.assertEqual(result['overflow_mm'], [{'page': 3, 'question_id': 'q13', 'content_mm': 104.8, 'room_mm': 90.4}])


class FitMeasureTests(unittest.TestCase):
    HP = 'http://www.hancom.co.kr/hwpml/2011/paragraph'

    def measure(self, paragraphs):
        from xml.etree import ElementTree as ET
        from restoration_fit import content_height
        body = ''.join('<hp:p>' + extra + '<hp:linesegarray>' + ''.join(f'<hp:lineseg vertpos="{a}" vertsize="{b}"/>' for a, b in lines)
                       + '</hp:linesegarray></hp:p>' for lines, extra in paragraphs)
        node = ET.fromstring(f'<hp:tc xmlns:hp="{self.HP}"><hp:subList>{body}</hp:subList></hp:tc>')
        return content_height(node, list(node.iter('{' + self.HP + '}lineseg')))

    def test_plain_cell_is_its_last_line_bottom(self):
        self.assertEqual(self.measure([([(0, 800), (1300, 800)], '')]), (2100, False))

    def test_cell_continued_on_the_next_page_adds_its_parts(self):
        self.assertEqual(self.measure([([(0, 800), (20000, 900)], ''), ([(0, 800), (1300, 900)], '')]), (20900 + 2200, True))

    def test_floating_figure_at_the_end_counts_to_its_bottom_edge(self):
        picture = '<hp:run><hp:pic><hp:sz width="14000" height="12000"/><hp:pos treatAsChar="0" vertOffset="0"/></hp:pic></hp:run>'
        self.assertEqual(self.measure([([(0, 800)], ''), ([(1300, 800)], picture)]), (13300, False))


class ReplyTests(unittest.TestCase):
    def test_review_reply_names_pages_instead_of_listing_tasks(self):
        tasks = [{'page': n, 'source_image': 'C:/job/pages/page-%04d.png' % n,
                  'output_image': 'C:/temp/hwp-single-0123456789abcdef/review-inputs/page-%04d.png' % n,
                  'figure_sheet': 'C:/job/mcp/evidence/%064d.png' % n, 'source_size_px': [1654, 2356], 'output_size_px': [1653, 2337]}
                 for n in range(1, 8)]
        result = {'status': 'pending_review', 'review_tasks': tasks, 'report_path': 'C:/job/review/round-01.md', 'next_action': 'x' * 400}
        short = compact_reply('build', result)
        self.assertNotIn('review_tasks', short)
        self.assertEqual(short['review_pages'], list(range(1, 8)))
        self.assertEqual(short['report_path'], result['report_path'])
        self.assertIn('report_path', short['next_action'])
        self.assertLess(size(short), 1200)
        self.assertIn('review_tasks', result)

    def test_many_equation_errors_share_one_hint(self):
        hint = '표시한 간격 명령은 HWP에서 근사되어 엄격 검증이 거부했습니다. ' * 3
        errors = [{'question_id': f'q{i}', 'location': f'q{i}.content[0]', 'latex': r'4\,x',
                   'message': 'equation_requires_successful_lossless_parse: equation contains approximations',
                   'engine_diagnostics': [{'code': 'spacing_approximation', 'message': 'TeX spacing units are approximated', 'severity': 'warning',
                                           'start': 1, 'end': 3, 'token': r'\,', 'span_unit': 'unicode_codepoint'}],
                   'hint': hint, 'field': 'content', 'line': 10 + i, 'source_line': 10 + i, 'source_location': 'equation'} for i in range(17)]
        result = {'status': 'failed', 'error_kind': 'input', 'errors': errors, 'next_action': 'fix'}
        short = compact_reply('submit_reading', result)
        self.assertGreater(size(result), 9000)
        self.assertLess(size(short), 6000)
        self.assertEqual(short['errors'][0]['hint'], hint)
        self.assertEqual(short['errors'][5]['hint'], '같은 안내: 1번째 오류')
        self.assertEqual([e['line'] for e in short['errors']], list(range(10, 27)))
        self.assertEqual(short['errors'][3]['at'], r'\,')
        small = {'status': 'failed', 'errors': errors[:1]}
        self.assertIs(compact_reply('submit_reading', small), small)


class EquationRuleTests(unittest.TestCase):
    def script(self, latex):
        from restoration_compiler import _studio_equation
        return _studio_equation(latex, 'q1')['selected_script']

    def test_thin_space_before_a_unit_prints_like_the_plain_unit(self):
        self.assertEqual(self.script(r'4\,\mathrm{cm}'), self.script(r'4 \mathrm{cm}'))
        self.assertEqual(self.script(r'x=4\;\text{cm}^2'), self.script(r'x=4 \text{cm}^2'))
        for latex in (r'x\,\text{또는}\,y', r'x\quad y'):
            with self.assertRaises(ValueError):
                self.script(latex)

    def test_widehat_over_point_names_asks_for_arc_or_angle(self):
        from restoration_author_help import equation_guidance
        for latex in (r'\widehat{AB}', r'\widehat{\mathrm{ABC}}=3'):
            with self.assertRaises(ValueError) as caught:
                self.script(latex)
            hint = equation_guidance(latex, caught.exception)['hint']
            self.assertIn(r'\overparen{AB}', hint)
            self.assertIn(r'\angle ABC', hint)
        self.assertEqual(self.script(r'\hat{AB}'), 'hat {A B}')
        self.assertEqual(self.script(r'\widehat{a+b}'), 'hat {a + b}')

    def test_arc_spellings_and_named_replacements(self):
        from restoration_author_help import equation_guidance
        for latex in (r'\wideparen{AB}', r'\overset{\frown}{AB}', r'\overarc{AB}'):
            self.assertEqual(self.script(latex), 'arch {A B}')
        with self.assertRaises(ValueError) as caught:
            self.script(r'1,2,\dots,n')
        self.assertIn(r'\cdots', equation_guidance(r'1,2,\dots,n', caught.exception)['hint'])


class ReviewSubmissionTests(ReviewNotesTests):
    """The reviewer submits verdicts itself; the main agent confirms with the reviewer's ID only."""

    def pending(self):
        return [t['page'] for t in self.call('status')['review_tasks']]

    def test_submitted_review_is_confirmed_without_repeating_verdicts(self):
        pages = self.pending()
        rows = [{'page': pages[0], 'status': 'passed_with_notes', 'issues': [FIGURE_NOTE], 'record': 'q1 원본 3+2 / 출력 3+2'}]
        rows += [{'page': n, 'status': 'passed', 'issues': []} for n in pages[1:]]
        sent = self.call('submit_review', reviews=rows)
        self.assertEqual(sent['status'], 'review_recorded', sent)
        report = Path(sent['report_path']).read_text(encoding='utf-8')
        self.assertIn('- 판정: passed_with_notes', report)
        self.assertIn(FIGURE_NOTE, report)
        self.assertIn('q1 원본 3+2 / 출력 3+2', report)
        result = self.call('finish_review', reviewer_id='reviewer', spawn_evidence='spawned reviewer')
        self.assertEqual(result['status'], 'notes_build_required', result)
        self.assertEqual(result['review_notes'], [{'page': pages[0], 'issues': [FIGURE_NOTE]}])

    def test_untagged_issue_and_missing_page_are_returned_to_the_reviewer(self):
        pages = self.pending()
        sent = self.call('submit_review', reviews=[{'page': pages[0], 'status': 'failed', 'issues': ['q1 윗줄이 없음']}])
        self.assertEqual((sent['status'], sent['message']), ('failed', 'invalid_review_submission'))
        fields = {c['field'] for c in sent['corrections']}
        self.assertEqual(fields, {'page', 'issues'})
        self.assertIsNone(job.load_json(self.root / 'mcp/state.json').get('review_submission'))

    def test_confirmation_without_a_submission_names_both_ways(self):
        result = self.call('finish_review', reviewer_id='reviewer', spawn_evidence='spawned reviewer')
        self.assertEqual(result['status'], 'failed')
        self.assertIn('hwp_submit_review', result['message'])

    def test_a_producer_cannot_confirm_a_submitted_review(self):
        self.call('submit_review', reviews=[{'page': n, 'status': 'passed', 'issues': []} for n in self.pending()])
        result = self.call('finish_review', reviewer_id='producer', spawn_evidence='spawned producer')
        self.assertEqual(result['status'], 'failed')
        self.assertIn('not_a_producer', result['message'])

    def test_report_lists_the_text_that_went_into_the_document(self):
        report = Path(self.call('status')['report_path'])
        text = report.read_text(encoding='utf-8')
        self.assertIn('- output_text: ', text)
        saved = Path(text.split('- output_text: ')[1].splitlines()[0])
        body = saved.read_text(encoding='utf-8')
        self.assertIn('### q1', body)
        self.assertNotIn('::: answer', body)

    def test_engine_checked_answer_table_is_not_read_from_its_image(self):
        from unittest import mock
        with mock.patch('restoration_answers.answer_table_check', return_value={'verified': True, 'cells': 8, 'problems': []}):
            current = self.call('status')
        answer = Path(current['report_path']).read_text(encoding='utf-8').split('(정답표)')[1]
        self.assertIn('- 표 대조: 엔진 확인 완료', answer)
        self.assertNotIn('- output_image:', answer)
        sent = self.call('submit_review', reviews=[{'page': t['page'], 'status': 'passed', 'issues': []} for t in current['review_tasks']])
        self.assertEqual(sent['status'], 'review_recorded', sent)
        self.assertEqual(self.call('finish_review', reviewer_id='reviewer', spawn_evidence='spawned reviewer')['status'], 'complete')

    def test_unchecked_answer_table_keeps_its_image_in_the_report(self):
        answer = Path(self.call('status')['report_path']).read_text(encoding='utf-8').split('(정답표)')[1]
        self.assertIn('- output_image:', answer)
        self.assertNotIn('표 대조', answer)

    def test_report_leaves_the_source_tiles_to_the_producers(self):
        # Reviewers found as many seeded errors without them, sooner, and open four files a page instead of ten.
        text = Path(self.call('status')['report_path']).read_text(encoding='utf-8')
        self.assertNotIn('tile-', text); self.assertIn('- output_text:', text); self.assertIn('- source_image:', text)

    def test_report_names_every_source_tile_as_a_file_when_asked(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {'HWP_REVIEW_TILES': '1'}):
            text = Path(self.call('status')['report_path']).read_text(encoding='utf-8')
        tiles = [Path(line.split(': ', 1)[1]) for line in text.splitlines() if line.startswith('  - ') and 'tile-' in line]
        self.assertEqual(len(tiles), 6)
        self.assertTrue(all(path.is_file() for path in tiles))


class FigureToolsTests(unittest.TestCase):
    def test_angle_and_length_arc_helpers_are_inlined_when_used(self):
        doc = single.diagram_document(r'\begin{tikzpicture}\coordinate (A) at (0,0);\coordinate (B) at (2,0);\coordinate (C) at (1,2);'
                                      r'\draw (A)--(B)--(C)--cycle;\ExamAngle{B}{A}{C}{$x$}\end{tikzpicture}')
        for command in ('ExamAngle', 'ExamLengthArc', 'ExamSideOf'):
            self.assertIn('\\newcommand{\\' + command + '}', doc)
        marks = single.TIKZ_HELPERS.read_text(encoding='utf-8')
        self.assertIn('current bounding box.center', marks)
        # The side of a length arc follows the centre of the named points: a bounding box centre lies on a right triangle's hypotenuse.
        self.assertIn(r'\newcommand{\ExamInnerSide}', marks)
        self.assertIn(r'\ExamInnerSide{#2}{#3}', marks)
        self.assertIn('inside/.style', marks)

    def test_source_box_grows_until_its_edges_are_blank(self):
        from PIL import Image, ImageDraw
        page = Image.new('RGB', (400, 400), 'white')
        ImageDraw.Draw(page).rectangle((100, 100, 300, 300), outline='black', width=3)
        self.assertEqual(single.grown_box(page, [150, 150, 60, 60]), (150, 150, 210, 210))  # blank edges: unchanged
        left, top, right, bottom = single.grown_box(page, [120, 60, 160, 240])              # bottom edge lies on the lower stroke
        self.assertEqual(top, 60)
        self.assertGreaterEqual(bottom, 303)  # the bottom edge lay on the lower stroke and moved past it
        self.assertIsNone(single.grown_box(page, [500, 500, 20, 20]))

    def test_tiles_cover_the_page_in_reading_order(self):
        import fitz
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'job'
            source = Path(d) / 'source.pdf'
            with fitz.open() as doc:
                doc.new_page()
                doc.save(source)
            result = single.dispatch('prepare', {'job': str(root), 'source': str(source), 'question_pages': [1], 'include_answers': False})
            self.assertEqual(result['status'], 'prepared', result)
            tiles = single.source_tiles(root, 1)
            self.assertEqual([t['name'] for t in tiles], ['왼쪽 위', '왼쪽 가운데', '왼쪽 아래', '오른쪽 위', '오른쪽 가운데', '오른쪽 아래'])
            width, height = single.info(root, 1)['source_size_px']
            self.assertEqual((tiles[0]['box'][0], tiles[0]['box'][1], tiles[-1]['box'][2], tiles[-1]['box'][3]), (0, 0, width, height))
            self.assertGreater(tiles[0]['box'][2], tiles[3]['box'][0])  # neighbours overlap
            task = Path(result['spawn_requests'][0]['task_path']).read_text(encoding='utf-8')
            self.assertIn('확대 조각', task)
            self.assertIn(tiles[4]['path'], task)


if __name__ == '__main__':
    unittest.main()
