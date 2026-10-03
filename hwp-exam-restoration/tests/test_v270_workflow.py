"""Behavioral regressions for diagnostics and independent per-item batches."""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import restoration_job as job
import restoration_single as single

MD = '## left\n### q1\n1. $1+1$의 값은?\n'


class Workflow270Tests(unittest.TestCase):
    def setUp(self):
        import fitz
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'job'
        source = Path(self.temp.name) / 'source.pdf'
        with fitz.open() as doc:
            doc.new_page()
            doc.new_page()
            doc.save(source)
        self.assertEqual(self.call('prepare', source=str(source), question_pages=[1, 2])['status'], 'prepared')

    def call(self, action, **params):
        return single.dispatch(action, {'job': str(self.root), **params})

    def assignments(self):
        return [{'page': n, 'worker_id': f'producer-{n}', 'evidence': f'actual fixture worker producer-{n}'} for n in (1, 2)]

    def test_syntax_error_survives_status_and_correction_clears_it(self):
        self.call('assign', **self.assignments()[0])
        result = self.call('submit_reading', page=1, markdown=MD.replace('$1+1$', '$1+1'))
        self.assertEqual(result['status'], 'failed')
        self.assertTrue(result.get('errors'), result)
        self.assertEqual(self.call('status', page=1)['errors'], result['errors'])
        self.assertEqual(result['errors'][0]['question_id'], 'q1')
        self.assertEqual(result['errors'][0]['source_line'], 3)
        self.assertEqual(self.call('submit_reading', page=1, markdown=MD)['status'], 'accepted')
        self.assertEqual(self.call('status', page=1)['errors'], [])

    def test_missing_input_is_persisted_and_blocks_old_acceptance(self):
        self.call('assign', **self.assignments()[0])
        self.assertEqual(self.call('submit_reading', page=1, markdown=MD)['status'], 'accepted')
        result = self.call('submit_reading', page=1, markdown_path=str(self.root / 'workers/page-0001/reading.md'))
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result.get('error_kind'), 'missing_file')
        status = self.call('status', page=1)
        self.assertEqual(status['status'], 'needs_correction')
        self.assertEqual(status['errors'], result['errors'])
        self.assertEqual(self.call('build', output=str(self.root/'out.hwpx'), title='', school='', year='', exam_title='', native=False)['status'], 'failed')

    def test_assignment_batch_and_identical_retry_preserve_actual_ids(self):
        result = self.call('assign', items=self.assignments())
        self.assertEqual(result['status'], 'batch_processed', result)
        metrics = job.load_json(self.root/'mcp/metrics.json')
        self.assertEqual(metrics['by_action']['assign']['statuses'].get('batch_processed'), 1)
        self.assertEqual(metrics['mcp_requests'], 2)
        self.assertEqual(metrics['engine_operations'], 3)
        before = job._manifest(self.root)['assignments']
        retry = self.call('assign', items=self.assignments())
        self.assertEqual(retry['status'], 'batch_processed', retry)
        self.assertTrue(all(r.get('unchanged') for r in retry['results']))
        self.assertEqual(job._manifest(self.root)['assignments'], before)
        conflict = self.call('assign', page=1, worker_id='replacement', evidence='new worker')
        self.assertEqual(conflict['status'], 'failed')
        self.assertEqual(job._manifest(self.root)['assignments'], before)

    def test_batch_partial_failure_keeps_good_sibling_and_retries_only_bad(self):
        self.call('assign', items=self.assignments())
        result = self.call('submit_reading', items=[{'page': 1, 'markdown': MD}, {'page': 2, 'markdown': 'broken'}])
        self.assertEqual(result['status'], 'partial_failure', result)
        self.assertEqual(result['failed_pages'], [2])
        self.assertEqual(self.call('status', page=1)['status'], 'accepted')
        before = job.load_json(self.root/'mcp/state.json')['pages']['1']['reading']
        corrected = self.call('submit_reading', items=[{'page': 2, 'markdown': MD}])
        self.assertEqual(corrected['status'], 'batch_processed', corrected)
        self.assertEqual(job.load_json(self.root/'mcp/state.json')['pages']['1']['reading'], before)
        self.assertEqual(self.call('status', page=2)['status'], 'accepted')
        self.assertNotEqual(self.call('status')['status'], 'complete')

    def test_identical_assignment_retry_preserves_windows_evidence_bytes(self):
        item = dict(self.assignments()[0], evidence='actual spawn response\r\nworker_id: producer-1')
        self.assertEqual(self.call('assign', items=[item])['status'], 'batch_processed')
        retry = self.call('assign', items=[item])
        self.assertEqual(retry['status'], 'batch_processed', retry)
        self.assertTrue(retry['results'][0]['unchanged'])
        assignment = job._manifest(self.root)['assignments'][0]
        self.assertEqual(job._artifact(self.root, assignment['evidence']).read_bytes(), item['evidence'].encode('utf8'))

    def test_batch_preflight_rejects_duplicate_mixed_and_unknown_fields_without_mutation(self):
        first = self.assignments()[0]
        before = job._manifest(self.root)['assignments']  # prepared page slots only
        for params in ({'items': [first, first]}, {'items': [first], 'page': 1},
                       {'items': [dict(first, shell='forbidden')]}, {'items': []}):
            with self.subTest(params=params):
                result = self.call('assign', **params)
                self.assertEqual(result['status'], 'failed', result)
                self.assertEqual(job._manifest(self.root)['assignments'], before)

    def test_environment_failure_does_not_request_math_rewrite(self):
        with patch.object(single, 'perform', side_effect=PermissionError('denied')):
            result = self.call('status')
        self.assertEqual(result.get('error_kind'), 'environment')
        self.assertNotIn('Correct only', result['next_action'])

    def test_returned_draft_failure_is_persisted_with_environment_guidance(self):
        self.call('assign', **self.assignments()[0])
        with patch('restoration_draft._expand', side_effect=PermissionError('draft access denied')):
            result = self.call('submit_reading', page=1, markdown=MD)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result.get('error_kind'), 'environment')
        self.assertNotIn('Correct only', result['next_action'])
        status = self.call('status', page=1)
        self.assertEqual(status['status'], 'needs_correction')
        self.assertEqual(status['errors'], result['errors'])
        self.assertEqual(self.call('submit_reading', page=1, markdown=MD)['status'], 'accepted')

    def test_body_equation_error_has_original_line_after_answer_block(self):
        self.call('assign', **self.assignments()[0])
        text = MD + '::: answer\n번호: 1\n정답: $2$\n근거: $1+1=2$\n:::\n' + r'추가 조건 $\unsupported{x}$' + '\n'
        result = self.call('submit_reading', page=1, markdown=text)
        self.assertEqual(result['status'], 'failed', result)
        error = next(row for row in result['errors'] if row.get('latex') == r'\unsupported{x}')
        self.assertEqual(error.get('source_line'), 9)
        self.assertEqual(error.get('field'), 'content')
        self.assertEqual(self.call('status', page=1)['errors'], result['errors'])

    def test_public_transport_accepts_batch_alongside_optional_help(self):
        import restoration_mcp
        inventory = asyncio.run(restoration_mcp.mcp.list_tools())
        self.assertEqual(len(inventory), 10)
        schema = next(t.inputSchema for t in inventory if t.name == 'hwp_assign')
        self.assertIn('items', schema['properties'])
        result = asyncio.run(restoration_mcp.hwp_assign(str(self.root), items=self.assignments()))
        self.assertEqual(result.structuredContent['status'], 'batch_processed')
        result = asyncio.run(restoration_mcp.hwp_submit_reading(str(self.root), items=[{'page': 1, 'markdown': MD}, {'page': 2, 'markdown': 'broken'}]))
        self.assertTrue(result.isError)
        self.assertEqual(result.structuredContent['failed_pages'], [2])


if __name__ == '__main__':
    unittest.main()
