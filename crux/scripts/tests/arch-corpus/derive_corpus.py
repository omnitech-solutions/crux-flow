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
"""derive_corpus.py — run the CURRENT arch deriver over the fetched corpus.

Two jobs, one code path:

  * `--bless` writes `golden/<name>/` — the four spine files plus
    `_meta/coverage.json` exactly as the deriver produced them. These are
    baseline goldens: they record what the packs produce today, warts included.
  * without `--bless` it derives and compares, printing a JSON report with the
    per-repo, per-concern numbers the baseline report is built from.

The goldens under `golden/` are derived on `BASE_INTERPRETER`. A parser that
reads the same source differently on another supported minor version produces
different bytes for a reason that is not a pack change. Those files, and only
those, live in an overlay at `golden-by-python/<major>.<minor>/<name>/<rel>`,
and every overlay repository must be declared in `INTERPRETER_DELTAS` with the
source paths that cause it. `golden_path` resolves one file for the running
interpreter. `--bless` on the base interpreter writes `golden/`; on another
minor it writes only the files that differ from the base golden into the
overlay, and removes an overlay file that no longer differs.

The dependency set mirrors `crux/scripts/derive-arch.py`: httpx because
importing `crux.arch` pulls the eager package `__init__`, pyyaml because the
frontmatter parser it selects decides the spine bytes, and the five tree-sitter
lines because the elixir, node and ruby packs declare the input class `parser`
(ADR-0096 clause 1) and those grammars decide the routes seven corpus
repositories render. Mirroring is required, not tidy: derive twice with two
dependency sets and the goldens are measuring two different derivers.

Each repository is derived with a scratch tree directory INSIDE its own cached
clone (`.crux-arch-scratch/`), removed before and after every derive so the
input state is identical on every run. Nothing outside `.cache/` is written
except the goldens.

Usage:
  uv run python3 crux/scripts/tests/arch-corpus/derive_corpus.py --bless
  uv run python3 crux/scripts/tests/arch-corpus/derive_corpus.py --only codetriage

Exit codes:
  0  every cached repo derived, and (without --bless) matched its golden
  1  a golden mismatch, or a repo derived that has no golden
  2  environment error — the cache is absent, or the deriver cannot import
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parents[1]                      # crux/scripts
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(HERE))

import fetch as corpus_fetch  # noqa: E402

CACHE = HERE / ".cache"
GOLDEN = HERE / "golden"
OVERLAY_ROOT = HERE / "golden-by-python"

#: The interpreter the goldens under `golden/` are derived on. Older minors are
#: not compared at all (the 3.11 parser refuses a djangoproject-com source).
BASE_INTERPRETER = (3, 13)

#: Interpreter-dependent inputs, by minor version, then by corpus repository.
#: `paths` are the sources whose parse outcome differs from BASE_INTERPRETER;
#: an overlay golden may differ from its base golden only on lines naming one
#: of them, plus the `n_sources` counters their hashing moves.
#: `test_arch_corpus.InterpreterOverlayTests` enforces that bound without the
#: corpus cache.
INTERPRETER_DELTAS: dict[tuple[int, int], dict[str, dict]] = {
    (3, 14): {
        "fastapi-fullstack": {
            "paths": ["backend/app/api/deps.py"],
            "because": (
                "PEP 758: 3.14 parses the unparenthesized `except "
                "InvalidTokenError, ValidationError:` on line 36 that 3.13 "
                "refuses, so the file is no longer a refused source. It "
                "contributes no column and no route either way."
            ),
        },
    },
}
SCRATCH_DOCS = ".crux-arch-scratch"

# A `source: self` entry derives a copy of THIS repository, not a cached clone;
# `_self_hosted_copy` below says what the copy leaves out and why.
REPO_ROOT = HERE.parents[3]

# The five files a golden captures. `overview.md`, `index.md` and
# `_meta/manifest.json` are deliberately out: the first two are synthesized
# from the spine (so they carry no independent signal), and the manifest is a
# source-hash ledger that says nothing about extraction quality.
GOLDEN_FILES = (
    "data-model.md",
    "api-surface.md",
    "module-graph.md",
    "decision-index.md",
    "_meta/coverage.json",
)

def overlay_dir(version: tuple[int, int]) -> Path:
    """The overlay root for one interpreter minor version."""
    return OVERLAY_ROOT / f"{version[0]}.{version[1]}"


def golden_path(name: str, rel: str, version: tuple[int, int] | None = None) -> Path:
    """The golden one file is compared against on `version` (default: running).

    The base golden, unless `version` is not the base interpreter and an
    overlay file exists for it.
    """
    version = tuple(version or sys.version_info[:2])
    if version != BASE_INTERPRETER:
        overlay = overlay_dir(version) / name / rel
        if overlay.is_file():
            return overlay
    return GOLDEN / name / rel


_TABLE_SEP_RE = re.compile(r"\A\|[\s:|-]+\|\Z")

# The per-repo `arch_extractors` override in a repository's own `.bionic.yml`
# executes that repository's code, and it executes only under
# `CRUX_ARCH_ALLOW_OVERRIDES=1`. Every repo in this corpus is an untrusted
# third-party clone, so the flag is popped for the duration of each derive: a
# developer who set it for their own tree must not have it apply here.
_OVERRIDE_FLAG = "CRUX_ARCH_ALLOW_OVERRIDES"


@contextlib.contextmanager
def _overrides_denied():
    """Run the body with `CRUX_ARCH_ALLOW_OVERRIDES` absent, then restore it."""
    prior = os.environ.pop(_OVERRIDE_FLAG, None)
    try:
        yield
    finally:
        if prior is not None:
            os.environ[_OVERRIDE_FLAG] = prior


def count_entities(text: str) -> dict:
    """Count what a spine file actually extracted.

    Two mechanical counts, chosen because every current spine renderer emits
    one or both:
      * `table_rows` — markdown table data rows (header and `|---|` separator
        rows excluded), which is how entities, columns, routes and ADRs render.
      * `graph_edges` — `-->` lines, which is how the module graph renders.
    `headings` is reported alongside as the section count (one per model /
    router / package), never added into the other two.
    """
    rows = edges = headings = 0
    lines = text.splitlines()
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("#"):
            headings += 1
        elif "-->" in s:
            edges += 1
        elif s.startswith("|") and s.endswith("|"):
            if _TABLE_SEP_RE.match(s):
                continue
            nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
            if _TABLE_SEP_RE.match(nxt):      # this is the header row
                continue
            rows += 1
    return {"table_rows": rows, "graph_edges": edges, "headings": headings}


# The `source: self` derive reads a COPY of this checkout, never the checkout.
# Its expectation record states that the derive reads a scratch `docs_dir` and
# never this repository's configured documentation tree. Once that tree has
# published an implementation migration, the in-place derive cannot keep that
# promise: the decision index refuses to read architectural history for a
# `docs_dir` other than the configured one ("architectural history belongs to a
# different configured tree"), and the refusal is correct. The copy leaves out
# the configured tree and `.git`, so the derive reads exactly the sources the
# record describes. The other names are regenerable caches and isolation state;
# `.cache` holds the corpus's own third-party clones.
_SELF_COPY_IGNORE = shutil.ignore_patterns(
    ".git", "__pycache__", "*.pyc", "node_modules", ".venv", ".cache", "logs",
    ".pytest_cache", ".ruff_cache", SCRATCH_DOCS, ".crux-selftest-scratch",
)


@contextlib.contextmanager
def _self_hosted_copy(root: Path):
    """Yield a copy of *root* without `.git` and without its configured docs tree."""
    import tempfile                                # noqa: PLC0415

    from bionic_config import load_config          # noqa: PLC0415

    root = root.resolve()
    try:
        docs_tree = (root / load_config(root).docs_dir).resolve()
    except Exception:                              # noqa: BLE001 — no config, no docs tree
        docs_tree = None
    harness = (root / ".claude").resolve()

    def ignore(directory: str, names: list[str]) -> set[str]:
        ignored = set(_SELF_COPY_IGNORE(directory, names))
        here = Path(directory).resolve()
        if docs_tree is not None and here == docs_tree.parent:
            ignored.update({docs_tree.name}.intersection(names))
        if here == harness:
            ignored.update({"worktrees"}.intersection(names))
        return ignored

    with tempfile.TemporaryDirectory(prefix="crux-arch-self-") as tmp:
        copy = Path(tmp) / root.name
        shutil.copytree(root, copy, ignore=ignore, symlinks=True)
        if docs_tree is not None and (copy / docs_tree.relative_to(root)).exists():
            raise RuntimeError("the self-hosted copy still holds the configured docs tree")
        yield copy


def derive_one(name: str, root: Path, *, arch_stack: str | None = None) -> dict:
    """Derive one corpus entry; a `source: self` entry derives a copy of this checkout."""
    if Path(root).resolve() == REPO_ROOT.resolve():
        with _self_hosted_copy(Path(root)) as copy:
            return _derive_one(name, copy, arch_stack=arch_stack)
    return _derive_one(name, root, arch_stack=arch_stack)


def _derive_one(name: str, root: Path, *, arch_stack: str | None = None) -> dict:
    """Derive one cached repo. Returns {"tree": {rel: text}, "report": {...}}.

    `arch_stack`, given only for a `source: self` entry, forces
    `resolve_stack` past this checkout's own `.bionic.yml` pin
    (`arch_stack: crux`) to the pinned pack instead — the self-hosted entry
    measures a language pack over these sources, not the `crux` pack.
    """
    import importlib                               # noqa: PLC0415

    from bionic_config import load_config          # noqa: PLC0415

    # `from crux.arch import derive` binds the FUNCTION re-exported by the
    # package `__init__`, not the module; import the module by name instead.
    D = importlib.import_module("crux.arch.derive")

    scratch = root / SCRATCH_DOCS
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True)
    try:
        with _overrides_denied():
            try:
                cfg = load_config(root)
            except Exception:                      # noqa: BLE001 — no config is the norm
                cfg = None
            if arch_stack is not None:
                if cfg is None:
                    from types import SimpleNamespace  # noqa: PLC0415
                    cfg = SimpleNamespace(
                        arch_stack=arch_stack, arch_extractors={},
                        arch_decision_index_mode="complete",
                    )
                else:
                    cfg.arch_stack = arch_stack
            pack = D.detect_stack(root, cfg)
            mode = getattr(cfg, "arch_decision_index_mode", "complete")
            tree = D._build(root, SCRATCH_DOCS, mode, cfg)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    coverage = json.loads(tree["_meta/coverage.json"])
    concerns = []
    for rec in coverage["concerns"]:
        fname = f"{rec['concern']}.md"
        # ADR-0096 clauses 2/3 replaced `status`/`reason` with `verdict` plus,
        # on a stub, `stub_reason`/`expected`/`found`. Read through `.get` so a
        # vocabulary change moves the report rather than raising KeyError and
        # taking every downstream gate with it — which is what a plain subscript
        # did the first time the recorded channel changed shape.
        concerns.append({
            "concern": rec["concern"],
            "verdict": rec["verdict"],
            "stub_reason": rec.get("stub_reason"),
            "extractor": rec["extractor"],
            "n_sources": len(rec["inputs_found"]),
            # `count_entities` below is the harness's own coarse heuristic, kept
            # for the baseline report's per-file numbers. It is NOT what decides
            # a verdict: that is `core.count_concern_entities`, which counts from
            # each concern's own rendered structure, and `n_entities` here is the
            # recorded figure it produced.
            "n_entities": rec.get("n_entities"),
            **count_entities(tree[fname]),
        })
    return {
        "tree": {rel: tree[rel] for rel in GOLDEN_FILES},
        "report": {"name": name, "detected_pack": pack, "concerns": concerns},
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Derive the arch corpus and bless/compare goldens.")
    ap.add_argument("--bless", action="store_true", help="write golden/<name>/ from this derive")
    ap.add_argument("--only", action="append", default=None, metavar="NAME")
    args = ap.parse_args(argv)

    try:
        repos = corpus_fetch.load_manifest()
    except corpus_fetch.ManifestError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.only:
        repos = [r for r in repos if r["name"] in set(args.only)]

    # A `source: self` entry is always "present" — it derives this checkout,
    # not a cache dir under `.cache/`, so it never gates on a clone existing.
    cached = [
        r for r in repos
        if r.get("source") == "self" or (CACHE / r["name"] / ".git").exists()
    ]
    if not cached:
        print(f"no cached corpus repos under {CACHE}; run fetch.py first", file=sys.stderr)
        return 2

    version = sys.version_info[:2]
    if version < BASE_INTERPRETER:
        print(f"refusing: the goldens are derived on {BASE_INTERPRETER[0]}."
              f"{BASE_INTERPRETER[1]} or later; this is {version[0]}.{version[1]}",
              file=sys.stderr)
        return 2
    on_base = version == BASE_INTERPRETER

    reports, mismatches = [], []
    for entry in cached:
        name = entry["name"]
        is_self = entry.get("source") == "self"
        root = REPO_ROOT if is_self else CACHE / name
        got = derive_one(name, root, arch_stack=entry.get("arch_stack") if is_self else None)
        dest = GOLDEN / name
        # No byte golden for a self-hosted entry (W5b): this checkout is not
        # frozen at a SHA, so a byte golden here would go red on every future
        # edit to `crux/scripts/**/*.py`. The self entry's gate is the
        # expectation record in `corpus.yml`, checked in
        # `test_arch_pack_acceptance.py`'s never-skipped self case.
        if not is_self:
            for rel, text in got["tree"].items():
                if args.bless and on_base:
                    path = dest / rel
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(text, encoding="utf-8")
                    continue
                if args.bless:
                    base = dest / rel
                    overlay = overlay_dir(version) / name / rel
                    if base.is_file() and base.read_text(encoding="utf-8") == text:
                        if overlay.exists():
                            overlay.unlink()
                    else:
                        overlay.parent.mkdir(parents=True, exist_ok=True)
                        overlay.write_text(text, encoding="utf-8")
                    continue
                path = golden_path(name, rel, version)
                if not path.exists():
                    mismatches.append(f"{name}/{rel}: no golden")
                elif path.read_text(encoding="utf-8") != text:
                    mismatches.append(f"{name}/{rel}: differs from golden")
        report = got["report"]
        report["pack_intent"] = entry["pack"]
        report["golden"] = None if is_self else str(dest.relative_to(HERE.parents[3]))
        report["golden_bytes"] = sum(len(t.encode("utf-8")) for t in got["tree"].values())
        reports.append(report)

    print(json.dumps({"blessed": bool(args.bless), "mismatches": mismatches,
                      "repos": reports}, indent=2, sort_keys=True))
    return 1 if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
