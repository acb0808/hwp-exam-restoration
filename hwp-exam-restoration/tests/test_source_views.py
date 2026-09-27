"""Source crops must preserve evidence and reject bad batches before writing."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import restoration_job as job
import restoration_prepare as prepare


class SourceViewsTests(unittest.TestCase):
    def fixture(self, root):
        from PIL import Image
        root.mkdir()
        source = root / 'original.bin'
        source.write_bytes(b'immutable original')
        page = root / 'page.png'
        image = Image.new('RGB', (20, 30))
        image.putdata([(x * 10, y * 7, (x + y) * 3)
                       for y in range(30) for x in range(20)])
        image.save(page)
        manifest = {'schema': 'restoration-job/1',
                    'source': {'path': source.name, 'sha256': job.digest(source)},
                    'pages': [{'page': 1, 'width_mm': 10, 'height_mm': 15,
                               'image': {'path': page.name, 'sha256': job.digest(page)}}],
                    'assignments': [], 'accepted': []}
        job.save_json(root / 'manifest.json', manifest)
        return image, manifest

    def test_batch_crops_exact_pixels_and_records_rounding_and_transform(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'job'
            original, manifest = self.fixture(root)
            before = {p.name: p.read_bytes() for p in root.iterdir()}
            output = root / 'views'
            result = prepare.source_views(root, 1, [
                {'id': 'q1', 'bbox_mm': [1.1, 2.2, 2.1, 3.1]},
                {'id': 'edge', 'bbox_mm': [9, 14, 1, 1]}], output)
            self.assertEqual(result['source_sha256'], manifest['source']['sha256'])
            self.assertEqual(result['source_image']['sha256'], manifest['pages'][0]['image']['sha256'])
            self.assertEqual(result['mm_per_pixel'], [0.5, 0.5])
            first = result['views'][0]
            self.assertEqual(first['bbox_px'], [2, 4, 5, 7])
            self.assertEqual(first['actual_bbox_mm'], [1, 2, 2.5, 3.5])
            self.assertEqual(first['crop_pixel_to_page_mm'], {'scale': [0.5, 0.5], 'offset': [1, 2]})
            with Image.open(first['image']['path']) as cropped:
                self.assertEqual(cropped.tobytes(), original.crop((2, 4, 7, 11)).tobytes())
                self.assertEqual(cropped.size, (5, 7))
            self.assertEqual(job.digest(first['image']['path']), first['image']['sha256'])
            self.assertEqual(result['visual_status'], 'not_verified')
            self.assertEqual(json.loads((output / 'index.json').read_text(encoding='utf8')), result)
            self.assertEqual({name: (root / name).read_bytes() for name in before}, before)

    def test_all_rows_validated_before_any_output_is_created(self):
        valid = {'id': 'ok', 'bbox_mm': [0, 0, 1, 1]}
        invalids = [[], [valid, valid], [valid, {'id': 'OK', 'bbox_mm': [0, 0, 1, 1]}],
                    [valid, {'id': '../escape', 'bbox_mm': [0, 0, 1, 1]}],
                    [valid, {'id': 'CON', 'bbox_mm': [0, 0, 1, 1]}],
                    [valid, {'id': 'bad', 'bbox_mm': [0, 0, 0, 1]}],
                    [valid, {'id': 'bad', 'bbox_mm': [0, 0, float('nan'), 1]}],
                    [valid, {'id': 'bad', 'bbox_mm': [9, 0, 2, 1]}],
                    [valid, {'id': 'bad', 'bbox_mm': [-1, 0, 1, 1]}],
                    [valid, {'id': 'bad', 'bbox_mm': [True, 0, 1, 1]}]]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'job'
            self.fixture(root)
            for rows in invalids:
                with self.subTest(rows=rows):
                    with self.assertRaises(ValueError):
                        prepare.source_views(root, 1, rows, root / 'new')
                    self.assertFalse((root / 'new').exists())

    def test_existing_or_escaping_output_and_tampered_source_rejected(self):
        rows = [{'id': 'q1', 'bbox_mm': [0, 0, 1, 1]}]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'job'
            self.fixture(root)
            (root / 'existing').mkdir()
            for output in [root / 'existing', root.parent / 'escape']:
                with self.assertRaises(ValueError):
                    prepare.source_views(root, 1, rows, output)
            with self.assertRaises(ValueError):
                prepare.source_views(root, True, rows, root / 'new')
            (root / 'page.png').write_bytes(b'tampered')
            with self.assertRaisesRegex(ValueError, 'hash_mismatch'):
                prepare.source_views(root, 1, rows, root / 'new')
            self.assertFalse((root / 'new').exists())

    def test_environment_reports_presence_without_running_engine(self):
        with tempfile.TemporaryDirectory() as temp:
            engine = Path(temp) / 'xelatex.exe'
            engine.write_bytes(b'test fixture, deliberately not executable')
            with patch('subprocess.run', side_effect=AssertionError('must not execute')):
                result = prepare.environment_info(engine)
            self.assertEqual(result['engine']['path'], str(engine.resolve()))
            self.assertEqual(result['engine']['sha256'], job.digest(engine))
            self.assertEqual(result['compile_probe'], 'not_performed')
            self.assertEqual(result['visual_status'], 'not_verified')
            self.assertTrue(Path(result['python']).is_file())
            self.assertEqual(result['renderer']['sha256'], job.digest(result['renderer']['path']))
            blocked = prepare.environment_info(Path(temp) / 'missing.exe')
            self.assertEqual(blocked['status'], 'blocked')
            self.assertIsNone(blocked['engine'])


if __name__ == '__main__':
    unittest.main()
