from __future__ import annotations

import importlib
import json
from pathlib import Path
import tomllib

import pytest
import yaml

ROOT=Path(__file__).resolve().parents[2]
PLUGIN=ROOT/'crux'


def api():
    try:
        return importlib.import_module('crux.flow.hosts')
    except ModuleNotFoundError:
        pytest.fail('host adapters must share the upstream rendering and policy seams')


@pytest.mark.parametrize('host',['claude','codex','opencode','omp'])
def test_each_host_projects_resolved_roles_without_commander(host,tmp_path):
    from crux.flow.policy import resolve
    policy=resolve(PLUGIN,tmp_path,tmp_path/'home',host)
    result=api().roles(PLUGIN,policy)
    assert len(result['files'])==9
    assert not any('commander' in name for name in result['files'])
    assert result['runtime_loaded']=='unobserved'
    for name,raw in result['files'].items():
        assert b'Primary session' in raw
        assert b'flow' in raw
    if host=='codex':
        data=tomllib.loads(result['files']['crux-flow-developer.toml'].decode())
        assert data['name']=='crux_flow_developer'
        assert data['model']==policy['roles']['developer']['model']
        assert data['model_reasoning_effort']==policy['roles']['developer']['effort']
        review=tomllib.loads(result['files']['crux-flow-reviewer.toml'].decode())
        assert review['sandbox_mode']=='read-only'
    else:
        head=yaml.safe_load(result['files']['crux-flow-reviewer.md'].decode().split('---',2)[1])
        if host=='claude':
            assert 'Edit' not in head['tools'] and 'Write' not in head['tools']
        elif host=='opencode':
            assert any(r['action']=='edit' and r['effect']=='deny' for r in head['permissions'])
            assert any(r['action']=='subagent' and r['effect']=='deny' for r in head['permissions'])
        else:
            assert 'edit' not in head['tools'] and head['spawns']==[]


def test_upstream_codex_renderer_defaults_are_preserved(tmp_path):
    import codex_agents
    import models_catalog
    from crux.flow.policy import resolve
    generated=api().roles(PLUGIN,resolve(PLUGIN,tmp_path,tmp_path/'home','codex',{'mode':'upstream'}))
    original=codex_agents.generate(PLUGIN/'agents',models_catalog.load(PLUGIN/'catalog/models.yml',PLUGIN/'agents'),skill_root=PLUGIN/'skills')
    assert {k.removeprefix('crux-flow-'):v.decode() for k,v in generated['files'].items()}=={k.removeprefix('crux-'):v for k,v in original.items()}


def test_unsupported_controls_are_disclosed(tmp_path):
    from crux.flow.policy import resolve
    result=api().roles(PLUGIN,resolve(PLUGIN,tmp_path,tmp_path/'home','omp'))
    assert 'Skill' in result['unsupported']['reviewer']
    assert 'native_delegation' in result['enforcement']


def test_every_exported_skill_is_self_contained_and_routes_normal_changes(tmp_path):
    result=api().skills(PLUGIN,'codex')
    text=result['flow/SKILL.md'].decode()
    assert 'implement this' in text and 'read-only' in text
    assert 'run-promptbook' in text
    assert b'/tmp/' not in result['flow/SKILL.md']
    assert 'engine' in result['flow/SKILL.md'].decode()
    for name,raw in result.items():
        if name.endswith('SKILL.md'):
            assert b'BEGIN GENERATED: runtime-compat' not in raw
    assert any('/references/' in name for name in result)


@pytest.mark.parametrize('host,scope,path',[
    ('claude','project','.claude'),('codex','project','.codex'),
    ('opencode','user','.config/opencode'),('omp','user','.omp/agent'),
    ('omp','project','.omp')])
def test_discovery_scopes_are_native(host,scope,path,tmp_path):
    result=api().paths(host,scope,repo=tmp_path/'repo',home=tmp_path/'home')
    root=tmp_path/('repo' if scope=='project' else 'home')
    assert result['agents']==root/path/'agents'


def test_explicit_host_home_wins_without_reading_credentials(tmp_path):
    result=api().paths('omp','user',repo=tmp_path/'repo',home=tmp_path/'home',host_home=tmp_path/'custom')
    assert result['agents']==tmp_path/'custom/agents'


@pytest.mark.parametrize('host',['claude','codex','opencode','omp'])
def test_native_turn_uses_exact_model_and_preserves_permissions(host,tmp_path):
    command,env=api().turn(host,'/usr/bin/test-host',repo=tmp_path,home=tmp_path/'home',model='vendor/model',effort='medium' if host in {'claude','codex'} else None,prompt='Resume exact run')
    assert 'vendor/model' in ' '.join(command)
    assert not any(x in ' '.join(command) for x in ['dangerously','bypass','full-access','yolo'])
    if host=='codex': assert env['CODEX_HOME']==str(tmp_path/'home/.codex')
    if host=='claude': assert '--verbose' in command


def test_unknown_host_and_unsupported_effort_refuse(tmp_path):
    with pytest.raises(ValueError):
        api().turn('omp','omp',repo=tmp_path,home=tmp_path,model='vendor/model',effort='high',prompt='x')
    with pytest.raises(ValueError): api().paths('unknown','user',repo=tmp_path,home=tmp_path)


def test_installed_codex_roles_bind_projected_skill_not_raw_engine_instructions(tmp_path):
    from crux.flow import packaging, policy
    release = packaging.build(PLUGIN, tmp_path / 'build')
    engine = Path(release['payload']) / 'engine'
    effective = policy.resolve(engine, tmp_path / 'repo', tmp_path / 'home', 'codex')
    for destination in (None, tmp_path / 'retained' / 'crux-flow' / 'engine'):
        rendered = api().roles(engine, effective, engine=destination)
        config = tomllib.loads(rendered['files']['crux-flow-developer.toml'].decode())
        wanted = (destination or engine).parent / 'skills' / 'flow' / 'SKILL.md'
        assert config['skills']['config'][0]['path'] == str(wanted)
    body = (engine.parent / 'skills' / 'flow' / 'SKILL.md').read_text()
    assert 'Installed runtime and routing' in body
    assert 'Install the generated role agents before delegating:' not in body


def test_source_only_codex_preview_does_not_preload_unprojected_upstream_instructions(tmp_path):
    from crux.flow.policy import resolve
    rendered = api().roles(PLUGIN, resolve(PLUGIN, tmp_path / 'repo', tmp_path / 'home', 'codex'))
    config = tomllib.loads(rendered['files']['crux-flow-developer.toml'].decode())
    assert 'skills' not in config
    assert 'flow skill' in config['developer_instructions']
