"""A projected rule says which lifecycle status its source record carried.

THE DEFECT. The summaries projection reads ACTIVE ADRs whether Proposed or
Accepted — deliberately, so a `rule:<slug>` citation resolves as soon as an
ADR is written. It never said which it had read. A rule from a Proposed ADR
and one from an Accepted ADR projected identical rows, so a consumer citing
the rule table could not tell a resolvable handle from an adopted obligation.

WHY A NEW FIELD AND NOT AN EXISTING ONE. `disposition` and `authority` are
both pure functions of `provenance` and partition the decision-versus-
observation axis; `review_state` is a closed five-value enum about the
backfill-receipt chain, where `accepted-with-adr` means "prospective, so it
needs no receipt". None concerns the ADR state machine and none has a spare
value.

WHAT MUST NOT MOVE. `source_status` stays outside `canonical_string`, so
accepting an ADR leaves every entry's content digest byte-identical and every
signed backfill receipt and reconciliation valid. That is asserted here, not
assumed: it is the property that makes this change safe to ship.

Run:
  uv run python3 -m unittest discover -s crux/scripts/tests -p 'test_projection_status_exposure.py'
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import summaries_projection as sp  # noqa: E402

ADR_TMPL = """---
id: {aid}
title: "Fixture {n}"
status: {status}
date: 2026-09-17
proposed_date: 2026-09-17
accepted_date: {accepted}
deprecated_date: null
superseded_date: null
supersedes: []
amends: []
superseded_by: null
deciders: ["fixture"]
tags: [fixture]
related_briefs: []
related_research: []
governs:
  - domain: fixture-domain
    rule: "Fixture rule {n} says every widget is frobnicated before use."
    scope: the fixture
    handle: {aid}/fixture-rule-{n}
    provenance: authored
---

# {aid} — Fixture {n}

## Context
c
## Decision
d
## Alternatives Considered
a
## Consequences
q
## References
r
"""


class StatusExposureTestCase(unittest.TestCase):
    """A disposable tree holding one Accepted and one Proposed ADR."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / ".bionic.yml").write_text(
            'config_version: "1"\ndocs_dir: bionic\n', encoding="utf-8")
        self.docs = self.root / "bionic"
        self.adrs = self.docs / "adrs"
        self.adrs.mkdir(parents=True)
        (self.docs / "manifest.yml").write_text(
            'schema_version: "5"\nconcerns_enabled: [adrs]\nadr:\n'
            '  next_number: 3\n  governs_from: 1\n', encoding="utf-8")
        self.write_adr(1, "Accepted")
        self.write_adr(2, "Proposed")

    def write_adr(self, n: int, status: str) -> Path:
        aid = f"ADR-{n:04d}"
        p = self.adrs / f"{aid}-fixture-{n}.md"
        p.write_text(ADR_TMPL.format(aid=aid, n=n, status=status,
                                     accepted="2026-09-17" if status == "Accepted" else "null"),
                     encoding="utf-8")
        return p

    def records(self) -> list[dict]:
        return sp.collect_records(self.adrs, governs_from=1, observations=None)

    def resolver(self) -> dict:
        return sp.build_resolver(self.records(), reviews=None, governs_from=1)


class ResolverExposesStatusTests(StatusExposureTestCase):

    def test_proposed_and_accepted_rows_differ_on_source_status(self):
        r = self.resolver()
        acc = r["ADR-0001/fixture-rule-1"]
        pro = r["ADR-0002/fixture-rule-2"]
        self.assertEqual(acc["source_status"], "Accepted")
        self.assertEqual(pro["source_status"], "Proposed")

    def test_they_agree_on_every_other_facet(self):
        # PAIRED CONTROL: the new field is the ONLY thing that separates them,
        # so nothing else silently changed meaning.
        r = self.resolver()
        acc = dict(r["ADR-0001/fixture-rule-1"])
        pro = dict(r["ADR-0002/fixture-rule-2"])
        for d in (acc, pro):
            d.pop("source_status"); d.pop("source_adr"); d.pop("rule")
        self.assertEqual(acc, pro)

    def test_disposition_and_authority_keep_their_meanings(self):
        # Both are functions of provenance alone and must not have learned
        # anything about lifecycle.
        r = self.resolver()
        for h in ("ADR-0001/fixture-rule-1", "ADR-0002/fixture-rule-2"):
            self.assertEqual(r[h]["disposition"], "decided")
            self.assertEqual(r[h]["authority"], "prescriptive")

    def test_a_proposed_handle_still_resolves(self):
        # The existing contract requires it: a citation resolves as soon as
        # the ADR is written. Exposure must not become filtering.
        r = self.resolver()
        self.assertIn("ADR-0002/fixture-rule-2", r)
        self.assertTrue(r["ADR-0002/fixture-rule-2"]["rule"])

    def test_a_proposed_slug_satisfies_the_citation_maps(self):
        live, _retired = sp.live_and_retired_slugs(self.records())
        self.assertEqual(live.get("fixture-rule-2"), "ADR-0002/fixture-rule-2")

    def test_an_absent_status_is_null_not_a_sentinel_string(self):
        # On the MACHINE surface a missing status must be JSON null, never the
        # string "unknown" — a naive consumer cannot tell a sentinel string
        # from a real value, and it would have to join the value domain.
        row = sp._record_from_entry({"handle": "ADR-0009/x", "domain": "d", "rule": "r",
                                     "scope": "s", "provenance": "authored"},
                                    "ADR-0009", 9, source_kind="adr")
        self.assertIsNone(row["source_status"])
        r = sp.build_resolver([row], reviews=None, governs_from=1)
        self.assertIsNone(r["ADR-0009/x"]["source_status"])
        self.assertIn("source_status", json.dumps(r))


class DigestAndSignatureCompatibilityTests(StatusExposureTestCase):
    """Accepting an ADR must move nothing that a signature is bound to."""

    def test_accepting_an_adr_leaves_the_content_digest_byte_identical(self):
        before = {r["handle"]: sp.content_digest(r) for r in self.records()}
        self.write_adr(2, "Accepted")          # the ONLY change: the status line
        after = {r["handle"]: sp.content_digest(r) for r in self.records()}
        self.assertEqual(before, after)

    def test_source_status_is_absent_from_the_canonical_string(self):
        # The digest preimage is what a signed receipt and a signed
        # reconciliation are bound to. Naming the field here would orphan
        # every signature the moment an ADR was accepted.
        rec = next(r for r in self.records() if r["handle"] == "ADR-0002/fixture-rule-2")
        canon = sp.canonical_string(rec)
        self.assertNotIn("Proposed", canon)
        self.assertNotIn("source_status", canon)

    def test_a_receipt_bound_to_the_digest_survives_acceptance(self):
        # A signed receipt carries the digest it passed. If acceptance moved
        # the digest, the receipt would stop matching and the entry would
        # soft-exclude as unreviewed.
        rec = next(r for r in self.records() if r["handle"] == "ADR-0002/fixture-rule-2")
        signed_digest = sp.content_digest(rec)
        self.write_adr(2, "Accepted")
        rec_after = next(r for r in self.records() if r["handle"] == "ADR-0002/fixture-rule-2")
        self.assertEqual(sp.content_digest(rec_after), signed_digest)

    def test_the_status_really_did_change(self):
        # PAIRED CONTROL for the three tests above: they would all pass
        # vacuously if rewriting the fixture changed nothing.
        self.assertEqual(self.resolver()["ADR-0002/fixture-rule-2"]["source_status"], "Proposed")
        self.write_adr(2, "Accepted")
        self.assertEqual(self.resolver()["ADR-0002/fixture-rule-2"]["source_status"], "Accepted")


class ObservationRowTests(unittest.TestCase):
    """An observation's status is copied verbatim, never normalised."""

    def test_the_projected_observation_status_is_the_module_constant(self):
        self.assertEqual(sp.OBSERVATION_PROJECTED_STATUS, "ratified")

    def test_an_observation_row_carries_ratified_not_an_adr_value(self):
        row = sp._record_from_entry({"handle": "OBS-0001/x", "domain": "d", "rule": "r",
                                     "scope": "s", "provenance": "recovered"},
                                    "OBS-0001", 1, source_kind="observation",
                                    status=sp.OBSERVATION_PROJECTED_STATUS)
        self.assertEqual(row["source_status"], "ratified")
        r = sp.build_resolver([row], reviews=None, governs_from=1)
        self.assertEqual(r["OBS-0001/x"]["source_status"], "ratified")
        # And the provenance-derived facets are untouched by the new field.
        self.assertEqual(r["OBS-0001/x"]["disposition"], "observed")

    def test_the_value_domain_is_a_case_sensitive_union(self):
        # ADR values are capitalised, the observation constant is not. No
        # canonicalisation is applied, so a consumer lower-casing before
        # comparison would merge states that differ.
        self.assertNotEqual(sp.OBSERVATION_PROJECTED_STATUS,
                            sp.OBSERVATION_PROJECTED_STATUS.capitalize())


class DoctrineIndexTests(StatusExposureTestCase):
    """Both statuses render in the compiled doctrine index."""

    def run_doctrine(self) -> str:
        r = subprocess.run(
            [sys.executable, str(SCRIPTS / "compile-doctrine.py"), "--repo-root", str(self.root)],
            capture_output=True, timeout=180)
        self.assertEqual(r.returncode, 0, r.stdout.decode() + r.stderr.decode())
        return (self.docs / "adrs" / "doctrine" / "index.md").read_text(encoding="utf-8")

    def test_the_index_carries_a_source_status_column(self):
        body = self.run_doctrine()
        self.assertIn("| handle | citation | rule | source ADR | source_status | disposition | basis |", body)

    def test_both_statuses_render(self):
        body = self.run_doctrine()
        self.assertRegex(body, r"\| ADR-0001 \| Accepted \| decided \|")
        self.assertRegex(body, r"\| ADR-0002 \| Proposed \| decided \|")


if __name__ == "__main__":
    unittest.main()
