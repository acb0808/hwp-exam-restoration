"""Small public writing contract; no rendering, job mutation or source browsing."""
from pathlib import Path
import json
import restoration_job as job

MATH_EXAMPLES = [
    {'use': '한글이 포함된 집합', 'latex': r'A=\{x | x\le100,\text{ 자연수}\}'},
    {'use': '빈집합', 'latex': r'A\ne\emptyset'},
    {'use': '빈집합의 다른 표기', 'latex': r'A\ne\varnothing'},
    {'use': '생략점', 'latex': r'a_1+\cdots+a_n'},
    {'use': '선분', 'latex': r'\overline{AB}'},
    {'use': '호', 'latex': r'\overparen{AB}'},
    {'use': '각과 각도', 'latex': r'\angle ABC=30^\circ'},
    {'use': '단위', 'latex': r'4 \mathrm{cm}'},
    {'use': '평행·수직', 'latex': r'\overline{AB} \parallel \overline{CD},\overline{AB} \perp \overline{CD}'},
    {'use': '분수와 아래첨자', 'latex': r'A_k(a)=\frac{a+1}{2}'},
]

# Commands producers reach for that have one exact supported spelling: the error names it, so the fix is one edit.
REPLACEMENTS = {
    r'\implies': r'\implies 대신 \Rightarrow를 쓰세요.',
    r'\iff': r'\iff 대신 \Leftrightarrow를 쓰세요.',
    r'\dots': r'\dots 대신 \cdots(가운데 점) 또는 \ldots(아래 점)를 쓰세요.',
    r'\dotsc': r'\dotsc 대신 \ldots를 쓰세요.',
    r'\dotsb': r'\dotsb 대신 \cdots를 쓰세요.',
    r'\lt': r'\lt 대신 <를 쓰세요.',
    r'\gt': r'\gt 대신 >를 쓰세요.',
    r'\frown': r'호는 $\overparen{AB}$로 쓰세요.',
    r'\!': r'\! 는 지우세요. 평행은 \parallel입니다.',
}


def answer_mode(enabled):
    return ('include_answers=true: 각 문항에 answer 블록을 쓰고 원본으로 계산·검산합니다.'
            if enabled else 'include_answers=false: 정답 계산·answer 블록·정답표를 작성하지 않습니다.')


def figure_mode(enabled):
    """The task line of a text-only job; nothing for the default, which restores figures."""
    return ('' if enabled else
            'include_figures=false: 이 작업은 글과 수식만 옮깁니다. 원본의 그림·그래프·도형은 복원하지 않습니다. '
            '도형 자리 표시(`![](figure:…)`)와 TeX 파일을 쓰지 않고 hwp_render_figures·hwp_review_figures를 호출하지 않습니다. '
            '그림 안에만 인쇄된 글자·수치는 옮기지 않고, 선지가 그림인 문항은 발문만 적습니다. '
            '제출이 accepted를 돌려주면 이 쪽은 끝입니다.\n\n')


def submit_call(root, page):
    return {'tool': 'hwp_submit_reading', 'arguments': {'job': str(root), 'page': page,
            'markdown_path': str(root / 'workers' / f'page-{page:04d}' / 'reading.md')}}


def tool_examples(root, page, figures=True):
    """Exact call shapes so producers need not open MCP schema files."""
    inspect = ('## 도구 호출 형식 (스키마 파일을 열지 않아도 됩니다)\n\n'
            '- 확대: `hwp_inspect` ' + json.dumps({'job': str(root), 'page': page, 'requests': [
                {'question_id': 'q1', 'id': 'q1-sign', 'bbox_px': ['x', 'y', '폭', '높이'], 'reason': '불명확한 이유'}]}, ensure_ascii=False)
            + ' (필요한 곳을 한 번에 모아 요청; 여러 개면 라벨 붙은 한 장으로 반환)\n')
    closing = ('- 도구 목록에 `hwp_...` 이름이 직접 보이지 않고 `call_mcp_tool`만 있으면 ServerName `hwp-restoration`, ToolName은 위 도구 이름, '
            'Arguments는 위 JSON으로 호출합니다(`hwp_submit_reading`·`hwp_help`도 같습니다).\n'
            '- 이 도구 설명·SKILL.md·역할 파일은 다시 열지 않습니다. 필요한 파일은 한 턴에 함께 엽니다.\n\n')
    if not figures:
        return inspect + closing
    return (inspect +
            '- 도형 렌더: 도형마다 `' + str(root / 'workers' / f'page-{page:04d}' / 'ID.tex') + '` 파일을 쓰고(ID는 reading.md의 figure ID) '
            '첫 줄에 `% width_mm=50 source_bbox_px=x,y,폭,높이`(원본 픽셀 영역)를 적은 뒤 `hwp_render_figures` '
            + json.dumps({'job': str(root), 'page': page}, ensure_ascii=False)
            + ' 만 호출합니다. LaTeX를 JSON에 넣거나 이스케이프하지 않습니다. 고칠 때는 그 파일만 부분 수정하고 같은 호출을 반복합니다. '
            '너비는 원본에서 차지한 비율로 정하고, 단 폭을 넘으면 엔진이 줄여 알려 줍니다(width_clamped). '
            '원본 crop과 렌더는 비교 이미지로 반환되며 새 도형이 여럿이면 compare_sheets 한두 장으로 묶입니다.\n'
            '- 도형 검수: `hwp_review_figures` ' + json.dumps({'job': str(root), 'page': page, 'batch_path': '반환된 값', 'reviews': [
                {'id': 'q1-figure-1', 'status': 'passed', 'issues': [],
                 'checks': {'geometry': 'passed', 'labels': 'passed', 'marks': 'passed', 'source_comparison': 'passed'}}]}, ensure_ascii=False) + '\n'
            + closing)


def task_contract(root, page, enabled, figures=True):
    return ('## 이 작업에서 할 일\n\n' + answer_mode(enabled) + '\n\n' + figure_mode(figures) +
            '아래 Markdown을 작성한 뒤 바로 제출하세요. 지원 여부를 확인하려고 설치/설정/내부 Python 파일을 읽지 않습니다.\n'
            '제출 호출: `' + json.dumps(submit_call(root, page), ensure_ascii=False) + '`\n'
            '먼저 이 쪽 전체를 작성해 제출하세요. 제출이 형식·모든 수식을 한 번에 검사합니다. '
            '보고된 오류만 부분 수정하며, 반환된 설명으로 해결되지 않을 때만 hwp_help를 사용합니다. '
            '제출 전 지원 확인·상태 조회·수식별 시험 호출은 없습니다.\n\n')


def equation_guidance(latex, error):
    from restoration_diagnostics import equation_failure_details
    details = equation_failure_details(latex, error)
    codes = {d.get('code') for d in details.get('engine_diagnostics', [])}
    tokens = {d.get('token') for d in details.get('engine_diagnostics', [])}
    replacement = next((REPLACEMENTS[t] for t in tokens if t in REPLACEMENTS), None)
    if 'ambiguous_hat' in codes:
        hint = (r'\widehat{AB}는 모자(^) 기호로 인쇄됩니다. 원본이 호이면 $\overparen{AB}$, 각이면 $\angle ABC$로 쓰세요. '
                r'원본이 정말 모자 기호이면 $\hat{AB}$로 쓰세요.')
    elif replacement:
        hint = replacement
    elif r'\mid' in tokens:
        hint = r'집합 조건 구분선인 경우에만 \mid 대신 |를 쓰세요. 예: $A=\{x | x>0\}$. 나눗셈 관계 등 다른 의미라면 바꾸지 말고 해당 문항을 보고하세요.'
    elif 'spacing_approximation' in codes:
        hint = ('표시한 간격 명령은 HWP에서 근사되어 엄격 검증이 거부했습니다. 자동 삭제하거나 ~로 바꾸지 마세요. '
                '원본에서 서로 독립된 두 식 사이의 본문 공백인 경우에만 `$p:x=1$ $q:x=2$`처럼 '
                '수식 둘과 사이의 본문 공백으로 작성하세요. 수식 내부 간격이면 임의 변경하지 말고 보고하세요.')
    elif codes & {'missing_argument', 'syntax_error', 'duplicate_script'}:
        hint = (r'표시된 위치의 인수·괄호를 원본에 맞게 고치세요. 분수는 $\frac{분자}{분모}$, '
                r'아래첨자는 $A_{k}(a)$입니다. 값을 추측하거나 수학 내용을 바꾸지 말고 같은 reading.md를 부분 수정해 재제출하세요.')
    elif 'unsupported_text' in codes:
        hint = (r'\text{...} 안에는 한글 설명만 넣고 수학 명령은 밖에 두세요. '
                r'예: $A=\{x | x\le100,\text{ 자연수}\}$. 보고된 기호의 원래 의미를 보존하세요.')
    else:
        hint = ('반환된 기호·위치만 확인하여 reading.md의 해당 수식을 수정하세요. '
                '지원 예시는 hwp_help(topic="equations")에 있습니다. 보존 가능한 표현이 없으면 문항과 오류를 보고하세요. '
                'JSON 작성·내부 코드 열람·직접 변환기 실행은 필요 없습니다.')
    return {**details, 'hint': hint}


def author_help(root, page, topic='writing'):
    if type(page) is not int or page < 1:
        raise ValueError('page: use the positive page number from task.md')
    if topic not in ('writing', 'equations', 'figures'):
        raise ValueError('topic: choose writing, equations or figures')
    root = Path(root).resolve()
    manifest = job._manifest(root)
    if page not in manifest.get('question_pages', []) or not any(a['page'] == page for a in manifest['assignments']):
        raise ValueError('assigned_page_required: use your task.md job and page; ask main if not assigned')
    enabled = manifest.get('include_answers', False)
    result = {'status': 'help', 'page': page, 'topic': topic, 'include_answers': enabled,
              'answer_mode': answer_mode(enabled), 'visual_status': 'not_verified',
              'next_action': 'Write and submit the complete reading.md directly. Submission validates all equations together. Correct only reported errors; no separate preflight. Independent visual/answer review remains required.'}
    if topic in ('writing','equations'):
        state_path=root/'mcp/state.json'
        state=job.load_json(state_path) if state_path.exists() else {}
        errors=state.get('pages',{}).get(str(page),{}).get('errors',[])
        result.update(submit_call=submit_call(root,page),reported_errors=errors)
        if errors:
            result['next_action']='Use these existing submission errors and examples to correct only indicated lines, then resubmit the same reading.md. Help does not revalidate, mutate or approve a page.'
    if topic == 'writing':
        result.update(submit_call=submit_call(root, page),
            example='## left\n### q1\n1. $x+1$의 값은? [3점]\n\n::: choices 3\n① $1$ | ② $2$ | ③ $3$\n④ $4$ | ⑤ $5$\n:::',
            rules=['원본 단과 선택지 행(예: 3+2)을 보존합니다. N은 원본 한 줄 최대 개수입니다.',
                   '문장 안 수식은 $...$, 독립 수식은 빈 줄 사이 한 줄 $$...$$입니다.',
                   '한글 수식 설명은 \\text{자연수}, 빈칸은 수식 밖 [[box:(가)]]입니다.',
                   '형식 오류는 해당 줄만 수정합니다. 원본 재열기나 도형 재렌더는 필요 없습니다.'])
        if enabled:
            result['answer_example'] = '::: answer\n번호: 1\n정답: ③\n근거: 원본 조건에서 계산하고 ③과 대조했다.\n:::'
            result['answer_note'] = '위 정답은 형식 예시이며 실제 정답이 아닙니다. 실제 값을 계산·검산해 작성하세요.'
    elif topic == 'equations':
        result.update(examples=MATH_EXAMPLES,
            rules=[r'\text{한글}, \cdots, \emptyset, \varnothing을 지원합니다. 설명은 \text{}, 수학 명령은 그 밖에 둡니다.',
                   r'호는 \overparen{AB}, 각은 \angle ABC, 각도는 30^\circ, 평행은 \parallel입니다. \widehat{AB}는 모자 기호라서 호·각에 쓰지 않습니다.',
                   r'\mid는 지원하지 않습니다. 집합 조건 막대는 |를 사용합니다.',
                   r'\quad 등 근사 간격 명령을 임의 삭제·치환하지 않습니다. 본문 공백과 수식 내부 간격을 구분하세요.'])
    elif not manifest.get('include_figures', True):
        result.update(include_figures=False, rules=[
            '이 작업은 글과 수식만 옮깁니다(include_figures=false). 도형 자리 표시와 TeX를 쓰지 않고 도형 도구를 호출하지 않습니다.',
            'reading.md를 제출해 accepted가 오면 이 쪽은 끝입니다.'])
    else:
        result.update(rules=[
            'reading.md의 ![](figure:ID)와 같은 ID.tex를 배정 폴더에 저장합니다.',
            'ID.tex 첫 줄에 % width_mm=NN source_bbox_px=x,y,폭,높이 를 적고 hwp_render_figures(job,page)만 호출합니다. 엔진이 파일에서 목록을 만듭니다.',
            '반환된 pending review_tasks만 실제 원본과 비교합니다. 렌더 성공은 검수 통과가 아닙니다.',
            'hwp_review_figures는 새 batch_path와 관찰한 reviews를 씁니다. checks의 geometry,labels,marks,source_comparison은 정확히 passed/failed/not_verified입니다.',
            '모든 checks가 passed이고 실제 문제가 없을 때만 status=passed,issues=[]입니다. 변경 없는 검수는 엔진이 재사용합니다.'],
            reference=str(Path(__file__).resolve().parents[1] / 'references/tikz-exam.md'))
    return result
