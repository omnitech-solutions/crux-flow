"""A property test over the run state machine: random action sequences, fixed invariants. Seeded, offline, no extra dependency.

Whatever an agent does, in whatever order, three things must stay true: a refused action changes nothing; a unit never leaves
`done`; and a run is `completed` only when every unit is done, a declared check passed with a fingerprint still matching the
inputs, and an independent review was attested.
"""
from __future__ import annotations

from pathlib import Path
import random
import sys

import pytest

from test_records import PLUGIN, m, project, start  # noqa: F401  (fixtures)

ACTIONS=('advance','advance','check','review','self-review','edit','edit-unrelated','checkpoint','delegate','finding')
COMPLETED=[]


def step(action: str,path: Path,project: Path,rng: random.Random) -> None:
    records=m('records'); evidence=m('evidence')
    if action=='advance': records.advance_file(path,outcome=rng.choice(('done','done','skipped','blocked')),result='observed',artifacts=[])
    elif action=='check': evidence.run_check(path,'tests',[sys.executable,'-c','raise SystemExit(0)'])
    elif action=='review': records.record_review(path,reviewer='separate-reviewer',evidence='independent review',findings=[],independent=True)
    elif action=='self-review': records.record_review(path,reviewer='self',evidence='my own look',findings=[],independent=False)
    elif action=='edit': (project/'source.py').write_text(f'VALUE = {rng.randrange(10**6)}\n')
    elif action=='edit-unrelated': (project/'notes.txt').write_text(str(rng.random()))
    elif action=='checkpoint': records.checkpoint(path,next_action='continue')
    elif action=='delegate': records.attempt(path,kind='delegate',invocation=f'helper-{rng.randrange(10**6)}',justification='an independent unit')
    else: records.finding(path,'something to look at',dependency='non-blocking')


@pytest.mark.parametrize('seed',range(12))
def test_random_sequences_keep_the_run_honest(project,seed):
    rng=random.Random(seed); path=Path(start(project)['run']); records=m('records'); evidence=m('evidence')
    done_units=set()
    for _ in range(40):
        action=rng.choice(ACTIONS); before=path.read_bytes()
        try: step(action,path,project,rng)
        except (ValueError,m('common').FlowError):
            assert path.read_bytes()==before,f'seed {seed}: a refused {action} changed the run'
            continue
        run=records.load(path)
        now={p['n'] for p in run['prompts'] if p['state']=='done'}
        assert done_units<=now,f'seed {seed}: a done unit reverted after {action}'
        done_units=now
        assert run['flow']['effective_policy']['mode']=='aggressive'
        assert records.view(run)['counts']['delegate']==0,f'seed {seed}: aggressive mode spent a delegate'
        if run['status']=='completed':
            assert all(p['state']=='done' for p in run['prompts'])
            passed=[c for c in run['flow']['checks'] if c['status']=='passed']
            assert passed and evidence.fresh(path,passed[-1]),f'seed {seed}: completed without a fresh passing check'
            assert any(r['independent'] is True for r in run['flow']['reviews']),f'seed {seed}: completed without an independent review'
            COMPLETED.append(seed); return


def test_the_walks_are_not_vacuous():
    """Some seeds must reach acceptance, or the completion invariant above was never exercised. Runs after the walks (file order)."""
    assert COMPLETED,'no random walk reached acceptance; widen the action mix'
