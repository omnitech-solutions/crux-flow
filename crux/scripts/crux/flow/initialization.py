from __future__ import annotations

from pathlib import Path

import bionic_config

from .common import FlowError, decode, digest, identity, mapping
from . import hosts, lifecycle, managed, policy
from .processes import execute


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
