"""Builds of several jobs take turns at the native Hangul export (restoration_native_queue)."""
import json
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import restoration_native_queue as queue
import restoration_single as single


class TurnTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def test_free_queue_is_taken_at_once(self):
        with queue.turn(self.dir / 'run-a', 'a.hwpx', folder=self.dir) as waited:
            self.assertFalse(waited); self.assertTrue((self.dir / queue.QUEUE).exists())
            self.assertEqual(queue.queue_state(self.dir / 'run-a')['state'], 'exporting')
        self.assertFalse((self.dir / queue.QUEUE).exists()); self.assertIsNone(queue.queue_state(self.dir / 'run-a'))

    def test_five_jobs_export_one_after_another(self):
        inside = []; most = []; order = []

        def job(name):
            with queue.turn(self.dir / name, name, folder=self.dir, hwp_running=lambda: False):
                inside.append(name); most.append(len(inside)); time.sleep(0.05); order.append(name); inside.remove(name)
        threads = [threading.Thread(target=job, args=(f'run-{i}',)) for i in range(5)]
        with patch.object(queue, 'POLL_SECONDS', 0.01):
            [t.start() for t in threads]; [t.join(10) for t in threads]
        self.assertEqual(sorted(order), [f'run-{i}' for i in range(5)]); self.assertEqual(max(most), 1)

    def test_waiting_job_says_so_and_gives_up_after_the_wait(self):
        with queue.turn(self.dir / 'run-a', 'a.hwpx', folder=self.dir):
            with self.assertRaises(queue.QueueTimeout), patch.object(queue, 'POLL_SECONDS', 0.01):
                with queue.turn(self.dir / 'run-b', 'b.hwpx', folder=self.dir, wait=0.05): self.fail('entered while busy')
            self.assertEqual(queue.queue_state(self.dir / 'run-b')['state'], 'waiting')

    def test_lock_of_a_dead_owner_does_not_block(self):
        owner = {'schema_version': '1.0', 'run_id': 'gone', 'pid': 2 ** 22 + 12345, 'process_name': 'python.exe',
                 'document_key': 'x', 'host': socket.gethostname(), 'acquired_at': '2026-01-01T00:00:00Z'}
        (self.dir / queue.QUEUE).write_text(json.dumps(owner) + '\n', encoding='utf-8')
        with queue.turn(self.dir / 'run-a', 'a.hwpx', folder=self.dir, wait=1) as waited: self.assertFalse(waited)

    def test_an_open_hangul_is_not_waited_for_when_no_export_was_ahead(self):
        seen = []
        with queue.turn(self.dir / 'run-a', 'a.hwpx', folder=self.dir, hwp_running=lambda: seen.append(1) or True): pass
        self.assertEqual(seen, [])  # it is the user's; the export itself reports it


class ReleaseTests(unittest.TestCase):
    def test_release_is_tried_again_while_a_waiting_job_reads_the_lock(self):
        class Lease:
            calls = 0
            def release(self):
                self.calls += 1
                if self.calls < 3: raise PermissionError(13, 'in use')
        lease = Lease(); queue._release(lease, sleep=lambda s: None)
        self.assertEqual(lease.calls, 3)

    def test_release_never_fails_the_finished_export(self):
        class Lease:
            def release(self): raise PermissionError(13, 'in use')
        queue._release(Lease(), sleep=lambda s: None)  # the lock then names a dead owner once this process exits


class StatusTests(unittest.TestCase):
    def run_state(self, started, marker=None):
        folder = Path(tempfile.mkdtemp()) / 'hwp-single-x'
        if marker: queue.marker_path(folder).write_text(json.dumps(marker), encoding='utf-8')
        return {'native_run': {'receipt': str(folder / 'restoration-native.json'), 'started': started, 'pid': None}}

    def test_queued_build_reports_waiting_not_failure(self):
        state = self.run_state(time.time() - 400, {'state': 'waiting', 'at': time.time() - 400})
        with patch.object(single, 'native_running', return_value=True): got = single.collect_native(Path('.'), state)
        self.assertEqual((got['status'], got['message']), ('building', 'native_export_waiting_for_another_job'))

    def test_export_clock_starts_at_its_own_turn(self):
        state = self.run_state(time.time() - 500, {'state': 'exporting', 'at': time.time() - 30})
        with patch.object(single, 'native_running', return_value=True): got = single.collect_native(Path('.'), state)
        self.assertEqual((got['status'], got['message']), ('building', 'native_export_pending'))

    def test_build_that_never_queued_keeps_the_old_limit(self):
        with patch.object(single, 'native_running', return_value=True): got = single.collect_native(Path('.'), self.run_state(time.time() - 500))
        self.assertEqual(got['status'], 'failed')

    def test_queue_timeout_asks_to_build_again_later(self):
        folder = Path(tempfile.mkdtemp()); receipt = folder / 'restoration-native.json'
        receipt.write_text(json.dumps({'status': 'blocked', 'error': 'native_export_queue_timeout', 'cleanup': 'no_session_started'}), encoding='utf-8')
        got = single.collect_native(Path('.'), {'native_run': {'receipt': str(receipt), 'returncode': 2}})
        self.assertEqual(got['message'], 'native_export_queue_timeout'); self.assertIn('hwp_build again', got['next_action'])


class DamagedMarkerTests(unittest.TestCase):
    """The status reply while an export is pending reads the queue marker; a damaged one must not break it."""
    def collect(self, marker):
        import restoration_single as single
        run = Path(tempfile.mkdtemp()) / 'hwp-single-x'; run.mkdir()
        if marker is not None: queue.marker_path(run).write_text(marker, encoding='utf-8')
        state = {'native_run': {'receipt': str(run / 'restoration-native.json'), 'started': time.time(), 'log': 'x.log'}}
        with patch.object(single, 'native_running', return_value=True):
            return single.collect_native(run.parent, state)

    def test_marker_without_a_time_or_of_another_shape_still_reports_building(self):
        for marker in ('{"state": "exporting"}', '{"state": "exporting", "at": "soon"}', '[]', '"waiting"', '{', None):
            with self.subTest(marker=marker):
                self.assertEqual(self.collect(marker)['status'], 'building')

    def test_waiting_marker_is_reported_as_waiting(self):
        self.assertEqual(self.collect(json.dumps({'state': 'waiting', 'at': time.time()}))['message'], 'native_export_waiting_for_another_job')


if __name__ == '__main__':
    unittest.main()
