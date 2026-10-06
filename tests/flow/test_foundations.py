from __future__ import annotations

import importlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[2]


def module(name):
    assert importlib.util.find_spec('crux.flow') is not None, 'Flow must live inside the existing crux runtime package'
    return importlib.import_module('crux.flow.' + name)


def test_imports_share_the_runtime_namespace():
    flow = module('policy')
    council = importlib.import_module('crux.council.async_council')
    assert flow is not None and council is not None


@pytest.mark.parametrize('mode,budget,delegates,reviews', [('aggressive',20,0,1),('rapid',20,0,1),('balanced',60,2,2),('thorough',120,4,3),('upstream',None,None,None)])
def test_modes_are_real_and_preserve_requested_caps(tmp_path, mode, budget, delegates, reviews):
    p = module('policy').resolve(ROOT/'crux', tmp_path, tmp_path/'home', 'codex', {'mode':mode})
    assert p['mode'] == ('aggressive' if mode == 'rapid' else mode)
    assert p['budget_minutes'] == budget
    assert p['workflow']['delegates'] == delegates
    assert p['workflow']['reviewers'] == reviews
    assert p['observed_at_runtime'] == 'unobserved'


def test_default_precedence_and_project_isolation(tmp_path):
    home=tmp_path/'home'; one=tmp_path/'one'; two=tmp_path/'two'
    for x in (home,one,two): x.mkdir()
    p=module('policy')
    assert p.resolve(ROOT/'crux',two,home,'codex')['mode']=='aggressive'
    (home/'.crux-flow.yml').write_text('config_version: "1"\nmode: thorough\n')
    (one/'.crux-flow.yml').write_text('config_version: "1"\nmode: balanced\n')
    assert p.resolve(ROOT/'crux',one,home,'codex')['mode']=='balanced'
    assert p.resolve(ROOT/'crux',two,home,'codex')['mode']=='thorough'
    out=p.resolve(ROOT/'crux',one,home,'codex',{'mode':'aggressive','models':'strong'})
    assert out['mode']=='aggressive' and out['models']=='strong'
    assert out['sources']['mode']=='invocation'


@pytest.mark.parametrize('bad', ['config_version: "1"\nbudget_minutes: true\n', 'config_version: "1"\nmode: invalid\n','config_version: "1"\nsecurity: unrestricted\n', 'config_version: "1"\nmode: aggressive\nmode: thorough\n','config_version: "1"\nroles: &a {developer: *a}\n'])
def test_invalid_policy_never_silently_falls_back(tmp_path,bad):
    (tmp_path/'.crux-flow.yml').write_text(bad)
    with pytest.raises(ValueError): module('policy').resolve(ROOT/'crux',tmp_path,tmp_path/'home','codex')


def test_host_pin_does_not_leak(tmp_path):
    (tmp_path/'.crux-flow.yml').write_text('config_version: "1"\nroles:\n  developer:\n    codex:\n      model: verified-model\n      effort: low\n')
    p=module('policy')
    a=p.resolve(ROOT/'crux',tmp_path,tmp_path/'home','codex')
    b=p.resolve(ROOT/'crux',tmp_path,tmp_path/'home','claude')
    assert a['roles']['developer']['model']=='verified-model'
    assert b['roles']['developer']['model']!='verified-model'


def test_inspection_writes_nothing(tmp_path):
    before=list(tmp_path.rglob('*'))
    module('policy').resolve(ROOT/'crux',tmp_path,tmp_path/'absent','omp')
    assert list(tmp_path.rglob('*'))==before


def test_managed_noop_stale_rollback_and_foreign_edit(tmp_path):
    m=module('managed')
    plan=m.plan(tmp_path,{'a.txt':b'one'},owner='test')
    receipt=m.apply(plan)
    p=tmp_path/'a.txt'; stamp=p.stat().st_mtime_ns
    same=m.plan(tmp_path,{'a.txt':b'one'},owner='test')
    assert m.apply(same)['changed']==[]
    assert p.stat().st_mtime_ns==stamp
    stale=m.plan(tmp_path,{'a.txt':b'two'},owner='test')
    p.write_text('user changed')
    with pytest.raises(ValueError): m.apply(stale)
    with pytest.raises(ValueError): m.rollback(tmp_path,receipt['transaction_id'])
    assert p.read_text()=='user changed'


def test_managed_second_write_failure_restores_first(tmp_path,monkeypatch):
    m=module('managed'); (tmp_path/'a').write_text('original')
    p=m.plan(tmp_path,{'a':b'changed','b':b'new'},owner='test')
    original=m.write_file
    def fail(root,path,content,mode=0o600):
        if path=='b' and content==b'new': raise OSError('injected failure')
        return original(root,path,content,mode)
    monkeypatch.setattr(m,'write_file',fail)
    with pytest.raises(OSError): m.apply(p)
    assert (tmp_path/'a').read_text()=='original'
    assert not (tmp_path/'b').exists()


def test_managed_successful_rollback(tmp_path):
    m=module('managed'); (tmp_path/'a').write_bytes(b'old')
    receipt=m.apply(m.plan(tmp_path,{'a':b'new','b':b'created'},owner='test'))
    m.rollback(tmp_path,receipt['transaction_id'])
    assert (tmp_path/'a').read_bytes()==b'old' and not (tmp_path/'b').exists()


@pytest.mark.parametrize('path',['../escape','/absolute','a/../../escape','.crux-flow/transactions/fake'])
def test_managed_rejects_escape_and_own_journal_inputs(tmp_path,path):
    with pytest.raises(ValueError): module('managed').plan(tmp_path,{path:b'no'},owner='test')


def test_managed_symlink_ancestor_refused(tmp_path):
    outer=tmp_path/'outer'; outer.mkdir(); root=tmp_path/'repo'; root.mkdir()
    (root/'link').symlink_to(outer,target_is_directory=True)
    with pytest.raises((ValueError,OSError)): module('managed').plan(root,{'link/payload':b'no'},owner='test')
    assert list(outer.iterdir())==[]


def test_process_deadline_includes_stdin(tmp_path):
    start=time.monotonic()
    out=module('processes').execute([sys.executable,'-c','import time; time.sleep(4)'],cwd=tmp_path,timeout=0.1,input_bytes=b'x'*2_000_000)
    assert out.status=='timeout' and time.monotonic()-start<1.5


def test_process_returns_bounded_evidence_not_raw_public_output(tmp_path):
    out=module('processes').execute([sys.executable,'-c','print("PRIVATE"*100000)'],cwd=tmp_path,timeout=2,limit=1024)
    assert out.status=='output-limit'
    assert 'PRIVATE' not in json.dumps(out.public())


def test_process_success_and_failure(tmp_path):
    p=module('processes')
    ok=p.execute([sys.executable,'-c','print("ok")'],cwd=tmp_path,timeout=2)
    bad=p.execute([sys.executable,'-c','raise SystemExit(7)'],cwd=tmp_path,timeout=2)
    assert ok.status=='ok' and ok.stdout==b'ok\n'
    assert bad.status=='failed' and bad.exit_code==7
