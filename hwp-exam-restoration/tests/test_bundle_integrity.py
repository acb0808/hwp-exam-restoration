"""Release integrity: checks must survive git line-ending conversion, and shipped records must match."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / 'scripts'))
from runtime_paths import RUNTIME, content_digest
from exam_template import load_template


class ContentDigestTests(unittest.TestCase):
    def test_text_digest_ignores_crlf_but_not_content(self):
        with tempfile.TemporaryDirectory() as d:
            lf, crlf, other = (Path(d) / n for n in ('a.py', 'b.py', 'c.py'))
            lf.write_bytes(b'x = 1\ny = 2\n'); crlf.write_bytes(b'x = 1\r\ny = 2\r\n'); other.write_bytes(b'x = 1\ny = 3\n')
            self.assertEqual(content_digest(lf), content_digest(crlf))
            self.assertNotEqual(content_digest(lf), content_digest(other))

    def test_binary_files_are_hashed_as_stored(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d) / 'a.png', Path(d) / 'b.png'
            a.write_bytes(b'\x89PNG\r\n'); b.write_bytes(b'\x89PNG\n')
            self.assertNotEqual(content_digest(a), content_digest(b))


class ShippedRecordTests(unittest.TestCase):
    def test_every_runtime_file_matches_sources_json(self):
        manifest = json.loads((RUNTIME / 'sources.json').read_text(encoding='utf-8'))
        bad = [f['path'] for f in manifest['files'] if content_digest(RUNTIME / f['path']) != f['sha256']]
        self.assertEqual(bad, [])

    def test_template_loads_after_a_crlf_checkout(self):
        with tempfile.TemporaryDirectory() as d:
            copy = Path(d) / 'grid'
            shutil.copytree(SKILL / 'assets/templates/pdf2hwp-grid', copy)
            for path in (copy / 'base').rglob('*.xml'):
                path.write_bytes(path.read_bytes().replace(b'\r\n', b'\n').replace(b'\n', b'\r\n'))
            load_template(copy)  # must not raise template_asset_hash_mismatch

    def test_shipped_templates_carry_no_local_author(self):
        import zipfile
        texts = [p.read_bytes() for p in (SKILL / 'assets/templates/pdf2hwp-grid/base/Contents/content.hpf',
                                          SKILL / 'runtime/hwp_automation/template_base/Contents/content.hpf')]
        with zipfile.ZipFile(SKILL / 'assets/templates/pdf2hwp-grid/source.hwpx') as z:
            texts.append(z.read('Contents/content.hpf'))
        for text in texts:
            self.assertIn(b'content="text">hwp-exam-restoration</opf:meta>', text)


if __name__ == '__main__':
    unittest.main()
