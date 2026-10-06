from __future__ import annotations

import os
from pathlib import Path
import shutil

import pytest

from crux.flow import lifecycle
from test_lifecycle import PLUGIN, release

pytestmark = pytest.mark.native


@pytest.mark.parametrize('host', ['claude', 'codex', 'opencode', 'omp'])
def test_actual_requested_native_installation_in_disposable_home(tmp_path, release, host):
    if os.environ.get('CRUX_FLOW_NATIVE_TESTS') != '1':
        pytest.skip('opt-in native installation observation')
    executable = shutil.which(host)
    if executable is None:
        pytest.skip('requested native host is not installed')
    repo = tmp_path / 'project'; home = tmp_path / 'home'; repo.mkdir(); home.mkdir()
    plan = lifecycle.plan_install(PLUGIN, Path(release['archive']), repo=repo, home=home,
                                  host=host, scope='user', executable=executable)
    result = lifecycle.apply_install(plan)
    assert result['state'] == 'installed', result
    report = lifecycle.status(repo=repo, home=home, host=host, executable=executable)
    assert report['installed'] and report['statically_valid'], report
    assert report['runtime_loaded'] == 'unobserved'
    removal = lifecycle.uninstall(repo=repo, home=home, host=host, scope='user', authorized=True)
    assert removal['state'] == 'uninstalled', removal


def _stub_upstream(root: Path) -> Path:
    """A minimal marketplace named `crux` offering a plugin `crux`, readable by both hosts."""
    import json
    (root / 'crux/.claude-plugin').mkdir(parents=True); (root / 'crux/.codex-plugin').mkdir(parents=True)
    (root / 'crux/skills/stub').mkdir(parents=True); (root / '.claude-plugin').mkdir(); (root / '.agents/plugins').mkdir(parents=True)
    manifest = {'name': 'crux', 'version': '3.25.1', 'description': 'stand-in for upstream Crux', 'skills': './skills/'}
    for kind in ('.claude-plugin', '.codex-plugin'): (root / 'crux' / kind / 'plugin.json').write_text(json.dumps(manifest))
    (root / 'crux/skills/stub/SKILL.md').write_text('---\nname: stub\ndescription: stand-in skill\n---\nstub\n')
    (root / '.claude-plugin/marketplace.json').write_text(json.dumps({'name': 'crux', 'owner': {'name': 'test'}, 'plugins': [{'name': 'crux', 'source': './crux', 'description': 'stub'}]}))
    (root / '.agents/plugins/marketplace.json').write_text(json.dumps({'name': 'crux', 'plugins': [{'name': 'crux', 'source': {'source': 'local', 'path': './crux'},
        'policy': {'installation': 'AVAILABLE', 'authentication': 'ON_INSTALL'}, 'category': 'Productivity'}]}))
    return root


@pytest.mark.parametrize('host', ['claude', 'codex'])
def test_real_host_selects_flow_per_repository_while_upstream_stays_installed(tmp_path, release, host, capsys):
    """Observed against the actual host CLI in a disposable home: project settings pick the workflow plugin."""
    import json
    import subprocess
    from crux.flow import cli, hosts
    if os.environ.get('CRUX_FLOW_NATIVE_TESTS') != '1':
        pytest.skip('opt-in native installation observation')
    executable = shutil.which(host)
    if executable is None:
        pytest.skip('requested native host is not installed')
    home = tmp_path / 'home'; repo = tmp_path / 'flow-repo'; other = tmp_path / 'upstream-repo'
    for path in (home, repo, other): path.mkdir()
    env = hosts.environment(host, home)
    stub = _stub_upstream(tmp_path / 'upstream-market')
    scope = ['--scope', 'user'] if host == 'claude' else []
    (home / f'.{host}').mkdir(exist_ok=True)
    for argv in ([executable, 'plugin', 'marketplace', 'add', str(stub), *scope],
                 [executable, 'plugin', 'install' if host == 'claude' else 'add', 'crux@crux', *scope]):
        outcome = subprocess.run(argv, env=env, cwd=home, capture_output=True, timeout=120)  # never assert on the call: failures would echo `env`
        assert outcome.returncode == 0, (argv[1:], outcome.stderr.decode()[-400:])
    if host == 'codex':
        with (home / '.codex/config.toml').open('a') as handle:
            for path in (repo, other): handle.write(f'\n[projects."{path.resolve()}"]\ntrust_level = "trusted"\n')

    def effective(cwd):
        rows = lifecycle.inventory(host, executable, cwd=cwd, home=home, runner=__import__('crux.flow.processes', fromlist=['execute']).execute)
        return {lifecycle._identifier(r): r.get('enabled') for r in rows}

    plan = lifecycle.plan_install(PLUGIN, Path(release['archive']), repo=repo, home=home, host=host, scope='user', executable=executable)
    assert plan.deferred and lifecycle.apply_install(plan)['state'] == 'installed'
    assert effective(other) == {'crux@crux': True, 'crux-flow@crux-flow': False}

    status = cli.main(['--repo', str(repo), '--home', str(home), 'init', '--host', host, '--yes', '--no-docs'], executables={host: executable})
    report = json.loads(capsys.readouterr().out)
    assert status == 0 and report['hosts'][host]['effective_in_repository'] is True, report
    assert effective(repo) == {'crux@crux': False, 'crux-flow@crux-flow': True}
    assert effective(other) == {'crux@crux': True, 'crux-flow@crux-flow': False}

    status = cli.main(['--repo', str(repo), '--home', str(home), 'deinit', '--host', host, '--yes'], executables={host: executable})
    capsys.readouterr()
    assert status == 0 and effective(repo) == {'crux@crux': True, 'crux-flow@crux-flow': False}


@pytest.mark.parametrize('host', ['claude', 'codex'])
def test_real_host_installs_the_published_marketplace(tmp_path, host, monkeypatch):
    """Publish to a local bare repository, then install from a checkout of it with the actual host CLI."""
    import subprocess
    from crux.flow import hosts, publishing
    if os.environ.get('CRUX_FLOW_NATIVE_TESTS') != '1':
        pytest.skip('opt-in native installation observation')
    executable = shutil.which(host)
    if executable is None:
        pytest.skip('requested native host is not installed')
    for key, value in {'GIT_AUTHOR_NAME': 'T', 'GIT_AUTHOR_EMAIL': 't@example.test', 'GIT_COMMITTER_NAME': 'T', 'GIT_COMMITTER_EMAIL': 't@example.test'}.items():
        monkeypatch.setenv(key, value)
    bare = tmp_path / 'marketplace.git'
    subprocess.run(['git', 'init', '--bare', '-q', '-b', 'main', str(bare)], check=True)
    assert publishing.publish(PLUGIN, target=str(bare), allow_dirty=True, approve=lambda plan: True, github_release=False)['status'] == 'published'
    checkout = tmp_path / 'checkout'; subprocess.run(['git', 'clone', '-q', str(bare), str(checkout)], check=True)
    home = tmp_path / 'home'; (home / f'.{host}').mkdir(parents=True)
    env = hosts.environment(host, home); scope = ['--scope', 'user'] if host == 'claude' else []
    for argv in ([executable, 'plugin', 'marketplace', 'add', str(checkout), *scope],
                 [executable, 'plugin', 'install' if host == 'claude' else 'add', 'crux-flow@crux-flow', *scope]):
        outcome = subprocess.run(argv, env=env, cwd=home, capture_output=True, timeout=120)
        assert outcome.returncode == 0, (argv[1:], outcome.stderr.decode()[-400:])
    rows = lifecycle.inventory(host, executable, cwd=home, home=home, runner=__import__('crux.flow.processes', fromlist=['execute']).execute)
    assert {lifecycle._identifier(r): r.get('enabled') for r in rows} == {'crux-flow@crux-flow': True}
