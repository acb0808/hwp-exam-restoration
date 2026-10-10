"""Row plan chosen before the first export, from an estimate of each question's height.

A question that certainly overflows its share of the column's six rows gets more rows right away, which saves
the export that would only have measured the overflow (restoration_refit works from that measurement and
still corrects whatever this estimate gets wrong). Figures are exact; text is counted in lines.

The estimate is deliberately used as a lower bound: the default split is kept unless a question exceeds its
room even after CERTAIN_SHARE of the estimate, so pages that fitted before keep their layout
(calibration: est/measured over 1,000 exported cells, see the constants)."""
import math
import re

PT_MM = 25.4 / 72
WIDE_EM, NARROW_EM, SPACE_EM = 1.0, 0.55, 0.3   # Hangul and symbols, Latin letters and digits, spaces
EQUATION_EM = 0.52                               # per printed character of an equation
TALL_EQUATION = re.compile(r'\\(?:d?frac|sum|int|lim|prod|begin|sqrt\s*\[|binom)')
TALL_LINE = 1.45                                 # a line holding a fraction is this much taller
CERTAIN_SHARE = .95                             # est * this exceeded the room in none of 2,166 exported cells that fitted


def _equation_chars(latex):
    s = re.sub(r'\\(?:left|right|displaystyle|mathrm|text|mathbf|overline|bar|,|;|!|quad|qquad)', '', latex)
    s = re.sub(r'\\d?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}', lambda m: max(m.group(1), m.group(2), key=len), s)
    s = re.sub(r'\\[A-Za-z]+', 'x', s)           # a command prints about one symbol
    return len(re.sub(r'[\s{}^_\\]', '', s))


def _runs_width(runs, pt):
    """(widths in mm of the pieces between forced breaks, whether a tall equation is among them)"""
    em = pt * PT_MM; lines = [0.0]; tall = False
    for r in runs:
        kind = r['kind']
        if kind == 'break': lines.append(0.0)
        elif kind == 'equation':
            lines[-1] += _equation_chars(r['latex']) * EQUATION_EM * em; tall = tall or bool(TALL_EQUATION.search(r['latex']))
        else:
            for ch in r.get('text', ''):
                lines[-1] += (SPACE_EM if ch == ' ' else NARROW_EM if ch.isascii() else WIDE_EM) * em
    return lines, tall


def _text_height(runs, block, q, width):
    pt = block.get('font_pt', q['font_pt']); line = pt * PT_MM * block.get('line_spacing_pct', 150) / 100
    room = max(10.0, width - block.get('left_mm', 0) - block.get('right_mm', 0))
    pieces, tall = _runs_width(runs, pt)
    count = sum(max(1, math.ceil(w / room)) for w in pieces)
    return count * line * (TALL_LINE if tall else 1)


def content_height_mm(blocks, q, width):
    """Estimated printed height of a question's content in a cell of this usable width (mm)."""
    total = 0.0
    for block in blocks:
        kind = block['kind']; before, after = block.get('before_mm', 0), block.get('after_mm', 0)
        pt = block.get('font_pt', q['font_pt']); line = pt * PT_MM * block.get('line_spacing_pct', 150) / 100
        if kind == 'paragraph':
            figure = block.get('figure')
            has_text = any(r['kind'] in ('equation', 'boxed_text') or (r['kind'] == 'text' and r['text'].strip()) for r in block['runs'])
            if not figure: total += before + _text_height(block['runs'], block, q, width) + after
            else:
                # question_flow.contents: the text paragraph, then an anchor line reserving the picture's extent
                if has_text: total += before + _text_height(block['runs'], block, q, width)
                total += (0 if has_text else before) + line + max(after, figure['offset_mm'][1] + figure['size_mm'][1] + 1)
        elif kind == 'choices':
            rows = 0
            for row in block['rows']:
                span = (width - block.get('left_mm', 0) - block.get('right_mm', 0)) / block['columns']
                cells = [_runs_width(runs, pt) for runs in row]
                rows += max(1, max(math.ceil(sum(w) / max(span, 1) - .15) for w, _ in cells)) * (TALL_LINE if any(t for _, t in cells) else 1)
            total += before + rows * line + after
        elif kind == 'box':
            pad = block.get('padding_mm', [3, 2, 3, 2]); w = width - block.get('left_mm', 0) - block.get('right_mm', 0)
            inner = content_height_mm(block['content'], q, w - pad[0] - pad[2]) + pad[1] + pad[3]
            if block.get('title'): inner += pt * PT_MM * 1.6 + 1
            total += before + inner + block.get('after_mm', 2)
    return total


def figures_height_mm(blocks):
    return sum(b['figure']['size_mm'][1] for b in blocks if b.get('figure')) + sum(figures_height_mm(b['content']) for b in blocks if b['kind'] == 'box')


def plan_rows(cells):
    """Rows per question for one column, or None to keep the default split.
    cells: the column's questions in order as {'block_id','rows','cell_mm','available_mm','content_mm','figures_mm'}
    with content_mm estimated. A new split is returned only when a question certainly overflows its default
    room; it is the split restoration_refit would choose from the same numbers, figures left as they are."""
    if not any(c['content_mm'] * CERTAIN_SHARE > c['available_mm'] for c in cells): return None
    from restoration_refit import column_plan
    found = column_plan(cells, {})
    if found is None or found[0] == [c['rows'] for c in cells]: return None
    return found[0]
