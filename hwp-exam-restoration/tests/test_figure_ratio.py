"""Width:height of the main shape, source crop against render (restoration_figure_ratio)."""
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import restoration_figure_ratio as ratio
import restoration_single as single


def page(size=(600, 500), paper=255): return Image.new('L', size, paper)


def box(im, x0, y0, x1, y1, ink=40, width=3):
    ImageDraw.Draw(im).rectangle((x0, y0, x1, y1), outline=ink, width=width); return im


def text_lines(im, y, rows=2, ink=40):
    """Rows of short dashes: words of a question printed above the figure."""
    d = ImageDraw.Draw(im)
    for r in range(rows):
        for x in range(20, im.width - 40, 38): d.rectangle((x, y + 26 * r, x + 22, y + 26 * r + 12), fill=ink)
    return im


class MainShapeTests(unittest.TestCase):
    def test_same_shape_at_another_size_is_no_gap(self):
        gap = ratio.ratio_gap(box(page(), 100, 100, 400, 300), box(page((900, 700)), 60, 60, 810, 560))
        self.assertAlmostEqual(gap[0], 1.0, delta=0.03)

    def test_square_source_against_three_by_four_render(self):
        gap = ratio.ratio_gap(box(page(), 100, 100, 400, 400), box(page(), 100, 60, 400, 460))
        self.assertAlmostEqual(gap[1], 1.0, delta=0.03); self.assertAlmostEqual(gap[2], 0.75, delta=0.03)

    def test_question_text_inside_the_source_box_is_not_the_figure(self):
        source = text_lines(box(page(), 150, 180, 450, 380), 20)
        self.assertAlmostEqual(ratio.ratio_gap(source, box(page(), 100, 100, 400, 300))[0], 1.0, delta=0.04)

    def test_light_pencil_is_not_ink(self):
        source = box(page(), 150, 150, 450, 350)
        ImageDraw.Draw(source).ellipse((40, 60, 590, 480), outline=180, width=4)  # a pencil loop round the figure
        self.assertAlmostEqual(ratio.ratio_gap(source, box(page(), 100, 100, 400, 300))[0], 1.0, delta=0.04)

    def test_dashed_side_stays_one_shape(self):
        source = page(); d = ImageDraw.Draw(source)
        d.line((100, 100, 400, 100), fill=40, width=3); d.line((100, 100, 100, 300), fill=40, width=3); d.line((400, 100, 400, 300), fill=40, width=3)
        for x in range(100, 400, 12): d.line((x, 300, x + 7, 300), fill=40, width=3)
        self.assertAlmostEqual(ratio.ratio_gap(source, box(page(), 100, 100, 400, 300))[0], 1.0, delta=0.04)


class UnreadableTests(unittest.TestCase):
    def test_shape_cut_by_the_source_box(self):
        self.assertIsNone(ratio.ratio_gap(box(page(), 0, 100, 400, 300), box(page(), 100, 100, 400, 400)))

    def test_photo_or_table(self):
        source = page(); ImageDraw.Draw(source).rectangle((100, 100, 400, 300), fill=60)
        self.assertIsNone(ratio.ratio_gap(source, box(page(), 100, 100, 400, 400)))

    def test_render_in_several_panels(self):
        render = page()
        for x in (30, 220, 410): box(render, x, 150, x + 150, 300)
        self.assertIsNone(ratio.ratio_gap(box(page(), 100, 100, 400, 300), render))

    def test_blank_images(self):
        self.assertIsNone(ratio.ratio_gap(page(), box(page(), 100, 100, 400, 300)))


class WarningTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def save(self, name, im):
        im.save(self.dir / name); return self.dir / name

    def test_text_names_both_ratios(self):
        found = ratio.ratio_warning(self.save('s.png', box(page(), 100, 100, 400, 400)), self.save('r.png', box(page(), 100, 60, 400, 460)))
        self.assertIn('원본 그림은 가로:세로 1.00:1', found); self.assertIn('렌더는 1:1.3', found)
        self.assertLessEqual(len(found), 120)

    def test_small_differences_pass(self):
        self.assertIsNone(ratio.ratio_warning(self.save('s.png', box(page(), 100, 100, 400, 300)), self.save('r.png', box(page(), 100, 100, 400, 330))))

    def test_transparent_render(self):
        render = Image.new('RGBA', (600, 500), (0, 0, 0, 0)); ImageDraw.Draw(render).rectangle((100, 60, 400, 460), outline=(0, 0, 0, 255), width=3)
        self.assertIsNotNone(ratio.ratio_warning(self.save('s.png', box(page(), 100, 100, 400, 400)), self.save('r.png', render)))

    def test_missing_file_is_quiet(self):
        self.assertIsNone(ratio.ratio_warning(self.dir / 'none.png', self.dir / 'none2.png'))

    def test_said_once_per_figure_and_never_a_note(self):
        crop = self.save('s.png', box(page(), 100, 100, 400, 400)); png = self.save('r.png', box(page(), 100, 60, 400, 460))
        state = {'reviews': {'3': {'status': 'passed', 'issues': []}}}; item = {'id': 'q7-figure-1', 'png': str(png)}
        first = {}; single.ratio_slip(state, 3, item, crop, first)
        self.assertEqual(len(first['q7-figure-1']), 1); self.assertTrue(first['q7-figure-1'][0].startswith('비율:'))
        again = {}; single.ratio_slip(state, 3, item, crop, again)
        self.assertEqual(again, {}); self.assertEqual(single.current_notes(state), [])


if __name__ == '__main__':
    unittest.main()
