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
                             schema "6" declares `input_domain` (the sources
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
import stat
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


def _historical_summary_clauses(root: Path, view: dict) -> tuple[list[dict], dict | None]:
    """Load only the exact batch named by a proved publication, without a second approval."""
    import implementation_migration as migration
    if view["state"] != "published":
        sp._projection_require(not view["publications"], "summaries unproved publication refused")
        return [], None
    latest = view["publications"][-1]
    text, _ = migration._read(root, latest["path"])
    sp._projection_require(migration._digest(text.encode()) == latest["sha256"],
                           "summaries publication changed during input loading")
    locator = json.loads(text)
    batch, raw = migration._load_batch(root, locator["batch"]["path"])
    sp._projection_require(migration._digest(raw) == locator["batch"]["sha256"],
                           "summaries proved batch changed during input loading")
    fingerprints = {}
    for ref in view["dependency_fingerprints"]:
        sp._projection_require(ref["path"] not in fingerprints or fingerprints[ref["path"]] == ref["sha256"],
                               "summaries conflicting migration dependency fingerprints")
        fingerprints[ref["path"]] = ref["sha256"]
    sp._projection_require(fingerprints.get(locator["batch"]["path"]) == locator["batch"]["sha256"],
                           "summaries batch not in validated input snapshot")
    selected = {source["source_identity"]: source for source in view["source_refs"]}
    sp._projection_require(len(selected) == len(view["source_refs"]), "summaries duplicate selected identity")
    clauses = []
    for entry in batch["entries"]:
        source = selected.get(entry["source_identity"])
        sp._projection_require(source is not None and source["source_adr"] == entry["source_adr"]
            and source["clause_sha256"] == entry["clause"]["sha256"], "summaries selected clause identity mismatch")
        if entry["disposition"] == "historical-implementation":
            clauses.append(dict(source_identity=entry["source_identity"],
                source_ref={k: source[k] for k in ("path", "source_adr", "clause_sha256")},
                destination=entry["historical_destination"], replacements=entry["replacement_handles"]))
    sp._projection_require(set(selected) == {entry["source_identity"] for entry in batch["entries"]},
                           "summaries selected source roster mismatch")
    return sorted(clauses, key=lambda row: row["source_identity"]), locator


def _summary_destinations(root: Path, *, _source_io=None) -> tuple[Path, dict]:
    """Read-only path preflight; never creates a destination or temporary file."""
    tree = sp.resolve_tree(root, _source_io=_source_io)
    paths = {name: tree / "adrs/summaries" / name for name in
        ("rule-table.md", "resolver.json", "implementation-map.md", "_meta.json")}
    for path in paths.values():
        if sp._source_resolve(tree, _source_io) not in sp._source_resolve(path.parent, _source_io).parents:
            raise OSError("summaries destination is not contained under the validated tree")
        if _source_io is None:
            blocked = (path.is_symlink() or (path.exists() and not path.is_file()) or
                       path.with_suffix(path.suffix + ".tmp").exists() or
                       path.with_suffix(path.suffix + ".tmp").is_symlink())
        else:
            metadata = _source_io.metadata(path)
            temporary = _source_io.metadata(path.with_suffix(path.suffix + ".tmp"))
            blocked = (metadata is not None and not stat.S_ISREG(metadata['st_mode'])) or temporary is not None
        if blocked:
            raise OSError("summaries destination is symlinked or has an owned temporary path")
    return tree, paths


def _load_summary_inputs(root: Path, manifest: dict) -> tuple[dict, dict, dict | None, dict | None]:
    """Public proof boundary followed by one complete, contained input snapshot."""
    import implementation_migration as migration
    tree, paths = _summary_destinations(root)
    view = migration.authority_view(root)
    adrs = sp.adrs_dir(root)
    concerns = manifest.get("concerns_enabled") or []
    if not adrs.is_dir() and not ("adrs" not in concerns and "observations" in concerns):
        raise FileNotFoundError(f"{adrs} not found")
    summaries = adrs / "summaries"
    declared = sp.declared_input_domain(summaries)
    sp._projection_require(not isinstance(declared, list) or "implementation-migration" not in declared
        or view["state"] == "published", "summaries declared migration input unavailable")
    gf = sp.governs_from(manifest)
    reviews = sp.read_reviews(adrs)
    observations = sp.observations_source(root, manifest)
    records = sp.collect_records(adrs, governs_from=gf, observations=observations)
    aliases = sp.collect_observation_alias_rows(observations, records)
    clauses, locator = _historical_summary_clauses(root, view)
    metadata = dict(adr_frontmatter_sha256=sp.adr_frontmatter_sha256(adrs),
        backfill_reviews_sha256=sp.backfill_reviews_sha256(adrs),
        input_domain=["adrs", "backfill-reviews"] + (["observations", "survey-receipts"] if observations is not None else []),
        observations_sha256=sp.observations_sha256(observations) if observations is not None else None,
        survey_receipts_sha256=sp.survey_receipts_sha256(observations) if observations is not None else None,
        schema="6", tool="summarize-adrs.py")
    loaded = dict(tree=tree, paths=paths, records=records, reviews=reviews, aliases=aliases,
        governs_from=gf, implementation_map=sp.build_implementation_map(adrs, sp.runs_dir(root), repo_root=root),
        metadata=metadata, archived_handles=sorted(sp.archived_handles(adrs)),
        signed_handles=sorted(sp.signed_reconciliation_handles(adrs)), historical_clauses=clauses,
        publication_history=[{k: row[k] for k in ("path", "sha256", "publication_commit")}
                             for row in view["publications"][:-1]])
    pending = actual = None
    if locator is not None:
        actual = {k: view["publications"][-1][k] for k in ("path", "sha256", "publication_commit")}
        pending = dict(path=actual["path"], sha256=actual["sha256"], close_identity={k: locator[k] for k in
            ("batch", "book_id", "run_id", "book_content_hash", "slot", "gate_prompt")})
        # U8 validated these exact source/dependency bytes; recheck after every loaded reader.
        migration._unchanged_inputs(root, view["dependency_fingerprints"] +
                                   [{"path": actual["path"], "sha256": actual["sha256"]}])
    return loaded, view, pending, actual


def build(root: Path, *, manifest: dict | None = None) -> dict[Path, str]:
    """Build four schema-six outputs using fresh production authority proof."""
    root = Path(root).absolute()
    if manifest is None:
        manifest = sp.read_manifest(root)
    refusal = sp.declared_domain_refusal(root, manifest, sp.adrs_dir(root) / "summaries")
    sp._projection_require(refusal is None, "summaries declared input domain unavailable")
    loaded, view, pending, actual = _load_summary_inputs(root, manifest)
    plan = sp._plan_summary_outputs(loaded, view, pending)
    proven = sp._committed_publication_ref(pending, actual) if pending is not None else None
    return {path: content.decode("utf-8") for path, content in sp._materialize_summary_outputs(plan, proven).items()}


def _read_phase(root: Path) -> dict[Path, str] | int:
    """Refuse or build without writing; an int is the exit code of a refusal."""
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
        _print_remedy(exc)
        return 2
    if refusal is not None:
        sys.stderr.write(f"summarize-adrs: refusing to rewrite: {refusal}\n")
        _print_remedy(refusal)    # a migration refusal's remedy rides on the reason
        return 2

    try:
        wanted = build(root, manifest=manifest)
    except sp.GovernsValidationError as exc:
        print(json.dumps({"validation_errors": exc.problems}, sort_keys=True))
        return 1
    except Exception as exc:
        sys.stderr.write(f"summarize-adrs: {type(exc).__name__}: {exc}\n")
        _print_remedy(exc)
        return 2
    return wanted


def _print_remedy(exc) -> None:
    """Print a migration refusal's path-free next step on stderr, beside the unchanged code."""
    remedy = getattr(exc, "remedy", None)
    if isinstance(remedy, str) and remedy:
        sys.stderr.write(json.dumps({"remedy": remedy}) + "\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Regenerate the summaries projection (adrs/summaries/) from ADR governs blocks + run snapshots."
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--repo-root", default=".")
    args = ap.parse_args(argv)
    root = Path(args.repo_root).resolve()

    # The read phase writes nothing, so its repeated authority reads share one proof.
    try:
        import implementation_migration as migration
    except Exception as exc:
        sys.stderr.write(f"summarize-adrs: {type(exc).__name__}: {exc}\n")
        _print_remedy(exc)
        return 2
    with migration.read_scope():
        wanted = _read_phase(root)
    if isinstance(wanted, int):
        return wanted

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
        _print_remedy(exc)
        return 2
    print(json.dumps({"written": written}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
