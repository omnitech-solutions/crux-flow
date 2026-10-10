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
"""compile-doctrine.py — the vendored regenerator for <docs_dir>/adrs/doctrine/.

Per ADR-0090, doctrine is a deterministic, model-free templated projection of
the summaries projection: one entry per `governs` domain, reconciled against the
ratified invariants, behind ONE byte-stable drift gate. This script is a thin
driver mirroring summarize-adrs.py: build the two artifacts, compare-or-write
them, report drift/write status as JSON. All shared machinery lives in
`doctrine_projection.py` (which imports the frozen `summaries_projection.py`).

  - index.md     one section per governs domain (rules + source_status +
                 disposition + basis +
                 reconciliation status + state), an exempt-ADR roster, and a
                 provenance block of input digests
  - _meta.json   the input-digest + schema block for the projection

`adrs/doctrine/reconciliations.yml` is a compile INPUT, not an output of this
script: it is read (via `doctrine_projection.read_reconciliations`) and its
bytes are stamped into the provenance digest, but only `signoff-reconciliation.py`
ever writes it. This regenerator never touches it.

Determinism is the contract: no model in the path, no wall-clock value in the
output, every artifact sorts. A byte-for-byte re-run is always clean.

Writes are atomic and fail-closed: each artifact is written to <path>.tmp and
os.replace()d into place; a symlinked target or tmp path, and any intermediate
directory resolving outside the validated tree dir, are refused (exit 2) before
the write.

Usage:
  compile-doctrine.py [--repo-root DIR]        rewrite adrs/doctrine/{...}
  compile-doctrine.py --dry-run [...]          exit 1 + JSON diff if drift

Exit: 0 clean/written · 1 drift (JSON on stdout), or an input validation error
(malformed governs entry / malformed reconciliation ledger; JSON on stdout, key
`validation_errors`) · 2 env error (stderr).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import summaries_projection as sp  # noqa: E402
import doctrine_projection as dp  # noqa: E402
from untrusted import MESSAGE_LIMIT, redact  # noqa: E402


def _atomic_write_text(path: Path, body: str, *, contained_under: Path) -> None:
    """Atomic UTF-8 text write with no platform newline translation.

    Writes to <path>.tmp then os.replace()s into place. Bytes are written
    directly so Python does NOT translate '\\n' -> '\\r\\n' on Windows; that
    translation would shift the file's sha256 across platforms and break the
    byte-stable regenerative output contract.

    [SECURITY:S5] Every write target's RESOLVED parent directory must sit under
    `contained_under` — the validated tree dir. This guard covers the
    INTERMEDIATE directories (a symlink at <tree>/adrs or <tree>/adrs/doctrine
    leaves every entry under it a real file while steering the write outside the
    repo root); the leaf checks below guard the target and its tmp file. Shared
    posture with summarize-adrs.py / signoff-backfill.py — same guards, same
    order, same exception type (OSError); each names its own subject in its
    messages ("doctrine projection content" here). Change one, change all.
    """
    root_resolved = Path(contained_under).resolve()
    parent_resolved = path.parent.resolve()
    if parent_resolved != root_resolved \
            and root_resolved not in parent_resolved.parents:
        raise OSError(
            f"refusing to write {path}: the parent directory resolves to "
            f"{parent_resolved}, which is not contained under the validated "
            f"tree dir {root_resolved} — a symlinked intermediate directory "
            "would put doctrine projection content outside the tree"
        )
    tmp = path.with_suffix(path.suffix + ".tmp")
    for label, candidate in (("target", path), ("temporary file", tmp)):
        if candidate.is_symlink():
            raise OSError(
                f"refusing to write {path}: the {label} {candidate} is a symlink — "
                "writing through it would put doctrine projection content in the "
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
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise


def build(root: Path, *, manifest: dict | None = None) -> dict[Path, str]:
    """Build exactly two outputs after fresh proof and complete content validation."""
    root = Path(root).absolute()
    if manifest is None:
        manifest = sp.read_manifest(root)
    dp._doctrine_destinations(root)
    loaded, view, pending, actual = dp._load_doctrine_inputs(root, manifest)
    plan = dp._plan_doctrine_outputs(loaded, view, pending)
    proven = sp._committed_publication_ref(pending, actual) if pending is not None else None
    return {path: content.decode("utf-8") for path, content in dp._materialize_doctrine_outputs(plan, proven).items()}


def _print_remedy(exc) -> None:
    """Print a migration refusal's path-free next step on stderr, beside the unchanged code."""
    remedy = getattr(exc, "remedy", None)
    if isinstance(remedy, str) and remedy:
        sys.stderr.write(json.dumps({"remedy": remedy}) + "\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Regenerate the doctrine projection (adrs/doctrine/) from "
        "the summaries projection reconciled against the ratified invariants."
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--repo-root", default=".")
    args = ap.parse_args(argv)
    root = Path(args.repo_root).resolve()

    try:
        wanted = build(root)
    except (sp.GovernsValidationError, dp.DoctrineValidationError) as exc:
        print(json.dumps({"validation_errors": exc.problems}, sort_keys=True))
        return 1
    except Exception as exc:
        sys.stderr.write(f"compile-doctrine: {type(exc).__name__}: {exc}\n")
        _print_remedy(exc)
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
            parent_resolved = path.parent.resolve()
            if parent_resolved != root_resolved \
                    and root_resolved not in parent_resolved.parents:
                raise OSError(
                    f"refusing to write {path}: the parent directory resolves "
                    f"to {parent_resolved}, which is not contained under the "
                    f"validated tree dir {root_resolved} — a symlinked "
                    "intermediate directory would put doctrine projection "
                    "content outside the tree"
                )
            path.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write_text(path, wanted[path], contained_under=tree_dir)
            written.append(str(path.relative_to(root)))
    except Exception as exc:
        sys.stderr.write(f"compile-doctrine: {type(exc).__name__}: {exc}\n")
        _print_remedy(exc)
        return 2
    print(json.dumps({"written": written}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
