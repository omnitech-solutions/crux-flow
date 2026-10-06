"""The doctrine layer's observation widening (docs/AGENTS.md §17;
ADR-0095 requirement 5 and its Postconditions).

Pins, one named test per claim:

  - D1  per-domain authority rendering: the heading suffix, the
        `_Observed evidence:_` table, and the evidence-derived `basis`
        flag — each emitted ONLY when the domain holds an observation rule.
  - D2  the added (authored rule, ratified observation) pairing class, with
        all THREE conjuncts enforced individually; the ADR-0090 legacy seed
        retained verbatim; `candidate_pairings` is legacy union added.
  - D3  the generalized ledger key: the live `reconciliations.yml`
        round-trips byte-identically, its five keys are unchanged, every
        stored digest recomputes, slot A accepts an OBS handle and slot B
        rejects one, and `signoff-reconciliation.py` adjudicates the added
        class.
  - P1  the zero-observation identity, three legs (P1a / P1b / P1c).
  - P4  no persisted evidence carries a code excerpt.

Runs under the uv lane (PyYAML). Tempdirs for every fixture; the live-tree
legs read the repository and write nothing.
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import re
import shutil
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
    import summaries_projection as sp  # noqa: E402
    import doctrine_projection as dp  # noqa: E402
    CD = _load("compile_doctrine_obs", "compile-doctrine.py")
    SR = _load("signoff_reconciliation_obs", "signoff-reconciliation.py")

EMPTY_CORPUS_SHA256 = hashlib.sha256(b"").hexdigest()
EVIDENCE_RE = r"^[^\s:]+:\d+-\d+$"


# ── record helpers (pure-function lane) ─────────────────────────────────────

def _arec(handle, domain, scope, *, rule="the authored rule", adr="ADR-0090"):
    return {"handle": handle, "domain": domain, "rule": rule, "scope": scope,
            "anchor": None, "provenance": "authored", "source_adr": adr,
            "adr_num": sp.adr_num(adr), "source_kind": "adr",
            "evidence": [], "anchor_id": None}


def _orec(handle, domain, evidence, *, rule="the observed rule", obs="OBS-0001",
          scope="crux/scripts"):
    return {"handle": handle, "domain": domain, "rule": rule, "scope": scope,
            "anchor": None, "provenance": "recovered", "source_adr": obs,
            "adr_num": sp.adr_num(obs), "source_kind": "observation",
            "evidence": sorted(evidence), "anchor_id": "a" * 16}


def _invariant(iid, related, text, ratification="ratified"):
    return {"id": iid, "ratification": ratification, "related_adrs": related,
            "invariant_text": text, "path": None}


def _keys(pairings):
    return [(p["invariant"], p["handle"]) for p in pairings]


# ── scratch-tree fixture ────────────────────────────────────────────────────

def _adr_text(num: int, governs: list[dict] | None) -> str:
    lines = ["---", f"id: ADR-{num:04d}", f'title: "Decision {num}"',
             "status: Accepted", "date: 2026-08-01", "supersedes: []",
             "superseded_by: null", "tags: [test]"]
    if governs is not None:
        lines.append("governs:")
        for g in governs:
            lines.append(f"  - handle: {g['handle']}")
            lines.append(f"    domain: {g.get('domain', 'd')}")
            lines.append(f"    rule: \"{g.get('rule', 'the authored rule')}\"")
            lines.append(f"    scope: {g.get('scope', 'crux/scripts')}")
            lines.append(f"    provenance: {g.get('provenance', 'authored')}")
    lines += ["---", "", f"# ADR-{num:04d}", "", "Body.", ""]
    return "\n".join(lines) + "\n"


def _obs_text(num: int, *, status="ratified", provenance="recovered",
              entries=None, evidence=None, decided_by=None) -> str:
    oid = f"OBS-{num:04d}"
    if entries is None:
        entries = [{"handle": f"{oid}/what-it-does"}]
    if evidence is None:
        evidence = ["crux/scripts/x.py:1-10"]
    fm = {
        "id": oid, "title": "The code does X.", "status": status,
        "date": "2026-08-02", "observed_date": "2026-08-01",
        "ratified_date": "2026-08-02" if status in ("ratified", "retired", "decided") else None,
        "rejected_date": None, "retired_date": None, "decided_date": None,
        "provenance": provenance, "decided_by": decided_by, "evidence": evidence,
        "anchor_id": "a" * 16, "related_invariants": [], "tags": ["test"],
        "governs": [{
            "domain": e.get("domain", "d"), "rule": e.get("rule", "the observed rule"),
            "scope": e.get("scope", "crux/scripts"), "handle": e["handle"],
            "provenance": provenance} for e in entries],
    }
    return "---\n" + yaml.dump(fm, sort_keys=False) + "---\n\n# " + oid + "\n"


def _invariant_md(iid, *, related_adrs, text, ratification="ratified") -> str:
    related = "[" + ", ".join(related_adrs) + "]"
    return ("---\n" f"id: {iid}\nclass: behavior\nratification: {ratification}\n"
            f"related_adrs: {related}\n---\n\n# {iid} — title\n\n## The invariant\n\n"
            f"{text}\n\n## Class\n\n`behavior`\n")


class _Tree:
    """A throwaway tree: .bionic.yml, manifest, adrs/, observations/,
    invariants/, runs/, and the source files evidence paths resolve to."""

    def __init__(self, tmp: Path, *, concerns=("adrs", "observations", "invariants"),
                 make_obs: bool = True):
        self.root = tmp
        (self.root / ".bionic.yml").write_text("docs_dir: bionic\n", encoding="utf-8")
        self.tree = self.root / "bionic"
        self.adrs = self.tree / "adrs"
        self.obs = self.tree / "observations"
        self.doctrine = self.adrs / "doctrine"
        self.adrs.mkdir(parents=True)
        if make_obs:
            self.obs.mkdir(parents=True)
        (self.tree / "promptbooks" / "runs").mkdir(parents=True)
        (self.tree / "invariants").mkdir(parents=True)
        self.write_manifest(concerns)

    def write_manifest(self, concerns):
        body = "schema_version: \"5\"\nconcerns_enabled:\n"
        for c in concerns:
            body += f"  - {c}\n"
        body += "adr:\n  next_number: 100\n  governs_from: 1\nobservation:\n  next_number: 10\n"
        (self.tree / "manifest.yml").write_text(body, encoding="utf-8")

    def write_adr(self, num: int, governs: list[dict] | None = None):
        (self.adrs / f"ADR-{num:04d}-x.md").write_text(_adr_text(num, governs), encoding="utf-8")

    def write_obs(self, num: int, **kw) -> Path:
        p = self.obs / f"OBS-{num:04d}-x.md"
        p.write_text(_obs_text(num, **kw), encoding="utf-8")
        return p

    def write_invariant(self, iid, **kw):
        (self.tree / "invariants" / f"{iid.lower()}.md").write_text(
            _invariant_md(iid, **kw), encoding="utf-8")

    def write_source(self, rel: str):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x = 1\n" * 20, encoding="utf-8")

    def manifest(self) -> dict:
        return sp.read_manifest(self.root)

    def build(self, manifest=None) -> dict:
        return {p.name: body for p, body in CD.build(self.root, manifest=manifest).items()}

    def index(self, manifest=None) -> str:
        return self.build(manifest)["index.md"]


def _run(module, argv):
    out_b, err_b = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out_b), contextlib.redirect_stderr(err_b):
        rc = module.main(argv)
    return rc, out_b.getvalue(), err_b.getvalue()


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.t = _Tree(Path(self._tmp.name))


# ── D2: scope tokens + containment ──────────────────────────────────────────

@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under uv)")
class ScopePathTokenTests(unittest.TestCase):
    def test_docs_dir_placeholder_is_substituted_with_the_tree_name(self):
        self.assertEqual(dp.scope_path_tokens("<docs_dir>/adrs/summaries", "bionic"),
                         ["bionic/adrs/summaries"])

    def test_surrounding_punctuation_is_stripped(self):
        self.assertEqual(dp.scope_path_tokens("(`crux/scripts/x.py`), and manifest.yml.", "bionic"),
                         ["crux/scripts/x.py", "manifest.yml"])

    def test_prose_without_a_path_token_yields_nothing(self):
        self.assertEqual(dp.scope_path_tokens("the storage layer and its callers", "bionic"), [])

    def test_a_token_truncates_at_its_first_angle_bracket_segment(self):
        self.assertEqual(dp.scope_path_tokens("crux/skills/<name>/SKILL.md", "bionic"),
                         ["crux/skills"])

    def test_a_bare_angle_token_is_dropped(self):
        self.assertEqual(dp.scope_path_tokens("<path>/x.py", "bionic"), [])

    def test_absolute_home_and_dotdot_tokens_are_dropped(self):
        self.assertEqual(dp.scope_path_tokens("/etc/x.py ~/x.py a/../b.py", "bionic"), [])

    def test_tokens_are_deduped_and_sorted(self):
        self.assertEqual(dp.scope_path_tokens("tools/ crux/scripts tools", "bionic"),
                         ["crux/scripts", "tools"])


@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under uv)")
class ScopeContainsTests(unittest.TestCase):
    def test_equal_path_contains(self):
        self.assertTrue(dp.scope_contains("crux/scripts/x.py", "crux/scripts/x.py"))

    def test_directory_prefix_contains(self):
        self.assertTrue(dp.scope_contains("crux/scripts", "crux/scripts/x.py"))

    def test_string_prefix_alone_does_not_contain(self):
        self.assertFalse(dp.scope_contains("crux/script", "crux/scripts/x.py"))

    def test_posix_normalized_before_comparison(self):
        self.assertTrue(dp.scope_contains("crux/scripts/", "./crux/scripts/x.py"))


# ── D2: the added pairing class, conjunct by conjunct ───────────────────────

@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under uv)")
class ObservationPairingConjunctTests(unittest.TestCase):
    def test_shared_domain_alone_does_not_pair_the_option_d_guard(self):
        """A pairing generated on shared domain alone is the rejected Option D:
        an authored rule whose scope names no path pairs with nothing."""
        records = [_arec("ADR-0090/a", "d", "the storage layer"),
                   _orec("OBS-0001/o", "d", ["crux/scripts/x.py:1-2"])]
        self.assertEqual(dp.observation_pairings(records), [])

    def test_evidence_outside_the_declared_scope_does_not_pair(self):
        records = [_arec("ADR-0090/a", "d", "crux/scripts"),
                   _orec("OBS-0001/o", "d", ["tools/x.py:1-2"])]
        self.assertEqual(dp.observation_pairings(records), [])

    def test_a_different_domain_does_not_pair_even_when_scope_contains(self):
        records = [_arec("ADR-0090/a", "other", "crux/scripts"),
                   _orec("OBS-0001/o", "d", ["crux/scripts/x.py:1-2"])]
        self.assertEqual(dp.observation_pairings(records), [])

    def test_same_domain_and_contained_evidence_pairs(self):
        records = [_arec("ADR-0090/a", "d", "crux/scripts", rule="R"),
                   _orec("OBS-0001/o", "d", ["crux/scripts/x.py:1-2"], rule="O")]
        pairings = dp.observation_pairings(records)
        self.assertEqual(_keys(pairings), [("OBS-0001/o", "ADR-0090/a")])
        p = pairings[0]
        self.assertEqual(p["member_kind"], "observation")
        self.assertEqual(p["domain"], "d")
        self.assertEqual(p["source_adr"], "ADR-0090")
        self.assertEqual(p["rule"], "R")
        self.assertEqual(p["invariant_text"], "O")
        self.assertEqual(p["live_digest"], dp.reconciliation_content_digest("O", "R"))

    def test_narrowest_containing_scope_wins_over_a_broader_one(self):
        """`crux/` (trailing slash, so it IS a path token that contains the
        evidence) loses to `crux/scripts` on segment count — the broad rule
        is out-narrowed, not dropped."""
        broad = _arec("ADR-0090/broad", "d", "crux/")
        self.assertEqual(dp.scope_path_tokens(broad["scope"], "bionic"), ["crux"])
        records = [broad,
                   _arec("ADR-0091/narrow", "d", "crux/scripts", adr="ADR-0091"),
                   _orec("OBS-0001/o", "d", ["crux/scripts/x.py:1-2"])]
        self.assertEqual(_keys(dp.observation_pairings(records)),
                         [("OBS-0001/o", "ADR-0091/narrow")])
        alone = [broad, _orec("OBS-0001/o", "d", ["crux/scripts/x.py:1-2"])]
        self.assertEqual(_keys(dp.observation_pairings(alone)),
                         [("OBS-0001/o", "ADR-0090/broad")])

    def test_narrowness_is_segment_count_then_length(self):
        records = [_arec("ADR-0090/dir", "d", "crux/scripts"),
                   _arec("ADR-0091/file", "d", "crux/scripts/x.py", adr="ADR-0091"),
                   _orec("OBS-0001/o", "d", ["crux/scripts/x.py:1-2"])]
        self.assertEqual(_keys(dp.observation_pairings(records)),
                         [("OBS-0001/o", "ADR-0091/file")])

    def test_equally_narrow_scopes_both_pair(self):
        """The residual the decision admits: two rules declaring equally
        narrow scopes over the same code are a genuine co-declaration."""
        records = [_arec("ADR-0090/a", "d", "crux/scripts"),
                   _arec("ADR-0091/b", "d", "crux/scripts/", adr="ADR-0091"),
                   _orec("OBS-0001/o", "d", ["crux/scripts/x.py:1-2"])]
        self.assertEqual(_keys(dp.observation_pairings(records)),
                         [("OBS-0001/o", "ADR-0090/a"), ("OBS-0001/o", "ADR-0091/b")])

    def test_matching_runs_per_evidence_path_and_unions(self):
        records = [_arec("ADR-0090/a", "d", "crux/scripts"),
                   _arec("ADR-0091/b", "d", "tools/", adr="ADR-0091"),
                   _orec("OBS-0001/o", "d", ["crux/scripts/x.py:1-2", "tools/y.py:3-4"])]
        self.assertEqual(_keys(dp.observation_pairings(records)),
                         [("OBS-0001/o", "ADR-0090/a"), ("OBS-0001/o", "ADR-0091/b")])

    def test_two_evidence_paths_in_one_scope_yield_one_pairing(self):
        records = [_arec("ADR-0090/a", "d", "crux/scripts"),
                   _orec("OBS-0001/o", "d", ["crux/scripts/x.py:1-2", "crux/scripts/y.py:3-4"])]
        self.assertEqual(_keys(dp.observation_pairings(records)),
                         [("OBS-0001/o", "ADR-0090/a")])

    def test_a_broader_rule_does_not_pair_with_everything_beneath_it(self):
        records = [_arec("ADR-0090/root", "d", "crux/"),
                   _arec("ADR-0091/a", "d", "crux/scripts", adr="ADR-0091"),
                   _arec("ADR-0092/b", "d", "crux/skills", adr="ADR-0092"),
                   _orec("OBS-0001/o", "d", ["crux/scripts/x.py:1-2"]),
                   _orec("OBS-0002/p", "d", ["crux/skills/y.md:1-2"], obs="OBS-0002")]
        self.assertEqual(_keys(dp.observation_pairings(records)),
                         [("OBS-0001/o", "ADR-0091/a"), ("OBS-0002/p", "ADR-0092/b")])

    def test_observations_never_pair_with_each_other(self):
        records = [_orec("OBS-0001/o", "d", ["crux/scripts/x.py:1-2"], scope="crux/scripts"),
                   _orec("OBS-0002/p", "d", ["crux/scripts/y.py:1-2"], obs="OBS-0002",
                         scope="crux/scripts")]
        self.assertEqual(dp.observation_pairings(records), [])

    def test_docs_dir_scope_resolves_through_the_tree_name(self):
        records = [_arec("ADR-0090/a", "d", "<docs_dir>/adrs"),
                   _orec("OBS-0001/o", "d", ["docs/adrs/x.md:1-2"])]
        self.assertEqual(_keys(dp.observation_pairings(records, tree_name="docs")),
                         [("OBS-0001/o", "ADR-0090/a")])
        self.assertEqual(dp.observation_pairings(records, tree_name="bionic"), [])


@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under uv)")
class CandidatePairingUnionTests(unittest.TestCase):
    def test_legacy_seed_is_retained_verbatim(self):
        records = [_arec("ADR-0090/a", "d", "crux/scripts", rule="R")]
        invs = [_invariant("INV-0001", ["ADR-0090"], "T")]
        pairings = dp.candidate_pairings(records, invs)
        self.assertEqual(_keys(pairings), [("INV-0001", "ADR-0090/a")])
        self.assertEqual(pairings[0]["member_kind"], "invariant")
        self.assertEqual(pairings[0]["live_digest"], dp.reconciliation_content_digest("T", "R"))

    def test_legacy_seed_never_pairs_an_invariant_with_an_observation_rule(self):
        records = [_orec("OBS-0001/o", "d", ["crux/scripts/x.py:1-2"])]
        invs = [_invariant("INV-0001", ["ADR-0090", "OBS-0001"], "T")]
        self.assertEqual(dp.candidate_pairings(records, invs), [])

    def test_union_sorted_by_slot_a_then_handle(self):
        records = [_arec("ADR-0090/a", "d", "crux/scripts"),
                   _orec("OBS-0001/o", "d", ["crux/scripts/x.py:1-2"])]
        invs = [_invariant("INV-0001", ["ADR-0090"], "T")]
        self.assertEqual(_keys(dp.candidate_pairings(records, invs)),
                         [("INV-0001", "ADR-0090/a"), ("OBS-0001/o", "ADR-0090/a")])

    def test_with_zero_observations_the_union_is_the_legacy_seed(self):
        records = [_arec("ADR-0090/a", "d", "crux/scripts")]
        invs = [_invariant("INV-0001", ["ADR-0090"], "T")]
        legacy = [{k: v for k, v in p.items() if k != "member_kind"}
                  for p in dp.candidate_pairings(records, invs)]
        self.assertEqual(_keys(dp.candidate_pairings(records, invs)), _keys(legacy))
        self.assertEqual(len(legacy), 1)


# ── D3: the generalized ledger key ──────────────────────────────────────────

@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under uv)")
class LiveLedgerCompatibilityTests(unittest.TestCase):
    LEDGER = REPO_ROOT / "bionic" / "adrs" / "doctrine" / "reconciliations.yml"
    EXPECTED_KEYS = {
        ("INV-0001", "ADR-0054/quorum-and-responding-only-aggregation"),
        ("INV-0002", "ADR-0088/backfill-machinery"),
        ("INV-0003", "ADR-0088/backfill-machinery"),
        ("INV-0004", "ADR-0088/backfill-machinery"),
        ("INV-0005", "ADR-0088/backfill-machinery"),
    }

    def test_live_ledger_round_trips_byte_identically_under_the_widened_reader(self):
        require_dev_surface(self, self.LEDGER, "bionic/adrs/doctrine/reconciliations.yml")
        records = dp.read_reconciliations(REPO_ROOT)
        self.assertEqual(SR.serialize_ledger(records), self.LEDGER.read_text(encoding="utf-8"))

    def test_live_ledger_keys_are_exactly_the_five_on_disk(self):
        require_dev_surface(self, self.LEDGER, "bionic/adrs/doctrine/reconciliations.yml")
        records = dp.read_reconciliations(REPO_ROOT)
        self.assertEqual({(r["invariant"], r["handle"]) for r in records}, self.EXPECTED_KEYS)
        self.assertTrue(all(r["member_kind"] == "invariant" for r in records))

    def test_every_live_record_recomputes_its_stored_digest(self):
        require_dev_surface(self, self.LEDGER, "bionic/adrs/doctrine/reconciliations.yml")
        manifest = sp.read_manifest(REPO_ROOT)
        records = sp.collect_records(sp.adrs_dir(REPO_ROOT),
                                     governs_from=sp.governs_from(manifest))
        by_handle = {r["handle"]: r for r in records}
        by_inv = {i["id"]: i for i in dp.read_invariants(REPO_ROOT)}
        for rec in dp.read_reconciliations(REPO_ROOT):
            live = dp.reconciliation_content_digest(
                by_inv[rec["invariant"]]["invariant_text"], by_handle[rec["handle"]]["rule"])
            self.assertEqual(rec["content_digest"], live, rec)


@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under uv)")
class WidenedKeyShapeTests(_Base):
    def _ledger(self, invariant, handle):
        d = self.t.doctrine
        d.mkdir(parents=True, exist_ok=True)
        (d / "reconciliations.yml").write_text(
            'config_version: "1"\nreconciliations:\n'
            f"  - invariant: {invariant}\n    handle: {handle}\n"
            "    verdict: compatible\n"
            f"    content_digest: {'0' * 63}a\n    rationale: null\n    signed: 2026-08-28\n",
            encoding="utf-8")

    def test_member_a_re_accepts_both_forms_and_nothing_else(self):
        self.assertTrue(dp.MEMBER_A_RE.match("INV-0001"))
        self.assertTrue(dp.MEMBER_A_RE.match("OBS-0001/what-it-does"))
        for bad in ("OBS-0001", "ADR-0001/x", "INV-1", "OBS-0001/Bad", "inv-0001"):
            self.assertIsNone(dp.MEMBER_A_RE.match(bad), bad)

    def test_slot_a_accepts_an_observation_handle(self):
        self._ledger("OBS-0001/what-it-does", "ADR-0090/a")
        records = dp.read_reconciliations(self.t.root)
        self.assertEqual([(r["invariant"], r["handle"], r["member_kind"]) for r in records],
                         [("OBS-0001/what-it-does", "ADR-0090/a", "observation")])

    def test_slot_a_still_accepts_an_invariant_id(self):
        self._ledger("INV-0001", "ADR-0090/a")
        records = dp.read_reconciliations(self.t.root)
        self.assertEqual(records[0]["member_kind"], "invariant")

    def test_slot_b_rejects_an_observation_handle(self):
        self._ledger("INV-0001", "OBS-0001/what-it-does")
        with self.assertRaises(dp.DoctrineValidationError):
            dp.read_reconciliations(self.t.root)

    def test_slot_a_rejects_a_bare_obs_id(self):
        self._ledger("OBS-0001", "ADR-0090/a")
        with self.assertRaises(dp.DoctrineValidationError):
            dp.read_reconciliations(self.t.root)

    def test_serialize_keeps_the_on_disk_field_names(self):
        body = SR.serialize_ledger([{"invariant": "OBS-0001/o", "handle": "ADR-0090/a",
                                     "verdict": "compatible", "content_digest": "0" * 64,
                                     "rationale": None, "signed": "2026-08-28",
                                     "member_kind": "observation"}])
        self.assertIn("  - invariant: OBS-0001/o\n    handle: ADR-0090/a\n", body)
        self.assertNotIn("member_kind", body)


@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under uv)")
class SignoffAddedClassTests(_Base):
    def setUp(self):
        super().setUp()
        self.t.write_adr(90, [{"handle": "ADR-0090/a", "scope": "crux/scripts", "rule": "R"}])
        self.t.write_obs(1, entries=[{"handle": "OBS-0001/o", "rule": "O"}],
                         evidence=["crux/scripts/x.py:1-2"])
        self.t.write_source("crux/scripts/x.py")
        self.argv = ["--repo-root", str(self.t.root), "--invariant", "OBS-0001/o",
                     "--handle", "ADR-0090/a", "--verdict", "compatible",
                     "--date", "2026-08-28"]

    def test_dry_run_accepts_an_observation_slot_a_and_names_the_observation_rule(self):
        rc, out, _ = _run(SR, self.argv + ["--dry-run"])
        self.assertEqual(rc, 0, out)
        self.assertIn("observation rule text", out)
        self.assertNotIn("invariant text (verbatim", out)
        self.assertIn(dp.reconciliation_content_digest("O", "R"), out)

    def test_write_mode_signs_the_added_class_and_the_domain_believes(self):
        rc, out, _ = _run(SR, self.argv)
        self.assertEqual(rc, 0, out)
        records = dp.read_reconciliations(self.t.root)
        self.assertEqual([(r["invariant"], r["handle"]) for r in records],
                         [("OBS-0001/o", "ADR-0090/a")])
        self.assertEqual(records[0]["content_digest"], dp.reconciliation_content_digest("O", "R"))
        self.assertIn("## d — believed", (self.t.doctrine / "index.md").read_text(encoding="utf-8"))

    def test_refuses_an_observation_slot_a_that_seeds_no_pairing(self):
        self.t.write_obs(2, entries=[{"handle": "OBS-0002/p", "rule": "P"}],
                         evidence=["tools/y.py:1-2"])
        argv = [a if a != "OBS-0001/o" else "OBS-0002/p" for a in self.argv]
        rc, out, _ = _run(SR, argv + ["--dry-run"])
        self.assertEqual(rc, 1)
        self.assertIn("not a candidate pairing", out)

    def test_refuses_an_unknown_observation_handle(self):
        argv = [a if a != "OBS-0001/o" else "OBS-0009/nope" for a in self.argv]
        rc, out, _ = _run(SR, argv + ["--dry-run"])
        self.assertEqual(rc, 1)
        self.assertIn("unknown observation handle", out)

    def test_refuses_a_slot_a_outside_both_forms(self):
        argv = [a if a != "OBS-0001/o" else "OBS-0001" for a in self.argv]
        rc, out, _ = _run(SR, argv + ["--dry-run"])
        self.assertEqual(rc, 1)
        self.assertIn("slot A", out)


# ── D1: per-domain authority rendering ──────────────────────────────────────

@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under uv)")
class AuthorityRenderingTests(_Base):
    def test_mixed_domain_renders_the_suffix_and_the_evidence_table(self):
        self.t.write_adr(90, [{"handle": "ADR-0090/a", "scope": "tools"}])
        self.t.write_obs(1, entries=[{"handle": "OBS-0001/o"}],
                         evidence=["crux/scripts/x.py:1-2"])
        self.t.write_source("crux/scripts/x.py")
        index = self.t.index()
        self.assertIn("## d — no-applicable-invariant · authority: mixed", index)
        self.assertIn("_Observed evidence:_", index)
        self.assertIn("| OBS-0001/o | crux/scripts/x.py:1-2 | yes |", index)

    def test_descriptive_domain_when_every_rule_is_observed(self):
        self.t.write_adr(90, [{"handle": "ADR-0090/a", "domain": "other", "scope": "tools"}])
        self.t.write_obs(1, entries=[{"handle": "OBS-0001/o"}],
                         evidence=["crux/scripts/x.py:1-2"])
        index = self.t.index()
        self.assertIn("## d — no-applicable-invariant · authority: descriptive", index)
        self.assertIn("## other — no-applicable-invariant\n", index)

    def test_basis_is_evidence_resolves_when_every_evidence_path_resolves(self):
        self.t.write_adr(90, [{"handle": "ADR-0090/a", "domain": "other", "scope": "tools"}])
        self.t.write_obs(1, entries=[{"handle": "OBS-0001/o"}],
                         evidence=["crux/scripts/x.py:1-2", "crux/scripts/y.py:3-4"])
        self.t.write_source("crux/scripts/x.py")
        self.t.write_source("crux/scripts/y.py")
        index = self.t.index()
        self.assertIn("| OBS-0001/o | rule:o | the observed rule | OBS-0001 | ratified | observed "
                      "| evidence-resolves |", index)

    def test_basis_is_evidence_missing_when_one_evidence_path_is_missing(self):
        self.t.write_adr(90, [{"handle": "ADR-0090/a", "domain": "other", "scope": "tools"}])
        self.t.write_obs(1, entries=[{"handle": "OBS-0001/o"}],
                         evidence=["crux/scripts/x.py:1-2", "crux/scripts/missing.py:3-4"])
        self.t.write_source("crux/scripts/x.py")
        index = self.t.index()
        self.assertIn("| OBS-0001/o | rule:o | the observed rule | OBS-0001 | ratified | observed "
                      "| evidence-missing |", index)
        self.assertIn("| OBS-0001/o | crux/scripts/missing.py:3-4 | no |", index)
        self.assertIn("| OBS-0001/o | crux/scripts/x.py:1-2 | yes |", index)

    def test_observation_basis_ignores_run_bindings(self):
        self.t.write_adr(90, [{"handle": "ADR-0090/a", "domain": "other", "scope": "tools"}])
        self.t.write_obs(1, entries=[{"handle": "OBS-0001/o"}],
                         evidence=["crux/scripts/missing.py:1-2"])
        runs = self.t.tree / "promptbooks" / "runs" / "PB-0001-x"
        runs.mkdir(parents=True)
        (runs / "run-RUN-001.yaml").write_text(
            "format_version: \"1\"\nrun_id: RUN-001\nbook_id: PB-0001\nstatus: completed\n"
            "prompts:\n  - n: 1\n    state: done\n    artifacts: [OBS-0001, ADR-0090]\n",
            encoding="utf-8")
        index = self.t.index()
        self.assertIn("| OBS-0001/o | rule:o | the observed rule | OBS-0001 | ratified | observed "
                      "| evidence-missing |", index)

    def test_adr_sourced_basis_is_run_bound_when_a_run_binds_the_adr(self):
        """The ADR-sourced half of the `basis` column, driven to a RENDERED
        value (ADR-0097 part 7).

        The two observation values above were pinned as rendered strings; the
        two ADR values were pinned only as data. So the column could have
        rendered anything at all for an ADR-sourced rule and every gate stayed
        green — and part 7's whole subject is what this column publishes.
        """
        self.t.write_adr(90, [{"handle": "ADR-0090/a", "domain": "other",
                               "scope": "tools"}])
        runs = self.t.tree / "promptbooks" / "runs" / "PB-0001-x"
        runs.mkdir(parents=True)
        (runs / "run-RUN-001.yaml").write_text(
            "format_version: \"1\"\nrun_id: RUN-001\nbook_id: PB-0001\n"
            "status: completed\nprompts:\n  - n: 1\n    state: done\n"
            "    artifacts: [ADR-0090]\n", encoding="utf-8")
        self.assertIn("| ADR-0090/a | rule:a | the authored rule | ADR-0090 | Accepted | decided "
                      "| run-bound |", self.t.index())

    def test_adr_sourced_basis_is_not_run_bound_when_no_run_binds_the_adr(self):
        """The other value, and the discriminator for the test above: the SAME
        fixture minus the run snapshot must render the other string. Without
        this pair, a column hard-coded to one value would pass both."""
        self.t.write_adr(90, [{"handle": "ADR-0090/a", "domain": "other",
                               "scope": "tools"}])
        self.assertIn("| ADR-0090/a | rule:a | the authored rule | ADR-0090 | Accepted | decided "
                      "| not-run-bound |", self.t.index())

    def test_the_basis_legend_states_that_no_value_is_evidence(self):
        """The load-bearing half of ADR-0097 part 7, which had no assertion.

        Part 7 renamed the column so a reader could not mistake it for a
        verification claim, and the sentence saying so is the only thing on the
        published surface that says it. Deleting it moved no gate: the four
        values still rendered, the drift gate still compared bytes, and the
        program postcondition only asks that no row CLAIM implementation. This
        test is what makes the disclaimer a postcondition rather than a
        courtesy.
        """
        self.t.write_adr(90, [{"handle": "ADR-0090/a", "domain": "other",
                               "scope": "tools"}])
        index = self.t.index()
        self.assertIn(
            "No basis value is evidence that the rule holds", index,
            "the basis legend's disclaimer is gone — the column is a "
            "measurement, and the legend is where the index says so")
        for value in ("run-bound", "not-run-bound",
                      "evidence-resolves", "evidence-missing"):
            with self.subTest(value=value):
                self.assertIn(f"**{value}**", index,
                              "the legend must define all four values, or the "
                              "disclaimer disclaims a set the reader cannot see")

    def test_domain_without_an_observation_rule_renders_none_of_the_three(self):
        self.t.write_adr(90, [{"handle": "ADR-0090/a", "scope": "crux/scripts"}])
        index = self.t.index()
        self.assertIn("## d — no-applicable-invariant\n", index)
        self.assertNotIn("authority:", index)
        self.assertNotIn("_Observed evidence:_", index)

    def test_broken_domain_keeps_the_suffix(self):
        self.t.write_adr(90, [{"handle": "ADR-0090/a", "scope": "crux/scripts"}])
        self.t.write_obs(1, entries=[{"handle": "OBS-0001/o"}],
                         evidence=["crux/scripts/x.py:1-2"])
        index = self.t.index()
        self.assertIn("## d — BROKEN · authority: mixed", index)
        self.assertIn("| OBS-0001/o | ADR-0090/a | un-adjudicated |", index)


# ── the compile driver's observation gate ───────────────────────────────────

@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under uv)")
class ObservationDomainStateTests(unittest.TestCase):
    """A domain built from observations is never `backfilled`.

    `backfilled` means "admitted via the historic ADR backfill". An
    observation's host number is its OBS number, so `OBS-0001` reads as 1,
    which is below any `governs_from` — the backfill test would call a
    brand-new observed domain historic. `build_resolver` already guards this
    exact hazard by omitting `review_state` on observation rows.
    """

    def test_a_pure_observation_domain_is_not_backfilled(self):
        rec = _orec("OBS-0001/o", "storage", ["crux/scripts/a.py:1-2"])
        self.assertEqual(dp.domain_state([], [rec], 85), dp.STATE_NO_INVARIANT)

    def test_a_pure_adr_domain_below_the_boundary_is_still_backfilled(self):
        rec = _arec("ADR-0001/a", "storage", "crux/scripts", adr="ADR-0001")
        self.assertEqual(dp.domain_state([], [rec], 85), dp.STATE_BACKFILLED)

    def test_a_mixed_domain_evaluates_the_boundary_over_adr_records_only(self):
        adr = _arec("ADR-0001/a", "storage", "crux/scripts", adr="ADR-0001")
        obs = _orec("OBS-0001/o", "storage", ["crux/scripts/a.py:1-2"])
        self.assertEqual(dp.domain_state([], [adr, obs], 85), dp.STATE_BACKFILLED)

    def test_a_mixed_domain_above_the_boundary_is_not_backfilled(self):
        adr = _arec("ADR-0090/a", "storage", "crux/scripts", adr="ADR-0090")
        obs = _orec("OBS-0001/o", "storage", ["crux/scripts/a.py:1-2"])
        self.assertEqual(dp.domain_state([], [adr, obs], 85), dp.STATE_NO_INVARIANT)


class CompileDriverGateTests(unittest.TestCase):
    def test_adr_less_tree_compiles_doctrine_from_observations_alone(self):
        """Requirement 6: a tree may enable observations and never enable ADRs.

        The standalone doctrine drift gate — what `check-drift` and CHK-DRIFT-1
        invoke — must work on that tree. It only appears to work under
        `survey.py --phase project` because `summarize-adrs.py` runs first and
        creates `adrs/summaries/` as a side effect.
        """
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        t = _Tree(Path(tmp.name), concerns=("observations",))
        shutil.rmtree(t.adrs)
        rc, out, err = _run(CD, ["--repo-root", str(t.root), "--dry-run"])
        self.assertNotEqual(rc, 2, f"ADR-less tree must not be the environment lane: {err}")

    def test_adr_less_tree_compiles_the_observation_domain_itself(self):
        """M-TEST-4. The test above asserts only `rc != 2`, which an ADR-less
        tree with an EMPTY doctrine also satisfies — it cannot tell "compiles
        from observations alone" from "compiles nothing". This one gives the
        tree a ratified observation and asserts the domain it produces."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        t = _Tree(Path(tmp.name), concerns=("observations",))
        shutil.rmtree(t.adrs)
        t.write_source("crux/scripts/a.py")
        t.write_obs(10, entries=[{"handle": "OBS-0010/what-it-does",
                                  "domain": "storage"}],
                    evidence=["crux/scripts/a.py:1-2"])
        rc, out, err = _run(CD, ["--repo-root", str(t.root)])
        self.assertEqual(rc, 0, f"stdout={out} stderr={err}")
        index = (t.doctrine / "index.md").read_text(encoding="utf-8")
        self.assertIn("## storage", index)
        self.assertIn("authority: descriptive", index)
        self.assertIn("_Observed evidence:_", index)
        self.assertIn("crux/scripts/a.py:1-2", index)
        self.assertIsNone(re.search(r"ADR-\d{4}", index),
                          "an ADR-less tree's doctrine names no ADR")

    def test_enabled_concern_with_absent_directory_is_the_environment_lane(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        t = _Tree(Path(tmp.name), make_obs=False)
        t.write_adr(90, [{"handle": "ADR-0090/a"}])
        rc, out, err = _run(CD, ["--repo-root", str(t.root), "--dry-run"])
        self.assertEqual(rc, 2, (out, err))
        self.assertIn("observations", err)

    def test_disabled_concern_stamps_a_null_observations_digest(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        t = _Tree(Path(tmp.name), concerns=("adrs", "invariants"), make_obs=False)
        t.write_adr(90, [{"handle": "ADR-0090/a"}])
        meta = json.loads(t.build()["_meta.json"])
        self.assertIsNone(meta["observations_sha256"])
        self.assertIsNone(meta["survey_receipts_sha256"])
        # Hard-coded literal, and the OLD value asserted absent, so a
        # regression to "3" fails rather than being tolerated.
        self.assertEqual(meta["schema"], "4")
        self.assertNotEqual(meta["schema"], "3")


# ── P1: the zero-observation identity, three legs ───────────────────────────

@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under uv)")
class P1ZeroObservationIdentityTests(unittest.TestCase):
    """ADR-0095's postcondition: with zero ratified observations the widening
    contributes nothing to doctrine. It is a claim about the WIDENING, not
    about the projection never moving — it moved in this run for unrelated
    reasons (run-snapshot bindings turning ADR-0095 implemented, ADR-0096's
    handles), so no leg compares against committed bytes."""

    # The provenance stamps that record WHICH inputs the compile read. Both
    # are null when the observations source is suppressed, so both differ
    # between the two builds — and neither is content the widening contributed.
    PROVENANCE_LINE = "- `observations_sha256`: "
    PROVENANCE_LINES = ("- `observations_sha256`: ",
                        "- `survey_receipts_sha256`: ")

    def _live_builds(self):
        require_dev_surface(self, REPO_ROOT / "bionic" / "manifest.yml", "bionic/manifest.yml")
        manifest = sp.read_manifest(REPO_ROOT)
        obs = sp.observations_dir(REPO_ROOT)
        projecting = [p for p in sp.observation_paths(obs)
                      if sp.read_frontmatter(p.read_text(encoding="utf-8")).get("status")
                      == sp.OBSERVATION_PROJECTED_STATUS]
        if projecting:
            self.skipTest("P1 is a zero-ratified-observation postcondition; the live "
                          f"tree holds {len(projecting)} ratified record(s)")
        suppressed = dict(manifest)
        suppressed["concerns_enabled"] = [c for c in manifest["concerns_enabled"]
                                          if c != "observations"]
        read = {p.name: b for p, b in CD.build(REPO_ROOT, manifest=manifest).items()}
        held = {p.name: b for p, b in CD.build(REPO_ROOT, manifest=suppressed).items()}
        return read, held

    def test_p1a_index_is_byte_equal_with_and_without_the_source(self):
        """Every content line of index.md is identical between the two builds.
        The one line allowed to differ is the provenance stamp of the input
        itself (`observations_sha256`: the empty-corpus digest when read,
        null when suppressed) — that line records WHICH inputs were read, not
        content the widening contributed, and P1b pins its value."""
        read, held = self._live_builds()
        r_lines = read["index.md"].splitlines()
        h_lines = held["index.md"].splitlines()
        self.assertEqual(len(r_lines), len(h_lines))
        differing = [(a, b) for a, b in zip(r_lines, h_lines) if a != b]
        self.assertEqual(len(differing), 1, differing)
        for a, b in differing:
            self.assertTrue(a.startswith(self.PROVENANCE_LINES), differing)
            self.assertTrue(b.startswith(self.PROVENANCE_LINES), differing)
        content_r = [l for l in r_lines if not l.startswith(self.PROVENANCE_LINES)]
        content_h = [l for l in h_lines if not l.startswith(self.PROVENANCE_LINES)]
        self.assertEqual(content_r, content_h)

    def test_p1b_meta_digests_unchanged_and_only_observations_sha256_added(self):
        """The `schema` bump is a deliberate schema change (the provenance
        block would otherwise omit an input the compile reads), not the
        widening contributing content; the pre-existing digests are what the
        identity claim covers.

        "1" -> "2" recorded `observations_sha256` (ADR-0095); "2" -> "3"
        records `survey_receipts_sha256` (ADR-0098 clause 2), which joins the
        input domain for the same reason and moves the marker for the same
        reason. The literal below is the CURRENT schema, not the history."""
        read, held = self._live_builds()
        m_read, m_held = json.loads(read["_meta.json"]), json.loads(held["_meta.json"])
        for key in ("adr_frontmatter_sha256", "invariants_sha256",
                    "reconciliations_sha256", "governs_from"):
            self.assertEqual(m_read[key], m_held[key], key)
        self.assertEqual(set(m_read) - {"adr_frontmatter_sha256", "invariants_sha256",
                                        "reconciliations_sha256", "governs_from",
                                        "schema", "tool"},
                         {"observations_sha256", "survey_receipts_sha256"})
        self.assertEqual(m_read["observations_sha256"], EMPTY_CORPUS_SHA256)
        self.assertIsNone(m_held["observations_sha256"])
        self.assertEqual(m_read["schema"], "4")
        self.assertNotEqual(m_read["schema"], "3")

    def test_p1c_one_pairing_observation_changes_the_compile_and_its_removal_restores_it(self):
        """The anti-vacuity leg: without it P1a and P1b also pass against dead
        code. One ratified observation that genuinely pairs (same domain,
        evidence inside the authored rule's scope) must BREAK the domain on
        an un-adjudicated pairing; removing it must restore the bytes."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        t = _Tree(Path(tmp.name))
        t.write_adr(90, [{"handle": "ADR-0090/a", "scope": "crux/scripts"}])
        t.write_source("crux/scripts/x.py")
        before = t.build()
        self.assertIn("## d — no-applicable-invariant\n", before["index.md"])
        p = t.write_obs(1, entries=[{"handle": "OBS-0001/o"}],
                        evidence=["crux/scripts/x.py:1-2"])
        during = t.build()
        self.assertNotEqual(before["index.md"], during["index.md"])
        self.assertNotEqual(before["_meta.json"], during["_meta.json"])
        self.assertIn("## d — BROKEN · authority: mixed", during["index.md"])
        self.assertIn("| OBS-0001/o | ADR-0090/a | un-adjudicated |", during["index.md"])
        p.unlink()
        self.assertEqual(t.build(), before)


# ── P4: no persisted evidence carries a code excerpt ────────────────────────

@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under uv)")
class P4NoCodeExcerptTests(unittest.TestCase):
    def test_every_evidence_cell_reaching_index_md_matches_the_grammar(self):
        import re
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        t = _Tree(Path(tmp.name))
        t.write_adr(90, [{"handle": "ADR-0090/a", "domain": "other"}])
        t.write_obs(1, entries=[{"handle": "OBS-0001/o"}],
                    evidence=["crux/scripts/x.py:1-2", "tools/y.py:10-12"])
        index = t.index()
        rows = [l for l in index.splitlines() if l.startswith("| OBS-0001/o | ") and l.count("|") == 4]
        self.assertEqual(len(rows), 2, index)
        for row in rows:
            cell = row.split("|")[2].strip()
            self.assertRegex(cell, EVIDENCE_RE)

    def test_live_records_carry_only_grammar_conformant_evidence(self):
        import re
        manifest = sp.read_manifest(REPO_ROOT)
        records = sp.collect_records(sp.adrs_dir(REPO_ROOT),
                                     governs_from=sp.governs_from(manifest),
                                     observations=sp.observations_dir(REPO_ROOT))
        for r in records:
            for ev in r["evidence"]:
                self.assertRegex(ev, EVIDENCE_RE)

    def test_no_doctrine_artifact_line_carries_a_fenced_code_block(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        t = _Tree(Path(tmp.name))
        t.write_adr(90, [{"handle": "ADR-0090/a"}])
        t.write_obs(1, entries=[{"handle": "OBS-0001/o"}])
        for name, body in t.build().items():
            for line in body.splitlines():
                self.assertFalse(line.lstrip().startswith("```"), (name, line))
        require_dev_surface(self, REPO_ROOT / "bionic" / "adrs" / "doctrine" / "index.md",
                            "bionic/adrs/doctrine/index.md")
        for name in ("index.md", "_meta.json"):
            body = (REPO_ROOT / "bionic" / "adrs" / "doctrine" / name).read_text(encoding="utf-8")
            for line in body.splitlines():
                self.assertFalse(line.lstrip().startswith("```"), (name, line))


# ── the two doctrine lanes must read the same input domain ──────────────────

@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under uv)")
class LaneAgreementTests(_Base):
    """`compile-doctrine.py` (the regenerator) and `check-doctrine-reconciliation.py`
    (the CI consistency gate) must resolve the SAME input domain.

    They are two readers of one projection, and the gate is what turns a BROKEN
    domain red in CI. A gate that reads a narrower domain than the regenerator
    reports clean on a tree the regenerator marks BROKEN — a false green, and
    the worst failure mode a gate has. The observations source is the domain
    that widened (ADR-0095 requirement 5), so it is the one this pins.
    """

    def _tree_with_an_unadjudicated_pairing(self):
        """An authored rule and a ratified observation that pair on all three
        conjuncts, with no reconciliation record — so the domain is BROKEN."""
        self.t.write_source("crux/scripts/x.py")
        self.t.write_adr(90, [{"handle": "ADR-0090/authored", "domain": "shared",
                               "scope": "crux/scripts/x.py"}])
        self.t.write_obs(1, entries=[{"handle": "OBS-0001/observed", "domain": "shared"}],
                         evidence=["crux/scripts/x.py:1-10"])

    def test_the_regenerator_marks_the_domain_broken(self):
        self._tree_with_an_unadjudicated_pairing()
        self.assertIn("shared — BROKEN", self.t.index())

    def test_the_gate_agrees_with_the_regenerator(self):
        """The gate must fail the same tree the regenerator marks BROKEN."""
        self._tree_with_an_unadjudicated_pairing()
        CDR = _load("check_doctrine_reconciliation_lane", "check-doctrine-reconciliation.py")
        rc, out, _ = _run(CDR, ["--repo-root", str(self.t.root)])
        payload = json.loads(out)
        self.assertEqual(
            [d["domain"] for d in payload["broken_domains"]], ["shared"],
            "check-doctrine-reconciliation reported clean on a tree "
            "compile-doctrine marks BROKEN — the gate reads a narrower input "
            "domain than the regenerator, which is a false green")
        self.assertEqual(rc, 1)

    def test_both_lanes_are_clean_when_the_pairing_is_absent(self):
        """The anti-vacuity leg: with no pairing observation both lanes pass,
        so the assertion above is driven by the pairing and not by the fixture."""
        self.t.write_source("crux/scripts/x.py")
        self.t.write_adr(90, [{"handle": "ADR-0090/authored", "domain": "shared",
                               "scope": "crux/scripts/x.py"}])
        # Scoped to the domain heading: the word BROKEN also appears in the
        # static state legend every index carries, so a whole-document
        # substring check would pass vacuously.
        self.assertIn("## shared — no-applicable-invariant", self.t.index())
        CDR = _load("check_doctrine_reconciliation_lane", "check-doctrine-reconciliation.py")
        rc, out, _ = _run(CDR, ["--repo-root", str(self.t.root)])
        self.assertEqual(json.loads(out)["broken_domains"], [])
        self.assertEqual(rc, 0)


# ── ADR-0099 clause 2: the index renders the citation beside the handle ──────

# A rules-table row whose second cell is a citation. The evidence and pairing
# tables never match: their second cell is a path or a handle, not `rule:`.
_CITATION_ROW_RE = re.compile(r"^\| (?P<handle>[^|]+) \| rule:(?P<slug>[^|]+) \|")


@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under uv)")
class CitationRenderingTests(_Base):
    """`rule:<slug>` beside the handle in every rules-table row (ADR-0099
    clause 2; governing handle `ADR-0099/slug-uniqueness-gate`).

    The slug is `sp.slug_of(handle)` — the ONE split the summaries projection,
    the doctrine index, and the survey sign-off share. Every test below pins
    the rendered slug to that helper, never to a second speller, so the two
    cannot drift apart. Every absence claim carries a positive control: an
    empty render must fail, not pass.
    """

    @staticmethod
    def _citation_rows(index: str) -> list[tuple[str, str]]:
        """`(handle, slug)` for every rules-table row carrying a citation."""
        matches = (_CITATION_ROW_RE.match(ln) for ln in index.splitlines())
        return [(m.group("handle"), m.group("slug")) for m in matches if m]

    def test_the_header_names_the_citation_column_beside_the_handle(self):
        self.t.write_adr(90, [{"handle": "ADR-0090/a"}])
        self.assertIn("| handle | citation | rule | source ADR | source_status | disposition | basis |",
                      self.t.index())

    def test_an_adr_handle_renders_its_citation_beside_the_handle(self):
        self.t.write_adr(90, [{"handle": "ADR-0090/slug-gate"}])
        self.assertIn("| ADR-0090/slug-gate | rule:slug-gate | the authored rule "
                      "| ADR-0090 | Accepted | decided | not-run-bound |", self.t.index())

    def test_an_observation_handle_renders_its_citation_the_same_way(self):
        """ADR-0099 clause 1: the form is identical on every surface, so an
        `OBS-NNNN/slug` row carries `rule:<slug>` exactly as an ADR row does."""
        self.t.write_adr(90, [{"handle": "ADR-0090/a", "domain": "other", "scope": "tools"}])
        self.t.write_obs(1, entries=[{"handle": "OBS-0001/seen-rule"}],
                         evidence=["crux/scripts/x.py:1-2"])
        self.t.write_source("crux/scripts/x.py")
        self.assertIn("| OBS-0001/seen-rule | rule:seen-rule | the observed rule "
                      "| OBS-0001 | ratified | observed | evidence-resolves |", self.t.index())

    def test_every_rendered_slug_is_slug_of_its_handle(self):
        self.t.write_adr(90, [{"handle": "ADR-0090/one-two"},
                              {"handle": "ADR-0090/three", "domain": "other"}])
        self.t.write_obs(1, entries=[{"handle": "OBS-0001/four-five-six"}],
                         evidence=["crux/scripts/x.py:1-2"])
        self.t.write_source("crux/scripts/x.py")
        rows = self._citation_rows(self.t.index())
        # Positive control: three rules in, three citation rows out.
        self.assertEqual(sorted(h for h, _ in rows),
                         ["ADR-0090/one-two", "ADR-0090/three", "OBS-0001/four-five-six"])
        for handle, slug in rows:
            self.assertEqual(slug, sp.slug_of(handle), handle)

    def test_the_citation_carries_no_adr_number_and_the_handle_beside_it_does(self):
        self.t.write_adr(90, [{"handle": "ADR-0090/gate"}])
        index = self.t.index()
        rows = self._citation_rows(index)
        # Positive control: the row renders, and the number sits in the handle
        # cell. Without this, an empty index satisfies both absences below.
        self.assertEqual(rows, [("ADR-0090/gate", "gate")])
        self.assertNotIn("0090", rows[0][1])
        self.assertNotIn("rule:ADR-", index)

    def test_the_citation_cell_is_markdown_escaped(self):
        """`build_index` escapes every cell through `_md_escape`; the citation
        is a cell, so a pipe in the slug cannot open a column."""
        def entry(handle):
            return {"domain": "d", "state": "believed", "authority": None,
                    "rules": [{"handle": handle, "rule": "a rule",
                               "source_adr": "ADR-0001", "disposition": "held",
                               "basis": "run-bound"}],
                    "evidence": [], "pairings": []}
        forged = dp.build_index([entry("ADR-0001/a|b")], [], {})
        self.assertIn("| rule:a\\|b |", forged)
        self.assertNotIn("| rule:a|b |", forged)
        # Positive control: a clean slug renders unescaped.
        self.assertIn("| rule:a-b |", dp.build_index([entry("ADR-0001/a-b")], [], {}))

    def test_the_live_tree_renders_a_citation_on_every_rule_row(self):
        """Built in memory from the repository; nothing is written. The
        expected handle set is DERIVED from the live records on disk, never
        frozen as a count."""
        require_dev_surface(self, REPO_ROOT / "bionic" / "manifest.yml", "bionic/manifest.yml")
        manifest = sp.read_manifest(REPO_ROOT)
        live = {r["handle"] for r in sp.live_records(sp.collect_records(
            sp.adrs_dir(REPO_ROOT), governs_from=sp.governs_from(manifest),
            observations=sp.observations_dir(REPO_ROOT)))}
        built = {p.name: body for p, body in CD.build(REPO_ROOT, manifest=manifest).items()}
        rows = self._citation_rows(built["index.md"])
        self.assertTrue(live, "the live tree carries no governs rule; the test is vacuous")
        self.assertEqual({h for h, _ in rows}, live)
        for handle, slug in rows:
            self.assertEqual(slug, sp.slug_of(handle), handle)


if __name__ == "__main__":
    unittest.main(verbosity=2)
