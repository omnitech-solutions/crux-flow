"""Tests for `survey.py` — the scripted survey sequence (docs/AGENTS.md §17.2).

`survey.py` runs the two deterministic ends of the survey (`derive` and
`project`), reports the human step in the middle (`mine`, read-only), and
checks the survey postconditions (`verify`, read-only — P2 here). It never
writes an observation file or an ADR in any phase — that is the writer
boundary, and the P3 postcondition tests here prove it by snapshotting the
concern directories (names + bytes) before and after each phase. The P4 leg
proves no persisted observation field carries a code excerpt, reusing
`check_observations.py` (the evidence grammar's owner).

Runs under the uv lane (PyYAML + httpx, because `derive` shells to
`derive-arch.py`). Every fixture is a tempdir; nothing touches the real tree
or the arch corpus.
"""
from __future__ import annotations

import contextlib
import importlib
import importlib.util
import io
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPTS_DIR = REPO_ROOT / "crux" / "scripts"
SURVEY = SCRIPTS_DIR / "survey.py"
SKILL_MD = REPO_ROOT / "crux" / "skills" / "recover-decisions" / "SKILL.md"

try:
    import yaml  # noqa: F401
    HAVE_YAML = True
except ImportError:
    HAVE_YAML = False

sys.path.insert(0, str(SCRIPTS_DIR))
R = importlib.import_module("crux.arch.recover")
if HAVE_YAML:
    # check_observations imports yaml at module top and exits 2 without it,
    # so it is only importable under the uv lane the tests below skip on.
    CO = importlib.import_module("check_observations")

# A backtick code span whose body crosses a line — the "newline-bearing code
# span" shape P4 forbids in any persisted observation frontmatter field.
_CODE_SPAN_WITH_NEWLINE_RE = re.compile(r"`[^`]*\n[^`]*`")


def _load_survey_module():
    spec = importlib.util.spec_from_file_location("survey_under_test", SURVEY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SURVEY), *args], capture_output=True, text=True,
    )


def _snapshot(directory: Path, pattern: str = "**/*") -> dict[str, bytes]:
    """{relative path: bytes} for every file under `directory` (empty when absent)."""
    if not directory.is_dir():
        return {}
    return {
        str(p.relative_to(directory)): p.read_bytes()
        for p in sorted(directory.glob(pattern)) if p.is_file()
    }


class _TreeFixture(unittest.TestCase):
    """A minimal ADR-less crux tree at <root>/<docs_dir>/ with adrs, arch, and
    observations enabled — the bootstrap case the survey exists for."""

    docs_dir = "bionic"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self._make_tree(self.docs_dir)

    # ---- fixture helpers -------------------------------------------------

    def _make_tree(self, docs_dir: str, *, write_config: bool = True):
        tree = self.root / docs_dir
        (tree / "adrs").mkdir(parents=True)
        (tree / "observations").mkdir(parents=True)
        (tree / "arch").mkdir(parents=True)
        (tree / "manifest.yml").write_text(
            'schema_version: "5"\n'
            "concerns_enabled:\n  - adrs\n  - arch\n  - observations\n"
            "adr:\n  next_number: 1\n"
            "observation:\n  next_number: 1\n",
            encoding="utf-8",
        )
        if write_config:
            (self.root / ".bionic.yml").write_text(
                f'config_version: "1"\ndocs_dir: {docs_dir}\n', encoding="utf-8")
        return tree

    @property
    def tree(self) -> Path:
        return self.root / self.docs_dir

    @property
    def state_path(self) -> Path:
        return self.tree / "arch" / "_recovered" / "state.yml"

    def _state(self) -> R.StateFile:
        return R.StateFile(self.state_path)

    def _candidate(self, anchor: str, *, rule: str = "uses the thing",
                   evidence: list[str] | None = None) -> dict:
        cid = R.candidate_id("external-dependency", anchor)
        return {
            "id": cid, "anchor_kind": "external-dependency",
            "canonical_anchor": anchor, "rule": rule,
            "evidence": evidence or ["src/app.py:1-2"],
        }

    def _write_observation(self, num: int, anchor_id: str, *, status: str = "ratified",
                           rule: str = "uses the thing",
                           evidence: list[str] | None = None,
                           domain: str = "deps",
                           slug: str = "uses-the-thing") -> Path:
        oid = f"OBS-{num:04d}"
        fm = {
            "id": oid, "title": rule, "status": status, "date": "2026-08-01",
            "observed_date": "2026-08-01",
            "ratified_date": "2026-08-02" if status == "ratified" else None,
            "rejected_date": None, "retired_date": None, "decided_date": None,
            "provenance": "recovered", "decided_by": None,
            "evidence": evidence or ["src/app.py:1-2"], "anchor_id": anchor_id,
            "related_invariants": [], "tags": ["survey"],
            "governs": [{"handle": f"{oid}/{slug}", "domain": domain,
                         "rule": rule, "scope": "src/", "provenance": "recovered"}],
        }
        import yaml as y
        path = self.tree / "observations" / f"{oid}-{slug}.md"
        path.write_text("---\n" + y.dump(fm, sort_keys=False) + "---\n\nBody.\n",
                        encoding="utf-8")
        return path

    def _write_source_file(self, rel: str = "src/app.py") -> Path:
        """A real repo file for `path:line-range` evidence to resolve against."""
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("import os\n\nVALUE = 1\n", encoding="utf-8")
        return path

    def _write_governs_adr(self, num: int = 1) -> Path:
        """A well-formed Accepted ADR carrying one authored governs rule — the
        prescriptive counterexample for the P2 negative twin."""
        aid = f"ADR-{num:04d}"
        path = self.tree / "adrs" / f"{aid}-choose-a-queue.md"
        path.write_text(
            f"---\nid: {aid}\ntitle: Choose a queue\nstatus: Accepted\n"
            "date: 2026-08-01\ntags: []\ngoverns:\n"
            f"  - handle: {aid}/queue-choice\n    domain: queueing\n"
            "    rule: Background jobs run on the queue.\n    scope: src/\n"
            "    provenance: authored\n---\n\n# Choose a queue\n\nBody.\n",
            encoding="utf-8")
        return path

    def _write_malformed_adr(self):
        """An ADR whose governs entry carries an out-of-enum provenance, so
        summarize-adrs.py returns a real exit-1 validation finding."""
        (self.tree / "adrs" / "ADR-0001-bad.md").write_text(
            "---\nid: ADR-0001\ntitle: Bad\nstatus: Accepted\ndate: 2026-08-01\n"
            "tags: []\ngoverns:\n  - handle: bad-rule\n    domain: x\n"
            "    rule: r\n    scope: s\n    provenance: bogus\n---\n\n# Bad\n",
            encoding="utf-8",
        )

    def _payload(self, proc: subprocess.CompletedProcess) -> dict:
        self.assertTrue(proc.stdout.strip(), f"no JSON on stdout; stderr={proc.stderr}")
        return json.loads(proc.stdout)


# ── exit lanes ─────────────────────────────────────────────────────────────

@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under uv)")
class ExitLaneTests(_TreeFixture):

    def test_verify_before_project_is_exit_2_and_todo_marker_is_gone(self):
        proc = _run("--repo-root", str(self.root), "--phase", "verify")
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn("project", proc.stderr)
        self.assertEqual(proc.stdout.strip(), "")
        self.assertNotIn("TODO(B1-verify)", SURVEY.read_text(encoding="utf-8"))

    def test_verify_docs_dir_disagreeing_with_config_is_exit_2(self):
        self._make_tree("other", write_config=False)
        proc = _run("--repo-root", str(self.root), "--docs-dir", "other",
                    "--phase", "verify")
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn("other", proc.stderr)

    def test_unknown_phase_is_exit_2(self):
        proc = _run("--repo-root", str(self.root), "--phase", "bogus")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout.strip(), "")

    def test_missing_tree_is_exit_2(self):
        (self.root / ".bionic.yml").write_text(
            'config_version: "1"\ndocs_dir: nowhere\n', encoding="utf-8")
        proc = _run("--repo-root", str(self.root), "--phase", "mine")
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn("nowhere", proc.stderr)

    def test_mine_clean_tree_is_exit_0(self):
        proc = _run("--repo-root", str(self.root), "--phase", "mine")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = self._payload(proc)
        self.assertEqual(payload["phase"], "mine")
        self.assertFalse(payload["state_file_exists"])
        self.assertEqual(payload["candidates"], 0)
        self.assertEqual(payload["human_commands"], [])

    def test_mine_with_pending_candidate_is_exit_1(self):
        sf = self._state()
        sf.upsert_observed(self._candidate("httpx"))
        sf.save()
        proc = _run("--repo-root", str(self.root), "--phase", "mine")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        payload = self._payload(proc)
        self.assertEqual(payload["candidates"], 1)
        self.assertEqual(payload["candidates_by_state"], {"observed": 1})

    def test_mine_corrupt_state_file_is_exit_2(self):
        self.state_path.parent.mkdir(parents=True)
        self.state_path.write_text("candidates: [\n  unterminated", encoding="utf-8")
        proc = _run("--repo-root", str(self.root), "--phase", "mine")
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn("corrupt", proc.stderr)

    def test_derive_is_exit_0_and_writes_the_arch_spine(self):
        proc = _run("--repo-root", str(self.root), "--phase", "derive")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = self._payload(proc)
        self.assertEqual(payload["phase"], "derive")
        self.assertEqual([s["script"] for s in payload["steps"]], ["derive-arch.py"])
        self.assertEqual(payload["steps"][0]["exit"], 0)
        self.assertTrue((self.tree / "arch" / "overview.md").is_file())

    def test_project_runs_summaries_then_doctrine_and_is_exit_0(self):
        proc = _run("--repo-root", str(self.root), "--phase", "project")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = self._payload(proc)
        self.assertEqual([s["script"] for s in payload["steps"]],
                         ["summarize-adrs.py", "compile-doctrine.py"])
        self.assertEqual([s["exit"] for s in payload["steps"]], [0, 0])
        self.assertTrue((self.tree / "adrs" / "summaries" / "rule-table.md").is_file())
        self.assertTrue((self.tree / "adrs" / "doctrine" / "index.md").is_file())

    def test_project_child_finding_is_exit_1_and_doctrine_does_not_run(self):
        self._write_malformed_adr()
        proc = _run("--repo-root", str(self.root), "--phase", "project")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        payload = self._payload(proc)
        self.assertEqual([s["script"] for s in payload["steps"]], ["summarize-adrs.py"])
        self.assertEqual(payload["steps"][0]["exit"], 1)
        self.assertIn("validation_errors", payload["steps"][0]["stdout"])
        self.assertFalse((self.tree / "adrs" / "doctrine").exists())

    def test_project_docs_dir_disagreeing_with_config_is_exit_2(self):
        self._make_tree("other", write_config=False)
        proc = _run("--repo-root", str(self.root), "--docs-dir", "other",
                    "--phase", "project")
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn("other", proc.stderr)
        self.assertFalse((self.root / "other" / "adrs" / "summaries").exists())
        self.assertFalse((self.tree / "adrs" / "summaries").exists())


# ── the headline postcondition, on the green path ──────────────────────────

@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under uv)")
class VerifyGreenPathTests(_TreeFixture):
    """M-TEST-4. The postcondition this whole sequence exists to produce — an
    ADR-less tree whose doctrine is descriptive-only and fully evidenced — had
    no positive assertion anywhere: the one test that ran it asserted only
    `assertNotEqual(rc, 2)`, which a tree with an empty doctrine also satisfies.
    The property does hold; what was missing was the assertion. The negative
    twin below is what keeps this one from passing vacuously."""

    def _survey_an_observation(self, **kw):
        self._write_source_file()
        self._write_observation(1, "a" * 16, **kw)
        proc = _run("--repo-root", str(self.root), "--phase", "project")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return _run("--repo-root", str(self.root), "--phase", "verify")

    def test_an_adr_less_tree_verifies_clean_and_descriptive(self):
        proc = self._survey_an_observation()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = self._payload(proc)
        self.assertTrue(payload["clean"])
        self.assertEqual(payload["findings"], [])
        self.assertEqual(payload["adr_files"], 0)
        self.assertEqual(payload["adr_record_paths"], [])
        self.assertEqual(payload["prescriptive_entries"], 0)
        self.assertEqual(payload["entries_without_evidence"], 0)
        self.assertEqual(payload["descriptive_entries"], 1)
        self.assertEqual(payload["doctrine_entries"], 1)
        # the entry itself, not just the counts: one domain, descriptive, and
        # traced to a `path:line-range` that resolves.
        entry = payload["entries"][0]
        self.assertEqual(entry["domain"], "deps")
        self.assertEqual(entry["authority"], "descriptive")
        self.assertEqual(entry["evidence"], ["src/app.py:1-2"])

    def test_an_unratified_record_contributes_no_doctrine_entry(self):
        # The counts above are real counts, not constants: an `observed` record
        # projects no rule row, so the same sequence yields zero entries and
        # verify reports the empty-doctrine finding instead.
        proc = self._survey_an_observation(status="observed")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        payload = self._payload(proc)
        self.assertEqual(payload["doctrine_entries"], 0)
        self.assertFalse(payload["clean"])
        self.assertIn("the doctrine index holds no domain entry", payload["findings"])

    def test_an_adr_defeats_both_postconditions(self):
        # The negative twin. An Accepted ADR carrying an authored governs rule
        # makes the tree non-ADR-less AND its domain prescriptive, so both
        # findings fire and `clean` goes false. Without this, every assertion
        # in the green-path test could be satisfied by code that always says
        # "descriptive, no ADRs".
        self._write_governs_adr()
        proc = self._survey_an_observation()
        self.assertEqual(proc.returncode, 1, proc.stderr)
        payload = self._payload(proc)
        self.assertFalse(payload["clean"])
        self.assertEqual(payload["adr_files"], 1)
        self.assertEqual(payload["prescriptive_entries"], 1)
        self.assertTrue(any("postcondition is an ADR-less tree" in f
                            for f in payload["findings"]))
        self.assertTrue(any("not descriptive-only" in f for f in payload["findings"]))


# ── tree resolution ────────────────────────────────────────────────────────

@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under uv)")
class TreeResolutionTests(_TreeFixture):
    docs_dir = "docs-tree"

    def test_non_default_docs_dir_from_bionic_yml(self):
        proc = _run("--repo-root", str(self.root), "--phase", "mine")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = self._payload(proc)
        self.assertEqual(payload["docs_dir"], "docs-tree")
        self.assertEqual(payload["state_file"], "docs-tree/arch/_recovered/state.yml")

    def test_docs_dir_flag_overrides_config(self):
        other = self._make_tree("alt", write_config=False)
        sf = R.StateFile(other / "arch" / "_recovered" / "state.yml")
        sf.upsert_observed(self._candidate("redis"))
        sf.save()
        proc = _run("--repo-root", str(self.root), "--docs-dir", "alt", "--phase", "mine")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        payload = self._payload(proc)
        self.assertEqual(payload["docs_dir"], "alt")
        self.assertEqual(payload["candidates"], 1)

    def test_escaping_docs_dir_is_refused(self):
        for bad in ("../escape", "/abs/path"):
            proc = _run("--repo-root", str(self.root), "--docs-dir", bad, "--phase", "mine")
            self.assertEqual(proc.returncode, 2, f"{bad}: {proc.stdout}")
            self.assertEqual(proc.stdout.strip(), "")


# ── mine phase ─────────────────────────────────────────────────────────────

@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under uv)")
class MinePhaseTests(_TreeFixture):

    def test_mine_leaves_the_tree_byte_identical(self):
        sf = self._state()
        sf.upsert_observed(self._candidate("httpx"))
        sf.save()
        self._write_observation(1, "deadbeefdeadbeef")
        before = _snapshot(self.tree)
        proc = _run("--repo-root", str(self.root), "--phase", "mine")
        self.assertIn(proc.returncode, (0, 1), proc.stderr)
        self.assertEqual(_snapshot(self.tree), before)

    def test_candidate_and_recorded_counts(self):
        sf = self._state()
        sf.upsert_observed(self._candidate("httpx"))
        sf.upsert_observed(self._candidate("redis"))
        sf.save()
        self._write_observation(1, "a" * 16)
        self._write_observation(2, "b" * 16, status="observed")
        proc = _run("--repo-root", str(self.root), "--phase", "mine")
        payload = self._payload(proc)
        self.assertTrue(payload["state_file_exists"])
        self.assertEqual(payload["candidates"], 2)
        self.assertEqual(payload["recorded_observations"], 2)

    def test_stale_anchor_count_from_signal_stale_anchors(self):
        self._write_observation(1, "a" * 16)
        self._write_observation(2, "b" * 16)
        recorded = R.read_recorded_observations(self.tree / "observations")
        sf = self._state()
        sf.signal_stale_anchors(recorded, present_anchor_ids={"a" * 16})
        sf.save()
        proc = _run("--repo-root", str(self.root), "--phase", "mine")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        payload = self._payload(proc)
        self.assertEqual(payload["stale_anchors"], 1)
        self.assertEqual(payload["stale_anchor_records"][0]["anchor_id"], "b" * 16)
        self.assertEqual(payload["stale_anchor_records"][0]["obs_id"], "OBS-0002")
        self.assertIn("transition-observation retire OBS-0002",
                      payload["stale_anchor_records"][0]["command"])

    def test_decided_record_with_a_lost_anchor_emits_no_impossible_command(self):
        # `decided` is terminal (schema §17.2), so `transition-observation
        # retire` refuses it. If a decided record signalled stale, `mine` would
        # sit at exit 1 forever behind a human step no human can discharge.
        # This pins the user-visible symptom — the exit code — where the
        # recover-side test pins the signal itself.
        self._write_observation(1, "a" * 16)
        self._write_observation(2, "b" * 16, status="decided")
        recorded = R.read_recorded_observations(self.tree / "observations")
        sf = self._state()
        sf.signal_stale_anchors(recorded, present_anchor_ids={"a" * 16})
        sf.save()
        proc = _run("--repo-root", str(self.root), "--phase", "mine")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = self._payload(proc)
        self.assertEqual(payload["stale_anchors"], 0)
        self.assertNotIn("transition-observation retire OBS-0002", json.dumps(payload))

    def test_observed_record_gets_reject_not_retire(self):
        # M-COR-2: §17.2 admits `retire` only from `ratified`. An `observed`
        # record whose anchor went stale is REJECTED. Emitting `retire` names a
        # transition the gate refuses, so `mine` sits at exit 1 behind a step
        # no human can complete — the same defect already closed for `decided`,
        # missed here because `_write_observation` defaults to `ratified`.
        self._write_observation(1, "a" * 16, status="observed")
        self._write_observation(2, "b" * 16, status="ratified")
        recorded = R.read_recorded_observations(self.tree / "observations")
        self.assertEqual(len(recorded), 2, "fixture must reach the code under test")
        sf = self._state()
        sf.signal_stale_anchors(recorded, present_anchor_ids=set())
        sf.save()
        proc = _run("--repo-root", str(self.root), "--phase", "mine")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        payload = self._payload(proc)
        by_id = {r["obs_id"]: r for r in payload["stale_anchor_records"]}
        self.assertEqual(by_id["OBS-0001"]["command"],
                         "transition-observation reject OBS-0001")
        self.assertEqual(by_id["OBS-0002"]["command"],
                         "transition-observation retire OBS-0002")
        self.assertNotIn("transition-observation retire OBS-0001", json.dumps(payload))

    def test_exact_human_command_per_candidate(self):
        cand = self._candidate("httpx")
        sf = self._state()
        sf.upsert_observed(cand)
        sf.save()
        proc = _run("--repo-root", str(self.root), "--phase", "mine")
        payload = self._payload(proc)
        self.assertEqual(payload["human_commands"], [{
            "candidate_id": cand["id"], "state": "observed",
            "command": f"transition-decision ratify {cand['id']} --as observation",
        }])
        self.assertIn("transition-decision", payload["next_step"])

    def test_rejected_rows_are_not_pending(self):
        cand = self._candidate("httpx")
        sf = self._state()
        sf.upsert_observed(cand)
        sf.dispose(cand["id"], "rejected")
        sf.save()
        proc = _run("--repo-root", str(self.root), "--phase", "mine")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = self._payload(proc)
        self.assertEqual(payload["candidates"], 1)
        self.assertEqual(payload["candidates_by_state"], {"rejected": 1})
        self.assertEqual(payload["human_commands"], [])

    def test_corrupt_observation_frontmatter_is_exit_2(self):
        (self.tree / "observations" / "OBS-0001-broken.md").write_text(
            "---\nid: OBS-0001\nanchor_id: [unterminated\n---\n", encoding="utf-8")
        proc = _run("--repo-root", str(self.root), "--phase", "mine")
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn("corrupt", proc.stderr)


# ── P3: no machine path sets any state past `observed` ─────────────────────

@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under uv)")
class P3WriterBoundaryTests(_TreeFixture):

    def _seed(self):
        sf = self._state()
        sf.upsert_observed(self._candidate("httpx"))
        sf.save()
        self._write_observation(1, "a" * 16)
        self._write_observation(2, "b" * 16, status="observed")

    def _boundary(self, phase: str, proc: subprocess.CompletedProcess, key: str):
        """Every phase reports the boundary it measured, and the test reads it
        rather than trusting it. `verify` is held to the same bar as the other
        three: while it was a stub this branch asserted exit 2 and read no
        payload, which proved nothing about what `verify` writes."""
        self.assertIn(proc.returncode, (0, 1), proc.stderr)
        self.assertTrue(self._payload(proc)["boundary"][key], phase)

    def test_survey_writes_no_observation_file_in_any_phase(self):
        self._seed()
        for phase in ("derive", "mine", "project", "verify"):
            before = _snapshot(self.tree / "observations")
            self.assertEqual(len(before), 2, "fixture seeds two records")
            proc = _run("--repo-root", str(self.root), "--phase", phase)
            self.assertEqual(_snapshot(self.tree / "observations"), before, phase)
            self._boundary(phase, proc, "observations_unchanged")

    def test_survey_writes_no_adr_record_in_any_phase(self):
        self._seed()
        for phase in ("derive", "mine", "project", "verify"):
            before = _snapshot(self.tree / "adrs", "**/ADR-*.md")
            self.assertEqual(before, {}, "fixture is ADR-less")
            proc = _run("--repo-root", str(self.root), "--phase", phase)
            self.assertEqual(_snapshot(self.tree / "adrs", "**/ADR-*.md"), before, phase)
            self._boundary(phase, proc, "adr_records_unchanged")

    def test_emit_path_yields_only_observed_rows(self):
        obs_dir = self.tree / "observations"
        rec_path = self._write_observation(1, R.candidate_id("external-dependency", "httpx"))
        rec_bytes = rec_path.read_bytes()
        recorded = R.read_recorded_observations(obs_dir)
        sf = self._state()

        # `recorded`: same claim -> no row, and the record's bytes stay put.
        self.assertEqual(R.emit_candidate(sf, recorded, self._candidate("httpx")), "recorded")
        self.assertEqual(sf.rows, {})
        # `successor`: changed claim -> one row, state observed.
        succ = self._candidate("httpx", rule="uses the thing differently")
        self.assertEqual(R.emit_candidate(sf, recorded, succ), "successor")
        # `observed`: unrecorded anchor -> one row, state observed.
        self.assertEqual(R.emit_candidate(sf, recorded, self._candidate("redis")), "observed")

        self.assertEqual(len(sf.rows), 2)
        self.assertEqual({row["state"] for row in sf.rows.values()}, {"observed"})
        sf.save()
        self.assertEqual({row["state"] for row in self._state().rows.values()}, {"observed"})
        self.assertEqual(rec_path.read_bytes(), rec_bytes)
        self.assertEqual(sorted(p.name for p in obs_dir.iterdir()), [rec_path.name])


@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under uv)")
class P3WriterBoundaryNegativeTests(_TreeFixture):
    """M-TEST-2. Every other boundary assertion is POSITIVE and reads the
    script's own self-report, so replacing `boundary_report` with a constant-
    True stub left all 26 survey tests green — the guard carrying the central
    "no scan writes an observation" claim was unverified. These make a phase
    write where no phase may write, and assert the refusal."""

    def _run_phase(self, phase: str, write=None) -> tuple[int, str]:
        """Run `main` in-process with `phase` optionally wrapped in a writer."""
        mod = _load_survey_module()
        if write is not None:
            inner = mod.PHASE_RUNNERS[phase]

            def runner(root, docs_dir, configured, _inner=inner, _w=write):
                _w(root / docs_dir)
                return _inner(root, docs_dir, configured)

            mod.PHASE_RUNNERS = dict(mod.PHASE_RUNNERS, **{phase: runner})
        err, out = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            rc = mod.main(["--repo-root", str(self.root), "--phase", phase])
        return rc, err.getvalue()

    def test_control_the_unwrapped_phase_passes(self):
        # Without this the two negatives below could pass for the wrong reason.
        rc, err = self._run_phase("mine")
        self.assertIn(rc, (0, 1), err)

    def test_a_phase_that_writes_an_observation_is_refused(self):
        def write(tree):
            (tree / "observations" / "OBS-0001-smuggled.md").write_text(
                "---\nid: OBS-0001\n---\n", encoding="utf-8")

        rc, err = self._run_phase("mine", write)
        self.assertEqual(rc, 2)
        self.assertIn("writer boundary violated during --phase mine", err)
        self.assertIn('"observations_unchanged": false', err)
        self.assertIn("no phase may write an observation file or an ADR", err)

    def test_a_phase_that_writes_an_adr_is_refused(self):
        def write(tree):
            (tree / "adrs" / "ADR-0001-smuggled.md").write_text(
                "---\nid: ADR-0001\n---\n", encoding="utf-8")

        rc, err = self._run_phase("mine", write)
        self.assertEqual(rc, 2)
        self.assertIn("writer boundary violated during --phase mine", err)
        self.assertIn('"adr_records_unchanged": false', err)

    def test_a_phase_that_MUTATES_an_existing_record_is_refused(self):
        # Bytes, not just names: an in-place edit of a record is the writer-
        # boundary violation that a filename-only comparison would miss.
        self._write_observation(1, "a" * 16)

        def write(tree):
            p = tree / "observations" / "OBS-0001-uses-the-thing.md"
            p.write_text(p.read_text(encoding="utf-8").replace(
                "status: ratified", "status: retired"), encoding="utf-8")

        rc, err = self._run_phase("mine", write)
        self.assertEqual(rc, 2)
        self.assertIn('"observations_unchanged": false', err)


# ── M-AUTHZ-2: the override takes the loader's verdict ─────────────────────

@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under uv)")
class DocsDirOverrideTests(_TreeFixture):
    """One `docs_dir` value must take ONE verdict whichever door it arrives
    through. `--docs-dir` re-implemented part of the loader's textual layer and
    dropped the execution-adjacent first-segment denylist, so `.github` was
    refused in `.bionic.yml` and accepted on the flag — on a new entry point
    that writes."""

    def _tree_under(self, rel: str) -> Path:
        tree = self.root / rel
        (tree / "adrs").mkdir(parents=True)
        (tree / "observations").mkdir(parents=True)
        (tree / "manifest.yml").write_text(
            'schema_version: "5"\nconcerns_enabled:\n  - adrs\n', encoding="utf-8")
        return tree

    def test_execution_adjacent_override_is_refused(self):
        self._tree_under(".github")     # a real, well-formed tree: only the denylist refuses
        proc = _run("--repo-root", str(self.root), "--docs-dir", ".github",
                    "--phase", "mine")
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertEqual(proc.stdout.strip(), "")
        self.assertIn(".github", proc.stderr)

    def test_the_flag_and_the_config_take_the_same_verdict(self):
        self._tree_under(".github")
        (self.root / ".bionic.yml").write_text(
            'config_version: "1"\ndocs_dir: .github\n', encoding="utf-8")
        via_config = _run("--repo-root", str(self.root), "--phase", "mine")
        (self.root / ".bionic.yml").write_text(
            'config_version: "1"\ndocs_dir: bionic\n', encoding="utf-8")
        via_flag = _run("--repo-root", str(self.root), "--docs-dir", ".github",
                        "--phase", "mine")
        self.assertEqual((via_config.returncode, via_flag.returncode), (2, 2),
                         f"config={via_config.stderr!r} flag={via_flag.stderr!r}")

    def test_a_shell_metacharacter_override_is_refused(self):
        # Isolates the TEXTUAL leg: the directory exists and is a well-formed
        # tree, and its resolved first segment is not denylisted, so the only
        # thing that can refuse `bio;nic` is the loader's per-segment grammar.
        self._tree_under("bio;nic")
        proc = _run("--repo-root", str(self.root), "--docs-dir", "bio;nic",
                    "--phase", "mine")
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn("invalid docs_dir", proc.stderr)
        self.assertIn("shell metacharacters", proc.stderr)

    def test_a_symlink_override_resolving_into_a_denylisted_dir_is_refused(self):
        # Isolates the RESOLVED leg: `mytree` passes every textual rule, and
        # only re-checking the resolved location catches where it lands.
        self._tree_under(".github")
        (self.root / "mytree").symlink_to(self.root / ".github")
        proc = _run("--repo-root", str(self.root), "--docs-dir", "mytree",
                    "--phase", "mine")
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn("resolves into .github/", proc.stderr)

    def test_a_valid_override_still_works(self):
        self._make_tree("other", write_config=False)
        proc = _run("--repo-root", str(self.root), "--docs-dir", "other",
                    "--phase", "mine")
        self.assertIn(proc.returncode, (0, 1), proc.stderr)
        self.assertEqual(self._payload(proc)["docs_dir"], "other")


# ── shipped-script contract ────────────────────────────────────────────────

# ── the phase set is closed ────────────────────────────────────────────────

@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under uv)")
class PhaseSetGuardTests(_TreeFixture):
    """`survey.py` gained no batch-ratification phase, and gains none later.

    Batch ratification is a human gate reached by the `survey-signoff` skill,
    never a phase of the scripted survey: a survey phase runs unattended inside
    a cycle, which is exactly the authorization a batch sign-off cannot have.
    Adding one here would launder the gate into automation.

    Every absence assertion below is paired with the positive control at the
    foot: `--phase mine` still parses and returns a payload. Without it, a
    `survey.py` that had stopped parsing `--phase` at all — or that crashed on
    import — would satisfy every refusal here and leave the guard vacuous.
    """

    EXPECTED_PHASES = ("derive", "mine", "project", "verify")

    def test_phases_constant_is_exactly_the_four_scripted_phases(self):
        mod = _load_survey_module()
        self.assertEqual(mod.PHASES, self.EXPECTED_PHASES)

    def test_the_cli_offers_exactly_the_phases_constant(self):
        # Behavioral rather than textual: argparse's invalid-choice error names
        # the live `choices=`, so a phase wired into the parser alone (leaving
        # PHASES untouched) is caught here rather than passing both pins.
        proc = _run("--repo-root", str(self.root), "--phase", "no-such-phase")
        self.assertEqual(proc.returncode, 2, proc.stdout)
        match = re.search(r"choose from ([^)]*)\)", proc.stderr)
        self.assertIsNotNone(
            match, f"argparse printed no choice list; stderr={proc.stderr}")
        offered = tuple(re.findall(r"[a-z][a-z-]*", match.group(1)))
        self.assertEqual(offered, self.EXPECTED_PHASES)

    def test_no_batch_ratification_phase_is_accepted(self):
        # The names a future hand might reach for. Each must be refused by the
        # parser, not handled by a branch.
        for phase in ("signoff", "sheet", "survey-signoff", "survey-sheet",
                      "ratify", "batch"):
            with self.subTest(phase=phase):
                proc = _run("--repo-root", str(self.root), "--phase", phase)
                self.assertEqual(proc.returncode, 2, proc.stdout)
                self.assertIn("invalid choice", proc.stderr)
                self.assertFalse(
                    proc.stdout.strip(),
                    "a refused phase must emit no payload — a payload here "
                    "would mean the phase ran before being rejected")

    def test_positive_control_phase_mine_returns_a_parsed_payload(self):
        # The control for all three assertions above. `mine` is read-only and
        # needs no candidates, so a clean fixture tree exercises the whole
        # parse-dispatch-emit path and proves the CLI parses phases at all.
        proc = _run("--repo-root", str(self.root), "--phase", "mine")
        self.assertIn(proc.returncode, (0, 1), proc.stderr)
        payload = self._payload(proc)
        self.assertIn("candidates", payload)


class ShippedScriptContractTests(unittest.TestCase):

    def test_pep723_block_declares_both_lanes(self):
        head = SURVEY.read_text(encoding="utf-8").split('"""', 1)[0]
        self.assertIn("# /// script", head)
        self.assertIn("httpx>=0.27", head)
        # PyYAML is pinned EXACTLY here, not at a floor: `run_child` spawns the
        # deriver as `[sys.executable, <script>]`, so the child inherits THIS
        # block and this is the version that computes the spine hash
        # `survey derive` writes. `ParserPinLockStepTests` holds it in lock-step
        # with the other declaration sites; asserting a floor here would have let
        # the two disagree.
        self.assertIn("pyyaml==6.0.3", head)
        # The five grammars the deriver pins, for the same inheritance reason:
        # without them `survey derive` on a Ruby, Node, Elixir or Swift target exits 2
        # with `ParserUnavailable`.
        for grammar in ("tree-sitter==", "tree-sitter-elixir==",
                        "tree-sitter-ruby==", "tree-sitter-swift==",
                        "tree-sitter-typescript=="):
            with self.subTest(grammar=grammar):
                self.assertIn(grammar, head)

    def test_skill_md_carries_a_survey_section_with_no_adr_reference(self):
        text = SKILL_MD.read_text(encoding="utf-8")
        self.assertIn("survey.py", text)
        self.assertIn("--phase", text)
        self.assertIsNone(re.search(r"ADR-\d{4}", text))
        self.assertNotIn("[[adrs/", text)


if __name__ == "__main__":
    unittest.main()
