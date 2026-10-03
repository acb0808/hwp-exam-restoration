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


def compact_reply(action, result):
    if not isinstance(result, dict): return result
    if action == 'render_figures' and size(result) > HOST_REPLY_BYTES // 2:
        return compact_render(result)
    return result
