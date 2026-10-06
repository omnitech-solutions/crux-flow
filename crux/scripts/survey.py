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
# The parser set is the deriver's, not this driver's, and it is here because
# `run_child` spawns `[sys.executable, <script>]` rather than `uv run`: the
# child INHERITS this environment, and derive-arch.py's own PEP 723 block is
# never resolved on that route. Declaring only PyYAML left `survey derive` on a
# Ruby, Node or Elixir target exiting 2 with `ParserUnavailable` for a grammar
# the deriver pins and this driver did not supply. The pins are EXACT for the
# reason derive-arch.py's header gives — `core.parser_pins` records the resolved
# version into the byte-compared `tool_pins`, so a range moves the manifest on an
# unmodified tree. `ParserPinLockStepTests` holds every site to one value.
"""survey.py — the scripted survey sequence for a tree that has few or no ADRs.

The survey bootstraps a documentation tree from what the code already does:

    derive-arch  ->  recover-decisions  ->  ratify as observation
                 ->  summarize-adrs     ->  compile-doctrine

Two of those steps are deterministic scripts and two are human acts. This
driver runs the two deterministic ends and reports the human step in the
middle. It is deliberately NOT unattended, and that is the decision: mining is
the `recover-decisions` skill, ratification is `transition-decision ratify
<id> --as observation`, both are human-invoked, and the operational schema
(§17.2) states that no scan writes an observation file in any state. This
script writes no observation file and no ADR in any phase, and it proves that
on every run by snapshotting `<docs_dir>/observations/` and every `ADR-*.md`
under `<docs_dir>/adrs/` before and after the phase (names + bytes) and
reporting the comparison as `boundary` in its payload. A moved snapshot is a
contract violation and exits 2.

Phases:

  derive    run `derive-arch.py` against the target tree.
  mine      READ-ONLY. Report the candidate state file, the candidate count
            (by state), the recorded-observation count, the stale-anchor
            signals the last mine left in the state file, and the exact human
            command per pending candidate. Exit 1 while a human step is
            pending, 0 when nothing awaits one. Writes nothing.
  project   run `summarize-adrs.py` then `compile-doctrine.py`, in that order,
            stopping at the first non-zero child exit.
  verify    READ-ONLY. The postcondition checker, read back off the produced
            artifacts: the ADR-record count under `<docs_dir>/adrs/`, and the
            doctrine index's per-domain authority and `path:line-range`
            evidence. Exit 0 when no ADR was written and every doctrine entry
            is descriptive and evidenced; 1 with the findings otherwise.
            Refuses on exit 2 when `--phase project` has not run.

Usage:
  survey.py --repo-root DIR [--docs-dir NAME] --phase derive|mine|project|verify

Tree resolution goes through the shared `bionic_config` loader, with
`--docs-dir` overriding the configured value under the same textual and
containment refusal `derive-arch.py` applies. `summarize-adrs.py` and
`compile-doctrine.py` accept only `--repo-root` and resolve the tree
themselves, so a `--docs-dir` that disagrees with the configured value on the
`project` phase is refused (exit 2) rather than silently projecting into a
different tree than the one named.

Exit: 0 clean · 1 findings (JSON on stdout) · 2 environment / capability
error (stderr). A child's exit code maps onto the same lanes.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

from bionic_config import (  # noqa: E402
    BionicConfigError, load_config, validate_docs_dir_override,
)
from crux.arch.recover import StateFile, read_recorded_observations  # noqa: E402
from observation_evidence import parse_evidence  # noqa: E402
import summaries_projection as sp  # noqa: E402
from untrusted import MESSAGE_LIMIT, redact  # noqa: E402

PHASES = ("derive", "mine", "project", "verify")
STATE_FILE_REL = Path("arch") / "_recovered" / "state.yml"


class SurveyError(Exception):
    """A capability / environment refusal — exit 2, message on stderr."""


# ── tree resolution ────────────────────────────────────────────────────────

def resolve_docs_dir(root: Path, override: str | None) -> tuple[str, str]:
    """Return (docs_dir, configured_docs_dir), both validated.

    The configured value comes from the shared loader (never an ad hoc YAML
    read). The override, when given, passes the same textual + containment
    check the loader applies to the configured value.
    """
    try:
        cfg = load_config(root)
    except BionicConfigError as exc:
        raise SurveyError(
            f"config error: {redact(exc, quoted=False, limit=MESSAGE_LIMIT)}"
        ) from exc
    configured = cfg.docs_dir
    docs_dir = override if override else configured
    # The override goes through the loader's OWN validator, not a local
    # re-implementation of part of it. The hand-rolled absolute/`..` pair here
    # missed the execution-adjacent first-segment denylist, so `--docs-dir
    # .github` wrote into `.github/` while the identical value in `.bionic.yml`
    # was refused — one value, two verdicts, on a NEW entry point that writes.
    # `validate_docs_dir_override` is now that one implementation, shared with
    # `scaffold-survey-sheet.py` and `signoff-survey.py`, and it carries BOTH
    # legs: the textual layer and the resolved-location layer (so an in-repo
    # symlink cannot route the override past the textual denylist).
    try:
        target = validate_docs_dir_override(root, docs_dir).resolve()
    except BionicConfigError as exc:
        raise SurveyError(
            f"invalid docs_dir: {redact(exc, quoted=False, limit=MESSAGE_LIMIT)}"
        ) from exc
    if not target.is_dir():
        raise SurveyError(f"docs_dir not found: {root / docs_dir}")
    if not (target / "manifest.yml").is_file():
        raise SurveyError(f"docs_dir holds no crux manifest: {root / docs_dir}")
    return docs_dir, configured


# ── the writer boundary, measured ──────────────────────────────────────────

def snapshot_files(directory: Path, pattern: str) -> dict[str, bytes]:
    """{relative path: bytes} for every file matching `pattern` under
    `directory`; an absent directory is an empty map."""
    if not directory.is_dir():
        return {}
    return {
        str(p.relative_to(directory)): p.read_bytes()
        for p in sorted(directory.glob(pattern)) if p.is_file()
    }


def snapshot_boundary(tree: Path) -> tuple[dict[str, bytes], dict[str, bytes]]:
    # [SECURITY:S5] The root of the observations concern is decided before the
    # snapshot resolves anything against it, through the one resolution
    # `summaries_projection.observations_root`. A RELATIVE symlink at
    # `<docs_dir>/observations` is git-carryable, so a pull request could make
    # this boundary measure a directory outside the repository.
    try:
        obs = sp.observations_root(tree)
    except ValueError as exc:
        raise SurveyError(str(exc)) from exc
    return (
        snapshot_files(obs, "**/*"),
        snapshot_files(tree / "adrs", "**/ADR-*.md"),
    )


def boundary_report(before, after) -> dict[str, bool]:
    return {
        "observations_unchanged": before[0] == after[0],
        "adr_records_unchanged": before[1] == after[1],
    }


# ── child scripts ──────────────────────────────────────────────────────────

def run_child(script: str, args: list[str]) -> dict:
    """Run a sibling script under this interpreter. Returns
    {script, exit, stdout, stderr}; the caller maps the exit onto a lane."""
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / script), *args],
        capture_output=True, text=True,
    )
    return {"script": script, "exit": proc.returncode,
            "stdout": proc.stdout, "stderr": proc.stderr}


def run_steps(plan: list[tuple[str, list[str]]]) -> tuple[list[dict], int]:
    """Run children in order, stopping at the first non-zero exit. Returns
    (steps, exit) where exit is 0, or the first failing child's code."""
    steps: list[dict] = []
    for script, args in plan:
        step = run_child(script, args)
        steps.append(step)
        if step["exit"] != 0:
            return steps, step["exit"]
    return steps, 0


# ── phases ─────────────────────────────────────────────────────────────────

def phase_derive(root: Path, docs_dir: str, _configured: str) -> tuple[dict, int]:
    steps, code = run_steps([
        ("derive-arch.py", ["--repo-root", str(root), "--docs-dir", docs_dir]),
    ])
    return {"steps": steps}, code


def phase_project(root: Path, docs_dir: str, configured: str) -> tuple[dict, int]:
    if docs_dir != configured:
        raise SurveyError(
            f"--docs-dir {redact(docs_dir)} disagrees with the configured docs_dir "
            f"{redact(configured)}; summarize-adrs.py and compile-doctrine.py resolve "
            "the tree from the config themselves, so the survey would project "
            "into a different tree than the one named. Fix .bionic.yml or drop "
            "the flag."
        )
    steps, code = run_steps([
        ("summarize-adrs.py", ["--repo-root", str(root)]),
        ("compile-doctrine.py", ["--repo-root", str(root)]),
    ])
    return {"steps": steps}, code


# ── the doctrine index, read back ──────────────────────────────────────────

_DOMAIN_HEADING_RE = re.compile(
    r"^## (?P<domain>.+?) — (?P<state>[^·]+?)"
    r"(?: · authority: (?P<authority>descriptive|mixed))?$")
_EVIDENCE_ROW_RE = re.compile(
    r"^\| (?P<handle>[^|]+?) \| (?P<evidence>[^|]+?) \| (?P<resolves>yes|no) \|$")


def parse_doctrine_entries(text: str) -> list[dict]:
    """Read the rendered doctrine index back into one dict per domain entry:
    {domain, state, authority, evidence: [path:line-range, ...]}.

    Reading the artifact is the point — the postcondition is about what the
    survey PRODUCED, so re-deriving it from the projection code would prove
    only that the code agrees with itself. `## Exempt ADRs (N)` is the
    index's trailing roster, not a domain entry, and is skipped.
    """
    entries: list[dict] = []
    in_evidence = False
    for line in text.splitlines():
        if line.startswith("## "):
            in_evidence = False
            if line.startswith("## Exempt ADRs ("):
                continue
            m = _DOMAIN_HEADING_RE.match(line)
            if m:
                entries.append({"domain": m.group("domain").strip(),
                                "state": m.group("state").strip(),
                                "authority": m.group("authority"),
                                "evidence": []})
            continue
        if line.startswith("_Observed evidence:_"):
            in_evidence = True
            continue
        if not in_evidence or not entries:
            continue
        if line.startswith("_") or (line.startswith("|") and "---" in line):
            in_evidence = not line.startswith("_")
            continue
        m = _EVIDENCE_ROW_RE.match(line)
        if not m:
            continue
        evidence = m.group("evidence").replace("\\|", "|").strip()
        if evidence == "evidence":  # the table's own header row
            continue
        if parse_evidence(evidence) is not None and m.group("resolves") == "yes":
            entries[-1]["evidence"].append(evidence)
    return entries


def ratify_command(candidate_id: str) -> str:
    return f"transition-decision ratify {redact(candidate_id, quoted=False)} --as observation"


# §17.2 admits `retire` only from `ratified` and `reject` only from `observed`.
# A stale anchor can be signalled on a record in either status, so the command
# branches on the status the signal carries. Emitting `retire` for an `observed`
# record names a transition the gate refuses, which strands `--phase mine` at
# exit 1 behind a step no human can complete.
_STALE_DISPOSITION = {"ratified": "retire", "observed": "reject"}


def stale_disposition_command(obs_id: str, status: str) -> str | None:
    """The `transition-observation` command that discharges a stale-anchor
    signal on a record in `status`, or None when no transition discharges it."""
    op = _STALE_DISPOSITION.get(status)
    return f"transition-observation {op} {redact(obs_id, quoted=False)}" if op else None


def phase_mine(root: Path, docs_dir: str, _configured: str) -> tuple[dict, int]:
    """READ-ONLY. Loads the candidate state file and the recorded observations
    through the recover library, and never calls `StateFile.save`."""
    tree = root / docs_dir
    state_path = tree / STATE_FILE_REL
    try:
        state = StateFile(state_path)
    except ValueError as exc:
        raise SurveyError(str(exc)) from exc
    # [SECURITY:S5] Same root decision as `snapshot_boundary`: the concern is
    # read from the tree, never through a link out of it.
    try:
        recorded = read_recorded_observations(sp.observations_root(tree))
    except ValueError as exc:
        raise SurveyError(str(exc)) from exc

    by_state: dict[str, int] = {}
    human_commands: list[dict] = []
    for cid in sorted(state.rows):
        row_state = str(state.rows[cid].get("state", "observed"))
        by_state[row_state] = by_state.get(row_state, 0) + 1
        if row_state == "observed":
            human_commands.append({
                "candidate_id": cid, "state": row_state,
                "command": ratify_command(cid),
            })
    stale_records = [
        {"anchor_id": aid, "obs_id": sig.get("obs_id"), "status": sig.get("status"),
         "command": stale_disposition_command(str(sig.get("obs_id")),
                                              str(sig.get("status")))}
        for aid, sig in sorted(state.stale_anchors.items())
    ]

    pending = bool(human_commands) or bool(stale_records)
    if not state_path.exists():
        next_step = ("run the recover-decisions skill to mine candidates into "
                     f"{STATE_FILE_REL.as_posix()}, then re-run --phase mine")
    elif pending:
        next_step = ("a human ratifies each pending candidate with "
                     "`transition-decision ratify <id> --as observation` (or "
                     "rejects/defers it) and disposes each stale-anchor record "
                     "with the `command` its `stale_anchor_records` entry names; "
                     "then run --phase project")
    else:
        next_step = "nothing awaits a human; run --phase project"

    payload = {
        "state_file": (Path(docs_dir) / STATE_FILE_REL).as_posix(),
        "state_file_exists": state_path.exists(),
        "candidates": len(state.rows),
        "candidates_by_state": dict(sorted(by_state.items())),
        "recorded_observations": len(recorded),
        "stale_anchors": len(stale_records),
        "stale_anchor_records": stale_records,
        "human_commands": human_commands,
        "next_step": next_step,
    }
    return payload, 1 if pending else 0


def phase_verify(root: Path, docs_dir: str, configured: str) -> tuple[dict, int]:
    """READ-ONLY. The survey postcondition checker, measured against the
    artifacts the survey produced rather than asserted from the code that
    produced them.

    Two postconditions, both from the observation-record decision:

      - the sequence writes NO ADR, so `<docs_dir>/adrs/` holds no `ADR-*.md`;
      - the resulting doctrine is descriptive-only and every entry traces to a
        `path:line-range`.

    `prescriptive_entries` counts every entry that is not PURELY descriptive —
    an entry with no authority suffix (all rules ADR-sourced) and an entry
    marked `authority: mixed` (at least one rule ADR-sourced) alike. Both
    defeat "descriptive-only", so folding them into one count keeps the
    payload's six keys and the `clean` predicate honest.
    """
    if docs_dir != configured:
        raise SurveyError(
            f"--docs-dir {redact(docs_dir)} disagrees with the configured docs_dir "
            f"{redact(configured)}; verify reads the artifacts summarize-adrs.py and "
            "compile-doctrine.py wrote, and those resolve the tree from the "
            "config themselves, so the survey would verify a different tree "
            "than the one named. Fix .bionic.yml or drop the flag."
        )
    tree = root / docs_dir
    index_path = tree / "adrs" / "doctrine" / "index.md"
    if not index_path.is_file():
        raise SurveyError(
            f"no doctrine index at {index_path}; run --phase project first")

    entries = parse_doctrine_entries(index_path.read_text(encoding="utf-8"))
    adr_files = sorted(snapshot_files(tree / "adrs", "**/ADR-*.md"))

    descriptive, prescriptive, without_evidence = [], [], []
    for e in entries:
        (descriptive if e["authority"] == "descriptive" else prescriptive).append(
            e["domain"])
        if not e["evidence"]:
            without_evidence.append(e["domain"])

    findings = []
    if adr_files:
        # State the measured fact, not a cause. This phase compares a tree
        # against the postcondition; it cannot see who wrote what, and on a
        # tree that already had ADRs "the survey wrote N" would be false.
        # The per-phase `boundary` block is what attributes authorship.
        findings.append(
            f"the tree holds {len(adr_files)} ADR record(s); the survey "
            f"postcondition is an ADR-less tree: {redact(', '.join(adr_files), quoted=False)}")
    if not entries:
        findings.append("the doctrine index holds no domain entry")
    if prescriptive:
        findings.append("doctrine is not descriptive-only; not purely "
                        f"descriptive: {redact(', '.join(prescriptive), quoted=False)}")
    if without_evidence:
        findings.append("entries tracing to no `path:line-range`: "
                        f"{redact(', '.join(without_evidence), quoted=False)}")

    payload = {
        "adr_files": len(adr_files),
        "adr_record_paths": adr_files,
        "doctrine_entries": len(entries),
        "descriptive_entries": len(descriptive),
        "prescriptive_entries": len(prescriptive),
        "entries_without_evidence": len(without_evidence),
        "entries": entries,
        "findings": findings,
        "clean": not findings,
    }
    return payload, 0 if not findings else 1


PHASE_RUNNERS = {
    "derive": phase_derive,
    "mine": phase_mine,
    "project": phase_project,
    "verify": phase_verify,
}


# ── driver ─────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Run one phase of the survey sequence; never writes an "
                    "observation file or an ADR.")
    ap.add_argument("--repo-root", default=".", help="repository root (default: cwd)")
    ap.add_argument("--docs-dir", default=None,
                    help="tree dir (default: from .bionic.yml or discovery)")
    ap.add_argument("--phase", required=True, choices=PHASES)
    try:
        args = ap.parse_args(argv)
    except SystemExit as exc:  # argparse's own exit is 2 on a bad flag/choice
        return int(exc.code) if exc.code else 0

    root = Path(args.repo_root).resolve()
    if not root.is_dir():
        sys.stderr.write(f"survey: repo-root does not exist: {redact(root, quoted=False)}\n")
        return 2

    try:
        docs_dir, configured = resolve_docs_dir(root, args.docs_dir)
        tree = root / docs_dir
        before = snapshot_boundary(tree)
        body, code = PHASE_RUNNERS[args.phase](root, docs_dir, configured)
        after = snapshot_boundary(tree)
    except SurveyError as exc:
        sys.stderr.write(
            f"survey: {redact(exc, quoted=False, limit=MESSAGE_LIMIT)}\n")
        return 2
    except Exception as exc:  # noqa: BLE001 — a crash is the no-verdict lane
        sys.stderr.write(
            f"survey: {type(exc).__name__}: "
            f"{redact(exc, quoted=False, limit=MESSAGE_LIMIT)}\n")
        return 2

    boundary = boundary_report(before, after)
    if not all(boundary.values()):
        sys.stderr.write(
            f"survey: writer boundary violated during --phase {args.phase}: "
            f"{json.dumps(boundary, sort_keys=True)} — no phase may write an "
            "observation file or an ADR\n")
        return 2

    if code == 2:
        for step in body.get("steps", []):
            if step["exit"] == 2 and step.get("stderr"):
                sys.stderr.write(step["stderr"])
        sys.stderr.write(f"survey: --phase {args.phase} stopped on a capability error\n")
        return 2

    payload = {"phase": args.phase, "docs_dir": docs_dir}
    payload.update(body)
    for step in payload.get("steps", []):
        step.pop("stderr", None)
    payload["boundary"] = boundary
    print(json.dumps(payload, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
