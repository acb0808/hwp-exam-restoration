"""Observed square/check-format retries; no quota or native visual claims."""
import asyncio,copy,json,sys,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from restoration_compiler import _studio_equation
import restoration_mcp as transport
import restoration_single as single
import restoration_job as job
import restoration_batch as batch
import test_single_review_workflow as workflow
import test_figure_retry as fixtures

CHECKS=('geometry','labels','marks','source_comparison')
def passed(ident='f1'):
    return {'id':ident,'status':'passed','checks':dict.fromkeys(CHECKS,'passed'),'issues':[]}

class SquareTests(unittest.TestCase):
    def test_square_command_and_unicode_preserve_editable_symbol(self):
        for latex in (r'\square ABCD',r'\square EFGH=16',r'\Box ABCD',r'\frac{\square ABCD}{2}','□ ABCD'):
            with self.subTest(latex=latex):
                try:result=_studio_equation(latex,'square')
                except ValueError as exc:self.fail(str(exc))
                self.assertIn('□',result['selected_script'])
                self.assertNotIn('\\square',result['selected_script'])
                self.assertEqual(result['approximations'],[])
                self.assertTrue(result['review_required'])
                self.assertFalse(result['rendering_verified'])

    def test_similarly_named_commands_and_approximation_remain_rejected(self):
        for latex in (r'\squareplus ABCD',r'\squareRoot{2}',r'\boxed{x}',r'x\qquad y'):
            with self.subTest(latex=latex), self.assertRaises(ValueError):
                _studio_equation(latex,'unsupported')

    def test_render_environment_hash_tracks_values_and_ignores_key_order(self):
        before={'TEXMFVAR':'a','FONTCONFIG_FILE':'fonts.conf'}
        changed={'TEXMFVAR':'b','FONTCONFIG_FILE':'fonts.conf'}
        reordered={'FONTCONFIG_FILE':'fonts.conf','TEXMFVAR':'a'}
        self.assertNotEqual(batch.pages_digest(before),batch.pages_digest(changed))
        self.assertEqual(batch.pages_digest(before),batch.pages_digest(reordered))

class ReviewProtocolTests(unittest.TestCase):
    def test_discovered_schema_has_exact_checks_and_values(self):
        tools={t.name:t for t in asyncio.run(transport.mcp.list_tools())}
        schema=tools['hwp_review_figures'].inputSchema
        review=schema['$defs']['FigureReview'];checks=review['properties']['checks']
        self.assertIn('$ref',checks)
        checks=schema['$defs'][checks['$ref'].split('/')[-1]]
        self.assertEqual(set(checks['required']),set(CHECKS))
        self.assertFalse(checks['additionalProperties'])
        for item in checks['properties'].values():
            self.assertEqual(set(item['enum']),{'passed','failed','not_verified'})

    def test_bad_check_returns_actionable_error_without_dispatch(self):
        row=passed();row['checks']['geometry']='passed (직사각형 일치)'
        with patch.object(transport,'dispatch') as dispatch:
            result=asyncio.run(transport.mcp.call_tool('hwp_review_figures',
                {'job':'unused','page':1,'batch_path':'unused','reviews':[row]}))
        dispatch.assert_not_called()
        self.assertTrue(result.isError)
        value=result.structuredContent
        self.assertEqual(value['message'],'invalid_figure_review_input')
        self.assertIn('geometry',str(value['corrections']))
        self.assertIn(row['checks']['geometry'],str(value['corrections']))
        self.assertIn('not_verified',str(value['corrections']))
        self.assertIn('No image reread',value['next_action'])

    def test_passed_cannot_hide_failed_checks_or_issues(self):
        from pydantic import ValidationError
        for value in ('failed','not_verified'):
            row=passed();row['checks']['labels']=value
            with self.assertRaises(ValidationError):transport.FigureReview(**row)
        row=passed();row['issues']=['label overlaps line']
        with self.assertRaises(ValidationError):transport.FigureReview(**row)

    def test_missing_extra_or_explanatory_check_is_invalid(self):
        from pydantic import ValidationError
        for change in ('missing','extra','explanation'):
            row=passed()
            if change=='missing':row['checks'].pop('marks')
            elif change=='extra':row['checks']['layout']='passed'
            else:row['checks']['marks']='passed (일치)'
            with self.assertRaises(ValidationError):transport.FigureReview(**row)

class ReviewTransactionTests(unittest.TestCase):
    def setUp(self):
        self.case=workflow.SingleReviewTests();self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.root=self.case.root;self.call=self.case.call

    def figure_batch(self):
        base=Path(self.case.temp.name)/'renderer';base.mkdir()
        _,_,_,calls,runtime=fixtures.FigureRetryTests().fixture(base)
        md=workflow.MD.replace('1. 값은 $x-1$?','1. 값은 $x-1$?\n\n![](figure:f1)\n\n![](figure:f2)')
        self.assertEqual(self.call('submit_reading',page=1,markdown=md)['status'],'ready_for_figures')
        figures=[]
        for ident in ('f1','f2'):
            source=self.root/('workers/page-0001/'+ident+'.tex')
            source.write_text(r'\begin{tikzpicture}\draw (0,0)--(1,1);\end{tikzpicture}')
            figures.append({'id':ident,'question_id':'q1','latex_path':str(source),'width_mm':40})
        with patch.object(single.batch.runpy,'run_path',return_value=runtime):
            result=self.call('render_figures',page=1,figures=figures)
        self.assertEqual(result['status'],'pending_review',result)
        return result,calls

    def test_batch_validated_before_any_evidence_or_cache_changes(self):
        batch,calls=self.figure_batch()
        path=batch['batch_path']
        before=job.load_json(self.root/'mcp/state.json')
        batch_before=Path(path).read_bytes()
        evidence_before=set((self.root/'mcp/evidence').iterdir())
        rows=[passed('f1'),passed('f2')];rows[1]['checks']['marks']='passed (일치)'
        failed=self.call('review_figures',page=1,batch_path=path,reviews=rows)
        self.assertEqual(failed['status'],'failed')
        self.assertEqual(job.load_json(self.root/'mcp/state.json'),before)
        self.assertEqual(Path(path).read_bytes(),batch_before)
        self.assertEqual(set((self.root/'mcp/evidence').iterdir()),evidence_before)
        self.assertEqual(failed['message'],'invalid_figure_review_input')
        self.assertEqual(failed['pending_ids'],['f1','f2'])
        self.assertEqual(self.call('review_figures',page=1,batch_path=path,
            reviews=[passed('f1'),passed('f2')])['status'],'accepted')
        self.assertEqual(len(calls),2,'input correction must not render again')

    def test_wrong_ids_return_missing_and_unexpected_ids(self):
        batch,_=self.figure_batch()
        result=self.call('review_figures',page=1,batch_path=batch['batch_path'],reviews=[passed('f1'),passed('absent')])
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['missing_ids'],['f2'])
        self.assertEqual(result['unexpected_ids'],['absent'])

    def test_genuine_failed_review_remains_unaccepted(self):
        batch,_=self.figure_batch()
        row=passed('f2');row.update(status='failed',issues=['label overlaps line'])
        row['checks']['labels']='failed'
        result=self.call('review_figures',page=1,batch_path=batch['batch_path'],reviews=[passed('f1'),row])
        self.assertNotEqual(result['status'],'accepted')
        self.assertEqual(result['errors'],[{'id':'f2','error':'figure_comparison_failed',
                                            'issues':['label overlaps line']}])
        self.assertIn('hwp_render_figures',result['next_action'])
        with self.assertRaises(ValueError):job.assemble(self.root)

    def test_failed_figure_review_requires_a_specific_issue(self):
        batch,calls=self.figure_batch()
        row=passed('f2');row['status']='failed';row['checks']['labels']='failed'
        result=self.call('review_figures',page=1,batch_path=batch['batch_path'],reviews=[passed('f1'),row])
        self.assertEqual(result['message'],'invalid_figure_review_input')
        self.assertIn('issues',str(result['corrections']))
        self.assertEqual(len(calls),2,'missing explanation must not start another render')

    def test_edited_tex_rejects_old_batch_without_probing_or_accepting_it(self):
        rendered,calls=self.figure_batch()
        source=self.root/'workers/page-0001/f2.tex'
        source.write_text(r'\begin{tikzpicture}\draw (0,0)--(2,1);\end{tikzpicture}',encoding='utf8')
        before=job.load_json(self.root/'mcp/state.json')
        result=self.call('review_figures',page=1,batch_path=rendered['batch_path'],
                         reviews=[passed('f1'),passed('f2')])
        self.assertEqual(result['message'],'figure_source_changed_since_render')
        self.assertEqual(result['changed_ids'],['f2'])
        self.assertIn('hwp_render_figures',result['next_action'])
        self.assertEqual(job.load_json(self.root/'mcp/state.json'),before)
        self.assertEqual(len(calls),2,'stale review must not render or alter evidence')

    def test_pre_upgrade_batch_without_source_binding_requires_new_render(self):
        rendered,calls=self.figure_batch()
        state=job.load_json(self.root/'mcp/state.json')
        state['batches'][rendered['batch_path']].pop('input_sources')
        job.save_json(self.root/'mcp/state.json',state)
        result=self.call('review_figures',page=1,batch_path=rendered['batch_path'],
                         reviews=[passed('f1'),passed('f2')])
        self.assertEqual(result['message'],'figure_source_binding_unavailable')
        self.assertIn('hwp_render_figures',result['next_action'])
        self.assertEqual(len(calls),2)

    def test_tex_text_and_binding_hash_come_from_one_read(self):
        self.case.call('submit_reading',page=1,markdown=workflow.MD.replace(
            '1. 값은 $x-1$?','1. 값은 $x-1$?\n\n![](figure:f1)'))
        path=self.root/'workers/page-0001/f1.tex'
        path.write_bytes(b'\xef\xbb\xbf\\begin{tikzpicture}\r\n\\draw (0,0)--(1,1);\r\n\\end{tikzpicture}')
        text,source=single.text_input(self.root,1,{'latex_path':str(path)},
                                      'latex','latex_path','f1.tex',with_source=True)
        self.assertEqual(text,'\\begin{tikzpicture}\n\\draw (0,0)--(1,1);\n\\end{tikzpicture}')
        self.assertEqual(source['sha256'],job.digest(path))

    def test_square_markdown_builds_once_as_editable_equation(self):
        md=workflow.MD.replace('x-1',r'\square ABCD')
        result=self.call('submit_reading',page=1,markdown=md)
        self.assertEqual(result['status'],'accepted',result)
        result=self.call('build',output=str(self.root/'square.hwpx'),title='test',school='test',year='2026',exam_title='test',native=False)
        self.assertEqual(result['status'],'built',result)
        with zipfile.ZipFile(result['output']) as z:
            xml=z.read('Contents/section0.xml').decode('utf8')
        self.assertIn('□ A B C D',xml)
        self.assertIn('<hp:equation',xml)

if __name__=='__main__':unittest.main()
