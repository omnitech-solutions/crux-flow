from __future__ import annotations

import importlib
import os
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest

ROOT=Path(__file__).resolve().parents[2]; PLUGIN=ROOT/'crux'


def api():
    try: return importlib.import_module('crux.flow.packaging')
    except ModuleNotFoundError: pytest.fail('one release pipeline must package the actual runtime')


def test_portable_and_claude_manifests_share_one_engine(tmp_path):
    out=api().build(PLUGIN,tmp_path/'out')
    payload=Path(out['payload'])
    portable=json.loads((payload/'plugin.json').read_text())
    native=json.loads((payload/'.claude-plugin/plugin.json').read_text())
    assert portable['name']==native['name']=='crux-flow'
    assert 'agent-plugins.org' in portable['$schema']
    assert (payload/'engine/plugin.json').is_file()
    assert (payload/'engine/scripts/crux/flow/policy.py').is_file()
    assert not (payload/'engine/flow').exists()
    assert (payload/'skills/flow/SKILL.md').is_file()
    assert len(list((payload/'agents').glob('*.md')))==9
    assert api().inspect(Path(out['root']))['runtime_loaded']=='unobserved'


def test_release_is_reproducible_and_does_not_include_test_trees(tmp_path):
    first=api().build(PLUGIN,tmp_path/'one'); second=api().build(PLUGIN,tmp_path/'two')
    assert Path(first['archive']).read_bytes()==Path(second['archive']).read_bytes()
    with zipfile.ZipFile(first['archive']) as z:
        assert z.testzip() is None
        assert not any('/tests/' in name or '__pycache__' in name for name in z.namelist())


def test_packaged_cli_survives_source_relocation(tmp_path):
    out=api().build(PLUGIN,tmp_path/'out')
    moved=tmp_path/'moved'; shutil.move(out['root'],moved)
    done=subprocess.run([sys.executable,str(moved/'crux-flow/bin/crux-flow'),'mode','list'],cwd=tmp_path,capture_output=True,text=True)
    assert done.returncode==0,done.stderr
    assert json.loads(done.stdout)['default']=='aggressive'


def test_tampered_payload_cannot_validate(tmp_path):
    out=api().build(PLUGIN,tmp_path/'out'); payload=Path(out['payload'])
    (payload/'engine/scripts/crux/flow/policy.py').write_text('tampered')
    with pytest.raises(ValueError): api().inspect(Path(out['root']))


def test_running_packaged_engine_script_preserves_release_manifest(tmp_path):
    out=api().build(PLUGIN,tmp_path/'out')
    root=Path(out['root'])
    before=api().inspect(root)['digest']
    env=dict(os.environ)
    env.pop('PYTHONDONTWRITEBYTECODE',None)
    env.pop('PYTHONPYCACHEPREFIX',None)
    done=subprocess.run([sys.executable,str(Path(out['payload'])/'engine/scripts/bionic-config.py'),
                         '--repo-root',str(tmp_path)],env=env,capture_output=True,text=True)
    assert done.returncode==0,done.stderr
    assert json.loads(done.stdout)['docs_dir']=='bionic'
    assert api().inspect(root)['digest']==before
    assert not list(root.rglob('*.pyc'))


def test_unlisted_release_content_is_still_refused(tmp_path):
    out=api().build(PLUGIN,tmp_path/'out')
    root=Path(out['root'])
    extra=Path(out['payload'])/'engine/scripts/__pycache__/foreign.pyc'
    extra.parent.mkdir()
    extra.write_bytes(b'unlisted content')
    with pytest.raises(ValueError,match='release content set differs from manifest'):
        api().inspect(root)
    extra.unlink()
    assert not extra.exists()
    assert api().inspect(root)['statically_valid']


def test_zip_escape_and_symlink_are_refused(tmp_path):
    archive=tmp_path/'bad.zip'
    with zipfile.ZipFile(archive,'w') as z: z.writestr('../escape','x')
    with pytest.raises(ValueError): api().extract(archive,tmp_path/'extract')
    with zipfile.ZipFile(archive,'w') as z:
        info=zipfile.ZipInfo('link'); info.create_system=3; info.external_attr=0o120777<<16; z.writestr(info,'/etc/passwd')
    with pytest.raises(ValueError): api().extract(archive,tmp_path/'extract2')
    assert not (tmp_path/'escape').exists()


def test_source_provenance_does_not_invent_git_history(tmp_path,monkeypatch):
    monkeypatch.setattr(api().provenance,'git_head',lambda repo: None)
    out=api().build(PLUGIN,tmp_path/'out')
    meta=api().inspect(Path(out['root']))['release']
    assert meta['source_commit'] is None
    assert meta['source_kind']=='reconstructed-reference'
    assert len(meta['runtime_digest'])==64
