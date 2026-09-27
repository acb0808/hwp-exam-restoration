"""Contract diagnostics never repair content or replace provenance review."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from restoration_contract import validate_page


class QualityDiagnosticsTests(unittest.TestCase):
    def page(self):
        path = Path(__file__).resolve().parents[1] / 'examples/ocr-page.json'
        return json.loads(path.read_text(encoding='utf8'))

    def test_figure_only_paragraph_preserves_provenance_gate(self):
        from test_tikz_handoff import TikzHandoffTests
        from figure_provenance import validate_figures
        with tempfile.TemporaryDirectory() as directory:
            proof, figure, _ = TikzHandoffTests().fixture(Path(directory))
            page = self.page()
            page.update({k: proof[k] for k in ('source_sha256', 'page_number', 'worker_id')})
            page['questions'][0]['content'] = [
                {'id': 'figure-only', 'kind': 'paragraph', 'runs': [], 'figure': figure}]
            validate_page(page)
            self.assertEqual(len(validate_figures(page)), 1)
            del figure['tikz']
            with self.assertRaisesRegex(ValueError, 'tikz_provenance_required'):
                validate_figures(page)
            figure['size_mm'] = [1000, 20]
            with self.assertRaisesRegex(ValueError, 'outside question width'):
                validate_page(page)

    def test_empty_body_and_choice_still_fail(self):
        for kind in ('paragraph', 'choices'):
            page = self.page()
            block = {'id': 'empty', 'kind': kind}
            block.update({'runs': []} if kind == 'paragraph' else {'columns': 1, 'rows': [[[]]]})
            page['questions'][0]['content'] = [block]
            with self.assertRaisesRegex(ValueError, 'runs: expected nonempty'):
                validate_page(page)
        for figure in (None, {}, {'path': 'unbound.png'}):
            page = self.page()
            page['questions'][0]['content'] = [
                {'id': 'empty', 'kind': 'paragraph', 'runs': [], 'figure': figure}]
            with self.assertRaisesRegex(ValueError, 'figure: invalid fields'):
                validate_page(page)

    def test_collects_geometry_and_independent_content_errors_without_mutation(self):
        from restoration_diagnostics import contract_errors
        page = self.page()
        q = page['questions'][0]
        q['bbox_mm'] = [15, 190.1, 85, 80]
        page['questions'][1]['content'] = [
            {'id': 'bad-body', 'kind': 'paragraph', 'runs': []},
            {'id': 'bad-options', 'kind': 'choices', 'columns': 1, 'rows': [[[]]]}]
        before = copy.deepcopy(page)
        errors = contract_errors(page)
        self.assertEqual(page, before)
        geometry = next(e for e in errors if e.get('question_id') == 'q1' and 'excess_mm' in e)
        self.assertEqual(geometry['location'], 'page.questions[0].bbox_mm')
        self.assertAlmostEqual(geometry['excess_mm']['bottom'], .1)
        self.assertEqual(geometry['parent_bbox_mm'], [15, 30, 85, 240])
        locations = {e['location'] for e in errors}
        self.assertIn('page.questions[1].content[0]', locations)
        self.assertIn('page.questions[1].content[1]', locations)

    def test_authoritative_parity_and_malformed_input(self):
        from restoration_diagnostics import contract_errors
        valid = self.page()
        cases = [valid, None, [], {}, {'regions': None}, {**valid, 'regions': []},
                 {**valid, 'issues': ['unresolved']}, {**valid, 'version': 99}]
        for change in ({'content': None}, {'bbox_mm': ['bad', 1, 2, 3]},
                       {'bbox_mm': [0, 0, float('nan'), 2]}, {'region_id': 'missing'},
                       {'font_pt': 'bad'}, {'content': [{'kind': 'box', 'id': 'x', 'content': None}]}):
            page = copy.deepcopy(valid); page['questions'][0].update(change); cases.append(page)
        for page in cases:
            with self.subTest(page=repr(page)[:80]):
                try:
                    validate_page(page)
                    passed = True
                except (ValueError, KeyError, TypeError):
                    passed = False
                self.assertEqual(contract_errors(page) == [], passed)

    def test_nested_sibling_errors_are_localized(self):
        from restoration_diagnostics import contract_errors
        page = self.page()
        page['questions'][0]['content'] = [{'id': 'box', 'kind': 'box', 'title': '', 'content': [
            {'id': 'a', 'kind': 'paragraph', 'runs': [{'kind': 'text', 'text': 'bad\nline'}]},
            {'id': 'b', 'kind': 'paragraph', 'runs': []}]}]
        locations = {e['location'] for e in contract_errors(page)}
        self.assertIn('page.questions[0].content[0].content[0]', locations)
        self.assertIn('page.questions[0].content[0].content[1]', locations)

    def test_boundary_tolerance_and_global_failure_are_not_relaxed(self):
        from restoration_diagnostics import contract_errors
        page = self.page()
        page['questions'][0]['bbox_mm'] = [15, 190, 85, 80]
        self.assertEqual(contract_errors(page), [])
        page['questions'][0]['bbox_mm'][1] += .000001
        errors = contract_errors(page)
        self.assertTrue(any('excess_mm' in e for e in errors))
        page['issues'] = ['unresolved source text']
        errors = contract_errors(page)
        self.assertTrue(any('unresolved issues' in e['message'] for e in errors))
        self.assertTrue(any('excess_mm' in e for e in errors))


if __name__ == '__main__':
    unittest.main()
