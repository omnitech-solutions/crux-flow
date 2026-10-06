#!/usr/bin/env python3
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "httpx>=0.27",
#     "pyyaml==6.0.3",
# ]
# ///
# `httpx` is here because it is REACHED, not because this file calls it:
# importing `survey_sheet` / `crux.arch.recover` pulls the `crux` package, whose
# council module imports the LLM router, which imports httpx. Declaring only
# PyYAML passes in the dev venv, where httpx is ambient, and fails under
# `uv run --no-project`. `survey.py` and `scaffold-survey-sheet.py` carry the
# same declaration for the same reason.
"""survey_corpus.py — measure one batch-ratification survey against a corpus clone.

A TEST HARNESS, not a shipped script and not a skill. It exists to produce the
numbers ADR-0098 is measured by, over the real repositories in
`crux/scripts/tests/arch-corpus/.cache/`, and it publishes nothing.

Two halves:

  * **The scratch tree** (`bootstrap_tree` / `teardown_tree`). A survey needs a
    crux tree, and a corpus clone has none. The harness writes one INSIDE the
    gitignored cache — `<clone>/bionic/` plus `<clone>/.bionic.yml` — and
    removes it either side of the measurement. The clone's own tracked files
    are never written: `bootstrap_tree` refuses when git tracks either path.

  * **The measurement driver** (`--repo <name>`). bootstrap -> derive ->
    (stop for mining) -> mine -> scaffold `--dry-run` -> the simulated write
    set -> project -> verify -> teardown, emitting one JSON object of numbers.

**The containment guard is the point of unit 1.** Every entry point resolves
its `clone_root` and refuses anything that is not a direct child of
`.cache/`, and refuses this checkout's own root by name before that. A survey
phase run against this repository's own tree would derive, project and
scaffold over the dogfood tree — the one mutation this whole loop is built to
make impossible. The refusal is `ScratchTreeRefused`, a named exception with a
specific message, because a test asserts on it. Both halves of the write path
are checked: the clone root by `assert_corpus_clone`, and the `--docs-dir`
component joined onto it by `assert_docs_dir` — a validated prefix followed by
an unvalidated suffix is not a containment check.

**Nothing here signs anything.** ADR-0098 decision 4 puts `verdict`, `domain`
and `rationale` in human hands and marks both survey commands
`disable-model-invocation: true`; the sign-off is a run stop point, and a
scratch tree does not change who is invoking. So this harness stops at the
scaffold's `--dry-run` and computes the write set a hypothetical all-`ratify`
sheet WOULD produce, in memory, through the shipped `survey_sheet.build_plan`.
No sheet is written, no receipt is written, `signoff-survey.py` is never run,
and `records_ratified` is 0 by construction.

Usage:
  survey_corpus.py --repo rubygems-org --stage pre  [--budget N]
  # mine candidates into <clone>/bionic/arch/_recovered/state.yml
  survey_corpus.py --repo rubygems-org --stage post [--budget N] [--keep]

Exit: 0 the measurement was produced (a failing PHASE is a recorded finding,
not a failing run) · 1 the measurement itself could not be produced ·
2 environment / containment refusal (stderr).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE_ROOT = HERE / ".cache"
SCRIPTS = HERE.parent.parent                     # crux/scripts
REPO_ROOT = HERE.parents[3]                      # this checkout
SURVEY = SCRIPTS / "survey.py"
SCAFFOLD = SCRIPTS / "scaffold-survey-sheet.py"
CORPUS_YML = HERE / "corpus.yml"

sys.path.insert(0, str(SCRIPTS))

TOOL = "survey_corpus.py"
DEFAULT_DOCS_DIR = "bionic"
PARTITION_CAP = 8
CANDIDATE_CAP = 40

# `docs_dir` is JOINED onto the clone root and then removed wholesale, so the
# component is allowlisted exactly as `fetch.py` allowlists the one component
# it joins onto the cache (`NAME_RE`, its line 194). `assert_corpus_clone`
# validates the ROOT and cannot see what is appended to it: `--docs-dir
# ../../../../../../` walks out of a containment check that has already
# passed, and `Path.__truediv__` adopts an absolute component whole, so
# `--docs-dir /etc` discards the clone root entirely.
#
# A leading dot is outside the allowlist deliberately. It refuses `.` and
# `..`, and it refuses `--docs-dir .git` — contained, traversal-free, a plain
# name, and the clone's own repository.
DOCS_DIR_RE = re.compile(r"\A[A-Za-z0-9_][A-Za-z0-9._-]{0,63}\Z")

# The manifest a survey needs: the three concerns the sequence touches, plus
# the two counters batch ratification allocates from. Lifted from
# `crux/scripts/tests/test_survey.py:90 _make_tree` and extended with the
# observation block ADR-0098 reads (`next_survey_number`) and the staleness
# windows §17 names.
MANIFEST_BODY = (
    'schema_version: "5"\n'
    "concerns_enabled:\n"
    "  - adrs\n"
    "  - arch\n"
    "  - observations\n"
    "adr:\n"
    "  next_number: 1\n"
    "observation:\n"
    "  next_number: 1\n"
    "  next_survey_number: 1\n"
    "  stale_days: 90\n"
    "  survey_stub_days: 1\n"
)


class ScratchTreeRefused(Exception):
    """A scratch tree was asked for somewhere it may not be written.

    Named, and carrying a specific message, because the refusal is the
    behaviour under test — an assertion on `Exception` would pass on a typo."""


# ── unit 1: the scratch tree ────────────────────────────────────────────────

def assert_corpus_clone(clone_root: Path) -> Path:
    """Resolve `clone_root` and refuse anything that is not a corpus clone.

    Three legs, checked in this order because the order is the message:

      1. this checkout's own root — named first so the operator reading the
         error sees the dogfood tree named, not a generic containment string;
      2. the cache directory itself — `.cache/` is not a clone, and
         bootstrapping there would put a tree beside ten of them;
      3. containment — the resolved path's parent must BE the resolved
         `.cache/`. Not "under": a corpus clone is a direct child, and
         accepting a deeper path would admit `.cache/x/../../..`-shaped
         resolutions that land back in the repository.

    Both sides are resolved, so a symlinked prefix on the checkout still
    passes and a symlink planted at `.cache/<name>` pointing outward does not.
    """
    resolved = Path(clone_root).resolve()
    repo = REPO_ROOT.resolve()
    # `resolved.name == repo.name` is deliberately fail-closed: a corpus clone
    # named `crux-internal` is refused even though it is a legitimate direct
    # child of `.cache/`. Refusing one possible clone by name costs a rename;
    # accepting a path that reads as this checkout costs the dogfood tree.
    if resolved == repo or resolved.name == repo.name:
        raise ScratchTreeRefused(
            f"refusing to bootstrap a scratch tree at {resolved}: that is this "
            f"repository's own root ({repo.name}) — no survey phase runs "
            "against the dogfood tree")
    cache = CACHE_ROOT.resolve()
    if resolved == cache:
        raise ScratchTreeRefused(
            f"refusing to bootstrap a scratch tree at {resolved}: that is the "
            ".cache directory itself, not a corpus clone inside it")
    if resolved.parent != cache:
        raise ScratchTreeRefused(
            f"refusing to bootstrap a scratch tree at {resolved}: a scratch "
            f"tree is only ever written inside a corpus clone under {cache} "
            "(a direct child), which is gitignored")
    return resolved


def assert_docs_dir(docs_dir: str) -> str:
    """Return `docs_dir` when it is one plain relative name, else refuse.

    The containment guard on the clone root is only half of the path: the
    other half is this component, and a guard that validates a prefix and then
    concatenates an unvalidated suffix has validated nothing."""
    if not isinstance(docs_dir, str) or not DOCS_DIR_RE.match(docs_dir):
        raise ScratchTreeRefused(
            f"refusing a scratch tree with --docs-dir {docs_dir!r}: the docs "
            "dir is joined onto the clone root and then removed wholesale, so "
            "it must be one plain relative name matching "
            r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,63}$"
            " — no separator, no traversal, no leading dot")
    return docs_dir


def _refuse_symlinked_tree(tree: Path) -> None:
    """Refuse when `<clone>/<docs_dir>` is a symlink.

    `assert_docs_dir` validates the component's SPELLING; nothing re-checked
    the join, so a symlink there left every rule satisfied and steered the
    `mkdir(exist_ok=True)` and the `manifest.yml` write into the link's target.
    Only the write path escaped — `rmtree(ignore_errors=True)` is a no-op on a
    symlink — and this is the cheap version of the remedy
    `survey_sheet.atomic_write_text` already ships: refuse the link rather than
    follow it. Defence in depth: a corpus repo that TRACKS `bionic` as a
    symlink is already refused by `_refuse_tracked`, and `git clone` cannot
    produce an untracked one."""
    if tree.is_symlink():
        raise ScratchTreeRefused(
            f"refusing a scratch tree at {tree}: it is a symlink, and the "
            "harness writes and removes the tree wholesale — following the "
            "link would put the scratch tree in the link's target")


def _refuse_tracked(clone_root: Path, rel: str) -> None:
    """Refuse when git tracks `rel` inside the clone.

    The scratch tree and its config are written into somebody else's
    repository. A clone that already tracks `.bionic.yml` (or a `bionic/`)
    would have its own file overwritten and then deleted at teardown, and the
    `git status` gate would catch it only after the damage."""
    proc = subprocess.run(
        ["git", "-C", str(clone_root), "ls-files", "--error-unmatch", rel],
        capture_output=True, text=True)
    if proc.returncode == 0:
        raise ScratchTreeRefused(
            f"refusing to bootstrap a scratch tree in {clone_root}: git tracks "
            f"{rel!r} there, and the harness never writes a clone's own "
            "tracked files")


def bootstrap_tree(clone_root: Path, docs_dir: str = DEFAULT_DOCS_DIR) -> Path:
    """Write a minimal crux tree at `<clone>/<docs_dir>/` and return its path.

    `rmtree(ignore_errors=True)` first, as `derive_corpus.py` does either side
    of a derive (its lines 152-174): a previous run that died mid-measurement
    leaves a tree, and deriving into it would measure the leftover."""
    clone = assert_corpus_clone(clone_root)
    docs_dir = assert_docs_dir(docs_dir)
    _refuse_tracked(clone, docs_dir)
    _refuse_tracked(clone, ".bionic.yml")

    tree = clone / docs_dir
    _refuse_symlinked_tree(tree)
    shutil.rmtree(tree, ignore_errors=True)
    for sub in ("adrs", "arch", "observations"):
        (tree / sub).mkdir(parents=True, exist_ok=True)
    (tree / "manifest.yml").write_text(MANIFEST_BODY, encoding="utf-8")
    (clone / ".bionic.yml").write_text(
        f'config_version: "1"\ndocs_dir: {docs_dir}\n', encoding="utf-8")
    return tree


def teardown_tree(clone_root: Path, docs_dir: str = DEFAULT_DOCS_DIR) -> None:
    """Remove the scratch tree and its config. Idempotent, and guarded by the
    same containment check as the bootstrap — a teardown pointed at the wrong
    root is an `rmtree` on somebody's documentation.

    Both halves of the path are checked here, and this is the harness's
    earliest-firing write path: `measure` calls it BEFORE `bootstrap_tree`, so
    on `--stage pre` these two guards run before anything else in the run."""
    clone = assert_corpus_clone(clone_root)
    docs_dir = assert_docs_dir(docs_dir)
    _refuse_symlinked_tree(clone / docs_dir)
    shutil.rmtree(clone / docs_dir, ignore_errors=True)
    (clone / ".bionic.yml").unlink(missing_ok=True)
    for leftover in (".survey-measure.json",):
        (clone / leftover).unlink(missing_ok=True)


# ── unit 2: the measurement driver ──────────────────────────────────────────

def _yaml():
    import yaml                                   # noqa: PLC0415
    return yaml


def corpus_entry(name: str) -> dict:
    """The `corpus.yml` record for `name`, for `pack` and `pinned_sha`."""
    doc = _yaml().safe_load(CORPUS_YML.read_text(encoding="utf-8")) or {}
    for entry in doc.get("repos") or []:
        if entry.get("name") == name:
            return entry
    raise EnvironmentError(f"{name!r} names no entry in {CORPUS_YML}")


def run_phase(clone: Path, phase: str, docs_dir: str) -> dict:
    """One `survey.py --phase` invocation, recorded whatever it does.

    A phase that fails is a RECORDED FINDING carrying its exit code and its
    stderr — never an omitted key. Two exit codes read as failures and are
    not: `--phase mine` exits 1 by design whenever a human step is pending,
    and `--phase verify` exits 1 on a finding it is meant to report."""
    started = time.monotonic()
    proc = subprocess.run(
        [sys.executable, str(SURVEY), "--repo-root", str(clone),
         "--docs-dir", docs_dir, "--phase", phase],
        capture_output=True, text=True)
    elapsed = round(time.monotonic() - started, 2)
    try:
        payload = json.loads(proc.stdout) if proc.stdout.strip() else None
    except json.JSONDecodeError:
        payload = None
    return {
        "exit": proc.returncode,
        "wall_clock_s": elapsed,
        "boundary": (payload or {}).get("boundary"),
        # A phase that exits 1 says WHY on its payload. Dropping that left the
        # measurement reporting a bare `exit: 1` for a verify whose finding is
        # the most informative line in the run.
        "findings": (payload or {}).get("findings"),
        "stderr": proc.stderr.strip()[:2000] or None,
        "payload": payload,
    }


def run_scaffold_dry_run(clone: Path, docs_dir: str) -> dict:
    """`scaffold-survey-sheet.py --dry-run`: the row set, written nowhere.

    `--dry-run` moves no counter and writes no sheet, which is what makes it
    the far edge of what a machine may do here."""
    started = time.monotonic()
    proc = subprocess.run(
        [sys.executable, str(SCAFFOLD), "--repo-root", str(clone),
         "--docs-dir", docs_dir, "--dry-run"],
        capture_output=True, text=True)
    elapsed = round(time.monotonic() - started, 2)
    try:
        payload = json.loads(proc.stdout) if proc.stdout.strip() else None
    except json.JSONDecodeError:
        payload = None
    return {
        "exit": proc.returncode,
        "wall_clock_s": elapsed,
        "stderr": proc.stderr.strip()[:2000] or None,
        "payload": payload,
    }


def simulate_write_set(clone: Path, docs_dir: str, scaffold_payload: dict | None) -> dict:
    """The records a hypothetical all-`ratify` sheet would publish.

    Read-only and in memory. The sheet is built in the shape
    `scaffold-survey-sheet.py` writes, every `verdict` set to `ratify` and
    every `domain` left EMPTY so the mined `proposed_domain` seed applies —
    the simulation never authors a domain, because authoring one is the human
    act this harness refuses to perform. It is then pushed through the shipped
    `survey_sheet.build_plan`, and the ids are allocated exactly as
    `signoff-survey._cell_3_allocate` allocates them: ratify rows in plan
    order, `OBS-{next_number + i}`, path `<obs>/<id>-<slug>.md`.

    A row whose candidate carries no mined domain cannot be simulated —
    `signed_rows` refuses an empty domain after the seed, and filling it would
    be the machine writing a human cell. Those rows are excluded and COUNTED,
    never invented.
    """
    import survey_sheet as ss                     # noqa: PLC0415
    from crux.arch.recover import (               # noqa: PLC0415
        StateFile,
        anchor_of,
        read_recorded_observations,
    )

    tree = clone / docs_dir
    obs = tree / "observations"
    state_path = tree.joinpath(*ss.STATE_FILE_REL)
    rows = StateFile(state_path).rows if state_path.is_file() else {}
    recorded = read_recorded_observations(obs)
    disposed = ss.disposed_candidates(obs)

    anchors = list((scaffold_payload or {}).get("anchors") or [])
    domain_by_anchor: dict[str, str] = {}
    for cid, cand in rows.items():
        domain_by_anchor.setdefault(anchor_of(cid), str(cand.get("domain") or ""))

    simulated, skipped = [], []
    for anchor in anchors:
        seed = domain_by_anchor.get(anchor, "")
        if not seed.strip():
            skipped.append(anchor)
            continue
        simulated.append({
            "anchor_id": anchor,
            "proposed_domain": seed,
            "verdict": "ratify",
            "domain": "",
            "rationale": "",
        })

    manifest = _yaml().safe_load((tree / "manifest.yml").read_text(encoding="utf-8")) or {}
    counter_before = ss.read_counter(manifest, "observation", "next_number", 1)
    batch_id = (scaffold_payload or {}).get("batch_id") or "SVY-0001"
    sheet = {
        "config_version": ss.CONFIG_VERSION,
        "batch_id": batch_id,
        "scaffold_provenance": {
            "tool": "scaffold-survey-sheet.py",
            "scaffolded": "simulated",
            "state_file": str(Path(*ss.STATE_FILE_REL)),
            "candidates": len(simulated),
        },
        "rows": simulated,
    }

    out = {
        "basis": ("a hypothetical all-ratify sheet, built in memory and never "
                  "written; no verdict, domain or rationale cell was authored "
                  "by this harness"),
        "batch_id": batch_id,
        "rows_simulated": len(simulated),
        "rows_excluded_no_proposed_domain": skipped,
        "counter_before": counter_before,
        "counter_after": counter_before,
        "records": [],
        "retirements": [],
        "refusals": [],
    }
    if not simulated:
        return out
    try:
        plan = ss.build_plan(sheet, rows, recorded, root=clone, obs_dir=obs,
                             disposed_elsewhere=disposed, own_record_ids={})
    except ss.SurveySheetError as exc:
        out["refusals"] = list(exc.problems)
        return out

    ratify = [e for e in plan if e["verdict"] == "ratify"]
    for i, entry in enumerate(ratify):
        rid = f"OBS-{counter_before + i:04d}"
        out["records"].append({
            "record_id": rid,
            "record_path": f"{obs.name}/{rid}-{entry['slug']}.md",
            "anchor_id": entry["anchor_id"],
            "candidate_id": entry["candidate_id"],
            "domain": entry["domain"],
            "domain_source": entry["domain_source"],
            "title": entry["title"],
            "evidence": entry["evidence"],
        })
        if entry["retires"]:
            out["retirements"].append(
                {"record_id": rid, "retires": entry["retires"]})
    out["counter_after"] = counter_before + len(ratify)
    return out


def _mining_report(clone: Path) -> dict:
    """The mining step's own record, when it left one.

    Mining is not a phase of `survey.py` — it is the `recover-decisions` skill
    writing through `StateFile.upsert_observed` — so the counts only it can
    know (partitions scanned, dedup collapses, why it stopped) arrive through
    a file it drops beside the clone. Absent, the keys are null and say so."""
    path = clone / ".survey-mining.json"
    if not path.is_file():
        return {"recorded": False,
                "note": "the mining step left no .survey-mining.json"}
    # A half-written report is an ENVIRONMENT fault, and the module contract
    # says an environment fault exits 2 with a message on stderr. A bare
    # `json.loads` raised `JSONDecodeError` straight out of `measure`, past
    # both of `main`'s handlers, and turned that lane into a traceback with an
    # unparseable stdout — the exact shape the exit codes exist to avoid.
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise EnvironmentError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise EnvironmentError(
            f"{path} holds a {type(data).__name__}, not a JSON object")
    data["recorded"] = True
    return data


def measure(name: str, *, stage: str, budget: int, partitions: int,
            docs_dir: str, keep: bool) -> tuple[int, dict]:
    entry = corpus_entry(name)
    clone = assert_corpus_clone(CACHE_ROOT / name)
    if not clone.is_dir():
        raise EnvironmentError(
            f"no clone at {clone}; run fetch.py --only {name} first")
    partial = clone / ".survey-measure.json"

    base = {
        "repository": name,
        "pack": entry.get("pack"),
        "pinned_sha": entry.get("sha"),
        "substituted_for": None,
        "substitution_reason": None,
        "mining_budget": {"max_partitions": partitions,
                          "max_candidates": budget},
        "phases": {},
    }

    if stage in ("pre", "all"):
        teardown_tree(clone, docs_dir)
        bootstrap_tree(clone, docs_dir)
        base["phases"]["derive"] = run_phase(clone, "derive", docs_dir)
        base["phases"]["derive"].pop("payload", None)
        partial.write_text(json.dumps(base, indent=2, sort_keys=True),
                           encoding="utf-8")
        if stage == "pre":
            base["next_step"] = (
                "mine candidates into "
                f"{docs_dir}/arch/_recovered/state.yml, then re-run --stage post")
            return 0, base

    if stage == "post":
        if not partial.is_file():
            raise EnvironmentError(
                f"no partial measurement at {partial}; run --stage pre first")
        base = json.loads(partial.read_text(encoding="utf-8"))
        base["mining_budget"] = {"max_partitions": partitions,
                                 "max_candidates": budget}

    out = dict(base)
    tree = clone / docs_dir
    obs = tree / "observations"

    mine = run_phase(clone, "mine", docs_dir)
    mine_payload = mine.pop("payload", None) or {}
    mine["note"] = ("exit 1 is BY DESIGN whenever a human step is pending; "
                    "`survey.py` does not mine, it reports the state file")
    out["phases"]["mine"] = mine

    scaffold = run_scaffold_dry_run(clone, docs_dir)
    scaffold_payload = scaffold.pop("payload", None) or {}
    out["scaffold_dry_run"] = {
        "exit": scaffold["exit"],
        "wall_clock_s": scaffold["wall_clock_s"],
        "stderr": scaffold["stderr"],
        "batch_id": scaffold_payload.get("batch_id"),
        "sheet": scaffold_payload.get("sheet"),
        "anchors": scaffold_payload.get("anchors") or [],
        "written": scaffold_payload.get("written"),
    }

    out["simulated_write_set"] = simulate_write_set(clone, docs_dir,
                                                    scaffold_payload)

    project = run_phase(clone, "project", docs_dir)
    project.pop("payload", None)
    out["phases"]["project"] = project

    verify = run_phase(clone, "verify", docs_dir)
    verify_payload = verify.pop("payload", None) or {}
    out["phases"]["verify"] = verify

    mining = _mining_report(clone)
    by_state = mine_payload.get("candidates_by_state") or {}
    pending = len(mine_payload.get("human_commands") or [])
    rows_scaffolded = len(out["scaffold_dry_run"]["anchors"])
    records_on_disk = sorted(p.name for p in obs.glob("OBS-*.md")) if obs.is_dir() else []

    out.update({
        "candidates_total": mine_payload.get("candidates"),
        "candidates_by_state": by_state,
        "pending": pending,
        "stale_anchors": mine_payload.get("stale_anchors"),
        "stale_anchor_records": mine_payload.get("stale_anchor_records") or [],
        "recorded_observations": mine_payload.get("recorded_observations"),
        "rows_scaffolded": rows_scaffolded,
        "verdicts_by_kind": {"ratify": out["simulated_write_set"]["rows_simulated"],
                             "reject": 0, "defer": 0},
        "verdicts_by_kind_basis": (
            "PLANNED, not authored: the escalate branch was taken, so no human "
            "signed a sheet and every verdict here is the simulation's "
            "all-ratify assumption"),
        "human_acts": {"batch_path": 1, "per_record_path": pending},
        "records_written": len(records_on_disk),
        "records_ratified": 0,
        "records_ratified_basis": (
            "0 because the loop escalated: ADR-0098 decision 4 makes the "
            "sign-off a human act, so nothing published on this tree"),
        "dedup_collapses": mining.get("dedup_collapses"),
        "successors_handled": mining.get("successors_handled"),
        "adr_files_written": verify_payload.get("adr_files"),
        "doctrine_entries": verify_payload.get("entries") or [],
        "doctrine_entries_total": verify_payload.get("doctrine_entries"),
        "doctrine_entries_without_evidence":
            verify_payload.get("entries_without_evidence"),
        "mining_report": mining,
    })

    if not keep:
        teardown_tree(clone, docs_dir)
        (clone / ".survey-mining.json").unlink(missing_ok=True)
    return 0, out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog=TOOL,
        description="Measure one batch-ratification survey against a corpus clone.")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--stage", default="all", choices=("pre", "post", "all"))
    ap.add_argument("--budget", type=int, default=CANDIDATE_CAP,
                    help="stop mining at N candidates (recorded, never silent)")
    ap.add_argument("--partitions", type=int, default=PARTITION_CAP,
                    help="at most N module-graph partitions scanned")
    ap.add_argument("--docs-dir", default=DEFAULT_DOCS_DIR)
    ap.add_argument("--keep", action="store_true",
                    help="leave the scratch tree in place after --stage post")
    args = ap.parse_args(argv)
    try:
        code, payload = measure(args.repo, stage=args.stage, budget=args.budget,
                                partitions=args.partitions,
                                docs_dir=args.docs_dir, keep=args.keep)
    except ScratchTreeRefused as exc:
        sys.stderr.write(f"{TOOL}: {exc}\n")
        return 2
    except EnvironmentError as exc:
        sys.stderr.write(f"{TOOL}: {exc}\n")
        return 2
    print(json.dumps(payload, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
