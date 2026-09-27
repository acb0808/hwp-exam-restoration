"""Expand compact source observations without repairing or accepting OCR content."""
from copy import deepcopy
import os
from pathlib import Path
import tempfile

import restoration_job as job_api


def _fields(value, required, optional, where):
    if not isinstance(value, dict) or not required <= value.keys() or not value.keys() <= required | optional:
        raise ValueError(where + ': unknown or missing fields')


def _blocks(content):
    if not isinstance(content, list): raise ValueError('content: expected array')
    for block in content:
        if not isinstance(block, dict): raise ValueError('content: expected object')
        yield block
        if block.get('kind') == 'box': yield from _blocks(block.get('content'))


def _figure_map(root, page, manifest, assignment, path):
    if path is None: return {}
    value = job_api.load_json(path)
    required = {'schema', 'job', 'page', 'assignment_id', 'worker_id', 'source_sha256', 'figures'}
    _fields(value, required, set(), 'figures')
    identity = {'schema':'restoration-figures/1', 'job':str(root), 'page':page,
                'assignment_id':assignment['assignment_id'], 'worker_id':assignment['worker_id'],
                'source_sha256':manifest['source']['sha256']}
    if any(value[k] != expected for k, expected in identity.items()):
        raise ValueError('figure_file_binding_mismatch')
    if type(value['page']) is not int or not isinstance(value['figures'], dict):
        raise ValueError('invalid_figure_file')
    return value['figures']


def _expand(root, page, draft, manifest, assignment, figures):
    _fields(draft, {'schema','regions','questions','issues'}, set(), 'draft')
    if draft['schema'] != 'restoration-draft/1': raise ValueError('invalid_draft_schema')
    if not isinstance(draft['regions'], list) or not isinstance(draft['questions'], list):
        raise ValueError('regions and questions: expected arrays')
    # Clone before adding defaults: callers may retain this dictionary as source evidence.
    value = deepcopy(draft); value.pop('schema')
    # Coordinates are observed by the worker. Only units/origin are mechanical.
    coordinate_data = None
    for kind in ('regions','questions'):
        for index, item in enumerate(value[kind]):
            if not isinstance(item, dict): continue  # authoritative fields check below
            where = f'draft.{kind}[{index}]'
            if 'bbox_px' in item:
                if 'bbox_mm' in item or 'coordinate_space' not in item:
                    raise ValueError(where + ': use bbox_mm OR bbox_px with explicit coordinate_space')
                from restoration_prepare import coordinate_context, resolve_pixel_box
                if coordinate_data is None: coordinate_data = coordinate_context(root, page)
                try:
                    resolved = resolve_pixel_box(coordinate_data,item['bbox_px'],item['coordinate_space'])
                except (ValueError,OSError,KeyError,TypeError) as exc:
                    raise ValueError(where + ': ' + str(exc)) from exc
                item.pop('bbox_px');item.pop('coordinate_space')
                item['bbox_mm'] = resolved['bbox_mm']
    for region in value['regions']:
        _fields(region, {'id','bbox_mm'}, {'question_ids'}, 'region')
    for question in value['questions']:
        _fields(question, {'id','region_id','bbox_mm','content'}, {'font_family','font_pt'}, 'question')
    reserved = set()
    for record in value['regions'] + value['questions']:
        identifier = record['id']
        if not isinstance(identifier, str) or not identifier.strip(): raise ValueError('id: expected nonempty string')
        reserved.add(identifier)
    for question in value['questions']:
        for block in _blocks(question['content']):
            if 'id' in block:
                if not isinstance(block['id'], str) or not block['id'].strip():
                    raise ValueError('content.id: expected nonempty string')
                reserved.add(block['id'])
    profile = job_api.load_json(Path(__file__).resolve().parents[1] / 'assets/templates/pdf2hwp-grid/profile.json')
    linked = _figure_map(root, page, manifest, assignment, figures)
    for q_index, question in enumerate(value['questions'], 1):
        question.setdefault('font_family', profile['body_font'])
        question.setdefault('font_pt', profile['body_font_pt'])
        for b_index, block in enumerate(_blocks(question['content']), 1):
            if 'id' not in block:
                base = f'draft-content-{q_index}-{b_index}'; candidate = base; suffix = 1
                while candidate in reserved:
                    candidate = f'{base}-{suffix}'; suffix += 1
                block['id'] = candidate; reserved.add(candidate)
            if 'figure_ref' in block:
                reference = block['figure_ref']
                if block.get('kind') != 'paragraph' or 'figure' in block:
                    raise ValueError('figure_ref: requires paragraph without figure')
                if not isinstance(reference, str) or not reference.strip() or reference not in linked:
                    raise ValueError('figure_ref: missing or unknown finalized figure')
                figure = linked[reference]
                from figure_provenance import artifact
                review = job_api._json(artifact(figure['tikz']['review']))
                if not isinstance(review, dict) or review.get('question_id') != question['id']:
                    raise ValueError('figure_ref: question binding mismatch')
                block.pop('figure_ref'); block['figure'] = deepcopy(figure)
                if assignment.get('workflow')=='single-review/1':
                    # MCP widths are final template millimetres. This intermediate
                    # page uses source units; project_pages applies the inverse.
                    region=next(r for r in value['regions'] if r['id']==question['region_id'])
                    target_width=profile['paper_mm'][0]-sum(profile['side_margins_mm'])
                    if len(value['regions'])==2:target_width=(target_width-profile['column_gap_mm'])/2
                    scale=region['bbox_mm'][2]/target_width
                    for key in ('size_mm','offset_mm'):
                        block['figure'][key]=[v*scale for v in block['figure'][key]]
    for region in value['regions']:
        expected = [q['id'] for q in value['questions'] if q['region_id'] == region['id']]
        if 'question_ids' in region and region['question_ids'] != expected:
            raise ValueError('region: explicit question_ids conflict with question order')
        region.setdefault('question_ids', expected)
    source_page = manifest['pages'][page - 1]
    value.update(version=2, source_sha256=manifest['source']['sha256'], page_number=page,
                 size_mm=[source_page['width_mm'], source_page['height_mm']],
                 assignment_id=assignment['assignment_id'], worker_id=assignment['worker_id'], blocks=[])
    return value


def compile_draft(job, page, draft, output, figures=None):
    """Write a new strict v2 result only after validation; never accept or mark reviewed."""
    receipt = {'status':'failed', 'output':None, 'errors':[], 'accepted':False, 'visual_status':'not_verified'}
    try:
        root = Path(job).resolve(strict=True); destination = Path(output).resolve()
        if destination.exists(): raise ValueError('choose_new_output_file')
        manifest = job_api._manifest(root)
        if type(page) is not int or not 1 <= page <= len(manifest['pages']):
            raise ValueError('page_out_of_range')
        assignments = [a for a in manifest['assignments'] if a['page'] == page]
        if len(assignments) != 1: raise ValueError('missing_page_assignment')
        source = draft if isinstance(draft, dict) else job_api.load_json(draft)
        value = _expand(root, page, source, manifest, assignments[0], figures)
        with tempfile.TemporaryDirectory(prefix='restoration-draft-') as temporary:
            candidate = Path(temporary) / 'candidate.json'; job_api.save_json(candidate, value)
            validation = job_api.validate_result(root, candidate)
            if validation['status'] != 'validated':
                receipt['errors'] = validation['errors']; return receipt
            data = candidate.read_bytes()
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation closes the overwrite race without replacing existing results.
        with destination.open('xb') as stream:
            try:
                stream.write(data); stream.flush(); os.fsync(stream.fileno())
            except BaseException:
                stream.close(); destination.unlink(missing_ok=True); raise
        receipt.update(status='compiled', output=str(destination))
    except (ValueError, KeyError, TypeError, OSError, RecursionError) as exc:
        receipt['errors'] = [{'code':'draft', 'location':'draft', 'message':str(exc)}]
        receipt['error_kind'] = 'missing_file' if isinstance(exc, FileNotFoundError) else 'environment' if isinstance(exc, OSError) else 'input'
    return receipt
