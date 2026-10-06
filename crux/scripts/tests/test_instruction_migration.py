"""Tests for instruction_migration.py — the AGENTS.md discovery and migration contract.

Written before the implementation, per PB-0115 prompt 2.

The properties under test are the ones five council rounds rejected earlier designs
for. Each names the failure it exists to prevent:

- discovery's MUTATION set is the tracked files of ONE checkout, so a vendored
  dependency cache and a linked worktree are outside it by construction
- discovery's SUPPRESSION set is wider than the mutation set and includes untracked
  files, because a file crux must not touch can still silence the canonical one.
  No `governs` entry projects this clause; it is the safety property most likely to
  be missed by an implementer reading only the rule table
- deduplication matches on (heading path, bytes), never bytes alone, so a block is
  never silently REPARENTED while the accounting still balances
- the accounting balances as retained + synthesized + resolved, and refuses otherwise
- a `#` inside a fenced code block opens no block
- staging is per file and a rerun converges
"""
from __future__ import annotations

import errno
import hashlib
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE = REPO_ROOT / "crux" / "scripts" / "instruction_migration.py"
ENTRY = REPO_ROOT / "crux" / "scripts" / "migrate-instructions.py"

_spec = importlib.util.spec_from_file_location("instruction_migration", MODULE)
im = importlib.util.module_from_spec(_spec)
sys.modules["instruction_migration"] = im
_spec.loader.exec_module(im)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class TempRepo(unittest.TestCase):
    """A repo root whose tracked set is injected, so most tests need no git."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def discover(self, tracked=None, denylist=(), cwd=None):
        """Discovery with an injected tracked set (the git call is the one seam)."""
        if tracked is None:
            tracked = [
                p.relative_to(self.root).as_posix()
                for p in self.root.rglob("*")
                if p.is_file() and ".git" not in p.parts
            ]
        return im.discover(
            self.root,
            tracked_files=tracked,
            denylist=list(denylist),
            working_dir=cwd or self.root,
        )


# ------------------------------------------------------------------- discovery


class DiscoveryTests(TempRepo):
    def test_matching_is_case_insensitive_over_the_three_names(self):
        for name in ("AGENTS.md", "CLAUDE.md", "Claude.MD", "claude.local.md"):
            self.assertTrue(im.is_instruction_name(name), name)
        for name in ("AGENT.md", "CLAUDE.markdown", "README.md", "CLAUDE.md.tmpl"):
            self.assertFalse(im.is_instruction_name(name), name)

    def test_an_untracked_file_is_never_in_the_mutation_set(self):
        _write(self.root / "CLAUDE.md", "tracked\n")
        _write(self.root / "vendor" / "CLAUDE.md", "untracked\n")
        d = self.discover(tracked=["CLAUDE.md"])
        self.assertEqual([p.as_posix() for p in d.managed_paths()], ["CLAUDE.md"])

    def test_a_vendored_cache_and_a_worktree_are_outside_the_tracked_set(self):
        _write(self.root / "CLAUDE.md", "real\n")
        _write(self.root / ".cache" / "dep" / "CLAUDE.md", "vendored\n")
        _write(self.root / ".cache" / "dep" / "AGENTS.md", "vendored\n")
        _write(self.root / "wt" / "copy" / "CLAUDE.md", "worktree\n")
        d = self.discover(tracked=["CLAUDE.md"])
        self.assertEqual([p.as_posix() for p in d.managed_paths()], ["CLAUDE.md"])

    def test_a_template_is_excluded_by_path_and_keeps_its_bytes(self):
        t = _write(self.root / "crux" / "templates" / "CLAUDE.md", "tmpl\n")
        before = t.read_bytes()
        d = self.discover(tracked=["crux/templates/CLAUDE.md"])
        self.assertEqual(d.managed_paths(), [])
        self.assertEqual(d.disposition("crux/templates/CLAUDE.md"), "excluded:template")
        self.assertEqual(t.read_bytes(), before)

    def test_a_denylisted_fixture_is_excluded_and_named(self):
        rel = "crux/scripts/tests/fixtures/trips/CLAUDE.md"
        _write(self.root / rel, "fixture\n")
        d = self.discover(tracked=[rel], denylist=[rel])
        self.assertEqual(d.managed_paths(), [])
        self.assertEqual(d.disposition(rel), "excluded:denylist")

    def test_an_absent_denylist_is_empty_rather_than_an_error(self):
        _write(self.root / "CLAUDE.md", "x\n")
        d = im.discover(self.root, tracked_files=["CLAUDE.md"], denylist=None,
                        working_dir=self.root)
        self.assertEqual([p.as_posix() for p in d.managed_paths()], ["CLAUDE.md"])

    def test_claude_local_md_is_excluded_by_name_even_when_tracked(self):
        _write(self.root / "CLAUDE.local.md", "private\n")
        d = self.discover(tracked=["CLAUDE.local.md"])
        self.assertEqual(d.managed_paths(), [])
        self.assertEqual(d.disposition("CLAUDE.local.md"), "excluded:local-override")

    def test_a_dot_claude_claude_md_is_excluded_by_path_even_when_tracked(self):
        """Renaming it would silence it: no host is known to load .claude/AGENTS.md."""
        _write(self.root / ".claude" / "CLAUDE.md", "dot claude\n")
        d = self.discover(tracked=[".claude/CLAUDE.md"])
        self.assertEqual(d.managed_paths(), [])
        self.assertEqual(d.disposition(".claude/CLAUDE.md"), "excluded:dot-claude")

    def test_a_symlink_is_never_followed_and_keeps_its_bytes(self):
        _write(self.root / "real.md", "target\n")
        link = self.root / "CLAUDE.md"
        os.symlink(self.root / "real.md", link)
        d = self.discover(tracked=["CLAUDE.md", "real.md"])
        self.assertEqual(d.managed_paths(), [])
        self.assertEqual(d.disposition("CLAUDE.md"), "excluded:symlink")
        self.assertTrue(link.is_symlink())


class SuppressionScanTests(TempRepo):
    """Clause 5. No governs entry projects this; it is the clause most likely missed.

    The mutation set is tracked files. The suppression set is wider and includes
    untracked ones, because a file crux must not touch can still silence the
    canonical file.
    """

    def test_an_untracked_suppressor_is_reported_though_it_is_not_mutable(self):
        _write(self.root / "AGENTS.md", "canonical\n")
        _write(self.root / "CLAUDE.local.md", "private\n")
        d = self.discover(tracked=["AGENTS.md"])
        self.assertIn("CLAUDE.local.md", [s.path.as_posix() for s in d.suppressors])
        self.assertEqual(d.disposition("CLAUDE.local.md"), "excluded:untracked")
        self.assertNotIn("CLAUDE.local.md",
                         [p.as_posix() for p in d.managed_paths()])
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "add", "AGENTS.md"], cwd=self.root, check=True)
        self.assertEqual(im.build_plan(d).actions, [],
                         "a canonical-only scope needs no action")

    def test_the_scan_covers_the_whole_checkout_not_one_ancestor_chain(self):
        """A private bionic/CLAUDE.local.md silences the tree for anyone inside it."""
        _write(self.root / "AGENTS.md", "canonical\n")
        _write(self.root / "bionic" / "CLAUDE.local.md", "private\n")
        d = self.discover(tracked=["AGENTS.md"], cwd=self.root)
        found = [s.path.as_posix() for s in d.suppressors]
        self.assertIn("bionic/CLAUDE.local.md", found)

    def test_a_tracked_legacy_claude_md_is_also_a_suppressor_until_migrated(self):
        _write(self.root / "AGENTS.md", "canonical\n")
        _write(self.root / "CLAUDE.md", "legacy\n")
        d = self.discover(tracked=["AGENTS.md", "CLAUDE.md"])
        self.assertIn("CLAUDE.md", [s.path.as_posix() for s in d.suppressors])

    def test_on_chain_and_off_chain_suppressors_are_distinguished(self):
        _write(self.root / "AGENTS.md", "canonical\n")
        _write(self.root / "bionic" / "CLAUDE.local.md", "off chain from root\n")
        _write(self.root / "CLAUDE.local.md", "on chain\n")
        d = self.discover(tracked=["AGENTS.md"], cwd=self.root)
        by_path = {s.path.as_posix(): s for s in d.suppressors}
        self.assertTrue(by_path["CLAUDE.local.md"].on_chain)
        self.assertFalse(by_path["bionic/CLAUDE.local.md"].on_chain)

    def test_a_deeper_working_directory_puts_the_tree_file_on_the_chain(self):
        _write(self.root / "AGENTS.md", "canonical\n")
        _write(self.root / "bionic" / "CLAUDE.local.md", "private\n")
        d = self.discover(tracked=["AGENTS.md"], cwd=self.root / "bionic")
        by_path = {s.path.as_posix(): s for s in d.suppressors}
        self.assertTrue(by_path["bionic/CLAUDE.local.md"].on_chain)

    def test_the_scan_skips_a_nested_checkout_below_the_root_but_not_the_root(self):
        _write(self.root / "CLAUDE.local.md", "mine\n")
        nested = self.root / "wt" / "copy"
        _write(nested / "CLAUDE.local.md", "theirs\n")
        (nested / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
        (self.root / ".git").mkdir(exist_ok=True)
        d = self.discover(tracked=[])
        found = [s.path.as_posix() for s in d.suppressors]
        self.assertIn("CLAUDE.local.md", found)
        self.assertNotIn("wt/copy/CLAUDE.local.md", found)


class SuppressorRemedyTests(TempRepo):
    """Clause 5 reports a suppressor WITH ITS REMEDY. A classification is not one."""

    def test_every_suppressor_carries_an_actionable_remedy(self):
        _write(self.root / "CLAUDE.md", "legacy\n")
        _write(self.root / "CLAUDE.local.md", "private\n")
        _write(self.root / ".claude" / "CLAUDE.md", "dot\n")
        d = self.discover(tracked=["CLAUDE.md"])
        self.assertTrue(d.suppressors, "positive control: suppressors were found")
        for s in d.suppressors:
            self.assertTrue(s.remedy.strip(), f"{s.path} carries no remedy")

    def test_a_private_override_is_never_told_to_be_deleted_by_crux(self):
        _write(self.root / "CLAUDE.local.md", "private\n")
        d = self.discover(tracked=[])
        s = next(x for x in d.suppressors if x.path.name == "CLAUDE.local.md")
        self.assertIn("yours", s.remedy)

    def test_a_denylisted_path_is_not_described_as_awaiting_migration(self):
        """It will never be migrated, so `suppresses until migrated` is false."""
        rel = "fixtures/trips/CLAUDE.md"
        _write(self.root / rel, "fixture\n")
        d = self.discover(tracked=[rel], denylist=[rel])
        s = next(x for x in d.suppressors if x.path.as_posix() == rel)
        self.assertIn("denylist", s.reason)
        self.assertNotIn("until migrated", s.reason)

    def test_a_genuinely_legacy_file_still_names_the_migrate_command(self):
        """Positive control for the branch above."""
        _write(self.root / "CLAUDE.md", "legacy\n")
        d = self.discover(tracked=["CLAUDE.md"])
        s = next(x for x in d.suppressors if x.path.as_posix() == "CLAUDE.md")
        self.assertIn("until migrated", s.reason)
        self.assertIn("--migrate", s.remedy)


class DotClaudeChainTests(TempRepo):
    """Clause 5: `.claude/CLAUDE.md` belongs to the scope of the directory that
    holds `.claude`, so its chain membership follows that directory."""

    def _scan(self, cwd=None):
        return {s.path.as_posix(): s
                for s in im._scan_suppressors(self.root, cwd or self.root)}

    def test_a_root_dot_claude_file_is_on_the_chain_of_the_root(self):
        _write(self.root / ".claude" / "CLAUDE.md", "dot\n")
        s = self._scan()[".claude/CLAUDE.md"]
        self.assertTrue(s.on_chain)

    def test_the_dot_claude_reason_remedy_and_classification_are_unchanged(self):
        _write(self.root / ".claude" / "CLAUDE.md", "dot\n")
        s = self._scan()[".claude/CLAUDE.md"]
        self.assertEqual(s.reason, "dot-claude instruction file")
        self.assertIn("AGENTS.md of the same scope", s.remedy)
        self.assertEqual(im._classify(self.root, ".claude/CLAUDE.md"),
                         "excluded:dot-claude")

    def test_a_nested_dot_claude_file_follows_its_parent_directory(self):
        _write(self.root / "sub" / ".claude" / "CLAUDE.md", "dot\n")
        self.assertFalse(self._scan(self.root)["sub/.claude/CLAUDE.md"].on_chain)
        self.assertTrue(
            self._scan(self.root / "sub")["sub/.claude/CLAUDE.md"].on_chain)

    def test_a_deeper_dot_claude_path_keeps_its_own_directory_scope(self):
        """Only the exact `<dir>/.claude/CLAUDE.md` shape moves to its parent."""
        _write(self.root / ".claude" / "deep" / "CLAUDE.md", "dot\n")
        s = self._scan(self.root)[".claude/deep/CLAUDE.md"]
        self.assertFalse(s.on_chain)
        self.assertTrue(
            self._scan(self.root / ".claude" / "deep")[".claude/deep/CLAUDE.md"]
            .on_chain)

    def test_a_plain_claude_md_keeps_its_directory_scope(self):
        """Control: the root file was on the chain before and stays there."""
        _write(self.root / "CLAUDE.md", "legacy\n")
        _write(self.root / "sub" / "CLAUDE.md", "legacy\n")
        found = self._scan(self.root)
        self.assertTrue(found["CLAUDE.md"].on_chain)
        self.assertFalse(found["sub/CLAUDE.md"].on_chain)


class ContainmentTests(TempRepo):
    """Clause 2: no path resolves outside the root. Checked over the whole path."""

    def test_a_path_through_a_symlinked_directory_is_excluded(self):
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        _write(outside / "CLAUDE.md", "elsewhere\n")
        os.symlink(outside, self.root / "linked")
        d = self.discover(tracked=["linked/CLAUDE.md"])
        self.assertEqual(d.managed_paths(), [])
        self.assertEqual(d.disposition("linked/CLAUDE.md"), "excluded:escapes-root")
        self.assertEqual((outside / "CLAUDE.md").read_text(encoding="utf-8"),
                         "elsewhere\n")

    def test_an_ordinary_nested_path_is_still_managed(self):
        """Positive control: containment does not exclude everything."""
        _write(self.root / "sub" / "CLAUDE.md", "mine\n")
        d = self.discover(tracked=["sub/CLAUDE.md"])
        self.assertEqual([p.as_posix() for p in d.managed_paths()], ["sub/CLAUDE.md"])


# ------------------------------------ ADR-0143: CLAUDE.md wins, no silent loss
#
# These cases drive the real entry point in a real git repository, so they run
# unchanged against the code before the fix, where each one failed for the
# defect it names (D1, D2, D6, D7, the shared errors list, and the two owner
# decisions). They assert on bytes on disk first, and on the report second.


def _folding_volume(directory: Path) -> bool:
    """Does this volume fold letter case? Probed with a throwaway entry."""
    probe = directory / "fold-probe"
    probe.write_bytes(b"")
    try:
        return os.path.exists(directory / "FOLD-PROBE")
    finally:
        probe.unlink()


class GitRepoCase(unittest.TestCase):
    """A scratch git repository driven through `migrate-instructions.py`."""

    def setUp(self):
        if shutil.which("git") is None:
            self.skipTest("git is not available on PATH")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name).resolve()
        self.root = self.base / "repo"
        self.root.mkdir()
        self.git("init", "-q")
        self.git("config", "user.email", "t@example.invalid")
        self.git("config", "user.name", "t")
        self.git("config", "commit.gpgsign", "false")

    def git(self, *args: str, check: bool = True, stdin: bytes | None = None) -> str:
        proc = subprocess.run(["git", "-C", str(self.root), *args], input=stdin,
                              capture_output=True)
        if check and proc.returncode != 0:
            raise AssertionError(f"git {args} failed: {proc.stderr!r}")
        return proc.stdout.decode("utf-8", "surrogateescape")

    def put(self, rel: str, data, track: bool = False) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
        if track:
            self.git("add", "--", rel)
        return path

    def commit(self):
        self.git("commit", "-q", "--allow-empty", "-m", "fixture")

    def cli(self, *args: str):
        proc = subprocess.run(
            [sys.executable, str(ENTRY), "--repo-root", str(self.root), *args],
            capture_output=True, text=True)
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError:
            payload = None
        return proc.returncode, payload, proc.stderr

    def files(self) -> dict[str, bytes]:
        """Every regular file under the root, minus git's store and the receipt."""
        out = {}
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d != ".git"]
            for name in filenames:
                path = Path(dirpath) / name
                if name == ".instruction-migration-receipt.json" or path.is_symlink():
                    continue
                try:
                    held = path.read_bytes()
                except PermissionError:
                    # A mode-0 fixture: its identity is compared instead.
                    held = os.lstat(path).st_ino
                out[path.relative_to(self.root).as_posix()] = held
        return out

    def holders(self, data: bytes) -> list[str]:
        return sorted(rel for rel, held in self.files().items() if held == data)

    def listing(self, rel: str = ".") -> list[str]:
        return sorted(os.listdir(self.root / rel))

    def folds(self) -> bool:
        return _folding_volume(self.root)


class ClaudeWinsDefectTests(GitRepoCase):
    """Each defect PB-0132 reproduced, and each owner decision, end to end."""

    CLAUDE = b"# Claude\n\nclaude rules\n"
    AGENTS = b"# Agents\n\nagents notes\n"

    def assert_set_aside(self, payload, scope: str, entry: str, data: bytes):
        """The loser's bytes sit under a reported name in its own directory."""
        rows = [r for r in payload["set_asides"]
                if r["scope"] == scope and r["entry"] == entry]
        self.assertEqual(len(rows), 1, payload["set_asides"])
        name = rows[0]["set_aside"]
        self.assertTrue(name.startswith(f"{entry}.crux-set-aside-"), name)
        self.assertFalse(name.lower().endswith(".md"), name)
        where = name if scope == "." else f"{scope}/{name}"
        self.assertEqual((self.root / where).read_bytes(), data)
        self.assertEqual(rows[0]["sha256"], hashlib.sha256(data).hexdigest())

    def test_d1_untracked_agents_md_at_the_root_is_set_aside_not_overwritten(self):
        self.put("CLAUDE.md", self.CLAUDE, track=True)
        self.commit()
        self.put("AGENTS.md", self.AGENTS)
        code, payload, err = self.cli("--migrate")
        # The untracked bytes survive first; this is what D1 destroyed.
        self.assertEqual(len(self.holders(self.AGENTS)), 1, (code, err))
        self.assertEqual((self.root / "AGENTS.md").read_bytes(), self.CLAUDE)
        self.assertEqual(code, 0, (payload, err))
        self.assert_set_aside(payload, ".", "AGENTS.md", self.AGENTS)

    def test_d1_untracked_agents_md_in_a_nested_scope_is_set_aside(self):
        self.put("sub/CLAUDE.md", self.CLAUDE, track=True)
        self.commit()
        self.put("sub/AGENTS.md", self.AGENTS)
        code, payload, err = self.cli("--migrate")
        self.assertEqual(len(self.holders(self.AGENTS)), 1, (code, err))
        self.assertEqual((self.root / "sub" / "AGENTS.md").read_bytes(), self.CLAUDE)
        self.assert_set_aside(payload, "sub", "AGENTS.md", self.AGENTS)

    def test_set_aside_name_is_twelve_hex_of_the_entry_sha256(self):
        """The owner-confirmed pattern: `<name>.crux-set-aside-<12 hex of SHA-256>`."""
        self.put("CLAUDE.md", self.CLAUDE, track=True)
        self.commit()
        self.put("AGENTS.md", self.AGENTS)
        code, payload, err = self.cli("--migrate")
        self.assertEqual(code, 0, (payload, err))
        expected = "AGENTS.md.crux-set-aside-" + hashlib.sha256(self.AGENTS).hexdigest()[:12]
        self.assertEqual([r["set_aside"] for r in payload["set_asides"]], [expected])
        self.assertEqual((self.root / expected).read_bytes(), self.AGENTS)
        # Positive control: the pure helper yields the same name, and a one-byte
        # change of the content changes it, so the hash is what the name carries.
        self.assertEqual(im.set_aside_name("AGENTS.md", self.AGENTS, set()), expected)
        self.assertNotEqual(im.set_aside_name("AGENTS.md", self.AGENTS + b"x", set()),
                            expected)

    def test_set_aside_name_takes_suffix_2_when_the_name_is_taken(self):
        self.put("CLAUDE.md", self.CLAUDE, track=True)
        self.commit()
        self.put("AGENTS.md", self.AGENTS)
        base = "AGENTS.md.crux-set-aside-" + hashlib.sha256(self.AGENTS).hexdigest()[:12]
        squatter = b"someone else's bytes\n"
        self.put(base, squatter)
        code, payload, err = self.cli("--migrate")
        self.assertEqual(code, 0, (payload, err))
        # The pre-existing entry is itself reported (as found); the loser is the
        # row that carries the loser's hash.
        moved = [r for r in payload["set_asides"]
                 if r["sha256"] == hashlib.sha256(self.AGENTS).hexdigest()]
        self.assertEqual([r["set_aside"] for r in moved], [base + "-2"])
        self.assertEqual((self.root / (base + "-2")).read_bytes(), self.AGENTS)
        self.assertEqual((self.root / base).read_bytes(), squatter)
        # Positive control: the name really collides in the helper, and a third
        # collision steps to -3.
        self.assertEqual(im.set_aside_name("AGENTS.md", self.AGENTS, {base.lower()}),
                         base + "-2")
        self.assertEqual(im.set_aside_name("AGENTS.md", self.AGENTS,
                                           {base.lower(), (base + "-2").lower()}),
                         base + "-3")

    def test_d2_a_tracked_variant_beside_claude_md_keeps_both_contents(self):
        self.put("CLAUDE.md", self.CLAUDE, track=True)
        self.put("agents.md", self.AGENTS, track=True)
        self.commit()
        code, payload, err = self.cli("--migrate")
        self.assertEqual(len(self.holders(self.AGENTS)), 1, (code, err))
        self.assertEqual(len(self.holders(self.CLAUDE)), 1, (code, err))
        self.assertIn("AGENTS.md", self.listing())
        self.assertEqual((self.root / "AGENTS.md").read_bytes(), self.CLAUDE)
        self.assert_set_aside(payload, ".", "agents.md", self.AGENTS)
        # The index holds the result at the exact path and no other family path.
        staged = self.git("ls-files").split()
        self.assertIn("AGENTS.md", staged)
        self.assertNotIn("agents.md", staged)
        self.assertNotIn("CLAUDE.md", staged)

    def test_d6_a_denylisted_agents_md_refuses_its_scope_untouched(self):
        self.put(".bionic.yml", "docs_dir: bionic\ninstruction_migration_denylist:\n"
                                "  - AGENTS.md\n", track=True)
        self.put("CLAUDE.md", self.CLAUDE, track=True)
        self.put("AGENTS.md", self.AGENTS, track=True)
        # A second scope that does migrate: the positive control that the run acts.
        self.put("sub/CLAUDE.md", b"nested\n", track=True)
        self.commit()
        code, payload, err = self.cli("--migrate")
        self.assertEqual((self.root / "AGENTS.md").read_bytes(), self.AGENTS)
        self.assertEqual((self.root / "CLAUDE.md").read_bytes(), self.CLAUDE)
        self.assertEqual((self.root / "sub" / "AGENTS.md").read_bytes(), b"nested\n")
        self.assertEqual(code, 1, (payload, err))
        refusals = [r for r in payload["refusals"] if r["scope"] == "."]
        self.assertEqual(len(refusals), 1, payload["refusals"])
        self.assertIn("denylist", refusals[0]["reason"])
        self.assertIn("remove the denylist entry or move the file yourself",
                      refusals[0]["next_step"])

    def test_d7_a_non_utf8_loser_migrates_without_a_traceback(self):
        latin = "# Agents\n\ncafé notes\n".encode("latin-1")
        self.put("CLAUDE.md", self.CLAUDE, track=True)
        self.put("AGENTS.md", latin, track=True)
        self.commit()
        code, payload, err = self.cli("--migrate")
        self.assertNotIn("Traceback", err)
        self.assertIsNotNone(payload, err)
        self.assertEqual(len(self.holders(latin)), 1, (code, err))
        self.assertEqual((self.root / "AGENTS.md").read_bytes(), self.CLAUDE)
        self.assert_set_aside(payload, ".", "AGENTS.md", latin)

    def test_d7_a_non_utf8_configuration_exits_2_with_a_message(self):
        self.put(".bionic.yml", "docs_dir: bionic\n# café\n".encode("latin-1"),
                 track=True)
        self.put("CLAUDE.md", self.CLAUDE, track=True)
        self.commit()
        code, payload, err = self.cli("--dry-run")
        self.assertEqual(code, 2, (payload, err))
        self.assertNotIn("Traceback", err)
        self.assertIn(".bionic.yml", err)

    def test_one_refused_scope_blocks_no_other_scope(self):
        """The shared errors list: two spellings in a/ must not stop b/."""
        self.put("a/CLAUDE.md", b"upper\n", track=True)
        if self.folds():
            # The volume holds one spelling; the index holds the second one.
            blob = self.git("hash-object", "-w", "--stdin", stdin=b"lower\n").strip()
            self.git("update-index", "--add", "--cacheinfo", f"100644,{blob},a/claude.md")
        else:
            self.put("a/claude.md", b"lower\n", track=True)
        self.put("b/CLAUDE.md", b"bee\n", track=True)
        self.commit()
        before = {n: (self.root / "a" / n).read_bytes() for n in self.listing("a")}
        code, payload, err = self.cli("--migrate")
        self.assertEqual((self.root / "b" / "AGENTS.md").read_bytes(), b"bee\n", (code, err))
        self.assertEqual({n: (self.root / "a" / n).read_bytes()
                          for n in self.listing("a")}, before)
        self.assertEqual(code, 1, (payload, err))
        self.assertEqual([r["scope"] for r in payload["refusals"]], ["a"])

    def test_owner_decision_1_a_variant_is_found_from_directory_entries(self):
        """An untracked `agents.md` beside CLAUDE.md is found without a path probe."""
        self.put("CLAUDE.md", self.CLAUDE, track=True)
        self.commit()
        self.put("agents.md", self.AGENTS)
        code, payload, err = self.cli("--migrate")
        self.assertEqual(len(self.holders(self.AGENTS)), 1, (code, err))
        self.assertEqual((self.root / "AGENTS.md").read_bytes(), self.CLAUDE)
        self.assert_set_aside(payload, ".", "agents.md", self.AGENTS)

    def test_owner_decision_2_claude_md_wins_where_both_are_tracked(self):
        self.put("CLAUDE.md", self.CLAUDE, track=True)
        self.put("AGENTS.md", self.AGENTS, track=True)
        self.commit()
        code, payload, err = self.cli("--migrate")
        self.assertEqual((self.root / "AGENTS.md").read_bytes(), self.CLAUDE, (code, err))
        self.assertNotIn("CLAUDE.md", self.listing())
        self.assertEqual(len(self.holders(self.AGENTS)), 1)
        self.assert_set_aside(payload, ".", "AGENTS.md", self.AGENTS)
        self.assertEqual(code, 0, (payload, err))

    def test_a_pointer_claude_md_inlines_the_agents_md_it_imports(self):
        self.put("CLAUDE.md", b"@AGENTS.md\n", track=True)
        self.put("AGENTS.md", self.AGENTS, track=True)
        self.commit()
        code, payload, err = self.cli("--migrate")
        self.assertEqual((self.root / "AGENTS.md").read_bytes(), self.AGENTS, (code, err))
        self.assertNotIn("CLAUDE.md", self.listing())
        # The winner is set aside with its original bytes, import line included.
        self.assert_set_aside(payload, ".", "CLAUDE.md", b"@AGENTS.md\n")
        self.assertEqual(code, 0, (payload, err))

    def test_a_second_run_changes_nothing(self):
        self.put("CLAUDE.md", self.CLAUDE, track=True)
        self.put("agents.md", self.AGENTS, track=True)
        self.put("sub/CLAUDE.md", self.CLAUDE, track=True)
        self.commit()
        self.put("sub/AGENTS.md", self.AGENTS)
        first, _, err = self.cli("--migrate")
        self.assertEqual(first, 0, err)
        files, index = self.files(), self.git("ls-files", "-s")
        receipt = (self.root / im.RECEIPT_NAME).read_bytes()
        # Positive control: the first run did change the tree.
        self.assertIn("AGENTS.md", self.listing())
        self.assertNotIn("CLAUDE.md", self.listing())
        second, payload, err = self.cli("--migrate")
        self.assertEqual(second, 0, (payload, err))
        self.assertEqual(self.files(), files)
        self.assertEqual(self.git("ls-files", "-s"), index)
        self.assertEqual((self.root / im.RECEIPT_NAME).read_bytes(), receipt)
        self.assertEqual(payload["actions"], [])


class LoneScopeTests(GitRepoCase):
    """A scope holding one family keeps its behaviour, now staged in the index."""

    def test_a_lone_claude_md_migrates_byte_for_byte(self):
        body = b"line one\nline two\n\ntrailing\n"
        self.put("CLAUDE.md", body, track=True)
        self.commit()
        code, payload, err = self.cli("--migrate")
        self.assertEqual(code, 0, (payload, err))
        self.assertEqual((self.root / "AGENTS.md").read_bytes(), body)
        self.assertEqual(self.listing(), [".git", im.RECEIPT_NAME, "AGENTS.md"])
        self.assertEqual(self.git("ls-files").split(), ["AGENTS.md"])
        self.assertEqual(payload["set_asides"], [])

    def test_a_lone_variant_takes_the_exact_spelling(self):
        self.put("Agents.md", b"v\n", track=True)
        self.commit()
        code, payload, err = self.cli("--migrate")
        self.assertEqual(code, 0, (payload, err))
        self.assertIn("AGENTS.md", self.listing())
        self.assertNotIn("Agents.md", self.listing())
        self.assertEqual((self.root / "AGENTS.md").read_bytes(), b"v\n")
        self.assertEqual(self.git("ls-files").split(), ["AGENTS.md"])
        self.assertEqual([a["kind"] for a in payload["actions"]], ["respell", "index"])

    def test_dry_run_reports_and_writes_nothing(self):
        self.put("CLAUDE.md", b"body\n", track=True)
        self.commit()
        files, index = self.files(), self.git("ls-files", "-s")
        code, payload, err = self.cli("--dry-run")
        self.assertEqual(code, 1, err)
        self.assertEqual([a["kind"] for a in payload["actions"]], ["rename-in", "index"])
        self.assertEqual(self.files(), files)
        self.assertEqual(self.git("ls-files", "-s"), index)
        self.assertFalse(payload["clean"])

    def test_a_clean_tree_exits_zero(self):
        self.put("AGENTS.md", b"body\n", track=True)
        self.commit()
        code, payload, err = self.cli("--dry-run")
        self.assertEqual(code, 0, (payload, err))
        self.assertTrue(payload["clean"])

    def test_the_boundary_is_the_touched_scopes(self):
        """An untracked CLAUDE.md in a scope with no managed member is not touched."""
        self.put("CLAUDE.md", b"body\n", track=True)
        self.commit()
        self.put("untracked/CLAUDE.md", b"not mine\n")
        self.put("untracked/AGENTS.md", b"nor this\n")
        code, payload, err = self.cli("--migrate")
        self.assertEqual(code, 0, (payload, err))
        self.assertEqual((self.root / "AGENTS.md").read_bytes(), b"body\n")
        self.assertEqual((self.root / "untracked" / "CLAUDE.md").read_bytes(),
                         b"not mine\n")
        self.assertEqual((self.root / "untracked" / "AGENTS.md").read_bytes(),
                         b"nor this\n")

    def test_a_denylisted_claude_md_is_reported_excluded_as_today(self):
        self.put(".bionic.yml", "instruction_migration_denylist:\n  - fx/CLAUDE.md\n",
                 track=True)
        self.put("fx/CLAUDE.md", b"fixture\n", track=True)
        self.put("fx/AGENTS.md", b"canonical\n", track=True)
        self.commit()
        code, payload, err = self.cli("--migrate")
        self.assertEqual(code, 0, (payload, err))
        self.assertEqual(payload["excluded"]["fx/CLAUDE.md"], "excluded:denylist")
        self.assertEqual((self.root / "fx" / "CLAUDE.md").read_bytes(), b"fixture\n")
        self.assertEqual((self.root / "fx" / "AGENTS.md").read_bytes(), b"canonical\n")
        self.assertEqual(payload["actions"], [])

    def test_the_entry_point_declares_no_third_party_dependencies(self):
        head = ENTRY.read_text(encoding="utf-8")
        self.assertIn("# /// script", head)
        self.assertIn("dependencies = []", head)


class RefusalTests(GitRepoCase):
    """Clause 6 and the refusing rows of clause 4. Each refused scope changes
    nothing, and a second scope migrating in the same run is the positive control."""

    def setUp(self):
        super().setUp()
        self.put("ok/CLAUDE.md", b"control\n", track=True)

    def refused(self, scope: str = ".") -> dict:
        self.commit()
        files, index = self.files(), self.git("ls-files", "-s")
        code, payload, err = self.cli("--migrate")
        self.assertEqual((self.root / "ok" / "AGENTS.md").read_bytes(), b"control\n",
                         (code, payload, err))
        self.assertEqual(code, 1, (payload, err))
        rows = [r for r in payload["refusals"] if r["scope"] == scope]
        self.assertEqual(len(rows), 1, payload["refusals"])
        after = {k: v for k, v in self.files().items() if not k.startswith("ok/")}
        self.assertEqual(after, {k: v for k, v in files.items() if not k.startswith("ok/")})
        self.assertEqual([l for l in self.git("ls-files", "-s").splitlines()
                          if "ok/" not in l],
                         [l for l in index.splitlines() if "ok/" not in l])
        return rows[0]

    def test_a_symlinked_loser_refuses_and_names_its_target(self):
        self.put("CLAUDE.md", b"c\n", track=True)
        outside = self.base / "outside.md"
        outside.write_bytes(b"linked\n")
        os.symlink(outside, self.root / "AGENTS.md")
        row = self.refused()
        self.assertEqual(row["next_step"], im.NEXT_LINK)
        self.assertEqual(row["target"], str(outside))
        self.assertEqual(outside.read_bytes(), b"linked\n")

    def test_a_directory_loser_refuses(self):
        self.put("CLAUDE.md", b"c\n", track=True)
        self.put("AGENTS.md/notes.md", b"inside\n")
        self.assertEqual(self.refused()["next_step"], im.NEXT_REGULAR)

    def test_an_unreadable_loser_refuses(self):
        self.put("CLAUDE.md", b"c\n", track=True)
        path = self.put("AGENTS.md", b"private\n")
        path.chmod(0)
        self.addCleanup(path.chmod, 0o644)
        if os.access(path, os.R_OK):
            self.skipTest("this user reads a mode-0 file")
        self.assertEqual(self.refused()["next_step"], im.NEXT_REGULAR)

    def test_a_winner_that_is_not_a_regular_file_refuses(self):
        self.put("AGENTS.md", b"a\n", track=True)
        self.put("CLAUDE.md/rules.md", b"inside\n")
        row = self.refused()
        self.assertEqual(row["next_step"], im.NEXT_REGULAR)
        self.assertIn("winner", row["reason"])

    def test_an_ignored_loser_whose_set_aside_name_is_not_ignored_refuses(self):
        self.put(".gitignore", b"/AGENTS.md\n", track=True)
        self.put("CLAUDE.md", b"c\n", track=True)
        self.put("AGENTS.md", b"private\n")
        self.assertEqual(self.refused()["next_step"], im.NEXT_IGNORED)

    def test_control_an_ignored_loser_whose_set_aside_is_ignored_is_set_aside(self):
        self.put(".gitignore", b"/AGENTS.md*\n", track=True)
        self.put("CLAUDE.md", b"c\n", track=True)
        self.put("AGENTS.md", b"private\n")
        self.commit()
        code, payload, err = self.cli("--migrate")
        self.assertEqual(payload["refusals"], [], err)
        row = [r for r in payload["set_asides"] if r["entry"] == "AGENTS.md"][0]
        self.assertTrue(row["ignored_by_git"])
        self.assertEqual(row["git_state"], "ignored")
        self.assertEqual((self.root / row["set_aside"]).read_bytes(), b"private\n")

    def test_skip_worktree_refuses(self):
        self.put("CLAUDE.md", b"c\n", track=True)
        self.put("AGENTS.md", b"a\n", track=True)
        self.git("update-index", "--skip-worktree", "AGENTS.md")
        self.assertEqual(self.refused()["next_step"], im.NEXT_FLAGS)

    def test_assume_unchanged_refuses(self):
        self.put("CLAUDE.md", b"c\n", track=True)
        self.put("AGENTS.md", b"a\n", track=True)
        self.git("update-index", "--assume-unchanged", "CLAUDE.md")
        self.assertEqual(self.refused()["next_step"], im.NEXT_FLAGS)

    def test_an_unmerged_index_entry_refuses(self):
        self.put("CLAUDE.md", b"c\n", track=True)
        self.put("AGENTS.md", b"a\n", track=True)
        self.commit()
        blobs = [self.git("hash-object", "-w", "--stdin", stdin=d).strip()
                 for d in (b"base\n", b"ours\n", b"theirs\n")]
        info = "0 " + "0" * 40 + "\tAGENTS.md\n" + "".join(
            f"100644 {b} {n}\tAGENTS.md\n" for n, b in enumerate(blobs, 1))
        self.git("update-index", "--index-info", stdin=info.encode())
        files, index = self.files(), self.git("ls-files", "-s")
        code, payload, err = self.cli("--migrate")
        self.assertEqual((self.root / "ok" / "AGENTS.md").read_bytes(), b"control\n")
        rows = [r for r in payload["refusals"] if r["scope"] == "."]
        self.assertEqual(rows[0]["next_step"], im.NEXT_FLAGS, payload["refusals"])
        self.assertEqual(self.git("ls-files", "-s").count("AGENTS.md\n"), 4)
        self.assertEqual((self.root / "AGENTS.md").read_bytes(), b"a\n")
        self.assertEqual(code, 1)

    def test_a_volume_without_a_no_replace_rename_refuses(self):
        class NoRename(im.RealFS):
            def noreplace_available(self):
                return False
        self.put("CLAUDE.md", b"c\n", track=True)
        self.put("AGENTS.md", b"a\n", track=True)
        self.commit()
        d = im.discover(self.root, denylist=[])
        plan = im.build_plan(d, fs=NoRename())
        rows = [r for r in plan.refusals if r["scope"] == "."]
        self.assertEqual(rows[0]["next_step"], im.NEXT_VOLUME)
        # Positive control: the same tree plans steps on this platform's volume.
        self.assertTrue([s for s in im.build_plan(d).actions if s.scope == "."])


class ImportLineTests(unittest.TestCase):
    """Clause 3: which lines are import lines, and what replaces them."""

    def inline(self, winner, loser=b"L\n", name="AGENTS.md", folds=False):
        return im.inline_imports(winner, loser, name, folds)

    def test_an_import_line_and_its_dot_slash_form_are_replaced(self):
        for line in (b"@AGENTS.md\n", b"@./AGENTS.md\n"):
            out, rep, _, _ = self.inline(b"top\n" + line + b"end\n")
            self.assertEqual(out, b"top\nL\nend\n", line)
            self.assertEqual([r["line"] for r in rep], [2])

    def test_another_spelling_matches_only_on_a_folding_volume(self):
        out, rep, others, _ = self.inline(b"@agents.md\n")
        self.assertEqual((out, rep), (b"@agents.md\n", []))
        self.assertEqual(others[0]["next_step"], im.NEXT_IMPORT)
        out, rep, _, _ = self.inline(b"@agents.md\n", folds=True)
        self.assertEqual(out, b"L\n")

    def test_a_line_inside_a_fence_is_not_an_import_line(self):
        text = b"```\n@AGENTS.md\n```\n~~~~\n@AGENTS.md\n~~~~\n@AGENTS.md\n"
        out, rep, _, _ = self.inline(text)
        self.assertEqual([r["line"] for r in rep], [7])
        self.assertEqual(out, text[:-len(b"@AGENTS.md\n")] + b"L\n")

    def test_the_line_ending_is_appended_only_where_the_loser_lacks_one(self):
        out, _, _, _ = self.inline(b"@AGENTS.md\r\nx\r\n", loser=b"no ending")
        self.assertEqual(out, b"no ending\r\nx\r\n")
        out, _, _, _ = self.inline(b"@AGENTS.md\r\nx\r\n", loser=b"has\n")
        self.assertEqual(out, b"has\nx\r\n")
        out, _, _, _ = self.inline(b"@AGENTS.md", loser=b"no ending")
        self.assertEqual(out, b"no ending")

    def test_the_replacement_does_not_recurse_and_decodes_nothing(self):
        loser = b"@AGENTS.md\n\xff\xfe latin \xe9\n"
        out, rep, _, carries = self.inline(b"@AGENTS.md\n", loser=loser)
        self.assertEqual(out, loser)
        self.assertEqual(len(rep), 1)
        self.assertFalse(carries)

    def test_other_family_imports_are_reported_with_a_next_step(self):
        _, rep, others, carries = self.inline(b"@CLAUDE.md\n@./claude.MD\n@README.md\n")
        self.assertEqual(rep, [])
        self.assertEqual([o["line"] for o in others], [1, 2])
        self.assertTrue(carries)

    def test_trailing_text_makes_a_line_not_an_import_line(self):
        out, rep, others, _ = self.inline(b"@AGENTS.md \n@AGENTS.md.bak\n")
        self.assertEqual((rep, others), ([], []))
        self.assertEqual(out, b"@AGENTS.md \n@AGENTS.md.bak\n")

    def test_a_family_name_under_full_case_folding_is_not_a_member(self):
        """`agentſ.md` lowercases to itself; only full case folding reaches `s`."""
        self.assertIsNone(im.family_of("agentſ.md"))
        self.assertEqual("agentſ.md".casefold(), "agents.md")  # positive control


class StagingTests(GitRepoCase):
    """Clause 7: a scope that would publish private bytes or drop an index blob
    stages nothing, leaves the winner in place, and exits 1."""

    def assert_nothing_staged(self, index_before: str):
        self.assertEqual(self.git("ls-files", "-s"), index_before)

    def test_an_untracked_winner_is_left_in_place_and_nothing_is_staged(self):
        self.put("AGENTS.md", b"tracked agents\n", track=True)
        self.commit()
        self.put("CLAUDE.md", b"private claude\n")
        index = self.git("ls-files", "-s")
        code, payload, err = self.cli("--migrate")
        self.assertEqual((self.root / "AGENTS.md").read_bytes(), b"private claude\n")
        self.assertEqual((self.root / "CLAUDE.md").read_bytes(), b"private claude\n")
        self.assertEqual(len(self.holders(b"tracked agents\n")), 1)
        self.assert_nothing_staged(index)
        self.assertEqual(code, 1, (payload, err))
        scope = payload["scopes"][0]
        self.assertFalse(scope["stages"])
        self.assertEqual(scope["publish_note"], im.PUBLISH_NOTE)
        self.assertTrue(scope["winner_left_in_place"])
        rows = [s for s in payload["suppressors"] if s["path"] == "CLAUDE.md"]
        self.assertEqual(rows[0]["remedy"], "")
        self.assertNotIn("CLAUDE.md", payload["excluded"])

    def test_an_index_only_blob_stages_nothing_and_names_its_save_command(self):
        self.put("CLAUDE.md", b"claude\n", track=True)
        self.put("AGENTS.md", b"committed\n", track=True)
        self.commit()
        self.put("AGENTS.md", b"staged only\n", track=True)
        self.put("AGENTS.md", b"working\n")
        index = self.git("ls-files", "-s")
        code, payload, err = self.cli("--migrate")
        self.assert_nothing_staged(index)
        self.assertEqual(self.git("show", ":0:AGENTS.md"), "staged only\n")
        self.assertEqual((self.root / "AGENTS.md").read_bytes(), b"claude\n")
        self.assertEqual((self.root / "CLAUDE.md").read_bytes(), b"claude\n")
        self.assertEqual(len(self.holders(b"working\n")), 1)
        row = payload["scopes"][0]["index_only_blobs"][0]
        self.assertEqual(row["path"], "AGENTS.md")
        self.assertIn("git show :0:AGENTS.md", row["next_step"])
        self.assertEqual(code, 1)

    def test_an_ignored_exact_path_stages_nothing(self):
        self.put(".gitignore", b"/AGENTS.md\n", track=True)
        self.put("CLAUDE.md", b"claude\n", track=True)
        self.commit()
        index = self.git("ls-files", "-s")
        code, payload, err = self.cli("--migrate")
        self.assert_nothing_staged(index)
        self.assertEqual((self.root / "AGENTS.md").read_bytes(), b"claude\n")
        self.assertEqual((self.root / "CLAUDE.md").read_bytes(), b"claude\n")
        self.assertIn("ignores", " ".join(payload["scopes"][0]["stages_nothing_because"]))
        self.assertEqual(code, 1)

    def test_control_a_tracked_pair_stages_the_result(self):
        self.put("CLAUDE.md", b"claude\n", track=True)
        self.put("AGENTS.md", b"agents\n", track=True)
        self.commit()
        code, payload, err = self.cli("--migrate")
        self.assertEqual(code, 0, (payload, err))
        self.assertEqual(self.git("ls-files").split(), ["AGENTS.md"])
        self.assertEqual(self.git("show", ":0:AGENTS.md"), "claude\n")


class FoldingFS(im.RealFS):
    """The case-folding seam: stat, read, write, exclusive create, rename, link
    and unlink all resolve a name to its case-fold alias, as a case-insensitive
    volume does. The listing is the volume's own, which holds each entry under
    the one spelling it was created with, as a folding volume lists it. On a
    case-sensitive volume it simulates a folding one."""

    @staticmethod
    def alias(path: Path) -> Path | None:
        try:
            names = os.listdir(path.parent)
        except FileNotFoundError:
            return None
        if path.name in names:
            return path
        return next((path.parent / n for n in names
                     if n.lower() == path.name.lower()), None)

    def _at(self, path: Path) -> Path:
        return self.alias(path) or path

    def lstat(self, path):
        return super().lstat(self._at(path))

    def read(self, path):
        return super().read(self._at(path))

    def readlink(self, path):
        return super().readlink(self._at(path))

    def write(self, path: Path, data: bytes) -> None:
        """A replacing write, through any alias."""
        self._at(path).write_bytes(data)

    def create_excl(self, path, data):
        if self.alias(path) is not None:
            raise FileExistsError(errno.EEXIST, "exists", str(path))
        super().create_excl(path, data)

    def rename_noreplace(self, src, dst):
        if self.alias(dst) is not None:
            raise FileExistsError(errno.EEXIST, "exists", str(dst))
        super().rename_noreplace(self._at(src), dst)

    def rename(self, src, dst):
        source, target = self._at(src), self.alias(dst)
        if target is not None and target != source:
            os.replace(source, target)  # a folding volume replaces the alias
        else:
            os.rename(source, dst)

    def link(self, src, dst):
        if self.alias(dst) is not None:
            raise FileExistsError(errno.EEXIST, "exists", str(dst))
        os.link(self._at(src), dst)

    def unlink(self, path):
        os.unlink(self._at(path))


class FoldingSeamTests(GitRepoCase):
    """D2 proved on any volume, through the seam, and the seam proved faithful."""

    def test_positive_control_the_seam_folds_each_operation(self):
        fs = FoldingFS()
        d = self.base / "seam"
        d.mkdir()
        (d / "agents.md").write_bytes(b"variant\n")
        (d / "x").write_bytes(b"x\n")
        self.assertEqual(fs.read(d / "AGENTS.md"), b"variant\n")
        self.assertEqual(fs.lstat(d / "AGENTS.md").st_ino, os.lstat(d / "agents.md").st_ino)
        with self.assertRaises(FileExistsError):
            fs.create_excl(d / "AGENTS.md", b"new\n")
        with self.assertRaises(FileExistsError):
            fs.rename_noreplace(d / "x", d / "AGENTS.md")
        with self.assertRaises(FileExistsError):
            fs.link(d / "x", d / "Agents.MD")
        # The loss shape: a replacing rename onto an alias destroys the variant.
        fs.rename(d / "x", d / "AGENTS.md")
        self.assertEqual(sorted(os.listdir(d)), ["agents.md"])
        self.assertEqual((d / "agents.md").read_bytes(), b"x\n")
        fs.write(d / "AGENTS.MD", b"written\n")
        self.assertEqual((d / "agents.md").read_bytes(), b"written\n")
        fs.unlink(d / "AGENTS.md")
        self.assertEqual(os.listdir(d), [])

    def test_d2_migrates_without_loss_through_the_folding_seam(self):
        self.git("config", "core.ignorecase", "true")
        self.put("CLAUDE.md", b"claude\n", track=True)
        self.put("agents.md", b"variant\n", track=True)
        self.commit()
        fs = FoldingFS()
        plan = im.build_plan(im.discover(self.root, denylist=[]), fs=fs)
        self.assertTrue(plan.scopes[0].folds)
        self.assertEqual([s.kind for s in plan.actions],
                         ["set-aside", "rename-in", "index"])
        receipt = im.apply_plan(plan, self.root)
        self.assertFalse(receipt.has_unresolved(), receipt.scopes)
        self.assertEqual(len(self.holders(b"variant\n")), 1)
        self.assertEqual((self.root / "AGENTS.md").read_bytes(), b"claude\n")
        self.assertEqual(sorted(n for n in self.listing() if "agents" in n.lower()),
                         ["AGENTS.md", self.holders(b"variant\n")[0]])

    def test_positive_control_create_before_set_aside_meets_the_alias(self):
        """Reordered, the plan stops at the alias instead of writing through it."""
        self.git("config", "core.ignorecase", "true")
        self.put("CLAUDE.md", b"@agents.md\nclaude\n", track=True)
        self.put("agents.md", b"variant\n", track=True)
        self.commit()
        fs = FoldingFS()
        plan = im.build_plan(im.discover(self.root, denylist=[]), fs=fs)
        sp = plan.scopes[0]
        self.assertEqual([s.kind for s in sp.steps][:2], ["set-aside", "create"])
        sp.steps[0], sp.steps[1] = sp.steps[1], sp.steps[0]
        receipt = im.apply_plan(plan, self.root)
        self.assertIn("case-fold alias", receipt.scopes[0]["stopped"])
        self.assertEqual((self.root / "agents.md").read_bytes(), b"variant\n")
        self.assertEqual(sp.temporaries, [])
        self.assertEqual([n for n in self.listing() if n.endswith(".tmp")], [])

    @unittest.skipUnless(_folding_volume(Path(tempfile.gettempdir())),
                         "needs a case-insensitive volume")
    def test_the_seam_agrees_with_a_real_case_insensitive_volume(self):
        real, seam = im.RealFS(), FoldingFS()
        outcomes = []
        for fs in (real, seam):
            d = Path(tempfile.mkdtemp(dir=self.base))
            (d / "agents.md").write_bytes(b"variant\n")
            (d / "x").write_bytes(b"x\n")
            row = [fs.read(d / "AGENTS.md"),
                   fs.lstat(d / "AGENTS.MD").st_ino == os.lstat(d / "agents.md").st_ino]
            for op in (lambda: fs.create_excl(d / "AGENTS.md", b"n"),
                       lambda: fs.rename_noreplace(d / "x", d / "AGENTS.md")):
                try:
                    op()
                    row.append("ok")
                except FileExistsError:
                    row.append("exists")
            fs.rename(d / "x", d / "AGENTS.md")
            row.append(sorted(n.lower() for n in os.listdir(d)))
            row.append(fs.read(d / "agents.md"))
            outcomes.append(row)
        self.assertEqual(outcomes[0], outcomes[1])


class CaseSensitiveFS(im.RealFS):
    """The case-sensitive seam: a name resolves only when the listing holds it
    exactly, as on a case-sensitive volume. On a case-folding volume it simulates
    one; on a case-sensitive volume it behaves as the volume does.

    Limit: it cannot hold two entries whose names differ only in case on a
    folding volume, so a rename onto a different entry's alias is refused."""

    @staticmethod
    def _listed(path: Path) -> bool:
        try:
            return path.name in os.listdir(path.parent)
        except FileNotFoundError:
            return False

    def _need(self, path: Path) -> Path:
        if not self._listed(path):
            raise FileNotFoundError(errno.ENOENT, "not listed", str(path))
        return path

    def lstat(self, path):
        return super().lstat(self._need(path))

    def read(self, path):
        return super().read(self._need(path))

    def readlink(self, path):
        return super().readlink(self._need(path))

    def rename_noreplace(self, src, dst):
        self._need(src)
        if self._listed(dst):
            raise FileExistsError(errno.EEXIST, "exists", str(dst))
        alias = [n for n in os.listdir(dst.parent)
                 if n.lower() == dst.name.lower() and n != src.name]
        if alias:
            raise FileExistsError(errno.EEXIST, "a folding volume cannot hold both",
                                  str(dst))
        if src.name.lower() == dst.name.lower():
            os.rename(src, dst)  # a case-only rename of the one entry
        else:
            super().rename_noreplace(src, dst)


class CrashResumeTests(GitRepoCase):
    """Clause 7 and 8: a run stopped after any step, the last included, then
    rerun, ends where an uninterrupted run ends, and no step leaves a partial
    AGENTS.md. Dirty, filtered and respelled sources are cases of their own,
    because each once made the rerun miss what the stopped run had moved."""

    # label -> (builder, case-sensitive seam)
    CASES = {
        "create": ("build_create", False),
        "rename-in": ("build_rename_in", False),
        "dirty-winner-rename-in": ("build_dirty_winner_rename_in", False),
        "dirty-winner-create": ("build_dirty_winner_create", False),
        "dirty-loser-create": ("build_dirty_loser_create", False),
        "lone-dirty-claude": ("build_lone_dirty_claude", False),
        "filtered-create": ("build_filtered_create", False),
        "filtered-rename-in": ("build_filtered_rename_in", False),
        "respell-case-sensitive": ("build_respell", True),
        "dirty-respell-case-sensitive": ("build_dirty_respell", True),
        "dirty-variant-loser-case-sensitive": ("build_dirty_variant_loser", True),
    }

    def build(self, winner: bytes, loser: bytes, loser_tracked: bool):
        self.put("CLAUDE.md", winner, track=True)
        self.put("AGENTS.md", loser, track=loser_tracked)
        self.commit()
        if not loser_tracked:
            self.put("AGENTS.md", loser)

    def build_create(self):
        self.build(b"# C\n@AGENTS.md\n", b"# A\n", True)

    def build_rename_in(self):
        self.build(b"# C\n", b"# A\n", False)

    def build_dirty_winner_rename_in(self):
        self.build(b"# C\n", b"# A\n", True)
        self.put("CLAUDE.md", b"# C edited\n")

    def build_dirty_winner_create(self):
        self.build(b"# C\n@AGENTS.md\n", b"# A\n", True)
        self.put("CLAUDE.md", b"# C edited\n@AGENTS.md\n")

    def build_dirty_loser_create(self):
        self.build(b"# C\n@AGENTS.md\n", b"# A\n", True)
        self.put("AGENTS.md", b"# A edited\n")

    def build_lone_dirty_claude(self):
        self.put("CLAUDE.md", b"# C\n", track=True)
        self.commit()
        self.put("CLAUDE.md", b"# C edited\n")

    def build_filtered_create(self):
        self.put(".gitattributes", b"*.md text eol=crlf\n", track=True)
        self.build(b"# C\r\n@AGENTS.md\r\n", b"# A\r\n", True)

    def build_filtered_rename_in(self):
        self.put(".gitattributes", b"*.md text eol=crlf\n", track=True)
        self.build(b"# C\r\n", b"# A\r\n", True)

    def build_respell(self):
        self.put("agents.md", b"# variant\n", track=True)
        self.commit()

    def build_dirty_respell(self):
        self.build_respell()
        self.put("agents.md", b"# variant edited\n")

    def build_dirty_variant_loser(self):
        self.put("CLAUDE.md", b"# C\n@agents.md\n", track=True)
        self.put("agents.md", b"# A\n", track=True)
        self.commit()
        self.put("agents.md", b"# A edited\n")

    def state(self):
        # No filter on temporaries: a leftover `.crux-migrate-*.tmp` is a difference.
        return self.files(), self.git("ls-files", "-s")

    def reset(self, case_sensitive: bool = False):
        shutil.rmtree(self.root)
        self.root.mkdir()
        self.git("init", "-q")
        self.git("config", "user.email", "t@example.invalid")
        self.git("config", "user.name", "t")
        self.git("config", "commit.gpgsign", "false")
        if case_sensitive:
            self.git("config", "core.ignorecase", "false")

    def migrate(self, hook=None, fs=None):
        plan = im.build_plan(im.discover(self.root, denylist=[]), fs=fs)
        return plan, im.apply_plan(plan, self.root, hook=hook)

    def test_a_rerun_after_a_stop_at_every_step_matches_an_uninterrupted_run(self):
        for label, (builder, case_sensitive) in self.CASES.items():
            fs = CaseSensitiveFS() if case_sensitive else None
            self.reset(case_sensitive)
            getattr(self, builder)()
            plan, _ = self.migrate(fs=fs)
            kinds = [s.kind for s in plan.actions]
            self.assertTrue(kinds, label)
            want, want_findings = self.state(), plan.findings()
            for stop_after in range(1, len(kinds) + 1):
                with self.subTest(case=label, stop_after=kinds[stop_after - 1],
                                  step=stop_after):
                    self.reset(case_sensitive)
                    getattr(self, builder)()
                    before = self.files().get("AGENTS.md")
                    seen = []

                    def hook(scope, kind):
                        seen.append(kind)
                        if len(seen) == stop_after:
                            raise KeyboardInterrupt("stopped")

                    with self.assertRaises(KeyboardInterrupt):
                        self.migrate(hook, fs=fs)
                    # No partial AGENTS.md: it is absent or holds a whole file.
                    held = self.files().get("AGENTS.md")
                    self.assertIn(held, (None, before, plan.scopes[0].result))
                    rerun, _ = self.migrate(fs=fs)
                    self.assertEqual(rerun.refusals, [], rerun.scopes[0].to_json())
                    self.assertEqual(self.state(), want)
                    self.assertEqual(rerun.findings(), want_findings)
                    third, receipt = self.migrate(fs=fs)
                    self.assertTrue(receipt.is_noop())
                    self.assertEqual(self.state(), want)

    def test_positive_control_the_case_sensitive_seam_refuses_an_alias_name(self):
        """The seam resolves only an exactly listed name, so the respell test
        runs the case-sensitive planner on a folding volume too."""
        fs = CaseSensitiveFS()
        d = self.base / "cs"
        d.mkdir()
        (d / "agents.md").write_bytes(b"variant\n")
        self.assertEqual(fs.read(d / "agents.md"), b"variant\n")
        with self.assertRaises(FileNotFoundError):
            fs.read(d / "AGENTS.md")
        with self.assertRaises(FileNotFoundError):
            fs.lstat(d / "Agents.md")
        self.assertFalse(im._folds(fs, d, ["agents.md"]))

    def test_a_locked_index_stop_then_rerun_converges_and_never_reports_clean(self):
        """M1: git refused the index step (a stale index.lock); the rerun after
        the lock is gone ends where an uninterrupted run ends."""
        for builder in ("build_dirty_winner_rename_in", "build_lone_dirty_claude",
                        "build_dirty_loser_create", "build_dirty_winner_create"):
            with self.subTest(builder=builder):
                self.reset()
                getattr(self, builder)()
                want_code, _, err = self.cli("--migrate")
                want = self.state()
                self.reset()
                getattr(self, builder)()
                lock = self.root / ".git" / "index.lock"
                lock.write_bytes(b"")
                code, payload, err = self.cli("--migrate")
                self.assertEqual(code, 1, (payload, err))
                self.assertIn("git refused the index change",
                              payload["scopes"][0]["stopped"])
                lock.unlink()
                code, payload, err = self.cli("--migrate")
                self.assertEqual(self.state(), want, payload)
                self.assertEqual(code, want_code, (payload, err))
                code, payload, err = self.cli("--migrate")
                self.assertEqual((code, payload["actions"]), (want_code, []), payload)

    def test_a_temporary_entry_a_killed_run_left_is_reported_and_kept(self):
        self.put("CLAUDE.md", b"# C\n@AGENTS.md\n", track=True)
        self.put("AGENTS.md", b"# A\n", track=True)
        self.commit()
        leftover = self.put(".crux-migrate-0123456789ab.tmp", b"# C\n# A")
        code, payload, err = self.cli("--migrate")
        self.assertEqual(leftover.read_bytes(), b"# C\n# A")
        self.assertEqual(payload["temporaries"], [".crux-migrate-0123456789ab.tmp"])
        self.assertEqual((self.root / "AGENTS.md").read_bytes(), b"# C\n# A\n")
        self.assertEqual(code, 1, (payload, err))

    def test_more_than_one_set_aside_inlines_none(self):
        self.put("CLAUDE.md", b"@AGENTS.md\n", track=True)
        self.commit()
        self.put("AGENTS.md.crux-set-aside-aaaaaaaaaaaa", b"one\n")
        self.put("AGENTS.md.crux-set-aside-bbbbbbbbbbbb", b"two\n")
        code, payload, err = self.cli("--migrate")
        self.assertEqual((self.root / "AGENTS.md").read_bytes(), b"@AGENTS.md\n")
        scope = payload["scopes"][0]
        self.assertEqual(scope["imports_inlined"], [])
        self.assertEqual(sorted(scope["no_longer_loaded"]),
                         ["AGENTS.md.crux-set-aside-aaaaaaaaaaaa",
                          "AGENTS.md.crux-set-aside-bbbbbbbbbbbb"])
        self.assertEqual(len([r for r in payload["set_asides"] if r["status"] == "found"]),
                         2)
        self.assertEqual(scope["other_imports"][0]["next_step"], im.NEXT_IMPORT)

    def test_one_set_aside_is_the_loser_input_of_a_rerun(self):
        self.put("CLAUDE.md", b"# C\n@AGENTS.md\n", track=True)
        self.commit()
        self.put("AGENTS.md.crux-set-aside-aaaaaaaaaaaa", b"# A\n")
        code, payload, err = self.cli("--migrate")
        self.assertEqual((self.root / "AGENTS.md").read_bytes(), b"# C\n# A\n")
        # The set-aside is untracked, so its inlined bytes are not staged.
        self.assertEqual(code, 1, (payload, err))
        self.assertIn("set-aside AGENTS.md.crux-set-aside-aaaaaaaaaaaa, whose bytes no "
                      "index blob holds and no receipt record marks as tracked",
                      " ".join(payload["scopes"][0]["stages_nothing_because"]))


class ReceiptAndLogTests(GitRepoCase):
    def test_the_receipt_records_set_asides_imports_blobs_and_index_changes(self):
        self.put("CLAUDE.md", b"# C\n@AGENTS.md\n", track=True)
        self.put("AGENTS.md", b"# A\n", track=True)
        self.commit()
        code, payload, err = self.cli("--migrate")
        self.assertEqual(code, 0, (payload, err))
        receipt = im.read_receipt(self.root / im.RECEIPT_NAME)
        scope = receipt["scopes"][0]
        self.assertEqual({r["entry"] for r in scope["set_asides"]},
                         {"AGENTS.md", "CLAUDE.md"})
        for row in scope["set_asides"]:
            self.assertEqual(row["git_state"], "tracked")
            self.assertEqual(row["status"], "created")
            self.assertEqual(len(row["sha256"]), 64)
        self.assertEqual(scope["imports_inlined"], [{"line": 2, "text": "@AGENTS.md"}])
        self.assertEqual({b["path"] for b in scope["index_blobs"]},
                         {"AGENTS.md", "CLAUDE.md"})
        self.assertEqual(scope["index_changes"][0]["removed"], ["CLAUDE.md"])
        self.assertEqual(scope["index_changes"][0]["added"], "AGENTS.md")
        self.assertFalse(receipt["atomic"])
        self.assertIn("power loss", receipt["staging_note"])

    def test_the_receipt_records_each_refusal(self):
        self.put("CLAUDE.md", b"c\n", track=True)
        self.put("AGENTS.md", b"a\n", track=True)
        self.git("update-index", "--skip-worktree", "AGENTS.md")
        self.commit()
        self.cli("--migrate")
        receipt = im.read_receipt(self.root / im.RECEIPT_NAME)
        self.assertEqual(receipt["scopes"][0]["refusals"][0]["next_step"], im.NEXT_FLAGS)

    def test_the_log_records_one_migration_operation_not_one_entry_per_file(self):
        for rel in ("a/CLAUDE.md", "b/CLAUDE.md", "c/CLAUDE.md"):
            self.put(rel, b"x\n", track=True)
        self.commit()
        log = self.put("bionic/log.md", "# Operations log\n\n_Append-only. Newest first._\n\n")
        _, receipt = CrashResumeTests.migrate(self)
        im.append_log(log, receipt, date="2026-09-21")
        heads = [l for l in log.read_text(encoding="utf-8").splitlines()
                 if l.startswith("## [")]
        self.assertEqual(len(heads), 1)
        self.assertRegex(heads[0], r"^## \[2026-09-21\] schema \| .*\(3 scopes\)$")


class ReviewRoundOneTests(GitRepoCase):
    """Internal-review findings on the first cut, each pinned before its fix."""

    def test_a_filtered_winner_renamed_in_before_a_stop_still_converges(self):
        """Finding 1: an eol filter made the resume check miss the renamed winner."""
        self.put(".gitattributes", b"* text eol=crlf\n", track=True)
        self.put("CLAUDE.md", b"# C\r\nrules\r\n", track=True)
        self.commit()
        # Positive control: the filter makes the raw bytes differ from the blob.
        self.assertNotEqual(self.git("ls-files", "-s", "CLAUDE.md").split()[1],
                            im._git_blob(b"# C\r\nrules\r\n", "sha1"))
        os.rename(self.root / "CLAUDE.md", self.root / "AGENTS.md")  # stopped after rename-in
        code, payload, err = self.cli("--migrate")
        self.assertEqual(self.git("ls-files").split(), [".gitattributes", "AGENTS.md"],
                         (code, payload, err))
        self.assertEqual(code, 0, (payload, err))
        second, payload, err = self.cli("--migrate")
        self.assertEqual((second, payload["actions"]), (0, []))

    def test_control_the_same_stop_without_a_filter_converges(self):
        self.put("CLAUDE.md", b"# C\nrules\n", track=True)
        self.commit()
        os.rename(self.root / "CLAUDE.md", self.root / "AGENTS.md")
        code, payload, err = self.cli("--migrate")
        self.assertEqual(self.git("ls-files").split(), ["AGENTS.md"], (code, payload, err))

    def test_an_environment_stop_still_writes_the_receipt_of_completed_scopes(self):
        """Finding 3: exit 2 after scope a/ completed left no receipt of a/."""
        self.put("a/CLAUDE.md", b"a\n", track=True)
        self.put("a/AGENTS.md", b"old a\n", track=True)
        self.put("b/CLAUDE.md", b"b\n", track=True)
        self.put("b/AGENTS.md", b"old b\n", track=True)
        self.commit()
        class SecondScopeFails(im.RealFS):
            def rename_noreplace(inner, src, dst):
                if Path(src).parent.name == "b":
                    raise PermissionError(errno.EACCES, "denied", str(src))
                super().rename_noreplace(src, dst)
        plan = im.build_plan(im.discover(self.root, denylist=[]), fs=SecondScopeFails())
        with self.assertRaises(im.RunStopped) as caught:
            im.apply_plan(plan, self.root)
        self.assertEqual(caught.exception.completed, ["a"])
        receipt = im.read_receipt(self.root / im.RECEIPT_NAME)
        rows = {s["scope"]: s for s in receipt["scopes"]}
        self.assertEqual([(r["entry"], r["status"]) for r in rows["a"]["set_asides"]],
                         [("AGENTS.md", "created")])
        self.assertEqual((self.root / "a" / "AGENTS.md").read_bytes(), b"a\n")
        self.assertIn("environment failure", rows["b"]["stopped"])

    def test_a_stopped_scope_records_only_the_set_asides_that_happened(self):
        """Finding 4: a stop before a set-aside still read 'set aside as'."""
        self.put("CLAUDE.md", b"# C\n@AGENTS.md\n", track=True)
        self.put("AGENTS.md", b"# A\n", track=True)
        self.commit()
        plan = im.build_plan(im.discover(self.root, denylist=[]))
        (self.root / "CLAUDE.md").write_bytes(b"# C changed\n")  # the winner moved on
        receipt = im.apply_plan(plan, self.root)
        row = receipt.scopes[0]
        self.assertIn("changed since planning", row["stopped"])
        self.assertEqual([r["status"] for r in row["set_asides"]], ["planned", "planned"])
        self.assertFalse(any(v.startswith("set aside") for v in receipt.dispositions.values()),
                         receipt.dispositions)
        # Positive control: the loser is where it was, so "planned" is the truth.
        self.assertEqual((self.root / "AGENTS.md").read_bytes(), b"# A\n")

    def test_a_stop_after_the_loser_set_aside_records_it_as_created(self):
        self.put("CLAUDE.md", b"# C\n@AGENTS.md\n", track=True)
        self.put("AGENTS.md", b"# A\n", track=True)
        self.commit()
        plan = im.build_plan(im.discover(self.root, denylist=[]))

        def hook(scope, kind):
            if kind == "set-aside":
                (self.root / "CLAUDE.md").write_bytes(b"# C changed\n")
        receipt = im.apply_plan(plan, self.root, hook=hook)
        rows = {r["entry"]: r["status"] for r in receipt.scopes[0]["set_asides"]}
        self.assertEqual(rows, {"AGENTS.md": "created", "CLAUDE.md": "planned"})
        self.assertTrue(receipt.dispositions["AGENTS.md"].startswith("set aside as"))
        self.assertNotIn("CLAUDE.md", receipt.dispositions)

    def test_a_volume_refusal_reports_no_planned_set_aside(self):
        """Finding 5: the refused scope still listed a planned set-aside."""
        class NoRename(im.RealFS):
            def noreplace_available(self):
                return False
        self.put("CLAUDE.md", b"c\n", track=True)
        self.put("AGENTS.md", b"a\n", track=True)
        self.commit()
        sp = im.build_plan(im.discover(self.root, denylist=[]), fs=NoRename()).scopes[0]
        self.assertEqual(sp.refusals[0]["next_step"], im.NEXT_VOLUME)
        self.assertEqual(sp.set_asides, [])
        self.assertEqual(sp.no_longer_loaded, [])
        self.assertFalse(sp.winner_left)
        # Positive control: the same tree plans a set-aside on this volume.
        planned = im.build_plan(im.discover(self.root, denylist=[])).scopes[0]
        self.assertEqual([r["entry"] for r in planned.set_asides], ["AGENTS.md"])



class IndependentReviewRoundOneTests(GitRepoCase):
    """Independent-review findings on the dev module, each pinned before its fix."""

    def test_a_deleted_tracked_family_path_is_never_reported_clean(self):
        """M1: the index holds CLAUDE.md, the directory no longer lists it, and
        nothing accounts for it. That is a finding with a next step, not exit 0."""
        self.put("CLAUDE.md", b"# C\n", track=True)
        self.put("AGENTS.md", b"# A\n", track=True)
        self.commit()
        (self.root / "CLAUDE.md").unlink()
        index = self.git("ls-files", "-s")
        code, payload, err = self.cli("--migrate")
        self.assertEqual(code, 1, (payload, err))
        rows = payload["scopes"][0]["missing_index_paths"]
        self.assertEqual([r["path"] for r in rows], ["CLAUDE.md"])
        self.assertIn("git show :0:CLAUDE.md", rows[0]["next_step"])
        self.assertEqual(self.git("ls-files", "-s"), index)
        # Positive control: the same tree with the file restored migrates clean.
        self.git("checkout", "--", "CLAUDE.md")
        code, payload, err = self.cli("--migrate")
        self.assertEqual(code, 0, (payload, err))

    def _metachar_tree(self) -> tuple[str, Path]:
        scope = "x$(touch PWNED)"
        self.put(f"{scope}/CLAUDE.md", b"claude\n", track=True)
        self.put(f"{scope}/AGENTS.md", b"committed\n", track=True)
        self.commit()
        self.put(f"{scope}/AGENTS.md", b"staged only\n", track=True)
        self.put(f"{scope}/AGENTS.md", b"working\n")
        return scope, self.root / "PWNED"

    @staticmethod
    def _command(next_step: str) -> str:
        start = next_step.index("`") + 1
        return next_step[start:next_step.index("`", start)]

    def test_a_next_step_command_quotes_a_path_with_shell_metacharacters(self):
        """M4: pasted, `git show :0:x$(touch PWNED)/AGENTS.md` ran `touch`."""
        scope, pwned = self._metachar_tree()
        code, payload, err = self.cli("--dry-run")
        rows = [r for sp in payload["scopes"] for r in sp["index_only_blobs"]]
        self.assertEqual(len(rows), 1, payload)
        command = self._command(rows[0]["next_step"])
        proc = subprocess.run(["sh", "-c", command], cwd=self.root, capture_output=True)
        self.assertFalse(pwned.exists(), command)
        self.assertEqual(proc.stdout, b"staged only\n", (command, proc.stderr))

    def test_positive_control_the_unquoted_command_runs_the_substitution(self):
        scope, pwned = self._metachar_tree()
        subprocess.run(["sh", "-c", f"git show :0:{scope}/AGENTS.md"], cwd=self.root,
                       capture_output=True)
        self.assertTrue(pwned.exists())

    def test_every_next_step_template_that_names_a_path_quotes_it(self):
        path = "a b/$(x);y/CLAUDE.md"
        for text in (im.next_index_only(0, path), im.next_missing(path)):
            commands = re.findall(r"`([^`]*)`", text)
            self.assertTrue(commands, text)
            for command in commands:
                with self.subTest(command=command):
                    # The shell reads the path back as one word, unexpanded.
                    self.assertIn(shlex.split(command)[-1], (path, f":0:{path}"))

    def test_the_real_no_replace_rename_refuses_an_existing_exact_name(self):
        """S-a: the C library primitive itself, not the seam."""
        fs = im.RealFS()
        if not fs.noreplace_available():
            self.skipTest("no no-replace rename on this platform")
        d = self.base / "noreplace"
        d.mkdir()
        (d / "src").write_bytes(b"src\n")
        (d / "AGENTS.md").write_bytes(b"keep\n")
        with self.assertRaises(OSError) as caught:
            fs.rename_noreplace(d / "src", d / "AGENTS.md")
        self.assertEqual(caught.exception.errno, errno.EEXIST)
        self.assertEqual((d / "AGENTS.md").read_bytes(), b"keep\n")
        self.assertEqual((d / "src").read_bytes(), b"src\n")
        # Positive control: a plain rename replaces the destination.
        os.rename(d / "src", d / "AGENTS.md")
        self.assertEqual((d / "AGENTS.md").read_bytes(), b"src\n")

    @unittest.skipUnless(_folding_volume(Path(tempfile.gettempdir())),
                         "needs a case-insensitive volume")
    def test_the_real_no_replace_rename_refuses_a_case_fold_alias(self):
        fs = im.RealFS()
        if not fs.noreplace_available():
            self.skipTest("no no-replace rename on this platform")
        d = self.base / "alias"
        d.mkdir()
        (d / "src").write_bytes(b"src\n")
        (d / "agents.md").write_bytes(b"keep\n")
        with self.assertRaises(OSError) as caught:
            fs.rename_noreplace(d / "src", d / "AGENTS.md")
        self.assertEqual(caught.exception.errno, errno.EEXIST)
        self.assertEqual(sorted(os.listdir(d)), ["agents.md", "src"])
        self.assertEqual((d / "agents.md").read_bytes(), b"keep\n")

    def test_a_filtered_staged_only_entry_is_index_only(self):
        """S-b: clause 7 hashes raw bytes, so an eol-filtered staged entry is
        index-only: nothing is staged, its blob is kept, its save step named."""
        self.put(".gitattributes", b"*.md text eol=crlf\n", track=True)
        self.put("CLAUDE.md", b"claude\r\n", track=True)
        self.put("AGENTS.md", b"committed\r\n", track=True)
        self.commit()
        self.put("AGENTS.md", b"staged\r\n", track=True)
        index = self.git("ls-files", "-s")
        code, payload, err = self.cli("--migrate")
        self.assertEqual(self.git("ls-files", "-s"), index)
        self.assertEqual(self.git("show", ":0:AGENTS.md"), "staged\n")
        rows = payload["scopes"][0]["index_only_blobs"]
        self.assertEqual([r["path"] for r in rows], ["AGENTS.md"])
        self.assertIn("git show :0:AGENTS.md", rows[0]["next_step"])
        self.assertEqual(len(self.holders(b"staged\r\n")), 1)
        self.assertEqual(code, 1)

    def test_control_the_same_staged_entry_without_a_filter_stages(self):
        self.put("CLAUDE.md", b"claude\n", track=True)
        self.put("AGENTS.md", b"committed\n", track=True)
        self.commit()
        self.put("AGENTS.md", b"staged\n", track=True)
        code, payload, err = self.cli("--migrate")
        self.assertEqual(payload["scopes"][0]["index_only_blobs"], [])
        self.assertEqual(code, 0, (payload, err))

    def test_a_non_utf8_name_is_reported_escaped(self):
        """S-d, clause 8: an index path whose scope name is not UTF-8."""
        blob = self.git("hash-object", "-w", "--stdin", stdin=b"claude\n").strip()
        subprocess.run([b"git", b"-C", os.fsencode(self.root), b"update-index", b"--add",
                        b"--cacheinfo", b"100644," + blob.encode() + b",caf\xe9/CLAUDE.md"],
                       check=True, capture_output=True)
        self.commit()
        code, payload, err = self.cli("--dry-run")
        self.assertNotIn("Traceback", err)
        self.assertIsNotNone(payload, err)
        paths = [b["path"] for sp in payload["scopes"] for b in sp["index_blobs"]]
        self.assertEqual(paths, ["caf\\xe9/CLAUDE.md"])
        self.assertEqual(code, 1)

    def test_a_scope_reached_through_a_symlink_refuses_alone(self):
        """S-g: git fails on a path beyond a link, which stopped every scope."""
        self.put("CLAUDE.md", b"root\n", track=True)
        self.put("sub/CLAUDE.md", b"sub\n", track=True)
        self.commit()
        shutil.rmtree(self.root / "sub")
        (self.root / "real").mkdir()
        (self.root / "real" / "CLAUDE.md").write_bytes(b"real\n")
        os.symlink("real", self.root / "sub")
        code, payload, err = self.cli("--migrate")
        self.assertIsNotNone(payload, err)
        rows = [r for r in payload["refusals"] if r["scope"] == "sub"]
        self.assertEqual(rows[0]["next_step"], im.NEXT_LINK_DIR)
        self.assertEqual((self.root / "real" / "CLAUDE.md").read_bytes(), b"real\n")
        self.assertEqual((self.root / "AGENTS.md").read_bytes(), b"root\n")
        self.assertEqual(code, 1)

if __name__ == "__main__":
    unittest.main()


class ReviewRoundTwoTests(GitRepoCase):
    """Review fix round 2 of PB-0133: a rerun never drops a staged-only blob, and a
    rerun whose index differs from what an uninterrupted run stages is a finding."""

    def index_blob(self, path: str) -> str | None:
        for line in self.git("ls-files", "-s").splitlines():
            meta, _, name = line.partition("\t")
            if name == path:
                return meta.split()[1]
        return None

    def blob_of(self, data: bytes) -> str:
        return self.git("hash-object", "--stdin", stdin=data).strip()

    def build_staged_only_variant(self, commit_b0: bool = False):
        """`agents.md` staged holding B0, then edited to D: B0 is index-only
        unless `commit_b0` puts it in HEAD."""
        self.put("README", b"base\n", track=True)
        self.put("agents.md", b"B0\n", track=True)
        if commit_b0:
            self.commit()
        else:
            self.git("rm", "-q", "--cached", "--", "agents.md")
            self.commit()
            self.git("add", "--", "agents.md")
        self.put("agents.md", b"D\n")

    def assert_b0_kept(self, payload, label: str):
        scope = payload["scopes"][0]
        self.assertEqual(self.index_blob("agents.md"), self.blob_of(b"B0\n"), label)
        self.assertEqual([r["path"] for r in scope["index_only_blobs"]], ["agents.md"],
                         (label, scope))
        self.assertNotIn("index", [s["kind"] for s in scope["steps"]], (label, scope))

    def test_a_rerun_after_a_respell_keeps_a_staged_only_blob(self):
        """MF-1: run 1 respells and keeps B0 in the index; runs 2 and 3 keep it too,
        name it, and exit 1, because the scope stages nothing."""
        self.build_staged_only_variant()
        b0 = self.blob_of(b"B0\n")
        code, payload, err = self.cli("--migrate")
        self.assertEqual(code, 1, (payload, err))
        self.assertIn("AGENTS.md", self.listing())
        self.assert_b0_kept(payload, "run 1")
        state = self.files(), self.git("ls-files", "-s")
        for label in ("run 2", "run 3"):
            code, payload, err = self.cli("--migrate")
            self.assertEqual(code, 1, (label, payload, err))
            self.assert_b0_kept(payload, label)
            self.assertEqual((self.files(), self.git("ls-files", "-s")), state, label)
        self.assertEqual(self.git("cat-file", "-p", b0), "B0\n")

    def test_a_rerun_through_the_case_sensitive_seam_keeps_a_staged_only_blob(self):
        """MF-1, receipt adoption: on a case-sensitive volume the rerun adopts the
        old spelling's index path from the receipt row, and still keeps B0."""
        self.git("config", "core.ignorecase", "false")
        self.build_staged_only_variant()
        fs = CaseSensitiveFS()
        plan = im.build_plan(im.discover(self.root, denylist=[]), fs=fs)
        self.assertEqual([s.kind for s in plan.actions], ["respell"])
        im.apply_plan(plan, self.root)
        self.assertIn("AGENTS.md", self.listing())
        for label in ("run 2", "run 3"):
            plan = im.build_plan(im.discover(self.root, denylist=[]), fs=fs)
            scope = plan.scopes[0]
            self.assertEqual(scope.to_json()["index_only_blobs"][0]["path"], "agents.md",
                             label)
            self.assertEqual([s.kind for s in plan.actions], [], label)
            self.assertTrue(plan.findings(), label)
            im.apply_plan(plan, self.root)
            self.assertEqual(self.index_blob("agents.md"), self.blob_of(b"B0\n"), label)

    def test_positive_control_a_rerun_with_b0_in_head_removes_the_old_path(self):
        """The same rerun where HEAD holds B0 runs the index step and removes the
        old spelling, so the assertions above can see a removal."""
        self.build_staged_only_variant(commit_b0=True)
        seen = []

        def hook(scope, kind):
            seen.append(kind)
            if kind == "respell":
                raise KeyboardInterrupt("stopped after the respell")

        plan = im.build_plan(im.discover(self.root, denylist=[]))
        self.assertEqual([s.kind for s in plan.actions], ["respell", "index"])
        with self.assertRaises(KeyboardInterrupt):
            im.apply_plan(plan, self.root, hook=hook)
        self.assertEqual(seen, ["respell"])
        self.assertEqual(self.index_blob("agents.md"), self.blob_of(b"B0\n"))
        code, payload, err = self.cli("--migrate")
        self.assertEqual(code, 0, (payload, err))
        self.assertIsNone(self.index_blob("agents.md"))
        self.assertEqual(self.index_blob("AGENTS.md"), self.blob_of(b"D\n"))

    def stop_dirty_loser_create_at_the_index(self):
        self.put("CLAUDE.md", b"# C\n@AGENTS.md\n", track=True)
        self.put("AGENTS.md", b"# A\n", track=True)
        self.commit()
        self.put("AGENTS.md", b"# A edited\n")
        lock = self.root / ".git" / "index.lock"
        lock.write_bytes(b"")
        code, payload, err = self.cli("--migrate")
        self.assertEqual(code, 1, (payload, err))
        self.assertIn("git refused the index change", payload["scopes"][0]["stopped"])
        lock.unlink()

    def test_a_rerun_without_the_receipt_rows_is_a_finding(self):
        """SC-1: with the receipt deleted, or its hashes zeroed, the rerun cannot
        stage the tracked loser's dirty bytes; the index still differs from what an
        uninterrupted run stages, so the rerun is a finding and names the path."""
        for mode in ("deleted", "zeroed"):
            with self.subTest(mode=mode):
                shutil.rmtree(self.root)
                self.setUp()
                self.stop_dirty_loser_create_at_the_index()
                receipt = self.root / im.RECEIPT_NAME
                if mode == "deleted":
                    receipt.unlink()
                else:
                    data = json.loads(receipt.read_bytes())
                    for row in data["resume"].values():
                        for source in row.values():
                            source["sha256"] = "0" * 64
                    receipt.write_text(json.dumps(data))
                code, payload, err = self.cli("--migrate")
                self.assertEqual(code, 1, (payload, err))
                scope = payload["scopes"][0]
                self.assertEqual([m["path"] for m in scope["missing_index_paths"]],
                                 ["CLAUDE.md"], scope)
                reasons = " ".join(scope["stages_nothing_because"])
                self.assertNotIn("untracked entry AGENTS.md.crux-set-aside", reasons)
                self.assertIn("set-aside AGENTS.md.crux-set-aside-", reasons)

    def test_control_the_rerun_with_the_receipt_converges_clean(self):
        """With the receipt rows intact the same rerun stages and exits 0, so the
        finding above comes from the missing rows, not from the stop."""
        self.stop_dirty_loser_create_at_the_index()
        code, payload, err = self.cli("--migrate")
        self.assertEqual(code, 0, (payload, err))
        self.assertEqual(payload["scopes"][0]["missing_index_paths"], [])
        self.assertIsNone(self.index_blob("CLAUDE.md"))
