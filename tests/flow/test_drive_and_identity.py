"""Two claims no other test proves: `run drive` owns consecutive host turns until the run is accepted, and upstream mode keeps
the upstream role bodies and tools on every host (Codex is proven in test_hosts.py).

The host is a stand-in executable that plays a scripted agent: each turn it does one slice of the work through the real CLI.
No model, no network.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import sys

import yaml

from test_records import PLUGIN, m, project, start  # noqa: F401  (fixtures)

AGENT='''#!{python}
import json, sys
from pathlib import Path
sys.path.insert(0, {scripts!r})
from crux.flow import evidence, records
repo = Path({repo!r}); run = Path({run!r}); turn = repo / '.turns'
count = int(turn.read_text()) + 1 if turn.exists() else 1; turn.write_text(str(count))
sys.stdin.read()
if count == 1:
    (repo / 'source.py').write_text('VALUE = 2\\n'); records.advance_file(run, outcome='done', result='implemented', artifacts=[])
elif count == 2:
    evidence.run_check(run, 'tests', [sys.executable, '-c', 'raise SystemExit(0)']); records.advance_file(run, outcome='done', result='check executed', artifacts=[])
elif count == 3:
    records.record_review(run, reviewer='separate-session', evidence='read the diff and the receipt', findings=[], independent=True)
    records.advance_file(run, outcome='done', result='review accepted', artifacts=[])
'''


def test_drive_runs_consecutive_turns_until_the_run_is_accepted(project,tmp_path):
    path=Path(start(project)['run'])
    agent=tmp_path/'codex'
    agent.write_text(AGENT.format(python=sys.executable,scripts=str(PLUGIN/'scripts'),repo=str(project),run=str(path)))
    agent.chmod(agent.stat().st_mode|stat.S_IXUSR)
    events=[]
    report=m('continuation').drive(path,home=project/'home',authorized=True,executable=str(agent),emit=events.append)
    assert report['status']=='COMPLETED'
    assert [e['event'] for e in events]==['host-turn']*3 and (project/'.turns').read_text()=='3'
    saved=m('records').load(path)
    assert saved['status']=='completed' and saved['flow']['checks'][-1]['status']=='passed' and saved['flow']['reviews'][-1]['independent'] is True


def test_drive_refuses_without_authorization_and_stops_a_stalled_agent(project,tmp_path):
    path=Path(start(project)['run']); records=m('records')
    try: m('continuation').drive(path,home=project/'home',authorized=False)
    except m('common').FlowError: pass
    else: raise AssertionError('a native model turn started without authorization')
    idle=tmp_path/'codex'; idle.write_text(f'#!{sys.executable}\nimport sys\nsys.stdin.read()\n'); idle.chmod(idle.stat().st_mode|stat.S_IXUSR)
    report=m('continuation').drive(path,home=project/'home',authorized=True,executable=str(idle))
    assert report['status']=='BLOCKED' and any('without substantive' in f['description'] for f in records.load(path)['flow']['findings'])


def split(raw: str) -> tuple[dict,str]:
    _,head,body=raw.split('---',2)
    return yaml.safe_load(head),body


def test_upstream_mode_keeps_upstream_role_bodies_and_tools_on_claude_and_opencode(tmp_path):
    import opencode_agents
    from crux.flow import hosts,policy
    for name in ('architect','dev-lead','developer','reviewer','commander'):
        source=(PLUGIN/'agents'/f'{name}.md').read_text(); head,body=split(source)
        claude=hosts.roles(PLUGIN,policy.resolve(PLUGIN,tmp_path,tmp_path/'home','claude',{'mode':'upstream'}))['files'][f'crux-flow-{name}.md'].decode()
        flow_head,flow_body=split(claude)
        assert flow_body.lstrip()==body.lstrip(),name                                   # body: untouched
        assert flow_head['tools'].replace('crux-flow-','')==head['tools'],name         # tools: only the Agent(...) targets are prefixed
        assert flow_head['skills']==head.get('skills',[]) and flow_head['maxTurns']==head['maxTurns'],name
        native=hosts.roles(PLUGIN,policy.resolve(PLUGIN,tmp_path,tmp_path/'home','opencode',{'mode':'upstream'}))['files'][f'crux-flow-{name}.md'].decode()
        upstream_native=yaml.safe_load(opencode_agents.transform(name,source.split('---',2)[1],'x'))
        flow_native,_=split(native)
        strip=lambda rules:[(r['action'],r['resource'].replace('crux-flow-',''),r['effect']) for r in rules]
        assert strip(flow_native['permissions'])==strip(upstream_native['permissions']),name
