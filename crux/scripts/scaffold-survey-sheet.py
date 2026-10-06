#!/usr/bin/env python3
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "httpx>=0.27",
#     "pyyaml==6.0.3",
# ]
# ///
# `httpx` is here because it is REACHED, not because this script calls it:
# importing `crux.arch.recover` pulls the `crux` package, whose council module
# imports the LLM router, which imports httpx. Declaring only PyYAML passes in
# the dev venv — where httpx is ambient — and fails under `uv run --no-project`
# in the release gate, which is the only place it is ever tested. `survey.py`
# carries the same declaration for the same reason. The PyYAML pin is exact on
# that file's precedent.
"""scaffold-survey-sheet.py — write the batch review sheet a human then signs.

The machine half of batch ratification (ADR-0098; the protocol is
docs/AGENTS.md §17.5). It reads the candidate state file, filters it to the
candidates no receipt has disposed and no live record already holds, allocates
the next `SVY-NNNN` batch id, and writes one sheet with `anchor_id`,
`proposed_domain`, `rule` and `evidence` seeded, `slug` PROPOSED (the
title-derived slug the human may overwrite; ADR-0099 clause 3), and
`verdict`, `domain` and `rationale` EMPTY.

`rule` and `evidence` are seeded VERBATIM from the candidate, and they are
inside the signed digest. Without them the sheet carried an anchor id and the
rule was read from the candidate state file at publish time, so what the human
signed and what the batch published were two different things.

**It fills no human cell.** That is the whole boundary this script sits on: a
machine may scaffold a row and never fill a verdict, a domain or a rationale
(clause 4). The counterpart script `signoff-survey.py` refuses an empty
verdict, so a sheet this script wrote can never publish anything until a human
has authored every one of those cells.

The batch id comes from the monotonic `observation.next_survey_number` (§7),
never from the sheet's content: a content-derived id collides across
re-scaffolds of an identical candidate set, and two such re-scaffolds are two
batches (postcondition (g)). The counter is advanced BEFORE the sheet is
written — over-allocation is safe and reuse is not.

One live sheet at a time. A second scaffold while a sheet is still awaiting
sign-off refuses: two live sheets over overlapping candidate sets would both
ratify one anchor and leave two live records on it (CHK-OBS-ANCHOR).

Usage:
  scaffold-survey-sheet.py --repo-root DIR [--docs-dir NAME] [--dry-run]

Exit: 0 scaffolded (or nothing to scaffold) · 1 findings (JSON on stdout) ·
2 environment (stderr). `--dry-run` writes nothing at all — not the sheet and
not the counter — and reports the sheet it would have written.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import summaries_projection as sp        # noqa: E402
import survey_sheet as ss                # noqa: E402
from untrusted import redact             # noqa: E402
from bionic_config import (              # noqa: E402
    BionicConfigError,
    validate_docs_dir_override,
)
from crux.arch.recover import (          # noqa: E402
    StateFile,
    anchor_of,
    read_recorded_observations,
)

TOOL = "scaffold-survey-sheet.py"


def _eligible(rows: dict, recorded: dict, disposed: dict) -> list[tuple[str, dict]]:
    """The candidates a batch may carry, in candidate-key order.

    Four filters, and each one has a reason a sign-off would otherwise refuse
    the row it admitted:

      * `state != "observed"` — the candidate is already disposed;
      * the CANDIDATE KEY is in `disposed` — a receipt in this tree has
        ratified or rejected that candidate. Keyed on the candidate rather than
        the anchor, because a successor candidate sits on an anchor a previous
        batch already disposed and the successor lane is the only route a
        changed claim has. `defer` is absent from that map, which is the whole
        of "defer is the only re-scaffoldable verdict";
      * a LIVE record already holds the anchor and the candidate is not a
        successor — `build_plan` refuses that row, so scaffolding it would
        write a sheet no sign-off could sign;
      * the candidate carries no rule — there is nothing to review.
    """
    out = []
    for cid in sorted(rows):
        cand = rows[cid]
        if cand.get("state") != "observed":
            continue
        anchor = anchor_of(cid)
        if cid in disposed:
            continue
        live = recorded.get(anchor)
        if (live is not None and live.get("status") in ("observed", "ratified")
                and not cand.get("predecessor_id")):
            continue
        if not str(cand.get("rule") or "").strip():
            continue
        out.append((cid, cand))
    return out


def _anchor_collisions(eligible: list[tuple[str, dict]]) -> dict[str, list[str]]:
    """`{anchor_id: [candidate_id, ...]}` for every anchor two eligible
    candidates share.

    `_eligible` returns one entry per CANDIDATE key while the sheet is keyed by
    `anchor_id`, and a successor candidate sits on its predecessor's anchor. So
    two eligible candidates can land on one anchor — a plain candidate and a
    successor on the same anchor both survive the filters whenever no LIVE
    record holds it, which is the state a retired or rejected predecessor
    leaves behind. The sheet that results carries duplicate `anchor_id` rows,
    and `read_sheet` refuses it: the batch would be unsignable with its SVY
    number already burnt and no scripted recovery.

    Detected BEFORE the counter moves, so the refusal costs no number."""
    by_anchor: dict[str, list[str]] = {}
    for cid, _cand in eligible:
        by_anchor.setdefault(anchor_of(cid), []).append(cid)
    return {a: cids for a, cids in by_anchor.items() if len(cids) > 1}


def scaffold(root: Path, docs_dir: str | None, *, today: str,
             dry_run: bool) -> tuple[int, dict]:
    # `--docs-dir` is an operator-supplied component joined onto a validated
    # root, and a guard that validates a prefix and then concatenates an
    # unvalidated suffix has validated nothing. It takes the SAME verdict the
    # configured value takes, through the one shared implementation — not a
    # local re-check, and not the write guards below, which cannot see it: every
    # write here is `contained_under=tree` and the traversed path IS `tree`.
    tree = (sp.resolve_tree(root) if docs_dir is None
            else validate_docs_dir_override(root, docs_dir))
    manifest = sp.read_manifest(root)
    concerns = manifest.get("concerns_enabled") or []
    if "observations" not in concerns:
        raise EnvironmentError(
            "`observations` is absent from manifest.yml concerns_enabled — "
            "batch ratification is the observations concern's gate")
    # Beside the concern gate because it is the same kind of statement: a tree
    # this lane cannot ratify into. See `survey_sheet.assert_unprefixed_tree`.
    ss.assert_unprefixed_tree(root)
    # [SECURITY:S5] The concern directory is RESOLVED and proven contained
    # before anything resolves against it. Every reader below joins names
    # onto `obs`, and `resolve_contained(obs, name)` resolves `obs` too — so
    # a symlinked concern directory compares an outside directory against
    # itself and admits everything under it. One implementation, shared with
    # the projection that reads the same directory.
    obs = sp.observations_root(tree)
    if not obs.is_dir():
        raise EnvironmentError(f"the observations directory {redact(obs, quoted=False)} is absent")

    live = sorted(p.name for p in obs.glob("survey-*.yml")
                  if ss.SHEET_RE.match(p.name))
    if live:
        return 1, {"findings": [
            f"{live[0]} is still awaiting sign-off — one live sheet at a "
            "time, because two sheets over overlapping candidates would both "
            "ratify one anchor"]}

    state_path = tree.joinpath(*ss.STATE_FILE_REL)
    rows = StateFile(state_path).rows if state_path.is_file() else {}
    # A finding, never an environment error — the same lane
    # `survey_sheet.record_paths` puts the identical refusal in.
    try:
        recorded = read_recorded_observations(obs)
    except ValueError as exc:
        raise ss.SurveySheetError([str(exc)]) from exc
    disposed = ss.disposed_candidates(obs)
    eligible = _eligible(rows, recorded, disposed)

    # An unfinished batch holds every anchor its receipt names, because cell 1
    # deletes the live sheet and the refusal above is the only other interlock.
    # See `survey_sheet.unfinished_batch_anchors`.
    reserved = ss.unfinished_batch_anchors(obs)
    held = sorted({reserved[anchor_of(cid)] for cid, _c in eligible
                   if anchor_of(cid) in reserved})
    eligible = [(cid, cand) for cid, cand in eligible
                if anchor_of(cid) not in reserved]

    if not eligible:
        note = "no candidate awaits a batch"
        if held:
            note = (f"every candidate awaiting a batch sits on an anchor "
                    f"{', '.join(held)} has bound and not yet published — "
                    "re-run signoff-survey.py on it, then scaffold again")
        return 0, {"batch_id": None, "sheet": None, "rows": 0, "written": [],
                   "dry_run": dry_run, "note": note}

    collisions = _anchor_collisions(eligible)
    if collisions:
        return 1, {"findings": [
            f"anchor {anchor} carries {len(cids)} eligible candidates "
            f"({', '.join(sorted(cids))}) — a sheet is keyed by anchor, so "
            "these rows would collide and no sign-off could read the sheet. "
            "Dispose all but one through a single-record transition, then "
            "scaffold again."
            for anchor, cids in sorted(collisions.items())]}

    number = ss.read_counter(manifest, "observation", "next_survey_number", 1)
    batch_id = f"SVY-{number:04d}"
    paths = ss.receipt_paths(obs, batch_id)
    sheet = {
        "config_version": ss.CONFIG_VERSION,
        "batch_id": batch_id,
        "scaffold_provenance": {
            "tool": TOOL,
            "scaffolded": today,
            "state_file": str(Path(*ss.STATE_FILE_REL)),
            "candidates": len(eligible),
            # The tree this batch belongs to. Inside the digest, so the
            # receipt carries it and a batch cannot publish into another
            # tree. See `survey_sheet.tree_identity`.
            **ss.tree_identity(root, tree),
        },
        "rows": [
            {
                "anchor_id": anchor_of(cid),
                "proposed_domain": str(cand.get("domain") or ""),
                # The two seeded claim cells, verbatim from the candidate
                # through the one shared `seed_cells`. Machine-written and
                # read-only: they sit beside the verdict so the human reads
                # the claim they are signing, they are inside the digest, and
                # the sign-off refuses a row whose seed no longer matches the
                # candidate — in either direction.
                **ss.seed_cells(cand),
                "verdict": "",
                "domain": "",
                # The slug cell (ADR-0099 clause 3): PROPOSED here, through
                # the one shared `proposed_slug`, and the human may overwrite
                # it. This is the one human-editable cell the scaffold does
                # not leave empty — the proposal IS the cell's default, and
                # the sign-off records whether it stood or was overridden.
                # The proposal is not checked here: a digit-led or empty
                # proposal is refused at sign-off by name, which is the
                # human's cue to write the slug.
                "slug": ss.proposed_slug(cand),
                "rationale": "",
            }
            for cid, cand in eligible
        ],
    }
    payload = {
        "batch_id": batch_id,
        "sheet": str(paths["live"].relative_to(root)),
        "rows": len(sheet["rows"]),
        "anchors": [r["anchor_id"] for r in sheet["rows"]],
        "written": [],
        "dry_run": dry_run,
    }
    if dry_run:
        return 0, payload

    # The counter moves FIRST. Over-allocation is safe (a burnt number is a
    # number nobody signs); reuse is not (two batches under one id).
    ss.write_counter(tree / "manifest.yml", "observation",
                     "next_survey_number", number + 1, contained_under=tree)
    ss.write_sheet(paths["live"], sheet, contained_under=tree)
    payload["written"] = [str(paths["live"].relative_to(root)),
                          str((tree / "manifest.yml").relative_to(root))]
    return 0, payload


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="scaffold-survey-sheet",
        description="Scaffold a batch review sheet from the candidate state file.")
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--docs-dir", default=None)
    ap.add_argument("--date", default=None,
                    help="the scaffold date recorded in the sheet's provenance")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    root = Path(args.repo_root).resolve()
    today = args.date or date.today().isoformat()
    try:
        code, payload = scaffold(root, args.docs_dir, today=today,
                                 dry_run=args.dry_run)
    except ss.SurveySheetError as exc:
        print(json.dumps({"findings": exc.problems}, indent=2))
        return 1
    except sp.GovernsValidationError as exc:
        sys.stderr.write(f"{TOOL}: {exc}\n")
        return 2
    except (BionicConfigError, EnvironmentError, ValueError) as exc:
        sys.stderr.write(f"{TOOL}: {exc}\n")
        return 2
    print(json.dumps(payload, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
