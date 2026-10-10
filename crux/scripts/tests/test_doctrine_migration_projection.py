"""Pure doctrine plans; all supplied approvals and provenance are synthetic."""
from pathlib import Path
import copy
import hashlib
import json
from unittest import mock
import unittest
import doctrine_projection as dp
import summaries_projection as sp
import test_summaries_migration_projection as summary_fixtures


def loaded():
    rows = summary_fixtures.AuthorityOverlay().corpus()
    tree = Path("/synthetic/docs")
    return dict(tree=tree, paths={n: tree / "adrs/doctrine" / n for n in ("index.md", "_meta.json")},
        records=rows, invariants=[], reconciliations=[], bindings={}, governs_from=None,
        tree_name="docs", evidence_facts=[], exempt_entries=[], aliases={}, removed={}, publication_history=[],
        ledger=dict(path="docs/adrs/doctrine/reconciliations.yml", text=None),
        archived_handles=[],
        digests=dict(adr_frontmatter_sha256="1" * 64, invariants_sha256=dp.invariants_sha256([]),
            reconciliations_sha256=None, observations_sha256=None, survey_receipts_sha256=None,
            run_bindings_sha256=hashlib.sha256(b"{}").hexdigest(), governs_from=None, schema="5",
            tool="compile-doctrine.py", input_domain=["adrs", "invariants", "reconciliations", "run-bindings"]))


def pending():
    return dict(path="docs/adrs/migrations/pilot.application.json", sha256="4" * 64,
        close_identity=dict(batch=dict(path="docs/adrs/migrations/pilot.yaml", sha256="3" * 64),
            book_id="PB-0001", run_id="RUN-001", book_content_hash="sha256:" + "5" * 64,
            slot="implementation-1", gate_prompt=4))


def original():
    return dict(historical_handles={}, historical_displacements=[], retired_handles=[],
        reserved_slugs=[], retained_handles=[], source_refs=[], dependency_fingerprints=[])


class CompiledDoctrine(unittest.TestCase):
    def compile(self, data=None, facts=None, publication=None):
        return dp._plan_doctrine_outputs(data or loaded(), facts or summary_fixtures.facts(), publication or pending())

    def materialize(self, plan, commit="a" * 40):
        p = pending()
        return dp._materialize_doctrine_outputs(plan, sp._committed_publication_ref(p,
            dict(path=p["path"], sha256=p["sha256"], publication_commit=commit)))

    def test_compilation_is_pure_preserves_inputs_and_excludes_historical_text(self):
        data = loaded(); facts = summary_fixtures.facts(); before = copy.deepcopy((data, facts))
        with mock.patch.object(Path, "read_text", side_effect=AssertionError("read")), \
                mock.patch.object(dp, "evidence_resolves", side_effect=AssertionError("evidence")):
            plan = self.compile(data, facts)
        self.assertEqual((data, facts), before)
        outputs = self.materialize(plan)
        self.assertEqual(len(outputs), 2)
        index = outputs[data["paths"]["index.md"]].decode()
        self.assertNotIn("| ADR-0110/implementation |", index)
        self.assertNotIn("| ADR-0001/predecessor |", index)
        meta = json.loads(outputs[data["paths"]["_meta.json"]])
        self.assertEqual(meta["schema"], "5")
        self.assertEqual(meta["migration"]["historical_displacements"][summary_fixtures.OLD][0]["source_displacer"],
                         summary_fixtures.DEMOTED)
        self.assertNotIn("rule", json.dumps(meta["migration"]))

    def test_materializer_calls_no_policy_filesystem_or_authority(self):
        import implementation_migration as migration
        plan = self.compile()
        with mock.patch.object(dp, "_doctrine_source_models", side_effect=AssertionError("policy")), \
                mock.patch.object(dp, "_build_domain_entries_loaded", side_effect=AssertionError("policy")), \
                mock.patch.object(migration, "authority_view", side_effect=AssertionError("authority")), \
                mock.patch.object(Path, "read_bytes", side_effect=AssertionError("filesystem")):
            self.assertEqual(len(self.materialize(plan)), 2)

    def test_40_and_64_hex_substitutions_change_only_commit_and_fingerprints(self):
        plan = self.compile(); outputs = [self.materialize(plan, commit) for commit in ("a" * 40, "b" * 64)]
        meta_path = loaded()["paths"]["_meta.json"]
        first, second = (json.loads(result[meta_path]) for result in outputs)
        self.assertNotEqual(first.pop("migration_inputs_sha256"), second.pop("migration_inputs_sha256"))
        self.assertNotEqual(first["migration"]["publication"].pop("publication_commit"),
                            second["migration"]["publication"].pop("publication_commit"))
        for model in (first, second):
            for group in model["migration"]["historical_displacements"].values():
                for edge in group: edge["publication_ref"].pop("publication_commit")
        self.assertEqual(first, second)
        for commit in ("a" * 39, "f" * 41, "A" * 40, "HEAD", "g" * 64):
            with self.subTest(commit=commit), self.assertRaises(sp.GovernsValidationError):
                self.materialize(plan, commit)

    def test_invalid_content_destinations_and_deferred_authority_fields_refuse(self):
        for mutation in ("path", "path-encoding", "encoding", "slot", "duplicate", "evidence", "digest", "frame", "alias"):
            data = loaded(); facts = summary_fixtures.facts()
            if mutation == "path": data["paths"]["index.md"] = Path("/outside/index.md")
            if mutation == "path-encoding":
                data["tree"] = Path("/synthetic/\ud800")
                data["paths"] = {n: data["tree"] / "adrs/doctrine" / n for n in ("index.md", "_meta.json")}
            if mutation == "encoding": data["records"][-1]["rule"] = "\ud800"
            if mutation == "slot": data["records"][-1]["rule"] = sp._PublicationCommitSlot()
            if mutation == "duplicate": data["records"].append(copy.deepcopy(data["records"][-1]))
            if mutation == "evidence": data["evidence_facts"] = [dict(handle="OBS-0001/unknown", evidence="x:1-1", resolves=True)]
            if mutation == "digest": data["digests"]["invariants_sha256"] = "not-a-digest"
            if mutation == "frame": facts["dependency_fingerprints"].append({**facts["dependency_fingerprints"][0], "sha256": "0" * 64})
            if mutation == "alias": data["aliases"]["OBS-0001/alias"] = dict(alias_of="ADR-0110", alias_handles=[summary_fixtures.DEMOTED])
            before = repr(data)
            with self.subTest(mutation=mutation), self.assertRaises((sp.GovernsValidationError, UnicodeError)):
                self.compile(data, facts)
            self.assertEqual(repr(data), before)

    def test_reference_mismatch_missing_batch_and_unproved_history_refuse(self):
        for mutation in ("source", "identity", "batch", "history", "proof", "edge"):
            facts = summary_fixtures.facts(); data = loaded(); p = pending()
            if mutation == "source": facts["source_refs"][0]["clause_sha256"] = "f" * 64
            if mutation == "identity": facts["source_refs"] *= 2
            if mutation == "batch": facts["dependency_fingerprints"] = []
            if mutation == "history": data["publication_history"] = [dict(path="../escape", sha256="a" * 64, publication_commit="a" * 40)]
            if mutation == "proof": p["publication_commit"] = "a" * 40
            if mutation == "edge": facts["historical_displacements"][0]["source_ref"]["path"] = "docs/wrong.md"
            with self.subTest(mutation=mutation), self.assertRaises(sp.GovernsValidationError):
                dp._plan_doctrine_outputs(data, facts, p)

    def test_no_migration_plan_has_no_slots_or_fabricated_domain(self):
        data = loaded()
        plan = dp._plan_doctrine_outputs(data, original(), None)
        result = dp._materialize_doctrine_outputs(plan, None)
        meta = json.loads(result[data["paths"]["_meta.json"]])
        self.assertNotIn("migration", meta)
        self.assertNotIn("implementation-migration", meta["input_domain"])

    def test_raw_ledger_and_fingerprint_frames_cannot_disagree(self):
        for mutation in ("absent-bound", "raw-digest", "normalized-rows", "bindings", "invariants"):
            data = loaded(); facts = summary_fixtures.facts()
            if mutation == "absent-bound": facts["dependency_fingerprints"].append(dict(path=data["ledger"]["path"], sha256="a" * 64))
            if mutation == "raw-digest": data["digests"]["reconciliations_sha256"] = "a" * 64
            if mutation == "normalized-rows": data["ledger"]["text"] = 'config_version: "1"\nreconciliations: []\n'
            if mutation == "bindings": data["digests"]["run_bindings_sha256"] = "a" * 64
            if mutation == "invariants": data["digests"]["invariants_sha256"] = "a" * 64
            with self.subTest(mutation=mutation), self.assertRaises(sp.GovernsValidationError): self.compile(data, facts)

    def test_original_and_historical_retirements_cannot_orphan_signed_pairings(self):
        import yaml
        for archived in (False, True):
            data = loaded(); rows = [dict(invariant="INV-0001", handle=summary_fixtures.OLD,
                verdict="compatible", signed="2026-10-03", rationale=None, content_digest="a" * 64)]
            text = yaml.safe_dump(dict(config_version="1", reconciliations=rows))
            data["reconciliations"] = dp.parse_reconciliations(text); data["ledger"]["text"] = text
            data["digests"]["reconciliations_sha256"] = hashlib.sha256(text.encode()).hexdigest()
            if archived: data["records"] = [r for r in data["records"] if r["handle"] != summary_fixtures.DEMOTED]
            with self.subTest(archived=archived), self.assertRaises(sp.GovernsValidationError): self.compile(data)


class PreloadedEvidence(unittest.TestCase):
    def test_preloaded_core_never_resolves_filesystem_evidence(self):
        record = dict(handle="OBS-0001/fact", source_adr="OBS-0001", adr_num=1,
            source_kind="observation", source_status="ratified", domain="testing", rule="Observed fact.",
            scope="reader", provenance="recovered", evidence=["source.py:1-1"], retired_by=[])
        with mock.patch.object(dp, "evidence_resolves", side_effect=AssertionError("filesystem read")):
            entries = dp._build_domain_entries_loaded([record], [], [], {}, None,
                tree_name="docs", evidence_facts={(record["handle"], "source.py:1-1"): True})
        self.assertEqual(entries[0]["rules"][0]["basis"], dp.BASIS_EVIDENCE_RESOLVES)
