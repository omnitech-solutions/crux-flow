#!/usr/bin/env python3
"""facts_matcher.py — the cache-free Swift fact grammar and matcher.

ADR-0129 clause 9(c): "A committed, cache-free matcher checks every fact of
every facts file a `corpus.yml` entry names, against that entry's committed
goldens, and never skips a fact." This module is that matcher, plus the
one-parser-per-kind grammar clause 9(c) requires.

Two public entry points:

  * `parse_fact(raw: dict) -> Fact` — parses one fact record's `value` under
    its `kind`'s grammar (ADR-0129 clause 5's fact-kind-to-row map, plus the
    two shape discriminators for `public-symbol` and `package-dependency`).
    Raises `FactParseError` on anything it cannot parse; it never returns a
    best-effort guess.
  * `match(facts, golden: dict[str, str]) -> list[dict]` — checks every fact
    against `golden` (a concern name -> rendered markdown mapping) per the
    three ADR-0129 clause 9(c) statuses. Returns one problem dict per fact
    that fails its check; an empty list means every fact was satisfied.

This module reads no cache and clones nothing: `golden` is caller-supplied
markdown (a real spine file's text, or a synthetic fixture in a test). The
one exception is the `golden_for` convenience helper, which reads the four
committed spine files for a named corpus entry through
`derive_corpus.golden_path` — still no network, no clone, no derive.

The rows this matcher reads are the ones the Swift pack renders
(`crux/scripts/crux/arch/packs/swift.py`). A name, path or location cell is a
code span; a `declared at` cell is `path:a-b`, where `a` is the declaration's
first line and `b` the line of its name; a graph edge's labels read
`name (container)`; and a residual bullet reads
``- `<class>` `<path>` lines <a-b>[, <a-b>]* — <detail>``, or `lines —` for a
file that was not read.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent


class FactParseError(ValueError):
    """A fact's `value` does not parse under its `kind`'s grammar.

    Raised rather than returning a partial or guessed result: ADR-0129
    clause 9(c) states "an unparseable fact fails", never skips.
    """


# ---------------------------------------------------------------------------
# Evidence: "path:a-b", path may contain spaces so the split is on the LAST
# colon (the render contract's `declared at` / `file` cells share this shape).
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Evidence:
    path: str
    start: int
    end: int


def _parse_evidence(raw: str) -> Evidence:
    idx = raw.rfind(":")
    if idx <= 0:
        raise FactParseError(f"evidence has no `path:range` separator: {raw!r}")
    path, rng = raw[:idx], raw[idx + 1:]
    m = re.match(r"\A(\d+)-(\d+)\Z", rng)
    if not m or not path:
        raise FactParseError(f"evidence range is not `a-b`: {raw!r}")
    return Evidence(path=path, start=int(m.group(1)), end=int(m.group(2)))


def _overlaps(a: Evidence, b: Evidence) -> bool:
    return a.path == b.path and a.start <= b.end and b.start <= a.end


# ---------------------------------------------------------------------------
# The parsed fact
# ---------------------------------------------------------------------------

#: What table/graph shape a fact's kind (plus its shape discriminator, for
#: `public-symbol` and `package-dependency`) renders as. Matches ADR-0129
#: clause 5's map: "type", "stored-property", "interface", "product",
#: "requirement", "main", "target", "edge", "dependency", "import",
#: "membership", "residual".
@dataclass(frozen=True)
class Fact:
    concern: str
    kind: str
    status: str
    value: str
    evidence: Evidence
    row_kind: str
    key: str | None = None
    edge: tuple[str, str] | None = None
    parts: tuple | None = None
    product_kind: str | None = None


#: Entity kinds: a real declaration/edge/row the pack renders (or doesn't).
#: `out-of-scope` on one of these asserts "no row of that kind with that key
#: exists". The complement, `RESIDUAL_KINDS`, asserts nothing when
#: out-of-scope.
ENTITY_KINDS = frozenset({
    "type", "stored-property", "public-symbol", "protocol-requirement",
    "entry-declaration", "target", "target-dependency", "package-dependency",
    "import", "source-membership",
})
RESIDUAL_KINDS = frozenset({"residual", "limitation"})
KNOWN_KINDS = ENTITY_KINDS | RESIDUAL_KINDS
KNOWN_STATUSES = frozenset({"must-render", "limitation-expected", "out-of-scope"})


# ---------------------------------------------------------------------------
# Shared value-grammar helpers
# ---------------------------------------------------------------------------

def _strip_trailing_paren(value: str) -> tuple[str, str | None]:
    """Strip ONE trailing, balanced `(...)` group. Returns `(body, paren)`.

    `paren` is `None` when `value` does not end in a balanced parenthetical.
    Balanced (depth-counted) rather than a `[^()]*` regex, because a real
    fact's trailing parenthetical can itself contain a nested call, e.g.
    "ParsableCommand.configuration (static var; ... CommandConfiguration())".
    """
    v = value.rstrip()
    if not v.endswith(")"):
        return value, None
    depth = 0
    i = len(v) - 1
    while i >= 0:
        c = v[i]
        if c == ")":
            depth += 1
        elif c == "(":
            depth -= 1
            if depth == 0:
                break
        i -= 1
    else:
        return value, None
    if i < 0:
        return value, None
    body = v[:i].rstrip()
    paren = v[i + 1:-1]
    if not body:
        return value, None
    return body, paren


def _out_of_scope_key(value: str) -> str:
    """The out-of-scope grammar: the name before `:` or ` (`, whichever is
    first — applies regardless of the fact's `kind` (ADR-0129 clause 9(c))."""
    colon = value.find(":")
    paren = value.find(" (")
    candidates = [i for i in (colon, paren) if i >= 0]
    if not candidates:
        return value.strip()
    return value[:min(candidates)].strip()


# ---------------------------------------------------------------------------
# One parser per kind
# ---------------------------------------------------------------------------

def _parse_type(value: str, concern: str) -> tuple[str, str | None, tuple | None]:
    body, paren = _strip_trailing_paren(value)
    if paren is None:
        raise FactParseError(f"type value has no `(kind, access…)`: {value!r}")
    return "type", body, None


def _parse_stored_property(value: str, concern: str) -> tuple[str, str | None, tuple | None]:
    if ":" not in value:
        raise FactParseError(f"stored-property value has no `: Type`: {value!r}")
    head, _rest = value.split(":", 1)
    head = head.strip()
    if "." not in head:
        raise FactParseError(f"stored-property value has no `Owner.name`: {value!r}")
    owner, prop = head.rsplit(".", 1)
    if not owner or not prop:
        raise FactParseError(f"stored-property value has an empty owner or name: {value!r}")
    return "stored-property", f"{owner}.{prop}", (owner, prop)


_PRODUCT_KIND_RE = re.compile(r"\A(?:library|executable|command plugin|build-tool plugin|plugin)\Z")
_PROVIDES_RE = re.compile(
    r"\A(?P<container>.+) provides "
    r"(?P<kind>library|executable|command plugin|build-tool plugin|plugin) "
    r"product (?P<name>.+)\Z"
)


def _parse_public_symbol(value: str, concern: str) -> tuple[str, str | None, tuple | None]:
    body, paren = _strip_trailing_paren(value)
    if paren is not None and (paren == "library product" or paren.endswith(" plugin product")):
        product_kind = paren[: -len(" product")]
        if not _PRODUCT_KIND_RE.match(product_kind):
            raise FactParseError(f"public-symbol product kind unrecognised: {value!r}")
        return "product", body, ("product_kind", product_kind)
    m = _PROVIDES_RE.match(value)
    if m:
        return "product", m.group("name"), ("product_kind", m.group("kind"))
    if paren is None:
        raise FactParseError(f"public-symbol value has no shape this grammar recognises: {value!r}")
    ibody = body
    if ibody.endswith(" throws"):
        ibody = ibody[: -len(" throws")]
    if not ibody:
        raise FactParseError(f"public-symbol interface value is empty after stripping: {value!r}")
    return "interface", ibody, None


_REQUIREMENT_TYPE_GET_RE = re.compile(r"\A(?P<head>.+?)\s*:\s*.+\{\s*get(?:\s+set)?\s*\}\Z")


def _parse_protocol_requirement(value: str, concern: str) -> tuple[str, str | None, tuple | None]:
    body, paren = _strip_trailing_paren(value)
    working = body if paren is not None else value
    m = _REQUIREMENT_TYPE_GET_RE.match(working)
    if m:
        working = m.group("head")
    if "." not in working:
        raise FactParseError(f"protocol-requirement value has no `Protocol.member`: {value!r}")
    protocol, member = working.split(".", 1)
    protocol, member = protocol.strip(), member.strip()
    if not protocol or not member:
        raise FactParseError(f"protocol-requirement value has an empty side: {value!r}")
    return "requirement", f"{protocol}.{member}", (protocol, member)


#: The last comma component of an `entry-declaration` value's parenthetical
#: carries the owning target when it ends `target <Name>` (ADR-0130 clause
#: 16). "target/product" (no space after "target") never matches.
_ENTRY_TARGET_RE = re.compile(r"\btarget (?P<name>.+)\Z")


def _parse_entry_declaration(value: str, concern: str) -> tuple[str, str | None, tuple | None]:
    body, paren = _strip_trailing_paren(value)
    if paren is None:
        raise FactParseError(f"entry-declaration value has no `(…)`: {value!r}")
    target = None
    components = paren.split(",")
    if components:
        m = _ENTRY_TARGET_RE.search(components[-1].strip())
        if m:
            target = m.group("name").strip()
    return "main", body, (target,)


def _parse_target(value: str, concern: str) -> tuple[str, str | None, tuple | None]:
    body, paren = _strip_trailing_paren(value)
    if paren is None:
        raise FactParseError(f"target value has no `(… target…)`: {value!r}")
    # A local-package target is written `<container directory>: <Name> (…)`.
    # The container sits in the evidence path already, so the key is the name.
    if ": " in body:
        body = body.rsplit(": ", 1)[1]
    return "target", body, None


def _parse_target_dependency(value: str) -> tuple[str, str, tuple[str, str] | None]:
    parts = value.split(" -> ")
    if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
        raise FactParseError(f"target-dependency value is not `A -> B`: {value!r}")
    a, b = parts[0].strip(), parts[1].strip()
    return "edge", None, (a, b)


def _parse_package_dependency(value: str) -> tuple[str, str | None, tuple[str, str] | None]:
    body, paren = _strip_trailing_paren(value)
    if paren is not None and paren.startswith("local package "):
        parts = body.split(" -> ")
        if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
            raise FactParseError(f"package-dependency local-edge shape malformed: {value!r}")
        t, p = parts[0].strip(), parts[1].strip()
        return "edge", None, (t, p)
    if paren == "path dependency":
        m = re.match(r"\A(?P<c>.+) -> local package (?P<path>.+)\Z", body)
        if not m:
            raise FactParseError(f"package-dependency path-dependency shape malformed: {value!r}")
        c, path = m.group("c").strip(), m.group("path").strip()
        return "dependency", f"{c}→{path}", None
    if paren is not None and paren.startswith("remote package "):
        parts = body.split(" -> ")
        if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
            raise FactParseError(f"package-dependency target-remote shape malformed: {value!r}")
        t, p = parts[0].strip(), parts[1].strip()
        return "dependency", f"{t}→{p}", None
    if value.startswith("project references remote package "):
        rest = value[len("project references remote package "):]
        loc, _sep, _req = rest.partition(" (")
        loc = loc.strip()
        if not loc:
            raise FactParseError(f"package-dependency project-remote shape malformed: {value!r}")
        return "dependency", f"project→{loc}", None
    if " -> remote package " in value:
        c, rest = value.split(" -> remote package ", 1)
        loc = re.split(r" from | exact ", rest, maxsplit=1)[0].strip()
        c = c.strip()
        if not c or not loc:
            raise FactParseError(f"package-dependency container-remote shape malformed: {value!r}")
        return "dependency", f"{c}→{loc}", None
    raise FactParseError(f"package-dependency value matches no known shape: {value!r}")


_IMPORT_RE = re.compile(r"\A(?P<who>.+) imports (?P<module>.+) \((?P<kind>.+) import\)\Z")


def _parse_import(value: str) -> tuple[str, str | None, tuple | None]:
    m = _IMPORT_RE.match(value)
    if not m:
        raise FactParseError(f"import value is not `X imports Y (kind import)`: {value!r}")
    return "import", None, (m.group("who").strip(), m.group("module").strip(), m.group("kind").strip())


_MEMBERSHIP_EXCLUDED_RE = re.compile(
    r"\A(?P<file>.+) excluded from (?P<target>.+) by an exception set\Z")
_MEMBERSHIP_IN_RE = re.compile(r"\A(?P<file>.+) in (?P<target>.+) \((?P<route>.+)\)\Z")


def _parse_source_membership(value: str) -> tuple[str, str | None, tuple | None]:
    m = _MEMBERSHIP_EXCLUDED_RE.match(value)
    if m:
        # The excluded form maps to the rendered route wording verbatim
        # (ADR-0130 render contract's four-value `route` grammar), so the
        # matcher checks it byte for byte against the rendered column.
        return "membership", None, (
            m.group("file").strip(), m.group("target").strip(), "excluded by an exception set")
    m = _MEMBERSHIP_IN_RE.match(value)
    if m:
        return "membership", None, (m.group("file").strip(), m.group("target").strip(), m.group("route").strip())
    raise FactParseError(f"source-membership value matches no known form: {value!r}")


def parse_fact(raw: dict) -> Fact:
    """Parse one fact record (a `dict` shaped like a `facts:` list entry).

    Dispatches first on `status`: `out-of-scope` uses the generic "name
    before `:` or ` (`" grammar regardless of `kind` (ADR-0129 clause 9(c)
    reads this the same way for every kind). Everything else dispatches on
    `kind`. Raises `FactParseError` on anything unrecognised — never skips.
    """
    kind = raw.get("kind")
    status = raw.get("status")
    value = raw.get("value")
    concern = raw.get("concern")
    evidence_raw = raw.get("evidence")

    if kind not in KNOWN_KINDS:
        raise FactParseError(f"unknown fact kind: {kind!r}")
    if status not in KNOWN_STATUSES:
        raise FactParseError(f"unknown fact status: {status!r}")
    if not isinstance(value, str) or not value:
        raise FactParseError(f"fact value is not a non-empty string: {value!r}")
    if not isinstance(evidence_raw, str) or not evidence_raw:
        raise FactParseError(f"fact evidence is not a non-empty string: {evidence_raw!r}")

    evidence = _parse_evidence(evidence_raw)

    if status == "out-of-scope":
        key = _out_of_scope_key(value)
        row_kind = "residual" if kind in RESIDUAL_KINDS else _ENTITY_ROW_KIND.get(kind, kind)
        if row_kind in _NEEDS_STRUCTURE:
            # The generic grammar yields a name; these rows are checked by an
            # edge or a tuple. Without one the check would pass or crash, so
            # the fact fails as unparseable (clause 9(c): nothing is skipped).
            raise FactParseError(
                f"out-of-scope has no check for kind {kind!r}: its row is matched "
                f"by structure, not by name: {value!r}")
        fact = Fact(concern=concern, kind=kind, status=status, value=value,
                    evidence=evidence, row_kind=row_kind, key=key)
        _require_named_concern(fact)
        return fact

    if kind in RESIDUAL_KINDS:
        # "limitation/residual: evidence only" — no key is derived; the
        # matcher checks coverage by evidence path/range alone.
        return Fact(concern=concern, kind=kind, status=status, value=value,
                    evidence=evidence, row_kind="residual", key=None)

    if kind == "type":
        row_kind, key, parts = _parse_type(value, concern)
    elif kind == "stored-property":
        row_kind, key, parts = _parse_stored_property(value, concern)
    elif kind == "public-symbol":
        row_kind, key, extra = _parse_public_symbol(value, concern)
        parts = None
        product_kind = extra[1] if extra else None
        fact = Fact(concern=concern, kind=kind, status=status, value=value,
                    evidence=evidence, row_kind=row_kind, key=key,
                    product_kind=product_kind)
        _require_named_concern(fact)
        return fact
    elif kind == "protocol-requirement":
        row_kind, key, parts = _parse_protocol_requirement(value, concern)
    elif kind == "entry-declaration":
        row_kind, key, parts = _parse_entry_declaration(value, concern)
    elif kind == "target":
        row_kind, key, parts = _parse_target(value, concern)
    elif kind == "target-dependency":
        row_kind, key, edge = _parse_target_dependency(value)
        fact = Fact(concern=concern, kind=kind, status=status, value=value,
                    evidence=evidence, row_kind=row_kind, key=key, edge=edge)
        _require_named_concern(fact)
        return fact
    elif kind == "package-dependency":
        row_kind, key, edge = _parse_package_dependency(value)
        fact = Fact(concern=concern, kind=kind, status=status, value=value,
                    evidence=evidence, row_kind=row_kind, key=key, edge=edge)
        _require_named_concern(fact)
        return fact
    elif kind == "import":
        row_kind, key, parts = _parse_import(value)
    elif kind == "source-membership":
        row_kind, key, parts = _parse_source_membership(value)
    else:  # pragma: no cover - guarded by the KNOWN_KINDS check above
        raise FactParseError(f"unhandled fact kind: {kind!r}")

    fact = Fact(concern=concern, kind=kind, status=status, value=value,
                evidence=evidence, row_kind=row_kind, key=key, parts=parts)
    _require_named_concern(fact)
    return fact


#: The concern each entity row renders in (ADR-0129 clause 5's map). A fact
#: must name the same concern, because `_row_matches` looks only there.
_ROW_KIND_CONCERN = {
    "type": "data-model", "stored-property": "data-model",
    "interface": "api-surface", "product": "api-surface",
    "requirement": "api-surface", "main": "api-surface",
    "target": "module-graph", "edge": "module-graph", "dependency": "module-graph",
    "import": "module-graph", "membership": "module-graph",
}

#: Row kinds matched by an edge or a tuple rather than by a name.
_NEEDS_STRUCTURE = frozenset({"edge", "requirement", "import", "membership"})


def _require_named_concern(fact: "Fact") -> None:
    """Fail an entity fact whose named concern is not where its row renders."""
    expected = _ROW_KIND_CONCERN.get(fact.row_kind)
    if expected is not None and fact.concern != expected:
        raise FactParseError(
            f"a {fact.kind!r} fact renders in {expected}, but names {fact.concern!r}: "
            f"{fact.value!r}")


#: `row_kind` an entity kind resolves to for an `out-of-scope` fact whose
#: value never reaches its normal shape-sensitive parser (the out-of-scope
#: grammar is generic, so the row_kind used to check "no row of that kind"
#: is the kind's DEFAULT row shape — the shape discriminators for
#: `public-symbol`/`package-dependency` never apply to an out-of-scope value,
#: since it never rendered in the first place).
_ENTITY_ROW_KIND = {
    "type": "type",
    "stored-property": "stored-property",
    "public-symbol": "interface",
    "protocol-requirement": "requirement",
    "entry-declaration": "main",
    "target": "target",
    "target-dependency": "edge",
    "package-dependency": "edge",
    "import": "import",
    "source-membership": "membership",
}


# ---------------------------------------------------------------------------
# Markdown reading: tables, the mermaid fence, and residual bullets
# ---------------------------------------------------------------------------

def _cell_strip(cell: str) -> str:
    cell = cell.strip()
    if len(cell) >= 2 and cell[0] == "`" and cell[-1] == "`":
        return cell[1:-1]
    return cell


def _extract_table(markdown: str, heading: str) -> list[dict]:
    """Rows of the pipe table directly under `## <heading>`.

    Stops at the next `## ` heading. Returns `[]` when the heading is
    absent — the render contract's "rendered only when at least one row
    exists" sections (e.g. `## Membership`) are simply not present.
    """
    rows: list[dict] = []
    header: list[str] | None = None
    in_section = False
    for line in markdown.splitlines():
        if line.strip() == heading:
            in_section = True
            header = None
            continue
        if not in_section:
            continue
        if line.startswith("## ") and line.strip() != heading:
            break
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if header is None:
            header = [c.lower() for c in cells]
            continue
        if all(re.fullmatch(r":?-+:?", c) for c in cells):
            continue
        row = {header[j]: _cell_strip(cells[j]) for j in range(min(len(header), len(cells)))}
        rows.append(row)
    return rows


def _parse_location_cell(cell: str) -> Evidence | None:
    if not cell or cell == "—":
        return None
    idx = cell.rfind(":")
    if idx <= 0:
        return None
    path, rng = cell[:idx], cell[idx + 1:]
    m = re.match(r"\A(\d+)-(\d+)\Z", rng)
    if not m:
        return None
    return Evidence(path=path, start=int(m.group(1)), end=int(m.group(2)))


_FENCE_EDGE_RE = re.compile(r'^\s*\S+\["(?P<a>.*)"\]\s*-->\s*\S+\["(?P<b>.*)"\]\s*$')


def _extract_graph_edges(markdown: str) -> list[tuple[str, str]]:
    edges: list[tuple[str, str]] = []
    in_fence = False
    started = False
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped == "```mermaid":
            in_fence = True
            started = False
            continue
        if in_fence and stripped == "```":
            in_fence = False
            continue
        if not in_fence:
            continue
        if stripped == "graph LR":
            started = True
            continue
        if not started:
            continue
        m = _FENCE_EDGE_RE.match(line)
        if m:
            edges.append((_strip_container_suffix(m.group("a")), _strip_container_suffix(m.group("b"))))
    return edges


def _strip_container_suffix(label: str) -> str:
    body, paren = _strip_trailing_paren(label)
    return body if paren is not None else label


_RESIDUAL_LINE_RE = re.compile(
    r"^- `(?P<cls>[^`]+)` `(?P<path>[^`]+)` lines (?P<ranges>.+?) — (?P<detail>.*)$"
)


def _extract_residuals(markdown: str) -> list[dict]:
    residuals: list[dict] = []
    in_section = False
    for line in markdown.splitlines():
        if line.strip() == "## Residuals":
            in_section = True
            continue
        if not in_section:
            continue
        if line.startswith("## "):
            break
        m = _RESIDUAL_LINE_RE.match(line.strip())
        if not m:
            continue
        ranges_raw = m.group("ranges").strip()
        ranges: list[tuple[int, int]] = []
        if ranges_raw != "—":
            for part in ranges_raw.split(","):
                rm = re.match(r"\A(\d+)-(\d+)\Z", part.strip())
                if rm:
                    ranges.append((int(rm.group(1)), int(rm.group(2))))
        residuals.append({"cls": m.group("cls"), "path": m.group("path"), "ranges": ranges})
    return residuals


def _residual_covers(markdown: str, evidence: Evidence) -> bool:
    for r in _extract_residuals(markdown):
        if r["path"] != evidence.path:
            continue
        for a, b in r["ranges"]:
            if a <= evidence.start and evidence.end <= b:
                return True
    return False


def _normalize_selector(name: str) -> str:
    return re.sub(r"\(.*\)\Z", "", name.strip()).strip()


# ---------------------------------------------------------------------------
# Row lookups, must-render (location-checked) and existence-only (out-of-scope)
# ---------------------------------------------------------------------------

def _simple_row_match(golden: dict, concern: str, heading: str, key_col: str,
                       key: str, evidence: Evidence | None) -> bool:
    for row in _extract_table(golden.get(concern, ""), heading):
        if row.get(key_col, "") != key:
            continue
        if evidence is None:
            return True
        loc = _parse_location_cell(row.get("declared at", ""))
        if loc is not None and _overlaps(loc, evidence):
            return True
    return False


def _stored_property_match(golden: dict, key: str, evidence: Evidence | None) -> bool:
    for row in _extract_table(golden.get("data-model", ""), "## Stored properties"):
        row_key = f"{row.get('owner', '')}.{row.get('property', '')}"
        if row_key != key:
            continue
        if evidence is None:
            return True
        loc = _parse_location_cell(row.get("declared at", ""))
        if loc is not None and _overlaps(loc, evidence):
            return True
    return False


def _requirement_match(golden: dict, parts: tuple, evidence: Evidence | None) -> bool:
    protocol, member = parts
    norm_member = _normalize_selector(member)
    for row in _extract_table(golden.get("api-surface", ""), "## Protocol requirements"):
        if row.get("protocol", "") != protocol:
            continue
        if _normalize_selector(row.get("requirement", "")) != norm_member:
            continue
        if evidence is None:
            return True
        loc = _parse_location_cell(row.get("declared at", ""))
        if loc is not None and _overlaps(loc, evidence):
            return True
    return False


def _dependency_match(golden: dict, key: str, evidence: Evidence | None) -> bool:
    for row in _extract_table(golden.get("module-graph", ""), "## Dependencies"):
        row_key = f"{row.get('from', '')}→{row.get('dependency', '')}"
        if row_key != key:
            continue
        if evidence is None:
            return True
        loc = _parse_location_cell(row.get("declared at", ""))
        if loc is not None and _overlaps(loc, evidence):
            return True
    return False


def _import_match(golden: dict, parts: tuple, evidence: Evidence | None) -> bool:
    _who, module, kind = parts
    for row in _extract_table(golden.get("module-graph", ""), "## Imports"):
        if row.get("module", "") != module or row.get("import kind", "") != kind:
            continue
        if evidence is None:
            return True
        loc = _parse_location_cell(row.get("file", ""))
        if loc is not None and _overlaps(loc, evidence):
            return True
    return False


def _membership_match(golden: dict, parts: tuple, evidence: Evidence | None) -> bool:
    # A membership fact holds only on the right file, target and route
    # (ADR-0130 render contract's `| file | target | route | ... |` shape) —
    # matching on the file alone would let a wrong target or route pass.
    file_, target, route = parts
    for row in _extract_table(golden.get("module-graph", ""), "## Membership"):
        if row.get("file", "") != file_:
            continue
        if row.get("target", "") != target:
            continue
        if row.get("route", "") != route:
            continue
        if evidence is None:
            return True
        loc = _parse_location_cell(row.get("declared at", ""))
        if loc is not None and _overlaps(loc, evidence):
            return True
    return False


_OWNER_CELL_RE = re.compile(r"\A(?P<name>.+) \((?P<container>[^()]*)\)\Z")


def _owner_names(cell: str) -> frozenset[str]:
    """The name portion of every `Name (container)` owner in a rendered
    `@main`/import owning-target cell — sorted, comma-joined, one code span
    (ADR-0130 render contract). `—` (no owner) yields the empty set."""
    if not cell or cell == "—":
        return frozenset()
    names = set()
    for part in cell.split(", "):
        m = _OWNER_CELL_RE.match(part.strip())
        if m:
            names.add(m.group("name").strip())
    return frozenset(names)


def _main_match(golden: dict, key: str, target: str | None, evidence: Evidence | None) -> bool:
    # An `entry-declaration` fact keys on name, file and target (ADR-0130
    # clause 16). When the fact carries no target (e.g. a package-only
    # entry), the row is matched by name alone, as before.
    for row in _extract_table(golden.get("api-surface", ""), "## @main declarations"):
        if row.get("@main type", "") != key:
            continue
        if target is not None and target not in _owner_names(row.get("owning target", "")):
            continue
        if evidence is None:
            return True
        loc = _parse_location_cell(row.get("declared at", ""))
        if loc is not None and _overlaps(loc, evidence):
            return True
    return False


def _row_matches(fact: Fact, golden: dict, *, require_location: bool) -> bool:
    evidence = fact.evidence if require_location else None
    rk = fact.row_kind
    if rk == "edge":
        return fact.edge in _extract_graph_edges(golden.get("module-graph", ""))
    if rk == "type":
        return _simple_row_match(golden, "data-model", "## Types", "type", fact.key, evidence)
    if rk == "stored-property":
        return _stored_property_match(golden, fact.key, evidence)
    if rk == "interface":
        return _simple_row_match(golden, "api-surface", "## Interfaces", "interface", fact.key, evidence)
    if rk == "product":
        return _simple_row_match(golden, "api-surface", "## Products", "product", fact.key, evidence)
    if rk == "requirement":
        return _requirement_match(golden, fact.parts, evidence)
    if rk == "main":
        target = fact.parts[0] if fact.parts else None
        return _main_match(golden, fact.key, target, evidence)
    if rk == "target":
        return _simple_row_match(golden, "module-graph", "## Targets", "target", fact.key, evidence)
    if rk == "dependency":
        return _dependency_match(golden, fact.key, evidence)
    if rk == "import":
        return _import_match(golden, fact.parts, evidence)
    if rk == "membership":
        return _membership_match(golden, fact.parts, evidence)
    raise FactParseError(f"no row lookup defined for row_kind {rk!r}")  # pragma: no cover


# ---------------------------------------------------------------------------
# The three ADR-0129 clause 9(c) statuses
# ---------------------------------------------------------------------------

def _check_must_render(fact: Fact, golden: dict) -> bool:
    return _row_matches(fact, golden, require_location=True)


def _check_limitation_expected(fact: Fact, golden: dict) -> bool:
    return _residual_covers(golden.get(fact.concern, ""), fact.evidence)


def _check_out_of_scope(fact: Fact, golden: dict) -> bool:
    if fact.kind in RESIDUAL_KINDS:
        return True  # "on a residual or limitation kind, it asserts nothing"
    return not _row_matches(fact, golden, require_location=False)


def _check_one(fact: Fact, golden: dict) -> tuple[bool, str | None]:
    if fact.status == "must-render":
        ok = _check_must_render(fact, golden)
        return ok, None if ok else "must-render fact does not appear as its row/edge"
    if fact.status == "limitation-expected":
        ok = _check_limitation_expected(fact, golden)
        return ok, None if ok else "no covering residual/limitation bullet in the named concern"
    if fact.status == "out-of-scope":
        ok = _check_out_of_scope(fact, golden)
        return ok, None if ok else "out-of-scope fact's key still renders as an entity row"
    raise FactParseError(f"unknown fact status: {fact.status!r}")  # pragma: no cover


def match(facts, golden: dict) -> list[dict]:
    """Check every fact of `facts` against `golden`.

    `facts` is a list of raw fact dicts (as read from an expectations YAML
    `facts:` list), or a `{"facts": [...]}` mapping (the whole parsed
    document). `golden` maps concern name ("data-model", "api-surface",
    "module-graph") to that concern's rendered markdown text.

    Returns one problem dict per fact that fails its check. Never skips a
    fact: an unparseable value's `FactParseError` propagates rather than
    being swallowed, matching ADR-0129 clause 9(c)'s "an unparseable fact
    fails".
    """
    fact_list = facts["facts"] if isinstance(facts, dict) and "facts" in facts else facts
    problems: list[dict] = []
    for raw in fact_list:
        fact = raw if isinstance(raw, Fact) else parse_fact(raw)
        raw_evidence = raw.get("evidence") if isinstance(raw, dict) else \
            f"{fact.evidence.path}:{fact.evidence.start}-{fact.evidence.end}"
        ok, reason = _check_one(fact, golden)
        if not ok:
            problems.append({
                "concern": fact.concern, "kind": fact.kind, "status": fact.status,
                "value": fact.value, "evidence": raw_evidence, "reason": reason,
            })
    return problems


# ---------------------------------------------------------------------------
# golden_for — reads a corpus entry's committed spine files, no derive
# ---------------------------------------------------------------------------

_GOLDEN_RELS = {
    "data-model": "data-model.md",
    "api-surface": "api-surface.md",
    "module-graph": "module-graph.md",
}


def golden_for(name: str, version: tuple[int, int]) -> dict[str, str]:
    """Read `name`'s committed golden spine files for interpreter `version`.

    Delegates path resolution (base golden vs. a per-minor overlay) to
    `derive_corpus.golden_path`, imported by path exactly as
    `test_arch_corpus.py` imports it (the directory name has a hyphen, so
    it is not an importable package). Raises `FileNotFoundError` when a file
    is not committed.
    """
    spec = importlib.util.spec_from_file_location(
        "_facts_matcher_derive_corpus", HERE / "derive_corpus.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    out: dict[str, str] = {}
    for concern, rel in _GOLDEN_RELS.items():
        path = module.golden_path(name, rel, version)
        out[concern] = path.read_text(encoding="utf-8")
    return out
