"""Compact transcription retains strict production checks; fixtures are synthetic."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import restoration_job as job
import test_efficiency
import test_tikz_handoff


class CompactDraftTests(unittest.TestCase):
    def fixture(self, base):
        helper = test_efficiency.EfficiencyTests()
        root, manifest, assignment = helper.prepared(base)
        page = helper.page(manifest, assignment)
        draft = {'schema': 'restoration-draft/1', 'issues': [],
                 'regions': copy.deepcopy(page['regions']), 'questions': copy.deepcopy(page['questions'])}
        for region in draft['regions']: region.pop('question_ids')
        for question in draft['questions']:
            question.pop('font_family'); question.pop('font_pt')
            for block in question['content']: block.pop('id')
        return root, manifest, assignment, page, draft

    def compile(self, root, draft, output, figures=None):
        from restoration_draft import compile_draft
        path = root / 'draft.json'; job.save_json(path, draft)
        original = path.read_bytes(); manifest = (root / 'manifest.json').read_bytes()
        result = compile_draft(root, 1, path, output, figures=figures)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual((root / 'manifest.json').read_bytes(), manifest)
        self.assertFalse(result['accepted']); self.assertEqual(result['visual_status'], 'not_verified')
        return result

    def test_expands_only_mechanical_fields_and_keeps_content_and_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root, manifest, assignment, page, draft = self.fixture(Path(directory))
            output = root / 'compiled.json'
            result = self.compile(root, draft, output)
            self.assertEqual(result['status'], 'compiled')
            value = job.load_json(output)
            self.assertEqual(job.validate_result(root, output)['status'], 'validated')
            self.assertEqual(value['regions'], page['regions'])
            for original, expanded in zip(draft['questions'], value['questions']):
                self.assertEqual(original['bbox_mm'], expanded['bbox_mm'])
                for a, b in zip(original['content'], expanded['content']):
                    self.assertEqual(a, {k:v for k,v in b.items() if k != 'id'})
            self.assertEqual(value['source_sha256'], manifest['source']['sha256'])
            self.assertEqual(value['assignment_id'], assignment['assignment_id'])
            second = root / 'second.json'; self.compile(root, draft, second)
            self.assertEqual(output.read_bytes(), second.read_bytes())

    def test_rejects_unknown_fields_order_conflicts_and_invalid_source_content(self):
        mutations = [lambda d:d.update(source_sha256='a'*64),
                     lambda d:d['questions'][0].update(unexpected=True),
                     lambda d:d['regions'][0].update(question_ids=['q3','q2','q1']),
                     lambda d:d.update(issues=['unreadable original']),
                     lambda d:d['questions'][0]['content'][0].update(runs=[{'kind':'equation','latex':r'\unsupported{x}'}]),
                     lambda d:d['questions'][0].update(bbox_mm=[0,0,10,10])]
        for mutate in mutations:
            with self.subTest(mutation=mutate), tempfile.TemporaryDirectory() as directory:
                root, _, _, _, draft = self.fixture(Path(directory)); mutate(draft)
                output = root / 'compiled.json'; result = self.compile(root, draft, output)
                self.assertEqual(result['status'], 'failed'); self.assertTrue(result['errors'])
                self.assertFalse(output.exists())

    def test_does_not_overwrite_existing_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root, _, _, _, draft = self.fixture(Path(directory))
            output = root / 'compiled.json'; output.write_bytes(b'keep existing result')
            result = self.compile(root, draft, output)
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(output.read_bytes(), b'keep existing result')

    def test_dictionary_input_and_explicit_fonts_are_unchanged(self):
        from restoration_draft import compile_draft
        with tempfile.TemporaryDirectory() as directory:
            root, _, _, page, draft = self.fixture(Path(directory))
            draft['questions'][0].update(font_pt=11, font_family='바탕')
            draft['regions'][0]['question_ids'] = page['regions'][0]['question_ids']
            original = copy.deepcopy(draft); output = root / 'compiled.json'
            self.assertEqual(compile_draft(root, 1, draft, output)['status'], 'compiled')
            self.assertEqual(draft, original)
            question = job.load_json(output)['questions'][0]
            self.assertEqual(question['font_pt'], 11); self.assertEqual(question['font_family'], '바탕')

    def test_generated_ids_avoid_explicit_ids_even_in_later_nested_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root, _, _, _, draft = self.fixture(Path(directory))
            draft['questions'][1]['content'] = [{'kind':'box','title':'', 'id':'draft-content-1-1',
                'content':[{'kind':'paragraph','runs':[{'kind':'text','text':'원문'}]}]}]
            output = root / 'compiled.json'
            self.assertEqual(self.compile(root, draft, output)['status'], 'compiled')
            value = job.load_json(output)
            self.assertNotEqual(value['questions'][0]['content'][0]['id'], 'draft-content-1-1')
            self.assertEqual(value['questions'][1]['content'][0]['id'], 'draft-content-1-1')

    def figure_file(self, base, root, manifest, assignment):
        _, figure, review = test_tikz_handoff.TikzHandoffTests().fixture(base)
        review['source_sha256'] = manifest['source']['sha256']
        job.save_json(base / 'review.json', review)
        figure['tikz']['review']['sha256'] = job.digest(base / 'review.json')
        figures = {'schema':'restoration-figures/1','job':str(root.resolve()),'page':1,
            'assignment_id':assignment['assignment_id'],'worker_id':'worker1',
            'source_sha256':manifest['source']['sha256'],'figures':{'drawing':figure}}
        path = root / 'figures.json'; job.save_json(path, figures)
        return path, figures, review

    def test_links_reviewed_figure_only_to_its_original_question(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); root, manifest, assignment, _, draft = self.fixture(base)
            path, figures, _ = self.figure_file(base, root, manifest, assignment)
            draft['questions'][0]['content'][0]['figure_ref'] = 'drawing'
            output = root / 'compiled.json'
            self.assertEqual(self.compile(root, draft, output, path)['status'], 'compiled')
            self.assertEqual(job.load_json(output)['questions'][0]['content'][0]['figure'], figures['figures']['drawing'])
            draft['questions'][0]['content'][0].pop('figure_ref')
            draft['questions'][1]['content'][0]['figure_ref'] = 'drawing'
            other = root / 'wrong-question.json'
            self.assertEqual(self.compile(root, draft, other, path)['status'], 'failed')
            self.assertFalse(other.exists())

    def test_rejects_pending_tampered_foreign_or_conflicting_figures(self):
        for case in ('pending','malformed_review','artifact','assignment','source','page','worker','job','unknown','conflict','missing'):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                base = Path(directory); root, manifest, assignment, _, draft = self.fixture(base)
                path, figures, review = self.figure_file(base, root, manifest, assignment)
                block = draft['questions'][0]['content'][0]; block['figure_ref'] = 'drawing'
                if case == 'pending':
                    review['status'] = 'pending'; job.save_json(base / 'review.json', review)
                    figures['figures']['drawing']['tikz']['review']['sha256'] = job.digest(base / 'review.json')
                elif case == 'malformed_review':
                    job.save_json(base / 'review.json', [])
                    figures['figures']['drawing']['tikz']['review']['sha256'] = job.digest(base / 'review.json')
                elif case == 'artifact': (base / 'drawing.tex').write_bytes(b'tampered')
                elif case == 'assignment': figures['assignment_id'] = 'foreign'
                elif case == 'source': figures['source_sha256'] = 'b'*64
                elif case == 'page': figures['page'] = 2
                elif case == 'worker': figures['worker_id'] = 'foreign'
                elif case == 'job': figures['job'] = str(base)
                elif case == 'unknown': block['figure_ref'] = 'unknown'
                elif case == 'conflict': block['figure'] = figures['figures']['drawing']
                job.save_json(path, figures)
                output = root / 'compiled.json'
                result = self.compile(root, draft, output, None if case == 'missing' else path)
                self.assertEqual(result['status'], 'failed'); self.assertFalse(output.exists())


if __name__ == '__main__': unittest.main()
