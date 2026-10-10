"""Every MCP reply must come before the client gives up (opencode: 60 s, after which it drops the server).

Seen with five exams at once on one PC: a render took over a minute under load, and the other producers' calls
of the same job queued behind it until they timed out too; a build waited for other jobs' exports while holding
the job. Renders now run beside the job lock and builds wait outside it, both for wait_seconds() at most.
"""
import os
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import restoration_job as job
import restoration_single as single
import test_single_review_workflow as workflow


class Fixture(unittest.TestCase):
    def setUp(self):
        self.case = workflow.SingleReviewTests(); self.case.setUp(); self.addCleanup(self.case.doCleanups)
        self.root, self.call = self.case.root, self.case.call
        env = patch.dict(os.environ, {'HWP_MCP_WAIT_SECONDS': '0.6'}); env.start(); self.addCleanup(env.stop)

    def timed(self, action, **params):
        began = time.perf_counter(); result = self.call(action, **params)
        return result, time.perf_counter() - began


class MergeTests(unittest.TestCase):
    def test_changes_of_one_call_are_applied_over_those_of_another(self):
        base = {'batches': {'a': 1}, 'pages': {'1': {'x': 1}, '2': {'x': 1}}, 'reviews': {'1': 'r'}, 'gone': 1}
        mine = {'batches': {'a': 1, 'b': 2}, 'pages': {'1': {'x': 2}, '2': {'x': 1}}, 'reviews': {}}   # this render
        now = {'batches': {'a': 1, 'c': 3}, 'pages': {'1': {'x': 1}, '2': {'x': 5}}, 'reviews': {'1': 'r'}, 'gone': 1, 'new': 1}
        merged = single.merge_state(now, base, mine)
        self.assertEqual(merged['batches'], {'a': 1, 'b': 2, 'c': 3})
        self.assertEqual(merged['pages'], {'1': {'x': 2}, '2': {'x': 5}})
        self.assertEqual(merged['reviews'], {}); self.assertNotIn('gone', merged); self.assertEqual(merged['new'], 1)

    def test_tables_first_made_by_two_renders_at_once_keep_both_pages(self):
        # Antigravity run of 3 pages: the render of page 4 ended after that of page 3 and replaced the tables page 3
        # had just created, so a warning was no longer marked as told and its note was lost.
        base = {'pages': {}}
        now = {'pages': {}, 'geometry_rounds': {'3/q13-figure-1': 1}, 'geometry_notes': {'3': {'q13-figure-1': ['note']}}}
        mine = {'pages': {}, 'geometry_rounds': {}, 'geometry_notes': {'4': {}}}
        merged = single.merge_state(now, base, mine)
        self.assertEqual(merged['geometry_rounds'], {'3/q13-figure-1': 1})
        self.assertEqual(merged['geometry_notes'], {'3': {'q13-figure-1': ['note']}, '4': {}})
        self.assertEqual(single.merge_state({'a': 1}, {}, {'a': {'x': 1}}), {'a': {'x': 1}})   # a value of another kind is replaced


class RenderTests(Fixture):
    def slow_render(self, seconds, mark):
        def perform(action, params, root, state):
            assert action == 'render_figures'
            time.sleep(seconds); state.setdefault('batches', {})[mark] = {'page': params['page']}
            return {'status': 'pending_review', 'batch_path': mark}
        return perform

    def test_a_long_render_is_reported_and_its_result_handed_to_the_next_call(self):
        with patch.object(single, 'perform', side_effect=self.slow_render(1.5, 'b1')):
            first, took = self.timed('render_figures', page=1)
            self.assertEqual(first['status'], 'rendering'); self.assertLess(took, 1.2)
            second = self.call('render_figures', page=1)
            while second['status'] == 'rendering': second = self.call('render_figures', page=1)
        self.assertEqual(second['batch_path'], 'b1')
        self.assertIn('b1', job.load_json(self.root / 'mcp/state.json')['batches'])

    def test_other_calls_of_the_job_are_answered_while_a_page_renders(self):
        with patch.object(single, 'perform', side_effect=self.slow_render(2.0, 'b1')) as fake:
            self.call('render_figures', page=1)
            fake.side_effect = None; fake.return_value = {'status': 'in_progress'}
            status, took = self.timed('status')
            self.assertLess(took, 0.5)
            fake.side_effect = self.slow_render(0, 'unused')
            while self.call('render_figures', page=1)['status'] == 'rendering': pass

    def test_a_change_saved_by_another_call_during_the_render_is_kept(self):
        with patch.object(single, 'perform', side_effect=self.slow_render(1.0, 'b1')):
            self.call('render_figures', page=1)
            with single.shared._MUTEX:
                state = job.load_json(self.root / 'mcp/state.json'); state['reviews'] = {'9': {'status': 'passed'}}
                single.save(self.root, state)
            while self.call('render_figures', page=1)['status'] == 'rendering': pass
        state = job.load_json(self.root / 'mcp/state.json')
        self.assertIn('b1', state['batches']); self.assertEqual(state['reviews'], {'9': {'status': 'passed'}})

    def test_files_edited_during_the_render_are_pointed_out(self):
        folder = self.root / 'workers/page-0001'; folder.mkdir(parents=True, exist_ok=True)
        with patch.object(single, 'perform', side_effect=self.slow_render(1.0, 'b1')):
            self.call('render_figures', page=1)
            (folder / 'f1.tex').write_text('changed', encoding='utf-8')
            result = self.call('render_figures', page=1)
            while result['status'] == 'rendering': result = self.call('render_figures', page=1)
        self.assertTrue(result['inputs_changed_during_render']); self.assertIn('render the current files', result['next_action'])

    def test_a_failing_render_is_reported_not_left_running(self):
        with patch.object(single, 'perform', side_effect=ValueError('figure_not_in_current_reading')):
            result = self.call('render_figures', page=1)
        self.assertEqual(result['status'], 'failed'); self.assertIn('figure_not_in_current_reading', result['message'])
        self.assertEqual(single._RENDERS, {})


class BuildTests(Fixture):
    def start_build(self, running, collected):
        proc = MagicMock(); proc.pid = 987654
        self.addCleanup(lambda: single._NATIVE_PROCESSES.pop(proc.pid, None))
        return [patch.object(single.shared, '_perform', return_value={'status': 'built'}),
                patch.object(single.subprocess, 'Popen', return_value=proc),
                patch.object(single, 'native_running', side_effect=running),
                patch.object(single, 'collect_native', side_effect=collected)]

    def run_with(self, patches, fn):
        for p in patches: p.start()
        try: return fn()
        finally:
            for p in patches: p.stop()

    def test_an_export_that_ends_within_the_wait_is_reported_by_the_build_call(self):
        ticks = iter([True, False])  # one poll (0.5 s) inside the wait
        # The wait counts from the start of the call, so 0.6 s leaves the build itself only 0.1 s: too little on a busy PC.
        patches = [patch.dict(os.environ, {'HWP_MCP_WAIT_SECONDS': '5'})] + self.start_build(lambda run: next(ticks, False), [{'status': 'building'}, {'status': 'pending_review'}])
        result = self.run_with(patches, lambda: self.call('build', output=str(self.root / 'out.hwpx'), title='t', school='s', year='2026', exam_title='e'))
        self.assertEqual(result['status'], 'pending_review'); self.assertTrue(result['build_output'].endswith('out.hwpx'))

    def test_a_long_export_is_reported_as_building_and_the_job_stays_free(self):
        patches = self.start_build(lambda run: True, lambda root, state: {'status': 'building'})
        out = {}

        def build():
            began = time.perf_counter()
            out['result'] = self.call('build', output=str(self.root / 'out.hwpx'), title='t', school='s', year='2026', exam_title='e')
            out['took'] = time.perf_counter() - began
        def both():
            worker = threading.Thread(target=build); worker.start(); time.sleep(0.2)
            out['page'], out['page_took'] = self.timed('status', page=1)
            worker.join()
        self.run_with(patches, both)
        self.assertEqual(out['result']['status'], 'building'); self.assertLess(out['took'], 1.5)
        self.assertLess(out['page_took'], 0.4)


if __name__ == '__main__':
    unittest.main()
