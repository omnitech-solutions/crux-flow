from __future__ import annotations

import hashlib
from pathlib import Path
import re

from .common import FlowError, decode, identity, mapping
from .managed import read_file
from .processes import execute

RUNTIME_DIRS=('agents','box','catalog','schemas','scripts','skills','templates')


def runtime_digest(plugin: Path) -> str:
    digest=hashlib.sha256()
    count=0
    for folder in RUNTIME_DIRS:
        root=plugin/folder
        if not root.exists():
            continue
        for path in sorted(root.rglob('*')):
            relative=path.relative_to(plugin)
            if any(x in {'__pycache__','.pytest_cache','tests','.git'} for x in relative.parts) or path.suffix=='.pyc':
                continue
            if path.is_symlink():
                raise FlowError('symlinked runtime resource refused')
            if path.is_file():
                raw=read_file(plugin,relative.as_posix())
                if raw is None:
                    raise FlowError('runtime resource changed during inspection')
                digest.update(relative.as_posix().encode()+b'\0'+raw+b'\0')
                count+=1
    if not count:
        raise FlowError('runtime payload is empty')
    return digest.hexdigest()


def git_head(repo: Path) -> str | None:
    try:
        result=execute(['git','rev-parse','HEAD'],cwd=repo,timeout=3,limit=256)
    except OSError:
        return None
    value=result.stdout.decode('ascii',errors='ignore').strip()
    return value if result.status=='ok' and re.fullmatch('[0-9a-f]{40}',value) else None


def source_identity(plugin: Path) -> dict:
    raw=read_file(plugin,'release.json')
    release=mapping(decode(raw)) if raw is not None else {}
    observed=runtime_digest(plugin)
    if release.get('runtime_digest') is not None and release['runtime_digest']!=observed:
        raise FlowError('loaded release resources differ from its manifest')
    return {'runtime_digest':observed,'source_commit':release.get('source_commit'),
            'upstream_commit':mapping(decode(read_file(plugin,'catalog/flow-policy.json') or b''))['identity']['upstream_commit'],
            'kind':'packaged-release' if release else 'source-snapshot'}
