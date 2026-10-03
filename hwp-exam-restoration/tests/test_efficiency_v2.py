"""Batch mechanics must never stand in for an actual visual review."""
import importlib.util, json, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import restoration_job as job
import test_efficiency as fixtures
import test_tikz_handoff as handoff


class BatchTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('restoration_batch'), 'batch helper missing')
        import restoration_batch
        return restoration_batch

    def test_assignment_creates_rejected_draft_and_wrong_file_hint(self):
        with tempfile.TemporaryDirectory() as d:
            root, manifest, assignment = fixtures.EfficiencyTests().prepared(Path(d))
            packet = job.load_json(assignment['worker_input'])
            result = Path(packet['result_path'])
            self.assertTrue(result.is_file(), 'editable result draft missing')
            self.assertEqual(job.validate_result(root, result)['status'], 'failed')
            with self.assertRaises(ValueError): job.accept(root, result)
            wrong = job.validate_result(root, assignment['worker_input'])
            self.assertEqual(wrong['errors'][0]['code'], 'worker_input_is_not_result')
            self.assertEqual(wrong['errors'][0]['result_path'], str(result))

    def test_equation_errors_have_specific_hints_without_silent_rewrite(self):
        from restoration_tools import content_errors
        value = {'questions': [{'content': [{'runs': [
            {'kind': 'equation', 'latex': r'A=\{x\mid x>0\}'},
            {'kind': 'equation', 'latex': r'\text{자연수'},
            {'kind': 'equation', 'latex': ''}]}]}]}
        before = json.dumps(value)
        errors = [e for e in content_errors(value) if e['code'] == 'equation']
        self.assertEqual(len(errors), 3)
        self.assertIn('집합', errors[0]['hint'])
        self.assertIn('text', errors[1]['hint'])
        self.assertIn('빈', errors[2]['hint'])
        self.assertEqual(before, json.dumps(value))

    def fixture_render(self, base):
        import fitz
        base.mkdir()
        handoff.TikzHandoffTests().fixture(base)
        pdf = base/'drawing.pdf'
        with fitz.open() as doc:
            doc.new_page(width=144, height=72); doc.save(pdf)
        receipt = job.load_json(base/'render.json')
        receipt['pdf']['sha256'] = job.digest(pdf)
        job.save_json(base/'render.json', receipt)
        return base/'render.json'

    def test_batch_pending_review_failed_review_and_tampering(self):
        batch = self.module()
        with tempfile.TemporaryDirectory() as d:
            base = Path(d); root, _, _ = fixtures.EfficiencyTests().prepared(base)
            render = self.fixture_render(base/'figure')
            rows = [{'id': f'f{i}', 'question_id':'q1', 'render_receipt':str(render), 'width_mm':40} for i in (1,2)]
            out = base/'batch'
            report = batch.prepare_figures(root, 1, rows, out)
            self.assertEqual(report['status'], 'pending_review')
            self.assertEqual(len(report['items']), 2)
            self.assertEqual(batch.finalize_figures(root, 1, out)['status'], 'failed')
            self.assertFalse((out/'figures.json').exists())
            for item in report['items']:
                path = Path(item['review']); review = job.load_json(path)
                self.assertEqual(review['status'], 'pending')
                review.update(status='passed', checks={k:'passed' for k in review['checks']})
                job.save_json(path, review)
            final = batch.finalize_figures(root, 1, out)
            self.assertEqual(final['status'], 'ready')
            figures = job.load_json(final['figures'])['figures']
            self.assertEqual(figures['f2']['size_mm'], [40,20])
            (base/'figure/drawing.tex').write_text('tampered')
            self.assertEqual(batch.finalize_figures(root, 1, out)['status'], 'failed')

    def test_duplicate_ids_rejected_before_any_work(self):
        batch = self.module()
        with tempfile.TemporaryDirectory() as d:
            base = Path(d); root, _, _ = fixtures.EfficiencyTests().prepared(base)
            rows = [{'id':'same','question_id':'q1','source':'missing.tex','width_mm':40}]*2
            with self.assertRaisesRegex(ValueError, 'duplicate_figure_id'):
                batch.prepare_figures(root, 1, rows, base/'batch')
            self.assertFalse((base/'batch').exists())

    def test_review_pack_bound_to_native_pdf_and_accepted_pages(self):
        batch = self.module()
        import fitz
        with tempfile.TemporaryDirectory() as d:
            base = Path(d); root, manifest, assignment = fixtures.EfficiencyTests().prepared(base)
            path = root/'submit.json'; job.save_json(path, fixtures.EfficiencyTests().page(manifest, assignment)); job.accept(root,path)
            pdf = base/'native.pdf'
            with fitz.open() as doc: doc.new_page(width=595.2,height=841.8); doc.save(pdf)
            receipt = {'status':'rendered','job':str(root),'pages_sha256':batch.pages_digest(job.assemble(root)),
                       'page_count':1,'artifacts':{'pdf':{'path':str(pdf),'sha256':job.digest(pdf)}}}
            result = batch.review_pack(root, receipt, base/'review')
            self.assertEqual(result['status'], 'pending_review')
            packet = job.load_json(result['pages'][0]['review_input'])
            self.assertEqual(packet['visual_status'], 'not_verified')
            self.assertEqual(packet['output_crops'], [])
            self.assertTrue(all(Path(p['path']).is_file() for p in packet['output_crops']))
            receipt['pages_sha256'] = '0'*64
            with self.assertRaisesRegex(ValueError, 'native_review_binding_mismatch'):
                batch.review_pack(root, receipt, base/'bad')
            self.assertFalse((base/'bad').exists())
            receipt['pages_sha256'] = batch.pages_digest(job.assemble(root))
            pdf.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'hash_mismatch'):
                batch.review_pack(root, receipt, base/'changed')


if __name__ == '__main__': unittest.main()
