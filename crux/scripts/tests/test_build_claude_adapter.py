"""End-to-end tests for build-claude-adapter.py — the shipped adapter CLI.

WHY THIS FILE EXISTS. A second independent review found three defects in the
adapter work, and all three lived in this entry point, which shipped in
`crux/plugin.json` with no test file at all. The component tests exercised
`claude_adapter.audit()` directly and so never touched the wiring:

  1. `generate()` was a bare `write_text`. On the path the shipped reference doc
     documents — check compat, see unsupported, generate — it silently destroyed
     the user's un-migrated `CLAUDE.md`. In the default mode the ONLY thing making
     an otherwise-current host unsupported is a legacy `CLAUDE.md`, so the advice
     aimed the writer at exactly the file whose content had not been merged.
  2. It overwrote the declared denylist fixture, which two ADR clauses forbid.
  3. It wrote THROUGH a symlink, outside the repository root.

And `removable` could never fire end to end, because the verdict computed
`supported` with the adapter on the chain, so an adapter masked its own
removability.

Every test here drives the CLI as a subprocess, because that is the surface a
user touches and the surface the component tests could not see.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CLI = REPO_ROOT / "crux" / "scripts" / "build-claude-adapter.py"


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


#: A Claude Code version at or above the 2.1.277 floor for AGENTS.md support.
SUPPORTED_CLAUDE_VERSION = "2.1.280 (Claude Code)"


class CliFixture(unittest.TestCase):
    """Drive the CLI on a SUPPORTED host that this fixture defines.

    `build-claude-adapter.py` asks `check-claude-compat.evaluate` whether the
    host reads AGENTS.md, and that reads the `claude` on PATH, the user's
    `~/.claude/settings.json` and the `CLAUDE_CODE_USE_*` variables. Left to the
    real machine, a CI runner with no `claude` reported `host_supported: False`
    and two tests here failed. The CLI therefore runs with a stub `claude`
    reporting `SUPPORTED_CLAUDE_VERSION`, a scratch HOME, and those variables
    removed.
    """

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        _write(self.root / "AGENTS.md", "# AGENTS.md\n\nroot instructions\n")
        _write(self.root / "bionic" / "AGENTS.md", "# tree\n\ntree schema\n")
        host = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, host, ignore_errors=True)
        stub = _write(host / "bin" / "claude",
                      f"#!/bin/sh\necho '{SUPPORTED_CLAUDE_VERSION}'\n")
        stub.chmod(0o755)
        (host / "home").mkdir()
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith("CLAUDE_CODE_USE_")}
        self.env["PATH"] = f"{host / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}"
        self.env["HOME"] = str(host / "home")

    def run_cli(self, *args):
        r = subprocess.run(
            [sys.executable, str(CLI), "--repo-root", str(self.root), *args],
            capture_output=True, text=True, env=self.env)
        payload = json.loads(r.stdout) if r.stdout.strip() else {}
        return r.returncode, payload, r.stderr


class DestructiveWriteRefusalTests(CliFixture):
    """The defect: generate() destroyed an un-migrated instruction file."""

    def test_an_unmigrated_legacy_claude_md_is_never_overwritten(self):
        precious = "# CLAUDE.md\n\ninstructions that were never merged\n"
        _write(self.root / "CLAUDE.md", precious)
        code, payload, _ = self.run_cli("--generate", "--force")
        self.assertEqual((self.root / "CLAUDE.md").read_text(encoding="utf-8"),
                         precious, "the un-migrated file was destroyed")
        self.assertEqual(code, 1, "a refusal is a finding, not a clean run")
        self.assertTrue(any("NOT a generated adapter" in r["reason"]
                            for r in payload["refused"]))

    def test_the_refusal_names_the_migration_as_the_remedy(self):
        _write(self.root / "CLAUDE.md", "# CLAUDE.md\n\nlegacy\n")
        _, payload, _ = self.run_cli("--generate", "--force")
        self.assertTrue(any("audit-docs --migrate" in r["reason"]
                            for r in payload["refused"]))

    def test_one_refusal_does_not_block_the_other_scopes(self):
        _write(self.root / "CLAUDE.md", "# CLAUDE.md\n\nlegacy\n")
        _, payload, _ = self.run_cli("--generate", "--force")
        self.assertEqual(payload["generated"], ["bionic/CLAUDE.md"])
        self.assertEqual([r["scope"] for r in payload["refused"]], ["."])

    def test_a_previously_generated_adapter_is_refreshed(self):
        """Positive control: the refusal does not block the legitimate case."""
        code, payload, _ = self.run_cli("--generate", "--force")
        self.assertEqual(code, 0, payload)
        self.assertEqual(sorted(payload["generated"]),
                         ["CLAUDE.md", "bionic/CLAUDE.md"])
        _write(self.root / "AGENTS.md", "# AGENTS.md\n\nedited\n")
        code, payload, _ = self.run_cli("--generate", "--force")
        self.assertEqual(code, 0, payload)
        self.assertIn("edited", (self.root / "CLAUDE.md").read_text(encoding="utf-8"))


class UnsupportedHostGenerateTests(CliFixture):
    """`--generate` proceeds on a host that a root `.claude/CLAUDE.md` stops from
    reading AGENTS.md. A checkout with no root AGENTS.md is unsupported too, but an
    adapter cannot repair that, so `--generate` refuses there and names the missing
    root AGENTS.md rather than claiming the host already reads it."""

    def test_a_root_dot_claude_file_makes_the_host_unsupported_and_generate_proceeds(self):
        _write(self.root / ".claude" / "CLAUDE.md", "# legacy\n")
        code, payload, _ = self.run_cli("--generate")
        self.assertFalse(payload["host_supported"], payload)
        self.assertEqual(payload["refused"], [], payload)
        self.assertEqual(sorted(payload["generated"]),
                         ["CLAUDE.md", "bionic/CLAUDE.md"])
        self.assertEqual(code, 0, payload)

    def test_a_supported_host_still_refuses_without_force(self):
        """Positive control: the refusal string the tests above avoid still fires."""
        code, payload, _ = self.run_cli("--generate")
        self.assertTrue(payload["host_supported"])
        self.assertIn("already reads AGENTS.md", payload["refused"])
        self.assertEqual(code, 1)

    def test_no_canonical_file_is_reported_unsupported_and_writes_no_adapter(self):
        (self.root / "AGENTS.md").unlink()
        (self.root / "bionic" / "AGENTS.md").unlink()
        code, payload, _ = self.run_cli("--generate")
        self.assertFalse(payload["host_supported"], payload)
        self.assertEqual(payload["generated"], [])
        self.assertIn("no root AGENTS.md", payload["refused"])
        self.assertNotIn("already reads AGENTS.md", payload["refused"])
        self.assertFalse((self.root / "CLAUDE.md").exists())
        self.assertFalse((self.root / "bionic" / "CLAUDE.md").exists())
        self.assertEqual(code, 1)

    def test_a_missing_root_agents_md_alone_still_refuses_without_force(self):
        """A missing canonical file is not a host cause, and an adapter cannot
        create it, so `--generate` refuses rather than writing an adapter for a
        nested scope. The refusal names the missing root AGENTS.md."""
        (self.root / "AGENTS.md").unlink()
        code, payload, _ = self.run_cli("--generate")
        self.assertFalse(payload["host_supported"], payload)
        self.assertIn("no root AGENTS.md", payload["refused"])
        self.assertNotIn("already reads AGENTS.md", payload["refused"])
        self.assertEqual(payload["generated"], [])
        self.assertFalse((self.root / "bionic" / "CLAUDE.md").exists())
        self.assertEqual(code, 1)


class DenylistRefusalTests(CliFixture):
    def test_a_denylisted_scope_is_never_written(self):
        fixture_dir = self.root / "fixtures" / "trips"
        _write(fixture_dir / "AGENTS.md", "# roster host\n")
        precious = "# CLAUDE.md\n\nPRECIOUS FIXTURE BYTES\n"
        _write(fixture_dir / "CLAUDE.md", precious)
        _write(self.root / ".bionic.yml",
               'config_version: "1"\ndocs_dir: bionic\n'
               "instruction_migration_denylist:\n"
               "  - fixtures/trips/CLAUDE.md\n")
        code, payload, _ = self.run_cli("--generate", "--force")
        self.assertEqual((fixture_dir / "CLAUDE.md").read_text(encoding="utf-8"),
                         precious, "the denylisted fixture was overwritten")
        self.assertTrue(any("denylist" in r["reason"] for r in payload["refused"]))
        self.assertEqual(code, 1)

    def test_a_scope_absent_from_the_denylist_is_still_written(self):
        """Positive control for the refusal above."""
        _write(self.root / ".bionic.yml",
               'config_version: "1"\ndocs_dir: bionic\n'
               "instruction_migration_denylist:\n  - something/else.md\n")
        code, payload, _ = self.run_cli("--generate", "--force")
        self.assertEqual(code, 0, payload)
        self.assertIn("CLAUDE.md", payload["generated"])


class SymlinkRefusalTests(CliFixture):
    def test_a_symlinked_parent_cannot_create_an_external_adapter(self):
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "add", "AGENTS.md",
                        "bionic/AGENTS.md"], check=True)
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        _write(outside / "AGENTS.md", "external instructions\n")
        shutil.rmtree(self.root / "bionic")
        os.symlink(outside, self.root / "bionic")

        code, payload, _ = self.run_cli("--generate", "--force")

        self.assertFalse((outside / "CLAUDE.md").exists(),
                         "a missing destination escaped through its parent")
        self.assertEqual(code, 1, payload)
        self.assertTrue(any("outside" in r["reason"]
                            for r in payload["refused"]))
        self.assertIn("CLAUDE.md", payload["generated"])

    def test_a_symlinked_canonical_file_is_not_exported(self):
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        source = _write(outside / "source.md", "external instructions\n")
        (self.root / "AGENTS.md").unlink()
        os.symlink(source, self.root / "AGENTS.md")

        code, payload, _ = self.run_cli("--generate", "--force")

        self.assertFalse((self.root / "CLAUDE.md").exists())
        self.assertEqual(code, 1, payload)
        self.assertTrue(any("symlink" in r["reason"]
                            for r in payload["refused"]))

    def test_the_writer_never_writes_through_a_symlink(self):
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        victim = _write(outside / "victim.md", "do not touch\n")
        os.symlink(victim, self.root / "CLAUDE.md")
        code, payload, _ = self.run_cli("--generate", "--force")
        self.assertEqual(victim.read_text(encoding="utf-8"), "do not touch\n",
                         "the writer escaped the repository root")
        self.assertTrue(any("symlink" in r["reason"] for r in payload["refused"]))
        self.assertTrue((self.root / "CLAUDE.md").is_symlink())
        self.assertEqual(code, 1)

    def test_an_ordinary_target_is_still_written(self):
        """Positive control: the symlink refusal does not refuse everything."""
        code, payload, _ = self.run_cli("--generate", "--force")
        self.assertEqual(code, 0, payload)
        self.assertFalse((self.root / "CLAUDE.md").is_symlink())


class SupportedHostRefusalTests(CliFixture):
    def test_the_fixture_host_is_supported(self):
        """Precondition for this class and the next: the stub host is supported."""
        _, payload, _ = self.run_cli()
        self.assertIs(payload["host_supported"], True, payload)

    def test_generate_refuses_on_a_supported_host_without_force(self):
        code, payload, _ = self.run_cli("--generate")
        self.assertEqual(code, 1)
        self.assertEqual(payload["generated"], [])
        self.assertIn("already reads AGENTS.md", payload["refused"])
        self.assertFalse((self.root / "CLAUDE.md").exists())

    def test_force_overrides_that_refusal(self):
        """Positive control."""
        code, _, _ = self.run_cli("--generate", "--force")
        self.assertEqual(code, 0)
        self.assertTrue((self.root / "CLAUDE.md").is_file())


class RemovableReportTests(CliFixture):
    """The defect: an adapter's own presence masked its removability."""

    def test_a_complete_adapter_set_on_a_supported_host_reports_removable(self):
        self.run_cli("--generate", "--force")
        code, payload, _ = self.run_cli()
        statuses = {a["status"] for a in payload["adapters"]}
        self.assertEqual(statuses, {"removable"},
                         f"adapters masked their own removability: {payload}")
        self.assertTrue(any("REMOVABLE" in f for f in payload["findings"]))
        self.assertEqual(code, 1)

    def test_a_stale_adapter_outranks_removable(self):
        self.run_cli("--generate", "--force")
        _write(self.root / "AGENTS.md", "# AGENTS.md\n\nmoved on\n")
        _, payload, _ = self.run_cli()
        by_scope = {a["scope"]: a["status"] for a in payload["adapters"]}
        self.assertEqual(by_scope["."], "stale")

    def test_a_root_only_adapter_reports_incomplete(self):
        self.run_cli("--generate", "--force")
        (self.root / "bionic" / "CLAUDE.md").unlink()
        _, payload, _ = self.run_cli()
        self.assertEqual(payload["incomplete_scopes"], ["bionic"])
        self.assertTrue(any("INCOMPLETE" in f for f in payload["findings"]))

    def test_no_adapters_is_a_clean_audit(self):
        """Positive control: the audit is not simply always red."""
        code, payload, _ = self.run_cli()
        self.assertEqual(code, 0)
        self.assertEqual(payload["adapters"], [])
        self.assertEqual(payload["findings"], [])


class AuditIsReadOnlyTests(CliFixture):
    def test_the_audit_lane_writes_nothing(self):
        self.run_cli("--generate", "--force")
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.run_cli()
        after = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_no_temporary_file_survives_a_generate(self):
        self.run_cli("--generate", "--force")
        self.assertEqual([p for p in self.root.rglob("*.tmp")], [])

    def test_the_cli_declares_no_third_party_dependencies(self):
        head = CLI.read_text(encoding="utf-8")
        self.assertIn("# /// script", head)
        self.assertIn("dependencies = []", head)


if __name__ == "__main__":
    unittest.main()
