"""A long answer table: short written answers share rows, the rest continues on further pages, and a number
printed again in a later unit starts a new group instead of failing the build."""
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import restoration_job as job
import restoration_single as single
from restoration_answers import (ANSWER_PAGE_MM, answer_groups, answer_parts, answer_table_check, table_rows)
from restoration_batch import pages_digest, review_pack


def row(label, answer, kind='written', page=1):
    return {'question_id': 'q' + label, 'source_page': page, 'kind': kind, 'label': label, 'answer': answer, 'reason': 'r'}


def texts(part):
    return [text for items, _ in part for text, *_ in items]


def page_markdown(count, answer, numbering=None):
    """One page of `count` written questions, half in each column."""
    lines = []
    for index in range(1, count + 1):
        if index == 1: lines.append('## left')
        if index == count // 2 + 1: lines.append('## right')
        label = numbering(index) if numbering else str(index)
        lines += [f'### q{index}', f'{label}. $x$의 값을 구하시오.', '', '::: answer', f'번호: {label}',
                  f'정답: {answer(index)}', '근거: 조건에서 계산했다.', ':::', '']
    return '\n'.join(lines)


class AnswerLayoutTests(unittest.TestCase):
    def test_a_table_that_fits_keeps_its_usual_rows(self):
        rows = [row('1', '② $2$', 'choice'), row('서답형 1', '$x=3$')]
        [part] = answer_parts(rows, 160)
        self.assertEqual(part, table_rows(rows, 160))
        self.assertEqual([len(items) for items, _ in part], [1, 8, 1, 2])  # written answers keep a full-width row

    def test_many_short_written_answers_share_rows_on_one_page(self):
        # The stopped run: 8 choices and 43 written answers of 1 to 4 characters needed 685 mm in full-width rows.
        rows = [row(f'{n}-1', '③', 'choice') for n in range(1, 9)] + [row(f'{n}-2', f'${n}0^\\circ$') for n in range(1, 44)]
        self.assertGreater(sum(h for _, h in table_rows(rows, 160)), ANSWER_PAGE_MM)
        [part] = answer_parts(rows, 160)
        self.assertLessEqual(sum(h for _, h in part), ANSWER_PAGE_MM)
        printed = texts(part)
        for item in rows:
            self.assertEqual(printed.count(item['label']), 1, item['label'])

    def test_a_long_answer_keeps_its_own_row_in_source_order(self):
        rows = [row(str(n), '$12$') for n in range(1, 60)]
        rows[2] = row('3', '이등변삼각형의 두 밑각의 크기는 서로 같으므로 $\\angle B=\\angle C$이다')
        rows[5] = row('서답형 6', '$7$')  # a number too wide for the narrow number cell
        [part] = answer_parts(rows, 160)
        printed = [t for t in texts(part) if t.strip() and not t.startswith('$') and t != '서답형']
        self.assertEqual(printed[:6], ['1', '2', '3', rows[2]['answer'], '4', '5'])
        wide = [items for items, _ in part if len(items) == 2]
        self.assertEqual([items[0][0] for items in wide], ['3', '서답형 6'])

    def test_what_does_not_fit_one_page_continues_with_a_heading(self):
        rows = [row(str(n), '③', 'choice') for n in range(1, 21)] + [row(f'서술형 {n}', '풀이 과정을 모두 적은 긴 답 ' + str(n)) for n in range(1, 41)]
        parts = answer_parts(rows, 160)
        self.assertGreater(len(parts), 2)
        for part in parts:
            self.assertLessEqual(sum(h for _, h in part), ANSWER_PAGE_MM)
            self.assertEqual(len(part[0][0]), 1)             # a heading opens every page
            self.assertNotEqual(len(part[-1][0]), 1)         # and none is left alone at a page end
        self.assertEqual(parts[1][0][0][0][0], '서답형')
        printed = [t for part in parts for t in texts(part)]
        for item in rows:
            self.assertEqual(printed.count(item['label']), 1, item['label'])

    def test_a_page_inside_a_numbering_group_repeats_both_headings(self):
        long = '풀이 과정을 모두 적은 긴 답'
        rows = [row(f'서술형 {n}', long, page=page) for page in (1, 2) for n in range(1, 21)]
        parts = answer_parts(rows, 160)
        self.assertEqual([items[0][0] for items, _ in parts[0][:2]], ['원본 1쪽', '서답형'])
        self.assertEqual([items[0][0] for items, _ in parts[1][:2]], ['원본 1쪽', '서답형'])
        self.assertEqual([items[0][0] for items, _ in parts[-1][:2]], ['원본 2쪽', '서답형'])

    def test_a_page_that_starts_at_a_kind_heading_still_names_its_group(self):
        # Group title, 객관식 and 18 choice rows take 216 mm: 서답형 still fits the first page, its first row does not.
        long = '풀이 과정을 모두 적은 긴 답'
        rows = ([row(str(n), '③', 'choice', page=1) for n in range(1, 73)]
                + [row(f'서술형 {n}', long, page=1) for n in range(1, 3)]
                + [row('1', '③', 'choice', page=2)] + [row(f'서술형 {n}', long, page=2) for n in range(1, 30)])
        parts = answer_parts(rows, 160)
        heads = [[items[0][0] for items, _ in part if len(items) == 1] for part in parts]
        self.assertEqual(heads[0], ['원본 1쪽', '객관식'])
        self.assertEqual(heads[1], ['원본 1쪽', '서답형', '원본 2쪽', '객관식', '서답형'])
        self.assertEqual(heads[2], ['원본 2쪽', '서답형'])
        for part in parts:
            self.assertLessEqual(sum(h for _, h in part), ANSWER_PAGE_MM)
            self.assertNotEqual(len(part[-1][0]), 1)

    def test_an_exam_that_counts_written_questions_from_one_again_is_one_group(self):
        exam = [row(str(n), '②', 'choice', page=1) for n in range(1, 6)] + [row(str(n), '$3$', page=2) for n in range(1, 4)]
        self.assertEqual(len(answer_groups(exam)), 1)
        # A workbook mixes the two kinds, so a number printed again is the next unit whatever its kind.
        mixed = [row('1-1', '$3$', page=1), row('1-2', '②', 'choice', page=1), row('1-1', '④', 'choice', page=2), row('1-2', '$5$', page=2)]
        self.assertEqual([(title, len(group)) for title, group in answer_groups(mixed)], [('원본 1쪽', 2), ('원본 2쪽', 2)])

    def test_a_number_printed_again_starts_a_group(self):
        rows = [row('1-1', '$5$', page=1), row('1-2', '$6$', page=2), row('1-1', '$7$', page=3), row('1-2', '$8$', page=3)]
        self.assertEqual([title for title, _ in answer_groups(rows)], ['원본 1~2쪽', '원본 3쪽'])
        self.assertEqual([title for title, _ in answer_groups(rows[:2])], [None])
        heads = [items[0][0] for items, _ in table_rows(rows, 160) if len(items) == 1]
        self.assertEqual(heads, ['원본 1~2쪽', '서답형', '원본 3쪽', '서답형'])
        # 객관식 1 and 서답형 1 of an ordinary exam are different numbers, not a new unit.
        self.assertEqual(len(answer_groups([row('1', '②', 'choice'), row('1', '$3$')])), 1)


class LongAnswerSheetTests(unittest.TestCase):
    def setUp(self):
        import fitz
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'job'
        source = Path(self.tmp.name) / 'source.pdf'
        with fitz.open() as doc:
            doc.new_page(); doc.new_page()
            doc.save(source)
        self.call('prepare', source=str(source), question_pages=[1, 2], include_answers=True)
        for page in (1, 2):
            self.call('assign', page=page, worker_id=f'producer-{page}', evidence=f'Actual fixture worker response: producer-{page}')

    def call(self, action, **kwargs):
        return single.dispatch(action, {'job': str(self.root), **kwargs})

    def submit(self, answer, numbering=None):
        for page in (1, 2):
            result = self.call('submit_reading', page=page, markdown=page_markdown(12, answer, numbering))
            self.assertEqual(result['status'], 'accepted', result)

    def submit_long(self):
        """24 written answers too long to share a row, numbered 1 to 24 over the two pages: 369 mm of table."""
        for page in (1, 2):
            md = page_markdown(12, lambda n: f'풀이 과정을 모두 적은 긴 서술형 답 {n}', numbering=lambda n: str(n + 12 * (page - 1)))
            self.assertEqual(self.call('submit_reading', page=page, markdown=md)['status'], 'accepted')

    def build(self):
        result = self.call('build', output=str(self.root / 'exam.hwpx'), title='test', school='test', year='2026',
                           exam_title='test', native=False)
        self.assertEqual(result['status'], 'built', result)
        return result['output']

    def test_units_that_restart_their_numbers_build_one_grouped_table(self):
        # Both pages print 1-1 … 1-12: the build used to stop with duplicate_printed_answer_number.
        self.submit(lambda n: f'{n}cm', numbering=lambda n: f'1-{n}')  # no equation: its width is only known after Hangul
        pages = job.assemble(self.root)
        self.assertEqual([p.get('role') for p in pages], [None, None, 'answer_sheet'])
        output = self.build()
        with zipfile.ZipFile(output) as z: xml = z.read('Contents/section0.xml').decode()
        self.assertIn('원본 1쪽', xml); self.assertIn('원본 2쪽', xml)
        check = answer_table_check(output, output, pages[-1]['answer_rows'])
        self.assertTrue(check['verified'], check)

    def test_a_long_table_continues_on_a_second_answer_page(self):
        self.submit_long()
        pages = job.assemble(self.root)
        self.assertEqual([p.get('role') for p in pages], [None, None, 'answer_sheet', 'answer_sheet_more'])
        self.assertEqual([p['page_number'] for p in pages], [1, 2, 3, 4])
        self.assertEqual((pages[2]['answer_part'], pages[3]['answer_part']), ([1, 2], [2, 2]))
        self.assertNotIn('answer_reference', pages[3])
        output = self.build()
        with zipfile.ZipFile(output) as z: xml = z.read('Contents/section0.xml').decode()
        self.assertIn('정답표 (1/2)', xml); self.assertIn('정답표 (2/2)', xml)
        self.assertEqual(xml.count('colCnt="8"'), 2)
        check = answer_table_check(output, output, pages[2]['answer_rows'])
        self.assertTrue(check['verified'], check)
        self.assertEqual(check['cells'], 2 + 24 * 2)  # 서답형 heading on both pages, number and answer per row

    def test_the_further_page_is_reviewed_with_the_answer_sheet(self):
        import fitz
        self.submit_long()
        pages = job.assemble(self.root)
        pdf = self.root / 'fixture.pdf'
        with fitz.open() as doc:
            for _ in pages: doc.new_page()
            doc.save(pdf)
        native = {'status': 'rendered', 'cleanup': 'closed_owned_session', 'job': str(self.root), 'page_count': len(pages),
                  'pages_sha256': pages_digest(pages), 'artifacts': {'pdf': {'path': str(pdf), 'sha256': job.digest(pdf)}}}
        native['review_inputs'] = review_pack(self.root, native, self.root / 'fixture-review')
        self.assertEqual([p['page'] for p in native['review_inputs']['pages']], [1, 2, 3])  # page 4 has no review of its own
        receipt = self.root / 'fixture-native.json'
        job.save_json(receipt, native)
        state = job.load_json(self.root / 'mcp/state.json')
        state['native_run'] = {'receipt': str(receipt), 'returncode': 0}
        job.save_json(self.root / 'mcp/state.json', state)
        status = self.call('status')
        self.assertEqual(status['status'], 'pending_review', status)
        tasks = {t['page']: t for t in status['review_tasks']}
        self.assertEqual(sorted(tasks), [1, 2, 3])
        self.assertEqual(len(tasks[3]['more_output_images']), 1)
        report = self.root / 'report.md'
        single.write_report_skeleton(self.root, report, status['review_tasks'], job.load_json(self.root / 'mcp/state.json'))
        self.assertIn(tasks[3]['more_output_images'][0], report.read_text(encoding='utf-8'))
        rows = [{'page': n, 'status': 'passed', 'issues': []} for n in (1, 2, 3)]
        paths = ' '.join([tasks[1]['source_image'], tasks[2]['source_image'], tasks[3]['answer_reference']] + [tasks[n]['output_image'] for n in (1, 2, 3)])
        done = self.call('finish_review', reviewer_id='reviewer', reviews=rows, review_evidence='reviewer ' + paths)
        self.assertEqual(done['status'], 'complete', done)


if __name__ == '__main__':
    unittest.main()
