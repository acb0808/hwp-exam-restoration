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
# opencode ends an MCP request after 60 s and then drops the whole server, so every reply must come sooner.
# A build or render still running at this point is reported as such and the next call picks it up.
WAIT_SECONDS=30  # a build that ends at the limit still needs ~12 s to collect its review inputs (52 s seen at 40)
_RENDERS={}  # (job, page) -> one render running beside the job lock, until its result is handed out
_RENDERS_LOCK=__import__('threading').Lock()
def wait_seconds():
    try:return max(0.5,float(os.environ.get('HWP_MCP_WAIT_SECONDS',WAIT_SECONDS)))
    except ValueError:return WAIT_SECONDS
RUNTIME_VERSION='2.7.9'  # Loaded code version, never read from a replaced manifest.
TIKZ_HELPERS=shared.SKILL/'assets/tikz/exam-marks.tex'


FIT_BORDER_PT=2
# Coordinates shrink freely so labels keep body size (agents often draw 12 units for 12cm).
# Only when shrunk labels collide (five-panel choices) is the shrink stopped at FIT_MIN_SCALE;
# placement then shrinks text and geometry together for the rest.
FIT_FREE_SCALE=0.25
FIT_MIN_SCALE=0.8
# Invisible construction lines (computed intersections) must not widen the picture.
CONSTRUCTION_PATH=re.compile(r'\\path\s*\[(?![^\]]*\boverlay\b)([^\]]*\bname path\b[^\]]*)\]')

def fitted_body(body,width_mm,min_scale=FIT_FREE_SCALE):
    """Scale coordinates, never text, so the picture is width_mm wide and labels print at body size.
    Two measured passes: text does not scale, so one ratio overshoots; the second lands within a few percent."""
    target=width_mm*72.27/25.4-2*FIT_BORDER_PT
    probe=('\\sbox\\ExamFitBox{%\n'+body+'%\n}%\n'
           '\\ifdim\\wd\\ExamFitBox>1pt\\pgfmathsetmacro\\ExamFitScale{max('+str(min_scale)+',min(4,\\ExamFitScale*'
           +f'{target:.2f}'+'/\\wd\\ExamFitBox))}\\fi%\n')
    return ('\\newsavebox\\ExamFitBox\\def\\ExamFitScale{1}%\n'
            '\\tikzset{every picture/.append style={scale=\\ExamFitScale}}%\n'+probe+probe+body+'%\n')

def diagram_document(text,width_mm=None,min_scale=FIT_FREE_SCALE,shifts=None):
    """Accept one picture fragment or an existing full document; a full document is used as written.
    With width_mm the fragment is fitted to that width at constant label size (fitted_body), its nodes and
    named points are logged, and shifts (restoration_labels) move crossed labels to free spots."""
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
    text=CONSTRUCTION_PATH.sub(lambda m:'\\path[overlay,'+m.group(1)+']',expand_templates(text))
    clean=re.sub(r'(?<!\\)%[^\n]*','',text).strip()
    # Inline only when used: saved source hashes bind helper changes to the
    # existing render cache, without external input files or new model calls.
    helpers=(TIKZ_HELPERS.read_text(encoding='utf-8')+'\n'
             if re.search(r'\\Exam(?:RightAngle|LengthArc|Label|Ticks|Implies)\b',clean) else '')
    # Same rule for computed intersections, so figures without them keep their cached renders.
    libraries=('calc,arrows.meta,angles,quotes'+(',intersections' if re.search(r'name\s+(?:path|intersections)',clean) else '')
               +(',patterns' if re.search(r'\bpattern\s*=',clean) else ''))  # hatched shading in exam figures
    if width_mm is None:
        return ('\\documentclass[tikz,border=2pt]{standalone}\n'
                '\\usepackage{kotex}\n\\usepackage{amsmath}\n'
                '\\usetikzlibrary{'+libraries+'}\n'
                +helpers+'\\begin{document}\n'+text+'\n\\end{document}\n')
    from restoration_labels import preamble
    # Without the tikz class option the measuring boxes stay off the page: one page, the final picture only.
    return (f'\\documentclass[border={FIT_BORDER_PT}pt]{{standalone}}\n'
            '\\usepackage{kotex}\n\\usepackage{amsmath}\n\\usepackage{tikz}\n'
            '\\usetikzlibrary{'+libraries+'}\n'
            +helpers+preamble(text,shifts)+'\\begin{document}%\n'+fitted_body(text,width_mm,min_scale)+'\\end{document}\n')


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

QUESTION_WIDTH_SHARE=.425  # automatic layout: each question slot is this share of the source page width
BOX_PADDING_MM=6  # default <보기> box padding, left 3 + right 3

def automatic_layout(root,page,value):
    """Mechanical grid slots, not a transcription of scan coordinates."""
    source=job._manifest(root)['pages'][page-1]
    width,height=source['width_mm'],source['height_mm']
    rows=['units: mm','| type | id | region | x | y | width | height |','| --- | --- | --- | --- | --- | --- | --- |']
    for column,x in [('left',width*.05),('right',width*.525)]:
        qs=[q for q in value['questions'] if q['column']==column]
        if not qs:continue
        if len(qs)>6:raise ValueError('grid_column_requires_1_to_6_questions')
        w,h=width*QUESTION_WIDTH_SHARE,height*.9
        rows.append(f'| region | {column} | - | {x} | {height*.05} | {w} | {h} |')
        for i,q in enumerate(qs):
            rows.append(f'| question | {q["id"]} | {column} | {x} | {height*.05+i*h/len(qs)} | {w} | {h/len(qs)} |')
    return '\n'.join(rows)

def accept_ready(root,state,page):
    value=reading(root,state,page)
    ids=[f for q in value['questions'] for f in _figure_ids(q)]
    if ids and not state['figures'].get(str(page)):
        result={'status':'ready_for_figures','page':page,'figure_ids':ids,
                'next_action':'Same producer supplies only the required diagram TeX. Render and compare figures; layout is automatic.'}
        s=state['pages'].setdefault(str(page),{})
        if not s.get('figure_rules_sent'):
            # Delivered once, exactly when TeX is written: saves a separate file-read turn. The core rules keep this
            # reply under the ~4KB Antigravity spill limit (the full 8.7KB guide was always saved to a file and reread).
            result['figure_rules']=(shared.SKILL/'references/tikz-rules.md').read_text(encoding='utf-8');s['figure_rules_sent']=True
        return result
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
    if not a: raise ValueError('assign_one_producer_first: this job has no page slot; main calls hwp_assign with the actual spawn response (producers never call hwp_assign), then retry this call')
    return a
def agent(a):
    """Actual spawned ID. Slot records keep a stable worker_id so provisional work stays bound."""
    return a.get('agent_id') or (None if a.get('pending') else a['worker_id'])
def open_slots(root,pages):
    """Per-page slots let producers start before the host returns their ID; build still needs actual spawn evidence."""
    m=job._manifest(root);keep=[a for a in m['assignments'] if a['page'] in pages]
    have={a['page'] for a in keep}
    for n in pages:
        if n in have:continue
        keep.append({'assignment_id':uuid.uuid4().hex,'page':n,'worker_id':f'slot-page-{n:04d}','mode':'subagent',
                     'pending':True,'workflow':'single-review/1',
                     'evidence':job._snapshot(root,'evidence',f'slot for page {n} opened by hwp_prepare; actual spawn evidence is bound by hwp_assign'.encode(),'.log')})
    m['assignments']=sorted(keep,key=lambda a:a['page']);job.save_json(root/'manifest.json',m)
def unconfirmed(m):
    return [a['page'] for a in m['assignments'] if a.get('pending') and a['page'] in question_pages(m)]
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
    state['reviews']={};state.pop('review_notes',None)

# Review severity: only issues that change what a student reads or answers trigger a repair round.
# Diagram differences and cosmetic ones are recorded for the human checker on the 검수 노트 page.
REPAIR_TAGS=('누락','오독','선지','잘림','정답')
NOTE_TAGS=('도형','경미')
PASSING=('passed','passed_with_notes')
MAX_FAILED_VERDICTS=2  # the second failed verdict of a page is kept as notes instead of another repair round

def max_failed_verdicts():
    """HWP_MAX_REREVIEWS re-reviews per page (default 1); the failure after the last one becomes notes.
    0 records the first failure as notes with no repair round."""
    try:rereviews=int(os.environ.get('HWP_MAX_REREVIEWS',MAX_FAILED_VERDICTS-1))
    except ValueError:rereviews=MAX_FAILED_VERDICTS-1
    return 1+max(0,rereviews)
def issue_tag(text):
    m=re.match(r'\s*\[?\s*('+'|'.join(REPAIR_TAGS+NOTE_TAGS)+r')\s*\]?\s*[:：\-—]?',text)
    return m.group(1) if m else None
def verdict(issues):
    if any(issue_tag(i) not in NOTE_TAGS for i in issues):return 'failed'  # untagged issues stay repairs
    return 'passed_with_notes' if issues else 'passed'
def current_notes(state):
    """Reviewer notes plus measured figure slips left after the producer's one fix (measured_slips)."""
    measured=state.get('geometry_notes',{});stated=state.get('relation_notes',{});rows=[]
    for n,r in sorted(state['reviews'].items(),key=lambda x:int(x[0])):
        if r['status'] not in PASSING:continue
        issues=((r['issues'] if r['status']=='passed_with_notes' else [])+[i for found in measured.get(n,{}).values() for i in found]
                +[i for found in stated.get(n,{}).values() for i in found])
        if issues:rows.append({'page':int(n),'issues':issues})
    return rows

def measured_slips(state,page,items,lint):
    """Geometry near-misses measured on each rendered PDF (restoration_figure_geometry), no model call.
    The producer sees a figure's slips once, beside its first compare image, so the fix rides along with
    its own visual check. Slips still measured later are kept as 검수 노트 rows instead of another round."""
    try:from restoration_figure_geometry import geometry_warnings
    except ImportError:return
    rounds=state.setdefault('geometry_rounds',{});notes=state.setdefault('geometry_notes',{}).setdefault(str(page),{})
    for item in items:
        if item['status']=='failed':continue
        found=geometry_warnings(Path(item['png']).with_name('diagram.pdf'))
        if not found:notes.pop(item['id'],None);continue
        # One table row per figure: the first slip, and how many more.
        more=f" 외 {len(found)-1}곳" if len(found)>1 else ''
        notes[item['id']]=[f"도형: {item['question_id']} 자동 측정 — {found[0].split('. ')[0]}{more}"]
        key=f"{page}/{item['id']}"
        if not rounds.get(key):rounds[key]=1;lint.setdefault(item['id'],[]).extend(found)

def relation_slips(state,page,items,lint):
    """Position relations the question text states (restoration_relations, read when the page was submitted),
    measured on each rendered figure (restoration_relation_check). Delivered like measured_slips: once with
    the figure's first compare image, and a position (on a circle, tangent, crossing) still off later
    becomes a 검수 노트 row."""
    stated=state['pages'].get(str(page),{}).get('figure_relations') or {}
    if not any(stated.values()):return
    try:from restoration_relation_check import relation_findings,POSITION_KINDS
    except ImportError:return
    rounds=state.setdefault('relation_rounds',{});notes=state.setdefault('relation_notes',{}).setdefault(str(page),{})
    for item in items:
        if item['status']=='failed':continue
        found=relation_findings(Path(item['png']).with_name('diagram.pdf'),stated.get(item['id']))
        key=f"{page}/{item['id']}"
        if found and not rounds.get(key):rounds[key]=1;lint.setdefault(item['id'],[]).extend(f['text'] for f in found[:3])
        # Lengths and angles are often not to scale in the source itself, so only positions stay as notes.
        kept=[f['text'] for f in found if f['kind'] in POSITION_KINDS]
        if not kept:notes.pop(item['id'],None);continue
        more=f" 외 {len(kept)-1}곳" if len(kept)>1 else ''
        notes[item['id']]=[f"도형: {item['question_id']} {kept[0].split('. ')[0]}{more}"]

def ratio_slip(state,page,item,crop,lint):
    """Width:height of the figure's main shape, source crop against render (restoration_figure_ratio).
    Said once per figure and never kept as a note: the render may follow the lengths stated in the text
    where the source was not drawn to scale, and only the producer, looking at both, can tell."""
    key=f"{page}/{item['id']}";rounds=state.setdefault('ratio_rounds',{})
    if rounds.get(key):return
    rounds[key]=1
    try:from restoration_figure_ratio import ratio_warning
    except ImportError:return
    found=ratio_warning(crop,item['png'])
    if found:lint.setdefault(item['id'],[]).append(found)

def crossed_label_warnings(state,page,items,lint):
    """Labels still printed over each other after the engine moved what it could (restoration_labels).
    The producer hears of them once per figure, with its first compare image; later renders stay quiet.
    A label left on a line is not reported: in two of three such warnings of a real run the source had the
    label there too (an angle value in a narrow angle, a length on its dashed arc) and the redraw was worse."""
    try:from restoration_labels import overlapping_labels
    except ImportError:return
    rounds=state.setdefault('label_rounds',{})
    for item in items:
        key=f"{page}/{item['id']}"
        if item['status']=='failed' or rounds.get(key):continue
        found=overlapping_labels(Path(item['png']).with_name('diagram.pdf'))
        if not found:continue
        rounds[key]=1
        lint.setdefault(item['id'],[]).extend(
            f'라벨 "{a[:12]}"와 "{b[:12]}"가 서로 겹쳐 읽을 수 없습니다. 엔진이 4mm 안에서 빈자리를 찾지 못했습니다. 한쪽을 다른 쪽에 두세요.' for a,b in found[:2])

def printed_label(question):
    """Printed number of a question (e.g. 10번, 논술형1) from its first paragraph."""
    for block in question.get('content',[]):
        text=''.join(r.get('text','') for r in block.get('runs',[]) if r.get('kind')=='text')
        m=re.match(r'\s*((?:논술형|서술형|서답형|단답형)\s*\d+|\d+)\s*[.)]',text)
        if m:return m.group(1).replace(' ','')+('번' if m.group(1).isdigit() else '')
        if text.strip():break
    n=re.fullmatch(r'q(\d+)',question['id'])
    return f'{n.group(1)}번' if n else question['id']

def note_row(issue,page_label,labels):
    """Split one tagged issue into table cells: 구분, 문항 and the remaining observation."""
    unresolved=issue.startswith('미해결 ');body=issue[4:] if unresolved else issue
    tag=issue_tag(body) or '기타'
    text=re.sub(r'^\s*\[?\s*'+re.escape(tag)+r'\s*\]?\s*[:：\-—]?\s*','',body) if tag!='기타' else body.strip()
    found=re.search(r'(?<![A-Za-z0-9])[qQ](\d+)(?!\d)|((?:논술형|서술형|서답형)\s*\d+)',text)
    question='-'
    if found:
        question=labels.get('q'+found.group(1),f'{found.group(1)}번') if found.group(1) else found.group(2).replace(' ','')
        if found.start()==0:text=text[found.end():].lstrip(' :：,-—')  # the 문항 column already names it
    text=text if len(text)<=170 else text[:167]+'…'
    return {'page':page_label,'question':question,'kind':('미해결·' if unresolved else '')+tag,'text':text or '-'}

def append_review_notes(root,pages):
    """Mechanical 검수 노트 table page placed before the answer sheet; never reviewed, rebuilt only after all pages pass."""
    path=Path(root)/'mcp/state.json'
    notes=job.load_json(path).get('review_notes') if path.exists() else None
    if not notes:return pages
    from restoration_answers import note_row_height
    rows=[];budget=225-9-30;dropped=0  # table height budget less header row and title lines
    for item in notes:
        source=next((p for p in pages if p['page_number']==item['page']),{})
        answer=source.get('role')=='answer_sheet'
        labels={q['id']:printed_label(q) for q in source.get('questions',[])} if not answer else {}
        for issue in item['issues']:
            row=note_row(issue,'정답표' if answer else f"{item['page']}쪽",labels)
            if budget-note_row_height(row['text'])<0:dropped+=1;continue
            budget-=note_row_height(row['text']);rows.append(row)
    if dropped:rows.append({'page':'-','question':'-','kind':'생략','text':f'외 {dropped}건은 review 폴더의 검수 보고서를 확인하세요.'})
    template=pages[0];w,h=template['size_mm'];box=[10,15,w-20,h-30]
    page=deepcopy(template)
    page.update(page_number=max(p['page_number'] for p in pages)+1,role='review_notes',worker_id='mechanical-review-notes',
                assignment_id='mechanical-review-notes',blocks=[],issues=[],regions=[{'id':'notes','bbox_mm':box}],note_rows=rows,
                questions=[{'id':'review-notes','region_id':'notes','bbox_mm':box,'font_family':'함초롬바탕','font_pt':10,
                            'content':[{'id':'review-notes-title','kind':'paragraph','runs':[{'kind':'text','text':'검수 노트'}]}]}])
    for key in ('answer_rows','answer_review_questions','answer_reference'):page.pop(key,None)
    at=next((i for i,p in enumerate(pages) if p.get('role')=='answer_sheet'),len(pages))
    return pages[:at]+[page]+pages[at:]


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
    if text is not None and path is not None: raise ValueError('provide_exactly_one_'+inline+'_or_'+pathkey)
    if text is None and path is None:
        # The assigned file is the default source: producers often call with only job/page.
        default=root/'workers'/f'page-{page:04d}'/filename
        if not default.is_file():raise ValueError(f'write_{filename}_at: {default} (or pass {inline})')
        path=str(default)
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
                    'returncode':run.get('returncode'),'next_action':'The export was interrupted. Call hwp_build once more with the same output path: its leftover Hangul session is closed automatically and an unused output name is selected. If it fails again, report the saved export log. Do not inspect server internals.'}
        # Another job's export may be ahead of this one (restoration_native_queue); the clock starts at its own turn.
        from restoration_native_queue import queue_state
        queued=queue_state(path.parent)
        if isinstance(queued,dict) and queued.get('state')=='waiting':
            return {'status':'building','message':'native_export_waiting_for_another_job','next_check_after_seconds':5,'log':run.get('log'),
                    'next_action':'Another restoration job on this PC is exporting. Do not start another export: call hwp_status(job) again, it waits up to 30 s for this export.'}
        began=queued.get('at') if isinstance(queued,dict) and queued.get('state')=='exporting' else None
        elapsed=time.time()-(began if isinstance(began,(int,float)) else run.get('started',time.time()))
        return {'status':'building' if elapsed<330 else 'failed','message':'native_export_pending' if elapsed<330 else 'native_export_did_not_return_receipt',
                'next_check_after_seconds':5,'log':run.get('log'),
                'next_action':'The export is still running. Do not start another export or inspect server internals: call hwp_status(job) again, it waits up to 30 s for the export.' if elapsed<330 else 'Report the saved export log; do not kill user Hangul sessions or repeatedly rebuild.'}
    native_running(run)  # Reap completed owned processes even on successful output.
    native=job.load_json(path)
    if native.get('error') in ('native_content_exceeds_source_allocation','native_page_count_differs_from_source'):
        # The fixed grid keeps source pages; an actionable target avoids guess-and-rebuild loops.
        # A table pushed past the page adds a page without any cell reporting overflow, so rank by figure load too.
        issues=native.get('content_fit',{}).get('issues',[])
        over=sorted({i['block_id'] for i in issues if i.get('code')=='question_cell_content_overflow'})
        try:heavy=figure_load(root,state)[:3]
        except (OSError,ValueError,KeyError):heavy=[]  # guidance aid only
        return {'status':'failed','message':'content_exceeds_source_page','overflow_questions':over,'figure_heavy_questions':heavy,'log':run.get('log'),
                'next_action':'Send the listed questions to their producer (overflow_questions, else the top figure_heavy_questions): figures stack vertically in an equal share of the column, '
                              'so draw side-by-side source panels as ONE figure and shrink the tallest figures\' width_mm (about 20%, keeping every mark and label) in the % width_mm header, '
                              'rerender with hwp_render_figures(job,page), then hwp_build again. Never delete source content to fit.'}
    if native.get('error')=='native_export_queue_timeout':
        return {'status':'failed','message':'native_export_queue_timeout','log':run.get('log'),
                'next_action':'Other restoration jobs on this PC kept Hangul busy. Wait a few minutes, then call hwp_build again with the same output path.'}
    if native.get('status')!='rendered' or native.get('review_inputs',{}).get('status')!='pending_review':
        return {'status':'failed','message':native.get('error','native_review_inputs_missing'),'next_action':'Report the native failure and saved log. Never declare completion from HWPX alone.','log':run.get('log')}
    everything=job.assemble(root);assembled=[p for p in everything if p.get('role')!='review_notes']
    notes_page=len(assembled)!=len(everything)
    if native.get('job')!=str(root) or native.get('pages_sha256')!=batch.pages_digest(everything):
        if notes_page and native.get('job')==str(root) and native.get('pages_sha256')==batch.pages_digest(assembled):
            return {'status':'notes_build_required','review_notes':state.get('review_notes',[]),
                    'next_action':'All pages passed. Call hwp_build once with the same output path to add the 검수 노트 page '
                                  'before the answer sheet; that page needs no review. Do not send notes to producers.'}
        raise ValueError('changed_pages_require_new_output_and_review')
    selected=[p['page_number'] for p in assembled];count=len(selected)
    if native.get('page_count')!=len(everything) or [p['page'] for p in native['review_inputs']['pages']]!=selected:
        raise ValueError('native_review_must_cover_all_source_pages_once')
    for ref in native['artifacts'].values():
        if job.digest(ref['path'])!=ref['sha256']: raise ValueError('native_artifact_changed')
    bound=job.digest(path)
    if state.get('native',{}).get('sha256')!=bound:
        tasks=[];bindings={};content_bindings={};reused={};staged={'assets':dict(state['assets'])}
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
                sheet=review_figure_sheet(root,state,n)
                if sheet:task['figure_sheet']=shared._register(staged,sheet)
                spots=uncertain_sheet(root,state,n)
                if spots:task['uncertain_regions']={'image':shared._register(staged,spots),
                    'spots':[{k:s[k] for k in ('question_id','id','reason')} for s in state['uncertain'][str(n)]]}
            tasks.append(task)
            content_binding=fingerprint({'source':job.digest(source),'page':n,'position':position,'count':count,
                'content':batch.native_page(assembled[position-1])})
            bindings[str(n)]=fingerprint({'source':job.digest(source),
                'output':data['output_image']['sha256'],'page':n,'position':position,'count':count,
                'content':batch.native_page(assembled[position-1])})
            content_bindings[str(n)]=content_binding
            previous=state.get('page_review_cache',{}).get(str(n),{})
            semantic_answer_reuse=False
            if answer_page:
                current_questions=assembled[position-1]['answer_review_questions']
                old_questions=previous.get('answer_review_questions',{})
                prior_valid=(previous.get('status') in PASSING
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
            # The notes build only adds an unreviewed page, so reviewed content carries over even if pixels shift.
            same=(previous.get('binding')==bindings[str(n)] or semantic_answer_reuse
                  or (notes_page and previous.get('content_binding')==content_binding))
            if same and previous.get('reviewer_id')==state.get('reviewer_id') and previous.get('status') in PASSING:
                job._artifact(root,previous['evidence'])  # Verify saved actual review evidence.
                reused[str(n)]={**previous,'binding':bindings[str(n)],'content_binding':content_binding,'output_sha256':bound,
                    'reused_from_output_sha256':previous['output_sha256']}
        state.update(native={'path':str(path),'sha256':bound},reviews=reused,
                     review_tasks=tasks,review_bindings=bindings,review_content_bindings=content_bindings,assets=staged['assets'])
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
    complete=all(state['reviews'].get(str(n),{}).get('status') in PASSING for n in selected)
    notes=current_notes(state)
    if complete and notes and not notes_page:
        # Freeze the notes so the next build places them before the answer sheet.
        state['review_notes']=notes;save(root,state)
        return {'status':'notes_build_required','review_notes':notes,
                'next_action':'All pages passed. Call hwp_build once with the same output path to add the 검수 노트 page '
                              'before the answer sheet; that page needs no review. Do not send notes to producers.'}
    # Hashes and evidence stay in state; the caller only needs verdicts (long replies spill to files on some hosts).
    result={'status':'complete' if complete else 'pending_review','visual_status':'passed' if complete else 'not_verified',
            'build_output':run.get('build_output'),
            'artifacts':{k:v['path'] for k,v in native['artifacts'].items()},'reviewer_id':state.get('reviewer_id'),
            'review_tasks':[t for t in state['review_tasks'] if state['reviews'].get(str(t['page']),{}).get('status') not in PASSING],
            'reused_review_pages':[int(n) for n,r in state['reviews'].items() if r.get('reused_from_output_sha256')],
            'reviews':{'passed':sorted(int(n) for n,r in state['reviews'].items() if r['status'] in PASSING),
                       'failed':[{'page':int(n),'issues':r['issues']} for n,r in sorted(state['reviews'].items()) if r['status'] not in PASSING]},
            'next_action':('Deliver artifacts and list review_notes as items for the human check.' if notes else 'Deliver artifacts.') if complete else
                ('Same independent reviewer checks only review_tasks and saves its report at report_path. '
                 'First build: spawn one non-producer reviewer with the role file, job, review_tasks and report_path in its first prompt (it starts at once). '
                 'Record passed and failed pages with hwp_finish_review before repairs.')}
    if notes:result['review_notes']=notes
    if not complete:
        report=root/'review'/f"round-{state.get('review_round',0)+1:02d}.md";result['report_path']=str(report)
        if not report.exists():write_report_skeleton(root,report,result['review_tasks'])
    return result

STACK_WARNING_SHARE=.7  # completed runs peaked at .65; the observed extra-page overflow was .73

def page_figure_load(root,page,value,items):
    """Stacked figure height per question of one page against its automatic slot (source mm)."""
    from PIL import Image
    height_mm=job._manifest(root)['pages'][page-1]['height_mm'];rows=[]
    per_column={c:sum(q['column']==c for q in value['questions']) for c in ('left','right')}
    for q in value['questions']:
        total=0.0
        for item in items:
            if item.get('question_id')!=q['id'] or not Path(item.get('png') or '').is_file():continue
            with Image.open(item['png']) as im:total+=item['width_mm']*im.height/im.width
        if total:
            slot=height_mm*.9/max(1,per_column[q['column']])
            rows.append({'page':page,'question_id':q['id'],'label':printed_label(q),'figures_mm':round(total,1),
                         'slot_mm':round(slot,1),'share':round(total/slot,2)})
    return rows

def figure_load(root,state):
    """All pages' accepted figures, heaviest question first."""
    rows=[]
    for n in question_pages(job._manifest(root)):
        selected=state['figures'].get(str(n))
        if not selected:continue
        try:rows+=page_figure_load(root,n,reading(root,state,n),job.load_json(Path(selected['path']).parent/'batch.json')['items'])
        except (OSError,ValueError,KeyError):continue
    return sorted(rows,key=lambda r:-r['share'])

def write_report_skeleton(root,report,tasks):
    """Reviewer report with every reviewed path filled in: the reviewer only writes verdicts and records."""
    lines=[f'# 최종 검수 보고 {report.stem}','',f'job: {root}',
           '각 쪽의 판정·issues와 기록 줄만 채웁니다. 경로 줄은 지우거나 바꾸지 않습니다.','']
    for task in tasks:
        answer=task.get('kind')=='answer_sheet'
        lines+=[f"## page {task['page']}{' (정답표)' if answer else ''}",
                f"- {'answer_reference' if answer else 'source_image'}: {task.get('answer_reference') or task.get('source_image')}",
                f"- output_image: {task['output_image']}"]
        if task.get('figure_sheet'):lines.append(f"- figure_sheet: {task['figure_sheet']}")
        if task.get('uncertain_regions'):lines.append(f"- uncertain_regions: {task['uncertain_regions']['image']}")
        lines+=['- 판정: (passed / passed_with_notes / failed)','- issues:']
        if not answer:lines+=['- 선택지 행:','- 도형:']
        lines.append('')
    report.parent.mkdir(parents=True,exist_ok=True);report.write_text('\n'.join(lines),encoding='utf-8')

def figure_width_limits(root,page,value):
    """Widest figure per ID in template millimetres (the unit of width_mm).
    Composition converts width_mm into the source question slot with scale = slot / template column
    (restoration_draft) and the contract checks it there, less the source-mm box padding inside a <보기> box."""
    profile=job.load_json(shared.SKILL/'assets/templates/pdf2hwp-grid/profile.json')
    column=profile['paper_mm'][0]-sum(profile['side_margins_mm'])
    if len({q['column'] for q in value['questions']})==2:column=(column-profile['column_gap_mm'])/2
    slot=job._manifest(root)['pages'][page-1]['width_mm']*QUESTION_WIDTH_SHARE;scale=slot/column
    limits={}
    for q in value['questions']:
        for block in q['content']:
            if block.get('figure_ref'):limits[block['figure_ref']]=round(column-1,1)
            if block.get('kind')=='box':
                for child in block['content']:
                    if child.get('figure_ref'):limits[child['figure_ref']]=round((slot-BOX_PADDING_MM)/scale-1,1)
    return limits

FIGURE_HEADER_WIDTH=re.compile(r'width_mm\s*=\s*([0-9]+(?:\.[0-9]+)?)')
FIGURE_HEADER_BBOX=re.compile(r'source_bbox_px\s*=\s*\[?\s*'+r'\s*,\s*'.join([r'([0-9]+(?:\.[0-9]+)?)']*4))

def figure_header(text):
    """Render settings from the leading TeX comments: % width_mm=55 source_bbox_px=120,340,400,300"""
    found={}
    for line in text.splitlines()[:5]:
        if not line.lstrip().startswith('%'):continue
        if (m:=FIGURE_HEADER_WIDTH.search(line)):found['width_mm']=float(m.group(1))
        if (m:=FIGURE_HEADER_BBOX.search(line)):found['source_bbox_px']=[float(x) for x in m.groups()]
    return found

def figures_from_files(root,page,value,previous):
    """Figure list for a render call without `figures`: every figure of the reading, from its saved TeX file."""
    folder=root/'workers'/f'page-{page:04d}';last={f['id']:f for f in previous};rows=[];missing=[]
    page_px,page_mm=info(root,page)['source_size_px'][0],job._manifest(root)['pages'][page-1]['width_mm']
    for q in value['questions']:
        for fid in _figure_ids(q):
            saved=folder/(fid+'.tex')
            if not saved.is_file():missing.append(f'{saved} (first line: % width_mm=NN source_bbox_px=left,top,width,height)');continue
            head={**{k:v for k,v in last.get(fid,{}).items() if k in ('width_mm','source_bbox_px')},**figure_header(saved.read_text(encoding='utf-8'))}
            if 'width_mm' not in head and 'source_bbox_px' in head:
                head['width_mm']=round(head['source_bbox_px'][2]/page_px*page_mm,1)  # same share of the page as in the source
            if 'width_mm' not in head:missing.append(f'{saved}: add first line % width_mm=NN source_bbox_px=left,top,width,height');continue
            rows.append({'id':fid,'question_id':q['id'],**head})
    if missing:raise ValueError('write_figure_tex_files: '+'; '.join(missing))
    return rows

def compare_sheets(items,stem,limit_height=2400):
    """Pack new comparisons into as few native-size sheets as stay legible; one image needs no sheet."""
    if len(items)<2:return []
    from PIL import Image
    from restoration_montage import montage
    groups=[[]];height=0
    for label,path in items:
        with Image.open(path) as im:h=im.height+30
        if groups[-1] and height+h>limit_height:groups.append([]);height=0
        groups[-1].append((label,path));height+=h
    return [montage(g,Path(f'{stem}-{i}.png')) for i,g in enumerate(groups,1)]

def review_figure_sheet(root,state,page):
    """Source crop beside the final render for every figure the producer located, for the final reviewer."""
    selected=state['figures'].get(str(page));boxes=state.get('figure_sources',{}).get(str(page),{})
    if not selected or not boxes:return None
    try:
        from PIL import Image
        from restoration_montage import side_by_side
        folder=Path(selected['path']).parent;items=job.load_json(folder/'batch.json')['items'];pairs=[]
        with Image.open(shared._source(root,page)) as full:
            for item in items:
                box=boxes.get(item['id'])
                if box is None or not Path(item.get('png','')).is_file():continue
                l,t=max(0,math.floor(box[0])),max(0,math.floor(box[1]))
                r,b=min(full.width,math.ceil(box[0]+box[2])),min(full.height,math.ceil(box[1]+box[3]))
                if r-l<8 or b-t<8:continue
                crop=shared._fresh(root,'.png');full.crop((l,t,r,b)).save(crop)
                pairs.append((item['id'],side_by_side(crop,item['png'],shared._fresh(root,'.png'),label_left='source '+item['id'],label_right='output figure')))
        if not pairs:return None
        if len(pairs)==1:return pairs[0][1]
        from restoration_montage import montage
        return montage(pairs,shared._fresh(root,'.png'))
    except (OSError,ValueError,KeyError):return None  # optional aid; review still uses full source/output pages

def uncertain_sheet(root,state,page):
    """Enlarged source crops of every spot inspected for this page, labelled by question and spot ID."""
    spots=state.get('uncertain',{}).get(str(page),[])
    if not spots:return None
    try:
        from PIL import Image
        from restoration_montage import montage
        tiles=[]
        with Image.open(shared._source(root,page)) as full:
            for s in spots:
                x,y,w,h=s['bbox_px'];l,t=max(0,math.floor(x)),max(0,math.floor(y))
                r,b=min(full.width,math.ceil(x+w)),min(full.height,math.ceil(y+h))
                if r-l<4 or b-t<4:continue
                crop=full.crop((l,t,r,b))
                scale=max(1,min(3,600//max(1,crop.width)))  # thin overlines and primes need magnification
                if scale>1:crop=crop.resize((crop.width*scale,crop.height*scale),Image.LANCZOS)
                path=shared._fresh(root,'.png');crop.save(path);tiles.append((f"{s['question_id']} {s['id']}",path))
        return montage(tiles,shared._fresh(root,'.png')) if tiles else None
    except (OSError,ValueError,KeyError):return None  # optional aid

def write_task(root,page,include_answers):
    """Write the producer task once. It carries no worker ID, so it can exist before spawning."""
    folder=root/'workers'/f'page-{page:04d}';folder.mkdir(parents=True,exist_ok=True)
    task=folder/'task.md'
    if task.is_file():return task
    source=info(root,page)
    from restoration_author_help import task_contract,tool_examples
    head=(f'# {page}쪽 제작\n\njob: {root}\npage: {page}\n원본: {source["source_image"]}\n'
          f'원본 크기(px): {source["source_size_px"]}\n저장: {folder}\n\n')
    body=('원본 전체를 한 번 읽고 아래 형식으로 reading.md에 문제를 작성하세요. 도형 TeX는 제출이 ready_for_figures를 돌려준 뒤 작성하세요. '
          '불명확한 곳만 문항 ID·원본 픽셀 bbox·사유로 hwp_inspect 확대 요청하세요. 제출 전에도 가능합니다. '
          '도형 규칙은 그 제출 응답의 figure_rules로 옵니다. 규칙 파일을 따로 열지 않습니다. '
          '이 쪽의 배정 자리는 준비되어 있습니다. hwp_assign은 메인만 호출합니다.\n'
          '이 역할 지침을 따라 자기 쪽 제출·도형 대조까지 MCP로 직접 완료하세요: '
          f'{shared.SKILL / "agents/hwp-restoration-reader.md"}. '
          'accepted 전에는 완료 보고하지 마세요. 이미 본 원본은 재열지 않고, 수정은 부분 변경만 하며, 빌드·최종 검수는 메인이 맡습니다.\n\n')
    text=(head+task_contract(root,page,include_answers)+tool_examples(root,page)+body
          +(shared.SKILL/'references/markdown-format.md').read_text(encoding='utf-8')
          +('\n\n'+(shared.SKILL/'references/answer-sheet.md').read_text(encoding='utf-8') if include_answers else ''))
    task.write_text(text,encoding='utf-8')
    return task
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
        confirmed=[a for a in m['assignments'] if not a.get('pending')]
        if confirmed and selected!=question_pages(m):raise ValueError('question_pages_frozen_after_assignment')
        if confirmed:
            revision=2;restart=root.with_name(root.name+'_run2')
            while restart.exists():
                revision+=1;restart=root.with_name(f'{root.name}_run{revision}')
            # A run stopped after its last page was accepted has nothing left for producers: rebuild this job
            # instead of redoing every page in a new one. (A reviewer that already reported is bound to the job.)
            def accepted(n):
                s=state.get('pages',{}).get(str(n),{})
                return bool(s.get('composed') and s.get('composed_inputs')==composition_inputs(state,n))
            status={n:'accepted' if accepted(n) else 'in_progress' for n in selected}
            if all(v=='accepted' for v in status.values()) and not unconfirmed(m) and state.get('reviewer_id') is None:
                return {'status':'already_assigned','job':str(root),'spawn_requests':[],'page_status':status,'continue_with':'hwp_build',
                        'assigned_workers':[{'page':a['page'],'worker_id':agent(a)} for a in confirmed],
                        'next_action':'Every page of this job is already accepted, so nothing is redone. Do not spawn producers and do not start a new job: '
                                      'call hwp_build on this job with the desired output path (an unused name is selected automatically), then give the output to one new reviewer.'}
            return {'status':'already_assigned','job':str(root),'spawn_requests':[],'page_status':status,
                    'assigned_workers':[{'page':a['page'],'worker_id':agent(a)} for a in confirmed],
                    'restart_job':str(restart),
                    'next_action':'Do not spawn duplicate workers. Rewind does not undo saved assignments. For an explicit restart after stopping old workers, call hwp_prepare with restart_job and the same source/question_pages; then use the returned job path. For continuation, keep the existing job and its workers. Do not delete old files or search configuration/logs.'}
        m['question_pages']=selected
        m['include_answers']=bool(p.get('include_answers',m.get('include_answers',False)))
        job.save_json(root/'manifest.json',m)
        for n in selected:(root/'workers'/f'page-{n:04d}'/'task.md').unlink(missing_ok=True)  # options may have changed before any assignment
        tasks={n:write_task(root,n,m['include_answers']) for n in selected}
        open_slots(root,selected)
        return {'status':'prepared','job':str(root),'page_count':len(selected),
                'spawn_requests':[{'page':n,'output_page':i,'role':'producer','type':'self','task_path':str(tasks[n])} for i,n in enumerate(selected,1)],
                'next_action':'Spawn one fresh producer subagent per selected page with its task_path and the role file path in the first prompt; it starts at once and finishes its page. Bind each actual returned ID with its unmodified spawn/task response in one hwp_assign items call: right after spawning, or when a blocking host returns the finished producer. Producers never call hwp_assign. Build requires every page bound; layout is automatic.'}
    if action=='assign':
        m=job._manifest(root)
        if not m.get('question_pages') or page not in m['question_pages']:raise ValueError('select_question_pages_before_assignment')
        if not isinstance(p.get('evidence'),str) or not p['evidence'].strip():raise ValueError('actual_spawn_response_text_required')
        if not isinstance(p.get('worker_id'),str) or not p['worker_id'].strip():raise ValueError('worker_id_required')
        if state.get('reviewer_id')==p['worker_id']: raise ValueError('producer_cannot_be_final_reviewer')
        previous=next((a for a in m['assignments'] if a['page']==page),None)
        if previous and previous.get('pending'):
            # The spawn/task response names the ID it created; text without it is not that response.
            if p['worker_id'] not in p['evidence']:raise ValueError('spawn_evidence_must_contain_worker_id: pass the unmodified spawn or task response that shows this ID')
            if p['worker_id'] in {agent(a) for a in m['assignments']}:raise ValueError('one_page_per_independent_worker')
            previous.update(agent_id=p['worker_id'],evidence=job._snapshot(root,'evidence',p['evidence'].encode('utf-8'),'.log'))
            previous.pop('pending');job.save_json(root/'manifest.json',m)
            task=write_task(root,page,m.get('include_answers',False))
            s=state['pages'].get(str(page),{})
            done=bool(s.get('composed') and s.get('composed_inputs')==composition_inputs(state,page))
            return {'status':'assigned','worker_id':p['worker_id'],'page':page,'page_status':'accepted' if done else 'in_progress',
                    'worker_instructions':str(task),
                    'next_action':'Binding recorded. Build when every page is accepted and bound.' if done else 'Binding recorded. The producer already has its task; wait for its accepted report.'}
        if previous:
            if (agent(previous)!=p['worker_id']
                    or job._artifact(root,previous['evidence']).read_bytes()!=p['evidence'].encode('utf-8')):
                raise ValueError('page_already_assigned_to_different_worker_or_evidence')
            task=write_task(root,page,m.get('include_answers',False))
            return {'status':'assigned','unchanged':True,'worker_id':p['worker_id'],'page':page,
                    **info(root,page),'worker_instructions':str(task),'markdown_path':str(task.with_name('reading.md')),
                    'next_action':'Keep this existing worker and its task. Do not create a duplicate worker or repeat completed work.'}
        evidence=shared._fresh(root,'.log');evidence.write_bytes(p['evidence'].encode('utf-8'))
        a=job.assign(root,page,p['worker_id'],evidence)
        if a.get('workflow')!='single-review/1':
            m=job._manifest(root);next(x for x in m['assignments'] if x['page']==page)['workflow']='single-review/1';job.save_json(root/'manifest.json',m)
        folder=root/'workers'/f'page-{page:04d}'
        task=write_task(root,page,m.get('include_answers',False));source=info(root,page)
        return {'status':'assigned','worker_id':p['worker_id'],'page':page,**source,'worker_instructions':str(task),
                'markdown_path':str(folder/'reading.md'),'next_action':'Binding recorded. The producer already has this task (sent at spawn); send it only if the spawn prompt omitted it. Main waits for accepted; no relaying or content reads.'}
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
        try:
            # What each figure's question says about where its points lie; checked on the render (relation_slips).
            from restoration_relations import figure_relations
            s['figure_relations']=figure_relations(text)
        except Exception:s.pop('figure_relations',None)  # a checking aid, never a reason to reject a reading
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
                # Spots someone could not read at full-page size are where the final reviewer should look closely.
                spots=state.setdefault('uncertain',{}).setdefault(str(page),[])
                for r in requests:
                    spot={'question_id':r['question_id'],'id':r['id'],'bbox_px':r['bbox_px'],'reason':r['reason']}
                    if all((s['question_id'],s['bbox_px'])!=(spot['question_id'],spot['bbox_px']) for s in spots):spots.append(spot)
        crops=[shared._register(state,x) for x in paths]
        extra={}
        if requests and len(paths)>1:
            from restoration_montage import montage
            sheet=Path(paths[0]).parent/'sheet.png'
            shown=[shared._register(state,montage([(r['id'],x) for r,x in zip(requests,paths)],sheet))]
            extra={'crop_paths':crops,'sheet_of':[r['id'] for r in requests]}
        else:shown=crops
        return {'status':'ready_to_inspect',**source,'target':target,'image_paths':shown,**extra,
                'next_action':'Forward image_paths to the requesting producer/reviewer, who opens them once. Multiple crops arrive as one labelled sheet at native pixel size. Master does not open them. Inspect only that uncertainty; no whole-exam crops.'}
    if action=='render_figures':
        value=reading(root,state,page);context=figure_context(value)
        expected={(q['id'],f) for q in value['questions'] for f in _figure_ids(q)};rows=[];input_sources={}
        tex_paths={};bboxes={};lint={};refits={}
        figures=p.get('figures')
        last=[path for path,ref in state['batches'].items() if ref['page']==page]
        boxes=state.get('figure_sources',{}).get(str(page),{})
        previous=[{'id':i['id'],'question_id':i['question_id'],'width_mm':i['width_mm'],
                   **({'source_bbox_px':boxes[i['id']]} if i['id'] in boxes else {})}
                  for i in (job.load_json(last[-1])['items'] if last else [])]
        if figures is None:
            # The saved TeX files are the list: no JSON-escaped LaTeX and no list to forget.
            figures=figures_from_files(root,page,value,previous)
        # A partial list keeps the page's other figures from the last render instead of dropping them.
        sent={f['id'] for f in figures};added=[f['id'] for f in previous if f['id'] not in sent and (f['question_id'],f['id']) in expected]
        figures=list(figures)+[f for f in previous if f['id'] in added]
        limits=figure_width_limits(root,page,value);clamped={}
        for i,f in enumerate(figures):
            limit=limits.get(f['id'])
            if limit and f['width_mm']>limit:
                figures[i]={**f,'width_mm':limit};clamped[f['id']]={'requested':f['width_mm'],'used':limit}
        for f in figures:
            if (f['question_id'],f['id']) not in expected: raise ValueError('figure_not_in_current_reading')
            if f.get('source_bbox_px') is not None:
                bboxes[f['id']]=f['source_bbox_px'];state.setdefault('figure_sources',{}).setdefault(str(page),{})[f['id']]=f['source_bbox_px']
            saved=root/'workers'/f'page-{page:04d}'/(f['id']+'.tex')
            if f.get('latex') is None and f.get('latex_path') is None and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}',f['id']) and saved.is_file():
                f={**f,'latex_path':str(saved)}  # rerender after an in-place edit: the saved file is the only source
            if f.get('latex') is not None and f.get('latex_path') is None:
                # Inline TeX is stored at the assigned path so later fixes edit that file and resubmit latex_path.
                if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}',f['id']):raise ValueError('invalid_figure_id')
                target=root/'workers'/f'page-{page:04d}'/(f['id']+'.tex');target.parent.mkdir(parents=True,exist_ok=True)
                target.write_text(f['latex'],encoding='utf-8');f={**{k:v for k,v in f.items() if k!='latex'},'latex_path':str(target)}
            original,source=text_input(root,page,f,'latex','latex_path',f['id']+'.tex',with_source=True)
            if source is not None:tex_paths[f['id']]=source['path']
            try:
                from restoration_figure_lint import figure_warnings
                from restoration_tikz_templates import expand_templates
                found=figure_warnings(expand_templates(original))
                # The same TeX warnings are said once; a rerender of unchanged or still-warned TeX stays quiet.
                told=state.setdefault('lint_told',{});key=f"{page}/{f['id']}"
                if found and told.get(key)!=found:lint[f['id']]=found
                told[key]=found
            except Exception:pass  # advice only; template errors are reported by the render itself
            # A figure whose labels collided when fully fitted is rendered with the floor directly next time,
            # and one whose crossed labels were moved keeps its shift table: unchanged TeX renders once.
            mark=hashlib.sha256(original.encode('utf-8')).hexdigest()[:16]+f":{f['width_mm']}";memo=f"{page}/{f['id']}"
            floor=FIT_MIN_SCALE if state.get('fit_crowded',{}).get(memo)==mark else FIT_FREE_SCALE
            placed=state.get('label_shifts',{}).get(memo,{})
            shifts={int(n):tuple(v) for n,v in placed['shifts'].items()} if placed.get('mark')==f'{mark}:{floor}' else None
            text=diagram_document(original,f['width_mm'],floor,shifts)
            if shifts is None:refits[f['id']]=(original,f['width_mm'],memo,mark,floor)
            if source is not None:input_sources[f['id']]=source
            path=shared._fresh(root,'.tex');path.write_text(text,encoding='utf-8')
            rows.append({'id':f['id'],'question_id':f['question_id'],'width_mm':f['width_mm'],'source':str(path)})
        # Rendering reuse and visual approval are separate: batch validates render
        # inputs/assets; this layer binds actual saved reviews to each question.
        prior=[path for path,ref in state['batches'].items() if ref['page']==page]
        out=root/'mcp'/('figures-'+uuid.uuid4().hex)
        def refit(row,pdf):
            """Called until it returns None. Crowded figures: stop the coordinate shrink before labels collide.
            Then, once: move labels crossed by a line, a mark or another label (restoration_labels)."""
            found=refits.get(row['id'])
            if not found:return None
            original,width,memo,mark,floor=found
            try:
                from restoration_figure_geometry import labels_collide
                from restoration_labels import label_shifts
                if floor==FIT_FREE_SCALE and labels_collide(pdf):
                    state.setdefault('fit_crowded',{})[memo]=mark
                    refits[row['id']]=(original,width,memo,mark,FIT_MIN_SCALE)  # its labels are placed on the floored render
                    return diagram_document(original,width,FIT_MIN_SCALE)
                shifts=label_shifts(pdf)
            except Exception:return None  # measuring aid only
            refits.pop(row['id'],None)
            state.setdefault('label_shifts',{})[memo]={'mark':f'{mark}:{floor}','shifts':{str(n):list(v) for n,v in shifts.items()}}
            return diagram_document(original,width,floor,shifts) if shifts else None
        result=batch.prepare_figures(root,page,rows,out,reuse_batch=prior[-1] if prior else None,refit=refit)
        state['batches'][result['batch']]={'page':page,'context':context,'input_sources':input_sources}
        measured_slips(state,page,result['items'],lint)
        crossed_label_warnings(state,page,result['items'],lint)
        relation_slips(state,page,result['items'],lint)
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
            task={'id':item['id'],'source_image':str(shared._source(root,page)),'render_image':shared._register(state,item['png']),'reused':item['reused']}
            if lint.get(item['id']):task['warnings']=lint[item['id']]
            box=bboxes.get(item['id'])
            if box is not None:
                try:
                    from PIL import Image
                    from restoration_montage import side_by_side
                    with Image.open(task['source_image']) as full:
                        l,t=max(0,math.floor(box[0])),max(0,math.floor(box[1]))
                        r,b=min(full.width,math.ceil(box[0]+box[2])),min(full.height,math.ceil(box[1]+box[3]))
                        if r-l<8 or b-t<8:raise ValueError('source_bbox_px_too_small_or_outside_page')
                        crop=Path(item['png']).with_name(item['id']+'-source-crop.png');full.crop((l,t,r,b)).save(crop)
                    ratio_slip(state,page,item,crop,lint)
                    if lint.get(item['id']):task['warnings']=lint[item['id']]
                    task['compare_image']=shared._register(state,side_by_side(crop,item['png'],Path(item['png']).with_name(item['id']+'-compare.png'),label_left='source '+item['id'],label_right='render'))
                except (ValueError,OSError) as exc:task['compare_image_error']=str(exc)
            tasks.append(task)
        saved=job.load_json(result['batch']);saved['items']=result['items'];job.save_json(result['batch'],saved)
        sheets=compare_sheets([(t['id'],t['compare_image']) for t in tasks if t.get('compare_image')],out/'compare-sheet')
        # Warn before the build: stacked figures past this share of a question slot pushed a page over in a real run.
        crowded=[{**r,'fix':'draw side-by-side source panels as one figure or lower width_mm; figures stack vertically in the slot'}
                 for r in page_figure_load(root,page,value,[i for i in result['items'] if i['status']!='failed']) if r['share']>=STACK_WARNING_SHARE]
        response={'status':result['status'],'batch_path':result['batch'],'errors':result['errors'],
                  'reviews':reviews,'review_tasks':tasks,'reused_review_ids':reused,'tex_paths':tex_paths,
                  **({'compare_sheets':[shared._register(state,x) for x in sheets]} if sheets else {}),
                  **({'width_clamped':clamped} if clamped else {}),**({'kept_from_last_render':added} if added else {}),
                  **({'figure_warnings':lint} if lint else {}),
                  **({'layout_warnings':crowded} if crowded else {}),
                  'next_action':('Send failed IDs and exact errors to the same producer. Fix only TeX/compile errors, then rerender the full current page figure list. For engine, missing-file or integrity errors, report the blocker; do not blindly edit TeX or retry.' if result['errors'] else
                                 'Send only pending review_tasks to the same producer. If compare_sheets is present, open those sheets once (each row: source crop left, render right); else where a task has compare_image, open only that one image; otherwise compare with the original already visible in that context and reopen only if unavailable/unclear. To fix a figure, edit its tex_paths file and resubmit the full list with latex_path. Submit reviews with this current batch_path; every pending ID needs all four checks exactly passed/failed/not_verified. Explanations are not check values; put observed problems only in issues and keep issues=[] for passed. Unchanged approved figures need no reread.')}
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
        waiting=unconfirmed(job._manifest(root))
        if waiting:raise ValueError(f'bind_actual_producers_before_build: call hwp_assign with the actual spawn/task response for pages {waiting}')
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
            # An interrupted export leaves no receipt. Its own hidden Hangul is closed by its recorded identity;
            # only a session that cannot be shown to be gone still blocks the rebuild.
            from restoration_native_queue import owned_session_gone
            if (not preflight_only and previous.get('cleanup') not in ('closed_owned_session','terminated_owned_session','already_exited')
                    and not owned_session_gone(receipt.parent)):
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
        # The export may wait for other jobs' exports: dispatch waits for it outside the job lock, for a while.
        return {**collect_native(root,state),'build_output':p['output']}
    if action=='status':
        if state.get('native_run') and page is None and not state.get('output_stale'):return collect_native(root,state)
        m=job._manifest(root);rows=[]
        for n in ([page] if page is not None else question_pages(m)):
            source=info(root,n);s=state['pages'].get(str(n),{});a=next((a for a in m['assignments'] if a['page']==n),None)
            if a and a.get('pending'):source={**source,'binding':'pending_actual_spawn_evidence'}
            status='unassigned' if not a else 'needs_correction' if s.get('errors') else 'waiting_for_reading' if not s.get('reading') else 'accepted' if s.get('composed') and s.get('composed_inputs')==composition_inputs(state,n) else 'ready_for_production'
            rows.append({'page':n,'status':status,**source,'errors':s.get('errors',[])})
        return {**(rows[0] if page is not None else {'status':'in_progress','pages':rows}),
                'output_status':'needs_rebuild' if state.get('output_stale') else 'not_built' if not state.get('native_run') else 'available',
                'next_action':'Continue only missing page work; accepted pages need no recomposition. Build after all are accepted. No comparison or approval.'}
    if action=='read_asset':return shared._perform(action,p,root,state)
    if action=='finish_review':
        current=collect_native(root,state)
        if not current or current['status'] not in ('pending_review','complete'):raise ValueError('current_native_output_required')
        reviewer=p['reviewer_id'];owners={x for a in job._manifest(root)['assignments'] for x in (agent(a),a['worker_id']) if x}
        if not reviewer.strip() or reviewer in owners:raise ValueError('one_independent_reviewer_required_not_a_producer')
        if state.get('reviewer_id') not in (None,reviewer):raise ValueError('use_the_same_single_reviewer_for_this_job')
        rows=p['reviews'];ids=[r['page'] for r in rows];allowed={t['page'] for t in state['review_tasks']}
        if not ids or len(set(ids))!=len(ids) or any(type(n) is not int or n not in allowed for n in ids):raise ValueError('unique_valid_review_pages_required')
        spawn=p.get('spawn_evidence')
        if spawn is not None:
            if not isinstance(spawn,str) or reviewer not in spawn:raise ValueError('reviewer_spawn_evidence_must_contain_reviewer_id')
            if state.get('reviewer_spawn') is None:state['reviewer_spawn']=job._snapshot(root,'mcp/evidence',spawn.encode('utf-8'),'.log')
        evidence=p['review_evidence'];required=[] if state.get('reviewer_spawn') else [reviewer]
        for r in rows:
            if (r['status'] not in ('passed','passed_with_notes','failed') or not isinstance(r['issues'],list)
                    or any(not isinstance(i,str) or not i.strip() for i in r['issues'])
                    or (r['status']=='passed')!=(not r['issues'])):raise ValueError('invalid_review_verdict: passed has no issues; other verdicts list tagged issues')
            task=next(t for t in state['review_tasks'] if t['page']==r['page']);required.extend([task.get('source_image') or task['answer_reference'],task['output_image']]+([task['figure_sheet']] if task.get('figure_sheet') else []))
        missing=[x for x in required if x not in evidence]
        if missing:
            return {'status':'failed','message':'actual_reviewer_response_with_id_and_reviewed_paths_required',
                    'missing_evidence':missing,
                    'next_action':'Ask the same reviewer to include the listed paths/ID in its actual response, then resubmit the existing verdicts. Do not fabricate evidence, reread images or rebuild solely to repair response formatting.'}
        ref=job._snapshot(root,'mcp/evidence',evidence.encode(),'.log');state['reviewer_id']=reviewer
        state['review_round']=state.get('review_round',0)+1
        counts=state.setdefault('failed_verdicts',{})
        for r in rows:
            n=str(r['page']);status=verdict(r['issues']);issues=list(r['issues'])
            if status=='failed':
                counts[n]=counts.get(n,0)+1
                if counts[n]>=max_failed_verdicts():
                    # Bound repair rounds: what still fails goes to the human checker instead of another round.
                    status='passed_with_notes';issues=[i if issue_tag(i) in NOTE_TAGS else '미해결 '+i for i in issues]
            record={**r,'status':status,'issues':issues,'reviewer_status':r['status'],'evidence':ref,'output_sha256':state['native']['sha256'],
                    'binding':state.get('review_bindings',{}).get(n),
                    'content_binding':state.get('review_content_bindings',{}).get(n),'reviewer_id':reviewer}
            task=next(t for t in state['review_tasks'] if t['page']==r['page'])
            if task.get('kind')=='answer_sheet':
                assembled=[x for x in job.assemble(root) if x.get('role')!='review_notes'];answer_page=assembled[-1]
                record.update(answer_review_questions=answer_page['answer_review_questions'],
                              answer_output_sha256=job.digest(task['output_image']),
                              answer_output_position=next(i for i,p in enumerate(assembled,1)
                                                          if p['page_number']==r['page']),
                              answer_output_count=len(assembled))
            state['reviews'][n]=record
            cache=state.setdefault('page_review_cache',{});cache.pop(n,None)
            if record['status'] in PASSING and record['binding']:cache[n]=record
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


def merge_state(current,base,mine):
    """What one call changed (base -> mine), applied to the state as it is now: other calls' changes stay."""
    if not all(isinstance(v,dict) for v in (current,base,mine)):return deepcopy(mine)
    out=dict(current)
    for key in set(base)|set(mine):
        if key not in mine:out.pop(key,None)
        elif key not in base or base[key]!=mine[key]:
            out[key]=merge_state(current.get(key),base.get(key),mine[key]) if isinstance(base.get(key),dict) else deepcopy(mine[key])
    return out

def _render_once(params,root,request_kind):
    """A whole render on a copy of the job state, the compiles outside the job lock; then its changes are merged."""
    started=time.perf_counter();path=root/'mcp/state.json'
    with shared._MUTEX:
        try:
            if not (root/'manifest.json').exists():raise ValueError('prepared_job_required')
            if any(a.get('ab_session') for a in job._manifest(root)['assignments']):raise ValueError('legacy_ab_job_not_supported_by_this_mcp')
            base=job.load_json(path) if path.exists() else None
            if base is None or base.get('workflow')!='single-review/1':raise ValueError('new_empty_job_required_for_single_review_workflow')
        except (ValueError,KeyError,TypeError,OSError) as exc:return failure_result('render_figures',exc)
    mine=deepcopy(base)
    try:result=perform('render_figures',params,root,mine)
    except (ValueError,KeyError,TypeError,OSError,ImportError,subprocess.SubprocessError) as exc:result=failure_result('render_figures',exc)
    with shared._MUTEX:
        try:save(root,merge_state(job.load_json(path),base,mine))
        except (ValueError,OSError) as exc:return failure_result('render_figures',exc)
        from restoration_metrics import record_event
        record_event(root,'render_figures',params,result,time.perf_counter()-started,request_kind=request_kind)
    return result

def _render(params,root,request_kind):
    """Renders of different pages run side by side; a call waits wait_seconds() at most. A render still running
    then is reported, and the next render call of that page waits for it and hands its result out."""
    key=(str(root),str(params.get('page')))
    with _RENDERS_LOCK:
        run=_RENDERS.get(key)
        if run is None:
            run=_RENDERS[key]={'done':__import__('threading').Event(),'result':None,'began':time.time()}
            def work():
                try:run['result']=_render_once(params,root,request_kind)
                except Exception as exc:run['result']=failure_result('render_figures',exc)
                finally:run['done'].set()
            __import__('threading').Thread(target=work,daemon=True,name='render '+key[1]).start()
    if not run['done'].wait(wait_seconds()):
        return {'status':'rendering','page':params.get('page'),'next_action':'The render continues in the background (several pages are rendering at once). '
                'Call hwp_render_figures for this page again, unchanged: it returns this render\'s result when it is ready. Do not edit the TeX files meanwhile.'}
    with _RENDERS_LOCK:
        if _RENDERS.get(key) is run:_RENDERS.pop(key)
    result=dict(run['result'])
    folder=root/'workers'/f"page-{int(params['page']):04d}" if str(params.get('page','')).isdigit() else None
    if folder and any(p.stat().st_mtime>run['began'] for p in folder.glob('*.tex')):
        result['inputs_changed_during_render']=True
        result['next_action']='A TeX file of this page changed while this render ran, so this result is for the earlier files. Call hwp_render_figures again to render the current files. '+str(result.get('next_action',''))
    return result

def _await_export(root,result,started,request_kind):
    """Wait outside the job lock for a running export, until wait_seconds() after the call began; then report it."""
    while time.perf_counter()-started<wait_seconds():
        try:run=job.load_json(root/'mcp/state.json').get('native_run')
        except (ValueError,OSError):return result
        if not run or native_running(run) is not True:break
        time.sleep(0.5)
    else:return result
    final=_serial('status',{'job':str(root)},root,time.perf_counter(),request_kind)
    return {**final,'build_output':result['build_output']} if result.get('build_output') and isinstance(final,dict) else final

def dispatch(action,params,*,_request_kind='single'):
    job_param=str(params.get('job',''))
    if job_param and not Path(job_param).is_absolute():
        # A relative job resolves against the MCP server folder, i.e. inside the installed skill,
        # where the next skill update would delete it. Ask once for a real location instead.
        source=params.get('source')
        hint=str(Path(source).resolve().parent/Path(job_param).name) if source and Path(source).is_absolute() else '<folder next to the source PDF>'
        return failure_result(action,ValueError(f'job_path_must_be_absolute: use e.g. {hint}'))
    started=time.perf_counter();root=Path(params.get('job','')).resolve()
    if action=='render_figures' and params.get('items') is None and _request_kind=='single':
        return _render(params,root,_request_kind)
    result=_serial(action,params,root,started,_request_kind)
    if action in ('build','status') and isinstance(result,dict) and result.get('status')=='building' and params.get('items') is None:
        result=_await_export(root,result,started,_request_kind)
    return result

def _serial(action,params,root,started,_request_kind):
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
