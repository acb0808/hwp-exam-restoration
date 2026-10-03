"""Protocol boundary tests; install requirements-mcp.txt to exercise the real SDK."""
import asyncio
import base64
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
HAS_MCP = importlib.util.find_spec('mcp') is not None


@unittest.skipUnless(HAS_MCP, 'MCP SDK is absent; run these tests in the dedicated MCP environment')
class MCPTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import restoration_mcp
        cls.transport = restoration_mcp

    def run_async(self, value):
        return asyncio.run(value)

    def test_tool_inventory_has_typed_markdown_input_and_local_annotations(self):
        tools = {t.name: t for t in self.run_async(self.transport.mcp.list_tools())}
        self.assertEqual(set(tools), {'hwp_prepare', 'hwp_assign', 'hwp_submit_reading',
            'hwp_status', 'hwp_inspect', 'hwp_render_figures',
            'hwp_review_figures', 'hwp_build', 'hwp_finish_review', 'hwp_help'})
        schema = tools['hwp_submit_reading'].inputSchema
        self.assertIn({'minLength': 1, 'type': 'string'}, schema['properties']['markdown']['anyOf'])
        self.assertIn({'minLength': 1, 'type': 'string'}, schema['properties']['markdown_path']['anyOf'])
        self.assertNotIn('role', schema['properties'])
        self.assertNotIn('native', tools['hwp_build'].inputSchema['properties'])
        self.assertIn('question_pages', tools['hwp_prepare'].inputSchema['properties'])
        self.assertTrue(tools['hwp_prepare'].inputSchema['properties']['include_answers']['default'])
        self.assertIn('worker_id', tools['hwp_assign'].inputSchema['properties'])
        self.assertNotIn('worker_a', tools['hwp_assign'].inputSchema['properties'])
        self.assertIn('reviews', tools['hwp_finish_review'].inputSchema['properties'])
        self.assertIn('reviewer_id', tools['hwp_finish_review'].inputSchema['required'])
        self.assertFalse(schema['additionalProperties'])
        for name, tool in tools.items():
            self.assertFalse(tool.annotations.destructiveHint)
            self.assertFalse(tool.annotations.openWorldHint)
            self.assertEqual(tool.annotations.readOnlyHint, name in ('hwp_status','hwp_help'))

    def test_unknown_top_level_field_rejected_before_dispatch(self):
        with patch.object(self.transport, 'dispatch') as dispatch:
            result = self.run_async(self.transport.mcp.call_tool('hwp_status', {'job': 'job', 'shell': 'bad'}))
        self.assertTrue(result.isError)
        self.assertIn('shell', result.content[0].text)
        dispatch.assert_not_called()

    def test_removed_ab_approval_and_role_are_rejected(self):
        with patch.object(self.transport, 'dispatch') as dispatch:
            removed = self.run_async(self.transport.mcp.call_tool('hwp_approve', {'job': 'job'}))
            role = self.run_async(self.transport.mcp.call_tool('hwp_submit_reading',
                {'job': 'job', 'page': 1, 'role': 'a', 'markdown': 'text'}))
        self.assertTrue(removed.isError)
        self.assertTrue(role.isError)
        dispatch.assert_not_called()

    def test_strict_nested_inputs_reject_code_and_invalid_boxes(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            self.transport.CropRequest(id='crop1', question_id='q1', bbox_px=[1, 2, 3, 4], code='bad')
        with self.assertRaises(ValidationError):
            self.transport.CropRequest(id='crop1', question_id='q1', bbox_px=[1, 2, 3, 4])
        with self.assertRaises(ValidationError):
            self.transport.CropRequest(id='crop1', question_id='q1', bbox_px=[1, 2, 3, 4], reason=' ')
        with self.assertRaises(ValidationError):
            self.transport.CropRequest(id='crop1', question_id='q1', bbox_px=[1, 2, 3])
        with self.assertRaises(ValidationError):
            self.transport.CropRequest(id='crop1', question_id='q1', bbox_px=[1, 2, float('inf'), 4])
        with self.assertRaises(ValidationError):
            self.transport.FigureInput(id='f1', question_id='q1', latex='x', width_mm=0)
        with self.assertRaises(ValidationError):
            self.transport.PageReview(page=0, status='passed', issues=[])
        with self.assertRaises(ValidationError):
            self.transport.PageReview(page=1, status='passed', issues=[], worker_id='owner')

    def test_markdown_is_passed_losslessly_to_service_without_json_instructions(self):
        markdown = '## q1 left\n$x - 1$의 값은?\n'
        with patch.object(self.transport, 'dispatch', return_value={'status': 'submitted', 'next_action': 'hwp_compose'}) as dispatch:
            result = self.run_async(self.transport.hwp_submit_reading('job', 1, markdown))
        dispatch.assert_called_once_with('submit_reading', {'job': 'job', 'page': 1, 'markdown': markdown, 'markdown_path': None})
        self.assertFalse(result.isError)
        self.assertEqual(result.structuredContent['status'], 'submitted')

    def test_reader_path_is_forwarded_without_transport_reading_it(self):
        reader_path = 'job/workers/page-0001/reading.md'
        with patch.object(self.transport, 'dispatch', return_value={'status': 'submitted'}) as dispatch:
            result = self.run_async(self.transport.hwp_submit_reading('job', 1, markdown_path=reader_path))
        dispatch.assert_called_once_with('submit_reading', {'job': 'job', 'page': 1,
            'markdown': None, 'markdown_path': reader_path})
        self.assertFalse(result.isError)

    def test_layout_and_figure_paths_pass_through_without_reopening_source(self):
        figure = self.transport.FigureInput(id='q1-figure', question_id='q1',
            latex_path='job/workers/page-0001/q1-figure.tex', width_mm=35)
        with patch.object(self.transport, 'dispatch', return_value={'status': 'rendered'}) as dispatch:
            self.run_async(self.transport.hwp_render_figures('job', 1, [figure]))
        submitted = dispatch.call_args.args[1]['figures'][0]
        self.assertEqual(submitted['latex_path'], figure.latex_path)
        self.assertNotIn('latex', submitted)

    def test_actual_review_message_is_forwarded_unchanged(self):
        evidence = 'Independent reviewer fixture-reviewer viewed source.png and final.png.\nNo discrepancies observed.'
        reviews = [self.transport.PageReview(page=1, status='passed', issues=[])]
        with patch.object(self.transport, 'dispatch', return_value={'status': 'reviewed'}) as dispatch:
            self.run_async(self.transport.hwp_finish_review('job', 'fixture-reviewer', reviews, review_evidence=evidence))
        dispatch.assert_called_once_with('finish_review', {'job': 'job', 'reviewer_id': 'fixture-reviewer',
            'reviews': [{'page': 1, 'status': 'passed', 'issues': []}], 'review_evidence': evidence})

    def test_object_shaped_spawn_evidence_arrives_as_text(self):
        # opencode turned a JSON-looking evidence string into an object before sending it (v2.7.5 run).
        with patch.object(self.transport, 'dispatch', return_value={'status': 'assigned'}) as dispatch:
            self.run_async(self.transport.mcp.call_tool('hwp_assign', {'job': 'job', 'page': 2, 'worker_id': 'ses_a',
                                                                       'evidence': {'task_id': 'ses_a', 'state': 'completed'}}))
        self.assertIsInstance(dispatch.call_args.args[1]['evidence'], str)
        self.assertIn('ses_a', dispatch.call_args.args[1]['evidence'])

    def test_single_worker_assignment_is_forwarded(self):
        with patch.object(self.transport, 'dispatch', return_value={'status': 'assigned'}) as dispatch:
            self.run_async(self.transport.hwp_assign('job', 2, 'owner-2', 'actual spawn response'))
        dispatch.assert_called_once_with('assign', {'job': 'job', 'page': 2,
            'worker_id': 'owner-2', 'evidence': 'actual spawn response'})

    def test_answer_sheet_default_and_explicit_opt_out_reach_service(self):
        for include in (True,False):
            with patch.object(self.transport,'dispatch',return_value={'status':'prepared'}) as dispatch:
                kwargs={} if include else {'include_answers':False}
                self.run_async(self.transport.hwp_prepare('source.pdf','job',**kwargs))
            self.assertEqual(dispatch.call_args.args[1]['include_answers'],include)

    def test_images_are_native_mcp_blocks_and_not_base64_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'view.png'
            raw = b'\x89PNG\r\n\x1a\nfixture'
            path.write_bytes(raw)
            payload = {'status': 'ready', 'inspection_id': 'i1', '_images': [{'path': str(path), 'mimeType': 'image/png'}]}
            with patch.object(self.transport, 'dispatch', return_value=payload):
                result = self.run_async(self.transport.hwp_inspect('job', 1))
            self.assertEqual(result.content[1].type, 'image')
            self.assertEqual(base64.b64decode(result.content[1].data), raw)
            self.assertNotIn('_images', result.structuredContent)
            self.assertNotIn(base64.b64encode(raw).decode(), result.content[0].text)
            self.assertIn('_images', payload, 'Rendering must not mutate the service result')

    def test_pending_is_not_an_error_but_failure_is(self):
        for status, expected in [('blocked', False), ('failed', True)]:
            with self.subTest(status=status), patch.object(self.transport, 'dispatch', return_value={'status': status, 'next_action': 'hwp_status'}):
                result = self.run_async(self.transport.hwp_status('job'))
                self.assertEqual(result.isError, expected)

    def test_reply_is_sent_in_full_when_shortening_it_fails(self):
        import restoration_reply
        payload = {'status': 'pending_review', 'batch_path': 'b.json', 'review_tasks': [{'id': 'f1'}]}
        with patch.object(self.transport, 'dispatch', return_value=payload), \
                patch.object(restoration_reply, 'compact_reply', side_effect=TypeError('unexpected shape')):
            result = self.run_async(self.transport.hwp_render_figures('job', 1))
        self.assertEqual(result.structuredContent, payload)
        self.assertFalse(result.isError)

    def test_nested_models_become_plain_service_data(self):
        crop = self.transport.CropRequest(id='c1', question_id='q1', bbox_px=[0, 1, 2, 3], reason='unclear symbol')
        with patch.object(self.transport, 'dispatch', return_value={'status': 'ready'}) as dispatch:
            self.run_async(self.transport.hwp_inspect('job', 1, [crop]))
        self.assertIsInstance(dispatch.call_args.args[1]['requests'][0], dict)

    def test_output_inspection_target_uses_existing_tool(self):
        crop = self.transport.CropRequest(id='q1-mark',question_id='q1',bbox_px=[0,0,80,40],reason='thin bar')
        with patch.object(self.transport,'dispatch',return_value={'status':'ready'}) as dispatch:
            self.run_async(self.transport.hwp_inspect('job',1,[crop],target='output'))
        self.assertEqual(dispatch.call_args.args[1]['target'],'output')
        self.assertEqual(dispatch.call_args.args[1]['requests'][0]['bbox_px'],[0,0,80,40])

    def test_real_stdio_initialization_discovery_and_error_response(self):
        import anyio
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        async def scenario():
            command = StdioServerParameters(command=sys.executable,
                args=['-B', str(Path(self.transport.__file__).resolve())])
            with anyio.fail_after(30):
                async with stdio_client(command) as (reader, writer):
                    async with ClientSession(reader, writer) as session:
                        initialized = await session.initialize()
                        self.assertEqual(initialized.serverInfo.name, 'hwp_restoration_mcp')
                        listed = await session.list_tools()
                        self.assertEqual(len(listed.tools), 10)
                        result = await session.call_tool('hwp_status', {'job': 'unused', 'shell': 'not allowed'})
                        self.assertTrue(result.isError)
                        self.assertEqual(result.structuredContent['status'], 'failed')
                        with tempfile.TemporaryDirectory() as folder:
                            import fitz
                            root=Path(folder);source=root/'source.pdf';job=root/'job'
                            with fitz.open() as pdf:
                                pdf.new_page();pdf.save(source)
                            await session.call_tool('hwp_prepare',{'job':str(job),'source':str(source),'question_pages':[1],'include_answers':False})
                            await session.call_tool('hwp_assign',{'job':str(job),'page':1,'worker_id':'test-worker','evidence':'test-worker fixture, not real visual review.'})
                            before={p.relative_to(job):p.read_bytes() for p in job.rglob('*') if p.is_file()}
                            helped=await session.call_tool('hwp_help',{'job':str(job),'page':1,'topic':'equations'})
                            self.assertFalse(helped.isError,helped)
                            self.assertEqual(helped.structuredContent['reported_errors'],[])
                            self.assertEqual(helped.structuredContent['submit_call']['tool'],'hwp_submit_reading')
                            removed=await session.call_tool('hwp_help',{'job':str(job),'page':1,'topic':'equations','equations':['x']})
                            self.assertTrue(removed.isError)
                            after={p.relative_to(job):p.read_bytes() for p in job.rglob('*') if p.is_file()}
                            self.assertEqual(before,after)
        anyio.run(scenario)


if __name__ == '__main__':
    unittest.main()
