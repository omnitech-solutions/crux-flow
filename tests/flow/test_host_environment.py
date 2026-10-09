from __future__ import annotations

from pathlib import Path

from crux.flow import hosts


def test_claude_default_home_keeps_its_own_login(monkeypatch):
    # Naming Claude's default directory makes it look for a separate login, so a model turn ends "Not logged in".
    monkeypatch.delenv('CLAUDE_CONFIG_DIR', raising=False)
    assert 'CLAUDE_CONFIG_DIR' not in hosts.environment('claude', Path.home())


def test_claude_is_redirected_to_another_home(monkeypatch, tmp_path):
    monkeypatch.delenv('CLAUDE_CONFIG_DIR', raising=False)
    assert hosts.environment('claude', tmp_path)['CLAUDE_CONFIG_DIR'] == str(tmp_path / '.claude')
    chosen = tmp_path / 'elsewhere'
    assert hosts.environment('claude', Path.home(), host_home=chosen)['CLAUDE_CONFIG_DIR'] == str(chosen)


def test_claude_directory_set_by_the_caller_is_kept_consistent(monkeypatch):
    monkeypatch.setenv('CLAUDE_CONFIG_DIR', '/somewhere/else')
    assert hosts.environment('claude', Path.home())['CLAUDE_CONFIG_DIR'] == str(Path.home() / '.claude')
