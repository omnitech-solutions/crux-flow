from pathlib import Path
import json
import sys

import pytest

from test_records import project,PLUGIN
from test_lifecycle import Native,release


def cli(argv,**kwargs):
    from crux.flow.cli import main
    try: return main(argv,**kwargs)
    except TypeError: pytest.fail('CLI must expose injectable shared product services')


def test_mode_set_and_read_only_show_share_one_resolver(tmp_path,capsys):
    args=['--repo',str(tmp_path),'--home',str(tmp_path/'home')]
    assert cli(args+['mode','set','balanced','--scope','project','--yes'])==0
    assert json.loads(capsys.readouterr().out)['mode']=='balanced'
    assert cli(args+['mode','show','--resolved','--host','codex','--json'])==0
    assert json.loads(capsys.readouterr().out)['mode']=='balanced'
    assert not (tmp_path/'home').exists()


def test_cli_run_resume_can_execute_frozen_project_recipe(project,capsys):
    spec=project/'change.json'
    spec.write_text(json.dumps({'goal':'CLI bounded change','outcomes':[{'id':'feature','outcome':'Works','evidence':'Measured'}],
        'checks':[{'id':'tests','argv':[sys.executable,'-c','raise SystemExit(0)'],'inputs':['source.py'],'toolchain':'Python'}]}))
    args=['--repo',str(project),'--home',str(project/'home')]
    assert cli(args+['run','start','--host','codex','--spec',str(spec)])==0
    result=json.loads(capsys.readouterr().out); path=result['run']
    assert cli(args+['run','advance','--file',path,'--result','feature delivered'])==0
    capsys.readouterr()
    assert cli(args+['run','check','--file',path,'--label','tests'])==0
    assert json.loads(capsys.readouterr().out)['status']=='passed'
    assert cli(args+['run','resume','--file',path])==0
    assert 'instruction' in json.loads(capsys.readouterr().out)


def test_install_cli_and_strict_doctor_agree(tmp_path,release,capsys):
    repo=tmp_path/'repo'; home=tmp_path/'home'; repo.mkdir(); home.mkdir(); fake=Native('omp')
    args=['--repo',str(repo),'--home',str(home)]
    kwargs={'runner':fake,'executables':{'omp':'/test/omp'}}
    assert cli(args+['install','--host','omp','--scope','project','--source',release['root'],'--yes'],**kwargs)==0
    assert json.loads(capsys.readouterr().out)['hosts']['omp']['state']=='installed'
    assert cli(args+['doctor','--host','omp','--strict','--json'],**kwargs)==0
    assert json.loads(capsys.readouterr().out)['hosts']['omp']['statically_valid']


@pytest.mark.parametrize('source_kind', ['checkout', 'installed'])
def test_install_defaults_to_running_engine(tmp_path,release,capsys,source_kind):
    repo=tmp_path/'repo'; home=tmp_path/'home'; repo.mkdir(); home.mkdir()
    plugin=PLUGIN if source_kind=='checkout' else Path(release['payload'])/'engine'
    fake=Native('omp')
    args=['--repo',str(repo),'--home',str(home),'install','--host','omp','--scope','project']
    kwargs={'plugin':plugin,'runner':fake,'executables':{'omp':'/test/omp'}}
    assert cli(args+['--dry-run'],**kwargs)==0
    preview=json.loads(capsys.readouterr().out)
    assert preview['state']=='planned' and preview['hosts']['omp']
    assert not list(home.iterdir()) and not list(repo.iterdir())
    assert cli(args+['--yes'],**kwargs)==0
    assert json.loads(capsys.readouterr().out)['hosts']['omp']['state']=='installed'
    assert cli(['--repo',str(repo),'--home',str(home),'doctor','--host','omp','--strict'],**kwargs)==0
    assert json.loads(capsys.readouterr().out)['hosts']['omp']['statically_valid']


def test_setup_preview_never_writes_home(tmp_path,capsys):
    repo=tmp_path/'repo'; repo.mkdir(); home=tmp_path/'home'; fake=Native('codex')
    assert cli(['--repo',str(repo),'--home',str(home),'setup','--host','codex','--dry-run'],runner=fake,executables={'codex':'/test/codex'})==0
    assert json.loads(capsys.readouterr().out)['state']=='planned' and not home.exists()


def test_setup_retains_short_commands_and_rejects_foreign_launcher(tmp_path,capsys):
    repo=tmp_path/'repo'; repo.mkdir(); home=tmp_path/'home'; fake=Native('codex')
    args=['--repo',str(repo),'--home',str(home),'setup','--host','codex','--yes']
    assert cli(args,runner=fake,executables={'codex':'/test/codex'})==0
    result=json.loads(capsys.readouterr().out)
    assert result['state']=='installed' and (home/'.local/bin/crux-flow').is_file()
    stamp=(home/'.local/bin/crux-flow').stat().st_mtime_ns
    assert cli(args,runner=fake,executables={'codex':'/test/codex'})==0
    capsys.readouterr(); assert (home/'.local/bin/crux-flow').stat().st_mtime_ns==stamp
    (home/'.local/bin/crux-flow').write_text('foreign tool')
    assert cli(args,runner=fake,executables={'codex':'/test/codex'})==2
    assert (home/'.local/bin/crux-flow').read_text()=='foreign tool'


def test_requested_missing_host_is_not_a_success(tmp_path,capsys):
    assert cli(['--repo',str(tmp_path),'doctor','--host','codex','--strict'],executables={})==2
    assert not json.loads(capsys.readouterr().out)['hosts']['codex']['present']


def test_update_skips_uninstalled_hosts_and_is_noop_when_current(tmp_path,release,capsys):
    repo=tmp_path/'repo'; home=tmp_path/'home'; repo.mkdir(); home.mkdir(); fake=Native('omp')
    args=['--repo',str(repo),'--home',str(home)]
    kwargs={'plugin':Path(release['payload'])/'engine','runner':fake,'executables':{'omp':'/test/omp'}}
    update=args+['update','--host','omp','--scope','project','--yes']
    assert cli(update,**kwargs)==2          # nothing installed: update never installs
    assert 'no requested compatible host' in capsys.readouterr().err
    assert not list(home.iterdir())
    assert cli(args+['install','--host','omp','--scope','project','--yes'],**kwargs)==0
    capsys.readouterr()
    assert cli(update,**kwargs)==0
    assert json.loads(capsys.readouterr().out)['hosts']['omp']['state']=='no-op'


def test_skills_use_the_flow_plugin_namespace():
    from crux.flow import hosts
    rendered=hosts.skills(PLUGIN,'codex')
    offenders=[name for name,raw in rendered.items() if name.endswith('SKILL.md') and b'/crux:' in raw]
    assert not offenders
    assert b'/crux-flow:run-promptbook' in rendered['run-promptbook/SKILL.md']


def test_version_check_detects_a_stale_launcher_until_setup_refreshes_it(tmp_path,capsys):
    repo=tmp_path/'repo'; home=tmp_path/'home'; repo.mkdir(); home.mkdir(); fake=Native('omp')
    args=['--repo',str(repo),'--home',str(home)]
    kwargs={'runner':fake,'executables':{'omp':'/test/omp'}}
    assert cli(args+['version','--check'],**kwargs)==2
    stale=json.loads(capsys.readouterr().out)
    assert stale['running_from']=='checkout' and stale['installed_launcher_release'] is None and stale['launcher_current'] is False
    assert cli(args+['setup','--host','omp','--yes'],**kwargs)==0
    capsys.readouterr()
    assert cli(args+['version','--check'],**kwargs)==0
    fresh=json.loads(capsys.readouterr().out)
    assert fresh['launcher_current'] is True and fresh['installed_launcher_release']==fresh['release_digest']


def _hook_repo(tmp_path,check_exit,dirty=False):
    import shutil,subprocess
    repo=tmp_path/'r'; (repo/'.githooks').mkdir(parents=True); (repo/'crux').mkdir()
    shutil.copy(Path(__file__).parents[2]/'.githooks/post-commit',repo/'.githooks/post-commit')
    log=tmp_path/'calls.log'
    stub=repo/'crux-flow'
    stub.write_text(f'#!/bin/sh\necho "$@" >> {log}\n[ "$1" = version ] && exit {check_exit}\nexit 0\n'); stub.chmod(0o755)
    (repo/'.githooks/post-commit').chmod(0o755)
    bin_=tmp_path/'bin'; bin_.mkdir(); (bin_/'uv').write_text('#!/bin/sh\n'); (bin_/'uv').chmod(0o755)
    env={**__import__('os').environ,'PATH':f'{bin_}:'+__import__('os').environ['PATH'],'GIT_AUTHOR_NAME':'t','GIT_AUTHOR_EMAIL':'t@t','GIT_COMMITTER_NAME':'t','GIT_COMMITTER_EMAIL':'t@t'}
    def git(*a): return subprocess.run(['git',*a],cwd=repo,env=env,capture_output=True,text=True,check=True)
    (repo/'crux/.keep').write_text(''); git('init','-q'); git('add','-A'); git('commit','-qm','scaffold'); git('config','core.hooksPath','.githooks')
    return repo,log,git
def _calls(log): return log.read_text().splitlines() if log.exists() else []


def test_post_commit_refreshes_only_a_stale_launcher_after_release_input_commits(tmp_path):
    repo,log,git=_hook_repo(tmp_path,check_exit=2)
    (repo/'README.md').write_text('x'); git('add','README.md'); git('commit','-qm','docs')
    assert _calls(log)==[]                                  # not a release input
    (repo/'crux/a.py').write_text('x'); git('add','crux'); git('commit','-qm','engine')
    assert _calls(log)==['version --check','setup --yes']   # stale: refreshed


def test_post_commit_leaves_current_or_dirty_installs_alone(tmp_path):
    (tmp_path/'a').mkdir(); repo,log,git=_hook_repo(tmp_path/'a',check_exit=0)
    (repo/'crux/a.py').write_text('x'); git('add','crux'); git('commit','-qm','engine')
    assert _calls(log)==['version --check']                 # already current: no setup
    (tmp_path/'b').mkdir(); repo,log,git=_hook_repo(tmp_path/'b',check_exit=2)
    (repo/'crux/a.py').write_text('x'); git('add','crux'); (repo/'crux/dirty.py').write_text('y')
    git('commit','-qm','engine')                            # untracked release input remains
    assert _calls(log)==['version --check']                 # stale but dirty: warn, never install
