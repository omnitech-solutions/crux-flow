#!/usr/bin/env python3
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyyaml>=6.0"]
# ///
"""signoff-backfill.py — the owner sign-off write path for a backfill batch.

ADR-0088's clause 11: the single owner write path over the backfill surfaces.
One invocation renders each enumerated receipt's rule and anchor verbatim for
approval (clause 5's two-layer gate — the owner signs ENUMERATED spans, never
a count), validates fail-closed, then writes, with one threaded date:

  1. `adrs/summaries/backfill-reviews.yml` — flips the batch's `signed` date
     (the receipt history is append-only and is never touched);
  2. `<docs_dir>/manifest.yml` — appends the admitted handles to
     `adr.governs_backfilled` (append-only per handle; a tombstoned handle is
     retained) and sets `adr.governs_backfill_complete: true` IFF the
     simulated post-sign arithmetic has zero pending (absent otherwise,
     never false);
  3. `<docs_dir>/log.md` — prepends the `backfill` log op
     `## [<date>] backfill | <batch-id>` carrying `ADRs: <sorted ids>`;
  4. `<docs_dir>/journal/<YYYY-MM>.md` — appends the journal hook
     `## [<date> HH:MM] review | backfill sign-off <batch-id>` naming the
     batch id and its ADR ids.

Post-write it runs the real coverage gate (exit 0 required) and re-derives
the summaries projection (flipping `signed` moves `backfill_reviews_sha256` —
the re-derive is inside the write set), then re-checks the drift gate.

Pre-sign validation is fail-closed (exit 1, `{"findings": [...]}` on stdout):
the batch exists and is unsigned; every enumerated receipt's recorded digest
equals the live digest (drift-since-review voids the verdict); the recorded
anchor space-fold-equals the live one; the admission legs hold (an already-
ledgered enumerated handle must be a tombstone or a correction/supplementary
receipt on a ledgered ADR; a tombstone's digest equals the chain's prior
signed receipt); no-rule ADRs are cohort members with no live governs
entries; and the live tree's backfill contract is otherwise clean — never
sign onto a red tree. An inert tree (no cohort key) is refused.

Re-runs are idempotent: a signed, fully corroborated batch is a reported
no-op (exit 0); a batch left half-written by a crash is completed
absence-conditionally (each surface is written only if missing). A
contradiction — a surface present but different from what this batch's
recorded state implies — is refused, never overwritten.

Usage:
  signoff-backfill.py --batch <id> [--date YYYY-MM-DD] [--dry-run]
                      [--repo-root DIR]

Exit: 0 signed / completed / no-op · 1 findings (JSON on stdout) ·
2 environment/crash (stderr). `--dry-run` prints the rendering plus the
validation and marker report and writes nothing — on a refusing tree the
rendering still prints FIRST (the spot-audit surface), then the findings,
then exit 1.

Two deliberate carve-outs to "exit 1 == a findings JSON alone on stdout",
both because a human-facing rendering shares stdout with the machine payload:
  - `--dry-run` with pre-write findings prints the rendering, THEN the
    `{"findings": [...]}` JSON — spot-audit-first, as above.
  - A POST-write gate failure (the coverage / summaries re-check, after the write
    set has already landed) returns non-zero with, on STDOUT, the sign-off's
    human-readable output followed by the failing sub-gate's OWN output — e.g.
    check-governs-coverage's `{"uncovered": [...]}` / `{"backfill_errors": ...}`
    JSON, under THAT gate's schema, not this script's `{"findings": [...]}` shape
    — plus a `post-gate FAILED: ...` line. STDERR carries only a generic
    "the write set landed but a post-write gate is red — remediate" pointer, not
    the machine payload. The write succeeded; this exit reports a separate
    post-condition to remediate before the next batch, not a refusal of this
    sign. So a caller cannot treat every non-zero exit as this script's findings
    JSON on stdout: only the pre-write refusal path (exit 1, before any write)
    emits `{"findings": [...]}`; the post-write lane emits a different gate's JSON.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import importlib.util
import io
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import summaries_projection as sp  # noqa: E402

_SCRIPTS = Path(__file__).resolve().parent


class SurfaceContradiction(Exception):
    """A corroborating surface is present but contradicts the batch's
    recorded state. Raised only as defense-in-depth — the validation phase
    detects contradictions before any write; a raise mid-write means the
    pre-flight missed one."""

    def __init__(self, finding: dict):
        self.finding = finding
        super().__init__(finding["problem"])


# ── findings ─────────────────────────────────────────────────────────────────

def _finding(problem: str, handle=None, adr=None) -> dict:
    f: dict = {"problem": problem}
    if handle is not None:
        f["handle"] = handle
    if adr is not None:
        f["adr"] = adr
    return f


# ── batch views ──────────────────────────────────────────────────────────────

def _batch_receipts(reviews: dict, bid: str) -> list[dict]:
    """The batch's receipt set, in document order."""
    return [r for r in reviews["receipts"] if r["batch"] == bid]


def _batch_no_rule(reviews: dict, bid: str) -> list[dict]:
    return [n for n in reviews["no_rule"] if n["batch"] == bid]


def _stats(receipts: list[dict], no_rule: list[dict]) -> str:
    p = sum(1 for r in receipts if r["verdict"] == "pass")
    f = sum(1 for r in receipts if r["verdict"] == "fail")
    rm = sum(1 for r in receipts if r["verdict"] == "removed")
    return (f"{len(receipts)} receipts ({p} pass, {f} fail, {rm} removed); "
            f"{len(no_rule)} no-rule ADRs")


def _valid_date(text: str) -> bool:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text or ""):
        return False
    try:
        datetime.date.fromisoformat(text)
        return True
    except ValueError:
        return False


# ── validation ───────────────────────────────────────────────────────────────

def _validate(root: Path, adrs: Path, manifest: dict, reviews: dict,
              records: list[dict], batch: dict, bid: str, mode: str) -> tuple[list[dict], dict]:
    """Every pre-sign check, all fail-closed. Returns (findings, ctx); ctx
    carries the computed views the renderer and the write planner reuse."""
    findings: list[dict] = []
    adr_cfg = manifest.get("adr") or {}
    raw_cohort = adr_cfg.get("governs_backfill_cohort")
    if raw_cohort is not None and not isinstance(raw_cohort, list):
        findings.append(_finding(
            "adr.governs_backfill_cohort must be a list of ADR ids"))
    cohort = ([str(x) for x in raw_cohort] if isinstance(raw_cohort, list)
              else [])
    raw_ledger = adr_cfg.get("governs_backfilled") or {}
    ledger: dict[str, list[str]] = {
        str(k): [str(h) for h in v] for k, v in raw_ledger.items()
        if isinstance(v, list)} if isinstance(raw_ledger, dict) else {}

    if adr_cfg.get("governs_backfill_complete") not in (None, True):
        findings.append(_finding(
            "adr.governs_backfill_complete must be absent or true — the "
            "marker is terminal and binary, never false"))

    receipts = _batch_receipts(reviews, bid)
    no_rule = _batch_no_rule(reviews, bid)
    receipt_by_handle: dict[str, list[dict]] = {}
    for r in receipts:
        receipt_by_handle.setdefault(r["handle"], []).append(r)

    # The enumeration and the receipt set cover each other exactly — signing
    # a batch whose two sets disagree produces the shape errors the reader
    # enforces on signed batches.
    for h in batch["handles"]:
        n = len(receipt_by_handle.get(h, []))
        if n == 0:
            findings.append(_finding(
                f"batch {bid!r} enumerates handle {h} but records no receipt "
                "for it in this batch", handle=h))
        elif n > 1:
            findings.append(_finding(
                f"handle {h} has {n} receipts in batch {bid!r} — exactly one "
                "per enumerated handle", handle=h))
    for r in receipts:
        if r["handle"] not in batch["handles"]:
            findings.append(_finding(
                f"receipt for {r['handle']} is not enumerated by batch "
                f"{bid!r}", handle=r["handle"]))
    no_rule_by_adr = {n["adr"]: n for n in no_rule}
    for a in batch["no_rule"]:
        if a not in no_rule_by_adr:
            findings.append(_finding(
                f"batch {bid!r} enumerates no-rule ADR {a} but records no "
                "no_rule entry for it in this batch", adr=a))
    for n in no_rule:
        if n["adr"] not in batch["no_rule"]:
            findings.append(_finding(
                f"no_rule entry for {n['adr']} is not enumerated by batch "
                f"{bid!r}", adr=n["adr"]))

    records_by_handle = {r["handle"]: r for r in records}
    records_by_adr: dict[str, list[dict]] = {}
    for r in records:
        records_by_adr.setdefault(r["source_adr"], []).append(r)
    removed = sp.removed_handles(reviews)

    admissions: dict[str, list[str]] = {}
    kinds: dict[str, str] = {}
    for r in receipts:
        h = r["handle"]
        adr = h.split("/")[0]
        if r["verdict"] == "removed":
            # A removal retains the ledger row, drops the entry, and carries
            # the last live digest — the chain's prior signed receipt, never
            # recomputed from live frontmatter.
            if h not in ledger.get(adr, []):
                findings.append(_finding(
                    f"tombstone target {h} is absent from the "
                    "adr.governs_backfilled ledger — a removal retains an "
                    "admitted handle, it cannot remove a never-admitted one",
                    handle=h))
            if h in records_by_handle:
                findings.append(_finding(
                    f"tombstone target {h} is still live in {adr}'s "
                    "frontmatter — the removal correction drops the entry "
                    "before the tombstone signs", handle=h))
            prior = [x for x in reviews["receipts"]
                     if x["handle"] == h and x["batch"] != bid
                     and sp.receipt_signed(x, reviews["batches"])]
            if not prior:
                findings.append(_finding(
                    f"tombstone for {h} has no prior signed receipt in the "
                    "chain — a removal carries the last live digest, which "
                    "only a prior signed receipt supplies", handle=h))
            elif prior[-1]["digest"] != r["digest"]:
                findings.append(_finding(
                    f"tombstone for {h} does not carry the last live digest "
                    "— its digest differs from the chain's prior signed "
                    "receipt", handle=h))
            kinds[h] = "removal"
            continue
        # pass / fail
        rec = records_by_handle.get(h)
        if rec is None:
            findings.append(_finding(
                f"receipt handle {h} has no live entry in frontmatter — "
                "the digest binds reviewed content, which must exist",
                handle=h))
            kinds[h] = "unbindable"
            continue
        live_digest = sp.content_digest(rec)
        if r["digest"] != live_digest:
            findings.append(_finding(
                f"drift-since-review voids the verdict: receipt digest "
                f"{r['digest']} != live digest {live_digest} — re-review the "
                "current content in a correction batch", handle=h))
        if sp._folded_anchor(r["anchor"]) != sp._folded_anchor(rec["anchor"]):
            findings.append(_finding(
                f"the recorded anchor does not space-fold-equal the live "
                f"entry's anchor for {h} — the receipt must record the "
                "anchor rendered at sign-off", handle=h))
        if h in removed:
            findings.append(_finding(
                f"re-admission laundering: {h} is ledgered and its current "
                "receipt is a tombstone — a removed handle is never "
                "re-admitted", handle=h))
            kinds[h] = "laundered"
        elif h in ledger.get(adr, []):
            kinds[h] = "correction"  # a new receipt on an admitted handle
        else:
            kinds[h] = "supplementary" if ledger.get(adr) else "initial"
            admissions.setdefault(adr, []).append(h)

    for n in no_rule:
        a = n["adr"]
        if a not in cohort:
            findings.append(_finding(
                f"no-rule ADR {a} is not a member of the frozen cohort", adr=a))
        if records_by_adr.get(a):
            findings.append(_finding(
                f"no-rule laundering: ADR {a} carries live governs entries — "
                "the three legible states (exempt, not-yet-backfilled, "
                "no-rule) never collapse", adr=a))

    # Never sign onto a red tree: the live contract must be clean apart from
    # the admissions/removals THIS signing resolves (an extracted handle sits
    # unledgered until the sign-off records it; a removed entry awaits its
    # tombstone's signature). The completion-marker problem is handleless
    # (the projection emits it without source_adr/handle), so the skip above
    # can never match it; a batch resolves it when the SIMULATED post-sign
    # arithmetic reaches zero pending — without this skip, a tree whose
    # marker was set before a removal deadlocks the correction lane. Any
    # other handleless problem (shape errors) still blocks. On the idempotent
    # path the missing corroboration IS the red the completion fills, so the
    # check runs on the sign path only.
    if mode == "sign":
        problems, _ = sp.backfill_problems(root, adrs, manifest)
        admitted_now = {h for hs in admissions.values() for h in hs}
        removing_now = {r["handle"] for r in receipts if r["verdict"] == "removed"}
        ledger_sim = {k: list(v) for k, v in ledger.items()}
        for adr, handles in admissions.items():
            row = ledger_sim.setdefault(adr, [])
            for h in handles:
                if h not in row:
                    row.append(h)
        reviews_sim = {**reviews, "batches": {**reviews["batches"]}}
        reviews_sim["batches"][bid] = {**batch, "signed": "9999-12-31"}
        disp = _simulated_dispositions(reviews_sim, ledger_sim,
                                       records_by_adr, cohort)
        marker_resolved = not any(d == "pending" for d in disp.values())
        unrelated = []
        for p in problems:
            h = p.get("handle")
            if h in admitted_now and "ledger" in p["problem"]:
                continue  # this signing records the admission
            if h in removing_now and "tombstone" in p["problem"]:
                continue  # this signing signs the tombstone
            if h is None and "governs_backfill_complete" in p["problem"]:
                if marker_resolved:
                    continue  # this signing re-stands the marker at zero pending
            unrelated.append(p)
        if unrelated:
            detail = "; ".join(
                f"{p.get('source_adr') or '-'} {p.get('handle') or '-'}: "
                f"{p['problem']}" for p in unrelated[:5])
            findings.append(_finding(
                f"red tree: {len(unrelated)} pre-existing contract "
                f"problem(s) unrelated to batch {bid!r} — never sign onto a "
                f"red tree ({detail})"))

    ctx = {
        "ledger": ledger, "records_by_handle": records_by_handle,
        "records_by_adr": records_by_adr, "receipts": receipts,
        "no_rule": no_rule, "receipt_by_handle": receipt_by_handle,
        "no_rule_by_adr": no_rule_by_adr, "admissions": admissions,
        "kinds": kinds, "cohort": cohort,
    }
    return findings, ctx


# ── simulated marker arithmetic ──────────────────────────────────────────────

def _simulated_dispositions(reviews_sim: dict, ledger_sim: dict,
                            records_by_adr: dict, cohort: list[str]) -> dict[str, str]:
    """Post-sign per-ADR dispositions. Mirrors summaries_projection's
    backfill_problems clause-(iii) logic exactly, over the SIMULATED state
    (this batch signed, this batch's admissions ledgered): a signed no-rule
    receipt with no live entries is `no_rule`; any tombstone among the ADR's
    admitted handles leaves it `pending`; else fully ledgered and resolving
    is `backfilled`. The laundering case (no-rule receipt + live entries) is
    refused by validation before this runs."""
    removed = sp.removed_handles(reviews_sim)
    out: dict[str, str] = {}
    for aid in cohort:
        entries = records_by_adr.get(aid, [])
        if sp.no_rule_signed(aid, reviews_sim) and not entries:
            out[aid] = "no_rule"
            continue
        ledgered = set(ledger_sim.get(aid, []))
        any_tombstone = any(h in removed for h in ledgered)
        if (not any_tombstone and entries
                and all(e["handle"] in ledgered for e in entries)
                and all(sp.resolve_entry(e, reviews_sim) for e in entries)):
            out[aid] = "backfilled"
        else:
            out[aid] = "pending"
    return out


def _marker_report(ctx: dict, batch: dict, bid: str, date: str, reviews: dict) -> tuple[dict, dict, dict, bool]:
    """Returns (reviews_sim, ledger_sim, counts, set_marker)."""
    reviews_sim = {**reviews, "batches": {**reviews["batches"]}}
    reviews_sim["batches"][bid] = {**batch, "signed": date}
    ledger_sim = {k: list(v) for k, v in ctx["ledger"].items()}
    for adr, handles in ctx["admissions"].items():
        row = ledger_sim.setdefault(adr, [])
        for h in handles:
            if h not in row:
                row.append(h)
    disp = _simulated_dispositions(reviews_sim, ledger_sim,
                                   ctx["records_by_adr"], ctx["cohort"])
    counts = {
        "backfilled": sum(1 for d in disp.values() if d == "backfilled"),
        "no_rule": sum(1 for d in disp.values() if d == "no_rule"),
        "pending": sum(1 for d in disp.values() if d == "pending"),
    }
    return reviews_sim, ledger_sim, counts, counts["pending"] == 0


# ── the owner rendering (clause 5: enumerated spans, verbatim) ──────────────

def _render(batch: dict, bid: str, mode: str, date: str, ctx: dict,
            counts: dict, set_marker: bool) -> str:
    out = [f"Backfill batch sign-off — {bid}",
           f"mode: {mode} — signing date {date}",
           f"cohort: {len(ctx['cohort'])} ADRs", ""]
    receipts = [ctx["receipt_by_handle"][h][0] for h in batch["handles"]
                if ctx["receipt_by_handle"].get(h)]
    for i, r in enumerate(receipts, 1):
        h = r["handle"]
        out.append(f"Receipt {i}/{len(receipts)} — {h}")
        if r["verdict"] == "removed":
            out.append("  verdict: removed (tombstone)")
            out.append(f"  the entry is dropped from {h.split('/')[0]}'s "
                       "governs block; the ledger retains the handle")
            out.append(f"  digest: recorded {r['digest']} — the last live "
                       "digest (chain prior signed receipt match)")
        else:
            rec = ctx["records_by_handle"].get(h)
            out.append(f"  verdict: {r['verdict']}")
            out.append(f"  admission: {ctx['kinds'].get(h, '?')}")
            if rec is not None:
                out.append(f"  rule (verbatim from {rec['source_adr']} "
                           "frontmatter):")
                out.append(f"    {json.dumps(rec['rule'])}")
                anchor = rec["anchor"]
                if isinstance(anchor, list):
                    out.append(f"  anchor (entry shape: list, {len(anchor)} "
                               "spans):")
                    for j, el in enumerate(anchor, 1):
                        out.append(f"    span {j}/{len(anchor)}: {json.dumps(el)}")
                else:
                    out.append("  anchor (entry shape: string):")
                    out.append(f"    {json.dumps(anchor)}")
                live = sp.content_digest(rec)
                mark = "match" if live == r["digest"] else "MISMATCH"
                out.append(f"  digest: recorded {r['digest']} live {live} {mark}")
        out.append("")
    no_rules = [ctx["no_rule_by_adr"][a] for a in batch["no_rule"]
                if a in ctx["no_rule_by_adr"]]
    for i, n in enumerate(no_rules, 1):
        out.append(f"No-rule ADR {i}/{len(no_rules)} — {n['adr']}")
        out.append("  verdict: confirmed")
        out.append(f"  reason: {json.dumps(n['reason'])}")
        out.append("")
    out.append("marker arithmetic (simulated post-sign): "
               f"{counts['backfilled']} backfilled + {counts['no_rule']} "
               f"no-rule + {counts['pending']} pending = "
               f"{len(ctx['cohort'])} cohort")
    if set_marker:
        out.append("marker: governs_backfill_complete: true WILL be set "
                   f"({counts['pending']} pending)")
    else:
        out.append(f"marker: governs_backfill_complete stays absent "
                   f"({counts['pending']} pending)")
    return "\n".join(out)


# ── the four surfaces + the marker (textual, absence-conditional) ───────────

def _atomic_write_text(path: Path, body: str, *, contained_under: Path) -> None:
    """Atomic UTF-8 text write with no platform newline translation.

    Writes to <path>.tmp then os.replace()s into place. We write bytes
    directly so Python does NOT translate '\\n' → '\\r\\n' on Windows; that
    translation would shift the file's sha256 across platforms.

    [SECURITY:S5] Every write target's RESOLVED parent directory must sit
    under `contained_under` — the validated tree dir. The leaf checks below
    guard the target and its tmp file; this guard covers the INTERMEDIATE
    directories: a symlink at <tree>/adrs or <tree>/adrs/summaries leaves every
    entry under it a real file (no leaf symlink to catch) while steering the
    write outside the repo root. Checked first, before any filesystem
    mutation, on the resolved pair — the same resolve-then-contain shape
    `sp.resolve_tree` applies to the tree dir itself. The containment guard
    is shared with summarize-adrs.py's copy (both thread the validated
    tree dir from `sp.resolve_tree`); the remaining siblings have no
    validated tree dir to thread, so there is nothing to parity-check it
    against. One TOCTOU boundary stays open by design: the containment
    check is path-based and O_NOFOLLOW guards only the final component, so
    an intermediate-directory swap between the check and the create is
    unguarded — reachable only by a writer already inside the tree mid-run,
    which the committed-repo-content threat model excludes.

    [SECURITY:S5] Neither the target nor `<path>.tmp` may be a symlink. The
    tmp path is predictable and a bare write follows a link, so a pre-created
    link turns a sign-off write into a write into someone else's file, while
    `os.replace` moves the tmp PATH and leaves the link standing. Checked
    explicitly for a readable error, then created O_NOFOLLOW|O_EXCL so the
    check is not a TOCTOU window. The leaf symlink/tmp guards keep
    BEHAVIORAL parity with this helper's siblings in extract-code-docs.py,
    validate-catalog.py, and promote-changelog.py — same guards, same
    order, same exception type (OSError) — and are not byte-identical:
    each names its own subject in its messages ("sign-off surface content"
    here). Treat the messages as the only licensed difference IN THOSE
    GUARDS. Change one, change all. `_yaml_min.write_catalog_yaml` carries
    the same guards in the same order but raises its own CatalogYamlError
    (a ValueError) under that writer's single-exception-type contract, so
    it is not in the same-exception-type parity set.
    """
    root_resolved = Path(contained_under).resolve()
    parent_resolved = path.parent.resolve()
    if parent_resolved != root_resolved \
            and root_resolved not in parent_resolved.parents:
        raise OSError(
            f"refusing to write {path}: the parent directory resolves to "
            f"{parent_resolved}, which is not contained under the validated "
            f"tree dir {root_resolved} — a symlinked intermediate directory "
            "would put sign-off surface content outside the tree"
        )
    tmp = path.with_suffix(path.suffix + ".tmp")
    for label, candidate in (("target", path), ("temporary file", tmp)):
        if candidate.is_symlink():
            raise OSError(
                f"refusing to write {path}: the {label} {candidate} is a symlink — "
                "writing through it would put sign-off surface content in the "
                "link's target"
            )
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    except FileExistsError as exc:
        raise OSError(
            f"refusing to write {path}: the temporary file {tmp} already exists; "
            "remove it after checking what created it"
        ) from exc
    except OSError as exc:  # ELOOP from O_NOFOLLOW, or an unwritable directory
        raise OSError(f"refusing to write {path}: cannot create {tmp} ({exc})") from exc
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(body.encode("utf-8"))
        os.replace(tmp, path)
    except Exception:
        # Clean up partial tmp before re-raising (SC-1).
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise


_TOP_KEY_RE = re.compile(r"^[A-Za-z_]")
_BATCH_ITEM_RE = re.compile(r"^(\s*)-\s+id:\s*(\S.*)$")
_SIGNED_LINE_RE = re.compile(r"^(\s*)signed\s*:")


def _plain_scalar(text: str) -> str:
    """The scalar an authored YAML line carries: strip surrounding quotes and
    any trailing comment (a `#` preceded by whitespace starts one; inside
    quotes it is content)."""
    v = text.strip()
    if v[:1] in ("'", '"'):
        end = v.find(v[0], 1)
        if end != -1:
            return v[1:end]
        return v  # an unterminated quote is the YAML reader's refusal, not ours
    return re.split(r"\s+#", v, maxsplit=1)[0].strip()


def _scan_flip_target(lines: list[str], bid: str) -> int:
    """The line index of the batch's `- id:` item in the reviews manifest's
    batches block. Raises SurfaceContradiction when the manifest parses but
    carries no such item line (a hand-edited shape, e.g. a flow-style block).
    Shared by the planner (which catches the raise into a pre-write finding)
    and the writer (which keeps it as the mid-write backstop)."""
    in_batches = False
    for i, line in enumerate(lines):
        stripped = line.rstrip("\n")
        if stripped == "batches:" or stripped.startswith("batches: "):
            in_batches = True
            continue
        if in_batches and _TOP_KEY_RE.match(stripped):
            break
        if in_batches:
            m = _BATCH_ITEM_RE.match(stripped)
            if m and _plain_scalar(m.group(2)) == bid:
                return i
    raise SurfaceContradiction(_finding(
        f"batch {bid!r} parses in the manifest but has no `- id:` item "
        "in the batches block — hand-edited shape"))


def _write_reviews_flip(path: Path, bid: str, date: str, *,
                        contained_under: Path) -> bool:
    """Flip the batch's `signed` to the threaded date. Textual and targeted:
    the receipt history is append-only and its bytes are never touched.
    Returns True when the file changed."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    target = _scan_flip_target(lines, bid)
    j = target + 1
    while j < len(lines):
        stripped = lines[j].rstrip("\n")
        if _TOP_KEY_RE.match(stripped) or _BATCH_ITEM_RE.match(stripped):
            break
        m = _SIGNED_LINE_RE.match(stripped)
        if m:
            new = f"{m.group(1)}signed: {date}\n"
            if lines[j] == new:
                return False
            lines[j] = new
            _atomic_write_text(path, "".join(lines), contained_under=contained_under)
            return True
        j += 1
    # `signed` absent: insert as the item's first child key.
    item_indent = re.match(r"^(\s*)-", lines[target].rstrip("\n")).group(1)
    lines.insert(target + 1, f"{item_indent}  signed: {date}\n")
    _atomic_write_text(path, "".join(lines), contained_under=contained_under)
    return True


_LEDGER_ROW_RE = re.compile(r"^    (ADR-\d{4}):\s*\[(.*)\]\s*$")
_LEDGER_KEY_RE = re.compile(r"^  governs_backfilled:(?:\s*(.*))?$")
_MARKER_KEY_RE = re.compile(r"^  governs_backfill_complete:")
_MARKER_TRUE_RE = re.compile(r"^  governs_backfill_complete:\s*true\s*(?:#.*)?$")
_TWO_SPACE_KEY_RE = re.compile(r"^  \S")


def _scan_ledger(lines: list[str]) -> dict:
    """Parse the manifest's `adr:` block into the ledger's textual shape.

    Raises SurfaceContradiction on any shape this skill did not produce (a
    non-block inline value, a non-flow-list row, a missing `adr:` block) —
    the ledger is written ONLY by this script, so a foreign shape is a
    contradiction, never something to parse around. Shared by the planner
    (which catches the raise into a pre-write finding, so a shape
    contradiction never fires after a write) and the writer (which keeps the
    raise as the mid-write backstop)."""
    adr_i = next((i for i, l in enumerate(lines) if re.match(r"^adr:\s*$", l)),
                 None)
    if adr_i is None:
        raise SurfaceContradiction(_finding(
            "manifest.yml carries no top-level `adr:` block"))
    end = next((i for i in range(adr_i + 1, len(lines))
                if _TOP_KEY_RE.match(lines[i])), len(lines))

    gb_i = next((i for i in range(adr_i + 1, end)
                 if _LEDGER_KEY_RE.match(lines[i])), None)
    rows: dict[str, list[str]] = {}
    row_line: dict[str, int] = {}
    rows_end = None
    inline = None
    if gb_i is not None:
        inline = _LEDGER_KEY_RE.match(lines[gb_i]).group(1)
        if inline not in (None, "", "{}"):
            raise SurfaceContradiction(_finding(
                f"adr.governs_backfilled is not the block mapping this "
                f"skill writes: {lines[gb_i].strip()!r}"))
        j = gb_i + 1
        while j < end:
            m = _LEDGER_ROW_RE.match(lines[j])
            if m:
                rows[m.group(1)] = ([h.strip() for h in m.group(2).split(",")
                                     if h.strip()])
                row_line[m.group(1)] = j
                j += 1
                continue
            if lines[j].strip() == "" or lines[j].lstrip().startswith("#"):
                break
            if _TWO_SPACE_KEY_RE.match(lines[j]) or _TOP_KEY_RE.match(lines[j]):
                break
            raise SurfaceContradiction(_finding(
                f"adr.governs_backfilled row is not the flow list this "
                f"skill writes: {lines[j].strip()!r}"))
        rows_end = j
    return {"adr_i": adr_i, "end": end, "gb_i": gb_i, "inline": inline,
            "rows": rows, "row_line": row_line, "rows_end": rows_end}


def _write_ledger_and_marker(path: Path, appends: dict[str, list[str]],
                             set_marker: bool, *, contained_under: Path) -> list[str]:
    """Append admitted handles to `adr.governs_backfilled` (append-only per
    handle; a tombstoned handle is retained) and set the completion marker
    when the simulated arithmetic holds.

    Two shape repairs are this writer's own, both lossless: the documented
    `governs_backfilled: {}` inline default is rewritten to the block form
    before any row inserts beneath it (rows under the inline `{}` would be
    unparseable YAML no re-run could recover), and a present-but-null
    completion marker (`~`) is rewritten in place — null is absent
    semantics, and the rendering promised the marker. Returns the write
    descriptions (empty = nothing to write)."""
    writes: list[str] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    scan = _scan_ledger(lines)
    adr_i, gb_i, inline = scan["adr_i"], scan["gb_i"], scan["inline"]
    end, rows_end = scan["end"], scan["rows_end"]
    rows, row_line = scan["rows"], scan["row_line"]

    for aid in sorted(appends, key=sp.adr_num):
        new = [h for h in appends[aid] if h not in rows.get(aid, [])]
        if not new:
            continue
        if aid in rows:
            rows[aid] = rows[aid] + new
            lines[row_line[aid]] = f"    {aid}: [{', '.join(rows[aid])}]"
        else:
            rows[aid] = new
            row_line[aid] = -1  # marks "to insert", positioned below
        writes.append(f"ledger: {aid} recorded {', '.join(new)}")

    if any(row_line.get(a) == -1 for a in appends):
        # Insert the new rows in adr_num order among the existing rows.
        new_rows = sorted((a for a in appends if row_line.get(a) == -1),
                          key=sp.adr_num)
        if gb_i is None:
            anchor = end
            block_lines = ["", "  governs_backfilled:"]
        else:
            if inline == "{}":
                lines[gb_i] = "  governs_backfilled:"
            anchor = rows_end
            block_lines = []
        for aid in new_rows:
            text_row = f"    {aid}: [{', '.join(rows[aid])}]"
            if block_lines:
                # No ledger key existed, so no existing rows: the fresh block
                # carries every new row in adr_num order.
                block_lines.append(text_row)
                continue
            num = sp.adr_num(aid)
            pos = None
            for a2, li in sorted(row_line.items(), key=lambda kv: kv[1]):
                if li != -1 and sp.adr_num(a2) > num:
                    pos = li
                    break
            if pos is None:
                # Recompute the anchor per row from the LIVE row_line map:
                # land after the current last placed row. (A captured-once
                # anchor goes stale after the first insert and reverses a
                # multi-row append.)
                placed = [li for a2, li in row_line.items() if li != -1]
                pos = max(placed) + 1 if placed else anchor
            lines.insert(pos, text_row)
            end += 1
            if rows_end is not None:
                rows_end += 1
            for a2 in row_line:
                if row_line[a2] != -1 and row_line[a2] >= pos:
                    row_line[a2] += 1
            row_line[aid] = pos
        if block_lines:
            for k, bl in enumerate(block_lines):
                lines.insert(anchor + k, bl)
            end += len(block_lines)
            if rows_end is not None:
                rows_end += len(block_lines)

    # The marker is present only when it is TRUE: a key carrying `~` (null)
    # is absent-semantics — the rendering promised the marker, so the line
    # is rewritten in place rather than skipped. (`false` never reaches
    # here: validation refuses it.)
    marker_i = next((i for i in range(adr_i + 1, end)
                     if _MARKER_KEY_RE.match(lines[i])), None)
    marker_true = marker_i is not None and bool(
        _MARKER_TRUE_RE.match(lines[marker_i]))
    if set_marker and not marker_true:
        if marker_i is not None:
            lines[marker_i] = "  governs_backfill_complete: true"
        else:
            # Directly after the governs_backfilled block when it exists,
            # else at the end of the adr block.
            insert_at = rows_end if rows_end is not None else end
            lines.insert(insert_at, "  governs_backfill_complete: true")
        writes.append("marker: governs_backfill_complete: true")

    if writes:
        _atomic_write_text(path, "\n".join(lines) + "\n",
                           contained_under=contained_under)
    return writes


def _log_adrs_of(entry: dict) -> list[str] | None:
    m = sp._ADRS_LINE_RE.search(entry["body"])
    if not m:
        return None
    ids = m.group(1).split()
    if not all(re.fullmatch(r"ADR-\d{4}", i) for i in ids):
        return None
    return sorted(ids, key=sp.adr_num)


def _write_log_entry(log_path: Path, bid: str, date: str,
                     derived_ids: list[str], stats: str,
                     allow_present: bool, *, contained_under: Path) -> str:
    """Prepend the `backfill` log op (newest-first file). Returns
    "written" | "present". A pre-existing entry for the batch must match the
    threaded date and the derived ADR set exactly, else contradiction; on the
    sign path ANY pre-existing entry is a hand-authored corroboration and
    contradicts by construction."""
    text = log_path.read_text(encoding="utf-8") if log_path.is_file() else ""
    existing = [e for e in sp._log_entries(text)
                if e["op"] == "backfill" and e["subject"] == bid]
    if existing:
        if allow_present:
            for e in existing:
                if e["date"] == date and _log_adrs_of(e) == derived_ids:
                    return "present"
        raise SurfaceContradiction(_finding(
            f"log.md already carries an entry for batch {bid!r} that does "
            "not match this sign-off's date and ADR set — corroboration is "
            "written by the sign-off, never by hand"))
    entry = (f"## [{date}] backfill | {bid}\n\n"
             f"Batch sign-off: {stats}.\n"
             f"ADRs: {' '.join(derived_ids)}\n\n")
    lines = text.splitlines(keepends=True)
    first = next((i for i, l in enumerate(lines) if l.startswith("## [")), None)
    if first is None:
        new = text if text.endswith("\n") or not text else text + "\n"
        sep = "" if new.endswith("\n\n") or not new else "\n"
        _atomic_write_text(log_path, new + sep + entry,
                           contained_under=contained_under)
    else:
        _atomic_write_text(log_path,
                           "".join(lines[:first]) + entry + "".join(lines[first:]),
                           contained_under=contained_under)
    return "written"


def _write_journal_hook(journal_dir: Path, bid: str, date: str,
                        derived_ids: list[str], stats: str,
                        allow_present: bool, *, contained_under: Path) -> str:
    """Append the journal hook in the signed month (chronological file).
    Returns "written" | "present"; contradiction semantics mirror the log
    writer.

    The HH:MM stamp is the wall-clock time of the write — even when `--date`
    back-dates the entry. That is deliberate: the cross-validation gate
    joins the hook on the DATE, never the time, so the stamp is cosmetic."""
    if journal_dir.is_symlink():
        raise OSError(
            f"refusing to write the journal hook: the journal directory "
            f"{journal_dir} is a symlink — writing through it would put "
            "sign-off surface content outside the tree")
    # Containment runs BEFORE mkdir (SC-1): mkdir is itself a filesystem
    # mutation, so a journal directory escaping the validated tree dir is
    # refused before it is created, not only before the hook file is written
    # (the helper re-checks on the file path as the write-time backstop).
    root_resolved = Path(contained_under).resolve()
    dir_resolved = journal_dir.resolve()
    if dir_resolved != root_resolved \
            and root_resolved not in dir_resolved.parents:
        raise OSError(
            f"refusing to write the journal hook: the journal directory "
            f"{journal_dir} resolves to {dir_resolved}, which is not "
            f"contained under the validated tree dir {root_resolved} — "
            "creating it would put directories outside the tree")
    journal_dir.mkdir(parents=True, exist_ok=True)
    path = journal_dir / f"{date[:7]}.md"
    text = (path.read_text(encoding="utf-8") if path.is_file()
            else f"# Journal — {date[:7]}\n")
    hooks = [e for e in sp._journal_entries(text)
             if e["category"] == "review"
             and e["subject"] == f"backfill sign-off {bid}"]
    if hooks:
        if allow_present:
            for h in hooks:
                if h["date"] == date and all(a in h["block"] for a in derived_ids):
                    return "present"
        raise SurfaceContradiction(_finding(
            f"the journal already carries a hook for batch {bid!r} that does "
            "not match this sign-off's date and ADR set — corroboration is "
            "written by the sign-off, never by hand"))
    stamp = datetime.datetime.now().strftime("%H:%M")
    entry = (f"## [{date} {stamp}] review | backfill sign-off {bid}\n\n"
             f"Owner sign-off recorded batch {bid}: {stats}.\n"
             f"ADRs covered: {' '.join(derived_ids)}.\n")
    if not text.endswith("\n"):
        text += "\n"
    _atomic_write_text(path, text + "\n" + entry, contained_under=contained_under)
    return "written"


# ── write planning (contradiction detection ahead of any mutation) ─────────

def _plan_writes(tree_dir: Path, adrs: Path, manifest: dict,
                 reviews: dict, batch: dict, bid: str, mode: str, date: str,
                 ctx: dict, set_marker: bool) -> tuple[dict, list[dict]]:
    """Decide every write before making one — parse-and-plan, never write.
    Returns (plan, findings); a contradiction is a finding, never a partial
    write. The writers' text-shape parsers run HERE over the live bytes (the
    manifest ledger shape on every path, the reviews flip target on the sign
    path), so a shape contradiction refuses BEFORE the first surface moves
    instead of firing after the reviews flip landed."""
    findings: list[dict] = []
    derived_ids = sp.derived_adr_ids(batch)
    stats = _stats(ctx["receipts"], ctx["no_rule"])
    allow_present = mode == "idempotent"

    try:
        _scan_ledger((tree_dir / "manifest.yml")
                     .read_text(encoding="utf-8").splitlines())
    except SurfaceContradiction as exc:
        findings.append(exc.finding)
    if mode == "sign":
        try:
            _scan_flip_target(sp.reviews_path(adrs)
                              .read_text(encoding="utf-8").splitlines(), bid)
        except SurfaceContradiction as exc:
            findings.append(exc.finding)

    plan: dict = {
        "flip": mode == "sign",
        "appends": {a: [h for h in hs if h not in ctx["ledger"].get(a, [])]
                    for a, hs in ctx["admissions"].items()},
        "set_marker": set_marker
        and (manifest.get("adr") or {}).get("governs_backfill_complete") is not True,
        "log": None, "journal": None,
        "derived_ids": derived_ids, "stats": stats,
    }
    plan["appends"] = {a: hs for a, hs in plan["appends"].items() if hs}

    log_text_path = tree_dir / "log.md"
    log_text = (log_text_path.read_text(encoding="utf-8")
                if log_text_path.is_file() else "")
    existing_log = [e for e in sp._log_entries(log_text)
                    if e["op"] == "backfill" and e["subject"] == bid]
    if existing_log:
        ok = allow_present and any(
            e["date"] == date and _log_adrs_of(e) == derived_ids
            for e in existing_log)
        if ok:
            plan["log"] = "present"
        else:
            findings.append(_finding(
                f"log.md already carries an entry for batch {bid!r} that "
                "does not match this sign-off's date and ADR set"))
    else:
        plan["log"] = "write"

    journal_path = tree_dir / "journal" / f"{date[:7]}.md"
    journal_text = (journal_path.read_text(encoding="utf-8")
                    if journal_path.is_file() else "")
    hooks = [e for e in sp._journal_entries(journal_text)
             if e["category"] == "review"
             and e["subject"] == f"backfill sign-off {bid}"]
    if hooks:
        ok = allow_present and any(
            h["date"] == date and all(a in h["block"] for a in derived_ids)
            for h in hooks)
        if ok:
            plan["journal"] = "present"
        else:
            findings.append(_finding(
                f"the journal already carries a hook for batch {bid!r} that "
                "does not match this sign-off's date and ADR set"))
    else:
        plan["journal"] = "write"

    # Marker-vs-arithmetic consistency on the idempotent path: a set marker
    # with non-zero pending is the gate's clause-(iii) failure — surface it.
    if mode == "idempotent" and not set_marker \
            and (manifest.get("adr") or {}).get("governs_backfill_complete") is True:
        findings.append(_finding(
            "adr.governs_backfill_complete is set but the simulated "
            "arithmetic has pending cohort ADRs — the marker cannot stand"))

    return plan, findings


def _plan_is_noop(plan: dict) -> bool:
    return (not plan["flip"] and not plan["appends"] and not plan["set_marker"]
            and plan["log"] == "present" and plan["journal"] == "present")


# ── post-write gates ─────────────────────────────────────────────────────────

def _load_driver(filename: str):
    spec = importlib.util.spec_from_file_location(
        filename.replace("-", "_").removesuffix(".py"), _SCRIPTS / filename)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _run_driver(module, argv: list[str]) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = module.main(argv)
    return rc, buf.getvalue()


def _post_gates(root: Path) -> tuple[int | None, list[str]]:
    """The write set closes only when the real gates agree: the coverage gate
    (exit 0 required), the summaries re-derive (flipping `signed` moves
    backfill_reviews_sha256, so the re-derive is INSIDE the write set), and
    the drift check behind it. Returns (exit_code_or_None, report lines).

    A driver EXCEPTION — an import failure or a crash inside the driver — is
    the module docstring's pinned crash lane: exit 2 with the exception type
    on stderr, never an exit-1 traceback."""
    lines: list[str] = []
    try:
        cgc = _load_driver("check-governs-coverage.py")
        rc, out = _run_driver(cgc, ["--repo-root", str(root)])
    except Exception as exc:
        lines.append(f"post-gate CRASHED: check-governs-coverage.py: "
                     f"{type(exc).__name__}: {exc}")
        sys.stderr.write(f"signoff-backfill: post-gate driver "
                         f"check-governs-coverage.py crashed: "
                         f"{type(exc).__name__}: {exc}\n")
        return 2, lines
    if rc != 0:
        lines.append(f"post-gate FAILED: check-governs-coverage exit {rc}: {out}")
        print(out, end="")
        return (2 if rc == 2 else 1), lines
    lines.append("post-gate: check-governs-coverage exit 0")

    try:
        gs = _load_driver("summarize-adrs.py")
        rc, out = _run_driver(gs, ["--repo-root", str(root)])
    except Exception as exc:
        lines.append(f"post-gate CRASHED: summarize-adrs.py: "
                     f"{type(exc).__name__}: {exc}")
        sys.stderr.write(f"signoff-backfill: post-gate driver "
                         f"summarize-adrs.py crashed: "
                         f"{type(exc).__name__}: {exc}\n")
        return 2, lines
    if rc != 0:
        lines.append(f"post-gate FAILED: summarize-adrs exit {rc}: {out}")
        print(out, end="")
        return (2 if rc == 2 else 1), lines
    written = json.loads(out).get("written", [])
    lines.append(f"post-gate: summarize-adrs re-derived {len(written)} files")
    try:
        rc, out = _run_driver(gs, ["--repo-root", str(root), "--dry-run"])
    except Exception as exc:
        lines.append(f"post-gate CRASHED: summarize-adrs.py --dry-run: "
                     f"{type(exc).__name__}: {exc}")
        sys.stderr.write(f"signoff-backfill: post-gate driver "
                         f"summarize-adrs.py --dry-run crashed: "
                         f"{type(exc).__name__}: {exc}\n")
        return 2, lines
    if rc != 0:
        lines.append(f"post-gate FAILED: summarize-adrs --dry-run drift: {out}")
        print(out, end="")
        return 1, lines
    lines.append("post-gate: summarize-adrs --dry-run clean")
    return None, lines


# ── main ─────────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Owner sign-off for one backfill batch: render the "
        "enumerated receipts, validate fail-closed, write the four surfaces "
        "plus the completion marker when the arithmetic holds.")
    ap.add_argument("--batch", required=True, help="batch id to sign")
    ap.add_argument("--date", default=None,
                    help="YYYY-MM-DD; defaults to today on the sign path, "
                    "and must equal the recorded date on a signed batch")
    ap.add_argument("--dry-run", action="store_true",
                    help="render + validate, write nothing")
    ap.add_argument("--repo-root", default=".")
    args = ap.parse_args(argv)
    root = Path(args.repo_root).resolve()

    try:
        manifest = sp.read_manifest(root)
        adrs = sp.adrs_dir(root)
        tree_dir = sp.resolve_tree(root)
        reviews = sp.read_reviews(adrs)
        # These two reads are inside the try on purpose: an authored
        # manifest of an unusable shape (a scalar/list document, a non-int
        # `governs_from`) raises here, landing in the same exit-2 crash lane
        # the three sibling summaries drivers use — never an exit-1 traceback.
        adr_cfg = manifest.get("adr") or {}
        gf = sp.governs_from(manifest)
    except sp.GovernsValidationError as exc:
        print(json.dumps({"validation_errors": exc.problems}, sort_keys=True))
        return 1
    except Exception as exc:
        sys.stderr.write(f"signoff-backfill: {type(exc).__name__}: {exc}\n")
        return 2

    bid = args.batch
    if gf is None or adr_cfg.get("governs_backfill_cohort") is None:
        print(json.dumps({"findings": [_finding(
            "tree is inert: adr.governs_from and "
            "adr.governs_backfill_cohort must both be set — the backfill "
            "machinery was never adopted in this tree")]}, sort_keys=True))
        return 1
    batch = reviews["batches"].get(bid)
    if batch is None:
        print(json.dumps({"findings": [_finding(
            f"unknown batch {bid!r} — the reviews manifest records no such "
            "batch id")]}, sort_keys=True))
        return 1

    findings: list[dict] = []
    if any(c.isspace() for c in bid):
        findings.append(_finding(
            f"batch id {bid!r} contains whitespace — the log and journal "
            "heading grammars are one line per entry, so a newline-bearing "
            "id would inject a second line into the append-only log (and "
            "the rest of the whitespace class refuses with it)"))

    signed = batch["signed"]
    if signed is None:
        mode = "sign"
        date = args.date or datetime.date.today().isoformat()
        if not _valid_date(date):
            findings.append(_finding(
                f"--date {date!r} is not a valid YYYY-MM-DD date"))
    else:
        mode = "idempotent"
        date = signed
        if args.date is not None and args.date != signed:
            findings.append(_finding(
                f"batch {bid!r} is already signed ({signed}); --date "
                f"{args.date} contradicts the recorded sign-off date — the "
                "date threads all four surfaces and cannot move"))
    if findings:
        print(json.dumps({"findings": findings}, sort_keys=True))
        return 1

    try:
        records = sp.collect_records(adrs, governs_from=gf)
    except sp.GovernsValidationError as exc:
        print(json.dumps({"validation_errors": exc.problems}, sort_keys=True))
        return 1
    except Exception as exc:
        sys.stderr.write(f"signoff-backfill: {type(exc).__name__}: {exc}\n")
        return 2

    findings, ctx = _validate(root, adrs, manifest, reviews, records,
                              batch, bid, mode)
    _rsim, _lsim, counts, set_marker = _marker_report(ctx, batch, bid, date, reviews)
    # The completion marker is terminal: a batch whose simulated post-sign
    # arithmetic leaves any cohort ADR pending cannot sign while the marker
    # stands. The correction lane's answer is one mini-batch carrying the
    # removal AND its replacement extraction (or a signed no-rule receipt),
    # so the arithmetic still holds post-sign.
    if mode == "sign" and counts["pending"] > 0 \
            and adr_cfg.get("governs_backfill_complete") is True:
        findings.append(_finding(
            f"the completion marker stands and batch {bid!r} leaves "
            f"{counts['pending']} cohort ADR(s) pending post-sign — the "
            "marker cannot stand while any cohort ADR is pending; carry the "
            "replacement extraction or the no-rule receipt in the same "
            "correction batch"))
    plan, plan_findings = _plan_writes(tree_dir, adrs, manifest, reviews,
                                       batch, bid, mode, date, ctx, set_marker)
    findings += plan_findings

    rendering = _render(batch, bid, mode, date, ctx, counts, set_marker)

    if findings:
        findings.sort(key=lambda f: f["problem"])
        if args.dry_run:
            # The spot-audit promise holds on a refusing tree too: the
            # rendering comes FIRST, then the findings, then exit 1.
            print(rendering)
            print("\nvalidation: findings (DRY RUN — no writes):")
            print(json.dumps({"findings": findings}, sort_keys=True))
            return 1
        print(json.dumps({"findings": findings}, sort_keys=True))
        return 1

    if args.dry_run:
        print(rendering)
        print("\nvalidation: clean")
        print("DRY RUN — no writes")
        return 0

    if _plan_is_noop(plan):
        print(rendering)
        print(f"\nno-op: batch {bid!r} is already signed ({date}) and all "
              "four surfaces corroborate — nothing to write")
        return 0

    wrote: list[str] = []
    try:
        if plan["flip"]:
            if _write_reviews_flip(sp.reviews_path(adrs), bid, date,
                                   contained_under=tree_dir):
                wrote.append(f"reviews: batch {bid} signed {date}")
        ledger_writes = _write_ledger_and_marker(
            tree_dir / "manifest.yml", plan["appends"], plan["set_marker"],
            contained_under=tree_dir)
        wrote.extend(ledger_writes)
        if plan["log"] == "write":
            _write_log_entry(tree_dir / "log.md", bid, date,
                             plan["derived_ids"], plan["stats"],
                             allow_present=(mode == "idempotent"),
                             contained_under=tree_dir)
            wrote.append("log: backfill op prepended")
        if plan["journal"] == "write":
            _write_journal_hook(tree_dir / "journal", bid, date,
                                plan["derived_ids"], plan["stats"],
                                allow_present=(mode == "idempotent"),
                                contained_under=tree_dir)
            wrote.append("journal: hook appended")
    except SurfaceContradiction as exc:
        # The mid-write backstop: planning scans every text shape pre-write,
        # so a contradiction raised HERE means the pre-flight missed one —
        # that is the crash lane, not a findings payload.
        sys.stderr.write(
            f"signoff-backfill: surface contradiction the pre-flight missed: "
            f"{exc.finding['problem']}\n"
            f"writes completed before the failure: {wrote}\n"
            "re-run the same command — completion is absence-conditional\n")
        return 2
    except Exception as exc:
        sys.stderr.write(
            f"signoff-backfill: {type(exc).__name__} mid-write: {exc}\n"
            f"writes completed before the failure: {wrote}\n"
            "re-run the same command — completion is absence-conditional\n")
        return 2

    print(rendering)
    print()
    if mode == "idempotent":
        print(f"completed batch {bid!r} sign-off surfaces:")
    else:
        print(f"signed batch {bid!r} on {date}:")
    for w in wrote:
        print(f"  {w}")

    gate_rc, gate_lines = _post_gates(root)
    for line in gate_lines:
        print(line)
    if gate_rc is not None:
        sys.stderr.write(
            "signoff-backfill: the write set landed but a post-write gate is "
            "red — remediate before the next batch\n")
        return gate_rc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
