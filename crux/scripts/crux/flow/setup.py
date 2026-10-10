from __future__ import annotations

import os
from pathlib import Path
import shlex
import tempfile

from .common import FlowError, canonical, decode, digest, mapping
from . import lifecycle, managed, packaging
from .processes import execute

RECEIPT='.crux-flow/receipts/cli.json'


def launcher_changes(home: Path,target: Path) -> dict[str,bytes]:
    prior_raw=managed.read_file(home,RECEIPT)
    prior=mapping(decode(prior_raw)) if prior_raw else {'files':{}}
    files={}
    for name in ('crux-flow','crux-local'):
        path='.local/bin/'+name
        raw=managed.read_file(home,path)
        if raw is not None and digest(raw)!=prior['files'].get(path): raise FlowError('foreign or locally edited CLI command; no host installation applied')
        files[path]=('#!/bin/sh\nexec uv run --script '+shlex.quote(str(target/'bin'/name))+' "$@"\n').encode()
    files[RECEIPT]=canonical({'schema_version':1,'files':{name:digest(raw) for name,raw in files.items()},'retained_payload':str(target)})
    return files


def setup(plugin: Path,*,repo: Path,home: Path,selected: dict[str,str],runner=execute,approve=None,dry_run: bool=False) -> dict:
    home=home.resolve(); repo=repo.resolve()
    launcher_changes(home,home/'placeholder')
    with tempfile.TemporaryDirectory(prefix='crux-setup-') as temp:
        built=packaging.build(plugin,Path(temp)/'build')
        release=packaging.inspect(Path(built['root']))
        retained=home/lifecycle.STORE/release['digest']
        plans={host:lifecycle.plan_install(plugin,Path(built['root']),repo=repo,home=home,host=host,scope='user',executable=exe,runner=runner)
               for host,exe in selected.items()}
        files=launcher_changes(home,retained/'crux-flow')
        command_plan=managed.plan(home,files,owner='CLI-setup',modes={'.local/bin/crux-flow':0o755,'.local/bin/crux-local':0o755})
        public={'state':'planned','hosts':{host:p.public() for host,p in plans.items()},'cli':command_plan.public(),
                'atomicity':'each scope is recoverable; multiple native hosts are not globally atomic'}
        if dry_run: return public
        if approve is None or not approve(public): raise FlowError('setup was not authorized')
        if any(managed.read_file(home, item.path) != item.before for item in command_plan.changes):
            raise FlowError('CLI launcher changed after setup approval; no host installation applied')
        outcomes = {}
        for index, (host, plan) in enumerate(plans.items()):
            try:
                if index: plan = lifecycle.replan(plan, plugin, runner=runner)
                outcomes[host] = lifecycle.apply_install(plan, runner=runner, expected_digest=plan.public()['plan_digest'])
            except (FlowError, OSError) as exc:
                outcomes[host] = {'state':'failed', 'reason':str(exc) if isinstance(exc,FlowError) else type(exc).__name__,
                                  'action':'inspect this host scope; independent hosts remain eligible'}
        if any(o['state'] not in {'installed','no-op'} for o in outcomes.values()):
            return {'state':'partial','hosts':outcomes,'cli':'not activated','plan':public}
        if not plans:
            updates={f'{lifecycle.STORE}/{release["digest"]}/{name}':path.read_bytes() for path,name in packaging._files(Path(built['root']))}
            modes={f'{lifecycle.STORE}/{release["digest"]}/{name}':0o755 if path.stat().st_mode & 0o111 else 0o644 for path,name in packaging._files(Path(built['root']))}
            managed.apply(managed.plan(home,updates,owner='CLI-release-retention',modes=modes))
        try: activation=managed.apply(command_plan)
        except (FlowError,OSError):
            return {'state':'partial','hosts':outcomes,'cli':'activation failed; inspect the scoped transaction journal','plan':public}
        inert=sorted(h for h,plan in plans.items() if plan.deferred)
        return {'state':'installed','hosts':outcomes,'cli':activation,
                'next_step':'run `crux-flow init` inside each repository that should use Flow','user_activation':{'inert_until_repository_init':inert} if inert else 'active','commands':[str(home/'.local/bin'/name) for name in ('crux-flow','crux-local')],
                'runtime_loaded':'unobserved','path_action':None if str(home/'.local/bin') in os.environ.get('PATH','').split(os.pathsep) else 'add ~/.local/bin to PATH yourself',
                'plan':public}
