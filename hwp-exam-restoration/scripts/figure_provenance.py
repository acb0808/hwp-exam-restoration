"""Bind a TikZ render and its source comparison to the assigned exam question.

Hashes detect changed records; they do not authenticate the reviewer or prove
an external process ran. Real execution/tool history still needs inspection.
"""
from pathlib import Path
import hashlib,json

CHECK_NAMES=('geometry','labels','marks','source_comparison')
CHECK_VALUES=('passed','failed','not_verified')
REVIEW_INPUT_HELP=('Correct only the reported review fields and resubmit the same batch. '
    'No image reread or rerender is needed for formatting alone. Keep actual findings: '
    'never turn failed/not_verified into passed to satisfy validation. '
    'checks values are exact enums; put observed problems in issues, keep issues=[] for passed. '
    'Normal observations stay in the worker report, outside this tool payload. Do not inspect Python/server files.')

def review_input_errors(reviews):
    """Pure preflight for the complete batch, shared by MCP and direct dispatch."""
    errors=[]
    def error(index,ident,field,received,expected):
        errors.append({'index':index,'id':str(ident)[:80],'field':field,
                       'received':str(received)[:160],'expected':expected})
    if not isinstance(reviews,list):
        error(None,'','reviews',type(reviews).__name__,'array');return errors
    for index,row in enumerate(reviews):
        if not isinstance(row,dict):
            error(index,'','review',type(row).__name__,'object');continue
        ident=row.get('id','')
        if not isinstance(ident,str) or not ident.strip():error(index,ident,'id',ident,'nonempty figure ID')
        extra=set(row)-{'id','status','checks','issues'}
        if extra:error(index,ident,'review',sorted(extra),'only id/status/checks/issues')
        status=row.get('status');checks=row.get('checks');issues=row.get('issues')
        if status not in ('passed','failed'):error(index,ident,'status',status,['passed','failed'])
        if not isinstance(issues,list) or any(not isinstance(i,str) or not i.strip() for i in issues):
            error(index,ident,'issues',issues,'array of nonempty observed problem strings')
        elif status=='failed' and not issues:
            error(index,ident,'issues',issues,'at least one specific observed problem when status is failed')
        if not isinstance(checks,dict):
            error(index,ident,'checks',checks,list(CHECK_NAMES));continue
        for key in CHECK_NAMES:
            if checks.get(key) not in CHECK_VALUES:error(index,ident,'checks.'+key,checks.get(key),list(CHECK_VALUES))
        extra=set(checks)-set(CHECK_NAMES)
        if extra:error(index,ident,'checks',sorted(extra),list(CHECK_NAMES))
        if status=='passed':
            if any(checks.get(k)!='passed' for k in CHECK_NAMES):
                error(index,ident,'status','passed','all four checks must be passed')
            if issues!=[]:error(index,ident,'issues',issues,'[] when status is passed')
    return errors

def review_input_failure(errors,**context):
    return {'status':'failed','message':'invalid_figure_review_input',
            'corrections':errors[:12],'error_count':len(errors),'next_action':REVIEW_INPUT_HELP,**context}

def artifact(record):
    if not isinstance(record,dict) or set(record)!={'path','sha256'}:
        raise ValueError('tikz_artifact_requires_path_and_sha256')
    path=Path(record['path'])
    if not path.is_absolute() or not path.is_file():raise ValueError('tikz_artifact_requires_existing_absolute_path')
    data=path.read_bytes()
    if hashlib.sha256(data).hexdigest()!=record['sha256']:raise ValueError('tikz_artifact_hash_mismatch: '+str(path))
    return data

def validate_figures(page):
    from question_contract import figure_items
    figures=figure_items(page)
    verified=[]
    for qid,figure in figures:
        refs=figure.get('tikz')
        if not isinstance(refs,dict) or set(refs)!={'render','review'}:
            raise ValueError('tikz_provenance_required: '+qid)
        render=json.loads(artifact(refs['render']).decode('utf-8-sig'))
        review=json.loads(artifact(refs['review']).decode('utf-8-sig'))
        if (render.get('schema')!='tikz-render/1' or render.get('renderer')!='tikz'
                or render.get('status')!='rendered_pending_review' or render.get('exit_code')!=0):
            raise ValueError('successful_tikz_render_required: '+qid)
        for key in ('source','pdf','png','log'):artifact(render[key])
        if render['png']['sha256']!=figure['sha256'] or artifact(render['png'])!=artifact({k:figure[k] for k in ('path','sha256')}):
            raise ValueError('tikz_png_not_the_inserted_figure: '+qid)
        expected={'schema':'tikz-review/1','status':'passed','source_sha256':page['source_sha256'],
                  'page_number':page['page_number'],'question_id':qid,'worker_id':page['worker_id'],
                  'render_sha256':refs['render']['sha256'],'issues':[]}
        if any(review.get(k)!=v for k,v in expected.items()):raise ValueError('tikz_review_binding_mismatch: '+qid)
        checks=review.get('checks',{})
        if not isinstance(checks,dict) or any(checks.get(k)!='passed' for k in ('geometry','labels','marks','source_comparison')):
            raise ValueError('tikz_source_comparison_required: '+qid)
        verified.append({'page_number':page['page_number'],'question_id':qid,'renderer':'tikz',
                         'png_sha256':figure['sha256'],'render':refs['render'],'review':refs['review']})
    return verified
