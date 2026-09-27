"""Local stdio MCP transport. Workflow decisions belong to restoration_service.

Run with the dedicated MCP interpreter; stdout is reserved for MCP messages.
"""
from __future__ import annotations

import base64
import json
from functools import partial
from pathlib import Path
from typing import Annotated, Any, Literal

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, ImageContent, TextContent, ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, model_validator


def dispatch(action: str, params: dict) -> dict:
    # Delay workflow imports until execution, keeping tools/list fast and usable
    # even when a document-specific optional runtime is unavailable.
    from restoration_service import dispatch as service_dispatch
    return service_dispatch(action, params)


Nonempty = Annotated[str, Field(min_length=1)]
Page = Annotated[int, Field(ge=1)]
Pixels = Annotated[list[Annotated[float, Field(allow_inf_nan=False)]], Field(min_length=4, max_length=4)]


class AssignmentItem(BaseModel):
    """One actual worker binding in an optional page batch."""
    model_config = ConfigDict(extra='forbid')
    page: Page
    worker_id: Nonempty
    evidence: Nonempty


class ReadingItem(BaseModel):
    """One completed page, never a partial transcription."""
    model_config = ConfigDict(extra='forbid')
    page: Page
    markdown: Nonempty | None = None
    markdown_path: Nonempty | None = None

    @model_validator(mode='after')
    def exactly_one_input(self):
        if (self.markdown is None) == (self.markdown_path is None):
            raise ValueError('provide_exactly_one_markdown_or_markdown_path_per_item')
        return self


class CropRequest(BaseModel):
    """Rectangle for one unclear detail, in the selected full-page image pixels."""
    model_config = ConfigDict(extra='forbid')
    question_id: Nonempty = Field(description='Question identifier, e.g. q1; source inspection may precede transcription submission.')
    id: Nonempty = Field(description='Unique crop identifier, e.g. q1-symbol.')
    bbox_px: Pixels = Field(description='Selected full-page image pixels: [left, top, width, height].')
    reason: Annotated[str, Field(min_length=1, pattern=r'\S')] = Field(description='Specific nonblank uncertainty requiring enlargement.')


class FigureInput(BaseModel):
    """A single diagram from the page owner; the service handles rendering."""
    model_config = ConfigDict(extra='forbid')
    id: Nonempty = Field(description='Figure marker identifier in the submitted transcription.')
    question_id: Nonempty = Field(description='Question owning this figure.')
    latex: Nonempty | None = Field(default=None, description='One tikzpicture fragment or full TeX; supply exactly one of latex and latex_path.')
    latex_path: Nonempty | None = Field(default=None, description='Exact assigned workers/page-NNNN/FIGURE_ID.tex path; submit without rereading or retransmitting source.')
    width_mm: Annotated[float, Field(gt=0, allow_inf_nan=False)] = Field(description='Intended figure width in millimetres.')


class FigureChecks(BaseModel):
    """Exact verdicts; explanatory text belongs in the worker report."""
    model_config = ConfigDict(extra='forbid')
    geometry: Literal['passed','failed','not_verified']
    labels: Literal['passed','failed','not_verified']
    marks: Literal['passed','failed','not_verified']
    source_comparison: Literal['passed','failed','not_verified']


class FigureReview(BaseModel):
    """A human or agent source comparison, never a rendering success flag."""
    model_config = ConfigDict(extra='forbid')
    id: Nonempty = Field(description='Figure identifier from the render result.')
    checks: FigureChecks = Field(description='All four exact verdicts; no explanations inside values.')
    issues: list[str] = Field(description='Specific observed discrepancies; empty only when none remain.')
    status: Literal['passed', 'failed'] = Field(description='Passed only after viewing source and rendered diagram.')

    @model_validator(mode='after')
    def consistent_verdict(self):
        from figure_provenance import review_input_errors
        errors=review_input_errors([self.model_dump()])
        if errors:raise ValueError('invalid_figure_review_input: '+json.dumps(errors,ensure_ascii=False))
        return self


class PageReview(BaseModel):
    """One page decision made by the job's independent final reviewer."""
    model_config = ConfigDict(extra='forbid')
    page: Page = Field(description='Page ID from review_tasks, including the final answer sheet ID.')
    status: Literal['passed', 'failed'] = Field(description='Passed only after comparing the entire source and final page.')
    issues: list[str] = Field(description='Observed discrepancies requiring correction; empty only if none remain.')


def _failure(message: str, next_action: str) -> CallToolResult:
    result = {'status': 'failed', 'message': message, 'next_action': next_action}
    return CallToolResult(content=[TextContent(type='text', text=json.dumps(result, ensure_ascii=False))],
                          structuredContent=result, isError=True)


class RestorationMCP(FastMCP):
    """Reject misspelled top-level arguments rather than silently ignoring them.

    FastMCP validates typed values; its default argument model ignores extras.
    The public handlers below make discovery and execution agree about extras.
    """
    async def list_tools(self):
        result = await super().list_tools()
        for tool in result:
            tool.inputSchema['additionalProperties'] = False
        return result

    async def call_tool(self, name: str, arguments: dict[str, Any]):
        tool = next((item for item in await self.list_tools() if item.name == name), None)
        if tool is None:
            return _failure(f'Unknown tool: {name}', 'Use tools/list to select an available hwp_ tool.')
        unknown = set(arguments) - set(tool.inputSchema.get('properties', {}))
        if unknown:
            return _failure('Unknown argument(s): ' + ', '.join(sorted(unknown)),
                            f'Call {name} with only the fields in its input schema.')
        if name=='hwp_review_figures':
            from figure_provenance import review_input_errors,review_input_failure
            errors=review_input_errors(arguments.get('reviews'))
            if errors:
                value=review_input_failure(errors)
                return CallToolResult(content=[TextContent(type='text',text=json.dumps(value,ensure_ascii=False))],
                                      structuredContent=value,isError=True)
        return await super().call_tool(name, arguments)


mcp = RestorationMCP(
    'hwp_restoration_mcp',
    instructions=('Restore scanned exams with one fresh restoration worker per page. '
                  'Each page owner submits Markdown and restores its diagrams. '
                  'One separate final reviewer checks all pages; this reviewer must differ from every page owner. '
                  'Follow returned next_action and templates. Do not read Python files or '
                  'write conversion/crop code. Tool success never substitutes for visual review.'),
    log_level='WARNING',
)


def _annotations(title: str, read_only: bool = False) -> ToolAnnotations:
    return ToolAnnotations(title=title, readOnlyHint=read_only, destructiveHint=False,
                           idempotentHint=read_only, openWorldHint=False)


def _plain(value):
    if isinstance(value, BaseModel):
        return value.model_dump(exclude_none=True)
    if isinstance(value, list):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    return value


def _execute(action: str, params: dict) -> CallToolResult:
    payload = dispatch(action, _plain(params))
    if isinstance(payload, CallToolResult):
        return payload
    public = {key: value for key, value in payload.items() if key != '_images'}
    content = [TextContent(type='text', text=json.dumps(public, ensure_ascii=False, separators=(',', ':')))]
    # Paths are produced and scoped by the service, never accepted here as an
    # independent filesystem tool. Keep image bytes out of structured JSON.
    for item in payload.get('_images', []):
        data = base64.b64encode(Path(item['path']).read_bytes()).decode('ascii')
        content.append(ImageContent(type='image', data=data, mimeType=item['mimeType']))
    return CallToolResult(content=content, structuredContent=public, isError=public.get('status') in ('failed','partial_failure'))


async def _call(action: str, **params) -> CallToolResult:
    return await anyio.to_thread.run_sync(partial(_execute, action, params))


@mcp.tool(annotations=_annotations('Select question pages'))
async def hwp_prepare(source: Nonempty, job: Nonempty, dpi: Annotated[int, Field(ge=72, le=600)] | None = None,
                      question_pages: list[Page] | None = None, include_answers: bool = True) -> CallToolResult:
    """Prepare once. Without question_pages return an overview; repeat with selected question source page numbers without rerendering. Exclude source cover, blank and answer pages. include_answers defaults true: each owner supplies answer blocks and the engine appends one final answer table. Returns one worker handoff per selected page."""
    return await _call('prepare', source=source, job=job, dpi=dpi, include_answers=include_answers, **({'question_pages':question_pages} if question_pages is not None else {}))


@mcp.tool(annotations=_annotations('Assign page restoration worker'))
async def hwp_assign(job: Nonempty, page: Page | None = None, worker_id: Nonempty | None = None,
                     evidence: Nonempty | None = None,
                     items: Annotated[list[AssignmentItem], Field(min_length=1,max_length=64)] | None = None) -> CallToolResult:
    """Bind actual spawned workers: one page or items=[{page,worker_id,evidence}]. Never mix both forms. Each item needs its unmodified actual spawn response. Forward returned task paths to the same workers. Successful items persist; retry only failed_pages. Identical assignment retries reuse the binding."""
    params={'job':job,'page':page,'worker_id':worker_id,'evidence':evidence}
    if items is not None:
        params={key:value for key,value in params.items() if value is not None}
        params['items']=items
    return await _call('assign', **params)


@mcp.tool(annotations=_annotations('Submit page Markdown'))
async def hwp_submit_reading(job: Nonempty, page: Page | None = None, markdown: Nonempty | None = None,
                             markdown_path: Nonempty | None = None,
                             items: Annotated[list[ReadingItem], Field(min_length=1,max_length=64)] | None = None) -> CallToolResult:
    """Submit one complete page or items=[{page,markdown_path}]; do not mix forms. Each item supplies exactly one Markdown text/path. Submit ready pages without waiting for others; successful items persist and only failed_pages need correction. Return all original-line diagnostics to the same owner. Validation never replaces independent review."""
    params={'job':job,'page':page,'markdown':markdown,'markdown_path':markdown_path}
    if items is not None:
        params={key:value for key,value in params.items() if value is not None}
        params['items']=items
    return await _call('submit_reading', **params)


@mcp.tool(annotations=_annotations('Read restoration status', True))
async def hwp_status(job: Nonempty, page: Page | None = None) -> CallToolResult:
    """Get compact progress, outstanding issues and exact next-action templates for a job or page."""
    return await _call('status', job=job, page=page)


@mcp.tool(annotations=_annotations('Answer a page author’s writing question', True))
async def hwp_help(job: Nonempty, page: Page, topic: Literal['writing','equations','figures'] = 'writing') -> CallToolResult:
    """Recovery reference when a returned error remains unclear. Start by writing and submitting the complete reading.md, not by calling help. writing/equations returns existing submission errors, examples, answer mode and exact resubmission arguments. figures explains render/review rules. Read-only: no equation probe, rendering, submission or approval; hwp_submit_reading is the single whole-page validator."""
    return await _call('help', job=job, page=page, topic=topic)


@mcp.tool(annotations=_annotations('Inspect unclear source or output details'))
async def hwp_inspect(job: Nonempty, page: Page, requests: list[CropRequest] | None = None,
                      target: Literal['source','output'] = 'source') -> CallToolResult:
    """Return paths for unclear details to the requesting worker; no image input in master. Pre-submission source crops are allowed for a specific uncertainty; output needs current build. bbox_px uses that image's pixels."""
    return await _call('inspect', job=job, page=page, requests=requests, target=target)


@mcp.tool(annotations=_annotations('Render page figures'))
async def hwp_render_figures(job: Nonempty, page: Page, figures: list[FigureInput]) -> CallToolResult:
    """Render one page batch. Forward only pending review_tasks to its owner; verified unchanged reviews are reused. accepted needs no review call. Rendering alone never passes."""
    return await _call('render_figures', job=job, page=page, figures=figures)


@mcp.tool(annotations=_annotations('Record source-based figure review'))
async def hwp_review_figures(job: Nonempty, page: Page, batch_path: Nonempty, reviews: list[FigureReview]) -> CallToolResult:
    """Record actual owner comparisons for all pending IDs once. checks has geometry/labels/marks/source_comparison, each exactly passed/failed/not_verified. status=passed requires all passed and issues=[]. Put only observed problems in issues; normal comments stay in the worker report. Formatting errors require resubmission, not new images or rendering. Untouched verified figures retain evidence."""
    return await _call('review_figures', job=job, page=page, batch_path=batch_path, reviews=reviews)


@mcp.tool(annotations=_annotations('Build Hangul exam'))
async def hwp_build(job: Nonempty, output: Nonempty, title: str, school: str, year: str, exam_title: str) -> CallToolResult:
    """Build selected questions and the enabled final answer table into HWPX, HWP and PDF. Reuse the desired output name on revisions: existing files are preserved and an unused _v2/_v3 name is chosen automatically. build_output reports the chosen build path; artifacts are final native files. Keep this MCP session open until completion. No shell/COM fallback; visual and answer review remain required."""
    return await _call('build', job=job, output=output, title=title, school=school, year=year, exam_title=exam_title, native=True)


@mcp.tool(annotations=_annotations('Record independent final review'))
async def hwp_finish_review(job: Nonempty, reviewer_id: Nonempty, reviews: list[PageReview],
                            review_evidence: Nonempty | None = None,
                            review_evidence_path: Nonempty | None = None) -> CallToolResult:
    """Record passed AND failed pages before repairs. Prefer review_evidence_path: the independent reviewer's UTF-8 report saved inside job, containing actual reviewer ID and every reviewed source/reference/output path. Supply exactly one evidence text/path. No master rewriting. Missing/failed pages remain incomplete. Evidence cannot authenticate host origin."""
    if (review_evidence is None)==(review_evidence_path is None):
        return _failure('exactly_one_review_evidence_text_or_path_required','Use the existing reviewer report path only; do not regenerate the report.')
    if review_evidence_path is not None:
        try:
            root=Path(job).resolve(strict=True);path=Path(review_evidence_path).resolve(strict=True)
            if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size>262144:
                raise ValueError('review_evidence_file_must_be_inside_job_and_at_most_256KB')
            review_evidence=path.read_text(encoding='utf-8-sig')
            if not review_evidence.strip():raise ValueError('empty_review_evidence')
        except (OSError,ValueError) as exc:
            return _failure(str(exc),'The same reviewer repairs only its report path/file; no new image reads or rebuild are needed.')
    return await _call('finish_review', job=job, reviewer_id=reviewer_id, reviews=reviews, review_evidence=review_evidence)


if __name__ == '__main__':
    import argparse
    argparse.ArgumentParser(description='Local MCP stdio server. Connect through the host; no direct document commands.').parse_args()
    mcp.run(transport='stdio')
