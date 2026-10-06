"""journal_reference.py — does a journal entry reflect on a named artifact?

The decision behind `cleanup-campsite` CLN-JR-1. The rule asks whether a
recent `adr`/`promptbook`/`schema` log op has a matching journal entry; this
module answers the "matching" half and owns no I/O.

WHY IT IS NOT JUST A WIKI-LINK SEARCH. CLN-JR-1 originally accepted one kind
of evidence: a `[[wiki-link]]` naming the artifact. Writing rule 7 tells the
journal to cite `rule:<slug>` where a rule exists, and to name the ADR only
where it carries no `governs` block. An ADR WITH a `governs` block therefore
cannot satisfy both contracts — the journal obeys rule 7, the scan sees no
wiki-link, and a P1 stands for as long as the rule holds. Three reflections
sat in this tree while being reported missing.

MERE SLUG PRESENCE IS NOT EVIDENCE. A `rule:<slug>` token counts only when
the summaries resolver maps that slug to a handle owned by the artifact under
check. A slug the resolver does not carry proves nothing, and a slug owned by
a different ADR is evidence about that other decision. Both are reported as
REJECTED rather than dropped, so a finding can say which decision the entry
actually reflects on.

A RETIRED SLUG STILL POINTS AT A DECISION. `retired_slugs` maps a withdrawn
slug to the handles that replaced it. An entry citing a retired slug is
evidence for the ADR that now owns the successor: that is what retirement
means, and treating it as unresolved would manufacture the same false
positive one rename later.

AN ABSENT RESOLVER IS NOT A FAILURE. `init-docs` creates no summaries
projection, so a fresh tree has none. The slug lane is then UNVERIFIABLE:
tokens are neither evidence nor rejections, `resolver_available` is false,
and the caller can tell "no reflection" from "could not check". The wiki-link
lane still decides on its own.

Fence-aware, using the shared subset in `crux/scripts/md_fences.py`: a
citation quoted inside a fenced block is content, not a citation.

Stdlib only. One production importer, `crux/scripts/check-journal-reference.py`,
which owns every filesystem guard.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from md_fences import closes_fence, fence_marker, split_lines  # noqa: E402

#: A wiki-link target, e.g. `[[adrs/ADR-0111-install-personal-codex-agents]]`.
WIKILINK_RE = re.compile(r"\[\[([^\]\n]+)\]\]")

#: A rule citation token. The slug grammar matches the handle half the
#: summaries projection emits: lowercase ASCII, digits, and hyphens.
RULE_TOKEN_RE = re.compile(r"rule:([a-z0-9][a-z0-9-]*)")


def _handle_owner(handle: str) -> str:
    """`ADR-0112` from `ADR-0112/personal-codex-agents-are-dogfood`."""
    return handle.split("/", 1)[0]


def _names_artifact(target: str, artifact: str) -> bool:
    """Does a wiki-link `target` name `artifact`?

    Compared on the last path segment, with a right boundary so `ADR-0111`
    cannot be satisfied by `ADR-01110-something-else`. A bare id in prose is
    not considered at all: only text inside `[[...]]` reaches here, because
    rule 7 bars an inline ADR number and prose is not a citation.
    """
    segment = target.rsplit("/", 1)[-1].strip()
    return segment == artifact or segment.startswith(artifact + "-")


def citation_lines(text: str) -> list[tuple[int, str]]:
    """Every line of `text` outside a fenced block, as `(1-based line, text)`.

    Fence-aware on the same terms as the journal index, including an opener
    the file never closes, which runs to end of file.
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
        out.append((lineno, line))
    return out


def artifact_references(text: str, artifact: str, resolver: dict | None) -> dict:
    """Whether `text` carries a citation of `artifact`, and on what evidence.

    `resolver` is the parsed `<docs_dir>/adrs/summaries/resolver.json`, or
    None where the tree has no summaries projection.

    Returns a dict carrying `artifact`, `referenced`, `resolver_available`,
    and three lists that never overlap:

      `evidence`     — what makes `referenced` true: a `wikilink`, a
                       `resolved-slug`, or a `retired-slug`.
      `rejected`     — a citation checked and found to be about something
                       else: a `foreign-slug` (with `resolves_to`) or an
                       `unresolved-slug`.
      `unverifiable` — a slug token seen while no resolver was available.
                       Neither evidence nor a rejection, because nothing
                       checked it.
    """
    slugs = (resolver or {}).get("slugs", {}) if isinstance(resolver, dict) else {}
    retired = (resolver or {}).get("retired_slugs", {}) if isinstance(resolver, dict) else {}
    evidence: list[dict] = []
    rejected: list[dict] = []
    unverifiable: list[dict] = []

    for lineno, line in citation_lines(text):
        for target in WIKILINK_RE.findall(line):
            if _names_artifact(target, artifact):
                evidence.append({"kind": "wikilink", "token": target, "line": lineno})
        for slug in RULE_TOKEN_RE.findall(line):
            if resolver is None:
                unverifiable.append({"kind": "unverifiable-slug", "token": slug, "line": lineno})
                continue
            handle = slugs.get(slug)
            if handle is not None:
                if _handle_owner(handle) == artifact:
                    evidence.append({"kind": "resolved-slug", "token": slug, "line": lineno})
                else:
                    rejected.append({"kind": "foreign-slug", "token": slug, "line": lineno,
                                     "resolves_to": _handle_owner(handle)})
                continue
            successors = retired.get(slug)
            if successors:
                owners = [_handle_owner(h) for h in successors]
                if artifact in owners:
                    evidence.append({"kind": "retired-slug", "token": slug, "line": lineno})
                else:
                    rejected.append({"kind": "foreign-slug", "token": slug, "line": lineno,
                                     "resolves_to": ", ".join(sorted(set(owners)))})
                continue
            rejected.append({"kind": "unresolved-slug", "token": slug, "line": lineno})

    return {
        "artifact": artifact,
        "referenced": bool(evidence),
        "resolver_available": resolver is not None,
        "evidence": evidence,
        "rejected": rejected,
        "unverifiable": unverifiable,
    }
