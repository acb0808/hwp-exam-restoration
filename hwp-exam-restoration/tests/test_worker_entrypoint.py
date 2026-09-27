"""One normal authoring entrypoint, without breaking legacy packets or resume."""
import json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import restoration_job as job
import restoration_handoff as handoff
import restoration_tools as helpers
import restore
import test_efficiency

class WorkerEntrypointTests(unittest.TestCase):
    def test_default_cli_assignment_only_exposes_ready_handoffs(self):
        value={'worker_input':'internal.json','handoffs':[{'page':1,'worker_id':'worker1',
            'worker_brief':'internal-task.json','worker_instructions':'task.md','message':'Read task.md'}]}
        result=restore.summarize('assign',value)
        self.assertNotIn('worker_input',result)
        self.assertNotIn('worker_brief',result['handoffs'][0])
        self.assertEqual(result['handoffs'][0]['message'],'Read task.md')
        self.assertEqual(value['worker_input'],'internal.json')

    def test_new_assignment_exposes_readable_task_without_legacy_authoring_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,manifest,assignment=test_efficiency.EfficiencyTests().prepared(Path(tmp))
            folder=root/'workers/page-0001'
            packet=job.load_json(assignment['worker_input'])
            self.assertFalse(Path(assignment['worker_input']).is_relative_to(folder))
            self.assertFalse((folder/'input.json').exists())
            self.assertFalse((folder/'result.json').exists())
            entry=folder/'task.md'
            self.assertTrue(entry.is_file())
            text=entry.read_text(encoding='utf-8')
            self.assertIn('compile-draft',text)
            self.assertIn(str(folder/'draft.json'),text)
            self.assertNotIn('compact-draft.md',text)
            self.assertNotIn('ocr-draft.json',text)
            self.assertNotIn('skeleton',text)
            brief=job.load_json(packet['worker_brief'])
            self.assertEqual(brief['instructions_path'],str(entry.resolve()))
            self.assertEqual(len(brief['navigation_views']),2)
            self.assertEqual(brief['navigation_views'][1]['pixel_box'],packet['navigation_crops'][1]['pixel_box'])
            ready=handoff.assignment_handoffs(assignment)[0]
            self.assertIn(str(entry.resolve()),ready['message'])
            self.assertNotIn(packet['worker_brief'],ready['message'])
            self.assertEqual(job._manifest(root)['accepted'],[])

    def test_regenerating_handoff_preserves_existing_work_and_legacy_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,manifest,assignment=test_efficiency.EfficiencyTests().prepared(Path(tmp))
            folder=root/'workers/page-0001'
            files=[folder/n for n in ('draft.json','result.json','input.json')]
            for p in files:p.write_text('existing work: '+p.name,encoding='utf-8')
            before={p:p.read_bytes() for p in files}
            helpers.worker_input(root,manifest,assignment)
            self.assertEqual(before,{p:p.read_bytes() for p in files})

if __name__=='__main__':unittest.main()
