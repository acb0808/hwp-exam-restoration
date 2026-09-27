"""Read-only check of bundled resources and external application prerequisites."""
from pathlib import Path
import hashlib,importlib.util,json,os,runpy,sys
from runtime_paths import SKILL_ROOT,RUNTIME,automation_root,native_root,equation_compiler

def available(name):
    try:return importlib.util.find_spec(name) is not None
    except (ImportError,ValueError):return False

def inspect():
    errors=[]
    try:
        from exam_template import load_template
        load_template(SKILL_ROOT/'assets/templates/pdf2hwp-grid')
        automation_root();native_root();equation_compiler()
        manifest=json.loads((RUNTIME/'sources.json').read_text(encoding='utf-8'))
        for item in manifest['files']:
            path=(RUNTIME/item['path']).resolve()
            if not path.is_relative_to(RUNTIME) or hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']:
                raise ValueError('bundled_runtime_hash_mismatch: '+item['path'])
    except (OSError,ValueError,ImportError,KeyError) as exc:errors.append(str(exc))
    deps={name:available(name) for name in ('jsonschema','fitz','PIL','psutil','win32com','pythoncom')}
    ready=not errors and all(deps[n] for n in ('jsonschema','fitz','PIL'))
    try:tikz=runpy.run_path(str(RUNTIME/'tikz_render.py'))['doctor']()
    except (OSError,ValueError,ImportError) as exc:tikz={'status':'blocked','error':str(exc)}
    registered=False
    if os.name=='nt':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT,r'HWPFrame.HwpObject\CLSID'):registered=True
        except OSError:pass
    return {'schema':'restoration-doctor/1','status':'ready' if ready else 'blocked','skill_root':str(SKILL_ROOT),
            'bundle_errors':errors,'python_dependencies':deps,'hwpx_ready':ready,'tikz':tikz,
            'native_prerequisites_ready':ready and registered and all(deps[n] for n in ('psutil','win32com','pythoncom')),
            'hancom_com_registered':registered,'native_validation':'not_run','font_and_security_module_validation':'not_run',
            'external_project_required':False,'other_skills_required':False}

if __name__=='__main__':
    result=inspect();print(json.dumps(result,ensure_ascii=False,indent=2));raise SystemExit(0 if result['status']=='ready' else 2)
