"""Whole-submission validation with bounded recovery for useful source errors."""
import re

from restoration_answers import (AnswerEquationError, answer_error,
                                 split_answers_detailed, validate_answers)
from restoration_markdown import _escaped, parse_reading_markdown


class SubmissionError(ValueError):
    def __init__(self, errors, *, value=None):
        self.errors = errors
        # A fully parsed body may support more diagnostics, never acceptance.
        self.value = value
        self.answer_rows = []
        super().__init__('invalid_submission')


def _regions(text):
    """Return certain top-level question starts; never resync through a block."""
    lines = text.splitlines()
    regions = []
    column = None
    block = False
    for index, raw in enumerate(lines):
        line = raw.strip()
        if block:
            if line == ':::':
                block = False
            elif line.startswith((':::', '#')):
                break  # malformed nesting may have stolen a later terminator
            continue
        if line.startswith(':::') and line != ':::':
            block = True
        elif line in ('## left', '## right'):
            column = (index, line)
        else:
            heading = re.fullmatch(r'### ([A-Za-z0-9][A-Za-z0-9_-]*)(?: \?)?', line)
            if heading:
                regions.append({'start': index, 'question_id': heading.group(1), 'column': column})
    return lines, regions


def _source_equations(lines, start, end):
    """Locate delimiters in an already parsed body, without interpreting math."""
    equations = []
    for index in range(start, end):
        line = lines[index].strip()
        if line.startswith(('### ', '## ', ':::')) or re.fullmatch(r'<!-- incomplete: (.+) -->', line):
            continue
        if line.startswith('>'):
            line = line[1:].lstrip()
        if line.startswith('$$') and line.endswith('$$'):
            equations.append((line[2:-2], index + 1))
            continue
        cursor = 0
        while cursor < len(line):
            if line[cursor] != '$' or _escaped(line, cursor):
                cursor += 1
                continue
            closing = cursor + 1
            while closing < len(line) and (line[closing] != '$' or _escaped(line, closing)):
                closing += 1
            if closing == len(line):
                return None  # Unexpected source mismatch: do not guess.
            equations.append((line[cursor + 1:closing], index + 1))
            cursor = closing + 1
    return equations


def body_equation_source_map(text, value):
    """Map existing equation paths to original lines, with explicit fallback.

    Call lazily on a conversion failure and reuse the map for all failures.
    The entire per-question equation sequence must match before any line is
    marked exact, so repeated identical formulas cannot select the wrong line.
    This does not parse a body or convert an equation again.
    """
    from restoration_tools import equation_entries
    clean, _, _, _ = split_answers_detailed(text)
    lines, regions = _regions(clean)
    sources = {}
    for index, region in enumerate(regions):
        end = regions[index + 1]['start'] if index + 1 < len(regions) else len(lines)
        sources[region['question_id']] = (region['start'] + 1,
                                         _source_equations(lines, region['start'], end))
    result = {}
    for question in value['questions']:
        entries = list(equation_entries(question, question['id']))
        heading, found = sources.get(question['id'], (None, None))
        exact = found is not None and [latex for _, latex in entries] == [latex for latex, _ in found]
        for index, (location, _) in enumerate(entries):
            line = found[index][1] if exact else heading
            result[location] = {'field': 'content', 'line': line, 'source_line': line,
                                'source_location': 'equation' if exact else
                                'question_heading' if heading is not None else 'unavailable'}
    return result


def _body_error(exc, regions, fallback_line):
    match = re.search(r'Markdown line (\d+):', str(exc))
    line = int(match.group(1)) if match else fallback_line
    question = next((r for r in reversed(regions) if r['start'] < line), None)
    # Contract errors name a question index, unlike syntax errors.
    contract = re.search(r'reading\.questions\[(\d+)\]', str(exc))
    if contract and int(contract.group(1)) < len(regions):
        question = regions[int(contract.group(1))]
        line = question['start'] + 1
    qid = question['question_id'] if question else None
    message = re.sub(r'Markdown line \d+:', f'Markdown line {line}:', str(exc), count=1)
    return {'question_id': qid, 'field': 'content', 'line': line, 'source_line': line,
            'location': (qid + '.' if qid else '') + 'content', 'message': message,
            'hint': '원본 reading.md의 해당 줄에서 수식 구분자와 블록 문법을 확인하세요. 의미를 추측하여 수정하지 마세요.'}


def _body_errors(text, first_error, parse_args):
    lines, regions = _regions(text)
    original = _body_error(first_error, regions, max(1, len(lines)))
    if len(regions) < 2:
        return [original]
    errors = []
    for index, region in enumerate(regions):
        end = regions[index + 1]['start'] if index + 1 < len(regions) else len(lines)
        chunk = [''] * region['start'] + lines[region['start']:end]
        if region['column']:
            column_index, column_text = region['column']
            chunk[column_index] = column_text
        try:
            parse_reading_markdown('\n'.join(chunk), **parse_args)
        except ValueError as exc:
            # The chunk still has original source line numbers. Its contract
            # question index, however, is always zero.
            errors.append(_body_error(exc, [region], region['start'] + 1))
    if not any(e['line'] == original['line'] and e['message'] == original['message'] for e in errors):
        errors.insert(0, original)
    return errors


def validate_submission(text, *, page, worker_id, source_sha256, include_answers):
    """Return one validated body/answer pair or reject the entire submission.

    Valid bodies are parsed once. Only an already rejected body is reparsed by
    independent question to find further errors without guessing boundaries.
    """
    parse_args = dict(page=page, worker_id=worker_id, source_sha256=source_sha256)
    if not isinstance(text, str):
        raise SubmissionError([_body_error(ValueError('expected a Markdown string'), [], 1)])
    clean, answers, locations, errors = split_answers_detailed(text)
    value = None
    try:
        value = parse_reading_markdown(clean, **parse_args)
    except ValueError as exc:
        errors = _body_errors(clean, exc, parse_args) + errors
        # Diagnostics only: independently delimited equations remain checkable
        # even when paragraph layout is invalid. Never accept a recovered body.
        from restoration_compiler import _studio_equation
        from restoration_author_help import equation_guidance
        lines,regions=_regions(clean)
        for index,region in enumerate(regions):
            end=regions[index+1]['start'] if index+1<len(regions) else len(lines)
            equations=[]
            for source_index in range(region['start'],end):
                line_text=lines[source_index].strip()
                if '$$' in line_text and not re.fullmatch(r'\$\$[^$]+\$\$',line_text):
                    continue  # Invalid/ambiguous delimiters: keep Markdown error only.
                equations.extend(_source_equations(lines,source_index,source_index+1) or [])
            for latex,line in equations or []:
                if '[[box:' in latex:continue  # already a Markdown diagnostic
                try:_studio_equation(latex,region['question_id'])
                except (ValueError,KeyError,TypeError) as eq_error:
                    errors.append({'question_id':region['question_id'],'field':'content',
                        'line':line,'source_line':line,'latex':latex,'message':str(eq_error),
                        **equation_guidance(latex,eq_error)})
    rows = []
    if (include_answers or answers) and value is not None:
        _, regions = _regions(clean)
        starts = {r['question_id']: r['start'] + 1 for r in regions}
        for question in value['questions']:
            qid = question['id']
            source = locations.get(qid, {})
            row = answers.get(qid)
            if row is None:
                if qid not in locations:  # unclosed block already diagnosed
                    errors.append(answer_error(qid, 'answer_block', starts.get(qid, 1),
                                               'one_answer_for_each_question_required'))
                continue
            if set(row) != {'label', 'answer', 'reason'}:
                continue  # missing fields already have precise block diagnostics
            try:
                rows.extend(validate_answers({**value, 'questions': [question]}, {qid: row}))
            except AnswerEquationError as exc:
                for error in exc.errors:
                    line = source.get(error['field'], source.get('answer_block', starts.get(qid, 1)))
                    message = re.sub(r'Markdown line \d+:', f'Markdown line {line}:', error['message'], count=1)
                    errors.append({**error, 'line': line, 'source_line': line, 'message': message})
            except ValueError as exc:
                field = 'answer' if str(exc).startswith('choice_answer_') else 'answer_block'
                errors.append(answer_error(qid, field, source.get(field, starts.get(qid, 1)), str(exc)))
    if errors:
        raise SubmissionError(errors, value=value)
    return value, rows
