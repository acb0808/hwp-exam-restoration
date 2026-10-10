"""Bounded recursive-descent parser. No TeX execution or textual substitution."""
from __future__ import annotations

import re
from dataclasses import replace

from .model import Diagnostic, Node, ParseError, Token
from .symbols import ACCENTS, DECLARATIONS, DELIMITERS, FONTS, FUNCTIONS, SPACES, SYMBOLS, UNICODE, MATH_ALPHABETS

MAX_INPUT = 100_000
MAX_TOKENS = 30_000
MAX_DEPTH = 64


def tokenize(source: str) -> list[Token]:
    tokens = []
    i = 0
    while i < len(source):
        start, c = i, source[i]
        if c.isspace():
            i += 1
            continue
        if c == '%':
            newline = source.find('\n', i)
            i = len(source) if newline < 0 else newline + 1
            continue
        if c == '\\':
            i += 1
            if i == len(source):
                raise ParseError('syntax_error', 'Trailing backslash.', Token('cmd', '', start, i))
            if source[i].isascii() and source[i].isalpha():
                while i < len(source) and source[i].isascii() and source[i].isalpha():
                    i += 1
            else:
                i += 1
            tokens.append(Token('cmd', source[start + 1:i], start, i))
        else:
            i += 1
            tokens.append(Token('char', c, start, i))
        if len(tokens) > MAX_TOKENS:
            raise ParseError('resource_limit', f'Maximum token count is {MAX_TOKENS}.', tokens[-1])
    tokens.append(Token('eof', '', len(source), len(source)))
    return tokens


def seq(nodes: list[Node] | tuple[Node, ...]) -> Node:
    return Node('seq', children=tuple(nodes))


class Parser:
    def __init__(self, source: str, *, allow_layout_approximation: bool = False):
        self.source = source
        self.allow_layout_approximation = allow_layout_approximation
        self.tokens = tokenize(source)
        self.i = 0
        self.depth = 0
        self.warnings: list[Diagnostic] = []

    @property
    def token(self) -> Token:
        return self.tokens[self.i]

    def is_token(self, value: str, kind: str = 'char') -> bool:
        return self.token.kind == kind and self.token.value == value

    def take(self) -> Token:
        token = self.token
        if token.kind != 'eof':
            self.i += 1
        return token

    def fail(self, code: str, message: str, token: Token | None = None):
        raise ParseError(code, message, token or self.token)

    def expect(self, value: str, kind: str = 'char') -> Token:
        if not self.is_token(value, kind):
            self.fail('syntax_error', f'Expected {value!r}.')
        return self.take()

    def warn(self, code: str, message: str, token: Token):
        self.warnings.append(Diagnostic(code, message, token.start, token.end, 'warning',
                                       self.source.count('\n', 0, token.start) + 1,
                                       token.start - self.source.rfind('\n', 0, token.start)))

    def parse(self) -> Node:
        # Strip only a matched outer pair. Never erase embedded math boundaries.
        if self.is_token('$'):
            opening = self.take()
            double = self.is_token('$') and self.token.start == opening.end
            if double:
                self.take()
            body = self.sequence(stop_chars={'$'})
            self.expect('$')
            if double:
                self.expect('$')
        elif self.token.kind == 'cmd' and self.token.value in ('(', '['):
            opening = self.take()
            closing = ')' if opening.value == '(' else ']'
            body = self.sequence(stop_cmds={closing})
            self.expect(closing, 'cmd')
        else:
            body = self.sequence()
        if self.token.kind != 'eof':
            self.fail('syntax_error', 'Only one complete math expression is accepted; extra content or mismatched math delimiters.')
        def has_math(node):
            if node.kind in ('seq', 'group', 'font', 'math_alphabet'):
                return any(has_math(child) for child in node.children)
            return node.kind not in ('style_declaration', 'empty')
        if not has_math(body):
            self.fail('empty_input', 'The expression contains no math.')
        return body

    def sequence(self, stop_chars=frozenset(), stop_cmds=frozenset()) -> Node:
        nodes: list[Node] = []
        infix = None
        infix_token = None
        numerator = None
        while self.token.kind != 'eof':
            t = self.token
            if (t.kind == 'char' and t.value in stop_chars) or (t.kind == 'cmd' and t.value in stop_cmds):
                break
            if t.kind == 'cmd' and t.value in ('displaystyle', 'textstyle', 'scriptstyle', 'scriptscriptstyle'):
                if not self.allow_layout_approximation:
                    self.fail('unsupported_layout', 'TeX style requires native size settings, or explicit allow_layout_approximation=True.', t)
                self.take()
                nodes.append(Node('style_declaration', t.value, source_command=t.value, start=t.start, end=t.end))
                self.warn('style_not_preserved', 'TeX '+t.value+' is not retained; the native equation determines size and limit placement.', t)
                continue
            if t.kind == 'cmd' and t.value in ('over', 'atop', 'choose'):
                if infix is not None or not nodes:
                    self.fail('syntax_error', 'An infix fraction requires one numerator and one denominator.', t)
                infix_token = self.take()
                infix, numerator, nodes = infix_token.value, seq(nodes), []
                continue
            if t.kind == 'cmd' and t.value in DECLARATIONS:
                self.take()
                rest = self.sequence(stop_chars, stop_cmds)
                nodes.append(Node('font', DECLARATIONS[t.value], (rest,), source_command=t.value, start=t.start, end=t.end))
                break
            nodes.append(self.scripted(self.atom()))
        if infix:
            if not nodes:
                self.fail('syntax_error', 'Missing denominator.')
            kind = {'over': 'frac', 'atop': 'atop', 'choose': 'binom'}[infix]
            return seq([Node(kind, children=(numerator, seq(nodes)), source_command=infix_token.value,
                             start=infix_token.start, end=infix_token.end)])
        return seq(nodes)

    def argument(self, *, allow_fraction_field: bool = False) -> Node:
        if self.token.kind == 'eof' or (self.token.kind == 'char' and self.token.value in '}^_&$'):
            self.fail('missing_argument', 'Expected a math argument.')
        argument_commands = set(ACCENTS) | set(FONTS) | set(MATH_ALPHABETS) | {
            'displaystyle', 'textstyle', 'scriptstyle', 'scriptscriptstyle',
            'frac', 'dfrac', 'tfrac', 'binom', 'dbinom', 'tbinom', 'sqrt', 'text',
            'textrm', 'mbox', 'operatorname', 'left', 'begin', 'substack', 'not', 'pmod', 'pod',
            'overset', 'stackrel', 'xrightarrow', 'xleftarrow', 'xleftrightarrow',
            'big', 'Big', 'bigg', 'Bigg', 'bigl', 'bigr', 'Bigl', 'Bigr',
            'biggl', 'biggr', 'Biggl', 'Biggr'}
        if (self.token.kind == 'cmd' and self.token.value in argument_commands
                and not (allow_fraction_field and self.token.value == 'frac')):
            self.fail('ambiguous_argument', 'Put this argument-taking command inside braces; unbraced macro arguments are single TeX tokens.')
        node = self.atom()
        return node.children[0] if node.kind == 'group' else node

    def group(self) -> Node:
        self.expect('{')
        body = self.sequence(stop_chars={'}'})
        self.expect('}')
        return body

    def scripted(self, base: Node) -> Node:
        sub = sup = None
        while self.token.kind == 'char' and self.token.value in ('_', '^', "'"):
            t = self.take()
            if t.value == "'":
                primes = [Node('atom', 'prime')]
                while self.is_token("'"):
                    self.take()
                    primes.append(Node('atom', 'prime'))
                if sup is not None:
                    self.fail('duplicate_script', 'A superscript is already present; group the base explicitly.', t)
                sup = seq(primes)
            elif t.value == '_':
                if sub is not None:
                    self.fail('duplicate_script', 'Duplicate subscript.', t)
                sub = self.argument(allow_fraction_field=True)
            else:
                if sup is not None:
                    self.fail('duplicate_script', 'Duplicate superscript.', t)
                sup = self.argument(allow_fraction_field=True)
        if sub is None and sup is None:
            return base
        return Node('scripts', children=(base, sub or Node('empty'), sup or Node('empty')))

    def atom(self) -> Node:
        self.depth += 1
        try:
            if self.depth > MAX_DEPTH:
                self.fail('resource_limit', f'Maximum nesting depth is {MAX_DEPTH}.')
            return self._atom()
        finally:
            self.depth -= 1

    def _atom(self) -> Node:
        t = self.token
        if self.is_token('{'):
            return Node('group', children=(self.group(),))
        if t.kind == 'eof':
            self.fail('missing_argument', 'Unexpected end of input.')
        self.take()
        if t.kind == 'cmd':
            return replace(self.command(t), source_command=t.value, start=t.start, end=t.end)
        c = t.value
        if c in '}^_&$#"`':
            self.fail('syntax_error', f'Unexpected character {c!r}.', t)
        if c == '~':
            return Node('atom', '~')
        if c in UNICODE:
            if c == '∖':
                self.warn('unicode_symbol', 'Set difference uses Unicode U+2216; glyph metrics depend on the target font.', t)
            return Node('atom', UNICODE[c])
        if c.isascii() and (c.isalnum() or c in '+-=*/()[]<>.,:;!?|'):
            return Node('literal', c)
        # Korean and CJK text is supported by Hancom, but explicit text avoids keyword collisions.
        if '\uac00' <= c <= '\ud7a3' or '\u4e00' <= c <= '\u9fff':
            return Node('text', c)
        self.fail('unsupported_character', f'Unsupported character U+{ord(c):04X}: {c!r}.', t)

    def delimiter(self) -> str:
        t = self.take()
        key = ('\\' if t.kind == 'cmd' else '') + t.value
        if key not in DELIMITERS:
            self.fail('invalid_delimiter', f'Unsupported delimiter {key!r}.', t)
        return DELIMITERS[key]

    def raw_group(self) -> tuple[str, Token]:
        opening = self.expect('{')
        depth = 1
        while self.token.kind != 'eof':
            t = self.take()
            if t.kind == 'char' and t.value == '{':
                depth += 1
            elif t.kind == 'char' and t.value == '}':
                depth -= 1
                if depth == 0:
                    return self.source[opening.end:t.start], opening
            if depth > MAX_DEPTH:
                self.fail('resource_limit', 'Text group is too deeply nested.', t)
        self.fail('syntax_error', 'Unclosed text group.', opening)

    def text_argument(self) -> Node:
        raw, opening = self.raw_group()
        out = []
        i = 0
        while i < len(raw):
            c = raw[i]
            if c == '%':
                newline = raw.find('\n', i)
                i = len(raw) if newline < 0 else newline + 1
                continue
            if c == '\\':
                i += 1
                if i >= len(raw) or raw[i] not in '{}%&#_$ ':
                    self.fail('unsupported_text', 'Only escaped literal characters are supported inside text.', opening)
                out.append(raw[i])
            elif c in '$^_&#':
                self.fail('unsupported_text', f'Unescaped math/control character {c!r} is unsupported inside text.',
                          Token('char', c, opening.end+i, opening.end+i+1))
            elif c not in '{}':
                out.append(' ' if c == '~' else c)
            i += 1
        value = re.sub(r'\s+', ' ', ''.join(out))
        if '"' in value or any(ord(c) < 32 for c in value):
            self.fail('unsupported_text', 'Double quotes and control characters cannot be safely represented in HWP quoted text.', opening)
        return Node('text', value)

    def command(self, t: Token) -> Node:
        c = t.value
        if c == 'setminus':
            self.warn('unicode_symbol', 'Set difference uses Unicode U+2216; glyph metrics depend on the target font.', t)
        if c in MATH_ALPHABETS:
            argument = self.argument()
            mapping = MATH_ALPHABETS[c]
            def styled(node):
                if node.kind in ('seq', 'group'):
                    return Node(node.kind, children=tuple(styled(child) for child in node.children))
                if node.kind == 'literal' and node.value in mapping:
                    return Node('atom', mapping[node.value])
                self.fail('unsupported_math_alphabet', c+' supports plain uppercase Latin letters only; nested math and other characters are not approximated.', t)
            styled(argument)  # Validate without replacing the editable letters.
            self.warn('unicode_style_fallback', c+' uses Unicode mathematical letters; exact TeX font appearance and metrics are not retained.', t)
            return Node('math_alphabet', c, (argument,))
        if c == 'Re':
            self.warn('unicode_symbol', 'Real-part symbol uses Unicode ℜ rather than a documented HWP command.', t)
        if c in SYMBOLS:
            return Node('atom', SYMBOLS[c])
        if c in ('pmod', 'pod', 'bmod'):
            self.warn('spacing_approximation', 'TeX modulo spacing is approximated by HWP spaces.', t)
            if c == 'bmod':
                return Node('atom', '~ mod ~')
            body = self.argument()
            return Node('modulo', c, (body,))
        if c in FUNCTIONS:
            return Node('atom', c)
        if c in SPACES:
            self.warn('spacing_approximation', 'TeX spacing units are approximated by Hancom space/quarter-space units.', t)
            return Node('atom', SPACES[c])
        if c in ('frac', 'dfrac', 'tfrac', 'binom', 'dbinom', 'tbinom'):
            if c[0] in 'dt':
                self.warn('style_not_preserved', 'Explicit TeX display/text size is not retained by HWP equation script.', t)
            return Node('binom' if c.endswith('binom') else 'frac', children=(self.argument(), self.argument()))
        if c == 'sqrt':
            index = Node('empty')
            if self.is_token('['):
                self.take()
                index = self.sequence(stop_chars={']'})
                self.expect(']')
                if not index.children:
                    self.fail('missing_argument', 'A root index cannot be empty.', t)
            return Node('root', children=(index, self.argument(allow_fraction_field=True)))
        if c in ACCENTS:
            return Node('accent', ACCENTS[c], (self.argument(),))
        if c in FONTS:
            return Node('font', FONTS[c], (self.argument(),))
        if c in ('text', 'textrm', 'mbox', 'operatorname'):
            return self.text_argument()
        if c == 'left':
            left = self.delimiter()
            body = self.sequence(stop_cmds={'right'})
            self.expect('right', 'cmd')
            return Node('delimited', left + '\0' + self.delimiter(), (body,))
        if c in ('big', 'Big', 'bigg', 'Bigg', 'bigl', 'bigr', 'Bigl', 'Bigr', 'biggl', 'biggr', 'Biggl', 'Biggr'):
            self.warn('delimiter_size_approximation', 'TeX delimiter size levels map to the single HWP BIGG command.', t)
            return Node('atom', 'bigg ' + self.delimiter())
        if c in ('{', '}', 'lbrace', 'rbrace'):
            return Node('text', '{' if c in ('{', 'lbrace') else '}')
        if c in ('langle', 'rangle', 'lfloor', 'rfloor', 'lceil', 'rceil', '|'):
            return Node('atom', DELIMITERS['\\' + c])
        if c in ('%', '#', '&', '$', '_'):
            return Node('text', c)
        if c == 'not':
            return Node('prefix', 'not', (self.argument(),))
        if c in ('xrightarrow', 'xleftarrow', 'xleftrightarrow'):
            bottom = Node('empty')
            if self.is_token('['):
                self.take()
                bottom = self.sequence(stop_chars={']'})
                self.expect(']')
            top = self.argument()
            arrow = {'xrightarrow': 'rarrow', 'xleftarrow': 'larrow', 'xleftrightarrow': 'lrarrow'}[c]
            return Node('relation', arrow, (top, bottom))
        if c in ('overset', 'stackrel'):
            # \overset{rown}{AB} is the arc over AB, written without an arc package.
            ahead = self.tokens[self.i:self.i + 3]
            braced = (len(ahead) == 3 and ahead[0].value == '{' and ahead[0].kind == 'char'
                      and ahead[1].kind == 'cmd' and ahead[1].value == 'frown' and ahead[2].value == '}')
            if braced or (self.token.kind == 'cmd' and self.token.value == 'frown'):
                self.i += 3 if braced else 1
                return Node('accent', 'arch', (self.argument(),))
            top, base = self.argument(), self.argument()
            while base.kind in ('group', 'seq') and len(base.children) == 1:
                base = base.children[0]
            relations = {'=', '<', '>', 'LEQ', 'GEQ', 'neq', 'SIM', 'APPROX', 'SIMEQ', 'CONG', 'EQUIV',
                         'larrow', 'rarrow', 'lrarrow', 'LARROW', 'RARROW', 'LRARROW', 'mapsto'}
            if base.kind not in ('literal', 'atom') or base.value not in relations:
                self.fail('unsupported_annotation_base', 'HWP BUILDREL is supported only for a single relation or arrow.', t)
            return Node('overset', children=(base, top))
        if c == 'substack':
            self.expect('{')
            rows = []
            while True:
                row = self.sequence(stop_chars={'}'}, stop_cmds={'\\'})
                rows.append(Node('row', children=(row,)))
                if self.is_token('}'):
                    self.take()
                    break
                self.expect('\\', 'cmd')
                if self.is_token('}'):
                    self.take()
                    break
            return Node('environment', 'gathered:', tuple(rows))
        if c == 'begin':
            return self.environment(t)
        if c in ('displaystyle', 'textstyle', 'scriptstyle', 'scriptscriptstyle'):
            self.fail('unsupported_layout', 'TeX style declarations are not supported; use the native HWP equation size settings.', t)
        self.fail('unsupported_command', f'Unsupported LaTeX command: \\{c}.', t)

    def environment(self, opening: Token) -> Node:
        name, _ = self.raw_group()
        original_name = name
        name = {'align*': 'aligned', 'gather*': 'gathered', 'alignat*': 'alignedat'}.get(name, name)
        matrices = {'matrix', 'pmatrix', 'bmatrix', 'Bmatrix', 'vmatrix', 'Vmatrix', 'smallmatrix'}
        alignments = {'aligned', 'alignedat', 'split', 'gathered', 'array', 'cases'}
        wrappers = {'equation*', 'displaymath', 'math'}
        if name not in matrices | alignments | wrappers:
            self.fail('unsupported_environment', f'Unsupported environment: {name}.', opening)
        if name in wrappers:
            body = self.sequence(stop_cmds={'end'})
            self.end_environment(original_name)
            return Node('group', children=(body,))
        columns = ''
        align_pairs = None
        if name == 'array':
            columns, pos = self.raw_group()
            columns = self.array_columns(columns, pos)
        if name == 'alignedat':
            count, pos = self.raw_group()
            count = re.sub(r'%[^\n]*(?:\n|$)', '', count).strip()
            if len(count) > 3 or not count.isascii() or not count.isdigit() or not 1 <= int(count) <= 100:
                self.fail('syntax_error', 'alignedat needs a column-pair count from 1 to 100.', pos)
            align_pairs = int(count)
        if name == 'smallmatrix':
            self.warn('style_not_preserved', 'smallmatrix uses ordinary matrix size in HWP.', opening)
        rows: list[Node] = []
        cells: list[Node] = []
        while True:
            cells.append(self.sequence(stop_chars={'&'}, stop_cmds={'\\', 'end'}))
            if self.is_token('&'):
                self.take()
                continue
            if self.is_token('\\', 'cmd'):
                self.take()
                rows.append(Node('row', children=tuple(cells)))
                cells = []
                if self.is_token('[', 'char'):
                    spacing = self.row_spacing()
                    rows[-1] = replace(rows[-1], value=spacing)
                if self.is_token('end', 'cmd'):
                    break
                continue
            if self.is_token('end', 'cmd'):
                rows.append(Node('row', children=tuple(cells)))
                break
            self.fail('syntax_error', f'Expected a cell separator, row separator or \\end{{{name}}}.')
        self.end_environment(original_name)
        widths = {len(row.children) for row in rows}
        if len(widths) != 1:
            self.fail('irregular_matrix', 'All rows must have the same number of cells; add explicit empty cells.', opening)
        width = next(iter(widths))
        if columns and width != len(columns):
            self.fail('irregular_matrix', 'Array column count does not match its specification.', opening)
        if name == 'cases' and width > 2:
            self.fail('irregular_matrix', 'cases supports at most two columns.', opening)
        if name == 'gathered' and width != 1:
            self.fail('irregular_matrix', 'gathered contains one column.', opening)
        if align_pairs is not None and width != 2 * align_pairs:
            self.fail('irregular_matrix', 'alignedat column-pair count does not match its rows.', opening)
        if name == 'split' and width != 2:
            self.fail('irregular_matrix', 'split requires two alignment columns.', opening)
        if any(c in columns for c in 'lr'):
            self.warn('array_alignment_not_preserved', 'Row-based HWP matrix preserves row correspondence but uses centered columns; requested l/r alignment is retained in editor JSON only.', opening)
        return Node('environment', name + ':' + columns, tuple(rows))



    def row_spacing(self):
        opening = self.expect('[')
        if not self.allow_layout_approximation:
            self.fail('unsupported_layout', 'Explicit row spacing needs document layout, or allow_layout_approximation=True.', opening)
        while not self.is_token(']'):
            if self.token.kind == 'eof':
                self.fail('syntax_error', 'Unclosed row spacing.', opening)
            self.take()
        closing = self.take()
        raw = self.source[opening.end:closing.start].strip()
        match = re.fullmatch(r'([+-]?(?:[0-9]{1,4}(?:\.[0-9]{1,4})?|\.[0-9]{1,4}))\s*(pt|bp|mm|cm|in|em|ex|mu|pc|dd|cc|sp)', raw)
        if not match or abs(float(match[1])) > 1000:
            self.fail('unsupported_layout', 'Row spacing must be a literal TeX length with magnitude at most 1000; macros and expressions are not supported.', opening)
        self.warn('row_spacing_not_preserved', 'Requested row spacing '+raw+' is not represented in HWP script; native row spacing is used. Exact spacing requires document layout.',
                  Token('layout', raw, opening.start, closing.end))
        return match[1] + match[2]

    def array_columns(self, source: str, pos: Token) -> str:
        """Expand only l/c/r and bounded *{count}{spec}; never evaluate TeX."""
        source = re.sub(r'%[^\n]*(?:\n|$)', '', source)
        source = ''.join(source.split())
        i = 0

        def fail():
            self.fail('unsupported_array_spec', 'Use l/c/r columns or *{1..100}{spec}, at most 100 columns; rules and other layouts are unsupported.', pos)

        def read_spec(depth=0, nested=False):
            nonlocal i
            if depth > 32:
                fail()
            out = ''
            while i < len(source) and source[i] != '}':
                c = source[i]
                i += 1
                if c in 'lcr':
                    out += c
                elif c == '*':
                    if i >= len(source) or source[i] != '{':
                        fail()
                    end = source.find('}', i+1)
                    if end < 0:
                        fail()
                    count = source[i+1:end]
                    if len(count) > 3 or not count.isascii() or not count.isdigit() or not 1 <= int(count) <= 100:
                        fail()
                    i = end + 1
                    if i >= len(source) or source[i] != '{':
                        fail()
                    i += 1
                    repeated = read_spec(depth+1, True)
                    if len(repeated) * int(count) > 100:
                        fail()
                    out += repeated * int(count)
                else:
                    fail()
                if len(out) > 100:
                    fail()
            if not out:
                fail()
            if nested:
                if i >= len(source) or source[i] != '}':
                    fail()
                i += 1
            return out
        result = read_spec()
        if i != len(source):
            fail()
        return result

    def end_environment(self, name: str):
        t = self.expect('end', 'cmd')
        end, _ = self.raw_group()
        if end != name:
            self.fail('mismatched_environment', f'Expected \\end{{{name}}}, got \\end{{{end}}}.', t)

