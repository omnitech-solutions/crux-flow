"""Tests for `survey_sheet.py` — the shared reader, validator, digest and
state derivation for batch ratification (ADR-0098, docs/AGENTS.md §17.5).

Shape of this suite: **one case per refusal, each paired with the near-miss
control that must NOT refuse.** A refusal test on its own proves only that
something failed; the control proves the refusal is keyed on the cell under
test and not on the fixture being broken in some other way. Every control here
is the same document with exactly one cell made valid.
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

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
from crux.arch.recover import (  # noqa: E402
    ANCHOR_KINDS,
    StateFile,
    canonical_anchor,
    claim_digest,
)
from _yaml_min import write_catalog_yaml  # noqa: E402

A1 = "0123456789abcdef"
A2 = "fedcba9876543210"


def _prov(**over):
    base = {"tool": "scaffold-survey-sheet.py", "scaffolded": "2026-08-30",
            "state_file": "arch/_recovered/state.yml", "candidates": 1}
    base.update(over)
    return base


def _row(anchor=A1, proposed="storage", verdict="ratify", domain="",
         rationale="It is what the code does."):
    return {"anchor_id": anchor, "proposed_domain": proposed,
            "verdict": verdict, "domain": domain, "rationale": rationale}


def _sheet(rows=None, **over):
    doc = {"config_version": "1", "batch_id": "SVY-0001",
           "scaffold_provenance": _prov(),
           "rows": rows if rows is not None else [_row()]}
    doc.update(over)
    return doc


# The v2 fixtures. v2 widened both key sets — the row gained the machine-seeded
# `rule` and `evidence`, the provenance gained the tree identity — so a v2
# document is a v1 document plus those four cells and nothing else.

def _prov_v2(**over):
    base = _prov()
    base.update({"tree": "bionic", "tree_id": "0" * 64})
    base.update(over)
    return base


def _row_v2(**over):
    row = _row()
    row.update({"rule": "The gateway depends on httpx",
                "evidence": "src/a.py:1-1"})
    row.update(over)
    return row


def _sheet_v2(rows=None, **over):
    doc = {"config_version": "2", "batch_id": "SVY-0001",
           "scaffold_provenance": _prov_v2(),
           "rows": rows if rows is not None else [_row_v2()]}
    doc.update(over)
    return doc


class _TreeCase(unittest.TestCase):
    """A minimal tree: `<root>/bionic/observations/` plus a real evidence file."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.tree = self.root / "bionic"
        self.obs = self.tree / "observations"
        self.obs.mkdir(parents=True)
        (self.root / "src").mkdir()
        (self.root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")

    def write_sheet(self, doc, name=None):
        path = self.obs / (name or f"survey-{doc['batch_id']}.yml")
        write_catalog_yaml(path, doc)
        return path

    def write_receipt(self, doc, batch_id="SVY-0001"):
        paths = SS.receipt_paths(self.obs, batch_id)
        paths["dir"].mkdir(parents=True, exist_ok=True)
        SS.write_receipt(paths["receipt"], doc, contained_under=self.tree)
        return paths["receipt"]

    def write_receipt_raw(self, doc, batch_id="SVY-0001"):
        """The receipt as bytes, past every writer-side guard.

        `SS.write_receipt` validates what it emits, so it cannot express a
        malformed receipt — and a malformed receipt on disk is exactly what
        `read_receipt` is asked to trust. A tamper is a raw write."""
        import yaml
        paths = SS.receipt_paths(self.obs, batch_id)
        paths["dir"].mkdir(parents=True, exist_ok=True)
        paths["receipt"].write_text(
            yaml.safe_dump(doc, sort_keys=False, allow_unicode=True),
            encoding="utf-8")
        return paths["receipt"]


# ── read_sheet: one refusal per case, each with its near-miss control ───────

class ReadSheetRefusalTests(_TreeCase):

    def _refuses(self, doc, needle, name=None):
        path = self.write_sheet(doc, name=name)
        with self.assertRaises(SS.SurveySheetError) as ctx:
            SS.read_sheet(path)
        self.assertIn(needle, str(ctx.exception))
        return ctx.exception

    def test_control_a_well_formed_sheet_reads(self):
        """The positive control every refusal below is one cell away from."""
        path = self.write_sheet(_sheet())
        doc = SS.read_sheet(path)
        self.assertEqual(doc["batch_id"], "SVY-0001")
        self.assertEqual(len(doc["rows"]), 1)

    def test_an_unsupported_config_version_refuses_by_name(self):
        """Three versions are read, and the refusal names the version.

        `"4"` is a schema this reader does not know. Its rows and its
        provenance are then whatever that schema says, so a refusal that
        reported the keys they happen to be missing would send the human to
        patch cells on a document no cell edit can fix. The version check
        therefore refuses before the key sets are read, and the message
        carries the version it got."""
        exc = self._refuses(_sheet(config_version="4"), "config_version")
        self.assertIn(f"must be one of {list(SS.SUPPORTED_CONFIG_VERSIONS)}",
                      str(exc))
        self.assertIn("'4'", str(exc))
        self.assertNotIn("missing the key", str(exc))
        # Controls: both supported versions read, each the same document with
        # its own schema's cells.
        SS.read_sheet(self.write_sheet(_sheet(config_version="1")))
        SS.read_sheet(self.write_sheet(_sheet_v2()))

    def test_unknown_top_level_key_refuses(self):
        doc = _sheet()
        doc["signed_by"] = "mark"
        self._refuses(doc, "unknown key `signed_by`")
        doc.pop("signed_by")
        SS.read_sheet(self.write_sheet(doc))

    def test_missing_top_level_key_refuses(self):
        doc = _sheet()
        doc.pop("scaffold_provenance")
        self._refuses(doc, "missing the key `scaffold_provenance`")
        doc["scaffold_provenance"] = _prov()
        SS.read_sheet(self.write_sheet(doc))

    def test_batch_id_outside_the_grammar_refuses(self):
        self._refuses(_sheet(batch_id="SVY-1"), "is not SVY-NNNN",
                      name="survey-SVY-0001.yml")
        SS.read_sheet(self.write_sheet(_sheet(batch_id="SVY-0001")))

    def test_filename_disagreeing_with_batch_id_refuses(self):
        self._refuses(_sheet(batch_id="SVY-0002"), "must name one batch",
                      name="survey-SVY-0001.yml")
        # Control: the SAME batch_id under its own filename reads clean.
        SS.read_sheet(self.write_sheet(_sheet(batch_id="SVY-0002"),
                                       name="survey-SVY-0002.yml"))

    def test_empty_rows_list_refuses(self):
        self._refuses(_sheet(rows=[]), "non-empty list")
        SS.read_sheet(self.write_sheet(_sheet(rows=[_row()])))

    def test_unknown_row_key_refuses(self):
        bad = _row()
        bad["verdit"] = "ratify"
        self._refuses(_sheet(rows=[bad]), "unknown key `verdit`")
        bad.pop("verdit")
        SS.read_sheet(self.write_sheet(_sheet(rows=[bad])))

    def test_malformed_anchor_id_refuses(self):
        self._refuses(_sheet(rows=[_row(anchor="NOTHEX")]),
                      "16 lowercase hex digits")
        SS.read_sheet(self.write_sheet(_sheet(rows=[_row(anchor=A1)])))

    def test_duplicate_anchor_in_one_batch_refuses(self):
        self._refuses(_sheet(rows=[_row(anchor=A1), _row(anchor=A1)]),
                      "at most once")
        # Control: two DIFFERENT anchors in one batch read clean.
        SS.read_sheet(self.write_sheet(_sheet(rows=[_row(anchor=A1),
                                                    _row(anchor=A2)])))

    def test_non_string_cell_refuses(self):
        bad = _row()
        bad["verdict"] = 1
        self._refuses(_sheet(rows=[bad]), "not a string")
        bad["verdict"] = "ratify"
        SS.read_sheet(self.write_sheet(_sheet(rows=[bad])))

    def test_absent_sheet_refuses(self):
        with self.assertRaises(SS.SurveySheetError) as ctx:
            SS.read_sheet(self.obs / "survey-SVY-0009.yml")
        self.assertIn("does not exist", str(ctx.exception))
        SS.read_sheet(self.write_sheet(_sheet()))

    def test_symlinked_sheet_refuses(self):
        real = self.write_sheet(_sheet())
        link = self.obs / "survey-SVY-0002.yml"
        link.symlink_to(real)
        with self.assertRaises(SS.SurveySheetError) as ctx:
            SS.read_sheet(link)
        self.assertIn("is a symlink", str(ctx.exception))
        # Control: the same bytes at a real path read clean.
        SS.read_sheet(real)

    def test_duplicate_yaml_key_refuses_through_the_strict_loader(self):
        path = self.obs / "survey-SVY-0001.yml"
        path.write_text(
            'config_version: "1"\nconfig_version: "1"\nbatch_id: "SVY-0001"\n',
            encoding="utf-8")
        with self.assertRaises(SS.SurveySheetError) as ctx:
            SS.read_sheet(path)
        self.assertIn("not readable", str(ctx.exception))
        SS.read_sheet(self.write_sheet(_sheet()))


# ── signed_rows: the four human-cell refusals ──────────────────────────────

class SignedRowRefusalTests(_TreeCase):

    def _refuses(self, row, needle):
        with self.assertRaises(SS.SurveySheetError) as ctx:
            SS.signed_rows(_sheet(rows=[row]))
        self.assertIn(needle, str(ctx.exception))

    def test_control_a_signed_row_resolves_to_the_seed(self):
        [out] = SS.signed_rows(_sheet(rows=[_row(proposed="storage", domain="")]))
        self.assertEqual(out["domain"], "storage")
        self.assertEqual(out["domain_source"], "proposed")

    def test_control_an_override_is_recorded_as_overridden(self):
        [out] = SS.signed_rows(_sheet(rows=[_row(proposed="storage",
                                                 domain="runtime")]))
        self.assertEqual(out["domain"], "runtime")
        self.assertEqual(out["domain_source"], "overridden")

    def test_empty_verdict_refuses(self):
        self._refuses(_row(verdict=""), "carries no verdict")
        SS.signed_rows(_sheet(rows=[_row(verdict="ratify")]))

    def test_verdict_outside_the_enum_refuses(self):
        self._refuses(_row(verdict="retire"), "is outside")
        SS.signed_rows(_sheet(rows=[_row(verdict="reject")]))

    def test_whitespace_only_domain_refuses(self):
        self._refuses(_row(domain="   "), "whitespace only")
        # Control: the EMPTY cell (not whitespace) accepts the seed.
        [out] = SS.signed_rows(_sheet(rows=[_row(domain="")]))
        self.assertEqual(out["domain_source"], "proposed")

    def test_multi_line_domain_refuses(self):
        self._refuses(_row(domain="storage\nruntime"), "more than one")
        SS.signed_rows(_sheet(rows=[_row(domain="storage")]))

    def test_domain_empty_after_the_seed_is_applied_refuses(self):
        self._refuses(_row(proposed="", domain=""), "after the seed is applied")
        # Control: the same unseeded row with a human domain signs clean.
        [out] = SS.signed_rows(_sheet(rows=[_row(proposed="", domain="storage")]))
        self.assertEqual(out["domain"], "storage")

    def test_defer_and_reject_still_need_a_domain(self):
        """A verdict is not a licence to skip the domain: the receipt records
        the resolved domain for every row, seeded or overridden."""
        self._refuses(_row(verdict="defer", proposed="", domain=""),
                      "after the seed is applied")
        SS.signed_rows(_sheet(rows=[_row(verdict="defer", proposed="storage")]))


# ── the digest ─────────────────────────────────────────────────────────────

class SheetDigestTests(_TreeCase):

    def test_digest_is_64_lowercase_hex(self):
        self.assertRegex(SS.sheet_digest(_sheet()), r"^[0-9a-f]{64}$")

    def test_row_order_does_not_move_the_digest(self):
        a = _sheet(rows=[_row(anchor=A1), _row(anchor=A2)])
        b = _sheet(rows=[_row(anchor=A2), _row(anchor=A1)])
        self.assertEqual(SS.sheet_digest(a), SS.sheet_digest(b))

    def test_whitespace_variance_does_not_move_the_digest(self):
        a = _sheet(rows=[_row(rationale="one  two")])
        b = _sheet(rows=[_row(rationale=" one \t two ")])
        self.assertEqual(SS.sheet_digest(a), SS.sheet_digest(b))

    def test_a_changed_verdict_moves_the_digest(self):
        a = _sheet(rows=[_row(verdict="reject")])
        b = _sheet(rows=[_row(verdict="ratify")])
        self.assertNotEqual(SS.sheet_digest(a), SS.sheet_digest(b))

    def test_a_swapped_anchor_moves_the_digest(self):
        a = _sheet(rows=[_row(anchor=A1)])
        b = _sheet(rows=[_row(anchor=A2)])
        self.assertNotEqual(SS.sheet_digest(a), SS.sheet_digest(b))

    def test_a_changed_domain_moves_the_digest(self):
        self.assertNotEqual(SS.sheet_digest(_sheet(rows=[_row(domain="a")])),
                            SS.sheet_digest(_sheet(rows=[_row(domain="b")])))

    def test_a_changed_batch_id_moves_the_digest(self):
        self.assertNotEqual(SS.sheet_digest(_sheet(batch_id="SVY-0001")),
                            SS.sheet_digest(_sheet(batch_id="SVY-0002")))

    def test_scaffold_provenance_is_inside_the_digest(self):
        self.assertNotEqual(
            SS.sheet_digest(_sheet()),
            SS.sheet_digest(_sheet(scaffold_provenance=_prov(candidates=2))))

    def test_the_version_marker_selects_the_preimage_rather_than_being_hashed(self):
        """The marker is not hashed as a VALUE, and it is not inert either.

        No line of the canonical string carries `config_version`. What the
        marker does is pick the field list and the per-cell fold, so
        relabelling a document to another version hashes a different
        preimage. A test that demanded an unchanged digest across a relabel
        would be demanding that the two schemas share one preimage, which is
        the opposite of the migration."""
        canonical = SS.canonical_sheet_string(_sheet())
        self.assertNotIn("config_version", canonical)
        relabelled = _sheet(config_version="2")
        self.assertNotEqual(SS.sheet_digest(_sheet()),
                            SS.sheet_digest(relabelled))


# ── the v1 digest, frozen ──────────────────────────────────────────────────

# A `config_version: "1"` review sheet, verbatim, and the digest the v1
# preimage produces for it.
#
# ***FROZEN VECTOR. NEVER EDIT EITHER LITERAL TO MAKE A TEST PASS.*** The hex
# was computed once from the v1 path and pasted here as a literal. Every
# receipt archived under `_surveys/` in every downstream tree records the
# digest its own sheet hashed to, and CHK-OBS-SURVEY-DIGEST recomputes that
# digest on every audit. So a failure here is never a stale expectation. It
# says the v1 preimage moved, and therefore that every archived receipt in
# every tree just became unverifiable. The remedy is to restore the v1 path;
# repasting the hex converts a caught break into a shipped one.
#
# The document pins every v1 rule at once: two rows in NON-anchor order (the
# sort), a `rationale` carrying a doubled space, a tab and a trailing space
# (`space_fold`), and a NO-BREAK SPACE in `domain` — the one cell where the
# two folds disagree, because `space_fold` folds every `Zs` and `cell_canon`
# does not.
V1_FROZEN_SHEET = {
    "config_version": "1",
    "batch_id": "SVY-0007",
    "scaffold_provenance": {
        "tool": "scaffold-survey-sheet.py",
        "scaffolded": "2026-08-30",
        "state_file": "arch/_recovered/state.yml",
        "candidates": 2,
    },
    "rows": [
        {"anchor_id": "fedcba9876543210", "proposed_domain": "runtime",
         "verdict": "reject", "domain": "runtime",
         "rationale": "The cited lines do not support the rule."},
        {"anchor_id": "0123456789abcdef", "proposed_domain": "storage",
         "verdict": "ratify", "domain": "run\u00a0time",
         "rationale": "Reviewed  against\tthe cited lines. "},
    ],
}
V1_FROZEN_DIGEST = \
    "bc522463deca3c95a5e6f1a5a2932c73ddf7473cde5385ed5d82283105d2d583"

_FROZEN_BREAK = (
    "the v1 preimage moved: every receipt archived under _surveys/ in every "
    "downstream tree now recomputes to a digest its sheet does not produce, "
    "and CHK-OBS-SURVEY-DIGEST reports BROKEN on a batch nobody touched. "
    "Restore the v1 path. Do not repaste this hex.")


class V1FrozenDigestVectorTests(_TreeCase):
    """The migration's central safety claim, pinned to a real value.

    Widening the row schema re-digests every sheet unless the preimage is
    versioned, and the rest of this suite asserts only the SHAPE of the v1
    preimage — five cells per row, no seeded cell. Shape is not the claim.
    The claim is that one specific archived document still hashes to one
    specific recorded value, and only a frozen vector says that."""

    def test_the_v1_digest_is_byte_stable_across_the_migration(self):
        self.assertEqual(SS.sheet_digest(V1_FROZEN_SHEET), V1_FROZEN_DIGEST,
                         _FROZEN_BREAK)

    def test_the_frozen_sheet_is_a_document_the_reader_still_admits(self):
        """The vector is a live v1 sheet, not a museum piece. A v1 batch in
        flight resumes, so the reader has to keep accepting it."""
        path = self.write_sheet(V1_FROZEN_SHEET)
        doc = SS.read_sheet(path)
        self.assertEqual(doc["config_version"], "1")
        self.assertEqual(SS.sheet_digest(doc), V1_FROZEN_DIGEST,
                         _FROZEN_BREAK)

    def test_the_v1_fold_still_folds_a_no_break_space(self):
        """Why the vector carries a `Zs`. v1 hashes through `space_fold`,
        which folds it; v2 hashes through `cell_canon`, which keeps it. So a
        v1 sheet routed through the v2 fold moves this digest, and the
        assertion above is what notices."""
        plain = {**V1_FROZEN_SHEET,
                 "rows": [dict(r) for r in V1_FROZEN_SHEET["rows"]]}
        plain["rows"][1]["domain"] = "run time"
        self.assertEqual(SS.sheet_digest(plain), V1_FROZEN_DIGEST,
                         _FROZEN_BREAK)
        # The control: the same two spellings are two strings under v2.
        keyed_v2 = {**V1_FROZEN_SHEET, "config_version": "2"}
        plain_v2 = {**plain, "config_version": "2"}
        self.assertNotEqual(SS.sheet_digest(keyed_v2),
                            SS.sheet_digest(plain_v2))


# ── receipt IO ─────────────────────────────────────────────────────────────

def _receipt(rows=None, **over):
    doc = {
        "config_version": "1", "batch_id": "SVY-0001",
        "sheet_ref": "_surveys/SVY-0001/sheet.yml",
        "digest": "a" * 64, "scaffold_provenance": _prov(),
        "signed": "2026-08-30", "completed": None, "records": [],
        "rows": rows if rows is not None else [
            {"anchor_id": A1, "candidate_id": A1, "verdict": "ratify",
             "domain": "storage", "domain_source": "proposed",
             "record_id": "", "record_path": "", "retires": ""}],
    }
    doc.update(over)
    return doc


class ReceiptTests(_TreeCase):

    def test_control_a_receipt_round_trips(self):
        path = self.write_receipt(_receipt())
        back = SS.read_receipt(path)
        self.assertIsNone(back["completed"])
        self.assertEqual(back["rows"][0]["anchor_id"], A1)

    def test_completed_receipt_round_trips_with_records(self):
        path = self.write_receipt(_receipt(completed="2026-08-30",
                                           records=["OBS-0001"]))
        back = SS.read_receipt(path)
        self.assertEqual(back["completed"], "2026-08-30")
        self.assertEqual(back["records"], ["OBS-0001"])

    def test_bad_digest_shape_refuses(self):
        path = self.write_receipt_raw(_receipt(digest="short"))
        with self.assertRaises(SS.SurveySheetError) as ctx:
            SS.read_receipt(path)
        self.assertIn("64 lowercase hex", str(ctx.exception))
        SS.read_receipt(self.write_receipt(_receipt(digest="a" * 64)))

    def test_bad_domain_source_refuses(self):
        rows = _receipt()["rows"]
        rows[0]["domain_source"] = "guessed"
        path = self.write_receipt_raw(_receipt(rows=rows))
        with self.assertRaises(SS.SurveySheetError) as ctx:
            SS.read_receipt(path)
        self.assertIn("domain_source", str(ctx.exception))
        rows[0]["domain_source"] = "proposed"
        SS.read_receipt(self.write_receipt(_receipt(rows=rows)))

    def test_bad_signed_date_refuses(self):
        path = self.write_receipt_raw(_receipt(signed="yesterday"))
        with self.assertRaises(SS.SurveySheetError) as ctx:
            SS.read_receipt(path)
        self.assertIn("YYYY-MM-DD", str(ctx.exception))
        SS.read_receipt(self.write_receipt(_receipt(signed="2026-08-30")))

    def test_write_receipt_refuses_what_read_receipt_would_refuse(self):
        """`write_receipt`'s docstring claimed the receipt was validated
        before it was written; nothing validated it. Cell 1 writes the receipt
        and deletes the live sheet, so a receipt the reader refuses left the
        batch in a state only a human could leave — the very next
        `batch_state` raises on the file the sign-off had just written."""
        rows = _receipt()["rows"]
        rows[0]["retires"] = "/etc/ssh/ssh_host_rsa_key"
        with self.assertRaises(SS.SurveySheetError) as ctx:
            self.write_receipt(_receipt(rows=rows))
        self.assertIn("retires", str(ctx.exception))
        self.assertFalse(
            SS.receipt_paths(self.obs, "SVY-0001")["receipt"].exists(),
            "the refused receipt was written anyway")
        # Control: the same receipt with that one cell valid writes and reads.
        rows[0]["retires"] = ""
        back = SS.read_receipt(self.write_receipt(_receipt(rows=rows)))
        self.assertEqual(back["rows"][0]["retires"], "")

    def test_write_receipt_refuses_a_path_outside_the_tree(self):
        outside = self.root / "elsewhere"
        outside.mkdir()
        with self.assertRaises(SS.SurveySheetError) as ctx:
            SS.write_receipt(outside / "receipt.yml", _receipt(),
                             contained_under=self.tree)
        self.assertIn("not contained under", str(ctx.exception))
        # Control: inside the tree, the same payload writes.
        self.write_receipt(_receipt())

    def test_new_receipt_records_no_completion_and_no_records(self):
        sheet = _sheet(rows=[_row(anchor=A2), _row(anchor=A1)])
        # `new_receipt` takes PLAN rows, which carry `slug_source` beside
        # `domain_source`; `signed_rows` alone cannot know the slug's source
        # (it needs the candidate), so the plan's answer is supplied here.
        rows = [dict(r, slug_source="proposed") for r in SS.signed_rows(sheet)]
        r = SS.new_receipt(sheet, rows, signed="2026-08-30",
                           sheet_ref="_surveys/SVY-0001/sheet.yml")
        self.assertEqual(r["config_version"], SS.CONFIG_VERSION)
        self.assertEqual([row["slug_source"] for row in r["rows"]],
                         ["proposed", "proposed"])
        self.assertIsNone(r["completed"])
        self.assertEqual(r["records"], [])
        self.assertEqual([row["anchor_id"] for row in r["rows"]], sorted([A1, A2]))
        self.assertEqual(r["digest"], SS.sheet_digest(sheet))


class ReceiptRowFieldValidationTests(_TreeCase):
    """Every key in `RECEIPT_ROW_KEYS` has a declared shape, and `read_receipt`
    must enforce all eight.

    Written from the SCHEMA rather than from the values the writer happens to
    produce. Every other receipt test in this file builds its fixture with
    `SS.write_receipt`, so no test ever supplied a field the reader does not
    validate — the reader checked three of eight and the suite was blind to the
    other five by construction. The receipt is TRUSTED INPUT on every read:
    `batch_state`, the audit rules and both projections all parse it, and its
    fields reach a file copy (`retires`), a filename (`record_path`), a log
    entry (`record_id`) and record frontmatter (`domain`).

    The receipts here are written as RAW YAML, bypassing `write_receipt`. That
    is the threat model: the file on disk is what `read_receipt` is asked to
    trust, and a fixture that can only express what the writer emits cannot
    express a tampered one."""

    # One entry per key in `RECEIPT_ROW_KEYS`; the roster test below fails if a
    # key is ever added without a case.
    MALFORMED = {
        "anchor_id": ["", "not-hex", "0123456789ABCDEF", "0123456789abcde",
                      "0123456789abcdef0"],
        "candidate_id": ["", "zzzzzzzzzzzzzzzz", A1 + "+", A1 + "+x",
                         "../../etc/passwd", A2],
        "verdict": ["", "approve", "RATIFY", "ratify "],
        "domain": ["", "   ", "storage\nrm -rf /", "storage\x00hidden"],
        "domain_source": ["", "guessed", "PROPOSED"],
        "slug_source": ["", "human", "guessed", "PROPOSED"],
        "record_id": ["OBS-1", "obs-0001", "../OBS-0001", "OBS-0001-slug",
                      "OBS-0001\n\n## [2020-01-01] adr | fabricated"],
        "record_path": ["/etc/passwd", "../../../etc/passwd",
                        "observations/../../etc/passwd", "~/OBS-0001-x.md",
                        "observations/id_rsa", "a/b/OBS-0001-x.md",
                        "OBS-0001-x.md", "observations/OBS-0001-x.md\nmore"],
        "retires": ["/etc/ssh/ssh_host_rsa_key", "../../../.ssh/id_rsa",
                    "observations/../../.ssh/id_rsa", "~/.ssh/id_rsa",
                    "observations/id_rsa", "observations/OBS-1-x.md"],
    }

    # The near-miss control for each key: the same receipt with this one value
    # in place must read back cleanly.
    WELL_FORMED = {
        "anchor_id": A1,
        "candidate_id": A1,
        "verdict": "ratify",
        "domain": "storage",
        "domain_source": "proposed",
        "slug_source": "overridden",
        "record_id": "OBS-0001",
        "record_path": "observations/OBS-0001-a-slug.md",
        "retires": "observations/OBS-0002-another-slug.md",
    }

    def _row_with(self, **over):
        row = dict(_receipt()["rows"][0])
        row.update(over)
        return row

    def test_every_receipt_row_key_carries_a_declared_shape(self):
        """The roster covers the UNION of every version's row key set, so a
        key added to one version cannot land without a malformed and a
        well-formed shape — including v3's `slug_source`."""
        every_key = set()
        for version in SS.SUPPORTED_CONFIG_VERSIONS:
            every_key.update(SS.receipt_row_keys(version))
        self.assertEqual(sorted(self.MALFORMED), sorted(every_key))
        self.assertEqual(sorted(self.WELL_FORMED), sorted(every_key))
        self.assertIn("slug_source", every_key)

    def test_the_v3_slug_source_refuses_malformed_and_reads_well_formed(self):
        """`slug_source` lives only on a v3 row, so its shapes are driven on
        a v3 receipt: the fixture `_receipt()` is v1 and cannot carry it."""
        def v3(row):
            return _receipt(config_version="3",
                            scaffold_provenance=_prov_v2(), rows=[row])
        base = dict(self._row_with(), slug_source=self.WELL_FORMED["slug_source"])
        for bad in self.MALFORMED["slug_source"]:
            with self.subTest(value=bad):
                path = self.write_receipt_raw(v3(dict(base, slug_source=bad)))
                with self.assertRaises(SS.SurveySheetError) as ctx:
                    SS.read_receipt(path)
                self.assertIn("slug_source", str(ctx.exception))
        back = SS.read_receipt(self.write_receipt_raw(v3(base)))
        self.assertEqual(back["rows"][0]["slug_source"],
                         self.WELL_FORMED["slug_source"])

    def test_a_malformed_value_refuses_for_every_receipt_row_key(self):
        """Driven on the v1 fixture `_receipt()`, so over the v1 key set;
        `slug_source` has its own v3 case below."""
        for key in sorted(SS.receipt_row_keys("1")):
            for bad in self.MALFORMED[key]:
                with self.subTest(key=key, value=bad):
                    path = self.write_receipt_raw(
                        _receipt(rows=[self._row_with(**{key: bad})]))
                    with self.assertRaises(SS.SurveySheetError) as ctx:
                        SS.read_receipt(path)
                    self.assertIn(key, str(ctx.exception))
            # The near-miss control: this key made valid, everything else
            # untouched, reads back.
            with self.subTest(key=key, value="<well-formed>"):
                path = self.write_receipt_raw(
                    _receipt(rows=[self._row_with(
                        **{key: self.WELL_FORMED[key]})]))
                back = SS.read_receipt(path)
                self.assertEqual(back["rows"][0][key], self.WELL_FORMED[key])

    def test_a_candidate_id_off_its_own_anchor_refuses(self):
        """A row's candidate must sit on the row's anchor: `_cell_14_dispose`
        joins the state file on `candidate_id` and `batch_state` reads it back,
        so a candidate pointing at another anchor disposes a row this batch
        never reviewed."""
        path = self.write_receipt_raw(_receipt(rows=[self._row_with(
            anchor_id=A1, candidate_id=A2)]))
        with self.assertRaises(SS.SurveySheetError) as ctx:
            SS.read_receipt(path)
        self.assertIn("candidate_id", str(ctx.exception))
        # Control: the successor key on the SAME anchor is legal, and is the
        # one shape that is not equal to its anchor.
        back = SS.read_receipt(self.write_receipt_raw(_receipt(rows=[
            self._row_with(anchor_id=A1, candidate_id=A1 + "+1")])))
        self.assertEqual(back["rows"][0]["candidate_id"], A1 + "+1")

    def test_a_non_ratify_row_may_not_carry_an_allocation(self):
        """`reject` and `defer` write no record, so `new_receipt` and
        `_cell_3_allocate` leave all three allocation cells empty on them. A
        receipt that carries one anyway plans a staged file and a promotion for
        a row that reviewed nothing."""
        for verdict in ("reject", "defer"):
            for key in ("record_id", "record_path", "retires"):
                with self.subTest(verdict=verdict, key=key):
                    path = self.write_receipt_raw(_receipt(rows=[self._row_with(
                        verdict=verdict, **{key: self.WELL_FORMED[key]})]))
                    with self.assertRaises(SS.SurveySheetError) as ctx:
                        SS.read_receipt(path)
                    self.assertIn(key, str(ctx.exception))
        # Control: the same rows with the cells empty read back.
        back = SS.read_receipt(self.write_receipt_raw(_receipt(rows=[
            self._row_with(verdict="defer")])))
        self.assertEqual(back["rows"][0]["verdict"], "defer")

    def test_a_record_path_stem_must_name_the_row_s_record_id(self):
        path = self.write_receipt_raw(_receipt(rows=[self._row_with(
            record_id="OBS-0001",
            record_path="observations/OBS-0009-a-slug.md")]))
        with self.assertRaises(SS.SurveySheetError) as ctx:
            SS.read_receipt(path)
        self.assertIn("record_path", str(ctx.exception))
        back = SS.read_receipt(self.write_receipt_raw(_receipt(rows=[self._row_with(
            record_id="OBS-0001",
            record_path="observations/OBS-0001-a-slug.md")])))
        self.assertEqual(back["rows"][0]["record_id"], "OBS-0001")


class RecordIndexContainmentTests(_TreeCase):
    """[SECURITY] `record_paths` reads every `OBS-*.md` under the concern and
    hands the map to `build_plan`, which turns a predecessor's FILE into the
    `retires` cell the sign-off later copies. `glob` returns a symlink, and
    `read_text` follows it — so a link planted in the concern directory put a
    file from outside the repository into the retirement lane.

    `_assert_contained` never saw this: it only ever guards a WRITE target,
    which is contained by construction."""

    def _record(self, name, body):
        (self.obs / name).write_text(body, encoding="utf-8")

    def test_a_symlinked_record_refuses_rather_than_being_read_through(self):
        secret = self.root / "outside" / "id_rsa"
        secret.parent.mkdir()
        secret.write_text("---\nid: OBS-0001\n---\nPRIVATE KEY\n",
                          encoding="utf-8")
        (self.obs / "OBS-0001-x.md").symlink_to(secret)
        with self.assertRaises(SS.SurveySheetError) as ctx:
            SS.record_paths(self.obs)
        self.assertIn("OBS-0001-x.md", str(ctx.exception))
        # Control: the identical bytes as a REAL file in the concern are read.
        (self.obs / "OBS-0001-x.md").unlink()
        self._record("OBS-0001-x.md", "---\nid: OBS-0001\n---\nbody\n")
        self.assertEqual(sorted(SS.record_paths(self.obs)), ["OBS-0001"])


# ── disposition ────────────────────────────────────────────────────────────

class DisposedCandidateTests(_TreeCase):

    def _receipt_with(self, verdict, anchor=A1, batch="SVY-0001"):
        rows = [{"anchor_id": anchor, "candidate_id": anchor,
                 "verdict": verdict, "domain": "storage",
                 "domain_source": "proposed", "record_id": "",
                 "record_path": "", "retires": ""}]
        self.write_receipt(_receipt(rows=rows, batch_id=batch), batch_id=batch)

    def test_ratify_and_reject_dispose(self):
        self._receipt_with("ratify", anchor=A1, batch="SVY-0001")
        self._receipt_with("reject", anchor=A2, batch="SVY-0002")
        self.assertEqual(SS.disposed_candidates(self.obs),
                         {A1: "SVY-0001", A2: "SVY-0002"})

    def test_defer_does_not_dispose_but_is_still_covered(self):
        """`defer` is the only re-scaffoldable verdict (§17.5), so it must be
        absent from the disposed map — and present in the covered set, which is
        what CHK-OBS-SURVEY-RECORD reads."""
        self._receipt_with("defer", anchor=A1)
        self.assertEqual(SS.disposed_candidates(self.obs), {})
        self.assertEqual(SS.receipt_covered_anchors(self.obs), {A1})

    def test_no_surveys_directory_is_an_empty_map(self):
        self.assertEqual(SS.disposed_candidates(self.obs), {})


# ── the plan (build_plan's refusals) ───────────────────────────────────────

class BuildPlanTests(_TreeCase):

    def setUp(self):
        super().setUp()
        self.anchor = SS.__dict__  # placeholder, replaced below
        from crux.arch.recover import candidate_id
        self.aid = candidate_id("external-dependency",
                                canonical_anchor("external-dependency",
                                                 name="httpx"))
        self.rule = "The gateway depends on httpx for every outbound call"
        self.evidence = ["src/a.py:1-1"]
        self.state = StateFile(self.tree / "arch" / "_recovered" / "state.yml")
        self.state.upsert_observed({
            "id": self.aid, "anchor_kind": "external-dependency",
            "canonical_anchor": "httpx", "rule": self.rule,
            "evidence": list(self.evidence), "domain": "runtime"})

    def _sheet_for(self, **over):
        return _sheet(rows=[_row(anchor=self.aid, proposed="runtime", **over)])

    def _plan(self, sheet=None, recorded=None):
        return SS.build_plan(sheet or self._sheet_for(), self.state.rows,
                             recorded or {}, root=self.root, obs_dir=self.obs)

    def _record(self, oid="OBS-0001", anchor=None, status="ratified"):
        """A real record file. `retires` carries the predecessor's PATH, so a
        successor's predecessor must exist on disk, not only in a map."""
        (self.obs / f"{oid}-x.md").write_text(
            f'---\nid: {oid}\nstatus: {status}\n'
            f'anchor_id: "{anchor or self.aid}"\n'
            'evidence: ["src/a.py:1-1"]\ngoverns: []\n---\n\nbody\n',
            encoding="utf-8")
        return f"observations/{oid}-x.md"

    def test_control_a_ratify_row_plans_a_record(self):
        [entry] = self._plan()
        self.assertEqual(entry["verdict"], "ratify")
        self.assertEqual(entry["domain"], "runtime")
        self.assertEqual(entry["retires"], "")
        self.assertTrue(entry["slug"])
        self.assertEqual(entry["evidence"], ["src/a.py:1-1"])

    def test_anchor_with_no_candidate_refuses(self):
        sheet = _sheet(rows=[_row(anchor=A2, proposed="runtime")])
        with self.assertRaises(SS.SurveySheetError) as ctx:
            self._plan(sheet)
        self.assertIn("no candidate", str(ctx.exception))
        self._plan()          # control: the anchor that DOES have a candidate

    def test_anchor_kind_outside_the_closed_set_refuses(self):
        self.state.rows[self.aid]["anchor_kind"] = "vibes"
        with self.assertRaises(SS.SurveySheetError) as ctx:
            self._plan()
        self.assertIn("outside", str(ctx.exception))
        self.state.rows[self.aid]["anchor_kind"] = ANCHOR_KINDS[0]
        self._plan()

    def test_candidate_with_no_evidence_refuses(self):
        self.state.rows[self.aid]["evidence"] = []
        with self.assertRaises(SS.SurveySheetError) as ctx:
            self._plan()
        self.assertIn("no evidence", str(ctx.exception))
        self.state.rows[self.aid]["evidence"] = list(self.evidence)
        self._plan()

    def test_unresolvable_evidence_path_refuses(self):
        self.state.rows[self.aid]["evidence"] = ["src/gone.py:1-1"]
        with self.assertRaises(SS.SurveySheetError) as ctx:
            self._plan()
        self.assertIn("does not resolve", str(ctx.exception))
        self.state.rows[self.aid]["evidence"] = list(self.evidence)
        self._plan()

    def test_evidence_escaping_through_a_symlink_refuses(self):
        (self.root / "link").symlink_to("/etc")
        self.state.rows[self.aid]["evidence"] = ["link/hosts:1-1"]
        with self.assertRaises(SS.SurveySheetError) as ctx:
            self._plan()
        self.assertIn("does not resolve", str(ctx.exception))
        self.state.rows[self.aid]["evidence"] = list(self.evidence)
        self._plan()

    def test_a_symlinked_evidence_path_refuses_the_whole_batch(self):
        """Absence assertion 11: one bad row publishes NOTHING — and its
        control, removing that row, plans the rest."""
        second = "aaaaaaaaaaaaaaaa"
        self.state.upsert_observed({
            "id": second, "anchor_kind": "external-dependency",
            "canonical_anchor": "yaml", "rule": "The loader pins pyyaml",
            "evidence": ["src/a.py:1-1"], "domain": "runtime"})
        (self.root / "link").symlink_to("/etc")
        self.state.rows[self.aid]["evidence"] = ["link/hosts:1-1"]
        both = _sheet(rows=[_row(anchor=self.aid, proposed="runtime"),
                            _row(anchor=second, proposed="runtime")])
        with self.assertRaises(SS.SurveySheetError):
            self._plan(both)
        only_good = _sheet(rows=[_row(anchor=second, proposed="runtime")])
        self.assertEqual(len(self._plan(only_good)), 1)

    def test_a_ratify_on_an_occupied_live_anchor_refuses(self):
        recorded = {self.aid: {"id": "OBS-0001", "status": "ratified",
                               "rule": self.rule, "evidence": self.evidence}}
        with self.assertRaises(SS.SurveySheetError) as ctx:
            self._plan(recorded=recorded)
        self.assertIn("second live record", str(ctx.exception))
        # Control: a RETIRED predecessor leaves the anchor free.
        recorded[self.aid]["status"] = "retired"
        self._plan(recorded=recorded)

    def test_successor_with_no_predecessor_refuses(self):
        sid = f"{self.aid}+1"
        self.state.rows[sid] = {
            "id": sid, "anchor_id": self.aid, "state": "observed",
            "anchor_kind": "external-dependency", "canonical_anchor": "httpx",
            "rule": "The gateway now depends on httpx and h2",
            "evidence": list(self.evidence), "domain": "runtime"}
        rel = self._record()
        recorded = {self.aid: {"id": "OBS-0001", "status": "ratified",
                               "rule": "old", "evidence": self.evidence}}
        with self.assertRaises(SS.SurveySheetError) as ctx:
            self._plan(recorded=recorded)
        self.assertIn("naming no predecessor", str(ctx.exception))
        # Control: the same successor NAMING its predecessor plans a retirement.
        self.state.rows[sid]["predecessor_id"] = "OBS-0001"
        [entry] = self._plan(recorded=recorded)
        self.assertEqual(entry["retires"], rel)

    def test_successor_naming_an_unrecorded_predecessor_refuses(self):
        sid = f"{self.aid}+1"
        self.state.rows[sid] = {
            "id": sid, "anchor_id": self.aid, "state": "observed",
            "predecessor_id": "OBS-0404",
            "anchor_kind": "external-dependency", "canonical_anchor": "httpx",
            "rule": "The gateway now depends on httpx and h2",
            "evidence": list(self.evidence), "domain": "runtime"}
        self._record()
        recorded = {self.aid: {"id": "OBS-0001", "status": "ratified",
                               "rule": "old", "evidence": self.evidence}}
        with self.assertRaises(SS.SurveySheetError) as ctx:
            self._plan(recorded=recorded)
        self.assertIn("no recorded observation", str(ctx.exception))
        self.state.rows[sid]["predecessor_id"] = "OBS-0001"
        self._plan(recorded=recorded)

    def test_a_claim_that_moved_since_the_scaffold_refuses(self):
        sid = f"{self.aid}+1"
        moved_rule = "The gateway now depends on httpx and h2"
        self.state.rows[sid] = {
            "id": sid, "anchor_id": self.aid, "state": "observed",
            "predecessor_id": "OBS-0001",
            "claim_digest": claim_digest("something else", self.evidence),
            "anchor_kind": "external-dependency", "canonical_anchor": "httpx",
            "rule": moved_rule, "evidence": list(self.evidence),
            "domain": "runtime"}
        self._record()
        recorded = {self.aid: {"id": "OBS-0001", "status": "ratified",
                               "rule": "old", "evidence": self.evidence}}
        with self.assertRaises(SS.SurveySheetError) as ctx:
            self._plan(recorded=recorded)
        self.assertIn("moved since it was mined", str(ctx.exception))
        # Control: the recorded digest matching the live claim plans clean.
        self.state.rows[sid]["claim_digest"] = claim_digest(moved_rule,
                                                            self.evidence)
        self._plan(recorded=recorded)

    def test_an_anchor_another_batch_disposed_refuses(self):
        with self.assertRaises(SS.SurveySheetError) as ctx:
            SS.build_plan(self._sheet_for(), self.state.rows, {},
                          root=self.root, obs_dir=self.obs,
                          disposed_elsewhere={self.aid: "SVY-0002"})
        self.assertIn("already disposed by batch", str(ctx.exception))
        # Control: disposed by THIS batch (a resume) is not a refusal.
        SS.build_plan(self._sheet_for(), self.state.rows, {},
                      root=self.root, obs_dir=self.obs,
                      disposed_elsewhere={self.aid: "SVY-0001"})

    def test_a_reject_row_needs_no_resolvable_evidence(self):
        """Refusing a batch because a candidate the human is REJECTING has
        unresolvable evidence would make a bad candidate unrejectable."""
        self.state.rows[self.aid]["evidence"] = ["src/gone.py:1-1"]
        [entry] = self._plan(_sheet(rows=[_row(anchor=self.aid,
                                               proposed="runtime",
                                               verdict="reject")]))
        self.assertEqual(entry["verdict"], "reject")
        self.assertNotIn("slug", entry)

    def test_a_rule_that_slugs_to_nothing_refuses(self):
        self.state.rows[self.aid]["rule"] = "。。。"
        with self.assertRaises(SS.SurveySheetError) as ctx:
            self._plan()
        # A v1 row carries no slug cell, so the title-derived slug is the one
        # refused — and the message says which of the two it was.
        self.assertIn("title-derived slug is empty", str(ctx.exception))
        self.state.rows[self.aid]["rule"] = self.rule
        self._plan()


class SlugTests(unittest.TestCase):

    def test_slug_is_kebab_ascii_and_bounded(self):
        slug = SS.slugify(SS.derive_title("The gateway retries a failed "
                                          "provider seat exactly once, always"))
        self.assertRegex(slug, r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
        self.assertLessEqual(len(slug), 60)

    def test_non_ascii_only_rule_slugs_to_empty(self):
        self.assertEqual(SS.slugify("。。。"), "")


class AnchorKindsTests(unittest.TestCase):

    def test_canonical_anchor_refuses_a_kind_outside_the_tuple(self):
        with self.assertRaises(ValueError):
            canonical_anchor("vibes", name="x")
        # Control: every kind IN the tuple is constructible.
        parts = {"external-dependency": {"name": "x"},
                 "module-boundary": {"src": "a", "dst": "b"},
                 "api-contract": {"module": "m", "symbol": "S"},
                 "system-of-record": {"file": "f", "key": "k"},
                 "cross-cutting-policy": {"file": "f", "key": "k"}}
        for kind in ANCHOR_KINDS:
            self.assertTrue(canonical_anchor(kind, **parts[kind]))


if __name__ == "__main__":
    unittest.main()


class HoldingAreaContainmentTests(_TreeCase):
    """[SECURITY:S4/S5] `receipt_paths` is "the one place the `_surveys/`
    layout is spelled", so it is the one place the layout's containment can be
    decided for the survey lane.

    `_load_yaml_doc` guards the LEAF — a sheet or receipt that is itself a
    symlink — and that was the only leg. A symlink at `<obs>/_surveys`, or at
    one batch directory under it, leaves every entry below it a real file, so
    the leaf check passes and an outside file is read; `validate_receipt` then
    quotes its CELL VALUES into the refusal. `_assert_contained` is the
    write-side twin and cannot see it: it guards a write target.
    """

    def _outside_batch(self):
        outside = self.root.parent / f"outside-{self.root.name}"
        (outside / "SVY-0001").mkdir(parents=True, exist_ok=True)
        self.addCleanup(__import__("shutil").rmtree, outside, True)
        return outside

    def test_paths_are_returned_for_a_contained_holding_area(self):
        """The control: the ordinary layout is unaffected, and every returned
        path still sits under the concern."""
        paths = SS.receipt_paths(self.obs, "SVY-0001")
        for key, path in paths.items():
            self.assertTrue(str(path).startswith(str(self.obs)), key)

    def test_a_linked_holding_area_refuses(self):
        (self.obs / "_surveys").symlink_to(self._outside_batch())
        with self.assertRaises(SS.SurveySheetError) as ctx:
            SS.receipt_paths(self.obs, "SVY-0001")
        self.assertIn("does not resolve", str(ctx.exception))

    def test_a_linked_batch_directory_refuses(self):
        """The INTERIOR leg: the holding area is real and the batch is the
        link. A guard on `_surveys` alone would pass this."""
        (self.obs / "_surveys").mkdir()
        (self.obs / "_surveys" / "SVY-0001").symlink_to(
            self._outside_batch() / "SVY-0001")
        with self.assertRaises(SS.SurveySheetError) as ctx:
            SS.receipt_paths(self.obs, "SVY-0001")
        self.assertIn("does not resolve", str(ctx.exception))

    def test_a_batch_id_that_is_not_a_batch_id_cannot_traverse(self):
        """The path-traversal leg on the same function: the batch id reaches a
        path join, so a `..` spelling must not walk out of the concern."""
        with self.assertRaises(SS.SurveySheetError):
            SS.receipt_paths(self.obs, "../../../etc")
