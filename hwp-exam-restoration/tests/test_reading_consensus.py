"""A/B comparison catches substantive OCR disagreement without inventing layout."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from restoration_reading import compare_readings, validate_reading


def reading(worker='a'):
    return {'schema': 'restoration-reading/1', 'page': 1, 'worker_id': worker,
            'source_sha256': 'a' * 64, 'complete': True, 'issues': [],
            'questions': [
                {'id': 'q1', 'column': 'left', 'uncertain': False, 'content': [
                    {'kind': 'paragraph', 'runs': [
                        {'kind': 'text', 'text': '다음 식의 값은? '},
                        {'kind': 'equation', 'latex': 'x-1'}]},
                    {'kind': 'choices', 'columns': 2, 'rows': [[
                        [{'kind': 'text', 'text': '① '}, {'kind': 'equation', 'latex': '-1'}],
                        [{'kind': 'text', 'text': '② '}, {'kind': 'equation', 'latex': '1'}]]]}]},
                {'id': 'q2', 'column': 'right', 'uncertain': False, 'content': [
                    {'kind': 'box', 'title': '', 'content': [
                        {'kind': 'paragraph', 'runs': [{'kind': 'text', 'text': '조건'}]}]},
                    {'kind': 'paragraph', 'runs': [], 'figure_ref': 'q2-figure-1'}]}]}


class ReadingConsensusTests(unittest.TestCase):
    def pair(self):
        return reading('a'), reading('b')

    def assert_dispute(self, a, b, qid, reason):
        result = compare_readings(a, b)
        self.assertEqual(result['status'], 'needs_resolution')
        dispute = next(item for item in result['disputes'] if item['question_id'] == qid)
        self.assertIn(reason, dispute['reasons'])
        return result

    def test_identical_content_agrees_without_mutation(self):
        a, b = self.pair()
        original = deepcopy(a)
        self.assertIs(validate_reading(a, 1, 'a', 'a' * 64), a)
        result = compare_readings(a, b)
        self.assertEqual(a, original)
        self.assertEqual(result['status'], 'agreement')
        self.assertEqual(result['matching_ids'], ['q1', 'q2'])
        self.assertEqual(result['figure_ids'], ['q2-figure-1'])
        self.assertEqual(result['disputes'], [])

    def test_missing_minus_and_choices_are_not_normalized(self):
        a, b = self.pair()
        b['questions'][0]['content'][0]['runs'][1]['latex'] = 'x1'
        result = self.assert_dispute(a, b, 'q1', 'content_mismatch')
        self.assertEqual(result['matching_ids'], ['q2'])
        # Diagnostics are detached from original evidence.
        result['disputes'][0]['a']['content'].clear()
        self.assertEqual(len(a['questions'][0]['content']), 2)
        a, b = self.pair()
        b['questions'][0]['content'][1]['rows'][0][0][1]['latex'] = '1'
        self.assert_dispute(a, b, 'q1', 'content_mismatch')

    def test_whitespace_and_latex_equivalence_still_need_review(self):
        for replacement in ['x - 1', 'x+(-1)', '{x}-1']:
            with self.subTest(replacement=replacement):
                a, b = self.pair()
                b['questions'][0]['content'][0]['runs'][1]['latex'] = replacement
                self.assert_dispute(a, b, 'q1', 'content_mismatch')
        a, b = self.pair()
        b['questions'][0]['content'][0]['runs'][0]['text'] += ' '
        self.assert_dispute(a, b, 'q1', 'content_mismatch')

    def test_omission_and_reordering_block_agreement(self):
        a, b = self.pair()
        b['questions'].pop()
        result = self.assert_dispute(a, b, 'q2', 'missing_in_b')
        self.assertIsNone(result['disputes'][0]['b'])
        self.assertEqual(result['matching_ids'], ['q1'])
        a, b = self.pair()
        b['questions'].reverse()
        self.assert_dispute(a, b, 'q1', 'question_order_mismatch')
        a, b = self.pair()
        b['questions'][0]['content'].reverse()
        self.assert_dispute(a, b, 'q1', 'content_mismatch')
        a, b = self.pair()
        b['questions'][0]['content'][1]['rows'][0].reverse()
        self.assert_dispute(a, b, 'q1', 'content_mismatch')

    def test_column_and_figure_identity_or_count_need_resolution(self):
        a, b = self.pair()
        b['questions'][0]['column'] = 'right'
        self.assert_dispute(a, b, 'q1', 'column_mismatch')
        a, b = self.pair()
        b['questions'][1]['content'][1]['figure_ref'] = 'q2-figure-2'
        result = self.assert_dispute(a, b, 'q2', 'figure_refs_mismatch')
        self.assertEqual(result['figure_ids'], ['q2-figure-1', 'q2-figure-2'])
        a, b = self.pair()
        b['questions'][1]['content'].pop()
        self.assert_dispute(a, b, 'q2', 'figure_refs_mismatch')

    def test_uncertainty_incomplete_or_issues_always_block(self):
        a, b = self.pair()
        a['questions'][0]['uncertain'] = True
        self.assert_dispute(a, b, 'q1', 'uncertain_a')
        for label in ['a', 'b']:
            for field, value in [('complete', False), ('issues', ['문항 번호 판독 불명'])]:
                with self.subTest(label=label, field=field):
                    a, b = self.pair()
                    (a if label == 'a' else b)[field] = value
                    result = compare_readings(a, b)
                    self.assertEqual(result['status'], 'needs_resolution')
                    self.assertTrue(result['page_issues'])

    def test_same_worker_and_cross_source_or_page_rejected(self):
        for key, value in [('worker_id', 'a'), ('page', 2), ('source_sha256', 'b' * 64)]:
            with self.subTest(key=key):
                a, b = self.pair()
                b[key] = value
                with self.assertRaises(ValueError):
                    compare_readings(a, b)

    def test_explicit_expected_binding_is_enforced(self):
        for args in [(2, 'a', 'a' * 64), (1, 'b', 'a' * 64), (1, 'a', 'b' * 64)]:
            with self.subTest(args=args):
                with self.assertRaises(ValueError):
                    validate_reading(reading(), *args)

    def test_metadata_geometry_crop_and_direct_figure_rejected(self):
        for location, extra in [
            ('root', {'metadata': {}}), ('root', {'crop_path': 'image.png'}),
            ('question', {'bbox_mm': [1, 2, 3, 4]}),
            ('block', {'id': 'block-id'}), ('block', {'figure': {'path': 'made.png'}}),
            ('block', {'crop_path': 'crop.png'}), ('block', {'tikz': 'code'})]:
            with self.subTest(location=location, extra=extra):
                a = reading()
                target = a if location == 'root' else a['questions'][0] if location == 'question' else a['questions'][0]['content'][0]
                target.update(extra)
                with self.assertRaises(ValueError):
                    validate_reading(a, 1, 'a', 'a' * 64)

    def test_duplicate_ids_and_invalid_figure_anchors_rejected(self):
        for modification in ['question', 'reference', 'path', 'nonempty_runs']:
            with self.subTest(modification=modification):
                a = reading()
                if modification == 'question':
                    a['questions'][1]['id'] = 'q1'
                elif modification == 'reference':
                    a['questions'][1]['content'].append(deepcopy(a['questions'][1]['content'][1]))
                elif modification == 'path':
                    a['questions'][1]['content'][1]['figure_ref'] = '../figure.png'
                else:
                    a['questions'][1]['content'][1]['runs'] = [{'kind': 'text', 'text': 'figure'}]
                with self.assertRaises(ValueError):
                    validate_reading(a, 1, 'a', 'a' * 64)

    def test_invalid_types_cannot_fake_complete_or_safe_content(self):
        for key, value in [('complete', 1), ('page', True), ('issues', 'none'),
                           ('questions', []), ('source_sha256', 'x' * 64)]:
            with self.subTest(key=key):
                a = reading()
                a[key] = value
                with self.assertRaises(ValueError):
                    compare_readings(a, reading('b'))
        a = reading()
        a['questions'][0]['content'][0]['font_pt'] = float('nan')
        with self.assertRaises(ValueError):
            validate_reading(a, 1, 'a', 'a' * 64)

    def test_malformed_nested_types_raise_contract_errors(self):
        for key, value in [('kind', []), ('align', []), ('runs', {}), ('font_pt', True)]:
            with self.subTest(key=key):
                a = reading()
                a['questions'][0]['content'][0][key] = value
                with self.assertRaises(ValueError):
                    validate_reading(a, 1, 'a', 'a' * 64)
        a = reading()
        a['questions'][0]['content'][0]['runs'][0]['kind'] = []
        with self.assertRaises(ValueError):
            validate_reading(a, 1, 'a', 'a' * 64)

    def test_literal_math_and_multiline_text_fail_before_approval(self):
        for text in ['값은 $x$', r'값은 \frac{1}{2}', '첫째\n둘째', '첫째\t둘째']:
            for target in ['text', 'title']:
                with self.subTest(text=text, target=target):
                    a = reading()
                    if target == 'text':
                        a['questions'][0]['content'][0]['runs'][0]['text'] = text
                    else:
                        a['questions'][1]['content'][0]['title'] = text
                    with self.assertRaises(ValueError):
                        validate_reading(a, 1, 'a', 'a' * 64)

    def test_negative_styles_fail_under_existing_numeric_contract(self):
        for field in ['before_mm', 'after_mm', 'left_mm', 'right_mm', 'font_pt', 'line_spacing_pct']:
            with self.subTest(field=field):
                a = reading()
                a['questions'][0]['content'][0][field] = -1
                with self.assertRaises(ValueError):
                    validate_reading(a, 1, 'a', 'a' * 64)
        for field, value in [('padding_mm', [-1, 0, 0, 0]), ('stroke_mm', -1)]:
            with self.subTest(field=field):
                a = reading()
                a['questions'][1]['content'][0][field] = value
                with self.assertRaises(ValueError):
                    validate_reading(a, 1, 'a', 'a' * 64)
        # Existing contract permits zero spacing/padding and empty box titles.
        a = reading()
        a['questions'][0]['content'][0]['after_mm'] = 0
        a['questions'][1]['content'][0]['padding_mm'] = [0, 0, 0, 0]
        validate_reading(a, 1, 'a', 'a' * 64)


if __name__ == '__main__':
    unittest.main()
