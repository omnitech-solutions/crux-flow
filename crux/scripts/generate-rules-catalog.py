#!/usr/bin/env python3
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyyaml>=6.0"]
# ///
"""generate-rules-catalog.py — project this project's own rules into a catalog the plugin ships.

WHY THIS EXISTS. The plugin cites its own rules by slug on the surfaces it ships, and until this
catalog it shipped nothing that defined one. The release artifact carries no decision tree, so
every one of those citations was unresolvable for the reader it shipped to, while the shipped
contract said a citation must resolve. Per rule:citation-resolves-by-its-citing-surface, a
citation on a shipped surface resolves against this catalog; per
rule:shipped-catalog-is-derived-and-names-no-decision-record, this catalog is derived and carries
no decision-record identifier.

INPUT DOMAIN, and why it is the cited set rather than every rule. The catalog carries the DISTINCT
slugs cited on shipped surfaces, excluding test fixtures and the catalog's own output so the
projection cannot feed on itself. A citation naming a slug the catalog does not yet carry drifts
it until this runs; a citation naming a slug already carried drifts nothing, and removing a slug's
last citation drifts it again. That is what makes the drift gate a coverage gate for slugs.

NO DECISION-RECORD IDENTIFIER. `source_adr` is deliberately dropped. The catalog directory is not
one of the three prose surfaces the release-content scan governs, so carrying it would be legal —
it is omitted because a reader without this project cannot open that record, and the citation form
those surfaces use names a rule rather than a record.

PUBLICATION ELIGIBILITY, and why it lives here rather than in the citation lint. A slug reaching
this catalog is published to a reader who cannot open the record behind it -- the payload carries
no decision-record identifier by design. So the question "has the decision behind this rule been
accepted?" has to be answered at the moment of projection, which is here. It is a SECOND check
beside resolution, never a stricter first one: `lint-governs-references.py` still resolves every
citation on every surface exactly as before, and a citation on a reader-owned surface is never
asked this question at all. Per rule:shipped-citation-requires-an-accepted-decision the verdict
comes from `publication_eligibility`, and per rule:an-ineligible-citation-refuses-the-projection an
ineligible citation REFUSES this projection rather than being filtered out of it -- filtering would
leave the shipped surface citing a slug the catalog no longer carries, which is the unresolvable
state the catalog exists to end.

The refusal runs before the target is read, before its parent is created, and before anything is
written, so a refused run leaves an existing catalog byte-unchanged and creates no absent one. It
runs in BOTH modes, and the drift mode is the load-bearing one: a status change moves no byte of
this file, so a byte comparison alone can never see it.

Exit codes follow the repository convention: 0 clean, 1 drift OR a validation error (valid JSON on
stdout), 2 environment or input error (stderr, no stdout payload). An eligibility refusal is a
DOCUMENT-lane finding and so takes exit 1 with `validation_errors` on stdout: its remedy is to
accept the decision or drop the citation, never to re-run this script, and exit 2 would be read by
every CI classifier as "the gate did not execute".
"""

from __future__ import annotations

import argparse
import bisect
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import authoring_scope as _scope  # noqa: E402

import publication_eligibility as pe  # noqa: E402
import summaries_projection as sp  # noqa: E402

# The repository's one bound-and-redact helper, reached through the projection
# exactly as the sibling citation lint reaches it. Every untrusted value this
# script emits -- a slug read out of an arbitrary file, a `status:` cell copied
# verbatim off ADR frontmatter with no enum validation, an OS error string --
# goes through it before reaching stdout. The payload's consumer is a model that
# renders these rows into a report, where an unbounded value carrying control
# characters or injected prose is no longer inside JSON quotes.
redact = sp.redact

# The citation token the contract's grammar defines: `rule:` followed by a lowercase
# letter, then lowercase letters, digits and hyphens. The token ends at the first
# character outside that set, which is what makes the `rule:<slug>` placeholder form
# a non-token.
CITATION_RE = re.compile(r"rule:([a-z][a-z0-9-]*)")

# Shipped surfaces scanned for citations. `crux/catalog` is excluded so the projection
# cannot feed on itself, and `crux/scripts/tests` because a fixture citation is test
# data rather than a claim a reader acts on.
SCAN_ROOTS = ("skills", "agents", "templates", "scripts")
EXCLUDED_PARTS = ("tests", "catalog", "__pycache__")
MAX_FILE_BYTES = 4 * 1024 * 1024  # the sibling lint's ceiling
# The sibling lint's file-count ceiling, adopted for the same reason: an unbounded
# walk over a large tree stalls every enforcement point rather than failing, and a
# gate that never returns is a gate that did not execute. Hitting it is a REFUSAL,
# not a truncation, because the surfaces past the ceiling were not examined.
MAX_SCANNED_FILES = 5000
TEXT_SUFFIXES = {".md", ".tmpl", ".py", ".yaml", ".yml", ".json", ".toml", ".sh", ".txt"}


def cited_slug_paths(
        plugin_root: Path) -> tuple[dict[str, list[str]], list[dict]]:
    """`(slug -> sorted `path:line` locations, refusals)` over the shipped surfaces.

    The CITING PATH is what makes a refusal actionable: a finding naming only the slug
    leaves the author grepping sixty skills for it. Per
    rule:an-ineligible-citation-refuses-the-projection the finding names where the
    citation sits, so the scan has to carry the location rather than discard it.

    A FILE THIS SCAN CANNOT READ IS A REFUSAL, NEVER A SKIP. This is the difference
    between a guard and a suggestion. A file that is not read contributes no citations,
    so it produces no finding — while the release still ships it, because the stage
    allowlists `crux/` wholesale. Dropping unreadable files silently therefore let a
    committed shipped file HIDE an ineligible citation from every enforcement point at
    once: one invalid UTF-8 byte, or padding past `MAX_FILE_BYTES`, or a suffix outside
    the allowlist, and the citation ships unexamined while the gate stays green. The
    sibling citation lint already had the right shape — it records refusal rows and
    makes a non-empty list part of its exit condition — and this now matches it.

    The corpus, the roots and the exclusions are unchanged: a richer return and a
    refusal list, not a wider scan. Two readers of "what is shipped" giving two answers
    is a defect this repository has already paid to fix once.
    """
    found: dict[str, list[str]] = {}
    refusals: list[dict] = []
    scanned = 0
    for root in SCAN_ROOTS:
        base = plugin_root / root
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(plugin_root)
            if any(part in EXCLUDED_PARTS for part in rel.parts):
                continue
            label = f"crux/{rel}"
            if path.suffix not in TEXT_SUFFIXES:
                # NOT a refusal: the extension allowlist is the scan's DEFINITION of a
                # text surface, and a binary asset legitimately carries no citation.
                # Recording every image as a refusal would bury the real ones. The
                # residual — a citation inside an unlisted text extension — is a
                # roster question about SCAN_ROOTS/TEXT_SUFFIXES, not a read failure.
                continue
            # The walk is bounded, matching the sibling lint's ceiling. Without one, a
            # tree with enough shipped files stalls every enforcement point rather than
            # failing, and a stalled gate is a gate that did not execute.
            if scanned >= MAX_SCANNED_FILES:
                refusals.append({
                    "path": label,
                    "reason": f"scope exceeds MAX_SCANNED_FILES ({MAX_SCANNED_FILES}); "
                              "the walk stopped and the remaining surfaces were not read",
                })
                break
            scanned += 1
            # Containment and size refusals, matching the sibling citation lint rather
            # than diverging from it: a symlink out of the plugin root is not a shipped
            # surface, and an oversized file is refused rather than read whole. Each is
            # now RECORDED rather than skipped.
            try:
                real = path.resolve(strict=True)
            except OSError as exc:
                refusals.append({"path": label,
                                 "reason": f"cannot resolve: {redact(exc, quoted=False)}"})
                continue
            try:
                real.relative_to(plugin_root.resolve())
            except ValueError:
                # Deliberately NOT a refusal that reds the gate: a symlink leaving the
                # plugin root does not ship, so it is not a shipped surface and has no
                # citations to hide. Recorded for visibility only.
                refusals.append({"path": label, "reason": "resolves outside the plugin "
                                                          "root; not a shipped surface",
                                 "advisory": True})
                continue
            try:
                if real.stat().st_size > MAX_FILE_BYTES:
                    refusals.append({
                        "path": label,
                        "reason": f"larger than MAX_FILE_BYTES ({MAX_FILE_BYTES}); "
                                  "refused rather than read whole",
                    })
                    continue
                text = real.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                refusals.append({
                    "path": label,
                    "reason": f"unreadable as UTF-8 text: {type(exc).__name__}: "
                              f"{redact(exc, quoted=False)}",
                })
                continue
            # Newline offsets computed ONCE per file, then bisected. The obvious
            # `text.count("\n", 0, m.start())` per match rescans the buffer from zero
            # every time, which is quadratic: measured at 1.37s / 5.43s / 21.96s for
            # 32k / 64k / 128k citations in one file — a clean 4x per 2x. At the 4 MiB
            # ceiling that is minutes for a single committed file, enough to exhaust the
            # workflow timeout and to wedge the release gates, which have none.
            starts = [0]
            for i, ch in enumerate(text):
                if ch == "\n":
                    starts.append(i + 1)
            for m in CITATION_RE.finditer(text):
                line = bisect.bisect_right(starts, m.start())
                found.setdefault(m.group(1), []).append(f"{label}:{line}")
        else:
            continue
        break
    return ({slug: sorted(set(locs)) for slug, locs in found.items()}, refusals)


def build_catalog(repo_root: Path, plugin_root: Path) -> dict:
    """The catalog payload, plus the two diagnostic lists `main` consumes and strips.

    Returns `{"schema", "rules", "unresolved", "ineligible", "refusals"}`. Only the
    first three reach `render`; `main` refuses on the last two and serializes neither. The
    payload shape the plugin ships is therefore UNCHANGED -- this guard needs no change
    to the distributed catalog schema, and does not make one.

    `by_slug` holds the WHOLE record rather than the two fields the catalog emits,
    because eligibility is decided from `source_kind` and `source_status`, which the
    two-field projection discarded. The two emitted fields are projected at the end
    instead, so the bytes are identical to before.
    """
    # No `hasattr` guard with a hardcoded `bionic` fallback: the tree's name is
    # configurable, so a helper rename would have silently projected the wrong tree
    # instead of failing. An AttributeError here is the exit-2 lane, which is correct.
    docs = sp.resolve_tree(repo_root)
    records = sp.live_records(
        sp.collect_records(docs / "adrs", governs_from=None, observations=docs / "observations")
    )
    # The slug -> handle map is the projection's OWN, not a second spelling of the
    # split. It raises on a live-slug collision, where the previous inline loop resolved
    # one last-write-wins -- which would have let this guard judge one record while the
    # catalog published another's text. One map, one answer.
    slugs, _retired = sp.live_and_retired_slugs(records)
    # No `if handle in by_handle` guard: both sides derive from `records`, so every
    # handle the slug map names is a key here. A guard over a condition that cannot
    # fail reads as caution and is really a claim that the two maps might disagree.
    by_handle = {r["handle"]: r for r in records}
    by_slug: dict[str, dict] = {slug: by_handle[handle] for slug, handle in slugs.items()}

    cited, refusals = cited_slug_paths(plugin_root)
    wanted = set(cited)
    missing = sorted(s for s in wanted if s not in by_slug)
    publishable = sorted(wanted & set(by_slug))

    # THE GUARD, over exactly the publishable set and nothing wider. An unresolvable
    # slug is the `unresolved` list's business and is refused there first, so no input
    # reaches two refusals; the predicate's own absent-record refusal stays a fail-closed
    # default for any other caller.
    ineligible = pe.findings({s: cited[s] for s in publishable}, by_slug)

    rules = {
        s: {"rule": by_slug[s].get("rule", ""), "domain": by_slug[s].get("domain", "")}
        for s in publishable
    }
    return {"schema": "1", "rules": rules, "unresolved": missing,
            "ineligible": ineligible, "refusals": refusals}


def _require_surface_refusal(why: str) -> int:
    """The refusal an ENFORCEMENT POINT gets where a consumer gets the N/A lane.

    WHY A CALLER DECLARES THIS RATHER THAN THE SCRIPT INFERRING IT. Surface-absent
    exits 0 so a consuming project, which owns no decision tree, files the row N/A
    instead of failing — correct, and the whole reason the lane exists. But the same
    exit 0 reads as GREEN to a shell that tests only the return code, and all three of
    this guard's enforcement points are such shells. The tree can go absent from a
    well-formed config naming a directory that is not there, from a rename, or from a
    move; the gate then fires, prints OK, and has examined nothing. Worse, the config
    file is inside the CI trigger's own paths list, so the edit that disables the guard
    is the edit that fires the gate that then passes it.

    A script cannot tell the two callers apart -- both run the same command in a
    directory -- so the caller says which it is. Per
    rule:one-guard-called-from-every-enforcement-point, no enforcement point is
    satisfied by a check that reports a pass without executing; "cannot verify" is not
    "nothing to verify".

    Exit 2 with empty stdout: this is the environment lane, and every consumer of this
    gate already refuses on it rather than reading it as a document finding.
    """
    print(f"generate-rules-catalog: --require-surface was passed and the surface is "
          f"absent: {why}. Refusing to report a pass from a check that inspected "
          f"nothing.", file=sys.stderr)
    return 2


# The remedy line, in one place because it is wrong in three different ways if it is
# written once per call site. "Accept the decision" is the remedy for a PROPOSED host
# only: the state machine offers no transition back from Deprecated or Superseded, so
# there the remedy is to migrate the citation to whatever displaced the rule, and an
# observation-backed row needs an owner decision that nobody has made yet. The per-row
# `reason` was always accurate; the summary beside it was not.
REMEDY = ("Accept a Proposed decision; for a Deprecated or Superseded one, migrate the "
          "citation to the rule that displaced it or remove it; an observation-backed "
          "rule needs an owner decision before it can ship.")

SHIPPED_KEYS = ("schema", "rules", "unresolved")


def render(catalog: dict) -> str:
    """The bytes the plugin ships. `build_catalog` returns two diagnostic keys beside
    the payload; they are projected out here so the distributed schema is unchanged and
    a diagnostic can never leak into the artifact by someone adding a key upstream."""
    shipped = {k: catalog[k] for k in SHIPPED_KEYS}
    return json.dumps(shipped, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--require-surface", action="store_true",
        help="refuse instead of reporting surface-absent. An ENFORCEMENT POINT passes "
             "this; a consuming project's drift sweep does not.")
    args = ap.parse_args(argv)

    repo_root = _scope.resolve_repo_root(args.repo_root)
    plugin_root = repo_root / "crux"
    target = plugin_root / "catalog" / "rules.json"

    # SURFACE-ABSENT LANE, on the `extract-code-docs` model and for the same reason the
    # parity check skips a clause whose twin is absent. This regenerator projects THIS
    # project's own governs blocks, which exist only in the authoring checkout; an
    # installed project has the plugin in its plugin cache, not at `<repo>/crux/`.
    # Without this lane a downstream `check-drift` run reported drift against a file it
    # had no business owning, and the remedy that row names WROTE a `crux/` tree into the
    # reader's repository — which the decision explicitly disclaims.
    if not _scope.is_authoring_checkout(repo_root, __file__):
        if args.require_surface:
            return _require_surface_refusal(
                "the plugin's authoring checkout is not here, so there is no source to "
                "project from")
        return _scope.print_surface_absent(written=None)

    # SECOND TRIGGER of the same lane. The catalog is projected from THIS tree's own
    # `governs` blocks, and the release artifact ships `crux/` without the tree. Run
    # from the staged artifact or a public clone, the first probe passes -- both carry
    # `crux/scripts/` -- and then every cited slug resolved to nothing, so the gate
    # exited 2 with empty stdout and `check-drift` filed a CRASH against an artifact
    # that owns no source to project from. No tree, no projection, nothing to drift.
    # A MALFORMED CONFIG IS NOT AN ABSENT SURFACE. This used to be one blanket
    # `except Exception` that turned every failure of `resolve_tree` into the
    # surface-absent lane -- exit 0, `surface_absent: true`, a green. That is right for
    # a tree that genuinely is not here and wrong for a tree whose config cannot be
    # read: the tree contract's §14.1 requires a malformed `.bionic.yml` to fail loud
    # and NEVER to fall back. It mattered more here than for a pure drift gate, because
    # this same command is the CI step, the preflight row and the publication gate, and
    # `.bionic.yml` is inside the workflow's own paths list -- so one edit to it made
    # the workflow fire, report OK, and leave publication eligibility unverified
    # everywhere at once. "Cannot verify" is not "nothing to verify".
    try:
        docs = sp.resolve_tree(repo_root)
    except Exception as exc:  # noqa: BLE001 — the environment/input lane, loudly
        print(f"generate-rules-catalog: cannot resolve the documentation tree: "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    if docs is None or not (docs / "adrs").is_dir():
        if args.require_surface:
            return _require_surface_refusal(
                "no documentation tree is present here, so no source record's status "
                "could be read")
        return _scope.print_surface_absent(
            written=None,
            reason="the rules catalog is projected from this tree's own governs blocks, "
                   "and no documentation tree is present here")

    try:
        catalog = build_catalog(repo_root, plugin_root)
    except Exception as exc:  # noqa: BLE001 — environment/input failures are the exit-2 lane
        print(f"generate-rules-catalog: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    if catalog["unresolved"]:
        print(
            "generate-rules-catalog: shipped surfaces cite slugs no live rule defines: "
            f"{catalog['unresolved']}",
            file=sys.stderr,
        )
        return 2

    # UNREAD-SURFACE REFUSAL, and it comes FIRST because it bounds what the eligibility
    # check below could possibly have seen. A shipped file this scan could not read
    # contributes no citations, so it yields no eligibility finding while the release
    # ships it regardless — which made an unreadable file a way to hide an ineligible
    # citation from every enforcement point at once. A guard that cannot read its own
    # corpus has not measured it, and reporting a pass from that is the false green
    # rule:one-guard-called-from-every-enforcement-point forbids.
    #
    # An advisory row (a symlink leaving the plugin root) is reported and does NOT red
    # the gate: it names a path that does not ship, so it hides nothing.
    blocking = [r for r in catalog["refusals"] if not r.get("advisory")]
    if blocking:
        print(json.dumps({
            "validation_errors": blocking,
            "path": str(target.relative_to(repo_root)),
            "written": None,
        }, sort_keys=True))
        print(
            f"generate-rules-catalog: {len(blocking)} shipped surface(s) could not be "
            "read, so their citations were not examined; nothing was written. Make each "
            "file readable UTF-8 text within the size ceiling, or move it off the "
            "shipped surfaces.",
            file=sys.stderr,
        )
        return 1

    # PUBLICATION-ELIGIBILITY REFUSAL (rule:an-ineligible-citation-refuses-the-projection).
    #
    # POSITION IS THE CONTRACT. This sits before `target.read_text`, before
    # `target.parent.mkdir` and before `target.write_text`, so a refused run leaves an
    # existing catalog byte-unchanged and creates no absent one. It also sits OUTSIDE the
    # `--dry-run` branch below, which is the load-bearing half: a source record's status
    # moves no byte of this file, so a refusal reachable only through the write lane would
    # let the drift gate report clean forever on a catalog holding an unaccepted rule.
    #
    # Every ineligible citation is reported, never the first alone, so the remedy is one
    # round trip. The DOCUMENT lane (exit 1, JSON on stdout) is deliberate: the remedy is
    # to accept the decision or drop the citation, and exit 2 would be announced by every
    # CI classifier as an environment failure the reader cannot act on.
    if catalog["ineligible"]:
        print(json.dumps({
            "validation_errors": catalog["ineligible"],
            "path": str(target.relative_to(repo_root)),
            "written": None,
        }, sort_keys=True))
        print(
            f"generate-rules-catalog: {len(catalog['ineligible'])} shipped citation(s) "
            "name a rule whose source record is not eligible for publication; nothing "
            "was written. The remedy depends on WHY, and each row's `reason` says which: "
            + REMEDY,
            file=sys.stderr,
        )
        return 1

    payload = render(catalog)
    current = target.read_text(encoding="utf-8") if target.is_file() else None

    if args.dry_run:
        drift = current != payload
        print(json.dumps({"drift": drift, "path": str(target.relative_to(repo_root)),
                          "rules": len(catalog["rules"])}, sort_keys=True))
        return 1 if drift else 0

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(payload, encoding="utf-8")
    print(json.dumps({"written": str(target.relative_to(repo_root)),
                      "rules": len(catalog["rules"])}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
