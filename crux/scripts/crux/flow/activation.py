from __future__ import annotations

from pathlib import Path

import bionic_config

from .common import FlowError,decode
from . import managed


def guard_project(repo: Path,effective: dict) -> None:
    try: layout=bionic_config.load_config(repo,require_tree=False)
    except bionic_config.BionicConfigError as exc: raise FlowError('project knowledge layout cannot be resolved for safe activation') from exc
    directory=layout.docs_root/'promptbooks/runs'
    if not directory.exists(): return
    def assignments(policy):
        return {name:(row.get('model'),row.get('effort')) for name,row in policy.get('roles',{}).items()}
    for path in directory.glob('*/run-RUN-*.yaml'):
        raw=managed.read_file(repo,path.relative_to(repo).as_posix())
        run=decode(raw) if raw else None
        if not isinstance(run,dict) or run.get('status')!='in_progress': continue
        flow=run.get('flow')
        if not isinstance(flow,dict) or flow.get('stop') or flow.get('status') in {'COMPLETED','CANCELLED'}: continue
        frozen=flow.get('effective_policy',{})
        if frozen.get('host')==effective['host'] and (frozen.get('mode')!=effective['mode'] or assignments(frozen)!=assignments(effective)):
            raise FlowError('active project run pins different native role settings; keep the proposal staged and materialize after completion or an explicit owner transition')
