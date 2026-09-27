"""Explicit field diagnostics must not infer omitted source content."""
import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from restoration_contract import validate_page
from restoration_diagnostics import contract_errors


class DiagnosticFieldTests(unittest.TestCase):
    def page(self):
        return json.loads((Path(__file__).resolve().parents[1] /
                           'examples/ocr-page.json').read_text(encoding='utf8'))

    def box(self, page):
        box = {'id': 'q18-box', 'kind': 'box', 'content': [
            {'id': 'q18-body', 'kind': 'paragraph', 'runs': [{'kind': 'text', 'text': '조건'}]}]}
        page['questions'][0]['content'] = [box]
        return box

    def test_missing_box_title_is_exact_and_never_inferred(self):
        page = self.page(); box = self.box(page); before = copy.deepcopy(page)
        with self.assertRaisesRegex(ValueError, 'missing_fields=.*title'):
            validate_page(page)
        row = next(e for e in contract_errors(page) if e['location'] == 'page.questions[0].content[0]')
        self.assertEqual(row['missing_fields'], ['title'])
        self.assertEqual(row['unknown_fields'], [])
        self.assertIn('title', row['hint'])
        self.assertIn('제목', row['hint'])
        self.assertIn('""', row['hint'])
        self.assertEqual(page, before)
        box['title'] = ''
        validate_page(page)
        self.assertEqual(contract_errors(page), [])

    def test_unknown_and_missing_fields_are_both_listed(self):
        page = self.page(); box = self.box(page)
        box.update(bbox_mm=[1, 2, 3, 4], caption='no inference')
        row = next(e for e in contract_errors(page) if e['location'] == 'page.questions[0].content[0]')
        self.assertEqual(row['missing_fields'], ['title'])
        self.assertEqual(row['unknown_fields'], ['bbox_mm', 'caption'])
        with self.assertRaisesRegex(ValueError, 'line bbox coordinates forbidden'):
            validate_page(page)

    def test_nested_independent_fields_are_not_hidden_by_parent(self):
        page = self.page(); box = self.box(page)
        del box['id']; del box['content'][0]['id']; del box['content'][0]['runs']
        errors = {e['location']: e for e in contract_errors(page)}
        self.assertEqual(errors['page.questions[0].content[0]']['missing_fields'], ['id', 'title'])
        self.assertEqual(errors['page.questions[0].content[0].content[0]']['missing_fields'], ['id', 'runs'])

    def test_parent_and_child_same_field_error_are_both_reported(self):
        page = self.page(); box = self.box(page); box['title'] = ''
        del box['id']; del box['content'][0]['id']
        errors = {e['location']: e for e in contract_errors(page)}
        for path in ('page.questions[0].content[0]', 'page.questions[0].content[0].content[0]'):
            self.assertEqual(errors[path]['missing_fields'], ['id'])

    def test_other_field_failures_stay_invalid_and_valid_blocks_unchanged(self):
        page = self.page(); self.assertEqual(contract_errors(page), [])
        for block in ({'id': 'a', 'kind': 'paragraph'},
                      {'id': 'b', 'kind': 'choices', 'rows': []},
                      {'id': 'c', 'kind': 'invalid', 'title': ''}):
            changed = copy.deepcopy(page); changed['questions'][0]['content'] = [block]
            with self.assertRaises(ValueError): validate_page(changed)
            self.assertTrue(contract_errors(changed))


if __name__ == '__main__':
    unittest.main()
