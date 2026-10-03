import sys,tempfile,time,unittest
from unittest.mock import patch
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from restoration_single import dispatch
import restoration_single as single
import restoration_job as job
from test_restoration_service import MD,LAYOUT

class SingleReviewTests(unittest.TestCase):
    def setUp(self):
        import fitz
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'job';source=Path(self.temp.name)/'source.pdf'
        with fitz.open() as doc: doc.new_page();doc.save(source)
        result=self.call('prepare',source=str(source),question_pages=[1]);self.assertEqual(result['status'],'prepared',result)
        self.assertEqual(result['spawn_requests'][0]['role'],'producer')
        self.call('assign',page=1,worker_id='producer-1',evidence='Actual test worker creation response: producer-1')

    def call(self,action,**params): return dispatch(action,{'job':str(self.root),**params})

    def compose(self):
        self.assertEqual(self.call('submit_reading',page=1,markdown=MD)['status'],'accepted')
        result=self.call('compose',page=1,layout_markdown=LAYOUT)
        self.assertEqual(result['status'],'accepted',result)

    def test_single_submission_needs_no_comparison_or_approval(self):
        self.compose();m=job._manifest(self.root)
        self.assertEqual(len(m['assignments']),1)
        self.assertNotIn('ab_session',m['assignments'][0])
        self.assertFalse((self.root/'ab').exists())
        result=self.call('build',output=str(self.root/'out.hwpx'),title='test',school='test',year='2026',exam_title='test',native=False)
        self.assertEqual(result['status'],'built',result)
        self.assertEqual(self.call('finish_review',reviewer_id='reviewer',reviews=[{'page':1,'status':'passed','issues':[]}],review_evidence='test')['status'],'failed')

    def test_submission_detects_equation_errors_before_production(self):
        result=self.call('submit_reading',page=1,markdown=MD.replace('x-1',r'x\mid y'))
        self.assertEqual(result['status'],'failed',result)
        self.assertTrue(result['errors']);self.assertIn('q1',str(result['errors']))
        self.assertEqual(self.call('compose',page=1,layout_markdown=LAYOUT)['status'],'failed')

    def test_current_reading_required_after_edit_and_source_size_is_explicit(self):
        self.compose()
        result=self.call('inspect',page=1)
        self.assertGreater(result['source_size_px'][0],1000)
        self.assertEqual(self.call('submit_reading',page=1,markdown=MD.replace('x-1','x+1'))['status'],'accepted')
        result=self.call('build',output=str(self.root/'out.hwpx'),title='test',school='test',year='2026',exam_title='test',native=False)
        self.assertEqual(result['status'],'built',result)

    def test_source_crop_is_available_before_first_reading_submission(self):
        request={'id':'q1-detail','question_id':'q1','bbox_px':[0,0,40,40],
                 'reason':'The printed coefficient is unclear.'}
        result=self.call('inspect',page=1,target='source',requests=[request])
        self.assertEqual(result['status'],'ready_to_inspect',result)
        self.assertEqual(result['target'],'source')
        self.assertEqual(len(result['image_paths']),1)
        self.assertTrue(Path(result['image_paths'][0]).is_file())
        self.assertNotIn('1',job.load_json(self.root/'mcp/state.json')['pages'])

    def test_pre_submission_crop_requires_source_reason_and_valid_bbox(self):
        request={'id':'q1-detail','question_id':'q1','bbox_px':[0,0,40,40],
                 'reason':'The printed coefficient is unclear.'}
        for target,change in [('output',{}),('source',{'reason':'  '}),
                              ('source',{'bbox_px':[-1,0,40,40]})]:
            with self.subTest(target=target,change=change):
                result=self.call('inspect',page=1,target=target,requests=[request|change])
                self.assertEqual(result['status'],'failed',result)

    def test_submitted_reading_rejects_unregistered_source_question(self):
        self.assertEqual(self.call('submit_reading',page=1,markdown=MD)['status'],'accepted')
        request={'id':'unknown-detail','question_id':'q99','bbox_px':[0,0,40,40],
                 'reason':'Check this mark.'}
        result=self.call('inspect',page=1,target='source',requests=[request])
        self.assertEqual(result['status'],'failed',result)
        self.assertIn('known_question',result['message'])

    def test_changed_diagram_requires_recomposition_and_binding(self):
        import fitz
        from test_tikz_handoff import TikzHandoffTests
        base=Path(self.temp.name)/'figure';base.mkdir()
        TikzHandoffTests().fixture(base)
        with fitz.open() as doc:doc.new_page(width=144,height=72);doc.save(base/'drawing.pdf')
        receipt=job.load_json(base/'render.json');receipt['pdf']['sha256']=job.digest(base/'drawing.pdf')
        job.save_json(base/'render.json',receipt)
        markdown=MD.replace('1. 값은 $x-1$?','1. 값은 $x-1$?\n\n![](figure:f1)')
        self.assertEqual(self.call('submit_reading',page=1,markdown=markdown)['status'],'ready_for_figures')
        def select(width):
            out=base/str(width)
            result=single.batch.prepare_figures(self.root,1,[{'id':'f1','question_id':'q1','width_mm':width,'render_receipt':str(base/'render.json')}],out)
            state=job.load_json(self.root/'mcp/state.json')
            state['batches'][result['batch']]={'page':1,'context':single.figure_context(single.reading(self.root,state,1)),
                                              'input_sources':{}}
            job.save_json(self.root/'mcp/state.json',state)
            checks=job.load_json(result['items'][0]['review'])['checks']
            reviewed=self.call('review_figures',page=1,batch_path=result['batch'],reviews=[{'id':'f1','status':'passed','issues':[],'checks':{k:'passed' for k in checks}}])
            self.assertEqual(reviewed['status'],'accepted',reviewed)
        select(40)
        self.assertEqual(self.call('compose',page=1,layout_markdown=LAYOUT)['status'],'accepted')
        old=job.assemble(self.root)
        before=job.load_json(self.root/'mcp/state.json')['pages']['1'].copy()
        select(45)
        self.assertNotEqual(job.assemble(self.root),old)
        # Even accidentally retaining an old snapshot must fail its input binding.
        state=job.load_json(self.root/'mcp/state.json');state['pages']['1']=before
        job.save_json(self.root/'mcp/state.json',state)
        with self.assertRaisesRegex(ValueError,'compose_current'):job.assemble(self.root)
        self.assertEqual(self.call('compose',page=1,layout_markdown=LAYOUT)['status'],'accepted')
        self.assertNotEqual(job.assemble(self.root),old)

    def test_corrected_or_invalid_reading_reports_work_instead_of_old_output(self):
        self.fixture_native()
        self.call('submit_reading',page=1,markdown=MD.replace('x-1','x+1'))
        self.assertEqual(self.call('status',page=1)['status'],'accepted')
        self.assertEqual(self.call('status')['output_status'],'needs_rebuild')
        result=self.call('finish_review',reviewer_id='reviewer',reviews=[{'page':1,'status':'passed','issues':[]}],review_evidence='old')
        self.assertEqual(result['status'],'failed')
        self.call('submit_reading',page=1,markdown='invalid markdown')
        self.assertEqual(self.call('status',page=1)['status'],'needs_correction')

    def test_failed_accept_does_not_claim_new_composition(self):
        self.call('submit_reading',page=1,markdown=MD)
        with patch.object(job,'accept',side_effect=ValueError('synthetic accept failure')):
            self.assertEqual(self.call('compose',page=1,layout_markdown=LAYOUT)['status'],'failed')
        self.assertEqual(self.call('status',page=1)['status'],'accepted')

    def test_missing_review_packet_does_not_commit_new_output_binding(self):
        self.fixture_native()
        state=job.load_json(self.root/'mcp/state.json');old=state['native'].copy()
        receipt=self.root/'fixture-native.json';native=job.load_json(receipt)
        native['review_inputs']['pages'][0]['review_input']=str(self.root/'missing.json')
        job.save_json(receipt,native)
        for _ in range(2):
            self.assertEqual(self.call('status')['status'],'failed')
            self.assertEqual(job.load_json(self.root/'mcp/state.json')['native'],old)

    def test_exited_native_fails_immediately_and_active_native_never_duplicates(self):
        state=job.load_json(self.root/'mcp/state.json')
        state['native_run']={'receipt':str(self.root/'missing-native.json'),'started':time.time(),'pid':12345,'returncode':2}
        job.save_json(self.root/'mcp/state.json',state)
        self.assertEqual(self.call('status')['message'],'native_export_exited_without_receipt')
        state['native_run'].pop('returncode');state['native_run']['started']=time.time()-500
        job.save_json(self.root/'mcp/state.json',state)
        with patch.object(single,'native_running',return_value=True),patch.object(single.shared,'_perform') as build:
            self.assertEqual(self.call('build',output=str(self.root/'new.hwpx'),title='',school='',year='',exam_title='')['status'],'failed')
            build.assert_not_called()
            state['output_stale']=True;job.save_json(self.root/'mcp/state.json',state)
            self.assertEqual(self.call('build',output=str(self.root/'new.hwpx'),title='',school='',year='',exam_title='')['status'],'building')
            build.assert_not_called()

    def test_process_liveness_detects_actual_exit_without_hangul(self):
        import subprocess
        process=subprocess.Popen([sys.executable,'-c','pass'])
        single._NATIVE_PROCESSES[process.pid]=process
        process.wait(timeout=10)
        run={'pid':process.pid}
        self.assertFalse(single.native_running(run));self.assertEqual(run['returncode'],0)

    def test_two_pages_require_all_reviews_and_identical_output_preserves_them(self):
        import fitz
        self.root=Path(self.temp.name)/'two-pages';source=Path(self.temp.name)/'two.pdf'
        with fitz.open() as doc:doc.new_page();doc.new_page();doc.save(source)
        self.call('prepare',source=str(source),question_pages=[1,2])
        for n in (1,2):
            self.call('assign',page=n,worker_id='owner-'+str(n),evidence='synthetic worker owner-'+str(n))
            self.assertEqual(self.call('submit_reading',page=n,markdown=MD)['status'],'accepted')
            self.assertEqual(self.call('compose',page=n,layout_markdown=LAYOUT)['status'],'accepted')
        self.fixture_native(compose=False)
        def review(rows):
            tasks=self.call('status')['review_tasks']
            evidence='reviewer '+' '.join(t['source_image']+' '+t['output_image'] for t in tasks)
            return self.call('finish_review',reviewer_id='reviewer',reviews=rows,review_evidence=evidence)
        first={'page':1,'status':'passed','issues':[]};second={**first,'page':2}
        self.assertEqual(review([first,first])['status'],'failed')
        self.assertEqual(review([first])['status'],'pending_review')
        self.assertEqual(review([second])['status'],'complete')
        # New receipt alone does not require rereading identical verified pixels/content.
        receipt=self.root/'fixture-native.json';value=job.load_json(receipt);value['test_revision']=2;job.save_json(receipt,value)
        self.assertEqual(self.call('status')['status'],'complete')
        self.assertEqual(self.call('status')['reused_review_pages'],[1,2])
        self.assertEqual(self.call('status')['review_tasks'],[])

    def fixture_native(self,compose=True):
        if compose:self.compose()
        import restoration_single as single
        m=job._manifest(self.root);packets=[]
        for page in m['pages']:
            n=page['page'];out=self.root/f'fixture-output-{n}.png'
            out.write_bytes((self.root/page['image']['path']).read_bytes())
            packet=self.root/f'fixture-packet-{n}.json'
            job.save_json(packet,{'source_image':str(self.root/page['image']['path']),
                                'output_image':{'path':str(out),'sha256':job.digest(out)}})
            packets.append({'page':n,'review_input':str(packet)})
        artifact=self.root/'fixture.hwp';artifact.write_bytes(b'synthetic-test-only')
        native=self.root/'fixture-native.json'
        job.save_json(native,{'status':'rendered','job':str(self.root),'page_count':len(m['pages']),
             'pages_sha256':single.batch.pages_digest(job.assemble(self.root)),
             'artifacts':{'hwp':{'path':str(artifact),'sha256':job.digest(artifact)}},
             'review_inputs':{'status':'pending_review','pages':packets}})
        state=job.load_json(self.root/'mcp/state.json');state['native_run']={'receipt':str(native),'returncode':0}
        job.save_json(self.root/'mcp/state.json',state)
        result=self.call('status')
        task=result['review_tasks'][0]
        return task['source_image']+' '+task['output_image']

    def test_review_task_gives_image_sizes_for_targeted_inspection(self):
        from PIL import Image
        self.fixture_native()
        task=self.call('status')['review_tasks'][0]
        with Image.open(task['source_image']) as source:
            self.assertEqual(task['source_size_px'],list(source.size))
        with Image.open(task['output_image']) as output:
            self.assertEqual(task['output_size_px'],list(output.size))

    def test_existing_pending_review_gets_image_sizes_without_rebuild(self):
        from PIL import Image
        self.fixture_native()
        state=job.load_json(self.root/'mcp/state.json')
        for task in state['review_tasks']:
            task.pop('source_size_px',None)
            task.pop('output_size_px',None)
        job.save_json(self.root/'mcp/state.json',state)
        task=self.call('status')['review_tasks'][0]
        with Image.open(task['source_image']) as source:
            self.assertEqual(task['source_size_px'],list(source.size))
        with Image.open(task['output_image']) as output:
            self.assertEqual(task['output_size_px'],list(output.size))

    def test_one_independent_reviewer_and_changed_output_blocks_completion(self):
        paths=self.fixture_native();rows=[{'page':1,'status':'passed','issues':[]}]
        self.assertEqual(self.call('finish_review',reviewer_id='producer-1',reviews=rows,review_evidence='producer-1 '+paths)['status'],'failed')
        failed=[{'page':1,'status':'failed','issues':['q1 needs source check']}]
        self.assertEqual(self.call('finish_review',reviewer_id='reviewer',reviews=failed,review_evidence='reviewer '+paths)['status'],'pending_review')
        self.assertEqual(self.call('finish_review',reviewer_id='other',reviews=rows,review_evidence='other '+paths)['status'],'failed')
        self.assertEqual(self.call('finish_review',reviewer_id='reviewer',reviews=rows,review_evidence='reviewer '+paths)['status'],'complete')
        (self.root/'fixture.hwp').write_bytes(b'changed')
        self.assertEqual(self.call('finish_review',reviewer_id='reviewer',reviews=rows,review_evidence='reviewer '+paths)['status'],'failed')
