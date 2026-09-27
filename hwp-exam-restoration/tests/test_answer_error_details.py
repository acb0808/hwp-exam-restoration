"""Answer formulas must get the same actionable error boundary as questions."""
import unittest
import test_answer_sheet as fixture
import restoration_job as job


class AnswerErrorDetailsTests(unittest.TestCase):
    def setUp(self):
        self.case=fixture.AnswerSheetTests();self.case.setUp()
        self.addCleanup(self.case.doCleanups)

    def test_unprinted_reason_does_not_require_hwp_equation_conversion(self):
        reason=r'$1, 2, \dots, 8$에서 $1+1=2 \implies 2$이며 $\lim_{x\to 0} \, f(x)$를 검산한다.'
        md=fixture.MD.replace('$1+1=2$이므로 두 번째 선택지.',reason)
        result=self.case.call('submit_reading',page=1,markdown=md)
        self.assertEqual(result['status'],'accepted',result)
        rows=job.load_json(self.case.root/'mcp/state.json')['pages']['1']['answers']
        stored=job.load_json(job._artifact(self.case.root,rows))
        self.assertEqual(stored['rows'][0]['reason'],reason)

    def test_bad_printed_answer_is_rejected_even_with_unprinted_reason_command(self):
        md=fixture.MD.replace('정답: ② $2$',r'정답: ② $\unsupported{2}$')
        md=md.replace('$1+1=2$',r'$1\quad +1=2$')
        result=self.case.call('submit_reading',page=1,markdown=md)
        self.assertEqual(result['status'],'failed')
        self.assertIn('errors',result,'A generic LaTeX error forces the producer to guess the failing field')
        self.assertEqual(len(result['errors']),1)
        self.assertEqual({e['field'] for e in result['errors']},{'answer'})
        for error in result['errors']:
            self.assertEqual(error['question_id'],'q1')
            self.assertIn(error['field'],error['location'])
            self.assertTrue(error['latex'])
            self.assertTrue(error['engine_diagnostics'])
            self.assertIn('hint',error)
        state=job.load_json(self.case.root/'mcp/state.json')
        self.assertFalse(state['pages']['1'].get('answers'))
        self.assertEqual(state['pages']['1']['errors'],result['errors'])
        self.case.submit()  # Corrected original accepted on the next submission.

    def test_bad_revision_invalidates_old_output_but_keeps_previous_answers(self):
        self.case.submit()
        state=job.load_json(self.case.root/'mcp/state.json')
        before=state['pages']['1']['answers']
        state['native_run']={'receipt':'synthetic-existing-output','returncode':0}
        job.save_json(self.case.root/'mcp/state.json',state)
        result=self.case.call('submit_reading',page=1,markdown=fixture.MD.replace('정답: ② $2$',r'정답: ② $\badformula$'))
        self.assertEqual(result['status'],'failed')
        self.assertIn('errors',result)
        state=job.load_json(self.case.root/'mcp/state.json')
        self.assertEqual(state['pages']['1']['answers'],before)
        self.assertTrue(state['output_stale'])

    def test_unclosed_or_empty_math_keeps_question_and_field_and_other_errors(self):
        md=fixture.MD.replace('정답: ② $2$', '정답: ② $2')
        md=md.replace('$1+1=2$', '$$')
        md=md.replace(r'정답: $\frac{1}{2}$',r'정답: $\unsupported{2}$')
        result=self.case.call('submit_reading',page=1,markdown=md)
        self.assertEqual(result['status'],'failed')
        self.assertEqual(len(result.get('errors',[])),3,result)
        self.assertEqual([(e['question_id'],e['field']) for e in result['errors']],
                         [('q1','answer'),('q1','reason'),('q2','answer')])
        self.assertEqual(result['errors'][0]['text'],'② $2')


if __name__=='__main__':unittest.main()
