"""The arch derive CORE — everything that is not a stack pack (ADR-0096 clause 12).

Clause 12 splits the deriver into a core and one module per pack: the core owns
the probe registry, input resolution and decoding, canonical serialization,
hashing, verdict computation and the stub renderer; each pack module owns its
own detector and probes. Clause 1's structural boundary *is* this split — "the
core reads, the probe receives the decoded form" is a statement about where the
seam falls.

**This module carries no module-level intra-crux import, and that is load-bearing.**
`runtime/capture.py::_load_cell` file-loads this module by path via
`spec_from_file_location`, deliberately bypassing `crux/__init__`'s LLM/httpx
import chain so the runtime executor stays stdlib-only under the USER's project
interpreter. A `from .packs import ...` at module scope here would break it.
Import a pack lazily, inside the function body, exactly as `derive.py` has always
imported `recover.curated_keep`. `test_arch_split_seam.py` asserts this
mechanically over the module's own AST.

**The byte-identity claim below is HISTORY, and it is dated.** Clause 12's gate
was byte-identity across the corpus goldens, and it closed on the U7 move: no
golden moved, because nothing in this module was rewritten while it moved. That
sentence is true of that unit and of no other. Later units changed behaviour on
top of the moved code — the entity-first verdict predicate, the contained and
size-bounded reads, the escape-refusing atomic spine write — so a reader must not
carry the claim forward as a standing property of this file.
"""

from __future__ import annotations

import enum
import errno
import functools
import hashlib
import importlib.metadata
import importlib.util
import inspect
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path
from typing import Callable, NamedTuple

# Fixed filename order — this IS the spine-hash domain (ADR-0060 Decision 2).
SPINE_FILES = ["data-model.md", "api-surface.md", "module-graph.md", "decision-index.md"]
NO_EXTRACTOR = "> _no extractor for this stack — empty-but-valid spine file._\n"

# Tool-version pins recorded in _meta/manifest.json (byte-stable provenance).
TOOL_PINS = {"engine": "arch-derive/1", "serialization": "canonical/1"}

# The manifest's tree key, and the one sub-object `dry_run` does not compare —
# the VALUE of `_meta/manifest.json["sources"]`, the only exclusion anywhere in
# the drift gate (PB-0073). Every other property of the file, down to key order
# and whitespace, stays byte-compared; see `_manifest_drifted` for how. Named
# here, beside the pins it sits next to in the manifest, so the key has one
RECOVERED_DIRNAME = "_recovered"   # arch/_recovered/state.yml — ADR-0062 D4, never a derive output
# definition: `_build` writes the tree under `MANIFEST_REL` and `dry_run` routes
# on the same name.
MANIFEST_REL = "_meta/manifest.json"
MANIFEST_UNCOMPARED_KEY = "sources"


# ───────────────────────────── helpers ─────────────────────────────────────

def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _rel(root: Path, p: Path) -> str:
    """Repo-relative POSIX path — environment-independent, per Decision 4."""
    return p.resolve().relative_to(root.resolve()).as_posix()


_FM_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def _frontmatter(text: str) -> dict:
    """Parse a leading YAML frontmatter block; {} if absent.

    The stdlib-only fallback keeps this module IMPORTABLE without PyYAML, and
    that is all it is for now. No derive reaches it: the universal
    `decision-index` concern declares `yaml`, so `resolve_declared_parsers`
    refuses before any extractor runs. That matters because the fallback ANSWERS
    DIFFERENTLY — one tree produced two spine hashes through the two readers,
    both at exit 0 — which is the defect the declaration exists to end.
    """
    m = _FM_RE.match(text)
    if not m:
        return {}
    try:
        import yaml  # PyYAML is available under `uv run`.
        return yaml.safe_load(m.group(1)) or {}
    except Exception:
        out: dict = {}
        for line in m.group(1).splitlines():
            mm = re.match(r"^([A-Za-z0-9_]+)\s*:\s*(.*)$", line)
            if mm:
                out[mm.group(1)] = mm.group(2).strip().strip('"\'')
        return out


def _body(text: str) -> str:
    m = _FM_RE.match(text)
    return text[m.end():] if m else text


def _canon(lines: list[str]) -> str:
    """Join to a single canonical string: exactly one trailing newline, no CRLF."""
    return "\n".join(lines).rstrip("\n") + "\n"


# The four spine slots addressed as concern names (the spine filename without
# its `.md` suffix). These are the keys of a pack's probe registry and of the
# `arch_extractors` per-repo override map.
CONCERNS = tuple(f[:-3] for f in SPINE_FILES)      # data-model, api-surface, …


# ─────────────────────── pluggable stack-pack seam (ADR-0066) ───────────────
# A Probe pairs a cheap detector with an extractor sharing the uniform
# `(root, docs_dir) -> (markdown, sources)` contract every extractor above
# already honors. A Pack is a detector (for stack auto-detection) plus an
# ordered probe list per concern; the first probe whose `detect` fires wins.

class DetectResult(NamedTuple):
    """One pack's answer to "does this repository look like my stack?" (ADR-0096
    clause 6).

    `matched` is the boolean the pre-split marker predicate returned. `markers`
    carries the repo-relative marker paths that fired, and it is not decoration:
    clause 6 requires that every unrecorded multi-match report
    `stubbed: ambiguous_stack` **naming each candidate AND the marker that
    matched it**, so the marker set is the payload that verdict is rendered from.
    A pack whose detector fires on no marker returns an empty tuple.
    """

    matched: bool
    markers: tuple[str, ...] = ()


class Probe(NamedTuple):
    detect: Callable[[Path], bool]
    extract: Callable[[Path, str], tuple[str, dict]]
    #: The probe's own declared input class (ADR-0097 part 1), a member of
    #: `INPUT_CLASS_KINDS`. REQUIRED, no default: a default would let a new
    #: probe declare nothing about what it reads and still pass, which is the
    #: exact gap ADR-0097 part 1 exists to close. A concern's class is then
    #: DERIVED from every registered probe's `kind` — see `derive_concern_kind`.
    kind: str


def _always(_root: Path) -> bool:
    return True


def _stub_extract(concern: str, root: Path, docs_dir: str) -> tuple[str, dict]:
    """The empty-but-valid spine file for a concern no pack/override supplies
    (ADR-0060 graceful degradation). Title mirrors the concern's real extractor
    so a stubbed file is indistinguishable in shape from a degraded one."""
    title = _CONCERN_TITLES.get(concern, f"# {concern}")
    return _canon([title, "", NO_EXTRACTOR.rstrip("\n")]), {}


def _is_empty_but_valid(content: str) -> bool:
    """True iff `content` is the empty-but-valid spine file and nothing more.

    The structural form of the question `NO_EXTRACTOR.strip() in content` asked
    badly. A substring test says "this sentence appears somewhere", which target
    document content satisfies trivially; this says "this file consists of a
    title line and the marker", which a file carrying any real extraction cannot.

    Written over non-blank lines rather than over exact bytes because the marker
    is emitted by `_stub_extract` here and by twenty-odd self-degrading probes
    across the packs, and they do not all agree on the title text — only on
    the shape.
    """
    lines = [ln for ln in content.splitlines() if ln.strip()]
    return len(lines) == 2 and lines[1].strip() == NO_EXTRACTOR.strip()


_CONCERN_TITLES = {
    "data-model": "# Data model",
    "api-surface": "# API surface",
    "module-graph": "# Module graph",
    "decision-index": "# Decision index",
}


# ══════════════════════════ ADR-0096 clauses 2/3/4 ══════════════════════════
# Two channels, six reasons, one honest stub line.
#
# The RECORDED channel is `_meta/coverage.json` plus the spine stub line. Both
# sit inside the drift gate's byte-compared set, and both carry exactly one
# `Verdict` per concern. **A verdict is a function of committed bytes alone.** A
# condition that depends on the running environment is not a verdict and never
# reaches this channel — a declared parser that will not resolve on this machine
# is exit 2 with nothing written, exactly as a missing interpreter dependency
# already is.
#
# The REPORTED channel is stdout and the printed coverage table, written nowhere
# under `arch/`. It carries the recorded verdict PLUS zero or more annotations.
# An annotation never enters the byte-compared set, never changes a recorded
# verdict, and never changes the gate's exit status. `ANNOTATIONS` below is that
# vocabulary; `possibly_stale` (clause 7) is its only member, and `staleness.py`
# produces it. It is attached AFTER this module's work: `reported_coverage`
# builds every record with an empty annotation list, and `staleness.annotate`
# appends to that list from outside the derive path, which is why nothing here
# ever holds a non-empty one.
# `ArchTwoChannelTests` asserts the vocabulary appears nowhere under `arch/`.

class StubReason(enum.Enum):
    """The CLOSED set of six reasons a concern's recorded verdict is `stubbed`.

    Each is semantically distinct, each is a function of committed bytes, and no
    reason is reused for a second condition. Deliberately absent: any reason
    naming an environment condition. A parser that cannot be resolved on this
    machine is clause 2's exit 2, never a stub — a verdict that varied with the
    machine would drift the byte-compared gate across environments.

    The definitions are input-class-neutral: `parse_failed` and `no_entities`
    apply equally to a decoded committed artifact and to a parse tree.
    """

    #: The declared input is absent.
    PRECONDITION_MISSING = "precondition_missing"
    #: The detected stack has no registered pack.
    UNSUPPORTED_STACK = "unsupported_stack"
    #: More than one pack matched with no recorded precedence (clause 6).
    AMBIGUOUS_STACK = "ambiguous_stack"
    #: More than one candidate package, and no per-package graph (clause 6).
    AMBIGUOUS_PACKAGE = "ambiguous_package"
    #: The declared input was present and could not be decoded.
    PARSE_FAILED = "parse_failed"
    #: The declared input was consumed cleanly and declares none of the
    #: concern's entities. Claims only that; it does NOT claim the repository
    #: has none. Clause 8's expectation record is what tells those apart.
    NO_ENTITIES = "no_entities"


#: The reported channel's annotation vocabulary (clause 2). Never recorded.
ANNOTATIONS = ("possibly_stale",)

#: The reserved sentence. Clause 4 gives it to `unsupported_stack` and to no
#: other branch, because in 20 of the 23 baseline stubs it was simply false: a
#: pack was selected and an extractor existed.
UNSUPPORTED_STACK_SENTENCE = "no extractor for this stack"

#: The noun a concern counts, singular. This is the "entity of the concern's own
#: kind" clause 3 requires at least one of before a verdict may be `populated`.
_ENTITY_NOUN = {
    "data-model": "entity row",
    "api-surface": "route row",
    "module-graph": "graph edge",
    "decision-index": "indexed decision",
}


def _entity_noun(pack_name: str, concern: str) -> str:
    """The noun a `no_entities` stub line counts for `concern`: the per-pack
    entity-noun seam (ADR-0129 clause 6).

    A registered pack may declare `ENTITY_NOUNS: dict[str, str]` — an optional
    module attribute overriding the core default for the concerns it names.
    Every other concern, and every unregistered pack, gets the unchanged core
    default from `_ENTITY_NOUN`.
    """
    if pack_name in REGISTERED_PACKS:
        nouns = getattr(_pack_module(pack_name), "ENTITY_NOUNS", {})
        if concern in nouns:
            return nouns[concern]
    return _ENTITY_NOUN[concern]


class Verdict(NamedTuple):
    """Exactly one recorded verdict per concern (clause 2).

    Built through `populated()` or `stubbed()` and never by hand: `populated()`
    REFUSES an entity count below 1, which is where clause 3's floor is
    enforced. That refusal is the whole mechanism — the pre-ADR-0096 classifier
    called a concern populated whenever its extractor returned any source at
    all, which is how a data-model rendering no entity table and a module graph
    with no edges both reported `populated`.
    """

    kind: str                                   # "populated" | "stubbed"
    n_sources: int = 0
    n_entities: int = 0
    reason: "StubReason | None" = None
    expected: str = ""
    found: str = ""

    @classmethod
    def populated(cls, n_sources: int, n_entities: int) -> "Verdict":
        if n_entities < 1:
            raise ValueError(
                "populated requires at least one entity of the concern's own "
                f"kind (clause 3); got n_entities={n_entities}"
            )
        return cls(kind="populated", n_sources=int(n_sources), n_entities=int(n_entities))

    @classmethod
    def stubbed(cls, reason: StubReason, expected: str, found: str) -> "Verdict":
        if not isinstance(reason, StubReason):
            raise ValueError(f"stub reason outside the closed set: {reason!r}")
        return cls(kind="stubbed", reason=reason, expected=expected, found=found)

    def as_record(self) -> dict:
        """The recorded channel's fields for this verdict.

        `populated` carries the source count and the entity count; `stubbed`
        carries the reason plus what was expected and what was found. Neither
        carries the other's fields, so a reader cannot mistake a default 0 for a
        measurement.
        """
        if self.kind == "populated":
            return {"verdict": "populated",
                    "n_sources": self.n_sources,
                    "n_entities": self.n_entities}
        return {"verdict": "stubbed",
                "stub_reason": self.reason.value,
                "expected": self.expected,
                "found": self.found}


def render_stub_line(verdict: Verdict) -> str:
    """The clause 4 stub line: the reason, what was expected, what was found.

    One grammar, so the line is mechanically distinguishable from the
    blockquote-italic PROSE every spine renderer also emits:

        > _stub: <reason> — expected <expected>, found <found>._

    `golden_diff_classify.py` matches exactly this grammar plus the pre-ADR-0096
    spelling; it deliberately does not match "any blockquote", because a prose
    cell is extraction content.

    Almost every token is authored here or an integer computed here. The two
    clause 6 verdicts are the exception and the only one: `ambiguous_stack` names
    the marker that matched, and the ruby pack's marker is a `*.gemspec` glob, so
    a filename from the repository reaches this line; `ambiguous_package` names
    candidate package directories the same way. Both build their `found` string
    through `_cell`, so a hostile filename cannot break the blockquote or the
    surrounding code span. No exception text and no environment string reaches
    this line at all.
    """
    return (f"> _stub: {verdict.reason.value} — expected {verdict.expected}, "
            f"found {verdict.found}._")


def _apply_stub_line(content: str, verdict: Verdict) -> str:
    """Render `verdict`'s stub line into one spine file's markdown.

    Two shapes, because a stubbed concern is not always an empty file:

      * the extractor returned the empty-but-valid stub → the legacy
        `NO_EXTRACTOR` sentence is REPLACED in place;
      * the extractor returned real content that declares no entity of this
        concern's kind (`no_entities`) → the stub line is INSERTED under the
        title, and the content is left exactly as the extractor produced it.

    The second shape is deliberate. Discarding a `no_entities` concern's content
    would delete extraction the extractor really did — fastapi-fullstack's
    Alembic timeline is five migrations it genuinely read — and the verdict is
    about entities of the concern's own kind, not about the file being empty.
    """
    if verdict.kind == "populated":
        return content
    line = render_stub_line(verdict)
    marker = NO_EXTRACTOR.rstrip("\n")
    lines = content.split("\n")
    for i, existing in enumerate(lines):
        if existing == marker:
            lines[i] = line
            return "\n".join(lines)
    # No marker: insert under the `# Title` heading and its blank line.
    at = 2 if len(lines) > 1 and lines[0].startswith("# ") and not lines[1].strip() else 0
    lines[at:at] = [line, ""]
    return "\n".join(lines)


# ── structural entity counting, per concern (clause 3) ───────────────────────
# Counted from the RENDERER'S OWN STRUCTURE, one rule per concern. This is
# deliberately not the corpus harness's `count_entities`, which sums every
# markdown table row in a file: that heuristic counts an `## Indexes` row, a
# `## Relations` row and an `## Isolated modules` row as entities, so a
# data-model with no entity table and a module graph with no edges both score
# above zero — which is exactly the reading clause 3 exists to refuse.

_TABLE_SEP_RE = re.compile(r"\A\|[\s:|-]+\|\Z")

# A concern's ENTITY TABLE, identified by the first two cells of its header row.
# Every renderer of a given concern emits the same header for its entity table,
# and a different one for every table beside it, so the header is the structural
# fact that separates the two. The alternative — matching the `##` heading a
# table sits under — cannot work: the OpenAPI api-surface renderer groups by tag
# and emits `## <tag> (N)`, a heading drawn from the target document.
#
#   data-model    `| table | column | …`   ruby, elixir, all three node probes,
#                                          and the python SQLAlchemy table
#                 `| field | type | …`     the crux pack, one table per entity
#                 `| type | kind | …`      the Swift pack's type table
#   api-surface   `| method | path | …`    ruby, elixir, node, and OpenAPI
#                 `| skill | description`  the crux pack
#                 `| interface | kind | …`         the Swift pack's interface table
#                 `| requirement | protocol | …`   the Swift pack's protocol requirements
#                 `| product | product kind | …`   the Swift pack's SwiftPM products
#                 `| @main type | kind | …`        the Swift pack's app entry points
#   decision-index `| # | decision | …`    the universal probe
#
# A table this does not recognize contributes nothing, which UNDER-counts rather
# than over-counts. That direction is deliberate: an unrecognized renderer
# reports `no_entities` and says so, where an over-counting default would report
# `populated` over a table of something else — which is the reading clause 3
# exists to refuse. A new renderer earns a row here, and the corpus expectation
# record is what catches one that did not get it.
#
# The Swift pack ALSO renders tables this set deliberately excludes — a
# data-model property table (`| owner | property | … |`) and a relationship
# table (`| from type | relation | … |`), and a module-graph container/target
# roster (`| container | container kind | … |`, `| target | container | … |`,
# `| from | dependency | … |`, `| file | module | … |`, `| file | target | … |`).
# None of those is this concern's OWN entity — a data-model property belongs to
# a type already counted once, and a module-graph container is a grouping, not
# an edge — so registering them would double-count or count the wrong noun.
# `HeaderRegistryDisciplineTests` in `test_arch_verdicts.py` pins both halves of
# this: the five Swift pairs registered above, and that none of these seven stays
# uncounted only by omission.
_ENTITY_TABLE_HEADERS = {
    "data-model": {("table", "column"), ("field", "type"), ("type", "kind")},
    "api-surface": {
        ("method", "path"), ("skill", "description"),
        ("interface", "kind"), ("requirement", "protocol"),
        ("product", "product kind"), ("@main type", "kind"),
    },
    "decision-index": {("#", "decision")},
}

#: The crux pack renders each data-model entity as a `###` subsection carrying
#: its own field table, so the subsection heading is itself an entity.
_ENTITY_SUBSECTION_CONCERNS = ("data-model",)


def _header_cells(line: str) -> tuple[str, ...]:
    """The lowercased cells of a markdown table row, outer pipes stripped."""
    return tuple(c.strip().strip("`").lower() for c in line.strip().strip("|").split("|"))


def _entity_table_rows(concern: str, markdown: str) -> int:
    """Data rows of every table whose header names this concern's entity."""
    want = _ENTITY_TABLE_HEADERS.get(concern, set())
    lines = markdown.split("\n")
    n = 0
    counting = False
    for i, raw in enumerate(lines):
        s = raw.strip()
        if not (s.startswith("|") and s.endswith("|")):
            counting = False
            continue
        if _TABLE_SEP_RE.match(s):
            continue
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
        if _TABLE_SEP_RE.match(nxt):            # a header row: open or close
            counting = _header_cells(s)[:2] in want
            continue
        if counting:
            n += 1
    return n


def _mermaid_edges(markdown: str) -> int:
    """`-->` edges inside a ```mermaid fence — how a module graph renders one."""
    n = 0
    inside = False
    for line in markdown.split("\n"):
        s = line.strip()
        if s.startswith("```"):
            inside = s.startswith("```mermaid")
            continue
        if inside and "-->" in s:
            n += 1
    return n


def count_concern_entities(concern: str, markdown: str) -> int:
    """How many entities OF THIS CONCERN'S OWN KIND the rendered file declares.

    data-model counts rendered entity rows, api-surface route rows, module-graph
    graph edges, decision-index indexed decisions. Each is read from the
    structure that concern's renderers actually emit, so a section a renderer
    adds beside its entities — indexes, relations, enums, residuals, isolated
    modules, an Alembic migration timeline — never inflates the count.
    """
    if concern == "module-graph":
        return _mermaid_edges(markdown)
    n = _entity_table_rows(concern, markdown)
    if concern in _ENTITY_SUBSECTION_CONCERNS:
        n += sum(1 for line in markdown.split("\n") if line.startswith("### "))
    return n


# ── declared input classes (ADR-0097 part 1: probe-level kind) ──────────────

#: The closed FOUR-value kind set (ADR-0097 part 1, widened from ADR-0096's
#: three). `committed-artifact` reads a file the target project's own toolchain
#: emitted; `parser` reads authored source through a real parse; `stub`
#: declares neither is available; `regex-over-source` matches a regular
#: expression against authored source and is admissible on a probe only when
#: the ADR-0097 roster manifest names that probe's qualified function name.
#:
#: **The tie-breaker between `parser` and `regex-over-source`, because those two
#: are the pair a probe gets wrong.** The question is whether the pattern reads
#: authored GRAMMAR or finds a document DELIMITER. Splitting a SKILL.md on its
#: `---` fence is a delimiter scan and stays `parser`, because the fence carries
#: no grammar and the YAML inside it is then parsed. Recovering the same keys by
#: matching `^([a-z_]+)\s*:` is a pattern read of authored grammar and is
#: `regex-over-source` — roster, or convert the reader. ADR-0097 part 3 draws
#: that line, and it is repeated here rather than left in a frozen ADR body:
#: the declaration is made in code, so the test for it belongs beside the code.
INPUT_CLASS_KINDS = ("committed-artifact", "parser", "regex-over-source", "stub")

#: The total order ADR-0097 part 1 states, weakest first. A concern's derived
#: `kind` is the MINIMUM of its probe chain's non-`stub` rungs under this
#: order — the rungs are alternatives, and any one of them may be the rung that
#: answers, so the weakest one that can is the honest name for what the
#: concern consumes.
INPUT_CLASS_ORDER = ("stub", "regex-over-source", "parser", "committed-artifact")


def derive_concern_kind(kinds) -> str:
    """A concern's derived `kind`: the minimum of `kinds` under
    `INPUT_CLASS_ORDER`, ignoring any `stub` rung unless every rung is `stub`
    (ADR-0097 part 1).

    Two edge cases, both stated because the ADR states them explicitly. An
    all-`stub` chain — including the empty chain, the empty pack's case —
    derives `stub`. A chain mixing a `stub` rung with a non-`stub` rung ignores
    the `stub` rung: `stub` is the weakest value in the order but is never
    itself the answer once any other rung can answer.

    Raises `ValueError` naming the offending value when any of `kinds` sits
    outside `INPUT_CLASS_KINDS` — this is the real validation ADR-0097 part 1
    asks for; the set was declarative only, and enforced nowhere but a test,
    under ADR-0096.
    """
    kinds = tuple(kinds)
    for k in kinds:
        if k not in INPUT_CLASS_KINDS:
            raise ValueError(
                f"input-class kind outside the closed set {INPUT_CLASS_KINDS!r}: {k!r}"
            )
    non_stub = [k for k in kinds if k != "stub"]
    if not non_stub:
        return "stub"
    return min(non_stub, key=INPUT_CLASS_ORDER.index)


class InputClass(NamedTuple):
    """What class of input one pack-concern pair consumes.

    Five fields sit on the CONCERN, declared once per pack in its
    `INPUT_CLASSES` map, because none of them varies by which rung of a probe
    chain answers: `expected`, `globs`, `parser`, `artifact`, `refresh` (each
    documented at its own field below). `kind` sits on the PROBE instead
    (ADR-0097 part 1) — a probe chain mixes classes, so a single concern-level
    `kind` could not name which rung was which. `input_classes()` DERIVES this
    field per concern from the resolved pack's registered `Probe.kind` values
    via `derive_concern_kind`, and never hand-writes it; a pack's
    `INPUT_CLASSES` map declares no `kind` at all.

    `expected` is the sentence a `precondition_missing` stub line renders — the
    thing whose absence produced the stub, in the same terms as the recorded
    verdict.

    `globs` is the concern's declared input SET, repo-relative and
    machine-readable. It is what clause 8 postcondition (e) derives its edit set
    from: a file matching no concern's globs is outside every declared input
    set, and editing it must never move the tree.

    **What the declaration does and does not assert.** `kind` is DERIVED and is
    exactly as strong as the weakest probe in a concern's chain —
    `regex-over-source` names that a probe matches a pattern against authored
    source rather than performing a real parse, and it is admissible only when
    the ADR-0097 roster names that probe. Nothing here asserts that a
    `parser`-kind probe IS a conforming parser rather than a pattern reader
    that merely declares itself one; closing that residual is pack review's
    job, which ADR-0097 part 1 states as the honest limit rather than papering
    over it.
    """

    expected: str
    globs: tuple[str, ...] = ()
    #: The importable module names a `parser` declaration needs on this machine.
    #: Empty means the concern requires no third-party module, so nothing is
    #: import-checked for it at derive time. It is NOT a claim about which reader
    #: meets the declaration — the class docstring above owns that question, and
    #: names it a pack-review residual rather than something this table asserts.
    #:
    #: Empty was once every pack's position. It no longer is: SIXTEEN of the
    #: twenty-four shipped pack-concern pairs name a parser.
    #:
    #: Eight name tree-sitter modules — the ruby api-surface, both the
    #: data-model and api-surface of node and elixir, and all three of the
    #: swift pack's non-universal concerns. Two name `yaml` on the
    #: crux pack: its data-model, which parses `<docs_dir>/manifest.yml` with
    #: `yaml.safe_load` and SKILL.md frontmatter through `_frontmatter`, and its
    #: api-surface, which parses SKILL.md frontmatter through `_frontmatter` and
    #: reads no manifest at all. The remaining six are one per pack: `decision-index` is the
    #: universal concern and declares `yaml` for every pack, because it reads ADR
    #: frontmatter through the same `_frontmatter`, whose regex fallback answers
    #: differently and would otherwise move the spine hash on a machine without
    #: PyYAML. Every registered pack therefore pins PyYAML, and no pack can
    #: derive a spine through the fallback.
    #:
    #: A named module that will not import is clause 2's ENVIRONMENT failure —
    #: exit 2 with nothing written — and never a stub reason, because a verdict
    #: that varied with the machine would drift the byte-compared gate across
    #: environments. Declared here rather than invented at the first pinned
    #: grammar so the seam exists before it is used.
    parser: tuple[str, ...] = ()
    #: Which of `globs` name the EMITTED ARTIFACT rather than the source set the
    #: concern would otherwise parse. Clause 7 draws that line and this field is
    #: it: `globs` conflates the two, and the staleness advisory needs them apart
    #: to ask "was a source committed after the artifact" rather than "was a file
    #: committed after another file in the same set".
    #:
    #: Three shipped pack-concern pairs declare one, and each names an artifact the target
    #: project's own toolchain emitted: the python pack's api-surface declares
    #: `openapi.json`, and the ruby pack's data-model and api-surface declare
    #: `db/schema.rb` and `arch-inputs/routes.txt`. None of the three carries a
    #: content gate of its own, which is the case this field exists for.
    #:
    #: The two committed artifacts crux ITSELF consumes stay undeclared, and that
    #: is a judgment rather than an omission. The regenerated
    #: `crux/catalog/skills.json` and the ADR index are already gated on their
    #: CONTENT: a drifted one raises `StaleProjectionInput` and the derive
    #: refuses. Commit order is the weaker question, so asking it of an artifact
    #: that already answered the stronger one produces noise, not signal.
    artifact: tuple[str, ...] = ()
    #: The one command that refreshes a `committed-artifact`, printed beside a
    #: clause 7 `possibly_stale` annotation. Empty means the pack declares none,
    #: and the annotation says so rather than inventing one. Refresh is the
    #: user's act: the command runs the target's own framework, which is the code
    #: execution the unattended pipeline must not perform.
    refresh: str = ""
    #: DERIVED by `input_classes()` from the resolved pack's registered probe
    #: kinds (`derive_concern_kind`) — never hand-written in a pack's
    #: `INPUT_CLASSES` map (ADR-0097 part 1). Defaults to `""`, trailing every
    #: other field, only so a bare `InputClass(...)` construction stays valid
    #: before derivation runs; every value `input_classes()` actually returns
    #: carries a real member of `INPUT_CLASS_KINDS`.
    kind: str = ""


#: `decision-index` is the UNIVERSAL concern: the ADR tree is stack-independent,
#: so the core binds its probe for every pack and declares its input class here
#: rather than making six packs repeat one sentence. `input_classes()` merges
#: it in, so every registered pack answers for all four concerns. `kind` is
#: absent here — like every `InputClass`, it is DERIVED — and both the
#: `complete` and `curated` decision-index probe bindings declare
#: `committed-artifact` (ADR-0097 part 1): both read the committed ADR index
#: and the committed ADR files, so the derived class is `committed-artifact`
#: in both modes.
#:
#: **It declares `yaml`, and that declaration is why every pack refuses without
#: PyYAML.** This probe reads ADR frontmatter through `_frontmatter`, whose
#: regex fallback answers DIFFERENTLY: measured on one python-pack tree with a
#: folded-scalar ADR `title`, the derive produced two `spine_hash` values, both
#: at exit 0, with identical `tool_pins`, and wrote the tree both times. The
#: crux pack declaring `yaml` for its own two concerns did not reach this one,
#: so the identical defect stayed live on every other pack downstream
#: repositories use. Declaring it on the UNIVERSAL concern is what routes every pack to
#: ADR-0096 clause 2's environment lane — exit 2, nothing written — instead of a
#: quieter spine, and it is why `parser_pins` now records `parser:yaml` for every
#: pack rather than only for crux.
DECISION_INDEX_INPUT = InputClass(
    expected="an ADR index at `<docs_dir>/adrs/index.md` with Accepted, non-archived entries",
    globs=("*/adrs/index.md", "*/adrs/ADR-*.md"),
    parser=("yaml",),
)

#: The kind every decision-index probe declares (ADR-0097 part 1) — see
#: `DECISION_INDEX_INPUT` above. Shared by `pack_probes` (which builds the
#: bound `Probe`) and `input_classes` (which derives the concern's class from
#: it), so the two cannot drift apart.
_DECISION_INDEX_PROBE_KIND = "committed-artifact"

#: The empty pack: nothing detected and nothing pinned. Its three non-universal
#: concerns are the only place clause 4's reserved sentence is true, and (per
#: ADR-0097 part 1) the only place `derive_concern_kind` derives a kind from an
#: EMPTY probe chain — which is exactly `stub`.
_STUB_PACK_INPUT = InputClass(
    expected="a registered stack pack for this repository",
    globs=(),
)


def input_classes(pack_name: str) -> dict[str, InputClass]:
    """The four declared input classes for a resolved pack (ADR-0097 part 1).

    A registered pack supplies its own three through the module-level
    `INPUT_CLASSES` mapping; the core supplies `decision-index`. The empty stub
    pack declares `stub` for its three and still carries the universal
    decision-index declaration, because that probe runs for it too.

    `kind` is never read off the declared `InputClass` — it is DERIVED here,
    per concern, from the resolved pack's registered `Probe.kind` values via
    `derive_concern_kind`. The declaration moved from the concern to the probe;
    a concern's class is a function of its probes, never a hand-written field.
    """
    if pack_name in REGISTERED_PACKS:
        declared = dict(getattr(_pack_module(pack_name), "INPUT_CLASSES", {}))
        registry = dict(_pack_module(pack_name).probes())
    else:
        declared = {c: _STUB_PACK_INPUT for c in CONCERNS if c != "decision-index"}
        registry = {}
    result = {
        concern: ic._replace(
            kind=derive_concern_kind(p.kind for p in registry.get(concern, ()))
        )
        for concern, ic in declared.items()
    }
    result["decision-index"] = DECISION_INDEX_INPUT._replace(
        kind=derive_concern_kind([_DECISION_INDEX_PROBE_KIND])
    )
    return result


def declared_input_globs(pack_name: str) -> tuple[str, ...]:
    """The union of every concern's declared input globs, sorted and deduped.

    Postcondition (e)'s edit set is defined AGAINST this and never against a
    gate outcome: a repo-relative path matching none of these globs is outside
    every concern's declared input set.
    """
    seen: set[str] = set()
    for ic in input_classes(pack_name).values():
        seen.update(ic.globs)
    return tuple(sorted(seen))


class ParserUnavailable(RuntimeError):
    """A declared parser cannot be resolved on this machine (clause 2).

    The environment lane, not a stub reason. It carries no verdict, nothing is
    written, and `derive-arch.py` routes it to exit 2 exactly as it routes a
    missing interpreter dependency — because that is what it is. A machine that
    cannot resolve a pinned grammar gets no tree rather than a degraded spine,
    which the ADR names as the harder failure and the only one compatible with a
    byte-compared recorded channel.
    """


def resolve_declared_parsers(pack_name: str) -> None:
    """Raise `ParserUnavailable` if any declared parser is missing here.

    Called once, before any extractor runs, so the refusal lands before the
    deriver has written or compared a byte. Resolution is `find_spec`, which
    locates a module without executing it — the derive path executes no
    application code, and checking a dependency must not become the exception.

    A concern whose `parser` tuple is empty names no third-party module, so
    nothing is import-checked for it. Eight of the twenty-four shipped pack-concern
    pairs are in that position. The other sixteen are the ones this function can
    raise for: eight name tree-sitter modules, two name `yaml` on the crux pack,
    and six are the universal `decision-index`, once per pack. Because that
    last one is universal, EVERY pack can raise here — which is the point of
    declaring it there rather than on one pack's own concerns.

    **The load-check seam (ADR-0129 clause 2).** After every `find_spec`
    check has passed, a registered pack's
    optional `verify_parser_load()` hook is called once. ANY exception it
    raises becomes `ParserUnavailable` naming the pack and the exception type,
    raised in the same place and before the same nothing-written-yet moment as
    the `find_spec` refusal above. A pack without the hook resolves exactly as
    it did before this hook existed.
    """
    missing: list[str] = []
    for concern, ic in sorted(input_classes(pack_name).items()):
        for name in ic.parser:
            try:
                found = importlib.util.find_spec(name) is not None
            except Exception:            # a broken/half-installed dist, same lane
                found = False
            if not found:
                missing.append(f"{concern} declares the parser {name!r}")
    if missing:
        raise ParserUnavailable(
            f"the {pack_name} pack declares parsers this machine cannot resolve: "
            + "; ".join(missing)
        )
    if pack_name in REGISTERED_PACKS:
        verify = getattr(_pack_module(pack_name), "verify_parser_load", None)
        if verify is not None:
            try:
                verify()
            except Exception as exc:
                raise ParserUnavailable(
                    f"the {pack_name} pack's verify_parser_load() failed: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc


@functools.lru_cache(maxsize=1)
def _module_distributions() -> dict:
    """Import-name → distribution-name map, built once per process.

    `importlib.metadata.version` takes a DISTRIBUTION name, and an import name
    is not one: `tree_sitter_elixir` ships from `tree-sitter-elixir` and `yaml`
    from `PyYAML`. `packages_distributions()` is the mapping the stdlib
    maintains for exactly that question.
    """
    return importlib.metadata.packages_distributions()


def _parser_version(module: str) -> str | None:
    """The installed version of the distribution providing `module`, or None.

    Two lookups, in order, and the second is the fallback for a module the
    top-level map does not cover: the distribution map, then the module name
    itself read as a distribution name (`importlib.metadata` applies PEP 503
    normalization, so `tree_sitter` finds `tree-sitter`). A module mapping to
    more than one distribution takes the codepoint-first one, so the answer is
    the same on two machines with the same environment.
    """
    for dist in sorted(_module_distributions().get(module, ())):
        try:
            return importlib.metadata.version(dist)
        except importlib.metadata.PackageNotFoundError:
            continue
    try:
        return importlib.metadata.version(module)
    except importlib.metadata.PackageNotFoundError:
        return None


def parser_pins(pack_name: str) -> dict[str, str]:
    """The version pin of every parser the RESOLVED pack declares (clause 1).

    Clause 1: "Each parser's version pin is hashed into the provenance
    manifest, so a parser upgrade moves the tree deliberately rather than
    silently." These merge into the manifest's `tool_pins` under a `parser:`
    prefix, so the manifest's key set stays the closed four the ledger has
    always had.

    **Pack-scoped, never global.** A module-level constant would record every
    GRAMMAR version in every tree's manifest, including the two packs that
    consult no grammar at all — crux and python. That would move bytes a grammar
    had no part in producing, and the manifest is the ledger of what produced
    these bytes.

    No pack declares NO parser any more, so the scoping argument is now entirely
    about the grammars: every pack records `parser:yaml` through the universal
    `decision-index`, and only the four tree-sitter packs record a grammar
    beside it.

    A declared parser that resolves but whose version cannot be read raises
    `ParserUnavailable`. Clause 1 requires the pin RECORDED, and a manifest
    that silently omitted one would let the byte-compared channel disagree with
    itself across two machines — the exact failure clause 2 keeps out of the
    recorded channel by routing every environment condition to exit 2.
    """
    pins: dict[str, str] = {}
    unpinnable: list[str] = []
    for concern, ic in sorted(input_classes(pack_name).items()):
        for name in ic.parser:
            version = _parser_version(name)
            if version is None:
                unpinnable.append(f"{concern} declares the parser {name!r}")
                continue
            pins[f"parser:{name}"] = version
    if unpinnable:
        raise ParserUnavailable(
            f"the {pack_name} pack declares parsers whose version this machine "
            "cannot read, so clause 1's pin cannot be recorded: "
            + "; ".join(unpinnable)
        )
    return pins


# ── the pack registry: one structure, with the precedence recorded in it ─────
# ADR-0096 clause 6 asks for "a pairwise precedence recorded in the pack
# registry itself", and the ADR-0066 clause 2 seam is the venue. Before this,
# the registry was the five-name tuple `STACK_PRECEDENCE` and the precedence was
# its ORDER, which had two consequences worth naming.
#
# It made "registered" and "beats" the same fact read two ways, so there was no
# way to add a pack without also ranking it against every other pack. And a
# total order decides every pair whether or not anything justifies deciding it:
# python-over-ruby was as settled as ruby-over-node, though the corpus measured
# the second and has never seen the first.
#
# `PackEntry.beats` records only the pairs something measured justifies. Every
# other pair is left undecided on purpose — that is what makes clause 6's
# `ambiguous_stack` reachable rather than dead code.

class PackEntry(NamedTuple):
    """One registered stack pack, and the packs it takes precedence over.

    `name` is the pack module under `crux.arch.packs` and the value an
    `arch_stack:` pin may name. `beats` is this pack's side of the PAIRWISE
    precedence: the packs it wins against when both match the same repository
    AT THE SAME DEPTH. `why` is the measurement that justifies the entry, and
    it is required wherever `beats` is non-empty — an unjustified precedence is
    the guess clause 6 exists to refuse.

    **The relation is depth-scoped, and every `why` below states the depth it
    was measured at.** `_undominated` applies `beats` only among the candidates
    whose shallowest marker sits at the same minimum depth; a shallower match
    wins outright and never consults the relation. So a pair recorded here is a
    claim about co-located markers, which is the only shape any of them was
    measured on. Reading a pair as depth-blind is what let a root
    `package.json` lose to a `tools/setup.py` one directory down.

    The relation must stay antisymmetric and acyclic. `PackRegistryTests`
    asserts both, because a cycle would make resolution order-dependent and a
    mutual `beats` would make it arbitrary.
    """

    name: str
    beats: frozenset[str] = frozenset()
    why: str = ""


PACK_REGISTRY: tuple[PackEntry, ...] = (
    PackEntry(
        "crux",
        frozenset({"python", "ruby", "node", "elixir"}),
        "Its marker is a CONJUNCTION of two directories — `crux/schemas` and "
        "`crux/skills` — so a repository carrying both is the crux monorepo or a "
        "vendored copy of it, and the dogfood pack is the one that describes it. "
        "Measured on this checkout, where both markers sit at the SAME depth: "
        "the crux marker and `pyproject.toml` are both at the root.",
    ),
    PackEntry(
        "python",
        frozenset({"node"}),
        "A repository declaring a Python manifest and a `package.json` AT THE "
        "SAME DEPTH is a Python service with a JavaScript frontend. Measured on "
        "`fastapi-fullstack`, which carries both at the root. The claim is about "
        "co-located markers only: a `package.json` nearer the root than any "
        "Python manifest is a Node repository that vendors Python, and depth "
        "settles that before this pair is consulted.",
    ),
    PackEntry(
        "ruby",
        frozenset({"node"}),
        "The same shape one stack over: a Rails application with a JavaScript "
        "asset pipeline. Measured on `codetriage`, which carries `Gemfile` and "
        "`package.json` at the root — again the SAME depth, and again the claim "
        "reaches no further than that.",
    ),
    PackEntry(
        "node",
        frozenset(),
        "",
    ),
    PackEntry(
        "elixir",
        frozenset({"node"}),
        "A Phoenix application vendors its own `assets/package.json`. Measured "
        "on `papercups`, where the JavaScript is the asset build and the "
        "application is Elixir. There the markers sit at DIFFERENT depths — "
        "`mix.exs` at the root, `assets/package.json` one down — so depth alone "
        "already resolves it and this pair only covers the co-located case, an "
        "umbrella application declaring both at one level.",
    ),
    # Registered last with an empty `beats` set, and no other entry names it
    # (ADR-0129 clause 3). Neither pinned Swift corpus repository carries
    # another pack's marker, so no pair was measured. A co-located Swift and
    # Ruby or Node marker is therefore `ambiguous_stack` until a pin settles it.
    PackEntry(
        "swift",
        frozenset(),
        "",
    ),
)

#: Registration order, and NOT the precedence any more — the precedence is
#: `PackEntry.beats`. This is the deterministic iteration order for scans and
#: reports, derived from the registry so the two cannot drift.
PACK_NAMES: tuple[str, ...] = tuple(e.name for e in PACK_REGISTRY)

#: Packs a pin (`arch_stack:`) may legitimately name. A pin outside this set is
#: a fail-closed error (never a silent stub) — see `detect_stack`. Derived from
#: the registry for the same reason.
REGISTERED_PACKS = frozenset(PACK_NAMES)

#: How many directories below the repository root the marker scan reaches
#: (clause 6). Root-only detection missed an Express application living one
#: directory down; depth two reaches it without walking a whole tree.
DETECTION_MAX_DEPTH = 2

# The empty pack chosen when nothing detects and nothing is pinned.
STUB_PACK_NAME = "stub"


_SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", ".tox",
    "build", "dist", ".mypy_cache", ".pytest_cache", ".eggs", ".idea",
    "deps", "_build",   # Elixir/mix vendored deps + build output (not the project's own source).
}


def _cell(s) -> str:
    """Escape a value for a Markdown table cell OR a `code span`: backslash and
    pipe escaped (table-structure safety), newline and carriage return
    flattened, and backtick neutralized so a value drawn from an untrusted
    filename cannot break out of a surrounding code span. Real identifiers never
    contain these, so this is lossless for valid input and injection-safe for a
    hostile filename.

    **Backslash is escaped FIRST, and the order is the whole point.** Escaping
    pipe first turns `a\\|b` into `a\\\\|b`, where the backslash now escapes the
    backslash and leaves the pipe bare — so the row splits anyway and the escape
    bought nothing. Escaping backslash first yields `a\\\\\\|b`: an escaped
    backslash followed by an escaped pipe. Any future escape added here belongs
    after the backslash replacement for the same reason.

    Use `_prose_cell` instead for a cell whose content renders as markdown
    rather than sitting inside a code span; the two differ only on backtick.
    """
    return (str(s).replace("\\", "\\\\").replace("|", "\\|")
            .replace("\n", " ").replace("\r", " ").replace("`", "ʼ").strip())


def _prose_cell(s) -> str:
    """Escape a value for a Markdown table cell that renders AS MARKDOWN.

    The `_cell` sibling for the case where the cell is not wrapped in a code
    span. Identical except that backtick is left alone: this cell's content is
    rendered markdown, and a backtick in it is legitimate inline code rather
    than a code-span breakout. Substituting it would corrupt real content —
    29 of the 51 skill descriptions carry inline code today, 5 of them inside
    the first 100 characters that survive truncation.

    Backslash before pipe, for the reason `_cell` states.
    """
    return (str(s).replace("\\", "\\\\").replace("|", "\\|")
            .replace("\n", " ").replace("\r", " "))


#: The ways a recursive-descent parser in this engine fails on
#: repository-controlled source — four classes, not two.
#:
#: `SyntaxError` and `ValueError` cover text that is malformed or carries a null
#: byte. `RecursionError` and `MemoryError` come from the CPython parser itself
#: on source that is deeply nested but perfectly valid: `x = 1 + 1 + …` with
#: 100k terms raises the first at 200 KB, and `x = ----…1` 100k deep raises the
#: second at 100 KB. Both sit well inside the 2 MB `_MAX_FILE_BYTES` bound, so
#: `_safe_read_bytes` hands them straight to the parser.
#:
#: Catching only the first two is a REGRESSION, not a tightening. Both
#: `ast.parse` sites in the monolithic `derive.py` caught `Exception`; the split
#: into packs narrowed the handler, and a narrowed handler turns one committed
#: file into `derive-arch.py --dry-run` exiting 2 with empty stdout — the code
#: reserved for an ENVIRONMENT failure, on the exact invocation
#: `.github/workflows/check-arch-drift.yml` runs. One such file takes the CI
#: drift gate down and reports it as a broken runner rather than a hostile
#: input. `packs/elixir.py` already catches `RecursionError` beside its own
#: `_ElixirScrubBail`, and its docstring names this failure class.
#:
#: **Spelled HERE, in core, rather than in one pack.** It lived in
#: `packs/python.py` and covered the five `ast.parse` sites that module had
#: then. It has six handler sites now, because the `tomllib` one below joined
#: them with this move. The crux pack's
#: `_cli_verbs` then became a sixth site in another module, could not see the
#: constant, and shipped catching `SyntaxError` alone — the exact regression the
#: paragraph above forbids, reintroduced because the contract was spelled inside
#: one of the two modules that owe it. One name, one tuple, every pack.
#:
#: At the sites with a residual channel, a file caught here is HASHED but not
#: parsed, and named in the concern's residuals — the honest stub — rather than
#: dropped silently or crashed on. The two halves are one contract, not two
#: courtesies: `inputs_found` is `sorted(sources)`, and `staleness.py` walks
#: `inputs_found` to decide whether a spine file has fallen behind. A path named
#: in the prose and missing from `sources` is invisible there, so editing a
#: refused file while leaving it broken changes no rendered byte and trips no
#: gate. `packs/python.py`'s `_scan_sqlalchemy_models` states the coupling for
#: the three data-model scans; `_RouteScan.sources` states it for api-surface.
#:
#: **The consumers are not all `ast.parse`.** Two of them parse something else,
#: and both were added when this constant moved here: `packs/python.py`'s
#: `tomllib.loads` of `pyproject.toml` — `tomllib` is recursive-descent too, and
#: 2000 nested brackets raise `RecursionError` past a `(ValueError, TypeError)`
#: handler — and `core._is_manifest_drifted`'s `json.loads`/`json.dumps` pair.
#: `runtime/capture.py` needs the same four and spells them literally instead of
#: importing this name, because that module is loaded by path under the TARGET
#: project's interpreter and must not acquire a module-level import from `core`.
#:
#: **The tree-sitter parses are NOT enrolled, and that is a recorded residual
#: rather than an oversight.** `packs/node.py::_js_parse`,
#: `packs/elixir.py::_elixir_parse` and ruby's `_ruby_ts_parser().parse` each run
#: outside any handler, and `core` calls each extractor unguarded, so anything
#: they raise becomes exit 2 with empty stdout — the failure this constant
#: exists to prevent, arriving through a different parser. It is not reachable
#: today: `_MAX_FILE_BYTES` caps input at 2 MB, tree-sitter's LR parse is
#: iterative so it raises no `RecursionError`, and py-tree-sitter represents
#: malformed input as `ERROR` nodes rather than exceptions. The residual is
#: `MemoryError`, plus any exception a future py-tree-sitter adds. Closing it
#: means each wrapper returning None and every caller in three packs handling
#: that and recording an `_unparsed_residual`, which is a behaviour change across
#: many call sites rather than one handler — filed as a follow-on instead of
#: rushed in beside an unrelated fix.
#:
#: The Swift pack is not a further site of this residual: it catches every
#: exception at its own per-file boundary and renders the file as a residual
#: there, so nothing from its tree-sitter parse reaches this constant at all.
#:
#: Three sites have no residual channel and no `sources` of their own —
#: `packs/python.py::_setup_py_pkg_name` and `packs/python.py::_pyproject_pkg_name`,
#: which each answer one yes/no question, and `packs/crux.py::_cli_verbs`, whose
#: whole contract is to return the verb set it could read. None names anything,
#: so none hashes anything; falling through to the empty answer is the honest
#: stub at all three.
#:
#: `module-graph` is not a further site. `core._extract_module_graph` never calls
#: `ast.parse` — it is a regex pass over the file text — so it has no refusal to
#: report, and it hashes every file it reads whether or not any OTHER concern
#: could parse it. Naming a refusal there would be inventing one.
_PARSE_FAILED = (SyntaxError, ValueError, RecursionError, MemoryError)


@functools.lru_cache(maxsize=1)
def _retained_source_readers():
    """Load trusted stdlib readers without importing the package or held code."""
    scripts = Path(__file__).resolve().parents[2]
    readers = []
    for name in ("bionic_config", "retained_evidence"):
        spec = importlib.util.spec_from_file_location("_arch_source_" + name, scripts / (name + ".py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        readers.append(module)
    return tuple(readers)


def _retained_source_filter(root: Path, *, discovery: bool = True):
    """Resolve only the containing project; do not cache its mutable layout."""
    config, retained = _retained_source_readers()
    docs = config.load_config(root).docs_root
    if discovery:
        retained.retained_evidence_roots(docs)
    return lambda path: retained.is_retained_evidence_path(docs, Path(path).absolute())


def _retained_source_resolver(root: Path):
    """Admit ordinary source paths without entering retained repository content."""
    config, retained = _retained_source_readers()
    repo = Path(root).absolute()
    docs = config.load_config(repo).docs_root
    retained.retained_evidence_roots(docs)
    return lambda path: retained.resolve_source_path(repo, docs, Path(path).absolute())


def _retained_source_walk(root: Path, base: Path):
    """Prune holdings before descent; callers retain their own source filters."""
    held = _retained_source_filter(root)
    if not _contained(root, base) or held(base):
        return
    for directory, children, files in os.walk(base, followlinks=False):
        remaining = []
        for name in children:
            path = Path(directory) / name
            if path.is_symlink() and not _contained(root, path):
                continue
            if not held(path):
                remaining.append(name)
        children[:] = remaining
        remaining_files = []
        for name in files:
            path = Path(directory) / name
            # Existing file escape/read diagnostics belong to the shared reader.
            if not _contained(root, path):
                remaining_files.append(name)
                continue
            if path.is_symlink() and not path.is_file():
                # Missing targets still reach the shared read diagnostics.
                # Preserve the skip for loops and other non-file links.
                try:
                    path.stat()
                except FileNotFoundError:
                    pass
                except OSError:
                    continue
                else:
                    continue
            if not held(path):
                remaining_files.append(name)
        yield directory, children, remaining_files


def _iter_py_files(root: Path):
    """Yield every `.py` file under `root`, skipping VCS/venv/build dirs and any
    dot-directory. Deterministic ordering is the caller's concern.

    `followlinks=False` is `os.walk`'s default and is stated explicitly, as the
    other five packs' walkers state it: ADR-0068 and ADR-0069 clause 6 name the
    behaviour, so it is spelled rather than inherited. A symlinked FILE is still
    yielded — containment is `_safe_read_bytes`'s job at the read, which is why
    every caller of this generator goes through it."""
    for dirpath, dirnames, filenames in _retained_source_walk(root, root):
        dirnames[:] = [
            d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")
        ]
        for fn in filenames:
            if fn.endswith(".py"):
                yield Path(dirpath) / fn


_MAX_FILE_BYTES = 2 * 1024 * 1024      # 2 MB per-file size bound (point 8).


def _contained(root: Path, p: Path) -> bool:
    """True when `p`, fully resolved, stays under the resolved repo root (point 8).
    A symlink whose target escapes the root resolves outside and returns False, so
    it is neither read nor hashed.

    A path that cannot be resolved is not contained. `Path.resolve()` raises
    `OSError` on an unreadable component, and before Python 3.13 it raises
    `RuntimeError` on a symlink loop, which a committed file can create."""
    try:
        rp = p.resolve()
        root_r = root.resolve()
    except (OSError, RuntimeError):
        return False
    return rp == root_r or rp.is_relative_to(root_r)


def _safe_read_bytes(
    root: Path,
    path: Path,
    oversize: list,
    *,
    max_bytes: int = _MAX_FILE_BYTES,
    refusal: list | None = None,
) -> bytes | None:
    """Read `path` iff it is a REGULAR file contained under `root` and within
    `max_bytes` (point 8; default `_MAX_FILE_BYTES`, the existing 2 MB bound).
    An oversize file's repo-relative path is appended to `oversize` (a
    recorded residual) and None returned; an escaping, non-regular or
    unreadable path yields None silently (not read, not hashed).

    `max_bytes` and `refusal` are keyword-only, additive parameters: the
    Swift pack's Xcode reader passes them for ADR-0130 clause 2's
    dedicated `project.pbxproj` bound and its refusal kinds. A caller that
    passes only `root`, `path` and `oversize` positionally, as every other
    pack does, sees byte-identical behaviour: `max_bytes` defaults to the
    existing bound and `refusal` defaults to `None`, under which nothing is
    recorded, exactly as before.

    `refusal` is an OUT-parameter, in the same shape as `oversize`: the
    caller passes a list and this function appends exactly one reason to it
    on refusal, never on success. The closed reason set:

    * `escape` — `path` is not contained under `root` (`_contained` refused
      it), including a symlinked component that resolves outside;
    * `not-regular` — the final component is not a regular file: a symlink
      (`O_NOFOLLOW` raises `ELOOP`, which counts here), a FIFO, a device, a
      socket or a directory;
    * `read-failed` — any other OS error opening, stat-ing or reading the
      file;
    * `oversize` — over `max_bytes`, checked both from `st_size` before any
      byte is read and from the bytes actually read, exactly as the
      existing two-point size check already did.

    **Every read in this engine goes through here, so the three guarantees below
    are the engine's, not this function's.**

    *Regular-file only.* A FIFO inside the repository is contained, reports
    `st_size` 0, and blocks the whole derive forever inside `open(2)` waiting for
    a writer that never comes. A device node, a socket and a directory are the
    same class of thing: a path that is not a file of bytes is not an input, and
    `S_ISREG` is the check that says so. `O_NONBLOCK` is what makes the FIFO case
    reach that check at all — without it the hang happens in the open, before any
    `fstat` can rule the path out.

    *One resolve, not three.* The previous form resolved the path in
    `_contained`, again in `stat()`, and a third time in `read_bytes()`, so a
    symlink swapped in between any two of them was checked at one target and read
    at another. Everything after the open here reads the DESCRIPTOR — `fstat` and
    `os.read` both address the file the open returned — so the file that passed
    the checks is the file whose bytes come back.

    *No symlink at the final component.* `O_NOFOLLOW` refuses one outright rather
    than resolving it. `_contained` already refused a symlink escaping the root;
    this refuses the swap that `_contained` cannot see, at the cost of declining a
    symlinked file that points back inside the root — a shape no shipped pack's
    walk depends on, and the safe side of the trade.

    The size bound is enforced twice on purpose: once from `st_size` before any
    byte is read, which is what stops a 2 GB ADR from being resident, and once
    against the bytes actually returned, because `st_size` is a snapshot and a
    file being appended to can outrun it.
    """
    if not _contained(root, path):
        if refusal is not None:
            refusal.append("escape")
        return None
    if _retained_source_filter(root, discovery=False)(path):
        return None
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        if refusal is not None:
            refusal.append("not-regular" if exc.errno == errno.ELOOP else "read-failed")
        return None
    try:
        try:
            st = os.fstat(fd)
        except OSError:
            if refusal is not None:
                refusal.append("read-failed")
            return None
        if not stat.S_ISREG(st.st_mode):
            if refusal is not None:
                refusal.append("not-regular")
            return None
        if st.st_size > max_bytes:
            _note_oversize(root, path, oversize)
            if refusal is not None:
                refusal.append("oversize")
            return None
        try:
            os.set_blocking(fd, True)
            chunks: list[bytes] = []
            total = 0
            while total <= max_bytes:
                block = os.read(fd, 1 << 20)
                if not block:
                    break
                chunks.append(block)
                total += len(block)
        except OSError:
            if refusal is not None:
                refusal.append("read-failed")
            return None
    finally:
        os.close(fd)
    if total > max_bytes:
        _note_oversize(root, path, oversize)
        if refusal is not None:
            refusal.append("oversize")
        return None
    return b"".join(chunks)


def _note_oversize(root: Path, path: Path, oversize: list) -> None:
    """Record an over-bound path once, as a repo-relative residual. A path that
    cannot be resolved records nothing (see `_contained`)."""
    try:
        rel = _rel(root, path)
    except (OSError, RuntimeError, ValueError):
        return
    if rel not in oversize:
        oversize.append(rel)


def _universal(text: str) -> str:
    """Universal-newline translation, exactly as `read_text` applies it.

    Every read that used to go through `Path.read_text` got this for free, and
    losing it when those reads moved to `_safe_read_bytes` would be a silent
    behaviour change rather than a safety fix. Two places depend on it directly:
    a projection compares its input against a regenerator's LF output and must
    not call a CRLF-checked-out file stale, and `_frontmatter`'s `^---\\n` anchor
    does not match `---\\r\\n`.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _read_gated(root: Path, path: Path) -> str:
    """`_read`'s gated replacement: the text of a contained, regular, in-bound
    file, or `""` for anything else.

    `""` is the same answer `_read` gave for an absent file, and it is the right
    answer for the new refusals too. Every caller is comparing against content
    this derive would write, and content this derive would write is never empty —
    so a FIFO, a symlink, a directory or an over-bound file at a spine path all
    compare unequal and report drift, which is what they are.
    """
    raw = _safe_read_bytes(root, path, [])
    return "" if raw is None else _universal(raw.decode("utf-8", errors="replace"))


# ═══════════════════════════════════════════════════════════════════════════
# U2 — the SHARED EXTRACTOR LAYER (ADR-0096 clause 12)
# Everything below is used by more than one pack, or by every pack. It moved
# here as a PURE MOVE; not a line was rewritten in transit, because clause 12's
# gate is byte-identity across the corpus goldens.
#
# `extract_decision_index` is the one UNIVERSAL probe: the ADR tree is stack-
# independent (ADR-0060) and the extractor self-degrades to the empty-but-valid
# marker when no ADR tree exists, so every pack — including the empty stub pack —
# keeps a working decision index. It lives here rather than being duplicated per
# pack, and rather than earning a sixth module. Its `recover` import stays
# FUNCTION-LEVEL: a module-level `from .recover import curated_keep` would break
# `runtime/capture.py::_load_cell`, which file-loads this module outside the
# package. See this module's docstring.
# ═══════════════════════════════════════════════════════════════════════════

# ────────────── projections of gated artifacts (ADR-0079) ───────────────────
# Two spine files used to answer a question the tree already answers elsewhere.
# `api-surface.md` re-parsed the skill frontmatter that `validate-catalog.py`
# projects into `crux/catalog/skills.json`; `decision-index.md` re-parsed the ADR
# frontmatter that `generate-adr-index.py` projects into `<docs_dir>/adrs/
# index.md`. Each fact had two parsers and nothing compared their answers, so the
# two surfaces could disagree with no gate noticing.
#
# Three clauses, and the code below is exactly their shape:
#   1. Where the tree already regenerates a fact into an artifact carrying both a
#      regenerator and a drift gate, the spine PROJECTS that artifact.
#   2. A projection is fail-closed on a stale input: it is emitted only when the
#      input's OWN drift check reports clean. On drift the derive REFUSES, leaves
#      the spine on disk untouched, and names the input.
#   3. An input that is absent, field-omitting, or whose check crux cannot run
#      disqualifies itself, and the spine derives that file from the source of
#      truth as it did before. That is a different case from clause 2 because the
#      fallback IS the source of truth: an absent check leaves freshness unknown
#      and the source settles it, whereas a check reporting drift has settled it.
#
# Each rendered file names the input it used in its own first line, so a reader
# never has to guess which of the two paths produced what they are reading.


class StaleProjectionInput(RuntimeError):
    """Clause 2's refusal: an input artifact's own drift check reported drift.

    Raised out of an extractor, so it escapes `_build` before `derive` writes
    anything and before `dry_run` compares anything — which is what makes
    "leaves the spine on disk unchanged" true by construction rather than by
    care. `derive-arch.py` routes it to its capability lane (exit 2), because a
    refusal is a capability failure and not a document finding: the derive is
    saying it declines to answer, not that the arch tree is stale.

    The message names the input and the remedy, never the diff. A diff would put
    examined-repo bytes into a process output for no gain — the input's own
    regenerator prints one on demand.
    """


# The scripts directory of the RUNNING crux, computed from this module's own
# location and never from the repo under derivation:
# `<plugin_root>/scripts/crux/arch/derive.py`.parents[2] == `<plugin_root>/
# scripts`. That distinction IS clause 2's execution boundary (the property
# ADR-0075 gates). An examined repo may itself carry a `crux/scripts/` tree — the
# crux stack pack fires precisely when it does — and resolving the regenerator
# through `root` would then run the examined repo's code. This resolves through
# crux's own tree instead, so the assertion always runs crux's vendored copy.
_VENDORED_SCRIPTS = Path(__file__).resolve().parents[2]


@functools.lru_cache(maxsize=None)
def _vendored(script: str, modname: str):
    """Import one of crux's OWN vendored regenerators by file location, or None.

    Loading by location rather than by name is forced by the filenames: neither
    `validate-catalog.py` nor `generate-adr-index.py` is a legal module name.
    This is the mechanism the test suite already uses on these same scripts.

    Every failure returns None, which routes the caller to clause 3 rather than
    to a refusal. That direction is deliberate: an unrunnable check leaves
    freshness unknown, and clause 3's fallback is the source of truth, so the
    spine file stays correct. Failing the other way would refuse a derive over a
    missing optional dependency.
    """
    target = _VENDORED_SCRIPTS / script
    if not target.is_file():
        return None
    # Import without caching bytecode beside the regenerator. `exec_module` on a
    # source file writes `__pycache__/` next to it, which lands inside the plugin
    # directory — documented as ephemeral across updates and as holding no state —
    # and makes a `--dry-run` write files while the workflow comment says it
    # writes nothing. Stale bytecode has decided a verdict in this repo before,
    # which is the second reason. The env-var form is the repo's own idiom
    # (`PYTHONDONTWRITEBYTECODE=1` in `sync.sh` and in every gate command).
    prior_dwb = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec = importlib.util.spec_from_file_location(modname, target)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:
        return None
    finally:
        sys.dont_write_bytecode = prior_dwb


def _disqualified(concern: str, reason: str) -> None:
    """Announce a clause-3 fallback taken because a PRESENT input was uncheckable.

    Loud on stderr, like the override guard's warnings, because a silent
    fallback would hide that the projection never happened. Not called when the
    input is simply absent: that is the normal state of a target repo enabling
    arch, and warning on the normal path trains readers to ignore the channel.

    `reason` is a closed literal token the caller chooses — never an exception
    message, a config-derived path, or file content — so no examined-repo bytes
    reach this line. Exception TYPE names are admitted; they come from the
    interpreter, not the repo.
    """
    print(f"crux-arch: {concern} derived from source — {reason}", file=sys.stderr)


def _skills_catalog_projection(root: Path) -> tuple[list, str, str] | None:
    """The projected skill inventory for `api-surface.md`, or None for clause 3.

    Returns `(entries, relpath, sha256)` when `crux/catalog/skills.json` is
    present, carries the fields the spine file needs, and its own drift check
    reports clean. Returns None on every clause-3 condition. Raises
    `StaleProjectionInput` when the check RAN and reported the artifact unclean.

    The check is the skills half of `validate-catalog.py --dry-run`, and clean
    means what that script means by it: the regenerated bytes equal the bytes on
    disk AND the walk produced no validation error (its own `--dry-run` refuses
    both). It is not the whole of that script: the agents half compares
    `crux/catalog/agents.json`, which is not this spine file's input, and
    coupling the derive to it would refuse over an artifact the projection never
    reads.
    """
    cat = root / "crux" / "catalog" / "skills.json"
    if not cat.is_file():
        return None                                      # clause 3: absent
    vc = _vendored("validate-catalog.py", "_crux_arch_catalog_regenerator")
    needed = ("regenerate_skills_json", "serialize_skills_json")
    if vc is None or not all(hasattr(vc, n) for n in needed):
        _disqualified("api-surface", "no runnable drift check for crux/catalog/skills.json")
        return None                                      # clause 3: no check
    try:
        entries, errors = vc.regenerate_skills_json(root / "crux", False)
        want = vc.serialize_skills_json(entries)
        # Gated like every other read: an input that is not a contained, regular,
        # in-bound file is one this projection cannot compare, and an
        # unread-therefore-different byte string would refuse as clause 2 STALE
        # over a file it never opened. Clause 3 is the honest lane for it.
        raw = _safe_read_bytes(root, cat, [])
        if raw is None:
            _disqualified(
                "api-surface",
                "crux/catalog/skills.json is not a readable, in-bound regular file",
            )
            return None                                  # clause 3: unreadable
        # `_universal` because the previous `read_text` applied universal-newline
        # translation, and the regenerator this is compared against emits LF. Without
        # it a CRLF checkout refuses its own clean index as stale.
        have = _universal(raw.decode("utf-8"))   # strict: UnicodeDecodeError is clause 3
    except Exception as exc:                             # clause 3: check unrunnable
        _disqualified(
            "api-surface",
            f"drift check for crux/catalog/skills.json raised {type(exc).__name__}",
        )
        return None
    # Outside the try on purpose: a refusal must never be reachable from an
    # `except` that also catches the unrunnable case, or a stale input could be
    # downgraded into a silent fallback.
    #
    # The two unclean causes are reported SEPARATELY because they have different
    # remedies, and the previous single message named the wrong one for half its
    # callers. `validate-catalog.py --dry-run` refuses on either, so both are
    # "not clean" under clause 2 and both refuse — but a validation error can
    # hold with the bytes BYTE-IDENTICAL, and telling that caller its bytes
    # differ sent them to re-run a regenerator that rewrites nothing and reports
    # the same error again. That remedy was a loop.
    if errors:
        raise StaleProjectionInput(
            "crux/catalog/skills.json cannot be trusted as an input: its own "
            "regenerator reported validation errors while walking "
            "crux/skills/*/SKILL.md, so the drift check reached no verdict about "
            "the catalog. Refusing to project an unvalidated input into "
            "arch/api-surface.md. Run `validate-catalog.py`, fix the frontmatter "
            "errors it reports, then re-derive. The catalog's own bytes may "
            "already be correct; regenerating alone will not clear this."
        )
    if want != have:
        raise StaleProjectionInput(
            "crux/catalog/skills.json is not what its own regenerator produces; "
            "refusing to project a stale input into arch/api-surface.md. Run "
            "`validate-catalog.py`, commit the result, then re-derive."
        )
    try:
        on_disk = json.loads(have)
    except Exception:                                    # unreachable while want==have
        _disqualified("api-surface", "crux/catalog/skills.json is not readable JSON")
        return None
    if not isinstance(on_disk, list):
        _disqualified("api-surface", "crux/catalog/skills.json is not a list of entries")
        return None
    # Clause 1's postcondition: an input omitting a field the spine file needs
    # disqualifies itself under clause 3 rather than narrowing the spine file.
    for entry in on_disk:
        if not isinstance(entry, dict) or "name" not in entry or "description" not in entry:
            _disqualified(
                "api-surface",
                "crux/catalog/skills.json omits a field api-surface.md needs",
            )
            return None
    # Hashed from the bytes the check compared, not from a second read. A
    # re-read would let the recorded hash describe content the assertion
    # never saw, which is the TOCTOU the override guard names one screen up.
    return on_disk, _rel(root, cat), _sha256_hex(have.encode("utf-8"))


# The seven columns `generate-adr-index.py` renders for the active tier, in
# order. `tags` is the one clause 1 calls out by name: the curated
# decision-index mode filters on it, so an index without it is field-omitting
# and disqualifies itself rather than silently producing an unfiltered file.
_ADR_INDEX_COLUMNS = ("id", "title", "status", "date",
                      "supersedes", "superseded_by", "tags")


# The archived roster's heading carries a count, which moves with archive
# membership rather than with the index's SHAPE. Normalized out of the shape
# signature below so archiving an ADR without re-running the regenerator stays a
# staleness question and does not read as a different kind of document.
_ARCHIVED_HEADING_RE = re.compile(r"^## Archived \(\d+\)$")

# An ADR id, in both spellings §14.3 sanctions: bare (`ADR-0012`) and carrying
# the repo's configured artifact prefix (`CRX-ADR-0012`). The prefixed form is
# not hypothetical — §14.3 defines it, `.bionic.yml` configures it, and the
# prefixed string IS the id "verbatim on every surface", ADR frontmatter
# included. A bare `^ADR-(\d+)$` rejected every prefixed id, which disqualified
# the whole index in any repo that set a prefix.
#
# §14.3 spells the digit group `(\d{4})`. It stays `(\d+)` here so this match
# adds prefix tolerance and nothing else: tightening the digit count in the same
# edit would introduce a rejection class that has nothing to do with the finding.
_ADR_ID_RE = re.compile(r"^(?:[A-Z][A-Z0-9]{1,9}-)?ADR-(\d+)$")
# The same two spellings as a FILENAME prefix, for the source-of-truth path.
# Unanchored at the tail, because a filename continues into its slug.
_ADR_FILENAME_RE = re.compile(r"^(?:[A-Z][A-Z0-9]{1,9}-)?ADR-(\d+)")


def _adr_index_shape(text: str) -> tuple[str, ...]:
    """The structural signature of an ADR index: its non-row, non-blank lines.

    Table rows are dropped because they are the index's CONTENT, and content is
    what staleness is about. Blank lines are dropped so trailing-newline
    variance is not mistaken for a different document. The archived roster's
    count is normalized away for the reason stated above the regex.
    """
    sig = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("|"):
            continue
        sig.append(_ARCHIVED_HEADING_RE.sub("## Archived (N)", stripped))
    return tuple(sig)


def _index_is_regenerator_shaped(have: str, want: str) -> bool:
    """True when `have` has the STRUCTURE `want` has, ignoring row content.

    This is the test for "does this tree maintain its ADR index with the
    regenerator?", and it exists to separate two states the byte comparison
    alone conflates.

    A tree that DOES maintain the index with the regenerator, and whose index
    has fallen behind, produces the regenerator's structure with different rows.
    That is staleness, and clause 2 refuses it.

    A tree that has NEVER run the regenerator produces a different structure
    entirely. `init-docs` writes a `_Last updated:` line and no `## Archived (N)`
    roster; `propose-adr` maintains that same shape on every append. Neither is
    stale — the index is exactly what its tree intends — and refusing it made
    the FIRST derive in a freshly initialized repository exit 2. Arch is
    default-on for new repos, so that refusal met every downstream reader on day
    one, and it falsified clause 3's postcondition that "enabling arch in a
    target repo requires no artifact that repo lacks". Such a tree carries no
    check that applies to this artifact, which is a clause-3 disqualifier, and
    the spine derives the file from ADR frontmatter instead.

    The predicate is computed from the regenerator's OWN output rather than from
    a hardcoded signature, so a future change to `render()` moves both sides
    together and cannot leave this check asserting a shape the regenerator
    stopped producing.

    A tree that adopted the regenerator and then hand-broke the shape falls to
    the second case and derives from source. That direction is deliberate: the
    fallback IS the source of truth, so the spine file stays correct, and the
    misedit is still reported elsewhere — the spine file's own provenance line
    flips, which moves bytes the arch drift gate compares.

    A claim that `generate-adr-index.py --dry-run` also reports it was cut from
    this docstring. That check runs in no venue here. Citing it was circular,
    because the missing venue is the gap clause 2 exists to fill.
    """
    return _adr_index_shape(have) == _adr_index_shape(want)


def _split_md_row(line: str) -> list[str] | None:
    """Split one pipe-table row into its cells, or None if it is not one.

    Returns None rather than a best guess for any row whose cell count differs
    from the declared column count — a title containing an unescaped `|` is the
    real instance, and the caller turns None into a clause-3 fallback. Guessing
    would silently drop or merge a decision.
    """
    if not line.startswith("|") or not line.rstrip().endswith("|"):
        return None
    parts = line.rstrip().split("|")
    if len(parts) != len(_ADR_INDEX_COLUMNS) + 2:        # leading + trailing empty
        return None
    return [p.strip() for p in parts[1:-1]]


def _adr_index_projection(root: Path, docs_dir: str) -> tuple[list, str, str] | None:
    """The projected accepted-decision list for `decision-index.md`, or None.

    Returns `(records, relpath, sha256)` where each record is
    `(num, title, date, tags)`. Returns None on every clause-3 condition, and
    raises `StaleProjectionInput` when the index's own drift check reported it
    stale.

    Only the ACTIVE table is read: parsing stops at the first `## ` heading,
    which is the `## Archived (N)` roster. That keeps clause 1's postcondition —
    the decision index still excludes archived decisions, which the ADR index
    carries in a roster of its own.
    """
    index_path = root / docs_dir / "adrs" / "index.md"
    if not index_path.is_file():
        return None                                      # clause 3: absent
    gai = _vendored("generate-adr-index.py", "_crux_arch_adr_index_regenerator")
    if gai is None or not hasattr(gai, "build"):
        _disqualified("decision-index", "no runnable drift check for the ADR index")
        return None                                      # clause 3: no check
    try:
        built_path, want = gai.build(root)
        # Gated like every other read, for the reason the skills-json projection
        # states: an unread input compared as a differing byte string would
        # refuse as clause 2 STALE over a file this never opened.
        raw = _safe_read_bytes(root, index_path, [])
        if raw is None:
            _disqualified(
                "decision-index",
                "the ADR index is not a readable, in-bound regular file",
            )
            return None                                  # clause 3: unreadable
        # `_universal` because the previous `read_text` applied universal-newline
        # translation, and the regenerator this is compared against emits LF. Without
        # it a CRLF checkout refuses its own clean index as stale.
        have = _universal(raw.decode("utf-8"))   # strict: UnicodeDecodeError is clause 3
    except Exception as exc:                             # clause 3: check unrunnable
        _disqualified(
            "decision-index",
            f"drift check for the ADR index raised {type(exc).__name__}",
        )
        return None
    # The regenerator resolves the tree name from `.bionic.yml` itself, so it can
    # target a different tree than this derive was asked for (`--docs-dir`
    # overrides, a multi-tree checkout). A check aimed at another file proves
    # nothing about this one, so decline rather than trust it.
    if Path(built_path).resolve() != index_path.resolve():
        _disqualified("decision-index", "the ADR index regenerator targets a different tree")
        return None
    if want != have:
        # Two states, one byte comparison. `_index_is_regenerator_shaped` carries
        # the distinction and the reasoning; the short form is that a differing
        # index in a tree that maintains it with the regenerator is STALE, and a
        # differing index in a tree that does not is simply not this
        # regenerator's artifact.
        if not _index_is_regenerator_shaped(have, want):
            # SILENT, unlike every other clause-3 branch in this function, and
            # the asymmetry is the same one `_disqualified` already draws for an
            # absent input: a hand-maintained ADR index is the NORMAL state of a
            # target repo, because `init-docs` writes it and nothing obliges that
            # repo to adopt the regenerator. Warning on the normal path is what
            # trains a reader to ignore the channel, and it would cost the loud
            # branches below their meaning.
            #
            # Silence is safe here because two other reporters already carry the
            # signal, and neither is stderr. The rendered file names the input it
            # used, so a projection that stops flips `decision-index.md`'s
            # provenance line; that line is inside the hashed spine, so the flip
            # moves bytes the drift gate compares and fails on. In a tree that
            # never enrolled the artifact there is nothing to report at all,
            # because clause 3's fallback is the source of truth.
            return None                              # clause 3: no check applies
        raise StaleProjectionInput(
            "the ADR index is not what its own regenerator produces; refusing to "
            "project a stale input into arch/decision-index.md. Run "
            "`generate-adr-index.py`, commit the result, then re-derive."
        )

    active = have.split("\n## ")[0]
    rows = []
    header_seen = False
    for line in active.splitlines():
        cells = _split_md_row(line)
        if cells is None:
            if line.startswith("|"):                     # clause 3: unparseable table
                _disqualified("decision-index",
                              "an ADR index row does not split into its declared columns")
                return None
            continue
        if not header_seen:
            if [c.lower() for c in cells] != list(_ADR_INDEX_COLUMNS):
                # Field-omitting or reordered: disqualify, never reinterpret.
                _disqualified("decision-index", "the ADR index header is not the expected columns")
                return None
            header_seen = True
            continue
        if set("".join(cells)) <= set("-: "):            # the header separator row
            continue
        rows.append(cells)
    if not header_seen:
        _disqualified("decision-index", "the ADR index carries no active table")
        return None

    records = []
    for cells in rows:
        cell = dict(zip(_ADR_INDEX_COLUMNS, cells))
        num = _ADR_ID_RE.match(cell["id"])
        if num is None:                                  # clause 3: unrecognized id
            _disqualified("decision-index", "an ADR index row carries an unrecognized id")
            return None
        if cell["status"] != "Accepted":
            continue
        tags = [] if cell["tags"] in ("", "—") else [
            t.strip() for t in cell["tags"].split(",") if t.strip()
        ]
        records.append((num.group(1), cell["title"], cell["date"], tags))
    return records, _rel(root, index_path), _sha256_hex(have.encode("utf-8"))


def _extract_module_graph(root: Path, pkg: Path, pkg_name: str) -> tuple[str, dict]:
    """Intra-package import graph of the Python package `pkg` (import name
    `pkg_name`), rendered deterministically (ADR-0066 points 8/10). Regex-escapes
    `pkg_name` in the two absolute-import patterns; relative-import resolution is
    package-name-independent."""
    sources: dict = {}
    edges: set = set()
    modules: set = set()
    # Pass 1: enumerate modules (so relative `from . import X` can resolve X against
    # the known set), recording each file's module name, package path, and text.
    files: list[tuple[str, str, str]] = []
    for py in sorted(Path(directory) / name
                     for directory, _children, files in _retained_source_walk(root, pkg)
                     for name in files if name.endswith(".py")):
        if "__pycache__" in py.parts:
            continue
        # `rglob` does not recurse into a symlinked directory (measured on 3.13),
        # but it does yield a symlinked FILE, so containment is enforced here at
        # the read rather than at the walk. One
        # gated read serves both the hash and pass 2's text, so this engine has
        # exactly one way into a file. Ungated, `_rel` on the next line raised
        # on a path resolving outside the root and took the whole derive down.
        raw = _safe_read_bytes(root, py, [])
        if raw is None:
            continue
        sources[_rel(root, py)] = _sha256_hex(raw)
        rel_parts = py.relative_to(pkg).with_suffix("").parts
        mod = ".".join(rel_parts).replace(".__init__", "") or "__init__"
        pkg_path = ".".join(py.relative_to(pkg).parts[:-1])   # containing package
        modules.add(mod)
        files.append((mod, pkg_path, raw.decode("utf-8", errors="replace")))

    # Pass 2: absolute (<pkg_name>.X) AND relative (from .X / ..X / from . import X)
    # imports. `pkg_name` is regex-escaped so a dotted or punctuated package name
    # cannot corrupt the pattern (ADR-0066 point 8).
    esc = re.escape(pkg_name)
    # Two shapes, and both matter for how long this loop runs.
    #
    # `[ \t]` rather than `\s`: under `re.MULTILINE` a `\s` run crosses newlines,
    # so `^\s*` could start a match lines above the import it eventually matched
    # and re-try that start position for every blank line above it. Horizontal
    # whitespace is all an import statement can actually carry.
    #
    # `([A-Za-z0-9_][A-Za-z0-9_.]*)?` rather than `([A-Za-z0-9_.]*)`: the old
    # second group could match a `.`, which the FIRST group `(\.+)` also matches,
    # so a run of dots had exponentially many splits between the two groups and
    # the engine tried them all before failing. Measured on a 60 KB adversarial
    # input: 9.81 s in this one `finditer`, and 28.8 s end-to-end on a single
    # 100 KB file against a 0.12 s baseline — quadratic in the run length, with
    # `_MAX_FILE_BYTES` admitting 2 MB. Requiring the second group to START with
    # a non-dot makes the split unambiguous: every dot belongs to group 1.
    for mod, pkg_path, text in files:
        for m in re.finditer(rf"^[ \t]*from[ \t]+{esc}\.([A-Za-z0-9_.]+)[ \t]+import", text, re.MULTILINE):
            edges.add((mod, m.group(1)))
        for m in re.finditer(rf"^[ \t]*import[ \t]+{esc}\.([A-Za-z0-9_.]+)", text, re.MULTILINE):
            edges.add((mod, m.group(1)))
        for m in re.finditer(r"^[ \t]*from[ \t]+(\.+)([A-Za-z0-9_][A-Za-z0-9_.]*)?[ \t]+import[ \t]+([^\n#]+)", text, re.MULTILINE):
            dots, sub, names = m.group(1), m.group(2), m.group(3)
            base = pkg_path.split(".") if pkg_path else []
            strip = len(dots) - 1                              # `.`=current pkg, `..`=parent
            if strip:
                base = base[:-strip] if len(base) >= strip else []
            if sub:                                            # from .sub import ...
                target = ".".join([*base, *sub.split(".")])
                if target and target != mod:
                    edges.add((mod, target))
            else:                                              # from . import X, Y
                for nm in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", names.split(" as ")[0]):
                    target = ".".join([*base, nm])
                    if target in modules and target != mod:    # only real sibling modules
                        edges.add((mod, target))

    rel_pkg = _rel(root, pkg)
    out = ["# Module graph", "",
           f"_Intra-package imports of `{_cell(pkg_name)}` under `{_cell(rel_pkg)}/` "
           f"({len(modules)} modules, {len(edges)} edges)._", "",
           "```mermaid", "graph LR"]
    for a, b in sorted(edges):
        na = re.sub(r"[^A-Za-z0-9_]", "_", a)
        nb = re.sub(r"[^A-Za-z0-9_]", "_", b)          # full module id, not the first path part
        out.append(f"  {na}[{a}] --> {nb}[{b}]")
    out += ["```", ""]
    if not edges:
        out += ["_No intra-package import edges detected._", ""]
    # Isolated modules (no intra-package edge) — listed so all N modules are represented,
    # not only the edge-participating ones.
    connected = {a for a, _ in edges} | {b for _, b in edges}
    isolated = sorted(m for m in modules if m not in connected)
    if isolated:
        out += [f"## Isolated modules ({len(isolated)})", "",
                "_No intra-package import edge (leaf or standalone):_", "",
                ", ".join(f"`{_cell(m)}`" for m in isolated), ""]
    return _canon(out), sources


@functools.lru_cache(maxsize=1)
def _decision_history_reader():
    """Load the mandatory trusted reader; optional projection checks are separate."""
    spec = importlib.util.spec_from_file_location(
        "_crux_arch_summary_reader", _VENDORED_SCRIPTS / "summarize-adrs.py")
    if spec is None or spec.loader is None:
        raise OSError("architectural authority reader is unavailable")
    prior = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        driver = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(driver)
        return driver
    finally:
        sys.dont_write_bytecode = prior


def _decision_history(root: Path, docs_dir: str) -> tuple[list[dict], dict, dict]:
    """Read proved clause history before either architectural input path."""
    driver = _decision_history_reader()
    import implementation_migration as migration
    view = migration.authority_view(root)
    if view["state"] == "published" and driver.sp.resolve_tree(root).resolve() != (root / docs_dir).resolve():
        raise OSError("architectural history belongs to a different configured tree")
    clauses, _ = driver._historical_summary_clauses(root, view)
    sources = {ref["path"]: ref["sha256"] for ref in view["dependency_fingerprints"]}
    sources.update({ref["path"]: ref["sha256"] for ref in view["publications"]})
    return clauses, sources, view


def _architectural_frontmatter(text: str) -> dict:
    front = _frontmatter(text)
    if "record_type" in front:
        raise ValueError("typed implementation evidence cannot supply architectural decisions")
    return front


def extract_decision_index(root: Path, docs_dir: str, mode: str = "complete") -> tuple[str, dict]:
    """Decision index = Accepted, non-archived ADRs, footnote-only. `complete`
    (ADR-0060 default) lists all; `curated` (ADR-0062) keeps load_bearing +
    unclassifiable and excludes not_load_bearing (fail-open). No ADR number inline.

    The accepted-decision list is PROJECTED from `<docs_dir>/adrs/index.md` when
    that artifact is present and its own drift check reports clean (ADR-0079
    clauses 1-2), and derived from ADR frontmatter when it is not (clause 3).
    The projection is stack-portable for the same reason this whole extractor is:
    the ADR tree and its index are stack-independent.
    """
    from .recover import curated_keep  # noqa: E402
    import implementation_migration as migration
    if migration._decision_index_applicability(root, docs_dir) == "ABSENT":
        return _canon(["# Decision index", "", NO_EXTRACTOR.rstrip("\n")]), {}
    # This gate stays outside _adr_index_projection's optional-input catches.
    # A failed publication proof is never permission to restore old authority.
    clauses, sources, authority = _decision_history(root, docs_dir)
    # Every ADR read below goes through `_safe_read_bytes`, and it has to: this is
    # the UNIVERSAL probe, bound for every pack, so an unbounded read here is an
    # unbounded read in every repository crux touches. `_rel` cannot stand in for
    # the guard — `d[_rel(root, ap)] = _sha256_hex(ap.read_bytes())` evaluates the
    # read first, so the whole file is resident before `_rel` gets a chance to
    # object. A single 2 GB ADR drove 4.33 GB RSS through that shape.
    oversize: list = []
    adrs_dir = root / docs_dir / "adrs"
    if not adrs_dir.exists():
        return _canon(["# Decision index", "", NO_EXTRACTOR.rstrip("\n")]), sources

    projected = _adr_index_projection(root, docs_dir)
    if projected is not None:
        records, _index_rel, index_hash = projected
        # `adrs/index.md` rather than the full repo-relative path: the tree name
        # comes from config a repo controls, and this line is rendered markdown.
        # The literal needs no sanitizing and the file's own location already
        # says which tree it belongs to.
        origin = ", projected from `adrs/index.md`"
        sources[_index_rel] = index_hash
        # Provenance for what the assertion read — see `extract_api_surface`.
        # Both tiers, because the regenerator walks both to build the index.
        for ap in sorted(adrs_dir.glob("ADR-*.md")):
            raw = _safe_read_bytes(root, ap, oversize)
            if raw is None:
                continue
            _architectural_frontmatter(_universal(raw.decode("utf-8", errors="replace")))
            sources[_rel(root, ap)] = _sha256_hex(raw)
        for ap in sorted((adrs_dir / "archive").glob("ADR-*.md")):
            raw = _safe_read_bytes(root, ap, oversize)
            if raw is None:
                continue
            sources[_rel(root, ap)] = _sha256_hex(raw)
        rows = [(num, title, date) for num, title, date, tags in records
                if mode != "curated" or curated_keep(tags)]
    else:
        origin = ", derived from ADR frontmatter"
        rows = []
        # RESIDUAL, named rather than fixed here: this glob and the two
        # provenance globs above select `ADR-*.md` only, so a repo that sets
        # `artifact_prefix` (§14.3) has its ADRs named `<PFX>-ADR-NNNN-*.md` and
        # this walk finds none of them. The id regexes below now read both
        # spellings, which is what the finding asked for and what the projected
        # path needs, since its ids come from index cells rather than filenames.
        # Widening the glob changes which files enter `sources` and therefore the
        # spine hash of a prefixed tree, so it is a separate change with its own
        # tests, filed as a follow-on. This tree sets `artifact_prefix: ""`, so
        # both spellings select the same files here and nothing is masked by it.
        for ap in sorted(adrs_dir.glob("ADR-*.md")):
            # One gated read serves both the frontmatter parse and the hash, so
            # this path has exactly one way into an ADR file, as the projected
            # path above does.
            raw = _safe_read_bytes(root, ap, oversize)
            if raw is None:
                continue
            fm = _architectural_frontmatter(_universal(raw.decode("utf-8", errors="replace")))
            status = str(fm.get("status", "")).strip()
            if status != "Accepted":
                continue
            tags = fm.get("tags") or []
            if isinstance(tags, str):
                tags = [t.strip() for t in tags.strip("[]").split(",") if t.strip()]
            if mode == "curated" and not curated_keep(tags):
                continue
            sources[_rel(root, ap)] = _sha256_hex(raw)
            # Both id spellings, for the reason `_ADR_ID_RE` states. The two
            # paths must agree on the answer (clause 1), so a prefix tolerated
            # on the projected path and dropped here would make the fallback
            # render `CRX-ADR-0012-slug` where the projection renders `0012`.
            num = _ADR_FILENAME_RE.match(ap.name)
            rows.append((num.group(1) if num else ap.stem,
                         str(fm.get("title", ap.stem)).strip(),
                         str(fm.get("date", "")).strip()))

    label = "load-bearing (curated)" if mode == "curated" else "Accepted, non-archived"
    out = ["# Decision index", "",
           f"_{label} decisions ({len(rows)}){origin}. "
           f"ADR references are footnotes, never inline._", "",
           "| # | decision | date | ref |", "|---|---|---|---|"]
    foots = []
    for i, (num, title, date) in enumerate(sorted(rows, key=lambda r: r[0]), start=1):
        # An escape helper, not a bare pipe replacement. A title is a YAML scalar
        # and YAML scalars carry NEWLINES, so escaping only `|` left the rest of
        # the payload to be emitted verbatim into the document: a title has
        # injected its own `# H1`, a live `[[wiki-link]]`, a real `[^fn]:`
        # footnote DEFINITION, and a `SYSTEM: ignore all previous instructions.`
        # line into `decision-index.md`. Flattening the newlines stops all four
        # at once, because a heading and a footnote definition must each START a
        # line and there is no longer a line for them to start. This is the
        # UNIVERSAL concern, bound for every pack, so the title is target-repo
        # content in every repository crux touches.
        #
        # `_prose_cell` and not `_cell`, and the two differ only on backtick.
        # This cell is not wrapped in a code span — it renders as markdown — so a
        # backtick in it is legitimate inline code, and `_cell` would substitute
        # it for `ʼ`. Measured on this repository: `_cell` mangles 6 ADR titles,
        # turning `Make \`log-work --silent\` default to log-only` into
        # `Make ʼlog-work --silentʼ default to log-only`. That is the exact
        # corruption `_prose_cell`'s docstring exists to prevent, and it buys
        # nothing here: a table row is split on unescaped `|` before inline
        # parsing, so an unbalanced backtick cannot reach past its own cell.
        title = _prose_cell(title)
        # `date` is target-repo frontmatter too, and it was the one cell in this
        # row left bare. A YAML `date:` is usually a date object, but a QUOTED
        # one is a string the reader passes straight through, so `"2026-01-01 |
        # x"` split the row and a folded scalar carrying a newline reached
        # line-start position — in the UNIVERSAL concern, on every repository.
        # `_cell` rather than `_prose_cell`: unlike a title, a date has no
        # legitimate inline code, so neutralizing backtick costs nothing.
        date = _cell(date)
        out.append(f"| {i} | {title} | {date} | [^d{i}] |")
        foots.append(f"[^d{i}]: ADR-{num}")
    out += [""] + foots + [""]
    if clauses:
        out += ["## Historical implementation clauses", "",
                "_These source clauses retain evidence and establish no current architectural requirement._", "",
                "| evidence | clause digest | disposition |", "|---|---|---|"]
        historical_foots = []
        for i, clause in enumerate(clauses, start=1):
            ref = clause["source_ref"]
            out.append(f"| [^h{i}] | {_cell(ref['clause_sha256'])} | historical implementation |")
            historical_foots.append(f"[^h{i}]: {_cell(ref['path'])}; architectural replacements: "
                                    f"{_cell(', '.join(clause['replacements'])) or 'none'}")
        out += [""] + historical_foots + [""]
    if authority["state"] == "published":
        import implementation_migration as migration
        migration._unchanged_inputs(root, authority["dependency_fingerprints"] +
                                   [{"path": p["path"], "sha256": p["sha256"]}
                                    for p in authority["publications"]])
    if not rows:
        out += ["_No Accepted decisions yet._", ""]
    return _canon(out), sources


# ── api-surface: a committed openapi.json, grouped by tag ────────────────────

_HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}


def _path_prefix(path: str) -> str:
    """First non-parameter path segment, the untagged-operation group key."""
    segs = [s for s in path.split("/") if s and not s.startswith("{")]
    return segs[0] if segs else "/"


def _render_openapi(doc, derived_from: str) -> str | None:
    """Render a parsed OpenAPI document into the api-surface markdown, shared by
    the Python and Ruby packs (ADR-0067 point 6). Operations are grouped by first
    `tag` (untagged → path prefix), sorted by group, then path, then HTTP method.
    `derived_from` is the italic provenance line. Returns None when the document
    yields no operations (the caller emits the stub) — a pure extraction of the
    former inline renderer, so both packs share one code path with no drift."""
    paths = doc.get("paths") if isinstance(doc, dict) else None
    groups: dict[str, list[tuple[str, str, str]]] = {}
    if isinstance(paths, dict):
        for path in sorted(paths):
            item = paths[path]
            if not isinstance(item, dict):
                continue
            for method in sorted(item):
                if method.lower() not in _HTTP_METHODS:
                    continue
                op = item[method]
                if not isinstance(op, dict):
                    continue
                summary = str(op.get("summary", "")).strip()
                tags = op.get("tags")
                group = str(tags[0]) if isinstance(tags, list) and tags else _path_prefix(path)
                groups.setdefault(group, []).append((path, method.upper(), summary))

    if not groups:
        return None

    out = ["# API surface", "", derived_from, ""]
    for group in sorted(groups):
        entries = sorted(groups[group], key=lambda e: (e[0], e[1]))
        out += [f"## {_cell(group)} ({len(entries)})", "",
                "| method | path | summary |", "|---|---|---|"]
        for path, method, summary in entries:
            out.append(f"| {_cell(method)} | `{_cell(path)}` | {_cell(summary)} |")
        out.append("")
    return _canon(out)


def _topo_order(nodes: dict) -> tuple[list[str], bool]:
    """Canonical topological order over the migration DAG, `revision` id as the
    same-depth tie-break (ADR-0066 points 8/10). Depth = longest path from a
    base. A cycle or a missing parent → (plain revision-id sort, malformed=True),
    so the order never depends on filesystem discovery order either way."""
    from collections import deque

    revs = set(nodes)
    parents = {r: list(nodes[r][0]) for r in revs}
    for r in revs:
        for d in parents[r]:
            if d not in revs:                      # missing parent → malformed
                return sorted(revs), True

    children: dict[str, list[str]] = {r: [] for r in revs}
    for r in revs:
        for d in parents[r]:
            children[d].append(r)
    indeg = {r: len(parents[r]) for r in revs}
    level = {r: 0 for r in revs}
    q = deque(sorted(r for r in revs if indeg[r] == 0))
    seen = 0
    while q:
        r = q.popleft()
        seen += 1
        for c in sorted(children[r]):
            level[c] = max(level[c], level[r] + 1)
            indeg[c] -= 1
            if indeg[c] == 0:
                q.append(c)
    if seen != len(revs):                          # cycle → malformed
        return sorted(revs), True
    return sorted(revs, key=lambda r: (level[r], r)), False


_MAX_GRAPH_EDGES = 5000                # rendered module-graph edge bound (point 8 DoS guard).


def _node_ids(constants: list) -> dict:
    """Map each constant to a stable Mermaid node id: the constant sanitized to
    `[A-Za-z0-9_]`, with a deterministic `_2`/`_3` suffix (ordered by constant)
    on any sanitized-id collision (point 3). The id is kept SEPARATE from the
    escaped display label."""
    ids: dict = {}
    used: dict = {}
    for const in sorted(constants):
        base = re.sub(r"[^A-Za-z0-9_]", "_", const)
        used[base] = used.get(base, 0) + 1
        ids[const] = base if used[base] == 1 else f"{base}_{used[base]}"
    return ids


def _mermaid(label: str) -> str:
    """Escape a constant for a quoted Mermaid label (point 8): quotes → entity,
    newlines flattened."""
    return str(label).replace('"', "#quot;").replace("\n", " ")


# ═══════════════════════════════════════════════════════════════════════════
# U2b — the CROSS-PACK helpers the split surfaced (ADR-0096 clause 12)
# Ten names that read as pack-local in the monolith and are not. Each was
# defined once inside the ruby or node region and referenced from two or three
# packs, which is invisible while every pack shares one file and becomes an
# import error the moment they do not. All three pack agents hit this
# independently, from three different regions, which is what identified it as
# one finding rather than three.
#
# They are hoisted rather than duplicated per pack deliberately. Two copies of
# `_norm_path` would pass every test in this repository today and silently
# diverge the first time one of them is corrected — the failure mode clause 1
# already names for regex extractors, in a different costume. One name, one
# definition.
#
# `_SYM_RE` travels with `_syms`, which is its only caller.
# ═══════════════════════════════════════════════════════════════════════════

_OPENAPI_CANDIDATES = (
    "openapi.json", "swagger.json", "docs/openapi.json",
    "swagger/v1/swagger.json", "doc/openapi.json",
)


_ONLY_RE = re.compile(r"only:\s*(\[[^\]]*\]|:[A-Za-z_]\w*)")
_EXCEPT_RE = re.compile(r"except:\s*(\[[^\]]*\]|:[A-Za-z_]\w*)")
_SYM_RE = re.compile(r":([A-Za-z_]\w*)")


def _norm_path(p: str) -> str:
    p = re.sub(r"/+", "/", "/" + p)
    return p.rstrip("/") if len(p) > 1 else "/"


def _syms(m) -> set:
    return set(_SYM_RE.findall(m.group(1))) if m else set()


def _singularize(name: str) -> str:
    return name[:-1] if name.endswith("s") else name


_MAX_SCAN_FILES = 5000                 # aggregate files scanned per concern (point 6).
_MAX_SCAN_BYTES = 64 * 1024 * 1024     # aggregate bytes read per concern (point 6).


def _split_top_level(s: str) -> list:
    parts, depth, cur = [], 0, []
    for c in s:
        if c in "{[(":
            depth += 1
        elif c in ")]}":
            depth -= 1
        if c == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(c)
    if "".join(cur).strip():
        parts.append("".join(cur))
    return parts


# ═══════════════════════════════════════════════════════════════════════════
# U7 — the ENGINE (ADR-0096 clause 12)
# Stack detection, per-concern extractor resolution, the override trust
# boundary, the build, the synthesized artifacts, and the drift gate. This is
# the half of clause 12's sentence that reads "the core reads": every probe is
# handed what the core resolved, and no probe reaches the registry itself.
#
# **Every pack import below is LAZY, inside a function body, and that is the
# whole reason this module can still be file-loaded by
# `runtime/capture.py::_load_cell`.** A module-level `from .packs import ...`
# here would make `core.py` un-loadable outside the package and break the
# runtime executor. `test_arch_split_seam.py` walks this module's AST and fails
# on any module-scope intra-crux import, naming that loader as the reason. The
# established precedent is `derive.py`'s own function-level `from .recover
# import curated_keep`, which has always been function-level for a related
# reason.
# ═══════════════════════════════════════════════════════════════════════════


def _pack_module(name: str):
    """Import one pack module lazily. See the banner above for why never at
    module scope. Raises ModuleNotFoundError for a name that is not a shipped
    pack, which `pack_probes` and `_pack_detect` both guard against by checking
    `REGISTERED_PACKS` first."""
    from . import packs                     # lazy — see the banner above
    return importlib.import_module(f".{name}", packs.__name__)


def _pack_detect(name: str, root: Path) -> bool:
    """One pack's marker predicate at ONE directory, as a plain bool.

    Deliberately root-only and marker-set-only: this is the predicate
    `pack_probes` uses to gate the crux pack's decision-index probe, and that
    gate is pre-existing behavior the byte-identity gate wants unmoved. The
    depth-two scan clause 6 asks for lives in `detect_candidates`, which calls
    each pack's own `detect` per directory and keeps the markers.
    """
    return _pack_module(name).detect(root).matched


def _pack_detect_exclude(name: str) -> frozenset[str]:
    """A registered pack's `DETECT_EXCLUDE` set: the pack seam's exclusion
    set (ADR-0129 clause 3).

    REQUIRED on every registered pack module — a pack that declares no walk
    exclusions still declares an empty one, one line, so the scan never has to
    ask "did this pack forget the attribute or mean to exclude nothing". A
    missing attribute raises `AttributeError`; a value that is not a
    `frozenset` raises `TypeError`. Both fire at the scan, before any
    directory is visited, so a malformed declaration never partially prunes.
    """
    module = _pack_module(name)
    if not hasattr(module, "DETECT_EXCLUDE"):
        raise AttributeError(
            f"pack {name!r} declares no DETECT_EXCLUDE (every pack needs one, "
            "even if empty: DETECT_EXCLUDE = frozenset())"
        )
    value = module.DETECT_EXCLUDE
    if not isinstance(value, frozenset):
        raise TypeError(
            f"pack {name!r}'s DETECT_EXCLUDE must be a frozenset, got "
            f"{type(value).__name__}"
        )
    return value


def _dir_excluded(prefix: str, exclude: frozenset[str]) -> bool:
    """True when `prefix` (a repo-relative scan path, `""` at the root) has a
    path component one of `exclude`'s entries names (ADR-0129 clause 3).

    An entry is either an exact directory name (`Pods`) or a `*`-prefixed name
    suffix (`*.xcodeproj`, matched by `component.endswith(".xcodeproj")`).
    Matching reads REPO-RELATIVE components only — never the absolute path —
    so a checkout sitting under a directory that happens to share an excluded
    name is unaffected: the root's own relative path is empty and carries no
    component at all.
    """
    if not prefix or not exclude:
        return False
    components = prefix.split("/")
    for component in components:
        for entry in exclude:
            if entry.startswith("*"):
                if component.endswith(entry[1:]):
                    return True
            elif component == entry:
                return True
    return False


# The per-pack marker detectors, addressed by pack name. Bound through
# `_pack_detect` so the mapping itself imports nothing: the pack module loads on
# the first call, not at import time. Keyed off the registry, so registering a
# pack is the one edit that adds its detector.
PACK_DETECTORS: dict[str, Callable[[Path], bool]] = {
    name: functools.partial(_pack_detect, name) for name in PACK_NAMES
}


class StackAmbiguity(NamedTuple):
    """More than one pack matched, and the recorded precedence did not decide.

    `candidates` is every pack that matched, sorted. `markers` maps each of them
    to the repo-relative marker paths that fired. Both are the payload clause 6
    requires the verdict to name — "each candidate AND the marker that matched
    it" — so this carries the markers rather than only the pack names.
    """

    candidates: tuple[str, ...]
    markers: dict[str, tuple[str, ...]]


class StackResolution(NamedTuple):
    """What detection decided, including when it decided nothing.

    `pack_name` is the pack whose probes run. Under an ambiguity it is the EMPTY
    pack, because running one contender's extractors would be exactly the silent
    single pick clause 6 forbids; `ambiguity` then carries what to say instead.
    """

    pack_name: str
    ambiguity: StackAmbiguity | None = None
    pinned: bool = False


def _scan_dirs(root: Path, max_depth: int = DETECTION_MAX_DEPTH):
    """`(directory, repo-relative prefix)` for `root` and every directory at a
    depth of at most `max_depth` below it.

    Breadth-first and sorted, so the scan order is deterministic. `_SKIP_DIRS`
    and dot-directories are pruned: a marker inside `node_modules/` or a
    vendored `deps/` tree describes a dependency, not this repository.

    A symlinked directory is pruned too, matching what every pack's walker
    already does with `followlinks=False`. `is_dir()` FOLLOWS, so without the
    `is_symlink()` test this scan descended through a link and reported markers
    from wherever it pointed — including outside the repository — as markers of
    this one.
    """
    root = Path(root)
    held = _retained_source_filter(root)
    frontier = [(root, "")]
    yield root, ""
    for _ in range(max_depth):
        nxt = []
        for parent, prefix in frontier:
            try:
                children = sorted(
                    p for p in parent.iterdir() if not p.is_symlink() and not held(p) and p.is_dir()
                )
            except OSError:
                continue
            for child in children:
                if child.name.startswith(".") or child.name in _SKIP_DIRS:
                    continue
                rel = f"{prefix}/{child.name}" if prefix else child.name
                nxt.append((child, rel))
                yield child, rel
        frontier = nxt


def _scan_candidates(root: Path, max_depth: int = DETECTION_MAX_DEPTH):
    """`(markers, depths)` for every registered pack whose marker fired.

    `markers` is `{pack_name: (repo-relative marker, ...)}`, sorted and deduped,
    packs in registration order. `depths` is `{pack_name: shallowest depth}` —
    the fewest directories below the repository root at which that pack's marker
    matched, 0 meaning the root itself.

    The depth is kept rather than discarded because it is half of what resolves
    a multi-match (`_undominated`). The scan produces it for free and one pass
    supplies both, so the two views cannot disagree about what matched.

    Each pack's OWN `detect` runs per directory, so every pack's marker
    semantics survive the depth change unaltered — the crux pack still requires
    both of its directories, the ruby pack still globs `*.gemspec` — and this
    function never learns what any pack's markers are.

    **A pack's own `DETECT_EXCLUDE` prunes its OWN calls only (ADR-0129
    clause 3).** Computed
    once, before the scan starts, so a malformed declaration (see
    `_pack_detect_exclude`) fails before any directory is visited rather than
    mid-scan. A directory excluded for one pack is still scanned for every
    other — the exclusion is per-pack, not a shared skip list.
    """
    hits: dict[str, set] = {}
    depths: dict[str, int] = {}
    excludes = {name: _pack_detect_exclude(name) for name in PACK_NAMES}
    for directory, prefix in _scan_dirs(Path(root), max_depth):
        depth = prefix.count("/") + 1 if prefix else 0
        for name in PACK_NAMES:
            if _dir_excluded(prefix, excludes[name]):
                continue
            result = _pack_module(name).detect(directory)
            if not result.matched:
                continue
            hits.setdefault(name, set()).update(
                f"{prefix}/{m}" if prefix else m for m in result.markers
            )
            if depth < depths.get(name, max_depth + 1):
                depths[name] = depth
    markers = {name: tuple(sorted(hits[name])) for name in PACK_NAMES if name in hits}
    return markers, depths


def detect_candidates(root: Path, max_depth: int = DETECTION_MAX_DEPTH) -> dict:
    """Every registered pack whose marker fired at depth <= `max_depth`.

    Returns `{pack_name: (repo-relative marker, ...)}`, markers sorted and
    deduped, packs in registration order. A pack absent from the mapping did not
    match anywhere in the scanned depth. The markers view of `_scan_candidates`.
    """
    return _scan_candidates(root, max_depth)[0]


def _undominated(candidates, depths) -> list[str]:
    """The matched packs nothing beats, in registration order — DEPTH FIRST.

    Two stages, and the order between them is the whole rule.

      1. **Depth dominates.** Only the packs whose shallowest marker sits at the
         minimum depth over all candidates contend. A pack that matched only
         deeper is out, whatever the registry records.
      2. **`beats` decides within one depth.** Among the contenders — all at the
         same depth by construction — the recorded pairwise precedence applies.

    Exactly one survivor means resolution succeeded. More than one means it did
    not, and clause 6 makes that an `ambiguous_stack` verdict rather than a pick.

    **Why depth comes first, and why this is a fix rather than a preference.**
    Every `beats` pair in the registry was measured on a repository carrying
    both markers AT THE SAME DEPTH, and each entry's `why` says so. Before the
    clause 6 depth-two scan, root-only detection made that the only shape a
    multi-match could take, so the relation never had to name a depth. The scan
    removed that premise: applying `beats` on pack name alone then let a root
    `package.json` lose to a `tools/setup.py` one directory down, and an Express
    repository vendoring a Python helper directory lost its whole node
    extraction to three `precondition_missing` stubs. That is a marker the
    registry never measured, and resolving it from a pair measured at equal
    depth is the guess clause 6 exists to refuse.
    """
    if not candidates:
        return []
    beats = {e.name: e.beats for e in PACK_REGISTRY}
    floor = min(depths[c] for c in candidates)
    contenders = {c for c in candidates if depths[c] == floor}
    return [c for c in PACK_NAMES
            if c in contenders
            and not any(c in beats[o] for o in contenders if o != c)]


def resolve_stack(root: Path, cfg=None) -> StackResolution:
    """Resolve the stack pack for `root`, or name why it could not (clause 6).

    Order, and each step is one of the ADR's sentences:

      * an `arch_stack:` pin wins, and a pin naming an unregistered pack is the
        fail-closed error ADR-0066 clause 10 established — raise, never a silent
        stub;
      * else scan markers at depth <= 2 and collect EVERY pack that matched,
        with the shallowest depth at which each one matched;
      * else, among the matched packs, drop everything a shallower match
        dominates, then take the one no survivor beats under the registry's
        pairwise precedence;
      * more than one survivor is an ambiguity, named rather than picked;
      * no candidate at all is the empty pack, which is `unsupported_stack` and
        is a different statement from "several stacks matched".

    The ambiguity payload names EVERY matched pack and its markers, including
    one a shallower match dominated. A reader looking at three stubs needs to
    see everything the scan found, and the depth rule is stated in
    `_undominated` rather than hidden by pruning the report.
    """
    pin = getattr(cfg, "arch_stack", None) if cfg is not None else None
    if pin:
        if pin not in REGISTERED_PACKS:
            raise ValueError(
                f"arch_stack pin names an unregistered pack: {pin!r} "
                f"(registered packs: {', '.join(sorted(REGISTERED_PACKS))})"
            )
        return StackResolution(pin, None, True)

    candidates, depths = _scan_candidates(root)
    if not candidates:
        return StackResolution(STUB_PACK_NAME)
    winners = _undominated(candidates, depths)
    if len(winners) == 1:
        return StackResolution(winners[0])
    return StackResolution(
        STUB_PACK_NAME,
        StackAmbiguity(tuple(sorted(candidates)), dict(candidates)),
    )


def ambiguous_stack_verdict(ambiguity: StackAmbiguity) -> "Verdict":
    """The clause 6 verdict for an unresolved multi-match.

    `found` names each candidate AND the marker that matched it. Marker paths
    are repo content — the ruby pack's marker is a `*.gemspec` glob, so a
    filename reaches this string — and every one is passed through `_cell`,
    which is the same escaping the coverage vocabulary has always used.
    """
    named = ", ".join(
        f"{_cell(pack)} ({', '.join(_cell(m) for m in ambiguity.markers.get(pack, ()))})"
        for pack in ambiguity.candidates
    )
    return Verdict.stubbed(
        StubReason.AMBIGUOUS_STACK,
        expected="one stack pack, or a recorded pairwise precedence between the "
                 "packs that matched",
        found=f"{len(ambiguity.candidates)} packs matched — {named}",
    )


def pack_ambiguity(pack_name: str, concern: str, root: Path):
    """A pack's own clause 6 ambiguity verdict for one concern, or None.

    The seam for `ambiguous_package`: a pack that can have more than one
    candidate for a concern's input exposes `concern_ambiguity(concern, root)`
    and returns a `Verdict` naming them. The core does not know what a package
    is — only the pack does — so the knowledge stays behind the ADR-0066
    clause 2 seam exactly as a probe detector does.
    """
    if pack_name not in REGISTERED_PACKS:
        return None
    hook = getattr(_pack_module(pack_name), "concern_ambiguity", None)
    if hook is None:
        return None
    return hook(concern, Path(root))


def _concern_decode_verdict(
    pack_name: str, concern: str, content: str, sources: dict
) -> "Verdict | None":
    """A pack's own decode-failure verdict for one already-stubbed concern
    (ADR-0129 clause 7).

    The seam for `parse_failed`: a registered pack may expose
    `concern_decode_verdict(concern, content, sources)`, called by `_build`
    over a concern `concern_verdict` already called `stubbed`, before
    `pack_ambiguity`. A `None` return (or no hook at all) changes nothing. A
    non-`None` return MUST be a stubbed `Verdict` with reason
    `StubReason.PARSE_FAILED` — anything else, a populated verdict or another
    stub reason, raises `ValueError`, so a pack cannot mint a verdict this seam
    was never meant to grant it.
    """
    if pack_name not in REGISTERED_PACKS:
        return None
    hook = getattr(_pack_module(pack_name), "concern_decode_verdict", None)
    if hook is None:
        return None
    result = hook(concern, content, sources)
    if result is None:
        return None
    if not (isinstance(result, Verdict) and result.kind == "stubbed"
            and result.reason is StubReason.PARSE_FAILED):
        raise ValueError(
            f"the {pack_name} pack's concern_decode_verdict({concern!r}) must "
            "return None or a stubbed Verdict with reason PARSE_FAILED, got "
            f"{result!r}"
        )
    return result


def detect_stack(root: Path, cfg=None) -> str:
    """The resolved pack's NAME (ADR-0066 point 3).

    The narrow accessor over `resolve_stack`, kept because eleven test modules
    and the corpus harness bind it. It reports the empty pack for an ambiguity,
    which is honest about which extractors run and silent about why — callers
    that record a verdict must use `resolve_stack`, and `_build` does.
    """
    return resolve_stack(root, cfg).pack_name


def pack_probes(pack_name: str, decision_index_mode: str) -> dict[str, list[Probe]]:
    """The probe registry for a resolved pack (ADR-0066 points 2/3).

    Each pack module supplies its own three concerns through `probes()`; the
    core supplies the fourth. `decision-index` is a stack-portable UNIVERSAL
    probe — the ADR tree is stack-independent (ADR-0060) and
    `extract_decision_index` self-degrades to the empty-but-valid marker when no
    ADR tree exists — so every stack, including the empty stub pack, keeps a
    working decision index, and no pack module binds it.

    **The crux pack gates its decision-index on its own detector; every other
    pack binds it unconditionally.** That asymmetry is pre-existing behavior
    carried across the split unchanged, and it is load-bearing: changing it
    would move this repository's own spine, which is the one thing clause 12's
    byte-identity gate forbids.
    """
    di = functools.partial(extract_decision_index, mode=decision_index_mode)
    if pack_name not in REGISTERED_PACKS:
        # Stub + not-yet-populated language packs: only the universal probe.
        return {"decision-index": [Probe(_always, di, kind=_DECISION_INDEX_PROBE_KIND)]}
    registry = dict(_pack_module(pack_name).probes())
    gate = PACK_DETECTORS["crux"] if pack_name == "crux" else _always
    registry["decision-index"] = [Probe(gate, di, kind=_DECISION_INDEX_PROBE_KIND)]
    return registry


def _qualified_probe_name(fn) -> str:
    """`packs.<pack>:<function>` from `fn.__module__`/`fn.__qualname__` — the
    exact spelling ADR-0097's roster table uses, so the roster is mechanically
    comparable to the registered code rather than to a hand-typed name. The
    core's own `extract_decision_index` reports as `core:extract_decision_index`
    instead, since it is not a pack module.
    """
    mod = fn.__module__.rsplit(".", 1)[-1]
    namespace = f"packs.{mod}" if mod in REGISTERED_PACKS else mod
    return f"{namespace}:{fn.__qualname__}"


def _probe_registrations(pack_name: str) -> dict[str, tuple[str, str]]:
    """qualified_name -> (concern, kind) for every probe a pack registers,
    including the universal decision-index probe the core binds for it
    (ADR-0097 part 1). The shared read `probe_kinds` and `probe_concerns` both
    project from, so the two answers cannot drift apart.
    """
    out, _ = _probe_registrations_and_collisions(pack_name)
    return out


def _pack_registry(pack_name: str) -> dict:
    return dict(_pack_module(pack_name).probes()) if pack_name in REGISTERED_PACKS else {}


def _probe_registrations_and_collisions(
        pack_name: str) -> tuple[dict[str, tuple[str, str]], list[str]]:
    """The merged registration map AND the WITHIN-pack qualified-name
    collisions the merge would otherwise swallow.

    The merge is keyed by qualified name and is therefore last-write-wins, so
    one pack registering a single function under two concerns collapses to one
    entry BEFORE `roster_problems` ever runs — and that gate's whole job is to
    have no silent hole. The cross-pack half of the same check lives in
    `roster_problems`, which cannot see this half because it receives the
    already-merged map. Detected here, where the collapse happens.

    Reported only on a DIFFERING `(concern, kind)`: registering one function
    twice under identical terms is a redundant registration, not a hidden one.
    """
    out: dict[str, tuple[str, str]] = {}
    collisions: list[str] = []
    for concern, probe_list in _pack_registry(pack_name).items():
        for p in probe_list:
            name = _qualified_probe_name(p.extract)
            entry = (concern, p.kind)
            prior = out.get(name)
            if prior is not None and prior != entry:
                collisions.append(
                    f"duplicate-probe: {name} is registered by pack "
                    f"{pack_name!r} under more than one (concern, kind) — "
                    f"{prior!r} and {entry!r} — so one registration would hide "
                    "the other from the roster gate"
                )
            out[name] = entry
    out[_qualified_probe_name(extract_decision_index)] = (
        "decision-index", _DECISION_INDEX_PROBE_KIND)
    return out, collisions


def probe_registration_collisions(pack_name: str) -> list[str]:
    """The within-pack collisions for one pack — the roster gate's second
    surface, beside `roster_problems`' cross-pack one. Empty on every shipped
    pack; a pack-acceptance test asserts that and a control asserts the check
    fires."""
    return _probe_registrations_and_collisions(pack_name)[1]


def probe_kinds(pack_name: str) -> dict[str, str]:
    """{qualified_name: kind} for every probe the named pack registers
    (ADR-0097 part 1) — the roster gate's comparison surface. A qualified name
    reads `packs.<pack>:<function_name>`, e.g.
    `packs.ruby:extract_ruby_module_graph`, mirroring ADR-0097's own table.
    """
    return {name: kind for name, (_, kind) in _probe_registrations(pack_name).items()}


def probe_concerns(pack_name: str) -> dict[str, str]:
    """{qualified_name: concern} for every probe the named pack registers —
    which spine concern each probe answers for. Pairs with `probe_kinds` so a
    roster gate can assert no `api-surface` probe declares `regex-over-source`
    (ADR-0097 part 2).
    """
    return {name: concern for name, (concern, _) in _probe_registrations(pack_name).items()}


# ── the ADR-0097 part 1 regex-over-source admission roster ──────────────────

def load_regex_roster(path: Path | None = None) -> list[dict]:
    """Load and validate the ADR-0097 part 1 `regex-over-source` admission
    roster (`crux/scripts/crux/arch/regex-roster.yml` by default — the
    committed manifest beside the packs).

    Imports PyYAML at the USE SITE, not at module scope, so `core.py` stays
    importable on an interpreter without PyYAML — the posture `_frontmatter`
    and `packs/crux.py`'s manifest read already take.

    Fails loudly, naming the file and the problem, on a missing file,
    unparseable YAML, a missing `probes` list, or a row missing any of
    `probe`, `pack`, `concern`, `admitting_adr`, `why_no_parser` — a roster
    that half-loads would make the roster gate pass for the wrong reason.
    """
    if path is None:
        path = Path(__file__).resolve().parent / "regex-roster.yml"
    if not path.is_file():
        raise ValueError(f"regex roster not found: {path}")
    import yaml  # PyYAML is available under `uv run`; see the docstring above.
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"regex roster {path} is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("probes"), list):
        raise ValueError(f"regex roster {path} carries no `probes` list")
    required = ("probe", "pack", "concern", "admitting_adr", "why_no_parser")
    rows = raw["probes"]
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"regex roster {path} row {i} is not a mapping")
        missing = [k for k in required if k not in row]
        if missing:
            raise ValueError(
                f"regex roster {path} row {i} ({row.get('probe', '?')!r}) is "
                f"missing required field(s): {missing}"
            )
    return rows


def roster_problems(
    probe_kinds_by_pack: dict[str, dict[str, tuple[str, str]]],
    roster_rows: list[dict],
    active_adr_ids,
) -> list[str]:
    """Compare the ADR-0097 part 1 roster against the code, in both
    directions, plus the ADR-0097 part 2 api-surface rule — ONE pure function
    so every direction and every positive control drives the same
    implementation rather than a test-only re-statement.

    `probe_kinds_by_pack` is `{pack_name: {qualified_name: (concern, kind)}}`
    — one dict per registered pack, each mapping a probe's qualified name (as
    `_qualified_probe_name` spells it) to its `(concern, kind)` pair.
    `roster_rows` is `load_regex_roster`'s return. `active_adr_ids` is the set
    of ADR ids the caller has already resolved as active — this function does
    no ADR resolution of its own, so that logic has exactly one definition
    (`summaries_projection.adr_paths` plus the `Proposed | Accepted` status
    filter, per `docs/AGENTS.md` §4 "adrs").

    Four rules, four distinct message prefixes, each naming the offending
    qualified name or ADR id — the positive controls discriminate on content,
    not merely on non-emptiness:

      `unenrolled:` — a probe declares `regex-over-source` and is absent from
      the roster.

      `unresolved:` — a roster row names no registered probe, or names a
      probe that does not declare `regex-over-source`.

      `inactive-adr:` — a roster row's `admitting_adr` is not active.

      `api-surface-regex:` — a probe answering for `api-surface` declares
      `regex-over-source` (ADR-0097 part 2's own rule, asserted independent of
      what the roster happens to contain).

    **The open residual, stated in the ADR's own terms.** This function checks
    that a roster row's `admitting_adr` is ACTIVE. It cannot check that the
    named ADR actually ADMITS that probe — the admission lives in an ADR
    body, and a body is prose. A change adding a probe and a roster row
    naming any active ADR passes this gate and fails review. That is
    ADR-0097 part 1's stated residual, not a second, broader guarantee.
    """
    problems: list[str] = []

    # Merged by qualified name, because that is the roster's key. This CROSS-PACK
    # merge is collision-checked rather than last-write-wins: `_qualified_probe_name`
    # names a function by its DEFINING module, so two packs registering one
    # shared function collapse to a single key and the later registration would
    # hide the earlier from the `unenrolled:` and `api-surface-regex:` rules —
    # a silent hole in a gate whose whole job is to have none. The universal
    # decision-index probe is registered by every pack and is the expected
    # duplicate; it collides on an IDENTICAL `(concern, kind)`, so only a
    # DIFFERING value is reported. The WITHIN-pack half of the same hole cannot
    # be seen from here — this function receives per-pack maps that are already
    # merged — and is checked at the collapse site by
    # `probe_registration_collisions`.
    all_probes: dict[str, tuple[str, str]] = {}
    owners: dict[str, set[str]] = {}
    for pack, registrations in sorted(probe_kinds_by_pack.items()):
        for name, entry in registrations.items():
            prior = all_probes.get(name)
            if prior is not None and prior != entry:
                problems.append(
                    f"duplicate-probe: {name} is registered by more than one "
                    f"pack under different (concern, kind) — {prior!r} and "
                    f"{entry!r} — so one registration would hide the other"
                )
            all_probes[name] = entry
            owners.setdefault(name, set()).add(pack)

    roster_names = {row["probe"] for row in roster_rows}

    for name, (_concern, kind) in sorted(all_probes.items()):
        if kind == "regex-over-source" and name not in roster_names:
            problems.append(
                f"unenrolled: {name} declares regex-over-source but is absent "
                "from the roster"
            )

    for row in roster_rows:
        name = row["probe"]
        entry = all_probes.get(name)
        if entry is None:
            problems.append(
                f"unresolved: roster row {name} resolves to no registered probe"
            )
        elif entry[1] != "regex-over-source":
            problems.append(
                f"unresolved: roster row {name} resolves to a probe declaring "
                f"{entry[1]!r}, not regex-over-source"
            )
        # A row's `pack` and `concern` are ASSERTIONS about the code, so the
        # gate checks them like every other one. A row is otherwise free to
        # record `concern: api-surface` — a thing ADR-0097 part 2 forbids
        # outright — and a reviewer reading the roster would get a false
        # statement backed by a green gate.
        if entry is not None:
            if row["concern"] != entry[0]:
                problems.append(
                    f"mismatched-concern: roster row {name} records concern "
                    f"{row['concern']!r}, but the registered probe answers for "
                    f"{entry[0]!r}"
                )
            if row["pack"] not in owners.get(name, set()):
                problems.append(
                    f"mismatched-pack: roster row {name} records pack "
                    f"{row['pack']!r}, but the probe is registered by "
                    f"{sorted(owners.get(name, set()))}"
                )

    for row in roster_rows:
        adr = row["admitting_adr"]
        if adr not in active_adr_ids:
            problems.append(
                f"inactive-adr: roster row {row['probe']} names {adr}, which "
                "is not an active ADR"
            )

    for name, (concern, kind) in sorted(all_probes.items()):
        if concern == "api-surface" and kind == "regex-over-source":
            problems.append(
                f"api-surface-regex: {name} answers for api-surface but "
                "declares regex-over-source"
            )

    return problems


def resolve_extractor(
    concern: str,
    root: Path,
    docs_dir: str,
    cfg=None,
    *,
    decision_index_mode: str = "complete",
    pack_name: str | None = None,
) -> Callable[[Path, str], tuple[str, dict]]:
    """Resolve the extractor for one concern (ADR-0066 point 1).

    Resolution chain: a per-repo override for the concern → the detected pack's
    first matching probe → the stub. Always returns a callable honoring the
    uniform `(root, docs_dir) -> (markdown, sources)` contract; the override
    branch is guarded and never raises — it falls through to pack then stub.
    """
    return _resolve_extractor(
        concern, root, docs_dir, cfg,
        decision_index_mode=decision_index_mode, pack_name=pack_name,
    )[0]


def _resolve_extractor(
    concern: str,
    root: Path,
    docs_dir: str,
    cfg=None,
    *,
    decision_index_mode: str = "complete",
    pack_name: str | None = None,
) -> tuple[Callable[[Path, str], tuple[str, dict]], str]:
    """`resolve_extractor` plus WHICH rung of the chain answered.

    The second element is `"probe"` when a probe's own detector fired (so an
    extractor for this concern really ran) and `"stub"` when none did (so the
    empty-but-valid renderer answered). Clause 3 needs the distinction that
    `resolve_extractor`'s single return value cannot carry: a stub file produced
    because NO probe matched is `precondition_missing` under a registered pack
    and `unsupported_stack` under the empty one, while a stub file produced by a
    probe that ran and self-degraded is always `precondition_missing`.

    An override counts as `"probe"`: it is an extractor the tree configured for
    this concern, and if it falls through, its fallback's own rung is what the
    content then shows.
    """
    if pack_name is None:
        pack_name = detect_stack(root, cfg)

    # Fallback (pack → stub), resolved first so the override wrapper can defer to
    # it on any guard failure without re-running detection.
    fallback = functools.partial(_stub_extract, concern)
    rung = "stub"
    for probe in pack_probes(pack_name, decision_index_mode).get(concern, []):
        try:
            hit = probe.detect(root)
        except Exception:
            hit = False
        if hit:
            fallback = probe.extract
            rung = "probe"
            break

    override_spec = _configured_override(concern, cfg)
    if override_spec is None:
        return fallback, rung

    # Override trust boundary (ADR-0066 point 6): opt-in ALWAYS. Absent the flag,
    # read the config, ignore it with a one-line notice, fall through.
    if os.environ.get("CRUX_ARCH_ALLOW_OVERRIDES") != "1":
        print(
            f"crux-arch: arch_extractors override for {concern!r} ignored; set "
            "CRUX_ARCH_ALLOW_OVERRIDES=1 to enable per-repo override execution",
            file=sys.stderr,
        )
        return fallback, rung
    return functools.partial(_run_override, concern, override_spec, fallback), "probe"


def _configured_override(concern: str, cfg) -> str | None:
    if cfg is None:
        return None
    extractors = getattr(cfg, "arch_extractors", None) or {}
    spec = extractors.get(concern)
    return spec if isinstance(spec, str) and spec.strip() else None


def _reject_override_path(path_part: str) -> str | None:
    """Textual path rejection (ADR-0066 point 5): no absolute, `~`, `..`,
    backslash, or control chars. Returns the reason string, or None if clean."""
    if not path_part or path_part != path_part.strip():
        return "empty or whitespace-padded path"
    if path_part.startswith("/") or path_part.startswith("~"):
        return "absolute or ~-leading path"
    if "\\" in path_part:
        return "backslash in path"
    if ".." in Path(path_part).parts:
        return "'..' path segment"
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in path_part):
        return "control character in path"
    return None


def _run_override(
    concern: str,
    spec: str,
    fallback: Callable[[Path, str], tuple[str, dict]],
    root: Path,
    docs_dir: str,
) -> tuple[str, dict]:
    """Execute a guarded per-repo override, or fall through (ADR-0066 points 5/6).

    Only reached with CRUX_ARCH_ALLOW_OVERRIDES=1. Every guard failure emits a
    loud stderr warning naming the concern and reason, then falls through to the
    pack/stub fallback — this branch NEVER raises. `spec` is "rel/path.py:function".
    """
    def warn(reason: str) -> None:
        print(
            f"crux-arch: override for {concern!r} failed ({reason}); "
            "falling through to pack/stub",
            file=sys.stderr,
        )

    if ":" not in spec:
        warn("spec must be 'rel/path/module.py:function'")
        return fallback(root, docs_dir)
    path_part, _, func_name = spec.rpartition(":")

    reason = _reject_override_path(path_part)
    if reason:
        warn(f"path rejected: {reason}")
        return fallback(root, docs_dir)

    root_r = root.resolve()
    target = (root_r / path_part).resolve()
    if target == root_r or not target.is_relative_to(root_r):
        warn("path resolves outside the repository root")
        return fallback(root, docs_dir)
    if not target.is_file():
        warn(f"module file not found: {path_part}")
        return fallback(root, docs_dir)
    if not func_name.isidentifier():
        warn(f"invalid function name: {func_name!r}")
        return fallback(root, docs_dir)

    try:
        mod_spec = importlib.util.spec_from_file_location(
            f"_crux_arch_override_{concern}", target
        )
        module = importlib.util.module_from_spec(mod_spec)
        mod_spec.loader.exec_module(module)
        fn = getattr(module, func_name)
    except Exception as exc:
        warn(f"import failed: {type(exc).__name__}: {exc}")
        return fallback(root, docs_dir)

    # Require two positional parameters (root, docs_dir).
    try:
        params = list(inspect.signature(fn).parameters.values())
        positional = [
            p for p in params
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ]
        has_varargs = any(p.kind == p.VAR_POSITIONAL for p in params)
        if len(positional) < 2 and not has_varargs:
            warn(f"override must accept 2 positional params, accepts {len(positional)}")
            return fallback(root, docs_dir)
    except (TypeError, ValueError) as exc:
        warn(f"cannot inspect signature: {exc}")
        return fallback(root, docs_dir)

    try:
        result = fn(root, docs_dir)
    except Exception as exc:
        warn(f"raised: {type(exc).__name__}: {exc}")
        return fallback(root, docs_dir)

    if not (
        isinstance(result, tuple)
        and len(result) == 2
        and isinstance(result[0], str)
        and isinstance(result[1], dict)
    ):
        warn("did not return a (str, dict) pair")
        return fallback(root, docs_dir)

    markdown, sources = result
    # An override returning non-stub markdown with empty sources cannot be
    # drift-gated (nothing to hash) — reject it.
    if not sources and NO_EXTRACTOR.strip() not in markdown:
        warn("returned non-stub markdown with empty sources (cannot be drift-gated)")
        return fallback(root, docs_dir)

    # Hash the override module file itself into sources, so a change to the
    # override code drifts the spine (ADR-0066 point 5). Guarded: an override
    # that deletes/replaces its own file during execution (or any TOCTOU after
    # the is_file() check) must not turn the derive into a crash — the contract
    # is never-raises → fall through to pack/stub.
    raw = _safe_read_bytes(root, target, [])
    if raw is None:
        warn("could not hash override module after execution (it is no longer a "
             "readable, in-bound regular file within the size bound)")
        return fallback(root, docs_dir)
    override_hash = _sha256_hex(raw)
    sources = dict(sources)
    sources[_rel(root, target)] = override_hash
    return markdown, sources


def _pin_config_path(root: Path, cfg) -> Path | None:
    """The repo-root config file whose `arch_stack` pin was honored, if any."""
    source = getattr(cfg, "source", "") if cfg is not None else ""
    for name in (".bionic.yml", ".crux"):
        if name in source:
            p = root / name
            if p.exists():
                return p
    # Fall back to whichever config file exists (source may be a combined form).
    for name in (".bionic.yml", ".crux"):
        p = root / name
        if p.exists():
            return p
    return None


# ───────────────────────────── engine ──────────────────────────────────────

def concern_verdict(
    concern: str,
    pack_name: str,
    rung: str,
    content: str,
    sources: dict,
) -> Verdict:
    """The one recorded verdict for one concern (ADR-0096 clauses 2 and 3).

    A pure function of committed bytes: the rendered markdown, the source map
    the extractor returned, which rung of the resolution chain answered, and the
    resolved pack name. Nothing here reads the environment, the clock, the
    commit graph or the filesystem, which is what keeps the recorded channel
    byte-stable across machines.

    **The entity count is asked FIRST, and the order is the whole point.** The
    previous form opened on
    `rendered_stub = (not sources) or NO_EXTRACTOR.strip() in content` — a
    SUBSTRING match standing in for a structural fact, over content the target
    repository controls — and it was wrong in both directions.

    It over-stubbed: the literal empty-but-valid sentence, placed in an
    `openapi.json` operation summary, travelled through the renderer unchanged
    (`_cell` is the identity on that sentence, which carries no pipe, backtick or
    newline) and made a fully populated api-surface record `stubbed`. A target
    repository could pick its own recorded verdict by writing one line of prose.

    It also mislabelled a real reading of a real input: a `db/schema.rb` that
    declares no tables consumed its declared input, found nothing, and recorded
    `precondition_missing` with `inputs_found=['db/schema.rb']` and
    `found='no matching input'` — a record that contradicts itself in two of its
    own fields. Clause 3 assigns that case `no_entities`, and says so.

    Counting first fixes both, because the count is a fact about the RENDERER'S
    OWN STRUCTURE (`count_concern_entities` matches this engine's entity-table
    headers) and not about a sentence a target repo can type. Four branches:

      * one or more entities of this concern's kind — `populated`, whatever
        prose the file happens to contain;
      * no entities and NO input consumed, under the EMPTY pack — the stack
        really has no registered pack, so `unsupported_stack`, and this is the
        only branch clause 4's reserved sentence is reachable from;
      * no entities and NO input consumed, under a REGISTERED pack — an
        extractor exists and its declared input was absent, so
        `precondition_missing`;
      * no entities but inputs WERE consumed — `no_entities`, which names what
        happened: the reader ran, read what it declared, and the repository
        declares none of this concern's entities.

    `ambiguous_stack` and `ambiguous_package` are members of the closed set and
    are not produced here, because neither is a function of the five values this
    signature takes: both are facts about DETECTION, known before any extractor
    runs. `_build` computes them from the resolution and the pack's own
    `concern_ambiguity` hook, and overrides this verdict when one applies. That
    override is confined to concerns this function already called `stubbed`, so
    it can never turn a real extraction into an ambiguity.

    `parse_failed` is likewise absent: it belongs to clause 1's input decoding,
    which is not landed. It is declared rather than invented later so the set
    stays closed.
    """
    noun = _entity_noun(pack_name, concern)
    n_entities = count_concern_entities(concern, content)
    if n_entities >= 1:
        return Verdict.populated(len(sources), n_entities)
    declared = input_classes(pack_name).get(concern)
    if not sources:
        # NOTHING was consumed. `sources` is the extractor's own report of what it
        # read, not a reading of the file it produced, so this is the structural
        # question the substring match was standing in for.
        if rung == "stub" and pack_name not in REGISTERED_PACKS:
            return Verdict.stubbed(
                StubReason.UNSUPPORTED_STACK,
                expected=_STUB_PACK_INPUT.expected,
                found=UNSUPPORTED_STACK_SENTENCE,
            )
        return Verdict.stubbed(
            StubReason.PRECONDITION_MISSING,
            expected=declared.expected if declared else f"a declared input for {concern}",
            found="no matching input",
        )
    return Verdict.stubbed(
        StubReason.NO_ENTITIES,
        expected=f"at least one {noun}",
        found=f"{len(sources)} input(s) consumed and 0 {noun}s",
    )


def _build(
    root: Path,
    docs_dir: str,
    decision_index_mode: str = "complete",
    cfg=None,
    *,
    report: list | None = None,
) -> dict:
    """Build the whole arch tree in memory. Returns {relpath: content_str}.

    `report`, when given, is filled with the REPORTED channel's records — the
    recorded verdict plus its annotation list — so a caller can print the
    coverage table without building the tree twice. It is an out-parameter
    rather than a second return value because `_build`'s single-dict return is
    bound by eleven test modules and the corpus harness.
    """
    resolution = resolve_stack(root, cfg)          # once; may raise on bad pin
    pack_name = resolution.pack_name
    # Clause 2's environment lane, checked BEFORE anything is extracted, hashed
    # or compared. A declared parser this machine cannot resolve produces no
    # verdict at all, so the refusal has to precede the first coverage record —
    # raising it mid-loop would leave a partial report behind.
    resolve_declared_parsers(pack_name)
    # Clause 1's pin, read in the same lane and at the same moment: a grammar
    # that resolves but cannot be versioned is an environment failure too, and
    # discovering it after the spine was built would mean discarding a tree.
    pack_parser_pins = parser_pins(pack_name)
    arch_stack_pin = getattr(cfg, "arch_stack", None) if cfg is not None else None

    spine: dict = {}
    all_sources: dict = {}
    coverage_records: list = []
    for fname in SPINE_FILES:                      # fixed order == hash domain
        concern = fname[:-3]
        extractor, rung = _resolve_extractor(
            concern, root, docs_dir, cfg,
            decision_index_mode=decision_index_mode, pack_name=pack_name,
        )
        content, sources = extractor(root, docs_dir)
        all_sources.update(sources)

        # `extractor` names WHAT PRODUCED THE CONTENT — the pack when a probe
        # rendered real content, `stub` when the empty-but-valid renderer did.
        # It is deliberately not derived from the verdict: a `no_entities`
        # concern's file was produced by its pack's extractor, and recording
        # `stub` there would say the pack never ran.
        #
        # This used to carry `concern_verdict`'s substring twin, and inherited
        # the same defect: the literal empty-but-valid sentence in a target
        # document made a record claim `stub` for a file a pack probe really
        # produced. `_is_empty_but_valid` asks the structural question instead —
        # is this file NOTHING BUT a title and the marker — which target content
        # inside a populated document cannot satisfy, because a populated
        # document has other lines.
        rendered_stub = _is_empty_but_valid(content)
        verdict = concern_verdict(concern, pack_name, rung, content, sources)

        # Clause 6's two detection verdicts, applied ONLY over an already-stubbed
        # concern so a real extraction can never be relabelled an ambiguity.
        #
        # `decision-index` is exempt from `ambiguous_stack` on purpose: it is the
        # UNIVERSAL concern, the ADR tree is stack-independent, and a repository
        # whose stack is undecided still has whatever ADR index it has. Saying
        # the decision index is ambiguous because two stacks matched would be the
        # same false sentence clause 4 removed from the other 20 stubs.
        # The decode-failure seam (`_concern_decode_verdict`, ADR-0129 clause
        # 7) sits between the two: called only when the
        # STACK resolved with no ambiguity at all (not merely "not ambiguous
        # for this concern" — `resolution.ambiguity is None`), and always
        # before `pack_ambiguity` so a pack's own `parse_failed` verdict is
        # never shadowed by an ambiguous-package check that runs after it.
        if verdict.kind == "stubbed":
            if resolution.ambiguity is not None and concern != "decision-index":
                verdict = ambiguous_stack_verdict(resolution.ambiguity)
            else:
                decoded = None
                if resolution.ambiguity is None:
                    decoded = _concern_decode_verdict(pack_name, concern, content, sources)
                if decoded is not None:
                    verdict = decoded
                else:
                    by_pack = pack_ambiguity(pack_name, concern, root)
                    if by_pack is not None:
                        verdict = by_pack

        spine[fname] = _apply_stub_line(content, verdict)

        coverage_records.append({
            "concern": concern,
            "extractor": "stub" if rendered_stub else pack_name,
            "inputs_found": sorted(sources),
            **verdict.as_record(),
        })

    if report is not None:
        # The declared map is passed rather than re-resolved inside, so a
        # `precondition_missing` remediation names the refresh command THIS
        # pack declared — clause 9's line and clause 7's advisory then read the
        # one `InputClass.refresh` field and cannot disagree about it.
        report.extend(reported_coverage(coverage_records, input_classes(pack_name)))

    # A pin's config file is hashed into sources (ADR-0066 point 9b). That hash
    # is NOT how a pin change reaches the drift gate any more: `sources` is the
    # one value `_manifest_drifted` stops comparing, so this route is closed
    # (PB-0073). Two routes survive it. A pin that selects a DIFFERENT pack
    # changes which extractors run, so the spine files themselves move and the
    # spine hash with them. A pin that names the pack auto-detection would have
    # chosen anyway leaves the spine byte-identical; there the route is
    # `_synthesize_overview`, which renders the pin inline into `overview.md`,
    # and `overview.md` stays byte-compared — pinned by
    # `test_arch_stack_pin_change_moves_overview`. The hash is kept because the
    # manifest is the provenance ledger and records what was read.
    if arch_stack_pin:
        cfg_file = _pin_config_path(root, cfg)
        if cfg_file is not None:
            # Gated: the config file is repo content like any other input, and a
            # provenance entry is not worth an unbounded read. An unreadable one
            # simply contributes no source row.
            raw_cfg = _safe_read_bytes(root, cfg_file, [])
            if raw_cfg is not None:
                all_sources[_rel(root, cfg_file)] = _sha256_hex(raw_cfg)

    # Spine hash: SHA-256 over exactly the four spine files, fixed order.
    spine_hash = _sha256_hex(b"".join(spine[f].encode("utf-8") for f in SPINE_FILES))

    overview = _synthesize_overview(spine, spine_hash, pack_name, arch_stack_pin)
    index = _synthesize_index(spine_hash)
    manifest = _synthesize_manifest(
        spine_hash, all_sources,
        {c: ic.globs for c, ic in input_classes(pack_name).items()},
        pack_parser_pins,
    )

    tree = dict(spine)
    tree["overview.md"] = overview
    tree["index.md"] = index
    tree[MANIFEST_REL] = manifest
    tree["_meta/coverage.json"] = _synthesize_coverage(coverage_records, pack_name)
    return tree


def _synthesize_overview(
    spine: dict, spine_hash: str, pack_name: str = STUB_PACK_NAME, arch_stack_pin: str | None = None
) -> str:
    """Deterministic narrative synthesized FROM the spine. Header carries the
    spine-hash stamp the gate checks (ADR-0060 Decision 2). Records the resolved
    stack pack + `arch_stack` pin (ADR-0066 point 6/9b) so a selection change
    drifts this `--dry-run`-checked file even when the spine is byte-identical.

    Since PB-0073 the pin's other trace — its config-file hash in
    `_meta/manifest.json["sources"]` — is the one value `_manifest_drifted` no
    longer compares. A pin that selects a different pack still reaches the gate
    through the spine, because different extractors produce different spine
    files. For a pin that names the pack auto-detection would have chosen, the
    spine is byte-identical and this inline render is the only remaining
    route."""
    def _count(fname: str, pat: str) -> str:
        m = re.search(pat, spine[fname])
        return m.group(1) if m else "?"

    entities = len(re.findall(r"^### ", spine["data-model.md"], re.MULTILINE))
    skills = _count("api-surface.md", r"## Skills \((\d+)\)")
    modules = _count("module-graph.md", r"\((\d+) modules")
    decisions = _count("decision-index.md", r"decisions \((\d+)\)")
    pin_note = (
        f"pinned via `arch_stack: {arch_stack_pin}`" if arch_stack_pin else "auto-detected"
    )
    # The Shape bullets are pack-aware: the crux dogfood vocabulary (schemas +
    # skills + the crux package) is only correct for the crux pack, so it is
    # emitted only there — keeping the crux overview byte-identical. Other packs
    # get stack-neutral pointers rather than crux-worded counts that would render
    # factually wrong (e.g. "0 entities … the crux-env CLI" for a FastAPI repo).
    # The decisions line is universal — decision-index is stack-portable.
    if pack_name == "crux":
        shape = [
            f"- **Data model:** {entities} entities derived from the schemas + manifest + skill"
            " metadata contract (see `data-model.md`).",
            f"- **Interface surface:** {skills} skills plus the `crux-env` CLI (see `api-surface.md`).",
            f"- **Module structure:** {modules} internal `crux` package modules (see `module-graph.md`).",
            f"- **Decisions:** {decisions} Accepted, non-archived decisions (see `decision-index.md`).",
        ]
    else:
        shape = [
            "- **Data model:** see `data-model.md`.",
            "- **Interface surface:** see `api-surface.md`.",
            "- **Module structure:** see `module-graph.md`.",
            f"- **Decisions:** {decisions} Accepted, non-archived decisions (see `decision-index.md`).",
        ]
    return _canon([
        "# Architecture overview",
        "",
        f"<!-- arch-spine-hash: sha256:{spine_hash} -->",
        "",
        "_Derived from the spine; regenerated whenever the spine moves. Stands on its own;"
        " ADR references live in `decision-index.md` as footnotes._",
        "",
        f"_Stack pack: `{pack_name}` ({pin_note})._",
        "",
        "## Shape",
        "",
        *shape,
        "",
        "## How to read this",
        "",
        "This tree IS the architecture, derived and kept current — not documentation. The four"
        " spine files are regenerated wholesale and byte-stable; this overview is synthesized from"
        " them. A drift-gate stamps the spine hash above; `derive-arch.py --dry-run` fails when the"
        " spine moved without a re-derive.",
        "",
    ])


def _synthesize_index(spine_hash: str) -> str:
    files = SPINE_FILES + ["overview.md"]
    return _canon(
        ["# arch", "",
         "_The project's derived architecture. No date — provenance is the spine hash + git._", "",
         f"**spine hash:** `sha256:{spine_hash}`", "",
         "## Files", ""]
        + [f"- [[arch/{f[:-3]}]]" for f in files]
        + ["", "See `_meta/manifest.json` for source hashes and tool pins."]
    )


def _synthesize_manifest(spine_hash: str, sources: dict,
                         declared_sources: dict | None = None,
                         parser_pins: dict | None = None) -> str:
    """Byte-stable JSON: no timestamps; sorted keys; tool pins + per-source hash.

    `parser_pins` is clause 1's pin, supplied by `core.parser_pins` for the
    RESOLVED pack and merged into `tool_pins` under a `parser:` prefix. Merged
    rather than given its own key so the manifest's key set stays the closed
    four the ledger has always carried, and so a parser upgrade drifts the same
    value a tooling upgrade does. A pack declaring no parser passes `{}` and
    the merge is the identity — which is what keeps this repository's own
    committed manifest, derived under the crux pack, byte-unmoved.

    `declared_sources` is clause 7's addition: each concern's declared source set
    — the set it would otherwise parse — recorded beside the hashes of the
    artifacts the extractors actually consumed. Together they are the provenance
    the staleness advisory reads.

    It records the GLOBS and not the files they resolve to, and that choice is
    the whole reason this key can sit inside the drift gate's compared region.
    Globs move only when a pack's declaration moves, which is a deliberate code
    change. A resolved file list would move whenever a matching file was added —
    including a file that changes no emitted byte — and the compared region would
    then flap on an edit the spine did not notice. Resolving the globs against
    the working tree is the reported channel's job, where it costs nothing.
    """
    doc = {
        "spine_hash": f"sha256:{spine_hash}",
        "tool_pins": {**TOOL_PINS, **(parser_pins or {})},
        "sources": dict(sorted(sources.items())),
        "declared_sources": {k: list(v) for k, v in
                             sorted((declared_sources or {}).items())},
    }
    return json.dumps(doc, indent=2, sort_keys=True) + "\n"


def _synthesize_coverage(records: list, pack_name: str) -> str:
    """Byte-stable JSON: no timestamps; sorted keys. `concerns` is a LIST (not a
    dict keyed by concern) so `sort_keys=True` cannot alphabetize the concerns
    out of SPINE_FILES order — records already arrive in that order from
    `_build`'s SPINE_FILES loop. `pack_name` is accepted for the `_synthesize_*`
    call-shape convention but is currently unused: each record already carries
    its own pack-derived `extractor`, so nothing here needs re-emitting it."""
    del pack_name  # unused: kept for signature parity; see docstring
    doc = {
        "_note": "internal, unstable schema; not a downstream compatibility surface",
        "concerns": records,
    }
    return json.dumps(doc, indent=2, sort_keys=True) + "\n"


#: The sentence every remediation opens with when no command establishes the
#: concern's input. Clause 9 asks each stub to say what would establish it, and
#: for five of the six reasons the honest answer is that nothing crux can run
#: will: the reader must supply a stack, resolve an ambiguity, fix an input, or
#: accept that the repository declares nothing. Saying so in one fixed phrase is
#: what keeps a reader from reading silence as "not implemented yet".
NO_COMMAND = "no command establishes this"


def remediation(record: dict, input_class: "InputClass | None" = None) -> str | None:
    """The clause 9 remediation line for one coverage record. Reported, never
    recorded.

    Returns `None` for a `populated` concern — there is nothing to remedy — and
    exactly one line for each of the six stub reasons.

    **`InputClass.refresh` is the SINGLE source of any command text here.** The
    clause 7 staleness advisory already prints that field, and a second table of
    commands in this function would let the two disagree the first time a pack's
    refresh command changed. So `precondition_missing` names a command only when
    the pack declared one, and every other reason opens with `NO_COMMAND`.
    `ambiguous_stack` is the one line that names a *key* rather than a command:
    pinning `arch_stack` is a config edit, and a config edit is not a command, so
    the line says a human can pin it and never phrases it as something to run.

    **Clause 11's seam is a prohibition, not an omission.** "Run your application
    to recover the routes" is the obvious remediation for a route stub and is
    precisely the sentence a crux surface must not say: the runtime escalation
    path is out of scope for this channel, so no line here names it, its
    environment variable, or the module that would house it.

    The line is built from the record's own `expected` and `found` — the same
    strings the recorded verdict carries — rather than from a parallel
    vocabulary, so a remediation cannot describe a stub the recorded channel
    did not report.
    """
    if record.get("verdict") == "populated":
        return None
    reason = record.get("stub_reason")
    expected = record.get("expected", "")
    found = record.get("found", "")
    refresh = getattr(input_class, "refresh", "") if input_class is not None else ""

    if reason == StubReason.PRECONDITION_MISSING.value:
        if refresh:
            return f"run `{refresh}`, which produces {expected}"
        return f"{NO_COMMAND} — the derive needs {expected}"
    if reason == StubReason.UNSUPPORTED_STACK.value:
        return f"{NO_COMMAND} — no registered pack claims this repository's stack"
    if reason == StubReason.AMBIGUOUS_STACK.value:
        return f"{NO_COMMAND}; a human can pin `arch_stack` in `.bionic.yml`"
    if reason == StubReason.AMBIGUOUS_PACKAGE.value:
        return f"{NO_COMMAND} — {found}; a per-package graph is open follow-on work"
    if reason == StubReason.PARSE_FAILED.value:
        return f"{NO_COMMAND} — {found} would not decode"
    if reason == StubReason.NO_ENTITIES.value:
        return f"{NO_COMMAND} — {found}"
    # The set is closed and every member is answered above. A record carrying
    # something else is a caller defect, and a line that named it would put an
    # unreviewed string on a reader-facing surface.
    raise ValueError(f"no remediation for stub reason {reason!r}")


def reported_coverage(records: list, declared: dict | None = None) -> list[dict]:
    """The REPORTED channel (clause 2): every recorded verdict, plus annotations
    and the clause 9 remediation line.

    Built FROM the recorded records rather than beside them, so the reported
    channel can never disagree with the byte-compared one about a verdict. Each
    record here is a COPY: the list the caller passes is the one
    `_synthesize_coverage` serializes into `_meta/coverage.json`, and a function
    that annotated it in place would put a reported-only string into the
    byte-compared tree. No corpus golden moving is the proof that it does not.

    `declared` is the resolved pack's per-concern `InputClass` map, and it is a
    parameter for the same reason `staleness.annotate` takes one: the caller has
    already resolved the pack, and a test can state the declaration it is testing
    rather than build a repository that happens to produce one. Absent, no pack
    declares a refresh command and every `precondition_missing` line falls back
    to naming what the derive needs.

    Two added keys, and they carry their absence differently on purpose.
    `annotations` is always present — a consumer should find a list, not a
    `KeyError`, the day the first one fires — while `remediation` is present only
    when there is a line, because a `populated` concern has nothing to remedy and
    a null field in the stdout envelope would be noise rather than information.

    `possibly_stale` is clause 7's work and is attached later by
    `staleness.annotate`, so every list here is empty at this point.
    """
    declared = declared or {}
    out: list[dict] = []
    for rec in records:
        rep = dict(rec, annotations=[])
        line = remediation(rep, declared.get(rep.get("concern")))
        if line is not None:
            rep["remediation"] = line
        out.append(rep)
    return out


#: Where the `detail` column starts, so a remediation row can be indented under
#: it rather than under the concern name. Derived from the two fixed widths
#: below plus their separating spaces, so widening a column moves the
#: continuation row with it.
_COVERAGE_CONCERN_W = 15
_COVERAGE_VERDICT_W = 10
_COVERAGE_DETAIL_COL = _COVERAGE_CONCERN_W + 1 + _COVERAGE_VERDICT_W + 1


def coverage_table(records: list) -> str:
    """The human-readable per-concern coverage table (clause 9).

    Goes to STDERR, never stdout: stdout carries the JSON envelope the drift-gate
    workflow parses, and a table interleaved into it would break that parse. It
    is written nowhere under `arch/` either way — the reported channel leaves no
    trace in the byte-compared tree.

    A stub's remediation gets its OWN row, indented under the detail column,
    rather than being appended to the detail cell. The detail cell already
    carries an expectation and a finding, and a third clause on the same line
    would run past any terminal width and bury the one sentence a reader acts on.
    """
    head = (f"{'concern':<{_COVERAGE_CONCERN_W}} "
            f"{'verdict':<{_COVERAGE_VERDICT_W}} {'detail'}")
    lines = [head, "-" * len(head)]
    for rec in records:
        if rec["verdict"] == "populated":
            detail = f"{rec['n_entities']} entities from {rec['n_sources']} sources"
        else:
            detail = (f"{rec['stub_reason']}: expected {rec['expected']}, "
                      f"found {rec['found']}")
        note = "".join(f"  [{a}]" for a in rec.get("annotations", ()))
        lines.append(f"{rec['concern']:<{_COVERAGE_CONCERN_W}} "
                     f"{rec['verdict']:<{_COVERAGE_VERDICT_W}} {detail}{note}")
        if rec.get("remediation"):
            lines.append(" " * _COVERAGE_DETAIL_COL + f"remediation: {rec['remediation']}")
    return "\n".join(lines)


#: What a strict failure names as the thing that required the concern. Two
#: values, because the two routes into the gate are answerable differently: a
#: reader fixes the first by editing the tree's manifest and the second by
#: dropping a flag.
REQUIRED_BY_MANIFEST = "arch.require"
REQUIRED_BY_FLAG = "--strict"


def strict_failures(
    records: list,
    require: tuple[str, ...] = (),
    strict: bool = False,
) -> list[dict]:
    """The clause 5 gate: which recorded verdicts fail this run.

    A pure function of the coverage records, the required set and the flag.
    Deliberately pure, and deliberately reading `verdict` and nothing else:

      * it reads the RECORDED verdict, so the gate cannot be moved by anything
        outside the byte-compared channel;
      * it never reads `annotations`, which is how clause 5's last sentence is
        kept — `possibly_stale` never fails strict, because strict must not
        depend on the commit graph. An annotation added to the vocabulary later
        inherits that silence rather than needing a carve-out.

    A concern named by `arch.require` AND caught by `--strict` is reported once,
    attributed to `arch.require`: the tree declared it, and saying the flag
    required it would send the reader to the wrong remedy.

    Returns copies. The records belong to the reported channel and a gate that
    mutated them would edit the table its own caller is about to print.
    """
    required = set(require)
    out: list[dict] = []
    for rec in records:
        named = rec["concern"] in required
        if not (named or strict):
            continue
        if rec.get("verdict") == "populated":
            continue
        out.append(dict(rec, required_by=REQUIRED_BY_MANIFEST if named
                        else REQUIRED_BY_FLAG))
    return out


def _gate(records: list, require, strict: bool, failures: list | None) -> None:
    """Apply the gate and deposit its findings in the caller's out-parameter.

    `failures` is an out-parameter for the same reason `report` is: `derive` and
    `dry_run` have return types bound by their callers. Asking for a gate with
    nowhere to report it is refused rather than ignored — a caller that set
    `strict=True` and got a silently ungated derive would have the one failure
    mode this clause exists to remove.
    """
    if not (require or strict):
        return
    if failures is None:
        raise ValueError(
            "require/strict need a `failures` list to report into; a gate with "
            "nowhere to report is an ungated derive"
        )
    failures.extend(strict_failures(records, tuple(require), strict))


def derive(
    root: Path,
    docs_dir: str = "bionic",
    decision_index_mode: str = "complete",
    cfg=None,
    *,
    report: list | None = None,
    require: tuple[str, ...] = (),
    strict: bool = False,
    failures: list | None = None,
) -> list[str]:
    """Regenerate <docs_dir>/arch/ wholesale. Returns the list of written paths.

    The tree is written and THEN gated. A strict failure is a finding about the
    extraction, not a reason to leave the spine stale — refusing to write would
    make a required-but-stubbed concern un-fixable without first editing the
    manifest, and would leave the drift gate red for an unrelated reason.
    """
    root = Path(root)
    own_report: list = [] if report is None else report
    tree = _build(root, docs_dir, decision_index_mode, cfg, report=own_report)
    arch = root / docs_dir / "arch"
    root_r = root.resolve()
    # Containment is checked BEFORE the first mkdir, not after the last write.
    # `arch` itself is attacker-reachable: a symlink at `<docs_dir>/arch` pointing
    # outside the repository made `(arch / "_meta").mkdir(...)` create a directory
    # at the link's target, and the per-file writes then landed there too. `_rel`
    # ran only afterwards, so the refusal it raised arrived one destroyed file too
    # late. Resolving first is what makes "the deriver writes only inside the
    # repository" true before anything happens rather than after.
    arch_r = _resolved_or_refuse(arch, "the arch directory")
    if not (arch_r == root_r or arch_r.is_relative_to(root_r)):
        raise ArchWriteRefused(
            f"refusing to derive: {arch} resolves to {arch_r}, which is outside "
            f"the repository root {root_r}. Nothing was written. Remove the "
            f"symlink at that path and re-run."
        )
    (arch / "_meta").mkdir(parents=True, exist_ok=True)
    written = []
    for rel, content in sorted(tree.items()):
        p = arch / rel
        # Per-destination, because a contained `arch/` can still hold a symlink at
        # a spine path or at `_meta/`. Refusal is an error and never a silent
        # skip: a derive that quietly omitted a spine file would leave a stale one
        # on disk and report success.
        dest = _resolved_or_refuse(p, f"the spine path {rel}")
        if not dest.is_relative_to(arch_r):
            raise ArchWriteRefused(
                f"refusing to write {rel}: it resolves to {dest}, outside the "
                f"arch tree at {arch_r}. Remove the symlink at that path and "
                f"re-run."
            )
        p.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(p, content)
        written.append(_rel(root, p))
    _gate(own_report, require, strict, failures)
    return sorted(written)


class ArchWriteRefused(RuntimeError):
    """A derive destination escapes the repository, so nothing is written.

    Routed by `derive-arch.py`'s broad handler to exit 2 — the no-verdict lane —
    which is the correct lane: a refusal means no tree was produced, so nothing a
    caller reads may be taken as a document finding.
    """


def _resolved_or_refuse(p: Path, what: str) -> Path:
    """`p.resolve()`, or `ArchWriteRefused` when the path cannot be resolved.

    Non-strict resolution, so a destination that does not exist yet resolves to
    where it WOULD be created. A symlink loop is the case that raises, and it
    refuses rather than defaulting to a path that was never checked.
    """
    try:
        return p.resolve()
    except OSError as exc:
        raise ArchWriteRefused(f"refusing to derive: cannot resolve {what} ({exc})") from exc


def _atomic_write_text(p: Path, content: str) -> None:
    """Write `content` to `p` atomically — temp file in the same directory, then
    `os.replace` — following `recover.py`'s pattern for the same reason.

    Plain `write_text` truncates and then writes, so an interrupted derive left a
    PARTIAL `manifest.json` on disk that the drift gate then read and compared.
    `os.replace` is atomic on POSIX: a reader sees either the whole previous file
    or the whole new one.

    It also refuses to write THROUGH a symlink at `p` — a rename replaces the
    link itself and never touches the link's target — so the containment refusal
    in `derive` is belt and braces rather than the only guard.
    """
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=f".{p.name}.", suffix=".tmp")
    try:
        # `mkstemp` creates 0600 and `os.replace` carries the temp file's mode
        # onto the destination, so writing atomically would otherwise have made
        # every spine file owner-only — a permission change nobody asked for,
        # invisible to git, which records no non-exec mode bit.
        os.fchmod(fd, 0o666 & ~_process_umask())
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, p)                              # atomic
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _process_umask() -> int:
    """The current umask, read the only way POSIX offers: set it and put it back."""
    cur = os.umask(0o022)
    os.umask(cur)
    return cur


def _is_string_map(value: object) -> bool:
    """True iff *value* is a mapping whose every key and value is a `str`.

    The one shape the manifest's uncompared region may hold. Empty is well
    shaped; `bool` is not admitted as a string, and no numeric, null, list or
    nested-object member is.
    """
    return isinstance(value, dict) and all(
        isinstance(k, str) and isinstance(v, str) for k, v in value.items()
    )


def _manifest_drifted(on_disk_text: str, built_text: str) -> bool:
    """True when the on-disk `_meta/manifest.json` differs from the built one in
    any way except the VALUE of its per-source hash map.

    `sources` maps every tracked source file to its SHA-256, so any content edit
    to any tracked file moved this one key and tripped the gate — even when all
    four spine files, `overview.md`, `index.md` and `coverage.json` came out
    byte-identical. That false positive is what this excludes. Dropping the whole
    manifest from the gate instead would delete toolchain-pin detection and let
    the committed provenance lie about which tools produced the spine.

    The comparison target is the on-disk BYTES, not a re-rendering of them. This
    takes the built manifest, substitutes the on-disk `sources` value into it,
    and renders the result through the build's own serializer — so `rendered` is
    byte-identical to what `_synthesize_manifest` would have written for that
    sources map. Comparing that against `on_disk_text` keeps duplicate keys, key
    order, indentation and trailing whitespace inside the gate.

    Normalizing BOTH sides through a parse instead — the first form of this
    guard — discarded exactly those byte-level properties. A manifest carrying a
    duplicate `tool_pins` key parses to the last one, so a file whose first
    `tool_pins` was forged displayed the forged pins to a human reader and
    reported CLEAN. Substituting into the built doc closes that: the forged
    duplicate survives in `on_disk_text` and the render never reproduces it.

    A manifest that lacks `sources` entirely is tolerated: the key is dropped
    from the render too, so the remaining keys still compare.

    The exemption is bounded by the shape that justified it. The false positive
    it removes was measured on ONE shape — a mapping of tracked source path to
    SHA-256 hex — so a present `sources` value of any OTHER shape reports drift
    rather than inheriting the exemption. Without that bound the uncompared
    region admits arbitrary JSON: a list, a scalar, or a nested document placed
    there would be substituted into the render verbatim and reported CLEAN.
    Only the ON-DISK value is shape-checked; the built value is this process's
    own output, so checking it would assert against the deriver instead of
    against the artifact. A non-string KEY cannot arrive through `json.loads`,
    whose object keys are always strings; the predicate checks keys anyway,
    because the contract is about the value's shape and not about the parser.
    An EMPTY mapping is vacuously well-shaped and keeps the exemption.

    Fail-closed on input this cannot parse or render. `json.loads` raises
    `ValueError` on malformed text and `RecursionError` — not a `ValueError` —
    on JSON nested past the interpreter's recursion limit, and deep nesting can
    raise the same from `json.dumps`. `MemoryError` joins them for the reason
    `_PARSE_FAILED` gives: a parser that overflows its stack raises that one
    instead, and the two come from one cause. All of them report DRIFT. Reporting clean
    from a guard that crashed would make the manifest undriftable, which is the
    opposite of the point.
    """
    if on_disk_text == built_text:
        return False
    try:
        disk = json.loads(on_disk_text)
        built = json.loads(built_text)
        if not isinstance(disk, dict) or not isinstance(built, dict):
            return True
        expected = dict(built)
        if MANIFEST_UNCOMPARED_KEY in disk:
            uncompared = disk[MANIFEST_UNCOMPARED_KEY]
            if not _is_string_map(uncompared):
                return True
            expected[MANIFEST_UNCOMPARED_KEY] = uncompared
        else:
            expected.pop(MANIFEST_UNCOMPARED_KEY, None)
        rendered = json.dumps(expected, indent=2, sort_keys=True) + "\n"
    except (*_PARSE_FAILED, TypeError):
        return True
    return rendered != on_disk_text


def dry_run(
    root: Path,
    docs_dir: str = "bionic",
    decision_index_mode: str = "complete",
    cfg=None,
    *,
    report: list | None = None,
    require: tuple[str, ...] = (),
    strict: bool = False,
    failures: list | None = None,
) -> list[str]:
    """Return the list of arch paths that would change (drift). Empty == clean.

    Seven of the derived tree's eight keys are compared byte-for-byte. The eighth,
    `_meta/manifest.json`, is compared byte-for-byte on everything except the
    value of `sources` — see `_manifest_drifted`. Stale-artifact detection is
    unchanged.

    `report` is the same out-parameter `derive` takes: the reported channel for
    the tree this run WOULD write, so a dry run can print verdicts without a
    second build. `require`, `strict` and `failures` are the same clause 5 gate
    `derive` carries, over the verdicts this run computed. The two findings stay
    separable: drift is the returned list, a strict failure lands in `failures`,
    and a reader is never told a byte-identical tree is stale.
    """
    root = Path(root)
    own_report: list = [] if report is None else report
    tree = _build(root, docs_dir, decision_index_mode, cfg, report=own_report)
    arch = root / docs_dir / "arch"
    drift = []
    on_disk = set()
    if arch.exists():
        # `_recovered/` is the decision-recovery candidate state file's home
        # (ADR-0062 D4): a scan artifact that lives inside `arch/` by design and
        # that no derive writes. It is not a derive output, so it is neither
        # compared nor swept as a stale artifact; everything else under `arch/`
        # still is.
        on_disk = {_rel(root, p) for p in arch.rglob("*")
                   if p.is_file() and p.relative_to(arch).parts[0] != RECOVERED_DIRNAME}
    for rel, content in tree.items():
        p = arch / rel
        # Gated: the tree being compared is on disk and is therefore attacker-
        # reachable in the same way every other input is. A path that is not a
        # contained, regular, in-bound file reads as the empty string and so
        # reports DRIFT — which is the correct answer, because it is not the file
        # this derive would have written.
        on_disk_text = _read_gated(root, p)
        if rel == MANIFEST_REL:
            drifted = _manifest_drifted(on_disk_text, content)
        else:
            drifted = on_disk_text != content
        if drifted:
            drift.append(_rel(root, p))
    # Files on disk the derive would not produce are also drift (stale artifacts).
    would_write = {_rel(root, arch / rel) for rel in tree}
    for extra in sorted(on_disk - would_write):
        drift.append(extra)
    _gate(own_report, require, strict, failures)
    return sorted(set(drift))


# ═══════════════════════════════════════════════════════════════════════════
# U7 — WHY THE ADR-0074 CONFIDENCE LAYER PASSED THROUGH HERE (sequencing history)
# `_synthesize_coverage` read `_confidence_grade`, `_escalation_offered` and
# both `_COVERAGE_*` vocabularies, so leaving them in the facade would have made
# the core depend on the module that re-exports it. U7 moved the layer here
# verbatim, and U8 then deleted it: clause 12's byte-identity gate had to close
# on an unchanged tree BEFORE any behaviour moved, so relocate-then-retire was
# two units rather than one. Both units are done. The retirement itself, and the
# measurement that justified it, are recorded ONCE — in the canonical clause 10
# note twenty lines below, which is the note to read and to keep current.
#
# The PEP 508 / pyproject dependency-name readers below are the exception that
# SURVIVES clause 10: `_pep508_names_in`, `_requirements_direct_dep_names`,
# `_pyproject_direct_dep_names`, `_toml_strip_comment` and
# `_array_closes_outside_quotes` read a declared dependency set, which detection
# needs under clause 6. They are kept.
# ═══════════════════════════════════════════════════════════════════════════

# ── ADR-0096 clause 3: the PB-0069 coverage vocabularies are RETIRED ────────
# `_COVERAGE_STUB_INPUTS` and `_COVERAGE_REASONS` are gone, and with them the
# `inputs_missing` and `reason` fields they fed. Both tables shipped EMPTY, so
# every one of the 40 corpus records fell through to the templated default and
# read `"<concern> <status> under the <pack> pack"` — a restatement of two
# fields already in the record, carrying no information about why. Clause 3
# replaces them with a closed reason set that is computed rather than tabulated,
# so a stub cannot be added without naming its precondition.

# ── ADR-0096 clause 10: the confidence layer is RETIRED ─────────────────────
# ADR-0074's four-level grade scale, its expectation-marker table, its
# confidence floors and partial-capture probes, and the three `coverage.json`
# fields they fed are gone from here. The reason is a measurement, not a
# preference: the floor and probe tables shipped EMPTY, so the floor defaulted
# to 1 and no probe ever fired. All 17 populated corpus concerns graded `high`,
# `medium` was unreachable, and a concern with one captured source graded
# exactly like one with 537. Only 2 of 23 stubs graded `low`; the other 21
# reported "no expectation marker present", including a Rails application with
# 381 routes sitting in its routes file.
#
# What replaces it is not a better scale. It is a count and a named
# precondition — clause 2's two channels and clause 3's six stub reasons.
#
# `arch_confidence_threshold` is retired with it. A tree that still sets the key
# is tolerated silently through the §14.1 forward-compat valve, which ignores
# unknown top-level keys on read; there is deliberately no fail-closed branch,
# because refusing a config a previous version told the user to write would turn
# an upgrade into an outage over a key that no longer does anything.
#
# What SURVIVES clause 10 is directly below: the PEP 508 and pyproject
# dependency-name readers. They were built for the expectation markers, and they
# outlive them because clause 6 detection needs to read a declared dependency
# set. They are kept deliberately, not by oversight.


def _pep508_names_in(fragment: str) -> set[str]:
    """Extract the casefolded distribution names from the quoted PEP 508 strings
    in a pyproject `dependencies` array fragment."""
    out: set[str] = set()
    for quoted in re.findall(r"""["']([^"']+)["']""", fragment):
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)", quoted.strip())
        if m:
            out.add(m.group(1).casefold())
    return out


def _requirements_direct_dep_names(text: str) -> set[str]:
    """Casefolded direct-dependency names from a requirements.txt.

    Commented lines (``# fastapi``), blank lines, and pip options (``-r``,
    ``-e``, ``--hash``) never contribute. Line-scanned and deterministic.
    """
    names: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        line = line.split(" #", 1)[0].strip()  # drop an inline comment
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)", line)
        if m:
            names.add(m.group(1).casefold())
    return names


def _toml_strip_comment(line: str) -> str:
    """Drop a TOML line comment, respecting quoted strings — a ``#`` inside a
    quoted string is data (e.g. a VCS URL's ``#egg=`` fragment), not a comment.
    Either quote opens a string; the matching quote closes it. Escapes are
    irrelevant here — PEP 508 dep specs and distribution names never carry an
    escaped quote. Deterministic and interpreter-independent (byte-stability
    matters — a marker verdict feeds coverage.json)."""
    out: list[str] = []
    quote = ""
    for ch in line:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = ""
        elif ch == "#":
            break
        else:
            if ch in "\"'":
                quote = ch
            out.append(ch)
    return "".join(out)


def _array_closes_outside_quotes(fragment: str) -> bool:
    """True iff a ``]`` appears outside any quoted string in ``fragment`` — the
    real end of a ``dependencies = [ ... ]`` array. A ``]`` inside a quoted
    extras spec (``"fastapi[all]"``) does not close the array."""
    quote = ""
    for ch in fragment:
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch == "]":
            return True
    return False


# Poetry marks an extra with an inline ``optional = true`` — an extra is not a
# direct dependency (mirrors the [project.optional-dependencies] exclusion).
_POETRY_OPTIONAL_RE = re.compile(r"\boptional\s*=\s*true\b")


def _pyproject_direct_dep_names(text: str) -> set[str]:
    """Casefolded direct-dependency names from a pyproject.toml.

    Line-scanned rather than parsed with ``tomllib`` because ``tomllib`` is
    absent before Python 3.11 — using it would force a conditional import or a
    third-party dependency for a script that must stay stdlib-only. (Confinement
    is NOT the reason: ``tomllib`` parses data, it does not execute target code.)
    A hand scan is also byte-stable across interpreters — a marker verdict feeds
    coverage.json, which the `--dry-run` gate compares byte-for-byte. Included
    regions: PEP 621 ``[project]``'s ``dependencies`` array, and Poetry's
    ``[tool.poetry.dependencies]`` table. EXCLUDED: ``[project.optional-
    dependencies]``, any ``[tool.poetry.group.*]`` table, a Poetry entry marked
    ``optional = true``, and every other section — extras and dev groups are not
    direct dependencies. Comment stripping and array-close detection are
    quote-aware, so a ``#`` or ``]`` inside a quoted PEP 508 string never ends a
    comment or the array early.
    """
    names: set[str] = set()
    section = ""             # current [table] header, "" before the first one
    in_project_deps = False  # inside the `dependencies = [` array of [project]
    for raw in text.splitlines():
        line = _toml_strip_comment(raw).rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        header = re.match(r"^\[([^\]]+)\]", stripped)
        if header:
            section = header.group(1).strip()
            in_project_deps = False
            continue
        if section == "project":
            if not in_project_deps:
                m = re.match(r"^dependencies\s*=\s*\[(.*)$", stripped)
                if m:
                    rest = m.group(1)
                    names |= _pep508_names_in(rest)
                    in_project_deps = not _array_closes_outside_quotes(rest)
                continue
            names |= _pep508_names_in(stripped)
            if _array_closes_outside_quotes(stripped):
                in_project_deps = False
        elif section == "tool.poetry.dependencies":
            m = re.match(r"""^["']?([A-Za-z0-9][A-Za-z0-9._-]*)["']?\s*=""", stripped)
            if m and not _POETRY_OPTIONAL_RE.search(stripped):
                names.add(m.group(1).casefold())
    return names
