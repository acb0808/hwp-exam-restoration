"""Exercise the actual stdio workflow with saved synthetic text, never live OCR.

The test's source-review messages are fixture decisions. Passing this test verifies
transport and workflow integration, not transcription or visual quality.
"""
import importlib.util
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

HAS_RUNTIME = all(importlib.util.find_spec(name) is not None for name in ('mcp', 'fitz'))
SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'restoration_mcp.py'
MARKDOWN = '''## left
### q1
1. Find $2-1$.
::: answer
번호: 1
정답: $1$
근거: $2-1=1$.
:::
## right
### q2
2. Find $1+1$.
::: answer
번호: 2
정답: $2$
근거: $1+1=2$.
:::
'''
LAYOUT = '''units: mm
| type | id | region | x | y | width | height |
| --- | --- | --- | --- | --- | --- | --- |
| region | left | - | 10 | 20 | 85 | 250 |
| region | right | - | 110 | 20 | 85 | 250 |
| question | q1 | left | 10 | 20 | 85 | 100 |
| question | q2 | right | 110 | 20 | 85 | 100 |
'''


@unittest.skipUnless(HAS_RUNTIME, 'MCP SDK and PDF runtime required; use the dedicated MCP environment')
class MCPWorkflowTests(unittest.TestCase):
    def test_stdio_prepare_to_hwpx_and_no_automatic_visual_pass(self):
        import anyio
        import fitz
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'job'
            source = Path(directory) / 'source.pdf'
            with fitz.open() as pdf:
                page = pdf.new_page()
                page.insert_text((30, 70), '1. Find 2-1.')
                page.insert_text((320, 70), '2. Find 1+1.')
                pdf.save(source)

            async def scenario():
                command = StdioServerParameters(command=sys.executable, args=['-B', str(SCRIPT)])
                with anyio.fail_after(90):
                    async with stdio_client(command) as (reader, writer):
                        async with ClientSession(reader, writer) as session:
                            await session.initialize()

                            async def call(name, **params):
                                result = await session.call_tool(name, params)
                                self.assertFalse(result.isError, str(result.content))
                                self.assertIsInstance(result.structuredContent, dict)
                                self.assertNotEqual(result.structuredContent.get('status'), 'failed', result.structuredContent)
                                return result

                            prepared = await call('hwp_prepare', source=str(source), job=str(root), question_pages=[1])
                            self.assertEqual(prepared.structuredContent['status'], 'prepared')
                            await call('hwp_assign', job=str(root), page=1, worker_id='fixture-owner',
                                       evidence='Synthetic test spawn evidence: fixture-owner.')
                            reader_path = root / 'workers' / 'page-0001' / 'reading.md'
                            reader_path.parent.mkdir(parents=True, exist_ok=True)
                            reader_path.write_text(MARKDOWN, encoding='utf-8')
                            await call('hwp_submit_reading', job=str(root), page=1, markdown_path=str(reader_path))
                            submitted = await call('hwp_status', job=str(root), page=1)
                            self.assertNotIn('decision_template', submitted.structuredContent)
                            inspected = await call('hwp_inspect', job=str(root), page=1)
                            self.assertFalse(any(item.type == 'image' for item in inspected.content))
                            self.assertTrue(inspected.structuredContent['image_paths'])
                            self.assertEqual(submitted.structuredContent['status'], 'accepted')
                            # Real stdio reaches accepted without layout/compose. Native execution
                            # is deliberately not started by this transport fixture.
                            rejected = await session.call_tool('hwp_build', {'job':str(root),
                                'output':str(root/'out.hwpx'), 'title':'test','school':'test',
                                'year':'2026','exam_title':'test','native':False})
                            self.assertTrue(rejected.isError)
                            incomplete = await session.call_tool('hwp_finish_review',
                                {'job': str(root), 'reviewer_id': 'fixture-independent-reviewer',
                                 'reviews': [{'page': 1, 'status': 'passed', 'issues': []}],
                                 'review_evidence': 'Synthetic independent reviewer response: fixture-independent-reviewer.'})
                            self.assertTrue(incomplete.isError, 'Native output and real image delivery cannot be skipped.')
            anyio.run(scenario)


if __name__ == '__main__':
    unittest.main()
