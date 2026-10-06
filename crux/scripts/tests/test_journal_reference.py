"""The journal-reference decision behind `cleanup-campsite` CLN-JR-1.

CLN-JR-1 asks whether a journal entry reflects on an artifact. It used to ask
only whether an entry carried a `[[wiki-link]]` to it. Writing rule 7 tells the
journal to cite `rule:<slug>` where a rule exists and to name the ADR only
where it carries no `governs` block, so an ADR with a `governs` block could not
satisfy both contracts. Three reflections were reported missing while sitting
in the tree.

Run:
  uv run python3 -m unittest discover -s crux/scripts/tests -p 'test_journal_reference.py'
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
SCRIPT = SCRIPTS / "check-journal-reference.py"
sys.path.insert(0, str(SCRIPTS))

from journal_reference import artifact_references  # noqa: E402

# A resolver shaped like the real projection: handle keys, plus the `slugs`
# and `retired_slugs` maps `summarize-adrs.py` emits beside them.
RESOLVER = {
    "ADR-0111/personal-agent-scope": {"source_adr": "ADR-0111", "rule": "…"},
    "ADR-0113/per-agent-codex-runtime-override": {"source_adr": "ADR-0113", "rule": "…"},
    "slugs": {
        "personal-agent-scope": "ADR-0111/personal-agent-scope",
        "per-agent-codex-runtime-override": "ADR-0113/per-agent-codex-runtime-override",
    },
    "retired_slugs": {
        "codex-agents-regenerated-never-hand-edited": [
            "ADR-0112/personal-codex-agents-are-dogfood"],
    },
}


def entry(refs: str) -> str:
    return ("## [2026-09-11 08:04] decision | a reflection\n\n"
            "One body line.\n\n"
            f"Refs: {refs}\n")


class WikiLinkEvidenceTests(unittest.TestCase):
    """The original evidence kind, which must keep working unchanged."""

    def test_a_wiki_link_to_the_adr_is_evidence(self):
        text = entry("[[adrs/ADR-0111-install-personal-codex-agents-with-pinned-settings]]")
        got = artifact_references(text, "ADR-0111", RESOLVER)
        self.assertTrue(got["referenced"])
        self.assertEqual([e["kind"] for e in got["evidence"]], ["wikilink"])

    def test_a_wiki_link_to_a_promptbook_is_evidence_for_that_promptbook(self):
        text = entry("[[promptbooks/PB-0102-codex-reviewer-sol-xhigh]]")
        self.assertTrue(
            artifact_references(text, "PB-0102-codex-reviewer-sol-xhigh", RESOLVER)["referenced"])

    def test_a_wiki_link_to_a_different_adr_is_not_evidence(self):
        text = entry("[[adrs/ADR-0113-allow-per-agent-codex-runtime-overrides]]")
        self.assertFalse(artifact_references(text, "ADR-0111", RESOLVER)["referenced"])

    def test_a_longer_id_does_not_satisfy_a_shorter_one(self):
        # `ADR-0111` must not be satisfied by a link naming `ADR-01110`.
        text = entry("[[adrs/ADR-01110-something-else]]")
        self.assertFalse(artifact_references(text, "ADR-0111", RESOLVER)["referenced"])

    def test_the_bare_id_in_prose_is_not_a_wiki_link(self):
        # Rule 7 bars an inline ADR number in prose; prose is not a citation.
        text = entry("none") .replace("Refs: none", "") + "We revisited ADR-0111 today.\n"
        self.assertFalse(artifact_references(text, "ADR-0111", RESOLVER)["referenced"])


class ResolvedSlugEvidenceTests(unittest.TestCase):
    """A `rule:<slug>` counts only when the resolver maps it to THIS artifact."""

    def test_a_slug_resolving_to_the_adr_is_evidence(self):
        got = artifact_references(entry("rule:personal-agent-scope"), "ADR-0111", RESOLVER)
        self.assertTrue(got["referenced"])
        self.assertEqual(got["evidence"][0]["kind"], "resolved-slug")
        self.assertEqual(got["evidence"][0]["token"], "personal-agent-scope")

    def test_a_slug_belonging_to_another_adr_is_not_evidence(self):
        got = artifact_references(
            entry("rule:per-agent-codex-runtime-override"), "ADR-0111", RESOLVER)
        self.assertFalse(got["referenced"])
        self.assertEqual(got["rejected"][0]["kind"], "foreign-slug")
        self.assertEqual(got["rejected"][0]["resolves_to"], "ADR-0113")

    def test_an_unresolved_slug_is_not_evidence(self):
        # Mere slug presence is insufficient: a slug the resolver does not carry
        # proves nothing about which decision the entry reflects on.
        got = artifact_references(entry("rule:invented-slug"), "ADR-0111", RESOLVER)
        self.assertFalse(got["referenced"])
        self.assertEqual(got["rejected"][0]["kind"], "unresolved-slug")

    def test_a_retired_slug_whose_successor_is_the_adr_is_evidence(self):
        got = artifact_references(
            entry("rule:codex-agents-regenerated-never-hand-edited"), "ADR-0112", RESOLVER)
        self.assertTrue(got["referenced"])
        self.assertEqual(got["evidence"][0]["kind"], "retired-slug")

    def test_a_retired_slug_whose_successor_is_another_adr_is_not_evidence(self):
        got = artifact_references(
            entry("rule:codex-agents-regenerated-never-hand-edited"), "ADR-0111", RESOLVER)
        self.assertFalse(got["referenced"])
        self.assertEqual(got["rejected"][0]["kind"], "foreign-slug")

    def test_a_slug_inside_a_fenced_block_is_content_not_a_citation(self):
        text = ("## [2026-09-11 08:04] decision | a reflection\n\n"
                "```\nRefs: rule:personal-agent-scope\n```\n")
        self.assertFalse(artifact_references(text, "ADR-0111", RESOLVER)["referenced"])


class AbsentResolverTests(unittest.TestCase):
    """A tree with no summaries projection cannot check slug evidence.

    `init-docs` creates no projection, so this is the normal downstream state
    rather than an error. The decision falls back to wiki-links and SAYS the
    slug lane was unverifiable, so a caller can tell "no reflection" from
    "could not check".
    """

    def test_a_wiki_link_still_decides_without_a_resolver(self):
        text = entry("[[adrs/ADR-0111-install-personal-codex-agents-with-pinned-settings]]")
        got = artifact_references(text, "ADR-0111", None)
        self.assertTrue(got["referenced"])
        self.assertFalse(got["resolver_available"])

    def test_a_slug_is_reported_unverifiable_rather_than_rejected(self):
        got = artifact_references(entry("rule:personal-agent-scope"), "ADR-0111", None)
        self.assertFalse(got["referenced"])
        self.assertEqual(got["rejected"], [])
        self.assertEqual([u["token"] for u in got["unverifiable"]], ["personal-agent-scope"])


class LiveTreeTests(unittest.TestCase):
    """The three reflections CLN-JR-1 reported missing satisfy the repair.

    Checked against the real journal and the real resolver, not fixtures, and
    WITHOUT consulting `whats_next.md` — a dismissal records a judgement, and
    this asserts the evidence the judgement was based on.
    """

    CASES = {"ADR-0111": "personal-agent-scope",
             "ADR-0112": "personal-codex-agents-are-dogfood",
             "ADR-0113": "per-agent-codex-runtime-override"}

    def setUp(self):
        self.repo = SCRIPTS.parent.parent
        if not (self.repo / "bionic" / "journal" / "2026-09.md").is_file():
            self.skipTest("no dogfood journal tree in this checkout")

    def test_each_reflection_is_found_by_its_resolved_slug(self):
        journal = (self.repo / "bionic" / "journal" / "2026-09.md").read_text(encoding="utf-8")
        resolver = json.loads(
            (self.repo / "bionic" / "adrs" / "summaries" / "resolver.json").read_text(encoding="utf-8"))
        for adr, slug in self.CASES.items():
            with self.subTest(adr=adr):
                got = artifact_references(journal, adr, resolver)
                self.assertTrue(got["referenced"], got)
                kinds = {e["kind"] for e in got["evidence"]}
                self.assertEqual(kinds, {"resolved-slug"},
                                 f"{adr} should be found by slug, not by wiki-link: {got}")
                self.assertIn(slug, [e["token"] for e in got["evidence"]])

    def test_no_journal_entry_wiki_links_any_of_the_three(self):
        # PAIRED CONTROL: the old detector really did have nothing to find, so
        # the repair is what closes the finding rather than a fixture quirk.
        journal = (self.repo / "bionic" / "journal" / "2026-09.md").read_text(encoding="utf-8")
        for adr in self.CASES:
            with self.subTest(adr=adr):
                got = artifact_references(journal, adr, None)
                self.assertFalse(got["referenced"], got)


class CliTests(unittest.TestCase):

    def run_cli(self, root: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SCRIPT), "--repo-root", str(root), *args],
                              capture_output=True, timeout=60)

    def make_tree(self, journal: str, resolver: dict | None) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / ".bionic.yml").write_text('config_version: "1"\ndocs_dir: bionic\n', encoding="utf-8")
        docs = root / "bionic"
        (docs / "journal").mkdir(parents=True)
        (docs / "manifest.yml").write_text(
            'schema_version: "5"\nconcerns_enabled: [journal]\n', encoding="utf-8")
        (docs / "journal" / "2026-09.md").write_text(journal, encoding="utf-8")
        if resolver is not None:
            (docs / "adrs" / "summaries").mkdir(parents=True)
            (docs / "adrs" / "summaries" / "resolver.json").write_text(
                json.dumps(resolver), encoding="utf-8")
        return root

    def test_a_referenced_artifact_exits_0(self):
        root = self.make_tree(entry("rule:personal-agent-scope"), RESOLVER)
        r = self.run_cli(root, "--artifact", "ADR-0111", "--month", "2026-09")
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        self.assertTrue(json.loads(r.stdout)["referenced"])

    def test_an_unreferenced_artifact_exits_1_with_the_rejection_named(self):
        root = self.make_tree(entry("rule:per-agent-codex-runtime-override"), RESOLVER)
        r = self.run_cli(root, "--artifact", "ADR-0111", "--month", "2026-09")
        self.assertEqual(r.returncode, 1, r.stdout.decode())
        payload = json.loads(r.stdout)
        self.assertFalse(payload["referenced"])
        self.assertEqual(payload["rejected"][0]["resolves_to"], "ADR-0113")

    def test_an_absent_resolver_is_reported_not_an_error(self):
        root = self.make_tree(entry("rule:personal-agent-scope"), None)
        r = self.run_cli(root, "--artifact", "ADR-0111", "--month", "2026-09")
        self.assertEqual(r.returncode, 1, r.stderr.decode())
        payload = json.loads(r.stdout)
        self.assertFalse(payload["resolver_available"])
        self.assertEqual(len(payload["unverifiable"]), 1)

    def test_an_absent_month_file_is_an_environment_error(self):
        root = self.make_tree(entry("rule:personal-agent-scope"), RESOLVER)
        r = self.run_cli(root, "--artifact", "ADR-0111", "--month", "2026-08")
        self.assertEqual(r.returncode, 2, r.stdout.decode())


if __name__ == "__main__":
    unittest.main()
