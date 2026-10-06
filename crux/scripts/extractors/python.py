"""Python code-doc extractor entry point (ADR-0131).

The API-2 extractor (`EXTRACTOR_API = 2`, ADR-0131 clause 13) that joins
three pieces:

- `_py_load.load_selection` — turns the selected `.py`/`.pyi` sources into
  Griffe modules, statically, once per run.
- `_py_page.render_page` — turns one loaded source (plus the whole loaded
  selection, for alias resolution) into a rendered page.
- the dispatcher's own `resolve_source`/`read_source_bytes` (ADR-0131's D6
  source-containment read path) and
  `ExtractContext`/`ExtractionRefusal`.

Never imports or executes target code: `discover` reads bytes through that path and
hands them to `_py_load.load_selection`, which parses with `ast`/`griffe.visit`
only (see `_py_load`'s own module docstring). `extract` never touches the
filesystem again; it renders from the already-loaded selection.

## Sharing the loaded selection across units

`load_selection` runs exactly once per `discover()` call, over every selected
source together (aliases need the whole selection to resolve, per clause 11).
Every `SourceUnit` this module returns carries the SAME `LoadedSelection`
object in its payload — cheap, since it is one in-memory reference, not a
copy — so `extract()` never reloads or re-parses anything.

## Loading siblings

This file has no sibling `import` statements for `_py_load`, `_py_render`,
`_py_page` or the dispatcher: each is loaded by file path, the same way
`_py_load.py` loads `_py_locals.py` and the dispatcher. `_py_page` is loaded
lazily, on the first `extract()` call, because `discover()` and
`provenance()` need nothing from it: a run whose selection is empty, or
that only calls `provenance()`, never pays for loading the page renderer.
"""

from __future__ import annotations

import hashlib
import importlib.util as _ilu
import sys as _sys
from pathlib import Path
from typing import Any

EXTRACTOR_API = 2

_HERE = Path(__file__).resolve().parent


def _load_sibling_by_path(module_name: str, filename: str):
    """Load `filename` (a sibling under extractors/) by file path.

    Mirrors `_py_load.py`'s own helper of the same name and purpose.
    """
    cached = _sys.modules.get(module_name)
    if cached is not None:
        return cached
    spec = _ilu.spec_from_file_location(module_name, _HERE / filename)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load spec for {_HERE / filename}")
    mod = _ilu.module_from_spec(spec)
    _sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


_py_load = _load_sibling_by_path("crux_py_load", "_py_load.py")
_py_render = _load_sibling_by_path("crux_py_render", "_py_render.py")

# Obtain dispatcher symbols from the module `load_extractor_module` already
# registered in sys.modules; fall back to loading ../extract-code-docs.py by
# path, exactly as _py_load.py, elixir.py and fallback.py do, for a test or
# REPL context that loaded this file directly.
_dispatch = _sys.modules.get("extract_code_docs_dispatcher")
if _dispatch is None:  # pragma: no cover — exercised by direct-load callers
    _spec = _ilu.spec_from_file_location(
        "extract_code_docs_dispatcher",
        _HERE.parent / "extract-code-docs.py",
    )
    if _spec is not None and _spec.loader is not None:
        _dispatch = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_dispatch)
        _sys.modules["extract_code_docs_dispatcher"] = _dispatch
SourceUnit = _dispatch.SourceUnit  # type: ignore[attr-defined]
DocPage = _dispatch.DocPage  # type: ignore[attr-defined]
ExtractionRefusal = _dispatch.ExtractionRefusal  # type: ignore[attr-defined]
resolve_source = _dispatch.resolve_source  # type: ignore[attr-defined]
read_source_bytes = _dispatch.read_source_bytes  # type: ignore[attr-defined]
GRIFFE_REQUIREMENT = _dispatch.GRIFFE_REQUIREMENT  # type: ignore[attr-defined]


def _get_py_page():
    """Lazily load `_py_page.py` (see module docstring). Cached after the
    first call, same as every other sibling load in this file."""
    return _load_sibling_by_path("crux_py_page", "_py_page.py")


_SELECTED_SUFFIXES = (".py", ".pyi")

#: ADR-0131 clause 4: `include_private` lives at the top level of a Python
#: language key's own entry, alongside `extractor` and `glob` — never nested
#: under an `options:` mapping. Any other key at that level refuses, because a
#: misplaced key would otherwise be a silent no-op. This is the exhaustive set the dispatcher passes through as `config`.
_ALLOWED_CONFIG_KEYS = frozenset({"extractor", "glob", "include_private"})


def _key_path(lang_key: str | None, key: str) -> str:
    if lang_key is None:
        return key
    return f"code.extractors.{lang_key}.{key}"


def _validate_config_keys(config: dict, lang_key: str) -> None:
    """Refuse a key the Python extractor does not honour.

    Named by its full manifest path so `options: {include_private: false}`
    (nested, the wrong shape) refuses instead of silently defaulting
    `include_private` to `True`.
    """
    for key in config:
        if key not in _ALLOWED_CONFIG_KEYS:
            raise ExtractionRefusal(
                _key_path(lang_key, key),
                "unrecognized key for the python extractor "
                f"(allowed: {', '.join(sorted(_ALLOWED_CONFIG_KEYS))})",
            )


def _resolve_include_private(config: dict, lang_key: str | None = None) -> bool:
    """`include_private` from `config`, default `True`.

    A non-boolean value refuses, naming the config key (contract-mandated
    behavior for this extractor, not a dispatcher-level check). `lang_key`
    is available from `discover()`'s `ctx` and names the full manifest path;
    `provenance(config)` carries no `ctx` (API-2 contract), so it names the
    bare key only — a gap this extractor cannot close without a dispatcher
    change to that call site.
    """
    if "include_private" not in config:
        return True
    value = config["include_private"]
    if not isinstance(value, bool):
        raise ExtractionRefusal(
            _key_path(lang_key, "include_private"),
            f"must be a boolean, got {type(value).__name__}",
        )
    return value


def _globs_from_config(config: dict) -> list[str]:
    globs = config.get("glob") or []
    if isinstance(globs, str):
        globs = [globs]
    return list(globs)


def discover(repo_root: Path, config: dict, ctx: Any) -> list[SourceUnit]:
    """Select every `.py`/`.pyi` file matching `config["glob"]`, load the
    whole selection once, and return one `SourceUnit` per selected file.

    Excludes anything under `ctx.output_root` even when a glob covers it.
    Every candidate is read through the dispatcher's ADR-0131 D6 helpers
    (`resolve_source`/`read_source_bytes`); a refusal there propagates. The
    recorded `source_path` is the repo-relative path as selected, never the
    resolved spelling.
    """
    _validate_config_keys(config, ctx.lang_key)
    include_private = _resolve_include_private(config, ctx.lang_key)
    output_root_resolved = ctx.output_root

    candidates: set[str] = set()
    for pattern in _globs_from_config(config):
        for path in repo_root.glob(pattern):
            if path.suffix not in _SELECTED_SUFFIXES:
                continue
            if not path.is_file():
                continue
            resolved = path.resolve()
            if resolved.is_relative_to(output_root_resolved):
                continue
            rel = str(path.relative_to(repo_root))
            candidates.add(rel)

    rel_paths = sorted(candidates)

    units_bytes: list[tuple[str, bytes]] = []
    for rel in rel_paths:
        resolved = resolve_source(repo_root, Path(rel))
        raw = read_source_bytes(repo_root, resolved, max_bytes=_py_load.MAX_SOURCE_BYTES)
        units_bytes.append((rel, raw))

    # ExtractionRefusal from here (a grammar failure, a resource bound,
    # anything else clause 7 names) propagates: never caught.
    selection = _py_load.load_selection(units_bytes)

    raw_by_rel = dict(units_bytes)
    source_by_rel = {source.rel_path: source for source in selection.sources}

    units: list[SourceUnit] = []
    for rel in rel_paths:
        units.append(
            SourceUnit(
                language="python",
                identifier=rel,
                source_path=rel,
                payload={
                    "selection": selection,
                    "source": source_by_rel[rel],
                    "raw_bytes": raw_by_rel[rel],
                    "include_private": include_private,
                },
            )
        )
    return units


def extract(unit: SourceUnit, ctx: Any) -> DocPage:
    """Render `unit` via `_py_page.render_page`, from the already-loaded
    selection carried in its payload (see module docstring)."""
    payload = unit.payload
    page = _get_py_page().render_page(
        payload["source"],
        payload["selection"],
        include_private=payload["include_private"],
    )
    source_sha256 = hashlib.sha256(payload["raw_bytes"]).hexdigest()
    return DocPage(
        title=page.title,
        path=page.doc_path,
        body=page.body,
        source_path=unit.source_path,
        meta={
            "language": "python",
            "source_sha256": source_sha256,
            "gaps": list(page.gaps),
        },
    )


def provenance(config: dict) -> dict:
    """The `extractors.<key>` provenance block this extractor contributes
    (ADR-0131 clause 13). No interpreter version anywhere in the result."""
    include_private = _resolve_include_private(config)
    return {
        "backend": GRIFFE_REQUIREMENT,
        "grammar": _py_load.GRAMMAR_VERSION_STR,
        "renderer_version": _py_render.RENDERER_VERSION,
        "extension_version": _py_load.EXTENSION_VERSION,
        "include_private": include_private,
    }
