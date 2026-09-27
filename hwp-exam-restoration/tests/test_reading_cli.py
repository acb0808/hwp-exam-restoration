"""Public CLI routes A/B evidence without generating or changing transcription."""
import contextlib
import io
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import restore
import restoration_job


class ReadingCliTests(unittest.TestCase):
    def setUp(self):
        self.ab = types.ModuleType('restoration_ab')
        for name in ['assign_readers', 'submit_reading', 'compare_page', 'prepare_dispute_views',
                     'approve_page', 'compose_page', 'preparation_handoff']:
            setattr(self.ab, name, Mock(return_value={'status': 'pending'}))

    def call(self, argv):
        stream = io.StringIO()
        with patch.dict(sys.modules, {'restoration_ab': self.ab}), contextlib.redirect_stdout(stream):
            code = restore.main(argv)
        return code, json.loads(stream.getvalue())

    def test_prepare_defaults_to_ab_with_explicit_legacy_escape(self):
        manifest = {'pages': [{'page': 1, 'image': {'path': 'page.png'}}]}
        self.ab.preparation_handoff.return_value = {'mode': 'ab'}
        with patch.object(restoration_job, 'prepare', return_value=manifest), \
                patch.object(restore, 'preparation_handoff', return_value={'mode': 'legacy'}) as legacy:
            code, result = self.call(['prepare', 'source.pdf', 'job'])
            self.assertEqual(code, 0)
            self.assertEqual(result['next'], {'mode': 'ab'})
            self.ab.preparation_handoff.assert_called_once_with(Path('job'), manifest)
            legacy.assert_not_called()
            code, result = self.call(['prepare', 'source.pdf', 'job', '--legacy-workers'])
            self.assertEqual(code, 0)
            self.assertEqual(result['next'], {'mode': 'legacy'})
            legacy.assert_called_once()

    def test_assign_and_submit_preserve_worker_and_evidence_paths(self):
        self.call(['ab-assign', 'job', '2', '--worker-a', 'actual-a', '--worker-b', 'actual-b', '--evidence', 'actual-tool.txt'])
        self.ab.assign_readers.assert_called_once_with(Path('job'), 2, 'actual-a', 'actual-b', Path('actual-tool.txt'))
        self.call(['ab-submit', 'job', '2', 'b', 'reading.json'])
        self.ab.submit_reading.assert_called_once_with(Path('job'), 2, 'b', Path('reading.json'))

    def test_compare_only_prints_module_receipt(self):
        receipt = {'status': 'needs_resolution', 'dispute_count': 1, 'comparison': 'comparison.json'}
        self.ab.compare_page.return_value = receipt
        code, result = self.call(['ab-compare', 'job', '2'])
        self.assertEqual(code, 0)
        self.assertEqual(result, receipt)
        self.ab.compare_page.assert_called_once_with(Path('job'), 2)

    def test_crops_and_approval_use_validated_json_input(self):
        rows = [{'question_id': 'q1', 'bbox_mm': [1, 2, 3, 4]}]
        with patch.object(restore, 'input_json', return_value=rows) as load:
            self.call(['ab-crops', 'job', '1', 'crops.json'])
            load.assert_called_once_with(Path('crops.json'))
        self.ab.prepare_dispute_views.assert_called_once_with(Path('job'), 1, rows)
        decision = {'questions': []}
        with patch.object(restore, 'input_json', return_value=decision) as load:
            self.call(['ab-approve', 'job', '1', 'decision.json'])
            load.assert_called_once_with(Path('decision.json'))
        self.ab.approve_page.assert_called_once_with(Path('job'), 1, decision)

    def test_compose_uses_fresh_output_or_explicit_path_without_implicit_accept(self):
        layout = {'regions': []}
        with patch.object(restore, 'input_json', return_value=layout), \
                patch.object(restore, 'automatic_output', return_value=Path('fresh.json')) as output:
            self.call(['ab-compose', 'job', '1', 'layout.json', '--figures', 'figures.json'])
            output.assert_called_once_with(Path('job'), 1, 'ab-compiled', '.json')
        self.ab.compose_page.assert_called_once_with(Path('job'), 1, layout, Path('fresh.json'), figures=Path('figures.json'))
        self.ab.compose_page.reset_mock()
        with patch.object(restore, 'input_json', return_value=layout), \
                patch.object(restore, 'automatic_output') as output:
            self.call(['ab-compose', 'job', '1', 'layout.json', 'chosen.json'])
            output.assert_not_called()
        self.ab.compose_page.assert_called_once_with(Path('job'), 1, layout, Path('chosen.json'), figures=None)

    def test_failed_operation_returns_failure_exit_status(self):
        self.ab.approve_page.return_value = {'status': 'blocked', 'reason': 'source review required'}
        with patch.object(restore, 'input_json', return_value={}):
            code, result = self.call(['ab-approve', 'job', '1', 'decision.json'])
        self.assertEqual(code, 2)
        self.assertEqual(result['status'], 'blocked')


if __name__ == '__main__':
    unittest.main()
