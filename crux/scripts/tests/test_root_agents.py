"""Tests for root_agents.py — init-docs step 9's file work on the repo-root AGENTS.md.

Written before the implementation. The helper does what skill prose cannot
guarantee: it classifies the root from directory entries, writes under
O_EXCL / O_NOFOLLOW, and deletes only the file this run created.

Every absence assertion carries a positive control that shows the fixture would
have produced the bad behaviour without the guard (a plain `open` follows the
link; a `casefold` copy disagrees on a lookalike name).
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = REPO_ROOT / "crux" / "scripts"
SKILL_MD = REPO_ROOT / "crux" / "skills" / "init-docs" / "SKILL.md"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


im = _load("instruction_migration", "instruction_migration.py")
ra = _load("root_agents", "root_agents.py")

POINTER = "See `bionic/AGENTS.md` for documentation operations."


def _run(*argv: str) -> tuple[int, dict, str]:
    """Drive the CLI entry point in-process; return (exit code, JSON, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = ra.main(list(argv))
    text = out.getvalue()
    return code, (json.loads(text) if text.strip() else {}), err.getvalue()


def E(name: str, kind: str = "regular", text_state: str = "ok"):
    return ra.Entry(name, kind, text_state)


def _codes(decision) -> dict[str, str]:
    return {r.entry: r.code for r in decision.reports}


class TmpRoot(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()


# --------------------------------------------------------------------- A1: names


class NameParityTests(unittest.TestCase):
    """The helper classifies names as instruction_migration does."""

    #: name -> the role the helper assigns it for the root state.
    EXPECTED = {
        "AGENTS.md": "agents-exact",
        "agents.md": "agents-variant",
        "Agents.MD": "agents-variant",
        "CLAUDE.md": "claude",
        "claude.md": "claude",
        "Claude.md": "claude",
        "CLAUDE.local.md": None,          # a personal override never blocks or is reported
        "AGENTſ.md": None,                # long s: lower() keeps it, casefold() folds it to s
        "AGENTS.md.bak": None,
    }

    def test_role_table(self):
        for name, role in self.EXPECTED.items():
            with self.subTest(name=name):
                self.assertEqual(ra.root_role(name), role)

    def test_agrees_with_instruction_migration_on_every_name(self):
        """A name is a root instruction name exactly when the migration says so."""
        for name in self.EXPECTED:
            with self.subTest(name=name):
                migration = im.is_instruction_name(name) and name.lower() != "claude.local.md"
                self.assertEqual(ra.root_role(name) is not None, migration)

    def test_positive_control_casefold_and_equality_disagree(self):
        """A copy that folds case, or compares exactly, gets a row wrong.

        This is what makes the parity test above able to fail.
        """
        def folded(name: str) -> bool:
            return name.casefold() in {"agents.md", "claude.md"}

        def exact(name: str) -> bool:
            return name in {"AGENTS.md", "CLAUDE.md"}

        wrong_fold = [n for n, r in self.EXPECTED.items() if folded(n) != (r is not None)]
        wrong_exact = [n for n, r in self.EXPECTED.items() if exact(n) != (r is not None)]
        self.assertIn("AGENTſ.md", wrong_fold)
        self.assertIn("agents.md", wrong_exact)


# ----------------------------------------------------------- A1: the state table


class RootStateTableTests(unittest.TestCase):
    """One case per row of the decision's root-state table, then the compositions."""

    def decide(self, entries, tracked=None, denylist=()):
        return ra.decide(entries, tracked=tracked, denylist=frozenset(denylist))

    def test_row1_no_instruction_entry_creates(self):
        d = self.decide([E("README.md"), E("CLAUDE.local.md"), E(".claude", "directory")],
                        tracked=frozenset())
        self.assertEqual((d.action, d.reports), ("create", []))

    def test_row2_exact_regular_utf8_appends_and_reports_nothing(self):
        d = self.decide([E("AGENTS.md")], tracked=frozenset({"AGENTS.md"}))
        self.assertEqual((d.action, d.reports), ("append", []))

    def test_row3_exact_with_claude_appends_and_reports_claude(self):
        d = self.decide([E("AGENTS.md"), E("CLAUDE.md")],
                        tracked=frozenset({"AGENTS.md", "CLAUDE.md"}))
        self.assertEqual(d.action, "append")
        self.assertEqual(_codes(d), {"CLAUDE.md": "migrate-set-aside"})

    def test_row4_exact_with_variant_appends_and_reports_the_ambiguous_family(self):
        d = self.decide([E("AGENTS.md"), E("agents.md")],
                        tracked=frozenset({"AGENTS.md", "agents.md"}))
        self.assertEqual(d.action, "append")
        self.assertEqual(_codes(d), {"agents.md": "resolve-family-then-migrate"})

    def test_row5_claude_alone_writes_nothing(self):
        d = self.decide([E("CLAUDE.md")], tracked=frozenset({"CLAUDE.md"}))
        self.assertEqual((d.action, _codes(d)), ("none", {"CLAUDE.md": "migrate"}))

    def test_row6_variant_alone_writes_nothing(self):
        d = self.decide([E("Agents.md")], tracked=frozenset({"Agents.md"}))
        self.assertEqual((d.action, _codes(d)), ("none", {"Agents.md": "migrate"}))

    def test_row7_two_variants_are_both_reported_with_the_family_remedy(self):
        d = self.decide([E("agents.md"), E("Agents.md")],
                        tracked=frozenset({"agents.md", "Agents.md"}))
        self.assertEqual(d.action, "none")
        self.assertEqual(_codes(d), {"agents.md": "resolve-family-then-migrate",
                                     "Agents.md": "resolve-family-then-migrate"})

    def test_row8_legacy_directory_writes_nothing_and_migration_does_not_apply(self):
        for name in ("CLAUDE.md", "agents.md"):
            with self.subTest(name=name):
                d = self.decide([E(name, "directory")], tracked=frozenset())
                self.assertEqual((d.action, _codes(d)),
                                 ("none", {name: "not-applicable:not-regular-file"}))

    def test_row9_exact_symlink_or_non_regular_writes_nothing(self):
        for kind in ("symlink", "directory", "other"):
            with self.subTest(kind=kind):
                d = self.decide([E("AGENTS.md", kind)])
                self.assertEqual((d.action, _codes(d)),
                                 ("none", {"AGENTS.md": "replace-with-regular-file"}))

    def test_row10_exact_unreadable_or_not_utf8_writes_nothing(self):
        for state, code in (("unreadable", "make-readable-or-reencode"),
                            ("not-utf8", "make-readable-or-reencode")):
            with self.subTest(state=state):
                d = self.decide([E("AGENTS.md", "regular", state)])
                self.assertEqual((d.action, _codes(d)), ("none", {"AGENTS.md": code}))

    # -- the compositions the decision spells out ----------------------------

    def test_claude_beside_a_variant_with_no_exact_writes_nothing_and_reports_both(self):
        # CLAUDE.md wins and the variant is set aside, in every state git can see.
        cases = {
            "both tracked": (frozenset({"CLAUDE.md", "agents.md"}),
                             "migrate-set-aside", "migrate-set-aside"),
            "CLAUDE.md tracked": (frozenset({"CLAUDE.md"}),
                                  "migrate-set-aside", "migrate-set-aside"),
            "variant tracked": (frozenset({"agents.md"}),
                                "migrate-set-aside", "migrate-set-aside"),
            "neither tracked": (frozenset(), "track-then-migrate", "track-then-migrate"),
            "no git": (None, "not-applicable:no-git", "not-applicable:no-git"),
        }
        for label, (tracked, claude, variant) in cases.items():
            with self.subTest(label):
                d = self.decide([E("CLAUDE.md"), E("agents.md")], tracked=tracked)
                self.assertEqual(d.action, "none")
                self.assertEqual(_codes(d), {"CLAUDE.md": claude, "agents.md": variant})

    def test_exact_symlink_beside_claude_writes_nothing_and_reports_both(self):
        # The migration refuses a linked loser, so the link is resolved first.
        d = self.decide([E("AGENTS.md", "symlink"), E("CLAUDE.md")],
                        tracked=frozenset({"CLAUDE.md"}))
        self.assertEqual(d.action, "none")
        self.assertEqual(_codes(d), {"AGENTS.md": "replace-with-regular-file",
                                     "CLAUDE.md": "fix-agents-then-migrate"})

    def test_exact_directory_beside_claude_keeps_the_fix_agents_remedy(self):
        d = self.decide([E("AGENTS.md", "directory"), E("CLAUDE.md")],
                        tracked=frozenset({"CLAUDE.md"}))
        self.assertEqual(_codes(d), {"AGENTS.md": "replace-with-regular-file",
                                     "CLAUDE.md": "fix-agents-then-migrate"})

    def test_an_entrys_own_state_comes_before_its_partner(self):
        """The migration never writes over a partner, so an entry's own step is safe first."""
        cases = {
            "claude family beside an untracked AGENTS.md": (
                [E("CLAUDE.md"), E("claude.md"), E("AGENTS.md")],
                frozenset({"CLAUDE.md", "claude.md"}),
                {"CLAUDE.md": "resolve-family-then-migrate",
                 "claude.md": "resolve-family-then-migrate"}),
            "linked CLAUDE.md beside an untracked AGENTS.md": (
                [E("CLAUDE.md", "symlink"), E("AGENTS.md")], frozenset({"CLAUDE.md"}),
                {"CLAUDE.md": "not-applicable:symlink"}),
            "no git, CLAUDE.md beside AGENTS.md": (
                [E("CLAUDE.md"), E("AGENTS.md")], None,
                {"CLAUDE.md": "not-applicable:no-git"}),
            "linked variant beside an untracked AGENTS.md": (
                [E("agents.md", "symlink"), E("AGENTS.md")], frozenset({"agents.md"}),
                {"agents.md": "not-applicable:symlink"}),
            "variant beside a linked AGENTS.md": (
                [E("agents.md"), E("AGENTS.md", "symlink")], frozenset({"agents.md"}),
                {"AGENTS.md": "replace-with-regular-file",
                 "agents.md": "fix-agents-then-migrate"}),
        }
        for label, (entries, tracked, want) in cases.items():
            with self.subTest(label):
                self.assertEqual(_codes(self.decide(entries, tracked)), want)

    def test_a_partner_the_migration_refuses_is_resolved_first(self):
        cases = {
            "denylisted AGENTS.md beside CLAUDE.md": (
                [E("CLAUDE.md"), E("AGENTS.md")], frozenset({"CLAUDE.md", "AGENTS.md"}),
                ("AGENTS.md",), {"CLAUDE.md": "resolve-partner-then-migrate"}),
            "CLAUDE.md beside a directory named agents.md": (
                [E("CLAUDE.md"), E("agents.md", "directory")], frozenset({"CLAUDE.md"}),
                (), {"CLAUDE.md": "resolve-partner-then-migrate",
                     "agents.md": "not-applicable:not-regular-file"}),
            "variant beside a directory named CLAUDE.md": (
                [E("CLAUDE.md", "directory"), E("agents.md")], frozenset({"agents.md"}),
                (), {"CLAUDE.md": "not-applicable:not-regular-file",
                     "agents.md": "resolve-partner-then-migrate"}),
            "a variant beside two spellings of CLAUDE.md": (
                [E("CLAUDE.md"), E("Claude.md"), E("agents.md")],
                frozenset({"CLAUDE.md", "Claude.md", "agents.md"}), (),
                {"CLAUDE.md": "resolve-family-then-migrate",
                 "Claude.md": "resolve-family-then-migrate",
                 "agents.md": "resolve-partner-then-migrate"}),
            "CLAUDE.md beside two spellings of AGENTS.md": (
                [E("CLAUDE.md"), E("agents.md"), E("AGENTS.md")],
                frozenset({"CLAUDE.md", "agents.md", "AGENTS.md"}), (),
                {"CLAUDE.md": "resolve-partner-then-migrate",
                 "agents.md": "resolve-family-then-migrate"}),
        }
        for label, (entries, tracked, deny, want) in cases.items():
            with self.subTest(label):
                self.assertEqual(_codes(self.decide(entries, tracked, deny)), want)

    def test_control_partners_the_migration_reads_keep_the_set_aside_code(self):
        cases = {
            "linked CLAUDE.md beside a tracked AGENTS.md": (
                [E("CLAUDE.md", "symlink"), E("AGENTS.md")],
                frozenset({"CLAUDE.md", "AGENTS.md"}), (),
                {"CLAUDE.md": "not-applicable:symlink"}),
            "no git, CLAUDE.md alone": ([E("CLAUDE.md")], None, (),
                                        {"CLAUDE.md": "not-applicable:no-git"}),
            "untracked AGENTS.md beside a tracked CLAUDE.md": (
                [E("CLAUDE.md"), E("AGENTS.md")], frozenset({"CLAUDE.md"}), (),
                {"CLAUDE.md": "migrate-set-aside"}),
            "a variant beside a linked CLAUDE.md is respelled, not set aside": (
                [E("CLAUDE.md", "symlink"), E("agents.md")], frozenset({"agents.md"}), (),
                {"CLAUDE.md": "not-applicable:symlink", "agents.md": "migrate"}),
            "a variant beside a listed CLAUDE.md is respelled, not set aside": (
                [E("CLAUDE.md"), E("agents.md")], frozenset({"CLAUDE.md", "agents.md"}),
                ("CLAUDE.md",),
                {"CLAUDE.md": "unlist-then-migrate", "agents.md": "migrate"}),
        }
        for label, (entries, tracked, deny, want) in cases.items():
            with self.subTest(label):
                self.assertEqual(_codes(self.decide(entries, tracked, deny)), want)

    def test_directory_row_limits_only_that_entry(self):
        """An exact regular AGENTS.md beside a legacy directory still gets its append."""
        d = self.decide([E("AGENTS.md"), E("CLAUDE.md", "directory")], tracked=frozenset())
        self.assertEqual(d.action, "append")
        self.assertEqual(_codes(d), {"CLAUDE.md": "not-applicable:not-regular-file"})

    # -- remedy codes: every one the report can carry -------------------------

    def test_remedy_codes_follow_what_the_migration_plan_converts(self):
        cases = {
            "untracked": ([E("CLAUDE.md")], frozenset(), (), "track-then-migrate"),
            "denylisted": ([E("CLAUDE.md")], frozenset({"CLAUDE.md"}), ("CLAUDE.md",),
                           "unlist-then-migrate"),
            "both": ([E("CLAUDE.md")], frozenset(), ("CLAUDE.md",),
                     "track-and-unlist-then-migrate"),
            "symlink": ([E("CLAUDE.md", "symlink")], frozenset({"CLAUDE.md"}), (),
                        "not-applicable:symlink"),
            "no git": ([E("CLAUDE.md")], None, (), "not-applicable:no-git"),
            "tracked": ([E("CLAUDE.md")], frozenset({"CLAUDE.md"}), (), "migrate"),
        }
        for label, (entries, tracked, deny, code) in cases.items():
            with self.subTest(label):
                self.assertEqual(_codes(self.decide(entries, tracked, deny)),
                                 {"CLAUDE.md": code})

    def test_report_never_names_a_code_outside_the_closed_set(self):
        seen = set()
        for entries, tracked in (
                ([E("CLAUDE.md"), E("agents.md")], frozenset({"CLAUDE.md", "agents.md"})),
                ([E("AGENTS.md", "symlink"), E("CLAUDE.md")], frozenset({"CLAUDE.md"})),
                ([E("CLAUDE.md")], None), ([E("CLAUDE.md", "symlink")], frozenset()),
                ([E("AGENTS.md", "regular", "not-utf8")], None)):
            seen |= {r.code for r in self.decide(entries, tracked).reports}
        self.assertTrue(seen)
        self.assertLessEqual(seen, ra.REMEDY_CODES)


# ------------------------------------------------------------ A1: the real root


class InspectRootTests(TmpRoot):
    def test_inspect_reports_kinds_and_text_state(self):
        (self.root / "AGENTS.md").write_bytes(b"\xff\xfe not utf8")
        (self.root / "target").write_text("x")
        os.symlink("target", self.root / "CLAUDE.md")
        os.symlink("missing", self.root / "claude.md.dangling")
        (self.root / "somedir").mkdir()
        found = {e.name: (e.kind, e.text_state) for e in ra.inspect_root(self.root)}
        self.assertEqual(found["AGENTS.md"], ("regular", "not-utf8"))
        self.assertEqual(found["CLAUDE.md"][0], "symlink")
        self.assertEqual(found["claude.md.dangling"][0], "symlink")
        self.assertEqual(found["somedir"][0], "directory")

    def test_a_regular_utf8_file_reads_ok(self):
        (self.root / "AGENTS.md").write_text("héllo\n", encoding="utf-8")
        entry = {e.name: e for e in ra.inspect_root(self.root)}["AGENTS.md"]
        self.assertEqual((entry.kind, entry.text_state), ("regular", "ok"))

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root reads any file")
    def test_an_unreadable_file_is_flagged(self):
        path = self.root / "AGENTS.md"
        path.write_text("x")
        path.chmod(0)
        self.addCleanup(path.chmod, 0o644)
        entry = {e.name: e for e in ra.inspect_root(self.root)}["AGENTS.md"]
        self.assertEqual(entry.text_state, "unreadable")


# --------------------------------------------------------------- A2: blocks parse


class BlocksTests(unittest.TestCase):
    def test_blocks_come_from_step_9_of_the_shipped_skill(self):
        blocks = ra.load_blocks(SKILL_MD, "bionic")
        self.assertEqual(blocks.pointer, POINTER)
        self.assertTrue(blocks.objectives.startswith(
            "Read `bionic/objectives.md` before work of any size"))
        self.assertIn("\n\n[^objectives]: rule:objectives-read-before-work", blocks.objectives)
        self.assertNotIn("${DOCS_DIR}", blocks.pointer + blocks.objectives)

    def test_docs_dir_is_substituted(self):
        blocks = ra.load_blocks(SKILL_MD, "notes")
        self.assertEqual(blocks.pointer, "See `notes/AGENTS.md` for documentation operations.")
        self.assertIn("`notes/objectives.md`", blocks.objectives)

    def test_a_mangled_fence_is_refused(self):
        text = SKILL_MD.read_text(encoding="utf-8")
        start = text.index("### 9. ")
        for label, mutated in (
                ("no markdown fence",
                 text[:start] + text[start:].replace("```markdown", "```md", 1)),
                ("three markdown fences",
                 text[:start] + text[start:].replace(
                     "### 10.", "```markdown\nextra\n```\n\n### 10.", 1)),
                ("no step 9", text.replace("### 9. ", "### 9x. ", 1))):
            with self.subTest(label):
                self.assertNotEqual(mutated, text)
                with tempfile.TemporaryDirectory() as tmp:
                    bad = Path(tmp) / "SKILL.md"
                    bad.write_text(mutated, encoding="utf-8")
                    with self.assertRaises(ra.CapabilityError):
                        ra.load_blocks(bad, "bionic")


# ------------------------------------------------------------ A2: create + append


class CreateTests(TmpRoot):
    def setUp(self):
        super().setUp()
        self.blocks = ra.load_blocks(SKILL_MD, "bionic")
        self.expected = (self.blocks.pointer + "\n\n" + self.blocks.objectives + "\n").encode()

    def test_created_bytes_are_the_two_blocks_and_nothing_else(self):
        record = ra.create_exclusive(self.root, self.blocks)
        data = (self.root / "AGENTS.md").read_bytes()
        self.assertEqual(data, self.expected)
        self.assertFalse(data.startswith((b"\n", b"#")))
        self.assertTrue(data.endswith(b"\n") and not data.endswith(b"\n\n"))
        self.assertNotIn(b"\r", data)
        self.assertEqual(record["sha256"], hashlib.sha256(data).hexdigest())

    def test_a_rerun_of_the_append_conditions_changes_nothing(self):
        """The postcondition: append conditions run on the created file append nothing."""
        ra.create_exclusive(self.root, self.blocks)
        before = (self.root / "AGENTS.md").read_bytes()
        outcome = ra.append_nofollow(self.root, self.blocks, "bionic")
        self.assertEqual(outcome["outcome"], "unchanged")
        self.assertEqual((self.root / "AGENTS.md").read_bytes(), before)

    def test_an_existing_entry_is_never_replaced(self):
        (self.root / "AGENTS.md").write_bytes(b"mine\n")
        with self.assertRaises(FileExistsError):
            ra.create_exclusive(self.root, self.blocks)
        self.assertEqual((self.root / "AGENTS.md").read_bytes(), b"mine\n")

    def test_dangling_and_live_symlinks_are_not_followed_by_create(self):
        (self.root / "victim").write_text("keep")
        for label, target in (("dangling", "missing-target"), ("live", "victim")):
            with self.subTest(label):
                link = self.root / "AGENTS.md"
                if link.is_symlink():
                    link.unlink()
                os.symlink(target, link)
                with self.assertRaises(FileExistsError):
                    ra.create_exclusive(self.root, self.blocks)
                self.assertFalse((self.root / "missing-target").exists())
                self.assertEqual((self.root / "victim").read_text(), "keep")

    def test_positive_control_a_plain_open_would_have_followed_the_link(self):
        os.symlink("missing-target", self.root / "AGENTS.md")
        with open(self.root / "AGENTS.md", "w") as fh:  # what an unguarded write does
            fh.write("x")
        self.assertTrue((self.root / "missing-target").exists())


class AppendTests(TmpRoot):
    def setUp(self):
        super().setUp()
        self.blocks = ra.load_blocks(SKILL_MD, "bionic")

    def append(self):
        return ra.append_nofollow(self.root, self.blocks, "bionic")

    def test_appends_each_missing_block_and_keeps_existing_bytes(self):
        original = "# Mine\n\nuse tabs\n"
        (self.root / "AGENTS.md").write_text(original, encoding="utf-8")
        outcome = self.append()
        self.assertEqual(outcome["wrote"], ["pointer", "objectives"])
        data = (self.root / "AGENTS.md").read_text(encoding="utf-8")
        self.assertEqual(data, original + "\n" + self.blocks.pointer + "\n\n"
                         + self.blocks.objectives + "\n")

    def test_adds_the_missing_newline_before_the_separator(self):
        (self.root / "AGENTS.md").write_text("no trailing newline", encoding="utf-8")
        self.append()
        data = (self.root / "AGENTS.md").read_text(encoding="utf-8")
        self.assertTrue(data.startswith("no trailing newline\n\n" + self.blocks.pointer))

    def test_pointer_only_when_it_does_not_reference_the_tree(self):
        (self.root / "AGENTS.md").write_text(
            f"x\n\n{self.blocks.pointer}\n", encoding="utf-8")
        outcome = self.append()
        self.assertEqual(outcome["wrote"], ["objectives"])

    def test_objectives_only_when_it_does_not_name_objectives_md(self):
        (self.root / "AGENTS.md").write_text("read bionic/objectives.md first\n",
                                             encoding="utf-8")
        outcome = self.append()
        self.assertEqual(outcome["wrote"], ["pointer"])

    def test_second_append_is_a_noop(self):
        (self.root / "AGENTS.md").write_text("# Mine\n", encoding="utf-8")
        self.append()
        before = (self.root / "AGENTS.md").read_bytes()
        self.assertEqual(self.append()["outcome"], "unchanged")
        self.assertEqual((self.root / "AGENTS.md").read_bytes(), before)

    def test_a_link_is_refused_and_its_target_is_untouched(self):
        (self.root / "victim").write_text("keep\n")
        for label, target in (("live", "victim"), ("dangling", "missing")):
            with self.subTest(label):
                link = self.root / "AGENTS.md"
                if link.is_symlink():
                    link.unlink()
                os.symlink(target, link)
                outcome = self.append()
                self.assertEqual(outcome["outcome"], "refused")
                self.assertEqual(outcome["code"], "replace-with-regular-file")
                self.assertEqual((self.root / "victim").read_text(), "keep\n")
                self.assertFalse((self.root / "missing").exists())

    def test_positive_control_a_plain_append_open_follows_the_link(self):
        (self.root / "victim").write_text("keep\n")
        os.symlink("victim", self.root / "AGENTS.md")
        with open(self.root / "AGENTS.md", "a") as fh:
            fh.write("leak\n")
        self.assertEqual((self.root / "victim").read_text(), "keep\nleak\n")

    def test_a_non_utf8_file_is_refused_byte_identical(self):
        payload = b"\xff\xfe\x00 not utf8\n"
        (self.root / "AGENTS.md").write_bytes(payload)
        outcome = self.append()
        self.assertEqual((outcome["outcome"], outcome["code"]),
                         ("refused", "make-readable-or-reencode"))
        self.assertEqual((self.root / "AGENTS.md").read_bytes(), payload)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root reads any file")
    def test_an_unreadable_file_is_refused(self):
        path = self.root / "AGENTS.md"
        path.write_text("x\n")
        path.chmod(0)
        self.addCleanup(path.chmod, 0o644)
        outcome = self.append()
        self.assertEqual((outcome["outcome"], outcome["code"]),
                         ("refused", "make-readable-or-reencode"))
        path.chmod(0o644)
        self.assertEqual(path.read_text(), "x\n")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "needs mkfifo")
    def test_a_named_pipe_is_refused_by_the_fstat_check(self):
        """O_NONBLOCK lets the open succeed on a pipe; only fstat refuses it."""
        os.mkfifo(self.root / "AGENTS.md")
        outcome = self.append()
        self.assertEqual((outcome["outcome"], outcome["code"]),
                         ("refused", "replace-with-regular-file"))

    def test_a_directory_is_refused(self):
        (self.root / "AGENTS.md").mkdir()
        outcome = self.append()
        self.assertEqual((outcome["outcome"], outcome["code"]),
                         ("refused", "replace-with-regular-file"))


# --------------------------------------------------------------------- rollback


class RollbackTests(TmpRoot):
    def setUp(self):
        super().setUp()
        self.blocks = ra.load_blocks(SKILL_MD, "bionic")
        self.record = ra.create_exclusive(self.root, self.blocks)
        self.path = self.root / "AGENTS.md"

    def test_an_untouched_created_file_is_deleted(self):
        self.assertEqual(ra.rollback_created(self.record, self.root)["outcome"], "deleted")
        self.assertFalse(self.path.exists())

    def test_a_file_edited_in_place_is_kept(self):
        with open(self.path, "ab") as fh:
            fh.write(b"user text\n")
        result = ra.rollback_created(self.record, self.root)
        self.assertEqual((result["outcome"], result["reason"]), ("kept", "edited"))
        self.assertTrue(self.path.exists())

    def test_a_file_replaced_by_another_regular_file_is_kept(self):
        data = self.path.read_bytes()
        self.path.unlink()
        self.path.write_bytes(data)  # identical bytes, new inode
        result = ra.rollback_created(self.record, self.root)
        self.assertEqual((result["outcome"], result["reason"]), ("kept", "replaced"))
        self.assertEqual(self.path.read_bytes(), data)

    def test_a_file_replaced_by_a_symlink_is_kept_and_its_target_survives(self):
        (self.root / "victim").write_text("keep")
        self.path.unlink()
        os.symlink("victim", self.path)
        result = ra.rollback_created(self.record, self.root)
        self.assertEqual((result["outcome"], result["reason"]), ("kept", "not-regular"))
        self.assertTrue(self.path.is_symlink())
        self.assertEqual((self.root / "victim").read_text(), "keep")

    def test_a_reused_inode_with_identical_bytes_is_kept_by_the_mtime_check(self):
        """Same device, inode and hash, different write time: another file's inode reuse."""
        stale = dict(self.record, mtime_ns=self.record["mtime_ns"] - 10**9)
        result = ra.rollback_created(stale, self.root)
        self.assertEqual((result["outcome"], result["reason"]), ("kept", "replaced"))
        self.assertTrue(self.path.exists())
        self.assertEqual(ra.rollback_created(self.record, self.root)["outcome"], "deleted")  # control

    def test_an_already_absent_file_is_reported_not_an_error(self):
        self.path.unlink()
        self.assertEqual(ra.rollback_created(self.record, self.root)["outcome"], "absent")

    def test_positive_control_an_unguarded_delete_would_remove_the_edited_file(self):
        with open(self.path, "ab") as fh:
            fh.write(b"user text\n")
        self.path.unlink()  # what a path-only rollback does
        self.assertFalse(self.path.exists())


# -------------------------------------------------------------------------- CLI


class CliTests(TmpRoot):
    def args(self, verb, *more):
        return (verb, "--repo-root", str(self.root), "--docs-dir", "bionic", *more)

    def test_plan_on_an_empty_root_says_create_and_writes_nothing(self):
        code, out, _ = _run(*self.args("plan"))
        self.assertEqual((code, out["action"], out["reports"]), (0, "create", []))
        self.assertFalse((self.root / "AGENTS.md").exists())

    def test_apply_creates_then_a_second_apply_leaves_the_file_byte_identical(self):
        record = self.root.parent / (self.root.name + "-record.json")
        self.addCleanup(lambda: record.unlink(missing_ok=True))
        code, out, _ = _run(*self.args("apply", "--record", str(record)))
        self.assertEqual((code, out["outcome"]), (0, "created"))
        before = (self.root / "AGENTS.md").read_bytes()
        code2, out2, _ = _run(*self.args("apply"))
        self.assertEqual((code2, out2["action"], out2["outcome"]), (0, "append", "unchanged"))
        self.assertEqual((self.root / "AGENTS.md").read_bytes(), before)
        code3, out3, _ = _run("rollback", "--repo-root", str(self.root), "--record", str(record))
        self.assertEqual((code3, out3["outcome"]), (0, "deleted"))
        self.assertFalse((self.root / "AGENTS.md").exists())

    def test_a_legacy_claude_md_blocks_the_create_and_is_reported(self):
        (self.root / "CLAUDE.md").write_text("legacy\n")
        code, out, _ = _run(*self.args("apply"))
        self.assertEqual((code, out["action"], out["outcome"]), (1, "none", "none"))
        self.assertEqual([(r["entry"], r["code"]) for r in out["reports"]],
                         [("CLAUDE.md", "not-applicable:no-git")])
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["CLAUDE.md"])

    def test_git_state_drives_the_remedy_code(self):
        (self.root / "CLAUDE.md").write_text("legacy\n")
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        _, out, _ = _run(*self.args("plan"))
        self.assertEqual(out["reports"][0]["code"], "track-then-migrate")
        subprocess.run(["git", "-C", str(self.root), "add", "CLAUDE.md"], check=True)
        _, out, _ = _run(*self.args("plan"))
        self.assertEqual(out["reports"][0]["code"], "migrate")

    def test_an_entry_that_appears_between_inspection_and_write_is_left_alone(self):
        real = ra.inspect_root

        def inspect_then_race(root):
            entries = real(root)
            (Path(root) / "AGENTS.md").write_bytes(b"appeared\n")
            return entries

        ra.inspect_root = inspect_then_race
        self.addCleanup(setattr, ra, "inspect_root", real)
        code, out, _ = _run(*self.args("apply"))
        self.assertEqual((code, out["action"], out["outcome"]), (1, "create", "refused"))
        self.assertEqual(out["reports"][0]["code"], "appeared-during-run")
        self.assertEqual((self.root / "AGENTS.md").read_bytes(), b"appeared\n")

    def test_an_unwritable_record_exits_2_before_the_root_is_touched(self):
        """Exit 2 tells step 9 that nothing reached the root. The record is
        created before the root is touched, so an unwritable record stops the run
        there. A record write that fails after the create undoes the create; see
        RecordFileTests."""
        record = self.root.parent / (self.root.name + "-missing-dir") / "record.json"
        code, out, err = _run(*self.args("apply", "--record", str(record)))
        self.assertEqual((code, out), (2, {}))
        self.assertIn("rollback record", err)
        self.assertFalse((self.root / "AGENTS.md").exists(),
                         "exit 2 holds: nothing reached the root")

    def test_positive_control_the_same_record_path_is_unwritable(self):
        """Control: the record path above really fails to write, so the test is not
        passing on a record that was written."""
        record = self.root.parent / (self.root.name + "-missing-dir") / "record.json"
        with self.assertRaises(OSError):
            record.write_text("{}", encoding="utf-8")

    def test_a_refused_append_reports_the_entry_kind_it_found(self):
        """A link swapped in between inspection and the append is reported as the
        link it is, so the WARNING never tells the user to replace a regular file."""
        (self.root / "AGENTS.md").write_text("mine\n")
        (self.root / "victim").write_text("keep\n")
        real = ra.inspect_root

        def inspect_then_swap(root):
            entries = real(root)
            (Path(root) / "AGENTS.md").unlink()
            os.symlink("victim", Path(root) / "AGENTS.md")
            return entries

        ra.inspect_root = inspect_then_swap
        self.addCleanup(setattr, ra, "inspect_root", real)
        code, out, _ = _run(*self.args("apply"))
        self.assertEqual((code, out["action"], out["outcome"]), (1, "append", "refused"))
        self.assertEqual((out["reports"][0]["code"], out["reports"][0]["kind"]),
                         ("replace-with-regular-file", "symlink"))
        self.assertEqual((self.root / "victim").read_text(), "keep\n")

    def test_a_link_is_refused_end_to_end_and_the_target_is_untouched(self):
        (self.root / "victim").write_text("keep\n")
        os.symlink("victim", self.root / "AGENTS.md")
        code, out, _ = _run(*self.args("apply"))
        self.assertEqual((code, out["outcome"]), (1, "none"))
        self.assertEqual(out["reports"][0]["code"], "replace-with-regular-file")
        self.assertEqual((self.root / "victim").read_text(), "keep\n")

    def test_a_mangled_step_9_exits_2_and_writes_nothing(self):
        text = SKILL_MD.read_text(encoding="utf-8")
        start = text.index("### 9. ")
        bad = self.root.parent / (self.root.name + "-SKILL.md")
        self.addCleanup(lambda: bad.unlink(missing_ok=True))
        bad.write_text(text[:start] + text[start:].replace("```markdown", "```md", 1),
                       encoding="utf-8")
        code, _, err = _run(*self.args("apply", "--skill", str(bad)))
        self.assertEqual(code, 2)
        self.assertIn("fence", err)
        self.assertFalse((self.root / "AGENTS.md").exists())
        # POSITIVE CONTROL: the unmangled skill, same root, does create the file.
        code_ok, out, _ = _run(*self.args("apply"))
        self.assertEqual((code_ok, out["outcome"]), (0, "created"))

    def test_a_missing_root_exits_2(self):
        code, _, err = _run("plan", "--repo-root", str(self.root / "nope"),
                            "--docs-dir", "bionic")
        self.assertEqual(code, 2)
        self.assertIn("not a directory", err)

    def test_rollback_keeps_an_edited_file_and_exits_1(self):
        record = self.root.parent / (self.root.name + "-record.json")
        self.addCleanup(lambda: record.unlink(missing_ok=True))
        _run(*self.args("apply", "--record", str(record)))
        with open(self.root / "AGENTS.md", "ab") as fh:
            fh.write(b"edit\n")
        code, out, _ = _run("rollback", "--repo-root", str(self.root), "--record", str(record))
        self.assertEqual((code, out["outcome"], out["reason"]), (1, "kept", "edited"))
        self.assertTrue((self.root / "AGENTS.md").exists())

    def test_rollback_with_no_record_has_nothing_to_do(self):
        code, out, _ = _run("rollback", "--repo-root", str(self.root),
                               "--record", str(self.root / "absent.json"))
        self.assertEqual((code, out["outcome"]), (0, "nothing-recorded"))

    def test_the_script_runs_as_a_program_and_speaks_json(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "root_agents.py"), "plan",
             "--repo-root", str(self.root), "--docs-dir", "bionic"],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["action"], "create")

    def test_a_shell_metacharacter_docs_dir_is_written_as_text_only(self):
        code, out, _ = _run("apply", "--repo-root", str(self.root), "--docs-dir", "a b")
        self.assertEqual((code, out["outcome"]), (0, "created"))
        self.assertIn("See `a b/AGENTS.md`", (self.root / "AGENTS.md").read_text())



# ------------------------------------------------ review fix round 1: the record


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True,
                   capture_output=True, text=True)


class RecordFileTests(TmpRoot):
    """The rollback record binds one apply run to one repository.

    A stale record, reused by a later run or another repository, deleted a root
    AGENTS.md that run never created, and the record write followed a symlink.
    """

    def setUp(self):
        super().setUp()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.scratch = self.root / "scratch"
        self.scratch.mkdir()
        self.record = self.scratch / "record.json"

    def apply(self, repo=None, record=None, *more):
        return _run("apply", "--repo-root", str(repo or self.repo), "--docs-dir", "bionic",
                    "--record", str(record or self.record), *more)

    def rollback(self, repo=None, record=None):
        return _run("rollback", "--repo-root", str(repo or self.repo),
                    "--record", str(record or self.record))

    def test_an_existing_record_path_is_refused_before_the_root_is_touched(self):
        self.record.write_text("earlier run\n")
        code, out, err = self.apply()
        self.assertEqual((code, out), (2, {}))
        self.assertIn("already exists", err)
        self.assertEqual(os.listdir(self.repo), [])
        self.assertEqual(self.record.read_text(), "earlier run\n")

    def test_a_symlinked_record_path_is_refused_and_its_target_is_untouched(self):
        victim = self.scratch / "precious.txt"
        victim.write_text("keep\n")
        os.symlink(victim, self.record)
        code, _, _ = self.apply()
        self.assertEqual(code, 2)
        self.assertEqual(victim.read_text(), "keep\n")
        self.assertEqual(os.listdir(self.repo), [])
        # A dangling link is refused too, and its target is never created.
        dangling = self.scratch / "dangling.json"
        os.symlink(self.scratch / "nowhere.json", dangling)
        code2, _, _ = self.apply(record=dangling)
        self.assertEqual(code2, 2)
        self.assertFalse((self.scratch / "nowhere.json").exists())

    def test_positive_control_a_plain_write_follows_the_record_link(self):
        victim = self.scratch / "precious.txt"
        victim.write_text("keep\n")
        os.symlink(victim, self.record)
        self.record.write_text("{}", encoding="utf-8")
        self.assertEqual(victim.read_text(), "{}")

    def test_the_record_is_private_to_its_owner(self):
        code, out, _ = self.apply()
        self.assertEqual((code, out["outcome"]), (0, "created"))
        self.assertEqual(stat.S_IMODE(os.lstat(self.record).st_mode), 0o600)

    def test_the_record_names_the_absolute_path_for_a_relative_repo_root(self):
        here = os.getcwd()
        self.addCleanup(os.chdir, here)
        os.chdir(self.root)
        code, out, _ = _run("apply", "--repo-root", "repo", "--docs-dir", "bionic",
                            "--record", str(self.record))
        self.assertEqual((code, out["outcome"]), (0, "created"))
        recorded = json.loads(self.record.read_text())["path"]
        self.assertTrue(os.path.isabs(recorded), recorded)
        self.assertEqual(recorded, str(self.repo / "AGENTS.md"))
        os.chdir(self.scratch)  # a rollback from another directory still finds it
        code2, out2, _ = self.rollback()
        self.assertEqual((code2, out2["outcome"]), (0, "deleted"))
        self.assertFalse((self.repo / "AGENTS.md").exists())

    def test_a_rerun_with_the_same_record_is_refused_and_the_created_file_stays(self):
        code, out, _ = self.apply()
        self.assertEqual((code, out["outcome"]), (0, "created"))
        before = (self.repo / "AGENTS.md").read_bytes()
        code2, out2, err2 = self.apply()
        self.assertEqual((code2, out2), (2, {}))
        self.assertIn("already exists", err2)
        self.assertEqual((self.repo / "AGENTS.md").read_bytes(), before)

    def test_a_run_that_created_nothing_records_that_and_its_rollback_deletes_nothing(self):
        code, out, _ = self.apply()                      # run 1 creates
        self.assertEqual(out["outcome"], "created")
        second = self.scratch / "second.json"
        code2, out2, _ = self.apply(record=second)       # run 2 finds it and appends nothing
        self.assertEqual((code2, out2["outcome"]), (0, "unchanged"))
        self.assertEqual(json.loads(second.read_text()), {"created": False})
        code3, out3, _ = self.rollback(record=second)
        self.assertEqual((code3, out3["outcome"]), (0, "nothing-created"))
        self.assertTrue((self.repo / "AGENTS.md").exists())

    def test_a_record_from_another_repository_deletes_nothing_there(self):
        other = self.root / "other"
        other.mkdir()
        code, out, _ = self.apply()                      # repo gets AGENTS.md
        self.assertEqual(out["outcome"], "created")
        (other / "CLAUDE.md").write_text("legacy\n")
        code2, _, _ = self.apply(repo=other)             # same record path: refused
        self.assertEqual(code2, 2)
        code3, out3, err3 = self.rollback(repo=other)    # the record names repo, not other
        self.assertEqual((code3, out3), (2, {}))
        self.assertIn("names", err3)
        self.assertTrue((self.repo / "AGENTS.md").exists())
        # POSITIVE CONTROL: the same record, bound to its own repository, deletes.
        code4, out4, _ = self.rollback()
        self.assertEqual((code4, out4["outcome"]), (0, "deleted"))

    def test_a_record_naming_a_file_outside_the_root_is_refused(self):
        outside = self.scratch / "other.txt"
        outside.write_bytes(b"precious\n")
        st = os.lstat(outside)
        planted = {"created": True, "path": str(outside), "dev": st.st_dev,
                   "ino": st.st_ino, "mtime_ns": st.st_mtime_ns,
                   "sha256": hashlib.sha256(b"precious\n").hexdigest()}
        self.record.write_text(json.dumps(planted))
        code, out, err = self.rollback()
        self.assertEqual((code, out), (2, {}))
        self.assertIn("names", err)
        self.assertEqual(outside.read_bytes(), b"precious\n")
        # POSITIVE CONTROL: the same identity, taken by the guard alone, would delete.
        self.assertEqual(ra._matches_identity(Path(planted["path"]), planted), True)

    def test_an_unusable_record_exits_2_and_deletes_nothing(self):
        (self.repo / "AGENTS.md").write_text("mine\n")
        for label, body in (("not json", "{"), ("not an object", "[1]"),
                            ("empty", ""), ("missing keys", '{"created": true}')):
            with self.subTest(label):
                self.record.write_text(body)
                code, out, err = self.rollback()
                self.assertEqual((code, out), (2, {}))
                self.assertIn("unusable rollback record", err)
                self.assertEqual((self.repo / "AGENTS.md").read_text(), "mine\n")

    def test_a_record_write_that_fails_after_the_create_undoes_the_create(self):
        real = ra._write_record

        def boom(fd, record):
            raise OSError(28, "No space left on device")

        ra._write_record = boom
        self.addCleanup(setattr, ra, "_write_record", real)
        code, out, err = self.apply()
        self.assertEqual((code, out), (2, {}))
        self.assertIn("rollback record", err)
        self.assertIn("deleted", err)
        self.assertEqual(os.listdir(self.repo), [])


class UncoveredPathTests(TmpRoot):
    """Branches whose claims the report or the ADR makes, pinned end to end."""

    def args(self, verb, *more):
        return (verb, "--repo-root", str(self.root), "--docs-dir", "bionic", *more)

    def test_an_empty_agents_md_gets_the_blocks_with_no_leading_blank_line(self):
        (self.root / "AGENTS.md").write_bytes(b"")
        code, out, _ = _run(*self.args("apply"))
        self.assertEqual((code, out["outcome"]), (0, "appended"))
        blocks = ra.load_blocks(SKILL_MD, "bionic")
        self.assertEqual((self.root / "AGENTS.md").read_bytes(), ra.created_bytes(blocks))

    def test_an_append_that_fails_part_way_exits_2_and_says_so(self):
        (self.root / "AGENTS.md").write_text("mine\n")
        real = ra._write_all

        def partial(fd, data):
            os.write(fd, data[:5])
            raise OSError(28, "No space left on device")

        ra._write_all = partial
        self.addCleanup(setattr, ra, "_write_all", real)
        code, out, err = _run(*self.args("apply"))
        self.assertEqual((code, out), (2, {}))
        self.assertIn("failed after the write began", err)

    def test_a_create_that_fails_part_way_removes_the_half_written_file(self):
        real = ra._write_all

        def partial(fd, data):
            os.write(fd, data[:5])
            raise OSError(28, "No space left on device")

        ra._write_all = partial
        self.addCleanup(setattr, ra, "_write_all", real)
        code, _, _ = _run(*self.args("apply"))
        self.assertEqual(code, 2)
        self.assertEqual(os.listdir(self.root), [])

    def test_a_docs_dir_that_could_break_the_created_text_is_refused(self):
        for bad in ("a\nb", "a\rb", "a`b", ""):
            with self.subTest(repr(bad)):
                with self.assertRaises(ra.CapabilityError):
                    ra.load_blocks(SKILL_MD, bad)
        ra.load_blocks(SKILL_MD, "a b")  # control: a plain name is accepted

    def test_a_step_9_whose_fences_hold_the_wrong_blocks_is_refused(self):
        text = SKILL_MD.read_text(encoding="utf-8")
        start = text.index("### 9. ")
        pointer = "See `${DOCS_DIR}/AGENTS.md` for documentation operations."
        cases = {
            "two-line pointer": text[:start] + text[start:].replace(
                pointer, pointer + "\nsecond line", 1),
            "pointer without the tree": text[:start] + text[start:].replace(
                pointer, "See the docs.", 1),
            "objectives without the footnote": text[:start] + text[start:].replace(
                "\n\n[^objectives]: ", "\n[^objectives]: ", 1),
        }
        for label, mutated in cases.items():
            with self.subTest(label):
                self.assertNotEqual(mutated, text)
                with tempfile.TemporaryDirectory() as tmp:
                    bad = Path(tmp) / "SKILL.md"
                    bad.write_text(mutated, encoding="utf-8")
                    with self.assertRaises(ra.CapabilityError):
                        ra.load_blocks(bad, "bionic")

    def test_a_variant_on_disk_blocks_the_create(self):
        (self.root / "agents.md").write_text("variant\n")
        code, out, _ = _run(*self.args("apply"))
        self.assertEqual((code, out["action"], out["outcome"]), (1, "none", "none"))
        self.assertEqual([(r["entry"], r["code"]) for r in out["reports"]],
                         [("agents.md", "not-applicable:no-git")])
        self.assertEqual(os.listdir(self.root), ["agents.md"])
        self.assertEqual((self.root / "agents.md").read_text(), "variant\n")
        _git(self.root, "init", "-q")
        _git(self.root, "add", "agents.md")
        code2, out2, _ = _run(*self.args("apply"))
        self.assertEqual([(r["entry"], r["code"]) for r in out2["reports"]],
                         [("agents.md", "migrate")])
        self.assertEqual(sorted(os.listdir(self.root)), [".git", "agents.md"])

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root writes any file")
    def test_a_read_only_utf8_file_is_told_to_become_writable(self):
        path = self.root / "AGENTS.md"
        path.write_text("mine\n")
        path.chmod(0o444)
        self.addCleanup(path.chmod, 0o644)
        code, out, _ = _run(*self.args("apply"))
        self.assertEqual((code, out["outcome"]), (1, "refused"))
        self.assertEqual(out["reports"][0]["code"], "make-writable")
        self.assertEqual(path.read_text(), "mine\n")


# ----------------------------- review fix round 1: the remedy the migration earns


def _migration_plan(root: Path):
    """What audit-docs --migrate would do here, from the migration's own planner."""
    return im.build_plan(im.discover(root, denylist=im.load_denylist(root)))


class PartnerRemedyTests(TmpRoot):
    """A remedy that names the migration must be true of what the migration does.

    With a tracked CLAUDE.md beside an AGENTS.md-family entry, CLAUDE.md wins and
    the migration plans a set-aside of the other entry, whether git tracks it or
    not. Where it would refuse the root, the report must say so first.
    """

    def setUp(self):
        super().setUp()
        _git(self.root, "init", "-q")
        _git(self.root, "config", "user.email", "t@example.invalid")
        _git(self.root, "config", "user.name", "t")
        (self.root / "CLAUDE.md").write_text("claude rules\n")
        _git(self.root, "add", "CLAUDE.md")

    def codes(self):
        code, out, _ = _run("plan", "--repo-root", str(self.root), "--docs-dir", "bionic")
        return {r["entry"]: r["code"] for r in out["reports"]}

    def plan_steps(self):
        return [(s.kind, s.source) for s in _migration_plan(self.root).actions]

    def assert_plan_sets_aside(self, partner: str):
        """The migration keeps the partner's bytes under a new name."""
        self.assertIn(("set-aside", partner), self.plan_steps())

    def assert_plan_refuses(self):
        plan = _migration_plan(self.root)
        self.assertEqual(plan.actions, [])
        self.assertEqual([r["scope"] for r in plan.refusals], ["."])

    def test_an_untracked_agents_md_beside_a_tracked_claude_md(self):
        (self.root / "AGENTS.md").write_text("my agents\n")
        self.assert_plan_sets_aside("AGENTS.md")
        self.assertEqual(self.codes(), {"CLAUDE.md": "migrate-set-aside"})

    def test_a_denylisted_agents_md_beside_a_tracked_claude_md(self):
        (self.root / "AGENTS.md").write_text("my agents\n")
        (self.root / ".bionic.yml").write_text(
            "docs_dir: bionic\ninstruction_migration_denylist:\n  - AGENTS.md\n")
        _git(self.root, "add", "AGENTS.md", ".bionic.yml")
        self.assert_plan_refuses()
        self.assertEqual(self.codes(), {"CLAUDE.md": "resolve-partner-then-migrate"})
        (self.root / ".bionic.yml").write_text("docs_dir: bionic\n")  # the step it names
        self.assert_plan_sets_aside("AGENTS.md")
        self.assertEqual(self.codes(), {"CLAUDE.md": "migrate-set-aside"})

    def test_an_untracked_variant_beside_a_tracked_claude_md(self):
        (self.root / "agents.md").write_text("my agents\n")
        self.assert_plan_sets_aside("agents.md")
        self.assertEqual(self.codes(), {"CLAUDE.md": "migrate-set-aside",
                                        "agents.md": "migrate-set-aside"})
        _git(self.root, "add", "agents.md")
        self.assert_plan_sets_aside("agents.md")
        self.assertEqual(self.codes(), {"CLAUDE.md": "migrate-set-aside",
                                        "agents.md": "migrate-set-aside"})

    def test_an_untracked_claude_md_beside_an_untracked_agents_md(self):
        """No tracked file: the migration leaves the root alone until one is tracked."""
        _git(self.root, "rm", "-q", "--cached", "CLAUDE.md")
        (self.root / "AGENTS.md").write_text("my agents\n")
        self.assertEqual(self.plan_steps(), [])
        self.assertEqual(self.codes(), {"CLAUDE.md": "track-then-migrate"})
        _git(self.root, "add", "CLAUDE.md")  # the step it names
        self.assert_plan_sets_aside("AGENTS.md")

    def test_control_a_tracked_partner_is_set_aside(self):
        (self.root / "AGENTS.md").write_text("my agents\n")
        _git(self.root, "add", "AGENTS.md")
        self.assert_plan_sets_aside("AGENTS.md")
        self.assertEqual(self.codes(), {"CLAUDE.md": "migrate-set-aside"})

    def test_control_a_lone_tracked_claude_md_keeps_the_plain_remedy(self):
        self.assertEqual(self.plan_steps(), [("rename-in", "CLAUDE.md"), ("index", None)])
        self.assertEqual(self.codes(), {"CLAUDE.md": "migrate"})

    def test_a_variant_beside_an_untracked_claude_md_is_set_aside(self):
        _git(self.root, "rm", "-q", "--cached", "CLAUDE.md")
        (self.root / "agents.md").write_text("my agents\n")
        _git(self.root, "add", "agents.md")
        self.assertEqual(self.plan_steps(), [("set-aside", "agents.md"), ("create", None)])
        self.assertEqual(self.codes(), {"CLAUDE.md": "migrate-set-aside",
                                        "agents.md": "migrate-set-aside"})


# ----------------------- review fix round 2: every migrate remedy, followed through


MIGRATE_CLI = SCRIPTS / "migrate-instructions.py"
MIGRATE_MODE = REPO_ROOT / "crux" / "skills" / "audit-docs" / "references" / "migrate-mode.md"


def _remedy_sentences() -> dict[str, str]:
    """Step 9's table, code -> sentence, read from the skill the report points at."""
    rows = {}
    for line in SKILL_MD.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\| `([a-z:-]+)` \| (.+) \|$", line)
        if m and m.group(1) in ra.REMEDY_CODES:
            rows[m.group(1)] = m.group(2)
    return rows


def _folds_case(directory: Path) -> bool:
    probe = directory / "case-probe"
    probe.write_text("")
    try:
        return (directory / "CASE-PROBE").exists()
    finally:
        probe.unlink()


def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


class FollowedRemedyTests(unittest.TestCase):
    """Each remedy that names audit-docs --migrate loses nothing when it is followed.

    A case builds a root in a scratch repository, reads each entry's code from the
    report, and performs the step that code's SENTENCE names: every sub-step below
    runs only when its phrase is in the step 9 sentence, so the test follows the
    words a user reads. It then runs the checkout's migration and asserts that
    every entry's prior text is still in a file under the root, a set-aside
    included. A migration that refuses leaves each text where it was, and the same
    assertion accepts that. `test_positive_control_*` shows the assertion reads
    the set-aside: with the set-aside removed, the loser's text is gone.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name).resolve()
        self.root = base / "repo"
        self.root.mkdir()
        # A link target lives OUTSIDE the root, so a lost link cannot pass as
        # surviving through its target.
        self.outside = base / "outside"
        self.outside.mkdir()
        self.sentences = _remedy_sentences()
        self.deny_list: list[str] = []

    # -- building a root -------------------------------------------------------

    def git_init(self):
        _git(self.root, "init", "-q")
        _git(self.root, "config", "user.email", "t@example.invalid")
        _git(self.root, "config", "user.name", "t")

    def need_case_sensitive(self):
        if _folds_case(self.root):
            self.skipTest("needs a case-sensitive volume")

    def put(self, name: str, text: str, track: bool = False, raw: bytes | None = None):
        (self.root / name).write_bytes(raw if raw is not None else text.encode("utf-8"))
        if track:
            _git(self.root, "add", "--", name)

    def link(self, name: str, text: str, track: bool = False):
        target = self.outside / f"{name}.target"
        target.write_text(text)
        os.symlink(target, self.root / name)
        if track:
            _git(self.root, "add", "--", name)

    def deny(self, *names: str):
        self.deny_list = list(names)
        self._write_denylist()

    def _write_denylist(self):
        body = "docs_dir: bionic\n"
        if self.deny_list:
            body += "instruction_migration_denylist:\n" + "".join(
                f"  - {n}\n" for n in self.deny_list)
        (self.root / ".bionic.yml").write_text(body)

    # -- reading it ------------------------------------------------------------

    def codes(self) -> dict[str, str]:
        code, out, err = _run("plan", "--repo-root", str(self.root), "--docs-dir", "bionic")
        self.assertIn(code, (0, 1), err)
        return {r["entry"]: r["code"] for r in out["reports"]}

    def plan_action(self) -> str:
        code, out, err = _run("plan", "--repo-root", str(self.root), "--docs-dir", "bionic")
        self.assertIn(code, (0, 1), err)
        return out["action"]

    def set_asides(self) -> list[str]:
        return sorted(n.name for n in self.root.iterdir() if im.SET_ASIDE_MARK in n.name)

    def surviving_text(self) -> str:
        """Every regular file under the root, minus git's store and the migration's own files."""
        skip = {im.RECEIPT_NAME}
        out = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d != ".git"]
            for f in filenames:
                path = Path(dirpath) / f
                if f in skip or path.is_symlink() or not path.is_file():
                    continue
                out.append(_decode(path.read_bytes()))
        return "\n".join(out)

    def migrate(self) -> int:
        proc = subprocess.run([sys.executable, str(MIGRATE_CLI), "--repo-root",
                               str(self.root), "--migrate"],
                              capture_output=True, text=True)
        self.assertIn(proc.returncode, (0, 1), proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)
        return proc.returncode

    def index_entries(self) -> str:
        """Every index entry, mode and blob and path, as git lists it."""
        if not (self.root / ".git").exists():
            return ""
        return subprocess.run(["git", "-C", str(self.root), "ls-files", "-s", "-z"],
                              capture_output=True, text=True, check=True).stdout

    def index_texts(self) -> list[str]:
        """The text of each blob the index holds."""
        texts = []
        for rec in self.index_entries().split("\0"):
            if rec:
                blob = rec.split("\t")[0].split()[1]
                texts.append(subprocess.run(
                    ["git", "-C", str(self.root), "cat-file", "-p", blob],
                    capture_output=True, text=True, check=True).stdout)
        return texts

    def assert_publishes_nothing(self, snapshot: list[str]):
        """Every line of every index blob now was in an index blob before."""
        held = {line for text in snapshot for line in text.splitlines()}
        for text in self.index_texts():
            for line in text.splitlines():
                self.assertIn(line, held,
                              f"the migration staged a line git did not hold: {line!r}")

    # -- following a sentence --------------------------------------------------

    def says(self, code: str, phrase: str) -> bool:
        return phrase.lower() in self.sentences[code].lower()

    def _tracked(self, name: str) -> bool:
        if not (self.root / ".git").exists():
            return False
        return name in _git_ls(self.root)

    def _text_of(self, name: str) -> str:
        path = self.root / name
        if path.is_dir() and not path.is_symlink():
            return "".join(_decode(p.read_bytes()) for p in sorted(path.rglob("*"))
                           if p.is_file())
        return _decode(path.read_bytes())

    def make_regular(self, name: str):
        """Replace a link or a directory with a regular file holding what it held."""
        path = self.root / name
        if path.is_symlink() or path.is_dir():
            text = self._text_of(name)
            if path.is_symlink():
                path.unlink()
            else:
                shutil.rmtree(path)
            path.write_text(text)

    def track(self, name: str):
        if not (self.root / ".git").exists():
            self.git_init()
        _git(self.root, "add", "--", name)

    def unlist(self, name: str):
        if name in self.deny_list:
            self.deny_list.remove(name)
            self._write_denylist()

    def delete(self, name: str):
        if self._tracked(name):
            _git(self.root, "rm", "-q", "--cached", "--", name)
        path = self.root / name
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()

    def add_missing_lines(self, name: str):
        """Append each WARNING line the entry lacks, the pointer and the objectives."""
        blocks = ra.load_blocks(SKILL_MD, "bionic")
        path = self.root / name
        text = path.read_text(encoding="utf-8")
        for block in (blocks.pointer, blocks.objectives):
            if block not in text:
                text += ("" if text.endswith("\n") else "\n") + "\n" + block + "\n"
        path.write_text(text, encoding="utf-8")

    def resolve_agents_md(self, code: str):
        """AGENTS.md's own report: the entry the fix-agents sentence names."""
        path = self.root / "AGENTS.md"
        if code == "replace-with-regular-file":
            self.make_regular("AGENTS.md")
        elif code == "make-readable-or-reencode":
            path.chmod(0o644)
            path.write_text(_decode(path.read_bytes()), encoding="utf-8")
        elif code == "make-writable":
            path.chmod(0o644)
        else:
            self.fail(f"AGENTS.md reported with {code}")

    def follow(self, entry: str, code: str, codes: dict[str, str]):
        """Perform what `code`'s sentence names for `entry`, and nothing more."""
        did = []

        def step(phrase, action):
            if self.says(code, phrase):
                action()
                did.append(phrase)

        family = sorted(n.name for n in self.root.iterdir()
                        if n.name.lower() == entry.lower())
        if code in ("migrate", "migrate-set-aside"):
            did.append("nothing to do before the migration")
        step("run git init", self.git_init)
        step("git add on the entry", lambda: self.track(entry))
        step("git add on it", lambda: self.track(entry))
        step("remove it from instruction_migration_denylist", lambda: self.unlist(entry))
        if self.says(code, "Make that entry a regular file that instruction_migration_denylist does not name"):
            for name in sorted(n.name for n in self.root.iterdir()
                               if n.name != entry and n.name.lower() in ("agents.md", "claude.md")):
                self.make_regular(name)
                self.unlist(name)
            did.append("Make that entry a regular file")
        if self.says(code, "Move one of them to a name that does not exist yet"):
            keep = next((n for n in family if n in ("AGENTS.md", "CLAUDE.md")), family[0])
            for n in family:
                if n == keep:
                    continue
                if self._tracked(n):
                    _git(self.root, "mv", "--", n, f"{n}.moved")
                else:
                    os.rename(self.root / n, self.root / f"{n}.moved")
            did.append("Move one of them")
        if self.says(code, "Resolve the AGENTS.md entry named above first"):
            self.resolve_agents_md(codes["AGENTS.md"])
            if self.says(code, "regular file that git tracks"):
                self.track("AGENTS.md")
            if self.says(code, "instruction_migration_denylist does not name"):
                self.unlist("AGENTS.md")
            did.append("Resolve the AGENTS.md entry")
        step("No step is needed", lambda: None)
        step("Add any line below that this file lacks to this file, not to AGENTS.md",
             lambda: self.add_missing_lines(entry))
        step("Replace the link with a regular file", lambda: self.make_regular(entry))
        step("Rename the entry to a name that does not exist yet",
             lambda: os.rename(self.root / entry, self.root / f"{entry}-renamed"))
        self.assertTrue(did, f"no step of {code!r} matched its sentence")

    # -- the cases -------------------------------------------------------------

    def run_case(self, entry: str, want: str, texts: list[str]):
        """Follow the reported code, migrate, then check nothing was lost and the code."""
        before = self.codes()
        self.assertIn(entry, before)
        self.follow(entry, before[entry], before)
        snapshot = self.index_texts()
        self.migrate()
        self.assert_publishes_nothing(snapshot)
        surviving = self.surviving_text()
        for text in texts:
            self.assertIn(text, surviving, f"{text!r} lost after following {before[entry]!r}")
        self.assertEqual(before[entry], want)

    def test_migrate(self):
        self.git_init()
        self.put("CLAUDE.md", "claude-alone-rules\n", track=True)
        self.run_case("CLAUDE.md", "migrate", ["claude-alone-rules"])
        self.assertIn("claude-alone-rules", (self.root / "AGENTS.md").read_text())

    def test_migrate_set_aside(self):
        self.git_init()
        self.put("CLAUDE.md", "## Claude\n\nclaude-set-aside-rules\n", track=True)
        self.put("AGENTS.md", "## Agents\n\nagents-set-aside-notes\n", track=True)
        self.run_case("CLAUDE.md", "migrate-set-aside",
                      ["claude-set-aside-rules", "agents-set-aside-notes"])

    def test_carried_winner_is_reported_once_and_the_remedy_does_not_loop(self):
        """M3: an untracked CLAUDE.md beside a tracked AGENTS.md, migrated once, stays carried."""
        self.git_init()
        self.put("CLAUDE.md", "## Claude\n\nclaude-m3-rules\n")
        self.put("AGENTS.md", "## Agents\n\nagents-m3-notes\n", track=True)
        before = self.codes()
        self.assertEqual(before["CLAUDE.md"], "migrate-set-aside")
        self.follow("CLAUDE.md", before["CLAUDE.md"], before)
        self.migrate()
        after = self.codes()
        self.assertEqual(after["CLAUDE.md"], "not-applicable:carried")
        # MF-2: init-docs plans no append into an AGENTS.md the next migration
        # would set aside; it reports the carried entry instead.
        self.assertEqual(self.plan_action(), "none")
        self.follow("CLAUDE.md", after["CLAUDE.md"], after)
        self.migrate()
        files = self.surviving_text()
        index = self.index_entries()
        self.migrate()
        self.assertEqual(self.surviving_text(), files)
        self.assertEqual(self.index_entries(), index)
        self.assertEqual(self.codes()["CLAUDE.md"], "not-applicable:carried")
        self.assertEqual(self.plan_action(), "none")
        agents = (self.root / "AGENTS.md").read_text()
        self.assertIn("claude-m3-rules", agents)
        self.assertIn(POINTER, agents)
        self.assertIn("agents-m3-notes", self.surviving_text())

    # -- MF-2: the migrate-mode guidance for init-docs' lines, followed literally

    def guidance(self) -> str:
        return " ".join(MIGRATE_MODE.read_text(encoding="utf-8").split())

    def build_untracked_winner_migrated(self):
        """rereview-c/live1: an untracked CLAUDE.md beside a committed AGENTS.md
        holding init-docs' pointer line, migrated once."""
        self.git_init()
        self.put("CLAUDE.md", "# Claude rules\n")
        self.put("AGENTS.md", f"# Agents notes\n\n{POINTER}\n", track=True)
        _git(self.root, "commit", "-q", "-m", "fixture")
        self.migrate()
        self.assertNotIn(POINTER, (self.root / "AGENTS.md").read_text())

    def assert_converged(self):
        files, index, asides = self.surviving_text(), self.index_entries(), self.set_asides()
        self.migrate()
        self.assertEqual(self.set_asides(), asides, "the next migration set AGENTS.md aside")
        self.assertEqual((self.surviving_text(), self.index_entries()), (files, index))

    def test_followed_guidance_for_an_untracked_winner_does_not_loop(self):
        """Copy the lines into the winner left in place, then migrate again."""
        text = self.guidance()
        self.assertIn("While an untracked or ignored `CLAUDE.md` stays in place, copy them "
                      "into that `CLAUDE.md`, not into `AGENTS.md`, then run `--migrate` "
                      "again", text)
        self.build_untracked_winner_migrated()
        self.add_missing_lines("CLAUDE.md")
        self.migrate()
        self.assertIn(POINTER, (self.root / "AGENTS.md").read_text())
        self.assert_converged()
        self.assertEqual(self.codes(), {"CLAUDE.md": "not-applicable:carried"})
        self.assertEqual(self.plan_action(), "none")
        surviving = self.surviving_text()
        for piece in ("# Claude rules", "# Agents notes", POINTER):
            self.assertIn(piece, surviving)

    def test_followed_guidance_to_track_the_winner_first_does_not_loop(self):
        """Or git add the winner, migrate, and only then copy the lines into AGENTS.md."""
        text = self.guidance()
        self.assertIn("Or, where git does not ignore that `CLAUDE.md`, track it first with "
                      "`git add`, then run `--migrate` again. Once a run stages the scope, "
                      "copy them into `AGENTS.md`.", text)
        # Forcing an ignored winner into the index publishes it, which the migration's
        # "publishes nothing" contract forbids, so the guidance never offers it.
        self.assertNotIn("git add -f", text)
        self.build_untracked_winner_migrated()
        _git(self.root, "add", "--", "CLAUDE.md")
        snapshot = self.index_texts()
        self.migrate()
        self.assert_publishes_nothing(snapshot)
        # The run staged the scope: the index holds AGENTS.md and no winner path.
        self.assertEqual(_git_ls(self.root), {"AGENTS.md"})
        self.add_missing_lines("AGENTS.md")
        self.assert_converged()
        self.assertIn(POINTER, (self.root / "AGENTS.md").read_text())
        surviving = self.surviving_text()
        for piece in ("# Claude rules", "# Agents notes"):
            self.assertIn(piece, surviving)

    def test_positive_control_copying_into_agents_md_beside_the_winner_loops(self):
        """The old guidance: lines added to AGENTS.md while the untracked winner
        stays are set aside by the next migration, which assert_converged sees."""
        self.build_untracked_winner_migrated()
        self.add_missing_lines("AGENTS.md")
        with self.assertRaises(AssertionError):
            self.assert_converged()
        self.assertNotIn(POINTER, (self.root / "AGENTS.md").read_text())

    def test_a_tracked_winner_left_in_place_is_not_reported_carried(self):
        """The carried sentence says the winner is untracked or ignored, so a
        tracked winner a blocked scope leaves in place never gets it."""
        self.git_init()
        self.put("CLAUDE.md", "## Claude\n\nclaude-t1\n", track=True)
        self.put("AGENTS.md", "## Agents\n\ncommitted-t1\n", track=True)
        _git(self.root, "commit", "-q", "-m", "fixture")
        self.put("AGENTS.md", "## Agents\n\nstaged-only-t1\n", track=True)
        self.put("AGENTS.md", "## Agents\n\nworking-t1\n")
        self.migrate()
        self.assertTrue((self.root / "CLAUDE.md").exists())
        self.assertEqual((self.root / "AGENTS.md").read_text(), "## Claude\n\nclaude-t1\n")
        self.assertNotEqual(self.codes()["CLAUDE.md"], "not-applicable:carried")

    def test_track_then_migrate(self):
        self.git_init()
        self.put("CLAUDE.md", "## Claude\n\nclaude-untracked-rules\n")
        self.run_case("CLAUDE.md", "track-then-migrate", ["claude-untracked-rules"])

    def test_unlist_then_migrate(self):
        self.git_init()
        self.deny("CLAUDE.md")
        self.put("CLAUDE.md", "## Claude\n\nclaude-listed-rules\n", track=True)
        self.put("AGENTS.md", "## Agents\n\nagents-plain-notes\n", track=True)
        self.run_case("CLAUDE.md", "unlist-then-migrate",
                      ["claude-listed-rules", "agents-plain-notes"])

    def test_track_and_unlist_then_migrate(self):
        self.git_init()
        self.deny("CLAUDE.md")
        self.put("CLAUDE.md", "claude-both-rules\n")
        self.run_case("CLAUDE.md", "track-and-unlist-then-migrate", ["claude-both-rules"])

    def test_set_aside_untracked_agents_md(self):
        """D1 at the root: the untracked AGENTS.md was written over."""
        self.git_init()
        self.put("CLAUDE.md", "## Claude\n\nclaude-p1\n", track=True)
        self.put("AGENTS.md", "## Agents\n\nagents-p1\n")
        self.run_case("CLAUDE.md", "migrate-set-aside", ["claude-p1", "agents-p1"])

    def test_set_aside_an_untracked_claude_md_beside_a_tracked_agents_md(self):
        self.git_init()
        self.put("CLAUDE.md", "## Claude\n\nclaude-u1\n")
        self.put("AGENTS.md", "## Agents\n\nagents-u1\n", track=True)
        self.run_case("CLAUDE.md", "migrate-set-aside", ["claude-u1", "agents-u1"])

    def test_fix_agents_linked_agents_md(self):
        for tracked_link in (False, True):
            with self.subTest(tracked_link=tracked_link):
                self.setUp()
                self.git_init()
                self.put("CLAUDE.md", "## Claude\n\nclaude-p2\n", track=True)
                self.link("AGENTS.md", "## Agents\n\nagents-p2-link\n", track=tracked_link)
                self.run_case("CLAUDE.md", "fix-agents-then-migrate",
                              ["claude-p2", "agents-p2-link"])

    def test_track_then_migrate_both_untracked(self):
        self.git_init()
        self.put("CLAUDE.md", "## Claude\n\nclaude-p3\n")
        self.put("AGENTS.md", "## Agents\n\nagents-p3\n")
        self.run_case("CLAUDE.md", "track-then-migrate", ["claude-p3", "agents-p3"])

    def test_resolve_partner_denylisted_agents_md(self):
        """D6 at the root: the denylisted AGENTS.md was written over."""
        self.git_init()
        self.deny("AGENTS.md")
        self.put("CLAUDE.md", "## Claude\n\nclaude-p4\n", track=True)
        self.put("AGENTS.md", "## Agents\n\nagents-p4\n", track=True)
        self.run_case("CLAUDE.md", "resolve-partner-then-migrate", ["claude-p4", "agents-p4"])

    def test_resolve_partner_directory_variant(self):
        self.git_init()
        self.put("CLAUDE.md", "## Claude\n\nclaude-p5\n", track=True)
        (self.root / "agents.md").mkdir()
        (self.root / "agents.md" / "notes.md").write_text("## Agents\n\nagents-p5-dir\n")
        self.run_case("CLAUDE.md", "resolve-partner-then-migrate",
                      ["claude-p5", "agents-p5-dir"])

    def test_fix_agents_untracked_non_utf8_agents_md(self):
        self.git_init()
        self.put("CLAUDE.md", "## Claude\n\nclaude-p6\n", track=True)
        self.put("AGENTS.md", "", raw="## Agents\n\ncafé-agents-p6\n".encode("latin-1"))
        self.run_case("CLAUDE.md", "fix-agents-then-migrate",
                      ["claude-p6", "café-agents-p6"])

    def test_not_applicable_linked_claude_md_beside_untracked_agents_md(self):
        self.git_init()
        self.link("CLAUDE.md", "## Claude\n\nclaude-p7-link\n", track=True)
        self.put("AGENTS.md", "## Agents\n\nagents-p7\n")
        self.run_case("CLAUDE.md", "not-applicable:symlink",
                      ["claude-p7-link", "agents-p7"])

    def test_set_aside_untracked_variant(self):
        self.git_init()
        self.put("CLAUDE.md", "## Claude\n\nclaude-v0\n", track=True)
        self.put("agents.md", "## Agents\n\nagents-v0-variant\n")
        self.run_case("CLAUDE.md", "migrate-set-aside", ["claude-v0", "agents-v0-variant"])

    def test_set_aside_variant_both_tracked(self):
        """D2 at the root: on a case-folding volume the merge deleted both files."""
        for entry in ("CLAUDE.md", "agents.md"):
            with self.subTest(entry=entry):
                self.setUp()
                self.git_init()
                self.put("CLAUDE.md", "## Claude\n\nclaude-v1\n", track=True)
                self.put("agents.md", "## Agents\n\nagents-v1-variant\n", track=True)
                self.run_case(entry, "migrate-set-aside", ["claude-v1", "agents-v1-variant"])

    def test_not_applicable_linked_variant_beside_untracked_claude(self):
        self.git_init()
        self.put("claude.md", "## Claude\n\nclaude-v2\n")
        self.link("Agents.md", "## Agents\n\nagents-v2-link\n", track=True)
        self.run_case("Agents.md", "not-applicable:symlink", ["claude-v2", "agents-v2-link"])

    def test_resolve_partner_linked_variant_beside_untracked_claude(self):
        self.git_init()
        self.put("claude.md", "## Claude\n\nclaude-v3\n")
        self.link("Agents.md", "## Agents\n\nagents-v3-link\n", track=True)
        self.run_case("claude.md", "resolve-partner-then-migrate",
                      ["claude-v3", "agents-v3-link"])

    def test_not_applicable_no_git_beside_agents_md(self):
        self.put("CLAUDE.md", "## Claude\n\nclaude-p8\n")
        self.put("AGENTS.md", "## Agents\n\nagents-p8\n")
        self.run_case("CLAUDE.md", "not-applicable:no-git", ["claude-p8", "agents-p8"])

    def test_resolve_family_claude_family_beside_untracked_agents_md(self):
        self.git_init()
        self.need_case_sensitive()
        self.put("CLAUDE.md", "## Claude\n\nclaude-p9-upper\n", track=True)
        self.put("claude.md", "## More\n\nclaude-p9-lower\n", track=True)
        self.put("AGENTS.md", "## Agents\n\nagents-p9\n")
        self.run_case("CLAUDE.md", "resolve-family-then-migrate",
                      ["claude-p9-upper", "claude-p9-lower", "agents-p9"])

    def test_resolve_family_claude_pair(self):
        self.git_init()
        self.need_case_sensitive()
        self.put("CLAUDE.md", "## Claude\n\nclaude-f1-upper\n", track=True)
        self.put("claude.md", "## More\n\nclaude-f1-lower\n", track=True)
        self.run_case("claude.md", "resolve-family-then-migrate",
                      ["claude-f1-upper", "claude-f1-lower"])

    def test_resolve_family_variant_beside_untracked_agents_md(self):
        self.git_init()
        self.need_case_sensitive()
        self.put("agents.md", "## Variant\n\nagents-f2-variant\n", track=True)
        self.put("AGENTS.md", "## Agents\n\nagents-f2-exact\n")
        self.run_case("agents.md", "resolve-family-then-migrate",
                      ["agents-f2-variant", "agents-f2-exact"])

    def test_fix_agents_variant_beside_linked_agents_md(self):
        self.git_init()
        self.need_case_sensitive()
        self.put("agents.md", "## Variant\n\nagents-f3-variant\n", track=True)
        self.link("AGENTS.md", "## Agents\n\nagents-f3-link\n")
        self.run_case("agents.md", "fix-agents-then-migrate",
                      ["agents-f3-variant", "agents-f3-link"])

    def test_fix_agents_non_utf8_tracked_agents_md(self):
        self.git_init()
        self.put("CLAUDE.md", "## Claude\n\nclaude-x1\n", track=True)
        self.put("AGENTS.md", "", raw="## Agents\n\ncafé-agents-x1\n".encode("latin-1"),
                 track=True)
        self.run_case("CLAUDE.md", "fix-agents-then-migrate",
                      ["claude-x1", "café-agents-x1"])

    def test_fix_agents_directory_agents_md(self):
        self.git_init()
        self.put("CLAUDE.md", "## Claude\n\nclaude-x2\n", track=True)
        (self.root / "AGENTS.md").mkdir()
        (self.root / "AGENTS.md" / "notes.md").write_text("## Agents\n\nagents-x2-dir\n")
        self.run_case("CLAUDE.md", "fix-agents-then-migrate", ["claude-x2", "agents-x2-dir"])

    def test_not_applicable_no_git(self):
        self.put("CLAUDE.md", "claude-n1\n")
        self.run_case("CLAUDE.md", "not-applicable:no-git", ["claude-n1"])

    def test_not_applicable_symlink(self):
        self.git_init()
        self.link("CLAUDE.md", "claude-n2-link\n", track=True)
        self.run_case("CLAUDE.md", "not-applicable:symlink", ["claude-n2-link"])

    def test_not_applicable_not_regular_file(self):
        self.git_init()
        (self.root / "CLAUDE.md").mkdir()
        (self.root / "CLAUDE.md" / "rules.md").write_text("claude-n3-dir\n")
        _git(self.root, "add", "--", "CLAUDE.md/rules.md")
        self.run_case("CLAUDE.md", "not-applicable:not-regular-file", ["claude-n3-dir"])

    # -- coverage and controls --------------------------------------------------

    def test_every_code_that_names_the_migration_has_a_followed_case(self):
        named = {c for c, s in self.sentences.items() if "audit-docs --migrate" in s}
        self.assertEqual(len(named), 12, sorted(named))
        cases = "".join(_source_of(type(self), n) for n in dir(self)
                        if n.startswith("test_") and "positive_control" not in n)
        # A case names its expected code as a string literal in its run_case call.
        self.assertEqual({c for c in named if f'"{c}"' not in cases}, set())

    def set_aside_of(self, text: str) -> Path:
        held = [p for p in self.root.iterdir() if ".crux-set-aside-" in p.name
                and text in _decode(p.read_bytes())]
        self.assertEqual(len(held), 1, sorted(p.name for p in self.root.iterdir()))
        return held[0]

    def test_positive_control_an_untracked_agents_md_survives_only_in_its_set_aside(self):
        """The check reads the set-aside: without it the D1 loser's text is gone."""
        self.git_init()
        self.put("CLAUDE.md", "## Claude\n\nclaude-ctl\n", track=True)
        self.put("AGENTS.md", "## Agents\n\nagents-ctl\n")
        self.migrate()
        self.assertIn("agents-ctl", self.surviving_text())
        self.set_aside_of("agents-ctl").unlink()
        self.assertNotIn("agents-ctl", self.surviving_text())
        self.assertIn("claude-ctl", self.surviving_text())

    def test_positive_control_a_tracked_variant_survives_only_in_its_set_aside(self):
        """D2's loser text lives in one place after the migration: its set-aside."""
        self.git_init()
        self.put("CLAUDE.md", "## Claude\n\nclaude-vctl\n", track=True)
        self.put("agents.md", "## Agents\n\nagents-vctl\n", track=True)
        self.migrate()
        self.set_aside_of("agents-vctl").unlink()
        surviving = self.surviving_text()
        self.assertNotIn("agents-vctl", surviving)
        self.assertIn("claude-vctl", surviving)

    def test_positive_control_the_publish_check_fails_on_a_staged_new_line(self):
        """A line staged by hand after the snapshot is one git did not hold."""
        self.git_init()
        self.put("CLAUDE.md", "## Claude\n\nclaude-pub\n", track=True)
        snapshot = self.index_texts()
        self.assert_publishes_nothing(snapshot)
        self.put("notes.md", "a-line-git-never-held\n", track=True)
        with self.assertRaises(AssertionError):
            self.assert_publishes_nothing(snapshot)

    def test_positive_control_a_step_is_gated_on_its_sentence(self):
        """Without its clause the fix-agents step leaves the directory in place."""
        self.git_init()
        self.put("CLAUDE.md", "## Claude\n\nclaude-gate\n", track=True)
        (self.root / "AGENTS.md").mkdir()
        (self.root / "AGENTS.md" / "notes.md").write_text("## Agents\n\nagents-gate\n")
        codes = self.codes()
        self.sentences["fix-agents-then-migrate"] = "Then run audit-docs --migrate."
        with self.assertRaises(AssertionError):
            self.follow("CLAUDE.md", codes["CLAUDE.md"], codes)
        self.assertTrue((self.root / "AGENTS.md").is_dir())
        self.sentences = _remedy_sentences()
        self.follow("CLAUDE.md", codes["CLAUDE.md"], codes)
        self.assertTrue((self.root / "AGENTS.md").is_file())


def _git_ls(root: Path) -> set[str]:
    out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"],
                         capture_output=True, text=True, check=True).stdout
    return {p for p in out.split("\0") if p}


def _source_of(cls, name: str) -> str:
    import inspect
    return inspect.getsource(getattr(cls, name))


if __name__ == "__main__":
    unittest.main()
