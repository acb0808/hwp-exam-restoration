"""v2.7.6: only answer-changing issues trigger repairs; diagrams and cosmetics become a 검수 노트 page."""
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import restoration_job as job
import restoration_single as single
from restoration_batch import pages_digest, review_pack
from test_answer_sheet import MD

FIGURE_NOTE = '도형: q1 20° 라벨이 O 옆에 있음(원본은 D 근처)'
TEXT_ERROR = '오독: q2 분모 2가 3으로 출력됨'
FIGURE_REPAIR = '누락: q3 도형에서 부채꼴의 반지름 OP 실선 및 중심 O의 직각 표시 누락'
FIGURE_REPAIR_AS_NOTE = '도형: q3 도형에서 부채꼴의 반지름 OP 실선 및 중심 O의 직각 표시 누락'


class ReviewNotesTests(unittest.TestCase):
    def setUp(self):
        import fitz
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'job'
        source = Path(self.tmp.name) / 'source.pdf'
        with fitz.open() as doc:
            doc.new_page()
            doc.save(source)
        self.call('prepare', source=str(source), question_pages=[1], include_answers=True)
        self.call('assign', page=1, worker_id='producer', evidence='Actual fixture worker response: producer')
        self.assertEqual(self.call('submit_reading', page=1, markdown=MD)['status'], 'accepted')
        self.native()

    def call(self, action, **kwargs):
        return single.dispatch(action, {'job': str(self.root), **kwargs})

    def native(self, name='fixture'):
        """Native receipt whose PDF has one page per assembled page, like the real export."""
        import fitz
        pages = job.assemble(self.root)
        pdf = self.root / f'{name}.pdf'
        with fitz.open() as doc:
            for _ in pages:
                doc.new_page()
            doc.save(pdf)
        value = {'status': 'rendered', 'cleanup': 'closed_owned_session', 'job': str(self.root), 'page_count': len(pages), 'pages_sha256': pages_digest(pages),
                 'artifacts': {'pdf': {'path': str(pdf), 'sha256': job.digest(pdf)}}}
        value['review_inputs'] = review_pack(self.root, value, self.root / f'{name}-review')
        receipt = self.root / f'{name}-native.json'
        job.save_json(receipt, value)
        state = job.load_json(self.root / 'mcp/state.json')
        state['native_run'] = {'receipt': str(receipt), 'returncode': 0}
        job.save_json(self.root / 'mcp/state.json', state)
        return value

    def review(self, rows):
        tasks = {t['page']: t for t in self.call('status')['review_tasks']}
        paths = ' '.join(tasks[r['page']].get('source_image') or tasks[r['page']]['answer_reference'] for r in rows)
        paths += ' ' + ' '.join(tasks[r['page']]['output_image'] for r in rows)
        return self.call('finish_review', reviewer_id='reviewer', reviews=rows, review_evidence='reviewer ' + paths)

    def test_diagram_issue_passes_with_a_note_page_before_the_answer_sheet(self):
        # A reviewer that still says failed for a diagram-only difference gets no repair round.
        result = self.review([{'page': 1, 'status': 'failed', 'issues': [FIGURE_NOTE]},
                              {'page': 2, 'status': 'passed', 'issues': []}])
        self.assertEqual(result['status'], 'notes_build_required', result)
        self.assertNotIn('artifacts', result)
        self.assertEqual(result['review_notes'], [{'page': 1, 'issues': [FIGURE_NOTE]}])
        pages = job.assemble(self.root)
        self.assertEqual([p.get('role') for p in pages], [None, 'review_notes', 'answer_sheet'])
        self.assertEqual(pages[1]['note_rows'], [{'page': '1쪽', 'question': '1번', 'kind': '도형',
                                                  'text': '20° 라벨이 O 옆에 있음(원본은 D 근처)'}])
        self.assertEqual(self.call('status')['status'], 'notes_build_required')
        value = self.native('with-notes')
        self.assertEqual([p['page'] for p in value['review_inputs']['pages']], [1, 2])  # the notes page is never reviewed
        final = self.call('status')
        self.assertEqual(final['status'], 'complete', final)
        self.assertEqual(final['review_tasks'], [])
        self.assertEqual(final['review_notes'], [{'page': 1, 'issues': [FIGURE_NOTE]}])
        self.assertEqual(sorted(final['reused_review_pages']), [1, 2])

    def test_the_notes_page_builds_into_the_hwpx_before_the_answer_table(self):
        self.review([{'page': 1, 'status': 'passed_with_notes', 'issues': [FIGURE_NOTE]},
                     {'page': 2, 'status': 'passed', 'issues': []}])
        result = self.call('build', output=str(self.root / 'exam.hwpx'), title='test', school='test', year='2026',
                           exam_title='test', native=False)
        self.assertEqual(result['status'], 'built', result)
        with zipfile.ZipFile(result['output']) as z:
            xml = z.read('Contents/section0.xml').decode()
        self.assertLess(xml.index('검수 노트'), xml.index('정답표'))
        self.assertIn('20° 라벨', xml)
        self.assertIn('colCnt="4"', xml)
        for header in ('쪽', '문항', '구분', '검수 내용'):
            self.assertIn(f'<hp:t>{header}</hp:t>', xml)

    def test_many_notes_continue_on_further_notes_pages(self):
        from restoration_answers import note_row_height
        # 45 notes, each as long as a note may be (170 characters: five lines, 29 mm): six fit one page.
        notes = [f'경미: q{n % 2 + 1} ' + '가' * 200 for n in range(45)]
        result = self.review([{'page': 1, 'status': 'passed_with_notes', 'issues': notes},
                              {'page': 2, 'status': 'passed', 'issues': []}])
        self.assertEqual(result['status'], 'notes_build_required', result)
        pages = job.assemble(self.root)
        sheets = [p for p in pages if p.get('role') == 'review_notes']
        self.assertEqual([p.get('role') for p in pages], [None] + ['review_notes'] * len(sheets) + ['answer_sheet'])
        self.assertEqual(len(sheets), 8)
        self.assertEqual([p['note_part'] for p in sheets], [[n, 8] for n in range(1, 9)])
        self.assertEqual(sum(len(p['note_rows']) for p in sheets), 45)
        self.assertEqual(len({p['page_number'] for p in pages}), len(pages))
        for sheet in sheets:
            heights = [note_row_height(r['text'], r.get('image')) for r in sheet['note_rows']]
            self.assertLessEqual(sum(heights), single.NOTE_PAGE_BUDGET_MM)
            self.assertTrue(all(len(r['text']) <= 170 for r in sheet['note_rows']))
        built = self.call('build', output=str(self.root / 'exam.hwpx'), title='test', school='test', year='2026',
                          exam_title='test', native=False)
        self.assertEqual(built['status'], 'built', built)
        with zipfile.ZipFile(built['output']) as z:
            xml = z.read('Contents/section0.xml').decode()
        self.assertIn('검수 노트 (1/8)', xml)
        self.assertIn('검수 노트 (8/8)', xml)
        self.assertLess(xml.index('검수 노트 (8/8)'), xml.index('정답표'))
        value = self.native('many-notes')
        self.assertEqual([p['page'] for p in value['review_inputs']['pages']], [1, 2])  # no notes page is reviewed
        self.assertEqual(self.call('status')['status'], 'complete')

    def test_answer_changing_issue_gets_one_repair_round_then_becomes_unresolved_note(self):
        first = self.review([{'page': 1, 'status': 'failed', 'issues': [TEXT_ERROR, FIGURE_NOTE]},
                             {'page': 2, 'status': 'passed', 'issues': []}])
        self.assertEqual(first['status'], 'pending_review', first)
        self.assertEqual(first['reviews']['failed'], [{'page': 1, 'issues': [TEXT_ERROR, FIGURE_NOTE]}])
        second = self.review([{'page': 1, 'status': 'failed', 'issues': [TEXT_ERROR]}])
        self.assertEqual(second['status'], 'notes_build_required', second)
        self.assertEqual(second['review_notes'], [{'page': 1, 'issues': ['미해결 ' + TEXT_ERROR]}])
        [row] = [p for p in job.assemble(self.root) if p.get('role') == 'review_notes'][0]['note_rows']
        self.assertEqual((row['question'], row['kind']), ('논술형1', '미해결·오독'))

    def test_rereview_cap_comes_from_the_environment(self):
        from unittest import mock
        with mock.patch.dict('os.environ', {'HWP_MAX_REREVIEWS': '0'}):
            self.assertEqual(single.max_failed_verdicts(), 1)
            first = self.review([{'page': 1, 'status': 'failed', 'issues': [TEXT_ERROR]},
                                 {'page': 2, 'status': 'passed', 'issues': []}])
        self.assertEqual(first['status'], 'notes_build_required', first)
        self.assertEqual(first['review_notes'], [{'page': 1, 'issues': ['미해결 ' + TEXT_ERROR]}])
        with mock.patch.dict('os.environ', {'HWP_MAX_REREVIEWS': '1'}):
            self.assertEqual(single.max_failed_verdicts(), single.MAX_FAILED_VERDICTS)

    def test_untagged_issue_is_treated_as_a_repair(self):
        result = self.review([{'page': 1, 'status': 'passed_with_notes', 'issues': ['q1 윗줄 누락']},
                              {'page': 2, 'status': 'passed', 'issues': []}])
        self.assertEqual(result['status'], 'pending_review', result)
        self.assertEqual([t['page'] for t in result['review_tasks']], [1])

    def test_verdict_shape_is_checked(self):
        for row in ({'page': 1, 'status': 'passed', 'issues': [FIGURE_NOTE]},
                    {'page': 1, 'status': 'passed_with_notes', 'issues': []},
                    {'page': 1, 'status': 'failed', 'issues': ['  ']}):
            with self.subTest(row=row):
                result = self.review([row])
                self.assertEqual(result['status'], 'failed', result)
                self.assertIn('invalid_review_verdict', result['message'])

    def test_a_figure_difference_tagged_as_a_repair_opens_no_repair_round(self):
        # v2.8.1: a reviewer started from an old role file tags a figure difference 누락. The figure is in the output.
        from unittest import mock
        with mock.patch.object(single, 'drawn_figure_questions', return_value={'q1'}):
            result = self.review([{'page': 1, 'status': 'failed', 'issues': [FIGURE_REPAIR.replace('q3', 'q1')]},
                                  {'page': 2, 'status': 'passed', 'issues': []}])
        self.assertEqual(result['status'], 'notes_build_required', result)
        self.assertEqual(result['review_notes'], [{'page': 1, 'issues': [FIGURE_REPAIR_AS_NOTE.replace('q3', 'q1')]}])
        self.assertNotIn('1', job.load_json(self.root / 'mcp/state.json').get('failed_verdicts', {}))

    def test_a_figure_that_is_not_in_the_output_is_still_a_repair(self):
        result = self.review([{'page': 1, 'status': 'failed', 'issues': ['누락: q1 도형 누락']},
                              {'page': 2, 'status': 'passed', 'issues': []}])
        self.assertEqual(result['status'], 'pending_review', result)
        self.assertEqual(result['reviews']['failed'], [{'page': 1, 'issues': ['누락: q1 도형 누락']}])

    def test_changed_page_after_notes_drops_the_frozen_notes(self):
        self.review([{'page': 1, 'status': 'passed_with_notes', 'issues': [FIGURE_NOTE]},
                     {'page': 2, 'status': 'passed', 'issues': []}])
        self.assertTrue(job.load_json(self.root / 'mcp/state.json').get('review_notes'))
        self.assertEqual(self.call('submit_reading', page=1, markdown=MD.replace('$1+1$의 값은?', '$1+1$의 값은 얼마인가?'))['status'], 'accepted')
        self.assertIsNone(job.load_json(self.root / 'mcp/state.json').get('review_notes'))
        self.assertEqual([p.get('role') for p in job.assemble(self.root)], [None, 'answer_sheet'])


class IssueTagTests(unittest.TestCase):
    def test_tags_accept_common_spellings(self):
        for text, tag in (('도형: 호 위치', '도형'), ('[경미] 간격', '경미'), ('누락 - q3 배점', '누락'), ('q3 누락', None)):
            with self.subTest(text=text):
                self.assertEqual(single.issue_tag(text), tag)

    def test_verdict_from_tags(self):
        self.assertEqual(single.verdict([]), 'passed')
        self.assertEqual(single.verdict([FIGURE_NOTE, '경미: 간격']), 'passed_with_notes')
        self.assertEqual(single.verdict([FIGURE_NOTE, TEXT_ERROR]), 'failed')


class FigureFindingTests(unittest.TestCase):
    def test_a_difference_inside_a_drawn_figure_becomes_a_note(self):
        self.assertEqual(single.figure_issues_as_notes([FIGURE_REPAIR], {'q3'}), [FIGURE_REPAIR_AS_NOTE])
        self.assertEqual(single.verdict(single.figure_issues_as_notes([FIGURE_REPAIR], {'q3'})), 'passed_with_notes')
        self.assertEqual(single.figure_issues_as_notes(['[오독] Q3 그림의 각 표시가 다름'], {'q3'}), ['도형: Q3 그림의 각 표시가 다름'])

    def test_other_issues_stay_as_written(self):
        for issue, drawn in ((FIGURE_REPAIR, set()),                                   # the figure is not in the output
                             ('누락: q3 도형 누락', {'q4'}),
                             ('누락: 도형에서 반지름 누락', {'q3'}),                    # no question named
                             ('누락: q3, q4 그림의 반지름 누락', {'q3'}),               # one of the two is not drawn
                             ('정답: q3 그림과 정답 ③이 맞지 않음', {'q3'}),
                             ('선지: q3 그림 아래 ④ 누락', {'q3'}),
                             ('누락: q3 그림 아래 조건 한 줄 누락', {'q3'}),            # body text next to the figure
                             ('오독: q3 분모 2가 3으로 출력됨', {'q3'}),                # not about a figure
                             ('q3 도형에서 반지름 누락', {'q3'}),                       # untagged stays a repair
                             (FIGURE_NOTE, {'q1'})):
            with self.subTest(issue=issue):
                self.assertEqual(single.figure_issues_as_notes([issue], drawn), [issue])

    def test_drawn_figures_are_read_from_the_accepted_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'figures-1'
            folder.mkdir()
            (folder / 'q3.png').write_bytes(b'png')
            job.save_json(folder / 'batch.json', {'items': [
                {'id': 'q3-figure-1', 'question_id': 'q3', 'png': str(folder / 'q3.png')},
                {'id': 'q4-figure-1', 'question_id': 'q4', 'png': str(folder / 'missing.png')},
                {'id': 'q5-figure-1'},
                {'id': 'q6-figure-1', 'question_id': 'q6', 'png': str(folder / 'q3.png')}]})
            figure = lambda *ids: [{'kind': 'paragraph', 'figure_ref': i} for i in ids]
            page = {'questions': [{'id': 'q2', 'content': [{'kind': 'paragraph'}]}] + [
                {'id': q, 'content': figure(*ids)} for q, ids in (
                    ('q3', ['q3-figure-1']), ('q4', ['q4-figure-1']), ('q5', ['q5-figure-1']),
                    ('q6', ['q6-figure-1', 'q6-figure-2']))]}       # the second figure of q6 has no render
            state = {'figures': {'2': {'path': str(folder / 'figures.json')}, '3': {'path': str(Path(tmp) / 'gone/figures.json')}}}
            from unittest import mock
            with mock.patch.object(single, 'reading', return_value=page):
                self.assertEqual(single.drawn_figure_questions(tmp, state, 2), {'q3'})
                self.assertEqual(single.drawn_figure_questions(tmp, state, 3), set())
                self.assertEqual(single.drawn_figure_questions(tmp, state, 1), set())
                self.assertEqual(single.drawn_figure_questions(tmp, {}, 1), set())
                self.assertEqual(single.figure_issues_as_notes(['누락: q6 두 번째 그림 누락'], single.drawn_figure_questions(tmp, state, 2)),
                                 ['누락: q6 두 번째 그림 누락'])
            with mock.patch.object(single, 'reading', side_effect=ValueError('submit_page_markdown_first')):
                self.assertEqual(single.drawn_figure_questions(tmp, state, 2), set())


if __name__ == '__main__':
    unittest.main()
