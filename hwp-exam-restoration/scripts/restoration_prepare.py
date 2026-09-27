"""Batch source views and environment facts without semantic reconstruction."""
from __future__ import annotations

import hashlib
import io
import math
from pathlib import Path
import re
import runpy
import sys

from restoration_job import _artifact, _manifest, digest, load_json, save_json


def _coordinate_frame(root, page, reference, source_image):
    """Resolve only original-page pixels or an unchanged registered navigation crop."""
    width, height = source_image.size
    if reference == 'page':
        return 0, 0, width, height
    if not isinstance(reference, str) or not Path(reference).is_absolute():
        raise ValueError('coordinate_space_requires_page_or_registered_crop_absolute_path')
    target = Path(reference).resolve(strict=True)
    if not target.is_relative_to(root):
        raise ValueError('coordinate_crop_must_be_inside_job')
    index = load_json(root / 'inputs' / 'index.json')
    if (not isinstance(index, dict) or index.get('schema') != 'restoration-inputs/1'
            or not isinstance(index.get('pages'), list)
            or any(not isinstance(entry, dict)
                   or type(entry.get('page')) is not int
                   or not isinstance(entry.get('navigation_crops'), list)
                   for entry in index['pages'])):
        raise ValueError('invalid_navigation_crop_index')
    candidates = [crop for entry in index.get('pages', []) if entry.get('page') == page
                  for crop in entry.get('navigation_crops', [])
                  if isinstance(crop, dict) and isinstance(crop.get('path'), str)
                  and Path(crop['path']).resolve() == target]
    if len(candidates) != 1:
        raise ValueError('coordinate_crop_not_registered_for_page')
    crop = candidates[0]
    edges = crop.get('pixel_box')
    if (not isinstance(edges, list) or len(edges) != 4
            or any(type(n) is not int for n in edges)):
        raise ValueError('invalid_navigation_crop_pixel_box')
    left, top, right, bottom = edges
    if not (0 <= left < right <= width and 0 <= top < bottom <= height):
        raise ValueError('invalid_navigation_crop_pixel_box')
    raw = target.read_bytes()
    if hashlib.sha256(raw).hexdigest() != crop.get('sha256'):
        raise ValueError('navigation_crop_hash_mismatch')
    from PIL import Image
    with Image.open(io.BytesIO(raw)) as opened:
        opened.load()
        expected = source_image.crop(tuple(edges))
        if (opened.size != expected.size or opened.mode != expected.mode
                or opened.tobytes() != expected.tobytes()
                or opened.getpalette() != expected.getpalette()):
            raise ValueError('navigation_crop_does_not_match_source_pixels')
    return left, top, right - left, bottom - top


def coordinate_context(job, page):
    """Load immutable source evidence once for a batch of coordinate conversions."""
    root = Path(job).resolve(strict=True)
    manifest = _manifest(root)
    if type(page) is not int or not 1 <= page <= len(manifest['pages']):
        raise ValueError('page_out_of_range')
    record = manifest['pages'][page - 1]
    source = _artifact(root, record['image'])
    raw = source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != record['image']['sha256']:
        raise ValueError('artifact_hash_mismatch: ' + record['image']['path'])
    from PIL import Image
    with Image.open(io.BytesIO(raw)) as opened:
        opened.load()
        image = opened.copy()
    width, height = image.size
    sx, sy = record['width_mm'] / width, record['height_mm'] / height
    return {'root': root, 'page': page, 'manifest': manifest, 'record': record,
            'source': source, 'image': image, 'scale': (sx, sy), 'frames': {}}


def resolve_pixel_box(context, bbox_px, coordinate_space):
    """Convert observed pixel edges, never infer or round semantic source bounds."""
    if (not isinstance(bbox_px, list) or len(bbox_px) != 4
            or any(type(n) not in (int, float) or not math.isfinite(n) for n in bbox_px)):
        raise ValueError('invalid_source_view_bbox_px')
    if not isinstance(coordinate_space, str):
        raise ValueError('coordinate_space_requires_page_or_registered_crop_absolute_path')
    frames = context['frames']
    if coordinate_space not in frames:
        frames[coordinate_space] = _coordinate_frame(
            context['root'], context['page'], coordinate_space, context['image'])
    ox, oy, width, height = frames[coordinate_space]
    x, y, w, h = bbox_px
    if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > width or y + h > height:
        raise ValueError('source_view_outside_coordinate_space_or_empty')
    sx, sy = context['scale']
    return {'bbox_mm': [(x + ox) * sx, (y + oy) * sy, w * sx, h * sy],
            'page_bbox_px': [x + ox, y + oy, w, h],
            'coordinate_space_origin_px': [ox, oy]}


def source_views(job, page, rows, output):
    """Crop original pixels using page mm or explicitly referenced pixel boxes.

    Coordinates denote pixel edges. Crop-local (x, y) maps to page millimetres
    using ``offset + (x, y) * scale`` from ``crop_pixel_to_page_mm``.
    This prepares inspection evidence, never an accepted replacement diagram.
    """
    context = coordinate_context(job, page)
    root, manifest = context['root'], context['manifest']
    if not isinstance(rows, list) or not rows:
        raise ValueError('source_views_requires_nonempty_rows')
    destination = Path(output)
    if not destination.is_absolute():
        destination = root / destination
    destination = destination.resolve()
    if not destination.is_relative_to(root) or destination == root:
        raise ValueError('source_views_output_must_be_inside_job')
    if destination.exists():
        raise ValueError('source_views_output_must_be_new')

    record, source, image = context['record'], context['source'], context['image']
    width, height = image.size
    mm_width, mm_height = record['width_mm'], record['height_mm']
    sx, sy = mm_width / width, mm_height / height
    prepared, identifiers = [], set()
    for row in rows:
        if not isinstance(row, dict) or set(row) not in (
                {'id', 'bbox_mm'}, {'id', 'bbox_px', 'coordinate_space'}):
            raise ValueError('source_view_requires_id_and_bbox_mm_or_bbox_px_with_coordinate_space')
        identifier = row['id']
        if (not isinstance(identifier, str)
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', identifier)
                or re.fullmatch(r'(?i:con|prn|aux|nul|com[1-9]|lpt[1-9])', identifier)):
            raise ValueError('invalid_source_view_id')
        if identifier.casefold() in identifiers:
            raise ValueError('duplicate_source_view_id')
        identifiers.add(identifier.casefold())
        unit = 'bbox_mm' if 'bbox_mm' in row else 'bbox_px'
        box = row[unit]
        if (not isinstance(box, list) or len(box) != 4
                or any(type(n) not in (int, float) or not math.isfinite(n) for n in box)):
            raise ValueError('invalid_source_view_' + unit)
        x, y, w, h = box
        request = {}
        if unit == 'bbox_px':
            reference = row['coordinate_space']
            resolved = resolve_pixel_box(context, box, reference)
            px, py, w, h = resolved['page_bbox_px']
            requested_mm = resolved['bbox_mm']
            left, top = math.floor(px), math.floor(py)
            right, bottom = math.ceil(px + w), math.ceil(py + h)
            request = {'requested_bbox_px': list(box), 'coordinate_space': reference,
                       'coordinate_space_origin_px': resolved['coordinate_space_origin_px']}
        else:
            if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > mm_width or y + h > mm_height:
                raise ValueError('source_view_outside_page_or_empty')
            requested_mm = list(box)
            # Round outwards to retain every source pixel touched by the request.
            left, top = math.floor(x / sx), math.floor(y / sy)
            right, bottom = min(width, math.ceil((x + w) / sx)), min(height, math.ceil((y + h) / sy))
        if not (0 <= left < right <= width and 0 <= top < bottom <= height):
            raise ValueError('source_view_pixel_bounds_invalid')
        offset = [left * sx, top * sy]
        prepared.append({'id': identifier, **request, 'requested_bbox_mm': requested_mm,
                         'bbox_px': [left, top, right - left, bottom - top],
                         'pixel_edges': [left, top, right, bottom],
                         'actual_bbox_mm': offset + [(right - left) * sx, (bottom - top) * sy],
                         'crop_pixel_to_page_mm': {'scale': [sx, sy], 'offset': offset}})

    # No output is created until all rows and original evidence have passed.
    destination.mkdir(parents=True, exist_ok=False)
    for view in prepared:
        target = destination / (view['id'] + '.png')
        image.crop(tuple(view['pixel_edges'])).save(target, format='PNG')
        view['image'] = {'path': str(target), 'sha256': digest(target)}
    result = {'schema': 'restoration-source-views/1', 'page_number': page,
              'source_sha256': manifest['source']['sha256'],
              'source_image': {'path': str(source), 'sha256': record['image']['sha256']},
              'page_size_mm': [mm_width, mm_height], 'page_size_px': [width, height],
              'mm_per_pixel': [sx, sy], 'pixels_per_mm': [1 / sx, 1 / sy],
              'coordinate_convention': 'pixel_edges; bbox is [x,y,width,height]',
              'views': prepared, 'visual_status': 'not_verified',
              'provenance_scope': 'unchanged_source_pixels; inspection_only; not_semantic_geometry'}
    save_json(destination / 'index.json', result)
    return result


def environment_info(engine=None):
    """Report bundled renderer/engine presence; never compile or claim quality."""
    renderer = Path(__file__).resolve().parents[1] / 'runtime' / 'tikz_render.py'
    runtime = runpy.run_path(str(renderer))
    status = runtime['doctor'](engine)
    executable = status.get('engine')
    engine_record = None
    if executable:
        try:
            engine_record = {'path': executable, 'sha256': digest(executable)}
        except OSError as exc:
            status.update(status='blocked', error='tikz_engine_hash_unavailable: ' + str(exc))
    return {**status, 'schema': 'restoration-environment/1',
            'python': str(Path(sys.executable).resolve()),
            'renderer': {'path': str(renderer), 'sha256': digest(renderer)},
            'engine': engine_record, 'visual_status': 'not_verified'}
