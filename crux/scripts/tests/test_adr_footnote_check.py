"""Tests for writing rule #7 — cite a decision by a footnote whose definition
names the rule (rule:footnote-definition-names-the-rule).

The footnote-definition line is checked the way running text is checked: a
definition carrying an ADR number, bare or as a link that renders one, is a
finding. The marker in the prose stays free text and is never a finding.
"""

import importlib
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
C = importlib.import_module("crux.prose.adr_footnote_check")


def refs(md):
    return [m for _, m in C.find_inline_adr_refs(md)]


class FlaggedTests(unittest.TestCase):
    # AC-2 — flags a bare number AND a link whose visible text renders it.
    def test_bare_number(self):
        self.assertEqual(refs("Enforced by ADR-0058 here."), ["ADR-0058"])

    def test_wikilink_rendering_number(self):
        self.assertEqual(refs("See [[adrs/ADR-0058-six-rules]] please."), ["ADR-0058"])

    def test_inline_link_visible_number(self):
        self.assertEqual(refs("See [ADR-0058](adrs/ADR-0058-x.md)."), ["ADR-0058"])

    def test_footnote_definition_carrying_adr_number_is_flagged(self):
        # SANCTIONED INVERSION of the former `test_footnote_definition`, which
        # asserted the definition line was exempt. The rule's content changed:
        # the definition names the rule and carries no ADR number, so a
        # definition rendering one is checked the way running text is.
        self.assertEqual(refs("[^adr-0058]: [[adrs/ADR-0058-six-rules]]"), ["ADR-0058"])

    def test_footnote_definition_bare_number_is_flagged(self):
        self.assertEqual(refs("[^rules]: ADR-0058"), ["ADR-0058"])

    def test_old_compliant_form_is_a_finding_on_the_definition_line(self):
        # The pre-inversion "compliant" fixture: number only in the definition.
        # The finding sits on the definition line (3), never on the marker (1).
        md = "Enforced by our rules.[^adr-0058]\n\n[^adr-0058]: [[adrs/ADR-0058-x]]\n"
        self.assertEqual(C.find_inline_adr_refs(md), [(3, "ADR-0058")])


class ExemptTests(unittest.TestCase):
    # AC-2 — non-findings across the exempt token classes.
    def test_link_destination_only(self):
        self.assertEqual(refs("See [the rules](adrs/ADR-0058-x.md)."), [])

    def test_inline_code(self):
        self.assertEqual(refs("The token `ADR-0058` is code."), [])

    def test_fenced_code(self):
        self.assertEqual(refs("```\nADR-0058\n```\n"), [])

    def test_blockquote(self):
        self.assertEqual(refs("> quoting ADR-0058 verbatim"), [])

    def test_html_block(self):
        self.assertEqual(refs("<!-- ADR-0058 in a comment -->"), [])

    def test_frontmatter(self):
        self.assertEqual(refs("---\nrelated: ADR-0058\n---\n\nclean prose here."), [])

    def test_compliant_footnote_marker(self):
        # The compliant form: a free-text marker in prose, and a definition
        # naming the rule with no ADR number. Positive control for this
        # absence: `test_old_compliant_form_is_a_finding_on_the_definition_line`
        # shows the same shape WITH a number in the definition is flagged.
        md = (
            "Enforced by our rules.[^rules]\n\n"
            "[^rules]: rule:footnote-definition-names-the-rule\n"
        )
        self.assertEqual(refs(md), [])

    def test_footnote_marker_is_free_text(self):
        # Clause 7(a): the marker is typographic. A marker spelled after an ADR
        # is not a finding; only the definition carries the identity.
        md = (
            "Enforced by our rules.[^adr-0058]\n\n"
            "[^adr-0058]: rule:footnote-definition-names-the-rule\n"
        )
        self.assertEqual(refs(md), [])


class RatchetTests(unittest.TestCase):
    # AC-5 — the guard flags only NEW violations relative to a baseline.
    def test_ratchet(self):
        md = "Old ref ADR-0001 stays.\nNew ref ADR-0002 added."
        base = {"1:ADR-0001"}
        new = C.new_violations(md, base)
        self.assertEqual([m for _, m in new], ["ADR-0002"])   # only the un-baselined one

    def test_baseline_for(self):
        self.assertEqual(C.baseline_for("ADR-0009 here"), {"1:ADR-0009"})

    def test_ratchet_baselines_a_definition_line_finding(self):
        # A pre-existing numbered definition is baselined like any other
        # finding, so the ratchet stays live across the migration.
        md = "Text.[^a]\n\n[^a]: [[adrs/ADR-0001-x]]\n[^b]: [[adrs/ADR-0002-y]]\n"
        self.assertEqual(C.baseline_for(md), {"3:ADR-0001", "4:ADR-0002"})
        new = C.new_violations(md, {"3:ADR-0001"})
        self.assertEqual(new, [(4, "ADR-0002")])


if __name__ == "__main__":
    unittest.main(verbosity=2)
