"""Measured geometry slips on rendered figure PDFs (restoration_figure_geometry)."""
import importlib.util
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import restoration_single as single
import restoration_figure_geometry as geometry

HAS_TEX = bool(shutil.which('xelatex'))


def render(picture, width=50):
    spec = importlib.util.spec_from_file_location('tr', single.shared.SKILL / 'runtime/tikz_render.py')
    tr = importlib.util.module_from_spec(spec); spec.loader.exec_module(tr)
    d = Path(tempfile.mkdtemp())
    src = d / 'f.tex'; src.write_text(single.diagram_document(picture, width), encoding='utf-8')
    tr.render(src, d / 'out')
    return d / 'out' / 'diagram.pdf'


def kinds(picture, width=50):
    return sorted(f['kind'] for f in geometry.geometry_findings(render(picture, width)))


@unittest.skipUnless(HAS_TEX, 'no TeX engine')
class SlipTests(unittest.TestCase):
    def test_eyeballed_tangent_is_reported_and_computed_one_is_not(self):
        # 중학교 시험지 A q20 style: a line from P meant to touch the circle.
        self.assertEqual(kinds(r'\begin{tikzpicture}\draw (0,0) circle (1);\draw (-3,1.05)--(3,1.05);\end{tikzpicture}'), ['tangent'])
        self.assertEqual(kinds(r'\begin{tikzpicture}\draw (0,0) circle (1);\draw (-3,1)--(3,1);\end{tikzpicture}'), [])

    def test_clear_secants_and_chords_are_drawings(self):
        self.assertEqual(kinds(r'\begin{tikzpicture}\draw (0,0) circle (1);\draw (-3,0.5)--(3,0.5);'
                               r'\draw (0:1)--(100:1);\draw (180:1)--(2,1.2);\end{tikzpicture}'), [])

    def test_line_end_short_of_a_circle(self):
        # 고등학교 시험지 C q20 style: a fold line meant to end at Q on the circle.
        self.assertEqual(kinds(r'\begin{tikzpicture}\draw (0,0) circle (1);\draw[dotted] (-3,-2)--(-0.75,-0.55);\end{tikzpicture}'), ['endpoint'])
        self.assertEqual(kinds(r'\begin{tikzpicture}\draw (0,0) circle (1);\draw[dotted] (-3,-2)--(225:1);\end{tikzpicture}'), [])

    def test_dashed_guide_ending_on_a_side_is_fine(self):
        self.assertEqual(kinds(r'\begin{tikzpicture}\draw (0,0) rectangle (4,2);\draw (2.5,1) circle (0.9);'
                               r'\draw[dotted] (1.5,0)--(2.2,2);\end{tikzpicture}'), [])

    def test_circles_meant_to_touch(self):
        self.assertEqual(kinds(r'\begin{tikzpicture}\draw (0,0) circle (1);\draw (2.2,0) circle (1);\end{tikzpicture}'), ['circles'])
        self.assertEqual(kinds(r'\begin{tikzpicture}\draw (0,0) circle (1);\draw (2,0) circle (1);\end{tikzpicture}'), [])
        self.assertEqual(kinds(r'\begin{tikzpicture}\draw (0,0) circle (1);\draw (4,0) circle (1);\end{tikzpicture}'), [])

    def test_symbols_arrows_and_marks_are_ignored(self):
        self.assertEqual(kinds(r'\begin{tikzpicture}\draw (0,0)--(3,0)--(1.5,2)--cycle;\ExamImplies{4,1}'
                               r'\draw[->] (0.3,2.5)--(1.45,2.05);\ExamRightAngle{A}{B}{C}\end{tikzpicture}'
                               .replace(r'\ExamRightAngle{A}{B}{C}', '')), [])


@unittest.skipUnless(HAS_TEX, 'no TeX engine')
class NotesFlowTests(unittest.TestCase):
    def test_shown_once_then_kept_as_note(self):
        pdf = render(r'\begin{tikzpicture}\draw (0,0) circle (1);\draw (2.2,0) circle (1);\end{tikzpicture}')
        item = {'id': 'q20-figure-1', 'question_id': 'q20', 'status': 'rendered', 'png': str(pdf.with_name('diagram.png'))}
        state = {'reviews': {'5': {'status': 'passed', 'issues': []}}}
        first = {}; single.measured_slips(state, 5, [item], first)
        self.assertIn('q20-figure-1', first)
        again = {}; single.measured_slips(state, 5, [item], again)
        self.assertEqual(again, {})
        notes = single.current_notes(state)
        self.assertEqual(notes[0]['page'], 5)
        self.assertTrue(notes[0]['issues'][0].startswith('도형: q20 자동 측정'))
        row = single.note_row(notes[0]['issues'][0], '5쪽', {'q20': '20번'})
        self.assertEqual((row['question'], row['kind']), ('20번', '도형'))

    def test_fixed_figure_drops_its_note(self):
        bad = render(r'\begin{tikzpicture}\draw (0,0) circle (1);\draw (2.2,0) circle (1);\end{tikzpicture}')
        good = render(r'\begin{tikzpicture}\draw (0,0) circle (1);\draw (2,0) circle (1);\end{tikzpicture}')
        state = {'reviews': {'5': {'status': 'passed', 'issues': []}}}
        for pdf in (bad, good):
            single.measured_slips(state, 5, [{'id': 'f', 'question_id': 'q20', 'status': 'rendered', 'png': str(pdf.with_name('diagram.png'))}], {})
        self.assertEqual(single.current_notes(state), [])

    def test_length_printed_without_its_dashed_arc_is_told_then_kept_until_drawn(self):
        points = r'\coordinate (A) at (0,0);\coordinate (B) at (4,0);\coordinate (C) at (4,3);\draw (A)--(B)--(C)--cycle;'
        bare = r'\begin{tikzpicture}' + points + r'\node[below] at (2,0) {$4\,\mathrm{cm}$};\end{tikzpicture}'
        drawn = r'\begin{tikzpicture}' + points + r'\ExamLengthArc{A}{B}{$4\,\mathrm{cm}$}\end{tikzpicture}'
        state = {'reviews': {'5': {'status': 'passed', 'issues': []}}}

        def measure(tex):
            item = {'id': 'f', 'question_id': 'q18', 'status': 'rendered', 'png': str(render(tex).with_name('diagram.png'))}
            told = {}; single.measured_slips(state, 5, [item], told, {'f': tex}); return told
        self.assertIn(r'\ExamLengthArc', measure(bare)['f'][0])
        self.assertEqual(measure(bare), {})  # said once
        self.assertEqual(single.current_notes(state)[0]['issues'], ['도형: q18 자동 측정 — 길이 표시 호 없음: 단위가 붙은 길이 라벨(4cm)이 있는데 점선 호가 하나도 없습니다'])
        self.assertEqual(measure(drawn), {})
        self.assertEqual(single.current_notes(state), [])


if __name__ == '__main__':
    unittest.main()
