"""The crux dogfood stack pack (ADR-0060 Decision 5, ADR-0066 point 3).

Three extractors over this repository's own sources: the JSON Schemas plus the
manifest shape plus SKILL.md metadata (data-model), the skill catalog plus the
crux-env CLI (api-surface), and the intra-package import graph of the `crux`
Python package (module-graph). The universal decision-index probe is NOT bound
here — it lives in `core.extract_decision_index` and the core binds it for every
pack.

Static, in that nothing in this repository is imported or executed, and every
file read is hashed into `sources`. NOT stdlib-only: PyYAML parses
`<docs_dir>/manifest.yml` here with `yaml.safe_load`, and each SKILL.md's
frontmatter through `core._frontmatter`. Both YAML-reading concerns name it in
`INPUT_CLASSES`, and that declaration is what makes the claim true rather than
aspirational: `resolve_declared_parsers` refuses the derive at exit 2 before any
extractor runs when PyYAML is unresolvable, so no run of this pack reaches
`_frontmatter`'s regex fallback, whose answer differs. The resolved version
reaches the byte-compared `tool_pins` through `parser_pins`, and PyYAML is pinned
exactly at every declaring site. The other two parsers are stdlib — `json` for
the JSON Schemas and the regenerated catalog, `ast` for
`crux/scripts/crux-env.py` and for the import graph.

Two of these extractors sit on the ADR-0079 gated-projection layer in `core`,
reading a fact through the artifact that already regenerates it rather than
re-parsing the source a second time. That is why this pack is the one that
`StaleProjectionInput` can reach.

**This pack derives THIS repository's spine, which is why clause 12 keeps it
shipped and in place.** Moving it would change `bionic/arch/` and this repo's own
goldens, confounding the byte-identity gate the split is measured by. It moved
here verbatim under ADR-0096 clause 12; not a line was rewritten in transit.
That is HISTORY, true of the U7 move rather than of this file today: a later
unit escaped the JSON Schema `title`, property name and type that
`extract_data_model` renders, because all three reach `data-model.md` from a
repository-controlled file and a `title` carrying a newline was injecting a
second H1.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

from ..core import (
    NO_EXTRACTOR,
    DetectResult,
    InputClass,
    ParserUnavailable,
    Probe,
    _PARSE_FAILED,
    _canon,
    _cell,
    _extract_module_graph,
    _frontmatter,
    _prose_cell,
    _rel,
    _safe_read_bytes,
    _sha256_hex,
    _skills_catalog_projection,
)


# ───────────────────────────── extractors ──────────────────────────────────
# Each returns (markdown, sources) where sources maps repo-relative path -> sha256.

def extract_data_model(root: Path, docs_dir: str) -> tuple[str, dict]:
    """Data model = the JSON Schemas + the manifest shape + the SKILL.md metadata
    contract. Introspected from the real files (Decision 4, reality-authoritative)."""
    sources: dict = {}
    out = ["# Data model", "",
           "_Derived from the project's schemas, manifest, and skill metadata contract._", ""]

    schemas_dir = root / "crux" / "schemas"
    schema_paths = sorted(schemas_dir.glob("*.json")) if schemas_dir.exists() else []
    if not schema_paths:
        return _canon([out[0], "", NO_EXTRACTOR.rstrip("\n")]), sources

    out += ["## Entities (JSON Schema)", ""]
    for sp in schema_paths:
        # Containment + the 2 MB bound, as every other pack's reads take. The
        # target here is the crux monorepo itself rather than an arbitrary
        # repository, which lowers the exposure but not the rule: `_rel` below
        # raises on a path resolving outside the root, so an ungated read is a
        # crash as well as an off-root read.
        raw = _safe_read_bytes(root, sp, [])
        if raw is None:
            continue
        sources[_rel(root, sp)] = _sha256_hex(raw)
        try:
            doc = json.loads(raw.decode("utf-8"))
        except Exception:
            continue
        # Every value below is repository-controlled — it is whatever the JSON
        # Schema file says — and all three land in written markdown, so all
        # three are escaped at the render.
        #
        # The heading takes `_prose_cell` rather than `_cell`: a schema `title`
        # is prose, and a backtick in it is legitimate inline code (the same
        # call `_prose_cell` documents for skill descriptions). What matters for
        # a heading is that newline and carriage return are FLATTENED, because
        # every structural markdown injection needs line-start position — a
        # title of "Book\n# Owned" was rendering a second H1, "Book\n```" an
        # unclosed fence that swallowed the rest of the file, and "Book\n[^x]:"
        # a real footnote definition.
        name = _prose_cell(doc.get("title") or sp.stem)
        props = doc.get("properties", {})
        required = set(doc.get("required", []))
        out += [f"### {name}", "",
                f"_source: `{_cell(_rel(root, sp))}`_", "",
                "| field | type | required |", "|---|---|---|"]
        for field in sorted(props):
            spec = props[field]
            typ = spec.get("type", spec.get("$ref", spec.get("enum", "—")))
            # `_cell` per element, NOT over the joined string: the ` \| `
            # separator is a deliberate literal, and escaping after the join
            # would turn its backslash into `\\` and free the pipe it was
            # escaping — the ordering trap `_cell`'s own docstring names.
            if isinstance(typ, list):
                typ = " \\| ".join(_cell(t) for t in typ)
            else:
                typ = _cell(typ)
            req = "yes" if field in required else "no"
            out.append(f"| `{_cell(field)}` | {typ} | {req} |")
        out.append("")

    # Manifest shape. The arch uses only the top-level KEY NAMES, so provenance is
    # hashed over that derived subset — NOT the whole file. This is accurate (records
    # exactly what was derived from) and stops a routine counter bump (`adr.next_number`,
    # `promptbook.next_number`) from drifting arch when the shape is unchanged.
    #
    # Gated like every other read in this engine. This one was the last ungated
    # site whose result IS hashed, so an off-root `manifest.yml` symlink at least
    # crashed in `_rel` — loud, but a crash rather than a refusal, and only after
    # the whole target was already resident.
    man = root / docs_dir / "manifest.yml"
    man_raw = _safe_read_bytes(root, man, []) if man.exists() else None
    if man_raw is not None:
        man_text = man_raw.decode("utf-8", errors="replace")
        # A REAL PARSE, not a pattern read (ADR-0097 part 3). PyYAML is the
        # parser: the stdlib has none, and `derive-arch.py` pins PyYAML in its
        # PEP 723 block for exactly this reason.
        #
        # The pattern this replaced was `^([a-z_]+)\s*:` under MULTILINE, whose
        # result is hashed into `_meta/manifest.json["sources"]` as the sha256
        # of the comma-joined list below. So the DERIVED SUBSET has to survive
        # the conversion unchanged, or the provenance hash moves while the
        # rendered spine stays put. `sorted` then `dict.fromkeys` reproduces the
        # pattern's ordering and de-duplication exactly; the two were measured
        # equal on this tree before the conversion landed.
        #
        # A manifest that does not parse yields an empty key list, which is the
        # same answer the pattern gave for a file with no top-level key at
        # column 0 — a degradation, never a crash.
        #
        # Imported at the use site rather than at module scope: `core` and the
        # packs stay importable on an interpreter without PyYAML, and a derive
        # that actually needs the parser fails in the environment lane rather
        # than at import of the package.
        #
        # A missing PyYAML raises `ParserUnavailable`, which is the class this
        # engine reserves for a declared parser it cannot resolve on this
        # machine. `derive-arch.py` prints the exception TYPE beside the message
        # and returns 2, and the type is what tells the two exit-2 lanes apart —
        # so a bare `ImportError` would reach the same exit code while naming
        # the wrong condition. It is NOT caught into a stub: a silent
        # degradation here would write a spine with an empty manifest key list
        # and move the byte-compared provenance hash.
        try:
            import yaml
        except ImportError as exc:
            raise ParserUnavailable(
                "the crux pack's data-model parses `manifest.yml` with PyYAML, "
                "which this machine cannot import; the stdlib has no YAML "
                "parser, and `derive-arch.py` pins PyYAML in its PEP 723 block"
            ) from exc

        try:
            man_doc = yaml.safe_load(man_text)
        except Exception:
            man_doc = None
        top = man_doc.keys() if isinstance(man_doc, dict) else ()
        keys = sorted(str(k) for k in top)
        keys = list(dict.fromkeys(keys))
        # The PROVENANCE hash is over the raw parsed keys — it records what was
        # read, and escaping before hashing would record what was rendered.
        sources[_rel(root, man)] = _sha256_hex(",".join(keys).encode("utf-8"))
        # Escaped at the RENDER, like every other repository-controlled value in
        # this pack. The pattern this reader replaced was `^([a-z_]+)\s*:`, and
        # its character class was the only thing keeping a newline out of this
        # cell: a real parse takes the literal YAML was handed, and a quoted key
        # carrying "\n## " opened a second H2 that `count_concern_entities` then
        # counted. `_cell` rather than `_prose_cell`, because each key renders
        # inside a code span and `_cell` is the one that neutralizes backtick.
        # No key on any tree today carries any of these characters, so this moves
        # no rendered byte — it removes the way it could have moved.
        out += ["## Manifest shape", "",
                f"_source: `{_cell(_rel(root, man))}` — top-level keys:_", "",
                ", ".join(f"`{_cell(k)}`" for k in keys), ""]

    # SKILL.md metadata contract — union of metadata keys across skills (sorted).
    skills_dir = root / "crux" / "skills"
    meta_keys: set = set()
    n_skills = 0
    if skills_dir.exists():
        for sk in sorted(skills_dir.glob("*/SKILL.md")):
            # The worst of the two ungated sites, and the reason both are gone.
            # This result is never hashed, so `_rel`'s incidental ValueError
            # never fired and an off-root symlink here was COMPLETELY SILENT: a
            # `crux/skills/*/SKILL.md` pointing at a 2 GB file outside the root
            # was read whole, measured at 4.33 GB RSS, and the derive exited on
            # its ordinary drift verdict with no warning and no residual. Same
            # gate as the sibling read in `extract_api_surface`, same decode.
            raw = _safe_read_bytes(root, sk, [])
            if raw is None:
                continue
            fm = _frontmatter(raw.decode("utf-8", errors="replace"))
            md = fm.get("metadata") or {}
            if isinstance(md, dict):
                meta_keys |= set(md.keys())
            n_skills += 1
    if meta_keys:
        out += ["## Skill metadata contract", "",
                f"_union of `metadata.*` keys across {n_skills} skills:_", "",
                ", ".join(f"`{_cell(k)}`" for k in sorted(meta_keys)), ""]
    return _canon(out), sources


def _cli_verbs(source: str) -> set[str]:
    """The subcommand verbs `crux/scripts/crux-env.py` registers, by `ast` parse.

    A REAL PARSE, not a pattern read (ADR-0097 parts 2 and 3): no shipped pack's
    api-surface probe may declare `regex-over-source`, and a stdlib parse of this
    file was available, so this case closed by conversion rather than by joining
    the roster.

    Every `<anything>.add_parser("<literal>")` call in the module is a verb, and
    the first positional argument is the name argparse binds. A non-literal first
    argument names nothing static and is dropped, which is the resolve-or-drop
    posture every other reader in this engine takes.

    The pattern this replaced also required the verb to match `[a-z-]+`. That
    filter is gone: a real parse takes the literal argparse was handed, and
    re-imposing a character class would be the pattern read wearing the parse's
    name. Measured on this repository before the conversion, both readers answer
    with the same seven verbs.
    """
    try:
        tree = ast.parse(source)
    except _PARSE_FAILED:
        # The four classes `ast.parse` raises on repository-controlled source,
        # spelled once in `core` and bound here rather than copied. This clause
        # caught `SyntaxError` alone and that was a REGRESSION: a NUL byte is
        # only one of the four, and CPython's own parser raises `RecursionError`
        # or `MemoryError` while building the tree for source it ACCEPTED —
        # 200 KB of `1 + 1 + …` reaches the first and 100 KB of unary minus the
        # second, both far inside the 2 MB `_safe_read_bytes` bound. An escaping
        # one of those aborts the whole derive at exit 2, the code reserved for
        # an ENVIRONMENT failure, on the exact invocation
        # `.github/workflows/check-arch-drift.yml` runs — so one committed file
        # would take the CI drift gate down and report it as a broken runner.
        #
        # This site has no residual channel: its whole contract is to return the
        # verb set it could read, and an unreadable CLI contributes no verbs.
        # The empty answer is the honest stub here, as it is at
        # `packs/python.py::_setup_py_pkg_name`.
        return set()
    out: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # BOTH call shapes, because the pattern this replaced matched both:
        # `sub.add_parser("verb")` (an Attribute) and a bare `add_parser("verb")`
        # (a Name), which is what a `add_parser = sub.add_parser` binding or an
        # imported helper produces. Accepting only the Attribute would make the
        # reader return an EMPTY set on that refactor — a silent drift, since an
        # empty api-surface reads as "this CLI has no verbs" rather than as an
        # error, and the whole subject of the input-class rule is that a reader
        # must not fail quietly.
        if isinstance(func, ast.Attribute):
            named = func.attr
        elif isinstance(func, ast.Name):
            named = func.id
        else:
            continue
        if named != "add_parser":
            continue
        if not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            out.add(first.value)
    return out


def extract_api_surface(root: Path, docs_dir: str) -> tuple[str, dict]:
    """Interface surface = the skill inventory (name + one-line description) + the
    crux-env CLI verbs. This is crux's 'API' (a plugin, not an HTTP service).

    The inventory is PROJECTED from `crux/catalog/skills.json` when that artifact
    is present and its own drift check reports clean (ADR-0079 clauses 1-2), and
    derived from `crux/skills/*/SKILL.md` frontmatter when it is not (clause 3).
    Both paths render the same two fields — `name` and `description` — through
    the same truncation and escaping, so the projection changes which parser
    answers "which skills exist" and not what the answer is.
    """
    sources: dict = {}
    skills_dir = root / "crux" / "skills"
    skill_paths = sorted(skills_dir.glob("*/SKILL.md")) if skills_dir.exists() else []

    projected = _skills_catalog_projection(root)
    if projected is not None:
        entries, cat_rel, cat_hash = projected
        provenance = f"_Projected from `{_cell(cat_rel)}` and the `crux-env` CLI._"
        sources[cat_rel] = cat_hash
        # The SKILL.md files the staleness assertion read, hashed as PROVENANCE
        # and not as a second derivation. `_meta/manifest.json` is the ledger of
        # what the derive read (ADR-0078 clause 5), and clause 3 of that decision
        # checks the drift gate's trigger scope against a read set the derive
        # computes. Recording only the projected artifact would shrink that read
        # set below the set of files the derive actually opens, and a derive input
        # added inside the assertion's reach would then pass unnoticed — the one
        # outcome that clause forbids. This is a glob and a hash, never a parse,
        # so "which skills exist" still has exactly one answerer (clause 1).
        for sk in skill_paths:
            raw = _safe_read_bytes(root, sk, [])
            if raw is None:
                continue
            sources[_rel(root, sk)] = _sha256_hex(raw)
        pairs = [(str(e.get("name", "")), str(e.get("description", ""))) for e in entries]
    else:
        provenance = "_Derived from `crux/skills/*/SKILL.md` frontmatter and the `crux-env` CLI._"
        pairs = []
        for sk in skill_paths:
            # One gated read serves both the hash and the frontmatter parse, so
            # this branch acquires no second way into the file.
            raw = _safe_read_bytes(root, sk, [])
            if raw is None:
                continue
            fm = _frontmatter(raw.decode("utf-8", errors="replace"))
            sources[_rel(root, sk)] = _sha256_hex(raw)
            pairs.append((str(fm.get("name", sk.parent.name)),
                          str(fm.get("description", ""))))

    out = ["# API surface", "", provenance, ""]
    skill_rows = []
    for name, desc in pairs:
        desc = desc.strip()
        desc = (desc[:100] + "…") if len(desc) > 100 else desc
        # Both cells are repo-derived and land inside a pipe table, so both are
        # escaped. `name` was not before this refactor, which was a latent table
        # corruption: nothing validates a skill `name` against a character class
        # (`ALIAS_NAME_RE` governs model aliases), so one containing a pipe would
        # have split its own row.
        #
        # The two cells take DIFFERENT escapers, and the difference is not an
        # oversight. `name` is rendered inside a code span below, so it takes
        # `_cell`, which also neutralizes backtick and therefore cannot break out
        # of that span. `desc` is rendered as markdown, so it takes `_prose_cell`,
        # which leaves backtick alone — 29 of the 51 descriptions carry legitimate
        # inline code, and substituting it would corrupt real content and move the
        # spine. Both escape backslash before pipe, which closes a double-escape
        # hole the previous single `|` replacement left open: a value `a\|b` came
        # out as `a\\|b`, whose backslash escaped the backslash and left the pipe
        # bare, so the row split regardless.
        #
        # No skill name carries any of these characters today, so this moves no
        # byte of the rendered spine — it removes the way it could have moved.
        desc = _prose_cell(desc)
        name = _cell(name)
        skill_rows.append((name, desc))
    if not skill_rows:
        return _canon([out[0], "", NO_EXTRACTOR.rstrip("\n")]), sources

    out += [f"## Skills ({len(skill_rows)})", "", "| skill | description |", "|---|---|"]
    for name, desc in sorted(skill_rows):
        out.append(f"| `{name}` | {desc} |")
    out.append("")

    cli = root / "crux" / "scripts" / "crux-env.py"
    cli_raw = _safe_read_bytes(root, cli, []) if cli.exists() else None
    if cli_raw is not None:
        sources[_rel(root, cli)] = _sha256_hex(cli_raw)
        verbs = sorted(_cli_verbs(cli_raw.decode("utf-8", errors="replace")))
        if verbs:
            # Same escape and the same reason as the manifest keys above. The
            # pattern this reader replaced required `[a-z-]+`; the parse takes
            # whatever literal argparse was handed, so a verb carrying a newline
            # reached line-start position in api-surface.md and opened a heading
            # or an unclosed code fence. Re-imposing the character class in the
            # reader would be the pattern read wearing the parse's name, which is
            # why the fix is at the render.
            out += ["## `crux-env` CLI verbs", "",
                    ", ".join(f"`{_cell(v)}`" for v in verbs), ""]
    return _canon(out), sources


def extract_module_graph(root: Path, docs_dir: str) -> tuple[str, dict]:
    """Module graph = the intra-package import edges of the `crux` Python package
    under crux/scripts/crux/. Static analysis, no build step (Decision 5).

    Pinned to `crux/scripts/crux` + name `crux` so the crux spine stays
    byte-identical (ADR-0066 point 3); the parameterized engine is
    `_extract_module_graph`, shared with the Python pack's probe."""
    pkg = root / "crux" / "scripts" / "crux"
    if not pkg.exists():
        return _canon(["# Module graph", "", NO_EXTRACTOR.rstrip("\n")]), {}
    return _extract_module_graph(root, pkg, "crux")


def _detect_crux(root: Path) -> bool:
    return (root / "crux" / "skills").exists() and (root / "crux" / "schemas").exists()


#: Empty: the core's marker scan prunes no directory from this pack's own
#: `detect` calls, so this pack's marker semantics are unchanged.
DETECT_EXCLUDE: frozenset[str] = frozenset()


def detect(root: Path) -> DetectResult:
    """Does this repository look like the crux monorepo? (ADR-0096 clause 6.)

    The marker pair is `crux/skills/` and `crux/schemas/` — BOTH, because either
    alone is a directory name a hundred repositories could carry. `matched` is
    the predicate `_detect_crux` has always computed; `markers` names the two
    directories so an ambiguous multi-match can report the marker that fired
    rather than only the pack that fired.
    """
    hits = tuple(
        rel for rel in ("crux/schemas", "crux/skills") if (root / rel).exists()
    )
    return DetectResult(matched=len(hits) == 2, markers=hits)


# The crux dogfood pack's declared input class per concern (ADR-0097 part 1).
# `decision-index` is the universal concern the core declares and binds.
# `kind` lives on the probe below, not here — see `probes()`.
#
# Per ADR-0097 part 3, the two pattern readers this pack once ran over
# `<docs_dir>/manifest.yml` and `crux-env.py` converted to real parses —
# `yaml.safe_load` and an `ast` walk over the CLI's subparsers — so both
# `data-model` and `api-surface` declare `parser` at the probe: the JSON
# Schemas and `crux/catalog/skills.json` are read through their own fixed
# grammars, and the two prose targets are now genuinely parsed rather than
# pattern-matched. `module-graph` stays `regex-over-source`: it delegates to
# `core._extract_module_graph`, the pattern-matching engine the python pack's
# module-graph probe also binds, and ADR-0097's roster names both.
#
# **Both YAML-reading concerns DECLARE `yaml`, and the declaration is what arms
# the refusal.** `core._frontmatter` degrades to a line regex when PyYAML is
# missing, and the degraded reader ANSWERS DIFFERENTLY: the same source tree with
# and without PyYAML produced two different `spine_hash` values, both at exit 0,
# with identical `tool_pins`. The `ParserUnavailable` raise in the manifest
# reader below is not enough on its own — it sits inside `if man_raw is not
# None:`, so it fires only on a tree that HAS a `<docs_dir>/manifest.yml`, and a
# `crux/`-only tree with no docs tree (the shape `sync.sh` publishes) degraded in
# silence. `resolve_declared_parsers` runs once, before any extractor, so the
# declaration refuses the whole derive at exit 2 with nothing written — which is
# the environment lane ADR-0060 Decision 4 and ADR-0096 clause 2 route this to,
# and the rule ADR-0097 publishes for the ruby, node and elixir packs.
#
# The declaration also puts PyYAML's resolved version into the byte-compared
# `tool_pins`, which is `parser_pins`'s whole point: a parser that decides the
# spine hash moves the tree deliberately rather than silently. That closes the
# residual this module used to state. It also made an exact pin mandatory rather
# than optional — a floor would let an upstream release move
# `_meta/manifest.json` on an unmodified tree — so PyYAML is pinned `==` at every
# declaring and consuming site, held there by `ParserPinLockStepTests`.
#
# This pack's declaration is NOT what makes every pack refuse without PyYAML.
# The universal `decision-index` concern declares `yaml` in `core`, because it
# reads ADR frontmatter through the same `_frontmatter` for every pack. These two
# entries cover what this pack reads on top of that.
INPUT_CLASSES = {
    "data-model": InputClass(
        expected="JSON Schemas under `crux/schemas/`, plus `<docs_dir>/manifest.yml` and the SKILL.md metadata contract",
        globs=("crux/schemas/*.json", "*/manifest.yml", "crux/skills/*/SKILL.md"),
        parser=("yaml",),
    ),
    "api-surface": InputClass(
        expected="the regenerated `crux/catalog/skills.json` and the `crux-env` CLI",
        globs=("crux/catalog/skills.json", "crux/scripts/crux-env.py",
               "crux/skills/*/SKILL.md"),
        parser=("yaml",),
    ),
    "module-graph": InputClass(
        expected="intra-package imports of the `crux` package under `crux/scripts/crux/`",
        globs=("crux/scripts/crux/*.py", "crux/scripts/crux/**/*.py"),
    ),
}


def probes() -> dict[str, list[Probe]]:
    """The crux pack's per-concern probe registry (ADR-0066 points 2/3).

    One probe per concern, each gated on the same whole-pack detector — this pack
    is all-or-nothing, unlike ruby/node/elixir whose ordered probe lists fall
    through input by input. `decision-index` is absent by design: it is the
    universal probe and the core binds it.

    Each probe declares its own `kind` (ADR-0097 part 1): `data-model` and
    `api-surface` are real parses (`yaml.safe_load`, `ast`), `module-graph`
    matches a pattern against authored source and is on the ADR-0097 roster.
    """
    return {
        "data-model": [Probe(_detect_crux, extract_data_model, kind="parser")],
        "api-surface": [Probe(_detect_crux, extract_api_surface, kind="parser")],
        "module-graph": [Probe(_detect_crux, extract_module_graph, kind="regex-over-source")],
    }
