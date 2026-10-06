from __future__ import annotations

import copy
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any

import bionic_config
import record_numbers
import yaml

from .common import FlowError, identity, integer, mapping, text, utc_now
from . import managed, policy, provenance, upstream_api

KINDS={'additive','replacement','migration','repair'}
REPLACEMENT=(
    'All controlled consumers use the replacement; no production caller uses the superseded implementation.',
    'Obsolete implementation, dependencies, configuration, tests and documentation are removed.',
    'Every retained compatibility boundary delegates to the replacement and names its consumer, reason and removal condition.',
    'Supported external contracts and persistent data are preserved or have an explicit migration path.',
)


def route(plugin: Path, repo: Path, home: Path, host: str, *, intent: str='change', invocation: dict | None=None) -> dict:
    if intent not in {'change','inspect'}:
        raise FlowError('task intent must be explicit change or inspect')
    effective=policy.resolve(plugin,repo,home,host,invocation)
    return {'write_required':intent=='change','effective_policy':effective,
            'route':'inspect-and-answer' if intent=='inspect' else 'original-crux-workflow' if effective['mode']=='upstream' else 'ordinary-crux-promptbook'}


def _units(outcomes: list[dict], checks: list[dict], change_kind: str, replaces: str | None) -> tuple[list[dict],list[dict]]:
    if not outcomes or not checks:
        raise FlowError('an executable run requires primary outcomes and final checks')
    prompts=[]; contracts=[]; ids={}
    for raw in outcomes:
        row=mapping(raw,allowed={'id','outcome','evidence','constraints','writes','depends_on','kind','unblocks'},required={'id','outcome','evidence'})
        unit=text(row['id'],maximum=100,label='unit id')
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9._-]*',unit) or unit in ids:
            raise FlowError('duplicate or invalid unit id')
        kind=row.get('kind','primary')
        if kind not in {'primary','supporting'}:
            raise FlowError('invalid deliverable category')
        dependencies=row.get('depends_on',[])
        if not isinstance(dependencies,list) or any(x not in ids for x in dependencies):
            raise FlowError('dependencies must name earlier units in the ordered Crux book')
        constraints=row.get('constraints',[])
        writes=row.get('writes',[])
        if not isinstance(constraints,list) or not isinstance(writes,list):
            raise FlowError('constraints and advisory writes must be arrays')
        constraints=[text(x) for x in constraints]
        writes=[managed.relative(x) for x in writes]
        if change_kind=='replacement': constraints.extend(REPLACEMENT)
        n=len(prompts)+1; ids[unit]=n
        outcome=text(row['outcome']); evidence=text(row['evidence'])
        body='Outcome: '+outcome+'\nEvidence: '+evidence+'\nConstraint: '+('; '.join(constraints) or 'Preserve established supported contracts.')
        if writes: body+='\nExpected write set (advisory): '+', '.join(writes)
        if dependencies: body+='\nDepends on: '+', '.join(dependencies)
        if replaces: body+='\nResponsibility replaced: '+replaces
        prompts.append({'n':n,'title':outcome,'purpose':kind,'prompt':body,'expected_output':evidence})
        contracts.append({'n':n,'id':unit,'kind':kind,'depends_on':[ids[x] for x in dependencies],
                          'writes':writes,'unblocks':row.get('unblocks'),'command_digest':None,'inputs':[],'toolchain':None})
    if not any(x['kind']=='primary' for x in contracts):
        raise FlowError('run needs a primary outcome')
    for c in contracts:
        if c['kind']=='supporting' and c['n']<max(x['n'] for x in contracts if x['kind']=='primary'):
            target = next((unit for unit in contracts if unit['id'] == c['unblocks']), None)
            if target is None or target['kind'] != 'primary' or target['n'] <= c['n']:
                raise FlowError('supporting work before primary delivery must identify a later primary outcome it unblocks')
    for raw in checks:
        row=mapping(raw,allowed={'id','argv','inputs','toolchain'},required={'id','argv','inputs','toolchain'})
        label=text(row['id'],maximum=100)
        if label in ids or label=='independent-review':
            raise FlowError('duplicate verification id')
        argv=row['argv']; inputs=row['inputs']
        if not isinstance(argv,list) or not argv or any(not isinstance(x,str) or '\x00' in x for x in argv):
            raise FlowError('check argv must be a nonempty argument vector')
        if not isinstance(inputs,list) or not inputs:
            raise FlowError('check needs explicit consumed inputs')
        n=len(prompts)+1; ids[label]=n
        command_hash=identity(argv)
        prompts.append({'n':n,'title':'Verify '+label,'purpose':'check','prompt':'Execute the project check matching sha256:'+command_hash+'. Record actual execution and current consumed inputs; never mark a planned command as passed.',
                        'expected_output':'Successful current execution of '+label})
        contracts.append({'n':n,'id':label,'kind':'check','depends_on':[],'writes':[],'unblocks':None,
                          'command_digest':command_hash,'inputs':[managed.relative(x) if x!='.' else '.' for x in inputs],
                          'toolchain':text(row['toolchain'],maximum=1000)})
    n=len(prompts)+1
    prompts.append({'n':n,'title':'Independent acceptance review','purpose':'review',
                    'prompt':'A separate reviewer checks every required outcome, unexpected writes and the dominant risk lens. Report concrete material findings. No N/A fanout; no recursive skill building. '+(' '.join(REPLACEMENT) if change_kind=='replacement' else ''),
                    'expected_output':'Independent reviewed result with no unresolved blocking finding; final checks are current.'})
    contracts.append({'n':n,'id':'independent-review','kind':'review','depends_on':[],'writes':[],'unblocks':None,'command_digest':None,'inputs':['.'],'toolchain':None})
    for prompt in prompts: prompt.setdefault("side_effects",[])
    return prompts,contracts


def start(plugin: Path, repo: Path, home: Path, host: str, *, goal: str, outcomes: list[dict], checks: list[dict],
          invocation: dict | None=None, change_kind: str='additive', replaces: str | None=None,
          previous_run: str | None=None, owner_authorized: bool=False, predecessor_reason: str | None=None, spec_path: Path | None=None) -> dict:
    import time
    from . import records
    repo=repo.resolve(); plugin=plugin.resolve(); home=home.resolve()
    if change_kind not in KINDS or (change_kind=='replacement' and not replaces):
        raise FlowError('invalid change kind or missing replaced responsibility')
    effective=policy.resolve(plugin,repo,home,host,invocation)
    if effective['mode']=='upstream':
        raise FlowError('upstream mode uses the selected original Crux workflow; do not mislabel a fork book')
    layout=bionic_config.load_config(repo,require_tree=True)
    manifest_path=(layout.docs_root/'manifest.yml').relative_to(repo).as_posix()
    raw=managed.read_file(repo,manifest_path)
    if raw is None: raise FlowError('documentation manifest absent')
    manifest=mapping(yaml.safe_load(raw))
    number=integer(mapping(manifest.get('promptbook'))['next_number'],low=1,high=9999)
    if any(x.kind=='PB' and x.number>=number for x in record_numbers.scan_records(layout.docs_root)):
        raise FlowError('promptbook counter conflicts with existing history; repair the manifest explicitly')
    book_id=layout.prefixed(f'PB-{number:04d}')
    slug=re.sub(r'[^a-z0-9]+','-',text(goal,maximum=500).lower()).strip('-')[:60] or 'change'
    filename=f'{book_id}-{slug}'
    book_relative=(layout.docs_root/'promptbooks/active'/f'{filename}.yaml').relative_to(repo).as_posix()
    run_relative=(layout.docs_root/'promptbooks/runs'/filename/'run-RUN-001.yaml').relative_to(repo).as_posix()
    if managed.read_file(repo,book_relative) is not None or managed.read_file(repo,run_relative) is not None:
        raise FlowError('allocated book or run already exists')
    predecessor=None
    if previous_run is not None:
        from .history import prepare_successor
        predecessor=prepare_successor(repo,Path(previous_run),reason=predecessor_reason or "",owner_authorized=owner_authorized)
    prompts,contracts=_units(outcomes,checks,change_kind,replaces)
    if spec_path is not None:
        from .common import digest
        selected=spec_path.absolute()
        if not selected.is_relative_to(repo): raise FlowError('resumable check recipe must live in the selected project')
        relative=selected.relative_to(repo).as_posix(); recipe=managed.read_file(repo,relative)
        if recipe is None: raise FlowError('check recipe file is absent')
        for contract in contracts:
            if contract['kind']=='check':
                contract['recipe']={'path':relative,'sha256':digest(recipe)}
                if relative not in contract['inputs']: contract['inputs'].append(relative)
    book={'format_version':'1','id':book_id,'title':text(goal,maximum=500),'status':'active','created_at':datetime.now(timezone.utc).date().isoformat(),
          'total_prompts':len(prompts),'current_run':'RUN-001','current_prompt':1,'forked_from':None,'tags':['flow'],
          'goal':text(goal),'strategy':'Parent-led outcome delivery, focused verification and bounded independent acceptance review.',
          'prompts':prompts}
    if predecessor: book['strategy']+=' Explicit successor of '+predecessor['book_id']+'; original history and counters remain in the predecessor.'
    snapshot={'format_version':'1','run_id':'RUN-001','book_id':book_id,'book_content_hash':upstream_api.book_hash(plugin,book),
              'started_at':utc_now(),'completed_at':None,'status':'in_progress','current_prompt':1,'base_commit':provenance.git_head(repo),
              'prompts':[{'n':p['n'],'title':p['title'],'state':'pending','started':None,'completed':None,'result':'','artifacts':[]} for p in prompts],
              'notes':'','pr_draft':'','summary':'',
              'flow':{'schema_version':'2','repo':str(repo),'book':book_relative,'run':run_relative,'plugin_root':str(plugin),
                      'effective_policy':effective,'policy_digest':identity(effective),'source':provenance.source_identity(plugin),
                      'started_epoch_ms':int(time.time()*1000),'contracts':contracts,'events':[],'checks':[],'reviews':[],
                      'findings':[],'stop':None,'next_action':prompts[0]['title'],'phase':'implementation','change_kind':change_kind,
                      'replaces':replaces,'previous_run':previous_run,'revision':0}}
    if predecessor:
        snapshot["flow"]["events"].append({"type":"owner-successor","at":utc_now(),"predecessor":predecessor["history"],"reason":predecessor["reason"],"authority":predecessor["authority"]})
    records.validate(snapshot,plugin)
    validator=upstream_api.load(plugin,'validate-promptbook.py')
    from tempfile import TemporaryDirectory
    with TemporaryDirectory() as td:
        candidate=Path(td)/'book.yaml'; candidate.write_text(yaml.safe_dump(book,sort_keys=False))
        if validator.validate_file(candidate,'promptbook')[0]!=0:
            raise FlowError('composer produced an invalid upstream promptbook')
    changed=copy.deepcopy(manifest); changed['promptbook']['next_number']=number+1
    from . import authoring
    changes={book_relative:authoring.book_bytes(book),
             run_relative:yaml.safe_dump(snapshot,sort_keys=False,allow_unicode=True).encode(),
             manifest_path:upstream_api.splice(plugin,raw,manifest,changed)}
    changes.update(authoring.records(plugin,repo,layout.docs_root,book,filename))
    if predecessor: changes.update(predecessor['updates'])
    plan=managed.plan(repo,changes,owner='run-author')
    managed.apply(plan)
    return {'book':str(repo/book_relative),'run':str(repo/run_relative),'mode':effective['mode'],'status':'ACTIVE'}
