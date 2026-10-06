"""Tests for the observations concern (docs/AGENTS.md §17).

Covers the four CHK-OBS audit rules via the reference checker
`check_observations.py` (§17.3), the concern-disabled / missing-directory
silent paths, the evidence-path escape rules, the stale_days threshold +
fallback, and the no-coverage-percentage property.

Also covers the recover-decisions skill-prose conformance for the W4 read-only
observation steps (§17.2).

Runs under the uv lane (PyYAML). Uses tempdirs for the checker fixtures; never
mutates the real tree.
"""
from __future__ import annotations

import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPTS_DIR = REPO_ROOT / "crux" / "scripts"

try:
    import yaml  # noqa: F401
    HAVE_YAML = True
except ImportError:
    HAVE_YAML = False

_spec = importlib.util.spec_from_file_location("check_observations", SCRIPTS_DIR / "check_observations.py")
_co = importlib.util.module_from_spec(_spec)
if HAVE_YAML:
    _spec.loader.exec_module(_co)


def _dump_all(obj) -> str:
    import yaml as y
    return y.dump(obj, sort_keys=False)


@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under uv)")
class CheckObservationsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "bionic" / "observations").mkdir(parents=True)
        (self.root / "bionic" / "adrs" / "archive").mkdir(parents=True)
        self._manifest(stale_days=None, enabled=True)

    # ---- fixture helpers -------------------------------------------------

    def _manifest(self, *, stale_days=None, enabled=True):
        concerns = ["observations"] if enabled else []
        m = {
            "schema_version": "5",
            "concerns_enabled": concerns,
        }
        if enabled:
            obs = {"next_number": 1}
            if stale_days is not None:
                obs["stale_days"] = stale_days
            m["observation"] = obs
        (self.root / "bionic" / "manifest.yml").write_text(_dump_all(m), encoding="utf-8")

    def _obs(self, obs_id, *, filename=None, status="observed",
              observed_date="2026-08-01", decided_by=None,
              evidence=None, anchor_id="a" * 16, extra_fm=None):
        if evidence is None:
            evidence = ["crux/scripts/check_observations.py:1-10"]
        fm = {
            "id": obs_id,
            "title": "The code does X.",
            "status": status,
            "date": observed_date,
            "observed_date": observed_date,
            "ratified_date": None,
            "rejected_date": None,
            "retired_date": None,
            "decided_date": None,
            "provenance": "recovered",
            "decided_by": decided_by,
            "evidence": evidence,
            "anchor_id": anchor_id,
            "related_invariants": [],
            "tags": [],
            "governs": [],
        }
        if extra_fm:
            fm.update(extra_fm)
        body = "---\n" + _dump_all(fm) + "---\n\n# " + obs_id + "\n"
        fname = filename or f"{obs_id}-slug.md"
        (self.root / "bionic" / "observations" / fname).write_text(body, encoding="utf-8")

    def _index(self, text):
        (self.root / "bionic" / "observations" / "index.md").write_text(text, encoding="utf-8")

    def _adr(self, adr_id, *, archived=False):
        d = self.root / "bionic" / "adrs" / ("archive" if archived else ".")
        d.mkdir(parents=True, exist_ok=True)
        fm = f"---\nid: {adr_id}\nstatus: Accepted\n---\n\n# {adr_id}\n"
        (d / f"{adr_id}-slug.md").write_text(fm, encoding="utf-8")

    def _run(self):
        return _co.check(self.root)

    # ---- concern-disabled / missing-directory silent paths ---------------

    def test_concern_disabled_is_silent(self):
        self._manifest(enabled=False)
        r = self._run()
        self.assertEqual(r["broken"], [])
        self.assertEqual(r["warning"], [])
        self.assertFalse(r.get("concern_enabled", True))

    def test_missing_directory_is_silent(self):
        import shutil
        shutil.rmtree(self.root / "bionic" / "observations")
        r = self._run()
        self.assertEqual(r["broken"], [])
        self.assertEqual(r["warning"], [])

    # ---- CHK-OBS-BIJECTION -------------------------------------------------

    def test_bijection_clean(self):
        self._obs("OBS-0001")
        r = self._run()
        self.assertFalse(any("BIJECTION" in b for b in r["broken"]))

    def test_bijection_no_parseable_id(self):
        (self.root / "bionic" / "observations" / "OBS-0002-broken.md").write_text(
            "no frontmatter here\n", encoding="utf-8"
        )
        r = self._run()
        self.assertTrue(any("BIJECTION" in b for b in r["broken"]))

    def test_bijection_duplicate_id(self):
        self._obs("OBS-0001", filename="OBS-0001-a.md")
        self._obs("OBS-0001", filename="OBS-0001-b.md", anchor_id="b" * 16)
        r = self._run()
        self.assertTrue(any("BIJECTION" in b and "duplicate" in b for b in r["broken"]))

    def test_bijection_id_disagrees_with_filename(self):
        self._obs("OBS-0001", filename="OBS-0002-mismatch.md")
        r = self._run()
        self.assertTrue(any("BIJECTION" in b for b in r["broken"]))

    def test_bijection_index_row_names_missing_record(self):
        self._obs("OBS-0001")
        self._index("| OBS-0001 | ok |\n| OBS-0099 | ghost |\n")
        r = self._run()
        self.assertTrue(any("BIJECTION" in b and "OBS-0099" in b for b in r["broken"]))

    def test_bijection_forged_ref_in_a_later_cell_is_not_a_reference(self):
        """A record's identity is its id column, not any cell that follows.
        A mined `domain` value carrying the SHAPE of an id must not forge a
        reference: read by scanning the whole page, `OBS-9999` in a domain
        cell produced a false 'names record with no file' BROKEN; read from
        the id column, it is content, not a claim."""
        self._obs("OBS-0001")
        self._index(
            "| OBS-0001 | ratified | recovered | see OBS-9999 | ev | a | — |\n")
        r = self._run()
        self.assertFalse(any("OBS-9999" in b for b in r["broken"]), r["broken"])
        # Control: the same id in the ID COLUMN is a genuine dangling row and
        # stays BROKEN — the fix narrows where ids are read, it does not stop
        # reading them.
        self._index("| OBS-0001 | ok |\n| OBS-9999 | ghost |\n")
        r = self._run()
        self.assertTrue(
            any("BIJECTION" in b and "OBS-9999" in b for b in r["broken"]))

    # ---- Finding 4: prefix-aware index bijection scan ---------------------

    def test_bijection_prefixed_index_row_matches_prefixed_record(self):
        """A tree configured with an artifact prefix (§14.3) writes both the
        record id and the index row in the prefixed dual form, e.g.
        `CRX-OBS-0001`. The index scan must match on the full prefixed id,
        not silently truncate to the bare `OBS-0001` form and report a false
        BROKEN against a record that in fact exists."""
        self._obs("CRX-OBS-0001", filename="CRX-OBS-0001-slug.md")
        self._index("| CRX-OBS-0001 | ok |\n")
        r = self._run()
        self.assertFalse(any("BIJECTION" in b for b in r["broken"]), r["broken"])

    def test_bijection_prefixed_index_row_ghost_still_broken(self):
        """A genuinely absent prefixed record must still report BROKEN."""
        self._obs("CRX-OBS-0001", filename="CRX-OBS-0001-slug.md")
        self._index("| CRX-OBS-0001 | ok |\n| CRX-OBS-0099 | ghost |\n")
        r = self._run()
        self.assertTrue(any("BIJECTION" in b and "CRX-OBS-0099" in b for b in r["broken"]))

    def test_bijection_bare_index_row_still_matches_unprefixed_tree(self):
        self._obs("OBS-0001")
        self._index("| OBS-0001 | ok |\n")
        r = self._run()
        self.assertFalse(any("BIJECTION" in b for b in r["broken"]), r["broken"])

    # ---- Finding 8: reverse-direction bijection (file with no index row) --

    def test_bijection_record_with_no_index_row_is_broken(self):
        self._obs("OBS-0001")
        self._index("")  # index exists but names nothing
        r = self._run()
        self.assertTrue(any("BIJECTION" in b and "OBS-0001" in b for b in r["broken"]))

    def test_bijection_record_with_no_index_row_message_distinguishable(self):
        """The reverse-direction finding must read differently from the
        forward (index-names-missing-file) finding, so a reader can tell
        which direction broke without cross-referencing code."""
        self._obs("OBS-0001")
        self._index("| OBS-0099 | ghost |\n")
        r = self._run()
        forward = [b for b in r["broken"] if "BIJECTION" in b and "OBS-0099" in b]
        reverse = [b for b in r["broken"] if "BIJECTION" in b and "OBS-0001" in b]
        self.assertEqual(len(forward), 1)
        self.assertEqual(len(reverse), 1)
        self.assertNotEqual(forward[0], reverse[0])

    def test_bijection_record_referenced_in_index_is_clean(self):
        self._obs("OBS-0001")
        self._index("| OBS-0001 | ok |\n")
        r = self._run()
        self.assertFalse(any("BIJECTION" in b for b in r["broken"]))

    def test_bijection_missing_index_file_does_not_spam_reverse_findings(self):
        """No index.md at all: keep the existing silent forward behaviour and
        do not emit one reverse finding per record file."""
        self._obs("OBS-0001")
        self._obs("OBS-0002", filename="OBS-0002-slug.md")
        r = self._run()
        self.assertFalse(any("BIJECTION" in b for b in r["broken"]))

    def test_bijection_unparseable_frontmatter_is_a_finding_not_a_traceback(self):
        """A record whose frontmatter is not parseable YAML is the same family
        of record-shape defect as one with no id: CHK-OBS-BIJECTION reports it.
        Letting the YAML error escape breaks the module's exit contract — the
        checker would exit non-zero with EMPTY stdout, which the crux
        convention reserves for a crash, so `audit-docs` and CHK-DRIFT-1 get no
        verdict at all for what is an authored typo."""
        (self.root / "bionic" / "observations" / "OBS-0002-broken.md").write_text(
            '---\nid: OBS-0002\nevidence: [foo.py:1-2\n---\n\nBody.\n',
            encoding="utf-8")
        r = self._run()
        self.assertTrue(any("BIJECTION" in b and "OBS-0002-broken.md" in b
                            for b in r["broken"]), r["broken"])

    def test_unparseable_frontmatter_keeps_the_json_on_stdout_lane(self):
        """The end-to-end contract, through `main()`: exit 1 with a parseable
        JSON payload on stdout, never a traceback."""
        import subprocess
        import sys
        (self.root / ".bionic.yml").write_text(
            'config_version: "1"\ndocs_dir: bionic\n', encoding="utf-8")
        (self.root / "bionic" / "observations" / "OBS-0002-broken.md").write_text(
            '---\nid: OBS-0002\nevidence: [foo.py:1-2\n---\n\nBody.\n',
            encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "check_observations.py"),
             "--root", str(self.root)],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertTrue(any("BIJECTION" in b for b in payload["broken"]), payload)

    # ---- CHK-OBS-EVIDENCE ---------------------------------------------------

    def test_evidence_clean(self):
        self._obs("OBS-0001", evidence=["crux/scripts/check_observations.py:1-10"])
        r = self._run()
        self.assertFalse(any("EVIDENCE" in b for b in r["broken"]))

    def test_evidence_empty_list_is_broken(self):
        self._obs("OBS-0001", evidence=[])
        r = self._run()
        self.assertTrue(any("EVIDENCE" in b for b in r["broken"]))

    def test_evidence_absolute_path_is_broken(self):
        self._obs("OBS-0001", evidence=["/etc/passwd:1-2"])
        r = self._run()
        self.assertTrue(any("EVIDENCE" in b for b in r["broken"]))

    def test_evidence_dotdot_traversal_is_broken(self):
        self._obs("OBS-0001", evidence=["../../etc/passwd:1-2"])
        r = self._run()
        self.assertTrue(any("EVIDENCE" in b for b in r["broken"]))

    def test_evidence_tilde_path_is_broken(self):
        self._obs("OBS-0001", evidence=["~/secrets.txt:1-2"])
        r = self._run()
        self.assertTrue(any("EVIDENCE" in b for b in r["broken"]))

    def test_evidence_unparseable_entry_is_broken(self):
        self._obs("OBS-0001", evidence=["not-a-grammar-match"])
        r = self._run()
        self.assertTrue(any("EVIDENCE" in b for b in r["broken"]))

    def test_evidence_ratified_unresolvable_path_is_broken(self):
        self._obs("OBS-0001", status="ratified",
                   evidence=["crux/scripts/does_not_exist_at_all.py:1-2"])
        r = self._run()
        self.assertTrue(any("EVIDENCE" in b for b in r["broken"]))

    def test_evidence_ratified_resolvable_path_is_clean(self):
        target = self.root / "src" / "widget.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# widget\n" * 20, encoding="utf-8")
        self._obs("OBS-0001", status="ratified",
                   evidence=["src/widget.py:1-10"])
        r = self._run()
        self.assertFalse(any("EVIDENCE" in b for b in r["broken"]))

    # ---- Finding 11: scalar `evidence` is a schema violation, not N grammar
    #      violations ---------------------------------------------------------

    def test_evidence_scalar_string_is_a_single_finding(self):
        """A hand-written scalar `evidence: "src/a.py:1-2"` must not iterate
        as characters (one grammar-violation finding per character); it is a
        single clear schema-shape finding."""
        self._obs("OBS-0001", evidence="src/a.py:1-2")
        r = self._run()
        evidence_findings = [b for b in r["broken"] if "EVIDENCE" in b and "OBS-0001" in b]
        self.assertEqual(len(evidence_findings), 1, evidence_findings)

    # ---- CHK-OBS-DECIDED -----------------------------------------------------

    def test_decided_resolves_in_adrs(self):
        self._adr("ADR-0001")
        self._obs("OBS-0001", status="decided", decided_by="ADR-0001")
        r = self._run()
        self.assertFalse(any("DECIDED" in b for b in r["broken"]))

    def test_decided_resolves_in_archive(self):
        self._adr("ADR-0002", archived=True)
        self._obs("OBS-0001", status="decided", decided_by="ADR-0002")
        r = self._run()
        self.assertFalse(any("DECIDED" in b for b in r["broken"]))

    def test_decided_with_missing_adr_is_broken(self):
        self._obs("OBS-0001", status="decided", decided_by="ADR-0099")
        r = self._run()
        self.assertTrue(any("DECIDED" in b for b in r["broken"]))

    def test_non_decided_with_decided_by_is_broken(self):
        self._obs("OBS-0001", status="observed", decided_by="ADR-0001")
        self._adr("ADR-0001")
        r = self._run()
        self.assertTrue(any("DECIDED" in b for b in r["broken"]))

    def test_decided_with_null_decided_by_is_broken(self):
        self._obs("OBS-0001", status="decided", decided_by=None)
        r = self._run()
        self.assertTrue(any("DECIDED" in b for b in r["broken"]))

    # ---- CHK-OBS-STALE ---------------------------------------------------

    def test_stale_default_90_days(self):
        self._obs("OBS-0001", status="observed", observed_date="2020-01-01")
        r = self._run()
        self.assertTrue(any("STALE" in w for w in r["warning"]))
        self.assertEqual(r["survey_debt"], 1)

    def test_stale_not_triggered_when_recent(self):
        from datetime import date
        recent = date.today().isoformat()
        self._obs("OBS-0001", status="observed", observed_date=recent)
        r = self._run()
        self.assertFalse(any("STALE" in w for w in r["warning"]))

    def test_stale_threshold_read_from_manifest(self):
        from datetime import date, timedelta
        self._manifest(stale_days=5)
        old_enough = (date.today() - timedelta(days=10)).isoformat()
        self._obs("OBS-0001", status="observed", observed_date=old_enough)
        r = self._run()
        self.assertTrue(any("STALE" in w for w in r["warning"]))

    def test_stale_only_counts_observed_status(self):
        self._obs("OBS-0001", status="ratified", observed_date="2020-01-01",
                   evidence=["crux/scripts/check_observations.py:1-10"])
        r = self._run()
        self.assertFalse(any("STALE" in w for w in r["warning"]))
        self.assertEqual(r["survey_debt"], 0)

    # ---- Finding 5: STATUSES must actually be validated --------------------

    def test_invalid_status_is_broken(self):
        """A typo'd/miscased status (`ratifed`, `Ratified`) must not silently
        skip evidence resolution, decided-checking, and staleness — it must
        raise a finding of its own."""
        self._obs("OBS-0001", status="ratifed",
                   evidence=["crux/scripts/check_observations.py:1-10"])
        r = self._run()
        self.assertTrue(any("OBS-0001" in b for b in r["broken"]), r["broken"])

    def test_invalid_status_not_counted_in_survey_debt(self):
        self._obs("OBS-0001", status="Observed",
                   evidence=["crux/scripts/check_observations.py:1-10"])
        r = self._run()
        self.assertEqual(r["survey_debt"], 0)

    # ---- no-coverage-percentage property -----------------------------------

    def test_no_percent_anywhere_in_output(self):
        self._obs("OBS-0001", status="observed", observed_date="2020-01-01")
        self._obs("OBS-0002", status="decided", decided_by=None)
        r = self._run()
        payload = json.dumps(r)
        self.assertNotIn("%", payload)


class RecoverDecisionsSkillProseTests(unittest.TestCase):
    """W4: the recover-decisions skill's read-only observation steps (§17.2)."""

    def setUp(self):
        self.text = (REPO_ROOT / "crux" / "skills" / "recover-decisions" / "SKILL.md").read_text(
            encoding="utf-8"
        )

    def test_mentions_read_recorded_observations(self):
        self.assertIn("read_recorded_observations", self.text)

    def test_mentions_signal_stale_anchors(self):
        self.assertIn("signal_stale_anchors", self.text)

    def test_mentions_successor_id(self):
        self.assertIn("successor_id", self.text)

    def test_mentions_writer_boundary_phrase(self):
        self.assertIn("writes no observation file in any state", self.text)

    def test_no_literal_adr_number(self):
        self.assertIsNone(re.search(r"ADR-\d{4}", self.text))

    def test_no_wiki_link_to_adrs(self):
        self.assertNotIn("[[adrs/", self.text)


@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under uv)")
class EvidenceContainmentTests(CheckObservationsTests):
    """M-SEC-3. The module docstring claims an evidence path "must never be
    able to escape the repo root". It did the textual refusal and then called
    `is_file()`, which follows symlinks — so a ratified record citing
    `link/secret.txt:1-1` through a committed `link -> outside` audited clean.
    """

    def setUp(self):
        super().setUp()
        self._outside_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._outside_tmp.cleanup)
        self.outside = Path(self._outside_tmp.name)
        (self.outside / "secret.txt").write_text("token\n", encoding="utf-8")

    def test_ratified_evidence_escaping_through_a_symlink_is_broken(self):
        (self.root / "link").symlink_to(self.outside, target_is_directory=True)
        cited = "link/secret.txt"
        # Fixture reach-assertion: the link MUST be followable, or the test
        # proves nothing about the defect it stands for.
        self.assertTrue((self.root / cited).is_file())
        self._obs("OBS-0001", status="ratified", evidence=[f"{cited}:1-1"])
        self._index("| id |\n|---|\n| OBS-0001 |\n")
        broken = self._run()["broken"]
        self.assertTrue(
            any("CHK-OBS-EVIDENCE" in b and "OBS-0001" in b for b in broken),
            f"evidence reaching outside the repo root audited clean: {broken}")

    def test_ratified_evidence_inside_the_root_still_resolves(self):
        (self.root / "real.py").write_text("x = 1\n", encoding="utf-8")
        self._obs("OBS-0001", status="ratified", evidence=["real.py:1-1"])
        self._index("| id |\n|---|\n| OBS-0001 |\n")
        self.assertEqual(
            [b for b in self._run()["broken"] if "CHK-OBS-EVIDENCE" in b], [])


@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under uv)")
class CheckObsAnchorTests(CheckObservationsTests):
    """M-DOC-8 / CHK-OBS-ANCHOR. `anchor_id` is required by §17.1 and
    load-bearing for dedup, and nothing validated it: a record with the line
    deleted returned `broken: [] exit 0`, and `read_recorded_observations` then
    skipped it silently, so the re-mine proposed a duplicate."""

    def setUp(self):
        super().setUp()
        (self.root / "real.py").write_text("x = 1\n", encoding="utf-8")

    def _one(self, oid, **kw):
        kw.setdefault("evidence", ["real.py:1-1"])
        self._obs(oid, **kw)

    def test_a_missing_anchor_id_is_broken(self):
        self._one("OBS-0001", extra_fm={"anchor_id": None})
        self._index("| id |\n|---|\n| OBS-0001 |\n")
        broken = self._run()["broken"]
        self.assertTrue(any("CHK-OBS-ANCHOR" in b and "OBS-0001" in b for b in broken),
                        f"a record with no anchor_id audited clean: {broken}")

    def test_a_malformed_anchor_id_is_broken(self):
        for bad in ("A" * 16, "abc", "a" * 17, "g" * 16, 2):
            with self.subTest(anchor=bad):
                self.setUp()
                self._one("OBS-0001", anchor_id=bad)
                self._index("| id |\n|---|\n| OBS-0001 |\n")
                broken = self._run()["broken"]
                self.assertTrue(
                    any("CHK-OBS-ANCHOR" in b for b in broken),
                    f"anchor_id {bad!r} audited clean: {broken}")

    def test_two_live_records_sharing_an_anchor_are_broken(self):
        for a, b in (("ratified", "ratified"), ("observed", "ratified"),
                     ("observed", "observed")):
            with self.subTest(statuses=(a, b)):
                self.setUp()
                self._one("OBS-0001", status=a, anchor_id="b" * 16)
                self._one("OBS-0002", status=b, anchor_id="b" * 16)
                self._index("| id |\n|---|\n| OBS-0001 |\n| OBS-0002 |\n")
                broken = self._run()["broken"]
                self.assertTrue(
                    any("CHK-OBS-ANCHOR" in x for x in broken),
                    f"two live records on one anchor audited clean: {broken}")

    def test_a_terminal_record_may_share_an_anchor_with_a_live_one(self):
        """`retired`, `rejected` and `decided` are terminal: the successor
        legitimately carries its predecessor's anchor once the predecessor is
        retired. Flagging that would refuse the lifecycle §17.2 prescribes."""
        for terminal in ("retired", "rejected", "decided"):
            with self.subTest(terminal=terminal):
                self.setUp()
                self._one("OBS-0001", status=terminal, anchor_id="c" * 16,
                          decided_by="ADR-0001" if terminal == "decided" else None)
                if terminal == "decided":
                    self._adr("ADR-0001")
                self._one("OBS-0002", status="ratified", anchor_id="c" * 16)
                self._index("| id |\n|---|\n| OBS-0001 |\n| OBS-0002 |\n")
                broken = self._run()["broken"]
                self.assertEqual(
                    [x for x in broken if "CHK-OBS-ANCHOR" in x], [],
                    f"a terminal predecessor was flagged as a live collision: {broken}")

    def test_a_well_formed_record_set_is_anchor_clean(self):
        self._one("OBS-0001", anchor_id="0123456789abcdef")
        self._one("OBS-0002", anchor_id="fedcba9876543210")
        self._index("| id |\n|---|\n| OBS-0001 |\n| OBS-0002 |\n")
        self.assertEqual(
            [x for x in self._run()["broken"] if "CHK-OBS-ANCHOR" in x], [])


if __name__ == "__main__":
    unittest.main()


# ── CHK-OBS-SURVEY-* : the four batch rules (§17.3, protocol in §17.5) ───────

def _survey_sheet_module():
    """`survey_sheet`, imported the way the checker imports it.

    Not a by-path load: `survey_sheet` imports `summaries_projection` and
    `crux.arch.recover` BY NAME, so `crux/scripts/` has to be importable, and
    sharing the module name is what makes the test drive the same
    `batch_state` the checker calls rather than a second copy of it.
    """
    import sys as _sys
    p = str(SCRIPTS_DIR)
    if p not in _sys.path:
        _sys.path.insert(0, p)
    return importlib.import_module("survey_sheet")


@unittest.skipUnless(HAVE_YAML, "PyYAML required (run under uv)")
class CheckObsSurveyRuleTests(CheckObservationsTests):
    """The four batch rules over `<docs_dir>/observations/_surveys/`.

    Every fixture is written through `survey_sheet`'s own writers, so a sheet
    or receipt the real sign-off would refuse cannot silently become a test
    fixture that passes. Every rule gets three cases: one that FIRES it, one
    NEAR-MISS that must not, and — inside the near-miss test — a one-cell
    mutation that makes the same fixture fire, which is what proves the
    near-miss is a real negative rather than a fixture that never triggers.
    """

    A1 = "b" * 16
    A2 = "c" * 16

    def setUp(self):
        super().setUp()
        self.ss = _survey_sheet_module()
        self.tree = self.root / "bionic"
        self.obs = self.tree / "observations"

    # ---- fixture builders ------------------------------------------------

    def _sheet_doc(self, bid, rows):
        return {
            "config_version": "1",
            "batch_id": bid,
            "scaffold_provenance": {
                "tool": "scaffold-survey-sheet.py",
                "scaffolded": "2026-08-30",
                "state_file": "bionic/arch/_recovered/state.yml",
                "candidates": str(len(rows)),
            },
            "rows": [
                {
                    "anchor_id": r["anchor_id"],
                    "proposed_domain": r.get("proposed_domain", "observations"),
                    "verdict": r.get("verdict", "ratify"),
                    "domain": r.get("domain", ""),
                    "rationale": r.get("rationale", "The code holds it."),
                }
                for r in rows
            ],
        }

    def _receipt_doc(self, bid, rows, *, digest, signed, completed, records):
        return {
            "config_version": "1",
            "batch_id": bid,
            "sheet_ref": f"_surveys/{bid}/sheet.yml",
            "digest": digest,
            "scaffold_provenance": {
                "tool": "scaffold-survey-sheet.py",
                "scaffolded": "2026-08-30",
                "state_file": "bionic/arch/_recovered/state.yml",
                "candidates": str(len(rows)),
            },
            "signed": signed,
            "completed": completed,
            "records": list(records),
            "rows": [
                {
                    "anchor_id": r["anchor_id"],
                    "candidate_id": r.get("candidate_id", r["anchor_id"]),
                    "verdict": r.get("verdict", "ratify"),
                    "domain": r.get("domain") or r.get("proposed_domain", "observations"),
                    "domain_source": r.get("domain_source", "proposed"),
                    "record_id": r.get("record_id", ""),
                    "record_path": r.get("record_path", ""),
                    "retires": r.get("retires", ""),
                }
                for r in sorted(rows, key=lambda r: r["anchor_id"])
            ],
        }

    def _batch(self, bid="SVY-0001", *, rows=None, signed="2026-08-30",
               completed=None, records=(), archived_rows=None,
               write_sheet=True):
        """Write one batch's `sheet.yml` + `receipt.yml`.

        The receipt's digest is always computed over `rows`. `archived_rows`
        overrides what is WRITTEN to `sheet.yml` — that split is the only way
        to build a receipt whose sheet no longer matches it, and it exists for
        CHK-OBS-SURVEY-DIGEST alone.
        """
        rows = rows if rows is not None else [{"anchor_id": self.A1}]
        paths = self.ss.receipt_paths(self.obs, bid)
        paths["dir"].mkdir(parents=True, exist_ok=True)
        bound = self._sheet_doc(bid, rows)
        digest = self.ss.sheet_digest(bound)
        if write_sheet:
            written = (self._sheet_doc(bid, archived_rows)
                       if archived_rows is not None else bound)
            self.ss.write_sheet(paths["sheet"], written, contained_under=self.tree)
        self.ss.write_receipt(
            paths["receipt"],
            self._receipt_doc(bid, rows, digest=digest, signed=signed,
                              completed=completed, records=records),
            contained_under=self.tree)
        return paths

    def _days_ago(self, n):
        from datetime import date as _d, timedelta
        return (_d.today() - timedelta(days=n)).isoformat()

    def _findings(self, rule):
        r = self._run()
        return ([b for b in r["broken"] if b.startswith(rule + ":")],
                [w for w in r["warning"] if w.startswith(rule + ":")])

    # ---- CHK-OBS-SURVEY-STUB --------------------------------------------

    def test_stub_past_the_window_is_broken(self):
        """The trigger. `survey_stub_days` defaults to 1, so a receipt signed
        two days ago and still recording no completion is past the window."""
        self._batch(signed=self._days_ago(2))
        broken, warning = self._findings("CHK-OBS-SURVEY-STUB")
        self.assertEqual(len(broken), 1, broken)
        self.assertIn("SVY-0001", broken[0])
        self.assertEqual(warning, [])

    def test_stub_inside_the_window_is_a_warning_and_not_broken(self):
        """The near-miss: one day inside `survey_stub_days`, which is exactly
        the state a sign-off in progress leaves behind.

        The second half is the positive control. Without it this test would
        pass just as well against a rule that never fires at all — re-dating
        the same fixture one day further back must flip WARNING to BROKEN.
        """
        self._batch(signed=self._days_ago(1))
        broken, warning = self._findings("CHK-OBS-SURVEY-STUB")
        self.assertEqual(broken, [], "a batch inside the window reported BROKEN")
        self.assertEqual(len(warning), 1, warning)

        # positive control: the same fixture, one day older.
        self._batch(signed=self._days_ago(2))
        broken, warning = self._findings("CHK-OBS-SURVEY-STUB")
        self.assertEqual(len(broken), 1,
                         "the near-miss fixture cannot fire the rule at all")
        self.assertEqual(warning, [])

    def test_stub_honours_a_widened_survey_stub_days(self):
        """The window is read from `observation.survey_stub_days`, not pinned
        at the fallback: at 5 days a 4-day-old stub is a WARNING, and the
        same fixture is BROKEN at the default."""
        self._manifest_with_stub_days(5)
        self._batch(signed=self._days_ago(4))
        broken, warning = self._findings("CHK-OBS-SURVEY-STUB")
        self.assertEqual(broken, [])
        self.assertEqual(len(warning), 1, warning)

        # positive control: the identical fixture under the fallback window.
        self._manifest(stale_days=None, enabled=True)
        broken, _ = self._findings("CHK-OBS-SURVEY-STUB")
        self.assertEqual(len(broken), 1,
                         "the 4-day-old stub cannot fire the rule at all")

    def test_stub_silent_on_a_batch_that_records_completion(self):
        """A completed receipt is past the commit point, so the rule is silent
        however old it is. Control: clearing `completed` on the same bytes
        fires it."""
        self._batch(signed=self._days_ago(400), completed=self._days_ago(399),
                    records=["OBS-0001"])
        broken, warning = self._findings("CHK-OBS-SURVEY-STUB")
        self.assertEqual((broken, warning), ([], []))

        self._batch(signed=self._days_ago(400), completed=None)
        broken, _ = self._findings("CHK-OBS-SURVEY-STUB")
        self.assertEqual(len(broken), 1,
                         "the completed-batch fixture cannot fire the rule")

    def _manifest_with_stub_days(self, days):
        m = {
            "schema_version": "5",
            "concerns_enabled": ["observations"],
            "observation": {"next_number": 1, "survey_stub_days": days},
        }
        (self.tree / "manifest.yml").write_text(_dump_all(m), encoding="utf-8")

    # ---- CHK-OBS-SURVEY-VISIBLE -----------------------------------------

    def _visible_rows(self):
        return [{"anchor_id": self.A1, "record_id": "OBS-0001",
                 "record_path": "observations/OBS-0001-slug.md"}]

    def test_visible_record_under_an_incomplete_batch_is_broken(self):
        """The trigger, at an age the stub rule would only WARN at — the rule
        is BROKEN at any age, because §17.5 lands completion before
        visibility and no healthy in-flight state produces one."""
        self._obs("OBS-0001", status="observed")
        self._index("| id |\n|---|\n| OBS-0001 |\n")
        self._batch(rows=self._visible_rows(), signed=self._days_ago(0))
        broken, _ = self._findings("CHK-OBS-SURVEY-VISIBLE")
        self.assertEqual(len(broken), 1, broken)
        self.assertIn("OBS-0001-slug.md", broken[0])
        # The discriminator against CHK-OBS-SURVEY-STUB: at this age the stub
        # rule is a WARNING, so a BROKEN here is this rule and not that one.
        self.assertEqual(self._findings("CHK-OBS-SURVEY-STUB")[0], [])

    def test_visible_silent_while_the_record_is_only_staged(self):
        """The near-miss: the healthy in-flight state. The planned record
        exists under `staged/`, which no walk sees, and NOT under
        `observations/`.

        The control promotes the identical bytes and must fire the rule —
        without it this passes against a rule that never fires.
        """
        rows = self._visible_rows()
        paths = self._batch(rows=rows, signed=self._days_ago(0))
        paths["staged"].mkdir(parents=True, exist_ok=True)
        body = "---\nid: OBS-0001\n---\n\n# OBS-0001\n"
        (paths["staged"] / "OBS-0001-slug.md").write_text(body, encoding="utf-8")
        broken, _ = self._findings("CHK-OBS-SURVEY-VISIBLE")
        self.assertEqual(broken, [], "a staged-only record reported visible")

        # positive control: promote the same bytes.
        (self.obs / "OBS-0001-slug.md").write_text(body, encoding="utf-8")
        broken, _ = self._findings("CHK-OBS-SURVEY-VISIBLE")
        self.assertEqual(len(broken), 1,
                         "the staged fixture cannot fire the rule at all")

    def test_visible_silent_when_the_batch_records_completion(self):
        """Past the commit point a visible record is the POINT of the batch.
        Control: the same visible record under an incomplete receipt fires."""
        self._obs("OBS-0001", status="observed")
        self._batch(rows=self._visible_rows(), signed=self._days_ago(0),
                    completed=self._days_ago(0), records=["OBS-0001"])
        broken, _ = self._findings("CHK-OBS-SURVEY-VISIBLE")
        self.assertEqual(broken, [])

        self._batch(rows=self._visible_rows(), signed=self._days_ago(0))
        broken, _ = self._findings("CHK-OBS-SURVEY-VISIBLE")
        self.assertEqual(len(broken), 1,
                         "the completed-batch fixture cannot fire the rule")

    def test_visible_ignores_a_retired_predecessor_still_on_disk(self):
        """The other near-miss, and the reason the rule reads `record_path`
        and never `retires`: a predecessor a batch retires is visible BEFORE
        the batch starts and stays visible throughout. Reading `retires` here
        would report BROKEN on every healthy batch that retires anything."""
        self._obs("OBS-0009", status="ratified")
        rows = [{"anchor_id": self.A1, "record_id": "OBS-0010",
                 "record_path": "observations/OBS-0010-slug.md",
                 "retires": "observations/OBS-0009-slug.md"}]
        self._batch(rows=rows, signed=self._days_ago(0))
        broken, _ = self._findings("CHK-OBS-SURVEY-VISIBLE")
        self.assertEqual(broken, [], "a retired predecessor read as a "
                                     "premature promote")

        # positive control: the batch's OWN record becoming visible fires.
        (self.obs / "OBS-0010-slug.md").write_text(
            "---\nid: OBS-0010\n---\n\n# OBS-0010\n", encoding="utf-8")
        broken, _ = self._findings("CHK-OBS-SURVEY-VISIBLE")
        self.assertEqual(len(broken), 1,
                         "the retires fixture cannot fire the rule at all")

    # ---- CHK-OBS-SURVEY-DIGEST ------------------------------------------

    def test_digest_mismatch_after_the_sheet_is_edited_is_broken(self):
        """The trigger: the receipt's digest was computed over one rationale
        and the archived sheet now carries another."""
        bound = [{"anchor_id": self.A1, "rationale": "The code holds it."}]
        edited = [{"anchor_id": self.A1, "rationale": "The code does not."}]
        self._batch(rows=bound, archived_rows=edited)
        broken, _ = self._findings("CHK-OBS-SURVEY-DIGEST")
        self.assertEqual(len(broken), 1, broken)
        self.assertIn("SVY-0001", broken[0])

    def test_digest_silent_on_reordered_rows_and_refolded_whitespace(self):
        """The near-miss, and it is the rule's whole contract: the digest is
        canonicalized in ANCHOR order with `space_fold` per cell, so
        reordering the rows and re-spacing a rationale are non-changes.

        The control edits one cell's WORDS in the same fixture and must fire —
        a rule that ignored the sheet entirely would pass the first half.
        """
        bound = [{"anchor_id": self.A1, "rationale": "The code holds it."},
                 {"anchor_id": self.A2, "rationale": "So does this one."}]
        shuffled = [{"anchor_id": self.A2, "rationale": "So   does this\n one."},
                    {"anchor_id": self.A1, "rationale": "The  code holds  it."}]
        self._batch(rows=bound, archived_rows=shuffled)
        broken, _ = self._findings("CHK-OBS-SURVEY-DIGEST")
        self.assertEqual(broken, [], "reordering or re-spacing moved the digest")

        # positive control: one cell's words, same order, same spacing.
        edited = [{"anchor_id": self.A1, "rationale": "The code holds it."},
                  {"anchor_id": self.A2, "rationale": "So does that one."}]
        self._batch(rows=bound, archived_rows=edited)
        broken, _ = self._findings("CHK-OBS-SURVEY-DIGEST")
        self.assertEqual(len(broken), 1,
                         "the reorder fixture cannot fire the rule at all")

    def test_digest_broken_when_the_archived_sheet_is_gone(self):
        """A receipt whose sheet was deleted binds nothing, which is the same
        finding. Control: writing the matching sheet back clears it."""
        self._batch(write_sheet=False)
        broken, _ = self._findings("CHK-OBS-SURVEY-DIGEST")
        self.assertEqual(len(broken), 1, broken)

        self._batch()
        broken, _ = self._findings("CHK-OBS-SURVEY-DIGEST")
        self.assertEqual(broken, [])

    # ---- CHK-OBS-SURVEY-RECORD ------------------------------------------

    def _log(self, op, subject, body):
        (self.tree / "log.md").write_text(
            f"# Log\n\n## [2026-08-30] {op} | {subject}\n\n{body}\n",
            encoding="utf-8")

    def test_record_with_neither_receipt_nor_log_op_is_broken(self):
        """The trigger: a ratified record no receipt covers and no
        `observation` log op stands behind."""
        self._obs("OBS-0001", status="ratified", anchor_id=self.A1)
        self._index("| id |\n|---|\n| OBS-0001 |\n")
        broken, _ = self._findings("CHK-OBS-SURVEY-RECORD")
        self.assertEqual(len(broken), 1, broken)
        self.assertIn("OBS-0001", broken[0])

    def test_record_silent_when_a_receipt_covers_the_anchor(self):
        """Near-miss one: the batch route. Control: pointing the receipt at a
        different anchor — one byte of the fixture — fires the rule."""
        self._obs("OBS-0001", status="ratified", anchor_id=self.A1)
        self._index("| id |\n|---|\n| OBS-0001 |\n")
        self._batch(rows=[{"anchor_id": self.A1}],
                    signed=self._days_ago(0), completed=self._days_ago(0),
                    records=["OBS-0001"])
        broken, _ = self._findings("CHK-OBS-SURVEY-RECORD")
        self.assertEqual(broken, [], "a receipt-covered record reported BROKEN")

        # positive control: the same batch covering some OTHER anchor.
        self._batch(rows=[{"anchor_id": self.A2}],
                    signed=self._days_ago(0), completed=self._days_ago(0),
                    records=["OBS-0001"])
        broken, _ = self._findings("CHK-OBS-SURVEY-RECORD")
        self.assertEqual(len(broken), 1,
                         "the covered-anchor fixture cannot fire the rule")

    def test_record_silent_when_an_observation_log_op_names_it(self):
        """Near-miss two, and the reason the rule is a DISJUNCTION: the
        single-record route (§17.2) writes no receipt at all, so a conjunction
        would report BROKEN on every record that route has written.

        Control: the identical entry under a different op must NOT satisfy it.
        """
        self._obs("OBS-0001", status="ratified", anchor_id=self.A1)
        self._index("| id |\n|---|\n| OBS-0001 |\n")
        self._log("observation", "ratify", "OBS-0001 ratified.")
        broken, _ = self._findings("CHK-OBS-SURVEY-RECORD")
        self.assertEqual(broken, [], "a logged record reported BROKEN")

        # positive control: same subject, same body, wrong op.
        self._log("adr", "ratify", "OBS-0001 ratified.")
        broken, _ = self._findings("CHK-OBS-SURVEY-RECORD")
        self.assertEqual(len(broken), 1,
                         "a non-`observation` op satisfied the rule")

    def test_record_silent_when_the_batch_log_entry_names_it_in_the_body(self):
        """The batch writer's own shape (§6): the subject is
        `survey batch SVY-NNNN` and the record ids live in the BODY."""
        self._obs("OBS-0001", status="ratified", anchor_id=self.A1)
        self._index("| id |\n|---|\n| OBS-0001 |\n")
        self._log("observation", "survey batch SVY-0001", "Records: OBS-0001.")
        broken, _ = self._findings("CHK-OBS-SURVEY-RECORD")
        self.assertEqual(broken, [])

        # positive control: the same entry naming a different record.
        self._log("observation", "survey batch SVY-0001", "Records: OBS-0002.")
        broken, _ = self._findings("CHK-OBS-SURVEY-RECORD")
        self.assertEqual(len(broken), 1,
                         "the body scan matched a record it does not name")

    def test_record_silent_on_a_non_ratified_record(self):
        """Near-miss three: the rule's subject is `ratified` alone. An
        `observed` record is survey debt (CHK-OBS-STALE), not an unrecorded
        ratification. Control: flipping the status fires the rule."""
        self._obs("OBS-0001", status="observed", anchor_id=self.A1)
        self._index("| id |\n|---|\n| OBS-0001 |\n")
        broken, _ = self._findings("CHK-OBS-SURVEY-RECORD")
        self.assertEqual(broken, [])

        self._obs("OBS-0001", status="ratified", anchor_id=self.A1)
        broken, _ = self._findings("CHK-OBS-SURVEY-RECORD")
        self.assertEqual(len(broken), 1,
                         "the observed-record fixture cannot fire the rule")

    # ---- the healthy tree, and the rules' shared silence -----------------

    def test_all_four_rules_silent_on_a_tree_with_no_batches(self):
        """No `_surveys/` directory, one observed record: every batch rule is
        silent. The control is the rest of this class — each rule is shown
        firing on its own trigger fixture above, so this is a real negative
        rather than four rules that never run."""
        self._obs("OBS-0001", status="observed", anchor_id=self.A1)
        self._index("| id |\n|---|\n| OBS-0001 |\n")
        r = self._run()
        survey = [f for f in r["broken"] + r["warning"]
                  if f.startswith("CHK-OBS-SURVEY-")]
        self.assertEqual(survey, [])

    def test_an_unreadable_receipt_is_broken_and_never_a_traceback(self):
        """The fail-closed edge. A receipt that will not parse cannot yield a
        batch state, and the checker must report it rather than raise — the
        crux exit convention reserves a non-zero exit with EMPTY stdout for a
        crash, which leaves `audit-docs` with no verdict at all."""
        paths = self._batch(signed=self._days_ago(0))
        paths["receipt"].write_text("config_version: '1'\nrows: []\n",
                                    encoding="utf-8")
        broken, _ = self._findings("CHK-OBS-SURVEY-STUB")
        self.assertEqual(len(broken), 1, broken)
        self.assertIn("unreadable", broken[0])

    def test_the_rule_ids_are_the_four_the_contract_names(self):
        """The ids are the contract (§17.3 names them and `audit-docs`
        restates them), so a rename is a break rather than a refactor. Each is
        asserted against the fixture that fires it, so the set cannot be
        satisfied by a string constant nothing emits."""
        emitted = set()

        self._batch(signed=self._days_ago(2))
        emitted.update(b.split(":")[0] for b in self._run()["broken"])
        emitted.update(w.split(":")[0] for w in self._run()["warning"])

        self._obs("OBS-0001", status="observed")
        self._batch(rows=self._visible_rows(), signed=self._days_ago(0))
        emitted.update(b.split(":")[0] for b in self._run()["broken"])

        self._batch(rows=[{"anchor_id": self.A1, "rationale": "a"}],
                    archived_rows=[{"anchor_id": self.A1, "rationale": "b"}])
        emitted.update(b.split(":")[0] for b in self._run()["broken"])

        self._obs("OBS-0002", status="ratified", anchor_id=self.A2)
        emitted.update(b.split(":")[0] for b in self._run()["broken"])

        self.assertEqual(
            {e for e in emitted if e.startswith("CHK-OBS-SURVEY")},
            {"CHK-OBS-SURVEY-STUB", "CHK-OBS-SURVEY-VISIBLE",
             "CHK-OBS-SURVEY-DIGEST", "CHK-OBS-SURVEY-RECORD"})
