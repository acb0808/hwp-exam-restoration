"""An author can resolve writing questions without reading the implementation."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from restoration_single import dispatch


class AuthorHelpTests(unittest.TestCase):
    def setUp(self):
        import fitz
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'job'
        source = Path(self.temp.name) / 'source.pdf'
        with fitz.open() as pdf:
            pdf.new_page()
            pdf.save(source)
        self.call('prepare', source=str(source), question_pages=[1], include_answers=False)
        self.call('assign', page=1, worker_id='test-producer', evidence='Test fixture assignment, not a visual review.')

    def call(self, action, **params):
        return dispatch(action, {'job': str(self.root), **params})

    def snapshot(self):
        return {str(p.relative_to(self.root)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.root.rglob('*') if p.is_file()}

    def test_disabled_answer_mode_is_explicit_in_task(self):
        text = (self.root / 'workers/page-0001/task.md').read_text(encoding='utf-8')
        self.assertIn('include_answers=false', text)
        self.assertIn('정답 계산·answer 블록·정답표를 작성하지 않습니다', text)
        self.assertNotIn('각 문항 끝에는 이어지는 정답 블록 형식을 적용', text)
        self.assertIn('hwp_submit_reading', text)
        self.assertIn('hwp_help', text)

    def test_help_returns_real_job_scope_and_exact_submit_arguments_without_writing(self):
        before = self.snapshot()
        result = self.call('help', page=1, topic='writing')
        self.assertEqual(result['status'], 'help', result)
        self.assertFalse(result['include_answers'])
        self.assertEqual(result['submit_call'], {'tool': 'hwp_submit_reading', 'arguments': {
            'job': str(self.root), 'page': 1, 'markdown_path': str(self.root / 'workers/page-0001/reading.md')}})
        self.assertEqual(before, self.snapshot())
        self.assertLess(len(json.dumps(result, ensure_ascii=False)), 6000)

    def test_equation_probe_uses_production_validation_preserves_input_and_state(self):
        before = self.snapshot()
        equations = [r'A=\{x | x\le100,\text{ 자연수}\}', r'a_1+\cdots+a_n',
                     r'\varnothing', r'x\mid y', r'a\quad b', r'\frac{1}']
        result = self.call('help', page=1, topic='equations', equations=equations)
        self.assertEqual(result['status'], 'failed', result)
        self.assertIn('hwp_submit_reading', result['message'])
        self.assertEqual(before, self.snapshot())
        text = '## left\n'+'\n'.join(f'### q{i}\n{i}. ${s}$' for i,s in enumerate(equations,1))
        result = self.call('submit_reading', page=1, markdown=text)
        self.assertEqual(result['status'], 'failed', result)
        self.assertEqual([e['question_id'] for e in result['errors']], ['q4','q5','q6'])
        self.assertEqual(result['errors'][0]['engine_diagnostics'][0]['token'], r'\mid')
        self.assertNotIn('kind:text', result['errors'][1]['hint'])

    def test_help_reuses_reported_errors_and_directs_back_to_submission(self):
        submitted = self.call('submit_reading', page=1, markdown='## left\n### q1\n1. $x\\mid y$')
        before = self.snapshot()
        from unittest.mock import patch
        with patch('restoration_compiler._studio_equation', side_effect=AssertionError('no duplicate validation')):
            result = self.call('help', page=1, topic='equations')
        self.assertEqual(result['reported_errors'], submitted['errors'])
        self.assertEqual(result['submit_call']['tool'], 'hwp_submit_reading')
        self.assertEqual(before, self.snapshot())

    def test_all_documented_math_examples_are_supported_by_real_converter(self):
        help_result = self.call('help', page=1, topic='equations')
        self.assertEqual(help_result['status'], 'help', help_result)
        examples = [x['latex'] for x in help_result['examples']]
        text = '## left\n'+'\n'.join(f'### q{i}\n{i}. ${s}$' for i,s in enumerate(examples,1))
        submitted = self.call('submit_reading', page=1, markdown=text)
        self.assertEqual(submitted['status'], 'accepted', submitted)

    def test_help_rejects_invalid_topic_oversized_input_and_unassigned_page_without_writes(self):
        before = self.snapshot()
        for params in ({'page': 2, 'topic': 'writing'}, {'page': 1, 'topic': 'internals'},
                       {'page': 1, 'topic': 'equations', 'equations': ['x'] * 13},
                       {'page': 1, 'topic': 'equations', 'equations': ['x' * 2001]},
                       {'page': 1, 'topic': 'writing', 'equations': ['x']}):
            with self.subTest(params=params):
                self.assertEqual(self.call('help', **params)['status'], 'failed')
                self.assertEqual(before, self.snapshot())

    def test_submission_includes_exact_engine_token_and_markdown_repair(self):
        result = self.call('submit_reading', page=1, markdown='## left\n### q1\n1. $x\\mid y$는?')
        self.assertEqual(result['status'], 'failed', result)
        error = result['errors'][0]
        self.assertEqual(error['question_id'], 'q1')
        self.assertEqual(error['line'], 3)
        self.assertEqual(error['engine_diagnostics'][0]['token'], r'\mid')
        self.assertIn('집합', error['hint'])

    def test_enabled_answer_mode_and_example_are_usable(self):
        self.root = self.root.parent / 'answer-job'
        self.call('prepare', source=str(self.root.parent / 'source.pdf'), question_pages=[1], include_answers=True)
        self.call('assign', page=1, worker_id='test-answer-worker', evidence='Test fixture assignment.')
        task = (self.root / 'workers/page-0001/task.md').read_text(encoding='utf-8')
        self.assertIn('include_answers=true', task)
        self.assertIn('마지막 정답표용 답 블록', task)
        before = self.snapshot()
        result = self.call('help', page=1, topic='writing')
        self.assertTrue(result['include_answers'])
        self.assertEqual(before, self.snapshot())
        submitted = self.call('submit_reading', page=1, markdown=result['example']+'\n'+result['answer_example'])
        self.assertEqual(submitted['status'], 'accepted', submitted)
        # Acceptance is syntactic; fixture answer is not an actual visual/answer review.

    def test_figure_help_does_not_render_or_change_any_evidence(self):
        before = self.snapshot()
        result = self.call('help', page=1, topic='figures')
        self.assertEqual(result['status'], 'help')
        self.assertEqual(result['visual_status'], 'not_verified')
        self.assertTrue(Path(result['reference']).is_file())
        self.assertIn('렌더 성공은 검수 통과가 아닙니다', ' '.join(result['rules']))
        self.assertEqual(before, self.snapshot())


if __name__ == '__main__':
    unittest.main()
