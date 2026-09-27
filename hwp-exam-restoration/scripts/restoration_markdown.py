"""Small, loss-aware Markdown input for coordinate-free exam transcription.

This is deliberately not a general Markdown renderer. Unknown structural syntax
fails visibly; identities and source bindings are supplied only by the caller.
"""
from copy import deepcopy
import re

from restoration_reading import validate_reading

_ID = r'[A-Za-z0-9][A-Za-z0-9_-]*'
_ESCAPES = '\\$|#![]*_`<>'


def _error(line, message):
    raise ValueError(f'Markdown line {line}: {message}')


def _escaped(text, index):
    count = 0
    while index > 0 and text[index - 1] == '\\':
        count += 1
        index -= 1
    return count % 2 == 1


def _runs(text, line):
    runs, plain = [], []

    def flush():
        if plain:
            runs.append({'kind': 'text', 'text': ''.join(plain)})
            plain.clear()

    index = 0
    while index < len(text):
        char = text[index]
        if char == '\\' and index + 1 < len(text) and text[index + 1] in _ESCAPES:
            plain.append(text[index + 1])
            index += 2
            continue
        if char == '$':
            flush()
            end = index + 1
            while end < len(text) and (text[end] != '$' or _escaped(text, end)):
                end += 1
            if end == len(text) or not text[index + 1:end].strip():
                _error(line, 'empty or unclosed $LaTeX$ equation')
            latex = text[index + 1:end]
            if '$' in latex:
                _error(line, 'literal dollar inside an equation is unsupported')
            if '[[box:' in latex:
                _error(line, 'inline box must be outside math')
            runs.append({'kind': 'equation', 'latex': latex})
            index = end + 1
            continue
        if text.startswith('[[box:', index):
            flush()
            end = text.find(']]', index + 6)
            label = text[index + 6:end] if end >= 0 else ''
            if not re.fullmatch(r'[^\[\]$\\\r\n]{1,20}', label):
                _error(line, 'inline box requires 1..20 plain-text characters and closing ]]')
            runs.append({'kind': 'boxed_text', 'text': label})
            index = end + 2
            continue
        if char in '*`_' or text.startswith('~~', index) or (char == '<' and re.match(r'</?[A-Za-z!]', text[index:])):
            _error(line, 'unsupported Markdown/HTML; escape literal punctuation with a backslash')
        if char in '![' and re.match(r'!?\[[^\]]*\]\(', text[index:]):
            _error(line, 'links/images are unsupported; use a standalone figure marker')
        plain.append(char)
        index += 1
    flush()
    if not runs:
        _error(line, 'empty text or choice')
    return runs


def _paragraph(lines):
    runs = []
    for index, (number, text) in enumerate(lines):
        if index:
            runs.append({'kind': 'break'})
        if text.startswith('$$') or text.endswith('$$'):
            if len(lines) != 1 or not (text.startswith('$$') and text.endswith('$$')):
                _error(number, 'display equation must occupy one paragraph on one line')
            latex = text[2:-2]
            if not latex.strip() or '$' in latex:
                _error(number, 'invalid display equation')
            if '[[box:' in latex:
                _error(number, 'inline box must be outside math')
            runs.append({'kind': 'equation', 'latex': latex})
        else:
            if text.lstrip().startswith(('#', ':::', '```', '>', '|', '- ', '+ ')):
                _error(number, 'unsupported or misplaced structural syntax')
            runs.extend(_runs(text, number))
    return {'kind': 'paragraph', 'runs': runs}


def _choice_cells(text, line):
    cells, start, math = [], 0, False
    index = 0
    while index < len(text):
        if text[index] == '$' and not _escaped(text, index):
            math = not math
        if text[index] == '|' and not math and not _escaped(text, index):
            if index < 1 or index + 1 >= len(text) or text[index - 1] != ' ' or text[index + 1] != ' ':
                _error(line, 'choice separator requires spaces; escape literal pipe as \\|')
            cells.append(text[start:index - 1])
            index += 2
            start = index
        else:
            index += 1
    cells.append(text[start:])
    if any(not cell.strip() or cell.strip() == '|' for cell in cells):
        _error(line, 'empty choice cell; escape a literal separator as \\|')
    return [_runs(cell, line) for cell in cells]


def parse_reading_markdown(text, *, page, worker_id, source_sha256):
    """Parse the documented subset and return validated restoration-reading/1."""
    if not isinstance(text, str):
        _error(1, 'expected a Markdown string')
    lines = text.splitlines()
    reading = {'schema': 'restoration-reading/1', 'page': page, 'worker_id': worker_id,
               'source_sha256': source_sha256, 'complete': True, 'issues': [], 'questions': []}
    column, question, pending = None, None, []

    def flush():
        if pending:
            question['content'].append(_paragraph(pending))
            pending.clear()

    index = 0
    while index < len(lines):
        raw, number = lines[index], index + 1
        line = raw.strip()
        if not line:
            flush()
            index += 1
            continue
        incomplete = re.fullmatch(r'<!-- incomplete: (.+) -->', line)
        heading = re.fullmatch(r'### (' + _ID + r')( \?)?', line)
        if incomplete:
            flush()
            reading['complete'] = False
            reading['issues'].append(incomplete.group(1))
        elif line in ('## left', '## right'):
            flush()
            column = line[3:]
            question = None
        elif heading:
            flush()
            if column is None:
                _error(number, 'question needs ## left or ## right first')
            question = {'id': heading.group(1), 'column': column,
                        'uncertain': bool(heading.group(2)), 'content': []}
            reading['questions'].append(question)
        else:
            if question is None:
                _error(number, 'content needs a question heading')
            figure = re.fullmatch(r'!\[\]\(figure:(' + _ID + r')\)', line)
            directive = re.fullmatch(r'::: (box(?: .*)?|choices [1-5])', line)
            if figure:
                flush()
                question['content'].append({'kind': 'paragraph', 'runs': [], 'figure_ref': figure.group(1)})
            elif directive:
                flush()
                start = number
                name = directive.group(1)
                block_lines = []
                index += 1
                while index < len(lines) and lines[index].strip() != ':::':
                    block_lines.append((index + 1, lines[index]))
                    index += 1
                if index == len(lines):
                    _error(start, 'unclosed ::: block')
                if name.startswith('choices '):
                    rows = []
                    for row_number, row in block_lines:
                        if not row.strip():
                            _error(row_number, 'blank choice row is unsupported')
                        if row.lstrip().startswith((':::', '#', '>', '![')):
                            _error(row_number, 'nested structures are unsupported in choices')
                        cells = _choice_cells(row, row_number)
                        if len(cells) > int(name[-1]):
                            _error(row_number, 'choice row exceeds declared columns')
                        rows.append(cells)
                    if not rows:
                        _error(start, 'empty choices block')
                    question['content'].append({'kind': 'choices', 'columns': int(name[-1]), 'rows': rows})
                else:
                    contents, chunk = [], []
                    for row_number, row in block_lines + [(number, '')]:
                        if not row.strip():
                            if chunk:
                                contents.append(_paragraph(chunk))
                                chunk = []
                        else:
                            chunk.append((row_number, row))
                    if not contents:
                        _error(start, 'empty box')
                    title = name[4:] if name.startswith('box ') else ''
                    if title:
                        title_runs = _runs(title, start)
                        if len(title_runs) != 1 or title_runs[0]['kind'] != 'text':
                            _error(start, 'box title must be plain text')
                        title = title_runs[0]['text']
                    question['content'].append({'kind': 'box', 'title': title, 'content': contents})
            elif line.startswith('>'):
                flush()
                contents, chunk = [], []
                while index < len(lines) and lines[index].lstrip().startswith('>'):
                    quoted = lines[index].lstrip()[1:]
                    if quoted.startswith(' '):
                        quoted = quoted[1:]
                    if quoted:
                        chunk.append((index + 1, quoted))
                    elif chunk:
                        contents.append(_paragraph(chunk))
                        chunk = []
                    index += 1
                if chunk:
                    contents.append(_paragraph(chunk))
                if not contents:
                    _error(number, 'empty quoted box')
                question['content'].append({'kind': 'box', 'title': '', 'content': contents})
                continue
            else:
                pending.append((number, raw))
        index += 1
    flush()
    try:
        return validate_reading(reading, page, worker_id, source_sha256)
    except ValueError as error:
        _error(max(1, len(lines)), str(error))


def _escape(text):
    return ''.join('\\' + char if char in _ESCAPES else char for char in text)


def _write_runs(runs):
    return ''.join(_escape(run['text']) if run['kind'] == 'text' else
                   '$' + run['latex'] + '$' if run['kind'] == 'equation' else '\n'
                   if run['kind'] == 'break' else '[[box:' + run['text'] + ']]'
                   for run in runs)


def _write_block(block):
    kind = block['kind']
    expected = {'paragraph': {'kind', 'runs', 'figure_ref'}, 'box': {'kind', 'title', 'content'},
                'choices': {'kind', 'columns', 'rows'}}[kind]
    if set(block) - expected:
        raise ValueError('Markdown cannot encode presentation fields; retain the original reading')
    if kind == 'paragraph':
        if 'figure_ref' in block:
            return '![](figure:' + block['figure_ref'] + ')'
        return _write_runs(block['runs'])
    if kind == 'box':
        if any('figure_ref' in child for child in block['content']):
            raise ValueError('Markdown cannot encode a figure nested inside a box')
        return ('::: box' + (' ' + _escape(block['title']) if block['title'] else '') + '\n' +
                '\n\n'.join(_write_block(child) for child in block['content']) + '\n:::')
    if any(any(run['kind'] == 'break' for run in cell) for row in block['rows'] for cell in row):
        raise ValueError('Markdown choice cells cannot contain line breaks')
    return ('::: choices ' + str(block['columns']) + '\n' +
            '\n'.join(' | '.join(_write_runs(cell) for cell in row) for row in block['rows']) + '\n:::')


def reading_to_markdown(reading):
    """Serialize only representable readings; do not silently strip style/layout."""
    validate_reading(reading, reading.get('page'), reading.get('worker_id'), reading.get('source_sha256'))
    if reading['complete'] and reading['issues']:
        raise ValueError('Markdown issues require incomplete=false')
    if not reading['complete'] and not reading['issues']:
        raise ValueError('Markdown incomplete reading needs an explicit issue')
    parts = []
    for issue in reading['issues']:
        if '\n' in issue or '-->' in issue:
            raise ValueError('Markdown issue must be one line without a comment terminator')
        parts.append('<!-- incomplete: ' + issue + ' -->')
    column = None
    for question in reading['questions']:
        if not re.fullmatch(_ID, question['id']):
            raise ValueError('Markdown question ID must be an ASCII identifier')
        if question['column'] != column:
            column = question['column']
            parts.append('## ' + column)
        parts.append('### ' + question['id'] + (' ?' if question['uncertain'] else ''))
        parts.extend(_write_block(block) for block in question['content'])
    output = '\n\n'.join(parts) + '\n'
    # Reparse to detect unsupported edge cases instead of emitting misleading text.
    reparsed = parse_reading_markdown(output, page=reading['page'], worker_id=reading['worker_id'], source_sha256=reading['source_sha256'])
    if _canonical_runs(reparsed) != _canonical_runs(reading):
        raise ValueError('Markdown cannot represent this reading without changing its content')
    return output


def _canonical_runs(value):
    """Only merge adjacent text runs; never normalize text or equations."""
    if isinstance(value, dict):
        return {key: _canonical_runs(item) for key, item in value.items()}
    if isinstance(value, list):
        result = []
        for item in value:
            item = _canonical_runs(item)
            if isinstance(item, dict) and item.get('kind') == 'text' and set(item) == {'kind', 'text'}:
                if not item['text']:
                    continue
                if result and isinstance(result[-1], dict) and result[-1].get('kind') == 'text':
                    result[-1]['text'] += item['text']
                    continue
            result.append(item)
        return result
    return deepcopy(value)
