from __future__ import annotations

import copy
from pathlib import Path

import bionic_config

from .common import FlowError, PLUGIN, decode, mapping, text, utc_now
from . import managed, upstream_api


def inspect(path: Path) -> dict:
    raw=managed.read_file(path.parent,path.name)
    if raw is None: raise FlowError('historical run not found')
    if path.suffix=='.md': return {'format':'legacy-markdown','path':str(path),'execution':'read-only; use the upstream documented migration route','status':'not-inferred'}
    run=mapping(decode(raw)); version=run.get('flow',{}).get('schema_version')
    result={'format':'flow-v2' if version=='2' else 'flow-v1' if version=='1' else 'upstream-yaml',
            'path':str(path.resolve()),'book_id':run.get('book_id'),'run_id':run.get('run_id'),'status':run.get('status'),
            'current_prompt':run.get('current_prompt'),'execution':'current' if version=='2' else 'original workflow or explicit successor',
            'pending_prompts':[{'n':p.get('n'),'title':p.get('title'),'state':p.get('state')} for p in run.get('prompts',[]) if p.get('state') not in {'done','skipped'}]}
    if version=='1':
        flow=run['flow']; result['legacy_flow_status']=flow.get('status')
        result['unfinished_outcomes']={kind:[k for k,v in flow.get(kind,{}).items() if not v.get('done')] for kind in ['primary','supporting','verification']}
        result['known_invocation_counts']=flow.get('invocation_counts','unobserved')
        result['consistency_warning']=run.get('status')=='completed' and flow.get('status')!='COMPLETED'
    return result


def prepare_successor(repo: Path,path: Path,*,reason: str,owner_authorized: bool) -> dict:
    if owner_authorized is not True: raise FlowError('historical supersession requires explicit owner approval of the new scope and budget')
    path=path.resolve(); repo=repo.resolve(); reason=text(reason)
    if not path.is_relative_to(repo) or path.suffix!='.yaml': raise FlowError('only a contained YAML run can be explicitly superseded here')
    raw=managed.read_file(repo,path.relative_to(repo).as_posix())
    if raw is None: raise FlowError('historical run missing')
    old=mapping(decode(raw)); history=inspect(path)
    layout=bionic_config.load_config(repo,require_tree=True)
    book_path=layout.docs_root/'promptbooks/active'/(path.parent.name+'.yaml')
    book_rel=book_path.relative_to(repo).as_posix(); book_raw=managed.read_file(repo,book_rel)
    if book_raw is None: raise FlowError('historical active book missing; do not infer its path or rewrite history')
    book=mapping(decode(book_raw))
    if book.get('id')!=old.get('book_id') or book.get('current_run')!=old.get('run_id'):
        raise FlowError('historical book/run pointers disagree; reconcile explicitly before migration')
    updates={}
    if old.get('status')=='in_progress':
        changed=copy.deepcopy(old)
        try: upstream_api.load(PLUGIN,'advance-run.py').abandon(changed,'Explicit owner supersession: '+reason)
        except SystemExit as exc: raise FlowError('historical writer refused supersession') from exc
        if changed.get('flow',{}).get('schema_version')=='1': changed['flow']['status']='CANCELLED'
        elif changed.get('flow',{}).get('schema_version')=='2':
            changed['flow']['stop']={'status':'CANCELLED','reason':reason,'at':utc_now()}
        changed['notes']=(changed.get('notes','')+'\nExplicit owner supersession into a new execution basis: '+reason).strip()
        next_book=copy.deepcopy(book); next_book['current_prompt']=None
        updates[path.relative_to(repo).as_posix()]=upstream_api.splice(PLUGIN,raw,old,changed)
        updates[book_rel]=upstream_api.splice(PLUGIN,book_raw,book,next_book)
    return {'updates':updates,'book_id':book['id'],'history':history,'reason':reason,
            'authority':'explicit owner-approved successor; original counters and completed prompts remain in predecessor'}
