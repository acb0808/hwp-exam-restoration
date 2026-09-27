"""Two independent readings; explicit source adjudication; one production owner.

Receipts preserve bytes and workflow state, not the authenticity of model claims.
No agreement, crop creation, or compilation constitutes a visual review.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import uuid

import restoration_job as job
from restoration_reading import validate_reading, compare_readings, _figure_ids


def _bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode('utf-8')


def _save(root, value):
    job.save_json(root / 'ab' / f"page-{value['page']:04d}" / 'session.json', value)


def _load(root, page):
    manifest = job._manifest(root)
    matches = [a for a in manifest['assignments'] if a['page'] == page]
    if len(matches) != 1 or not matches[0].get('ab_session'):
        raise ValueError('ab_assignment_required')
    assignment = matches[0]
    expected = f'ab/page-{page:04d}/session.json'
    if assignment['ab_session'] != expected: raise ValueError('ab_session_path_mismatch')
    path = (root / expected).resolve(strict=True)
    if not path.is_relative_to(root): raise ValueError('ab_session_path_escape')
    state = job.load_json(path)
    if (state.get('schema') != 'restoration-ab/1' or state.get('page') != page
            or state.get('assignment_id') != assignment['assignment_id']
            or state.get('worker_a') != assignment['worker_id']
            or state.get('worker_b') != assignment.get('reader_b')
            or state.get('worker_a') == state.get('worker_b')
            or state.get('source_sha256') != manifest['source']['sha256']):
        raise ValueError('ab_binding_mismatch')
    for role, ref in state['readings'].items():
        validate_reading(job.load_json(job._artifact(root, ref)), page,
                         state['worker_' + role], state['source_sha256'])
    return manifest, assignment, state


def preparation_handoff(root, manifest):
    from restoration_handoff import powershell, command
    root = Path(root).resolve()
    return {'spawn_requests': [
        {'page': p['page'], 'role': role, 'type': 'hwp-restoration-reader',
         'message': f"{p['page']}쪽 독립 전사 {role.upper()}. 배정 task.md를 받을 때까지 대기. 다른 전사 결과를 보거나 도형을 만들지 마세요."}
        for p in manifest['pages'] for role in ('a', 'b')],
        'assignment_commands': [powershell(command('ab-assign', root, p['page'],
            '--worker-a', f"ACTUAL_PAGE_{p['page']}_A_ID", '--worker-b', f"ACTUAL_PAGE_{p['page']}_B_ID",
            '--evidence', root / 'spawn-response.txt')) for p in manifest['pages']],
        'evidence_path': str(root / 'spawn-response.txt'),
        'note': 'Use the installed custom type with inherited model, NOT self. Save actual tool responses, substitute actual IDs, then send each returned handoff. Keep A idle for later sole-owner production. Never give either reader the other reading before submission.',
        'environment_file': str(root / 'environment.json')}


def assign_readers(job_dir, page, worker_a, worker_b, evidence):
    root = Path(job_dir).resolve(strict=True)
    if any(not isinstance(w, str) or not w.strip() for w in (worker_a, worker_b)) or worker_a == worker_b:
        raise ValueError('two_independent_worker_ids_required')
    data = job._read(evidence)
    if not data.strip(): raise ValueError('nonempty_tool_evidence_required')
    with job._locked(root):
        manifest = job._manifest(root)
        if type(page) is not int or not 1 <= page <= len(manifest['pages']): raise ValueError('page_out_of_range')
        if any(a['page'] == page for a in manifest['assignments']): raise ValueError('page_already_assigned')
        used = {w for a in manifest['assignments'] for w in (a['worker_id'], a.get('reader_b'))}
        if used & {worker_a, worker_b}: raise ValueError('one_page_per_independent_worker')
        assignment = {'assignment_id': uuid.uuid4().hex, 'page': page, 'worker_id': worker_a,
            'reader_b': worker_b, 'mode': 'subagent', 'evidence': job._snapshot(root, 'evidence', data, '.log'),
            'ab_session': f'ab/page-{page:04d}/session.json'}
        state = {'schema': 'restoration-ab/1', 'page': page, 'assignment_id': assignment['assignment_id'],
            'source_sha256': manifest['source']['sha256'], 'worker_a': worker_a, 'worker_b': worker_b,
            'readings': {}, 'crops': [], 'flags': [], 'history': [], 'composed': []}
        handoffs = []
        guide = Path(__file__).resolve().parents[1] / 'references' / 'ab-reader.md'
        for role, worker in [('a', worker_a), ('b', worker_b)]:
            folder = root / 'readers' / f'page-{page:04d}' / role
            folder.mkdir(parents=True, exist_ok=False)
            template = {'schema': 'restoration-reading/1', 'page': page, 'worker_id': worker,
                'source_sha256': state['source_sha256'], 'complete': False, 'issues': [], 'questions': []}
            job.save_json(folder / 'reading.json', template)
            task = folder / 'task.md'
            task.write_text(f"# 독립 전사 {role.upper()} / {page}쪽\n\n담당자: {worker}\n"
                f"원본 전체: {root / manifest['pages'][page-1]['image']['path']}\n"
                f"저장할 결과: {folder / 'reading.json'}\n\n"
                '원본 전체를 한 번 읽고 본문·수식·보기·선지·배점을 모두 전사하세요. 도형은 위치 표식만 남깁니다. '
                '좌표 측정·crop 코드·도형 제작·추가 에이전트 생성은 하지 마세요. '
                '다른 전사 결과를 보지 마세요. 불확실한 문항은 uncertain=true로 표시하고 결과 경로만 보고하세요.\n\n'
                + guide.read_text(encoding='utf-8'), encoding='utf-8')
            handoffs.append({'page': page, 'role': role, 'worker_id': worker,
                'worker_instructions': str(task), 'message': f'{task}를 읽고 독립 전사하세요. 결과 경로만 보고하고 대기하세요.'})
        _save(root, state)
        manifest['assignments'].append(assignment); job.save_json(root / 'manifest.json', manifest)
        return {'status': 'assigned', 'owner_worker_id': worker_a, 'handoffs': handoffs}


def submit_reading(job_dir, page, role, path):
    root = Path(job_dir).resolve(strict=True)
    if role not in ('a', 'b'): raise ValueError('reading_role_must_be_a_or_b')
    data = job._read(path); value = job._json(data)
    with job._locked(root):
        _, _, state = _load(root, page)
        validate_reading(value, page, state['worker_' + role], state['source_sha256'])
        if role in state['readings']:
            if job._read(job._artifact(root, state['readings'][role])) == data:
                return {'status': 'already_submitted', 'page': page, 'role': role}
            state['history'].append({k: deepcopy(state[k]) for k in
                ('readings', 'comparison', 'approval', 'approved_reading', 'crops', 'flags', 'composed') if k in state})
        state['readings'][role] = job._snapshot(root, 'ab/evidence', data, '.json')
        for key in ('comparison', 'approval', 'approved_reading'): state.pop(key, None)
        state.update(crops=[], flags=[], composed=[])
        _save(root, state)
    return {'status': 'submitted', 'page': page, 'role': role, 'accepted': False}


def _comparison(root, state):
    if set(state['readings']) != {'a', 'b'}: raise ValueError('both_readings_required')
    values = [job.load_json(job._artifact(root, state['readings'][r])) for r in ('a', 'b')]
    result = compare_readings(*values)
    if 'comparison' not in state: raise ValueError('comparison_required')
    saved = job.load_json(job._artifact(root, state['comparison']))
    if saved != {'readings': state['readings'], 'result': result}: raise ValueError('stale_comparison')
    return result, values


def compare_page(job_dir, page):
    root = Path(job_dir).resolve(strict=True)
    with job._locked(root):
        _, _, state = _load(root, page)
        if set(state['readings']) != {'a', 'b'}: raise ValueError('both_readings_required')
        values = [job.load_json(job._artifact(root, state['readings'][r])) for r in ('a', 'b')]
        result = compare_readings(*values)
        if 'comparison' not in state:
            state['comparison'] = job._snapshot(root, 'ab/evidence', _bytes({'readings': state['readings'], 'result': result}), '.json')
            _save(root, state)
        decision = _decision_template(root, state, result)
        return {**{k: result[k] for k in ('status', 'matching_ids', 'page_issues', 'figure_ids')},
            'disputed_ids': [d['question_id'] for d in result['disputes']],
            'comparison_path': str(job._artifact(root, state['comparison'])),
            'decision_template': str(decision), 'accepted': False}


def _decision_template(root, state, comparison):
    identifiers = list(dict.fromkeys([d['question_id'] for d in comparison['disputes']] + state['flags']))
    identity = hashlib.sha256(_bytes([state['comparison'], identifiers])).hexdigest()[:20]
    path = root / 'ab' / f"page-{state['page']:04d}" / ('decision-template-' + identity + '.json')
    if not path.exists():
        template = {'comparison_sha256': state['comparison']['sha256'], 'reviewer_id': '',
            'source_overview_checked': False, 'question_inventory_checked': False,
            'figure_inventory_checked': False, 'issues': [],
            'resolutions': [{'question_id': qid, 'choice': '', 'source_checked': False, 'reason': ''} for qid in identifiers]}
        if 'question_order_mismatch' in comparison['page_issues']:
            template['question_order'] = list(dict.fromkeys(comparison['question_order']['a'] + comparison['question_order']['b']))
        job.save_json(path, template)
    return path


def prepare_dispute_views(job_dir, page, rows):
    from restoration_prepare import source_views
    root = Path(job_dir).resolve(strict=True)
    with job._locked(root):
        _, _, state = _load(root, page); comparison, values = _comparison(root, state)
        if 'approval' in state: raise ValueError('resubmit_reading_before_reopening_approval')
        if not isinstance(rows, list) or not rows: raise ValueError('nonempty_crop_requests_required')
        ids = {q['id'] for v in values for q in v['questions']}
        disputed = {d['question_id'] for d in comparison['disputes']}
        stripped = []; flags = []
        for row in rows:
            if not isinstance(row, dict) or row.get('question_id') not in ids: raise ValueError('unknown_crop_question')
            qid = row['question_id']
            if qid not in disputed:
                if not isinstance(row.get('reason'), str) or not row['reason'].strip():
                    raise ValueError('crop_only_disputed_or_explicitly_flagged_questions')
                flags.append(qid)
            stripped.append({k: v for k, v in row.items() if k not in ('question_id', 'reason')})
        # Cache identical requests only while the exact reading pair is unchanged.
        for prior in state['crops']:
            if prior['requests'] == rows:
                _check_crops(root, state)
                return {'status': 'prepared', 'reused': True, 'index': str(job._artifact(root, prior['index'])),
                        'decision_template': str(_decision_template(root, state, comparison))}
        folder = root / 'ab' / f'page-{page:04d}' / ('views-' + uuid.uuid4().hex)
        result = source_views(root, page, stripped, folder)
        refs = [{'path': Path(v['image']['path']).relative_to(root).as_posix(), 'sha256': v['image']['sha256']}
                for v in result['views']]
        state['crops'].append({'requests': deepcopy(rows), 'images': refs,
            'index': {'path': (folder/'index.json').relative_to(root).as_posix(), 'sha256': job.digest(folder/'index.json')}})
        state['flags'] = list(dict.fromkeys(state['flags'] + flags)); _save(root, state)
        return {'status': 'prepared', 'reused': False, 'index': str(folder/'index.json'),
                'views': result['views'], 'decision_template': str(_decision_template(root, state, comparison)),
                'visual_status': 'not_verified'}


def _check_crops(root, state):
    covered = set()
    for crop in state['crops']:
        job._artifact(root, crop['index'])
        for image in crop['images']: job._artifact(root, image)
        covered.update(r['question_id'] for r in crop['requests'])
    return covered


def approve_page(job_dir, page, decision):
    root = Path(job_dir).resolve(strict=True)
    value = deepcopy(decision) if isinstance(decision, dict) else job.load_json(decision)
    with job._locked(root):
        manifest, _, state = _load(root, page); comparison, readings = _comparison(root, state)
        if 'approval' in state: raise ValueError('already_approved_resubmit_changed_reading_to_reopen')
        if not isinstance(value, dict): raise ValueError('approval_object_required')
        if value.get('comparison_sha256') != state['comparison']['sha256']:
            raise ValueError('approval_requires_current_comparison_hash')
        reviewer = value.get('reviewer_id')
        if not isinstance(reviewer, str) or not reviewer.strip() or reviewer in (state['worker_a'], state['worker_b']):
            raise ValueError('master_reviewer_required')
        if any(value.get(k) is not True for k in ('source_overview_checked', 'question_inventory_checked', 'figure_inventory_checked')):
            raise ValueError('explicit_source_and_inventory_checks_required')
        if value.get('issues') != []: raise ValueError('unresolved_approval_issues')
        if any(x != 'question_order_mismatch' for x in comparison['page_issues']):
            raise ValueError('incomplete_or_page_issues_require_corrected_reading_resubmission')
        required = {d['question_id'] for d in comparison['disputes']} | set(state['flags'])
        rows = value.get('resolutions')
        if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows): raise ValueError('resolution_array_required')
        resolved = {r.get('question_id'): r for r in rows}
        if len(resolved) != len(rows) or set(resolved) != required: raise ValueError('explicit_resolution_for_each_dispute_required')
        if not required <= _check_crops(root, state): raise ValueError('source_crop_required_for_each_dispute')
        maps = [{q['id']: q for q in v['questions']} for v in readings]
        order = list(dict.fromkeys(comparison['question_order']['a'] + comparison['question_order']['b']))
        questions = []
        for qid in order:
            question = maps[0].get(qid)
            if qid in required:
                row = resolved[qid]
                if row.get('source_checked') is not True or not isinstance(row.get('reason'), str) or not row['reason'].strip():
                    raise ValueError('resolution_requires_source_check_and_reason')
                choice = row.get('choice')
                if choice == 'drop': continue
                if choice in ('a', 'b'): question = maps[0 if choice == 'a' else 1].get(qid)
                elif choice == 'custom': question = row.get('question')
                else: raise ValueError('resolution_choice_required')
                if not isinstance(question, dict) or question.get('id') != qid: raise ValueError('resolution_question_missing_or_wrong_id')
            question = deepcopy(question); question['uncertain'] = False; questions.append(question)
        if 'question_order_mismatch' in comparison['page_issues'] or 'question_order' in value:
            requested = value.get('question_order')
            mapping = {q['id']: q for q in questions}
            if not isinstance(requested, list) or len(requested) != len(mapping) or set(requested) != set(mapping):
                raise ValueError('explicit_resolved_question_order_required')
            questions = [mapping[qid] for qid in requested]
        approved = {**readings[0], 'questions': questions, 'complete': True, 'issues': []}
        validate_reading(approved, page, state['worker_a'], state['source_sha256'])
        state['approval'] = job._snapshot(root, 'ab/evidence', _bytes(value), '.json')
        state['approved_reading'] = job._snapshot(root, 'ab/evidence', _bytes(approved), '.json')
        state['composed'] = []; _save(root, state)
        folder = root / 'ab' / f'page-{page:04d}'
        guide = Path(__file__).resolve().parents[1] / 'references' / 'ab-production.md'
        task = folder / 'production.md'
        task.write_text(f"# {page}쪽 제작 / 유일 담당자 {state['worker_a']}\n\n"
            f"확정 전사: {job._artifact(root, state['approved_reading'])}\n"
            f"원본: {root / manifest['pages'][page-1]['image']['path']}\n"
            f"작업 폴더: {folder}\n\n" + guide.read_text(encoding='utf-8') + '\n\n'
            f"도형 제작 시에만 읽을 안내: {guide.with_name('tikz-exam.md')}\n", encoding='utf-8')
        return {'status': 'approved_pending_production', 'owner_worker_id': state['worker_a'],
            'production_instructions': str(task), 'approved_reading': str(job._artifact(root, state['approved_reading'])),
            'accepted': False, 'visual_status': 'not_verified'}


def production_state(job_dir, page):
    root = Path(job_dir).resolve(strict=True)
    _, _, state = _load(root, page); _comparison(root, state)
    if 'approval' not in state or 'approved_reading' not in state: raise ValueError('master_approval_required_before_production')
    job._artifact(root, state['approval']); job._artifact(root, state['approved_reading']); _check_crops(root, state)
    return state


def check_figure_requests(job_dir, page, rows):
    root = Path(job_dir).resolve(strict=True); manifest = job._manifest(root)
    assignment = next((a for a in manifest['assignments'] if a['page'] == page), {})
    if not assignment.get('ab_session'): return
    state = production_state(root, page)
    reading = job.load_json(job._artifact(root, state['approved_reading']))
    expected = {(q['id'], ref) for q in reading['questions'] for ref in _figure_ids(q)}
    if not isinstance(rows, list) or any(not isinstance(r, dict) or (r.get('question_id'), r.get('id')) not in expected for r in rows):
        raise ValueError('figure_not_in_approved_reading')


def compose_page(job_dir, page, layout, output, figures=None):
    from restoration_draft import compile_draft, _fields
    root = Path(job_dir).resolve(strict=True)
    value = deepcopy(layout) if isinstance(layout, dict) else job.load_json(layout)
    with job._locked(root):
        state = production_state(root, page)
        reading = job.load_json(job._artifact(root, state['approved_reading']))
        _fields(value, {'schema', 'regions', 'questions'}, set(), 'layout')
        if value['schema'] != 'restoration-layout/1': raise ValueError('invalid_layout_schema')
        if not isinstance(value['questions'], list) or not isinstance(value['regions'], list): raise ValueError('layout_arrays_required')
        if [q.get('id') for q in value['questions']] != [q['id'] for q in reading['questions']]:
            raise ValueError('layout_must_preserve_approved_question_order')
        questions = []
        for geometry, question in zip(value['questions'], reading['questions']):
            _fields(geometry, {'id', 'region_id'}, {'bbox_mm', 'bbox_px', 'coordinate_space'}, 'layout.question')
            if ('bbox_mm' in geometry) == ('bbox_px' in geometry): raise ValueError('explicit_question_bounds_required')
            questions.append({**geometry, 'content': deepcopy(question['content'])})
        # A region may not silently combine or interchange the approved left/right columns.
        columns = {}
        for geometry, question in zip(questions, reading['questions']):
            rid = geometry['region_id']; previous = columns.setdefault(rid, question['column'])
            if previous != question['column']: raise ValueError('layout_region_mixes_columns')
        draft = {'schema': 'restoration-draft/1', 'regions': value['regions'], 'questions': questions, 'issues': []}
        result = compile_draft(root, page, draft, output, figures=figures)
        if result['status'] == 'compiled':
            expanded = job.load_json(result['output'])
            for left in expanded['questions']:
                for right in expanded['questions']:
                    if columns[left['region_id']] == 'left' and columns[right['region_id']] == 'right' and left['bbox_mm'][0] >= right['bbox_mm'][0]:
                        raise ValueError('layout_column_order_mismatch')
            state['composed'].append(job._snapshot(root, 'ab/composed', job._read(result['output']), '.json')); _save(root, state)
        return result


def validate_accepted_result(job_dir, value, assignment):
    if not assignment.get('ab_session'): return
    root = Path(job_dir).resolve(strict=True); state = production_state(root, assignment['page'])
    if not any(job.load_json(job._artifact(root, ref)) == value for ref in state['composed']):
        raise ValueError('ab_result_requires_unchanged_approved_composition')
