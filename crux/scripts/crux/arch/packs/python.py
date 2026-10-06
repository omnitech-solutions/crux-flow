"""The Python stack pack (ADR-0066 point 8).

Three stdlib-only, deterministic extractors: SQLAlchemy declarative models plus
the Alembic migration timeline (data-model), a committed root `openapi.json`
grouped by tag (api-surface), and the detected package's import graph
(module-graph). The universal decision-index probe is NOT bound here — it lives
in `core.extract_decision_index` and the core binds it for every pack.

The target application is never imported: this is AST and JSON only, so no
target code runs. Every file read is hashed into `sources`, so a changed source
drifts the spine.

HISTORY — true of the U7 move, not of the tree as it stands. It moved here
verbatim under ADR-0096 clause 12, whose gate was byte-identity: every corpus
golden unchanged across the split, and not a line rewritten in transit. That
claim is scoped to that move and is still true OF IT.

Two things the same paragraph used to assert are NOT true of this file today:
  * "the missing Django ORM probe … is follow-on work" — it shipped.
    `_scan_orm_models` reads `models.Model` subclasses, `extract_python_data_model`
    renders a `## Django models` section, and two corpus goldens carry one
    (`djangoproject-com`, 55 models; `wagtail-bakerydemo`, 7).
  * "not a line was rewritten" — later units rewrote lines here. The parse-failure
    handling (`_PARSE_FAILED`), the literal-eliding view renderer (`_ref_text`)
    and the `down_revision` cell escape are all new code in this module.
The single-package restriction on package detection does still stand.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path, PurePosixPath

from ..core import (
    NO_EXTRACTOR,
    DetectResult,
    InputClass,
    Probe,
    StubReason,
    Verdict,
    _HTTP_METHODS,
    _MAX_SCAN_BYTES,
    _MAX_SCAN_FILES,
    _PARSE_FAILED,
    _SKIP_DIRS,
    _canon,
    _cell,
    _extract_module_graph,
    _iter_py_files,
    _rel,
    _render_openapi,
    _safe_read_bytes,
    _sha256_hex,
    _topo_order,
)


# ─────────────────────── python stack pack (ADR-0066 point 8) ───────────────
# Three stdlib-only, deterministic extractors. Every file read is hashed into
# `sources`; the app is never imported (AST + JSON only), so no target code runs.


# `_PARSE_FAILED` — the four classes `ast.parse` raises on
# repository-controlled source — is imported from `core` above rather than
# defined here. It lived in this module and covered its five parse sites; the
# crux pack's `_cli_verbs` then became a sixth site in another module, could not
# see the constant, and shipped catching `SyntaxError` alone. The contract, the
# four classes and the reason a narrowed handler is a regression are spelled at
# the definition in `core`.


def _unparsed_residual(rels) -> str:
    """The residual sentence naming files Python's own parser refused.

    Spelled once so all three concerns report the degradation identically, in
    the shape `packs/elixir.py` uses for its scrub bails.
    """
    return ("Source(s) Python's parser refused — malformed, or nested past the "
            "parser's own limits (hashed, not parsed): "
            + ", ".join(f"`{_cell(r)}`" for r in sorted(rels)) + ".")



# ── module-graph: detect the package, then reuse the parameterized engine ────

def _pyproject_pkg_name(root: Path, d: Path) -> str | None:
    """The `[project].name` (or `[tool.poetry].name`) hint from `<d>/pyproject.toml`.

    Parsed with stdlib `tomllib`, never matched against the source text — the
    same rule `_setup_py_pkg_name` follows below, and for the same reason:
    `module-graph` declares the input class `parser`, and clause 1 admits a real
    parser over a declared input while it prohibits a regular expression.

    There was a `name = "..."` regex here as a fallback. It ran not only when
    `tomllib` raised but whenever the parse SUCCEEDED and neither key was
    present, because control simply left the `try` — so a `pyproject.toml` whose
    only `name =` sat under `[[tool.mypy.overrides]]` returned `'sqlalchemy.*'`
    as the package name. `detect_packages` short-circuits on the first name that
    resolves, so that stray match produced the silent single wrong pick clause 6
    forbids. `tomllib` is stdlib on the pinned `>=3.13`, so the fallback bought
    nothing that could justify it. A file that does not parse, or one that
    declares neither key, declares nothing here — the caller falls through to
    `setup.py` rather than guessing.

    `root` is the REPOSITORY root and `d` the manifest's own directory; the two
    differ for every tier-1b candidate. The read goes through `_safe_read_bytes`
    against `root`, so a `d` reached through a symlinked directory — which
    `_manifest_dirs` accepts, because `Path.is_dir()` is true for one — resolves
    outside the root and declares nothing (ADR-0068/ADR-0069 clause 6).
    """
    p = d / "pyproject.toml"
    if not p.is_file():
        return None
    raw = _safe_read_bytes(root, p, [])
    if raw is None:
        return None
    text = raw.decode("utf-8", errors="replace")
    try:
        import tomllib  # noqa: PLC0415  — stdlib since 3.11; the pinned floor is 3.13.
    except ImportError:
        return None
    try:
        data = tomllib.loads(text)
    except (*_PARSE_FAILED, TypeError):
        # `_PARSE_FAILED` plus `TypeError`, and the union is not belt-and-braces.
        # `TOMLDecodeError` subclasses `ValueError`, which is why the handler was
        # written as `(ValueError, TypeError)` — but `tomllib` is a RECURSIVE-
        # DESCENT parser, so a `pyproject.toml` carrying `a = [[[…]]]` 2000 deep
        # raises `RecursionError` from `tomllib/_parser.py::parse_array`, past
        # that tuple and out of the derive. Measured: `derive-arch.py --dry-run`
        # exited 2 with empty stdout — the environment-failure code, on the
        # invocation `.github/workflows/check-arch-drift.yml` runs, from one
        # committed file. `_PARSE_FAILED` is the tuple that names this class for
        # every parser in the engine; `TypeError` stays because this reader also
        # guards against a non-str `text`, which is not a parse failure.
        return None
    proj = data.get("project")
    if isinstance(proj, dict) and proj.get("name"):
        return str(proj["name"])
    tool = data.get("tool")
    if isinstance(tool, dict):
        poetry = tool.get("poetry")
        if isinstance(poetry, dict) and poetry.get("name"):
            return str(poetry["name"])
    return None


def _resolve_pkg_dir(root: Path, name: str) -> tuple[Path, str] | None:
    """Map a distribution name to an import package dir: `src/<name>/__init__.py`
    then `<name>/__init__.py` (with `-`→`_` normalization)."""
    norm = name.replace("-", "_")
    for base in (root / "src", root):
        cand = base / norm
        if cand.is_dir() and (cand / "__init__.py").is_file():
            return cand, norm
    return None


def _setup_py_pkg_name(root: Path, d: Path) -> str | None:
    """The `name=` argument of a `setup(...)` call in `<d>/setup.py`, or None.

    Parsed with `ast`, never matched against the source text: `setup.py` is
    authored Python, and clause 1 admits a real parser over authored source
    while it prohibits a regular expression. A file that does not parse, or one
    whose `name=` is computed rather than literal, declares nothing here — the
    caller falls through rather than guessing.

    "Does not parse" is `_PARSE_FAILED`, which includes the parser's own
    `RecursionError` and `MemoryError`. This is the one of the five parse sites
    with no residual channel to record into: the function answers a single
    yes/no question about one file, and its whole contract is already to return
    None when it cannot answer. Falling through is the honest stub here.

    Containment is `_pyproject_pkg_name`'s, for the reason stated there: `d` can
    be a symlinked directory, so the read is gated against the repository root
    rather than against `d`.
    """
    p = d / "setup.py"
    if not p.is_file():
        return None
    raw = _safe_read_bytes(root, p, [])
    if raw is None:
        return None
    try:
        tree = ast.parse(raw.decode("utf-8", errors="replace"))
    except _PARSE_FAILED:
        return None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        called = fn.id if isinstance(fn, ast.Name) else (
            fn.attr if isinstance(fn, ast.Attribute) else None
        )
        if called != "setup":
            continue
        kw = _kw_node(node, "name")
        if isinstance(kw, ast.Constant) and isinstance(kw.value, str) and kw.value:
            return kw.value
    return None


def _declared_pkg_name(root: Path, d: Path) -> str | None:
    """The package name a manifest in `d` declares — pyproject first, then
    setup.py. One directory, one declaration; the caller resolves it against
    that same directory. Both reads are contained under the repository `root`."""
    return _pyproject_pkg_name(root, d) or _setup_py_pkg_name(root, d)


def _manifest_dirs(root: Path, max_depth: int = 2) -> list[Path]:
    """Every directory at depth 1..`max_depth` below `root` carrying a
    `pyproject.toml` or a `setup.py`, breadth-first and sorted.

    The root itself is excluded — it is tier 1's venue, and including it here
    would make tier 1b re-answer a hint tier 1 already declined. `_SKIP_DIRS`
    and dot-directories are pruned, so a vendored `.venv` or `node_modules`
    never contributes a declaration.
    """
    out: list[Path] = []
    frontier = [root]
    for _depth in range(max_depth):
        nxt: list[Path] = []
        for parent in frontier:
            try:
                children = sorted(
                    (c for c in parent.iterdir()
                     if c.is_dir() and not c.name.startswith(".")
                     and c.name not in _SKIP_DIRS),
                    key=lambda p: p.name,
                )
            except OSError:
                continue
            for child in children:
                if (child / "pyproject.toml").is_file() or (child / "setup.py").is_file():
                    out.append(child)
                nxt.append(child)
        frontier = nxt
    return out


def detect_packages(root: Path) -> tuple[tuple[Path, str], ...]:
    """EVERY candidate import package, in the pack's preference order.

    ADR-0096 clause 6: "package detection reports every candidate package". The
    pre-clause-6 detector answered `(pkg, name)` or None, which collapsed two
    different repositories into the same silence — `fastapi-fullstack`, where
    nothing matched, and `djangoproject-com`, where twelve did. Reporting the
    set is what lets those be told apart, and `concern_ambiguity` below is what
    says so in the recorded channel.

    Three tiers, and the first two short-circuit:

      1. a root `pyproject.toml` `[project].name`/`[tool.*]` hint that RESOLVES
         to a real package directory — the project named its own package, so
         there is one candidate BY DECLARATION and no ambiguity to report;
      1b. otherwise every `pyproject.toml`/`setup.py` at depth 1 or 2 below the
         root whose declared name RESOLVES **against its own directory**. One
         resolving declaration short-circuits exactly as tier 1 does; two or
         more are all reported, so clause 6's no-silent-pick rule holds there
         too;
      2. otherwise the UNION of every `src/<name>/__init__.py` under a `src/`
         layout and every top-level `<name>/__init__.py`, sorted by name.

    **Tier 1b is anchored on a declaration, never on a depth-2 union.** The
    repository it targets is `fastapi-fullstack`, whose `backend/pyproject.toml`
    declares `name = "app"` beside `backend/app/__init__.py`: one candidate by
    declaration. A blanket depth-2 package union was measured against the same
    repository and answers two — `backend/app` AND `backend/tests` — which is
    `ambiguous_package`, so the widening that looks more thorough fails to fix
    the very repository it was written for. Resolving each declaration against
    its own directory is also what keeps `svc/pyproject.toml` naming `svc-core`
    from binding a same-named package at the repository root.

    Tier 1b answers nothing on `djangoproject-com`, and that is the intended
    outcome rather than a gap: it carries no nested manifest at any depth, its
    root manifest names a package resolving to no directory, and its sixteen
    top-level packages are co-equal Django apps. It stays `ambiguous_package`
    naming all sixteen. Clause 6 is satisfied — nothing is picked silently — and
    deriving a module graph per package is a spine-shape change this unit does
    not make.

    **Tier 2 is a union rather than a first-non-empty preference.** The
    single-package detector this replaced returned early on exactly one `src/`
    package and otherwise fell through to the top-level scan, so a repository
    with two `src/` packages and one top-level package reported the top-level
    one. Carrying that shape forward inverted it — the first-non-empty form
    reported the two `src/` packages and never saw the third. Both drop a real
    candidate, and clause 6 asks for every candidate, so neither is right: a
    project laying out `src/alpha`, `src/beta` and `gamma` has three, and
    reporting fewer is the silent pick the clause forbids. Nothing else changes,
    because `concern_ambiguity` already reports a multi-candidate set rather
    than choosing within it.

    Sorted by package name, with the directory as the tiebreak, so a `src/`
    package and a top-level package sharing a name order deterministically. The
    two are distinct import roots, and reporting both is what makes that
    collision visible as an ambiguity rather than resolving it by tier.

    **Tier 2 stays root-only.** Clause 6's depth-two scan is about STACK
    markers, and a depth-widened tier 2 moves corpus repositories this unit did
    not name. Tier 1b reaches deeper only where a manifest says which package
    lives there, so the widening is bounded by a declaration rather than by a
    filesystem walk.
    """
    root = Path(root)
    hint = _pyproject_pkg_name(root, root)
    if hint:
        resolved = _resolve_pkg_dir(root, hint)
        if resolved is not None:
            return (resolved,)

    nested: list[tuple[Path, str]] = []
    for d in _manifest_dirs(root):
        name = _declared_pkg_name(root, d)
        if not name:
            continue
        resolved = _resolve_pkg_dir(d, name)
        if resolved is not None:
            nested.append(resolved)
    if nested:
        return tuple(sorted(nested, key=lambda pair: (pair[1], str(pair[0]))))

    candidates: list[Path] = []
    src = root / "src"
    if src.is_dir():
        candidates.extend(
            d for d in src.iterdir()
            if d.is_dir() and (d / "__init__.py").is_file()
        )
    try:
        candidates.extend(
            d for d in root.iterdir()
            if d.is_dir() and not d.name.startswith(".")
            and d.name not in _SKIP_DIRS and (d / "__init__.py").is_file()
        )
    except OSError:
        pass
    return tuple((d, d.name)
                 for d in sorted(candidates, key=lambda p: (p.name, str(p))))


def detect_package(root: Path) -> tuple[Path, str] | None:
    """The project's SINGLE primary import package, or None (ADR-0066 point 8).

    The narrow accessor over `detect_packages`, and the one the module-graph
    extractor uses: a graph over one of several candidate packages would be a
    silent pick. None here means "not exactly one", and the two ways that
    happens are told apart by `concern_ambiguity`, not by this signature.
    """
    found = detect_packages(root)
    return found[0] if len(found) == 1 else None


def concern_ambiguity(concern: str, root: Path):
    """The python pack's clause 6 ambiguity verdict for one concern, or None.

    One concern has one: `module-graph`, whose input is a package and which has
    more than one candidate on a repository laid out as several sibling
    applications. `djangoproject-com` is the measured case — roughly fifteen
    Django apps side by side at the repository root.

    ZERO candidates is deliberately NOT reported here. It is an absent input,
    which is `precondition_missing`; calling it an ambiguity would reuse a reason
    for a second condition, which clause 3 forbids.

    Package names are directory names, so they are repo content; `_cell` escapes
    them exactly as the stub renderer requires.
    """
    if concern != "module-graph":
        return None
    found = detect_packages(Path(root))
    if len(found) < 2:
        return None
    named = ", ".join(_cell(name) for _dir, name in found)
    return Verdict.stubbed(
        StubReason.AMBIGUOUS_PACKAGE,
        expected="one importable package to graph, or a recorded choice among the candidates",
        found=f"{len(found)} candidate packages — {named}",
    )


def _detect_python_module_graph(root: Path) -> bool:
    return detect_package(Path(root)) is not None


def extract_python_module_graph(root: Path, docs_dir: str) -> tuple[str, dict]:
    """Module graph for the detected package (ADR-0066 point 8), via the same
    engine the crux probe uses. Stub when no single package is discernible."""
    found = detect_package(Path(root))
    if found is None:
        return _canon(["# Module graph", "", NO_EXTRACTOR.rstrip("\n")]), {}
    pkg, pkg_name = found
    return _extract_module_graph(Path(root), pkg, pkg_name)



def _detect_python_api_surface(root: Path) -> bool:
    return (Path(root) / "openapi.json").is_file()




def extract_python_api_surface(root: Path, docs_dir: str) -> tuple[str, dict]:
    """API surface from a committed `openapi.json` at the repo root (ADR-0066
    point 8). Operations are grouped by first `tag` (untagged → path prefix),
    sorted by group, then path, then HTTP method. The app is never imported —
    live route introspection is deferred. Stub when `openapi.json` is absent."""
    root = Path(root)
    spec = root / "openapi.json"
    if not spec.is_file():
        return _canon(["# API surface", "", NO_EXTRACTOR.rstrip("\n")]), {}
    raw = _safe_read_bytes(root, spec, [])
    if raw is None:
        # A symlinked `openapi.json` whose target escapes the root, or one over
        # the 2 MB bound. Neither is read, so the concern stubs.
        return _canon(["# API surface", "", NO_EXTRACTOR.rstrip("\n")]), {}
    sources = {_rel(root, spec): _sha256_hex(raw)}
    try:
        doc = json.loads(raw.decode("utf-8"))
    except Exception:
        return _canon(["# API surface", "", NO_EXTRACTOR.rstrip("\n")]), sources

    md = _render_openapi(doc, "_Derived from `openapi.json`._")
    if md is None:
        return _canon(["# API surface", "", NO_EXTRACTOR.rstrip("\n")]), sources
    return md, sources


# ══ api-surface, second probe: an `ast` parse of the project's own sources ═══
#
# ADR-0096 clause 1 admits `parser` as an input class, and this is the python
# pack's use of it: the stdlib `ast` module, so nothing is pinned and no version
# reaches the provenance manifest. **No regular expression is matched against
# authored source anywhere below.** Every fact here is read off a syntax tree.
#
# Why the concern's DECLARATION moves with the probe. Dev loop 1 narrowed the
# api-surface glob to `openapi.json` alone, with the reason recorded in the
# declaration itself: the probe checked the repository root and nowhere else, so
# naming a wider search would have put a location in the stub line that nothing
# ever looked at. Widening where the concern looks reverses that argument
# exactly, so the glob widens with it, and `artifact=` is what keeps the emitted
# document distinguishable from the authored sources beside it in one glob set.
#
# Three shapes are recognized, and the seam between them is a decorator or a
# `urlpatterns` list rather than a dependency marker — a repository can carry
# `fastapi` in its manifest and declare no route, and the manifest is not what
# this reads.
#
#   FastAPI  `@router.<verb>("/path")` / `@app.<verb>(…)`, with mount prefixes
#            composed across `include_router(x, prefix=…)`.
#   Flask    `@app.route("/path", methods=[…])` and the `@app.<verb>` shortcut,
#            with `Blueprint(url_prefix=…)` and `register_blueprint`.
#   Django   `urlpatterns` elements `path()` / `re_path()` / `url()`, with
#            `include()` composing prefixes across urls modules.
#
# **An unresolvable prefix is rendered verbatim and named, never guessed.** The
# measured case is the FastAPI template's `app.include_router(api_router,
# prefix=settings.API_V1_STR)`: a name whose value is decided at import time by
# a settings object, which a static parse cannot evaluate. It renders as
# `${settings.API_V1_STR}` and earns a residual. The alternative — assuming the
# conventional `/api/v1` — is the failure the node baseline already made once,
# where 9 of 9 extracted paths were confidently wrong.

#: The rendering of a path segment whose value this parse cannot evaluate. The
#: braces are not decoration: `{param}` is a FastAPI path parameter and
#: `<int:year>` is a Django converter, so an unresolved segment needs a spelling
#: neither framework uses.
def _unresolved(text: str) -> str:
    return "${" + text + "}"


_FASTAPI_FACTORIES = frozenset({"APIRouter", "FastAPI"})
_FLASK_FACTORIES = frozenset({"Blueprint", "Flask"})
_URLCONF_CALLS = frozenset({"path", "re_path", "url"})

#: Django resolves a request to a view by URL alone; the HTTP method is decided
#: inside the view, so there is no method to report and this says so.
_NO_METHOD = "—"

_ROUTES_UNDER_COUNT = (
    "This table is a floor, not a census. A static parse sees a route only where "
    "a decorator or a `urlpatterns` element DECLARES it; a route registered by "
    "`add_api_route`, `add_url_rule`, a loop, or an application factory is not "
    "declared anywhere this parse can read."
)

_ROUTES_CONDITIONAL = (
    "A `urlpatterns` assignment nested inside an `if` block is read "
    "unconditionally, because the condition is evaluated at import time and this "
    "parse evaluates nothing. A development-only pattern therefore appears here."
)


def _kw_node(call: ast.Call, key: str):
    """The value node of keyword `key` on `call`, or None."""
    for kw in call.keywords:
        if kw.arg == key:
            return kw.value
    return None


def _unparse(node) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return "<unparsable>"


def _dotted_callee(node) -> str | None:
    """The dotted name of a callee when it is a pure identifier chain.

    `RedirectView.as_view` renders as `RedirectView.as_view`. Anything that is
    not `Name`/`Attribute` the whole way down returns None, because a subscript
    or a nested call in callee position can itself hold a string literal — which
    is the payload `_ref_text` exists to drop.
    """
    parts: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if not isinstance(cur, ast.Name):
        return None
    parts.append(cur.id)
    return ".".join(reversed(parts))


def _carries_literal(call: ast.Call) -> bool:
    """True when any argument of `call` holds a constant AT ANY DEPTH.

    The walk is the point. Checking only direct arguments would pass
    `as_view(url='https://x/?token=' + T)`, where the direct argument is a
    `BinOp` and the literal sits one level down — the same hole a shallow check
    always leaves.
    """
    for arg in [*call.args, *(kw.value for kw in call.keywords)]:
        for sub in ast.walk(arg):
            if isinstance(sub, ast.Constant):
                return True
    return False


def _ref_text(node) -> str:
    """A view/expression rendering that names the REFERENCE and drops literals.

    `ast.unparse` reproduces a string literal VERBATIM, so rendering a view
    expression whole republishes whatever the route literal holds. A Django
    URLconf is an ordinary Python module, and projects do put credentials in one
    — `as_view(url='https://internal/?token=…')` is the measured shape. That
    string then lands in `api-surface.md`, which is a COMMITTED artifact, and
    `tools/sync_stage.py` republishes the corpus goldens to the public repo. The
    80-char bound on `_view_text` did not help: a short secret survived intact
    and a long one survived truncated with its identifying prefix — a prefix is
    the part that makes a leaked key actionable.

    So the elide is by CLASS, not by length. A call carrying any literal renders
    as `Dotted.callee(…)`: identifiers only, never a value. That keeps the fact
    the table exists to convey — which view serves which path — and removes the
    channel. Every other pack already renders identifiers only; this brings the
    Python pack to the same rule.

    A call carrying no literal is unparsed as before, because it is identifiers
    already and reads better in full.
    """
    if isinstance(node, ast.Call) and _carries_literal(node):
        dotted = _dotted_callee(node.func) or _base_name(node.func) or "<view>"
        return f"{dotted}(…)"
    return _unparse(node)


def _segment(node) -> tuple[str, str | None]:
    """`(rendered, unresolved_expression)` for one path segment.

    A string constant renders itself and resolves. Anything else renders its own
    source text inside `${…}` and is returned as a residual, so the reader sees
    the expression the project wrote rather than a value crux invented.

    "Its own source text" is `_ref_text`, which elides the arguments of a call
    that carries a literal. Both returned values needed it, not just the first:
    the second is the residual, and `_RouteScan.residual_lines` renders the
    unresolved prefixes into `api-surface.md` verbatim — so leaving the residual
    unelided would have kept the payload channel open through the residual while
    closing it in the table.
    """
    literal = _const_str(node)
    if literal is not None:
        return literal, None
    text = _ref_text(node)
    return _unresolved(text), text


def _join_segments(*parts: str) -> str:
    """Concatenate path segments, collapsing runs of `/` that the join created.

    A plain string operation over a string this module built, not a pattern over
    anything the project authored.
    """
    joined = "".join(p for p in parts if p)
    while "//" in joined:
        joined = joined.replace("//", "/")
    return joined


def _rooted(path: str) -> str:
    """Ensure a rendered path starts at the site root, unless it opens with an
    unresolved segment (where the leading `/` belongs to that value, not here)."""
    if not path:
        return "/"
    if path.startswith("/") or path.startswith("${"):
        return path
    return "/" + path


def _decorator_methods(dec: ast.Call) -> tuple[str, ...]:
    """The `methods=[…]` of a Flask `route` decorator; `("GET",)` when absent —
    which is Flask's own default."""
    node = _kw_node(dec, "methods")
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        named = {s.upper() for s in (_const_str(e) for e in node.elts) if s}
        if named:
            return tuple(sorted(named))
    return ("GET",)


def _route_decorators(fn) -> list:
    """`(router_var, METHOD, path, handler)` per route decorator on `fn`.

    Guarded exactly as the node pack's verb-call scan is: the first argument must
    be a string literal starting with `/`. That is what separates
    `@router.get("/items")` from a decorator named `get` on something that is not
    a router, and it is a structural test rather than a name heuristic.
    """
    out = []
    for dec in fn.decorator_list:
        if not isinstance(dec, ast.Call) or not dec.args:
            continue
        func = dec.func
        if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)):
            continue
        path = _const_str(dec.args[0])
        if path is None or not path.startswith("/"):
            continue
        var, attr = func.value.id, func.attr
        if attr in _HTTP_METHODS:
            out.append((var, attr.upper(), path, fn.name))
        elif attr == "route":
            for method in _decorator_methods(dec):
                out.append((var, method, path, fn.name))
    return out


def _include_target(node):
    """The dotted urls-module a Django `include(…)` names, or None.

    `include("blog.urls")` and `include(("blog.urls", "blog"))` both resolve;
    `include(some_list)` and `include(debug_toolbar.urls)` do not, and the caller
    records those as residuals rather than dropping them silently.
    """
    if not isinstance(node, ast.Call):
        return None
    if _base_name(node.func) != "include" or not node.args:
        return None
    first = node.args[0]
    literal = _const_str(first)
    if literal is not None:
        return literal
    if isinstance(first, (ast.Tuple, ast.List)) and first.elts:
        return _const_str(first.elts[0])
    return None


def _is_include(node) -> bool:
    return isinstance(node, ast.Call) and _base_name(node.func) == "include"


def _view_text(node) -> str:
    """A readable rendering of a Django view REFERENCE, length-bounded.

    `_ref_text`, not `_unparse`: see its docstring for why a length bound is not
    a containment control for a string literal. The 80-char bound stays as a
    row-width backstop over the identifiers that survive the elide.
    """
    text = _ref_text(node)
    return text if len(text) <= 80 else text[:79] + "…"


def _collect_urlpatterns(value, rows: list, mounts: list, merges: list) -> None:
    """Split one `urlpatterns` value into endpoint rows, mounts, and merges.

    `urlpatterns = other_urlpatterns + [...]` is a Django idiom and is how the
    measured corpus repository composes its documentation host's URLconf, so a
    collector that reads only a bare list drops that module whole. The addition
    is walked on both sides; a bare name on either side is a MERGE — another
    module's whole `urlpatterns` arriving at this module's root, which is an
    `include` with no prefix and is resolved as one.
    """
    if isinstance(value, ast.BinOp) and isinstance(value.op, ast.Add):
        _collect_urlpatterns(value.left, rows, mounts, merges)
        _collect_urlpatterns(value.right, rows, mounts, merges)
        return
    if isinstance(value, ast.Name):
        merges.append(value.id)
        return
    if not isinstance(value, (ast.List, ast.Tuple)):
        return
    for elt in value.elts:
        if not isinstance(elt, ast.Call) or not elt.args:
            continue
        call = _base_name(elt.func)
        if call not in _URLCONF_CALLS:
            continue
        pattern = elt.args[0]
        target = elt.args[1] if len(elt.args) > 1 else None
        if _is_include(target):
            mounts.append((call, pattern, target))
        elif target is not None:
            rows.append((call, pattern, _view_text(target)))


def _scan_route_module(tree: ast.Module) -> dict:
    """Every route-relevant declaration in one parsed module.

    Walked rather than read off `tree.body`, so a router or a `urlpatterns`
    assignment nested in an `if` or a function is seen. `conditional` records
    when a `urlpatterns` assignment was NOT at module scope, which is what lets
    the renderer name that residual only where it applies.
    """
    top = {id(stmt) for stmt in tree.body}
    aliases: dict = {}
    routers: dict = {}
    routes: list = []
    mounts: list = []
    url_rows: list = []
    url_mounts: list = []
    url_merges: list = []
    has_urlpatterns = False
    conditional = False

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    aliases[alias.asname] = ("import", alias.name)
                elif "." not in alias.name:
                    aliases[alias.name] = ("import", alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                if alias.name == "*":
                    continue
                aliases[alias.asname or alias.name] = (
                    "from", node.level, module, alias.name,
                )
        elif isinstance(node, ast.Assign):
            if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                continue
            name = node.targets[0].id
            if name == "urlpatterns":
                has_urlpatterns = True
                conditional = conditional or id(node) not in top
                _collect_urlpatterns(node.value, url_rows, url_mounts, url_merges)
            elif isinstance(node.value, ast.Call):
                factory = _base_name(node.value.func)
                if factory in _FASTAPI_FACTORIES:
                    routers[name] = ("fastapi", _kw_node(node.value, "prefix"))
                elif factory in _FLASK_FACTORIES:
                    routers[name] = ("flask", _kw_node(node.value, "url_prefix"))
        elif isinstance(node, ast.AugAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "urlpatterns":
                has_urlpatterns = True
                conditional = conditional or id(node) not in top
                _collect_urlpatterns(node.value, url_rows, url_mounts, url_merges)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            routes.extend(_route_decorators(node))
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and node.args:
                if func.attr == "include_router":
                    mounts.append(("fastapi", func.value, node.args[0],
                                   _kw_node(node, "prefix")))
                elif func.attr == "register_blueprint":
                    mounts.append(("flask", func.value, node.args[0],
                                   _kw_node(node, "url_prefix")))

    return {
        "aliases": aliases,
        "routers": routers,
        "routes": routes,
        "mounts": mounts,
        "url_rows": url_rows,
        "url_mounts": url_mounts,
        "url_merges": url_merges,
        "has_urlpatterns": has_urlpatterns,
        "conditional": conditional,
    }


def _dotted_index(rels) -> dict:
    """Every dotted suffix a repo-relative module path can be imported as.

    The import root of a repository is not knowable statically — the FastAPI
    template's is `backend/`, and nothing in the tree says so — so a dotted name
    is matched against the SUFFIXES of each file's path rather than against one
    assumed root. Ties resolve to the shallowest path, then codepoint order, so
    two machines index the same tree identically.
    """
    index: dict = {}
    for rel in sorted(rels):
        parts = rel[:-3].split("/")
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        for i in range(len(parts)):
            key = ".".join(parts[i:])
            prior = index.get(key)
            if prior is None or (rel.count("/"), rel) < (prior.count("/"), prior):
                index[key] = rel
    return index


def _resolve_module(rels: set, index: dict, importer: str, level: int, dotted: str):
    """The repo-relative file a dotted import names, or None.

    An absolute import resolves through the suffix index; a relative one walks up
    from the importing file's own directory, which needs no import root at all.
    """
    tail = dotted.replace(".", "/") if dotted else ""
    if level:
        base = PurePosixPath(importer).parent
        for _ in range(level - 1):
            base = base.parent
        stem = str(base)
        stem = tail if stem in ("", ".") else (f"{stem}/{tail}" if tail else stem)
        for candidate in (f"{stem}.py", f"{stem}/__init__.py"):
            if candidate in rels:
                return candidate
        return None
    return index.get(dotted) if dotted else None


class _RouteScan:
    """One repository's route facts, resolved across modules.

    A class rather than a chain of functions because the mount graph is a
    cross-module structure: a router declared in one file is mounted in a second
    and reached from a third, so every step needs the same three tables.
    """

    def __init__(self, root: Path):
        self.root = root
        self.per_file: dict = {}
        self.hashes: dict = {}
        self.residuals: list = []
        self.unresolved_prefixes: set = set()
        self.unresolved_includes: set = set()
        self.cycles: set = set()
        self.oversize: list = []
        self.unparsed: list = []
        self.truncated = False
        self.rows: set = set()
        self._scan()
        self.rels = set(self.per_file)
        self.index = _dotted_index(self.rels)
        self._collect()

    # ── reading ─────────────────────────────────────────────────────────────
    def _scan(self) -> None:
        files = bytes_read = 0
        for py in sorted(_iter_py_files(self.root)):
            if files >= _MAX_SCAN_FILES or bytes_read >= _MAX_SCAN_BYTES:
                self.truncated = True
                break
            raw = _safe_read_bytes(self.root, py, self.oversize)
            if raw is None:
                continue
            files += 1
            bytes_read += len(raw)
            rel = _rel(self.root, py)
            try:
                tree = ast.parse(raw.decode("utf-8", errors="replace"))
            except _PARSE_FAILED:
                # Hash before the `continue`, or the residual below names a path
                # this concern's `inputs_found` does not carry — see
                # `_PARSE_FAILED`. `sources()` adds `unparsed` to what it emits;
                # this dict is keyed by `per_file`, which a refused file never
                # enters.
                self.unparsed.append(rel)
                self.hashes[rel] = _sha256_hex(raw)
                continue
            self.per_file[rel] = _scan_route_module(tree)
            self.hashes[rel] = _sha256_hex(raw)

    # ── router identity ─────────────────────────────────────────────────────
    def _module_of(self, rel: str, alias) -> str | None:
        if alias[0] == "import":
            return _resolve_module(self.rels, self.index, rel, 0, alias[1])
        _, level, module, name = alias
        dotted = f"{module}.{name}" if module else name
        return _resolve_module(self.rels, self.index, rel, level, dotted)

    def _router_by_name(self, rel: str, name: str):
        mod = self.per_file[rel]
        if name in mod["routers"]:
            return (rel, name)
        alias = mod["aliases"].get(name)
        if alias and alias[0] == "from":
            _, level, module, imported = alias
            owner = _resolve_module(self.rels, self.index, rel, level, module)
            if owner in self.per_file and imported in self.per_file[owner]["routers"]:
                return (owner, imported)
        return None

    def _router_by_expr(self, rel: str, expr):
        if isinstance(expr, ast.Name):
            return self._router_by_name(rel, expr.id)
        if isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name):
            alias = self.per_file[rel]["aliases"].get(expr.value.id)
            if alias is None:
                return None
            owner = self._module_of(rel, alias)
            if owner in self.per_file and expr.attr in self.per_file[owner]["routers"]:
                return (owner, expr.attr)
        return None

    # ── composition ─────────────────────────────────────────────────────────
    def _collect(self) -> None:
        self.routers = {
            (rel, name): decl
            for rel, mod in self.per_file.items()
            for name, decl in mod["routers"].items()
        }
        self.parents: dict = {}
        for rel, mod in self.per_file.items():
            for kind, parent_expr, child_expr, prefix in mod["mounts"]:
                parent = self._router_by_expr(rel, parent_expr)
                child = self._router_by_expr(rel, child_expr)
                if parent is None or child is None:
                    self.unresolved_includes.add(
                        f"{_unparse(parent_expr)}.{'include_router' if kind == 'fastapi' else 'register_blueprint'}"
                        f"({_unparse(child_expr)})"
                    )
                    continue
                self.parents.setdefault(child, []).append((parent, prefix))
        self._decorator_rows()
        self._urlconf_rows()

    def _prefixes(self, rid, seen: frozenset) -> list:
        kind, own_node = self.routers[rid]
        own, unresolved = ("", None) if own_node is None else _segment(own_node)
        if unresolved:
            self.unresolved_prefixes.add(unresolved)
        ups = self.parents.get(rid, [])
        if not ups or rid in seen:
            if rid in seen:
                self.cycles.add(f"{rid[0]}:{rid[1]}")
            return [own]
        out = []
        for parent, prefix_node in ups:
            if parent not in self.routers:
                continue
            mount, unresolved = ("", None) if prefix_node is None else _segment(prefix_node)
            if unresolved:
                self.unresolved_prefixes.add(unresolved)
            # Flask's own rule: a `url_prefix` given at registration REPLACES the
            # one the blueprint declared. FastAPI's `include_router` prefix
            # composes with the router's own instead.
            tail = mount if (kind == "flask" and prefix_node is not None) else mount + own
            for base in self._prefixes(parent, seen | {rid}):
                out.append(_join_segments(base, tail))
        return sorted(set(out)) or [own]

    def _decorator_rows(self) -> None:
        for rel, mod in self.per_file.items():
            for var, method, path, handler in mod["routes"]:
                rid = self._router_by_name(rel, var)
                if rid is None:
                    # No declaration binds this receiver to a router in this
                    # repository, so the evidence that it IS one is missing.
                    self.unresolved_includes.add(f"{var}.{method.lower()}({path!r})")
                    continue
                for prefix in self._prefixes(rid, frozenset()):
                    self.rows.add((method, _rooted(_join_segments(prefix, path)), handler))

    # ── django urlconf ──────────────────────────────────────────────────────
    def _urlconf_rows(self) -> None:
        modules = {rel for rel, mod in self.per_file.items() if mod["has_urlpatterns"]}
        if not modules:
            return
        edges: dict = {}
        included: set = set()
        for rel in sorted(modules):
            for call, pattern, include in self.per_file[rel]["url_mounts"]:
                dotted = _include_target(include)
                child = (_resolve_module(self.rels, self.index, rel, 0, dotted)
                         if dotted else None)
                if child not in modules:
                    self.unresolved_includes.add(_unparse(include))
                    continue
                text, unresolved = self._pattern(call, pattern)
                edges.setdefault(rel, []).append((text, child))
                included.add(child)
            for name in self.per_file[rel]["url_merges"]:
                alias = self.per_file[rel]["aliases"].get(name)
                child = None
                if alias and alias[0] == "from" and alias[3] == "urlpatterns":
                    _, level, module, _imported = alias
                    child = _resolve_module(self.rels, self.index, rel, level, module)
                if child not in modules:
                    self.unresolved_includes.add(f"urlpatterns + {_cell(name)}")
                    continue
                # A merge is an include with no prefix: the other module's whole
                # pattern list arrives at this module's own root.
                edges.setdefault(rel, []).append(("", child))
                included.add(child)
        roots = sorted(modules - included)
        for root in roots:
            self._walk_urlconf(root, "", frozenset({root}), edges)

    def _pattern(self, call: str, node) -> tuple[str, str | None]:
        """A urlconf pattern, with a regex form's own anchors removed.

        `re_path(r"^legacy/$")` anchors relative to where it is mounted, so the
        `^` and `$` are positional syntax rather than part of the path; keeping
        them would render `/blog/^legacy/$` for a nested pattern.
        """
        text, unresolved = _segment(node)
        if unresolved is None and call in ("re_path", "url"):
            if text.startswith("^"):
                text = text[1:]
            if text.endswith("$"):
                text = text[:-1]
        if unresolved:
            self.unresolved_prefixes.add(unresolved)
        return text, unresolved

    def _walk_urlconf(self, rel: str, prefix: str, seen: frozenset, edges: dict) -> None:
        for call, pattern, view in self.per_file[rel]["url_rows"]:
            text, _ = self._pattern(call, pattern)
            self.rows.add((_NO_METHOD, _rooted(_join_segments(prefix, text)), view))
        for text, child in edges.get(rel, []):
            if child in seen:
                self.cycles.add(child)
                continue
            self._walk_urlconf(child, _join_segments(prefix, text),
                               seen | {child}, edges)

    # ── reporting ───────────────────────────────────────────────────────────
    def sources(self) -> dict:
        """The hash of every file that contributed a route fact, plus every file
        the parser refused.

        Contributing files only, on the `_scan_sqlalchemy_models` precedent in
        this same pack: hashing every `.py` in the repository would put a test
        helper into `inputs_found` and claim the api-surface read it.

        A refused file is the one addition, and it is not an exception to that
        rule but the same rule applied. `residual_lines` NAMES it, so this
        concern does report on it, and a named-but-unhashed path is absent from
        `inputs_found` — the list `staleness.py` walks. Edit a refused file
        while leaving it broken and the rendered residual is byte-identical, so
        without the hash nothing reports the edit at all.
        """
        keys = ("routers", "routes", "mounts", "url_rows", "url_mounts",
                "url_merges")
        out = {
            rel: self.hashes[rel]
            for rel, mod in self.per_file.items()
            if any(mod[key] for key in keys)
        }
        out.update({rel: self.hashes[rel] for rel in self.unparsed})
        return out

    def residual_lines(self) -> list:
        lines = [_ROUTES_UNDER_COUNT]
        if self.unresolved_prefixes:
            named = ", ".join(f"`{_cell(e)}`" for e in sorted(self.unresolved_prefixes))
            lines.append(
                f"{len(self.unresolved_prefixes)} mount prefix expression(s) are "
                "decided at import time and cannot be evaluated by a parse, so "
                "every path under them renders the expression VERBATIM as "
                f"`${{…}}` rather than a guessed value: {named}."
            )
        if any(mod["conditional"] for mod in self.per_file.values()):
            lines.append(_ROUTES_CONDITIONAL)
        if self.unresolved_includes:
            named = ", ".join(f"`{_cell(e)}`" for e in sorted(self.unresolved_includes))
            lines.append(
                f"{len(self.unresolved_includes)} mount(s) name a target this "
                "repository does not declare, so their routes are absent: "
                f"{named}."
            )
        if self.cycles:
            named = ", ".join(f"`{_cell(c)}`" for c in sorted(self.cycles))
            lines.append(f"A mount cycle was cut at: {named}.")
        if self.oversize:
            named = ", ".join(f"`{_cell(p)}`" for p in sorted(self.oversize))
            lines.append(f"Source(s) above the per-file size bound were not read: {named}.")
        if self.unparsed:
            lines.append(_unparsed_residual(self.unparsed))
        if self.truncated:
            lines.append(
                f"The scan stopped at the {_MAX_SCAN_FILES}-file bound, so sources "
                "sorting after that point were not read."
            )
        return lines


def _detect_python_routes(root: Path) -> bool:
    """Cheap presence check: at least one Python source to parse.

    The probe self-degrades to the stub when the parse finds no route, exactly as
    the node route scan does, so detection stays O(1)-ish and the real question
    is answered once by the extractor rather than twice.
    """
    return next(_iter_py_files(Path(root)), None) is not None


def extract_python_api_surface_routes(root: Path, docs_dir: str) -> tuple[str, dict]:
    """API surface parsed from the project's own Python sources (clause 1).

    FastAPI and Flask decorators plus Django `urlpatterns`, with mount prefixes
    composed across `include_router`, `register_blueprint` and `include`. The
    app is never imported. Stub when no route is declared.

    The table header is the shared `| method | path | … |` every other pack's
    api-surface renderer emits, and that is load-bearing rather than cosmetic:
    `core.count_concern_entities` recognizes a concern's entity table by its
    first two header cells, so a new spelling would count zero entities and
    report `no_entities` over a full table.
    """
    root = Path(root)
    scan = _RouteScan(root)
    if not scan.rows:
        return _canon(["# API surface", "", NO_EXTRACTOR.rstrip("\n")]), {}
    out = ["# API surface", "",
           "_Static `ast` parse of the project's own Python sources (FastAPI and "
           "Flask route decorators, Django `urlpatterns`); the app is never "
           "imported._", "",
           f"## Routes ({len(scan.rows)})", "",
           "| method | path | handler |", "|---|---|---|"]
    for method, path, handler in sorted(scan.rows, key=lambda r: (r[1], r[0], r[2])):
        out.append(f"| {_cell(method)} | `{_cell(path)}` | `{_cell(handler)}` |")
    out.append("")
    out += ["## Residuals", ""] + [f"- {line}" for line in scan.residual_lines()] + [""]
    return _canon(out), scan.sources()


# ── data-model: SQLAlchemy declarative models + the Alembic timeline ─────────

_MODEL_BASES = {"Base", "DeclarativeBase"}


def _base_name(node) -> str:
    """The trailing identifier of a base/callable/subscript AST node."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return _base_name(node.func)
    if isinstance(node, ast.Subscript):
        return _base_name(node.value)
    return ""


def _const_str(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _extract_tablename(stmt) -> str | None:
    if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 \
            and isinstance(stmt.targets[0], ast.Name) and stmt.targets[0].id == "__tablename__":
        return _const_str(stmt.value)
    return None


def _kw_bool(call, key):
    for kw in call.keywords:
        if kw.arg == key and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, bool):
            return kw.value.value
    return None


def _yn(v, default: str) -> str:
    return "yes" if v is True else "no" if v is False else default


def _first_str_arg(call):
    for a in call.args:
        s = _const_str(a)
        if s is not None:
            return s
    return None


def _column_type_and_fk(call) -> tuple[str, str | None]:
    """Extract a readable type string and a ForeignKey target from a
    Column(...)/mapped_column(...) call's positional args."""
    typ = "—"
    fk = None
    type_node = None
    for a in call.args:
        if isinstance(a, ast.Call) and _base_name(a.func) == "ForeignKey":
            fk = _first_str_arg(a) or "ForeignKey"
            continue
        if _const_str(a) is not None:          # a leading column-name override string
            continue
        if type_node is None:
            type_node = a
    if type_node is not None:
        try:
            typ = ast.unparse(type_node)
        except Exception:
            typ = _base_name(type_node) or "—"
    return typ, fk


def _extract_column(stmt):
    """Return (name, type, nullable, pk, fk) for a Column/mapped_column
    assignment, or None."""
    if isinstance(stmt, ast.Assign):
        if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
            return None
        name, value = stmt.targets[0].id, stmt.value
    elif isinstance(stmt, ast.AnnAssign):
        if not isinstance(stmt.target, ast.Name):
            return None
        name, value = stmt.target.id, stmt.value
    else:
        return None
    if name.startswith("__") or not isinstance(value, ast.Call):
        return None
    if _base_name(value.func) not in ("Column", "mapped_column"):
        return None
    typ, fk = _column_type_and_fk(value)
    nullable = _yn(_kw_bool(value, "nullable"), "—")
    pk = _yn(_kw_bool(value, "primary_key"), "no")
    return (name, typ, nullable, pk, fk or "—")


def _classdef_model_info(node: ast.ClassDef):
    """(table, [columns]) when `node` is a SQLAlchemy declarative model
    (a Base/DeclarativeBase subclass or a class carrying `__tablename__`)."""
    is_model = bool({_base_name(b) for b in node.bases} & _MODEL_BASES)
    tablename = None
    columns = []
    for stmt in node.body:
        t = _extract_tablename(stmt)
        if t is not None:
            tablename, is_model = t, True
        col = _extract_column(stmt)
        if col is not None:
            columns.append(col)
    if not is_model:
        return None
    return (tablename or node.name), columns


def _scan_sqlalchemy_models(root: Path, unparsed=None):
    """AST-scan the repo for declarative models. Returns (rows, sources) where a
    row is (table, column, type, nullable, pk, fk); sources hashes every file
    that contributed at least one model.

    The read is `_safe_read_bytes`, matching `_scan_orm_models` below: an
    unbounded `read_bytes()` here both admitted an off-root symlink target's
    tables into `data-model.md` and CRASHED the derive, because the `_rel` that
    records provenance sits outside the parse's `try` and raises on a path that
    resolves outside the root.

    `unparsed` is an out-list, the same shape `_safe_read_bytes` already takes
    for oversize paths: a file the parser refuses is appended there so the
    caller can name it in the concern's residuals. Passing None discards the
    record but never crashes — see `_PARSE_FAILED`.

    A refused file is ALSO hashed into `sources`, and the two are one step
    rather than two: naming a path in the residuals while leaving it out of
    `inputs_found` hides every later edit to it from `staleness.py`. They are
    coupled under the same `if` for that reason — a caller passing None names
    the file nowhere, so hashing it would claim an input this concern never
    reported."""
    rows = []
    sources: dict = {}
    for py in sorted(_iter_py_files(root)):
        raw = _safe_read_bytes(root, py, [])
        if raw is None:
            continue
        try:
            tree = ast.parse(raw.decode("utf-8", errors="replace"))
        except _PARSE_FAILED:
            if unparsed is not None:
                unparsed.append(_rel(root, py))
                sources[_rel(root, py)] = _sha256_hex(raw)
            continue
        has_model = False
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            info = _classdef_model_info(node)
            if info is None:
                continue
            has_model = True
            table, cols = info
            for col in cols:
                rows.append((table, *col))
        if has_model:
            sources[_rel(root, py)] = _sha256_hex(raw)
    return rows, sources


# ══ data-model, second and third shapes: the Django ORM and SQLModel (U-P2) ══
#
# Both read a syntax tree; neither matches a pattern against source text. They
# share one pass over the repository and one row shape — `(table, column, type,
# nullable, pk, fk)` — because the renderer reuses the entity-table header
# `core.count_concern_entities` keys on, and a section with its own spelling
# would count zero entities under a full table.
#
# They also share ONE rule about inheritance, and it is the substantive decision
# here: fields declared on a base class in the SAME MODULE compose onto the
# concrete class. Both frameworks need it, for the same reason and with
# different vocabulary. `class User(UserBase, table=True)` declares 4 members in
# its own body and takes the rest from `UserBase`, which is not itself a table;
# a Django model declares its shared columns on an `abstract = True` base for
# which Django creates no table at all. Rendering the base as its own table
# would name a table that does not exist, and ignoring it would drop a real
# column, so neither of those is available.
#
# The limit is stated rather than hidden: composition is within one module. A
# base imported from elsewhere contributes nothing, and the renderer says so in
# a residual.

_DJANGO_RELATION_FIELDS = frozenset({
    "ForeignKey", "OneToOneField", "ManyToManyField",
})

_DJANGO_IMPLICIT_PK = (
    "Django adds an implicit `id` primary key to a model that declares none, and "
    "the ORM adds it rather than the model, so no row here reports one unless the "
    "model declared `primary_key=True` itself."
)

_INHERITED_FIELDS_RESIDUAL = (
    "Field inheritance is resolved WITHIN one module. A model whose base class is "
    "imported from another module renders only the fields it declares itself, "
    "because resolving the base would mean deciding which of several same-named "
    "classes across the repository the import meant."
)

_SQLMODEL_RELATIONSHIP = (
    "A SQLModel `Relationship()` attribute is an ORM-level link rather than a "
    "column, so it is not a row here. The foreign-key column it travels with is."
)


def _django_app_label(rel: str) -> str:
    """The Django app label a models module belongs to, or "".

    The label is the app package's directory name, which is what Django's own
    default table name is built from. `blog/models.py` and `blog/models/entry.py`
    both label as `blog`.
    """
    names = list(PurePosixPath(rel).parts[:-1])
    if names and names[-1] == "models":
        names = names[:-1]
    return names[-1] if names else ""


def _django_meta(node: ast.ClassDef) -> tuple[str | None, bool]:
    """`(db_table, abstract)` read off a Django model's nested `class Meta`."""
    for stmt in node.body:
        if not (isinstance(stmt, ast.ClassDef) and stmt.name == "Meta"):
            continue
        db_table = None
        abstract = False
        for inner in stmt.body:
            if not (isinstance(inner, ast.Assign) and len(inner.targets) == 1
                    and isinstance(inner.targets[0], ast.Name)):
                continue
            key = inner.targets[0].id
            if key == "db_table":
                db_table = _const_str(inner.value)
            elif key == "abstract" and isinstance(inner.value, ast.Constant):
                abstract = inner.value.value is True
        return db_table, abstract
    return None, False


def _django_field(stmt):
    """`(name, type, nullable, pk, fk)` for a Django model field, or None.

    A field is an assignment whose call is a `…Field` or one of the three
    relation constructors, which is what separates `title = models.CharField(…)`
    from `objects = models.Manager()`.
    """
    if not (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Name)
            and isinstance(stmt.value, ast.Call)):
        return None
    call = stmt.value
    kind = _base_name(call.func)
    if not (kind.endswith("Field") or kind in _DJANGO_RELATION_FIELDS):
        return None
    fk = "—"
    if kind in _DJANGO_RELATION_FIELDS and call.args:
        first = call.args[0]
        literal = _const_str(first)
        fk = literal if literal is not None else _unparse(first)
    # Django's own default is `null=False`, so an undeclared `null` is "no"
    # rather than the unknown "—" the SQLAlchemy column reader reports.
    return (stmt.targets[0].id, kind,
            _yn(_kw_bool(call, "null"), "no"),
            _yn(_kw_bool(call, "primary_key"), "no"),
            fk)


def _django_model_names(classes: dict) -> set:
    """Every class in one module whose bases resolve to `models.Model`.

    A fixpoint rather than one pass: a model may subclass a base declared later
    in the same module, and the answer must not depend on declaration order.
    """
    models: set = set()
    changed = True
    while changed:
        changed = False
        for name, node in classes.items():
            if name in models:
                continue
            for base in node.bases:
                hit = ((isinstance(base, ast.Attribute) and base.attr == "Model")
                       or (isinstance(base, ast.Name) and base.id in models))
                if hit:
                    models.add(name)
                    changed = True
                    break
    return models


def _sqlmodel_is_table(node: ast.ClassDef) -> bool:
    """`class X(Base, table=True)` — the marker SQLModel uses for a real table."""
    for kw in node.keywords:
        if kw.arg == "table" and isinstance(kw.value, ast.Constant) \
                and kw.value.value is True:
            return True
    return False


def _optional_annotation(node) -> bool:
    """True for `X | None` and `Optional[X]`, which is SQLModel's nullable."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _optional_annotation(node.left) or _optional_annotation(node.right)
    if isinstance(node, ast.Constant) and node.value is None:
        return True
    if isinstance(node, ast.Subscript) and _base_name(node.value) == "Optional":
        return True
    return False


def _sqlmodel_field(stmt):
    """`(name, type, nullable, pk, fk)` for a SQLModel column, or None."""
    if not (isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)):
        return None
    name = stmt.target.id
    if name.startswith("__"):
        return None
    value = stmt.value
    if isinstance(value, ast.Call) and _base_name(value.func) == "Relationship":
        return None
    pk, fk = "no", "—"
    if isinstance(value, ast.Call) and _base_name(value.func) == "Field":
        pk = _yn(_kw_bool(value, "primary_key"), "no")
        fk = _const_str(_kw_node(value, "foreign_key")) or "—"
    return (name, _unparse(stmt.annotation),
            "yes" if _optional_annotation(stmt.annotation) else "no", pk, fk)


def _composed_fields(name: str, classes: dict, seen: frozenset, read) -> list:
    """One class's fields, with same-module base classes composed in first.

    A child that redeclares a base's field wins, which is what both frameworks
    do. Bases are walked first so the child's own declaration is the one that
    survives the merge.
    """
    merged: dict = {}
    node = classes[name]
    for base in node.bases:
        if isinstance(base, ast.Name) and base.id in classes and base.id not in seen:
            for field in _composed_fields(base.id, classes, seen | {base.id}, read):
                merged[field[0]] = field
    for stmt in node.body:
        field = read(stmt)
        if field is not None:
            merged[field[0]] = field
    return list(merged.values())


def _scan_orm_models(root: Path, unparsed=None):
    """One pass for both ORMs. Returns `(sqlmodel_rows, django_rows, sources)`.

    Row order follows file order then class name then declaration order; the
    renderer sorts anyway, so this only has to be deterministic.

    `unparsed` is the out-list documented on `_scan_sqlalchemy_models`, and a
    refused file is hashed into `sources` there for the reason given there.
    """
    sqlmodel_rows: list = []
    django_rows: list = []
    sources: dict = {}
    for py in sorted(_iter_py_files(root)):
        raw = _safe_read_bytes(root, py, [])
        if raw is None:
            continue
        rel = _rel(root, py)
        try:
            tree = ast.parse(raw.decode("utf-8", errors="replace"))
        except _PARSE_FAILED:
            if unparsed is not None:
                unparsed.append(rel)
                sources[rel] = _sha256_hex(raw)
            continue
        classes = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
        if not classes:
            continue
        contributed = False

        for name in sorted(classes):
            if not _sqlmodel_is_table(classes[name]):
                continue
            table = _sqlmodel_tablename(classes[name]) or name.lower()
            for field in _composed_fields(name, classes, frozenset({name}),
                                          _sqlmodel_field):
                sqlmodel_rows.append((table, *field))
                contributed = True

        label = _django_app_label(rel)
        for name in sorted(_django_model_names(classes)):
            db_table, abstract = _django_meta(classes[name])
            if abstract:
                continue
            table = db_table or (f"{label}_{name.lower()}" if label else name.lower())
            for field in _composed_fields(name, classes, frozenset({name}),
                                          _django_field):
                django_rows.append((table, *field))
                contributed = True

        if contributed:
            sources[rel] = _sha256_hex(raw)
    return sqlmodel_rows, django_rows, sources


def _sqlmodel_tablename(node: ast.ClassDef) -> str | None:
    for stmt in node.body:
        table = _extract_tablename(stmt)
        if table is not None:
            return table
    return None


def _has_migration(root: Path, d: Path) -> bool:
    """Does `d` hold at least one Alembic revision file?

    `root` is threaded through because `d` is not the containment anchor — the
    repository root is. Without it an off-root symlink inside a `versions/` dir
    could be the sole evidence that selected that directory.
    """
    for py in d.glob("*.py"):
        if py.name.startswith("__"):
            continue
        raw = _safe_read_bytes(root, py, [])
        if raw is None:
            continue
        text = raw.decode("utf-8", errors="replace")
        if "down_revision" in text and "revision" in text:
            return True
    return False


def _detect_alembic_versions(root: Path) -> Path | None:
    """Locate the Alembic migration `versions/` dir: the two conventional
    locations, then a bounded search skipping VCS/venv dirs."""
    for c in (root / "alembic" / "versions", root / "migrations" / "versions"):
        if c.is_dir() and _has_migration(root, c):
            return c
    for d in sorted(root.rglob("versions")):
        rel = d.relative_to(root).parts
        if any(part in _SKIP_DIRS or part.startswith(".") for part in rel):
            continue
        if d.is_dir() and _has_migration(root, d):
            return d
    return None


def _down_revisions(node) -> list[str]:
    """The parent revision id(s) of a `down_revision = ...` value: None → [],
    a string → [str], a tuple/list of strings → the (order-preserving) list."""
    s = _const_str(node)
    if s is not None:
        return [s]
    if isinstance(node, (ast.Tuple, ast.List)):
        return [_const_str(e) for e in node.elts if _const_str(e) is not None]
    return []




def _scan_alembic_timeline(root: Path, unparsed=None):
    """Parse the Alembic `versions/*.py` for revision/down_revision/message (no
    op-replay). Returns (ordered_rows, sources, note); rows are (revision,
    down_revision_display, message) in canonical topological order.

    `unparsed` is the out-list documented on `_scan_sqlalchemy_models`, and a
    refused file is hashed into `sources` there for the reason given there.

    This scan alone can discard the hash it just took: the `if not nodes`
    return below drops `sources` while `unparsed` — an out-list mutated in
    place — keeps the path the caller will name. The path is hashed anyway,
    because `_scan_sqlalchemy_models` and `_scan_orm_models` each walk every
    `.py` under the repository root through the same `_iter_py_files`, and a
    `versions/` directory is not in `_SKIP_DIRS`. Both refuse the same file and
    both hash it, so the caller's merged `sources` carries it whatever this
    scan returns."""
    versions = _detect_alembic_versions(root)
    if versions is None:
        return [], {}, ""
    sources: dict = {}
    nodes: dict = {}          # rev -> (downs, message)
    for py in sorted(versions.glob("*.py")):
        if py.name.startswith("__"):
            continue
        raw = _safe_read_bytes(root, py, [])
        if raw is None:
            continue
        try:
            tree = ast.parse(raw.decode("utf-8", errors="replace"))
        except _PARSE_FAILED:
            if unparsed is not None:
                unparsed.append(_rel(root, py))
                sources[_rel(root, py)] = _sha256_hex(raw)
            continue
        rev = None
        downs: list[str] = []
        for stmt in tree.body:
            if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 \
                    and isinstance(stmt.targets[0], ast.Name):
                tid = stmt.targets[0].id
                if tid == "revision":
                    rev = _const_str(stmt.value)
                elif tid == "down_revision":
                    downs = _down_revisions(stmt.value)
        if rev is None:
            continue
        doc = ast.get_docstring(tree) or ""
        msg = doc.strip().splitlines()[0].strip() if doc.strip() else ""
        nodes[rev] = (downs, msg)
        sources[_rel(root, py)] = _sha256_hex(raw)

    if not nodes:
        return [], {}, ""
    order, malformed = _topo_order(nodes)
    note = (
        "Malformed migration graph (cycle or missing parent) — fallback sort by revision id."
        if malformed else
        "Canonical topological order; the revision id breaks ties at equal depth."
    )
    rows = []
    for rev in order:
        downs = nodes[rev][0]
        # `_cell`, not raw: `disp` is rendered into `data-model.md` as a table
        # cell beside `rev` and `msg`, both of which ARE escaped. A
        # `down_revision` carrying a pipe closes the cell early and shifts every
        # column right of it; one carrying a backtick escapes the code span.
        # The value is repository-controlled — it is whatever string the
        # migration file assigns — so it gets the same treatment as its
        # neighbours.
        disp = ", ".join(f"`{_cell(d)}`" for d in sorted(downs)) if downs else "(base)"
        rows.append((rev, disp, nodes[rev][1]))
    return rows, sources, note


def _detect_python_data_model(root: Path) -> bool:
    """Cheap presence check: a Python source, or an Alembic `versions/` tree.

    It replaces a scan that read every `.py` file's TEXT looking for
    `__tablename__`, `Column(` or `mapped_column(`. That scan had to grow a
    fourth and fifth substring for the Django ORM and SQLModel, and each one
    would have been a pattern matched against authored source — the thing clause
    1 removes from this pack rather than adds to it. The extractor self-degrades
    to the stub when it finds no model, so the question is answered once, on a
    syntax tree, by the code that renders the answer.
    """
    root = Path(root)
    if next(_iter_py_files(root), None) is not None:
        return True
    return _detect_alembic_versions(root) is not None


def _model_table_section(heading: str, rows: list) -> list:
    """One entity table, in the shared six-column shape every ORM section uses.

    The header is `core.count_concern_entities`'s key for a data-model entity
    table, so all three sections render the same one and a repository declaring
    two kinds of model counts both.
    """
    tables = sorted({r[0] for r in rows})
    out = [f"## {heading} ({len(tables)})", "",
           "| table | column | type | nullable | pk | fk |",
           "|---|---|---|---|---|---|"]
    for table, col, typ, nullable, pk, fk in sorted(rows, key=lambda r: (r[0], r[1])):
        out.append(f"| {_cell(table)} | `{_cell(col)}` | {_cell(typ)} | "
                   f"{nullable} | {pk} | {_cell(fk)} |")
    out.append("")
    return out


def extract_python_data_model(root: Path, docs_dir: str) -> tuple[str, dict]:
    """Data model = every declarative model shape this pack reads, plus the
    Alembic migration timeline (ADR-0066 point 8, ADR-0096 clause 1).

    Three model shapes — SQLAlchemy declarative classes, SQLModel `table=True`
    classes, and Django `models.Model` subclasses — each rendered as its own
    section over one shared entity-table header. Every source set is hashed into
    `sources`; the app is never imported. Stub when none of the four is found.
    """
    root = Path(root)
    # One out-list across all three scans: a file the parser refuses is named
    # once in the residuals, not once per scan that tripped over it.
    unparsed: list = []
    model_rows, model_sources = _scan_sqlalchemy_models(root, unparsed)
    sqlmodel_rows, django_rows, orm_sources = _scan_orm_models(root, unparsed)
    mig_rows, mig_sources, mig_note = _scan_alembic_timeline(root, unparsed)
    if not (model_rows or sqlmodel_rows or django_rows or mig_rows):
        return _canon(["# Data model", "", NO_EXTRACTOR.rstrip("\n")]), {}

    sources = dict(model_sources)
    sources.update(orm_sources)
    sources.update(mig_sources)

    # Name only what actually rendered. A static sentence naming all four would
    # tell a FastAPI repository its spine was derived from Django models.
    read = []
    if model_rows:
        read.append("SQLAlchemy declarative models")
    if sqlmodel_rows:
        read.append("SQLModel tables")
    if django_rows:
        read.append("Django ORM models")
    if mig_rows:
        read.append("the Alembic migration timeline")
    if len(read) > 1:
        derived = ", ".join(read[:-1]) + " and " + read[-1]
    else:
        derived = read[0]

    out = ["# Data model", "", f"_Derived from {derived}._", ""]

    if model_rows:
        out += _model_table_section("SQLAlchemy models", model_rows)
    if sqlmodel_rows:
        out += _model_table_section("SQLModel tables", sqlmodel_rows)
    if django_rows:
        out += _model_table_section("Django models", django_rows)

    if mig_rows:
        out += [f"## Alembic migration timeline ({len(mig_rows)})", "",
                f"_{mig_note}_", "",
                "| # | revision | down_revision | message |",
                "|---|---|---|---|"]
        for i, (rev, disp, msg) in enumerate(mig_rows, start=1):
            out.append(f"| {i} | `{_cell(rev)}` | {disp} | {_cell(msg)} |")
        out.append("")

    residuals = []
    if sqlmodel_rows:
        residuals.append(_SQLMODEL_RELATIONSHIP)
    if django_rows:
        residuals.append(_DJANGO_IMPLICIT_PK)
    if sqlmodel_rows or django_rows:
        residuals.append(_INHERITED_FIELDS_RESIDUAL)
    if unparsed:
        residuals.append(_unparsed_residual(set(unparsed)))
    if residuals:
        out += ["## Residuals", ""] + [f"- {line}" for line in residuals] + [""]

    return _canon(out), sources


#: The root markers that make a repository a Python one. `requirements.txt` is
#: the third, and it is here because omitting it produced a SILENT WRONG PICK,
#: which is the failure ADR-0096's ambiguity-is-named-never-guessed clause
#: exists to prevent.
#:
#: `wagtail-bakerydemo` is the measured case: a Django application carrying no
#: `pyproject.toml` and no `setup.py`, so the only marker `core._scan_candidates`
#: found was its `package.json`, `_undominated` returned a single contender, and
#: `resolve_stack` answered `node` — with no ambiguity recorded, because from
#: the detector's side there was nothing to be ambiguous about. A repository
#: pinning its dependencies in `requirements.txt` is the ordinary shape of a
#: Django or Flask application, and it is now detected as one.
#:
#: The honest scope of the fix: `core`'s tie-break prefers python over node at
#: equal depth, so this changes detection for EVERY repository carrying both
#: files. It does not make "no silent wrong pick" universally true — a Go
#: service with a `package.json` still resolves silently to node — it makes no
#: corpus repository a counterexample, which is the scope clause 8 defines.
_PYTHON_MARKERS = ("pyproject.toml", "requirements.txt", "setup.py")


def _detect_python(root: Path) -> bool:
    return any((root / rel).exists() for rel in _PYTHON_MARKERS)


#: Empty: the core's marker scan prunes no directory from this pack's own
#: `detect` calls, so this pack's marker semantics are unchanged.
DETECT_EXCLUDE: frozenset[str] = frozenset()


def detect(root: Path) -> DetectResult:
    """Does this repository look like a Python project? (ADR-0096 clause 6.)

    `matched` is the predicate `_detect_python` computes — any one marker
    suffices. `markers` names every marker that fired, sorted by
    `_PYTHON_MARKERS`, so an ambiguous multi-match can report the marker rather
    than only the pack.
    """
    hits = tuple(rel for rel in _PYTHON_MARKERS if (root / rel).exists())
    return DetectResult(matched=bool(hits), markers=hits)


# The python pack's declared input class per concern (ADR-0097 part 1). `kind`
# lives on each probe below (`probes()`), not in this map. The fourth concern,
# `decision-index`, is the universal one: the core binds its probe for every
# pack and declares its input class, so it is absent here for the same reason
# it is absent from `probes()`.
#
# `api-surface` is the pack's one concern with a `committed-artifact` PROBE,
# and the reason the corpus stubs it 3 times out of 3: FastAPI, Django and
# Wagtail all emit an OpenAPI document on demand and none of the three commits
# one. Its DERIVED class is `parser`, not `committed-artifact` — the second
# probe, a real `ast` parse of route decorators / `urlpatterns`, is the
# weaker rung a repository with no committed document falls through to.
INPUT_CLASSES = {
    "data-model": InputClass(
        expected=(
            "SQLAlchemy declarative models, SQLModel `table=True` classes, "
            "Django `models.Model` subclasses, or an Alembic `versions/` "
            "migration timeline"
        ),
        globs=("*.py", "**/*.py"),
    ),
    # Two probes below declare two kinds, and the concern's DERIVED class is
    # the weaker of the two, `parser` (ADR-0097 part 1): the committed
    # document (`committed-artifact`) is tried first, and the `ast` parse of
    # the project's own sources (`parser`) answers when there is none.
    #
    # The glob widened WITH the probe. Dev loop 1 narrowed it to `openapi.json`
    # because `_detect_python_api_surface` checked the repository root and
    # nowhere else, and naming a wider search would have put a location in the
    # stub line that nothing looked at. That argument now runs the other way.
    #
    # `artifact` names which of these globs the target's own toolchain emits,
    # which is the line clause 7 draws between an emitted document and the
    # authored sources beside it in one glob set. It is a record rather than a
    # live signal here: `staleness.annotate` fires only on a `committed-artifact`
    # concern, so a `parser` concern is never annotated, and `possibly_stale`
    # reaches only the reported channel in any case.
    #
    # `refresh` is EMPTY, and that is a prohibition rather than an omission.
    #
    # The only command that emits this artifact imports the target's own
    # application object and calls `.openapi()` on it — it executes the
    # project's import-time code. That is precisely the operation ADR-0075
    # confines behind a two-factor consent gate, a subprocess jail, untrusted-
    # capture handling and inbox-only landing, and clause 11 says the runtime is
    # never the remedy any crux surface points a reader at. A remediation line
    # is a reader-facing surface: `derive-arch` runs in a forked subagent whose
    # skill instructs it to paste the coverage table verbatim.
    #
    # This is the line ruby's `bin/rails db:schema:dump` sits on the other side
    # of. That is a first-party CLI a developer already runs in their own shell;
    # this would be a crux-composed interpreter invocation, and the one that
    # stood here was not even runnable as printed — it carried a `<module>` hole,
    # because the import path of a FastAPI application is a project's own choice
    # and nothing in the tree declares it.
    #
    # Empty makes `core.remediation` fall back to `NO_COMMAND — the derive needs
    # <expected>`, which clause 9 sanctions for exactly this case: no command
    # establishes the precondition. `test_arch_verdicts.py`'s `FORBIDDEN` roster
    # holds the prohibition mechanically.
    "api-surface": InputClass(
        expected=(
            "a committed OpenAPI document at the repository root "
            "(`openapi.json`), else FastAPI or Flask route decorators, else a "
            "Django `urlpatterns` list"
        ),
        globs=("openapi.json", "*.py", "**/*.py"),
        artifact=("openapi.json",),
    ),
    # The manifest globs widened WITH tier 1b. Package detection now reads a
    # `pyproject.toml` or `setup.py` at depth 1 or 2 as well as at the root, so
    # an edit to one of those files can move this concern's verdict — and a
    # declared input set that did not name them would place such a file outside
    # every concern's declared inputs while the detector was reading it. The two
    # explicit depth levels say exactly what tier 1b scans; `**/pyproject.toml`
    # would over-declare a depth the detector never reaches.
    "module-graph": InputClass(
        # The sentence NAMES THE DEPTH BOUND (ADR-0097 part 4). ADR-0096
        # clause 6's "neither detector returns a silent None" holds only
        # within the scanned depth: a package outside it yields an empty
        # candidate set and a `precondition_missing` stub, and a sentence
        # reading "a detected package" left a reader unable to tell "no
        # package" from "none within reach". The `crux-repo` corpus entry is
        # the measured case — this repository's own importable package sits
        # at `crux/scripts/crux/`, two directories below the manifest, so no
        # candidate is anchored at any depth the manifest-anchored tier scans.
        expected=(
            "importable Python modules under a single package, detected from "
            "a `pyproject.toml` or `setup.py` at the repository root or one "
            "or two directories below it"
        ),
        globs=(
            "*.py", "**/*.py",
            "pyproject.toml", "setup.py",
            "*/pyproject.toml", "*/setup.py",
            "*/*/pyproject.toml", "*/*/setup.py",
        ),
    ),
}


def probes() -> dict[str, list[Probe]]:
    """The python pack's per-concern probe registry (ADR-0066 points 2/8).

    Each concern has its own ordered probe list and its own detector, so a
    repository carrying an `openapi.json` but no SQLAlchemy models populates
    api-surface and stubs data-model independently. `decision-index` is absent by
    design: it is the universal probe and the core binds it.

    api-surface is a two-probe chain, committed artifact first. The order is the
    decision: a document the project's own toolchain emitted carries the
    framework's full fidelity, including the routes registration produces at
    import time, and the parse below it sees only what the source declares.

    module-graph declares `regex-over-source` (ADR-0097 part 1): it delegates
    to `core._extract_module_graph`, the same engine the crux pack's own
    module-graph probe binds, and both are named on the ADR-0097 roster.
    """
    return {
        "data-model": [
            Probe(_detect_python_data_model, extract_python_data_model,
                  kind="parser"),
        ],
        "api-surface": [
            Probe(_detect_python_api_surface, extract_python_api_surface,
                  kind="committed-artifact"),
            Probe(_detect_python_routes, extract_python_api_surface_routes,
                  kind="parser"),
        ],
        "module-graph": [
            Probe(_detect_python_module_graph, extract_python_module_graph,
                  kind="regex-over-source"),
        ],
    }
