"""Fixes from the first installed v2.7.9 run (하안북중 2-2, 5 pages, opencode muse)."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import restoration_single as single


class RelativeJobPathTests(unittest.TestCase):
    """The job landed inside the installed skill's scripts folder, where an update deletes it."""
    def test_relative_job_is_refused_with_a_concrete_location(self):
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / 'exam.pdf'
            result = single.dispatch('prepare', {'job': 'job_exam', 'source': str(source), 'question_pages': [1]})
        self.assertEqual(result['status'], 'failed')
        self.assertIn('job_path_must_be_absolute', result['message'])
        self.assertIn(str(Path(d) / 'job_exam'), result['message'])
        self.assertFalse((Path.cwd() / 'job_exam').exists())

    def test_other_actions_refuse_relative_jobs_too(self):
        self.assertIn('job_path_must_be_absolute', single.dispatch('status', {'job': 'job_exam'})['message'])


class DisplayFractionTests(unittest.TestCase):
    """\\dfrac was rejected as a lossy style in this run and in v279-muse-1."""
    def test_dfrac_and_tfrac_print_as_frac(self):
        from restoration_compiler import _studio_equation
        script = lambda latex: _studio_equation(latex, 'e1')['selected_script']
        self.assertEqual(script(r'y=-\dfrac{3}{2}x'), script(r'y=-\frac{3}{2}x'))
        self.assertEqual(script(r'\tfrac{1}{2}'), script(r'\frac{1}{2}'))


class PatternLibraryTests(unittest.TestCase):
    """A hatched region (pattern=north east lines) failed to compile without the patterns library."""
    def test_patterns_library_only_when_used(self):
        hatched = single.diagram_document(r'\begin{tikzpicture}\fill[pattern=north east lines] (0,0) rectangle (1,1);\end{tikzpicture}')
        self.assertIn('patterns', hatched.split(r'\begin{document}')[0])
        plain = single.diagram_document(r'\begin{tikzpicture}\draw (0,0)--(1,1);\end{tikzpicture}')
        self.assertNotIn('patterns', plain)


class FittedFigureTests(unittest.TestCase):
    """광남중 3-2: label size ranged 0.6x-1.4x with width_mm, and extended construction paths left q18 64% blank."""
    def test_construction_paths_do_not_size_the_picture(self):
        doc = single.diagram_document(r'\begin{tikzpicture}\path[name path=a] (0,0)--(9,9);\path [draw, name path=b] (0,1)--(1,0);\path[overlay,name path=c] (0,0)--(1,1);\end{tikzpicture}')
        self.assertIn(r'\path[overlay,name path=a]', doc)
        self.assertIn(r'\path[overlay,draw, name path=b]', doc)
        self.assertEqual(doc.count('overlay'), 3)

    def test_without_width_the_document_is_unchanged(self):
        doc = single.diagram_document(r'\begin{tikzpicture}\draw (0,0)--(1,1);\end{tikzpicture}')
        self.assertIn('[tikz,border=2pt]', doc)
        self.assertNotIn('ExamFitScale', doc)

    def test_fitted_picture_is_width_mm_wide_with_body_size_labels(self):
        import importlib.util, shutil
        if not shutil.which('xelatex'): self.skipTest('no TeX engine')
        spec = importlib.util.spec_from_file_location('tr', single.shared.SKILL / 'runtime/tikz_render.py')
        tr = importlib.util.module_from_spec(spec); spec.loader.exec_module(tr)
        import fitz
        picture = r'\begin{tikzpicture}\draw (0,0) rectangle (5,2.5);\node at (2.5,1.25) {$A$};\end{tikzpicture}'
        with tempfile.TemporaryDirectory() as d:
            sizes = {}
            for width in (40, 80):
                src = Path(d) / f'{width}.tex'; src.write_text(single.diagram_document(picture, width), encoding='utf-8')
                tr.render(src, Path(d) / str(width))
                with fitz.open(Path(d) / str(width) / 'diagram.pdf') as doc:
                    page = doc[0]; sizes[width] = page.rect.width / 72 * 25.4
                    glyph = page.search_for('A')
                    self.assertTrue(glyph)
                    sizes[f'{width}-label'] = glyph[0].height
            self.assertAlmostEqual(sizes[40], 40, delta=2)
            self.assertAlmostEqual(sizes[80], 80, delta=2)
            self.assertAlmostEqual(sizes['40-label'], sizes['80-label'], delta=0.5)

    def test_shrink_is_free_unless_labels_collide(self):
        """12 units for 12cm is common; only a crowded figure is re-rendered with the FIT_MIN_SCALE stop."""
        picture = r'\begin{tikzpicture}\draw (0,0)--(12,0);\end{tikzpicture}'
        self.assertIn(f'max({single.FIT_FREE_SCALE},', single.diagram_document(picture, 30))
        self.assertIn(f'max({single.FIT_MIN_SCALE},', single.diagram_document(picture, 30, single.FIT_MIN_SCALE))

    def test_crowded_labels_are_detected(self):
        import importlib.util, shutil
        if not shutil.which('xelatex'): self.skipTest('no TeX engine')
        from restoration_figure_geometry import labels_collide
        spec = importlib.util.spec_from_file_location('tr', single.shared.SKILL / 'runtime/tikz_render.py')
        tr = importlib.util.module_from_spec(spec); spec.loader.exec_module(tr)
        picture = r'\begin{tikzpicture}\draw (0,0) rectangle (6,1);\node at (0,0) {$O$};\node at (0.6,0) {$x$};\end{tikzpicture}'
        with tempfile.TemporaryDirectory() as d:
            found = {}
            for floor in (single.FIT_FREE_SCALE, 1.0):
                src = Path(d) / f'{floor}.tex'; src.write_text(single.diagram_document(picture, 15, floor), encoding='utf-8')
                tr.render(src, Path(d) / str(floor)); found[floor] = labels_collide(Path(d) / str(floor) / 'diagram.pdf')
        self.assertEqual(found, {single.FIT_FREE_SCALE: True, 1.0: False})


if __name__ == '__main__':
    unittest.main()
