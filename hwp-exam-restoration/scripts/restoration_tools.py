"""Mechanical worker inputs and diagnostics; never authors or repairs OCR content."""
from pathlib import Path
import copy, math, re, sys


def prepare_inputs(root, manifest):
    from PIL import Image
    from restoration_job import save_json, digest
    from restoration_prepare import environment_info
    root=Path(root);folder=root/'inputs';folder.mkdir(exist_ok=True);inputs=[]
    for page in manifest['pages']:
        image=root/page['image']['path'];crops=[]
        with Image.open(image) as im:
            w,h=im.size;overlap=max(1,round(w*.03))
            for name,box in [('left',(0,0,w//2+overlap,h)),('right',(w//2-overlap,0,w,h))]:
                path=folder/f"page-{page['page']:04d}-{name}.png";im.crop(box).save(path)
                crops.append({'path':str(path.resolve()),'sha256':digest(path),'pixel_box':list(box)})
        inputs.append({'page':page['page'],'navigation_crops':crops})
    save_json(folder/'index.json',{'schema':'restoration-inputs/1','pages':inputs,
        'note':'Overlapping half-page navigation crops only; use the full page for actual columns and question bounds.'})
    save_json(root/'environment.json',environment_info())
    return str((folder/'index.json').resolve())


def worker_input(root,manifest,assignment):
    from restoration_job import load_json,save_json
    root=Path(root);page=manifest['pages'][assignment['page']-1]
    folder=root/'workers'/f"page-{page['page']:04d}";folder.mkdir(parents=True,exist_ok=True)
    skill=Path(__file__).resolve().parents[1]
    profile=load_json(skill/'assets/templates/pdf2hwp-grid/profile.json')
    metadata={'source_sha256':manifest['source']['sha256'],'page_number':page['page'],
        'size_mm':[page['width_mm'],page['height_mm']],
        'assignment_id':assignment['assignment_id'],'worker_id':assignment['worker_id']}
    index=root/'inputs/index.json';crops=[]
    if index.is_file():
        crops=next((p['navigation_crops'] for p in load_json(index)['pages'] if p['page']==page['page']),[])
    internal=root/'inputs/worker-bindings';internal.mkdir(parents=True,exist_ok=True)
    result=internal/f"page-{page['page']:04d}-result.json"
    draft=folder/'draft.json';compiled=folder/'compiled.json';environment=root/'environment.json'
    if not environment.exists():
        from restoration_prepare import environment_info
        save_json(environment,environment_info())
    if not draft.exists():save_json(draft,{'schema':'restoration-draft/1','issues':[],'regions':[],'questions':[]})
    command=[sys.executable,str(skill/'scripts/restore.py')]
    value={'schema':'restoration-worker-input/1','job':str(root.resolve()),'skill_dir':str(skill),
        'python':sys.executable,'restore':str(skill/'scripts/restore.py'),
        'page_image':str((root/page['image']['path']).resolve()),'navigation_crops':crops,
        'page_metadata':metadata,'font_family':profile['body_font'],'font_pt':profile['body_font_pt'],
        'result_path':str(result.resolve()),'work_dir':str(folder.resolve()),
        'skeleton':{'version':2,**metadata,'issues':[],'regions':[],'questions':[],'blocks':[]},
        'references':[str(skill/'references/compact-draft.md'),str(skill/'examples/ocr-draft.json')],
        'validation_command':[sys.executable,str(skill/'scripts/restore.py'),'validate-page',str(root.resolve()),str(result.resolve())],
        'note':'Skeleton is intentionally incomplete. Transcribe the assigned original; do not copy example content or infer question bounds from the navigation crops.'}
    # Legacy metadata remains available to direct-v2 callers, but new workers
    # receive only the compact draft. Existing result/input files are untouched.
    if not result.exists():save_json(result,value['skeleton'])
    value.update(authoring_mode='compact_draft',draft_path=str(draft.resolve()),compiled_result_path=str(compiled.resolve()),
        compile_command=command+['compile-draft',str(root.resolve()),str(page['page']),str(draft.resolve()),str(compiled.resolve())],
        environment_file=str(environment.resolve()),environment=load_json(environment),
        source_views_command=command+['source-views',str(root.resolve()),str(page['page']),str(folder/'views-spec.json'),str(folder/'source-views')],
        figures_command=command+['figures',str(root.resolve()),str(page['page']),str(folder/'figures-spec.json'),str(folder/'figures')],
        figures_finalize_command=command+['figures-finalize',str(root.resolve()),str(page['page']),str(folder/'figures')])
    value['note']='Fill draft_path from the original, then run compile_command and submit compiled_result_path. Add --figures with the finalized figures JSON when using figure_ref. Empty drafts cannot be accepted. result_path/skeleton/validation_command are retained only for direct v2 editing. Never validate an empty draft to discover its format.'
    from restoration_handoff import write_worker_brief
    value['worker_brief']=write_worker_brief(value)
    path=root/'inputs/worker-bindings'/f"page-{page['page']:04d}.json"
    path.parent.mkdir(parents=True,exist_ok=True)
    save_json(path,value);return str(path.resolve())


def equation_hint(latex):
    if not latex.strip():
        return '빈 수식입니다. 원본 수식을 입력하세요. 그림 전용 문단이면 빈 equation 대신 runs: []를 사용하세요.'
    if re.search(r'\\mid\b',latex):
        return r'\mid는 미지원입니다. 집합 조건 구분선이면 |를 사용하고 원본 의미를 대조하세요. 나눗셈 관계 등 다른 의미는 임의 치환하지 말고 issues로 보고하세요.'
    if re.search(r'\\text\b',latex):
        return r'\text{...}는 지원합니다. 중괄호와 중첩 문법을 확인하세요. 본문 설명은 인접한 kind:text run으로 분리할 수 있지만 수식 의미를 바꾸지 마세요.'
    return '보고된 LaTeX의 괄호와 명령을 원본과 대조하여 Markdown의 해당 수식만 수정하세요. 의미를 보존할 수 없으면 문항과 오류를 보고하세요. JSON 작성이나 내부 소스 탐색은 필요 없습니다.'


def equation_entries(value,location='page'):
    if isinstance(value,dict):
        if value.get('kind')=='equation' and isinstance(value.get('latex'),str):
            yield location,value['latex']
        for key,child in value.items():
            if key in ('content','runs','rows','questions','blocks'):yield from equation_entries(child,location+'.'+key)
    elif isinstance(value,list):
        for i,child in enumerate(value):yield from equation_entries(child,f'{location}[{i}]')


def content_errors(value):
    from restoration_diagnostics import contract_errors, equation_failure_details
    from restoration_compiler import _studio_equation
    errors=contract_errors(value)
    # One unsupported formula must not hide the remaining formula errors.
    for location,latex in equation_entries(value):
        try:_studio_equation(latex,location)
        except (ValueError,KeyError,TypeError) as exc:
            errors.append({'code':'equation','location':location,'latex':latex,'message':str(exc),
                'hint':equation_hint(latex),**equation_failure_details(latex,exc)})
    if not errors or all(e['code']=='equation' for e in errors):
        from question_contract import figure_items
        from figure_provenance import validate_figures
        from restoration_job import _read,digest
        for qid,figure in figure_items(value):
            try:
                path=Path(figure['path'])
                if not path.is_absolute():raise ValueError('image_asset_requires_absolute_path')
                _read(path)
                if digest(path)!=figure['sha256']:raise ValueError('image_asset_hash_mismatch')
                probe={**value,'blocks':[],'questions':[{'id':qid,'content':[{'figure':figure}]}]}
                validate_figures(probe)
            except (ValueError,KeyError,TypeError,OSError) as exc:
                errors.append({'code':'figure','location':qid,'message':str(exc)})
    return errors


def figure_info(root,manifest,assignment,qid,render_path,*,width_mm,review=None):
    from restoration_job import load_json,digest
    from figure_provenance import artifact,validate_figures
    import fitz
    if not isinstance(qid,str) or not qid.strip():raise ValueError('question_id_required')
    if type(width_mm) not in (float,int) or not math.isfinite(width_mm) or width_mm<=0:raise ValueError('positive_finite_width_required')
    render_path=Path(render_path).resolve(strict=True);render=load_json(render_path)
    if (render.get('schema')!='tikz-render/1' or render.get('renderer')!='tikz' or
        render.get('status')!='rendered_pending_review' or render.get('exit_code')!=0):raise ValueError('successful_tikz_render_required')
    for key in ('source','pdf','png','log'):artifact(render[key])
    with fitz.open(render['pdf']['path']) as doc:
        if len(doc)!=1:raise ValueError('single_page_figure_required')
        ratio=doc[0].rect.height/doc[0].rect.width
    render_ref={'path':str(render_path),'sha256':digest(render_path)}
    figure={**render['png'],'size_mm':[width_mm,round(width_mm*ratio,6)],'offset_mm':[0,0], 'tikz':{'render':render_ref}}
    draft={'schema':'tikz-review/1','status':'pending','source_sha256':manifest['source']['sha256'],
        'page_number':assignment['page'],'question_id':qid,'worker_id':assignment['worker_id'],
        'render_sha256':render_ref['sha256'],
        'checks':{k:'not_verified' for k in ('geometry','labels','marks','source_comparison')},'issues':[]}
    if review is not None:
        review=Path(review).resolve(strict=True);figure['tikz']['review']={'path':str(review),'sha256':digest(review)}
        validate_figures({**draft,'blocks':[],'questions':[{'id':qid,'content':[{'figure':figure}]}]})
    return {'status':'ready' if review else 'pending_review','figure':figure,'review_template':draft,
            'note':'Open the original and rendered PNG before completing the review. This helper does not perform visual review.'}
