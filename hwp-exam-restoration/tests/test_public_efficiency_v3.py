"""The public worker packet routes drafting and preparation without code discovery."""
import contextlib, io, json, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import restoration_job as job
import test_efficiency as fixtures
import restore


class PublicV3Tests(unittest.TestCase):
    def test_packet_provides_compact_draft_and_preserves_existing_work(self):
        from restoration_tools import worker_input
        with tempfile.TemporaryDirectory() as d:
            root,manifest,assignment=fixtures.EfficiencyTests().prepared(Path(d))
            packet=job.load_json(assignment['worker_input'])
            self.assertIn('draft_path',packet)
            draft=Path(packet['draft_path']);compiled=Path(packet['compiled_result_path'])
            self.assertEqual(job.load_json(draft)['schema'],'restoration-draft/1')
            self.assertFalse(compiled.exists())
            self.assertIn('source-views',packet['source_views_command'])
            self.assertIn('compile-draft',packet['compile_command'])
            self.assertTrue(Path(packet['environment_file']).is_file())
            draft.write_text('{"working":"do not overwrite"}',encoding='utf8')
            worker_input(root,manifest,assignment)
            self.assertEqual(job.load_json(draft),{'working':'do not overwrite'})

    def test_cli_compiles_draft_and_never_accepts_it(self):
        with tempfile.TemporaryDirectory() as d:
            root,manifest,assignment=fixtures.EfficiencyTests().prepared(Path(d))
            page=fixtures.EfficiencyTests().page(manifest,assignment)
            draft={'schema':'restoration-draft/1','regions':page['regions'],'questions':page['questions'],'issues':[]}
            path=root/'draft-test.json';job.save_json(path,draft);output=root/'compiled-test.json'
            captured=io.StringIO()
            with contextlib.redirect_stdout(captured):code=restore.main(['compile-draft',str(root),'1',str(path),str(output)])
            self.assertEqual(code,0)
            self.assertFalse(json.loads(captured.getvalue())['accepted'])
            self.assertEqual(job.load_json(output),page)
            self.assertEqual(job.load_json(root/'manifest.json')['accepted'],[])


if __name__=='__main__':unittest.main()
