"""Strict, dependency-free page restoration contract; never repairs its input.

Geometry validation cannot establish faithful transcription or prove agent identity.
Those claims require source comparison and the orchestration receipt respectively.
Image role is a declaration, not an OCR-based rasterized-text detector.
"""
from __future__ import annotations

import math
import re


def _fail(where, message):
    raise ValueError(f'{where}: {message}')


def _keys(value, names, where):
    if not isinstance(value, dict) or set(value) != set(names.split()):
        _fail(where, 'expected exactly keys: ' + names)


def _string(value, where, empty=False):
    if not isinstance(value, str) or (not empty and not value.strip()):
        _fail(where, 'expected string' + ('' if empty else ' with content'))


def _number(value, where, positive=False):
    if type(value) not in (int, float) or not math.isfinite(value):
        _fail(where, 'expected finite number')
    if value < 0 or (positive and value == 0):
        _fail(where, 'expected positive number' if positive else 'negative coordinate')


def _bbox(value, where):
    if not isinstance(value, list) or len(value) != 4:
        _fail(where, 'expected [x, y, width, height]')
    for index, number in enumerate(value):
        _number(number, where, positive=index >= 2)


def _inside(inner, outer, where):
    x, y, w, h = inner
    ox, oy, ow, oh = outer
    if x < ox - 1e-7 or y < oy - 1e-7 or x + w > ox + ow + 1e-7 or y + h > oy + oh + 1e-7:
        _fail(where, 'outside containing area')


def _hash(value, where):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', value):
        _fail(where, 'expected SHA-256 hex digest')


def _list(value, where, nonempty=False):
    if not isinstance(value, list) or (nonempty and not value):
        _fail(where, 'expected ' + ('nonempty ' if nonempty else '') + 'array')


def _text(value, where, empty=False):
    _string(value, where, empty)
    if '$' in value or re.search(r'\\(?:[A-Za-z]+|[()\[\]])', value):
        _fail(where, 'math must be an equation block, not literal LaTeX')
    if any(c in value for c in '\r\n\t'):
        _fail(where, 'each text block must be one original line, without tabs')


def validate_page(page) -> None:
    """Accept only complete production page contracts, else raise ValueError."""
    _keys(page, 'version source_sha256 page_number size_mm assignment_id worker_id issues regions questions blocks', 'page')
    if type(page['version']) is not int or page['version'] not in (1,2):
        _fail('version', 'supported versions are integers 1 and 2')
    if type(page['page_number']) is not int or page['page_number'] < 1:
        _fail('page_number', 'expected integer >= 1')
    _hash(page['source_sha256'], 'source_sha256')
    for name in ('assignment_id', 'worker_id'):
        _string(page[name], name)
    size = page['size_mm']
    if not isinstance(size, list) or len(size) != 2:
        _fail('size_mm', 'expected [width, height]')
    for n in size:
        _number(n, 'size_mm', positive=True)
    bounds = [0, 0, *size]
    _list(page['issues'], 'issues')
    for issue in page['issues']:
        _string(issue, 'issues')
    if page['issues']:
        _fail('issues', 'unresolved issues block production acceptance')
    for key in ('regions', 'questions', 'blocks'):
        _list(page[key], key, nonempty=not(key=='blocks' and page['version']==2))
    ids = set()

    def identify(record, where):
        _string(record['id'], where + '.id')
        if record['id'] in ids:
            _fail(where, 'duplicate ID')
        ids.add(record['id'])
        _bbox(record['bbox_mm'], where)
        _inside(record['bbox_mm'], bounds, where)

    regions = {}
    for region in page['regions']:
        _keys(region, 'id bbox_mm question_ids', 'region')
        identify(region, 'region')
        _list(region['question_ids'], 'question_ids')
        for qid in region['question_ids']:
            _string(qid, 'question_ids')
        if len(set(region['question_ids'])) != len(region['question_ids']):
            _fail('region', 'duplicate question mapping')
        regions[region['id']] = region
    questions = {}
    for question in page['questions']:
        _keys(question, 'id region_id bbox_mm'+(' font_pt font_family content' if page['version']==2 else ''), 'question')
        identify(question, 'question')
        _string(question['region_id'], 'region_id')
        region = regions.get(question['region_id'])
        if region is None:
            _fail('question', 'unknown region_id')
        _inside(question['bbox_mm'], region['bbox_mm'], 'question')
        questions[question['id']] = question
        if page['version']==2:
            from question_contract import validate_content
            validate_content(question,ids)
    for rid, region in regions.items():
        expected = [q['id'] for q in page['questions'] if q['region_id'] == rid]
        if region['question_ids'] != expected:
            _fail('region ' + rid, 'question mapping and order must match questions exactly')
    extra = {'text': 'text font_pt font_family', 'line': 'runs font_pt font_family', 'equation': 'latex font_pt',
             'image': 'path sha256 role', 'box': 'title title_bbox_mm stroke_mm font_pt font_family',
             'rule': 'stroke_mm'}
    content_questions = set(questions) if page['version']==2 else set()
    for block in page['blocks']:
        if not isinstance(block, dict) or not isinstance(block.get('kind'), str) or block['kind'] not in extra:
            _fail('block', 'unsupported kind')
        kind = block['kind']
        _keys(block, 'id question_id kind bbox_mm ' + extra[kind] + (' tikz' if kind=='image' and 'tikz' in block else ''), 'block')
        identify(block, 'block')
        qid = block['question_id']
        if page['version']==2 and qid is not None:
            _fail('block','v2 question content belongs in questions.content, never page blocks')
        if qid is not None:
            _string(qid, 'question_id')
            if qid not in questions:
                _fail('block', 'unknown question_id')
            _inside(block['bbox_mm'], questions[qid]['bbox_mm'], 'block')
            if kind in ('text', 'line', 'equation', 'image'):
                content_questions.add(qid)
        if 'font_pt' in block:
            _number(block['font_pt'], 'font_pt', positive=True)
        if 'font_family' in block:
            _string(block['font_family'], 'font_family')
        if 'stroke_mm' in block:
            _number(block['stroke_mm'], 'stroke_mm', positive=True)
        if kind == 'text':
            _text(block['text'], 'text')
        elif kind == 'line':
            _list(block['runs'], 'runs', nonempty=True)
            for run in block['runs']:
                if not isinstance(run, dict) or run.get('kind') not in ('text', 'equation'):
                    _fail('run', 'expected text or equation')
                if run['kind'] == 'text':
                    _keys(run, 'kind text', 'run')
                    _text(run['text'], 'run.text')
                else:
                    _keys(run, 'kind latex', 'run')
                    _string(run['latex'], 'run.latex')
                    if any(c in run['latex'] for c in '$\r\n'):
                        _fail('run.latex', 'delimiter-free single-line LaTeX required')
        elif kind == 'equation':
            _string(block['latex'], 'latex')
            if '$' in block['latex'] or '\n' in block['latex'] or '\r' in block['latex']:
                _fail('latex', 'use delimiter-free equation source on one line')
        elif kind == 'image':
            _string(block['path'], 'path')
            _hash(block['sha256'], 'sha256')
            if qid is None or block['role'] != 'figure':
                _fail('image', 'only question-owned figures are accepted')
            if block['bbox_mm'][2] * block['bbox_mm'][3] >= size[0] * size[1] * 0.8:
                _fail('image', 'full-page raster replacement is forbidden')
        elif kind == 'box':
            _text(block['title'], 'title', empty=True)
            title_box = block['title_bbox_mm']
            if block['title']:
                _bbox(title_box, 'title_bbox_mm')
                _inside(title_box, bounds, 'title_bbox_mm')
                x, y, w, h = block['bbox_mm']
                tx, ty, tw, th = title_box
                if tx + tw <= x or tx >= x + w or ty + th <= y or ty >= y + h:
                    _fail('title_bbox_mm', 'title must intersect its box')
            elif title_box is not None:
                _fail('title_bbox_mm', 'empty title requires null')
    if content_questions != set(questions):
        _fail('questions', 'each question needs at least one content block')
