"""Closed format-two public shapes; synthetic examples grant no council approval."""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path

import yaml

SCRIPTS = Path(__file__).resolve().parent.parent
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("_vp_cycle_contracts", SCRIPTS / "validate-promptbook.py")
vp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vp)


def errors(doc, kind="promptbook"):
    schema = vp.load_schema(ROOT / "schemas" / f"{kind}.schema.json")
    result = []
    vp.validate(doc, schema, "#", "#", result, kind)
    return result


def slot(name="implementation-1"):
    return dict(slot=name, slug="keyed-index", scope=["src/index.py"],
                constraint_refs=["DEMO-ADR-0001/stable-api"])


def book(kind="implementation", version="2"):
    primary = "adr" if kind == "adr" else "verify" if kind == "verify" else "implementation"
    prompts = []
    for tag, count in ((f"{primary}-1", 4), ("dev-1", 4), ("review-1", 3), (None, 2)):
        for _ in range(count):
            prompt = dict(n=len(prompts) + 1, title="synthetic", purpose="purpose",
                          prompt="body", expected_output="evidence")
            if tag:
                prompt["module_tag"] = tag
            prompts.append(prompt)
    counts = dict(adrs=int(primary == "adr"), implementations=int(primary == "implementation"),
                  verify=int(primary == "verify"), dev_loops=1, review_cycles=1)
    doc = dict(format_version=version, id="DEMO-PB-0001", title="synthetic", status="active",
               created_at="2026-10-02", total_prompts=13, current_run=None, current_prompt=None,
               forked_from=None, tags=["cycle"], goal="Outcome; Evidence; Constraint",
               strategy="synthetic", prompts=prompts, cycle_kind=kind, modules=counts,
               implementation_slots=[slot()] if kind == "implementation" else [])
    if kind == "patch":
        doc.pop("modules")
        doc["total_prompts"] = 5
        doc["blast_radius"] = ["src/index.py"]
        doc["prompts"] = [dict(n=i + 1, title=phase, purpose="purpose", prompt="body",
                               expected_output="evidence", phase=phase)
                          for i, phase in enumerate(("verify", "plan", "implement", "review", "summary"))]
    if version == "1":
        doc.pop("implementation_slots")
        if kind != "patch":
            doc["modules"] = {k: v for k, v in counts.items() if v}
    return doc


def migration_slot():
    item = slot()
    item["scope"] = ["knowledge/adrs/migrations"]
    item["migration_batch"] = {"role": "migration-batch",
                               "path": "knowledge/adrs/migrations/implementation-pilot-001.yaml"}
    return item


def binding():
    ref = dict(path="evidence/revision.yaml", sha256="a" * 64)
    return dict(slot="implementation-1", book_id="DEMO-PB-0001", run_id="RUN-001",
                book_content_hash="sha256:" + "b" * 64, revision=copy.deepcopy(ref),
                gate_prompt=4, deciding_record=copy.deepcopy(ref), retained_subject=copy.deepcopy(ref),
                context=copy.deepcopy(ref))


def run(version="2"):
    doc = dict(format_version=version, run_id="RUN-001", book_id="DEMO-PB-0001",
               book_content_hash="sha256:" + "b" * 64, started_at="2026-10-02T00:00:00Z",
               completed_at=None, status="in_progress", current_prompt=1,
               prompts=[dict(n=1, title="synthetic", state="pending", started=None,
                             completed=None, result="", artifacts=[])])
    if version == "2":
        doc["implementation_bindings"] = []
    return doc


class BookContracts(unittest.TestCase):
    def test_original_format_one_books_remain_valid(self):
        for kind in ("adr", "verify", "patch"):
            with self.subTest(kind=kind):
                self.assertEqual(errors(book(kind, "1")), [])

    def test_all_four_format_two_kinds_are_admitted(self):
        for kind in ("adr", "implementation", "verify", "patch"):
            with self.subTest(kind=kind):
                self.assertEqual(errors(book(kind)), [])

    def test_format_two_patch_forbids_even_empty_modules(self):
        self.assertEqual(errors(book("patch")), [])
        for modules in ({}, dict(adrs=1, implementations=1, verify=1,
                                 dev_loops=1, review_cycles=1)):
            doc = book("patch")
            doc["modules"] = modules
            with self.subTest(modules=modules):
                self.assertTrue(errors(doc))

    def test_format_one_patch_module_shape_admission_is_unchanged(self):
        # Historical schema admission remains distinct from cycle policy checks.
        self.assertEqual(errors(book("patch", "1")), [])
        for modules in ({}, dict(adrs=1, verify=1, dev_loops=1, review_cycles=1)):
            doc = book("patch", "1")
            doc["modules"] = modules
            with self.subTest(modules=modules):
                self.assertEqual(errors(doc), [])

    def test_missing_unknown_discriminators_are_refused(self):
        for field in ("format_version", "cycle_kind", "implementation_slots"):
            doc = book()
            del doc[field]
            with self.subTest(field=field):
                self.assertTrue(errors(doc))
        for field, value in (("format_version", "3"), ("cycle_kind", "unknown")):
            doc = book()
            doc[field] = value
            self.assertTrue(errors(doc))

    def test_format_two_refuses_grandfather_presence_at_either_value(self):
        for value in (True, False, None):
            doc = book()
            doc.update(cycle_grandfathered=value, grandfather_reason="old")
            with self.subTest(value=value):
                self.assertTrue(errors(doc))
        doc = book()
        doc["grandfather_reason"] = "old"
        self.assertTrue(errors(doc))

    def test_original_grandfather_contract_remains_valid(self):
        doc = book("adr", "1")
        doc["cycle_grandfathered"] = False
        self.assertEqual(errors(doc), [])
        doc["cycle_grandfathered"] = True
        self.assertTrue(errors(doc))
        doc["grandfather_reason"] = "historical"
        self.assertEqual(errors(doc), [])

    def test_format_one_cannot_smuggle_new_semantics(self):
        for mutation in (lambda d: d.update(implementation_slots=[]),
                         lambda d: d.update(cycle_kind="implementation"),
                         lambda d: d["modules"].update(implementations=1),
                         lambda d: d["prompts"][0].update(module_tag="implementation-1")):
            doc = book("adr", "1")
            mutation(doc)
            self.assertTrue(errors(doc))

    def test_format_two_requires_all_module_counts(self):
        for key in book()["modules"]:
            doc = book()
            del doc["modules"][key]
            self.assertTrue(errors(doc), key)

    def test_module_counts_are_nonnegative_integers_with_dev_review_floor(self):
        for key in book()["modules"]:
            for value in (-1, True, 1.5, "1"):
                doc = book()
                doc["modules"][key] = value
                with self.subTest(key=key, value=value):
                    self.assertTrue(errors(doc))
        for key in ("dev_loops", "review_cycles"):
            doc = book()
            doc["modules"][key] = 0
            self.assertTrue(errors(doc))

    def test_schema_closes_slot_fields_and_requires_nonempty_scope_constraints(self):
        for key in slot():
            doc = book()
            del doc["implementation_slots"][0][key]
            self.assertTrue(errors(doc), key)
        for key in ("scope", "constraint_refs"):
            doc = book()
            doc["implementation_slots"][0][key] = []
            self.assertTrue(errors(doc))
        doc = book()
        doc["implementation_slots"][0]["governs"] = []
        self.assertTrue(errors(doc))

    def test_slot_lexical_boundaries(self):
        for key, values in dict(slot=["adr-1", "implementation-0", "verify-1\n"],
                                slug=["a b", "../a", "a\n"],
                                scope=[["../a"], ["/a"], ["a/./b"], ["a\\b"], ["a\n"]],
                                constraint_refs=[["rule:stable-api"], ["PB-0001/a"],
                                                 ["ADR-0001/a\n"]]).items():
            for value in values:
                doc = book()
                doc["implementation_slots"][0][key] = value
                with self.subTest(key=key, value=value):
                    self.assertTrue(errors(doc))


    def test_explicit_migration_slot_admits_only_closed_role_path_shape(self):
        doc = book(); doc["implementation_slots"] = [migration_slot()]
        self.assertEqual(errors(doc), [])
        vp.validate_format_two(doc)
        for key, value in (("role", "implementation-decision"), ("path", "../batch.yaml"),
                           ("path", "/batch.yaml"), ("authority", True)):
            damaged = copy.deepcopy(doc); damaged["implementation_slots"][0]["migration_batch"][key] = value
            self.assertTrue(errors(damaged), (key, value))
            with self.assertRaises(ValueError): vp.validate_format_two(damaged)
        for field in ("role", "path"):
            damaged = copy.deepcopy(doc); del damaged["implementation_slots"][0]["migration_batch"][field]
            self.assertTrue(errors(damaged))
            with self.assertRaises(ValueError): vp.validate_format_two(damaged)

    def test_migration_path_scope_slot_and_duplicate_role_refuse(self):
        doc = book(); doc["implementation_slots"] = [migration_slot()]
        for mutation in ("scope", "duplicate", "verify", "role-top"):
            bad = copy.deepcopy(doc)
            if mutation == "scope": bad["implementation_slots"][0]["scope"] = ["src/index.py"]
            if mutation == "duplicate": bad["implementation_slots"].append(slot())
            if mutation == "verify":
                bad = book("verify"); item = migration_slot(); item["slot"] = "verify-1"; bad["implementation_slots"] = [item]
            if mutation == "role-top": bad["implementation_slots"][0]["role"] = "migration-batch"
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): vp.validate_format_two(bad)

    def test_batch_role_and_exact_path_are_hash_bound_without_legacy_changes(self):
        ordinary = book(); original = vp.compute_book_hash(ordinary)
        candidate = copy.deepcopy(ordinary); candidate["implementation_slots"] = [migration_slot()]
        self.assertNotEqual(original, vp.compute_book_hash(candidate))
        for field, value in (("role", "other"), ("path", "knowledge/adrs/migrations/another.yaml")):
            changed = copy.deepcopy(candidate); changed["implementation_slots"][0]["migration_batch"][field] = value
            self.assertNotEqual(vp.compute_book_hash(candidate), vp.compute_book_hash(changed))
        self.assertEqual(original, vp.compute_book_hash(ordinary))


class RunContracts(unittest.TestCase):
    def owned_run(self, source):
        snapshot = run(); snapshot["book_content_hash"] = vp.compute_book_hash(source)
        snapshot["prompts"] = [dict(n=p["n"], title=p["title"], state="pending", started=None,
                                   completed=None, result="", artifacts=[]) for p in source["prompts"]]
        return snapshot

    def test_every_ordinary_binding_owns_declared_slot_identity_and_close(self):
        source = book(); snapshot = self.owned_run(source)
        entry = binding(); entry["book_content_hash"] = snapshot["book_content_hash"]
        snapshot["implementation_bindings"] = [entry]
        vp.validate_format_two_run(snapshot, source)
        for key, value in (("slot", "implementation-2"), ("book_id", "DEMO-PB-0002"),
                           ("run_id", "RUN-002"), ("book_content_hash", "sha256:" + "c" * 64),
                           ("gate_prompt", 5)):
            damaged = copy.deepcopy(snapshot); damaged["implementation_bindings"][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                vp.validate_format_two_run(damaged, source)
        duplicate = copy.deepcopy(snapshot); duplicate["implementation_bindings"].append(copy.deepcopy(entry))
        with self.assertRaises(ValueError): vp.validate_format_two_run(duplicate, source)

    def test_absent_ordinary_field_is_no_entry_but_present_malformed_containers_refuse(self):
        source = book(); snapshot = self.owned_run(source)
        snapshot.pop("implementation_bindings")
        vp.validate_format_two_run(snapshot, source)
        for present in (None, "", 1, {}, [None]):
            damaged = copy.deepcopy(snapshot); damaged["implementation_bindings"] = present
            with self.subTest(present=present), self.assertRaises(ValueError):
                vp.validate_format_two_run(damaged, source)
        # Structural absence grants no writer admission or historical approval.
        self.assertTrue(errors(snapshot, "run"))

    def test_distinct_declared_closes_keep_role_and_subject_identities_separate(self):
        source = book(); inserted = copy.deepcopy(source["prompts"][:4])
        for prompt in inserted: prompt["module_tag"] = "implementation-2"
        source["prompts"][4:4] = inserted
        for n, prompt in enumerate(source["prompts"], 1): prompt["n"] = n
        source["total_prompts"] = len(source["prompts"]); source["modules"]["implementations"] = 2
        second = slot("implementation-2"); source["implementation_slots"].append(second)
        snapshot = self.owned_run(source)
        first = binding(); first["book_content_hash"] = snapshot["book_content_hash"]
        other = copy.deepcopy(first); other.update(slot="implementation-2", gate_prompt=8)
        snapshot["implementation_bindings"] = [first, other]
        # Shape/ownership only: repeated subject bytes at distinct closes are not globally deduplicated.
        vp.validate_format_two_run(snapshot, source)
        second.update(migration_slot()); second["slot"] = "implementation-2"
        snapshot = self.owned_run(source)
        first["book_content_hash"] = snapshot["book_content_hash"]
        other.update(book_content_hash=snapshot["book_content_hash"])
        other["batch"] = other.pop("revision"); other["batch"]["path"] = second["migration_batch"]["path"]
        snapshot.update(implementation_bindings=[first], migration_bindings=[other])
        vp.validate_format_two_run(snapshot, source)
        for fault in ("close", "duplicate", "cross-role"):
            damaged = copy.deepcopy(snapshot)
            if fault == "close": damaged["migration_bindings"][0]["gate_prompt"] = 4
            if fault == "duplicate": damaged["migration_bindings"].append(copy.deepcopy(other))
            if fault == "cross-role": damaged["implementation_bindings"][0]["slot"] = "implementation-2"
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                vp.validate_format_two_run(damaged, source)

    def test_original_run_and_initial_format_two_run_pass(self):
        self.assertEqual(errors(run("1"), "run"), [])
        self.assertEqual(errors(run(), "run"), [])

    def test_format_two_bindings_are_required_and_format_one_forbids_them(self):
        doc = run()
        del doc["implementation_bindings"]
        self.assertTrue(errors(doc, "run"))
        doc = run("1")
        doc["implementation_bindings"] = []
        self.assertTrue(errors(doc, "run"))

    def test_exact_binding_shape_passes_without_claiming_approval(self):
        doc = run()
        doc["implementation_bindings"] = [binding()]
        self.assertEqual(errors(doc, "run"), [])
        # A schema admits shape, not a successful/committed gate; core proves that.
        self.assertEqual(doc["prompts"][0]["state"], "pending")

    def test_binding_requires_every_identity_and_evidence_field(self):
        for key in binding():
            doc = run()
            value = binding()
            del value[key]
            doc["implementation_bindings"] = [value]
            self.assertTrue(errors(doc, "run"), key)

    def test_binding_refuses_untyped_authority_and_unsafe_evidence_refs(self):
        mutations = [("governs", []), ("gate_prompt", True), ("book_id", "ADR-0001"),
                     ("run_id", "RUN-001\n"), ("slot", "adr-1")]
        for key, value in mutations:
            doc = run()
            item = binding()
            item[key] = value
            doc["implementation_bindings"] = [item]
            self.assertTrue(errors(doc, "run"), key)
        for key in ("revision", "deciding_record", "retained_subject", "context"):
            for bad in (dict(path="../evidence", sha256="a" * 64),
                        dict(path="evidence", sha256="oops"),
                        dict(path="evidence", sha256="a" * 64, version="2")):
                doc = run()
                item = binding()
                item[key] = bad
                doc["implementation_bindings"] = [item]
                self.assertTrue(errors(doc, "run"), (key, bad))


    def test_migration_binding_has_batch_not_revision_and_never_upgrades_format_one(self):
        item = binding(); item["batch"] = item.pop("revision")
        doc = run(); doc["migration_bindings"] = [item]
        self.assertEqual(errors(doc, "run"), [])
        for mutation in ("revision", "batch-missing", "unknown", "unsafe"):
            damaged = copy.deepcopy(doc); entry = damaged["migration_bindings"][0]
            if mutation == "revision": entry["revision"] = copy.deepcopy(entry["batch"])
            if mutation == "batch-missing": del entry["batch"]
            if mutation == "unknown": entry["authority"] = True
            if mutation == "unsafe": entry["batch"]["path"] = "../batch.yaml"
            with self.subTest(mutation=mutation): self.assertTrue(errors(damaged, "run"))
        legacy = run("1"); legacy["migration_bindings"] = []
        self.assertTrue(errors(legacy, "run"))
        self.assertEqual(errors(run(), "run"), [])
        self.assertNotIn("migration_bindings", run())

    def test_run_matches_explicit_batch_role_path_and_container(self):
        source = book(); source["implementation_slots"] = [migration_slot()]
        snapshot = run(); snapshot["book_content_hash"] = vp.compute_book_hash(source)
        snapshot["prompts"] = [dict(n=p["n"], title=p["title"]) for p in source["prompts"]]
        with self.assertRaises(ValueError): vp.validate_format_two_run(snapshot, source)
        snapshot["migration_bindings"] = []
        vp.validate_format_two_run(snapshot, source)
        entry = binding(); entry["batch"] = entry.pop("revision")
        entry["batch"]["path"] = source["implementation_slots"][0]["migration_batch"]["path"]
        entry["book_content_hash"] = snapshot["book_content_hash"]
        snapshot["migration_bindings"] = [entry]
        vp.validate_format_two_run(snapshot, source)
        for mutation in ("path", "slot", "revision", "cross-role"):
            damaged = copy.deepcopy(snapshot)
            if mutation == "path": damaged["migration_bindings"][0]["batch"]["path"] = "evidence/wrong.yaml"
            if mutation == "slot": damaged["migration_bindings"][0]["slot"] = "implementation-2"
            if mutation == "revision": damaged["migration_bindings"][0]["revision"] = damaged["migration_bindings"][0].pop("batch")
            if mutation == "cross-role": damaged["implementation_bindings"] = [binding()]
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): vp.validate_format_two_run(damaged, source)
        ordinary = book(); stale = copy.deepcopy(snapshot); stale["book_content_hash"] = vp.compute_book_hash(ordinary)
        with self.assertRaises(ValueError): vp.validate_format_two_run(stale, ordinary)


class RenderedAuthoringContracts(unittest.TestCase):
    def test_new_authoring_skeletons_render_as_closed_format_two_books(self):
        for name in ("cycle-promptbook-template", "iterate-promptbook-template", "patch-promptbook-template"):
            doc = yaml.safe_load((ROOT / "templates" / f"{name}.yaml").read_text())
            doc.update(id="DEMO-PB-0001", created_at="2026-10-02")
            if doc["cycle_kind"] == "patch":
                doc["blast_radius"] = ["src/index.py"]
            with self.subTest(name=name):
                self.assertEqual(doc["format_version"], "2")
                self.assertEqual(errors(doc), [])
                vp.validate_format_two(doc)

    def test_dedicated_partial_renders_four_ordered_prompts_in_a_real_book_shape(self):
        path = ROOT / "templates" / "cycle-module-implementation.yaml"
        prompts = yaml.safe_load(path.read_text().replace("{M}", "1").replace("{IMPLEMENTATION_TOPIC}", "index"))
        doc = book()
        for n, item in enumerate(prompts, 1):
            item["n"] = n
        doc["prompts"][:4] = prompts
        self.assertEqual(len(prompts), 4)
        self.assertEqual(errors(doc), [])
        vp.validate_format_two(doc)

    def test_combined_adr_implementation_assembly_requires_its_slot(self):
        doc = book("adr")
        partial = (ROOT / "templates" / "cycle-module-implementation.yaml").read_text()
        prompts = yaml.safe_load(partial.replace("{M}", "1").replace("{IMPLEMENTATION_TOPIC}", "index"))
        doc["prompts"][4:4] = prompts
        for n, prompt in enumerate(doc["prompts"], 1):
            prompt["n"] = n
        doc["total_prompts"] = len(doc["prompts"])
        doc["modules"]["implementations"] = 1
        doc["implementation_slots"] = [slot()]
        self.assertEqual(errors(doc), [])
        vp.validate_format_two(doc)
        doc["implementation_slots"] = []
        with self.assertRaises(vp.FormatTwoError) as refusal:
            vp.validate_format_two(doc)
        self.assertEqual(refusal.exception.code, "dedicated-slot-missing")


if __name__ == "__main__":
    unittest.main()
