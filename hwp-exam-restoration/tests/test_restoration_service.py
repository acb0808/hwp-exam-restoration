import copy
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from restoration_service import dispatch_legacy as dispatch, parse_layout_markdown
import restoration_job as job

MD = '## left\n### q1\n1. 값은 $x-1$?\n\n## right\n### q2\n2. 다음 값을 구하시오.\n'
LAYOUT = '''units: mm
| type | id | region | x | y | width | height |
| --- | --- | --- | --- | --- | --- | --- |
| region | l | - | 10 | 20 | 85 | 250 |
| region | r | - | 110 | 20 | 85 | 250 |
| question | q1 | l | 10 | 20 | 85 | 100 |
| question | q2 | r | 110 | 20 | 85 | 100 |
'''

class ServiceTests(unittest.TestCase):
    def setUp(self):
        import fitz
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        source = Path(self.tmp.name) / 'source.pdf'
        with fitz.open() as pdf:
            pdf.new_page(); pdf.save(source)
        self.root = Path(self.tmp.name) / 'job'
        result = dispatch('prepare', dict(source=str(source), job=str(self.root)))
        self.assertEqual(result['status'], 'prepared', result)
        self.call('assign', worker_a='actual-a', worker_b='actual-b', evidence='actual tool response a/b')

    def call(self, action, **params):
        return dispatch(action, dict(job=str(self.root), page=1, **params))

    def pair(self, different=False):
        self.call('submit_reading', role='a', markdown=MD)
        return self.call('submit_reading', role='b', markdown=MD.replace('x-1','x+1') if different else MD)

    def decision(self):
        value = self.call('status')['decision_template']
        value.update(reviewer_id='actual-master', source_overview_checked=True,
                     question_inventory_checked=True, figure_inventory_checked=True)
        return value

    def approve(self):
        self.pair(); viewed = self.call('inspect')
        d = self.decision(); d['inspection_ids'] = viewed['inspection_ids']
        result = self.call('approve', decision=d)
        self.assertEqual(result['status'], 'approved_pending_production', result)

    def test_identity_injected_and_blind_submission_response(self):
        result = self.pair(True)
        self.assertNotIn('decision_template', result)
        self.assertNotIn('disputes', result)
        state = job.load_json(self.root/'ab/page-0001/session.json')
        for role in ('a','b'):
            value = job.load_json(job._artifact(self.root,state['readings'][role]))
            self.assertEqual(value['worker_id'], 'actual-'+role)
        self.assertEqual(self.call('status')['disputed_ids'], ['q1'])
        self.assertTrue(list((self.root/'mcp/evidence').glob('*.md')))

    def test_agreement_does_not_approve_and_requires_delivered_image(self):
        self.pair()
        self.assertEqual(self.call('status')['status'], 'agreement')
        failed = self.call('approve', decision=self.decision())
        self.assertEqual(failed['status'], 'failed')
        self.assertIn('inspect', failed['next_action'])
        self.approve()

    def test_crop_receipt_required_and_stale_after_resubmit(self):
        self.pair(True)
        overview = self.call('inspect')
        crop = self.call('inspect', requests=[dict(id='q1-detail',question_id='q1',bbox_px=[10,20,100,100])])
        self.assertEqual(len(crop['_images']),1,crop)
        d = self.decision(); d['inspection_ids'] = overview['inspection_ids']+crop['inspection_ids']
        d['resolutions'] = [dict(question_id='q1',choice='a',source_checked=True,reason='Minus confirmed in source.')]
        self.call('submit_reading', role='b', markdown=MD.replace('x-1','x+2'))
        d['comparison_sha256'] = self.decision()['comparison_sha256']
        self.assertEqual(self.call('approve',decision=d)['status'],'failed')

    def test_compose_accept_build_and_asset_scope(self):
        self.approve()
        result = self.call('compose',layout_markdown=LAYOUT)
        self.assertEqual(result['status'],'accepted',result)
        self.assertEqual(self.call('status')['status'],'accepted')
        self.assertIn('Do not recompose',self.call('status')['next_action'])
        self.assertEqual(len(job.assemble(self.root)),1)
        built = dispatch('build',dict(job=str(self.root), output=str(self.root/'out.hwpx'),
                    title='시험',school='학교',year='2026',exam_title='시험',native=False))
        self.assertNotEqual(built['status'],'failed',built)
        self.assertEqual(built['visual_status'],'not_verified')
        self.assertTrue((self.root/'out.hwpx').is_file())
        self.assertEqual(self.call('finish_review',status='passed',issues=[])['status'],'failed')
        rogue = self.root/'secret.py'; rogue.write_text('print(1)')
        self.assertEqual(self.call('read_asset',asset_path=str(rogue))['status'],'failed')

    def test_bad_markdown_is_actionable_without_source_code(self):
        result = self.call('submit_reading',role='a',markdown='## left\n### q1\n$x')
        self.assertEqual(result['status'],'failed')
        self.assertIn('line',result['message'].lower())
        self.assertIn('Markdown',result['next_action'])
        self.assertNotIn('Traceback',result['message'])

    def test_layout_rejects_unknown_rows_and_nonfinite_values(self):
        value = parse_layout_markdown(LAYOUT)
        self.assertEqual(value['questions'][0]['bbox_mm'],[10,20,85,100])
        for changed in (LAYOUT.replace('85','nan'), LAYOUT.replace('question | q1','figure | q1'), LAYOUT+'surprise'):
            with self.assertRaises(ValueError): parse_layout_markdown(changed)

    def test_changed_reading_drops_stale_figure_cache(self):
        self.approve()
        path=self.root/'mcp/state.json'; state=job.load_json(path)
        state['figures']['1']={'path':'old-figure-path','approval':'old','sha256':'old'}
        job.save_json(path,state)
        self.call('submit_reading',role='a',markdown=MD+'\n')
        # Same parsed content is idempotent; stale cache is ignored at composition.
        self.assertEqual(self.call('compose',layout_markdown=LAYOUT)['status'],'accepted')

    def test_submission_accepts_only_its_assigned_markdown_path(self):
        path=self.root/'readers/page-0001/a/reading.md'; path.write_text(MD,encoding='utf-8')
        result=self.call('submit_reading',role='a',markdown_path=str(path))
        self.assertEqual(result['status'],'submitted',result)
        self.assertEqual(self.call('submit_reading',role='b',markdown_path=str(path))['status'],'failed')
        self.assertEqual(self.call('submit_reading',role='a',markdown=MD,markdown_path=str(path))['status'],'failed')

if __name__=='__main__': unittest.main()
