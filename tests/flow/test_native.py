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
