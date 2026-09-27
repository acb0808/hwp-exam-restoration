"""Read-only contract diagnostics; production validation remains authoritative."""
import math
from restoration_contract import validate_page, _bbox, _inside
from question_contract import validate_content, ContentFieldsError

FAILURES = (ValueError, KeyError, TypeError)


def equation_failure_details(latex, error):
    """Expose original engine spans and recovery guidance, never rewrite input."""
    diagnostics = []
    for item in getattr(error, 'diagnostics', []):
        if not isinstance(item, dict):
            continue
        row = {key: item[key] for key in
               ('code', 'message', 'severity', 'start', 'end', 'line', 'column') if key in item}
        start, end = item.get('start'), item.get('end')
        if type(start) is int and type(end) is int and 0 <= start <= end <= len(latex):
            row.update(token=latex[start:end], span_unit='unicode_codepoint')
        diagnostics.append(row)
    if not diagnostics:
        return {}
    details = {'engine_diagnostics': diagnostics}
    codes = {row.get('code') for row in diagnostics}
    if codes == {'spacing_approximation'}:
        details['hint'] = (
            '표시된 간격 명령은 파싱되지만 HWP에서 근사 간격으로 변환되어 엄격 검증이 거부했습니다. '
            '자동 삭제·물결표(~) 치환으로 통과시키지 마세요. 원본에서 p와 q처럼 독립된 식 사이의 '
            '본문 간격인지 확인한 경우 각각 kind:equation run으로 분리하고 그 사이를 '
            '원본에 맞춘 kind:text 공백 run으로 작성할 수 있습니다. 수식 내부 간격이면 이 방법을 '
            '적용하지 마세요. 출력 간격은 원본·최종 렌더를 대조해야 하며 보존할 수 없으면 '
            'issues에 남기세요. 내부 코드 탐색이나 수식별 시험 프로그램은 필요 없습니다.')
    elif 'missing_argument' in codes:
        details['hint'] = (
            '표시된 위치에 명령의 인수가 없습니다. 원본을 대조해 누락된 인수·중괄호를 복원하세요. '
            '예를 들어 분수는 \\frac{분자}{분모}가 필요합니다. 원본을 읽을 수 없으면 추측하지 말고 '
            'issues에 남기세요. 수정 후 같은 compile-draft 명령으로 재검증하세요.')
    return details


def contract_errors(value):
    """Return no errors iff validate_page passes, without normalizing any input.

    Isolated checks explain independent failures; they never authorize a page.
    Malformed parent structures suppress dependent geometry/content checks.
    """
    try:
        validate_page(value)
        return []
    except FAILURES as exc:
        first = str(exc)
    errors = []

    def add(location, message, qid=None, **details):
        row = {'code': 'contract', 'location': location, 'message': message, **details}
        if qid is not None:
            row['question_id'] = qid
        if row not in errors:
            errors.append(row)

    def box_ok(box):
        try:
            _bbox(box, 'bbox_mm')
            return True
        except FAILURES:
            return False

    def geometry(box, parent, location, qid=None):
        try:
            _bbox(box, 'bbox_mm')
        except FAILURES as exc:
            add(location, str(exc), qid,
                hint='bbox_mm은 유한한 [x, y, 너비, 높이]이며 너비와 높이는 양수여야 합니다.')
            return
        if parent is None:
            return
        try:
            _inside(box, parent, 'bbox_mm')
        except FAILURES as exc:
            x, y, w, h = box; px, py, pw, ph = parent
            add(location, str(exc), qid, bbox_mm=list(box), parent_bbox_mm=list(parent),
                excess_mm={'left': max(0, px-x), 'top': max(0, py-y),
                           'right': max(0, x+w-px-pw), 'bottom': max(0, y+h-py-ph)},
                hint='원본과 문항·상위 영역 경계를 대조해 좌표를 수정하세요. 자동 이동이나 허용 오차 확대는 하지 않습니다.')

    def hint(message,fields=None):
        if fields:
            missing=fields['missing_fields']; unknown=fields['unknown_fields']
            parts=[]
            if missing:parts.append('필수 필드 누락: '+', '.join(missing)+'.')
            if unknown:parts.append('허용되지 않는 필드: '+', '.join(map(str,unknown))+'.')
            if 'title' in missing:
                parts.append('box.title은 필수입니다. 원본 제목을 확인해 기록하세요. 제목이 없는 상자는 title: ""를 명시하세요. 제목을 자동 추론하거나 누락을 빈 제목으로 처리하지 않습니다.')
            else:
                parts.append('누락된 내용은 원본을 확인해 입력하세요. 자동 추론하거나 기존 내용을 삭제해 통과시키지 마세요.')
            return ' '.join(parts)
        if 'runs: expected nonempty' in message:
            return '본문·선지 runs는 비울 수 없습니다. 그림 전용 paragraph는 유효한 figure와 runs: []를 함께 넣으세요.'
        if 'each text block must be one original line' in message:
            return 'text에 줄바꿈/탭을 넣지 마세요. 원본에 필요한 줄바꿈은 별도의 {"kind":"break"} run으로 기록하세요.'
        return '표시된 문항·블록을 page-contract.md의 계약과 비교하세요. 원본 내용은 삭제하거나 추측해 통과시키지 마세요.'

    def content(blocks, question, width, location):
        if not isinstance(blocks, list) or not blocks:
            add(location, 'content: expected nonempty array', question.get('id'))
            return
        for index, block in enumerate(blocks):
            where = f'{location}[{index}]'
            probe = dict(question, bbox_mm=[0, 0, width, 1], content=[block])
            fields = {}
            try:
                validate_content(probe, set())
                continue
            except FAILURES as exc:
                message = str(exc)
                # Nested validation may raise for a child: bind field details to
                # the exact block, not every enclosing box with the same text.
                if isinstance(exc,ContentFieldsError) and exc.block is block:
                    fields={'missing_fields':list(exc.missing_fields),
                            'unknown_fields':list(exc.unknown_fields)}
            previous = len(errors)
            if isinstance(block, dict) and block.get('kind') == 'box':
                children = block.get('content')
                pad = block.get('padding_mm', [3, 2, 3, 2])
                left, right = block.get('left_mm', 0), block.get('right_mm', 0)
                valid = (isinstance(pad, list) and len(pad) == 4 and
                         all(type(n) in (int, float) and math.isfinite(n) and n >= 0
                             for n in [left, right, *pad]))
                if valid and width-left-right-pad[0]-pad[2] > 0 and isinstance(children, list):
                    content(children, question, width-left-right-pad[0]-pad[2], where+'.content')
            if fields or not any(e['message'] == message for e in errors[previous:]):
                add(where, message, question.get('id'), hint=hint(message,fields), **fields)

    if isinstance(value, dict):
        size = value.get('size_mm')
        bounds = [0, 0, *size] if isinstance(size, list) and len(size) == 2 else None
        if not box_ok(bounds):
            bounds = None
        regions = {}
        rows = value.get('regions')
        if isinstance(rows, list):
            for i, region in enumerate(rows):
                if not isinstance(region, dict):
                    continue
                box = region.get('bbox_mm')
                geometry(box, bounds, f'page.regions[{i}].bbox_mm')
                rid = region.get('id')
                if isinstance(rid, str):
                    # Duplicate region IDs cannot supply an unambiguous parent.
                    regions[rid] = None if rid in regions else box if box_ok(box) else None
        questions = value.get('questions')
        if isinstance(questions, list):
            for i, question in enumerate(questions):
                where = f'page.questions[{i}]'
                if not isinstance(question, dict):
                    add(where, 'question: expected object')
                    continue
                qid = question.get('id')
                qid = qid if isinstance(qid, str) else None
                box = question.get('bbox_mm')
                geometry(box, bounds, where+'.bbox_mm', qid)
                rid = question.get('region_id')
                if isinstance(rid, str) and rid in regions:
                    if box_ok(box) and regions[rid] != bounds:
                        geometry(box, regions[rid], where+'.bbox_mm', qid)
                elif isinstance(rows, list) and rows:
                    add(where+'.region_id', 'question: unknown region_id', qid)
                if value.get('version') == 2 and box_ok(box):
                    # Invalid font metadata is a question error, not N block errors.
                    pt = question.get('font_pt'); family = question.get('font_family')
                    if (type(pt) in (int, float) and math.isfinite(pt) and pt > 0 and
                            isinstance(family, str) and family.strip()):
                        content(question.get('content'), question, box[2], where+'.content')
                    else:
                        add(where, 'question: positive font_pt and nonempty font_family required', qid)
    # Keep any authoritative error not explained by a more precise diagnostic.
    if not any(e['message'] == first or
               (first.endswith('outside containing area') and 'excess_mm' in e)
               for e in errors):
        add('page', first)
    return errors
