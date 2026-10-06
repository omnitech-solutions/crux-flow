#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""CI-gated public-release content scan per ADR-0034 §4.

Two coverage zones:

  Zone 1 — distributed surfaces (`crux/skills/`, `crux/agents/`,
  `crux/templates/`): fail on internal ADR references — the `ADR-\\d{4}`
  token form AND the literal `[[adrs/` wiki-link form (the latter catches
  slug-only links the token regex would miss). Placeholder spellings like
  "ADR-NNNN" intentionally do NOT match.

  Zone 2 — repo-wide (excluding `.git/` and the usual binary/cache dirs):
  fail on a case-insensitive match of the banned predecessor-workshop
  name. The name has no legitimate future use anywhere in the repo, so
  there is no content allowlist — only this checker and its test file are
  exempt (they necessarily construct the pattern; it is built from string
  parts and never written literally).

Exit-code semantics match check-no-stale-skill-names.py /
validate-catalog.py / extract-code-docs.py:
  0 — clean.
  1 — findings. One `<path>:<lineno>: <line>` row per hit on stdout.
  non-zero with empty/unparseable stdout — crash; surface stderr.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Final


sys.path.insert(0, str(Path(__file__).resolve().parent))
import authoring_scope as _scope  # noqa: E402


# ────────────────────────────── patterns ──────────────────────────────────
#
# The banned name is assembled from parts so this file never contains it
# literally (it is one of the two self-allowlisted paths, but keeping the
# literal out makes the allowlist a formality rather than a load-bearing
# exception).

_BANNED_NAME: Final[str] = "zi" + "ppy"
BANNED_NAME_RE: Final[re.Pattern[str]] = re.compile(re.escape(_BANNED_NAME), re.IGNORECASE)

# Zone 1: internal ADR references on distributed surfaces. `ADR-NNNN`
# (the documented placeholder spelling) does not match `\d{4}` by design.
ADR_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"ADR-\d{4}")
ADR_WIKILINK_LITERAL: Final[str] = "[[adrs/"

# Distributed surfaces, relative to the repo root.
DISTRIBUTED_SURFACES: Final[tuple[str, ...]] = (
    "crux/skills",
    "crux/agents",
    "crux/templates",
)

# ───────────────────────────── allowlist ──────────────────────────────────
#
# EXACTLY two paths: this checker and its unit test. Both necessarily
# construct the banned pattern (from parts). Nothing else is exempt.

_SELF_ALLOWLIST: Final[frozenset[str]] = frozenset(
    {
        "crux/scripts/check-public-release-content.py",
        "crux/scripts/tests/test_check_public_release_content.py",
    }
)


def _is_self_allowlisted(rel: Path) -> bool:
    return rel.as_posix() in _SELF_ALLOWLIST


# ───────────────────────────── scanning ───────────────────────────────────
#
# Skipped dirs/suffixes mirror check-no-stale-skill-names.py: binaries and
# dependency caches can't host load-bearing references; `.claude/` is an
# untracked self-referential plugin symlink that would recurse.


_SKIP_DIRS: Final[frozenset[str]] = frozenset(
    {
        ".git",
        ".claude",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        "dist",
        "build",
    }
)

_TEXT_SUFFIXES: Final[frozenset[str]] = frozenset(
    {
        ".md",
        ".py",
        ".json",
        ".yml",
        ".yaml",
        ".sh",
        ".toml",
        ".txt",
        ".tmpl",
        ".cfg",
        ".ini",
        ".html",
        ".css",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".rst",
    }
)


def _iter_text_files(root: Path):
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            # A committed file symlink can point outside --root; reading it
            # would scan (and echo into CI logs) out-of-tree content.
            continue
        if not path.is_file():
            continue
        if any(part in _SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        if path.suffix.lower() not in _TEXT_SUFFIXES and path.name not in {
            "Makefile",
            "Dockerfile",
        }:
            continue
        yield path


def _in_distributed_surface(rel: Path) -> bool:
    s = rel.as_posix()
    return any(s == surface or s.startswith(surface + "/") for surface in DISTRIBUTED_SURFACES)


# The shipped meta-ADR template is the one zone-1 file allowed to carry ADR
# tokens: its `id: ADR-0000` and H1 are the artifact's own identity, and
# init-docs installs the file INTO the target tree where that id resolves.
# It is not an internal-reference leak — the policy bans pointers into THIS
# repo's decision history, and this file ships with its referent.
_ZONE1_SELF_IDENTITY: Final[frozenset[str]] = frozenset(
    {"crux/templates/ADR-0000-record-architecture-decisions.md"}
)

# Within a self-identity file, the exemption is TOKEN-level, not file-level:
# only pure `ADR-0000` tokens pass. Any other numbered token, and the
# `[[adrs/` link form, are still flagged — so a future edit cannot smuggle a
# fresh internal reference past the gate under the identity exemption.
_SELF_IDENTITY_TOKEN: Final[str] = "ADR-0000"


def _line_is_pure_self_identity(line: str) -> bool:
    if ADR_WIKILINK_LITERAL in line:
        return False
    return all(
        tok == _SELF_IDENTITY_TOKEN for tok in ADR_TOKEN_RE.findall(line)
    )


def scan(root: Path) -> list[str]:
    """Return a sorted, de-duplicated list of `<rel>:<lineno>: <line>` rows
    for every zone-1 or zone-2 hit."""
    hits: list[str] = []
    for path in _iter_text_files(root):
        rel = path.relative_to(root)
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        lines = text.splitlines()
        zone1 = _in_distributed_surface(rel)
        self_identity = rel.as_posix() in _ZONE1_SELF_IDENTITY
        allowlisted = _is_self_allowlisted(rel)
        for i, line in enumerate(lines, start=1):
            # The two zones are independent per line — a zone-1 hit (or the
            # self-identity exemption swallowing one) must never shadow a
            # zone-2 hit on the same line, so no early continue here.
            row = f"{rel.as_posix()}:{i}: {line.strip()}"
            if zone1 and (ADR_TOKEN_RE.search(line) or ADR_WIKILINK_LITERAL in line):
                if not (self_identity and _line_is_pure_self_identity(line)):
                    hits.append(row)
            if not allowlisted and BANNED_NAME_RE.search(line):
                hits.append(row)
    return sorted(set(hits))


# ──────────────────────────────── main ────────────────────────────────────


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="check-public-release-content",
        description=(
            "Fail if a distributed surface carries internal ADR references, "
            "or if any file repo-wide mentions the banned predecessor-"
            "workshop name. Permanent CI gate per ADR-0034 §4."
        ),
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Repo root to scan (default: the working directory).",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print a summary line to stderr even on success.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = _scope.resolve_repo_root(args.root)

    # SURFACE-ABSENT LANE, the same one every plugin-authoring regenerator takes.
    # rule:out-of-scope-is-surface-absent. What this gate scans is the plugin's own
    # distributed surfaces, and a consuming project holds none of them. The default
    # root came from this file's own path, so a downstream run -- `tend-garden`
    # invokes this script -- scanned the INSTALLED plugin and exited 0, reporting a
    # verdict about the plugin as though it were a verdict about the reader's project.
    # The prose guard in `tend-garden` said not to do that; this makes it mechanical.
    if not _scope.is_authoring_checkout(root, __file__):
        sys.stderr.write(
            "check-public-release-content: not the plugin's authoring checkout; "
            "no distributed surface here to scan.\n"
        )
        return 0

    hits = scan(root)
    if hits:
        for hit in hits:
            print(hit)
        print(
            f"check-public-release-content: {len(hits)} finding(s).",
            file=sys.stderr,
        )
        return 1
    if args.verbose:
        print("check-public-release-content: clean.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
