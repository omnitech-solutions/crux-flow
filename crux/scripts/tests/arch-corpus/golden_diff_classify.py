#!/usr/bin/env python3
"""Classify a corpus golden diff, and refuse an unnamed extraction-content change.

ADR-0096 clause 12 makes byte-identity the split's gate: every corpus golden is
unchanged across it. After the split, three units deliberately move goldens, and
each is supposed to move a DIFFERENT and bounded thing:

  * retiring the confidence layer (clause 10) removes three `coverage.json` keys
    and touches no extracted content at all;
  * the honest stub line (clauses 3/4) rewrites stub sentences and coverage
    vocabulary and touches no route, table or edge;
  * detection (clause 6) changes extraction content, in two named repositories
    and nowhere else.

The risk this script exists to close is that a re-bless is a `--bless` flag and a
human eye. Nothing mechanical distinguishes "the three coverage keys came out"
from "a route table silently lost nine rows in the same commit". ADR-0096's own
Consequences say it plainly: an expectation edited to match a regression passes
the gate exactly as a careless re-bless does. This script narrows that to one
question a human must answer explicitly — *which repositories may change
extracted content in this unit* — and refuses everything else.

Three classes, assigned per changed line:

  `coverage-vocabulary`  a change confined to `_meta/coverage.json` that ADDS
                         or REMOVES a key in the set this script names. A key
                         appearing on both sides of one file's diff is a VALUE
                         change, not a vocabulary change, and is classed as
                         extraction content however the key is named. Changing
                         `inputs_found` is never vocabulary, because that is
                         what the extractor actually read.
  `stub-line`            a change to a stub sentence in a spine file — the line
                         a concern renders to name a non-`populated` verdict. A
                         stub line cannot carry a table row, a route or a graph
                         edge, so it cannot hide extraction movement. Matched
                         against the renderer's own grammar (`STUB_LINE_RE`),
                         NOT against "is a blockquote": every spine file also
                         renders blockquote-italic prose, and that prose is
                         extraction content.
  `extraction-content`   everything else. This is the class that means the
                         derived facts moved.

Exit 0 when no `extraction-content` hunk survives the allow-list; exit 1 when one
does, naming the repository and the line. Exit 2 for an environment problem —
not a git work tree, no diff obtainable — never a verdict.

Usage:
    uv run python3 crux/scripts/tests/arch-corpus/golden_diff_classify.py
    uv run python3 .../golden_diff_classify.py --allow-content djangoproject-com \\
                                               --allow-content sequelize-express-example

`--allow-content` names a repository whose extracted content this unit is
expected to move. It is per-repository and never global: there is deliberately
no `--allow-all`, because the whole value here is that the eight repositories
nobody claimed would move are still proven not to have moved.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
GOLDEN = HERE / "golden"
REPO_ROOT = HERE.parents[3]

# The `coverage.json` keys whose APPEARANCE OR DISAPPEARANCE is vocabulary
# rather than extraction. `inputs_found` is deliberately ABSENT: it lists the
# files the extractor actually read, so a change there is a change in what was
# extracted, and calling it vocabulary would let a real regression through the
# one gate meant to catch it.
#
# **Membership here is necessary and not sufficient**, and that is the whole
# repair. The earlier form bucketed by key NAME alone, so these three lines all
# classified as vocabulary:
#
#     "verdict": "stubbed",
#     "n_entities": 0,
#     "stub_reason": "no_entities",
#
# A `populated` concern regressing to `no_entities` moves exactly those lines in
# `coverage.json`, and in the spine file `_apply_stub_line` INSERTS the stub line
# rather than replacing content — the table rows stay, so the only `.md` change
# is one `stub-line`. The gate saw zero `extraction-content` and exited 0 over a
# repository that had just lost its extraction, which is the one thing it exists
# to refuse.
#
# `_side_keys` supplies the missing half: a key is vocabulary only when the diff
# for that file assigns it on ONE SIDE. A key on both sides is a value change,
# which is extraction content however it is named. That distinction is what the
# docstring always claimed ("adding or removing a coverage KEY is vocabulary")
# and what the code did not do.
COVERAGE_VOCABULARY_KEYS = frozenset({
    "confidence",
    "confidence_reason",
    "escalation_offered",
    "reason",
    "status",
    "inputs_missing",
    "verdict",
    "stub_reason",
    "expected",
    "found",
    "n_sources",
    "n_entities",
    "_note",
})

# A changed markdown line is a stub line when it MATCHES THE STUB-LINE GRAMMAR
# the renderer emits — not merely when it is a blockquote.
#
# The earlier form of this check was a prefix pair, `("> _no extractor for this
# stack", "> _")`, and the second entry subsumed the first: every
# blockquote-italic line classified as `stub-line`. Every spine renderer emits
# blockquote-italic PROSE too (`> _Derived from db/schema.rb._`), so that prefix
# could not tell a moved stub line from a moved prose cell — in the one unit
# where all 23 stub lines move and no extraction content may, which is exactly
# where the distinction has to hold.
#
# Two spellings are stub lines, and nothing else is:
#
#   legacy   `> _no extractor for this stack — empty-but-valid spine file._`
#   clause 4 `> _stub: <reason> — expected <expected>, found <found>._`
#
# `<reason>` is one of `core.StubReason`'s six members. That enum is the source
# of truth; this list is a deliberate copy, because this script is stdlib-only
# and standalone by design (importing `crux.arch` pulls the package's eager
# httpx chain). `test_golden_diff_classify.py` asserts the two agree, so the
# copy cannot drift silently.
STUB_REASONS = (
    "precondition_missing",
    "unsupported_stack",
    "ambiguous_stack",
    "ambiguous_package",
    "parse_failed",
    "no_entities",
)

STUB_LINE_RE = re.compile(
    r"^> _(?:"
    r"no extractor for this stack\b"
    r"|stub: (?:" + "|".join(STUB_REASONS) + r")\b"
    r")"
)


def _run(args: list[str]) -> str:
    try:
        proc = subprocess.run(args, cwd=REPO_ROOT, capture_output=True, text=True)
    except OSError as exc:                       # git missing entirely
        print(f"golden_diff_classify: cannot run git: {exc}", file=sys.stderr)
        raise SystemExit(2)
    if proc.returncode not in (0, 1):
        print(f"golden_diff_classify: git failed: {proc.stderr.strip()}", file=sys.stderr)
        raise SystemExit(2)
    return proc.stdout


def _repo_of(path: str) -> str:
    """The corpus repository a golden path belongs to, or '' when it is not
    under `golden/`."""
    marker = "arch-corpus/golden/"
    if marker not in path:
        return ""
    return path.split(marker, 1)[1].split("/", 1)[0]


def _coverage_key(line: str) -> str | None:
    """The JSON key a changed `coverage.json` line assigns, if it assigns one.

    `coverage.json` is emitted with `indent=2, sort_keys=True`, so an assignment
    line is always `"key": value`. A line that is only structural — a brace, a
    bracket, an array element — has no key and is not vocabulary.
    """
    stripped = line.strip().rstrip(",")
    if not stripped.startswith('"'):
        return None
    head, sep, _ = stripped.partition(":")
    if not sep:
        return None
    try:
        return json.loads(head)
    except ValueError:
        return None


def classify_line(path: str, line: str, both_sided_keys: frozenset[str] = frozenset()) -> str:
    """Classify one changed line (without its leading +/-).

    `both_sided_keys` names the `coverage.json` keys this file's diff assigns on
    BOTH sides — the keys whose value changed rather than appeared or vanished.
    A key in that set is never vocabulary, whatever `COVERAGE_VOCABULARY_KEYS`
    says. Defaulting it empty keeps single-line classification callable on its
    own, and `classify_diff` always supplies the real set.
    """
    if path.endswith("_meta/coverage.json"):
        key = _coverage_key(line)
        if key is not None:
            if key in COVERAGE_VOCABULARY_KEYS and key not in both_sided_keys:
                return "coverage-vocabulary"
            return "extraction-content"
        if not line.strip().startswith('"'):
            # A brace or bracket that moved because a key above it did.
            return "coverage-vocabulary"
        return "extraction-content"
    if path.endswith(".md"):
        if STUB_LINE_RE.match(line):
            return "stub-line"
        return "extraction-content"
    return "extraction-content"


def _changed_lines(diff: str) -> list[tuple[str, str, str]]:
    """`(path, sign, line)` for every changed line in a unified diff.

    Two properties this walk has and the previous one did not.

    **A deleted file is attributed to ITS OWN path.** `git diff` writes
    `+++ /dev/null` for a deletion, which matches no `+++ b/` prefix, so the
    previous walk left `path` pointing at whatever file came before — every
    removed row of repository B landed under repository A, and an
    `--allow-content A` licensed a deletion in B. The `--- a/` side is tracked
    too, and the current path is the `b` side when there is one and the `a` side
    otherwise. `+++ /dev/null` also stopped being counted as a changed line,
    which it was.

    **Header lines are recognized by POSITION, not by prefix.** `---` and `+++`
    are file headers only before the first `@@` of a file. Inside a hunk they are
    content: a removed line reading `-- x` is written `--- x`, and reading that
    as a header would silently retarget every line after it.
    """
    out: list[tuple[str, str, str]] = []
    a_path = b_path = ""
    in_hunk = False
    for raw in diff.splitlines():
        if raw.startswith("diff --git"):
            a_path = b_path = ""
            in_hunk = False
            continue
        if raw.startswith("@@"):
            in_hunk = True
            continue
        if not in_hunk:
            if raw.startswith("--- "):
                rest = raw[4:]
                a_path = "" if rest == "/dev/null" else rest[2:] if rest.startswith("a/") else rest
                continue
            if raw.startswith("+++ "):
                rest = raw[4:]
                b_path = "" if rest == "/dev/null" else rest[2:] if rest.startswith("b/") else rest
                continue
            continue                      # index / mode / similarity headers
        if not raw or raw[0] not in "+-":
            continue
        line = raw[1:]
        if not line.strip():
            continue
        path = b_path or a_path
        if path:
            out.append((path, raw[0], line))
    return out


def _side_keys(changed: list[tuple[str, str, str]]) -> dict[str, frozenset[str]]:
    """Per `coverage.json` path, the keys assigned on BOTH sides of its diff.

    Scoped to one file rather than the whole diff, because `coverage.json` holds
    one record per concern: a key added for one concern and changed for another
    within the same file is the conservative case, and it falls to extraction
    content, which is the safe direction for a gate.
    """
    sides: dict[str, dict[str, set[str]]] = {}
    for path, sign, line in changed:
        if not path.endswith("_meta/coverage.json"):
            continue
        key = _coverage_key(line)
        if key is None:
            continue
        sides.setdefault(path, {"+": set(), "-": set()})[sign].add(key)
    return {path: frozenset(s["+"] & s["-"]) for path, s in sides.items()}


def classify_diff(diff: str) -> dict[str, dict[str, list[str]]]:
    """Walk a unified diff and bucket every changed line by repository and class."""
    changed = _changed_lines(diff)
    both_sided = _side_keys(changed)
    findings: dict[str, dict[str, list[str]]] = {}
    for path, sign, line in changed:
        repo = _repo_of(path)
        if not repo:
            continue
        cls = classify_line(path, line, both_sided.get(path, frozenset()))
        findings.setdefault(repo, {}).setdefault(cls, []).append(f"{path}: {sign}{line.strip()}")
    return findings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--allow-content", action="append", default=[], metavar="REPO",
                    help="a corpus repository whose extracted content this unit "
                         "is expected to move (repeatable; never global)")
    ap.add_argument("--json", action="store_true", help="emit the classification as JSON")
    args = ap.parse_args(argv)

    if not GOLDEN.is_dir():
        print(f"golden_diff_classify: no golden tree at {GOLDEN}", file=sys.stderr)
        return 2

    diff = _run(["git", "diff", "--no-renames", "--", str(GOLDEN)])
    diff += _run(["git", "diff", "--no-renames", "--cached", "--", str(GOLDEN)])
    findings = classify_diff(diff)

    allowed = set(args.allow_content)
    unknown = allowed - {p.name for p in GOLDEN.iterdir() if p.is_dir()}
    if unknown:
        print("golden_diff_classify: --allow-content names a repository that is "
              f"not in the corpus: {sorted(unknown)}", file=sys.stderr)
        return 2

    offenders = {
        repo: lines["extraction-content"]
        for repo, lines in findings.items()
        if "extraction-content" in lines and repo not in allowed
    }

    if args.json:
        print(json.dumps({
            "moved": sorted(findings),
            "allowed_content": sorted(allowed),
            "classes": {r: {c: len(v) for c, v in d.items()} for r, d in findings.items()},
            "unnamed_extraction_content": {r: v for r, v in offenders.items()},
        }, indent=2, sort_keys=True))
    else:
        if not findings:
            print("golden_diff_classify: no golden moved.")
        for repo in sorted(findings):
            classes = findings[repo]
            summary = ", ".join(f"{c}={len(classes[c])}" for c in sorted(classes))
            flag = "  [content allowed]" if repo in allowed else ""
            print(f"{repo}: {summary}{flag}")
        for repo in sorted(offenders):
            print(f"\nREFUSED — {repo} moved extraction content and was not named "
                  f"by --allow-content:", file=sys.stderr)
            for line in offenders[repo][:20]:
                print(f"    {line}", file=sys.stderr)
            extra = len(offenders[repo]) - 20
            if extra > 0:
                print(f"    ... and {extra} more", file=sys.stderr)

    return 1 if offenders else 0


if __name__ == "__main__":
    raise SystemExit(main())
