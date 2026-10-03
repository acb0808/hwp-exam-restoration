"""A run stopped half-way must not leave the job, or this PC's Hangul export, unusable."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import restoration_job as job
import restoration_native_queue as queue


def dead_pid():
    """Pid of a process that has just exited."""
    p = subprocess.Popen([sys.executable, '-c', 'pass']); p.wait(); return p.pid


class JobLockTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp()); self.lock = self.root / '.job.lock'

    def enter(self):
        with job._locked(self.root): return self.lock.read_text()

    def test_lock_of_a_killed_process_is_taken_over(self):
        self.lock.write_text(str(dead_pid()))
        self.assertEqual(self.enter(), str(os.getpid())); self.assertFalse(self.lock.exists())

    def test_own_lock_left_by_a_failed_release_is_taken_over(self):
        self.lock.write_text(str(os.getpid()))
        self.assertEqual(self.enter(), str(os.getpid()))

    def test_lock_of_a_running_process_still_blocks(self):
        other = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        try:
            self.lock.write_text(str(other.pid))
            with self.assertRaisesRegex(ValueError, 'job_busy_or_interrupted_lock_requires_review'): self.enter()
        finally:
            other.kill(); other.wait()

    def test_lock_whose_pid_now_belongs_to_a_younger_process_is_taken_over(self):
        other = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])  # stands in for a reused pid
        try:
            self.lock.write_text(str(other.pid)); old = time.time() - 600; os.utime(self.lock, (old, old))
            self.assertEqual(self.enter(), str(os.getpid()))
        finally:
            other.kill(); other.wait()

    def test_lock_taken_by_someone_else_during_the_takeover_is_not_removed(self):
        self.lock.write_text(str(dead_pid())); real = job._abandoned

        def judged(path, root):
            verdict = real(path, root); path.write_text('retaken'); return verdict  # another process got in first
        with patch.object(job, '_abandoned', judged):
            with self.assertRaisesRegex(ValueError, 'job_busy'): self.enter()
        self.assertEqual(self.lock.read_text(), 'retaken')

    def test_nested_use_in_this_process_still_blocks(self):
        with job._locked(self.root):
            with self.assertRaisesRegex(ValueError, 'job_busy'): self.enter()
        self.assertFalse(self.lock.exists())

    def test_unwritten_lock_blocks_until_it_is_old(self):
        self.lock.write_text('')
        with self.assertRaisesRegex(ValueError, 'job_busy'): self.enter()
        old = time.time() - job.STALE_LOCK_SECONDS - 5; os.utime(self.lock, (old, old))
        self.assertEqual(self.enter(), str(os.getpid()))

    def test_release_waits_out_a_file_briefly_held_by_another_program(self):
        real = Path.unlink; calls = []

        def unlink(path, *a, **k):
            if path.name == '.job.lock' and len(calls) < 2: calls.append(1); raise PermissionError(13, 'in use')
            return real(path, *a, **k)
        with patch.object(Path, 'unlink', unlink), patch.object(job.time, 'sleep'):
            with job._locked(self.root): pass
        self.assertEqual(len(calls), 2); self.assertFalse(self.lock.exists())


class OwnedSessionTests(unittest.TestCase):
    def setUp(self):
        self.run = Path(tempfile.mkdtemp())

    def test_export_that_never_started_hangul_left_nothing(self):
        self.assertTrue(queue.owned_session_gone(self.run))

    def test_recorded_session_that_already_exited(self):
        (self.run / 'ownership.json').write_text(json.dumps({'pid': dead_pid(), 'exe': 'C:/x/Hwp.exe', 'create_time': 1.0}), encoding='utf-8')
        self.assertTrue(queue.owned_session_gone(self.run))

    def test_another_program_now_using_that_pid_is_left_alone(self):
        other = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        try:
            (self.run / 'ownership.json').write_text(json.dumps({'pid': other.pid, 'exe': 'C:/x/Hwp.exe', 'create_time': 1.0}), encoding='utf-8')
            self.assertFalse(queue.owned_session_gone(self.run)); self.assertIsNone(other.poll())
        finally:
            other.kill(); other.wait()

    def test_the_recorded_session_itself_is_closed(self):
        import psutil
        owned = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        try:
            p = psutil.Process(owned.pid)
            (self.run / 'ownership.json').write_text(json.dumps({'pid': p.pid, 'exe': p.exe(), 'create_time': p.create_time()}), encoding='utf-8')
            self.assertTrue(queue.owned_session_gone(self.run)); owned.wait(5); self.assertIsNotNone(owned.poll())
        finally:
            if owned.poll() is None: owned.kill(); owned.wait()

    def test_unreadable_record_is_not_taken_as_gone(self):
        (self.run / 'ownership.json').write_text('{', encoding='utf-8')
        self.assertFalse(queue.owned_session_gone(self.run))


class ReapTests(unittest.TestCase):
    """A stand-in process plays the hidden Hangul an interrupted export left running."""
    def setUp(self):
        import psutil
        self.dir = Path(tempfile.mkdtemp()); self.run = Path(tempfile.mkdtemp()) / 'run-x'; self.run.mkdir()
        self.owned = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        p = psutil.Process(self.owned.pid); self.identity = {'pid': p.pid, 'exe': p.exe(), 'create_time': p.create_time()}
        self.addCleanup(lambda: self.owned.poll() is None and (self.owned.kill(), self.owned.wait()))

    def as_hwp(self):
        import psutil
        fake = type('P', (), {'pid': self.owned.pid, 'info': {'name': 'Hwp.exe'}})()
        return patch.object(psutil, 'process_iter', return_value=[fake])

    def test_export_cut_short_is_found_through_its_pointer_and_closed(self):
        (self.run / 'ownership.json').write_text(json.dumps(self.identity), encoding='utf-8')
        pointer = self.dir / f'{queue.RUNNING}run-x.json'; pointer.write_text(json.dumps({'run_dir': str(self.run)}), encoding='utf-8')
        with self.as_hwp(): cleared = queue.reap_orphans(self.dir)
        self.assertEqual(cleared, ['run-x']); self.owned.wait(5); self.assertFalse(pointer.exists())

    def test_run_folder_in_the_usual_place_is_found_without_a_pointer(self):
        run = self.dir / 'hwp-single-abc'; run.mkdir(); (run / 'ownership.json').write_text(json.dumps(self.identity), encoding='utf-8')
        with self.as_hwp(): self.assertEqual(queue.reap_orphans(self.dir), ['hwp-single-abc'])
        self.owned.wait(5)

    def test_a_hangul_with_no_export_record_is_the_users_and_is_left_alone(self):
        with self.as_hwp(): self.assertEqual(queue.reap_orphans(self.dir), [])
        self.assertIsNone(self.owned.poll())

    def test_turn_clears_the_leftover_before_the_next_export(self):
        (self.run / 'ownership.json').write_text(json.dumps(self.identity), encoding='utf-8')
        (self.dir / f'{queue.RUNNING}run-x.json').write_text(json.dumps({'run_dir': str(self.run)}), encoding='utf-8')
        with self.as_hwp():
            with queue.turn(self.dir / 'run-next', 'next.hwpx', folder=self.dir, hwp_running=lambda: False):
                self.owned.wait(5); self.assertTrue((self.dir / f'{queue.RUNNING}run-next.json').exists())
        self.assertEqual(list(self.dir.glob(queue.RUNNING + '*')), [])

    def test_pointer_stays_when_the_export_is_cut_short(self):
        with self.assertRaises(KeyboardInterrupt):
            with queue.turn(self.dir / 'run-cut', 'cut.hwpx', folder=self.dir, hwp_running=lambda: False): raise KeyboardInterrupt
        self.assertTrue((self.dir / f'{queue.RUNNING}run-cut.json').exists()); self.assertFalse((self.dir / queue.QUEUE).exists())

    def lock_record(self, pid, age):
        return json.dumps({'schema_version': '1.0', 'run_id': 'hwp-single-old', 'pid': pid, 'process_name': 'python.exe', 'document_key': 'x',
                           'host': __import__('socket').gethostname(), 'acquired_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time() - age))})

    def test_locks_whose_pid_was_reused_are_cleared_before_the_export(self):
        """Seen in a real restart: the killed export's pid belonged to a new process a minute later."""
        for name in (queue.QUEUE, queue.SESSION): (self.dir / name).write_text(self.lock_record(self.owned.pid, age=600), encoding='utf-8')
        with queue.turn(self.dir / 'run-a', 'a.hwpx', folder=self.dir, wait=3, sleep=lambda s: None, hwp_running=lambda: False) as waited:
            self.assertFalse((self.dir / queue.SESSION).exists())
        self.assertFalse((self.dir / queue.QUEUE).exists())

    def test_lock_of_a_running_export_is_respected(self):
        (self.dir / queue.SESSION).write_text(self.lock_record(self.owned.pid, age=-5), encoding='utf-8')  # taken after its owner started
        self.assertTrue(queue._lock_held(self.dir / queue.SESSION)); queue._drop_stale(self.dir / queue.SESSION)
        self.assertTrue((self.dir / queue.SESSION).exists())

    def test_pointers_are_tidied_when_no_hangul_is_running(self):
        import psutil
        pointer = self.dir / f'{queue.RUNNING}run-x.json'; pointer.write_text(json.dumps({'run_dir': str(self.run)}), encoding='utf-8')
        with patch.object(psutil, 'process_iter', return_value=[]): self.assertEqual(queue.reap_orphans(self.dir), [])
        self.assertFalse(pointer.exists())

    def test_session_lock_of_a_killed_export_does_not_delay_the_next(self):
        (self.dir / queue.SESSION).write_text(json.dumps({'pid': dead_pid()}), encoding='utf-8'); slept = []
        with queue.turn(self.dir / 'run-a', 'a.hwpx', folder=self.dir, sleep=slept.append, hwp_running=lambda: False): pass
        self.assertEqual(slept, [])


if __name__ == '__main__':
    unittest.main()
