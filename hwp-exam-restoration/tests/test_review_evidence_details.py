import unittest
import test_single_review_workflow as fixture
import restoration_job as job

class ReviewEvidenceDetailsTests(unittest.TestCase):
    def test_missing_evidence_paths_are_reported_without_recording_verdicts(self):
        case=fixture.SingleReviewTests();case.setUp();self.addCleanup(case.doCleanups)
        case.fixture_native()
        task=case.call('status')['review_tasks'][0]
        rows=[{'page':1,'status':'passed','issues':[]}]
        result=case.call('finish_review',reviewer_id='reviewer',reviews=rows,review_evidence='reviewer '+task['source_image'])
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result.get('missing_evidence'),[task['output_image']])
        self.assertIn('same reviewer',result['next_action'])
        self.assertFalse(job.load_json(case.root/'mcp/state.json')['reviews'])
        result=case.call('finish_review',reviewer_id='reviewer',reviews=rows,
                         review_evidence='reviewer '+task['source_image']+' '+task['output_image'])
        self.assertEqual(result['status'],'complete')

if __name__=='__main__':unittest.main()
