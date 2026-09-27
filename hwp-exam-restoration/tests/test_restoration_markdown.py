from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from restoration_markdown import parse_reading_markdown, reading_to_markdown


def parse(text):
    return parse_reading_markdown(text, page=1, worker_id='a', source_sha256='a' * 64)


class MarkdownTests(unittest.TestCase):
    def test_inline_box_is_one_editable_run_and_roundtrips_inside_box(self):
        value = parse('## left\n### q13\n13. [[box:(가)]]에 알맞은 수는?\n\n::: box\n값은 [[box:(나)]]이다.\n:::')
        outer = value['questions'][0]['content'][0]['runs']
        inner = value['questions'][0]['content'][1]['content'][0]['runs']
        self.assertEqual(outer[1], {'kind': 'boxed_text', 'text': '(가)'})
        self.assertEqual(inner[1], {'kind': 'boxed_text', 'text': '(나)'})
        self.assertEqual(parse(reading_to_markdown(value)), value)

    def test_inline_box_rejects_empty_or_unclosed_marker(self):
        for text in ('[[box:]]', '[[box:(가)]', '[[box:$x$]]'):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, 'line'):
                parse('## left\n### q13\n' + text)

    def test_inline_box_inside_math_is_rejected_before_build(self):
        for body in (r'13. $n=\text{[[box:(가)]]}$',
                     '13. 다음 식\n\n' + r'$$n=[[box:(가)]]$$'):
            with self.subTest(body=body), self.assertRaisesRegex(ValueError, 'inline box.*outside math'):
                parse('## left\n### q13\n' + body)

    def test_complete_math_boxes_choices_and_figures(self):
        value = parse(r'''## left
### q1
1. $C_{1}$의 값은? [3점]
다음 줄.

::: box 보기
ㄱ. $x\in\{a | b\}$

ㄴ. $x-1$
:::

::: choices 2
① $|x|$ | ② $\{x | x>0\}$
③ $-1$
:::

![](figure:q1-figure-1)

## right
### q2 ?
$$\frac{1}{2}$$
''')
        self.assertTrue(value['complete'])
        self.assertEqual(value['worker_id'], 'a')
        q1, q2 = value['questions']
        self.assertEqual(q1['content'][0]['runs'][1]['latex'], 'C_{1}')
        self.assertEqual(q1['content'][0]['runs'][3], {'kind': 'break'})
        self.assertEqual(len(q1['content'][2]['rows'][0]), 2)
        self.assertEqual(q1['content'][3]['figure_ref'], 'q1-figure-1')
        self.assertTrue(q2['uncertain'])
        self.assertEqual(parse(reading_to_markdown(value)), value)

    def test_literal_escaping_and_quotes(self):
        value = parse('## left\n### q1\n가격 5원, \\*문자\\*, \\|, \\\\ 경로\n\n> 조건 $x-1$\n> 다음 줄\n')
        self.assertEqual(value['questions'][0]['content'][0]['runs'][0]['text'], '가격 5원, *문자*, |, \\ 경로')
        self.assertEqual(parse(reading_to_markdown(value)), value)
        with self.assertRaisesRegex(ValueError, 'math must be an equation'):
            parse('## left\n### q1\n가격 \\$5')

    def test_incomplete_is_never_silently_completed(self):
        value = parse('<!-- incomplete: 아래쪽 잘림 -->\n')
        self.assertFalse(value['complete'])
        self.assertEqual(value['issues'], ['아래쪽 잘림'])
        self.assertEqual(parse(reading_to_markdown(value)), value)

    def test_bad_inputs_have_line_diagnostics(self):
        for body in ['$x', '$$', '<script>x</script>', '![x](a.png)', '[link](https://a)',
                     '::: unknown\nx\n:::', '::: box 보기\nx', '::: choices 2\n① x | | ③ z\n:::',
                     '::: choices 1\n① x | ② y\n:::', '```python\nx\n```', '# ignored heading',
                     '::: box\n![](figure:f1)\n:::', '*emphasis*', '_emphasis_', '| table | row |',
                     '- list', '<!-- unknown -->']:
            with self.subTest(body=body), self.assertRaisesRegex(ValueError, 'line'):
                parse('## left\n### q1\n' + body)

    def test_duplicate_ids_and_missing_column_rejected(self):
        for text in ['### q1\nx', '## left\n### q1\nx\n### q1\ny', '## left\n### q1\n']:
            with self.assertRaisesRegex(ValueError, 'line'):
                parse(text)

    def test_serializer_does_not_drop_unrepresentable_styles(self):
        value = parse('## left\n### q1\nx')
        value['questions'][0]['content'][0]['font_pt'] = 12
        with self.assertRaisesRegex(ValueError, 'presentation'):
            reading_to_markdown(value)

    def test_no_math_normalization_or_identity_from_document(self):
        value = parse('## left\n### q1\n$x - 1$ $C_1$ $C_{1}$')
        equations = [r['latex'] for r in value['questions'][0]['content'][0]['runs'] if r['kind'] == 'equation']
        self.assertEqual(equations, ['x - 1', 'C_1', 'C_{1}'])
        original = deepcopy(value)
        reading_to_markdown(value)
        self.assertEqual(value, original)

    def test_indented_quote_and_literal_choice_pipe(self):
        value = parse('## left\n### q1\n  > 조건\n\n::: choices 2\n① 세로 \\| 문자 | ② $|x|$\n:::')
        self.assertEqual(value['questions'][0]['content'][0]['kind'], 'box')
        self.assertEqual(parse(reading_to_markdown(value)), value)

    def test_serializer_detects_unrepresentable_line_breaks(self):
        value = parse('## left\n### q1\n본문')
        value['questions'][0]['content'][0]['runs'].insert(0, {'kind': 'break'})
        with self.assertRaisesRegex(ValueError, 'without changing'):
            reading_to_markdown(value)

    def test_serializer_only_merges_adjacent_text(self):
        value = parse('## left\n### q1\n본문 $x-1$')
        value['questions'][0]['content'][0]['runs'][0:1] = [
            {'kind': 'text', 'text': '본'}, {'kind': 'text', 'text': '문 '}]
        result = parse(reading_to_markdown(value))
        self.assertEqual(result['questions'][0]['content'][0]['runs'][0]['text'], '본문 ')
        self.assertEqual(result['questions'][0]['content'][0]['runs'][1]['latex'], 'x-1')


if __name__ == '__main__':
    unittest.main()
