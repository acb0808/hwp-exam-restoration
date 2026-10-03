"""A chat rewind must not confuse existing worker assignments with a fresh job."""
import subprocess
import sys
import unittest
from pathlib import Path

import test_single_review_workflow as workflow
import restoration_job as job


class RewindRestartTests(unittest.TestCase):
    def setUp(self):
        self.case=workflow.SingleReviewTests();self.case.setUp()
        self.addCleanup(self.case.doCleanups)

    def test_reprepare_does_not_spawn_duplicate_workers_and_preserves_old_job(self):
        root=self.case.root;source=Path(self.case.temp.name)/'source.pdf'
        before=(root/'manifest.json').read_bytes()
        (root.parent/(root.name+'_run2')).mkdir()
        result=self.case.call('prepare',source=str(source),question_pages=[1])
        self.assertEqual(result['status'],'already_assigned')
        self.assertEqual(result['spawn_requests'],[])
        self.assertEqual(result['assigned_workers'],[{'page':1,'worker_id':'producer-1'}])
        fresh=Path(result['restart_job'])
        self.assertEqual(fresh,root.parent/(root.name+'_run3'))
        self.assertFalse(fresh.exists())
        self.assertEqual((root/'manifest.json').read_bytes(),before)
        next_run=workflow.dispatch('prepare',{'job':str(fresh),'source':str(source),'question_pages':[1]})
        self.assertEqual(next_run['status'],'prepared')
        self.assertEqual(len(next_run['spawn_requests']),1)
        self.assertTrue(all(a.get('pending') for a in job._manifest(fresh)['assignments']))  # slots only, no actual worker
        self.assertEqual((root/'manifest.json').read_bytes(),before)

    def test_run_stopped_after_its_last_page_is_rebuilt_not_redone(self):
        root=self.case.root;source=Path(self.case.temp.name)/'source.pdf'
        first=self.case.call('prepare',source=str(source),question_pages=[1])
        self.assertEqual(first['page_status'],{1:'in_progress'});self.assertIn('restart_job',first)
        self.case.compose()  # the producer finished its page; then the run was stopped before or during the build
        again=self.case.call('prepare',source=str(source),question_pages=[1])
        self.assertEqual((again['status'],again['page_status'],again.get('continue_with')),('already_assigned',{1:'accepted'},'hwp_build'))
        self.assertNotIn('restart_job',again);self.assertEqual(again['spawn_requests'],[])
        built=self.case.call('build',output=str(root/'restarted.hwpx'),title='t',school='s',year='2026',exam_title='e',native=False)
        self.assertEqual(built['status'],'built',built)

    def test_unassigned_prepare_stays_idempotent_for_page_selection(self):
        import fitz
        root=Path(self.case.temp.name)/'fresh';source=Path(self.case.temp.name)/'source.pdf'
        first=workflow.dispatch('prepare',{'job':str(root),'source':str(source)})
        self.assertEqual(first['status'],'needs_selection')
        image=root/'pages/page-0001.png';stamp=image.stat().st_mtime_ns
        second=workflow.dispatch('prepare',{'job':str(root),'source':str(source),'question_pages':[1]})
        self.assertEqual(second['status'],'prepared')
        self.assertEqual(image.stat().st_mtime_ns,stamp)

    def test_help_exits_without_waiting_for_stdio(self):
        script=Path(__file__).resolve().parents[1]/'scripts/restoration_mcp.py'
        try:
            result=subprocess.run([sys.executable,'-B',str(script),'--help'],
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                timeout=8,text=True,encoding='utf-8')
        except subprocess.TimeoutExpired:
            self.fail('--help started a waiting MCP server')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('usage:',result.stdout)


if __name__=='__main__':unittest.main()
