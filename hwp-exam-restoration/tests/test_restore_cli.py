import contextlib
import importlib.util
import io
import json
import hashlib
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import restore
from restoration_batch import pages_digest


class CliTests(unittest.TestCase):
    def test_omitted_template_uses_saved_pdf2hwp_grid(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);captured={}
            def build(pages,target,**kwargs):
                captured.update(kwargs);target.write_bytes(b'package')
                return {'status':'built_pending_native_validation'}
            modules={'restoration_job':self.job_module([{'page_number':1}]),
                     'restoration_compiler':types.SimpleNamespace(build_hwpx=build)}
            with patch.dict(sys.modules,modules),contextlib.redirect_stdout(io.StringIO()):
                restore.main(['build','job',str(root/'out.hwpx')])
            self.assertEqual(captured['template_dir'].resolve(),SCRIPTS.parent/'assets/templates/pdf2hwp-grid')

    def job_module(self, pages):
        return types.SimpleNamespace(prepare=lambda *a, **k: {}, assign=lambda *a, **k: {},
            accept=lambda *a, **k: {}, assemble=lambda *a: pages)

    def test_missing_results_prevent_compiler_execution(self):
        module = self.job_module([])
        def fail(*a): raise ValueError('missing_page_results')
        module.assemble = fail
        with patch.dict(sys.modules, {'restoration_job': module}):
            with self.assertRaisesRegex(ValueError, 'missing_page_results'):
                restore.main(['build', 'job', 'out.hwpx'])

    def test_changed_built_file_blocks_native(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); output = root / 'out.hwpx'; job = root / 'job'
            def build(pages, target, **kw):
                target.write_bytes(b'original-package')
                return {'status': 'built_pending_native_validation'}
            modules = {'restoration_job': self.job_module([{'page_number': 1}]),
                       'restoration_compiler': types.SimpleNamespace(build_hwpx=build)}
            with patch.dict(sys.modules, modules), contextlib.redirect_stdout(io.StringIO()):
                restore.main(['build', str(job), str(output)])
                output.write_bytes(b'changed-package')
                code=restore.main(['native', str(job), str(output.with_suffix('.build.json')), str(root/'native')])
            self.assertEqual(code,2)
            report=json.loads((root/'native/restoration-native.json').read_text())
            self.assertEqual(report['error'],'build_receipt_or_accepted_pages_changed')
            self.assertEqual(report['cleanup'],'no_session_started')

    def test_native_accepts_service_receipt_with_answer_review_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); job = root/'job'; job.mkdir()
            output = root/'out.hwpx'; output.write_bytes(b'package')
            pages = [
                {'page_number': 5, 'questions': [{'id': 'q12'}]},
                {'page_number': 11, 'role': 'answer_sheet',
                 'answer_review_questions': {'5/q12': {'revision': 'review-only'}}},
            ]
            receipt = {
                'job': str(job.resolve()), 'output': str(output),
                'output_sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
                'pages_sha256': pages_digest(pages),
                'template': {'name': 'pdf2hwp-grid'},
            }
            receipt_path = root/'out.build.json'
            receipt_path.write_text(json.dumps(receipt), encoding='utf-8')
            calls = []
            def render(*args, **kwargs):
                calls.append(args)
                Path(args[1]).mkdir()
                return {'status': 'blocked', 'cleanup': 'no_session_started'}
            modules = {
                'restoration_job': self.job_module(pages),
                'native_layout': types.SimpleNamespace(render_native=render),
            }
            with patch.dict(sys.modules, modules), contextlib.redirect_stdout(io.StringIO()):
                code = restore.main(['native', str(job), str(receipt_path), str(root/'native')])
            self.assertEqual(code, 2)
            self.assertEqual(len(calls), 1)
            native = json.loads((root/'native/restoration-native.json').read_text())
            self.assertEqual(native['pages_sha256'], receipt['pages_sha256'])

    def test_native_receipt_mismatch_records_no_session_started(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); job = root/'job'; job.mkdir()
            output = root/'out.hwpx'; output.write_bytes(b'package')
            receipt = {
                'job': str(job.resolve()), 'output': str(output),
                'output_sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
                'pages_sha256': 'wrong', 'template': {'name': 'pdf2hwp-grid'},
            }
            receipt_path = root/'out.build.json'
            receipt_path.write_text(json.dumps(receipt), encoding='utf-8')
            modules = {'restoration_job': self.job_module([{'page_number': 1}])}
            with patch.dict(sys.modules, modules), contextlib.redirect_stdout(io.StringIO()):
                code = restore.main(['native', str(job), str(receipt_path), str(root/'native')])
            self.assertEqual(code, 2)
            native = json.loads((root/'native/restoration-native.json').read_text())
            self.assertEqual(native['error'], 'build_receipt_or_accepted_pages_changed')
            self.assertEqual(native['cleanup'], 'no_session_started')

    def test_image_dpi_not_silently_assumed(self):
        module = self.job_module([]); captured = {}
        def prepare(*a, **kwargs): captured.update(kwargs); return {'pages':[]}
        module.prepare = prepare
        with patch.dict(sys.modules, {'restoration_job': module}), contextlib.redirect_stdout(io.StringIO()):
            restore.main(['prepare', 'scan.png', 'job'])
        self.assertIsNone(captured['dpi'])

    def test_native_extra_page_cannot_be_success(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); output = root/'out.hwpx'; job = root/'job'; native = root/'native'
            def build(pages, target, **kw):
                target.write_bytes(b'package')
                return {'status': 'built_pending_native_validation', 'template': {'name':'pdf2hwp-grid'}}
            def render(*a, **kw):
                native.mkdir()
                return {'status': 'rendered', 'page_count': 2}
            modules = {'restoration_job': self.job_module([{'page_number': 1}]),
                'restoration_compiler': types.SimpleNamespace(build_hwpx=build),
                'native_layout': types.SimpleNamespace(render_native=render)}
            with patch.dict(sys.modules, modules), contextlib.redirect_stdout(io.StringIO()):
                restore.main(['build', str(job), str(output)])
                code = restore.main(['native', str(job), str(output.with_suffix('.build.json')), str(native)])
            self.assertEqual(code, 2)
            report = json.loads((native/'restoration-native.json').read_text())
            self.assertEqual(report['error'], 'native_page_count_differs_from_source')


if __name__ == '__main__': unittest.main()
