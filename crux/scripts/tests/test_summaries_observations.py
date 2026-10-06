"""The summaries projection's observation widening (docs/AGENTS.md §17;
ADR-0095 requirement 4 and requirement 6's declared input domain).

Pins, one named test per claim:

  - S1  `collect_records` reads active ADRs UNION `ratified` observations; a
        record in observed / rejected / retired / decided projects no rule
        row; the §17.1-only entry constraints (provenance equals the record's
        and is never `authored`, the handle is `OBS-NNNN/<slug>` anchored to
        its host, no `anchor` sub-field) refuse through
        `GovernsValidationError`; handles stay unique across the union.
  - S2  observed provenance projects `authority: descriptive`, derived by the
        existing `authority_for` with no branch on `source_kind`.
  - S3  a `decided` record projects an alias row per handle and no rule row.
  - S4  `observations_sha256` mirrors `adr_frontmatter_sha256`'s construction
        and moves on ratification, retirement, and a ratified successor.
  - S5  `_meta.json` is schema "5" with `input_domain`,
        `observations_sha256` and `survey_receipts_sha256` added (the last
        two schema bumps: ADR-0095 then ADR-0098); the driver refuses (exit 2, nothing
        written) to rewrite a projection whose declared domain names a source
        it cannot read, in write mode and in --dry-run alike.
  - P1  the zero-observation identity, in three legs (P1a / P1b / P1c).
  - Requirement 6: a tree with `adrs` absent from `concerns_enabled` projects
        from the observation half alone.

Runs under the uv lane (PyYAML). Tempdirs for every fixture; the live-tree
legs read the repository and write nothing.
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPTS_DIR = REPO_ROOT / "crux" / "scripts"
# The live-tree tests below read `bionic/` — a dev-only surface absent from the
# staged crux-only release artifact (ADR-0036 boundary). Guard, never false-fail.
try:
    from ._dev_surface import require_dev_surface
except ImportError:
    from _dev_surface import require_dev_surface
sys.path.insert(0, str(SCRIPTS_DIR))

try:
    import yaml  # noqa: F401
    HAVE_YAML = True
except ImportError:  # pragma: no cover
    HAVE_YAML = False


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / filename)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


if HAVE_YAML:
    SP = _load("summaries_projection", "summaries_projection.py")
    GS = _load("gen_summaries_obs", "summarize-adrs.py")

EMPTY_CORPUS_SHA256 = hashlib.sha256(b"").hexdigest()


# ── fixture helpers ─────────────────────────────────────────────────────────

def _adr_text(num: int, governs: list[dict] | None) -> str:
    lines = [
        "---",
        f"id: ADR-{num:04d}",
        f'title: "Decision {num}"',
        "status: Accepted",
        "date: 2026-08-01",
        "supersedes: []",
        "superseded_by: null",
        "tags: [test]",
    ]
    if governs is not None:
        lines.append("governs:")
        for g in governs:
            lines.append(f"  - handle: {g['handle']}")
            lines.append(f"    domain: {g.get('domain', 'd')}")
            lines.append(f"    rule: \"{g.get('rule', 'the rule')}\"")
            lines.append(f"    scope: {g.get('scope', 'crux/scripts')}")
            lines.append(f"    provenance: {g.get('provenance', 'authored')}")
    lines += ["---", "", f"# ADR-{num:04d}", "", "Body.", ""]
    return "\n".join(lines) + "\n"


def _obs_text(num: int, *, status: str = "ratified", provenance: str = "recovered",
              entries: list[dict] | None = None, decided_by=None,
              evidence: list[str] | None = None, anchor_id: str = "a" * 16) -> str:
    oid = f"OBS-{num:04d}"
    if entries is None:
        entries = [{"handle": f"{oid}/what-it-does"}]
    if evidence is None:
        evidence = ["crux/scripts/summaries_projection.py:1-10"]
    fm = {
        "id": oid,
        "title": "The code does X.",
        "status": status,
        "date": "2026-08-02",
        "observed_date": "2026-08-01",
        "ratified_date": "2026-08-02" if status in ("ratified", "retired", "decided") else None,
        "rejected_date": "2026-08-02" if status == "rejected" else None,
        "retired_date": "2026-08-03" if status == "retired" else None,
        "decided_date": "2026-08-03" if status == "decided" else None,
        "provenance": provenance,
        "decided_by": decided_by,
        "evidence": evidence,
        "anchor_id": anchor_id,
        "related_invariants": [],
        "tags": ["test"],
        "governs": [],
    }
    for e in entries:
        entry = {
            "domain": e.get("domain", "d"),
            "rule": e.get("rule", "the observed rule"),
            "scope": e.get("scope", "crux/scripts"),
            "handle": e["handle"],
            "provenance": e.get("provenance", provenance),
        }
        if "anchor" in e:
            entry["anchor"] = e["anchor"]
        fm["governs"].append(entry)
    return "---\n" + yaml.dump(fm, sort_keys=False) + "---\n\n# " + oid + "\n"


class _Tree:
    """A throwaway tree: .bionic.yml, manifest, adrs/, observations/, runs/."""

    def __init__(self, tmp: Path, *, concerns=("adrs", "observations"),
                 make_adrs: bool = True, make_obs: bool = True):
        self.root = tmp
        (self.root / ".bionic.yml").write_text("docs_dir: bionic\n", encoding="utf-8")
        self.tree = self.root / "bionic"
        self.adrs = self.tree / "adrs"
        self.obs = self.tree / "observations"
        self.summaries = self.adrs / "summaries"
        if make_adrs:
            self.adrs.mkdir(parents=True)
        if make_obs:
            self.obs.mkdir(parents=True)
        (self.tree / "promptbooks" / "runs").mkdir(parents=True)
        self.write_manifest(concerns)

    def write_manifest(self, concerns):
        body = "schema_version: \"5\"\nconcerns_enabled:\n"
        for c in concerns:
            body += f"  - {c}\n"
        body += "adr:\n  next_number: 100\nobservation:\n  next_number: 10\n"
        self.tree.mkdir(exist_ok=True)
        (self.tree / "manifest.yml").write_text(body, encoding="utf-8")

    def write_adr(self, num: int, governs: list[dict] | None = None):
        self.adrs.mkdir(parents=True, exist_ok=True)
        (self.adrs / f"ADR-{num:04d}-x.md").write_text(_adr_text(num, governs), encoding="utf-8")

    def write_obs(self, num: int, **kw) -> Path:
        p = self.obs / f"OBS-{num:04d}-x.md"
        p.write_text(_obs_text(num, **kw), encoding="utf-8")
        return p

    def manifest(self) -> dict:
        return SP.read_manifest(self.root)

    def records(self):
        return SP.collect_records(self.adrs, governs_from=None, observations=self.obs)

    def build(self) -> dict:
        wanted = GS.build(self.root)
        return {p.name: body for p, body in wanted.items()}

    def snapshot(self) -> dict:
        return {p.name: p.read_bytes() for p in self.summaries.iterdir()} \
            if self.summaries.is_dir() else {}


def _run_main(argv):
    out_b, err_b = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out_b), contextlib.redirect_stderr(err_b):
        rc = GS.main(argv)
    return rc, out_b.getvalue(), err_b.getvalue()


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.t = _Tree(Path(self._tmp.name))


# ── S1: the widened record set and the §17.1-only constraints ───────────────

@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under uv)")
class S1CollectRecordsTests(_Base):
    def test_ratified_observation_projects_a_rule_row(self):
        self.t.write_adr(90, [{"handle": "ADR-0090/authored-rule"}])
        self.t.write_obs(1, status="ratified")
        handles = [r["handle"] for r in self.t.records()]
        self.assertEqual(handles, ["ADR-0090/authored-rule", "OBS-0001/what-it-does"])
        obs = [r for r in self.t.records() if r["source_kind"] == "observation"][0]
        self.assertEqual(obs["source_adr"], "OBS-0001")
        self.assertEqual(obs["adr_num"], 1)
        self.assertEqual(obs["evidence"], ["crux/scripts/summaries_projection.py:1-10"])
        self.assertEqual(obs["anchor_id"], "a" * 16)
        self.assertIsNone(obs["anchor"])

    def test_adr_records_carry_the_uniform_shape(self):
        self.t.write_adr(90, [{"handle": "ADR-0090/authored-rule"}])
        rec = self.t.records()[0]
        self.assertEqual(rec["source_kind"], "adr")
        self.assertEqual(rec["evidence"], [])
        self.assertIsNone(rec["anchor_id"])

    def test_observed_record_projects_no_rule_row(self):
        self.t.write_obs(1, status="observed")
        self.assertEqual(self.t.records(), [])

    def test_rejected_record_projects_no_rule_row(self):
        self.t.write_obs(1, status="rejected")
        self.assertEqual(self.t.records(), [])

    def test_retired_record_projects_no_rule_row(self):
        self.t.write_obs(1, status="retired")
        self.assertEqual(self.t.records(), [])

    def test_decided_record_projects_no_rule_row(self):
        self.t.write_adr(90, [{"handle": "ADR-0090/authored-rule"}])
        self.t.write_obs(1, status="decided", decided_by="ADR-0090")
        self.assertEqual([r["handle"] for r in self.t.records()],
                         ["ADR-0090/authored-rule"])

    def test_observations_none_leaves_the_adr_half_unchanged(self):
        self.t.write_adr(90, [{"handle": "ADR-0090/authored-rule"}])
        self.t.write_obs(1, status="ratified")
        without = SP.collect_records(self.t.adrs, governs_from=None)
        self.assertEqual([r["handle"] for r in without], ["ADR-0090/authored-rule"])

    def test_entry_provenance_authored_is_a_validation_error(self):
        self.t.write_obs(1, entries=[{"handle": "OBS-0001/x", "provenance": "authored"}])
        with self.assertRaises(SP.GovernsValidationError) as cm:
            self.t.records()
        self.assertTrue(any("authored" in p["problem"] for p in cm.exception.problems))

    def test_record_provenance_authored_is_a_validation_error(self):
        self.t.write_obs(1, provenance="authored")
        with self.assertRaises(SP.GovernsValidationError):
            self.t.records()

    def test_entry_provenance_must_equal_the_records(self):
        self.t.write_obs(1, provenance="recovered",
                         entries=[{"handle": "OBS-0001/x", "provenance": "reconstructed"}])
        with self.assertRaises(SP.GovernsValidationError) as cm:
            self.t.records()
        self.assertTrue(any("record" in p["problem"] for p in cm.exception.problems))

    def test_anchor_sub_field_is_a_validation_error(self):
        self.t.write_obs(1, entries=[{"handle": "OBS-0001/x", "anchor": "a span"}])
        with self.assertRaises(SP.GovernsValidationError) as cm:
            self.t.records()
        self.assertTrue(any("anchor" in p["problem"] for p in cm.exception.problems))

    def test_handle_must_be_obs_namespaced_to_its_host(self):
        self.t.write_obs(1, entries=[{"handle": "OBS-0002/x"}])
        with self.assertRaises(SP.GovernsValidationError):
            self.t.records()
        self.t.write_obs(1, entries=[{"handle": "ADR-0001/x"}])
        with self.assertRaises(SP.GovernsValidationError):
            self.t.records()

    def test_adr_entry_with_obs_handle_stays_an_error(self):
        # Widened by host kind, never by union.
        self.t.write_adr(90, [{"handle": "OBS-0090/x"}])
        with self.assertRaises(SP.GovernsValidationError):
            self.t.records()

    def test_handle_uniqueness_spans_adrs_union_observations(self):
        # Two observation files claiming the same id AND handle.
        self.t.write_obs(1)
        (self.t.obs / "OBS-0001-dup.md").write_text(_obs_text(1), encoding="utf-8")
        with self.assertRaises(SP.GovernsValidationError) as cm:
            self.t.records()
        self.assertTrue(any("globally unique" in p["problem"] for p in cm.exception.problems))

    def test_evidence_written_as_a_bare_string_is_a_validation_error(self):
        """The evidence half must fail closed exactly as the `governs` half
        does. A hand-edit writing `evidence: path:1-2` instead of a one-item
        list used to coerce to `[]`: the record projected a rule row, seeded no
        pairing, and rendered `implemented: no` with no evidence table and no
        finding anywhere in this lane."""
        p = self.t.write_obs(1)
        p.write_text(p.read_text(encoding="utf-8").replace(
            "evidence:\n- crux/scripts/summaries_projection.py:1-10",
            "evidence: crux/scripts/summaries_projection.py:1-10"), encoding="utf-8")
        with self.assertRaises(SP.GovernsValidationError) as cm:
            self.t.records()
        self.assertTrue(any("evidence" in p["problem"] for p in cm.exception.problems),
                        cm.exception.problems)

    def test_evidence_with_a_non_string_member_is_a_validation_error(self):
        self.t.write_obs(1, evidence=["crux/scripts/summaries_projection.py:1-10", 7])
        with self.assertRaises(SP.GovernsValidationError) as cm:
            self.t.records()
        self.assertTrue(any("evidence" in p["problem"] for p in cm.exception.problems),
                        cm.exception.problems)

    def test_evidence_absent_on_a_non_projecting_record_is_not_checked(self):
        """The constraint is scoped to records that project — an `observed`
        record is not read into the union at all."""
        self.t.write_adr(90, [{"handle": "ADR-0090/authored-rule"}])
        p = self.t.write_obs(1, status="observed")
        p.write_text(p.read_text(encoding="utf-8").replace(
            "evidence:\n- crux/scripts/summaries_projection.py:1-10",
            "evidence: not-a-list"), encoding="utf-8")
        self.assertEqual([r["handle"] for r in self.t.records()],
                         ["ADR-0090/authored-rule"])

    def test_refusal_is_the_document_verdict_lane_not_a_traceback(self):
        self.t.write_obs(1, entries=[{"handle": "OBS-0001/x", "anchor": "span"}])
        rc, out, _err = _run_main(["--repo-root", str(self.t.root), "--dry-run"])
        self.assertEqual(rc, 1)
        self.assertIn("validation_errors", json.loads(out))


# ── S2: descriptive authority, derived from provenance only ─────────────────

@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under uv)")
class S2AuthorityTests(_Base):
    def test_observation_rows_project_descriptive_authority(self):
        self.t.write_obs(1, provenance="recovered")
        self.t.write_obs(2, provenance="reconstructed")
        resolver = SP.build_resolver(self.t.records())
        for h in ("OBS-0001/what-it-does", "OBS-0002/what-it-does"):
            self.assertEqual(resolver[h]["authority"], "descriptive")
            self.assertEqual(resolver[h]["disposition"], "observed")

    def test_authority_derivation_has_no_branch_on_source_kind(self):
        # S2: widen the input only. authority/disposition derive from
        # provenance alone; `build_resolver` passes only `provenance` to them.
        import inspect
        for fn in (SP.authority_for, SP.disposition_for):
            self.assertNotIn("source_kind", inspect.getsource(fn))
        src = inspect.getsource(SP.build_resolver)
        self.assertIn("authority_for(prov)", src)
        self.assertIn("disposition_for(prov)", src)


# ── S3: the decided alias row ───────────────────────────────────────────────

@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under uv)")
class S3AliasRowTests(_Base):
    def test_decided_record_projects_an_alias_row_of_exactly_two_keys(self):
        self.t.write_adr(90, [{"handle": "ADR-0090/b"}, {"handle": "ADR-0090/a"}])
        self.t.write_obs(1, status="decided", decided_by="ADR-0090")
        records = self.t.records()
        rows = SP.collect_observation_alias_rows(self.t.obs, records)
        self.assertEqual(rows, {"OBS-0001/what-it-does": {
            "alias_of": "ADR-0090", "alias_handles": ["ADR-0090/a", "ADR-0090/b"]}})

    def test_alias_handles_empty_when_the_adr_carries_no_governs(self):
        self.t.write_adr(90, None)
        self.t.write_obs(1, status="decided", decided_by="ADR-0090")
        rows = SP.collect_observation_alias_rows(self.t.obs, self.t.records())
        self.assertEqual(rows["OBS-0001/what-it-does"],
                         {"alias_of": "ADR-0090", "alias_handles": []})

    def test_alias_row_reaches_the_resolver_and_no_rule_row_does(self):
        self.t.write_adr(90, [{"handle": "ADR-0090/a"}])
        self.t.write_obs(1, status="decided", decided_by="ADR-0090")
        built = self.t.build()
        resolver = json.loads(built["resolver.json"])
        self.assertEqual(resolver["OBS-0001/what-it-does"],
                         {"alias_of": "ADR-0090", "alias_handles": ["ADR-0090/a"]})
        self.assertNotIn("OBS-0001", built["rule-table.md"])

    def test_non_decided_records_project_no_alias_row(self):
        self.t.write_obs(1, status="ratified")
        self.t.write_obs(2, status="observed")
        self.assertEqual(SP.collect_observation_alias_rows(self.t.obs, self.t.records()), {})

    def test_no_observations_dir_projects_no_alias_row(self):
        self.assertEqual(SP.collect_observation_alias_rows(None, []), {})


# ── review_state omission on observation rows ───────────────────────────────

@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under uv)")
class ReviewStateOmissionTests(_Base):
    def test_observation_row_carries_no_review_state_and_no_unreviewed_suffix(self):
        self.t.write_adr(90, [{"handle": "ADR-0090/a"}])
        self.t.write_obs(1, status="ratified")
        records = self.t.records()
        reviews = SP.read_reviews(self.t.adrs)
        resolver = SP.build_resolver(records, reviews, 87)
        self.assertNotIn("review_state", resolver["OBS-0001/what-it-does"])
        self.assertEqual(resolver["ADR-0090/a"]["review_state"], "accepted-with-adr")
        table = SP.build_rule_table(records, reviews, 87)
        row = [l for l in table.splitlines() if l.startswith("| OBS-0001/")][0]
        self.assertNotIn("**unreviewed**", row)


# ── S4: the input hash covers both sources ──────────────────────────────────

@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under uv)")
class S4InputHashTests(_Base):
    def test_empty_corpus_digest(self):
        self.assertEqual(SP.observations_sha256(self.t.obs), EMPTY_CORPUS_SHA256)

    def test_construction_mirrors_adr_frontmatter_sha256(self):
        p = self.t.write_obs(1)
        block = SP._frontmatter_block(p.read_text(encoding="utf-8"))
        h = hashlib.sha256()
        h.update(p.name.encode("utf-8")); h.update(b"\0")
        h.update(block.encode("utf-8")); h.update(b"\0")
        self.assertEqual(SP.observations_sha256(self.t.obs), h.hexdigest())

    def test_index_md_is_outside_the_hash(self):
        before = SP.observations_sha256(self.t.obs)
        (self.t.obs / "index.md").write_text("# Observations\n", encoding="utf-8")
        self.assertEqual(SP.observations_sha256(self.t.obs), before)

    def test_ratification_moves_the_hash(self):
        self.t.write_obs(1, status="observed")
        before = SP.observations_sha256(self.t.obs)
        self.t.write_obs(1, status="ratified")
        self.assertNotEqual(SP.observations_sha256(self.t.obs), before)

    def test_retirement_moves_the_hash(self):
        self.t.write_obs(1, status="ratified")
        before = SP.observations_sha256(self.t.obs)
        self.t.write_obs(1, status="retired")
        self.assertNotEqual(SP.observations_sha256(self.t.obs), before)

    def test_ratified_successor_moves_the_hash(self):
        self.t.write_obs(1, status="ratified")
        before = SP.observations_sha256(self.t.obs)
        self.t.write_obs(2, status="ratified", entries=[{"handle": "OBS-0002/successor"}])
        self.assertNotEqual(SP.observations_sha256(self.t.obs), before)

    def test_each_mutation_drifts_the_projection(self):
        self.assertEqual(_run_main(["--repo-root", str(self.t.root)])[0], 0)
        self.t.write_obs(1, status="observed")
        rc, out, _ = _run_main(["--repo-root", str(self.t.root), "--dry-run"])
        self.assertEqual(rc, 1)
        self.assertIn("bionic/adrs/summaries/_meta.json", json.loads(out)["paths"])


# ── S5: the declared domain and the fail-closed refusal ─────────────────────

@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under uv)")
class MalformedObservationIdTests(_Base):
    """A malformed observation `id` is a DOCUMENT finding, never a crash.

    `_ADR_ID_SHAPE_RE` exists on the ADR half so an authored-document error is
    a validation finding rather than an `int()` crash. The observation half had
    no equivalent, so `adr_num("OBS-alpha")` raised `ValueError`, which the
    drivers surface as exit 2 — the environment lane that CHK-DRIFT-1 says is
    "never a document finding". A reader would hunt a broken toolchain instead
    of a typo in a record they just wrote.
    """

    def _write_bad_id(self, status="ratified", **kw):
        p = self.t.obs / "OBS-0001-x.md"
        text = _obs_text(1, status=status, **kw).replace(
            "id: OBS-0001", "id: OBS-alpha", 1)
        p.write_text(text, encoding="utf-8")

    def test_a_ratified_record_with_a_malformed_id_is_a_validation_error(self):
        self._write_bad_id()
        with self.assertRaises(SP.GovernsValidationError):
            SP.collect_records(self.t.adrs, observations=self.t.obs)

    def test_a_decided_record_with_a_malformed_id_is_a_validation_error(self):
        self._write_bad_id(status="decided", decided_by="ADR-0090")
        with self.assertRaises(SP.GovernsValidationError):
            SP.collect_observation_alias_rows(self.t.obs, [])

    def test_a_prefixed_id_is_still_accepted(self):
        # §14.3 lets an id carry an artifact prefix. The handle stays bare
        # `OBS-NNNN/slug` — that is the existing anchoring contract, and the
        # shape guard must not narrow it into rejecting a prefixed record.
        p = self.t.obs / "OBS-0001-x.md"
        p.write_text(_obs_text(1).replace("id: OBS-0001", "id: CRX-OBS-0001", 1),
                     encoding="utf-8")
        recs = SP.collect_records(self.t.adrs, observations=self.t.obs)
        self.assertEqual([r["source_adr"] for r in recs], ["CRX-OBS-0001"])


class S5MetaAndRefusalTests(_Base):
    def _meta(self) -> dict:
        return json.loads(self.t.build()["_meta.json"])

    def test_meta_schema_is_5_with_exactly_the_added_keys(self):
        # Schema "4" is ADR-0098 clause 2's arrival: the per-batch survey
        # receipts join the input domain as one declared source beside the
        # records, so `survey-receipts` joins `input_domain` and
        # `survey_receipts_sha256` joins the digests. Null here, because this
        # tree has signed no batch.
        meta = self._meta()
        self.assertEqual(meta["schema"], "5")
        self.assertNotEqual(meta["schema"], "4")
        self.assertEqual(set(meta), {"adr_frontmatter_sha256", "backfill_reviews_sha256",
                                     "input_domain", "observations_sha256",
                                     "survey_receipts_sha256", "schema", "tool"})
        self.assertEqual(meta["input_domain"],
                         ["adrs", "backfill-reviews", "observations",
                          "survey-receipts"])
        self.assertEqual(meta["observations_sha256"], EMPTY_CORPUS_SHA256)
        self.assertIsNone(meta["survey_receipts_sha256"])

    def test_domain_omits_observations_when_the_concern_is_disabled(self):
        self.t.write_manifest(["adrs"])
        meta = self._meta()
        self.assertEqual(meta["input_domain"], ["adrs", "backfill-reviews"])
        self.assertIsNone(meta["observations_sha256"])

    def _declare_then_disable(self):
        self.assertEqual(_run_main(["--repo-root", str(self.t.root)])[0], 0)
        self.t.write_manifest(["adrs"])
        return self.t.snapshot()

    def test_write_mode_refuses_when_declared_observations_is_unreadable(self):
        before = self._declare_then_disable()
        rc, out, err = _run_main(["--repo-root", str(self.t.root)])
        self.assertEqual(rc, 2)
        self.assertEqual(out, "")
        self.assertIn("observations", err)
        self.assertEqual(self.t.snapshot(), before)

    def test_dry_run_refuses_identically(self):
        before = self._declare_then_disable()
        rc, out, err = _run_main(["--repo-root", str(self.t.root), "--dry-run"])
        self.assertEqual(rc, 2)
        self.assertEqual(out, "")
        self.assertIn("observations", err)
        self.assertEqual(self.t.snapshot(), before)

    def test_refuses_when_the_declared_directory_is_absent(self):
        import shutil
        self.assertEqual(_run_main(["--repo-root", str(self.t.root)])[0], 0)
        shutil.rmtree(self.t.obs)
        rc, _out, err = _run_main(["--repo-root", str(self.t.root), "--dry-run"])
        self.assertEqual(rc, 2)
        self.assertIn("absent", err)

    def test_refuses_when_a_declared_record_is_unparseable(self):
        self.assertEqual(_run_main(["--repo-root", str(self.t.root)])[0], 0)
        (self.t.obs / "OBS-0007-bad.md").write_text("---\n: [\n---\n", encoding="utf-8")
        rc, _out, err = _run_main(["--repo-root", str(self.t.root), "--dry-run"])
        self.assertEqual(rc, 2)
        self.assertIn("OBS-0007-bad.md", err)

    def test_refuses_an_unknown_domain_member(self):
        self.t.summaries.mkdir(parents=True)
        (self.t.summaries / "_meta.json").write_text(json.dumps(
            {"schema": "4", "input_domain": ["adrs", "sidecar"]}), encoding="utf-8")
        before = self.t.snapshot()
        rc, out, err = _run_main(["--repo-root", str(self.t.root)])
        self.assertEqual(rc, 2)
        self.assertEqual(out, "")
        self.assertIn("sidecar", err)
        self.assertEqual(self.t.snapshot(), before)

    def test_schema_2_meta_without_a_declaration_is_rewritten(self):
        # The accepted limit: a projection an older plugin wrote declares no
        # domain, so the regenerate widens it rather than refusing.
        self.t.summaries.mkdir(parents=True)
        (self.t.summaries / "_meta.json").write_text(json.dumps(
            {"schema": "2", "tool": "summarize-adrs.py"}), encoding="utf-8")
        rc, _out, _err = _run_main(["--repo-root", str(self.t.root)])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads((self.t.summaries / "_meta.json").read_text())["schema"], "5")


# ── Requirement 6: observations without ADRs ────────────────────────────────

@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under uv)")
class Requirement6Tests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_adr_less_tree_projects_the_observation_rule(self):
        t = _Tree(Path(self._tmp.name), concerns=("observations",), make_adrs=False)
        t.write_obs(1, status="ratified")
        rc, out, _err = _run_main(["--repo-root", str(t.root)])
        self.assertEqual(rc, 0, out)
        resolver = json.loads((t.summaries / "resolver.json").read_text(encoding="utf-8"))
        self.assertEqual(resolver["OBS-0001/what-it-does"]["authority"], "descriptive")
        meta = json.loads((t.summaries / "_meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["input_domain"],
                         ["adrs", "backfill-reviews", "observations",
                          "survey-receipts"])
        self.assertEqual(_run_main(["--repo-root", str(t.root), "--dry-run"])[0], 0)

    def test_missing_adrs_dir_is_still_refused_when_adrs_is_enabled(self):
        t = _Tree(Path(self._tmp.name), concerns=("adrs", "observations"), make_adrs=False)
        rc, _out, err = _run_main(["--repo-root", str(t.root)])
        self.assertEqual(rc, 2)
        self.assertIn("adrs", err)


# ── P1: the zero-observation identity, three legs ───────────────────────────

@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under uv)")
class P1ZeroObservationIdentityTests(unittest.TestCase):
    """ADR-0095's postcondition: with zero ratified observations the widening
    contributes nothing. It is a claim about the WIDENING, not about the
    projections never moving — they moved in this run for unrelated reasons
    (run-snapshot bindings, ADR-0096's handles), so no leg compares against
    committed bytes."""

    CONTENT = ("rule-table.md", "resolver.json", "implementation-map.md")

    def _live_builds(self):
        require_dev_surface(self, REPO_ROOT / "bionic" / "manifest.yml", "bionic/manifest.yml")
        manifest = SP.read_manifest(REPO_ROOT)
        obs = SP.observations_dir(REPO_ROOT)
        projecting = [p for p in SP.observation_paths(obs)
                      if SP.read_frontmatter(p.read_text(encoding="utf-8")).get("status")
                      in (SP.OBSERVATION_PROJECTED_STATUS, SP.OBSERVATION_ALIAS_STATUS)]
        if projecting:
            self.skipTest("P1 is a zero-ratified-observation postcondition; the live "
                          f"tree holds {len(projecting)} projecting record(s)")
        suppressed = dict(manifest)
        suppressed["concerns_enabled"] = [c for c in manifest["concerns_enabled"]
                                          if c != "observations"]
        read = {p.name: b for p, b in GS.build(REPO_ROOT, manifest=manifest).items()}
        held = {p.name: b for p, b in GS.build(REPO_ROOT, manifest=suppressed).items()}
        return read, held

    def test_p1a_content_artifacts_are_byte_equal_with_and_without_the_source(self):
        read, held = self._live_builds()
        for name in self.CONTENT:
            self.assertEqual(read[name], held[name], name)

    def test_p1b_meta_digests_unchanged_and_only_the_domain_keys_added(self):
        """The `schema` bumps ("2" -> "3" for ADR-0095, "3" -> "4" for
        ADR-0098) are deliberate schema changes, not the widening contributing
        content; the content-bearing digests are what the identity claim
        covers."""
        read, held = self._live_builds()
        m_read, m_held = json.loads(read["_meta.json"]), json.loads(held["_meta.json"])
        for key in ("adr_frontmatter_sha256", "backfill_reviews_sha256"):
            self.assertEqual(m_read[key], m_held[key], key)
        self.assertEqual(set(m_read) - {"adr_frontmatter_sha256", "backfill_reviews_sha256",
                                        "schema", "tool"},
                         {"input_domain", "observations_sha256",
                          "survey_receipts_sha256"})
        self.assertEqual(m_read["observations_sha256"], EMPTY_CORPUS_SHA256)
        self.assertIsNone(m_held["observations_sha256"])
        self.assertEqual(m_read["schema"], "5")
        self.assertNotEqual(m_read["schema"], "4")

    def test_p1c_one_ratified_observation_changes_the_build_and_its_removal_restores_it(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        t = _Tree(Path(tmp.name))
        t.write_adr(90, [{"handle": "ADR-0090/a"}])
        before = t.build()
        p = t.write_obs(1, status="ratified")
        during = t.build()
        for name in ("rule-table.md", "resolver.json", "_meta.json"):
            self.assertNotEqual(before[name], during[name], name)
        self.assertIn("OBS-0001/what-it-does", during["resolver.json"])
        p.unlink()
        self.assertEqual(t.build(), before)


# ── the third reader of the concern: the one that PUBLISHES ─────────────────

@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under uv)")
class ObservationRecordContainmentTests(unittest.TestCase):
    """[SECURITY:S5] `observation_paths` walks a directory whose contents it
    does not own, and every caller `read_text`s what it returns.

    It had no resolved-containment leg, so a symlink planted in the concern
    directory was followed OUT of the repository and the outside file's rule
    text was published into three committed artifacts — `resolver.json`,
    `rule-table.md`, and (through the summaries projection) `doctrine/index.md`
    — at exit 0. `recover._contained_record` and `survey_sheet.record_paths`
    are the two siblings that already carry this leg; this is the third reader
    of the same directory and the only one that writes committed artifacts.

    Every absence assertion below is paired with the `_plant_real_record`
    control: the SAME bytes as a real file inside the concern, which IS
    published. That is what makes "the secret is not in the artifact" a claim
    about the guard rather than about a fixture that never reached the writer.
    """

    SECRET = "The CFO runway is nine months and tier-2 layoffs are planned"
    LINK_NAME = "OBS-0777-planted.md"

    #: A well-formed ratified record — nothing here is malformed, because the
    #: leak is a PUBLICATION, not a parse error. The record is valid, so the
    #: projection is willing to render it; containment is the only thing that
    #: can decide it must not.
    def _record_text(self) -> str:
        return _obs_text(777, status="ratified", anchor_id="0" * 16,
                         entries=[{"domain": "leaked", "handle": "OBS-0777/leak",
                                   "rule": self.SECRET}])

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        (self.home / "repo").mkdir()
        self.t = _Tree(self.home / "repo")
        (self.t.tree / "invariants").mkdir(exist_ok=True)
        (self.t.tree / "log.md").write_text("# Log\n", encoding="utf-8")
        self.outside = self.home / "private-note.md"
        self.outside.write_text(self._record_text(), encoding="utf-8")

    def _plant_link(self):
        (self.t.obs / self.LINK_NAME).symlink_to(self.outside)

    def _plant_real_record(self):
        (self.t.obs / self.LINK_NAME).write_text(self._record_text(),
                                                 encoding="utf-8")

    def _artifact_blob(self) -> str:
        out = []
        for sub in ("summaries", "doctrine"):
            d = self.t.adrs / sub
            if d.is_dir():
                out += [f.read_text(encoding="utf-8", errors="replace")
                        for f in sorted(d.iterdir()) if f.is_file()]
        return "".join(out)

    def _cli(self, script):
        return subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / script), "--repo-root",
             str(self.t.root)], capture_output=True, text=True)

    #: Every shipped command whose own run reaches `observation_paths`.
    COMMANDS = ("summarize-adrs.py", "compile-doctrine.py",
                "check-doctrine-reconciliation.py")

    def test_no_command_reads_an_observation_through_a_link_out_of_the_tree(self):
        for script in self.COMMANDS:
            with self.subTest(script=script):
                self.setUp()
                self._plant_link()
                proc = self._cli(script)
                self.assertNotEqual(proc.returncode, 0, proc.stdout)
                blob = proc.stdout + proc.stderr
                self.assertNotIn(self.SECRET, blob, "the outside rule reached a stream")
                self.assertNotIn(str(self.outside), blob, "the link target reached a stream")
                self.assertIn(self.LINK_NAME, blob)
                self.assertIn("does not resolve to a file inside", blob)
                self.assertNotIn(self.SECRET, self._artifact_blob(),
                                 "the outside rule was published into an artifact")

    def test_the_same_bytes_as_a_real_record_are_published(self):
        """The positive control. Without it, every assertion above holds on a
        fixture that never reached the projection at all."""
        self._plant_real_record()
        for script in ("summarize-adrs.py", "compile-doctrine.py"):
            proc = self._cli(script)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        blob = self._artifact_blob()
        self.assertIn(self.SECRET, blob,
                      "the control never reached the writer, so the refusal "
                      "test above proves nothing")
        for name in ("resolver.json", "rule-table.md"):
            self.assertIn(self.SECRET,
                          (self.t.summaries / name).read_text(encoding="utf-8"))
        self.assertIn(self.SECRET,
                      (self.t.adrs / "doctrine" / "index.md").read_text(encoding="utf-8"))

    def test_observation_paths_refuses_the_link_rather_than_returning_it(self):
        self._plant_link()
        with self.assertRaises(ValueError) as caught:
            SP.observation_paths(self.t.obs)
        self.assertIn("does not resolve to a file inside", str(caught.exception))
        self.assertNotIn(self.SECRET, str(caught.exception))

    def test_observations_sha256_refuses_rather_than_absorbing_outside_bytes(self):
        """The digest is the drift gate's input. A digest that silently absorbs
        an outside file makes the gate agree with a projection built from it."""
        self._plant_link()
        with self.assertRaises(ValueError):
            SP.observations_sha256(self.t.obs)

    def test_the_source_problem_reports_the_refusal_rather_than_crashing(self):
        """`observations_source_problem`'s contract is "why this build cannot
        read the source, or None". `declared_domain_refusal` calls it for that
        sentence, so the containment refusal has to arrive as the string and
        not as a raise that reaches a generic handler."""
        self._plant_link()
        problem = SP.observations_source_problem(self.t.root, self.t.manifest())
        self.assertIsNotNone(problem)
        self.assertIn("does not resolve to a file inside", problem)
        self.assertNotIn(self.SECRET, problem)

    def test_an_in_tree_link_to_a_real_record_is_admitted(self):
        """Containment is a property of the resolved path, not of being a
        symlink: a link that lands INSIDE the concern is a contained file and
        is read. Without this leg the guard could be `is_symlink()` and pass."""
        target = self.t.write_obs(1, status="ratified")
        (self.t.obs / "OBS-0002-alias.md").symlink_to(target)
        paths = SP.observation_paths(self.t.obs)
        self.assertEqual([p.name for p in paths],
                         ["OBS-0001-x.md", "OBS-0002-alias.md"])


if __name__ == "__main__":
    unittest.main(verbosity=2)


@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under uv)")
class SurveyHoldingAreaContainmentTests(unittest.TestCase):
    """[SECURITY:S4/S5] The same class as `ObservationRecordContainmentTests`,
    one directory deeper and on the ANCESTOR leg.

    `survey_receipt_paths` walks `<obs>/_surveys/*/receipt.yml` and
    `survey_sheet.receipt_paths` spells the same layout for the survey lane.
    Both had a LEAF leg only: `_load_yaml_doc` refuses a receipt that is itself
    a symlink. Neither had an ancestor leg, so a symlink at `<obs>/_surveys`
    (or at one batch directory under it) leaves every entry below it a real
    file, the leaf check passes, and the outside file is read.

    It is read into a message, and `validate_receipt` quotes CELL VALUES —
    `batch receipt digest 'deadbeef' is not 64 lowercase hex digits`. So an
    outside file's field values reached stderr through
    `observations_source_problem`. `_assert_contained` is the write-side twin
    of this leg and could not see it: it guards a write target.
    """

    SECRET = "1f2e3d4c5b6a79880000deadbeefcafe1122334455667788aabbccddeeff0011"

    def _receipt_text(self) -> str:
        return ("config_version: \"1\"\n"
                "batch_id: SVY-0001\n"
                "sheet_ref: x\n"
                f"digest: {self.SECRET}z\n"
                "scaffold_provenance: {}\n"
                "signed: \"2026-08-30\"\n"
                "completed: null\n"
                "records: []\n"
                "rows: []\n")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        (self.home / "repo").mkdir()
        self.t = _Tree(self.home / "repo")
        (self.t.tree / "log.md").write_text("# Log\n", encoding="utf-8")
        self.outside = self.home / "private-surveys"
        (self.outside / "SVY-0001").mkdir(parents=True)
        (self.outside / "SVY-0001" / "receipt.yml").write_text(
            self._receipt_text(), encoding="utf-8")

    def _link_holding_area(self):
        (self.t.obs / "_surveys").symlink_to(self.outside)

    def _real_holding_area(self):
        (self.t.obs / "_surveys" / "SVY-0001").mkdir(parents=True)
        (self.t.obs / "_surveys" / "SVY-0001" / "receipt.yml").write_text(
            self._receipt_text(), encoding="utf-8")

    def _summarize(self):
        return subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "summarize-adrs.py"),
             "--repo-root", str(self.t.root)], capture_output=True, text=True)

    def test_summarize_refuses_a_holding_area_linked_out_of_the_tree(self):
        self._link_holding_area()
        proc = self._summarize()
        self.assertNotEqual(proc.returncode, 0, proc.stdout)
        blob = proc.stdout + proc.stderr
        self.assertNotIn(self.SECRET, blob,
                         "a cell value from the outside receipt reached a stream")
        self.assertIn("does not resolve", blob)

    def test_the_same_receipt_inside_the_concern_is_read_and_quoted(self):
        """The positive control, and the measurement that motivates the leg:
        the reader DOES quote a cell value, so the absence assertion above is
        about the guard and not about a reader that never ran."""
        self._real_holding_area()
        proc = self._summarize()
        self.assertNotEqual(proc.returncode, 0, proc.stdout)
        self.assertIn(self.SECRET, proc.stdout + proc.stderr)

    def test_survey_receipt_paths_refuses_rather_than_returning_the_outside_path(self):
        self._link_holding_area()
        with self.assertRaises(ValueError) as caught:
            SP.survey_receipt_paths(self.t.obs)
        self.assertNotIn(self.SECRET, str(caught.exception))

    def test_the_source_problem_reports_it_rather_than_raising(self):
        self._link_holding_area()
        problem = SP.observations_source_problem(self.t.root, self.t.manifest())
        self.assertIsNotNone(problem)
        self.assertIn("does not resolve", problem)

    def test_an_empty_linked_holding_area_refuses_on_its_own(self):
        """The ANCESTOR leg, stated on its own rather than as a side effect of
        the per-batch one: reading the holding area through a link out of the
        tree refuses whatever is inside it, so a later reader added under
        `_surveys/` inherits the refusal instead of needing its own."""
        empty = self.home / "empty-outside"
        empty.mkdir()
        (self.t.obs / "_surveys").symlink_to(empty)
        with self.assertRaises(ValueError) as caught:
            SP.survey_receipt_paths(self.t.obs)
        self.assertIn("does not resolve", str(caught.exception))

    def test_a_single_batch_directory_linked_out_is_refused_too(self):
        """The INTERIOR leg: the holding area is a real directory and one batch
        under it is the link. A guard on `_surveys` alone would pass this."""
        (self.t.obs / "_surveys").mkdir()
        (self.t.obs / "_surveys" / "SVY-0001").symlink_to(
            self.outside / "SVY-0001")
        with self.assertRaises(ValueError):
            SP.survey_receipt_paths(self.t.obs)


@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under uv)")
class ObservationsRootContainmentTests(unittest.TestCase):
    """[SECURITY:S5] The ROOT leg: the concern directory itself.

    `ObservationRecordContainmentTests` covers the LEAF (a record that is a
    symlink) and `SurveyHoldingAreaContainmentTests` covers the ANCESTOR and
    INTERIOR legs under `_surveys/`. All three resolve an entry against `obs`
    — and `obs` itself was never validated. `resolve_contained(obs, name)`
    computes `root_r = obs.resolve()`, so when `<docs_dir>/observations` is a
    link out of the repository the guard compares the outside directory
    against itself and admits everything under it.

    A relative link is git-carryable, so this travels in a pull request: the
    outside file lands in `resolver.json`, `rule-table.md` and the doctrine
    index compiled from them, at exit 0.

    The root is therefore validated with the same containment its entries take
    — resolved, contained, decided BEFORE anything resolves against it — on
    the shape `survey_sheet.receipt_paths` already uses for the holding area.
    """

    HANDLE = "OBS-0001/what-it-does"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        (self.home / "repo").mkdir()
        self.t = _Tree(self.home / "repo", make_obs=False)
        (self.t.tree / "log.md").write_text("# Log\n", encoding="utf-8")
        self.outside = self.home / "private-observations"
        self.outside.mkdir()
        (self.outside / "OBS-0001-x.md").write_text(
            _obs_text(1, status="ratified"), encoding="utf-8")

    def _link_concern(self, target: Path) -> None:
        """A RELATIVE link, because that is the one a commit carries."""
        rel = os.path.relpath(target, self.t.obs.parent)
        self.t.obs.symlink_to(rel, target_is_directory=True)

    def test_a_concern_directory_linked_out_of_the_repo_is_refused(self):
        self._link_concern(self.outside)
        with self.assertRaises(ValueError) as caught:
            SP.observation_paths(SP.observations_dir(self.t.root))
        self.assertIn("does not resolve", str(caught.exception))

    def test_the_source_problem_reports_it_rather_than_raising(self):
        self._link_concern(self.outside)
        problem = SP.observations_source_problem(self.t.root, self.t.manifest())
        self.assertIsNotNone(problem)
        self.assertIn("does not resolve", problem)

    def test_the_projection_refuses_rather_than_publishing_the_outside_file(self):
        """End to end, through the shipped driver: the leaked handle reached
        three committed artifacts at exit 0."""
        self._link_concern(self.outside)
        self.t.write_adr(90, [{"handle": "ADR-0090/authored-rule"}])
        rc, out, err = _run_main(["--repo-root", str(self.t.root)])
        self.assertNotEqual(
            rc, 0, f"published the outside record at exit 0: {out}")
        self.assertNotIn(self.HANDLE, out)
        built = (self.t.summaries / "resolver.json")
        if built.is_file():
            self.assertNotIn(self.HANDLE, built.read_text(encoding="utf-8"))

    def test_a_link_landing_inside_the_tree_stays_admissible(self):
        """The negative control. Containment is a property of the RESOLVED
        path, never of being a link — a concern directory linked to another
        directory INSIDE the repository is contained, and it is read."""
        inside = self.t.tree / "real-observations"
        inside.mkdir()
        (inside / "OBS-0001-x.md").write_text(
            _obs_text(1, status="ratified"), encoding="utf-8")
        self._link_concern(inside)
        names = [p.name for p in
                 SP.observation_paths(SP.observations_dir(self.t.root))]
        self.assertEqual(names, ["OBS-0001-x.md"])
        self.assertIsNone(
            SP.observations_source_problem(self.t.root, self.t.manifest()))

    def test_an_ordinary_concern_directory_is_unaffected(self):
        """The second control: no link at all."""
        self.t.obs.mkdir(parents=True)
        self.t.write_obs(1, status="ratified")
        names = [p.name for p in
                 SP.observation_paths(SP.observations_dir(self.t.root))]
        self.assertEqual(names, ["OBS-0001-x.md"])
