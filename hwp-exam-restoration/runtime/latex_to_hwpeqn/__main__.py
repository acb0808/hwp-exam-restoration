"""UTF-8 CLI. Exit 0=success, 1=conversion failure, 2=usage/IO."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
from . import __version__, convert, convert_document, convert_for_editor, convert_document_for_editor

MAX_BYTES = 8 * 1024 * 1024
MAX_BATCH = 1000

def read_input(path: str) -> str:
    if path == '-':
        data = sys.stdin.buffer.read(MAX_BYTES + 1)
    else:
        with Path(path).open('rb') as stream:
            data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError(f'Input exceeds {MAX_BYTES} bytes.')
    return data.decode('utf-8-sig')

def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='Convert LaTeX math to Hancom equation script. UTF-8 input/output.')
    parser.add_argument('formula', nargs='?', help='One formula; omit to read stdin.')
    parser.add_argument('--input', metavar='FILE', help='Read one expression from a UTF-8 file (or - for stdin).')
    parser.add_argument('--batch', metavar='FILE', help='UTF-8 JSON file containing a string array or {"formulas": [...]}; use - for stdin.')
    parser.add_argument('--document', metavar='FILE', help='Convert delimited formulas in plain text to separate JSON segments (or - for stdin).')
    parser.add_argument('--editor-json', action='store_true', help='Preserve editable structure and layout requirements in a versioned JSON handoff.')
    parser.add_argument('--json', action='store_true', help='Emit structured result including diagnostics.')
    parser.add_argument('--matrix-padding', type=int, choices=range(9), default=1,
                        help='Visible HWP spaces on each side of a matrix cell (default: 1).')
    parser.add_argument('--allow-layout-approximation', action='store_true', help='Allow style and row-spacing loss with explicit warnings; strict mode still rejects them.')
    parser.add_argument('--warnings-as-errors', action='store_true', help='Refuse approximations as well as errors.')
    parser.add_argument('--version', action='version', version=__version__)
    args = parser.parse_args(argv)
    if sum(value is not None for value in (args.formula, args.input, args.batch, args.document)) > 1:
        parser.error('Choose only one of formula, --input, --batch, or --document.')

    if args.editor_json and (args.warnings_as_errors or args.allow_layout_approximation):
        parser.error('--editor-json preserves layout; fallback strict/approximation flags are not applicable.')

    def run(formula):
        return convert(formula, matrix_padding=args.matrix_padding, strict=args.warnings_as_errors,
                       allow_layout_approximation=args.allow_layout_approximation)

    try:
        if args.document is not None:
            if args.editor_json:
                payload = convert_document_for_editor(read_input(args.document), matrix_padding=args.matrix_padding)
                print(json.dumps(payload, ensure_ascii=False, indent=2))
                return 0 if payload['ok'] else 1
            result = convert_document(read_input(args.document), matrix_padding=args.matrix_padding,
                                      strict=args.warnings_as_errors,
                       allow_layout_approximation=args.allow_layout_approximation)
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
            return 0 if result.ok else 1
        if args.batch is not None:
            payload = json.loads(read_input(args.batch))
            formulas = payload.get('formulas') if isinstance(payload, dict) else payload
            if not isinstance(formulas, list) or any(not isinstance(f, str) for f in formulas):
                raise ValueError('Batch input must be a JSON array of strings or {"formulas": [...strings]}.')
            if len(formulas) > MAX_BATCH:
                raise ValueError(f'Batch limit is {MAX_BATCH} expressions.')
            if args.editor_json:
                payloads = [convert_for_editor(f, matrix_padding=args.matrix_padding) for f in formulas]
                print(json.dumps({'results':payloads}, ensure_ascii=False, indent=2))
                return 0 if all(p['ok'] for p in payloads) else 1
            results = [run(f) for f in formulas]
            print(json.dumps({'results': [r.to_dict() for r in results]}, ensure_ascii=False, indent=2))
            return 0 if all(r.ok for r in results) else 1
        formula = args.formula if args.formula is not None else read_input(args.input or '-')
        if args.editor_json:
            payload = convert_for_editor(formula, matrix_padding=args.matrix_padding)
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0 if payload['ok'] else 1
        result = run(formula)
        if args.json:
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        else:
            if result.ok:
                print(result.hwpeqn)
            for diagnostic in result.diagnostics:
                print(f'{diagnostic.severity}: {diagnostic.code} at {diagnostic.line}:{diagnostic.column}: '
                      f'{diagnostic.message}', file=sys.stderr)
        return 0 if result.ok else 1
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        print(f'input_error: {exc}', file=sys.stderr)
        return 2

if __name__ == '__main__':
    raise SystemExit(main())

