"""Best-effort local counters, never model-call, token, effort, or quota estimates.

Call ``record_event`` while holding the workflow's existing mutation lock. One
atomic JSON replacement stores counters and a bounded recent-event window, so
cost per update does not grow with the number of prior requests. The record is
not an audit trail. It deliberately contains no source, paths, prose, IDs of
workers/questions/figures, or evidence. A logging failure must not fail the job.
"""
from collections import Counter
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import tempfile

SCHEMA = 'restoration-operational-metrics/1'
MAX_RECENT_EVENTS = 64
MAX_JSON_BYTES = 4 * 1024 * 1024
ACTIONS = frozenset(('prepare', 'assign', 'submit_reading', 'inspect',
    'render_figures', 'review_figures', 'compose', 'build', 'status',
    'read_asset', 'finish_review', 'submit_review', 'batch_assign', 'batch_submit_reading', 'unknown'))
STATUSES = frozenset(('prepared', 'needs_selection', 'already_assigned', 'assigned',
    'accepted', 'ready_for_figures', 'ready_to_inspect', 'pending_review', 'ready',
    'failed', 'blocked', 'partial_failure', 'batch_processed', 'complete', 'building', 'built',
    'in_progress', 'needs_rebuild', 'review_recorded', 'unknown'))
# Explicit allowlist: even identifier-shaped user prose must not enter metrics.
ERROR_CODES = frozenset(('input', 'environment', 'missing_file',
    'input_error', 'environment_error', 'integrity_error',
    'markdown_syntax', 'answer_syntax', 'answer_validation', 'equation_error',
    'invalid_input', 'reading_parse_failed', 'reading_validation_failed',
    'submission_failed', 'runtime_error', 'unclassified_error'))
COUNTS = ('mcp_requests', 'engine_operations', 'request_failures', 'operation_failures')
TIMES = ('request_seconds', 'operation_seconds')
FIGURES = ('rendered', 'cached', 'failed', 'review_reused', 'unobserved_batches')
SUBMISSIONS = ('attempts', 'resubmissions', 'unchanged_replays', 'failures')
HOST_FIELDS = ('model_calls', 'tokens', 'effort', 'quota')


class MetricsError(ValueError):
    pass


def _read(path):
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_JSON_BYTES:
        raise MetricsError('metrics_corrupt')
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except RecursionError:
        raise MetricsError('metrics_corrupt') from None


def _location(root):
    root = Path(root).resolve(strict=True)
    manifest = _read(root / 'manifest.json')
    if (not isinstance(manifest, dict) or manifest.get('schema') != 'restoration-job/1'
            or not isinstance(manifest.get('pages'), list) or not manifest['pages']
            or not isinstance(manifest.get('assignments'), list)):
        raise MetricsError('metrics_unprepared_job')
    pages = [p.get('page') if isinstance(p, dict) else None for p in manifest['pages']]
    if any(type(p) is not int or p < 1 for p in pages) or len(set(pages)) != len(pages):
        raise MetricsError('metrics_unprepared_job')
    folder = (root / 'mcp').resolve(strict=True)
    if not folder.is_dir() or not folder.is_relative_to(root):
        raise MetricsError('metrics_unprepared_job')
    path = folder / 'metrics.json'
    if path.exists() and path.resolve() != path:
        raise MetricsError('metrics_invalid_path')
    return root, path, frozenset(pages)


def _counters():
    return {**dict.fromkeys(COUNTS, 0), **dict.fromkeys(TIMES, 0.0)}


def _empty():
    return {'schema': SCHEMA, **_counters(),
            'by_action': {}, 'submissions_by_page': {}, 'error_codes': {},
            'figures': dict.fromkeys(FIGURES, 0), 'recent_events': [],
            **dict.fromkeys(HOST_FIELDS)}


def _integer(value):
    return type(value) is int and value >= 0


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _validate_counts(value, keys):
    return isinstance(value, dict) and set(value) == set(keys) and all(_integer(v) for v in value.values())


def _validate_counters(value):
    return (all(_integer(value.get(k)) for k in COUNTS)
            and all(_number(value.get(k)) for k in TIMES))


def _validate(value, pages):
    valid = (isinstance(value, dict) and set(value) == set(_empty())
             and value.get('schema') == SCHEMA and _validate_counters(value)
             and all(value.get(k) is None for k in HOST_FIELDS)
             and _validate_counts(value.get('figures'), FIGURES))
    if not valid:
        raise MetricsError('metrics_corrupt')
    actions = value['by_action']
    submissions = value['submissions_by_page']
    errors = value['error_codes']
    recent = value['recent_events']
    if (not isinstance(actions, dict) or not set(actions).issubset(ACTIONS)
            or not isinstance(submissions, dict)
            or not set(submissions).issubset({str(p) for p in pages})
            or not isinstance(errors, dict) or not set(errors).issubset(ERROR_CODES)
            or any(not _integer(v) for v in errors.values())
            or not isinstance(recent, list) or len(recent) > MAX_RECENT_EVENTS):
        raise MetricsError('metrics_corrupt')
    for row in actions.values():
        if (not isinstance(row, dict) or set(row) != set(COUNTS + TIMES + ('statuses',))
                or not _validate_counters(row) or not isinstance(row['statuses'], dict)
                or not set(row['statuses']).issubset(STATUSES)
                or any(not _integer(v) for v in row['statuses'].values())):
            raise MetricsError('metrics_corrupt')
    if any(not _validate_counts(row, SUBMISSIONS) for row in submissions.values()):
        raise MetricsError('metrics_corrupt')
    for event in recent:
        if (not isinstance(event, dict)
                or set(event) != {'action', 'page', 'status', 'request_kind', 'elapsed_seconds'}
                or event['action'] not in ACTIONS or event['status'] not in STATUSES
                or event['request_kind'] not in ('single', 'batch_item', 'batch')
                or (event['page'] is not None and (type(event['page']) is not int or event['page'] not in pages))
                or not _number(event['elapsed_seconds'])):
            raise MetricsError('metrics_corrupt')
    return value


def _load(path, pages):
    if not path.exists():
        return _empty()
    try:
        return _validate(_read(path), pages)
    except (ValueError, KeyError, TypeError, OverflowError):
        raise MetricsError('metrics_corrupt') from None


def _figure_counts(root, page, result):
    counts = dict.fromkeys(FIGURES, 0)
    try:
        path = Path(result['batch_path']).resolve(strict=True)
        if not path.is_relative_to(root / 'mcp'):
            raise ValueError('batch_outside_job')
        batch = _read(path)
        if (not isinstance(batch, dict) or batch.get('schema') != 'restoration-figure-batch/1' or batch.get('page') != page
                or Path(batch['job']).resolve() != root or not isinstance(batch.get('items'), list)):
            raise ValueError('batch_binding_mismatch')
        reusable_ids = set()
        for item in batch['items']:
            if not isinstance(item, dict):
                raise ValueError('invalid_batch_item')
            if item.get('status') == 'failed':
                counts['failed'] += 1
            elif item.get('status') == 'pending_review':
                if item.get('reused') is True:
                    counts['cached'] += 1
                elif item.get('reused') is False and item.get('reuse_reason') != 'explicit_render_receipt':
                    counts['rendered'] += 1
                if isinstance(item.get('id'), str):
                    reusable_ids.add(item['id'])
        reviewed = result.get('reused_review_ids', [])
        if isinstance(reviewed, list):
            counts['review_reused'] = len(reusable_ids.intersection(x for x in reviewed if isinstance(x, str)))
        return counts
    except (OSError, ValueError, KeyError, TypeError):
        return {**dict.fromkeys(FIGURES, 0), 'unobserved_batches': 1}


def _error_counts(result):
    codes = []
    if result.get('error_code'):
        codes.append(result['error_code'])
    errors = result.get('errors', [])
    if isinstance(errors, list):
        codes.extend(e.get('code') if isinstance(e, dict) else None for e in errors)
    if result.get('status') in ('failed', 'blocked') and not codes:
        codes.append(None)
    return Counter(code if isinstance(code, str) and code in ERROR_CODES else 'unclassified_error' for code in codes)


def _atomic_write(path, value):
    data = json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
    if len(data) > MAX_JSON_BYTES:
        raise OSError('metrics_size_limit')
    fd, temporary = tempfile.mkstemp(prefix='metrics.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def record_event(root, action, params, result, elapsed_seconds, *, request_kind='single'):
    """Return recorded/unavailable without raising or changing the workflow result.

    ``single`` counts one MCP request and one engine operation. A batch wrapper
    counts one request (including its total latency); its ``batch_item`` calls
    count only operations. Durations of wrappers and items are never summed.
    Repeated unchanged submissions are attempts/replays, not resubmissions.
    """
    try:
        root, path, pages = _location(root)
    except (OSError, ValueError, KeyError, TypeError):
        return {'status': 'unavailable', 'code': 'metrics_unprepared_job'}
    try:
        value = _load(path, pages)
    except (OSError, ValueError, KeyError, TypeError):
        return {'status': 'unavailable', 'code': 'metrics_corrupt'}
    try:
        params = params if isinstance(params, dict) else {}
        result = result if isinstance(result, dict) else {}
        action = action if isinstance(action, str) and action in ACTIONS else 'unknown'
        status = result.get('status')
        status = status if isinstance(status, str) and status in STATUSES else 'unknown'
        page = params.get('page')
        page = page if type(page) is int and page in pages else None
        elapsed = round(float(elapsed_seconds), 6) if _number(elapsed_seconds) else 0.0
        if request_kind not in ('single', 'batch_item', 'batch'):
            return {'status': 'unavailable', 'code': 'metrics_invalid_request_kind'}
        request = request_kind != 'batch_item'
        operation = request_kind != 'batch'
        failed = status in ('failed', 'blocked', 'partial_failure')
        delta = {'mcp_requests': int(request), 'engine_operations': int(operation),
                 'request_failures': int(request and failed), 'operation_failures': int(operation and failed),
                 'request_seconds': elapsed if request else 0.0, 'operation_seconds': elapsed if operation else 0.0}
        row = value['by_action'].setdefault(action, {**_counters(), 'statuses': {}})
        for key, count in delta.items():
            value[key] += count
            row[key] += count
        row['statuses'][status] = row['statuses'].get(status, 0) + 1
        if operation:
            for code, count in _error_counts(result).items():
                value['error_codes'][code] = value['error_codes'].get(code, 0) + count
            if action == 'submit_reading' and page is not None:
                counts = value['submissions_by_page'].setdefault(str(page), dict.fromkeys(SUBMISSIONS, 0))
                replay = result.get('unchanged') is True
                counts['resubmissions'] += int(not replay and counts['attempts'] > counts['unchanged_replays'])
                counts['attempts'] += 1
                counts['unchanged_replays'] += int(replay)
                counts['failures'] += int(failed)
            if action == 'render_figures':
                for key, count in _figure_counts(root, page, result).items():
                    value['figures'][key] += count
        value['recent_events'].append({'action': action, 'page': page, 'status': status,
                                      'request_kind': request_kind, 'elapsed_seconds': elapsed})
        value['recent_events'] = value['recent_events'][-MAX_RECENT_EVENTS:]
        _atomic_write(path, value)
        return {'status': 'recorded', 'metrics_path': str(path)}
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        return {'status': 'unavailable', 'code': 'metrics_write_failed'}


def summary(root):
    """Compact counters only; unavailable host usage remains null, never zero."""
    try:
        _, path, pages = _location(root)
    except (OSError, ValueError, KeyError, TypeError):
        return {'status': 'unavailable', 'code': 'metrics_unprepared_job', **dict.fromkeys(HOST_FIELDS)}
    try:
        value = deepcopy(_load(path, pages))
        value.pop('recent_events')
        return {'status': 'available', 'metrics_path': str(path), **value}
    except (OSError, ValueError, KeyError, TypeError):
        return {'status': 'unavailable', 'code': 'metrics_corrupt', **dict.fromkeys(HOST_FIELDS)}
