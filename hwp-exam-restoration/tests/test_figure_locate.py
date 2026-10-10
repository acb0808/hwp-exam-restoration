"""Source crop of a figure: its render found on the page, and the producer's box where it is not."""
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import restoration_figure_locate as locator
import restoration_single as single

HAS_NUMPY = importlib.util.find_spec('numpy') is not None
FIGURE = (420, 520, 300, 240)   # left, top, width, height of the figure drawn on the page


def figure(draw, x, y, k, width):
    """A triangle in a circle with a chord, `k` page pixels per unit."""
    draw.ellipse((x + 30 * k, y, x + 270 * k, y + 240 * k), outline=0, width=width)
    draw.polygon([(x + 150 * k, y + 2 * k), (x + 50 * k, y + 190 * k), (x + 250 * k, y + 190 * k)], outline=0, width=width)
    draw.line((x, y + 120 * k, x + 300 * k, y + 120 * k), fill=0, width=width)


def page():
    im = Image.new('L', (1200, 1600), 255); draw = ImageDraw.Draw(im)
    for row in range(6):   # question text above and choices below, as on a scan
        draw.rectangle((380, 380 + row * 22, 900, 388 + row * 22), fill=60)
        draw.rectangle((380, 840 + row * 26, 520, 850 + row * 26), fill=60)
    figure(draw, FIGURE[0], FIGURE[1], 1, 3)
    return im.convert('RGB')


def render(transparent=False):
    im = Image.new('L', (700, 560), 255); figure(ImageDraw.Draw(im), 20, 20, 2.2, 2)
    if not transparent: return im.convert('RGB')
    out = Image.new('RGBA', im.size, (0, 0, 0, 0)); out.paste((0, 0, 0, 255), mask=im.point(lambda v: 255 if v < 128 else 0)); return out


def inside(area, box, slack=6):
    return area[0] <= box[0] + slack and area[1] <= box[1] + slack and area[2] >= box[0] + box[2] - slack and area[3] >= box[1] + box[3] - slack


@unittest.skipUnless(HAS_NUMPY, 'numpy is optional: without it the producer box is used')
class LocateTests(unittest.TestCase):
    def test_render_is_found_where_the_producer_box_cut_the_figure(self):
        full = page(); box = (300, 420, 330, 230)                       # the figure's lower half and right side are outside
        found = locator.locate(full, box, render())
        self.assertGreater(found[4], 0.7)
        for got, want in zip(found[:4], FIGURE): self.assertAlmostEqual(got, want, delta=25)   # sizes are tried in steps of a tenth
        grown = single.grown_box(full, box)
        self.assertFalse(inside(grown, FIGURE))                         # widening by 15% does not reach the whole figure
        area = locator.settled(full, box, grown, found, single.grown_sides)
        self.assertTrue(inside(area, FIGURE))
        self.assertLess((area[2] - area[0]) * (area[3] - area[1]), 1.6 * FIGURE[2] * FIGURE[3])

    def test_transparent_render_counts_as_white_paper(self):
        found = locator.locate(page(), (400, 500, 330, 280), render(transparent=True))
        for got, want in zip(found[:4], FIGURE): self.assertAlmostEqual(got, want, delta=25)   # sizes are tried in steps of a tenth

    def test_figure_absent_from_the_search_window_is_not_adopted(self):
        full = page(); box = (60, 1300, 200, 200)                       # blank paper, the figure is far away
        found = locator.locate(full, box, render())
        self.assertIsNone(locator.settled(full, box, single.grown_box(full, box), found, single.grown_sides))


class SettleTests(unittest.TestCase):
    def test_low_score_and_a_place_off_the_producer_box_keep_the_producer_box(self):
        full = page(); box = (400, 500, 330, 280); grown = single.grown_box(full, box)
        self.assertIsNone(locator.settled(full, box, grown, None, single.grown_sides))
        self.assertIsNone(locator.settled(full, box, grown, (*FIGURE, locator.MIN_SCORE - 0.01), single.grown_sides))
        self.assertIsNone(locator.settled(full, box, grown, (760, 500, 300, 240, 0.9), single.grown_sides))   # beside the box

    def test_a_side_that_cuts_ink_goes_as_far_as_the_producer_box_went(self):
        full = page(); box = (FIGURE[0] - 20, FIGURE[1] - 20, FIGURE[2] + 40, FIGURE[3] + 40); grown = single.grown_box(full, box)
        cut = (FIGURE[0], FIGURE[1], 150, FIGURE[3], 0.6)               # found box holds only the figure's left half
        area = locator.settled(full, box, grown, cut, single.grown_sides)
        self.assertGreater(area[2], 700)                                # 15% of the found box would stop near x=610
        self.assertTrue(inside(area, FIGURE))

    def test_without_numpy_nothing_is_found(self):
        with patch.dict(sys.modules, {'numpy': None}):
            self.assertIsNone(locator.locate(page(), (400, 500, 330, 280), render()))


class EngineAreaTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix='바탕 화면 '))        # the user's job folders have Hangul names
        self.page = self.folder / '쪽-0001.png'; page().save(self.page)
        self.png = self.folder / '도형.png'; render().save(self.png)
        self.box = [300, 420, 330, 230]
        self.state = {'figure_sources': {'1': {'q1-figure-1': self.box}}}
        self.items = [{'id': 'q1-figure-1', 'status': 'pending_review', 'png': str(self.png), 'render_sha256': 'a'}]

    def run_locate(self):
        with patch.object(single.shared, '_source', return_value=self.page):
            single.locate_figures(self.folder, self.state, 1, self.items)

    @unittest.skipUnless(HAS_NUMPY, 'numpy is optional')
    def test_found_box_is_kept_once_and_cut_by_every_user(self):
        self.run_locate()
        kept = self.state['figure_located']['1']['q1-figure-1']
        with Image.open(self.page) as full:
            area = single.source_area(self.state, 1, 'q1-figure-1', full, self.box)
            self.assertEqual(area, tuple(kept['area'])); self.assertTrue(inside(area, FIGURE))
            self.state['figure_sources']['1']['q1-figure-1'] = moved = [320, 440, 330, 230]   # the producer set another box
            self.assertEqual(single.source_area(self.state, 1, 'q1-figure-1', full, moved), single.grown_box(full, moved))
        self.state['figure_sources']['1']['q1-figure-1'] = self.box
        with patch.object(locator, 'locate', side_effect=AssertionError('located twice')):
            self.items[0]['render_sha256'] = 'b'; self.run_locate()       # a later render of a found figure is not searched again

    def test_without_numpy_the_producer_box_is_cut(self):
        with patch.dict(sys.modules, {'numpy': None}): self.run_locate()
        self.assertIsNone(self.state['figure_located']['1']['q1-figure-1']['area'])
        with Image.open(self.page) as full:
            self.assertEqual(single.source_area(self.state, 1, 'q1-figure-1', full, self.box), single.grown_box(full, self.box))
        calls = []
        with patch.object(locator, 'locate', side_effect=lambda *a: calls.append(1)):
            self.run_locate(); self.assertEqual(calls, [])                # same render, same box: not tried again
            self.items[0]['render_sha256'] = 'b'; self.run_locate(); self.assertEqual(calls, [1])

    def test_a_render_close_to_its_wait_leaves_the_search_to_the_next_render(self):
        import time
        late = time.monotonic() - single.wait_seconds(single.RENDER_WAIT_SECONDS) + 1
        with patch.object(single.shared, '_source', return_value=self.page):
            single.locate_figures(self.folder, self.state, 1, self.items, late)
            self.assertEqual(self.state.get('figure_located', {}), {})
            single.locate_figures(self.folder, self.state, 1, self.items, time.monotonic())
        self.assertIn('q1-figure-1', self.state['figure_located']['1'])

    def test_failed_figures_and_figures_without_a_box_are_skipped(self):
        self.items[0]['status'] = 'failed'; self.run_locate()
        self.assertEqual(self.state['figure_located']['1'], {})


class CompareSizeTests(unittest.TestCase):
    def pair(self, size, **options):
        from restoration_montage import side_by_side
        folder = Path(tempfile.mkdtemp())
        Image.new('RGB', size, 'white').save(folder / 'a.png'); render().save(folder / 'b.png')
        with Image.open(side_by_side(folder / 'a.png', folder / 'b.png', folder / 'c.png', **options)) as im: return im.size

    def test_a_close_crop_of_a_small_figure_is_shown_no_smaller_than_a_producer_box(self):
        plain = self.pair((260, 250)); grown = self.pair((260, 250), min_height=single.COMPARE_MIN_HEIGHT)
        self.assertLess(plain[1], 300); self.assertGreaterEqual(grown[1], single.COMPARE_MIN_HEIGHT)
        self.assertEqual(self.pair((500, 450), min_height=single.COMPARE_MIN_HEIGHT), self.pair((500, 450)))   # large crops keep their pixels

    def test_a_wide_crop_is_not_enlarged_past_the_sheet_width(self):
        from restoration_montage import MAX_WIDTH
        self.assertLessEqual(self.pair((900, 150), min_height=single.COMPARE_MIN_HEIGHT)[0], MAX_WIDTH)


if __name__ == '__main__':
    unittest.main()
