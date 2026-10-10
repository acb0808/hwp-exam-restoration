"""Submission diagnostics retain original source positions and reject whole pages."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from restoration_answers import split_answers
from restoration_markdown import parse_reading_markdown


def validate(text, include_answers=False):
    from restoration_submission import validate_submission
    return validate_submission(text, page=1, worker_id='producer',
                               source_sha256='a' * 64, include_answers=include_answers)


def answer(label='1', value='$2$', reason='대입하여 확인했다.'):
    return f'::: answer\n번호: {label}\n정답: {value}\n근거: {reason}\n:::'


class SubmissionDiagnosticsTests(unittest.TestCase):
    def test_ambiguous_display_delimiters_do_not_invent_equations(self):
        for malformed in ('앞 $$x+1$$ 뒤','$$x+1$$ 그리고 $$y+1$$'):
            errors=self.errors('## left\n### q1\n'+malformed+'\n$x \\mid y$')
            self.assertFalse(any('latex' in e and e['line']==3 for e in errors),errors)
            self.assertTrue(any(e.get('latex')==r'x \mid y' and e['line']==4 for e in errors))

    def test_format_error_does_not_hide_independent_equation_errors(self):
        text='## left\n### q1\n집합 $U=\\{x \\mid x>0\\}$\n$$X\\ne\\emptyset, \\quad Y\\ne\\emptyset$$\n의 개수'
        errors=self.errors(text)
        self.assertTrue(any('display equation' in e['message'] for e in errors))
        self.assertTrue(any(e.get('latex')==r'U=\{x \mid x>0\}' and e['line']==3 for e in errors))
        self.assertTrue(any('\\quad' in e.get('latex','') and e['line']==4 for e in errors))

    def errors(self, text, include_answers=False):
        try:
            validate(text, include_answers)
        except ValueError as exc:
            self.assertTrue(hasattr(exc, 'errors'), str(exc))
            self.assertTrue(exc.errors)
            for error in exc.errors:
                self.assertTrue({'question_id', 'field', 'line', 'message', 'hint'} <= error.keys(), error)
                self.assertTrue(error['hint'])
            return exc.errors
        self.fail('invalid submission was accepted')

    def test_original_line_ten_is_not_line_six_after_answer_removal(self):
        text = '## left\n### q1\n첫 문항\n' + answer() + '\n### q2\n$x'
        clean, answers = split_answers(text)
        self.assertEqual(answers['q1']['label'], '1')
        with self.assertRaisesRegex(ValueError, 'Markdown line 10:'):
            parse_reading_markdown(clean, page=1, worker_id='producer', source_sha256='a' * 64)

    def test_independent_question_errors_are_returned_together(self):
        text = '## left\n### q1\n$x\n## right\n### q2\n*본문*'
        errors = self.errors(text)
        self.assertEqual([(e['question_id'], e['line']) for e in errors], [('q1', 3), ('q2', 6)])

    def test_closed_answer_blocks_collect_shape_errors_and_field_lines(self):
        text = ('## left\n### q1\n첫 문항\n' + answer(value='확인 필요') +
                '\n### q2\n둘째 문항\n' + answer(label='1', value='$3$'))
        errors = self.errors(text, True)
        self.assertEqual([(e['question_id'], e['field'], e['line']) for e in errors],
                         [('q1', 'answer', 6), ('q2', 'label', 12)])

    def test_missing_answer_fields_are_reported_for_each_closed_block(self):
        text = ('## left\n### q1\n첫 문항\n::: answer\n번호: 1\n정답: $2$\n:::'
                '\n### q2\n둘째 문항\n::: answer\n번호: 2\n근거: 검산\n:::')
        errors = self.errors(text, True)
        self.assertEqual([(e['question_id'], e['field']) for e in errors],
                         [('q1', 'reason'), ('q2', 'answer')])

    def test_body_and_answer_errors_are_collected_in_one_submission(self):
        errors = self.errors('## left\n### q1\n$x\n' + answer(value='미확정'), True)
        self.assertEqual({(e['field'], e['line']) for e in errors}, {('content', 3), ('answer', 6)})

    def test_unclosed_body_block_does_not_invent_a_later_question(self):
        text = '## left\n### q1\n::: box\n$x\n### q2\n*본문*'
        errors = self.errors(text)
        self.assertEqual(len(errors), 1)
        self.assertEqual((errors[0]['question_id'], errors[0]['line']), ('q1', 3))
        self.assertIn('unclosed', errors[0]['message'])

    def test_unclosed_answer_cannot_consume_a_later_answer_terminator(self):
        text = ('## left\n### q1\n첫 문항\n::: answer\n번호: 1\n정답: $2$\n근거: 검산'
                '\n### q2\n*본문*\n' + answer(label='2'))
        errors = self.errors(text, True)
        self.assertEqual(len(errors), 1)
        self.assertEqual((errors[0]['question_id'], errors[0]['line']), ('q1', 4))

    def test_valid_submission_preserves_reading_and_answer_rows(self):
        text = '## left\n### q1\n값 $1+1$은?\n' + answer()
        value, rows = validate(text, True)
        expected = parse_reading_markdown('## left\n### q1\n값 $1+1$은?', page=1,
                                         worker_id='producer', source_sha256='a' * 64)
        self.assertEqual(value, expected)
        self.assertEqual(rows, [{'question_id': 'q1', 'source_page': 1, 'kind': 'written',
                                 'label': '1', 'answer': '$2$', 'reason': '대입하여 확인했다.'}])
        self.assertEqual(validate('## left\n### q1\n본문')[1], [])

    def test_picture_choices_answer_is_a_choice_row(self):
        """중학교 시험지 A q2: ①~⑤ scatter plots drawn as one figure went to the 서답형 row in two runs."""
        rows = validate('## left\n### q2\n양의 상관관계를 나타내는 산점도는?\n' + answer(value='⑤'), True)[1]
        self.assertEqual(rows[0]['kind'], 'choice')

    def test_answer_equations_retain_source_line_and_body_for_more_diagnostics(self):
        text = '## left\n### q1\n본문\n' + answer(value='$2', reason='$$')
        errors = self.errors(text, True)
        self.assertEqual([(e['field'], e['line']) for e in errors], [('answer', 6), ('reason', 7)])
        self.assertIn('Markdown line 6:', errors[0]['message'])
        self.assertIn('Markdown line 7:', errors[1]['message'])
        from restoration_submission import SubmissionError
        with self.assertRaises(SubmissionError) as caught:
            validate(text, True)
        self.assertEqual(caught.exception.value['questions'][0]['id'], 'q1')

    def test_empty_question_does_not_repeat_same_diagnostic_with_different_end_line(self):
        errors = self.errors('## left\n### q1\n\n### q2\n본문')
        self.assertEqual(len(errors), 1, errors)
        self.assertEqual(errors[0]['question_id'], 'q1')

    def test_valid_page_body_is_parsed_once(self):
        from unittest.mock import patch
        import restoration_submission
        with patch.object(restoration_submission, 'parse_reading_markdown', wraps=parse_reading_markdown) as parser:
            validate('## left\n### q1\n본문\n' + answer(), True)
        self.assertEqual(parser.call_count, 1)

    def test_supplied_answers_are_validated_even_when_answer_sheet_is_disabled(self):
        errors = self.errors('## left\n### q1\n본문\n' + answer(value='$2'), False)
        self.assertEqual([(e['question_id'], e['field'], e['line']) for e in errors],
                         [('q1', 'answer', 6)])

    def test_valid_supplied_answers_keep_rows_when_answer_sheet_is_disabled(self):
        text = '## left\n### q1\n본문\n' + answer()
        self.assertEqual(validate(text, False), validate(text, True))

    def test_partial_supplied_answers_still_require_full_coverage_when_disabled(self):
        text = '## left\n### q1\n본문\n' + answer() + '\n### q2\n다른 문항'
        errors = self.errors(text, False)
        self.assertEqual([(e['question_id'], e['field'], e['line']) for e in errors],
                         [('q2', 'answer_block', 9)])

    def test_body_equation_source_map_keeps_line_ten_after_answer_block(self):
        from restoration_submission import body_equation_source_map
        text = '## left\n### q1\n첫 문항\n' + answer() + '\n### q2\n$\\unsupported{x}$'
        clean, _ = split_answers(text)
        value = parse_reading_markdown(clean, page=1, worker_id='producer', source_sha256='a' * 64)
        mapped = body_equation_source_map(text, value)['q2.content[0].runs[0]']
        self.assertEqual(mapped, {'field': 'content', 'line': 10, 'source_line': 10,
                                 'source_location': 'equation'})

    def test_body_equation_source_map_distinguishes_repeated_equations_and_nested_blocks(self):
        from restoration_submission import body_equation_source_map
        from restoration_tools import equation_entries
        text = ('## left\n### q1\n$\\bad{x}$ 그리고 $\\bad{x}$\n$\\bad{x}$\n\n'
                '::: box 보기\n$\\bad{x}$\n:::\n\n> $$\\bad{x}$$\n\n'
                '::: choices 2\n① $\\bad{x}$ | ② $\\bad{x}$\n:::')
        value, _ = validate(text)
        mapped = body_equation_source_map(text, value)
        entries = list(equation_entries(value['questions'][0], 'q1'))
        self.assertEqual([mapped[location]['line'] for location, _ in entries], [3, 3, 4, 7, 10, 13, 13])
        self.assertTrue(all(info['source_location'] == 'equation' for info in mapped.values()))

    def test_body_equation_source_map_marks_heading_fallback_instead_of_guessing(self):
        from restoration_submission import body_equation_source_map
        value, _ = validate('## left\n### q1\n$x$')
        mapped = body_equation_source_map('## left\n### q1\n$y$', value)
        self.assertEqual(mapped['q1.content[0].runs[0]'],
                         {'field': 'content', 'line': 2, 'source_line': 2,
                          'source_location': 'question_heading'})

    def test_body_equation_source_map_does_not_parse_the_body_again(self):
        from unittest.mock import patch
        import restoration_submission
        text = '## left\n### q1\n$x$'
        value, _ = validate(text)
        with patch.object(restoration_submission, 'parse_reading_markdown', side_effect=AssertionError('reparsed')):
            self.assertEqual(restoration_submission.body_equation_source_map(text, value)
                             ['q1.content[0].runs[0]']['line'], 3)


if __name__ == '__main__':
    unittest.main()
