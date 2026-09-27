import sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import anyio
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import restoration_mcp as transport

class ReviewEvidenceFileTests(unittest.TestCase):
    def test_exact_reviewer_file_is_forwarded(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'review.md';path.write_text('reviewer actual source output passed',encoding='utf-8')
            with patch.object(transport,'dispatch',return_value={'status':'complete'}) as dispatch:
                result=anyio.run(lambda:transport.hwp_finish_review(folder,'reviewer',
                    [transport.PageReview(page=1,status='passed',issues=[])],review_evidence_path=str(path)))
            self.assertFalse(result.isError)
            self.assertEqual(dispatch.call_args.args[1]['review_evidence'],path.read_text(encoding='utf-8'))

    def test_outside_job_or_mixed_evidence_does_not_dispatch(self):
        with tempfile.TemporaryDirectory() as folder:
            job=Path(folder)/'job';job.mkdir();path=Path(folder)/'outside.md';path.write_text('no',encoding='utf-8')
            for extra in ({'review_evidence_path':str(path)}, {'review_evidence':'text','review_evidence_path':str(path)}):
                with patch.object(transport,'dispatch') as dispatch:
                    result=anyio.run(lambda:transport.hwp_finish_review(str(job),'reviewer',
                        [transport.PageReview(page=1,status='passed',issues=[])],**extra))
                self.assertTrue(result.isError);dispatch.assert_not_called()

if __name__=='__main__':unittest.main()
