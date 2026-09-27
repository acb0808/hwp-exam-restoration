"""Local operation counts are not model usage or a claim about quota savings."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
if importlib.util.find_spec('restoration_metrics'):
    import restoration_metrics as metrics
else:
    metrics = None


class OperationalMetricsTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(metrics, 'operational metrics module must exist')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'job'
        (self.root / 'mcp').mkdir(parents=True)
        self.write(self.root / 'manifest.json', {
            'schema': 'restoration-job/1', 'pages': [{'page': 1}, {'page': 2}],
            'question_pages': [1, 2], 'assignments': [],
        })

    @staticmethod
    def write(path, value):
        path.write_text(json.dumps(value), encoding='utf-8')

    def record(self, action='submit_reading', result=None, page=1, **kwargs):
        return metrics.record_event(self.root, action, {'page': page},
                                    result or {'status': 'accepted'}, 0.25, **kwargs)

    def test_records_counts_and_elapsed_without_claiming_host_usage(self):
        self.assertEqual(self.record()['status'], 'recorded')
        value = metrics.summary(self.root)
        self.assertEqual(value['status'], 'available')
        self.assertEqual(value['mcp_requests'], 1)
        self.assertEqual(value['engine_operations'], 1)
        self.assertEqual(value['by_action']['submit_reading']['operation_seconds'], 0.25)
        for field in ('model_calls', 'tokens', 'effort', 'quota'):
            self.assertIsNone(value[field])

    def test_redacts_source_body_evidence_paths_and_error_text(self):
        private = 'PRIVATE_STUDENT_SECRET'
        metrics.record_event(self.root, 'submit_reading', {
            'page': 1, 'markdown': private, 'markdown_path': private,
            'evidence': private, 'worker_id': private,
        }, {'status': 'failed', 'message': private, 'error_code': 'input_error',
            'errors': [{'code': 'markdown_syntax', 'question_id': private,
                        'field': private, 'message': private, 'latex': private},
                       {'code': private, 'error': private}]}, 0.3)
        raw = (self.root / 'mcp/metrics.json').read_text(encoding='utf-8')
        self.assertNotIn(private, raw)
        value = metrics.summary(self.root)
        self.assertEqual(value['operation_failures'], 1)
        self.assertEqual(value['error_codes']['input_error'], 1)
        self.assertEqual(value['error_codes']['markdown_syntax'], 1)
        self.assertEqual(value['error_codes']['unclassified_error'], 1)

    def test_workflow_failure_categories_are_counted_without_messages(self):
        for code in ('input', 'environment', 'missing_file'):
            self.record(result={'status': 'failed', 'error_kind': code,
                                'errors': [{'code': code, 'message': 'PRIVATE'}]})
        self.assertEqual(metrics.summary(self.root)['error_codes'],
                         {'input': 1, 'environment': 1, 'missing_file': 1})

    def test_resubmission_counts_exclude_unchanged_replays(self):
        self.record(result={'status': 'failed', 'error_code': 'input_error'})
        self.record()
        self.record(result={'status': 'accepted', 'unchanged': True})
        page = metrics.summary(self.root)['submissions_by_page']['1']
        self.assertEqual(page, {'attempts': 3, 'resubmissions': 1,
                               'unchanged_replays': 1, 'failures': 1})

    def test_batch_items_are_operations_but_wrapper_is_one_request(self):
        self.record(page=1, request_kind='batch_item')
        self.record(page=2, request_kind='batch_item', result={'status': 'failed'})
        self.record(action='batch_submit_reading', page=None, request_kind='batch',
                    result={'status': 'partial_failure', 'results': [{'status': 'failed'}]})
        value = metrics.summary(self.root)
        self.assertEqual(value['mcp_requests'], 1)
        self.assertEqual(value['engine_operations'], 2)
        self.assertEqual(value['request_failures'], 1)
        self.assertEqual(value['operation_failures'], 1)
        self.assertEqual(value['request_seconds'], 0.25)
        self.assertEqual(value['operation_seconds'], 0.5)
        self.assertEqual(len(value['submissions_by_page']), 2)

    def test_actual_batch_flags_count_approved_cache_and_failed_renders(self):
        path = self.root / 'mcp/batch.json'
        self.write(path, {'schema': 'restoration-figure-batch/1', 'job': str(self.root),
                         'page': 1, 'items': [
            {'id': 'a', 'status': 'pending_review', 'reused': False},
            {'id': 'b', 'status': 'pending_review', 'reused': True},
            {'id': 'c', 'status': 'failed', 'reused': False, 'error': 'PRIVATE'},
            {'id': 'd', 'status': 'pending_review', 'reused': False,
             'reuse_reason': 'explicit_render_receipt'},
        ]})
        self.record(action='render_figures', result={
            'status': 'failed', 'batch_path': str(path),
            'review_tasks': [{'id': 'a', 'reused': False}],
            'reused_review_ids': ['b', 'b', 'unknown'],
        })
        value = metrics.summary(self.root)
        self.assertEqual(value['figures'], {'rendered': 1, 'cached': 1,
                         'failed': 1, 'review_reused': 1, 'unobserved_batches': 0})
        self.assertNotIn('PRIVATE', (self.root / 'mcp/metrics.json').read_text())

    def test_batch_outside_job_or_wrong_page_is_not_inspected(self):
        path = Path(self.temp.name) / 'foreign.json'
        self.write(path, {'schema': 'restoration-figure-batch/1', 'job': str(self.root),
                         'page': 1, 'items': [{'id': 'a', 'status': 'pending_review', 'reused': False}]})
        self.record(action='render_figures', result={'status': 'failed', 'batch_path': str(path)})
        self.assertEqual(metrics.summary(self.root)['figures']['rendered'], 0)
        self.assertEqual(metrics.summary(self.root)['figures']['unobserved_batches'], 1)

    def test_malformed_batch_shape_cannot_interrupt_workflow_or_change_result(self):
        path = self.root / 'mcp/batch.json'
        self.write(path, ['invalid batch'])
        result = {'status': 'failed', 'batch_path': str(path), 'message': 'PRIVATE'}
        original = dict(result)
        recorded = self.record(action='render_figures', result=result)
        self.assertEqual(recorded['status'], 'recorded')
        self.assertEqual(result, original)
        self.assertEqual(metrics.summary(self.root)['figures']['unobserved_batches'], 1)

    def test_wrong_batch_page_does_not_count_foreign_render_results(self):
        path = self.root / 'mcp/batch.json'
        self.write(path, {'schema': 'restoration-figure-batch/1', 'job': str(self.root),
                         'page': 2, 'items': [{'id': 'a', 'status': 'pending_review', 'reused': False}]})
        self.record(action='render_figures', result={'status': 'accepted', 'batch_path': str(path)})
        figures = metrics.summary(self.root)['figures']
        self.assertEqual(figures['rendered'], 0)
        self.assertEqual(figures['unobserved_batches'], 1)

    def test_invalid_job_does_not_create_directories(self):
        missing = Path(self.temp.name) / 'missing'
        result = metrics.record_event(missing, 'status', {}, {'status': 'failed'}, 1)
        self.assertEqual(result['status'], 'unavailable')
        self.assertFalse(missing.exists())
        self.write(self.root / 'manifest.json', {'schema': 'unrelated'})
        self.assertEqual(self.record()['status'], 'unavailable')
        self.assertFalse((self.root / 'mcp/metrics.json').exists())

    def test_malformed_saved_metrics_are_preserved_and_reported(self):
        path = self.root / 'mcp/metrics.json'
        path.write_text('{broken', encoding='utf-8')
        self.assertEqual(self.record()['code'], 'metrics_corrupt')
        self.assertEqual(metrics.summary(self.root)['code'], 'metrics_corrupt')
        self.assertEqual(path.read_text(), '{broken')

    def test_valid_json_with_wrong_counter_type_is_not_overwritten(self):
        self.record()
        path = self.root / 'mcp/metrics.json'
        value = json.loads(path.read_text())
        value['mcp_requests'] = 'private'
        self.write(path, value)
        original = path.read_bytes()
        self.assertEqual(self.record()['code'], 'metrics_corrupt')
        self.assertEqual(path.read_bytes(), original)

    def test_excessively_nested_metrics_are_nonfatal_and_preserved(self):
        path = self.root / 'mcp/metrics.json'
        path.write_text('[' * 2000 + '0' + ']' * 2000, encoding='utf-8')
        original = path.read_bytes()
        self.assertEqual(self.record()['code'], 'metrics_corrupt')
        self.assertEqual(metrics.summary(self.root)['code'], 'metrics_corrupt')
        self.assertEqual(path.read_bytes(), original)

    def test_failed_atomic_write_keeps_prior_summary_and_returns_failure_signal(self):
        self.record()
        before = (self.root / 'mcp/metrics.json').read_bytes()
        with patch.object(metrics.os, 'replace', side_effect=OSError('PRIVATE_PATH')):
            result = self.record()
        self.assertEqual(result, {'status': 'unavailable', 'code': 'metrics_write_failed'})
        self.assertEqual((self.root / 'mcp/metrics.json').read_bytes(), before)
        self.assertEqual(list((self.root / 'mcp').glob('metrics.*.tmp')), [])

    def test_recent_events_are_bounded_but_totals_are_not_reset(self):
        for _ in range(105):
            self.record(action='status', page=None)
        value = metrics.summary(self.root)
        self.assertEqual(value['engine_operations'], 105)
        stored = json.loads((self.root / 'mcp/metrics.json').read_text())
        self.assertLessEqual(len(stored['recent_events']), 64)
        self.assertNotIn('recent_events', value)

    def test_untrusted_action_status_page_and_nonfinite_duration_are_redacted(self):
        metrics.record_event(self.root, 'PRIVATE', {'page': 'PRIVATE'},
                             {'status': 'PRIVATE', 'tokens': 120, 'effort': 'high'}, float('nan'))
        raw = (self.root / 'mcp/metrics.json').read_text()
        self.assertNotIn('PRIVATE', raw)
        value = metrics.summary(self.root)
        self.assertEqual(value['operation_seconds'], 0)
        self.assertIsNone(value['tokens'])
        self.assertEqual(value['by_action']['unknown']['statuses'], {'unknown': 1})


if __name__ == '__main__':
    unittest.main()
