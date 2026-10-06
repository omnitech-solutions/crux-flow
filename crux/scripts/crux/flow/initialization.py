from __future__ import annotations

from pathlib import Path

import bionic_config

from .common import FlowError, canonical, decode, digest, identity, mapping
from . import hosts, lifecycle, managed, materialize, policy, repository
from .processes import execute

INIT_RECEIPT='.crux-flow/receipts/init.json'
NATIVE={'claude','codex'}   # official project plugin activation; opencode/omp use project agent/skill/config surfaces


def initialize(plugin: Path,*,repo: Path,home: Path,host: str,scope: str,runner=execute,dry_run: bool=False,approve=None) -> dict:
    root=repo if scope=='project' else home
    receipt=lifecycle._owned_receipt(root,host,scope)
    if receipt is None: raise FlowError('init-docs requires an explicitly selected owned installed host')
    release=lifecycle._retained(home,receipt); engine=release/'crux-flow/engine'
    skill=engine/'skills/init-docs/SKILL.md'
    if managed.read_file(engine,'skills/init-docs/SKILL.md') is None: raise FlowError('installed initialization skill is absent')
    layout=bionic_config.load_config(repo,require_tree=False); docs=layout.docs_root
    if docs.exists() and any(docs.iterdir()): raise FlowError('documentation tree already contains work; initialization cannot overwrite it')
    before=digest(managed.read_file(repo,'.bionic.yml'))
    effective=policy.resolve(engine,repo,home,host); model=effective['roles']['developer']
    prompt=f'Use only the installed Crux init-docs skill at {skill}. Initialize project knowledge for {repo}. Preserve unrelated files and native permission controls. Do not install another runtime. Resolve the documentation root through the installed Bionic resolver.'
    command,env=hosts.turn(host,receipt['executable'],repo=repo,home=home,model=model['model'],effort=model['effort'],prompt=prompt,
                           host_home=Path(receipt['host_home']) if receipt.get('host_home') else None)
    public={'state':'planned','operation':'init-docs','host':host,'repo':str(repo),'docs_root':str(docs),'skill':str(skill),
            'configuration_digest':before,'release_digest':receipt['release_digest'],'command':command,'paid_execution':'requires explicit authorization'}
    public['plan_digest']=identity(public)
    if dry_run: return public
    if approve is None or not approve(public): raise FlowError('native initialization was not authorized')
    if digest(managed.read_file(repo,'.bionic.yml'))!=before or (docs.exists() and any(docs.iterdir())):
        raise FlowError('initialization inputs changed after planning')
    outcome=runner(command,cwd=repo,env=env,input_bytes=prompt.encode() if host in {'claude','codex'} else b'',timeout=min(3600, effective['budget_minutes']*60) if effective['budget_minutes'] is not None else 3600,limit=2_000_000)
    observed=False
    try:
        current=bionic_config.load_config(repo,require_tree=True)
        manifest=mapping(decode(managed.read_file(current.docs_root,'manifest.yml') or b''))
        observed=(str(manifest.get('schema_version'))=='5' and all(managed.read_file(current.docs_root,name) is not None for name in ('AGENTS.md','index.md','log.md','objectives.md')))
    except (FlowError,OSError,ValueError,bionic_config.BionicConfigError): pass
    return {'state':'initialized' if outcome.status=='ok' and observed else 'failed','host':host,'execution':outcome.public(),
            'output_observed':observed,'docs_root':str(docs),'runtime_loaded':'unobserved','partial_output':'preserved; never automatically deleted'}


def _owned(repo: Path,home: Path,host: str) -> tuple[str,dict] | None:
    for scope,root in (('project',repo),('user',home)):
        receipt=lifecycle._owned_receipt(root,host,scope)
        if receipt is not None: return scope,receipt
    return None


def initialize_any(plugin: Path,*,repo: Path,home: Path,selected: dict[str,str],runner=execute,dry_run: bool=False,approve=None) -> dict:
    """init-docs without naming a host: one native turn on the first detected host that has an owned Flow installation."""
    for host in selected:
        found=_owned(repo,home,host)
        if found is None: continue
        scope,_=found
        return {**initialize(plugin,repo=repo,home=home,host=host,scope=scope,runner=runner,dry_run=dry_run,approve=approve),
                'hosts_considered':list(selected)}
    raise FlowError('init-docs requires a detected host with an owned Flow installation; run crux-flow init first')


def _plan_native(plugin: Path,host: str,exe: str,*,repo: Path,home: Path,runner,invocation: dict | None) -> dict:
    receipt=lifecycle._owned_receipt(home,host,'user')
    if receipt is None: raise FlowError('Flow is not installed for this host; run crux-flow setup first')
    host_home=Path(receipt['host_home']) if receipt.get('host_home') else None
    engine=lifecycle._retained(home,receipt)/'crux-flow/engine'
    rows=lifecycle.inventory(host,exe,cwd=home,home=home,runner=runner,host_home=host_home)
    flow,upstream=repository.select([i for row in rows if (i:=lifecycle._identifier(row))])
    effective=policy.resolve(engine,repo,home,host,invocation)
    _,projection,report=materialize.updates(engine,effective,repo=repo,home=home,scope='project',host_home=host_home,engine=engine)
    activation,_=repository.plan(host,repo,flow,upstream)
    plan=managed.plan(repo,{**projection,**activation},owner='repository-init')
    public={**plan.public(),'activation':{'enable':flow,'disable':upstream,'file':repository.FILES[host]},'unsupported_controls':report['unsupported_controls']}
    return {'kind':'native','plan':plan,'public':public,'flow':flow,'upstream':upstream,'host_home':host_home,'exe':exe}


def _verify_native(host: str,item: dict,*,repo: Path,home: Path,runner) -> dict:
    rows={lifecycle._identifier(r):r.get('enabled') for r in lifecycle.inventory(host,item['exe'],cwd=repo,home=home,runner=runner,host_home=item['host_home'])}
    effective=rows.get(item['flow']) is True and all(rows.get(i) is not True for i in item['upstream'])
    trust=repository.trusted(hosts.paths(host,'user',repo=repo,home=home,host_home=item['host_home'])['base'],repo) if host=='codex' else None
    report={'enabled':item['flow'],'disabled_upstream':item['upstream'],'effective_in_repository':effective}
    if host=='codex': report['trusted_project']=trust
    return report


def _plan_project(plugin: Path,host: str,exe: str,*,repo: Path,home: Path,runner,source: Path) -> dict:
    plan=lifecycle.plan_install(plugin,source,repo=repo,home=home,host=host,scope='project',executable=exe,runner=runner)
    return {'kind':'project','install':plan,'public':plan.public(),'exe':exe}


def init_repository(plugin: Path,*,repo: Path,home: Path,selected: dict[str,str],skipped: dict[str,str],runner=execute,dry_run: bool=False,
                    approve=None,docs: bool=True,mode: str | None=None,source: Path | None=None,host_home: Path | None=None) -> dict:
    """Make this repository a Flow repository on every selected host, using each host's official seam."""
    if mode is not None and mode not in {'aggressive','balanced','thorough','upstream'}: raise FlowError('unsupported mode')
    config=managed.read_file(repo,policy.CONFIG); config_changes={}; config_report={'file':policy.CONFIG,'status':'already-correct'}
    invocation=None
    if config is None:
        wanted=mode or 'aggressive'; proposal=f'config_version: "1"\nmode: {wanted}\n'.encode()
        config_changes={policy.CONFIG:proposal,INIT_RECEIPT:canonical({'schema_version':1,'config_created':digest(proposal)})}
        config_report['status']='configured'; invocation={'mode':wanted}
    else:
        policy.read_config(repo/policy.CONFIG)
        if mode is not None: config_report['note']='existing project configuration retained; use crux-flow mode set to change it'
    config_plan=managed.plan(repo,config_changes,owner='repository-config') if config_changes else None
    effective_mode=policy.resolve(plugin,repo,home,'codex',invocation)['mode']
    plans={}; failed={}
    with lifecycle.install_source(plugin,source) as built:
        for host,exe in selected.items():
            try:
                if host in NATIVE:
                    receipt=lifecycle._owned_receipt(home,host,'user')
                    selected_home=host_home or (Path(receipt['host_home']) if receipt and receipt.get('host_home') else None)
                    if receipt and host_home is not None and str(host_home)!=receipt.get('host_home'):
                        raise FlowError('requested host home differs from the owned installation')
                    state=lifecycle._native_state(host,exe,repo,home,runner,selected_home,cwd=home)
                    market=lifecycle.marketplace_root(host,exe,repo=repo,home=home,runner=runner,host_home=selected_home)
                    if receipt and state['installed'] and state['statically_valid'] and market is not None:
                        plans[host]=_plan_native(plugin,host,exe,repo=repo,home=home,runner=runner,invocation=invocation)
                        installation={'state':'skipped-already-installed',
                                      'reason':'native host inventories report the marketplace registered and the plugin installed; no installation commands needed',
                                      'marketplace':{'name':'crux-flow','status':'already-registered','path':market},
                                      'plugin':{'id':'crux-flow@crux-flow','status':'already-installed','path':state['install_path'],'version':state.get('version')},
                                      'checks':[[exe,'plugin','marketplace','list','--json'],[exe,'plugin','list','--json']],
                                      'commands':[]}
                        plans[host]['installation']=installation
                        plans[host]['public']['installation']=installation
                    else:
                        install_source=built
                        if source is None:
                            if receipt: install_source=lifecycle._retained(home,receipt)
                            elif market is not None: install_source=Path(market)
                        install=lifecycle.plan_install(plugin,install_source,repo=repo,home=home,host=host,scope='user',executable=exe,runner=runner,host_home=selected_home)
                        plans[host]={'kind':'native-install','install':install,'exe':exe,
                                     'public':{'installation':install.public(),'activation':'configure repository after successful installation'}}
                else:
                    if config_plan is not None:
                        # Project hosts fingerprint the project configuration, so they are planned (and digest-checked)
                        # immediately after it is written; the displayed plan states that intent.
                        plans[host]={'kind':'project','deferred':True,'exe':exe,'public':{'operation':'install','host':host,'scope':'project',
                                     'plan':'computed from the project configuration once it is written'}}
                    else: plans[host]=_plan_project(plugin,host,exe,repo=repo,home=home,runner=runner,source=built)
            except (FlowError,OSError) as exc:
                failed[host]=str(exc) if isinstance(exc,FlowError) else 'host could not be planned'
        public={'operation':'init','repo':str(repo),'config':{**config_report,**(config_plan.public() if config_plan else {})},
                'hosts':{h:p['public'] for h,p in plans.items()},'failed':failed,'skipped':skipped,'docs':'initialize if the documentation tree is absent' if docs else 'not requested'}
        public['plan_digest']=identity(public)
        if dry_run: return {'status':'planned',**public}
        if failed and not plans: return _summary({},failed,skipped,config_report,{'status':'not-attempted'},effective_mode)
        if not plans and config_plan is None: return _summary({},{},skipped,config_report,{'status':'not-attempted'},effective_mode)
        if _has_work(config_plan,plans,docs,repo) and (approve is None or not approve(public)): raise FlowError('operation cancelled')
        if config_plan is not None: managed.apply(config_plan,expected_digest=config_plan.plan_digest)
        results={}; applied_project=False
        for host,item in plans.items():
            try:
                installation=item.get('installation')
                if item['kind']=='native-install':
                    # The approved project configuration and earlier hosts can change
                    # shared installation inputs; use the same replan/apply service as install.
                    install=lifecycle.replan(item['install'],plugin,runner=runner)
                    if install.source_digest!=item['install'].source_digest or install.before_native!=item['install'].before_native or install.before_marketplace!=item['install'].before_marketplace:
                        raise FlowError('native installation inputs changed after init approval')
                    installation=lifecycle.apply_install(install,runner=runner,expected_digest=install.public()['plan_digest'])
                    if installation['state'] not in {'installed','no-op'}:
                        results[host]={'status':'failed','reason':'native plugin installation failed','installation':installation}
                        continue
                    item=_plan_native(plugin,host,item['exe'],repo=repo,home=home,runner=runner,invocation=invocation)
                if item['kind']=='native':
                    changed=bool(item['plan'].changes)
                    if changed: managed.apply(item['plan'],expected_digest=item['plan'].plan_digest)
                    seen=_verify_native(host,item,repo=repo,home=home,runner=runner)
                    ok=seen['effective_in_repository'] or (host=='codex' and seen.get('trusted_project') is False)
                    results[host]={'status':('configured' if changed else 'already-correct') if ok else 'failed',**seen,
                                   'requires_restart':changed,'runtime_loaded':'unobserved'}
                    if installation is not None: results[host]['installation']=installation
                    if not ok: results[host]['reason']='host did not report the repository activation'
                    elif seen.get('trusted_project') is False and not seen['effective_in_repository']:
                        results[host]['attention']='Codex ignores project plugin settings until this repository is trusted'
                else:
                    if item.get('deferred'): item['install']=lifecycle.plan_install(plugin,built,repo=repo,home=home,host=host,scope='project',executable=item['exe'],runner=runner)
                    elif applied_project: item['install']=lifecycle.replan(item['install'],plugin,runner=runner)
                    applied_project=True
                    outcome=lifecycle.apply_install(item['install'],runner=runner,expected_digest=item['install'].public()['plan_digest'])
                    good=outcome['state'] in {'installed','no-op'}
                    results[host]={'status':('already-correct' if outcome['state']=='no-op' else 'configured') if good else 'failed',
                                   'requires_restart':outcome['state']=='installed','runtime_loaded':'unobserved',
                                   **({} if good else {'reason':outcome['state']})}
            except (FlowError,OSError) as exc:
                results[host]={'status':'failed','reason':str(exc) if isinstance(exc,FlowError) else 'host configuration failed; inspect the scoped transaction journal'}
        for host,reason in failed.items(): results[host]={'status':'failed','reason':reason}
        docs_report=_docs(plugin,repo,home,selected,results,runner,approve,docs)
        return _summary(results,{},skipped,config_report,docs_report,effective_mode)


def _has_work(config_plan,plans: dict,docs: bool,repo: Path) -> bool:
    """False when every host is already correct: nothing would be written, so there is nothing to approve."""
    if config_plan is not None: return True
    for item in plans.values():
        if item['kind'] in {'native-install'} or item.get('deferred'): return True
        if item['kind']=='native' and (item['plan'].changes or item.get('installation',{}).get('commands')): return True
        if item['kind']=='project' and not item['install'].no_op: return True
    if docs:
        root=bionic_config.load_config(repo,require_tree=False).docs_root
        if not (root.exists() and any(root.iterdir())): return True
    return False


def _docs(plugin,repo,home,selected,results,runner,approve,wanted) -> dict:
    if not wanted: return {'status':'not-requested'}
    try:
        layout=bionic_config.load_config(repo,require_tree=False)
        if layout.docs_root.exists() and any(layout.docs_root.iterdir()): return {'status':'already-initialized','docs_root':str(layout.docs_root)}
        ready={h:e for h,e in selected.items() if results.get(h,{}).get('status') in {'configured','already-correct'}}
        if not ready: return {'status':'skipped','reason':'no configured host available to run the initialization skill'}
        outcome=initialize_any(plugin,repo=repo,home=home,selected=ready,runner=runner,approve=approve)
        return {'status':'initialized' if outcome['state']=='initialized' else 'failed','host':outcome['host'],'docs_root':outcome['docs_root']}
    except (FlowError,OSError) as exc:
        return {'status':'failed','reason':str(exc) if isinstance(exc,FlowError) else 'documentation initialization failed'}


def _summary(results: dict,failed: dict,skipped: dict,config: dict,docs: dict,mode) -> dict:
    hosts_report={h:{'status':'skipped-not-installed','reason':r} for h,r in skipped.items()}
    hosts_report.update(results)
    for h,reason in failed.items(): hosts_report[h]={'status':'failed','reason':reason}
    bad=[h for h,r in hosts_report.items() if r['status']=='failed']
    good=[h for h,r in hosts_report.items() if r['status'] in {'configured','already-correct'}]
    state='completed' if not bad and docs.get('status')!='failed' else 'partial' if good else 'failed'
    return {'status':state,'mode':mode,'config':config,'docs':docs,'hosts':dict(sorted(hosts_report.items(),key=lambda kv:hosts.HOSTS.index(kv[0]))),
            'runtime_loaded':'unobserved'}


def deinit_repository(plugin: Path,*,repo: Path,home: Path,selected: dict[str,str],skipped: dict[str,str],runner=execute,dry_run: bool=False,approve=None) -> dict:
    """Remove only Flow-owned repository activation and projection. Project knowledge (bionic/) is never touched."""
    plans={}; results={h:{'status':'skipped-not-installed','reason':r} for h,r in skipped.items()}
    for host,exe in selected.items():
        try:
            if host in NATIVE:
                changes={}
                if managed.read_file(repo,repository.receipt_name(host)) is not None: changes.update(repository.removal(host,repo))
                if managed.read_file(repo,f'.crux-flow/receipts/{host}-project-projection.json') is not None:
                    host_home=None; changes.update(materialize.removal(repo,host,'project',repo=repo,home=home,host_home=host_home))
                if not changes: results[host]={'status':'already-correct','reason':'repository is not initialized for this host'}; continue
                plans[host]={'kind':'native','plan':managed.plan(repo,changes,owner='repository-deinit')}
            else:
                if lifecycle._owned_receipt(repo,host,'project') is None: results[host]={'status':'already-correct','reason':'repository is not initialized for this host'}; continue
                plans[host]={'kind':'project','plan':lifecycle.uninstall(repo=repo,home=home,host=host,scope='project',authorized=False,dry_run=True)}
        except (FlowError,OSError) as exc:
            results[host]={'status':'failed','reason':str(exc) if isinstance(exc,FlowError) else 'host could not be planned'}
    public={'operation':'deinit','repo':str(repo),'hosts':{h:(p['plan'].public() if p['kind']=='native' else p['plan']) for h,p in plans.items()},
            'preserves':'project knowledge, user installations and unrelated settings'}
    public['plan_digest']=identity(public)
    if dry_run: return {'status':'planned',**public,'hosts_unplanned':results}
    if plans and (approve is None or not approve(public)): raise FlowError('operation cancelled')
    for host,item in plans.items():
        try:
            if item['kind']=='native':
                managed.apply(item['plan'],expected_digest=item['plan'].plan_digest); results[host]={'status':'removed','requires_restart':True}
            else:
                outcome=lifecycle.uninstall(repo=repo,home=home,host=host,scope='project',authorized=True,runner=runner,expected_plan_digest=identity(item['plan']))
                results[host]={'status':'removed' if outcome['state']=='uninstalled' else 'failed','requires_restart':True,**({} if outcome['state']=='uninstalled' else {'reason':outcome['state']})}
        except (FlowError,OSError) as exc:
            results[host]={'status':'failed','reason':str(exc) if isinstance(exc,FlowError) else 'host deactivation failed; inspect the scoped transaction journal'}
    config=_release_config(repo,results)
    bad=any(r['status']=='failed' for r in results.values())
    return {'status':'partial' if bad else 'completed','config':config,'hosts':dict(sorted(results.items(),key=lambda kv:hosts.HOSTS.index(kv[0]))),'runtime_loaded':'unobserved'}


def _release_config(repo: Path,results: dict) -> dict:
    """Remove the project configuration only if init created it, it is unchanged, and no host activation remains."""
    raw=managed.read_file(repo,INIT_RECEIPT)
    if raw is None: return {'file':policy.CONFIG,'status':'retained','reason':'not created by Flow init'}
    if any(r['status']=='failed' for r in results.values()) or any(managed.read_file(repo,repository.receipt_name(h)) is not None for h in NATIVE) or any(
            lifecycle._owned_receipt(repo,h,'project') is not None for h in hosts.HOSTS):
        return {'file':policy.CONFIG,'status':'retained','reason':'another host activation remains'}
    receipt=mapping(decode(raw)); current=managed.read_file(repo,policy.CONFIG)
    if current is not None and digest(current)!=receipt['config_created']:
        managed.apply(managed.plan(repo,{INIT_RECEIPT:None},owner='repository-deinit'))
        return {'file':policy.CONFIG,'status':'retained','reason':'edited after init'}
    managed.apply(managed.plan(repo,{INIT_RECEIPT:None,**({policy.CONFIG:None} if current is not None else {})},owner='repository-deinit'))
    return {'file':policy.CONFIG,'status':'removed'}
