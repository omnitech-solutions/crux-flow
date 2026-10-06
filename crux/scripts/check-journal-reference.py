#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""check-journal-reference.py — the CLN-JR-1 reference decision, as a command.

`cleanup-campsite` CLN-JR-1 invokes this once per artifact it found in a
recent `adr`/`promptbook`/`schema` log op. It reads one month file and, when
the tree has one, the summaries resolver; it writes nothing.

The decision itself lives in `crux/scripts/journal_reference.py` and is
shared, never re-implemented here — this script owns the filesystem guards
and the exit contract only.

Usage:
  check-journal-reference.py --artifact ADR-0111 --month 2026-09 [--repo-root DIR]

Exit: 0 the artifact is referenced · 1 it is not (JSON on stdout, carrying
`rejected` and `unverifiable` so a finding can say why) · 2 environment or
usage failure (stderr).

An ABSENT RESOLVER is exit 1 with `resolver_available: false`, never exit 2:
a tree with no summaries projection is the normal downstream state, and the
wiki-link lane still decides.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bionic_config import BionicConfigError, load_config  # noqa: E402
from journal_reference import artifact_references  # noqa: E402
from untrusted import MESSAGE_LIMIT, redact  # noqa: E402

MONTH_RE = re.compile(r"^[0-9]{4}-(0[1-9]|1[0-2])\Z")
#: `ADR-NNNN`, `OBS-NNNN`, or `PB-NNNN-<slug>` — the artifact shapes CLN-JR-1
#: lifts out of a log subject. A closed grammar, so a forged subject cannot
#: steer this script at an arbitrary path fragment.
ARTIFACT_RE = re.compile(r"^(?:(?:ADR|OBS)-[0-9]{4}|PB-[0-9]{4}-[a-z0-9-]+)\Z")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Does a journal month reference an artifact?")
    ap.add_argument("--artifact", required=True)
    ap.add_argument("--month", required=True, help="YYYY-MM")
    ap.add_argument("--repo-root", default=".")
    args = ap.parse_args(argv)

    if not MONTH_RE.fullmatch(args.month):
        sys.stderr.write(f"check-journal-reference: {args.month!r} is not a valid YYYY-MM month\n")
        return 2
    if not ARTIFACT_RE.fullmatch(args.artifact):
        sys.stderr.write(
            f"check-journal-reference: {redact(args.artifact, quoted=True)} is not an "
            "ADR-NNNN, OBS-NNNN or PB-NNNN-<slug> artifact id\n")
        return 2

    try:
        root = Path(args.repo_root).resolve()
        config = load_config(root, require_tree=True)
        tree = config.docs_root
        month_file = tree / "journal" / f"{args.month}.md"
        if month_file.is_symlink() or not month_file.is_file():
            raise OSError(
                f"{config.docs_dir}/journal/{args.month}.md is not a regular file")
        text = month_file.read_text(encoding="utf-8")
    except (BionicConfigError, OSError, UnicodeDecodeError) as exc:
        sys.stderr.write(
            f"check-journal-reference: {redact(exc, quoted=False, limit=MESSAGE_LIMIT)}\n")
        return 2

    resolver = None
    resolver_path = tree / "adrs" / "summaries" / "resolver.json"
    if resolver_path.is_file() and not resolver_path.is_symlink():
        try:
            parsed = json.loads(resolver_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            # A projection this script cannot parse is the same situation as
            # one that is absent: the slug lane is unverifiable. It is NOT an
            # environment failure, because the wiki-link lane still decides
            # and the payload says the resolver was unavailable.
            parsed = None
        resolver = parsed if isinstance(parsed, dict) else None

    result = artifact_references(text, args.artifact, resolver)
    result["month"] = args.month
    print(json.dumps(result, sort_keys=True))
    return 0 if result["referenced"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
