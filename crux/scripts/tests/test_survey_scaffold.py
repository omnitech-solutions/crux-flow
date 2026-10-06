"""Tests for `scaffold-survey-sheet.py` (ADR-0098, docs/AGENTS.md §17.5).

The scaffold is the machine half of batch ratification: it seeds `anchor_id`
and `proposed_domain` and **fills no human cell**. Two properties carry that,
and both are pinned here with a positive control:

  * every human cell in a scaffolded sheet is the empty string, and the
    control is that the machine cells are NOT empty (so the emptiness is a
    property of the human cells, not of a sheet the scaffold failed to fill);
  * `--dry-run` writes nothing, and the control is the same invocation without
    the flag, which changes the whole-tree byte snapshot.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
SCAFFOLD = SCRIPTS / "scaffold-survey-sheet.py"
SIGNOFF = SCRIPTS / "signoff-survey.py"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


SS = _load("survey_sheet")


def _load_signoff():
    spec = importlib.util.spec_from_file_location("signoff_survey", SIGNOFF)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["signoff_survey"] = mod
    spec.loader.exec_module(mod)
    return mod
from crux.arch.recover import StateFile, candidate_id  # noqa: E402

MANIFEST = """schema_version: 5
concerns_enabled: [adrs, observations, arch]

observation:
  next_number: 1
  stale_days: 90
"""


def _run(*args):
    return subprocess.run([sys.executable, str(SCAFFOLD), *args],
                          capture_output=True, text=True)


def _snapshot(root: Path) -> dict:
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts:
            out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


class ScaffoldTestCase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.tree = self.root / "bionic"
        self.obs = self.tree / "observations"
        self.obs.mkdir(parents=True)
        (self.root / ".bionic.yml").write_text("docs_dir: bionic\n", encoding="utf-8")
        (self.tree / "manifest.yml").write_text(MANIFEST, encoding="utf-8")
        (self.root / "src").mkdir()
        (self.root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
        self.state_path = self.tree / "arch" / "_recovered" / "state.yml"
        self.state = StateFile(self.state_path)
        self.a1 = self._add("httpx", "The gateway depends on httpx", "runtime")
        self.state.save()

    def _add(self, name, rule, domain=None, state="observed"):
        cid = candidate_id("external-dependency", name)
        row = {"id": cid, "anchor_kind": "external-dependency",
               "canonical_anchor": name, "rule": rule,
               "evidence": ["src/a.py:1-1"], "state": state}
        if domain is not None:
            row["domain"] = domain
        self.state.rows[cid] = row
        return cid

    def _scaffold(self, *extra):
        return _run("--repo-root", str(self.root), *extra)

    def _payload(self, proc):
        return json.loads(proc.stdout)

    def _signoff(self, verdicts, batch_id="SVY-0001"):
        """Author the human cells on the live sheet and drive the batch to S9
        through the shipped sign-off.

        A hand-forged receipt is not a published batch: `batch_state` derives
        the state from the log op, the journal hook and the candidate
        dispositions as well, and a batch below S9 HOLDS its anchors
        (`survey_sheet.unfinished_batch_anchors`). So the fixture runs the real
        command rather than writing the one file a scaffold happens to read."""
        path = self.obs / f"survey-{batch_id}.yml"
        sheet = SS.read_sheet(path)
        for row in sheet["rows"]:
            row["verdict"] = verdicts.get(row["anchor_id"], "ratify")
            row["domain"] = "runtime"
            row["rationale"] = "Reviewed against the cited lines."
        SS.write_sheet(path, sheet, contained_under=self.tree)
        (self.tree / "log.md").write_text("# Log\n", encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(SIGNOFF), "--repo-root", str(self.root),
             "--date", "2026-08-30", "--batch", batch_id],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(
            SS.batch_state(SS.receipt_paths(self.obs, batch_id)["receipt"],
                           self.obs), "S9")
        return proc

    def _sheet(self, batch_id="SVY-0001"):
        return SS.read_sheet(self.obs / f"survey-{batch_id}.yml")

    # ── the happy path ─────────────────────────────────────────────────────

    def test_scaffold_writes_a_readable_sheet_with_the_seeded_cells(self):
        proc = self._scaffold()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = self._payload(proc)
        self.assertEqual(payload["batch_id"], "SVY-0001")
        sheet = self._sheet()
        self.assertEqual(len(sheet["rows"]), 1)
        row = sheet["rows"][0]
        self.assertEqual(row["anchor_id"], self.a1)
        self.assertEqual(row["proposed_domain"], "runtime")

    def test_no_human_cell_is_filled_and_the_machine_cells_are(self):
        """The absence assertion, with its positive control: the human cells
        are empty AND the machine cells are not, so the emptiness is a property
        of the human cells rather than of an empty sheet."""
        self.assertEqual(self._scaffold().returncode, 0)
        row = self._sheet()["rows"][0]
        for cell in ("verdict", "domain", "rationale"):
            self.assertEqual(row[cell], "", f"the scaffold filled {cell}")
        self.assertNotEqual(row["anchor_id"], "")
        self.assertNotEqual(row["proposed_domain"], "")

    def test_an_unmined_domain_seeds_empty_so_the_human_must_name_one(self):
        self._add("yaml", "The loader pins pyyaml", domain=None)
        self.state.save()
        self.assertEqual(self._scaffold().returncode, 0)
        seeds = {r["anchor_id"]: r["proposed_domain"] for r in self._sheet()["rows"]}
        self.assertEqual(seeds[candidate_id("external-dependency", "yaml")], "")
        # Control: the mined candidate in the SAME sheet does carry its seed.
        self.assertEqual(seeds[self.a1], "runtime")

    def test_the_counter_advances_and_is_never_reused(self):
        self.assertEqual(self._scaffold().returncode, 0)
        text = (self.tree / "manifest.yml").read_text(encoding="utf-8")
        self.assertIn("next_survey_number: 2", text)
        # A second batch on a fresh candidate takes the NEXT number.
        self._sign_off_the_live_sheet("SVY-0001")
        self._add("yaml", "The loader pins pyyaml", "runtime")
        self.state.save()
        proc = self._scaffold()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._payload(proc)["batch_id"], "SVY-0002")

    def _sign_off_the_live_sheet(self, batch_id):
        """Stand in for the sign-off: archive the sheet and drop a receipt
        disposing its anchors, which is what frees the tree for a next batch."""
        paths = SS.receipt_paths(self.obs, batch_id)
        paths["dir"].mkdir(parents=True, exist_ok=True)
        sheet = SS.read_sheet(paths["live"])
        rows = [{"anchor_id": r["anchor_id"], "candidate_id": r["anchor_id"],
                 "verdict": "ratify",
                 "domain": r["proposed_domain"] or "runtime",
                 "domain_source": "proposed", "record_id": "",
                 "record_path": "", "retires": ""} for r in sheet["rows"]]
        SS.write_receipt(paths["receipt"], {
            "config_version": "1", "batch_id": batch_id,
            "sheet_ref": f"_surveys/{batch_id}/sheet.yml",
            "digest": SS.sheet_digest(sheet),
            "scaffold_provenance": sheet["scaffold_provenance"],
            "signed": "2026-08-30", "completed": "2026-08-30",
            "records": [], "rows": rows}, contained_under=self.tree)
        paths["live"].rename(paths["sheet"])

    # ── the filters ────────────────────────────────────────────────────────

    def test_an_anchor_a_receipt_disposed_is_not_re_scaffolded(self):
        self.assertEqual(self._scaffold().returncode, 0)
        self._sign_off_the_live_sheet("SVY-0001")
        proc = self._scaffold()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._payload(proc)["rows"], 0)
        # Control: a NEW candidate on the same tree does scaffold.
        self._add("yaml", "The loader pins pyyaml", "runtime")
        self.state.save()
        proc = self._scaffold()
        self.assertEqual(self._payload(proc)["rows"], 1)

    def test_a_deferred_anchor_is_re_scaffoldable(self):
        """`defer` is the only re-scaffoldable verdict (§17.5). Its control is
        the ratified sibling in the same receipt, which is NOT re-scaffolded.

        The first batch is driven to S9 rather than hand-forged, because a
        batch below S9 holds its anchors whatever its verdicts say — see
        `_signoff`."""
        other = self._add("yaml", "The loader pins pyyaml", "runtime")
        self.state.save()
        self.assertEqual(self._scaffold().returncode, 0)
        self._signoff({self.a1: "defer"})
        proc = self._scaffold()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        anchors = [r["anchor_id"] for r in self._sheet("SVY-0002")["rows"]]
        self.assertEqual(anchors, [self.a1])
        self.assertNotIn(other, anchors)

    def test_an_anchor_a_bound_batch_still_holds_is_not_scaffolded(self):
        """Cell 1 deletes the live sheet, so the "one live sheet" refusal above
        stops guarding the moment a batch is bound. The receipt carries the
        rest of that interlock: until the batch reaches S9 its anchors are
        held, so no second sheet can ratify one of them.

        The control is the same tree one sign-off later — the hold lifts, and
        the deferred anchor is offered again."""
        other = self._add("yaml", "The loader pins pyyaml", "runtime")
        self.state.save()
        self.assertEqual(self._scaffold().returncode, 0)
        # Bind SVY-0001 and stop there: the receipt exists, the live sheet
        # does not, and every write the batch makes is still ahead of it.
        signoff = _load_signoff()
        ctx = signoff.make_context(self.root, None, "SVY-0001", "2026-08-30")
        path = self.obs / "survey-SVY-0001.yml"
        sheet = SS.read_sheet(path)
        for row in sheet["rows"]:
            row["verdict"] = "defer" if row["anchor_id"] == self.a1 else "ratify"
            row["domain"] = "runtime"
            row["rationale"] = "Reviewed."
        SS.write_sheet(path, sheet, contained_under=self.tree)
        signoff.advance(ctx)
        self.assertFalse(path.exists())
        self.assertNotEqual(
            SS.batch_state(SS.receipt_paths(self.obs, "SVY-0001")["receipt"],
                           self.obs), "S9")

        proc = self._scaffold()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = self._payload(proc)
        self.assertIsNone(payload["batch_id"])
        self.assertEqual(payload["rows"], 0)
        self.assertIn("SVY-0001", payload["note"])
        self.assertFalse((self.obs / "survey-SVY-0002.yml").exists())

        # Control: the hold lifts at S9 and the deferred anchor comes back.
        (self.tree / "log.md").write_text("# Log\n", encoding="utf-8")
        done = subprocess.run(
            [sys.executable, str(SIGNOFF), "--repo-root", str(self.root),
             "--date", "2026-08-30", "--batch", "SVY-0001"],
            capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        proc = self._scaffold()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        anchors = [r["anchor_id"] for r in self._sheet("SVY-0002")["rows"]]
        self.assertEqual(anchors, [self.a1])
        self.assertNotIn(other, anchors)

    def test_an_anchor_a_live_record_already_holds_is_not_scaffolded(self):
        (self.obs / "OBS-0001-httpx.md").write_text(
            "---\nid: OBS-0001\nstatus: ratified\n"
            f'anchor_id: "{self.a1}"\nevidence: ["src/a.py:1-1"]\n'
            "governs:\n  - domain: runtime\n    rule: The gateway depends on httpx\n"
            "---\n\nbody\n", encoding="utf-8")
        proc = self._scaffold()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._payload(proc)["rows"], 0)
        # Control: a successor candidate on that SAME recorded anchor scaffolds.
        sid = f"{self.a1}+1"
        self.state.rows[sid] = {
            "id": sid, "anchor_id": self.a1, "state": "observed",
            "predecessor_id": "OBS-0001", "anchor_kind": "external-dependency",
            "canonical_anchor": "httpx", "rule": "The gateway also pins h2",
            "evidence": ["src/a.py:1-1"], "domain": "runtime"}
        self.state.save()
        proc = self._scaffold()
        self.assertEqual(self._payload(proc)["rows"], 1)

    def test_a_disposed_candidate_row_is_not_scaffolded(self):
        self.state.rows[self.a1]["state"] = "rejected"
        self.state.save()
        self.assertEqual(self._payload(self._scaffold())["rows"], 0)
        # Control: flipped back to observed, the same row scaffolds.
        self.state.rows[self.a1]["state"] = "observed"
        self.state.save()
        self.assertEqual(self._payload(self._scaffold())["rows"], 1)

    # ── refusals ───────────────────────────────────────────────────────────

    def test_a_live_unsigned_sheet_refuses_a_second_scaffold(self):
        self.assertEqual(self._scaffold().returncode, 0)
        self._add("yaml", "The loader pins pyyaml", "runtime")
        self.state.save()
        proc = self._scaffold()
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("awaiting sign-off", json.dumps(self._payload(proc)))
        # Control: with the live sheet signed away, the same tree scaffolds.
        self._sign_off_the_live_sheet("SVY-0001")
        self.assertEqual(self._scaffold().returncode, 0)

    def test_the_concern_being_disabled_is_exit_two_naming_the_concern(self):
        (self.tree / "manifest.yml").write_text(
            MANIFEST.replace("[adrs, observations, arch]", "[adrs, arch]"),
            encoding="utf-8")
        proc = self._scaffold()
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn("concerns_enabled", proc.stderr)
        # Control run: the enabled tree exits 0 and writes a sheet.
        (self.tree / "manifest.yml").write_text(MANIFEST, encoding="utf-8")
        self.assertEqual(self._scaffold().returncode, 0)

    def test_an_absent_state_file_scaffolds_nothing_and_exits_zero(self):
        self.state_path.unlink()
        proc = self._scaffold()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._payload(proc)["rows"], 0)
        self.assertEqual(self._payload(proc)["written"], [])

    def test_a_corrupt_state_file_is_exit_two_naming_the_state_file(self):
        self.state_path.write_text("candidates: [\n unterminated", encoding="utf-8")
        proc = self._scaffold()
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn("state", proc.stderr.lower())
        # Control run: a readable state file exits 0.
        self.state.save()
        self.assertEqual(self._scaffold().returncode, 0)

    # ── --dry-run ──────────────────────────────────────────────────────────

    def test_dry_run_writes_nothing_and_the_control_writes(self):
        before = _snapshot(self.root)
        proc = self._scaffold("--dry-run")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(_snapshot(self.root), before,
                         "--dry-run changed the tree")
        self.assertEqual(self._payload(proc)["written"], [])
        # Positive control: the same invocation without the flag DOES write.
        proc = self._scaffold()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotEqual(_snapshot(self.root), before)
        self.assertTrue(self._payload(proc)["written"])

    def test_dry_run_does_not_burn_the_counter(self):
        self._scaffold("--dry-run")
        self.assertNotIn("next_survey_number",
                         (self.tree / "manifest.yml").read_text(encoding="utf-8"))
        self._scaffold()
        self.assertIn("next_survey_number: 2",
                      (self.tree / "manifest.yml").read_text(encoding="utf-8"))


class AnchorCollisionTests(ScaffoldTestCase):
    """`_eligible` returns one entry per CANDIDATE key; a sheet is keyed by
    `anchor_id`, and a successor candidate sits on its predecessor's anchor.

    Two eligible candidates on one anchor therefore produced a sheet with
    duplicate `anchor_id` rows — a sheet the scaffold's own reader refuses, with
    the SVY number already burnt and no scripted way back."""

    def _open_successor(self):
        sid = f"{self.a1}+1"
        self.state.rows[sid] = {
            "id": sid, "anchor_id": self.a1, "state": "observed",
            "predecessor_id": "OBS-0001",
            "anchor_kind": "external-dependency", "canonical_anchor": "httpx",
            "rule": "The gateway depends on httpx and on h2",
            "evidence": ["src/a.py:1-1"], "domain": "runtime"}
        self.state.save()
        return sid

    def test_two_candidates_on_one_anchor_refuse_before_the_counter_moves(self):
        sid = self._open_successor()
        before = _snapshot(self.root)
        proc = self._scaffold()
        self.assertEqual(proc.returncode, 1, proc.stdout)
        findings = json.dumps(self._payload(proc))
        self.assertIn("would collide", findings)
        self.assertIn(self.a1, findings)
        self.assertIn(sid, findings)
        self.assertEqual(_snapshot(self.root), before,
                         "the refusal wrote a sheet or burnt a number")
        self.assertNotIn("next_survey_number",
                         (self.tree / "manifest.yml").read_text(encoding="utf-8"))
        # Positive control: the identical fixture with one candidate on the
        # anchor scaffolds SVY-0001, so the refusal is the collision and not a
        # tree the scaffold could not read.
        self.state = StateFile(self.state_path)
        del self.state.rows[sid]
        self.state.save()
        proc = self._scaffold()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._payload(proc)["batch_id"], "SVY-0001")


class SurveySheetSkillContractTests(unittest.TestCase):
    """The scaffold skill's step 4 told the model to write the SIGN-OFF's log
    op. Following it wedged the batch: `batch_state` reads any
    `observation | survey batch <id>` entry as cell 12 already done, so the
    sign-off skips it, and cell 16 then refuses the entry as a contradicting
    surface — with nothing published and a `log.md` hand-edit, which
    `survey-signoff` forbids, as the only way out."""

    SKILLS = Path(__file__).resolve().parents[2] / "skills"

    def test_the_scaffold_skill_does_not_instruct_the_signoffs_log_op(self):
        text = (self.SKILLS / "survey-sheet" / "SKILL.md").read_text(
            encoding="utf-8")
        self.assertNotIn("observation | survey batch SVY-NNNN\n```", text,
                         "the scaffold skill still hands the model the "
                         "sign-off's log-op heading to write")
        self.assertIn("The scaffold logs nothing", text)
        # Positive control: the subject is not gone from the pair, it moved to
        # its owner — the sign-off, which writes it as part of the publish.
        signoff = (self.SKILLS / "survey-signoff" / "SKILL.md").read_text(
            encoding="utf-8")
        self.assertIn("The script writes the `observation` log op", signoff)


if __name__ == "__main__":
    unittest.main()


class PrefixedTreeRefusalTests(ScaffoldTestCase):
    """§17.1 permits a record id to carry the §14.3 `artifact_prefix`, and the
    audit (`check_observations`) and the summaries projection both read that
    dual form. The survey lane does not: `survey_sheet.record_paths` and
    `recover.read_recorded_observations` glob `OBS-*.md`, and the sign-off
    allocates a bare `f"OBS-{n:04d}"`.

    In a tree with a non-empty prefix both survey readers therefore returned
    `[]` for every record on disk, so `build_plan`'s "would ratify a second
    live record on an anchor" refusal never fired, `CHK-OBS-ANCHOR` broke, and
    the batch published a bare-id record into a prefixed tree at exit 0.

    A supported configuration that fails OPEN is the defect. The remedy here
    is a fail-closed refusal — the survey lane does not support a prefixed
    tree and now says so — not a partial prefix-awareness, because a reader
    made prefix-aware while one glob or the allocation was missed reopens the
    same hole under a new spelling.
    """

    PREFIX_LINE = 'docs_dir: bionic\nartifact_prefix: "CRX"\n'

    def _prefix(self):
        (self.root / ".bionic.yml").write_text(self.PREFIX_LINE, encoding="utf-8")

    def test_scaffold_refuses_a_prefixed_tree(self):
        self._prefix()
        proc = self._scaffold("--date", "2026-08-30")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        blob = proc.stdout + proc.stderr
        self.assertIn("artifact_prefix", blob)
        self.assertIn("CRX", blob)
        self.assertFalse(list(self.obs.glob("survey-*.yml")),
                         "a refusing scaffold wrote a sheet")

    def test_the_unprefixed_control_scaffolds(self):
        """The positive control: the same fixture, one config line different,
        reaches the writer. Without it the refusal above holds on a tree that
        could not scaffold for some unrelated reason."""
        proc = self._scaffold("--date", "2026-08-30")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(list(self.obs.glob("survey-*.yml")))

    def test_signoff_refuses_a_tree_that_gained_a_prefix_after_scaffolding(self):
        """The sign-off is the WRITER, and it re-reads the concern on every
        cell, so it takes the refusal on its own read rather than trusting the
        scaffold's."""
        proc = self._scaffold("--date", "2026-08-30")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        path = self.obs / "survey-SVY-0001.yml"
        sheet = SS.read_sheet(path)
        for row in sheet["rows"]:
            row["verdict"] = "ratify"
            row["domain"] = "runtime"
            row["rationale"] = "Reviewed against the cited lines."
        SS.write_sheet(path, sheet, contained_under=self.tree)
        (self.tree / "log.md").write_text("# Log\n", encoding="utf-8")
        before = _snapshot(self.root)
        self._prefix()
        proc = subprocess.run(
            [sys.executable, str(SIGNOFF), "--repo-root", str(self.root),
             "--date", "2026-08-30", "--batch", "SVY-0001"],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("artifact_prefix", proc.stdout + proc.stderr)
        after = _snapshot(self.root)
        moved = {k for k in set(before) | set(after)
                 if before.get(k) != after.get(k)} - {".bionic.yml"}
        self.assertEqual(moved, set(), f"a refusing sign-off wrote {moved}")
        self.assertFalse(list(self.obs.glob("*OBS-*.md")))

    def test_the_helper_refuses_directly_and_admits_the_empty_prefix(self):
        self.assertIsNone(SS.assert_unprefixed_tree(self.root))
        self._prefix()
        with self.assertRaises(EnvironmentError) as caught:
            SS.assert_unprefixed_tree(self.root)
        self.assertIn("artifact_prefix", str(caught.exception))

    def test_every_spelling_of_a_prefix_refuses_and_the_empty_ones_do_not(self):
        """The guard's reach is the reader's regex, so the spellings are the
        contract. A prefix the reader misses is a prefix the lane treats as
        absent, which is the failure mode this whole refusal exists to stop."""
        refusing = ('docs_dir: bionic\nartifact_prefix: "CRX"\n',
                    "docs_dir: bionic\nartifact_prefix: CRX\n",
                    "docs_dir: bionic\nartifact_prefix: 'CRX'\n",
                    "docs_dir: bionic\nartifact_prefix:    CRX   # ours\n")
        for body in refusing:
            with self.subTest(body=body):
                (self.root / ".bionic.yml").write_text(body, encoding="utf-8")
                with self.assertRaises(EnvironmentError):
                    SS.assert_unprefixed_tree(self.root)
        admitting = ('docs_dir: bionic\nartifact_prefix: ""\n',
                     "docs_dir: bionic\n",
                     "docs_dir: bionic\n# artifact_prefix: CRX\n")
        for body in admitting:
            with self.subTest(body=body):
                (self.root / ".bionic.yml").write_text(body, encoding="utf-8")
                self.assertIsNone(SS.assert_unprefixed_tree(self.root))

    def test_a_prefix_in_the_legacy_crux_config_refuses_too(self):
        """`bionic_config` reads a legacy `.crux` as a config source, so a
        prefix declared only there is a real prefix. Scanned because a prefix
        this reader misses is one the lane would treat as absent."""
        (self.root / ".crux").write_text("docs_dir: bionic\nartifact_prefix: CRX\n",
                                         encoding="utf-8")
        with self.assertRaises(EnvironmentError):
            SS.assert_unprefixed_tree(self.root)

    def test_the_reason_the_refusal_exists_still_holds(self):
        """The tripwire on the remedy. If this ever fails because both survey
        readers were made prefix-aware, the refusal above may be replaced — but
        only together with the sign-off's `OBS-{n:04d}` allocation and every
        remaining `OBS-*.md` glob, which is the whole reason it was not
        attempted here."""
        import summaries_projection as _sp
        from crux.arch.recover import read_recorded_observations
        (self.obs / "CRX-OBS-0001-live.md").write_text(
            "---\nid: CRX-OBS-0001\nanchor_id: " + self.a1 + "\n"
            "status: ratified\ngoverns:\n  - rule: A live claim\n---\n\nbody\n",
            encoding="utf-8")
        self.assertEqual(SS.record_paths(self.obs), {},
                         "record_paths now sees a prefixed record")
        self.assertEqual(read_recorded_observations(self.obs), {},
                         "read_recorded_observations now sees a prefixed record")
        self.assertEqual([p.name for p in _sp.observation_paths(self.obs)],
                         ["CRX-OBS-0001-live.md"],
                         "the summaries reader is the one that sees it")
