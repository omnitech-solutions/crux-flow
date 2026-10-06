"""md_fences.py — the Markdown line and fence subset, in ONE place.

WHAT IS IN HERE. Two things, and they are the same kind of thing: `split_lines`,
which decides where a line ENDS, and `fence_marker` / `closes_fence`, which
decide what a line IS. Both answer a question about the shape of a Markdown
document that Python's own string methods answer differently from CommonMark,
and both were hand-copied into the two readers that ask them. The module is
named for the fences because they came first; the line splitter joined it
because a tenth copy of the split — the one this module was created to stop —
shipped in `check_template_parity.py` while nine sites in `adr-signals.py` were
being converted. One home, one conformance suite, no eleventh copy.

WHY THE FENCE HALF EXISTS AT ALL. The subset lived as two hand-copied functions,
one in `adr-signals.py` and one in `check_template_parity.py`, each carrying
the same six rules and — because they were copies — the same defect. A
differential test between the two copies passed 486 inputs while both were
wrong, which is the property that makes duplication dangerous rather than
merely untidy: a twin comparison can only find a divergence, never a shared
error. So the subset is extracted rather than patched twice, and both callers
are pinned against ONE conformance suite in
`crux/scripts/tests/test_md_fences.py`.

THE DEFECT THE EXTRACTION CLOSED. Both copies bounded the indent on SPACES
and then matched the run after stripping ALL Unicode whitespace:

    if len(expanded) - len(expanded.lstrip(" ")) >= 4:   # spaces only
        return None
    m = _FENCE_RUN.match(line.lstrip())                  # ALL whitespace

`str.lstrip()` with no argument strips every character `str.isspace()` admits
— U+000B, U+000C, U+000D, U+001C-U+001E, U+0085, U+00A0, U+2028, U+2029 and
more — none of which the indent measurement counts. So ONE such character in
front of a run made a four-column indent measure as zero, and the line was
read as a fence marker. CommonMark reads none of those lines as a marker: the
only characters that may precede the run are spaces and tabs, and the run must
begin at the first character that is neither. Measured end to end, a U+2028
before four spaces forged a phantom fence that dropped `friction_citations`
from 2 to 1.

THE SUBSET, ENUMERATED. This is the whole contract; anything not listed here
is not modelled.

  1. FENCE CHARACTER. A marker is a run of backticks or of tildes, and the two
     do not mix: a `~~~` fence is closed only by tildes and a ``` fence only
     by backticks.
  2. RUN LENGTH. Three or more of that character. Two is not a marker.
  3. INDENT BOUND. Zero to three columns of leading indentation, for the
     OPENER and for the CLOSER alike. Four or more columns is an indented code
     block and is never a marker. Unbounded indent was exploitable in both
     directions: a four-space run CLOSED a fence a reader still sees as open,
     and at top level the same run OPENED a phantom fence, after which the next
     real run closed the phantom while opening the reader's — inverting every
     fence state for the rest of the document.
  4. TAB EXPANSION. A tab advances to the next multiple of four, so `\\t` alone
     is four columns and refused, and `  \\t` is likewise four and refused.
     Indentation is measured on the expanded line.
  5. INDENTATION IS SPACES AND TABS AND NOTHING ELSE. After tab expansion the
     run must begin at the first character that is not a space. A line opening
     on any other character — including a character Python calls whitespace,
     such as U+000B, U+00A0 or U+2028 — is not a marker, whatever follows it.
     Rule 5 is the one the two copies got wrong.
  6. INFO STRING. Whatever follows the run on the opener's line. A BACKTICK
     opener whose info string carries a backtick is not a fence, per
     CommonMark; a tilde opener carries no such restriction. A CLOSER carries
     nothing but SPACES AND TABS after its run — the same character class rule
     5 admits before the run, and for the same reason. `str.strip()` with no
     argument was the trailing side of rule 5's defect: it accepts every
     character `str.isspace()` admits, so ```` ``` ```` followed by U+00A0 or
     U+2028 closed a fence CommonMark leaves open. Measured end to end, that
     forged closer inverted every fence state after it and moved `gate_count`
     from the real roster's 2 to a decoy roster's 1 — the same inversion rule
     5 closed on the leading side.
  7. CLOSER RUN LENGTH. A closer's run is at least as long as its opener's, so
     four backticks may wrap three-backtick content.

Deliberately NOT modelled, because no caller needs it: the container-block
context that lets a fence close early at the end of a list item or block
quote, and the leading-indent stripping CommonMark applies to a fenced
block's content lines. Every caller here asks one question of a line — is it
a marker, and does it close the fence I am tracking — and tracks the fence
itself over a flat document.

Stdlib only, and imported by both callers through the `__file__`-derived
`sys.path` insert those files already use for `untrusted`.
"""

from __future__ import annotations

import re

#: A tab advances to the next multiple of this many columns.
TAB_STOP = 4

#: The largest indentation, in columns, at which a run is still a marker.
MAX_INDENT_COLUMNS = 3

#: The shortest run that is a marker.
MIN_RUN = 3

#: The two fence characters. A run mixes neither with the other.
FENCE_CHARS = ("`", "~")

#: A fence-opening or fence-closing RUN, anchored at the first column of the
#: line ALREADY STRIPPED OF ITS LEADING SPACES BY `fence_marker`. It is
#: deliberately not applied to a raw line: the leading-whitespace question is
#: rule 5 above and is answered before this pattern is consulted.
#:
#: BUILT FROM `MIN_RUN` AND `FENCE_CHARS`, never spelled beside them. Both
#: constants used to be declared here and read by nothing — the pattern
#: hardcoded ``` `{3,}|~{3,} ``` — so editing `MIN_RUN` to 4 changed no
#: behaviour and no test. Two constants that read as if they govern and do
#: not are worse than no constants, because the next reader edits one. The
#: derivation is what makes rule 2's `"``"`-is-not-a-run row and rule 1's
#: two-character rows discriminating for them.
_FENCE_RUN = re.compile(
    "^(" + "|".join(f"{re.escape(c)}{{{MIN_RUN},}}" for c in FENCE_CHARS)
    + ")(.*)$"
)


def fence_marker(line: str) -> tuple[str, int, str] | None:
    """`(fence char, run length, info string)` if `line` is a fence marker.

    Returns None when it is not one. Whether a marker OPENS or CLOSES the
    fence a caller is tracking is the caller's decision — `closes_fence`
    answers the closing half; this recognizes the shape.

    Rules 1-6 of the module contract are enforced here. The one step that
    carries rule 5 is that the indent is stripped and the run is matched on
    the SAME string: `expanded.lstrip(" ")` begins at the first character
    that is not a space, so a line opening on U+000B, U+00A0, U+2028 or any
    other non-space character simply fails `_FENCE_RUN`.
    """
    expanded = line.expandtabs(TAB_STOP)
    body = expanded.lstrip(" ")
    if len(expanded) - len(body) > MAX_INDENT_COLUMNS:
        return None
    m = _FENCE_RUN.match(body)
    if not m:
        return None
    run, info = m.group(1), m.group(2)
    char = run[0]
    if char == "`" and "`" in info:
        return None
    return char, len(run), info


def closes_fence(marker: tuple[str, int, str] | None,
                 opener: tuple[str, int] | None) -> bool:
    """Whether `marker` closes the fence `opener` opened.

    `opener` is the `(fence char, run length)` a caller recorded when it saw
    the opening marker; `marker` is `fence_marker`'s verdict on a later line,
    passed straight through so a caller need not test it for None first.

    Rules 1, 6 and 7: the same fence character, a run at least as long as the
    opener's, and nothing but SPACES AND TABS after the run. An opener is
    bounded at three columns and so is this closer — `fence_marker` already
    refused a marker indented past the bound, which is what stops a four-space
    run from closing a fence the reader still sees as open.

    THE TAIL IS STRIPPED ON `" \\t"`, NEVER BARE. `marker[2].strip()` accepted
    every character `str.isspace()` admits, which is the SAME over-wide class
    rule 5 refuses before the run — the leading side was fixed and this
    trailing side was left, so a closing run followed by U+00A0, U+2028 or
    U+000B still closed a fence CommonMark leaves open. Both sides now name
    the same two characters. Tabs cannot in fact survive `fence_marker`'s
    `expandtabs`, so `"\\t"` is redundant today and is kept anyway: it states
    the intended class rather than the class the expansion happens to leave.
    """
    if marker is None or opener is None:
        return False
    return (marker[0] == opener[0]
            and marker[1] >= opener[1]
            and not marker[2].strip(" \t"))


def split_lines(text: str) -> list[str]:
    """`text` split into lines on `"\n"` ALONE, never `str.splitlines()`.

    THE ONE LINE SPLIT for every document either caller reads off disk. Every
    reader used `str.splitlines()`, and `splitlines()` breaks on far more
    than `\n`: U+000B, U+000C, U+000D, U+001C, U+001D, U+001E, U+0085, U+2028
    and U+2029 each end a line for it, and NONE of them ends a line for a
    Markdown or YAML reader. So a single such character inside a sentence
    forged a line that nobody wrote, and every grammar this script anchors at
    the start of a line — `## [YYYY-MM-DD]` entry headings, `Friction:`,
    `## [version] — date` changelog headings, a `|`-opening roster row, a
    frontmatter key, a Markdown heading, a fence run — could be spelled
    mid-sentence and read as real. Measured: one U+2028 inside a fenced
    block's first content line closed that fence early and opened a phantom
    one, which moved `gate_count` from the real roster's 2 to a decoy
    roster's 1.

    Two steps follow the split, and both restore something `splitlines()` was
    doing that a bare `split` is not:

      * ONE TRAILING `\r` PER LINE IS STRIPPED. These are FILE reads, so a
        CRLF document is in scope and a bare split would leave the `\r` on
        every line — which no anchored grammar tolerates. Exactly one is
        removed, so a line that really ends in two carriage returns keeps one.
      * THE TRAILING EMPTY ELEMENT IS DROPPED. `split` leaves one after a
        final `\n` and `splitlines()` returns none, so dropping it makes this
        a drop-in replacement rather than a reader-visible change.

    `adr-signals._git` implements the same discipline over git's stdout and
    deliberately does NOT strip `\r`: its `--format=%s` output is
    `\n`-separated by construction, so a `\r` there is part of a commit
    subject rather than a line ending.

    WHY IT LIVES HERE RATHER THAN IN ONE CALLER. It was written in
    `adr-signals.py` and replaced nine `splitlines()` sites there — and the
    TENTH, in `check_template_parity._section_after`, was left. That site's
    failure mode was worse than the nine: a forged fence before the anchor
    made the section unresolvable, which reports P3 STALE, and P3 does not
    flip the exit code. Measured on a canonical/twin pair whose twin had
    genuinely dropped the governed value, the control exited 1 with a P2
    DRIFT and one U+000B exited 0 with a P3 stale. No attacker is needed:
    a stray character pasted into either AGENTS.md twin silently disables
    the clause and the drift ships. A split copied per caller is a split
    that gets fixed per caller, so it is shared here instead.
    """
    out = [line[:-1] if line.endswith("\r") else line
           for line in text.split("\n")]
    if out and out[-1] == "":
        out.pop()
    return out
