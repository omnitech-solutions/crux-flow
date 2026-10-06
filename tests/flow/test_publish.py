from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest

from crux.flow import packaging, publishing
from crux.flow.common import FlowError
from crux.flow.processes import ProcessResult, execute
from test_lifecycle import PLUGIN


@pytest.fixture(autouse=True)
def git_identity(monkeypatch):
    for key, value in {'GIT_AUTHOR_NAME': 'T', 'GIT_AUTHOR_EMAIL': 't@example.test', 'GIT_COMMITTER_NAME': 'T', 'GIT_COMMITTER_EMAIL': 't@example.test',
                       'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_CONFIG_NOSYSTEM': '1'}.items(): monkeypatch.setenv(key, value)


def git(cwd, *args) -> str:
    return subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def remote(tmp_path) -> Path:
    bare = tmp_path / 'marketplace.git'; git(tmp_path, 'init', '--bare', '-q', '-b', 'main', str(bare))
    return bare


def publish(remote, **kwargs):
    return publishing.publish(PLUGIN, target=str(remote), allow_dirty=True, approve=lambda plan: True, github_release=False, **kwargs)


def refs(remote) -> str: return git(remote, 'for-each-ref')


def test_publish_pushes_a_complete_marketplace_that_is_itself_a_valid_release(remote, tmp_path):
    result = publish(remote)
    assert result['status'] == 'published' and result['remote_tag_verified'] and result['tag'] == 'v0.2.0'
    assert result['files']['added'] > 0 and result['install']['claude'][0].endswith(str(remote))
    clone = tmp_path / 'check'; git(tmp_path, 'clone', '-q', str(remote), str(clone))
    tree = tmp_path / 'tree'; shutil.copytree(clone, tree, ignore=shutil.ignore_patterns('.git'))
    assert packaging.inspect(tree)['statically_valid']            # the repository root is the marketplace
    assert (clone / '.claude-plugin/marketplace.json').is_file() and (clone / '.agents/plugins/marketplace.json').is_file()
    assert git(clone, 'tag', '--list') == 'v0.2.0' and 'Release crux-flow 0.2.0' in git(clone, 'log', '-1', '--format=%B')
    assert git(clone, 'cat-file', '-t', 'v0.2.0') == 'tag'        # annotated


def test_publishing_the_same_release_again_is_a_noop(remote):
    publish(remote); before = refs(remote)
    again = publish(remote)
    assert again['status'] == 'already-published' and refs(remote) == before


def test_dry_run_and_declined_approval_push_nothing(remote):
    planned = publishing.publish(PLUGIN, target=str(remote), allow_dirty=True, dry_run=True)
    assert planned['status'] == 'planned' and planned['files']['added'] > 0 and refs(remote) == ''
    with pytest.raises(FlowError, match='cancelled'):
        publishing.publish(PLUGIN, target=str(remote), allow_dirty=True, approve=lambda plan: False)
    assert refs(remote) == ''


def test_a_version_already_published_with_different_content_is_refused(remote, tmp_path):
    other = tmp_path / 'other'; git(tmp_path, 'clone', '-q', str(remote), str(other))
    (other / 'x.txt').write_text('different\n'); git(other, 'add', '-A'); git(other, 'commit', '-qm', 'other')
    git(other, 'tag', 'v0.2.0'); git(other, 'push', '-q', 'origin', 'HEAD:refs/heads/main', 'refs/tags/v0.2.0')
    before = refs(remote)
    with pytest.raises(FlowError, match='different content'): publish(remote)
    assert refs(remote) == before


def test_new_content_is_committed_on_top_of_the_existing_branch_without_force(remote, tmp_path):
    seed = tmp_path / 'seed'; git(tmp_path, 'clone', '-q', str(remote), str(seed))
    (seed / 'stale.txt').write_text('left from an older release\n'); git(seed, 'add', '-A'); git(seed, 'commit', '-qm', 'older')
    git(seed, 'push', '-q', 'origin', 'HEAD:refs/heads/main'); older = git(remote, 'rev-parse', 'main')
    result = publish(remote)
    assert result['status'] == 'published' and result['committed'] is True and result['files']['removed'] == 1
    assert git(remote, 'rev-list', '--count', 'main') == '2' and git(remote, 'merge-base', '--is-ancestor', older, 'main') == ''
    assert 'stale.txt' not in git(remote, 'ls-tree', '--name-only', 'main').split()


def test_a_new_version_with_unchanged_content_is_tagged_without_a_new_commit(remote, monkeypatch):
    publish(remote)
    monkeypatch.setattr(packaging, 'inspect', lambda root: _bump(_real_inspect(root)))
    result = publish(remote)
    assert result['status'] == 'published' and result['tag'] == 'v0.2.1' and result['committed'] is False
    assert git(remote, 'tag', '--list').split() == ['v0.2.0', 'v0.2.1'] and git(remote, 'rev-list', '--count', 'main') == '1'


_real_inspect = packaging.inspect


def _bump(data):
    return {**data, 'release': {**data['release'], 'identity': {**data['release']['identity'], 'version': '0.2.1'}}}


def test_a_dirty_source_checkout_is_refused_unless_allowed(tmp_path):
    repo = tmp_path / 'src'; repo.mkdir(); git(repo, 'init', '-q'); (repo / 'a').write_text('1'); git(repo, 'add', '-A'); git(repo, 'commit', '-qm', 'c')
    assert len(publishing.require_clean(repo)) == 40
    (repo / 'a').write_text('2')
    with pytest.raises(FlowError, match='uncommitted'): publishing.require_clean(repo)
    with pytest.raises(FlowError, match='Git checkout'): publishing.require_clean(tmp_path)


def test_remote_credentials_are_never_echoed(tmp_path):
    with pytest.raises(FlowError) as raised:
        publishing.publish(PLUGIN, target='https://user:supersecret@127.0.0.1:9/x.git', allow_dirty=True, approve=lambda p: True)
    assert 'supersecret' not in str(raised.value)
    assert publishing.redact('https://user:tok@github.com/o/n.git') == 'https://***@github.com/o/n.git'


def test_github_release_attaches_the_zip_when_gh_is_available(remote, monkeypatch):
    monkeypatch.setenv('GIT_CONFIG_COUNT', '1'); monkeypatch.setenv('GIT_CONFIG_KEY_0', f'url.{remote}.insteadOf')
    monkeypatch.setenv('GIT_CONFIG_VALUE_0', 'https://github.com/acme/crux-flow-marketplace.git')
    calls = []
    def runner(argv, **kwargs):
        if argv[0] == 'git': return execute(argv, **kwargs)
        calls.append(argv)
        if argv[1:3] == ['release', 'view']: return ProcessResult('failed', 1, 0, 0, b'', b'release not found')
        assert Path(argv[4]).is_file()                                   # the ZIP exists while gh runs
        return ProcessResult('ok', 0, 0, 0, b'', b'')
    result = publishing.publish(PLUGIN, target='https://github.com/acme/crux-flow-marketplace.git', allow_dirty=True,
                                approve=lambda p: True, runner=runner, gh='/fake/gh')
    assert result['status'] == 'published' and result['github_release'] == {'status': 'created', 'tag': 'v0.2.0'}
    create = calls[-1]
    assert create[3] == 'v0.2.0' and create[create.index('--repo') + 1] == 'acme/crux-flow-marketplace' and '--verify-tag' in create
    assert result['install']['claude'][0] == 'claude plugin marketplace add acme/crux-flow-marketplace'


def test_cli_publishes_with_one_command(remote, capsys):
    from crux.flow.cli import main
    assert main(['publish', '--to', str(remote), '--allow-dirty', '--no-github-release', '--yes']) == 0
    out = json.loads(capsys.readouterr().out)
    assert out['status'] == 'published' and git(remote, 'tag', '--list') == 'v0.2.0'
    assert main(['publish', '--yes']) == 2 and 'publish needs --to' in capsys.readouterr().err
