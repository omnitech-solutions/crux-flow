# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""check_template_parity — dogfood ↔ distributed-template parity checker.

Reads a curated manifest of designated parity-bearing clauses. For each
clause it evaluates a TRIGGER PREDICATE: does the entry's own declared twin
template path exist on disk (resolved against --root)? If the twin is ABSENT
the entry is SKIPPED ENTIRELY — neither the parity check nor the stale-manifest
guard runs for it, and it contributes ZERO findings. This per-entry
self-detection guarantees that downstream installs (where crux/templates/*.tmpl
are not present) the whole run is a clean 0-finding no-op that cannot crash.

For entries whose twin IS present:
- The canonical file is read live; the relevant section is located by `anchor`
  and the discriminating value(s) are extracted via `pattern` (a Python regex).
- If the anchor/pattern no longer resolves in the canonical file: a STALE
  finding (severity P3) is emitted INSTEAD OF a parity result — never a
  vacuous green (a stale anchor would otherwise make the rule pass silently,
  the exact failure mode the rule exists to prevent).
- If the canonical values are present in the twin section: status OK.
- If any canonical value is absent from the twin section: status DRIFT,
  severity P2 (a drifted clause in a shipped template reaches every install).

N3 discipline is honored by DESIGN: the manifest stores only markers (an
`anchor` + a `pattern`), never the clause text itself — the value compared is
read live from the canonical source, so the manifest cannot silently become a
stale parallel copy of the clauses it guards.

Importable API:
    check_parity(manifest_path: Path, root: Path) -> list[dict]
    main(argv: list[str]) -> int

Exit codes:
    0 = parity or inert no-op (zero drift findings; P3-stale alone does NOT
        flip the exit to 1 — stale is a maintenance signal about the checker
        manifest, not evidence of shipped-template drift; downstream CI should
        not break on it)
    1 = drift found (one or more P2 DRIFT findings)
    2 = usage/manifest error (missing manifest, invalid JSON, malformed schema
        — incl. a clause entry missing a required key — or a non-UTF-8 file)

Usage:
    check_template_parity.py                         # default manifest, root = cwd
    check_template_parity.py --manifest M --root R   # explicit (used by tests)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# The CommonMark fenced-code-block subset is imported, never re-copied.
# `__file__`-derived and resolved, matching the insert `adr-signals.py`
# already uses for `untrusted`; no environment variable participates.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from md_fences import closes_fence as _closes_fence, \
    fence_marker as _fence_marker, \
    split_lines as _lines  # noqa: E402

DEFAULT_MANIFEST = Path(__file__).with_name("template_parity_manifest.json")

# Template placeholder tokens that are EXPECTED to differ between a dogfood
# file and its template twin; stripped before comparison so they never read
# as drift.
_PLACEHOLDER = re.compile(r"\{\{.*?\}\}|<[A-Z0-9_./-]+>")


def _normalize(text: str) -> str:
    text = _PLACEHOLDER.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip().lower()


# Required keys on every manifest clause entry. Validated up front so a
# malformed manifest fails as a usage error (exit 2) rather than a mid-scan
# KeyError traceback — and so validation runs even for entries whose twin is
# absent (a malformed entry is a real error everywhere; a well-formed entry
# with an absent twin is the inert downstream skip).
_REQUIRED_ENTRY_KEYS = ("id", "canonical", "twin", "anchor")


def _findall_strings(pattern: str, text: str) -> list[str]:
    """re.findall, but always returns plain strings. A pattern carrying capture
    group(s) makes re.findall return tuples (or, for one group, the group
    text); collapse those to a single comparable string per match so a grouped
    pattern can never raise AttributeError on `.lower()` downstream."""
    out: list[str] = []
    for m in re.finditer(pattern, text):
        out.append(m.group(0))
    return out


# `_fence_marker` and `_closes_fence` are imported from `md_fences` at the
# head of this file. They USED to be a hand-copy of the pair in
# `crux/scripts/adr-signals.py`, and both copies carried the same defect —
# the indent bound measured on spaces, the run matched after stripping ALL
# Unicode whitespace — which a differential test between the two could not
# see. The subset now lives in one module with one conformance suite.
#
# `_lines` comes from the same module, for the same reason. This file held
# the TENTH copy of the line split — nine were converted in `adr-signals.py`
# and this one was missed — and its failure mode was the quietest of the
# ten: a forged line boundary before an anchor makes `_section_after`
# return None, which reports P3 STALE, and P3 does not flip the exit code.
# Measured on a pair whose twin had genuinely dropped the governed value,
# the control exited 1 with a P2 DRIFT and one U+000B exited 0 with a P3
# stale.


def _section_after(text: str, anchor: str) -> str | None:
    """Return the section body from the line containing `anchor` up to the next
    Markdown heading, or None if the anchor is absent. Fence-aware on BOTH the
    anchor search and the heading-termination scan: lines inside a fence are
    content, never the real anchor heading and never a terminating heading
    (avoids the PB-0017 MF-1 fence-blindness class, where a fenced
    '## [YYYY-MM-DD] ...' example was mis-read as a heading and truncated the
    section — and the symmetric hazard where a fenced example that merely quotes
    the anchor text would be mistaken for the real heading). Tracked by fence
    character and run length, so a `~~~` fence, four backticks wrapping
    three-backtick content, an indented fence, and an unclosed fence are all
    read correctly — not just ``` alone.

    TWO LINE-SHAPE DECISIONS, BOTH BOUNDED TO SPACES AND TABS. `_lines`
    decides where a line ENDS and refuses the nine extra terminators
    `splitlines()` admits; the heading test below strips `" \t"` and refuses
    the same class before a `#`. Both were bare, and both produced the SAME
    quiet failure — a section that does not resolve reports P3 STALE, and P3
    does not flip the exit code, so the guarded clause is disabled and the
    drift ships green. Measured against a twin that had genuinely dropped the
    governed value: the control exits 1 with a P2 DRIFT; a U+000B before a
    fence run exited 0 through the split, and a U+00A0 before a `#` exited 0
    through this test. U+00A0 is NOT a `splitlines()` terminator, so the
    heading test was a second, independent way in rather than a second
    symptom of the first."""
    lines = _lines(text)
    start = None
    fence: tuple[str, int] | None = None
    for i, line in enumerate(lines):
        marker = _fence_marker(line)
        if fence is not None:
            if _closes_fence(marker, fence):
                fence = None
            continue
        if marker is not None:
            fence = (marker[0], marker[1])
            continue
        if anchor in line:
            start = i
            break
    if start is None:
        return None
    body = [lines[start]]
    fence = None
    for line in lines[start + 1 :]:
        marker = _fence_marker(line)
        if fence is not None:
            if _closes_fence(marker, fence):
                fence = None
            body.append(line)
            continue
        if marker is not None:
            fence = (marker[0], marker[1])
            body.append(line)
            continue
        if line.lstrip(" \t").startswith("#"):
            break
        body.append(line)
    return "\n".join(body)



def split_top_level_alternatives(pattern: str) -> list[str]:
    """Split a regex on its TOP-LEVEL `|` only.

    A `|` inside `(...)`, `[...]`, or escaped by a backslash belongs to a
    sub-expression and is not an alternative of the whole pattern. Splitting
    naively would manufacture fragments that never compile, and every one of
    them would look like a dead alternative.

    This lived in the test suite until the count comparison below needed it.
    It MOVED rather than being copied: a second hand-maintained copy of a
    parser is the defect class this file's own history already paid for
    (rule:parity-counts-match-on-both-sides).
    """
    parts: list[str] = []
    buf: list[str] = []
    depth = bracket = 0
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "\\" and i + 1 < len(pattern):
            buf.append(pattern[i:i + 2]); i += 2; continue
        if c == "[" and not bracket:
            bracket = 1
        elif c == "]" and bracket:
            bracket = 0
        elif not bracket and c == "(":
            depth += 1
        elif not bracket and c == ")":
            depth -= 1
        elif not bracket and depth == 0 and c == "|":
            parts.append("".join(buf)); buf = []; i += 1; continue
        buf.append(c); i += 1
    parts.append("".join(buf))
    parts = [p for p in parts if p.strip()]
    # A leading inline-flag group scopes the WHOLE pattern, but after the split it
    # sits on the first fragment only, so every later fragment compiles under
    # different flags than the pattern it came from. An anchored alternation in a
    # `(?m)` pattern then matched 0 times on BOTH sides, which this file reports as a
    # stale manifest entry rather than as the working alternation it is — a false
    # green for a real clause. No live manifest pattern trips this today; one
    # anchored alternation added to any of the three `(?m)` clauses would.
    if parts:
        flags = re.match(r"(?:\(\?[aiLmsux]+\))+", parts[0])
        if flags:
            prefix = flags.group(0)
            parts = [parts[0]] + [prefix + p for p in parts[1:]]
    return parts


def _count_comparison(pattern: str, canon_sec: str, twin_sec: str) -> list[dict]:
    """Per-alternation match counts on both sides (rule:parity-counts-match-on-both-sides).

    Returns one record per alternation whose counts disagree, or whose counts
    are equal and zero — equal counts of zero name text the manifest guards and
    neither file carries, which is a stale clause rather than a pass.
    """
    out: list[dict] = []
    for alt in split_top_level_alternatives(pattern):
        try:
            rx = re.compile(alt)
        except re.error as e:
            raise ValueError(
                f"manifest pattern alternation is not a valid regex: {alt!r}: {e}"
            ) from e
        nc = len(rx.findall(canon_sec))
        nt = len(rx.findall(twin_sec))
        if nc != nt or (nc == 0 and nt == 0):
            out.append({"alternation": alt, "canonical": nc, "twin": nt})
    return out


def _check_entry(entry: dict, root: Path) -> dict | None:
    """Check one manifest entry against the repo rooted at `root`.

    Returns a result dict, or None if the twin is absent (entry is skipped).
    The result dict carries at minimum: id, section, status, and — for
    actionable statuses — severity and detail.

    TRIGGER PREDICATE (ADR-0050 §3): if the entry's own declared twin path
    does NOT exist, return None (skip entirely — zero findings, no stale guard).
    The stale guard is gated behind twin-presence so it cannot fire downstream.
    """
    # Validate the entry shape FIRST (raises ValueError → main() maps to exit 2).
    # This runs before the twin-presence skip so a malformed manifest is a loud
    # usage error everywhere — including downstream — rather than a mid-scan
    # KeyError traceback. A well-formed entry whose twin is simply absent still
    # takes the inert skip below.
    missing_keys = [k for k in _REQUIRED_ENTRY_KEYS if k not in entry]
    if missing_keys:
        cid = entry.get("id", "<unnamed>")
        raise ValueError(
            f"manifest entry {cid!r} missing required key(s): {missing_keys}"
        )

    cid = entry["id"]
    section = entry.get("section", cid)
    canonical_path = root / entry["canonical"]
    twin_path = root / entry["twin"]
    anchor = entry["anchor"]
    pattern = entry.get("pattern")

    # Per-entry self-detection: twin absent → skip, zero findings.
    if not twin_path.is_file():
        return None

    # Twin is present — run both parity check and stale-manifest guard.
    if not canonical_path.is_file():
        # Canonical absent is unusual; treat as stale (checker/manifest issue).
        return {
            "id": cid,
            "section": section,
            "status": "STALE",
            "severity": "P3",
            "detail": (
                f"canonical file not found: {entry['canonical']} "
                f"(twin {entry['twin']} is present; manifest stale?)"
            ),
        }

    # Both files are read with universal-newline translation DISABLED. In
    # text mode Python rewrites a lone `\r` to `\n` before this function
    # sees the text, so a `\r` spelled mid-sentence became a real line
    # boundary that `_lines` could not refuse — it was already gone.
    # Measured: a `\r` before a fence run turned a live clause into an
    # exit-0 P3 stale, exactly as U+000B did through `splitlines()`.
    # `_lines` strips the one trailing `\r` a CRLF document leaves, so
    # CRLF still reads correctly — and that strip is only reachable at
    # all because of this argument.
    #
    # `open(newline="")` rather than `read_text(newline="")`: the latter
    # needs 3.13 and this script declares `requires-python = ">=3.9"`.
    # A non-UTF-8 file still raises UnicodeDecodeError here, which
    # `main` maps to the documented exit 2.
    with canonical_path.open(encoding="utf-8", newline="") as handle:
        canon_text = handle.read()
    with twin_path.open(encoding="utf-8", newline="") as handle:
        twin_text = handle.read()

    # BOTH sides are resolved before either is judged. Deciding on the canonical
    # alone cannot tell a one-sided loss (the surfaces disagree — DRIFT) from a
    # two-sided one (the manifest names a heading neither file carries — STALE),
    # and the difference is the exit status
    # (rule:one-sided-anchor-is-drift-absent-file-is-stale).
    canon_sec = _section_after(canon_text, anchor)
    twin_sec = _section_after(twin_text, anchor)

    if canon_sec is None and twin_sec is None:
        return {
            "id": cid,
            "section": section,
            "status": "STALE",
            "severity": "P3",
            "detail": (
                f"anchor {anchor!r} absent from both {entry['canonical']} and "
                f"twin {entry['twin']} (manifest stale?)"
            ),
        }
    if canon_sec is None or twin_sec is None:
        short = entry["canonical"] if canon_sec is None else entry["twin"]
        other = entry["twin"] if canon_sec is None else entry["canonical"]
        return {
            "id": cid,
            "section": section,
            "status": "DRIFT",
            "severity": "P2",
            "detail": (
                f"section {section} (anchor {anchor!r}) is absent from {short} "
                f"but present in {other} — the two surfaces disagree"
            ),
        }

    if pattern:
        # An invalid manifest regex is a manifest schema error, not a crash:
        # map re.error to ValueError so main() returns the documented exit 2
        # with a clean message (consistent with the other manifest-error paths)
        # rather than letting a raw traceback escape.
        try:
            canon_vals = _findall_strings(pattern, canon_sec)
        except re.error as e:
            raise ValueError(
                f"manifest pattern is not a valid regex: {pattern!r}: {e}"
            ) from e
        if not canon_vals and not _findall_strings(pattern, twin_sec):
            # Pattern found nothing on EITHER side — stale manifest entry. The
            # two-sided test is load-bearing: a pattern matching nothing in the
            # canonical while the twin still carries it is a one-sided loss,
            # which the count comparison below reports as drift. Returning stale
            # here on the canonical alone would exit 0 on exactly the deletion
            # this check exists to catch (rule:one-sided-anchor-is-drift-absent-file-is-stale).
            return {
                "id": cid,
                "section": section,
                "status": "STALE",
                "severity": "P3",
                "detail": (
                    f"pattern {pattern!r} matched nothing in section {section} of "
                    f"either {entry['canonical']} or {entry['twin']} (manifest stale?)"
                ),
            }
        # rule:parity-counts-match-on-both-sides — each alternation must match the
        # same number of times on both sides. This runs BEFORE the value comparison
        # because a surviving sibling alternation would otherwise mask a deletion.
        count_findings = _count_comparison(pattern, canon_sec, twin_sec)
        if count_findings:
            dead = [f for f in count_findings if f["canonical"] == 0 and f["twin"] == 0]
            if len(dead) == len(count_findings):
                return {
                    "id": cid,
                    "section": section,
                    "status": "STALE",
                    "severity": "P3",
                    "detail": (
                        f"alternation(s) {[f['alternation'] for f in dead]} match neither "
                        f"{entry['canonical']} nor {entry['twin']} in section {section} "
                        f"(manifest stale?)"
                    ),
                }
            live = [f for f in count_findings if not (f["canonical"] == 0 and f["twin"] == 0)]
            # The side that holds fewer is named PER ALTERNATION. Deriving one label
            # from the first record and applying it to the whole clause sent a
            # maintainer to the wrong file whenever two alternations drifted in
            # opposite directions.
            parts = ", ".join(
                f"{f['alternation']!r} canonical={f['canonical']} twin={f['twin']} "
                f"({'canonical' if f['canonical'] < f['twin'] else 'twin'} holds fewer)"
                for f in live
            )
            detail = f"match counts differ in section {section}: {parts}"
            if dead:
                # A dead-on-both alternation is reported here too. Dropping it because
                # a sibling drifted hid a stale manifest entry at ANY severity, where
                # the same entry alone is reported at P3.
                detail += (
                    f"; alternation(s) {[f['alternation'] for f in dead]} match neither "
                    f"side (manifest stale?)"
                )
            return {
                "id": cid,
                "section": section,
                "status": "DRIFT",
                "severity": "P2",
                "detail": detail,
            }

        twin_norm = _normalize(twin_sec)
        # Normalize BOTH sides symmetrically: a captured canonical value
        # containing collapsible whitespace or a template placeholder is run
        # through the same _normalize() as the twin before the substring test,
        # so an in-sync clause whose value carries such characters cannot read
        # as a false DRIFT. (`_normalize("")` is "" — empty matches are dropped.)
        # Containment (not positional/equality) comparison: parity means each
        # canonical value is present SOMEWHERE in the normalized twin section, so
        # the manifest author must choose a `pattern` specific enough that an
        # incidental twin occurrence elsewhere in the section can't mask real
        # drift (the rule's known coverage limitation; deferred to ADR-0050's
        # follow-on for an equality-vs-containment semantics change).
        missing = [
            v
            for v in dict.fromkeys(canon_vals)
            if (nv := _normalize(v)) and nv not in twin_norm
        ]
        if missing:
            return {
                "id": cid,
                "section": section,
                "status": "DRIFT",
                "severity": "P2",
                "detail": (
                    f"canonical value(s) {missing} present in {entry['canonical']} "
                    f"section {section} but absent from twin {entry['twin']}"
                ),
            }
        return {"id": cid, "section": section, "status": "OK", "detail": "parity"}

    # No pattern: compare whole normalized section bodies.
    if _normalize(canon_sec) != _normalize(twin_sec):
        return {
            "id": cid,
            "section": section,
            "status": "DRIFT",
            "severity": "P2",
            "detail": (
                f"section {section} body differs between {entry['canonical']} "
                f"and twin {entry['twin']}"
            ),
        }
    return {"id": cid, "section": section, "status": "OK", "detail": "parity"}


def check_parity(manifest_path: Path, root: Path) -> list[dict]:
    """Run all manifest entries against the repo rooted at `root`.

    Returns a list of result dicts — one per entry that was NOT skipped (i.e.
    whose twin was present). Skipped entries (twin absent, per-entry
    self-detection) contribute zero items to the list. Each result dict
    carries: id, section, status ("OK" | "DRIFT" | "STALE"), detail, and —
    for DRIFT/STALE — severity ("P2" | "P3").

    Raises ValueError on manifest parse/schema errors — a non-list `clauses`
    or a clause entry missing a required key (caller maps to exit 2).
    """
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = manifest.get("clauses", [])
    if not isinstance(entries, list):
        raise ValueError("manifest 'clauses' must be a list")

    results = []
    for entry in entries:
        result = _check_entry(entry, root)
        if result is not None:
            results.append(result)
    return results


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description="dogfood↔template parity checker (ADR-0050)"
    )
    ap.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help="path to the parity manifest JSON (default: template_parity_manifest.json "
        "beside this script)",
    )
    ap.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="repo root; entry file paths are resolved relative to it (default: cwd)",
    )
    args = ap.parse_args(argv)

    if not args.manifest.is_file():
        print(f"[error] manifest not found: {args.manifest}", file=sys.stderr)
        return 2
    try:
        results = check_parity(args.manifest, args.root)
    except json.JSONDecodeError as e:
        print(f"[error] manifest is not valid JSON: {e}", file=sys.stderr)
        return 2
    except UnicodeDecodeError as e:
        # A canonical/twin file (or the manifest) is not valid UTF-8. This is a
        # ValueError subclass, so it would also be caught below; handled here
        # only to label it accurately rather than as a "schema error".
        print(f"[error] file is not valid UTF-8: {e}", file=sys.stderr)
        return 2
    except ValueError as e:
        print(f"[error] manifest schema error: {e}", file=sys.stderr)
        return 2

    n_entries = len(results)
    drift = [r for r in results if r["status"] == "DRIFT"]
    stale = [r for r in results if r["status"] == "STALE"]

    print(
        f"check-template-parity: {n_entries} clause(s) checked "
        f"({len(drift)} drift, {len(stale)} stale)"
    )
    _STATUS_MARK = {
        "OK": "  ok  ",
        "DRIFT": "DRIFT",
        "STALE": "stale",
    }
    for r in results:
        mark = _STATUS_MARK.get(r["status"], r["status"][:5].ljust(5))
        sev = f" [{r['severity']}]" if "severity" in r else ""
        print(f"  [{mark}]{sev} {r['id']} ({r['section']}): {r['detail']}")

    # Exit 0 on parity or inert no-op.
    # Exit 1 on DRIFT (P2 findings).
    # P3-stale alone does NOT flip exit to 1 (see module docstring rationale).
    return 1 if drift else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
