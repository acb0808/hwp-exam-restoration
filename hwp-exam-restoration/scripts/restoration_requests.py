"""Bounded batches of ordinary page operations, with per-item persistence."""

MAX_ITEMS = 64


def validate_items(action, params):
    if action not in ('assign', 'submit_reading'):
        raise ValueError('batch_only_supported_for_assign_and_submit_reading')
    if any(value is not None for key, value in params.items() if key not in ('job', 'items')):
        raise ValueError('provide_either_items_or_single_page_fields')
    items = params.get('items')
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_ITEMS:
        raise ValueError('items_requires_1_to_64_entries')
    allowed = {'page', 'worker_id', 'evidence'} if action == 'assign' else {'page', 'markdown', 'markdown_path'}
    pages = set()
    for item in items:
        if not isinstance(item, dict) or set(item) - allowed:
            raise ValueError('batch_item_has_unknown_fields')
        page = item.get('page')
        if type(page) is not int or page < 1 or page in pages:
            raise ValueError('batch_requires_distinct_positive_pages')
        pages.add(page)
        required = ('worker_id', 'evidence') if action == 'assign' else ()
        if action == 'submit_reading':
            selected = [key for key in ('markdown', 'markdown_path') if item.get(key) is not None]
            if len(selected) > 1:  # none: the page's assigned reading.md is used
                raise ValueError('provide_exactly_one_markdown_or_markdown_path_per_item')
            required = tuple(selected)
        if any(not isinstance(item.get(key), str) or not item[key].strip() for key in required):
            raise ValueError('batch_item_requires_nonempty_fields')
    return items


def execute_batch(action, params, dispatch):
    items = validate_items(action, params)
    results = []
    for item in items:
        # Each ordinary dispatch reloads/saves its own state even after failure.
        result = dispatch(action, {'job': params['job'], **item}, _request_kind='batch_item')
        results.append({**result, 'page': item['page']})
    failed = [row['page'] for row in results if row['status'] == 'failed']
    return {'status': 'failed' if len(failed) == len(results) else 'partial_failure' if failed else 'batch_processed',
            'results': results, 'failed_pages': failed,
            'next_action': 'Keep successful page results. Correct and resubmit only failed_pages; forward each page next_action to its existing owner. Do not wait for other unfinished pages or recreate workers.'}
