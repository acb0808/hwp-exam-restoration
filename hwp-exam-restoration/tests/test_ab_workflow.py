"""AB agreement is evidence, never automatic source approval or figure review."""
import copy,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import restoration_job as job

class ABWorkflowTests(unittest.TestCase):
    def fixture(self,d):
        import fitz
        import restoration_ab as ab
        source=Path(d)/'source.pdf'
        with fitz.open() as pdf:pdf.new_page();pdf.save(source)
        root=Path(d)/'job';m=job.prepare(source,root)
        evidence=Path(d)/'spawn.txt';evidence.write_text('actual tool fixture: reader-a reader-b')
        session=ab.assign_readers(root,1,'reader-a','reader-b',evidence)
        def reading(worker):
            return {'schema':'restoration-reading/1','page':1,'worker_id':worker,
                'source_sha256':m['source']['sha256'],'complete':True,'issues':[],
                'questions':[{'id':'q1','column':'left','uncertain':False,'content':[
                    {'kind':'paragraph','runs':[{'kind':'text','text':'1. 값은? '},{'kind':'equation','latex':'x-1'}]}]},
                    {'id':'q2','column':'right','uncertain':False,'content':[
                    {'kind':'paragraph','runs':[{'kind':'text','text':'2. 값은?'}]}]}]}
        return root,m,session,reading

    def submit_pair(self,root,reading,change=False):
        import restoration_ab as ab
        a=reading('reader-a');b=reading('reader-b')
        if change:b['questions'][0]['content'][0]['runs'][1]['latex']='x+1'
        for role,value in [('a',a),('b',b)]:
            p=root/(role+'.json');job.save_json(p,value);ab.submit_reading(root,1,role,p)
        return ab.compare_page(root,1)

    def approval(self,root,**kw):
        state=job.load_json(root/'ab/page-0001/session.json')
        value={'comparison_sha256':state['comparison']['sha256'],'reviewer_id':'master','source_overview_checked':True,'question_inventory_checked':True,
            'figure_inventory_checked':True,'issues':[], 'resolutions':[]}
        value.update(kw);p=root/'decision.json';job.save_json(p,value);return p

    def test_agreement_still_requires_master_approval_and_only_owner_builds(self):
        import restoration_ab as ab
        with tempfile.TemporaryDirectory() as d:
            root,m,assigned,reading=self.fixture(d)
            self.assertEqual(len(assigned['handoffs']),2)
            for h in assigned['handoffs']:
                text=Path(h['worker_instructions']).read_text(encoding='utf-8')
                self.assertNotIn('compile-draft',text)
                self.assertNotIn('tikz-exam.md',text)
            comparison=self.submit_pair(root,reading)
            self.assertEqual(comparison['status'],'agreement')
            with self.assertRaisesRegex(ValueError,'approval'):ab.production_state(root,1)
            result=ab.approve_page(root,1,self.approval(root))
            self.assertEqual(result['owner_worker_id'],'reader-a')
            self.assertFalse(result['accepted'])
            self.assertEqual(ab.production_state(root,1)['worker_a'],'reader-a')

    def test_disagreement_requires_crop_and_explicit_resolution(self):
        import restoration_ab as ab
        with tempfile.TemporaryDirectory() as d:
            root,m,assigned,reading=self.fixture(d);self.submit_pair(root,reading,True)
            with self.assertRaisesRegex(ValueError,'resolution'):ab.approve_page(root,1,self.approval(root))
            with self.assertRaisesRegex(ValueError,'disputed'):
                ab.prepare_dispute_views(root,1,[{'id':'wrong','question_id':'q2','bbox_px':[1,1,30,30],'coordinate_space':'page'}])
            ab.prepare_dispute_views(root,1,[{'id':'q1-detail','question_id':'q1','bbox_px':[1,1,30,30],'coordinate_space':'page'}])
            resolved={'question_id':'q1','choice':'a','source_checked':True,'reason':'Original minus sign confirmed.'}
            self.assertEqual(ab.approve_page(root,1,self.approval(root,resolutions=[resolved]))['status'],'approved_pending_production')

    def test_same_worker_cannot_supply_both_and_foreign_reading_is_rejected(self):
        import restoration_ab as ab
        with tempfile.TemporaryDirectory() as d:
            root,m,assigned,reading=self.fixture(d)
            p=root/'foreign.json';job.save_json(p,reading('reader-a'))
            with self.assertRaises(ValueError):ab.submit_reading(root,1,'b',p)
            self.assertEqual(job._manifest(root)['accepted'],[])

    def layout(self):
        return {'schema':'restoration-layout/1','regions':[
            {'id':'l','bbox_mm':[10,20,85,250]}, {'id':'r','bbox_mm':[110,20,85,250]}],
            'questions':[{'id':'q1','region_id':'l','bbox_mm':[10,20,85,100]},
                         {'id':'q2','region_id':'r','bbox_mm':[110,20,85,100]}]}

    def test_composition_keeps_approved_text_and_accept_rejects_bypass(self):
        import restoration_ab as ab
        from restoration_draft import compile_draft
        with tempfile.TemporaryDirectory() as d:
            root,m,assigned,reading=self.fixture(d);self.submit_pair(root,reading)
            ab.approve_page(root,1,self.approval(root))
            result=ab.compose_page(root,1,self.layout(),root/'composed.json')
            self.assertEqual(result['status'],'compiled',result)
            original=job.load_json(root/'composed.json')
            changed=copy.deepcopy(original);changed['questions'][0]['content'][0]['runs'][1]['latex']='x+1'
            job.save_json(root/'bypass.json',changed)
            with self.assertRaisesRegex(ValueError,'unchanged_approved'):job.accept(root,root/'bypass.json')
            job.accept(root,root/'composed.json');self.assertEqual(job.assemble(root),[original])
            layout=self.layout();layout['questions'][0]['content']=[]
            with self.assertRaises(ValueError):ab.compose_page(root,1,layout,root/'unsafe.json')

    def test_resubmission_invalidates_approval_and_old_composition(self):
        import restoration_ab as ab
        with tempfile.TemporaryDirectory() as d:
            root,m,assigned,reading=self.fixture(d);self.submit_pair(root,reading)
            ab.approve_page(root,1,self.approval(root));ab.compose_page(root,1,self.layout(),root/'out.json')
            job.accept(root,root/'out.json')
            revised=reading('reader-a');revised['questions'][0]['content'][0]['runs'][1]['latex']='x+1'
            job.save_json(root/'revised.json',revised);ab.submit_reading(root,1,'a',root/'revised.json')
            with self.assertRaises(ValueError):job.assemble(root)
            self.assertEqual(ab.compare_page(root,1)['status'],'needs_resolution')
            with self.assertRaisesRegex(ValueError,'approval'):ab.production_state(root,1)

    def test_figure_production_gated_and_unplanned_figures_rejected(self):
        import restoration_ab as ab
        with tempfile.TemporaryDirectory() as d:
            root,m,assigned,reading=self.fixture(d);self.submit_pair(root,reading)
            with self.assertRaisesRegex(ValueError,'approval'):
                ab.check_figure_requests(root,1,[{'question_id':'q1','id':'q1-figure-1'}])
            ab.approve_page(root,1,self.approval(root))
            with self.assertRaisesRegex(ValueError,'not_in_approved'):
                ab.check_figure_requests(root,1,[{'question_id':'q1','id':'q1-figure-1'}])

    def test_agreed_but_suspect_question_can_be_flagged_and_crop_reused(self):
        import restoration_ab as ab
        with tempfile.TemporaryDirectory() as d:
            root,m,assigned,reading=self.fixture(d);self.submit_pair(root,reading)
            rows=[{'id':'q1-detail','question_id':'q1','bbox_px':[1,1,30,30],
                   'coordinate_space':'page','reason':'Minus sign is faint despite agreement.'}]
            one=ab.prepare_dispute_views(root,1,rows);two=ab.prepare_dispute_views(root,1,rows)
            self.assertFalse(one['reused']);self.assertTrue(two['reused']);self.assertEqual(one['index'],two['index'])
            with self.assertRaisesRegex(ValueError,'resolution'):ab.approve_page(root,1,self.approval(root))
            resolution={'question_id':'q1','choice':'a','source_checked':True,'reason':'Original checked.'}
            ab.approve_page(root,1,self.approval(root,resolutions=[resolution]))
            view=job.load_json(one['index'])['views'][0]['image']['path']
            Path(view).write_bytes(b'changed evidence')
            with self.assertRaisesRegex(ValueError,'hash_mismatch'):ab.production_state(root,1)

    def test_source_snapshot_tampering_rejected_and_incomplete_not_approved(self):
        import restoration_ab as ab
        with tempfile.TemporaryDirectory() as d:
            root,m,assigned,reading=self.fixture(d);self.submit_pair(root,reading)
            value=reading('reader-b');value['complete']=False
            job.save_json(root/'incomplete.json',value);ab.submit_reading(root,1,'b',root/'incomplete.json')
            ab.compare_page(root,1)
            with self.assertRaisesRegex(ValueError,'incomplete'):ab.approve_page(root,1,self.approval(root))
            state=job.load_json(root/'ab/page-0001/session.json')
            (root/state['readings']['a']['path']).write_bytes(b'{}')
            with self.assertRaisesRegex(ValueError,'hash_mismatch'):ab.compare_page(root,1)

    def test_missing_question_template_and_stale_decision(self):
        import restoration_ab as ab
        with tempfile.TemporaryDirectory() as d:
            root,m,assigned,reading=self.fixture(d);old=self.submit_pair(root,reading)
            stale=job.load_json(self.approval(root))
            b=reading('reader-b');b['questions'].pop()
            job.save_json(root/'missing.json',b);ab.submit_reading(root,1,'b',root/'missing.json')
            compared=ab.compare_page(root,1)
            self.assertNotEqual(old['decision_template'],compared['decision_template'])
            decision=job.load_json(compared['decision_template'])
            self.assertEqual(decision['question_order'],['q1','q2'])
            with self.assertRaisesRegex(ValueError,'current_comparison'):ab.approve_page(root,1,stale)
            crops=ab.prepare_dispute_views(root,1,[{'id':'q2','question_id':'q2','bbox_px':[1,1,30,30],'coordinate_space':'page'}])
            decision.update(reviewer_id='master',source_overview_checked=True,question_inventory_checked=True,figure_inventory_checked=True)
            decision['resolutions'][0].update(choice='a',source_checked=True,reason='q2 exists on original.')
            ab.approve_page(root,1,decision)

    def test_reused_worker_and_same_worker_assignment_rejected(self):
        import restoration_ab as ab
        with tempfile.TemporaryDirectory() as d:
            root,m,assigned,reading=self.fixture(d)
            with self.assertRaisesRegex(ValueError,'independent_worker'):
                ab.assign_readers(root,1,'same','same',Path(d)/'spawn.txt')

    def test_default_handoff_contains_two_restricted_roles_per_page(self):
        import restoration_ab as ab
        with tempfile.TemporaryDirectory() as d:
            root,m,assigned,reading=self.fixture(d)
            handoff=ab.preparation_handoff(root,m)
            self.assertEqual([r['role'] for r in handoff['spawn_requests']],['a','b'])
            self.assertTrue(all(r['type']=='hwp-restoration-reader' for r in handoff['spawn_requests']))

    def test_approved_figure_requires_review_before_composition_and_acceptance(self):
        import fitz
        import restoration_ab as ab
        import restoration_batch as batch
        import test_tikz_handoff
        with tempfile.TemporaryDirectory() as d:
            root,m,assigned,reading=self.fixture(d)
            def with_figure(worker):
                value=reading(worker);value['questions'][0]['content'].append(
                    {'kind':'paragraph','runs':[],'figure_ref':'q1-figure-1'})
                return value
            self.submit_pair(root,with_figure);ab.approve_page(root,1,self.approval(root))
            base=Path(d);test_tikz_handoff.TikzHandoffTests().fixture(base)
            # Synthetic receipt tests the protocol, not a real model's visual judgment.
            with fitz.open() as pdf:
                p=pdf.new_page(width=144,height=72);p.draw_line((10,10),(100,50))
                p.get_pixmap().save(base/'drawing.png');pdf.save(base/'replacement.pdf')
            (base/'drawing.pdf').write_bytes((base/'replacement.pdf').read_bytes())
            receipt=job.load_json(base/'render.json')
            for key in ('pdf','png'):receipt[key]['sha256']=job.digest(receipt[key]['path'])
            job.save_json(base/'render.json',receipt)
            rows=[{'id':'q1-figure-1','question_id':'q1','render_receipt':str(base/'render.json'),'width_mm':40}]
            prepared=batch.prepare_figures(root,1,rows,root/'figures')
            pending=batch.finalize_figures(root,1,root/'figures');self.assertNotEqual(pending['status'],'ready')
            item=job.load_json(root/'figures/batch.json')['items'][0]
            review_path=Path(item['review']);review=job.load_json(review_path)
            review.update(status='passed',checks={k:'passed' for k in review['checks']});job.save_json(review_path,review)
            finalized=batch.finalize_figures(root,1,root/'figures');self.assertEqual(finalized['status'],'ready',finalized)
            result=ab.compose_page(root,1,self.layout(),root/'figure-result.json',figures=Path(finalized['figures']))
            self.assertEqual(result['status'],'compiled',result)
            job.accept(root,root/'figure-result.json');self.assertEqual(len(job.assemble(root)),1)

if __name__=='__main__':unittest.main()
