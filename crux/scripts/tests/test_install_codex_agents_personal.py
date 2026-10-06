"""Personal and explicit-project installation contract for Codex agents."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
import tomllib


REPO_ROOT = Path(__file__).resolve().parents[3]
INSTALLER = REPO_ROOT / "crux" / "skills" / "install-codex-agents" / "scripts" / "install.py"
SCRIPTS_DIR = REPO_ROOT / "crux" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
import codex_agents as agents  # noqa: E402


class PersonalInstallTests(unittest.TestCase):
    def run_install(self, *args: str, executable: Path = INSTALLER, env=None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(executable), *args], capture_output=True, text=True, check=False, timeout=10, env=env,
        )

    def test_no_argument_install_uses_the_active_home_without_touching_real_home(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary) / "home"
            home.mkdir()
            result = self.run_install(env={**os.environ, "HOME": str(home)})
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue((home / ".codex" / "agents" / "crux-architect.toml").is_file())

    def test_default_scope_can_use_an_isolated_codex_home_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            codex_home = Path(temporary) / "codex-home"
            first = self.run_install("--codex-home", str(codex_home))
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            first_payload = json.loads(first.stdout)
            self.assertEqual(first_payload["scope"], "personal")
            self.assertEqual(Path(first_payload["target"]), (codex_home / "agents").resolve())
            self.assertEqual(first_payload["drift"]["status"], "clean")
            installed = sorted((codex_home / "agents").glob("crux-*.toml"))
            before = {path.name: path.stat().st_mtime_ns for path in installed}
            self.assertEqual(len(before), 10)
            time.sleep(0.01)
            second = self.run_install("--codex-home", str(codex_home))
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertEqual(json.loads(second.stdout)["written"], [])
            self.assertEqual(
                {path.name: path.stat().st_mtime_ns for path in installed}, before
            )

    def test_personal_install_pins_the_reviewer_model_and_effort(self):
        with tempfile.TemporaryDirectory() as temporary:
            codex_home = Path(temporary) / "codex-home"
            result = self.run_install("--codex-home", str(codex_home))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            reviewer = tomllib.loads(
                (codex_home / "agents" / "crux-reviewer.toml").read_text(encoding="utf-8")
            )
        self.assertEqual(reviewer["model"], "gpt-6.1-sol")
        self.assertEqual(reviewer["model_reasoning_effort"], "xhigh")

    def test_installed_skill_bindings_map_each_declared_skill_to_its_exact_resource(self):
        with tempfile.TemporaryDirectory() as temporary:
            codex_home = Path(temporary) / "codex-home"
            result = self.run_install("--codex-home", str(codex_home))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            for source_path in sorted((REPO_ROOT / "crux" / "agents").glob("*.md")):
                source = agents.parse_source(source_path)
                installed = tomllib.loads((codex_home / "agents" / agents.codex_agent_filename(source.name)).read_text(encoding="utf-8"))
                paths = [entry["path"] for entry in installed.get("skills", {}).get("config", [])]
                expected = [str((REPO_ROOT / "crux" / "skills" / skill / "SKILL.md").resolve()) for skill in source.skills]
                self.assertEqual(paths, expected, source.name)

    def test_relocated_plugin_requires_force_then_refreshes_skill_bindings(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            codex_home = root / "codex-home"
            self.assertEqual(self.run_install("--codex-home", str(codex_home)).returncode, 0)
            plugin = root / "relocated-crux"
            for directory in (".codex-plugin", "agents", "catalog", "skills"):
                shutil.copytree(REPO_ROOT / "crux" / directory, plugin / directory)
            relocated_scripts = plugin / "skills" / "install-codex-agents" / "scripts"
            relocated_scripts.mkdir(parents=True, exist_ok=True)
            (plugin / "scripts").mkdir()
            for name in ("codex_agents.py", "codex_agent_health.py", "models_catalog.py", "_yaml_min.py"):
                shutil.copy2(SCRIPTS_DIR / name, plugin / "scripts" / name)
            shutil.copy2(INSTALLER, relocated_scripts / "install.py")
            relocated = relocated_scripts / "install.py"
            refused = self.run_install("--codex-home", str(codex_home), executable=relocated)
            self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
            refreshed = self.run_install("--codex-home", str(codex_home), "--force", executable=relocated)
            self.assertEqual(refreshed.returncode, 0, refreshed.stdout + refreshed.stderr)
            installed = tomllib.loads((codex_home / "agents" / "crux-architect.toml").read_text(encoding="utf-8"))
            self.assertTrue(
                all(entry["path"].startswith(str(plugin.resolve())) for entry in installed["skills"]["config"]), installed
            )

    def test_repo_root_keeps_project_scope_explicit(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary) / "repo"
            repo.mkdir()
            result = self.run_install("--repo-root", str(repo))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["scope"], "project")
            self.assertEqual(Path(payload["target"]), (repo / ".codex" / "agents").resolve())

    def test_check_is_read_only_and_reports_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            codex_home = Path(temporary) / "codex-home"
            result = self.run_install("--codex-home", str(codex_home), "--check")
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["drift"]["status"], "drift")
            self.assertFalse(codex_home.exists())

    def test_project_context_reports_shadow_without_rewriting_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            codex_home = root / "codex-home"
            project = root / "project"
            agent = project / ".codex" / "agents" / "local-name.toml"
            agent.parent.mkdir(parents=True)
            agent.write_text('name = "crux_architect"\n', encoding="utf-8")
            result = self.run_install("--codex-home", str(codex_home), "--project-context", str(project))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["shadows"]["status"], "findings")
            self.assertEqual(payload["shadows"]["roles"][0]["role"], "architect")
            self.assertEqual(agent.read_text(encoding="utf-8"), 'name = "crux_architect"\n')
