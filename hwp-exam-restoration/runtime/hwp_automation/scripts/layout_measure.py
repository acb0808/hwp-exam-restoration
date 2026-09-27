"""Measure actual PDF output. Explicit regions do not imply automatic object mapping."""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
from itertools import combinations

PT_PER_MM = 72 / 25.4


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def number(value, *, minimum=0, maximum=10000):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('invalid_numeric_value')
    if not minimum <= value <= maximum:
        raise ValueError('numeric_value_out_of_range')
    return float(value)


def box(value):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError('bbox_requires_four_mm_coordinates')
    result = [number(v) for v in value]
    if result[2] <= result[0] or result[3] <= result[1]:
        raise ValueError('invalid_bbox_order')
    return result


def check_constraints(nodes, constraints):
    """Check only requested relations, returning unknown for unresolved mappings."""
    results = []
    if not isinstance(constraints, list) or len(constraints) > 1000 or any(not isinstance(c, dict) for c in constraints):
        raise ValueError('invalid_constraints')
    ids = set()
    for c in constraints:
        name = c.get('id')
        if not isinstance(name, str) or not name or name in ids:
            raise ValueError('constraint_id_required_and_unique')
        ids.add(name)
        kind = c.get('type')
        if kind not in {'same_page', 'min_gap', 'inside', 'no_overlap', 'clear_region'}:
            raise ValueError('unsupported_constraint: ' + str(kind))
        permitted = {'id', 'type'} | {
            'same_page': {'targets'}, 'min_gap': {'above', 'below', 'min_mm'},
            'inside': {'target', 'region', 'tolerance_mm'},
            'no_overlap': {'targets', 'tolerance_mm'},
            'clear_region': {'target'},
        }[kind]
        if set(c) - permitted:
            raise ValueError('unknown_constraint_properties')
        minimum = number(c.get('min_mm', 0))
        tolerance = number(c.get('tolerance_mm', 0.3), maximum=10)
        keys = ([c.get('target')] if kind == 'clear_region' else [c.get('above'), c.get('below')] if kind == 'min_gap' else
                [c.get('target'), c.get('region')] if kind == 'inside' else c.get('targets'))
        if not isinstance(keys, list) or not keys or any(not isinstance(k, str) or not k for k in keys):
            raise ValueError('constraint_targets_required')
        if len(set(keys)) != len(keys):
            raise ValueError('duplicate_constraint_targets')
        values = [nodes.get(key, {'status': 'missing'}) for key in keys]
        entry = {'id': name, 'type': kind, 'targets': keys}
        if kind == 'clear_region':
            value = values[0]
            state = value.get('status')
            entry['status'] = ('passed' if state == 'empty' else 'failed') if value.get('method') == 'raster_ink_bounds' and state in {'empty', 'measured'} else 'unknown'
            entry['scope'] = 'raster_threshold_235_at_144dpi'
            results.append(entry)
            continue
        if any(v.get('status') not in {'measured', 'region'} for v in values):
            entry.update(status='unknown', reason='target_not_uniquely_measured')
        elif kind == 'inside' and values[0]['status'] != 'measured':
            entry.update(status='unknown', reason='inside_target_requires_measured_content')
        elif kind == 'inside' and values[0].get('method') == 'raster_ink_bounds' and not _covers_with_guard(values[0].get('region_mm'), values[1]['bbox_mm'], max(1, tolerance)):
            entry.update(status='unknown', reason='raster_sample_requires_outer_guard_band')
        elif kind != 'inside' and any(v['status'] == 'region' for v in values):
            entry.update(status='unknown', reason='declared_region_is_not_measured_content')
        else:
            bounds = [box(v['bbox_mm']) for v in values]
            same_page = len({v['page'] for v in values}) == 1
            if kind == 'same_page':
                passed = same_page
            elif kind == 'min_gap':
                gap = bounds[1][1] - bounds[0][3] if same_page else None
                entry['measured_mm'] = gap
                entry['required_mm'] = minimum
                passed = gap is not None and gap >= minimum
            elif kind == 'inside':
                a, b = bounds
                passed = same_page and (a[0] >= b[0] - tolerance and a[1] >= b[1] - tolerance
                                       and a[2] <= b[2] + tolerance and a[3] <= b[3] + tolerance)
            else:
                collisions = []
                for i, j in combinations(range(len(values)), 2):
                    a, b = bounds[i], bounds[j]
                    if (values[i]['page'] == values[j]['page'] and
                        min(a[2], b[2]) - max(a[0], b[0]) > tolerance and
                        min(a[3], b[3]) - max(a[1], b[1]) > tolerance):
                        collisions.append([keys[i], keys[j]])
                entry['collisions'] = collisions
                passed = not collisions
            entry['status'] = 'passed' if passed else 'failed'
        results.append(entry)
    return results


def _covers_with_guard(sample, boundary, guard):
    if sample is None: return False
    a, b = box(sample), box(boundary)
    return a[0] <= b[0] - guard and a[1] <= b[1] - guard and a[2] >= b[2] + guard and a[3] >= b[3] + guard


def validate_selectors(selectors):
    """Validate syntax before any optional native session; page bounds need the PDF."""
    if not isinstance(selectors, list) or len(selectors) > 500:
        raise ValueError('selector_limit')
    ids = set()
    for s in selectors:
        if not isinstance(s, dict) or set(s) - {'id', 'text', 'page', 'region_mm', 'ink_region_mm'}:
            raise ValueError('unknown_selector_property')
        key = s.get('id')
        if not isinstance(key, str) or not key or key in ids: raise ValueError('selector_id_required_and_unique')
        ids.add(key)
        kinds = [k for k in ('text', 'region_mm', 'ink_region_mm') if k in s]
        if len(kinds) != 1: raise ValueError('one_selector_kind_required')
        if 'page' in s and (type(s['page']) is not int or s['page'] < 1): raise ValueError('invalid_page')
        if kinds[0] == 'text':
            if not isinstance(s['text'], str) or not s['text'] or len(s['text']) > 1000: raise ValueError('invalid_anchor_text')
        else:
            if 'page' not in s: raise ValueError('region_page_required')
            box(s[kinds[0]])
    return ids


def _ink_bounds(page, region):
    import fitz
    clip = fitz.Rect([v * PT_PER_MM for v in region])
    if (math.ceil(clip.width * 2) + 2) * (math.ceil(clip.height * 2) + 2) > 16_000_000:
        raise ValueError('region_pixel_limit')
    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=clip, colorspace=fitz.csGRAY, alpha=False)
    if pix.width * pix.height > 16_000_000:
        raise ValueError('region_pixel_limit')
    pixels = pix.samples
    left, top, right, bottom = pix.width, pix.height, -1, -1
    for y in range(pix.height):
        row = pixels[y * pix.stride:y * pix.stride + pix.width]
        dark = [x for x, shade in enumerate(row) if shade < 235]
        if dark:
            left = min(left, dark[0]); right = max(right, dark[-1])
            top = min(top, y); bottom = y
    if right < 0:
        return None
    # pix.x/y account for MuPDF's outward rounding of clip pixel boundaries.
    return [(pix.x + left) / 2 / PT_PER_MM, (pix.y + top) / 2 / PT_PER_MM,
            (pix.x + right + 1) / 2 / PT_PER_MM, (pix.y + bottom + 1) / 2 / PT_PER_MM]


def measure_pdf(pdf, selectors, constraints, *, provenance=None):
    validate_selectors(selectors)
    import fitz
    pdf = Path(pdf).resolve(strict=True)
    sha = digest(pdf)
    engine = 'pdf'
    if provenance is not None:
        if (provenance.get('pdf_sha256') != sha or provenance.get('status') != 'rendered'
                or not provenance.get('source_sha256') or provenance.get('native_reopen') != 'passed'):
            raise ValueError('render_provenance_mismatch')
        engine = provenance.get('engine', 'pdf')
    if not isinstance(selectors, list) or len(selectors) > 500:
        raise ValueError('selector_limit')
    # Validate relation syntax even when a PDF has no usable mappings.
    check_constraints({}, constraints)
    nodes = {}
    with fitz.open(pdf) as document:
        for s in selectors:
            key = s.get('id')
            if not isinstance(key, str) or not key or key in nodes:
                raise ValueError('selector_id_required_and_unique')
            if set(s) - {'id', 'text', 'page', 'region_mm', 'ink_region_mm'}:
                raise ValueError('unknown_selector_property')
            kinds = [k for k in ('text', 'region_mm', 'ink_region_mm') if k in s]
            if len(kinds) != 1:
                raise ValueError('one_selector_kind_required')
            page_number = s.get('page')
            if page_number is not None and (type(page_number) is not int or not 1 <= page_number <= len(document)):
                raise ValueError('page_out_of_range')
            if kinds[0] == 'text':
                if not isinstance(s['text'], str) or not s['text'] or len(s['text']) > 1000:
                    raise ValueError('invalid_anchor_text')
                hits = []
                for i in ([page_number - 1] if page_number else range(len(document))):
                    for rect in document[i].search_for(s['text']):
                        hits.append({'page': i + 1, 'bbox_mm': [v / PT_PER_MM for v in rect]})
                nodes[key] = {'status': 'missing' if not hits else 'ambiguous', 'matches': hits,
                              'method': 'pdf_text_search'}
                if len(hits) == 1:
                    nodes[key].update(hits[0], status='measured')
            else:
                if page_number is None:
                    raise ValueError('region_page_required')
                region = box(s[kinds[0]])
                page = document[page_number - 1]
                if region[2] > page.rect.width / PT_PER_MM or region[3] > page.rect.height / PT_PER_MM:
                    raise ValueError('region_outside_page')
                if kinds[0] == 'region_mm':
                    nodes[key] = {'status': 'region', 'page': page_number, 'bbox_mm': region,
                                  'method': 'declared_boundary'}
                else:
                    bounds = _ink_bounds(page, region)
                    nodes[key] = {'status': 'measured' if bounds else 'empty', 'page': page_number,
                                  'bbox_mm': bounds, 'region_mm': region, 'method': 'raster_ink_bounds',
                                  'scope': 'all_visible_ink_in_explicit_region; identity_not_inferred'}
        count = len(document)
    results = check_constraints(nodes, constraints)
    statuses = {r['status'] for r in results}
    status = 'failed' if 'failed' in statuses else 'unknown' if 'unknown' in statuses or not results else 'passed'
    return {'schema': 'hwp-layout-measure/1', 'pdf': str(pdf), 'pdf_sha256': sha,
            'engine': engine, 'provenance': provenance, 'page_count': count, 'nodes': nodes,
            'constraints': results, 'constraints_status': status, 'visual_status': 'not_tested',
            'scope': 'requested_relations_only', 'unmeasured': ['equation_semantics', 'whole_document_layout']}
