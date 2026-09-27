"""HWP serialization. Every atom boundary is explicit; fractions are grouped."""
from __future__ import annotations

from .model import Node
from .symbols import MATH_ALPHABETS


def render(node: Node, matrix_padding: int = 1, _style: str = 'it') -> str:
    def r(n: Node) -> str:
        return render(n, matrix_padding, _style)

    def br(n: Node) -> str:
        return '{' + r(n) + '}'

    kind, value, children = node.kind, node.value, node.children
    if kind in ('atom', 'literal'):
        return value
    if kind in ('empty', 'style_declaration'):
        return ''
    if kind == 'seq':
        parts: list[str] = []
        digits = []
        def flush_number():
            if not digits:
                return
            token = ''.join(digits)
            if token.count('.') <= 1:
                parts.append(token)
            else:
                # Multiple punctuation dots are not a single decimal token.
                parts.extend(token)
            digits.clear()
        for child in children:
            if child.kind == 'style_declaration':
                continue
            if child.kind == 'literal' and child.value in '0123456789.':
                digits.append(child.value)
            else:
                flush_number()
                parts.append(r(child))
        flush_number()
        return ' '.join(parts)
    if kind == 'group':
        body = children[0]
        # A fraction/binomial already protects its complete extent.
        if body.kind == 'seq' and len(body.children) == 1 and body.children[0].kind in ('frac', 'atop'):
            return r(body)
        return br(body)
    if kind in ('frac', 'atop'):
        return '{' + br(children[0]) + (' over ' if kind == 'frac' else ' atop ') + br(children[1]) + '}'
    if kind == 'binom':
        return 'binom ' + br(children[0]) + ' ' + br(children[1])
    if kind == 'root':
        index, radicand = children
        return 'sqrt ' + (br(index) + ' of ' if index.kind != 'empty' else '') + br(radicand)
    if kind == 'scripts':
        base, sub, sup = children
        # Commands with arguments must be grouped before adding scripts.
        protected = base.kind in ('delimited', 'root', 'accent', 'binom', 'overset', 'environment', 'prefix', 'modulo')
        text = br(base) if protected else r(base)
        return text + ('_' + br(sub) if sub.kind != 'empty' else '') + ('^' + br(sup) if sup.kind != 'empty' else '')
    if kind == 'accent':
        return value + ' ' + br(children[0])
    if kind == 'math_alphabet':
        def letters(n):
            if n.kind in ('seq', 'group'):
                return ' '.join(letters(c) for c in n.children)
            return MATH_ALPHABETS[value][n.value]
        return '{' + letters(children[0]) + '}'
    if kind == 'font':
        return '{' + value + ' {' + render(children[0], matrix_padding, value) + '} ' + _style + '}'
    if kind == 'text':
        pieces = []
        words = value.split(' ')
        for index, word in enumerate(words):
            if word:
                pieces.append('"' + word + '"')
            if index < len(words) - 1:
                pieces.append('~')
        return '{rm ' + (' '.join(pieces) or '""') + ' ' + _style + '}'
    if kind == 'modulo':
        return '~ ( ' + ('mod ~ ' if value == 'pmod' else '') + br(children[0]) + ' )'
    if kind == 'prefix':
        return value + ' ' + r(children[0])
    if kind == 'delimited':
        left, right = value.split('\0')
        return 'left ' + left + ' ' + r(children[0]) + ' right ' + right
    if kind == 'relation':
        top, bottom = children
        if bottom.kind == 'empty':
            return '{buildrel ' + value + ' ' + br(top) + '}'
        return '{rel ' + value + ' ' + br(top) + ' ' + br(bottom) + '}'
    if kind == 'overset':
        return 'buildrel ' + r(children[0]) + ' ' + br(children[1])
    if kind == 'environment':
        name, columns = value.split(':')
        padding = '~' * matrix_padding if name in {'matrix', 'pmatrix', 'bmatrix', 'Bmatrix', 'vmatrix', 'Vmatrix', 'smallmatrix', 'array'} else ''

        def cell(n: Node) -> str:
            text = r(n) or '{}'
            return (padding + ' ' + text + ' ' + padding) if padding else text

        body = ' # '.join(' & '.join(cell(c) for c in row.children) for row in children)
        mapped = {'vmatrix': 'dmatrix', 'smallmatrix': 'matrix', 'array': 'matrix', 'aligned': 'eqalign',
                  'alignedat': 'eqalign', 'split': 'eqalign', 'gathered': 'pile'}
        if name == 'Bmatrix':
            return 'left { matrix {' + body + '} right }'
        if name == 'Vmatrix':
            return 'left VERT matrix {' + body + '} right VERT'
        return mapped.get(name, name) + ' {' + body + '}'
    raise AssertionError(f'Unknown node kind: {kind}')
