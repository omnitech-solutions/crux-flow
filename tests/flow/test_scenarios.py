"""Scenarios: the real `crux-flow` CLI and upstream's own writer, driven in a temporary repository, asserted on the record.

Each file in tests/flow/scenarios/ states one claim and the steps that prove it. The scripted stand-in for the agent is the
scenario itself: it writes the files an agent would write and runs the commands an agent would run, so what is measured is
the machinery (gates, receipts, caps, refusals), deterministically, offline and for free. Whether a model chooses to follow
the skill is a different question; tests/flow/test_behaviour_live.py measures that, opt in.

Step kinds: write (files), cli (crux-flow argv; exit 0|"nonzero"; stdout JSON subset by dotted path; capture a value into a
variable), writer (upstream advance-run.py on $RUN), snapshot / unchanged (a refused command leaves the run byte-identical),
run (dotted paths into the run record; a list index may be negative).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

ROOT=Path(__file__).resolve().parents[2]; PLUGIN=ROOT/'crux'
SCENARIOS=sorted((Path(__file__).resolve().parent/'scenarios').glob('*.json'))


def dig(value,path: str):
    for part in path.split('.'):
        value=value[int(part)] if isinstance(value,list) else value[part]
    return value


def expand(value,variables: dict):
    if isinstance(value,str):
        for key,text in variables.items(): value=value.replace('$'+key,text)
        return value
    if isinstance(value,list): return [expand(v,variables) for v in value]
    if isinstance(value,dict): return {k:expand(v,variables) for k,v in value.items()}
    return value


def execute(argv: list[str],repo: Path) -> subprocess.CompletedProcess:
    env=dict(os.environ,PYTHONPATH=str(PLUGIN/'scripts'),PYTHONDONTWRITEBYTECODE='1',HOME=str(repo/'home'))
    return subprocess.run(argv,cwd=repo,env=env,capture_output=True,text=True,timeout=120)


def settled(done: subprocess.CompletedProcess,expected) -> None:
    assert (done.returncode!=0) if expected=='nonzero' else (done.returncode==expected),f'exit {done.returncode}\n{done.stdout}\n{done.stderr}'


@pytest.mark.parametrize('path',SCENARIOS,ids=[p.stem for p in SCENARIOS])
def test_scenario(path,tmp_path):
    scenario=json.loads(path.read_text())
    (tmp_path/'.bionic.yml').write_text('config_version: "1"\ndocs_dir: knowledge\n')
    (tmp_path/'knowledge').mkdir(); (tmp_path/'home').mkdir()
    (tmp_path/'knowledge/manifest.yml').write_text('schema_version: "5"\nconcerns_enabled: [promptbooks, journal]\npromptbook:\n  next_number: 1\n')
    (tmp_path/'calc.py').write_text('def add(a, b):\n    return a - b\n')
    variables={'PY':sys.executable,'REPO':str(tmp_path)}; frozen=None
    base=[sys.executable,'-m','crux.flow','--repo',str(tmp_path),'--home',str(tmp_path/'home')]
    for number,step in enumerate(scenario['steps'],1):
        step=expand(step,variables); where=f'{scenario["name"]} step {number}'
        if 'write' in step:
            for name,text in step['write'].items(): (tmp_path/name).write_text(text)
        elif 'cli' in step:
            done=execute([*base,*step['cli']],tmp_path); settled(done,step['exit'])
            out=json.loads(done.stdout) if done.stdout.strip().startswith('{') else {}
            for dotted,want in step.get('stdout',{}).items(): assert dig(out,dotted)==want,f'{where}: {dotted}'
            for name,dotted in step.get('capture',{}).items(): variables[name]=dig(out,dotted)
        elif 'writer' in step:
            settled(execute([sys.executable,str(PLUGIN/'scripts/advance-run.py'),variables['RUN'],*step['writer']],tmp_path),step['exit'])
        elif 'snapshot' in step: frozen=Path(variables['RUN']).read_bytes()
        elif 'unchanged' in step: assert Path(variables['RUN']).read_bytes()==frozen,f'{where}: a refused command changed the run'
        elif 'run' in step:
            record=yaml.safe_load(Path(variables['RUN']).read_text())
            for dotted,want in step['run'].items(): assert dig(record,dotted)==want,f'{where}: {dotted}'
        else: pytest.fail(f'{where}: unknown step')
