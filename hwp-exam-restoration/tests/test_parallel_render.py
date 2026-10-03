"""Figures of one page compile side by side; everything else in prepare_figures stays single-threaded."""
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import restoration_batch as batch
import test_figure_retry as retry

BARRIER = None
FAIL = set()


def compile_tex(command, root):
    """Stands in for the TeX compiler: returns only once every figure of the page is compiling."""
    BARRIER.wait()
    return 0, b''


def side_by_side(inner):
    def render(source, output, engine=None, dpi=300):
        if Path(source).stem in FAIL: raise ValueError('tikz_compile_failed: synthetic')
        compile_tex(['tex', 'diagram.tex'], output)
        return inner(source, output, engine=engine, dpi=dpi)
    return render


class EngineRetryTests(unittest.TestCase):
    def wrapped(self, replies):
        calls = []

        def compile_tex(command, root):
            calls.append(command); return replies[min(len(calls), len(replies)) - 1]

        import types
        scope = {'compile_tex': compile_tex}; render = types.FunctionType((lambda: None).__code__, scope)
        gate = threading.RLock(); gate.acquire()
        undo = batch._compile_outside(render, gate)
        try:
            with patch.object(batch.time, 'sleep'): return scope['compile_tex'](['tex'], '.'), len(calls)
        finally:
            undo(); gate.release()

    def test_engine_crash_without_a_tex_error_is_compiled_again(self):
        crash = (1, b'This is XeTeX\n\nSorry, but xelatex.exe did not succeed.\n')
        self.assertEqual(self.wrapped([crash, (0, b'ok')]), ((0, b'ok'), 2))

    def test_tex_error_is_not_retried(self):
        error = (1, b'! Undefined control sequence.\nl.12 \\foo\n')
        self.assertEqual(self.wrapped([error, (0, b'ok')]), (error, 1))

    def test_retries_are_bounded(self):
        crash = (1, b'Sorry, but xelatex.exe did not succeed.\n')
        self.assertEqual(self.wrapped([crash]), (crash, 1 + batch.ENGINE_RETRIES))


class ParallelRenderTests(unittest.TestCase):
    def run_page(self, base, count=2, refit=None):
        global BARRIER
        root, engine, rows, calls, runtime = retry.FigureRetryTests().fixture(base)
        runtime = {**runtime, 'render': side_by_side(runtime['render'])}
        BARRIER = threading.Barrier(count, timeout=10)
        with patch.object(batch.runpy, 'run_path', return_value=runtime):
            return batch.prepare_figures(root, 1, rows, base / 'batch', engine=engine, refit=refit), calls

    def test_figures_compile_at_the_same_time_and_keep_their_order(self):
        with tempfile.TemporaryDirectory() as d:
            result, calls = self.run_page(Path(d))  # a serial run would break the barrier
        self.assertEqual([i['id'] for i in result['items']], ['f1', 'f2'])
        self.assertEqual([i['status'] for i in result['items']], ['pending_review'] * 2)
        self.assertEqual(len(calls), 2)

    def test_compiler_hook_is_removed_afterwards(self):
        original = compile_tex
        with tempfile.TemporaryDirectory() as d:
            self.run_page(Path(d))
        self.assertIs(globals()['compile_tex'], original)

    def test_one_failed_figure_does_not_stop_the_other(self):
        FAIL.add('f1')
        try:
            with tempfile.TemporaryDirectory() as d:
                result, _ = self.run_page(Path(d), count=1)
        finally:
            FAIL.clear()
        self.assertEqual([i['status'] for i in result['items']], ['failed', 'pending_review'])
        self.assertEqual((result['status'], [e['id'] for e in result['errors']]), ('failed', ['f1']))

    def test_refit_callbacks_never_overlap(self):
        inside = []; most = []

        def refit(row, pdf):
            inside.append(row['id']); most.append(len(inside))
            time.sleep(0.05)  # room for another thread to barge in, were the callback not serialized
            inside.pop()
            return None
        with tempfile.TemporaryDirectory() as d:
            self.run_page(Path(d), refit=refit)
        self.assertEqual(most, [1, 1])


if __name__ == '__main__':
    unittest.main()
