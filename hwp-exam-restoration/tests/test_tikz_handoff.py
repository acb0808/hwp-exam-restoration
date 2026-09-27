"""Receipts here are synthetic unit fixtures, never evidence for a real exam."""
import copy,hashlib,json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))

class TikzHandoffTests(unittest.TestCase):
    def fixture(self,root):
        def artifact(name,data):
            p=root/name;p.write_bytes(data)
            return {'path':str(p),'sha256':hashlib.sha256(data).hexdigest()}
        render={'schema':'tikz-render/1','status':'rendered_pending_review','renderer':'tikz','exit_code':0,
                'source':artifact('drawing.tex',b'\\begin{tikzpicture}\\draw (0,0)--(1,1);\\end{tikzpicture}'),
                'pdf':artifact('drawing.pdf',b'pdf-fixture'),'png':artifact('drawing.png',b'png-fixture'),
                'log':artifact('compile.log',b'synthetic unit test')}
        receipt=artifact('render.json',json.dumps(render).encode())
        page={'source_sha256':'a'*64,'page_number':1,'worker_id':'worker1','blocks':[],
              'questions':[{'id':'q1','content':[{'kind':'paragraph','figure':{
                  **render['png'],'size_mm':[20,20],'offset_mm':[0,0]}}]}]}
        review={'schema':'tikz-review/1','status':'passed','source_sha256':'a'*64,'page_number':1,
                'question_id':'q1','worker_id':'worker1','render_sha256':receipt['sha256'],
                'checks':{k:'passed' for k in ('geometry','labels','marks','source_comparison')},'issues':[]}
        review_ref=artifact('review.json',json.dumps(review).encode())
        figure=page['questions'][0]['content'][0]['figure']
        figure['tikz']={'render':receipt,'review':review_ref}
        return page,figure,review

    def test_crop_without_tikz_receipt_is_rejected(self):
        from figure_provenance import validate_figures
        with tempfile.TemporaryDirectory() as d:
            page,figure,_=self.fixture(Path(d));del figure['tikz']
            with self.assertRaisesRegex(ValueError,'tikz_provenance_required'):validate_figures(page)

    def test_bound_review_and_render_are_accepted(self):
        from figure_provenance import validate_figures
        with tempfile.TemporaryDirectory() as d:
            page,_,_=self.fixture(Path(d));self.assertEqual(len(validate_figures(page)),1)

    def test_changed_source_png_or_receipt_is_rejected(self):
        from figure_provenance import validate_figures
        for name in ('drawing.tex','drawing.png','drawing.pdf','compile.log','render.json','review.json'):
            with self.subTest(name=name),tempfile.TemporaryDirectory() as d:
                page,_,_=self.fixture(Path(d));Path(d,name).write_bytes(b'tampered')
                with self.assertRaisesRegex(ValueError,'hash_mismatch'):validate_figures(page)

    def test_review_cannot_be_reused_for_another_page_worker_or_question(self):
        from figure_provenance import validate_figures
        for field,value in [('page_number',2),('worker_id','worker2'),('source_sha256','b'*64)]:
            with self.subTest(field=field),tempfile.TemporaryDirectory() as d:
                page,_,_=self.fixture(Path(d));page[field]=value
                with self.assertRaisesRegex(ValueError,'review_binding_mismatch'):validate_figures(page)
        with tempfile.TemporaryDirectory() as d:
            page,_,_=self.fixture(Path(d));page['questions'][0]['id']='q2'
            with self.assertRaisesRegex(ValueError,'review_binding_mismatch'):validate_figures(page)

    def test_no_diagrams_needs_no_tikz_engine(self):
        from figure_provenance import validate_figures
        self.assertEqual(validate_figures({'blocks':[],'questions':[]}),[])
