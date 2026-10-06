from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import tempfile

from .common import FlowError, identity
from . import packaging
from .processes import execute

GITHUB=re.compile(r'github\.com[:/](?P<owner>[A-Za-z0-9_.-]+)/(?P<name>[A-Za-z0-9_.-]+?)(?:\.git)?/?\Z')
VERSION=re.compile(r'\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?\Z')


def redact(text: str) -> str:
    """Never echo credentials embedded in a remote URL."""
    return re.sub(r'(://)[^/@\s]+@',r'\1***@',text)


def _env() -> dict[str,str]:
    env=dict(os.environ); env['GIT_TERMINAL_PROMPT']='0'; env['GIT_ASKPASS']=env.get('GIT_ASKPASS','true')
    return env


def _run(runner,argv: list[str],cwd: Path,*,timeout: float=180,allow_failure: bool=False):
    result=runner(argv,cwd=cwd,env=_env(),timeout=timeout,limit=2_000_000)
    if result.status!='ok' and not allow_failure:
        detail=redact(result.stderr.decode('utf-8',errors='replace').strip().splitlines()[-1] if result.stderr.strip() else result.status)
        raise FlowError(f'{argv[0]} {argv[1] if len(argv)>1 else ""} failed: {detail}'[:300])
    return result


def _git(runner,cwd: Path,*args: str,allow_failure: bool=False):
    return _run(runner,['git',*args],cwd,allow_failure=allow_failure)


def _out(result) -> str: return result.stdout.decode('utf-8',errors='replace').strip()


def require_clean(source: Path,*,runner=execute) -> str | None:
    """Refuse to publish something that cannot be reproduced from a commit."""
    inside=_git(runner,source,'rev-parse','--is-inside-work-tree',allow_failure=True)
    if inside.status!='ok': raise FlowError('publishing needs a Git checkout of the source; use --allow-dirty to override')
    if _out(_git(runner,source,'status','--porcelain')): raise FlowError('the source checkout has uncommitted changes; commit them or use --allow-dirty')
    return _out(_git(runner,source,'rev-parse','HEAD'))


def _slug(target: str) -> tuple[str,str] | None:
    found=GITHUB.search(target)
    return (found['owner'],found['name']) if found else None


def _install_hints(target: str) -> dict[str,list[str]]:
    slug=_slug(target); where='/'.join(slug) if slug else redact(target)
    return {'claude':[f'claude plugin marketplace add {where}','claude plugin install crux-flow@crux-flow --scope user'],
            'codex':[f'codex plugin marketplace add {where}','codex plugin add crux-flow@crux-flow'],
            'then':['crux-flow setup','crux-flow init   # inside each repository that should use Flow']}


def _replace_tree(dist: Path,release: Path) -> None:
    for entry in dist.iterdir():
        if entry.name=='.git': continue
        shutil.rmtree(entry) if entry.is_dir() and not entry.is_symlink() else entry.unlink()
    for entry in release.iterdir():
        (shutil.copytree(entry,dist/entry.name,symlinks=False) if entry.is_dir() else shutil.copy2(entry,dist/entry.name))


def publish(plugin: Path,*,target: str,branch: str='main',runner=execute,dry_run: bool=False,approve=None,
            allow_dirty: bool=False,github_release: bool | None=None,gh: str | None=None) -> dict:
    """Publish the built Flow marketplace to a Git distribution repository.

    One path for Claude and Codex: the repository root *is* the marketplace. Nothing outward-facing happens
    before approval; the push is atomic (branch and tag together) and never forced."""
    if not target or re.search(r'\s',target): raise FlowError('publish needs a Git URL or path (--to)')
    if re.fullmatch(r'[A-Za-z0-9._/-]{1,100}',branch) is None or branch.startswith('-'): raise FlowError('invalid branch name')
    source=plugin.parent
    source_commit=None if allow_dirty else require_clean(source,runner=runner)
    with tempfile.TemporaryDirectory(prefix='crux-publish-') as temp:
        work=Path(temp)
        built=packaging.build(plugin,work/'build'); release=Path(built['root'])
        info=packaging.inspect(release)
        version=info['release']['identity']['version']
        if VERSION.fullmatch(str(version)) is None: raise FlowError('release version is not a semantic version')
        tag=f'v{version}'
        dist=work/'dist'
        _git(runner,work,'clone','--quiet','--',target,str(dist))
        has_branch=_git(runner,dist,'ls-remote','--exit-code','--heads','origin',branch,allow_failure=True).status=='ok'
        if has_branch: _git(runner,dist,'checkout','--quiet','-B',branch,f'origin/{branch}')
        else: _git(runner,dist,'symbolic-ref','HEAD',f'refs/heads/{branch}')
        _replace_tree(dist,release)
        _git(runner,dist,'add','-A')
        tree=_out(_git(runner,dist,'write-tree'))
        head_tree=_out(_git(runner,dist,'rev-parse','-q','--verify','HEAD^{tree}',allow_failure=True)) if has_branch else ''
        tagged=_out(_git(runner,dist,'rev-parse','-q','--verify',f'refs/tags/{tag}^{{tree}}',allow_failure=True))
        if tagged and tagged!=tree:
            raise FlowError(f'{tag} is already published with different content; bump the Flow version before publishing again')
        status={}
        for line in _out(_git(runner,dist,'diff','--cached','--name-status')).splitlines():
            status[line[:1]]=status.get(line[:1],0)+1
        public={'operation':'publish','target':redact(target),'branch':branch,'tag':tag,'version':version,'tree':tree,
                'release_digest':info['digest'],'source_commit':source_commit,'archive':Path(built['archive']).name,
                'archive_sha256':built['sha256'],'files':{'added':status.get('A',0),'modified':status.get('M',0),'removed':status.get('D',0)},
                'already_published':bool(tagged),'github_release':'if gh is available' if github_release is not False and _slug(target) else 'not applicable'}
        public['plan_digest']=identity(public)
        if tagged:
            return {'status':'already-published',**public,'install':_install_hints(target)}
        if dry_run: return {'status':'planned',**public,'install':_install_hints(target)}
        if approve is None or not approve(public): raise FlowError('operation cancelled')
        committed=head_tree!=tree
        if committed:
            _git(runner,dist,'commit','--quiet','--no-verify','-m',
                 f'Release crux-flow {version}\n\nSource commit: {source_commit or "unrecorded"}\nRelease digest: {info["digest"]}')
        _git(runner,dist,'tag','-a',tag,'-m',f'crux-flow {version}')
        _git(runner,dist,'push','--atomic','--quiet','origin',f'HEAD:refs/heads/{branch}',f'refs/tags/{tag}')
        commit=_out(_git(runner,dist,'rev-parse','HEAD'))
        remote=_out(_git(runner,dist,'ls-remote','origin',f'refs/tags/{tag}^{{}}'))
        verified=remote.split()[:1]==[commit]
        result={'status':'published' if verified else 'unverified',**public,'commit':commit,'committed':committed,
                'remote_tag_verified':verified,'install':_install_hints(target),'runtime_loaded':'unobserved'}
        result['github_release']=_github_release(runner,_slug(target),tag,Path(built['archive']),version,gh,github_release)
        return result


def _github_release(runner,slug,tag: str,archive: Path,version: str,gh: str | None,wanted: bool | None) -> dict:
    if wanted is False or slug is None: return {'status':'skipped','reason':'not requested or not a GitHub remote'}
    executable=gh or shutil.which('gh')
    if executable is None: return {'status':'skipped','reason':'gh is not installed; run crux-flow package and attach its ZIP to the release manually'}
    repo='/'.join(slug); cwd=archive.parent
    if _run(runner,[executable,'release','view',tag,'--repo',repo],cwd,allow_failure=True).status=='ok': return {'status':'exists','tag':tag}
    try:
        _run(runner,[executable,'release','create',tag,str(archive),'--repo',repo,'--verify-tag','--title',f'crux-flow {version}',
                     '--notes',f'crux-flow {version}. Install through the marketplace, or crux-flow install --source the attached ZIP.'],cwd)
    except FlowError as exc:
        return {'status':'failed','reason':str(exc),'action':'attach the ZIP to the release manually'}
    return {'status':'created','tag':tag}
