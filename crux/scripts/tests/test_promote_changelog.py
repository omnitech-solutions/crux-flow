"""Test suite for promote-changelog.py.

Per [[adrs/ADR-0009-automate-changelog-promotion-during-release]]. Stdlib
only (unittest, tempfile, subprocess, pathlib, sys, hashlib, json, os).
Each test writes a fresh CHANGELOG fixture to a tempdir and invokes either
the CLI via subprocess or the in-process `promote()` function directly.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPTS_DIR = REPO_ROOT / "crux" / "scripts"
CLI = SCRIPTS_DIR / "promote-changelog.py"


def _load_module():
    """Import promote-changelog.py as a module (dash in name needs importlib)."""
    spec = importlib.util.spec_from_file_location("promote_changelog_module", CLI)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


MODULE = _load_module()


def run_cli(*args, env=None):
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        capture_output=True,
        text=True,
        env=full_env,
    )


SAMPLE_HAPPY = textwrap.dedent("""\
    # Changelog

    All notable changes to this project will be documented in this file.

    ## [Unreleased]

    ### Added
    - New `audit-docs` skill for vault-wide drift checks.

    ### Changed
    - Bumped Python pin to 3.13.

    ## [0.3.0] — 2026-05-26

    ### Added
    - Initial cycle promptbook.
""")

SAMPLE_EMPTY_STUB = textwrap.dedent("""\
    # Changelog

    ## [Unreleased]

    ### Added

    ### Changed

    ### Fixed

    ### Removed

    ## [0.3.0] — 2026-05-26

    ### Added
    - Stuff.
""")

SAMPLE_HTML_COMMENT_ONLY = textwrap.dedent("""\
    # Changelog

    ## [Unreleased]

    <!-- TODO: write release notes -->

    ## [0.3.0] — 2026-05-26

    ### Added
    - Stuff.
""")


class PromoteHappyPathTest(unittest.TestCase):
    def test_happy_path_promotes_unreleased(self):
        new_text, _, auto_filled = MODULE.promote(SAMPLE_HAPPY, "0.4.0", "2026-05-27")
        self.assertFalse(auto_filled)
        # Promoted heading uses em-dash, not hyphen.
        self.assertIn("## [0.4.0] — 2026-05-27", new_text)
        # Body of Unreleased was copied through.
        self.assertIn("New `audit-docs` skill", new_text)
        self.assertIn("Bumped Python pin to 3.13.", new_text)
        # Fresh Unreleased shell exists with four canonical subsections.
        unreleased_block = new_text.split("## [0.4.0]")[0]
        self.assertIn("## [Unreleased]", unreleased_block)
        self.assertIn("### Added", unreleased_block)
        self.assertIn("### Changed", unreleased_block)
        self.assertIn("### Fixed", unreleased_block)
        self.assertIn("### Removed", unreleased_block)
        # Older versioned section preserved.
        self.assertIn("## [0.3.0] — 2026-05-26", new_text)


class EmptyUnreleasedAutoFillTest(unittest.TestCase):
    def test_empty_stub_auto_fills(self):
        new_text, notes, auto_filled = MODULE.promote(SAMPLE_EMPTY_STUB, "0.4.0", "2026-05-27")
        self.assertTrue(auto_filled)
        self.assertIn("- No user-facing changes.", new_text)
        self.assertEqual(notes.strip(), "- No user-facing changes.")

    def test_html_comment_only_counts_as_empty(self):
        new_text, _, auto_filled = MODULE.promote(SAMPLE_HTML_COMMENT_ONLY, "0.4.0", "2026-05-27")
        self.assertTrue(auto_filled)
        self.assertIn("- No user-facing changes.", new_text)


class ReRunGuardTest(unittest.TestCase):
    def test_version_already_present_raises(self):
        with self.assertRaises(ValueError) as ctx:
            MODULE.promote(SAMPLE_HAPPY, "0.3.0", "2026-05-27")
        self.assertIn("already promoted", str(ctx.exception))

    def test_separator_tolerant_collision(self):
        text = "# Changelog\n\n## [Unreleased]\n\n- foo\n\n## [0.4.0]-2026-05-26\n"
        with self.assertRaises(ValueError):
            MODULE.promote(text, "0.4.0", "2026-05-27")

    def test_idempotency_second_run_aborts(self):
        new_text, _, _ = MODULE.promote(SAMPLE_HAPPY, "0.4.0", "2026-05-27")
        with self.assertRaises(ValueError):
            MODULE.promote(new_text, "0.4.0", "2026-05-27")


class MissingUnreleasedTest(unittest.TestCase):
    def test_missing_unreleased_raises(self):
        text = "# Changelog\n\n## [0.3.0] — 2026-05-26\n\n- Stuff.\n"
        with self.assertRaises(ValueError) as ctx:
            MODULE.promote(text, "0.4.0", "2026-05-27")
        self.assertIn("no `## [Unreleased]`", str(ctx.exception))

    def test_multiple_unreleased_raises(self):
        text = "# C\n\n## [Unreleased]\n\n- foo\n\n## [Unreleased]\n\n- bar\n"
        with self.assertRaises(ValueError):
            MODULE.promote(text, "0.4.0", "2026-05-27")


class EmDashByteLevelTest(unittest.TestCase):
    def test_em_dash_byte_level(self):
        new_text, _, _ = MODULE.promote(SAMPLE_HAPPY, "0.4.0", "2026-05-27")
        self.assertIn(b"## [0.4.0] \xe2\x80\x94 2026-05-27", new_text.encode("utf-8"))
        # No hyphen-form heading produced.
        self.assertNotIn("## [0.4.0] - 2026-05-27", new_text)


class CliDryRunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="promote-changelog-"))
        self.cl = self.tmp / "CHANGELOG.md"
        self.cl.write_text(SAMPLE_HAPPY, encoding="utf-8")

    def tearDown(self):
        for p in self.tmp.iterdir():
            if p.is_file():
                p.unlink()
        self.tmp.rmdir()

    def test_dry_run_makes_no_writes(self):
        before = hashlib.sha256(self.cl.read_bytes()).hexdigest()
        result = run_cli(
            "--version", "0.4.0",
            "--changelog", str(self.cl),
            "--dry-run",
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        after = hashlib.sha256(self.cl.read_bytes()).hexdigest()
        self.assertEqual(before, after, msg="dry-run modified the file")
        payload = json.loads(result.stdout)
        self.assertEqual(payload["version"], "0.4.0")
        self.assertEqual(payload["auto_filled_empty"], False)

    def test_dry_run_validation_failure_exits_1(self):
        result = run_cli(
            "--version", "0.3.0",   # already in fixture
            "--changelog", str(self.cl),
            "--dry-run",
        )
        self.assertEqual(result.returncode, 1)


class ReleaseNotesOutTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="promote-changelog-"))
        self.cl = self.tmp / "CHANGELOG.md"
        self.cl.write_text(SAMPLE_HAPPY, encoding="utf-8")
        self.notes_out = self.tmp / "dist" / "release-notes.md"

    def tearDown(self):
        for sub in self.tmp.rglob("*"):
            if sub.is_file():
                sub.unlink()
        for sub in sorted(self.tmp.rglob("*"), reverse=True):
            if sub.is_dir():
                sub.rmdir()
        self.tmp.rmdir()

    def test_release_notes_out_body_only_no_heading(self):
        result = run_cli(
            "--version", "0.4.0",
            "--changelog", str(self.cl),
            "--release-notes-out", str(self.notes_out),
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        notes = self.notes_out.read_text(encoding="utf-8")
        # No version heading line in the release-notes file.
        for line in notes.splitlines():
            self.assertFalse(line.startswith("## ["), msg=f"unexpected heading: {line!r}")
        # Body content preserved.
        self.assertIn("New `audit-docs` skill", notes)
        self.assertIn("Bumped Python pin to 3.13.", notes)


class VersionParsingTest(unittest.TestCase):
    def test_leading_v_stripped(self):
        new_text, _, _ = MODULE.promote(SAMPLE_HAPPY, "0.4.0", "2026-05-27")
        self.assertIn("## [0.4.0] — 2026-05-27", new_text)
        # Now feed with v-prefix via normalize_version helper.
        version = MODULE._normalize_version("v0.4.0")
        self.assertEqual(version, "0.4.0")

    def test_pre_release_version_accepted(self):
        version = MODULE._normalize_version("0.4.0-alpha.1")
        self.assertEqual(version, "0.4.0-alpha.1")
        new_text, _, _ = MODULE.promote(SAMPLE_HAPPY, version, "2026-05-27")
        self.assertIn("## [0.4.0-alpha.1] — 2026-05-27", new_text)

    def test_invalid_version_rejected(self):
        with self.assertRaises(ValueError):
            MODULE._normalize_version("0.4")
        with self.assertRaises(ValueError):
            MODULE._normalize_version("not-semver")
        with self.assertRaises(ValueError):
            MODULE._normalize_version("1.0.0+meta")


class TrailingNewlineTest(unittest.TestCase):
    def test_trailing_newline_preserved(self):
        text_with = SAMPLE_HAPPY
        self.assertTrue(text_with.endswith("\n"))
        new_text, _, _ = MODULE.promote(text_with, "0.4.0", "2026-05-27")
        self.assertTrue(new_text.endswith("\n"))

    def test_no_trailing_newline_preserved(self):
        text_no = SAMPLE_HAPPY.rstrip("\n")
        new_text, _, _ = MODULE.promote(text_no, "0.4.0", "2026-05-27")
        self.assertFalse(new_text.endswith("\n"))


class FenceAwareTest(unittest.TestCase):
    def test_section_lookalike_in_code_fence_not_treated_as_heading(self):
        text = textwrap.dedent("""\
            # Changelog

            ## [Unreleased]

            ### Added
            - A new feature. Example:
              ```markdown
              ## [Foo]
              this is inside a fence
              ```

            ## [0.3.0] — 2026-05-26

            ### Added
            - Stuff.
        """)
        new_text, _, auto_filled = MODULE.promote(text, "0.4.0", "2026-05-27")
        self.assertFalse(auto_filled)
        # The fenced content should be preserved verbatim in the promoted body.
        self.assertIn("## [Foo]", new_text)
        # Only ONE 0.4.0 heading appears.
        self.assertEqual(new_text.count("## [0.4.0]"), 1)


class NestedBulletsTest(unittest.TestCase):
    def test_nested_bullets_preserved_verbatim(self):
        text = textwrap.dedent("""\
            # Changelog

            ## [Unreleased]

            ### Added
            - Top-level
              - Sub-bullet A
              - Sub-bullet B

            ## [0.3.0] — 2026-05-26

            ### Added
            - Stuff.
        """)
        new_text, _, _ = MODULE.promote(text, "0.4.0", "2026-05-27")
        self.assertIn("  - Sub-bullet A", new_text)
        self.assertIn("  - Sub-bullet B", new_text)


class UtcDateTest(unittest.TestCase):
    def test_today_utc_format(self):
        today = MODULE._today_utc()
        self.assertRegex(today, r"^\d{4}-\d{2}-\d{2}$")

    def test_date_flag_overrides_default(self):
        tmp = Path(tempfile.mkdtemp(prefix="promote-changelog-"))
        try:
            cl = tmp / "CHANGELOG.md"
            cl.write_text(SAMPLE_HAPPY, encoding="utf-8")
            result = run_cli(
                "--version", "0.4.0",
                "--changelog", str(cl),
                "--date", "2030-01-15",
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertIn("## [0.4.0] — 2030-01-15", cl.read_text(encoding="utf-8"))
        finally:
            for p in tmp.iterdir():
                p.unlink()
            tmp.rmdir()


class ExtraSubsectionsTest(unittest.TestCase):
    def test_non_canonical_subsection_preserved(self):
        text = textwrap.dedent("""\
            # Changelog

            ## [Unreleased]

            ### Added
            - X

            ### Repo / config
            - Y

            ## [0.3.0] — 2026-05-26

            ### Added
            - Z
        """)
        new_text, _, _ = MODULE.promote(text, "0.4.0", "2026-05-27")
        # Promoted section retains the non-canonical subsection.
        self.assertIn("### Repo / config", new_text)
        # Fresh Unreleased shell has only the four canonical subsections.
        head = new_text.split("## [0.4.0]")[0]
        self.assertIn("### Removed", head)
        self.assertNotIn("### Repo / config", head)


class CliMissingFileTest(unittest.TestCase):
    def test_missing_changelog_exits_1(self):
        result = run_cli(
            "--version", "0.4.0",
            "--changelog", "/tmp/does-not-exist-69b7c2.md",
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("changelog not found", result.stderr)


class CliDateValidationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="promote-changelog-"))
        self.cl = self.tmp / "CHANGELOG.md"
        self.cl.write_text(SAMPLE_HAPPY, encoding="utf-8")

    def tearDown(self):
        for p in self.tmp.iterdir():
            if p.is_file():
                p.unlink()
        self.tmp.rmdir()

    def test_invalid_date_exits_1(self):
        for bad in ("27-05-2026", "tomorrow", "2026/05/27", "20260527"):
            result = run_cli(
                "--version", "0.4.0",
                "--changelog", str(self.cl),
                "--date", bad,
                "--dry-run",
            )
            self.assertEqual(result.returncode, 1, msg=f"bad date {bad!r} should exit 1")
            self.assertIn("YYYY-MM-DD", result.stderr)


class EmptyStringInputTest(unittest.TestCase):
    def test_empty_string_raises_no_unreleased(self):
        with self.assertRaises(ValueError) as ctx:
            MODULE.promote("", "1.0.0", "2026-01-01")
        self.assertIn("no `## [Unreleased]`", str(ctx.exception))


class CliErrorMessageIncludesPathTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="promote-changelog-"))
        self.cl = self.tmp / "CHANGELOG.md"
        self.cl.write_text("# Changelog\n\n## [0.3.0] — 2026-05-26\n\n- foo\n", encoding="utf-8")

    def tearDown(self):
        for p in self.tmp.iterdir():
            if p.is_file():
                p.unlink()
        self.tmp.rmdir()

    def test_error_message_names_file(self):
        result = run_cli(
            "--version", "0.4.0",
            "--changelog", str(self.cl),
        )
        self.assertEqual(result.returncode, 1)
        # The error message should name the file path so the operator knows where to look.
        self.assertIn(str(self.cl), result.stderr)


class AtomicWriteSymlinkRefusalTests(unittest.TestCase):
    """[SECURITY:S5] The promote-changelog sibling of the extract-code-docs.py
    guard. A bare `tmp.write_bytes` FOLLOWS a planted link at the predictable
    `<path>.tmp`, so a release promotion writes the changelog into the link's
    target, and `os.replace` then moves the tmp PATH, leaving the link
    standing. Surfaced by the fail-closed sibling sweep (PB-0078 round 3).
    """

    def test_a_symlinked_target_or_tmp_is_refused_and_the_victim_holds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            victim = root / "victim.txt"
            victim.write_text("VICTIM\n", encoding="utf-8")
            target = root / "CHANGELOG.md"
            # Leg 1: the target itself is a planted symlink.
            target.symlink_to(victim)
            with self.assertRaises(OSError):
                MODULE._atomic_write_text(target, "# promoted\n")
            self.assertEqual(victim.read_text(encoding="utf-8"), "VICTIM\n")
            self.assertTrue(target.is_symlink())
            # Leg 2: the predictable tmp path is a planted symlink.
            target.unlink()
            (root / "CHANGELOG.md.tmp").symlink_to(victim)
            with self.assertRaises(OSError):
                MODULE._atomic_write_text(target, "# promoted\n")
            self.assertEqual(victim.read_text(encoding="utf-8"), "VICTIM\n")
            self.assertFalse(target.exists())


class AtomicWriteErrorPathTest(unittest.TestCase):
    """Cover the cleanup branch of _atomic_write_text when the write fails."""

    def test_original_unchanged_on_write_failure(self):
        tmp = Path(tempfile.mkdtemp(prefix="atomic-write-"))
        try:
            target = tmp / "CHANGELOG.md"
            original = "# original content — must survive failure\n"
            target.write_text(original, encoding="utf-8")

            # Inject a write failure by making the tmp parent un-writable in a
            # controlled way: monkey-patch Path.write_bytes on a *different* path
            # object via a tempfile inside a read-only directory. Simpler: just
            # call _atomic_write_text with a path whose parent doesn't exist —
            # the tmp suffix path can't be created, raising OSError.
            bogus = tmp / "no-such-dir" / "CHANGELOG.md"
            with self.assertRaises(Exception):
                MODULE._atomic_write_text(bogus, "should not arrive")
            # The original file is untouched.
            self.assertEqual(target.read_text(encoding="utf-8"), original)
            # No stray .tmp anywhere we would notice.
            self.assertFalse((tmp / "no-such-dir").exists())
        finally:
            for p in tmp.rglob("*"):
                if p.is_file():
                    p.unlink()
            for d in sorted(tmp.rglob("*"), reverse=True):
                if d.is_dir():
                    d.rmdir()
            tmp.rmdir()


class CliSymlinkedChangelogRefusalTests(unittest.TestCase):
    """[SECURITY:S5/NC6] The CLI entry point itself must reach the
    _atomic_write_text symlink guard. Regression for the pre-resolve-of-the-leaf
    bug: main() called args.changelog.resolve() before the guard, dereferencing a
    planted CHANGELOG.md symlink so the guard never fired and the promotion wrote
    straight through into the link's target at exit 0. AtomicWriteSymlinkRefusalTests
    proves the helper refuses in isolation; this proves main() actually reaches it.
    """

    def test_cli_refuses_a_symlinked_changelog_and_the_victim_holds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            victim = root / "victim.md"
            victim.write_text(SAMPLE_HAPPY, encoding="utf-8")
            original_victim = victim.read_text(encoding="utf-8")
            target = root / "CHANGELOG.md"
            target.symlink_to(victim)

            result = run_cli("--version", "0.4.0", "--changelog", str(target))

            # The write must be refused (non-zero exit) — before the fix this
            # promoted into the victim and returned 0.
            self.assertNotEqual(result.returncode, 0)
            # The file behind the symlink is untouched: no promoted heading, and
            # byte-for-byte identical to what it held before the run.
            after = victim.read_text(encoding="utf-8")
            self.assertEqual(after, original_victim)
            self.assertNotIn("## [0.4.0]", after)
            # The symlink itself still stands (os.replace never moved a tmp onto it).
            self.assertTrue(target.is_symlink())


if __name__ == "__main__":
    unittest.main()
