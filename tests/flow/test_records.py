from __future__ import annotations

import importlib
import json
from pathlib import Path
import subprocess
import sys
import time

import pytest
import yaml

ROOT=Path(__file__).resolve().parents[2]
PLUGIN=ROOT/'crux'


def m(name):
    return importlib.import_module('crux.flow.'+name)


@pytest.fixture
def project(tmp_path):
    (tmp_path/'.bionic.yml').write_text('config_version: "1"\ndocs_dir: knowledge\n')
    docs=tmp_path/'knowledge'; docs.mkdir()
    (docs/'manifest.yml').write_text('# retain this comment\nschema_version: "5"\nconcerns_enabled: [promptbooks, journal]\npromptbook:\n  next_number: 1\n')
    (tmp_path/'source.py').write_text('VALUE = 1\n')
    (tmp_path/'consumed.md').write_text('required contract\n')
    return tmp_path


def start(project, *, mode='aggressive', change_kind='additive'):
    return m('workflow').start(PLUGIN,project,project/'home','codex',goal='Deliver a bounded change',
        outcomes=[{'id':'feature','outcome':'Feature works','evidence':'One observed behavior','constraints':['Preserve public contract'],'writes':['source.py']}],
        checks=[{'id':'tests','argv':[sys.executable,'-c','raise SystemExit(0)'],'inputs':['source.py','consumed.md'],'toolchain':'Python test'}],
        invocation={'mode':mode},change_kind=change_kind,replaces='old feature' if change_kind=='replacement' else None)


def test_read_only_routing_never_authors(project):
    before={p.relative_to(project).as_posix():p.read_bytes() for p in project.rglob('*') if p.is_file()}
    out=m('workflow').route(PLUGIN,project,project/'home','codex',intent='inspect')
    assert out['write_required'] is False
    after={p.relative_to(project).as_posix():p.read_bytes() for p in project.rglob('*') if p.is_file()}
    assert before==after


def test_book_uses_existing_schema_layout_counter_and_real_units(project):
    result=start(project)
    run=m('records').load(Path(result['run']))
    assert Path(result['book']).is_relative_to(project/'knowledge/promptbooks/active')
    assert len(run['prompts'])==3
    assert 'status' not in run['flow'] and 'primary' not in run['flow']
    assert yaml.safe_load((project/'knowledge/manifest.yml').read_text())['promptbook']['next_number']==2
    assert 'retain this comment' in (project/'knowledge/manifest.yml').read_text()
    validator=m('upstream_api').load(PLUGIN,'validate-promptbook.py')
    assert validator.validate_file(Path(result['book']),'promptbook')[0]==0
    assert validator.validate_file(Path(result['run']),'run')[0]==0


def test_original_writer_cannot_skip_check_or_complete_with_missing_review(project):
    result=start(project); path=Path(result['run'])
    r=m('records')
    r.advance_file(path,outcome='done',result='Observed feature works',artifacts=[])
    raw=path.read_bytes()
    command=[sys.executable,str(PLUGIN/'scripts/advance-run.py'),str(path),'--outcome','done','--result','merely claimed']
    done=subprocess.run(command,capture_output=True,text=True)
    assert done.returncode!=0
    assert path.read_bytes()==raw
    assert r.load(path)['status']=='in_progress'


def test_shared_writer_refuses_required_skip(project):
    result=start(project); path=Path(result['run'])
    with pytest.raises(ValueError): m('records').advance_file(path,outcome='skipped',result='later',artifacts=[])
    assert m('records').load(path)['prompts'][0]['state']=='pending'


def test_yaml_checkpoint_survives_and_comments_are_not_lost(project):
    path=Path(start(project)['run'])
    path.write_text('# run comment\n'+path.read_text())
    before=m('records').load(path)
    out=m('records').checkpoint(path,next_action='Implement behavior',phase='implementation')
    assert out['status']=='ACTIVE'
    assert m('records').load(path)['current_prompt']==before['current_prompt']
    assert path.read_text().startswith('# run comment\n')


def test_frozen_book_and_source_not_reinterpreted(project):
    result=start(project); path=Path(result['run'])
    book=Path(result['book']); original=book.read_text()
    book.write_text(original.replace('Feature works','Feature silently changed'))
    with pytest.raises(ValueError): m('records').checkpoint(path,next_action='continue')
    book.write_text(original)
    old=m('records').load(path)['flow']['effective_policy']
    (project/'.crux-flow.yml').write_text('config_version: "1"\nmode: thorough\n')
    assert m('records').load(path)['flow']['effective_policy']==old


def test_real_check_execution_cannot_be_replaced_by_marking_label(project):
    path=Path(start(project)['run']); r=m('records')
    r.advance_file(path,outcome='done',result='Observed implementation',artifacts=[])
    with pytest.raises(ValueError): r.advance_file(path,outcome='done',result='tests passed',artifacts=[])
    out=m('evidence').run_check(path,'tests',[sys.executable,'-c','raise SystemExit(0)'])
    assert out['status']=='passed' and out['execution']['exit_code']==0
    r.advance_file(path,outcome='done',result='Executed check',artifacts=[])
    assert r.status(path)['status']=='ACTIVE'
    with pytest.raises(ValueError): r.advance_file(path,outcome='done',result='review done',artifacts=[])


def test_failure_and_changed_consumed_docs_never_become_fresh(project):
    path=Path(start(project)['run']); r=m('records')
    r.advance_file(path,outcome='done',result='feature',artifacts=[])
    m('evidence').run_check(path,'tests',[sys.executable,'-c','raise SystemExit(0)'])
    (project/'consumed.md').write_text('changed contract\n')
    with pytest.raises(ValueError): r.advance_file(path,outcome='done',result='old test',artifacts=[])


def test_checkpoint_bookkeeping_does_not_invalidate_own_check(project):
    path=Path(start(project)['run']); r=m('records')
    r.advance_file(path,outcome='done',result='feature',artifacts=[])
    m('evidence').run_check(path,'tests',[sys.executable,'-c','raise SystemExit(0)'])
    r.checkpoint(path,next_action='Review change')
    assert m('evidence').fresh(path,r.load(path)['flow']['checks'][-1]) is True


def test_command_must_match_frozen_acceptance_identity(project):
    path=Path(start(project)['run'])
    with pytest.raises(ValueError): m('evidence').run_check(path,'tests',[sys.executable,'-c','raise SystemExit(1)'])


def test_review_attestation_is_explicit_and_final_acceptance_consistent(project):
    path=Path(start(project)['run']); r=m('records')
    r.advance_file(path,outcome='done',result='Observed behavior',artifacts=[])
    m('evidence').run_check(path,'tests',[sys.executable,'-c','raise SystemExit(0)'])
    r.advance_file(path,outcome='done',result='Actual execution',artifacts=[])
    r.record_review(path,reviewer='separate-reviewer',evidence='Independent review supplied',findings=[],independent=True)
    done=r.advance_file(path,outcome='done',result='Independent review accepted',artifacts=[])
    assert done['status']=='COMPLETED'
    saved=r.load(path)
    assert saved['status']=='completed' and all(p['state']=='done' for p in saved['prompts'])
    assert saved['flow']['reviews'][-1]['evidence_class']=='external-attestation'
    assert 'status' not in saved['flow']


def test_review_outage_allows_independent_work(project):
    path=Path(start(project)['run']); r=m('records')
    r.finding(path,'Reviewer unavailable',dependency='blocking-finalization')
    assert r.status(path)['status']=='ACTIVE'
    r.advance_file(path,outcome='done',result='feature works',artifacts=[])
    assert r.load(path)['prompts'][0]['state']=='done'


def test_unrelated_warning_does_not_block(project):
    path=Path(start(project)['run']); r=m('records')
    r.finding(path,'Optional documentation mismatch',dependency='non-blocking')
    assert r.status(path)['status']=='ACTIVE'


def test_explicit_mode_transition_retains_start_and_whole_run_attempts(project):
    path=Path(start(project)['run']); r=m('records')
    r.attempt(path,kind='review',invocation='review-1')
    before=r.load(path)
    with pytest.raises(ValueError): r.transition(path,mode='balanced',reason='convenience',owner_authorized=False)
    r.transition(path,mode='balanced',reason='Owner requested balanced',owner_authorized=True)
    after=r.load(path)
    assert after['started_at']==before['started_at']
    assert r.status(path)['counts']['review']==1
    assert after['flow']['effective_policy']['mode']=='balanced'


def test_caps_never_reset_on_checkpoint(project):
    path=Path(start(project)['run']); r=m('records')
    with pytest.raises(ValueError): r.attempt(path,kind='delegate',invocation='not-allowed')
    r.attempt(path,kind='review',invocation='review-one')
    r.checkpoint(path,next_action='more review',phase='renamed-phase')
    with pytest.raises(ValueError): r.attempt(path,kind='review',invocation='review-two')


def test_budget_exhaustion_is_honest_and_owner_can_extend_without_clock_reset(project,monkeypatch):
    path=Path(start(project)['run']); r=m('records')
    initial=r.load(path)['flow']['started_epoch_ms']
    monkeypatch.setattr(r.time,'time',lambda:initial/1000+21*60)
    assert r.status(path)['status']=='BUDGET_EXHAUSTED'
    with pytest.raises(ValueError): r.attempt(path,kind='implementation',invocation='late')
    r.transition(path,budget_minutes=40,reason='Owner approves extension',owner_authorized=True)
    assert r.status(path)['status']=='ACTIVE'
    assert r.load(path)['flow']['started_epoch_ms']==initial


def test_replacement_contract_has_single_path_acceptance(project):
    out=start(project,change_kind='replacement')
    book=yaml.safe_load(Path(out['book']).read_text())
    assert 'controlled consumers' in str(book)
    assert 'removal condition' in str(book)
