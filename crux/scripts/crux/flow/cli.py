from __future__ import annotations

import argparse
from dataclasses import asdict,is_dataclass
import json
import os
from pathlib import Path
import shutil
import sys

from .common import FlowError, PLUGIN, canonical, decode, digest, identity, mapping
from . import hosts, managed, policy
from .processes import execute


def _mutation(parser):
    parser.add_argument('--yes',action='store_true')
    parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--plan-digest')


def parser() -> argparse.ArgumentParser:
    root=argparse.ArgumentParser(prog='crux-flow')
    root.add_argument('--repo',type=Path,default=Path.cwd())
    root.add_argument('--home',type=Path,default=Path.home())
    sub=root.add_subparsers(dest='command',required=True)
    for name in ('status','doctor'):
        cmd=sub.add_parser(name); cmd.add_argument('--host',choices=(*hosts.HOSTS,'all'),default='all'); cmd.add_argument('--json',action='store_true'); cmd.add_argument('--strict',action='store_true')
    cmd=sub.add_parser('setup'); cmd.add_argument('--host',choices=(*hosts.HOSTS,'all'),default='all'); _mutation(cmd)
    cmd=sub.add_parser('package'); cmd.add_argument('--output',type=Path)
    cmd=sub.add_parser('publish'); cmd.add_argument('--to',default=os.environ.get('CRUX_FLOW_MARKETPLACE_REPO'),help='distribution Git URL or path (or CRUX_FLOW_MARKETPLACE_REPO)')
    cmd.add_argument('--branch',default='main'); cmd.add_argument('--allow-dirty',action='store_true'); cmd.add_argument('--no-github-release',action='store_true'); _mutation(cmd)
    for name in ('install','upgrade'):
        cmd=sub.add_parser(name); cmd.add_argument('--host',choices=(*hosts.HOSTS,'all'),default='all'); cmd.add_argument('--scope',choices=('user','project'),default='user'); cmd.add_argument('--source',type=Path,required=True)
        cmd.add_argument('--host-home',type=Path); _mutation(cmd)
    for name in ('uninstall','rollback'):
        cmd=sub.add_parser(name); cmd.add_argument('--host',choices=(*hosts.HOSTS,'all'),default='all'); cmd.add_argument('--scope',choices=('user','project'),default='user'); _mutation(cmd)
    cmd=sub.add_parser('init'); cmd.add_argument('--host',choices=(*hosts.HOSTS,'all'),default='all'); cmd.add_argument('--mode',choices=('aggressive','balanced','thorough','upstream')); cmd.add_argument('--no-docs',action='store_true'); _mutation(cmd)
    cmd=sub.add_parser('deinit'); cmd.add_argument('--host',choices=(*hosts.HOSTS,'all'),default='all'); _mutation(cmd)
    cmd=sub.add_parser('init-docs'); cmd.add_argument('--host',choices=(*hosts.HOSTS,'all'),default='all'); cmd.add_argument('--scope',choices=('user','project'),default=None); _mutation(cmd)
    mode=sub.add_parser('mode'); modes=mode.add_subparsers(dest='action',required=True); modes.add_parser('list')
    cmd=modes.add_parser('show'); cmd.add_argument('--resolved',action='store_true'); cmd.add_argument('--host',choices=(*hosts.HOSTS,'all'),default='all'); cmd.add_argument('--json',action='store_true')
    cmd=modes.add_parser('set'); cmd.add_argument('mode',choices=('aggressive','rapid','balanced','thorough','upstream')); cmd.add_argument('--scope',choices=('project','user','run'),default='project'); cmd.add_argument('--file',type=Path); cmd.add_argument('--reason'); cmd.add_argument('--budget-minutes',type=int); _mutation(cmd)
    cmd=modes.add_parser('materialize'); cmd.add_argument('--host',choices=(*hosts.HOSTS,'all'),default='all'); cmd.add_argument('--scope',choices=('project','user'),default='project'); _mutation(cmd)
    runs=sub.add_parser('run'); rs=runs.add_subparsers(dest='action',required=True)
    cmd=rs.add_parser('start'); cmd.add_argument('--host',choices=hosts.HOSTS,default='codex'); cmd.add_argument('--spec',type=Path,required=True); cmd.add_argument('--mode',choices=('aggressive','rapid','balanced','thorough','upstream')); cmd.add_argument('--previous-run',type=Path); cmd.add_argument('--owner-approved',action='store_true'); cmd.add_argument('--reason')
    for name in ('status','resume','history','advance','check','checkpoint','attempt','finding','resolve-finding','review','repair-review','transition','stop','supersede','drive','call-api','council'):
        cmd=rs.add_parser(name); cmd.add_argument('--file',type=Path,required=True); cmd.add_argument('--json',action='store_true')
        if name=='advance': cmd.add_argument('--outcome',choices=('done','skipped','blocked'),default='done'); cmd.add_argument('--result',required=True); cmd.add_argument('--artifact',action='append',default=[])
        if name=='check': cmd.add_argument('--label',required=True); cmd.add_argument('--argv-json')
        if name=='checkpoint': cmd.add_argument('--next-action',required=True); cmd.add_argument('--phase',default='implementation')
        if name=='attempt': cmd.add_argument('--kind',choices=('implementation','delegate','review','review-repair','council','test','generation','bookkeeping'),required=True); cmd.add_argument('--invocation',required=True); cmd.add_argument('--justification',default='')
        if name=='finding': cmd.add_argument('--description',required=True); cmd.add_argument('--dependency',choices=('blocking-now','blocking-finalization','non-blocking'),required=True)
        if name=='resolve-finding': cmd.add_argument('--id',required=True); cmd.add_argument('--evidence',required=True)
        if name in {'review','repair-review'}:
            cmd.add_argument('--report',type=Path,required=True)
        if name in {'transition','supersede'}: cmd.add_argument('--reason',required=True); cmd.add_argument('--owner-approved',action='store_true')
        if name=='transition': cmd.add_argument('--mode',choices=('aggressive','rapid','balanced','thorough')); cmd.add_argument('--budget-minutes',type=int)
        if name=='stop': cmd.add_argument('--status',choices=('BLOCKED','BUDGET_EXHAUSTED','CANCELLED'),required=True); cmd.add_argument('--reason',required=True)
        if name in {'drive','call-api','council'}: cmd.add_argument('--yes',action='store_true')
        if name in {'call-api','council'}: cmd.add_argument('--prompt-file',type=Path,required=True)
        if name=='call-api': cmd.add_argument('--role',required=True)
        if name=='council': cmd.add_argument('--invocation',required=True); cmd.add_argument('--justification',required=True)
    model=sub.add_parser('models'); ms=model.add_subparsers(dest='action',required=True)
    cmd=ms.add_parser('list'); cmd.add_argument('--host',choices=(*hosts.HOSTS,'api','all'),default='all'); cmd.add_argument('--json',action='store_true')
    cmd=ms.add_parser('resolve'); cmd.add_argument('--host',choices=(*hosts.HOSTS,'api','all'),default='all'); cmd.add_argument('--mode'); cmd.add_argument('--role'); cmd.add_argument('--json',action='store_true')
    cmd=ms.add_parser('refresh'); cmd.add_argument('--host',choices=('openrouter','codex','claude'),default='openrouter'); cmd.add_argument('--output',type=Path,required=True); cmd.add_argument('--changes',type=Path); cmd.add_argument('--scope',choices=('source','user','project'),default='project'); cmd.add_argument('--require',action='append',default=[])
    cmd=ms.add_parser('apply'); cmd.add_argument('--proposal',type=Path,required=True); cmd.add_argument('--unattended-policy',type=Path); _mutation(cmd)
    cmd=ms.add_parser('rollback'); cmd.add_argument('--scope',choices=('source','user','project'),default='project'); cmd.add_argument('--transaction',required=True); _mutation(cmd)
    up=sub.add_parser('upstream'); us=up.add_subparsers(dest='action',required=True)
    cmd=us.add_parser('check'); cmd.add_argument('--repository')
    cmd=us.add_parser('prepare'); cmd.add_argument('--repository'); cmd.add_argument('--ref',required=True); cmd.add_argument('--output',type=Path,required=True)
    cmd=us.add_parser('verify'); cmd.add_argument('--candidate',type=Path,required=True); cmd.add_argument('--full',action='store_true'); cmd.add_argument('--test-seconds',type=int,default=600)
    transactions = sub.add_parser('transactions')
    ts = transactions.add_subparsers(dest='action', required=True)
    for name in ('inspect', 'recover', 'rollback'):
        cmd = ts.add_parser(name)
        cmd.add_argument('--transaction', required=True)
        cmd.add_argument('--scope', choices=('project', 'user', 'source'), default='project')
        _mutation(cmd)
    sub.add_parser('guide')
    return root


def _read(path: Path):
    raw=managed.read_file(path.absolute().parent,path.name)
    if raw is None: raise FlowError('requested input file is missing')
    return decode(raw)


def _approve(plan,args,confirm=None) -> bool:
    expected=getattr(args,'plan_digest',None)
    actual=plan.get('plan_digest',identity(plan))
    if expected is not None and expected!=actual: raise FlowError('displayed plan no longer matches current inputs')
    if getattr(args,'yes',False): return True
    if confirm is not None: return confirm(plan) is True
    if not sys.stdin.isatty(): raise FlowError('noninteractive writes require --yes for the displayed operation')
    print(json.dumps(plan,indent=2),file=sys.stderr)
    return input('Apply this plan? [y/N] ').strip().lower()=='y'


def _selected(host: str,executables: dict[str,str] | None) -> tuple[dict[str,str],dict[str,str]]:
    available=hosts.detect(executables)
    chosen=hosts.HOSTS if host=='all' else [host]
    found={h:available[h] for h in chosen if h in available}
    missing={h:'not installed' for h in chosen if h not in available}
    if host!='all' and missing: raise FlowError('requested host executable is absent')
    return found,missing


def _across(args,func,found,skipped,runner,confirm,*,done: set[str],root_for) -> tuple[dict,int]:
    """One displayed plan, then each detected host independently; one host failing never blocks another."""
    kwargs={'repo':args.repo,'home':args.home,'scope':args.scope,'runner':runner}
    plans={}; results={h:{'status':'skipped-not-installed','reason':r} for h,r in skipped.items()}
    for host in found:
        if lifecycle_owned(root_for(host),host,args.scope) is None: results[host]={'status':'skipped','reason':'no Flow installation in this scope'}; continue
        try: plans[host]=func(**kwargs,host=host,authorized=False,dry_run=True)
        except (FlowError,OSError) as exc: results[host]={'status':'failed','reason':str(exc) if isinstance(exc,FlowError) else 'host could not be planned'}
    public={'operation':args.command,'scope':args.scope,'hosts':plans}
    if args.dry_run: return {'state':'planned',**public,'skipped':results},0
    if plans and not _approve(public,args,confirm): raise FlowError('operation cancelled')
    for host,plan in plans.items():
        try:
            outcome=func(**kwargs,host=host,authorized=True,expected_plan_digest=identity(plan))
            results[host]={'status':'completed' if outcome['state'] in done else 'failed',**outcome}
        except (FlowError,OSError) as exc: results[host]={'status':'failed','reason':str(exc) if isinstance(exc,FlowError) else 'host operation failed; inspect the scoped transaction journal'}
    bad=any(r['status']=='failed' for r in results.values())
    return {'status':'partial' if bad else 'completed','hosts':dict(sorted(results.items(),key=lambda kv:hosts.HOSTS.index(kv[0])))},2 if bad else 0


def lifecycle_owned(root,host,scope):
    from . import lifecycle
    return lifecycle._owned_receipt(root,host,scope)


def _run(args,plugin,runner,executables):
    from . import records,workflow,history,continuation,evidence,api_calls
    action=args.action
    if action=='start':
        data=mapping(_read(args.spec),allowed={'goal','outcomes','checks','change_kind','replaces'},required={'goal','outcomes','checks'})
        return workflow.start(plugin,args.repo,args.home,args.host,**data,invocation={'mode':args.mode} if args.mode else None,
            previous_run=str(args.previous_run) if args.previous_run else None,owner_authorized=args.owner_approved,predecessor_reason=args.reason,spec_path=args.spec)
    path=args.file
    if action=='status': return records.status(path)
    if action=='resume': return continuation.resume(path)
    if action=='history': return history.inspect(path)
    if action=='advance': return records.advance_file(path,outcome=args.outcome,result=args.result,artifacts=args.artifact)
    if action=='check': return evidence.run_check(path,args.label,json.loads(args.argv_json) if args.argv_json else None,runner=runner)
    if action=='checkpoint': return records.checkpoint(path,next_action=args.next_action,phase=args.phase)
    if action=='attempt': return records.attempt(path,kind=args.kind,invocation=args.invocation,justification=args.justification)
    if action=='finding': return records.finding(path,args.description,dependency=args.dependency)
    if action=='resolve-finding': return records.resolve_finding(path,args.id,evidence=args.evidence)
    if action in {'review','repair-review'}:
        data=mapping(_read(args.report),allowed={'reviewer','evidence','findings','independent'},required={'reviewer','evidence','findings','independent'})
        return records.record_review(path,**data,repair=action=='repair-review')
    if action=='transition': return records.transition(path,reason=args.reason,owner_authorized=args.owner_approved,mode=args.mode,budget_minutes=args.budget_minutes)
    if action=='stop': return records.stop(path,args.status,reason=args.reason)
    if action=='supersede': return records.supersede_unit(path,reason=args.reason,owner_authorized=args.owner_approved)
    if action=='drive':
        host=records.load(path)['flow']['effective_policy']['host']
        return continuation.drive(path,home=args.home,authorized=args.yes,executable=(executables or {}).get(host),runner=runner)
    raw=managed.read_file(args.prompt_file.absolute().parent,args.prompt_file.name)
    if raw is None: raise FlowError('explicit API prompt file missing')
    if action=='call-api': return {'result':api_calls.call_role(path,args.role,raw.decode(),authorized=args.yes)}
    result=api_calls.deliberate(path,raw.decode(),invocation=args.invocation,justification=args.justification,authorized=args.yes)
    return asdict(result) if is_dataclass(result) else result


def main(argv: list[str] | None=None,*,plugin: Path=PLUGIN,runner=execute,executables: dict[str,str] | None=None,confirm=None,fetch=None) -> int:
    args=parser().parse_args(argv); args.repo=args.repo.resolve(); args.home=args.home.resolve()
    code=0
    try:
        from . import lifecycle,materialize,models,packaging
        if args.command in {'status','doctor'}:
            available=hosts.detect(executables)
            chosen=hosts.HOSTS if args.host=='all' else [args.host]
            reports={h:lifecycle.status(repo=args.repo,home=args.home,host=h,executable=available.get(h),runner=runner) if h in available else
                     {'host':h,'present':False,'installed':False,'statically_valid':False,'runtime_loaded':'unobserved'} for h in chosen}
            result={'hosts':reports}
            if args.command=='doctor' and args.strict and any(not r['statically_valid'] for h,r in reports.items() if args.host!='all' or h in available): code=2
            if args.command=='doctor' and args.strict and not available: code=2
        elif args.command=='publish':
            from . import publishing
            if not args.to: raise FlowError('publish needs --to <git url or path> or CRUX_FLOW_MARKETPLACE_REPO')
            result=publishing.publish(plugin,target=args.to,branch=args.branch,runner=runner,dry_run=args.dry_run,approve=lambda p:_approve(p,args,confirm),
                                      allow_dirty=args.allow_dirty,github_release=False if args.no_github_release else None,
                                      gh=(executables or {}).get('gh') if executables is not None else None)
            if result['status'] in {'unverified'} or result.get('github_release',{}).get('status')=='failed': code=2
        elif args.command=='package': result=packaging.build(plugin,args.output) if args.output is not None else packaging.build_default(plugin,args.repo/'.cache/crux-flow-releases')
        elif args.command=='setup':
            from .setup import setup
            selected,skipped=_selected(args.host,executables)
            result=setup(plugin,repo=args.repo,home=args.home,selected=selected,runner=runner,dry_run=args.dry_run,approve=lambda p:_approve(p,args,confirm))
            result['skipped']=skipped
            if result['state']=='partial': code=2
        elif args.command in {'install','upgrade'}:
            selected,skipped=_selected(args.host,executables); plans={}; outcomes={}
            for h,exe in selected.items():
                if h=='codex' and args.scope=='project' and args.host=='all': skipped[h]='native registration is user-scoped; use separate project role materialization'; continue
                plan=lifecycle.plan_install(plugin,args.source,repo=args.repo,home=args.home,host=h,scope=args.scope,executable=exe,runner=runner,host_home=args.host_home)
                if args.command=='upgrade':
                    from .upstream import semver
                    if plan.previous is None: raise FlowError('upgrade requires an owned installed release')
                    old=packaging.inspect(Path(plan.previous['release']))['release']['identity']['version']
                    new=packaging.inspect(plan.retained)['release']['identity']['version'] if plan.retained.exists() else None
                    if new is None:
                        with lifecycle.source_release(args.source) as candidate: new=candidate['release']['identity']['version']
                    if semver(new)<=semver(old): raise FlowError('upgrade requires a newer tested fork release; use install to rematerialize settings')
                plans[h]=plan
            public={'operation':args.command,'hosts':{h:p.public() for h,p in plans.items()},'skipped':skipped}
            if args.dry_run: result={'state':'planned',**public}
            else:
                if not plans: raise FlowError('no requested compatible host installation route')
                if not _approve(public,args,confirm): raise FlowError('operation cancelled')
                for index,(h,p) in enumerate(plans.items()):
                    try:
                        if index: p=lifecycle.replan(p,plugin,runner=runner)
                        outcomes[h]=lifecycle.apply_install(p,runner=runner,expected_digest=p.public()['plan_digest'])
                    except (FlowError,OSError) as exc: outcomes[h]={'state':'failed','reason':str(exc) if isinstance(exc,FlowError) else type(exc).__name__,'action':'inspect scoped transaction journal; later independent hosts remain eligible'}
                result={'hosts':outcomes,'skipped':skipped,'plan':public}
                if any(r['state'] not in {'installed','no-op'} for r in outcomes.values()): code=2
        elif args.command in {'uninstall','rollback'}:
            from . import initialization
            func=lifecycle.uninstall if args.command=='uninstall' else lifecycle.rollback_install
            root_for=lambda h: args.repo if args.scope=='project' else args.home
            if args.command=='uninstall' and args.scope=='project' and (args.host=='all' or args.host in initialization.NATIVE):
                found,skipped=_selected(args.host,executables) if args.host=='all' else ({args.host:(executables or {}).get(args.host,'')},{})
                result=initialization.deinit_repository(plugin,repo=args.repo,home=args.home,selected=found,skipped=skipped,runner=runner,dry_run=args.dry_run,approve=lambda p:_approve(p,args,confirm))
                if result['status']=='partial': code=2
            elif args.host=='all':
                found,skipped=_selected('all',executables)
                result,code=_across(args,func,found,skipped,runner,confirm,done={'uninstalled','rolled-back'},root_for=root_for)
            else:
                kwargs={'repo':args.repo,'home':args.home,'host':args.host,'scope':args.scope,'runner':runner}
                plan=func(**kwargs,authorized=False,dry_run=True)
                if args.dry_run: result=plan
                else:
                    if not _approve(plan,args,confirm): raise FlowError('operation cancelled')
                    result=func(**kwargs,authorized=True,expected_plan_digest=identity(plan))
                    if result['state'] not in {'uninstalled','rolled-back'}: code=2
        elif args.command=='init':
            from . import initialization
            found,skipped=_selected(args.host,executables)
            result=initialization.init_repository(plugin,repo=args.repo,home=args.home,selected=found,skipped=skipped,runner=runner,dry_run=args.dry_run,
                                                  approve=lambda p:_approve(p,args,confirm),docs=not args.no_docs,mode=args.mode)
            if result['status'] in {'partial','failed'}: code=2
        elif args.command=='deinit':
            from . import initialization
            found,skipped=_selected(args.host,executables) if args.host=='all' else ({args.host:(executables or {}).get(args.host,'')},{})
            result=initialization.deinit_repository(plugin,repo=args.repo,home=args.home,selected=found,skipped=skipped,runner=runner,dry_run=args.dry_run,approve=lambda p:_approve(p,args,confirm))
            if result['status']=='partial': code=2
        elif args.command=='init-docs':
            from .initialization import initialize,initialize_any
            if args.host=='all':
                found,_=_selected('all',executables)
                result=initialize_any(plugin,repo=args.repo,home=args.home,selected=found,runner=runner,dry_run=args.dry_run,approve=lambda p:_approve(p,args,confirm))
            else:
                result=initialize(plugin,repo=args.repo,home=args.home,host=args.host,scope=args.scope or 'user',runner=runner,dry_run=args.dry_run,approve=lambda p:_approve(p,args,confirm))
            if result['state']=='failed': code=2
        elif args.command=='mode':
            if args.action=='list': result={'default':'aggressive',**policy.load_definition(plugin)}
            elif args.action=='show' and args.host=='all':
                result={'hosts':{h:policy.resolve(plugin,args.repo,args.home,h) for h in (hosts.detect(executables) or hosts.HOSTS)}}
            elif args.action=='show': result=policy.resolve(plugin,args.repo,args.home,args.host)
            elif args.action=='materialize' and args.host=='all':
                found,skipped=_selected('all',executables); plans={}; results={h:{'status':'skipped-not-installed','reason':r} for h,r in skipped.items()}
                for h in found:
                    try:
                        effective=policy.resolve(plugin,args.repo,args.home,h)
                        root,updates,report=materialize.updates(plugin,effective,repo=args.repo,home=args.home,scope=args.scope)
                        plans[h]=(effective,managed.plan(root,updates,owner='mode-projection'),report)
                    except (FlowError,OSError) as exc: results[h]={'status':'failed','reason':str(exc) if isinstance(exc,FlowError) else 'host could not be planned'}
                public={'operation':'materialize','hosts':{h:p[1].public() for h,p in plans.items()}}
                if args.dry_run: result={'state':'planned',**public,'skipped':results}
                else:
                    if plans and not _approve(public,args,confirm): raise FlowError('operation cancelled')
                    for h,(effective,plan,report) in plans.items():
                        try:
                            if policy.resolve(plugin,args.repo,args.home,h)!=effective: raise FlowError('preferences changed after materialization was approved')
                            results[h]={**managed.apply(plan),**report,'status':'configured' if plan.changes else 'already-correct'}
                        except (FlowError,OSError) as exc: results[h]={'status':'failed','reason':str(exc) if isinstance(exc,FlowError) else 'materialization failed; inspect the scoped transaction journal'}
                    bad=any(r['status']=='failed' for r in results.values()); code=2 if bad else 0
                    result={'status':'partial' if bad else 'completed','hosts':dict(sorted(results.items(),key=lambda kv:hosts.HOSTS.index(kv[0])))}
            elif args.action=='materialize':
                effective=policy.resolve(plugin,args.repo,args.home,args.host)
                root,updates,report=materialize.updates(plugin,effective,repo=args.repo,home=args.home,scope=args.scope)
                plan=managed.plan(root,updates,owner='mode-projection')
                if args.dry_run: result={'plan':plan.public(),**report}
                else:
                    if not _approve(plan.public(),args,confirm): raise FlowError('operation cancelled')
                    if policy.resolve(plugin,args.repo,args.home,args.host) != effective:
                        raise FlowError('preferences changed after materialization was approved')
                    result={**managed.apply(plan),**report}
            elif args.scope=='run':
                from . import records
                if args.file is None or not args.reason: raise FlowError('run mode change needs --file and a material --reason')
                plan={'run':str(args.file),'mode':args.mode,'budget_minutes':args.budget_minutes,'reason':args.reason}
                result=plan if args.dry_run else records.transition(args.file,reason=args.reason,owner_authorized=_approve(plan,args,confirm),mode=args.mode,budget_minutes=args.budget_minutes)
            else:
                from ruamel.yaml import YAML
                import io
                root=args.repo if args.scope=='project' else args.home; raw=managed.read_file(root,policy.CONFIG)
                policy.read_config(root/policy.CONFIG)
                codec=YAML(); codec.preserve_quotes=True
                document=codec.load(raw.decode()) if raw else {'config_version':'1'}
                document['mode']='aggressive' if args.mode=='rapid' else args.mode
                if args.budget_minutes is not None: document['budget_minutes']=args.budget_minutes
                buffer=io.StringIO(); codec.dump(document,buffer)
                policy.resolve(plugin,args.repo,args.home,'codex',{'mode':document['mode'],**({'budget_minutes':args.budget_minutes} if args.budget_minutes is not None else {})})
                plan=managed.plan(root,{policy.CONFIG:buffer.getvalue().encode()},owner='mode-preference')
                if not args.dry_run:
                    if not _approve(plan.public(),args,confirm): raise FlowError('operation cancelled')
                    managed.apply(plan)
                result={'mode':document['mode'],'scope':args.scope,'activation':'not-applied' if args.dry_run else 'new runs; materialize for subsequent native delegations','plan':plan.public()}
        elif args.command=='run':
            result = _run(args, plugin, runner, executables)
            if args.action == 'check' and result.get('status') != 'passed':
                code = 2
            if args.action == 'drive' and result.get('status') != 'COMPLETED':
                code = 2
        elif args.command=='models':
            if args.action in {'list','resolve'}:
                def view(h):
                    selected=policy.resolve(plugin,args.repo,args.home,'codex' if h=='api' else h,{'mode':args.mode} if getattr(args,'mode',None) else None)
                    value=selected['api'] if h=='api' else {'roles':selected['roles'],'warnings':selected['warnings'],'mode':selected['mode'],'runtime_loaded':'unobserved'}
                    return value['roles'][args.role] if getattr(args,'role',None) and h!='api' else value
                result={'hosts':{h:view(h) for h in (hosts.detect(executables) or hosts.HOSTS)}} if args.host=='all' else view(args.host)
            elif args.action=='refresh':
                found=models.discover(args.host,executable=(executables or {}).get(args.host),runner=runner,fetch=fetch)
                proposal=models.propose(plugin,args.repo,args.home,scope=args.scope,changes=_read(args.changes),discovery=found,required_capabilities=args.require) if args.changes else found
                output=args.output.absolute(); plan=managed.plan(output.parent,{output.name:canonical(proposal)},owner='model-proposal')
                if managed.read_file(output.parent,output.name) is not None: raise FlowError('proposal output already exists; choose a new staged path')
                managed.apply(plan); result={'staged':str(output),'activation':'not-applied','proposal':proposal}
            elif args.action=='apply':
                proposal=mapping(_read(args.proposal)); display={k:v for k,v in proposal.items() if k not in {'candidate','discovery'}}
                if args.dry_run: result=display
                else:
                    unattended=_read(args.unattended_policy) if args.unattended_policy else None
                    authorized=_approve(display,args,confirm) if unattended is None else False
                    current=models.discover(proposal['discovery']['provider'],executable=(executables or {}).get(proposal['discovery']['provider']),runner=runner,fetch=fetch)
                    result=models.apply(plugin,args.repo,args.home,proposal,authorized=authorized,current_discovery=current,unattended=unattended)
            else:
                root=plugin if args.scope=='source' else args.repo if args.scope=='project' else args.home
                snapshot=managed.receipt(root,args.transaction); display={'root':str(root),'transaction':args.transaction,'state':snapshot['status']}
                result=display if args.dry_run else models.rollback(root,args.transaction) if _approve(display,args,confirm) else {'state':'cancelled'}
        elif args.command == 'transactions':
            root = plugin if args.scope == 'source' else args.home if args.scope == 'user' else args.repo
            snapshot = managed.receipt(root, args.transaction)
            display = {'transaction': args.transaction, 'root': str(root), 'status': snapshot['status'],
                       'owner': snapshot['payload']['owner'],
                       'changes': [{k: v for k, v in row.items() if k not in {'before_bytes', 'after_bytes'}}
                                   for row in snapshot['payload']['changes']]}
            if args.action == 'inspect' or args.dry_run:
                result = display
            else:
                if not _approve(display, args, confirm): raise FlowError('operation cancelled')
                if managed.receipt(root, args.transaction) != snapshot:
                    raise FlowError('transaction changed after approval')
                result = managed.rollback(root, args.transaction, recover=args.action == 'recover')
        elif args.command=='upstream':
            from . import upstream
            if args.action=='check': result=upstream.available(args.repository or policy.load_definition(plugin)['identity']['upstream_repository'])
            elif args.action=='prepare': result=upstream.prepare(args.repo,args.output,ref=args.ref,repository=args.repository,plugin=plugin)
            else: result=upstream.verify(args.candidate,full=args.full,test_seconds=args.test_seconds)
            if result.get('status') in {'conflict','refused'}: code=2
        else:
            path=plugin/'FLOW_GUIDE.md'
            if not path.exists(): path=plugin.parent/'FLOW_GUIDE.md'
            result={'guide':str(path),'content':path.read_text() if path.exists() else 'Use the version-matched reference operator guide.'}
        print(json.dumps(result,indent=2,sort_keys=True))
        return code
    except (FlowError,OSError,ValueError,KeyError,TypeError) as exc:
        message=str(exc) if isinstance(exc,FlowError) else 'Operation failed; no successful result is claimed. Inspect the scoped transaction journal and input schema.'
        print(json.dumps({'error':message,'error_type':type(exc).__name__}),file=sys.stderr)
        return 2


def compatibility(argv: list[str] | None=None) -> int:
    values=list(sys.argv[1:] if argv is None else argv)
    index=0
    while index<len(values) and values[index] in {'--repo','--home'}: index+=2
    if index>=len(values):
        if not sys.stdin.isatty(): return main([*values,'status'])
        choices=['status','doctor','install-crux-env','upgrade','init-docs','guide']
        print('\n'.join(f'{n+1}. {c}' for n,c in enumerate(choices)))
        selected=input('Command: ').strip()
        if not selected.isdecimal() or not 1<=int(selected)<=len(choices): return 2
        values.append(choices[int(selected)-1])
    if values[index]=='install-crux-env': values[index]='install'
    return main(values)
