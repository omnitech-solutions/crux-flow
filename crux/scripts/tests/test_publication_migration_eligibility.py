"""Publication eligibility consumes live architecture, never historical authority."""
import unittest
import publication_eligibility as pe


class MigrationEligibility(unittest.TestCase):
    def test_accepted_live_architecture_is_the_positive_control(self):
        self.assertEqual(pe.eligibility(dict(source_kind="adr", source_status="Accepted")), (True, ""))

    def test_accepted_historical_retired_removed_or_alias_record_cannot_publish(self):
        for facet in (dict(historical=True), dict(retired_by=["ADR-0146"]),
                      dict(review_state="removed"), dict(alias_handles=["OBS-0001/choice"]),
                      dict(authority="descriptive"), dict(disposition="observed")):
            with self.subTest(facet=facet):
                self.assertFalse(pe.eligibility(dict(source_kind="adr", source_status="Accepted", **facet))[0])

    def test_nonarchitectural_record_and_undecided_source_are_refused(self):
        for kind, status in (("implementation-decision", "Accepted"), ("implementation-result", "Accepted"),
                             ("implementation-migration", "Accepted"), ("adr", "Proposed"),
                             ("observation", "ratified")):
            with self.subTest(kind=kind, status=status):
                self.assertFalse(pe.eligibility(dict(source_kind=kind, source_status=status))[0])

    def test_historical_findings_name_each_citing_path(self):
        rows = pe.findings({"choice": ["one:1", "two:2"]}, {"choice": dict(
            source_kind="adr", source_status="Accepted", historical=True)})
        self.assertEqual([r["path"] for r in rows], ["one:1", "two:2"])
        self.assertTrue(all("historical" in r["reason"] for r in rows))
