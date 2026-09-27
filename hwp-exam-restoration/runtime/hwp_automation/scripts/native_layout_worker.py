"""Private process worker; only adapter-proven owned Hancom sessions are used."""
from pathlib import Path
import sys
import time
import traceback
import site
import tempfile
from native_layout import read_json,write_json,owned_identity_matches,cleanup_owned
from layout_measure import digest

def main(run):
    request=read_json(run/'request.json');cfg=request['config'];source=Path(request['source'])
    if cfg.get('runtime_site_packages'): site.addsitedir(cfg['runtime_site_packages'])
    def stage(name): write_json(run/'stage.json',{'stage':name,'started_at':time.time()})
    sys.path.insert(0,cfg['adapter_runtime'])
    from academy.documents.hwp_adapter import _create_new_hwp,_close_owned_hwp,_hwp_window_process_id
    from academy.documents.locks import AppSessionLock
    import psutil
    import fitz
    hwp=None;identity=None;lease=None;run_lease=None
    result={'schema':'hwp-native-render/1','status':'failed','source':str(source),
            'source_sha256':request['source_sha256'],'engine':'hancom-native','native_reopen':'not_performed','visual_status':'not_tested'}
    def save(path,fmt):
        stage('save_'+fmt)
        if hwp.SaveAs(str(path),fmt,'') is False or not path.is_file() or path.stat().st_size==0:
            raise RuntimeError('native_save_failed_'+fmt)
    def reopen(path,fmt):
        stage('reopen_'+fmt)
        if hwp.Clear(1) is False: raise RuntimeError('native_clear_failed')
        if not hwp.Open(str(path),fmt,''): raise RuntimeError('native_open_failed_'+fmt)
    try:
        stage('acquire_session_lock')
        # A shared path serializes independent render directories in this user session.
        lease=AppSessionLock(Path(tempfile.gettempdir())/'hwp-automation-native-session.lock',run_id=run.name,document_key=str(source))
        lease.acquire()
        run_lease=AppSessionLock(run/'session.lock',run_id=run.name,document_key=str(source))
        run_lease.acquire()
        stage('create_owned_session')
        # The default adapter performs read-only process preflight before activation
        # and verifies HWND/PID creation identity before returning an owned object.
        hwp,owned=_create_new_hwp()
        if owned is not True: raise RuntimeError('ownership_not_verified')
        process=psutil.Process(_hwp_window_process_id(hwp))
        identity={'pid':process.pid,'exe':process.exe(),'create_time':process.create_time()}
        write_json(run/'ownership.json',identity)
        stage('security_module')
        if not hwp.RegisterModule('FilePathCheckDLL',cfg['security_module']): raise RuntimeError('security_module_unavailable')
        if digest(source)!=request['source_sha256']: raise RuntimeError('source_changed_after_request')
        reopen(source,source.suffix[1:].upper())
        pages=int(hwp.PageCount)
        if not 1<=pages<=100: raise RuntimeError('native_page_limit_1_to_100')
        for fmt in ('HWPX','HWP'):
            output=run/('render.'+fmt.lower());save(output,fmt);reopen(output,fmt)
            if int(hwp.PageCount)!=pages: raise RuntimeError('reopen_page_count_changed_'+fmt)
        # PDF evidence belongs to this exact delivered HWPX, not the HWP roundtrip.
        reopen(run/'render.hwpx','HWPX')
        if int(hwp.PageCount)!=pages: raise RuntimeError('final_hwpx_reopen_page_count_changed')
        save(run/'render.pdf','PDF')
        stage('validate_pdf')
        previews=[]
        with fitz.open(run/'render.pdf') as pdf:
            if len(pdf)!=pages: raise RuntimeError('pdf_page_count_changed')
            folder=run/'pages';folder.mkdir()
            for index,page in enumerate(pdf):
                stage('preview_page_'+str(index+1))
                if page.rect.width*page.rect.height*(100/72)**2>16_000_000:
                    raise RuntimeError('preview_pixel_limit')
                path=folder/f'page-{index+1}.png';page.get_pixmap(dpi=100).save(path)
                previews.append({'path':str(path),'sha256':digest(path),'page':index+1,'dpi':100})
        try: engine_version=str(hwp.Version)
        except Exception: engine_version='unknown'
        result.update(status='rendered',native_reopen='passed',page_count=pages,pdf=str(run/'render.pdf'),
                      pdf_sha256=digest(run/'render.pdf'),ownership=identity,
                      measured_artifact={'path':str(run/'render.hwpx'),'sha256':digest(run/'render.hwpx')},pages=previews,
                      environment={'python':sys.version,'executable':identity['exe'],'engine_version':engine_version},
                      artifacts={fmt:{'path':str(run/('render.'+fmt)),'sha256':digest(run/('render.'+fmt))} for fmt in ('hwpx','hwp','pdf')})
    except Exception as exc:
        result['error']=str(exc);traceback.print_exc()
    finally:
        if hwp is not None and identity:
            stage('close_owned_session')
            try:
                process=psutil.Process(identity['pid'])
                current={'pid':process.pid,'exe':process.exe(),'create_time':process.create_time()}
                if owned_identity_matches(identity,current):
                    _close_owned_hwp(hwp)
                    try:
                        process.wait(timeout=2);result['cleanup']='closed_owned_session'
                    except psutil.TimeoutExpired: result['cleanup']=cleanup_owned(identity)
                else: result['cleanup']='ownership_mismatch_no_cleanup'
            except psutil.NoSuchProcess: result['cleanup']='already_exited'
            except Exception as exc: result['cleanup']='cleanup_unavailable: '+str(exc)
            if result.get('cleanup') not in {'closed_owned_session','terminated_owned_session','already_exited'}:
                result.update(status='failed',error='owned_session_cleanup_not_verified')
        if run_lease is not None and run_lease.acquired: run_lease.release()
        if lease is not None and lease.acquired: lease.release()
        write_json(run/'render.json',result);stage('complete')
    return 0 if result['status']=='rendered' else 1

if __name__=='__main__': sys.exit(main(Path(sys.argv[1]).resolve()))
