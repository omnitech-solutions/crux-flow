"""Writing rule #7 — cite a decision by a footnote whose definition names the
rule (rule:footnote-definition-names-the-rule).

The AST-scoped check: flag an ADR *number or ADR-file link that renders the
number* in the running prose of a covered surface, while skipping the exempt
token classes (fenced + inline code, link destinations, HTML, YAML frontmatter,
and Markdown blockquotes). The footnote-definition line is NOT exempt: it is
checked the way running text is, because the definition names the rule and
carries no ADR number. The footnote marker in the prose is free text and never
matches. A raw grep is deliberately NOT used — the exemptions require
structural parsing.

`compliant`: `…the seven rules.[^rules]` with
`[^rules]: rule:footnote-definition-names-the-rule` at the document foot.

The `CHK` guard is a ratchet: `new_violations` flags only findings absent from a
baseline of un-migrated surfaces, so the guard is live at acceptance without
blocking PRs during the incremental migration.
"""

from __future__ import annotations

import re

# an ADR number in the running prose: bare token OR the visible text of a
# wiki-link / inline link that renders it. Footnote markers `[^adr-0058]` do NOT
# match (they carry no `ADR-NNNN`), so the compliant form passes.
_ADR = re.compile(r"ADR-\d{4}")
_INLINE_CODE = re.compile(r"`[^`]*`")
# [text](destination) — the destination is exempt; keep only the visible text.
_LINK_DEST = re.compile(r"(\[[^\]]*\])\([^)]*\)")


def _strip_exempt_spans(line: str) -> str:
    line = _INLINE_CODE.sub("", line)          # inline code
    line = _LINK_DEST.sub(r"\1", line)          # link destinations → keep text
    return line


def find_inline_adr_refs(md_text: str) -> list[tuple[int, str]]:
    """Return [(lineno, 'ADR-NNNN'), …] for every rule-#7 violation in prose."""
    findings: list[tuple[int, str]] = []
    in_frontmatter = False
    in_fence = False
    lines = md_text.splitlines()
    for i, raw in enumerate(lines, 1):
        stripped = raw.lstrip()
        # YAML frontmatter (leading --- … ---)
        if i == 1 and stripped == "---":
            in_frontmatter = True
            continue
        if in_frontmatter:
            if stripped == "---":
                in_frontmatter = False
            continue
        # fenced code
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        # exempt line classes: blockquote, HTML block/comment. A footnote
        # definition is deliberately NOT here: it is checked like running text.
        if stripped.startswith(">"):
            continue
        if stripped.startswith("<"):                 # HTML block / comment
            continue
        line = _strip_exempt_spans(raw)
        for m in _ADR.finditer(line):
            findings.append((i, m.group()))
    return findings


def _key(v: tuple[int, str]) -> str:
    return f"{v[0]}:{v[1]}"


def new_violations(md_text: str, baseline: set[str] | None = None) -> list[tuple[int, str]]:
    """Ratchet: findings NOT in the baseline (un-migrated pre-existing violations)."""
    baseline = baseline or set()
    return [v for v in find_inline_adr_refs(md_text) if _key(v) not in baseline]


def baseline_for(md_text: str) -> set[str]:
    """The set of currently-present violations, to seed a surface's ratchet baseline."""
    return {_key(v) for v in find_inline_adr_refs(md_text)}
