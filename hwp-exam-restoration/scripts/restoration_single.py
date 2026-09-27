"""One producer per page and one independent final reviewer for the whole job."""
from copy import deepcopy
import hashlib,json,math,os,re,subprocess,sys,tempfile,time,uuid
from pathlib import Path
import restoration_job as job
import restoration_batch as batch
import restoration_service as shared
from restoration_markdown import parse_reading_markdown
from restoration_reading import _figure_ids

_NATIVE_PROCESSES={}
RUNTIME_VERSION='2.7.3'  # Loaded code version, never read from a replaced manifest.
TIKZ_HELPERS=shared.SKILL/'assets/tikz/exam-marks.tex'


def diagram_document(text):
    """Accept one picture fragment or an existing full document, without rewriting it."""
    import importlib.util
    spec=importlib.util.spec_from_file_location('restoration_tikz_validator',shared.SKILL/'runtime/tikz_render.py')
    renderer=importlib.util.module_from_spec(spec);spec.loader.exec_module(renderer)
    renderer.validate_source(text)
    clean=re.sub(r'(?<!\\)%[^\n]*','',text).strip()
    if re.search(r'\\documentclass\b',clean):return text
    if (not clean.startswith(r'\begin{tikzpicture}') or not clean.endswith(r'\end{tikzpicture}')
            or clean.count(r'\begin{tikzpicture}')!=1 or clean.count(r'\end{tikzpicture}')!=1):
        raise ValueError('one_tikzpicture_fragment_or_complete_document_required')
    from restoration_tikz_templates import expand_templates
    text=expand_templates(text)
    clean=re.sub(r'(?<!\\)%[^\n]*','',text).strip()
    # Inline only when used: saved source hashes bind helper changes to the
    # existing render cache, without external input files or new model calls.
    helpers=(TIKZ_HELPERS.read_text(encoding='utf-8')+'\n'
             if re.search(r'\\Exam(?:RightAngle|LengthArc|Label|Ticks)\b',clean) else '')
    return ('\\documentclass[tikz,border=2pt]{standalone}\n'
            '\\usepackage{kotex}\n\\usepackage{amsmath}\n'
            '\\usetikzlibrary{calc,arrows.meta,angles,quotes}\n'
            +helpers+'\\begin{document}\n'+text+'\n\\end{document}\n')


def figure_review_key(root,page,value,item):
    """Approval depends on semantics and printed size, not only cached pixels."""
    a=assignment(root,page)
    question=next(q for q in value['questions'] if q['id']==item['question_id'])
    return fingerprint({'page':page,'worker':a['worker_id'],'assignment':a['assignment_id'],
                        'source':job._manifest(root)['source']['sha256'],
                        'source_image':job.digest(shared._source(root,page)),
                        'question':question,'id':item['id'],'width_mm':item['width_mm'],
                        'render':item['render_sha256']})

def unused_output(path):
    """Choose a revision without replacing a prior document or build receipt."""
    requested=Path(path).resolve()
    if requested.suffix.lower()!='.hwpx':raise ValueError('output_must_end_hwpx')
    candidate=requested;revision=2
    while any(candidate.with_suffix(s).exists() for s in ('.hwpx','.build.json','.hwp','.pdf')):
        candidate=requested.with_name(f'{requested.stem}_v{revision}.hwpx');revision+=1
    return candidate

def question_pages(manifest):
    return manifest.get('question_pages', [x['page'] for x in manifest['pages']])

def automatic_layout(root,page,value):
    """Mechanical grid slots, not a transcription of scan coordinates."""
    source=job._manifest(root)['pages'][page-1]
    width,height=source['width_mm'],source['height_mm']
    rows=['units: mm','| type | id | region | x | y | width | height |','| --- | --- | --- | --- | --- | --- | --- |']
    for column,x in [('left',width*.05),('right',width*.525)]:
        qs=[q for q in value['questions'] if q['column']==column]
        if not qs:continue
        if len(qs)>6:raise ValueError('grid_column_requires_1_to_6_questions')
        w,h=width*.425,height*.9
        rows.append(f'| region | {column} | - | {x} | {height*.05} | {w} | {h} |')
        for i,q in enumerate(qs):
            rows.append(f'| question | {q["id"]} | {column} | {x} | {height*.05+i*h/len(qs)} | {w} | {h/len(qs)} |')
    return '\n'.join(rows)

def accept_ready(root,state,page):
    value=reading(root,state,page)
    ids=[f for q in value['questions'] for f in _figure_ids(q)]
    if ids and not state['figures'].get(str(page)):
        return {'status':'ready_for_figures','page':page,'figure_ids':ids,
                'next_action':'Same producer supplies only the required diagram TeX. Render and compare figures; layout is automatic.'}
    return perform('compose',{'page':page,'layout_markdown':automatic_layout(root,page,value)},root,state)

def native_running(run):
    """False only for a confirmed exit; unknown ownership never allows overlap."""
    if run.get('returncode') is not None:return False
    pid=run.get('pid');process=_NATIVE_PROCESSES.get(pid)
    if process is not None:
        code=process.poll()
        if code is None:return True
        run['returncode']=code;_NATIVE_PROCESSES.pop(pid,None);return False
    if not pid:return None
    if os.name=='nt':
        import ctypes
        from ctypes import wintypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        kernel.OpenProcess.restype=wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
        kernel.WaitForSingleObject.restype=wintypes.DWORD
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        handle=kernel.OpenProcess(0x100000,False,pid)
        if not handle:return False if ctypes.get_last_error()==87 else None
        try:
            status=kernel.WaitForSingleObject(handle,0)
            return True if status==258 else False if status==0 else None
        finally:kernel.CloseHandle(handle)
    try:os.kill(pid,0);return True
    except ProcessLookupError:return False
    except PermissionError:return None
def save(root,state): job.save_json(root/'mcp/state.json',state)
def fingerprint(value): return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
def info(root,page):
    from PIL import Image
    path=shared._source(root,page)
    with Image.open(path) as im: size=list(im.size)
    return {'source_image':str(path),'source_size_px':size,'coordinate_format':'x,y,width,height; full-page pixels'}
def assignment(root,page):
    a=next((a for a in job._manifest(root)['assignments'] if a['page']==page),None)
    if not a: raise ValueError('assign_one_producer_first')
    return a
def reading(root,state,page):
    s=state['pages'].get(str(page),{})
    if s.get('errors'): raise ValueError('correct_reported_reading_errors_before_production')
    if not s.get('reading'): raise ValueError('submit_page_markdown_first')
    return job.load_json(job._artifact(root,s['reading']))
def figure_context(value): return fingerprint([q for q in value['questions'] if _figure_ids(q)])

def output_views(root,state,page,requests):
    """Inspect only requested pixels from the current, hash-verified output."""
    if not requests:raise ValueError('output_inspection_requires_specific_requests')
    current=collect_native(root,state)
    if not current or current['status'] not in ('pending_review','complete'):
        raise ValueError('current_native_output_required_for_inspection')
    task=next((t for t in state['review_tasks'] if t['page']==page),None)
    if not task:raise ValueError('selected_output_page_required')
    from PIL import Image
    from restoration_prepare import resolve_pixel_box
    with Image.open(task['output_image']) as original:
        width,height=original.size
        context={'frames':{'page':(0,0,width,height)},'scale':(1,1)}
        rows=[];seen=set()
        for request in requests:
            name=request['id']
            if (not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}',name)
                    or re.fullmatch(r'(?i:con|prn|aux|nul|com[1-9]|lpt[1-9])',name)
                    or name.casefold() in seen):raise ValueError('invalid_or_duplicate_output_view_id')
            seen.add(name.casefold())
            x,y,w,h=resolve_pixel_box(context,request['bbox_px'],'page')['page_bbox_px']
            rows.append((name,(math.floor(x),math.floor(y),math.ceil(x+w),math.ceil(y+h))))
        out=root/'mcp'/('views-'+uuid.uuid4().hex);out.mkdir()
        paths=[]
        for name,box in rows:
            path=out/(name+'.png');original.crop(box).save(path);paths.append(str(path))
    return paths,[width,height]
def composition_inputs(state,page):
    return fingerprint({'reading':state['pages'][str(page)].get('reading'),
                        'figures':state['figures'].get(str(page))})
def invalidate_output(state):
    if state.get('native_run'): state['output_stale']=True
    state['reviews']={}


def reconsider_selected_figure(root,state,page,item):
    """A fresh verdict on a selected render supersedes its saved approval."""
    selected=state['figures'].get(str(page))
    if not selected:return
    if job.digest(selected['path'])!=selected['sha256']:raise ValueError('selected_figures_changed')
    figure=job.load_json(selected['path'])['figures'].get(item['id'],{})
    if (figure.get('tikz',{}).get('render',{}).get('sha256')==item.get('render_sha256')
            and figure.get('size_mm',[None])[0]==item['width_mm']):
        state['figures'].pop(str(page),None)
        state['pages'][str(page)].pop('composed',None)
        state['pages'][str(page)].pop('composed_inputs',None)
        state.get('page_review_cache',{}).pop(str(page),None)
        invalidate_output(state)
def text_input(root,page,p,inline,pathkey,filename,*,with_source=False):
    text=p.get(inline);path=p.get(pathkey)
    if (text is None)==(path is None): raise ValueError('provide_exactly_one_'+inline+'_or_'+pathkey)
    source=None
    if path is not None:
        actual=Path(path).resolve(strict=True);expected=(root/'workers'/f'page-{page:04d}'/filename).resolve()
        if actual!=expected or not actual.is_relative_to(root): raise ValueError('use_assigned_output_path: '+str(expected))
        raw=actual.read_bytes();text=raw.decode('utf-8-sig').replace('\r\n','\n').replace('\r','\n')
        source={'path':str(actual),'sha256':hashlib.sha256(raw).hexdigest()}
    return (text,source) if with_source else text
def validate_accepted_result(root,value,a):
    if a.get('workflow')!='single-review/1': return
    state=job.load_json(Path(root)/'mcp/state.json');page=a['page']
    reading(root,state,page)
    ref=state['pages'][str(page)].get('composed')
    if (not ref or state['pages'][str(page)].get('composed_inputs')!=composition_inputs(state,page)
            or job.load_json(job._artifact(root,ref))!=value):
        raise ValueError('compose_current_reading_before_build')

def collect_native(root,state):
    run=state.get('native_run')
    if not run: return None
    if state.get('output_stale'):
        return {'status':'needs_rebuild','visual_status':'not_verified',
                'next_action':'Finish only changed page work and call hwp_build with the same desired output path; an unused revision is selected automatically. Send the new output to the same reviewer.'}
    path=Path(run['receipt'])
    if not path.exists():
        if native_running(run) is False:
            return {'status':'failed','message':'native_export_exited_without_receipt','log':run.get('log'),
                    'returncode':run.get('returncode'),'next_action':'Report the saved export log. Resolve the failure and confirm owned-session cleanup before retrying; output names are selected automatically. Do not inspect server internals.'}
        elapsed=time.time()-run.get('started',time.time())
        return {'status':'building' if elapsed<330 else 'failed','message':'native_export_pending' if elapsed<330 else 'native_export_did_not_return_receipt',
                'next_check_after_seconds':60,'log':run.get('log'),
                'next_action':'Do not start another export or inspect server internals. Wait before one status check.' if elapsed<330 else 'Report the saved export log; do not kill user Hangul sessions or repeatedly rebuild.'}
    native_running(run)  # Reap completed owned processes even on successful output.
    native=job.load_json(path)
    if native.get('status')!='rendered' or native.get('review_inputs',{}).get('status')!='pending_review':
        return {'status':'failed','message':native.get('error','native_review_inputs_missing'),'next_action':'Report the native failure and saved log. Never declare completion from HWPX alone.','log':run.get('log')}
    assembled=job.assemble(root)
    if native.get('job')!=str(root) or native.get('pages_sha256')!=batch.pages_digest(assembled):
        raise ValueError('changed_pages_require_new_output_and_review')
    selected=[p['page_number'] for p in assembled];count=len(selected)
    if native.get('page_count')!=count or [p['page'] for p in native['review_inputs']['pages']]!=selected:
        raise ValueError('native_review_must_cover_all_source_pages_once')
    for ref in native['artifacts'].values():
        if job.digest(ref['path'])!=ref['sha256']: raise ValueError('native_artifact_changed')
    bound=job.digest(path)
    if state.get('native',{}).get('sha256')!=bound:
        tasks=[];bindings={};reused={};staged={'assets':dict(state['assets'])}
        for position,packet in enumerate(native['review_inputs']['pages'],1):
            data=job.load_json(packet['review_input']);n=packet['page']
            answer_page=assembled[position-1].get('role')=='answer_sheet'
            source_key='answer_reference' if answer_page else 'source_image'
            source=Path(assembled[position-1]['answer_reference']['path']) if answer_page else shared._source(root,n)
            if Path(data[source_key]).resolve()!=source: raise ValueError('review_source_mismatch')
            if answer_page and job.digest(source)!=assembled[position-1]['answer_reference']['sha256']:raise ValueError('answer_reference_changed')
            if job.digest(data['output_image']['path'])!=data['output_image']['sha256']: raise ValueError('review_image_changed')
            from PIL import Image
            with Image.open(data['output_image']['path']) as image:
                output_size_px=list(image.size)
            task={'page':n,source_key:shared._register(staged,source),
                  'output_image':shared._register(staged,data['output_image']['path']),
                  'output_size_px':output_size_px}
            if answer_page:
                task.update(kind='answer_sheet',instructions=data['instructions'],question_id='answer-sheet')
            else:
                with Image.open(source) as image:
                    task['source_size_px']=list(image.size)
            tasks.append(task)
            bindings[str(n)]=fingerprint({'source':job.digest(source),
                'output':data['output_image']['sha256'],'page':n,'position':position,'count':count,
                'content':batch.native_page(assembled[position-1])})
            previous=state.get('page_review_cache',{}).get(str(n),{})
            semantic_answer_reuse=False
            if answer_page:
                current_questions=assembled[position-1]['answer_review_questions']
                old_questions=previous.get('answer_review_questions',{})
                prior_valid=(previous.get('status')=='passed' and previous.get('issues')==[]
                    and previous.get('reviewer_id')==state.get('reviewer_id')
                    and bool(old_questions) and bool(previous.get('evidence')))
                if prior_valid:job._artifact(root,previous['evidence'])
                stable_keys=(prior_valid and list(old_questions)==list(current_questions))
                changed=[key for key in current_questions if not stable_keys
                    or old_questions[key]['revision']!=current_questions[key]['revision']]
                visual_unchanged=bool(prior_valid
                    and previous.get('answer_output_sha256')==data['output_image']['sha256']
                    and previous.get('answer_output_position')==position
                    and previous.get('answer_output_count')==count
                    and stable_keys
                    and all((old_questions[key].get('label'),old_questions[key].get('kind'),old_questions[key].get('answer'))
                            ==(current_questions[key]['label'],current_questions[key]['kind'],current_questions[key]['answer'])
                            for key in current_questions))
                scope={'mode':'changed_questions' if prior_valid else 'full',
                       'question_keys':changed,
                       'output_image_unchanged':visual_unchanged}
                if prior_valid:
                    scope['changed_questions']=[{**{k:v for k,v in current_questions[key].items() if k!='revision'},
                                                 'source_image':str(shared._source(root,current_questions[key]['source_page']))}
                                                for key in changed]
                    tasks[-1]['instructions']=('같은 검수자의 이전 정답표 통과 근거가 확인되었습니다. '
                        'changed_questions에 나온 문항만 원본 조건으로 다시 독립 검산하고 나머지는 이전 검산을 재사용하세요. '
                        'output_image_unchanged=true면 이전 정답표의 번호·표기·잘림 시각 검수를 재사용하고, '
                        'false면 현재 출력 정답표 전체를 다시 보세요. 이전 검수 내용을 기억할 수 없거나 불확실하면 '
                        '전체 answer_reference를 확인하세요. 실제 변경 문항·출력에 오류가 있으면 failed입니다.')
                tasks[-1]['answer_review_scope']=scope
                semantic_answer_reuse=bool(prior_valid and not changed and visual_unchanged)
            if ((previous.get('binding')==bindings[str(n)] or semantic_answer_reuse) and previous.get('reviewer_id')==state.get('reviewer_id')
                    and previous.get('status')=='passed' and previous.get('issues')==[]):
                job._artifact(root,previous['evidence'])  # Verify saved actual review evidence.
                reused[str(n)]={**previous,'binding':bindings[str(n)],'output_sha256':bound,
                    'reused_from_output_sha256':previous['output_sha256']}
        state.update(native={'path':str(path),'sha256':bound},reviews=reused,
                     review_tasks=tasks,review_bindings=bindings,assets=staged['assets'])
    for task in state['review_tasks']:
        for key in (('answer_reference' if task.get('kind')=='answer_sheet' else 'source_image'),'output_image'):
            if job.digest(task[key])!=state['assets'][task[key]]['sha256']: raise ValueError('review_image_changed')
        # Jobs built by earlier runtimes lack pixel sizes but retain valid images.
        if 'output_size_px' not in task:
            from PIL import Image
            with Image.open(task['output_image']) as image:
                task['output_size_px']=list(image.size)
        if task.get('kind')!='answer_sheet' and 'source_size_px' not in task:
            from PIL import Image
            with Image.open(task['source_image']) as image:
                task['source_size_px']=list(image.size)
    for review in state['reviews'].values():job._artifact(root,review['evidence'])
    complete=all(state['reviews'].get(str(n),{}).get('status')=='passed' for n in selected)
    return {'status':'complete' if complete else 'pending_review','visual_status':'passed' if complete else 'not_verified',
            'build_output':run.get('build_output'),
            'artifacts':{k:v['path'] for k,v in native['artifacts'].items()},'reviewer_id':state.get('reviewer_id'),
            'review_tasks':[t for t in state['review_tasks'] if state['reviews'].get(str(t['page']),{}).get('status')!='passed'],
            'reused_review_pages':[int(n) for n,r in state['reviews'].items() if r.get('reused_from_output_sha256')],
            'reviews':{n:{k:v for k,v in record.items() if k!='answer_review_questions'}
                       for n,record in state['reviews'].items()},
            'next_action':'Deliver verified output.' if complete else 'Use the SAME independent reviewer for these pending pages (create ONE only on the first build). Compare every question, math, marks, order and clipping. Reuse a source image already visible in that reviewer context; reopen only if unavailable or unclear. Return actual ID, paths, verdict and precise issues; record passed AND failed pages with hwp_finish_review each round before repairs. Unchanged verified pages need no new image reads. Never use a producer as reviewer.'}

def perform(action,p,root,state):
    page=p.get('page')
    if action=='prepare':
        if (root/'manifest.json').exists():
            m=job._manifest(root)
            if job.digest(p['source'])!=m['source']['sha256']:raise ValueError('prepared_source_mismatch')
        else:m=job.prepare(Path(p['source']),root,dpi=p.get('dpi'),workflow='single-review/1')
        selected=p.get('question_pages',m.get('question_pages'))
        if selected is None:
            from PIL import Image,ImageDraw
            overview=root/'pages/overview.jpg'
            if not overview.exists():
                canvas=Image.new('RGB',(800,320*((len(m['pages'])+3)//4)),'#ddd');draw=ImageDraw.Draw(canvas)
                for i,item in enumerate(m['pages']):
                    with Image.open(root/item['image']['path']) as im:
                        im.thumbnail((190,290));x=(i%4)*200;y=(i//4)*320
                        canvas.paste(im,(x,y+22));draw.text((x+8,y+4),str(item['page']),fill='black')
                canvas.save(overview)
            return {'status':'needs_selection','job':str(root),'source_page_count':len(m['pages']),
                    'overview_image':str(overview),'next_action':'View overview once, select question pages only, then call hwp_prepare with question_pages. Exclude cover, blank, instructions-only and answer pages without asking the user.'}
        if not isinstance(selected,list) or not selected or any(type(n) is not int or not 1<=n<=len(m['pages']) for n in selected) or selected!=sorted(set(selected)):
            raise ValueError('question_pages_must_be_sorted_unique_source_page_numbers')
        if m['assignments'] and selected!=question_pages(m):raise ValueError('question_pages_frozen_after_assignment')
        if m['assignments']:
            revision=2;restart=root.with_name(root.name+'_run2')
            while restart.exists():
                revision+=1;restart=root.with_name(f'{root.name}_run{revision}')
            return {'status':'already_assigned','job':str(root),'spawn_requests':[],
                    'assigned_workers':[{'page':a['page'],'worker_id':a['worker_id']} for a in m['assignments']],
                    'restart_job':str(restart),
                    'next_action':'Do not spawn duplicate workers. Rewind does not undo saved assignments. For an explicit restart after stopping old workers, call hwp_prepare with restart_job and the same source/question_pages; then use the returned job path. For continuation, keep the existing job and its workers. Do not delete old files or search configuration/logs.'}
        m['question_pages']=selected
        m['include_answers']=bool(p.get('include_answers',m.get('include_answers',False)))
        job.save_json(root/'manifest.json',m)
        return {'status':'prepared','job':str(root),'page_count':len(selected),
                'spawn_requests':[{'page':n,'output_page':i,'role':'producer','type':'self'} for i,n in enumerate(selected,1)],
                'next_action':'First spawn one fresh TypeName=self subagent with Role=hwp-restoration-reader per selected page, then hwp_assign with the actual returned worker ID and spawn response. Send the returned task path to that worker. No guessed IDs or invented evidence; layout is automatic.'}
    if action=='assign':
        m=job._manifest(root)
        if not m.get('question_pages') or page not in m['question_pages']:raise ValueError('select_question_pages_before_assignment')
        if state.get('reviewer_id')==p['worker_id']: raise ValueError('producer_cannot_be_final_reviewer')
        previous=next((a for a in m['assignments'] if a['page']==page),None)
        if previous:
            if (previous['worker_id']!=p['worker_id']
                    or job._artifact(root,previous['evidence']).read_bytes()!=p['evidence'].encode('utf-8')):
                raise ValueError('page_already_assigned_to_different_worker_or_evidence')
            task=root/'workers'/f'page-{page:04d}'/'task.md'
            if not task.is_file():raise FileNotFoundError('assigned_task_missing: '+str(task))
            return {'status':'assigned','unchanged':True,'worker_id':p['worker_id'],'page':page,
                    **info(root,page),'worker_instructions':str(task),'markdown_path':str(task.with_name('reading.md')),
                    'next_action':'Keep this existing worker and its task. Do not create a duplicate worker or repeat completed work.'}
        evidence=shared._fresh(root,'.log');evidence.write_bytes(p['evidence'].encode('utf-8'))
        a=job.assign(root,page,p['worker_id'],evidence)
        if a.get('workflow')!='single-review/1':
            m=job._manifest(root);next(x for x in m['assignments'] if x['page']==page)['workflow']='single-review/1';job.save_json(root/'manifest.json',m)
        folder=root/'workers'/f'page-{page:04d}';folder.mkdir(parents=True,exist_ok=True)
        task=folder/'task.md';source=info(root,page)
        from restoration_author_help import task_contract
        task.write_text(f'# {page}쪽 제작 / {p["worker_id"]}\n\njob: {root}\npage: {page}\n원본: {source["source_image"]}\n원본 크기(px): {source["source_size_px"]}\n저장: {folder}\n\n'
          +task_contract(root,page,m.get('include_answers',False))
          +'원본 전체를 한 번 읽고 아래 형식으로 reading.md에 문제를 작성하세요. 필요한 도형 TeX는 같은 작업에서 작성하세요. '
          '불명확한 곳만 문항 ID·원본 픽셀 bbox·사유로 hwp_inspect 확대 요청하세요. 제출 전에도 가능합니다. '
          '도형이 있으면 아래 경로의 규칙을 그때만 읽으세요: '
          f'{shared.SKILL / "references/tikz-exam.md"}\n'
          '이 역할 지침을 따라 자기 쪽 제출·도형 대조까지 MCP로 직접 완료하세요: '
          f'{shared.SKILL / "agents/hwp-restoration-reader.md"}. '
          'accepted 전에는 완료 보고하지 마세요. 이미 본 원본은 재열지 않고, 수정은 부분 변경만 하며, 빌드·최종 검수는 메인이 맡습니다.\n\n'
          +(shared.SKILL/'references/markdown-format.md').read_text(encoding='utf-8')
          + ('\n\n'+(shared.SKILL/'references/answer-sheet.md').read_text(encoding='utf-8') if m.get('include_answers') else ''),encoding='utf-8')
        return {'status':'assigned','worker_id':p['worker_id'],'page':page,**source,'worker_instructions':str(task),
                'markdown_path':str(folder/'reading.md'),'next_action':'Send this task to the sole producer, who submits and reviews figures via MCP until accepted. Main waits for accepted; no relaying or content reads.'}
    if action=='submit_reading':
        a=assignment(root,page);m=job._manifest(root)
        s=state['pages'].setdefault(str(page),{});s['errors']=['submission_pending']
        text=text_input(root,page,p,'markdown','markdown_path','reading.md')
        from restoration_submission import validate_submission,SubmissionError,body_equation_source_map
        answer_errors=[];answer_rows=[]
        try:
            value,answer_rows=validate_submission(text,page=page,worker_id=a['worker_id'],
                                                  source_sha256=m['source']['sha256'],include_answers=m.get('include_answers',False))
        except SubmissionError as exc:
            answer_errors=exc.errors;value=exc.value
            if value is None:
                s['errors']=answer_errors;invalidate_output(state)
                return failure_result(action,exc)
        s['submitted']=job._snapshot(root,'mcp/evidence',json.dumps(value,ensure_ascii=False).encode(),'.json')
        from restoration_tools import equation_entries
        from restoration_author_help import equation_guidance
        from restoration_compiler import _studio_equation
        errors=list(answer_errors);source_map=None
        for q in value['questions']:
            if q['id'].lower() in ('cover','blank','footer','header','copyright'):errors.append({'question_id':q['id'],'message':'question_content_only'})
            if q['uncertain']: errors.append({'question_id':q['id'],'message':'Resolve this marked uncertainty against the source before production.'})
            for loc,latex in equation_entries(q,q['id']):
                try: _studio_equation(latex,loc)
                except (ValueError,KeyError,TypeError) as exc:
                    if source_map is None:source_map=body_equation_source_map(text,value)
                    errors.append({'question_id':q['id'],'location':loc,'latex':latex,'message':str(exc),
                                   **equation_guidance(latex,exc),**source_map[loc]})
        if not value['complete'] or value['issues']: errors.append({'message':'incomplete_reading','issues':value['issues']})
        s['errors']=errors
        if errors:
            invalidate_output(state)
            return {'status':'failed','error_kind':'input','errors':errors,
                    'next_action':'Send all reported errors to the same producer together. Correct only the indicated original lines/questions; do not reread images or recalculate unchanged answers for syntax-only fixes.'}
        if m.get('include_answers'):
            answer_value={'reading_sha256':s['submitted']['sha256'],'rows':answer_rows}
            previous=job.load_json(job._artifact(root,s['answers'])) if s.get('answers') else None
            if previous!=answer_value:
                s['answers']=job._snapshot(root,'mcp/evidence',json.dumps(answer_value,ensure_ascii=False).encode(),'.json')
                invalidate_output(state)
        if s.get('reading') and job.load_json(job._artifact(root,s['reading']))==value:
            if s.get('composed') and s.get('composed_inputs')==composition_inputs(state,page):return {'status':'accepted','page':page,'unchanged':True}
            return accept_ready(root,state,page)
        ref=job._snapshot(root,'mcp/evidence',json.dumps(value,ensure_ascii=False).encode(),'.json')
        job._snapshot(root,'mcp/evidence',text.encode(),'.md')
        s.update(reading=ref);s.pop('composed',None);s.pop('composed_inputs',None);invalidate_output(state)
        if state['figures'].get(str(page),{}).get('context')!=figure_context(value): state['figures'].pop(str(page),None)
        return accept_ready(root,state,page)
    if action=='inspect':
        requests=p.get('requests');target=p.get('target','source')
        answer_page=page==len(job._manifest(root)['pages'])+1 and job._manifest(root).get('include_answers')
        if answer_page:
            if target!='output' or not requests or any(r['question_id']!='answer-sheet' or not r.get('reason','').strip() for r in requests):
                raise ValueError('answer_sheet_inspect_requires_output_and_question_id_answer-sheet')
            paths,size=output_views(root,state,page,requests)
            return {'status':'ready_to_inspect','target':'output','image_size_px':size,
                    'image_paths':[shared._register(state,x) for x in paths],
                    'next_action':'Forward paths only to the same final reviewer.'}
        source=info(root,page)
        if target not in ('source','output'):raise ValueError('inspection_target_must_be_source_or_output')
        if requests is None and target=='source': paths=[source['source_image']]
        else:
            ref=state['pages'].get(str(page),{}).get('submitted')
            ids=None
            if ref or target=='output':
                value=job.load_json(job._artifact(root,ref)) if ref else reading(root,state,page)
                ids={q['id'] for q in value['questions']}
            if not requests or any((ids is not None and r['question_id'] not in ids)
                                   or not r.get('reason','').strip() for r in requests):
                raise ValueError('known_question_and_specific_uncertainty_reason_required')
            if target=='output':
                paths,size=output_views(root,state,page,requests);source['image_size_px']=size
            else:
                from restoration_prepare import source_views
                out=root/'mcp'/('views-'+uuid.uuid4().hex)
                result=source_views(root,page,[{k:v for k,v in r.items() if k not in ('question_id','reason')}|{'coordinate_space':'page'} for r in requests],out)
                paths=[v['image']['path'] for v in result['views']]
        return {'status':'ready_to_inspect',**source,'target':target,'image_paths':[shared._register(state,x) for x in paths],
                'next_action':'Forward image_paths to the requesting producer/reviewer, who opens them once. Master does not open them. Inspect only that uncertainty; no whole-exam crops.'}
    if action=='render_figures':
        value=reading(root,state,page);context=figure_context(value)
        expected={(q['id'],f) for q in value['questions'] for f in _figure_ids(q)};rows=[];input_sources={}
        for f in p['figures']:
            if (f['question_id'],f['id']) not in expected: raise ValueError('figure_not_in_current_reading')
            original,source=text_input(root,page,f,'latex','latex_path',f['id']+'.tex',with_source=True)
            text=diagram_document(original)
            if source is not None:input_sources[f['id']]=source
            path=shared._fresh(root,'.tex');path.write_text(text,encoding='utf-8')
            rows.append({'id':f['id'],'question_id':f['question_id'],'width_mm':f['width_mm'],'source':str(path)})
        # Rendering reuse and visual approval are separate: batch validates render
        # inputs/assets; this layer binds actual saved reviews to each question.
        prior=[path for path,ref in state['batches'].items() if ref['page']==page]
        out=root/'mcp'/('figures-'+uuid.uuid4().hex)
        result=batch.prepare_figures(root,page,rows,out,reuse_batch=prior[-1] if prior else None)
        state['batches'][result['batch']]={'page':page,'context':context,'input_sources':input_sources}
        reviews=[];tasks=[];reused=[]
        for item in result['items']:
            if item['status']=='failed':continue
            key=figure_review_key(root,page,value,item)
            cached=state.get('figure_review_cache',{}).get(key)
            if cached:
                proof=job._artifact(root,cached)
                job.figure_info(root,page,item['question_id'],item['render_receipt'],width_mm=item['width_mm'],review=proof)
                item['review']=str(proof)
                item['review_evidence']=cached;reused.append(item['id'])
                continue
            reviews.append({'id':item['id'],'checks':job.load_json(item['review'])['checks'],'status':'failed','issues':[]})
            tasks.append({'id':item['id'],'source_image':str(shared._source(root,page)),'render_image':shared._register(state,item['png']),'reused':item['reused']})
        saved=job.load_json(result['batch']);saved['items']=result['items'];job.save_json(result['batch'],saved)
        response={'status':result['status'],'batch_path':result['batch'],'errors':result['errors'],
                  'reviews':reviews,'review_tasks':tasks,'reused_review_ids':reused,
                  'next_action':('Send failed IDs and exact errors to the same producer. Fix only TeX/compile errors, then rerender the full current page figure list. For engine, missing-file or integrity errors, report the blocker; do not blindly edit TeX or retry.' if result['errors'] else
                                 'Send only pending review_tasks to the same producer. Compare with the original already visible in that context; reopen only if unavailable/unclear. Submit reviews with this current batch_path; every pending ID needs all four checks exactly passed/failed/not_verified. Explanations are not check values; put observed problems only in issues and keep issues=[] for passed. Unchanged approved figures need no reread.')}
        if not tasks and not result['errors']:
            response.update(perform('review_figures',{'page':page,'batch_path':result['batch'],'reviews':[]},root,state))
        return response
    if action=='review_figures':
        value=reading(root,state,page);path=Path(p['batch_path']).resolve(strict=True)
        batch_state=state['batches'].get(str(path),{})
        if batch_state.get('page')!=page or batch_state.get('context')!=figure_context(value):
            raise ValueError('current_figure_batch_required')
        if 'input_sources' not in batch_state:
            return {'status':'failed','message':'figure_source_binding_unavailable',
                    'visual_status':'not_verified',
                    'next_action':'This batch predates TeX source binding. Call hwp_render_figures with the complete current figure list for this page. Unchanged rendered figures are reused automatically; compare only the new pending renders.'}
        changed_ids=sorted(ident for ident,source in batch_state.get('input_sources',{}).items()
            if not Path(source['path']).is_file() or job.digest(source['path'])!=source['sha256'])
        if changed_ids:
            return {'status':'failed','message':'figure_source_changed_since_render',
                    'changed_ids':changed_ids,'visual_status':'not_verified',
                    'next_action':'The producer edited TeX after this render. Do not review or inspect this old batch. Call hwp_render_figures with the complete current figure list for this page; unchanged figures are reused automatically. Compare only the new pending renders, then submit their review.'}
        b=job.load_json(path)
        ids={i['id'] for i in b['items']};required={i['id'] for i in b['items'] if not i.get('review_evidence')}
        from figure_provenance import review_input_errors,review_input_failure,REVIEW_INPUT_HELP
        errors=review_input_errors(p.get('reviews'))
        if errors:return review_input_failure(errors,pending_ids=sorted(required))
        reviews={r['id']:r for r in p['reviews']}
        if len(reviews)!=len(p['reviews']) or not required<=set(reviews)<=ids:
            return {'status':'failed','message':'review_each_pending_figure_once','pending_ids':sorted(required),
                    'missing_ids':sorted(required-set(reviews)),'unexpected_ids':sorted(set(reviews)-ids),
                    'duplicate_ids':sorted({r['id'] for r in p['reviews'] if sum(v['id']==r['id'] for v in p['reviews'])>1}),
                    'next_action':'Submit every pending ID exactly once with its actual comparison. '+REVIEW_INPUT_HELP}
        for item in b['items']:
            key=figure_review_key(root,page,value,item)
            cache=state.setdefault('figure_review_cache',{})
            if item['id'] not in reviews:
                if cache.get(key)!=item['review_evidence']:raise ValueError('current_figure_review_evidence_required')
                proof=job._artifact(root,item['review_evidence'])
                item['review']=str(proof)
            else:
                reconsider_selected_figure(root,state,page,item)
                cache.pop(key,None)
                r=reviews[item['id']];data=job.load_json(item['review'])
                data.update({k:r[k] for k in ('status','checks','issues')})
                ref=job._snapshot(root,'mcp/evidence',json.dumps(data,ensure_ascii=False).encode(),'.json')
                item['review']=str(job._artifact(root,ref))
                if data['status']=='passed':
                    job.figure_info(root,page,item['question_id'],item['render_receipt'],width_mm=item['width_mm'],review=item['review'])
                    cache[key]=ref
        job.save_json(path,b)
        result=batch.finalize_figures(root,page,path.parent)
        if result['status']=='ready':
            selected={'path':result['figures'],'sha256':job.digest(result['figures']),'context':figure_context(value)}
            previous=state['figures'].get(str(page))
            if (previous and previous['context']==selected['context']
                    and job.digest(previous['path'])==previous['sha256']
                    and job.load_json(previous['path'])==job.load_json(selected['path'])):
                selected=previous
            if state['figures'].get(str(page))!=selected:
                state['pages'][str(page)].pop('composed',None)
                state['pages'][str(page)].pop('composed_inputs',None);invalidate_output(state)
            state['figures'][str(page)]=selected
        elif Path(state['figures'].get(str(page),{}).get('path','')).parent==path.parent:
            state['figures'].pop(str(page),None)
            state['pages'][str(page)].pop('composed',None)
            state['pages'][str(page)].pop('composed_inputs',None);invalidate_output(state)
        if result['status']=='ready':return accept_ready(root,state,page)
        return {**result,'next_action':'Use the reported failed figure IDs and issues. If the producer has already edited TeX, do not submit the old batch again: call hwp_render_figures with the complete current figure list for this page, then review only its returned pending tasks. Unchanged figures are reused by the engine. Do not inspect Python/server files.'}
    if action=='compose':
        value=reading(root,state,page)
        layout=shared.parse_layout_markdown(text_input(root,page,p,'layout_markdown','layout_path','layout.md'))
        if [q['id'] for q in layout['questions']]!=[q['id'] for q in value['questions']]:raise ValueError('preserve_current_question_order')
        columns={};questions=[]
        for geo,q in zip(layout['questions'],value['questions']):
            rid=geo['region_id']
            if columns.setdefault(rid,q['column'])!=q['column']: raise ValueError('do_not_mix_source_columns')
            questions.append({**geo,'content':deepcopy(q['content'])})
        figures=state['figures'].get(str(page));figurepath=None
        if figures:
            if figures['context']!=figure_context(value) or job.digest(figures['path'])!=figures['sha256']: raise ValueError('current_reviewed_figures_required')
            figurepath=figures['path']
        if p.get('figures_path') and (not figurepath or Path(p['figures_path']).resolve()!=Path(figurepath).resolve()): raise ValueError('use_returned_figures_path')
        from restoration_draft import compile_draft
        out=shared._fresh(root,'.json')
        result=compile_draft(root,page,{'schema':'restoration-draft/1','regions':layout['regions'],'questions':questions,'issues':[]},out,figures=figurepath)
        if result['status']!='compiled':
            kind=result.get('error_kind','input')
            exemplar=FileNotFoundError('required draft file missing') if kind=='missing_file' else OSError('draft environment failure') if kind=='environment' else ValueError('draft validation failed')
            return {**failure_result(action,exemplar),**result}
        expanded=job.load_json(out)
        for left in expanded['questions']:
            for right in expanded['questions']:
                if columns[left['region_id']]=='left' and columns[right['region_id']]=='right' and left['bbox_mm'][0]>=right['bbox_mm'][0]: raise ValueError('source_column_order_mismatch')
        s=state['pages'][str(page)];previous=deepcopy(s)
        s['composed']=job._snapshot(root,'mcp/evidence',out.read_bytes(),'.json')
        s['composed_inputs']=composition_inputs(state,page);save(root,state)
        try:job.accept(root,out,replace=any(a['page']==page for a in job._manifest(root)['accepted']))
        except Exception:
            state['pages'][str(page)]=previous
            raise
        if previous.get('composed',{}).get('sha256')!=s['composed']['sha256']:invalidate_output(state)
        return {'status':'accepted','page':page,'visual_status':'not_verified','next_action':'After all pages are accepted, build once. One separate reviewer checks the whole output.'}
    if action=='build':
        if state.get('native_run') and native_running(state['native_run']) is not False:
            # A receipt can appear just before process exit. Never overlap exports.
            if Path(state['native_run']['receipt']).exists() or state.get('output_stale'):
                return {'status':'building','message':'previous_native_export_still_active','next_check_after_seconds':60,
                        'next_action':'Wait for the existing export to exit before rebuilding.'}
            return collect_native(root,state)
        if state.get('native_run'):
            receipt=Path(state['native_run']['receipt'])
            previous=job.load_json(receipt) if receipt.exists() else {}
            preflight_only=previous.get('status')=='blocked' and previous.get('cleanup')=='no_session_started'
            if not preflight_only and previous.get('cleanup') not in ('closed_owned_session','terminated_owned_session','already_exited'):
                return {'status':'failed','message':'native_cleanup_unconfirmed','log':state['native_run'].get('log'),
                        'next_action':'Report the previous export failure. Do not retry or use direct Hangul/CLI; the owned session must be resolved by service maintenance first.'}
        p={**p,'output':str(unused_output(p['output']))}
        result=shared._perform('build',{**p,'native':False},root,state)
        state.pop('native_run',None);state.pop('output_stale',None)
        if not p.get('native',True):return result
        folder=Path(tempfile.gettempdir())/('hwp-single-'+uuid.uuid4().hex);log=shared._fresh(root,'.log')
        command=[sys.executable,'-B','-X','utf8',str(shared.SKILL/'scripts/restore.py'),'native',str(root),str(Path(p['output']).resolve().with_suffix('.build.json')),str(folder)]
        with log.open('wb') as stream:
            process=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=stream,stderr=subprocess.STDOUT,
                                     creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        state['native_run']={'receipt':str(folder/'restoration-native.json'),'log':str(log),'started':time.time(),'pid':process.pid,
                             'build_output':p['output']};save(root,state)
        _NATIVE_PROCESSES[process.pid]=process
        # Keep the MCP request alive until the bounded native supervisor exits.
        # Returning early allowed short-lived stdio clients to terminate its owner.
        process.wait()
        return {**collect_native(root,state),'build_output':p['output']}
    if action=='status':
        if state.get('native_run') and page is None and not state.get('output_stale'):return collect_native(root,state)
        m=job._manifest(root);rows=[]
        for n in ([page] if page is not None else question_pages(m)):
            source=info(root,n);s=state['pages'].get(str(n),{});a=next((a for a in m['assignments'] if a['page']==n),None)
            status='unassigned' if not a else 'needs_correction' if s.get('errors') else 'waiting_for_reading' if not s.get('reading') else 'accepted' if s.get('composed') and s.get('composed_inputs')==composition_inputs(state,n) else 'ready_for_production'
            rows.append({'page':n,'status':status,**source,'errors':s.get('errors',[])})
        return {**(rows[0] if page is not None else {'status':'in_progress','pages':rows}),
                'output_status':'needs_rebuild' if state.get('output_stale') else 'not_built' if not state.get('native_run') else 'available',
                'next_action':'Continue only missing page work; accepted pages need no recomposition. Build after all are accepted. No comparison or approval.'}
    if action=='read_asset':return shared._perform(action,p,root,state)
    if action=='finish_review':
        current=collect_native(root,state)
        if not current or current['status'] not in ('pending_review','complete'):raise ValueError('current_native_output_required')
        reviewer=p['reviewer_id'];owners={a['worker_id'] for a in job._manifest(root)['assignments']}
        if not reviewer.strip() or reviewer in owners:raise ValueError('one_independent_reviewer_required_not_a_producer')
        if state.get('reviewer_id') not in (None,reviewer):raise ValueError('use_the_same_single_reviewer_for_this_job')
        rows=p['reviews'];ids=[r['page'] for r in rows];allowed={t['page'] for t in state['review_tasks']}
        if not ids or len(set(ids))!=len(ids) or any(type(n) is not int or n not in allowed for n in ids):raise ValueError('unique_valid_review_pages_required')
        evidence=p['review_evidence'];required=[reviewer]
        for r in rows:
            if r['status'] not in ('passed','failed') or not isinstance(r['issues'],list) or (r['status']=='passed' and r['issues']):raise ValueError('invalid_review_verdict')
            task=next(t for t in state['review_tasks'] if t['page']==r['page']);required.extend([task.get('source_image') or task['answer_reference'],task['output_image']])
        missing=[x for x in required if x not in evidence]
        if missing:
            return {'status':'failed','message':'actual_reviewer_response_with_id_and_reviewed_paths_required',
                    'missing_evidence':missing,
                    'next_action':'Ask the same reviewer to include the listed paths/ID in its actual response, then resubmit the existing verdicts. Do not fabricate evidence, reread images or rebuild solely to repair response formatting.'}
        ref=job._snapshot(root,'mcp/evidence',evidence.encode(),'.log');state['reviewer_id']=reviewer
        for r in rows:
            n=str(r['page'])
            record={**r,'evidence':ref,'output_sha256':state['native']['sha256'],
                    'binding':state.get('review_bindings',{}).get(n),'reviewer_id':reviewer}
            task=next(t for t in state['review_tasks'] if t['page']==r['page'])
            if task.get('kind')=='answer_sheet':
                assembled=job.assemble(root);answer_page=assembled[-1]
                record.update(answer_review_questions=answer_page['answer_review_questions'],
                              answer_output_sha256=job.digest(task['output_image']),
                              answer_output_position=next(i for i,p in enumerate(assembled,1)
                                                          if p['page_number']==r['page']),
                              answer_output_count=len(assembled))
            state['reviews'][n]=record
            cache=state.setdefault('page_review_cache',{});cache.pop(n,None)
            if r['status']=='passed' and record['binding']:cache[n]=record
        return collect_native(root,state)
    raise ValueError('unsupported_single_workflow_action')

def failure_result(action,exc):
    if isinstance(exc,FileNotFoundError):
        kind='missing_file';next_action='The required file is missing. Restore the exact assigned file/path with its owner, then retry this operation. Do not rewrite mathematics or repeatedly rebuild.'
    elif isinstance(exc,(OSError,ImportError,subprocess.SubprocessError)):
        kind='environment';next_action='Report this environment/file-access failure and preserve the job. Do not change transcription, kill user processes, or retry the same operation repeatedly.'
    else:
        kind='input';next_action='Correct only the reported input with the same owner. Use original source line numbers and returned paths; do not reread images for a syntax-only correction.'
    message=str(exc)[:1600]
    errors=getattr(exc,'errors',None) or [{'code':kind,'message':message}]
    return {'status':'failed','action':action,'error_kind':kind,'message':message,'errors':errors,'next_action':next_action}


def _dispatch_one(action,params,root):
    try:
        if action!='prepare' and not (root/'manifest.json').exists():raise ValueError('prepared_job_required')
        if action!='prepare' and any(a.get('ab_session') for a in job._manifest(root)['assignments']):raise ValueError('legacy_ab_job_not_supported_by_this_mcp')
        path=root/'mcp/state.json'
        state=job.load_json(path) if path.exists() else {'workflow':'single-review/1','pages':{},'assets':{},'batches':{},'figures':{},'reviews':{},'delivered':[]}
        if state.get('workflow')!='single-review/1':raise ValueError('new_empty_job_required_for_single_review_workflow')
        try:
            try:
                result=perform(action,params,root,state)
                if action=='prepare':result['runtime_version']=RUNTIME_VERSION
            except (ValueError,KeyError,TypeError,OSError,ImportError,subprocess.SubprocessError) as exc:
                result=failure_result(action,exc)
            if action=='submit_reading' and result.get('status')=='failed':
                page_state=state['pages'].get(str(params.get('page')))
                if page_state is not None:
                    page_state['errors']=result.get('errors') or [{'code':result.get('error_kind','input'),'message':result.get('message','submission_failed')}]
                    invalidate_output(state)
            return result
        finally:
            if (root/'manifest.json').exists():save(root,state)
    except (ValueError,KeyError,TypeError,OSError,ImportError,subprocess.SubprocessError) as exc:
        return failure_result(action,exc)


def dispatch(action,params,*,_request_kind='single'):
    started=time.perf_counter();root=Path(params.get('job','')).resolve()
    with shared._MUTEX:
        if action=='help':
            # Help must not mutate state, metrics, assignments or review evidence.
            from restoration_author_help import author_help
            try:
                if 'equations' in params:raise ValueError('Equation preflight removed: write the complete reading.md and use hwp_submit_reading once.')
                return author_help(root,params.get('page'),params.get('topic','writing'))
            except (ValueError,KeyError,TypeError,OSError,ImportError) as exc:return failure_result(action,exc)
        request_kind=_request_kind
        if params.get('items') is not None:
            request_kind='batch'
            from restoration_requests import execute_batch
            try:result=execute_batch(action,params,dispatch)
            except (ValueError,KeyError,TypeError) as exc:result=failure_result(action,exc)
        else:
            result=_dispatch_one(action,params,root)
        from restoration_metrics import record_event,summary
        metrics=record_event(root,action,params,result,time.perf_counter()-started,request_kind=request_kind)
        if metrics['status']=='recorded':
            if action=='status' or result.get('status')=='complete':result['metrics']=summary(root)
        elif (root/'manifest.json').is_file():
            result['metrics_warning']=metrics
        return result
