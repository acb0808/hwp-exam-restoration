"""Page assignments and immutable evidence snapshots for source-backed restoration.

Hashes prove recorded bytes are unchanged. They do not authenticate a user's
approval or establish that a saved tool transcript came from a particular host.
"""
from __future__ import annotations
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import time
import uuid

MAX_ARTIFACT_BYTES = 200 * 1024 * 1024

def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()

def _read(path):
    path=Path(path)
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_ARTIFACT_BYTES:
        raise ValueError('missing_empty_or_oversized_artifact')
    data=path.read_bytes()
    if len(data)>MAX_ARTIFACT_BYTES:raise ValueError('oversized_artifact')
    return data

def _unique_pairs(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('duplicate_json_key')
        result[key]=value
    return result

def _json(data):
    return json.loads(data.decode('utf-8-sig'),object_pairs_hook=_unique_pairs,
                      parse_constant=lambda value:(_ for _ in ()).throw(ValueError('nonfinite_json_number')))

def load_json(path):return _json(_read(path))

def save_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    data=json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False).encode('utf-8')
    fd,name=tempfile.mkstemp(prefix=path.name+'.',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)

_HELD=set()  # job roots this process is inside right now
STALE_LOCK_SECONDS=120  # a lock is held for file writes only; an unreadable one this old was abandoned

def _pid_alive(pid):
    """False only when the process is known to be gone."""
    if os.name=='nt':
        import ctypes
        handle=ctypes.windll.kernel32.OpenProcess(0x1000,False,pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:return ctypes.windll.kernel32.GetLastError()!=87  # 87: no such process
        try:
            code=ctypes.c_ulong()
            return not ctypes.windll.kernel32.GetExitCodeProcess(handle,ctypes.byref(code)) or code.value==259
        finally:ctypes.windll.kernel32.CloseHandle(handle)
    try:os.kill(pid,0)
    except ProcessLookupError:return False
    except OSError:return True
    return True

def _abandoned(path,root):
    """A lock left by a stopped run: its owner is gone, or it is this process's own from a section it already left."""
    try:text=path.read_text(encoding='ascii',errors='replace').strip();age=time.time()-path.stat().st_mtime
    except OSError:return False
    if not text.isdigit():return age>STALE_LOCK_SECONDS
    pid=int(text)
    if pid==os.getpid():return str(root) not in _HELD
    if not _pid_alive(pid):return True
    # The pid may already belong to a new process (Windows reuses them at once): the owner cannot be younger than its lock.
    try:
        import psutil
        return psutil.Process(pid).create_time()>path.stat().st_mtime+2
    except ImportError:return False
    except Exception:return False

@contextmanager
def _locked(root):
    path=root/'.job.lock'
    for attempt in (0,1):
        try:fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);break
        except FileExistsError:
            # Stopping the CLI in the middle of a write used to leave this file, and the job then refused every call.
            if attempt==0:
                try:seen=path.read_bytes()
                except OSError:seen=None
                if seen is not None and _abandoned(path,root):
                    try:
                        if path.read_bytes()==seen:path.unlink()  # not a lock another process took meanwhile
                    except OSError:pass
                    continue
            raise ValueError('job_busy_or_interrupted_lock_requires_review') from None
    _HELD.add(str(root))
    try:
        os.write(fd,str(os.getpid()).encode());os.close(fd)
        yield
    finally:
        _HELD.discard(str(root))
        for _ in range(50):  # a sync client or scanner may hold the file for a moment (job folders often sit in OneDrive)
            try:path.unlink();break
            except FileNotFoundError:break
            except PermissionError:time.sleep(0.02)

def _artifact(root,record):
    if not isinstance(record,dict) or not isinstance(record.get('path'),str):raise ValueError('invalid_artifact_record')
    relative=Path(record['path'])
    if relative.is_absolute() or '..' in relative.parts:raise ValueError('artifact_path_escape')
    path=(root/relative).resolve(strict=True)
    if not path.is_relative_to(root):raise ValueError('artifact_path_escape')
    if digest(path)!=record.get('sha256'):raise ValueError('artifact_hash_mismatch: '+record['path'])
    return path

def _snapshot(root,folder,data,suffix):
    destination=root/folder/(uuid.uuid4().hex+suffix)
    destination.parent.mkdir(parents=True,exist_ok=True)
    with destination.open('xb') as stream:stream.write(data)
    return {'path':destination.relative_to(root).as_posix(),'sha256':hashlib.sha256(data).hexdigest()}

def _approval(data,page):
    value=_json(data)
    if not isinstance(value,dict) or any(not isinstance(value.get(k),str) or not value[k].strip() for k in ('user_message','reason')):
        raise ValueError('explicit_user_message_and_reason_required')
    pages=value.get('allowed_pages')
    if not isinstance(pages,list) or not pages or any(type(p) is not int or p<1 for p in pages) or page not in pages:
        raise ValueError('approval_does_not_cover_page')
    return value

def _manifest(root):
    value=load_json(root/'manifest.json')
    if not isinstance(value,dict) or value.get('schema')!='restoration-job/1':raise ValueError('invalid_job_schema')
    _artifact(root,value['source'])
    pages=value.get('pages')
    if not isinstance(pages,list) or not pages:raise ValueError('missing_source_pages')
    if [p.get('page') for p in pages]!=list(range(1,len(pages)+1)):raise ValueError('source_page_order_invalid')
    for page in pages:
        for key in ('width_mm','height_mm'):
            n=page.get(key)
            if type(n) not in (int,float) or not math.isfinite(n) or n<=0:raise ValueError('invalid_page_dimensions')
        _artifact(root,page['image'])
    assignments=value.get('assignments');accepted=value.get('accepted')
    if not isinstance(assignments,list) or not isinstance(accepted,list):raise ValueError('invalid_job_records')
    identifiers=set();assigned_pages=set();accepted_pages=set();subagent_workers=set()
    for assignment in assignments:
        identifier=assignment.get('assignment_id');page=assignment.get('page')
        if not isinstance(identifier,str) or not identifier or identifier in identifiers:raise ValueError('duplicate_or_invalid_assignment_id')
        if type(page) is not int or not 1<=page<=len(pages) or page in assigned_pages:raise ValueError('duplicate_or_invalid_page_assignment')
        if not isinstance(assignment.get('worker_id'),str) or not assignment['worker_id'].strip():raise ValueError('worker_id_required')
        if assignment.get('mode') not in ('subagent','main_exception'):raise ValueError('invalid_assignment_mode')
        if assignment['mode']=='subagent':
            if assignment['worker_id'] in subagent_workers:raise ValueError('one_page_per_independent_worker')
            subagent_workers.add(assignment['worker_id'])
            if assignment.get('agent_id') is not None:
                # Single-review slots keep worker_id stable; the actual spawned ID must still be unique.
                if not isinstance(assignment['agent_id'],str) or not assignment['agent_id'].strip() or assignment['agent_id'] in subagent_workers:
                    raise ValueError('one_page_per_independent_worker')
                subagent_workers.add(assignment['agent_id'])
            if assignment.get('ab_session'):
                reader_b=assignment.get('reader_b')
                if not isinstance(reader_b,str) or not reader_b.strip() or reader_b in subagent_workers:
                    raise ValueError('two_independent_workers_per_ab_page')
                subagent_workers.add(reader_b)
        evidence=_artifact(root,assignment['evidence'])
        if not _read(evidence).strip():raise ValueError('nonempty_tool_evidence_required')
        if assignment['mode']=='main_exception':_approval(_read(_artifact(root,assignment['approval'])),page)
        identifiers.add(identifier);assigned_pages.add(page)
    for accepted_page in accepted:
        page=accepted_page.get('page')
        if type(page) is not int or not 1<=page<=len(pages) or page in accepted_pages:raise ValueError('duplicate_or_invalid_accepted_page')
        if accepted_page.get('assignment_id') not in identifiers:raise ValueError('unassigned_result')
        _artifact(root,accepted_page['result']);accepted_pages.add(page)
    history=value.get('history',[])
    if not isinstance(history,list):raise ValueError('invalid_revision_history')
    for previous in history:
        if previous.get('assignment_id') not in identifiers:raise ValueError('unassigned_history_result')
        _artifact(root,previous['result'])
    return value

def prepare(source:Path,job_dir:Path,*,dpi=None,workflow=None)->dict:
    """Snapshot a PDF and render 200dpi pages, or a single image with explicit dpi."""
    source=Path(source).resolve(strict=True);root=Path(job_dir).resolve()
    if root.exists() and any(root.iterdir()):raise ValueError('job_directory_must_be_empty')
    is_pdf=source.suffix.lower()=='.pdf'
    if not is_pdf and source.suffix.lower() not in {'.png','.jpg','.jpeg','.tif','.tiff','.bmp'}:raise ValueError('unsupported_source_type')
    if not is_pdf and dpi is None:raise ValueError('image_requires_explicit_dpi')
    resolution=200 if dpi is None else dpi
    if type(resolution) not in (int,float) or not math.isfinite(resolution) or not 50<=resolution<=600:raise ValueError('dpi_out_of_range_50_to_600')
    data=_read(source)
    import fitz
    root.mkdir(parents=True,exist_ok=True)
    source_record=_snapshot(root,'source',data,source.suffix.lower())
    source_record['original_path']=str(source)
    pages=[];image_dir=root/'pages';image_dir.mkdir()
    if is_pdf:
        with fitz.open(stream=data,filetype='pdf') as document:
            if document.needs_pass or not 1<=len(document)<=500:raise ValueError('encrypted_or_page_limit_exceeded')
            for index,page in enumerate(document):
                if page.rect.width*page.rect.height*(resolution/72)**2>40_000_000:raise ValueError('page_pixel_limit')
                path=image_dir/f'page-{index+1:04d}.png'
                pix=page.get_pixmap(matrix=fitz.Matrix(resolution/72,resolution/72),alpha=False)
                pix.save(path);compact_page_image(path)
                pages.append({'page':index+1,'width_mm':page.rect.width*25.4/72,'height_mm':page.rect.height*25.4/72,
                              'image':{'path':path.relative_to(root).as_posix(),'sha256':digest(path)},'dpi':resolution})
    else:
        with fitz.open(stream=data,filetype=source.suffix[1:]) as image_document:
            if len(image_document)!=1:raise ValueError('single_image_required')
        pix=fitz.Pixmap(data)
        if pix.width*pix.height>40_000_000:raise ValueError('page_pixel_limit')
        path=image_dir/'page-0001.png';pix.save(path)
        pages.append({'page':1,'width_mm':pix.width*25.4/resolution,'height_mm':pix.height*25.4/resolution,
                      'image':{'path':path.relative_to(root).as_posix(),'sha256':digest(path)},'dpi':resolution})
    manifest={'schema':'restoration-job/1','source':source_record,'pages':pages,'assignments':[],'accepted':[],'history':[],
              'provenance_scope':'hashes_preserve_recorded_bytes; tool_and_user_message_authenticity_requires_external_review'}
    if workflow is not None:manifest['workflow']=workflow
    save_json(root/'manifest.json',manifest)
    if workflow!='single-review/1':
        from restoration_tools import prepare_inputs
        prepare_inputs(root,manifest)
    return manifest

def assign(job_dir:Path,page_number:int,worker_id:str,evidence:Path,mode='subagent',approval:Path|None=None)->dict:
    root=Path(job_dir).resolve(strict=True)
    if mode not in ('subagent','main_exception'):raise ValueError('invalid_assignment_mode')
    if not isinstance(worker_id,str) or not worker_id.strip():raise ValueError('worker_id_required')
    evidence_data=_read(evidence)
    if not evidence_data.strip():raise ValueError('nonempty_tool_evidence_required')
    approval_data=None
    if mode=='main_exception':
        if approval is None:raise ValueError('explicit_user_approval_required')
        approval_data=_read(approval);_approval(approval_data,page_number)
    elif approval is not None:raise ValueError('approval_only_for_main_exception')
    with _locked(root):
        manifest=_manifest(root)
        if type(page_number) is not int or not 1<=page_number<=len(manifest['pages']):raise ValueError('page_out_of_range')
        if any(a['page']==page_number for a in manifest['assignments']):raise ValueError('page_already_assigned')
        if mode=='subagent' and any(a['mode']=='subagent' and worker_id in (a['worker_id'],a.get('reader_b')) for a in manifest['assignments']):
            raise ValueError('one_page_per_independent_worker')
        record={'assignment_id':uuid.uuid4().hex,'page':page_number,'worker_id':worker_id,'mode':mode,
                'evidence':_snapshot(root,'evidence',evidence_data,'.log')}
        if approval_data is not None:record['approval']=_snapshot(root,'evidence',approval_data,'.json')
        if manifest.get('workflow')=='single-review/1':record['workflow']='single-review/1'
        else:
            from restoration_tools import worker_input
            record['worker_input']=worker_input(root,manifest,record)
        manifest['assignments'].append(record);save_json(root/'manifest.json',manifest)
        return record

def _validate_result(value,manifest,assignment):
    from restoration_contract import validate_page
    validate_page(value)
    page=manifest['pages'][assignment['page']-1]
    expected={'assignment_id':assignment['assignment_id'],'worker_id':assignment['worker_id'],
              'source_sha256':manifest['source']['sha256'],'page_number':page['page'],
              'size_mm':[page['width_mm'],page['height_mm']]}
    if any(value.get(k)!=v for k,v in expected.items()):raise ValueError('result_assignment_or_source_mismatch')
    if value.get('issues')!=[]:raise ValueError('unresolved_page_issues')
    from question_contract import figures
    for block in figures(value):
        path=Path(block['path'])
        if not path.is_absolute():raise ValueError('image_asset_requires_absolute_path')
        if hashlib.sha256(_read(path)).hexdigest()!=block['sha256']:raise ValueError('image_asset_hash_mismatch')
    from figure_provenance import validate_figures
    validate_figures(value)
    from restoration_tools import content_errors
    errors=content_errors(value)
    if errors:raise ValueError(json.dumps(errors,ensure_ascii=False))


def assign_many(job_dir,rows,evidence):
    """Register actual workers from one tool response under one job lock."""
    from restoration_tools import worker_input
    root=Path(job_dir).resolve(strict=True);data=_read(evidence)
    if not data.strip():raise ValueError('nonempty_tool_evidence_required')
    if not isinstance(rows,list) or not rows:raise ValueError('nonempty_assignment_array_required')
    with _locked(root):
        manifest=_manifest(root);pages={a['page'] for a in manifest['assignments']}
        workers={w for a in manifest['assignments'] if a['mode']=='subagent' for w in (a['worker_id'],a.get('reader_b'))}
        for row in rows:
            if not isinstance(row,dict) or set(row)!={'page','worker_id'}:raise ValueError('assignment_requires_page_and_worker_id')
            page=row['page'];worker=row['worker_id']
            if type(page) is not int or not 1<=page<=len(manifest['pages']):raise ValueError('page_out_of_range')
            if page in pages:raise ValueError('page_already_assigned')
            if not isinstance(worker,str) or not worker.strip():raise ValueError('worker_id_required')
            if worker in workers:raise ValueError('one_page_per_independent_worker')
            pages.add(page);workers.add(worker)
        evidence_ref=_snapshot(root,'evidence',data,'.log');records=[]
        for row in rows:
            record={**row,'assignment_id':uuid.uuid4().hex,'mode':'subagent','evidence':evidence_ref}
            record['worker_input']=worker_input(root,manifest,record);records.append(record)
        manifest['assignments'].extend(records);save_json(root/'manifest.json',manifest)
    return {'status':'assigned','assignments':records}


def validate_result(job_dir,result):
    """Read-only diagnostics using the same production acceptance checks."""
    from restoration_tools import content_errors
    root=Path(job_dir).resolve(strict=True)
    value=load_json(result);manifest=_manifest(root)
    if not isinstance(value,dict):
        return {'status':'failed','errors':[{'code':'contract','location':'page','message':'page_result_object_required'}],'visual_status':'not_verified','accepted':False}
    if value.get('schema')=='restoration-worker-input/1':
        return {'status':'failed','errors':[{'code':'worker_input_is_not_result','location':'page',
            'message':'배정 안내 파일입니다. result_path의 JSON에 전사한 뒤 그 파일을 검증하세요.',
            'result_path':value.get('result_path'),'validation_command':value.get('validation_command')}],
            'visual_status':'not_verified','accepted':False}
    errors=content_errors(value)
    matches=[a for a in manifest['assignments'] if a['assignment_id']==value.get('assignment_id')]
    if len(matches)!=1:errors.append({'code':'assignment','location':'page','message':'missing_or_ambiguous_assignment'})
    else:
        a=matches[0];p=manifest['pages'][a['page']-1]
        expected={'assignment_id':a['assignment_id'],'worker_id':a['worker_id'],'source_sha256':manifest['source']['sha256'],
                  'page_number':p['page'],'size_mm':[p['width_mm'],p['height_mm']]}
        for k,v in expected.items():
            if value.get(k)!=v:errors.append({'code':'assignment','location':k,'message':'result_assignment_or_source_mismatch'})
    return {'status':'failed' if errors else 'validated','page':value.get('page_number'),'errors':errors,
            'visual_status':'not_verified','accepted':False}


def figure_info(job_dir,page,qid,render_path,*,width_mm,review=None):
    from restoration_tools import figure_info as make_info
    root=Path(job_dir).resolve(strict=True);manifest=_manifest(root)
    matches=[a for a in manifest['assignments'] if a['page']==page]
    if len(matches)!=1:raise ValueError('missing_page_assignment')
    return make_info(root,manifest,matches[0],qid,render_path,width_mm=width_mm,review=review)

def accept(job_dir:Path,result:Path,*,replace=False)->dict:
    root=Path(job_dir).resolve(strict=True);data=_read(result);value=_json(data)
    if not isinstance(value,dict):raise ValueError('page_result_object_required')
    with _locked(root):
        manifest=_manifest(root)
        matches=[a for a in manifest['assignments'] if a['assignment_id']==value.get('assignment_id')]
        if len(matches)!=1:raise ValueError('missing_or_ambiguous_assignment')
        assignment=matches[0];_validate_result(value,manifest,assignment)
        from restoration_ab import validate_accepted_result
        validate_accepted_result(root,value,assignment)
        from restoration_single import validate_accepted_result as validate_single
        validate_single(root,value,assignment)
        previous=[a for a in manifest['accepted'] if a['page']==assignment['page']]
        if previous and not replace:raise ValueError('page_already_accepted')
        if replace and not previous:raise ValueError('revision_requires_previously_accepted_page')
        if previous and previous[0]['assignment_id']!=assignment['assignment_id']:raise ValueError('revision_assignment_mismatch')
        receipt={'page':assignment['page'],'assignment_id':assignment['assignment_id'],
                 'result':_snapshot(root,'results',data,'.json')}
        if previous:
            manifest.setdefault('history',[]).append(previous[0])
            manifest['accepted'].remove(previous[0])
        manifest['accepted'].append(receipt);save_json(root/'manifest.json',manifest)
        return receipt

def revise(job_dir:Path,result:Path)->dict:
    """Accept a worker's explicit same-assignment revision; preserve prior bytes."""
    return accept(job_dir,result,replace=True)

def compact_page_image(path):
    """Optional 16-level grayscale page image (HWP_PAGE_IMAGE_MODE=gray16): about a third of the RGB bytes,
    same pixels, thin print strokes kept. Opt-in until measured on each host."""
    import os
    if os.environ.get('HWP_PAGE_IMAGE_MODE')!='gray16':return
    from PIL import Image
    with Image.open(path) as im:gray=im.convert('L')
    gray.quantize(16,dither=Image.Dither.NONE).save(path,optimize=True,bits=4)

def assemble(job_dir:Path)->list:
    root=Path(job_dir).resolve(strict=True)
    with _locked(root):
        manifest=_manifest(root)
        expected=manifest.get('question_pages',[p['page'] for p in manifest['pages']])
        if sorted(r['page'] for r in manifest['accepted'])!=expected:raise ValueError('missing_page_results')
        result=[]
        for receipt in sorted(manifest['accepted'],key=lambda r:r['page']):
            matches=[a for a in manifest['assignments'] if a['assignment_id']==receipt['assignment_id'] and a['page']==receipt['page']]
            if len(matches)!=1:raise ValueError('accepted_assignment_mismatch')
            value=load_json(_artifact(root,receipt['result']));_validate_result(value,manifest,matches[0])
            from restoration_ab import validate_accepted_result
            validate_accepted_result(root,value,matches[0])
            from restoration_single import validate_accepted_result as validate_single
            validate_single(root,value,matches[0]);result.append(value)
        from restoration_answers import append_answer_page
        from restoration_single import append_review_notes,layout_adjustments
        return append_review_notes(root,append_answer_page(root,manifest,layout_adjustments(root,result)))
