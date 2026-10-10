from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from crux.flow import cli, evidence, hosts, lifecycle, managed, models, packaging, policy, records, workflow
from crux.flow.common import FlowError, canonical, identity
from crux.flow.processes import ProcessResult
from test_lifecycle import Native, release
from test_records import project, start

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / 'crux'


def install(tmp_path, release, host='omp'):
    repo = tmp_path / 'repo'
    home = tmp_path / 'home'
    repo.mkdir(); home.mkdir()
    native = Native(host)
    scope = 'project' if host == 'omp' else 'user'
    plan = lifecycle.plan_install(PLUGIN, Path(release['root']), repo=repo, home=home,
        host=host, scope=scope, executable='/test/' + host, runner=native)
    assert lifecycle.apply_install(plan, runner=native)['state'] == 'installed'
    return repo, home, native, scope


def test_source_checkout_has_executable_entrypoints(tmp_path):
    for name, arguments in [('crux-flow', ['mode', 'list']), ('crux-local', ['guide'])]:
        path = ROOT / name
        assert path.is_file(), 'standalone source checkout needs short launchers'
        result = subprocess.run([sys.executable, str(path), *arguments], cwd=tmp_path,
            capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, result.stderr
    result = subprocess.run([sys.executable, '-m', 'crux.flow', 'mode', 'list'], cwd=ROOT,
        env={**os.environ, 'PYTHONPATH': str(PLUGIN / 'scripts')}, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['default'] == 'aggressive'


def test_package_launcher_resolves_declared_dependencies(release):
    launcher = Path(release['payload']) / 'bin/crux-flow'
    assert launcher.read_text().splitlines()[0] == '#!/usr/bin/env -S uv run --script'


def test_model_rollback_cli_uses_transaction_status(tmp_path, capsys):
    result = managed.apply(managed.plan(tmp_path, {'.crux-flow/model-bindings.json': b'{}'}, owner='model-maintenance'))
    code = cli.main(['--repo', str(tmp_path), '--home', str(tmp_path / 'home'),
        'models', 'rollback', '--transaction', result['transaction_id'], '--yes'])
    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert not (tmp_path / '.crux-flow/model-bindings.json').exists()
    assert json.loads(captured.out)['status'] == 'rolled-back'


def test_failed_owned_check_returns_nonzero_cli_status(project, capsys):
    path = Path(workflow.start(PLUGIN, project, project / 'home', 'codex', goal='Failure test',
        outcomes=[{'id': 'delivery', 'outcome': 'Delivered', 'evidence': 'Observed'}],
        checks=[{'id': 'gate', 'argv': [sys.executable, '-c', 'raise SystemExit(3)'],
            'inputs': ['source.py'], 'toolchain': 'python'}])['run'])
    records.advance_file(path, outcome='done', result='Delivered', artifacts=[])
    result = cli.main(['--repo', str(project), 'run', 'check', '--file', str(path),
        '--label', 'gate', '--argv-json', json.dumps([sys.executable, '-c', 'raise SystemExit(3)'])])
    captured = capsys.readouterr()
    assert result != 0
    assert json.loads(captured.out)['status'] == 'failed'
    assert records.status(path)['status'] == 'ACTIVE'


def test_supporting_work_cannot_claim_it_unblocks_itself():
    with pytest.raises(FlowError):
        workflow._units([
            {'id': 'support', 'kind': 'supporting', 'outcome': 'Docs', 'evidence': 'Written', 'unblocks': 'support'},
            {'id': 'feature', 'outcome': 'Working feature', 'evidence': 'Observed'}],
            [{'id': 'tests', 'argv': ['python', '-V'], 'inputs': ['.'], 'toolchain': 'python'}], 'additive', None)


def test_council_attempt_requires_a_material_justification(project):
    path = Path(start(project)['run'])
    records.transition(path, mode='balanced', reason='Owner approved decision review', owner_authorized=True)
    before = path.read_bytes()
    with pytest.raises(FlowError):
        records.attempt(path, kind='council', invocation='unjustified')
    assert path.read_bytes() == before


def test_claude_delegation_targets_match_projected_role_names(tmp_path):
    effective = policy.resolve(PLUGIN, tmp_path, tmp_path / 'home', 'claude')
    rendered = hosts.roles(PLUGIN, effective)
    head = yaml.safe_load(rendered['files']['crux-flow-dev-lead.md'].split(b'---')[1])
    assert 'Agent(crux-flow-developer)' in head['tools']
    assert 'Agent(developer)' not in head['tools']
    assert 'do not start another run' in rendered['files']['crux-flow-developer.md'].decode().lower()


def test_release_inspection_checks_executable_modes(tmp_path, release):
    import shutil
    root = tmp_path / 'release'
    shutil.copytree(release['root'], root)
    (root / 'crux-flow/bin/crux-flow').chmod(0o777)
    with pytest.raises(FlowError):
        packaging.inspect(root)


def test_uninstall_refuses_an_approved_plan_that_changed(tmp_path, release, capsys):
    repo, home, native, scope = install(tmp_path, release)
    config = repo / '.omp/config.yml'
    def confirm(plan):
        config.write_text(config.read_text() + '\ntheme: owner-added\n')
        return True
    code = cli.main(['--repo', str(repo), '--home', str(home), 'uninstall',
        '--host', 'omp', '--scope', scope], runner=native, confirm=confirm)
    capsys.readouterr()
    assert code == 2
    assert (repo / '.omp/agents/crux-flow-developer.md').exists()
    assert 'owner-added' in config.read_text()


def test_installation_rollback_merges_only_owned_omp_aliases(tmp_path, release):
    repo, home, native, scope = install(tmp_path, release)
    role = repo / '.omp/agents/crux-flow-developer.md'
    original = role.read_bytes()
    (repo / '.crux-flow.yml').write_text('config_version: "1"\nmode: thorough\n')
    next_plan = lifecycle.plan_install(PLUGIN, Path(release['root']), repo=repo, home=home,
        host='omp', scope=scope, executable='/test/omp', runner=native)
    lifecycle.apply_install(next_plan, runner=native)
    config = repo / '.omp/config.yml'
    data = yaml.safe_load(config.read_text()); data['theme'] = 'owner-added'
    config.write_text(yaml.safe_dump(data))
    result = lifecycle.rollback_install(repo=repo, home=home, host='omp', scope=scope,
        authorized=True, runner=native)
    assert result['state'] == 'rolled-back'
    assert yaml.safe_load(config.read_text())['theme'] == 'owner-added'
    assert role.read_bytes() == original


def test_native_rollback_failure_restores_current_registration(tmp_path, release):
    repo, home, native, scope = install(tmp_path, release, 'claude')
    (repo / '.crux-flow.yml').write_text('config_version: "1"\nmode: thorough\n')
    plan = lifecycle.plan_install(PLUGIN, Path(release['root']), repo=repo, home=home,
        host='claude', scope=scope, executable='/test/claude', runner=native)
    lifecycle.apply_install(plan, runner=native)
    role = home / '.claude/agents/crux-flow-developer.md'; before = role.read_bytes()
    calls = 0
    def fails_once(argv, **kwargs):
        nonlocal calls
        if argv[1:3] == ['plugin', 'install']:
            calls += 1
            if calls == 1:
                return ProcessResult('failed', 1, 0, 0)
        return native(argv, **kwargs)
    result = lifecycle.rollback_install(repo=repo, home=home, host='claude', scope=scope,
        authorized=True, runner=fails_once)
    assert result['state'] == 'failed-recovered'
    assert native.active is not None
    assert role.read_bytes() == before


def test_recovery_cli_restores_a_prepared_transaction(tmp_path, capsys):
    transaction = 'a' * 32
    plan = managed.plan(tmp_path, {'target.txt': b'pending'}, owner='example')
    managed._save(tmp_path, managed._record(plan, transaction))
    managed.write_file(tmp_path, 'target.txt', b'pending')
    try:
        code = cli.main(['--repo', str(tmp_path), 'transactions', 'recover',
            '--transaction', transaction, '--yes'])
    except SystemExit:
        pytest.fail('recoverable transactions need a supported CLI recovery command')
    result = capsys.readouterr()
    assert code == 0, result.err
    assert json.loads(result.out)['status'] == 'recovered'
    assert not (tmp_path / 'target.txt').exists()


def test_model_source_scope_cannot_mutate_an_installed_payload(tmp_path, release):
    engine = Path(release['payload']) / 'engine'
    with pytest.raises(FlowError, match='scope|source|installed'):
        models._scope(engine, tmp_path, tmp_path / 'home', 'source')


def test_completed_run_history_rejects_late_findings(project):
    path = Path(start(project)['run'])
    records.advance_file(path, outcome='done', result='Observed delivery', artifacts=[])
    evidence.run_check(path, 'tests', [sys.executable, '-c', 'raise SystemExit(0)'])
    records.advance_file(path, outcome='done', result='Executed tests', artifacts=[])
    records.record_review(path, reviewer='separate-reviewer', evidence='Independent review attestation',
                         findings=[], independent=True)
    records.advance_file(path, outcome='done', result='Accepted independent review', artifacts=[])
    before = path.read_bytes()
    with pytest.raises(FlowError, match='terminal|completed'):
        records.finding(path, 'Late finding', dependency='non-blocking')
    assert path.read_bytes() == before


def test_mutation_lock_has_a_bounded_wait(tmp_path):
    import time
    with managed.locked(tmp_path):
        started = time.monotonic()
        with pytest.raises(FlowError, match='lock'):
            with managed.locked(tmp_path, timeout=0.03):
                pytest.fail('second writer entered an exclusive lock')
        assert time.monotonic() - started < 1


def test_whole_workspace_fingerprint_uses_git_input_boundary(project):
    subprocess.run(['git', 'init', '-q', str(project)], check=True)
    (project / '.gitignore').write_text('.venv/\n')
    (project / '.venv').mkdir()
    dependency = project / '.venv/dependency.txt'; dependency.write_text('first')
    path = Path(start(project)['run'])
    run = records.load(path)
    first = evidence.fingerprint(project, ['.'], run=run)
    dependency.write_text('second')
    assert evidence.fingerprint(project, ['.'], run=run) == first
    explicit = evidence.fingerprint(project, ['.venv/dependency.txt'], run=run)
    dependency.write_text('third')
    assert evidence.fingerprint(project, ['.venv/dependency.txt'], run=run) != explicit
    (project / 'consumed.md').write_text('changed public contract')
    assert evidence.fingerprint(project, ['.'], run=run) != first


def test_upstream_initialization_has_an_owned_finite_deadline(tmp_path, release):
    from crux.flow import initialization
    repo, home, native, scope = install(tmp_path, release, 'claude')
    (repo / '.crux-flow.yml').write_text('config_version: "1"\nmode: upstream\n')
    seen = []
    def run(argv, **kwargs):
        seen.append(kwargs['timeout'])
        return ProcessResult('failed', 1, 0, 0)
    result = initialization.initialize(PLUGIN, repo=repo, home=home, host='claude', scope=scope,
                                       runner=run, approve=lambda _: True)
    assert result['state'] == 'failed'
    assert 0 < seen[0] <= 3600


def test_packaged_guide_is_available_without_the_source_checkout(release):
    path = Path(release['payload']) / 'engine/FLOW_GUIDE.md'
    assert path.is_file()
    assert 'pnpm run setup' in path.read_text()


def test_default_package_command_is_repeatable(tmp_path, capsys):
    args = ['--repo', str(tmp_path), 'package']
    try:
        code = cli.main(args)
    except SystemExit:
        pytest.fail('source build needs a default repeatable package destination')
    assert code == 0
    first = json.loads(capsys.readouterr().out)
    archive = Path(first['archive']); stamp = archive.stat().st_mtime_ns
    assert cli.main(args) == 0
    second = json.loads(capsys.readouterr().out)
    assert second['archive'] == first['archive']
    assert second['sha256'] == first['sha256']
    assert archive.stat().st_mtime_ns == stamp


def test_materialization_refuses_changed_preferences_after_confirmation(tmp_path, capsys):
    home = tmp_path / 'home'; repo = tmp_path / 'repo'; repo.mkdir(); home.mkdir()
    def confirm(_):
        (repo / '.crux-flow.yml').write_text('config_version: "1"\nmode: thorough\n')
        return True
    code = cli.main(['--repo', str(repo), '--home', str(home), 'mode', 'materialize', '--host', 'codex'], confirm=confirm)
    capsys.readouterr()
    assert code == 2
    assert not (repo / '.codex/agents').exists()


def test_setup_stale_launcher_refuses_before_any_host_install(tmp_path):
    from crux.flow import setup
    repo = tmp_path / 'repo'; home = tmp_path / 'home'; repo.mkdir(); home.mkdir()
    native = Native('claude')
    def approve(_):
        target = home / '.local/bin/crux-flow'; target.parent.mkdir(parents=True)
        target.write_text('foreign command')
        return True
    with pytest.raises(FlowError):
        setup.setup(PLUGIN, repo=repo, home=home, selected={'claude':'/test/claude'}, runner=native, approve=approve)
    assert native.active is None
    assert (home / '.local/bin/crux-flow').read_text() == 'foreign command'


def test_status_uses_the_owned_custom_host_home(tmp_path, release):
    repo = tmp_path / 'repo'; home = tmp_path / 'home'; repo.mkdir(); home.mkdir()
    custom = home / 'custom-codex'; native = Native('codex')
    plan = lifecycle.plan_install(PLUGIN, Path(release['root']), repo=repo, home=home,
        host='codex', scope='user', host_home=custom, executable='/test/codex', runner=native)
    lifecycle.apply_install(plan, runner=native)
    report = lifecycle.status(repo=repo, home=home, host='codex', executable='/test/codex', runner=native)
    assert report['installed'] and report['statically_valid']


def test_fork_catalog_sources_are_explicitly_validated_without_regeneration(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location('flow_catalog_contract_test', PLUGIN / 'scripts/validate-catalog.py')
    validator = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = validator
    spec.loader.exec_module(validator)
    targets = getattr(validator, 'AUTHORED_CATALOG_JSON', None)
    assert targets is not None, 'authored JSON must be enrolled separately from generated catalogs'
    assert set(targets) == {'flow-policy.json', 'flow-bindings.json', 'flow-roles.json', 'flow-technology.json'}
    plugin = tmp_path / 'crux'; catalog = plugin / 'catalog'; catalog.mkdir(parents=True)
    target = catalog / 'flow-policy.json'
    target.write_text('{"schema_version": 77}')
    before = target.read_bytes()
    errors = targets['flow-policy.json'](target, {'plugin_dir':plugin})
    assert errors and target.read_bytes() == before
    assert all(error['file'] == 'catalog/flow-policy.json' for error in errors)


@pytest.mark.parametrize('change', ['unknown_field', 'zero_budget', 'invalid_alias'])
def test_shipped_mode_data_has_a_strict_schema(tmp_path, change):
    data = json.loads((PLUGIN / 'catalog/flow-policy.json').read_text())
    if change == 'unknown_field': data['modes']['aggressive']['unexpected'] = True
    if change == 'zero_budget': data['modes']['aggressive']['elapsed_minutes'] = 0
    if change == 'invalid_alias': data['aliases']['rapid'] = 'unknown'
    target = tmp_path / 'catalog/flow-policy.json'; target.parent.mkdir()
    target.write_text(json.dumps(data))
    with pytest.raises(FlowError):
        policy.load_definition(tmp_path)
