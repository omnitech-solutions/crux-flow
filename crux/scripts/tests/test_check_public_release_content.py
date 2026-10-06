"""Tests for check-public-release-content.py (ADR-0034 §4).

Exercises the scan MECHANICS on tmp-dir fixtures: zone-1 hits (ADR tokens
+ wiki-link form on distributed surfaces), zone-2 hits (banned name,
case-insensitive, repo-wide), the two-path self-allowlist, placeholder
non-matching, dir/suffix skipping, and exit-code semantics. Deliberately
does NOT scan the live repo — the live-tree gate runs at integration.

The banned name is constructed from parts so this file never contains it
literally (this file is one of the checker's two allowlisted paths).

Stdlib unittest only.
"""

from __future__ import annotations

import importlib.util
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPT_PATH = REPO_ROOT / "crux" / "scripts" / "check-public-release-content.py"
from _authoring_fixture import seed_authoring_probe


BANNED = "zi" + "ppy"


def _load_checker():
    spec = importlib.util.spec_from_file_location("check_public_release_content", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_public_release_content"] = mod
    spec.loader.exec_module(mod)
    return mod


checker = _load_checker()


class FixtureCase(unittest.TestCase):
    """Shared tmp-root scaffolding."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def write(self, rel: str, body: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        return path

    def scan(self) -> list[str]:
        return checker.scan(self.root)


class Zone1DistributedSurfaceTests(FixtureCase):
    def test_adr_token_in_skill_is_a_hit(self):
        self.write("crux/skills/foo/SKILL.md", "Decided per ADR-0021 long ago.\n")
        hits = self.scan()
        self.assertEqual(len(hits), 1)
        self.assertTrue(hits[0].startswith("crux/skills/foo/SKILL.md:1:"))

    def test_adr_token_in_agent_and_template_are_hits(self):
        self.write("crux/agents/architect.md", "See ADR-0034 for rationale.\n")
        self.write("crux/templates/ADR-template.md", "ok line\nrefs [[adrs/ADR-0001-slug]]\n")
        hits = self.scan()
        self.assertEqual(len(hits), 2)
        self.assertTrue(any(h.startswith("crux/agents/architect.md:1:") for h in hits))
        self.assertTrue(any(h.startswith("crux/templates/ADR-template.md:2:") for h in hits))

    def test_wikilink_form_without_digits_is_a_hit(self):
        # Slug-only link the token regex would miss — caught by the literal.
        self.write("crux/skills/foo/SKILL.md", "see [[adrs/some-decision-slug]] here\n")
        hits = self.scan()
        self.assertEqual(len(hits), 1)

    def test_placeholder_adr_nnnn_does_not_match(self):
        self.write("crux/skills/foo/SKILL.md", 'say "accept ADR-NNNN" to transition.\n')
        self.assertEqual(self.scan(), [])

    def test_adr_token_outside_distributed_surfaces_is_not_zone1(self):
        # Scripts, docs, etc. may reference ADRs freely (zone 1 only covers
        # the three distributed surfaces).
        self.write("crux/scripts/some-tool.py", "# per ADR-0034 strictness\n")
        self.write("docs/adrs/ADR-0001-x.md", "id: ADR-0001\n")
        self.assertEqual(self.scan(), [])

    def test_three_digit_or_five_digit_tokens_do_not_match(self):
        self.write("crux/skills/foo/SKILL.md", "ADR-001 and ADR-12345x are not 4-digit tokens\n")
        hits = self.scan()
        # ADR-12345 contains ADR-1234 as a prefix match — the contract is the
        # ADR-\d{4} token regex, which DOES match inside longer digit runs.
        # Only the genuinely-short form is a non-match.
        self.assertEqual(len(hits), 1)
        self.assertIn("ADR-12345x", hits[0])


class Zone2BannedNameTests(FixtureCase):
    def test_banned_name_anywhere_is_a_hit(self):
        self.write("README.md", f"derived from the {BANNED} workshop\n")
        hits = self.scan()
        self.assertEqual(len(hits), 1)
        self.assertTrue(hits[0].startswith("README.md:1:"))

    def test_match_is_case_insensitive(self):
        self.write("notes.md", f"The {BANNED.upper()} era.\n")
        self.write("more.md", f"{BANNED.capitalize()}LoopRunner lives on\n")
        self.assertEqual(len(self.scan()), 2)

    def test_banned_name_inside_identifiers_matches(self):
        self.write("crux/scripts/tool.py", f"X = '{BANNED}_traces'\n")
        self.assertEqual(len(self.scan()), 1)

    def test_clean_tree_is_clean(self):
        self.write("README.md", "a perfectly ordinary file\n")
        self.write("crux/skills/foo/SKILL.md", "no references here\n")
        self.assertEqual(self.scan(), [])

    def test_docs_research_raw_is_in_scope(self):
        self.write("docs/research/raw/2026-01-01/cap/page.md", f"{BANNED} mention\n")
        self.assertEqual(len(self.scan()), 1)


class SelfAllowlistTests(FixtureCase):
    def test_checker_and_test_paths_are_exempt_from_zone2(self):
        self.write("crux/scripts/check-public-release-content.py", f"_B = '{BANNED}'\n")
        self.write(
            "crux/scripts/tests/test_check_public_release_content.py",
            f"BANNED = '{BANNED}'\n",
        )
        self.assertEqual(self.scan(), [])

    def test_allowlist_is_exactly_two_paths(self):
        self.assertEqual(
            checker._SELF_ALLOWLIST,
            frozenset(
                {
                    "crux/scripts/check-public-release-content.py",
                    "crux/scripts/tests/test_check_public_release_content.py",
                }
            ),
        )

    def test_similarly_named_file_elsewhere_is_not_exempt(self):
        self.write("tools/check-public-release-content.py", f"# {BANNED}\n")
        self.assertEqual(len(self.scan()), 1)

    def test_checker_source_never_spells_the_name(self):
        """The real checker builds the pattern from parts — the 5 letters
        must not appear contiguously in its source."""
        src = SCRIPT_PATH.read_text(encoding="utf-8")
        self.assertNotIn(BANNED, src.lower())


class SkippingTests(FixtureCase):
    def test_skip_dirs_are_skipped(self):
        for d in (".git", ".claude", ".venv", "node_modules", "__pycache__", "dist", "build"):
            self.write(f"{d}/hit.md", f"{BANNED}\n")
        self.assertEqual(self.scan(), [])

    def test_non_text_suffixes_are_skipped(self):
        self.write("image.png", f"{BANNED}\n")
        self.write("data.bin", f"{BANNED}\n")
        self.assertEqual(self.scan(), [])

    def test_makefile_and_dockerfile_are_scanned(self):
        self.write("Makefile", f"# {BANNED}\n")
        self.assertEqual(len(self.scan()), 1)


class MainExitCodeTests(FixtureCase):
    """`main` on a fixture that IS the plugin's authoring checkout.

    rule:out-of-scope-is-surface-absent. `main` now declines a root holding no
    plugin source, because run from a consuming project it used to scan the
    INSTALLED plugin and report that verdict as the project's. Seeding the probe
    makes the fixture model what these tests claim to drive; without it every
    assertion below would pass through the decline lane and measure nothing.
    """

    def setUp(self):
        super().setUp()
        seed_authoring_probe(self.root, SCRIPT_PATH)

    def test_a_root_without_plugin_source_is_declined_not_scanned(self):
        """The control for the seeding above: the decline lane exists and is real."""
        other = Path(self._tmp.name) / "consumer"
        (other / "docs").mkdir(parents=True)
        (other / "README.md").write_text(f"{BANNED}\n", encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = checker.main(["--root", str(other)])
        self.assertEqual(rc, 0)
        self.assertEqual(out.getvalue(), "")
        self.assertIn("not the plugin's authoring checkout", err.getvalue())

    def _run_main(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = checker.main(["--root", str(self.root), *argv])
        return rc, out.getvalue(), err.getvalue()

    def test_exit_0_and_silent_when_clean(self):
        self.write("README.md", "clean\n")
        rc, out, err = self._run_main()
        self.assertEqual(rc, 0)
        self.assertEqual(out, "")
        self.assertEqual(err, "")

    def test_verbose_summary_on_success(self):
        self.write("README.md", "clean\n")
        rc, _, err = self._run_main("--verbose")
        self.assertEqual(rc, 0)
        self.assertIn("clean", err)

    def test_exit_1_with_rows_on_findings(self):
        self.write("README.md", f"{BANNED} one\n")
        self.write("crux/skills/foo/SKILL.md", "per ADR-0008\n")
        rc, out, err = self._run_main()
        self.assertEqual(rc, 1)
        rows = out.strip().splitlines()
        self.assertEqual(len(rows), 2)
        for row in rows:
            # `<path>:<lineno>: <line>` shape.
            path, lineno, rest = row.split(":", 2)
            self.assertTrue(path)
            self.assertTrue(lineno.isdigit())
            self.assertTrue(rest.startswith(" "))
        self.assertIn("2 finding(s)", err)

    def test_rows_are_sorted_and_deduplicated(self):
        self.write("b.md", f"{BANNED}\n")
        self.write("a.md", f"{BANNED}\n")
        _, out, _ = self._run_main()
        rows = out.strip().splitlines()
        self.assertEqual(rows, sorted(rows))


class MetaAdrSelfIdentityTests(FixtureCase):
    """The shipped meta-ADR template's exemption is token-level, not
    file-level: only pure ADR-0000 tokens pass; any other numbered token or
    the wiki-link form in that same file is still flagged."""

    META = "crux/templates/ADR-0000-record-architecture-decisions.md"

    def test_pure_identity_tokens_are_exempt(self):
        self.write(self.META, "---\nid: ADR-0000\n---\n# ADR-0000 — Record architecture decisions\n")
        self.assertEqual(self.scan(), [])

    def test_other_adr_token_in_meta_adr_is_still_a_hit(self):
        self.write(self.META, "id: ADR-0000\n\nSee also ADR-0005 for the schema.\n")
        hits = self.scan()
        self.assertEqual(len(hits), 1)
        self.assertIn("ADR-0005", hits[0])

    def test_wikilink_form_in_meta_adr_is_still_a_hit(self):
        self.write(self.META, "id: ADR-0000\n\n[[adrs/ADR-0000-record-architecture-decisions]]\n")
        hits = self.scan()
        self.assertEqual(len(hits), 1)

    def test_mixed_line_with_identity_and_other_token_is_a_hit(self):
        self.write(self.META, "ADR-0000 supersedes ADR-0001 here\n")
        hits = self.scan()
        self.assertEqual(len(hits), 1)


class ZoneIndependenceAndSymlinkTests(FixtureCase):
    """Regression guards from the PB-0028 review: the self-identity
    exemption must not shadow zone-2, and file symlinks are never read."""

    META = "crux/templates/ADR-0000-record-architecture-decisions.md"

    def test_banned_name_on_exempt_identity_line_is_still_zone2_hit(self):
        self.write(self.META, "id: ADR-0000 " + BANNED + "\n")
        hits = self.scan()
        self.assertEqual(len(hits), 1)
        self.assertIn(BANNED, hits[0])

    def test_zone1_hit_line_with_banned_name_reports_both_zones_once_each(self):
        self.write("crux/skills/x/SKILL.md", "see ADR-0042 and " + BANNED + "\n")
        hits = self.scan()
        # one deduplicated row carrying both violations
        self.assertEqual(len(hits), 1)

    def test_file_symlink_is_never_read(self):
        import tempfile
        outside = Path(tempfile.mkdtemp()) / "outside.md"
        outside.write_text(BANNED + "\n", encoding="utf-8")
        link = self.root / "crux" / "skills" / "leak.md"
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(outside)
        self.assertEqual(self.scan(), [])


if __name__ == "__main__":
    unittest.main()
