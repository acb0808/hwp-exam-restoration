"""Immutable conversion results and internal syntax nodes."""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    start: int
    end: int
    severity: str = 'error'
    line: int = 1
    column: int = 1


@dataclass(frozen=True)
class ConversionResult:
    latex: str
    ok: bool
    hwpeqn: str | None = None
    error: str | None = None
    diagnostics: tuple[Diagnostic, ...] = ()

    # Conversion itself does not invoke a renderer for this particular input.
    rendering_verified: bool = False

    @property
    def status(self) -> str:
        if not self.ok:
            return 'error'
        return 'review_required' if self.diagnostics else 'converted'

    def to_dict(self) -> dict:
        data = asdict(self)
        data['status'] = self.status
        data['diagnostics'] = list(data['diagnostics'])
        return data


@dataclass(frozen=True)
class Token:
    kind: str
    value: str
    start: int
    end: int


@dataclass(frozen=True)
class Node:
    kind: str
    value: str = ''
    children: tuple[Node, ...] = ()
    source_command: str = ''
    start: int | None = None
    end: int | None = None


class ParseError(Exception):
    def __init__(self, code: str, message: str, token: Token):
        super().__init__(message)
        self.code, self.message, self.token = code, message, token


class LatexConversionError(ValueError):
    """Raised by the string-returning API when conversion fails."""

    def __init__(self, result: ConversionResult):
        super().__init__(result.error)
        self.result = result

@dataclass(frozen=True)
class DocumentSegment:
    kind: str
    source: str
    start: int
    end: int
    display: bool = False
    result: ConversionResult | None = None


@dataclass(frozen=True)
class DocumentResult:
    source: str
    ok: bool
    segments: tuple[DocumentSegment, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()

    def to_dict(self) -> dict:
        data = asdict(self)
        for segment, serialized in zip(self.segments, data['segments']):
            if segment.result is not None:
                serialized['result'] = segment.result.to_dict()
        return data
