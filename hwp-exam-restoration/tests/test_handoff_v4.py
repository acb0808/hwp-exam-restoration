"""Public handoffs remove rediscovery without accepting unreviewed content."""
import contextlib,io,json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import restore,restoration_job as job
import test_efficiency as fixtures

class HandoffTests(unittest.TestCase):
    def call(self,args):
        out=io.StringIO()
        with contextlib.redirect_stdout(out): code=restore.main(args)
        return code,json.loads(out.getvalue())

    def test_prepare_literal_filename_and_assign_without_mapping_script(self):
        import fitz
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);source=base/'[2024] 한글 시험.pdf';root=base/'job'
            with fitz.open() as pdf:
                pdf.new_page();pdf.new_page();pdf.save(source)
            code,result=self.call(['prepare',str(source),str(root),'--legacy-workers'])
            self.assertEqual(code,0)
            self.assertIn('next',result)
            self.assertEqual([p['page'] for p in result['next']['spawn_requests']],[1,2])
            evidence=base/'actual-response.txt';raw=b'actual synthetic tool fixture: worker-a, worker-b';evidence.write_bytes(raw)
            code,assigned=self.call(['assign-many',str(root),'--workers','worker-a','worker-b','--evidence',str(evidence)])
            self.assertEqual(code,0)
            self.assertEqual(len(assigned['handoffs']),2)
            for number,h in enumerate(assigned['handoffs'],1):
                record=job._manifest(root)['assignments'][number-1]
                packet=job.load_json(job.load_json(record['worker_input'])['worker_brief'])
                self.assertEqual(packet['page'],number)
                self.assertIn('guide',packet)
                self.assertNotIn('skeleton',packet)
                self.assertNotIn('validation_command',packet)
                self.assertTrue(Path(packet['page_image']).is_file())
                self.assertEqual(len(packet['navigation_crops']),2)
                self.assertIn('source_views',packet['commands'])
                self.assertIn(h['worker_instructions'],h['message'])
                self.assertAlmostEqual(packet['scale']['mm_per_pixel'][0]*packet['scale']['page_size_px'][0],packet['page_size_mm'][0])
            manifest=job._manifest(root)
            self.assertEqual(manifest['accepted'],[])
            self.assertEqual(job._artifact(root,manifest['assignments'][0]['evidence']).read_bytes(),raw)

    def test_wrong_worker_count_leaves_assignments_empty(self):
        import fitz
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);source=base/'a.pdf';root=base/'job'
            with fitz.open() as pdf:pdf.new_page();pdf.new_page();pdf.save(source)
            job.prepare(source,root);evidence=base/'evidence';evidence.write_text('fixture')
            with self.assertRaisesRegex(ValueError,'worker_count'):
                self.call(['assign-many',str(root),'--workers','only-one','--evidence',str(evidence)])
            self.assertEqual(job._manifest(root)['assignments'],[])

    def test_automatic_output_is_fresh_and_legacy_explicit_path_still_works(self):
        with tempfile.TemporaryDirectory() as d:
            root,manifest,assignment=fixtures.EfficiencyTests().prepared(Path(d))
            page=fixtures.EfficiencyTests().page(manifest,assignment)
            draft={'schema':'restoration-draft/1',**{k:page[k] for k in ['regions','questions','issues']}}
            p=root/'filled.json';job.save_json(p,draft)
            code,first=self.call(['compile-draft',str(root),'1',str(p)])
            code2,second=self.call(['compile-draft',str(root),'1',str(p)])
            self.assertEqual((code,code2),(0,0));self.assertNotEqual(first['output'],second['output'])
            self.assertEqual(job.load_json(first['output']),page);self.assertFalse(first['accepted'])
            spec=root/'views.json';job.save_json(spec,[{'id':'detail','bbox_mm':[10,10,10,10]}])
            _,a=self.call(['source-views',str(root),'1',str(spec)])
            _,b=self.call(['source-views',str(root),'1',str(spec)])
            self.assertNotEqual(a['views'][0]['image']['path'],b['views'][0]['image']['path'])
            self.assertEqual(a['views'][0]['image']['sha256'],b['views'][0]['image']['sha256'])
            self.assertEqual(job._manifest(root)['accepted'],[])

    def test_missing_spec_identifies_exact_file_without_creating_output(self):
        with tempfile.TemporaryDirectory() as d:
            root,_,_=fixtures.EfficiencyTests().prepared(Path(d));missing=root/'missing-views.json'
            with self.assertRaisesRegex(ValueError,'input_file_missing.*missing-views.json'):
                self.call(['source-views',str(root),'1',str(missing)])

    def test_figure_summary_exposes_outputs_without_repeating_cache_fingerprints(self):
        receipt={'status':'pending_review','batch':'batch.json','errors':[],
                 'items':[{'id':'f1','png':'figure.png','review':'review.json',
                           'reused':True,'render_conditions':{'large':'cache metadata'}}]}
        value=restore.summarize('figures',receipt)
        self.assertEqual(value['items'][0]['png'],'figure.png')
        self.assertNotIn('render_conditions',value['items'][0])
        self.assertIn('render_conditions',receipt['items'][0])

if __name__=='__main__':unittest.main()
