"""What the human's signature actually covers.

Four questions the rest of the survey suite does not answer, each one a way a
batch published something the signature never named:

  * **The claim.** A sheet carried `anchor_id`, `proposed_domain`, `verdict`,
    `domain` and `rationale` — an anchor id and a verdict, and no rule and no
    evidence. The rule was read from the candidate state file at PUBLISH time,
    so a mutation between the signature and the publish landed a claim nobody
    reviewed, at exit 0.
  * **The digest's blind spot.** The digest folded every `str.isspace()` run to
    one space while the publish only stripped, so a `Zs` character — a
    no-break space, an ideographic space — survived to disk under an UNCHANGED
    digest, in `domain`, which is the doctrine's per-domain grouping key.
  * **The index cell.** A `|` in a domain reached `index.md` unescaped, so one
    record's row rendered as ten cells where the header declares seven.
  * **The tree.** Nothing bound a receipt to the tree it was signed against, so
    a batch signed in one repository published into another.

Every refusal test here carries the near-miss control that must NOT refuse:
the same fixture with the one crafted cell made ordinary.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

try:                                          # discovery runs this as a package
    from .test_survey_signoff import (
        DATE, MANIFEST, SCAFFOLD, SIGNOFF, SS, SO, _SignoffCase,
    )
except ImportError:                           # ... and as a bare directory
    from test_survey_signoff import (         # type: ignore[no-redef]
        DATE, MANIFEST, SCAFFOLD, SIGNOFF, SS, SO, _SignoffCase,
    )

from crux.arch.recover import StateFile, candidate_id  # noqa: E402

CHECK = None


def _check_module():
    global CHECK
    if CHECK is None:
        import importlib.util
        scripts = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location(
            "check_observations", scripts / "check_observations.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["check_observations"] = mod
        spec.loader.exec_module(mod)
        CHECK = mod
    return CHECK


class _ClaimCase(_SignoffCase):
    """`_SignoffCase` plus the two fixtures every case below needs: mutating a
    candidate's claim after the signature, and reading the published record."""

    def _mutate_rule(self, cid: str, rule: str) -> None:
        """Re-mine one candidate's rule, on disk, after the sheet was authored.

        Read back from disk first: the sign-off rewrites the state file when it
        disposes a batch, so an in-memory `StateFile` held across a publish
        would write the pre-publish states back."""
        state = StateFile(self.state_path)
        state.rows[cid]["rule"] = rule
        state.save()

    def _mutate_evidence(self, cid: str, evidence: list) -> None:
        state = StateFile(self.state_path)
        state.rows[cid]["evidence"] = list(evidence)
        state.save()

    def _published_rules(self) -> dict[str, str]:
        """`{record filename: the rule the record's governs entry carries}`."""
        return {name: self._frontmatter(name)["governs"][0]["rule"]
                for name in self._records()}

    def _sheet_path(self, bid: str) -> Path:
        return self.obs / f"survey-{bid}.yml"

    def _findings(self, proc) -> str:
        return " ".join(self._payload(proc)["findings"])


# ── the claim the human signed ─────────────────────────────────────────────

class SeededClaimTests(_ClaimCase):
    """The sheet carries the rule and the evidence, and they are BOUND.

    The scaffold seeds both verbatim from the candidate and the digest covers
    them, so the human signs a claim rather than an anchor id. The published
    record's rule and evidence are still copied from the CANDIDATE — the sheet
    is what the human reads, the candidate is what the record carries, and any
    divergence between the two refuses rather than silently preferring one."""

    def test_the_scaffold_seeds_the_rule_and_the_evidence(self):
        bid = self._scaffold()
        sheet = SS.read_sheet(self._sheet_path(bid))
        rows = {r["anchor_id"]: r for r in sheet["rows"]}
        self.assertEqual(rows[self.a1]["rule"],
                         "The gateway depends on httpx")
        self.assertEqual(rows[self.a1]["evidence"], "src/a.py:1-1")
        # Machine cells only — the three human cells stay empty (clause 4).
        self.assertEqual(
            [rows[self.a1][k] for k in ("verdict", "domain", "rationale")],
            ["", "", ""])

    def test_the_seeded_cells_are_inside_the_digest(self):
        bid = self._scaffold()
        sheet = SS.read_sheet(self._sheet_path(bid))
        before = SS.sheet_digest(sheet)
        for cell in ("rule", "evidence"):
            with self.subTest(cell=cell):
                edited = SS.read_sheet(self._sheet_path(bid))
                row = next(r for r in edited["rows"]
                           if r["anchor_id"] == self.a1)
                row[cell] = row[cell] + " and more"
                self.assertNotEqual(SS.sheet_digest(edited), before)

    def test_a_remine_between_the_signature_and_the_publish_refuses(self):
        """The reproduction: scaffold, ratify, mutate the candidate's rule,
        sign off. The rule reached the record from the candidate at publish
        time, so the mutated claim published at exit 0."""
        bid = self._prepare({self.a1: "ratify", self.a2: "defer"})
        self._mutate_rule(self.a1, "The gateway depends on requests")
        proc = self._run("--batch", bid)
        self.assertEqual(
            proc.returncode, 1,
            f"published {self._published_rules()} at exit 0 — the rule on the "
            "sheet the human signed is not the rule the record carries")
        self.assertIn("no longer matches the candidate", self._findings(proc))
        self.assertEqual(self._records(), [])

    def test_a_remined_evidence_list_between_signature_and_publish_refuses(self):
        bid = self._prepare({self.a1: "ratify", self.a2: "defer"})
        self._mutate_evidence(self.a1, ["src/a.py:1-1", "src/b.py:1-1"])
        proc = self._run("--batch", bid)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("no longer matches the candidate", self._findings(proc))
        self.assertEqual(self._records(), [])

    def test_a_remine_mid_flight_refuses(self):
        """The mid-flight variant: the mutation lands AFTER the slug and the
        record id are allocated, so the filename, the `record_path`, the rule
        and the handle all disagree — under a signature that named none of
        them."""
        bid = self._prepare({self.a1: "ratify", self.a2: "defer"})
        ctx = self._ctx(bid)
        SO.advance(ctx)                       # cell 1: bind
        SO.advance(ctx)                       # cell 3: allocate id + slug
        self.assertEqual(self._state(bid), "S2")
        allocated = self._allocated(bid)[self.a1][1]
        self.assertIn("the-gateway-depends-on-httpx", allocated)
        self._mutate_rule(self.a1, "The gateway depends on requests")
        proc = self._run("--batch", bid)
        self.assertEqual(
            proc.returncode, 1,
            f"resumed onto {allocated} carrying {self._published_rules()}")
        self.assertIn("no longer matches the candidate", self._findings(proc))
        self.assertEqual(self._records(), [])

    def test_a_human_edit_to_a_seeded_cell_refuses(self):
        """A seeded cell is machine-written and read-only. Editing one is the
        other half of the same binding: the sheet may not become a second
        source of truth for the claim."""
        for cell, edit in (("rule", "The gateway depends on nothing at all"),
                           ("evidence", "src/b.py:1-1")):
            with self.subTest(cell=cell):
                bid = self._prepare({self.a1: "ratify", self.a2: "defer"})
                sheet = SS.read_sheet(self._sheet_path(bid))
                row = next(r for r in sheet["rows"]
                           if r["anchor_id"] == self.a1)
                row[cell] = edit
                SS.write_sheet(self._sheet_path(bid), sheet,
                               contained_under=self.tree)
                proc = self._run("--batch", bid)
                self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
                self.assertIn("no longer matches the candidate",
                              self._findings(proc))
                self.assertEqual(self._records(), [])
                # Reset for the next subtest: nothing was written, so the tree
                # is reusable, but the live sheet carries the bad edit.
                self._sheet_path(bid).unlink()

    def test_the_control_publishes(self):
        """The near-miss control: the identical batch with the claim left
        alone publishes at exit 0, so the four refusals above are keyed on the
        moved claim and not on the fixture."""
        bid = self._prepare({self.a1: "ratify", self.a2: "defer"})
        proc = self._run("--batch", bid)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(list(self._published_rules().values()),
                         ["The gateway depends on httpx"])

    def test_the_record_carries_the_candidate_rule_not_the_sheet_cell(self):
        """Governs block 3: the record's rule is copied from the CANDIDATE.
        The sheet cell is what the human reads; it is never the source."""
        bid = self._prepare({self.a1: "ratify", self.a2: "defer"})
        sheet = SS.read_sheet(self._sheet_path(bid))
        seeded = next(r["rule"] for r in sheet["rows"]
                      if r["anchor_id"] == self.a1)
        proc = self._run("--batch", bid)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        rule = list(self._published_rules().values())[0]
        self.assertEqual(rule, seeded)
        self.assertEqual(rule, StateFile(self.state_path).rows[self.a1]["rule"])


class SheetMigrationTests(_ClaimCase):
    """Widening the row schema re-digests every sheet, so the preimage is
    VERSIONED: a `config_version: "1"` sheet still digests under the old field
    list, which is what keeps an archived batch's CHK-OBS-SURVEY-DIGEST green
    downstream. A v1 sheet may still be read and digested; it may not be signed
    on any publish path — a first signature and a resume alike.

    What this class asserts is the SHAPE of the v1 preimage. The value is
    pinned elsewhere, by a frozen vector — a hardcoded v1 document and its
    hardcoded digest hex — in `test_survey_sheet.V1FrozenDigestVectorTests`.
    Shape and value are one claim, and the hex is written once."""

    def _v1_sheet(self, bid="SVY-0001"):
        return {"config_version": "1", "batch_id": bid,
                "scaffold_provenance": {
                    "tool": "scaffold-survey-sheet.py", "scaffolded": DATE,
                    "state_file": "arch/_recovered/state.yml",
                    "candidates": 1},
                "rows": [{"anchor_id": self.a1, "proposed_domain": "runtime",
                          "verdict": "ratify", "domain": "",
                          "rationale": "Reviewed."}]}

    def test_a_v1_sheet_still_reads_and_digests_under_the_v1_preimage(self):
        """The migration is in the PREIMAGE, not in a refusal to read: an
        archived v1 sheet must keep digesting to the value its receipt
        recorded, or every batch already published downstream reports
        CHK-OBS-SURVEY-DIGEST BROKEN on upgrade."""
        path = self.obs / "survey-SVY-0001.yml"
        doc = self._v1_sheet()
        SS.write_sheet(path, doc, contained_under=self.tree)
        self.assertEqual(SS.read_sheet(path)["config_version"], "1")
        # The v1 preimage is the OLD field list and the OLD fold: five cells
        # per row, hashed exactly as they were before this file existed.
        self.assertEqual(len(SS.sheet_row_keys("1")), 5)
        self.assertEqual(len(SS.sheet_row_keys("2")), 7)
        self.assertEqual(len(SS.sheet_row_keys("3")), 8)
        canonical = SS.canonical_sheet_string(doc)
        for key in ("rule", "evidence"):
            self.assertNotIn(f"{key}=", canonical)

    def test_a_v1_sheet_cannot_be_newly_signed(self):
        """Refusing to BIND an unmigrated sheet is the whole point: a v1 sheet
        carries no rule and no evidence, so signing it is signing an anchor
        id. Named as a migration, with the remedy in the sentence."""
        path = self.obs / "survey-SVY-0001.yml"
        SS.write_sheet(path, self._v1_sheet(), contained_under=self.tree)
        proc = self._run("--batch", "SVY-0001")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        findings = self._findings(proc)
        self.assertIn("config_version", findings)
        self.assertIn("Re-scaffold the batch", findings)
        self.assertEqual(self._records(), [])

    def test_a_v1_sheet_cannot_be_signed_on_resume(self):
        """N1: `assert_signable` ran only on the NO-receipt branch, so a v1
        sheet standing beside a receipt — a resumed or transplanted batch —
        published without it. A v1 sheet also carries no tree-identity fields,
        so `assert_tree_identity` returns early and cannot catch it; the fix
        runs `assert_signable` on every publish path.

        Driven end to end: advance a real v2 batch to S1 so a receipt exists,
        then swap a v1 sheet into the archived slot and resume. Refused before
        any record is written, and refused for `config_version` specifically —
        not the immutability or digest edge — which is what pins the seam."""
        bid = self._prepare({self.a1: "ratify", self.a2: "defer"})
        SO.advance(self._ctx(bid))                    # S0 -> S1: receipt exists
        paths = SS.receipt_paths(self.obs, bid)
        self.assertTrue(paths["receipt"].is_file())
        SS.write_sheet(paths["sheet"], self._v1_sheet(bid),
                       contained_under=self.tree)
        if paths["live"].is_file():
            paths["live"].unlink()
        proc = self._run("--batch", bid)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        findings = self._findings(proc)
        self.assertIn("config_version", findings)
        self.assertIn("Re-scaffold the batch", findings)
        self.assertEqual(self._records(), [])

    def test_the_scaffold_writes_the_version_this_lane_signs(self):
        bid = self._scaffold()
        self.assertEqual(
            SS.read_sheet(self._sheet_path(bid))["config_version"],
            SS.CONFIG_VERSION)

    def test_a_v2_sheet_cannot_be_newly_signed_and_the_refusal_names_the_slug(self):
        """The v2 -> v3 seam, on the v1 precedent above: a v2 sheet carries
        `rule` and `evidence` but no `slug` cell, so the sign-off has no cell
        to consume. The refusal must say THAT — not "no rule and no
        evidence", which is false of a v2 sheet and names the wrong remedy."""
        path = self.obs / "survey-SVY-0001.yml"
        v2 = self._v1_sheet()
        v2["config_version"] = "2"
        v2["scaffold_provenance"].update(SS.tree_identity(self.root, self.tree))
        v2["rows"][0].update({"rule": "The gateway depends on httpx",
                              "evidence": "src/a.py:1-1"})
        SS.write_sheet(path, v2, contained_under=self.tree)
        proc = self._run("--batch", "SVY-0001")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        findings = self._findings(proc)
        self.assertIn("config_version", findings)
        self.assertIn("no `slug`", findings)
        self.assertNotIn("no `rule`", findings)
        self.assertIn("Re-scaffold the batch", findings)
        self.assertEqual(self._records(), [])


# ── the digest's whitespace blind spot ─────────────────────────────────────

ZS_VARIANTS = ("run time", "run\u00a0time", "run\u2007time", "run\u3000time")


class WhitespaceCanonicalizationTests(_ClaimCase):
    """One canonicalization, at digest time and at publish time.

    The digest folded every `str.isspace()` run — which includes every Unicode
    `Zs` — to one space, while the publish only called `.strip()`. So four
    domains that digest identically wrote four different strings to disk, and
    `domain` is the doctrine's per-domain grouping key."""

    def _sheet_with_domain(self, bid, domain):
        sheet = SS.read_sheet(self._sheet_path(bid))
        for row in sheet["rows"]:
            row["verdict"] = "ratify" if row["anchor_id"] == self.a1 else "defer"
            row["domain"] = domain if row["anchor_id"] == self.a1 else ""
            row["rationale"] = "Reviewed against the cited lines."
        return sheet

    def test_a_zs_moves_the_digest(self):
        bid = self._scaffold()
        digests = {v: SS.sheet_digest(self._sheet_with_domain(bid, v))
                   for v in ZS_VARIANTS}
        self.assertEqual(
            len(set(digests.values())), len(ZS_VARIANTS),
            f"{len(set(digests.values()))} distinct digests for "
            f"{len(ZS_VARIANTS)} distinct domains: "
            + json.dumps({repr(k): v[:12] for k, v in digests.items()},
                         indent=2))

    def test_canonically_equivalent_text_digests_identically(self):
        """The other side of the same function: NFC. Two spellings of one
        character are one string, so they cannot digest apart and then render
        identically."""
        bid = self._scaffold()
        composed = self._sheet_with_domain(bid, "caf\u00e9 runtime")
        decomposed = self._sheet_with_domain(bid, "cafe\u0301 runtime")
        self.assertEqual(SS.sheet_digest(composed),
                         SS.sheet_digest(decomposed))

    def test_a_zs_edit_after_the_signature_refuses(self):
        """The reproduction, end to end: bind the batch, then edit the
        archived sheet's domain by exactly one `Zs`. The digest the receipt
        records did not move, so the resume published a domain the signature
        never covered."""
        bid = self._prepare({self.a1: "ratify", self.a2: "defer"},
                            domains={self.a1: "run time"})
        SO.advance(self._ctx(bid))            # cell 1: bind, live sheet gone
        archived = SS.receipt_paths(self.obs, bid)["sheet"]
        archived.write_text(
            archived.read_text(encoding="utf-8")
            .replace("run time", "run\u00a0time"), encoding="utf-8")
        proc = self._run("--batch", bid)
        published = [self._frontmatter(n)["governs"][0]["domain"]
                     for n in self._records()]
        self.assertEqual(
            proc.returncode, 1,
            f"published {published!r} under an unchanged digest")
        self.assertIn("no longer matches the digest", self._findings(proc))

    def test_a_non_whitespace_edit_after_the_signature_still_refuses(self):
        """The control this fix may not break: the ordinary tampering case
        refused before and refuses now."""
        bid = self._prepare({self.a1: "ratify", self.a2: "defer"},
                            domains={self.a1: "run time"})
        SO.advance(self._ctx(bid))
        archived = SS.receipt_paths(self.obs, bid)["sheet"]
        archived.write_text(
            archived.read_text(encoding="utf-8")
            .replace("run time", "walk time"), encoding="utf-8")
        proc = self._run("--batch", bid)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("no longer matches the digest", self._findings(proc))

    def test_an_untouched_bound_sheet_still_publishes(self):
        """The near-miss control for both refusals above."""
        bid = self._prepare({self.a1: "ratify", self.a2: "defer"},
                            domains={self.a1: "run time"})
        SO.advance(self._ctx(bid))
        proc = self._run("--batch", bid)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(
            [self._frontmatter(n)["governs"][0]["domain"]
             for n in self._records()], ["run time"])

    def test_a_zs_domain_lands_on_disk_exactly_as_it_was_signed(self):
        """The publish writes the SAME canonical string the digest hashed. A
        `Zs` is content, not whitespace to fold: it survives, and the
        signature covers it."""
        bid = self._prepare({self.a1: "ratify", self.a2: "defer"},
                            domains={self.a1: "run\u00a0time"})
        signed = SS.sheet_digest(SS.read_sheet(self._sheet_path(bid)))
        proc = self._run("--batch", bid)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        receipt = SS.read_receipt(SS.receipt_paths(self.obs, bid)["receipt"])
        self.assertEqual(receipt["digest"], signed)
        self.assertEqual(
            [self._frontmatter(n)["governs"][0]["domain"]
             for n in self._records()], ["run\u00a0time"])


# ── the index cell ─────────────────────────────────────────────────────────

FORGED_ROW = "runtime | ratified | recovered | forged | `x` | `deadbeef` | —"
FORGED_REF = "runtime | ratified | OBS-9999 | forged"


class IndexCellEscapeTests(_ClaimCase):
    """Every cell `_index_row` writes is escaped for the channel it lands in.

    A `|` in a domain rendered as a cell separator, so one record's row became
    ten cells where the header declares seven — a row a human reads as two
    records, one of which does not exist."""

    def _cells(self, line: str) -> list[str]:
        return [c for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]

    def _rows(self):
        text = (self.obs / "index.md").read_text(encoding="utf-8")
        return [ln for ln in text.splitlines() if ln.startswith("| OBS-")]

    def _publish_with_domain(self, domain: str):
        bid = self._prepare({self.a1: "ratify", self.a2: "defer"},
                            domains={self.a1: domain})
        proc = self._run("--batch", bid)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return bid

    def test_a_pipe_in_a_domain_does_not_forge_a_cell(self):
        self._publish_with_domain(FORGED_ROW)
        rows = self._rows()
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(
            len(self._cells(rows[0])), 7,
            f"the header declares 7 cells; this row rendered "
            f"{len(self._cells(rows[0]))}: {rows[0]}")

    def test_the_forged_row_leaves_the_checker_clean(self):
        self._publish_with_domain(FORGED_ROW)
        report = _check_module().check(self.root, "bionic")
        self.assertEqual(report["broken"], [])
        self.assertEqual(report["warning"], [])

    def test_an_ordinary_domain_is_unchanged(self):
        """The control: escaping is not mangling. A domain with nothing to
        escape reaches the cell verbatim."""
        self._publish_with_domain("runtime")
        rows = self._rows()
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(self._cells(rows[0])[3].strip(), "runtime")

    def test_every_cell_is_escaped_not_only_the_domain(self):
        """`_index_row` writes five caller-supplied values. The escape is the
        channel's rule, so it applies to all of them — a guard on `domain`
        alone leaves the identical hole one argument to the left."""
        row = SO._index_row("OBS-0001| forged", "ratified| forged",
                            "runtime| forged", ["src/a.py:1-1| forged"],
                            "deadbeefdeadbeef| forged")
        self.assertEqual(len(self._cells(row)), 7, row)
        self.assertNotIn("\n", row.rstrip("\n"))


# ── the tree a receipt was signed against ──────────────────────────────────

class ReceiptTreeBindingTests(_ClaimCase):
    """A receipt names the tree it was signed against.

    Anchor ids are content-derived, so two repositories that depend on the
    same library carry the same anchor. Nothing bound a receipt to a tree, so
    a batch signed in one repository was copied into another and published
    there — the human's verdict, on another project's records, at exit 0."""

    def _elsewhere(self) -> Path:
        """A scratch directory of this test's own. `self.root` IS the case's
        temporary directory, so its parent is the shared system temp — two
        cases writing a sibling there would collide."""
        import tempfile
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _foreign_tree(self, docs_dir="docs") -> Path:
        """A second repository: its own config, its own tree, and the SAME
        candidate claim (a shared dependency mines to the same anchor and the
        same rule in both), so nothing but the tree binding can refuse it."""
        root = self._elsewhere() / f"other-{docs_dir}"
        tree = root / docs_dir
        (tree / "observations").mkdir(parents=True)
        (root / ".bionic.yml").write_text(f"docs_dir: {docs_dir}\n",
                                          encoding="utf-8")
        (tree / "manifest.yml").write_text(MANIFEST, encoding="utf-8")
        (tree / "log.md").write_text("# Log\n", encoding="utf-8")
        (root / "src").mkdir(exist_ok=True)
        (root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
        (root / "src" / "b.py").write_text("y = 2\n", encoding="utf-8")
        state = StateFile(tree / "arch" / "_recovered" / "state.yml")
        for cid, name, rule, ev in (
                (self.a1, "httpx", "The gateway depends on httpx",
                 "src/a.py:1-1"),
                (self.a2, "pyyaml", "The loader pins pyyaml exactly",
                 "src/b.py:1-1")):
            state.rows[cid] = {"id": cid, "anchor_kind": "external-dependency",
                               "canonical_anchor": name, "rule": rule,
                               "evidence": [ev], "state": "observed",
                               "domain": "runtime"}
        state.save()
        return root

    def _transplant(self, bid: str, target_root: Path, docs_dir: str) -> None:
        """Copy the signed batch — the archived sheet and its receipt — into
        the other tree, exactly as a `cp -r` of a `_surveys/` directory or a
        merged branch would."""
        source = SS.receipt_paths(self.obs, bid)["dir"]
        dest = target_root / docs_dir / "observations" / "_surveys" / bid
        shutil.copytree(source, dest)

    def _signoff_in(self, root: Path, bid: str):
        return subprocess.run(
            [sys.executable, str(SIGNOFF), "--repo-root", str(root),
             "--date", DATE, "--batch", bid], capture_output=True, text=True)

    def test_the_scaffold_records_the_tree_it_scaffolded_into(self):
        bid = self._scaffold()
        prov = SS.read_sheet(self._sheet_path(bid))["scaffold_provenance"]
        self.assertEqual(prov["tree"], "bionic")
        self.assertRegex(str(prov["tree_id"]), r"^[0-9a-f]{64}$")

    def test_a_receipt_refuses_to_publish_into_another_tree(self):
        bid = self._prepare({self.a1: "ratify", self.a2: "ratify"})
        SO.advance(self._ctx(bid))            # cell 1: bind in this tree
        other = self._foreign_tree()
        self._transplant(bid, other, "docs")
        proc = self._signoff_in(other, bid)
        published = sorted(p.name for p in
                           (other / "docs" / "observations").glob("OBS-*.md"))
        self.assertEqual(
            proc.returncode, 1,
            f"published {published} into a tree the batch was not signed "
            f"against: {proc.stdout}{proc.stderr}")
        self.assertIn("was signed against", proc.stdout)
        self.assertEqual(published, [])

    def test_the_same_tree_at_another_path_still_publishes(self):
        """The legitimate case a path-based binding would have broken: the
        same tree, moved or cloned to another absolute path. Tree identity is
        the tree's own committed configuration, never its location."""
        bid = self._prepare({self.a1: "ratify", self.a2: "ratify"})
        SO.advance(self._ctx(bid))
        clone = self._elsewhere() / "clone"
        shutil.copytree(self.root, clone)
        proc = self._signoff_in(clone, bid)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(
            len(sorted((clone / "bionic" / "observations").glob("OBS-*.md"))),
            2)

    def test_the_home_tree_still_publishes(self):
        """The near-miss control: the untransplanted batch, in the tree it was
        signed against, publishes."""
        bid = self._prepare({self.a1: "ratify", self.a2: "ratify"})
        SO.advance(self._ctx(bid))
        proc = self._run("--batch", bid)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(len(self._records()), 2)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
