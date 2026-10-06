"""Postcondition tests for batch ratification — the write set, the equivalence
with the per-record path, and the corpus-mined publish.

Four questions the rest of the survey suite does not answer:

  * **Equivalence.** The premise of batch ratification is that one signed
    sheet is N individual ratifications under one signature. The oracle here
    runs the SHIPPED code both ways — one batch of N, and N sequential
    single-row batches into an identically seeded tree — and compares what
    each published. An in-test emulator of the per-record path would re-encode
    the sign-off and then agree with itself.
  * **The write set.** Which candidate a row means when a plain candidate and
    a successor sit on one anchor, and what the batch does to the predecessor
    it retires.
  * **The corpus publish.** `test_a_corpus_mined_batch_publishes_records_and_
    check_observations_sees_them` drives the frozen rubygems.org candidate set
    end to end and asserts the checker counts the records the batch wrote.
  * **The miner's domain contract.** A candidate mined without a `domain`
    scaffolds cleanly and then refuses every row of the batch, one human step
    after the mistake.

**On the verdict cells in this file — read before copying anything out of it.**
Every `verdict`, `domain` and `rationale` written here is SYNTHETIC TEST DATA,
authored against a throw-away tree under `tempfile` and deleted with that tree.
This is a test of the write protocol, not a ratification: no sheet, receipt or
record written by this file ever lands in a tree anyone reads as ratified
truth. The rule that a machine may not author the verdict, domain or rationale
of a sheet publishing into a real tree stands unchanged, and `_assert_hermetic`
below is its mechanical guard — every test asserts its root is under `tempfile`
before it writes, and no test writes under this repository's own tree or under
the corpus cache.

**These numbers are not the corpus measurement.** The corpus survey's
`records_written: 0` records that nothing was published against a corpus tree,
because the signing act is a human's. The `records >= 5` this file asserts is
a temporary tree's count. The two answer different questions, and neither one
may be quoted for the other.
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent                                  # crux/scripts
REPO_ROOT = HERE.parents[2]                            # this checkout
SIGNOFF = SCRIPTS / "signoff-survey.py"
SCAFFOLD = SCRIPTS / "scaffold-survey-sheet.py"
CORPUS_FIXTURES = HERE / "fixtures" / "survey-corpus"
SURVEY_CORPUS = HERE / "arch-corpus" / "survey_corpus.py"
MINER_SKILL = REPO_ROOT / "crux" / "skills" / "recover-decisions" / "SKILL.md"

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
CHECK = _load("check_observations")
SP = _load("summaries_projection")
from crux.arch.recover import StateFile, candidate_id  # noqa: E402

try:
    import yaml
    HAVE_YAML = True
except ImportError:  # pragma: no cover - the suite runs under uv
    HAVE_YAML = False

DATE = "2026-08-30"

MANIFEST = """schema_version: 5
concerns_enabled: [adrs, observations, arch]

observation:
  next_number: 1
  stale_days: 90
  survey_stub_days: 1
"""

# Resolved once: `TemporaryDirectory` hands back `/var/folders/...` on macOS
# and the same path resolves to `/private/var/folders/...`, so the containment
# check compares two resolved paths or it compares nothing.
TMP_ROOT = Path(tempfile.gettempdir()).resolve()
FORBIDDEN_ROOTS = (
    (REPO_ROOT / "bionic").resolve(),
    (HERE / "arch-corpus" / ".cache").resolve(),
)

_BATCH_REF = re.compile(r"batch SVY-\d{4}")


def _assert_hermetic(tc: unittest.TestCase, root: Path) -> Path:
    """Refuse to write a survey anywhere but a temporary tree.

    The binding constraint on this file. A sign-off publishes records, an
    index row, a log op and a journal hook; run against this repository's own
    tree it would publish machine-authored verdicts into the dogfood tree, and
    run against a corpus clone it would write into somebody else's
    repository. Both are checked, and the positive leg — the root IS under
    `tempfile` — is checked first so a path that is neither still fails."""
    resolved = Path(root).resolve()
    tc.assertTrue(
        str(resolved).startswith(str(TMP_ROOT) + os.sep),
        f"refusing to run a survey at {resolved}: a test tree lives under "
        f"{TMP_ROOT}, and these verdict cells are synthetic test data that may "
        "not reach any tree a reader would take as ratified truth")
    for bad in FORBIDDEN_ROOTS:
        tc.assertFalse(
            resolved == bad or bad in resolved.parents,
            f"refusing to run a survey at {resolved}: it is under {bad}")
    return resolved


def _fingerprint(paths) -> list:
    """`(exists, bytes-or-None)` per path — the discriminator a refusal test
    needs when the path it guards may legitimately exist already. This
    checkout HAS a `bionic/`, so "absent afterwards" is not the postcondition;
    "unchanged afterwards" is, and `bootstrap_tree` would replace
    `manifest.yml` with its own minimal body.

    Staged-artifact safe without `require_dev_surface`: `bionic/` is dev-only
    and absent from the staged tree, where every path fingerprints as
    `(False, None)` before and after — the comparison still holds, so the
    guard is exercised on the legs that do exist rather than skipped."""
    out = []
    for p in paths:
        out.append((p.exists(), p.read_bytes() if p.is_file() else None))
    return out


def _records_snapshot(obs: Path) -> dict[str, str]:
    """`{filename: sha256}` over the published records, with the batch id
    folded out.

    The record body names the batch that ratified it (`Ratified in batch
    SVY-0001.`), and the batch id is the ONE thing that differs by
    construction between one batch of N and N batches of one. Folding it is
    what leaves the comparison about the claim; everything else — title,
    rule, evidence, scope, domain, handle, anchor — stays byte-exact."""
    out = {}
    for page in sorted(obs.glob("OBS-*.md")):
        data = _BATCH_REF.sub("batch <SVY>", page.read_text(encoding="utf-8"))
        out[page.name] = hashlib.sha256(data.encode("utf-8")).hexdigest()
    return out


def _index_rows(obs: Path) -> set[str]:
    """The index's record rows as a SET.

    `build_index` inserts a batch's rows sorted at the top of the table body,
    so N rows inserted at once sit in ascending id order while N inserted one
    at a time sit in descending order. The rows are the postcondition; their
    order is an artifact of where the insert lands."""
    path = obs / "index.md"
    if not path.is_file():
        return set()
    return {line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.startswith("| OBS-")}


class HermeticGuardTests(unittest.TestCase):
    """`_assert_hermetic` refuses, so the rest of this file's use of it means
    something.

    Every other test in this file calls the guard on a path that is already
    under `tempfile`, so the guard passes in every one of them whether it
    checks anything or not. These three cases are the only place it is shown
    firing."""

    def test_a_root_outside_tempfile_is_refused(self):
        # `/etc` is outside `tempfile` in every world. The checkout is outside
        # it only when this file runs from a real checkout: `sync.sh` stages
        # the tree UNDER `tempfile` to gate it, and there the repo-root legs
        # would assert a premise that is false, so they run only where it holds.
        roots = [Path("/etc")]
        if not str(REPO_ROOT.resolve()).startswith(str(TMP_ROOT) + os.sep):
            roots += [REPO_ROOT, REPO_ROOT / "bionic"]
        for root in roots:
            with self.subTest(root=str(root)):
                with self.assertRaises(AssertionError) as ctx:
                    _assert_hermetic(self, root)
                self.assertIn("synthetic test data", str(ctx.exception))

    def test_a_forbidden_root_under_tempfile_is_refused(self):
        """The second leg on its own. Nothing under `tempfile` is under a real
        forbidden root, so the leg is unreachable without redirecting one —
        without this the first leg would be doing all the work and a broken
        containment check would never show."""
        with tempfile.TemporaryDirectory() as tmp:
            banned = Path(tmp).resolve() / "cache"
            (banned / "clone").mkdir(parents=True)
            global FORBIDDEN_ROOTS
            original = FORBIDDEN_ROOTS
            FORBIDDEN_ROOTS = (banned,)
            try:
                with self.assertRaises(AssertionError) as ctx:
                    _assert_hermetic(self, banned / "clone")
                self.assertIn(str(banned), str(ctx.exception))
                # Control: a sibling outside the banned root passes, so the
                # refusal is containment and not a function that refuses all.
                sibling = Path(tmp).resolve() / "elsewhere"
                sibling.mkdir()
                self.assertEqual(_assert_hermetic(self, sibling), sibling)
            finally:
                FORBIDDEN_ROOTS = original

    def test_a_temporary_root_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            self.assertEqual(_assert_hermetic(self, root), root.resolve())


class _TreeCase(unittest.TestCase):
    """One or more hermetic trees, each with a candidate state file."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    # ── tree construction ────────────────────────────────────────────────

    def _tree(self, name: str = "repo") -> Path:
        """A fresh repository root with an empty observations concern."""
        root = self.base / name
        _assert_hermetic(self, root)
        obs = root / "bionic" / "observations"
        obs.mkdir(parents=True)
        (root / ".bionic.yml").write_text("docs_dir: bionic\n", encoding="utf-8")
        (root / "bionic" / "manifest.yml").write_text(MANIFEST, encoding="utf-8")
        (root / "bionic" / "log.md").write_text("# Log\n", encoding="utf-8")
        (root / "src").mkdir(exist_ok=True)
        return root

    def _obs(self, root: Path) -> Path:
        return root / "bionic" / "observations"

    def _state_path(self, root: Path) -> Path:
        return root / "bionic" / "arch" / "_recovered" / "state.yml"

    def _add_candidate(self, root: Path, spec: dict) -> str:
        """Append one candidate to the tree's state file, on disk.

        Re-read before every write, because the sign-off rewrites the file
        when it disposes a batch — an in-memory `StateFile` held across a
        publish would write the pre-publish states back."""
        state = StateFile(self._state_path(root))
        cid = spec["id"]
        state.rows[cid] = dict(spec)
        state.save()
        return cid

    def _source(self, root: Path, rel: str) -> None:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text("x = 1\n", encoding="utf-8")

    def _spec(self, root: Path, name: str, rule: str, domain: str,
              rel: str, **extra) -> dict:
        self._source(root, rel)
        cid = candidate_id("external-dependency", name)
        spec = {"id": cid, "anchor_kind": "external-dependency",
                "canonical_anchor": name, "rule": rule,
                "evidence": [f"{rel}:1-1"], "state": "observed",
                "domain": domain}
        spec.update(extra)
        return spec

    # ── driving the two shipped commands ──────────────────────────────────

    def _scaffold(self, root: Path, expect: int = 0) -> dict:
        _assert_hermetic(self, root)
        proc = subprocess.run(
            [sys.executable, str(SCAFFOLD), "--repo-root", str(root),
             "--date", DATE], capture_output=True, text=True)
        self.assertEqual(proc.returncode, expect, proc.stdout + proc.stderr)
        return json.loads(proc.stdout)

    def _author(self, root: Path, batch_id: str, verdicts=None, domains=None):
        """Author the human cells. SYNTHETIC — see the module docstring."""
        path = self._obs(root) / f"survey-{batch_id}.yml"
        sheet = SS.read_sheet(path)
        for row in sheet["rows"]:
            row["verdict"] = (verdicts or {}).get(row["anchor_id"], "ratify")
            row["domain"] = (domains or {}).get(row["anchor_id"], "")
            row["rationale"] = "Reviewed against the cited lines."
        SS.write_sheet(path, sheet, contained_under=root / "bionic")
        return sheet

    def _signoff(self, root: Path, batch_id: str | None = None, *extra):
        _assert_hermetic(self, root)
        argv = [sys.executable, str(SIGNOFF), "--repo-root", str(root),
                "--date", DATE]
        if batch_id:
            argv += ["--batch", batch_id]
        return subprocess.run(argv + list(extra), capture_output=True, text=True)

    def _publish(self, root: Path, verdicts=None, domains=None) -> str:
        batch_id = self._scaffold(root)["batch_id"]
        self._author(root, batch_id, verdicts, domains)
        proc = self._signoff(root, batch_id)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return batch_id

    def _counter(self, root: Path, key: str) -> int:
        text = (root / "bionic" / "manifest.yml").read_text(encoding="utf-8")
        return SS.read_counter(yaml.safe_load(text), "observation", key, -1)

    def _check(self, root: Path) -> dict:
        return CHECK.check(root, "bionic")


# ── unit 4: the equivalence oracle and the human-act count ──────────────────

SIX = [
    ("httpx", "The gateway depends on httpx for every outbound call",
     "runtime", "src/gateway.py"),
    ("pyyaml", "The loader pins pyyaml exactly", "runtime", "src/loader.py"),
    ("flipper", "Feature flags are Flipper", "feature-flags", "src/flags.py"),
    ("rack-attack", "Request throttling is Rack::Attack", "security",
     "src/throttle.py"),
    ("good-job", "Background work runs on GoodJob", "background-jobs",
     "src/jobs.py"),
    ("blazer", "Ad-hoc analytics queries are served by Blazer", "analytics",
     "src/analytics.py"),
]


@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under uv)")
class BatchEquivalenceTests(_TreeCase):
    """One signed sheet against N single-row sign-offs of the same claims."""

    def _specs(self, root: Path) -> list[dict]:
        return [self._spec(root, name, rule, domain, rel)
                for name, rule, domain, rel in SIX]

    def _batch_tree(self, domains=None, name: str = "batch") -> Path:
        """Every candidate seeded, then ONE sheet signed."""
        root = self._tree(name)
        for spec in self._specs(root):
            self._add_candidate(root, spec)
        self._publish(root, domains=domains)
        return root

    def _sequential_tree(self) -> Path:
        """The same claims, one candidate at a time, one batch each.

        Seeded in ANCHOR order because record ids are allocated in anchor
        order inside a batch: adding them in declaration order would allocate
        a different id to each claim and turn an equivalence test into a
        comparison of two id assignments."""
        root = self._tree("sequential")
        for spec in sorted(self._specs(root), key=lambda s: s["id"]):
            self._add_candidate(root, spec)
            self._publish(root)
        return root

    def test_one_signed_sheet_produces_the_same_record_set_as_n_single_row_batches(self):
        batch = self._batch_tree()
        sequential = self._sequential_tree()

        self.assertEqual(len(self._records_of(batch)), len(SIX))
        self.assertEqual(_records_snapshot(self._obs(batch)),
                         _records_snapshot(self._obs(sequential)),
                         "one batch of N and N batches of one published "
                         "different record bytes")
        self.assertEqual(_index_rows(self._obs(batch)),
                         _index_rows(self._obs(sequential)))
        self.assertEqual(self._counter(batch, "next_number"),
                         self._counter(sequential, "next_number"))

        # Positive control. Without it this test passes just as well against a
        # comparison that is blind to the records — one domain cell moved must
        # separate the two trees.
        first_anchor = sorted(s["id"] for s in self._specs(batch))[0]
        moved = self._batch_tree(domains={first_anchor: "moved"}, name="moved")
        self.assertNotEqual(_records_snapshot(self._obs(moved)),
                            _records_snapshot(self._obs(sequential)),
                            "the comparison cannot see a changed domain at all")

    def _records_of(self, root: Path) -> list[str]:
        return sorted(p.name for p in self._obs(root).glob("OBS-*.md"))

    def test_one_signed_sheet_costs_one_human_act_against_n(self):
        """The number batch ratification exists to move.

        A human act is a signature over a sheet, and every signature leaves
        exactly one receipt. Six claims cost six receipts one at a time and
        one receipt in a batch."""
        self.assertGreaterEqual(len(SIX), 5)
        batch = self._batch_tree()
        sequential = self._sequential_tree()

        batch_receipts = sorted(
            (self._obs(batch) / "_surveys").rglob("receipt.yml"))
        seq_receipts = sorted(
            (self._obs(sequential) / "_surveys").rglob("receipt.yml"))

        self.assertEqual(len(batch_receipts), 1)
        self.assertEqual(len(seq_receipts), len(SIX))
        self.assertEqual(len(self._records_of(batch)), len(SIX))
        self.assertEqual(self._records_of(batch), self._records_of(sequential))

        # The batch ids are distinct within one tree, which is the property
        # `next_survey_number` carries — ids repeat ACROSS trees by design.
        ids = [p.parent.name for p in seq_receipts]
        self.assertEqual(len(set(ids)), len(SIX), ids)


# ── unit 4: the write set ───────────────────────────────────────────────────

@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under uv)")
class WriteSetTests(_TreeCase):
    """What one row plans, and which candidate a row means."""

    def setUp(self):
        super().setUp()
        self.root = self._tree()
        self.spec = self._spec(self.root, "httpx",
                               "The gateway depends on httpx", "runtime",
                               "src/gateway.py")
        self.anchor = self._add_candidate(self.root, self.spec)

    def _sheet(self, verdict="ratify", domain="", rationale="Reviewed."):
        return {"config_version": SS.CONFIG_VERSION, "batch_id": "SVY-0001",
                "scaffold_provenance": {"tool": "scaffold-survey-sheet.py",
                                        "scaffolded": DATE,
                                        "state_file": "arch/_recovered/state.yml",
                                        "candidates": 1},
                "rows": [{"anchor_id": self.anchor,
                          "proposed_domain": self.spec["domain"],
                          "verdict": verdict, "domain": domain,
                          "rationale": rationale}]}

    def _plan(self, recorded=None, rows=None):
        return SS.build_plan(self._sheet(), rows or StateFile(
            self._state_path(self.root)).rows, recorded or {},
            root=self.root, obs_dir=self._obs(self.root))

    def _record_file(self, oid="OBS-0001", status="ratified"):
        (self._obs(self.root) / f"{oid}-x.md").write_text(
            f'---\nid: {oid}\nstatus: {status}\n'
            f'anchor_id: "{self.anchor}"\n'
            'evidence: ["src/gateway.py:1-1"]\ngoverns: []\n---\n\nbody\n',
            encoding="utf-8")

    def test_a_successor_and_a_plain_candidate_on_one_anchor_plan_one_record(self):
        """Two candidates, one anchor, one row — the row means the SUCCESSOR.

        `emit_candidate` opens a successor only because the recorded claim
        changed, so the successor is the newer proposal and the plain
        candidate is the one the record already covers. The postcondition is
        the count as much as the choice: an anchor never plans two records,
        because two live records on one anchor is a CHK-OBS-ANCHOR finding."""
        self._record_file()
        rows = StateFile(self._state_path(self.root)).rows
        sid = f"{self.anchor}+1"
        rows[sid] = dict(self.spec, id=sid, anchor_id=self.anchor,
                         predecessor_id="OBS-0001",
                         rule="The gateway depends on httpx and h2")
        recorded = {self.anchor: {"id": "OBS-0001", "status": "ratified"}}

        plan = self._plan(recorded=recorded, rows=rows)
        self.assertEqual(len(plan), 1, plan)
        self.assertEqual(plan[0]["candidate_id"], sid)
        self.assertIn("httpx and h2", plan[0]["rule"])
        self.assertTrue(plan[0]["retires"].endswith("OBS-0001-x.md"))

        # Positive control: with the successor gone the SAME sheet plans the
        # plain candidate — so the assertion above is reading the choice and
        # not a fixture that only ever had one candidate.
        del rows[sid]
        plain = SS.build_plan(self._sheet(), rows, {}, root=self.root,
                              obs_dir=self._obs(self.root))
        self.assertEqual(plain[0]["candidate_id"], self.anchor)
        self.assertEqual(plain[0]["retires"], "")

    def test_a_ratify_row_carries_the_claim_and_a_non_ratify_row_carries_none(self):
        """The write set's shape: a `ratify` row plans a body, a `reject` row
        plans a disposition and nothing else."""
        [entry] = self._plan()
        self.assertEqual(entry["verdict"], "ratify")
        self.assertEqual(entry["evidence"], ["src/gateway.py:1-1"])
        self.assertEqual(entry["scope"], "src/gateway.py")
        self.assertEqual(entry["domain"], "runtime")
        self.assertTrue(entry["slug"])

        sheet = self._sheet(verdict="reject")
        [rejected] = SS.build_plan(
            sheet, StateFile(self._state_path(self.root)).rows, {},
            root=self.root, obs_dir=self._obs(self.root))
        self.assertEqual(rejected["verdict"], "reject")
        self.assertNotIn("rule", rejected)
        self.assertNotIn("slug", rejected)

    def test_a_batch_retires_its_predecessor_and_publishes_its_successor(self):
        """End to end through the shipped sign-off: the predecessor's record
        flips to `retired`, the successor's record is `ratified`, and the
        index carries both."""
        self._publish(self.root)
        first = sorted(self._obs(self.root).glob("OBS-*.md"))[0]

        state = StateFile(self._state_path(self.root))
        sid = f"{self.anchor}+1"
        state.rows[sid] = dict(self.spec, id=sid, anchor_id=self.anchor,
                               predecessor_id=first.stem.split("-")[0] + "-"
                               + first.stem.split("-")[1],
                               rule="The gateway depends on httpx and h2")
        state.save()
        self._publish(self.root)

        pages = sorted(self._obs(self.root).glob("OBS-*.md"))
        self.assertEqual(len(pages), 2, [p.name for p in pages])
        statuses = {p.name: yaml.safe_load(
            re.match(r"^---\n(.*?)\n---\n", p.read_text(encoding="utf-8"),
                     re.S).group(1))["status"] for p in pages}
        self.assertEqual(sorted(statuses.values()), ["ratified", "retired"])
        self.assertEqual(self._check(self.root)["broken"], [])


# ── unit 6: the claim that moved, and the corpus-cache containment guard ────

@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under uv)")
class MovedClaimTests(_TreeCase):
    """The sheet is untouched and the CANDIDATE moves under it."""

    def _seed(self):
        root = self._tree()
        spec = self._spec(root, "httpx", "The gateway depends on httpx",
                          "runtime", "src/gateway.py")
        self._add_candidate(root, spec)
        self._publish(root)                        # OBS-0001 on the anchor
        first = sorted(self._obs(root).glob("OBS-*.md"))[0]
        pred = re.match(r"^(OBS-\d{4})", first.stem).group(1)
        return root, spec, pred

    def _open_successor(self, root, spec, pred, rule, digest_of):
        sid = f"{spec['id']}+1"
        state = StateFile(self._state_path(root))
        state.rows[sid] = dict(spec, id=sid, anchor_id=spec["id"],
                               predecessor_id=pred, rule=rule,
                               claim_digest=SS.claim_digest(
                                   digest_of, spec["evidence"]))
        state.save()
        return sid

    def test_a_moved_rule_refuses_and_names_the_sheet_it_diverged_from(self):
        """The candidate moves under an untouched sheet.

        The sheet now CARRIES the rule (seeded, inside the digest), so the
        divergence is stated as what it is: the cell the human read and the
        candidate the record would be built from say different things. That
        refusal covers the plain lane as well as the successor lane — the hole
        this test's predecessor explicitly said it could not speak for."""
        root, spec, pred = self._seed()
        rule = "The gateway depends on httpx and h2"
        self._open_successor(root, spec, pred, rule, digest_of=rule)

        batch_id = self._scaffold(root)["batch_id"]
        self._author(root, batch_id)
        before = sorted(p.name for p in self._obs(root).glob("OBS-*.md"))

        # The claim moves AFTER the sheet is signed and BEFORE the sign-off.
        state = StateFile(self._state_path(root))
        state.rows[f"{spec['id']}+1"]["rule"] = (
            "The gateway depends on httpx, h2 and anyio")
        state.save()

        proc = self._signoff(root, batch_id)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("no longer matches the candidate", proc.stdout)
        self.assertEqual(sorted(p.name for p in self._obs(root).glob("OBS-*.md")),
                         before, "a refused batch published a record")

        # Positive control: the same fixture, with the candidate holding the
        # claim the sheet was seeded from, publishes. Without it the assertion
        # above holds for a sign-off that refuses everything.
        state = StateFile(self._state_path(root))
        row = state.rows[f"{spec['id']}+1"]
        row["rule"] = rule
        row["claim_digest"] = SS.claim_digest(row["rule"], row["evidence"])
        state.save()
        proc = self._signoff(root, batch_id)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(len(list(self._obs(root).glob("OBS-*.md"))), 2)

    def test_a_stale_claim_digest_still_voids_its_row(self):
        """The `claim_digest` leg ON ITS OWN.

        With the rule seeded onto the sheet, a moved rule is caught one check
        earlier — so the digest comparison would be shadowed and untested if
        this case did not isolate it. Here the rule is exactly what the sheet
        was seeded from and the RECORDED digest is the thing that disagrees,
        which is what a hand-edited state row looks like."""
        root, spec, pred = self._seed()
        rule = "The gateway depends on httpx and h2"
        sid = self._open_successor(root, spec, pred, rule, digest_of=rule)

        batch_id = self._scaffold(root)["batch_id"]
        self._author(root, batch_id)
        before = sorted(p.name for p in self._obs(root).glob("OBS-*.md"))

        state = StateFile(self._state_path(root))
        state.rows[sid]["claim_digest"] = SS.claim_digest(
            "a claim this candidate never carried", spec["evidence"])
        state.save()

        proc = self._signoff(root, batch_id)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("moved since it was mined", proc.stdout)
        self.assertEqual(sorted(p.name for p in self._obs(root).glob("OBS-*.md")),
                         before, "a refused batch published a record")

        # Positive control: the digest restored, the same batch publishes.
        state = StateFile(self._state_path(root))
        row = state.rows[sid]
        row["claim_digest"] = SS.claim_digest(row["rule"], row["evidence"])
        state.save()
        proc = self._signoff(root, batch_id)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(len(list(self._obs(root).glob("OBS-*.md"))), 2)


class _SurveyCorpusCase(unittest.TestCase):
    """The harness module, loaded once, with `CACHE_ROOT` redirected at a
    temporary directory.

    The redirection is what lets a write leg exercise `bootstrap_tree`'s and
    `teardown_tree`'s real paths without touching the real corpus cache (which
    a concurrent survey may be using), and it is undone after every test."""

    @classmethod
    def setUpClass(cls):
        if not SURVEY_CORPUS.is_file():  # pragma: no cover
            raise unittest.SkipTest(f"{SURVEY_CORPUS} absent")
        spec = importlib.util.spec_from_file_location(
            "survey_corpus_under_test", SURVEY_CORPUS)
        cls.SC = importlib.util.module_from_spec(spec)
        sys.modules["survey_corpus_under_test"] = cls.SC
        spec.loader.exec_module(cls.SC)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fake_cache = Path(self.tmp.name).resolve() / ".cache"
        self.fake_cache.mkdir()
        self._real_cache = self.SC.CACHE_ROOT
        self.SC.CACHE_ROOT = self.fake_cache
        self.addCleanup(setattr, self.SC, "CACHE_ROOT", self._real_cache)

    def _plant_tree(self, root: Path) -> tuple[Path, ...]:
        """A canary scratch tree at `root`, and the paths that watch it.

        A refusal test over an `rmtree` needs something to lose. `REPO_ROOT`
        brings its own tree, but a synthetic root is empty, and empty before
        equals empty after whether the guard fired or not."""
        (root / "bionic").mkdir(parents=True, exist_ok=True)
        (root / "bionic" / "manifest.yml").write_text(
            "canary: do not delete\n", encoding="utf-8")
        (root / ".bionic.yml").write_text("canary: do not delete\n",
                                          encoding="utf-8")
        return self._watched(root)

    @staticmethod
    def _watched(root: Path) -> tuple[Path, ...]:
        """The three paths a bootstrap writes and a teardown removes."""
        return (Path(root) / "bionic",
                Path(root) / "bionic" / "manifest.yml",
                Path(root) / ".bionic.yml")


class CorpusCacheContainmentTests(_SurveyCorpusCase):
    """`survey_corpus.assert_corpus_clone` — the guard that keeps a scratch
    tree out of this repository and out of everything that is not a clone.
    """

    def test_bootstrap_refuses_a_tree_root_outside_the_corpus_cache(self):
        """Four refusals, each named separately so a typo in one message
        cannot be mistaken for the guard working."""
        outside = Path(self.tmp.name).resolve() / "somewhere-else"
        outside.mkdir()
        deeper = self.fake_cache / "clone" / "nested"
        deeper.mkdir(parents=True)

        for root, why in ((REPO_ROOT, "this repository's own root"),
                          (self.fake_cache, "the cache directory itself"),
                          (outside, "a path outside the cache"),
                          (deeper, "a path deeper than a direct child")):
            with self.subTest(why=why):
                # This checkout HAS a `bionic/`, so "the tree is absent" is
                # not the postcondition — "the tree is unchanged" is. The
                # bootstrap's write would replace `manifest.yml` with its own
                # minimal body, so the manifest's bytes are the discriminator.
                watched = (Path(root) / "bionic",
                           Path(root) / "bionic" / "manifest.yml",
                           Path(root) / ".bionic.yml")
                before = _fingerprint(watched)
                with self.assertRaises(self.SC.ScratchTreeRefused):
                    self.SC.bootstrap_tree(root)
                self.assertEqual(
                    before, _fingerprint(watched),
                    f"a refused bootstrap wrote a scratch tree at {root}")

    def test_bootstrap_writes_a_tree_for_a_direct_child_of_the_cache(self):
        """The positive control for the test above: a well-formed clone path
        bootstraps, so the four refusals are the guard discriminating and not
        a function that refuses everything."""
        clone = self.fake_cache / "rubygems-org"
        clone.mkdir()
        tree = self.SC.bootstrap_tree(clone)
        self.assertTrue((tree / "manifest.yml").is_file())
        self.assertTrue((clone / ".bionic.yml").is_file())
        self.SC.teardown_tree(clone)
        self.assertFalse(tree.exists())

    def test_the_repository_root_is_refused_under_the_real_cache_too(self):
        """The redirection above must not be what produces the refusal: with
        `CACHE_ROOT` restored to the real one, this checkout is still
        refused."""
        self.SC.CACHE_ROOT = self._real_cache
        with self.assertRaises(self.SC.ScratchTreeRefused) as ctx:
            self.SC.assert_corpus_clone(REPO_ROOT)
        self.assertIn("own root", str(ctx.exception))

    def test_teardown_refuses_the_same_roots_the_bootstrap_refuses(self):
        """`teardown_tree` is the harness's EARLIEST-firing write path.

        `measure` calls it before `bootstrap_tree`, so on `--stage pre` the
        teardown's containment guard is the first thing between a mistyped
        `--repo` and an `rmtree`. The bootstrap's four refusals were tested and
        the teardown's were not, and the two do not share a call site — only a
        callee.

        Refusal is not the whole postcondition. `shutil.rmtree(...,
        ignore_errors=True)` raises nothing on its way through a tree, so each
        root is fingerprinted before and after: the assertion is that the tree
        is still there, not merely that an exception was raised."""
        outside = Path(self.tmp.name).resolve() / "somewhere-else"
        deeper = self.fake_cache / "clone" / "nested"
        for synthetic in (outside, deeper, self.fake_cache):
            self._plant_tree(synthetic)

        for root, why in ((REPO_ROOT, "this repository's own root"),
                          (self.fake_cache, "the cache directory itself"),
                          (outside, "a path outside the cache"),
                          (deeper, "a path deeper than a direct child")):
            with self.subTest(why=why):
                watched = self._watched(root)
                before = _fingerprint(watched)
                with self.assertRaises(self.SC.ScratchTreeRefused):
                    self.SC.teardown_tree(root)
                self.assertEqual(
                    before, _fingerprint(watched),
                    f"a refused teardown deleted a scratch tree at {root}")

        # Positive control: a well-formed clone DOES tear down, so the four
        # refusals are the guard discriminating and not a function that
        # refuses everything.
        clone = self.fake_cache / "rubygems-org"
        clone.mkdir()
        tree = self.SC.bootstrap_tree(clone)
        self.assertTrue((tree / "manifest.yml").is_file())
        self.SC.teardown_tree(clone)
        self.assertFalse(tree.exists())
        self.assertFalse((clone / ".bionic.yml").exists())


class CorpusTrackedPathTests(_SurveyCorpusCase):
    """`survey_corpus._refuse_tracked` — the leg that keeps the harness out of
    a clone's OWN tracked files.

    Every other test in this file bootstraps into a plain `tempfile`
    directory, where `git ls-files` exits 128 and the refusal branch is never
    reached: the guard could be deleted with the whole suite green. These
    tests build a real repository, commit the two paths the harness writes,
    and drive both directions."""

    def setUp(self):
        if shutil.which("git") is None:  # pragma: no cover
            self.skipTest("git is not on PATH")
        super().setUp()

    def _git(self, cwd: Path, *args: str) -> None:
        """git, with the user's config layers pointed at /dev/null.

        A global `core.hooksPath` or `commit.gpgsign` would otherwise run on
        this fixture's commit, and the fixture would fail for a reason that
        has nothing to do with the guard under test."""
        proc = subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, text=True,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                 "HOME": os.environ.get("HOME", ""),
                 "GIT_TERMINAL_PROMPT": "0",
                 "GIT_CONFIG_GLOBAL": os.devnull,
                 "GIT_CONFIG_SYSTEM": os.devnull})
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def _repo_tracking(self, clone: Path, rel: str, body: str) -> Path:
        """A real git repository at `clone` with `rel` committed."""
        clone.mkdir(parents=True)
        path = clone / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        self._git(clone, "init", "--quiet")
        self._git(clone, "add", rel)
        self._git(clone, "-c", "user.email=t@example.invalid",
                  "-c", "user.name=t", "commit", "--quiet", "-m", "seed")
        # The fixture is only worth anything if git is really tracking it —
        # `_refuse_tracked` asks git, so the test asks git too.
        self._git(clone, "ls-files", "--error-unmatch", rel)
        return clone

    def test_bootstrap_refuses_a_clone_that_tracks_a_path_it_would_write(self):
        """Both tracked paths, separately: the docs dir and the config.

        The harness writes into somebody else's repository, and a clone that
        tracks `.bionic.yml` (or a `bionic/`) would have its own file
        overwritten and then deleted at teardown. The bytes on disk are the
        postcondition."""
        for rel, body in ((".bionic.yml", "docs_dir: theirs\n"),
                          ("bionic/manifest.yml", "schema_version: '9'\n")):
            with self.subTest(tracked=rel):
                clone = self._repo_tracking(
                    self.fake_cache / f"tracks-{rel.replace('/', '-')}",
                    rel, body)
                watched = self._watched(clone)
                before = _fingerprint(watched)
                with self.assertRaises(self.SC.ScratchTreeRefused) as ctx:
                    self.SC.bootstrap_tree(clone)
                self.assertIn("git tracks", str(ctx.exception))
                self.assertIn(rel.split("/")[0], str(ctx.exception))
                self.assertEqual(
                    before, _fingerprint(watched),
                    "a refused bootstrap wrote over the clone's tracked file")

    def test_bootstrap_accepts_a_clone_that_tracks_neither_path(self):
        """The other direction, in a repository that IS a repository.

        Without this leg the refusal above holds just as well for a
        `_refuse_tracked` that raises on every git checkout. The clone carries
        an UNTRACKED `.bionic.yml` on disk, so the leg also pins which
        question is asked: `git ls-files`, not `path.exists()`."""
        clone = self._repo_tracking(self.fake_cache / "untracked",
                                    "README.md", "# a corpus clone\n")
        self.assertTrue((clone / ".git").is_dir())
        (clone / ".bionic.yml").write_text("stale: leftover\n",
                                           encoding="utf-8")

        tree = self.SC.bootstrap_tree(clone)
        self.assertTrue((tree / "manifest.yml").is_file())
        self.assertIn("docs_dir: bionic",
                      (clone / ".bionic.yml").read_text(encoding="utf-8"))
        self.SC.teardown_tree(clone)
        self.assertFalse(tree.exists())
        # The clone's own tracked file is untouched by all of it.
        self.assertEqual((clone / "README.md").read_text(encoding="utf-8"),
                         "# a corpus clone\n")


class CorpusDocsDirTests(_SurveyCorpusCase):
    """`--docs-dir` is joined onto the validated clone root, and nothing used
    to validate the component itself.

    `assert_corpus_clone` checks the ROOT and cannot see what is appended to
    it, so `--docs-dir ../../../../../../` walked out of a containment check
    that had already passed and computed an `rmtree` of this repository's
    root. The refusals are checked by an untouched canary and by path
    arithmetic: a test that proves a traversal by letting it run is a test
    that deletes something."""

    # Leaf, interior, ancestor and root, plus the two components that are
    # plain names and still wrong: `.` (the clone root) and `.git` (contained,
    # traversal-free, and the clone's repository).
    REFUSED = (
        ("nested/tree", "interior: a path rather than a name"),
        (".", "the clone root itself"),
        ("..", "ancestor: the cache directory"),
        ("../..", "ancestor: the directory above the cache"),
        ("../../../../../../", "root-ward: six levels of traversal"),
        ("../victim", "a sibling of the clone"),
        ("/etc", "absolute: pathlib's join adopts it whole"),
        (".git", "the clone's own git directory"),
        ("", "the empty component"),
        ("bionic/x", "a name with a separator glued on"),
        ("bionic\n", "the trailing newline `$` would admit"),
    )

    def test_bootstrap_and_teardown_refuse_a_docs_dir_that_is_not_a_name(self):
        clone = self.fake_cache / "rubygems-org"
        clone.mkdir()
        victim = self.fake_cache / "victim"
        victim.mkdir()
        (victim / "keep.md").write_text("canary: another clone\n",
                                        encoding="utf-8")
        git_dir = clone / ".git"
        git_dir.mkdir()
        (git_dir / "HEAD").write_text("ref: refs/heads/main\n",
                                      encoding="utf-8")

        for docs_dir, why in self.REFUSED:
            for fn in (self.SC.bootstrap_tree, self.SC.teardown_tree):
                with self.subTest(docs_dir=docs_dir, why=why,
                                  fn=fn.__name__):
                    with self.assertRaises(self.SC.ScratchTreeRefused) as ctx:
                        fn(clone, docs_dir)
                    self.assertIn("--docs-dir", str(ctx.exception))
                    self.assertIn(repr(docs_dir), str(ctx.exception))

        # Nothing the refused components pointed at was touched.
        self.assertTrue((victim / "keep.md").is_file())
        self.assertTrue((git_dir / "HEAD").is_file())
        self.assertTrue(self.fake_cache.is_dir())

    def test_a_symlinked_docs_dir_is_not_written_through(self):
        """Defence in depth: `assert_docs_dir` validates the component's
        SPELLING, and nothing re-checked the join.

        `bionic` passes every rule in `DOCS_DIR_RE`, so a symlink at
        `<clone>/bionic` steered `mkdir(exist_ok=True)` and the `manifest.yml`
        write into the link's target. Only the write path escaped —
        `rmtree(ignore_errors=True)` is a no-op on a symlink — which is exactly
        why an exit-code assertion is not the test: the canary is the victim
        directory, fingerprinted byte for byte.

        Reachability is low (a corpus repo that TRACKS `bionic` as a symlink is
        already refused by `_refuse_tracked`, and `git clone` cannot produce an
        untracked one), and the remedy is one line, so it is taken."""
        clone = self.fake_cache / "rubygems-org"
        clone.mkdir()
        victim = self.fake_cache / "victim"
        victim.mkdir()
        (victim / "keep.md").write_text("canary: another clone\n",
                                        encoding="utf-8")
        watched = (victim / "keep.md", victim / "manifest.yml",
                   victim / "adrs", victim / "arch", victim / "observations")
        before = _fingerprint(watched)
        (clone / "bionic").symlink_to(victim, target_is_directory=True)

        for fn in (self.SC.bootstrap_tree, self.SC.teardown_tree):
            with self.subTest(fn=fn.__name__):
                with self.assertRaises(self.SC.ScratchTreeRefused) as ctx:
                    fn(clone, "bionic")
                self.assertIn("symlink", str(ctx.exception))
        self.assertEqual(_fingerprint(watched), before,
                         "the harness wrote through the symlink")
        # Control: the identical name with no symlink in the way is accepted,
        # so the refusal is a property of the link and not of `bionic`.
        plain = self.fake_cache / "clone-plain"
        plain.mkdir()
        self.assertTrue((self.SC.bootstrap_tree(plain, "bionic")
                         / "manifest.yml").is_file())

    def test_a_plain_docs_dir_name_is_accepted(self):
        """Leaf: the positive control. `bionic` and `docs` are the two names
        real trees use, and both must survive the validator — a guard that
        refuses every component refuses the harness's own default."""
        for docs_dir in ("bionic", "docs", "docs_v2", "arch-notes"):
            with self.subTest(docs_dir=docs_dir):
                clone = self.fake_cache / f"clone-{docs_dir}"
                clone.mkdir()
                tree = self.SC.bootstrap_tree(clone, docs_dir)
                self.assertEqual(tree, clone / docs_dir)
                self.assertTrue((tree / "manifest.yml").is_file())
                self.SC.teardown_tree(clone, docs_dir)
                self.assertFalse(tree.exists())

    def test_the_reported_traversal_resolves_to_this_repository_s_root(self):
        """The finding verified rather than paraphrased.

        With the REAL cache root, `--docs-dir ../../../../../../` joined onto
        a corpus clone resolves to this checkout — six levels being exactly
        `.cache` → `arch-corpus` → `tests` → `scripts` → `crux` → the root.
        Path arithmetic only: nothing is called and nothing is removed."""
        real_clone = self._real_cache / "rubygems-org"
        self.assertEqual((real_clone / "../../../../../../").resolve(),
                         REPO_ROOT.resolve())
        # An absolute component is adopted whole by `Path.__truediv__`: the
        # clone root is discarded and nothing of the join is left inside the
        # cache. (Compared resolved, because `/etc` is a symlink on macOS.)
        self.assertEqual((real_clone / "/etc").resolve(),
                         Path("/etc").resolve())

    @unittest.skipUnless(HAVE_YAML, "PyYAML required (run under uv)")
    def test_the_cli_refuses_a_traversing_docs_dir_with_exit_2(self):
        """The entry point, not the helper.

        `--docs-dir` arrives from a shell, and `measure` hands it to
        `teardown_tree` before anything else runs. `rubygems-org` is used
        because `corpus_entry` reads the real `corpus.yml` before any of this;
        the clone it names is the empty directory in the fake cache."""
        clone = self.fake_cache / "rubygems-org"
        clone.mkdir()

        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = self.SC.main(["--repo", "rubygems-org", "--stage", "pre",
                                 "--docs-dir", "../../../../../../"])
        self.assertEqual(code, 2, err.getvalue())
        self.assertIn("--docs-dir", err.getvalue())
        self.assertEqual(_fingerprint(self._watched(clone)),
                         [(False, None)] * 3)

        # The OTHER exit-2 lane, so "exit 2" is not the whole assertion: an
        # absent clone exits 2 as well, and says something else entirely.
        shutil.rmtree(clone)
        err2 = io.StringIO()
        with contextlib.redirect_stderr(err2):
            code2 = self.SC.main(["--repo", "rubygems-org", "--stage", "pre"])
        self.assertEqual(code2, 2, err2.getvalue())
        self.assertIn("no clone at", err2.getvalue())
        self.assertNotIn("--docs-dir", err2.getvalue())

    def test_an_unparseable_mining_report_is_an_environment_error(self):
        """`_mining_report` reads a file the mining step drops beside the
        clone, and a half-written one used to raise `JSONDecodeError` out of
        `measure` — past `main`'s two handlers, so the documented exit-2
        environment lane became a traceback and an unparseable stdout."""
        clone = self.fake_cache / "rubygems-org"
        clone.mkdir()
        self.assertEqual(self.SC._mining_report(clone)["recorded"], False)

        (clone / ".survey-mining.json").write_text("{truncated",
                                                   encoding="utf-8")
        with self.assertRaises(EnvironmentError) as ctx:
            self.SC._mining_report(clone)
        self.assertIn(".survey-mining.json", str(ctx.exception))

        # Control: a well-formed report reads back, so the refusal is the
        # parse and not a function that raises on every file.
        (clone / ".survey-mining.json").write_text(
            '{"dedup_collapses": 3}', encoding="utf-8")
        self.assertEqual(self.SC._mining_report(clone),
                         {"dedup_collapses": 3, "recorded": True})


# ── unit 5: the corpus-mined batch, end to end ──────────────────────────────

@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under uv)")
class CorpusBatchTests(_TreeCase):
    """The frozen corpus candidate sets, driven end to end in a temp tree.

    Each fixture holds a `state.yml` mined from a real repository at a pinned
    SHA, plus `evidence_paths.json` — the distinct evidence paths the batch
    cites. Those paths are real files in the mined repository and nothing in a
    temporary tree, so `build_plan` refuses every row until they are
    materialized: a test that skips that step measures the refusal and reports
    it as a pass.
    """

    def _fixtures(self) -> list[Path]:
        if not CORPUS_FIXTURES.is_dir():  # pragma: no cover
            return []
        return sorted(p for p in CORPUS_FIXTURES.iterdir()
                      if (p / "state.yml").is_file())

    def _seed_from_fixture(self, fixture: Path, name: str) -> Path:
        root = self._tree(name)
        state_path = self._state_path(root)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(fixture / "state.yml", state_path)

        paths = json.loads(
            (fixture / "evidence_paths.json").read_text(encoding="utf-8"))["paths"]
        self.assertGreater(len(paths), 0, f"{fixture} lists no evidence paths")
        for rel in paths:
            self.assertFalse(Path(rel).is_absolute(), rel)
            self._source(root, rel)
        return root

    def test_a_corpus_mined_batch_publishes_records_and_check_observations_sees_them(self):
        """The end-to-end publish, over a real mined candidate set.

        Every verdict here is synthetic (module docstring), and the count it
        asserts is this temporary tree's, never the corpus survey's.
        """
        fixture = CORPUS_FIXTURES / "rubygems-org"
        self.assertTrue((fixture / "state.yml").is_file(), fixture)
        root = self._seed_from_fixture(fixture, "corpus")

        payload = self._scaffold(root)
        rows = payload["rows"]
        self.assertGreaterEqual(rows, 5, payload)

        self._author(root, payload["batch_id"])
        proc = self._signoff(root, payload["batch_id"])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        published = json.loads(proc.stdout)
        self.assertEqual(published["state"], "S9")
        self.assertEqual(len(published["records"]), rows)

        result = self._check(root)
        self.assertGreaterEqual(result["records"], 5, result)
        self.assertEqual(result["records"], rows, result)
        self.assertEqual(result["broken"], [], result["broken"])
        self.assertEqual(result["survey_debt"], 0, result)

        # Positive control, in this test because it must run against THESE
        # records: deleting one published record makes the checker report
        # BROKEN. Without it, `broken == []` is equally true of a checker
        # pointed at an empty concern.
        victim = sorted(self._obs(root).glob("OBS-*.md"))[0]
        victim.unlink()
        after = self._check(root)
        bijection = [b for b in after["broken"]
                     if b.startswith("CHK-OBS-BIJECTION:")]
        self.assertTrue(bijection,
                        f"deleting {victim.name} left the checker clean: "
                        f"{after}")
        self.assertIn(victim.stem.split("-")[0] + "-" + victim.stem.split("-")[1],
                      " ".join(bijection))
        self.assertEqual(after["records"], rows - 1, after)

    def test_a_corpus_batch_moves_the_summaries_receipt_digest(self):
        """The receipts manifest is a declared input to the summaries
        projection, so a published batch must move its hash off `None`."""
        root = self._tree("digest")
        self.assertIsNone(SP.survey_receipts_sha256(self._obs(root)))

        fixture = CORPUS_FIXTURES / "rubygems-org"
        root = self._seed_from_fixture(fixture, "digest-published")
        self._publish(root)
        digest = SP.survey_receipts_sha256(self._obs(root))
        self.assertIsNotNone(digest)
        self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_every_corpus_fixture_publishes_under_a_distinct_batch_id(self):
        """Batch ids are per-tree and monotonic, so two batches signed in one
        tree never share a number — which is what makes a receipt directory
        name an identity rather than a content hash.

        Runs over every fixture present, so a second corpus repository landing
        beside `rubygems-org` is covered without editing this test."""
        fixtures = self._fixtures()
        self.assertTrue(fixtures, f"no corpus fixture under {CORPUS_FIXTURES}")

        seen = []
        for fixture in fixtures:
            root = self._seed_from_fixture(fixture, f"multi-{fixture.name}")
            first = self._publish(root)
            # A second batch in the SAME tree, over one fresh candidate.
            self._add_candidate(root, self._spec(
                root, "a-second-claim", "The tree carries one more claim",
                "runtime", "src/second.py"))
            second = self._publish(root)
            self.assertNotEqual(first, second, fixture.name)
            self.assertLess(first, second, "batch ids are not monotonic")
            seen.append((fixture.name, first, second))
        self.assertTrue(seen)


# ── unit 4b: the miner's domain contract ────────────────────────────────────

@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under uv)")
class MinerDomainContractTests(_TreeCase):
    """A candidate mined without a `domain` produces an unsignable sheet."""

    def test_a_candidate_with_no_domain_reaches_the_scaffold_and_refuses(self):
        """The defect the corpus exposed, pinned.

        The scaffold seeds `proposed_domain` from the candidate's `domain` and
        writes the sheet either way, so a domainless candidate costs a batch
        number and a human's review pass before anything complains. The
        refusal is real and it lands one whole step late — this test is what
        keeps the miner's obligation from being documentation alone."""
        root = self._tree()
        spec = self._spec(root, "httpx", "The gateway depends on httpx",
                          "", "src/gateway.py")
        anchor = self._add_candidate(root, spec)

        payload = self._scaffold(root)
        sheet = SS.read_sheet(self._obs(root) / f"survey-{payload['batch_id']}.yml")
        self.assertEqual(sheet["rows"][0]["proposed_domain"], "",
                         "the scaffold invented a domain the miner never mined")

        self._author(root, payload["batch_id"])
        proc = self._signoff(root, payload["batch_id"])
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("no domain after the seed is applied", proc.stdout)
        self.assertIn(anchor, proc.stdout)
        self.assertEqual(list(self._obs(root).glob("OBS-*.md")), [])

        # Fixing the MINER does not rescue the sheet: the seed was frozen into
        # it at scaffold time, so the batch stays unsignable.
        state = StateFile(self._state_path(root))
        state.rows[anchor]["domain"] = "runtime"
        state.save()
        proc = self._signoff(root, payload["batch_id"])
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("no domain after the seed is applied", proc.stdout)

        # Positive control: the same sheet with the human's `domain` cell
        # filled publishes — so the refusal is the empty cell and not a
        # fixture that could never publish at all. That cell is the human act
        # the miner's omission costs.
        self._author(root, payload["batch_id"], domains={anchor: "runtime"})
        proc = self._signoff(root, payload["batch_id"])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(len(list(self._obs(root).glob("OBS-*.md"))), 1)

    def test_the_miner_skill_names_domain_and_the_cost_of_omitting_it(self):
        """`recover-decisions` step 2 is the only instruction a miner reads.

        It listed the anchor, the id and the evidence and never named
        `domain`, so a miner following it produced a sheet that refused every
        row. The skill text is the fix; this test is what stops it being
        edited back out."""
        text = MINER_SKILL.read_text(encoding="utf-8")
        step = next(line for line in text.splitlines()
                    if line.startswith("2. **Extract candidates.**"))
        self.assertIn("`domain`", step,
                      "step 2 does not name the domain field")
        self.assertRegex(
            step, r"proposed_domain",
            "step 2 does not name the sheet cell the domain seeds")
        self.assertRegex(
            step, r"refus",
            "step 2 names the field without naming the consequence of "
            "omitting it")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
