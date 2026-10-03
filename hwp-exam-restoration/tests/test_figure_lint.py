"""v2.7.8: computed incidence, the exam arrow glyph and a label list checked by the engine."""
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / 'scripts'))
import restoration_single as single
from restoration_figure_lint import circle_point_warnings, drawn_labels, declared_labels, label_warnings

# The q20 figure observed in the v2.7.6 run: P and Q typed by eye near the circle (8.4,1) r=1.
HAND_PLACED = r"""\begin{tikzpicture}
\draw (8.4,1) circle (1); \draw[dotted] (6.6,0) -- (8.6,2);
\coordinate (Pp) at (8.35,1.7); \coordinate (Qq) at (7.9,1.05);
\node[above right] at (Pp) {$P$}; \node[above right] at (Qq) {$Q$};
\end{tikzpicture}"""


class CirclePointTests(unittest.TestCase):
    def test_hand_placed_point_near_a_circle_is_flagged(self):
        [warning] = circle_point_warnings(HAND_PLACED)
        self.assertIn('(Pp) is 0.30 off the circle', warning)

    def test_points_on_the_circle_or_its_centre_are_not_flagged(self):
        tex = r"""\coordinate (O) at (0,0); \coordinate (P) at (70:2); \coordinate (A) at (-2,0);
        \draw (O) circle (2); \fill (O) circle (1.2pt);"""
        self.assertEqual(circle_point_warnings(tex), [])

    def test_unlabelled_construction_points_are_not_flagged(self):
        # v279 runs: the fold meets the top edge at (2.5,2), next to a circle tangent to that edge.
        tex = r"""\draw (3,1) circle (1); \coordinate (TOP) at (2.5,2); \draw (0,2) -- (4,2);
        \draw[dotted] (1.5,0) -- (TOP);"""
        self.assertEqual(circle_point_warnings(tex), [])
        self.assertEqual(len(circle_point_warnings(tex + r' \node[above] at (TOP) {$P$};')), 1)

    def test_points_well_inside_are_not_flagged(self):
        tex = r"\coordinate (A) at (0,1); \draw (0,0) circle[radius=2];"
        self.assertEqual(circle_point_warnings(tex), [])

    def test_small_dot_marks_are_not_curves(self):
        tex = r"\coordinate (A) at (0.1,0); \fill (0,0) circle (0.05);"
        self.assertEqual(circle_point_warnings(tex), [])


class LabelTests(unittest.TestCase):
    def test_drawn_labels_cover_nodes_and_exam_helpers(self):
        tex = (r"\node[below] at (0,0) {$A$}; \ExamLabel[above]{B}{$B$}; \ExamLengthArc{A}{B}{$8$};"
               r"\node at (1,1) {\small $60^\circ$}; \draw (0,0) -- node[midway] {$x$} (1,0);")
        self.assertEqual(drawn_labels(tex), ['60', '8', 'A', 'B', 'x'])

    def test_degree_written_as_deg_in_the_header(self):
        tex = ('% width_mm=50 labels=A,40deg\n' r'\begin{tikzpicture}\node at (0,0) {$A$};\node at (1,0) {$40^\circ$};\end{tikzpicture}')
        self.assertEqual(label_warnings(tex), [])

    def test_centres_of_neighbouring_circles_are_not_points_on_them(self):
        tex = (r'\begin{tikzpicture}\coordinate (O) at (0,0);\coordinate (Op) at (2.3,0);\draw (O) circle (1.2);\draw (Op) circle (1.4);'
               r'\node at (O) {$O$};\node at (Op) {$O^\prime$};\end{tikzpicture}')
        self.assertEqual(circle_point_warnings(tex), [])

    def test_circle_warnings_are_capped_and_not_repeated(self):
        points = ''.join(rf'\coordinate (P{i}) at ({i * 20}:2.2);\node at (P{i}) {{$P_{i}$}};' for i in range(6))
        tex = r'\begin{tikzpicture}\draw (0,0) circle (2);\draw (0,0) circle (2);' + points + r'\end{tikzpicture}'
        found = circle_point_warnings(tex)
        self.assertEqual(len(found), 3); self.assertEqual(len(set(found)), 3)

    def test_degree_signs_and_arrow_connectors_are_not_mismatches(self):
        # v278-muse-1: header "60" against a drawn 60°, and a drawn $\Rightarrow$ between panels.
        tex = '% labels=O,60\n' + r"\node at (0,0) {$O$}; \node at (1,1) {\small $60^\circ$}; \node at (2,0) {$\Rightarrow$};"
        self.assertEqual(label_warnings(tex), [])

    def test_header_list_is_compared_with_drawn_labels(self):
        tex = '% width_mm=50 labels=P,Q,[그림2]\n' + HAND_PLACED
        self.assertEqual(declared_labels(tex), ['P', 'Q', '[그림2]'])
        self.assertEqual(label_warnings(tex), ['labels listed from the source but not drawn: [그림2]'])

    def test_no_header_means_no_label_check(self):
        self.assertEqual(label_warnings(HAND_PLACED), [])


class LabelPositionTests(unittest.TestCase):
    def test_table_in_a_node_is_read_cell_by_cell(self):
        tex = ('% width_mm=70 labels=요일,월,화,가게 A,-1,0,a\n'
               r'\begin{tikzpicture}\node at (0,0) {\begin{tabular}{|c|c|c|}\hline 요일 & 월 & 화 \\ \hline 가게 A & -1 & $a$ \\ \hline\end{tabular}};\end{tikzpicture}')
        self.assertEqual(label_warnings(tex), ['labels listed from the source but not drawn: 0'])

    def test_label_at_a_computed_position_counts_as_drawn(self):
        # 광남중 q12: the position nests parentheses, and the label was reported as not drawn.
        tex = ('% width_mm=50 labels=A,47°,√10\n\\begin{tikzpicture}\\coordinate (A) at (0,0);\\node[left] at (A) {$A$};'
               '\\node[above right] at ($(A)+(0.55,0.25)$) {$47^\\circ$};'
               '\\node at ($(A)!0.5!(1,1)$) {$\\sqrt{10}$};\\end{tikzpicture}')
        self.assertEqual(drawn_labels(tex), ['47', 'A', '√10'])
        self.assertEqual(label_warnings(tex), [])


class WrapperTests(unittest.TestCase):
    def test_intersections_library_only_when_used(self):
        plain = single.diagram_document(r'\begin{tikzpicture}\draw (0,0)--(1,1);\end{tikzpicture}')
        self.assertIn(r'\usetikzlibrary{calc,arrows.meta,angles,quotes}', plain)  # cached renders keep their bytes
        named = single.diagram_document(r'\begin{tikzpicture}\draw[name path=c] (0,0) circle (1);\end{tikzpicture}')
        self.assertIn('intersections', named)

    def test_exam_implies_pulls_in_the_helpers(self):
        doc = single.diagram_document(r'\begin{tikzpicture}\ExamImplies{1,0}\end{tikzpicture}')
        self.assertIn(r'\newcommand{\ExamImplies}', doc)
        self.assertIn('shapes.arrows', doc)


class MissingGlyphTests(unittest.TestCase):
    def test_error_names_the_glyph_and_the_replacement(self):
        spec = importlib.util.spec_from_file_location('tikz_render_under_test', SKILL / 'runtime/tikz_render.py')
        tr = importlib.util.module_from_spec(spec); spec.loader.exec_module(tr)
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / 'in.tex'; source.write_text(r'\begin{tikzpicture}\end{tikzpicture}', encoding='utf-8')

            def compile_tex(command, root):
                (Path(root) / 'diagram.pdf').write_bytes(b'%PDF')
                return 0, 'Missing character: There is no ⇨ in font [lmroman10-regular]!'.encode()
            with patch.object(tr, 'compile_tex', compile_tex), patch.object(tr, 'engine_path', return_value=Path(d)):
                with self.assertRaisesRegex(ValueError, r'tikz_missing_character: ⇨ .*ExamImplies'):
                    tr.render(source, Path(d) / 'out')

    def test_compile_failure_quotes_the_error_and_hints_missing_intersections(self):
        # v278 runs: 1-4 renders each failed on paths that never crossed, and the producer had to open the log.
        spec = importlib.util.spec_from_file_location('tikz_render_under_test3', SKILL / 'runtime/tikz_render.py')
        tr = importlib.util.module_from_spec(spec); spec.loader.exec_module(tr)
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / 'in.tex'; source.write_text(r'\begin{tikzpicture}\end{tikzpicture}', encoding='utf-8')
            log = "! Package pgf Error: No shape named `intersection-1' is known.\nl.22 ...f=circ and fold, by={Q,P}]"
            with patch.object(tr, 'compile_tex', return_value=(1, log.encode())), patch.object(tr, 'engine_path', return_value=Path(d)):
                with self.assertRaises(ValueError) as caught:
                    tr.render(source, Path(d) / 'out')
        message = str(caught.exception)
        self.assertIn("No shape named `intersection-1'", message)
        self.assertIn('l.22', message)
        self.assertIn('extend the construction line', message)

    def test_nullfont_means_stray_text_not_a_missing_glyph(self):
        # v278-muse-1: a doubled ';' on a path was reported as a font problem.
        spec = importlib.util.spec_from_file_location('tikz_render_under_test2', SKILL / 'runtime/tikz_render.py')
        tr = importlib.util.module_from_spec(spec); spec.loader.exec_module(tr)
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / 'in.tex'; source.write_text(r'\begin{tikzpicture}\end{tikzpicture}', encoding='utf-8')

            def compile_tex(command, root):
                (Path(root) / 'diagram.pdf').write_bytes(b'%PDF')
                return 0, b'Missing character: There is no ; in font nullfont!'
            with patch.object(tr, 'compile_tex', compile_tex), patch.object(tr, 'engine_path', return_value=Path(d)):
                with self.assertRaises(ValueError) as caught:
                    tr.render(source, Path(d) / 'out')
        self.assertIn('stray text ; outside a node', str(caught.exception))
        self.assertNotIn('ExamImplies', str(caught.exception))


class TypedSpacingTests(unittest.TestCase):
    """Rejected in v276-muse-1 and v278-muse-1: (3,\\ 1). The converter now spaces commas and dx itself."""
    def script(self, latex):
        from restoration_compiler import _studio_equation
        return _studio_equation(latex, 'e1')['selected_script']

    def test_space_after_a_comma_is_redundant(self):
        self.assertEqual(self.script(r'(3,\ 1)'), self.script(r'(3, 1)'))
        self.assertEqual(self.script(r'(1,\,2)'), self.script(r'(1,2)'))

    def test_thin_space_before_the_dx_of_an_integral_is_redundant(self):
        self.assertEqual(self.script(r'\int_0^1 x\,dx'), self.script(r'\int_0^1 x dx'))

    def test_other_typed_spacing_is_still_rejected(self):
        from runtime_paths import equation_compiler
        equation_compiler()  # puts the bundled runtime on the path; this test must also pass when run alone
        from restoration_equations import EquationError
        for latex in (r'a\,b', r'x\,dx'):
            with self.subTest(latex=latex), self.assertRaises(EquationError):
                self.script(latex)


class StackLoadTests(unittest.TestCase):
    """Observed in v277-muse-2: [그림2] split into two stacked figures pushed page 4 onto an extra page."""
    def setUp(self):
        import test_single_review_workflow as workflow
        self.case = workflow.SingleReviewTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.root = self.case.root
        self.value = {'questions': [
            {'id': 'q20', 'column': 'left', 'content': [{'kind': 'paragraph', 'runs': [{'kind': 'text', 'text': '20. 종이'}]}]},
            {'id': 'q19', 'column': 'left', 'content': [{'kind': 'paragraph', 'runs': [{'kind': 'text', 'text': '19. 원'}]}]}]}

    def item(self, qid, width, size):
        from PIL import Image
        path = Path(self.case.temp.name) / f'{qid}-{width}-{size[0]}x{size[1]}.png'
        Image.new('RGB', size, 'white').save(path)
        return {'question_id': qid, 'width_mm': width, 'png': str(path)}

    def test_share_of_the_slot_uses_render_aspect_and_equal_slots(self):
        height = single.job._manifest(self.root)['pages'][0]['height_mm']
        rows = single.page_figure_load(self.root, 1, self.value, [self.item('q20', 50, (500, 600)), self.item('q20', 45, (450, 300))])
        [row] = rows
        self.assertEqual(row['label'], '20번')
        self.assertEqual(row['figures_mm'], round(50 * 1.2 + 45 * 300 / 450, 1))
        self.assertEqual(row['slot_mm'], round(height * .9 / 2, 1))

    def test_page_count_failure_names_the_heaviest_question(self):
        import json
        receipt = Path(self.case.temp.name) / 'native.json'
        receipt.write_text(json.dumps({'status': 'failed', 'error': 'native_page_count_differs_from_source'}), encoding='utf-8')
        with patch.object(single, 'figure_load', return_value=[{'page': 4, 'label': '20번', 'share': 0.73}]):
            result = single.collect_native(self.root, {'native_run': {'receipt': str(receipt), 'returncode': 2}})
        self.assertEqual(result['message'], 'content_exceeds_source_page')
        self.assertEqual(result['figure_heavy_questions'][0]['label'], '20번')
        self.assertIn('ONE figure', result['next_action'])


if __name__ == '__main__':
    unittest.main()
