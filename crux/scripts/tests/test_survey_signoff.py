"""Tests for `signoff-survey.py` — the batch sign-off write path.

ADR-0098 and docs/AGENTS.md §17.5. The suite has three layers:

  * **one transition test per numbered cell** of the §17.5 state table, cells
    1 through 17 (cell 17 is the projections' side and is pinned in
    `test_survey_receipts_projection.py`, named here only so the roster is
    complete);
  * **the crash test** — every pre-commit crash point is driven by advancing
    the phase machine one transition at a time, and after each one the tree is
    asserted to expose no record; a plain re-run then converges to S9
    byte-identically to an uninterrupted run;
  * **the refusal families**, each with the near-miss control that publishes.

Every absence assertion here carries its positive control, because "no record
is visible" is also true of a sign-off that did nothing at all.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
SIGNOFF = SCRIPTS / "signoff-survey.py"
SCAFFOLD = SCRIPTS / "scaffold-survey-sheet.py"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _load(name: str, filename: str | None = None):
    spec = importlib.util.spec_from_file_location(
        name, SCRIPTS / (filename or f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


SS = _load("survey_sheet")
SO = _load("signoff_survey", "signoff-survey.py")
sp_module = _load("summaries_projection")
from crux.arch.recover import (  # noqa: E402
    StateFile,
    candidate_id,
    read_recorded_observations,
)

DATE = "2026-08-30"

MANIFEST = """schema_version: 5
concerns_enabled: [adrs, observations, arch]

observation:
  next_number: 1
  stale_days: 90
"""


_HHMM_STAMP = re.compile(rb"^(## \[\d{4}-\d{2}-\d{2}) \d{2}:\d{2}\]", re.M)


def _snapshot(root: Path, normalize_time: bool = False) -> dict:
    """Byte hashes of every file under `root`.

    Nothing is excluded. An earlier `skip=("manifest.yml.tmp",)` on the two
    convergence comparisons blinded them to exactly the artifact
    `SyscallCrashConvergenceTests` exists to catch — a temporary file left
    behind by an interrupted write — and no run of this fixture ever produces
    one, so the exclusion hid a defect and covered nothing.

    `normalize_time` blanks the `HH:MM` half of a journal entry's heading
    stamp before hashing. The journal hook is stamped with the WALL CLOCK
    minute of the write (`write_journal_hook`), which is cosmetic — the join is
    by day — but two runs a minute apart hash differently, so a byte-identity
    comparison across two runs is time-dependent without this. That is a false
    RED, not a false green: it fires on a correct implementation whenever a run
    straddles a minute boundary, and `sync.sh` runs this suite as a
    refuse-first release gate. Only the convergence comparisons pass it; the
    refusal tests keep the strict hash, where a stamp-only rewrite is still a
    write that must be caught."""
    out = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file() or "__pycache__" in p.parts:
            continue
        rel = str(p.relative_to(root))
        data = p.read_bytes()
        if normalize_time and p.suffix == ".md":
            data = _HHMM_STAMP.sub(rb"\1 ##:##]", data)
        out[rel] = hashlib.sha256(data).hexdigest()
    return out


def _safe_text(path: Path) -> str:
    """A file's text, or "" when it is not UTF-8 — the whole-tree sweeps below
    walk every file the fixture makes, binaries included."""
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return ""


class _SignoffCase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.tree = self.root / "bionic"
        self.obs = self.tree / "observations"
        self.obs.mkdir(parents=True)
        (self.root / ".bionic.yml").write_text("docs_dir: bionic\n", encoding="utf-8")
        (self.tree / "manifest.yml").write_text(MANIFEST, encoding="utf-8")
        (self.tree / "log.md").write_text("# Log\n", encoding="utf-8")
        (self.root / "src").mkdir()
        (self.root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
        (self.root / "src" / "b.py").write_text("y = 2\n", encoding="utf-8")
        self.state_path = self.tree / "arch" / "_recovered" / "state.yml"
        self.state = StateFile(self.state_path)
        self.a1 = self._candidate("httpx", "The gateway depends on httpx",
                                  "runtime", "src/a.py:1-1")
        self.a2 = self._candidate("pyyaml", "The loader pins pyyaml exactly",
                                  "runtime", "src/b.py:1-1")
        self.state.save()

    # ── fixture helpers ───────────────────────────────────────────────────

    def _candidate(self, name, rule, domain, evidence, **extra):
        cid = candidate_id("external-dependency", name)
        row = {"id": cid, "anchor_kind": "external-dependency",
               "canonical_anchor": name, "rule": rule, "evidence": [evidence],
               "state": "observed", "domain": domain}
        row.update(extra)
        self.state.rows[cid] = row
        return cid

    def _scaffold(self):
        proc = subprocess.run(
            [sys.executable, str(SCAFFOLD), "--repo-root", str(self.root),
             "--date", DATE], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)["batch_id"]

    def _sign(self, batch_id, verdicts, domains=None):
        """Author the human cells on the live sheet, as a human would."""
        path = self.obs / f"survey-{batch_id}.yml"
        sheet = SS.read_sheet(path)
        for row in sheet["rows"]:
            row["verdict"] = verdicts.get(row["anchor_id"], "ratify")
            row["domain"] = (domains or {}).get(row["anchor_id"], "")
            row["rationale"] = "Reviewed against the cited lines."
        SS.write_sheet(path, sheet, contained_under=self.tree)
        return sheet

    def _run(self, *extra):
        return self._run_dated(DATE, *extra)

    def _run_dated(self, date, *extra):
        """The sign-off invoked on a day of the caller's choosing. Every
        surface the sign-off writes may be resumed or re-checked on a later day
        than the one that produced it, so the day is a test variable."""
        return subprocess.run(
            [sys.executable, str(SIGNOFF), "--repo-root", str(self.root),
             "--date", date, *extra], capture_output=True, text=True)

    def _payload(self, proc):
        return json.loads(proc.stdout)

    def _prepare(self, verdicts=None, domains=None):
        bid = self._scaffold()
        self._sign(bid, verdicts or {}, domains)
        return bid

    def _ctx(self, batch_id):
        return SO.make_context(self.root, None, batch_id, DATE)

    def _state(self, batch_id):
        paths = SS.receipt_paths(self.obs, batch_id)
        return SS.batch_state(paths["receipt"], self.obs)

    def _records(self):
        return sorted(p.name for p in self.obs.glob("OBS-*.md"))

    def _allocated(self, batch_id):
        """`{anchor_id: (record_id, record_path)}` off the receipt. Record ids
        are allocated in ANCHOR order — `build_plan` sorts by `anchor_id`, so
        the id a candidate gets depends on its hash, never on the order the
        fixture declared it. Tests therefore ask rather than assume."""
        receipt = SS.read_receipt(SS.receipt_paths(self.obs, batch_id)["receipt"])
        return {r["anchor_id"]: (r["record_id"], r["record_path"])
                for r in receipt["rows"] if r["record_id"]}

    def _name_of(self, batch_id, anchor):
        return Path(self._allocated(batch_id)[anchor][1]).name

    def _frontmatter(self, name):
        import yaml
        text = (self.obs / name).read_text(encoding="utf-8")
        return yaml.safe_load(re.match(r"^---\n(.*?)\n---\n", text, re.S).group(1))

    def _rows(self):
        """The candidate state file as it stands on disk right now."""
        return StateFile(self.state_path).rows

    def _open_successor(self, anchor, predecessor_id, rule):
        """Mint the successor candidate a re-mine opens when the claim on an
        already-recorded anchor changes. Keyed `<anchor>+1`, so it sits on its
        PREDECESSOR's anchor — the shape both the disposition join and the
        scaffold's anchor keying have to get right."""
        sid = f"{anchor}+1"
        self.state = StateFile(self.state_path)
        self.state.rows[sid] = {
            "id": sid, "anchor_id": anchor, "state": "observed",
            "predecessor_id": predecessor_id,
            "anchor_kind": "external-dependency", "canonical_anchor": "httpx",
            "rule": rule, "evidence": ["src/a.py:1-1"], "domain": "runtime"}
        return sid


# ── cells 1-8: bind, allocate, stage, commit ───────────────────────────────

class CellsOneToEightTests(_SignoffCase):

    def test_cell_01_s0_to_s1_writes_the_sheet_then_the_bound_receipt(self):
        bid = self._prepare()
        ctx = self._ctx(bid)
        self.assertEqual(self._state(bid), "S0")
        SO.advance(ctx)
        self.assertEqual(self._state(bid), "S1")
        paths = SS.receipt_paths(self.obs, bid)
        self.assertTrue(paths["sheet"].is_file())
        receipt = SS.read_receipt(paths["receipt"])
        self.assertIsNone(receipt["completed"])
        self.assertEqual(receipt["records"], [])
        self.assertEqual(receipt["digest"],
                         SS.sheet_digest(SS.read_sheet(paths["sheet"])))
        self.assertTrue(all(r["record_id"] == "" for r in receipt["rows"]))

    def test_cell_02_s0_stays_s0_when_the_sheet_is_invalid(self):
        bid = self._scaffold()          # every verdict still empty
        proc = self._run("--batch", bid)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("carries no verdict", json.dumps(self._payload(proc)))
        self.assertEqual(self._state(bid), "S0")
        self.assertFalse(SS.receipt_paths(self.obs, bid)["receipt"].exists())
        # Control: the same sheet with the verdicts authored reaches S9.
        self._sign(bid, {})
        self.assertEqual(self._run("--batch", bid).returncode, 0)
        self.assertEqual(self._state(bid), "S9")

    def test_cell_03_s1_to_s2_allocates_ids_and_moves_the_counter_first(self):
        bid = self._prepare()
        ctx = self._ctx(bid)
        SO.advance(ctx)                                  # -> S1
        SO.advance(ctx)                                  # -> S2
        self.assertEqual(self._state(bid), "S2")
        receipt = SS.read_receipt(SS.receipt_paths(self.obs, bid)["receipt"])
        ids = sorted(r["record_id"] for r in receipt["rows"])
        self.assertEqual(ids, ["OBS-0001", "OBS-0002"])
        self.assertIn("next_number: 3",
                      (self.tree / "manifest.yml").read_text(encoding="utf-8"))
        self.assertEqual(self._records(), [], "cell 3 made a record visible")

    def test_cell_04_s1_refuses_and_writes_nothing_when_the_digest_differs(self):
        bid = self._prepare()
        ctx = self._ctx(bid)
        SO.advance(ctx)                                  # -> S1
        paths = SS.receipt_paths(self.obs, bid)
        sheet = SS.read_sheet(paths["sheet"])
        sheet["rows"][0]["verdict"] = "reject"           # tamper post-signature
        SS.write_sheet(paths["sheet"], sheet, contained_under=self.tree)
        before = _snapshot(self.root)
        proc = self._run("--batch", bid)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("digest", json.dumps(self._payload(proc)))
        self.assertEqual(_snapshot(self.root), before,
                         "a digest refusal wrote something")
        self.assertEqual(self._state(bid), "S1")

    def test_cell_05_audit_reads_s1_as_a_receipt_recording_no_completion(self):
        bid = self._prepare()
        SO.advance(self._ctx(bid))
        receipt = SS.read_receipt(SS.receipt_paths(self.obs, bid)["receipt"])
        self.assertIsNone(receipt["completed"])
        self.assertEqual(self._records(), [])

    def test_cell_06_s2_to_s3_stages_every_planned_file_and_shows_none(self):
        bid = self._prepare()
        ctx = self._ctx(bid)
        for _ in range(3):
            SO.advance(ctx)
        self.assertEqual(self._state(bid), "S3")
        staged = SS.receipt_paths(self.obs, bid)["staged"]
        self.assertEqual(
            sorted(p.name for p in staged.glob("OBS-*.md")),
            sorted([self._name_of(bid, self.a1), self._name_of(bid, self.a2)]))
        self.assertTrue((staged / "index.md").is_file())
        # The absence assertion, with its control: nothing is visible yet, and
        # the identical bytes ARE visible after the publish completes.
        self.assertEqual(self._records(), [])
        self.assertEqual(self._run("--batch", bid).returncode, 0)
        self.assertEqual(len(self._records()), 2)

    def test_cell_07_s3_to_s4_is_the_commit_point(self):
        bid = self._prepare()
        ctx = self._ctx(bid)
        for _ in range(4):
            SO.advance(ctx)
        self.assertEqual(self._state(bid), "S4")
        receipt = SS.read_receipt(SS.receipt_paths(self.obs, bid)["receipt"])
        self.assertEqual(receipt["completed"], DATE)
        self.assertEqual(receipt["records"], ["OBS-0001", "OBS-0002"])
        self.assertEqual(self._records(), [],
                         "completion must precede visibility (§17.5)")

    def test_cell_08_audit_at_s3_sees_no_visible_record(self):
        bid = self._prepare()
        ctx = self._ctx(bid)
        for _ in range(3):
            SO.advance(ctx)
        self.assertEqual(self._state(bid), "S3")
        self.assertEqual(self._records(), [])
        # Control: the same tree at S9 exposes both records.
        self.assertEqual(self._run("--batch", bid).returncode, 0)
        self.assertEqual(len(self._records()), 2)


# ── cells 9-17: promote, index, record, dispose, no-op, contradiction ──────

class CellsNineToSeventeenTests(_SignoffCase):

    def _advance_to(self, bid, state):
        ctx = self._ctx(bid)
        for _ in range(20):
            if self._state(bid) == state:
                return ctx
            SO.advance(ctx)
        self.fail(f"never reached {state}")

    def test_cell_09_s4_to_s5_promotes_every_record(self):
        bid = self._prepare()
        self._advance_to(bid, "S4")
        SO.advance(self._ctx(bid))
        self.assertEqual(self._state(bid), "S5")
        self.assertEqual(len(self._records()), 2)
        self.assertTrue(SS.receipt_paths(self.obs, bid)["staged"].is_dir())

    def test_cell_10_a_partial_promote_leaves_the_index_stale(self):
        """Cell 10 is the one window the protocol leaves open. The remedy is a
        re-run, and this pins that it converges."""
        bid = self._prepare()
        self._advance_to(bid, "S5")
        index = self.obs / "index.md"
        self.assertFalse(index.is_file(), "the index promotes last (cell 11)")
        self.assertEqual(self._run("--batch", bid).returncode, 0)
        text = index.read_text(encoding="utf-8")
        for oid in ("OBS-0001", "OBS-0002"):
            self.assertIn(oid, text)

    def test_cell_11_s5_to_s6_promotes_the_index_and_removes_staged(self):
        bid = self._prepare()
        self._advance_to(bid, "S5")
        SO.advance(self._ctx(bid))
        self.assertEqual(self._state(bid), "S6")
        self.assertFalse(SS.receipt_paths(self.obs, bid)["staged"].exists())
        self.assertTrue((self.obs / "index.md").is_file())

    def test_cell_12_s6_to_s7_writes_one_observation_log_op(self):
        bid = self._prepare()
        self._advance_to(bid, "S6")
        SO.advance(self._ctx(bid))
        self.assertEqual(self._state(bid), "S7")
        text = (self.tree / "log.md").read_text(encoding="utf-8")
        self.assertEqual(text.count(f"observation | survey batch {bid}"), 1)
        self.assertIn("OBS-0001", text)

    def test_cell_13_s7_to_s8_writes_one_journal_hook(self):
        bid = self._prepare()
        self._advance_to(bid, "S7")
        SO.advance(self._ctx(bid))
        self.assertEqual(self._state(bid), "S8")
        hook = (self.tree / "journal" / f"{DATE[:7]}.md").read_text(encoding="utf-8")
        self.assertEqual(hook.count(f"review | survey sign-off {bid}"), 1)

    def test_cell_14_s8_to_s9_disposes_the_state_file(self):
        bid = self._prepare(verdicts={self.a2: "reject"})
        self._advance_to(bid, "S8")
        SO.advance(self._ctx(bid))
        self.assertEqual(self._state(bid), "S9")
        rows = StateFile(self.state_path).rows
        self.assertEqual(rows[self.a1]["state"], "ratified")
        self.assertEqual(rows[self.a2]["state"], "rejected")

    def test_cell_14_defer_writes_nothing_to_the_state_file(self):
        """The absence assertion — a deferred candidate stays `observed` —
        with its control: the ratified sibling in the same batch moves."""
        bid = self._prepare(verdicts={self.a2: "defer"})
        self.assertEqual(self._run("--batch", bid).returncode, 0)
        rows = StateFile(self.state_path).rows
        self.assertEqual(rows[self.a2]["state"], "observed")
        self.assertEqual(rows[self.a1]["state"], "ratified")
        # And the deferred candidate is re-scaffoldable, which is the point.
        self.assertNotIn(self.a2, SS.disposed_candidates(self.obs))
        self.assertIn(self.a1, SS.disposed_candidates(self.obs))

    def test_cell_15_a_re_run_at_s9_is_a_reported_no_op(self):
        bid = self._prepare()
        self.assertEqual(self._run("--batch", bid).returncode, 0)
        before = _snapshot(self.root)
        proc = self._run("--batch", bid)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._payload(proc)["state"], "S9")
        self.assertEqual(self._payload(proc)["writes"], [])
        self.assertEqual(_snapshot(self.root), before,
                         "an idempotent re-run wrote something")
        # Control: the FIRST run wrote exactly one log op and one hook.
        log = (self.tree / "log.md").read_text(encoding="utf-8")
        self.assertEqual(log.count(f"survey batch {bid}"), 1)

    def test_cell_16_a_contradicting_surface_refuses_and_never_overwrites(self):
        bid = self._prepare()
        self._advance_to(bid, "S6")
        # A log op for this batch that the sign-off did not write.
        log = self.tree / "log.md"
        log.write_text(f"## [2020-01-01] observation | survey batch {bid}\n\n"
                       "Records: OBS-9999\n\n"
                       + log.read_text(encoding="utf-8"), encoding="utf-8")
        before = _snapshot(self.root)
        proc = self._run("--batch", bid)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("contradict", json.dumps(self._payload(proc)))
        self.assertEqual(_snapshot(self.root), before)
        # Control: with the hand-written op removed, the same tree reaches S9.
        log.write_text("# Log\n", encoding="utf-8")
        self.assertEqual(self._run("--batch", bid).returncode, 0)
        self.assertEqual(self._state(bid), "S9")

    def test_cell_17_is_pinned_by_the_projection_suite(self):
        """Cell 17 — summarize-adrs / compile-doctrine refusing while the batch
        is below S9 — is the projections' side of this state table and is
        pinned in `test_survey_receipts_projection.py`. Named here so the
        17-cell roster is complete in one place."""
        self.assertTrue(
            (SCRIPTS / "tests" / "test_survey_receipts_projection.py").is_file())


# ── the published record ──────────────────────────────────────────────────

class PublishedRecordTests(_SignoffCase):

    def test_the_record_carries_the_rule_and_evidence_verbatim(self):
        bid = self._prepare(domains={self.a1: "storage"})
        self.assertEqual(self._run("--batch", bid).returncode, 0)
        import yaml
        rid, rel = self._allocated(bid)[self.a1]
        text = (self.obs / Path(rel).name).read_text(encoding="utf-8")
        fm = yaml.safe_load(re.match(r"^---\n(.*?)\n---\n", text, re.S).group(1))
        self.assertEqual(fm["status"], "ratified")
        self.assertEqual(fm["anchor_id"], self.a1)
        self.assertEqual(fm["evidence"], ["src/a.py:1-1"])
        self.assertEqual(fm["provenance"], "recovered")
        self.assertEqual(fm["governs"][0]["rule"],
                         "The gateway depends on httpx")
        self.assertEqual(fm["governs"][0]["domain"], "storage")
        self.assertEqual(fm["governs"][0]["handle"],
                         f"{rid}/the-gateway-depends-on-httpx")
        self.assertEqual(fm["id"], rid)

    def test_the_receipt_records_domain_source_per_row(self):
        bid = self._prepare(domains={self.a1: "storage"})
        self.assertEqual(self._run("--batch", bid).returncode, 0)
        receipt = SS.read_receipt(SS.receipt_paths(self.obs, bid)["receipt"])
        by_anchor = {r["anchor_id"]: r for r in receipt["rows"]}
        self.assertEqual(by_anchor[self.a1]["domain_source"], "overridden")
        self.assertEqual(by_anchor[self.a1]["domain"], "storage")
        self.assertEqual(by_anchor[self.a2]["domain_source"], "proposed")
        self.assertEqual(by_anchor[self.a2]["domain"], "runtime")

    def test_a_reject_row_writes_no_record(self):
        bid = self._prepare(verdicts={self.a2: "reject"})
        self.assertEqual(self._run("--batch", bid).returncode, 0)
        self.assertEqual(self._records(), [self._name_of(bid, self.a1)])
        # Control: the ratified sibling DID write one.
        self.assertTrue((self.obs / self._records()[0]).is_file())

    def test_the_published_tree_is_clean_under_check_observations(self):
        bid = self._prepare()
        self.assertEqual(self._run("--batch", bid).returncode, 0)
        check = _load("check_observations")
        result = check.check(self.root, "bionic")
        self.assertEqual(result["broken"], [])
        self.assertEqual(result["records"], 2)

    def test_a_successor_row_retires_its_predecessor_in_the_same_batch(self):
        bid = self._prepare()
        self.assertEqual(self._run("--batch", bid).returncode, 0)
        pred_id, pred_rel = self._allocated(bid)[self.a1]
        # Re-mine: the httpx claim moved.
        sid = f"{self.a1}+1"
        self.state = StateFile(self.state_path)
        self.state.rows[sid] = {
            "id": sid, "anchor_id": self.a1, "state": "observed",
            "predecessor_id": pred_id, "anchor_kind": "external-dependency",
            "canonical_anchor": "httpx",
            "rule": "The gateway depends on httpx and on h2",
            "evidence": ["src/a.py:1-1"], "domain": "runtime"}
        self.state.save()
        bid2 = self._prepare()
        self.assertEqual(self._run("--batch", bid2).returncode, 0)
        import yaml
        old = (self.obs / Path(pred_rel).name).read_text(encoding="utf-8")
        fm = yaml.safe_load(re.match(r"^---\n(.*?)\n---\n", old, re.S).group(1))
        self.assertEqual(fm["status"], "retired")
        self.assertEqual(str(fm["retired_date"]), DATE)
        # Control: the successor is live on the same anchor, and the concern
        # audits clean — which it would not if both records were live.
        check = _load("check_observations")
        self.assertEqual(check.check(self.root, "bionic")["broken"], [])


# ── the crash test ────────────────────────────────────────────────────────

class CrashConvergenceTests(_SignoffCase):

    def _uninterrupted(self):
        bid = self._prepare()
        self.assertEqual(self._run("--batch", bid).returncode, 0)
        return bid, _snapshot(self.root, normalize_time=True)

    def test_no_record_is_visible_at_any_pre_commit_crash_point(self):
        bid = self._prepare()
        ctx = self._ctx(bid)
        seen = []
        while self._state(bid) != "S4":
            SO.advance(ctx)
            seen.append(self._state(bid))
            self.assertEqual(self._records(), [],
                             f"a record became visible at {seen[-1]}")
        self.assertEqual(seen, ["S1", "S2", "S3", "S4"])
        # Positive control: after the resume completes, both ARE visible.
        self.assertEqual(self._run("--batch", bid).returncode, 0)
        self.assertEqual(len(self._records()), 2)

    def test_a_resume_from_every_state_converges_byte_identically(self):
        reference = None
        for stop_after in range(1, 9):
            with self.subTest(stop_after=stop_after):
                self.setUp()
                bid = self._prepare()
                ctx = self._ctx(bid)
                for _ in range(stop_after):
                    if self._state(bid) == "S9":
                        break
                    SO.advance(ctx)              # the "crash" is here
                proc = self._run("--batch", bid)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(self._state(bid), "S9")
                snap = _snapshot(self.root, normalize_time=True)
                if reference is None:
                    reference = snap
                else:
                    self.assertEqual(snap, reference,
                                     f"resume after {stop_after} steps diverged")

    def test_the_uninterrupted_run_is_the_reference_byte_image(self):
        self.setUp()
        _bid, uninterrupted = self._uninterrupted()
        self.setUp()
        bid = self._prepare()
        ctx = self._ctx(bid)
        SO.advance(ctx)
        SO.advance(ctx)
        self.assertEqual(self._run("--batch", bid).returncode, 0)
        self.assertEqual(
            _snapshot(self.root, normalize_time=True),
            uninterrupted)


# ── the crash test, one level down: the syscall, not the cell ──────────────

class SyscallCrashConvergenceTests(_SignoffCase):
    """Crash injection between `os.open(tmp, O_EXCL)` and `os.replace`.

    `CrashConvergenceTests` above crashes by calling `advance()` N times — but
    `advance` IS the transactional unit, so every write inside it either
    completed or was rolled back and no interior-of-a-cell state is
    constructible from it. The window a real kill lands in is inside one
    `atomic_write_text`: the temporary file exists and has not been renamed
    onto the target.

    The injection raises `KeyboardInterrupt`, which is a `BaseException` and
    therefore passes straight through `atomic_write_text`'s `except Exception`
    rollback — exactly as a killed process does. What it leaves on disk is the
    real artifact: a predictable `<path>.tmp` beside an untouched target.

    Every write site a clean run passes through is driven, and after each one
    the plain documented remedy — re-run the same command — must converge to
    S9, leave no `.tmp` in the tree, and land the byte-identical image."""

    def _spy(self, stop_at=None):
        real = os.replace
        seen: list[str] = []

        def patched(src, dst, *args, **kwargs):
            seen.append(str(dst))
            if stop_at is not None and len(seen) == stop_at:
                raise KeyboardInterrupt(f"killed before os.replace -> {dst}")
            return real(src, dst, *args, **kwargs)

        return real, seen, patched

    def _leftover_tmps(self):
        return sorted(str(p.relative_to(self.root))
                      for p in self.root.rglob("*")
                      if p.is_file() and p.name.endswith(".tmp"))

    def _published_claims(self):
        """Every published record as an ID-FREE tuple, plus the receipt's own
        record list checked against the files on disk.

        Byte identity is the wrong invariant for a crash INSIDE cell 3. That
        cell makes the counter durable before any id reaches the receipt, so a
        kill between the two re-allocates from the advanced counter and the
        resumed batch publishes OBS-0003/OBS-0004 rather than OBS-0001/0002 —
        the documented behaviour, since over-allocation is safe and reuse is
        not. What must converge is the CONTENT: the same claims, the same
        slugs, the same statuses, one record per receipt row."""
        out = []
        for name in self._records():
            fm = self._frontmatter(name)
            slug = name.split("-", 2)[2]
            governs = fm.get("governs") or [{}]
            out.append((slug, fm["status"], governs[0].get("domain"),
                        governs[0].get("rule")))
        return sorted(out)

    def _run_in_process(self, bid, stop_at=None):
        real, seen, patched = self._spy(stop_at)
        os.replace = patched
        try:
            if stop_at is None:
                SO.run(self._ctx(bid), dry_run=False)
            else:
                with self.assertRaises(KeyboardInterrupt):
                    SO.run(self._ctx(bid), dry_run=False)
        finally:
            os.replace = real
        return seen

    def test_a_completed_run_leaves_no_tmp_file_anywhere_in_the_tree(self):
        """The assertion nobody had written. Every convergence comparison in
        this file either skipped `manifest.yml.tmp` by name or hashed whatever
        was there, so a run that left a temporary file behind was green."""
        bid = self._prepare()
        self.assertEqual(self._run("--batch", bid).returncode, 0)
        self.assertEqual(self._state(bid), "S9")
        self.assertEqual(self._leftover_tmps(), [])
        # Positive control: the walk CAN see one, so the empty list above is a
        # fact about the run and not about the walk.
        (self.tree / "manifest.yml.tmp").write_text("x", encoding="utf-8")
        self.assertEqual(self._leftover_tmps(), ["bionic/manifest.yml.tmp"])

    def test_a_crash_between_open_and_replace_converges_at_every_write_site(self):
        bid = self._prepare()
        sites = self._run_in_process(bid)
        self.assertEqual(self._state(bid), "S9")
        self.assertGreaterEqual(
            len(sites), 8,
            "the fixture reaches too few atomic writes to be a sweep")
        reference = self._published_claims()
        self.assertEqual(len(reference), 2)
        self.assertEqual(self._leftover_tmps(), [])

        for nth, target in enumerate(sites, start=1):
            with self.subTest(nth=nth, target=Path(target).name):
                self.setUp()
                bid = self._prepare()
                self._run_in_process(bid, stop_at=nth)
                # The documented remedy, and the only one: re-run the command.
                proc = self._run("--batch", bid)
                self.assertEqual(
                    proc.returncode, 0,
                    f"a crash before write {nth} ({Path(target).name}) wedged "
                    f"the batch: {proc.stdout}{proc.stderr}")
                self.assertEqual(self._state(bid), "S9")
                self.assertEqual(
                    self._leftover_tmps(), [],
                    f"a crash before write {nth} ({Path(target).name}) left a "
                    "temporary file in the published tree")
                self.assertEqual(
                    self._published_claims(), reference,
                    f"a crash before write {nth} ({Path(target).name}) "
                    "converged to different published content")
                # The bijection the byte comparison used to stand in for: one
                # visible record per receipt row that names one, and a tree
                # the concern's own checker reports nothing on.
                receipt = SS.read_receipt(
                    SS.receipt_paths(self.obs, bid)["receipt"])
                self.assertEqual(
                    sorted(receipt["records"]),
                    sorted(n.split("-", 2)[0] + "-" + n.split("-", 2)[1]
                           for n in self._records()))
                self.assertEqual(
                    _load("check_observations").check(
                        self.root, "bionic")["broken"], [],
                    f"a crash before write {nth} ({Path(target).name}) left "
                    "the tree with a finding")


# ── refusal families, each with its control ───────────────────────────────

class RefusalFamilyTests(_SignoffCase):

    def _refuses(self, bid, needle):
        before = _snapshot(self.root)
        proc = self._run("--batch", bid)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn(needle, json.dumps(self._payload(proc)))
        self.assertEqual(_snapshot(self.root), before,
                         "a refusal wrote something")

    def _edit_live(self, bid, fn):
        path = self.obs / f"survey-{bid}.yml"
        sheet = SS.read_sheet(path)
        fn(sheet)
        SS.write_sheet(path, sheet, contained_under=self.tree)

    def test_empty_verdict_refuses_and_the_control_publishes(self):
        bid = self._prepare()
        self._edit_live(bid, lambda s: s["rows"][0].update(verdict=""))
        self._refuses(bid, "carries no verdict")
        self._sign(bid, {})
        self.assertEqual(self._run("--batch", bid).returncode, 0)
        self.assertEqual(len(self._records()), 2)

    def test_domain_empty_after_the_seed_refuses(self):
        self.state.rows[self.a1].pop("domain")
        self.state.save()
        bid = self._prepare()
        self._refuses(bid, "after the seed is applied")
        self._edit_live(bid, lambda s: [r.update(domain="runtime")
                                        for r in s["rows"]])
        self.assertEqual(self._run("--batch", bid).returncode, 0)

    def test_whitespace_only_domain_refuses(self):
        bid = self._prepare()
        self._edit_live(bid, lambda s: s["rows"][0].update(domain="   "))
        self._refuses(bid, "whitespace only")
        self._edit_live(bid, lambda s: s["rows"][0].update(domain="storage"))
        self.assertEqual(self._run("--batch", bid).returncode, 0)

    def test_multi_line_domain_refuses(self):
        bid = self._prepare()
        self._edit_live(bid, lambda s: s["rows"][0].update(domain="a\nb"))
        self._refuses(bid, "more than one")
        self._edit_live(bid, lambda s: s["rows"][0].update(domain="a"))
        self.assertEqual(self._run("--batch", bid).returncode, 0)

    def test_a_control_character_in_the_domain_refuses_before_any_write(self):
        """A tab survives `.strip()` and is not a newline, so it passed the
        sheet's domain rules and reached `write_receipt`, which refuses it.
        The refusal has to land in `build_plan` instead — every pre-write
        refusal runs there, before the first write of any run."""
        bid = self._prepare()
        self._edit_live(bid, lambda s: s["rows"][0].update(domain="stor\tage"))
        self._refuses(bid, "control, format or separator")
        self.assertFalse(SS.receipt_paths(self.obs, bid)["dir"].exists(),
                         "the refusal wrote into the holding area")
        # Control: the same cell without the tab publishes.
        self._edit_live(bid, lambda s: s["rows"][0].update(domain="storage"))
        self.assertEqual(self._run("--batch", bid).returncode, 0)
        self.assertEqual(len(self._records()), 2)

    def test_anchor_kind_outside_the_closed_set_refuses(self):
        bid = self._prepare()
        self.state.rows[self.a1]["anchor_kind"] = "vibes"
        self.state.save()
        self._refuses(bid, "outside")
        self.state.rows[self.a1]["anchor_kind"] = "external-dependency"
        self.state.save()
        self.assertEqual(self._run("--batch", bid).returncode, 0)

    def test_a_duplicate_anchor_in_one_batch_refuses(self):
        bid = self._prepare()
        path = self.obs / f"survey-{bid}.yml"
        good = path.read_bytes()
        self._edit_live(bid, lambda s: s["rows"][1].update(
            anchor_id=s["rows"][0]["anchor_id"]))
        self._refuses(bid, "at most once")
        # Control: the byte-identical sheet before the tamper publishes.
        path.write_bytes(good)
        self.assertEqual(self._run("--batch", bid).returncode, 0)
        self.assertEqual(len(self._records()), 2)

    def test_a_successor_naming_no_predecessor_refuses(self):
        sid = f"{self.a1}+1"
        self.state.rows.pop(self.a1)
        self.state.rows.pop(self.a2)
        self.state.rows[sid] = {
            "id": sid, "anchor_id": self.a1, "state": "observed",
            "predecessor_id": "OBS-0001",
            "anchor_kind": "external-dependency", "canonical_anchor": "httpx",
            "rule": "The gateway also pins h2", "evidence": ["src/a.py:1-1"],
            "domain": "runtime"}
        self.state.save()
        (self.obs / "OBS-0001-x.md").write_text(
            f'---\nid: OBS-0001\nstatus: ratified\nanchor_id: "{self.a1}"\n'
            'evidence: ["src/a.py:1-1"]\nprovenance: recovered\n'
            'governs: []\n---\n\nbody\n',
            encoding="utf-8")
        bid = self._prepare()
        # Strip the predecessor AFTER the scaffold: an orphan successor never
        # reaches a sheet, because the scaffold filters it out, so the sign-off
        # meets one only when the state file changed under a signed batch.
        self.state = StateFile(self.state_path)
        self.state.rows[sid].pop("predecessor_id")
        self.state.save()
        self._refuses(bid, "naming no predecessor")
        # Control: the predecessor restored, the same sheet publishes.
        self.state.rows[sid]["predecessor_id"] = "OBS-0001"
        self.state.save()
        self.assertEqual(self._run("--batch", bid).returncode, 0)

    def test_an_escaping_evidence_path_refuses_the_whole_batch(self):
        (self.root / "link").symlink_to("/etc")
        self.state.rows[self.a1]["evidence"] = ["link/hosts:1-1"]
        self.state.save()
        bid = self._prepare()
        self._refuses(bid, "does not resolve")
        self.assertEqual(self._records(), [], "a refused batch published")
        # Control: removing the bad row publishes the rest.
        self._edit_live(bid, lambda s: s["rows"].remove(
            next(r for r in s["rows"] if r["anchor_id"] == self.a1)))
        self.assertEqual(self._run("--batch", bid).returncode, 0)
        self.assertEqual(len(self._records()), 1)

    def test_a_changed_sheet_refuses_and_the_unchanged_one_publishes(self):
        """Absence assertion 4: the digest refusal writes nothing, and the
        byte-identical unchanged sheet publishes N records, moves the counter
        and writes one log op."""
        bid = self._prepare()
        ctx = self._ctx(bid)
        SO.advance(ctx)                                   # bind at S1
        paths = SS.receipt_paths(self.obs, bid)
        sheet = SS.read_sheet(paths["sheet"])
        original = paths["sheet"].read_bytes()
        sheet["rows"][0]["verdict"] = "reject"
        SS.write_sheet(paths["sheet"], sheet, contained_under=self.tree)
        self._refuses(bid, "digest")
        paths["sheet"].write_bytes(original)
        self.assertEqual(self._run("--batch", bid).returncode, 0)
        self.assertEqual(len(self._records()), 2)
        self.assertIn("next_number: 3",
                      (self.tree / "manifest.yml").read_text(encoding="utf-8"))
        self.assertEqual(
            (self.tree / "log.md").read_text(encoding="utf-8").count(
                f"survey batch {bid}"), 1)

    def test_dry_run_writes_nothing_and_renders_every_planned_record(self):
        bid = self._prepare()
        before = _snapshot(self.root)
        proc = self._run("--batch", bid, "--dry-run")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(_snapshot(self.root), before)
        rendering = "\n".join(self._payload(proc)["rendering"])
        self.assertIn("OBS-0001", rendering)
        self.assertIn("OBS-0002", rendering)
        self.assertIn("observations/OBS-0001-", rendering)
        self.assertIn("the-gateway-depends-on-httpx.md", rendering)
        # Control: without the flag the snapshot changes.
        self.assertEqual(self._run("--batch", bid).returncode, 0)
        self.assertNotEqual(_snapshot(self.root), before)

    def test_no_surveys_file_is_visible_to_the_record_walk(self):
        """Absence assertion 6, with its control: the `_surveys/` holding area
        is invisible to both walks, and the same records under
        `observations/` ARE seen by both."""
        bid = self._prepare()
        self.assertEqual(self._run("--batch", bid).returncode, 0)
        import summaries_projection as sp
        walked = {p.name for p in sp.observation_paths(self.obs)}
        self.assertEqual(walked, set(self._records()))
        for p in (self.obs / "_surveys").rglob("*"):
            self.assertNotIn(p.name, walked)
        check = _load("check_observations")
        self.assertEqual(check.check(self.root, "bionic")["records"], 2)


# ── the wedge class: three defects, one exit-1 message and one blast radius ─
#
# Every test below reproduces a batch that reached a state it could never leave
# — no scripted remedy, `summarize-adrs` and `compile-doctrine` failing closed
# at exit 2 for the WHOLE tree while the batch sits below S9. The shared root is
# a comparison against today (`ctx["date"]`) where the receipt's own recorded
# date is the only stable answer, plus a `batch_state` predicate that a
# legitimate batch cannot satisfy.

class ConvergenceRegressionTests(_SignoffCase):

    def test_a_batch_with_no_ratify_row_converges_to_s9(self):
        """ADR-0098 clause 1 admits an all-`reject`/`defer` batch. Neither
        verdict writes a record, so NO id is allocated — and `ids_allocated`
        required at least one, pinning such a batch at S1 forever."""
        bid = self._prepare({self.a1: "reject", self.a2: "defer"})
        proc = self._run("--batch", bid)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertEqual(self._payload(proc)["state"], "S9")
        self.assertEqual(self._state(bid), "S9")
        self.assertEqual(self._records(), [], "a non-ratify batch published")
        # The batch DID its cell-14 write: `reject` disposes, `defer` does not.
        # Without this pair the S9 above is also true of a batch that ran no
        # cell at all.
        rows = self._rows()
        self.assertEqual(rows[self.a1]["state"], "rejected")
        self.assertEqual(rows[self.a2]["state"], "observed")
        # ...and the tree it leaves behind audits clean, which is the blast
        # radius: a batch below S9 refuses both projections for every ADR.
        check = _load("check_observations")
        self.assertEqual(check.check(self.root, "bionic")["broken"], [])
        # Positive control: the same fixture WITH a ratify row publishes two.
        self.setUp()
        bid2 = self._prepare()
        self.assertEqual(self._run("--batch", bid2).returncode, 0)
        self.assertEqual(len(self._records()), 2)

    def test_a_deferred_row_a_later_batch_disposed_leaves_the_re_run_a_no_op(self):
        """`defer` writes nothing, so a later batch disposing the same
        candidate takes nothing away from the batch that deferred it — but the
        already-disposed refusal ran on EVERY row, including a `defer` one.
        A re-run of the deferring batch then exited 1 with a finding, and cell
        15 says a re-run of a published batch is a reported no-op.

        Reachability is the point, so this runs through the two shipped
        commands only. Batch A is driven to S9 first: a batch below S9 HOLDS
        every anchor its receipt names, so batch B cannot exist until A
        converges (`survey_sheet.unfinished_batch_anchors`). That ordering is
        what makes the fixture buildable, and it is the ordering an operator
        actually produces — defer, publish, re-mine, ratify."""
        bid_a = self._prepare({self.a1: "defer"})        # a2 defaults to ratify
        self.assertEqual(self._run("--batch", bid_a).returncode, 0)
        self.assertEqual(self._state(bid_a), "S9")
        self.assertEqual(len(self._records()), 1)

        # Batch B takes the deferred anchor — the designed behaviour of
        # `defer` — and ratifies it, disposing the candidate A deferred.
        bid_b = self._prepare()
        self.assertNotEqual(bid_a, bid_b)
        sheet_b = SS.read_sheet(self.obs / f"survey-{bid_b}.yml")
        self.assertEqual([r["anchor_id"] for r in sheet_b["rows"]], [self.a1])
        self.assertEqual(self._run("--batch", bid_b).returncode, 0)
        self.assertEqual(SS.disposed_candidates(self.obs).get(self.a1), bid_b)
        self.assertEqual(len(self._records()), 2)

        proc = self._run("--batch", bid_a)               # cell 15's no-op
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self._payload(proc)["state"], "S9")
        self.assertEqual(self._state(bid_a), "S9")
        # Discriminators. The no-op wrote nothing over B's work, both rows
        # carry their own disposition, and the tree audits clean.
        self.assertEqual(len(self._records()), 2)
        rows = self._rows()
        self.assertEqual([rows[self.a1]["state"], rows[self.a2]["state"]],
                         ["ratified", "ratified"])
        self.assertEqual(self._payload(proc)["records"],
                         [self._allocated(bid_a)[self.a2][0]])
        check = _load("check_observations")
        self.assertEqual(check.check(self.root, "bionic")["broken"], [])

    def test_a_writing_row_another_batch_disposed_still_refuses(self):
        """The near-miss control for the fix above: the refusal is narrowed to
        the rows that WRITE (`DISPOSING_VERDICTS`), not deleted.

        Driven at the API rather than through the CLI because the shipped
        commands cannot construct this state, for a reason that had to be
        BUILT: an unfinished batch holds every anchor its receipt names, so
        while A sits bound no second sheet can carry one of its rows, and once
        A reaches S9 a `ratify` or `reject` row has disposed its own candidate
        and the scaffold filters it out. The guard therefore stands against a
        hand-edited tree. The disposal map is still read off the REAL receipts
        on disk, and A is driven to S9 through the shipped command."""
        bid_a = self._prepare({self.a1: "defer"})
        self.assertEqual(self._run("--batch", bid_a).returncode, 0)
        bid_b = self._prepare()
        self.assertEqual(self._run("--batch", bid_b).returncode, 0)

        disposed = SS.disposed_candidates(self.obs)      # off B's receipt
        self.assertEqual(disposed.get(self.a1), bid_b)
        sheet = SS.read_sheet(SS.receipt_paths(self.obs, bid_a)["sheet"])
        rows = StateFile(self.state_path).rows
        recorded = read_recorded_observations(self.obs)
        # A's OWN published record, so its `ratify` row is not re-read as a
        # second live record on its anchor. Read off A's receipt, never named,
        # and keyed by anchor: the exemption is the allocating row's alone.
        own = {r["anchor_id"]: r["record_id"] for r
               in SS.read_receipt(SS.receipt_paths(self.obs, bid_a)["receipt"])
               ["rows"] if r["record_id"]}
        for verdict in ("ratify", "reject"):
            with self.subTest(verdict=verdict):
                tampered = {**sheet, "rows": [dict(r) for r in sheet["rows"]]}
                for row in tampered["rows"]:
                    if row["anchor_id"] == self.a1:
                        row["verdict"] = verdict
                with self.assertRaises(SS.SurveySheetError) as caught:
                    SS.build_plan(tampered, rows, recorded, root=self.root,
                                  obs_dir=self.obs,
                                  disposed_elsewhere=disposed,
                                  own_record_ids=own)
                self.assertIn("was already disposed by",
                              " ".join(caught.exception.problems))
        # Positive control: the untouched `defer` sheet, the same disposal map
        # and the same state file build a plan rather than raising.
        plan = SS.build_plan(sheet, rows, recorded, root=self.root,
                             obs_dir=self.obs, disposed_elsewhere=disposed,
                             own_record_ids=own)
        self.assertEqual([e["verdict"] for e in plan
                          if e["anchor_id"] == self.a1], ["defer"])

    def test_a_resume_across_a_month_boundary_converges(self):
        """The journal hook's month file is keyed on the receipt's `signed`
        date at BOTH ends. Writing by today and reading by `signed` put the
        hook in a file the reader never opens, and the batch never left S7."""
        bid = self._prepare()
        ctx = self._ctx(bid)
        for _ in range(7):                       # S0 -> S7: log written, no hook
            SO.advance(ctx)
        self.assertEqual(self._state(bid), "S7")
        proc = self._run_dated("2026-09-15", "--batch", bid)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertEqual(self._state(bid), "S9")
        signed_month = self.tree / "journal" / f"{DATE[:7]}.md"
        self.assertIn(f"survey sign-off {bid}",
                      signed_month.read_text(encoding="utf-8"))
        self.assertFalse((self.tree / "journal" / "2026-09.md").exists(),
                         "the hook landed in the resume's month, not the "
                         "batch's own")
        # A third run on a third day stays a no-op rather than appending a
        # second hook somewhere new — the control for the absence above.
        self.assertEqual(
            self._run_dated("2026-10-02", "--batch", bid).returncode, 0)
        self.assertFalse((self.tree / "journal" / "2026-10.md").exists())
        self.assertEqual(
            signed_month.read_text(encoding="utf-8").count(
                f"survey sign-off {bid}"), 1)

    def test_re_running_a_published_batch_on_a_later_day_is_a_no_op(self):
        """§17.5 cell 15. `check_surfaces` compared the recorded log op and
        journal hook against TODAY, so every later day read a healthy batch as
        a hand-edited surface and raised `Contradiction` — and because `run`
        calls `check_surfaces` first, `--dry-run` could not even inspect it."""
        bid = self._prepare()
        self.assertEqual(self._run("--batch", bid).returncode, 0)
        before = _snapshot(self.root)
        for later in ("2026-09-01", "2027-01-31"):
            with self.subTest(day=later):
                proc = self._run_dated(later, "--batch", bid)
                self.assertEqual(proc.returncode, 0, proc.stdout)
                self.assertEqual(self._payload(proc)["state"], "S9")
                self.assertEqual(self._payload(proc)["writes"], [])
                self.assertEqual(_snapshot(self.root), before,
                                 "a reported no-op wrote something")
        dry = self._run_dated("2026-09-01", "--batch", bid, "--dry-run")
        self.assertEqual(dry.returncode, 0, dry.stdout)
        self.assertTrue(self._payload(dry)["dry_run"])
        # Positive control: cell 16 still refuses. A log entry whose record set
        # no longer matches the receipt is caught on that same later day, so
        # the fix relaxed the DATE comparison and nothing else.
        log = self.tree / "log.md"
        log.write_text(
            log.read_text(encoding="utf-8").replace("OBS-0001", "OBS-9999"),
            encoding="utf-8")
        proc = self._run_dated("2026-09-01", "--batch", bid)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("contradicts the receipt",
                      json.dumps(self._payload(proc)))

    def test_rejecting_a_successor_leaves_its_predecessor_ratified(self):
        """A successor sits on its predecessor's ANCHOR, so joining the receipt
        to the state file by anchor rewrote the predecessor's disposition too:
        its row said `rejected` while its published record said `ratified`, and
        `upsert_observed` never re-emits a rejected row, so the anchor was
        suppressed from every later mine and `prune` was free to drop it.

        The batch carries a `ratify` row deliberately — an all-reject batch
        never reached the dispose cell at all before the sibling fix, and would
        pass this test for the wrong reason."""
        bid = self._prepare()
        self.assertEqual(self._run("--batch", bid).returncode, 0)
        pred_id, pred_rel = self._allocated(bid)[self.a1]
        sid = self._open_successor(self.a1, pred_id,
                                   "The gateway depends on httpx and on h2")
        a3 = self._candidate("tree-sitter", "The extractor pins tree-sitter",
                             "runtime", "src/b.py:1-1")
        self.state.save()
        bid2 = self._prepare({self.a1: "reject"})    # a3 defaults to ratify
        self.assertEqual(self._run("--batch", bid2).returncode, 0)
        rows = self._rows()
        self.assertEqual(rows[self.a1]["state"], "ratified",
                         "rejecting the successor rewrote its predecessor's "
                         "disposition")
        self.assertEqual(self._frontmatter(Path(pred_rel).name)["status"],
                         "ratified",
                         "the record and its candidate row disagree")
        # Positive control: cell 14 DID run — the successor is rejected and the
        # batch's ratify row is ratified. Without these the assertions above
        # are also true of a batch that disposed nothing.
        self.assertEqual(rows[sid]["state"], "rejected")
        self.assertEqual(rows[a3]["state"], "ratified")

    def test_a_bare_re_run_after_a_successful_publish_is_a_no_op(self):
        """`_infer_batch` considered only batches BELOW S9, so the invocation
        `survey-signoff` documents as a no-op at exit 0 — a bare re-run right
        after a publish — exited 2, the lane callers skip rather than fail on."""
        bid = self._prepare()
        self.assertEqual(self._run("--batch", bid).returncode, 0)
        before = _snapshot(self.root)
        proc = self._run()                                  # no --batch
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = self._payload(proc)
        self.assertEqual(payload["batch_id"], bid)          # it inferred THIS one
        self.assertEqual(payload["state"], "S9")
        self.assertEqual(payload["writes"], [])
        self.assertEqual(_snapshot(self.root), before)
        # Positive control: a SECOND published batch makes the bare invocation
        # genuinely ambiguous, and it still refuses by name rather than picking.
        # Reload first — the sign-off ran in a subprocess and wrote its
        # dispositions, so the in-memory copy from setUp is stale.
        self.state = StateFile(self.state_path)
        self._candidate("tree-sitter", "The extractor pins tree-sitter",
                        "runtime", "src/b.py:1-1")
        self.state.save()
        bid2 = self._prepare()
        self.assertEqual(self._run("--batch", bid2).returncode, 0)
        proc = self._run()
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn("--batch", proc.stderr)
        self.assertIn(bid, proc.stderr)
        self.assertIn(bid2, proc.stderr)


class SignatureBindingTests(_SignoffCase):
    """A resume publishes the CANDIDATE the receipt named.

    `survey_sheet.build_plan` re-resolved every row against the candidate state
    file on every run, resume included, and `_pick_candidate` prefers a
    successor. So a re-mine that opened a successor on a bound row's anchor
    swapped the claim under the human's signature: the receipt named the plain
    candidate and cell 3 allocated a record id and a slug for its rule, and the
    resume then staged the SUCCESSOR's rule under that id.
    """

    def _plant_retired_predecessor(self):
        """A non-live record and a plain observed candidate on ONE anchor.

        Planted, not mined. `emit_candidate` cannot reach this state on its
        own — it opens a successor only when a RECORDED claim changed, and it
        never re-opens a plain candidate on an anchor a record already holds.
        Observation records are hand-maintained (schema §17), so the state is
        real: retiring the record is exactly what makes the plain candidate
        eligible again, because `_eligible` skips an anchor only while a LIVE
        record holds it. Every later step here runs through the two shipped
        commands.
        """
        rid = "OBS-0100"
        (self.obs / f"{rid}-a-retired-claim.md").write_text(
            f"---\nid: {rid}\nanchor_id: {self.a1}\nstatus: retired\n"
            'evidence: ["src/a.py:1-1"]\nprovenance: recovered\n'
            "governs:\n  - domain: runtime\n"
            "    rule: An older claim\n    scope: src/a.py\n"
            f"    handle: {rid}/an-older-claim\n"
            "    provenance: recovered\n---\n\nbody\n",
            encoding="utf-8")
        return rid

    def _bind_then_remine(self, verdict):
        """Bind one batch, then let a re-mine open a successor on its anchor.

        After cell 1 the live sheet is gone, so the batch cannot be re-authored
        and cannot be abandoned — whatever the resume decides is what the
        signature published."""
        rid = self._plant_retired_predecessor()
        bid = self._prepare({self.a1: verdict, self.a2: "defer"})
        SO.advance(self._ctx(bid))                # cell 1: bind
        self.assertFalse((self.obs / f"survey-{bid}.yml").exists(),
                         "cell 1 left the live sheet, so this fixture is not "
                         "the unrecoverable one")
        row = next(r for r in SS.read_receipt(
            SS.receipt_paths(self.obs, bid)["receipt"])["rows"]
            if r["anchor_id"] == self.a1)
        self.assertEqual(row["candidate_id"], self.a1)
        self.assertEqual(row["verdict"], verdict)
        sid = self._open_successor(self.a1, rid,
                                   "The gateway now depends on httpx 2")
        self.state.save()
        return bid, sid

    def _live_records_per_anchor(self):
        import yaml
        out = {}
        for path in sorted(self.obs.glob("OBS-*.md")):
            fm = yaml.safe_load(re.match(r"^---\n(.*?)\n---\n",
                                         path.read_text(encoding="utf-8"),
                                         re.S).group(1))
            if str(fm.get("status")) in ("observed", "ratified"):
                out.setdefault(str(fm.get("anchor_id")), []).append(fm["id"])
        return out

    def test_a_bound_ratify_row_publishes_the_bound_claim(self):
        bid, sid = self._bind_then_remine("ratify")
        proc = self._run("--batch", bid)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self._state(bid), "S9")
        name = self._name_of(bid, self.a1)
        self.assertEqual(self._frontmatter(name)["governs"][0]["rule"],
                         "The gateway depends on httpx",
                         "the resume published the successor's claim under "
                         "the record id allocated for the bound one")
        self.assertIn("the-gateway-depends-on-httpx", name)
        # The successor is untouched — it awaits a batch of its own.
        self.assertEqual(self._rows()[sid]["state"], "observed")
        self.assertEqual(self._rows()[self.a1]["state"], "ratified")
        self.assertEqual({a: v for a, v in
                          self._live_records_per_anchor().items()
                          if len(v) > 1}, {})

    def test_a_bound_reject_row_converges_and_writes_no_record(self):
        bid, sid = self._bind_then_remine("reject")
        proc = self._run("--batch", bid)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self._state(bid), "S9")
        self.assertEqual(self._records(), ["OBS-0100-a-retired-claim.md"])
        # The REJECTED candidate is the bound one; the successor is untouched.
        self.assertEqual(self._rows()[self.a1]["state"], "rejected")
        self.assertEqual(self._rows()[sid]["state"], "observed")

    def test_a_bound_defer_row_converges_and_disposes_nothing(self):
        bid, sid = self._bind_then_remine("defer")
        proc = self._run("--batch", bid)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self._state(bid), "S9")
        self.assertEqual(self._rows()[self.a1]["state"], "observed")
        self.assertEqual(self._rows()[sid]["state"], "observed")

    def test_a_bound_row_whose_candidate_vanished_says_so(self):
        """The near-miss control for the binding: the row is bound to a
        candidate the state file no longer carries. Before the binding this
        silently resolved to whatever else sat on the anchor."""
        bid, sid = self._bind_then_remine("ratify")
        state = StateFile(self.state_path)
        del state.rows[self.a1]
        state.save()
        proc = self._run("--batch", bid)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn(f"bound by this batch's receipt to candidate {self.a1}",
                      " ".join(self._payload(proc)["findings"]))
        self.assertEqual(self._records(), ["OBS-0100-a-retired-claim.md"])


class UnfinishedBatchReservationTests(_SignoffCase):
    """An unfinished batch reserves every anchor its receipt names.

    The scaffold's "one live sheet at a time" refusal is the interlock that
    stops two sheets from ratifying one anchor — and cell 1 DELETES the live
    sheet, so from S1 on the interlock was gone while the batch still had
    every one of its writes ahead of it. A second batch could then scaffold a
    successor on a bound anchor and ratify it, and the earlier batch's resume
    either refused forever or published a second live record on that anchor.
    The receipt is the durable half of the same interlock."""

    def test_a_bound_batch_reserves_every_anchor_its_receipt_names(self):
        bid = self._prepare({self.a1: "defer"})       # a2 defaults to ratify
        SO.advance(self._ctx(bid))                    # cell 1: bind
        self.assertFalse((self.obs / f"survey-{bid}.yml").exists())
        proc = subprocess.run(
            [sys.executable, str(SCAFFOLD), "--repo-root", str(self.root),
             "--date", DATE], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertIsNone(payload["batch_id"],
                          "a second sheet opened over a bound batch's anchors")
        self.assertIn(bid, payload["note"])
        # Positive control: once the batch converges, the DEFERRED anchor is
        # offered again — the reservation is a hold, not a disposal.
        self.assertEqual(self._run("--batch", bid).returncode, 0)
        self.assertEqual(self._state(bid), "S9")
        nxt = self._scaffold()
        self.assertIsNotNone(nxt)
        sheet = SS.read_sheet(self.obs / f"survey-{nxt}.yml")
        self.assertEqual([r["anchor_id"] for r in sheet["rows"]], [self.a1])


class WriterReaderClosureTests(_SignoffCase):
    """The writer never emits a receipt this module's own reader refuses.

    `write_receipt` already runs `validate_receipt` — but it runs it once per
    write, and cell 1's write is the one that DELETES the live sheet. A row
    whose `retires` or `record_id` the reader refuses was therefore caught at
    cell 3, with the batch bound, the sheet gone and no route back: every retry
    re-ran cell 3, burnt two counter numbers and refused again on the same
    cell. The per-write check is not enough on its own, so the whole batch —
    every row, with the record id, path and retirement the allocation predicts
    — is validated in the PRE-WRITE lane, where a refusal costs the batch
    nothing and the human still has the sheet.
    """

    #: Filenames `record_paths` indexes (it keys on the frontmatter `id`) and
    #: `_tree_relative_record` refuses (it composes `_OBS_ID_RE` with
    #: `_SLUG_RE`). Observation records are hand-maintained, so any of these
    #: can sit in a real concern directory.
    UNNAMEABLE = [
        "OBS-0100-Mixed_Case.md",     # the slug grammar is lowercase kebab
        "OBS-0100-trailing-.md",      # ...with no trailing separator
        "OBS-0100-.md",               # ...and a non-empty slug
        "OBS-0100.md",                # the stem grammar wants a slug at all
        "OBS-100-short-number.md",    # the id grammar wants four digits
        "OBS-0100-a.b.c.md",          # a dot is outside the slug grammar
    ]

    def _plant_predecessor(self, filename):
        """A recorded predecessor under `filename`, and the successor a re-mine
        opens against it. Ratifying the successor makes the batch write
        `retires: <concern>/<filename>` into its receipt."""
        rid = "OBS-0100"
        (self.obs / filename).write_text(
            f"---\nid: {rid}\nanchor_id: {self.a1}\nstatus: ratified\n"
            'evidence: ["src/a.py:1-1"]\nprovenance: recovered\n'
            "governs:\n  - domain: runtime\n"
            "    rule: The gateway depends on httpx\n    scope: src/a.py\n"
            f"    handle: {rid}/the-gateway-depends-on-httpx\n"
            "    provenance: recovered\n---\n\nbody\n",
            encoding="utf-8")
        self._open_successor(self.a1, rid, "The gateway depends on httpx 2")
        self.state.save()
        return rid

    def _set_counter(self, value):
        path = self.tree / "manifest.yml"
        path.write_text(path.read_text(encoding="utf-8").replace(
            "next_number: 1", f"next_number: {value}"), encoding="utf-8")

    def _counter(self):
        return SS.read_counter(sp_module.read_manifest(self.root),
                               "observation", "next_number", 1)

    def test_a_predecessor_the_receipt_cannot_name_refuses_before_the_bind(self):
        for filename in self.UNNAMEABLE:
            with self.subTest(filename=filename):
                self.setUp()
                self._plant_predecessor(filename)
                bid = self._prepare()
                before = _snapshot(self.root)
                proc = self._run("--batch", bid)
                self.assertEqual(proc.returncode, 1, proc.stdout)
                self.assertIn(filename,
                              " ".join(self._payload(proc)["findings"]))
                self.assertEqual(self._state(bid), "S0")
                self.assertTrue((self.obs / f"survey-{bid}.yml").is_file(),
                                "the refusal took the human's sheet with it")
                self.assertEqual(_snapshot(self.root), before,
                                 "a pre-write refusal wrote to the tree")

    def test_the_same_predecessor_under_a_nameable_filename_publishes(self):
        """The positive control for the refusal above: nothing about the
        fixture except the predecessor's FILENAME changes."""
        self._plant_predecessor("OBS-0100-kebab-slug.md")
        bid = self._prepare()
        proc = self._run("--batch", bid)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self._state(bid), "S9")
        self.assertEqual(self._frontmatter("OBS-0100-kebab-slug.md")["status"],
                         "retired")

    def test_an_id_past_the_four_digit_space_refuses_before_it_burns(self):
        """`_cell_3_allocate` emits `OBS-{n:04d}` and the reader requires
        exactly four digits, so the writer's own output stopped being readable
        at 10000. Cell 3 moves the counter BEFORE it writes the ids, so each
        retry burnt one number per ratify row and refused again."""
        self._set_counter(10000)
        bid = self._prepare()
        for attempt in range(3):
            with self.subTest(attempt=attempt):
                proc = self._run("--batch", bid)
                self.assertEqual(proc.returncode, 1, proc.stdout)
                self.assertIn("OBS-10000",
                              " ".join(self._payload(proc)["findings"]))
                self.assertEqual(self._state(bid), "S0")
                self.assertTrue((self.obs / f"survey-{bid}.yml").is_file())
                self.assertEqual(self._counter(), 10000,
                                 "the retry burnt a counter number")

    def test_the_last_two_four_digit_ids_still_publish(self):
        """The positive control: one number below the boundary, the same two
        rows publish — so the refusal above is about the id GRAMMAR and not
        about a large counter."""
        self._set_counter(9998)
        bid = self._prepare()
        proc = self._run("--batch", bid)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self._state(bid), "S9")
        self.assertEqual(self._payload(proc)["records"],
                         ["OBS-9998", "OBS-9999"])

    def test_no_fixture_ever_leaves_the_batch_bound_and_unreadable(self):
        """The closure the two refusals above are instances of: over the whole
        fixture space, a batch either reaches S9 or refuses at S0 with the
        human's sheet still on disk. Bound-and-refusing is the one outcome
        with no scripted remedy, and it is what every case here used to be."""
        cases = [("baseline", lambda: None)]
        cases += [(f"predecessor {n}", lambda n=n: self._plant_predecessor(n))
                  for n in self.UNNAMEABLE + ["OBS-0100-kebab-slug.md"]]
        cases += [(f"counter at {c}", lambda c=c: self._set_counter(c))
                  for c in (9998, 9999, 10000, 99999)]
        reached = set()
        for name, setup in cases:
            with self.subTest(case=name):
                self.setUp()
                setup()
                bid = self._prepare()
                proc = self._run("--batch", bid)
                state = self._state(bid)
                reached.add(proc.returncode)
                if proc.returncode == 0:
                    self.assertEqual(state, "S9", proc.stdout)
                    SS.read_receipt(
                        SS.receipt_paths(self.obs, bid)["receipt"])
                else:
                    self.assertEqual(proc.returncode, 1, proc.stderr)
                    self.assertEqual(state, "S0", proc.stdout)
                    self.assertTrue((self.obs / f"survey-{bid}.yml").is_file())
        self.assertEqual(reached, {0, 1},
                         "the fixture space exercised only one outcome, so "
                         "the closure above is vacuous")


class DomainCharacterClassTests(_SignoffCase):
    """A domain is one line of PRINTABLE text.

    `_valid_domain` refused C0, C1 and DEL and nothing else, so the whole
    Unicode format class was admitted — a zero-width space, a bidi override, a
    BOM, a bare combining mark. `.strip()` does not remove any of them (none is
    whitespace by Python's definition), so a domain of nothing but a zero-width
    space read as a signed value. It is written into two record bodies, the
    receipt, the archived sheet and the human-facing concern index, and it is
    the doctrine's per-domain grouping key: two domains that render
    identically and group separately is a silent split of the doctrine.
    """

    #: Reach `signed_rows` from a human cell unchanged, so the predicate is
    #: the only thing standing between them and every published surface.
    INVISIBLE = {
        "zero-width space inside": "run\u200btime",
        "zero-width space alone": "\u200b",
        "right-to-left override": "\u202eevil",
        "bare combining acute": "\u0301runtime",
        "combining marks alone": "\u0301\u0302",
    }
    #: Never reach a signature through the SHEET at all: `write_catalog_yaml`
    #: re-reads what it emits through both parse paths and refuses output it
    #: cannot round-trip, and neither of these survives that. Both still reach
    #: `validate_receipt`, which is trusted input on every read.
    SHEET_LANE_CLOSED = {
        "byte-order mark": "\ufeffruntime",
        "line separator": "run\u2028time",
    }
    #: Ordinary text the refusal must not touch.
    PRINTABLE = {
        "ascii": "runtime",
        "accented": "caf\u00e9",
        "precomposed sequence": "runtime\u0301",   # a mark, not leading
        "cjk": "\u904b\u7528",
        "spaced": "build tooling",
    }

    def test_an_invisible_character_in_the_domain_refuses_before_any_write(self):
        for name, domain in self.INVISIBLE.items():
            with self.subTest(case=name):
                self.setUp()
                bid = self._prepare({}, {self.a1: domain, self.a2: domain})
                before = _snapshot(self.root)
                proc = self._run("--batch", bid)
                self.assertEqual(proc.returncode, 1, proc.stdout)
                self.assertIn("domain", json.dumps(self._payload(proc)))
                self.assertEqual(_snapshot(self.root), before,
                                 "the refusal wrote to the tree")
                self.assertEqual(self._records(), [])

    def test_the_receipt_reader_refuses_every_invisible_domain(self):
        """The receipt lane, which is wider than the sheet lane.

        A receipt is trusted input on every read and the digest binds the
        SHEET, so a hand-edited receipt is exactly what the reader is asked to
        trust — and it can carry a character the sheet writer will not emit or
        that `space_fold` removes before a signature ever sees it."""
        import yaml
        bid = self._prepare()
        SO.advance(self._ctx(bid))                       # -> S1, bind
        path = SS.receipt_paths(self.obs, bid)["receipt"]
        clean = SS.read_receipt(path)
        for name, domain in {**self.INVISIBLE, **self.SHEET_LANE_CLOSED}.items():
            with self.subTest(case=name):
                doc = json.loads(json.dumps(clean))
                doc["rows"][0]["domain"] = domain
                path.write_text(yaml.safe_dump(doc, sort_keys=False,
                                               allow_unicode=True),
                                encoding="utf-8")
                proc = self._run("--batch", bid)
                self.assertEqual(proc.returncode, 1, proc.stdout)
                self.assertIn("domain", json.dumps(self._payload(proc)))
                self.assertEqual(self._records(), [])
        # Control: the clean receipt restored, the same batch publishes.
        path.write_text(yaml.safe_dump(clean, sort_keys=False,
                                       allow_unicode=True), encoding="utf-8")
        self.assertEqual(self._run("--batch", bid).returncode, 0)
        self.assertEqual(self._state(bid), "S9")

    def test_the_sheet_writer_refuses_a_domain_it_cannot_read_back(self):
        """Why the case above is a RECEIPT test and not a CLI one for these
        two: `write_catalog_yaml` re-reads what it emits through both parse
        paths and refuses output it cannot round-trip, so neither character
        reaches a signature through the sheet at all.

        The control is the printable run in
        `test_ordinary_printable_domains_still_publish`, which the same writer
        emits without complaint."""
        for name, domain in self.SHEET_LANE_CLOSED.items():
            with self.subTest(case=name):
                self.setUp()
                bid = self._scaffold()
                with self.assertRaises(SS.SurveySheetError):
                    self._sign(bid, {}, {self.a1: domain, self.a2: domain})

    def test_ordinary_printable_domains_still_publish(self):
        """The positive control: the same fixture, the same cells, one
        printable domain, and every surface carries it."""
        for name, domain in self.PRINTABLE.items():
            with self.subTest(case=name):
                self.setUp()
                bid = self._prepare({}, {self.a1: domain, self.a2: domain})
                proc = self._run("--batch", bid)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                self.assertEqual(self._state(bid), "S9")
                record = self._frontmatter(self._name_of(bid, self.a1))
                # NFC, because `survey_sheet.cell_canon` normalizes at digest
                # time and at publish time alike: a decomposed sequence and
                # its composed spelling are one string, so they cannot digest
                # apart and then render identically. Every other case here is
                # already NFC, so this is an identity for four of the five.
                self.assertEqual(record["governs"][0]["domain"],
                                 unicodedata.normalize("NFC", domain))

    def test_no_invisible_character_reaches_any_surface(self):
        """The reach the refusal closes, stated as a whole-tree sweep: after a
        refused batch no file in the tree carries the character. The control is
        the printable run above, whose domain DOES reach five surfaces."""
        marker = "\u200b"
        bid = self._prepare({}, {self.a1: f"run{marker}time",
                                 self.a2: f"run{marker}time"})
        self.assertEqual(self._run("--batch", bid).returncode, 1)
        carriers = [str(f.relative_to(self.root))
                    for f in sorted(self.root.rglob("*"))
                    if f.is_file() and marker in _safe_text(f)]
        self.assertEqual(carriers, [f"bionic/observations/survey-{bid}.yml"],
                         "the only file that may carry it is the unsigned "
                         "sheet the human typed it into")
        # Control: the identical fixture with a printable domain lands on the
        # record bodies, the receipt, the archived sheet and the index.
        self.setUp()
        bid = self._prepare({}, {self.a1: "runtimeZZ", self.a2: "runtimeZZ"})
        self.assertEqual(self._run("--batch", bid).returncode, 0)
        reached = [str(f.relative_to(self.root))
                   for f in sorted(self.root.rglob("*"))
                   if f.is_file() and "runtimeZZ" in _safe_text(f)]
        self.assertGreaterEqual(len(reached), 5, reached)


class StateFileTmpNameTests(_SignoffCase):
    """`StateFile.save` was the one write in this lane not on the
    `<path>.tmp` + `O_EXCL` + `O_NOFOLLOW` shape.

    It used `tempfile.mkstemp`, whose name is random, and cleaned up in a
    `finally`. A `finally` runs for `KeyboardInterrupt` and does not run for
    `SIGKILL`, so a real kill left `arch/_recovered/tmp<random>.tmp` that
    nothing could ever name again — and
    `test_a_completed_run_leaves_no_tmp_file_anywhere_in_the_tree` was green at
    that site only because no test kills the process there.
    """

    def _tmps(self):
        return sorted(str(p.relative_to(self.root))
                      for p in self.root.rglob("*")
                      if p.is_file() and p.name.endswith(".tmp"))

    def test_an_interrupted_save_leaves_only_the_predictable_tmp_name(self):
        state = StateFile(self.state_path)
        real = os.replace

        def killed(src, dst, *args, **kwargs):
            raise KeyboardInterrupt(f"killed before os.replace -> {dst}")

        os.replace = killed
        try:
            with self.assertRaises(KeyboardInterrupt):
                state.save()
        finally:
            os.replace = real
        self.assertEqual(self._tmps(), ["bionic/arch/_recovered/state.yml.tmp"],
                         "the interrupted save left a name no later run can "
                         "reconstruct")

    def test_the_next_save_removes_the_stale_tmp_and_converges(self):
        """The remedy the predictable name buys: the writer that owns the name
        removes it. The control is the assertion that the stale file really was
        there to remove."""
        stale = self.state_path.parent / "state.yml.tmp"
        stale.parent.mkdir(parents=True, exist_ok=True)
        stale.write_text("left by a kill\n", encoding="utf-8")
        self.assertTrue(stale.is_file())
        state = StateFile(self.state_path)
        state.rows["deadbeefdeadbeef"] = {"id": "deadbeefdeadbeef",
                                          "state": "observed"}
        state.save()
        self.assertEqual(self._tmps(), [])
        self.assertIn("deadbeefdeadbeef",
                      self.state_path.read_text(encoding="utf-8"))

    def test_save_refuses_a_symlinked_target(self):
        """The leaf leg the shape carries: the target itself is a symlink, so
        writing through it would put the tree's dispositions in the link's
        target. The control is the same save with the link removed."""
        outside = self.root / "outside" / "state.yml"
        outside.parent.mkdir(parents=True, exist_ok=True)
        outside.write_text("candidates: []\n", encoding="utf-8")
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        if self.state_path.exists():
            self.state_path.unlink()
        self.state_path.symlink_to(outside)
        state = StateFile(self.state_path)
        state.rows["deadbeefdeadbeef"] = {"id": "deadbeefdeadbeef",
                                          "state": "observed"}
        with self.assertRaises(ValueError) as caught:
            state.save()
        self.assertIn("symlink", str(caught.exception))
        self.assertNotIn("deadbeef", outside.read_text(encoding="utf-8"))
        self.state_path.unlink()
        state.save()
        self.assertIn("deadbeef",
                      self.state_path.read_text(encoding="utf-8"))


class WedgeReportingTests(_SignoffCase):
    """A batch that cannot advance must say WHICH cell refused.

    The loop previously reported only that the machine "did not converge",
    which tells an operator with a wedged tree — both projections refusing
    every ADR — nothing about which surface to inspect."""

    def test_a_cell_that_refuses_to_advance_is_named_with_its_surface(self):
        bid = self._prepare()
        ctx = self._ctx(bid)
        real = SO.advance
        self.addCleanup(setattr, SO, "advance", real)
        SO.advance = lambda _c: ("S1", ["pretended to write the receipt"])
        with self.assertRaises(SS.SurveySheetError) as caught:
            SO.run(ctx, dry_run=False)
        text = " ".join(caught.exception.problems)
        self.assertIn(f"batch {bid} is wedged at S0", text)
        self.assertIn("cell 1", text)
        self.assertIn("sheet.yml", text)                 # the surface it names
        self.assertIn("pretended to write the receipt", text)  # what it reported
        # Positive control: the same ctx and the same tree converge to S9 the
        # moment the real cell runs, so the message above is a report about the
        # cell and not about the fixture.
        SO.advance = real
        code, payload = SO.run(ctx, dry_run=False)
        self.assertEqual((code, payload["state"]), (0, "S9"))


class AuditCheckerContractTests(_SignoffCase):
    """`check_observations` must exit 0 or 1 with JSON. `audit-docs` delegates
    to it, so a traceback breaks every rule's report, not just the one that
    raised — and past S7 `batch_state` builds a `StateFile`, whose fail-closed
    parse raises `ValueError` for a file outside this concern entirely."""

    def test_an_unreadable_candidate_state_file_is_a_finding(self):
        bid = self._prepare()
        self.assertEqual(self._run("--batch", bid).returncode, 0)
        check = _load("check_observations")
        # Control: the published tree audits clean through the same call.
        self.assertEqual(check.check(self.root, "bionic")["broken"], [])
        self.state_path.write_text("candidates: [oops\n", encoding="utf-8")
        result = check.check(self.root, "bionic")          # must not raise
        self.assertTrue(
            any("CHK-OBS-SURVEY-STUB" in b and bid in b
                for b in result["broken"]),
            f"the unreadable state file produced no finding: {result['broken']}")

    def _check_cli(self):
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "check_observations.py"),
             "--root", str(self.root)], capture_output=True, text=True)

    def test_the_cli_keeps_the_exit_0_or_1_with_json_contract(self):
        # The CLI resolves the tree through the strict config reader.
        (self.root / ".bionic.yml").write_text(
            'config_version: "1"\ndocs_dir: bionic\n', encoding="utf-8")
        bid = self._prepare()
        self.assertEqual(self._run("--batch", bid).returncode, 0)
        # Control: the clean tree reports through the same CLI at exit 0.
        clean = self._check_cli()
        self.assertEqual(clean.returncode, 0, clean.stderr)
        self.assertEqual(json.loads(clean.stdout)["broken"], [])

        self.state_path.write_text("candidates: [oops\n", encoding="utf-8")
        proc = self._check_cli()
        self.assertIn(proc.returncode, (0, 1), proc.stderr)
        # The discriminator: an uncaught ValueError also exits 1, but it
        # leaves stdout empty and a traceback on stderr. A report is a report
        # only if it parses and names the rule.
        payload = json.loads(proc.stdout or "null")
        self.assertIsInstance(payload, dict, proc.stderr)
        self.assertIn("CHK-OBS-SURVEY-STUB", json.dumps(payload["broken"]))
        self.assertNotIn("Traceback", proc.stderr)


class SnapshotNormalizationTests(_SignoffCase):
    """The byte-identity comparisons must not depend on the wall clock.

    `write_journal_hook` stamps the minute of the write; the stamp is cosmetic
    (the join is by day) but it is inside the journal file the snapshot hashes,
    so a run straddling a minute boundary failed the convergence subtests. That
    is a false RED on correct code, and `sync.sh` runs this suite as a
    refuse-first release gate."""

    def test_the_normalized_snapshot_ignores_the_stamp_and_sees_content(self):
        bid = self._prepare()
        self.assertEqual(self._run("--batch", bid).returncode, 0)
        hook = self.tree / "journal" / f"{DATE[:7]}.md"
        normalized = _snapshot(self.root, normalize_time=True)
        strict = _snapshot(self.root)

        text = hook.read_text(encoding="utf-8")
        bumped = re.sub(r"(## \[\d{4}-\d{2}-\d{2}) \d{2}:\d{2}\]", r"\1 23:59]",
                        text)
        self.assertNotEqual(bumped, text, "the hook carries no HH:MM stamp")
        hook.write_text(bumped, encoding="utf-8")
        self.assertEqual(_snapshot(self.root, normalize_time=True), normalized,
                         "a different minute changed the normalized snapshot")
        # Two controls. The STRICT snapshot does see the stamp — which is why
        # the convergence tests were time-dependent — and the normalized one
        # still sees a real content edit, so it has not been blinded.
        self.assertNotEqual(_snapshot(self.root), strict)
        hook.write_text(bumped + "\nA real content change.\n", encoding="utf-8")
        self.assertNotEqual(_snapshot(self.root, normalize_time=True),
                            normalized)


# ── [SECURITY] the candidate-state write's containment ─────────────────────

class StateFileContainmentTests(_SignoffCase):
    """`StateFile.save` is the sign-off's last write (§17.5 cell 14).

    `mkstemp` + `os.replace` made the LEAF and the TEMPORARY FILE safe and left
    the ANCESTOR and ROOT legs open — the class `survey_sheet._assert_contained`
    closes for every other write in this lane. A symlink at
    `<tree>/arch/_recovered` steered every ratification disposition outside the
    tree, at exit 0, with nothing on any surface to show for it.

    Both the CLI entry point and the helper are driven here: a refusal proven
    only against the private helper does not show that any real invocation
    reaches it."""

    def _outside(self, name="recovered"):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Path(tmp.name) / name

    def test_the_cli_refuses_a_symlinked_recovered_directory(self):
        outside = self._outside()
        recovered = self.state_path.parent
        shutil.move(str(recovered), str(outside))
        recovered.symlink_to(outside, target_is_directory=True)
        planted = (outside / "state.yml").read_bytes()

        bid = self._prepare()
        proc = self._run("--batch", bid)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("is a symlink", json.dumps(self._payload(proc)))
        self.assertEqual((outside / "state.yml").read_bytes(), planted,
                         "a disposition was written outside the tree")
        self.assertEqual(sorted(p.name for p in outside.iterdir()),
                         ["state.yml"], "a stray file landed outside the tree")

        # Positive control: the identical fixture with no symlink DOES write
        # the dispositions, so the refusal above is the guard and not a batch
        # that had nothing to dispose.
        self.setUp()
        bid2 = self._prepare()
        self.assertEqual(self._run("--batch", bid2).returncode, 0)
        rows = self._rows()
        self.assertEqual([rows[self.a1]["state"], rows[self.a2]["state"]],
                         ["ratified", "ratified"])

    def test_save_refuses_a_symlinked_parent_directory(self):
        outside = self._outside()
        outside.mkdir(parents=True)
        recovered = self.tree / "arch" / "linked"
        recovered.symlink_to(outside, target_is_directory=True)
        state = StateFile(recovered / "state.yml", contained_under=self.tree)
        state.rows["x"] = {"id": "x", "state": "observed"}
        with self.assertRaises(ValueError) as caught:
            state.save()
        self.assertIn("is a symlink", str(caught.exception))
        self.assertEqual(list(outside.iterdir()), [])
        # Positive control: the same rows through a real directory DO land.
        real = StateFile(self.tree / "arch" / "real" / "state.yml",
                         contained_under=self.tree)
        real.rows["x"] = {"id": "x", "state": "observed"}
        real.save()
        self.assertIn("id: x", real.path.read_text(encoding="utf-8"))

    def test_save_refuses_a_parent_resolving_outside_the_declared_tree(self):
        """The ANCESTOR leg: the immediate parent is a real directory, and a
        symlink one level higher steers the whole write out of the tree. Only
        the resolved-parent-under-root check sees this one."""
        outside = self._outside("arch")
        (outside / "_recovered").mkdir(parents=True)
        linked_arch = self.tree / "linked-arch"
        linked_arch.symlink_to(outside, target_is_directory=True)
        target = linked_arch / "_recovered" / "state.yml"
        self.assertFalse(target.parent.is_symlink())     # the parent is real

        state = StateFile(target, contained_under=self.tree)
        state.rows["x"] = {"id": "x", "state": "observed"}
        with self.assertRaises(ValueError) as caught:
            state.save()
        self.assertIn("not contained under", str(caught.exception))
        self.assertEqual(list((outside / "_recovered").iterdir()), [])
        # Positive control: declaring the tree the path actually resolves into
        # writes the identical rows, so the refusal is the containment check
        # rather than anything about the path or the payload.
        allowed = StateFile(target, contained_under=outside)
        allowed.rows["x"] = {"id": "x", "state": "observed"}
        allowed.save()
        self.assertIn("id: x", target.read_text(encoding="utf-8"))


# ── the cross-device promotion refusal ─────────────────────────────────────

class _Dev:
    """Just the one attribute `_assert_same_device` reads off a stat result."""

    def __init__(self, value):
        self.st_dev = value


class _OsWithForeignDevice:
    """The `os` module with `stat` reporting a distinct `st_dev` for one path.

    A genuine cross-device promotion needs two mounted filesystems, which this
    suite cannot arrange. What the guard reads is the SYSCALL'S ANSWER, so that
    is what is faked — the guard itself, the cell that calls it, the state
    machine that drives the cell and the CLI entry point all run unmodified.
    Every other attribute proxies to the real module, so `os.replace` in the
    same cell is the real one."""

    def __init__(self, real, foreign: Path):
        self._real = real
        # Compared after `realpath`: `make_context` resolves the repo root, and
        # on macOS the fixture's `/var/folders/...` temporary directory is a
        # symlink to `/private/var/folders/...`, so the two spellings of one
        # directory never compare equal as strings.
        self._foreign = real.path.realpath(foreign)

    def __getattr__(self, name):
        return getattr(self._real, name)

    def stat(self, path, *args, **kwargs):
        result = self._real.stat(path, *args, **kwargs)
        if self._real.path.realpath(path) == self._foreign:
            return _Dev(result.st_dev + 1)
        return result


class CrossDevicePromotionTests(_SignoffCase):
    """`_assert_same_device` had zero coverage: neutering it left all 183
    survey tests green, because every fixture stages and promotes inside one
    temporary directory.

    `os.replace` is atomic only within one filesystem. Across a device
    boundary it raises `OSError` rather than degrading silently — so the guard
    is what turns that into a named finding instead of an exit-2 crash with an
    empty stdout, on the one cell where a partial promotion is visible."""

    def _main(self, bid):
        """The CLI entry point, in-process so the fake `os` is reachable."""
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = SO.main(["--repo-root", str(self.root), "--batch", bid,
                            "--date", DATE])
        return code, buf.getvalue()

    def test_the_cli_refuses_to_promote_across_a_device_boundary(self):
        bid = self._prepare()
        staged = SS.receipt_paths(self.obs, bid)["staged"]
        real = SO.os
        self.addCleanup(setattr, SO, "os", real)
        SO.os = _OsWithForeignDevice(real, staged)

        code, out = self._main(bid)
        self.assertEqual(code, 1, out)
        self.assertIn("different filesystems", out)
        # The batch stopped exactly at the commit point, with completion
        # recorded and nothing yet visible — the state the guard protects.
        self.assertEqual(self._state(bid), "S4")
        self.assertEqual(self._records(), [])
        self.assertTrue(staged.is_dir())

        # Positive control: the identical tree with the real `os` promotes the
        # same staged bytes and reaches S9, so the refusal above is the device
        # check and not a batch that had nothing to promote.
        SO.os = real
        code, out = self._main(bid)
        self.assertEqual(code, 0, out)
        self.assertEqual(self._state(bid), "S9")
        self.assertEqual(len(self._records()), 2)

    def test_the_index_promotion_carries_the_same_guard(self):
        """Cell 11 moves the staged index with a second `os.replace`, and it
        is guarded separately — a batch that promoted its records across a
        boundary would otherwise lose the index move silently."""
        bid = self._prepare()
        ctx = self._ctx(bid)
        while self._state(bid) != "S5":
            SO.advance(ctx)
        staged = SS.receipt_paths(self.obs, bid)["staged"]
        real = SO.os
        self.addCleanup(setattr, SO, "os", real)
        SO.os = _OsWithForeignDevice(real, staged)
        with self.assertRaises(SS.SurveySheetError) as caught:
            SO.advance(ctx)
        self.assertIn("different filesystems", str(caught.exception))
        self.assertEqual(self._state(bid), "S5")
        self.assertTrue((staged / "index.md").is_file())
        # Control: the real `os` completes cell 11 from the same state.
        SO.os = real
        SO.advance(ctx)
        self.assertEqual(self._state(bid), "S6")
        self.assertFalse(staged.exists())


# ── [SECURITY] the receipt is trusted input on every read ──────────────────

class TamperedReceiptTests(_SignoffCase):
    """`read_receipt` validated three of eight row cells, so five were
    unchecked input on a path carrying a human signature.

    Each test below is an end-to-end reproduction through the shipped command,
    with the tamper applied to the FILE — not to a dict handed to a helper.
    The digest binds the SHEET, never the receipt, so a receipt edited after
    the signature is exactly what the reader is asked to trust.
    """

    def setUp(self):
        super().setUp()
        self.secret = self.root / "outside" / "id_rsa"
        self.secret.parent.mkdir()
        self.secret.write_text(
            "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaAAAA\n"
            "-----END OPENSSH PRIVATE KEY-----\n", encoding="utf-8")

    def _write_receipt_raw(self, batch_id, receipt):
        """The receipt as bytes, past every writer-side guard — which is what
        a tamper is."""
        import yaml
        path = SS.receipt_paths(self.obs, batch_id)["receipt"]
        path.write_text(yaml.safe_dump(receipt, sort_keys=False), encoding="utf-8")
        return path

    def _leaked(self):
        """Every file under the tree carrying a byte of the secret."""
        needle = "BEGIN OPENSSH PRIVATE KEY"
        out = []
        for f in sorted(self.tree.rglob("*")):
            if not f.is_file():
                continue
            try:
                if needle in f.read_text(encoding="utf-8"):
                    out.append(str(f.relative_to(self.tree)))
            except (UnicodeDecodeError, OSError):
                continue
        return out

    def _publish_and_open_successor(self):
        """Publish one batch, then re-mine a changed claim on its anchor and
        scaffold the successor batch that retires the record."""
        bid = self._prepare()
        self.assertEqual(self._run("--batch", bid).returncode, 0)
        pred_id, pred_rel = self._allocated(bid)[self.a1]
        self._open_successor(self.a1, pred_id,
                             "The gateway depends on httpx and on h2")
        self.state.save()
        return bid, pred_id, Path(pred_rel).name

    def test_a_symlinked_predecessor_is_never_read_through(self):
        """The record index reads every `OBS-*.md` under the concern and hands
        the predecessor's FILE to the retirement lane. `glob` returns a
        symlink and `read_text` follows it, so a link planted in the concern
        put a file from outside the repository into `bionic/observations/` at
        exit 0 and state S9."""
        _bid, _pred_id, pred_name = self._publish_and_open_successor()
        # The fixture is a real attack: the target exists and is readable.
        self.assertIn("BEGIN OPENSSH PRIVATE KEY",
                      self.secret.read_text(encoding="utf-8"))
        bid2 = self._prepare()
        # Planted after the scaffold, because the scaffold now refuses the
        # link on its own record walk — asserted below — and the subject here
        # is the SIGN-OFF's refusal.
        (self.obs / pred_name).unlink()
        (self.obs / pred_name).symlink_to(self.secret)

        proc = self._run("--batch", bid2)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("never through a link out of it",
                      json.dumps(self._payload(proc)))
        self.assertEqual(self._leaked(), [pred_name and
                                          f"observations/{pred_name}"],
                         "the only file naming the secret must be the planted "
                         "link itself")
        # The scaffold reads the same directory and refuses the same link in
        # the same exit-1 findings lane; that leg is driven end to end in
        # `test_recover.ObservationReaderContainmentTests`, over all three
        # shipped commands.
        # Control: the identical batch over a REAL predecessor publishes and
        # retires it, so the refusal is the containment leg and not the
        # successor lane failing for some other reason.
        (self.obs / pred_name).unlink()
        self.setUp()
        _bid, _pred_id, pred_name = self._publish_and_open_successor()
        bid3 = self._prepare()
        self.assertEqual(self._run("--batch", bid3).returncode, 0)
        self.assertEqual(self._frontmatter(pred_name)["status"], "retired")
        self.assertEqual(self._leaked(), [])

    def test_a_tampered_retires_path_never_copies_a_file_into_the_concern(self):
        """`retires` is read by `_cell_6_stage`, its basename is staged, and
        cell 9 promotes it into `observations/`. Unvalidated, it copied an
        OpenSSH private key into the concern at exit 0 and state S9."""
        _bid, _pred_id, pred_name = self._publish_and_open_successor()
        bid2 = self._prepare()
        ctx = self._ctx(bid2)
        SO.advance(ctx)                                  # -> S1  bind
        SO.advance(ctx)                                  # -> S2  allocate
        receipt = SS.read_receipt(SS.receipt_paths(self.obs, bid2)["receipt"])
        self.assertEqual(receipt["rows"][0]["retires"],
                         f"observations/{pred_name}")
        for tampered in (str(self.secret),                    # absolute
                         "../outside/id_rsa",                 # escaping
                         "observations/../../outside/id_rsa"):
            with self.subTest(retires=tampered):
                doc = json.loads(json.dumps(receipt))
                doc["rows"][0]["retires"] = tampered
                self._write_receipt_raw(bid2, doc)
                proc = self._run("--batch", bid2)
                self.assertEqual(proc.returncode, 1, proc.stdout)
                self.assertIn("retires", json.dumps(self._payload(proc)))
                self.assertEqual(self._leaked(), [],
                                 f"retires={tampered!r} put the secret into "
                                 "the tree")
                self.assertNotIn("id_rsa", self._records())
        # Control: the untampered receipt restored, the same batch publishes
        # and the retirement lands.
        self._write_receipt_raw(bid2, receipt)
        self.assertEqual(self._run("--batch", bid2).returncode, 0)
        self.assertEqual(self._state(bid2), "S9")
        self.assertEqual(self._frontmatter(pred_name)["status"], "retired")
        self.assertEqual(self._leaked(), [])

    def test_a_symlinked_retires_target_inside_the_tree_is_refused_at_the_read(self):
        """The read-side leg, isolated from the grammar leg above.

        `retires` is `<concern-dir>/OBS-NNNN-<slug>.md`, and a symlink AT that
        spelling passes every spelling check there is — the grammar describes a
        name and a symlink is a name. Only resolved containment decides it, and
        three guards now stand on that one value: `record_paths` refuses the
        link when it builds the predecessor index, `read_receipt` pins the
        directory component to the concern, and `_cell_6_stage` resolves the
        path it is about to read. This drives the whole stack through the
        shipped command; the assertion is that NO byte of the target lands."""
        _bid, _pred_id, pred_name = self._publish_and_open_successor()
        bid2 = self._prepare()
        ctx = self._ctx(bid2)
        SO.advance(ctx)                                  # -> S1
        SO.advance(ctx)                                  # -> S2
        # Planted AFTER the scaffold, because `record_paths` and
        # `read_recorded_observations` both refuse a link in the concern the
        # moment they see one — which is the point: this test drives the leg
        # BEHIND them, on a receipt already bound.
        planted = self.obs / "OBS-0001-planted-slug.md"
        planted.symlink_to(self.secret)
        # The fixture is a real attack: the link reads through to the secret,
        # and the value passes the receipt grammar untouched.
        self.assertIn("BEGIN OPENSSH PRIVATE KEY",
                      planted.read_text(encoding="utf-8"))
        doc = SS.read_receipt(SS.receipt_paths(self.obs, bid2)["receipt"])
        clean = json.loads(json.dumps(doc))
        doc["rows"][0]["retires"] = "observations/OBS-0001-planted-slug.md"
        self._write_receipt_raw(bid2, doc)
        # The GRAMMAR admits it — the value is a well-formed concern-relative
        # record name, so the refusal below is the resolved-containment leg.
        SS.read_receipt(SS.receipt_paths(self.obs, bid2)["receipt"])

        proc = self._run("--batch", bid2)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("never through a link out of it",
                      json.dumps(self._payload(proc)))
        self.assertEqual(self._leaked(), ["observations/OBS-0001-planted-slug.md"],
                         "the only file naming the secret must be the planted "
                         "link itself")
        # Control: the receipt restored and the link removed, the same batch
        # publishes and retires its own predecessor.
        planted.unlink()
        self._write_receipt_raw(bid2, clean)
        self.assertEqual(self._run("--batch", bid2).returncode, 0)
        self.assertEqual(self._state(bid2), "S9")
        self.assertEqual(self._frontmatter(pred_name)["status"], "retired")

    def test_a_retires_path_outside_the_concern_directory_is_refused(self):
        """`_tree_relative_record` checked "exactly two components" and never
        compared the first to the concern, while its own error text promised
        `<concern-dir>/`. So `retires: adrs/OBS-0100-x.md` — a perfectly
        ordinary spelling naming a file in a SIBLING concern — passed the
        grammar, resolved inside the tree, and copied an ADR body into
        `observations/` at exit 0 and state S9.

        The concern is read off the receipt's own LOCATION
        (`<concern>/_surveys/<batch>/receipt.yml`), never off a cell inside it:
        a hand-edited receipt can restate any cell, and not the directory it
        sits in."""
        _bid, _pred_id, pred_name = self._publish_and_open_successor()
        adrs = self.tree / "adrs"
        adrs.mkdir(exist_ok=True)
        (adrs / "OBS-0100-not-an-observation.md").write_text(
            "---\nid: OBS-0100\nstatus: ratified\n"
            f'anchor_id: "{self.a1}"\n---\n\nCONFIDENTIAL ADR BODY\n',
            encoding="utf-8")

        bid2 = self._prepare()
        ctx = self._ctx(bid2)
        SO.advance(ctx)                                  # -> S1  bind
        SO.advance(ctx)                                  # -> S2  allocate
        receipt = SS.read_receipt(SS.receipt_paths(self.obs, bid2)["receipt"])
        clean = json.loads(json.dumps(receipt))
        for cell in ("retires", "record_path"):
            with self.subTest(cell=cell):
                doc = json.loads(json.dumps(clean))
                # `record_path` keeps the row's OWN record id, so the sibling
                # directory is the only difference from a value that validates.
                name = (Path(clean["rows"][0]["record_path"]).name
                        if cell == "record_path"
                        else "OBS-0100-not-an-observation.md")
                doc["rows"][0][cell] = f"adrs/{name}"
                self._write_receipt_raw(bid2, doc)
                with self.assertRaises(SS.SurveySheetError):
                    SS.read_receipt(
                        SS.receipt_paths(self.obs, bid2)["receipt"])
                proc = self._run("--batch", bid2)
                self.assertEqual(proc.returncode, 1, proc.stdout)
                self.assertIn(cell, json.dumps(self._payload(proc)))
                self.assertFalse(
                    (self.obs / "OBS-0100-not-an-observation.md").exists(),
                    "the sibling concern's file was copied into observations/")
                self.assertEqual(len(self._records()), 2,
                                 "the batch published while its receipt was "
                                 "unreadable")
        # Control: the untampered receipt publishes and retires its own
        # predecessor, so the refusal is about the DIRECTORY component alone.
        self._write_receipt_raw(bid2, clean)
        self.assertEqual(self._run("--batch", bid2).returncode, 0)
        self.assertEqual(self._state(bid2), "S9")
        self.assertEqual(self._frontmatter(pred_name)["status"], "retired")

    def test_a_crafted_record_id_never_forges_a_log_entry(self):
        """`record_id` is written verbatim into `log.md` and the journal hook.
        Unvalidated, a crafted one wrote a fabricated
        `## [2020-01-01] adr | ...` entry that the summaries projection parses
        as genuine."""
        payload = ("OBS-0001\n\n## [2020-01-01] adr | forged decision\n\n"
                   "This entry was never written by anyone.\n")
        # The fixture is a real attack: the payload parses as a log op.
        parsed = list(sp_module._log_entries("# Log\n\n" + payload))
        self.assertEqual([(e["op"], e["subject"]) for e in parsed],
                         [("adr", "forged decision")])

        bid = self._prepare()
        ctx = self._ctx(bid)
        SO.advance(ctx)                                  # -> S1
        SO.advance(ctx)                                  # -> S2  ids allocated
        doc = SS.read_receipt(SS.receipt_paths(self.obs, bid)["receipt"])
        doc["rows"][0]["record_id"] = payload
        self._write_receipt_raw(bid, doc)

        proc = self._run("--batch", bid)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("record_id", json.dumps(self._payload(proc)))
        log = (self.tree / "log.md").read_text(encoding="utf-8")
        self.assertNotIn("forged decision", log)
        self.assertEqual([e["op"] for e in sp_module._log_entries(log)], [])
        # Control: the untampered receipt publishes and writes exactly one
        # genuine `observation | survey batch` op.
        doc["rows"][0]["record_id"] = "OBS-0001"
        self._write_receipt_raw(bid, doc)
        self.assertEqual(self._run("--batch", bid).returncode, 0)
        log = (self.tree / "log.md").read_text(encoding="utf-8")
        self.assertEqual([(e["op"], e["subject"])
                          for e in sp_module._log_entries(log)],
                         [("observation", f"survey batch {bid}")])


# ── the nested `docs_dir` the successor read path got wrong ────────────────

class NestedDocsDirTests(_SignoffCase):
    """`docs_dir` is validated as a multi-segment path, and the successor
    lane's predecessor read joined `root / tree.name / retires` — dropping
    every leading segment of a `sub/bionic`. The read then raised
    FileNotFoundError, an OSError, so it surfaced on the exit-2 ENVIRONMENT
    lane rather than as a finding, with the batch wedged at S2 and its
    record ids already allocated."""

    def setUp(self):
        super().setUp()
        (self.root / "sub").mkdir()
        shutil.move(str(self.tree), str(self.root / "sub" / "bionic"))
        self.tree = self.root / "sub" / "bionic"
        self.obs = self.tree / "observations"
        self.state_path = self.tree / "arch" / "_recovered" / "state.yml"
        self.state = StateFile(self.state_path)
        (self.root / ".bionic.yml").write_text("docs_dir: sub/bionic\n",
                                               encoding="utf-8")

    def test_a_successor_retires_its_predecessor_under_a_nested_docs_dir(self):
        bid = self._prepare()
        first = self._run("--batch", bid)
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        self.assertEqual(len(self._records()), 2)
        pred_id, pred_rel = self._allocated(bid)[self.a1]

        self._open_successor(self.a1, pred_id,
                             "The gateway depends on httpx and on h2")
        self.state.save()
        bid2 = self._prepare()
        second = self._run("--batch", bid2)
        self.assertEqual(second.returncode, 0,
                         f"stderr={second.stderr} stdout={second.stdout}")
        self.assertEqual(self._state(bid2), "S9")
        # The discriminator: the retirement actually happened. Exit 0 alone
        # would also be true of a batch that staged nothing.
        self.assertEqual(self._frontmatter(Path(pred_rel).name)["status"],
                         "retired")
        # Three files: the untouched sibling, the retired predecessor, and the
        # successor the second batch published.
        self.assertEqual(len(self._records()), 3)


if __name__ == "__main__":
    unittest.main()
