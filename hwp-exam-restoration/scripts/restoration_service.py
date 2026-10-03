"""Task-level local workflow. No generic shell or source-code reader is exposed."""
from contextlib import contextmanager
from copy import deepcopy
import difflib
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import uuid

import restoration_job as job
import restoration_ab as ab
import restoration_batch as batch
from restoration_markdown import parse_reading_markdown, reading_to_markdown

SKILL = Path(__file__).resolve().parents[1]
_MUTEX = threading.RLock()
_ACTIONS = {'prepare','assign','submit_reading','status','inspect','approve',
            'render_figures','review_figures','compose','build','finish_review','read_asset'}
LAYOUT_GUIDE = '''units: px
| type | id | region | x | y | width | height |
| --- | --- | --- | --- | --- | --- | --- |
| region | left | - | X | Y | WIDTH | HEIGHT |
| question | q1 | left | X | Y | WIDTH | HEIGHT |
원본에서 관찰한 좌표로 대체하세요. px는 전체 페이지 기준이며 mm도 가능합니다.
각 단의 region 행과 제출한 모든 문항의 question 행을 원래 순서로 작성하세요.
본문은 쓰지 않습니다. 도구가 확정 전사를 가져옵니다.'''


def parse_layout_markdown(text):
    rows = [(i,s.strip()) for i,s in enumerate(text.splitlines(),1) if s.strip()]
    if not rows or rows[0][1] not in ('units: px','units: mm'):
        raise ValueError('layout line 1: use units: px or units: mm')
    unit = rows.pop(0)[1][-2:]
    if len(rows)<3: raise ValueError('layout: header, separator and geometry rows required')
    def cells(line): return [s.strip() for s in line.strip('|').split('|')]
    if cells(rows.pop(0)[1]) != ['type','id','region','x','y','width','height']:
        raise ValueError('layout: copy the returned table header exactly')
    separator=cells(rows.pop(0)[1])
    if len(separator)!=7 or any(not re.fullmatch(r':?-{3,}:?',s) for s in separator):
        raise ValueError('layout: Markdown table separator required')
    result={'schema':'restoration-layout/1','regions':[],'questions':[]}
    identifiers=set()
    for line,raw in rows:
        values=cells(raw)
        if len(values)!=7 or values[0] not in ('region','question'):
            raise ValueError(f'layout line {line}: expected region or question and seven cells')
        kind,identifier,region,*numbers=values
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',identifier) or identifier in identifiers:
            raise ValueError(f'layout line {line}: safe unique identifier required')
        identifiers.add(identifier)
        try: box=[float(n) for n in numbers]
        except ValueError: raise ValueError(f'layout line {line}: coordinates must be numbers') from None
        if not all(math.isfinite(n) for n in box) or min(box[:2])<0 or min(box[2:])<=0:
            raise ValueError(f'layout line {line}: finite x,y >= 0 and width,height > 0 required')
        value={'id':identifier,'bbox_'+unit:box}
        if unit=='px': value['coordinate_space']='page'
        if kind=='question': value['region_id']=region
        elif region!='-': raise ValueError(f'layout line {line}: region row must use - in region cell')
        result['regions' if kind=='region' else 'questions'].append(value)
    return result


def _fresh(root,suffix):
    folder=root/'mcp/evidence'; folder.mkdir(parents=True,exist_ok=True)
    return folder/(uuid.uuid4().hex+suffix)


def _register(meta,path,**binding):
    path=Path(path).resolve(strict=True)
    meta['assets'][str(path)]={'sha256':job.digest(path),**binding}
    return str(path)


def _source(root,page):
    manifest=job._manifest(root)
    if type(page) is not int or not 1<=page<=len(manifest['pages']): raise ValueError('page_out_of_range')
    return job._artifact(root,manifest['pages'][page-1]['image'])


def _image(path): return {'path':str(path),'mimeType':'image/png'}


def _assigned_text(root,page,filename,path):
    actual=Path(path).resolve(strict=True)
    expected=(root/'readers'/f'page-{page:04d}'/'a'/filename).resolve()
    if actual!=expected or not actual.is_relative_to(root): raise ValueError('use_assigned_production_file_path: '+str(expected))
    return actual.read_text(encoding='utf-8-sig')


def _pair(state):
    return {r:v['sha256'] for r,v in state['readings'].items()}


def _receipt(meta,root,page,kind,path,question_id=None):
    _,_,state=ab._load(root,page)
    token=uuid.uuid4().hex
    meta['inspections'][token]={'page':page,'kind':kind,'question_id':question_id,
        'pair':_pair(state),'source':state['source_sha256'],'path':str(path),'sha256':job.digest(path)}
    return token


def _inspect_valid(meta,root,page,tokens):
    _,_,state=ab._load(root,page); valid=[]
    for token in tokens:
        value=meta['inspections'].get(token)
        if (not value or value['page']!=page or value['pair']!=_pair(state)
                or value['source']!=state['source_sha256'] or job.digest(value['path'])!=value['sha256']):
            raise ValueError('inspection_receipt_stale_or_missing: inspect current reading pair')
        valid.append(value)
    return valid


def _page_status(root,page,meta):
    manifest,assignment,state=ab._load(root,page)
    if 'approval' in state:
        ab.production_state(root,page)
        accepted=next((a for a in manifest['accepted'] if a['page']==page),None)
        if accepted:
            try:
                ab.validate_accepted_result(root,job.load_json(job._artifact(root,accepted['result'])),assignment)
            except ValueError: accepted=None
        if accepted:
            native=meta.get('native')
            current=False
            if native:
                try:
                    current=(job.digest(native['path'])==native['sha256'] and batch.pages_digest(job.assemble(root))==native['pages_sha256'])
                    if current:
                        current=all(job.digest(v['path'])==v['sha256'] for v in job.load_json(native['path'])['artifacts'].values())
                except (ValueError,OSError): current=False
            if current:
                review=meta['reviews'].get(str(page),{})
                passed=review.get('status')=='passed' and review.get('build')==native['sha256']
                packet=next(x for x in job.load_json(native['path'])['review_inputs']['pages'] if x['page']==page)
                data=job.load_json(packet['review_input'])
                return {'status':'complete' if passed else 'pending_review','page':page,
                        'review_task':{'worker_id':state['worker_a'],'source_image':data['source_image'],'output_image':data['output_image']['path']},
                        'issues':review.get('issues',[]),
                        'next_action':'This page is complete; do not recompose.' if passed else 'Same A compares source/output paths and reports actual review; call hwp_finish_review with its completion evidence.'}
            return {'status':'accepted','page':page,'next_action':'After all pages are accepted call hwp_build. Do not recompose accepted pages unless corrections are needed.'}
        return {'status':'approved_pending_production','page':page,'owner_worker_id':state['worker_a'],
                'next_action':'A alone prepares approved figures if any and the returned layout table; call hwp_compose.'}
    if set(state['readings'])!={'a','b'}:
        return {'status':'waiting_for_readings','page':page,'received_roles':sorted(state['readings']),
                'next_action':'Wait for the missing independent reader; submit its Markdown path with hwp_submit_reading.'}
    comparison,values=ab._comparison(root,state)
    decision=job.load_json(ab._decision_template(root,state,comparison))
    decision['inspection_ids']=[]
    differences=[]
    for d in comparison['disputes']:
        # Only changed runs, never entire A/B pages in a main-agent response.
        lines=[]
        for role,reading in zip(('a','b'),values):
            one={**reading,'questions':[d[role]]} if d[role] else None
            lines.append(reading_to_markdown(one).splitlines() if one else ['(missing)'])
        diff='\n'.join(difflib.unified_diff(*lines,fromfile='A',tofile='B',n=1,lineterm=''))
        differences.append({'question_id':d['question_id'],'reasons':d['reasons'],
                            'difference':diff[:1200],'truncated':len(diff)>1200})
    return {'status':comparison['status'],'page':page,'matching_count':len(comparison['matching_ids']),
            'disputed_ids':[d['question_id'] for d in differences], 'disputes':differences,
            'page_issues':comparison['page_issues'],'figure_ids':comparison['figure_ids'],
            'decision_template':decision,'accepted':False,
            'next_action':'Master: hwp_inspect for source overview, then only disputed/suspect crops. Complete this decision and call hwp_approve. Never infer a visual pass from agreement.'}


def _perform(action,p,root,meta):
    page=p.get('page')
    if action=='prepare':
        manifest=job.prepare(Path(p['source']),root,dpi=p.get('dpi'))
        return {'status':'prepared','job':str(root),'page_count':len(manifest['pages']),
                'spawn_requests':[{'page':r['page'],'roles':['a','b'],'type':'hwp-restoration-reader'} for r in manifest['pages']],
                'next_action':'Spawn a fresh independent A/B pair for each page, keep model inherited; call hwp_assign with actual IDs and actual spawn response text.'}
    if action=='assign':
        evidence=_fresh(root,'.log'); evidence.write_text(p['evidence'],encoding='utf-8')
        result=ab.assign_readers(root,page,p['worker_a'],p['worker_b'],evidence)
        for h in result['handoffs']:
            task=Path(h['worker_instructions']); reading=task.with_name('reading.md')
            guide=(SKILL/'references/markdown-format.md').read_text(encoding='utf-8')
            task.write_text(f"# {page}쪽 독립 전사 {h['role'].upper()}\n\n원본: {_source(root,page)}\n저장: {reading}\n\n"
                '원본 전체 한 번을 읽어 본문·수식·선지·조건·배점을 Markdown으로 저장하세요. '
                '다른 전사 결과·다른 스킬·Python 소스·JSON 템플릿은 열지 마세요. '
                '도형은 표식만 남기고 crop·좌표·도형 제작·추가 에이전트·명령 실행은 하지 마세요. '
                '완료 후 파일 경로만 보고하고 대기하세요.\n\n'+guide,encoding='utf-8')
            h['message']=f'{task}만 읽고 원본 독립 전사. Markdown 결과 경로만 보고.'
            h['markdown_path']=str(reading)
            _register(meta,task)
        return {**result,'next_action':'Send each handoff only to its assigned reader. On completion submit markdown_path; do not read and re-emit its text.'}
    if action=='submit_reading':
        _,_,state=ab._load(root,page); role=p['role']
        if role not in ('a','b'): raise ValueError('reading_role_must_be_a_or_b')
        text=p.get('markdown'); source=p.get('markdown_path')
        if (text is None)==(source is None): raise ValueError('provide_exactly_one_markdown_or_markdown_path')
        if source is not None:
            actual=Path(source).resolve(strict=True)
            expected=(root/'readers'/f'page-{page:04d}'/role/'reading.md').resolve()
            if actual!=expected or not actual.is_relative_to(root): raise ValueError('use_assigned_reading_markdown_path')
            text=actual.read_text(encoding='utf-8-sig')
        value=parse_reading_markdown(text,page=page,worker_id=state['worker_'+role],source_sha256=state['source_sha256'])
        md=_fresh(root,'.md'); md.write_text(text,encoding='utf-8')
        data=md.with_suffix('.json'); job.save_json(data,value)
        result=ab.submit_reading(root,page,role,data)
        if result['status']!='already_submitted': meta['figures'].pop(str(page),None)
        meta['markdown'].append({'page':page,'role':role,'markdown':str(md),'sha256':job.digest(md),'reading_sha256':job.digest(data)})
        _,_,current=ab._load(root,page)
        ready=set(current['readings'])=={'a','b'}
        if ready: ab.compare_page(root,page)
        # Do not reveal the other reader's work to a submitting worker.
        return {**result,'comparison_ready':ready,'next_action':'Reader: report completion and wait. Master: call hwp_status for this page after both submissions.'}
    if action=='status':
        if page is not None: return _page_status(root,page,meta)
        m=job._manifest(root); assigned={a['page'] for a in m['assignments']}
        rows=[]
        for row in m['pages']:
            n=row['page']; entry={'page':n,'status':'unassigned'}
            if n in assigned:
                _,_,s=ab._load(root,n)
                entry['status']=_page_status(root,n,meta)['status'] if 'approval' in s else 'compared' if 'comparison' in s else 'waiting_for_readings'
            rows.append(entry)
        complete=all(r['status']=='complete' for r in rows)
        return {'status':'complete' if complete else 'in_progress','pages':rows,
                'next_action':'Deliver verified output.' if complete else 'Use hwp_status(page) for the next completed pair; wait on actual workers instead of polling.'}
    if action=='inspect':
        requests=p.get('requests'); images=[]; tokens=[]
        if requests is None:
            path=_source(root,page); images=[_image(path)]
            tokens=[_receipt(meta,root,page,'overview',path)]
        else:
            rows=[{**r,'coordinate_space':'page'} for r in requests]
            _,_,state=ab._load(root,page)
            if 'approval' in state:
                from restoration_prepare import source_views
                approved=job.load_json(job._artifact(root,state['approved_reading']))
                ids={q['id'] for q in approved['questions']}
                if not rows or any(r['question_id'] not in ids for r in rows): raise ValueError('approved_question_required_for_production_crop')
                out=root/'mcp'/('source-'+uuid.uuid4().hex)
                source_views(root,page,[{k:v for k,v in r.items() if k not in ('question_id','reason')} for r in rows],out)
                result={'index':str(out/'index.json')}
            else:
                result=ab.prepare_dispute_views(root,page,rows)
            views=job.load_json(result['index'])['views']
            for row,view in zip(rows,views):
                path=Path(view['image']['path']); images.append(_image(path))
                tokens.append(_receipt(meta,root,page,'crop',path,row['question_id']))
        return {'status':'ready_to_inspect','page':page,'inspection_ids':tokens,'_images':images,
                'image_paths':[item['path'] for item in images],
                'next_action':'Actually inspect these source images; use these IDs in decision.inspection_ids with current hwp_status decision template.'}
    if action=='approve':
        decision=deepcopy(p['decision']); tokens=decision.pop('inspection_ids',[])
        seen=_inspect_valid(meta,root,page,tokens)
        if not any(v['kind']=='overview' for v in seen): raise ValueError('source_overview_inspection_required')
        needed={r['question_id'] for r in decision.get('resolutions',[])}
        if not needed<={v['question_id'] for v in seen if v['kind']=='crop'}: raise ValueError('crop_inspection_required_for_each_resolution')
        result=ab.approve_page(root,page,decision)
        state=ab.production_state(root,page)
        approved=job.load_json(job._artifact(root,state['approved_reading']))
        path=_fresh(root,'.md'); path.write_text(reading_to_markdown(approved),encoding='utf-8')
        task=root/'ab'/f'page-{page:04d}'/'production.md'
        production_dir=root/'readers'/f'page-{page:04d}'/'a'
        task.write_text(f'# {page}쪽 제작 / 담당 {state["worker_a"]}\n\n확정 전사: {path}\n원본: {_source(root,page)}\n'
            f'저장 폴더: {production_dir}\n배치: {production_dir / "layout.md"}\n도형: 이 폴더의 FIGURE_ID.tex\n\n'
            '본문은 재작성하지 마세요. 관찰 좌표의 layout.md와 도형이 있다면 .tex만 작성하여 경로를 보고하세요. '
            '도형은 A만 한 번 복원합니다. 메인이 hwp_render_figures 호출 후 원본과 렌더를 실제 대조합니다. '
            '도형마다 승인된 ID·문항 ID·원본을 고려한 width_mm와 파일 경로를 간단히 보고하세요. '
            f'도형이 있을 때만 {SKILL / "references/tikz-exam.md"}의 그림 규칙을 읽되 명령은 실행하지 마세요. '
            '원본에 없는 선·라벨·표식이나 스캔 crop을 완성 도형에 넣지 않습니다. '
            '원본과 실제 렌더의 geometry·labels·marks·source_comparison을 각각 확인하고 passed/failed와 차이를 보고하세요. '
            'Python 파일이나 다른 스킬은 열지 마세요.\n\n'+LAYOUT_GUIDE,encoding='utf-8')
        return {'status':result['status'],'owner_worker_id':state['worker_a'],'approved_markdown':_register(meta,path),
                'production_task':_register(meta,task),'layout_template':LAYOUT_GUIDE,
                'next_action':'Send production_task only to the same A. B stays idle. Figures are optional; submit observed layout through hwp_compose.'}
    if action=='render_figures':
        rows=[]
        for figure in p['figures']:
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',figure['id']): raise ValueError('safe_figure_id_required')
            latex=figure.get('latex'); source=figure.get('latex_path')
            if (latex is None)==(source is None): raise ValueError('provide_exactly_one_latex_or_latex_path')
            if source is not None: latex=_assigned_text(root,page,figure['id']+'.tex',source)
            path=_fresh(root,'.tex'); path.write_text(latex,encoding='utf-8')
            rows.append({k:v for k,v in figure.items() if k not in ('latex','latex_path')}|{'source':str(path)})
        out=root/'mcp'/('figures-'+uuid.uuid4().hex)
        state=ab.production_state(root,page)
        previous=[path for path,ref in meta['batches'].items()
                  if ref=={'page':page,'approval':state['approval']['sha256']}]
        result=batch.prepare_figures(root,page,rows,out,reuse_batch=previous[-1] if previous else None)
        meta['batches'][str(out/'batch.json')]={'page':page,'approval':state['approval']['sha256']}
        reviews=[]; images=[]; review_tasks=[]
        for item in result['items']:
            if item['status']=='failed':
                log=out/item['id']/'compile.log'
                if log.exists():
                    lines=log.read_text(encoding='utf-8',errors='replace').splitlines()
                    concise=[line for line in lines if line.startswith(('!','l.')) or 'Error:' in line or 'Missing character:' in line]
                    for error in result['errors']:
                        if error['id']==item['id']: error['diagnostic']='\n'.join(concise[-10:])[:1200] or '\n'.join(lines[-8:])[:1200]
                continue
            review=job.load_json(item['review'])
            reviews.append({'id':item['id'],'checks':review['checks'],'issues':[],'status':'failed'})
            images.append(_image(item['png']))
            review_tasks.append({'id':item['id'],'reused':item.get('reused',False),'source_image':str(_source(root,page)),
                                 'render_image':_register(meta,item['png'])})
        return {'status':result['status'],'batch_path':result['batch'],'reviews':reviews,'errors':result['errors'],
                'review_tasks':review_tasks,
                'next_action':'Send review_tasks paths directly to A. A opens and compares each render with its original; main submits actual checks using hwp_review_figures. Never mark pass from render success.'}
    if action=='review_figures':
        path=Path(p['batch_path']).resolve(strict=True)
        ref=meta['batches'].get(str(path)); state=ab.production_state(root,page)
        if ref!={'page':page,'approval':state['approval']['sha256']}: raise ValueError('unknown_or_stale_figure_batch')
        value=job.load_json(path); supplied={r['id']:r for r in p['reviews']}
        if len(supplied)!=len(p['reviews']) or set(supplied)!={i['id'] for i in value['items']}: raise ValueError('review_each_figure_exactly_once')
        for item in value['items']:
            review=job.load_json(item['review']); r=supplied[item['id']]
            if r['status'] not in ('passed','failed') or (r['status']=='passed' and r['issues']): raise ValueError('invalid_figure_review_status')
            review.update({k:r[k] for k in ('checks','issues','status')}); job.save_json(item['review'],review)
        result=batch.finalize_figures(root,page,path.parent)
        if result['status']=='ready':
            meta['figures'][str(page)]={'path':result['figures'],'sha256':job.digest(result['figures']), 'approval':state['approval']['sha256']}
        return {**result,'next_action':'hwp_compose with the observed layout table.' if result['status']=='ready' else 'A corrects failed diagrams, then hwp_render_figures and real review again.'}
    if action=='compose':
        state=ab.production_state(root,page); figures=p.get('figures_path')
        saved=meta['figures'].get(str(page))
        if saved and saved['approval']!=state['approval']['sha256']: saved=None
        if figures is not None or saved:
            if not saved or saved['approval']!=state['approval']['sha256']: raise ValueError('current_reviewed_figures_required')
            if figures is not None and Path(figures).resolve()!=Path(saved['path']).resolve(): raise ValueError('use_returned_reviewed_figures_path')
            figures=job._artifact(root,{'path':Path(saved['path']).relative_to(root).as_posix(),'sha256':saved['sha256']})
        text=p.get('layout_markdown'); path=p.get('layout_path')
        if (text is None)==(path is None): raise ValueError('provide_exactly_one_layout_markdown_or_layout_path')
        if path is not None: text=_assigned_text(root,page,'layout.md',path)
        layout=parse_layout_markdown(text)
        output=_fresh(root,'.json'); result=ab.compose_page(root,page,layout,output,figures=figures)
        if result['status']!='compiled': return {**result,'next_action':'Correct only reported layout/math errors and call hwp_compose again. Content corrections require resubmitting Markdown and renewed approval.'}
        exists=any(a['page']==page for a in job._manifest(root)['accepted'])
        accepted=job.accept(root,output,replace=exists)
        return {'status':'accepted','page':page,'visual_status':'not_verified','next_action':'After all pages are accepted call hwp_build. Review final output before completion.'}
    if action=='build':
        from restoration_compiler import build_hwpx
        pages=job.assemble(root); output=Path(p['output']).resolve()
        if output.suffix.lower()!='.hwpx': raise ValueError('output_must_end_hwpx')
        if output.exists() or output.with_suffix('.build.json').exists(): raise ValueError('output_exists_choose_new_output')
        output.parent.mkdir(parents=True,exist_ok=True)
        fields={k:p[k] for k in ('school','year','exam_title')}
        result=build_hwpx(pages,output,template_dir=SKILL/'assets/templates/pdf2hwp-grid',title=p['title'],template_fields=fields)
        result.update(job=str(root),output=str(output),output_sha256=job.digest(output),pages_sha256=batch.pages_digest(pages),page_count=len(pages),visual_status='not_verified')
        receipt=output.with_suffix('.build.json'); job.save_json(receipt,result)
        meta.pop('native',None); meta['reviews']={}
        if not p.get('native',True):
            return {'status':'built','output':str(output),'visual_status':'not_verified','next_action':'Native rendering and actual final-page review still required; hwp_build with native=True and a fresh output path.'}
        # Fixed arguments, no shell, fresh local staging; never attach to or kill user's Hangul.
        native_dir=Path(tempfile.gettempdir())/('hwp-restoration-'+uuid.uuid4().hex)
        command=[sys.executable,'-B','-X','utf8',str(SKILL/'scripts/restore.py'),'native',str(root),str(receipt),str(native_dir)]
        run=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=900)  # may wait for another job's export
        log=_fresh(root,'.log'); log.write_text(run.stdout+'\n'+run.stderr,encoding='utf-8')
        native_receipt=native_dir/'restoration-native.json'
        if not native_receipt.exists(): raise ValueError('native_render_failed: '+run.stdout[-500:])
        native=job.load_json(native_receipt)
        if native.get('status')!='rendered' or native.get('review_inputs',{}).get('status')!='pending_review':
            return {'status':'failed','message':native.get('error','native_review_pack_unavailable'),'output':str(output),
                    'next_action':'Preserve the HWPX. Resolve the reported Hangul/render condition and retry with a fresh output; never close the user session.'}
        meta['native']={'path':str(native_receipt),'sha256':job.digest(native_receipt),'pages_sha256':native['pages_sha256']}
        tasks=[]
        for packet in native['review_inputs']['pages']:
            data=job.load_json(packet['review_input']); n=packet['page']; binding={'page':n,'build':job.digest(native_receipt)}
            source=_register(meta,data['source_image'],purpose='final_source',**binding)
            image=_register(meta,data['output_image']['path'],purpose='final_output',**binding)
            tasks.append({'page':n,'worker_id':packet['worker_id'],'source_image':source,'output_image':image})
        return {'status':'pending_review','artifacts':{k:v['path'] for k,v in native['artifacts'].items()},
                'review_tasks':tasks,'visual_status':'not_verified',
                'next_action':'Send each review_tasks source/output paths directly to its assigned A. A opens both, checks text/math/figures/order/clipping and reports verdict, differences, its worker ID and both paths. Pass the actual completion response as hwp_finish_review.review_evidence. Do not reopen all images in master. Missing/unreadable images cannot pass.'}
    if action=='read_asset':
        path=Path(p['asset_path']).resolve(strict=True); ref=meta['assets'].get(str(path))
        if not ref or job.digest(path)!=ref['sha256']: raise ValueError('asset_not_registered_or_changed')
        if path.suffix.lower()=='.png':
            if ref.get('purpose') in ('final_source','final_output'):
                meta['delivered'].append({'path':str(path),**ref})
            return {'status':'ready_to_inspect','asset_path':str(path),'_images':[_image(path)],'next_action':'Actually inspect the image before recording review.'}
        if path.suffix.lower()=='.md':
            text=path.read_text(encoding='utf-8'); limit=12000
            return {'status':'ready','text':text[:limit],'truncated':len(text)>limit,
                    'next_action':'Follow the task instructions. If truncated, use the assigned reader to open the returned Markdown file.'}
        raise ValueError('asset_type_not_exposed')
    if action=='finish_review':
        native=meta.get('native')
        if not native or job.digest(native['path'])!=native['sha256'] or batch.pages_digest(job.assemble(root))!=native['pages_sha256']:
            raise ValueError('current_native_output_required')
        record=job.load_json(native['path'])
        if type(page) is not int or not 1<=page<=len(job._manifest(root)['pages']): raise ValueError('page_out_of_range')
        for asset in record['artifacts'].values():
            if job.digest(asset['path'])!=asset['sha256']: raise ValueError('native_artifact_changed_rebuild_required')
        for path,ref in meta['assets'].items():
            if ref.get('page')==page and ref.get('build')==native['sha256'] and job.digest(path)!=ref['sha256']:
                raise ValueError('review_image_changed_rebuild_required')
        seen={x.get('purpose') for x in meta['delivered'] if x.get('page')==page and x.get('build')==native['sha256']}
        evidence=p.get('review_evidence')
        evidence_ref=None
        if evidence is not None:
            assignment=next(a for a in job._manifest(root)['assignments'] if a['page']==page)
            packet=next(x for x in record['review_inputs']['pages'] if x['page']==page)
            inputs=job.load_json(packet['review_input'])
            required=[assignment['worker_id'],inputs['source_image'],inputs['output_image']['path']]
            # Preserve reported host evidence; this local service cannot authenticate an agent's claims.
            if not isinstance(evidence,str) or any(item not in evidence for item in required):
                raise ValueError('actual_owner_review_response_with_worker_id_and_both_paths_required')
            evidence_ref=str(_fresh(root,'.log')); Path(evidence_ref).write_text(evidence,encoding='utf-8')
        elif seen!={'final_source','final_output'}: raise ValueError('source_and_output_images_must_be_opened_or_actual_owner_review_evidence_required')
        if p['status'] not in ('passed','failed') or (p['status']=='passed' and p['issues']): raise ValueError('invalid_final_review_status')
        meta['reviews'][str(page)]={'status':p['status'],'issues':p['issues'],'build':native['sha256'],
                                    'evidence':evidence_ref,'evidence_scope':'reported_visual_review_not_independent_host_authentication'}
        count=len(job._manifest(root)['pages'])
        complete=all(meta['reviews'].get(str(n),{}).get('status')=='passed' for n in range(1,count+1))
        return {'status':'complete' if complete else 'pending_review','reviews':meta['reviews'],
                'visual_status':'passed' if complete else 'not_verified',
                'next_action':'Deliver verified output.' if complete else 'Review remaining pages or correct failed content/layout/figures, rebuild and review again.'}
    raise ValueError('unknown_action')


def dispatch_legacy(action,params):
    """Serialize local operations and return bounded, corrective errors, not traces."""
    advice={
        'prepare':'Correct source/job path; choose an empty job directory. Image input requires explicit dpi.',
        'assign':'Use fresh actual A/B IDs and actual spawn response text; one pair per page.',
        'submit_reading':'Correct the indicated Markdown line/question in the same reading.md and submit again. Use only the assigned output path or Markdown text, not both.',
        'status':'Check the prepared job path and page. Complete the missing assignment or submissions.',
        'inspect':'Use hwp_status for disputed IDs. bbox_px is [x,y,width,height] in full-page pixels; supply reason for an agreed but suspect question.',
        'approve':'Use hwp_inspect on the current source overview and each disputed crop, then fill the current hwp_status decision template with inspection_ids and actual source checks.',
        'render_figures':'Use only approved figure IDs; A corrects reported LaTeX or rendering errors. Never read Python internals to guess parameters.',
        'review_figures':'Use the returned batch_path and all returned check names; failed figures need correction and new real comparison.',
        'compose':'Correct the indicated layout Markdown row, preserve question order and source coordinates. A text correction requires renewed A/B approval.',
        'build':'Use a fresh .hwpx output path and accepted current pages. Preserve user Hangul sessions; correct the reported native condition before retrying.',
        'read_asset':'Use an exact asset_path returned by this job. Python/internal files and changed artifacts are not exposed.',
        'finish_review':'Open the current source and final output with hwp_read_asset; inspect before reporting. Changed pages need rebuild and renewed review.'}
    try:
        if action not in _ACTIONS: raise ValueError('unknown_action')
        root=Path(params['job']).resolve()
        with _MUTEX:
            if action!='prepare' and not (root/'manifest.json').exists(): raise ValueError('prepared_job_required')
            # One server process is the supported local owner. Core job mutations also have a filesystem lock.
            statepath=root/'mcp/state.json'
            meta=job.load_json(statepath) if statepath.exists() else {'assets':{},'inspections':{},'markdown':[],'batches':{},'figures':{},'reviews':{},'delivered':[]}
            try: return _perform(action,params,root,meta)
            finally:
                if (root/'manifest.json').exists(): job.save_json(statepath,meta)
    except (ValueError,KeyError,TypeError,OSError,ImportError,subprocess.TimeoutExpired) as exc:
        return {'status':'failed','action':action,'message':str(exc)[:1600],
                'next_action':advice.get(action,'Use an available hwp_ tool and its documented fields.')}


def dispatch(action,params):
    from restoration_single import dispatch as single_dispatch
    return single_dispatch(action,params)
