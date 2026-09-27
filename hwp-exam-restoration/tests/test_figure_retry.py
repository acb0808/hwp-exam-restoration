"""Synthetic rendering verifies retry reuse, never exam or visual correctness."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import restoration_batch as batch
import restoration_job as job
import test_efficiency as fixtures


class FigureRetryTests(unittest.TestCase):
    def fixture(self, base):
        root, _, _ = fixtures.EfficiencyTests().prepared(base)
        engine = base / 'engine.exe'
        engine.write_bytes(b'synthetic engine')
        rows = []
        calls = []
        for number in (1, 2):
            tex = base / f'f{number}.tex'
            tex.write_text(r'\begin{tikzpicture}\draw (0,0)--(1,1);\end{tikzpicture}')
            rows.append({'id': f'f{number}', 'question_id': f'q{number}',
                         'source': str(tex), 'width_mm': 40})

        def render(source, output, engine=None, dpi=300):
            import fitz
            from PIL import Image
            calls.append(str(source))
            out = Path(output)
            out.mkdir()
            (out / 'diagram.tex').write_bytes(Path(source).read_bytes())
            with fitz.open() as doc:
                doc.new_page(width=144, height=72)
                doc.save(out / 'diagram.pdf')
            Image.new('RGB', (20, 10), 'white').save(out / 'diagram.png')
            (out / 'compile.log').write_bytes(b'synthetic successful render')
            result = {'schema': 'tikz-render/1', 'status': 'rendered_pending_review',
                      'renderer': 'tikz', 'exit_code': 0, 'engine': str(Path(engine).resolve()),
                      'dpi': dpi, 'visual_status': 'not_verified',
                      'command': [str(Path(engine).resolve()), '-no-shell-escape',
                                  '-interaction=nonstopmode', '-halt-on-error', 'diagram.tex']}
            for key, name in [('source', 'diagram.tex'), ('pdf', 'diagram.pdf'),
                              ('png', 'diagram.png'), ('log', 'compile.log')]:
                result[key] = {'path': str(out / name), 'sha256': job.digest(out / name)}
            job.save_json(out / 'render.json', result)
            return result

        runtime = {'render': render, 'engine_path': lambda explicit=None: Path(explicit or engine).resolve()}
        return root, engine, rows, calls, runtime

    def test_retry_renders_only_changed_source_and_never_reuses_review(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            root, engine, rows, calls, runtime = self.fixture(base)
            original = copy.deepcopy(rows)
            with patch.object(batch.runpy, 'run_path', return_value=runtime):
                first = batch.prepare_figures(root, 1, rows, base / 'batch1', engine=engine)
                review = job.load_json(first['items'][1]['review'])
                review['status'] = 'passed'
                review['checks'] = {k: 'passed' for k in review['checks']}
                job.save_json(first['items'][1]['review'], review)
                Path(rows[0]['source']).write_text(r'\begin{tikzpicture}\draw (0,0)--(2,1);\end{tikzpicture}')
                second = batch.prepare_figures(root, 1, rows, base / 'batch2', engine=engine,
                                               reuse_batch=first['batch'])
            self.assertEqual(len(calls), 3)
            self.assertEqual([i['reused'] for i in second['items']], [False, True])
            self.assertEqual(second['status'], 'pending_review')
            self.assertEqual(second['items'][1]['render_receipt'], first['items'][1]['render_receipt'])
            for item in second['items']:
                self.assertEqual(job.load_json(item['review'])['status'], 'pending')
                self.assertTrue(all(v == 'not_verified' for v in job.load_json(item['review'])['checks'].values()))
            self.assertEqual(rows, original)

    def test_engine_dpi_or_missing_conditions_force_rerender(self):
        for change in ('engine', 'dpi', 'conditions'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as d:
                base = Path(d)
                root, engine, rows, calls, runtime = self.fixture(base)
                with patch.object(batch.runpy, 'run_path', return_value=runtime):
                    first = batch.prepare_figures(root, 1, rows[:1], base / 'batch1', engine=engine)
                    dpi = 300
                    if change == 'engine': engine.write_bytes(b'updated engine')
                    if change == 'dpi': dpi = 400
                    if change == 'conditions':
                        saved = job.load_json(first['batch'])
                        saved['items'][0].pop('render_conditions', None)
                        job.save_json(first['batch'], saved)
                    second = batch.prepare_figures(root, 1, rows[:1], base / 'batch2', engine=engine,
                                                   dpi=dpi, reuse_batch=base / 'batch1')
                self.assertEqual(len(calls), 2)
                self.assertFalse(second['items'][0]['reused'])

    def test_tampered_receipt_or_artifact_is_not_reused(self):
        for filename in ('diagram.tex', 'diagram.pdf', 'diagram.png', 'compile.log', 'render.json'):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as d:
                base = Path(d)
                root, engine, rows, calls, runtime = self.fixture(base)
                with patch.object(batch.runpy, 'run_path', return_value=runtime):
                    first = batch.prepare_figures(root, 1, rows[:1], base / 'batch1', engine=engine)
                    (base / 'batch1' / 'f1' / filename).write_bytes(b'tampered')
                    second = batch.prepare_figures(root, 1, rows[:1], base / 'batch2', engine=engine,
                                                   reuse_batch=first['batch'])
                self.assertEqual(second['status'], 'failed')
                self.assertFalse(second['items'][0]['reused'])
                self.assertEqual(len(calls), 1)

    def test_wrong_binding_rejected_before_new_directory(self):
        for key, value in [('job', 'other'), ('page', 2), ('worker_id', 'other'),
                           ('assignment_id', 'other'), ('source_sha256', '0' * 64)]:
            with self.subTest(key=key), tempfile.TemporaryDirectory() as d:
                base = Path(d)
                root, engine, rows, calls, runtime = self.fixture(base)
                with patch.object(batch.runpy, 'run_path', return_value=runtime):
                    first = batch.prepare_figures(root, 1, rows[:1], base / 'batch1', engine=engine)
                    saved = job.load_json(first['batch'])
                    saved[key] = value
                    job.save_json(first['batch'], saved)
                    with self.assertRaisesRegex(ValueError, 'binding_mismatch'):
                        batch.prepare_figures(root, 1, rows[:1], base / 'batch2', engine=engine,
                                              reuse_batch=first['batch'])
                self.assertFalse((base / 'batch2').exists())

    def test_invalid_rows_and_existing_output_rejected_before_render(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            root, engine, rows, calls, runtime = self.fixture(base)
            with patch.object(batch.runpy, 'run_path', return_value=runtime):
                first = batch.prepare_figures(root, 1, rows, base / 'batch1', engine=engine)
                with self.assertRaises(ValueError):
                    batch.prepare_figures(root, 1, rows, base / 'batch1', engine=engine,
                                          reuse_batch=first['batch'])
                invalid = [rows[0], {**rows[1], 'width_mm': -1}]
                with self.assertRaises(ValueError):
                    batch.prepare_figures(root, 1, invalid, base / 'batch2', engine=engine,
                                          reuse_batch=first['batch'])
                self.assertEqual(len(calls), 2)
                self.assertFalse((base / 'batch2').exists())

    def test_external_tex_inputs_disable_reuse(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            root, engine, rows, calls, runtime = self.fixture(base)
            Path(rows[0]['source']).write_text(r'\input{external.tex}\begin{tikzpicture}\end{tikzpicture}')
            with patch.object(batch.runpy, 'run_path', return_value=runtime):
                first = batch.prepare_figures(root, 1, rows[:1], base / 'batch1', engine=engine)
                second = batch.prepare_figures(root, 1, rows[:1], base / 'batch2', engine=engine,
                                               reuse_batch=first['batch'])
            self.assertEqual(len(calls), 2)
            self.assertFalse(second['items'][0]['reused'])


if __name__ == '__main__':
    unittest.main()
