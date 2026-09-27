"""Unchanged answer proofs/images can be reused without accepting changed math."""
import copy,unittest,sys,tempfile
from pathlib import Path
import fitz

import test_answer_sheet as fixture
import restoration_job as job
import restoration_batch as batch
import restoration_single as single


class AnswerReviewDeltaTests(unittest.TestCase):
    def test_choice_rows_reuse_answer_proof_but_not_question_visual_review(self):
        first=self.native('initial-rows')
        evidence='reviewer '+' '.join((t.get('source_image') or t.get('answer_reference'))+' '+t['output_image'] for t in first['review_tasks'])
        self.case.call('finish_review',reviewer_id='reviewer',
            reviews=[{'page':t['page'],'status':'passed','issues':[]} for t in first['review_tasks']],review_evidence=evidence)
        self.case.submit(fixture.MD.replace('::: choices 2\n① $1$ | ② $2$', '::: choices 1\n① $1$\n② $2$'))
        second=self.native('changed-rows')
        self.assertEqual(second['reused_review_pages'],[2])
        self.assertEqual([t['page'] for t in second['review_tasks']],[1])

    def setUp(self):
        self.case=fixture.AnswerSheetTests();self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.root=self.case.root
        self.case.submit()
        self.pdf=self.root/'same-print.pdf'
        with fitz.open() as doc:
            doc.new_page();doc.new_page();doc.save(self.pdf)

    def native(self,version):
        pages=job.assemble(self.root)
        value={'status':'rendered','job':str(self.root),'page_count':2,
               'pages_sha256':batch.pages_digest(pages),
               'artifacts':{'pdf':{'path':str(self.pdf),'sha256':job.digest(self.pdf)}}}
        value['review_inputs']=batch.review_pack(self.root,value,self.root/('review-'+version))
        receipt=self.root/('native-'+version+'.json');job.save_json(receipt,value)
        state=job.load_json(self.root/'mcp/state.json')
        state['native_run']={'receipt':str(receipt),'returncode':0}
        state.pop('output_stale',None);job.save_json(self.root/'mcp/state.json',state)
        return self.case.call('status')

    def test_review_only_metadata_does_not_change_existing_native_receipt(self):
        current=job.assemble(self.root)
        old_shape=copy.deepcopy(current)
        old_shape[-1].pop('answer_review_questions')
        self.assertEqual(batch.pages_digest(old_shape),batch.pages_digest(current))
        self.assertEqual(old_shape[-1],batch.native_page(current[-1]))

    def test_review_cache_is_not_repeated_in_tool_responses(self):
        first=self.native('compact')
        tasks=first['review_tasks']
        evidence='reviewer '+' '.join((t.get('source_image') or t.get('answer_reference'))+' '+t['output_image'] for t in tasks)
        rows=[{'page':t['page'],'status':'passed','issues':[]} for t in tasks]
        result=self.case.call('finish_review',reviewer_id='reviewer',reviews=rows,review_evidence=evidence)
        self.assertEqual(result['status'],'complete')
        self.assertNotIn('answer_review_questions',str(result['reviews']))
        self.assertNotIn('answer_review_questions',str(self.case.call('status')['reviews']))
        stored=job.load_json(self.root/'mcp/state.json')
        self.assertIn('answer_review_questions',stored['reviews']['2'])

    def test_answer_only_change_rechecks_one_question_and_reuses_identical_print(self):
        first=self.native('first')
        self.assertEqual(first['status'],'pending_review',first)
        tasks=first['review_tasks']
        self.assertEqual(first['review_tasks'][-1]['kind'],'answer_sheet')
        evidence='reviewer '+' '.join(
            (t.get('source_image') or t.get('answer_reference'))+' '+t['output_image'] for t in tasks)
        rows=[{'page':t['page'],'status':'passed','issues':[]} for t in tasks]
        self.assertEqual(self.case.call('finish_review',reviewer_id='reviewer',reviews=rows,
                                        review_evidence=evidence)['status'],'complete')
        changed=fixture.MD.replace('근거: $1+1=2$이므로 두 번째 선택지.',
                                   '근거: $1+1=2$이며 두 번째 선택지와 일치한다.')
        self.case.submit(changed)
        second=self.native('second')
        self.assertEqual(second['status'],'pending_review',second)
        self.assertEqual(second['reused_review_pages'],[1])
        self.assertEqual([t['page'] for t in second['review_tasks']],[2])
        scope=second['review_tasks'][0]['answer_review_scope']
        self.assertEqual(scope['mode'],'changed_questions')
        self.assertEqual(scope['question_keys'],['1/q1'])
        self.assertNotIn('question_ids',scope)
        self.assertTrue(scope['output_image_unchanged'])
        self.assertIn('1+1',scope['changed_questions'][0]['question'])
        self.assertEqual(scope['changed_questions'][0]['answer'],'② $2$')
        self.assertEqual(scope['changed_questions'][0]['source_image'],str(single.shared._source(self.root,1)))
        self.assertNotIn('q2',str(scope['changed_questions']))
        task=second['review_tasks'][0]
        evidence='reviewer '+task['answer_reference']+' '+task['output_image']+' q1 independently recalculated'
        self.assertEqual(self.case.call('finish_review',reviewer_id='reviewer',
            reviews=[{'page':2,'status':'passed','issues':[]}],review_evidence=evidence)['status'],'complete')

    def test_changed_candidate_is_never_declared_unchanged(self):
        first=self.native('initial')
        tasks=first['review_tasks']
        evidence='reviewer '+' '.join((t.get('source_image') or t.get('answer_reference'))+' '+t['output_image'] for t in tasks)
        rows=[{'page':t['page'],'status':'passed','issues':[]} for t in tasks]
        self.case.call('finish_review',reviewer_id='reviewer',reviews=rows,review_evidence=evidence)
        self.case.submit(fixture.MD.replace('정답: ② $2$','정답: ② $3$'))
        second=self.native('candidate-change')
        self.assertEqual([t['page'] for t in second['review_tasks']],[2])
        scope=second['review_tasks'][0]['answer_review_scope']
        self.assertEqual(scope['question_keys'],['1/q1'])
        self.assertNotIn('question_ids',scope)
        self.assertEqual(scope['changed_questions'][0]['answer'],'② $3$')
        self.assertFalse(scope['output_image_unchanged'],
                         'A stale-looking print must be checked against the changed candidate')


if __name__=='__main__':unittest.main()
