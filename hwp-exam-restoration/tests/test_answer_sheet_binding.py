"""Synthetic answer-sheet regressions; no native HWP or visual claims."""
import unittest
from pathlib import Path
from unittest.mock import patch

import test_answer_sheet as answers
import test_figure_retry as figures
import restoration_single as single
import restoration_job as job
from restoration_fit import check_native_fit


class AnswerSheetBindingTests(unittest.TestCase):
    def setUp(self):
        self.case = answers.AnswerSheetTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.root, self.call = self.case.root, self.case.call

    def test_diagram_only_revision_invalidates_answer_review_reference(self):
        folder = Path(self.case.tmp.name) / 'renderer'
        folder.mkdir()
        _, _, _, _, runtime = figures.FigureRetryTests().fixture(folder)
        markdown = answers.MD.replace(
            '1. $1+1$의 값은?', '1. $1+1$의 값은?\n\n![](figure:f1)\n')
        result = self.call('submit_reading', page=1, markdown=markdown)
        self.assertEqual(result['status'], 'ready_for_figures', result)
        source = self.root / 'workers/page-0001/f1.tex'
        source.write_text(r'\begin{tikzpicture}\draw (0,0)--(1,1);\end{tikzpicture}')
        rows = [{'id': 'f1', 'question_id': 'q1',
                 'latex_path': str(source), 'width_mm': 40}]

        def render_and_approve():
            result = self.call('render_figures', page=1, figures=rows)
            self.assertEqual(result['status'], 'pending_review', result)
            reviews = [{**r, 'status': 'passed', 'issues': [],
                        'checks': {k: 'passed' for k in r['checks']}}
                       for r in result['reviews']]
            result = self.call('review_figures', page=1,
                               batch_path=result['batch_path'], reviews=reviews)
            self.assertEqual(result['status'], 'accepted', result)

        with patch.object(single.batch.runpy, 'run_path', return_value=runtime):
            render_and_approve()
            before = job.assemble(self.root)
            source.write_text(source.read_text().replace('(1,1)', '(2,1)'))
            render_and_approve()
            after = job.assemble(self.root)
        self.assertNotEqual(before[0], after[0])
        self.assertEqual(before[-1]['answer_rows'], after[-1]['answer_rows'])
        self.assertNotEqual(before[-1]['answer_reference']['sha256'],
                            after[-1]['answer_reference']['sha256'],
                            'diagram corrections must require answer re-review')
        old=before[-1]['answer_review_questions'];new=after[-1]['answer_review_questions']
        self.assertNotEqual(old['1/q1']['revision'],new['1/q1']['revision'])
        self.assertEqual(old['1/q2']['revision'],new['1/q2']['revision'])

    def test_editable_answer_table_is_registered_for_native_fit(self):
        self.case.submit()
        output = self.root / 'answer-fit.hwpx'
        result = self.call('build', output=str(output), title='test',
                           school='test', year='2026', exam_title='test', native=False)
        self.assertEqual(result['status'], 'built', result)
        receipt = job.load_json(output.with_suffix('.build.json'))
        fit = check_native_fit(receipt, output)
        # Pre-native XML lacks line metrics. Only assert the independent table
        # registration invariant; this is not a native fit or rendering pass.
        unexpected = [issue for issue in fit['issues']
                      if issue['code'] == 'unexpected_nested_layout_container']
        self.assertEqual(unexpected, [], unexpected)


if __name__ == '__main__':
    unittest.main()
