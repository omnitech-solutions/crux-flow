"""review_findings.py — the decision-review finding grammar, in ONE place.

WHAT IS IN HERE. The parse of one decision-review report into three structures
— definitions, lifecycle records, and lifecycle notes — plus the three
questions a reader asks across dates: does this finding still stand, how many
of a report's own findings sit in each standing, and how many of a report's
records name a finding some other report defined. The reviews-index
regenerator is the consumer; the reader that writes a report is the other.

WHAT IS DELIBERATELY NOT IN HERE.

  * I/O OF ANY KIND, AFTER IMPORT. No `open`, no `Path.exists`, no `os.stat`
    in any function here. (Import time is the one exception, and it is not an
    exception to the property: the `sys.path` insert and the two sibling
    imports below run once, read no report, and touch no hand-edited file.)
    The module takes text and returns structures. That is a supply-chain property (the
    parser of a hand-editable report reaches for nothing) and a testability
    property (every lane is drivable from a string). The one question that
    needs the filesystem — does a `resolved` record's locator name a surface
    that exists — is asked through an INJECTED probe, `locator_exists`, which
    the caller supplies already bounded to the repository root.
  * ANY GRADE. `validate_structure` checks the SHAPE of a report. Whether a
    finding is right, whether a resolution really enacted it, whether a
    dispute is fair — none of that is a question this module has an opinion
    about, and the decision behind it says so explicitly.

REFUSALS ARE DOCUMENT-LANE REFUSALS. One exception type, `ReviewFindingsError`,
carrying a `problem` string. The caller turns it into exit 1 with a
`validation_errors` entry, the shape `generate-reviews-index.py` already
emits. Nothing here raises `OSError`: a hand-edited report is a fact about the
corpus a human repairs, never an environment failure. And nothing here
truncates a value into admission — a locator outside the grammar is refused,
never trimmed until it fits.

EVERY QUOTED-BACK VALUE IS UNTRUSTED. A report is hand-editable content, and
every cell this module refuses reaches a JSON envelope and stderr through the
caller. All of them render through `untrusted.redact`, which bounds the
rendered length and replaces every unprintable character. A record lives in a
table cell and no fence stands inside a table cell, so the fencing half of the
usual mined-value rule is unavailable here and the bounding half carries the
whole load.

THE TWO GRAMMARS, AND THE KEY THAT TELLS THEM APART. `report_grammar` is the
discriminator, and `lifecycle` is its one recognised value.

  * ABSENT. The report reads under the FROZEN LEGACY COUNTING RULE: `raised`
    is the number of distinct `adr-review-<slug>` tokens below the
    frontmatter, exactly as `generate-reviews-index.py` counted before this
    module existed, so the two historical reports keep rendering 6 and 5.
    `LEGACY_TOKEN` is that pattern, spelled ONCE, here. The regenerator no
    longer carries a copy: it imports this module, and
    `test_the_legacy_token_regex_has_exactly_one_copy` reads the regenerator's
    source and fails on a second spelling.
  * ABSENT ON A REPORT DATED AFTER 2026-09-08. Refused. The boundary is a day
    later than the six-section cohort's, because the newer of the two
    historical reports is dated 2026-09-08 and both must read as legacy.
  * PRESENT WITH ANY OTHER VALUE. Refused.

Neither absence nor an unrecognised value is ever defaulted in either
direction, and historical Coverage prose is never read as a confirmed
lifecycle event: a legacy report yields zero records, full stop.

DEFINITION VERSUS REFERENCE. A finding DEFINITION is a `### `-level heading
under one of the four finding sections whose heading text carries exactly one
id. Zero ids, or two or more, is refused. A heading deeper than `### ` inside
an entry is neither a definition nor a refusal. Every other occurrence of an
id anywhere — the summary table, a Coverage line, a fenced block, a lifecycle
record, a note — is a REFERENCE and defines nothing. Under THIS grammar,
content inside a fenced block never defines anything and never records
anything; fence state comes from `md_fences`, never from a local scanner.

The legacy path is deliberately NOT fence-aware, and saying so here is the
point: its count is FROZEN, so it must reproduce what
`generate-reviews-index.py` counted before this module existed — a bare
`findall` over the whole body, fences included. A fenced id on a legacy report
is therefore counted, and `_definitions_across` claims it as that report's
definition. Making the legacy path fence-aware would move a historical
report's number, which is the one thing the freeze exists to prevent.

Stdlib only, and imported through the `__file__`-derived `sys.path` insert the
scripts beside it already use — the one filesystem-adjacent act in this file,
and it happens at import, before any report is read.
"""

from __future__ import annotations

import datetime
import re
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Callable, Iterable, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

from md_fences import closes_fence, fence_marker, split_lines  # noqa: E402
from untrusted import redact  # noqa: E402

#: The one recognised value of the `report_grammar` discriminator.
LIFECYCLE = "lifecycle"

#: The last date on which a report may carry no discriminator. A report dated
#: after this and carrying none is refused.
LEGACY_BOUNDARY = "2026-09-08"

#: The FROZEN legacy counting pattern, and the ONLY spelling of it in the
#: tree. `generate-reviews-index.py` used to define a second copy of it; a
#: comparison across two copies can find a divergence but never a shared
#: error, so the copy was deleted and the regenerator imports this module
#: instead. `test_the_legacy_token_regex_has_exactly_one_copy` reads the
#: regenerator's source and fails on a redefinition, on a missing import, or
#: on this pattern appearing there at all — the pattern cannot drift if it
#: exists once. That test names the deleted constant; this module does not,
#: so a reader here is never sent looking for a name that resolves to nothing.
LEGACY_TOKEN = re.compile(r"\badr-review-[a-z0-9]+(?:-[a-z0-9]+)*\b")

#: A finding id in FULL FORM. A bare slug is not one.
FINDING_ID = re.compile(r"\Aadr-review-[a-z0-9]+(?:-[a-z0-9]+)*\Z")

#: A lifecycle note, recognised on a line inside a finding entry. The note's
#: finding is the id of the `### ` definition heading whose entry it sits in.
#:
#: [SECURITY:S1] The ordinal is the same BOUNDED ASCII digit run `_POSITIVE_INT`
#: admits, and for the same two reasons. `\d` matches every Unicode decimal
#: digit in a `str` pattern, so `pass \u0662` parsed and reached `int()`; and an
#: unbounded run put its whole length into a rendered refusal. A line outside
#: this grammar is not a note at all — it is prose, and the parser never reads
#: prose as an event.
NOTE_MARKER = re.compile(
    r"^[ \t]*-[ \t]+\*\*(re-verified|resolved|disputed), "
    r"pass ([1-9][0-9]{0,3})\*\*")

#: The note marker's SHAPE, with the ordinal left free. A line that carries the
#: shape but misses `NOTE_MARKER` is a note whose ordinal is out of grammar —
#: `pass 10000`, `pass 0`, `pass \u0662` — and it is REFUSED naming the line
#: rather than degrading to prose. Degrading was the silent failure: a pass that
#: believed it wrote a note wrote nothing a reader or the pairing check sees.
_NOTE_MARKER_SHAPE = re.compile(
    r"^[ \t]*-[ \t]+\*\*(?:re-verified|resolved|disputed), "
    r"pass ([^*]{0,40})\*\*")

#: The evidence locator: a reference, never a quotation. Pipe, newline and
#: carriage return are excluded because the value lives in a table cell;
#: backtick because no fence stands inside one; `[` and `^` because they are
#: the first characters of a wiki-link and of a footnote marker. [SECURITY:S1]
#: The C0 and C1 control ranges are excluded whole, so a value that reaches
#: stderr cannot carry an escape sequence or forge a line; `\r` and `\n` stay
#: named above for the reader, though the ranges already cover them.
#:
#: A locator names a SURFACE and carries no `:line` suffix. It is not the
#: `path:line-range` grammar `observation_evidence.EVIDENCE_RE` declares for an
#: observation's evidence, and the two must not be confused: this one answers
#: "does that surface exist", never "what does line 40 say".
#:
#: 200 is longer than the longest repo-relative path this repository ships, so
#: a real locator is never refused for its length. The RENDERING bound is a
#: different number and a smaller one: every refusal below renders the value
#: through `redact`, whose default `untrusted.LIMIT` is 120 characters, so a
#: refused locator longer than that renders truncated and carries the
#: truncation note. That is the channel bound working, not a defect — the
#: grammar refuses an out-of-grammar locator rather than trimming it into
#: admission, and the refusal names as much of it as the channel allows.
LOCATOR_LIMIT = 200
LOCATOR = re.compile(r"^[^|\r\n`\[\^\x00-\x1f\x7f-\x9f]{1,%d}$" % LOCATOR_LIMIT)

#: The written event kinds. `raised` is implicit — the definition itself — and
#: is never written as a record.
RECORD_KINDS = ("re-verified", "resolved", "disputed")

#: The note kinds that must pair with a record of the same finding, kind and
#: pass. A `resolved` note is recognised and is not bound: the decision names
#: the disputed note and the re-verification note, and this module implements
#: what it names rather than a stricter reading of its own.
PAIRED_NOTE_KINDS = ("re-verified", "disputed")

#: The four standings, in the order a renderer shows them.
STANDINGS = ("open", "resolved", "disputed", "unknown")

#: The four finding sections, lowercased for comparison.
FINDING_SECTIONS = ("propose", "amend", "repair", "revoke")

#: The header row that identifies the summary table, and the one that
#: identifies the lifecycle record table. Each table is found BY ITS HEADER
#: ROW and never by position — the per-goal coverage matrix sits under the
#: same `## Coverage` heading and the two must never merge.
SUMMARY_HEADER = ("finding id", "section", "objective", "proposed act", "size")
#: WIDENING `RECORD_HEADER` IS A SILENT-DATA-LOSS CHANGE. Every existing
#: report's record table stops matching, its rows fall through to the
#: unowned-row lane, and the five-cell trap below no longer fires because the
#: cell count moved — so every lifecycle record is dropped with NO refusal and
#: the index computes every finding open. A new column belongs in a new table.
RECORD_HEADER = ("source report date", "finding id", "pass", "event", "locator")

#: The header row that identifies the assessment table (ADR-0108 rule
#: `assessment-row-is-the-measure-part`). Seven columns is a FORMAT CONTRACT
#: the decision fixes, not a layout preference.
#:
#: Be precise about what the arity does and does not do. Identification is by
#: header TEXT — `_header_matches` compares the tuple — so arity plays no part
#: in telling one table from another. Arity is load-bearing in ONE place: the
#: orphan-row trap below keys on `len(ASSESSMENT_HEADER)`, and its five-cell
#: sibling keys on `len(RECORD_HEADER)`. Equal lengths there would need the
#: first-cell shape check to carry the whole separation alone. Change this
#: tuple's length and that trap is what breaks.
ASSESSMENT_HEADER = ("objective", "measure", "evidence", "locator", "domains",
                     "conclusion", "findings")

#: The per-goal matrix that survives beside the assessment table. Four
#: columns: it keeps the two `rule:coverage-matrix` requires and gains the
#: derived alignment rollup. Read back only so the rollup can be checked
#: against the parts it rolls up.
MATRIX_HEADER = ("objective", "alignment rollup", "signals that measured it",
                 "domains that measured it")

#: The evidence-availability vocabulary (ADR-0108 rule
#: `evidence-availability-and-alignment-are-separate`).
EVIDENCE_AVAILABILITY = ("resolved", "partial", "unavailable", "not-attempted")

#: The alignment-conclusion vocabulary. Kept deliberately apart from
#: `EVIDENCE_AVAILABILITY`: one grades whether evidence exists, the other
#: grades what it shows, and neither value set stands in for the other.
ALIGNMENT_CONCLUSION = ("serves", "gap", "inconclusive", "not-assessed")

#: The six legal `(evidence, conclusion)` pairs out of the sixteen possible.
#: Published in full rather than left for a checker to infer.
LEGAL_PAIRS = frozenset({
    ("resolved", "serves"), ("resolved", "gap"),
    ("partial", "gap"), ("partial", "inconclusive"),
    ("unavailable", "inconclusive"),
    ("not-attempted", "not-assessed"),
})

#: Outcome strength, for the append-only merge on one date: a later pass's
#: weaker outcome never buries an earlier pass's stronger one.
_OUTCOME_RANK = {"not-assessed": 1, "attempted": 2, "measured": 3}

#: Every `measured_objectives` entry field this reader NAMES. It is the
#: reader's declared vocabulary and the shipped template's field list is
#: checked against it, so a template that grows a field the reader never
#: reads — or spells one differently, as `measure_version` once did — fails a
#: test rather than hard-refusing the first report written from it. The
#: reader does not refuse an entry carrying a field outside this set: a
#: report may carry its own annotations, and the historical reports predate
#: several of these.
ENTRY_FIELDS = frozenset({
    "objective", "part", "outcome", "measure_digest", "pass",
    "evidence", "blocker",
    "escalation_strikes", "escalation_owed", "escalation_owed_since",
    "escalation_spent_on", "escalation_state",
})

#: The three pairs in which evidence SETTLED something — the pairs that make
#: a goal's measurement outcome `measured` rather than merely `attempted`.
DISCRIMINATING_PAIRS = frozenset({
    ("resolved", "serves"), ("resolved", "gap"), ("partial", "gap"),
})

#: A bare `OBJ-N` id — the objective column of an assessment row, and the
#: orphan-row trap's own first-cell check below. A flat legacy
#: `measured_objectives` list uses the separate `_FLAT_OBJECTIVE_ID`.
#:
#: [SECURITY:S1] `{0,3}`, never `*`: an unbounded digit run reaches `int()` in
#: `validate_structure`'s numeric-ordering check, and CPython refuses an
#: integer conversion past 4300 digits with `ValueError`. The reviews-index
#: regenerator catches `ReviewFindingsError` alone and documents that this
#: module raises nothing else, so an unbounded id turns a malformed report
#: into a traceback with unparseable stdout — the crash lane — instead of a
#: clean exit 1. Four digits is the same bound `_POSITIVE_INT` already carries
#: for the pass ordinal, and no objectives file numbers past 9999.
OBJECTIVE_ID = re.compile(r"\AOBJ-[1-9][0-9]{0,3}\Z")

#: The measure cell: `OBJ-<n>.<k> — <label>`, the part id and a non-empty
#: label in the architect's own words. `.+` cannot match an empty label; the
#: row builder additionally strips and re-checks so a whitespace-only label
#: refuses too.
MEASURE_PART = re.compile(
    r"\AOBJ-([1-9][0-9]{0,3})\.([1-9][0-9]{0,3}) — (.+)\Z")

#: A measure PART id on its own (`OBJ-<n>.<k>`, no label) — the shape a
#: `measured_objectives` entry's optional `part` field carries (
#: rule:a-blocked-part-is-counted-in-its-own-right). This is the COUNTED-THING
#: key for a part-level entry; a part-less entry's counted-thing key is its
#: bare `OBJ-N` objective id. The two key shapes never collide — a part id
#: always carries a `.` and an objective id never does — so one dict may hold
#: both kinds of key at once.
PART_ID = re.compile(r"\AOBJ-([1-9][0-9]{0,3})\.([1-9][0-9]{0,3})\Z")

_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)[ \t]*$")
_DELIMITER_CELL = re.compile(r"^:?-{3,}:?$")
#: [SECURITY:S1] `[0-9]`, never `\d`: `\d` matches every Unicode decimal digit
#: in a `str` pattern, so a date spelled in Arabic-Indic digits matched a
#: pattern whose whole job is to say "this is an ISO calendar date".
_ISO_DATE = re.compile(r"\A[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
#: A pass ordinal: ASCII digits only, no leading zero, and a BOUNDED run.
#: [SECURITY:S1] `[0-9]*` is unbounded, so a 4000-digit cell parsed, reached
#: `int()`, and rendered 4000 characters into a refusal that `redact` never saw
#: because the value was no longer a string. Four digits is far past any real
#: pass ordinal, and the refusal below still renders the raw cell through
#: `redact` rather than the parsed int.
_POSITIVE_INT = re.compile(r"\A[1-9][0-9]{0,3}\Z")

#: How many ids a set-difference refusal names before it stops counting.
#: [SECURITY:S1] A hand-editable summary table carries as many rows as its
#: author typed; joining the whole difference put the entire table into one
#: error string, on stdout and on stderr both.
_ID_LIST_LIMIT = 10


def _join_ids(ids: Sequence[str]) -> str:
    """`ids`, redacted and joined, capped at `_ID_LIST_LIMIT` plus a count."""
    shown = ", ".join(redact(fid) for fid in ids[:_ID_LIST_LIMIT])
    if len(ids) > _ID_LIST_LIMIT:
        shown += f", and {len(ids) - _ID_LIST_LIMIT} more"
    return shown


class ReviewFindingsError(Exception):
    """A report this module refuses to read — a document-lane validation error.

    Carries the problem separately from the message so a caller can build a
    `{"file", "error"}` entry without reparsing a string. Every untrusted part
    of `problem` is redacted at the raise site, where the surrounding prose
    says which value is the report's.
    """

    def __init__(self, problem: str) -> None:
        super().__init__(problem)
        self.problem = problem


@dataclass(frozen=True)
class Definition:
    """One finding, defined by a `### ` heading under a finding section."""

    finding_id: str
    section: str
    date: str
    line: int


@dataclass(frozen=True)
class Record:
    """One lifecycle record: five fields, plus where it was written.

    `source_date` is the date of the report that DEFINED the finding, and it
    is the only date a record carries of its own. `report_date` is the
    containing report's date — the record's own position in the stream.
    """

    source_date: str
    finding_id: str
    pass_ordinal: int
    event: str
    locator: str
    report_date: str
    line: int


@dataclass(frozen=True)
class Note:
    """One lifecycle note, and the entry it sits in."""

    finding_id: str
    kind: str
    pass_ordinal: int
    line: int


@dataclass(frozen=True)
class Assessment:
    """One assessment row: one measure PART, not one goal (ADR-0108).

    `objective` is the bare `OBJ-N` id; `part` is the `OBJ-N.k` id the
    `measure` cell carries; `label` is the architect's own words for that
    part. `domains` and `findings` are parsed lists — `findings` is a
    REFERENCE list, exactly like every other id occurrence outside a
    heading: it defines nothing and is never added to `definitions` or to
    `summary_row_ids`.
    """

    objective: str
    part: str
    label: str
    evidence: str
    locator: str
    domains: tuple[str, ...]
    conclusion: str
    findings: tuple[str, ...]
    line: int


@dataclass(frozen=True)
class ParsedReport:
    """One report, read under whichever grammar its discriminator selects.

    On a legacy report `definitions`, `records`, `notes` and `summary_row_ids`
    are all empty, `legacy_ids` holds the frozen token set, and `raised` is its
    size. On a lifecycle report `legacy_ids` is empty and `raised` is the
    number of definitions.
    """

    date: str
    grammar: str | None
    is_legacy: bool
    definitions: tuple[Definition, ...]
    records: tuple[Record, ...]
    notes: tuple[Note, ...]
    summary_row_ids: tuple[str, ...]
    legacy_ids: frozenset[str]
    raised: int
    assessments: tuple[Assessment, ...]
    #: `OBJ-N` -> the alignment rollup the per-goal matrix RENDERS. Checked
    #: against the value its parts derive; never used in place of it.
    matrix_rollups: dict[str, str]
    #: `OBJ-N` -> the `measured_objectives` entry the frontmatter records.
    #: Checked against the outcome its parts derive.
    frontmatter_outcomes: dict[str, dict[str, str]]


# ── reading one report ────────────────────────────────────────────────────


def _calendar_date(value: str, subject: str) -> str:
    """`value`, refused unless it is an ISO calendar date that exists."""
    if not _ISO_DATE.match(value or ""):
        raise ReviewFindingsError(
            f"{subject} is {redact(value)}, which is not an ISO calendar date "
            "of the form YYYY-MM-DD")
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        raise ReviewFindingsError(
            f"{subject} is {redact(value)}, which is not a calendar date that "
            "exists") from None
    return value


def _frontmatter_scalar(frontmatter: str, key: str) -> str | None:
    match = re.search(rf"^{re.escape(key)}[ \t]*:[ \t]*(.*)$", frontmatter or "",
                      re.MULTILINE)
    if not match:
        return None
    return match.group(1).strip().strip("\"'").strip()


def _cells(line: str) -> list[str] | None:
    """The stripped cells of a Markdown table row, or None for a non-row."""
    stripped = line.strip()
    if not stripped.startswith("|"):
        return None
    inner = stripped[1:]
    if inner.endswith("|"):
        inner = inner[:-1]
    return [cell.strip() for cell in inner.split("|")]


def _is_delimiter(cells: Sequence[str]) -> bool:
    return bool(cells) and all(_DELIMITER_CELL.match(cell) for cell in cells)


def _header_matches(cells: Sequence[str], header: Sequence[str]) -> bool:
    return tuple(cell.strip().lower() for cell in cells) == tuple(header)


def _content_lines(body: str, line_offset: int):
    """Every line of `body` that stands OUTSIDE a fenced block, with its number.

    Fence state comes from `md_fences`, so an opener's info string, the indent
    bound, the two fence characters and the closer's run length are all decided
    in one place. A marker line — opener or closer — is itself content the
    grammar never reads, so neither is yielded.
    """
    opener: tuple[str, int] | None = None
    for index, line in enumerate(split_lines(body), start=1):
        marker = fence_marker(line)
        if opener is not None:
            if closes_fence(marker, opener):
                opener = None
            continue
        if marker is not None:
            opener = (marker[0], marker[1])
            continue
        yield index + line_offset, line


def _refuse_escaping_shape(locator: str, line: int,
                           subject: str = "lifecycle record") -> None:
    """Refuse the three locator shapes that reach outside the repository root.

    Outcome 2 refuses an absolute path, a `~`-prefixed path and a `..`
    traversal without qualifying the event kind, and the skill and the template
    both promise it. Those refusals used to live only in the injected existence
    probe, which is asked about a `resolved` record alone — so a `re-verified`
    record naming `/etc/passwd` was admitted and the promise was false for two
    of the three kinds. The check moves here, where every record passes.

    A PURE STRING check: `Path(...).parts` parses, it does not stat. This
    module still touches the filesystem nowhere after import.
    """
    if locator.startswith("~") or PurePosixPath(locator).is_absolute() \
            or PureWindowsPath(locator).is_absolute():
        raise ReviewFindingsError(
            f"the locator of the {subject} at line {line} is "
            f"{redact(locator)}, which is an absolute or home-relative path; a "
            "locator resolves only inside the repository root, and such a path "
            "is refused rather than followed")
    # BOTH flavours, as the absolute check above already does: `PurePosixPath`
    # treats `\` as an ordinary character, so `a\..\b` is one component to it
    # and its `..` went unseen — the same value is three components on Windows,
    # where it traverses.
    if ".." in PurePosixPath(locator).parts \
            or ".." in PureWindowsPath(locator).parts:
        raise ReviewFindingsError(
            f"the locator of the {subject} at line {line} is "
            f"{redact(locator)}, which traverses out of the repository root "
            "with `..`; it is refused rather than followed")


def _record_from_row(cells: Sequence[str], report_date: str,
                     line: int) -> Record:
    if len(cells) != len(RECORD_HEADER):
        raise ReviewFindingsError(
            f"the lifecycle record at line {line} carries {len(cells)} cells; "
            f"a record row carries {len(RECORD_HEADER)} cells — "
            + ", ".join(RECORD_HEADER))
    raw_date, raw_id, raw_pass, raw_event, raw_locator = cells

    source_date = _calendar_date(
        raw_date, f"the source report date of the lifecycle record at line {line}")

    # ONE BACKTICK RULE. Backticks are Markdown formatting on a closed-
    # vocabulary cell, and a reader who backticks the id cell backticks the two
    # beside it; they are stripped before the vocabulary check. The LOCATOR
    # cell is the deliberate exception and is NOT stripped — a backtick run
    # terminates a fence, so the grammar refuses the character outright in the
    # one free field.
    finding_id = raw_id.strip().strip("`").strip()
    raw_pass = raw_pass.strip().strip("`").strip()
    raw_event = raw_event.strip().strip("`").strip()

    if not FINDING_ID.match(finding_id):
        raise ReviewFindingsError(
            f"the lifecycle record at line {line} names {redact(finding_id)}, "
            "which is not a finding id in full form (`adr-review-<slug>`)")

    if not _POSITIVE_INT.match(raw_pass):
        raise ReviewFindingsError(
            f"the pass of the lifecycle record at line {line} is "
            f"{redact(raw_pass)}, which is not a pass ordinal: 1 to 4 ASCII "
            "digits with no leading zero")

    if raw_event == "raised":
        raise ReviewFindingsError(
            f"the lifecycle record at line {line} carries the event "
            f"{redact(raw_event)}; `raised` is the definition itself: it is "
            "dated by the report that carries the definition, so a record "
            "claiming it would date the definition a second time, and it is "
            "never written as a record")
    if raw_event not in RECORD_KINDS:
        raise ReviewFindingsError(
            f"the lifecycle record at line {line} carries the event "
            f"{redact(raw_event)}, which is outside the closed set "
            + " | ".join(RECORD_KINDS))

    if not LOCATOR.match(raw_locator):
        raise ReviewFindingsError(
            f"the locator of the lifecycle record at line {line} is "
            f"{redact(raw_locator)}, which is outside the locator grammar: 1 to "
            f"{LOCATOR_LIMIT} characters carrying no pipe, line break, "
            "backtick, control character, `[` or `^`")
    _refuse_escaping_shape(raw_locator, line)

    return Record(source_date=source_date, finding_id=finding_id,
                  pass_ordinal=int(raw_pass), event=raw_event,
                  locator=raw_locator, report_date=report_date, line=line)


def _split_csv(cell: str) -> tuple[str, ...]:
    """A comma-separated cell, or `()` for an empty or dash-only cell."""
    stripped = cell.strip()
    if not stripped or stripped in ("—", "-"):
        return ()
    return tuple(token.strip() for token in stripped.split(",")
                 if token.strip())


def _assessment_from_row(cells: Sequence[str], line: int) -> Assessment:
    if len(cells) != len(ASSESSMENT_HEADER):
        raise ReviewFindingsError(
            f"the assessment row at line {line} carries {len(cells)} cells; "
            f"an assessment row carries {len(ASSESSMENT_HEADER)} cells — "
            + ", ".join(ASSESSMENT_HEADER))
    (raw_objective, raw_measure, raw_evidence, raw_locator, raw_domains,
     raw_conclusion, raw_findings) = cells

    # ONE BACKTICK RULE, on the same terms `_record_from_row` already applies:
    # the objective, evidence and conclusion cells are closed-vocabulary cells
    # a reader may backtick, and are stripped before the vocabulary check. The
    # LOCATOR cell is the deliberate exception and is never stripped.
    objective = raw_objective.strip().strip("`").strip()
    if not OBJECTIVE_ID.match(objective):
        raise ReviewFindingsError(
            f"the assessment row at line {line} names the objective "
            f"{redact(objective)}, which is not an `OBJ-N` id")

    measure_match = MEASURE_PART.match(raw_measure.strip())
    if not measure_match:
        raise ReviewFindingsError(
            f"the assessment row at line {line} carries the measure "
            f"{redact(raw_measure)}, which does not match `OBJ-<n>.<k> — "
            "<label>` with a non-empty label")
    part = f"OBJ-{measure_match.group(1)}.{measure_match.group(2)}"
    if f"OBJ-{measure_match.group(1)}" != objective:
        # Both derived values are computed PER GOAL, so a part filed under the
        # wrong objective silently folds into the wrong rollup while the
        # rendered row still reads the other goal's id.
        raise ReviewFindingsError(
            f"the assessment row at line {line} names the objective "
            f"{redact(objective)} and the measure part {redact(part)}, whose "
            "goal numbers disagree; a part belongs to the goal its row names")
    label = measure_match.group(3).strip()
    if not label:
        raise ReviewFindingsError(
            f"the assessment row at line {line} carries an empty label after "
            f"its measure part {redact(part)}")

    evidence = raw_evidence.strip().strip("`").strip()
    if evidence not in EVIDENCE_AVAILABILITY:
        raise ReviewFindingsError(
            f"the assessment row at line {line} carries the evidence "
            f"availability {redact(evidence)}, which is outside the closed "
            "set " + " | ".join(EVIDENCE_AVAILABILITY))

    conclusion = raw_conclusion.strip().strip("`").strip()
    if conclusion not in ALIGNMENT_CONCLUSION:
        raise ReviewFindingsError(
            f"the assessment row at line {line} carries the alignment "
            f"conclusion {redact(conclusion)}, which is outside the closed "
            "set " + " | ".join(ALIGNMENT_CONCLUSION))

    if (evidence, conclusion) not in LEGAL_PAIRS:
        raise ReviewFindingsError(
            f"the assessment row at line {line} pairs the evidence "
            f"availability {redact(evidence)} with the alignment conclusion "
            f"{redact(conclusion)}, which is outside the six legal pairs")

    if not LOCATOR.match(raw_locator):
        raise ReviewFindingsError(
            f"the locator of the assessment row at line {line} is "
            f"{redact(raw_locator)}, which is outside the locator grammar: 1 "
            f"to {LOCATOR_LIMIT} characters carrying no pipe, line break, "
            "backtick, control character, `[` or `^`")
    _refuse_escaping_shape(raw_locator, line, "assessment row")

    domains = _split_csv(raw_domains)

    findings = tuple(fid.strip().strip("`").strip()
                     for fid in raw_findings.split(",") if fid.strip()) \
        if raw_findings.strip() not in ("", "—", "-") else ()
    for finding_id in findings:
        if not FINDING_ID.match(finding_id):
            raise ReviewFindingsError(
                f"the assessment row at line {line} names the finding "
                f"{redact(finding_id)} in its findings cell, which is not a "
                "finding id in full form (`adr-review-<slug>`)")

    return Assessment(objective=objective, part=part, label=label,
                      evidence=evidence, locator=raw_locator, domains=domains,
                      conclusion=conclusion, findings=findings, line=line)


def parse_report(*, date: str, frontmatter: str, body: str,
                 line_offset: int = 0) -> ParsedReport:
    """One report, read under the grammar its discriminator selects.

    `frontmatter` is the raw text between the `---` delimiters and `body` is
    everything below them. `line_offset` is added to every reported line
    number, so a caller that read a file hands back file-relative lines by
    passing the frontmatter's own line count; the default reports lines
    relative to the body.
    """
    date = _calendar_date(date, "the report date")
    grammar = _frontmatter_scalar(frontmatter, "report_grammar")

    if grammar is None or grammar == "":
        if date > LEGACY_BOUNDARY:
            raise ReviewFindingsError(
                f"the {date} report carries no `report_grammar` key; a report "
                f"dated after {LEGACY_BOUNDARY} declares its grammar, and the "
                "absent key is never defaulted")
        ids = frozenset(LEGACY_TOKEN.findall(body))
        return ParsedReport(date=date, grammar=None, is_legacy=True,
                            definitions=(), records=(), notes=(),
                            summary_row_ids=(), legacy_ids=ids,
                            raised=len(ids), assessments=(),
                            matrix_rollups={}, frontmatter_outcomes={})

    if grammar != LIFECYCLE:
        raise ReviewFindingsError(
            f"the {date} report declares `report_grammar` {redact(grammar)}, "
            f"which is outside the recognised set ({LIFECYCLE}); an "
            "unrecognised value is never defaulted")

    definitions: list[Definition] = []
    defined_at: dict[str, int] = {}
    records: list[Record] = []
    notes: list[Note] = []
    summary_row_ids: list[str] = []
    assessments: list[Assessment] = []

    section = ""
    current: Definition | None = None
    seen_definition_section = False
    summary_table_seen = False
    record_table_seen = False
    assessment_table_seen = False
    matrix_rollups: dict[str, str] = {}
    matrix_table_seen = False
    reading: str | None = None   # "summary" | "record" | "assessment" | None

    for line, text in _content_lines(body, line_offset):
        heading = _HEADING.match(text)
        if heading:
            reading = None
            level, title = len(heading.group(1)), heading.group(2)
            if level <= 2:
                section = title.strip().lower()
                current = None
                if section in FINDING_SECTIONS:
                    seen_definition_section = True
                continue
            if level == 3:
                current = None
                if section not in FINDING_SECTIONS:
                    continue
                found = LEGACY_TOKEN.findall(title)
                if len(found) != 1:
                    raise ReviewFindingsError(
                        f"the `### ` heading at line {line} under `## "
                        f"{section}` carries "
                        + ("no finding id" if not found
                           else f"{len(found)} finding ids")
                        + "; a finding definition carries exactly one")
                current = Definition(finding_id=found[0], section=section,
                                     date=date, line=line)
                # A DICT lookup rather than a rescan of every definition read
                # so far: the rescan was quadratic in a hand-editable file's
                # definition count.
                earlier_line = defined_at.get(current.finding_id)
                if earlier_line is not None:
                    raise ReviewFindingsError(
                        f"the finding id {redact(current.finding_id)} is "
                        f"defined twice in the {date} report, at lines "
                        f"{earlier_line} and {line}; a slug defined twice "
                        "is refused rather than disambiguated")
                defined_at[current.finding_id] = line
                definitions.append(current)
            continue

        cells = _cells(text)
        if cells is None:
            reading = None
            if current is not None:
                note = NOTE_MARKER.match(text)
                if note:
                    notes.append(Note(finding_id=current.finding_id,
                                      kind=note.group(1),
                                      pass_ordinal=int(note.group(2)),
                                      line=line))
                else:
                    near = _NOTE_MARKER_SHAPE.match(text)
                    if near:
                        raise ReviewFindingsError(
                            f"the note at line {line} carries the pass ordinal "
                            f"{redact(near.group(1))}, which is outside the "
                            "ordinal grammar: 1 to 4 ASCII digits with no "
                            "leading zero; a note the parser cannot read is "
                            "refused rather than read as prose")
            continue

        if _header_matches(cells, SUMMARY_HEADER):
            if summary_table_seen:
                raise ReviewFindingsError(
                    f"the {date} report carries a second summary table at line "
                    f"{line}; the report carries one summary table, between "
                    "the title and `## Propose`")
            if seen_definition_section:
                raise ReviewFindingsError(
                    f"the summary table at line {line} stands after a finding "
                    "section; the summary table sits between the title and "
                    "`## Propose`")
            summary_table_seen = True
            reading = "summary"
            continue

        if _header_matches(cells, RECORD_HEADER):
            if record_table_seen:
                raise ReviewFindingsError(
                    f"the {date} report carries a second lifecycle record "
                    f"table at line {line}; the records live in one table "
                    "under `## Coverage`")
            if section != "coverage":
                raise ReviewFindingsError(
                    f"the lifecycle record table at line {line} stands outside "
                    "`## Coverage`; the records live in one table under that "
                    "heading")
            record_table_seen = True
            reading = "record"
            continue

        if _header_matches(cells, ASSESSMENT_HEADER):
            if assessment_table_seen:
                raise ReviewFindingsError(
                    f"the {date} report carries a second assessment table at "
                    f"line {line}; the report carries one assessment table, "
                    "under `## Coverage`")
            if section != "coverage":
                raise ReviewFindingsError(
                    f"the assessment table at line {line} stands outside `## "
                    "Coverage`; the assessment rows live in one table under "
                    "that heading")
            assessment_table_seen = True
            reading = "assessment"
            continue

        if _header_matches(cells, MATRIX_HEADER):
            if matrix_table_seen:
                raise ReviewFindingsError(
                    f"the {date} report carries a second per-goal matrix at "
                    f"line {line}; the report carries one, under `## Coverage`")
            if section != "coverage":
                raise ReviewFindingsError(
                    f"the per-goal matrix at line {line} stands outside `## "
                    "Coverage`; the matrix lives under that heading")
            matrix_table_seen = True
            reading = "matrix"
            continue

        if _is_delimiter(cells):
            continue

        if reading is None:
            # INTEGRITY. The record table is found BY ITS HEADER ROW, so a
            # mistyped header left every row beneath it owned by nothing and
            # skipped in silence: a pass that believed it recorded a
            # resolution recorded nothing, and the index computed the finding
            # open. A five-cell, ISO-dated row under `## Coverage` is a record
            # row whose table went missing, and that is a refusal rather than
            # a drop.
            if (section == "coverage" and len(cells) == len(MATRIX_HEADER)
                    and OBJECTIVE_ID.match(cells[0].strip().strip("`").strip())):
                raise ReviewFindingsError(
                    f"the row at line {line} under `## Coverage` carries "
                    f"{len(MATRIX_HEADER)} cells and an objective id in the "
                    "first, and no table owns it; the per-goal matrix is found "
                    "by its header row — " + " | ".join(MATRIX_HEADER)
                    + " — so a mistyped header drops every row beneath it and "
                      "silently disables the rollup check for those goals")
            if (section == "coverage" and len(cells) == len(RECORD_HEADER)
                    and _ISO_DATE.match(cells[0].strip().strip("`").strip())):
                raise ReviewFindingsError(
                    f"the row at line {line} under `## Coverage` carries "
                    f"{len(RECORD_HEADER)} cells and an ISO date in the first, "
                    "and no table owns it; the lifecycle record table is found "
                    "by its header row — "
                    + " | ".join(RECORD_HEADER)
                    + " — so a mistyped header drops every row beneath it")
            # INTEGRITY, the same trap for the assessment table. It too is
            # found BY ITS HEADER ROW, so a mistyped header leaves every row
            # beneath it unowned. A seven-cell row under `## Coverage` whose
            # first cell is an `OBJ-N` id is a mistyped-header symptom, not a
            # coincidence — the record header is five cells and never carries
            # an objective id, so this shape belongs to no table but the
            # assessment one, and is refused rather than silently dropped.
            if (section == "coverage" and len(cells) == len(ASSESSMENT_HEADER)
                    and OBJECTIVE_ID.match(cells[0].strip().strip("`").strip())):
                raise ReviewFindingsError(
                    f"the row at line {line} under `## Coverage` carries "
                    f"{len(ASSESSMENT_HEADER)} cells and an objective id in "
                    "the first, and no table owns it; the assessment table is "
                    "found by its header row — "
                    + " | ".join(ASSESSMENT_HEADER)
                    + " — so a mistyped header drops every row beneath it")
            continue

        if reading == "summary":
            found = LEGACY_TOKEN.findall(cells[0])
            if len(found) != 1:
                raise ReviewFindingsError(
                    f"the summary row at line {line} carries "
                    + ("no finding id" if not found
                       else f"{len(found)} finding ids")
                    + " in its first cell; one row names one finding")
            summary_row_ids.append(found[0])
            continue

        if reading == "assessment":
            assessments.append(_assessment_from_row(cells, line))
            continue

        if reading == "matrix":
            if len(cells) != len(MATRIX_HEADER):
                raise ReviewFindingsError(
                    f"the per-goal matrix row at line {line} carries "
                    f"{len(cells)} cells; a matrix row carries "
                    f"{len(MATRIX_HEADER)} — "
                    + " | ".join(MATRIX_HEADER))
            objective = cells[0].strip().strip("`").strip()
            rendered = cells[1].strip().strip("`").strip()
            if not OBJECTIVE_ID.match(objective):
                raise ReviewFindingsError(
                    f"the per-goal matrix row at line {line} names "
                    f"{redact(objective)}, which is outside the `OBJ-N` "
                    "grammar")
            if rendered not in ALIGNMENT_CONCLUSION:
                raise ReviewFindingsError(
                    f"the per-goal matrix row at line {line} renders the "
                    f"alignment rollup {redact(rendered)}, which is outside "
                    "the closed set " + " | ".join(ALIGNMENT_CONCLUSION))
            matrix_rollups[objective] = rendered
            continue

        records.append(_record_from_row(cells, date, line))

    return ParsedReport(date=date, grammar=LIFECYCLE, is_legacy=False,
                        definitions=tuple(definitions),
                        records=tuple(records), notes=tuple(notes),
                        summary_row_ids=tuple(summary_row_ids),
                        legacy_ids=frozenset(), raised=len(definitions),
                        assessments=tuple(assessments),
                        matrix_rollups=matrix_rollups,
                        frontmatter_outcomes=measured_objectives(
                            frontmatter)[0])


# ── checking one report's shape ───────────────────────────────────────────


def validate_structure(report: ParsedReport) -> None:
    """The summary-table postcondition and the note-pairing requirement.

    A STRUCTURAL check, deliberately distinct from any judgement about whether
    a finding is right. Returns None; raises `ReviewFindingsError` on a
    disagreement. A legacy report is outside both rules and passes.
    """
    if report.is_legacy:
        return None

    seen: set[str] = set()
    repeated = sorted({fid for fid in report.summary_row_ids
                       if fid in seen or seen.add(fid)})
    if repeated:
        raise ReviewFindingsError(
            f"the {report.date} report's summary-table row ids are not "
            "pairwise distinct: " + _join_ids(repeated))

    defined = {definition.finding_id for definition in report.definitions}
    rows = set(report.summary_row_ids)
    if rows != defined:
        missing = sorted(defined - rows)
        extra = sorted(rows - defined)
        parts = []
        if extra:
            parts.append("in the summary table and defined by no finding "
                         "section: " + _join_ids(extra))
        if missing:
            parts.append("defined by a finding section and absent from the "
                         "summary table: " + _join_ids(missing))
        raise ReviewFindingsError(
            f"the {report.date} report's summary-table row id set does not "
            "equal the set of ids its finding sections define — "
            + "; ".join(parts))

    # ── the three consistency checks ADR-0108 permits ─────────────────────
    # "A structural checker may verify that a row carries its required
    # fields, that its pair is legal, that its references resolve, and that
    # the rollup agrees with its parts." NONE of these checks whether an
    # assessment is TRUE — that judgment stays the architect's, and a checker
    # claiming otherwise would restore the false green this grammar removes.
    # They check only that the record does not say two different things.

    # (a) A findings-cell reference must resolve. A dangling id names a
    #     finding nobody can read, so the gap it points at is unreachable.
    known = defined | {record.finding_id for record in report.records}
    dangling = sorted({fid for assessment in report.assessments
                       for fid in assessment.findings if fid not in known})
    if dangling:
        raise ReviewFindingsError(
            f"the {report.date} report's assessment rows reference findings "
            "this report neither defines nor records: " + _join_ids(dangling))

    # (b) The per-goal matrix's alignment rollup must agree with the parts it
    #     rolls up. Disagreement means one of the two was written by hand.
    by_goal: dict[str, list[Assessment]] = {}
    for assessment in report.assessments:
        by_goal.setdefault(assessment.objective, []).append(assessment)
    for objective, rendered in sorted(report.matrix_rollups.items()):
        parts = by_goal.get(objective, [])
        derived = alignment_rollup(part.conclusion for part in parts)
        if rendered != derived:
            raise ReviewFindingsError(
                f"the {report.date} report's per-goal matrix renders "
                f"{redact(objective)} as {redact(rendered)}, and its "
                f"{len(parts)} assessment row(s) derive {redact(derived)}; "
                "the rollup is derived from the parts and is never written "
                "independently")

    # (c) The frontmatter's measurement outcome must agree with the rows.
    #     This is the check that catches a report saying `attempted` in its
    #     frontmatter while its own parts discriminate — one record, two
    #     answers, and the rotation reads the wrong one. ADR-0109 widens the
    #     key from "objective" to "counted thing": a part-less key (a bare
    #     `OBJ-N`) is checked against ALL of that goal's parts, exactly as
    #     before; a part-level key (`OBJ-N.k`) is checked against that ONE
    #     row alone, because a part's entry counts only itself.
    by_part: dict[str, Assessment] = {a.part: a for a in report.assessments}
    for thing, entry in sorted(report.frontmatter_outcomes.items()):
        if PART_ID.match(thing):
            row = by_part.get(thing)
            parts = [row] if row is not None else []
        else:
            parts = by_goal.get(thing, [])
        recorded = entry.get("outcome")
        derived = measurement_outcome(
            (part.evidence, part.conclusion) for part in parts)
        if recorded is not None and recorded != derived:
            raise ReviewFindingsError(
                f"the {report.date} report records {redact(thing)} as "
                f"{redact(recorded)} in `measured_objectives`, and its "
                f"{len(parts)} assessment row(s) derive {redact(derived)}; "
                "the measurement outcome is derived from the parts")

    # (d) rule:a-blocked-part-is-counted-in-its-own-right: a part
    #     BLOCKED in this report (`partial`/`unavailable` evidence reaching
    #     `inconclusive`) must carry its own part-level `measured_objectives`
    #     entry, keyed on its part id. A report whose key omits it drops the
    #     obligation with nothing on the surface to show why — the false
    #     green a sibling part's `measured` outcome would otherwise let
    #     stand.
    for assessment in report.assessments:
        blocked = (assessment.evidence in ("partial", "unavailable")
                   and assessment.conclusion == "inconclusive")
        if blocked and assessment.part not in report.frontmatter_outcomes:
            raise ReviewFindingsError(
                f"the {report.date} report's assessment row for "
                f"{redact(assessment.part)} is blocked ({assessment.evidence} "
                "evidence reaching an inconclusive conclusion), and the "
                "`measured_objectives` key carries no entry for it; a "
                "blocked part is counted in its own right, and its "
                "obligation must stay visible even where a sibling part or "
                "the goal's own entry reads measured")

    paired = {(record.finding_id, record.event, record.pass_ordinal)
              for record in report.records}
    for note in report.notes:
        if note.kind not in PAIRED_NOTE_KINDS:
            continue
        if (note.finding_id, note.kind, note.pass_ordinal) not in paired:
            raise ReviewFindingsError(
                f"the {note.kind} note at line {note.line} of the "
                f"{report.date} report names {redact(note.finding_id)} and "
                f"pairs with no lifecycle record of that finding, that kind "
                f"and pass {redact(str(note.pass_ordinal), quoted=False)}; the "
                "note's prose is not quoted here")

    # ADR-0108: assessment rows list objectives in NUMERIC order, so OBJ-2
    # precedes OBJ-10. A lexicographic comparison of the id strings would get
    # this backwards ("OBJ-10" < "OBJ-2"), which is exactly the bug this check
    # exists to catch — the comparison below is over the parsed integer.
    for earlier, later in zip(report.assessments, report.assessments[1:]):
        earlier_n = int(earlier.objective[len("OBJ-"):])
        later_n = int(later.objective[len("OBJ-"):])
        if later_n < earlier_n:
            raise ReviewFindingsError(
                f"the assessment row at line {later.line} names "
                f"{redact(later.objective)} after {redact(earlier.objective)}; "
                "assessment rows list objectives in numeric order, so OBJ-2 "
                "precedes OBJ-10")
    return None


# ── reading the stream across dates ───────────────────────────────────────


def _definitions_across(reports: Iterable[ParsedReport]) -> dict[str, tuple[str, bool]]:
    """`{finding id: (defining report date, whether that report is legacy)}`.

    Definitions are recognised per grammar: on a lifecycle report by its
    `### `-headed entries, and on a legacy report by the id set of the frozen
    legacy count. One id is defined once across the directory, so a second
    claim on the same id is refused rather than disambiguated.
    """
    claims: dict[str, tuple[str, bool]] = {}

    def claim(finding_id: str, date: str, legacy: bool) -> None:
        held = claims.get(finding_id)
        if held is not None:
            raise ReviewFindingsError(
                f"the finding id {redact(finding_id)} is defined twice — by "
                f"the {held[0]} report and by the {date} report; a slug "
                "defined twice is refused rather than disambiguated")
        claims[finding_id] = (date, legacy)

    for report in sorted(reports, key=lambda r: r.date):
        for definition in report.definitions:
            claim(definition.finding_id, report.date, False)
        for finding_id in sorted(report.legacy_ids):
            claim(finding_id, report.date, True)
    return claims


def standing_by_finding(
        reports: Iterable[ParsedReport], *,
        locator_exists: Callable[[str], bool] | None = None) -> dict[str, str]:
    """`{finding id: standing}` over every finding the corpus defines.

    Records order by containing-report date, then by pass ordinal. Standing
    follows the stream in order rather than a rank table: no record is `open`,
    a `re-verified` record leaves the standing where it stands, a `disputed`
    record disputes it, and a `resolved` record resolves it and CLOSES the
    stream — a later record of any kind against that finding is refused. A
    finding a legacy report defined is `unknown` and stays `unknown`: the
    legacy row is never recomputed from later records.

    `locator_exists` is the injected existence probe. `None` skips the check
    entirely. When supplied, it is asked about a `resolved` record's locator
    alone; a False answer refuses the record, and a `ReviewFindingsError` the
    probe raises is re-raised carrying the record's line and report date. The
    caller supplies a probe already bounded to the repository root; this module
    performs no I/O after import. The three textual shapes that reach outside
    the root — an absolute path, a `~` prefix and a `..` traversal — are
    already refused at parse time by `_refuse_escaping_shape`, on every record
    whatever its event, so the probe never sees one.
    """
    reports = list(reports)
    claims = _definitions_across(reports)
    standing = {finding_id: ("unknown" if legacy else "open")
                for finding_id, (_date, legacy) in claims.items()}

    ordered = sorted(
        (record for report in reports for record in report.records),
        key=lambda r: (r.report_date, r.pass_ordinal, r.finding_id, r.event))

    seen_pass: set[tuple[str, str, int]] = set()
    closed: dict[str, Record] = {}

    for record in ordered:
        held = claims.get(record.finding_id)
        if held is None or held[0] != record.source_date:
            raise ReviewFindingsError(
                f"the lifecycle record at line {record.line} of the "
                f"{record.report_date} report names {redact(record.finding_id)} "
                f"on {record.source_date}, and no definition of that finding "
                "stands on that date")
        if record.report_date < record.source_date:
            raise ReviewFindingsError(
                f"the lifecycle record at line {record.line} of the "
                f"{record.report_date} report names "
                f"{redact(record.finding_id)} and predates the "
                f"{record.source_date} definition it names; a record cannot "
                "predate the definition it references")

        key = (record.finding_id, record.report_date, record.pass_ordinal)
        if key in seen_pass:
            raise ReviewFindingsError(
                f"the {record.report_date} report carries two lifecycle "
                f"records for {redact(record.finding_id)} from pass "
                f"{redact(str(record.pass_ordinal), quoted=False)}; one pass "
                "writes at most one record per finding")
        seen_pass.add(key)

        earlier = closed.get(record.finding_id)
        if earlier is not None:
            raise ReviewFindingsError(
                f"the lifecycle record at line {record.line} of the "
                f"{record.report_date} report stands against "
                f"{redact(record.finding_id)}, which the {earlier.report_date} "
                "report resolved; a resolution closed the stream, and a "
                "recurrence is a new finding naming the resolved one")

        if record.event == "resolved":
            # The probe is handed a locator and nothing else, so its own
            # refusal can name neither the record nor the report it came from.
            # This is the layer that knows both, so this is the layer that adds
            # them — on a corpus of several reports the reader would otherwise
            # grep the string to find the row.
            try:
                found = locator_exists is None or locator_exists(record.locator)
            except ReviewFindingsError as exc:
                raise ReviewFindingsError(
                    f"the resolved record at line {record.line} of the "
                    f"{record.report_date} report: {exc.problem}") from None
            if not found:
                raise ReviewFindingsError(
                    f"the resolved record at line {record.line} of the "
                    f"{record.report_date} report names the locator "
                    f"{redact(record.locator)}, which is no surface in the tree")
            closed[record.finding_id] = record

        if held[1]:
            continue                       # a legacy row never moves
        if record.event == "resolved":
            standing[record.finding_id] = "resolved"
        elif record.event == "disputed":
            standing[record.finding_id] = "disputed"
        # `re-verified` leaves the standing where it stands: it keeps an open
        # finding open, and it never clears a dispute.

    return standing


def report_standing(report: ParsedReport, standing: dict[str, str]) -> dict[str, int]:
    """The per-standing count over the report's OWN raised findings.

    A legacy report reports its frozen legacy count under `unknown`, and that
    key is always present — the row carries a value rather than a blank, and
    it is never recomputed from a later report's records. A lifecycle report
    reports only the states its findings actually occupy, so a renderer reads
    an absent key as zero.
    """
    if report.is_legacy:
        return {"unknown": report.raised}
    counts: dict[str, int] = {}
    for definition in report.definitions:
        state = standing.get(definition.finding_id, "unknown")
        counts[state] = counts.get(state, 0) + 1
    return counts


def events_elsewhere(report: ParsedReport,
                     reports: Iterable[ParsedReport]) -> int:
    """How many of `report`'s records name a finding another report defined.

    The count belongs on the RECORDING report's row and is never projected
    onto the row of the report that defined the finding. A record whose target
    resolves to no definition counts here too; `standing_by_finding` is the
    lane that refuses it.
    """
    claims = _definitions_across(reports)
    return sum(1 for record in report.records
               if claims.get(record.finding_id, (None, False))[0] != report.date)


def check_part_carryover(reports: Iterable[ParsedReport]) -> None:
    """rule:a-blocked-part-is-counted-in-its-own-right, cross-date half.

    A part that EVER carried a part-level `measured_objectives` entry keeps
    carrying one for as long as it is still assessed. `validate_structure`
    checks the single-report half (a part blocked IN THIS report); this
    function checks the other: a part that carried a part-level entry on an
    EARLIER date and is still assessed here, but this report's key omits it.

    Raises `ReviewFindingsError` naming the report where the obligation is
    first dropped. Reads lifecycle reports only, in date order; a legacy
    report neither starts nor breaks a part's carryover — it carries no
    parts at all — and is skipped.
    """
    carried: set[str] = set()
    for report in sorted((r for r in reports if not r.is_legacy),
                          key=lambda r: r.date):
        assessed_parts = {a.part for a in report.assessments}
        for part in sorted(carried & assessed_parts):
            if part not in report.frontmatter_outcomes:
                raise ReviewFindingsError(
                    f"the {report.date} report still assesses "
                    f"{redact(part)}, which carried a part-level "
                    "`measured_objectives` entry on an earlier date; the "
                    "obligation stays visible for as long as the part is "
                    "assessed, and this report's key omits it")
        for thing in report.frontmatter_outcomes:
            if PART_ID.match(thing):
                carried.add(thing)
    return None


# ── ADR-0108: the two derived values over one goal's part rows ───────────


def alignment_rollup(conclusions: Iterable[str]) -> str:
    """The per-goal ALIGNMENT rollup over one goal's part conclusions.

    PRECONDITION: every input is a member of `ALIGNMENT_CONCLUSION`.
    `_assessment_from_row` has already refused anything else, so this
    function never validates its input, and an out-of-vocabulary value falls
    into the catch-all rather than raising.

    TOTAL, including the empty case: a goal whose declared measure is the
    recorded absence of one yields no parts, and its rollup is `not-assessed`
    — the same value a goal every one of whose parts is itself `not-assessed`
    yields. `gap` outranks everything; only then does every-`serves` reach
    `serves`; everything else that is not the all-`not-assessed`/empty case
    is `inconclusive`, including a `serves` sitting beside a `not-assessed`.
    """
    conclusions = list(conclusions)
    if any(conclusion == "gap" for conclusion in conclusions):
        return "gap"
    if conclusions and all(conclusion == "serves" for conclusion in conclusions):
        return "serves"
    if not conclusions or all(conclusion == "not-assessed"
                              for conclusion in conclusions):
        return "not-assessed"
    return "inconclusive"


def measurement_outcome(pairs: Iterable[tuple[str, str]]) -> str:
    """The per-goal MEASUREMENT outcome over one goal's `(evidence, conclusion)` pairs.

    PRECONDITION: every pair is a member of `LEGAL_PAIRS`.
    `_assessment_from_row` has already refused an illegal pair, so this
    function never validates its input.

    TOTAL, including the empty case (`not-assessed`). `measured` when any part
    discriminates (its pair is in `DISCRIMINATING_PAIRS`); otherwise
    `attempted` when at least one part's evidence is not `not-attempted` —
    i.e. at least one part was attempted, whatever it settled; otherwise
    `not-assessed`. So `partial`+`inconclusive` and `unavailable`+
    `inconclusive` both read `attempted`, never `measured`.
    """
    pairs = list(pairs)
    if any(pair in DISCRIMINATING_PAIRS for pair in pairs):
        return "measured"
    if any(evidence != "not-attempted" for evidence, _conclusion in pairs):
        return "attempted"
    return "not-assessed"


# ── ADR-0108: the rotation reader ─────────────────────────────────────────

#: The three `measured_objectives` shapes `measured_objectives()` recognises,
#: plus `"absent"` for no key at all. `"empty"` and `"absent"` are distinct:
#: an empty structured/flat value is a POSITIVE record of zero goals measured,
#: while an absent key is no recorded measurement evidence at all — the
#: literal `unknown`, for every goal, forever.
MEASURED_OBJECTIVES_SHAPES = ("structured", "flat", "empty", "absent")

_MEASURED_OBJECTIVES_KEY = re.compile(
    r"^measured_objectives:[ \t]*(.*)$", re.MULTILINE)
#: The `OBJ-N` id inside a flat legacy list, matched with `findall` and so
#: NOT anchored by the pattern itself. The trailing lookahead does that
#: work: without it a bounded run matched the first four digits of a
#: longer one, so `OBJ-99999` read as `OBJ-9999` and discharged a goal
#: nobody named — a silent misread of exactly the class this module
#: refuses everywhere else.
_FLAT_OBJECTIVE_ID = re.compile(r"OBJ-[1-9][0-9]{0,3}(?![0-9])")
_BLOCK_KV = re.compile(r"^([A-Za-z_]+):[ \t]*(.*)$")


def _read_block_sequence(text: str) -> list[list[str]]:
    """The YAML block-sequence-of-mappings starting at the front of `text`.

    A minimal, stdlib-only reading of exactly the shape this module's own
    writer produces — a `- key: value` line opening each item, followed by
    zero or more further-indented `key: value` continuation lines, all at one
    fixed list indent. A blank line is NEVER a terminator — see the body.
    Stops at the first line at or
    above the list's own indent that is not a new `- ` item, or the text's
    end. This is not a YAML parser: it reads one shape and refuses nothing —
    a line matching neither `- ` nor a continuation simply ends the sequence,
    which is the correct read for "no block sequence follows here" (the
    `empty` shape) rather than a parse error.
    """
    entries: list[list[str]] = []
    current: list[str] | None = None
    list_indent: int | None = None
    for line in text.splitlines():
        if line.strip() == "":
            # A blank line is never a terminator: not before the sequence
            # starts (it is the key line's own trailing remainder) and not
            # inside it (a hand-kept ten-goal list may space its entries, and
            # truncating there would silently drop every entry below, leaving
            # their rotations undischarged with nothing to see).
            continue
        if line.lstrip(" ").startswith("#"):
            # A COMMENT is never a terminator either, at any indent. `#` opens
            # a comment in YAML, so a real parser skips the line and reads the
            # sequence beneath it — and the SHIPPED TEMPLATE puts `##` guidance
            # lines between the key and the entries it tells the writer to
            # uncomment. Breaking here read that report as `empty`: a positive
            # record of ZERO measurements, with no refusal, every rotation
            # undischarged and every escalation chain broken, from a report
            # whose author did nothing wrong but leave the instructions in.
            continue
        stripped = line.lstrip(" ")
        indent = len(line) - len(stripped)
        if stripped.startswith("- "):
            if list_indent is None:
                list_indent = indent
            elif indent > list_indent:
                # A DEEPER `- ` is a nested sub-list inside the current entry,
                # not the end of the sequence. Treat it as a continuation line:
                # breaking here dropped every entry below it in silence, which
                # is the same failure the blank-line branch above exists to
                # prevent — a dropped entry reads as an undischarged rotation
                # with nothing on the surface to show why.
                if current is not None:
                    current.append(stripped)
                continue
            elif indent < list_indent:
                break
            if current is not None:
                entries.append(current)
            current = [stripped[2:]]
            continue
        if list_indent is None or indent <= list_indent:
            break
        if current is not None:
            current.append(stripped)
    if current is not None:
        entries.append(current)
    return entries


def _counted_thing_key(objective: str, part: str | None) -> str:
    """The COUNTED-THING key for one `measured_objectives` entry.

    rule:rotation-discharges-on-attempt-or-measurement: an entry carries one
    counted thing per objective-and-part pair where it names a part, and one
    per objective where it does not. The part id already carries the goal
    number (`OBJ-N.k`), so it alone is a unique key; a part-less entry keys
    on the bare objective id. The two shapes never collide.
    """
    return part if part else objective


def measured_objectives(frontmatter: str) -> tuple[dict[str, dict[str, str]], str]:
    """`measured_objectives`, read from raw frontmatter text, and its shape.

    Returns `(outcomes, shape)`. `outcomes` maps a COUNTED-THING key — a bare
    `OBJ-N` objective id for a part-less entry, or an `OBJ-N.k` part id for a
    part-level one (rule:a-blocked-part-is-counted-in-its-own-right) —
    to a field mapping carrying at least `"outcome"`; `shape` is one of
    `MEASURED_OBJECTIVES_SHAPES`.

      * ABSENT — no `measured_objectives` key at all. `outcomes` is `{}` and
        `shape` is `"absent"`; a caller reads this as `unknown` for EVERY
        goal, never `not-assessed` and never an empty list.
      * EMPTY — the key is present with no entries (`[]`, `{}`, or a bare key
        with no block sequence beneath it). `outcomes` is `{}` and `shape` is
        `"empty"` — a POSITIVE record of zero goals measured, distinct from
        `absent` even though both yield an empty mapping here.
      * FLAT — a bracketed list of bare `OBJ-N` ids (the pre-ADR-0108 shape).
        Every id is DEMOTED to `"attempted"`, never promoted to `"measured"`,
        because that grammar cannot distinguish the two. A flat list carries
        no parts, so every key it yields is a bare objective id.
      * STRUCTURED — a YAML block sequence of per-goal or per-part mappings,
        read as recorded: each entry's own `outcome` field stands, whatever
        it is. An entry MAY carry an optional `part` field (`OBJ-N.k`), and
        EVERY entry — whether or not it names a part — MUST carry a
        `measure_digest` field: an identifier of the measure text version
        the entry concerns (rule:rotation-discharges-on-attempt-or-measurement:
        "on EVERY entry whether or not it names a part", a correctness
        requirement rather than a convenience). An entry missing it is
        REFUSED, on the same footing as a missing `outcome`. An entry MAY
        also carry `escalation_strikes` / `escalation_owed` /
        `escalation_owed_since` / `escalation_state` — the carried
        escalation claim `escalation_state`/`owed_escalations`/
        `spend_escalations` read and verify; see `escalation_state`'s own
        docstring for that grammar.

    The STRONGEST outcome wins across two entries naming the same counted
    thing on one date (append-only across passes), and this comparison is
    scoped to ONE counted thing: a part's `attempted` entry is never buried
    by, and never buries, a sibling part's or the goal's own `measured`
    entry, because they key on different counted-thing keys.

    This module performs no I/O: `frontmatter` is raw text the caller already
    holds, on the same contract `_frontmatter_scalar` uses elsewhere here.
    """
    match = _MEASURED_OBJECTIVES_KEY.search(frontmatter or "")
    if match is None:
        return {}, "absent"

    remainder = match.group(1).strip()
    if remainder in ("", "{}"):
        # A bare key: either a block sequence follows, or nothing does.
        entries = _read_block_sequence((frontmatter or "")[match.end():])
        if remainder == "{}" and entries:
            # `{}` is an explicit empty MAPPING, and a block sequence cannot
            # belong to it. Reading the pair as empty would silently discard
            # entries a pass wrote, and reading it as structured would accept
            # YAML no parser accepts — so refuse and name both halves.
            raise ReviewFindingsError(
                "the `measured_objectives` key carries an explicit empty "
                "mapping `{}` with a block sequence beneath it; a value is "
                "written one way or the other, and reading this pair either "
                "way would discard one half of what was written")
        if not entries:
            return {}, "empty"
        outcomes: dict[str, dict[str, str]] = {}
        for entry_lines in entries:
            fields: dict[str, str] = {}
            for entry_line in entry_lines:
                kv = _BLOCK_KV.match(entry_line)
                if kv:
                    fields[kv.group(1)] = kv.group(2).strip().strip(
                        '"').strip("'")
            unknown = sorted(set(fields) - ENTRY_FIELDS)
            if unknown:
                # `ENTRY_FIELDS` is the reader's declared vocabulary, and an
                # entry outside it is refused rather than kept. Read
                # permissively, a typo — `eskalation_strikes` — was retained
                # verbatim and the entry fell through to reconstruction: the
                # carried chain broke, `owed_since` was lost, and nothing on
                # any surface said why. Refusing costs a writer one error
                # message; the permissive read cost a silent demotion.
                raise ReviewFindingsError(
                    "a `measured_objectives` entry carries the field(s) "
                    + ", ".join(redact(name) for name in unknown)
                    + ", which this reader does not know; a mistyped field is "
                    "kept by no one and would break the carried escalation "
                    "chain in silence")
            objective = fields.get("objective")
            if objective and not OBJECTIVE_ID.match(objective):
                raise ReviewFindingsError(
                    "a `measured_objectives` entry names the objective "
                    f"{redact(objective)}, which is outside the `OBJ-N` "
                    "grammar; a mistyped id would discharge a rotation nobody "
                    "could match back to a goal")
            part = fields.get("part")
            if part:
                if not PART_ID.match(part):
                    raise ReviewFindingsError(
                        "a `measured_objectives` entry names the part "
                        f"{redact(part)}, which is outside the `OBJ-N.k` "
                        "grammar")
                if objective and f"OBJ-{PART_ID.match(part).group(1)}" != objective:
                    raise ReviewFindingsError(
                        "a `measured_objectives` entry names the objective "
                        f"{redact(objective)} and the part {redact(part)}, "
                        "whose goal numbers disagree; a part belongs to the "
                        "goal its own entry names")
            outcome = fields.get("outcome")
            if outcome is None:
                raise ReviewFindingsError(
                    "a `measured_objectives` entry for "
                    f"{redact(objective or '(no objective)')} carries no "
                    "`outcome` field; a missing outcome reads as an "
                    "undischarged rotation exactly as a mistyped one does")
            if outcome not in _OUTCOME_RANK:
                raise ReviewFindingsError(
                    "a `measured_objectives` entry for "
                    f"{redact(objective or '(no objective)')} carries the "
                    f"outcome {redact(outcome)}, which is outside the closed "
                    "set " + " | ".join(_OUTCOME_RANK)
                    + "; a typo would read as an undischarged rotation with "
                      "nothing on the surface to show why")
            if not objective:
                raise ReviewFindingsError(
                    "a `measured_objectives` entry carries no `objective` "
                    "field; an entry naming no goal is refused rather than "
                    "dropped, because a dropped entry reads as an "
                    "undischarged rotation nobody can see")
            thing = _counted_thing_key(objective, part)
            # rule:rotation-discharges-on-attempt-or-measurement: EVERY entry —
            # goal-level or part-level alike — carries an identifier of the
            # measure text version it concerns, as a CORRECTNESS requirement:
            # the rewrite-reset and the carried-escalation verification below
            # both compare this identifier, reconstruction reads frontmatter
            # alone, and an entry without one leaves both unexecutable. This
            # is refused on the same footing as a missing `outcome`.
            if not fields.get("measure_digest"):
                raise ReviewFindingsError(
                    "the `measured_objectives` entry for "
                    f"{redact(thing)} carries no `measure_digest` field; "
                    "every structured entry carries an identifier of the "
                    "measure text version it concerns, whether or not it "
                    "names a part — without it neither the rewrite reset nor "
                    "the carried-escalation verification is executable")
            # The key is append-only across passes on ONE date, so two entries
            # may name one counted thing. The STRONGEST outcome wins — last-
            # entry-wins would let a later pass's `attempted` bury an earlier
            # pass's `measured` and advance a strike the thing never earned.
            # This comparison is scoped to ONE counted thing (the dict key),
            # so a part's `attempted` is never weighed against, and never
            # buries, a sibling part's or the goal's own `measured`.
            standing = outcomes.get(thing)
            if standing is None or _OUTCOME_RANK.get(
                    fields.get("outcome", ""), 0) > _OUTCOME_RANK.get(
                        standing.get("outcome", ""), 0):
                outcomes[thing] = fields
        return outcomes, "structured"

    if remainder == "[]":
        return {}, "empty"

    if remainder.startswith("["):
        if not remainder.endswith("]"):
            raise ReviewFindingsError(
                "the `measured_objectives` key opens a bracketed list that "
                "does not close on the same line; this reader admits the "
                "single-line flat form only, and reading an unclosed one "
                "would name zero goals and silently discharge none")
        ids = _FLAT_OBJECTIVE_ID.findall(remainder)
        return ({obj_id: {"objective": obj_id, "outcome": "attempted"}
                 for obj_id in ids},
                "flat")

    # An unrecognised scalar is REFUSED, never read as `empty`. The three
    # readable shapes are a block sequence, `[]`, and a bracketed flat list;
    # `empty` means the writer positively recorded zero goals measured, which
    # is a claim, and an unparsed value is not that claim. Reading one as
    # `empty` discharged nothing for every goal with nothing on the surface to
    # show why — the same silent-read class the missing-`objective` and
    # out-of-enum-`outcome` refusals above exist to remove. An unbracketed id
    # list (`measured_objectives: OBJ-1, OBJ-2`) is the plausible hand-typing
    # slip this catches.
    raise ReviewFindingsError(
        f"the `measured_objectives` key carries {redact(remainder)}, which is "
        "outside the shapes this reader admits: a block sequence of per-goal "
        "entries, a bracketed list of `OBJ-N` ids, or `[]` for a positive "
        "record of zero; an unrecognised value is refused rather than read as "
        "empty")


#: How many of the newest report dates the ESCALATION HISTORY span walks
#: when it must reconstruct — a bounded window, never the whole corpus. A run
#: of attempts longer than this truncates, which DEFERS an obligation and
#: never invents one (rule:escalation-history-is-carried-and-bounded).
#: The COVERAGE span: the three newest report dates. Named beside
#: `ESCALATION_WINDOW` because the rule this module implements says the two
#: "are two spans and are never one variable" — a bare `dated[-3:]` beside a
#: named twelve invites exactly the collapse that clause forbids.
COVERAGE_WINDOW = 3

ESCALATION_WINDOW = 12

#: WU4 FIELD-SPELLING NOTE (ADR-0109 defers the spelling to this plan).
#: `measure_digest` is REQUIRED on every structured entry (enforced in
#: `measured_objectives` itself). Five more fields are OPTIONAL on a
#: structured entry and together carry the escalation state a pass computed
#: FOR ITSELF, so a later pass reads it in O(1) rather than recomputing it:
#: `escalation_strikes` (the strike count as of this report, for this
#: counted thing), `escalation_owed` (`"true"` or `"false"`),
#: `escalation_owed_since` (an ISO date, present iff owed),
#: `escalation_spent_on` (an ISO date, the pass that raised the strike-three
#: finding — without it the same obligation is re-owed on every later
#: attempted date, which is why `_step_escalation` carries `spent` at all),
#: and `escalation_state` (`"verified"` or `"unverified"` — the pass's own
#: claim about whether IT could verify its carried count against its
#: predecessor, or had to reconstruct). These five are optional, unlike
#: `measure_digest`:
#: the ADR states a correctness argument for the digest ("a reset ... is
#: unexecutable" without it) but frames the carried count itself as the
#: NORMAL path with an explicit, named FALLBACK (bounded reconstruction) for
#: when it is missing or broken — an entry that omits them is exactly the
#: "legacy or absent-key" shape that fallback exists for, not a malformed one.


def rotation_state(reports: Iterable[tuple[str, str]],
                    active_things: Iterable[str]) -> dict[str, dict[str, object]]:
    """The rotation, over TWO SPANS that are never one variable
    (rule:escalation-history-is-carried-and-bounded): a three-date COVERAGE
    window and a separately-computed ESCALATION history. `active_things` is
    every counted thing to report on — a bare `OBJ-N` objective id or an
    `OBJ-N.k` measure-part id
    (rule:a-blocked-part-is-counted-in-its-own-right); the two callers who care
    about a goal-level-only rotation and a per-part-inclusive one both pass
    exactly the set of keys they want back.

    `reports` is `(date, frontmatter)` pairs — this module performs no I/O,
    so the caller supplies each report's own raw frontmatter text alongside
    its date.

    Returns one entry per thing in `active_things`:

        {"coverage": {date: (outcome, shape)}, "discharged": bool,
         "escalation": {"strikes": int, "owed": bool,
                        "owed_since": str | None, "verified": bool}}

    `coverage` spans the THREE NEWEST report dates that exist and asserts
    nothing about dates that do not, so fewer than three is read as a
    narrower, not a failing, window — unchanged from the pre-ADR-0109
    reading. A thing absent from every report still gets an entry, with an
    empty `coverage` mapping, `discharged: False`, and a zeroed `escalation`.

    `discharged` is true iff AT LEAST ONE date in the COVERAGE window
    records `measured` or `attempted` for that thing — any date in the
    window discharges and the newest carries no special weight.
    `not-assessed` and `unknown` discharge nothing.

    `escalation` is computed by `escalation_state` below, over its OWN
    bounded window (`ESCALATION_WINDOW`) — never the three-date coverage
    window. This is the D4 fix: five report dates recording attempted,
    silent, attempted, silent, attempted give three strikes, because the
    escalation span reads all five and steps over the silent ones, rather
    than the two a three-date coverage window would see.
    """
    # ONE materialization. `reports` is an Iterable, and consuming it twice —
    # once here and once inside escalation_state — leaves a generator empty on
    # the second read, dropping every owed escalation with no error at all.
    dated = sorted(reports, key=lambda pair: pair[0])
    coverage_window = dated[-COVERAGE_WINDOW:]
    coverage_parsed = [(date, *measured_objectives(frontmatter))
                       for date, frontmatter in coverage_window]

    things = list(active_things)
    escalation = escalation_state(dated, things)

    result: dict[str, dict[str, object]] = {}
    for thing in things:
        coverage: dict[str, tuple[str, str]] = {}
        for date, per_thing, shape in coverage_parsed:
            if shape == "absent":
                coverage[date] = ("unknown", shape)
                continue
            entry = per_thing.get(thing)
            if entry is None:
                coverage[date] = ("not-assessed", shape)
                continue
            coverage[date] = (entry.get("outcome", "not-assessed"), shape)

        discharged = any(outcome in ("measured", "attempted")
                         for outcome, _shape in coverage.values())
        result[thing] = {"coverage": coverage,
                         "discharged": discharged,
                         "escalation": escalation[thing]}
    return result


def _step_escalation(baseline: dict[str, object], outcome: str,
                     digest: str | None, prev_digest: str | None,
                     date: str) -> dict[str, object]:
    """One step of the escalation state machine: `baseline` plus one date's
    own `outcome` (and its `measure_digest` compared against the PRIOR
    dated entry's `prev_digest`) yields the state as of `date`.

    Shared by the fast VERIFY path (which compares its result against a
    pass's own claimed carried value) and the bounded RECONSTRUCTION walk
    (which has no claim to verify against and simply accumulates it) — one
    implementation, so the two can never silently disagree on what one step
    means.
    """
    strikes = baseline["strikes"]
    owed = baseline["owed"]
    owed_since = baseline["owed_since"]
    # SPENT: the obligation for THIS run of consecutive attempts was already
    # discharged by a finding. It is not owed and must not be re-owed on the
    # next attempt — "on the third consecutive attempted outcome … the pass
    # owes ONE finding" is one obligation per run, not one per attempt past
    # three. Only a reset ends the run and makes a fresh obligation possible.
    spent = baseline.get("spent", False)
    if digest is not None and prev_digest is not None and digest != prev_digest:
        # The measure was rewritten: clear the old obligation before this
        # date's own outcome is applied on the fresh baseline.
        strikes, owed, owed_since, spent = 0, False, None, False
    if outcome == "measured":
        strikes, owed, owed_since, spent = 0, False, None, False
    elif outcome == "attempted":
        strikes += 1
        if strikes >= 3 and not owed and not spent:
            owed, owed_since = True, date
    # "not-assessed": step over — neither reset nor advance.
    return {"strikes": strikes, "owed": owed, "owed_since": owed_since,
            "spent": spent}


_ZERO_ESCALATION: dict[str, object] = {"strikes": 0, "owed": False,
                                       "owed_since": None, "spent": False}


def _carried_claim(entry: dict[str, str] | None, *, thing: str,
                   date: str) -> dict[str, object] | None:
    """The escalation claim ONE structured entry carries, or `None` when it
    carries none at all (the "legacy or absent-key" shape the bounded
    reconstruction fallback exists for). A claim that IS present but
    malformed is refused outright — the same "refuse rather than silently
    degrade" footing every other field in this module stands on; only true
    ABSENCE of the carrying fields falls through to reconstruction.
    """
    if entry is None or "escalation_strikes" not in entry \
            or "escalation_owed" not in entry:
        return None
    raw_strikes = entry["escalation_strikes"]
    if not _POSITIVE_INT.match(raw_strikes) and raw_strikes != "0":
        raise ReviewFindingsError(
            f"the `measured_objectives` entry for {redact(thing)} on "
            f"{date} carries `escalation_strikes` {redact(raw_strikes)}, "
            "which is not a non-negative integer")
    strikes = int(raw_strikes)
    raw_owed = entry["escalation_owed"].strip().lower()
    if raw_owed not in ("true", "false"):
        raise ReviewFindingsError(
            f"the `measured_objectives` entry for {redact(thing)} on "
            f"{date} carries `escalation_owed` {redact(raw_owed)}, which is "
            "outside the closed set true | false")
    owed = raw_owed == "true"
    owed_since = entry.get("escalation_owed_since") or None
    if owed and owed_since is None:
        raise ReviewFindingsError(
            f"the `measured_objectives` entry for {redact(thing)} on "
            f"{date} carries `escalation_owed: true` with no "
            "`escalation_owed_since`; an owed obligation carries the date it "
            "was first owed")
    if not owed and owed_since is not None:
        raise ReviewFindingsError(
            f"the `measured_objectives` entry for {redact(thing)} on "
            f"{date} carries `escalation_owed: false` alongside an "
            f"`escalation_owed_since` of {redact(owed_since)}; a discharged "
            "obligation carries no owed-since date")
    spent_on = entry.get("escalation_spent_on") or None
    if owed and spent_on is not None:
        raise ReviewFindingsError(
            f"the `measured_objectives` entry for {redact(thing)} on "
            f"{date} carries `escalation_owed: true` alongside an "
            f"`escalation_spent_on` of {redact(spent_on)}; an obligation is "
            "owed or spent and never both")
    if spent_on is not None:
        # A DATE, checked like every other date-shaped value in this module.
        # Unchecked, the spend was unfalsifiable: any non-empty string cleared
        # the obligation, because the predicate downstream reads only whether
        # the field is present.
        _calendar_date(
            spent_on,
            f"the `escalation_spent_on` on the `measured_objectives` entry "
            f"for {redact(thing)} on {date}")
        if spent_on > date:
            raise ReviewFindingsError(
                f"the `measured_objectives` entry for {redact(thing)} on "
                f"{date} was spent on {redact(spent_on)}, a date AFTER the "
                "report recording it; a pass records a finding it raised, "
                "never one it intends to raise")
    return {"strikes": strikes, "owed": owed, "owed_since": owed_since,
            "spent": spent_on is not None}


def _reconstruct_escalation(dated: list[tuple[str, str]],
                            thing: str) -> dict[str, object]:
    """The BOUNDED reconstruction fallback: the `ESCALATION_WINDOW` newest
    report dates, oldest to newest, frontmatter only, stepping over a silent
    date and stopping early at the newest reset
    (rule:escalation-history-is-carried-and-bounded). Used when the fast verify
    path in `escalation_state` finds no usable carried claim to trust.

    Reconstruction recovers `strikes` and `owed` from the outcomes
    themselves, but NEVER a trustworthy `owed_since`: the true first-owed
    date may lie outside this bounded window, or an obligation inside it may
    already have been spent in a way outcomes alone cannot show. The
    returned `owed_since` is therefore always `None` — an `unverified`
    obligation orders by `verified` alone, never by a reconstructed date
    that would misstate how old it really is.
    """
    window = dated[-ESCALATION_WINDOW:]
    parsed = [(date, *measured_objectives(frontmatter))
             for date, frontmatter in window]

    state = dict(_ZERO_ESCALATION)
    last_digest: str | None = None
    for date, per_thing, shape in parsed:
        if shape == "absent":
            continue                          # no evidence at all: step over
        entry = per_thing.get(thing)
        if shape == "flat":
            if entry is not None:
                # A DEMOTED entry RESETS rather than steps over. The flat
                # grammar cannot tell `attempted` from `measured`, so a run of
                # strikes accumulated across it was never earned and must not
                # be carried past it. The reset drops `last_digest` too: an
                # unknown measure version cannot support the rewrite
                # comparison, and comparing against a stale one would fire a
                # rewrite reset the owner never made.
                state = dict(_ZERO_ESCALATION)
                last_digest = None
            continue
        if entry is None:
            continue                          # silent date: step over
        outcome = entry.get("outcome", "not-assessed")
        digest = entry.get("measure_digest")
        state = _step_escalation(state, outcome, digest, last_digest, date)
        if digest is not None:
            last_digest = digest

    # `spent` is False by construction: this walk reads OUTCOMES, and the
    # rule states outright that reconstruction "cannot recover … whether one
    # was already spent". Over-owing is the deliberate direction — deferring
    # an obligation already met costs a pass one finding, while dropping one
    # that was not met loses it silently.
    return {"strikes": state["strikes"], "owed": state["owed"],
           "owed_since": None, "spent": False, "verified": False}


def escalation_state(reports: Iterable[tuple[str, str]],
                      active_things: Iterable[str]) -> dict[str, dict[str, object]]:
    """The ESCALATION HISTORY span alone — the counterpart to the three-date
    coverage window `rotation_state` also reads, and a SEPARATE variable from
    it (rule:escalation-history-is-carried-and-bounded).

    For each counted thing in `active_things`, returns:

        {"strikes": int, "owed": bool, "owed_since": str | None,
         "verified": bool}

    THE READ PATH IS TWO-TIERED, per the ADR's own text: "Escalation reads
    the newest report's carried count, verified against the previous
    report's carried count plus this report's own derived outcome, and a
    contradiction between the two is refused rather than resolved. Where
    that chain is broken by a legacy or absent-key report, the count is
    reconstructed..."

      1. VERIFY. Read the newest report's OWN carried claim for this thing
         (`_carried_claim`). If it carries one, and the PREVIOUS report is
         not itself legacy/absent-key for `measured_objectives`, compute the
         one-step-expected value from the previous report's own carried
         claim (or a zero baseline, when this is the thing's first-ever
         appearance) plus the newest report's outcome and digest
         (`_step_escalation`). A MISMATCH between the claimed and the
         expected value is a CONTRADICTION and is REFUSED — never silently
         resolved by trusting either side. A match is accepted as `verified`.
      2. RECONSTRUCT. Otherwise — the newest report carries no claim at all,
         or the previous report's own shape is `absent`/`flat` and so
         supplies no trustworthy baseline to verify against —
         `_reconstruct_escalation` recovers the count over the bounded
         12-date window and the result is marked `verified: False`
         (`unverified`), because its own first-owed date cannot be
         recovered and its `owed_since` is always `None`.

    A newest entry MAY additionally carry an explicit `escalation_state`
    field (`"verified"` or `"unverified"`) — the pass's own claim, which
    this reader HONOURS when it says `"unverified"` even where the one-step
    check above would otherwise pass (the writer may know the chain is
    broken further back than one step), and otherwise leaves the
    mechanically-derived value standing.

    `strikes` counts CONSECUTIVE `attempted` outcomes for THIS counted
    thing alone — a part's count resets only when THAT part discriminates,
    never when its goal or a sibling part reads `measured` on a date the
    part itself was not. `owed` becomes true the first time `strikes`
    reaches 3 without having been cleared since; a later `measured` outcome
    or a rewritten `measure_digest` clears both `strikes` and `owed`.
    Clearing an obligation is NOT a finding event — this module records no
    finding lifecycle at all; that ledger is `standing_by_finding`'s,
    untouched here.

    `reports` is `(date, frontmatter)` pairs; this module performs no I/O.
    """
    dated = sorted(reports, key=lambda pair: pair[0])
    things = list(active_things)

    parsed_by_date: dict[str, tuple[dict[str, dict[str, str]], str]] = {
        date: measured_objectives(frontmatter) for date, frontmatter in dated}

    result: dict[str, dict[str, object]] = {}
    for thing in things:
        if not dated:
            result[thing] = {**_ZERO_ESCALATION, "verified": True}
            continue

        newest_date, _ = dated[-1]
        newest_outcomes, newest_shape = parsed_by_date[newest_date]
        newest_entry = (newest_outcomes.get(thing)
                        if newest_shape == "structured" else None)
        claimed = _carried_claim(newest_entry, thing=thing, date=newest_date)

        if claimed is None:
            result[thing] = _reconstruct_escalation(dated, thing)
            continue

        prev_carried: dict[str, object] | None = None
        prev_digest: str | None = None
        chain_broken_at_prev = False
        if len(dated) >= 2:
            prev_date, _ = dated[-2]
            prev_outcomes, prev_shape = parsed_by_date[prev_date]
            if prev_shape in ("absent", "flat"):
                chain_broken_at_prev = True
            else:
                prev_entry = prev_outcomes.get(thing)
                prev_carried = _carried_claim(prev_entry, thing=thing,
                                              date=prev_date)
                prev_digest = (prev_entry.get("measure_digest")
                              if prev_entry else None)

        if chain_broken_at_prev:
            result[thing] = _reconstruct_escalation(dated, thing)
            continue

        if prev_carried is None:
            # Distinguish a genuine FIRST APPEARANCE — no earlier report names
            # this counted thing at all — from a predecessor that named it but
            # carried no claim, or was simply silent about it on its own date.
            # Only the first may step from zero. The other two are broken links,
            # and `_reconstruct_escalation` is the repair path the rule names;
            # stepping them from zero refused reports that were correct, and
            # made the two paths `_step_escalation` exists to keep in agreement
            # disagree about a silent date.
            seen_before = any(
                thing in measured_objectives(frontmatter)[0]
                for _date, frontmatter in dated[:-1])
            if seen_before:
                result[thing] = _reconstruct_escalation(dated, thing)
                continue
            baseline = dict(_ZERO_ESCALATION)
        else:
            baseline = prev_carried
        outcome = newest_entry.get("outcome", "not-assessed")
        digest = newest_entry.get("measure_digest")
        expected = _step_escalation(baseline, outcome, digest, prev_digest,
                                    newest_date)
        if _is_spend_of(expected, claimed):
            spent_on = newest_entry.get("escalation_spent_on") or ""
            first_owed = expected.get("owed_since")
            if isinstance(first_owed, str) and spent_on < first_owed:
                raise ReviewFindingsError(
                    f"the {newest_date} report's `measured_objectives` entry "
                    f"for {redact(thing)} records the escalation spent on "
                    f"{redact(spent_on)}, before {redact(first_owed)} — the "
                    "date the obligation was first owed; a finding cannot "
                    "discharge an obligation that did not yet exist")
        elif expected != claimed:
            raise ReviewFindingsError(
                f"the {newest_date} report's `measured_objectives` entry for "
                f"{redact(thing)} carries the escalation claim {claimed}, "
                f"which contradicts {expected} — the previous report's "
                "carried value stepped forward by this report's own outcome; "
                "a contradiction is refused rather than resolved")

        declared_state = (newest_entry.get("escalation_state") or "").strip()
        if declared_state and declared_state not in ("verified", "unverified"):
            # A CLOSED SET, like `outcome` and `escalation_owed`. Read
            # permissively, a typo — `unverfied` — read as `verified` and
            # silently moved an unverified obligation out of the
            # ahead-of-every-dated-one slot the rule puts it in.
            raise ReviewFindingsError(
                f"the {newest_date} report's `measured_objectives` entry for "
                f"{redact(thing)} carries `escalation_state` "
                f"{redact(declared_state)}, which is outside the closed set "
                "verified | unverified")
        verified = declared_state != "unverified"
        result[thing] = {**claimed, "verified": verified}

    return result


def _is_spend_of(expected: dict[str, object],
                 claimed: dict[str, object]) -> bool:
    """Does `claimed` record the SPEND of the obligation `expected` owes?

    The one divergence from the stepped-forward value that is not a
    contradiction. A pass that raises the strike-three finding discharges the
    obligation in the same act, so its own entry records `escalation_owed:
    false` with an `escalation_spent_on` date while the one-step check still
    expects `owed: true` — the check reads the PREVIOUS report's state
    stepped forward, and cannot see a finding this pass wrote. The count
    itself is unchanged by a spend, so `strikes` must still match: this
    admits the discharge and nothing else.

    Also covers the cap-deferred case, where the obligation was first owed on
    an earlier date and is spent now: `expected` carries the original
    `owed_since` and `claimed` carries none, because a spent obligation is no
    longer owed. Reconstruction cannot recover spent-ness
    (rule:escalation-history-is-carried-and-bounded), which is why the fact has
    to be CARRIED here rather than derived.
    """
    return (bool(expected.get("owed"))
            and not expected.get("spent")
            and bool(claimed.get("spent"))
            and not claimed.get("owed")
            and claimed.get("owed_since") is None
            and claimed.get("strikes") == expected.get("strikes"))


def _counted_thing_sort_key(thing: str) -> tuple[int, int, int]:
    """Numeric ordering over a counted-thing key: goal number, then
    part-or-not, then part number — a bare `OBJ-N` orders before any part of
    the same objective (rule:escalation-history-is-carried-and-bounded:
    "a part-less goal-level obligation ordering before any part of the same
    objective")."""
    part_match = PART_ID.match(thing)
    if part_match:
        return (int(part_match.group(1)), 1, int(part_match.group(2)))
    return (int(thing[len("OBJ-"):]), 0, 0)


def owed_escalations(state: dict[str, dict[str, object]]) -> list[str]:
    """The counted things carrying an owed escalation, in SPEND order.

    An `unverified` obligation orders ahead of every dated one, because its
    own first-owed date cannot be recovered and may be older than any of
    them. Dated obligations order oldest-owed-first. Ties break by objective
    id then part id, a part-less goal-level obligation ordering before any
    part of the same objective
    (rule:escalation-history-is-carried-and-bounded).
    """
    owed = [thing for thing, entry in state.items() if entry.get("owed")]

    def key(thing: str) -> tuple[int, str, tuple[int, int, int]]:
        entry = state[thing]
        rank = 0 if not entry.get("verified", True) else 1
        since = entry.get("owed_since") or ""
        return (rank, since, _counted_thing_sort_key(thing))

    return sorted(owed, key=key)


def spend_escalations(state: dict[str, dict[str, object]],
                       cap_remaining: int) -> tuple[list[str], list[str]]:
    """Split the owed escalations into `(spend, defer)` against a pass's
    remaining finding budget, oldest/unverified-first
    (rule:escalation-history-is-carried-and-bounded: "An escalation the per-pass
    finding cap delays remains owed rather than lost").

    `cap_remaining` may be zero or negative (nothing left this pass); every
    owed escalation then defers.
    """
    ordered = owed_escalations(state)
    take = max(cap_remaining, 0)
    return ordered[:take], ordered[take:]
