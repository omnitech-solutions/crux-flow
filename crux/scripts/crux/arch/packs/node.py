"""Node stack pack (ADR-0068) — the static JS/TS extractors for the arch spine.

This pack populates three concerns for a repository whose root carries a
`package.json`: `api-surface` (a committed OpenAPI/Swagger document, else one
receiver-agnostic route scan covering Express/Fastify verb calls and NestJS
decorators), `data-model` (Prisma `schema.prisma`, else TypeORM `@Entity`
classes, else Sequelize `define`/`init` attribute maps), and `module-graph`
(the explicit TS/JS import graph, resolve-or-drop over an extension ladder plus
root-`tsconfig.json` path aliases). Every extractor is static and deterministic:
no Node, npm, tsc or prisma is executed (ADR-0068 point 6). Every file read is
containment-checked, size-bounded and hashed into `sources`, so a changed source
drifts the spine.

Every parser this pack uses is named by the consuming probe's input class and
pinned in the deriver's PEP 723 block (ADR-0097 part 5). `data-model` and
`api-surface` both declare `parser=_NODE_TS_PARSER_MODULES` —
`("tree_sitter", "tree_sitter_typescript")` — and read JS/TS through
`_js_ts_parser`; `module-graph` declares none and needs nothing beyond the
stdlib.

Naming the parser on the declaration is what gives a missing grammar a lane of
its own. `core.resolve_declared_parsers` runs BEFORE any extraction and raises
`ParserUnavailable` when a declared grammar is not importable, which
`derive-arch.py` reports as exit 2 — the no-verdict lane, not a drift finding.
On a machine without those wheels this pack refuses rather than degrading to a
stub.

The bound on this pack's work is a PER-FILE SIZE BOUND — 2 MB, via
`_MAX_FILE_BYTES` — and not a linear-time guarantee. A tree-sitter parse does
not backtrack over its input as a regex engine does, but its worst case is not
linear, and capping the input bounds the work without making the parse linear.

HISTORY — true of the U7 move, not of the tree as it stands. The extractor
bodies below moved here VERBATIM from `crux/arch/derive.py` under ADR-0096
clause 12 ("the monolith splits"). The gate for that move was byte-identity:
every corpus golden unchanged across it, and every function and constant in this
module carrying source text identical to its `derive.py` origin, with only the
cross-boundary references rewritten to a module-scope `from ..core import` — a
pack module MAY import from `..core` at module scope; only `core.py` may not
import from here. That claim is scoped to that move and is still true OF IT.

Later units then added code that was never in `derive.py` — the tree-sitter
reader named above is the largest — so read the byte-identity sentence as a
dated record of the split rather than as a description of this file today.

`detect()` and `probes()` at the foot of the module are the pack interface
clause 12 requires: the stack-marker detector (`DetectResult`) and the
ordered per-concern probe registry the core binds. The universal
`decision-index` probe is NOT bound here — the core binds it for every pack.
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
    _split_top_level,
)


# ─────────────────────── node stack pack (ADR-0068) ─────────────────────────
# Static, deterministic extractors. No Node/npm/tsc/prisma is
# executed (ADR-0068 point 6). `data-model` and `api-surface` declare the
# tree-sitter TypeScript grammar, and a machine missing it takes the exit-2
# no-verdict lane rather than a stub — see the module docstring.
# Every file read is containment-checked, size-
# bounded (2 MB via `_MAX_FILE_BYTES`) and hashed into `sources`, so a changed
# source drifts the spine. Enumeration is `os.walk(followlinks=False)` with
# codepoint-sorted dir/file order and aggregate file/byte caps + a residual.
# This repo pins `arch_stack: crux`, so these never run here (point 7). All three
# concerns are populated: api-surface (OpenAPI → route scan), data-model (Prisma
# → TypeORM → Sequelize), and module-graph (the explicit TS/JS import graph).

_JS_EXTS = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")

# Line comments are linear (newline-bounded). Block comments are stripped by a
# linear str.find scan, NOT a regex — a lazy `/\*.*?\*/` is O(n²) on a file of
# many unclosed `/*` openers (each anchor scans to EOF), a ReDoS-class DoS the
# 2 MB bound only caps at minutes-per-file.
_JS_LINE_COMMENT = re.compile(r"//[^\n]*")


def _scrub_js_comments(text: str) -> str:
    """Drop `/* */` block comments and `//` line comments before the route scan
    (point 6). String literals are NOT scrubbed — a route path is a quoted string
    the verb-call/decorator patterns capture with its quotes. JS lexical edge
    cases (a `//` inside a string, a `/*` inside a regex/template literal) are a
    named residual per ADR-0068, mitigated by the `/`-leading string guard."""
    out = []
    i, n = 0, len(text)
    while i < n:                                   # single linear pass; string-aware.
        c = text[i]
        if c in "\"'`":                            # string literal: copy verbatim so a
            out.append(c)                          # `//` or `/*` INSIDE it is not a comment
            i += 1                                 # (e.g. a JSON `"@/*"` path or a route path).
            while i < n:
                d = text[i]
                out.append(d)
                if d == "\\" and i + 1 < n:        # keep an escaped char (incl. an escaped quote).
                    out.append(text[i + 1])
                    i += 2
                    continue
                i += 1
                if d == c:
                    break
            continue
        if c == "/" and i + 1 < n:
            nxt = text[i + 1]
            if nxt == "/":                         # line comment → drop to newline (kept).
                nl = text.find("\n", i)
                if nl == -1:
                    break
                i = nl
                continue
            if nxt == "*":                         # block comment → drop to `*/` (→ space).
                out.append(" ")
                end = text.find("*/", i + 2)
                if end == -1:
                    break                          # unclosed: the rest is comment.
                i = end + 2
                continue
        out.append(c)
        i += 1
    return "".join(out)


def _walk_js_files(root: Path):
    """Yield contained JS/TS files under `root`, skipping VCS/vendor + dot dirs.
    `os.walk(followlinks=False)` never descends a symlinked directory; dir and
    file names are codepoint-sorted so an aggregate-cap truncation selects a
    stable, locale-independent prefix (point 6)."""
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(
            d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")
        )
        for fn in sorted(filenames):
            if fn.endswith(_JS_EXTS):
                p = Path(dirpath) / fn
                if _contained(root, p):
                    yield p


def _has_js_sources(root: Path) -> bool:
    for _ in _walk_js_files(Path(root)):
        return True
    return False


def _scan_node_files(root: Path):
    """Enumerate JS/TS sources (sorted, contained, size-bounded) with aggregate
    file/byte caps (point 6). Returns (entries, sources, residuals): entries is
    [(repo-rel path, raw bytes)]; every read file is hashed into `sources`
    (point 6 drift rule — even a route-less file shapes the negative result).

    **Bytes, not text, and this is the postcondition (c) seam.** Every read in
    this pack goes through `core._safe_read_bytes` here — containment-checked and
    size-bounded in one place — and every probe below RECEIVES the result and
    opens nothing itself. Bytes rather than a decoded string because tree-sitter
    node offsets are byte offsets: decoding first makes every slice wrong on a
    file holding a multi-byte character. The three readers that still want text
    decode from these same bytes, so no probe acquires a second way in.
    """
    entries: list = []
    sources: dict = {}
    residuals: list = []
    oversize: list = []
    n_files = 0
    n_bytes = 0
    capped = False
    for p in _walk_js_files(root):
        if n_files >= _MAX_SCAN_FILES or n_bytes >= _MAX_SCAN_BYTES:
            capped = True
            break
        raw = _safe_read_bytes(root, p, oversize)   # containment + 2 MB bound.
        if raw is None:
            continue
        rel = _rel(root, p)
        sources[rel] = _sha256_hex(raw)
        entries.append((rel, raw))
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


# ── api-surface: committed OpenAPI first, then one receiver-agnostic route scan ─

def _node_openapi_path(root: Path) -> Path | None:
    for c in _OPENAPI_CANDIDATES:
        p = root / c
        if p.is_file() and _contained(root, p):
            return p
    return None


def _detect_node_openapi(root: Path) -> bool:
    return _node_openapi_path(Path(root)) is not None


def extract_node_api_surface_openapi(root: Path, docs_dir: str) -> tuple[str, dict]:
    """API surface from a committed OpenAPI/Swagger document (point 1), rendered
    through the shared `_render_openapi` (point 6). Stub when absent/unparseable."""
    root = Path(root)
    spec = _node_openapi_path(root)
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


# ── the tree-sitter TypeScript grammar (ADR-0096 clause 1) ───────────────────
# The api-surface concern declares the input class `parser`, and this is the
# parser. Clause 1 prohibits matching a regular expression against authored
# source for a concern that declares one, and the corpus measured the cost of
# doing it anyway rather than arguing it stylistically. Three defects, all in
# the goldens before this reader replaced them:
#
#   * The handler was read as the argument immediately after the path, so every
#     Express route carrying middleware (`post(p, validate(v), ctrl.fn)`)
#     reported `—`. That is all eight routes of one corpus repository's auth
#     router.
#   * The chained form `router.route(p).get(h).post(h)` was missed ENTIRELY: the
#     path sits on `.route()` and the verb on a call whose receiver is the
#     previous call, so a pattern anchored on `<ident>.<verb>(` sees no path
#     argument at all. Five more routes of the same repository.
#   * An interpolated template literal was rendered as though its SOURCE TEXT
#     were a path — the measured `/api/${routeName}/:id` rows. A path composed
#     at require-time has no static value; the reader never executes the
#     application, so the only honest outcome is to drop the row and name it.
#
# One distribution covers all six extensions the pack scans. The TypeScript
# grammar is a superset of JavaScript, so `.js`/`.mjs`/`.cjs` parse under it;
# `.jsx`/`.tsx` need the TSX dialect, because JSX and TypeScript's type-assertion
# syntax claim the same angle brackets and no single grammar can hold both.

_TS_PARSERS: dict = {}

#: The two extensions that must go through the TSX dialect rather than the
#: TypeScript one. Everything else the pack scans parses under TypeScript.
_TSX_EXTS = (".jsx", ".tsx")

#: The importable modules this pack's `parser` declaration needs. Named once,
#: consulted by `INPUT_CLASSES` below, so `core.resolve_declared_parsers` refuses
#: before extraction on a machine without them and `core.parser_pins` records
#: their versions into the byte-compared provenance manifest.
_NODE_TS_PARSER_MODULES = ("tree_sitter", "tree_sitter_typescript")


def _js_ts_parser(dialect: str):
    """The tree-sitter parser for one dialect, built once per process.

    The import is inside the function, deliberately, exactly as the elixir pack
    does it. The core imports every pack module to read its registry, so a
    module-scope `import tree_sitter` would make a python, ruby, elixir, crux or
    swift derive fail on a machine with no TypeScript grammar — packs that declare no
    such parser, consult none, and record none in the provenance manifest.
    `core.resolve_declared_parsers` has already refused, before any extraction,
    if the grammar is absent for a pack that DOES declare it, so reaching here
    means the import resolves.
    """
    if dialect not in _TS_PARSERS:
        import tree_sitter                                # noqa: PLC0415
        import tree_sitter_typescript                     # noqa: PLC0415
        language = getattr(tree_sitter_typescript, f"language_{dialect}")()
        _TS_PARSERS[dialect] = tree_sitter.Parser(tree_sitter.Language(language))
    return _TS_PARSERS[dialect]


def _js_parse(rel: str, raw: bytes):
    """Parse JS/TS source BYTES into a tree-sitter tree, dialect by extension.

    Bytes, not text: tree-sitter node offsets are byte offsets, and decoding
    first would make every slice below wrong on any file with a multi-byte
    character in it. The caller supplies the bytes `core._safe_read_bytes`
    returned; nothing here opens a file.
    """
    dialect = "tsx" if rel.endswith(_TSX_EXTS) else "typescript"
    return _js_ts_parser(dialect).parse(raw)


def _ts_nodes(root):
    """Every node in the tree, pre-order. Iterative, so a deep file cannot
    exhaust the interpreter stack the way a recursive walk would."""
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(node.children))


def _ts_text(node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _ts_static_string(node, src: bytes) -> str | None:
    """A string literal's static value, or None when it has none.

    None for an INTERPOLATED template literal. `` `/api/${name}` `` has no
    static value — computing it means executing the module — so the caller drops
    the declaration and names it a residual rather than rendering the
    interpolation's source text as if it were a path. A backtick string with NO
    substitution does have a static value and resolves normally: the prohibition
    is on inventing a value that does not exist, not on the quote character.
    """
    if node is None:
        return None
    if node.type == "string":
        return "".join(_ts_text(c, src) for c in node.children
                       if c.type == "string_fragment")
    if node.type == "template_string":
        if any(c.type == "template_substitution" for c in node.children):
            return None
        return "".join(_ts_text(c, src) for c in node.children
                       if c.type == "string_fragment")
    return None


def _ts_is_dropped_path(node, src: bytes) -> bool:
    """Whether the node is a ROUTE path the reader could not evaluate.

    This is the drop rule, and its guard is the same `/`-leading test the
    resolvable form uses — deliberately, because the two have to agree about
    what counts as a path or the residual becomes a list of things that were
    never routes.

    A resolvable path argument is a string whose value starts with `/`. An
    unresolvable one is an interpolated template whose first STATIC fragment
    starts with `/`: `` `/api/${name}` `` is a route whose value needs the
    module executed, and it is dropped and named. `` `${prefix}_SECRET` `` is
    not — that is `configService.get(...)`, a decoy the `/`-guard has always
    excluded silently, and naming it as a dropped route would fill the residual
    with declarations that are not routes. A template whose value begins with
    the interpolation is unresolvable AND unidentifiable, so it stays silent
    too: the reader cannot tell a route from a config lookup there, and
    guessing in the residual is still guessing.
    """
    if node is None or node.type != "template_string":
        return False
    if not any(c.type == "template_substitution" for c in node.children):
        return False
    for child in node.children:
        if child.type == "template_substitution":
            return False                    # interpolation first: no static prefix.
        if child.type == "string_fragment":
            return _ts_text(child, src).startswith("/")
    return False


#: The receiver shapes a DIRECT verb call accepts. `app.get(...)`,
#: `router.post(...)`, `this.server.get(...)` — an identifier or a member
#: expression. A CALL receiver is excluded here and reaches the chained branch
#: instead, which is what keeps `request(app.getHttpServer()).get('/')` — a
#: supertest assertion in a test file, not a route declaration — out of the
#: table. The pre-parser reader excluded it by requiring a bare `\w+` before the
#: verb; that guard is preserved rather than quietly widened, because widening
#: it added four rows to one corpus repository that has two routes.
_JS_RECEIVER_TYPES = ("identifier", "member_expression")


def _ts_call_args(node) -> list:
    """A `call_expression`'s argument nodes, punctuation dropped."""
    for child in node.children:
        if child.type == "arguments":
            return [c for c in child.children if c.is_named]
    return []


def _ts_member_parts(node):
    """`(object, property-name-node)` for a `member_expression`, else (None, None)."""
    if node is None or node.type != "member_expression":
        return None, None
    obj = node.child_by_field_name("object")
    prop = node.child_by_field_name("property")
    return obj, prop


def _ts_handler_name(args: list, src: bytes) -> str:
    """The handler cell: the LAST argument, when it names something.

    Express and Fastify both take `(path, ...middleware, handler)`, so the
    handler is the last argument and never reliably the second. An argument that
    is an inline arrow, a `function` expression, or a call such as
    `swaggerUi.setup(specs, {})` names nothing recoverable and renders `—`;
    deriving a name from that call's source text would be the same class of
    error as rendering an interpolation placeholder.
    """
    if not args:
        return "—"
    last = args[-1]
    if last.type in ("identifier", "member_expression"):
        return _ts_text(last, src)
    return "—"


#: The verb set, unchanged from the pre-parser reader. `head`/`options` are
#: deliberately NOT added here: widening the set is a content change that would
#: move goldens for a reason unrelated to the parser conversion, and every
#: movement in this unit has to be attributable to the parser.
_JS_VERBS = ("get", "post", "put", "patch", "delete", "all")
_NEST_VERBS = ("Get", "Post", "Put", "Patch", "Delete", "All")


def _ts_chain_route_path(node, src: bytes):
    """The path on a `.route(p)` at the base of a chained verb call.

    `router.route(p).get(h).post(h)` parses as nested `call_expression`s: each
    verb call's receiver is the previous call. Walking that receiver chain down
    to the `.route()` is what recovers a form the pattern-matching reader could
    not see at all. Returns `(path, found)` — `found` distinguishes "a `.route()`
    whose argument has no static value" (drop AND name it) from "no `.route()`
    in this chain" (not a route declaration).
    """
    while node is not None and node.type == "call_expression":
        fn = node.child_by_field_name("function")
        _obj, prop = _ts_member_parts(fn)
        if prop is None:
            return None, False
        if _ts_text(prop, src) == "route":
            args = _ts_call_args(node)
            first = args[0] if args else None
            value = _ts_static_string(first, src)
            if value is not None and value.startswith("/"):
                return value, True
            return None, _ts_is_dropped_path(first, src)
        node = _ts_member_parts(fn)[0]
    return None, False


def _scan_verb_calls(rel: str, tree, src: bytes, dropped: list) -> set:
    """Express/Fastify rows from a parsed tree: (METHOD, path, handler).

    Two forms, one walk. The direct form takes its path from the verb call's
    first argument; the chained form takes it from a `.route()` further down the
    receiver chain. A declaration whose path is a string the reader cannot
    evaluate is appended to `dropped` and contributes no row.
    """
    rows: set = set()
    for node in _ts_nodes(tree.root_node):
        if node.type != "call_expression":
            continue
        fn = node.child_by_field_name("function")
        obj, prop = _ts_member_parts(fn)
        if prop is None or _ts_text(prop, src) not in _JS_VERBS:
            continue
        method = _ts_text(prop, src).upper()
        args = _ts_call_args(node)
        first = args[0] if args else None
        if obj is not None and obj.type in _JS_RECEIVER_TYPES:
            value = _ts_static_string(first, src)
            if value is not None and value.startswith("/"):
                rows.add((method, _norm_path(value), _ts_handler_name(args[1:], src)))
                continue
            if _ts_is_dropped_path(first, src):
                # A path with a static `/` prefix but no computable value.
                dropped.append((rel, node.start_point[0] + 1))
                continue
        chained, found = _ts_chain_route_path(obj, src)
        if chained is not None:
            rows.add((method, _norm_path(chained), _ts_handler_name(args, src)))
        elif found:
            dropped.append((rel, node.start_point[0] + 1))
    return rows


# ── mount composition: the closed four-form set (ADR-0096 clause 1) ──────────
# The baseline's second measured api-surface defect. The reader found routes but
# composed no mount prefix, so it rendered each declaration at the path relative
# to its own router file rather than to the application — nine wrong paths on
# one corpus repository.
#
# Composition is bounded by a CLOSED set of four mount forms, and the bound is
# the point. A mount outside the set contributes a residual and leaves its
# routes at their file-relative path; it never contributes a partial prefix.
# Adjudication 3 in one sentence: a composed path is never a guess. Rendering
# `/v1/x` for a mount whose middle segment is unknown asserts that segment is
# empty, which is exactly the class of claim the dropped-path rule refuses.
#
#   (1) `app.use('<path>', <ident>)`      — ident bound to a require/import here
#   (2) `app.use('<path>', require('…'))` — the same mount written inline
#   (3) `<table>.forEach((e) => r.use(e.<k>, e.<k>))` — a static array of object
#       literals, each with a static path and a module identifier. The form
#       express-boilerplate uses, and the reason `/v1/auth/register` is knowable
#       without executing anything: every segment is a literal in the AST.
#   (4) `app.use(<ident|require>)`        — mounted at the root, no prefix
#
# Everything else is silent or a residual, and the split matters. A mount whose
# module argument does not resolve to an in-repo module is MIDDLEWARE
# (`app.use(express.json())`) and is silent. A mount that does resolve but whose
# prefix has no static value is extraction debt and is named.


def _js_module_bindings(tree, src: bytes, rel: str, resolved_map: dict,
                        root: Path) -> dict:
    """`{local name: repo-relative module}` for this file's require/import binds.

    Both module systems, because the corpus holds both: CommonJS
    `const r = require('./x')` and the ES `import r from './x'`. A bare package
    specifier resolves to nothing and is absent from the result — the same
    resolve-or-drop posture the module graph takes.
    """
    out: dict = {}

    def _resolve(spec: str):
        return _resolve_js_spec(root / rel, spec, resolved_map, [], root)

    for node in _ts_nodes(tree.root_node):
        if node.type == "variable_declarator":
            name = node.child_by_field_name("name")
            value = node.child_by_field_name("value")
            if (name is None or name.type != "identifier"
                    or value is None or value.type != "call_expression"):
                continue
            fn = value.child_by_field_name("function")
            if fn is None or fn.type != "identifier" or _ts_text(fn, src) != "require":
                continue
            args = _ts_call_args(value)
            spec = _ts_static_string(args[0], src) if args else None
            target = _resolve(spec) if spec else None
            if target:
                out[_ts_text(name, src)] = target
        elif node.type == "import_statement":
            source = node.child_by_field_name("source")
            spec = _ts_static_string(source, src) if source is not None else None
            target = _resolve(spec) if spec else None
            if not target:
                continue
            for clause in node.children:
                if clause.type != "import_clause":
                    continue
                for child in clause.children:
                    if child.type == "identifier":       # the default import.
                        out[_ts_text(child, src)] = target
    return out


def _js_mounted_module(node, src: bytes, bindings: dict, resolved_map: dict,
                       rel: str, root: Path):
    """The in-repo module a mount's second argument names, or None.

    Forms (1)/(3) reach it through a local binding; form (2) resolves the inline
    `require(...)` directly. None means "not an in-repo module", which is how
    middleware stays silent rather than becoming a finding.
    """
    if node is None:
        return None
    if node.type == "identifier":
        return bindings.get(_ts_text(node, src))
    if node.type == "call_expression":
        fn = node.child_by_field_name("function")
        if fn is not None and fn.type == "identifier" and _ts_text(fn, src) == "require":
            args = _ts_call_args(node)
            spec = _ts_static_string(args[0], src) if args else None
            if spec:
                return _resolve_js_spec(root / rel, spec, resolved_map, [], root)
    return None


def _js_static_mount_table(tree, src: bytes, name: str) -> list:
    """`[(path, module-identifier)]` for a same-file `const <name> = [ {...} ]`.

    Form (3)'s table. Every entry must be an object literal carrying a static
    string and an identifier; an entry that is not contributes nothing, so a
    half-static table composes only the half that is static and the rest simply
    does not appear as a mount.
    """
    for node in _ts_nodes(tree.root_node):
        if node.type != "variable_declarator":
            continue
        ident = node.child_by_field_name("name")
        value = node.child_by_field_name("value")
        if (ident is None or ident.type != "identifier"
                or _ts_text(ident, src) != name
                or value is None or value.type != "array"):
            continue
        rows: list = []
        for element in value.children:
            if element.type != "object":
                continue
            fields: dict = {}
            for pair in element.children:
                if pair.type != "pair":
                    continue
                key = pair.child_by_field_name("key")
                val = pair.child_by_field_name("value")
                if key is None or val is None:
                    continue
                fields[_ts_text(key, src).strip("'\"")] = val
            rows.append(fields)
        return rows
    return []


def _ts_sole_param_name(fn_node, src: bytes):
    """The name of a callback's ONE parameter, or None if it does not have one.

    Three spellings reach here and the grammar reports each differently.
    `(r) => …` is a `formal_parameters` whose child, under the TYPESCRIPT
    grammar, is a `required_parameter` wrapping the identifier in a `pattern`
    field rather than the bare identifier the JavaScript grammar yields. `r => …`
    puts the identifier on the `parameter` field instead. Reading only the bare
    shape found nothing on every `.tsx`/`.ts`/`.js` file in the corpus, since all
    of them parse under the TypeScript grammar.
    """
    single = fn_node.child_by_field_name("parameter")
    if single is not None and single.type == "identifier":
        return _ts_text(single, src)
    params = fn_node.child_by_field_name("parameters")
    if params is None:
        return None
    names: list = []
    for child in params.children:
        if child.type == "identifier":
            names.append(child)
        elif child.type in ("required_parameter", "optional_parameter"):
            pattern = child.child_by_field_name("pattern")
            if pattern is not None and pattern.type == "identifier":
                names.append(pattern)
            else:
                return None              # a destructured parameter: not this form.
    return _ts_text(names[0], src) if len(names) == 1 else None


def _js_arrow_mount_call(node, src: bytes):
    """A `forEach` callback's `<recv>.use(<p>.<k1>, <p>.<k2>)`, as `(k1, k2)`.

    Returns None unless the callback takes one parameter and both arguments are
    member expressions on exactly that parameter — which is what makes the two
    keys safe to read off the table rather than guessed.
    """
    args = _ts_call_args(node)
    if len(args) != 1 or args[0].type not in ("arrow_function", "function_expression"):
        return None
    param = _ts_sole_param_name(args[0], src)
    if param is None:
        return None
    for inner in _ts_nodes(args[0]):
        if inner.type != "call_expression":
            continue
        fn = inner.child_by_field_name("function")
        _obj, prop = _ts_member_parts(fn)
        if prop is None or _ts_text(prop, src) != "use":
            continue
        use_args = _ts_call_args(inner)
        if len(use_args) != 2:
            continue
        keys = []
        for arg in use_args:
            obj, prop2 = _ts_member_parts(arg)
            if (obj is None or obj.type != "identifier"
                    or _ts_text(obj, src) != param or prop2 is None):
                keys = []
                break
            keys.append(_ts_text(prop2, src))
        if len(keys) == 2:
            return keys[0], keys[1]
    return None


def _js_mount_edges(tree, src: bytes, rel: str, bindings: dict,
                    resolved_map: dict, root: Path, unresolved: list) -> list:
    """`[(prefix, mounted module)]` for every mount this file declares.

    A mount naming an in-repo module whose prefix has no static value is
    appended to `unresolved` and yields no edge.
    """
    edges: list = []
    for node in _ts_nodes(tree.root_node):
        if node.type != "call_expression":
            continue
        fn = node.child_by_field_name("function")
        obj, prop = _ts_member_parts(fn)
        if prop is None:
            continue
        verb = _ts_text(prop, src)
        args = _ts_call_args(node)

        if verb == "use" and obj is not None and obj.type in _JS_RECEIVER_TYPES:
            if len(args) == 1:                          # form (4): mounted at root.
                target = _js_mounted_module(args[0], src, bindings,
                                            resolved_map, rel, root)
                if target:
                    edges.append(("", target))
                continue
            if len(args) >= 2:                          # forms (1) and (2).
                target = _js_mounted_module(args[1], src, bindings,
                                            resolved_map, rel, root)
                if not target:
                    continue                            # middleware: silent.
                prefix = _ts_static_string(args[0], src)
                if prefix is not None and prefix.startswith("/"):
                    edges.append((prefix, target))
                else:
                    unresolved.append((rel, node.start_point[0] + 1))
                continue

        if verb == "forEach" and obj is not None and obj.type == "identifier":
            keys = _js_arrow_mount_call(node, src)      # form (3): the mount table.
            if keys is None:
                continue
            path_key, route_key = keys
            for fields in _js_static_mount_table(tree, src, _ts_text(obj, src)):
                prefix = _ts_static_string(fields.get(path_key), src)
                target = _js_mounted_module(fields.get(route_key), src, bindings,
                                            resolved_map, rel, root)
                if not target:
                    continue
                if prefix is not None and prefix.startswith("/"):
                    edges.append((prefix, target))
                else:
                    unresolved.append((rel, node.start_point[0] + 1))
    return edges


def _join_mount(prefix: str, path: str) -> str:
    parts = [seg for seg in (prefix.strip("/"), path.strip("/")) if seg]
    return "/" + "/".join(parts) if parts else "/"


def _node_mount_prefixes(parsed: list, root: Path, resolved_map: dict):
    """`({module: prefix}, residuals)` — the composed prefix per module.

    Composition walks the mount graph from its roots, so a two-level mount
    composes to the concatenation of both prefixes. Three conditions leave a
    module UNCOMPOSED at the file-relative fallback, and each is a residual
    rather than a silent choice:

      * a mount whose prefix has no static value (outside the closed set);
      * a module mounted at two different prefixes — one row cannot carry both,
        and taking the first is a guess wearing determinism's clothes;
      * a mount cycle, which no traversal can assign a finite prefix to.
    """
    unresolved: list = []
    incoming: dict = {}
    for rel, raw, tree in parsed:
        bindings = _js_module_bindings(tree, raw, rel, resolved_map, root)
        for prefix, target in _js_mount_edges(tree, raw, rel, bindings,
                                              resolved_map, root, unresolved):
            if target != rel:
                incoming.setdefault(target, []).append((rel, prefix))

    residuals: list = []
    if unresolved:
        by_file: dict = {}
        for rel, line in sorted(set(unresolved)):
            by_file.setdefault(rel, []).append(line)
        where = "; ".join(
            f"`{_cell(rel)}` line{'s' if len(v) > 1 else ''} "
            + ", ".join(str(n) for n in v)
            for rel, v in sorted(by_file.items()))
        n = len(set(unresolved))
        residuals.append(
            f"{n} mount{'s' if n != 1 else ''} outside the composable forms: the "
            "prefix has no static value, so the routes reached through "
            f"{'them' if n != 1 else 'it'} render at their path RELATIVE to their "
            f"own router file rather than to the application — {where}.")

    ambiguous = sorted(t for t, srcs in incoming.items()
                       if len({p for _s, p in srcs}) > 1)
    if ambiguous:
        residuals.append(
            "Mounted at more than one prefix, so no prefix is composed and the "
            "routes render at their file-relative path (a named residual): "
            + ", ".join(f"`{_cell(t)}`" for t in ambiguous) + ".")

    prefixes: dict = {rel: "" for rel, _raw, _tree in parsed}
    single = {t: srcs[0] for t, srcs in incoming.items() if t not in ambiguous}
    resolved: dict = {}

    def _prefix_of(rel: str, seen: frozenset) -> str:
        if rel in resolved:
            return resolved[rel]
        parent = single.get(rel)
        if parent is None or rel in seen:
            return ""                                   # a root, or a cycle.
        value = _join_mount(_prefix_of(parent[0], seen | {rel}), parent[1])
        resolved[rel] = value
        return value

    cyclic: list = []
    for rel in sorted(prefixes):
        chain, node = set(), rel
        while node in single and node not in chain:
            chain.add(node)
            node = single[node][0]
        if node in chain:
            cyclic.append(rel)
            continue
        prefixes[rel] = _prefix_of(rel, frozenset())
    if cyclic:
        residuals.append(
            "Mount cycle: no finite prefix exists, so these render at their "
            "file-relative path (a named residual): "
            + ", ".join(f"`{_cell(c)}`" for c in sorted(cyclic)) + ".")
    return prefixes, residuals


def _join_nest(prefix: str, sub: str) -> str:
    parts = [seg.strip("/") for seg in (prefix, sub)]
    parts = [p for p in parts if p]
    return "/" + "/".join(parts) if parts else "/"


def _ts_decorator_call(node, src: bytes):
    """`(name, args)` for a `@Name(...)` decorator, else `(None, [])`.

    Only the bare-identifier callee form is a Nest route decorator; a dotted or
    parenthesized callee is something else and is not guessed at.
    """
    if node.type != "decorator":
        return None, []
    for child in node.children:
        if child.type == "call_expression":
            fn = child.child_by_field_name("function")
            if fn is not None and fn.type == "identifier":
                return _ts_text(fn, src), _ts_call_args(child)
        elif child.type == "identifier":            # bare `@Get` with no parens.
            return _ts_text(child, src), []
    return None, []


def _ts_decorated_method_name(node, src: bytes) -> str:
    """The method a decorator decorates, found through the AST rather than a
    lookahead over the following characters.

    Decorators are SIBLINGS of the `method_definition` inside `class_body`, and
    they stack, so the answer is the next sibling that is actually a method.
    """
    sib = node.next_named_sibling
    while sib is not None:
        if sib.type == "method_definition":
            name = sib.child_by_field_name("name")
            return _ts_text(name, src) if name is not None else "—"
        if sib.type != "decorator":
            return "—"
        sib = sib.next_named_sibling
    return "—"


def _scan_nest_routes(rel: str, tree, src: bytes, dropped: list) -> set:
    """NestJS rows from a parsed tree. A method decorator pairs with the nearest
    preceding `@Controller` in the same file; one with no preceding controller is
    skipped. A decorator path with no static value drops, exactly as above."""
    decorators = [n for n in _ts_nodes(tree.root_node) if n.type == "decorator"]
    decorators.sort(key=lambda n: n.start_byte)
    rows: set = set()
    prefix = None
    for node in decorators:
        name, args = _ts_decorator_call(node, src)
        if name is None:
            continue
        if name == "Controller":
            value = _ts_static_string(args[0], src) if args else ""
            prefix = value if value is not None else ""
            continue
        if name not in _NEST_VERBS or prefix is None:
            continue
        first = args[0] if args else None
        sub = _ts_static_string(first, src) if args else ""
        if sub is None:
            dropped.append((rel, node.start_point[0] + 1))
            continue
        rows.add((name.upper(), _join_nest(prefix, sub),
                  _ts_decorated_method_name(node, src)))
    return rows


_NODE_FRAMEWORKS = ("express", "fastify", "@nestjs/common", "@nestjs/core")


def _node_pkg_label(root: Path) -> tuple[str, dict]:
    """A LABEL-only read of `package.json` (point 1: deps never gate). Returns
    (note, sources): the note names declared route frameworks; `package.json` is
    hashed into `sources` ONLY when it actually labels (point 6 drift rule — a
    version-only bump that changes no label must not drift the spine)."""
    pkg = Path(root) / "package.json"
    raw = _safe_read_bytes(Path(root), pkg, [])
    if raw is None:
        return "", {}
    try:
        doc = json.loads(raw.decode("utf-8"))
    except Exception:
        return "", {}
    deps: dict = {}
    if isinstance(doc, dict):
        for k in ("dependencies", "devDependencies",
                  "peerDependencies", "optionalDependencies"):
            d = doc.get(k)
            if isinstance(d, dict):
                deps.update(d)
    found = sorted(n for n in _NODE_FRAMEWORKS if n in deps)
    if not found:
        return "", {}
    note = ("_Declared route frameworks: "
            + ", ".join(f"`{_cell(n)}`" for n in found)
            + " (labels only; the scan is receiver-agnostic)._")
    return note, {_rel(Path(root), pkg): _sha256_hex(raw)}


def _detect_node_routes(root: Path) -> bool:
    return _has_js_sources(Path(root))


# ---- U-N4: the GraphQL debt, carried VISIBLY in the rendered spine ----------
# ADR-0096 clause 8 requires an honest expectation record, and `stubbed` on
# nestjs-prisma-starter would be a FALSE one: the two `@Get` handlers are real
# and committed, and a verdict computed from those bytes alone is `populated`.
# So the debt stays declared in `corpus.yml`'s `because:` (2 is a floor, not a
# claim), and this residual makes the SAME gap visible to a reader of the
# rendered `api-surface.md` -- not only a reader of the test expectations --
# by naming the GraphQL surface it found and did not read. No new probe: this
# adds no route, drops no row, and changes no repository's verdict.

#: `*.graphql`/`*.gql` files are how a GraphQL schema is authored under this
#: pack's stack (Apollo/NestJS `GraphQLModule` code-first or schema-first).
_GRAPHQL_EXTS = (".graphql", ".gql")

#: The three NestJS GraphQL decorators whose presence means "resolvers exist",
#: independent of the REST decorators `_scan_nest_routes` already reads.
_GRAPHQL_DECORATORS = frozenset({"Resolver", "Query", "Mutation"})


def _find_graphql_files(root: Path) -> list:
    """Contained `*.graphql`/`*.gql` files, sorted by repo-relative path.

    Presence only -- the residual below NAMES the file, never renders its
    contents, so nothing here reads one. The walk itself mirrors
    `_walk_js_files`: `os.walk(followlinks=False)`, skip-dir/dot-dir pruned,
    codepoint-sorted for a byte-stable render.
    """
    out: list = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(
            d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")
        )
        for fn in sorted(filenames):
            if fn.endswith(_GRAPHQL_EXTS):
                p = Path(dirpath) / fn
                if _contained(root, p):
                    out.append(_rel(root, p))
    return sorted(out)


def _graphql_resolver_files(parsed) -> list:
    """Repo-relative paths of already-parsed files carrying a `@Resolver`,
    `@Query` or `@Mutation` decorator.

    This is the postcondition (c) seam held: `parsed` is the SAME
    `(rel, raw, tree)` list `extract_node_api_surface_routes` already built
    from `core._safe_read_bytes` output, so finding a GraphQL decorator opens
    no file a second time -- it walks trees that are already in memory.
    """
    out = []
    for rel, raw, tree in parsed:
        for node in _ts_nodes(tree.root_node):
            if node.type != "decorator":
                continue
            name, _args = _ts_decorator_call(node, raw)
            if name in _GRAPHQL_DECORATORS:
                out.append(rel)
                break
    return sorted(out)


def _node_graphql_residual(root: Path, parsed) -> str:
    """A named residual when a GraphQL surface is present but unread.

    api-surface reads REST route declarations only (Express/Fastify verb
    calls + NestJS HTTP decorators); a repository whose real API is GraphQL
    still renders -- a route table, a stub, or both empty of GraphQL -- with
    nothing on the page itself saying so. This states that plainly, at the
    surface a reader of the ARCHITECTURE MAP reads, without inventing a
    GraphQL probe, reading a schema's contents, or changing a single row of
    the REST table above."""
    schemas = _find_graphql_files(root)
    resolvers = _graphql_resolver_files(parsed)
    if not schemas and not resolvers:
        return ""
    parts = []
    if schemas:
        parts.append(
            ("schema file " if len(schemas) == 1 else "schema files ")
            + ", ".join(f"`{_cell(p)}`" for p in schemas))
    if resolvers:
        parts.append(
            ("a resolver decorator in " if len(resolvers) == 1
             else "resolver decorators in ")
            + ", ".join(f"`{_cell(p)}`" for p in resolvers))
    return ("A GraphQL surface is present but not read by this scan (a named "
            "residual) -- " + "; ".join(parts) + ". api-surface covers REST "
            "route declarations only.")


def _dropped_path_residual(dropped: list) -> str:
    """Name every route declaration whose path had no static value.

    The residual states WHERE, never WHAT. Reproducing the interpolation's
    source text here would put the guessed path back into the rendered file
    under a different heading, which is the defect this reader removed; the
    file-and-line citation sends a reader to the declaration instead. Sorted by
    (path, line), so the render is byte-stable across machines.
    """
    by_file: dict = {}
    for rel, line in sorted(set(dropped)):
        by_file.setdefault(rel, []).append(line)
    where = "; ".join(
        f"`{_cell(rel)}` line{'s' if len(lines) > 1 else ''} "
        + ", ".join(str(n) for n in lines)
        for rel, lines in sorted(by_file.items()))
    n = len(set(dropped))
    return (f"{n} route declaration{'s' if n != 1 else ''} dropped: the path is "
            "composed at runtime and has no static value, and the application is "
            f"not executed, so no path is rendered for {'them' if n != 1 else 'it'} "
            f"(a named residual) — {where}.")


def extract_node_api_surface_routes(root: Path, docs_dir: str) -> tuple[str, dict]:
    """API surface from one receiver-agnostic static route scan (point 1): verb-
    call routes (Express/Fastify) + NestJS decorators, over every JS/TS source.
    `package.json` deps only LABEL. Sorted by (method, path, handler); all cells
    escaped. Stub when no route is found."""
    root = Path(root)
    entries, sources, residuals = _scan_node_files(root)

    # One parse per file, shared by the row scan and the mount scan. The mount
    # scan needs the SAME trees rather than a second read of the same files:
    # re-resolving a module by opening it again would put a file read back
    # inside a probe, which is the seam `_scan_node_files` exists to hold.
    parsed = [(rel, raw, _js_parse(rel, raw)) for rel, raw in entries]
    resolved_map: dict = {}
    for rel, _raw, _tree in parsed:
        try:
            resolved_map[(root / rel).resolve()] = rel
        except OSError:
            continue
    prefixes, mount_residuals = _node_mount_prefixes(parsed, root, resolved_map)

    rows: set = set()
    dropped: list = []
    for rel, raw, tree in parsed:
        prefix = prefixes.get(rel, "")
        found = _scan_verb_calls(rel, tree, raw, dropped)
        found |= _scan_nest_routes(rel, tree, raw, dropped)
        for method, path, handler in found:
            rows.add((method, _join_mount(prefix, path) if prefix else path, handler))
    if dropped:
        residuals.append(_dropped_path_residual(dropped))
    residuals += mount_residuals

    graphql_residual = _node_graphql_residual(root, parsed)
    if graphql_residual:
        residuals.append(graphql_residual)

    label, pkg_sources = _node_pkg_label(root)
    sources.update(pkg_sources)

    if not rows:
        # Stub, but keep the scanned-source hashes: a route added to an
        # already-hashed file must drift the spine (point 6 drift rule). A
        # GraphQL residual still renders here (U-N4): a repository whose only
        # HTTP surface is GraphQL has zero REST rows by construction, and the
        # residual is the one thing on this page telling a reader why.
        out = ["# API surface", "", NO_EXTRACTOR.rstrip("\n")]
        if residuals:
            out += ["", "## Residuals", ""] + [f"- {r}" for r in residuals]
        return _canon(out), sources

    out = ["# API surface", "",
           "_Static route scan (Express/Fastify verb calls + NestJS decorators, "
           "labeling GraphQL resolvers as a residual rather than reading them); "
           "the app is not executed._", ""]
    if label:
        out += [label, ""]
    out += [f"## Routes ({len(rows)})", "",
            "| method | path | handler |", "|---|---|---|"]
    for method, path, handler in sorted(rows):
        out.append(f"| {_cell(method)} | `{_cell(path)}` | {_cell(handler)} |")
    out.append("")
    if residuals:
        out += ["## Residuals", ""] + [f"- {r}" for r in residuals] + [""]
    return _canon(out), sources


# ── data-model: Prisma → TypeORM → Sequelize (ordered, first match wins) ──────
# All static and deterministic (ADR-0068 point 2). This concern declares the
# tree-sitter TypeScript grammar, so the TypeORM and Sequelize rungs are a real
# parse and a missing wheel is exit 2, not a stub.
# Prisma leads (the committed projection); TypeORM and Sequelize follow. Every parsed schema/model
# file is hashed so a change drifts the spine (point 6). Prisma scalar-typed
# fields (incl. scalar lists `String[]`) are COLUMNS; a model-typed field is a
# RELATION (omitted from the six-column table, listed in a relations note);
# `@relation(fields:[fk], references:[id])` sits on the relation field and names
# the scalar FK column it binds.

_PRISMA_SCALARS = frozenset({
    "String", "Boolean", "Int", "BigInt", "Float", "Decimal",
    "DateTime", "Json", "Bytes",
})
_PRISMA_BLOCK_HEAD = re.compile(r"^\s*(model|enum|type)\s+([A-Za-z_]\w*)\s*\{")
# A field line: `<name> <Type>[?|[]] <attrs...>`. Backtracking-safe.
_PRISMA_FIELD = re.compile(r"^\s*([A-Za-z_]\w*)\s+([A-Za-z_]\w*)(\[\])?(\?)?\s*(.*)$")


def _find_prisma_schemas(root: Path) -> list:
    """All contained `schema.prisma` files, sorted by repo-relative path (point 2
    monorepo rule: the sorted-first is rendered, the rest a residual)."""
    out: list = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(
            d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")
        )
        for fn in sorted(filenames):
            if fn == "schema.prisma":
                p = Path(dirpath) / fn
                if _contained(root, p):
                    out.append(p)
    return sorted(out, key=lambda p: _rel(root, p))


def _detect_node_prisma(root: Path) -> bool:
    return bool(_find_prisma_schemas(Path(root)))


def _strip_prisma_comments(text: str) -> str:
    """Drop `//` line comments, preserving double-quoted strings (a `//` inside a
    default string is not a comment). Prisma has no block comments (point 6)."""
    out = []
    for line in text.split("\n"):
        res = []
        i, n, in_str = 0, len(line), False
        while i < n:
            c = line[i]
            if in_str:
                res.append(c)
                if c == "\\" and i + 1 < n:
                    res.append(line[i + 1])
                    i += 2
                    continue
                if c == '"':
                    in_str = False
                i += 1
                continue
            if c == '"':
                in_str = True
                res.append(c)
                i += 1
                continue
            if c == "/" and i + 1 < n and line[i + 1] == "/":
                break
            res.append(c)
            i += 1
        out.append("".join(res))
    return "\n".join(out)


def _prisma_call_arg(attrs: str, name: str) -> str | None:
    """Balanced inner text of `@name(...)` (point 6: nested parens like
    `@default(autoincrement())` are matched, not truncated). None if absent."""
    m = re.search(r"(?<!@)@" + name + r"\s*\(", attrs)
    if not m:
        return None
    i, depth, start = m.end(), 1, m.end()
    while i < len(attrs) and depth:
        if attrs[i] == "(":
            depth += 1
        elif attrs[i] == ")":
            depth -= 1
            if depth == 0:
                return attrs[start:i]
        i += 1
    return attrs[start:i]


def _prisma_default(attrs: str) -> str:
    arg = _prisma_call_arg(attrs, "default")
    if arg is None:
        return "—"
    arg = arg.strip()
    if len(arg) >= 2 and arg[0] in "\"'" and arg[-1] == arg[0]:
        arg = arg[1:-1]
    return arg or "—"


def _parse_prisma(text: str):
    """Static block parse of `schema.prisma` (point 2). Returns
    (models, enums, composites) where each model dict carries columns, relations,
    fk_map, index notes, and the `@@map` residual name. Linear scan, no
    backtracking."""
    text = _strip_prisma_comments(text)
    # Pass 1: block bodies by kind.
    blocks: list = []               # (kind, name, [body lines])
    cur = None
    for line in text.split("\n"):
        if cur is None:
            m = _PRISMA_BLOCK_HEAD.match(line)
            if m:
                cur = [m.group(1), m.group(2), []]
            continue
        if line.strip().startswith("}"):
            blocks.append(cur)
            cur = None
            continue
        cur[2].append(line)
    if cur is not None:
        blocks.append(cur)

    model_names = {b[1] for b in blocks if b[0] == "model"}
    enum_names = {b[1] for b in blocks if b[0] == "enum"}
    composite_names = {b[1] for b in blocks if b[0] == "type"}

    enums: list = []
    for kind, name, body in blocks:
        if kind != "enum":
            continue
        vals = [ln.strip() for ln in body if ln.strip() and not ln.strip().startswith("@")]
        vals = [re.match(r"([A-Za-z_]\w*)", v).group(1) for v in vals if re.match(r"([A-Za-z_]\w*)", v)]
        enums.append((name, vals))

    composites = sorted(composite_names)

    models: list = []
    for kind, name, body in blocks:
        if kind != "model":
            continue
        model = {
            "name": name, "columns": [], "relations": [],
            "fk_map": {}, "index_notes": [], "mapped": None, "composite_fields": [],
        }
        for raw in body:
            s = raw.strip()
            if not s:
                continue
            if s.startswith("@@"):
                bm = re.match(r"@@(\w+)\s*(?:\(([^)]*)\))?", s)
                if not bm:
                    continue
                bname, barg = bm.group(1), bm.group(2) or ""
                if bname == "map":
                    mm = re.search(r"['\"]([^'\"]+)['\"]", barg)
                    if mm:
                        model["mapped"] = mm.group(1)
                elif bname in ("id", "unique", "index"):
                    cols = ", ".join(
                        c.strip().strip("'\"") for c in
                        re.sub(r"^\s*\[|\]\s*$", "", barg.strip()).split(",")
                        if c.strip() and not c.strip().startswith(("name", "map"))
                    )
                    lbl = {"id": "primary key", "unique": "unique", "index": "index"}[bname]
                    model["index_notes"].append(f"({_cell(cols)}) {lbl}" if cols else lbl)
                continue
            fm = _PRISMA_FIELD.match(raw)
            if not fm:
                continue
            fname, base, is_list, is_opt, attrs = (
                fm.group(1), fm.group(2), bool(fm.group(3)),
                bool(fm.group(4)), fm.group(5) or "",
            )
            if base in _PRISMA_SCALARS or base in enum_names:
                typ = base + ("[]" if is_list else "")
                null = "yes" if is_opt else "no"
                default = _prisma_default(attrs)
                has_id = re.search(r"(?<!@)@id\b", attrs) is not None
                has_unique = re.search(r"(?<!@)@unique\b", attrs) is not None
                has_map = re.search(r"(?<!@)@map\s*\(", attrs) is not None
                model["columns"].append(
                    (fname, typ, null, default, has_id, has_unique, has_map))
                if has_id:
                    model["index_notes"].append(f"`{_cell(fname)}` primary key")
                if has_unique:
                    model["index_notes"].append(f"`{_cell(fname)}` unique")
            elif base in model_names:
                disp = base + ("[]" if is_list else "")
                model["relations"].append((fname, disp))
                rel_arg = _prisma_call_arg(attrs, "relation")
                if rel_arg:
                    lm = re.search(r"fields\s*:\s*\[([^\]]*)\]", rel_arg)
                    if lm:
                        first = lm.group(1).split(",")[0].strip()
                        if first:
                            model["fk_map"][first] = base
            elif base in composite_names:
                model["composite_fields"].append((fname, base + ("[]" if is_list else "")))
            # else: an unresolved type — omitted (best-effort, point 2).
        models.append(model)
    return models, enums, composites


def extract_node_data_model_prisma(root: Path, docs_dir: str) -> tuple[str, dict]:
    """Data model from Prisma `schema.prisma` — the committed projection, peer to
    Ruby's `db/schema.rb` (point 2). Scalar-typed fields (incl. `String[]`) are
    the six-column entity rows; model-typed fields are relations (omitted, noted);
    the `@relation` FK is attributed to its named scalar column. Sorted by
    (model, field). Multiple schemas: sorted-first rendered, the rest a residual."""
    root = Path(root)
    schemas = _find_prisma_schemas(root)
    if not schemas:
        return _canon(["# Data model", "", NO_EXTRACTOR.rstrip("\n")]), {}
    primary = schemas[0]
    oversize: list = []
    raw = _safe_read_bytes(root, primary, oversize)
    if raw is None:
        return _canon(["# Data model", "", NO_EXTRACTOR.rstrip("\n")]), {}
    sources = {_rel(root, primary): _sha256_hex(raw)}
    models, enums, composites = _parse_prisma(raw.decode("utf-8", errors="replace"))
    if not models:
        return _canon(["# Data model", "", NO_EXTRACTOR.rstrip("\n")]), sources

    rows: list = []
    mapped_cols: list = []
    for m in models:
        for (col, typ, null, default, _hid, _hun, has_map) in m["columns"]:
            rows.append((m["name"], col, typ, null, default,
                         m["fk_map"].get(col, "—")))
            if has_map:
                mapped_cols.append(f"{m['name']}.{col}")
    rows.sort(key=lambda r: (r[0], r[1]))

    out = ["# Data model", "",
           f"_Derived from `{_cell(_rel(root, primary))}` (Prisma schema; static parse, "
           "no Prisma executed)._", "",
           f"## Entities ({len(models)} models)", "",
           "| table | column | type | null | default | fk |",
           "|---|---|---|---|---|---|"]
    for table, col, typ, null, default, fk in rows:
        out.append(f"| {_cell(table)} | `{_cell(col)}` | {_cell(typ)} | {null} "
                   f"| {_cell(default)} | {_cell(fk)} |")
    out.append("")

    idx_lines = []
    for m in sorted(models, key=lambda x: x["name"]):
        if m["index_notes"]:
            idx_lines.append(f"- `{_cell(m['name'])}`: " + "; ".join(m["index_notes"]))
    if idx_lines:
        out += ["## Indexes", ""] + idx_lines + [""]

    rel_lines = []
    for m in sorted(models, key=lambda x: x["name"]):
        if m["relations"]:
            joined = ", ".join(
                f"`{_cell(f)}` → `{_cell(t)}`" for f, t in sorted(m["relations"]))
            rel_lines.append(f"- `{_cell(m['name'])}`: {joined}")
    if rel_lines:
        out += ["## Relations", "",
                "_Model-typed fields (not columns):_", ""] + rel_lines + [""]

    if enums:
        out += ["## Enums", ""]
        for name, vals in sorted(enums):
            joined = ", ".join(f"`{_cell(v)}`" for v in vals)
            out.append(f"- `{_cell(name)}`: {joined}" if vals else f"- `{_cell(name)}`")
        out.append("")

    res: list = []
    if mapped_cols or any(m["mapped"] for m in models):
        renamed = []
        for m in models:
            if m["mapped"]:
                renamed.append(f"model `{_cell(m['name'])}` → `{_cell(m['mapped'])}`")
        for mc in mapped_cols:
            renamed.append(f"column `{_cell(mc)}`")
        res.append("`@@map`/`@map` renames not applied — declared names rendered "
                   "(a named residual): " + ", ".join(renamed) + ".")
    if composites:
        res.append("Composite/embedded `type` blocks not rendered as entities "
                   "(a named residual): "
                   + ", ".join(f"`{_cell(c)}`" for c in composites) + ".")
    if len(schemas) > 1:
        res.append("Additional `schema.prisma` files not rendered (single-primary "
                   "posture): "
                   + ", ".join(f"`{_cell(_rel(root, p))}`" for p in schemas[1:]) + ".")
    if oversize:
        res.append("Skipped for exceeding the 2 MB bound: "
                   + ", ".join(f"`{_cell(p)}`" for p in sorted(oversize)))
    if res:
        out += ["## Residuals", ""] + [f"- {r}" for r in res] + [""]
    return _canon(out), sources


# ── TypeORM: a `.ts` with `@Entity(` → `@Column`/`@PrimaryGeneratedColumn` ─────

def _detect_node_scan(root: Path, want: tuple, exts: tuple | None = None) -> bool:
    """True if any contained JS/TS file (optionally filtered to `exts`) contains
    one of `want` on comment-scrubbed text — BOUNDED by the aggregate file/byte
    caps (point 6). The detect phase must not scan an unbounded tree on a repo
    with no marker; a marker past the cap degrades that probe to the stub, the
    same tradeoff the extractor's own aggregate cap makes."""
    root = Path(root)
    n_files = n_bytes = 0
    for p in _walk_js_files(root):
        if exts and not str(p).endswith(exts):
            continue
        if n_files >= _MAX_SCAN_FILES or n_bytes >= _MAX_SCAN_BYTES:
            break
        raw = _safe_read_bytes(root, p, [])
        if raw is None:
            continue
        n_files += 1
        n_bytes += len(raw)
        if any(w in _scrub_js_comments(raw.decode("utf-8", errors="replace")) for w in want):
            return True
    return False


def _detect_node_typeorm(root: Path) -> bool:
    """A `.ts`/`.tsx` file containing `@Entity(` (gated on `@Entity(` so
    `sequelize-typescript`'s `@Table`/`@Column` is owned by Sequelize, point 2)."""
    return _detect_node_scan(Path(root), ("@Entity(",), (".ts", ".tsx"))


def _ts_prop_type(s: str) -> str | None:
    m = re.search(r":\s*([A-Za-z_][\w<>\[\]. |]*?)\s*[;=]?\s*$", s)
    return m.group(1).strip() if m else None


def _parse_typeorm(text: str) -> list:
    """Parse `@Entity` classes into (name, columns, fks). `@Column`/
    `@PrimaryGeneratedColumn`/`@PrimaryColumn` are columns; `@ManyToOne(() => X)`
    + `@JoinColumn({name})` is the FK (point 2)."""
    text = _scrub_js_comments(text)
    entities: list = []
    cur = None
    pending: list = []
    is_entity = False
    entity_name = None
    for raw in text.split("\n"):
        s = raw.strip()
        if not s:
            continue
        cm = re.match(r"(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+([A-Za-z_]\w*)", s)
        if cm:
            if is_entity:
                cur = {"name": entity_name or cm.group(1), "columns": [], "fks": []}
                entities.append(cur)
            else:
                cur = None
            pending, is_entity, entity_name = [], False, None
            continue
        if s.startswith("@"):
            pending.append(s)
            em = re.match(r"@Entity\s*\(\s*(?:(['\"])([^'\"]*)\1)?", s)
            if em:
                is_entity = True
                entity_name = em.group(2) or None
            continue
        pm = re.match(r"([A-Za-z_]\w*)\s*[!?]?\s*:", s)
        if pm and cur is not None:
            prop = pm.group(1)
            decs = " ".join(pending)
            pending = []
            if any(d in decs for d in ("@Column", "@PrimaryGeneratedColumn", "@PrimaryColumn")):
                typ = _ts_prop_type(s)
                if not typ:
                    tm = re.search(r"type\s*:\s*['\"]([^'\"]+)['\"]", decs)
                    typ = tm.group(1) if tm else "—"
                null = "yes" if re.search(r"nullable\s*:\s*true", decs) else "no"
                dm = re.search(r"(?<!\w)default\s*:\s*([^,}]+)", decs)
                default = dm.group(1).strip().strip("'\"") if dm else "—"
                pk = ("@PrimaryGeneratedColumn" in decs) or ("@PrimaryColumn" in decs)
                cur["columns"].append((prop, typ, null, default, pk))
            if "@ManyToOne" in decs or "@OneToOne" in decs:
                rm = re.search(r"@(?:ManyToOne|OneToOne)\s*\(\s*\(\)\s*=>\s*([A-Za-z_]\w*)", decs)
                jm = re.search(r"@JoinColumn\s*\(\s*\{[^}]*name\s*:\s*['\"]([^'\"]+)['\"]", decs)
                if rm:
                    cur["fks"].append((jm.group(1) if jm else prop, rm.group(1)))
            continue
        pending = []
    return entities


def extract_node_data_model_typeorm(root: Path, docs_dir: str) -> tuple[str, dict]:
    """Data model from TypeORM `@Entity` classes (point 2). Entity table only; the
    `@ManyToOne`+`@JoinColumn` FK is attributed to its named scalar column. Every
    scanned `.ts`/`.js` is hashed (point 6). Sorted by (entity, field)."""
    root = Path(root)
    entries, sources, residuals = _scan_node_files(root)
    entities: list = []
    for _rel_path, raw in entries:
        entities.extend(_parse_typeorm(raw.decode("utf-8", errors="replace")))
    if not entities:
        return _canon(["# Data model", "", NO_EXTRACTOR.rstrip("\n")]), sources

    rows: list = []
    pk_notes: list = []
    for e in entities:
        fkmap = {c: r for c, r in e["fks"]}
        present = {c[0] for c in e["columns"]}
        for c, r in e["fks"]:
            if c not in present:
                rows.append((e["name"], c, "—", "no", "—", r))
        pks = []
        for (col, typ, null, default, pk) in e["columns"]:
            rows.append((e["name"], col, typ, null, default, fkmap.get(col, "—")))
            if pk:
                pks.append(col)
        if pks:
            pk_notes.append(f"- `{_cell(e['name'])}`: "
                            + ", ".join(f"`{_cell(c)}` primary key" for c in pks))
    rows.sort(key=lambda r: (r[0], r[1]))

    out = ["# Data model", "",
           "_Derived from TypeORM `@Entity` classes (static parse, no TS "
           "executed)._", "",
           f"## Entities ({len(entities)} entities)", "",
           "| table | column | type | null | default | fk |",
           "|---|---|---|---|---|---|"]
    for table, col, typ, null, default, fk in rows:
        out.append(f"| {_cell(table)} | `{_cell(col)}` | {_cell(typ)} | {null} "
                   f"| {_cell(default)} | {_cell(fk)} |")
    out.append("")
    if pk_notes:
        out += ["## Indexes", ""] + pk_notes + [""]
    if residuals:
        out += ["## Residuals", ""] + [f"- {r}" for r in residuals] + [""]
    return _canon(out), sources


# ── Sequelize: `sequelize.define("X", {…})` or a `Model` subclass `.init({…})` ─

def _detect_node_sequelize(root: Path) -> bool:
    """A JS/TS file calling `sequelize.define(` or `.init(` (point 2)."""
    return _detect_node_scan(Path(root), ("sequelize.define", ".init("))


def _js_object_from(text: str, idx: int):
    """The first balanced `{…}` at/after `idx`: (inner text, end index) or
    (None, idx). Brace depth only — strings-with-braces are a residual (point 6)."""
    i = text.find("{", idx)
    if i < 0:
        return None, idx
    depth, start = 0, i
    while i < len(text):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1:i], i + 1
        i += 1
    return None, idx


def _parse_seq_attrs(inner: str) -> list:
    """Parse a Sequelize attribute map into (col, type, null, default, fk)."""
    cols: list = []
    for part in _split_top_level(inner):
        km = re.match(r"\s*(['\"]?)([A-Za-z_]\w*)\1\s*:\s*(.*)$", part, re.DOTALL)
        if not km:
            continue
        col, val = km.group(2), km.group(3)
        tm = re.search(r"(?:DataTypes|Sequelize)\.([A-Za-z_]\w*)", val)
        typ = tm.group(1) if tm else "—"
        am = re.search(r"allowNull\s*:\s*(true|false)", val)
        null = ("yes" if am.group(1) == "true" else "no") if am else "—"
        dm = re.search(r"defaultValue\s*:\s*([^,\n}]+)", val)
        default = dm.group(1).strip().strip("'\"") if dm else "—"
        rm = re.search(r"references\s*:\s*\{[^}]*model\s*:\s*['\"]?([A-Za-z_]\w*)", val)
        fk = rm.group(1) if rm else "—"
        cols.append((col, typ, null, default, fk))
    return cols


def _parse_sequelize(text: str) -> list:
    """Parse `sequelize.define("X", {…})` and `X.init({…})` into (model, cols)."""
    text = _scrub_js_comments(text)
    models: list = []
    for m in re.finditer(r"sequelize\.define\s*\(\s*(['\"])([^'\"]+)\1\s*,", text):
        inner, _ = _js_object_from(text, m.end())
        if inner is not None:
            models.append((m.group(2), _parse_seq_attrs(inner)))
    for m in re.finditer(r"(?<![\w.])([A-Za-z_]\w*)\.init\s*\(", text):
        inner, _ = _js_object_from(text, m.end())
        if inner is not None:
            models.append((m.group(1), _parse_seq_attrs(inner)))
    return models


def extract_node_data_model_sequelize(root: Path, docs_dir: str) -> tuple[str, dict]:
    """Data model from Sequelize `sequelize.define`/`.init` attribute maps
    (point 2). Every scanned `.ts`/`.js` is hashed (point 6). Sorted by
    (model, field)."""
    root = Path(root)
    entries, sources, residuals = _scan_node_files(root)
    models: list = []
    for _rel_path, raw in entries:
        models.extend(_parse_sequelize(raw.decode("utf-8", errors="replace")))
    if not models:
        return _canon(["# Data model", "", NO_EXTRACTOR.rstrip("\n")]), sources

    rows: list = []
    for name, cols in models:
        for (col, typ, null, default, fk) in cols:
            rows.append((name, col, typ, null, default, fk))
    rows.sort(key=lambda r: (r[0], r[1]))
    if not rows:
        return _canon(["# Data model", "", NO_EXTRACTOR.rstrip("\n")]), sources

    out = ["# Data model", "",
           "_Derived from Sequelize `define`/`init` attribute maps (static parse, "
           "no Node executed)._", "",
           f"## Entities ({len(models)} models)", "",
           "| table | column | type | null | default | fk |",
           "|---|---|---|---|---|---|"]
    for table, col, typ, null, default, fk in rows:
        out.append(f"| {_cell(table)} | `{_cell(col)}` | {_cell(typ)} | {null} "
                   f"| {_cell(default)} | {_cell(fk)} |")
    out.append("")
    if residuals:
        out += ["## Residuals", ""] + [f"- {r}" for r in residuals] + [""]
    return _canon(out), sources


# ── Mongoose: `mongoose.Schema({…})` / `new mongoose.Schema({…})` / ──────
# `new Schema({…})` (dev loop 3, ADR-0096 clause 8's Mongoose debt). Appended
# LAST in the data-model chain, after Prisma, TypeORM and Sequelize, so no
# repository's current resolution changes. Detected and read entirely through
# the tree-sitter parse tree shared with the api-surface route scan — never a
# regular expression over authored source (clause 1). Every scanned `.ts`/`.js`
# is hashed via `_scan_node_files` (point 6).

def _is_mongoose_schema_call(node, src: bytes) -> bool:
    """True when `node` is `mongoose.Schema(...)`, `new mongoose.Schema(...)`,
    or `new Schema(...)` — matched on tree SHAPE (a call/new expression whose
    callee resolves to `mongoose.Schema` or the bare `Schema` identifier),
    never on source text."""
    if node.type == "call_expression":
        obj, prop = _ts_member_parts(node.child_by_field_name("function"))
        return (obj is not None and prop is not None
                and _ts_text(obj, src) == "mongoose" and _ts_text(prop, src) == "Schema")
    if node.type == "new_expression":
        ctor = node.child_by_field_name("constructor")
        if ctor is None:
            return False
        if ctor.type == "member_expression":
            obj, prop = _ts_member_parts(ctor)
            return (obj is not None and prop is not None
                    and _ts_text(obj, src) == "mongoose" and _ts_text(prop, src) == "Schema")
        return ctor.type == "identifier" and _ts_text(ctor, src) == "Schema"
    return False


def _detect_node_mongoose(root: Path) -> bool:
    """A JS/TS source calling `mongoose.Schema(`, `new mongoose.Schema(`, or
    `new Schema(` — matched on the parse TREE (point above), BOUNDED by the
    same aggregate scan caps `_detect_node_scan` uses (a marker past the cap
    degrades this probe to the stub, the same tradeoff every detector here
    makes)."""
    root = Path(root)
    n_files = n_bytes = 0
    for p in _walk_js_files(root):
        if n_files >= _MAX_SCAN_FILES or n_bytes >= _MAX_SCAN_BYTES:
            break
        raw = _safe_read_bytes(root, p, [])
        if raw is None:
            continue
        n_files += 1
        n_bytes += len(raw)
        rel = _rel(root, p)
        tree = _js_parse(rel, raw)
        for node in _ts_nodes(tree.root_node):
            if _is_mongoose_schema_call(node, raw):
                return True
    return False


def _find_mongoose_schemas(tree, src: bytes) -> list:
    """`(schema_ident, definition_object_node)` for every schema call bound to
    a `const`/`let`/`var` identifier. `mongoose.Schema({…}, {options})` and
    `new mongoose.Schema(...)`/`new Schema(...)` are all recognized identically
    (point above); `definition_object_node` is `None` when the first argument
    is not an object literal (e.g. a computed spread) — the caller renders no
    fields for it rather than guessing."""
    out: list = []
    for node in _ts_nodes(tree.root_node):
        if node.type != "variable_declarator":
            continue
        value = node.child_by_field_name("value")
        if value is None or not _is_mongoose_schema_call(value, src):
            continue
        name_node = node.child_by_field_name("name")
        if name_node is None or name_node.type != "identifier":
            continue
        args = _ts_call_args(value)
        def_obj = args[0] if args and args[0].type == "object" else None
        out.append((_ts_text(name_node, src), def_obj))
    return out


def _find_mongoose_model_calls(tree, src: bytes) -> dict:
    """`{schema_ident: model_name}` from `mongoose.model('<Name>', schemaIdent)`
    calls. A call whose second argument is not a bare identifier, or whose
    first is not a static string, contributes nothing — the caller falls back
    to the schema identifier and names the residual."""
    out: dict = {}
    for node in _ts_nodes(tree.root_node):
        if node.type != "call_expression":
            continue
        obj, prop = _ts_member_parts(node.child_by_field_name("function"))
        if obj is None or prop is None:
            continue
        if _ts_text(obj, src) != "mongoose" or _ts_text(prop, src) != "model":
            continue
        args = _ts_call_args(node)
        if len(args) < 2 or args[1].type != "identifier":
            continue
        name = _ts_static_string(args[0], src)
        if name:
            out[_ts_text(args[1], src)] = name
    return out


def _find_mongoose_discriminators(tree, src: bytes) -> set:
    """Schema identifiers carrying a `.discriminator(` call — never rendered as
    an entity, always a named residual."""
    out: set = set()
    for node in _ts_nodes(tree.root_node):
        if node.type != "call_expression":
            continue
        obj, prop = _ts_member_parts(node.child_by_field_name("function"))
        if obj is None or prop is None or obj.type != "identifier":
            continue
        if _ts_text(prop, src) == "discriminator":
            out.add(_ts_text(obj, src))
    return out


def _mongoose_type_cell(node, src: bytes) -> str | None:
    """The rendered `type` cell for a `type:` value node: an identifier's own
    text (`String`), or a member expression's LAST property
    (`mongoose.SchemaTypes.ObjectId` → `ObjectId`). `None` when the value has
    no static shape of either kind (an inline array/object type — the caller
    names it a residual rather than inventing a type)."""
    if node is None:
        return None
    if node.type == "identifier":
        return _ts_text(node, src)
    if node.type == "member_expression":
        _obj, prop = _ts_member_parts(node)
        return _ts_text(prop, src) if prop is not None else None
    return None


def _mongoose_default_cell(node, src: bytes) -> str:
    """The rendered `default` cell: a string's static value, or a boolean/
    number/`null` literal's own source text. A function or other expression
    default has no static value and renders `—` — evaluating it would mean
    executing the schema module."""
    val = _ts_static_string(node, src)
    if val is not None:
        return val
    if node.type in ("true", "false", "number", "null"):
        return _ts_text(node, src)
    return "—"


def _mongoose_fields(def_obj, src: bytes) -> tuple:
    """`(rows, residuals)` for one schema definition object's top-level pairs.

    `rows` is `[(field, type, null, default, fk)]`. A field whose value is a
    bare type (`name: String`, the shorthand form) is a column with no
    `required`/`default`/`ref`. A field whose value is an object carrying a
    `type:` key is the full descriptor form: `required: true` → `null = no`
    (else `yes`); `default:` → the default cell; `ref: '<Model>'` → the fk
    cell. A field whose value is an object with NO `type:` key is a nested
    path — a named residual, never silently dropped. An array-valued field
    (`tags: [String]`, an array of sub-schemas) is likewise a named residual,
    never a column."""
    rows: list = []
    residuals: list = []
    if def_obj is None:
        return rows, residuals
    for pair in def_obj.named_children:
        if pair.type != "pair":
            continue
        key_node = pair.child_by_field_name("key")
        value_node = pair.child_by_field_name("value")
        if key_node is None or value_node is None:
            continue
        field = (_ts_text(key_node, src) if key_node.type == "property_identifier"
                 else (_ts_static_string(key_node, src) or _ts_text(key_node, src)))
        if value_node.type in ("identifier", "member_expression"):
            typ = _mongoose_type_cell(value_node, src)
            if typ:
                rows.append((field, typ, "yes", "—", "—"))
            else:
                residuals.append(f"nested path `{_cell(field)}`")
            continue
        if value_node.type == "array":
            residuals.append(f"array field `{_cell(field)}`")
            continue
        if value_node.type != "object":
            residuals.append(f"nested path `{_cell(field)}`")
            continue
        has_type_key = False
        typ = None
        required = False
        default = "—"
        fk = "—"
        for sub in value_node.named_children:
            if sub.type != "pair":
                continue
            sk = sub.child_by_field_name("key")
            sv = sub.child_by_field_name("value")
            if sk is None or sv is None:
                continue
            sk_text = (_ts_text(sk, src) if sk.type == "property_identifier"
                       else (_ts_static_string(sk, src) or _ts_text(sk, src)))
            if sk_text == "type":
                has_type_key = True
                typ = _mongoose_type_cell(sv, src)
            elif sk_text == "required":
                required = sv.type == "true"
            elif sk_text == "default":
                default = _mongoose_default_cell(sv, src)
            elif sk_text == "ref":
                ref = _ts_static_string(sv, src)
                if ref:
                    fk = ref
        if not has_type_key:
            residuals.append(f"nested path `{_cell(field)}`")
            continue
        rows.append((field, typ or "—", "no" if required else "yes", default, fk))
    return rows, residuals


def extract_node_data_model_mongoose(root: Path, docs_dir: str) -> tuple[str, dict]:
    """Data model from Mongoose `mongoose.Schema`/`new Schema` definitions
    (dev loop 3, the ADR-0096 clause 8 Mongoose debt). Appended LAST in the
    data-model chain — after Prisma, TypeORM and Sequelize — so no
    repository's current resolution changes. Detection and reading both go
    through the tree-sitter parse tree the api-surface route scan already
    declares; no probe below opens a source file (postcondition (c)). Model
    name from `mongoose.model('<Name>', schemaIdent)`; a schema with no such
    call falls back to its own identifier, named as a residual. Nested paths,
    array/embedded fields, and `.discriminator(...)` calls are named
    residuals, never silently dropped. Sorted by (model, field)."""
    root = Path(root)
    entries, sources, residuals = _scan_node_files(root)

    schema_defs: dict = {}          # ident -> definition object node (first wins)
    schema_src: dict = {}           # ident -> the bytes it was parsed from
    schema_order: list = []
    model_names: dict = {}          # ident -> resolved model name
    discriminated: set = set()

    for rel, raw in entries:
        tree = _js_parse(rel, raw)
        for ident, def_obj in _find_mongoose_schemas(tree, raw):
            if ident not in schema_defs:
                schema_defs[ident] = def_obj
                schema_src[ident] = raw
                schema_order.append(ident)
        model_names.update(_find_mongoose_model_calls(tree, raw))
        discriminated |= _find_mongoose_discriminators(tree, raw)

    if not schema_defs:
        return _canon(["# Data model", "", NO_EXTRACTOR.rstrip("\n")]), sources

    rows: list = []
    unresolved_names: list = []
    field_residuals: list = []
    for ident in schema_order:
        model = model_names.get(ident)
        if not model:
            model = ident
            unresolved_names.append(ident)
        f_rows, f_res = _mongoose_fields(schema_defs[ident], schema_src[ident])
        for field, typ, null, default, fk in f_rows:
            rows.append((model, field, typ, null, default, fk))
        for note in f_res:
            field_residuals.append(f"`{_cell(model)}` {note}")

    if not rows:
        return _canon(["# Data model", "", NO_EXTRACTOR.rstrip("\n")]), sources
    rows.sort(key=lambda r: (r[0], r[1]))

    out = ["# Data model", "",
           "_Derived from Mongoose `Schema`/`.model` definitions (parse-tree "
           "read, no Node executed)._", "",
           f"## Entities ({len(rows)} fields across {len(schema_order)} models)",
           "",
           "| table | column | type | null | default | fk |",
           "|---|---|---|---|---|---|"]
    for table, col, typ, null, default, fk in rows:
        out.append(f"| {_cell(table)} | `{_cell(col)}` | {_cell(typ)} | {null} "
                   f"| {_cell(default)} | {_cell(fk)} |")
    out.append("")

    if unresolved_names:
        residuals.append(
            "Model name not resolved via `mongoose.model(...)` — the schema "
            "identifier rendered instead (a named residual): "
            + ", ".join(f"`{_cell(n)}`" for n in sorted(unresolved_names)) + ".")
    if discriminated:
        residuals.append(
            "`.discriminator(...)` calls not rendered as entities (a named "
            "residual): "
            + ", ".join(f"`{_cell(n)}`" for n in sorted(discriminated)) + ".")
    residuals += field_residuals
    if residuals:
        out += ["## Residuals", ""] + [f"- {r}" for r in residuals] + [""]
    return _canon(out), sources


# ── module-graph: the explicit TS/JS import graph, resolve-or-drop (point 3) ───
# Strip COMMENTS ONLY (a module specifier IS a quoted string literal captured by
# the import pattern, so strings are not scrubbed). Relative specifiers resolve
# via a fixed extension ladder, FIRST HIT WINS; root-`tsconfig.json` baseUrl+paths
# string-literal aliases resolve; bare packages drop (resolve-or-drop). No
# `extends`/`references`/`package.json` `exports` — a recorded residual.

_JS_LADDER_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
# The `import…from` gap excludes the quote and `;` but NOT the newline (a
# multiline named import spans lines), so it is BOUNDED at 500 chars — an
# unbounded `*?` scans to EOF from every `import` anchor (O(n²)); 500 chars
# covers any realistic import head while keeping each anchor's scan O(1).
_JS_IMPORT_FROM = re.compile(r"""import\b[^;'"`]{0,500}?\bfrom\s*(['"`])([^'"`\n]+)\1""")
_JS_EXPORT_FROM = re.compile(r"""export\b[^;'"`]{0,500}?\bfrom\s*(['"`])([^'"`\n]+)\1""")
_JS_SIDE_EFFECT = re.compile(r"""import\s*(['"`])([^'"`\n]+)\1""")
_JS_REQUIRE = re.compile(r"""require\s*\(\s*(['"`])([^'"`\n]+)\1\s*\)""")
_JS_DYNIMPORT = re.compile(r"""import\s*\(\s*(['"`])([^'"`\n]+)\1\s*\)""")


def _js_specifiers(scrubbed: str) -> list:
    """Every module specifier in a comment-scrubbed source: `import…from`,
    side-effect `import`, `export…from`, `require(…)`, dynamic `import(…)`."""
    specs: list = []
    for pat in (_JS_IMPORT_FROM, _JS_EXPORT_FROM, _JS_REQUIRE,
                _JS_DYNIMPORT, _JS_SIDE_EFFECT):
        for m in pat.finditer(scrubbed):
            specs.append(m.group(2))
    return specs


def _ladder(base: str, resolved_map: dict) -> str | None:
    """First-hit-wins extension ladder over `base` (a normalized path with no
    assumed extension): exact → .ts → .tsx → .js → .jsx → .mjs → .cjs →
    /index.<ext>. Returns the resolved repo-relative path or None (point 3)."""
    cands = ([base] + [base + e for e in _JS_LADDER_EXTS]
             + [os.path.join(base, "index" + e) for e in _JS_LADDER_EXTS])
    for c in cands:
        try:
            rp = Path(c).resolve()
        except OSError:
            continue
        if rp in resolved_map:
            return resolved_map[rp]
    return None


def _load_tsconfig_aliases(root: Path):
    """Root `tsconfig.json` `compilerOptions.baseUrl`+`paths` string aliases
    (point 3). Returns (rules, base_dir, residual, sources). `extends`/`references`
    → aliases deferred + residual. The read tsconfig is hashed (point 6)."""
    p = Path(root) / "tsconfig.json"
    if not (p.is_file() and _contained(Path(root), p)):
        return [], Path(root), "", {}
    raw = _safe_read_bytes(Path(root), p, [])
    if raw is None:
        return [], Path(root), "", {}
    sources = {_rel(Path(root), p): _sha256_hex(raw)}
    text = _scrub_js_comments(raw.decode("utf-8", errors="replace"))
    text = re.sub(r",(\s*[}\]])", r"\1", text)          # tolerate trailing commas.
    try:
        doc = json.loads(text)
    except Exception:
        return ([], Path(root),
                "`tsconfig.json` present but unparseable; path aliases not applied "
                "(a named residual).", sources)
    if not isinstance(doc, dict):
        return [], Path(root), "", sources
    if "extends" in doc or "references" in doc:
        return ([], Path(root),
                "`tsconfig.json` uses `extends`/`references` (multi-project); path "
                "aliases deferred (a named residual).", sources)
    co = doc.get("compilerOptions")
    if not isinstance(co, dict):
        return [], Path(root), "", sources
    base_url = co.get("baseUrl") if isinstance(co.get("baseUrl"), str) else "."
    base_dir = Path(root) / base_url
    rules: list = []
    paths = co.get("paths")
    if isinstance(paths, dict):
        for pat, targets in sorted(paths.items()):
            if (isinstance(pat, str) and isinstance(targets, list)
                    and targets and isinstance(targets[0], str)):
                rules.append((pat, targets[0]))
    return rules, base_dir, "", sources


def _resolve_js_spec(importer: Path, spec: str, resolved_map: dict,
                     alias_rules: list, base_dir: Path) -> str | None:
    """Resolve one specifier to a repo-relative file (point 3). Relative → ladder
    off the importer dir; alias → ladder off `base_dir`; bare → None (drop)."""
    if spec.startswith("."):
        base = os.path.normpath(os.path.join(str(importer.parent), spec))
        return _ladder(base, resolved_map)
    for pat, tgt in alias_rules:
        if pat.endswith("/*") and "*" in tgt:
            prefix = pat[:-1]
            if spec.startswith(prefix):
                expanded = tgt.replace("*", spec[len(prefix):], 1)
                r = _ladder(os.path.normpath(os.path.join(str(base_dir), expanded)),
                            resolved_map)
                if r:
                    return r
        elif "*" not in pat and spec == pat:
            r = _ladder(os.path.normpath(os.path.join(str(base_dir), tgt)),
                        resolved_map)
            if r:
                return r
    return None


def _detect_node_module_graph(root: Path) -> bool:
    return _has_js_sources(Path(root))


def extract_node_module_graph(root: Path, docs_dir: str) -> tuple[str, dict]:
    """Explicit TS/JS import graph, resolve-or-drop (point 3). Nodes are the
    in-repo scanned modules; an edge A→B is emitted only when a specifier in A
    resolves (extension ladder / tsconfig alias) to an in-repo module B — bare
    packages drop. Every scanned file is hashed (point 6); the render is bounded
    by `_MAX_GRAPH_EDGES`."""
    root = Path(root)
    entries, sources, residuals = _scan_node_files(root)
    if not entries:
        return _canon(["# Module graph", "", NO_EXTRACTOR.rstrip("\n")]), {}

    resolved_map: dict = {}
    for rel, _text in entries:
        try:
            resolved_map[(root / rel).resolve()] = rel
        except OSError:
            continue
    alias_rules, base_dir, ts_residual, ts_sources = _load_tsconfig_aliases(root)
    sources.update(ts_sources)

    edges: set = set()
    for rel, raw in entries:
        importer = root / rel
        text = raw.decode("utf-8", errors="replace")
        for spec in _js_specifiers(_scrub_js_comments(text)):
            tgt = _resolve_js_spec(importer, spec, resolved_map, alias_rules, base_dir)
            if tgt and tgt != rel:
                edges.add((rel, tgt))

    graph_residuals: list = []
    all_edges = sorted(edges)
    if len(all_edges) > _MAX_GRAPH_EDGES:
        graph_residuals.append(
            f"module graph truncated: {len(all_edges)} edges exceed the "
            f"{_MAX_GRAPH_EDGES}-edge render bound")
        all_edges = all_edges[:_MAX_GRAPH_EDGES]
    edges = set(all_edges)

    nodes = sorted(rel for rel, _ in entries)
    node_ids = _node_ids(nodes)
    out = ["# Module graph", "",
           f"_Static TS/JS import graph of {len(nodes)} modules, {len(edges)} "
           "edges (resolve-or-drop; static parse, no Node executed)._",
           "", "```mermaid", "graph LR"]
    for a, b in sorted(edges):
        out.append(f'  {node_ids[a]}["{_mermaid(a)}"] --> {node_ids[b]}["{_mermaid(b)}"]')
    out += ["```", ""]
    if not edges:
        out += ["_No resolved import edges detected._", ""]
    connected = {a for a, _ in edges} | {b for _, b in edges}
    isolated = sorted(n for n in nodes if n not in connected)
    if isolated:
        out += [f"## Isolated modules ({len(isolated)})", "",
                "_No resolved import edge (leaf or standalone):_", "",
                ", ".join(f"`{_cell(c)}`" for c in isolated), ""]
    notes = list(residuals)
    if ts_residual:
        notes.append(ts_residual)
    notes += graph_residuals
    if notes:
        out += ["## Residuals", ""] + [f"- {n}" for n in notes] + [""]
    return _canon(out), sources


# ─────────────────────── pack interface (ADR-0096 clause 12) ────────────────

#: Empty: the core's marker scan prunes no directory from this pack's own
#: `detect` calls, so this pack's marker semantics are unchanged.
DETECT_EXCLUDE: frozenset[str] = frozenset()


def detect(root: Path) -> DetectResult:
    """The node stack-marker detector: a `package.json` at the repository root.

    `matched` is exactly what the pre-split `_detect_node` predicate returned
    (`(root / "package.json").exists()`); `markers` names the repo-relative
    marker path that fired, sorted, so an `ambiguous_stack` verdict can cite it
    (ADR-0096 clause 6). No marker → an empty tuple.
    """
    matched = (root / "package.json").exists()
    markers = ("package.json",) if matched else ()
    return DetectResult(matched=matched, markers=markers)


# The node pack's declared input class per concern (ADR-0097 part 1). `kind`
# lives on each probe below (`probes()`), not in this map.
# `decision-index` is the universal concern the core declares and binds.
#
# **api-surface is met by a real parser and names the grammar it needs.** The
# `parser` tuple is what `core.resolve_declared_parsers` refuses on and what
# `core.parser_pins` records into the byte-compared provenance manifest, so the
# thing that decides the rendered routes is in the ledger beside them. Its
# DERIVED class is `parser`: `openapi.json` is `committed-artifact` and the
# route scan is `parser`, and `parser` is the weaker of the two.
#
# **The ADR-0097 roster, named here rather than restated per probe below.** The
# Prisma, TypeORM and Sequelize data-model readers, and the module-graph
# reader, match patterns against authored source and declare
# `regex-over-source` — all four are on the ADR-0097 roster. The Mongoose
# reader (dev loop 3) is the exception: it is met entirely through the
# tree-sitter parse tree the api-surface route scan already declares, so it
# declares `parser`. data-model's DERIVED class is therefore
# `regex-over-source` — the minimum over a chain mixing three regex rungs with
# one parser rung; module-graph's is `regex-over-source` outright, its only
# rung. Closing the three regex readers is the pack's remaining follow-on.
_JS_SOURCE_GLOBS = (
    "*.js", "*.jsx", "*.mjs", "*.cjs", "*.ts", "*.tsx",
    "**/*.js", "**/*.jsx", "**/*.mjs", "**/*.cjs", "**/*.ts", "**/*.tsx",
    "package.json", "**/package.json", "tsconfig.json", "**/tsconfig.json",
)

INPUT_CLASSES = {
    "data-model": InputClass(
        expected="`prisma/schema.prisma`, else TypeORM `@Entity` classes, else Sequelize model definitions, else Mongoose `Schema` definitions",
        globs=("prisma/schema.prisma", "**/schema.prisma") + _JS_SOURCE_GLOBS,
        parser=_NODE_TS_PARSER_MODULES,
    ),
    "api-surface": InputClass(
        expected="a committed OpenAPI document, else Express/Fastify verb calls or NestJS route decorators",
        globs=("openapi.json", "swagger.json", "docs/openapi.json",
               "swagger/v1/swagger.json", "doc/openapi.json") + _JS_SOURCE_GLOBS,
        parser=_NODE_TS_PARSER_MODULES,
    ),
    "module-graph": InputClass(
        expected="resolvable TS/JS import or require specifiers between the repository's own modules",
        globs=_JS_SOURCE_GLOBS,
    ),
}


def probes() -> dict[str, list[Probe]]:
    """The node pack's ordered per-concern probe registry (ADR-0068).

    Within each concern the first matching probe wins, and each probe self-
    degrades to the stub. api-surface: committed OpenAPI → the single
    receiver-agnostic route scan. data-model: Prisma `schema.prisma` → TypeORM
    `@Entity` → Sequelize `define`/`init` → Mongoose `Schema`/`.model` (dev
    loop 3, appended last so no repository's current resolution changes).
    module-graph: the explicit TS/JS import graph (resolve-or-drop).

    `decision-index` is deliberately absent: it is the universal probe
    (`core.extract_decision_index`) that the core binds for every pack.
    """
    return {
        "data-model": [
            Probe(_detect_node_prisma, extract_node_data_model_prisma,
                  kind="regex-over-source"),
            Probe(_detect_node_typeorm, extract_node_data_model_typeorm,
                  kind="regex-over-source"),
            Probe(_detect_node_sequelize, extract_node_data_model_sequelize,
                  kind="regex-over-source"),
            Probe(_detect_node_mongoose, extract_node_data_model_mongoose,
                  kind="parser"),
        ],
        "api-surface": [
            Probe(_detect_node_openapi, extract_node_api_surface_openapi,
                  kind="committed-artifact"),
            Probe(_detect_node_routes, extract_node_api_surface_routes,
                  kind="parser"),
        ],
        "module-graph": [
            Probe(_detect_node_module_graph, extract_node_module_graph,
                  kind="regex-over-source"),
        ],
    }
