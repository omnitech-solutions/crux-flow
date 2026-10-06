from __future__ import annotations

import json
from pathlib import Path
import tomllib

import pytest

from test_lifecycle import PLUGIN, release  # noqa: F401  (module fixture)

HOSTS = ('claude', 'codex', 'opencode', 'omp')
EXES = {h: f'/test/bin/{h}' for h in HOSTS}


class World:
    """Disposable stand-ins for the four host CLIs.

    Plugin activation follows the semantics verified against the real hosts (see FLOW_SEAMS.md):
    project settings overlay user state, and Codex ignores project config in untrusted repositories.
    Nothing here touches a real installation."""
    VERSIONS = {'claude': b'2.1.291 (Claude Code)', 'codex': b'codex-cli 0.160.0', 'opencode': b'opencode v2.0.16', 'omp': b'omp/18.3.2'}

    def __init__(self, home: Path, *, upstream=True):
        self.home = home; self.market = {}; self.calls = []; self.fail_list = set(); self.docs_written = 0
        self.user = {h: {} for h in HOSTS}
        for host in ('claude', 'codex'):
            (home / f'.{host}').mkdir(parents=True, exist_ok=True)
            if upstream: self.user[host]['crux@crux'] = {'enabled': True, 'path': None}
        self.upstream = upstream

    # -- state helpers -----------------------------------------------------------------------
    def _user_config(self) -> Path: return self.home / '.codex/config.toml'

    def trust(self, repo: Path):
        with self._user_config().open('a') as handle: handle.write(f'\n[projects."{repo.resolve()}"]\ntrust_level = "trusted"\n')

    def _set_codex_flow(self, enabled):
        """Codex records plugin enablement in the user config: one table per plugin, replaced on re-install, dropped on removal."""
        import re
        path = self._user_config(); text = path.read_text() if path.exists() else ''
        text = re.sub(r'\n?\[plugins\."crux-flow@crux-flow"\]\nenabled = (?:true|false)\n', '\n', text)
        if enabled is not None: text += f'\n[plugins."crux-flow@crux-flow"]\nenabled = {"true" if enabled else "false"}\n'
        path.write_text(text)

    def _trusted(self, cwd: Path) -> bool:
        try: projects = tomllib.loads(self._user_config().read_text()).get('projects', {})
        except OSError: return False
        return any(projects.get(str(p), {}).get('trust_level') == 'trusted' for p in (cwd.resolve(), *cwd.resolve().parents))

    def _user_enabled(self, host: str, identifier: str) -> bool:
        if host == 'codex':
            try: table = tomllib.loads(self._user_config().read_text()).get('plugins', {}).get(identifier, {})
            except OSError: table = {}
            if 'enabled' in table: return table['enabled']
        return self.user[host][identifier]['enabled']

    def effective(self, host: str, cwd: Path) -> dict[str, bool]:
        state = {i: self._user_enabled(host, i) for i in self.user[host]}
        if host == 'claude':
            path = cwd / '.claude/settings.json'
            overlay = json.loads(path.read_text()).get('enabledPlugins', {}) if path.exists() else {}
        elif host == 'codex' and self._trusted(cwd):
            path = cwd / '.codex/config.toml'
            overlay = {k: v['enabled'] for k, v in tomllib.loads(path.read_text()).get('plugins', {}).items() if 'enabled' in v} if path.exists() else {}
        else: overlay = {}
        return {i: overlay.get(i, v) for i, v in state.items()}

    # -- the runner ----------------------------------------------------------------------------
    def __call__(self, argv, **kwargs):
        from crux.flow.processes import ProcessResult
        self.calls.append(list(argv))
        host = Path(argv[0]).name; cwd = Path(kwargs.get('cwd') or self.home)
        ok = lambda data=b'{}': ProcessResult('ok', 0, 0, len(data), data, b'')
        if '--version' in argv: return ok(self.VERSIONS[host])
        if '--help' in argv: return ok(b'plugin marketplace add remove install uninstall list --scope --json')
        if argv[1:4] == ['plugin', 'marketplace', 'add']: self.market[host] = Path(argv[4]); return ok()
        if argv[1:3] in (['plugin', 'add'], ['plugin', 'install']):
            self.user[host]['crux-flow@crux-flow'] = {'enabled': True, 'path': self.market[host] / 'crux-flow'}
            if host == 'codex': self._set_codex_flow(True)
            return ok()
        if argv[1:3] in (['plugin', 'remove'], ['plugin', 'uninstall']):
            self.user[host].pop('crux-flow@crux-flow', None)
            if host == 'codex': self._set_codex_flow(None)
            return ok()
        if argv[1:3] == ['plugin', 'disable']: self.user[host][argv[3]]['enabled'] = False; return ok()
        if argv[1:3] == ['plugin', 'list']:
            if host in self.fail_list: return ProcessResult('failed', 1, 0, 0, b'', b'private diagnostics')
            state = self.effective(host, cwd); rows = []
            for identifier, row in self.user[host].items():
                path = row['path']
                if host == 'claude':
                    item = {'id': identifier, 'enabled': state[identifier], 'scope': 'user', 'version': '1.0.0'}
                    if path: item.update(installPath=str(path), version=json.loads((path / 'plugin.json').read_text())['version'])
                else:
                    item = {'pluginId': identifier, 'name': identifier.split('@')[0], 'installed': True, 'enabled': state[identifier]}
                    if path: item['source'] = {'source': 'local', 'path': str(path)}
                rows.append(item)
            return ok(json.dumps(rows if host == 'claude' else {'installed': rows, 'available': []}).encode())
        if host == 'codex' and argv[1] == 'exec':
            docs = cwd / 'bionic'; docs.mkdir(exist_ok=True); self.docs_written += 1
            (docs / 'manifest.yml').write_text('schema_version: 5\nconcerns_enabled: [adrs]\n')
            for name in ('AGENTS.md', 'index.md', 'log.md', 'objectives.md'): (docs / name).write_text('# generated\n')
            return ok()
        return ok()


@pytest.fixture
def env(tmp_path, release):  # noqa: F811
    repo = tmp_path / 'repo'; home = tmp_path / 'home'; repo.mkdir(); home.mkdir()
    return repo, home, World(home), release


def machine_install(env, hosts=('claude', 'codex')):
    """What `crux-flow setup` does at user scope, driven through the real lifecycle against the fake hosts."""
    from crux.flow import lifecycle
    repo, home, world, release = env
    for host in hosts:
        plan = lifecycle.plan_install(PLUGIN, Path(release['root']), repo=repo, home=home, host=host, scope='user', executable=EXES[host], runner=world)
        assert lifecycle.apply_install(plan, runner=world)['state'] == 'installed'


def cli(env, *argv, installed=HOSTS, capsys=None, code=None):
    from crux.flow.cli import main
    repo, home, world, _ = env
    status = main(['--repo', str(repo), '--home', str(home), *argv], runner=world, executables={h: EXES[h] for h in installed})
    out = capsys.readouterr().out if capsys else ''
    if code is not None: assert status == code, out
    return status, (json.loads(out) if out.strip().startswith('{') else out)


def snapshot(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns) for p in sorted(root.rglob('*'))
            if p.is_file() and '.crux-flow/transactions' not in p.as_posix() and '.crux-flow/locks' not in p.as_posix()}


# ---- host-agnostic onboarding ---------------------------------------------------------------------

def test_init_configures_claude_and_codex_without_naming_a_host(env, capsys):
    machine_install(env)
    repo, home, world, _ = env
    world.trust(repo)
    _, out = cli(env, 'init', '--yes', '--no-docs', installed=('claude', 'codex'), capsys=capsys, code=0)
    assert out['status'] == 'completed' and out['mode'] == 'aggressive'
    assert out['hosts']['claude']['status'] == 'configured' and out['hosts']['codex']['status'] == 'configured'
    assert out['hosts']['opencode']['status'] == 'skipped-not-installed' and out['hosts']['omp']['status'] == 'skipped-not-installed'
    assert out['hosts']['codex']['runtime_loaded'] == 'unobserved' and out['hosts']['codex']['requires_restart'] is True
    assert (repo / '.crux-flow.yml').read_text() == 'config_version: "1"\nmode: aggressive\n'
    for host in ('claude', 'codex'):
        state = world.effective(host, repo)
        assert state['crux-flow@crux-flow'] is True and state['crux@crux'] is False
        assert list((repo / f'.{host}/agents').glob('crux*flow*'))


@pytest.mark.parametrize('only', ['claude', 'codex', 'opencode', 'omp'])
def test_no_host_requires_another_host(only, env, capsys):
    machine_install(env, hosts=(only,) if only in ('claude', 'codex') else ())
    env[2].trust(env[0])
    _, out = cli(env, 'init', '--yes', '--no-docs', installed=(only,), capsys=capsys, code=0)
    assert out['status'] == 'completed' and out['hosts'][only]['status'] == 'configured'
    assert sorted(h for h, r in out['hosts'].items() if r['status'] == 'skipped-not-installed') == sorted(h for h in HOSTS if h != only)


def test_host_filter_limits_the_operation_without_touching_other_hosts(env, capsys):
    machine_install(env)
    repo, _, world, _ = env
    _, out = cli(env, 'init', '--host', 'codex', '--yes', '--no-docs', capsys=capsys, code=0)
    assert set(out['hosts']) == {'codex', 'claude', 'opencode', 'omp'} or set(out['hosts']) == {'codex'}
    assert out['hosts']['codex']['status'] == 'configured'
    assert not (repo / '.claude').exists()


def test_rerunning_init_is_a_noop_that_preserves_content_and_mtimes(env, capsys):
    machine_install(env)
    repo, _, _, _ = env
    cli(env, 'init', '--yes', '--no-docs', installed=('claude', 'codex'), capsys=capsys, code=0)
    before = snapshot(repo)
    _, out = cli(env, 'init', '--yes', '--no-docs', installed=('claude', 'codex'), capsys=capsys, code=0)
    assert out['hosts']['claude']['status'] == 'already-correct' and out['hosts']['codex']['status'] == 'already-correct'
    assert out['hosts']['claude']['requires_restart'] is False
    assert snapshot(repo) == before


# ---- official project-scoped plugin selection -----------------------------------------------------

def test_codex_activation_is_per_repository_and_other_repositories_stay_on_upstream(env, capsys, tmp_path):
    machine_install(env)
    repo, _, world, _ = env
    other = tmp_path / 'other'; other.mkdir(); world.trust(repo); world.trust(other)
    # Machine setup alone must leave every repository on exactly one workflow plugin: upstream.
    assert world.effective('codex', other) == {'crux@crux': True, 'crux-flow@crux-flow': False}
    cli(env, 'init', '--host', 'codex', '--yes', '--no-docs', capsys=capsys, code=0)
    assert world.effective('codex', repo) == {'crux@crux': False, 'crux-flow@crux-flow': True}
    assert world.effective('codex', other) == {'crux@crux': True, 'crux-flow@crux-flow': False}
    config = tomllib.loads((repo / '.codex/config.toml').read_text())['plugins']
    assert config['crux-flow@crux-flow']['enabled'] is True and config['crux@crux']['enabled'] is False
    assert not (other / '.codex').exists()


def test_claude_activation_uses_project_settings(env, capsys, tmp_path):
    machine_install(env)
    repo, _, world, _ = env
    other = tmp_path / 'other'; other.mkdir()
    cli(env, 'init', '--host', 'claude', '--yes', '--no-docs', capsys=capsys, code=0)
    assert json.loads((repo / '.claude/settings.json').read_text())['enabledPlugins'] == {'crux-flow@crux-flow': True, 'crux@crux': False}
    assert world.effective('claude', repo) == {'crux@crux': False, 'crux-flow@crux-flow': True}
    assert world.effective('claude', other) == {'crux@crux': True, 'crux-flow@crux-flow': False}


def test_codex_untrusted_repository_reports_that_project_settings_are_ignored(env, capsys):
    machine_install(env)
    _, out = cli(env, 'init', '--host', 'codex', '--yes', '--no-docs', capsys=capsys, code=0)
    assert out['hosts']['codex']['trusted_project'] is not True
    assert out['hosts']['codex']['effective_in_repository'] is False and 'trusted' in out['hosts']['codex']['attention']


def test_unrelated_plugin_settings_are_preserved(env, capsys):
    machine_install(env)
    repo, _, world, _ = env
    (repo / '.codex').mkdir(); (repo / '.claude').mkdir()
    toml = '# mine\nmodel = "x"\n\n[plugins."github@openai-curated"]\nenabled = true\n'
    settings = '{"permissions": {"allow": ["Bash(ls)"]}, "enabledPlugins": {"other@market": true}}\n'
    (repo / '.codex/config.toml').write_text(toml); (repo / '.claude/settings.json').write_text(settings)
    cli(env, 'init', '--yes', '--no-docs', installed=('claude', 'codex'), capsys=capsys, code=0)
    parsed = tomllib.loads((repo / '.codex/config.toml').read_text())
    assert parsed['model'] == 'x' and parsed['plugins']['github@openai-curated']['enabled'] is True
    assert '# mine' in (repo / '.codex/config.toml').read_text()
    data = json.loads((repo / '.claude/settings.json').read_text())
    assert data['permissions'] == {'allow': ['Bash(ls)']} and data['enabledPlugins']['other@market'] is True


def test_locally_edited_activation_is_never_silently_overwritten(env, capsys):
    machine_install(env)
    repo, _, _, _ = env
    cli(env, 'init', '--yes', '--no-docs', installed=('claude', 'codex'), capsys=capsys, code=0)
    path = repo / '.codex/config.toml'; path.write_text(path.read_text().replace('enabled = false', 'enabled = true'))
    edited = path.read_bytes()
    status, out = cli(env, 'init', '--yes', '--no-docs', installed=('claude', 'codex'), capsys=capsys)
    assert status == 2 and out['status'] == 'partial'
    assert out['hosts']['codex']['status'] == 'failed' and 'locally' in out['hosts']['codex']['reason']
    assert out['hosts']['claude']['status'] == 'already-correct'
    assert path.read_bytes() == edited


def test_one_failing_host_does_not_corrupt_another_and_overall_status_is_not_success(env, capsys):
    machine_install(env)
    repo, _, world, _ = env
    world.fail_list.add('claude')
    status, out = cli(env, 'init', '--yes', '--no-docs', installed=('claude', 'codex'), capsys=capsys)
    assert status == 2 and out['status'] == 'partial'
    assert out['hosts']['claude']['status'] == 'failed' and out['hosts']['codex']['status'] == 'configured'
    assert not (repo / '.claude').exists()
    world.fail_list.clear(); world.trust(repo)
    assert world.effective('codex', repo)['crux@crux'] is False


def test_upstream_mode_is_flow_policy_not_plugin_activation(env, capsys):
    machine_install(env)
    repo, _, world, _ = env
    world.trust(repo)
    _, out = cli(env, 'init', '--mode', 'upstream', '--yes', '--no-docs', installed=('claude', 'codex'), capsys=capsys, code=0)
    assert out['mode'] == 'upstream' and 'mode: upstream' in (repo / '.crux-flow.yml').read_text()
    for host in ('claude', 'codex'):
        assert world.effective(host, repo) == {'crux@crux': False, 'crux-flow@crux-flow': True}


def test_setup_without_upstream_leaves_flow_active_and_writes_no_project_files(env):
    env[2].upstream = False
    for host in ('claude', 'codex'): env[2].user[host].pop('crux@crux', None)
    machine_install(env)
    assert env[2].effective('claude', env[0])['crux-flow@crux-flow'] is True


# ---- project-surface hosts ------------------------------------------------------------------------

def test_opencode_uses_its_project_agent_and_skill_directories(env, capsys):
    repo, _, _, _ = env
    _, out = cli(env, 'init', '--yes', '--no-docs', installed=('opencode',), capsys=capsys, code=0)
    assert out['hosts']['opencode']['status'] == 'configured'
    assert (repo / '.opencode/agents/crux-flow-developer.md').is_file()
    assert any((repo / '.opencode/skills').glob('crux-flow-*/SKILL.md'))
    assert not (repo / '.claude').exists() and not (repo / '.codex').exists()


def test_omp_aliases_come_from_policy_and_never_overwrite_user_pins(env, capsys):
    import yaml
    repo, _, _, _ = env
    (repo / '.omp').mkdir()
    (repo / '.omp/config.yml').write_text('theme: mine\nmodelRoles:\n  default: my/pinned-model\n')
    cli(env, 'init', '--yes', '--no-docs', installed=('omp',), capsys=capsys, code=0)
    config = yaml.safe_load((repo / '.omp/config.yml').read_text())
    assert config['theme'] == 'mine' and config['modelRoles']['default'] == 'my/pinned-model'
    assert {'smol', 'slow'} <= set(config['modelRoles'])


# ---- docs ---------------------------------------------------------------------------------------

def test_init_docs_needs_no_host_and_runs_one_native_turn(env, capsys):
    machine_install(env, hosts=('codex',))
    repo, _, world, _ = env
    cli(env, 'init', '--yes', '--no-docs', installed=('codex',), capsys=capsys, code=0)
    _, out = cli(env, 'init-docs', '--yes', installed=('claude', 'codex'), capsys=capsys, code=0)
    assert out['state'] == 'initialized' and out['host'] == 'codex' and world.docs_written == 1
    assert (repo / 'bionic/manifest.yml').is_file()


def test_init_initializes_docs_when_absent_and_skips_when_present(env, capsys):
    machine_install(env, hosts=('codex',))
    repo, _, world, _ = env
    _, out = cli(env, 'init', '--yes', installed=('codex',), capsys=capsys, code=0)
    assert out['docs']['status'] == 'initialized' and world.docs_written == 1
    _, again = cli(env, 'init', '--yes', installed=('codex',), capsys=capsys, code=0)
    assert again['docs']['status'] == 'already-initialized' and world.docs_written == 1


# ---- inspection -----------------------------------------------------------------------------------

@pytest.mark.parametrize('command', ['status', 'doctor'])
def test_status_and_doctor_inspect_every_host_by_default(command, env, capsys):
    machine_install(env)
    _, out = cli(env, command, installed=('claude', 'codex'), capsys=capsys, code=0)
    assert set(out['hosts']) == set(HOSTS)
    assert out['hosts']['claude']['present'] and out['hosts']['codex']['present']
    assert out['hosts']['opencode']['present'] is False and out['hosts']['omp']['present'] is False


def test_models_and_mode_views_span_all_hosts_by_default(env, capsys):
    _, out = cli(env, 'mode', 'show', '--resolved', installed=('codex', 'omp'), capsys=capsys, code=0)
    assert set(out['hosts']) == {'codex', 'omp'}
    _, out = cli(env, 'models', 'resolve', installed=('claude',), capsys=capsys, code=0)
    assert set(out['hosts']) == {'claude'}


def test_mode_materialize_without_a_host_projects_every_detected_host(env, capsys):
    repo, _, _, _ = env
    _, out = cli(env, 'mode', 'materialize', '--yes', installed=('opencode', 'omp'), capsys=capsys, code=0)
    assert out['status'] == 'completed' and out['hosts']['opencode']['status'] == 'configured' and out['hosts']['omp']['status'] == 'configured'
    assert out['hosts']['claude']['status'] == 'skipped-not-installed'
    assert (repo / '.opencode/agents').is_dir() and (repo / '.omp/agents').is_dir()


# ---- deactivation ---------------------------------------------------------------------------------

def test_deinit_restores_only_flow_owned_settings_and_keeps_project_knowledge(env, capsys):
    machine_install(env)
    repo, _, world, _ = env
    (repo / '.codex').mkdir(); (repo / '.claude').mkdir(); (repo / 'bionic').mkdir()
    toml = '# mine\n[plugins."github@openai-curated"]\nenabled = true\n'
    settings = '{\n  "enabledPlugins": {\n    "other@market": true\n  }\n}\n'
    (repo / '.codex/config.toml').write_text(toml); (repo / '.claude/settings.json').write_text(settings)
    (repo / 'bionic/notes.md').write_text('project knowledge\n')
    cli(env, 'init', '--yes', '--no-docs', installed=('claude', 'codex'), capsys=capsys, code=0)
    _, out = cli(env, 'deinit', '--yes', installed=('claude', 'codex'), capsys=capsys, code=0)
    assert out['status'] == 'completed' and out['hosts']['codex']['status'] == 'removed' and out['hosts']['claude']['status'] == 'removed'
    assert (repo / '.codex/config.toml').read_text() == toml and (repo / '.claude/settings.json').read_text() == settings
    assert (repo / 'bionic/notes.md').read_text() == 'project knowledge\n'
    assert not (repo / '.crux-flow.yml').exists() and not list((repo / '.codex').glob('agents/*'))
    assert world.effective('codex', repo)['crux@crux'] is True


def test_deinit_keeps_edited_project_configuration_and_refuses_edited_activation(env, capsys):
    machine_install(env)
    repo, _, _, _ = env
    cli(env, 'init', '--yes', '--no-docs', installed=('claude', 'codex'), capsys=capsys, code=0)
    (repo / '.crux-flow.yml').write_text('config_version: "1"\nmode: thorough\n')
    path = repo / '.claude/settings.json'; path.write_text(path.read_text().replace('"crux@crux": false', '"crux@crux": true'))
    status, out = cli(env, 'deinit', '--yes', installed=('claude', 'codex'), capsys=capsys)
    assert status == 2 and out['hosts']['claude']['status'] == 'failed' and out['hosts']['codex']['status'] == 'removed'
    assert 'mode: thorough' in (repo / '.crux-flow.yml').read_text()


def test_project_uninstall_is_the_same_outcome_as_deinit(env, capsys):
    machine_install(env)
    repo, _, _, _ = env
    cli(env, 'init', '--yes', '--no-docs', installed=('claude', 'codex'), capsys=capsys, code=0)
    _, out = cli(env, 'uninstall', '--scope', 'project', '--yes', installed=('claude', 'codex'), capsys=capsys, code=0)
    assert out['hosts']['codex']['status'] == 'removed'
    assert not (repo / '.codex/config.toml').exists() and not (repo / '.claude/settings.json').exists()


def test_deinit_of_an_uninitialized_repository_is_a_noop(env, capsys):
    _, out = cli(env, 'deinit', '--yes', installed=('claude', 'codex', 'opencode', 'omp'), capsys=capsys, code=0)
    assert all(r['status'] == 'already-correct' for r in out['hosts'].values())


def test_user_scope_uninstall_across_hosts_and_rollback_need_no_host(env, capsys):
    machine_install(env)
    _, out = cli(env, 'uninstall', '--yes', installed=('claude', 'codex'), capsys=capsys, code=0)
    assert out['hosts']['claude']['status'] == 'completed' and out['hosts']['codex']['status'] == 'completed'


# ---- the seam adapter itself -------------------------------------------------------------------------

def test_activation_adapter_refuses_ambiguous_flow_installations(tmp_path):
    from crux.flow import repository
    from crux.flow.common import FlowError
    with pytest.raises(FlowError, match='ambiguous'): repository.select(['crux-flow@a', 'crux-flow@b', 'crux@crux'])
    with pytest.raises(FlowError, match='not installed'): repository.select(['crux@crux'])
    assert repository.select(['crux-flow@x', 'crux@y', 'crux@z', 'figma@w']) == ('crux-flow@x', ['crux@y', 'crux@z'])


def test_activation_adapter_rejects_invalid_toml_and_unsupported_layouts(tmp_path):
    from crux.flow import repository
    from crux.flow.common import FlowError
    (tmp_path / '.codex').mkdir()
    (tmp_path / '.codex/config.toml').write_text('this is = = not toml\n')
    with pytest.raises(FlowError, match='TOML'): repository.plan('codex', tmp_path, 'crux-flow@m', ['crux@m'])
    (tmp_path / '.codex/config.toml').write_text('[plugins]\n"crux@m" = { enabled = true }\n')
    with pytest.raises(FlowError, match='unsupported'): repository.plan('codex', tmp_path, 'crux-flow@m', ['crux@m'])


def test_host_version_parsing_accepts_every_real_host_format(tmp_path):
    from crux.flow import hosts
    world = World(tmp_path)
    for host in HOSTS:
        found = hosts.probe(host, home=tmp_path, repo=tmp_path, executable=EXES[host], runner=world)
        assert found['present'] and found['version'] in {'2.1.291', '0.160.0', '2.0.16', '18.3.2'}


@pytest.mark.parametrize('host', ['opencode', 'omp'])
def test_project_surface_hosts_rerun_is_a_noop_and_deinit_removes_only_owned_files(host, env, capsys):
    import yaml
    repo, _, _, _ = env
    base = repo / f'.{host}'; (base / 'agents').mkdir(parents=True)
    (base / 'agents/mine.md').write_text('my own agent\n')
    if host == 'omp': (base / 'config.yml').write_text('theme: mine\nmodelRoles:\n  default: my/pinned-model\n')
    cli(env, 'init', '--yes', '--no-docs', installed=(host,), capsys=capsys, code=0)
    before = snapshot(repo)
    _, again = cli(env, 'init', '--yes', '--no-docs', installed=(host,), capsys=capsys, code=0)
    assert again['hosts'][host]['status'] == 'already-correct' and again['hosts'][host]['requires_restart'] is False
    assert snapshot(repo) == before
    _, out = cli(env, 'deinit', '--yes', installed=(host,), capsys=capsys, code=0)
    assert out['hosts'][host]['status'] == 'removed'
    assert (base / 'agents/mine.md').read_text() == 'my own agent\n'
    assert not list((base / 'agents').glob('crux-flow-*')) and not (repo / '.crux-flow.yml').exists()
    if host == 'omp': assert yaml.safe_load((base / 'config.yml').read_text()) == {'theme': 'mine', 'modelRoles': {'default': 'my/pinned-model'}}


@pytest.mark.parametrize('host', ['claude', 'codex'])
def test_rollback_keeps_flow_inert_at_user_scope_while_upstream_is_enabled(host, env, capsys, tmp_path):
    from crux.flow import lifecycle
    repo, home, world, release = env
    machine_install(env, hosts=(host,))
    (home / '.crux-flow.yml').write_text('config_version: "1"\nmode: thorough\n')
    plan = lifecycle.plan_install(PLUGIN, Path(release['root']), repo=repo, home=home, host=host, scope='user', executable=EXES[host], runner=world)
    assert plan.deferred and lifecycle.apply_install(plan, runner=world)['state'] == 'installed'
    _, out = cli(env, 'rollback', '--yes', installed=(host,), capsys=capsys, code=0)
    assert out['hosts'][host]['status'] == 'completed'
    other = tmp_path / 'other'; other.mkdir()
    assert world.effective(host, other) == {'crux@crux': True, 'crux-flow@crux-flow': False}


def test_install_command_no_longer_exposes_a_side_by_side_override(env):
    from crux.flow.cli import parser
    with pytest.raises(SystemExit): parser().parse_args(['install', '--source', 'x', '--allow-side-by-side'])


def test_setup_installs_every_detected_host_in_one_run(env, capsys):
    """Hosts share one retained release; a later host must not see the first host's writes as a stale plan."""
    repo, home, world, _ = env
    _, out = cli(env, 'setup', '--yes', installed=HOSTS, capsys=capsys, code=0)
    assert out['state'] == 'installed'
    assert {h: r['state'] for h, r in out['hosts'].items()} == {h: 'installed' for h in HOSTS}
    assert out['user_activation'] == {'inert_until_repository_init': ['claude', 'codex']}
    assert world.effective('claude', repo)['crux@crux'] is True and world.effective('claude', repo)['crux-flow@crux-flow'] is False


def test_init_configures_both_project_surface_hosts_in_one_run_when_config_exists(env, capsys):
    repo, _, _, _ = env
    (repo / '.crux-flow.yml').write_text('config_version: "1"\nmode: balanced\n')
    _, out = cli(env, 'init', '--yes', '--no-docs', installed=('opencode', 'omp'), capsys=capsys, code=0)
    assert out['hosts']['opencode']['status'] == 'configured' and out['hosts']['omp']['status'] == 'configured'


def test_a_failed_host_reports_its_reason(env, capsys):
    machine_install(env, hosts=('codex',))
    env[2].fail_list.add('codex')
    status, out = cli(env, 'init', '--yes', '--no-docs', installed=('codex',), capsys=capsys)
    assert status == 2 and out['hosts']['codex']['status'] == 'failed' and out['hosts']['codex']['reason']
