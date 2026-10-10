#!/usr/bin/env python3
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyyaml>=6.0", "httpx>=0.27"]
# ///
# Shared proof needs PyYAML; observation source admission can reach the httpx
# import through the runtime package. Inline metadata owns both dependencies.
"""check-journal-reference.py — the CLN-JR-1 reference decision, as a command.

`cleanup-campsite` CLN-JR-1 invokes this once per artifact it found in a
recent `adr`/`promptbook`/`schema` log op. It reads one month file and, when
the tree has one, the summaries resolver; it writes nothing.

The decision itself lives in `crux/scripts/journal_reference.py` and is
shared, never re-implemented here — this script owns the filesystem guards
and the exit contract only.

Usage:
  check-journal-reference.py --artifact ADR-0111 --month 2026-09 [--repo-root DIR]

Exit: 0 the artifact is referenced · 1 it is not (JSON on stdout, carrying
`rejected` and `unverifiable` so a finding can say why), or bounded proof/history
refusal carrying `authority: none` and `limit` · 2 environment or
usage failure (stderr).

An ABSENT RESOLVER is exit 1 with `resolver_available: false`, never exit 2:
a tree with no summaries projection is the normal downstream state, and the
wiki-link lane still decides.
This unavailable lane applies to original trees admitted by shared authority.
Published history requires valid proof and canonical maps before either citation lane.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bionic_config import BionicConfigError, load_config  # noqa: E402
from journal_reference import artifact_references  # noqa: E402
from untrusted import MESSAGE_LIMIT, redact  # noqa: E402

MONTH_RE = re.compile(r"^[0-9]{4}-(0[1-9]|1[0-2])\Z")
#: `ADR-NNNN`, `OBS-NNNN`, or `PB-NNNN-<slug>` — the artifact shapes CLN-JR-1
#: lifts out of a log subject. A closed grammar, so a forged subject cannot
#: steer this script at an arbitrary path fragment.
ARTIFACT_RE = re.compile(r"^(?:(?:ADR|OBS)-[0-9]{4}|PB-[0-9]{4}-[a-z0-9-]+)\Z")


def _journal_resolver(root: Path, tree: Path) -> tuple[dict | None, dict | None]:
    """Fresh proof owns history; unavailable original projections retain their lane."""
    import implementation_migration as migration
    import summaries_projection as sp
    view = migration.authority_view(root)
    path = tree / "adrs/summaries/resolver.json"
    parsed = None
    if path.is_file() and not path.is_symlink():
        try:
            text, _ = migration._bounded_read(root, path, migration._reference_reader().MAX_FILE_BYTES)
            def unique(pairs):
                result = {}
                for key, value in pairs:
                    if key in result: raise ValueError("duplicate resolver key")
                    result[key] = value
                return result
            value = json.loads(text, object_pairs_hook=unique)
            parsed = value if isinstance(value, dict) else None
        except (OSError, UnicodeError, ValueError, RecursionError):
            parsed = None
    if view["state"] == "original":
        migration.ap.require(not parsed or not any(parsed.get(key) for key in
            ("historical_slugs", "historical_displacements", "historical_clauses")),
            "journal-unproved-history-refused")
        return parsed, None
    migration.ap.require(parsed is not None, "journal-published-resolver-unavailable")
    manifest = sp.read_manifest(root)
    observations = sp.observations_source(root, manifest)
    records = sp.collect_records(tree / "adrs", sp.governs_from(manifest), observations)
    reviews = sp.read_reviews(tree / "adrs")
    aliases = sp.collect_observation_alias_rows(observations, records)
    overlay = sp._summary_authority_records(records, view, sp.removed_handles(reviews), aliases)
    migration.ap.require(all(parsed.get(key) == overlay[key] for key in
        ("slugs", "retired_slugs", "historical_slugs", "historical_displacements")),
        "journal-resolver-history-mismatch")
    sources = {row["source_identity"]: row for row in view["source_refs"]}
    historical = {slug: {key: row[key] for key in ("source_handle", "source_identity", "destination")}
        | {"source_ref": {key: sources[row["source_identity"]][key]
                         for key in ("path", "source_adr", "clause_sha256")}}
        for slug, row in overlay["historical_slugs"].items()}
    edges = {}
    for target, rows in overlay["historical_displacements"].items():
        edges.setdefault(sp.slug_of(target), []).extend({key: row[key] for key in
            ("target_handle", "source_displacer", "source_identity", "source_ref")} for row in rows)
    migration._unchanged_inputs(root, view["dependency_fingerprints"] + [
        {key: row[key] for key in ("path", "sha256")} for row in view["publications"]])
    return parsed, dict(historical_slugs=historical, historical_displacements=edges)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Does a journal month reference an artifact?")
    ap.add_argument("--artifact", required=True)
    ap.add_argument("--month", required=True, help="YYYY-MM")
    ap.add_argument("--repo-root", default=".")
    args = ap.parse_args(argv)

    if not MONTH_RE.fullmatch(args.month):
        sys.stderr.write(f"check-journal-reference: {args.month!r} is not a valid YYYY-MM month\n")
        return 2
    if not ARTIFACT_RE.fullmatch(args.artifact):
        sys.stderr.write(
            f"check-journal-reference: {redact(args.artifact, quoted=True)} is not an "
            "ADR-NNNN, OBS-NNNN or PB-NNNN-<slug> artifact id\n")
        return 2

    try:
        root = Path(args.repo_root).resolve()
        config = load_config(root, require_tree=True)
        tree = config.docs_root
        month_file = tree / "journal" / f"{args.month}.md"
        if month_file.is_symlink() or not month_file.is_file():
            raise OSError(
                f"{config.docs_dir}/journal/{args.month}.md is not a regular file")
        text = month_file.read_text(encoding="utf-8")
    except (BionicConfigError, OSError, UnicodeDecodeError) as exc:
        sys.stderr.write(
            f"check-journal-reference: {redact(exc, quoted=False, limit=MESSAGE_LIMIT)}\n")
        return 2

    try:
        import implementation_migration as migration
        import summaries_projection as sp
        resolver, history = _journal_resolver(root, tree)
    except (migration.Refused, sp.GovernsValidationError) as exc:
        print(json.dumps(dict(artifact=args.artifact, month=args.month, authority="none",
            limit=exc.code if isinstance(exc, migration.Refused) else "journal-source-model-invalid"), sort_keys=True))
        return 1
    except (OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
        sys.stderr.write(f"check-journal-reference: {redact(exc, quoted=False, limit=MESSAGE_LIMIT)}\n")
        return 2

    result = artifact_references(text, args.artifact, resolver, _history=history)
    result["month"] = args.month
    print(json.dumps(result, sort_keys=True))
    return 0 if result["referenced"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
