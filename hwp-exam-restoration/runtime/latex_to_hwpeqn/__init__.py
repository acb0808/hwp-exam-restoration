"""LaTeX math to Hancom equation script, without a Node.js dependency."""
from __future__ import annotations

from collections.abc import Iterable

from .model import ConversionResult, Diagnostic, DocumentResult, DocumentSegment, LatexConversionError, ParseError
from .document import convert_document
from .parser import MAX_INPUT, Parser
from .renderer import render
from .editor import convert_for_editor, convert_document_for_editor

__version__ = '0.3.0'
__all__ = ['convert', 'latex_to_hwpeqn', 'EquationConverter', 'ConversionResult', 'Diagnostic', 'LatexConversionError', 'convert_document', 'DocumentResult', 'DocumentSegment', 'convert_for_editor', 'convert_document_for_editor']


def convert(latex: str, *, matrix_padding: int = 1, strict: bool = False, allow_layout_approximation: bool = False) -> ConversionResult:
    """Convert one expression. Errors yield no script. Offsets index original Unicode text.

    matrix_padding is an explicit count (0..8) of HWP spaces on each side of a
    matrix cell. Lexical token separation is always enabled independently.
    """
    if not isinstance(latex, str):
        d = Diagnostic('invalid_input', 'Input must be a string.', 0, 0)
        return ConversionResult('', False, error=d.message, diagnostics=(d,))
    if type(allow_layout_approximation) is not bool:
        d = Diagnostic('invalid_option', 'allow_layout_approximation must be a boolean.', 0, 0)
        return ConversionResult(latex, False, error=d.message, diagnostics=(d,))
    if type(strict) is not bool:
        d = Diagnostic('invalid_option', 'strict must be a boolean.', 0, 0)
        return ConversionResult(latex, False, error=d.message, diagnostics=(d,))
    if type(matrix_padding) is not int or not 0 <= matrix_padding <= 8:
        d = Diagnostic('invalid_option', 'matrix_padding must be an integer from 0 to 8.', 0, 0)
        return ConversionResult(latex, False, error=d.message, diagnostics=(d,))
    if len(latex) > MAX_INPUT:
        d = Diagnostic('resource_limit', f'Maximum input length is {MAX_INPUT} characters.', MAX_INPUT, len(latex))
        return ConversionResult(latex, False, error=d.message, diagnostics=(d,))
    parser = None
    try:
        parser = Parser(latex, allow_layout_approximation=allow_layout_approximation)
        tree = parser.parse()
        script = render(tree, matrix_padding)
        if strict and parser.warnings:
            return ConversionResult(latex, False, error='Strict mode rejected conversion warnings.',
                                    diagnostics=tuple(parser.warnings))
        return ConversionResult(latex, True, script, diagnostics=tuple(parser.warnings))
    except ParseError as exc:
        t = exc.token
        d = Diagnostic(exc.code, exc.message, t.start, t.end, 'error',
                       latex.count('\n', 0, t.start) + 1, t.start - latex.rfind('\n', 0, t.start))
        return ConversionResult(latex, False, error=exc.message, diagnostics=(d,))
    except RecursionError:
        d = Diagnostic('resource_limit', 'Expression exceeds the recursion limit.', 0, len(latex))
        return ConversionResult(latex, False, error=d.message, diagnostics=(d,))


def latex_to_hwpeqn(latex: str, *, matrix_padding: int = 1, strict: bool = True, allow_layout_approximation: bool = False) -> str:
    """Return script or raise; strict defaults to True so warnings cannot disappear."""
    result = convert(latex, matrix_padding=matrix_padding, strict=strict,
                     allow_layout_approximation=allow_layout_approximation)
    if not result.ok:
        raise LatexConversionError(result)
    return result.hwpeqn  # type: ignore[return-value]


class EquationConverter:
    """Compatibility-shaped converter with no subprocesses or shared mutable cache."""
    def __init__(self, *, matrix_padding: int = 1, strict: bool = False, allow_layout_approximation: bool = False):
        self.matrix_padding = matrix_padding
        self.strict = strict
        self.allow_layout_approximation = allow_layout_approximation

    def convert(self, formula: str) -> ConversionResult:
        return convert(formula, matrix_padding=self.matrix_padding, strict=self.strict,
                       allow_layout_approximation=self.allow_layout_approximation)

    def convert_many(self, formulas: Iterable[str]) -> list[ConversionResult]:
        if isinstance(formulas, (str, bytes)):
            raise TypeError('convert_many expects an iterable of expressions, not a string.')
        return [self.convert(formula) for formula in formulas]
