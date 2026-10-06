"""Regression test for the survey sign-off's index retire path (N2).

`signoff-survey.build_index`'s retire path re-parsed a rendered row by
splitting on every `|`, so an escaped `\\|` inside a `domain` cell tore in two
and lost its backslash on rejoin — a retired hostile row re-rendered with more
cells than the header declares. The reverse-direction defect on the same
surface (N3, index references read by page-grep) is covered end to end against
`check_observations.check` in `test_observations.py`.

The absence assertion carries its positive control: the benign retire path must
keep working, or "the cell count is right" would also be true of code that
never touched the row.
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _load(name: str, filename: str | None = None):
    spec = importlib.util.spec_from_file_location(
        name, SCRIPTS / (filename or f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


SO = _load("signoff_survey", "signoff-survey.py")


class BuildIndexRetireTests(unittest.TestCase):
    """`build_index` flips a retired row's status cell without re-parsing the
    whole row on bare pipes."""

    def _retire(self, existing: str) -> str:
        ctx = {"date": "2026-08-31"}
        plan = [{"verdict": "ratify", "anchor_id": "a" * 16,
                 "domain": "runtime", "evidence": ["src/a.py:1-1"],
                 "retires": "observations/OBS-0001-x.md"}]
        receipt = {"rows": [{"anchor_id": "a" * 16, "record_id": "OBS-0002"}]}
        return SO.build_index(ctx, plan, receipt, existing)

    def _existing(self, domain: str) -> str:
        row = SO._index_row("OBS-0001", "ratified", domain,
                            ["src/a.py:1-1"], "b" * 16)
        return ("# Observations\n\n_Last updated: 2026-08-01_\n\n"
                "## Records (1)\n\n" + SO.INDEX_HEADER + row)

    def test_a_retired_row_with_an_escaped_pipe_keeps_seven_cells(self):
        out = self._retire(self._existing("runtime \\| gateway"))
        retired = next(ln for ln in out.splitlines()
                       if ln.startswith("| OBS-0001 "))
        # Seven data cells, exactly as the header declares — the escaped pipe
        # stayed inside its cell rather than splitting the row.
        self.assertEqual(retired.count(" | "), 6)
        self.assertIn("\\|", retired)                       # escape preserved
        self.assertIn("| retired |", retired)               # status flipped

    def test_a_benign_retired_row_still_flips_to_retired(self):
        # Positive control: no escaped pipe, the ordinary path still works.
        out = self._retire(self._existing("runtime"))
        retired = next(ln for ln in out.splitlines()
                       if ln.startswith("| OBS-0001 "))
        self.assertEqual(retired.count(" | "), 6)
        self.assertIn("| retired |", retired)


if __name__ == "__main__":
    unittest.main()
