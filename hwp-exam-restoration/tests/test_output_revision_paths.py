import sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,MagicMock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import restoration_single as single
import restoration_job as job


class OutputRevisionTests(unittest.TestCase):
    def setUp(self):
        import fitz
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'job';source=Path(self.tmp.name)/'source.pdf'
        with fitz.open() as doc:doc.new_page();doc.save(source)
        self.call('prepare',source=str(source),question_pages=[1])
        self.call('assign',page=1,worker_id='actual-owner',evidence='actual-owner test spawn')
        self.assertEqual(self.call('submit_reading',page=1,markdown='## left\n### q1\n1. Test.\n')['status'],'accepted')

    def call(self,action,**p):return single.dispatch(action,{'job':str(self.root),**p})

    def build(self,output,native=False):
        return self.call('build',output=str(output),title='Test',school='Test',year='2026',exam_title='Test',native=native)

    def test_rebuild_automatically_chooses_next_unused_revision(self):
        output=self.root/'exam.hwpx'
        first=self.build(output);self.assertEqual(first['status'],'built',first)
        before=output.read_bytes()
        sidecar=self.root/'exam_v2.build.json';sidecar.write_bytes(b'preserve sidecar')
        second=self.build(output)
        self.assertEqual(second['status'],'built',second)
        self.assertEqual(Path(second['output']),self.root/'exam_v3.hwpx')
        self.assertEqual(output.read_bytes(),before)
        self.assertEqual(sidecar.read_bytes(),b'preserve sidecar')

    def test_related_deliverables_are_preserved(self):
        for suffix in ('.hwp','.pdf'):
            with self.subTest(suffix=suffix):
                base=self.root/('exam-'+suffix[1:]+'.hwpx')
                existing=base.with_suffix(suffix);existing.write_bytes(b'keep')
                result=self.build(base)
                self.assertEqual(result['status'],'built',result)
                self.assertEqual(Path(result['output']),base.with_name(base.stem+'_v2.hwpx'))
                self.assertEqual(existing.read_bytes(),b'keep')

    def test_native_receives_automatically_selected_build_receipt(self):
        output=self.root/'exam.hwpx';output.write_bytes(b'existing final')
        proc=MagicMock();proc.pid=987656;proc.wait.return_value=0
        with patch.object(single.subprocess,'Popen',return_value=proc) as launch, \
             patch.object(single,'collect_native',return_value={'status':'pending_review'}):
            result=self.build(output,native=True)
        single._NATIVE_PROCESSES.pop(proc.pid,None)
        self.assertEqual(result['status'],'pending_review',result)
        selected=self.root/'exam_v2.hwpx'
        self.assertEqual(result['build_output'],str(selected))
        self.assertIn(str(selected.with_suffix('.build.json')),launch.call_args.args[0])
        self.assertEqual(job.load_json(self.root/'mcp/state.json')['native_run']['build_output'],str(selected))
        self.assertEqual(output.read_bytes(),b'existing final')
