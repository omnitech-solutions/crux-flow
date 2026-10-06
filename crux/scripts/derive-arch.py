#!/usr/bin/env python3
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "httpx>=0.27",
#     "pyyaml==6.0.3",
#     "tree-sitter==0.26.0",
#     "tree-sitter-elixir==0.3.5",
#     "tree-sitter-ruby==0.23.1",
#     "tree-sitter-swift==0.7.3",
#     "tree-sitter-typescript==0.23.2",
# ]
# ///
# NOTE: derive-arch.py itself is stdlib-only, but importing `crux.arch` triggers the
# package's eager `crux/__init__.py` (council -> llm_caller -> httpx, per the
# ADR-0087 gateway consolidation — no provider SDKs remain), so the runtime dep
# set matches the other `crux.*`-importing scripts (spawner, runbook). See
# docs/AGENTS.md §10.A.
#
# `pyyaml` is pinned here for a different reason than the one transport dep:
# it changes the OUTPUT, not just the import. The universal `decision-index`
# concern declares `yaml`, so EVERY pack declares it and `core.parser_pins`
# records the resolved PyYAML version into the byte-compared `tool_pins`.
#
# It is pinned EXACTLY, for the reason the tree-sitter paragraph below gives: a
# range would let an upstream release move `_meta/manifest.json` on an
# unmodified tree, which is ADR-0096 clause 1 running backwards.
# `ParserPinLockStepTests` enforces it, and it self-enrols over the pack
# registry rather than over a list — which is why declaring the parser is what
# pulled PyYAML into the exact-pin discipline.
#
# The frontmatter reader prefers PyYAML, and its stdlib `_yaml_min` fallback
# parses differently enough to produce a DIFFERENT spine hash from one source
# tree. That fallback is no longer REACHABLE from a derive: the declaration
# makes `resolve_declared_parsers` refuse first. Without this line, `uv run`
# resolved an isolated env with no PyYAML, so the canonical invocation reported
# drift on a pristine checkout while `python3` reported clean — two hashes,
# one tree. ADR-0060 Decision point 4 requires environment-independent output,
# so the dependency that decides the parse branch belongs in the dependency
# list.
#
# The five `tree-sitter` lines are pinned for the same reason as `pyyaml` and
# under ADR-0096 clause 1: concerns of the ruby, node, elixir and swift packs
# declare the input class `parser`, and each grammar decides what its pack
# renders.
# A machine that cannot resolve a declared grammar takes clause 2's environment
# lane — exit 2 with nothing written — rather than a degraded spine, so the
# canonical `uv run` invocation has to supply it. The pins are EXACT, not
# ranges, because `core.parser_pins` records the RESOLVED version into the
# byte-compared `tool_pins` map: under a range, a new upstream patch moves the
# manifest bytes on an unmodified tree, and the drift gate's "regenerate and
# commit" remedy then flips that value back and forth between machines. `==`
# makes the recorded pin a function of this declaration, so a grammar upgrade
# is a deliberate edit here — which is what clause 1 asks for. The same strings
# appear in pyproject.toml, in the corpus driver's PEP 723 block and in
# survey.py's; `ParserPinLockStepTests` holds every declaration site and both
# release gates to the same values, and its roster is the count.
"""derive-arch.py — regenerate the arch concern spine (ADR-0060, SP-1 + SP-2).

The arch concern IS the project's derived architecture: a deterministic spine
(data-model, api-surface, module-graph, decision-index) plus a synthesized
`overview.md` kept honest by a SHA-256 hash-stamp drift-gate.

Usage:
  derive-arch.py [--docs-dir DIR] [--repo-root DIR]     regenerate <docs_dir>/arch/
  derive-arch.py --dry-run [...]                         drift check; exit 1 if drift
  derive-arch.py --strict [...]                          require EVERY concern populated

Exit codes (sibling of extract-code-docs.py --dry-run):
  0  clean (no drift on --dry-run; wrote the tree otherwise)
  1  findings, in either of two channels, both on stdout as valid JSON:
       * drift detected on --dry-run (the `drift` list); or
       * ADR-0096 clause 5's strict gate — a concern named by the tree
         manifest's `arch.require`, or every concern under --strict, whose
         RECORDED verdict is not `populated` (the `strict_failures` list).
     No new code is minted for the second: exit 1 already means findings, so a
     strict failure stays distinguishable from an environment failure.
  2  no verdict (surface stderr) — two lanes share this code:
       * environment / usage error, with no in-repo remedy; or
       * a deliberate refusal, when a spine file that projects a gated
         artifact finds that artifact is not what its own regenerator
         produces. Nothing is written. stderr carries StaleProjectionInput
         and names the input; the remedy is to run that input's regenerator,
         commit, then re-derive. Never reported as document drift.

PyYAML parses frontmatter, and the engine no longer degrades to a stdlib parser
without it. Every registered pack declares `yaml` through the universal
`decision-index` concern, so a derive on a machine that cannot resolve it exits 2
with nothing written rather than computing a different spine hash. Run this
through `uv run`, which supplies the pinned version.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # crux/scripts on path

from bionic_config import BionicConfigError, load_config, load_yaml  # noqa: E402

# The `crux.arch` import pulls the eager `crux/__init__.py` and with it the LLM
# transport (httpx, per the PEP 723 block above — the provider SDKs were retired
# by the ADR-0087 gateway consolidation). When httpx is absent the import
# raises, and an uncaught exception exits 1 — the code this script reserves for
# DRIFT. A caller reading exit codes would then be told the arch tree is stale
# by a process that never looked at it, with an empty payload on stdout. Exit 2
# is this script's documented capability lane, so the import is caught and
# routed there instead.
#
# The catch is `Exception`, not `ImportError`: a broken or half-installed
# dependency can fail at module level with something else entirely (an
# `AttributeError` raised while a provider SDK initializes, say), and every one
# of those is the same environment failure wearing a different exception class.
# Narrowing to `ImportError` would let those escape to exit 1 and be misread as
# drift. `SystemExit` and `KeyboardInterrupt` derive from `BaseException` and so
# still pass through untouched.
try:
    from crux.arch.derive import CONCERNS, coverage_table, derive, dry_run  # noqa: E402
    # ADR-0096 clause 7's annotation, imported HERE and not by the deriver. The
    # recorded channel stays git-free: a byte-compared tree that varied with the
    # commit graph would report drift on a fresh clone and clean on the machine
    # that wrote it. The reported channel is where the commit graph belongs.
    from crux.arch.staleness import advisory_lines, annotate  # noqa: E402
except Exception as exc:  # capability lane, not a drift finding
    print(f"cannot import the arch deriver: {exc}", file=sys.stderr)
    print(
        "This is an environment failure, not a drift finding. The deriver needs "
        "the dependencies named in this script's PEP 723 block. Run it as "
        "`uv run crux/scripts/derive-arch.py`, or install httpx and pyyaml "
        "into the interpreter you are using.",
        file=sys.stderr,
    )
    raise SystemExit(2) from exc


def _print_table(report: list) -> None:
    """The per-concern coverage table, on stderr (ADR-0096 clauses 2 and 9)."""
    if report:
        print(coverage_table(report), file=sys.stderr)


#: The tree-manifest key naming the concerns this tree requires (clause 5).
REQUIRE_KEY = "arch.require"


def required_concerns(target: Path) -> tuple[str, ...]:
    """Read `arch.require` from `<docs_dir>/manifest.yml`. Absent → empty.

    **This read lives here and not in `_build`, and that placement is
    load-bearing.** `_build` records every file it reads into `_meta/manifest.json`
    as provenance, so a `_build` that opened the tree manifest would put a
    GOVERNANCE-CONFIG file into the arch source set — and an edit to the required
    concerns would then move the provenance ledger and flap the drift gate.
    Clause 2's "every recorded verdict is a function of committed bytes alone" is
    a statement about the ARCH sources; which concerns a tree requires is not one
    of them. Keeping the read in the driver keeps the recorded channel a function
    of the code and the sources, and leaves the required set purely an exit-code
    question.

    Raises `ValueError` on a mis-shaped or unknown value. Fail closed: a required
    set naming a concern nothing answers would otherwise gate nothing while
    reading as though it gated something, which is the worst of the three
    available outcomes.
    """
    manifest = target / "manifest.yml"
    if not manifest.is_file():
        return ()
    try:
        raw = load_yaml(manifest.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"cannot parse {manifest}: {exc}") from exc
    if not isinstance(raw, dict):
        return ()
    arch = raw.get("arch")
    if arch is None:
        return ()
    if not isinstance(arch, dict):
        raise ValueError(f"{REQUIRE_KEY}: `arch` must be a mapping, "
                         f"got {type(arch).__name__}")
    value = arch.get("require")
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"{REQUIRE_KEY} must be a list of concern names, "
                         f"got {value!r}")
    unknown = [v for v in value if v not in CONCERNS]
    if unknown:
        raise ValueError(f"{REQUIRE_KEY} names concerns that do not exist: "
                         f"{unknown} (known: {list(CONCERNS)})")
    return tuple(value)


def _annotate(root: Path, report: list, cfg) -> list:
    """ADR-0096 clause 7's annotation, attached AFTER the gates have decided.

    The ordering is the guarantee. `derive` and `dry_run` apply the strict gate
    and compute drift before returning, so neither can have seen an annotation:
    strict does not depend on the commit graph because, structurally, it has not
    been told about it yet.

    Fail-open, and only here. An advisory that cannot be computed — a git this
    machine lacks, a repository shape this cannot read — must not turn a
    successful derive into exit 2. It is an advisory a human reads, never a gate,
    so its own failure is a warning and nothing more.
    """
    try:
        return annotate(root, report, cfg=cfg)
    except Exception as exc:                    # advisory-only: never a verdict
        print(f"staleness advisory unavailable: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return []


def _print_advisories(advisories: list) -> None:
    """The reported channel's annotations, on stderr with the table."""
    for line in advisory_lines(advisories):
        print(line, file=sys.stderr)


def _print_strict_failures(failures: list) -> None:
    """One line per failing concern, on stderr beside the table it belongs to."""
    for f in failures:
        print(f"strict: {f['concern']} is required by {f['required_by']} and its "
              f"recorded verdict is {f['verdict']}"
              + (f" ({f['stub_reason']}: expected {f['expected']}, "
                 f"found {f['found']})" if f["verdict"] == "stubbed" else ""),
              file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Derive the arch concern spine.")
    ap.add_argument("--dry-run", action="store_true",
                    help="report drift without writing; exit 1 if the tree is stale")
    ap.add_argument("--docs-dir", default=None, help="tree dir (default: from .bionic.yml or 'bionic')")
    ap.add_argument("--repo-root", default=".", help="repository root (default: cwd)")
    ap.add_argument("--strict", action="store_true",
                    help="require EVERY concern to be populated for this run; "
                         "exit 1 with the failing verdicts on stdout")
    args = ap.parse_args(argv)

    root = Path(args.repo_root).resolve()
    if not root.exists():
        print(f"repo-root does not exist: {root}", file=sys.stderr)
        return 2

    # Resolve per-repo config through the shared loader (ADR-0066 retires the
    # ad-hoc `.bionic.yml` regex read). The loader supplies docs_dir plus the
    # arch_stack pin and arch_extractors override map the deriver consumes.
    try:
        cfg = load_config(root)
    except BionicConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    docs_dir = args.docs_dir if args.docs_dir else cfg.docs_dir
    # ADR-0062: thread the configured decision-index curation mode through to
    # the deriver; absent a config key, "complete" preserves prior behavior.
    mode = getattr(cfg, "arch_decision_index_mode", "complete")
    # Security: docs_dir is attacker-influenceable (a committed .bionic.yml, or a
    # passed flag). Confine it to a relative path inside the repo so the derive can
    # never write arch/ outside root. Reject absolute paths, `..`, and symlink escapes.
    if Path(docs_dir).is_absolute() or ".." in Path(docs_dir).parts:
        print(f"docs_dir must be a relative path inside the repo: {docs_dir!r}", file=sys.stderr)
        return 2
    target = (root / docs_dir).resolve()
    if root not in target.parents and target != root:
        print(f"docs_dir resolves outside the repository root: {docs_dir!r}", file=sys.stderr)
        return 2
    if not target.exists():
        print(f"docs_dir not found: {root / docs_dir}", file=sys.stderr)
        return 2

    # ADR-0096 clause 2: the REPORTED channel. `report` is filled with one
    # record per concern — the recorded verdict plus its annotations — and goes
    # to stdout inside the envelope this script already emits, under a new
    # `coverage` key. The addition is strictly additive: no existing key is
    # removed or renamed, so `.github/workflows/check-arch-drift.yml` keeps
    # parsing `clean` and `drift` exactly as before. The human-readable table
    # goes to STDERR, because a table interleaved into stdout would break that
    # parse. Neither is written anywhere under `arch/`.
    report: list = []

    # Clause 5's required set, read HERE and not in `_build` — see
    # `required_concerns`. A mis-shaped or unknown value is a config error, which
    # is exit 2 (no verdict) exactly as a bad `.bionic.yml` already is.
    try:
        require = required_concerns(target)
    except ValueError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    # `strict_failures` is a SECOND finding channel beside `drift`, not a
    # replacement for it. `clean` keeps answering the drift question alone, so a
    # byte-identical tree is never reported as stale by a gate that found no
    # drift; the exit code is 1 when either channel has findings, because exit 1
    # already means findings and clause 5 mints no new code.
    failures: list = []
    gate = {"require": require, "strict": args.strict, "failures": failures}
    try:
        if args.dry_run:
            drift = dry_run(root, docs_dir, decision_index_mode=mode, cfg=cfg,
                            report=report, **gate)
            advisories = _annotate(root, report, cfg)
            print(json.dumps({"drift": drift, "clean": not drift, "coverage": report,
                              "strict_failures": failures},
                             indent=2, sort_keys=True))
            _print_table(report)
            _print_strict_failures(failures)
            _print_advisories(advisories)
            return 1 if (drift or failures) else 0
        written = derive(root, docs_dir, decision_index_mode=mode, cfg=cfg,
                         report=report, **gate)
        advisories = _annotate(root, report, cfg)
        print(json.dumps({"written": written, "coverage": report,
                          "strict_failures": failures},
                         indent=2, sort_keys=True))
        _print_table(report)
        _print_strict_failures(failures)
        _print_advisories(advisories)
        return 1 if failures else 0
    # Exit 2 is the no-verdict lane, and two things reach it. A crash or broken
    # environment is one. A StaleProjectionInput refusal is the other: a spine
    # file that projects a gated artifact declines rather than baking a stale
    # input into the spine. Both are handled here on purpose, because both mean
    # the same thing to a caller — no verdict was produced, so nothing may be
    # read as a document finding. The exception TYPE in the message is what tells
    # the two apart, which is why it is printed rather than just the text.
    except Exception as exc:
        print(f"derive-arch failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
