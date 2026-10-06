"""Regression tests for Crux's Codex-native generated role agents."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

try:
    import tomllib
    HAVE_TOMLLIB = True
except ImportError:
    HAVE_TOMLLIB = False


SCRIPTS_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = SCRIPTS_DIR.parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

import codex_agents as agents  # noqa: E402
import models_catalog  # noqa: E402

CATALOG = models_catalog.load()


EXPECTED_SOURCE_NAMES = {
    "architect", "brainstormer", "commander", "dev-lead", "developer",
    "historian", "librarian", "night-gardener", "reviewer", "wayfinder",
}
EXPECTED_FILES = {agents.codex_agent_filename(name) for name in EXPECTED_SOURCE_NAMES}
INSTALLER = REPO_ROOT / "crux" / "skills" / "install-codex-agents" / "scripts" / "install.py"

# Exact per-role (model, model_reasoning_effort) expectations. The pairs come
# from each role's resolved Codex runtime in `crux/catalog/models.yml`. The
# catalog selects a complete agent override when present, otherwise the level
# fallback. This table independently states the required values, so a catalog
# edit fails here rather than passing silently. Provenance
# (ADR-0046 clause 6a): these slugs are an OpenAI-owned external contract,
# GPT-6.1 Sol, and its high and xhigh efforts, were verified 2026-09-29
# against the local Codex 0.159.0 model catalog. GPT-6 Astra and its high
# effort were verified 2026-09-22 against the local Codex 0.155.1 catalog. GPT-6 has no Terra,
# so the flagship and standard fallbacks both select Sol. The apex fallback
# selects Astra. The reviewer override selects Sol at xhigh effort.
# Exact expectations catch a wrong slug or a model leaking into another tier.
EXPECTED_RUNTIME = {
    "architect": ("gpt-6.1-sol", "high"),
    "brainstormer": ("gpt-6.1-sol", "high"),
    "commander": ("gpt-6-astra", "high"),
    "dev-lead": ("gpt-6.1-sol", "high"),
    "developer": ("gpt-6.1-sol", "high"),
    "historian": ("gpt-6.1-sol", "high"),
    "librarian": ("gpt-6.1-sol", "high"),
    "night-gardener": ("gpt-6-astra", "high"),
    "reviewer": ("gpt-6.1-sol", "xhigh"),
    "wayfinder": ("gpt-6.1-sol", "high"),
}


class RenderTests(unittest.TestCase):
    def test_catalog_roster_matches_source_agents(self):
        self.assertEqual(set(CATALOG.agents), EXPECTED_SOURCE_NAMES)

    @unittest.skipUnless(HAVE_TOMLLIB, "tomllib not available — Python 3.11+ (or uv) required")
    def test_each_projection_is_valid_toml_with_required_fields(self):
        generated = agents.generate()
        self.assertEqual(set(generated), EXPECTED_FILES)
        for filename, content in generated.items():
            parsed = tomllib.loads(content)
            source_name = filename.removeprefix(agents.OUTPUT_PREFIX).removesuffix(".toml")
            self.assertEqual(parsed["name"], agents.codex_agent_name(source_name))
            self.assertTrue(parsed["description"])
            self.assertIn(parsed["sandbox_mode"], {"read-only", "workspace-write"})
            self.assertIn("Codex compatibility rules", parsed["developer_instructions"])
            self.assertIn("Do not spawn subagents", parsed["developer_instructions"])
            self.assertIn("Codex execution override (binding)", parsed["developer_instructions"])

    @unittest.skipUnless(HAVE_TOMLLIB, "tomllib not available — Python 3.11+ (or uv) required")
    def test_each_role_pins_exact_model_and_effort(self):
        # EXACT per-role (model, effort) — replaces the former permissive
        # membership check. Guards against a silent regression to bare `gpt-5.6`
        # (a crux-router-only alias, not a Codex catalog slug) or a wrong slug.
        generated = agents.generate()
        for source_name, (model, effort) in EXPECTED_RUNTIME.items():
            with self.subTest(role=source_name):
                parsed = tomllib.loads(generated[agents.codex_agent_filename(source_name)])
                self.assertEqual(parsed["model"], model)
                self.assertEqual(parsed["model_reasoning_effort"], effort)

    def test_resolved_codex_cells_match_exact_expectations(self):
        # Assert the resolution directly (no tomllib needed), so the exact
        # model/effort contract is verified even on a no-tomllib lane.
        actual = {
            name: (CATALOG.resolve(name).codex.model, CATALOG.resolve(name).codex.reasoning_effort)
            for name in CATALOG.agents
        }
        self.assertEqual(actual, EXPECTED_RUNTIME)
        # No flagship role may regress to the bare router alias.
        self.assertNotIn("gpt-5.6", {m for m, _ in actual.values()})

    def test_astral_plane_description_serializes_to_valid_toml(self):
        # TOML serialization correctness (SHOULD): json.dumps would emit UTF-16
        # surrogate pairs for astral-plane codepoints, which TOML rejects. The
        # custom _toml_basic_string encoder must round-trip them.
        src = agents.SourceAgent(
            name="architect",
            description="rocket \U0001F680 and quote \" and tab \t end",
            tools=frozenset({"Read", "Edit", "Write"}),
            body="body",
        )
        rendered = agents.render_agent(src, CATALOG.resolve("architect").codex)
        if HAVE_TOMLLIB:
            parsed = tomllib.loads(rendered)
            self.assertEqual(parsed["description"], src.description)
        else:  # still assert no raw surrogate escape leaked into the output
            self.assertNotIn("\\ud", rendered.lower())

    @unittest.skipUnless(HAVE_TOMLLIB, "tomllib not available — Python 3.11+ (or uv) required")
    def test_read_only_roles_do_not_gain_write_default(self):
        generated = agents.generate()
        for source_name in ("brainstormer", "commander", "librarian", "reviewer"):
            parsed = tomllib.loads(generated[agents.codex_agent_filename(source_name)])
            self.assertEqual(parsed["sandbox_mode"], "read-only")

    @unittest.skipUnless(HAVE_TOMLLIB, "tomllib not available — Python 3.11+ (or uv) required")
    def test_writer_roles_default_to_workspace_write(self):
        generated = agents.generate()
        for source_name in ("architect", "dev-lead", "developer", "historian", "night-gardener"):
            parsed = tomllib.loads(generated[agents.codex_agent_filename(source_name)])
            self.assertEqual(parsed["sandbox_mode"], "workspace-write")

    @unittest.skipUnless(HAVE_TOMLLIB, "tomllib not available — Python 3.11+ required")
    def test_install_render_binds_declared_skills_without_affecting_portable_output(self):
        """Installation binds absolute resources; committed output stays portable."""
        for role in EXPECTED_SOURCE_NAMES:
            with self.subTest(role=role):
                source = agents.parse_source(agents.SOURCE_DIR / f"{role}.md")
                portable = agents.render_agent(source, CATALOG.resolve(role).codex)
                installed = agents.render_agent(
                    source, CATALOG.resolve(role).codex, skill_root=REPO_ROOT / "crux" / "skills"
                )
                self.assertNotIn("[[skills.config]]", portable)
                self.assertNotIn(str(REPO_ROOT), portable)
                parsed = tomllib.loads(installed)
                self.assertEqual(parsed["model"], EXPECTED_RUNTIME[role][0])
                self.assertEqual(parsed["model_reasoning_effort"], EXPECTED_RUNTIME[role][1])
                configured = parsed.get("skills", {}).get("config", [])
                self.assertEqual(len(configured), len(source.skills))
                self.assertEqual([item["path"] for item in configured], sorted(item["path"] for item in configured))
                self.assertTrue(all(item["enabled"] is True for item in configured))
                for item in configured:
                    resource = Path(item["path"])
                    self.assertTrue(resource.is_absolute())
                    self.assertTrue(resource.is_file())
                    self.assertEqual(resource.name, "SKILL.md")

    def test_install_render_refuses_missing_or_escaping_skill_resource(self):
        source = agents.SourceAgent(
            name="architect", description="x", tools=frozenset({"Read"}), body="body",
            skills=("does-not-exist",),
        )
        with self.assertRaises(agents.SpecViolation):
            agents.render_agent(source, CATALOG.resolve("architect").codex, skill_root=REPO_ROOT / "crux" / "skills")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "skills"
            root.mkdir()
            outside = Path(temporary) / "outside.md"
            outside.write_text("outside", encoding="utf-8")
            escaped = root / "escape"
            escaped.mkdir()
            (escaped / "SKILL.md").symlink_to(outside)
            source = agents.SourceAgent(
                name="architect", description="x", tools=frozenset({"Read"}), body="body", skills=("escape",)
            )
            with self.assertRaises(agents.SpecViolation):
                agents.render_agent(source, CATALOG.resolve("architect").codex, skill_root=root)


class DevCheckoutShadowTests(unittest.TestCase):
    # Personal agents are the dogfood configuration. A project-local copy in
    # this checkout shadows them and can silently omit new personal settings.
    # The `tools/` marker limits this assertion to the private development
    # checkout; the public artifact does not ship that directory.
    @unittest.skipUnless(
        (REPO_ROOT / "tools" / "sync_stage.py").is_file(),
        "project-shadow guard applies only to the private development checkout",
    )
    def test_dev_checkout_has_no_project_codex_agents(self):
        output_dir = REPO_ROOT / ".codex" / "agents"
        shadows = sorted(path.name for path in output_dir.glob("crux-*.toml"))
        self.assertEqual(shadows, [], "project-local Crux agents shadow personal agents")


class WriteSymlinkRefusalTests(unittest.TestCase):
    """MUST B (S5): the shared write() must never follow a leaf crux-*.toml
    symlink — for the direct generator AND (via the shared path) the installer.
    Covers escaping and contained leaf targets; asserts the victim is untouched
    and the refusal is a structured SpecViolation (fail-closed), incl. --force."""

    def _victim(self, path: Path, content: str = "SENTINEL — must not be clobbered\n") -> Path:
        path.write_text(content, encoding="utf-8")
        return path

    def test_write_refuses_leaf_symlink_with_escaping_target(self):
        with tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as outside:
            output_dir = Path(repo) / ".codex" / "agents"
            output_dir.mkdir(parents=True)
            victim = self._victim(Path(outside) / "victim.toml")
            # a crux-managed leaf name that generate() WILL try to write
            leaf = output_dir / agents.codex_agent_filename("architect")
            leaf.symlink_to(victim)

            generated = agents.generate()
            with self.assertRaises(agents.SpecViolation):
                agents.write(output_dir, generated)
            # victim untouched, no crux content leaked through the link
            self.assertEqual(victim.read_text(encoding="utf-8"), "SENTINEL — must not be clobbered\n")

    def test_write_refuses_leaf_symlink_with_contained_target(self):
        with tempfile.TemporaryDirectory() as repo:
            output_dir = Path(repo) / ".codex" / "agents"
            output_dir.mkdir(parents=True)
            victim = self._victim(Path(repo) / "in_repo_victim.toml")
            leaf = output_dir / agents.codex_agent_filename("developer")
            leaf.symlink_to(victim)

            with self.assertRaises(agents.SpecViolation):
                agents.write(output_dir, agents.generate())
            self.assertEqual(victim.read_text(encoding="utf-8"), "SENTINEL — must not be clobbered\n")

    def test_write_refuses_stale_leaf_symlink_before_unlink(self):
        # A STALE crux-*.toml (no current role) that is a symlink must not be
        # unlinked-through either; write(remove_stale=True) refuses fail-closed.
        with tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as outside:
            output_dir = Path(repo) / ".codex" / "agents"
            output_dir.mkdir(parents=True)
            victim = self._victim(Path(outside) / "stale_victim.toml")
            stale = output_dir / "crux-retired-role.toml"
            stale.symlink_to(victim)

            with self.assertRaises(agents.SpecViolation):
                agents.write(output_dir, agents.generate(), remove_stale=True)
            self.assertTrue(stale.is_symlink())
            self.assertTrue(victim.exists())

    def test_diff_refuses_dangling_leaf_symlink_with_clean_error(self):
        # SHOULD: a dangling crux-*.toml symlink must yield a clean SpecViolation
        # from diff(), not a raw FileNotFoundError leaking mid-scan.
        with tempfile.TemporaryDirectory() as repo:
            output_dir = Path(repo) / ".codex" / "agents"
            output_dir.mkdir(parents=True)
            (output_dir / agents.codex_agent_filename("architect")).symlink_to(
                Path(repo) / "nonexistent.toml"
            )
            with self.assertRaises(agents.SpecViolation):
                agents.diff(output_dir, agents.generate())

    def test_write_remove_stale_false_returns_empty_removed_and_keeps_stale(self):
        # SHOULD: write(remove_stale=False) writes generated, removes nothing,
        # and returns removed == [].
        with tempfile.TemporaryDirectory() as repo:
            output_dir = Path(repo) / ".codex" / "agents"
            output_dir.mkdir(parents=True)
            stale = output_dir / "crux-retired-role.toml"
            stale.write_text('name = "crux_retired_role"\n', encoding="utf-8")

            written, removed = agents.write(output_dir, agents.generate(), remove_stale=False)
            self.assertEqual(set(written), EXPECTED_FILES)
            self.assertEqual(removed, [])
            self.assertTrue(stale.exists())  # untouched when remove_stale=False

    def test_write_replaces_a_managed_hardlink_without_mutating_its_victim(self):
        with tempfile.TemporaryDirectory() as temporary:
            output_dir = Path(temporary) / "agents"
            generated = agents.generate()
            agents.write(output_dir, generated)
            leaf = output_dir / agents.codex_agent_filename("architect")
            victim = Path(temporary) / "victim.toml"
            victim.hardlink_to(leaf)
            original = victim.read_text(encoding="utf-8")
            replacement = dict(generated)
            replacement[leaf.name] = replacement[leaf.name] + "# refreshed\n"

            agents.write(output_dir, replacement)

            self.assertEqual(victim.read_text(encoding="utf-8"), original)
            self.assertEqual(leaf.read_text(encoding="utf-8"), replacement[leaf.name])

    def test_write_refuses_nonregular_leaf_before_any_partial_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            output_dir = Path(temporary) / "agents"
            output_dir.mkdir()
            (output_dir / agents.codex_agent_filename("developer")).mkdir()
            with self.assertRaises(agents.SpecViolation):
                agents.write(output_dir, agents.generate())
            self.assertFalse((output_dir / agents.codex_agent_filename("architect")).exists())

    def test_atomic_replace_does_not_follow_leaf_substituted_after_validation(self):
        with tempfile.TemporaryDirectory() as temporary:
            output_dir = Path(temporary) / "agents"
            generated = agents.generate()
            agents.write(output_dir, generated)
            leaf = output_dir / agents.codex_agent_filename("architect")
            victim = Path(temporary) / "victim.toml"
            victim.write_text("SENTINEL\n", encoding="utf-8")
            replacement = dict(generated)
            replacement[leaf.name] = replacement[leaf.name] + "# refreshed\n"
            actual_replace = agents.os.replace

            def substitute_then_replace(src, dst, *args, **kwargs):
                leaf.unlink()
                leaf.symlink_to(victim)
                return actual_replace(src, dst, *args, **kwargs)

            with mock.patch.object(agents.os, "replace", side_effect=substitute_then_replace):
                agents.write(output_dir, replacement)

            self.assertEqual(victim.read_text(encoding="utf-8"), "SENTINEL\n")
            self.assertFalse(leaf.is_symlink())
            self.assertEqual(leaf.read_text(encoding="utf-8"), replacement[leaf.name])

    def test_pinned_directory_survives_output_directory_substitution(self):
        """A post-validation output link swap cannot redirect replacement."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            output_dir = root / ".codex" / "agents"
            output_dir.parent.mkdir(parents=True)
            target = root / "managed-agents"
            target.mkdir()
            output_dir.symlink_to(target, target_is_directory=True)
            outside = Path(temporary) / "outside"
            outside.mkdir()
            generated = agents.generate()

            with agents.pin_directory(output_dir, containment_root=root) as pinned:
                output_dir.unlink()
                output_dir.symlink_to(outside, target_is_directory=True)
                agents.write(pinned, generated)

            self.assertEqual(
                {path.name for path in target.glob("crux-*.toml")},
                set(EXPECTED_FILES),
            )
            self.assertEqual({path.name for path in outside.glob("crux-*.toml")}, set())

    def test_pinned_directory_survives_parent_directory_substitution(self):
        """A post-validation parent swap cannot redirect replacement."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            output_dir = root / ".codex" / "agents"
            output_dir.mkdir(parents=True)
            outside = Path(temporary) / "outside"
            outside.mkdir()
            generated = agents.generate()

            with agents.pin_directory(output_dir, containment_root=root) as pinned:
                original_parent = root / ".codex-original"
                (root / ".codex").rename(original_parent)
                (root / ".codex").symlink_to(outside, target_is_directory=True)
                agents.write(pinned, generated)

            self.assertEqual(
                {path.name for path in (original_parent / "agents").glob("crux-*.toml")},
                set(EXPECTED_FILES),
            )
            self.assertEqual({path.name for path in outside.glob("crux-*.toml")}, set())


class InstallerTests(unittest.TestCase):
    def _run(self, repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(INSTALLER), "--repo-root", str(repo_root), *args],
            check=False,
            capture_output=True,
            text=True,
        )

    def _scratch_installer_without_source_agents(self, scratch: Path) -> Path:
        """Copy the installer and its shared module into a plugin tree with no source agents.

        The installer derives its source directory from its own resolved path, so
        a scratch plugin tree is the only way to make `generate()` fail: a
        symlinked installer resolves back to the real tree and reads the real
        crux/agents/.
        """
        plugin_root = scratch / "crux"
        (plugin_root / "agents").mkdir(parents=True)
        (plugin_root / "scripts").mkdir()
        scratch_scripts = plugin_root / "skills" / "install-codex-agents" / "scripts"
        scratch_scripts.mkdir(parents=True)
        # All four modules, because they ship together. Copying only
        # `codex_agents.py` modelled an install that cannot exist, and the gap
        # was invisible in the dev tree — there, `models_catalog` is importable
        # from sys.path, so the fixture accidentally worked. Under the staged
        # release artifact it was not, and the by-location fallback raised
        # FileNotFoundError at import time: exit 1 and a traceback where this
        # test asserts exit 2 and structured JSON.
        for module in ("codex_agents.py", "codex_agent_health.py", "models_catalog.py", "_yaml_min.py"):
            shutil.copy2(SCRIPTS_DIR / module, plugin_root / "scripts")
        shutil.copy2(INSTALLER, scratch_scripts)
        return scratch_scripts / "install.py"

    def _scratch_installer_without_models_catalog(self, scratch: Path) -> Path:
        """A scratch plugin tree with a real source agent but NO models_catalog.py.

        The sibling fixture makes `generate()` fail on an empty source roster,
        which never reaches the catalog. This one enters the OTHER lane:
        `codex_agents` imports cleanly (its by-location fallback installs a
        `_DeferredCatalogFault` stand-in instead of raising), so the
        installer's module-level import succeeds and the fault surfaces at
        first use — as the `SpecViolation` the stand-in serves. Providing a
        source agent is what makes the difference. Mirrors the OpenCode lane's
        fixture in `test_install_opencode_agents.py`.
        """
        plugin_root = scratch / "crux"
        (plugin_root / "agents").mkdir(parents=True)
        (plugin_root / "scripts").mkdir()
        scratch_scripts = plugin_root / "skills" / "install-codex-agents" / "scripts"
        scratch_scripts.mkdir(parents=True)
        shutil.copy2(
            REPO_ROOT / "crux" / "agents" / "architect.md", plugin_root / "agents" / "architect.md"
        )
        # `models_catalog.py` deliberately omitted — the broken install F1 reported.
        for module in ("codex_agents.py", "_yaml_min.py"):
            shutil.copy2(SCRIPTS_DIR / module, plugin_root / "scripts")
        shutil.copy2(INSTALLER, scratch_scripts)
        return scratch_scripts / "install.py"

    def test_installer_missing_models_catalog_is_exit_two_json_not_a_traceback(self):
        """[F1 lane] A broken install reports; it does not crash.

        The dev venv's `.pth` puts `<repo>/crux/scripts` on every
        interpreter's `sys.path`, so a scratch tree that omits a module is not
        actually missing it here. The driver below removes that entry, so the
        omission is real in the dev tree; under the staged artifact the same
        entry is the one `sync.sh` exports as PYTHONPATH, so it is removed
        there too and the test measures the same thing in both worlds.

        The comparison is on RESOLVED paths, and that is load-bearing rather
        than tidiness. `sync.sh` builds its stage with `mktemp -d`, which on
        macOS yields an unresolved `/var/folders/...` path, and exports it as
        PYTHONPATH; `SCRIPTS_DIR` is `.resolve()`d to the `/private/var/...`
        form. A string compare therefore never matched the staged entry, the
        real `models_catalog` stayed importable, and the scratch tree's
        deliberate omission was invisible — the dev suite passed while the
        staged gate failed. (Distinct from the `.pth` leak, which is real and
        separate.)
        """
        with tempfile.TemporaryDirectory() as temporary:
            scratch = Path(temporary)
            installer = self._scratch_installer_without_models_catalog(scratch)
            repo_root = scratch / "repo"
            repo_root.mkdir()

            driver = (
                "import runpy, sys\n"
                "from pathlib import Path as _Path\n"
                f"_target = _Path({str(SCRIPTS_DIR)!r}).resolve()\n"
                "def _leaks(p):\n"
                "    if p == '':\n"
                "        return True\n"
                "    try:\n"
                "        return _Path(p).resolve() == _target\n"
                "    except OSError:\n"
                "        return False\n"
                "sys.path[:] = [p for p in sys.path if not _leaks(p)]\n"
                f"sys.argv = ['install.py', '--repo-root', {str(repo_root)!r}]\n"
                f"runpy.run_path({str(installer)!r}, run_name='__main__')\n"
            )
            result = subprocess.run(
                [sys.executable, "-c", driver], check=False, capture_output=True, text=True
            )
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            payload = json.loads(result.stdout)
            self.assertIn("models_catalog.py", payload["error"])
            self.assertIn("ships beside", payload["error"])
            self.assertFalse((repo_root / ".codex").exists())

    def test_installer_translates_generate_spec_violation_to_exit_two(self):
        # generate() must sit inside a fail-closed handler like diff() and
        # write() do: an unwrapped call ends the run in a traceback and exit 1
        # with empty stdout, which misreports a capability error as reviewable
        # drift — exit 1 stdout is contractually the JSON diff report.
        with tempfile.TemporaryDirectory() as temporary:
            scratch = Path(temporary)
            installer = self._scratch_installer_without_source_agents(scratch)
            repo_root = scratch / "repo"
            repo_root.mkdir()

            result = subprocess.run(
                [sys.executable, str(installer), "--repo-root", str(repo_root)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn("no source agents", json.loads(result.stdout)["error"])
            self.assertNotIn("Traceback", result.stderr)
            self.assertFalse((repo_root / ".codex").exists())

    def test_installer_writes_crux_agents_without_touching_custom_agent(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo_root = Path(temporary)
            custom = repo_root / ".codex" / "agents" / "project-reviewer.toml"
            custom.parent.mkdir(parents=True)
            custom.write_text('name = "project_reviewer"\n', encoding="utf-8")

            result = self._run(repo_root)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(set(payload["written"]), EXPECTED_FILES)
            self.assertEqual(custom.read_text(encoding="utf-8"), 'name = "project_reviewer"\n')

            # A user file that never matches crux-*.toml is never a removal/
            # overwrite candidate under any flag, --force included (ADR-0046
            # clause 7 no-clobber contract).
            forced = self._run(repo_root, "--force")
            self.assertEqual(forced.returncode, 0, forced.stderr)
            self.assertEqual(custom.read_text(encoding="utf-8"), 'name = "project_reviewer"\n')

    def test_installer_refuses_changed_generated_agent_without_force(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo_root = Path(temporary)
            self.assertEqual(self._run(repo_root).returncode, 0)
            changed = repo_root / ".codex" / "agents" / "crux-developer.toml"
            changed.write_text('name = "locally_changed"\n', encoding="utf-8")

            result = self._run(repo_root)
            self.assertEqual(result.returncode, 1)
            self.assertIn("crux-developer.toml", json.loads(result.stdout)["changed"])

            forced = self._run(repo_root, "--force")
            self.assertEqual(forced.returncode, 0, forced.stderr)
            self.assertIn('name = "crux_developer"', changed.read_text(encoding="utf-8"))

    def test_stale_crux_managed_file_without_force_is_reported_not_removed(self):
        # A crux-*.toml with no corresponding current role is "stale" —
        # crux-managed by the filename predicate alone (the internal crux_*
        # agent name plays no role in selection), and per ADR-0046 clause 7 is
        # reported but NOT removed without --force.
        with tempfile.TemporaryDirectory() as temporary:
            repo_root = Path(temporary)
            self.assertEqual(self._run(repo_root).returncode, 0)
            stale = repo_root / ".codex" / "agents" / "crux-retired-role.toml"
            stale.write_text('name = "crux_retired_role"\n', encoding="utf-8")

            result = self._run(repo_root)
            self.assertEqual(result.returncode, 1)
            self.assertIn("crux-retired-role.toml", json.loads(result.stdout)["removed"])
            self.assertTrue(stale.exists())
            self.assertEqual(stale.read_text(encoding="utf-8"), 'name = "crux_retired_role"\n')

    def test_stale_crux_managed_file_with_force_is_removed(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo_root = Path(temporary)
            self.assertEqual(self._run(repo_root).returncode, 0)
            stale = repo_root / ".codex" / "agents" / "crux-retired-role.toml"
            stale.write_text('name = "crux_retired_role"\n', encoding="utf-8")

            forced = self._run(repo_root, "--force")
            self.assertEqual(forced.returncode, 0, forced.stderr)
            payload = json.loads(forced.stdout)
            self.assertIn("crux-retired-role.toml", payload["removed"])
            self.assertFalse(stale.exists())

    def test_installer_refuses_when_output_dir_escapes_repo_root(self):
        # resolve-then-contain (ADR-0046 clause 7): a symlinked .codex whose
        # resolved target escapes the repo root must be refused, not followed.
        with tempfile.TemporaryDirectory() as temporary, tempfile.TemporaryDirectory() as outside:
            repo_root = Path(temporary)
            outside_path = Path(outside)
            (repo_root / ".codex").symlink_to(outside_path, target_is_directory=True)

            result = self._run(repo_root)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((outside_path / "agents").exists())
            payload = json.loads(result.stdout)
            self.assertIn("error", payload)

    def test_installer_permits_contained_symlink_output_dir(self):
        # A symlink whose resolved target stays under the repo root is
        # permitted — this is resolve-then-contain, not symlink prohibition.
        with tempfile.TemporaryDirectory() as temporary:
            repo_root = Path(temporary)
            (repo_root / ".codex").mkdir()
            actual_agents = repo_root / "actual_agents"
            actual_agents.mkdir()
            (repo_root / ".codex" / "agents").symlink_to(actual_agents, target_is_directory=True)

            result = self._run(repo_root)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(set(payload["written"]), EXPECTED_FILES)
            self.assertEqual(
                {path.name for path in actual_agents.glob("crux-*.toml")},
                EXPECTED_FILES,
            )

    def test_installer_refuses_leaf_symlink_escaping_repo(self):
        # MUST B (S5): a crux-*.toml LEAF that is a symlink to an OUT-OF-REPO
        # target must be refused fail-closed — never written through to clobber
        # the victim — with a structured error, even though the .codex/agents
        # directory itself is contained.
        with tempfile.TemporaryDirectory() as temporary, tempfile.TemporaryDirectory() as outside:
            repo_root = Path(temporary)
            victim = Path(outside) / "victim.toml"
            victim.write_text("SENTINEL\n", encoding="utf-8")
            agents_dir = repo_root / ".codex" / "agents"
            agents_dir.mkdir(parents=True)
            leaf = agents_dir / agents.codex_agent_filename("architect")
            leaf.symlink_to(victim)

            result = self._run(repo_root, "--force")  # even under --force
            self.assertEqual(result.returncode, 2)
            self.assertIn("error", json.loads(result.stdout))
            self.assertEqual(victim.read_text(encoding="utf-8"), "SENTINEL\n")

    def test_installer_refuses_output_dir_that_is_a_regular_file(self):
        # `mkdir(exist_ok=True)` raises FileExistsError when the path exists as a
        # non-directory, so an unguarded write() ends the run in a traceback and
        # exit 1. The contract is a structured error and exit 2.
        with tempfile.TemporaryDirectory() as temporary:
            repo_root = Path(temporary)
            (repo_root / ".codex").mkdir()
            blocker = repo_root / ".codex" / "agents"
            blocker.write_text("NOT A DIRECTORY\n", encoding="utf-8")

            result = self._run(repo_root)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn("error", json.loads(result.stdout))
            self.assertEqual(blocker.read_text(encoding="utf-8"), "NOT A DIRECTORY\n")

    def test_installer_refuses_output_dir_symlinked_to_a_regular_file(self):
        # The contained-directory-symlink allowance is about a link that resolves
        # to a DIRECTORY. A link resolving to a regular file is a non-directory
        # output path: refuse with a structured error, and never write through it.
        with tempfile.TemporaryDirectory() as temporary:
            repo_root = Path(temporary)
            victim = repo_root / "victim.toml"
            victim.write_text("SENTINEL\n", encoding="utf-8")
            (repo_root / ".codex").mkdir()
            (repo_root / ".codex" / "agents").symlink_to(victim)

            result = self._run(repo_root)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn("error", json.loads(result.stdout))
            self.assertEqual(victim.read_text(encoding="utf-8"), "SENTINEL\n")

    def test_installer_refuses_dangling_symlink_output_dir(self):
        # A dangling link is absent to `exists()` but present to `lexists`; the
        # guard must see it, refuse, leave the link alone, and not create the
        # target directory behind it.
        with tempfile.TemporaryDirectory() as temporary:
            repo_root = Path(temporary)
            (repo_root / ".codex").mkdir()
            target = repo_root / "nowhere"
            link = repo_root / ".codex" / "agents"
            link.symlink_to(target, target_is_directory=True)

            result = self._run(repo_root)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn("error", json.loads(result.stdout))
            self.assertTrue(link.is_symlink())
            self.assertFalse(target.exists(), "the link target must not be created")

    def test_installer_refuses_leaf_symlink_contained_in_repo(self):
        # A leaf crux-*.toml symlink to an IN-REPO victim is refused too — leaf
        # clobber prevention is independent of the directory-level containment
        # allowance.
        with tempfile.TemporaryDirectory() as temporary:
            repo_root = Path(temporary)
            victim = repo_root / "in_repo_victim.toml"
            victim.write_text("SENTINEL\n", encoding="utf-8")
            agents_dir = repo_root / ".codex" / "agents"
            agents_dir.mkdir(parents=True)
            leaf = agents_dir / agents.codex_agent_filename("developer")
            leaf.symlink_to(victim)

            result = self._run(repo_root, "--force")
            self.assertEqual(result.returncode, 2)
            self.assertIn("error", json.loads(result.stdout))
            self.assertEqual(victim.read_text(encoding="utf-8"), "SENTINEL\n")


class RegeneratorCliTests(unittest.TestCase):
    """The generator translates EVERY SpecViolation to exit 2, not just generate()'s.

    Its try/except used to wrap `generate()` alone, leaving `diff()` and `write()`
    outside: `--output-dir <regular file>` ended the run in an uncaught
    SpecViolation — a traceback and exit 1 — and so did a symlinked managed leaf.
    The contract for this lane is exit 2 with a message on stderr and nothing on
    stdout (matching validate-catalog.py / extract-code-docs.py); exit 1 is
    reserved for --dry-run drift, whose JSON report a CI gate parses.
    """

    GENERATOR = SCRIPTS_DIR / "generate-codex-agents.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(self.GENERATOR), *args],
            check=False, capture_output=True, text=True,
        )

    def _assert_refused(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("generate-codex-agents:", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_output_dir_is_required(self):
        result = self._run("--dry-run")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("--output-dir", result.stderr)
        self.assertIn("required", result.stderr)

    def test_output_dir_that_is_a_regular_file_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            blocker = Path(temporary) / "agents"
            blocker.write_text("NOT A DIRECTORY\n", encoding="utf-8")
            self._assert_refused(self._run("--output-dir", str(blocker)))
            self.assertEqual(blocker.read_text(encoding="utf-8"), "NOT A DIRECTORY\n")

    def test_output_dir_that_is_a_dangling_symlink_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "nowhere"
            link = Path(temporary) / "agents"
            link.symlink_to(target, target_is_directory=True)
            self._assert_refused(self._run("--output-dir", str(link)))
            self.assertTrue(link.is_symlink())
            self.assertFalse(target.exists(), "the link target must not be created")

    def test_symlinked_leaf_is_refused_on_the_write_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            output_dir = Path(temporary) / "agents"
            output_dir.mkdir()
            victim = Path(temporary) / "victim.toml"
            victim.write_text("SENTINEL\n", encoding="utf-8")
            (output_dir / agents.codex_agent_filename("architect")).symlink_to(victim)

            self._assert_refused(self._run("--output-dir", str(output_dir)))
            self.assertEqual(victim.read_text(encoding="utf-8"), "SENTINEL\n")
            self.assertFalse((output_dir / agents.codex_agent_filename("developer")).exists())

    def test_symlinked_leaf_is_refused_on_the_dry_run_path(self):
        # --dry-run reads the on-disk files to compare them; reading through a
        # link is the same trust violation as writing through it, and it must not
        # be mistaken for exit-1 drift.
        with tempfile.TemporaryDirectory() as temporary:
            output_dir = Path(temporary) / "agents"
            output_dir.mkdir()
            (output_dir / agents.codex_agent_filename("architect")).symlink_to(
                Path(temporary) / "gone.toml"
            )
            self._assert_refused(self._run("--output-dir", str(output_dir), "--dry-run"))

    def test_clean_write_and_dry_run_still_report_normally(self):
        # Guards the refusal tests above against passing vacuously.
        with tempfile.TemporaryDirectory() as temporary:
            output_dir = Path(temporary) / "agents"
            written = self._run("--output-dir", str(output_dir))
            self.assertEqual(written.returncode, 0, written.stderr)
            self.assertEqual(set(json.loads(written.stdout)["written"]), EXPECTED_FILES)

            clean = self._run("--output-dir", str(output_dir), "--dry-run")
            self.assertEqual(clean.returncode, 0, clean.stderr)
            self.assertEqual(
                json.loads(clean.stdout), {"added": [], "changed": [], "removed": []}
            )


class SpecViolationTests(unittest.TestCase):
    """Targeted SpecViolation coverage (economical additions)."""

    def _write_agent(self, dir_: Path, name: str, frontmatter: str, body: str = "Body.") -> Path:
        path = dir_ / f"{name}.md"
        path.write_text(f"---\n{frontmatter}\n---\n{body}\n", encoding="utf-8")
        return path

    def test_edit_without_write_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            src = self._write_agent(
                Path(d), "architect",
                'name: architect\ndescription: x\ntools: Read, Edit',
            )
            with self.assertRaises(agents.SpecViolation):
                agents.parse_source(src)

    def test_empty_source_dir_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(agents.SpecViolation):
                agents.generate(Path(d))


class SourceSkillsParserTests(unittest.TestCase):
    """The Codex source parser matches the catalog's flow-list contract."""

    def _write(self, skills_lines: str) -> Path:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        path = Path(tempdir.name) / "architect.md"
        path.write_text(
            "---\n"
            "name: architect\n"
            "description: x\n"
            "tools: Read\n"
            f"{skills_lines}"
            "---\n"
            "Body.\n",
            encoding="utf-8",
        )
        return path

    def test_missing_skills_is_rejected(self):
        with self.assertRaisesRegex(agents.SpecViolation, "missing skills"):
            agents.parse_source(self._write(""))

    def test_duplicate_skill_list_entry_is_rejected(self):
        with self.assertRaisesRegex(agents.SpecViolation, "duplicate skill"):
            agents.parse_source(self._write("skills: [forge-skill, forge-skill]\n"))

    def test_quoted_flow_skill_strings_are_accepted(self):
        source = agents.parse_source(
            self._write("skills: [\"forge-skill\", 'log-work']\n")
        )
        self.assertEqual(source.skills, ("forge-skill", "log-work"))

    def test_flow_skills_allow_a_yaml_comment_after_the_list(self):
        source = agents.parse_source(
            self._write("skills: [propose-adr] # required workflow\n")
        )
        self.assertEqual(source.skills, ("propose-adr",))

    def test_hash_inside_quoted_skill_name_is_not_treated_as_a_comment(self):
        with self.assertRaisesRegex(agents.SpecViolation, "invalid skills declaration"):
            agents.parse_source(self._write("skills: ['propose#adr'] # annotation\n"))

    def test_empty_flow_skill_element_is_rejected(self):
        with self.assertRaisesRegex(agents.SpecViolation, "empty"):
            agents.parse_source(self._write("skills: [forge-skill,, log-work]\n"))

    def test_duplicate_top_level_skills_key_is_rejected(self):
        with self.assertRaisesRegex(agents.SpecViolation, "duplicate top-level skills"):
            agents.parse_source(
                self._write("skills: [forge-skill]\nskills: [log-work]\n")
            )


class CatalogFailClosedTests(unittest.TestCase):
    """AC-5 and AC-6 on the Codex projection: the loader refuses before any write.

    The roster check moved out of `parse_source` — which sees one file and
    cannot know the roster — into the catalog load `generate()` performs first.
    That is what makes both directions of the mismatch catchable, not only the
    "file with no entry" direction the old per-file check covered.
    """

    def _fixture_source_dir(self, tmp: Path, *, drop=None, extra=None) -> Path:
        out = tmp / "agents"
        out.mkdir()
        for src in sorted(agents.SOURCE_DIR.glob("*.md")):
            if drop is not None and src.stem == drop:
                continue
            shutil.copy2(src, out / src.name)
        if extra is not None:
            (out / f"{extra}.md").write_text(
                f"---\nname: {extra}\ndescription: Synthetic.\ntools: Read\nmodel: sonnet\n---\nBody.\n",
                encoding="utf-8",
            )
        return out

    def test_agent_file_with_no_roster_entry_raises_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            source = self._fixture_source_dir(tmp, extra="interloper")
            out = tmp / "out"
            with self.assertRaises(agents.SpecViolation):
                agents.write(out, agents.generate(source))
            self.assertFalse(out.exists())

    def test_stale_roster_entry_with_no_agent_file_raises(self):
        with tempfile.TemporaryDirectory() as d:
            source = self._fixture_source_dir(Path(d), drop="wayfinder")
            with self.assertRaises(agents.SpecViolation):
                agents.generate(source)

    def test_a_symlinked_source_leaf_is_refused_before_it_is_rendered(self):
        """[SECURITY:S5] The Codex mirror of the OpenCode leaf guard.

        `parse_source` is where a `crux/agents/*.md` file's bytes enter
        `.codex/agents/crux-<role>.toml`, so a planted link would render
        out-of-tree content into a generated file.
        """
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            source = self._fixture_source_dir(tmp)
            victim = tmp / "outside.md"
            victim.write_text(
                "---\nname: wayfinder\ndescription: Planted.\ntools: Read\nmodel: sonnet\n"
                "---\nPLANTED-FROM-OUTSIDE\n",
                encoding="utf-8",
            )
            leaf = source / "wayfinder.md"
            leaf.unlink()
            leaf.symlink_to(victim)

            with self.assertRaises(agents.SpecViolation) as ctx:
                agents.generate(source)
        self.assertIn("symlink", str(ctx.exception))

    def test_a_dangling_symlinked_source_leaf_is_a_spec_violation(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            source = self._fixture_source_dir(tmp)
            leaf = source / "wayfinder.md"
            leaf.unlink()
            leaf.symlink_to(tmp / "does-not-exist.md")

            with self.assertRaises(agents.SpecViolation):
                agents.generate(source)


if __name__ == "__main__":
    unittest.main()
