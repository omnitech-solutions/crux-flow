"""The Elixir/Phoenix stack pack (ADR-0069), split out of the deriver under ADR-0096 clause 12.

What it extracts, for a repository whose stack marker is `mix.exs`:
  * api-surface — a committed OpenAPI/Swagger document first, else a static parse
    of the Phoenix `router.ex` DSL (`scope`/`pipe_through`/`resources`/`live`/verbs).
  * data-model — Ecto `schema`/`embedded_schema` blocks walked across every `.ex`
    (umbrella-covering); the `priv/repo/migrations/` presence stub is the fallback.
  * module-graph — `defmodule` nodes with alias/import/use/require + remote-ref
    edges, resolved through each file's alias table and RESOLVE-OR-DROP.

Everything here is static: no Elixir, `mix`, or BEAM is executed (ADR-0069
point 6). Every read is containment-checked, size-bounded and hashed into
`sources`; the comment/sigil/heredoc scrub is fail-closed (`_ElixirScrubBail`).

Every parser this pack uses is named by the consuming concern's input class and
pinned in the deriver's PEP 723 block (ADR-0097 part 5). `api-surface` and
`data-model` both declare `parser=_ELIXIR_TS_PARSER_MODULES` —
`("tree_sitter", "tree_sitter_elixir")` — and read Elixir through
`_elixir_parse`; `module-graph` declares none and needs nothing beyond the
stdlib.

Naming the parser on the declaration is what gives a missing grammar a lane of
its own. `core.resolve_declared_parsers` runs BEFORE any extraction and raises
`ParserUnavailable` when a declared grammar is not importable, which
`derive-arch.py` reports as exit 2 — the no-verdict lane, not a drift finding.
On a machine without those wheels this pack refuses rather than degrading to a
stub.

HISTORY — true of the U7 move, not of the tree as it stands. This module began
as a VERBATIM move of the elixir banner region of `derive.py` (ADR-0096 clause
12, "the monolith splits"). Clause 12's gate was byte-identity — every corpus
golden unchanged across the split — so nothing below the import block was
rewritten while it moved, and only the cross-boundary references were rebound to
`..core` imports. That claim is scoped to that move and is still true OF IT;
later units then added code that was never in `derive.py`, the tree-sitter
reader above being the largest. The two additions at the foot, `detect` and
`probes`, are the pack's clause-12 surface (a detector plus a probe registry).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from ..core import (
    DetectResult,
    InputClass,
    NO_EXTRACTOR,
    Probe,
    _MAX_GRAPH_EDGES,
    _MAX_SCAN_BYTES,
    _MAX_SCAN_FILES,
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
    _split_top_level,
)


# ─────────────────────── elixir stack pack (ADR-0069) ───────────────────────
# Static, deterministic extractors. No Elixir/mix is executed (ADR-0069
# point 6). `api-surface` and `data-model` declare the tree-sitter Elixir
# grammar, and a machine missing it takes the exit-2 no-verdict lane rather
# than a stub — see the module docstring.
# Every file read is containment-checked, size-bounded (2 MB
# via `_MAX_FILE_BYTES`) and hashed into `sources`, so a changed source drifts
# the spine. Enumeration is `os.walk(followlinks=False)` with codepoint-sorted
# dir/file order and aggregate file/byte caps + a residual. This repo pins
# `arch_stack: crux`, so these never run here (point 7). All three concerns are
# populated: api-surface (committed OpenAPI → a static Phoenix router parse),
# data-model (Ecto schemas, migrations a presence stub) and module-graph (the
# explicit alias/import/use + remote-ref dependency graph, resolve-or-drop).

_ELIXIR_SIGIL_OPEN = {"(": ")", "[": "]", "{": "}", "<": ">"}
_ELIXIR_SIGIL_DELIMS = set("([{<") | set("/|\"'")


class _ElixirScrubBail(Exception):
    """The comment/sigil/heredoc scrub could not confidently close a construct.

    Raised by `_scrub_elixir_comments` on an unbalanced sigil, heredoc, or `#{}`
    interpolation (ADR-0069 point 4, fail-closed). The caller hashes-and-notes
    the file as an unparseable residual rather than emitting a truncated parse.
    """


def _scrub_elixir_comments(text: str, blank_strings: bool = False) -> str:
    """String+sigil+heredoc-aware `#`-to-end-of-line comment scrub (ADR-0069
    point 4). Guards `#{}` interpolation, the `?#` char literal, and `#` inside
    `"…"`/`'…'`/`\"\"\"…\"\"\"` heredocs and `~s`/`~S`/`~r`/`~w` sigils with their
    delimiter pairs. FAIL-CLOSED: an unclosed construct raises `_ElixirScrubBail`.

    `blank_strings` replaces every string/sigil/heredoc interior (and delimiters)
    with spaces, preserving newlines — used by the module graph so a module name
    inside a docstring or string cannot create a phantom edge. Left False for the
    router and schema parses, which read route paths and defaults from strings.
    """
    n = len(text)
    out: list = []

    def blanked(s: str) -> str:
        return "".join(ch if ch == "\n" else " " for ch in s)

    def emit(s: str, is_string: bool) -> None:
        out.append(blanked(s) if (is_string and blank_strings) else s)

    def skip_simple(k: int, quote: str) -> int:
        # A single-line string starting at text[k]==quote (used inside an
        # interpolation). Returns the index just past the closing quote.
        j = k + 1
        while j < n:
            ch = text[j]
            if ch == "\\" and j + 1 < n:
                j += 2
                continue
            if ch == quote:
                return j + 1
            if ch == "\n":
                raise _ElixirScrubBail("unterminated string in interpolation")
            j += 1
        raise _ElixirScrubBail("unterminated string in interpolation")

    def scan_interp(k: int, nest: int = 0) -> int:
        # text[k]=='#', text[k+1]=='{'. Returns index just past the matching '}'.
        # A crafted file of deeply-nested `#{` would blow the Python recursion
        # limit; a RecursionError is NOT a _ElixirScrubBail and would escape the
        # per-file handlers and crash the whole derive (point 4). Cap the nesting
        # and bail fail-closed well before the interpreter limit.
        if nest > 200:
            raise _ElixirScrubBail("interpolation nested too deep")
        j = k + 2
        depth = 1
        while j < n:
            ch = text[j]
            if ch == "{":
                depth += 1
                j += 1
            elif ch == "}":
                depth -= 1
                j += 1
                if depth == 0:
                    return j
            elif ch in "\"'":
                j = skip_simple(j, ch)
            elif ch == "#" and j + 1 < n and text[j + 1] == "{":
                j = scan_interp(j, nest + 1)
            else:
                j += 1
        raise _ElixirScrubBail("unterminated interpolation")

    def scan_string(k: int, quote: str, heredoc: bool) -> int:
        closeseq = quote * 3 if heredoc else quote
        j = k + (3 if heredoc else 1)
        while j < n:
            ch = text[j]
            if ch == "\\" and j + 1 < n:
                j += 2
                continue
            if ch == "#" and j + 1 < n and text[j + 1] == "{":
                j = scan_interp(j)
                continue
            if not heredoc and ch == "\n":
                raise _ElixirScrubBail("unterminated string")
            if text.startswith(closeseq, j):
                j += len(closeseq)
                emit(text[k:j], True)
                return j
            j += 1
        raise _ElixirScrubBail("unterminated string/heredoc")

    def scan_sigil(k: int) -> int:
        j = k + 1
        while j < n and text[j].isalpha():
            j += 1
        if j >= n or text[j] not in _ELIXIR_SIGIL_DELIMS:
            out.append(text[k:j])          # not a sigil (e.g. a `~` operator run).
            return j
        raw = text[k + 1].isupper()
        delim = text[j]
        if delim in "\"'" and text.startswith(delim * 3, j):
            closeseq = delim * 3
            m = j + 3
            while m < n:
                ch = text[m]
                if ch == "\\" and not raw and m + 1 < n:
                    m += 2
                    continue
                if text.startswith(closeseq, m):
                    m += 3
                    break
                m += 1
            else:
                raise _ElixirScrubBail("unterminated sigil heredoc")
        elif delim in _ELIXIR_SIGIL_OPEN:
            close = _ELIXIR_SIGIL_OPEN[delim]
            m = j + 1
            depth = 1
            while m < n:
                ch = text[m]
                if ch == "\\" and not raw and m + 1 < n:
                    m += 2
                    continue
                if ch == delim:
                    depth += 1
                    m += 1
                    continue
                if ch == close:
                    depth -= 1
                    m += 1
                    if depth == 0:
                        break
                    continue
                m += 1
            else:
                raise _ElixirScrubBail("unterminated sigil")
        else:
            close = delim
            m = j + 1
            while m < n:
                ch = text[m]
                if ch == "\\" and not raw and m + 1 < n:
                    m += 2
                    continue
                if ch == close:
                    m += 1
                    break
                m += 1
            else:
                raise _ElixirScrubBail("unterminated sigil")
        while m < n and text[m].isalpha():       # trailing sigil modifiers.
            m += 1
        emit(text[k:m], True)
        return m

    i = 0
    while i < n:
        c = text[i]
        if c == "?" and i + 1 < n:
            prev = text[i - 1] if i > 0 else ""
            if not (prev.isalnum() or prev == "_"):   # char literal, not `valid?`
                if text[i + 1] == "\\" and i + 2 < n:
                    out.append(text[i:i + 3])
                    i += 3
                    continue
                out.append(text[i:i + 2])
                i += 2
                continue
        if c == "#":
            nl = text.find("\n", i)
            if nl == -1:
                break
            i = nl
            continue
        if text.startswith('"""', i):
            i = scan_string(i, '"', True)
            continue
        if text.startswith("'''", i):
            i = scan_string(i, "'", True)
            continue
        if c == '"':
            i = scan_string(i, '"', False)
            continue
        if c == "'":
            i = scan_string(i, "'", False)
            continue
        if c == "~" and i + 1 < n and text[i + 1].isalpha():
            i = scan_sigil(i)
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _walk_ex_files(root: Path):
    """Yield contained `.ex` files (NOT `.exs`) under `root`, skipping VCS/vendor
    + dot dirs. `os.walk(followlinks=False)` never descends a symlinked dir; dir
    and file names are codepoint-sorted so an aggregate-cap truncation selects a
    stable, locale-independent prefix (ADR-0069 point 6)."""
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(
            d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")
        )
        for fn in sorted(filenames):
            if fn.endswith(".ex"):
                p = Path(dirpath) / fn
                if _contained(root, p):
                    yield p


def _has_ex_sources(root: Path) -> bool:
    for _ in _walk_ex_files(Path(root)):
        return True
    return False


def _scan_ex_files(root: Path):
    """Enumerate `.ex` sources (sorted, contained, size-bounded) with aggregate
    file/byte caps (ADR-0069 point 6). Returns (entries, sources, residuals):
    entries is [(repo-rel path, decoded text)]; every read file is hashed into
    `sources` (drift rule — even a schema-less `.ex` shapes the negative result)."""
    root = Path(root)
    entries: list = []
    sources: dict = {}
    residuals: list = []
    oversize: list = []
    n_files = 0
    n_bytes = 0
    capped = False
    for p in _walk_ex_files(root):
        if n_files >= _MAX_SCAN_FILES or n_bytes >= _MAX_SCAN_BYTES:
            capped = True
            break
        raw = _safe_read_bytes(root, p, oversize)
        if raw is None:
            continue
        rel = _rel(root, p)
        sources[rel] = _sha256_hex(raw)
        entries.append((rel, raw.decode("utf-8", errors="replace")))
        n_files += 1
        n_bytes += len(raw)
    if oversize:
        residuals.append("Skipped for exceeding the 2 MB bound: "
                         + ", ".join(f"`{_cell(p)}`" for p in sorted(oversize)))
    if capped:
        residuals.append(
            f"Aggregate scan cap reached ({_MAX_SCAN_FILES} files / "
            f"{_MAX_SCAN_BYTES} bytes); the remaining sorted sources were not "
            "scanned (a named residual).")
    return entries, sources, residuals


def _elixir_scan_contains(root: Path, regex) -> bool:
    """True when a contained `.ex` file matches `regex` on raw text — BOUNDED by
    the aggregate file/byte caps (ADR-0069 point 6). A marker past the cap
    degrades that probe to the stub, the same tradeoff the extractor makes."""
    root = Path(root)
    n_files = n_bytes = 0
    for p in _walk_ex_files(root):
        if n_files >= _MAX_SCAN_FILES or n_bytes >= _MAX_SCAN_BYTES:
            break
        raw = _safe_read_bytes(root, p, [])
        if raw is None:
            continue
        n_files += 1
        n_bytes += len(raw)
        if regex.search(raw.decode("utf-8", errors="replace")):
            return True
    return False


# ── api-surface: committed OpenAPI first, then a static Phoenix router parse ──

def _elixir_openapi_path(root: Path):
    for c in _OPENAPI_CANDIDATES:
        p = Path(root) / c
        if p.is_file() and _contained(Path(root), p):
            return p
    return None


def _detect_elixir_openapi(root: Path) -> bool:
    return _elixir_openapi_path(Path(root)) is not None


def extract_elixir_api_surface_openapi(root: Path, docs_dir: str) -> tuple[str, dict]:
    """API surface from a committed OpenAPI/Swagger document (ADR-0069 point 1),
    rendered through the shared `_render_openapi`. Stub when absent/unparseable."""
    root = Path(root)
    spec = _elixir_openapi_path(root)
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


# ── the tree-sitter Elixir grammar (ADR-0096 clause 1) ───────────────────────
# The api-surface concern declares the input class `parser`, and this is the
# parser. Clause 1 prohibits matching a regular expression against authored
# source for a concern that declares one, and the reason is measured rather
# than stylistic: a pattern matched against a surface syntax the project's
# FORMATTER chooses has no bounded failure class. The two spellings of one
# Phoenix declaration — `get "/x", C, :a` and `get("/x", C, :a)` — parse to the
# same `call` node with the same argument list, and no regex over the text sees
# that.

_TS_PARSER = None


def _elixir_ts_parser():
    """The tree-sitter Elixir parser, built once per process.

    The import is inside the function, deliberately. The core imports every
    pack module to read its registry, so a module-scope `import tree_sitter`
    would make a python, ruby, node, crux or swift derive fail on a machine with
    no Elixir grammar — the five packs that declare no ELIXIR parser, consult
    none, and record none in the provenance manifest. (Each declares `yaml`, and
    ruby, node and swift each declare their own grammar; the scoping word is what makes this
    sentence true, exactly as `packs/ruby.py` and `packs/node.py` scope theirs.) `core.resolve_declared_parsers`
    has already refused, before any extraction, if the grammar is absent for a
    pack that DOES declare it, so reaching here means the import resolves.
    """
    global _TS_PARSER
    if _TS_PARSER is None:
        import tree_sitter                               # noqa: PLC0415
        import tree_sitter_elixir                        # noqa: PLC0415
        _TS_PARSER = tree_sitter.Parser(
            tree_sitter.Language(tree_sitter_elixir.language()))
    return _TS_PARSER


def _elixir_parse(raw: bytes):
    """Parse Elixir source bytes into a tree-sitter tree.

    Bytes, not text: tree-sitter node offsets are byte offsets, and decoding
    first would make every slice below wrong on any file with a multi-byte
    character in it.
    """
    return _elixir_ts_parser().parse(raw)


def _ts_call_name(node, src: bytes):
    """The bare identifier a `call` node invokes, or None.

    None is every call this reader does not act on: a dotted `Mod.fun(…)`, an
    anonymous `f.()`, an operator. The Phoenix router DSL is bare identifiers
    only, so None always means "not a route declaration".
    """
    if node.type != "call" or not node.children:
        return None
    head = node.children[0]
    if head.type != "identifier":
        return None
    return src[head.start_byte:head.end_byte].decode("utf-8", errors="replace")


def _ts_args(node) -> list:
    """A `call`'s argument nodes, punctuation dropped.

    This is the whole parenthesized-versus-space-delimited fix in one line. The
    `arguments` node carries `(`, `)` and `,` as ANONYMOUS children when the
    call is written parenthesized and omits them when it is not, so filtering
    on `is_named` yields the same list for both spellings.
    """
    for child in node.children:
        if child.type == "arguments":
            return [c for c in child.children if c.is_named]
    return []


def _ts_do_block(node):
    """A `call`'s `do … end` block, or None."""
    for child in node.children:
        if child.type == "do_block":
            return child
    return None


def _ts_string(node, src: bytes):
    """A plain string literal's contents, or None when the node is not one.

    None for an INTERPOLATED string. `"/#{@prefix}/x"` has no static value —
    computing it means executing the module — so the reader names it a residual
    rather than rendering the interpolation's source text as if it were a path.
    That literal-rendering is the node pack's measured `/api/${routeName}/:id`
    defect, and there is no reason to repeat it here.
    """
    if node.type != "string":
        return None
    if any(c.type == "interpolation" for c in node.children):
        return None
    return src[node.start_byte + 1:node.end_byte - 1].decode("utf-8", errors="replace")


def _ts_alias(node, src: bytes):
    """An `alias` node's dotted module name (`MyAppWeb.PageController`), else None."""
    if node.type != "alias":
        return None
    return src[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _ts_atom(node, src: bytes):
    """An `atom` node's name without its leading colon (`:index` → `index`)."""
    if node.type != "atom":
        return None
    text = src[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
    return text[1:] if text.startswith(":") else None


def _ts_keywords(args: list, src: bytes) -> dict:
    """`{keyword: value-node}` over every `keywords` node in an argument list.

    The AST route to the options a `resources` declaration carries — `only:`,
    `except:`, `param:`, `singleton:`. Reading them off the argument nodes
    rather than pattern-matching the call's source text is what keeps this
    concern's `parser` declaration true of its whole reader and not just of the
    part that finds the call.
    """
    out: dict = {}
    for node in args:
        if node.type != "keywords":
            continue
        for pair in node.children:
            if pair.type != "pair":
                continue
            parts = [c for c in pair.children if c.is_named]
            if len(parts) < 2 or parts[0].type != "keyword":
                continue
            key = src[parts[0].start_byte:parts[0].end_byte].decode(
                "utf-8", errors="replace").strip().rstrip(":")
            out[key] = parts[1]
    return out


def _ts_atom_set(node, src: bytes) -> set | None:
    """The atom names in `[:index, :show]`, or in a bare `:index`. TRI-STATE.

      * a `set` of names — the value node was read, completely;
      * `set()` — the value node is a genuinely EMPTY literal list;
      * `None` — the value node could not be read, in whole or in part.

    The same defect `_rb_symbol_set` carries in the ruby pack, with a MORE
    idiomatic trigger: `resources "/books", BookController, only: @actions` is
    the normal Phoenix spelling of an action filter, and a module attribute
    matched neither branch here and fell to `set()`. `_expand_elixir_resource`
    reads a falsy filter as "no filter", so that declaration rendered all eight
    REST rows with nothing said about it — an over-claim indistinguishable from
    a resource that really does expose every action.

    A list is read as a whole or not at all, so an unreadable ELEMENT no longer
    yields a partial set the caller cannot recognize as partial. `None`
    propagates, and the caller renders the full action set and NAMES a residual.

    `set()` means zero actions, matching Phoenix: `resources "/x", XController,
    only: []` declares no REST actions, and `_expand_elixir_resource` renders no
    rows for it. An ABSENT `only:` is not this function's business — the caller
    tests the key and passes `None` without calling here, which is why a `None`
    node reads as "not read" rather than as "empty".
    """
    if node is None:
        return None                      # an option with no value node — unread.
    single = _ts_atom(node, src)
    if single:
        return {single}
    if node.type != "list":
        return None                      # not a literal action list — not read.
    names = set()
    for child in node.children:
        if not child.is_named:
            continue                     # `[`, `]`, `,` — punctuation, not an element.
        name = _ts_atom(child, src)
        if name is None:
            return None                  # one unreadable element, one unread list.
        names.add(name)
    return names


#: What a Phoenix router module `use`s. The value is the alias exactly as the
#: grammar reports it, because the predicate below compares an `alias` node's
#: own text and never a pattern over the surrounding line.
_PHOENIX_ROUTER_ALIAS = "Phoenix.Router"


def _elixir_declares_phoenix_router(raw: bytes) -> bool:
    """True iff the file `use`s `Phoenix.Router` in a MODULE BODY.

    "Module body" is the whole predicate: the `use` call's parent is a
    `do_block` whose own parent is a `defmodule` call. Nothing else qualifies,
    and one file is what that excludes.

    The measured defect it closes. `lib/<app>_web.ex` — the Phoenix entrypoint
    every conventional application has — carries `use Phoenix.Router` inside
    `def router do quote do … end end`. The previous collector matched the text
    `use Phoenix.Router` anywhere in the file, so the entrypoint became a router
    candidate; its POSIX path sorts AHEAD of `lib/<app>_web/router.ex` because
    `.` is 0x2E and `/` is 0x2F; and the single-primary rule then selected a
    file that declares no route at all. Both corpus Phoenix applications
    returned zero routes for exactly this reason.

    The walk is an explicit stack rather than recursion. A crafted file of
    deeply nested blocks would raise `RecursionError` from a recursive walk,
    and that exception escapes every fail-closed net in this module — the
    failure class ADR-0069's `scan_interp` shipped and the security review
    caught.
    """
    root = _elixir_parse(raw).root_node
    stack = [(root, None, None)]
    while stack:
        node, parent, grandparent = stack.pop()
        if (parent is not None and parent.type == "do_block"
                and grandparent is not None
                and _ts_call_name(grandparent, raw) == "defmodule"
                and _ts_call_name(node, raw) == "use"):
            for arg in _ts_args(node):
                if _ts_alias(arg, raw) == _PHOENIX_ROUTER_ALIAS:
                    return True
        for child in node.children:
            stack.append((child, node, parent))
    return False


def _elixir_router_files(root: Path) -> list:
    """The Phoenix router files: `**/router.ex` plus any `.ex` whose MODULE BODY
    `use`s `Phoenix.Router` (ADR-0069 point 1, ADR-0096 clause 1).
    POSIX-path sorted; the first is primary.

    The glob half and the POSIX-sorted single-primary rule are unchanged. Only
    the second half moved, from a pattern over the file's text to the parse-tree
    predicate above — and the single-primary rule stops being wrong once the
    entrypoint is no longer a candidate.
    """
    root = Path(root)
    found: list = []
    seen: set = set()
    for p in sorted(root.rglob("router.ex")):
        rel = p.relative_to(root).parts
        if any(part in _SKIP_DIRS or part.startswith(".") for part in rel):
            continue
        if _contained(root, p) and p.resolve() not in seen:
            found.append(p)
            seen.add(p.resolve())
    n_files = n_bytes = 0
    for p in _walk_ex_files(root):
        if n_files >= _MAX_SCAN_FILES or n_bytes >= _MAX_SCAN_BYTES:
            break
        raw = _safe_read_bytes(root, p, [])
        if raw is None:
            continue
        n_files += 1
        n_bytes += len(raw)
        if p.resolve() in seen:
            continue
        # A byte-containment prefilter, and it can change no answer: the
        # predicate fires only on an `alias` node whose own text is exactly
        # `Phoenix.Router`, so a file whose bytes do not contain that sequence
        # cannot satisfy it. It exists so a repository of several thousand `.ex`
        # files is not fully parsed to find one router.
        if _PHOENIX_ROUTER_ALIAS.encode() not in raw:
            continue
        if _elixir_declares_phoenix_router(raw):
            found.append(p)
            seen.add(p.resolve())
    return sorted(found, key=lambda p: _rel(root, p))


def _detect_elixir_router(root: Path) -> bool:
    return bool(_elixir_router_files(Path(root)))


#: The HTTP verb macros the Phoenix router DSL defines. A closed set read off
#: the call's own identifier, so `get` the route macro and `get` the map access
#: are told apart by node type rather than by a line pattern.
_ELIXIR_ROUTE_VERBS = frozenset(
    ("get", "post", "put", "patch", "delete", "options", "head"))

#: Still a regex, and deliberately so: `_gather_ecto_block` (the data-model
#: reader) and nothing on the router path uses it. The data-model concern's
#: reader is unchanged by this unit.
_ELIXIR_OPENS_BLOCK = re.compile(r'\bdo\s*$')

_PHOENIX_ACTIONS = [
    ("index", "GET", ""),
    ("new", "GET", "/new"),
    ("create", "POST", ""),
    ("show", "GET", "/:id"),
    ("edit", "GET", "/:id/edit"),
    ("update", "PATCH", "/:id"),
    ("update", "PUT", "/:id"),
    ("delete", "DELETE", "/:id"),
]

_ELIXIR_ROUTES_UNDER_COUNT = (
    "Best-effort static parse: `forward`, `resources` path-restructuring options "
    "(`param:`, `singleton:`, `member`/`collection` blocks), unrecognized `scope` "
    "forms, dynamic scope aliases, and metaprogrammed/macro-defined routes are "
    "not expanded (named residuals)."
)


def _elixir_opens_block(line: str) -> bool:
    return bool(_ELIXIR_OPENS_BLOCK.search(line))


def _expand_elixir_resource(rpath, ctrl, path_prefix, alias_prefix, only, excpt) -> list:
    """Expand a Phoenix `resources` declaration into canonical REST rows, honoring
    `only:`/`except:` on Phoenix's action set (ADR-0069 point 1). `update` yields
    both a PATCH and a PUT row; the action token is `:delete` (not `:destroy`).

    `only`/`excpt` are tri-state, tested with `is not None` rather than by truth,
    because an EMPTY filter is not an absent one: `None` is no filter or an
    unread one (full action set renders, and the caller names the residual),
    `set()` is `only: []` and renders zero rows, and a populated set filters.
    `except: []` excludes nothing and still renders all."""
    base = _norm_path("/".join([*path_prefix, rpath.strip("/")]))
    controller = ".".join([*alias_prefix, ctrl])
    rows = []
    for action, method, suffix in _PHOENIX_ACTIONS:
        if only is not None and action not in only:
            continue
        if excpt is not None and action in excpt:
            continue
        rows.append((method, _norm_path(base + suffix), f"{controller}#{action}"))
    return rows


def _parse_elixir_router(raw: bytes) -> tuple:
    """Parse a Phoenix `router.ex` into (rows, residuals, pipelines).

    A walk of the parse tree (ADR-0096 clause 1), not a line scan. A `scope`
    call pushes a `(path, alias)` frame bounded by ITS OWN `do_block`, so the
    frame's extent is a structural fact rather than a `do`/`end` counter's
    guess; a verb call emits a row; `resources` feeds `_expand_elixir_resource`
    unchanged; `forward` stays a named residual. The DSL is never executed.

    What the tree gives that the line scan could not. The two spellings a
    formatter chooses between — `get "/x", C, :a` and `get("/x", C, :a)` — are
    one `call` node with one argument list, so the reader cannot see a
    difference. `resources "/x", C, only: [:index]` written across three lines
    is one node too, where the line scan saw one unmatched line. A route inside
    a comment, a string, a heredoc or a sigil is not a `call` node at all,
    which is why this path no longer runs `_scrub_elixir_comments` — the
    grammar already knows what is code. (The data-model and module-graph
    readers still call the scrub, and it is unchanged.)

    Both walks are explicit stacks. A recursive walk raises `RecursionError` on
    a deeply nested file, and that exception escapes every fail-closed handler
    in this module — the class ADR-0069's `scan_interp` shipped.
    """
    tree = _elixir_parse(raw)
    rows: list = []
    residuals: list = []
    pipelines: set = set()
    saw_forward = saw_restructure = saw_member = saw_bad_scope = False
    saw_dynamic_path = saw_unresolved_handler = saw_unread_filter = False

    def _row(method: str, path_seg: str, prefixes: tuple, args: list):
        """Render one verb/live declaration, or report why it could not be."""
        nonlocal saw_dynamic_path, saw_unresolved_handler
        path_prefix, alias_prefix = prefixes
        if path_seg is None:
            saw_dynamic_path = True
            return
        handler = _ts_alias(args[1], raw) if len(args) > 1 else None
        if handler is None:
            saw_unresolved_handler = True
            return
        action = _ts_atom(args[2], raw) if len(args) > 2 else None
        full = _norm_path("/".join([*path_prefix, path_seg.strip("/")]))
        controller = ".".join([*alias_prefix, handler])
        if action:
            controller += "#" + action
        rows.append((method, full, controller))

    # (node, path_prefix, alias_prefix). Popped rather than recursed; see above.
    stack = [(tree.root_node, (), ())]
    while stack:
        node, path_prefix, alias_prefix = stack.pop()
        if node.type != "call":
            for child in node.children:
                stack.append((child, path_prefix, alias_prefix))
            continue

        name = _ts_call_name(node, raw)
        args = _ts_args(node)
        body = _ts_do_block(node)
        inner_path, inner_alias = path_prefix, alias_prefix

        if name == "scope":
            seg = _ts_string(args[0], raw) if args else None
            if seg is None:
                # A keyword-form `scope path: "/x"`, or an interpolated path.
                # Not framed, and said so — the same residual the line scan
                # named, reached from the argument nodes instead of a pattern.
                saw_bad_scope = True
            else:
                alias = _ts_alias(args[1], raw) if len(args) > 1 else None
                inner_path = (*path_prefix, seg.strip("/")) if seg.strip("/") else path_prefix
                inner_alias = (*alias_prefix, alias) if alias else alias_prefix
        elif name == "pipe_through":
            for arg in args:
                # Tri-state: `pipe_through @pipelines` reads as None, which is
                # "no pipeline label learned here" rather than a set to merge.
                # Pipeline labels are a per-scope annotation and not a row, so
                # this contributes no residual of its own.
                pipelines.update(_ts_atom_set(arg, raw) or ())
        elif name == "forward":
            saw_forward = True
        elif name == "resources":
            seg = _ts_string(args[0], raw) if args else None
            handler = _ts_alias(args[1], raw) if len(args) > 1 else None
            options = _ts_keywords(args, raw)
            if seg is None:
                saw_dynamic_path = True
            elif handler is None:
                saw_unresolved_handler = True
            elif "param" in options or "singleton" in options:
                saw_restructure = True     # path-restructuring — not expanded.
            else:
                # Key presence decides "no filter" (None) from `only: []` (an
                # empty set, which declares zero actions); the helper never
                # sees an absent option, so its `None` means exactly one thing.
                only = _ts_atom_set(options["only"], raw) if "only" in options else None
                excpt = (_ts_atom_set(options["except"], raw)
                         if "except" in options else None)
                if ("only" in options and only is None) or (
                        "except" in options and excpt is None):
                    saw_unread_filter = True
                rows += _expand_elixir_resource(
                    seg, handler, list(path_prefix), list(alias_prefix),
                    only, excpt)
            if seg is not None:
                nested = seg.strip("/")
                param = _singularize(nested.split("/")[-1]) + "_id"
                inner_path = (*path_prefix, nested + "/:" + param)
        elif name == "live":
            _row("LIVE", _ts_string(args[0], raw) if args else None,
                 (path_prefix, alias_prefix), args)
        elif name in _ELIXIR_ROUTE_VERBS:
            _row(name.upper(), _ts_string(args[0], raw) if args else None,
                 (path_prefix, alias_prefix), args)
        elif name in ("member", "collection"):
            saw_member = True

        if body is not None:
            stack.append((body, inner_path, inner_alias))

    if saw_forward:
        residuals.append("A `forward` route was found and not expanded (a named "
                         "residual).")
    if saw_restructure:
        residuals.append("A `resources` with `param:`/`singleton:` restructures "
                         "its paths and was not expanded (a named residual).")
    if saw_member:
        residuals.append("A `member`/`collection` block was found and not "
                         "expanded (a named residual).")
    if saw_bad_scope:
        residuals.append("An unrecognized `scope` form (e.g. keyword `path:`/"
                         "`alias:`) was not framed (a named residual).")
    if saw_unread_filter:
        residuals.append("An `only:`/`except:` action list that is not a literal "
                         "atom or list — a module attribute, a function call, or "
                         "a list carrying an unreadable element — could not be "
                         "read, so EVERY REST action was rendered for that "
                         "declaration and the rows above over-claim (a named "
                         "residual).")
    if saw_dynamic_path:
        residuals.append("A route path composed at compile time (an interpolated "
                         "string) has no static value and was not rendered (a "
                         "named residual).")
    if saw_unresolved_handler:
        residuals.append("A route whose handler is not a module alias was not "
                         "rendered (a named residual).")
    if tree.root_node.has_error:
        residuals.append("The Elixir grammar reported a syntax error in this "
                         "router; declarations inside the unparsed region were "
                         "not read (a named residual).")
    return rows, residuals, pipelines


def _elixir_unmerged_routers(root: Path, files: list):
    """The named residual for every router the single-primary rule discarded.

    Lifted out of the render path because it must also reach the STUB path, and
    it did not. When the primary produced no rows the whole file degraded to
    the empty-but-valid stub, and a router that had been found and dropped was
    never mentioned — so a reader could not tell "this repository declares no
    routes" from "the routes are in a file we discarded". That is the baseline's
    unnamed residual, and this is where it is closed.
    """
    if len(files) <= 1:
        return None
    return ("Additional Phoenix routers not merged (single-primary posture): "
            + ", ".join(f"`{_cell(_rel(root, p))}`" for p in files[1:]) + ".")


def extract_elixir_api_surface_router(root: Path, docs_dir: str) -> tuple[str, dict]:
    """API surface from a static Phoenix `router.ex` parse (ADR-0069 point 1,
    ADR-0096 clause 1). Multiple routers: the POSIX-sorted-first is primary,
    the rest a residual on BOTH the render path and the stub path. Rows sorted
    by (path, method, controller); all cells escaped.

    The `_ElixirScrubBail` branch is gone from this path with the scrub that
    raised it. tree-sitter has no unparseable input: a malformed file yields a
    tree carrying `ERROR` nodes, which `_parse_elixir_router` reports as a named
    residual over whatever it did read. That is the same fail-closed posture in
    a different shape — degrade one file and say so, never a truncated parse
    presented as complete.
    """
    root = Path(root)
    files = _elixir_router_files(root)
    if not files:
        return _canon(["# API surface", "", NO_EXTRACTOR.rstrip("\n")]), {}
    primary = files[0]
    raw = _safe_read_bytes(root, primary, [])
    if raw is None:
        return _canon(["# API surface", "", NO_EXTRACTOR.rstrip("\n")]), {}
    sources = {_rel(root, primary): _sha256_hex(raw)}
    rows, residuals, pipelines = _parse_elixir_router(raw)
    unmerged = _elixir_unmerged_routers(root, files)
    if not rows:
        out = ["# API surface", "", NO_EXTRACTOR.rstrip("\n")]
        if unmerged:
            out += ["", "## Residuals", "", f"- {unmerged}"]
        return _canon(out), sources
    out = ["# API surface", "",
           f"_Static parse of the Phoenix router `{_cell(_rel(root, primary))}`; the "
           "DSL is not executed._", ""]
    if pipelines:
        # "labels", not "annotations". `annotation` is reserved vocabulary in
        # this tree: clause 2 gives it to the REPORTED channel, and
        # `test_the_annotation_vocabulary_appears_nowhere_under_arch` scans every
        # committed arch file for the token to prove no annotation reached the
        # byte-compared set. This line predates that guard (ADR-0069) and never
        # collided with it, because no corpus Phoenix router was ever selected
        # and so the line never rendered. Fixing router selection rendered it,
        # and the guard fired on a word used here for an unrelated thing — a
        # Phoenix pipeline. The guard is right and the word was wrong.
        out += ["_Pipelines (per-scope labels, not columns): "
                + ", ".join(f"`:{_cell(p)}`" for p in sorted(pipelines))
                + "._", ""]
    out += [f"## Routes ({len(rows)})", "",
            "| method | path | controller |", "|---|---|---|"]
    for method, path, controller in sorted(rows, key=lambda r: (r[1], r[0], r[2])):
        out.append(f"| {_cell(method)} | `{_cell(path)}` | `{_cell(controller)}` |")
    out.append("")
    if unmerged:
        residuals.append(unmerged)
    out += ["## Residuals", "", f"- {_ELIXIR_ROUTES_UNDER_COUNT}"]
    for r in residuals:
        out.append(f"- {r}")
    out.append("")
    return _canon(out), sources


# ── data-model: Ecto schemas primary, migrations a presence-flag stub ─────────

_ELIXIR_HAS_SCHEMA_RE = re.compile(r'\bschema\s+"')
_ECTO_SCHEMA_RE = re.compile(r'^schema\s+"([^"]*)"\s+do\b')
_ECTO_EMBEDDED_SCHEMA_RE = re.compile(r'^embedded_schema\s+do\b')
_ECTO_FIELD_RE = re.compile(r'^field\s+:([A-Za-z_]\w*)\s*,\s*(.+?)\s*$')
_ECTO_BELONGS_TO_RE = re.compile(
    r'^belongs_to\s+:([A-Za-z_]\w*)\s*,\s*([A-Z][A-Za-z0-9_.]*)\s*(.*)$')
_ECTO_HAS_RE = re.compile(
    r'^(has_many|has_one)\s+:([A-Za-z_]\w*)\s*,\s*([A-Z][A-Za-z0-9_.]*)')
_ECTO_M2M_RE = re.compile(
    r'^many_to_many\s+:([A-Za-z_]\w*)\s*,\s*([A-Z][A-Za-z0-9_.]*)\s*(.*)$')
_ECTO_EMBEDS_RE = re.compile(
    r'^(embeds_one|embeds_many)\s+:([A-Za-z_]\w*)\s*,\s*([A-Z][A-Za-z0-9_.]*)')
_PRIMARY_KEY_FALSE_RE = re.compile(r'^@primary_key\s+false\b')
_PRIMARY_KEY_TUPLE_RE = re.compile(
    r'^@primary_key\s+\{\s*:([A-Za-z_]\w*)\s*,\s*:?([A-Za-z_][A-Za-z0-9_.]*)')
_FOREIGN_KEY_TYPE_RE = re.compile(r'^@foreign_key_type\s+:?([A-Za-z_][A-Za-z0-9_.]*)')

_ELIXIR_NULL_RESIDUAL = (
    "An Ecto schema cannot express `null` or indexes — NOT-NULL constraints and "
    "DB indexes are declared in `priv/repo/migrations/`, not the schema. So "
    "`null` is `—` for every non-primary-key column, including a `belongs_to` FK "
    "column, whose required-ness is a migration constraint."
)


def _ecto_type_render(typ_expr: str) -> str:
    t = typ_expr.strip()
    return t[1:] if t.startswith(":") else t


def _ecto_default(opts: str) -> str:
    m = re.search(
        r'default:\s*("(?:[^"\\]|\\.)*"|:[A-Za-z_]\w*|\{[^}]*\}|\[[^\]]*\]|[^,\n]+)',
        opts)
    if not m:
        return "—"
    v = m.group(1).strip()
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        v = v[1:-1]
    if v.startswith(":"):
        v = v[1:]
    return v or "—"


def _ecto_enum_values(opts: str) -> list:
    m = re.search(r'values:\s*\[([^\]]*)\]', opts)
    if not m:
        return []
    return re.findall(r':?([A-Za-z_]\w*)', m.group(1))


def _gather_ecto_block(lines: list, i: int) -> tuple:
    """From a `schema/embedded_schema … do` line at index i, collect the block
    body until the matching `end` (do/end depth). Returns (body_lines, next_i)."""
    depth = 1
    body: list = []
    j = i + 1
    while j < len(lines) and depth > 0:
        s = lines[j].strip()
        if s == "end" or s.startswith("end "):
            depth -= 1
            j += 1
            if depth == 0:
                break
            body.append(s)
            continue
        body.append(s)
        if _elixir_opens_block(s):
            depth += 1
        j += 1
    return body, j


def _build_ecto_schema(table, body: list, pending_pk, pending_fkt) -> dict:
    """Turn one schema block's body into a structured schema dict (ADR-0069
    point 2). `null` is `no` only for primary-key columns (the fixed residual)."""
    columns: list = []        # (col, type, null, default, fk)
    relations: list = []      # (name, target, kind)
    enums: list = []          # (col, [values])
    embeds: list = []         # (name, target, kind)
    join_residuals: list = []
    timestamps_residual = False
    has_explicit_pk = False
    for s in body:
        st = s.strip()
        if st == "timestamps()" or st == "timestamps":
            columns.append(("inserted_at", "naive_datetime", "—", "—", "—"))
            columns.append(("updated_at", "naive_datetime", "—", "—", "—"))
            continue
        if re.match(r'^timestamps\s*\(.+\)', st):
            timestamps_residual = True
            continue
        bm = _ECTO_BELONGS_TO_RE.match(st)
        if bm:
            assoc, target, opts = bm.group(1), bm.group(2), bm.group(3)
            if re.search(r'define_field:\s*false', opts):
                relations.append((assoc, target, "belongs_to"))
                continue
            fkm = re.search(r'foreign_key:\s*:([A-Za-z_]\w*)', opts)
            fk_col = fkm.group(1) if fkm else assoc + "_id"
            tym = re.search(r'type:\s*:?([A-Za-z_][A-Za-z0-9_.]*)', opts)
            typ = tym.group(1) if tym else (pending_fkt or "id")
            columns.append((fk_col, typ, "—", "—", target))
            continue
        hm = _ECTO_HAS_RE.match(st)
        if hm:
            relations.append((hm.group(2), hm.group(3), hm.group(1)))
            continue
        mm = _ECTO_M2M_RE.match(st)
        if mm:
            name, target, opts = mm.group(1), mm.group(2), mm.group(3)
            relations.append((name, target, "many_to_many"))
            jt = re.search(r'join_through:\s*"([^"]*)"', opts)
            if jt:
                join_residuals.append((name, jt.group(1)))
            continue
        emb = _ECTO_EMBEDS_RE.match(st)
        if emb:
            embeds.append((emb.group(2), emb.group(3), emb.group(1)))
            continue
        fm = _ECTO_FIELD_RE.match(st)
        if fm:
            name = fm.group(1)
            parts = _split_top_level(fm.group(2))
            typ_expr = parts[0].strip() if parts else ":"
            opts = ",".join(parts[1:])
            is_pk = bool(re.search(r'primary_key:\s*true', opts))
            if is_pk:
                has_explicit_pk = True
            default = _ecto_default(opts)
            if re.match(r'Ecto\.Enum\b', typ_expr):
                enums.append((name, _ecto_enum_values(opts)))
                typ = "Ecto.Enum"
            else:
                typ = _ecto_type_render(typ_expr)
            columns.append((name, typ, "no" if is_pk else "—", default, "—"))
            continue
    if table is not None and not has_explicit_pk:
        if pending_pk == "false":
            pass
        elif isinstance(pending_pk, tuple):
            columns.insert(0, (pending_pk[0], _ecto_type_render(":" + pending_pk[1]),
                               "no", "—", "—"))
        else:
            columns.insert(0, ("id", "id", "no", "—", "—"))
    return {
        "table": table, "columns": columns, "relations": relations,
        "enums": enums, "embeds": embeds, "join_residuals": join_residuals,
        "timestamps_residual": timestamps_residual,
    }


def _parse_elixir_schemas(text: str) -> list:
    """Parse every `schema "t" do … end` / `embedded_schema do … end` in a file
    (ADR-0069 point 2). Tracks the nearest `@primary_key`/`@foreign_key_type`
    per module. Raises `_ElixirScrubBail` on an unparseable comment/sigil scrub."""
    text = _scrub_elixir_comments(text)
    lines = text.split("\n")
    schemas: list = []
    pending_pk = "default"
    pending_fkt = None
    i = 0
    n = len(lines)
    while i < n:
        s = lines[i].strip()
        if s.startswith("defmodule"):
            pending_pk, pending_fkt = "default", None
            i += 1
            continue
        if _PRIMARY_KEY_FALSE_RE.match(s):
            pending_pk = "false"
            i += 1
            continue
        pk = _PRIMARY_KEY_TUPLE_RE.match(s)
        if pk:
            pending_pk = (pk.group(1), pk.group(2))
            i += 1
            continue
        fkt = _FOREIGN_KEY_TYPE_RE.match(s)
        if fkt:
            pending_fkt = fkt.group(1)
            i += 1
            continue
        sm = _ECTO_SCHEMA_RE.match(s)
        em = _ECTO_EMBEDDED_SCHEMA_RE.match(s)
        if sm or em:
            body, i = _gather_ecto_block(lines, i)
            schemas.append(_build_ecto_schema(
                sm.group(1) if sm else None, body, pending_pk, pending_fkt))
            pending_pk, pending_fkt = "default", None
            continue
        i += 1
    return schemas


def _detect_elixir_schema(root: Path) -> bool:
    return _elixir_scan_contains(Path(root), _ELIXIR_HAS_SCHEMA_RE)


def extract_elixir_data_model(root: Path, docs_dir: str) -> tuple[str, dict]:
    """Data model from Ecto schemas walked across every `.ex` (umbrella-covering,
    ADR-0069 point 2). Six-column entity table; belongs_to → FK column; has_*/
    many_to_many → a Relations note; `Ecto.Enum` → an Enums note; `null` is `—`
    for every non-PK column (the fixed residual). Sorted by (table, field)."""
    root = Path(root)
    entries, sources, scan_residuals = _scan_ex_files(root)
    table_schemas: list = []
    embedded_count = 0
    bail_residuals: list = []
    for rel, text in entries:
        try:
            schemas = _parse_elixir_schemas(text)
        except (_ElixirScrubBail, RecursionError):
            bail_residuals.append(rel)
            continue
        for sch in schemas:
            if sch["table"] is None:
                embedded_count += 1
            else:
                table_schemas.append(sch)
    if not table_schemas and embedded_count == 0 and not bail_residuals:
        return _canon(["# Data model", "", NO_EXTRACTOR.rstrip("\n")]), sources

    out = ["# Data model", "",
           "_Derived from Ecto schemas (static parse; no Elixir executed)._", ""]
    rows: list = []
    for sch in table_schemas:
        for (col, typ, null, default, fk) in sch["columns"]:
            rows.append((sch["table"], col, typ, null, default, fk))
    rows.sort(key=lambda r: (r[0], r[1]))
    if rows:
        tables = sorted({r[0] for r in rows})
        out += [f"## Entities ({len(tables)} tables)", "",
                "| table | column | type | null | default | fk |",
                "|---|---|---|---|---|---|"]
        for table, col, typ, null, default, fk in rows:
            out.append(f"| {_cell(table)} | `{_cell(col)}` | {_cell(typ)} | {null} "
                       f"| {_cell(default)} | {_cell(fk)} |")
        out.append("")

    rel_lines = []
    for sch in sorted(table_schemas, key=lambda x: x["table"]):
        if sch["relations"]:
            joined = ", ".join(f"`{_cell(nm)}` → `{_cell(t)}` ({k})"
                               for nm, t, k in sorted(sch["relations"]))
            rel_lines.append(f"- `{_cell(sch['table'])}`: {joined}")
    if rel_lines:
        out += ["## Relations", "", "_Associations (not columns):_", ""] + rel_lines + [""]

    enum_lines = []
    for sch in sorted(table_schemas, key=lambda x: x["table"]):
        for col, vals in sch["enums"]:
            joined = ", ".join(f"`{_cell(v)}`" for v in vals)
            enum_lines.append(
                f"- `{_cell(sch['table'])}`.`{_cell(col)}`: {joined}" if vals
                else f"- `{_cell(sch['table'])}`.`{_cell(col)}`")
    if enum_lines:
        out += ["## Enums", "", "_`Ecto.Enum` columns:_", ""] + enum_lines + [""]

    emb_lines = []
    for sch in table_schemas:
        for (name, target, kind) in sch["embeds"]:
            emb_lines.append(f"- `{_cell(sch['table'])}`.`{_cell(name)}` → "
                             f"`{_cell(target)}` ({kind})")
    if embedded_count:
        emb_lines.append(f"- {embedded_count} `embedded_schema` module(s) — no DB "
                         "table, not rendered as entities.")
    if emb_lines:
        out += ["## Embedded schemas", "", "_Not database tables:_", ""] \
            + sorted(emb_lines) + [""]

    res = [_ELIXIR_NULL_RESIDUAL]
    for sch in table_schemas:
        for (name, tbl) in sch["join_residuals"]:
            res.append(f"`many_to_many :{_cell(name)}` uses "
                       f"`join_through: \"{_cell(tbl)}\"` — a schema-less join "
                       "table, not rendered (a named residual).")
    if any(sch["timestamps_residual"] for sch in table_schemas):
        res.append("A non-bare `timestamps(...)` call was not expanded to "
                   "inserted_at/updated_at (a named residual).")
    if bail_residuals:
        res.append("Files whose comment/sigil/heredoc scrub bailed (hashed, not "
                   "parsed): "
                   + ", ".join(f"`{_cell(r)}`" for r in sorted(bail_residuals)) + ".")
    res += scan_residuals
    out += ["## Residuals", ""] + [f"- {r}" for r in res] + [""]
    return _canon(out), sources


def _elixir_migrations_dir(root: Path):
    d = Path(root) / "priv" / "repo" / "migrations"
    return d if d.is_dir() else None


def _detect_elixir_migrations(root: Path) -> bool:
    d = _elixir_migrations_dir(Path(root))
    return d is not None and any(d.glob("*.exs"))


# ── the Ecto migration reader (U-E2) ─────────────────────────────────────────
# What replaced the presence stub. The stub globbed `priv/repo/migrations/*.exs`,
# hashed each file, and rendered one sentence saying a parse was deferred — so a
# repository whose only data-model surface is its migrations rendered ZERO
# entities and took a `no_entities` verdict over a full schema.
#
# This reader replays the migrations instead. It is a tree-sitter walk for the
# same reason the router reader is one (ADR-0096 clause 1): the two spellings a
# formatter chooses between — `add :email, :string` and `add(:email, :string)` —
# are one `call` node with one argument list, and no pattern over the text sees
# that. The DSL is never executed.

#: The migration verbs that open a statement. `create_if_not_exists` and
#: `drop_if_exists` are the idempotent spellings and mean the same thing to a
#: reader that is describing the end state rather than performing it.
_ELIXIR_MIGRATION_CREATE = ("create", "create_if_not_exists")
_ELIXIR_MIGRATION_DROP = ("drop", "drop_if_exists")
_ELIXIR_MIGRATION_VERBS = _ELIXIR_MIGRATION_CREATE + _ELIXIR_MIGRATION_DROP + ("alter",)

#: The column verbs inside a `create table`/`alter table` block.
_ELIXIR_MIGRATION_ADD = ("add", "add_if_not_exists")
_ELIXIR_MIGRATION_REMOVE = ("remove", "remove_if_exists")

_ELIXIR_MIGRATION_RESIDUAL = (
    "Replayed statically in filename order — the order `mix ecto.migrate` runs "
    "them in. `execute/1` raw SQL, `rename`, `constraint`, and any operation "
    "guarded by a conditional or hidden in a helper function are not applied "
    "(named residuals). Only `change` and `up` are read; `down` is a rollback "
    "and would undo the state being described."
)


def _mig_bool(node, src: bytes):
    """`true`/`false` read off a keyword's value node, else None."""
    if node is None:
        return None
    text = src[node.start_byte:node.end_byte].decode("utf-8", errors="replace").strip()
    return {"true": True, "false": False}.get(text)


def _mig_default(node, src: bytes) -> str:
    """A `default:` value rendered as a cell, or `—`.

    An INTERPOLATED string renders `—` rather than its own source text, for the
    reason `_ts_string` gives: the interpolation has no static value, and
    printing the source as if it were one is the node pack's measured defect.
    Anything else — an integer, `fragment("now()")` — renders its source text,
    which is what the migration literally declares.
    """
    if node is None:
        return "—"
    if node.type == "string":
        text = _ts_string(node, src)
        return text if text else "—"
    atom = _ts_atom(node, src)
    if atom:
        return atom
    text = src[node.start_byte:node.end_byte].decode("utf-8", errors="replace").strip()
    return text or "—"


def _mig_type(node, src: bytes) -> tuple:
    """`(type, fk)` for an `add`'s type argument, or `(None, "—")` when unreadable.

    `references(:orgs)` is the FK form, and the referenced column is named
    explicitly (`orgs.id`) rather than left implicit, because the fk cell is the
    only place a reader learns which table the column points at.
    """
    atom = _ts_atom(node, src)
    if atom:
        return atom, "—"
    if node.type == "call" and _ts_call_name(node, src) == "references":
        args = _ts_args(node)
        target = _ts_atom(args[0], src) if args else None
        options = _ts_keywords(args, src)
        typ = _ts_atom(options.get("type"), src) if "type" in options else None
        col = _ts_atom(options.get("column"), src) if "column" in options else None
        if target is None:
            return (typ or "id"), "—"
        return (typ or "id"), f"{target}.{col or 'id'}"
    return None, "—"


def _mig_column_ops(block, src: bytes) -> tuple:
    """The `(ops, saw_unreadable)` a `create table`/`alter table` block declares.

    DIRECT children only, not a descent. Ecto does not nest column declarations,
    and a descent would read an `add` inside a nested block as if it applied to
    this table.
    """
    ops: list = []
    saw_unreadable = False
    if block is None:
        return ops, saw_unreadable
    for node in block.children:
        if node.type != "call":
            continue
        name = _ts_call_name(node, src)
        args = _ts_args(node)
        if name == "timestamps":
            # Ecto's migration `timestamps/1` emits both columns NOT NULL.
            for col in ("inserted_at", "updated_at"):
                ops.append(("add", col, "naive_datetime", "no", "—", "—"))
            continue
        if name in _ELIXIR_MIGRATION_REMOVE:
            col = _ts_atom(args[0], src) if args else None
            if col is None:
                saw_unreadable = True
            else:
                ops.append(("remove", col))
            continue
        if name not in _ELIXIR_MIGRATION_ADD and name != "modify":
            continue
        col = _ts_atom(args[0], src) if args else None
        if col is None or len(args) < 2:
            saw_unreadable = True
            continue
        typ, fk = _mig_type(args[1], src)
        if typ is None:
            saw_unreadable = True
            continue
        options = _ts_keywords(args, src)
        is_pk = _mig_bool(options.get("primary_key"), src) is True
        nullable = _mig_bool(options.get("null"), src)
        null = "no" if is_pk or nullable is False else "yes"
        ops.append(("add", col, typ, null,
                    _mig_default(options.get("default"), src), fk))
    return ops, saw_unreadable


def _parse_elixir_migration(raw: bytes) -> tuple:
    """One migration file → `(statements, saw_unreadable)`.

    The walk is an explicit stack, for the reason every walk in this module is:
    a recursive one raises `RecursionError` on a deeply nested file, and that
    exception escapes every fail-closed handler here.

    A `def down` body is not descended into. `mix ecto.migrate` runs `change` and
    `up`; replaying `down` as well would apply a create and then its own
    rollback, and render an empty tree for a repository that has the tables.
    """
    tree = _elixir_parse(raw)
    statements: list = []
    saw_unreadable = False
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type == "call":
            name = _ts_call_name(node, raw)
            args = _ts_args(node)
            if name in ("def", "defp") and args:
                head = args[0]
                fname = (raw[head.start_byte:head.end_byte]
                         .decode("utf-8", errors="replace").split("(")[0].strip())
                if fname == "down":
                    continue                      # a rollback, never replayed
            elif name in _ELIXIR_MIGRATION_VERBS and args:
                subject = args[0]
                if subject.type != "call":
                    saw_unreadable = True
                else:
                    saw_unreadable |= _mig_statement(
                        name, subject, node, raw, statements)
        for child in node.children:
            stack.append(child)
    return statements, saw_unreadable


def _mig_statement(verb: str, subject, node, raw: bytes, out: list) -> bool:
    """Append the statement `verb subject` declares. True if it was unreadable.

    The block is taken from the outer call OR from the subject, and that is the
    whole parenthesized-versus-space-delimited fix for this reader. The two
    spellings attach it to DIFFERENT nodes: `create table(:t) do … end` gives
    the block to `create`, while `create(table(:t) do … end)` gives it to
    `table`. Reading only one place would make the reader's answer depend on
    which spelling the formatter chose.
    """
    subject_name = _ts_call_name(subject, raw)
    subject_args = _ts_args(subject)
    block = _ts_do_block(node) or _ts_do_block(subject)
    if subject_name == "table":
        table = _ts_atom(subject_args[0], raw) if subject_args else None
        if table is None:
            return True
        if verb in _ELIXIR_MIGRATION_DROP:
            out.append(("drop", table))
            return False
        ops, unreadable = _mig_column_ops(block, raw)
        if verb == "alter":
            out.append(("alter", table, ops))
        else:
            options = _ts_keywords(subject_args, raw)
            implicit_pk = _mig_bool(options.get("primary_key"), raw) is not False
            out.append(("create", table, implicit_pk, ops))
        return unreadable
    if subject_name in ("index", "unique_index", "create_if_not_exists"):
        table = _ts_atom(subject_args[0], raw) if subject_args else None
        if table is None:
            return True
        columns = [name for name in
                   (_ts_atom(c, raw) for c in
                    (subject_args[1].children if len(subject_args) > 1
                     and subject_args[1].type == "list" else []))
                   if name]
        options = _ts_keywords(subject_args, raw)
        unique = (subject_name == "unique_index"
                  or _mig_bool(options.get("unique"), raw) is True)
        named = _ts_atom(options.get("name"), raw) if "name" in options else None
        if verb in _ELIXIR_MIGRATION_DROP:
            return False                          # dropping an index we may not have
        out.append(("index", table, columns, unique, named or "—"))
        return False
    return True                                   # constraint, execute, rename, …


def extract_elixir_migrations(root: Path, docs_dir: str) -> tuple[str, dict]:
    """Data model from `priv/repo/migrations/*.exs`, replayed in filename order.

    The SECOND data-model probe: it runs only where the repository declares no
    Ecto schema, which is why neither corpus Phoenix application reaches it.
    Filename order is the schedule `mix ecto.migrate` follows, so replaying it
    yields the current shape rather than the first one.

    What this reader has that the schema reader does not: `null` and indexes.
    Both are migration facts — `_ELIXIR_NULL_RESIDUAL` says so on the schema
    path, where they are unavailable — so the cells are filled here rather than
    dashed. Every file is hashed whether or not it declared anything, so adding
    a migration drifts the spine.
    """
    root = Path(root)
    d = _elixir_migrations_dir(root)
    if d is None:
        return _canon(["# Data model", "", NO_EXTRACTOR.rstrip("\n")]), {}
    sources: dict = {}
    statements: list = []
    unreadable_files: list = []
    error_files: list = []
    for p in sorted(d.glob("*.exs")):
        raw = _safe_read_bytes(root, p, [])
        if raw is None:
            continue
        rel = _rel(root, p)
        sources[rel] = _sha256_hex(raw)
        try:
            parsed, unreadable = _parse_elixir_migration(raw)
        except RecursionError:                    # belt and braces; the walk is flat
            error_files.append(rel)
            continue
        if _elixir_parse(raw).root_node.has_error:
            error_files.append(rel)
        if unreadable:
            unreadable_files.append(rel)
        statements += parsed
    if not sources:
        return _canon(["# Data model", "", NO_EXTRACTOR.rstrip("\n")]), {}

    # The replay. `tables` maps table → {column: (type, null, default, fk)};
    # insertion order is irrelevant because the rows are sorted before render.
    tables: dict = {}
    indexes: list = []
    for stmt in statements:
        if stmt[0] == "create":
            _, table, implicit_pk, ops = stmt
            cols: dict = {}
            if implicit_pk:
                cols["id"] = ("id", "no", "—", "—")
            tables[table] = cols
            _apply_migration_ops(cols, ops)
        elif stmt[0] == "alter":
            _apply_migration_ops(tables.setdefault(stmt[1], {}), stmt[2])
        elif stmt[0] == "drop":
            tables.pop(stmt[1], None)
            indexes = [i for i in indexes if i[0] != stmt[1]]
        elif stmt[0] == "index":
            indexes.append(stmt[1:])

    rows = sorted(
        (table, col, *rest)
        for table, cols in tables.items() for col, rest in cols.items()
    )
    if not rows:
        return _canon(["# Data model", "", NO_EXTRACTOR.rstrip("\n")]), sources

    out = ["# Data model", "",
           "_Derived from Ecto migrations in `priv/repo/migrations/` (static "
           "parse; no Elixir executed)._", ""]
    out += [f"## Entities ({len({r[0] for r in rows})} tables)", "",
            "| table | column | type | null | default | fk |",
            "|---|---|---|---|---|---|"]
    for table, col, typ, null, default, fk in rows:
        out.append(f"| {_cell(table)} | `{_cell(col)}` | {_cell(typ)} | {null} "
                   f"| {_cell(default)} | {_cell(fk)} |")
    out.append("")

    if indexes:
        # A DIFFERENT header from the entity table, deliberately. The entity
        # count reads a table by its first two header cells, so an index table
        # spelled `| table | column |` would be counted as entities.
        out += ["## Indexes", "", "| table | index | columns | unique |",
                "|---|---|---|---|"]
        for table, columns, unique, named in sorted(
                indexes, key=lambda i: (i[0], i[3], tuple(i[1]))):
            joined = ", ".join(f"`{_cell(c)}`" for c in columns) or "—"
            out.append(f"| {_cell(table)} | {_cell(named)} | {joined} "
                       f"| {'yes' if unique else 'no'} |")
        out.append("")

    residuals = [_ELIXIR_MIGRATION_RESIDUAL]
    if unreadable_files:
        residuals.append(
            "Declarations whose table, column or type is not a static atom were "
            "not applied (hashed, not read): "
            + ", ".join(f"`{_cell(r)}`" for r in sorted(set(unreadable_files))) + ".")
    if error_files:
        residuals.append(
            "The Elixir grammar reported a syntax error in these migrations; "
            "declarations inside the unparsed region were not read: "
            + ", ".join(f"`{_cell(r)}`" for r in sorted(set(error_files))) + ".")
    out += ["## Residuals", ""] + [f"- {r}" for r in residuals] + [""]
    return _canon(out), sources


def _apply_migration_ops(cols: dict, ops: list) -> None:
    """Apply one block's column operations to a table's column map, in order."""
    for op in ops:
        if op[0] == "remove":
            cols.pop(op[1], None)
        else:
            cols[op[1]] = tuple(op[2:])


# ── module-graph: defmodule index + alias-resolve, resolve-or-drop ────────────

_ELIXIR_DEFMODULE_RE = re.compile(r'defmodule\s+([A-Z][A-Za-z0-9_.]*)\s+do\b')
_ELIXIR_ALIAS_GROUP_RE = re.compile(r'^alias\s+([A-Z][A-Za-z0-9_.]*)\.\{([^}]*)\}')
_ELIXIR_ALIAS_RE = re.compile(
    r'^alias\s+([A-Z][A-Za-z0-9_.]*)(?:\s*,\s*as:\s*([A-Z][A-Za-z0-9_]*))?\s*$')
_ELIXIR_IMPORT_USE_RE = re.compile(r'^(import|use|require)\s+([A-Z][A-Za-z0-9_.]*)')
_ELIXIR_CAMEL_RE = re.compile(r'[A-Z][A-Za-z0-9_]*(?:\.[A-Z][A-Za-z0-9_]*)*')


def _elixir_alias_table(text: str) -> dict:
    """Per-file alias bindings resolved BEFORE resolve-or-drop (ADR-0069 point 3):
    `alias Foo.Bar` → Bar→Foo.Bar; `alias Foo.Bar, as: Baz` → Baz→Foo.Bar; a
    grouped `alias Foo.{Bar, Baz}` → Bar→Foo.Bar, Baz→Foo.Baz."""
    table: dict = {}
    for line in text.split("\n"):
        s = line.strip()
        g = _ELIXIR_ALIAS_GROUP_RE.match(s)
        if g:
            base = g.group(1)
            for child in g.group(2).split(","):
                cm = re.match(r'([A-Z][A-Za-z0-9_]*)', child.strip())
                if cm:
                    table[cm.group(1)] = base + "." + cm.group(1)
            continue
        a = _ELIXIR_ALIAS_RE.match(s)
        if a:
            target, as_name = a.group(1), a.group(2)
            key = as_name if as_name else target.split(".")[-1]
            table[key] = target
    return table


def _elixir_refs(text: str, alias_table: dict) -> set:
    """The set of full module names referenced by a file: alias/import/use/require
    directive targets, plus CamelCase remote refs rewritten through the alias
    table (ADR-0069 point 3). Resolve-or-drop is applied by the caller."""
    refs: set = set()
    for line in text.split("\n"):
        s = line.strip()
        g = _ELIXIR_ALIAS_GROUP_RE.match(s)
        if g:
            base = g.group(1)
            for child in g.group(2).split(","):
                cm = re.match(r'([A-Z][A-Za-z0-9_]*)', child.strip())
                if cm:
                    refs.add(base + "." + cm.group(1))
            continue
        a = _ELIXIR_ALIAS_RE.match(s)
        if a:
            refs.add(a.group(1))
            continue
        iu = _ELIXIR_IMPORT_USE_RE.match(s)
        if iu:
            refs.add(iu.group(2))
    for m in _ELIXIR_CAMEL_RE.finditer(text):
        tok = m.group(0)
        head, _, rest = tok.partition(".")
        if head in alias_table:
            refs.add(alias_table[head] + (("." + rest) if rest else ""))
        else:
            refs.add(tok)
    return refs


def _detect_elixir_module_graph(root: Path) -> bool:
    return _has_ex_sources(root)


def extract_elixir_module_graph(root: Path, docs_dir: str) -> tuple[str, dict]:
    """Elixir dependency graph over every in-repo `.ex` (ADR-0069 point 3). Nodes
    are `defmodule` module names; an edge A→B is emitted only when an alias/import/
    use/remote-ref in A resolves (via the file's alias table) to an in-repo module
    B — deps/stdlib drop (RESOLVE-OR-DROP). A file with multiple `defmodule`s
    attributes edges to the first (a nested-defmodule residual). Every scanned
    `.ex` is hashed; the render is bounded by `_MAX_GRAPH_EDGES`."""
    root = Path(root)
    entries, sources, scan_residuals = _scan_ex_files(root)
    if not entries:
        return _canon(["# Module graph", "", NO_EXTRACTOR.rstrip("\n")]), {}

    index: set = set()
    parsed: list = []          # (rel, scrubbed, modules)
    bail_residuals: list = []
    for rel, text in entries:
        try:
            scrubbed = _scrub_elixir_comments(text, blank_strings=True)
        except (_ElixirScrubBail, RecursionError):
            bail_residuals.append(rel)
            continue
        mods = _ELIXIR_DEFMODULE_RE.findall(scrubbed)
        for mm in mods:
            index.add(mm)
        parsed.append((rel, scrubbed, mods))

    edges: set = set()
    nested_files: list = []
    for rel, scrubbed, mods in parsed:
        if not mods:
            continue
        owner = mods[0]
        if len(mods) > 1:
            nested_files.append(rel)
        alias_table = _elixir_alias_table(scrubbed)
        for ref in _elixir_refs(scrubbed, alias_table):
            if ref in index and ref != owner:
                edges.add((owner, ref))

    graph_residuals: list = []
    all_edges = sorted(edges)
    if len(all_edges) > _MAX_GRAPH_EDGES:
        graph_residuals.append(
            f"module graph truncated: {len(all_edges)} edges exceed the "
            f"{_MAX_GRAPH_EDGES}-edge render bound")
        all_edges = all_edges[:_MAX_GRAPH_EDGES]
    edges = set(all_edges)

    node_ids = _node_ids(sorted(index))
    out = ["# Module graph", "",
           f"_Elixir module dependency graph of {len(index)} modules, "
           f"{len(edges)} edges (alias/import/use + remote refs, resolve-or-drop; "
           "static parse, no Elixir executed)._", "", "```mermaid", "graph LR"]
    for a, b in sorted(edges):
        out.append(f'  {node_ids[a]}["{_mermaid(a)}"] --> {node_ids[b]}["{_mermaid(b)}"]')
    out += ["```", ""]
    if not edges:
        out += ["_No resolved module edges detected._", ""]
    connected = {a for a, _ in edges} | {b for _, b in edges}
    isolated = sorted(m for m in index if m not in connected)
    if isolated:
        out += [f"## Isolated modules ({len(isolated)})", "",
                "_No resolved dependency edge (leaf or standalone):_", "",
                ", ".join(f"`{_cell(m)}`" for m in isolated), ""]
    notes = list(scan_residuals)
    if nested_files:
        notes.append("Nested `defmodule` definitions attributed to the enclosing "
                     "module (a named residual): "
                     + ", ".join(f"`{_cell(r)}`" for r in sorted(nested_files)) + ".")
    if bail_residuals:
        notes.append("Files whose comment/sigil/heredoc scrub bailed (hashed, not "
                     "parsed): "
                     + ", ".join(f"`{_cell(r)}`" for r in sorted(bail_residuals)) + ".")
    notes += graph_residuals
    if notes:
        out += ["## Residuals", ""] + [f"- {n}" for n in notes] + [""]
    return _canon(out), sources

#: Empty: the core's marker scan prunes no directory from this pack's own
#: `detect` calls, so this pack's marker semantics are unchanged.
DETECT_EXCLUDE: frozenset[str] = frozenset()


def detect(root: Path) -> DetectResult:
    """The elixir stack detector (ADR-0096 clause 6/12).

    `matched` is exactly what the pre-split marker predicate `_detect_elixir`
    returned — `(root / "mix.exs").exists()`; `markers` names the repo-relative
    marker paths that fired, sorted, so an ambiguous multi-match verdict can cite
    them. Empty when nothing fired.
    """
    markers = tuple(sorted(m for m in ("mix.exs",) if (Path(root) / m).exists()))
    return DetectResult(matched=bool(markers), markers=markers)


# The elixir pack's declared input class per concern (ADR-0097 part 1). `kind`
# lives on each probe below (`probes()`), not in this map.
# `decision-index` is the universal concern the core declares and binds.
#
# **api-surface is closed; module-graph is the remaining gap, and data-model is
# half.** The api-surface router reader is a tree-sitter parse: which file is
# the router, and what it declares, are both parse-tree questions now, and the
# two measured defects the baseline recorded are closed with them. The
# `parser` tuple names the modules that reader needs, so
# `core.resolve_declared_parsers` refuses before extraction on a machine that
# has neither and `core.parser_pins` hashes both versions into the provenance
# manifest, pack-scoped (clause 1). api-surface's DERIVED class is `parser`
# (its committed-OpenAPI rung is `committed-artifact`, its router rung is
# `parser`, and `parser` is the weaker of the two).
#
# data-model declares the grammar because its SECOND probe is one: the
# migration replay walks the parse tree and declares `parser`. Its FIRST
# probe — the Ecto schema aggregator — is still a set of regular expressions
# over authored `.ex` source and declares `regex-over-source`, and so does
# module-graph's sole probe. All three are on the ADR-0097 roster; data-model's
# DERIVED class is `regex-over-source` (the minimum over its two rungs) and
# module-graph's is `regex-over-source` outright. Closing them is the pack's
# own follow-on. Declaring the `parser` tuple on both concerns costs nothing
# and states a true thing regardless of which rung answers: `parser_pins` keys
# on the module name, so the manifest records the same two pins either way.
_EX_SOURCE_GLOBS = ("*.ex", "*.exs", "**/*.ex", "**/*.exs", "mix.exs")

#: The Elixir grammar the api-surface reader binds. Declared on the concern
#: that uses it and on no other, because the pin is pack-and-concern scoped: a
#: machine deriving a repository whose api-surface probe never runs still
#: refuses without it, which is correct — the probe's availability cannot be a
#: function of the repository, or the recorded channel would vary by machine.
_ELIXIR_TS_PARSER_MODULES = ("tree_sitter", "tree_sitter_elixir")

INPUT_CLASSES = {
    "data-model": InputClass(
        expected="Ecto `schema`/`embedded_schema` declarations, else a `priv/repo/migrations/` directory",
        globs=_EX_SOURCE_GLOBS,
        parser=_ELIXIR_TS_PARSER_MODULES,
    ),
    "api-surface": InputClass(
        expected="a committed OpenAPI document, else a Phoenix `router.ex` declaring routes",
        globs=("openapi.json", "swagger.json", "docs/openapi.json",
               "swagger/v1/swagger.json", "doc/openapi.json") + _EX_SOURCE_GLOBS,
        parser=_ELIXIR_TS_PARSER_MODULES,
    ),
    "module-graph": InputClass(
        expected="resolvable `alias`/`import`/`use` or remote references between the repository's own modules",
        globs=_EX_SOURCE_GLOBS,
    ),
}


def probes() -> dict[str, list[Probe]]:
    """The elixir probe registry, exactly what `derive._pack_probes("elixir", …)`
    bound before the split (ADR-0069, ADR-0066 points 2/3).

    Ordered probes per concern: the first matching probe wins, and each
    self-degrades to the stub. api-surface: committed OpenAPI → the static
    Phoenix router parse. data-model: Ecto schemas → the replayed Ecto
    migrations. module-graph: the alias/import/use + remote-ref dependency graph
    (resolve-or-drop).

    `decision-index` is deliberately absent: it is the UNIVERSAL base probe
    (`core.extract_decision_index`), bound by the core for every pack, never by
    a pack module.
    """
    return {
        "data-model": [
            Probe(_detect_elixir_schema, extract_elixir_data_model,
                  kind="regex-over-source"),
            Probe(_detect_elixir_migrations, extract_elixir_migrations,
                  kind="parser"),
        ],
        "api-surface": [
            Probe(_detect_elixir_openapi, extract_elixir_api_surface_openapi,
                  kind="committed-artifact"),
            Probe(_detect_elixir_router, extract_elixir_api_surface_router,
                  kind="parser"),
        ],
        "module-graph": [
            Probe(_detect_elixir_module_graph, extract_elixir_module_graph,
                  kind="regex-over-source"),
        ],
    }
