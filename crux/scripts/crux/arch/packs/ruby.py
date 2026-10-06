"""The ruby stack pack (ADR-0067), split out of `derive.py` under ADR-0096 clause 12.

What it extracts, per spine concern:
  * `data-model`   — `db/schema.rb` (ActiveRecord's committed schema dump) into
                     the six-column entity table + indexes + FK residuals. One
                     rung: a repository carrying only `db/structure.sql` takes
                     an honest `precondition_missing` (U-R3).
  * `api-surface`  — a three-rung chain into one `(method, path, controller)`
                     row shape: a committed OpenAPI/Swagger document (rendered
                     by the shared `_render_openapi`), else the committed
                     `bin/rails routes --expanded` dump at
                     `arch-inputs/routes.txt`, else a `config/routes.rb` parse.
  * `module-graph` — a Zeitwerk constant index over the autoload roots with
                     resolve-or-drop edges (in-repo constants only).

Every extractor is static and deterministic: no Ruby is executed, every file
read is containment-checked and size-bounded, and every file read is hashed into
`sources` so a change drifts the spine. `decision-index` is NOT bound here — it
is the universal probe the core binds for every pack.

Every parser this pack uses is named by the consuming concern's input class and
pinned in the deriver's PEP 723 block (ADR-0097 part 5). `api-surface` declares
`parser=("tree_sitter", "tree_sitter_ruby")` and its `config/routes.rb` rung is
a tree-sitter parse (`_ruby_ts_parser`); `data-model` and `module-graph` declare
none and need nothing beyond the stdlib.

Naming the parser on the declaration is what gives a missing grammar a lane of
its own. `core.resolve_declared_parsers` runs BEFORE any extraction and raises
`ParserUnavailable` when a declared grammar is not importable, which
`derive-arch.py` reports as exit 2 — the no-verdict lane, not a drift finding.
So on a machine without the `tree-sitter` and `tree-sitter-ruby` wheels this
pack does not degrade to a stub; it refuses.

HISTORY — true of the U7 move, not of the tree as it stands. The extractor
region below moved here VERBATIM from `derive.py` (the block between the ruby
and node pack banners) under ADR-0096 clause 12: nothing was reformatted,
renamed or tidied in transit, the cross-boundary references were rewritten to a
single module-scope `from ..core import` as the only edit, and the gate was
byte-identity across every corpus golden. That claim is scoped to that move and
is still true OF IT.

Later units then changed the tree on top of the move, so the byte-identity
statement no longer describes this file as a whole:
  * U-R1b replaced the `config/routes.rb` line scan with the tree-sitter routes
    reader — new code, not moved code — and brought `member`/`collection`
    blocks in with it.
  * U-R3 removed the `db/structure.sql` probe rather than leave a reader that
    pretended to be one.
Read the byte-identity sentence as a dated record of the split, and this list as
what has landed since. `detect` and `probes` at the module foot are the two
additions clause 12 asks each pack to expose.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import NamedTuple

from ..core import (
    DetectResult,
    InputClass,
    NO_EXTRACTOR,
    Probe,
    _MAX_FILE_BYTES,
    _MAX_GRAPH_EDGES,
    _OPENAPI_CANDIDATES,
    _SKIP_DIRS,
    _canon,
    _cell,
    _contained,
    _mermaid,
    _node_ids,
    _norm_path,
    _rel,
    _render_openapi,
    _safe_read_bytes,
    _sha256_hex,
    _singularize,
)

# ─────────────────────── ruby stack pack (ADR-0067) ─────────────────────────
# Static, deterministic extractors. No Ruby is executed. Every file read is
# containment-checked and size-bounded (point 8) and hashed into `sources`, so a
# changed openapi.json / routes.rb / schema.rb / .rb drifts the spine. This repo
# pins `arch_stack: crux`, so these never run here (point 7).
#
# `api-surface` declares the tree-sitter Ruby grammar, and a machine missing it
# takes the exit-2 no-verdict lane rather than a stub. The module docstring
# above carries the full statement.


_ROUTES_UNDER_COUNT = (
    "Under-counted, and the residual is now a SHORT list because the reader is a "
    "tree-sitter parse rather than a line scan: `root`, `mount`ed engines, route "
    "`concern`s, `shallow:` nesting, `direct`/`resolve` helpers and "
    "metaprogrammed `draw`s are declarations this rung does not expand. Every "
    "one of them is already expanded in `arch-inputs/routes.txt` — the committed "
    "output of `bin/rails routes --expanded`, which is the rung above this one "
    "and the input that closes them."
)


# ── lexical scrubbing + constant collection (points 3/8) ─────────────────────
# All patterns are linear-time and backtracking-safe (no nested unbounded
# quantifiers over overlapping character classes), so a pathological committed
# file cannot cause catastrophic backtracking.

_RB_DQ = re.compile(r'"(?:[^"\\]|\\.)*"')          # double-quoted string (safe).
_RB_SQ = re.compile(r"'(?:[^'\\]|\\.)*'")          # single-quoted string (safe).
_RB_LINE_COMMENT = re.compile(r"#[^\n]*")
_RB_CONST = re.compile(
    r"(?<![A-Za-z0-9_:])(?:::)?([A-Z][A-Za-z0-9_]*(?:::[A-Z][A-Za-z0-9_]*)*)"
)
_RB_REQUIRE_REL = re.compile(r'require_relative\s+["\']([^"\']+)["\']')
_RB_REQUIRE = re.compile(r'require\s+["\']([^"\']+)["\']')


def _scrub_ruby_comments(text: str) -> str:
    """Drop `=begin/=end` blocks and full-line `#` comments (linear line scan).
    Quoted strings are preserved (autoload/route/schema parsing needs them)."""
    kept = []
    in_block = False
    for ln in text.split("\n"):
        s = ln.lstrip()
        if in_block:
            if s.startswith("=end"):
                in_block = False
            continue
        if s.startswith("=begin"):
            in_block = True
            continue
        if s.startswith("#"):
            continue
        kept.append(ln)
    return "\n".join(kept)


def _scrub_ruby(text: str) -> str:
    """Scrub `#` comments, `=begin/=end`, and quoted strings before collecting
    CamelCase constants (point 3). Strings are removed after comment-blocks so a
    `#` inside a string cannot be misread as a comment."""
    text = _scrub_ruby_comments(text)
    text = _RB_DQ.sub(" ", text)
    text = _RB_SQ.sub(" ", text)
    text = _RB_LINE_COMMENT.sub("", text)
    return text


def _ruby_constants(scrubbed: str) -> set:
    return {m.group(1) for m in _RB_CONST.finditer(scrubbed)}


def _camelize(segment: str) -> str:
    """Default Zeitwerk inflection of one path segment: `users_controller` →
    `UsersController` (each underscore-separated word capitalized, no acronym
    table)."""
    return "".join(w[:1].upper() + w[1:] for w in segment.split("_") if w)


def _rb_constant(root_dir: Path, path: Path) -> str:
    """The Zeitwerk constant path for `path` under autoload root `root_dir`:
    `app/models/foo/bar.rb` (root `app/models`) → `Foo::Bar`."""
    rel = path.relative_to(root_dir).with_suffix("")
    return "::".join(_camelize(s) for s in rel.parts)


def _walk_rb_dir(root: Path, base: Path):
    """Yield contained `.rb` files under `base` (skips VCS/vendor + dot dirs and
    file symlinks that escape the root). `os.walk` does not follow directory
    symlinks (followlinks defaults to False)."""
    if not base.is_dir():
        return
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for fn in sorted(filenames):
            if fn.endswith(".rb"):
                p = Path(dirpath) / fn
                if _contained(root, p):
                    yield p


# ── autoload roots + source discovery (points 3/4) ───────────────────────────

_AUTOLOAD_ASSIGN = re.compile(
    r"config\.(?:autoload_paths|eager_load_paths)\s*(?:<<|\+=|=)\s*([^\n#]*)"
)
_RB_PATH_STRING = re.compile(r"""["']([^"'\n]{1,300})["']""")


class RubySources(NamedTuple):
    rails_root: Path | None          # dir carrying config/application.rb or bin/rails+app/, else lib root.
    roots: list                      # ordered autoload root dirs (precedence order).
    entries: list                    # (root_index:int, root_dir:Path, path:Path), contained + size-ok.
    residuals: list                  # note strings (e.g. non-literal autoload assignment).
    oversize: list                   # repo-relative paths skipped for exceeding the size bound.


def _find_rails_root(root: Path) -> Path | None:
    """The Rails root: the dir carrying `config/application.rb`, or `bin/rails`
    plus an `app/` (point 4). v1 checks the repo root only."""
    if (root / "config" / "application.rb").is_file():
        return root
    if (root / "bin" / "rails").is_file() and (root / "app").is_dir():
        return root
    return None


def _autoload_roots(root: Path, rails_root: Path) -> tuple:
    """Autoload roots: the conventional `app/*` + `lib`, plus any `autoload_paths`
    /`eager_load_paths` additions in `config/application.rb` that parse as string
    literals (point 3). A non-literal/expression assignment EMITS a residual note
    rather than silently defaulting, so an under-covered root stays legible."""
    residuals: list = []
    literal_dirs: list = []
    nonliteral = False
    oversize: list = []
    # `autoload_paths`/`eager_load_paths` can be set in config/application.rb OR
    # in a per-environment file (config/environments/*.rb) — point 3 requires the
    # environment-file sibling to be honored, not silently defaulted past.
    cfg_files = [rails_root / "config" / "application.rb"]
    env_dir = rails_root / "config" / "environments"
    if env_dir.is_dir():
        cfg_files += sorted(p for p in env_dir.glob("*.rb"))
    for cfg in cfg_files:
        if not cfg.is_file():
            continue
        raw = _safe_read_bytes(root, cfg, oversize)
        if raw is None:
            continue
        text = _scrub_ruby_comments(raw.decode("utf-8", errors="replace"))
        for m in _AUTOLOAD_ASSIGN.finditer(text):
            rhs = m.group(1).strip()
            strings = _RB_PATH_STRING.findall(rhs)
            residue = re.sub(r"[\[\]{}(),\s]", "", _RB_PATH_STRING.sub("", rhs))
            if strings and not residue:
                for s in strings:
                    cand = rails_root / s
                    if _contained(root, cand) and cand.is_dir():
                        literal_dirs.append(cand)
            else:
                nonliteral = True
    if oversize:
        residuals.append(
            "A Rails config file exceeded the 2 MB bound and was not scanned for "
            "autoload roots: " + ", ".join(f"`{_cell(_rel(root, p))}`" for p in sorted(oversize)))
    if nonliteral:
        residuals.append(
            "A `config.autoload_paths`/`eager_load_paths` assignment is not a "
            "string literal — some autoload roots may be uncovered (a named "
            "residual); the conventional `app/*` + `lib` set is used."
        )
    app = rails_root / "app"
    roots: list = sorted(d for d in app.iterdir() if d.is_dir()) if app.is_dir() else []
    lib = rails_root / "lib"
    if lib.is_dir():
        roots.append(lib)
    roots += literal_dirs
    seen: set = set()
    uniq: list = []
    for r in roots:
        if not _contained(root, r):
            continue
        rp = r.resolve()
        if rp not in seen:
            seen.add(rp)
            uniq.append(r)
    return uniq, residuals


def detect_ruby_sources(root: Path) -> RubySources:
    """Autoload roots + the scanned `.rb` set for a Ruby repo (point 4). A Rails
    root is the dir carrying `config/application.rb` or `bin/rails` + `app/`; a
    plain gem falls back to `lib/`. Files are contained + size-filtered."""
    root = Path(root)
    rails_root = _find_rails_root(root)
    residuals: list = []
    oversize: list = []
    if rails_root is not None:
        roots, residuals = _autoload_roots(root, rails_root)
    else:
        lib = root / "lib"
        if lib.is_dir():
            rails_root, roots = root, [lib]
        else:
            roots = []
    entries: list = []
    seen: set = set()
    for idx, root_dir in enumerate(roots):
        for p in _walk_rb_dir(root, root_dir):
            try:
                size = p.stat().st_size
            except OSError:
                continue
            if size > _MAX_FILE_BYTES:
                rel = _rel(root, p)
                if rel not in oversize:
                    oversize.append(rel)
                continue
            rp = p.resolve()
            if rp in seen:
                continue
            seen.add(rp)
            entries.append((idx, root_dir, p))
    return RubySources(rails_root, roots, entries, residuals, oversize)


# ── module-graph: Zeitwerk constant index + resolve-or-drop (point 3) ─────────

def _detect_ruby_module_graph(root: Path) -> bool:
    return bool(detect_ruby_sources(Path(root)).entries)


def extract_ruby_module_graph(root: Path, docs_dir: str) -> tuple[str, dict]:
    """Structural module graph over the Zeitwerk autoload roots (point 3). Nodes
    are the in-repo `.rb` constants; an edge `A → B` is emitted only when a
    CamelCase reference in A resolves to an in-repo constant B (RESOLVE-OR-DROP),
    so gem/stdlib names drop. Constant collisions resolve to the first file by
    (root precedence, POSIX path); the shadowed file is recorded. Every scanned
    `.rb` is hashed, including edge-less isolated files."""
    root = Path(root)
    src = detect_ruby_sources(root)
    if not src.entries:
        return _canon(["# Module graph", "", NO_EXTRACTOR.rstrip("\n")]), {}

    sources: dict = {}
    index: dict = {}          # constant -> (root_index, path)
    shadowed: list = []       # (constant, repo-rel path)
    reverse: dict = {}        # resolved file path -> constant
    file_texts: list = []     # (constant, path, raw_text)
    ordered = sorted(src.entries, key=lambda e: (e[0], _rel(root, e[2])))
    for root_index, root_dir, path in ordered:
        raw = _safe_read_bytes(root, path, [])
        if raw is None:
            continue
        sources[_rel(root, path)] = _sha256_hex(raw)      # hash every scanned .rb.
        const = _rb_constant(root_dir, path)
        if not const:
            continue
        if const in index:
            shadowed.append((const, _rel(root, path)))
            continue
        index[const] = (root_index, path)
        reverse[path.resolve()] = const
        file_texts.append((const, path, raw.decode("utf-8", errors="replace")))

    edges: set = set()
    for const, path, text in file_texts:
        commented = _scrub_ruby_comments(text)            # comments dropped, strings kept.
        for ref in _ruby_constants(_scrub_ruby(text)):    # full scrub for the constant pass.
            if ref in index and ref != const:
                edges.add((const, ref))
        for m in _RB_REQUIRE_REL.finditer(commented):     # require_relative → file.
            b = reverse.get((path.parent / (m.group(1) + ".rb")).resolve())
            if b and b != const:
                edges.add((const, b))
        for m in _RB_REQUIRE.finditer(commented):         # require → load-path file.
            for r_dir in src.roots:
                b = reverse.get((r_dir / (m.group(1) + ".rb")).resolve())
                if b and b != const:
                    edges.add((const, b))
                    break

    # Bound the RENDERED graph so a hostile repo (N files each referencing all N
    # in-repo constants → ~N² edges) cannot blow the spine to multi-GB. Truncate
    # to a deterministic sorted prefix and record a residual (point 8).
    graph_residuals: list = []
    all_edges = sorted(edges)
    if len(all_edges) > _MAX_GRAPH_EDGES:
        graph_residuals.append(
            f"module graph truncated: {len(all_edges)} edges exceed the "
            f"{_MAX_GRAPH_EDGES}-edge render bound")
        all_edges = all_edges[:_MAX_GRAPH_EDGES]
    edges = set(all_edges)

    node_ids = _node_ids(list(index))
    out = ["# Module graph", "",
           f"_Structural module graph of {len(index)} constants, {len(edges)} "
           f"edges (Zeitwerk resolve-or-drop; static parse, no Ruby executed)._",
           "", "```mermaid", "graph LR"]
    for a, b in sorted(edges):
        out.append(f'  {node_ids[a]}["{_mermaid(a)}"] --> {node_ids[b]}["{_mermaid(b)}"]')
    out += ["```", ""]
    if not edges:
        out += ["_No resolved constant edges detected._", ""]
    connected = {a for a, _ in edges} | {b for _, b in edges}
    isolated = sorted(c for c in index if c not in connected)
    if isolated:
        out += [f"## Isolated modules ({len(isolated)})", "",
                "_No resolved constant edge (leaf or standalone):_", "",
                ", ".join(f"`{_cell(c)}`" for c in isolated), ""]
    if shadowed:
        out += [f"## Shadowed constants ({len(shadowed)})", "",
                "_A later file camelizes to an already-claimed constant; the "
                "first by (root precedence, POSIX path) wins (a named residual):_",
                ""]
        for const, relp in sorted(shadowed):
            out.append(f"- `{_cell(const)}` — shadowed file `{_cell(relp)}`")
        out.append("")
    notes = list(src.residuals) + graph_residuals
    if src.oversize:
        notes.append("Skipped for exceeding the 2 MB bound: "
                     + ", ".join(f"`{_cell(p)}`" for p in sorted(src.oversize)))
    if notes:
        out += ["## Residuals", ""] + [f"- {n}" for n in notes] + [""]
    return _canon(out), sources


# ── api-surface: committed OpenAPI first, then a static routes.rb parse ───────


def _ruby_openapi_path(root: Path) -> Path | None:
    for c in _OPENAPI_CANDIDATES:
        p = root / c
        if p.is_file() and _contained(root, p):
            return p
    return None


def _detect_ruby_openapi(root: Path) -> bool:
    return _ruby_openapi_path(Path(root)) is not None


def extract_ruby_api_surface_openapi(root: Path, docs_dir: str) -> tuple[str, dict]:
    """API surface from a committed OpenAPI/Swagger document (point 1), rendered
    through the shared `_render_openapi` (point 6). Stub when absent/unparseable."""
    root = Path(root)
    spec = _ruby_openapi_path(root)
    if spec is None:
        return _canon(["# API surface", "", NO_EXTRACTOR.rstrip("\n")]), {}
    raw = _safe_read_bytes(root, spec, [])
    if raw is None:
        return _canon(["# API surface", "", NO_EXTRACTOR.rstrip("\n")]), {}
    sources = {_rel(root, spec): _sha256_hex(raw)}
    try:
        doc = json.loads(raw.decode("utf-8"))
    except Exception:
        return _canon(["# API surface", "", NO_EXTRACTOR.rstrip("\n")]), sources
    md = _render_openapi(doc, f"_Derived from `{_cell(_rel(root, spec))}`._")
    if md is None:
        return _canon(["# API surface", "", NO_EXTRACTOR.rstrip("\n")]), sources
    return md, sources


# ── api-surface, rung 2: the committed `rails routes --expanded` dump ────────
#
# WHY A PATTERN READ IS ADMISSIBLE HERE, on a concern that declares `parser`.
# ADR-0096 clause 1 prohibits matching a regular expression against AUTHORED
# SOURCE text, and states the test in one sentence: "Admissibility follows the
# input's provenance, not the file's language." `arch-inputs/routes.txt` is the
# stdout of `bin/rails routes --expanded` — Rails' own `ExpandedFormatter`
# writing its own routing table. Its grammar is the emitter's rather than a
# human's, so a pattern over it has a BOUNDED failure class: it breaks when
# Rails changes its formatter, which is a version fact, and not when a developer
# reformats a line. Clause 1 lists "a route dump" among its own examples of a
# `committed-artifact`. The same argument carries `db/schema.rb` below (U-R2),
# and it is the argument `config/routes.rb` cannot make.
#
# The concern's declared `kind` stays `parser`, because the CHAIN ends at a
# hand-authored `config/routes.rb` and the declaration names the class of input
# the concern consumes at its weakest rung. `artifact` is the field that draws
# clause 7's line between the emitted dump and the parsed source beside it.

#: The record separator Rails writes between entries: `--[ Route 7 ]------…`.
_RAILS_DUMP_RECORD = re.compile(r"^--\[ Route \d+ \]-*\s*$")
#: One `Field | value` line. The four fields Rails emits are `Prefix`, `Verb`,
#: `URI Pattern` and `Controller#Action`; an unrecognized key is kept and
#: ignored rather than treated as a parse failure, so a future Rails that adds a
#: field degrades to "we did not read that one" instead of to zero routes.
_RAILS_DUMP_FIELD = re.compile(r"^([A-Za-z][A-Za-z0-9 #_-]*?)\s*\|\s?(.*)$")
#: Rails appends the optional format segment to every pattern it prints.
_RAILS_DUMP_FORMAT = re.compile(r"\(\.:format\)$")

#: The method cell for a record whose `Verb` column is empty. Rails leaves it
#: empty for a mounted Rack application, which answers every verb; rendering the
#: empty string would put a blank cell in the table and claim nothing.
_RAILS_DUMP_ANY_VERB = "ANY"

_RAILS_DUMP_ANY_RESIDUAL = (
    "A mounted application's record in the dump records no verb (Rails leaves "
    "the `Verb` column empty for a Rack mount, which answers all of them); those "
    "rows render `ANY` (a named residual)."
)

_ROUTES_ARTIFACT_REL = "arch-inputs/routes.txt"
_ROUTES_ARTIFACT_REFRESH = "bin/rails routes --expanded > arch-inputs/routes.txt"


def _parse_rails_routes_dump(text: str) -> tuple:
    """Read `bin/rails routes --expanded` output into `(rows, parsed)`.

    `rows` is the same `(method, path, controller)` triple the other two rungs
    of this chain produce, so the renderer is shared and the concern's row shape
    does not depend on which rung answered.

    `parsed` is False when the text carries NO record separator at all — the
    condition ADR-0096 clause 3 names `parse_failed`. The caller acts on it
    rather than rendering an empty table: see `_detect_ruby_routes_artifact`.
    """
    records: list = []
    cur: dict | None = None
    for line in text.split("\n"):
        if _RAILS_DUMP_RECORD.match(line.rstrip()):
            if cur is not None:
                records.append(cur)
            cur = {}
            continue
        if cur is None:
            continue
        m = _RAILS_DUMP_FIELD.match(line)
        if m:
            cur[m.group(1).strip()] = m.group(2).strip()
    if cur is not None:
        records.append(cur)
    if not records:
        return [], False
    rows: list = []
    for rec in records:
        pattern = rec.get("URI Pattern")
        if not pattern:
            continue
        path = _norm_path(_RAILS_DUMP_FORMAT.sub("", pattern).strip())
        controller = rec.get("Controller#Action", "").strip()
        if not controller:
            continue
        # NOT a closed set. `rec["Verb"]` is whatever the committed
        # `bin/rails routes --expanded` dump carries, so this is target-repo
        # content and `_RAILS_DUMP_ANY_VERB` is the fallback for an EMPTY field
        # rather than a filter.
        #
        # **No row break is reachable today, and the escape is not defending
        # against one.** `split("|")` consumes pipes and the per-line field match
        # stops a newline. An earlier draft of this comment claimed a trailing
        # backslash escapes the next cell delimiter and a backtick swallows the
        # cell beside it; a reviewer rendered both through GFM and neither holds.
        # The template puts a SPACE between the value and the delimiter, so a
        # trailing backslash escapes that space, and `core.py`'s decision-index
        # comment already states the general rule — a table row is split on
        # unescaped `|` BEFORE inline parsing, so an unbalanced backtick cannot
        # reach past its own cell.
        #
        # The render escapes it anyway, and the reason is the cell's provenance
        # rather than a live exploit: this value is target-controlled, and the
        # next renderer that drops the space before the delimiter, or wraps the
        # cell in a code span the way the two cells beside it already are, gets
        # the guarantee for free instead of needing this analysis redone.
        verbs = [v.strip().upper() for v in rec.get("Verb", "").split("|") if v.strip()]
        for verb in (verbs or [_RAILS_DUMP_ANY_VERB]):
            rows.append((verb, path, controller))
    return rows, True


def _ruby_routes_artifact_path(root: Path) -> Path | None:
    p = Path(root) / "arch-inputs" / "routes.txt"
    return p if p.is_file() and _contained(Path(root), p) else None


def _detect_ruby_routes_artifact(root: Path) -> bool:
    """The dump is present AND matches its emitter's grammar.

    The grammar check belongs in the DETECTOR, not in the extractor, and that
    placement is the honest answer to a condition the recorded channel cannot
    yet express. `core.concern_verdict` computes four branches and says so in
    its own docstring: `parse_failed` "belongs to clause 1's input decoding,
    which is not landed". An extractor that degraded to the stub here would
    therefore record `precondition_missing` — "the declared input is absent" —
    of a file sitting on disk. That is the same false claim U-R3 removes from
    the data-model chain, and there is no reason to add one here while removing
    one there. Failing the detector instead makes no claim at all: the chain
    continues to the `config/routes.rb` reader, which is a better answer than
    either stub.

    The RESIDUAL of that placement, named here because it is real: "no claim at
    all" holds only while a LOWER rung answers. When `arch-inputs/routes.txt` is
    present but unreadable AND `config/routes.rb` is absent, every rung fails
    and `core.concern_verdict` records `precondition_missing` with `found="no
    matching input"` — a false claim about a file sitting on disk, which is the
    `parse_failed` branch clause 1 has not landed. `RoutesArtifactUnreadableResidualTests`
    pins that behaviour, so the day `parse_failed` becomes reachable, a failing
    assertion is already waiting.
    """
    root = Path(root)
    p = _ruby_routes_artifact_path(root)
    if p is None:
        return False
    raw = _safe_read_bytes(root, p, [])
    if raw is None:
        return False
    _rows, parsed = _parse_rails_routes_dump(raw.decode("utf-8", errors="replace"))
    return parsed


def extract_ruby_api_surface_routes_artifact(root: Path, docs_dir: str) -> tuple[str, dict]:
    """API surface from the committed `bin/rails routes --expanded` dump.

    The only rung of this chain that sees the routes Rails ITSELF resolved:
    engine mounts, route `concern`s, `direct`/`resolve` helpers and
    metaprogrammed draws are all already expanded in the dump, because Rails
    expanded them. Those are precisely the residuals the `config/routes.rb`
    reader names, which is why this rung sits above it.
    """
    root = Path(root)
    dump = _ruby_routes_artifact_path(root)
    if dump is None:
        return _canon(["# API surface", "", NO_EXTRACTOR.rstrip("\n")]), {}
    oversize: list = []
    raw = _safe_read_bytes(root, dump, oversize)
    if raw is None:
        return _canon(["# API surface", "", NO_EXTRACTOR.rstrip("\n")]), {}
    sources = {_rel(root, dump): _sha256_hex(raw)}
    rows, parsed = _parse_rails_routes_dump(raw.decode("utf-8", errors="replace"))
    if not parsed or not rows:
        return _canon(["# API surface", "", NO_EXTRACTOR.rstrip("\n")]), sources
    out = ["# API surface", "",
           f"_Derived from `{_cell(_rel(root, dump))}` — the committed output of "
           "`bin/rails routes --expanded`, Rails' own routing table._", "",
           f"## Routes ({len(rows)})", "",
           "| method | path | controller |", "|---|---|---|"]
    for method, path, controller in sorted(rows, key=lambda r: (r[1], r[0], r[2])):
        out.append(f"| {_cell(method)} | `{_cell(path)}` | `{_cell(controller)}` |")
    out.append("")
    if any(r[0] == _RAILS_DUMP_ANY_VERB for r in rows):
        out += ["## Residuals", "", f"- {_RAILS_DUMP_ANY_RESIDUAL}", ""]
    return _canon(out), sources


# ── api-surface, rung 3: `config/routes.rb` through tree-sitter (U-R1b) ──────
#
# The concern declares the input class `parser`, and this is the parser.
# ADR-0096 clause 1 prohibits matching a regular expression against authored
# source for such a concern, and `config/routes.rb` is authored source — the one
# rung of this chain whose provenance is a human's rather than an emitter's.
# Eight patterns were that reader, and every one of the defects below is a
# structural fact of the parse tree that no pattern over the text could see.
#
# What the tree gives that the line scan could not:
#
#   * FRAMES COME FROM BLOCKS. The line scan counted `do` and `end` tokens, so an
#     `if … end` decremented a depth it had never incremented and popped the
#     enclosing `namespace` frame early. A `do_block` is a node; its extent is
#     the frame's extent, and nothing needs counting.
#   * `member`/`collection` become path frames. The line scan opened a depth for
#     a `resources` block and named the routes inside it a residual, because it
#     had no way to know which resource they belonged to.
#   * NESTED `resources` INHERIT THE PARENT SEGMENT. `resources :dependencies`
#     inside `resources :versions do` rendered `/dependencies`, losing the parent
#     entirely; the frame now carries `versions/:version_id`.
#   * One node shape for every verb spelling: `get "/x", to: "c#a"`, the
#     `=> "c#a"` hash-rocket form, the same call broken across four lines, and
#     the bare-symbol member form `get :subscribe` are all one `call` with one
#     argument list.
#   * `only:`/`except:` are read as keyword-argument NODES, so `only: %i[a b]`
#     is seen. The pattern required `[…]` or a bare `:sym` and silently matched
#     nothing against `%i[]`, which meant NO filter and every REST action
#     rendered for a resource that declares three.

#: The five HTTP verb macros this reader renders — the same closed set the
#: retired pattern carried, read off the call's own identifier so `get` the route
#: macro and `get` a Hash lookup are told apart by node type, not by a line
#: anchor. `match … via:` is deliberately not here; it is a named residual.
_RUBY_ROUTE_VERBS = frozenset(("get", "post", "put", "patch", "delete"))

#: A path segment this reader will infer an action name from. Rails infers the
#: controller from the enclosing resource and the action from the segment, but
#: only a bare identifier is an action name — `:webauthn_token/status` is not.
_RUBY_ACTION_SEGMENT = re.compile(r"^[A-Za-z_]\w*$")

_TS_PARSER = None


def _ruby_ts_parser():
    """The tree-sitter Ruby parser, built once per process.

    The import is inside the function, deliberately, for the reason the elixir
    and node packs give: the core imports every pack module to read its probe
    registry, so a module-scope `import tree_sitter_ruby` would make a python,
    node, elixir, crux or swift derive fail on a machine carrying no Ruby
    grammar — five packs that declare no Ruby parser, consult none, and record none in the
    provenance manifest. `core.resolve_declared_parsers` has already refused,
    before any extraction, if the grammar is absent for a pack that DOES declare
    it, so reaching here means the import resolves.
    """
    global _TS_PARSER
    if _TS_PARSER is None:
        import tree_sitter                                # noqa: PLC0415
        import tree_sitter_ruby                           # noqa: PLC0415
        _TS_PARSER = tree_sitter.Parser(
            tree_sitter.Language(tree_sitter_ruby.language()))
    return _TS_PARSER


def _ruby_ts_parse(raw: bytes):
    """Parse Ruby source BYTES into a tree-sitter tree.

    Bytes, not text: tree-sitter node offsets are byte offsets, and decoding
    first would make every slice below wrong on any file carrying a multi-byte
    character. The caller supplies the bytes `core._safe_read_bytes` returned.
    """
    return _ruby_ts_parser().parse(raw)


def _ts_text(node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _rb_call_name(node, src: bytes):
    """The bare identifier a receiver-less `call` invokes, or None.

    None is every call this reader does not act on. A call WITH a receiver is
    excluded by the same test — `Rails.application.routes.draw` and
    `Sidekiq::Web.foo` are not routing macros — and the router DSL is
    receiver-less bare identifiers only, so None always means "not a route
    declaration".
    """
    if node.type != "call" or node.child_by_field_name("receiver") is not None:
        return None
    method = node.child_by_field_name("method")
    if method is None or method.type != "identifier":
        return None
    return _ts_text(method, src)


def _rb_args(node) -> list:
    """A `call`'s argument nodes, punctuation dropped.

    This is the parenthesized-versus-space-delimited fix in one line: the
    `argument_list` carries `(`, `)` and `,` as ANONYMOUS children when the call
    is written parenthesized and omits them when it is not, so filtering on
    `is_named` yields the same list for both spellings — and for the same call
    broken across four lines, which the line scan saw as four unmatched lines.
    """
    args = node.child_by_field_name("arguments")
    return [c for c in args.children if c.is_named] if args is not None else []


def _rb_block(node):
    """A `call`'s `do … end` (or brace) block, or None."""
    return node.child_by_field_name("block")


def _rb_string(node, src: bytes):
    """A plain string literal's contents, or None when the node is not one.

    None for an INTERPOLATED string. `"/#{prefix}/x"` has no static value —
    computing it means executing the DSL — so the reader names it a residual
    rather than rendering the interpolation's source text as if it were a path.
    """
    if node is None or node.type != "string":
        return None
    if any(c.type == "interpolation" for c in node.children):
        return None
    return "".join(_ts_text(c, src) for c in node.children
                   if c.type == "string_content")


def _rb_symbol(node, src: bytes):
    """A symbol's name without its punctuation: `:index` and the `index:` of a
    keyword pair and the `index` of a `%i[]` element all read as `index`."""
    if node is None:
        return None
    if node.type == "simple_symbol":
        return _ts_text(node, src).lstrip(":")
    if node.type == "hash_key_symbol":
        return _ts_text(node, src)
    if node.type == "bare_symbol":
        return _ts_text(node, src)
    return None


def _rb_keywords(args: list, src: bytes) -> dict:
    """`{keyword: value-node}` over the `pair` nodes in an argument list.

    The AST route to the options a declaration carries — `only:`, `except:`,
    `on:`, `to:`, `module:`. Both spellings of a Ruby hash key reach the same
    key: `to: "x"` (a `hash_key_symbol`) and `:to => "x"` (a `simple_symbol`).
    A pair whose key is a STRING is skipped, because that is the hash-rocket
    verb form `get "/x" => "c#a"`, which is read positionally below.
    """
    out: dict = {}
    for node in args:
        if node.type != "pair":
            continue
        key = _rb_symbol(node.child_by_field_name("key"), src)
        if key is not None:
            out[key] = node.child_by_field_name("value")
    return out


def _rb_symbol_set(node, src: bytes) -> set | None:
    """The symbol names in `[:index, :show]`, `%i[index show]`, or a bare
    `:index`. TRI-STATE, and the third state is the point.

      * a `set` of names — the value node was read, completely;
      * `set()` — the value node was read and is a genuinely EMPTY literal;
      * `None` — the value node could not be read, in whole or in part.

    The two-state version returned `set()` for the last two facts alike, and
    `_expand_resource` reads a falsy filter as "no filter", so an `only:` this
    reader could not parse rendered EVERY action with nothing said about it.
    That is the empty-set-means-permissive defect that produced 144 phantom
    routes on `rubygems-org`, and it is permissive in both directions:

      * `only: ALLOWED` (a constant) matched neither branch below and fell to
        `set()`, so a declaration of two actions rendered all eight — six
        phantom routes from one line;
      * `only: [:index, *EXTRA]` reached the comprehension, which DROPPED the
        splat it could not read and returned `{index}` — a partial set the
        caller cannot tell from a complete one, so one row rendered where the
        declaration admits more.

    A list is now read as a whole or not at all. `None` propagates to the
    caller, which renders the full action set and NAMES a residual — the same
    over-claim, reported rather than silent. This is the tri-state discipline
    `_undominated` already applies to pack resolution.

    `set()` now means what Rails means by it. `resources :x, only: []` declares
    ZERO REST actions, and `_expand_resource` renders zero rows for it; a block
    route such as `get 'avatar', on: :member` still renders, because the block
    is walked independently of the filter. The earlier reading — empty filter as
    "no filter" — put 37 phantom rows into the `rubygems-org` golden from five
    declarations, and was the direct source of that golden's 11 duplicate rows.

    An ABSENT `only:` is not this function's business: the caller tests the key
    and passes `None` (no filter) without calling here, which is why a `None`
    node is "not read" rather than "empty".
    """
    if node is None:
        return None                      # an option with no value node — unread.
    single = _rb_symbol(node, src)
    if single:
        return {single}
    if node.type not in ("array", "symbol_array"):
        return None                      # not a literal action list — not read.
    names = set()
    for child in node.children:
        if not child.is_named:
            continue                     # `[`, `]`, `,` — punctuation, not an element.
        name = _rb_symbol(child, src)
        if name is None:
            return None                  # one unreadable element, one unread list.
        names.add(name)
    return names


class _RbResource(NamedTuple):
    """The enclosing `resources`/`resource` block, as its children need it.

    `member` and `collection` are the two path prefixes a `member do` /
    `collection do` block (or an `on:` option) selects between; the block's own
    default prefix carries the nested `:<singular>_id` segment and lives in the
    walk's `path_prefix`. `controller` is what a handler-less verb infers.
    """

    member: tuple
    collection: tuple
    controller: str


_PLURAL_ACTIONS = [
    ("index", "GET", ""), ("new", "GET", "/new"), ("create", "POST", ""),
    ("show", "GET", "/:id"), ("edit", "GET", "/:id/edit"),
    ("update", "PATCH", "/:id"), ("update", "PUT", "/:id"),
    ("destroy", "DELETE", "/:id"),
]
_SINGULAR_ACTIONS = [
    ("new", "GET", "/new"), ("create", "POST", ""), ("show", "GET", ""),
    ("edit", "GET", "/edit"), ("update", "PATCH", ""), ("update", "PUT", ""),
    ("destroy", "DELETE", ""),
]


def _pluralize(name: str) -> str:
    return name if name.endswith("s") else name + "s"


def _expand_resource(name, singular, path_prefix, mod_prefix, only, excpt) -> list:
    """Expand a `resources`/`resource` declaration into canonical REST rows,
    honoring `only:`/`except:` (point 1). `update` yields both a PATCH and a PUT
    row. Member/collection are out of scope (the under-count residual).

    `only`/`excpt` are tri-state, and the test is `is not None` rather than a
    truth test, because an EMPTY filter is not an absent one:

      * `None` — no filter, or one the reader could not read. The full action
        set renders. The caller turns the unread case into a NAMED residual,
        and the caller is where that belongs, because this function renders one
        declaration and the residual is a property of the document.
      * `set()` — `only: []`, which in Rails declares zero REST actions. Zero
        rows render. `except: []` excludes nothing and still renders all.
      * a populated set — that filter.
    """
    actions = _SINGULAR_ACTIONS if singular else _PLURAL_ACTIONS
    controller = "/".join([*mod_prefix, _pluralize(name) if singular else name])
    base = "/" + "/".join([*path_prefix, name])
    rows = []
    for action, method, suffix in actions:
        if only is not None and action not in only:
            continue
        if excpt is not None and action in excpt:
            continue
        rows.append((method, _norm_path(base + suffix), f"{controller}#{action}"))
    return rows


def _parse_routes_tree(raw: bytes) -> tuple:
    """Parse `config/routes.rb` into `(rows, residuals)` by walking its tree.

    A walk of the parse tree (ADR-0096 clause 1), not a line scan. A `namespace`
    or `scope` call pushes a `(path, module)` frame bounded by ITS OWN
    `do_block`, so the frame's extent is a structural fact rather than a
    `do`/`end` counter's guess. A `resources` call expands through the unchanged
    `_expand_resource` and pushes the nested `:<singular>_id` frame its children
    inherit; `member`/`collection` select the two other prefixes that block
    offers. The DSL is never executed.

    The walk is an EXPLICIT STACK rather than recursion. A crafted file of deeply
    nested blocks would raise `RecursionError` from a recursive walk, and that
    exception escapes every fail-closed net in this module — the failure class
    ADR-0069's `scan_interp` shipped and the security review caught.

    A `call` node contributes only its BLOCK to the walk, never its arguments: a
    routing macro's arguments are paths, symbols and options, and descending into
    them would read the body of a `lambda { … }` passed to `authenticate` as if
    it declared routes. Every other node type contributes all its children, which
    is what carries the walk through an `if`, a `case` or a `begin` without the
    conditional's `end` disturbing a frame.
    """
    tree = _ruby_ts_parse(raw)
    rows: list = []
    saw_root = saw_mount = saw_dynamic_path = saw_no_handler = False
    saw_unexpanded_option = saw_other_macro = saw_unread_filter = False

    def emit(verb: str, prefix: tuple, mod_prefix: tuple, seg, handler,
             res: _RbResource | None) -> None:
        """Render one verb declaration, or record why it could not be."""
        nonlocal saw_dynamic_path, saw_no_handler
        if seg is None:
            saw_dynamic_path = True
            return
        full_path = _norm_path("/".join([*prefix, seg.strip("/")]))
        if handler is not None and "#" in handler:
            ctrl, action = handler.split("#", 1)
            rows.append((verb, full_path, "/".join([*mod_prefix, ctrl]) + "#" + action))
            return
        # No static `controller#action`. Rails infers both from the enclosing
        # resource when there is one, and that inference is exactly what makes
        # `get :subscribe` inside a `member do` a route rather than a mystery.
        if handler is None and res is not None and _RUBY_ACTION_SEGMENT.match(seg):
            rows.append((verb, full_path, f"{res.controller}#{seg}"))
            return
        saw_no_handler = True

    # (node, path_prefix, mod_prefix, enclosing resource). Popped, not recursed.
    stack = [(tree.root_node, (), (), None)]
    while stack:
        node, path_prefix, mod_prefix, res = stack.pop()
        if node.type != "call":
            for child in node.children:
                stack.append((child, path_prefix, mod_prefix, res))
            continue

        name = _rb_call_name(node, raw)
        args = _rb_args(node)
        opts = _rb_keywords(args, raw)
        body = _rb_block(node)
        inner_path, inner_mod, inner_res = path_prefix, mod_prefix, res

        if name == "namespace":
            seg = _rb_symbol(args[0], raw) if args else None
            if seg is None:
                saw_dynamic_path = True
            else:
                inner_path = (*path_prefix, seg)
                inner_mod = (*mod_prefix, seg)
            inner_res = None
        elif name == "scope":
            # Only the leading STRING form frames a path, which is what the
            # pattern reader did. `scope :oauth` and `scope path: "/x"` are
            # named residuals rather than silent reframings, because reframing
            # them would move paths this unit is not measuring.
            seg = _rb_string(args[0], raw) if args else None
            mod = _rb_string(opts.get("module"), raw)
            if seg is None and args and not _rb_keywords(args[:1], raw):
                saw_unexpanded_option = True
            if seg and seg.strip("/"):
                inner_path = (*path_prefix, seg.strip("/"))
            if mod and mod.strip("/"):
                inner_mod = (*mod_prefix, mod.strip("/"))
            if seg is None and ("path" in opts or "scope" in opts):
                saw_unexpanded_option = True
            inner_res = None
        elif name in ("resources", "resource"):
            singular = name == "resource"
            seg = _rb_symbol(args[0], raw) if args else _rb_string(
                args[0], raw) if args else None
            if seg is None and args:
                seg = _rb_string(args[0], raw)
            if seg is None:
                saw_dynamic_path = True
            else:
                # Key presence decides "no filter" (None) from `only: []`
                # (an empty set, which declares zero actions); the helper
                # itself never sees an absent option, so its `None` means
                # exactly one thing — a filter that could not be read.
                only = _rb_symbol_set(opts["only"], raw) if "only" in opts else None
                excpt = _rb_symbol_set(opts["except"], raw) if "except" in opts else None
                if ("only" in opts and only is None) or (
                        "except" in opts and excpt is None):
                    saw_unread_filter = True
                rows += _expand_resource(
                    seg, singular, list(path_prefix), list(mod_prefix),
                    only, excpt)
                if "path" in opts or "param" in opts or "controller" in opts:
                    saw_unexpanded_option = True
                controller = "/".join(
                    [*mod_prefix, _pluralize(seg) if singular else seg])
                base = (*path_prefix, seg)
                if singular:
                    inner_path = base
                    inner_res = _RbResource(base, base, controller)
                else:
                    inner_path = (*base, ":" + _singularize(seg) + "_id")
                    inner_res = _RbResource(
                        (*base, ":id"), base, controller)
        elif name in ("member", "collection") and res is not None:
            inner_path = res.member if name == "member" else res.collection
        elif name in _RUBY_ROUTE_VERBS:
            prefix = path_prefix
            on = _rb_symbol(opts.get("on"), raw)
            if res is not None and on in ("member", "collection"):
                prefix = res.member if on == "member" else res.collection
            handler = _rb_string(opts.get("to"), raw)
            seg = None
            if args:
                first = args[0]
                if first.type == "pair":
                    # The hash-rocket form: `get "/x" => "c#a"`. The path is the
                    # pair's KEY, which no keyword read would reach.
                    seg = _rb_string(first.child_by_field_name("key"), raw)
                    handler = handler or _rb_string(
                        first.child_by_field_name("value"), raw)
                else:
                    seg = _rb_string(first, raw)
                    if seg is None:
                        seg = _rb_symbol(first, raw)
            if "to" in opts and handler is None:
                # `to: redirect(…)` / `to: SomeRackApp` — a handler with no
                # static `controller#action`, so nothing is claimed for it.
                emit(name.upper(), prefix, mod_prefix, seg, "", res)
            else:
                emit(name.upper(), prefix, mod_prefix, seg, handler, res)
        elif name == "root":
            saw_root = True
        elif name == "mount":
            saw_mount = True
        elif name in ("concern", "concerns", "direct", "resolve", "draw", "match"):
            saw_other_macro = True
        elif "shallow" in opts:
            saw_unexpanded_option = True

        if "shallow" in opts:
            saw_unexpanded_option = True
        if body is not None:
            stack.append((body, inner_path, inner_mod, inner_res))

    residuals: list = []
    if saw_root:
        residuals.append("A `root` declaration was found and not expanded (a "
                         "named residual).")
    if saw_mount:
        residuals.append("A `mount`ed Rack application or engine contributes the "
                         "whole route table of another program and was not "
                         "expanded (a named residual).")
    if saw_other_macro:
        residuals.append("A route `concern`, a `direct`/`resolve` helper, a "
                         "`match … via:` declaration or a metaprogrammed `draw` "
                         "was found and not expanded (a named residual).")
    if saw_unexpanded_option:
        residuals.append("An option that RESTRUCTURES a path or a controller — "
                         "`path:`, `param:`, `controller:`, `shallow:`, or a "
                         "`scope` whose path is a symbol or a `path:` keyword — "
                         "was read and not applied (a named residual).")
    if saw_unread_filter:
        residuals.append("An `only:`/`except:` action list that is not a literal "
                         "symbol or array — a constant, a method call, or a list "
                         "carrying a splat — could not be read, so EVERY REST "
                         "action was rendered for that declaration and the rows "
                         "above over-claim (a named residual).")
    if saw_dynamic_path:
        residuals.append("A path composed at load time (an interpolated string "
                         "or a non-literal argument) has no static value and was "
                         "not rendered (a named residual).")
    if saw_no_handler:
        residuals.append("A verb declaration with no `to:` handler this reader "
                         "could resolve to a static `controller#action`, and no "
                         "enclosing resource to infer one from, was not rendered "
                         "(a named residual).")
    if tree.root_node.has_error:
        residuals.append("The Ruby grammar reported a syntax error in this file; "
                         "declarations inside the unparsed region were not read "
                         "(a named residual).")
    return rows, residuals


def _detect_ruby_routes(root: Path) -> bool:
    root = Path(root)
    rr = _find_rails_root(root) or root
    return (rr / "config" / "routes.rb").is_file()


def extract_ruby_api_surface_routes(root: Path, docs_dir: str) -> tuple[str, dict]:
    """API surface from a static `config/routes.rb` parse (point 1, ADR-0096
    clause 1). Renders one row per REST action / explicit verb, sorted by
    (path, method, controller), labeled a static parse, with its residuals.

    tree-sitter has no unparseable input: a malformed file yields a tree carrying
    `ERROR` nodes, which `_parse_routes_tree` reports as a named residual over
    whatever it did read. That is the same fail-closed posture in a different
    shape — degrade one file and say so, never a truncated parse presented as
    complete."""
    root = Path(root)
    rr = _find_rails_root(root) or root
    routes = rr / "config" / "routes.rb"
    oversize: list = []
    raw = _safe_read_bytes(root, routes, oversize)
    if raw is None:
        return _canon(["# API surface", "", NO_EXTRACTOR.rstrip("\n")]), {}
    sources = {_rel(root, routes): _sha256_hex(raw)}
    rows, residuals = _parse_routes_tree(raw)
    if not rows:
        return _canon(["# API surface", "", NO_EXTRACTOR.rstrip("\n")]), sources
    out = ["# API surface", "",
           "_Static parse of `config/routes.rb`; the DSL is not executed._", "",
           f"## Routes ({len(rows)})", "",
           "| method | path | controller |", "|---|---|---|"]
    for method, path, controller in sorted(rows, key=lambda r: (r[1], r[0], r[2])):
        out.append(f"| {_cell(method)} | `{_cell(path)}` | `{_cell(controller)}` |")
    out.append("")
    out += ["## Residuals", "", f"- {_ROUTES_UNDER_COUNT}"]
    for r in residuals:
        out.append(f"- {r}")
    out.append("")
    return _canon(out), sources


# ── data-model: db/schema.rb, ActiveRecord's own emitted schema dump ─────────

_CREATE_TABLE_HEAD = re.compile(r"""^create_table\s+["']([^"']+)["']""")
_ADD_FK = re.compile(
    r"""^add_foreign_key\s+["']([^"']+)["']\s*,\s*["']([^"']+)["']"""
)
_FK_COLUMN = re.compile(r"""column:\s*["']([^"']+)["']""")
_T_COLUMN = re.compile(r"""^t\.([A-Za-z_]\w*)\s+["']([^"']+)["']""")
_T_INDEX = re.compile(r"^t\.index\b")
_INDEX_COLS = re.compile(r"t\.index\s+\[([^\]]*)\]")
_INDEX_NAME = re.compile(r"""name:\s*["']([^"']+)["']""")
_INDEX_UNIQUE = re.compile(r"unique:\s*true")
_NULL_FALSE = re.compile(r"null:\s*false")
_NULL_TRUE = re.compile(r"null:\s*true")
_DEFAULT = re.compile(r"""default:\s*("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|[^,\n]+)""")


def _null_opt(line: str) -> str:
    if _NULL_FALSE.search(line):
        return "no"
    if _NULL_TRUE.search(line):
        return "yes"
    return "—"


def _default_opt(line: str):
    m = _DEFAULT.search(line)
    if not m:
        return None
    v = m.group(1).strip()
    if len(v) >= 2 and v[0] in "\"'" and v[-1] == v[0]:
        v = v[1:-1]
    return v


def _index_note(line: str) -> tuple:
    cm = _INDEX_COLS.search(line)
    cols = ", ".join(s.strip().strip("\"'") for s in cm.group(1).split(",") if s.strip()) if cm else ""
    unique = bool(_INDEX_UNIQUE.search(line))
    nm = _INDEX_NAME.search(line)
    parts = [p for p in (cols, "unique" if unique else "") if p]
    return (nm.group(1) if nm else "", f"({', '.join(parts)})" if parts else "")


def _parse_schema(text: str) -> tuple:
    """Parse `db/schema.rb` create_table blocks + top-level add_foreign_key
    (point 2). Linear line scan (no backtracking). Returns (tables, fks)."""
    text = _scrub_ruby_comments(text)
    tables: list = []
    fks: list = []
    cur = None
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if cur is None:
            m = _CREATE_TABLE_HEAD.match(line)
            if m:
                cur = {"name": m.group(1), "cols": [], "indexes": []}
                continue
            fk = _ADD_FK.match(line)
            if fk:
                col = _FK_COLUMN.search(line)
                fks.append((fk.group(1), fk.group(2), col.group(1) if col else None))
            continue
        if line == "end" or line.startswith("end"):
            tables.append(cur)
            cur = None
            continue
        if _T_INDEX.match(line):
            cur["indexes"].append(_index_note(line))
            continue
        col = _T_COLUMN.match(line)
        if col:
            cur["cols"].append((col.group(2), col.group(1),
                                _null_opt(line), _default_opt(line)))
    return tables, fks


def _build_fk_map(fks: list) -> tuple:
    """Map (from_table, column) → referenced_table. The referencing column is the
    explicit `column:` when present, else the inferred `<referenced_singular>_id`
    (the inferred case is a named residual — point 2)."""
    fk_map: dict = {}
    inferred = False
    for from_table, ref_table, column in fks:
        col = column if column else _singularize(ref_table) + "_id"
        if not column:
            inferred = True
        fk_map[(from_table, col)] = ref_table
    return fk_map, inferred


def _detect_ruby_schema_rb(root: Path) -> bool:
    return (Path(root) / "db" / "schema.rb").is_file()


def extract_ruby_data_model(root: Path, docs_dir: str) -> tuple[str, dict]:
    """Data model from `db/schema.rb` — ActiveRecord's committed schema dump
    (point 2). Renders the fixed six-column entity table, a per-table indexes
    note, and FK/oversize residuals. Sorted by (table, column)."""
    root = Path(root)
    schema = root / "db" / "schema.rb"
    oversize: list = []
    raw = _safe_read_bytes(root, schema, oversize)
    if raw is None:
        return _canon(["# Data model", "", NO_EXTRACTOR.rstrip("\n")]), {}
    sources = {_rel(root, schema): _sha256_hex(raw)}
    tables, fks = _parse_schema(raw.decode("utf-8", errors="replace"))
    if not tables:
        return _canon(["# Data model", "", NO_EXTRACTOR.rstrip("\n")]), sources
    fk_map, inferred = _build_fk_map(fks)

    rows: list = []
    for t in tables:
        for (cname, typ, null, default) in t["cols"]:
            rows.append((t["name"], cname, typ, null,
                         default if default is not None else "—",
                         fk_map.get((t["name"], cname), "—")))
    rows.sort(key=lambda r: (r[0], r[1]))

    out = ["# Data model", "",
           "_Derived from `db/schema.rb` (ActiveRecord's committed schema dump)._",
           "", f"## Entities ({len(tables)} tables)", "",
           "| table | column | type | null | default | fk |",
           "|---|---|---|---|---|---|"]
    for table, col, typ, null, default, fk in rows:
        out.append(f"| {_cell(table)} | `{_cell(col)}` | {_cell(typ)} | {null} "
                   f"| {_cell(default)} | {_cell(fk)} |")
    out.append("")

    idx_lines = []
    for t in sorted(tables, key=lambda x: x["name"]):
        if t["indexes"]:
            # `lbl` is escaped in BOTH branches. It was `_cell`'d in the `else`
            # and raw in the `if`, so an index that carried a name — the exact
            # case the `if` exists for — rendered its label unescaped next to a
            # code span. A `name:`-bearing index whose label held a backtick
            # closed the span around `n` and let the rest of the label render as
            # markdown; one holding a pipe broke the enclosing list row.
            notes = "; ".join(
                (f"`{_cell(n)}` {_cell(lbl)}".strip() if n else _cell(lbl))
                for n, lbl in t["indexes"]
            )
            idx_lines.append(f"- `{_cell(t['name'])}`: {notes}")
    if idx_lines:
        out += ["## Indexes", ""] + idx_lines + [""]

    res: list = []
    if inferred:
        res.append("An `add_foreign_key` without an explicit `column:` infers the "
                   "referencing column as `<referenced_singular>_id`, which "
                   "mis-attributes a non-conventional FK column (a named residual).")
    if oversize:
        res.append("Skipped for exceeding the 2 MB bound: "
                   + ", ".join(f"`{_cell(p)}`" for p in sorted(oversize)))
    if res:
        out += ["## Residuals", ""] + [f"- {r}" for r in res] + [""]
    return _canon(out), sources


# U-R3: `extract_ruby_structure_sql_stub` and `_detect_ruby_structure_sql` were
# HERE, and they are gone rather than replaced. The probe was worse than no
# probe. It rendered one sentence, hashed `db/structure.sql`, and returned zero
# entities — so `core.concern_verdict` reached its third branch and recorded
# `no_entities`, whose definition is "the declared input was consumed
# successfully and declares none of the concern's entities". Applied to a schema
# file the probe never opened for meaning, that is simply false, and a reader
# with no other source would take it as "this application declares no tables".
#
# The replacement is the honest verdict, which needs no code: with the probe
# removed the chain has one rung, a repository carrying only `db/structure.sql`
# reaches no probe, and the core records `precondition_missing` — "the declared
# input is absent" — which is true, and whose `expected` sentence names the file
# that IS the input and the command that emits it.
#
# Why not a SQL DDL parser instead, which clause 1 would admit? Because it could
# not be measured. `db/schema.rb` wins this chain on BOTH corpus Ruby
# repositories — `rubygems-org/db/` carries `schema.rb` and no `structure.sql`,
# and `codetriage/db/` carries both — so a DDL reader written here would never
# execute against real code in the corpus, and shipping an unmeasured extractor
# is the thing ADR-0096 exists to stop. Follows the
# `extract_elixir_migrations_stub` retirement precedent exactly, with the
# opposite conclusion about what replaces it, and for a stated reason.


# ─────────────────────── pack interface (ADR-0096 clause 12) ─────────────────

#: Empty: the core's marker scan prunes no directory from this pack's own
#: `detect` calls, so this pack's marker semantics are unchanged.
DETECT_EXCLUDE: frozenset[str] = frozenset()


def detect(root: Path) -> DetectResult:
    """Does this repository look like a Ruby project? Wraps the pre-split marker
    predicate `_detect_ruby` — a `Gemfile` or any `*.gemspec` at the repo root —
    so `matched` is behaviourally identical to it. `markers` names the
    repo-relative marker paths that fired, sorted, so an ambiguous multi-pack
    match can be rendered naming each candidate AND its marker (clause 6)."""
    root = Path(root)
    markers: list[str] = []
    if (root / "Gemfile").exists():
        markers.append("Gemfile")
    markers += [p.name for p in root.glob("*.gemspec")]
    markers.sort()
    return DetectResult(matched=bool(markers), markers=tuple(markers))


# The ruby pack's declared input class per concern (ADR-0096 clause 1).
# `decision-index` is the universal concern the core declares and binds.
#
# `data-model` is a committed artifact in the strict sense clause 1 means, and
# the distinction is worth writing out because this pack holds both sides of it.
#
# PROVENANCE DECIDES ADMISSIBILITY, NOT LANGUAGE. `db/schema.rb` and
# `config/routes.rb` are both Ruby, and only one of them may be read with a
# pattern. `db/schema.rb` is written by ActiveRecord's schema dumper, so its
# grammar is the emitter's: it emits `t.string "name", null: false` and never
# `t.string("name", null: false)`, because no human chooses. A pattern over it
# has a BOUNDED failure class — it breaks when the dumper's output format
# changes, which is a Rails version fact a reader can look up.
# `config/routes.rb` is written by a developer, so its grammar is the whole
# Ruby language and its surface syntax is whatever a formatter picked. A pattern
# over it has NO bounded failure class, which is why that concern's reader is a
# tree-sitter parse. Same language, opposite answers, and the input's provenance
# is the only thing that differs.
#
# THE NEAR MISS. `db/structure.sql` is ALSO an emitted artifact — `pg_dump`
# writes it — so the same argument would admit a pattern over it. This pack does
# not read it, and the reason is fidelity and effort rather than admissibility:
# a SQL DDL reader written here would ship unmeasured, because `db/schema.rb`
# wins the chain on BOTH corpus Ruby repositories and the reader would never
# run against real code. So the honest answer is no reader at all — see U-R3,
# where the probe that pretended to be one was removed.
#
# `artifact` and `refresh` are clause 7's line, and this concern draws it:
# `db/schema.rb` is what ActiveRecord emitted, `db/migrate/**/*.rb` is what it
# emitted the file FROM, and commit order between them is exactly the question
# the staleness advisory asks. The advisory is reported-only — it never enters
# `coverage.json`, never changes a verdict, and never fails `--strict`.
#
# It is NOT the only concern to declare `artifact`, and an earlier draft of this
# comment claimed it was ("the first shipped `committed-artifact` concern to
# draw it"). Three concerns ship a declaration today, and the checkable split is:
#   * this one — its sole probe declares `committed-artifact`, `artifact` +
#     `refresh`. The only concern whose lone probe carries either; the crux
#     pack's two declare neither, and so does the core's universal
#     `decision-index`.
#   * `api-surface` below — two of its three probes declare `committed-artifact`
#     with `artifact` + `refresh`, but the chain's DERIVED class is `parser`
#     (see below) because the third, weaker rung is a real parse.
#   * the python pack's `api-surface` — its committed-artifact probe declares
#     `artifact=("openapi.json",)` and NO `refresh`, because nothing in that
#     pack knows the command that regenerates a hand-committed OpenAPI
#     document.
# So the line is drawn by probe kind (ADR-0097 part 1) and by whether a refresh
# command is knowable, not by shipping order — which is why the precedence
# claim is gone rather than renumbered.
#
# `api-surface`'s three probes declare, in chain order, `committed-artifact`
# (the OpenAPI document), `committed-artifact` (the routes-artifact dump), then
# `parser` for `config/routes.rb`, which is hand-authored source — U-R1b closed
# the gap this comment used to name: that last rung is now a tree-sitter Ruby
# parse, so its declaration is true of the reader and not only of the input.
# `input_classes()` DERIVES the concern's class as the minimum over that chain
# (ADR-0097 part 1), which is `parser` — a concern is only as strong as its
# weakest alternative.
#
# `artifact` names the middle rung, `arch-inputs/routes.txt`, which is the one
# glob in this set that an emitter wrote: it is the stdout of `bin/rails routes
# --expanded`, so its grammar is fixed by Rails' own `ExpandedFormatter` rather
# than by a human, which is what makes the pattern read of it admissible on a
# probe declaring `committed-artifact`. That argument is made in full further
# up this file, under "WHY A PATTERN READ IS ADMISSIBLE HERE", immediately
# above `_RAILS_DUMP_RECORD`. Clause 7 needs the emitted file told apart from
# the parsed sources beside it; without the line, the staleness question
# degenerates into "was one source committed after another source in the same
# set". The concern's DERIVED `kind` stays `parser` because the class is named
# at the chain's weakest rung, so `annotate` never raises the advisory for it —
# the declaration is a record that survives a reordering, not a live signal.
INPUT_CLASSES = {
    "data-model": InputClass(
        # `expected` describes the INPUT and nothing else: the artifact, who
        # emitted it, and why a pattern read of it is admissible (U-R2, and the
        # argument set out in full under "WHY A PATTERN READ IS ADMISSIBLE
        # HERE" above). It names no command. `refresh` below is the single
        # declared source of command text, and clause 9 composes the two into
        # the `precondition_missing` remediation — so a command spelled here as
        # well would be named twice in one reader-facing line.
        expected="`db/schema.rb` — the schema dump ActiveRecord emits, so its "
                 "grammar is the emitter's rather than a human's; a "
                 "`db/structure.sql` is found but not read",
        globs=("db/schema.rb", "**/db/schema.rb",
               "db/migrate/**/*.rb", "**/db/migrate/**/*.rb"),
        artifact=("db/schema.rb",),
        refresh="bin/rails db:schema:dump",
    ),
    "api-surface": InputClass(
        expected="a committed OpenAPI document, else `arch-inputs/routes.txt`, "
                 "else `config/routes.rb`",
        globs=("openapi.json", "swagger.json", "docs/openapi.json",
               "swagger/v1/swagger.json", "doc/openapi.json",
               "arch-inputs/routes.txt", "**/arch-inputs/routes.txt",
               "config/routes.rb", "**/config/routes.rb"),
        parser=("tree_sitter", "tree_sitter_ruby"),
        artifact=("arch-inputs/routes.txt",),
        refresh=_ROUTES_ARTIFACT_REFRESH,
    ),
    "module-graph": InputClass(
        expected="Ruby sources under a Zeitwerk autoload root (`app/`, `lib/`)",
        globs=("*.rb", "**/*.rb", "Gemfile", "*.gemspec"),
    ),
}


def probes() -> dict[str, list[Probe]]:
    """The ruby pack's probe registry — exactly what `_pack_probes("ruby", …)`
    bound before the split, minus the universal `decision-index` probe, which
    the core binds for every pack.

    Ordered probes per concern (ADR-0067 point 4): committed OpenAPI first, then
    the committed `bin/rails routes --expanded` dump (U-R1a), then the
    `config/routes.rb` parse. `data-model` has ONE rung — `db/schema.rb` — since
    U-R3 removed the `db/structure.sql` probe that read nothing. First matching
    probe wins; each degrades to the stub.

    The api-surface chain is ordered by how much of Rails' own resolution the
    input already carries. The OpenAPI document is a contract a human published;
    the route dump is the table Rails resolved, with engine mounts, route
    `concern`s and metaprogrammed draws already expanded; `config/routes.rb` is
    the DSL before any of that happened, and is the rung that names them as
    residuals."""
    return {
        "data-model": [
            Probe(_detect_ruby_schema_rb, extract_ruby_data_model,
                  kind="committed-artifact"),
        ],
        "api-surface": [
            Probe(_detect_ruby_openapi, extract_ruby_api_surface_openapi,
                  kind="committed-artifact"),
            Probe(_detect_ruby_routes_artifact,
                  extract_ruby_api_surface_routes_artifact,
                  kind="committed-artifact"),
            Probe(_detect_ruby_routes, extract_ruby_api_surface_routes,
                  kind="parser"),
        ],
        "module-graph": [
            Probe(_detect_ruby_module_graph, extract_ruby_module_graph,
                  kind="regex-over-source"),
        ],
    }
