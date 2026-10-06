"""Tests for decision recovery (ADR-0062, SP-3). Each names its acceptance criterion."""

import importlib
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
REPO = SCRIPTS.parents[1]
sys.path.insert(0, str(SCRIPTS))
R = importlib.import_module("crux.arch.recover")
D = importlib.import_module("crux.arch.derive")


class ClassifyTests(unittest.TestCase):
    # AC-1 — pure three-way classifier.
    def test_load_bearing(self):
        self.assertEqual(R.classify(["schema", "meta"]), "load_bearing")
        self.assertEqual(R.classify(["api"]), "load_bearing")

    def test_not_load_bearing(self):
        self.assertEqual(R.classify(["meta", "process", "docs"]), "not_load_bearing")

    def test_unclassifiable(self):
        self.assertEqual(R.classify([]), "unclassifiable")
        self.assertEqual(R.classify(["nonsense-tag"]), "unclassifiable")

    def test_curated_keep(self):
        self.assertTrue(R.curated_keep(["schema"]))       # load_bearing
        self.assertTrue(R.curated_keep(["nonsense"]))     # unclassifiable → fail-open
        self.assertFalse(R.curated_keep(["meta", "docs"]))  # not_load_bearing → dropped

    def test_pure_no_fs(self):
        # classify must not touch the filesystem (AC-1)
        self.assertEqual(R.classify(["cli"]), "load_bearing")


class IdentityTests(unittest.TestCase):
    # AC-3 — identity keys on the anchor, never LLM output.
    def test_stable_across_signal_and_prose(self):
        a = R.candidate_id("external-dependency", "anthropic")
        b = R.candidate_id("external-dependency", "anthropic")
        self.assertEqual(a, b)
        self.assertEqual(len(a), 16)

    def test_distinct_anchors(self):
        self.assertNotEqual(
            R.candidate_id("external-dependency", "anthropic"),
            R.candidate_id("external-dependency", "openai"),
        )

    def test_canonical_forms(self):
        self.assertEqual(R.canonical_anchor("module-boundary", src="a", dst="b"), "a->b")
        self.assertEqual(R.canonical_anchor("api-contract", method="GET", route="/x"), "GET /x")
        self.assertEqual(R.canonical_anchor("api-contract", module="m", symbol="S"), "m:S")
        self.assertEqual(R.canonical_anchor("external-dependency", name="dep"), "dep")


class StateFileTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.p = Path(self.d.name) / "state.yml"

    def tearDown(self):
        self.d.cleanup()

    def test_upsert_and_suppress(self):
        sf = R.StateFile(self.p)
        cid = R.candidate_id("external-dependency", "anthropic")
        self.assertTrue(sf.upsert_observed({"id": cid, "anchor_kind": "external-dependency",
                                            "canonical_anchor": "anthropic"}))
        # re-scan: same id already present → not re-emitted (AC-3)
        self.assertFalse(sf.upsert_observed({"id": cid}))
        sf.dispose(cid, "rejected")
        sf.save()
        # reload; a rejected id stays suppressed
        sf2 = R.StateFile(self.p)
        self.assertFalse(sf2.upsert_observed({"id": cid}))

    def test_atomic_save_roundtrip(self):
        sf = R.StateFile(self.p)
        sf.upsert_observed({"id": "abc", "anchor_kind": "k", "canonical_anchor": "c"})
        sf.save()
        self.assertTrue(self.p.exists())
        self.assertEqual(R.StateFile(self.p).rows["abc"]["canonical_anchor"], "c")

    def test_fail_closed_on_corrupt(self):
        self.p.write_text("candidates: [unterminated\n")
        with self.assertRaises(ValueError):
            R.StateFile(self.p)

    def test_prune_anchor_conditioned(self):
        sf = R.StateFile(self.p)
        sf.upsert_observed({"id": "gone", "anchor_kind": "k", "canonical_anchor": "c"})
        sf.dispose("gone", "rejected")
        sf.upsert_observed({"id": "here", "anchor_kind": "k", "canonical_anchor": "d"})
        sf.dispose("here", "rejected")
        sf.prune(present_anchor_ids={"here"})   # "here" still in code, "gone" isn't
        self.assertNotIn("gone", sf.rows)       # dropped (anchor absent)
        self.assertIn("here", sf.rows)          # kept (still-present → suppressed)


class RatifyCorrelationTests(unittest.TestCase):
    # AC-5 — idempotent ratify via recovered_id.
    def test_find_ratified_adr(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        adrs = Path(d.name)
        (adrs / "ADR-0099-x.md").write_text(
            "---\nid: ADR-0099\nstatus: Proposed\nrecovered_id: cafef00d\n---\n# body\n")
        self.assertEqual(R.find_ratified_adr(adrs, "cafef00d"), "ADR-0099")
        self.assertIsNone(R.find_ratified_adr(adrs, "deadbeef"))


OBS_TEMPLATE = REPO / "crux" / "templates" / "OBS-template.md"

# Line-anchored placeholder -> value. Rendering the fixture FROM the shipped
# template is the whole point: the previous hand-written fixture wrote
# `anchor_id` UNQUOTED while the template quotes it, and that one divergence
# hid a reader that missed every real record. A fixture that does not model the
# artifact it stands for proves nothing, so this one is generated from the
# artifact and guarded by `TemplateFixtureTests` below.
_OBS_SUBS = (
    (r"^id: OBS-NNNN$", "id: {oid}"),
    (r'^title: ".*"$', 'title: "T{num}"'),
    (r"^status: observed$", "status: {status}"),
    (r"^date: YYYY-MM-DD$", "date: 2026-08-01"),
    (r"^observed_date: YYYY-MM-DD$", "observed_date: 2026-08-01"),
    (r"^ratified_date: null$", "ratified_date: {ratified_date}"),
    (r"^provenance: <provenance>$", "provenance: {provenance}"),
    (r'^evidence: \[".*"\]$', "evidence: {evidence}"),
    (r'^anchor_id: ".*"$', 'anchor_id: "{anchor_id}"'),
    (r"^tags: \[.*\]$", "tags: [survey]"),
    (r"^  - domain: .*$", "  - domain: d"),
    (r'^    rule: ".*"$', '    rule: "{rule}"'),
    (r"^    scope: .*$", "    scope: s"),
    (r"^    handle: OBS-NNNN/<rule-slug>$", "    handle: {oid}/r"),
    (r"^    provenance: <provenance>$", "    provenance: {provenance}"),
)


def render_obs(num: int, anchor_id: str, rule: str, evidence,
               status="ratified", provenance="recovered") -> str:
    """Render one observation record FROM `crux/templates/OBS-template.md`, the
    artifact both on-ramps actually fill in (operational schema §17.1 owns the
    field set). Substitution is line-anchored, so a template field that is
    renamed or reformatted stops matching and `TemplateFixtureTests` fails
    rather than the fixture silently drifting."""
    text = OBS_TEMPLATE.read_text(encoding="utf-8")
    values = {
        "oid": f"OBS-{num:04d}", "num": num, "status": status,
        "provenance": provenance, "anchor_id": anchor_id, "rule": rule,
        "evidence": "[" + ", ".join(f'"{e}"' for e in evidence) + "]",
        "ratified_date": "2026-08-02" if status == "ratified" else "null",
    }
    for pattern, replacement in _OBS_SUBS:
        text, n = re.subn(pattern, replacement.format(**values), text,
                          count=1, flags=re.MULTILINE)
        if n != 1:
            raise AssertionError(
                f"OBS-template.md no longer carries a line matching {pattern!r}; "
                "the fixture renderer has drifted from the template")
    return text


def _write_obs(obs_dir: Path, num: int, anchor_id: str, rule: str, evidence,
               status="ratified", provenance="recovered"):
    (obs_dir / f"OBS-{num:04d}-x.md").write_text(
        render_obs(num, anchor_id, rule, evidence, status, provenance),
        encoding="utf-8")


class TemplateFixtureTests(unittest.TestCase):
    """The fixture is generated from the shipped template; these guard the
    generation. Without them a template edit would silently produce a fixture
    that still parses but no longer models a real record."""

    def test_no_placeholder_survives_rendering(self):
        rendered = render_obs(7, "cafef00dcafef00d", "r", ["a.py:1-2"])
        fm = re.match(r"^---\n(.*?)\n---\n", rendered, re.DOTALL).group(1)
        for token in ("NNNN", "YYYY-MM-DD", "<provenance>", "<anchor_id>",
                      "<rule-slug>", "<tag1>"):
            self.assertNotIn(token, fm, f"placeholder {token!r} survived rendering")

    def test_rendered_keyset_equals_the_template_keyset(self):
        import yaml
        def keys(text):
            fm = yaml.safe_load(re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL).group(1))
            return set(fm), set(fm["governs"][0])
        self.assertEqual(keys(render_obs(7, "cafef00dcafef00d", "r", ["a.py:1-2"])),
                         keys(OBS_TEMPLATE.read_text(encoding="utf-8")))

    def test_the_template_quotes_anchor_id_and_so_does_the_fixture(self):
        # The M-COR-1 shape, pinned at both ends.
        self.assertRegex(OBS_TEMPLATE.read_text(encoding="utf-8"),
                         r'(?m)^anchor_id: "<anchor_id>"$')
        self.assertRegex(render_obs(7, "cafef00dcafef00d", "r", ["a.py:1-2"]),
                         r'(?m)^anchor_id: "cafef00dcafef00d"$')


class ObservationCorrelationTests(unittest.TestCase):
    # ADR-0095 req. 3(a) — idempotent `ratify --as observation` correlates on anchor_id.
    def test_find_ratified_observation_hit_and_miss(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        obs = Path(d.name)
        _write_obs(obs, 7, "cafef00dcafef00d", "r", ["a.py:1-2"], status="observed")
        self.assertEqual(R.find_ratified_observation(obs, "cafef00dcafef00d"), "OBS-0007")
        self.assertIsNone(R.find_ratified_observation(obs, "deadbeefdeadbeef"))

    def test_find_ratified_observation_missing_dir(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.assertIsNone(R.find_ratified_observation(Path(d.name) / "nope", "cafef00d"))

    def test_the_template_quoted_anchor_id_correlates(self):
        """M-COR-1. `OBS-template.md` writes `anchor_id` QUOTED and both
        on-ramps fill from it, so a reader that requires the bare spelling
        misses every record this tree can produce. The fixture is rendered from
        that template, so this test is what a real record does."""
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        obs = Path(d.name)
        aid = "cafef00dcafef00d"
        _write_obs(obs, 7, aid, "r", ["a.py:1-2"])
        raw = (obs / "OBS-0007-x.md").read_text(encoding="utf-8")
        self.assertIn(f'anchor_id: "{aid}"', raw, "fixture must reach the code under test")
        self.assertEqual(R.find_ratified_observation(obs, aid), "OBS-0007")

    def _write_raw_anchor(self, obs: Path, num: int, spelling: str):
        """A record whose `anchor_id` is spelled some way OTHER than the way the
        template spells it — the whole class the quoting defect came from."""
        raw = render_obs(num, "x", "r", ["a.py:1-2"]).replace(
            'anchor_id: "x"', f"anchor_id: {spelling}")
        (obs / f"OBS-{num:04d}-x.md").write_text(raw, encoding="utf-8")

    def test_an_unquoted_all_digit_anchor_correlates_too(self):
        """The other half of the same defect: an unquoted all-digit anchor
        YAML-resolves to an INT, so a reader that compares the parsed value
        against a string misses a record it should find. Comparing stringified
        values is what makes this one correlate."""
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        obs = Path(d.name)
        self._write_raw_anchor(obs, 8, "1234567890123456")
        self.assertEqual(
            R.find_ratified_observation(obs, "1234567890123456"), "OBS-0008")

    def test_a_leading_zero_unquoted_anchor_misses_and_that_is_fail_closed(self):
        """The honest floor. YAML 1.1 reads `0000000000000002` as OCTAL, so the
        anchor's own characters are destroyed at parse time and no reader can
        recover them — this is why the vacuous `retired` guard was vacuous. The
        correct behaviour is to MISS, not to guess: such a record fails
        CHK-OBS-ANCHOR (`^[0-9a-f]{16}$` against the parsed value) and audit
        reports it BROKEN. A reader that guessed would correlate a candidate
        onto a record the tree has already declared broken."""
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        obs = Path(d.name)
        self._write_raw_anchor(obs, 9, "0000000000000002")
        self.assertIsNone(R.find_ratified_observation(obs, "0000000000000002"))
        self.assertEqual(R.read_recorded_observations(obs), {
            "2": {"id": "OBS-0009", "status": "ratified", "rule": "r",
                  "evidence": ["a.py:1-2"]}},
            "the reader surfaces what YAML actually produced, unguessed")

    def test_successor_correlation_is_exact(self):
        """M-COR-3. The successor key is `<anchor_id>+<n>` — anchor plus a
        state-file counter, no prose — so the anchor is recoverable from it and
        the predecessor's id (carried on every successor row) separates the two
        records on that anchor. Before the successor's record exists the check
        misses; after it exists the check finds THAT record, not the
        predecessor's."""
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        obs = Path(d.name)
        aid = "cafef00dcafef00d"
        _write_obs(obs, 7, aid, "the original rule", ["a.py:1-2"], status="ratified")
        sid = R.successor_id(aid, 1)
        self.assertEqual(sid, aid + "+1")
        self.assertEqual(R.anchor_of(sid), aid)
        # the predecessor's record is on this anchor and must NOT answer for the successor
        self.assertIsNone(R.find_ratified_observation(obs, sid, predecessor_id="OBS-0007"))
        # ... and correlating without the predecessor id still finds the predecessor
        self.assertEqual(R.find_ratified_observation(obs, sid), "OBS-0007")
        # once the successor's record exists, the check finds exactly it
        _write_obs(obs, 9, aid, "a CHANGED rule", ["a.py:1-2"], status="observed")
        self.assertEqual(
            R.find_ratified_observation(obs, sid, predecessor_id="OBS-0007"), "OBS-0009")

    def test_the_closed_limit_is_recorded_where_a_caller_reads_it(self):
        self.assertIn("EXACT for both candidate forms",
                      R.find_ratified_observation.__doc__)
        skill = (REPO / "crux" / "skills" / "transition-decision"
                 / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("predecessor_id", skill)
        # the stale claim must be GONE, not merely joined by a correct one
        self.assertNotIn("does not cover a successor candidate", skill)
        # the DECLARED limit is gone; the docstring may still narrate it as history
        self.assertNotIn("KNOWN LIMIT — plain candidates only",
                         R.find_ratified_observation.__doc__)


class ObservationDedupTests(unittest.TestCase):
    # ADR-0095 req. 2 — the immutable claim, the successor candidate, the stale signal.
    # §17.2: no function here creates, mutates, or deletes an observation file.
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)
        root = Path(self.d.name)
        self.obs = root / "observations"
        self.obs.mkdir()
        self.state = root / "state.yml"
        self.aid = R.candidate_id("external-dependency", "anthropic")
        _write_obs(self.obs, 1, self.aid, "The gateway is anthropic", ["a.py:1-2"])
        self.snapshot = self._snap()

    def _snap(self):
        return {p.name: p.read_bytes() for p in sorted(self.obs.iterdir())}

    def _cand(self, rule="The gateway is anthropic", evidence=("a.py:1-2",)):
        return {"id": self.aid, "anchor_kind": "external-dependency",
                "canonical_anchor": "anthropic", "rule": rule, "evidence": list(evidence)}

    def test_reader_collects_by_anchor_id(self):
        rec = R.read_recorded_observations(self.obs)
        self.assertEqual(set(rec), {self.aid})
        self.assertEqual(rec[self.aid]["id"], "OBS-0001")
        self.assertEqual(rec[self.aid]["status"], "ratified")
        self.assertEqual(rec[self.aid]["evidence"], ["a.py:1-2"])
        self.assertIn("anthropic", rec[self.aid]["rule"])
        self.assertEqual(R.read_recorded_observations(self.obs / "absent"), {})

    def test_unchanged_remine_proposes_no_candidate(self):
        sf = R.StateFile(self.state)
        rec = R.read_recorded_observations(self.obs)
        self.assertEqual(R.emit_candidate(sf, rec, self._cand()), "recorded")
        self.assertEqual(sf.rows, {})

    def test_changed_rule_opens_exactly_one_successor(self):
        sf = R.StateFile(self.state)
        rec = R.read_recorded_observations(self.obs)
        self.assertEqual(R.emit_candidate(sf, rec, self._cand(rule="The gateway is openrouter")),
                         "successor")
        self.assertEqual(len(sf.rows), 1)
        row = next(iter(sf.rows.values()))
        self.assertEqual(row["state"], "observed")
        self.assertEqual(row["anchor_id"], self.aid)
        self.assertEqual(row["predecessor_id"], "OBS-0001")
        self.assertNotEqual(row["id"], self.aid)          # distinct state-file key

    def test_changed_evidence_opens_exactly_one_successor(self):
        sf = R.StateFile(self.state)
        rec = R.read_recorded_observations(self.obs)
        self.assertEqual(R.emit_candidate(sf, rec, self._cand(evidence=["a.py:10-12"])),
                         "successor")
        self.assertEqual(len(sf.rows), 1)
        self.assertEqual(next(iter(sf.rows.values()))["predecessor_id"], "OBS-0001")

    def test_identical_remines_are_idempotent(self):
        sf = R.StateFile(self.state)
        rec = R.read_recorded_observations(self.obs)
        R.emit_candidate(sf, rec, self._cand(rule="changed"))
        sf.save()
        sf2 = R.StateFile(self.state)                      # a second, separate re-mine
        self.assertEqual(R.emit_candidate(sf2, rec, self._cand(rule="changed")), "present")
        self.assertEqual(len(sf2.rows), 1)
        self.assertEqual(sf2.successor_counters, {self.aid: 1})   # no second issue

    def test_successor_identity_carries_no_prose(self):
        """M-COR-3. The key is `<anchor_id>+<n>`, a pure function of the anchor
        and the state-file counter — ADR-0062 Decision 3's 'no LLM input'."""
        self.assertEqual(R.successor_id(self.aid, 1), f"{self.aid}+1")
        self.assertEqual(R.successor_id(self.aid, 2), f"{self.aid}+2")
        self.assertEqual(R.anchor_of(R.successor_id(self.aid, 7)), self.aid)
        self.assertEqual(R.anchor_of(self.aid), self.aid)
        sf = R.StateFile(self.state)
        self.assertEqual([sf.next_successor_n(self.aid) for _ in range(3)], [1, 2, 3])
        sf.save()
        self.assertEqual(R.StateFile(self.state).next_successor_n(self.aid), 4)

    def test_rephrasings_of_one_claim_do_not_grow_the_state_file(self):
        """M-COR-3, the measured half. Three phrasings of one changed claim
        previously produced three permanent `observed` rows, all on one anchor,
        that `prune` kept while the anchor persisted. At most one successor is
        open per anchor now; a later phrasing refreshes that row in place."""
        sf = R.StateFile(self.state)
        rec = R.read_recorded_observations(self.obs)
        self.assertEqual(R.emit_candidate(sf, rec, self._cand(rule="phrasing one")),
                         "successor")
        for phrasing in ("phrasing two", "phrasing three"):
            self.assertEqual(R.emit_candidate(sf, rec, self._cand(rule=phrasing)),
                             "present")
        self.assertEqual(len(sf.rows), 1)
        row = next(iter(sf.rows.values()))
        self.assertEqual(row["id"], f"{self.aid}+1")
        self.assertEqual(row["rule"], "phrasing three")     # refreshed, not stale
        self.assertEqual(row["predecessor_id"], "OBS-0001")

    def test_a_rejected_successor_claim_stays_suppressed(self):
        """The property `upsert_observed` gives a plain candidate, kept for a
        successor: the digest is stored as row DATA (never as the key), so a
        claim a human rejected does not come back on the next re-mine, while a
        genuinely different claim still opens a new successor."""
        sf = R.StateFile(self.state)
        rec = R.read_recorded_observations(self.obs)
        R.emit_candidate(sf, rec, self._cand(rule="changed"))
        sid = next(iter(sf.rows))
        sf.dispose(sid, "rejected")
        self.assertEqual(R.emit_candidate(sf, rec, self._cand(rule="changed")), "present")
        self.assertEqual(len(sf.rows), 1)
        self.assertEqual(R.emit_candidate(sf, rec, self._cand(rule="changed otherwise")),
                         "successor")
        self.assertEqual(sorted(sf.rows), [f"{self.aid}+1", f"{self.aid}+2"])

    def test_unrecorded_anchor_is_plain_observed(self):
        sf = R.StateFile(self.state)
        other = {"id": R.candidate_id("external-dependency", "httpx"),
                 "anchor_kind": "external-dependency", "canonical_anchor": "httpx",
                 "rule": "r", "evidence": ["b.py:1-1"]}
        self.assertEqual(R.emit_candidate(sf, R.read_recorded_observations(self.obs), other),
                         "observed")
        self.assertEqual(sf.rows[other["id"]]["anchor_id"], other["id"])

    def test_stale_anchor_records_signal_only(self):
        sf = R.StateFile(self.state)
        rec = R.read_recorded_observations(self.obs)
        stale = sf.signal_stale_anchors(rec, present_anchor_ids=set())
        self.assertEqual(set(stale), {self.aid})
        self.assertEqual(stale[self.aid]["obs_id"], "OBS-0001")
        self.assertEqual(sf.rows, {})                      # a signal is not a candidate
        sf.save()
        sf2 = R.StateFile(self.state)
        self.assertEqual(sf2.stale_anchors, stale)         # persisted
        # idempotent across re-mines: same input, same signal, no accumulation
        self.assertEqual(sf2.signal_stale_anchors(rec, present_anchor_ids=set()), stale)
        # the anchor came back: the signal clears (wholesale recompute)
        self.assertEqual(sf2.signal_stale_anchors(rec, present_anchor_ids={self.aid}), {})
        self.assertEqual(self._snap(), self.snapshot)      # no observation file touched

    def test_retired_record_never_signals_stale(self):
        # M-TEST-1: this test was VACUOUS. Its anchor was all digits, which
        # YAML resolves to the int 2, so `recorded` was keyed "2" and the
        # assertion checked a key that could never appear — it passed with the
        # guard deleted. A hex anchor and the reach-assertion below are what
        # make it fail under mutation.
        aid = "b7c1e2d3a4f50611"
        _write_obs(self.obs, 2, aid, "r", ["c.py:1-1"], status="retired")
        sf = R.StateFile(self.state)
        recorded = R.read_recorded_observations(self.obs)
        self.assertIn(aid, recorded, "fixture must reach the code under test")
        stale = sf.signal_stale_anchors(recorded, set())
        self.assertNotIn(aid, stale)

    def test_rejected_record_never_signals_stale(self):
        # M-TEST-3: `rejected` is the third excluded status and had no test at
        # all — `retired` was vacuous and `decided` was covered twice.
        aid = "f10e9d8c7b6a5041"
        _write_obs(self.obs, 4, aid, "r", ["e.py:1-1"], status="rejected")
        sf = R.StateFile(self.state)
        recorded = R.read_recorded_observations(self.obs)
        self.assertIn(aid, recorded, "fixture must reach the code under test")
        stale = sf.signal_stale_anchors(recorded, set())
        self.assertNotIn(aid, stale)
        self.assertIn(self.aid, stale, "the ratified record still signals")

    def test_observed_record_signals_stale_and_carries_its_status(self):
        # M-COR-2: an `observed` record DOES signal — §17.2 admits `reject`
        # from it — and the signal carries the status so the reader can name
        # the transition the gate accepts. `retire` is admitted only from
        # `ratified`, and emitting it here would strand the survey behind a
        # step no human can complete.
        aid = "a1b2c3d4e5f60718"
        _write_obs(self.obs, 5, aid, "r", ["f.py:1-1"], status="observed")
        sf = R.StateFile(self.state)
        recorded = R.read_recorded_observations(self.obs)
        self.assertIn(aid, recorded, "fixture must reach the code under test")
        stale = sf.signal_stale_anchors(recorded, set())
        self.assertEqual(stale[aid], {"obs_id": "OBS-0005", "status": "observed"})
        self.assertEqual(stale[self.aid]["status"], "ratified")

    def test_decided_record_never_signals_stale(self):
        # `decided` is terminal in the §17.2 lifecycle: the rule is now governed
        # by the ADR named in `decided_by`, so the anchor leaving the mined set
        # is expected, exactly as it is for `retired` and `rejected`. It is also
        # undischargeable — the survey emits `transition-observation retire` for
        # a stale signal, and that gate refuses a decided record — so a signal
        # here strands `--phase mine` at exit 1 forever.
        # The anchor carries hex letters deliberately. An all-digit anchor is
        # YAML-parsed as an integer, so `0000000000000003` reads back as the key
        # `"3"` and an assertion against the 16-char spelling passes vacuously
        # against unfixed code. See the deferral note on unquoted all-digit
        # anchor ids.
        aid = "d4f3a1b2c3e5f607"
        _write_obs(self.obs, 3, aid, "r", ["d.py:1-1"], status="decided")
        sf = R.StateFile(self.state)
        recorded = R.read_recorded_observations(self.obs)
        self.assertIn(aid, recorded, "fixture must reach the code under test")
        stale = sf.signal_stale_anchors(recorded, set())
        self.assertNotIn(aid, stale)

    def test_prune_keys_on_anchor_id_for_successor_rows(self):
        sf = R.StateFile(self.state)
        rec = R.read_recorded_observations(self.obs)
        R.emit_candidate(sf, rec, self._cand(rule="changed"))
        sid = next(iter(sf.rows))
        sf.dispose(sid, "rejected")
        sf.prune(present_anchor_ids={self.aid})            # anchor still present → kept
        self.assertIn(sid, sf.rows)
        sf.prune(present_anchor_ids=set())                 # anchor gone → dropped
        self.assertNotIn(sid, sf.rows)

    def test_no_path_writes_the_observations_dir(self):
        # Negative: the whole W4 code path runs with the observations dir read-only and
        # leaves it byte-identical. A write would raise (PermissionError) or change the snapshot.
        import os
        os.chmod(self.obs, 0o555)
        self.addCleanup(os.chmod, self.obs, 0o755)
        sf = R.StateFile(self.state)
        rec = R.read_recorded_observations(self.obs)
        R.emit_candidate(sf, rec, self._cand())
        R.emit_candidate(sf, rec, self._cand(rule="changed"))
        sf.signal_stale_anchors(rec, present_anchor_ids=set())
        sf.save()
        R.find_ratified_observation(self.obs, self.aid)
        self.assertEqual(self._snap(), self.snapshot)


class CuratedIndexTests(unittest.TestCase):
    # AC-6 — curated mode excludes not_load_bearing, keeps unclassifiable. Self-contained
    # fixture so it is deterministic regardless of the ambient tree (staged artifact carries
    # no bionic/adrs, so an ambient-tree assertion would false-fail there).
    def test_curated_narrower_than_complete(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        root = Path(d.name)
        adrs = root / "bionic" / "adrs"
        adrs.mkdir(parents=True)
        for num, tags in [(1, "[schema]"), (2, "[meta, docs]"), (3, "[nonsense]")]:
            (adrs / f"ADR-{num:04d}-x.md").write_text(
                f"---\nid: ADR-{num:04d}\ntitle: \"T{num}\"\nstatus: Accepted\n"
                f"date: 2026-01-01\ntags: {tags}\n---\n# body\n")
        complete, _ = D.extract_decision_index(root, "bionic", "complete")
        curated, _ = D.extract_decision_index(root, "bionic", "curated")
        # complete: all 3 Accepted; curated drops the meta/docs-only one (not_load_bearing),
        # keeps schema (load_bearing) + nonsense (unclassifiable, fail-open).
        self.assertEqual(complete.count("[^d"), 3 * 2)   # marker + def per decision
        self.assertEqual(curated.count("[^d"), 2 * 2)
        self.assertLess(curated.count("[^d"), complete.count("[^d"))
        self.assertIn("curated", curated)


class ObservationReaderContainmentTests(unittest.TestCase):
    """[SECURITY:S4/S5] The two observation readers here walk a directory whose
    contents they do not own, and both `read_text` every `OBS-*.md` in it.

    Two legs were missing. There was no CONTAINMENT leg, so a symlink planted
    in the concern directory was followed out of the tree — the same class
    `survey_sheet.record_paths` already closed. And the fail-closed raise
    embedded `str(exc)`, which for a PyYAML error carries `Mark.get_snippet()`
    — the offending SOURCE LINE, verbatim — so an ordinary private file was
    quoted onto stderr before any guard could refuse it.
    """

    #: An ordinary private note. Nothing here is attacker-authored: the file
    #: opens with a `---` block whose second value contains a colon, which is
    #: enough to make `yaml.safe_load` raise with the line in the message.
    PRIVATE = ("---\n"
               "title: Q3 board deck\n"
               "owner: cfo@example.com\n"
               "notes: Revenue: 41.2M, runway: 9 months, layoff plan: tier-2\n"
               "---\n\nBody text.\n")
    SECRETS = ("Revenue: 41.2M", "runway", "layoff", "cfo@example.com",
               "Q3 board deck")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.outside = self.home / "private-note.md"
        self.outside.write_text(self.PRIVATE, encoding="utf-8")
        self.root = self.home / "repo"
        self.tree = self.root / "bionic"
        self.obs = self.tree / "observations"
        self.obs.mkdir(parents=True)
        (self.root / ".bionic.yml").write_text(
            'config_version: "1"\ndocs_dir: bionic\n', encoding="utf-8")
        (self.tree / "manifest.yml").write_text(
            "schema_version: 5\nconcerns_enabled: [adrs, observations, arch]\n"
            "\nobservation:\n  next_number: 1\n  stale_days: 90\n",
            encoding="utf-8")
        (self.tree / "log.md").write_text("# Log\n", encoding="utf-8")
        (self.root / "src").mkdir()
        (self.root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
        state_path = self.tree / "arch" / "_recovered" / "state.yml"
        state = R.StateFile(state_path)
        self.anchor = R.candidate_id("external-dependency", "httpx")
        state.rows[self.anchor] = {
            "id": self.anchor, "anchor_kind": "external-dependency",
            "canonical_anchor": "httpx", "rule": "The gateway depends on httpx",
            "evidence": ["src/a.py:1-1"], "state": "observed",
            "domain": "runtime"}
        state.save()

    # ── the two plants ────────────────────────────────────────────────────

    def _plant_symlink(self):
        """The containment case: the concern names a file OUTSIDE the tree."""
        (self.obs / "OBS-9999-planted.md").symlink_to(self.outside)

    def _plant_real_file(self):
        """The positive control for every absence assertion below: the SAME
        bytes, as a real file inside the concern. Containment admits it, so the
        parser is genuinely reached and genuinely raises — which is what makes
        `no secret in the output` a claim about the message rather than about a
        reader that never ran."""
        (self.obs / "OBS-9999-planted.md").write_text(self.PRIVATE,
                                                      encoding="utf-8")

    def _assert_no_leak(self, *streams):
        blob = "".join(s or "" for s in streams)
        for secret in self.SECRETS:
            self.assertNotIn(secret, blob,
                             f"{secret!r} reached an output stream")
        self.assertNotIn(str(self.outside), blob,
                         "the symlink's target path reached an output stream")
        return blob

    # ── the three shipped commands that reach the reader ──────────────────

    def _cli(self, script, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPTS / script), "--repo-root",
             str(self.root), *args], capture_output=True, text=True)

    def _scaffold_and_sign(self):
        """Bring the tree to the point `signoff-survey.py` reads the concern:
        a scaffolded sheet with its human cells authored. Run BEFORE the plant,
        so the sign-off meets the plant on its own record walk rather than on
        the scaffold's."""
        proc = self._cli("scaffold-survey-sheet.py", "--date", "2026-08-30")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "survey_sheet", SCRIPTS / "survey_sheet.py")
        ss = importlib.util.module_from_spec(spec)
        sys.modules["survey_sheet"] = ss
        spec.loader.exec_module(ss)
        path = self.obs / "survey-SVY-0001.yml"
        sheet = ss.read_sheet(path)
        for row in sheet["rows"]:
            row["verdict"] = "ratify"
            row["domain"] = "runtime"
            row["rationale"] = "Reviewed against the cited lines."
        ss.write_sheet(path, sheet, contained_under=self.tree)

    #: (script, args, setup-before-the-plant). Each is a shipped command whose
    #: own run reaches `read_recorded_observations`.
    COMMANDS = (
        ("scaffold-survey-sheet.py", ("--date", "2026-08-30"), None),
        ("signoff-survey.py", ("--date", "2026-08-30", "--batch", "SVY-0001"),
         "_scaffold_and_sign"),
        ("survey.py", ("--phase", "mine"), None),
    )

    def test_no_command_reads_an_observation_through_a_link_out_of_the_tree(self):
        for script, args, setup in self.COMMANDS:
            with self.subTest(script=script):
                self.setUp()
                if setup:
                    getattr(self, setup)()
                self._plant_symlink()
                proc = self._cli(script, *args)
                self.assertNotEqual(proc.returncode, 0, proc.stdout)
                blob = self._assert_no_leak(proc.stdout, proc.stderr)
                self.assertIn("OBS-9999-planted.md", blob)
                self.assertIn("does not resolve to a file inside", blob)

    def test_no_command_quotes_the_frontmatter_it_cannot_parse(self):
        """The positive control: the same bytes as a REAL file in the concern.
        Containment admits it, the parser raises, and the message still carries
        no line of the file."""
        for script, args, setup in self.COMMANDS:
            with self.subTest(script=script):
                self.setUp()
                if setup:
                    getattr(self, setup)()
                self._plant_real_file()
                proc = self._cli(script, *args)
                self.assertNotEqual(proc.returncode, 0, proc.stdout)
                blob = self._assert_no_leak(proc.stdout, proc.stderr)
                self.assertIn("OBS-9999-planted.md", blob)
                self.assertIn("frontmatter", blob)

    # ── the library entry point `transition-decision` calls directly ──────

    def test_find_ratified_observation_refuses_a_link_out_of_the_tree(self):
        self._plant_symlink()
        with self.assertRaises(ValueError) as caught:
            R.find_ratified_observation(self.obs, self.anchor)
        self._assert_no_leak(str(caught.exception))
        self.assertIn("does not resolve to a file inside", str(caught.exception))

    def test_find_ratified_observation_never_quotes_the_frontmatter(self):
        self._plant_real_file()
        with self.assertRaises(ValueError) as caught:
            R.find_ratified_observation(self.obs, self.anchor)
        self._assert_no_leak(str(caught.exception))
        self.assertIn("frontmatter", str(caught.exception))

    def test_read_recorded_observations_refuses_a_link_out_of_the_tree(self):
        self._plant_symlink()
        with self.assertRaises(ValueError) as caught:
            R.read_recorded_observations(self.obs)
        self._assert_no_leak(str(caught.exception))

    def test_read_recorded_observations_never_quotes_the_frontmatter(self):
        self._plant_real_file()
        with self.assertRaises(ValueError) as caught:
            R.read_recorded_observations(self.obs)
        self._assert_no_leak(str(caught.exception))

    def test_the_summaries_projection_never_quotes_the_frontmatter_either(self):
        """The sibling reader found by the fail-closed sibling sweep.

        `summaries_projection.observations_source_problem` walks the same
        concern directory and RETURNS its message, which the three regenerators
        print. It embedded the same `str(exc)`, so the same private line landed
        on stdout instead of stderr. The sentence is now the shared one.

        KNOWN GAP, reported rather than closed here: that walk still has no
        resolved-containment leg, so a symlink planted in the concern is READ
        (and folded into `observations_sha256`) even though nothing of it is
        printed any more."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "summaries_projection", SCRIPTS / "summaries_projection.py")
        sp = importlib.util.module_from_spec(spec)
        sys.modules["summaries_projection"] = sp
        spec.loader.exec_module(sp)
        self._plant_real_file()
        manifest = {"concerns_enabled": ["adrs", "observations", "arch"]}
        problem = sp.observations_source_problem(self.root, manifest)
        self.assertIsNotNone(problem, "the fixture never reached the parser")
        self._assert_no_leak(problem)
        self.assertIn("OBS-9999-planted.md", problem)

    def test_the_readers_still_read_an_ordinary_record(self):
        """The control for all six refusals: an ordinary record in the same
        concern directory is read, so the refusals above are about the plant
        and not about a reader that refuses everything."""
        (self.obs / "OBS-0001-httpx.md").write_text(
            "---\nid: OBS-0001\nstatus: ratified\n"
            f'anchor_id: "{self.anchor}"\nevidence: ["src/a.py:1-1"]\n'
            "governs:\n  - rule: The gateway depends on httpx\n---\n\nbody\n",
            encoding="utf-8")
        recorded = R.read_recorded_observations(self.obs)
        self.assertEqual(recorded[self.anchor]["id"], "OBS-0001")
        self.assertEqual(R.find_ratified_observation(self.obs, self.anchor),
                         "OBS-0001")

if __name__ == "__main__":
    unittest.main(verbosity=2)
