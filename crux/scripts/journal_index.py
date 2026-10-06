"""journal_index.py — the shipped derivation behind `<docs_dir>/journal/index.md`.

Implements `rule:journal-index-row-is-derived-on-every-write`[^derived-row] and
is read by `rule:journal-index-agrees-with-the-month-file`.[^agrees] Every cell
in a journal-index row is derived, on every write, from the entries the
month's own journal file holds — no cell is ever computed from the row it
replaces. The count comes from a fence-aware split built on the shared subset
in `crux/scripts/md_fences.py` (never re-implemented here); the two dates are
bounded by the month the file names; the category rollup is anchored on the
journal's closed category enum.

WRITTEN POLICY, VERBATIM, so a test can assert against it rather than against
this module's behaviour:

  A heading line is an entry only when its date group is four ASCII digits, a
  literal hyphen, two ASCII digits, a literal hyphen, two ASCII digits — never
  matched with `\\d`, which admits non-ASCII digit scripts — AND that captured
  string round-trips `datetime.date.fromisoformat` as a real calendar date. A
  heading failing either check is not an entry heading: it opens no entry, and
  it is left untouched as body prose. A heading whose category is not one of
  the enum members, case-sensitive and no superstring, is likewise not an
  entry heading. Such a heading IS reported: where its date is a real
  calendar date and its shape is otherwise exact, it is a near miss, and
  `unknown_category_headings` returns its 1-based line number and its
  category token so the caller can refuse the file. A heading whose date is
  not a real calendar date is not reported as a near miss, because the date
  is the defect and naming the category would send the reader to the wrong
  token. A heading whose date is well-formed and real but falls
  outside the month the file names contributes no entry to that month's
  derivation — the heading is still read, its date and category are still
  valid, but the entry is dropped from the count, the dates, and the rollup
  bounded by that month.

This module is stdlib-only and imports only `md_fences` for the fence subset
`journal_entries` tracks. It has ONE production importer,
`crux/scripts/generate-journal-index.py`, which owns every filesystem guard;
this module owns no I/O and reads no path. Its other importers are the test
files under `crux/scripts/tests/` that name it, and they are not listed here
— a list of test importers is a list that rots. `grep -rl journal_index crux`
is the current answer. The suites exist because these five names used to be
fixture-only helpers each test file defined for itself, so nothing shipped
what the tests measured.

[^derived-row]: rule:journal-index-row-is-derived-on-every-write
[^agrees]: rule:journal-index-agrees-with-the-month-file
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import date
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from md_fences import closes_fence, fence_marker, split_lines  # noqa: E402

# The journal's closed category enum, in the order bionic/AGENTS.md §4 states
# it. The rollup cell is anchored on it, so a forged heading cannot place an
# arbitrary token in the "top categories" cell.
CATEGORIES = (
    "decision", "implementation", "bug", "learning",
    "blocker", "refactor", "meeting", "review", "misc",
    # `release` joined the journal category enum when the `release` op was adopted
    # into the log-op enum. It was missing here for one release cycle, and the cost
    # was silent: an unrecognised category is read as prose, not as an entry, so the
    # month's count and its top-category cell both excluded every release entry
    # while the drift gate reported the index clean.
    "release",
)

# The month cell's own grammar: a four-ASCII-digit year, a real two-digit
# month, nothing else. `[0-9]`, never `\d` — `\d` admits non-ASCII digit
# scripts under Python's default (non-`re.ASCII`) matching, which both sorts
# above every ASCII month string and passes a forged filename grammar built
# the same way. `fullmatch`, never `match`+`$` — `$` admits a trailing
# newline `match` would otherwise accept. Shared by the driver's filename
# grammar, the `--month` CLI validation, and the render-time cell assertion
# below, so one definition governs all three uses.
MONTH_RE = re.compile(r"^[0-9]{4}-(0[1-9]|1[0-2])\Z")

# The entry-heading pattern. The date group is spelled with `[0-9]`, never
# `\d`: `\d` with no `re.ASCII` flag matches any Unicode decimal digit, so
# `## [2026-09-９９ 10:00] bug | x` (fullwidth digits) would match a
# `\d`-based pattern, pass a naive month-prefix check, and land a non-ASCII
# string in the "last entry" cell — and a two-digit non-ASCII day sorts above
# every ASCII date under plain string comparison. `[0-9]` refuses both. The
# captured date is validated separately, in `journal_entries`, against
# `date.fromisoformat` — a well-formed-but-unreal date such as `2026-09-99`
# passes this regex and is caught there instead, so the written policy above
# stays true in one place.
ENTRY_RE = re.compile(
    r"^## \[(?P<date>[0-9]{4}-[0-9]{2}-[0-9]{2}) [0-9]{2}:[0-9]{2}\] "
    r"(?P<category>" + "|".join(CATEGORIES) + r") \| \S"
)

# The near-miss pattern: an entry heading in every respect except the token
# in its category slot. `\S+` is the widest token that slot can hold, so a
# category the enum does not carry is CAUGHT here rather than falling through
# `ENTRY_RE` as body prose. The date group keeps `[0-9]`, and the captured
# date is validated against `date.fromisoformat` in
# `unknown_category_headings` — a heading whose date is unreal is a date
# defect, not a category one, and belongs to the reader who owns that lane.
NEAR_MISS_RE = re.compile(
    r"^## \[(?P<date>[0-9]{4}-[0-9]{2}-[0-9]{2}) [0-9]{2}:[0-9]{2}\] "
    r"(?P<category>\S+) \| \S"
)

# The em-dash placeholder `log-work` step 6 already uses for an empty cell.
_DASH = "—"


def journal_entries(text: str, month: str) -> list[tuple[str, str]]:
    """Every entry heading `text` opens for `month`, as `(date, category)`.

    Fence-aware: a heading quoted inside a fenced block is content and opens
    no entry — including one behind an opener the file never closes, which
    runs the fence to end of file per `md_fences`'s own CommonMark-conforming
    contract. Bounded by `month`: a heading dated outside the month `month`
    names is read but contributes no entry. A heading whose date is not a
    real calendar date, per `date.fromisoformat`, is not an entry heading at
    all and is left as body prose — it never reaches the month bound.
    """
    out: list[tuple[str, str]] = []
    fence: tuple[str, int] | None = None
    for line in split_lines(text):
        marker = fence_marker(line)
        if fence is not None:
            if closes_fence(marker, fence):
                fence = None
            continue
        if marker is not None:
            fence = (marker[0], marker[1])
            continue
        m = ENTRY_RE.match(line)
        if not m:
            continue
        raw_date = m.group("date")
        try:
            date.fromisoformat(raw_date)
        except ValueError:
            continue
        if not raw_date.startswith(month + "-"):
            continue
        out.append((raw_date, m.group("category")))
    return out


def unknown_category_headings(text: str) -> list[tuple[int, str]]:
    """Every near-miss heading in `text`, as `(1-based line, category token)`.

    A near miss is a heading that would open an entry but for the token in
    its category slot. `journal_entries` reads such a heading as body prose,
    which is correct — it opens no entry — and silent, which is not: a
    category added to the tree schema and not to `CATEGORIES` shrinks a
    month's count with every gate still green. This function is what a caller
    refuses on.

    Fence-aware on the same terms as `journal_entries`, so a heading quoted
    inside a fenced block is content and is reported by neither. That
    includes a block behind an opener the file never closes, which runs to
    end of file — the caller diagnoses that file by its unclosed fence
    instead.

    Bounded to near misses on purpose. The date must be ASCII and must
    round-trip `date.fromisoformat`, so a forged or impossible date is
    diagnosed as a date rather than mislabelled a category. No month bound
    applies: an unclassifiable category is a defect in the file wherever the
    heading is dated.
    """
    out: list[tuple[int, str]] = []
    fence: tuple[str, int] | None = None
    for lineno, line in enumerate(split_lines(text), start=1):
        marker = fence_marker(line)
        if fence is not None:
            if closes_fence(marker, fence):
                fence = None
            continue
        if marker is not None:
            fence = (marker[0], marker[1])
            continue
        m = NEAR_MISS_RE.match(line)
        if not m:
            continue
        category = m.group("category")
        if category in CATEGORIES:
            continue
        try:
            date.fromisoformat(m.group("date"))
        except ValueError:
            continue
        out.append((lineno, category))
    return out


def find_unclosed_fence(text: str) -> int | None:
    """The 1-based line number of an opener `text` never closes, or `None`.

    A month file ending inside an open fence makes every entry heading below
    the opener unreadable as an entry — the driver refuses such a file rather
    than silently under-counting it (the refuse-and-preserve lane).
    """
    fence: tuple[str, int] | None = None
    opened_at: int | None = None
    for lineno, line in enumerate(split_lines(text), start=1):
        marker = fence_marker(line)
        if fence is not None:
            if closes_fence(marker, fence):
                fence = None
                opened_at = None
            continue
        if marker is not None:
            fence = (marker[0], marker[1])
            opened_at = lineno
    return opened_at


def derive_row(month: str, text: str) -> dict:
    """The journal-index row for `month`, derived from the month file alone.

    An all-empty month (no admissible entries) yields `entries: 0` and the
    em-dash placeholder in `first`, `last`, and `categories` — decision (iii)
    of the shape this module implements.
    """
    entries = journal_entries(text, month)
    dates = sorted(d for d, _ in entries)
    counts = Counter(c for _, c in entries)
    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
    return {
        "month": month,
        "first": dates[0] if dates else _DASH,
        "last": dates[-1] if dates else _DASH,
        "entries": len(entries),
        "categories": ", ".join(name for name, _ in top) if top else _DASH,
    }


def _valid_month_cell(value: str) -> bool:
    return bool(MONTH_RE.fullmatch(value))


def _valid_date_or_dash(value: str) -> bool:
    if value == _DASH:
        return True
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _valid_categories_cell(value: str) -> bool:
    if value == _DASH:
        return True
    parts = value.split(", ")
    return bool(parts) and all(p in CATEGORIES for p in parts) and len(set(parts)) == len(parts)


def _assert_row_alphabet(row: dict) -> None:
    """The closed-alphabet cell rule, asserted before a row is rendered.

    `redact` is NOT the defence here — a pipe is printable and passes through
    it unchanged. Every cell is checked against its own closed grammar before
    it reaches a Markdown table row; a failure here is a fail-closed internal
    error (a bug in the derivation), never a rendered cell.
    """
    if not _valid_month_cell(row["month"]):
        raise ValueError(f"internal error: month cell {row['month']!r} fails its grammar")
    if not isinstance(row["entries"], int) or row["entries"] < 0:
        raise ValueError(f"internal error: entries cell {row['entries']!r} is not a non-negative integer")
    for key in ("first", "last"):
        if not _valid_date_or_dash(row[key]):
            raise ValueError(f"internal error: {key} cell {row[key]!r} is neither an ISO date nor {_DASH!r}")
    if not _valid_categories_cell(row["categories"]):
        raise ValueError(f"internal error: categories cell {row['categories']!r} fails its grammar")


def render_index(rows: list[dict], last_updated: str) -> str:
    """The whole `journal/index.md` body, byte-for-byte.

    `last_updated` is `max(last entry)` over rows carrying a real date,
    skipping the em-dash placeholder, or the em dash itself when no row
    carries one — computed by the caller, since only the caller knows the
    full row set the file-level "as of" line describes. Every cell is
    asserted against its closed alphabet before it is emitted.
    """
    if not _valid_date_or_dash(last_updated):
        raise ValueError(f"internal error: last_updated {last_updated!r} is neither an ISO date nor {_DASH!r}")
    for row in rows:
        _assert_row_alphabet(row)
    body = "".join(
        f"| {r['month']} | {r['first']} | {r['last']} | {r['entries']} "
        f"| {r['categories']} |\n"
        for r in rows
    )
    return (
        f"# Journal index\n\n_Last updated: {last_updated}_\n\n"
        "| month | first entry | last entry | entries | top categories |\n"
        "|-------|-------------|------------|---------|----------------|\n"
        + body
    )


def parse_index_row(text: str, month: str) -> dict | None:
    """The row for `month` in a rendered index, or `None` if there is none.

    `entries` is returned as `int(cells[3])` when that cell parses as one,
    and as the raw string otherwise — this function never raises on a
    hand-maintained `—` in the entries cell. A caller that needs an
    integer checks `isinstance(row["entries"], int)` and reports a
    `validation_errors` entry itself when it is not.
    """
    for line in split_lines(text):
        if not line.startswith("| " + month + " |"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 5:
            return None
        entries_cell = cells[3]
        try:
            entries: object = int(entries_cell)
        except ValueError:
            entries = entries_cell
        return {
            "month": cells[0], "first": cells[1], "last": cells[2],
            "entries": entries, "categories": cells[4],
        }
    return None
