#!/usr/bin/env python3
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""check-promptbook-index.py — is the promptbook index complete against the books on disk?

WHY THIS EXISTS. The index has a rule already: the audit's CHK-PB-11 says a missing row is DRIFT
and the remedy is a rebuild from a directory walk. The rule was never the problem. The audit has
two execution paths, and on a tree large enough to route it to the delegated search agent -- which
this tree is -- the delegated walk enumerates seven other promptbook items and carries NO item for
CHK-PB-11. So the index was never inspected, and 107 rows sat against 109 books on disk through
however many audits. A rule whose only executor can drop it is a rule with no executor.

This is a CHECKER, not a regenerator, and that is a decision rather than an omission. Three inputs
block byte-exact regeneration today:

  * the index carries one contract-mandated LAGGING cell -- the `progress` cell of an active book's
    row, which CHK-PB-11 exempts because a prompt advance deliberately writes the run snapshot and
    the book pointer and never this index. A whole-file regenerator would rewrite that cell, and its
    drift gate would then fire on every audit during every live run: the exact outcome the exemption
    exists to prevent;
  * 30 of the 109 archived books carry no `archive_note`, so a regenerator has no machine source for
    their date cell and would have to invent one or preserve what it found, and preserving what it
    found is not regeneration;
  * the recent-runs section carries editorial free text ("abandoned (deliberate: blast-radius
    overshoot)") that no field holds.

A set comparison touches none of the three. It reads no date, no `progress` cell, no ordering and no
free text -- so the exemption never applies to it and no backfill is owed before it can run. A
regenerator remains the better end state; the honest sequence is backfill first, and that is a
separate decision.

THE MEMBERSHIP PREDICATE IS THE DIRECTORY WALK, and pinning it is load-bearing rather than
pedantic. Two predicates are available and they disagree on exactly the books this check was written
to find: a walk of `archive/` yields 109, while a `status: archived` frontmatter filter yields 107,
because two books were archived without their final book-side write. Under the second reading the
index is already consistent and this check would report nothing. The walk is what four contract
surfaces state -- "enumerate both `*.yaml` and `*.md` books in `active/` and `archive/`", "walk
active, runs and archive", extension-agnostic, `legacy/` excluded from every CHK-PB-* walk -- so the
walk is the predicate, and this check's verdict never consults a status field.

`legacy/` is excluded because it holds byte-preserved duplicates of migrated books; enumerating it
would demand a second row for every migrated book.

Exit codes follow the repository convention: 0 clean, 1 findings with valid JSON on stdout, 2
environment or input error (stderr, no stdout payload).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bionic_config as _cfg  # noqa: E402

# A row in the archived roster. The wiki-link is extension-less by contract, so the captured
# target is the book's filename stem.
_ARCHIVED_ROW = re.compile(r"^- \[\[promptbooks/archive/([^\]]+)\]\]", re.M)
# An active-table row. The link is lifecycle-neutral (no `active/` segment) by contract.
_ACTIVE_ROW = re.compile(r"^\|\s*\[\[promptbooks/(?:active/)?([^\]]+)\]\]", re.M)
_ARCHIVED_HEADING = re.compile(r"^## Archived \((\d+)\)", re.M)
_ACTIVE_HEADING = re.compile(r"^## Active \((\d+)\)", re.M)
# The tree index's promptbook rollup, which is a third surface that can disagree with both.
_TREE_ROLLUP = re.compile(r"^## Promptbooks \((\d+) active, (\d+) archived\)", re.M)

BOOK_SUFFIXES = (".yaml", ".md")


def book_stems(directory: Path) -> list[str]:
    """Every book filename stem in one promptbook directory, extension-agnostic.

    `index.md` is not a book. Nothing else in `active/` or `archive/` is anything but a book, and
    `legacy/` is never passed here -- the caller does not walk it.
    """
    if not directory.is_dir():
        return []
    out = []
    for p in sorted(directory.iterdir()):
        if not p.is_file() or p.suffix not in BOOK_SUFFIXES or p.name == "index.md":
            continue
        out.append(p.stem)
    return out


def _duplicates(names: list[str]) -> list[str]:
    seen, dupes = set(), []
    for n in names:
        if n in seen and n not in dupes:
            dupes.append(n)
        seen.add(n)
    return sorted(dupes)


def findings(root: Path, docs: Path) -> list[dict]:
    """Every completeness finding, sorted and deterministic. Empty means clean."""
    index = docs / "promptbooks" / "index.md"
    if not index.is_file():
        # Not a finding: a tree with no promptbook index has no index to be incomplete.
        # The caller reports this as surface-absent rather than as a defect.
        return []

    text = index.read_text(encoding="utf-8")
    rows = {
        "archived": _ARCHIVED_ROW.findall(text),
        "active": _ACTIVE_ROW.findall(text),
    }
    disk = {
        "archived": book_stems(docs / "promptbooks" / "archive"),
        "active": book_stems(docs / "promptbooks" / "active"),
    }
    headings = {
        "archived": _ARCHIVED_HEADING.search(text),
        "active": _ACTIVE_HEADING.search(text),
    }

    out: list[dict] = []
    for lane in ("active", "archived"):
        on_disk, in_index = set(disk[lane]), set(rows[lane])
        for stem in sorted(on_disk - in_index):
            out.append({
                "rule": "CHK-PB-11", "lane": lane, "defect": "missing-row", "book": stem,
                "problem": f"{lane} book {stem!r} is on disk and named in no index row",
                "remedy": "add its row; the index is rebuilt from a directory walk",
            })
        for stem in sorted(in_index - on_disk):
            out.append({
                "rule": "CHK-PB-11", "lane": lane, "defect": "dangling-row", "book": stem,
                "problem": f"the {lane} roster names {stem!r}, which is not on disk",
                # Row REMOVAL is the destructive case the audit contract puts behind a
                # confirmation, so this names the question rather than an automatic fix.
                "remedy": "confirm before removing: an orphan row is a destructive repair",
            })
        for stem in _duplicates(rows[lane]):
            out.append({
                "rule": "CHK-PB-11", "lane": lane, "defect": "duplicate-row", "book": stem,
                "problem": f"the {lane} roster names {stem!r} more than once",
                "remedy": "keep one row; a rebuild from the walk emits each book once",
            })
        m = headings[lane]
        if m is None:
            out.append({
                "rule": "CHK-PB-11", "lane": lane, "defect": "missing-heading", "book": None,
                "problem": f"the index carries no `## {lane.capitalize()} (N)` heading",
                "remedy": "restore the heading; its count is part of the checked shape",
            })
        elif int(m.group(1)) != len(rows[lane]):
            out.append({
                "rule": "CHK-PB-11", "lane": lane, "defect": "count-disagrees", "book": None,
                "problem": f"the {lane} heading says {m.group(1)} and the roster holds "
                           f"{len(rows[lane])} row(s)",
                "remedy": "the heading counts the rows below it",
            })

    # The third surface. The tree index carries its own promptbook rollup, and all three can
    # disagree independently -- measured here at 109 on disk, 107 in the roster, 108 in the rollup.
    tree_index = docs / "index.md"
    if tree_index.is_file():
        m = _TREE_ROLLUP.search(tree_index.read_text(encoding="utf-8"))
        if m:
            for lane, got in (("active", int(m.group(1))), ("archived", int(m.group(2)))):
                if got != len(disk[lane]):
                    out.append({
                        "rule": "CHK-PB-11", "lane": lane, "defect": "rollup-disagrees",
                        "book": None,
                        "problem": f"the tree index rollup says {got} {lane} and the directory "
                                   f"holds {len(disk[lane])}",
                        "remedy": "the rollup counts the books on disk",
                    })
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=".")
    ap.add_argument("--docs-dir", default=None)
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    try:
        docs = root / (args.docs_dir or _cfg.resolve_tree_name(root))
    except Exception as exc:  # noqa: BLE001 — an unresolvable tree is the environment lane
        print(f"check-promptbook-index: cannot resolve the documentation tree: "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    books = docs / "promptbooks"
    if not books.is_dir():
        # SURFACE-ABSENT, on the roster gates' own model: a tree with no promptbooks concern owes
        # no index, and reporting that as clean would be the false green the runner's classifier
        # exists to refuse. It neither fails nor passes, because it does not apply.
        print(json.dumps({"surface_absent": True, "drift": False,
                          "reason": "no promptbooks concern is present in this tree"},
                         sort_keys=True))
        return 0

    problems = findings(root, docs)
    payload = {
        "path": str((docs / "promptbooks" / "index.md").relative_to(root)),
        "validation_errors": problems,
        "checked": {
            "active_on_disk": len(book_stems(books / "active")),
            "archived_on_disk": len(book_stems(books / "archive")),
        },
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
