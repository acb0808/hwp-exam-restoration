"""Concrete handoffs and paths; never transcribes or accepts source content."""
from pathlib import Path
import json,sys,uuid

def command(*args):
    return [sys.executable,'-B','-X','utf8',str(Path(__file__).with_name('restore.py')),*map(str,args)]

def powershell(argv):
    # Literal single quotes protect brackets, dollar signs and apostrophes in paths.
    return '& '+' '.join("'"+str(a).replace("'","''")+"'" for a in argv)

def preparation_handoff(root,manifest):
    root=Path(root).resolve()
    return {'spawn_requests':[{'page':p['page'],'message':f"시험지 {p['page']}쪽 담당자입니다. 배정 파일이 올 때까지 조회·전사 없이 대기하세요. 추가 에이전트를 만들지 마세요."} for p in manifest['pages']],
        'assignment_command':powershell(command('assign-many',root,'--workers',*[f'ACTUAL_PAGE_{p["page"]}_WORKER_ID' for p in manifest['pages']], '--evidence',root/'spawn-response.txt')),
        'evidence_path':str(root/'spawn-response.txt'),
        'note':'Save the actual spawn tool response verbatim at evidence_path. Replace worker placeholders with its actual IDs in page order. Send returned handoffs[].message to each worker; no mapping script or internal source inspection is needed.',
        'template_fields':{'school':'source school name','year':'source year','exam_title':'source exam title'},
        'environment_file':str(root/'environment.json')}

def ordered_workers(root,workers):
    from restoration_job import _manifest
    manifest=_manifest(Path(root).resolve(strict=True))
    unassigned=[p['page'] for p in manifest['pages'] if p['page'] not in {a['page'] for a in manifest['assignments']}]
    if len(workers)!=len(unassigned):raise ValueError('worker_count_must_match_unassigned_pages')
    return [{'page':p,'worker_id':w} for p,w in zip(unassigned,workers)]

def automatic_output(root,page,prefix,suffix=''):
    from restoration_job import _manifest
    root=Path(root).resolve(strict=True);manifest=_manifest(root)
    if type(page) is not int or not 1<=page<=len(manifest['pages']):raise ValueError('page_out_of_range')
    if not any(a['page']==page for a in manifest['assignments']):raise ValueError('missing_page_assignment')
    # Actual writers still use their existing exclusive creation checks.
    return root/'workers'/f'page-{page:04d}'/(prefix+'-'+uuid.uuid4().hex+suffix)

def input_json(path):
    from restoration_job import load_json
    path=Path(path)
    if not path.is_file():raise ValueError('input_file_missing: '+str(path.resolve())+'; save the requested JSON before running this command')
    return load_json(path)

def write_worker_brief(packet):
    from restoration_job import save_json
    from PIL import Image
    root=Path(packet['job']);folder=Path(packet['work_dir']);page=packet['page_metadata']['page_number']
    with Image.open(packet['page_image']) as image:w,h=image.size
    size=packet['page_metadata']['size_mm']
    cmds={'compile':command('compile-draft',root,page,packet['draft_path']),
          'source_views':command('source-views',root,page,folder/'views-spec.json'),
          'figures':command('figures',root,page,folder/'figures-spec.json')}
    guide=Path(__file__).resolve().parents[1]/'references/worker-guide.md'
    views=[]
    for crop in packet['navigation_crops']:
        left,top,right,bottom=crop['pixel_box']
        views.append({**crop,'size_px':[right-left,bottom-top],
            'coordinate_space':crop['path'],
            'pixel_to_page_mm':{'scale':[size[0]/w,size[1]/h],
                'offset':[left*size[0]/w,top*size[1]/h]}})
    brief={'schema':'restoration-worker-task/1','page':page,'worker_id':packet['page_metadata']['worker_id'],
        'job':str(root),'work_dir':str(folder),'draft_path':packet['draft_path'],'page_image':packet['page_image'],
        'navigation_crops':[c['path'] for c in packet['navigation_crops']],
        'navigation_views':views,'page_size_mm':size,
        'scale':{'page_size_px':[w,h],'mm_per_pixel':[size[0]/w,size[1]/h]},
        'default_font':{'family':packet['font_family'],'pt':packet['font_pt']},
        'commands':{k:{'argv':v,'powershell':powershell(v)} for k,v in cmds.items()},
        'environment':packet['environment'],'guide':guide.read_text(encoding='utf-8'),
        'figure_guide':str(guide.with_name('tikz-exam.md'))}
    entry=folder/'task.md';brief['instructions_path']=str(entry.resolve())
    entry.write_text(worker_markdown(brief),encoding='utf-8')
    # Machine-readable compatibility metadata is not a second authoring entry.
    path=root/'inputs/worker-bindings'/f'page-{page:04d}-task.json'
    path.parent.mkdir(parents=True,exist_ok=True);save_json(path,brief)
    return str(path.resolve())

def worker_markdown(brief):
    """Readable, complete instructions with concrete paths, not escaped prose."""
    lines=[f"# {brief['page']}쪽 작업",'',
        f"담당자: `{brief['worker_id']}`",f"초안 저장: `{brief['draft_path']}`",'',
        f"원본 전체: `{brief['page_image']}`",
        f"원본 크기: {brief['scale']['page_size_px']} px / {brief['page_size_mm']} mm",
        '좌표는 [x, y, 너비, 높이]이며 기준 이미지의 왼쪽 위에서 잰다.',
        '`bbox_px`와 `coordinate_space`를 쓰면 변환은 컴파일러가 처리한다. mm 좌표가 있으면 기존 `bbox_mm`을 사용한다.',
        '반쪽 이미지는 탐색용이며 실제 단·문항 경계를 뜻하지 않는다.','']
    for index,view in enumerate(brief['navigation_views'],1):
        lines.extend([f"탐색 이미지 {index}: `{view['path']}`",
            f"- 크기 {view['size_px']} px; 원본 내 픽셀 경계 {view['pixel_box']}",
            '- 이 이미지에서 관찰한 좌표의 coordinate_space 값: '+json.dumps(view['coordinate_space'],ensure_ascii=False)])
    env=brief['environment']
    lines.extend(['',f"기본 글꼴: {brief['default_font']['family']} {brief['default_font']['pt']} pt",
        '공유 환경 확인: '+json.dumps({k:env[k] for k in ('status','engine','error') if k in env},ensure_ascii=False),
        '',brief['guide'],'','## 이 쪽의 실행 명령',''])
    for name in ('source_views','compile','figures'):
        lines.extend([f"### {name}",'```powershell',brief['commands'][name]['powershell'],'```',''])
    lines.extend([f"그림이 있을 때만 읽을 제작·검수 안내: `{brief['figure_guide']}`",
        '그림 검수 후 compile 명령 뒤에 --figures와 반환된 figures.json 경로를 붙인다.',
        '성공 응답의 output 경로·문항 번호·issues를 보고한다. 최종 원본/PDF 대조 요청을 기다린다.',''])
    return '\n'.join(lines)

def assignment_handoffs(result):
    from restoration_job import load_json
    records=result.get('assignments',[result]);handoffs=[]
    for record in records:
        packet=load_json(record['worker_input']);path=packet.get('worker_brief')
        if not path:raise ValueError('worker_brief_missing')
        brief=load_json(path);entry=brief.get('instructions_path',path)
        handoffs.append({'page':record['page'],'worker_id':record['worker_id'],'worker_brief':path,
            'worker_instructions':entry,
            'message':f'작업 안내: {entry}\n이 안내의 원본·작성 형식·명령으로 담당 쪽을 전사하고 검증된 출력 경로·문항 번호·issues를 보고하세요. 초안은 안내에 지정된 draft.json에 작성합니다. 이후 같은 쪽의 실제 출력 대조를 이어갑니다.'})
    return handoffs
