# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "httpx>=0.27",
#     "pyyaml>=6.0",
# ]
# ///
# `httpx` is REACHED, not called: this gate reads the doctrine projection,
# whose observations leg resolves survey-batch state through `survey_sheet`,
# which imports `crux.arch.recover` and so pulls the `crux` package, whose
# council module imports the LLM router, which imports httpx. Declaring only
# PyYAML passes in the dev venv, where httpx is ambient, and under
# `uv run --no-project` turns this gate into a reported CAPABILITY error —
# which its caller skips rather than fails, so the gate measures nothing.
"""check-doctrine-reconciliation.py — the doctrine consistency GATE.

Per ADR-0090 req 2: a domain renders its belief only when every
structurally-declared (ratified-invariant, governs-handle) pairing in it has a
digest-current reconciliation record with verdict `compatible` or `reconciled`.
Any pairing that is un-adjudicated, digest-stale, or `collision` marks that
DOMAIN broken. This gate fails (exit 1) when any domain is broken.

This is a thin CLI wrapper over the pairing/rollup logic in
`doctrine_projection.py` — a GATE, not a regenerator. It writes nothing, so it
carries NO row in the repo-root AGENTS.md regenerative-outputs roster and is
deliberately NOT named compile-*.py or generate-*.py, mirroring
check-governs-coverage.py.

It also carries the S1 warn lane (ADR-0090 req 2): a ratified invariant that
seeds ZERO candidate pairings goes undetected by this gate — whether because
`related_adrs` is empty, or because every ADR it names carries no active
`governs` handle — so a contradiction it might name never surfaces. That is a
WARNING — it never changes the exit code — reported under the `warnings` key,
matching validate-catalog.py's warning contract.

Usage:
  check-doctrine-reconciliation.py [--repo-root DIR]

Exit: 0 no broken domain (warnings may be present) · 1 one or more broken
domains (key `broken_domains`), or an input validation error (key
`validation_errors`) · 2 crash (stderr).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import summaries_projection as sp  # noqa: E402
import doctrine_projection as dp  # noqa: E402


def _warnings(invariants: list[dict], records: list[dict],
              tree_name: str = "bionic") -> list[dict]:
    """S1: a ratified invariant that seeds ZERO candidate pairings — detection
    coverage depends on that seed, so warn (never fail). Two ways to seed
    nothing: `related_adrs` is empty, or every ADR it names carries no active
    `governs` handle (a typo'd id, a since-superseded ADR, or an ADR that never
    adopted `governs`). Computed from the SAME `dp.candidate_pairings` the
    build path uses, so this warning can never disagree with what actually got
    seeded."""
    seeded = {p["invariant"] for p in dp.candidate_pairings(records, invariants, tree_name)}
    out = []
    for inv in invariants:
        if inv["ratification"] == "ratified" and inv["id"] not in seeded:
            out.append({
                "invariant": inv["id"],
                "warning": "ratified invariant seeds no doctrine pairing — "
                "related_adrs is empty, or names only ADRs with no active "
                "governs handle — so any contradiction it names is outside "
                "the detected scope",
            })
    out.sort(key=lambda w: w["invariant"])
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Gate: no governs domain is BROKEN — every ratified-invariant "
        "x governs-handle pairing has a digest-current compatible/reconciled record."
    )
    ap.add_argument("--repo-root", default=".")
    args = ap.parse_args(argv)
    root = Path(args.repo_root).resolve()
    try:
        manifest = sp.read_manifest(root)
        adrs = sp.adrs_dir(root)
        governs_from = sp.governs_from(manifest)
        # ADR-0095 requirement 5: this gate and `compile-doctrine.py` are two
        # readers of one projection, so they resolve the input domain through
        # the same `sp.observations_source` and thread the same tree_name /
        # repo_root into `build_domain_entries`. A gate reading a narrower
        # domain than the regenerator reports clean on a tree the regenerator
        # marks BROKEN.
        observations = sp.observations_source(root, manifest)
        records = sp.collect_records(adrs, governs_from=governs_from,
                                     observations=observations)
        invariants = dp.read_invariants(root)
        reconciliations = dp.read_reconciliations(root)
        bindings = sp.read_run_bindings(sp.runs_dir(root))
        entries = dp.build_domain_entries(records, invariants, reconciliations,
                                          bindings, governs_from,
                                          tree_name=sp.tree_name(root),
                                          repo_root=root)
    except (sp.GovernsValidationError, dp.DoctrineValidationError) as exc:
        print(json.dumps({"validation_errors": exc.problems}, sort_keys=True))
        return 1
    except Exception as exc:
        sys.stderr.write(
            f"check-doctrine-reconciliation: {type(exc).__name__}: {exc}\n")
        return 2

    broken = dp.broken_domains(entries)
    warnings = _warnings(invariants, records, sp.tree_name(root))
    if broken:
        payload = {"broken_domains": broken, "domains": len(entries)}
        if warnings:
            payload["warnings"] = warnings
        print(json.dumps(payload, sort_keys=True))
        return 1
    clean = {"broken_domains": [], "domains": len(entries)}
    if warnings:
        clean["warnings"] = warnings
    print(json.dumps(clean, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
