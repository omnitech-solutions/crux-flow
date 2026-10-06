from __future__ import annotations

import importlib
import json
from pathlib import Path
import shutil

import pytest

ROOT=Path(__file__).resolve().parents[2]; PLUGIN=ROOT/'crux'


def api():
    try: return importlib.import_module('crux.flow.lifecycle')
    except ModuleNotFoundError: pytest.fail('the product requires a scoped recoverable lifecycle')


@pytest.fixture(scope='module')
def release(tmp_path_factory):
    from crux.flow.packaging import build
    return build(PLUGIN,tmp_path_factory.mktemp('artifacts')/'build')


class Native:
    def __init__(self,host): self.host=host; self.active=None; self.market=None; self.fail=None; self.calls=[]
    def __call__(self,argv,**kwargs):
        from crux.flow.processes import ProcessResult
        self.calls.append(argv)
        if self.fail and self.fail in argv and '--help' not in argv: return ProcessResult('failed',1,0,0,b'',b'private diagnostics')
        if '--version' in argv: data=b'host 0.160.0'
        elif '--help' in argv: data=b'plugin marketplace add remove install uninstall list --scope --json'
        elif argv[1:4]==['plugin','marketplace','add']: self.market=Path(argv[4]); data=b'{}'
        elif argv[1:3] in (['plugin','add'],['plugin','install']):
            self.active=self.market/'crux-flow'; data=b'{}'
        elif argv[1:3] in (['plugin','remove'],['plugin','uninstall']): self.active=None; data=b'{}'
        elif argv[1:3]==['plugin','list']:
            items=[] if self.active is None else [{'id':'crux-flow@crux-flow','enabled':True,'installPath':str(self.active),'version':json.loads((self.active/'plugin.json').read_text())['version']}]
            data=json.dumps(items if self.host=='claude' else {'installed':items}).encode()
        else: data=b'{}'
        return ProcessResult('ok',0,0,len(data),data,b'')


@pytest.mark.parametrize('host',['opencode','omp'])
def test_project_install_noop_relocation_and_uninstall(host,tmp_path,release):
    repo=tmp_path/'repo'; home=tmp_path/'home'; repo.mkdir(); home.mkdir()
    fake=Native(host)
    plan=api().plan_install(PLUGIN,Path(release['archive']),repo=repo,home=home,host=host,scope='project',executable='/test/host',runner=fake)
    assert list(home.rglob('*'))==[] and list(repo.rglob('*'))==[]
    result=api().apply_install(plan,runner=fake)
    assert result['state']=='installed'
    role=repo/f'.{host}/agents/crux-flow-developer.md'; stamp=role.stat().st_mtime_ns
    again=api().plan_install(PLUGIN,Path(release['archive']),repo=repo,home=home,host=host,scope='project',executable='/test/host',runner=fake)
    assert api().apply_install(again,runner=fake)['state']=='no-op'
    assert role.stat().st_mtime_ns==stamp
    status=api().status(repo=repo,home=home,host=host,executable='/test/host',runner=fake)
    assert status['statically_valid'] and status['runtime_loaded']=='unobserved'
    removed=api().uninstall(repo=repo,home=home,host=host,scope='project',authorized=True,runner=fake)
    assert removed['state']=='uninstalled' and not role.exists()
    assert Path(result['retained_release']).is_dir()


@pytest.mark.parametrize('host',['codex','claude'])
def test_native_plugin_route_uses_immutable_release_and_lists_actual_registration(host,tmp_path,release):
    repo=tmp_path/'repo'; home=tmp_path/'home'; repo.mkdir(); home.mkdir(); fake=Native(host)
    plan=api().plan_install(PLUGIN,Path(release['root']),repo=repo,home=home,host=host,scope='user',executable='/test/host',runner=fake)
    assert any(c[1:4]==['plugin','marketplace','add'] for c in plan.public()['commands'])
    out=api().apply_install(plan,runner=fake)
    assert out['state']=='installed'
    report=api().status(repo=repo,home=home,host=host,executable='/test/host',runner=fake)
    assert report['installed'] and report['enabled'] and report['statically_valid']
    assert report['runtime_loaded']=='unobserved'
    assert not any('permission' in ' '.join(c) or 'bypass' in ' '.join(c) for c in fake.calls)
    assert api().uninstall(repo=repo,home=home,host=host,scope='user',authorized=True,runner=fake)['state']=='uninstalled'


def test_stale_projection_or_config_plan_refuses_without_activation(tmp_path,release):
    repo=tmp_path/'repo'; home=tmp_path/'home'; repo.mkdir(); home.mkdir(); fake=Native('omp')
    plan=api().plan_install(PLUGIN,Path(release['root']),repo=repo,home=home,host='omp',scope='project',executable='/test/host',runner=fake)
    (repo/'.crux-flow.yml').write_text('config_version: "1"\nmode: thorough\n')
    with pytest.raises(ValueError): api().apply_install(plan,runner=fake)
    assert not (repo/'.omp').exists()


def test_changed_role_prevents_uninstall_and_preserves_skills(tmp_path,release):
    repo=tmp_path/'repo'; home=tmp_path/'home'; repo.mkdir(); home.mkdir(); fake=Native('omp')
    api().apply_install(api().plan_install(PLUGIN,Path(release['root']),repo=repo,home=home,host='omp',scope='project',executable='/test/host',runner=fake),runner=fake)
    role=repo/'.omp/agents/crux-flow-developer.md'; role.write_text('my change')
    with pytest.raises(ValueError): api().uninstall(repo=repo,home=home,host='omp',scope='project',authorized=True,runner=fake)
    assert (repo/'.omp/skills/crux-flow-flow/SKILL.md').is_file() and role.read_text()=='my change'


def test_install_failure_does_not_leave_active_roles(tmp_path,release):
    repo=tmp_path/'repo'; home=tmp_path/'home'; repo.mkdir(); home.mkdir(); fake=Native('claude'); fake.fail='install'
    plan=api().plan_install(PLUGIN,Path(release['root']),repo=repo,home=home,host='claude',scope='user',executable='/test/host',runner=fake)
    result=api().apply_install(plan,runner=fake)
    assert result['state'] in {'failed-recovered','recovery-required'}
    assert not (home/'.claude/agents/crux-flow-developer.md').exists()


def test_rollback_restores_previous_effective_mode_and_preserves_release(tmp_path,release):
    repo=tmp_path/'repo'; home=tmp_path/'home'; repo.mkdir(); home.mkdir(); fake=Native('omp')
    first=api().apply_install(api().plan_install(PLUGIN,Path(release['root']),repo=repo,home=home,host='omp',scope='project',executable='/test/host',runner=fake),runner=fake)
    role=repo/'.omp/agents/crux-flow-developer.md'; before=role.read_bytes()
    (repo/'.crux-flow.yml').write_text('config_version: "1"\nmode: thorough\n')
    second=api().apply_install(api().plan_install(PLUGIN,Path(release['root']),repo=repo,home=home,host='omp',scope='project',executable='/test/host',runner=fake),runner=fake)
    assert role.read_bytes()!=before
    out=api().rollback_install(repo=repo,home=home,host='omp',scope='project',authorized=True,runner=fake)
    assert out['state']=='rolled-back' and role.read_bytes()==before
    assert Path(first['retained_release']).exists()


def test_unmanaged_matching_filename_is_not_adopted(tmp_path,release):
    repo=tmp_path/'repo'; home=tmp_path/'home'; home.mkdir(); role=repo/'.omp/agents/crux-flow-developer.md'; role.parent.mkdir(parents=True); role.write_text('foreign')
    with pytest.raises(ValueError): api().plan_install(PLUGIN,Path(release['root']),repo=repo,home=home,host='omp',scope='project',executable='/test/host',runner=Native('omp'))
    assert role.read_text()=='foreign'
