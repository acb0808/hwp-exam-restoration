"""v2.7.4 turn-reduction changes: they must not weaken assignment or review evidence."""
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
from restoration_montage import montage, side_by_side


class PreparedTaskTests(unittest.TestCase):
    def prepare(self, **extra):
        import fitz
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'job'
        source = Path(self.temp.name) / 'source.pdf'
        with fitz.open() as doc:
            doc.new_page()
            doc.save(source)
        return single.dispatch('prepare', {'job': str(self.root), 'source': str(source), 'question_pages': [1], **extra})

    def test_task_exists_at_prepare_before_any_worker_is_known(self):
        result = self.prepare()
        self.assertEqual(result['status'], 'prepared', result)
        task = Path(result['spawn_requests'][0]['task_path'])
        self.assertEqual(task, self.root / 'workers/page-0001/task.md')
        text = task.read_text(encoding='utf-8')
        self.assertIn('hwp_submit_reading', text)
        self.assertIn('hwp_render_figures', text)   # call shapes: no schema file needed
        self.assertIn('source_bbox_px', text)
        self.assertNotIn('producer-1', text)

    def test_assign_keeps_the_same_task_bytes_and_still_records_actual_evidence(self):
        self.prepare()
        task = self.root / 'workers/page-0001/task.md'
        before = task.read_bytes()
        result = single.dispatch('assign', {'job': str(self.root), 'page': 1, 'worker_id': 'producer-1',
                                            'evidence': 'Actual spawn response producer-1'})
        self.assertEqual(result['status'], 'assigned', result)
        self.assertEqual(task.read_bytes(), before)
        self.assertEqual(Path(result['worker_instructions']), task)
        self.assertEqual(single.agent(job._manifest(self.root)['assignments'][0]), 'producer-1')

    def test_submit_before_binding_uses_the_page_slot(self):
        # v2.7.5: blocking hosts return the producer ID only when it finishes, so work proceeds on the slot.
        self.prepare()
        result = single.dispatch('submit_reading', {'job': str(self.root), 'page': 1, 'markdown': workflow.MD})
        self.assertNotIn('assign_one_producer_first', str(result))
        self.assertTrue(job._manifest(self.root)['assignments'][0]['pending'])

    def test_answer_option_change_before_assignment_regenerates_task(self):
        self.prepare(include_answers=False)
        self.assertIn('include_answers=false', (self.root / 'workers/page-0001/task.md').read_text(encoding='utf-8'))
        source = self.root.parent / 'source.pdf'
        single.dispatch('prepare', {'job': str(self.root), 'source': str(source), 'question_pages': [1], 'include_answers': True})
        self.assertIn('include_answers=true', (self.root / 'workers/page-0001/task.md').read_text(encoding='utf-8'))

    def test_assign_creates_missing_task_for_jobs_prepared_by_older_versions(self):
        self.prepare()
        (self.root / 'workers/page-0001/task.md').unlink()
        result = single.dispatch('assign', {'job': str(self.root), 'page': 1, 'worker_id': 'producer-1', 'evidence': 'Actual producer-1'})
        self.assertEqual(result['status'], 'assigned', result)
        self.assertTrue((self.root / 'workers/page-0001/task.md').is_file())


class MontageTests(unittest.TestCase):
    def test_montage_keeps_native_pixels_and_packs_rows(self):
        with tempfile.TemporaryDirectory() as d:
            paths = []
            for i, size in enumerate([(300, 120), (300, 80), (300, 100)]):
                path = Path(d) / f'{i}.png'
                Image.new('RGB', size, 'white').save(path)
                paths.append((f'c{i}', path))
            out = montage(paths, Path(d) / 'sheet.png', max_width=700)
            with Image.open(out) as sheet:
                self.assertGreaterEqual(sheet.width, 300)
                self.assertLess(sheet.width, 700)
                # two per row at 700 wide: height covers two label+tile rows, not three
                self.assertLess(sheet.height, 3 * 120 + 100)

    def test_side_by_side_matches_heights(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d) / 'a.png', Path(d) / 'b.png'
            Image.new('RGB', (100, 50), 'white').save(a)
            Image.new('RGB', (400, 100), 'white').save(b)
            out = side_by_side(a, b, Path(d) / 'c.png')
            with Image.open(out) as image:
                self.assertGreater(image.width, 100 + 200)


class InspectSheetTests(unittest.TestCase):
    def setUp(self):
        self.case = workflow.SingleReviewTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.root, self.call = self.case.root, self.case.call

    def request(self, ident, box):
        return {'id': ident, 'question_id': 'q1', 'bbox_px': box, 'reason': 'unclear symbol'}

    def test_several_crops_arrive_as_one_sheet_and_keep_individual_paths(self):
        result = self.call('inspect', page=1, target='source',
                           requests=[self.request('a1', [0, 0, 60, 40]), self.request('b1', [100, 100, 80, 50])])
        self.assertEqual(result['status'], 'ready_to_inspect', result)
        self.assertEqual(len(result['image_paths']), 1)
        self.assertEqual(len(result['crop_paths']), 2)
        self.assertEqual(result['sheet_of'], ['a1', 'b1'])
        with Image.open(result['image_paths'][0]) as sheet:
            self.assertGreaterEqual(sheet.width, 80)
        for path in result['crop_paths']:
            self.assertTrue(Path(path).is_file())

    def test_single_crop_is_unchanged(self):
        result = self.call('inspect', page=1, target='source', requests=[self.request('a1', [0, 0, 60, 40])])
        self.assertEqual(len(result['image_paths']), 1)
        self.assertNotIn('crop_paths', result)


class FigureCompareTests(unittest.TestCase):
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
        md = workflow.MD.replace('1. 값은 $x-1$?', '1. 값은 $x-1$?\n\n![](figure:f1)')
        self.call('submit_reading', page=1, markdown=md)
        self.tex = r'\begin{tikzpicture}\draw (0,0)--(1,1);\end{tikzpicture}'

    def test_inline_latex_is_saved_at_assigned_path_and_reported(self):
        result = self.call('render_figures', page=1, figures=[
            {'id': 'f1', 'question_id': 'q1', 'latex': self.tex, 'width_mm': 40}])
        self.assertEqual(result['status'], 'pending_review', result)
        path = self.root / 'workers/page-0001/f1.tex'
        self.assertEqual(result['tex_paths'], {'f1': str(path.resolve())})
        self.assertEqual(path.read_text(encoding='utf-8'), self.tex)
        self.assertNotIn('compare_image', result['review_tasks'][0])

    def test_edit_saved_file_and_resubmit_by_path_rerenders_only_that_source(self):
        first = self.call('render_figures', page=1, figures=[
            {'id': 'f1', 'question_id': 'q1', 'latex': self.tex, 'width_mm': 40}])
        path = Path(first['tex_paths']['f1'])
        path.write_text(self.tex.replace('(1,1)', '(2,2)'), encoding='utf-8')
        second = self.call('render_figures', page=1, figures=[
            {'id': 'f1', 'question_id': 'q1', 'latex_path': str(path), 'width_mm': 40}])
        self.assertEqual(second['status'], 'pending_review', second)
        self.assertEqual(len(self.calls), 2)

    def test_source_bbox_adds_one_comparison_image(self):
        result = self.call('render_figures', page=1, figures=[
            {'id': 'f1', 'question_id': 'q1', 'latex': self.tex, 'width_mm': 40,
             'source_bbox_px': [10, 10, 200, 120]}])
        task = result['review_tasks'][0]
        self.assertTrue(Path(task['compare_image']).is_file(), task)
        self.assertTrue(Path(task['render_image']).is_file())   # evidence images stay registered
        state = job.load_json(self.root / 'mcp/state.json')
        self.assertIn(task['compare_image'], state['assets'])

    def test_bad_bbox_does_not_block_render_or_review(self):
        result = self.call('render_figures', page=1, figures=[
            {'id': 'f1', 'question_id': 'q1', 'latex': self.tex, 'width_mm': 40,
             'source_bbox_px': [999999, 999999, 10, 10]}])
        self.assertEqual(result['status'], 'pending_review', result)
        self.assertIn('compare_image_error', result['review_tasks'][0])
        self.assertNotIn('compare_image', result['review_tasks'][0])

    def test_latex_and_path_together_are_still_rejected(self):
        path = self.root / 'workers/page-0001/f1.tex'
        path.write_text(self.tex, encoding='utf-8')
        result = self.call('render_figures', page=1, figures=[
            {'id': 'f1', 'question_id': 'q1', 'latex': self.tex, 'latex_path': str(path), 'width_mm': 40}])
        self.assertEqual(result['status'], 'failed', result)


if __name__ == '__main__':
    unittest.main()
