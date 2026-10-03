"""Compile a self-contained TikZ document; no raster or alternate-renderer fallback."""
from pathlib import Path
import argparse,hashlib,importlib.util,json,os,re,shutil,subprocess,sys

def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def record(path):return {'path':str(Path(path).resolve()),'sha256':digest(path)}

def engine_path(explicit=None):
    requested=explicit or os.environ.get('HWP_TIKZ_ENGINE')
    if requested:
        path=Path(requested)
        if not path.is_file():raise ValueError('tikz_engine_unavailable: '+str(path))
        return path.resolve()
    for name in ('xelatex','lualatex','pdflatex'):
        found=shutil.which(name)
        if found:return Path(found).resolve()
    raise ValueError('tikz_engine_unavailable: install/configure XeLaTeX, LuaLaTeX or pdfLaTeX; crop fallback is forbidden')

def doctor(engine=None):
    try:found=str(engine_path(engine));error=None
    except ValueError as exc:found=None;error=str(exc)
    pymupdf=importlib.util.find_spec('fitz') is not None
    return {'status':'ready' if found and pymupdf else 'blocked','engine':found,'pymupdf':pymupdf,
            'error':error or (None if pymupdf else 'pymupdf_unavailable_use_installed_studio_python'),
            'compile_probe':'not_performed','note':'Engine presence alone does not verify TikZ packages or Korean fonts.'}

def validate_source(text):
    # These checks prevent the common crop-in-TikZ workaround, not arbitrary TeX execution.
    text=re.sub(r'(?<!\\)%[^\n]*','',text)
    if r'\begin{tikzpicture}' not in text or r'\end{tikzpicture}' not in text:
        raise ValueError('tikzpicture_source_required')
    if re.search(r'\\(?:includegraphics|includepdf|pgfimage|pdfximage)\b',text):
        raise ValueError('embedded_image_not_tikz_reconstruction')

def compile_tex(command,root):
    try:
        process=subprocess.run(command,cwd=root,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,timeout=60,check=False,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        return process.returncode,process.stdout
    except subprocess.TimeoutExpired as exc:
        return -1,(exc.stdout or b'')+b'\nTikZ compiler timed out after 60 seconds.'

def render(source,output,engine=None,dpi=300):
    source=Path(source).resolve(strict=True);data=source.read_bytes()
    if len(data)>1024*1024:raise ValueError('tikz_source_too_large')
    validate_source(data.decode('utf-8-sig'))
    executable=engine_path(engine)
    if type(dpi) is not int or not 150<=dpi<=600:raise ValueError('dpi_150_to_600_required')
    root=Path(output).resolve()
    if root.exists() and any(root.iterdir()):raise ValueError('output_directory_must_be_empty')
    root.mkdir(parents=True,exist_ok=True);tex=root/'diagram.tex';tex.write_bytes(data)
    command=[str(executable),'-no-shell-escape','-interaction=nonstopmode','-halt-on-error','diagram.tex']
    code,stdout=compile_tex(command,root);log=root/'compile.log';log.write_bytes(stdout)
    pdf=root/'diagram.pdf'
    if code!=0 or not pdf.is_file():
        # Quote the first TeX error so the producer need not open the log for the usual cases.
        text=stdout.decode('utf-8','replace');first=next((l.strip() for l in text.splitlines() if l.startswith('!')),'')
        where=next((l.strip() for l in text.splitlines() if re.match(r'l\.\d+',l)),'')
        hint=(' The named paths do not cross where they are drawn: extend the construction line past the curve, '
              'e.g. \\path[name path=fold] ($(A)!-0.5!(B)$) -- ($(A)!1.5!(B)$);' if re.search(r"No shape named .?intersection-\d",first) else '')
        raise ValueError('tikz_compile_failed: '+(first+' '+where).strip()+hint+' log: '+str(log))
    if b'Missing character:' in stdout:
        # Name the glyph and the fix: a retry cannot succeed with the same symbol.
        found=re.findall(r'Missing character: There is no (\S+) in font ([^!\n]*)',stdout.decode('utf-8','replace'))
        stray=sorted({g for g,font in found if font.strip()=='nullfont'});glyphs=sorted({g for g,font in found if font.strip()!='nullfont'})
        parts=[]
        # nullfont means text typed on a path, outside any node: usually a doubled ';' or a bare label.
        if stray:parts.append('stray text '+' '.join(stray)+' outside a node (remove extra ; or put the text in \\node{...})')
        if glyphs:parts.append(' '.join(glyphs)+' not in the TeX fonts; draw it instead (hollow arrow: \\ExamImplies{x,y}; '
                               'others: TikZ lines or math such as $\\Rightarrow$)')
        raise ValueError('tikz_missing_character: '+'; '.join(parts)+'. log: '+str(log))
    import fitz
    with fitz.open(pdf) as doc:
        if len(doc)!=1:raise ValueError('tikz_single_page_required')
        page=doc[0]
        if page.get_images(full=True):raise ValueError('embedded_image_not_tikz_reconstruction')
        if page.rect.width*page.rect.height*(dpi/72)**2>40_000_000:raise ValueError('tikz_pixel_budget_exceeded')
        png=root/'diagram.png';page.get_pixmap(matrix=fitz.Matrix(dpi/72,dpi/72),alpha=False).save(png)
    result={'schema':'tikz-render/1','status':'rendered_pending_review','renderer':'tikz',
            'source':record(tex),'pdf':record(pdf),'png':record(png),'log':record(log),
            'engine':str(executable),'command':command,'exit_code':code,'dpi':dpi,
            'visual_status':'not_verified'}
    (root/'render.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result

def main():
    parser=argparse.ArgumentParser(description=__doc__);commands=parser.add_subparsers(dest='command',required=True)
    p=commands.add_parser('doctor');p.add_argument('--engine',type=Path)
    p=commands.add_parser('render');p.add_argument('source',type=Path);p.add_argument('output',type=Path)
    p.add_argument('--engine',type=Path);p.add_argument('--dpi',type=int,default=300)
    args=parser.parse_args()
    try:
        result=doctor(args.engine) if args.command=='doctor' else render(args.source,args.output,args.engine,args.dpi)
    except (OSError,ValueError,ImportError) as exc:result={'status':'blocked' if 'unavailable' in str(exc) else 'failed','error':str(exc)}
    print(json.dumps(result,ensure_ascii=False,indent=2));return 2 if result.get('status') in ('blocked','failed') else 0

if __name__=='__main__':raise SystemExit(main())
