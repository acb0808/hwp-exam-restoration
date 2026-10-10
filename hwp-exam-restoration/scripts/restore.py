"""Coordinator entry point. Build only from validated, accepted page artifacts."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
from restoration_console import emit_json
from restoration_handoff import preparation_handoff,assignment_handoffs,ordered_workers,automatic_output,input_json


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def blocked_before_native(run_dir, error):
    """Preserve proof that validation failed before an owned HWP session began."""
    result={'status':'blocked','error':error,'cleanup':'no_session_started'}
    run_dir.mkdir(parents=True,exist_ok=True)
    run_dir.joinpath('restoration-native.json').write_text(
        json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    emit_json(result)
    return 2


def summarize(command,result):
    """Large build geometry stays in the receipt, never hidden or discarded."""
    if command in ('assign','assign-many'):
        return {'status':'assigned','handoffs':[
            {k:h[k] for k in ('page','worker_id','worker_instructions','message') if k in h}
            for h in result.get('handoffs',[])]}
    if command=='build':
        keys=('status','output','output_sha256','page_count','template','visual_status')
        return {**{k:result[k] for k in keys if k in result},'receipt':str(Path(result['output']).with_suffix('.build.json'))}
    if command=='figures':
        return {**{k:v for k,v in result.items() if k!='items'},
                'items':[{k:v for k,v in item.items() if k!='render_conditions'} for item in result.get('items',[])]}
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('prepare', help='Snapshot source PDF and render page inputs')
    p.add_argument('source', type=Path); p.add_argument('job', type=Path)
    p.add_argument('--dpi', type=int, help='PDF defaults to 200; image inputs require explicit physical DPI')
    p.add_argument('--legacy-workers', action='store_true', help='Use the compatibility single-reader handoff instead of independent A/B transcription')
    p = commands.add_parser('ab-assign', help='Bind two independent whole-page readers to actual worker evidence')
    p.add_argument('job', type=Path); p.add_argument('page', type=int)
    p.add_argument('--worker-a', required=True); p.add_argument('--worker-b', required=True)
    p.add_argument('--evidence', type=Path, required=True)
    p = commands.add_parser('ab-submit', help='Snapshot one bound reader transcription without accepting it')
    p.add_argument('job', type=Path); p.add_argument('page', type=int)
    p.add_argument('role', choices=['a', 'b']); p.add_argument('reading', type=Path)
    p = commands.add_parser('ab-compare', help='Compare independent readings and retain detailed differences on disk')
    p.add_argument('job', type=Path); p.add_argument('page', type=int)
    p = commands.add_parser('ab-crops', help='Prepare only explicit unresolved source regions through the shared crop tool')
    p.add_argument('job', type=Path); p.add_argument('page', type=int); p.add_argument('spec', type=Path)
    p = commands.add_parser('ab-approve', help='Record source-backed coordinator decisions without accepting a compiled page')
    p.add_argument('job', type=Path); p.add_argument('page', type=int); p.add_argument('decision', type=Path)
    p = commands.add_parser('ab-compose', help='Combine approved transcription, explicit layout and singly owned finalized figures')
    p.add_argument('job', type=Path); p.add_argument('page', type=int)
    p.add_argument('layout', type=Path); p.add_argument('output', type=Path, nargs='?')
    p.add_argument('--figures', type=Path, help='Bound figures.json produced by figures-finalize')
    p = commands.add_parser('assign', help='Bind one page to an actual worker/tool receipt')
    p.add_argument('job', type=Path); p.add_argument('page', type=int)
    p.add_argument('--worker-id', required=True); p.add_argument('--evidence', type=Path, required=True)
    p.add_argument('--mode', choices=['subagent', 'main_exception'], default='subagent')
    p.add_argument('--approval', type=Path)
    p = commands.add_parser('assign-many',help='Register actual page workers from one saved tool response')
    p.add_argument('job',type=Path);p.add_argument('mapping',type=Path,nargs='?');p.add_argument('--workers',nargs='+');p.add_argument('--evidence',type=Path,required=True)
    p = commands.add_parser('validate-page',help='Report contract, assignment, equation and figure errors without accepting or rewriting')
    p.add_argument('job',type=Path);p.add_argument('result',type=Path)
    p = commands.add_parser('compile-draft',help='Expand explicit transcription into validated v2 JSON without accepting or repairing it')
    p.add_argument('job',type=Path);p.add_argument('page',type=int);p.add_argument('draft',type=Path);p.add_argument('output',type=Path,nargs='?')
    p.add_argument('--figures',type=Path,help='Bound figures.json produced by figures-finalize')
    p = commands.add_parser('source-views',help='Prepare original-pixel crops and coordinate scales in one batch')
    p.add_argument('job',type=Path);p.add_argument('page',type=int);p.add_argument('spec',type=Path);p.add_argument('output',type=Path,nargs='?')
    p = commands.add_parser('environment',help='Refresh shared executable discovery; does not verify visual font output')
    p.add_argument('job',type=Path);p.add_argument('--engine',type=Path)
    p = commands.add_parser('figure-info',help='Derive figure dimensions, hashes and a pending review template')
    p.add_argument('job',type=Path);p.add_argument('page',type=int);p.add_argument('question_id');p.add_argument('render_receipt',type=Path)
    p.add_argument('--width-mm',type=float,required=True);p.add_argument('--review',type=Path)
    p = commands.add_parser('figures',help='Render or inspect a page figure batch and save pending review files')
    p.add_argument('job',type=Path);p.add_argument('page',type=int);p.add_argument('spec',type=Path);p.add_argument('output',type=Path,nargs='?')
    p.add_argument('--reuse-batch',type=Path,help='Reuse only identical, bound, hash-verified prior renders; new reviews remain pending')
    p.add_argument('--engine',type=Path);p.add_argument('--dpi',type=int,default=300)
    p = commands.add_parser('figures-finalize',help='Validate completed figure reviews and export linked figure objects')
    p.add_argument('job',type=Path);p.add_argument('page',type=int);p.add_argument('output',type=Path)
    p = commands.add_parser('review-pack',help='Prepare page views from a successful bound native receipt; never approves output')
    p.add_argument('job',type=Path);p.add_argument('native_receipt',type=Path);p.add_argument('output',type=Path)
    p.add_argument('--dpi',type=int,default=200)
    p = commands.add_parser('accept', help='Validate and snapshot an unchanged worker result')
    p.add_argument('job', type=Path); p.add_argument('result', type=Path)
    p = commands.add_parser('revise', help='Accept a correction by the same worker, preserving prior receipt')
    p.add_argument('job', type=Path); p.add_argument('result', type=Path)
    p = commands.add_parser('build', help='Compile all accepted pages without rewriting them')
    p.add_argument('job', type=Path); p.add_argument('output', type=Path)
    p.add_argument('--automation-dir', type=Path)
    p.add_argument('--template-dir',type=Path,default=Path(__file__).resolve().parents[1]/'assets/templates/pdf2hwp-grid',
                   help='Saved exam template (default: bundled pdf2HWP six-row merged grid); never fall back to a blank layout')
    p.add_argument('--title')
    p.add_argument('--template-fields',type=Path,help='JSON values for explicitly declared source template fields')
    p = commands.add_parser('native', help='Render a bound build through isolated Hancom automation')
    p.add_argument('job', type=Path); p.add_argument('build_receipt', type=Path)
    p.add_argument('run_dir', type=Path)
    p.add_argument('--automation-dir', type=Path)
    p.add_argument('--runtime-site-packages', type=Path)
    for subparser in commands.choices.values():
        subparser.add_argument('--full',action='store_true',help='Print the complete receipt (normally retained on disk)')
    args = parser.parse_args(argv)
    from restoration_job import prepare, assign, accept, assemble
    if args.command == 'prepare':
        result = prepare(args.source, args.job, dpi=args.dpi)
        if args.legacy_workers:
            next_steps=preparation_handoff(args.job,result)
        else:
            from restoration_ab import preparation_handoff as ab_preparation_handoff
            next_steps=ab_preparation_handoff(args.job,result)
        if not args.full and 'pages' in result:
            result={'status':'prepared','job':str(args.job.resolve()),'manifest':str((args.job/'manifest.json').resolve()),
                    'inputs':str((args.job/'inputs/index.json').resolve()),'page_count':len(result['pages']),
                    'pages':[{'page':p['page'],'image':str((args.job/p['image']['path']).resolve())} for p in result['pages']]}
        result['next']=next_steps
    elif args.command == 'ab-assign':
        from restoration_ab import assign_readers
        result=assign_readers(args.job,args.page,args.worker_a,args.worker_b,args.evidence)
    elif args.command == 'ab-submit':
        from restoration_ab import submit_reading
        result=submit_reading(args.job,args.page,args.role,args.reading)
    elif args.command == 'ab-compare':
        from restoration_ab import compare_page
        result=compare_page(args.job,args.page)
    elif args.command == 'ab-crops':
        from restoration_ab import prepare_dispute_views
        result=prepare_dispute_views(args.job,args.page,input_json(args.spec))
    elif args.command == 'ab-approve':
        from restoration_ab import approve_page
        result=approve_page(args.job,args.page,input_json(args.decision))
    elif args.command == 'ab-compose':
        from restoration_ab import compose_page
        output=args.output or automatic_output(args.job,args.page,'ab-compiled','.json')
        result=compose_page(args.job,args.page,input_json(args.layout),output,figures=args.figures)
    elif args.command == 'assign':
        result = assign(args.job, args.page, args.worker_id, args.evidence,
                        mode=args.mode, approval=args.approval)
    elif args.command=='assign-many':
        from restoration_job import assign_many,load_json
        if bool(args.mapping)==bool(args.workers):raise ValueError('provide_mapping_or_ordered_workers')
        rows=ordered_workers(args.job,args.workers) if args.workers else load_json(args.mapping)
        result=assign_many(args.job,rows,args.evidence)
    elif args.command=='validate-page':
        from restoration_job import validate_result
        result=validate_result(args.job,args.result)
    elif args.command=='compile-draft':
        from restoration_draft import compile_draft
        from restoration_job import load_json
        draft=input_json(args.draft)
        output=args.output or automatic_output(args.job,args.page,'compiled','.json')
        result=compile_draft(args.job,args.page,draft,output,figures=args.figures)
    elif args.command=='source-views':
        from restoration_prepare import source_views
        from restoration_job import load_json
        rows=input_json(args.spec)
        result=source_views(args.job,args.page,rows,args.output or automatic_output(args.job,args.page,'source-views'))
    elif args.command=='environment':
        from restoration_prepare import environment_info
        from restoration_job import _manifest,save_json
        root=args.job.resolve(strict=True);_manifest(root)
        result=environment_info(args.engine);save_json(root/'environment.json',result)
    elif args.command=='figure-info':
        from restoration_job import figure_info
        result=figure_info(args.job,args.page,args.question_id,args.render_receipt,width_mm=args.width_mm,review=args.review)
    elif args.command=='figures':
        from restoration_batch import prepare_figures
        from restoration_job import load_json
        rows=input_json(args.spec)
        kwargs={'engine':args.engine,'dpi':args.dpi}
        if args.reuse_batch:kwargs['reuse_batch']=args.reuse_batch
        result=prepare_figures(args.job,args.page,rows,args.output or automatic_output(args.job,args.page,'figures'),**kwargs)
    elif args.command=='figures-finalize':
        from restoration_batch import finalize_figures
        result=finalize_figures(args.job,args.page,args.output)
    elif args.command=='review-pack':
        from restoration_batch import review_pack
        from restoration_job import load_json
        result=review_pack(args.job,load_json(args.native_receipt),args.output,dpi=args.dpi)
    elif args.command in {'accept', 'revise'}:
        result = accept(args.job, args.result, replace=args.command == 'revise')
    elif args.command == 'build':
        pages = assemble(args.job)
        from restoration_batch import pages_digest
        from restoration_compiler import build_hwpx
        output = args.output.resolve()
        if output.exists() or output.with_suffix('.build.json').exists():
            raise ValueError('output_exists_choose_new_output')
        fields=json.loads(args.template_fields.read_text(encoding='utf-8')) if args.template_fields else None
        result = build_hwpx(pages, output, automation_dir=args.automation_dir,template_dir=args.template_dir,title=args.title,template_fields=fields)
        result.update({'job': str(args.job.resolve()), 'output': str(output),
                       'output_sha256': digest(output),
                       'pages_sha256': pages_digest(pages),
                       'page_count': len(pages), 'visual_status': 'not_verified'})
        output.with_suffix('.build.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    else:
        pages = assemble(args.job)
        from restoration_batch import pages_digest
        receipt = json.loads(args.build_receipt.read_text(encoding='utf-8'))
        expected = pages_digest(pages)
        if (receipt['job'] != str(args.job.resolve()) or receipt['pages_sha256'] != expected
                or digest(receipt['output']) != receipt['output_sha256']):
            return blocked_before_native(args.run_dir,'build_receipt_or_accepted_pages_changed')
        if not receipt.get('template'):
            return blocked_before_native(args.run_dir,'template_missing_rebuild_with_saved_template')
        from runtime_paths import automation_root,native_root
        automation = args.automation_dir or automation_root()
        sys.path.insert(0, str(automation / 'scripts'))
        from native_layout import render_native
        config = {'adapter_runtime':str(native_root())}
        if args.runtime_site_packages is not None:config['runtime_site_packages']=str(args.runtime_site_packages)
        # Builds of other jobs on this PC wait here for their turn instead of failing on the busy session.
        from restoration_native_queue import turn, QueueTimeout
        try:
            with turn(args.run_dir, receipt['output']):
                result = render_native(receipt['output'], args.run_dir, config=config)
        except QueueTimeout:
            return blocked_before_native(args.run_dir,'native_export_queue_timeout')
        result['job'] = str(args.job.resolve())
        result['pages_sha256'] = expected
        result['expected_source_pages'] = len(pages)
        result['visual_status'] = 'not_verified'
        if result.get('status') == 'rendered':
            from restoration_fit import check_native_fit
            if result.get('page_count') != len(pages):
                # An added page is a question pushed past its page: measure the cells anyway so the
                # question is named instead of guessed (the cell is continued on the next page).
                try:result['content_fit']=check_native_fit(receipt,result['artifacts']['hwpx']['path'])
                except Exception as exc:result['content_fit']={'status':'unavailable','error':str(exc),'issues':[]}  # diagnosis only
                result.update(status='failed', error='native_page_count_differs_from_source')
            else:
                result['content_fit']=check_native_fit(receipt,result['artifacts']['hwpx']['path'])
                if result['content_fit']['status']=='failed':
                    result.update(status='failed',error='native_content_exceeds_source_allocation')
        if result.get('status') == 'rendered':
            from restoration_batch import review_pack
            try:
                result['review_inputs'] = review_pack(args.job,result,args.run_dir/'review-inputs')
            except (ValueError,KeyError,OSError,ImportError) as exc:
                result['review_inputs'] = {'status':'failed','error':str(exc),
                    'hint':'Use review-pack with this saved native receipt and a new output directory. Visual review remains required.'}
        # Native supervisor owns actual exported paths and reopen receipts. Never synthesize success.
        args.run_dir.joinpath('restoration-native.json').write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    if args.command in {'assign','assign-many'}:
        result={**result,'handoffs':assignment_handoffs(result)}
    emit_json(result if args.full else summarize(args.command,result))
    return 0 if result.get('status') not in {'blocked', 'failed'} else 2


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, KeyError, OSError, ImportError) as exc:
        emit_json({'status': 'failed', 'error': str(exc)})
        raise SystemExit(2)
