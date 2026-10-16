"""Graph commands for producers (2026-10-14): axes, a smooth curve through points read from the source, and axis
labels set clear of the curve. Pure expansion tests plus one render."""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import restoration_graph_tools as g
import restoration_figure_lint as lint
from restoration_tikz_templates import expand_templates

DERIVATIVE = (r'\begin{tikzpicture}\ExamAxes{-1.4}{4.4}{-3.2}{2.8}'
              r'\ExamCurve[thick]{(-1,2.4) (-0.8,0) (-0.2,-2.9) (1,0) (1.8,1.4) (2.6,0) (3.3,2.1) (3.9,0) (4.1,-1.8)}'
              r'\ExamXLabel{-0.8}{$a$}\ExamXLabel{1}{$b$}\ExamXLabel{2.6}{$c$}\ExamXLabel{3.9}{$d$}\end{tikzpicture}')


class CurveTests(unittest.TestCase):
    def test_peaks_and_valleys_stay_at_the_listed_points(self):
        pts = [(-1, 2.4), (-0.8, 0), (-0.2, -2.9), (1, 0), (1.8, 1.4), (2.6, 0), (3.3, 2.1), (3.9, 0), (4.1, -1.8)]
        m = g.slopes([p[0] for p in pts], [p[1] for p in pts])
        for k in (2, 4, 5, 6): self.assertEqual(m[k], 0.0, pts[k])            # turning points are level
        for seg in g.curve_segments(pts):                                        # no overshoot between points
            ys = [g._bezier(seg, i / 50)[1] for i in range(51)]
            lo, hi = sorted((seg[0][1], seg[3][1]))
            self.assertGreaterEqual(min(ys), lo - 1e-9); self.assertLessEqual(max(ys), hi + 1e-9)

    def test_points_must_run_left_to_right(self):
        with self.assertRaisesRegex(ValueError, 'graph_curve_x_must_increase'):
            g.expand_graph_tools(r'\ExamCurve{(0,0) (2,1) (1,3)}')

    def test_expansion_is_plain_tikz(self):
        out = expand_templates(DERIVATIVE, 60)
        self.assertNotRegex(out, r'\\Exam(?:Axes|Curve|XLabel)')
        self.assertIn(r'\draw[thick] (-1,2.4) .. controls', out)
        self.assertIn(r'\coordinate (O) at (0,0);', out)


class LabelSideTests(unittest.TestCase):
    def test_labels_go_to_the_side_the_curve_leaves_free(self):
        anchors = {v: k for k, v in re.findall(r'anchor=([a-z ]+)(?:,[xy]shift=[-\d.]+mm)?,inner sep=1pt\] at \(([-\d.]+),0\)',
                                               expand_templates(DERIVATIVE, 60))}
        self.assertEqual(anchors['-0.8'], 'north east')   # the curve comes down through a: label below left
        self.assertEqual(anchors['1'], 'north west')      # going up through b: below right
        self.assertEqual(anchors['2.6'], 'north')         # a valley touching the axis at c: straight below
        self.assertEqual(anchors['3.9'], 'north east')    # coming down through d: below left

    def test_a_side_given_by_the_producer_wins(self):
        out = expand_templates(r'\ExamAxes{-1}{3}{-1}{2}\ExamXLabel[above]{2}{$2$}', 40)
        self.assertIn(r'\node[above,inner sep=1pt] at (2,0) {$2$};', out)


class AxesTests(unittest.TestCase):
    def test_axis_names_can_change_and_O_can_go(self):
        out = expand_templates(r'\ExamAxes[x=$t$,y=$x(t)$,O=]{-1}{5}{-2}{3}', 50)
        self.assertIn('{$t$}', out); self.assertIn('{$x(t)$}', out); self.assertNotIn('{$O$}', out)


class GraphLintTests(unittest.TestCase):
    def test_hand_bezier_graph_is_told_once(self):
        hand = (r'\begin{tikzpicture}\draw[->] (-1,0)--(5,0) node[below] {$x$};\draw[->] (0,-2)--(0,3) node[left] {$y$};'
                r'\draw (0,-1.8) .. controls (0.5,-0.9) and (0.7,-0.3) .. (1,0);\end{tikzpicture}')
        self.assertEqual(len(lint.graph_curve_warnings(hand)), 1)
        self.assertIn(r'\ExamCurve', lint.graph_curve_warnings(hand)[0])
        self.assertEqual(lint.graph_curve_warnings(r'\begin{tikzpicture}\draw (0,0) .. controls (1,1) .. (2,0);\end{tikzpicture}'), [])

    def test_a_hand_placed_angle_value_is_told(self):
        hand = r'\begin{tikzpicture}\draw (0,0)--(2,0);\node at (0.6,0.2) {$25^\circ$};\end{tikzpicture}'
        label = r'\begin{tikzpicture}\coordinate (L) at (1,1);\ExamLabel[below]{L}{$120^\circ$}\end{tikzpicture}'
        self.assertIn(r'\ExamAngle', lint.angle_arrow_warnings(hand)[0])
        self.assertEqual(len(lint.angle_arrow_warnings(label)), 1)
        self.assertEqual(lint.angle_arrow_warnings(r'\begin{tikzpicture}\ExamAngle{A}{B}{C}{$25^\circ$}\end{tikzpicture}'), [])


if __name__ == '__main__':
    unittest.main()
