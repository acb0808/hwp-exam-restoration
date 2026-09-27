import sys,tempfile,unittest,zipfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from restoration_single import dispatch
import restoration_job as job

MD='''## left
### q1
1. $1+1$의 값은?
::: choices 2
① $1$ | ② $2$
:::
::: answer
번호: 1
정답: ② $2$
근거: $1+1=2$이므로 두 번째 선택지.
:::
## right
### q2
논술형 1. $1/2$를 분수로 쓰시오.
::: answer
번호: 논술형 1
정답: $\\frac{1}{2}$
근거: 분자가 1이고 분모가 2이다.
:::
'''

class AnswerSheetTests(unittest.TestCase):
    def setUp(self):
        import fitz
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'job';source=Path(self.tmp.name)/'source.pdf'
        with fitz.open() as doc:doc.new_page();doc.save(source)
        self.call('prepare',source=str(source),question_pages=[1],include_answers=True)
        self.call('assign',page=1,worker_id='producer',evidence='Actual fixture worker response')
    def call(self,action,**kwargs):return dispatch(action,{'job':str(self.root),**kwargs})
    def submit(self,md=MD):
        result=self.call('submit_reading',page=1,markdown=md)
        self.assertEqual(result['status'],'accepted',result)
        return result
    def test_answers_are_last_page_and_not_question_content(self):
        self.submit();pages=job.assemble(self.root)
        self.assertEqual(len(pages),2)
        self.assertEqual(pages[-1]['page_number'],2)
        self.assertEqual(pages[-1]['role'],'answer_sheet')
        self.assertEqual([r['label'] for r in pages[-1]['answer_rows']],['1','논술형 1'])
        self.assertNotIn('근거',str(pages[0]['questions']))
        result=self.call('build',output=str(self.root/'exam.hwpx'),title='test',school='test',year='2026',exam_title='test',native=False)
        self.assertEqual(result['status'],'built',result)
        with zipfile.ZipFile(result['output']) as z:xml=z.read('Contents/section0.xml').decode()
        self.assertIn('정답표',xml);self.assertIn('논술형 1',xml)
        self.assertIn('colCnt="8"',xml)
        self.assertIn('over',xml)
    def test_answer_only_revision_preserves_question_composition(self):
        self.submit();before=job.load_json(self.root/'mcp/state.json')['pages']['1']
        self.submit(MD.replace('정답: ② $2$','정답: ②'))
        after=job.load_json(self.root/'mcp/state.json')['pages']['1']
        self.assertEqual(before['composed'],after['composed'])
        self.assertEqual(before['reading'],after['reading'])
        self.assertNotEqual(before['answers'],after['answers'])
    def test_missing_or_duplicate_answer_is_reported(self):
        for md in (MD.replace('정답: ② $2$','정답: 확인 필요'),MD.replace('번호: 논술형 1','번호: 1')):
            result=self.call('submit_reading',page=1,markdown=md)
            self.assertEqual(result['status'],'failed',result)
    def test_incomplete_coverage_cannot_build(self):
        from restoration_answers import split_answers
        clean,_=split_answers(MD)
        result=self.call('submit_reading',page=1,markdown=clean)
        self.assertEqual(result['status'],'failed',result)
    def test_answer_record_tampering_is_rejected(self):
        self.submit();state=job.load_json(self.root/'mcp/state.json')
        path=job._artifact(self.root,state['pages']['1']['answers'])
        path.write_text('{}',encoding='utf8')
        with self.assertRaises(ValueError):job.assemble(self.root)
    def test_final_answer_page_requires_independent_review(self):
        import fitz
        from restoration_batch import review_pack,pages_digest
        self.submit();pdf=self.root/'fixture-output.pdf'
        with fitz.open() as doc:doc.new_page();doc.new_page();doc.save(pdf)
        native={'status':'rendered','job':str(self.root),'page_count':2,'pages_sha256':pages_digest(job.assemble(self.root)),
                'artifacts':{'pdf':{'path':str(pdf),'sha256':job.digest(pdf)}}}
        native['review_inputs']=review_pack(self.root,native,self.root/'review-pack')
        receipt=self.root/'fixture-native.json';job.save_json(receipt,native)
        state=job.load_json(self.root/'mcp/state.json');state['native_run']={'receipt':str(receipt),'returncode':0}
        job.save_json(self.root/'mcp/state.json',state)
        result=self.call('status');self.assertEqual(result['status'],'pending_review',result)
        tasks=result['review_tasks'];self.assertEqual([t['page'] for t in tasks],[1,2])
        self.assertEqual(tasks[1]['kind'],'answer_sheet')
        evidence='reviewer '+tasks[0]['source_image']+' '+tasks[0]['output_image']
        result=self.call('finish_review',reviewer_id='reviewer',reviews=[{'page':1,'status':'passed','issues':[]}],review_evidence=evidence)
        self.assertEqual(result['status'],'pending_review',result)
        self.assertEqual([t['page'] for t in result['review_tasks']],[2])
        evidence='reviewer '+tasks[1]['answer_reference']+' '+tasks[1]['output_image']
        result=self.call('finish_review',reviewer_id='reviewer',reviews=[{'page':2,'status':'passed','issues':[]}],review_evidence=evidence)
        self.assertEqual(result['status'],'complete',result)
        self.submit(MD.replace('정답: ② $2$','정답: ②'))
        self.assertEqual(self.call('status')['output_status'],'needs_rebuild')

if __name__=='__main__':unittest.main()
