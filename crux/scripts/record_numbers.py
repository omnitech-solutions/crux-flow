"""record_numbers.py -- find two records that share one number.

A promptbook or an ADR takes its number from a monotonic counter, so two records
holding one number mean an allocation fault or a hand-copied file. This module reads
filenames only; it never opens a record.

Three namespaces, each checked on its own:

  books            files in promptbooks/active and promptbooks/archive, any extension
  run_directories  directories in promptbooks/runs
  adrs             files in adrs and adrs/archive

A book and its run directory carry the same number and are one record, so the books
and run_directories namespaces never collide with each other. promptbooks/legacy holds
byte-preserved copies of migrated books and runs, and no walk here enters it.

The number comes from the filename prefix `PB-NNNN` or `ADR-NNNN`, with an optional
artifact prefix. Two spellings of one number (`ADR-0001` and `CRX-ADR-0001`) count as
one number.

Stdlib only, so the checker script runs with no dependencies.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# WHY the optional artifact prefix is `{1,9}` after one capital: it admits `CRX-` and
# `PROJ-` style prefixes of 2 to 10 characters, so `CRX-ADR-0001` and `ADR-0001` are one
# number. WHY `[0-9]`, not `\d`: `\d` matches every Unicode decimal numeral, so an
# Arabic-Indic name would collide with its ASCII twin. Exactly 4 numerals; a 5-numeral
# number is deferred.
_ID = re.compile(r"^(?:[A-Z][A-Z0-9]{1,9}-)?(PB|ADR)-([0-9]{4})(?:[-.]|$)")

_LABEL = {"books": "book", "run_directories": "run directory", "adrs": "ADR"}
_ARTICLE = {"books": "a", "run_directories": "a", "adrs": "an"}


@dataclass(frozen=True)
class Record:
    kind: str        # "PB" or "ADR"
    number: int
    namespace: str   # "books" | "run_directories" | "adrs"
    name: str
    path: Path


def _entries(directory: Path, *, dirs: bool) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.iterdir()
                  if (p.is_dir() if dirs else p.is_file()) and p.name != "index.md")


def number_of(name: str) -> tuple[str, int] | None:
    """The `(kind, number)` a record's filename carries, else None."""
    m = _ID.match(name)
    return (m.group(1), int(m.group(2))) if m else None


def _records(paths: list[Path], namespace: str) -> list[Record]:
    out = []
    for p in paths:
        m = _ID.match(p.name)
        if m:
            out.append(Record(m.group(1), int(m.group(2)), namespace, p.name, p))
    return out


def scan_records(docs_dir: Path) -> list[Record]:
    """Every record the three namespaces hold, in a stable order."""
    docs = Path(docs_dir)
    pb = docs / "promptbooks"
    return (
        _records(_entries(pb / "active", dirs=False)
                 + _entries(pb / "archive", dirs=False), "books")
        + _records(_entries(pb / "runs", dirs=True), "run_directories")
        + _records(_entries(docs / "adrs", dirs=False)
                   + _entries(docs / "adrs" / "archive", dirs=False), "adrs")
    )


def count_records(docs_dir: Path) -> dict[str, int]:
    """How many records each namespace held, so a green can show what it covered."""
    counts = {ns: 0 for ns in _LABEL}
    for r in scan_records(docs_dir):
        counts[r.namespace] += 1
    return counts


def find_duplicate_numbers(docs_dir: Path, *, only: tuple[str, str, int] | None = None
                           ) -> list[str]:
    """One message per number held twice within a namespace. Empty means clean.

    `only`, a `(namespace, kind, number)` key, limits the report to that one record's
    number, so a caller checking one file is not blamed for another record's fault.
    """
    docs = Path(docs_dir)
    held: dict[tuple[str, str, int], list[Record]] = {}
    for r in scan_records(docs):
        held.setdefault((r.namespace, r.kind, r.number), []).append(r)
    messages = []
    for (namespace, kind, number), recs in sorted(held.items()):
        if len(recs) < 2 or (only is not None and only != (namespace, kind, number)):
            continue
        where = ", ".join(r.path.relative_to(docs).as_posix() for r in recs)
        messages.append(
            f"{kind}-{number:04d} is held by {len(recs)} {_LABEL[namespace]} "
            f"entries ({_ARTICLE[namespace]} {_LABEL[namespace]} number is allocated once): {where}"
        )
    return messages
