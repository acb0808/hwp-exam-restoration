"""Bounded native render supervision. Cleanup requires immutable owned identity."""
from __future__ import annotations
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import site
import time
from layout_measure import digest, number

def write_json(path,value):
    path=Path(path); temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    # The supervisor reads stage.json while the worker replaces it. Windows
    # briefly denies replacement when that reader still holds the file open.
    for attempt in range(50):
        try:
            temporary.replace(path)
            break
        except PermissionError:
            if attempt == 49: raise
            time.sleep(.05)

def read_json(path):
    # Atomic replacement can briefly deny readers on Windows as well as writers.
    # Retry only sharing/access errors, with the same bound as write_json.
    for attempt in range(50):
        try:
            return json.loads(Path(path).read_text(encoding='utf-8-sig'))
        except PermissionError:
            if attempt == 49: raise
            time.sleep(.05)

def _desktop_activation_allowed(config):
    """Probe before spawning any COM worker; unknown token state fails closed."""
    helper=Path(config['adapter_runtime'])/'academy/documents/hwp_desktop.py'
    if not helper.is_file(): return None
    try:
        spec=importlib.util.spec_from_file_location('hwp_desktop_preflight',helper)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        return module.desktop_activation_allowed()
    except Exception:
        return None

def owned_identity_matches(receipt,current):
    return (type(receipt.get('pid')) is int and receipt['pid']>0
            and isinstance(receipt.get('exe'),str) and bool(receipt['exe'])
            and isinstance(receipt.get('create_time'),(int,float))
            and all(receipt[k]==current.get(k) for k in ('pid','exe','create_time')))

def stage_timed_out(stage,now,limit): return now-stage['started_at']>limit

def native_worker_environment(parent):
    """Restore the Windows font-root variable omitted by MCP's safe env list."""
    env=dict(parent)
    windows={key.upper():value for key,value in env.items()}
    if not windows.get('WINDIR'):
        root=windows.get('SYSTEMROOT')
        if not root or not Path(root).is_absolute() or not Path(root).is_dir():
            raise ValueError('windows_directory_environment_missing')
        env={key:value for key,value in env.items() if key.upper()!='WINDIR'}
        env['WINDIR']=root
    env['PYTHONIOENCODING']='utf-8'
    return env

def cleanup_owned(receipt):
    import psutil
    try:
        process=psutil.Process(receipt['pid'])
        actual={'pid':process.pid,'exe':process.exe(),'create_time':process.create_time()}
        if not owned_identity_matches(receipt,actual): return 'ownership_mismatch_no_cleanup'
        process.terminate()
        try: process.wait(timeout=3)
        except psutil.TimeoutExpired: return 'owned_process_termination_pending'
        return 'terminated_owned_session'
    except psutil.NoSuchProcess: return 'already_exited'
    except Exception as exc: return 'cleanup_unavailable: '+str(exc)

def render_native(source,run_dir,*,config=None):
    source=Path(source).resolve(strict=True); run=Path(run_dir).resolve()
    if source.suffix.lower() not in {'.hwp','.hwpx'}: raise ValueError('native_source_extension')
    if run.exists() and any(run.iterdir()): raise ValueError('native_run_directory_must_be_empty')
    resources=Path(__file__).resolve().parents[2]
    adapter=resources/'native/src'
    if not adapter.is_dir():adapter=resources/'using-math-edu/runtime/src'
    cfg={'adapter_runtime':str(adapter),
         'stage_timeout_seconds':60,'overall_timeout_seconds':240,'security_module':'FilePathCheckerModule',
         'runtime_site_packages':None}
    if set(config or {})-set(cfg): raise ValueError('unknown_native_config')
    cfg.update(config or {})
    if cfg['runtime_site_packages'] is not None:
        dependency_path=Path(cfg['runtime_site_packages']).resolve(strict=True)
        if not dependency_path.is_dir(): raise ValueError('runtime_site_packages_requires_directory')
        site.addsitedir(str(dependency_path))
    number(cfg['stage_timeout_seconds'],minimum=1,maximum=60)
    number(cfg['overall_timeout_seconds'],minimum=1,maximum=300)
    run.mkdir(parents=True,exist_ok=True)
    initial={'schema':'hwp-native-render/1','status':'blocked','source':str(source),'source_sha256':digest(source),
             'native_reopen':'not_performed','engine':'hancom-native','visual_status':'not_tested',
             'cleanup':'no_session_started'}
    missing=[]
    if os.name!='nt': missing.append('windows_required')
    if not (Path(cfg['adapter_runtime'])/'academy/documents/hwp_adapter.py').is_file(): missing.append('ownership_adapter_missing')
    for module in ('psutil','fitz','win32com','pythoncom'):
        try: available=importlib.util.find_spec(module) is not None
        except (ImportError,ValueError): available=False
        if not available: missing.append(module+'_missing')
    if missing:
        initial['error']=';'.join(missing);write_json(run/'render.json',initial);return initial
    desktop_ready=_desktop_activation_allowed(cfg)
    if desktop_ready is not True:
        initial['error']='restricted_desktop_token_requires_host_execution' if desktop_ready is False else 'desktop_preflight_unavailable'
        write_json(run/'render.json',initial);return initial
    # Extra read-only preflight; worker repeats the authoritative adapter preflight.
    import psutil
    try:
        existing=[p.pid for p in psutil.process_iter(['name']) if (p.info.get('name') or '').lower()=='hwp.exe']
    except (psutil.Error,OSError) as exc:
        initial['error']='process_preflight_unavailable: '+str(exc);write_json(run/'render.json',initial);return initial
    if existing:
        initial['error']='existing_hwp_session';initial['existing_pids']=existing
        write_json(run/'render.json',initial);return initial
    try: env=native_worker_environment(os.environ)
    except ValueError as exc:
        initial['error']=str(exc);write_json(run/'render.json',initial);return initial
    # This marker proves only an early preflight return. Never carry it into
    # worker launch, timeouts or failures where activation may have occurred.
    initial.pop('cleanup')
    write_json(run/'request.json',{'source':str(source),'source_sha256':initial['source_sha256'],'config':cfg})
    write_json(run/'stage.json',{'stage':'launch','started_at':time.time()})
    write_json(run/'render.json',{**initial,'status':'running'})
    started=time.monotonic(); timeout=None
    with (run/'worker.log').open('w',encoding='utf-8') as log:
        worker=subprocess.Popen([sys.executable,str(Path(__file__).with_name('native_layout_worker.py')),str(run)],
                                env=env,stdout=log,stderr=subprocess.STDOUT,
                                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        while worker.poll() is None:
            time.sleep(.1)
            try:
                stage=read_json(run/'stage.json')
                if time.monotonic()-started>cfg['overall_timeout_seconds']: timeout='overall_timeout'
                elif stage_timed_out(stage,time.time(),cfg['stage_timeout_seconds']): timeout='stage_timeout'
            except (OSError,ValueError,KeyError,TypeError) as exc:
                # A broken monitor must still join its worker and use the owned
                # identity cleanup path below, never abandon a live COM worker.
                timeout='native_monitor_read_failed: '+str(exc)
            if timeout:
                worker.terminate()
                try: worker.wait(timeout=3)
                except subprocess.TimeoutExpired: worker.kill();worker.wait(timeout=3)
                break
    receipt=run/'ownership.json'
    cleanup='no_ownership_receipt_no_cleanup'
    if timeout or worker.returncode:
        if receipt.exists(): cleanup=cleanup_owned(read_json(receipt))
        worker_report=read_json(run/'render.json')
        result={**initial,'status':'failed','error':timeout or worker_report.get('error','native_worker_failed'),'cleanup':cleanup,'log':str(run/'worker.log')}
        write_json(run/'render.json',result);return result
    result=read_json(run/'render.json')
    if (result.get('status')!='rendered' or result.get('source_sha256')!=digest(source)
            or result.get('pdf_sha256')!=digest(run/'render.pdf')
            or result.get('measured_artifact')!={'path':str(run/'render.hwpx'),'sha256':digest(run/'render.hwpx')}
            or any(result.get('artifacts',{}).get(fmt,{}).get('sha256')!=digest(run/('render.'+fmt)) for fmt in ('hwpx','hwp','pdf'))):
        result={**initial,'status':'failed','error':'native_receipt_mismatch'}
        write_json(run/'render.json',result)
    return result
