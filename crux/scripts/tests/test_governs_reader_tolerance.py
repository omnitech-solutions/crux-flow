"""Guard tests for the `governs` frontmatter block's reader tolerance.

ADR-0085 adds an optional, structured `governs` block to ADR frontmatter (one
row in the canonical schema, docs/AGENTS.md §11.A). This is NOT the summaries
regenerator that will one day read `governs` for real — it is the guard that
proves every EXISTING reader stays inert to it:

  1. `crux.arch.derive._frontmatter` parses a governs-bearing ADR into a dict
     on both the real-PyYAML path and the stdlib-fallback path, without
     raising on either.
  2. The three ADR-frontmatter projections — `generate-adr-index.py`,
     `generate-index-rollup.py`, `generate-lineage.py` — produce BYTE-
     IDENTICAL output for an ADR set with vs. without a `governs` block,
     because none of the three reads that key today.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]  # crux/scripts
sys.path.insert(0, str(SCRIPTS))

# ── derive.py: import the real package module, per the established idiom
#    (see test_derive_arch.py) ────────────────────────────────────────────
D = importlib.import_module("crux.arch.derive")


def _load_hyphenated(name: str, filename: str):
    """Load a hyphen-named vendored script as a module (it can't be `import`ed
    by dotted name). Same idiom as test_adr_archive.py's `_load_module`."""
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GAI = _load_hyphenated("gen_adr_index_grt", "generate-adr-index.py")
GIR = _load_hyphenated("gen_index_rollup_grt", "generate-index-rollup.py")
GL = _load_hyphenated("gen_lineage_grt", "generate-lineage.py")


# ── a small in-test ADR frontmatter fixture, with and without `governs` ────

_GOVERNS_BLOCK = """\
governs:
  - domain: decision-doctrine
    rule: "each ADR's governing rule is authored once in a governs block"
    scope: bionic/adrs
    handle: ADR-0099/governs-block-shape
    provenance: authored
"""


def _adr_frontmatter(num: int, *, with_governs: bool) -> str:
    lines = [
        "---",
        f"id: ADR-{num:04d}",
        f'title: "Test decision {num}"',
        "status: Accepted",
        "date: 2026-08-26",
        "supersedes: []",
        "superseded_by: null",
        "tags: [governs, test]",
    ]
    if with_governs:
        lines.append(_GOVERNS_BLOCK.rstrip("\n"))
    lines += ["---", "", f"# ADR-{num:04d} — Test decision {num}", "", "## Context", "", "Test.", ""]
    return "\n".join(lines) + "\n"


class FrontmatterGovernsToleranceTests(unittest.TestCase):
    """Assertion (1): `_frontmatter` tolerates a governs block on both parse
    paths."""

    def test_real_pyyaml_path_parses_governs_bearing_adr(self):
        text = _adr_frontmatter(99, with_governs=True)
        fm = D._frontmatter(text)
        self.assertIsInstance(fm, dict)
        self.assertEqual(fm.get("id"), "ADR-0099")
        self.assertEqual(fm.get("title"), "Test decision 99")
        self.assertEqual(fm.get("status"), "Accepted")
        # governs parses as a real nested structure on the PyYAML path.
        governs = fm.get("governs")
        self.assertIsInstance(governs, list)
        self.assertEqual(len(governs), 1)
        entry = governs[0]
        self.assertEqual(
            set(entry.keys()), {"domain", "rule", "scope", "handle", "provenance"}
        )
        self.assertEqual(entry["handle"], "ADR-0099/governs-block-shape")
        self.assertEqual(entry["provenance"], "authored")

    def test_fallback_path_does_not_raise_on_governs_bearing_adr(self):
        text = _adr_frontmatter(99, with_governs=True)

        had_yaml = "yaml" in sys.modules
        orig_yaml = sys.modules.get("yaml")
        # Force `import yaml` inside `_frontmatter` to raise ImportError:
        # sys.modules['yaml'] = None makes the import machinery raise rather
        # than resolve, without needing to uninstall the real dependency.
        sys.modules["yaml"] = None
        try:
            fm = D._frontmatter(text)
        finally:
            if had_yaml:
                sys.modules["yaml"] = orig_yaml
            else:
                del sys.modules["yaml"]

        # No crash, and still a dict; the fallback degrades gracefully rather
        # than reconstructing the nested list.
        self.assertIsInstance(fm, dict)
        self.assertEqual(fm.get("id"), "ADR-0099")
        self.assertEqual(fm.get("title"), "Test decision 99")
        self.assertEqual(fm.get("status"), "Accepted")
        # The stdlib fallback's line regex `^([A-Za-z0-9_]+)\\s*:\\s*(.*)$`
        # matches the top-level `governs:` line (empty value) but skips the
        # indented `- domain:` / nested lines, since those don't start at
        # column 0. The key point is that this degrades silently: no crash,
        # no partial reconstruction of the nested mapping.
        self.assertEqual(fm.get("governs"), "")


class ProjectionsInertToGovernsTests(unittest.TestCase):
    """Assertion (2): the three ADR-frontmatter projections produce identical
    output whether or not the ADR set carries a `governs` block."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / ".bionic.yml").write_text("docs_dir: bionic\n", encoding="utf-8")
        self.adrs_dir = self.root / "bionic" / "adrs"
        self.adrs_dir.mkdir(parents=True)

    def _write_adrs(self, *, with_governs: bool) -> None:
        (self.adrs_dir / "ADR-0001-first.md").write_text(
            _adr_frontmatter(1, with_governs=with_governs), encoding="utf-8"
        )
        (self.adrs_dir / "ADR-0099-test-decision.md").write_text(
            _adr_frontmatter(99, with_governs=with_governs), encoding="utf-8"
        )

    def test_generate_adr_index_inert_to_governs(self):
        self._write_adrs(with_governs=False)
        _, without_governs = GAI.build(self.root)
        self._write_adrs(with_governs=True)
        _, with_governs = GAI.build(self.root)
        self.assertEqual(without_governs, with_governs)

    def test_generate_index_rollup_inert_to_governs(self):
        self._write_adrs(with_governs=False)
        without_governs = GIR.render_section(GIR.load_adrs(self.adrs_dir))
        self._write_adrs(with_governs=True)
        with_governs = GIR.render_section(GIR.load_adrs(self.adrs_dir))
        self.assertEqual(without_governs, with_governs)

    def test_generate_lineage_inert_to_governs(self):
        self._write_adrs(with_governs=False)
        without_governs = GL.render(GL._load_adrs(self.adrs_dir))
        self._write_adrs(with_governs=True)
        with_governs = GL.render(GL._load_adrs(self.adrs_dir))
        self.assertEqual(without_governs, with_governs)


if __name__ == "__main__":
    unittest.main()
