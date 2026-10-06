#!/usr/bin/env python3
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyyaml>=6.0"]
# ///
"""signoff-reconciliation.py — the single human sign-off write path for a
doctrine reconciliation record.

Per ADR-0090 req 2, the interpretive judgment — do a ratified invariant and a
governing rule conflict? — lives out of band and is persisted digest-bound,
exactly as the ADR-0088 backfill receipts are. This is the ONE write path over
`<docs_dir>/adrs/doctrine/reconciliations.yml`. One invocation renders the
(invariant text, rule text) pair for the human, validates fail-closed, then
upserts the record and re-compiles the doctrine projection inside its own write
set (the ledger is a compile input, so a sign without the re-compile would red
the drift gate).

A verdict is one of:
  - compatible — the two texts do not conflict;
  - reconciled — a human asserts two apparently-conflicting texts are in fact
    compatible, under an attached rationale (--rationale REQUIRED);
  - collision  — they conflict (blocks the domain's belief until resolved).

The record binds the CURRENT live content digest — SHA-256 over the invariant
ledger page's `## The invariant` text and the rule text, space-folded per the
ADR-0088 canonicalization (the shared `summaries_projection.space_fold`). A
later edit to either text moves the digest and re-gates the pairing
(digest-stale, BROKEN) until re-adjudicated against the settled texts.

Re-runs are idempotent: signing a pairing to the same verdict/rationale over
unchanged texts rewrites the same bytes and re-compiles to the same output — a
reported no-op. The ledger is machine-written in full on every sign (canonical,
sorted), so there is no partial-write state to reconcile.

The widened key (ADR-0095 requirement 5): `--invariant` is slot A of the
pairing and accepts EITHER an `INV-NNNN` id (the ADR-0090 lane: slot-A text is
the invariant's `## The invariant` section) OR an `OBS-NNNN/slug` observation
handle (the added class: slot-A text is the observation rule's text, and the
pairing must be one `doctrine_projection.observation_pairings` seeds — same
domain, evidence inside the authored rule's narrowest containing scope).
`--handle` is slot B and always an authored ADR handle. The flag keeps its
name because it IS the serialized field name (see `serialize_ledger`).

Usage:
  signoff-reconciliation.py --invariant INV-NNNN|OBS-NNNN/slug --handle ADR-NNNN/slug
     --verdict compatible|reconciled|collision [--rationale TEXT]
     [--date YYYY-MM-DD] [--dry-run] [--repo-root DIR]

Exit: 0 signed / no-op · 1 findings (JSON on stdout) · 2 environment/crash
(stderr). `--dry-run` prints the rendering plus the validation report and writes
nothing; on a refusing tree the rendering still prints FIRST (spot-audit), then
the findings, then exit 1.
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

from untrusted import MESSAGE_LIMIT, redact  # noqa: E402
import summaries_projection as sp  # noqa: E402
import doctrine_projection as dp  # noqa: E402

_SCRIPTS = Path(__file__).resolve().parent


def _finding(problem: str) -> dict:
    return {"problem": problem}


def _valid_date(text: str) -> bool:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text or ""):
        return False
    try:
        datetime.date.fromisoformat(text)
        return True
    except ValueError:
        return False


def _atomic_write_text(path: Path, body: str, *, contained_under: Path) -> None:
    """Atomic UTF-8 text write with no platform newline translation.

    [SECURITY:S5] Same containment + leaf-symlink + O_NOFOLLOW|O_EXCL posture as
    summarize-adrs.py / signoff-backfill.py / compile-doctrine.py — same guards,
    same order, same exception type (OSError); each names its own subject in its
    messages ("reconciliation ledger content" here). Change one, change all."""
    root_resolved = Path(contained_under).resolve()
    parent_resolved = path.parent.resolve()
    if parent_resolved != root_resolved \
            and root_resolved not in parent_resolved.parents:
        raise OSError(
            f"refusing to write {path}: the parent directory resolves to "
            f"{parent_resolved}, which is not contained under the validated "
            f"tree dir {root_resolved} — a symlinked intermediate directory "
            "would put reconciliation ledger content outside the tree"
        )
    tmp = path.with_suffix(path.suffix + ".tmp")
    for label, candidate in (("target", path), ("temporary file", tmp)):
        if candidate.is_symlink():
            raise OSError(
                f"refusing to write {path}: the {label} {candidate} is a symlink — "
                "writing through it would put reconciliation ledger content in the "
                "link's target"
            )
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    except FileExistsError as exc:
        raise OSError(
            f"refusing to write {path}: the temporary file {tmp} already exists; "
            "remove it after checking what created it"
        ) from exc
    except OSError as exc:
        raise OSError(f"refusing to write {path}: cannot create {tmp} ({exc})") from exc
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(body.encode("utf-8"))
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise


def serialize_ledger(records: list[dict]) -> str:
    """Canonical, sorted, byte-stable serialization of the reconciliation ledger.

    Machine-written in full on every sign — the ledger has no hand-authored
    region, so there is no textual-patch preservation to do (unlike the backfill
    reviews manifest). Records sort by (invariant, handle); every scalar is
    emitted plainly (invariant/handle/verdict/digest match strict regexes) except
    the free-text rationale, JSON-quoted (a JSON string is a valid YAML
    double-quoted scalar) so `read_reconciliations` round-trips it exactly.

    RECORDED BACK-COMPAT DECISION (ADR-0095 requirement 5): the key is now an
    ordered pair of member identities, and slot A may hold an `OBS-NNNN/slug`
    observation handle. The on-disk field is still spelled `invariant:` and the
    leading comment header still says "(invariant, governs-handle) pairing" —
    both are left byte-for-byte, because changing either would rewrite every
    signed record, and the ledger-compatibility requirement forbids that. A
    record's `member_kind` (from `read_reconciliations`) is derived from the
    slot-A value and is never serialized.
    """
    ordered = sorted(records, key=lambda r: (r["invariant"], r["handle"]))
    out = ["# bionic/adrs/doctrine/reconciliations.yml — the doctrine "
           "reconciliation ledger.",
           "#",
           "# Machine-written in full by signoff-reconciliation.py (ADR-0090). "
           "One record per",
           "# (invariant, governs-handle) pairing, its verdict bound to a "
           "content_digest over the",
           "# invariant text + rule text. Hand-edits are overwritten on the "
           "next sign-off.",
           "",
           'config_version: "1"',
           "reconciliations:"]
    for r in ordered:
        out.append(f"  - invariant: {r['invariant']}")
        out.append(f"    handle: {r['handle']}")
        out.append(f"    verdict: {r['verdict']}")
        out.append(f"    content_digest: {r['content_digest']}")
        rationale = ("null" if r["rationale"] is None
                     else json.dumps(r["rationale"]))
        out.append(f"    rationale: {rationale}")
        out.append(f"    signed: {r['signed'] if r['signed'] else 'null'}")
    return "\n".join(out) + "\n"


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


def _post_gate(root: Path) -> tuple[int | None, list[str]]:
    """Re-compile the doctrine projection (the ledger is a compile input, so the
    re-derive is INSIDE the write set), then the drift check behind it. Returns
    (exit_code_or_None, report lines). A driver exception is the crash lane:
    exit 2 with the type on stderr."""
    lines: list[str] = []
    try:
        cd = _load_driver("compile-doctrine.py")
        rc, out = _run_driver(cd, ["--repo-root", str(root)])
    except Exception as exc:
        lines.append(f"post-gate CRASHED: compile-doctrine.py: "
                     f"{type(exc).__name__}: {exc}")
        sys.stderr.write(f"signoff-reconciliation: post-gate driver "
                         f"compile-doctrine.py crashed: "
                         f"{type(exc).__name__}: {exc}\n")
        return 2, lines
    if rc != 0:
        lines.append(f"post-gate FAILED: compile-doctrine exit {rc}: {out}")
        print(out, end="")
        return (2 if rc == 2 else 1), lines
    written = json.loads(out).get("written", [])
    lines.append(f"post-gate: compile-doctrine re-derived {len(written)} files")
    try:
        rc, out = _run_driver(cd, ["--repo-root", str(root), "--dry-run"])
    except Exception as exc:
        lines.append(f"post-gate CRASHED: compile-doctrine.py --dry-run: "
                     f"{type(exc).__name__}: {exc}")
        sys.stderr.write(f"signoff-reconciliation: post-gate driver "
                         f"compile-doctrine.py --dry-run crashed: "
                         f"{type(exc).__name__}: {exc}\n")
        return 2, lines
    if rc != 0:
        lines.append(f"post-gate FAILED: compile-doctrine --dry-run drift: {out}")
        print(out, end="")
        return 1, lines
    lines.append("post-gate: compile-doctrine --dry-run clean")
    return None, lines


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Human sign-off for one doctrine reconciliation pairing: "
        "render the (invariant, rule) texts, validate fail-closed, upsert the "
        "digest-bound record, and re-compile the doctrine projection.")
    ap.add_argument("--invariant", required=True,
                    help="slot A: an INV-NNNN id, or an OBS-NNNN/slug observation handle")
    ap.add_argument("--handle", required=True,
                    help="slot B: the ADR-NNNN/slug authored governs handle")
    ap.add_argument("--verdict", required=True,
                    choices=list(dp.VERDICTS))
    ap.add_argument("--rationale", default=None,
                    help="required for verdict reconciled")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD; defaults to today")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--repo-root", default=".")
    args = ap.parse_args(argv)
    root = Path(args.repo_root).resolve()

    try:
        manifest = sp.read_manifest(root)
        adrs = sp.adrs_dir(root)
        tree_dir = sp.resolve_tree(root)
        governs_from = sp.governs_from(manifest)
        # ADR-0095 requirement 5: the same observation gate compile-doctrine.py
        # applies — read the corpus iff the concern is enabled; refuse (exit 2)
        # an enabled concern this run cannot read. Call the ONE shared
        # predicate rather than re-deriving it here: `observations_source`
        # exists because this test was briefly copied per-lane, and the copy
        # that drifted made a gate report clean on a tree the regenerator
        # marked BROKEN.
        observations = sp.observations_source(root, manifest)
        records = sp.collect_records(adrs, governs_from=governs_from,
                                     observations=observations)
        invariants = dp.read_invariants(root)
        existing = dp.read_reconciliations(root)
    except (sp.GovernsValidationError, dp.DoctrineValidationError) as exc:
        print(json.dumps({"validation_errors": exc.problems}, sort_keys=True))
        return 1
    except Exception as exc:
        sys.stderr.write(f"signoff-reconciliation: {type(exc).__name__}: {exc}\n")
        return 2

    findings: list[dict] = []
    slot_a = args.invariant
    slot_a_ok = bool(dp.MEMBER_A_RE.match(slot_a))
    kind = dp.member_kind(slot_a) if slot_a_ok else None
    inv = None
    obs = None
    # Slot B is always an authored handle: an observation record's handle is
    # never a valid `--handle`, exactly as `read_reconciliations` refuses it.
    # `live_records` is applied for the same reason the artifact builders apply
    # it (ADR-0097 part 6): a RETIRED handle publishes in no doctrine surface,
    # so signing a pairing against it would mint a signature for a rule nobody
    # can read — and the retirement machinery's own refusal would then treat
    # that signature as an orphan to protect.
    rec = next((r for r in sp.live_records(records) if r["handle"] == args.handle
                and r.get("source_kind", "adr") == "adr"), None)
    rationale = (args.rationale.strip()
                 if isinstance(args.rationale, str) and args.rationale.strip()
                 else None)
    date = args.date or datetime.date.today().isoformat()

    if not slot_a_ok:
        findings.append(_finding(
            f"slot A {slot_a!r} must be an INV-NNNN invariant id or an "
            "OBS-NNNN/slug observation handle"))
    elif kind == dp.MEMBER_KIND_INVARIANT:
        inv = next((i for i in invariants if i["id"] == slot_a), None)
        if inv is None:
            findings.append(_finding(
                f"unknown invariant {slot_a!r} — no ratified-or-observed "
                "ledger page carries that id"))
        elif inv["ratification"] != "ratified":
            findings.append(_finding(
                f"invariant {slot_a} is {inv['ratification']!r}, not "
                "ratified — only a ratified invariant seeds a pairing"))
    else:
        obs = next((r for r in records if r["handle"] == slot_a
                    and r.get("source_kind") == "observation"), None)
        if obs is None:
            findings.append(_finding(
                f"unknown observation handle {slot_a!r} — no ratified "
                "observation carries that governs handle"))
    if rec is None:
        findings.append(_finding(
            f"unknown handle {args.handle!r} — no live authored governs entry "
            "carries it"))
    if inv is not None and rec is not None \
            and rec["source_adr"] not in inv["related_adrs"]:
        findings.append(_finding(
            f"({slot_a}, {args.handle}) is not a candidate pairing — "
            f"{slot_a}'s related_adrs does not list {rec['source_adr']}, "
            "so no structural link seeds it"))
    if obs is not None and rec is not None:
        seeded = {(p["invariant"], p["handle"])
                  for p in dp.observation_pairings(records, sp.tree_name(root))}
        if (slot_a, args.handle) not in seeded:
            findings.append(_finding(
                f"({slot_a}, {args.handle}) is not a candidate pairing — no "
                f"evidence path of {slot_a} lies inside {args.handle}'s narrowest "
                f"containing declared scope in domain {obs['domain']!r}, so no "
                "structural link seeds it"))
    if args.verdict == "reconciled" and rationale is None:
        findings.append(_finding(
            "verdict reconciled requires --rationale — a human asserts two "
            "apparently-conflicting texts are compatible under it"))
    if args.verdict != "reconciled" and rationale is not None:
        findings.append(_finding(
            f"verdict {args.verdict} takes no rationale — a rationale is the "
            "reconciled verdict's required justification, meaningless elsewhere"))
    if not _valid_date(date):
        findings.append(_finding(f"--date {date!r} is not a valid YYYY-MM-DD date"))

    rendering = None
    if (inv is not None or obs is not None) and rec is not None:
        # Slot-A text then slot-B text — the digest function is unchanged;
        # only WHICH text fills slot A differs by member kind.
        if inv is not None:
            a_text = inv["invariant_text"]
            a_label = "invariant text (verbatim, ## The invariant):"
        else:
            a_text = obs["rule"]
            a_label = (f"observation rule text (verbatim from {obs['source_adr']} "
                       "governs block):")
        live_digest = dp.reconciliation_content_digest(a_text, rec["rule"])
        rendering = "\n".join([
            f"Doctrine reconciliation sign-off — {slot_a} x {args.handle}",
            f"verdict: {args.verdict}  signing date {date}",
            f"domain: {rec['domain']}   source ADR: {rec['source_adr']}",
            "",
            a_label,
            f"  {json.dumps(a_text)}",
            "",
            f"rule text (verbatim from {rec['source_adr']} governs block):",
            f"  {json.dumps(rec['rule'])}",
            "",
            f"content_digest (live): {live_digest}",
            f"rationale: {json.dumps(rationale) if rationale else '(none)'}",
        ])

    if findings:
        findings.sort(key=lambda f: f["problem"])
        if args.dry_run and rendering is not None:
            print(rendering)
            print("\nvalidation: findings (DRY RUN — no writes):")
        print(json.dumps({"findings": findings}, sort_keys=True))
        return 1

    # Upsert the record, always binding the CURRENT live digest.
    new_record = {"invariant": slot_a, "member_kind": kind, "handle": args.handle,
                  "verdict": args.verdict, "content_digest": live_digest,
                  "rationale": rationale, "signed": date}
    updated = [r for r in existing
               if not (r["invariant"] == args.invariant and r["handle"] == args.handle)]
    updated.append(new_record)
    wanted_bytes = serialize_ledger(updated)

    path = dp.reconciliations_path(root)
    current_bytes = path.read_text(encoding="utf-8") if path.is_file() else None
    noop = current_bytes == wanted_bytes

    if args.dry_run:
        print(rendering)
        print("\nvalidation: clean")
        print("no-op (record already current)" if noop
              else f"would sign {args.invariant} x {args.handle} = {args.verdict}")
        print("DRY RUN — no writes")
        return 0

    if noop:
        print(rendering)
        print(f"\nno-op: ({args.invariant}, {args.handle}) is already signed "
              f"{args.verdict} against the current texts — nothing to write")
        # Still confirm the projection is fresh behind the ledger.
        gate_rc, gate_lines = _post_gate(root)
        for line in gate_lines:
            print(line)
        return gate_rc if gate_rc is not None else 0

    try:
        # [SECURITY:S5] Containment is checked BEFORE mkdir — hoisted ahead of
        # the directory creation, matching compile-doctrine.py:165-174 and
        # signoff-backfill.py's containment-before-mkdir posture, so a
        # symlinked intermediate directory (e.g. `adrs/doctrine`) is refused
        # before this call creates anything through it. `_atomic_write_text`
        # repeats the same check on the leaf write as defense in depth; this
        # hoist changes nothing about the happy path, only WHEN the refusal
        # fires relative to mkdir.
        root_resolved = tree_dir.resolve()
        parent_resolved = path.parent.resolve()
        if parent_resolved != root_resolved \
                and root_resolved not in parent_resolved.parents:
            raise OSError(
                f"refusing to write {path}: the parent directory resolves to "
                f"{parent_resolved}, which is not contained under the "
                f"validated tree dir {root_resolved} — a symlinked "
                "intermediate directory would put reconciliation ledger "
                "content outside the tree"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(path, wanted_bytes, contained_under=tree_dir)
    except Exception as exc:
        sys.stderr.write(f"signoff-reconciliation: {type(exc).__name__} "
                         f"writing the ledger: {exc}\n")
        return 2

    print(rendering)
    print(f"\nsigned ({args.invariant}, {args.handle}) = {args.verdict} on {date}")
    gate_rc, gate_lines = _post_gate(root)
    for line in gate_lines:
        print(line)
    if gate_rc is not None:
        sys.stderr.write(
            "signoff-reconciliation: the ledger write landed but the doctrine "
            "re-compile is red — remediate before the next sign-off\n")
        return gate_rc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
