"""HWP serialization. Every atom boundary is explicit; fractions are grouped."""
from __future__ import annotations

import re

from .model import Node
from .symbols import GREEK, MATH_ALPHABETS

_TAIL_WORD = re.compile(r'(?:^|[^A-Za-z])([A-Za-z]{2,})$')

# Readability spacing follows Korean exam typesetting (2,260 scripts from published
# KICE/office exam HWP files): a quarter space (`) before a prime and before the dx of
# an integral, two after a comma, one after a function name taking a bare argument.
QUARTER = '`'
SPACED_FUNCTIONS = frozenset('sin cos tan cot sec csc cosec arcsin arccos arctan sinh cosh tanh coth log ln lg exp'.split())
INTEGRALS = frozenset(('int', 'oint', 'DINT', 'TINT', 'ODINT', 'OTINT'))


def _unwrap(n: Node) -> Node:
    while n.kind in ('group', 'seq') and len(n.children) == 1:
        n = n.children[0]
    return n


def _prime_count(sup: Node) -> int:
    """Number of primes when a superscript holds only primes (f', f'' or an explicit prime command)."""
    n = sup.children[0] if sup.kind == 'group' else sup
    items = n.children if n.kind == 'seq' else (n,)
    if items and all(_unwrap(c).kind == 'atom' and _unwrap(c).value == 'prime' for c in items):
        return len(items)
    return 0


def _function_name(n: Node) -> bool:
    n = n.children[0] if n.kind == 'scripts' else n
    return n.kind == 'atom' and n.value in SPACED_FUNCTIONS


def _is_integral(n: Node) -> bool:
    n = n.children[0] if n.kind == 'scripts' else n
    return n.kind == 'atom' and n.value in INTEGRALS


def _keyword_tail(text: str) -> bool:
    """The script so far ends in a Hancom keyword (TIMES, DIV, LEQ, DEG ...). Hancom prints a function name
    right after such a keyword in italics (2 TIMES sin x); a quarter space before the name keeps it upright."""
    word = _TAIL_WORD.search(text)
    return bool(word) and word.group(1) not in SPACED_FUNCTIONS and word.group(1).lower().removeprefix('var') not in GREEK


def _space(n: Node | None) -> bool:
    return n is not None and n.kind == 'atom' and bool(n.value) and set(n.value) <= set('`~ ')


def _bare_argument(n: Node) -> bool:
    """A function argument written without parentheses: sin x, log_2 8, cos 2x, sin theta."""
    if n.kind in ('group', 'frac', 'root', 'math_alphabet', 'font', 'accent'):
        return True
    if n.kind == 'scripts':
        return _bare_argument(n.children[0])
    return n.kind in ('atom', 'literal') and n.value[:1].isalnum() and n.value not in INTEGRALS


def render(node: Node, matrix_padding: int = 2, _style: str = 'it', _script: bool = False) -> str:
    def r(n: Node) -> str:
        return render(n, matrix_padding, _style, _script)

    def rs(n: Node) -> str:
        # Sub/superscripts stay tight (a_{i,j}, x^{2}).
        return '{' + render(n, matrix_padding, _style, True) + '}'

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
        items = [c for c in children if c.kind != 'style_declaration']
        for index, child in enumerate(items):
            following = items[index + 1] if index + 1 < len(items) else None
            if child.kind == 'literal' and child.value in '0123456789.':
                digits.append(child.value)
                continue
            flush_number()
            text = r(child)
            previous = items[index - 1] if index else None
            if _function_name(child) and parts and _keyword_tail(parts[-1]):
                text = QUARTER + text
            if not _script and following is not None and not _space(following):
                if child.kind == 'literal' and child.value == ',':
                    text += QUARTER * 2
                elif _function_name(child) and _bare_argument(following):
                    text += QUARTER
                elif (child.kind == 'literal' and child.value == 'd' and following.kind == 'literal'
                      and following.value.isalpha() and not _space(previous)
                      and any(_is_integral(c) for c in items[:index])):
                    text = QUARTER * 2 + ' ' + text
            parts.append(text)
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
        text += '_' + rs(sub) if sub.kind != 'empty' else ''
        primes = _prime_count(sup) if sup.kind != 'empty' else 0
        if primes:
            # A superscripted prime renders as a tiny raised tick in Hancom; the inline
            # glyph after a quarter space is what printed exams use (f`prime).
            return text + QUARTER + ' '.join(['prime'] * primes)
        if sup.kind != 'empty' and _unwrap(sup).kind == 'atom' and _unwrap(sup).value == 'CIRC':
            # 30^\circ is a degree: Hancom's DEG sits at the digits' height, a superscripted CIRC is a small detached ring.
            return text + ' DEG'
        return text + ('^' + rs(sup) if sup.kind != 'empty' else '')
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
