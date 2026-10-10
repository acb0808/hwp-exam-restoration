"""hwp_prepare include_figures=false: a text-only job. Producers get no figure steps, a figure place written by
habit costs no round, figure tools refuse, and the reviewer is told that missing figures are intended."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import restoration_job as job
import restoration_single as single

MD = '''## left
### q1
1. 그림과 같이 $\\overline{AB}=3$일 때, $x$의 값은?

![](figure:q1-figure-1)

::: box 보기
![](figure:q1-box)
:::

::: choices 2
① $1$ | ② $2$
:::
'''


class TextOnlyTests(unittest.TestCase):
    def setUp(self):
        import fitz
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'job'
        self.source = Path(self.tmp.name) / 'source.pdf'
        with fitz.open() as doc:
            doc.new_page()
            doc.save(self.source)

    def call(self, action, **kwargs):
        return single.dispatch(action, {'job': str(self.root), **kwargs})

    def prepare(self, **options):
        result = self.call('prepare', source=str(self.source), question_pages=[1], include_answers=False, **options)
        self.assertEqual(result['status'], 'prepared', result)
        self.call('assign', page=1, worker_id='producer', evidence='Actual fixture worker response: producer')
        return (self.root / 'workers/page-0001/task.md').read_text(encoding='utf-8')

    def test_figures_are_restored_by_default(self):
        task = self.prepare()
        self.assertNotIn('include_figures=false', task)
        self.assertIn('ready_for_figures', task)
        self.assertIn('hwp_review_figures', task)
        self.assertIn('![](figure:q1-figure-1)', task)
        self.assertTrue(job._manifest(self.root).get('include_figures'))
        result = self.call('submit_reading', page=1, markdown=MD)
        self.assertEqual(result['status'], 'ready_for_figures', result)

    def test_text_only_task_has_no_figure_steps(self):
        task = self.prepare(include_figures=False)
        self.assertIn('include_figures=false', task)
        for absent in ('ready_for_figures', 'figure_rules', 'ID.tex', '도형 렌더', '도형 검수', 'figure:q'):
            self.assertNotIn(absent, task)
        self.assertIn('::: box 제목', task)       # the box rule stays, without its figure clause
        self.assertIn('hwp_inspect', task)
        self.assertFalse(job._manifest(self.root)['include_figures'])

    def test_a_figure_place_written_by_habit_is_left_out_without_a_round(self):
        self.prepare(include_figures=False)
        result = self.call('submit_reading', page=1, markdown=MD)
        self.assertEqual(result['status'], 'accepted', result)
        [page] = job.assemble(self.root)
        self.assertNotIn('figure', str(page['questions']))
        self.assertIn('choices', str(page['questions']))

    def test_figure_tools_refuse_in_a_text_only_job(self):
        self.prepare(include_figures=False)
        self.call('submit_reading', page=1, markdown=MD)
        for action, extra in (('render_figures', {}), ('review_figures', {'batch_path': 'x', 'reviews': []})):
            result = self.call(action, page=1, **extra)
            self.assertEqual((result['status'], result['message']), ('failed', 'figures_not_restored_in_this_job'), result)

    def test_help_and_report_say_that_figures_are_left_out(self):
        self.prepare(include_figures=False)
        self.assertFalse(self.call('help', page=1, topic='figures')['include_figures'])
        report = self.root / 'report.md'
        task = {'page': 1, 'source_image': 'source.png', 'output_image': 'output.png'}
        single.write_report_skeleton(self.root, report, [task])
        text = report.read_text(encoding='utf-8')
        self.assertIn('include_figures=false', text)
        self.assertNotIn('- 도형:', text)

    def test_report_tells_the_reviewer_to_review_text_and_to_check_answers_on_the_source_figure(self):
        self.prepare(include_figures=False)
        report = self.root / 'report.md'
        tasks = [{'page': 1, 'source_image': 'source.png', 'output_image': 'output.png'},
                 {'page': 2, 'kind': 'answer_sheet', 'answer_reference': 'answers.md', 'output_image': 'table.png'}]
        single.write_report_skeleton(self.root, report, tasks)
        text = report.read_text(encoding='utf-8')
        self.assertIn('그림이 없어 출력만으로는 풀 수 없는 문항도 정상', text)
        self.assertIn('어떤 태그로도 적지 않고', text)
        answer = text[text.index('## page 2'):]
        self.assertIn('원본 쪽 이미지의 그림에서', answer)
        self.assertIn('글만으로 풀 수 없다는 이유로 `정답:`을 적지 않습니다', answer)

    def test_figure_absence_is_not_a_repair_in_a_text_only_job(self):
        kept, dropped = single.text_only_review([
            '도형: q3 원본 삼각형 / 출력 없음',
            '누락: q3 도형 누락',
            '누락: q4 그림 속 조건 AB=5cm가 출력에 없음',
            '정답: 3-1 그림 없이 검산 불가',
            '오독: q5 그림이 없어 문제를 풀 수 없음',
            '누락: q6 발문 아래 문장 "단, 점 O는 외심이다" 없음(그림 옆)',
            '오독: q7 $\\angle A=50^\\circ$를 $40^\\circ$로 출력',
            '정답: 4-2 원본 그림으로 계산하면 12cm인데 표는 10cm로 다름',
            '경미: q8 "대표문제" 표시 누락'])
        self.assertEqual(dropped, ['도형: q3 원본 삼각형 / 출력 없음'])
        self.assertEqual([single.issue_tag(i) for i in kept], ['경미', '경미', '경미', '경미', '누락', '오독', '정답', '경미'])
        self.assertTrue(kept[0].startswith('경미: (텍스트 전용 작업이라 수정하지 않음) q3 도형 누락'))
        self.assertEqual(single.verdict(kept[:4]), 'passed_with_notes')
        self.assertEqual(single.verdict(kept), 'failed')        # text omissions and wrong values still fail
        # A finding that quotes what is missing names printed text, wherever it stands on the page.
        quoted = ["누락: q4 그림 옆 조건 '점 O는 외심' 없음", '누락: q4 그림 아래 $\\overline{AB}=5$ 없음']
        self.assertEqual(single.text_only_review(quoted), (quoted, []))

    def reviewed(self, issues, **options):
        """One accepted page with a fixture export, reviewed with the given issues."""
        import fitz
        from restoration_batch import pages_digest, review_pack
        self.prepare(**options)
        self.assertEqual(self.call('submit_reading', page=1, markdown=MD.replace('![](figure:q1-figure-1)', '').replace('![](figure:q1-box)', '본문'))['status'], 'accepted')
        pages = job.assemble(self.root); pdf = self.root / 'fixture.pdf'
        with fitz.open() as doc:
            for _ in pages: doc.new_page()
            doc.save(pdf)
        native = {'status': 'rendered', 'cleanup': 'closed_owned_session', 'job': str(self.root), 'page_count': len(pages),
                  'pages_sha256': pages_digest(pages), 'artifacts': {'pdf': {'path': str(pdf), 'sha256': job.digest(pdf)}}}
        native['review_inputs'] = review_pack(self.root, native, self.root / 'fixture-review')
        receipt = self.root / 'fixture-native.json'; job.save_json(receipt, native)
        state = job.load_json(self.root / 'mcp/state.json'); state['native_run'] = {'receipt': str(receipt), 'returncode': 0}
        job.save_json(self.root / 'mcp/state.json', state)
        [task] = self.call('status')['review_tasks']
        status = 'passed' if not issues else 'failed'
        return self.call('finish_review', reviewer_id='reviewer', reviews=[{'page': 1, 'status': status, 'issues': issues}],
                         review_evidence='reviewer ' + task['source_image'] + ' ' + task['output_image'])

    def test_a_reviewer_that_fails_a_page_for_its_missing_figure_starts_no_repair_round(self):
        result = self.reviewed(['누락: q1 도형 누락', '도형: q1 원본 삼각형 / 출력 없음', '오독: q1 그림이 없어 풀 수 없음'], include_figures=False)
        self.assertIn(result['status'], ('notes_build_required', 'complete', 'building'), result)
        self.assertEqual(result['reviews']['failed'] if 'reviews' in result else [], [])
        notes = [i for n in result.get('review_notes', []) for i in n['issues']]
        self.assertEqual(len(notes), 2)                                   # the 도형 line is no finding at all
        self.assertTrue(all(i.startswith('경미: (텍스트 전용 작업이라 수정하지 않음)') for i in notes))

    def test_only_figure_differences_pass_without_a_notes_page(self):
        result = self.reviewed(['도형: q1 원본 삼각형 / 출력 없음'], include_figures=False)
        self.assertEqual(result['status'], 'complete', result)
        self.assertNotIn('review_notes', result)

    def test_a_text_error_still_fails_in_a_text_only_job(self):
        result = self.reviewed(['오독: q1 $\\overline{AB}=3$이 $8$로 출력됨'], include_figures=False)
        self.assertEqual(result['status'], 'pending_review', result)
        self.assertEqual(result['reviews']['failed'][0]['page'], 1)

    def test_missing_figure_still_fails_when_figures_are_restored(self):
        result = self.reviewed(['누락: q1 도형 누락'])
        self.assertEqual(result['status'], 'pending_review', result)

    def test_default_report_keeps_the_figure_line(self):
        self.prepare()
        report = self.root / 'report.md'
        single.write_report_skeleton(self.root, report, [{'page': 1, 'source_image': 'source.png', 'output_image': 'output.png'}])
        text = report.read_text(encoding='utf-8')
        self.assertNotIn('include_figures=false', text)
        self.assertIn('- 도형:', text)

    def test_removed_lines_keep_their_line_numbers(self):
        text, removed = single.without_figures(MD)
        self.assertEqual(removed, 2)
        self.assertEqual(len(text.split('\n')), len(MD.split('\n')))
        self.assertNotIn('::: box', text)          # the box held nothing but its figure
        kept, none = single.without_figures('::: box\n\n:::\n본문')
        self.assertEqual((kept, none), ('::: box\n\n:::\n본문', 0))


if __name__ == '__main__':
    unittest.main()
