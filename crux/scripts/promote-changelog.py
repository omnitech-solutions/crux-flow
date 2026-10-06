#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Promote `## [Unreleased]` to a versioned section in CHANGELOG.md.

Per [[adrs/ADR-0009-automate-changelog-promotion-during-release]]. Stdlib only.

Promotes the contents of `## [Unreleased]` under a new
`## [X.Y.Z] — YYYY-MM-DD` heading (em-dash, U+2014, matching the existing
file convention; no `v` prefix inside the brackets). Leaves a fresh empty
`## [Unreleased]` shell behind with `### Added` / `### Changed` /
`### Fixed` / `### Removed` subsection stubs. Optionally writes the
promoted section's body (no heading, no version, no date — just the
bullets/prose) to a separate file for use as the GitHub Release body via
`softprops/action-gh-release`'s `body_path:`.

Same exit-code semantics as validate-catalog.py / extract-code-docs.py:
  0 — clean (changes written, or --dry-run with no validation failures)
  1 — validation failure (missing Unreleased, version already promoted,
      malformed version, missing file) — stderr explains
  non-zero with empty stdout — crash; surface stderr

Atomicity: all validation runs before any filesystem mutation. Both
CHANGELOG.md and the optional release-notes-out file are written via
atomic temp-file replacement (write to <path>.tmp, then os.replace).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SEMVER_RE = re.compile(r"^v?(\d+)\.\d+\.\d+(-[A-Za-z0-9.-]+)?$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SECTION_RE = re.compile(r"^## \[([^\]]+)\]")
FENCE_RE = re.compile(r"^```")
SUBSECTION_RE = re.compile(r"^### ")
HTML_COMMENT_LINE_RE = re.compile(r"^\s*<!--.*-->\s*$")
EMPTY_FILL_BULLET = "- No user-facing changes."

# crux/scripts/promote-changelog.py  →  parent×3 == repo root.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _atomic_write_text(path: Path, body: str) -> None:
    """Atomic UTF-8 text write with no platform newline translation.

    Writes to <path>.tmp then os.replace()s into place. Bytes are written
    directly so '\\n' is NOT translated to '\\r\\n' on Windows — that would
    shift the file's sha256 across platforms.

    [SECURITY:S5] Neither the target nor `<path>.tmp` may be a symlink. The
    tmp path is predictable and `Path.write_bytes` follows a link, so a
    pre-created link turns a release promotion into a write into someone
    else's file, while `os.replace` moves the tmp PATH and leaves the link
    standing. Checked explicitly for a readable error, then created
    O_NOFOLLOW|O_EXCL so the check is not a TOCTOU window. This helper keeps
    BEHAVIORAL parity with its siblings in extract-code-docs.py,
    validate-catalog.py, and signoff-backfill.py — same guards, same order,
    same exception type — and is not byte-identical: each names its own
    subject in its messages ("changelog content" here). Treat the messages
    as the only licensed difference. Change one, change all.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    for label, candidate in (("target", path), ("temporary file", tmp)):
        if candidate.is_symlink():
            raise OSError(
                f"refusing to write {path}: the {label} {candidate} is a symlink — "
                "writing through it would put changelog content in the link's target"
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
        # Clean up partial tmp before re-raising (SC-1).
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise


def _normalize_version(raw: str) -> str:
    """Strip leading `v`/`V`, validate against SEMVER_RE, return canonical form."""
    if not SEMVER_RE.match(raw):
        raise ValueError(f"invalid version string: {raw!r} (expected X.Y.Z or X.Y.Z-prerelease)")
    return raw[1:] if raw[:1] in ("v", "V") else raw


def _today_utc() -> str:
    """Return today's date in YYYY-MM-DD form, in UTC. Extracted for tests."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _find_unreleased_and_next(
    lines: list[str],
) -> tuple[int, int | None]:
    """Find the Unreleased heading index and the next `## [` heading index.

    Returns (unreleased_idx, next_section_idx_or_None). next_section_idx_or_None
    is None if Unreleased runs to EOF. Section headings inside fenced code
    blocks are ignored.

    Raises ValueError if:
      - no `## [Unreleased]` heading is found
      - more than one `## [Unreleased]` heading is found
    """
    unreleased_idx: int | None = None
    next_idx: int | None = None
    in_fence = False
    for i, line in enumerate(lines):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = SECTION_RE.match(line)
        if not match:
            continue
        tag = match.group(1)
        if tag == "Unreleased":
            if unreleased_idx is not None:
                raise ValueError("multiple `## [Unreleased]` sections found")
            unreleased_idx = i
        elif unreleased_idx is not None and next_idx is None:
            next_idx = i

    if unreleased_idx is None:
        raise ValueError("no `## [Unreleased]` section found")
    return unreleased_idx, next_idx


def _check_version_not_present(lines: list[str], version: str) -> None:
    """Raise ValueError if a heading for this version already exists.

    Match rule: ^## \\[v?<VERSION>\\] (no trailing anchor; tolerates em-dash,
    hyphen, en-dash, or no separator at all after the closing bracket).
    Fence-aware.
    """
    needle = re.compile(r"^## \[v?" + re.escape(version) + r"\]")
    in_fence = False
    for line in lines:
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if needle.match(line):
            raise ValueError(f"version {version} already promoted (heading present in CHANGELOG.md)")


def _unreleased_body_is_empty(body_lines: list[str]) -> bool:
    """Detect whether the Unreleased body has any content bullets/prose.

    Empty means: no line that, after lstrip(), is non-empty AND not a
    `### ` subsection heading AND not an HTML-comment line.
    """
    for line in body_lines:
        stripped = line.lstrip()
        if not stripped:
            continue
        if SUBSECTION_RE.match(stripped):
            continue
        if HTML_COMMENT_LINE_RE.match(line):
            continue
        return False
    return True


def _build_promoted_body(body_lines: list[str], auto_filled: bool) -> list[str]:
    """Return the lines that go under the promoted heading.

    On auto-fill: returns a single bullet line "- No user-facing changes."
    Otherwise: returns the original Unreleased body lines verbatim, trimmed
    of leading/trailing blank lines.
    """
    if auto_filled:
        return [EMPTY_FILL_BULLET]
    # Trim leading blank lines
    start = 0
    while start < len(body_lines) and not body_lines[start].strip():
        start += 1
    # Trim trailing blank lines
    end = len(body_lines)
    while end > start and not body_lines[end - 1].strip():
        end -= 1
    return body_lines[start:end]


def _build_release_notes_text(promoted_body: list[str]) -> str:
    """Return the release-notes file content (body only; no heading)."""
    return "\n".join(promoted_body) + "\n"


def _build_fresh_unreleased_shell() -> list[str]:
    """Return the lines for a fresh empty `## [Unreleased]` section.

    Order: Added, Changed, Fixed, Removed (Keep a Changelog convention).
    Layout matches the existing file's visual rhythm (blank line between
    every heading).
    """
    return [
        "## [Unreleased]",
        "",
        "### Added",
        "",
        "### Changed",
        "",
        "### Fixed",
        "",
        "### Removed",
        "",
    ]


def promote(
    text: str,
    version: str,
    date_str: str,
) -> tuple[str, str, bool]:
    """Pure function: take the input file text, return (new_text, release_notes_text, auto_filled).

    Raises ValueError on validation failure. Called by the CLI after all
    file-read I/O; isolates the logic for unit testing.
    """
    had_trailing_newline = text.endswith("\n")
    lines = text.split("\n")
    # If the file ended with "\n", split adds an empty trailing element;
    # drop it so we operate on logical lines and re-add the newline on join.
    if had_trailing_newline and lines and lines[-1] == "":
        lines.pop()

    _check_version_not_present(lines, version)
    unreleased_idx, next_idx = _find_unreleased_and_next(lines)

    body_start = unreleased_idx + 1
    body_end = next_idx if next_idx is not None else len(lines)
    body_lines = lines[body_start:body_end]

    auto_filled = _unreleased_body_is_empty(body_lines)
    promoted_body = _build_promoted_body(body_lines, auto_filled)
    promoted_heading = f"## [{version}] — {date_str}"

    fresh_shell = _build_fresh_unreleased_shell()

    # Reassemble:
    # [prologue (everything before Unreleased)] +
    # [fresh Unreleased shell] +
    # [promoted heading] + [promoted body] + [blank] +
    # [everything from next_idx onward]
    prologue = lines[:unreleased_idx]
    tail = lines[body_end:] if next_idx is not None else []

    new_lines: list[str] = []
    new_lines.extend(prologue)
    new_lines.extend(fresh_shell)
    new_lines.append(promoted_heading)
    new_lines.append("")
    new_lines.extend(promoted_body)
    if tail:
        new_lines.append("")
        new_lines.extend(tail)

    new_text = "\n".join(new_lines)
    if had_trailing_newline:
        new_text += "\n"

    release_notes_text = _build_release_notes_text(promoted_body)

    return new_text, release_notes_text, auto_filled


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="promote-changelog",
        description=(
            "Promote `## [Unreleased]` in CHANGELOG.md to a new "
            "`## [X.Y.Z] — YYYY-MM-DD` heading and leave a fresh empty "
            "Unreleased shell. See ADR-0009."
        ),
    )
    parser.add_argument(
        "--version",
        required=True,
        help="Release version (e.g. 0.4.0 or v0.4.0; leading v is stripped).",
    )
    parser.add_argument(
        "--changelog",
        type=Path,
        default=REPO_ROOT / "CHANGELOG.md",
        help="Path to the CHANGELOG.md file (default: repo-root CHANGELOG.md).",
    )
    parser.add_argument(
        "--release-notes-out",
        type=Path,
        default=None,
        help="If set, write the promoted section body (no heading) to this file.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and compute the rewrite; print a JSON summary; do not write.",
    )
    parser.add_argument(
        "--date",
        default=None,
        help="Override the date stamp (YYYY-MM-DD). Default is today in UTC.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    try:
        version = _normalize_version(args.version)
    except ValueError as exc:
        print(f"promote-changelog: {exc}", file=sys.stderr)
        return 1

    if args.date is not None:
        if not DATE_RE.match(args.date):
            print(f"promote-changelog: --date must be YYYY-MM-DD, got {args.date!r}", file=sys.stderr)
            return 1
        date_str = args.date
    else:
        date_str = _today_utc()

    # [SECURITY:S5/NC6] Resolve only the PARENT directory; keep the final
    # component un-followed so a symlinked changelog is caught by
    # _atomic_write_text's is_symlink() guard. Calling .resolve() on the whole
    # path dereferenced the leaf, making that guard unreachable on the CLI path
    # — a planted CHANGELOG.md symlink was written straight through into its
    # target at exit 0. The parent is still resolved (so messages stay absolute
    # and a parent-dir symlink is followed as before); only the leaf changes.
    changelog_path = (
        args.changelog.parent.resolve() / args.changelog.name
        if args.changelog.exists()
        else args.changelog
    )
    if not changelog_path.is_file():
        print(f"promote-changelog: changelog not found: {changelog_path}", file=sys.stderr)
        return 1

    text = changelog_path.read_text(encoding="utf-8")

    try:
        new_text, release_notes_text, auto_filled = promote(text, version, date_str)
    except ValueError as exc:
        print(f"promote-changelog: {changelog_path}: {exc}", file=sys.stderr)
        return 1

    summary = {
        "version": version,
        "date": date_str,
        "auto_filled_empty": auto_filled,
        "would_write_changelog": True,
        "would_write_release_notes": args.release_notes_out is not None,
        "changelog_path": str(changelog_path),
        "release_notes_path": str(args.release_notes_out) if args.release_notes_out else None,
    }

    if args.dry_run:
        print(json.dumps(summary))
        return 0

    _atomic_write_text(changelog_path, new_text)

    if args.release_notes_out is not None:
        out_path = args.release_notes_out
        out_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(out_path, release_notes_text)

    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
