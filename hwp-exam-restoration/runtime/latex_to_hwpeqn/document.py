"""Explicit plain-text math extraction; never joins neighboring formulas.

All spans partition the unchanged input. Document diagnostics are absolute;
each conversion result keeps its original, segment-relative coordinates.
This is not a Markdown/HTML/TeX document parser.
"""
from __future__ import annotations
from dataclasses import replace
from bisect import bisect_right
from .model import Diagnostic, DocumentResult, DocumentSegment

MAX_DOCUMENT = 1_000_000
MAX_EQUATIONS = 1000


def convert_document(source: str, *, matrix_padding: int = 2, strict: bool = False, allow_layout_approximation: bool = False) -> DocumentResult:
    from . import convert
    if not isinstance(source, str):
        return DocumentResult('', False, diagnostics=(Diagnostic('invalid_input', 'Input must be a string.', 0, 0),))
    if type(allow_layout_approximation) is not bool:
        return DocumentResult(source, False, diagnostics=(Diagnostic('invalid_option', 'allow_layout_approximation must be a boolean.', 0, 0),))
    if type(strict) is not bool:
        return DocumentResult(source, False, diagnostics=(Diagnostic('invalid_option', 'strict must be a boolean.', 0, 0),))
    if type(matrix_padding) is not int or not 0 <= matrix_padding <= 8:
        return DocumentResult(source, False, diagnostics=(Diagnostic('invalid_option', 'matrix_padding must be an integer from 0 to 8.', 0, 0),))
    if len(source) > MAX_DOCUMENT:
        return DocumentResult(source, False, diagnostics=(Diagnostic('resource_limit', f'Maximum document length is {MAX_DOCUMENT}.', MAX_DOCUMENT, len(source)),))
    segments, diagnostics = [], []
    cursor = i = count = 0
    size = len(source)
    line_starts = [0] + [index+1 for index, c in enumerate(source) if c == '\n']

    def absolute(d, offset=0):
        start, end = d.start + offset, d.end + offset
        line = bisect_right(line_starts, start)
        return replace(d, start=start, end=end,
                       line=line, column=start - line_starts[line-1] + 1)

    def boundary_error(message, start, end, code='math_boundary'):
        diagnostics.append(absolute(Diagnostic(code, message, start, end)))

    def text_to(end):
        nonlocal cursor
        if cursor < end:
            segments.append(DocumentSegment('text', source[cursor:end], cursor, end))
        cursor = end

    while i < size:
        opening = closing = ''
        if source[i] == '\\':
            pair = source[i:i+2]
            if pair in (r'\(', r'\['):
                opening, closing = pair, (r'\)' if pair == r'\(' else r'\]')
            elif pair in (r'\)', r'\]'):
                boundary_error('Closing math delimiter has no opening delimiter.', i, i+2)
                i += 2
                continue
            else:
                i += min(2, size-i)
                continue
        elif source[i] == '$':
            if source.startswith('$$$', i):
                boundary_error('Three or more adjacent dollars are ambiguous.', i, i+3)
                break
            opening = closing = '$$' if source.startswith('$$', i) else '$'
        else:
            i += 1
            continue
        count += 1
        if count > MAX_EQUATIONS:
            boundary_error(f'Maximum equation count is {MAX_EQUATIONS}.', i, size, 'resource_limit')
            break
        text_to(i)
        start, j = i, i + len(opening)
        depth = 0
        end = None
        while j < size:
            c = source[j]
            if c == '%':
                newline = source.find('\n', j)
                j = size if newline < 0 else newline + 1
                continue
            if depth == 0 and source.startswith(closing, j):
                end = j + len(closing)
                break
            if c == '\\':
                # A closing delimiter of another kind must not consume a later formula.
                if depth == 0 and source[j:j+2] in (r'\)', r'\]', r'\(', r'\['):
                    break
                j += min(2, size-j)
                continue
            if depth == 0 and c == '$':
                break
            if c == '{':
                depth += 1
            elif c == '}':
                depth = max(0, depth-1)
            j += 1
        if end is None:
            boundary_error('Unclosed or mismatched math delimiter; remaining input is preserved.', start, min(start+len(opening), size))
            # Do not guess where a malformed expression ends.
            break
        original = source[start:end]
        result = convert(original, matrix_padding=matrix_padding, strict=strict,
                         allow_layout_approximation=allow_layout_approximation)
        segments.append(DocumentSegment('math', original, start, end,
                                        opening in ('$$', r'\['), result))
        diagnostics.extend(absolute(d, start) for d in result.diagnostics)
        cursor = i = end
    text_to(size)
    return DocumentResult(source, not any(d.severity == 'error' for d in diagnostics)
                          and all(s.result.ok for s in segments if s.result is not None),
                          tuple(segments), tuple(diagnostics))
