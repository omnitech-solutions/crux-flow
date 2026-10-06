"""Static health reports for installed Codex agents."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = REPO_ROOT / "crux" / "scripts"
HEALTH_CLI = SCRIPTS_DIR / "codex_agent_health.py"
spec = importlib.util.spec_from_file_location("codex_agent_health", SCRIPTS_DIR / "codex_agent_health.py")
assert spec and spec.loader
health = importlib.util.module_from_spec(spec)
spec.loader.exec_module(health)


class HealthTests(unittest.TestCase):
    def _health_cli(self, target: Path, *, plugin: Path = REPO_ROOT / "crux", timeout: float = 3) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                [sys.executable, str(HEALTH_CLI), "--target", str(target), "--plugin-root", str(plugin), "--scope", "personal"],
                capture_output=True, text=True, check=False, timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            self.fail(f"health CLI blocked on target inspection: {exc}")

    def test_no_project_context_is_explicitly_unverified(self):
        with tempfile.TemporaryDirectory() as temporary:
            report = health.inspect_install(
                target=Path(temporary) / "agents", plugin_root=REPO_ROOT / "crux", scope="personal"
            )
        self.assertEqual(report["shadows"]["status"], "unverified")
        self.assertEqual(report["runtime"]["status"], "unverified")
        self.assertIsNone(report["runtime"]["client_version"])

    def test_shadow_scan_uses_effective_name_and_reports_unsafe_and_malformed_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            agents_dir = root / ".codex" / "agents"
            agents_dir.mkdir(parents=True)
            (agents_dir / "custom-name.toml").write_text('name = "crux_reviewer"\n', encoding="utf-8")
            (agents_dir / "bad.toml").write_text('name = [\n', encoding="utf-8")
            victim = root / "victim.toml"
            victim.write_text('name = "crux_architect"\n', encoding="utf-8")
            (agents_dir / "unsafe.toml").symlink_to(victim)
            os.mkfifo(agents_dir / "special.toml")
            report = health.inspect_install(
                target=root / "target", plugin_root=REPO_ROOT / "crux", scope="personal", project_context=root
            )
        shadows = report["shadows"]
        self.assertEqual(shadows["status"], "findings")
        self.assertEqual(shadows["roles"], [{"role": "reviewer", "file": "custom-name.toml", "name": "crux_reviewer"}])
        self.assertEqual(shadows["malformed"][0]["file"], "bad.toml")
        self.assertEqual({item["file"] for item in shadows["unsafe"]}, {"unsafe.toml", "special.toml"})

    def test_shadow_scan_does_not_follow_an_escaping_codex_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "project"
            outside = Path(temporary) / "outside"
            root.mkdir()
            outside_agents = outside / "agents"
            outside_agents.mkdir(parents=True)
            (outside_agents / "external.toml").write_text('name = "crux_architect"\n', encoding="utf-8")
            (root / ".codex").symlink_to(outside, target_is_directory=True)
            report = health.inspect_install(
                target=Path(temporary) / "target", plugin_root=REPO_ROOT / "crux", scope="personal", project_context=root
            )
        shadows = report["shadows"]
        self.assertEqual(shadows["status"], "findings")
        self.assertEqual(shadows["unsafe"][0]["file"], ".codex")
        self.assertEqual(shadows["roles"], [])
        self.assertEqual(shadows["malformed"], [])

    def test_report_separates_installed_values_from_canonical_expectations(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "agents"
            plugin = REPO_ROOT / "crux"
            generated = health.codex_agents.generate(plugin / "agents", skill_root=plugin / "skills")
            health.codex_agents.write(target, generated)
            architect = target / "crux-architect.toml"
            text = architect.read_text(encoding="utf-8").replace(
                'model = "gpt-6.1-sol"', 'model = "locally-pinned"'
            )
            first = text.index("\n[[skills.config]]")
            second = text.index("\n[[skills.config]]", first + 1)
            text = text[:first] + text[second:]
            architect.write_text(text.replace("enabled = true", "enabled = false", 1), encoding="utf-8")
            report = health.inspect_install(target=target, plugin_root=plugin, scope="personal")
        expected = next(role for role in report["roles"] if role["role"] == "architect")
        installed = next(role for role in report["installed"]["roles"] if role["role"] == "architect")
        self.assertEqual(expected["model"], "gpt-6.1-sol")
        self.assertEqual(installed["model"], "locally-pinned")
        self.assertIn("missing", {binding["status"] for binding in installed["skill_bindings"]})
        self.assertIn("disabled", {binding["status"] for binding in installed["skill_bindings"]})
        self.assertEqual(
            next(role for role in report["installed"]["roles"] if role["role"] == "wayfinder")["status"],
            "installed",
        )

    def test_pinned_target_report_reads_the_directory_that_was_written(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            target = root / ".codex" / "agents"
            target.mkdir(parents=True)
            outside = Path(temporary) / "outside"
            outside.mkdir()
            plugin = REPO_ROOT / "crux"
            generated = health.codex_agents.generate(plugin / "agents", skill_root=plugin / "skills")

            with health.codex_agents.pin_directory(target, containment_root=root) as pinned:
                original_parent = root / ".codex-original"
                (root / ".codex").rename(original_parent)
                (root / ".codex").symlink_to(outside, target_is_directory=True)
                health.codex_agents.write(pinned, generated)
                report = health.inspect_install(target=pinned, plugin_root=plugin, scope="project")

            self.assertEqual(report["drift"]["status"], "clean")
            self.assertEqual(report["installed"]["status"], "clean")
            self.assertEqual({path.name for path in outside.glob("crux-*.toml")}, set())

    def test_plugin_root_selects_its_own_catalog_and_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            plugin = Path(temporary) / "fixture-crux"
            for directory in (".codex-plugin", "agents", "catalog", "skills"):
                shutil.copytree(REPO_ROOT / "crux" / directory, plugin / directory)
            manifest = plugin / ".codex-plugin" / "plugin.json"
            manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
            manifest_data["version"] = "fixture-version"
            manifest.write_text(json.dumps(manifest_data, indent=2) + "\n", encoding="utf-8")
            catalog = plugin / "catalog" / "models.yml"
            text, count = re.subn(
                r"^(  flagship:\n(?:    (?!codex:)\S.*\n)*    codex:\n      model: )\S+$",
                r"\1fixture-model",
                catalog.read_text(encoding="utf-8"),
                count=1,
                flags=re.M,
            )
            self.assertEqual(count, 1, "fixture inert: the flagship codex model line moved")
            catalog.write_text(text, encoding="utf-8")
            report = health.inspect_install(target=Path(temporary) / "agents", plugin_root=plugin, scope="personal")
        self.assertEqual(report["plugin"]["version"], "fixture-version")
        self.assertEqual(next(role for role in report["roles"] if role["role"] == "architect")["model"], "fixture-model")

    def test_plugin_version_and_role_static_fields_are_stable(self):
        with tempfile.TemporaryDirectory() as temporary:
            report = health.inspect_install(
                target=Path(temporary) / "agents", plugin_root=REPO_ROOT / "crux", scope="personal"
            )
        manifest = json.loads((REPO_ROOT / "crux" / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(report["plugin"]["version"], manifest["version"])
        self.assertEqual([role["role"] for role in report["roles"]], sorted(role["role"] for role in report["roles"]))
        self.assertTrue(all("model" in role and "model_reasoning_effort" in role for role in report["roles"]))

    def test_canonical_reviewer_runtime_uses_sol_at_xhigh_effort(self):
        with tempfile.TemporaryDirectory() as temporary:
            report = health.inspect_install(
                target=Path(temporary) / "agents", plugin_root=REPO_ROOT / "crux", scope="personal"
            )
        reviewer = next(role for role in report["roles"] if role["role"] == "reviewer")
        self.assertEqual(reviewer["model"], "gpt-6.1-sol")
        self.assertEqual(reviewer["model_reasoning_effort"], "xhigh")

    def test_health_reports_managed_symlink_fifo_and_invalid_utf8_without_blocking(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "agents"
            target.mkdir()
            victim = Path(temporary) / "victim.toml"
            victim.write_text("sentinel", encoding="utf-8")
            (target / "crux-architect.toml").symlink_to(victim)
            (target / "crux-developer.toml").write_bytes(b"\xff\xfe")
            fifo = target / "crux-historian.toml"
            os.mkfifo(fifo)
            (target / "crux-librarian.toml").mkdir()
            result = self._health_cli(target)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        installed = {item["role"]: item for item in report["installed"]["roles"]}
        self.assertEqual(installed["architect"]["status"], "unsafe")
        self.assertEqual(installed["developer"]["status"], "malformed")
        self.assertEqual(installed["historian"]["status"], "unsafe")
        self.assertEqual(installed["librarian"]["status"], "unsafe")

    def test_relocated_binding_keeps_the_actual_installed_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "agents"
            plugin = REPO_ROOT / "crux"
            generated = health.codex_agents.generate(plugin / "agents", skill_root=plugin / "skills")
            health.codex_agents.write(target, generated)
            architect = target / "crux-architect.toml"
            text = architect.read_text(encoding="utf-8").replace(
                str(plugin / "skills" / "author-promptbook" / "SKILL.md"), "/old/crux/skills/author-promptbook/SKILL.md"
            )
            architect.write_text(text, encoding="utf-8")
            report = health.inspect_install(target=target, plugin_root=plugin, scope="personal")
        installed = next(item for item in report["installed"]["roles"] if item["role"] == "architect")
        binding = next(item for item in installed["skill_bindings"] if item["skill"] == "author-promptbook")
        self.assertEqual(binding["installed_path"], "/old/crux/skills/author-promptbook/SKILL.md")
        self.assertEqual(binding["status"], "missing")

    def test_plugin_manifest_invalid_utf8_or_nonobject_is_a_spec_violation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / ".codex-plugin" / "plugin.json"
            manifest.parent.mkdir()
            manifest.write_bytes(b"\xff")
            with self.assertRaises(health.codex_agents.SpecViolation):
                health._plugin(root)
            manifest.write_text("[]", encoding="utf-8")
            with self.assertRaises(health.codex_agents.SpecViolation):
                health._plugin(root)

    def test_health_cli_returns_structured_exit_two_for_invalid_manifest_before_target_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "plugin"
            manifest = root / ".codex-plugin" / "plugin.json"
            manifest.parent.mkdir(parents=True)
            target = Path(temporary) / "agents"
            for payload in (b"\xff", b"not json", b"[]", b'{"version": null}', b'{"version": " "}'):
                manifest.write_bytes(payload)
                result = self._health_cli(target, plugin=root)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn("error", json.loads(result.stdout))
                self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
