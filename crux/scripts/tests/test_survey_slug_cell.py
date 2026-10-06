"""The survey sheet's slug cell (ADR-0099 clause 3; handle
`ADR-0099/survey-sheet-slug-cell`), and the three version seams the cell
opened when it minted `config_version: "3"`.

Three claims, each with the near-miss control that must NOT refuse:

  * **The tripwire.** The archived v2 batch under
    `<tree>/observations/_surveys/SVY-0001/` is read FROM DISK and must keep
    digesting to the value its signed receipt records. That receipt is
    already signed downstream; a v3 edit that re-digested it would report
    CHK-OBS-SURVEY-DIGEST BROKEN on a batch nobody touched, and both
    projections would refuse the whole tree. This test is what says so the
    moment it happens.
  * **The version selectors raise.** `sheet_row_keys`, `provenance_keys`,
    `cell_norms` and `receipt_row_keys` are keyed lookups with no fallback.
    The `else`-branch they replaced is exactly how a v3 edit re-digests a v2
    sheet: a v2 sheet falls into the "latest" arm and picks up v3's key list.
  * **The slug grammar.** The cell validator is the handle grammar
    intersected with the filename grammar. `slugify` builds `3-way-merge`
    from a rule whose title opens with a digit, the filename grammar admits
    it, and `summaries_projection._OBS_HANDLE_ANCHOR_RE` then refuses the
    handle fail-closed FOR THE WHOLE TREE. The cell validator closes that
    seam, and one test pins the two grammars together so they cannot drift.
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

try:                                          # discovery runs this as a package
    from ._dev_surface import REPO_ROOT, TREE, require_dev_surface
except ImportError:                           # ... and as a bare directory
    from _dev_surface import REPO_ROOT, TREE, require_dev_surface  # type: ignore[no-redef]

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


SS = _load("survey_sheet")
import summaries_projection as SP  # noqa: E402

# The archived batch. Read from disk on every run — it is the one v2 sheet
# with a signature already on it, and the whole point of reading it live is
# that a fixture copied into this file would drift from the archive it stands
# for. The digest is the one `receipt.yml` beside the sheet records.
ARCHIVED_BATCH = REPO_ROOT / TREE / "observations" / "_surveys" / "SVY-0001"
ARCHIVED_SHEET = ARCHIVED_BATCH / "sheet.yml"
ARCHIVED_RECEIPT = ARCHIVED_BATCH / "receipt.yml"
ARCHIVED_V2_DIGEST = (
    "dbdaea41592546e5913d9b32cad2b9bada44b98e613bde4567ba01917cf580d7")


class ArchivedV2BatchTests(unittest.TestCase):
    """C1 — the tripwire, and R2's live-fixture requirement."""

    def setUp(self):
        require_dev_surface(self, ARCHIVED_SHEET,
                            f"{TREE}/observations/_surveys/SVY-0001/sheet.yml")
        require_dev_surface(self, ARCHIVED_RECEIPT,
                            f"{TREE}/observations/_surveys/SVY-0001/receipt.yml")

    def test_the_archived_v2_sheet_still_digests_to_its_signed_value(self):
        """Read from disk, digested through the live module, compared with
        the hex the signed receipt beside it records."""
        sheet = SS.read_sheet(ARCHIVED_SHEET)
        self.assertEqual(sheet["config_version"], "2",
                         "the archived SVY-0001 sheet is the v2 fixture; if "
                         "it is no longer v2 this tripwire guards nothing")
        got = SS.sheet_digest(sheet)
        self.assertEqual(
            got, ARCHIVED_V2_DIGEST,
            "TRIPWIRE: the archived v2 sheet SVY-0001 no longer digests to "
            "the value its signed receipt records. A change to survey_sheet "
            "has re-digested an already-signed v2 sheet — most likely a v3 "
            "key list, fold or provenance list leaking into the v2 preimage. "
            "Every batch already published downstream would report "
            "CHK-OBS-SURVEY-DIGEST BROKEN and both projections would refuse "
            f"the whole tree. Got {got[:12]}…, receipt records "
            f"{ARCHIVED_V2_DIGEST[:12]}…")
        # Positive control: the receipt on disk records that same hex, so the
        # constant above is the archive's value and not this file's.
        text = ARCHIVED_RECEIPT.read_text(encoding="utf-8")
        self.assertIn(f'digest: "{ARCHIVED_V2_DIGEST}"', text)


# ── R1: the version selectors are keyed lookups, and they raise ────────────

UNKNOWN_VERSIONS = ("4", "", None, "0", "2.0")


class VersionSelectorTests(unittest.TestCase):

    def test_each_selector_raises_on_an_unknown_version(self):
        for selector in (SS.sheet_row_keys, SS.provenance_keys, SS.cell_norms,
                         SS.receipt_row_keys):
            for version in UNKNOWN_VERSIONS:
                with self.subTest(selector=selector.__name__, version=version):
                    with self.assertRaises(SS.SurveySheetError) as ctx:
                        selector(version)
                    self.assertIn("config_version", str(ctx.exception))

    def test_each_selector_answers_every_supported_version(self):
        """Positive control for the raise: the three supported versions all
        resolve, through the same lookup, to a non-empty answer."""
        self.assertEqual(SS.SUPPORTED_CONFIG_VERSIONS, ("1", "2", "3"))
        for version in SS.SUPPORTED_CONFIG_VERSIONS:
            with self.subTest(version=version):
                self.assertTrue(SS.sheet_row_keys(version))
                self.assertTrue(SS.provenance_keys(version))
                self.assertTrue(SS.receipt_row_keys(version))
                digest_fold, publish_fold = SS.cell_norms(version)
                self.assertTrue(callable(digest_fold) and callable(publish_fold))

    def test_the_v1_and_v2_key_lists_are_pinned_by_content(self):
        """A v2 sheet does NOT pick up v3's key list. Asserted on the tuple
        itself, not its length — a same-arity substitution would pass an
        arity check and still re-digest every archived sheet."""
        self.assertEqual(SS.sheet_row_keys("1"),
                         ("anchor_id", "proposed_domain", "verdict", "domain",
                          "rationale"))
        self.assertEqual(SS.sheet_row_keys("2"),
                         ("anchor_id", "proposed_domain", "rule", "evidence",
                          "verdict", "domain", "rationale"))
        self.assertEqual(SS.provenance_keys("1"),
                         ("tool", "scaffolded", "state_file", "candidates"))
        self.assertEqual(SS.provenance_keys("2"),
                         ("tool", "scaffolded", "state_file", "candidates",
                          "tree", "tree_id"))
        self.assertEqual(SS.receipt_row_keys("1"),
                         ("anchor_id", "candidate_id", "verdict", "domain",
                          "domain_source", "record_id", "record_path",
                          "retires"))
        self.assertEqual(SS.receipt_row_keys("2"), SS.receipt_row_keys("1"))
        self.assertIs(SS.cell_norms("1")[0], SS.space_fold)
        self.assertIs(SS.cell_norms("1")[1], SS._v1_publish_canon)
        self.assertEqual(SS.cell_norms("2"), (SS.cell_canon, SS.cell_canon))

    def test_v3_is_v2_plus_the_slug_cell_and_its_source(self):
        self.assertEqual(SS.CONFIG_VERSION, "3")
        self.assertEqual(set(SS.sheet_row_keys("3")) - set(SS.sheet_row_keys("2")),
                         {"slug"})
        self.assertEqual(len(SS.sheet_row_keys("3")), 8)
        self.assertEqual(SS.provenance_keys("3"), SS.provenance_keys("2"))
        self.assertEqual(set(SS.receipt_row_keys("3")) - set(SS.receipt_row_keys("2")),
                         {"slug_source"})
        self.assertEqual(len(SS.receipt_row_keys("3")), 9)
        # v3 shares v2's canonicalization pair: the slug cell is ASCII by
        # grammar, so nothing about the fold had to change.
        self.assertEqual(SS.cell_norms("3"), SS.cell_norms("2"))


# ── R2: the receipt's row key set is selected on ITS OWN config_version ────

A1 = "0123456789abcdef"


def _receipt_row(**over):
    row = {"anchor_id": A1, "candidate_id": A1, "verdict": "ratify",
           "domain": "storage", "domain_source": "proposed",
           "record_id": "", "record_path": "", "retires": ""}
    row.update(over)
    return row


def _receipt(version, rows):
    prov = {"tool": "scaffold-survey-sheet.py", "scaffolded": "2026-08-30",
            "state_file": "arch/_recovered/state.yml", "candidates": 1}
    if version != "1":
        prov.update({"tree": "bionic", "tree_id": "0" * 64})
    return {"config_version": version, "batch_id": "SVY-0001",
            "sheet_ref": "observations/_surveys/SVY-0001/sheet.yml",
            "digest": "a" * 64, "scaffold_provenance": prov,
            "signed": "2026-08-30", "completed": None, "records": [],
            "rows": rows}


class ReceiptRowKeysTests(unittest.TestCase):

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.obs = Path(self.tmp.name) / "bionic" / "observations"
        self.obs.mkdir(parents=True)

    def _write(self, doc):
        import yaml
        paths = SS.receipt_paths(self.obs, "SVY-0001")
        paths["dir"].mkdir(parents=True, exist_ok=True)
        paths["receipt"].write_text(
            yaml.safe_dump(doc, sort_keys=False, allow_unicode=True),
            encoding="utf-8")
        return paths["receipt"]

    def test_the_archived_v2_receipt_still_reads_under_its_own_key_set(self):
        """R2's live fixture: the 8-cell receipt already signed in this tree
        parses, and its rows carry exactly `receipt_row_keys("2")`. If this
        refuses, the batch drops below S9 and both projections exit 2 with
        nothing written."""
        require_dev_surface(self, ARCHIVED_RECEIPT,
                            f"{TREE}/observations/_surveys/SVY-0001/receipt.yml")
        receipt = SS.read_receipt(ARCHIVED_RECEIPT)
        self.assertEqual(receipt["config_version"], "2")
        self.assertTrue(receipt["rows"], "the archived receipt carries rows")
        for row in receipt["rows"]:
            self.assertEqual(tuple(row), SS.receipt_row_keys("2"))
        self.assertNotIn("slug_source", receipt["rows"][0])

    def test_a_v2_receipt_refuses_the_v3_arity_and_accepts_its_own(self):
        with self.assertRaises(SS.SurveySheetError) as ctx:
            SS.read_receipt(self._write(
                _receipt("2", [_receipt_row(slug_source="proposed")])))
        self.assertIn("slug_source", str(ctx.exception))
        # Positive control: the 8-key row reads back.
        back = SS.read_receipt(self._write(_receipt("2", [_receipt_row()])))
        self.assertEqual(back["rows"][0]["domain_source"], "proposed")

    def test_a_v3_receipt_refuses_the_v2_arity_and_accepts_its_own(self):
        with self.assertRaises(SS.SurveySheetError) as ctx:
            SS.read_receipt(self._write(_receipt("3", [_receipt_row()])))
        self.assertIn("slug_source", str(ctx.exception))
        # Positive control: the 9-key row reads back, with its source intact.
        back = SS.read_receipt(self._write(
            _receipt("3", [_receipt_row(slug_source="overridden")])))
        self.assertEqual(back["rows"][0]["slug_source"], "overridden")

    def test_slug_source_is_the_domain_source_vocabulary(self):
        """One name per thing: the slug's source is the SAME concept as the
        domain's (proposed by the scaffold, or overridden by the human), so
        the receipt validates it against `DOMAIN_SOURCES` and nothing else."""
        for bad in ("", "human", "guessed", "PROPOSED"):
            with self.subTest(value=bad):
                with self.assertRaises(SS.SurveySheetError) as ctx:
                    SS.read_receipt(self._write(
                        _receipt("3", [_receipt_row(slug_source=bad)])))
                self.assertIn("slug_source", str(ctx.exception))
        for good in SS.DOMAIN_SOURCES:
            with self.subTest(value=good):
                back = SS.read_receipt(self._write(
                    _receipt("3", [_receipt_row(slug_source=good)])))
                self.assertEqual(back["rows"][0]["slug_source"], good)

    def test_write_receipt_orders_rows_by_the_receipt_s_own_version(self):
        """The writer selects on the receipt's version too: a v2 receipt
        written back (a resume rewrites it on every cell) keeps 8 keys."""
        tree = self.obs.parent
        paths = SS.receipt_paths(self.obs, "SVY-0001")
        paths["dir"].mkdir(parents=True, exist_ok=True)
        SS.write_receipt(paths["receipt"], _receipt("2", [_receipt_row()]),
                         contained_under=tree)
        self.assertEqual(tuple(SS.read_receipt(paths["receipt"])["rows"][0]),
                         SS.receipt_row_keys("2"))
        SS.write_receipt(paths["receipt"],
                         _receipt("3", [_receipt_row(slug_source="proposed")]),
                         contained_under=tree)
        self.assertEqual(tuple(SS.read_receipt(paths["receipt"])["rows"][0]),
                         SS.receipt_row_keys("3"))


# ── R1: the refusal names the actual mismatch ──────────────────────────────

class AssertSignableMessageTests(unittest.TestCase):

    def _sheet(self, version):
        return {"config_version": version, "batch_id": "SVY-0001",
                "scaffold_provenance": {}, "rows": []}

    def test_a_v2_sheet_is_refused_for_its_missing_slug_cell_only(self):
        """A v2 row DOES carry `rule` and `evidence`; what it lacks is the
        slug cell. A refusal that said otherwise would send the human to the
        wrong remedy."""
        with self.assertRaises(SS.SurveySheetError) as ctx:
            SS.assert_signable(self._sheet("2"))
        text = str(ctx.exception)
        self.assertIn("config_version", text)
        self.assertIn("`slug`", text)
        self.assertNotIn("no `rule`", text)
        self.assertNotIn("no `evidence`", text)
        self.assertIn("Re-scaffold", text)

    def test_a_v1_sheet_is_refused_for_all_three_missing_cells(self):
        with self.assertRaises(SS.SurveySheetError) as ctx:
            SS.assert_signable(self._sheet("1"))
        text = str(ctx.exception)
        for cell in ("`rule`", "`evidence`", "`slug`"):
            self.assertIn(cell, text)

    def test_the_current_version_is_signable(self):
        SS.assert_signable(self._sheet(SS.CONFIG_VERSION))   # no raise


# ── R5: the slug cell grammar, pinned to the handle grammar ────────────────

REFUSED_SLUGS = ("", "3-way-merge", "9lives", "Serve-LLM", "serve-llm-",
                 "serve--llm", "-serve-llm", "serve llm", "serve_llm",
                 "serve.llm", "sérve-llm", "serve-llm\n")
ACCEPTED_SLUGS = ("serve-llm-cors", "a", "a1", "serve-llm-2", "x-3-way-merge",
                  "the-serve-llm-http-server-allows-every-cors-origin-method-an")


class SlugCellGrammarTests(unittest.TestCase):

    def test_refused_slugs(self):
        for slug in REFUSED_SLUGS:
            with self.subTest(slug=slug):
                self.assertFalse(SS.valid_slug_cell(slug))

    def test_accepted_slugs(self):
        for slug in ACCEPTED_SLUGS:
            with self.subTest(slug=slug):
                self.assertTrue(SS.valid_slug_cell(slug))

    def test_the_seam_is_real_the_filename_grammar_admits_what_the_handle_refuses(self):
        """Positive control for the whole class: `3-way-merge` is what
        `slugify` builds from a digit-led title, the filename grammar admits
        it, and the handle grammar refuses it. Without this seam the cell
        validator would be closing nothing."""
        self.assertEqual(SS.slugify("3-way merge of the config"),
                         "3-way-merge-of-the-config")
        self.assertIsNotNone(SS._record_filename_parts("OBS-0001-3-way-merge.md"))
        self.assertIsNone(SP._OBS_HANDLE_ANCHOR_RE.match("OBS-0001/3-way-merge"))

    def test_every_accepted_slug_composes_into_a_valid_handle_and_filename(self):
        for slug in ACCEPTED_SLUGS:
            with self.subTest(slug=slug):
                self.assertIsNotNone(
                    SP._OBS_HANDLE_ANCHOR_RE.match(f"OBS-0001/{slug}"))
                self.assertIsNotNone(
                    SS._record_filename_parts(f"OBS-0001-{slug}.md"))

    def test_the_cell_grammar_is_exactly_the_two_grammars_intersected(self):
        """Exhaustive over every string of length <= 4 on a four-symbol
        alphabet — a letter, a digit, a hyphen, an uppercase letter — so the
        cell grammar and the handle-AND-filename grammar are the same set on
        every shape short enough to enumerate. Neither regex is a fixture
        here; both are read live, so a drift in either fails."""
        import itertools
        alphabet = "a1-A"
        checked = 0
        for n in range(0, 5):
            for chars in itertools.product(alphabet, repeat=n):
                s = "".join(chars)
                composed = (
                    SP._OBS_HANDLE_ANCHOR_RE.match(f"OBS-0001/{s}") is not None
                    and SS._record_filename_parts(f"OBS-0001-{s}.md") is not None)
                self.assertEqual(SS.valid_slug_cell(s), composed, repr(s))
                checked += 1
        self.assertGreater(checked, 300)


# ── the scaffold proposes, the human may overwrite, the sign-off refuses ───

try:                                          # discovery runs this as a package
    from .test_survey_signoff import (
        DATE, SO, SS as _SS_SIGNOFF, StateFile, _SignoffCase,
        read_recorded_observations,
    )
except ImportError:                           # ... and as a bare directory
    from test_survey_signoff import (         # type: ignore[no-redef]
        DATE, SO, SS as _SS_SIGNOFF, StateFile, _SignoffCase,
        read_recorded_observations,
    )

OTHER_ANCHOR = "f" * 16


class _SlugCase(_SignoffCase):
    """`_SignoffCase` plus the three fixtures the slug cell needs: editing
    the cell, planting a live handle in the corpus, and planting a retired
    one."""

    def _sheet_path(self, bid):
        return self.obs / f"survey-{bid}.yml"

    def _set_slug(self, bid, anchor, slug):
        path = self._sheet_path(bid)
        sheet = SS.read_sheet(path)
        for row in sheet["rows"]:
            if row["anchor_id"] == anchor:
                row["slug"] = slug
        SS.write_sheet(path, sheet, contained_under=self.tree)

    def _findings(self, proc):
        return " ".join(self._payload(proc)["findings"])

    def _plant_live_observation(self, slug, record_id="OBS-0009"):
        """A ratified record on an UNRELATED anchor whose handle owns `slug`.
        Shaped as `signoff-survey.record_body` writes one, so the corpus
        reader admits it."""
        (self.obs / f"{record_id}-{slug}.md").write_text(
            "---\n"
            f"id: {record_id}\n"
            'title: "An earlier record"\n'
            "status: ratified\n"
            f"date: {DATE}\nobserved_date: {DATE}\nratified_date: {DATE}\n"
            "rejected_date: null\nretired_date: null\ndecided_date: null\n"
            "provenance: recovered\ndecided_by: null\n"
            'evidence: ["src/b.py:1-1"]\n'
            f'anchor_id: "{OTHER_ANCHOR}"\n'
            "related_invariants: []\ntags: []\n"
            "governs:\n"
            '  - domain: "runtime"\n'
            '    rule: "An earlier rule"\n'
            '    scope: "src/b.py"\n'
            f"    handle: {record_id}/{slug}\n"
            "    provenance: recovered\n"
            "---\n\nbody\n", encoding="utf-8")

    def _plant_adr(self, num, entries, status="Accepted"):
        adrs = self.tree / "adrs"
        adrs.mkdir(exist_ok=True)
        lines = ["---", f"id: ADR-{num:04d}", f'title: "Decision {num}"',
                 f"status: {status}", f"date: {DATE}", "supersedes: []",
                 "superseded_by: null", "tags: [test]", "governs:"]
        for e in entries:
            first = True
            for key, value in e.items():
                prefix = "  - " if first else "    "
                first = False
                if isinstance(value, list):
                    value = "[" + ", ".join(value) + "]"
                elif key in ("rule",):
                    value = f'"{value}"'
                lines.append(f"{prefix}{key}: {value}")
        lines += ["---", "", f"# ADR-{num:04d}", "", "Body.", ""]
        (adrs / f"ADR-{num:04d}-decision.md").write_text(
            "\n".join(lines) + "\n", encoding="utf-8")

    def _plant_retired_slug(self, slug):
        """`ADR-0001/<slug>` retired by `ADR-0002/successor`: the slug is then
        a retired slug in `live_and_retired_slugs`, with one displacer."""
        entry = {"handle": f"ADR-0001/{slug}", "domain": "d",
                 "rule": "the old rule", "scope": "crux/scripts",
                 "provenance": "authored"}
        self._plant_adr(1, [entry])
        self._plant_adr(2, [{"handle": "ADR-0002/successor", "domain": "d",
                             "rule": "the new rule", "scope": "crux/scripts",
                             "provenance": "authored",
                             "retires": [f"ADR-0001/{slug}"]}])


class ScaffoldProposesTheSlugTests(_SlugCase):

    def test_the_scaffold_writes_a_v3_sheet_with_a_proposed_slug(self):
        bid = self._scaffold()
        sheet = SS.read_sheet(self._sheet_path(bid))
        self.assertEqual(sheet["config_version"], "3")
        rows = {r["anchor_id"]: r for r in sheet["rows"]}
        for anchor, row in rows.items():
            with self.subTest(anchor=anchor):
                self.assertEqual(tuple(row), SS.sheet_row_keys("3"))
                self.assertTrue(SS.valid_slug_cell(row["slug"]), row["slug"])
                # The proposal is the title-derived slug, through the one
                # shared function the sign-off recomputes it with.
                cand = self._rows()[anchor]
                self.assertEqual(row["slug"], SS.proposed_slug(cand))
        self.assertEqual(rows[self.a1]["slug"], "the-gateway-depends-on-httpx")
        # The three human cells are still empty (ADR-0098 clause 4).
        self.assertEqual([rows[self.a1][k] for k in ("verdict", "domain",
                                                     "rationale")],
                         ["", "", ""])

    def test_the_slug_cell_is_inside_the_digest(self):
        bid = self._scaffold()
        sheet = SS.read_sheet(self._sheet_path(bid))
        before = SS.sheet_digest(sheet)
        sheet["rows"][0]["slug"] = "another-slug"
        self.assertNotEqual(SS.sheet_digest(sheet), before)


class SignoffSlugRefusalTests(_SlugCase):
    """Each refusal, with the positive control: a fresh, well-formed, unused
    slug publishes. The control is one test rather than one per refusal
    because every refusal edits the SAME cell on the same fixture."""

    def _refuses(self, bid, needle, planted=()):
        proc = self._run("--batch", bid)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        findings = self._findings(proc)
        self.assertIn(needle, findings)
        self.assertIn("slug", findings)
        # Nothing published: the only records on disk are the ones the
        # fixture planted before the run.
        self.assertEqual(self._records(), sorted(planted))
        self.assertFalse(SS.receipt_paths(self.obs, bid)["receipt"].exists(),
                         "refused BEFORE the bind — nothing written")
        return findings

    def test_the_control_a_fresh_slug_publishes_under_that_slug(self):
        bid = self._prepare({self.a1: "ratify", self.a2: "defer"})
        self._set_slug(bid, self.a1, "serve-llm-cors")
        proc = self._run("--batch", bid)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        [name] = self._records()
        self.assertEqual(name, "OBS-0001-serve-llm-cors.md")
        fm = self._frontmatter(name)
        self.assertEqual(fm["governs"][0]["handle"], "OBS-0001/serve-llm-cors")
        # The published handle is one the projection's own grammar admits —
        # the seam this cell closes.
        self.assertIsNotNone(
            SP._OBS_HANDLE_ANCHOR_RE.match(fm["governs"][0]["handle"]))

    def test_an_empty_slug_refuses(self):
        bid = self._prepare({self.a1: "ratify", self.a2: "defer"})
        self._set_slug(bid, self.a1, "")
        self._refuses(bid, "cell `slug` is empty")

    def test_a_malformed_slug_refuses(self):
        for bad in ("3-way-merge", "Serve-LLM", "serve llm", "serve-llm-"):
            with self.subTest(slug=bad):
                bid = self._prepare({self.a1: "ratify", self.a2: "defer"})
                self._set_slug(bid, self.a1, bad)
                self._refuses(bid, "is malformed")
                self._sheet_path(bid).unlink()   # one live sheet at a time

    def test_a_slug_a_live_handle_owns_refuses(self):
        self._plant_live_observation("serve-llm-cors")
        bid = self._prepare({self.a1: "ratify", self.a2: "defer"})
        self._set_slug(bid, self.a1, "serve-llm-cors")
        findings = self._refuses(bid, "already taken by the live handle",
                                 planted=["OBS-0009-serve-llm-cors.md"])
        self.assertIn("OBS-0009/serve-llm-cors", findings)

    def test_a_live_handle_in_the_corpus_does_not_refuse_a_different_slug(self):
        """Positive control for the live-handle fixture: the planted record
        is read (it is in the corpus) and refuses only its own slug."""
        self._plant_live_observation("serve-llm-cors")
        bid = self._prepare({self.a1: "ratify", self.a2: "defer"})
        self._set_slug(bid, self.a1, "serve-llm-cors-again")
        proc = self._run("--batch", bid)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        # Ids come from `observation.next_number`, not from the files on
        # disk, so the planted OBS-0009 does not move the allocation.
        self.assertEqual(self._records(), ["OBS-0001-serve-llm-cors-again.md",
                                           "OBS-0009-serve-llm-cors.md"])

    def test_a_retired_slug_refuses_and_names_the_displacer(self):
        self._plant_retired_slug("serve-llm-cors")
        bid = self._prepare({self.a1: "ratify", self.a2: "defer"})
        self._set_slug(bid, self.a1, "serve-llm-cors")
        findings = self._refuses(bid, "is a retired slug")
        self.assertIn("ADR-0002/successor", findings)

    def test_a_retired_slug_in_the_corpus_does_not_refuse_a_different_slug(self):
        """Positive control for the retired-slug fixture: both ADRs are read
        and the batch still publishes under an unrelated slug."""
        self._plant_retired_slug("serve-llm-cors")
        bid = self._prepare({self.a1: "ratify", self.a2: "defer"})
        self._set_slug(bid, self.a1, "serve-llm-cors-v2")
        proc = self._run("--batch", bid)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("OBS-0001-serve-llm-cors-v2.md", self._records())

    def test_a_slug_a_defer_row_carries_is_not_checked(self):
        """The refusals are on a RATIFY row (ADR-0099 clause 3). A deferred
        row's slug reaches no filename and no handle, so a malformed one
        does not stop the batch."""
        bid = self._prepare({self.a1: "ratify", self.a2: "defer"})
        self._set_slug(bid, self.a2, "3-way-merge")
        proc = self._run("--batch", bid)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(len(self._records()), 1)


class IntraBatchSlugCollisionTests(_SlugCase):
    """Two `ratify` rows of ONE batch proposing one slug.

    The live-and-retired maps are built once, from the corpus as it stood
    BEFORE the batch, so neither row's slug is taken by anything on disk and
    the corpus refusals above see nothing. Without a per-batch ledger both
    rows publish two live handles on one slug, `summaries_projection` then
    refuses the WHOLE tree fail-closed, and the batch wedges at S5 with
    `summarize-adrs` and `compile-doctrine` refusing for every other record
    too. The refusal belongs on the sheet, while it is still in the human's
    hands — the same lane as every other sheet finding, exit 1."""

    SHARED = "one-slug-two-rows"

    def test_two_ratify_rows_sharing_a_slug_refuse_and_publish_neither(self):
        bid = self._prepare({self.a1: "ratify", self.a2: "ratify"})
        self._set_slug(bid, self.a1, self.SHARED)
        self._set_slug(bid, self.a2, self.SHARED)
        proc = self._run("--batch", bid)
        # Exit 1 with a findings payload: a sheet finding, never the exit-2
        # corpus-fault lane, which callers skip rather than fail on.
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        findings = self._findings(proc)
        self.assertIn(self.SHARED, findings)
        # BOTH rows are named — the human has to change one of them, and the
        # message that names only the second does not say which pair collided.
        self.assertIn(self.a1, findings)
        self.assertIn(self.a2, findings)
        self.assertIn("in this same batch", findings)
        # NEITHER record is published, and the batch never binds: a batch is
        # signed or it is not.
        self.assertEqual(self._records(), [])
        self.assertFalse(SS.receipt_paths(self.obs, bid)["receipt"].exists(),
                         "refused BEFORE the bind — nothing written")

    def test_the_control_two_ratify_rows_with_distinct_slugs_publish_both(self):
        """The same two-row batch, one character apart: both records publish.
        Without this the refusal above is also satisfied by a sign-off that
        refuses every two-row batch."""
        bid = self._prepare({self.a1: "ratify", self.a2: "ratify"})
        self._set_slug(bid, self.a1, self.SHARED)
        self._set_slug(bid, self.a2, f"{self.SHARED}-b")
        proc = self._run("--batch", bid)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        names = self._records()
        self.assertEqual(len(names), 2, names)
        self.assertEqual(
            sorted(n.split("-", 2)[2] for n in names),
            sorted([f"{self.SHARED}.md", f"{self.SHARED}-b.md"]))

    def test_a_defer_row_does_not_claim_the_slug_a_ratify_row_publishes(self):
        """The ledger is the publish set. A `defer` row writes no record and
        no handle, so its slug cell — which is not checked at all — must not
        refuse the `ratify` row that carries the same value."""
        bid = self._prepare({self.a1: "ratify", self.a2: "defer"})
        self._set_slug(bid, self.a1, self.SHARED)
        self._set_slug(bid, self.a2, self.SHARED)
        proc = self._run("--batch", bid)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(len(self._records()), 1)


class OwnRecordExemptionIsRowScopedTests(_SlugCase):
    """The resume exemption: a slug owned by a record THIS batch published is
    this row's own output, not a collision.

    Paired against every record id the batch allocated it excused a PEER row
    too — any row of the batch could then take any record's slug, which is the
    intra-batch collision above wearing the exemption as cover. The exemption
    is the row's own record or nothing."""

    SHARED = "gateway-httpx-shared"

    def _publish_one(self):
        """Drive the shipped command to S9 on `a1` alone, and return
        `(record_id, the second batch's sheet)` — a live handle owning
        `SHARED`, plus a fresh sheet carrying only the deferred `a2` row."""
        bid = self._prepare({self.a1: "ratify", self.a2: "defer"})
        self._set_slug(bid, self.a1, self.SHARED)
        proc = self._run("--batch", bid)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        record_id = self._allocated(bid)[self.a1][0]
        return bid, record_id

    def _plan(self, sheet, bid, own_record_ids):
        live, retired = SO._slug_maps(SO.make_context(self.root, None, bid,
                                                      DATE))
        return SS.build_plan(
            sheet, StateFile(self.state_path).rows,
            read_recorded_observations(self.obs), root=self.root,
            obs_dir=self.obs, own_record_ids=own_record_ids,
            live_slugs=live, retired_slugs=retired)

    def test_a_peer_row_is_not_excused_by_another_row_s_record(self):
        _, record_id = self._publish_one()
        # `a2` was deferred, so it re-scaffolds; its row is the peer.
        bid2 = self._prepare({self.a2: "ratify"})
        self._set_slug(bid2, self.a2, self.SHARED)
        sheet = SS.read_sheet(self._sheet_path(bid2))
        with self.assertRaises(SS.SurveySheetError) as caught:
            self._plan(sheet, bid2, {self.a1: record_id})
        findings = " ".join(caught.exception.problems)
        self.assertIn("already taken by the live handle", findings)
        self.assertIn(f"{record_id}/{self.SHARED}", findings)

    def test_the_control_the_owning_row_is_still_excused(self):
        """The exemption's reason for existing: on a resume from S5 the
        batch's own promoted record is in the corpus, and its own row must
        still plan rather than refuse its own output."""
        bid, record_id = self._publish_one()
        sheet = SS.read_sheet(SS.receipt_paths(self.obs, bid)["sheet"])
        plan = self._plan(sheet, bid, {self.a1: record_id})
        self.assertEqual([e["slug"] for e in plan if e["verdict"] == "ratify"],
                         [self.SHARED])


class SlugSourceOnTheReceiptTests(_SlugCase):

    def _receipt_rows(self, bid):
        receipt = SS.read_receipt(SS.receipt_paths(self.obs, bid)["receipt"])
        self.assertEqual(receipt["config_version"], "3")
        for row in receipt["rows"]:
            self.assertEqual(tuple(row), SS.receipt_row_keys("3"))
        return {r["anchor_id"]: r for r in receipt["rows"]}

    def test_a_slug_left_as_proposed_records_proposed(self):
        bid = self._prepare({self.a1: "ratify", self.a2: "ratify"})
        proc = self._run("--batch", bid)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        rows = self._receipt_rows(bid)
        self.assertEqual(rows[self.a1]["slug_source"], "proposed")
        self.assertEqual(rows[self.a2]["slug_source"], "proposed")
        # The published filename carries the proposal.
        self.assertIn("the-gateway-depends-on-httpx",
                      rows[self.a1]["record_path"])

    def test_an_overwritten_slug_records_overridden_for_that_row_only(self):
        bid = self._prepare({self.a1: "ratify", self.a2: "ratify"})
        self._set_slug(bid, self.a1, "gateway-httpx")
        proc = self._run("--batch", bid)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        rows = self._receipt_rows(bid)
        self.assertEqual(rows[self.a1]["slug_source"], "overridden")
        self.assertEqual(rows[self.a2]["slug_source"], "proposed")
        self.assertTrue(rows[self.a1]["record_path"].endswith("-gateway-httpx.md"))
        self.assertIn("OBS-", rows[self.a1]["record_path"])

    def test_the_rendering_shows_the_slug_and_its_source(self):
        bid = self._prepare({self.a1: "ratify", self.a2: "defer"})
        self._set_slug(bid, self.a1, "gateway-httpx")
        proc = self._run("--batch", bid, "--dry-run")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        rendering = " ".join(self._payload(proc)["rendering"])
        self.assertIn("slug=gateway-httpx (overridden)", rendering)
