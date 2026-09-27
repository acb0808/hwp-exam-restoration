"""A transient progress-file lock must not orphan a successful native worker."""
import json, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from runtime_paths import automation_root
sys.path.insert(0, str(automation_root() / 'scripts'))
import native_layout as native


class NativeStageRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source.hwpx'
        self.source.write_bytes(b'fixture')
        self.run = self.root / 'native'
        self.worker = MagicMock(returncode=0)
        self.worker.poll.side_effect = [None, 0]
        self.real_read = Path.read_text

    def start_worker(self, *args, **kwargs):
        # Fake process boundary only: all receipts and digest checks remain real.
        artifacts = {}
        for fmt in ('hwpx', 'hwp', 'pdf'):
            path = self.run / ('render.' + fmt)
            path.write_bytes(b'fixture-' + fmt.encode())
            artifacts[fmt] = {'path': str(path), 'sha256': native.digest(path)}
        native.write_json(self.run / 'ownership.json',
                          {'pid': 123, 'exe': 'owned-fixture', 'create_time': 1.0})
        native.write_json(self.run / 'render.json', {
            'status': 'rendered', 'source_sha256': native.digest(self.source),
            'pdf_sha256': artifacts['pdf']['sha256'],
            'measured_artifact': artifacts['hwpx'], 'artifacts': artifacts,
            'cleanup': 'closed_owned_session'})
        return self.worker

    def run_supervisor(self, read):
        with patch.object(native.importlib.util, 'find_spec', return_value=object()), \
             patch.object(native, '_desktop_activation_allowed', return_value=True), \
             patch('psutil.process_iter', return_value=[]), \
             patch.object(native.subprocess, 'Popen', side_effect=self.start_worker), \
             patch.object(native.time, 'sleep'), \
             patch.object(Path, 'read_text', read):
            return native.render_native(self.source, self.run)

    def test_transient_stage_lock_keeps_worker_and_validates_success(self):
        attempts = []
        def read(path, *args, **kwargs):
            if path.name == 'stage.json':
                attempts.append(path)
                if len(attempts) <= 2:
                    raise PermissionError(13, 'Permission denied', str(path))
            return self.real_read(path, *args, **kwargs)
        try:
            result = self.run_supervisor(read)
        except PermissionError as exc:
            self.fail('Progress-file lock escaped and detached the worker: ' + str(exc))
        self.assertEqual(result['status'], 'rendered')
        self.assertEqual(result['cleanup'], 'closed_owned_session')
        self.worker.terminate.assert_not_called()
        self.assertEqual(len(attempts), 3)

    def test_persistent_stage_lock_stops_worker_and_preserves_owned_cleanup(self):
        def read(path, *args, **kwargs):
            if path.name == 'stage.json':
                raise PermissionError(13, 'Permission denied', str(path))
            return self.real_read(path, *args, **kwargs)
        with patch.object(native, 'cleanup_owned', return_value='terminated_owned_session') as cleanup:
            try:
                result = self.run_supervisor(read)
            except PermissionError as exc:
                self.fail('Permanent progress-file error escaped without worker cleanup: ' + str(exc))
        self.assertEqual(result['status'], 'failed')
        self.assertIn('native_monitor_read_failed', result['error'])
        self.assertEqual(result['cleanup'], 'terminated_owned_session')
        self.worker.terminate.assert_called_once()
        self.worker.wait.assert_called_once()
        self.assertEqual(cleanup.call_args.args[0]['exe'], 'owned-fixture')


if __name__ == '__main__':
    unittest.main()
