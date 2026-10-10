"""Each fork patch inside upstream's shared run writer (`advance-run.py`) has a test that fails without it.

The patches are declared in `crux/surface/declaration.json` (`patched_upstream_files`). They are the integrity gate:
upstream's writer is reachable by any agent, so the fork's acceptance rules must hold there as well.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from test_records import PLUGIN, m, project, start  # noqa: F401  (project is a fixture)


def writer():
    return m('upstream_api').load(PLUGIN, 'advance-run.py')


def run_writer(path: Path, *args: str):
    return subprocess.run([sys.executable, str(PLUGIN/'scripts/advance-run.py'), str(path), *args], capture_output=True, text=True)


def test_upstream_cannot_abandon_a_flow_run(project):
    # patch: the `flow` hook in `main`. Without it upstream's `--abandon` would write an abandonment and erase Flow's history.
    path = Path(start(project)['run']); raw = path.read_bytes()
    done = run_writer(path, '--abandon', '--reason', 'tidying')
    assert done.returncode == 1 and 'crux-flow run stop CANCELLED' in json.loads(done.stdout)['error']
    assert path.read_bytes() == raw


def test_upstream_gate_selectors_are_refused_on_a_flow_run(project):
    # patch: the same hook, for the 3.27 selectors. A Flow run has no council gate, so none of them may be honoured silently.
    path = Path(start(project)['run']); raw = path.read_bytes()
    for flag in (['--gate-info'], ['--outcome', 'done', '--implementation-revision', 'abc']):
        done = run_writer(path, *flag)
        assert done.returncode != 0, flag
        assert path.read_bytes() == raw


def test_upstream_advance_refuses_a_flow_run_that_is_not_accepted(project):
    # patch: `guard_advance` inside `advance()`. In-process callers (the seam, a future upstream caller) reach `advance` directly.
    path = Path(start(project)['run'])
    m('records').advance_file(path, outcome='done', result='Observed feature works', artifacts=[])   # the check unit is now current
    run = m('records').load(path)
    with pytest.raises((SystemExit, ValueError)):
        writer().advance(run, 'done', 'merely claimed', [])


def test_writer_refuses_non_finite_numbers_and_writes_booleans():
    # patch: `_emit` finite-number and boolean handling (upstream's `_emit` has neither).
    w = writer()
    assert w._emit(True) == 'true' and w._emit(False) == 'false' and w._emit(1.5) == '1.5'
    for bad in (float('inf'), float('nan')):
        with pytest.raises(SystemExit):
            w._emit(bad)


def test_flow_block_is_replaced_whole_by_the_splice():
    # patch: `_changed_paths` treats a changed schema-2 `flow` block as one `set`, so the splice never recurses into Flow's tree.
    w = writer()
    before = {'status': 'in_progress', 'flow': {'schema_version': '2', 'events': [{'a': 1}]}}
    after = {'status': 'in_progress', 'flow': {'schema_version': '2', 'events': [{'a': 1}, {'a': 2}]}}
    assert w._changed_paths(before, after) == [('set', ('flow',))]
    other = {'status': 'in_progress', 'keep': {'x': 1}}
    assert w._changed_paths(other, {'status': 'in_progress', 'keep': {'x': 2}}) == [('set', ('keep', 'x'))]


def test_patch_completion_is_refused_when_containment_cannot_be_checked(tmp_path):
    # patch: `_preflight_patch_completion` (retained correctness exception). Outside a repository it must refuse, never pass.
    book = tmp_path/'knowledge/promptbooks/active/PB-0001-x.yaml'; book.parent.mkdir(parents=True); book.write_text('id: PB-0001\n')
    run = tmp_path/'knowledge/promptbooks/runs/PB-0001-x/run-RUN-001.yaml'; run.parent.mkdir(parents=True); run.write_text('x: 1\n')
    with pytest.raises(SystemExit):
        writer()._preflight_patch_completion(book, run)
