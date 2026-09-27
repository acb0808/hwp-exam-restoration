"""Expose real engine failures without weakening lossless or native review gates."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from restoration_compiler import _studio_equation
from restoration_tools import content_errors

FAILED_FORMULAS = [
    r'p : xy=0 \qquad\qquad q : |x|+|y|=0',
    r'p : 0 < x+y < xy \qquad q : x > 0, y > 0',
    r'p : |x+y| \ge |x-y| \qquad q : |x-y| \ge |x|-|y|',
    r'p : |x+y|=0 \qquad\qquad q : x^3+y^3=0',
]


class EquationDiagnosticsTests(unittest.TestCase):
    def errors(self, formulas):
        page = json.loads((Path(__file__).resolve().parents[1] / 'examples/ocr-page.json').read_text(encoding='utf8'))
        page['questions'][0]['content'] = [
            {'id': 'diagnostic-formulas', 'kind': 'paragraph',
             'runs': [{'kind': 'equation', 'latex': latex} for latex in formulas]}]
        original = copy.deepcopy(page)
        errors = [error for error in content_errors(page) if error['code'] == 'equation']
        self.assertEqual(page, original)
        return errors

    def test_real_four_failures_report_spacing_commands_and_keep_rejection(self):
        errors = self.errors(FAILED_FORMULAS)
        self.assertEqual(len(errors), 4)
        for source, error in zip(FAILED_FORMULAS, errors):
            self.assertIn('equation_requires_successful_lossless_parse', error['message'])
            diagnostics = error['engine_diagnostics']
            self.assertEqual(len(diagnostics), source.count(r'\qquad'))
            for diagnostic in diagnostics:
                self.assertEqual(diagnostic['code'], 'spacing_approximation')
                self.assertEqual(diagnostic['token'], r'\qquad')
                self.assertEqual(source[diagnostic['start']:diagnostic['end']], diagnostic['token'])
                self.assertEqual(diagnostic['span_unit'], 'unicode_codepoint')
            self.assertIn('원본', error['hint'])
            self.assertIn('자동', error['hint'])
            self.assertIn('issues', error['hint'])
            with self.assertRaisesRegex(ValueError, 'lossless'):
                _studio_equation(source, 'still-rejected')

    def test_parse_error_reports_exact_command_without_converter_retry(self):
        from unittest.mock import patch
        import restoration_equations
        actual = restoration_equations.convert_for_editor
        with patch.object(restoration_equations, 'convert_for_editor', wraps=actual) as convert:
            error = self.errors([r'\unsupported{x}'])[0]
        self.assertEqual(sum(call.args == (r'\unsupported{x}',) for call in convert.call_args_list), 1)
        self.assertEqual(error['engine_diagnostics'][0]['token'], r'\unsupported')
        self.assertEqual(error['engine_diagnostics'][0]['code'], 'unsupported_command')

    def test_missing_argument_has_zero_width_location_and_actionable_hint(self):
        error = self.errors([r'\frac{1}'])[0]
        diagnostic = error['engine_diagnostics'][0]
        self.assertEqual((diagnostic['start'], diagnostic['end'], diagnostic['token']), (8, 8, ''))
        self.assertIn('인수', error['hint'])

    def test_source_verified_independent_formulas_can_use_existing_runs(self):
        # This demonstrates the already supported representation; it is never
        # an automatic rewrite or a claim that a particular gap matches a scan.
        for source in FAILED_FORMULAS:
            left, right = source.split(r'\qquad', 1)
            right = right.lstrip(' ').removeprefix(r'\qquad').lstrip(' ')
            for part in (left.rstrip(), right):
                receipt = _studio_equation(part, 'independent-expression')
                self.assertEqual(receipt['approximations'], [])
                self.assertFalse(receipt['rendering_verified'])
                self.assertTrue(receipt['review_required'])

    def test_compile_draft_returns_all_four_diagnostics_then_validates_explicit_runs(self):
        from test_compact_draft import CompactDraftTests
        from restoration_draft import compile_draft
        from restoration_job import load_json, save_json
        with tempfile.TemporaryDirectory() as directory:
            root, _, _, _, draft = CompactDraftTests().fixture(Path(directory))
            draft['questions'][0]['content'] = [
                {'kind': 'paragraph', 'runs': [{'kind': 'equation', 'latex': source}]}
                for source in FAILED_FORMULAS]
            path = root / 'equation-draft.json'
            output = root / 'equation-result.json'
            save_json(path, draft)
            original = path.read_bytes()
            failed = compile_draft(root, 1, path, output)
            errors = [error for error in failed['errors'] if error['code'] == 'equation']
            self.assertEqual(len(errors), 4)
            self.assertTrue(all(error['engine_diagnostics'][0]['token'] == r'\qquad' for error in errors))
            self.assertEqual(path.read_bytes(), original)
            self.assertFalse(output.exists())
            for block in draft['questions'][0]['content']:
                source = block['runs'][0]['latex']
                left, right = source.split(r'\qquad', 1)
                right = right.lstrip(' ').removeprefix(r'\qquad').lstrip(' ')
                block['runs'] = [{'kind': 'equation', 'latex': left.rstrip()},
                                 {'kind': 'text', 'text': '    '},
                                 {'kind': 'equation', 'latex': right}]
            save_json(path, draft)
            result = compile_draft(root, 1, path, output)
            self.assertEqual(result['status'], 'compiled')
            self.assertFalse(result['accepted'])
            self.assertEqual(result['visual_status'], 'not_verified')
            self.assertEqual(load_json(output)['questions'][0]['content'][0]['runs'],
                             draft['questions'][0]['content'][0]['runs'])


if __name__ == '__main__':
    unittest.main()
