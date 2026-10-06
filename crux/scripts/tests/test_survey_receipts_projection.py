"""Cell 17: the per-batch receipts join the summaries input domain.

ADR-0098 clause 2 and docs/AGENTS.md §17.5 row 17 — while a batch is below S9,
`summarize-adrs.py` and `compile-doctrine.py` refuse, write nothing, and say
why. That is leg 2 of the atomic-publish resolution: a multi-file promote
cannot make postcondition (a) true by write ordering, so it is enforced at the
READER.

**Exit 2 is never the assertion.** Every environment failure in this codebase
is also exit 2 — a missing interpreter, an unreadable tree, a bad flag. Each
refusal test here asserts the SPECIFIC stderr reason, and each is paired with a
control run that exits 0 and writes a projection carrying a NON-NULL
`survey_receipts_sha256`. Without that pair, a test that merely broke the
fixture would read as a working refusal.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
SIGNOFF = SCRIPTS / "signoff-survey.py"
SCAFFOLD = SCRIPTS / "scaffold-survey-sheet.py"
SUMMARIZE = SCRIPTS / "summarize-adrs.py"
DOCTRINE = SCRIPTS / "compile-doctrine.py"
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
import summaries_projection as sp  # noqa: E402
from crux.arch.recover import StateFile, candidate_id  # noqa: E402

DATE = "2026-08-30"

MANIFEST = """schema_version: 5
concerns_enabled: [adrs, observations, arch]

observation:
  next_number: 1
  stale_days: 90
"""


class SurveyReceiptProjectionTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.tree = self.root / "bionic"
        self.obs = self.tree / "observations"
        self.adrs = self.tree / "adrs"
        self.obs.mkdir(parents=True)
        self.adrs.mkdir(parents=True)
        (self.root / ".bionic.yml").write_text("docs_dir: bionic\n", encoding="utf-8")
        (self.tree / "manifest.yml").write_text(MANIFEST, encoding="utf-8")
        (self.tree / "log.md").write_text("# Log\n", encoding="utf-8")
        (self.root / "src").mkdir()
        (self.root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
        self.state = StateFile(self.tree / "arch" / "_recovered" / "state.yml")
        self.a1 = candidate_id("external-dependency", "httpx")
        self.state.rows[self.a1] = {
            "id": self.a1, "anchor_kind": "external-dependency",
            "canonical_anchor": "httpx", "state": "observed",
            "rule": "The gateway depends on httpx",
            "evidence": ["src/a.py:1-1"], "domain": "runtime"}
        self.state.save()

    # ── fixtures ───────────────────────────────────────────────────────────

    def _scaffold_and_sign(self):
        proc = subprocess.run(
            [sys.executable, str(SCAFFOLD), "--repo-root", str(self.root),
             "--date", DATE], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        bid = json.loads(proc.stdout)["batch_id"]
        path = self.obs / f"survey-{bid}.yml"
        sheet = SS.read_sheet(path)
        for row in sheet["rows"]:
            row["verdict"] = "ratify"
            row["rationale"] = "Reviewed."
        SS.write_sheet(path, sheet, contained_under=self.tree)
        return bid

    def _publish(self):
        bid = self._scaffold_and_sign()
        proc = subprocess.run(
            [sys.executable, str(SIGNOFF), "--repo-root", str(self.root),
             "--date", DATE, "--batch", bid], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return bid

    def _stop_at(self, state):
        bid = self._scaffold_and_sign()
        ctx = SO.make_context(self.root, None, bid, DATE)
        paths = SS.receipt_paths(self.obs, bid)
        for _ in range(10):
            if SS.batch_state(paths["receipt"], self.obs) == state:
                return bid
            SO.advance(ctx)
        self.fail(f"never reached {state}")

    def _summarize(self, *extra):
        return subprocess.run(
            [sys.executable, str(SUMMARIZE), "--repo-root", str(self.root), *extra],
            capture_output=True, text=True)

    def _compile(self, *extra):
        return subprocess.run(
            [sys.executable, str(DOCTRINE), "--repo-root", str(self.root), *extra],
            capture_output=True, text=True)

    def _meta(self):
        return json.loads((self.adrs / "summaries" / "_meta.json")
                          .read_text(encoding="utf-8"))

    # ── the control: a complete batch renders ──────────────────────────────

    def test_control_a_complete_batch_renders_with_a_non_null_digest(self):
        """The positive control every refusal below is paired with. It is not
        optional decoration: exit 2 alone proves nothing, so each refusal is
        read against this run's exit 0 and non-null digest."""
        self._publish()
        proc = self._summarize()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        meta = self._meta()
        self.assertIsNotNone(meta["survey_receipts_sha256"])
        self.assertRegex(meta["survey_receipts_sha256"], r"^[0-9a-f]{64}$")
        self.assertIn("survey-receipts", meta["input_domain"])
        # Hard literal, plus the superseded value asserted absent.
        self.assertEqual(meta["schema"], "5")
        self.assertNotEqual(meta["schema"], "4")
        self.assertEqual(self._compile().returncode, 0)

    def test_a_tree_with_no_receipts_renders_with_a_null_digest(self):
        proc = self._summarize()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIsNone(self._meta()["survey_receipts_sha256"])

    def test_the_digest_moves_when_a_receipt_changes(self):
        self._publish()
        self.assertEqual(self._summarize().returncode, 0)
        first = self._meta()["survey_receipts_sha256"]
        receipt = next((self.obs / "_surveys").glob("*/receipt.yml"))
        receipt.write_text(receipt.read_text(encoding="utf-8")
                           .replace('domain: "runtime"', 'domain: "storage"'),
                           encoding="utf-8")
        self.assertEqual(self._summarize().returncode, 0)
        self.assertNotEqual(self._meta()["survey_receipts_sha256"], first)

    # ── the refusals, each with a content discriminator ────────────────────

    def _refuses_with(self, proc, needle):
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn(needle, proc.stderr,
                      "exit 2 alone is not the assertion — the stderr reason is")

    def test_an_incomplete_receipt_refuses_both_regenerators_by_name(self):
        bid = self._stop_at("S1")
        self._refuses_with(self._summarize(), bid)
        self._refuses_with(self._summarize(), "below S9")
        self._refuses_with(self._compile(), "below S9")
        # Positive control: completing the batch renders with a real digest.
        proc = subprocess.run(
            [sys.executable, str(SIGNOFF), "--repo-root", str(self.root),
             "--date", DATE, "--batch", bid], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._summarize().returncode, 0)
        self.assertIsNotNone(self._meta()["survey_receipts_sha256"])

    def test_a_partially_promoted_batch_refuses(self):
        """Cell 10's window: the receipt is complete, records are promoted, the
        index is stale. The projection refuses to render it."""
        bid = self._stop_at("S4")
        self._refuses_with(self._summarize(), "below S9")
        proc = subprocess.run(
            [sys.executable, str(SIGNOFF), "--repo-root", str(self.root),
             "--date", DATE, "--batch", bid], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._summarize().returncode, 0)

    def test_the_dry_run_refuses_identically_and_writes_nothing(self):
        bid = self._stop_at("S1")
        summaries = self.adrs / "summaries"
        self._refuses_with(self._summarize("--dry-run"), "below S9")
        self.assertFalse((summaries / "_meta.json").exists(),
                         "--dry-run wrote a projection while refusing")
        # Control: after the batch completes, --dry-run reports clean drift
        # against the projection the write mode then produces.
        subprocess.run([sys.executable, str(SIGNOFF), "--repo-root",
                        str(self.root), "--date", DATE, "--batch", bid],
                       capture_output=True, text=True)
        self.assertEqual(self._summarize().returncode, 0)
        self.assertEqual(self._summarize("--dry-run").returncode, 0)

    def test_an_unreadable_receipt_refuses_naming_the_receipt(self):
        self._publish()
        receipt = next((self.obs / "_surveys").glob("*/receipt.yml"))
        receipt.write_text("digest: [\n unterminated", encoding="utf-8")
        proc = self._summarize()
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn("receipt", proc.stderr.lower())
        # Control: no receipt at all is not a refusal.
        receipt.unlink()
        self.assertEqual(self._summarize().returncode, 0)

    # ── the module-level surface ───────────────────────────────────────────

    def test_survey_receipts_join_the_known_input_domain(self):
        self.assertIn("survey-receipts", sp.INPUT_DOMAIN_KNOWN)

    def test_survey_receipt_paths_finds_every_receipt_and_no_sheet(self):
        self._publish()
        found = sp.survey_receipt_paths(self.obs)
        self.assertEqual([p.name for p in found], ["receipt.yml"])
        # Control: the sheet beside it is NOT in the input domain — only the
        # receipt is a declared source.
        self.assertTrue(found[0].with_name("sheet.yml").is_file())

    def test_survey_receipts_problem_is_none_on_a_healthy_tree(self):
        self._publish()
        manifest = sp.read_manifest(self.root)
        self.assertIsNone(sp.survey_receipts_problem(self.root, manifest))
        self.assertIsNone(sp.observations_source_problem(self.root, manifest))

    def test_survey_receipts_problem_names_the_batch_and_its_state(self):
        bid = self._stop_at("S3")
        manifest = sp.read_manifest(self.root)
        problem = sp.survey_receipts_problem(self.root, manifest)
        self.assertIsNotNone(problem)
        self.assertIn(bid, problem)
        self.assertIn("S3", problem)
        # And the observations leg carries it, which is what makes the
        # regenerators refuse rather than render.
        self.assertIn(bid, sp.observations_source_problem(self.root, manifest))


if __name__ == "__main__":
    unittest.main()
