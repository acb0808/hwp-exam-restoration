"""Regressions for observed retries, not assertions of OCR correctness or quota."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import restoration_single as single
import restoration_job as job
import test_single_review_workflow as workflow
import test_figure_retry as fixtures


class RetryCostTests(unittest.TestCase):
    def setUp(self):
        self.case = workflow.SingleReviewTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.root = self.case.root
        self.call = self.case.call

    def test_text_edit_reuses_pixels_but_requires_new_semantic_review(self):
        base = Path(self.case.temp.name)
        fixture = base / 'renderer'; fixture.mkdir()
        _, engine, _, calls, runtime = fixtures.FigureRetryTests().fixture(fixture)
        tex = self.root / 'workers/page-0001/f1.tex'
        tex.write_text(r'\begin{tikzpicture}\draw (0,0)--(1,1);\end{tikzpicture}')
        md = workflow.MD.replace('1. 값은 $x-1$?', '1. 값은 $x-1$?\n\n![](figure:f1)')
        self.assertEqual(self.call('submit_reading', page=1, markdown=md)['status'], 'ready_for_figures')
        figures = [{'id':'f1','question_id':'q1','latex_path':str(tex),'width_mm':40}]
        with patch.object(single.batch.runpy, 'run_path', return_value=runtime):
            first = self.call('render_figures', page=1, figures=figures)
            reviews = [{**r,'status':'passed','checks':{k:'passed' for k in r['checks']}} for r in first['reviews']]
            self.assertEqual(self.call('review_figures', page=1, batch_path=first['batch_path'], reviews=reviews)['status'], 'accepted')
            self.assertEqual(self.call('submit_reading', page=1, markdown=md)['status'], 'accepted')
            self.assertEqual(len(calls), 1, 'unchanged normal path must add no renders')
            changed = self.call('submit_reading', page=1, markdown=md.replace('x-1','x+1'))
            self.assertEqual(changed['status'], 'ready_for_figures')
            self.assertEqual(self.call('review_figures', page=1, batch_path=first['batch_path'], reviews=reviews)['status'], 'failed')
            second = self.call('render_figures', page=1, figures=figures)
            self.assertEqual(len(calls), 1, 'text-only edit must not rerender unchanged TeX')
            self.assertTrue(second['review_tasks'][0]['reused'])
            self.assertEqual(second['reviews'][0]['checks']['source_comparison'], 'not_verified')
            self.assertNotIn('1', job.load_json(self.root/'mcp/state.json')['figures'])
            tex.write_text(tex.read_text().replace('(1,1)', '(2,1)'))
            third = self.call('render_figures', page=1, figures=figures)
            self.assertEqual(len(calls), 2)
            self.assertFalse(third['review_tasks'][0]['reused'])

    def test_prepare_reports_loaded_runtime_without_extra_preflight(self):
        # Reuse the original fixture PDF; preparation must not regenerate pages.
        pdf = Path(self.case.temp.name)/'source.pdf'
        page = self.root/'pages/page-0001.png'; before = page.stat().st_mtime_ns
        with patch.object(job, 'prepare', side_effect=AssertionError('unnecessary preparation')):
            result = self.call('prepare', source=str(pdf), question_pages=[1])
        self.assertEqual(result.get('runtime_version'), single.RUNTIME_VERSION)
        self.assertEqual(page.stat().st_mtime_ns, before)
        self.assertFalse(list((self.root/'mcp').glob('views-*')))

    def request(self, **changes):
        return {'id':'q1-mark','question_id':'q1','bbox_px':[0,0,90,60],
                'reason':'Thin bar unclear in the full-page preview',**changes}

    def test_output_inspection_returns_only_requested_current_pixels(self):
        from PIL import Image
        self.case.fixture_native()
        current = self.root/'fixture-output-1.png'
        # Distinguish output pixels from the white source fixture.
        with Image.open(current) as original:
            original.paste('red',(0,0,90,60));original.save(current)
        packet=self.root/'fixture-packet-1.json';value=job.load_json(packet)
        value['output_image']['sha256']=job.digest(current);job.save_json(packet,value)
        receipt=self.root/'fixture-native.json';value=job.load_json(receipt)
        value['test_revision']=2;job.save_json(receipt,value)
        result = self.call('inspect', page=1, target='output', requests=[self.request()])
        self.assertEqual(result['status'], 'ready_to_inspect', result)
        self.assertEqual(result.get('target'), 'output')
        with Image.open(current) as original, Image.open(result['image_paths'][0]) as crop:
            self.assertEqual(crop.size, (90,60))
            self.assertEqual(crop.tobytes(), original.crop((0,0,90,60)).tobytes())
        self.assertEqual(self.call('status')['reviews'], {'passed': [], 'failed': []})

    def test_output_inspection_rejects_stale_tampered_or_invalid_requests(self):
        self.case.fixture_native()
        for request in [self.request(bbox_px=[-1,0,30,30]), self.request(id='../outside'),
                        self.request(reason=''), self.request(question_id='other'),
                        self.request(bbox_px=[0,0,float('nan'),20])]:
            with self.subTest(request=request):
                self.assertEqual(self.call('inspect', page=1, target='output', requests=[request])['status'], 'failed')
        self.assertEqual(self.call('inspect', page=1, target='output')['status'], 'failed')
        self.call('submit_reading', page=1, markdown=workflow.MD.replace('x-1','x+1'))
        self.assertEqual(self.call('inspect', page=1, target='output', requests=[self.request()])['status'], 'failed')

    def test_tampered_output_cannot_be_inspected_as_current(self):
        self.case.fixture_native()
        (self.root/'fixture-output-1.png').write_bytes(b'changed')
        self.assertEqual(self.call('inspect', page=1, target='output', requests=[self.request()])['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
