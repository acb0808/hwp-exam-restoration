"""Position relations read from question text (restoration_relations). Sentences are made up for the tests."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from restoration_relations import figure_relations, key, normalise, question_stem, relations


def keys(text): return sorted(key(r) for r in relations(text))


class CircleTests(unittest.TestCase):
    def test_points_on_a_named_circle(self):
        self.assertEqual(keys(r'원 $O$ 위의 세 점 $A$, $B$, $C$에 대하여 $\angle ABC=40^\circ$이다.'),
                         ['center O', 'on_circle A', 'on_circle B', 'on_circle C'])
        self.assertEqual(relations('원 O 위의 점 P')[1]['circle'], 'O')

    def test_other_ways_of_saying_on_the_circle(self):
        self.assertEqual(keys('두 점 P, Q는 원 위의 점이다.'), ['on_circle P', 'on_circle Q'])
        self.assertEqual(keys("네 점 A, B', C', C가 한 원 위에 있다."), ['on_circle A', "on_circle B'", 'on_circle C', "on_circle C'"])
        self.assertEqual(keys('원에 내접하는 사각형 ABCD'), ['on_circle A', 'on_circle B', 'on_circle C', 'on_circle D'])
        self.assertEqual(keys(r'$\triangle ABC$가 원 O에 내접할 때'), ['center O', 'on_circle A', 'on_circle B', 'on_circle C'])
        self.assertEqual(keys('원 O의 두 현 AB, CD'), ['center O', 'on_circle A', 'on_circle B', 'on_circle C', 'on_circle D'])
        self.assertEqual(keys('두 원이 두 점 A, B에서 만난다.'), ['on_circle A', 'on_circle B'])
        self.assertEqual(keys('두 원이 점 C에서 접한다.'), ['on_circle C'])
        self.assertEqual(keys('세 점 D, E, F는 접점이다.'), ['on_circle D', 'on_circle E', 'on_circle F'])

    def test_arcs_are_not_chords(self):
        got = keys(r'원 O의 두 호 AC, BC의 중점을 각각 D, E라 하자.')
        self.assertIn('arc_midpoint D A-C', got); self.assertIn('arc_midpoint E B-C', got)
        self.assertFalse([k for k in got if k.startswith('midpoint')])
        self.assertEqual(keys('호 PQ 위의 점 C'), ['on_circle C', 'on_circle P', 'on_circle Q'])
        self.assertEqual(keys(r'$\widehat{AB}$의 길이는 원주의 $\frac{1}{6}$이다.'), ['on_circle A', 'on_circle B'])

    def test_centres(self):
        self.assertEqual(keys('점 O를 중심으로 하고 반지름의 길이가 3인 원'), ['center O'])
        self.assertEqual(keys("두 원 O, O'의 중심 사이의 거리"), ['center O', "center O'"])
        self.assertEqual(keys('중심이 점 O로 같은 두 원'), ['center O'])
        self.assertEqual(keys(r'$\triangle ABC$를 점 A를 중심으로 하여 $30^\circ$ 회전시켰다.'), [])  # a rotation, not a circle
        self.assertEqual(keys('500원 동전 C의 넓이'), [])

    def test_diameter_and_radius(self):
        self.assertEqual(keys(r'$\overline{AB}$를 지름으로 하는 반원 O'), ['center O', 'diameter A-B'])
        self.assertEqual(keys(r'$\overline{AB}$는 원 O의 지름이다.'), ['center O', 'diameter A-B'])
        # 하안북중 2021: "반지름 OC" must not read as "지름 OC"
        self.assertEqual(keys('원 O에서 반지름 OC의 길이는 5이다.'), ['center O', 'on_circle C'])

    def test_tangents(self):
        self.assertEqual(keys(r'$\overline{ED}$는 반원 O의 접선이고 점 F는 접점이다.'), ['center O', 'on_circle F', 'tangent D-E'])
        touch = next(r for r in relations(r'$\overline{ED}$는 반원 O의 접선이고 점 F는 접점이다.') if r['kind'] == 'tangent')
        self.assertEqual((touch['points'], touch['circle']), (['E', 'D', 'F'], 'O'))
        self.assertEqual(keys('점 P에서 원 O에 그은 두 접선의 접점을 각각 A, B라 하자.'), ['center O', 'tangent_from P A', 'tangent_from P B'])
        self.assertEqual(keys(r'$\overline{AD}$, $\overline{BC}$, $\overline{CD}$는 반원에 접한다.'), ['tangent A-D', 'tangent B-C', 'tangent C-D'])
        self.assertEqual(keys(r'$\overline{BC}$는 원 O에 접한다.'), ['center O', 'tangent B-C'])
        self.assertEqual(keys(r'$\overline{AB}$의 연장선과 점 T에서의 접선이 만나는 점을 P라 하자.'), ['on_extension P A-B', 'tangent P-T'])
        self.assertEqual(keys(r'$\overline{AB}$는 지름이고, $\overline{CD}$는 접선이다.'), ['diameter A-B', 'tangent C-D'])  # clauses stay apart

    def test_inscribed_circles_and_centres_of_triangles(self):
        self.assertEqual(keys(r'원 O는 $\triangle ABC$의 내접원이다.'), ['center O', 'incircle ABC'])
        self.assertEqual(keys('변 BC, CD, DA와 접하는 원'), ['tangent_side A-D', 'tangent_side B-C', 'tangent_side C-D'])
        self.assertEqual(keys('원 O는 사각형 ABCD에 내접한다.'), ['center O', 'tangent_side A-B', 'tangent_side A-D', 'tangent_side B-C', 'tangent_side C-D'])
        self.assertEqual(keys(r'점 I가 $\triangle ABC$의 내심일 때'), ['incenter I ABC'])
        self.assertEqual(keys('두 점 O, I는 각각 삼각형 ABC의 외심과 내심이다.'), ['circumcenter O ABC', 'incenter I ABC'])
        self.assertEqual(keys(r'$\angle C=90^\circ$인 직각삼각형 ABC에서 점 O는 외심이다.'), ['circumcenter O ABC', 'right_angle C A-B'])


class LineTests(unittest.TestCase):
    def test_midpoints(self):
        self.assertEqual(keys(r'$\overline{BC}$의 중점을 M이라 하자.'), ['midpoint M B-C'])
        self.assertEqual(keys(r'점 M은 $\overline{BC}$의 중점이다.'), ['midpoint M B-C'])
        self.assertEqual(keys(r'E, F는 각각 $\overline{AD}$, $\overline{CD}$의 중점이다.'), ['midpoint E A-D', 'midpoint F C-D'])
        self.assertEqual(keys(r'$\overline{BC}$와 $\overline{AC}$의 중점은 각각 D, E이다.'), ['midpoint D B-C', 'midpoint E A-C'])
        self.assertEqual(keys('삼각형 ABC는 변 AB의 중점을 지난다.'), [])  # the C of ABC is not a midpoint

    def test_feet_of_perpendiculars(self):
        self.assertEqual(keys(r'꼭짓점 A에서 $\overline{BC}$에 내린 수선의 발을 H라 하자.'), ['foot H A B-C'])
        self.assertEqual(keys(r'꼭짓점 B, C에서 $\overline{AM}$ 또는 그 연장선에 내린 수선의 발을 각각 D, E라 하자.'), ['foot D B A-M', 'foot E C A-M'])
        self.assertEqual(keys(r'꼭짓점 A에서 $\overline{BC}$, $\overline{CD}$에 내린 수선의 발을 각각 E, F라 하자.'), ['foot E A B-C', 'foot F A C-D'])
        self.assertEqual(keys('꼭짓점 A에서 면 BCD에 내린 수선의 발을 H라 하자.'), [])  # a plane, not a line

    def test_intersections(self):
        self.assertEqual(keys(r'$\overline{AD}$와 $\overline{BC}$의 교점을 E라 하자.'), ['intersection E A-D B-C'])
        self.assertEqual(keys('점 P는 두 현 AC와 BD의 교점이다.')[-1], 'on_circle D')
        self.assertIn('intersection P A-C B-D', keys('점 P는 두 현 AC와 BD의 교점이다.'))
        self.assertEqual(keys(r'$\overline{AB}$와 $\overline{CD}$의 연장선의 교점이 P이다.'), ['intersection P A-B C-D'])
        self.assertEqual(keys('평행사변형 ABCD에서 두 대각선의 교점을 O라 하자.'), ['intersection O A-C B-D', 'parallelogram ABCD'])
        self.assertEqual(keys('두 정사각형 ABCD와 OEFG에서 점 O가 사각형 ABCD의 두 대각선의 교점이다.'),
                         ['intersection O A-C B-D', 'square ABCD', 'square EFGO'])

    def test_points_on_lines(self):
        self.assertEqual(keys(r'$\overline{BC}$의 연장선 위의 점 T'), ['on_extension T B-C'])
        self.assertEqual(keys(r'$\overline{CD}$ 위의 한 점 E'), ['on_segment E C-D'])
        self.assertEqual(keys(r'점 D는 $\overline{BC}$ 위의 점이다.'), ['on_segment D B-C'])
        self.assertEqual(keys(r'두 점 P, Q는 각각 $\overline{BC}$, $\overline{AC}$ 위의 점이다.'), ['on_segment P B-C', 'on_segment Q A-C'])
        self.assertEqual(keys('두 점 E와 F는 각각 변 BC와 CD 위에 있다.'), ['on_segment E B-C', 'on_segment F C-D'])
        self.assertEqual(keys(r'$\angle A$의 이등분선과 $\overline{BC}$가 만나는 점을 D라 하자.'), ['on_line D B-C'])
        self.assertEqual(keys('세 점 B, C, D는 한 직선 위에 있다.'), ['collinear BCD'])
        self.assertEqual(keys(r'선분 AB의 수직이등분선 위의 한 점 P'), ['on_bisector P A-B'])
        # 광명중 2024: C is folded onto D; only D is on AB
        self.assertEqual(keys(r'꼭짓점 C가 $\overline{AB}$ 위의 점 D에 오도록 접었다.'), ['on_segment D A-B'])


class LengthAndShapeTests(unittest.TestCase):
    def test_equal_lengths(self):
        self.assertEqual(keys(r'$\overline{AB}=\overline{AC}$인 이등변삼각형 ABC'), ['equal_length A-B A-C'])
        self.assertEqual(keys(r'$\overline{OD}=\overline{OE}=\overline{OF}=2\text{ cm}$'), ['equal_length D-O E-O', 'equal_length E-O F-O'])
        self.assertEqual(keys(r'$\overline{AB}=6\text{ cm}$, $\angle ACB=\angle DCA$'), [])  # a size, and angles

    def test_right_angles(self):
        self.assertEqual(keys(r'$\angle B=90^\circ$인 직각삼각형 ABC'), ['right_angle B A-C'])
        self.assertEqual(keys('직각삼각형 ABC의 넓이'), ['right_triangle ABC'])
        self.assertEqual(keys(r'$\angle AGB=\angle BHC=90^\circ$'), ['right_angle G A-B', 'right_angle H B-C'])

    def test_directions(self):
        self.assertEqual(keys(r'$\overline{AB}\perp\overline{CD}$, $\overline{AD}\parallel\overline{BC}$'), ['parallel A-D B-C', 'perpendicular A-B C-D'])
        self.assertEqual(keys(r'$\overline{AD}$ // $\overline{BC}$인 사다리꼴 ABCD'), ['parallel A-D B-C'])

    def test_named_shapes(self):
        self.assertEqual(keys('정사각형 ABCD와 직사각형 EFGH, 정삼각형 PQR, 마름모 KLMN'),
                         ['equilateral PQR', 'rectangle EFGH', 'rhombus KLMN', 'square ABCD'])
        self.assertEqual(keys(r'$\square ABCD$는 정사각형이고'), ['square ABCD'])

    def test_a_goal_is_not_a_given(self):
        # 광문중 2025: both read as givens by an earlier version
        self.assertEqual(keys(r'$\square ABCD$가 평행사변형이 되도록 하는 $\angle D$의 크기는?'), [])
        self.assertEqual(keys(r'$\square PBQD$가 마름모인 이유를 설명하시오.'), [])


class StemTests(unittest.TestCase):
    QUESTION = ('q7\n7. 원 $O$ 위의 두 점 $A$, $B$에 대하여 옳은 것은? [4점]\n\n![](figure:q7-figure-1)\n\n'
                '::: box 보기\nㄱ. $\\overline{PA}=\\overline{PB}$\n\nㄴ. 점 M은 $\\overline{AB}$의 중점이다.\n:::\n\n'
                '::: choices 3\n① $\\overline{AB}=\\overline{CD}$ | ② ㄴ | ③ ㄱ, ㄴ\n:::\n\n'
                '::: answer\n번호: 7\n정답: ③\n근거: $\\overline{OA}=\\overline{OB}$이므로 …\n:::\n\n## right\n')

    def test_boxes_choices_and_answers_are_not_givens(self):
        self.assertEqual(keys(self.QUESTION), ['center O', 'on_circle A', 'on_circle B'])
        self.assertNotIn('보기', question_stem(self.QUESTION)); self.assertNotIn('right', question_stem(self.QUESTION))

    def test_each_figure_gets_its_question(self):
        reading = '# page\n\n### ' + self.QUESTION + '\n### q8\n8. 풀이 없는 문항 [3점]\n\n### q9\n9. 직사각형 ABCD [3점]\n\n![](figure:q9-figure-1)\n'
        found = figure_relations(reading)
        self.assertEqual(sorted(found), ['q7-figure-1', 'q9-figure-1'])
        self.assertEqual([key(r) for r in found['q9-figure-1']], ['rectangle ABCD'])

    def test_latex_forms_read_the_same(self):
        plain = keys('선분 AB를 지름으로 하는 원 O 위의 점 P')
        for text in (r'$\overline{AB}$를 지름으로 하는 원 $O$ 위의 점 $P$', r'$\overline{\mathrm{AB}}$를 지름으로 하는 원 $\mathrm{O}$ 위의 점 $\mathrm{P}$'):
            self.assertEqual(keys(text), plain)
        self.assertEqual(normalise(r"$O^{\prime}$, $\triangle ABC$, $\angle A=90^{\circ}$").replace(' ', ''), "O',삼각형ABC,∠A=90°")

    def test_every_relation_names_its_text(self):
        for r in relations(r'$\overline{BC}$의 중점 M, 원 O 위의 점 P'):
            self.assertTrue(r['text']); self.assertLessEqual(len(r['text']), 60)


if __name__ == '__main__':
    unittest.main()
