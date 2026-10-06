from __future__ import annotations

import os
from pathlib import Path
import re
import sys
import tempfile

from .common import FlowError, canonical, decode, digest, identity, mapping, integer
from . import managed, policy, testing
from .processes import execute

RECORD='crux-flow-upstream-candidate.json'
TAG=re.compile(r'^v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-([0-9A-Za-z.-]+))?(?:\+([0-9A-Za-z.-]+))?$')


def semver(value: str) -> tuple:
    match=TAG.fullmatch(value)
    if match is None: raise FlowError('release must have an exact valid semantic version')
    major,minor,patch,pre,build=match.groups()
    for section in (pre,build):
        if section is not None and any(not part for part in section.split('.')): raise FlowError('empty version identifier')
    if pre and any(part.isdecimal() and len(part)>1 and part[0]=='0' for part in pre.split('.')): raise FlowError('leading zero in prerelease identifier')
    return int(major),int(minor),int(patch),pre is None,tuple((0,int(p)) if p.isdecimal() else (1,p) for p in (pre or '').split('.') if p)


def _git(args: list[str],*,cwd: Path | None=None,input_bytes: bytes=b'',timeout: float=120,env=None) -> bytes:
    result=execute(['git',*args],cwd=cwd or Path.cwd(),input_bytes=input_bytes,timeout=timeout,limit=12_000_000,env=env)
    if result.status!='ok': raise FlowError('Git operation failed; inspect the isolated candidate, never the working branch')
    return result.stdout


def available(repository: str) -> dict:
    observed={}
    lines=_git(['ls-remote','--tags',repository],timeout=45).decode().splitlines()
    for line in lines:
        parts=line.split()
        if len(parts)!=2 or not re.fullmatch('[0-9a-f]{40}',parts[0]) or not parts[1].startswith('refs/tags/'): continue
        tag=parts[1].removeprefix('refs/tags/').removesuffix('^{}')
        try: semver(tag)
        except FlowError: continue
        if tag not in observed or parts[1].endswith('^{}'): observed[tag]=parts[0]
    if not observed: raise FlowError('no recognized upstream release tags')
    latest=max(observed,key=semver)
    return {'repository':repository,'tags':observed,'latest':{'tag':latest,'commit':observed[latest]}}


def _tree(root: Path) -> str:
    names=_git(['ls-files','-z','--cached','--others','--exclude-standard'],cwd=root).decode().split('\0')
    values={}
    for name in sorted(set(names)-{'',RECORD}):
        values[name]=digest(managed.read_file(root,name))
    return identity(values)


def prepare(checkout: Path,output: Path,*,ref: str,repository: str | None,plugin: Path,baseline: str | None=None) -> dict:
    checkout=checkout.resolve(); output=output.absolute()
    if output.exists(): raise FlowError('candidate output already exists')
    if not (checkout/'.git').exists(): raise FlowError('upstream preparation requires real committed Git history; a flattened export is not history')
    if _git(['status','--porcelain','--untracked-files=all'],cwd=checkout).strip(): raise FlowError('commit and verify the fork before preparing an upstream candidate')
    definition=policy.load_definition(plugin)['identity']; source=repository or definition['upstream_repository']; base=baseline or definition['upstream_commit']
    if not re.fullmatch('[0-9a-f]{40}',base): raise FlowError('invalid pinned upstream commit')
    _git(['merge-base','--is-ancestor',base,'HEAD'],cwd=checkout)
    observed=available(source); tag=observed['latest']['tag'] if ref=='latest' else ref
    if tag not in observed['tags']: raise FlowError('selected exact release tag was not observed')
    _git(['clone','--no-hardlinks','--branch',tag,'--',source,str(output)],timeout=180)
    upstream_commit=_git(['rev-parse','HEAD'],cwd=output).decode().strip()
    if upstream_commit!=observed['tags'][tag]: raise FlowError('upstream tag changed during acquisition; candidate remains inactive')
    author_name=_git(['config','user.name'],cwd=checkout).decode().strip(); author_email=_git(['config','user.email'],cwd=checkout).decode().strip()
    env=dict(os.environ,GIT_COMMITTER_NAME=author_name,GIT_COMMITTER_EMAIL=author_email)
    patch=_git(['format-patch','--stdout',base+'..HEAD'],cwd=checkout)
    if patch:
        applied=execute(['git','am','-3'],cwd=output,input_bytes=patch,env=env,timeout=180,limit=2_000_000)
        if applied.status!='ok': return {'status':'conflict','candidate':str(output),'activation':'refused','action':'resolve in the isolated candidate; prepare a reconciled committed fork before acceptance'}
    candidate_policy = output / 'crux/catalog/flow-policy.json'
    definition = mapping(decode(managed.read_file(output, 'crux/catalog/flow-policy.json') or b''))
    definition['identity'].update(upstream_commit=upstream_commit, upstream_version=tag.removeprefix('v'),
                                  upstream_repository=source)
    managed.write_file(output, 'crux/catalog/flow-policy.json', canonical(definition), 0o644)
    metadata = managed.read_file(output, 'reference-source.json')
    if metadata is not None:
        provenance = mapping(decode(metadata))
        provenance.update(upstream_commit=upstream_commit, upstream_version=tag.removeprefix('v'))
        managed.write_file(output, 'reference-source.json', canonical(provenance), 0o644)
    generated = []
    for script in ('validate-catalog.py', 'generate-runtime-compat.py', 'generate-routing-table.py'):
        path = output / 'crux/scripts' / script
        if not path.is_file():
            continue
        result = execute([sys.executable, '-B', str(path), '--repo-root', str(output)], cwd=output,
                         env=dict(env, PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=str(output / 'crux/scripts')),
                         timeout=90, limit=2_000_000)
        generated.append({'script':script, **result.public()})
        if result.status != 'ok':
            return {'status':'refused', 'candidate':str(output), 'activation':'refused',
                    'stage':'regeneration', 'checks':generated}
    _git(['add', '--all'], cwd=output)
    pending = _git(['diff', '--cached', '--name-only'], cwd=output)
    if pending.strip():
        _git(['commit', '-m', 'Update pinned upstream baseline and regenerate derived catalogs'], cwd=output,
             env=dict(env, GIT_AUTHOR_NAME=author_name, GIT_AUTHOR_EMAIL=author_email))
    record={'schema_version':1,'status':'prepared','candidate':str(output),'upstream_repository':source,'upstream_tag':tag,'upstream_commit':upstream_commit,
            'fork_base':base,'regeneration':generated,'commit':_git(['rev-parse','HEAD'],cwd=output).decode().strip(),'tree_digest':_tree(output),'activation':'not-activated'}
    managed.write_file(output,RECORD,canonical(record))
    return record


def verify(candidate: Path,*,full: bool=False,test_seconds: int=600,runner=execute) -> dict:
    integer(test_seconds,low=1,high=3600)
    raw=managed.read_file(candidate,RECORD)
    if raw is None: raise FlowError('prepared candidate record missing')
    record=mapping(decode(raw),required={'status','commit','tree_digest','activation'})
    if record['status']!='prepared' or record['activation']!='not-activated': raise FlowError('candidate is not an inactive prepared fork')
    if _git(['rev-parse','HEAD'],cwd=candidate).decode().strip()!=record['commit'] or _tree(candidate)!=record['tree_digest']:
        raise FlowError('candidate changed since preparation; do not verify a different tree under the recorded commit')
    checks=[]
    with tempfile.TemporaryDirectory(prefix='crux-candidate-build-') as temp:
        commands=testing.commands(candidate,full=full,test_seconds=test_seconds)
        commands[-1]['argv'][-1]=str(Path(temp)/'build')
        for check in commands:
            outcome=runner(check['argv'],cwd=candidate,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),timeout=check['seconds'],limit=2_000_000)
            checks.append({'check':check['name'],'command':check['argv'],'scope':str(candidate),'deadline_seconds':check['seconds'],**outcome.public()})
            if outcome.status!='ok': break
    stable=_tree(candidate)==record['tree_digest']
    passed=len(checks)==len(commands) and all(c['status']=='ok' for c in checks) and stable
    return {'status':'verified' if passed else 'refused','commit':record['commit'],'tree_digest':record['tree_digest'],'tree_stable':stable,
            'checks':checks,'activation':'not-activated','native_loading':'unobserved','provider_delivery':'unobserved'}
