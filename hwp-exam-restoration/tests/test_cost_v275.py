"""v2.7.5: fewer turns on blocking hosts without weakening actual-ID binding or review evidence."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import restoration_job as job
import restoration_single as single
import test_figure_retry as fixtures
import test_single_review_workflow as workflow
from PIL import Image

TEX = r'\begin{tikzpicture}\draw (0,0)--(1,1);\end{tikzpicture}'
FIGURE_MD = workflow.MD.replace('1. 값은 $x-1$?', '1. 값은 $x-1$?\n\n![](figure:f1)')


class SlotBindingTests(unittest.TestCase):
    """opencode's task tool returns the producer ID only after the producer finishes."""
    def setUp(self):
        import fitz
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'job'
        source = Path(self.temp.name) / 'source.pdf'
        with fitz.open() as doc:
            doc.new_page()
            doc.save(source)
        self.prepared = self.call('prepare', source=str(source), question_pages=[1])

    def call(self, action, **params):
        return single.dispatch(action, {'job': str(self.root), **params})

    def test_prepare_opens_one_pending_slot_per_page(self):
        self.assertEqual(self.prepared['status'], 'prepared', self.prepared)
        [slot] = job._manifest(self.root)['assignments']
        self.assertTrue(slot['pending'])
        self.assertIsNone(single.agent(slot))

    def test_producer_finishes_on_the_slot_before_binding(self):
        result = self.call('submit_reading', page=1, markdown=workflow.MD)
        self.assertEqual(result['status'], 'accepted', result)

    def test_build_refuses_until_actual_producer_is_bound(self):
        self.call('submit_reading', page=1, markdown=workflow.MD)
        result = self.call('build', output=str(self.root / 'out.hwpx'), title='t', school='s', year='y', exam_title='e', native=False)
        self.assertEqual(result['status'], 'failed')
        self.assertIn('bind_actual_producers_before_build', result['message'])

    def test_binding_after_finish_keeps_work_and_records_actual_id(self):
        self.call('submit_reading', page=1, markdown=workflow.MD)
        spawn = '<task id="ses_abc123" state="completed">page 1 accepted</task>'
        bound = self.call('assign', page=1, worker_id='ses_abc123', evidence=spawn)
        self.assertEqual(bound['status'], 'assigned', bound)
        self.assertEqual(bound['page_status'], 'accepted')
        [a] = job._manifest(self.root)['assignments']
        self.assertEqual(single.agent(a), 'ses_abc123')
        self.assertNotIn('pending', a)
        self.assertEqual(job._artifact(self.root, a['evidence']).read_text(encoding='utf-8'), spawn)
        with patch.object(single.shared, '_perform', return_value={'status': 'built'}):
            built = self.call('build', output=str(self.root / 'out.hwpx'), title='t', school='s', year='y', exam_title='e', native=False)
        self.assertEqual(built['status'], 'built', built)

    def test_self_made_evidence_without_the_id_is_refused(self):
        # Observed in the v2.7.4 opencode baseline: a producer bound itself with invented text.
        result = self.call('assign', page=1, worker_id='page-0002-worker', evidence='fresh restoration producer for page 2')
        self.assertEqual(result['status'], 'failed')
        self.assertIn('spawn_evidence_must_contain_worker_id', result['message'])
        self.assertTrue(job._manifest(self.root)['assignments'][0]['pending'])

    def test_missing_evidence_is_a_clean_input_failure(self):
        result = self.call('assign', page=1, worker_id='ses_x', evidence=None)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error_kind'], 'input')
        self.assertIn('actual_spawn_response_text_required', result['message'])

    def test_rebinding_a_bound_page_to_another_id_is_refused(self):
        self.call('assign', page=1, worker_id='ses_a', evidence='ses_a spawned')
        again = self.call('assign', page=1, worker_id='ses_b', evidence='ses_b spawned')
        self.assertEqual(again['status'], 'failed')
        same = self.call('assign', page=1, worker_id='ses_a', evidence='ses_a spawned')
        self.assertEqual(same['status'], 'assigned', same)

    def test_bound_producer_cannot_be_final_reviewer(self):
        self.call('assign', page=1, worker_id='ses_a', evidence='ses_a spawned')
        owners = {x for a in job._manifest(self.root)['assignments'] for x in (single.agent(a), a['worker_id'])}
        self.assertIn('ses_a', owners)


class ReviewerStartTests(unittest.TestCase):
    def setUp(self):
        self.case = workflow.SingleReviewTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.root, self.call = self.case.root, self.case.call

    def test_first_build_returns_report_path_inside_job(self):
        self.case.fixture_native()
        status = self.call('status')
        self.assertEqual(Path(status['report_path']), self.root / 'review/round-01.md')

    def test_spawn_evidence_binds_reviewer_so_report_needs_only_paths(self):
        self.case.fixture_native()
        task = self.call('status')['review_tasks'][0]
        report = f"page 1 passed\n{task['source_image']}\n{task['output_image']}\n"  # no reviewer ID line
        done = self.call('finish_review', reviewer_id='ses_rev9', reviews=[{'page': 1, 'status': 'passed', 'issues': []}],
                         review_evidence=report, spawn_evidence='<task id="ses_rev9" state="completed">')
        self.assertEqual(done['status'], 'complete', done)

    def test_spawn_evidence_must_name_the_reviewer(self):
        self.case.fixture_native()
        task = self.call('status')['review_tasks'][0]
        result = self.call('finish_review', reviewer_id='ses_rev9', reviews=[{'page': 1, 'status': 'passed', 'issues': []}],
                           review_evidence=task['source_image'] + ' ' + task['output_image'], spawn_evidence='a reviewer')
        self.assertEqual(result['status'], 'failed')
        self.assertIn('reviewer_spawn_evidence_must_contain_reviewer_id', str(result))

    def test_without_spawn_evidence_report_still_needs_reviewer_id(self):
        self.case.fixture_native()
        task = self.call('status')['review_tasks'][0]
        result = self.call('finish_review', reviewer_id='ses_rev9', reviews=[{'page': 1, 'status': 'passed', 'issues': []}],
                           review_evidence=task['source_image'] + ' ' + task['output_image'])
        self.assertEqual(result['status'], 'failed')
        self.assertIn('ses_rev9', result['missing_evidence'])

    def test_failed_round_advances_report_path_and_reply_is_compact(self):
        paths = self.case.fixture_native()
        failed = self.call('finish_review', reviewer_id='ses_rev9', reviews=[{'page': 1, 'status': 'failed', 'issues': ['q1 sign']}],
                           review_evidence='ses_rev9 ' + paths)
        self.assertEqual(failed['reviews'], {'passed': [], 'failed': [{'page': 1, 'issues': ['q1 sign']}]})
        self.assertEqual(Path(failed['report_path']).name, 'round-02.md')
        self.assertNotIn('sha256', str(failed['reviews']))


class CompareSheetTests(unittest.TestCase):
    def images(self, d, heights):
        items = []
        for i, h in enumerate(heights):
            path = Path(d) / f'c{i}.png'
            Image.new('RGB', (600, h), 'white').save(path)
            items.append((f'f{i}', path))
        return items

    def test_single_comparison_needs_no_sheet(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(single.compare_sheets(self.images(d, [300]), Path(d) / 's'), [])

    def test_several_comparisons_share_one_native_size_sheet(self):
        with tempfile.TemporaryDirectory() as d:
            [sheet] = single.compare_sheets(self.images(d, [300, 250, 200]), Path(d) / 's')
            with Image.open(sheet) as im:
                self.assertGreaterEqual(im.height, 300)

    def test_tall_groups_split_to_stay_legible(self):
        with tempfile.TemporaryDirectory() as d:
            sheets = single.compare_sheets(self.images(d, [900, 900, 900]), Path(d) / 's')
            self.assertEqual(len(sheets), 2)


class FigureFlowTests(unittest.TestCase):
    def setUp(self):
        self.case = workflow.SingleReviewTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.root, self.call = self.case.root, self.case.call
        folder = Path(self.case.temp.name) / 'renderer'
        folder.mkdir()
        _, _, _, self.calls, runtime = fixtures.FigureRetryTests().fixture(folder)
        patcher = patch.object(single.batch.runpy, 'run_path', return_value=runtime)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_figure_rules_arrive_once_with_ready_for_figures(self):
        first = self.call('submit_reading', page=1, markdown=FIGURE_MD)
        self.assertEqual(first['status'], 'ready_for_figures', first)
        self.assertIn('TikZ', first['figure_rules'])
        again = self.call('submit_reading', page=1, markdown=FIGURE_MD)
        self.assertNotIn('figure_rules', again)

    def test_rerender_without_source_uses_the_saved_tex(self):
        # Observed in the v2.7.5 opencode run: after editing the saved file the producer resent neither field.
        self.call('submit_reading', page=1, markdown=FIGURE_MD)
        first = self.call('render_figures', page=1, figures=[{'id': 'f1', 'question_id': 'q1', 'latex': TEX, 'width_mm': 40}])
        Path(first['tex_paths']['f1']).write_text(TEX.replace('(1,1)', '(2,2)'), encoding='utf-8')
        second = self.call('render_figures', page=1, figures=[{'id': 'f1', 'question_id': 'q1', 'width_mm': 40}])
        self.assertEqual(second['status'], 'pending_review', second)
        self.assertEqual(len(self.calls), 2)

    def test_rerender_with_figures_omitted_reuses_last_list(self):
        self.call('submit_reading', page=1, markdown=FIGURE_MD)
        first = self.call('render_figures', page=1, figures=[{'id': 'f1', 'question_id': 'q1', 'latex': TEX, 'width_mm': 40,
                                                              'source_bbox_px': [10, 10, 200, 120]}])
        Path(first['tex_paths']['f1']).write_text(TEX.replace('(1,1)', '(3,1)'), encoding='utf-8')
        second = self.call('render_figures', page=1)
        self.assertEqual(second['status'], 'pending_review', second)
        self.assertIn('compare_image', second['review_tasks'][0])   # bbox carried over
        self.assertEqual(len(self.calls), 2)

    def test_first_render_without_files_names_the_file_to_write(self):
        # v2.7.7: the saved TeX files are the list; only a missing file is an error, and it says where.
        self.call('submit_reading', page=1, markdown=FIGURE_MD)
        result = self.call('render_figures', page=1)
        self.assertEqual(result['status'], 'failed')
        self.assertIn('write_figure_tex_files', result['message'])
        self.assertIn('f1.tex', result['message'])

    def test_final_review_task_carries_figure_sheet_and_requires_it(self):
        self.call('submit_reading', page=1, markdown=FIGURE_MD)
        rendered = self.call('render_figures', page=1, figures=[
            {'id': 'f1', 'question_id': 'q1', 'latex': TEX, 'width_mm': 40, 'source_bbox_px': [10, 10, 200, 120]}])
        checks = {k: 'passed' for k in ('geometry', 'labels', 'marks', 'source_comparison')}
        reviewed = self.call('review_figures', page=1, batch_path=rendered['batch_path'],
                             reviews=[{'id': 'f1', 'status': 'passed', 'issues': [], 'checks': checks}])
        self.assertEqual(reviewed['status'], 'accepted', reviewed)
        self.case.fixture_native(compose=False)
        task = self.call('status')['review_tasks'][0]
        self.assertTrue(Path(task['figure_sheet']).is_file(), task)
        without = self.call('finish_review', reviewer_id='ses_rev9', reviews=[{'page': 1, 'status': 'passed', 'issues': []}],
                            review_evidence=f"ses_rev9 {task['source_image']} {task['output_image']}")
        self.assertIn(task['figure_sheet'], without['missing_evidence'])


class PictureChoiceTests(unittest.TestCase):
    """Picture choices and <보기> diagrams: clear guidance instead of a rejection loop."""
    HEAD = '## left\n### q1\n1. 다음 중 그래프로 옳은 것은? [3점]\n\n'

    def setUp(self):
        self.case = workflow.SingleReviewTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)

    def submit(self, md):
        return self.case.call('submit_reading', page=1, markdown=md)

    def test_figure_in_choice_cell_explains_the_single_figure_form(self):
        result = self.submit(self.HEAD + '::: choices 2\n① ![](figure:q1-c1) | ② ![](figure:q1-c2)\n:::\n')
        self.assertEqual(result['status'], 'failed')
        self.assertIn('figure:QID-choices', str(result['errors']))

    def test_bare_labels_beside_a_figure_are_refused_with_the_same_guidance(self):
        result = self.submit(self.HEAD + '![](figure:q1-choices)\n\n::: choices 5\n① | ② | ③ | ④ | ⑤\n:::\n')
        self.assertEqual(result['status'], 'failed')
        self.assertIn('omit the ::: choices block', str(result['errors']))

    def test_one_picture_figure_without_choices_block_is_accepted(self):
        result = self.submit(self.HEAD + '![](figure:q1-choices)\n')
        self.assertEqual(result['status'], 'ready_for_figures', result)
        self.assertEqual(result['figure_ids'], ['q1-choices'])

    def test_diagram_stays_inside_its_box(self):
        md = ('## left\n### q1\n1. <보기>에서 옳은 것은? [4점]\n\n::: box 보기\nㄱ. $a>0$이다.\n\n'
              '![](figure:q1-box)\n\nㄴ. $b<0$이다.\n:::\n\n::: choices 2\n① ㄱ | ② ㄴ\n:::\n')
        result = self.submit(md)
        self.assertEqual(result['status'], 'ready_for_figures', result)
        from restoration_markdown import parse_reading_markdown, reading_to_markdown
        reading = parse_reading_markdown(md, page=1, worker_id='w', source_sha256='0' * 64)
        box = reading['questions'][0]['content'][1]
        self.assertEqual([b.get('figure_ref') for b in box['content']], [None, 'q1-box', None])
        self.assertIn('![](figure:q1-box)', reading_to_markdown(reading))


class FigureFitTests(unittest.TestCase):
    """Observed on 중학교 시험지 A p.1: a wide picture-choice figure and a partial rerender list."""
    def setUp(self):
        self.case = workflow.SingleReviewTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.call = self.case.call
        folder = Path(self.case.temp.name) / 'renderer'
        folder.mkdir()
        _, _, _, self.calls, runtime = fixtures.FigureRetryTests().fixture(folder)
        patcher = patch.object(single.batch.runpy, 'run_path', return_value=runtime)
        patcher.start()
        self.addCleanup(patcher.stop)
        md = ('## left\n### q1\n1. 그래프로 옳은 것은? [3점]\n\n![](figure:q1-choices)\n\n'
              '## right\n### q2\n2. <보기>에서 옳은 것은? [4점]\n\n::: box 보기\nㄱ. $a>0$\n\n![](figure:q2-box)\n:::\n')
        self.assertEqual(self.call('submit_reading', page=1, markdown=md)['status'], 'ready_for_figures')

    def render(self, figures):
        return self.call('render_figures', page=1, figures=figures)

    def test_too_wide_figures_are_clamped_to_the_column_and_the_box(self):
        result = self.render([{'id': 'q1-choices', 'question_id': 'q1', 'latex': TEX, 'width_mm': 150},
                              {'id': 'q2-box', 'question_id': 'q2', 'latex': TEX, 'width_mm': 150}])
        self.assertEqual(result['status'], 'pending_review', result)
        # width_mm is template millimetres: one template column less 1 mm (the box limit is checked by composition below).
        self.assertEqual(result['width_clamped']['q1-choices']['used'], round((210.00155555555554 - 2 * 12.8 - 3.6) / 2 - 1, 1))

    def test_clamped_figures_compose_on_wider_source_pages(self):
        # v2.7.9 on a 217 mm scan: a figure clamped in source millimetres failed composition with
        # "figure: outside question width", because composition scales template widths up to the source slot.
        import fitz
        for width_pt in (595, 615, 729):  # A4, the 217 mm scan, B4
            with self.subTest(width_pt=width_pt):
                root = Path(self.case.temp.name) / f'wide-{width_pt}'
                source = Path(self.case.temp.name) / f'wide-{width_pt}.pdf'
                with fitz.open() as doc:
                    doc.new_page(width=width_pt, height=842)
                    doc.save(source)
                call = lambda action, **p: single.dispatch(action, {'job': str(root), **p})
                call('prepare', source=str(source), question_pages=[1])
                call('assign', page=1, worker_id='producer-1', evidence='Actual test worker creation response: producer-1')
                md = ('## left\n### q1\n1. 그래프로 옳은 것은? [3점]\n\n![](figure:q1-choices)\n\n'
                      '## right\n### q2\n2. <보기>에서 옳은 것은? [4점]\n\n::: box 보기\nㄱ. $a>0$\n\n![](figure:q2-box)\n:::\n')
                self.assertEqual(call('submit_reading', page=1, markdown=md)['status'], 'ready_for_figures')
                rendered = call('render_figures', page=1, figures=[
                    {'id': 'q1-choices', 'question_id': 'q1', 'latex': TEX, 'width_mm': 150},
                    {'id': 'q2-box', 'question_id': 'q2', 'latex': TEX, 'width_mm': 150}])
                checks = {k: 'passed' for k in ('geometry', 'labels', 'marks', 'source_comparison')}
                result = call('review_figures', page=1, batch_path=rendered['batch_path'], reviews=[
                    {'id': i, 'status': 'passed', 'issues': [], 'checks': checks} for i in ('q1-choices', 'q2-box')])
                self.assertEqual(result['status'], 'accepted', result)

    def test_partial_rerender_keeps_the_other_figures(self):
        first = self.render([{'id': 'q1-choices', 'question_id': 'q1', 'latex': TEX, 'width_mm': 60},
                             {'id': 'q2-box', 'question_id': 'q2', 'latex': TEX, 'width_mm': 40}])
        Path(first['tex_paths']['q1-choices']).write_text(TEX.replace('(1,1)', '(2,1)'), encoding='utf-8')
        second = self.render([{'id': 'q1-choices', 'question_id': 'q1', 'width_mm': 60}])
        self.assertEqual(second['kept_from_last_render'], ['q2-box'])
        self.assertEqual(json_ids(second['batch_path']), ['q1-choices', 'q2-box'])


def json_ids(path):
    return [i['id'] for i in job.load_json(path)['items']]


class OverflowGuidanceTests(unittest.TestCase):
    def test_content_overflow_names_questions_and_the_fix(self):
        with tempfile.TemporaryDirectory() as d:
            receipt = Path(d) / 'native.json'
            job.save_json(receipt, {'status': 'failed', 'error': 'native_content_exceeds_source_allocation',
                                    'content_fit': {'issues': [{'block_id': 'page-1-grid', 'code': 'native_grid_expanded'},
                                                               {'block_id': 'q3', 'code': 'question_cell_content_overflow'}]}})
            state = {'native_run': {'receipt': str(receipt), 'returncode': 2, 'log': 'x.log'}}
            result = single.collect_native(Path(d), state)
        self.assertEqual(result['overflow_questions'], ['q3'])
        self.assertIn('width_mm', result['next_action'])


if __name__ == '__main__':
    unittest.main()
