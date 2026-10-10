"""A dashed line of a render that the source page shows as one unbroken line."""
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import restoration_figure_source as source
import restoration_single as single

HAS_NUMPY = importlib.util.find_spec('numpy') is not None
PT = 72 / 25.4            # points per mm
ZOOM = 4                  # render pixels per point
AT = (300, 400)           # where the figure's sheet sits on the page
K = 2.0                   # page pixels per point of the sheet


def sheet(folder, radius_dashed=True, hidden=False):
    """A 60 x 50 mm figure: a trapezium, a circle, a radius O-E (dashed or solid), and with `hidden` a dashed
    edge right beside a solid one. Returns (pdf, png)."""
    doc = fitz.open(); page = doc.new_page(width=60 * PT, height=50 * PT); mm = lambda x, y: fitz.Point(x * PT, y * PT)
    for a, b in (((5, 5), (5, 45)), ((5, 45), (55, 45)), ((55, 45), (30, 5)), ((30, 5), (5, 5))):
        page.draw_line(mm(*a), mm(*b), width=0.6)
    page.draw_circle(mm(22, 28), 17 * PT, width=0.6)
    page.draw_line(mm(22, 28), mm(22, 45), width=0.6, dashes='[3 3] 0' if radius_dashed else None)
    if hidden: page.draw_line(mm(7, 5), mm(7, 45), width=0.6, dashes='[3 3] 0')
    page.insert_text(mm(23, 26), 'O', fontsize=9); page.insert_text(mm(21, 49.5), 'E', fontsize=9)
    pdf = Path(folder) / 'diagram.pdf'; doc.save(pdf)
    png = Path(folder) / 'diagram.png'; page.get_pixmap(matrix=fitz.Matrix(ZOOM, ZOOM)).save(png); doc.close()
    return pdf, png


def scan(radius='solid', stipple=False):
    """The source page: the same figure with the radius solid, dashed or absent; `stipple` shades around it."""
    im = Image.new('L', (1000, 1300), 255); d = ImageDraw.Draw(im)
    at = lambda x, y: (AT[0] + x * PT * K, AT[1] + y * PT * K)
    for a, b in (((5, 5), (5, 45)), ((5, 45), (55, 45)), ((55, 45), (30, 5)), ((30, 5), (5, 5))):
        d.line((*at(*a), *at(*b)), fill=40, width=3)
    r = 17 * PT * K; cx, cy = at(22, 28); d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=40, width=3)
    if stipple:
        for y in range(int(at(0, 28)[1]), int(at(0, 45)[1]), 4):
            for x in range(int(at(14, 0)[0]), int(at(30, 0)[0]), 2): d.line((x, y, x, y + 1), fill=40)
    if radius == 'solid': d.line((*at(22, 28), *at(22, 45)), fill=40, width=3)
    elif radius == 'dashed':
        for i in range(0, 17, 2): d.line((*at(22, 28 + i), *at(22, 28 + i + 1)), fill=40, width=3)
    d.text(at(23, 23), 'O', fill=40); d.text(at(21, 46), 'E', fill=40)
    return im.convert('RGB')


def found(png):
    """The place of the render's ink on the page, as restoration_figure_locate would return it."""
    with Image.open(png) as im: box = im.convert('L').point(lambda v: 255 if v < 140 else 0).getbbox()
    k = K / ZOOM
    return (AT[0] + box[0] * k, AT[1] + box[1] * k, (box[2] - box[0]) * k, (box[3] - box[1]) * k, 0.9)


@unittest.skipUnless(HAS_NUMPY, 'numpy')
class SolidWhereDashedTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()

    def test_a_dashed_radius_over_a_solid_source_line_is_said_with_its_ends(self):
        pdf, png = sheet(self.folder)
        [line] = source.solid_where_dashed(scan('solid'), png, pdf, found(png))
        self.assertAlmostEqual(line['mm'], 17, delta=1.5)
        [text] = source.source_line_findings(scan('solid'), png, pdf, found(png))
        self.assertTrue(text.startswith('원본은 실선: 점선으로 그린 O–E 자리를'))
        self.assertIn('측정', text)

    def test_every_solid_line_is_named_in_one_sentence(self):
        # 중학교 시험지 F q6: three dashed radii, all solid on the source. Told of two, the producer left the third.
        doc = fitz.open(); page = doc.new_page(width=60 * PT, height=50 * PT); mm = lambda x, y: fitz.Point(x * PT, y * PT)
        page.draw_circle(mm(30, 25), 20 * PT, width=0.6); ends = {'D': (30, 8), 'E': (15, 33), 'F': (45, 33)}
        for name, (x, y) in ends.items():
            page.draw_line(mm(30, 25), mm(x, y), width=0.6, dashes='[3 3] 0'); page.insert_text(mm(x + (2 if x >= 30 else -4), y + (4 if y > 25 else -1)), name, fontsize=9)
        page.insert_text(mm(31, 24), 'O', fontsize=9)
        pdf = Path(self.folder) / 'diagram.pdf'; doc.save(pdf); png = Path(self.folder) / 'diagram.png'
        page.get_pixmap(matrix=fitz.Matrix(ZOOM, ZOOM)).save(png); doc.close()
        im = Image.new('L', (1000, 1300), 255); d = ImageDraw.Draw(im); at = lambda x, y: (AT[0] + x * PT * K, AT[1] + y * PT * K)
        r = 20 * PT * K; cx, cy = at(30, 25); d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=40, width=3)
        for x, y in ends.values(): d.line((*at(30, 25), *at(x, y)), fill=40, width=3)
        [text] = source.source_line_findings(im.convert('RGB'), png, pdf, found(png))
        for name in ends: self.assertIn(f'O–{name}', text)
        self.assertIn('모두 끊기지 않은 실선', text)

    def test_quiet_when_the_source_line_is_dashed_or_absent(self):
        pdf, png = sheet(self.folder)
        self.assertEqual(source.solid_where_dashed(scan('dashed'), png, pdf, found(png)), [])
        self.assertEqual(source.solid_where_dashed(scan('none'), png, pdf, found(png)), [])

    def test_quiet_when_the_render_draws_it_solid(self):
        pdf, png = sheet(self.folder, radius_dashed=False)
        self.assertEqual(source.solid_where_dashed(scan('solid'), png, pdf, found(png)), [])

    def test_a_dashed_edge_beside_a_solid_one_is_not_read_off_its_neighbour(self):
        pdf, png = sheet(self.folder, radius_dashed=False, hidden=True)   # 2 mm beside the left side, absent in the source
        self.assertEqual(source.solid_where_dashed(scan('solid'), png, pdf, found(png)), [])

    def test_a_line_inside_stipple_is_not_judged(self):
        pdf, png = sheet(self.folder)
        self.assertEqual(source.solid_where_dashed(scan('solid', stipple=True), png, pdf, found(png)), [])

    def test_the_figure_is_found_a_little_off_and_a_little_larger(self):
        pdf, png = sheet(self.folder); x, y, w, h, score = found(png)
        self.assertEqual(len(source.solid_where_dashed(scan('solid'), png, pdf, (x + 6, y - 5, w * 1.04, h * 1.04, score))), 1)


class WithoutNumpyTests(unittest.TestCase):
    def test_nothing_is_said(self):
        folder = tempfile.mkdtemp(); pdf, png = sheet(folder)
        with patch.dict(sys.modules, {'numpy': None}):
            self.assertIsNone(source.solid_where_dashed(scan('solid'), png, pdf, found(png)))


@unittest.skipUnless(HAS_NUMPY, 'numpy')
class EngineTests(unittest.TestCase):
    def test_told_once_as_a_measurement_and_kept_as_the_note_while_it_stays(self):
        folder = Path(tempfile.mkdtemp()); pdf, png = sheet(folder); page = folder / 'page.png'; scan('solid').save(page)
        item = {'id': 'q1-figure-1', 'question_id': 'q1', 'status': 'pending_review', 'png': str(png), 'render_sha256': 'a'}
        state = {'figure_located': {'5': {'q1-figure-1': {'bbox': [0, 0, 1, 1], 'area': [0, 0, 1, 1], 'render': 'a', 'found': list(found(png)[:4])}}},
                 'geometry_notes': {'5': {'q1-figure-1': ['도형: q1 자동 측정 — 직선 점선 위의 길이: 4cm 라벨이 직선 점선 가운데에 있습니다(원본이 점선 호인지 확인)']}}}
        with patch.object(single.shared, '_source', return_value=page):
            lint = {'q1-figure-1': ['직선 점선 위의 길이: 4cm 라벨이 직선 점선 가운데에 있습니다. 원본에서 …']}
            single.source_line_slips(folder, state, 5, [item], lint)
            self.assertEqual(len(lint['q1-figure-1']), 1)                       # the question is replaced by the measurement
            self.assertTrue(lint['q1-figure-1'][0].startswith('원본은 실선: 점선으로 그린 O–E'))
            self.assertEqual(state['geometry_notes']['5']['q1-figure-1'], ['도형: q1 자동 측정 — 점선으로 그린 O–E 자리를 엔진이 원본에서 재니 끊기지 않은 실선입니다'])
            with patch.object(source, 'source_line_findings', side_effect=AssertionError('read twice')):
                lint = {}; single.source_line_slips(folder, state, 5, [item], lint)   # the same render: not read again, not told again
            self.assertEqual(lint, {})
            self.assertEqual(len(state['geometry_notes']['5']['q1-figure-1']), 1)

    def test_what_a_slow_render_left_is_measured_when_the_verdicts_come(self):
        folder = Path(tempfile.mkdtemp()); pdf, png = sheet(folder); page = folder / 'page.png'; scan('solid').save(page)
        item = {'id': 'q1-figure-1', 'question_id': 'q1', 'status': 'pending_review', 'png': str(png), 'render_sha256': 'a'}
        state = {'figure_located': {'5': {'q1-figure-1': {'bbox': [0, 0, 1, 1], 'area': [0, 0, 1, 1], 'render': 'a', 'found': list(found(png)[:4])}}}}
        with patch.object(single.shared, '_source', return_value=page):
            close = single.time.monotonic() - single.wait_seconds(single.RENDER_WAIT_SECONDS) + 1
            single.source_line_slips(folder, state, 5, [item], {}, started=close)                               # the render call: too close to its wait
            late = single.late_measurements(folder, state, 5, [item])
            self.assertTrue(late['q1-figure-1'][0].startswith('원본은 실선: 점선으로 그린 O–E'))
            self.assertEqual(single.late_measurements(folder, state, 5, [item]), {})                           # told once

    def test_a_render_close_to_its_wait_is_left_to_the_next_one(self):
        folder = Path(tempfile.mkdtemp()); pdf, png = sheet(folder); page = folder / 'page.png'; scan('solid').save(page)
        item = {'id': 'q1-figure-1', 'question_id': 'q1', 'status': 'pending_review', 'png': str(png), 'render_sha256': 'a'}
        state = {'figure_located': {'5': {'q1-figure-1': {'bbox': [0, 0, 1, 1], 'area': [0, 0, 1, 1], 'render': 'a', 'found': list(found(png)[:4])}}}}
        with patch.object(single.shared, '_source', return_value=page):
            close = single.time.monotonic() - single.wait_seconds(single.RENDER_WAIT_SECONDS) + 1
            lint = {}; single.source_line_slips(folder, state, 5, [item], lint, started=close)
            self.assertEqual(lint, {}); self.assertEqual(state['source_lines'], {})
            # Past its wait the call is spent already and the host comes back for the result: it is read.
            single.source_line_slips(folder, state, 5, [item], lint, started=single.time.monotonic() - 10_000)
            self.assertEqual(len(lint['q1-figure-1']), 1)


if __name__ == '__main__':
    unittest.main()
