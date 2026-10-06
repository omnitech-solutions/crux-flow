#!/usr/bin/env python3
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "httpx>=0.27",
#     "pyyaml==6.0.3",
# ]
# ///
# `httpx` is REACHED, not called: importing `crux.arch.recover` pulls the
# `crux` package, whose council module imports the LLM router, which imports
# httpx. Declaring only PyYAML passes in the dev venv, where httpx is ambient,
# and fails under `uv run --no-project` in the release gate — the only place it
# is tested. `survey.py` carries the same declaration for the same reason.
"""signoff-survey.py — the human sign-off that publishes one batch.

ADR-0098; the protocol's source of truth is docs/AGENTS.md §17.5, whose state
table this file implements cell for cell. It is the ONLY batch route past
`observed`, and it is equivalent to N individual ratifications under one signed
receipt. `transition-observation` remains the only single-record route.

**Re-entrant by construction.** There is no resume flag and no phase argument.
Every run derives the batch's state from disk through the one shared function
`survey_sheet.batch_state`, applies the single write that state's cell names,
and repeats until S9. That is what makes postcondition (b) hold: re-running on
an unchanged sheet completes an unfinished publish and is otherwise a reported
no-op. It is also what makes a gate unable to disagree with the writer — the
audit rules and the projection refusal read the same function.

**Completion precedes visibility.** Record bodies stage under
`_surveys/SVY-NNNN/staged/`, which no record walk sees (both walks are a
non-recursive `glob("*.md")` over the concern directory). The receipt's
`completed` and `records` land in ONE atomic single-file write before any
record exists under `observations/`. So no visible record ever stands behind a
receipt recording no completion — CHK-OBS-SURVEY-VISIBLE is satisfied
structurally rather than by timing. The reverse order would trip that rule on
every healthy publish.

One window survives, and the protocol leaves it open deliberately: the receipt
is complete, some records are promoted, and the index is stale. CHK-OBS-
BIJECTION already reports it and the projections already refuse to render it,
and the remedy is total — the staged bytes remain and the receipt carries every
planned id, path and slug, so a plain re-run converges.

Usage:
  signoff-survey.py --repo-root DIR [--docs-dir NAME] [--batch SVY-NNNN]
                    [--date YYYY-MM-DD] [--dry-run]

Exit: 0 published / completed / no-op · 1 findings (JSON on stdout) ·
2 environment (stderr). `--dry-run` renders every planned record id, path and
derived retirement and writes nothing.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import doctrine_projection as dp         # noqa: E402
import observation_evidence as oe        # noqa: E402
import summaries_projection as sp        # noqa: E402
import survey_sheet as ss                # noqa: E402
from untrusted import MESSAGE_LIMIT, redact  # noqa: E402
from bionic_config import (              # noqa: E402
    BionicConfigError,
    validate_docs_dir_override,
)
from crux.arch.recover import (          # noqa: E402
    StateFile,
    read_recorded_observations,
)

TOOL = "signoff-survey.py"

INDEX_HEADER = (
    "| id | status | provenance | domain | evidence | anchor_id | "
    "related invariants |\n"
    "|----|--------|------------|--------|----------|-----------|"
    "--------------------|\n"
)


class Contradiction(ss.SurveySheetError):
    """A surface is present but contradicts the receipt. Cell 16: refuse,
    never overwrite. A distinct type so the caller can say so."""


# ── context ────────────────────────────────────────────────────────────────

def make_context(root: Path, docs_dir: str | None, batch_id: str | None,
                 today: str) -> dict:
    root = Path(root).resolve()
    # `--docs-dir` is an operator-supplied component joined onto a validated
    # root, and a guard that validates a prefix and then concatenates an
    # unvalidated suffix has validated nothing. It takes the SAME verdict the
    # configured value takes, through the one shared implementation — not a
    # local re-check, and not the write guards below, which cannot see it: every
    # write here is `contained_under=tree` and the traversed path IS `tree`.
    tree = (sp.resolve_tree(root) if docs_dir is None
            else validate_docs_dir_override(root, docs_dir))
    manifest = sp.read_manifest(root)
    if "observations" not in (manifest.get("concerns_enabled") or []):
        raise EnvironmentError(
            "`observations` is absent from manifest.yml concerns_enabled — "
            "batch ratification is the observations concern's gate")
    # Beside the concern gate because it is the same kind of statement: a tree
    # this lane cannot ratify into. It is re-taken HERE rather than trusted
    # from the scaffold, because the sign-off is the writer and it re-reads the
    # concern on every cell.
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
    if batch_id is None:
        batch_id = _infer_batch(obs)
    if not ss.BATCH_ID_RE.match(batch_id):
        raise ValueError(f"batch id {redact(batch_id)} is not SVY-NNNN")
    return {"root": root, "tree": tree, "obs": obs, "batch_id": batch_id,
            "paths": ss.receipt_paths(obs, batch_id), "date": today,
            "writes": []}


def _infer_batch(obs: Path) -> str:
    """The one batch this invocation means: the live sheet if there is one,
    else the one incomplete batch. Ambiguity refuses rather than guesses — a
    sign-off that picked the wrong batch would sign a human's name onto a
    review they did not read."""
    live = sorted(ss.SHEET_RE.match(p.name).group(1) for p in obs.glob("survey-*.yml")
                  if ss.SHEET_RE.match(p.name))
    if len(live) == 1:
        return live[0]
    if live:
        raise ValueError(f"more than one live sheet ({redact(', '.join(live), quoted=False)}) — "
                         "name the batch with --batch")
    known = list(ss.batch_ids(obs))
    pending = [bid for bid in known
               if ss.batch_state(ss.receipt_paths(obs, bid)["receipt"], obs) != "S9"]
    if len(pending) == 1:
        return pending[0]
    if pending:
        raise ValueError(f"more than one unfinished batch "
                         f"({redact(', '.join(pending), quoted=False)}) — name one with --batch")
    # No live sheet and nothing unfinished. A single PUBLISHED batch is still
    # this invocation's subject: §17.5 cell 15 makes a re-run over it a
    # reported no-op at exit 0, and `survey-signoff` documents the bare
    # re-invocation as exactly that. Refusing here returned exit 2 instead —
    # the environment lane, which callers skip rather than fail on — for the
    # single most likely command an operator types after a successful publish.
    if len(known) == 1:
        return known[0]
    if known:
        raise ValueError(f"no live sheet and no unfinished batch; more than "
                         f"one published batch ({redact(', '.join(known), quoted=False)}) — name one "
                         "with --batch")
    raise ValueError("no live sheet and no batch in this tree — scaffold a "
                     "sheet with scaffold-survey-sheet.py first")


# ── the plan ───────────────────────────────────────────────────────────────

def _read_bound_sheet(ctx: dict) -> dict:
    """The sheet this batch is bound to, live or archived.

    At S0 the live sheet is the subject. Past S0 the ARCHIVED copy is, and its
    digest is re-checked against the receipt on every run — cell 4's refusal.
    A live sheet still sitting beside a signed archive must be byte-identical
    to it; anything else is an edit made after the signature (cell 16).
    """
    paths = ctx["paths"]
    if paths["sheet"].is_file():
        if paths["live"].is_file():
            if paths["live"].read_bytes() != paths["sheet"].read_bytes():
                raise Contradiction([
                    f"{redact(paths['live'].name, quoted=False)} differs from the archived sheet "
                    f"this batch was signed on — a signed sheet is immutable, "
                    "so the live copy is never edited after the signature"])
        return ss.read_sheet(paths["sheet"])
    if paths["live"].is_file():
        return ss.read_sheet(paths["live"])
    raise ss.SurveySheetError([
        f"neither {redact(paths['live'], quoted=False)} nor {redact(paths['sheet'], quoted=False)} exists — there is no "
        f"sheet for batch {redact(ctx['batch_id'], quoted=False)}"])


def build_plan(ctx: dict) -> tuple[dict, list[dict]]:
    """Validate the whole batch and return `(sheet, plan)`. Every pre-write
    refusal runs here, before the first write of any run — including a resume,
    so a tree that drifted under a half-published batch refuses rather than
    completing onto it."""
    sheet = _read_bound_sheet(ctx)
    # The tree, before anything else about the batch is believed. A receipt
    # and its archived sheet are one directory; copying that directory into
    # another repository published one project's signed verdicts into
    # another's concern at exit 0, because anchor ids are content-derived and
    # the transplanted rows resolved cleanly against the other tree's
    # candidates. Checked on the SHEET and on the RECEIPT: both travel, and
    # the digest binds them to each other, not to a tree.
    ss.assert_tree_identity(sheet.get("scaffold_provenance"), ctx["root"],
                            ctx["tree"],
                            subject=f"batch {ctx['batch_id']}'s review sheet")
    if ctx["paths"]["receipt"].is_file():
        ss.assert_tree_identity(
            _receipt(ctx).get("scaffold_provenance"), ctx["root"],
            ctx["tree"], subject=f"batch {ctx['batch_id']}'s receipt")
    # A pre-v2 sheet carries no `rule` and no `evidence`, so its signature
    # covers an anchor id rather than a claim — refused on EVERY publish path,
    # not only a first signature. A v1 sheet and receipt also lack the
    # tree-identity fields, so `assert_tree_identity` returns early and cannot
    # catch a transplanted or resumed v1 batch; `assert_signable` is the refusal
    # that does. See `survey_sheet.assert_signable`.
    ss.assert_signable(sheet)
    state_path = ctx["tree"].joinpath(*ss.STATE_FILE_REL)
    rows = StateFile(state_path).rows if state_path.is_file() else {}
    # `read_recorded_observations` raises ValueError for its fail-closed parse
    # AND for its containment refusal. Both are findings in this lane, not
    # environment errors: `main` routes a bare ValueError to exit 2, which
    # callers skip rather than fail on, and `survey_sheet.record_paths` refuses
    # the very same file at exit 1. Two readers of one directory must not
    # disagree about which lane a refusal belongs in.
    try:
        recorded = read_recorded_observations(ctx["obs"])
    except ValueError as exc:
        raise ss.SurveySheetError([str(exc)]) from exc
    own: dict[str, str] = {}
    bound: dict[str, str] = {}
    if ctx["paths"]["receipt"].is_file():
        receipt = _receipt(ctx)
        # Keyed by ANCHOR, not a flat id set: each exemption in `build_plan`
        # is for the row that allocated the record, never for a peer row.
        own = {r["anchor_id"]: r["record_id"] for r in receipt["rows"]
               if r["record_id"]}
        # The row's candidate is the one the SIGNATURE named, never the one an
        # anchor re-resolves to now. See `survey_sheet.build_plan`.
        bound = {r["anchor_id"]: r["candidate_id"] for r in receipt["rows"]}
    live_slugs, retired_slugs = _slug_maps(ctx)
    plan = ss.build_plan(sheet, rows, recorded, root=ctx["root"],
                         obs_dir=ctx["obs"],
                         disposed_elsewhere=ss.disposed_candidates(ctx["obs"]),
                         own_record_ids=own, bound_candidates=bound,
                         live_slugs=live_slugs, retired_slugs=retired_slugs)
    if not ctx["paths"]["receipt"].is_file():
        ss.validate_receipt(planned_receipt(ctx, sheet, plan),
                            "the receipt this sign-off would write")
    return sheet, plan


def _receipt(ctx: dict) -> dict:
    return ss.read_receipt(ctx["paths"]["receipt"])


def _slug_maps(ctx: dict) -> tuple[dict, dict]:
    """`(live_slugs, retired_slugs)` over the corpus this batch publishes
    into — the two maps `build_plan`'s "already taken" refusal reads
    (ADR-0099 clause 3).

    The corpus is the same one the summaries projection reads: active ADRs
    UNION ratified observations, through `sp.collect_records`, and the maps
    come from `sp.live_and_retired_slugs`, the ONE builder the resolver uses.
    A sign-off that computed its own slug map could admit a slug the
    projection then refuses fail-closed for the whole tree; reading the
    resolver's builder is what makes the two agree.

    `collect_records` never consults the survey receipts — the below-S9
    refusal lives in `sp.survey_receipts_problem`, which the regenerators
    call and this lane does not — so an in-flight batch can read the corpus
    it is about to join. From S4 on its own promoted records are in that
    corpus; `build_plan` excludes each one for its OWN row, by
    `own_record_ids`.

    The ADR directory is joined onto `ctx["tree"]`, never resolved from the
    root: `--docs-dir` may name a tree the layout config does not, and every
    other path in this lane already comes off `ctx["tree"]`. An absent ADR
    directory reads as no ADR records, which is the observations-only tree
    the projection also admits.

    A `GovernsValidationError` here is a corpus already in violation — a
    duplicate handle, or two live slugs colliding — and it propagates to
    `main`'s exit-2 lane exactly as the projection's own refusal would."""
    manifest = sp.read_manifest(ctx["root"])
    records = sp.collect_records(ctx["tree"] / "adrs",
                                 governs_from=sp.governs_from(manifest),
                                 observations=ctx["obs"])
    return sp.live_and_retired_slugs(records)


def _sheet_ref(ctx: dict) -> str:
    """The receipt's pointer back at the sheet it was signed on. Spelled once,
    because the pre-write validation and cell 1 must agree byte for byte."""
    return (f"{ctx['obs'].name}/{ss.SURVEYS_DIRNAME}/"
            f"{ctx['batch_id']}/sheet.yml")


def _receipt_rows(plan: list[dict]) -> list[dict]:
    """The six human-and-machine cells cell 1 binds, one per planned row."""
    return [{"anchor_id": e["anchor_id"], "candidate_id": e["candidate_id"],
             "verdict": e["verdict"], "domain": e["domain"],
             "domain_source": e["domain_source"],
             "slug_source": e["slug_source"]}
            for e in plan]


def planned_receipt(ctx: dict, sheet: dict, plan: list[dict]) -> dict:
    """The receipt this batch will hold at S2: every row, carrying the record
    id, path and retirement the allocation predicts.

    Built so the WRITER can be run through the READER before the first write.
    `write_receipt` validates too, but once per write — and cell 1's write
    deletes the live sheet, so a row cell 3 could not express was caught with
    the batch bound, the sheet gone and no scripted remedy: each retry re-ran
    cell 3, moved the counter and refused again on the same cell. Validating
    the whole batch here costs a refusing batch nothing, because the human
    still holds the sheet.

    The prediction is the one `rendering` already shows the human: cell 3 walks
    the same plan in the same order from the same counter, so these ARE the
    ids and paths that land."""
    receipt = ss.new_receipt(sheet, _receipt_rows(plan), signed=ctx["date"],
                             sheet_ref=_sheet_ref(ctx))
    allocated = allocation(ctx, plan, None)
    by_anchor = {r["anchor_id"]: r for r in receipt["rows"]}
    for entry in plan:
        if entry["verdict"] != "ratify":
            continue
        record_id, record_path = allocated[entry["anchor_id"]]
        row = by_anchor[entry["anchor_id"]]
        row["record_id"] = record_id
        row["record_path"] = record_path
        row["retires"] = entry["retires"]
    return receipt


def allocation(ctx: dict, plan: list[dict], receipt: dict | None) -> dict:
    """`{anchor_id: (record_id, record_path)}` for every ratify row.

    Read off the receipt from S2 on, and PREDICTED from
    `observation.next_number` before that. The prediction is what makes clause
    4's rendering honest: a human must see every planned record id and path
    before the first write, and at S0/S1 no id exists yet. `_cell_3_allocate`
    walks the same plan in the same order from the same counter, so the
    predicted ids are the ids that land."""
    if receipt is not None:
        got = {r["anchor_id"]: (r["record_id"], r["record_path"])
               for r in receipt["rows"] if r["record_id"]}
        if got:
            return got
    number = ss.read_counter(sp.read_manifest(ctx["root"]), "observation",
                             "next_number", 1)
    out = {}
    for i, entry in enumerate(e for e in plan if e["verdict"] == "ratify"):
        rid = f"OBS-{number + i:04d}"
        out[entry["anchor_id"]] = (rid,
                                   f"{ctx['obs'].name}/{rid}-{entry['slug']}.md")
    return out


def rendering(ctx: dict, plan: list[dict], receipt: dict | None) -> list[str]:
    """Every planned record id, path and derived retirement, rendered BEFORE
    the first write (ADR-0098 clause 4)."""
    allocated = allocation(ctx, plan, receipt)
    lines = [f"batch {redact(ctx['batch_id'], quoted=False)} — {len(plan)} rows"]
    for entry in plan:
        rid, path = allocated.get(entry["anchor_id"], ("", ""))
        line = (f"  {redact(entry['anchor_id'], quoted=False)} {entry['verdict']:6} "
                f"domain={redact(entry['domain'], quoted=False)} ({redact(entry['domain_source'], quoted=False)})")
        if entry["verdict"] == "ratify":
            line += (f" -> {redact(rid, quoted=False)} "
                     f"{redact(path, quoted=False)} "
                     f"slug={redact(entry['slug'], quoted=False)} "
                     f"({redact(entry['slug_source'], quoted=False)})")
            if entry["retires"]:
                line += f" retires {redact(entry['retires'], quoted=False)}"
        lines.append(line)
        # The claim, beside the verdict it was signed under. The sheet shows
        # it in the cell; this shows it in the pre-write rendering, so the
        # operator running the sign-off sees the rule the record will carry
        # rather than an anchor id and a path.
        if entry.get("rule"):
            lines.append(f"      rule: "
                         f"{redact(entry['rule'], quoted=False, limit=MESSAGE_LIMIT)}")
    return lines


# ── record + index rendering ───────────────────────────────────────────────

def _yaml_str(value: str) -> str:
    """A double-quoted YAML scalar. `json.dumps` emits exactly the escapes a
    YAML double-quoted scalar admits for the ASCII range, and every string
    reaching here is `space_fold`ed, so no newline can appear."""
    return json.dumps(value, ensure_ascii=False)


def record_body(entry: dict, record_id: str, date: str) -> str:
    """The §17.1 record a ratified row publishes.

    The field set is §17.1's, in the template's order; the body is the
    template's three sections. `provenance` is `recovered` on every record this
    path writes — the claim was mined, and §17.1's enum excludes `authored`
    because an authored rule is an ADR.

    The frontmatter is parsed back before it is returned. A record whose
    frontmatter this tree's own reader cannot read would be published,
    unreadable, behind a receipt recording completion — the one state the
    protocol has no remedy for."""
    handle = f"{record_id}/{entry['slug']}"
    evidence = ", ".join(_yaml_str(e) for e in entry["evidence"])
    front = (
        "---\n"
        f"id: {record_id}\n"
        f"title: {_yaml_str(entry['title'])}\n"
        "status: ratified\n"
        f"date: {date}\n"
        f"observed_date: {date}\n"
        f"ratified_date: {date}\n"
        "rejected_date: null\n"
        "retired_date: null\n"
        "decided_date: null\n"
        "provenance: recovered\n"
        "decided_by: null\n"
        f"evidence: [{evidence}]\n"
        f"anchor_id: {_yaml_str(entry['anchor_id'])}\n"
        "related_invariants: []\n"
        "tags: []\n"
        "governs:\n"
        f"  - domain: {_yaml_str(entry['domain'])}\n"
        f"    rule: {_yaml_str(entry['rule'])}\n"
        f"    scope: {_yaml_str(entry['scope'])}\n"
        f"    handle: {handle}\n"
        "    provenance: recovered\n"
        "---\n"
    )
    import yaml
    try:
        parsed = yaml.safe_load(front.split("---\n")[1])
    except Exception as exc:  # noqa: BLE001
        raise ss.SurveySheetError([
            f"refusing to publish {redact(record_id, quoted=False)}: its frontmatter does not parse "
            f"({redact(exc, quoted=False, limit=MESSAGE_LIMIT)})"]) from exc
    if parsed.get("governs", [{}])[0].get("rule") != entry["rule"]:
        raise ss.SurveySheetError([
            f"refusing to publish {redact(record_id, quoted=False)}: its rule does not round-trip "
            "through the frontmatter reader"])
    body = (
        f"\n# {record_id} — {entry['title']}\n\n"
        "## What the code does\n\n"
        f"{entry['rule']}.\n\n"
        "## Evidence\n\n"
        + "".join(f"- `{e}`\n" for e in entry["evidence"])
        + "\n## Why this is observed, not decided\n\n"
        "No ADR records this rule. The code is the authority, and this record "
        f"describes it rather than prescribing it. Ratified in batch "
        f"{entry['batch_id']}.\n"
    )
    return front + body


def retire_body(text: str, date: str) -> str:
    """The predecessor's record with its lifecycle fields moved to `retired`.

    Only the lifecycle fields change: §17.2 makes a ratified record's claim —
    rule, domain, scope, provenance, evidence, anchor_id — immutable, and a
    retirement is a lifecycle transition, not a claim edit. The edit is
    line-oriented for exactly that reason: a parse-and-redump would rewrite
    every line in the file and could not prove it changed only these three."""
    out = []
    for line in text.splitlines():
        if line.startswith("status:"):
            out.append("status: retired")
        elif line.startswith("date:"):
            out.append(f"date: {date}")
        elif line.startswith("retired_date:"):
            out.append(f"retired_date: {date}")
        else:
            out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def _index_row(record_id: str, status: str, domain: str, evidence: list,
               anchor_id: str) -> str:
    """One `index.md` row, every caller-supplied cell escaped for the channel.

    A `|` in a mined `domain` reached the cell raw, so one record's row
    rendered as thirteen cells where the header declares seven — a row a human
    reads as several records, one of which does not exist, and which can carry
    a forged `OBS-NNNN` reference that CHK-OBS-BIJECTION then reports BROKEN.

    The escape is the CHANNEL's rule, so it applies to every value that
    crosses into the channel and not only to the one that was crafted first: a
    guard on `domain` alone leaves the identical hole one argument to the
    left. `doctrine_projection._md_escape` is the one implementation — it
    already bounds and redacts through `untrusted.redact` and then escapes the
    pipe — and a second copy here is exactly the drift this codebase has paid
    for before."""
    escape = dp._md_escape
    ev = ", ".join(f"`{escape(e)}`" for e in evidence)
    return (f"| {escape(record_id)} | {escape(status)} | recovered | "
            f"{escape(domain)} | {ev} | `{escape(anchor_id)}` | — |\n")


def build_index(ctx: dict, plan: list[dict], receipt: dict, existing: str) -> str:
    """The concern index after this batch: one row per new record inserted at
    the top of the table body, and the status cell rewritten on every row this
    batch retires.

    Surgical rather than regenerated. The index is hand-maintained (§17), so a
    wholesale rebuild would delete whatever a human wrote into it; every column
    here is frontmatter-derived, so inserting and rewriting is enough to leave
    CHK-OBS-BIJECTION clean, which is postcondition (d)."""
    if not existing.strip():
        existing = (
            "# Observations\n\n"
            f"_Last updated: {ctx['date']}_\n\n"
            "The observations concern (per `docs/AGENTS.md` §17): "
            "`governs`-shaped records of\n*what the code already does*, each "
            "evidenced by a `path:line-range`.\n\n"
            "## Records (0)\n\n"
            + INDEX_HEADER
        )
    lines = existing.splitlines(keepends=True)

    by_anchor = {r["anchor_id"]: r for r in receipt["rows"]}
    retired_ids = set()
    new_rows = []
    for entry in plan:
        if entry["verdict"] != "ratify":
            continue
        got = by_anchor[entry["anchor_id"]]
        new_rows.append(_index_row(got["record_id"], "ratified",
                                   entry["domain"], entry["evidence"],
                                   entry["anchor_id"]))
        if entry["retires"]:
            retired_ids.add(_id_of_path(entry["retires"]))

    # Rewrite the status cell of every row this batch retires. Split on
    # UNESCAPED pipes only: `_index_row` renders a mined `domain` through
    # `dp._md_escape`, which escapes an embedded `|` to `\|`. A naive
    # `split("|")` tears that cell in two and the rejoin drops the backslash,
    # so a retired hostile row re-renders with more cells than the header.
    import re
    for i, line in enumerate(lines):
        if not line.startswith("| "):
            continue
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
        if cells and cells[0] in retired_ids and len(cells) >= 2:
            cells[1] = "retired"
            lines[i] = "| " + " | ".join(cells) + " |\n"
            retired_ids.discard(cells[0])

    header_i = next((i for i, line in enumerate(lines)
                     if line.startswith("|----")), None)
    if header_i is None:
        body = "".join(lines).rstrip("\n") + "\n\n" + INDEX_HEADER
        lines = body.splitlines(keepends=True)
        header_i = len(lines) - 1

    # A retired predecessor with no row at all: insert one, because the
    # publish MAINTAINS the index (postcondition (d)) and a missing row is a
    # CHK-OBS-BIJECTION finding this batch would otherwise leave behind.
    for entry in plan:
        if entry["verdict"] == "ratify" and entry["retires"]:
            oid = _id_of_path(entry["retires"])
            if oid in retired_ids:
                new_rows.append(_index_row(oid, "retired", entry["domain"],
                                           entry["evidence"],
                                           entry["anchor_id"]))
    lines[header_i + 1:header_i + 1] = sorted(new_rows)

    text = "".join(lines)
    count = sum(1 for line in text.splitlines()
                if line.startswith("| OBS-") or
                (line.startswith("| ") and "OBS-" in line.split("|")[1]))
    text = _sub_first(text, r"^## Records \(\d+\)$", f"## Records ({count})")
    text = _sub_first(text, r"^_Last updated: .*_$",
                      f"_Last updated: {ctx['date']}_")
    return text


def _id_of_path(rel: str) -> str:
    """`observations/OBS-0001-slug.md` -> `OBS-0001`, honouring §14.3's
    optional artifact prefix."""
    import re
    stem = Path(rel).stem
    m = re.match(r"^((?:[A-Z][A-Z0-9]{1,9}-)?OBS-\d{4})", stem)
    return m.group(1) if m else stem


def _sub_first(text: str, pattern: str, replacement: str) -> str:
    import re
    return re.sub(pattern, replacement.replace("\\", "\\\\"), text, count=1,
                  flags=re.M)


# ── log + journal (textual, absence-conditional) ───────────────────────────

def _log_subject(batch_id: str) -> str:
    return f"survey batch {batch_id}"


# ── the two recorded dates every surface is stamped and checked against ─────
#
# Neither writer nor checker below reads `ctx["date"]` (today, or `--date`).
# Both read the receipt's OWN recorded dates, because every one of these
# surfaces may be written, re-checked or resumed on a later day than the one
# that produced it. Comparing a recorded surface against today makes a healthy
# re-run of a published batch look like a hand-edit — cell 16 refusing what
# cell 15 says is a reported no-op — and it makes `--dry-run` unusable, since
# `run` calls `check_surfaces` before it decides to write nothing.

def _log_date(receipt: dict) -> str:
    """The log op's date: the batch's COMPLETION. Cell 12 runs strictly after
    the commit point, so `completed` is always set by the time this is read."""
    return str(receipt.get("completed") or receipt["signed"])


def _journal_date(receipt: dict) -> str:
    """The journal hook's date: the batch's SIGNING. It fixes both the stamp
    and the `journal/YYYY-MM.md` the hook lives in, and `survey_sheet.
    _journal_has_batch` reads that same key — so the hook's location depends on
    a recorded fact rather than on the day the resume happened."""
    return str(receipt["signed"])


def write_log_entry(ctx: dict, receipt: dict) -> str:
    """Prepend the `observation` log op under the §6 subject grammar
    `survey batch <batch-id>`, with the sorted record ids in the body.

    Reimplemented rather than imported: `signoff-backfill._write_log_entry` is
    module private and hardcodes the `backfill` op. The duplication (this
    writer and the journal writer below, ~50 lines together) is recorded as an
    `iterate` candidate; hoisting a signed write path shared with another
    sign-off is a refactor outside ADR-0098's scope."""
    log_path = ctx["tree"] / "log.md"
    text = log_path.read_text(encoding="utf-8") if log_path.is_file() else ""
    subject = _log_subject(ctx["batch_id"])
    ids = list(receipt["records"])
    stamp = _log_date(receipt)
    for entry in sp._log_entries(text):
        if entry["op"] != "observation" or entry["subject"] != subject:
            continue
        if entry["date"] == stamp and all(i in entry["body"] for i in ids):
            return "present"
        raise Contradiction([
            f"log.md already carries an `observation | {subject}` entry that "
            "does not match this batch's date and record set — the log op is "
            "written by the sign-off, never by hand"])
    body = (f"## [{stamp}] observation | {subject}\n\n"
            f"Batch sign-off published {len(ids)} record(s).\n"
            f"Records: {' '.join(ids) if ids else '(none)'}\n\n")
    lines = text.splitlines(keepends=True)
    first = next((i for i, line in enumerate(lines) if line.startswith("## [")),
                 None)
    if first is None:
        head = text if (text.endswith("\n") or not text) else text + "\n"
        sep = "" if head.endswith("\n\n") or not head else "\n"
        ss.atomic_write_text(log_path, head + sep + body,
                             contained_under=ctx["tree"])
    else:
        ss.atomic_write_text(
            log_path, "".join(lines[:first]) + body + "".join(lines[first:]),
            contained_under=ctx["tree"])
    return "written"


def write_journal_hook(ctx: dict, receipt: dict) -> str:
    """Append the review-category journal hook for this batch.

    The month file and the date stamp both come from the receipt's `signed`
    date, never from today: `survey_sheet._journal_has_batch` reads the hook
    back out of `journal/<signed[:7]>.md`, and a writer keyed on today puts it
    in a different file the moment a resume crosses a month boundary — the
    reader then never sees it and the batch never leaves S7.

    The HH:MM stamp is the wall-clock time of the write even when the entry is
    back-dated: the join is by day, so the stamp is cosmetic — the same
    decision `signoff-backfill._write_journal_hook` records."""
    journal_dir = ctx["tree"] / "journal"
    if journal_dir.is_symlink():
        raise ss.SurveySheetError([
            f"refusing to write the journal hook: {redact(journal_dir, quoted=False)} is a symlink"])
    root_resolved = ctx["tree"].resolve()
    if journal_dir.exists():
        resolved = journal_dir.resolve()
        if resolved != root_resolved and root_resolved not in resolved.parents:
            raise ss.SurveySheetError([
                f"refusing to write the journal hook: {redact(journal_dir, quoted=False)} resolves "
                f"to {redact(resolved, quoted=False)}, outside the tree"])
    subject = f"survey sign-off {ctx['batch_id']}"
    stamp_date = _journal_date(receipt)
    path = journal_dir / f"{stamp_date[:7]}.md"
    text = (path.read_text(encoding="utf-8") if path.is_file()
            else f"# Journal — {stamp_date[:7]}\n")
    ids = list(receipt["records"])
    for hook in sp._journal_entries(text):
        if hook["category"] != "review" or hook["subject"] != subject:
            continue
        if hook["date"] == stamp_date and all(i in hook["block"] for i in ids):
            return "present"
        raise Contradiction([
            f"the journal already carries a `review | {subject}` hook that "
            "does not match this batch's date and record set — the hook is "
            "written by the sign-off, never by hand"])
    journal_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%H:%M")
    entry = (f"## [{stamp_date} {stamp}] review | {subject}\n\n"
             f"Batch {ctx['batch_id']} signed off: {len(ids)} record(s) "
             "published.\n"
             f"Records: {' '.join(ids) if ids else '(none)'}\n")
    if not text.endswith("\n"):
        text += "\n"
    ss.atomic_write_text(path, text + "\n" + entry, contained_under=ctx["tree"])
    return "written"


# ── the cells ──────────────────────────────────────────────────────────────

def _assert_same_device(a: Path, b: Path) -> None:
    """`os.replace` is atomic only within one filesystem. A cross-device move
    degrades to copy-then-unlink, which is not atomic and reopens the crash
    window completion-precedes-visibility exists to close — so it is refused
    rather than emulated."""
    if os.stat(a).st_dev != os.stat(b).st_dev:
        raise ss.SurveySheetError([
            f"refusing to promote: {redact(a, quoted=False)} and {redact(b, quoted=False)} are on different filesystems, "
            "so os.replace would degrade to a non-atomic copy"])


def _cell_1_bind(ctx: dict, sheet: dict, plan: list[dict]) -> list[str]:
    paths = ctx["paths"]
    paths["dir"].mkdir(parents=True, exist_ok=True)
    if not paths["sheet"].is_file():
        ss.atomic_write_text(paths["sheet"],
                             paths["live"].read_text(encoding="utf-8"),
                             contained_under=ctx["tree"])
    receipt = ss.new_receipt(sheet, _receipt_rows(plan), signed=ctx["date"],
                             sheet_ref=_sheet_ref(ctx))
    ss.write_receipt(paths["receipt"], receipt, contained_under=ctx["tree"])
    if paths["live"].is_file():
        paths["live"].unlink()
    return [f"bound {redact(ctx['batch_id'], quoted=False)} by digest {redact(receipt['digest'][:12], quoted=False)}"]


def _cell_3_allocate(ctx: dict, plan: list[dict]) -> list[str]:
    receipt = _receipt(ctx)
    manifest = sp.read_manifest(ctx["root"])
    number = ss.read_counter(manifest, "observation", "next_number", 1)
    ratify = [e for e in plan if e["verdict"] == "ratify"]
    allocated = {}
    for i, entry in enumerate(ratify):
        rid = f"OBS-{number + i:04d}"
        allocated[entry["anchor_id"]] = (
            rid, f"{ctx['obs'].name}/{rid}-{entry['slug']}.md", entry["retires"])
    # The counter is durable BEFORE any id appears in the receipt.
    # Over-allocation is safe; reuse is not.
    ss.write_counter(ctx["tree"] / "manifest.yml", "observation", "next_number",
                     number + len(ratify), contained_under=ctx["tree"])
    for row in receipt["rows"]:
        got = allocated.get(row["anchor_id"])
        if got:
            row["record_id"], row["record_path"], row["retires"] = got
    ss.write_receipt(ctx["paths"]["receipt"], receipt,
                     contained_under=ctx["tree"])
    return [f"allocated {len(ratify)} record id(s) from observation.next_number"]


def _cell_6_stage(ctx: dict, plan: list[dict]) -> list[str]:
    receipt = _receipt(ctx)
    staged = ctx["paths"]["staged"]
    staged.mkdir(parents=True, exist_ok=True)
    by_anchor = {r["anchor_id"]: r for r in receipt["rows"]}
    wrote = []
    for entry in plan:
        if entry["verdict"] != "ratify":
            continue
        row = by_anchor[entry["anchor_id"]]
        entry = dict(entry, batch_id=ctx["batch_id"])
        target = staged / Path(row["record_path"]).name
        ss.atomic_write_text(target,
                             record_body(entry, row["record_id"], ctx["date"]),
                             contained_under=ctx["tree"])
        wrote.append(row["record_id"])
        if row["retires"]:
            # `retires` is TREE-relative (`<obs-dirname>/<file>.md`), so it
            # joins onto the tree. Joining `root / tree.name` instead loses
            # every leading segment of a nested `docs_dir` such as
            # `sub/bionic`, and the read then dies with a FileNotFoundError —
            # an OSError, so it surfaced on the exit-2 environment lane rather
            # than as a finding, with the batch wedged at S2.
            # [SECURITY:S5] The predecessor is READ, and containment of a
            # read is a property of the RESOLVED path. `read_receipt`'s
            # grammar leg refuses a `retires` spelled outside the tree, but a
            # spelling cannot express what a symlink at that path does, and
            # `_assert_contained` only ever guards the WRITE target below —
            # which is contained by construction. `resolve_contained` is the
            # shared remedy, and its own docstring names this case.
            source = oe.resolve_contained(ctx["tree"], row["retires"])
            if source is None or not source.is_file():
                raise ss.SurveySheetError([
                    f"refusing to stage the retirement named by "
                    f"{redact(row['retires'])}: it does not resolve to a file "
                    f"inside {redact(ctx['tree'], quoted=False)} — a predecessor is read from the "
                    "tree, never through a link out of it"])
            ss.atomic_write_text(
                staged / Path(row["retires"]).name,
                retire_body(source.read_text(encoding="utf-8"), ctx["date"]),
                contained_under=ctx["tree"])
    index_path = ctx["obs"] / "index.md"
    existing = (index_path.read_text(encoding="utf-8")
                if index_path.is_file() else "")
    ss.atomic_write_text(staged / "index.md",
                         build_index(ctx, plan, receipt, existing),
                         contained_under=ctx["tree"])
    return [f"staged {len(wrote)} record body/bodies and the index"]


def _cell_7_commit(ctx: dict) -> list[str]:
    receipt = _receipt(ctx)
    receipt["completed"] = ctx["date"]
    receipt["records"] = sorted(r["record_id"] for r in receipt["rows"]
                                if r["record_id"])
    ss.write_receipt(ctx["paths"]["receipt"], receipt,
                     contained_under=ctx["tree"])
    return [f"COMMIT: receipt completed with {len(receipt['records'])} record(s)"]


def _cell_9_promote(ctx: dict) -> list[str]:
    receipt = _receipt(ctx)
    staged = ctx["paths"]["staged"]
    _assert_same_device(staged, ctx["obs"])
    moved = []
    for name in ss.planned_record_names(receipt):
        source = staged / name
        if not source.is_file():
            continue
        os.replace(source, ctx["obs"] / name)
        moved.append(name)
    return [f"promoted {len(moved)} record(s)"]


def _cell_11_index(ctx: dict) -> list[str]:
    staged = ctx["paths"]["staged"]
    index = staged / "index.md"
    if index.is_file():
        _assert_same_device(staged, ctx["obs"])
        os.replace(index, ctx["obs"] / "index.md")
    for leftover in sorted(staged.iterdir()) if staged.is_dir() else []:
        leftover.unlink()
    if staged.is_dir():
        staged.rmdir()
    return ["promoted the concern index and removed staged/"]


def _cell_14_dispose(ctx: dict) -> list[str]:
    receipt = _receipt(ctx)
    state_path = ctx["tree"].joinpath(*ss.STATE_FILE_REL)
    if not state_path.is_file():
        return ["candidate state file absent — nothing to dispose"]
    # `StateFile` raises ValueError for its fail-closed parse AND for its
    # containment refusal. Both are findings in this lane, not environment
    # errors: `main` routes a bare ValueError to exit 2, which callers skip
    # rather than fail on, and a refused write is exactly what an operator
    # must be shown. Re-raised as the exit-1 findings payload.
    try:
        state = StateFile(state_path, contained_under=ctx["tree"])
    except ValueError as exc:
        raise ss.SurveySheetError([str(exc)]) from exc
    wanted = {"ratify": "ratified", "reject": "rejected"}
    disposed = 0
    for row in receipt["rows"]:
        want = wanted.get(row["verdict"])
        if want is None:                 # `defer` writes NOTHING here (§17.5)
            continue
        # Join on the CANDIDATE, never on its anchor. A successor candidate
        # sits on its predecessor's anchor, so an anchor join rewrote the
        # predecessor's disposition too: rejecting a successor stamped
        # `rejected` over a row whose published record says `ratified`, and
        # `upsert_observed` never re-emits a rejected row, so the anchor was
        # suppressed from every later mine and `prune` was free to drop it.
        cid = str(row.get("candidate_id") or row["anchor_id"])
        if cid in state.rows and state.rows[cid].get("state") != want:
            state.dispose(cid, want)
            disposed += 1
    if disposed:
        try:
            state.save()
        except ValueError as exc:
            raise ss.SurveySheetError([str(exc)]) from exc
    return [f"disposed {disposed} candidate row(s)"]


def check_surfaces(ctx: dict) -> None:
    """Cell 16: a corroborating surface present but CONTRADICTING the receipt
    refuses, and is never overwritten.

    This runs ahead of the state dispatch rather than inside the two writers,
    because `batch_state` is structural — it reads a log op for this batch as
    "the log op landed" and moves the state past the writer that would have
    noticed. A planted or hand-edited entry would otherwise be adopted
    silently, which is the one thing cell 16 forbids."""
    if not ctx["paths"]["receipt"].is_file():
        return
    receipt = _receipt(ctx)
    if receipt.get("completed") is None:
        return
    ids = list(receipt["records"])
    log_path = ctx["tree"] / "log.md"
    subject = _log_subject(ctx["batch_id"])
    text = log_path.read_text(encoding="utf-8") if log_path.is_file() else ""
    for entry in sp._log_entries(text):
        if entry["op"] == "observation" and entry["subject"] == subject:
            if (entry["date"] != _log_date(receipt)
                    or not all(i in entry["body"] for i in ids)):
                raise Contradiction([
                    f"log.md carries an `observation | {subject}` entry that "
                    "contradicts the receipt's date or record set — a "
                    "sign-off refuses rather than overwriting it"])
    hook_subject = f"survey sign-off {ctx['batch_id']}"
    hook_date = _journal_date(receipt)
    hook_path = ctx["tree"] / "journal" / f"{hook_date[:7]}.md"
    hook_text = (hook_path.read_text(encoding="utf-8")
                 if hook_path.is_file() else "")
    for hook in sp._journal_entries(hook_text):
        if hook["category"] == "review" and hook["subject"] == hook_subject:
            if (hook["date"] != hook_date
                    or not all(i in hook["block"] for i in ids)):
                raise Contradiction([
                    f"the journal carries a `review | {hook_subject}` hook "
                    "that contradicts the receipt's date or record set — a "
                    "sign-off refuses rather than overwriting it"])


# The §17.5 cell each pre-S9 state's one write belongs to, and the surface
# that write lands on. Used only to name the cell that refused to advance.
CELL_OF_STATE = {"S0": 1, "S1": 3, "S2": 6, "S3": 7, "S4": 9,
                 "S5": 11, "S6": 12, "S7": 13, "S8": 14}
CELL_SURFACE = {
    "S0": "the archived sheet.yml and the bound receipt.yml",
    "S1": "the manifest counter and the receipt's record ids",
    "S2": "the staged/ record bodies and index.md",
    "S3": "the receipt's `completed` field (the commit point)",
    "S4": "the promoted record files under the observations concern",
    "S5": "the concern index.md and the removal of staged/",
    "S6": "the `observation | survey batch <id>` op in log.md",
    "S7": "the `review | survey sign-off <id>` hook in journal/YYYY-MM.md",
    "S8": "the candidate state file's dispositions",
}


def advance(ctx: dict) -> tuple[str, list[str]]:
    """Apply the ONE write the current cell names, and return the new state.

    The whole state machine is this function. Every caller — the CLI loop and
    the crash test alike — drives it, so there is no second code path that
    could disagree about what a state's write is."""
    paths = ctx["paths"]
    state = ss.batch_state(paths["receipt"], ctx["obs"])
    check_surfaces(ctx)
    sheet, plan = build_plan(ctx)
    if paths["receipt"].is_file():
        receipt = _receipt(ctx)
        if receipt["digest"] != ss.sheet_digest(sheet):
            raise ss.SurveySheetError([
                f"batch {redact(ctx['batch_id'], quoted=False)}: the sheet no longer matches the "
                f"digest {redact(receipt['digest'][:12], quoted=False)}… the receipt records — a "
                "changed sheet refuses and writes nothing"])
    if state == "S0":
        return "S1", _cell_1_bind(ctx, sheet, plan)
    if state == "S1":
        return "S2", _cell_3_allocate(ctx, plan)
    if state == "S2":
        return "S3", _cell_6_stage(ctx, plan)
    if state == "S3":
        return "S4", _cell_7_commit(ctx)
    if state == "S4":
        return "S5", _cell_9_promote(ctx)
    if state == "S5":
        return "S6", _cell_11_index(ctx)
    if state == "S6":
        return "S7", [f"log op: {write_log_entry(ctx, _receipt(ctx))}"]
    if state == "S7":
        return "S8", [f"journal hook: {write_journal_hook(ctx, _receipt(ctx))}"]
    if state == "S8":
        return "S9", _cell_14_dispose(ctx)
    return "S9", []


def run(ctx: dict, *, dry_run: bool) -> tuple[int, dict]:
    check_surfaces(ctx)
    sheet, plan = build_plan(ctx)
    receipt = _receipt(ctx) if ctx["paths"]["receipt"].is_file() else None
    lines = rendering(ctx, plan, receipt)
    if dry_run:
        state = ss.batch_state(ctx["paths"]["receipt"], ctx["obs"])
        return 0, {"batch_id": ctx["batch_id"], "state": state,
                   "rendering": lines, "writes": [], "records": [],
                   "dry_run": True}
    writes: list[str] = []
    seen: list[str] = []
    for _ in range(len(ss.STATES) + 1):
        state = ss.batch_state(ctx["paths"]["receipt"], ctx["obs"])
        if state == "S9":
            break
        seen.append(state)
        _next, wrote = advance(ctx)
        writes.extend(wrote)
        after = ss.batch_state(ctx["paths"]["receipt"], ctx["obs"])
        if after == state:
            # The cell ran its one write and the batch did not move. Naming
            # the CELL is the whole point: a wedged batch has no scripted
            # remedy, both projections refuse the entire tree while it sits
            # below S9, and an operator who is told only "did not converge"
            # cannot tell which surface to inspect.
            raise ss.SurveySheetError([
                f"batch {redact(ctx['batch_id'], quoted=False)} is wedged at {state}: cell "
                f"{CELL_OF_STATE.get(state, '?')} ran its write and the batch "
                f"is still at {state}.",
                f"that cell reported: {'; '.join(wrote) or '(no write)'}",
                f"states seen this run: {' -> '.join(seen)} -> {after}",
                f"the surface cell {CELL_OF_STATE.get(state, '?')} writes is "
                f"{CELL_SURFACE.get(state, 'unknown')} — inspect it before "
                "re-running; the sign-off never overwrites a surface it did "
                "not write",
            ])
    else:  # pragma: no cover - the wedge check above fires first
        raise ss.SurveySheetError([
            f"batch {redact(ctx['batch_id'], quoted=False)} did not converge to S9 — the state "
            f"machine advanced through {' -> '.join(seen)} without settling"])
    receipt = _receipt(ctx)
    return 0, {"batch_id": ctx["batch_id"], "state": "S9",
               "rendering": rendering(ctx, plan, receipt), "writes": writes,
               "records": receipt["records"], "dry_run": False}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="signoff-survey",
        description="Sign off one batch review sheet and publish its records.")
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--docs-dir", default=None)
    ap.add_argument("--batch", default=None)
    ap.add_argument("--date", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    today = args.date or datetime.date.today().isoformat()
    try:
        ctx = make_context(Path(args.repo_root), args.docs_dir, args.batch, today)
        code, payload = run(ctx, dry_run=args.dry_run)
    except ss.SurveySheetError as exc:
        print(json.dumps({"findings": exc.problems}, indent=2))
        return 1
    except sp.GovernsValidationError as exc:
        sys.stderr.write(
            f"{TOOL}: {redact(exc, quoted=False, limit=MESSAGE_LIMIT)}\n")
        return 2
    except (BionicConfigError, EnvironmentError, ValueError) as exc:
        sys.stderr.write(
            f"{TOOL}: {redact(exc, quoted=False, limit=MESSAGE_LIMIT)}\n")
        return 2
    print(json.dumps(payload, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
