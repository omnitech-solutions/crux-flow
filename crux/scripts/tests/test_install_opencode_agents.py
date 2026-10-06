"""Regression tests for the OpenCode installer lane of the agent projection.

`test_generate_opencode_agents.py` covers the transform rules and the dev-repo
regenerator's --dry-run drift contract. This file covers what that one does not:
the `opencode_agents` module's installer-facing surface (MANAGED_FILENAMES,
diff, write, symlink refusal) and the `install-opencode-agents` installer
(containment, no-clobber, --force). It mirrors the Codex installer coverage in
`test_generate_codex_agents.py`, because the two lanes share a threat model:
writing generated files into a repo the user also edits.

Stdlib only. Run: uv run python3 crux/scripts/tests/test_install_opencode_agents.py
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = SCRIPTS_DIR.parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

import opencode_agents as agents  # noqa: E402

EXPECTED_SOURCE_NAMES = {
    "architect", "brainstormer", "commander", "dev-lead", "developer",
    "historian", "librarian", "night-gardener", "reviewer", "wayfinder",
}
EXPECTED_FILES = {f"{name}.md" for name in EXPECTED_SOURCE_NAMES}
INSTALLER = REPO_ROOT / "crux" / "skills" / "install-opencode-agents" / "scripts" / "install.py"
SOURCE_DIR = REPO_ROOT / "crux" / "agents"


_RUNNER_STUB_DIR: tempfile.TemporaryDirectory | None = None
_RUNNER_STUB: Path | None = None

# What a real OpenCode 2.0.3 prints for `--version` on a machine that has it:
# one token, no banner, exit 0. Both `opencode` and `opencode2` print this
# after the 2.x upgrade, which is why the installer classifies on the version
# and not on the name.
REPORTED_V2 = "opencode v2.0.3"
REPORTED_V1 = "opencode v1.4.2"


def _make_runner_stub(
    directory: Path,
    *,
    exit_code: int = 0,
    name: str = "opencode",
    reported: str = REPORTED_V2,
) -> Path:
    """Write an executable stand-in for the `--version` preflight probe.

    The installer's preflight shells out to a real binary. CI carries no
    OpenCode, so without an override every installer test would fail on an
    environment fact rather than on the behaviour under test. The installer
    reads the runner name from `CRUX_OPENCODE_BIN`, and these tests point it
    at a stub. `RunnerPreflightTests` is where the stub is the SUBJECT rather
    than the scaffolding: it varies `reported` and `exit_code` across the
    verdict matrix, so each lane is exercised against the same code path the
    default name reaches.

    `reported` goes to stdout because the classifier reads stdout's first
    line. A stub that printed to stderr would model no real runner and would
    make every case read as unestablished.
    """
    path = directory / name
    path.write_text(
        "#!/bin/sh\n"
        f'if [ "$1" = "--version" ]; then echo "{reported}"; fi\n'
        f"exit {exit_code}\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def setUpModule() -> None:
    global _RUNNER_STUB_DIR, _RUNNER_STUB
    _RUNNER_STUB_DIR = tempfile.TemporaryDirectory()
    _RUNNER_STUB = _make_runner_stub(Path(_RUNNER_STUB_DIR.name))


def tearDownModule() -> None:
    if _RUNNER_STUB_DIR is not None:
        _RUNNER_STUB_DIR.cleanup()


def _env_with_runner(**overrides: str) -> dict[str, str]:
    """os.environ plus a compatible runner, unless an override says otherwise."""
    env = dict(os.environ)
    env["CRUX_OPENCODE_BIN"] = str(_RUNNER_STUB)
    env.pop("CRUX_OPENCODE2_BIN", None)
    env.update(overrides)
    return env


def _env_without_runner(bin_dir: Path | None = None, **overrides: str) -> dict[str, str]:
    """os.environ with PATH scrubbed to `bin_dir` alone and no override set.

    Needed because a developer machine HAS OpenCode installed. A test that
    asserts "no candidate resolves" while inheriting the ambient PATH measures
    the developer's machine, not the installer: `shutil.which("opencode")`
    finds the real one and the case silently stops testing what it names. So
    PATH is replaced rather than prepended, and both env overrides are cleared.
    """
    env = dict(os.environ)
    env["PATH"] = str(bin_dir) if bin_dir is not None else os.devnull
    env.pop("CRUX_OPENCODE_BIN", None)
    env.pop("CRUX_OPENCODE2_BIN", None)
    env.update(overrides)
    return env


def _run_installer_isolated(installer: Path, repo: Path) -> subprocess.CompletedProcess[str]:
    """Run a scratch-tree installer with the dev tree's `crux/scripts` off sys.path.

    A scratch plugin tree that OMITS a module only models a broken install if
    that module is genuinely unimportable. In this repo it is not: the
    development venv carries a `.pth` that appends `<repo>/crux/scripts` to
    every interpreter's `sys.path`, so `import models_catalog` succeeds from
    the ambient dev tree and the scratch omission is invisible. That is the
    same dev-tree/staged-artifact asymmetry that let F1 ship — the dev suite
    was green while the staged artifact raised at import.

    So the child is launched through a driver that removes that entry before
    executing the installer. Under the staged artifact the entry is the one
    `sync.sh` exports as PYTHONPATH, so it is removed there too: the test
    measures the same thing in both worlds instead of only one.

    The comparison is on RESOLVED paths, and that is load-bearing rather than
    tidiness. `sync.sh` builds its stage with `mktemp -d`, which on macOS
    yields an unresolved `/var/folders/...` path, and exports it as
    PYTHONPATH; `SCRIPTS_DIR` is `.resolve()`d to the `/private/var/...`
    form. A string compare therefore never matched the staged entry, the real
    `models_catalog` stayed importable, and the scratch tree's deliberate
    omission was invisible — the dev suite passed while the staged gate
    failed. (Distinct from the `.pth` leak above, which is real and separate.)
    """
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
        f"sys.argv = ['install.py', '--repo-root', {str(repo)!r}]\n"
        f"runpy.run_path({str(installer)!r}, run_name='__main__')\n"
    )
    return subprocess.run(
        [sys.executable, "-c", driver],
        check=False, capture_output=True, text=True, env=_env_with_runner(),
    )


class ManagedRosterTests(unittest.TestCase):
    """MANAGED_FILENAMES is the definition of 'crux-managed' for this lane.

    Unlike the Codex projection there is no `crux-` filename prefix to glob for
    (ADR-0048 settled collisions by choosing non-colliding role names), so the
    roster IS the safety boundary: anything outside it is a user file the
    installer must never read, overwrite, or delete.
    """

    def test_roster_matches_the_generated_set(self):
        self.assertEqual(agents.MANAGED_FILENAMES, EXPECTED_FILES)
        self.assertEqual(set(agents.generate(SOURCE_DIR)), EXPECTED_FILES)

    def test_roster_is_bare_role_names_not_namespaced(self):
        # A regression to `crux-architect.md` would rename every agent in every
        # user's OpenCode install; pin the bare form explicitly.
        for name in agents.MANAGED_FILENAMES:
            self.assertFalse(name.startswith("crux-"), name)
            self.assertFalse(name.startswith("crux_"), name)


def _parse_rules(text):
    """The emitted V2 `permissions:` array, as rule dicts.

    Strict: it raises on any line inside the block it does not recognize,
    rather than returning an empty list that every assertion below would pass
    over. The installer lane keeps its own reader so the two suites can never
    agree on a shape neither one emits.
    """
    m = re.match(r"^---\n(.*?\n)---\n", text, re.S)
    lines = (m.group(1) if m else text).splitlines()
    if "permissions:" not in lines:
        raise ValueError("no top-level `permissions:` block")
    i = lines.index("permissions:") + 1
    rules = []
    while i < len(lines) and lines[i][:1] == " ":
        head = re.match(r"^  - action: ([a-z]+)$", lines[i])
        if head is None:
            raise ValueError(f"unrecognized rule line {lines[i]!r}")
        if i + 2 >= len(lines):
            raise ValueError(f"truncated rule at {lines[i]!r}")
        res = re.match(r'^    resource: "([^"]*)"$', lines[i + 1])
        eff = re.match(r"^    effect: (allow|deny)$", lines[i + 2])
        if res is None or eff is None:
            raise ValueError(f"malformed rule at {lines[i]!r}")
        rules.append({"action": head.group(1), "resource": res.group(1),
                      "effect": eff.group(1)})
        i += 3
    if not rules:
        raise ValueError("`permissions:` block is empty")
    return rules


class SubagentProjectionTests(unittest.TestCase):
    """ADR-0100 point 3: the shared transform projects the dispatch grant onto
    ordered `subagent` rules, deny-first. install.py calls the same
    `opencode_agents` transform (via generate()), so this is the installer
    lane's copy of the assertion in test_generate_opencode_agents.py — a
    regression is caught on both paths. transform() takes an already-resolved
    model, so a literal is passed here rather than reaching into the catalog.

    The full ordered block is asserted as a LIST. Under last-match-wins a
    substring assertion says nothing about whether a later rule re-grants what
    an earlier one denied.
    """

    MODEL = "openrouter/test/model"

    def _fm(self, tools, extra=""):
        return f"description: Use when ...\ntools: {tools}\n{extra}"

    def _subagent_rules(self, name, tools, extra=""):
        rules = _parse_rules(agents.transform(name, self._fm(tools, extra), self.MODEL))
        return rules, [r for r in rules if r["action"] == "subagent"]

    def test_restricted_roles_emit_the_broad_deny_before_every_allow(self):
        rules, subagent = self._subagent_rules(
            "dev-lead", "Agent(reviewer), Agent(developer)")
        self.assertEqual(subagent, [
            {"action": "subagent", "resource": "*", "effect": "deny"},
            {"action": "subagent", "resource": "developer", "effect": "allow"},
            {"action": "subagent", "resource": "reviewer", "effect": "allow"},
        ])
        deny_at = rules.index(subagent[0])
        for allow in subagent[1:]:
            self.assertLess(deny_at, rules.index(allow))

    def test_bare_agent_is_one_wildcard_allow(self):
        _, subagent = self._subagent_rules("dev-lead", "Agent")
        self.assertEqual(
            subagent, [{"action": "subagent", "resource": "*", "effect": "allow"}])

    def test_absent_agent_grant_is_one_wildcard_deny(self):
        _, subagent = self._subagent_rules("developer", "Read")
        self.assertEqual(
            subagent, [{"action": "subagent", "resource": "*", "effect": "deny"}])

    def test_disallowed_agent_collapses_to_one_wildcard_deny(self):
        _, subagent = self._subagent_rules(
            "dev-lead", "Agent(developer)", extra="disallowedTools: Agent\n")
        self.assertEqual(
            subagent, [{"action": "subagent", "resource": "*", "effect": "deny"}])

    def test_the_installer_lane_emits_no_legacy_permission_map(self):
        # ADR-0100 point 1: no legacy fallback beside the array, on the
        # installer path too. Line-anchored — `permissions:` contains
        # `permission` as a substring.
        out = agents.transform("dev-lead", self._fm("Read, Agent(developer)"), self.MODEL)
        self.assertIsNone(re.search(r"^permission:", out, re.M))
        self.assertIsNotNone(re.search(r"^permissions:", out, re.M))

    def test_the_installer_lane_drops_name_and_metadata(self):
        # ADR-0100 point 4: either key beside the array voids every rule in it,
        # so the installer must not write one either.
        fm = (
            "name: developer\n"
            "description: Use when ...\n"
            "tools: Read\n"
            "metadata:\n"
            '  tags: "agents, implementation"\n'
            '  risk_level: "medium"\n'
        )
        out = agents.transform("developer", fm, self.MODEL)
        self.assertIsNone(re.search(r"^name:", out, re.M))
        self.assertIsNone(re.search(r"^metadata:", out, re.M))
        self.assertNotIn("risk_level", out)
        # Positive control: the same predicates DO fire on the source text,
        # so the absence assertions above are not vacuous.
        self.assertIsNotNone(re.search(r"^name:", fm, re.M))
        self.assertIsNotNone(re.search(r"^metadata:", fm, re.M))


class DiffAndWriteTests(unittest.TestCase):
    def setUp(self):
        self.generated = agents.generate(SOURCE_DIR)

    def test_unmanaged_files_are_invisible_to_diff(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / "my-own.md").write_text("mine\n", encoding="utf-8")
            added, changed, removed = agents.diff(out, self.generated)
            self.assertEqual(set(added), EXPECTED_FILES)
            self.assertEqual(changed, [])
            self.assertEqual(removed, [])

    def test_write_preserves_unmanaged_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            mine = out / "my-own.md"
            mine.write_text("mine\n", encoding="utf-8")
            written, removed = agents.write(out, self.generated)
            self.assertEqual(set(written), EXPECTED_FILES)
            self.assertEqual(removed, [])
            self.assertEqual(mine.read_text(encoding="utf-8"), "mine\n")

    def test_write_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            agents.write(out, self.generated)
            _, changed, removed = agents.diff(out, self.generated)
            self.assertEqual(changed, [])
            self.assertEqual(removed, [])

    def test_locally_edited_managed_file_is_reported_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            agents.write(out, self.generated)
            (out / "developer.md").write_text("locally changed\n", encoding="utf-8")
            _, changed, _ = agents.diff(out, self.generated)
            self.assertEqual(changed, ["developer.md"])

    def test_stale_managed_file_is_reported_and_removed(self):
        # A role dropped upstream: present on disk, in the roster, not generated.
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            agents.write(out, self.generated)
            partial = {k: v for k, v in self.generated.items() if k != "wayfinder.md"}
            _, _, removed = agents.diff(out, partial)
            self.assertEqual(removed, ["wayfinder.md"])
            _, actually_removed = agents.write(out, partial)
            self.assertEqual(actually_removed, ["wayfinder.md"])
            self.assertFalse((out / "wayfinder.md").exists())

    def test_write_remove_stale_false_keeps_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            agents.write(out, self.generated)
            partial = {k: v for k, v in self.generated.items() if k != "wayfinder.md"}
            _, removed = agents.write(out, partial, remove_stale=False)
            self.assertEqual(removed, [])
            self.assertTrue((out / "wayfinder.md").exists())


class SymlinkRefusalTests(unittest.TestCase):
    """A managed leaf that is a symlink is refused, never followed.

    `Path.write_text` follows a link and would clobber its target, so refusal
    must happen before any write and must not depend on the target being
    outside the repo — a contained target is still someone's file.
    """

    def setUp(self):
        self.generated = agents.generate(SOURCE_DIR)

    def test_diff_refuses_live_leaf_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "agent"
            out.mkdir()
            victim = Path(tmp) / "victim.md"
            victim.write_text("VICTIM\n", encoding="utf-8")
            (out / "architect.md").symlink_to(victim)
            with self.assertRaises(agents.SpecViolation) as ctx:
                agents.diff(out, self.generated)
            self.assertIn("architect.md", str(ctx.exception))
            self.assertEqual(victim.read_text(encoding="utf-8"), "VICTIM\n")

    def test_diff_refuses_dangling_leaf_symlink_with_clean_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "agent"
            out.mkdir()
            (out / "reviewer.md").symlink_to(Path(tmp) / "gone.md")
            # A raw FileNotFoundError here would leak an OSError to the caller.
            with self.assertRaises(agents.SpecViolation):
                agents.diff(out, self.generated)

    def test_write_never_clobbers_a_symlink_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "agent"
            out.mkdir()
            victim = Path(tmp) / "victim.md"
            victim.write_text("VICTIM\n", encoding="utf-8")
            (out / "librarian.md").symlink_to(victim)
            with self.assertRaises(agents.SpecViolation):
                agents.write(out, self.generated)
            self.assertEqual(victim.read_text(encoding="utf-8"), "VICTIM\n")
            # Fail-closed means NOTHING was written, not "everything but the link".
            self.assertFalse((out / "architect.md").exists())


class InstallerTests(unittest.TestCase):
    def _run(
        self, repo_root: Path, *args: str, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(INSTALLER), "--repo-root", str(repo_root), *args],
            check=False, capture_output=True, text=True, env=env or _env_with_runner(),
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
        scratch_scripts = plugin_root / "skills" / "install-opencode-agents" / "scripts"
        scratch_scripts.mkdir(parents=True)
        # All THREE modules, because all three ship together. Copying only
        # `opencode_agents.py` modelled an install that cannot exist, and the
        # gap was invisible in the dev tree — there, `models_catalog` is
        # importable from sys.path, so the fixture accidentally worked. Under
        # the staged release artifact it was not, and the by-location fallback
        # raised FileNotFoundError at import time: exit 1 and a traceback where
        # this test asserts exit 2 and structured JSON.
        for module in ("opencode_agents.py", "models_catalog.py", "_yaml_min.py"):
            shutil.copy2(SCRIPTS_DIR / module, plugin_root / "scripts")
        shutil.copy2(INSTALLER, scratch_scripts)
        return scratch_scripts / "install.py"

    def _scratch_installer_without_models_catalog(self, scratch: Path) -> Path:
        """A scratch plugin tree with a real source agent but NO models_catalog.py.

        The other fixture makes `generate()` fail on an empty source roster,
        which never reaches the catalog at all. This one exercises the OTHER
        lane: `opencode_agents` imports cleanly (its by-location fallback
        installs a `_DeferredCatalogFault` stand-in rather than raising), the
        installer's module-level `from opencode_agents import ...` therefore
        succeeds, and the fault surfaces at FIRST USE — inside the handler,
        as the `SpecViolation` the stand-in serves. Providing a source agent
        is what makes the difference: without one, `generate()` fails before
        it touches the catalog and this lane is never entered.
        """
        plugin_root = scratch / "crux"
        (plugin_root / "agents").mkdir(parents=True)
        (plugin_root / "scripts").mkdir()
        scratch_scripts = plugin_root / "skills" / "install-opencode-agents" / "scripts"
        scratch_scripts.mkdir(parents=True)
        shutil.copy2(SOURCE_DIR / "architect.md", plugin_root / "agents" / "architect.md")
        # `models_catalog.py` deliberately omitted — this models the broken
        # install F1 reported, where half of a set that ships together is gone.
        for module in ("opencode_agents.py", "_yaml_min.py"):
            shutil.copy2(SCRIPTS_DIR / module, plugin_root / "scripts")
        shutil.copy2(INSTALLER, scratch_scripts)
        return scratch_scripts / "install.py"

    def test_missing_models_catalog_is_exit_two_json_not_a_traceback(self):
        """[F1 lane] A broken install reports; it does not crash.

        The regression this pins: `spec_from_file_location` on an absent path
        raises FileNotFoundError from `exec_module`, and that happened while
        `opencode_agents` was still executing — BEFORE the installer's
        `except SpecViolation` existed. The process died with a traceback and
        exit 1, and exit-1 stdout is contractually the JSON diff report, so a
        capability error was being reported as reviewable drift.
        """
        with tempfile.TemporaryDirectory() as tmp:
            scratch = Path(tmp)
            installer = self._scratch_installer_without_models_catalog(scratch)
            repo = scratch / "repo"
            repo.mkdir()

            result = _run_installer_isolated(installer, repo)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            payload = json.loads(result.stdout)
            # The stand-in's message names the file the install is missing —
            # not a `models.yml` contract violation, which is what a leaked
            # real `models_catalog` would report instead.
            self.assertIn("models_catalog.py", payload["error"])
            self.assertIn("ships beside", payload["error"])
            self.assertFalse((repo / ".opencode").exists())

    def test_translates_generate_spec_violation_to_exit_two(self):
        # generate() sits inside the same fail-closed handler as diff(), so a
        # missing source roster is a structured exit-2 error rather than a
        # traceback and exit 1 — exit 1 stdout is contractually the JSON diff
        # report, so a capability error must never land there.
        with tempfile.TemporaryDirectory() as tmp:
            scratch = Path(tmp)
            installer = self._scratch_installer_without_source_agents(scratch)
            repo = scratch / "repo"
            repo.mkdir()

            result = subprocess.run(
                [sys.executable, str(installer), "--repo-root", str(repo)],
                check=False, capture_output=True, text=True, env=_env_with_runner(),
            )
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn("no source agents", json.loads(result.stdout)["error"])
            self.assertNotIn("Traceback", result.stderr)
            self.assertFalse((repo / ".opencode").exists())

    def test_installs_into_plural_agents_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            result = self._run(repo)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(set(json.loads(result.stdout)["written"]), EXPECTED_FILES)
            self.assertEqual(
                {p.name for p in (repo / ".opencode" / "agents").iterdir()}, EXPECTED_FILES
            )
            self.assertTrue((repo / ".opencode" / "agents").is_dir())
            self.assertFalse((repo / ".opencode" / "agent").exists())

    def test_writes_without_touching_a_project_agent(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            custom = repo / ".opencode" / "agents" / "project-reviewer.md"
            custom.parent.mkdir(parents=True)
            custom.write_text("mine\n", encoding="utf-8")

            self.assertEqual(self._run(repo).returncode, 0)
            self.assertEqual(custom.read_text(encoding="utf-8"), "mine\n")
            # --force must not widen the blast radius to unmanaged files.
            self.assertEqual(self._run(repo, "--force").returncode, 0)
            self.assertEqual(custom.read_text(encoding="utf-8"), "mine\n")

    def test_refuses_changed_generated_agent_without_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self.assertEqual(self._run(repo).returncode, 0)
            (repo / ".opencode" / "agents" / "developer.md").write_text("edited\n", encoding="utf-8")

            result = self._run(repo)
            self.assertEqual(result.returncode, 1)
            self.assertIn("developer.md", json.loads(result.stdout)["changed"])

            self.assertEqual(self._run(repo, "--force").returncode, 0)
            self.assertNotEqual(
                (repo / ".opencode" / "agents" / "developer.md").read_text(encoding="utf-8"),
                "edited\n",
            )

    def test_refuses_missing_repo_root(self):
        result = self._run(Path("/nonexistent-crux-test-root"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("error", json.loads(result.stdout))

    def test_refuses_when_output_dir_escapes_repo_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            outside = Path(tmp) / "outside"
            repo.mkdir()
            outside.mkdir()
            (repo / ".opencode").mkdir()
            (repo / ".opencode" / "agents").symlink_to(outside, target_is_directory=True)

            result = self._run(repo)
            self.assertEqual(result.returncode, 2)
            self.assertIn("outside the repo root", json.loads(result.stdout)["error"])
            self.assertEqual(list(outside.iterdir()), [])

    def test_permits_contained_symlink_output_dir(self):
        # resolve-then-contain: a link whose target stays inside the repo is fine.
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            real = repo / "real-agents"
            real.mkdir()
            (repo / ".opencode").mkdir()
            (repo / ".opencode" / "agents").symlink_to(real, target_is_directory=True)

            result = self._run(repo)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue((real / "architect.md").is_file())

    def test_refuses_when_output_dir_is_a_regular_file(self):
        # `mkdir(exist_ok=True)` raises FileExistsError when the path exists as a
        # non-directory, so an unguarded write() ends the run in a traceback and
        # exit 1. The contract is a structured error and exit 2.
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / ".opencode").mkdir()
            blocker = repo / ".opencode" / "agents"
            blocker.write_text("NOT A DIRECTORY\n", encoding="utf-8")

            result = self._run(repo)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn("error", json.loads(result.stdout))
            self.assertEqual(blocker.read_text(encoding="utf-8"), "NOT A DIRECTORY\n")

    def test_refuses_output_dir_symlinked_to_a_regular_file(self):
        # The contained-directory-symlink allowance is about a link that resolves
        # to a DIRECTORY. A link resolving to a regular file is a non-directory
        # output path: refuse with a structured error, and never write through it.
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            victim = repo / "victim.md"
            victim.write_text("VICTIM\n", encoding="utf-8")
            (repo / ".opencode").mkdir()
            (repo / ".opencode" / "agents").symlink_to(victim)

            result = self._run(repo)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn("error", json.loads(result.stdout))
            self.assertEqual(victim.read_text(encoding="utf-8"), "VICTIM\n")

    def test_refuses_dangling_symlink_output_dir(self):
        # A dangling link is absent to `exists()` but present to `lexists`; the
        # guard must see it, refuse, leave the link alone, and not create the
        # target directory behind it.
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / ".opencode").mkdir()
            target = repo / "nowhere"
            link = repo / ".opencode" / "agents"
            link.symlink_to(target, target_is_directory=True)

            result = self._run(repo)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn("error", json.loads(result.stdout))
            self.assertTrue(link.is_symlink())
            self.assertFalse(target.exists(), "the link target must not be created")

    def test_refuses_leaf_symlink_escaping_repo_even_with_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            victim = Path(tmp) / "victim.md"
            victim.write_text("VICTIM\n", encoding="utf-8")
            agent_dir = repo / ".opencode" / "agents"
            agent_dir.mkdir(parents=True)
            (agent_dir / "architect.md").symlink_to(victim)

            result = self._run(repo, "--force")
            self.assertEqual(result.returncode, 2)
            self.assertIn("error", json.loads(result.stdout))
            self.assertEqual(victim.read_text(encoding="utf-8"), "VICTIM\n")


class LegacyAgentDirTests(unittest.TestCase):
    """The singular `.opencode/agent/` guard, its migration flag and its collision policy.

    Five cases, and the asymmetry between the third and the fourth is itself
    the acceptance criterion: deleting the collision guard must FAIL the third
    while the fourth and fifth still pass. A guard that refused
    unconditionally would satisfy the third and fail the fourth, so neither
    half stands alone.

    "Populated" is decided by roster filename alone. Crux stamps no provenance
    marker on a projected agent file, so a user's own `.opencode/agent/
    architect.md` is indistinguishable from a crux-managed one and DOES trip
    the guard. That is a stated limitation of the design, not of these tests.
    """

    def _run(
        self, repo_root: Path, *args: str, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(INSTALLER), "--repo-root", str(repo_root), *args],
            check=False, capture_output=True, text=True, env=env or _env_with_runner(),
        )

    @staticmethod
    def _generated() -> dict[str, str]:
        return agents.generate(SOURCE_DIR)

    def _collision_fixture(self, repo: Path) -> tuple[list[str], str, dict[str, str]]:
        """Three roster-named legacy files; two already carry their names at the DESTINATION.

        The legacy bodies are the GENERATED bytes, so a completed migration
        leaves the destination matching the projection and the run reaches
        exit 0 through the ordinary no-clobber path rather than through
        `--force`. That is what makes the fourth and fifth cases positive
        controls rather than restatements of the third.

        Returns (colliding_names, non_colliding_name, destination_sentinels).
        """
        generated = self._generated()
        legacy = repo / ".opencode" / "agent"
        dest = repo / ".opencode" / "agents"
        legacy.mkdir(parents=True)
        dest.mkdir(parents=True)

        colliding = ["architect.md", "developer.md"]
        non_colliding = "reviewer.md"
        sentinels: dict[str, str] = {}
        for name in [*colliding, non_colliding]:
            (legacy / name).write_text(generated[name], encoding="utf-8")
        for name in colliding:
            sentinels[name] = f"# live destination copy of {name}\n"
            (dest / name).write_text(sentinels[name], encoding="utf-8")
        return colliding, non_colliding, sentinels

    # ---- case 1 -----------------------------------------------------------
    def test_populated_legacy_dir_refuses_and_names_the_offending_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            legacy = repo / ".opencode" / "agent"
            legacy.mkdir(parents=True)
            (legacy / "architect.md").write_text("stale\n", encoding="utf-8")
            (legacy / "not-a-role.md").write_text("mine\n", encoding="utf-8")

            result = self._run(repo)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["legacy_files"], ["architect.md"])
            self.assertIn("architect.md", payload["error"])
            # An unmanaged name is not an offender, and nothing was written.
            self.assertNotIn("not-a-role.md", result.stdout)
            self.assertFalse((repo / ".opencode" / "agents").exists())
            self.assertEqual((legacy / "architect.md").read_text(encoding="utf-8"), "stale\n")

    # ---- case 2 -----------------------------------------------------------
    def test_migrate_flag_moves_the_legacy_files_and_proceeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            legacy = repo / ".opencode" / "agent"
            legacy.mkdir(parents=True)
            (legacy / "architect.md").write_text(
                self._generated()["architect.md"], encoding="utf-8"
            )

            result = self._run(repo, "--migrate-legacy-agent-dir")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["migrated"], ["architect.md"])
            self.assertFalse((legacy / "architect.md").exists())
            self.assertEqual(
                {p.name for p in (repo / ".opencode" / "agents").iterdir()}, EXPECTED_FILES
            )

    # ---- case 3 -----------------------------------------------------------
    def test_destination_collision_moves_nothing_and_names_every_collider(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            colliding, non_colliding, sentinels = self._collision_fixture(repo)
            legacy = repo / ".opencode" / "agent"
            dest = repo / ".opencode" / "agents"

            result = self._run(repo, "--migrate-legacy-agent-dir")

            # (a) refusal exit
            self.assertNotEqual(result.returncode, 0, result.stdout)
            payload = json.loads(result.stdout)

            # (b) every colliding destination file's bytes are unchanged — the
            #     discriminator against a refusal taken for any other reason.
            for name, body in sentinels.items():
                self.assertEqual((dest / name).read_text(encoding="utf-8"), body)

            # (c) the error names EVERY collider (a single-name implementation
            #     fails on the second one).
            self.assertEqual(sorted(payload["colliding"]), sorted(colliding))
            for name in colliding:
                self.assertIn(name, payload["error"])

            # (d) whole-move atomicity: the NON-colliding legacy file did not move.
            self.assertTrue((legacy / non_colliding).exists())
            self.assertFalse((dest / non_colliding).exists())

    # ---- case 4 (positive control for case 3) -----------------------------
    def test_same_fixture_without_the_colliders_completes(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            colliding, non_colliding, _ = self._collision_fixture(repo)
            dest = repo / ".opencode" / "agents"
            for name in colliding:
                (dest / name).unlink()

            result = self._run(repo, "--migrate-legacy-agent-dir")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(
                sorted(payload["migrated"]), sorted([*colliding, non_colliding])
            )
            self.assertEqual({p.name for p in dest.iterdir()}, EXPECTED_FILES)
            self.assertEqual(list((repo / ".opencode" / "agent").iterdir()), [])

    # ---- case 5 -----------------------------------------------------------
    def test_force_overrides_the_collision_refusal(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            colliding, non_colliding, sentinels = self._collision_fixture(repo)
            dest = repo / ".opencode" / "agents"

            result = self._run(repo, "--migrate-legacy-agent-dir", "--force")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(
                sorted(payload["migrated"]), sorted([*colliding, non_colliding])
            )
            for name, body in sentinels.items():
                self.assertNotEqual((dest / name).read_text(encoding="utf-8"), body)
            self.assertEqual({p.name for p in dest.iterdir()}, EXPECTED_FILES)

    # ---- containment of each legacy SOURCE path ---------------------------
    def test_legacy_source_symlink_escaping_the_repo_is_refused_unmoved(self):
        with tempfile.TemporaryDirectory() as tmp_outside, tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp_outside) / "victim.md"
            outside.write_text("victim bytes\n", encoding="utf-8")
            repo = Path(tmp)
            legacy = repo / ".opencode" / "agent"
            legacy.mkdir(parents=True)
            (legacy / "architect.md").symlink_to(outside)

            result = self._run(repo, "--migrate-legacy-agent-dir")
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("architect.md", json.loads(result.stdout)["error"])
            self.assertNotIn("Traceback", result.stderr)
            # The link target is untouched, and the link itself did not move.
            self.assertEqual(outside.read_text(encoding="utf-8"), "victim bytes\n")
            self.assertTrue((legacy / "architect.md").is_symlink())
            self.assertFalse((repo / ".opencode" / "agents" / "architect.md").exists())

    def test_force_does_not_override_legacy_source_containment(self):
        """`--force` is the collision override, not a containment override."""
        with tempfile.TemporaryDirectory() as tmp_outside, tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp_outside) / "victim.md"
            outside.write_text("victim bytes\n", encoding="utf-8")
            repo = Path(tmp)
            legacy = repo / ".opencode" / "agent"
            legacy.mkdir(parents=True)
            (legacy / "architect.md").symlink_to(outside)

            result = self._run(repo, "--migrate-legacy-agent-dir", "--force")
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertEqual(outside.read_text(encoding="utf-8"), "victim bytes\n")
            # Discriminator. Without it this test passes on an installer with
            # NO legacy containment at all: the move would succeed, `diff()`
            # would then refuse the relocated symlink, and the exit code and
            # the target bytes would look identical. Asserting the link never
            # left `.opencode/agent/` is what distinguishes "refused before
            # the move" from "moved, then caught downstream".
            self.assertTrue((legacy / "architect.md").is_symlink())
            self.assertFalse((repo / ".opencode" / "agents" / "architect.md").is_symlink())
            self.assertIn("refusing to migrate", json.loads(result.stdout)["error"])

    def test_an_empty_legacy_dir_is_not_populated(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / ".opencode" / "agent").mkdir(parents=True)
            self.assertEqual(self._run(repo).returncode, 0)
            self.assertEqual(
                {p.name for p in (repo / ".opencode" / "agents").iterdir()}, EXPECTED_FILES
            )


class RunnerPreflightTests(unittest.TestCase):
    """The preflight: classify the runner by the VERSION it reports.

    NECESSARY, NOT SUFFICIENT. A `compatible` verdict proves a 2.x runner
    answered `--version` on this machine; it does not bind which binary a human
    later invokes against the projection. These tests measure the verdict and
    the refusal, and claim nothing beyond them.

    The matrix below is the decision's classifier, one case per rule:
      * below-2 anywhere on the line dominates, whatever the exit status, and
        an assertion cannot override it;
      * `compatible` needs exactly one token reading 2, with exit 0;
      * anything else is unestablished, which an assertion MAY override when a
        candidate resolved.
    """

    def _run(
        self, repo_root: Path, env: dict[str, str], *args: str
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(INSTALLER), "--repo-root", str(repo_root), *args],
            check=False, capture_output=True, text=True, env=env,
        )

    def _verdict_for(self, reported: str, exit_code: int = 0) -> tuple[str, int, dict]:
        """Probe one stub and return (verdict, returncode, payload)."""
        with tempfile.TemporaryDirectory() as tmp_bin, tempfile.TemporaryDirectory() as tmp:
            stub = _make_runner_stub(
                Path(tmp_bin), name="runner-stub", reported=reported, exit_code=exit_code
            )
            result = self._run(Path(tmp), _env_with_runner(CRUX_OPENCODE_BIN=str(stub)))
            payload = json.loads(result.stdout)
            verdict = payload.get("verdict") or payload["runner"]["verdict"]
            return verdict, result.returncode, payload

    # --- the classifier matrix -------------------------------------------

    def test_a_bare_two_x_version_is_compatible(self):
        for reported in ("opencode v2.0.3", "opencode 2.0.3", "2.0.3", "v2.1"):
            with self.subTest(reported=reported):
                verdict, code, _ = self._verdict_for(reported)
                self.assertEqual(verdict, "compatible", reported)
                self.assertEqual(code, 0)

    def test_a_below_two_version_is_known_incompatible(self):
        for reported in ("opencode v1.4.2", "opencode 1.4", "0.9.1", "v1.4.2.1"):
            with self.subTest(reported=reported):
                verdict, code, payload = self._verdict_for(reported)
                self.assertEqual(verdict, "known-incompatible", reported)
                self.assertEqual(code, 2)
                self.assertIn("older than 2", payload["error"])

    def test_a_below_two_version_dominates_a_higher_token_on_the_same_line(self):
        """Token boundaries establish a token, never WHICH token is the runner's.

        `opencode v1.4.2 (node 22.1.0)` carries a 22. Reading the highest, or
        the last, would call a V1 runner compatible on the strength of its
        Node version. Below-2 anywhere wins.
        """
        verdict, code, _ = self._verdict_for("opencode v1.4.2 (node 22.1.0)")
        self.assertEqual(verdict, "known-incompatible")
        self.assertEqual(code, 2)

    def test_an_ambiguous_multi_token_line_is_unestablished_not_compatible(self):
        """Two tokens, none below 2: which one is the runner is not established."""
        verdict, code, _ = self._verdict_for("opencode 2.0.3 (node 22.1.0)")
        self.assertEqual(verdict, "unestablished")
        self.assertEqual(code, 2)

    def test_an_unreadable_report_is_unestablished(self):
        for reported in ("welcome to the tui", "", "opencode (dev build)"):
            with self.subTest(reported=reported):
                verdict, code, _ = self._verdict_for(reported)
                self.assertEqual(verdict, "unestablished", reported)
                self.assertEqual(code, 2)

    def test_a_two_x_report_that_exits_non_zero_is_unestablished(self):
        """`compatible` is the only verdict that requires exit 0."""
        verdict, code, _ = self._verdict_for("opencode v2.0.3", exit_code=1)
        self.assertEqual(verdict, "unestablished")
        self.assertEqual(code, 2)

    def test_a_below_two_report_that_exits_non_zero_is_still_known_incompatible(self):
        """The security invariant: a readable V1 report is evidence whatever the exit.

        Routing this to `unestablished` would make it assertion-eligible, and
        `--assume-compatible` would then write a V2 projection against a runner
        already known to ignore its denies.
        """
        verdict, code, payload = self._verdict_for("opencode v1.4.2", exit_code=1)
        self.assertEqual(verdict, "known-incompatible")
        self.assertEqual(code, 2)
        self.assertIn("older than 2", payload["error"])

    # --- what the assertion flag may and may not override ------------------

    def test_assume_compatible_cannot_override_known_incompatible(self):
        with tempfile.TemporaryDirectory() as tmp_bin, tempfile.TemporaryDirectory() as tmp:
            stub = _make_runner_stub(Path(tmp_bin), name="v1-stub", reported=REPORTED_V1)
            repo = Path(tmp)
            result = self._run(
                repo, _env_with_runner(CRUX_OPENCODE_BIN=str(stub)), "--assume-compatible"
            )
            self.assertEqual(result.returncode, 2, result.stdout)
            error = json.loads(result.stdout)["error"]
            self.assertIn("An assertion cannot override this verdict", error)
            self.assertFalse((repo / ".opencode").exists())

    def test_assume_compatible_writes_when_a_candidate_resolved_but_said_nothing(self):
        with tempfile.TemporaryDirectory() as tmp_bin, tempfile.TemporaryDirectory() as tmp:
            stub = _make_runner_stub(
                Path(tmp_bin), name="quiet-stub", reported="welcome to the tui"
            )
            repo = Path(tmp)
            result = self._run(
                repo, _env_with_runner(CRUX_OPENCODE_BIN=str(stub)), "--assume-compatible"
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            runner = json.loads(result.stdout)["runner"]
            self.assertEqual(runner["verdict"], "unestablished")
            self.assertTrue(runner["assumed_compatible"])
            self.assertEqual(runner["bound"], str(stub))
            self.assertEqual(
                {p.name for p in (repo / ".opencode" / "agents").iterdir()}, EXPECTED_FILES
            )

    def test_assume_compatible_refuses_when_nothing_resolved_at_all(self):
        """An assertion needs something to bind to. Nothing resolved, nothing asserted."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            result = self._run(repo, _env_without_runner(), "--assume-compatible")
            self.assertEqual(result.returncode, 2, result.stdout)
            error = json.loads(result.stdout)["error"]
            self.assertIn("nothing an assertion could bind to", error)
            self.assertFalse((repo / ".opencode").exists())

    # --- candidate resolution ---------------------------------------------

    def test_neither_candidate_on_path_refuses_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            result = self._run(repo, _env_without_runner())
            self.assertEqual(result.returncode, 2, result.stdout)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["verdict"], "unestablished")
            self.assertIn("opencode (not on PATH)", payload["error"])
            self.assertFalse((repo / ".opencode").exists())
            self.assertNotIn("Traceback", result.stderr)

    def test_an_override_naming_a_missing_binary_does_not_fall_back_to_path(self):
        """The override is the ONLY candidate when set.

        Falling back would silently probe a different binary than the one the
        operator named, and report a verdict about that other binary.
        """
        with tempfile.TemporaryDirectory() as tmp_bin, tempfile.TemporaryDirectory() as tmp:
            _make_runner_stub(Path(tmp_bin), name="opencode", reported=REPORTED_V2)
            repo = Path(tmp)
            env = _env_without_runner(
                Path(tmp_bin), CRUX_OPENCODE_BIN="runner-does-not-exist-anywhere"
            )
            result = self._run(repo, env)
            self.assertEqual(result.returncode, 2, result.stdout)
            payload = json.loads(result.stdout)
            self.assertEqual(
                [c["name"] for c in payload["candidates"]], ["runner-does-not-exist-anywhere"]
            )
            self.assertFalse((repo / ".opencode").exists())

    def test_the_legacy_name_alone_still_installs_and_is_reported(self):
        """An upgraded machine keeps `opencode2` as a shim onto the 2.x binary."""
        with tempfile.TemporaryDirectory() as tmp_bin, tempfile.TemporaryDirectory() as tmp:
            _make_runner_stub(Path(tmp_bin), name="opencode2", reported=REPORTED_V2)
            repo = Path(tmp)
            result = self._run(repo, _env_without_runner(Path(tmp_bin)))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            runner = json.loads(result.stdout)["runner"]
            self.assertEqual(runner["verdict"], "compatible")
            self.assertEqual(
                [c["name"] for c in runner["candidates"]], ["opencode", "opencode2"]
            )
            self.assertEqual(runner["candidates"][0]["resolved"], None)

    def test_a_two_x_candidate_outweighs_a_v1_sibling(self):
        """Strongest verdict wins: a 2.x runner exists, whatever else is installed.

        The refusal exists because a V1 runner would ignore the `permissions`
        array. It is not a claim that no V1 binary may exist on the machine, so
        a probed 2.x candidate settles it — and BOTH candidates stay in the
        payload so the operator can see the V1 one.
        """
        with tempfile.TemporaryDirectory() as tmp_bin, tempfile.TemporaryDirectory() as tmp:
            _make_runner_stub(Path(tmp_bin), name="opencode", reported=REPORTED_V2)
            _make_runner_stub(Path(tmp_bin), name="opencode2", reported=REPORTED_V1)
            repo = Path(tmp)
            result = self._run(repo, _env_without_runner(Path(tmp_bin)))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            runner = json.loads(result.stdout)["runner"]
            self.assertEqual(runner["verdict"], "compatible")
            self.assertEqual(
                [c["verdict"] for c in runner["candidates"]],
                ["compatible", "known-incompatible"],
            )
            self.assertEqual(runner["candidates"][1]["reported"], REPORTED_V1)

    def test_the_deprecated_env_name_still_works_and_says_it_is_deprecated(self):
        with tempfile.TemporaryDirectory() as tmp_bin, tempfile.TemporaryDirectory() as tmp:
            stub = _make_runner_stub(Path(tmp_bin), name="legacy-stub", reported=REPORTED_V2)
            repo = Path(tmp)
            env = _env_without_runner(CRUX_OPENCODE2_BIN=str(stub))
            result = self._run(repo, env)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            runner = json.loads(result.stdout)["runner"]
            self.assertEqual(runner["candidates"][0]["name"], str(stub))
            self.assertIn("CRUX_OPENCODE2_BIN is deprecated", runner["notice"])

    def test_the_current_env_name_wins_over_the_deprecated_one_without_a_notice(self):
        with tempfile.TemporaryDirectory() as tmp_bin, tempfile.TemporaryDirectory() as tmp:
            stub = _make_runner_stub(Path(tmp_bin), name="current-stub", reported=REPORTED_V2)
            repo = Path(tmp)
            env = _env_without_runner(
                CRUX_OPENCODE_BIN=str(stub), CRUX_OPENCODE2_BIN="ignored-does-not-exist"
            )
            result = self._run(repo, env)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            runner = json.loads(result.stdout)["runner"]
            self.assertEqual([c["name"] for c in runner["candidates"]], [str(stub)])
            self.assertNotIn("notice", runner)

    # --- the acceptance fixture matrix ------------------------------------
    #
    # Four candidate layouts, each run with AND without --assume-compatible.
    # Rows 2 and 3 are the discriminator against a preflight that folds to the
    # WEAKEST candidate verdict rather than the strongest; row 4 is the
    # discriminator against a flag that writes with nothing resolved. Every row
    # runs over a scrubbed PATH, because an inherited one would let this
    # machine's own OpenCode resolve as a fifth, unnamed candidate.

    def _layout(self, bin_dir: Path, **names: str | None) -> None:
        for name, reported in names.items():
            if reported is not None:
                _make_runner_stub(bin_dir, name=name, reported=reported)

    def _run_layout(self, layout: dict, *args: str) -> tuple[int, dict, bool]:
        """Install over a PATH holding exactly `layout`. Returns (code, payload, wrote)."""
        with tempfile.TemporaryDirectory() as tmp_bin, tempfile.TemporaryDirectory() as tmp:
            self._layout(Path(tmp_bin), **layout)
            repo = Path(tmp)
            result = self._run(repo, _env_without_runner(Path(tmp_bin)), *args)
            wrote = (repo / ".opencode" / "agents").is_dir()
            return result.returncode, json.loads(result.stdout), wrote

    def test_fixture_v1_first_with_a_two_x_second_is_compatible_and_writes_unconfigured(self):
        """Row 1. V1 is probed FIRST; the 2.x behind it still decides.

        This is the case an implementation that stopped at the first verdict,
        or folded to the weakest, would refuse — on a machine that has a
        working 2.x runner sitting right there.
        """
        for args in ((), ("--assume-compatible",)):
            with self.subTest(args=args):
                code, payload, wrote = self._run_layout(
                    {"opencode": REPORTED_V1, "opencode2": REPORTED_V2}, *args
                )
                self.assertEqual(code, 0, payload)
                self.assertEqual(payload["runner"]["verdict"], "compatible")
                self.assertTrue(wrote)

    def test_fixture_v1_alone_is_known_incompatible_with_or_without_the_flag(self):
        """Row 2. No second candidate to dilute the V1 evidence, and no override."""
        for args in ((), ("--assume-compatible",)):
            with self.subTest(args=args):
                code, payload, wrote = self._run_layout({"opencode": REPORTED_V1}, *args)
                self.assertEqual(code, 2, payload)
                self.assertEqual(payload["verdict"], "known-incompatible")
                self.assertFalse(wrote)

    def test_fixture_v1_beside_an_uninterpretable_second_is_known_incompatible(self):
        """Row 3. An unreadable candidate must not soften a readable V1 report.

        A preflight folding to the weakest verdict would land on unestablished
        here, and `--assume-compatible` would then write a V2 projection
        against a runner already known to drop every deny.
        """
        for args in ((), ("--assume-compatible",)):
            with self.subTest(args=args):
                code, payload, wrote = self._run_layout(
                    {"opencode": REPORTED_V1, "opencode2": "welcome to the tui"}, *args
                )
                self.assertEqual(code, 2, payload)
                self.assertEqual(payload["verdict"], "known-incompatible")
                self.assertFalse(wrote)

    def test_fixture_nothing_resolves_is_unestablished_with_or_without_the_flag(self):
        """Row 4. The flag asserts something ABOUT a runner; there is no runner."""
        for args in ((), ("--assume-compatible",)):
            with self.subTest(args=args):
                code, payload, wrote = self._run_layout({}, *args)
                self.assertEqual(code, 2, payload)
                self.assertEqual(payload["verdict"], "unestablished")
                self.assertFalse(wrote)

    def test_a_major_above_two_is_unestablished_and_never_compatible(self):
        """A 3.x runner is outside the evidence this decision covers.

        Admitting it as compatible would claim the projection is read correctly
        by a release nobody measured. It is refused until a human asserts, or a
        later decision widens the admitted set. Deleting the upper bound — for
        example classifying on `major >= 2` — turns this red.
        """
        for reported in ("opencode v3.0.0", "opencode 4.2.1", "v10.0.0"):
            with self.subTest(reported=reported):
                verdict, code, _ = self._verdict_for(reported)
                self.assertEqual(verdict, "unestablished", reported)
                self.assertEqual(code, 2)

    def test_the_flag_binds_to_the_first_resolved_candidate_in_probe_order(self):
        """Several resolved unestablished candidates: probe order decides the binding.

        Both resolve and neither parses, so the flag may write. The record must
        name `opencode` — the first in probe order — not whichever the code
        happened to visit last.
        """
        with tempfile.TemporaryDirectory() as tmp_bin, tempfile.TemporaryDirectory() as tmp:
            first = _make_runner_stub(
                Path(tmp_bin), name="opencode", reported="welcome to the tui"
            )
            _make_runner_stub(Path(tmp_bin), name="opencode2", reported="some other banner")
            repo = Path(tmp)
            result = self._run(
                repo, _env_without_runner(Path(tmp_bin)), "--assume-compatible"
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            runner = json.loads(result.stdout)["runner"]
            self.assertEqual(runner["verdict"], "unestablished")
            self.assertTrue(runner["assumed_compatible"])
            self.assertEqual(runner["bound"], str(first))
            self.assertEqual(
                {p.name for p in (repo / ".opencode" / "agents").iterdir()}, EXPECTED_FILES
            )

    def test_an_unestablished_refusal_names_the_verdict_and_both_remedies(self):
        """And it must NOT claim no OpenCode runner is installed.

        Nothing resolved means nothing was asked. Saying "no V2 runner found"
        asserts a fact the installer did not establish, and sends a user to
        reinstall a runner they may already have.
        """
        code, payload, _ = self._run_layout({"opencode": "welcome to the tui"})
        self.assertEqual(code, 2)
        error = payload["error"]
        self.assertEqual(payload["verdict"], "unestablished")
        self.assertIn("unestablished", error)
        # The decision's two remedies: point the configuration at the runner, or
        # install OpenCode V2. Naming only the configuration strands a user who has
        # no OpenCode at all — the installer cannot tell that case apart from a
        # runner installed under a name it does not probe.
        self.assertIn("CRUX_OPENCODE_BIN", error)
        self.assertIn("install opencode 2.x", error.lower())
        self.assertIn("--assume-compatible", error)
        self.assertNotIn("no OpenCode V2 runner found", error)
        self.assertIn("does not mean no OpenCode runner is installed", error)

    def test_an_unestablished_refusal_with_nothing_resolved_leads_with_installing(self):
        """The likelier fix leads when no candidate resolved at all.

        A user with no OpenCode must not be handed, as its first remedy, a
        configuration variable that has nothing to point at.
        """
        code, payload, _ = self._run_layout({})
        self.assertEqual(code, 2)
        error = payload["error"]
        self.assertIn("Install OpenCode 2.x", error)
        self.assertLess(
            error.index("Install OpenCode 2.x"), error.index("CRUX_OPENCODE_BIN"),
            "installing must be offered before a variable with nothing to point at",
        )
        self.assertIn("nothing an assertion could bind to", error)

    def test_the_deprecation_notice_survives_a_refusal(self):
        """The notice must reach the runs most likely to be misconfigured.

        Emitting it only on success hides it from every user still on the old
        spelling whose runner ALSO fails the preflight — which is exactly the
        population the rename is trying to move.
        """
        with tempfile.TemporaryDirectory() as tmp_bin, tempfile.TemporaryDirectory() as tmp:
            stub = _make_runner_stub(Path(tmp_bin), name="old-v1", reported=REPORTED_V1)
            repo = Path(tmp)
            result = self._run(repo, _env_without_runner(CRUX_OPENCODE2_BIN=str(stub)))
            self.assertEqual(result.returncode, 2, result.stdout)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["verdict"], "known-incompatible")
            self.assertIn("CRUX_OPENCODE2_BIN is deprecated", payload["notice"])
            self.assertFalse((repo / ".opencode").exists())

    def test_a_compatible_verdict_binds_nothing_because_nothing_was_asserted(self):
        """`bound` records what an assertion bound to, not what the probe found.

        With a silent `opencode` and a 2.x `opencode2`, the first resolved
        not-known-incompatible candidate is the silent one. Reporting it as `bound`
        on a compatible verdict would name the candidate that established nothing
        while the one that decided the run goes unnamed in that field.
        """
        with tempfile.TemporaryDirectory() as tmp_bin, tempfile.TemporaryDirectory() as tmp:
            _make_runner_stub(Path(tmp_bin), name="opencode", reported="welcome to the tui")
            _make_runner_stub(Path(tmp_bin), name="opencode2", reported=REPORTED_V2)
            repo = Path(tmp)
            result = self._run(repo, _env_without_runner(Path(tmp_bin)))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            runner = json.loads(result.stdout)["runner"]
            self.assertEqual(runner["verdict"], "compatible")
            self.assertFalse(runner["assumed_compatible"])
            self.assertIsNone(runner["bound"])

    def test_a_known_incompatible_refusal_names_every_candidate_and_its_report(self):
        code, payload, _ = self._run_layout(
            {"opencode": REPORTED_V1, "opencode2": "welcome to the tui"}
        )
        self.assertEqual(code, 2)
        error = payload["error"]
        # Every probed candidate, with what it reported — the RAW first line,
        # including one that did not parse. A user deciding whether to assert
        # has to be able to read `opencode v1.4.2` rather than an empty field.
        self.assertIn(f"opencode ({REPORTED_V1})", error)
        self.assertIn("opencode2 (welcome to the tui)", error)
        # And the configuration that narrows the candidate set.
        self.assertIn("CRUX_OPENCODE_BIN", error)
        self.assertEqual(
            [c["reported"] for c in payload["candidates"]], [REPORTED_V1, "welcome to the tui"]
        )

    def test_a_refusal_distinguishes_absent_from_silent_from_reporting(self):
        """Three different things a candidate can be, said three different ways."""
        code, payload, _ = self._run_layout({"opencode": ""})
        self.assertEqual(code, 2)
        error = payload["error"]
        self.assertIn("opencode (no readable version)", error)   # resolved, said nothing
        self.assertIn("opencode2 (not on PATH)", error)          # never resolved

    def test_positive_control_a_two_x_named_opencode_writes_with_no_configuration(self):
        """The case the old name-matching implementation fails.

        A correctly upgraded 2.0.3 machine has `opencode` and no `opencode2`.
        Nothing is configured — no override env, no flag. It must write. An
        implementation keyed on the executable name refuses here, which is the
        defect this decision exists to remove.
        """
        code, payload, wrote = self._run_layout({"opencode": REPORTED_V2})
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["runner"]["verdict"], "compatible")
        self.assertFalse(payload["runner"]["assumed_compatible"])
        self.assertTrue(wrote)

    def test_positive_control_a_compatible_probe_writes_the_roster(self):
        """Without this, every refusal above would pass on a wholly broken installer."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            result = self._run(repo, _env_with_runner())
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(
                {p.name for p in (repo / ".opencode" / "agents").iterdir()}, EXPECTED_FILES
            )
            self.assertFalse(json.loads(result.stdout)["runner"]["assumed_compatible"])


class MigrationFailureLaneTests(unittest.TestCase):
    """The migration's failure lanes, and the fail-closed scan.

    Review of the first cut found three defects this class pins. Each is a
    lane where the installer had already changed the filesystem, or had failed
    to read it at all, and said neither.

    A later review found a fourth: the no-clobber gate ran AFTER the move, so
    its exit-1 refusal left the stale legacy bytes promoted into
    `.opencode/agents/` and the rest of the roster unwritten. Every decision
    the migration depends on is now taken before the first `os.replace`, so
    these tests assert the filesystem is untouched rather than asserting the
    payload explains what it already did.
    """

    def _run(
        self, repo_root: Path, *args: str, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(INSTALLER), "--repo-root", str(repo_root), *args],
            check=False, capture_output=True, text=True, env=env or _env_with_runner(),
        )

    @staticmethod
    def _generated() -> dict[str, str]:
        return agents.generate(SOURCE_DIR)

    def test_the_no_clobber_refusal_moves_nothing_out_of_the_legacy_dir(self):
        """A refusal must leave the tree exactly as it found it.

        The no-clobber gate compares the DESTINATION against the projection, so
        running it after the migration read a directory the same run had just
        filled: the stale legacy bytes were promoted into `.opencode/agents/`
        — the directory OpenCode resolves — the other nine agents were never
        written, and the run exited 1. The gate now decides over the state the
        move WOULD produce, before the move.
        """
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            legacy = repo / ".opencode" / "agent"
            legacy.mkdir(parents=True)
            # Locally EDITED, so the no-clobber gate has something to refuse.
            (legacy / "architect.md").write_text("locally edited\n", encoding="utf-8")

            result = self._run(repo, "--migrate-legacy-agent-dir")
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            payload = json.loads(result.stdout)

            # (a) the gate fired for the right reason, and named the right file.
            #     Without this the assertions below pass on an installer that
            #     refused for any unrelated reason, or before reading anything.
            self.assertIn("differ", payload["error"])
            self.assertEqual(payload["changed"], ["architect.md"])

            # (b) nothing moved: the legacy file is still where it started,
            #     with its bytes intact.
            self.assertEqual(
                (legacy / "architect.md").read_text(encoding="utf-8"), "locally edited\n"
            )

            # (c) the plural directory OpenCode resolves was never created, so
            #     the stale copy is not the one that would be served.
            self.assertFalse((repo / ".opencode" / "agents").exists())

            # (d) nothing moved, so the payload has nothing to report as moved.
            self.assertNotIn("migrated", payload)

    def test_positive_control_the_same_fixture_with_projection_bytes_completes(self):
        """Without this, the refusal above passes on an installer that never migrates.

        Same fixture, same flag, one byte-level difference: the legacy file
        carries the projection's own bytes instead of a local edit. The run
        must move it and write the whole roster — which is what proves the
        refusal above is the gate firing, not the migration being broken.
        """
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            legacy = repo / ".opencode" / "agent"
            legacy.mkdir(parents=True)
            (legacy / "architect.md").write_text(
                self._generated()["architect.md"], encoding="utf-8"
            )

            result = self._run(repo, "--migrate-legacy-agent-dir")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["migrated"], ["architect.md"])
            self.assertFalse((legacy / "architect.md").exists())
            self.assertEqual(
                {p.name for p in (repo / ".opencode" / "agents").iterdir()}, EXPECTED_FILES
            )

    def test_a_legacy_symlink_inside_the_repo_is_refused_before_it_is_relocated(self):
        """Containment only refuses a link ESCAPING the repo; this one does not.

        Left unrefused, the move relocates the link into `.opencode/agents/`,
        where `diff()` then refuses to read through it — after the link is
        already sitting in the directory OpenCode resolves.
        """
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            inside = repo / "elsewhere.md"
            inside.write_text("in-repo target\n", encoding="utf-8")
            legacy = repo / ".opencode" / "agent"
            legacy.mkdir(parents=True)
            (legacy / "architect.md").symlink_to(inside)

            result = self._run(repo, "--migrate-legacy-agent-dir")
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            error = json.loads(result.stdout)["error"]
            self.assertIn("symlink", error)
            self.assertIn("architect.md", error)
            self.assertTrue((legacy / "architect.md").is_symlink())
            self.assertFalse((repo / ".opencode" / "agents").exists())
            self.assertEqual(inside.read_text(encoding="utf-8"), "in-repo target\n")
            self.assertNotIn("Traceback", result.stderr)

    def test_a_non_directory_at_the_destination_is_exit_two_json_not_a_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            legacy = repo / ".opencode" / "agent"
            legacy.mkdir(parents=True)
            # The projection's own bytes, so the no-clobber gate passes and the
            # run reaches the `mkdir` this test is about. Stale bytes here would
            # exit 1 at the gate and never touch the destination at all.
            (legacy / "architect.md").write_text(
                self._generated()["architect.md"], encoding="utf-8"
            )
            (repo / ".opencode" / "agents").write_text("not a directory\n", encoding="utf-8")

            result = self._run(repo, "--migrate-legacy-agent-dir")
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            # Exit-1 stdout is the JSON report lane; a capability fault must
            # not land there, and must never be an empty stdout + traceback.
            self.assertIn("refusing to migrate", json.loads(result.stdout)["error"])
            self.assertNotIn("Traceback", result.stderr)
            # The destination is untouched — the mkdir refused, it did not
            # replace the file.
            self.assertEqual(
                (repo / ".opencode" / "agents").read_text(encoding="utf-8"), "not a directory\n"
            )

    def test_a_roster_named_directory_at_the_destination_is_exit_two_json_not_a_traceback(self):
        """`diff()` reads every managed leaf it finds; a directory is not a symlink.

        Its own guard passes this shape through to a raw `IsADirectoryError`,
        which would breach the exit-2-with-JSON contract.
        """
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            dest = repo / ".opencode" / "agents"
            dest.mkdir(parents=True)
            (dest / "architect.md").mkdir()

            result = self._run(repo)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn("cannot read", json.loads(result.stdout)["error"])
            self.assertNotIn("Traceback", result.stderr)

    def test_an_unreadable_legacy_dir_fails_closed_rather_than_installing_beside_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            legacy = repo / ".opencode" / "agent"
            legacy.mkdir(parents=True)
            (legacy / "architect.md").write_text("shadow\n", encoding="utf-8")
            os.chmod(legacy, 0o111)
            try:
                if os.access(legacy, os.R_OK):  # pragma: no cover - running as root
                    self.skipTest("cannot make a directory unreadable as this user")
                result = self._run(repo)
            finally:
                # Restore INSIDE the block: TemporaryDirectory cleanup cannot
                # list a 0o111 directory, and an addCleanup restore would run
                # after that cleanup had already removed the path.
                os.chmod(legacy, 0o755)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn("cannot list the legacy", json.loads(result.stdout)["error"])
            # The shadow copy the guard exists to prevent was never created.
            self.assertFalse((repo / ".opencode" / "agents").exists())

    def test_positive_control_a_readable_legacy_dir_still_refuses_normally(self):
        """Without this, the refusal above would pass on an installer that always failed."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            legacy = repo / ".opencode" / "agent"
            legacy.mkdir(parents=True)
            (legacy / "architect.md").write_text("shadow\n", encoding="utf-8")

            result = self._run(repo)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)["legacy_files"], ["architect.md"])


if __name__ == "__main__":
    unittest.main()
