"""Coordinate-free, exact A/B transcription comparison; never accepts a page.

Only syntax and worker/source binding are checked here. Strict equation parsing,
figure provenance, layout validation and final source review remain downstream.
"""
from copy import deepcopy
import re

from question_contract import COMMON
from restoration_contract import _text as _contract_text, _number as _contract_number


def _fields(value, required, optional, where):
    if not isinstance(value, dict) or not required <= value.keys() or not value.keys() <= required | optional:
        raise ValueError(where + ': unknown or missing fields')


def _text(value, where, empty=False):
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise ValueError(where + ': expected string' + ('' if empty else ' (nonempty)'))


def _number(value, where, positive=False):
    _contract_number(value, where, positive)


def _runs(values, where, allow_empty=False):
    if not isinstance(values, list) or (not allow_empty and not values):
        raise ValueError(where + ': expected nonempty runs')
    for index, run in enumerate(values):
        location = f'{where}[{index}]'
        if not isinstance(run, dict):
            raise ValueError(location + ': expected run object')
        kind = run.get('kind')
        expected = {'text': {'kind', 'text'}, 'boxed_text': {'kind', 'text'}, 'equation': {'kind', 'latex'}, 'break': {'kind'}}.get(kind) if isinstance(kind, str) else None
        if expected is None or set(run) != expected:
            raise ValueError(location + ': expected exact text/equation/break fields')
        if kind == 'text':
            _contract_text(run['text'], location + '.text', empty=True)
        elif kind == 'boxed_text':
            _contract_text(run['text'], location + '.text')
            if len(run['text']) > 20 or any(char in run['text'] for char in '[]\\'):
                raise ValueError(location + ': boxed text needs 1..20 plain-text characters')
        elif kind == 'equation':
            _text(run['latex'], location + '.latex')
            if any(char in run['latex'] for char in '$\r\n'):
                raise ValueError(location + ': delimiter-free single-line latex required')


def _content(values, where, references, in_box=False):
    if not isinstance(values, list) or not values:
        raise ValueError(where + ': expected nonempty content')
    for index, block in enumerate(values):
        location = f'{where}[{index}]'
        if not isinstance(block, dict):
            raise ValueError(location + ': expected block object')
        kind = block.get('kind')
        required = {'paragraph': {'kind', 'runs'}, 'box': {'kind', 'title', 'content'},
                    'choices': {'kind', 'columns', 'rows'}}.get(kind) if isinstance(kind, str) else None
        if required is None or (in_box and kind != 'paragraph'):
            raise ValueError(location + ': unsupported semantic block')
        optional = COMMON | ({'figure_ref'} if kind == 'paragraph' else
                             {'stroke_mm', 'padding_mm'} if kind == 'box' else {'tab_stops_mm'})
        _fields(block, required, optional, location)
        for key in COMMON - {'align', 'font_family'}:
            if key in block:
                _number(block[key], location + '.' + key, key in {'font_pt', 'line_spacing_pct'})
        if 'font_family' in block:
            _text(block['font_family'], location + '.font_family')
        if block.get('align', 'LEFT') not in ('LEFT', 'CENTER', 'RIGHT', 'JUSTIFY'):
            raise ValueError(location + ': unsupported align')
        if kind == 'paragraph':
            if 'figure_ref' in block:
                reference = block['figure_ref']
                _text(reference, location + '.figure_ref')
                if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', reference):
                    raise ValueError(location + ': figure_ref must be an identifier, not a path')
                if reference in references:
                    raise ValueError(location + ': duplicate figure_ref')
                if block['runs'] != []:
                    raise ValueError(location + ': figure_ref requires empty anchor runs')
                references.append(reference)
            _runs(block['runs'], location + '.runs', 'figure_ref' in block)
        elif kind == 'choices':
            columns = block['columns']
            if type(columns) is not int or not 1 <= columns <= 5:
                raise ValueError(location + ': columns must be 1..5')
            if 'tab_stops_mm' in block:
                stops = block['tab_stops_mm']
                if not isinstance(stops, list) or len(stops) != columns - 1:
                    raise ValueError(location + ': one tab stop per subsequent column required')
                for stop in stops:
                    _number(stop, location + '.tab_stops_mm', positive=True)
                if stops != sorted(set(stops)):
                    raise ValueError(location + ': ascending distinct tab stops required')
            rows = block['rows']
            if not isinstance(rows, list) or not rows:
                raise ValueError(location + ': nonempty choice rows required')
            for row in rows:
                if not isinstance(row, list) or not 1 <= len(row) <= columns:
                    raise ValueError(location + ': invalid choice row')
                for choice in row:
                    _runs(choice, location + '.rows')
        else:
            _contract_text(block['title'], location + '.title', empty=True)
            if 'stroke_mm' in block:
                _number(block['stroke_mm'], location + '.stroke_mm', positive=True)
            if 'padding_mm' in block:
                padding = block['padding_mm']
                if not isinstance(padding, list) or len(padding) != 4:
                    raise ValueError(location + ': four padding values required')
                for item in padding:
                    _number(item, location + '.padding_mm')
            _content(block['content'], location + '.content', references, in_box=True)


def validate_reading(value, page, worker_id, source_sha256):
    """Validate without mutation or fabricated geometry; return the original value."""
    required = {'schema', 'page', 'worker_id', 'source_sha256', 'complete', 'issues', 'questions'}
    _fields(value, required, set(), 'reading')
    if value['schema'] != 'restoration-reading/1':
        raise ValueError('reading: invalid schema')
    if type(value['page']) is not int or value['page'] < 1 or type(page) is not int or value['page'] != page:
        raise ValueError('reading: page binding mismatch')
    _text(value['worker_id'], 'reading.worker_id')
    if value['worker_id'] != worker_id:
        raise ValueError('reading: worker binding mismatch')
    if not isinstance(value['source_sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', value['source_sha256']):
        raise ValueError('reading: invalid source_sha256')
    if value['source_sha256'] != source_sha256:
        raise ValueError('reading: source binding mismatch')
    if type(value['complete']) is not bool:
        raise ValueError('reading.complete: expected boolean')
    if not isinstance(value['issues'], list):
        raise ValueError('reading.issues: expected array')
    for issue in value['issues']:
        _text(issue, 'reading.issues')
    if not isinstance(value['questions'], list):
        raise ValueError('reading.questions: expected array')
    if value['complete'] and not value['questions']:
        raise ValueError('reading: complete transcription needs questions')
    identifiers = set()
    references = []
    for index, question in enumerate(value['questions']):
        where = f'reading.questions[{index}]'
        _fields(question, {'id', 'column', 'uncertain', 'content'}, set(), where)
        _text(question['id'], where + '.id')
        if question['id'] in identifiers:
            raise ValueError(where + ': duplicate question ID')
        identifiers.add(question['id'])
        if question['column'] not in ('left', 'right'):
            raise ValueError(where + ': column must be left or right')
        if type(question['uncertain']) is not bool:
            raise ValueError(where + ': uncertain must be boolean')
        _content(question['content'], where + '.content', references)
    return value


def _figure_ids(question):
    result = []
    def walk(blocks):
        for block in blocks:
            if 'figure_ref' in block:
                result.append(block['figure_ref'])
            if block['kind'] == 'box':
                walk(block['content'])
    if question is not None:
        walk(question['content'])
    return result


def compare_readings(a, b):
    """Compare literal content; agreement is not proof of OCR correctness."""
    for value in (a, b):
        if not isinstance(value, dict):
            raise ValueError('reading: expected object')
        validate_reading(value, value.get('page'), value.get('worker_id'), value.get('source_sha256'))
    if a['page'] != b['page'] or a['source_sha256'] != b['source_sha256']:
        raise ValueError('reading: A/B page or source mismatch')
    if a['worker_id'] == b['worker_id']:
        raise ValueError('reading: independent worker IDs required')
    orders = {label: [q['id'] for q in value['questions']] for label, value in [('a', a), ('b', b)]}
    maps = {label: {q['id']: q for q in value['questions']} for label, value in [('a', a), ('b', b)]}
    ids = list(dict.fromkeys(orders['a'] + orders['b']))
    page_issues = []
    for label, value in [('a', a), ('b', b)]:
        if not value['complete']:
            page_issues.append(label + ': incomplete')
        page_issues.extend(label + ': ' + issue for issue in value['issues'])
    if orders['a'] != orders['b']:
        page_issues.append('question_order_mismatch')
    common_a = [identifier for identifier in orders['a'] if identifier in maps['b']]
    common_b = [identifier for identifier in orders['b'] if identifier in maps['a']]
    relative_order_changed = common_a != common_b
    matching = []
    disputes = []
    figure_ids = []
    for identifier in ids:
        qa, qb = maps['a'].get(identifier), maps['b'].get(identifier)
        reasons = []
        for label, question in [('a', qa), ('b', qb)]:
            if question is None:
                reasons.append('missing_in_' + label)
            elif question['uncertain']:
                reasons.append('uncertain_' + label)
            for reference in _figure_ids(question):
                if reference not in figure_ids:
                    figure_ids.append(reference)
        if qa is not None and qb is not None:
            if relative_order_changed:
                reasons.append('question_order_mismatch')
            if qa['column'] != qb['column']:
                reasons.append('column_mismatch')
            if qa['content'] != qb['content']:
                reasons.append('content_mismatch')
            if _figure_ids(qa) != _figure_ids(qb):
                reasons.append('figure_refs_mismatch')
        if reasons:
            disputes.append({'question_id': identifier, 'reasons': reasons,
                             'a': deepcopy(qa), 'b': deepcopy(qb)})
        else:
            matching.append(identifier)
    return {'status': 'needs_resolution' if disputes or page_issues else 'agreement',
            'matching_ids': matching, 'disputes': disputes, 'page_issues': page_issues,
            'question_order': orders, 'figure_ids': figure_ids}
