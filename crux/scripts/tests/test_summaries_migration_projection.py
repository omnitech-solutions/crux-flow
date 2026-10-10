"""Synthetic pure projection facts supply no council or publication approval."""
import copy
import json
from pathlib import Path
import tempfile
from unittest import mock
import unittest

import summaries_projection as sp


def record(handle, *, retires=(), status="Accepted"):
    return dict(handle=handle, source_adr=handle.split("/")[0], domain="testing",
                rule="Frozen implementation text.", scope="reader", provenance="authored",
                source_status=status, source_kind="adr", adr_num=sp.adr_num(handle.split("/")[0]),
                retires=list(retires), retired_by=[])


OLD = "ADR-0001/predecessor"
DEMOTED = "ADR-0110/implementation"
NEW = "ADR-0146/constraint"


def facts():
    return dict(historical_handles={DEMOTED: dict(source_identity="a" * 64,
        destination=dict(source_adr="ADR-0110", clause_sha256="b" * 64), replacements=[NEW])},
        historical_displacements=[dict(target_handle=OLD, source_displacer=DEMOTED,
            source_identity="a" * 64, source_ref=dict(path="docs/adrs/archive/source.md",
                source_adr="ADR-0110", clause_sha256="b" * 64))],
        retired_handles=[OLD], reserved_slugs=["implementation"], retained_handles=[],
        source_refs=[dict(path="docs/adrs/archive/source.md", source_adr="ADR-0110", tier="archive",
            status="Superseded", source_identity="a" * 64, clause_sha256="b" * 64)],
        dependency_fingerprints=[dict(path="docs/adrs/migrations/pilot.yaml", sha256="3" * 64)])


class AuthorityOverlay(unittest.TestCase):
    def corpus(self):
        rows = [record(OLD), record(DEMOTED, retires=[OLD]), record(NEW),
                record("ADR-0110/surviving")]
        sp._stamp_retirements(rows)
        return rows

    def plan(self, rows=None, view=None, aliases=None, removed=None):
        return sp._summary_authority_records(rows or self.corpus(), view or facts(),
            removed or {}, aliases or {})

    def test_order_preserves_predecessor_and_mixed_constraint_without_mutating_source(self):
        rows = self.corpus(); before = copy.deepcopy(rows)
        result = self.plan(rows)
        self.assertEqual(rows, before)
        self.assertEqual({r["handle"] for r in sp.live_records(result["records"])},
                         {NEW, "ADR-0110/surviving"})
        self.assertEqual(result["retired_slugs"], {"predecessor": []})
        self.assertEqual(result["historical_slugs"]["implementation"]["source_handle"], DEMOTED)
        self.assertNotIn("rule", result["historical_slugs"]["implementation"])
        self.assertEqual(result["historical_displacements"][OLD][0]["source_displacer"], DEMOTED)

    def test_archived_displacer_still_reserves_predecessor_by_key(self):
        rows = [record(OLD), record(NEW)]
        result = self.plan(rows)
        self.assertIn("predecessor", result["retired_slugs"])
        self.assertEqual(result["retired_slugs"]["predecessor"], [])
        self.assertNotIn(OLD, {r["handle"] for r in sp.live_records(result["records"])})

    def test_lost_historical_displacement_provenance_refuses_empty_reservation(self):
        view = facts(); view["historical_displacements"] = []
        with self.assertRaises(sp.GovernsValidationError):
            self.plan([record(OLD), record(NEW)], view)

    def test_re_retirement_of_historical_handle_refuses_even_when_proposed(self):
        rows = self.corpus() + [record("ADR-0150/proposal", retires=[DEMOTED], status="Proposed")]
        with self.assertRaises(sp.GovernsValidationError): self.plan(rows)

    def test_historical_alias_key_or_target_refuses(self):
        for aliases in ({DEMOTED: dict(alias_of="ADR-0146", alias_handles=[NEW])},
                        {"OBS-0001/alias": dict(alias_of="ADR-0110", alias_handles=[DEMOTED])}):
            with self.subTest(aliases=aliases), self.assertRaises(sp.GovernsValidationError):
                self.plan(aliases=aliases)

    def test_all_handle_lanes_and_slug_lanes_are_exclusive(self):
        for alteration in ("removed", "retired", "live-slug", "removed-slug"):
            rows = self.corpus(); view = facts(); removed = {}
            if alteration == "removed": removed[DEMOTED] = {}
            if alteration == "retired": view["retired_handles"].append(DEMOTED)
            if alteration == "live-slug": rows.append(record("ADR-0150/implementation"))
            if alteration == "removed-slug": removed["ADR-0150/implementation"] = {}
            with self.subTest(alteration=alteration), self.assertRaises(sp.GovernsValidationError):
                self.plan(rows, view, removed=removed)

    def test_conflicting_displacement_cannot_deduplicate(self):
        view = facts(); conflict = copy.deepcopy(view["historical_displacements"][0])
        conflict["source_identity"] = "c" * 64; view["historical_displacements"].append(conflict)
        with self.assertRaises(sp.GovernsValidationError): self.plan(view=view)

    def test_exact_duplicate_displacement_deduplicates_and_current_displacer_stays_distinct(self):
        view = facts(); view["historical_displacements"] *= 2
        rows = self.corpus(); rows[-1]["retires"] = [OLD]
        result = self.plan(rows, view)
        self.assertEqual(len(result["historical_displacements"][OLD]), 1)
        self.assertEqual(result["retired_slugs"]["predecessor"], ["ADR-0110/surviving"])

    def test_ordinary_retirement_chain_keeps_existing_empty_semantics(self):
        rows = [record(OLD), record(DEMOTED, retires=[OLD]), record(NEW, retires=[DEMOTED])]
        sp._stamp_retirements(rows)
        view = dict(historical_handles={}, historical_displacements=[], retired_handles=[],
                    reserved_slugs=[], retained_handles=[], source_refs=[], dependency_fingerprints=[])
        result = self.plan(rows, view)
        self.assertEqual(result["retired_slugs"], sp.live_and_retired_slugs(rows)[1])
        self.assertEqual(result["historical_slugs"], {})

    def test_mapping_disguised_as_ratified_observation_or_decided_alias_cannot_govern(self):
        import yaml
        with tempfile.TemporaryDirectory() as tmp:
            adrs = Path(tmp) / "adrs"; adrs.mkdir()
            obs = Path(tmp) / "observations"; obs.mkdir()
            path = obs / "OBS-0001-example.md"
            doc = dict(id="OBS-0001", status="ratified", provenance="recovered", evidence=["source.py:1-1"],
                anchor_id="a" * 16, governs=[dict(handle="OBS-0001/example", domain="testing",
                    rule="Observable factual constraint.", scope="reader", provenance="recovered")])
            path.write_text("---\n" + yaml.safe_dump(doc) + "---\n")
            self.assertEqual(len(sp.collect_records(adrs, observations=obs)), 1)
            doc["record_type"] = "implementation-migration"
            path.write_text("---\n" + yaml.safe_dump(doc) + "---\n")
            with self.subTest(lane="ratified"), self.assertRaises(sp.GovernsValidationError):
                sp.collect_records(adrs, observations=obs)
            doc.update(status="decided", decided_by="ADR-0146")
            path.write_text("---\n" + yaml.safe_dump(doc) + "---\n")
            with self.subTest(lane="alias"), self.assertRaises(sp.GovernsValidationError):
                sp.collect_observation_alias_rows(obs, [])

    def test_live_removed_handle_and_slug_or_retired_removed_slug_collisions_refuse(self):
        for removed in ({NEW: {}}, {"ADR-0150/constraint": {}}, {"ADR-0150/predecessor": {}}):
            with self.subTest(removed=removed), self.assertRaises(sp.GovernsValidationError):
                self.plan(removed=removed)


def loaded_inputs():
    tree = Path("/synthetic/docs")
    return dict(tree=tree, paths={name: tree / "adrs/summaries" / name for name in
        ("rule-table.md", "resolver.json", "implementation-map.md", "_meta.json")},
        records=AuthorityOverlay().corpus(), reviews=dict(batches={}, receipts=[], no_rule=[]), aliases={}, governs_from=None,
        archived_handles=[], signed_handles=[], historical_clauses=[], publication_history=[],
        implementation_map="# Synthetic implementation map\n", metadata=dict(
            adr_frontmatter_sha256="1" * 64, backfill_reviews_sha256=None,
            observations_sha256=None, survey_receipts_sha256=None,
            input_domain=["adrs", "backfill-reviews"], schema="6", tool="summarize-adrs.py"))


def pending():
    return dict(path="docs/adrs/migrations/pilot.application.json", sha256="2" * 64,
        close_identity=dict(batch=dict(path="docs/adrs/migrations/pilot.yaml", sha256="3" * 64),
            book_id="PB-9998", run_id="RUN-999", book_content_hash="sha256:" + "4" * 64,
            slot="implementation-1", gate_prompt=3))


class CompiledPlans(unittest.TestCase):
    def plan(self, loaded=None, view=None, publication=None):
        return sp._plan_summary_outputs(loaded or loaded_inputs(), view or facts(),
                                        publication or pending())

    def render(self, plan, commit="5" * 40):
        return sp._materialize_summary_outputs(plan,
            sp._committed_publication_ref(pending(), dict(path=pending()["path"],
                sha256=pending()["sha256"], publication_commit=commit)))

    def test_four_models_compiled_without_pending_serialization_or_governing_history(self):
        plan = self.plan(); outputs = self.render(plan)
        self.assertEqual(len(outputs), 4)
        resolver = json.loads(outputs[Path("/synthetic/docs/adrs/summaries/resolver.json")])
        self.assertNotIn(DEMOTED, resolver)
        self.assertNotIn(OLD, resolver)
        self.assertIn(NEW, resolver)
        self.assertEqual(resolver["retired_slugs"]["predecessor"], [])
        history = resolver["historical_slugs"]["implementation"]
        self.assertEqual(set(history), {"source_handle", "source_identity", "destination", "replacements"})
        table = outputs[Path("/synthetic/docs/adrs/summaries/rule-table.md")].decode()
        self.assertIn("## Historical implementation handles", table)
        self.assertNotIn("Frozen implementation text.", table.split("## Historical implementation handles")[1])

    def test_40_and_64_provenance_only_alter_declared_fields_and_dependent_fingerprint(self):
        plan = self.plan(); left = self.render(plan); right = self.render(plan, "6" * 64)
        for path in left:
            if path.suffix == ".md": self.assertEqual(left[path], right[path]); continue
            a, b = json.loads(left[path]), json.loads(right[path])
            if path.name == "_meta.json":
                self.assertNotEqual(a.pop("migration_inputs_sha256"), b.pop("migration_inputs_sha256"))
                a["migration"]["publication"]["publication_commit"] = "same"
                b["migration"]["publication"]["publication_commit"] = "same"
                a["migration"]["historical_displacements"][OLD][0]["publication_ref"]["publication_commit"] = "same"
                b["migration"]["historical_displacements"][OLD][0]["publication_ref"]["publication_commit"] = "same"
            else:
                a["historical_displacements"][OLD][0]["publication_ref"]["publication_commit"] = "same"
                b["historical_displacements"][OLD][0]["publication_ref"]["publication_commit"] = "same"
            self.assertEqual(a, b)

    def test_materializer_reads_no_policy_authority_or_filesystem(self):
        plan = self.plan()
        with mock.patch.object(sp, "_summary_authority_records", side_effect=AssertionError), \
                mock.patch.object(Path, "read_bytes", side_effect=AssertionError), \
                mock.patch.object(Path, "read_text", side_effect=AssertionError):
            self.assertEqual(len(self.render(plan)), 4)

    def test_invalid_commit_domain_or_wrong_witness_refuses_before_render(self):
        for commit in ("HEAD", "5" * 39, "g" * 40, "5" * 41, None):
            with self.subTest(commit=commit), self.assertRaises(sp.GovernsValidationError):
                self.render(self.plan(), commit)
        with self.assertRaises(sp.GovernsValidationError):
            sp._committed_publication_ref(pending(), dict(path="other", sha256="2" * 64,
                                                        publication_commit="5" * 40))

    def test_invalid_loaded_shapes_encoding_containment_or_slot_in_authority_refuse(self):
        faults = [lambda x: x.pop("records"), lambda x: x["paths"].pop("resolver.json"),
            lambda x: x["paths"].update({"resolver.json": Path("/outside/resolver.json")}),
            lambda x: x.update(implementation_map="\ud800"),
            lambda x: x["records"][0].update(rule=sp._PublicationCommitSlot()),
            lambda x: x["metadata"].update(adr_frontmatter_sha256="bad"),
            lambda x: x["metadata"].update(schema="5"),
            lambda x: x["metadata"].update(input_domain=["invented"])]
        for fault in faults:
            value = loaded_inputs()
            with self.subTest(fault=fault), self.assertRaises(sp.GovernsValidationError):
                fault(value); self.plan(value)

    def test_cyclic_models_refuse_before_materialization(self):
        loaded = loaded_inputs(); loaded["metadata"]["input_domain"].append(loaded)
        with self.assertRaises(sp.GovernsValidationError): self.plan(loaded)

    def test_pending_cannot_supply_commit_or_defer_authority(self):
        for fault in (lambda p: p.update(publication_commit="5" * 40),
                      lambda p: p["close_identity"].update(gate_prompt="3"),
                      lambda p: p.update(sha256=sp._PublicationCommitSlot())):
            value = pending()
            with self.subTest(fault=fault), self.assertRaises(sp.GovernsValidationError):
                fault(value); self.plan(publication=value)

    def test_invalid_source_alias_dependency_and_original_retirement_models_refuse(self):
        faults = [lambda x: x["records"][0].update(historical=True),
            lambda x: x["records"][0].update(source_status=3),
            lambda x: x["aliases"].update({"not-a-handle": dict(alias_of="ADR-0146", alias_handles=[NEW])}),
            lambda x: x["aliases"].update({"OBS-0001/alias": dict(alias_of="ADR-0146", alias_handles=["ADR-9999/missing"])}),
            lambda x: x["records"][-1].update(retires=["ADR-9999/missing"]),
            lambda x: x["signed_handles"].append(OLD)]
        for fault in faults:
            value = loaded_inputs()
            with self.subTest(fault=fault), self.assertRaises(sp.GovernsValidationError):
                fault(value); self.plan(value)
        for digest in ("bad", "c" * 64):
            view = facts(); view["dependency_fingerprints"] = [dict(path="docs/dep", sha256="a" * 64),
                                                             dict(path="docs/dep", sha256=digest)]
            with self.subTest(digest=digest), self.assertRaises(sp.GovernsValidationError): self.plan(view=view)

    def test_empty_retirement_reservation_differs_from_absent_key(self):
        result = self.render(self.plan())
        resolver = json.loads(result[Path("/synthetic/docs/adrs/summaries/resolver.json")])
        self.assertEqual(resolver["retired_slugs"].get("predecessor"), [])
        self.assertIn("predecessor", resolver["retired_slugs"])
        self.assertNotIn("never-present", resolver["retired_slugs"])

    def test_canonical_fingerprint_is_hash_of_materialized_frame(self):
        import hashlib
        output = self.render(self.plan())
        meta = json.loads(output[Path("/synthetic/docs/adrs/summaries/_meta.json")])
        raw = json.dumps(meta["migration"], sort_keys=True, separators=(",", ":")).encode()
        self.assertEqual(meta["migration_inputs_sha256"], hashlib.sha256(raw).hexdigest())

    def test_compiled_plan_and_substitution_are_immutable_and_domain_checked_at_construction(self):
        for value in (None, "HEAD", "bad", "5" * 39):
            with self.subTest(value=value), self.assertRaises(sp.GovernsValidationError):
                sp._CommittedPublicationRef(value)
        plan = self.plan(); ref = sp._committed_publication_ref(pending(),
            dict(path=pending()["path"], sha256=pending()["sha256"], publication_commit="5" * 40))
        with self.assertRaises(AttributeError): plan.outputs = ()
        with self.assertRaises(AttributeError): ref.commit = "unvalidated"

    def test_displacement_source_identity_cannot_be_paired_with_another_clause(self):
        view = facts(); view["historical_displacements"][0]["source_ref"]["clause_sha256"] = "c" * 64
        with self.assertRaises(sp.GovernsValidationError): self.plan(view=view)

    def test_historical_edge_cannot_orphan_signed_predecessor_after_archive(self):
        loaded = loaded_inputs(); loaded["records"] = [record(OLD), record(NEW)]
        loaded["signed_handles"] = [OLD]
        with self.assertRaises(sp.GovernsValidationError): self.plan(loaded)

    def test_body_clause_identity_and_digest_must_belong_to_same_selected_clause(self):
        view = facts(); second = copy.deepcopy(view["source_refs"][0]); second.update(
            source_identity="d" * 64, clause_sha256="e" * 64)
        view["source_refs"].append(second)
        loaded = loaded_inputs(); loaded["historical_clauses"] = [dict(source_identity="a" * 64,
            source_ref={k: second[k] for k in ("path", "source_adr", "clause_sha256")},
            destination={k: second[k] for k in ("source_adr", "clause_sha256")}, replacements=[NEW])]
        with self.assertRaises(sp.GovernsValidationError): self.plan(loaded, view)

    def test_destination_utf8_encodability_is_validated_before_materialization(self):
        loaded = loaded_inputs(); tree = Path("/synthetic/\ud800/docs")
        loaded["tree"] = tree
        loaded["paths"] = {name: tree / "adrs/summaries" / name for name in loaded["paths"]}
        with self.assertRaises(sp.GovernsValidationError): self.plan(loaded)

    def test_prior_publication_fields_and_clause_replacement_eligibility_are_prevalidated(self):
        for fault in (dict(path="docs/previous.application.json", sha256="bad", publication_commit="5" * 40),
                      dict(path="docs/previous.application.json", sha256="a" * 64, publication_commit="HEAD"),
                      dict(path="../escape", sha256="a" * 64, publication_commit="5" * 40)):
            loaded = loaded_inputs(); loaded["publication_history"] = [fault]
            with self.subTest(fault=fault), self.assertRaises(sp.GovernsValidationError): self.plan(loaded)
        loaded = loaded_inputs(); loaded["records"].append(record("ADR-0150/proposal", status="Proposed"))
        source = facts()["source_refs"][0]
        loaded["historical_clauses"] = [dict(source_identity=source["source_identity"],
            source_ref={k: source[k] for k in ("path", "source_adr", "clause_sha256")},
            destination={k: source[k] for k in ("source_adr", "clause_sha256")},
            replacements=["ADR-0150/proposal"])]
        with self.subTest(lane="proposal"), self.assertRaises(sp.GovernsValidationError): self.plan(loaded)


if __name__ == "__main__": unittest.main()
