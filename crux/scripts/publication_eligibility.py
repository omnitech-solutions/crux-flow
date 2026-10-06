#!/usr/bin/env python3
"""publication_eligibility.py — may this rule be cited on a surface the release ships?

rule:shipped-citation-requires-an-accepted-decision,
rule:eligibility-is-declared-and-refuses-the-unnamed,
rule:observation-backed-publication-is-denied-for-now.

WHY THIS EXISTS. A rule citation on a shipped surface resolves against the shipped
catalog, and the catalog is projected from this project's own `governs` blocks. The
projection admits a rule as soon as its host record is written: the summaries
projection reads ACTIVE ADRs, and an ADR is active from the moment it is Proposed. So
a proposal nobody has accepted could reach the catalog and ship to a reader as a rule
the plugin says it follows. RESOLUTION IS NOT ADOPTION — the two are different
questions and this module answers only the second.

  * RESOLUTION asks: does this slug name a live rule? `summaries_projection`
    answers it, `lint-governs-references.py` enforces it, and neither is changed by
    anything here.
  * PUBLICATION ELIGIBILITY asks: has the record behind that rule been decided, so
    the plugin may ship the rule to someone who cannot see the record?

Keeping them apart is the point. A citation may resolve and still be ineligible, and
an internal surface — a proposal, a plan, a dated record — may cite a Proposed rule
and keep citing it, because nothing about that citation crosses the release boundary.

THE TABLE IS DECLARED, NEVER DERIVED. `eligibility` reads the source record's KIND
and its RAW lifecycle status and looks both up in `_TABLE` below. A pair the table
does not name is REFUSED. That is the whole fail-closed contract: a new ADR state, a
new record kind, or a record carrying no status at all cannot acquire eligibility by
default, because no default exists. Nothing here normalises a status — the resolver
carries the value the source record wrote, case included, and a reader that
lower-cased before comparing would merge states that differ.

WHAT THIS MODULE IS NOT. It is not a parser: it reads records `summaries_projection`
already built. It is not a status registry: the statuses are the ADR state machine's
and the observation lifecycle's, spelled where they are already spelled. It is not a
linting framework: it is two functions and a dict.

Exit codes belong to the callers, not here. This module raises nothing and prints
nothing; it returns verdicts and finding rows.
"""

from __future__ import annotations

import importlib.util as _ilu
import sys as _sys
from pathlib import Path as _Path

# The repository's one bound-and-redact helper, loaded BY PATH like every other
# consumer of it. Two of the values this module puts into a reason string are
# UNTRUSTED: `source_status` is copied verbatim off a record's frontmatter with no
# enum validation, and `source_kind` is whatever the projection was handed. Both are
# unbounded in length and arbitrary in content, and the payload's consumer is a model
# that renders these rows into a report table, where a decoded control character or a
# line of injected prose is no longer inside JSON quotes.
def _redact():
    path = _Path(__file__).resolve().parent / "untrusted.py"
    spec = _ilu.spec_from_file_location("_pub_elig_untrusted", path)
    module = _ilu.module_from_spec(spec)
    _sys.modules.setdefault("_pub_elig_untrusted", module)
    spec.loader.exec_module(module)
    return module.redact


redact = _redact()

# The most locations one finding reports before it says how many more there are. A
# finding's job is to send a reader to the citation, and one file of nothing but
# citations produced 131,072 distinct locations for a single slug in testing — a
# payload that size is buffered whole by the shell in two of the three enforcement
# points and helps nobody.
MAX_LOCATIONS_PER_FINDING = 20

__all__ = [
    "eligibility",
    "findings",
    "REASON_UNKNOWN",
    "REASON_NO_STATUS",
    "REASON_UNRECOGNISED_KIND",
]

REASON_UNKNOWN = "the citation resolves to no live rule"
REASON_NO_STATUS = "the source record carries no lifecycle status"
REASON_UNRECOGNISED_KIND = "the source record's kind is not one this table decides"

# (source_kind, source_status) -> (eligible, reason). Every pair the projection can
# produce is listed, including the ineligible ones, so a reader sees the refusals as
# decisions rather than as gaps. An unlisted pair is refused by `eligibility`.
#
# THE ROWS ARE THE CONTRACT, and they are the decision's own table rather than a
# convenience here. Do not rewrite this as a denylist (`status != "Proposed"`): that
# spelling admits Deprecated, Superseded, and a missing status by silence, which is the
# one failure shape the declared form exists to prevent.
#
# Deprecated and Superseded are reachable because the status transition and the archival
# are two separate acts: an ADR moved to either state keeps producing records at that
# status until someone archives it. Once archived it contributes no record at all, its
# slug resolves to nothing, and the citation takes the unresolved lane instead.
_TABLE: dict[tuple[str, str], tuple[bool, str]] = {
    ("adr", "Accepted"): (True, ""),
    ("adr", "Proposed"): (
        False, "the source ADR is Proposed; a proposal is not a decision a reader "
        "may receive as a shipped rule"),
    ("adr", "Deprecated"): (
        False, "the source ADR is Deprecated; the decision behind the rule was "
        "withdrawn"),
    ("adr", "Superseded"): (
        False, "the source ADR is Superseded; a later decision replaced it"),
    # An observation DESCRIBES what the code does and decides nothing. `ratified`
    # affirms that the description is accurate; it confers no warrant to publish the
    # description as a rule the plugin follows, and reading it as one would make
    # ratification prescriptive, which the observations contract denies. Whether such
    # a rule may be cited on a shipped surface is an owner question no accepted
    # decision in this tree answers. Refused until one does — fail-closed and
    # reversible by a later decision, and touching neither the record's disposition
    # nor its authority, which stay exactly what the projection derives.
    ("observation", "ratified"): (
        False, "the rule is observation-backed; whether a ratified observation may "
        "be published as a shipped rule is not decided, and an undecided source is "
        "refused rather than assumed eligible"),
}


def eligibility(record: dict | None) -> tuple[bool, str]:
    """`(eligible, reason)` for one live rule record. `reason` is `""` when eligible.

    `record` is a `summaries_projection` record — the same dict `collect_records`
    builds and `build_resolver` reads. `None` means the citation resolved to no live
    rule, which is refused here as well as by the citation lint: the two checks ask
    different questions and an unresolvable citation fails both.

    Refusal is the default for everything the table does not name. A caller never
    gets `True` by omission.
    """
    if record is None:
        return False, REASON_UNKNOWN
    # NO DEFAULT KIND. `record.get("source_kind") or "adr"` would resolve a missing,
    # empty or null kind to the ONE kind that can be eligible — a fail-open default in
    # the module whose whole contract is that nothing is admitted by default. Not
    # reachable through the projection today, which always sets the keyword; latent
    # against a third record kind, which is exactly when a default is most dangerous.
    kind = record.get("source_kind")
    if not isinstance(kind, str) or not kind:
        return False, f"{REASON_UNRECOGNISED_KIND}: the record declares none"
    status = record.get("source_status")
    if not isinstance(status, str) or not status:
        return False, REASON_NO_STATUS
    verdict = _TABLE.get((kind, status))
    if verdict is None:
        if not any(k == kind for k, _ in _TABLE):
            return False, f"{REASON_UNRECOGNISED_KIND}: {redact(kind)}"
        return False, (
            f"the source record's status {redact(status)} is not one this table "
            f"admits for a {redact(kind, quoted=False)} record")
    return verdict


def findings(cited: dict[str, list[str]], by_slug: dict[str, dict]) -> list[dict]:
    """One row per (citing path, ineligible citation), sorted and deterministic.

    `cited` maps a slug to the shipped paths citing it; `by_slug` maps a slug to its
    live record. A slug absent from `by_slug` yields a row per citing path with the
    unknown reason, so a caller that refuses on this list cannot let an unresolvable
    citation through by treating it as somebody else's check.

    Each row names the four things a reader needs to act: WHERE the citation sits,
    WHAT it says, WHICH record it reached and in what state, and WHY that is refused.
    """
    rows: list[dict] = []
    for slug in sorted(cited):
        record = by_slug.get(slug)
        ok, reason = eligibility(record)
        if ok:
            continue
        locations = sorted(set(cited[slug]))
        shown, hidden = locations[:MAX_LOCATIONS_PER_FINDING], \
            max(0, len(locations) - MAX_LOCATIONS_PER_FINDING)
        for path in shown:
            rows.append({
                "path": path,
                "citation": f"rule:{slug}",
                "slug": slug,
                "source": redact((record or {}).get("source_adr"), quoted=False),
                "source_kind": redact((record or {}).get("source_kind"), quoted=False),
                "source_status": redact((record or {}).get("source_status"),
                                         quoted=False),
                "reason": reason,
            })
        if hidden:
            rows.append({
                "path": f"(+{hidden} further location(s) not shown)",
                "citation": f"rule:{slug}",
                "slug": slug,
                "source": redact((record or {}).get("source_adr"), quoted=False),
                "source_kind": redact((record or {}).get("source_kind"), quoted=False),
                "source_status": redact((record or {}).get("source_status"),
                                         quoted=False),
                "reason": reason,
                "truncated": hidden,
            })
    return rows
