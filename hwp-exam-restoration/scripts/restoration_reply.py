"""Shorter MCP replies for hosts that hide long tool output.

Antigravity saves a tool reply over about 4KB to a file and shows the model only its path, so the
producer spends a turn reopening it (64 of 431 replies in six measured runs, mostly render_figures).
The service result stays complete for callers and tests; only the text sent to the agent drops
repeated paths and templates the agent does not act on.
"""
import json
from pathlib import Path

HOST_REPLY_BYTES = 3800


def size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))


def compact_render(result):
    """render_figures: one shared source path, no per-task compare paths when a sheet shows them,
    no all-not_verified review template, one TeX folder instead of a path per figure."""
    out = dict(result)
    tasks = [dict(t) for t in result.get('review_tasks', [])]
    sources = {t.get('source_image') for t in tasks}
    if tasks and len(sources) == 1:
        out['source_image'] = sources.pop()
        for t in tasks: t.pop('source_image', None)
    if result.get('compare_sheets'):
        for t in tasks: t.pop('compare_image', None)
    out['review_tasks'] = tasks
    if 'reviews' in result:
        out.pop('reviews')
        out['pending_ids'] = [r['id'] for r in result['reviews']]
    paths = result.get('tex_paths') or {}
    folders = {str(Path(p).parent) for p in paths.values()}
    if len(folders) == 1:
        out.pop('tex_paths'); out['tex_dir'] = folders.pop()
    if not result.get('errors') and tasks:
        out['next_action'] = ('Open compare_sheets once (left source crop, right render), or a task render_image with the source. '
                              'Fix a figure by editing tex_dir/ID.tex, then hwp_render_figures(job,page) again. '
                              'Submit hwp_review_figures with this batch_path: every pending_ids entry once with status, all four checks '
                              '(geometry, labels, marks, source_comparison) and issues ([] when passed).')
    return out


def compact_review(result):
    """build/status/finish_review: the report skeleton at report_path lists every path and scope of each page,
    so the reply names only the pages. A 6-page build reply was 8-12 KB with the task list and was spilled to a file."""
    out = dict(result)
    tasks = out.pop('review_tasks')
    out['review_pages'] = [t['page'] for t in tasks]
    if out.get('status') == 'pending_review':
        out['next_action'] = ('The same independent reviewer checks only the pages listed in the report at report_path and fills it in. '
                              'First build: spawn one non-producer reviewer with the role file path, job and report_path in its first prompt '
                              '(the report lists every image path; do not open it or copy it into the prompt). '
                              'Record passed and failed pages with hwp_finish_review before repairs.')
    return out


def compact_errors(result):
    """submit_reading and help: one error per line of the reading, a shared hint said once.
    17 equation errors of one page were 9 KB with the same hint and engine spans repeated in each."""
    out = result
    for key in ('errors', 'reported_errors'):
        rows = result.get(key)
        if not isinstance(rows, list) or size(rows) <= HOST_REPLY_BYTES // 2: continue
        if out is result: out = dict(result)
        seen = {}; short = []
        for row in rows:
            if not isinstance(row, dict): short.append(row); continue
            item = {k: row[k] for k in ('question_id', 'line', 'latex', 'message', 'code', 'issues') if row.get(k) is not None}
            tokens = [d.get('token') for d in row.get('engine_diagnostics', []) if d.get('token')]
            if tokens: item['at'] = tokens[0] if len(tokens) == 1 else tokens
            hint = row.get('hint')
            if hint:
                if hint in seen: item['hint'] = f'같은 안내: {seen[hint]}번째 오류'
                else: seen[hint] = len(short) + 1; item['hint'] = hint
            short.append(item)
        out[key] = short
    return out


def compact_reply(action, result):
    if not isinstance(result, dict): return result
    if action == 'render_figures' and size(result) > HOST_REPLY_BYTES // 2:
        return compact_render(result)
    if action in ('build', 'status', 'finish_review') and isinstance(result.get('review_tasks'), list):
        return compact_review(result)
    if action in ('submit_reading', 'help'):
        return compact_errors(result)
    return result
