"""Contract test: the release-content scan is wired into every in-cycle
quality-gate surface (PB-0041).

Background.  `check-public-release-content.py` is the ADR-0034 §4 permanent
scan that fails a banned internal-reference leak (e.g. an `ADR-NNNN` token or
`[[adrs/...]]` wiki-link) onto a distributed surface (`crux/skills/`,
`crux/agents/`, `crux/templates/`).  It existed as a release/gardener-time
guard but was never named in the in-cycle quality-gate lists authored under
ADR-0020 — so a leak (PB-0040 was the live exemplar) only surfaced at release.
PB-0041 wires the scan into the in-cycle gate prose so it fails IN-CYCLE.

There are THREE lock-step prompt-7 gate surfaces plus ONE dev-module fragment
that must all name the scan:

  1. crux/templates/cycle-promptbook-template.yaml   (prompt 7 — adr canonical)
  2. crux/templates/iterate-promptbook-template.yaml (prompt 7 — verify canonical)
  3. crux/templates/cycle-module-dev.yaml            (the dev-loop FRAGMENT —
       a YAML partial, not a full book, so it cannot be exercised through the
       validator-based TemplateInstantiationTests; covered here by substring).

The adr/verify full books are ALSO covered through the real validator in
test_validate_promptbook.py::TemplateInstantiationTests (which proves they
still validate); this file is the cheap stdlib backstop that additionally
covers the fragment and pins the exit-semantics teaching so coverage cannot
silently regress.

Uses only stdlib (unittest, pathlib).  Reads repo-relative template files —
cwd-independent, no mocks.
"""

from __future__ import annotations

import unittest
from pathlib import Path

# tests/ -> scripts/ -> crux/ -> repo root
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
TEMPLATES = REPO_ROOT / "crux" / "templates"

# The literal that proves the scan is named in the gate prose.  This is the
# SCRIPT NAME, not an ADR token or wiki-link — so its presence in a Zone-1
# surface (crux/templates/) must NOT itself trip check-public-release-content.
SCAN_NAME: str = "check-public-release-content"

# Surfaces that must name the scan in their prompt-7 / dev-loop gate list.
GATE_SURFACES = (
    "cycle-promptbook-template.yaml",
    "iterate-promptbook-template.yaml",
    "cycle-module-dev.yaml",
)

# Exit-semantics teaching the gate prose must carry so a findings result is
# never misread as an environment failure and waved through.
SEMANTIC_MARKERS = ("exit 0", "exit 1", "stderr")


class TestReleaseContentScanWiredIntoGates(unittest.TestCase):
    """All three in-cycle gate surfaces name the release-content scan."""

    def _read(self, name: str) -> str:
        path = TEMPLATES / name
        self.assertTrue(path.exists(), msg=f"{path} not found.")
        return path.read_text(encoding="utf-8")

    def test_all_gate_surfaces_name_the_scan(self) -> None:
        for name in GATE_SURFACES:
            with self.subTest(template=name):
                self.assertIn(
                    SCAN_NAME,
                    self._read(name),
                    msg=(
                        f"{name} no longer names the release-content scan "
                        f"'{SCAN_NAME}'.  PB-0041 wired this into the in-cycle "
                        f"quality-gate list (ADR-0034 §4); removing it lets an "
                        f"internal-reference leak escape until release time."
                    ),
                )

    def test_gate_surfaces_teach_exit_semantics(self) -> None:
        """Each surface that names the scan must also teach exit-1 = finding
        vs crash = environment error, so a findings result is not waved
        through as an env failure.

        The markers are asserted within a WINDOW anchored on the scan name
        (not file-wide), so deleting the exit-semantics prose from the gate
        block fails this test even if the bare tokens survive elsewhere in
        the template (e.g. in an unrelated `result token` example)."""
        for name in GATE_SURFACES:
            with self.subTest(template=name):
                text = self._read(name)
                anchor = text.find(SCAN_NAME)
                self.assertNotEqual(
                    anchor, -1, msg=f"{name} no longer names the scan."
                )
                # The exit-semantics teaching sits in the same gate block,
                # immediately after the scan name; a generous window keeps the
                # test robust to wording while still excluding the rest of the
                # template.
                window = text[anchor : anchor + 900]
                for marker in SEMANTIC_MARKERS:
                    self.assertIn(
                        marker,
                        window,
                        msg=(
                            f"{name} names the scan but dropped the exit-"
                            f"semantics marker '{marker}' from the gate block.  "
                            f"The gate prose must distinguish exit 1 (a finding "
                            f"to fix) from a crash (an environment error) — see "
                            f"PB-0041 / the permanent release-content scan."
                        ),
                    )


if __name__ == "__main__":
    unittest.main()
