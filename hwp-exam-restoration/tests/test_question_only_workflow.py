import sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import restoration_job as job
import restoration_single as single
from unittest.mock import patch, MagicMock
from restoration_single import dispatch
from test_restoration_service import MD


class QuestionOnlyTests(unittest.TestCase):
    def setUp(self):
        import fitz
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'job'
        self.source = Path(self.tmp.name) / 'source.pdf'
        with fitz.open() as pdf:
            for _ in range(4): pdf.new_page()
            pdf.save(self.source)

    def call(self, action, **params):
        return dispatch(action, dict(job=str(self.root), **params))

    def test_preview_selection_reuses_images_and_only_assigns_questions(self):
        result = self.call('prepare', source=str(self.source))
        self.assertEqual(result['status'], 'needs_selection')
        self.assertNotIn('spawn_requests', result)
        image = self.root / 'pages/page-0003.png'
        stamp = image.stat().st_mtime_ns
        result = self.call('prepare', source=str(self.source), question_pages=[3, 4])
        self.assertEqual(result['status'], 'prepared', result)
        self.assertEqual([p['page'] for p in result['spawn_requests']], [3, 4])
        self.assertEqual(image.stat().st_mtime_ns, stamp)
        self.assertEqual(self.call('assign', page=1, worker_id='cover', evidence='test cover')['status'], 'failed')

    def test_markdown_alone_accepts_selected_pages_and_builds_two_pages(self):
        self.call('prepare', source=str(self.source), question_pages=[3, 4])
        for n in (3, 4):
            self.call('assign', page=n, worker_id=f'owner-{n}', evidence=f'test owner-{n}')
            result = self.call('submit_reading', page=n, markdown=MD)
            self.assertEqual(result['status'], 'accepted', result)
        self.assertEqual([p['page_number'] for p in job.assemble(self.root)], [3, 4])
        self.assertFalse(list(self.root.rglob('layout.md')))
        self.assertFalse(list(self.root.rglob('draft.json')))
        self.assertFalse((self.root/'inputs').exists())
        self.assertFalse((self.root/'environment.json').exists())
        result = self.call('build', output=str(self.root/'exam.hwpx'), title='test', school='school', year='2026', exam_title='exam', native=False)
        self.assertEqual(result['status'], 'built', result)

    def test_cover_cannot_be_submitted_as_a_question(self):
        self.call('prepare', source=str(self.source), question_pages=[3])
        self.call('assign', page=3, worker_id='owner', evidence='test owner')
        result = self.call('submit_reading', page=3, markdown='## left\n### cover\n시험 안내\n')
        self.assertEqual(result['status'], 'failed', result)

    def test_skipping_internal_blank_preserves_source_ids_when_building(self):
        self.call('prepare',source=str(self.source),question_pages=[1,3])
        for n in (1,3):
            self.call('assign',page=n,worker_id=f'owner-{n}',evidence=f'test owner-{n}')
            self.assertEqual(self.call('submit_reading',page=n,markdown=MD)['status'],'accepted')
        result=self.call('build',output=str(self.root/'selected.hwpx'),title='test',school='test',year='2026',exam_title='test',native=False)
        self.assertEqual(result['status'],'built',result)
        self.assertEqual([p['page_number'] for p in job.assemble(self.root)],[1,3])

    def test_verified_preflight_block_can_retry_without_cleanup(self):
        from runtime_paths import automation_root
        sys.path.insert(0,str(automation_root()/'scripts'))
        import native_layout
        self.call('prepare',source=str(self.source),question_pages=[3])
        self.call('assign',page=3,worker_id='owner',evidence='test owner')  # v2.7.5: build needs bound slots
        source=self.root/'fixture.hwpx';source.write_bytes(b'synthetic preflight only')
        with patch.object(native_layout.importlib.util,'find_spec',return_value=None),patch.object(native_layout.subprocess,'Popen') as process:
            native=native_layout.render_native(source,self.root/'preflight')
            process.assert_not_called()
        self.assertEqual(native['status'],'blocked')
        self.assertEqual(native.get('cleanup'),'no_session_started')
        receipt=self.root/'preflight/restoration-native.json';job.save_json(receipt,native)
        state=job.load_json(self.root/'mcp/state.json')
        state['native_run']={'receipt':str(receipt),'returncode':2};job.save_json(self.root/'mcp/state.json',state)
        with patch.object(single.shared,'_perform',return_value={'status':'built'}) as build:
            result=self.call('build',output=str(self.root/'retry.hwpx'),native=False)
            self.assertEqual(result['status'],'built',result)
            build.assert_called_once()

    def test_page_selection_is_frozen_after_assignment(self):
        self.call('prepare', source=str(self.source), question_pages=[3])
        self.call('assign', page=3, worker_id='owner', evidence='test owner')
        self.assertEqual(self.call('prepare', source=str(self.source), question_pages=[4])['status'], 'failed')

    def test_native_review_matches_source_three_to_output_one(self):
        import fitz
        self.call('prepare', source=str(self.source), question_pages=[3,4])
        for n in (3,4):
            self.call('assign',page=n,worker_id=f'owner-{n}',evidence=f'test owner-{n}')
            self.call('submit_reading',page=n,markdown=MD)
        pdf=self.root/'fixture-output.pdf'
        with fitz.open() as doc:
            doc.new_page();doc.new_page();doc.save(pdf)
        native={'status':'rendered','job':str(self.root),'page_count':2,
                'pages_sha256':single.batch.pages_digest(job.assemble(self.root)),
                'artifacts':{'pdf':{'path':str(pdf),'sha256':job.digest(pdf)}}}
        native['review_inputs']=single.batch.review_pack(self.root,native,self.root/'review')
        rows=native['review_inputs']['pages']
        self.assertEqual([(r['page'],r['output_page']) for r in rows],[(3,1),(4,2)])
        self.assertEqual(job.load_json(rows[0]['review_input'])['output_crops'],[])
        receipt=self.root/'fixture-native.json';job.save_json(receipt,native)
        state=job.load_json(self.root/'mcp/state.json')
        state['native_run']={'receipt':str(receipt),'returncode':0};job.save_json(self.root/'mcp/state.json',state)
        result=self.call('status');self.assertEqual(result['status'],'pending_review',result)
        evidence='reviewer '+' '.join(t['source_image']+' '+t['output_image'] for t in result['review_tasks'])
        # Synthetic decisions check identity mapping only, not visual correctness.
        reviewed=self.call('finish_review',reviewer_id='reviewer',review_evidence=evidence,
                           reviews=[{'page':n,'status':'passed','issues':[]} for n in (3,4)])
        self.assertEqual(reviewed['status'],'complete',reviewed)

    def test_build_starts_the_owned_supervisor_and_reports_its_receipt(self):
        # v2.7.9: the build no longer holds the job until the export ends (test_client_timeouts).
        self.call('prepare',source=str(self.source),question_pages=[3])
        self.call('assign',page=3,worker_id='owner',evidence='test owner')  # v2.7.5: build needs bound slots
        proc=MagicMock();proc.pid=987654
        with patch.object(single.shared,'_perform',return_value={'status':'built'}), \
             patch.object(single.subprocess,'Popen',return_value=proc) as popen, \
             patch.object(single,'collect_native',return_value={'status':'pending_review'}):
            self.assertEqual(self.call('build',output=str(self.root/'out.hwpx'))['status'],'pending_review')
            popen.assert_called_once()
        self.assertEqual(job.load_json(self.root/'mcp/state.json')['native_run']['pid'],987654)
        single._NATIVE_PROCESSES.pop(proc.pid,None)

    def test_unknown_cleanup_blocks_relaunch_even_after_supervisor_exit(self):
        self.call('prepare',source=str(self.source),question_pages=[3])
        self.call('assign',page=3,worker_id='owner',evidence='test owner')  # v2.7.5: build needs bound slots
        state=job.load_json(self.root/'mcp/state.json')
        receipt=self.root/'failed-native.json'
        job.save_json(receipt,{'status':'failed','cleanup':'no_ownership_receipt_no_cleanup'})
        state['native_run']={'receipt':str(receipt),'returncode':2};job.save_json(self.root/'mcp/state.json',state)
        # The session the failed export had started cannot be shown to be gone: still no relaunch.
        with patch.object(single.shared,'_perform') as build,              patch('restoration_native_queue.owned_session_gone',return_value=False):
            result=self.call('build',output=str(self.root/'new.hwpx'))
            self.assertEqual(result.get('message'),'native_cleanup_unconfirmed',result)
            build.assert_not_called()

    def test_export_that_left_no_session_can_be_built_again(self):
        # Killed with the CLI, or refused because another job held Hangul: no receipt of a clean close, but no
        # Hangul of its own either (no ownership record). This used to block the job for good.
        self.call('prepare',source=str(self.source),question_pages=[3])
        self.call('assign',page=3,worker_id='owner',evidence='test owner')
        state=job.load_json(self.root/'mcp/state.json')
        for name,previous in (('busy',{'status':'failed','cleanup':'no_ownership_receipt_no_cleanup'}),('killed',None)):
            run=self.root/name;run.mkdir();receipt=run/'restoration-native.json'
            if previous:job.save_json(receipt,previous)
            state['native_run']={'receipt':str(receipt),'returncode':2};job.save_json(self.root/'mcp/state.json',state)
            proc=MagicMock();proc.pid=987650;proc.wait.return_value=0
            with patch.object(single.shared,'_perform',return_value={'status':'built'}) as build,                  patch.object(single.subprocess,'Popen',return_value=proc),                  patch.object(single,'collect_native',return_value={'status':'pending_review'}):
                self.assertEqual(self.call('build',output=str(self.root/f'{name}.hwpx'))['status'],'pending_review',name)
                build.assert_called_once()
            single._NATIVE_PROCESSES.pop(proc.pid,None)
            state=job.load_json(self.root/'mcp/state.json')


class AutomaticFigureSizeTests(unittest.TestCase):
    def test_intended_figure_size_survives_source_units_and_template_projection(self):
        from copy import deepcopy
        from restoration_draft import _expand
        from exam_template import project_pages
        import restoration_draft
        profile=job.load_json(Path(single.__file__).resolve().parents[1]/'assets/templates/pdf2hwp-grid/profile.json')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);review=root/'review.json';job.save_json(review,{'question_id':'q1'})
            for source_width in (100,210,297):
                for column_count in (1,2):
                    with self.subTest(source_width=source_width,column_count=column_count):
                        manifest={'source':{'sha256':'test'},'pages':[{'page':1,'width_mm':source_width,'height_mm':297}]}
                        qs=[{'id':'q1','column':'left','content':[{'kind':'paragraph','runs':[],'figure_ref':'f1'}]}]
                        if column_count==2:qs.append({'id':'q2','column':'right','content':[{'kind':'paragraph','runs':[{'kind':'text','text':'second'}]}]})
                        with patch.object(job,'_manifest',return_value=manifest):
                            layout=single.shared.parse_layout_markdown(single.automatic_layout(root,1,{'questions':qs}))
                        draft={'schema':'restoration-draft/1','regions':layout['regions'],
                               'questions':[{**g,'content':q['content']} for g,q in zip(layout['questions'],qs)],'issues':[]}
                        intended_width=120 if column_count==1 else 60
                        figure={'size_mm':[intended_width,40],'offset_mm':[1,2],
                                'tikz':{'review':{'path':str(review),'sha256':job.digest(review)}}}
                        before=deepcopy(figure)
                        assignment={'assignment_id':'test','worker_id':'test','workflow':'single-review/1'}
                        with patch.object(restoration_draft,'_figure_map',return_value={'f1':figure}):
                            page=_expand(root,1,draft,manifest,assignment,None)
                        q=page['questions'][0];converted=q['content'][0]['figure']
                        self.assertLessEqual(converted['size_mm'][0]+converted['offset_mm'][0],q['bbox_mm'][2])
                        output=project_pages([page],profile)[0]['questions'][0]['content'][0]['figure']
                        for field in ('size_mm','offset_mm'):
                            for actual,expected in zip(output[field],before[field]):self.assertAlmostEqual(actual,expected)
                        self.assertEqual(figure,before,'Reviewed figure must remain immutable')
