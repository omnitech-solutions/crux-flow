#!/usr/bin/env python3
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyyaml>=6.0"]
# ///
"""check-governs-coverage.py — the governs-coverage GATE.

Per ADR-0085 (governs block) and its `adr.governs_from` cohort boundary: an
ADR numbered >= `governs_from` is COVERED iff it carries a non-empty `governs`
list OR is listed in the optional `adr.governs_exempt` manifest key. An ADR
numbered below `governs_from`, or every ADR when `governs_from` is
absent/null, is out of scope.

This is a thin CLI wrapper over the coverage logic in `summaries_projection.py`
(the frozen shared core) — a coverage GATE, not a regenerator. It writes
nothing, so it carries no row in the repo-root AGENTS.md regenerative-outputs
roster and is deliberately NOT named generate-*.py.

This script does not import `yaml` itself, but `summaries_projection.py` does
(to parse ADR frontmatter and run snapshots) — and PEP 723 inline metadata
applies only to the script `uv run` is invoked on, never to a module that
script imports. Without this block, `uv run` would resolve no dependency for
this script and `summaries_projection`'s `import yaml` would fail in any clean
downstream environment lacking a PyYAML install of its own. The PEP 723
block below matches `summarize-adrs.py` / `summaries_projection.py`'s exact
form so all four summaries scripts stay in lock-step.

Usage:
  check-governs-coverage.py [--repo-root DIR]

On the clean path it prints a JSON summary — `{"uncovered": [],
"cohort_size": N}` — to stdout, matching the sibling summaries drivers
(summarize-adrs.py, lint-governs-references.py), which emit JSON on their
clean paths too.

Exit: 0 every in-cohort ADR covered (or cohort empty -> vacuous pass) ·
      1 one or more uncovered ADRs, a governs-block validation error
      (malformed entry / out-of-enum provenance — `sp.GovernsValidationError`;
      JSON on stdout, key `validation_errors`), OR one or more ADR-0088
      backfill-contract problems (key `backfill_errors`; note these can fire
      with `uncovered` empty) · 2 crash (stderr). The clean-path JSON may also
      carry an optional `backfill` counts block (backfilled / no_rule / pending).

An optional `warnings` list rides BESIDE whichever verdict above is reached,
on the clean path and on the exit-1 payload alike. It is ADVISORY and never
changes the exit code: each row reports a `governs` rule longer than the
recommended maximum, carrying the ADR path, the handle, the measured length,
that maximum, and guidance for shortening it. One further row reports the
rule-length baseline key absent, which leaves the advisory inert. A reader
keying off the exit code sees no difference; a reader keying off `warnings`
must not treat its presence as a failure.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import summaries_projection as sp


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Gate: every ADR >= adr.governs_from carries a governs "
        "block or a recorded exemption."
    )
    ap.add_argument("--repo-root", default=".")
    args = ap.parse_args(argv)
    root = Path(args.repo_root).resolve()
    try:
        manifest = sp.read_manifest(root)
        adrs = sp.adrs_dir(root)
        report = sp.coverage(adrs, manifest)
        # ADR-0088: the backfill contract (at-most-once ledger, frozen cohort,
        # completion marker, receipts x log x journal cross-validation).
        # Inert — ([], None) — until the tree snapshots its cohort.
        backfill_errors, backfill_info = sp.backfill_problems(root, adrs, manifest)
        # ADVISORY, and it rides whichever verdict the checks above reach. It never raises and
        # never contributes to the exit code: a `governs` rule being long is a signal to its
        # author, not a defect in the corpus. See `sp.rule_length_advisories`.
        warnings = sp.rule_length_advisories(adrs, manifest)
        if not sp.rule_length_baseline(manifest)[1]:
            # An ABSENT baseline leaves the advisory inert, which is correct for a tree that has
            # not snapshotted yet and invisible if nobody says so. One row makes the inert state
            # discoverable from this tool's own output rather than only from documentation.
            warnings.append({
                # The manifest's basename. NOT built from a hardcoded tree directory: this
                # script ships, and the tree's name is configurable per repository.
                "file": "manifest.yml",
                "handle": None,
                "error": f"adr.{sp.RULE_LENGTH_BASELINE_KEY} is absent or is not a list, so the "
                         "governs rule-length advisory is inert. Snapshot this tree's existing "
                         "ADR ids into that key once to enable it; an empty list enables it for a "
                         "tree with no ADRs yet.",
            })
    except sp.GovernsValidationError as exc:
        print(json.dumps({"validation_errors": exc.problems}, sort_keys=True))
        return 1
    except Exception as exc:
        sys.stderr.write(f"check-governs-coverage: {type(exc).__name__}: {exc}\n")
        return 2
    if report["uncovered"] or backfill_errors:
        payload = dict(report)
        if backfill_errors:
            payload["backfill_errors"] = backfill_errors
        if backfill_info is not None:
            payload["backfill"] = backfill_info
        if warnings:
            # Present beside a FAILING verdict too: the drift contract requires a warning to be
            # readable beside any verdict, and an author whose coverage failed still wants to know
            # a rule they just wrote is over length. It does not change this return.
            payload["warnings"] = warnings
        print(json.dumps(payload, sort_keys=True))
        return 1
    clean = {"uncovered": [], "cohort_size": len(report["cohort"])}
    if backfill_info is not None:
        # The completion marker absent: the block is informational; contract
        # violations above still fail.
        clean["backfill"] = backfill_info
    if warnings:
        clean["warnings"] = warnings
    print(json.dumps(clean, sort_keys=True))
    # UNCHANGED, deliberately. A non-empty `warnings` list at exit 0 is a clean pass worth
    # reporting with its warning, never a failure — the same posture the catalog validator takes
    # with its six standing advisory rows.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
