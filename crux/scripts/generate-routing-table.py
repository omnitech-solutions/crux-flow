#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""generate-routing-table.py — the vendored regenerator for the §10 routing table.

The fourteenth regenerative output (ADR-0056 enrollment discipline, introduced by
[[adrs/ADR-0092]]). One source of truth — skill frontmatter, read from the
already-regenerated `crux/catalog/skills.json` — projected into the §10 "Skill
invocation table" of `<tree>/AGENTS.md`, between the markers:

    <!-- BEGIN GENERATED: routing-table -->
    <!-- END GENERATED: routing-table -->

Three columns, sorted by skill name:
  - User phrase — every quoted phrase in the `description`'s first SENTENCE
    (that is where each description puts its triggers). The sentence ends at a
    period followed by whitespace or end-of-text, so a dotted identifier such
    as `research.refresh_interval_days` does not end it.
  - Skill — the name.
  - Notes — the optional `metadata.routing_note`.

Skills with `user-invocable: false` are listed in a second, smaller table headed
"Claude-only (no user phrase)" — they are still routed, so they belong here.

Fail-closed marker handling mirrors generate-writing-rules.py: a missing,
duplicated, or misordered marker aborts with a message rather than guessing an
insertion point. Reads/writes with `newline=""` so a CRLF file stays CRLF.

Exit codes (the sibling-regenerator contract):
    0  clean — wrote (or, under --dry-run, found no drift)
    1  drift (--dry-run) or validation error, JSON on stdout
    2  capability/environment error, message on stderr
"""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path

BEGIN = "<!-- BEGIN GENERATED: routing-table -->"
END = "<!-- END GENERATED: routing-table -->"

# A quoted trigger phrase: a "double-quoted" span, or a 'single-quoted' span
# whose quotes are not adjacent to a letter (so a contraction apostrophe such as
# the one in "what's" is not read as an opening quote).
_PHRASE_RE = re.compile(r'"([^"]+)"' + r"|(?<![A-Za-z])'([^']+?)'(?![A-Za-z])")

# The end of the first SENTENCE: a period followed by whitespace or end-of-text.
# Splitting on any period cut `research.refresh_interval_days` in half and threw
# away the triggers that followed it in the same sentence.
_SENTENCE_END_RE = re.compile(r"\.(?=\s|$)")


sys.path.insert(0, str(Path(__file__).resolve().parent))
import authoring_scope as _scope  # noqa: E402

class RegenError(Exception):
    """A validation failure on the exit-1 findings lane."""


def _repo_root(explicit: str | None) -> Path:
    """The project root under inspection -- never this script's own location.

    `Path(__file__).parents[2]` used to stand here. In the authoring checkout it
    lands on the repo root and looks right; in an installed plugin it lands on the
    plugin cache, so a gate run from a consuming project inspected the plugin's own
    copy of itself and reported the result as if it were the project's.
    """
    return _scope.resolve_repo_root(explicit)


def _tree_name(root: Path) -> str:
    """Resolve the tree directory; the schema's AGENTS.md lives inside it."""
    import importlib.util as _ilu

    path = Path(__file__).resolve().parent / "bionic_config.py"
    key = "_bionic_config"
    mod = sys.modules.get(key)
    if mod is None:
        spec = _ilu.spec_from_file_location(key, path)
        mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules[key] = mod
    return mod.resolve_tree_name(root)


def read_text_verbatim(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as fh:
        return fh.read()


def write_text_atomically(path: Path, text: str) -> None:
    mode = path.stat().st_mode
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        os.fchmod(fd, stat.S_IMODE(mode))
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _marker_line(marker: str) -> re.Pattern[str]:
    return re.compile(r"^[ \t]*" + re.escape(marker) + r"[ \t\r]*$", re.MULTILINE)


def find_region(text: str, label: str) -> tuple[int, int]:
    b = _marker_line(BEGIN).findall(text)
    e = _marker_line(END).findall(text)
    if len(b) == 0 or len(e) == 0:
        missing = "BEGIN" if len(b) == 0 else "END"
        raise RegenError(
            f"{label}: missing marker ({missing} not found as its own line). "
            f"Expected exactly one line {BEGIN!r} and one line {END!r}."
        )
    if len(b) > 1 or len(e) > 1:
        raise RegenError(
            f"{label}: duplicate marker (BEGIN x{len(b)}, END x{len(e)}). "
            "Exactly one of each is required; refusing to guess which pair is real."
        )
    mb = _marker_line(BEGIN).search(text)
    me = _marker_line(END).search(text)
    assert mb is not None and me is not None
    if me.start() < mb.end():
        raise RegenError(f"{label}: END marker precedes BEGIN marker.")
    return mb.end(), me.start()


def extract_phrases(description: str) -> list[str]:
    """Ordered, de-duplicated quoted trigger phrases in the first sentence.

    "First sentence" ends at a period followed by whitespace or end-of-text.
    A dotted identifier — `research.refresh_interval_days`, `plugin.json` —
    carries a period followed by a letter, so it no longer ends the sentence;
    splitting on any period truncated such a description mid-token and dropped
    every trigger phrase that came after it, leaving an empty routing cell
    while this regenerator's drift gate stayed clean.
    """
    m = _SENTENCE_END_RE.search(description)
    head = description[:m.start()] if m else description
    seen: set[str] = set()
    out: list[str] = []
    for m in _PHRASE_RE.finditer(head):
        phrase = m.group(1) if m.group(1) is not None else m.group(2)
        if phrase and phrase not in seen:
            seen.add(phrase)
            out.append(phrase)
    return out


def routing_phrases(entry: dict) -> list[str]:
    """A skill's routing triggers: the declared field first, the description second.

    rule:declared-triggers-first-prose-fallback, rule:description-is-prose-not-a-trigger-list,
    rule:an-undeclared-trigger-is-a-reported-gap.

    `metadata.triggers` is the DECLARED source, projected into the catalog as a list.
    It exists because the descriptions used to carry their triggers inline as quoted
    spans, and shortening them to fit a discovery surface without truncation took all
    270 of those phrases with it -- every user-facing row of this table lost its cell
    while this regenerator's own drift gate stayed clean, because an empty cell is not
    drift. The phrases now live in a field a shortening cannot reach.

    The description fallback is kept rather than removed: a skill that still writes its
    triggers inline keeps routing, so the declared field is additive and no skill is
    forced to change. Declaring the field WINS over the description, so a skill that
    does both is read one way and not merged.
    """
    declared = entry.get("triggers") or []
    if isinstance(declared, str):
        declared = [tok.strip() for tok in declared.split("|") if tok.strip()]
    if declared:
        seen: set[str] = set()
        out: list[str] = []
        for phrase in declared:
            phrase = str(phrase).strip()
            if phrase and phrase not in seen:
                seen.add(phrase)
                out.append(phrase)
        return out
    return extract_phrases(entry.get("description", "") or "")


def _cell(text: str) -> str:
    """Escape a Markdown table cell: the pipe is the only structural character."""
    return text.replace("|", r"\|")


def build_region(skills: list[dict]) -> tuple[str, list[str]]:
    """Return (region_text, no_trigger_skill_ids)."""
    main_rows: list[tuple[str, str, str]] = []
    claude_only: list[tuple[str, str]] = []
    no_triggers: list[str] = []

    for entry in sorted(skills, key=lambda e: e["id"]):
        sid = entry["id"]
        note = entry.get("routing_note", "") or ""
        user_invocable = entry.get("user-invocable", True)
        if user_invocable is False:
            claude_only.append((sid, note))
            continue
        phrases = routing_phrases(entry)
        if not phrases:
            no_triggers.append(sid)
        phrase_cell = " / ".join(f'"{p}"' for p in phrases)
        main_rows.append((phrase_cell, sid, note))

    lines: list[str] = ["| User phrase | Skill | Notes |", "|---|---|---|"]
    for phrase_cell, sid, note in main_rows:
        lines.append(f"| {_cell(phrase_cell)} | `{sid}` | {_cell(note)} |")

    if claude_only:
        lines.append("")
        lines.append("### Claude-only (no user phrase)")
        lines.append("")
        lines.append("| Skill | Notes |")
        lines.append("|---|---|")
        for sid, note in claude_only:
            lines.append(f"| `{sid}` | {_cell(note)} |")

    # The region is the marker-delimited BODY: a leading and trailing newline so
    # the markers sit on their own lines after replacement.
    return "\n" + "\n".join(lines) + "\n", no_triggers


def run(root: Path, dry_run: bool) -> tuple[int, dict]:
    # SURFACE-ABSENT LANE, second trigger. rule:out-of-scope-is-surface-absent names
    # two: a plugin-authoring gate outside the authoring checkout, AND a projection
    # regenerator whose marked region exists nowhere. Only the first was implemented,
    # so the public clone and the staged release artifact -- both of which carry
    # `crux/scripts/` and so pass the first probe -- got exit 1 with an error naming a
    # file that never ships. A missing FILE is an absent surface; a file that is
    # present but carries no marker is still BROKEN, because that is a real defect in
    # a tree that owns the surface.
    skills_json = root / "crux" / "catalog" / "skills.json"
    if not skills_json.is_file():
        raise RegenError(f"{skills_json}: not found (run validate-catalog.py first)")
    try:
        skills = json.loads(skills_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RegenError(f"{skills_json}: invalid JSON ({exc})")
    if not isinstance(skills, list):
        raise RegenError(f"{skills_json}: expected a JSON array")

    region, no_triggers = build_region(skills)

    rel = f"{_tree_name(root)}/AGENTS.md"
    path = root / rel
    if not path.is_file():
        return 0, _scope.surface_absent_payload(
            reason=f"{rel} holds the routing-table region and is not present here; "
                   "this tree owns no region to project into")
    text = read_text_verbatim(path)
    i, j = find_region(text, rel)
    current = text[i:j]

    payload: dict = {"target": rel, "no_trigger_skills": no_triggers}
    if current == region:
        if dry_run:
            return 0, {"drift": False, "paths": [], **payload}
        return 0, {"written": [], **payload}

    if dry_run:
        return 1, {"drift": True, "paths": [rel], **payload}

    write_text_atomically(path, text[:i] + region + text[j:])
    return 0, {"written": [rel], **payload}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--dry-run", action="store_true", help="report drift; write nothing")
    ap.add_argument("--repo-root", default=None, help="repo root to inspect (default: the working directory)")
    args = ap.parse_args(argv)

    root = _repo_root(args.repo_root)

    # SURFACE-ABSENT LANE, on the `extract-code-docs` model. Every surface this
    # regenerator reads and every region it projects into lives in the plugin's own
    # source checkout. A consuming project owns none of them, so nothing here can
    # have drifted for that project and nothing can have been verified for it either.
    # Reporting a failure would file an inapplicable check as a defect in the reader's
    # repository; reporting a clean pass would file it as verified. This lane says
    # neither, and `check-drift` renders it N/A with the reason.
    if not _scope.is_authoring_checkout(root, __file__):
        return _scope.print_surface_absent()

    try:
        code, payload = run(root, args.dry_run)
    except RegenError as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        return 1
    except (OSError, UnicodeDecodeError) as exc:
        sys.stderr.write(f"generate-routing-table.py: {exc}\n")
        return 2

    print(json.dumps(payload, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
