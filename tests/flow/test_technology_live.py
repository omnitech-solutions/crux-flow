"""Paid model turns that observe whether the technology router is actually loaded. Opt-in, never part of `pnpm test`.

    CRUX_FLOW_PROVIDER_TESTS=1 uv run --group test python -m pytest tests/flow/test_technology_live.py -m provider

Every turn runs in a fixture copy inside a temporary directory, with the session's settings limited to that
project (`--setting-sources project`), no MCP servers, no session persistence and only read tools available, so
nothing is written and neither the owner's repositories nor his installed plugins take part. The login is the
owner's own. Each case is judged from the event stream (a `Skill` call, a `Read` of a reference, a canary the model
cannot guess), never from what the model says it did. Loading by description is the model's choice: those cases
run several times and pass at a floor; the role-file injection is deterministic and must pass every time.

CRUX_FLOW_LIVE_RUNS (5), CRUX_FLOW_LIVE_MODEL (sonnet), CRUX_FLOW_LIVE_FLOOR (0.8) and CRUX_FLOW_LIVE_EVIDENCE
(a directory that receives one JSON summary and the raw transcripts per case) tune a run.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from test_lifecycle import release  # noqa: F401  (module fixture)
from test_technology import FIXTURES, PLUGIN, ROUTER, api, consumer

pytestmark=pytest.mark.provider

RUNS=int(os.environ.get('CRUX_FLOW_LIVE_RUNS','5')); MODEL=os.environ.get('CRUX_FLOW_LIVE_MODEL','sonnet'); FLOOR=float(os.environ.get('CRUX_FLOW_LIVE_FLOOR','0.8'))
READ_ONLY='Read,Glob,Grep,Skill'      # the whole tool set of a turn: nothing that writes, runs, publishes or reaches the network
FAMILIES={'studio':{'persistence':r'bionic/research/(sources|raw/[^/]+)/drizzle-','ui':r'bionic/research/(sources|raw/[^/]+)/(vercel-|swift-)'},
          'engine':{'ai-runtime':r'bionic/adrs/ADR-00(02|04|28)-','storage':r'bionic/adrs/ADR-0016-','logging':r'bionic/adrs/ADR-00(24|36)-|logging/README'}}
ANY_REFERENCE=r'bionic/(research/(sources|raw|references)|adrs|invariants)/'


@pytest.fixture(autouse=True)
def authorized():
    if os.environ.get('CRUX_FLOW_PROVIDER_TESTS')!='1': pytest.skip('opt-in paid model turns')
    if shutil.which('claude') is None: pytest.skip('Claude Code is not installed')


def adopted(tmp_path: Path,name: str,*,routing_line: bool=False) -> Path:
    """The consumer as it would be after adoption. The Studio is untouched: its router and AGENTS.md are its own,
    unless a case asks what the routing line Flow proposes would change."""
    repo=consumer(tmp_path,name)
    if name=='engine': assert api().sync(PLUGIN,repo)[0]==0
    if name=='engine' or routing_line:
        with (repo/'AGENTS.md').open('a') as handle: handle.write('\n## Technology references\n\n'+api().agents_line('technology-references')+'\n')
    if name=='engine':
        code,report=api().check(PLUGIN,repo)
        assert code==0 and not report['warnings'],report
    return repo


def claude(repo: Path,prompt: str,*,tools: str=READ_ONLY,model: str | None=None,extra: tuple[str,...]=()) -> list[dict]:
    command=['claude','--print','--verbose','--output-format','stream-json','--model',model or MODEL,'--setting-sources','project',
             '--strict-mcp-config','--no-session-persistence','--tools',tools,'--max-budget-usd','1.00',*extra,prompt]
    done=subprocess.run(command,cwd=repo,stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=900)
    events=[json.loads(line) for line in done.stdout.splitlines() if line.startswith('{')]
    assert any(e.get('type')=='result' for e in events),done.stderr[-2000:]
    return events


def observe(repo: Path,events: list[dict]) -> dict:
    """What the transcript shows, split between the main session and any subagent."""
    seen={'skills':[],'reads':[],'handback':'','dispatched':[],'result':'','cost_usd':None,'turns':None}
    root=str(repo.resolve())+'/'; tasks=set()
    for event in events:
        if event.get('type')=='result': seen.update(result=event.get('result') or '',cost_usd=event.get('total_cost_usd'),turns=event.get('num_turns'))
        if event.get('type')=='user' and event.get('parent_tool_use_id') is None:      # the host's hand-back of a subagent's final report
            for part in event['message']['content']:
                if isinstance(part,dict) and part.get('type')=='tool_result' and part.get('tool_use_id') in tasks:
                    seen['handback']+=''.join(piece.get('text','') for piece in part['content']) if isinstance(part['content'],list) else str(part['content'])
        if event.get('type')!='assistant' or event.get('parent_tool_use_id') is not None: continue
        for part in event['message']['content']:
            if part['type']!='tool_use': continue
            given=part['input']
            if part['name']=='Skill': seen['skills'].append(given.get('skill'))
            elif part['name']=='Read': seen['reads'].append(str(given.get('file_path','')).replace(root,''))
            elif part['name'] in ('Task','Agent'): seen['dispatched'].append(given.get('subagent_type')); tasks.add(part['id'])
    seen['router_loaded']=any(str(s).split(':')[-1]=='technology-references' for s in seen['skills']) or any(r.endswith('technology-references/SKILL.md') for r in seen['reads'])
    return seen


def measure(request,tmp_path: Path,name: str,prompt: str,judge,*,runs: int | None=None,routing_line: bool=False,**options) -> dict:
    """Run one case several times at once, judge each transcript, and keep the evidence."""
    count=runs or RUNS; repos=[adopted(tmp_path/f'run-{n}',name,routing_line=routing_line) for n in range(count)]
    with ThreadPoolExecutor(max_workers=count) as pool: transcripts=list(pool.map(lambda repo: claude(repo,prompt,**options),repos))
    rows=[]
    for repo,events in zip(repos,transcripts):
        seen=observe(repo,events); seen['families']={family:any(re.search(pattern,read) for read in seen['reads']) for family,pattern in FAMILIES[name].items()}
        seen['pass']=bool(judge(seen)); seen['result']=seen['result'][:600]; rows.append(seen)
    report={'case':request.node.name,'consumer':name,'model':options.get('model') or MODEL,'prompt':prompt,'runs':count,'passes':sum(r['pass'] for r in rows),
            'router_loaded':sum(r['router_loaded'] for r in rows),'cost_usd':round(sum(r['cost_usd'] or 0 for r in rows),4),'observations':rows}
    target=os.environ.get('CRUX_FLOW_LIVE_EVIDENCE')
    if target:
        folder=Path(target); (folder/'transcripts').mkdir(parents=True,exist_ok=True)
        (folder/f'{request.node.name}.json').write_text(json.dumps(report,indent=1)+'\n')
        for n,events in enumerate(transcripts): (folder/'transcripts'/f'{request.node.name}-{n}.jsonl').write_text('\n'.join(json.dumps(e) for e in events)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='observations'}))
    return report


def prompts(name: str) -> dict:
    return json.loads((FIXTURES/f'{name}.triggers.json').read_text())


# ---- by description, in an ordinary session: the model's choice, so measured ------------------------------------

def test_a_studio_migration_prompt_loads_the_router_and_only_the_persistence_references(request,tmp_path):
    """Recorded, not gated on the rate: the router and the AGENTS.md line are the Studio's own hand-written files, and
    the first measurement (1 load in 5) is a finding about them. Reading a UI reference would be a failure anywhere."""
    report=measure(request,tmp_path,'studio',prompts('studio')['should_fire'][0]['prompt'],
                   lambda seen: seen['router_loaded'] and seen['families']['persistence'] and not seen['families']['ui'])
    assert not any(row['families']['ui'] for row in report['observations'])


def test_a_studio_with_the_routing_line_flow_proposes(request,tmp_path):
    """Recorded, not gated: the same router and prompt, with the proposed section appended to the Studio's AGENTS.md."""
    measure(request,tmp_path,'studio',prompts('studio')['should_fire'][0]['prompt'],
            lambda seen: seen['router_loaded'] and seen['families']['persistence'] and not seen['families']['ui'],routing_line=True)


def test_b_engine_provider_prompt_loads_the_router_and_only_the_ai_runtime_references(request,tmp_path):
    report=measure(request,tmp_path,'engine',prompts('engine')['should_fire'][0]['prompt'],
                   lambda seen: seen['router_loaded'] and seen['families']['ai-runtime'] and not seen['families']['storage'] and not seen['families']['logging'])
    for row in report['observations']:
        assert not re.search(r'Next\.js|\bReact\b',row['result']+' '.join(row['reads']))
    assert report['passes']/report['runs']>=FLOOR,report['passes']


def test_b_engine_with_the_smallest_model(request,tmp_path):
    """Recorded, not gated: how often the smallest model follows the same carriers."""
    measure(request,tmp_path,'engine',prompts('engine')['should_fire'][0]['prompt'],lambda seen: seen['router_loaded'] and seen['families']['ai-runtime'],model='haiku')


# ---- with the Flow plugin in the session, and with the flow skill run first --------------------------------------

@pytest.fixture(scope='module')
def flow_plugin(release):  # noqa: F811
    return ('--plugin-dir',release['payload'])


def test_c_engine_with_flow_enabled_but_not_invoked_still_loads_the_router(request,tmp_path,flow_plugin):
    report=measure(request,tmp_path,'engine',prompts('engine')['should_fire'][0]['prompt'],
                   lambda seen: seen['router_loaded'] and seen['families']['ai-runtime'] and 'crux-flow:flow' not in seen['skills'],runs=min(RUNS,3),extra=flow_plugin)
    assert report['passes']/report['runs']>=FLOOR,report['passes']


@pytest.mark.parametrize('name,family',[('engine','ai-runtime'),('studio','persistence')])
def test_c_the_same_prompt_with_the_flow_skill_run_first_reads_the_same_references(request,tmp_path,flow_plugin,name,family):
    report=measure(request,tmp_path,name,'/crux-flow:flow '+prompts(name)['should_fire'][0]['prompt'],
                   lambda seen: seen['router_loaded'] and seen['families'][family],runs=min(RUNS,3),extra=flow_plugin)
    if name=='engine': assert report['passes']/report['runs']>=FLOOR,report['passes']      # the Studio's own carriers are recorded, as in case a


# ---- a delegated role: injection, so deterministic -----------------------------------------------------------

def test_d_a_delegated_role_starts_with_the_router_in_its_context(request,tmp_path,flow_plugin):
    """The role file lists the router under `skills:`. The subagent is given no tool, so the digest it reports can only
    have come from what the host injected at its start. The same turn answers whether the bare name `flow` in a project
    role file resolves to the plugin's `crux-flow:flow`: the role is asked for a sentence that exists only in that
    projected skill, and a control role file that lists only the router must answer FLOW-ABSENT."""
    from crux.flow import hosts,policy
    count=min(RUNS,3); repos=[]
    for n in range(count+1):
        repo=adopted(tmp_path/f'run-{n}','engine'); home=tmp_path/f'home-{n}'; home.mkdir()
        role=hosts.roles(PLUGIN,policy.resolve(PLUGIN,repo,home,'claude'),preload=api().preload(repo))['files']['crux-flow-reviewer.md']
        if n==count: role=role.replace(b'skills:\n- flow\n- technology-references\n',b'skills:\n- technology-references\n')      # the control
        assert (b'- flow\n' in role)==(n!=count) and b'- technology-references\n' in role
        (repo/'.claude/agents').mkdir(parents=True); (repo/'.claude/agents/crux-flow-reviewer.md').write_bytes(role)
        repos.append(repo)
    digest=re.search(r'Routes digest: `(tr-[0-9a-f]{12})`',(repos[0]/ROUTER).read_text())[1]
    task=('Use no tool. Reply with exactly two lines. Line 1: the "Routes digest" value from the technology router in your context, or ROUTER-ABSENT. '
          'Line 2: the first sentence under the heading "Installed runtime and routing" if a skill with that heading is in your context, or FLOW-ABSENT.')
    prompt=('Dispatch the project agent whose subagent type is exactly `crux-flow-reviewer` (not a plugin-namespaced one) exactly once with this task, '
            f'then repeat its reply verbatim and stop. Task: {task}')
    with ThreadPoolExecutor(max_workers=len(repos)) as pool:
        transcripts=list(pool.map(lambda repo: claude(repo,prompt,tools='Task',model='haiku',extra=flow_plugin),repos))
    rows=[]
    for n,(repo,events) in enumerate(zip(repos,transcripts)):
        seen=observe(repo,events); used=re.search(r'tool_uses: (\d+)',seen['handback'])
        rows.append({'role_lists':['technology-references'] if n==count else ['flow','technology-references'],'dispatched':seen['dispatched'],
                     'subagent_tool_uses':int(used[1]) if used else None,'digest':digest in seen['handback'],
                     'flow_sentence':'The installed plugin contains engine/' in seen['handback'],'flow_absent':'FLOW-ABSENT' in seen['handback'],
                     'handback':seen['handback'][:900],'cost_usd':seen['cost_usd']})
    report={'case':request.node.name,'runs':count,'passes':sum(r['digest'] for r in rows[:count]),'expected_digest':digest,
            'bare_flow_resolved':sum(r['flow_sentence'] for r in rows[:count]),'control':rows[count],'observations':rows[:count]}
    target=os.environ.get('CRUX_FLOW_LIVE_EVIDENCE')
    if target:
        folder=Path(target); (folder/'transcripts').mkdir(parents=True,exist_ok=True); (folder/f'{request.node.name}.json').write_text(json.dumps(report,indent=1)+'\n')
        for n,events in enumerate(transcripts): (folder/'transcripts'/f'{request.node.name}-{n}.jsonl').write_text('\n'.join(json.dumps(e) for e in events)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='observations'}))
    for row in rows:
        assert row['dispatched']==['crux-flow-reviewer'] and row['subagent_tool_uses']==0,row
        assert row['digest'],row      # injected: every run, the control included


# ---- a prompt that should load nothing ------------------------------------------------------------------------

@pytest.mark.parametrize('name',['studio','engine'])
def test_e_a_readme_typo_loads_no_reference_family(request,tmp_path,name):
    report=measure(request,tmp_path,name,prompts(name)['should_not_fire'][0]['prompt'],
                   lambda seen: not seen['router_loaded'] and not any(re.search(ANY_REFERENCE,read) for read in seen['reads']))
    assert report['passes']/report['runs']>=FLOOR,report['passes']


# ---- Codex ----------------------------------------------------------------------------------------------------

def test_f_codex_reads_the_router_and_the_persistence_references(request,tmp_path):
    """Codex's event stream names the commands it ran; a read of the router and of a Drizzle page shows there."""
    if shutil.which('codex') is None: pytest.skip('Codex is not installed')
    count=min(RUNS,3); repos=[adopted(tmp_path/f'run-{n}','studio') for n in range(count)]
    prompt=prompts('studio')['should_fire'][0]['prompt']
    def turn(repo):
        done=subprocess.run(['codex','exec','--json','--ephemeral','--skip-git-repo-check','--sandbox','read-only','--cd',str(repo),prompt],
                            stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=900)
        return [json.loads(line) for line in done.stdout.splitlines() if line.startswith('{')]
    with ThreadPoolExecutor(max_workers=count) as pool: transcripts=list(pool.map(turn,repos))
    rows=[]
    for events in transcripts:
        # The command line only: a command's output (the router's own text, a file listing) names pages that were never opened.
        ran=[e['item'].get('command','') for e in events if e.get('type')=='item.completed' and e.get('item',{}).get('type')=='command_execution']
        commands=' '.join(ran)
        rows.append({'router_loaded':'technology-references/SKILL.md' in commands,'persistence':bool(re.search(r'research/(sources|raw/[^/ ]+)/drizzle-',commands)),
                     'ui':bool(re.search(r'research/(sources|raw/[^/ ]+)/(vercel-|swift-)',commands)),'commands':ran})
    report={'case':request.node.name,'runs':count,'passes':sum(r['router_loaded'] and r['persistence'] and not r['ui'] for r in rows),'observations':rows}
    target=os.environ.get('CRUX_FLOW_LIVE_EVIDENCE')
    if target:
        folder=Path(target); (folder/'transcripts').mkdir(parents=True,exist_ok=True); (folder/f'{request.node.name}.json').write_text(json.dumps(report,indent=1)+'\n')
        for n,events in enumerate(transcripts): (folder/'transcripts'/f'{request.node.name}-{n}.jsonl').write_text('\n'.join(json.dumps(e) for e in events)+'\n')
    print(json.dumps(report))
    assert report['passes']/report['runs']>=FLOOR,report
