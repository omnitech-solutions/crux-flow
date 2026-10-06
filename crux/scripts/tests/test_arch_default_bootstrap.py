"""Test for ADR-0065 acceptance criterion 3 — the arch-default bootstrap fixed point.

ADR-0065 ("Enable the arch concern by default in init-docs for new repositories")
criterion 3 requires that an actual freshly-init'd, arch-on scaffold reaches an
audit-clean fixed point after its first derive: `derive-arch` writes the spine, and
a subsequent `--dry-run` reports no drift.

This test encodes that criterion in-process (per council finding W1 — CI hygiene):
it builds a fresh arch-on tree fixture in a temp dir mimicking what init-docs now
produces, calls the derive engine's `derive`/`dry_run` functions IN-PROCESS (never
`uv run .../derive-arch.py`, whose PEP 723 block would trigger network dep
resolution → flaky CI), and asserts the fixed point plus the downstream thin-spine
shape (three `no extractor` markers + one meta-ADR decision-index row).

Import style, temp-tree creation, and module-call conventions mirror the sibling
`test_derive_arch.py`.
"""

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]      # crux/scripts
sys.path.insert(0, str(SCRIPTS))

import importlib  # noqa: E402
D = importlib.import_module("crux.arch.derive")     # the module, not the re-exported fn


def _tmpdir():
    import tempfile
    return tempfile.TemporaryDirectory()


class ArchDefaultBootstrapTests(unittest.TestCase):
    """Criterion 3: a fresh arch-on tree is audit-clean after one derive."""

    # The eight-entry default init-docs now emits (seven established + arch).
    DEFAULT_CONCERNS = [
        "code", "research", "adrs", "briefs",
        "journal", "promptbooks", "invariants", "arch",
    ]

    def _fresh_tree(self) -> Path:
        """Build a fresh arch-on tree fixture mimicking init-docs' output."""
        root = Path(self.enterContext(_tmpdir()))
        bionic = root / "bionic"
        (bionic / "adrs").mkdir(parents=True)
        (bionic / "arch").mkdir(parents=True)

        # manifest.yml — schema_version "5", arch in the eight-entry concerns_enabled.
        concerns = "\n".join(f"  - {c}" for c in self.DEFAULT_CONCERNS)
        (bionic / "manifest.yml").write_text(
            'schema_version: "5"\n'
            'concerns_enabled:\n' + concerns + '\n'
            'adr:\n  next_number: 1\n'
            'promptbook:\n  next_number: 1\n',
            encoding="utf-8",
        )

        # The bootstrap meta-ADR — valid frontmatter, status Accepted, id + title,
        # so the decision-index extractor parses it deterministically.
        (bionic / "adrs" / "ADR-0000-record-architecture-decisions.md").write_text(
            "---\n"
            "id: ADR-0000\n"
            'title: "Record architectural decisions as ADRs"\n'
            "status: Accepted\n"
            "date: 2026-08-16\n"
            "proposed_date: 2026-08-16\n"
            "accepted_date: 2026-08-16\n"
            "deciders: [\"mark\"]\n"
            "tags: [meta, process]\n"
            "---\n\n"
            "# ADR-0000 — Record architectural decisions as ADRs\n\n"
            "The bootstrap meta-ADR shipped with every fresh tree.\n",
            encoding="utf-8",
        )

        # arch/index.md placeholder, matching the init-docs scaffold (deferred derive).
        (bionic / "arch" / "index.md").write_text(
            "# arch\n\n_Placeholder — spine built on first derive._\n",
            encoding="utf-8",
        )
        return root

    def test_first_derive_writes_full_tree(self):
        """The first derive writes the four spine files + overview + index + manifest."""
        root = self._fresh_tree()
        written = D.derive(root, "bionic")
        arch = root / "bionic" / "arch"
        for f in D.SPINE_FILES + ["overview.md", "index.md", "_meta/manifest.json"]:
            p = arch / f
            self.assertTrue(p.exists(), f"derive did not write {f}")
            self.assertIn(D._rel(root, p), written, f"{f} missing from written list")

    def test_dry_run_clean_after_first_derive(self):
        """Criterion 3 core: after one derive, dry_run reports [] (fixed point)."""
        root = self._fresh_tree()
        D.derive(root, "bionic")
        self.assertEqual(D.dry_run(root, "bionic"), [], "dry_run dirty after first derive")

    def test_downstream_thin_spine_markers(self):
        """No crux/ paths → api-surface / module-graph / data-model carry a stub line.

        A bare tree matches no pack marker, so the empty stub pack resolves and
        these three concerns have no extractor at all. That is the ONE condition
        ADR-0096 clause 4 lets the reserved sentence describe, and this fixture
        is where it is true: `decision-index` is excluded below because it is the
        universal probe, which runs here and stubs for a different reason.
        """
        root = self._fresh_tree()
        D.derive(root, "bionic")
        arch = root / "bionic" / "arch"
        for fname in ("api-surface.md", "module-graph.md", "data-model.md"):
            text = (arch / fname).read_text(encoding="utf-8")
            self.assertIn(D.UNSUPPORTED_STACK_SENTENCE, text,
                          f"{fname} lacks the no-extractor sentence")
            self.assertIn("> _stub: unsupported_stack — ", text,
                          f"{fname} does not render the clause 4 stub line")

    def test_decision_index_has_one_meta_adr_row(self):
        """decision-index enriches from the tree's own ADRs — the one meta-ADR row."""
        root = self._fresh_tree()
        D.derive(root, "bionic")
        di = (root / "bionic" / "arch" / "decision-index.md").read_text(encoding="utf-8")
        self.assertNotIn("no extractor", di, "decision-index should not be empty-but-valid")
        self.assertIn("decisions (1)", di, "decision-index should count exactly one decision")
        self.assertIn("[^d1]: ADR-0000", di, "meta-ADR footnote missing")
        self.assertIn("Record architectural decisions as ADRs", di, "meta-ADR title missing")


if __name__ == "__main__":
    unittest.main(verbosity=2)
