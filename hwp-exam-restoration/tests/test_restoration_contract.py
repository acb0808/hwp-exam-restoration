import copy
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from restoration_contract import validate_page


def sample():
    return {
        'version': 1, 'source_sha256': 'a' * 64, 'page_number': 1,
        'size_mm': [210, 297], 'assignment_id': 'assignment-1', 'worker_id': 'worker-1',
        'issues': [],
        'regions': [{'id': 'left', 'bbox_mm': [10, 20, 90, 260], 'question_ids': ['q1']}],
        'questions': [{'id': 'q1', 'region_id': 'left', 'bbox_mm': [10, 20, 90, 100]}],
        'blocks': [{'id': 'b1', 'question_id': 'q1', 'kind': 'text',
                    'bbox_mm': [12, 22, 60, 5], 'text': '1. 다음 값을 구하시오.',
                    'font_pt': 10, 'font_family': '바탕'}],
    }


class ContractTests(unittest.TestCase):
    def test_valid_preserves_input(self):
        page = sample()
        before = copy.deepcopy(page)
        self.assertIsNone(validate_page(page))
        self.assertEqual(page, before)

    def test_invalid_contracts(self):
        changes = [
            lambda p: p.update(version=True),
            lambda p: p.update(page_number=1.0),
            lambda p: p.update(source_sha256='a' * 63),
            lambda p: p.update(issues=['uncertain symbol']),
            lambda p: p.update(unknown='ignored'),
            lambda p: p['regions'][0].update(question_ids=[]),
            lambda p: p['questions'][0].update(region_id='missing'),
            lambda p: p['questions'][0].update(bbox_mm=[5, 20, 90, 100]),
            lambda p: p['blocks'][0].update(bbox_mm=[12, 22, float('nan'), 5]),
            lambda p: p['blocks'][0].update(bbox_mm=[12, 22, -1, 5]),
            lambda p: p['blocks'][0].update(bbox_mm=[12, 22, 160, 5]),
            lambda p: p['blocks'][0].update(text='$x+1$'),
            lambda p: p['blocks'][0].update(text=r'\mathrm{P}'),
            lambda p: p['blocks'][0].update(text='line one\nline two'),
            lambda p: p['blocks'][0].update(font_pt=True),
            lambda p: p['blocks'].append(copy.deepcopy(p['blocks'][0])),
        ]
        for change in changes:
            with self.subTest(change=changes.index(change)):
                page = sample()
                change(page)
                with self.assertRaises(ValueError):
                    validate_page(page)

    def test_box_title_supports_original_inside_or_border(self):
        page = sample()
        box = {'id': 'box', 'question_id': 'q1', 'kind': 'box',
               'bbox_mm': [12, 40, 80, 30], 'title': '〈보 기〉',
               'title_bbox_mm': [45, 37, 15, 6], 'stroke_mm': 0.12,
               'font_pt': 10, 'font_family': '바탕'}
        page['blocks'].append(box)
        validate_page(page)
        box['title_bbox_mm'][1] = 44
        validate_page(page)
        box['title_bbox_mm'][0] = 208
        with self.assertRaises(ValueError):
            validate_page(page)

    def test_image_replacement_is_rejected(self):
        page = sample()
        image = {'id': 'image', 'question_id': 'q1', 'kind': 'image',
                 'bbox_mm': [12, 35, 40, 30], 'path': 'figures/q1.png', 'sha256': 'b' * 64,
                 'role': 'figure'}
        page['blocks'].append(image)
        validate_page(page)
        image['question_id'] = None
        with self.assertRaises(ValueError):
            validate_page(page)

    def test_example_left_three_right_two(self):
        page = json.loads((ROOT / 'examples/page-001.json').read_text(encoding='utf-8'))
        validate_page(page)
        self.assertEqual([len(r['question_ids']) for r in page['regions']], [3, 2])
        self.assertTrue(any(b['kind'] == 'equation' for b in page['blocks']))


if __name__ == '__main__':
    unittest.main()
