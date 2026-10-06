from __future__ import annotations

import copy
import hashlib
import os
from pathlib import Path
import platform
import shutil
import sys
import uuid

from .common import FlowError, canonical, digest, identity, mapping, utc_now
from . import managed, processes, records, upstream_api


def fingerprint(repo: Path, inputs: list[str], *, run: dict) -> dict[str,str | None]:
    flow=run['flow']; out={}; total=0
    for named in inputs:
        if named!='.': managed.relative(named)
        selected=repo/named
        if selected.is_symlink(): raise FlowError('symlinked verification input refused')
        if named == '.' and (repo / '.git').exists():
            listing = processes.execute(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
                                        cwd=repo, timeout=10, limit=4_000_000)
            if listing.status != 'ok':
                raise FlowError('cannot establish the Git verification input boundary')
            names = sorted(set(listing.stdout.decode('utf-8').split('\0')) - {''})
            candidates = [repo / managed.relative(name, internal=True) for name in names]
        else:
            candidates=sorted(selected.rglob('*')) if selected.is_dir() else [selected]
        for path in candidates:
            relative=path.relative_to(repo).as_posix()
            parts=path.relative_to(repo).parts
            if named=='.' and ('.git' in parts or '__pycache__' in parts or '.pytest_cache' in parts or relative.startswith('.crux-flow/transactions/') or relative.startswith('.crux-flow/locks/')):
                continue
            if path.is_symlink(): raise FlowError('symlink in consumed verification input')
            if path.is_dir() or relative==flow['run']:
                continue
            raw=managed.read_file(repo,relative)
            if relative==flow['book'] and raw is not None:
                raw=canonical(upstream_api.load(Path(flow['plugin_root']),'validate-promptbook.py').frozen_plan_subset(mapping(managed.decode(raw))))
            total+=len(raw or b'')
            if len(out)>20000 or total>200_000_000: raise FlowError('verification input fingerprint exceeds supported bounds')
            out[relative]=digest(raw)
    if not out: raise FlowError('verification input set contains no measured files')
    return dict(sorted(out.items()))


def _environment() -> str:
    return identity({'python':sys.version,'platform':platform.platform(),'env':{k:v for k,v in os.environ.items() if k not in {'_','PWD','OLDPWD','SHLVL','PYTEST_CURRENT_TEST'}}})


def _executable(argv: list[str], repo: Path) -> dict:
    name=argv[0]
    resolved=shutil.which(name) if '/' not in name else str((repo/name).resolve())
    if not resolved: raise FlowError('required check executable unavailable')
    path=Path(resolved).resolve()
    if not path.is_file() or path.stat().st_size>200_000_000: raise FlowError('unbounded or missing check executable')
    return {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


def run_check(path: Path, label: str, argv: list[str] | None=None, *, runner=processes.execute) -> dict:
    before=records.load(path); records._active(before)
    flow=before['flow']; repo=Path(flow['repo'])
    contracts=[c for c in flow['contracts'] if c['kind']=='check' and c['id']==label]
    if len(contracts)!=1: raise FlowError('check is not a declared acceptance requirement')
    contract=contracts[0]
    if argv is None:
        recipe=contract.get('recipe')
        if not isinstance(recipe,dict): raise FlowError('this run has no retained project command recipe; supply its exact frozen argv')
        raw=managed.read_file(repo,recipe['path'])
        if digest(raw)!=recipe['sha256']: raise FlowError('project command recipe changed; use its frozen version or an owner-approved successor')
        document=mapping(managed.decode(raw))
        entries=[c for c in document.get('checks',[]) if c.get('id')==label]
        if len(entries)!=1: raise FlowError('frozen recipe does not resolve one check')
        argv=entries[0]['argv']
    if identity(argv)!=contract['command_digest']: raise FlowError('command does not match frozen acceptance identity')
    if before['current_prompt'] is None or before['current_prompt']<contract['n']: raise FlowError('run has not reached this verification unit')
    records._source(before)
    first=fingerprint(repo,contract['inputs'],run=before)
    executable=_executable(argv,repo)
    env=_environment()
    budget=records.view(before)['remaining_seconds']
    timeout=min(3600.0,budget) if budget is not None else 3600.0
    outcome=runner(argv,cwd=repo,timeout=max(0.001,timeout))
    current=records.load(path)
    if current!=before: raise FlowError('run changed during check; execution must be reconciled explicitly')
    last=fingerprint(repo,contract['inputs'],run=before)
    status_='passed' if outcome.status=='ok' and first==last and _environment()==env and _executable(argv,repo)==executable else 'failed'
    receipt={'unit':contract['n'],'id':uuid.uuid4().hex,'label':label,'command_digest':identity(argv),
             'executable':executable,'toolchain_digest':identity(contract['toolchain']),'environment_digest':env,
             'inputs':contract['inputs'],'input_hashes':last,'stable_during_execution':first==last,
             'status':status_,'execution':outcome.public(),'recorded_at':utc_now(),'evidence_class':'owned-command-execution'}
    after=copy.deepcopy(before); after['flow']['checks'].append(receipt)
    after['flow']['events'].append({'type':'check','at':utc_now(),'unit':contract['n'],'status':status_,'execution_id':receipt['id']})
    records.persist(path,before,after)
    return receipt


def fresh(path: Path, receipt: dict, *, run: dict | None=None) -> bool:
    try:
        run=run or records.load(path); flow=run['flow']
        contract=next(c for c in flow['contracts'] if c['n']==receipt['unit'] and c['kind']=='check')
        executable=receipt['executable']; binary=Path(executable['path'])
        return (receipt['evidence_class']=='owned-command-execution' and receipt['status']=='passed'
                and receipt['execution']['status']=='ok' and receipt['execution']['exit_code']==0
                and receipt['stable_during_execution'] is True
                and receipt['command_digest']==contract['command_digest']
                and receipt['toolchain_digest']==identity(contract['toolchain'])
                and receipt['environment_digest']==_environment()
                and binary.is_file() and hashlib.sha256(binary.read_bytes()).hexdigest()==executable['sha256']
                and fingerprint(Path(flow['repo']),receipt['inputs'],run=run)==receipt['input_hashes'])
    except (KeyError,TypeError,ValueError,OSError,StopIteration):
        return False


def review_snapshot(path: Path, run: dict) -> dict:
    inputs=['.']
    return {'inputs':inputs,'input_hashes':fingerprint(Path(run['flow']['repo']),inputs,run=run)}


def review_fresh(path: Path, receipt: dict, *, run: dict) -> bool:
    try:
        return receipt['input_hashes']==fingerprint(Path(run['flow']['repo']),receipt['inputs'],run=run)
    except (KeyError,TypeError,ValueError,OSError):
        return False
