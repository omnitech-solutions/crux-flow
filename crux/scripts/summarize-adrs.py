# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "httpx>=0.27",
#     "pyyaml>=6.0",
# ]
# ///
# `httpx` is REACHED, not called: this script reads the survey-receipt leg of
# the summaries input domain, which resolves batch state through
# `survey_sheet`, which imports `crux.arch.recover` and so pulls the `crux`
# package, whose council module imports the LLM router, which imports httpx.
# Declaring only PyYAML passes in the dev venv, where httpx is ambient, and
# fails under `uv run --no-project` in the release gate — the only place it is
# tested. `signoff-survey.py` carries the same declaration for the same reason.
"""summarize-adrs.py — the vendored regenerator for <docs_dir>/adrs/summaries/.

Per ADR-0085 (Decision 2: the `governs` block) and ADR-0086 (its coexistence
follow-on), the summaries projection reads ADR `governs` blocks and run-snapshot
artifact bindings and builds three derived artifacts behind ONE drift gate:

  - rule-table.md          handle -> governing rule, one row per governs entry
                             (a backfill entry not yet signed is marked
                             `unreviewed`; a trailing `## Removed handles`
                             section lists tombstoned handles, per ADR-0088)
  - resolver.json           handle -> {rule, provenance, disposition, authority,
                             source_adr, review_state}; a `decided`
                             observation's handle maps to an alias row
                             {alias_of, alias_handles} instead (ADR-0095)
  - implementation-map.md  ADR <-> run implementation map
  - _meta.json              input-hash + schema metadata for the projection:
                             schema "4" declares `input_domain` (the sources
                             the build read) and `observations_sha256`

The input domain is active ADRs UNION `ratified` observations
(docs/AGENTS.md §17; ADR-0095 requirement 4) — read iff `observations` is in
the tree's `concerns_enabled`. The regenerator refuses, fail-closed, to rewrite
a projection whose declared domain names a source it cannot read (requirement
6); the refusal runs before the build, in --dry-run too, and exits 2.

This is a STANDALONE regenerative output, distinct from `derive-arch` — NOT a
fifth arch spine file. The four-file arch spine (data-model, api-surface,
module-graph, decision-index) and its decision-index are untouched by this
script. All shared machinery (frontmatter parsing, the input hash, the three
artifact builders) lives in the frozen `summaries_projection.py` sibling module;
this script is a thin driver: build the four artifacts, compare-or-write them,
and report drift/write status as JSON.

Determinism is the contract: every artifact sorts its rows/keys and contains
no timestamps, so a byte-for-byte re-run is always clean.

Writes are atomic and fail-closed: each artifact is written to <path>.tmp
and os.replace()d into place; a symlinked target or tmp path, and any
intermediate directory resolving outside the validated tree dir, are
refused (exit 2) before the write.

Usage:
  summarize-adrs.py [--repo-root DIR]        rewrite adrs/summaries/{...}
  summarize-adrs.py --dry-run [...]          exit 1 + JSON diff if drift

Exit: 0 clean/written · 1 drift (JSON on stdout), or a governs-block
validation error (malformed entry / out-of-enum provenance —
`sp.GovernsValidationError`; JSON on stdout, key `validation_errors`) ·
2 env error (stderr).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import summaries_projection as sp  # noqa: E402
from untrusted import MESSAGE_LIMIT, redact  # noqa: E402


def _atomic_write_text(path: Path, body: str, *, contained_under: Path) -> None:
    """Atomic UTF-8 text write with no platform newline translation.

    Writes to <path>.tmp then os.replace()s into place. We write bytes
    directly so Python does NOT translate '\\n' → '\\r\\n' on Windows; that
    translation would shift the file's sha256 across platforms and break
    the byte-stable regenerative output contract.

    [SECURITY:S5] Every write target's RESOLVED parent directory must sit
    under `contained_under` — the validated tree dir. The leaf checks below
    guard the target and its tmp file; this guard covers the INTERMEDIATE
    directories: a symlink at <tree>/adrs or <tree>/adrs/summaries leaves every
    entry under it a real file (no leaf symlink to catch) while steering the
    write outside the repo root. Checked first, before any filesystem
    mutation, on the resolved pair. Shared with signoff-backfill.py's copy —
    both thread the validated tree dir from `sp.resolve_tree`.

    [SECURITY:S5] Neither the target nor `<path>.tmp` may be a symlink. The
    tmp path is predictable and a bare write follows a link, so a
    pre-created link turns a regeneration into a write into someone else's
    file, while `os.replace` moves the tmp PATH and leaves the link
    standing. Checked explicitly for a readable error, then created
    O_NOFOLLOW|O_EXCL so the check is not a TOCTOU window. The leaf
    symlink/tmp guards keep BEHAVIORAL parity with this helper's siblings
    in extract-code-docs.py, validate-catalog.py, promote-changelog.py, and
    signoff-backfill.py — same guards, same order, same exception type —
    and are not byte-identical: each names its own subject in its messages
    ("summaries projection content" here). Treat the messages as the only
    licensed difference IN THOSE GUARDS. Change one, change all.
    """
    root_resolved = Path(contained_under).resolve()
    parent_resolved = path.parent.resolve()
    if parent_resolved != root_resolved \
            and root_resolved not in parent_resolved.parents:
        raise OSError(
            f"refusing to write {path}: the parent directory resolves to "
            f"{parent_resolved}, which is not contained under the validated "
            f"tree dir {root_resolved} — a symlinked intermediate directory "
            "would put summaries projection content outside the tree"
        )
    tmp = path.with_suffix(path.suffix + ".tmp")
    for label, candidate in (("target", path), ("temporary file", tmp)):
        if candidate.is_symlink():
            raise OSError(
                f"refusing to write {path}: the {label} {candidate} is a symlink — "
                "writing through it would put summaries projection content in the "
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


def build(root: Path, *, manifest: dict | None = None) -> dict[Path, str]:
    """Return {path: wanted contents} for the four summaries artifacts.

    `manifest` is the parsed `<docs_dir>/manifest.yml`; None reads it from
    the tree. Passing one lets a caller build twice from one tree with the
    observations source read and suppressed — the identity postcondition's
    P1a/P1b legs — while every other input stays on disk and constant.

    Requirement 6 (ADR-0095): a tree may enable observations and never
    enable ADRs. An absent `<tree>/adrs/` is refused (the fail-closed guard
    the projection was written with) UNLESS `adrs` is absent from
    `concerns_enabled` and `observations` is present — then the projection
    is built from the observation half alone, still at `adrs/summaries/`,
    which the write path creates.
    """
    adrs = sp.adrs_dir(root)
    if manifest is None:
        manifest = sp.read_manifest(root)
    concerns = manifest.get("concerns_enabled") or []
    observations_only = "adrs" not in concerns and "observations" in concerns
    if not adrs.is_dir() and not observations_only:
        raise FileNotFoundError(f"{adrs} not found")
    runs = sp.runs_dir(root)
    summaries = adrs / "summaries"

    governs_from = sp.governs_from(manifest)
    # ADR-0088: the anchor contract is boundary-scoped, and the reviews
    # manifest drives the review_state facet on resolver rows + rule table.
    reviews = sp.read_reviews(adrs)
    # ADR-0095 requirement 4: the input domain is active ADRs UNION ratified
    # observations. The observations source is read iff the concern is
    # enabled; an enabled concern this build cannot read is an environment
    # error (exit 2), never a silently narrower domain.
    input_domain = ["adrs", "backfill-reviews"]
    observations = sp.observations_source(root, manifest)
    if observations is not None:
        # ADR-0098 clause 2: the per-batch survey receipts are ONE declared
        # source beside the records, under the existing fail-closed refusal.
        # Declared together with `observations` because they are the same
        # source read two ways — the records and the receipts that published
        # them — and a build that read one without the other could render a
        # partially applied batch.
        input_domain.extend(["observations", "survey-receipts"])
    records = sp.collect_records(adrs, governs_from=governs_from,
                                 observations=observations)
    alias_rows = sp.collect_observation_alias_rows(observations, records)
    rule_table = sp.build_rule_table(records, reviews, governs_from)
    # ADR-0099 clause 2 (`ADR-0099/slug-uniqueness-gate`): the resolver
    # carries two top-level keys beside its handle rows — `slugs` (live slug
    # -> its one live handle) and `retired_slugs` (retired slug -> the sorted
    # live handles that displaced it). They are merged HERE, at the
    # serialization boundary, and nowhere earlier. `build_resolver` stays a
    # pure handle -> row builder. `live_and_retired_slugs` is the map and the
    # fail-closed gate in one traversal, so a collision raises
    # `GovernsValidationError` into main()'s existing exit-1 envelope before
    # anything is serialized. A handle key always matches `ADR-NNNN/slug` or
    # `OBS-NNNN/slug`, so neither new key can collide with a handle key, and
    # every existing key keeps its meaning. Both keys are emitted on an empty
    # corpus (`{}` each), never omitted.
    #
    # Alias rows are OUTSIDE the slug maps and outside the gate. An alias
    # handle (a `decided` observation's `OBS-NNNN/slug`) is a redirect onto
    # an ADR, not a rule, and `live_and_retired_slugs` reads records only.
    # An alias `OBS-0001/foo` beside a rule `ADR-0050/foo` is benign:
    # `slugs["foo"]` resolves to the ADR handle, which is where the alias
    # points anyway.
    resolver_rows = sp.build_resolver(records, reviews, governs_from,
                                      alias_rows=alias_rows)
    slugs, retired_slugs = sp.live_and_retired_slugs(records)
    resolver = json.dumps({**resolver_rows,
                           "slugs": slugs,
                           "retired_slugs": retired_slugs},
                          sort_keys=True, indent=2) + "\n"
    impl_map = sp.build_implementation_map(adrs, runs)
    meta = json.dumps(
        {
            "adr_frontmatter_sha256": sp.adr_frontmatter_sha256(adrs),
            # ADR-0088 (amending ADR-0086 Decision 3): the input hash's domain
            # widens to the reviews manifest — primary authored data, never a
            # downstream index. SHA-256 of its raw bytes; null when absent.
            "backfill_reviews_sha256": sp.backfill_reviews_sha256(adrs),
            # ADR-0095 requirement 6: the projection declares the input
            # domain it was built from, and a later regenerate refuses to
            # rewrite it without every declared source (see main()).
            "input_domain": sorted(input_domain),
            # ADR-0095 requirement 4: the observations half of the input
            # hash, mirroring adr_frontmatter_sha256's construction; null
            # when the concern is not read.
            "observations_sha256": (sp.observations_sha256(observations)
                                    if observations is not None else None),
            # ADR-0098 clause 2: the receipts' own digest, null when the tree
            # has signed no batch. Schema "4" is this key's arrival.
            "survey_receipts_sha256": (sp.survey_receipts_sha256(observations)
                                       if observations is not None else None),
            # Bumped 4 -> 5 when the resolver row gained `source_status`. The rule this
        # followed, recorded here because it is reusable: bump a projection's
        # declared schema where that value IDENTIFIES the output shape and no
        # migration rung depends on it, additive or not. The tree manifest's
        # "breaking changes only" convention does not govern here — that version
        # gates a migration, this one only tells a reader which contract the file
        # was written against.
        "schema": "5",
            "tool": "summarize-adrs.py",
        },
        sort_keys=True,
        indent=2,
    ) + "\n"

    return {
        summaries / "rule-table.md": rule_table,
        summaries / "resolver.json": resolver,
        summaries / "implementation-map.md": impl_map,
        summaries / "_meta.json": meta,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Regenerate the summaries projection (adrs/summaries/) from ADR governs blocks + run snapshots."
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--repo-root", default=".")
    args = ap.parse_args(argv)
    root = Path(args.repo_root).resolve()

    # ADR-0095 requirement 6, fail-closed: BEFORE building, read the existing
    # projection's declared input domain and refuse — exit 2, nothing written,
    # --dry-run identical — when it names a member outside the known set or
    # names `observations` while this build cannot read that source. A gate
    # that cannot read a declared source must never report clean.
    try:
        manifest = sp.read_manifest(root)
        refusal = sp.declared_domain_refusal(
            root, manifest, sp.adrs_dir(root) / "summaries")
    except sp.GovernsValidationError as exc:
        print(json.dumps({"validation_errors": exc.problems}, sort_keys=True))
        return 1
    except Exception as exc:
        sys.stderr.write(f"summarize-adrs: {type(exc).__name__}: {exc}\n")
        return 2
    if refusal is not None:
        sys.stderr.write(f"summarize-adrs: refusing to rewrite: {refusal}\n")
        return 2

    try:
        wanted = build(root, manifest=manifest)
    except sp.GovernsValidationError as exc:
        print(json.dumps({"validation_errors": exc.problems}, sort_keys=True))
        return 1
    except Exception as exc:
        sys.stderr.write(f"summarize-adrs: {type(exc).__name__}: {exc}\n")
        return 2

    if args.dry_run:
        drifted = []
        for path in sorted(wanted):
            want = wanted[path]
            have = path.read_text(encoding="utf-8") if path.exists() else ""
            if have != want:
                drifted.append(str(path.relative_to(root)))
        print(json.dumps({"drift": bool(drifted), "paths": drifted}, sort_keys=True))
        return 1 if drifted else 0

    written = []
    tree_dir = sp.resolve_tree(root)
    root_resolved = tree_dir.resolve()
    try:
        for path in sorted(wanted):
            # Containment runs BEFORE mkdir: mkdir is itself a filesystem
            # mutation, so a parent escaping the validated tree dir must be
            # refused before it is created, not only before the file write.
            parent_resolved = path.parent.resolve()
            if parent_resolved != root_resolved \
                    and root_resolved not in parent_resolved.parents:
                raise OSError(
                    f"refusing to write {path}: the parent directory resolves "
                    f"to {parent_resolved}, which is not contained under the "
                    f"validated tree dir {root_resolved} — a symlinked "
                    "intermediate directory would put summaries projection "
                    "content outside the tree"
                )
            path.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write_text(path, wanted[path], contained_under=tree_dir)
            written.append(str(path.relative_to(root)))
    except Exception as exc:
        sys.stderr.write(f"summarize-adrs: {type(exc).__name__}: {exc}\n")
        return 2
    print(json.dumps({"written": written}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
