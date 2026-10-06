from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess

import pytest

from crux.flow import testing, upstream
from crux.flow.common import canonical

PLUGIN = Path(__file__).resolve().parents[2] / 'crux'
pytestmark = pytest.mark.upstream


def git(root, *args):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True, check=True)
    return result.stdout.strip()


def history(tmp_path):
    original = tmp_path / 'upstream'; original.mkdir()
    git(original, 'init', '-q')
    git(original, 'config', 'user.name', 'Fixture')
    git(original, 'config', 'user.email', 'fixture@example.invalid')
    (original / 'seam.txt').write_text('original seam\n')
    git(original, 'add', '.'); git(original, 'commit', '-qm', 'baseline'); git(original, 'tag', 'v1.0.0')
    base = git(original, 'rev-parse', 'HEAD')
    fork = tmp_path / 'fork'
    subprocess.run(['git', 'clone', '-q', '--no-hardlinks', str(original), str(fork)], check=True)
    git(fork, 'config', 'user.name', 'Fixture'); git(fork, 'config', 'user.email', 'fixture@example.invalid')
    (fork / 'seam.txt').write_text('fork seam\n')
    definition = json.loads((PLUGIN / 'catalog/flow-policy.json').read_text())
    definition['identity'].update(upstream_commit=base, upstream_version='1.0.0', upstream_repository=str(original))
    target = fork / 'crux/catalog/flow-policy.json'; target.parent.mkdir(parents=True)
    target.write_bytes(canonical(definition))
    git(fork, 'add', '.'); git(fork, 'commit', '-qm', 'fork integration')
    return original, fork, base


def test_compatible_candidate_advances_its_recorded_upstream_baseline(tmp_path):
    original, fork, base = history(tmp_path)
    (original / 'added.txt').write_text('compatible update\n')
    git(original, 'add', '.'); git(original, 'commit', '-qm', 'compatible release'); git(original, 'tag', 'v1.0.1')
    expected = git(original, 'rev-parse', 'HEAD'); before = git(fork, 'rev-parse', 'HEAD')
    candidate = tmp_path / 'candidate'
    report = upstream.prepare(fork, candidate, ref='latest', repository=str(original), plugin=fork / 'crux')
    assert report['status'] == 'prepared'
    definition = json.loads((candidate / 'crux/catalog/flow-policy.json').read_text())
    assert definition['identity']['upstream_commit'] == expected
    assert definition['identity']['upstream_version'] == '1.0.1'
    assert report['commit'] == git(candidate, 'rev-parse', 'HEAD')
    assert git(fork, 'rev-parse', 'HEAD') == before
    assert git(fork, 'status', '--porcelain') == ''


def test_changed_integration_seam_refuses_candidate_without_modifying_fork(tmp_path):
    original, fork, base = history(tmp_path)
    (original / 'seam.txt').write_text('conflicting upstream seam\n')
    git(original, 'add', '.'); git(original, 'commit', '-qm', 'changed seam'); git(original, 'tag', 'v1.0.1')
    before = git(fork, 'rev-parse', 'HEAD')
    result = upstream.prepare(fork, tmp_path / 'candidate', ref='v1.0.1', repository=str(original), plugin=fork / 'crux')
    assert result['status'] == 'conflict' and result['activation'] == 'refused'
    assert git(fork, 'rev-parse', 'HEAD') == before
    assert git(fork, 'status', '--porcelain') == ''


def test_candidate_verification_never_recurses_into_update_integration_tests(tmp_path):
    commands = testing.commands(tmp_path)
    recipe = next(row['argv'] for row in commands if row['name'] == 'reference')
    assert recipe[recipe.index('-m', recipe.index('pytest')) + 1] == 'not upstream and not native and not provider'
    assert commands[-1]['name'] == 'package'
