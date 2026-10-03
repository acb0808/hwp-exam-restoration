"""Producer replies stay under the ~4KB size Antigravity shows inline (larger ones are saved to a file and reread)."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import restoration_single as single
from restoration_reply import HOST_REPLY_BYTES, compact_reply, size

JOB = 'D:\\Work\\OneDrive\\바탕 화면\\Coding\\testOCR\\pdfs\\job_exam_2025_3_2_final'


def render_result(n):
    batch = JOB + '\\mcp\\figures-' + 'a' * 32
    return {'status': 'pending_review', 'batch_path': batch + '\\batch.json', 'errors': [],
            'reviews': [{'id': f'q{i}-figure-1', 'checks': {k: 'not_verified' for k in ('geometry', 'labels', 'marks', 'source_comparison')},
                         'status': 'failed', 'issues': []} for i in range(n)],
            'review_tasks': [{'id': f'q{i}-figure-1', 'source_image': JOB + '\\pages\\page-0003.png',
                              'render_image': f'{batch}\\q{i}-figure-1\\diagram.png', 'reused': False,
                              'compare_image': f'{batch}\\q{i}-figure-1\\q{i}-figure-1-compare.png'} for i in range(n)],
            'reused_review_ids': [], 'tex_paths': {f'q{i}-figure-1': f'{JOB}\\workers\\page-0003\\q{i}-figure-1.tex' for i in range(n)},
            'compare_sheets': [batch + '\\compare-sheet-1.png'], 'next_action': 'x' * 670}


class HostReplyTests(unittest.TestCase):
    def test_render_reply_for_a_crowded_page_fits(self):
        full = render_result(6)
        self.assertGreater(size(full), HOST_REPLY_BYTES)  # the measured runs saved these to files
        short = compact_reply('render_figures', full)
        self.assertLess(size(short), HOST_REPLY_BYTES)
        self.assertEqual(short['pending_ids'], [r['id'] for r in full['reviews']])
        self.assertEqual(short['source_image'], JOB + '\\pages\\page-0003.png')
        self.assertTrue(all('render_image' in t and 'compare_image' not in t for t in short['review_tasks']))
        self.assertEqual(short['tex_dir'], JOB + '\\workers\\page-0003')
        self.assertIn('reviews', full)  # the service result itself is unchanged

    def test_without_a_sheet_each_task_keeps_its_compare_image(self):
        full = render_result(2); full.pop('compare_sheets')
        short = compact_reply('render_figures', full)
        self.assertTrue(all('compare_image' in t for t in short['review_tasks']))

    def test_other_actions_pass_through(self):
        value = {'status': 'accepted', 'page': 1}
        self.assertIs(compact_reply('submit_reading', value), value)

    def test_figure_rules_reply_fits(self):
        rules = (single.shared.SKILL / 'references/tikz-rules.md').read_text(encoding='utf-8')
        reply = {'status': 'ready_for_figures', 'page': 3, 'figure_ids': ['q12-figure-1', 'q13-figure-1', 'q14-figure-1'],
                 'next_action': 'Same producer supplies only the required diagram TeX. Render and compare figures; layout is automatic.',
                 'figure_rules': rules}
        self.assertLess(size(reply), HOST_REPLY_BYTES)


if __name__ == '__main__':
    unittest.main()
