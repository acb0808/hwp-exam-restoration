"""Synthetic fixtures test reuse safety, not actual visual correctness or quota."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import restoration_single as single
import restoration_job as job
import test_single_review_workflow as workflow
import test_figure_retry as fixtures


class ImageReviewReuseTests(unittest.TestCase):
    def setUp(self):
        self.case = workflow.SingleReviewTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.root, self.call = self.case.root, self.case.call

    def figure_fixture(self):
        folder = Path(self.case.temp.name) / 'renderer'; folder.mkdir()
        _, _, _, calls, runtime = fixtures.FigureRetryTests().fixture(folder)
        patcher = patch.object(single.batch.runpy, 'run_path', return_value=runtime)
        patcher.start(); self.addCleanup(patcher.stop)
        md = workflow.MD.replace('1. 값은 $x-1$?', '1. 값은 $x-1$?\n\n![](figure:f1)')
        self.call('submit_reading', page=1, markdown=md)
        tex = self.root / 'workers/page-0001/f1.tex'
        tex.write_text(r'\begin{tikzpicture}\draw (0,0)--(1,1);\end{tikzpicture}')
        self.figures = [{'id':'f1','question_id':'q1','latex_path':str(tex),'width_mm':40}]
        return md, tex, calls

    def render(self):
        return self.call('render_figures', page=1, figures=self.figures)

    def approve(self, result, passed=True):
        rows = [{**r, 'status':'passed' if passed else 'failed',
                 'checks':{k:'passed' if passed else 'failed' for k in r['checks']},
                 'issues':[] if passed else ['label overlaps']} for r in result['reviews']]
        return self.call('review_figures', page=1, batch_path=result['batch_path'], reviews=rows)

    def test_unchanged_approved_figure_adds_no_visual_task_or_compile(self):
        _, _, calls = self.figure_fixture()
        self.assertEqual(self.approve(self.render())['status'], 'accepted')
        before=job.assemble(self.root)
        self.case.fixture_native(compose=False)
        result = self.render()
        self.assertEqual(result['status'], 'accepted', result)
        self.assertEqual(result['review_tasks'], [])
        self.assertEqual(result['reused_review_ids'], ['f1'])
        self.assertEqual(len(calls), 1)
        self.assertEqual(job.assemble(self.root),before,'reuse must keep stable figure evidence paths')
        self.assertFalse(job.load_json(self.root/'mcp/state.json').get('output_stale'))

    def test_failed_figure_review_is_never_reused(self):
        self.figure_fixture()
        self.approve(self.render(), passed=False)
        result = self.render()
        self.assertEqual([t['id'] for t in result['review_tasks']], ['f1'])
        self.assertEqual(result['reviews'][0]['checks']['source_comparison'], 'not_verified')

    def test_rejecting_same_reused_figure_revokes_completed_output(self):
        self.figure_fixture(); self.approve(self.render())
        self.case.fixture_native(compose=False); self.final_review()
        again=self.render()
        rejected=self.call('review_figures',page=1,batch_path=again['batch_path'],reviews=[{
            'id':'f1','status':'failed','issues':['missed mark'],
            'checks':{k:'failed' for k in ('geometry','labels','marks','source_comparison')}}])
        self.assertEqual(rejected['status'],'failed')
        self.assertEqual(self.call('status').get('output_status'),'needs_rebuild')
        with self.assertRaises(ValueError):job.assemble(self.root)

    def test_size_source_code_or_question_edit_requires_visual_review(self):
        md, tex, _ = self.figure_fixture()
        self.approve(self.render())
        self.figures[0]['width_mm'] = 41
        result = self.render(); self.assertEqual(len(result['review_tasks']), 1)
        self.approve(result)
        tex.write_text(tex.read_text().replace('(1,1)', '(2,1)'))
        result = self.render(); self.assertEqual(len(result['review_tasks']), 1)
        self.approve(result)
        self.call('submit_reading', page=1, markdown=md.replace('x-1','x+1'))
        self.assertEqual(len(self.render()['review_tasks']), 1)

    def test_mixed_batch_reviews_only_changed_question_and_preserves_other(self):
        md, _, calls=self.figure_fixture()
        second=self.root/'workers/page-0001/f2.tex'
        second.write_text(r'\begin{tikzpicture}\draw (0,0)--(2,2);\end{tikzpicture}')
        md=md.replace('2. 다음 값을 구하시오.','2. 다음 값을 구하시오.\n\n![](figure:f2)')
        self.call('submit_reading',page=1,markdown=md)
        self.figures.append({'id':'f2','question_id':'q2','latex_path':str(second),'width_mm':40})
        self.assertEqual(self.approve(self.render())['status'],'accepted')
        self.call('submit_reading',page=1,markdown=md.replace('2. 다음 값을','2. 아래 값을'))
        result=self.render()
        self.assertEqual([t['id'] for t in result['review_tasks']],['f2'])
        self.assertEqual(result['reused_review_ids'],['f1'])
        self.assertEqual(len(calls),2,'text edit adds no compile')
        self.assertEqual(self.approve(result)['status'],'accepted')

    def test_tampered_saved_figure_review_is_rejected(self):
        self.figure_fixture(); self.approve(self.render())
        state = job.load_json(self.root/'mcp/state.json')
        ref = next(iter(state['figure_review_cache'].values()))
        job._artifact(self.root, ref).write_bytes(b'changed')
        self.assertEqual(self.render()['status'], 'failed')

    def final_review(self, passed=True):
        result = self.call('status')
        evidence = 'reviewer ' + ' '.join(t['source_image']+' '+t['output_image'] for t in result['review_tasks'])
        return self.call('finish_review', reviewer_id='reviewer',
                         reviews=[{'page':1,'status':'passed' if passed else 'failed',
                                   'issues':[] if passed else ['q1 missing line']}], review_evidence=evidence)

    def new_receipt(self):
        receipt = self.root/'fixture-native.json'; value=job.load_json(receipt)
        value['test_revision']=value.get('test_revision',0)+1; job.save_json(receipt,value)

    def test_identical_final_page_reuses_actual_review_without_image_tasks(self):
        self.case.fixture_native(); self.final_review(); self.new_receipt()
        result=self.call('status')
        self.assertEqual(result['status'],'complete',result)
        self.assertEqual(result['review_tasks'],[])
        self.assertEqual(result['reused_review_pages'],[1])
        self.assertIn('reused_from_output_sha256',result['reviews']['1'])

    def test_changed_final_pixels_require_new_review(self):
        from PIL import Image
        self.case.fixture_native(); self.final_review()
        image=self.root/'fixture-output-1.png'
        with Image.open(image) as im:
            im.paste('black',(0,0,20,20)); im.save(image)
        packet=self.root/'fixture-packet-1.json'; value=job.load_json(packet)
        value['output_image']['sha256']=job.digest(image); job.save_json(packet,value)
        self.new_receipt(); result=self.call('status')
        self.assertEqual(result['status'],'pending_review')
        self.assertEqual(len(result['review_tasks']),1)

    def test_failed_final_review_is_not_reused(self):
        self.case.fixture_native(); self.final_review(passed=False); self.new_receipt()
        self.assertEqual(self.call('status')['status'],'pending_review')

    def test_content_change_cannot_reuse_review_even_when_pixels_match(self):
        self.case.fixture_native(); self.final_review()
        self.call('submit_reading',page=1,markdown=workflow.MD.replace('x-1','x+1'))
        state=job.load_json(self.root/'mcp/state.json'); state.pop('output_stale',None)
        job.save_json(self.root/'mcp/state.json',state)
        receipt=self.root/'fixture-native.json'; value=job.load_json(receipt)
        value['pages_sha256']=single.batch.pages_digest(job.assemble(self.root)); job.save_json(receipt,value)
        self.assertEqual(self.call('status')['status'],'pending_review')

    def test_tampered_final_evidence_cannot_be_reused(self):
        self.case.fixture_native(); self.final_review()
        state=job.load_json(self.root/'mcp/state.json')
        job._artifact(self.root,state['reviews']['1']['evidence']).write_bytes(b'changed')
        self.new_receipt()
        self.assertEqual(self.call('status')['status'],'failed')

    def test_inspection_for_worker_does_not_embed_image_in_master_response(self):
        import asyncio
        import restoration_mcp
        result=asyncio.run(restoration_mcp.hwp_inspect(str(self.root),1))
        self.assertTrue(result.structuredContent['image_paths'])
        self.assertFalse(any(c.type=='image' for c in result.content))

    def test_passed_page_remains_available_for_specific_output_inspection(self):
        self.case.fixture_native(); self.final_review()
        result=self.call('inspect',page=1,target='output',requests=[{
            'id':'q1-mark','question_id':'q1','bbox_px':[0,0,20,20],'reason':'verify thin line'}])
        self.assertEqual(result['status'],'ready_to_inspect',result)


class TikzFragmentTests(unittest.TestCase):
    def test_fragment_validation_works_before_equation_runtime_is_loaded(self):
        import subprocess
        scripts=Path(__file__).resolve().parents[1]/'scripts'
        code="import sys; sys.path.insert(0,sys.argv[1]); from restoration_single import diagram_document; assert 'amsmath' in diagram_document(r'\\begin{tikzpicture}\\draw (0,0)--(1,1);\\end{tikzpicture}')"
        result=subprocess.run([sys.executable,'-c',code,str(scripts)],capture_output=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))

    def test_picture_fragment_gets_fixed_preamble_and_full_document_is_preserved(self):
        from restoration_single import diagram_document
        fragment=r'\begin{tikzpicture}\draw (0,0)--(1,1);\end{tikzpicture}'
        document=diagram_document(fragment)
        self.assertIn(r'\usepackage{amsmath}',document)
        self.assertIn(fragment,document)
        self.assertEqual(diagram_document(document),document)

    def test_embedded_scan_and_unbalanced_fragments_rejected(self):
        from restoration_single import diagram_document
        for text in (r'\begin{tikzpicture}\includegraphics{scan.png}\end{tikzpicture}',
                     r'\begin{tikzpicture}\draw (0,0)--(1,1);',
                     r'\end{tikzpicture}\begin{tikzpicture}',
                     r'\usepackage{unknown}\begin{tikzpicture}\end{tikzpicture}'):
            with self.subTest(text=text),self.assertRaises(ValueError): diagram_document(text)
