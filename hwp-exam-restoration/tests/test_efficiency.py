"""Behavioral checks for public helpers; synthetic fixtures are not OCR evidence."""
import copy, hashlib, json, sys, tempfile, unittest, zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import restoration_job as job
import test_tikz_handoff as handoff
import test_question_flow as flow
HP=flow.HP

class EfficiencyTests(unittest.TestCase):
    def prepared(self,root):
        import fitz
        source=root/'source.pdf'
        with fitz.open() as doc:
            doc.new_page(width=595.2,height=841.8);doc.save(source)
        work=root/'job';manifest=job.prepare(source,work)
        evidence=root/'spawn.json';evidence.write_text('{"worker_id":"worker1"}')
        assignment=job.assign(work,1,'worker1',evidence)
        return work,manifest,assignment

    def page(self,manifest,assignment):
        value=json.loads((Path(__file__).resolve().parents[1]/'examples/ocr-page.json').read_text(encoding='utf8'))
        value.update(source_sha256=manifest['source']['sha256'],page_number=1,
                     assignment_id=assignment['assignment_id'],worker_id='worker1',
                     size_mm=[manifest['pages'][0]['width_mm'],manifest['pages'][0]['height_mm']])
        return value

    def test_prepare_and_assign_provide_real_images_and_exact_metadata(self):
        with tempfile.TemporaryDirectory() as d:
            root,manifest,assignment=self.prepared(Path(d))
            self.assertIn('worker_input',assignment)
            packet=job.load_json(assignment['worker_input'])
            self.assertEqual(packet['page_metadata']['size_mm'],[manifest['pages'][0]['width_mm'],manifest['pages'][0]['height_mm']])
            self.assertEqual(packet['page_metadata']['worker_id'],'worker1')
            self.assertEqual(len(packet['navigation_crops']),2)
            self.assertTrue(all(Path(c['path']).is_file() for c in packet['navigation_crops']))
            self.assertEqual(packet['skeleton']['questions'],[])
            self.assertTrue(Path(packet['result_path']).parent.is_dir())

    def test_validation_collects_equation_errors_without_accepting_or_rewriting(self):
        self.assertTrue(hasattr(job,'validate_result'),'public read-only validator is missing')
        with tempfile.TemporaryDirectory() as d:
            root,manifest,assignment=self.prepared(Path(d));page=self.page(manifest,assignment)
            page['questions'][0]['content'][0]['runs']=[{'kind':'equation','latex':r'\unsupportedA{x}'},{'kind':'equation','latex':r'\unsupportedB{x}'}]
            path=root/'result.json';job.save_json(path,page);before=path.read_bytes();m=(root/'manifest.json').read_bytes()
            result=job.validate_result(root,path)
            self.assertEqual(result['status'],'failed')
            self.assertEqual(sum(e['code']=='equation' for e in result['errors']),2)
            self.assertTrue(all('location' in e for e in result['errors']))
            self.assertEqual(path.read_bytes(),before);self.assertEqual((root/'manifest.json').read_bytes(),m)
            with self.assertRaisesRegex(ValueError,'equation'):job.accept(root,path)

    def test_nested_unreviewed_figure_is_not_a_validation_shortcut(self):
        from figure_provenance import validate_figures
        with tempfile.TemporaryDirectory() as d:
            page,figure,_=handoff.TikzHandoffTests().fixture(Path(d));del figure['tikz']
            paragraph=page['questions'][0]['content'][0]
            page['questions'][0]['content']=[{'kind':'box','content':[paragraph]}]
            with self.assertRaisesRegex(ValueError,'tikz_provenance_required'):validate_figures(page)

    def test_figure_info_computes_dimensions_without_inventing_passed_review(self):
        self.assertTrue(hasattr(job,'figure_info'),'public figure info is missing')
        import fitz
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);root,manifest,assignment=self.prepared(base)
            page,figure,review=handoff.TikzHandoffTests().fixture(base)
            pdf=base/'drawing.pdf'
            with fitz.open() as doc:doc.new_page(width=144,height=72);doc.save(pdf)
            receipt=job.load_json(base/'render.json')
            receipt['pdf']['sha256']=job.digest(pdf);job.save_json(base/'render.json',receipt)
            info=job.figure_info(root,1,'q1',base/'render.json',width_mm=40)
            self.assertEqual(info['figure']['size_mm'],[40,20])
            self.assertEqual(info['review_template']['status'],'pending')
            self.assertNotIn('review',info['figure'].get('tikz',{}))
            review=info['review_template'];review.update(status='passed',checks={k:'passed' for k in review['checks']})
            job.save_json(base/'review.json',review)
            linked=job.figure_info(root,1,'q1',base/'render.json',width_mm=40,review=base/'review.json')
            self.assertIn('review',linked['figure']['tikz'])
            (base/'drawing.tex').write_text('tampered')
            with self.assertRaisesRegex(ValueError,'hash_mismatch'):job.figure_info(root,1,'q1',base/'render.json',width_mm=40)

    def test_picture_is_separate_and_height_is_reserved(self):
        from restoration_compiler import build_hwpx
        from unittest.mock import patch
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);png=root/'image.png';Image.new('RGB',(80,40),'white').save(png)
            page=flow.QuestionFlowTests().page();b=page['questions'][0]['content'][0]
            b['figure']={'path':str(png),'sha256':job.digest(png),'size_mm':[40,20],'offset_mm':[0,0]}
            # Only provenance is mocked: this test isolates native layout mechanics.
            with patch('figure_provenance.validate_figures',return_value=[]):build_hwpx([page],root/'out.hwpx')
            with zipfile.ZipFile(root/'out.hwpx')as z:
                section=ET.fromstring(z.read('Contents/section0.xml'));header=ET.fromstring(z.read('Contents/header.xml'))
            paras=[p for p in section.iter(HP+'p') if p.find('./'+HP+'run/'+HP+'pic') is not None]
            self.assertEqual(len(paras),1)
            self.assertNotIn('문장',''.join(paras[0].itertext()))
            hh='{http://www.hancom.co.kr/hwpml/2011/head}'
            style=next(p for p in header.iter(hh+'paraPr')if p.get('id')==paras[0].get('paraPrIDRef'))
            gap=next(n for n in style.iter() if n.tag.endswith('}next'))
            self.assertGreaterEqual(int(gap.get('value')),round(20*7200/25.4))

    def test_batch_assignment_rejects_reused_worker_without_partial_assignment(self):
        self.assertTrue(hasattr(job,'assign_many'),'batch assignment is missing')
        import test_restoration_job as fixtures
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);_,evidence=fixtures.JobTests().fixture(root)
            with self.assertRaisesRegex(ValueError,'one_page_per_independent_worker'):
                job.assign_many(root,[{'page':1,'worker_id':'same'},{'page':2,'worker_id':'same'}],evidence)
            self.assertEqual(job.load_json(root/'manifest.json')['assignments'],[])
            records=job.assign_many(root,[{'page':1,'worker_id':'a'},{'page':2,'worker_id':'b'}],evidence)
            self.assertEqual(len(records['assignments']),2)

    def test_cli_validation_returns_failed_exit_and_compact_build_has_receipt(self):
        import restore,contextlib,io
        self.assertTrue(hasattr(restore,'summarize'),'compact CLI response is missing')
        summary=restore.summarize('build',{'output':'example.hwpx','page_count':4,'pages':[{'objects':['large']}], 'visual_status':'not_verified'})
        self.assertNotIn('pages',summary);self.assertEqual(summary['receipt'],'example.build.json')
        with tempfile.TemporaryDirectory() as d:
            root,manifest,assignment=self.prepared(Path(d));page=self.page(manifest,assignment)
            page['questions'][0]['content'][0]['runs']=[{'kind':'equation','latex':r'\unknowncommand{x}'}]
            path=root/'result.json';job.save_json(path,page);out=io.StringIO()
            with contextlib.redirect_stdout(out):code=restore.main(['validate-page',str(root),str(path)])
            self.assertEqual(code,2);self.assertEqual(json.loads(out.getvalue())['status'],'failed')

if __name__=='__main__':unittest.main()
