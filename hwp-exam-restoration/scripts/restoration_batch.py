"""Batch deterministic preparation. All visual decisions remain with the worker."""
from pathlib import Path
import hashlib
import json
import math
import os
import re
import runpy
from restoration_job import _manifest, assemble, digest, load_json, save_json, figure_info


def native_page(page):
    # Review-only metadata never affects the native document or its bindings.
    return ({k:v for k,v in page.items() if k!='answer_review_questions'}
            if isinstance(page,dict) and 'answer_review_questions' in page else page)

def pages_digest(pages):
    # Preserve native receipts from jobs built before answer delta reviews.
    # Render-environment dictionaries also use this helper and must retain
    # both their keys and values in the hash.
    value=[native_page(page) for page in pages] if isinstance(pages,list) else pages
    return hashlib.sha256(json.dumps(value,
                                     sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def binding(job, page):
    root = Path(job).resolve(strict=True)
    manifest = _manifest(root)
    matches = [a for a in manifest['assignments'] if a['page'] == page]
    if len(matches) != 1: raise ValueError('missing_page_assignment')
    return root, {'job':str(root), 'page':page, 'assignment_id':matches[0]['assignment_id'],
                  'worker_id':matches[0]['worker_id'], 'source_sha256':manifest['source']['sha256']}


def new_folder(path):
    path = Path(path).resolve()
    if path.exists(): raise ValueError('choose_new_output_directory')
    path.mkdir(parents=True)
    return path


def _render_conditions(runtime, renderer_path, source, engine, dpi):
    """Fingerprint available render inputs; missing facts disable reuse."""
    try:
        source_text = Path(source).read_text(encoding='utf-8-sig')
        # Explicit external inputs need a dependency graph we do not record.
        # Re-render rather than assuming that unchanged TeX means unchanged data.
        if re.search(r'\\(?:input|include|includeonly|import|subimport|inputfrom|subinputfrom|'
                     r'openin|read|readline|pgfplotstableread|lstinputlisting|verbatiminput)\b', source_text):
            return None
        executable = runtime['engine_path'](engine).resolve(strict=True)
        # TeX path overrides and font configuration affect rendering as well.
        environment = {k:v for k,v in os.environ.items()
                       if k.upper().startswith(('TEX', 'MF', 'FONTCONFIG'))}
        import fitz
        return {'source_sha256':digest(source), 'engine':str(executable),
                'engine_sha256':digest(executable), 'dpi':dpi,
                'renderer_sha256':digest(renderer_path),
                'pymupdf_version':str(getattr(fitz, 'VersionBind', 'unknown')),
                'environment_sha256':pages_digest(environment),
                'flags':['-no-shell-escape', '-interaction=nonstopmode', '-halt-on-error']}
    except (ValueError, KeyError, TypeError, OSError, ImportError):
        return None


def _receipt_matches_conditions(receipt, conditions):
    if conditions is None: return False
    recorded = load_json(receipt)
    return (recorded.get('source',{}).get('sha256') == conditions['source_sha256']
            and recorded.get('engine') == conditions['engine']
            and recorded.get('dpi') == conditions['dpi']
            and recorded.get('command') == [conditions['engine'], *conditions['flags'], 'diagram.tex'])


def prepare_figures(job, page, rows, output, *, engine=None, dpi=300, reuse_batch=None):
    root, identity = binding(job, page)
    from restoration_ab import check_figure_requests
    check_figure_requests(root,page,rows)
    if not isinstance(rows,list) or not rows: raise ValueError('nonempty_figure_array_required')
    ids = set()
    for row in rows:
        if not isinstance(row,dict): raise ValueError('figure_object_required')
        id_ = row.get('id')
        if not isinstance(id_,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',id_): raise ValueError('safe_figure_id_required')
        if id_ in ids: raise ValueError('duplicate_figure_id')
        ids.add(id_)
        if not isinstance(row.get('question_id'),str) or not row['question_id'].strip(): raise ValueError('question_id_required')
        width = row.get('width_mm')
        if type(width) not in (int,float) or not math.isfinite(width) or width <= 0: raise ValueError('positive_finite_width_required')
        if ('source' in row) == ('render_receipt' in row): raise ValueError('provide_source_or_render_receipt')
    if type(dpi) is not int or not 150 <= dpi <= 600: raise ValueError('dpi_150_to_600_required')
    previous = {}
    if reuse_batch is not None:
        previous_path = Path(reuse_batch).resolve(strict=True)
        if previous_path.is_dir(): previous_path = previous_path/'batch.json'
        saved = load_json(previous_path)
        if (not isinstance(saved,dict) or saved.get('schema')!='restoration-figure-batch/1'
                or any(saved.get(k)!=v for k,v in identity.items())):
            raise ValueError('figure_batch_binding_mismatch')
        if not isinstance(saved.get('items'),list): raise ValueError('invalid_reuse_batch_items')
        for item in saved['items']:
            if not isinstance(item,dict) or not isinstance(item.get('id'),str) or item['id'] in previous:
                raise ValueError('invalid_reuse_batch_items')
            previous[item['id']] = item
    out = new_folder(output); items = []; errors = []
    runtime = None
    renderer_path = Path(__file__).resolve().parents[1]/'runtime/tikz_render.py'
    for row in rows:
        item = {'id':row['id'], 'question_id':row['question_id'], 'width_mm':row['width_mm'],
                'reused':False, 'reuse_reason':'not_requested' if reuse_batch is None else 'no_matching_render'}
        try:
            if 'source' in row:
                if runtime is None: runtime = runpy.run_path(str(renderer_path))
                conditions = _render_conditions(runtime, renderer_path, row['source'], engine, dpi)
                prior = previous.get(row['id'], {})
                receipt = None
                if (conditions is not None and prior.get('question_id')==row['question_id']
                        and prior.get('render_conditions')==conditions
                        and prior.get('status')=='pending_review'):
                    receipt = Path(prior['render_receipt']).resolve(strict=True)
                    if digest(receipt)!=prior.get('render_sha256'):
                        raise ValueError('render_receipt_hash_mismatch')
                    if not _receipt_matches_conditions(receipt, conditions):
                        raise ValueError('reuse_render_conditions_mismatch')
                    # figure_info below verifies every recorded artifact hash.
                    item.update(reused=True, reuse_reason='verified_unchanged_render')
                if receipt is None:
                    render_dir = out/row['id']
                    runtime['render'](row['source'], render_dir,
                                      engine=conditions['engine'] if conditions else engine, dpi=dpi)
                    receipt = render_dir/'render.json'
                    if reuse_batch is not None:
                        item['reuse_reason']='render_conditions_changed_or_unavailable'
                if _receipt_matches_conditions(receipt, conditions):
                    item['render_conditions']=conditions
            else:
                receipt = Path(row['render_receipt']).resolve(strict=True)
                item['reuse_reason']='explicit_render_receipt'
            info = figure_info(root, page, row['question_id'], receipt, width_mm=row['width_mm'])
            review = out/(row['id']+'.review.json')
            save_json(review,info['review_template'])
            item.update(render_receipt=str(receipt), render_sha256=digest(receipt),
                        png=info['figure']['path'], review=str(review), status='pending_review')
        except (ValueError, KeyError, TypeError, OSError, ImportError) as exc:
            item.update(status='failed', reused=False, error=str(exc)); errors.append({'id':row['id'],'error':str(exc)})
        items.append(item)
    batch = {'schema':'restoration-figure-batch/1', **identity, 'items':items,
             'status':'failed' if errors else 'pending_review', 'errors':errors}
    save_json(out/'batch.json', batch)
    return {**batch, 'batch':str(out/'batch.json'), 'note':'Open each original and PNG, then edit each pending review. No review is automatically passed.'}


def finalize_figures(job, page, output):
    root, identity = binding(job, page)
    out = Path(output).resolve(strict=True); batch = load_json(out/'batch.json')
    if batch.get('schema') != 'restoration-figure-batch/1' or any(batch.get(k)!=v for k,v in identity.items()):
        raise ValueError('figure_batch_binding_mismatch')
    if batch.get('errors') or not batch.get('items'): raise ValueError('repair_failed_items_in_new_batch')
    figures = {}; errors = []
    for item in batch['items']:
        try:
            if item['id'] in figures: raise ValueError('duplicate_figure_id')
            if digest(item['render_receipt']) != item['render_sha256']: raise ValueError('render_receipt_hash_mismatch')
            review = load_json(item['review'])
            if review.get('status')=='failed':
                errors.append({'id':item['id'],'error':'figure_comparison_failed',
                               'issues':review.get('issues',[])})
                continue
            info = figure_info(root,page,item['question_id'],item['render_receipt'],
                               width_mm=item['width_mm'],review=item['review'])
            figures[item['id']] = info['figure']
        except (ValueError, KeyError, TypeError, OSError, ImportError) as exc:
            errors.append({'id':item.get('id'),'error':str(exc)})
    if errors: return {'status':'failed', 'errors':errors, 'visual_status':'not_verified'}
    result = out/'figures.json'
    save_json(result, {'schema':'restoration-figures/1', **identity, 'figures':figures})
    return {'status':'ready', 'figures':str(result), 'count':len(figures), 'errors':[]}


def review_pack(job, native, output, *, dpi=200):
    """Prepare original/output views only for a successful bound native result."""
    from figure_provenance import artifact
    from PIL import Image
    import fitz
    root = Path(job).resolve(strict=True); pages = assemble(root); manifest = _manifest(root)
    if (native.get('status')!='rendered' or native.get('job')!=str(root)
            or native.get('pages_sha256')!=pages_digest(pages) or native.get('page_count')!=len(pages)):
        raise ValueError('native_review_binding_mismatch')
    if type(dpi) is not int or not 150 <= dpi <= 300: raise ValueError('review_dpi_150_to_300_required')
    pdf = native['artifacts']['pdf']; artifact(pdf)
    with fitz.open(pdf['path']) as doc:
        if len(doc)!=len(pages): raise ValueError('native_page_count_differs_from_source')
        if any(p.rect.width*p.rect.height*(dpi/72)**2 > 40_000_000 for p in doc): raise ValueError('review_pixel_budget_exceeded')
        out = new_folder(output); packets = []
        for number, rendered in enumerate(doc,1):
            png = out/f'page-{number:04d}.png'
            rendered.get_pixmap(dpi=dpi,alpha=False).save(png)
            crops = []  # Full pages only; enlarge only an observed uncertainty.
            source_number = pages[number-1]['page_number']
            if pages[number-1].get('role')=='answer_sheet':
                reference=pages[number-1]['answer_reference'];artifact(reference)
                packet={'schema':'restoration-review-input/1','kind':'answer_sheet','page':source_number,'output_page':number,
                        'worker_id':'mechanical-answer-sheet','answer_reference':reference['path'],
                        'output_pdf':pdf,'output_image':{'path':str(png),'sha256':digest(png)},
                        'pages_sha256':native['pages_sha256'],'visual_status':'not_verified'}
                packet['instructions']='마지막 정답표의 번호·객관식 선택지·서답형 소문항·수식·잘림을 검수하세요. answer_reference의 문항과 후보 근거를 읽고 독립 계산으로 정답을 확인하세요. 이미 본 원본은 재사용합니다. 미확정·오답·누락은 failed로 보고하고 해당 담당자의 answer 블록만 수정합니다.'
                path=out/f'page-{number:04d}.json';save_json(path,packet)
                packets.append({'page':source_number,'output_page':number,'worker_id':'mechanical-answer-sheet','review_input':str(path)})
                continue
            original = manifest['pages'][source_number-1]
            assignment = next(a for a in manifest['assignments'] if a['page']==source_number)
            packet = {'schema':'restoration-review-input/1', 'page':source_number, 'output_page':number, 'worker_id':assignment['worker_id'],
                      'source_image':str((root/original['image']['path']).resolve()), 'source_sha256':manifest['source']['sha256'],
                      'output_pdf':pdf, 'output_image':{'path':str(png),'sha256':digest(png)}, 'output_crops':crops,
                      'pages_sha256':native['pages_sha256'], 'visual_status':'not_verified',
                      'note':'Open original and output. Source/output page numbers can differ after excluding cover and blank pages. Enlarge only unclear areas; report actual differences.',
                      'instructions':'원본과 출력의 전체 이미지를 실제로 열어 같은 쪽·단 순서, 본문·배점·선지·수식·보기의 누락과 오독, 그림 라벨·표식, 겹침·잘림·넘침을 검사하세요. 모호한 부분만 추가 확대하세요. 임의 문항 이동이나 글자 축소는 금지합니다. 통과/수정 필요/검수 불가 판정과 문항별 차이를 보고하세요. 문제만 대조하며 표지·빈 면·원본 머리말·꼬리말은 제외합니다. 수정은 담당자의 Markdown 또는 도형 원본에 반영하고 MCP로 다시 출력합니다. 자료를 열 수 없으면 검수 불가입니다.'}
            if assignment.get('ab_session'):
                packet['instructions'] = ('원본과 출력의 전체 쪽을 실제로 대조하고 불명확한 부분만 확대하세요. '
                    '문항·단 순서, 본문·배점·보기·선지·수식, 그림 라벨·표식, 겹침·잘림·넘침을 검사하세요. '
                    '임의 문항 이동·글자 축소는 금지합니다. 판정과 문항별 차이만 보고하세요. '
                    '본문 오독은 해당 reading 수정→ab-submit→ab-compare→원본 재판정→ab-approve→ab-compose가 필요합니다. '
                    '배치만 수정하면 layout→ab-compose, 도형만 수정하면 A가 재제작·원본 대조→메인 figures/finalize→ab-compose로 진행합니다. '
                    '출력 JSON 직접 수정은 금지하며 메인이 revise 후 새 출력으로 다시 대조합니다. 자료를 열 수 없으면 검수 불가입니다.')
            path = out/f'page-{number:04d}.json';save_json(path,packet)
            packets.append({'page':source_number,'output_page':number,'worker_id':assignment['worker_id'],'review_input':str(path),
                            'message':f'검수 입력: {path}. instructions에 따라 실제 대조하고 판정과 문항별 차이를 보고하세요.'})
        result={'status':'pending_review','pages':packets,'visual_status':'not_verified'}
        save_json(out/'index.json',result)
    return result
