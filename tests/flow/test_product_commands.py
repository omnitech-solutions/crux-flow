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
