from __future__ import annotations

import copy
from pathlib import Path
import time
from typing import Callable
import uuid

from .common import FlowError, PLUGIN, canonical, decode, identity, integer, mapping, text, utc_now
from . import managed, policy, provenance, upstream_api

COUNTED={'delegate':'delegates','review':'reviewers','review-repair':'repair_cycles','council':'council_rounds'}
KINDS=set(COUNTED)|{'implementation','test','generation','bookkeeping'}
STOP_REASONS={'BLOCKED','BUDGET_EXHAUSTED','CANCELLED'}


def validate(run: dict, plugin: Path=PLUGIN) -> None:
    validator=upstream_api.load(plugin,'validate-promptbook.py')
    from tempfile import TemporaryDirectory
    import yaml
    with TemporaryDirectory() as temp:
        candidate=Path(temp)/'run.yaml'
        candidate.write_text(yaml.safe_dump(run,sort_keys=False))
        code,_=validator.validate_file(candidate,'run')
        if code!=0: raise FlowError('run fails the existing Crux schema')
    flow=mapping(run.get('flow'))
    if flow.get('schema_version')!='2':
        raise FlowError('legacy Flow snapshot is readable only; create an explicit linked successor before execution')
    if identity(flow['effective_policy'])!=flow['policy_digest']:
        raise FlowError('frozen policy digest mismatch')
    contracts=flow['contracts']
    if len(contracts)!=len(run['prompts']) or [x['n'] for x in contracts]!=[x['n'] for x in run['prompts']]:
        raise FlowError('contract metadata does not match existing run prompts')
    if len({x['id'] for x in contracts})!=len(contracts): raise FlowError('duplicate unit identity')
    if 'status' in flow or 'primary' in flow or 'supporting' in flow:
        raise FlowError('duplicate progress authority is forbidden')
    if len(flow['events'])>10000: raise FlowError('run event bound exceeded; explicit successor required')


def load(path: Path, *, verify_book: bool=True) -> dict:
    if path.is_symlink(): raise FlowError('symlinked run refused')
    raw=managed.read_file(path.parent,path.name)
    if raw is None: raise FlowError('run snapshot missing')
    run=mapping(decode(raw))
    validate(run)
    flow=run['flow']; repo=Path(flow['repo'])
    if path.resolve()!=repo/managed.relative(flow['run']): raise FlowError('run project/path binding mismatch')
    if verify_book:
        book_raw=managed.read_file(repo,managed.relative(flow['book']))
        if book_raw is None: raise FlowError('pinned promptbook missing')
        book=mapping(decode(book_raw))
        if upstream_api.book_hash(PLUGIN,book)!=run['book_content_hash']:
            raise FlowError('frozen promptbook was edited')
        if book['id']!=run['book_id'] or book['current_run']!=run['run_id']:
            raise FlowError('book/run identity mismatch')
        if book['current_prompt']!=run['current_prompt']:
            raise FlowError('book/run pointers disagree; recover the pending transaction')
    return run


def counts(run: dict) -> dict[str,int]:
    return {kind:sum(1 for e in run['flow']['events'] if e.get('type')=='attempt' and e.get('kind')==kind) for kind in KINDS}


def view(run: dict) -> dict:
    flow=run['flow']; elapsed=max(0,int(time.time()*1000)-flow['started_epoch_ms'])/1000
    budget=flow['effective_policy']['budget_minutes']
    remaining=max(0,budget*60-elapsed) if budget is not None else None
    pending=[p['n'] for p in run['prompts'] if p['state'] not in {'done','skipped'}]
    active_findings=[f for f in flow['findings'] if not f['resolved']]
    if run['status']=='completed': outcome='COMPLETED'
    elif run['status']=='abandoned': outcome='CANCELLED'
    elif flow['stop']: outcome=flow['stop']['status']
    elif remaining is not None and remaining<=0: outcome='BUDGET_EXHAUSTED'
    elif any(f['dependency']=='blocking-now' for f in active_findings): outcome='BLOCKED'
    else: outcome='ACTIVE'
    return {'status':outcome,'upstream_status':run['status'],'current_prompt':run['current_prompt'],
            'pending':pending,'counts':counts(run),'elapsed_seconds':elapsed,'remaining_seconds':remaining,
            'phase':flow['phase'],'next_action':flow['next_action'],'revision':flow['revision'],
            'active_invocation':next((e['invocation'] for e in reversed(flow['events']) if e.get('type')=='attempt'),None),
            'last_substantive_result':next((e for e in reversed(flow['events']) if e.get('type') in {'advanced','check','review'}),None),
            'findings':active_findings,'evidence_classes':['owned-command-execution','external-review-attestation'],
            'enforcement':flow['effective_policy']['enforcement']}


def status(path: Path) -> dict:
    return view(load(path))


def _active(run: dict) -> None:
    if view(run)['status']!='ACTIVE': raise FlowError('run is stopped or exhausted; owner-authorized transition or successor required')


def _source(run: dict) -> None:
    if provenance.source_identity(PLUGIN)['runtime_digest']!=run['flow']['source']['runtime_digest']:
        raise FlowError('loaded runtime differs from pinned source; retain the original release or create an explicit successor')


def persist(path: Path, before: dict, after: dict) -> dict:
    repo=Path(before['flow']['repo']); run_rel=before['flow']['run']; book_rel=before['flow']['book']
    current_raw=managed.read_file(repo,run_rel)
    if current_raw is None or decode(current_raw)!=before: raise FlowError('run changed during operation')
    after['flow']['revision']=before['flow']['revision']+1
    _source(before)
    try: upstream_api.load(PLUGIN,'advance-run.py')._check_base_commit_pin(before,path)
    except SystemExit as exc: raise FlowError('committed base revision pin differs') from exc
    validate(after)
    book_raw=managed.read_file(repo,book_rel)
    if book_raw is None: raise FlowError('book missing')
    book=mapping(decode(book_raw))
    if upstream_api.book_hash(PLUGIN,book)!=before['book_content_hash'] or book['current_prompt']!=before['current_prompt']:
        raise FlowError('book changed during operation')
    updated_book=copy.deepcopy(book); updated_book['current_prompt']=after['current_prompt']
    run_bytes=upstream_api.splice(PLUGIN,current_raw,before,after)
    book_bytes=upstream_api.splice(PLUGIN,book_raw,book,updated_book)
    p=managed.plan(repo,{run_rel:run_bytes,book_rel:book_bytes},owner='run-writer')
    if p.changes[0].before not in (current_raw,book_raw):
        raise FlowError('run changed before transaction planning')
    expected={run_rel:current_raw,book_rel:book_raw}
    if any(c.before!=expected[c.path] for c in p.changes): raise FlowError('book/run changed before transaction planning')
    managed.apply(p)
    return view(after)


def mutate(path: Path, action: Callable[[dict],None], *, active: bool=True) -> dict:
    before=load(path)
    if before['status'] in {'completed', 'abandoned'}:
        raise FlowError('terminal run history is immutable; use an explicit successor')
    if active: _active(before)
    _source(before)
    after=copy.deepcopy(before); action(after)
    return persist(path,before,after)


def checkpoint(path: Path, *, next_action: str, phase: str='implementation') -> dict:
    def update(run):
        run['flow']['next_action']=text(next_action)
        run['flow']['phase']=text(phase,maximum=100)
        run['flow']['events'].append({'type':'checkpoint','at':utc_now(),'next_action':next_action,'phase':phase})
    return mutate(path,update)


def attempt(path: Path, *, kind: str, invocation: str, justification: str='') -> dict:
    if kind not in KINDS: raise FlowError('unknown invocation kind')
    if kind == 'council':
        justification = text(justification, label='material council justification')
    def update(run):
        cap=run['flow']['effective_policy']['workflow'].get(COUNTED.get(kind,''))
        if cap is not None and counts(run)[kind]>=cap: raise FlowError('whole-run invocation cap reached')
        run['flow']['events'].append({'type':'attempt','at':utc_now(),'kind':kind,'invocation':text(invocation,maximum=200),'justification':justification})
    return mutate(path,update)


def finding(path: Path, description: str, *, dependency: str) -> dict:
    if dependency not in {'blocking-now','blocking-finalization','non-blocking'}: raise FlowError('invalid dependency classification')
    def update(run):
        run['flow']['findings'].append({'id':uuid.uuid4().hex,'description':text(description),'dependency':dependency,'resolved':False,'resolution':None})
    return mutate(path,update,active=False)


def resolve_finding(path: Path, finding_id: str, *, evidence: str) -> dict:
    def update(run):
        matches=[f for f in run['flow']['findings'] if f['id']==finding_id]
        if len(matches)!=1: raise FlowError('finding not found')
        matches[0]['resolved']=True; matches[0]['resolution']=text(evidence)
        if run['flow']['stop'] and run['flow']['stop']['status']=='BLOCKED': run['flow']['stop']=None
    return mutate(path,update,active=False)


def transition(path: Path, *, reason: str, owner_authorized: bool, mode: str | None=None,
               budget_minutes: int | None=None) -> dict:
    if owner_authorized is not True: raise FlowError('explicit owner authorization required')
    before=load(path); _source(before)
    if before['status']!='in_progress': raise FlowError('terminal history cannot be rewritten; create a linked successor')
    previous=before['flow']['effective_policy']
    if mode=='upstream': raise FlowError('transition to an upstream cycle requires an explicit successor, not relabeling')
    new=copy.deepcopy(previous)
    if mode is not None:
        definition=policy.load_definition(PLUGIN)
        selected=definition['aliases'].get(mode,mode)
        if selected not in {'aggressive','balanced','thorough'}: raise FlowError('unknown fork mode')
        frozen=previous['resolution_inputs']
        invocation={**frozen['invocation'],'mode':selected,'budget_minutes':previous['budget_minutes']}
        new=policy.resolve(PLUGIN,Path(before['flow']['repo']),Path(before['flow']['repo']),previous['host'],invocation,preferences=frozen)
    if budget_minutes is not None: new['budget_minutes']=integer(budget_minutes,low=1,high=525600)
    after=copy.deepcopy(before)
    after['flow']['events'].append({'type':'owner-transition','at':utc_now(),'reason':text(reason),
                                   'before_policy_digest':identity(previous),'after_policy_digest':identity(new),
                                   'before_policy':previous})
    after['flow']['effective_policy']=new; after['flow']['policy_digest']=identity(new); after['flow']['stop']=None
    return persist(path,before,after)


def stop(path: Path, status_: str, *, reason: str) -> dict:
    if status_ not in STOP_REASONS: raise FlowError('invalid stop status')
    def update(run):
        if status_=='BUDGET_EXHAUSTED' and (view(run)['remaining_seconds'] is None or view(run)['remaining_seconds']>0):
            raise FlowError('budget is not exhausted')
        if status_=='BLOCKED' and not any(not f['resolved'] and f['dependency']!='non-blocking' for f in run['flow']['findings']):
            raise FlowError('blocked stop requires a concrete blocking finding')
        if status_=='BLOCKED' and not any(not f['resolved'] and f['dependency']=='blocking-now' for f in run['flow']['findings']):
            remaining_primary=[c for c in run['flow']['contracts'] if c['kind'] in {'primary','supporting'} and run['prompts'][c['n']-1]['state']!='done']
            if remaining_primary: raise FlowError('finalization dependency does not block independent implementation')
        run['flow']['stop']={'status':status_,'reason':text(reason),'at':utc_now()}
    return mutate(path,update,active=False)


def guard_advance(run: dict, outcome: str, result: str) -> None:
    if 'flow' not in run: return
    validate(run); _active(run); _source(run)
    text(result)
    owner_skip=outcome=='skipped' and any(e.get('type')=='owner-disposition' and e.get('unit')==run['current_prompt'] and e.get('owner_authorized') is True for e in run['flow']['events'])
    if outcome!='done' and not owner_skip: raise FlowError('required fork outcomes cannot be silently skipped or blocked; record a finding or explicit owner disposition')
    current=run['current_prompt']
    if current is None: raise FlowError('completed run cannot advance')
    contract=run['flow']['contracts'][current-1]
    if any(run['prompts'][n-1]['state'] not in {'done','skipped'} for n in contract['depends_on']): raise FlowError('unit prerequisite is incomplete')
    from . import evidence
    path=Path(run['flow']['repo'])/run['flow']['run']
    if contract['kind']=='check':
        checks=[x for x in run['flow']['checks'] if x['unit']==current]
        if not checks or not evidence.fresh(path,checks[-1],run=run): raise FlowError('required check has no current successful execution')
    if contract['kind']=='review':
        reviews=[x for x in run['flow']['reviews'] if x['unit']==current]
        if not reviews or not reviews[-1]['independent'] or not evidence.review_fresh(path,reviews[-1],run=run):
            raise FlowError('independent review evidence is missing or stale')
        for c in run['flow']['contracts']:
            if c['kind']=='check':
                checks=[x for x in run['flow']['checks'] if x['unit']==c['n']]
                if not checks or not evidence.fresh(path,checks[-1],run=run): raise FlowError('final verification became stale')
        if any(not x['resolved'] and x['dependency']!='non-blocking' for x in run['flow']['findings']):
            raise FlowError('blocking findings remain unresolved')


def advance_file(path: Path, *, outcome: str, result: str, artifacts: list[str], book_path: Path | None=None) -> dict:
    before=load(path)
    if book_path is not None and book_path.resolve()!=Path(before['flow']['repo'])/before['flow']['book']:
        raise FlowError('explicit book differs from the pinned run book')
    guard_advance(before,outcome,result)
    after=copy.deepcopy(before)
    module=upstream_api.load(PLUGIN,'advance-run.py')
    try:
        module.advance(after,outcome,result,artifacts)
    except SystemExit as exc:
        raise FlowError('upstream advance refused') from exc
    after['flow']['events'].append({'type':'advanced','at':utc_now(),'unit':before['current_prompt'],'result':text(result)})
    cp=after['current_prompt']
    after['flow']['next_action']=after['prompts'][cp-1]['title'] if cp is not None else 'Completed'
    return persist(path,before,after)


def record_review(path: Path, *, reviewer: str, evidence: str, findings: list[dict], independent: bool, repair: bool=False) -> dict:
    from . import evidence as proofs
    before=load(path); _active(before)
    unit=before['current_prompt']
    if unit is None or before['flow']['contracts'][unit-1]['kind']!='review': raise FlowError('review is not the current acceptance unit')
    if independent is not True: raise FlowError('acceptance requires an independent reviewer')
    kind='review-repair' if repair else 'review'
    if repair and not before['flow']['reviews']: raise FlowError('targeted re-review needs an initial review')
    reserved=next((i for i,e in reversed(list(enumerate(before['flow']['events'])))
                   if e.get('type')=='attempt' and e.get('kind')==kind and e.get('invocation')==reviewer and not e.get('completed')),None)
    cap=before['flow']['effective_policy']['workflow']['repair_cycles' if repair else 'reviewers']
    if reserved is None and cap is not None and counts(before)[kind]>=cap: raise FlowError('whole-run review cap reached')
    after=copy.deepcopy(before)
    if reserved is not None: after['flow']['events'][reserved]['completed']=True
    else: after['flow']['events'].append({'type':'attempt','at':utc_now(),'kind':kind,'invocation':text(reviewer,maximum=200),'justification':'independent acceptance','completed':True})
    proof=proofs.review_snapshot(path,before)
    proof.update({'unit':unit,'reviewer':text(reviewer,maximum=200),'independent':True,'evidence':text(evidence),
                  'evidence_class':'external-attestation','recorded_at':utc_now()})
    after['flow']['reviews'].append(proof)
    for f in findings:
        mapping(f,allowed={'description','dependency'},required={'description','dependency'})
        if f['dependency'] not in {'blocking-now','blocking-finalization','non-blocking'}: raise FlowError('invalid finding classification')
        if not any(old['description']==text(f['description']) and not old['resolved'] for old in after['flow']['findings']):
            after['flow']['findings'].append({'id':uuid.uuid4().hex,'description':text(f['description']),'dependency':f['dependency'],'resolved':False,'resolution':None})
    after['flow']['events'].append({'type':'review','at':utc_now(),'unit':unit,'evidence_class':'external-attestation'})
    return persist(path,before,after)


def repair_review(path: Path, *, reviewer: str, evidence: str, findings: list[dict], independent: bool) -> dict:
    return record_review(path,reviewer=reviewer,evidence=evidence,findings=findings,independent=independent,repair=True)


def supersede_unit(path: Path, *, reason: str, owner_authorized: bool) -> dict:
    if owner_authorized is not True: raise FlowError('required outcome narrowing needs explicit owner authorization')
    before=load(path); _active(before); _source(before)
    unit=before['current_prompt']
    if unit is None or before['flow']['contracts'][unit-1]['kind'] not in {'primary','supporting'}:
        raise FlowError('project checks and independent acceptance cannot be waived as a unit disposition')
    after=copy.deepcopy(before)
    after['flow']['events'].append({'type':'owner-disposition','unit':unit,'disposition':'superseded','reason':text(reason),'owner_authorized':True,'at':utc_now()})
    try: upstream_api.load(PLUGIN,'advance-run.py').advance(after,'skipped',text(reason),[])
    except SystemExit as exc: raise FlowError('existing writer refused owner disposition') from exc
    return persist(path,before,after)
