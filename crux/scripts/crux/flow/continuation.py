from __future__ import annotations

from pathlib import Path
import shutil
import time
from typing import Callable

from .common import FlowError
from . import evidence, hosts, records
from .processes import execute


def resume(path: Path) -> dict:
    run=records.load(path); report=records.view(run)
    report['instruction']=(f'Resume the existing Crux run at {path.resolve()}. Read its frozen policy and next eligible prompt. '
                           'Continue required work until a declared terminal state or genuine authorization boundary. '
                           'Checkpointing and a subtask finishing are not run completion. Do not alter frozen book content.')
    return report


def _progress(path: Path,run: dict) -> tuple:
    return (tuple((p['n'],p['state']) for p in run['prompts']),evidence.fingerprint(Path(run['flow']['repo']),['.'],run=run))


def drive(path: Path,*,home: Path,authorized: bool,executable: str | None=None,runner=execute,
          emit: Callable[[dict],None] | None=None) -> dict:
    if authorized is not True: raise FlowError('native model turns require explicit execution authorization')
    stagnant=0
    while True:
        before=records.load(path); report=records.view(before)
        if report['status']!='ACTIVE': return report
        records._source(before)
        host=before['flow']['effective_policy']['host']; exe=executable or shutil.which(host)
        if not exe:
            records.finding(path,'selected native host executable is unavailable',dependency='blocking-now')
            return records.status(path)
        old=_progress(path,before)
        invocation=f'{host}-turn-{len(before["flow"]["events"])+1}'
        records.attempt(path,kind='implementation',invocation=invocation)
        snapshot=records.load(path); remaining=records.view(snapshot)['remaining_seconds']
        model=snapshot['flow']['effective_policy']['roles']['developer']
        prompt=resume(path)['instruction']
        if stagnant: prompt+=' The last turn made no substantive recorded or source progress. Flatten delegation and perform the next concrete action.'
        command,env=hosts.turn(host,exe,repo=Path(snapshot['flow']['repo']),home=home,model=model['model'],effort=model['effort'],prompt=prompt)
        directory=hosts.paths(host,'user',repo=Path(snapshot['flow']['repo']),home=home)['base']
        directory.mkdir(parents=True,exist_ok=True)
        try:
            result=runner(command,cwd=Path(snapshot['flow']['repo']),env=env,input_bytes=prompt.encode() if host in {'claude','codex'} else b'',
                          timeout=max(0.001,remaining if remaining is not None else 3600),limit=2_000_000)
            observation=result.public()
        except OSError:
            observation={'status':'unavailable','exit_code':None,'elapsed_seconds':0,'output_bytes':0}
        after=records.load(path)
        if emit: emit({'event':'host-turn','host':host,'invocation':invocation,'execution':observation,'run':records.view(after)})
        if records.view(after)['status']!='ACTIVE': return records.view(after)
        current=_progress(path,after)
        if current!=old: stagnant=0
        else: stagnant+=1
        if stagnant>=2:
            records.finding(path,'two bounded native turns ended without substantive unit or project-file progress',dependency='blocking-now')
            return records.status(path)
