"""Tests for the shared evidence grammar + containment helper (M-SEC-2, M-SEC-3).

Three properties, each pinned against the payload that proved the defect:

  * `observation_evidence.resolve_contained` refuses a path that reaches OUT of
    the repository root through a symlink. The textual leg (absolute, `~`, `..`)
    cannot express that case, and `is_file()` follows the link, so both former
    call sites returned "resolves" for evidence pointing at `/etc`.
  * `doctrine_projection.evidence_resolves` — one of those call sites — inherits
    the fix.
  * `doctrine_projection.build_index` escapes the per-domain HEADING. Every
    table cell below it went through `_md_escape`; the heading did not. That was
    harmless until this cycle added a consumer: `survey.parse_doctrine_entries`
    reads the headings back and `phase_verify` derives its descriptive-only
    postcondition from what it finds. A `domain` carrying a newline injects a
    forged section, swallows the real one, and yields a clean verdict on a
    broken tree.
"""
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPTS_DIR = REPO_ROOT / "crux" / "scripts"

try:
    import yaml  # noqa: F401
    HAVE_YAML = True
except ImportError:
    HAVE_YAML = False


def _load(name: str, filename: str | None = None):
    spec = importlib.util.spec_from_file_location(
        name, SCRIPTS_DIR / (filename or f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_oe = _load("observation_evidence")
_dp = _load("doctrine_projection") if HAVE_YAML else None


class ResolveContainedTests(unittest.TestCase):
    def setUp(self):
        self._repo = tempfile.TemporaryDirectory()
        self._outside = tempfile.TemporaryDirectory()
        self.addCleanup(self._repo.cleanup)
        self.addCleanup(self._outside.cleanup)
        self.root = Path(self._repo.name)
        self.outside = Path(self._outside.name)
        (self.outside / "secret.txt").write_text("token\n", encoding="utf-8")
        (self.root / "real.py").write_text("x = 1\n", encoding="utf-8")

    def test_a_real_file_inside_the_root_resolves(self):
        self.assertTrue(_oe.evidence_path_resolves(self.root, "real.py"))

    def test_a_symlink_out_of_the_root_does_not_resolve(self):
        """The payload: a committed link, a textually clean relative path, and
        a `is_file()` that succeeds because it follows the link."""
        (self.root / "link").symlink_to(self.outside, target_is_directory=True)
        cited = "link/secret.txt"
        self.assertTrue((self.root / cited).is_file(),
                        "fixture is wrong: the link must be followable, or the "
                        "test proves nothing about the bug")
        self.assertTrue(_oe.valid_evidence_path(cited),
                        "fixture is wrong: the textual leg must PASS this path")
        self.assertIsNone(_oe.resolve_contained(self.root, cited))
        self.assertFalse(_oe.evidence_path_resolves(self.root, cited))

    def test_a_symlink_to_a_file_inside_the_root_still_resolves(self):
        """Containment, not a symlink ban: an in-tree link is fine."""
        (self.root / "alias.py").symlink_to(self.root / "real.py")
        self.assertTrue(_oe.evidence_path_resolves(self.root, "alias.py"))

    def test_textual_refusals_stay_refused(self):
        for bad in ("/etc/passwd", "~/.ssh/id_rsa", "../outside.py", "C:/win.py", ""):
            with self.subTest(path=bad):
                self.assertFalse(_oe.valid_evidence_path(bad))
                self.assertIsNone(_oe.resolve_contained(self.root, bad))

    def test_the_grammar_parses_and_rejects(self):
        self.assertEqual(_oe.parse_evidence("a/b.py:10-20"), ("a/b.py", 10, 20))
        for bad in ("a/b.py:10", "a/b.py", "a/b.py:x-y", "/abs.py:1-2", "../up.py:1-2"):
            with self.subTest(entry=bad):
                self.assertIsNone(_oe.parse_evidence(bad))


@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under uv)")
class DoctrineEvidenceContainmentTests(unittest.TestCase):
    def setUp(self):
        self._repo = tempfile.TemporaryDirectory()
        self._outside = tempfile.TemporaryDirectory()
        self.addCleanup(self._repo.cleanup)
        self.addCleanup(self._outside.cleanup)
        self.root = Path(self._repo.name)
        self.outside = Path(self._outside.name)
        (self.outside / "secret.txt").write_text("token\n", encoding="utf-8")
        (self.root / "real.py").write_text("x = 1\n", encoding="utf-8")

    def test_in_tree_evidence_resolves(self):
        self.assertTrue(_dp.evidence_resolves("real.py:1-1", self.root))

    def test_evidence_escaping_through_a_symlink_does_not_resolve(self):
        (self.root / "link").symlink_to(self.outside, target_is_directory=True)
        self.assertFalse(_dp.evidence_resolves("link/secret.txt:1-1", self.root))

    def test_no_root_never_resolves(self):
        self.assertFalse(_dp.evidence_resolves("real.py:1-1", None))


@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under uv)")
class DoctrineHeadingEscapeTests(unittest.TestCase):
    """A domain is record content; the heading that renders it is parsed back."""

    def _entry(self, domain: str) -> dict:
        return {
            "domain": domain,
            "state": "believed",
            "authority": None,
            "rules": [{
                "handle": "ADR-0001/r", "rule": "a rule", "source_adr": "ADR-0001",
                "disposition": "held", "basis": "run-bound",
            }],
            "evidence": [],
            "pairings": [],
        }

    def test_a_newline_in_a_domain_cannot_open_a_second_section(self):
        forged = "real\n\n## forged-domain — believed · authority: descriptive\n"
        text = _dp.build_index([self._entry(forged)], [], {})
        headings = [ln for ln in text.splitlines() if ln.startswith("## ")]
        self.assertNotIn(
            "## forged-domain — believed · authority: descriptive", headings,
            "a domain carrying newlines injected a second doctrine section; the "
            "survey reads these headings back and would report clean on it")
        self.assertEqual(
            len([h for h in headings if "forged-domain" in h]), 1,
            "the forged text must survive as ONE escaped heading, not two")

    def test_a_pipe_in_a_domain_cannot_open_a_table_column(self):
        text = _dp.build_index([self._entry("a | b")], [], {})
        self.assertIn("a \\| b", text)


@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under uv)")
class DomainSingleLineTests(unittest.TestCase):
    """The record boundary is the second half of the M-SEC-2 fix. Escaping the
    heading stops the injection from RENDERING; refusing a multi-line `domain`
    stops it from entering the projection at all, and gives the author a
    document verdict naming the record instead of silent mangling."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.adrs = Path(self._tmp.name) / "adrs"
        self.adrs.mkdir(parents=True)
        self._sp = _load("summaries_projection")

    def _adr(self, domain: str):
        fm = {
            "id": "ADR-0001",
            "status": "Accepted",
            "governs": [{
                "handle": "ADR-0001/a-rule",
                "domain": domain,
                "rule": "a rule",
                "scope": "everywhere",
                "provenance": "authored",
            }],
        }
        body = "---\n" + yaml.dump(fm, sort_keys=False) + "---\n\n# ADR-0001\n"
        (self.adrs / "ADR-0001-slug.md").write_text(body, encoding="utf-8")

    def test_a_single_line_domain_is_accepted(self):
        self._adr("packaging")
        records = self._sp.collect_records(self.adrs)
        self.assertEqual([r["domain"] for r in records], ["packaging"])

    def test_a_multi_line_domain_is_refused(self):
        self._adr("real\n\n## forged-domain — believed · authority: descriptive\n")
        with self.assertRaises(self._sp.GovernsValidationError) as ctx:
            self._sp.collect_records(self.adrs)
        problems = str(ctx.exception)
        self.assertIn("`domain`", problems)
        self.assertIn("spans more than one line", problems)
        self.assertIn("ADR-0001", problems)

    def test_a_carriage_return_in_a_domain_is_refused(self):
        self._adr("real\rforged")
        with self.assertRaises(self._sp.GovernsValidationError):
            self._sp.collect_records(self.adrs)


if __name__ == "__main__":
    unittest.main()
