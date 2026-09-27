"""Observed pixel boxes convert mechanically with source-bound crop origins."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import restoration_job as job
import restoration_prepare as prepare
import test_source_views


class SourceCoordinateTests(unittest.TestCase):
    def fixture(self, root):
        original, manifest = test_source_views.SourceViewsTests().fixture(root)
        folder = root / 'inputs'
        folder.mkdir()
        crop = folder / 'right.png'
        original.crop((8, 0, 20, 30)).save(crop)
        index = {'schema': 'restoration-inputs/1', 'pages': [
            {'page': 1, 'navigation_crops': [
                {'path': str(crop), 'sha256': job.digest(crop), 'pixel_box': [8, 0, 20, 30]}]}]}
        job.save_json(folder / 'index.json', index)
        return original, crop, index

    def test_page_pixels_convert_without_rounding_semantic_bounds(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'job'
            self.fixture(root)
            context = prepare.coordinate_context(root, 1)
            box = prepare.resolve_pixel_box(context, [2.2, 4.4, 4.2, 6.2], 'page')
            self.assertEqual(box['bbox_mm'], [1.1, 2.2, 2.1, 3.1])
            self.assertEqual(box['page_bbox_px'], [2.2, 4.4, 4.2, 6.2])
            self.assertEqual(box['coordinate_space_origin_px'], [0, 0])

    def test_navigation_pixels_include_origin_and_reuse_verified_frame(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'job'
            original, crop, _ = self.fixture(root)
            context = prepare.coordinate_context(root, 1)
            with patch.object(prepare, '_coordinate_frame', wraps=prepare._coordinate_frame) as lookup:
                first = prepare.resolve_pixel_box(context, [2, 4, 4, 6], str(crop))
                second = prepare.resolve_pixel_box(context, [0, 0, 12, 30], str(crop))
                self.assertEqual(lookup.call_count, 1)
            self.assertEqual(first['bbox_mm'], [5, 2, 2, 3])
            self.assertEqual(second['bbox_mm'], [4, 0, 6, 15])

    def test_mixed_batch_crops_from_original_pixels_and_reports_both_bounds(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'job'
            original, crop, _ = self.fixture(root)
            result = prepare.source_views(root, 1, [
                {'id': 'mm', 'bbox_mm': [1.1, 2.2, 2.1, 3.1]},
                {'id': 'px', 'bbox_px': [2.2, 4.4, 4.2, 6.2], 'coordinate_space': str(crop)},
                {'id': 'page', 'bbox_px': [10, 4, 4, 6], 'coordinate_space': 'page'}], root / 'views')
            first = result['views'][1]
            self.assertEqual(first['bbox_px'], [10, 4, 5, 7])
            self.assertEqual(first['requested_bbox_mm'], [5.1, 2.2, 2.1, 3.1])
            self.assertEqual(first['actual_bbox_mm'], [5, 2, 2.5, 3.5])
            self.assertEqual(first['coordinate_space_origin_px'], [8, 0])
            with Image.open(first['image']['path']) as cropped:
                self.assertEqual(cropped.tobytes(), original.crop((10, 4, 15, 11)).tobytes())
            self.assertEqual(result['visual_status'], 'not_verified')

    def test_pixel_inputs_must_be_unambiguous_finite_and_inside_reference(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'job'
            _, crop, _ = self.fixture(root)
            invalids = [
                {'id': 'x', 'bbox_px': [0, 0, 1, 1]},
                {'id': 'x', 'bbox_px': [0, 0, 1, 1], 'bbox_mm': [0, 0, 1, 1], 'coordinate_space': 'page'},
                {'id': 'x', 'bbox_mm': [0, 0, 1, 1], 'coordinate_space': 'page'},
                *[{'id': 'x', 'bbox_px': box, 'coordinate_space': str(crop)} for box in [
                    [0, 0, 13, 1], [-1, 0, 1, 1], [0, 0, 0, 1],
                    [True, 0, 1, 1], [0, 0, float('nan'), 1], [0, 0, 1, float('inf')]]],
                {'id': 'x', 'bbox_px': [0, 0, 1, 1], 'coordinate_space': 'inputs/right.png'},
                {'id': 'x', 'bbox_px': [0, 0, 1, 1], 'coordinate_space': None}]
            for row in invalids:
                with self.subTest(row=row):
                    with self.assertRaises(ValueError):
                        prepare.source_views(root, 1, [row], root / 'new')
                    self.assertFalse((root / 'new').exists())

    def test_tampered_crop_or_registered_wrong_page_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'job'
            _, crop, index = self.fixture(root)
            original_bytes = crop.read_bytes()
            crop.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'hash_mismatch'):
                prepare.resolve_pixel_box(prepare.coordinate_context(root, 1), [0, 0, 1, 1], str(crop))
            crop.write_bytes(original_bytes)
            index['pages'][0]['page'] = 2
            job.save_json(root / 'inputs/index.json', index)
            with self.assertRaisesRegex(ValueError, 'not_registered_for_page'):
                prepare.resolve_pixel_box(prepare.coordinate_context(root, 1), [0, 0, 1, 1], str(crop))

    def test_changed_origin_rejected_even_when_crop_hash_still_matches(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'job'
            _, crop, index = self.fixture(root)
            index['pages'][0]['navigation_crops'][0]['pixel_box'] = [7, 0, 19, 30]
            job.save_json(root / 'inputs/index.json', index)
            with self.assertRaisesRegex(ValueError, 'does_not_match_source_pixels'):
                prepare.resolve_pixel_box(prepare.coordinate_context(root, 1), [0, 0, 1, 1], str(crop))

    def test_unregistered_crop_outside_job_and_invalid_registry_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'job'
            original, crop, _ = self.fixture(root)
            unregistered = root / 'custom.png'
            outside = root.parent / 'outside.png'
            original.save(unregistered)
            original.save(outside)
            for image_path, message in [(unregistered, 'not_registered_for_page'),
                                        (outside, 'must_be_inside_job')]:
                with self.subTest(image_path=image_path):
                    with self.assertRaisesRegex(ValueError, message):
                        prepare.resolve_pixel_box(prepare.coordinate_context(root, 1),
                                                  [0, 0, 1, 1], str(image_path))
            for bad_pages in [None, [None], [{'page': True, 'navigation_crops': []}],
                              [{'page': 1, 'navigation_crops': None}]]:
                job.save_json(root / 'inputs/index.json',
                              {'schema': 'restoration-inputs/1', 'pages': bad_pages})
                with self.assertRaisesRegex(ValueError, 'invalid_navigation_crop_index'):
                    prepare.resolve_pixel_box(prepare.coordinate_context(root, 1),
                                              [0, 0, 1, 1], str(crop))


if __name__ == '__main__':
    unittest.main()
