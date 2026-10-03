"""Versioned editor handoff. Parsing is not an assertion of rendering fidelity."""
from __future__ import annotations

import re
from .model import Node
from .parser import Parser

SCHEMA = 'latex-to-hwpeqn/editor-equation/v1'
DOCUMENT_SCHEMA = 'latex-to-hwpeqn/editor-document/v1'


def _tree(node: Node) -> dict:
    result = {'type': node.kind}
    if node.source_command:
        result['source_command'] = node.source_command
        result['command_span'] = {'start': node.start, 'end': node.end}
    if node.kind == 'environment':
        name, columns = node.value.split(':')
        result.update(name=name,
                      column_alignment=[{'l':'left','c':'center','r':'right'}[c] for c in columns],
                      rows=[_tree(c) for c in node.children])
    elif node.kind == 'row':
        result['cells'] = [_tree(c) for c in node.children]
        if node.value:
            match = re.fullmatch(r'([+-]?[0-9.]+)([a-z]+)', node.value)
            result['after_spacing'] = {
                'value': match[1], 'unit': match[2], 'semantics': 'extra'}
    elif node.kind == 'delimited':
        result['left'], result['right'] = node.value.split('\0')
        result['children'] = [_tree(c) for c in node.children]
    else:
        if node.value or node.kind in ('text', 'literal', 'atom'):
            result['value'] = node.value
        if node.children or node.kind in ('seq', 'group'):
            result['children'] = [_tree(c) for c in node.children]
    return result


def _requirements(tree: dict, diagnostics: list[dict]) -> list[dict]:
    requirements = [{'feature':'tree_v1', 'path':'/tree'}]

    def walk(node, path):
        kind = node['type']
        command = node.get('source_command', '')
        if kind == 'environment':
            requirements.append({'feature':'environment', 'path':path,
                                 'name':node['name'],
                                 'column_alignment':node['column_alignment']})
        if kind == 'row' and 'after_spacing' in node:
            requirements.append({'feature':'extra_row_spacing', 'path':path,
                                 **node['after_spacing']})
        if kind in ('style_declaration', 'math_alphabet', 'font'):
            requirements.append({'feature':kind, 'path':path, 'value':node['value']})
        if command in ('dfrac','tfrac','dbinom','tbinom'):
            requirements.append({'feature':'math_style', 'path':path,
                                 'value':'display' if command[0]=='d' else 'text'})
        if command in (',', ':', ';', '!', ' ', 'quad', 'qquad', 'enspace', 'thinspace', 'negthinspace'):
            requirements.append({'feature':'tex_spacing', 'path':path, 'command':command})
        for key in ('children','rows','cells'):
            for index, child in enumerate(node.get(key, [])):
                walk(child, f'{path}/{key}/{index}')

    walk(tree, '/tree')
    # Unhandled approximations remain actionable; absence of a specialized
    # requirement must never make a fallback warning disappear.
    for d in diagnostics:
        requirements.append({'feature':'fallback_review', 'code':d['code'],
                             'source_span':{'start':d['start'],'end':d['end']}})
    for requirement in requirements:
        requirement['status'] = 'unverified'
    return requirements


def convert_for_editor(latex: str, *, matrix_padding: int = 2) -> dict:
    """Return JSON-compatible source, editable tree, requirements and HWP fallback.

    ok means the supported syntax was parsed. The receiving editor must
    acknowledge requirements and validate its rendering before declaring success.
    The tree uses HWP vocabulary for symbols; source_command preserves LaTeX
    command identity. This is an editor interchange format, not MathML.
    """
    from . import convert
    fallback = convert(latex, matrix_padding=matrix_padding,
                       allow_layout_approximation=True).to_dict()
    payload = {
        'schema': SCHEMA,
        'ok': fallback['ok'],
        'status': 'parsed' if fallback['ok'] else 'error',
        'source': {'format':'latex', 'text':fallback['latex'], 'span_unit':'unicode_codepoint'},
        'tree': None,
        'requirements': [],
        'requires_editor_acknowledgement': True,
        'rendering_verified': False,
        'editor_action': 'review_required' if fallback['ok'] else 'fix_input',
        'fallback_policy': 'explicit_review',
        'fallback': fallback,
        'diagnostics': fallback['diagnostics'],
    }
    if fallback['ok']:
        tree = Parser(latex, allow_layout_approximation=True).parse()
        payload['tree'] = _tree(tree)
        payload['requirements'] = _requirements(payload['tree'], fallback['diagnostics'])
    return payload


def convert_document_for_editor(source: str, *, matrix_padding: int = 2) -> dict:
    """Preserve independently delimited equations and unchanged plain text."""
    from .document import convert_document
    document = convert_document(source, matrix_padding=matrix_padding,
                                allow_layout_approximation=True)
    segments = []
    for segment in document.segments:
        item = {'kind':segment.kind, 'source':segment.source,
                'start':segment.start, 'end':segment.end, 'display':segment.display}
        if segment.kind == 'math':
            item['equation'] = convert_for_editor(segment.source, matrix_padding=matrix_padding)
        segments.append(item)
    from dataclasses import asdict
    return {'schema':DOCUMENT_SCHEMA, 'ok':document.ok,
            'status':'parsed' if document.ok else 'error',
            'source':document.source, 'span_unit':'unicode_codepoint', 'segments':segments,
            'editor_action':'review_required' if document.ok else 'fix_input',
            'rendering_verified':False, 'requires_editor_acknowledgement':True,
            'diagnostics':[asdict(d) for d in document.diagnostics]}
