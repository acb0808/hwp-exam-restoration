"""v2.7.7: assigned files as defaults, a prefilled review report and enlarged uncertain spots for the reviewer."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import restoration_job as job
import restoration_single as single
import test_figure_retry as fixtures
import test_single_review_workflow as workflow
from test_cost_v275 import FIGURE_MD, TEX


class DefaultPathTests(unittest.TestCase):
    """Observed in v2.7.6 runs: hwp_submit_reading called with only job/page (2-4 times per run)."""
    def setUp(self):
        self.case = workflow.SingleReviewTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.root, self.call = self.case.root, self.case.call
        self.reading = self.root / 'workers/page-0001/reading.md'

    def test_submit_without_markdown_uses_the_assigned_reading(self):
        self.reading.write_text(workflow.MD, encoding='utf-8')
        self.assertEqual(self.call('submit_reading', page=1)['status'], 'accepted')

    def test_missing_default_says_where_to_write(self):
        result = self.call('submit_reading', page=1)
        self.assertEqual(result['status'], 'failed')
        self.assertIn(str(self.reading), result['message'])

    def test_both_sources_are_still_ambiguous(self):
        self.reading.write_text(workflow.MD, encoding='utf-8')
        result = self.call('submit_reading', page=1, markdown=workflow.MD, markdown_path=str(self.reading))
        self.assertIn('provide_exactly_one', result['message'])


class FigureFileTests(unittest.TestCase):
    """Observed: LaTeX JSON-escaped through python3, and the first render sent without a list."""
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
        self.assertEqual(self.call('submit_reading', page=1, markdown=FIGURE_MD)['status'], 'ready_for_figures')
        self.tex = self.root / 'workers/page-0001/f1.tex'

    def items(self, result):
        return job.load_json(result['batch_path'])['items']

    def test_header_comment_sets_width_and_source_box(self):
        self.tex.write_text('% width_mm=42 source_bbox_px=0,0,300,200\n' + TEX, encoding='utf-8')
        result = self.call('render_figures', page=1)
        self.assertEqual(result['status'], 'pending_review', result)
        self.assertEqual([i['width_mm'] for i in self.items(result)], [42.0])
        self.assertIn('compare_image', result['review_tasks'][0])

    def test_width_follows_the_source_share_when_only_the_box_is_given(self):
        self.tex.write_text('% source_bbox_px=[0, 0, 413, 200]\n' + TEX, encoding='utf-8')
        result = self.call('render_figures', page=1)
        size = single.info(self.root, 1)['source_size_px'][0]
        page_mm = job._manifest(self.root)['pages'][0]['width_mm']
        self.assertEqual(self.items(result)[0]['width_mm'], round(413 / size * page_mm, 1))

    def test_file_without_width_or_box_asks_for_the_header(self):
        self.tex.write_text(TEX, encoding='utf-8')
        result = self.call('render_figures', page=1)
        self.assertIn('add first line % width_mm', result['message'])

    def test_edited_header_changes_the_next_render(self):
        self.tex.write_text('% width_mm=40\n' + TEX, encoding='utf-8')
        self.call('render_figures', page=1)
        self.tex.write_text('% width_mm=30\n' + TEX, encoding='utf-8')
        self.assertEqual([i['width_mm'] for i in self.items(self.call('render_figures', page=1))], [30.0])

    def test_header_parser(self):
        self.assertEqual(single.figure_header('% width_mm=55.5 source_bbox_px=1,2,3,4\n\\begin{tikzpicture}'),
                         {'width_mm': 55.5, 'source_bbox_px': [1.0, 2.0, 3.0, 4.0]})
        self.assertEqual(single.figure_header('\\draw (0,0); % width_mm=9'), {})  # only leading comment lines


class ReviewAidTests(unittest.TestCase):
    def setUp(self):
        self.case = workflow.SingleReviewTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.root, self.call = self.case.root, self.case.call

    def test_report_skeleton_is_prefilled_with_every_reviewed_path(self):
        self.case.fixture_native()
        status = self.call('status')
        report = Path(status['report_path'])
        text = report.read_text(encoding='utf-8')
        task = status['review_tasks'][0]
        self.assertIn(task['source_image'], text)
        self.assertIn(task['output_image'], text)
        self.assertIn('- 판정:', text)
        # The reviewer's edits survive later status calls.
        report.write_text(text.replace('- 판정: (passed / passed_with_notes / failed)', '- 판정: passed'), encoding='utf-8')
        self.call('status')
        self.assertIn('- 판정: passed', report.read_text(encoding='utf-8'))
        # Prefilled paths satisfy the evidence check once the reviewer's spawn response is on record.
        result = self.call('finish_review', reviewer_id='reviewer', reviews=[{'page': 1, 'status': 'passed', 'issues': []}],
                           review_evidence=report.read_text(encoding='utf-8'), spawn_evidence='spawned reviewer')  # MCP reads report_path into this
        self.assertEqual(result['status'], 'complete', result)

    def test_inspected_spots_reach_the_reviewer_enlarged(self):
        request = {'id': 'q1-overline', 'question_id': 'q1', 'bbox_px': [10, 10, 60, 20], 'reason': '윗줄 유무'}
        self.assertEqual(self.call('inspect', page=1, target='source', requests=[request])['status'], 'ready_to_inspect')
        self.call('inspect', page=1, target='source', requests=[request])  # the same spot is kept once
        self.case.fixture_native()
        task = self.call('status')['review_tasks'][0]
        regions = task['uncertain_regions']
        self.assertEqual(regions['spots'], [{'question_id': 'q1', 'id': 'q1-overline', 'reason': '윗줄 유무'}])
        from PIL import Image
        with Image.open(regions['image']) as sheet:
            self.assertGreater(sheet.width, 60 * 2)  # magnified beyond the 60 px source crop
        self.assertIn(regions['image'], Path(self.call('status')['report_path']).read_text(encoding='utf-8'))


class PageImageModeTests(unittest.TestCase):
    def test_gray16_is_opt_in_and_keeps_pixel_size(self):
        import os
        import tempfile
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'page.png'
            Image.new('RGB', (40, 30), (200, 10, 10)).save(path)
            job.compact_page_image(path)
            with Image.open(path) as im:
                self.assertEqual(im.mode, 'RGB')  # unchanged without the setting
            with patch.dict(os.environ, {'HWP_PAGE_IMAGE_MODE': 'gray16'}):
                job.compact_page_image(path)
            with Image.open(path) as im:
                self.assertEqual((im.mode, im.size), ('P', (40, 30)))
                self.assertLessEqual(len(im.getcolors()), 16)


if __name__ == '__main__':
    unittest.main()
