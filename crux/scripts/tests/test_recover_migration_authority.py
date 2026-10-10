"""Recovery checks source origin before correlation or any candidate mutation."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS.parents[1] / "tools/tests"))
import yaml
try:  # package-relative when run as a module, flat when run by discovery
    from ._dev_surface import IS_STAGED_ARTIFACT
except ImportError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _dev_surface import IS_STAGED_ARTIFACT
try:
    import test_summarize_migration_consumers as published
except ModuleNotFoundError:
    if IS_STAGED_ARTIFACT:  # tools/tests is dev-repo-only and never crosses the sync boundary
        raise unittest.SkipTest("tools/tests/test_summarize_migration_consumers.py is absent "
                                "in the staged artifact")
    raise
from crux.arch import recover


def memory(state):
    return copy.deepcopy((state.rows, state.stale_anchors, state.successor_counters))


def candidate(anchor, evidence, rule="A separate source fact."):
    return dict(id=anchor, anchor_id=anchor, rule=rule, evidence=[evidence], domain="testing")


class OrdinaryRecovery(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / ".bionic.yml").write_text('config_version: "1"\ndocs_dir: docs\n')
        self.tree = self.root / "docs"; self.obs = self.tree / "observations"
        self.obs.mkdir(parents=True); (self.tree / "adrs").mkdir()
        (self.tree / "manifest.yml").write_text('schema_version: "5"\nconcerns_enabled: [adrs, observations]\n')
        (self.root / "source.py").write_text("VALUE = 1\nOTHER = 2\n")
        self.state = recover.StateFile(self.tree / "arch/_recovered/state.yml", contained_under=self.tree)

    def record(self, anchor="a" * 16, *, kind=None, dirname=None, number=1):
        path = (dirname or self.obs) / f"OBS-{number:04d}-fact.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        doc = dict(id=f"OBS-{number:04d}", status="ratified", title="An observed fact", date="2026-10-03",
            anchor_id=anchor, provenance="recovered", evidence=["source.py:1-1"], recovered_id=anchor,
            governs=[dict(handle=f"OBS-{number:04d}/fact-{number}", domain="testing", rule="A separate source fact.",
                scope="source", provenance="recovered")])
        if kind: doc["record_type"] = kind
        path.write_text("---\n" + yaml.safe_dump(doc) + "---\n\nA factual record.\n")
        return path

    def recorded(self):
        return recover.read_recorded_observations(self.obs, repo_root=self.root)

    def test_checked_single_preserves_successor_refresh_and_existing_counters(self):
        self.record(); recorded = self.recorded()
        same = candidate("a" * 16, "source.py:1-1")
        self.assertEqual(recover.checked_emit_candidate(self.root, self.state, recorded, same), "recorded")
        changed = candidate("a" * 16, "source.py:2-2", "A changed source fact.")
        self.assertEqual(recover.checked_emit_candidate(self.root, self.state, recorded, changed), "successor")
        self.assertEqual(self.state.successor_counters, {"a" * 16: 1})
        changed["rule"] = "A refreshed source fact."
        self.assertEqual(recover.checked_emit_candidate(self.root, self.state, recorded, changed), "present")
        self.assertEqual(self.state.successor_counters, {"a" * 16: 1})
        self.assertEqual(self.state.rows["a" * 16 + "+1"]["predecessor_id"], "OBS-0001")

    def test_supplied_recorded_map_is_not_source_approval(self):
        poisoned = {"a" * 16: dict(id="OBS-9999", status="ratified", rule="A separate source fact.", evidence=["source.py:1-1"])}
        before = memory(self.state)
        with self.assertRaises(ValueError):
            recover.checked_emit_candidate(self.root, self.state, poisoned, candidate("a" * 16, "source.py:1-1"))
        self.assertEqual(memory(self.state), before)

    def test_full_present_roster_is_checked_before_emission_prune_and_stale_work(self):
        self.record("b" * 16); recorded = self.recorded()
        self.state.rows["prune-me"] = dict(id="prune-me", state="rejected")
        self.state.stale_anchors = {"keep": dict(obs_id="OBS-1234", status="observed")}
        self.state.successor_counters = {"b" * 16: 7}
        self.state.save()
        valid = candidate("fresh", "source.py:2-2")
        held = self.tree / "promptbooks/runs/PB-9999-demo/evidence/implementation-cycles/retained-repositories/source.py"
        held.parent.mkdir(parents=True); held.write_text("VALUE = 1\n")
        invalid = candidate("held", held.relative_to(self.root).as_posix() + ":1-1")
        before = memory(self.state); files = published.snapshot(self.root)
        with self.assertRaises(ValueError):
            recover.checked_emit_candidates(self.root, self.state, recorded, [valid], present_candidates=[valid, invalid])
        self.assertEqual(memory(self.state), before)
        self.assertEqual(published.snapshot(self.root), files)

    def test_emitted_subset_cannot_invent_presence_and_caller_rows_are_not_mutated(self):
        valid = candidate("fresh", "source.py:1-1"); before = copy.deepcopy(valid)
        with self.assertRaises(ValueError):
            recover.checked_emit_candidates(self.root, self.state, {}, [valid], present_candidates=[])
        self.assertEqual(valid, before); self.assertEqual(memory(self.state), ({}, {}, {}))
        self.assertEqual(recover.checked_emit_candidates(self.root, self.state, {}, [valid]), ["observed"])
        self.assertEqual(valid, before)

    def test_each_present_candidate_requires_its_own_evidence(self):
        valid = candidate("fresh", "source.py:1-1")
        empty = dict(id="empty", evidence=[])
        before = memory(self.state)
        with self.assertRaises(ValueError):
            recover.checked_emit_candidates(self.root, self.state, {}, [valid], present_candidates=[valid, empty])
        with self.assertRaises(ValueError):
            recover.checked_emit_candidate(self.root, self.state, {}, empty)
        self.assertEqual(memory(self.state), before)

    def test_empty_roster_and_missing_correlations_still_use_shared_context(self):
        self.assertEqual(self.recorded(), {})
        self.obs.rmdir()
        self.assertIsNone(recover.find_ratified_observation(self.obs, "absent", repo_root=self.root))
        self.assertEqual(recover.checked_emit_candidates(self.root, self.state, {}, []), [])

    def test_full_source_roster_preserves_covered_presence_and_prunes_absent_rejections(self):
        self.record(); recorded = self.recorded()
        self.state.rows["absent"] = dict(id="absent", state="rejected")
        covered = candidate("a" * 16, "source.py:1-1")
        fresh = candidate("fresh", "source.py:2-2")
        self.assertEqual(recover.checked_emit_candidates(self.root, self.state, recorded, [fresh],
            present_candidates=[covered, fresh]), ["observed"])
        self.assertNotIn("absent", self.state.rows)
        self.assertNotIn("a" * 16, self.state.rows)
        self.assertNotIn("a" * 16, self.state.stale_anchors)
        self.assertIn("fresh", self.state.rows)

    def test_same_basename_outside_the_holding_is_ordinary_source(self):
        source = self.root / "source/retained-repositories/fact.py"
        source.parent.mkdir(parents=True); source.write_text("VALUE = 1\n")
        evidence = source.relative_to(self.root).as_posix() + ":1-1"
        self.assertEqual(recover.checked_emit_candidate(self.root, self.state, {}, candidate("fresh", evidence)), "observed")

    def test_late_refusal_after_staged_successor_preserves_caller_memory(self):
        self.record(); recorded = self.recorded()
        valid = candidate("a" * 16, "source.py:2-2", "A changed source fact.")
        before = memory(self.state)
        import observation_admission as admission
        original = admission.recovery_source_problems; calls = []; emitted = []
        emit = recover.emit_candidate
        def refuse_second(*args, **kwargs):
            if kwargs["evidence"]:
                calls.append(1)
                if len(calls) == 2: return ["late source refusal"]
            return original(*args, **kwargs)
        def staged_emit(*args, **kwargs):
            result = emit(*args, **kwargs); emitted.append(result); return result
        with mock.patch.object(admission, "recovery_source_problems", side_effect=refuse_second), \
             mock.patch.object(recover, "emit_candidate", side_effect=staged_emit):
            with self.assertRaises(ValueError):
                recover.checked_emit_candidates(self.root, self.state, recorded, [valid])
        self.assertEqual(emitted, ["successor"])
        self.assertEqual(memory(self.state), before)

    def test_typed_and_quoted_reasoning_cannot_correlate_as_observation(self):
        for kind in ("implementation-decision", "implementation-result", "implementation-annotation", "implementation-migration"):
            path = self.record(kind=kind)
            try:
                before = published.snapshot(self.root)
                with self.subTest(kind=kind), self.assertRaises(ValueError): self.recorded()
                with self.subTest(kind=kind), self.assertRaises(ValueError):
                    recover.find_ratified_observation(self.obs, "a" * 16, repo_root=self.root)
                self.assertEqual(published.snapshot(self.root), before)
            finally: path.unlink()

    def test_exact_successor_correlation_and_predecessor_exclusion(self):
        self.record(number=1); self.record(number=2)
        self.assertEqual(recover.find_ratified_observation(self.obs, "a" * 16 + "+1", "OBS-0001", repo_root=self.root), "OBS-0002")
        self.assertEqual(recover.find_ratified_observation(self.obs, "a" * 16 + "+1", repo_root=self.root), "OBS-0001")

    def test_selected_tree_is_explicit_and_root_config_is_unchanged(self):
        alternate = self.root / "notes"; (alternate / "adrs").mkdir(parents=True)
        (alternate / "manifest.yml").write_text((self.tree / "manifest.yml").read_text())
        self.record(dirname=alternate / "observations")
        recorded = recover.read_recorded_observations(alternate / "observations", repo_root=self.root, docs_dir="notes")
        state = recover.StateFile(alternate / "arch/_recovered/state.yml", contained_under=alternate)
        before = published.snapshot(self.root)
        self.assertEqual(recover.checked_emit_candidate(self.root, state, recorded, candidate("fresh", "source.py:2-2"), docs_dir="notes"), "observed")
        self.assertEqual(published.snapshot(self.root), before)
        with self.assertRaises(ValueError):
            recover.read_recorded_observations(alternate / "observations", repo_root=self.root)

    def test_checked_adr_correlation_rejects_typed_record_but_accepts_genuine_metadata(self):
        path = self.tree / "adrs/ADR-0001-correlated.md"
        text = "---\nid: ADR-0001\nstatus: Proposed\nrecovered_id: " + "a" * 16 + "\n---\nA real architecture requirement.\n"
        path.write_text(text)
        self.assertEqual(recover.find_ratified_adr(path.parent, "a" * 16, repo_root=self.root), "ADR-0001")
        path.write_text(text.replace("id: ADR-0001", "record_type: implementation-decision\nid: ADR-0001"))
        with self.assertRaises(ValueError): recover.find_ratified_adr(path.parent, "a" * 16, repo_root=self.root)

    def test_adr_correlation_refuses_concern_alias_into_retained_holding(self):
        held = self.tree / "promptbooks/runs/PB-9999-demo/evidence/implementation-cycles/retained-repositories/ADR-0001-held.md"
        held.parent.mkdir(parents=True)
        held.write_text("---\nid: ADR-0001\nstatus: Proposed\nrecovered_id: " + "a" * 16 + "\n---\nRetained evidence.\n")
        (self.tree / "adrs/ADR-0001-held.md").symlink_to(held)
        read = Path.read_text
        def refuse_held_read(path, *args, **kwargs):
            if path.resolve() == held.resolve():
                raise AssertionError("correlation read held source before containment refusal")
            return read(path, *args, **kwargs)
        with mock.patch.object(Path, "read_text", refuse_held_read), self.assertRaises(ValueError):
            recover.find_ratified_adr(self.tree / "adrs", "a" * 16, repo_root=self.root)


class PublishedRecovery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        published.PublishedConsumers.setUpClass.__func__(cls)
        cls.historical_lines = {}; cls.fact_lines = {}
        for number in ("0110", "0093"):
            source = next(cls.adrs.glob(f"ADR-{number}-*.md"))
            cls.historical_lines[number] = len(source.read_text().splitlines())
            source.write_text(source.read_text() + "\nDistinct source fact: the parser exists.\n")
            cls.fact_lines[number] = len(source.read_text().splitlines())
        (cls.root / "source.py").write_text("VALUE = 1\n")
        published.sup.commit_all(cls.root, "synthetic distinct source fact")

    def state(self):
        return recover.StateFile(self.root / "docs/arch/_recovered/state.yml", contained_under=self.root / "docs")

    def test_historical_mixed_and_body_only_spans_do_not_become_candidates(self):
        for number in ("0110", "0093"):
            source = next(self.adrs.glob(f"ADR-{number}-*.md"))
            last = self.historical_lines[number]
            evidence = source.relative_to(self.root).as_posix() + f":{last}-{last}"
            state = self.state(); before = memory(state)
            with self.subTest(number=number), self.assertRaises(ValueError):
                recover.checked_emit_candidate(self.root, state, {}, candidate("historical", evidence))
            self.assertEqual(memory(state), before)
        state = self.state()
        self.assertEqual(recover.checked_emit_candidate(self.root, state, {}, candidate("source", "source.py:1-1")), "observed")

    def test_straddling_reasoning_refuses_while_same_file_distinct_fact_passes(self):
        for number in ("0110", "0093"):
            source = next(self.adrs.glob(f"ADR-{number}-*.md"))
            rel = source.relative_to(self.root).as_posix(); line = self.historical_lines[number]
            state = self.state(); before = memory(state)
            with self.subTest(number=number), self.assertRaises(ValueError):
                recover.checked_emit_candidate(self.root, state, {}, candidate("mixed", f"{rel}:{line - 1}-{line + 1}"))
            self.assertEqual(memory(state), before)
            line = self.fact_lines[number]
            self.assertEqual(recover.checked_emit_candidate(self.root, state, {}, candidate("fact", f"{rel}:{line}-{line}")), "observed")

    def test_lost_proof_overrides_source_positive_and_leaves_files_and_memory(self):
        witness = self.fixture.witness; original = witness.read_bytes(); state = self.state()
        try:
            witness.unlink(); files = published.snapshot(self.root); before = memory(state)
            with self.assertRaises((ValueError, published.migration.Refused)):
                recover.checked_emit_candidates(self.root, state, {}, [candidate("source", "source.py:1-1")])
            self.assertEqual(memory(state), before); self.assertEqual(published.snapshot(self.root), files)
        finally: witness.write_bytes(original)
