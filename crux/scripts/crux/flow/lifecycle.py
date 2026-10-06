from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
from pathlib import Path
import re
import tempfile
import uuid
from typing import Iterator

from .common import FlowError, canonical, decode, digest, identity, mapping
from . import hosts, managed, materialize, packaging, policy, provenance, repository
from .processes import execute

STORE='.local/share/crux-flow/releases'


def receipt_name(host: str,scope: str) -> str:
    if host not in hosts.HOSTS or scope not in {'user','project'}: raise FlowError('invalid host or scope')
    return f'.crux-flow/receipts/{host}-{scope}-installation.json'


@contextmanager
def install_source(plugin: Path, source: Path | None):
    if source is not None:
        yield source
    elif (plugin/'release.json').is_file():
        # Packaged engines live at <marketplace>/<distribution>/engine.
        # Use the complete marketplace so its existing manifest is validated.
        yield plugin.parent.parent
    else:
        from . import packaging
        with tempfile.TemporaryDirectory(prefix='crux-install-') as temp:
            built=packaging.build(plugin,Path(temp)/'build')
            yield Path(built['root'])


@contextmanager
def source_release(source: Path) -> Iterator[dict]:
    if source.is_symlink(): raise FlowError('symlinked release source refused')
    if source.is_dir():
        yield packaging.inspect(source)
    else:
        with tempfile.TemporaryDirectory(prefix='crux-release-inspection-') as temp:
            root=packaging.extract(source,Path(temp)/'release')
            yield packaging.inspect(root)


def _controls(repo: Path,home: Path) -> dict:
    return {f'{scope}:{name}':digest(managed.read_file(root,name)) for scope,root in [('project',repo),('user',home)]
            for name in [policy.CONFIG,'.crux-flow/model-bindings.json']}


def inventory(host: str,exe: str,*,cwd: Path,home: Path,runner,host_home: Path | None=None) -> list[dict]:
    """Installed plugins as the host reports them from `cwd` (so project-scoped settings apply)."""
    result=runner([exe,'plugin','list','--json'],cwd=cwd,env=hosts.environment(host,home,host_home=host_home),timeout=12,limit=500_000)
    if result.status!='ok': raise FlowError('native plugin inventory could not be inspected')
    parsed=decode(result.stdout)
    items=parsed if host=='claude' else mapping(parsed).get('installed',[])
    if not isinstance(items,list): raise FlowError('invalid native plugin inventory')
    return [mapping(raw) for raw in items if mapping(raw).get('installed',True) is not False]


def _identifier(row: dict) -> str | None:
    value=row.get('id',row.get('pluginId'))
    return value if isinstance(value,str) else None


def _native_state(host: str,exe: str,repo: Path,home: Path,runner,host_home: Path | None=None,*,cwd: Path | None=None) -> dict:
    report={'installed':False,'enabled':False,'statically_valid':False,'runtime_loaded':'unobserved','upstream_active':False,'upstream_ids':[],'install_path':None}
    directory=hosts.paths(host,'user',repo=repo,home=home,host_home=host_home)['base']
    if not directory.exists(): return report
    items=inventory(host,exe,cwd=cwd or repo,home=home,runner=runner,host_home=host_home)
    for row in items:
        identifier=_identifier(row)
        if identifier is not None and identifier.partition('@')[0]=='crux':
            report['upstream_ids'].append(identifier)
            if row.get('enabled') is True: report['upstream_active']=True
        if identifier!='crux-flow@crux-flow': continue
        report['installed']=True; report['enabled']=row.get('enabled') is True
        selected=row.get('installPath',row.get('installedPath',mapping(row.get('source',{})).get('path')))
        if not isinstance(selected,str) or not selected: continue
        path=Path(selected)
        permitted=path.is_relative_to(home.resolve()) or (host_home is not None and path.is_relative_to(host_home.resolve()))
        if not path.is_absolute() or not permitted: continue
        report['install_path']=str(path)
        try:
            portable=mapping(decode(managed.read_file(path,'plugin.json') or b''))
            source=provenance.source_identity(path/'engine')
            report['statically_valid']=portable.get('name')=='crux-flow' and source['kind']=='packaged-release'
            report['runtime_digest']=source['runtime_digest']; report['version']=portable.get('version')
        except (FlowError,OSError,ValueError): pass
    return report


def marketplace_root(host: str,exe: str,*,repo: Path,home: Path,runner,host_home: Path | None=None) -> str | None:
    result=runner([exe,'plugin','marketplace','list','--json'],cwd=home,env=hosts.environment(host,home,host_home=host_home),timeout=12,limit=500_000)
    if result.status!='ok': raise FlowError('native marketplace inventory could not be inspected')
    data=decode(result.stdout)
    rows=data if host=='claude' else mapping(data).get('marketplaces',[])
    if not isinstance(rows,list): raise FlowError('invalid native marketplace inventory')
    matches=[mapping(row) for row in rows if mapping(row).get('name')=='crux-flow']
    if len(matches)>1: raise FlowError('ambiguous Flow marketplace registration')
    if not matches: return None
    row=matches[0]; root=row.get('root') if host=='codex' else row.get('path',row.get('installLocation'))
    if not isinstance(root,str) or not Path(root).is_absolute(): raise FlowError('Flow marketplace has no local absolute root')
    return root


def _commands(host: str,exe: str,scope: str,release: Path,*,replace_existing: bool=False,registered: bool=False) -> list[list[str]]:
    commands=[]
    if replace_existing:
        commands.append([exe,'plugin','uninstall' if host=='claude' else 'remove','crux-flow@crux-flow',*(['--scope',scope] if host=='claude' else [])])
        commands.append([exe,'plugin','marketplace','remove','crux-flow',*(['--scope',scope] if host=='claude' else [])])
    if replace_existing or not registered:
        commands.append([exe,'plugin','marketplace','add',str(release),*(['--scope',scope] if host=='claude' else [])])
    commands.append([exe,'plugin','install' if host=='claude' else 'add','crux-flow@crux-flow',*(['--scope',scope] if host=='claude' else [])])
    return commands


def _defer(host: str,exe: str,*,scope: str,repo: Path,home: Path,runner,host_home: Path | None=None) -> list[dict]:
    """Keep a user-scope Flow installation inert so each repository opts in through its own project setting.

    Only used while an upstream Crux plugin is enabled for the user: repositories that never run
    `crux-flow init` must keep seeing exactly one workflow plugin."""
    if scope!='user' or host not in repository.FILES: return []
    if host=='claude':
        result=runner([exe,'plugin','disable','crux-flow@crux-flow','--scope','user'],cwd=home,env=hosts.environment(host,home,host_home=host_home),timeout=45,limit=500_000)
        return [result.public()]
    base=hosts.paths(host,'user',repo=repo,home=home,host_home=host_home)['base'].resolve()
    if not base.is_relative_to(home.resolve()): raise FlowError('host configuration lies outside the selected home')
    name=(base/'config.toml').relative_to(home.resolve()).as_posix()
    updated=repository.write_setting(host,managed.read_file(home,name),'crux-flow@crux-flow',False)
    outcome=managed.apply(managed.plan(home,{name:updated},owner='user-activation')) if updated!=managed.read_file(home,name) else {}
    return [{'status':'ok','action':'set Flow inert in user Codex configuration','transaction':outcome.get('transaction_id')}]


@dataclass(frozen=True)
class InstallPlan:
    source: Path
    repo: Path
    home: Path
    host: str
    scope: str
    executable: str
    source_digest: str
    runtime_digest: str
    retained: Path
    release_plan: managed.Plan
    projection_plan: managed.Plan
    controls: dict
    before_native: dict | None
    commands: tuple[tuple[str,...],...]
    recovery_commands: tuple[tuple[str,...],...]
    previous: dict | None
    receipt: dict
    no_op: bool
    host_home: Path | None
    deferred: bool = False
    before_marketplace: str | None = None

    def public(self) -> dict:
        data={'operation':'install','source':str(self.source),'source_digest':self.source_digest,'host':self.host,'scope':self.scope,
              'retained_release':str(self.retained),'release_plan':self.release_plan.public(),
              'projection_plan':self.projection_plan.public(),'controls':self.controls,'before_native':self.before_native,
              'commands':[list(c) for c in self.commands],'recovery_commands':[list(c) for c in self.recovery_commands], 'before_marketplace':self.before_marketplace,
              'no_op':self.no_op,'user_activation':'deferred' if self.deferred else 'active','runtime_loaded':'unobserved','atomicity':'recoverable scoped files; native registration is a separate boundary'}
        data['plan_digest']=identity(data)
        return data


def _owned_receipt(root: Path,host: str,scope: str) -> dict | None:
    raw=managed.read_file(root,receipt_name(host,scope))
    if raw is None: return None
    row=mapping(decode(raw),required={'schema_version','host','scope','release','release_digest','policy_digest','state','executable'})
    if row['schema_version']!=1 or row['host']!=host or row['scope']!=scope: raise FlowError('installation receipt is not owned by selected scope')
    if not re.fullmatch('[0-9a-f]{64}',row['release_digest']): raise FlowError('invalid release receipt')
    return row


def _retained(home: Path,receipt: dict) -> Path:
    expected=home/STORE/receipt['release_digest']
    if receipt['release']!=str(expected): raise FlowError('retained release path escaped its owner')
    info=packaging.inspect(expected)
    if info['digest']!=receipt['release_digest']: raise FlowError('retained release changed')
    return expected


def plan_install(plugin: Path,source: Path,*,repo: Path,home: Path,host: str,scope: str,
                 executable: str | None=None,runner=execute,host_home: Path | None=None) -> InstallPlan:
    repo=repo.resolve(); home=home.resolve(); source=source.absolute()
    if host=='codex' and scope!='user': raise FlowError('native Codex registration uses user scope; materialize project roles separately')
    selected_root=repo if scope=='project' else home
    prior=_owned_receipt(selected_root,host,scope)
    with tempfile.TemporaryDirectory(prefix='crux-host-capabilities-') as scratch:
        capabilities=hosts.probe(host,home=Path(scratch),repo=Path(scratch),executable=executable,runner=runner)
    if not capabilities['present']: raise FlowError('requested host executable is absent')
    exe=capabilities['executable']
    before=_native_state(host,exe,repo,home,runner,host_home,cwd=home if scope=='user' else None) if host in {'claude','codex'} else None
    market=marketplace_root(host,exe,repo=repo,home=home,runner=runner,host_home=host_home) if before is not None else None
    deferred=bool(before and scope=='user' and before['upstream_active'])
    if before and before['installed'] and prior is None: raise FlowError('existing fork installation has no owned receipt; explicit migration required')
    if host in {'claude','codex'}:
        for parts in [['plugin','marketplace','add'],['plugin','install' if host=='claude' else 'add'],['plugin','list']]:
            result=runner([exe,*parts,'--help'],cwd=repo,env={'PATH':__import__('os').environ.get('PATH',''),'HOME':str(home)},timeout=8,limit=100_000)
            if result.status!='ok': raise FlowError('installed host does not support required native plugin commands')
    with source_release(source) as release:
        engine=Path(release['engine']); source_root=Path(release['root']); store=home/STORE/release['digest']
        if store.exists():
            if packaging.inspect(store)['digest']!=release['digest']: raise FlowError('immutable release location was modified')
            files={}
        else:
            files={f'{STORE}/{release["digest"]}/{name}':path.read_bytes() for path,name in packaging._files(source_root)}
        modes={name:0o755 if '/bin/' in name else 0o644 for name in files}
        release_plan=managed.plan(home,files,owner='release-retention',modes=modes)
        effective=policy.resolve(engine,repo,home,host)
        root,outputs,_=materialize.updates(engine,effective,repo=repo,home=home,scope=scope,
                 include_skills=host in {'omp','opencode'},host_home=host_home,engine=store/'crux-flow/engine')
        no_op=bool(prior and prior['state']=='installed' and prior['release_digest']==release['digest'] and
                   prior['policy_digest']==identity(effective) and not outputs and
                   (before is None or (market is not None and before['installed'] and before['enabled']==(not deferred) and before['statically_valid'] and before.get('runtime_digest')==release['release']['runtime_digest'])))
        previous={k:v for k,v in prior.items() if k!='previous'} if prior else None
        receipt={'schema_version':1,'host':host,'scope':scope,'release':str(store),'release_digest':release['digest'],
                 'runtime_digest':release['release']['runtime_digest'],'policy_digest':identity(effective),'state':'installed',
                 'executable':exe,'previous':previous,'host_home':str(host_home) if host_home else None,'user_activation':'deferred' if deferred else 'active','projection_transaction':uuid.uuid4().hex}
        if not no_op: outputs[receipt_name(host,scope)]=canonical(receipt)
        projection_plan=managed.plan(root,outputs,owner='installation')
        registered=False
        if market is not None and before is not None and not before['installed']:
            if packaging.inspect(Path(market))['digest']!=release['digest']:
                raise FlowError('registered Flow marketplace refers to another release; select that release with --source')
            registered=True
        commands=[] if host not in {'claude','codex'} or no_op else _commands(host,exe,scope,store,replace_existing=bool(before and before['installed']),registered=registered)
        if before is not None and before['installed'] and before['statically_valid'] and before.get('runtime_digest')==release['release']['runtime_digest'] and before['enabled']==(not deferred):
            commands=[] if market is not None else [[exe,'plugin','marketplace','add',str(store),*(['--scope',scope] if host=='claude' else [])]]
        recovery=(_commands(host,exe,scope,_retained(home,prior),replace_existing=True) if prior else
                [[exe,'plugin','uninstall' if host=='claude' else 'remove','crux-flow@crux-flow',*(['--scope',scope] if host=='claude' else [])]]) if host in {'claude','codex'} else []
        return InstallPlan(source,repo,home,host,scope,exe,release['digest'],release['release']['runtime_digest'],store,
               release_plan,projection_plan,_controls(repo,home),before,tuple(map(tuple,commands)),tuple(map(tuple,recovery)),previous,receipt,no_op,host_home,deferred,market)


def _run_commands(commands,plan: InstallPlan,runner) -> list[dict]:
    values=[]
    directory=hosts.paths(plan.host,'user',repo=plan.repo,home=plan.home,host_home=plan.host_home)['base']
    directory.mkdir(parents=True,exist_ok=True)
    for command in commands:
        result=runner(list(command),cwd=plan.repo,env=hosts.environment(plan.host,plan.home,host_home=plan.host_home),timeout=45,limit=500_000)
        values.append({'command':list(command),**result.public()})
        if result.status!='ok': break
    return values


def replan(plan: InstallPlan,plugin: Path,*,runner=execute) -> InstallPlan:
    """Plan again against current state. Hosts share one retained release, so after one host applies,
    the next host's up-front plan would see those files as a stale inspection."""
    return plan_install(plugin,plan.source,repo=plan.repo,home=plan.home,host=plan.host,scope=plan.scope,
                        executable=plan.executable,runner=runner,host_home=plan.host_home)


def _success(results,commands): return len(results)==len(commands) and all(r['status']=='ok' for r in results)


def apply_install(plan: InstallPlan,*,runner=execute,expected_digest: str | None=None) -> dict:
    if expected_digest is not None and expected_digest!=plan.public()['plan_digest']: raise FlowError('displayed plan digest mismatch')
    if _controls(plan.repo,plan.home)!=plan.controls: raise FlowError('scoped preferences changed after planning')
    with source_release(plan.source) as current:
        if current['digest']!=plan.source_digest: raise FlowError('source release changed after planning')
    user=plan.home if plan.scope=='user' else None
    if plan.before_native is not None and _native_state(plan.host,plan.executable,plan.repo,plan.home,runner,plan.host_home,cwd=user)!=plan.before_native:
        raise FlowError('native registration changed after planning')
    if plan.before_native is not None and marketplace_root(plan.host,plan.executable,repo=plan.repo,home=plan.home,runner=runner,host_home=plan.host_home)!=plan.before_marketplace:
        raise FlowError('marketplace registration changed after planning')
    for item in plan.projection_plan.changes:
        if managed.read_file(plan.projection_plan.root,item.path)!=item.before: raise FlowError('stale projection plan')
    if plan.no_op: return {'state':'no-op','host':plan.host,'retained_release':str(plan.retained),'runtime_loaded':'unobserved'}
    managed.apply(plan.release_plan)
    actions=_run_commands(plan.commands,plan,runner) if plan.commands else []
    ready=_success(actions,plan.commands)
    if ready and plan.deferred:
        try: deferral=_defer(plan.host,plan.executable,scope=plan.scope,repo=plan.repo,home=plan.home,runner=runner,host_home=plan.host_home)
        except (FlowError,OSError): deferral=[{'status':'failed'}]
        actions=[*actions,*deferral]; ready=all(r['status']=='ok' for r in deferral)
    if ready and plan.before_native is not None:
        native=_native_state(plan.host,plan.executable,plan.repo,plan.home,runner,plan.host_home,cwd=user)
        ready=native['installed'] and native['enabled']==(not plan.deferred) and native['statically_valid'] and native.get('runtime_digest')==plan.runtime_digest
    if not ready:
        recovery=_run_commands(plan.recovery_commands,plan,runner) if plan.recovery_commands else []
        recovered=_success(recovery,plan.recovery_commands)
        if not plan.recovery_commands and plan.before_native is not None:
            removal=[[plan.executable,'plugin','uninstall' if plan.host=='claude' else 'remove','crux-flow@crux-flow',*(['--scope',plan.scope] if plan.host=='claude' else [])]]
            recovery=_run_commands(removal,plan,runner); recovered=_success(recovery,removal)
        return {'state':'failed-recovered' if recovered else 'recovery-required','host':plan.host,'actions':actions,'recovery':recovery,'retained_release':str(plan.retained)}
    try:
        result=managed.apply(plan.projection_plan,transaction_id=plan.receipt['projection_transaction'])
    except (OSError,FlowError):
        recovery=_run_commands(plan.recovery_commands,plan,runner) if plan.recovery_commands else []
        restored=all(managed.read_file(plan.projection_plan.root,c.path)==c.before for c in plan.projection_plan.changes)
        if plan.before_native is not None:
            native=_native_state(plan.host,plan.executable,plan.repo,plan.home,runner,plan.host_home,cwd=user)
            restored=restored and native['installed']==plan.before_native['installed'] and (not native['installed'] or native.get('runtime_digest')==plan.before_native.get('runtime_digest'))
        return {'state':'failed-recovered' if restored and _success(recovery,plan.recovery_commands) else 'recovery-required',
                'host':plan.host,'actions':actions,'recovery':recovery,'file_recovery':'restored' if restored else 'inspect transaction journal'}
    return {'state':'installed','host':plan.host,'scope':plan.scope,'retained_release':str(plan.retained),
            'projection_transaction':result['transaction_id'],'runtime_loaded':'unobserved','actions':actions}


def status(*,repo: Path,home: Path,host: str,executable: str | None=None,runner=execute,host_home: Path | None=None) -> dict:
    import shutil
    exe=executable or shutil.which(host)
    report={'host':host,'present':exe is not None,'installed':False,'enabled':None,'statically_valid':False,'runtime_loaded':'unobserved',
            'next_verification':'open a compatible host session; inspect loaded skills, agent identity, model and restrictions'}
    if host not in hosts.HOSTS: raise FlowError('invalid host')
    if host_home is None:
        for selected_scope, root in [('project', repo), ('user', home)]:
            try:
                receipt = _owned_receipt(root, host, selected_scope)
                if receipt is not None and receipt.get('host_home'):
                    candidate = Path(receipt['host_home'])
                    if not candidate.is_absolute() or not candidate.is_relative_to(home.resolve()):
                        raise FlowError('custom host home is outside the owned home')
                    host_home = candidate
                    break
            except (FlowError, OSError):
                report['inspection_error'] = 'owned host location could not be validated'
    if host in {'claude','codex'} and exe:
        try: report.update(_native_state(host,exe,repo,home,runner,host_home))
        except FlowError: report['inspection_error']='native inventory unavailable'
    scopes=[]
    for scope,root in [('project',repo),('user',home)]:
        raw=managed.read_file(root,receipt_name(host,scope))
        if raw is None: continue
        try:
            item=_owned_receipt(root,host,scope)
            if item is None or item['state']!='installed': continue
            _retained(home,item)
            materialize.removal(root,host,scope,repo=repo,home=home,host_home=host_home)
            scopes.append({'scope':scope,'valid':True})
        except (FlowError,OSError): scopes.append({'scope':scope,'valid':False})
    if scopes:
        report['winning_scope']=scopes[0]['scope']; report['shadowed_scopes']=[x['scope'] for x in scopes[1:]]
        if host in {'opencode','omp'}:
            report['installed']=True; report['statically_valid']=scopes[0]['valid']
        elif not scopes[0]['valid']: report['statically_valid']=False
    return report


def uninstall(*,repo: Path,home: Path,host: str,scope: str,authorized: bool,runner=execute,dry_run: bool=False,expected_plan_digest: str | None=None) -> dict:
    if not authorized and not dry_run: raise FlowError('uninstall requires explicit authorization')
    root=repo if scope=='project' else home; old=_owned_receipt(root,host,scope)
    if old is None: raise FlowError('owned installation missing')
    retained=_retained(home,old); host_home=Path(old['host_home']) if old.get('host_home') else None
    changes=materialize.removal(root,host,scope,repo=repo,home=home,host_home=host_home)
    changes[receipt_name(host,scope)]=None
    files=managed.plan(root,changes,owner='uninstall')
    commands=[] if host not in {'claude','codex'} else [[old['executable'],'plugin','uninstall' if host=='claude' else 'remove','crux-flow@crux-flow',*(['--scope',scope] if host=='claude' else [])]]
    public = {'state':'planned','files':files.public(),'commands':commands,'release_retained':str(retained)}
    if dry_run: return public
    if expected_plan_digest is not None and expected_plan_digest != identity(public):
        raise FlowError('uninstall inputs changed after approval')
    results=[]
    for command in commands:
        result=runner(command,cwd=repo,env=hosts.environment(host,home,host_home=host_home),timeout=45,limit=500000)
        results.append(result.public())
        if result.status!='ok': return {'state':'recovery-required','actions':results}
    try:
        outcome=managed.apply(files)
    except (OSError,FlowError):
        recovery=[]
        restore=_commands(host,old['executable'],scope,retained) if commands else []
        for command in restore:
            result=runner(command,cwd=repo,env=hosts.environment(host,home,host_home=host_home),timeout=45,limit=500000)
            recovery.append(result.public())
            if result.status!='ok': break
        if restore and old.get('user_activation')=='deferred' and _success(recovery,restore):
            recovery.extend(_defer(host,old['executable'],scope=scope,repo=repo,home=home,runner=runner,host_home=host_home))
        restored=all(managed.read_file(files.root,c.path)==c.before for c in files.changes)
        return {'state':'failed-recovered' if restored and all(r['status']=='ok' for r in recovery) and len(recovery)>=len(restore) else 'recovery-required','recovery':recovery}
    return {'state':'uninstalled','transaction_id':outcome['transaction_id'],'release_retained':str(retained),'runtime_loaded':'unobserved'}


def _rollback_projection(root: Path, host: str, scope: str, current: dict, previous: dict,
                         *, repo: Path, home: Path, host_home: Path | None) -> dict[str, bytes | None]:
    import io
    from ruamel.yaml import YAML

    materialize.removal(root, host, scope, repo=repo, home=home, host_home=host_home)
    tx = managed.receipt(root, current['projection_transaction'])
    if tx['status'] != 'committed' or tx['payload']['owner'] != 'installation':
        raise FlowError('rollback requires the committed installation transaction')
    projection = f'.crux-flow/receipts/{host}-{scope}-projection.json'
    installed = mapping(decode(managed.read_file(root, projection) or b''))
    native = hosts.paths(host, scope, repo=repo, home=home, host_home=host_home)
    allowed = {receipt_name(host, scope), projection}
    if host == 'omp' and installed.get('config'):
        allowed.add(installed['config'])
    updates = {}
    for row in tx['payload']['changes']:
        name = row['path']
        if name not in allowed and not materialize._owned(root, name, native):
            raise FlowError('rollback transaction contains a foreign path')
        if name != installed.get('config') and digest(managed.read_file(root, name)) != row['after']:
            raise FlowError('rollback target changed after installation')
        updates[name] = managed._decode(row['before_bytes'])
    updates[receipt_name(host, scope)] = canonical(previous)
    config = installed.get('config')
    if host == 'omp' and config in updates:
        before = mapping(decode(updates[projection] or b''))
        if before.get('config') != config:
            raise FlowError('rollback cannot silently relocate OMP configuration')
        codec = YAML()
        codec.preserve_quotes = True
        raw = managed.read_file(root, config)
        mapping(decode(raw or b''))
        data = codec.load(raw.decode())
        roles = mapping(data.get('modelRoles', {}))
        prior_aliases = mapping(before.get('aliases', {}))
        for key, cell in mapping(installed.get('aliases', {})).items():
            value = prior_aliases[key]['after'] if key in prior_aliases else cell['before']
            if value is None:
                roles.pop(key, None)
            else:
                roles[key] = value
        if roles:
            data['modelRoles'] = roles
        else:
            data.pop('modelRoles', None)
        if not data and not before.get('config_existed'):
            updates[config] = None
        else:
            output = io.StringIO()
            codec.dump(data, output)
            updates[config] = output.getvalue().encode()
    return updates


def rollback_install(*, repo: Path, home: Path, host: str, scope: str, authorized: bool,
                     runner=execute, dry_run: bool=False, expected_plan_digest: str | None=None) -> dict:
    if not authorized and not dry_run:
        raise FlowError('rollback requires explicit authorization')
    root = repo if scope == 'project' else home
    current = _owned_receipt(root, host, scope)
    if current is None or not current.get('previous') or not current.get('projection_transaction'):
        raise FlowError('compatible rollback snapshot missing')
    previous = current['previous']
    old_release = _retained(home, previous)
    current_release = _retained(home, current)
    host_home = Path(current['host_home']) if current.get('host_home') else None
    updates = _rollback_projection(root, host, scope, current, previous,
                                   repo=repo, home=home, host_home=host_home)
    files = managed.plan(root, updates, owner='installation-rollback')
    native = host in {'claude', 'codex'}
    commands = _commands(host, current['executable'], scope, old_release, replace_existing=True) if native else []
    recovery_commands = _commands(host, current['executable'], scope, current_release, replace_existing=True) if native else []
    user = home if scope == 'user' else None
    before = _native_state(host, current['executable'], repo, home, runner, host_home, cwd=user) if native else None
    if before is not None and (not before['installed'] or not before['statically_valid']
                              or before.get('runtime_digest') != current['runtime_digest']):
        raise FlowError('current native installation differs from its owned receipt')
    public = {'state':'planned', 'files':files.public(), 'commands':commands,
              'recovery_commands':recovery_commands, 'before_native':before}
    if dry_run:
        return public
    if expected_plan_digest is not None and identity(public) != expected_plan_digest:
        raise FlowError('rollback inputs changed after approval')
    env = hosts.environment(host, home, host_home=host_home)
    actions = []
    try:
        for command in commands:
            result = runner(command, cwd=repo, env=env, timeout=45, limit=500000)
            actions.append(result.public())
            if result.status != 'ok':
                raise FlowError('native rollback command failed')
        if native and previous.get('user_activation') == 'deferred':
            actions.extend(_defer(host, current['executable'], scope=scope, repo=repo, home=home, runner=runner, host_home=host_home))
        if native:
            observed = _native_state(host, current['executable'], repo, home, runner, host_home, cwd=user)
            expected = previous['runtime_digest']
            if observed['enabled'] != (previous.get('user_activation') != 'deferred') or not observed['statically_valid'] or observed.get('runtime_digest') != expected:
                raise FlowError('native rollback registration did not match the retained release')
        outcome = managed.apply(files)
    except (FlowError, OSError):
        recovery = []
        restored = all(managed.read_file(root, row.path) == row.before for row in files.changes)
        if native:
            try:
                observed = _native_state(host, current['executable'], repo, home, runner, host_home, cwd=user)
                selected = recovery_commands if observed['installed'] else recovery_commands[1:]
                for command in selected:
                    result = runner(command, cwd=repo, env=env, timeout=45, limit=500000)
                    recovery.append(result.public())
                    if result.status != 'ok':
                        break
                if current.get('user_activation') == 'deferred':
                    recovery.extend(_defer(host, current['executable'], scope=scope, repo=repo, home=home, runner=runner, host_home=host_home))
                observed = _native_state(host, current['executable'], repo, home, runner, host_home, cwd=user)
                restored = (restored and all(r['status'] == 'ok' for r in recovery) and observed['enabled'] == (current.get('user_activation') != 'deferred')
                            and observed['statically_valid']
                            and observed.get('runtime_digest') == current['runtime_digest'])
            except (FlowError, OSError):
                restored = False
        return {'state':'failed-recovered' if restored else 'recovery-required',
                'operation':'rollback', 'actions':actions, 'recovery':recovery,
                'file_recovery':'restored' if restored else 'inspect transaction journal'}
    return {'state':'rolled-back', 'transaction_id':outcome['transaction_id'],
            'runtime_loaded':'unobserved', 'retained_release':str(old_release)}
