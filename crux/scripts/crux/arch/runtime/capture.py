"""The capture protocol — the shared contract between the confined child and the
trusted parent (ADR-0075 decision 3).

Closed schema, no open shape:

    {
      "routes": [{"method": str|null, "path": str, "name": str}, ...],
      "models": [{"name": str, "table": str,
                  "fields": [{"name": str, "type": str, "nullable": bool}, ...]},
                 ...]
    }

The child (`child.py` → `introspect.py`) builds normalized route/model dicts and
calls `serialize`, writing the bytes to a dedicated capture descriptor. The
parent (`harness.py`) drains that descriptor under a pre-parse byte cap and
deadline, then calls `parse_and_validate` on the drained bytes.

Trust boundary: the descriptor is inherited by the imported target code, which
could close or forge it, so the capture is UNTRUSTED advisory data, not a
trusted channel. `parse_and_validate` is the authoritative bound — `json.loads`
ONLY (never pickle / marshal / eval), a strict closed-schema validator that
rejects unknown keys and wrong types, post-parse count and length caps, a
deterministic re-ordering, and a re-escape of every string value through
`derive._cell`. Together these bound a forged capture to garbage advisory output
a human reviews before filing; it never reaches the spine.

`serialize` applies the same ordering, caps, and `_cell` re-escape so a
well-behaved child emits a byte-stable document; the parent re-applies them
because the child is untrusted.

DIRECTIONALITY: this module imports `crux.arch.derive._cell`. That is the
sanctioned `runtime → derive` edge (ADR-0075 decision 5); `derive` never imports
back. Importing `derive` runs no target code — it is the static engine.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any


def _load_cell():
    """Load `core.py`'s `_cell` by FILE PATH — the one sanctioned runtime → derive
    edge (ADR-0075 decision 5). A file-load (not `from crux.arch.core import
    _cell`) deliberately bypasses `crux/__init__`'s LLM/httpx import chain, so the
    executor stays stdlib-only and runs under the USER's project interpreter (the
    one that can import their app), not a uv-ephemeral env. Loading it runs no
    target code — it is the static engine — and it never imports back into this
    package.

    **The target is `core.py`, not `derive.py`, since ADR-0096 clause 12.** The
    file-load works only while the loaded module is stdlib-self-contained AT
    MODULE SCOPE, and after the split it is `core.py` that holds that property:
    `derive.py` is now a re-export facade whose head carries `from .core import
    ...`, a relative import that cannot resolve outside the package. `core.py`
    keeps the property under test — `test_arch_split_seam.py` walks its module
    scope AST and fails on any intra-crux import, naming this loader as the
    reason."""
    derive_path = Path(__file__).resolve().parent.parent / "core.py"
    spec = importlib.util.spec_from_file_location("_crux_arch_core_cell", derive_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._cell


# Every captured string value re-passes through this escaper before it is rendered.
_cell = _load_cell()

# Post-parse caps (ADR-0075 decision 3). Arrays over the cap are TRUNCATED (a
# forged capture is bounded to garbage, not rejected outright — the strict
# schema below is what rejects). Strings over the cap are truncated.
MAX_ROUTES = 4096
MAX_MODELS = 4096
MAX_FIELDS = 512
MAX_STR = 1024

# Closed key sets — any other key is an unknown key and is REJECTED.
_ROUTE_KEYS = ("method", "path", "name")
_MODEL_KEYS = ("name", "table", "fields")
_FIELD_KEYS = ("name", "type", "nullable")
_TOP_KEYS = ("routes", "models")


class CaptureError(ValueError):
    """A capture document violated the closed schema, or the bytes were not the
    single JSON object the protocol requires. Raising this yields no-capture."""


def _strip_controls(s: str) -> str:
    """Drop non-printable ASCII control chars — every byte < 0x20 (NUL, BEL, ESC,
    tab, newline) and DEL (0x7F) — so a hostile captured value cannot inject an
    ANSI escape or terminal-control sequence into the advisory when it is catted.
    Printable ASCII is unchanged, and so is `_cell`'s own output (its `ʼ`
    backtick-replacement is U+02BC, above 0x7F, and is kept)."""
    return "".join(ch for ch in s if 0x20 <= ord(ch) != 0x7F)


def _s(value: Any) -> str:
    """Coerce to string, re-escape through `derive._cell`, drop control chars,
    then hard-cap at MAX_STR.

    `_cell` can LENGTHEN a value — it rewrites each `|` to `\\|` (two chars for
    one) — so its output can exceed the input length. The [:MAX_STR] slice
    therefore MUST run AFTER the escape; that slice, not any length claim about
    `_cell`, is what guarantees the result is <= MAX_STR characters. Never move
    the cap before the escape."""
    return _strip_controls(_cell(str(value)))[:MAX_STR]


def _order_routes(routes: list[dict]) -> list[dict]:
    # Deterministic: by path, then method (None sorts as "" — stable and total).
    return sorted(routes, key=lambda r: (r["path"], r["method"] or ""))


def _order_fields(fields: list[dict]) -> list[dict]:
    return sorted(fields, key=lambda f: f["name"])


def _order_models(models: list[dict]) -> list[dict]:
    for m in models:
        m["fields"] = _order_fields(m["fields"])
    return sorted(models, key=lambda m: m["name"])


def _norm_route(raw: Any) -> dict:
    method = raw.get("method")
    if method is not None:
        method = _s(method)
    return {"method": method, "path": _s(raw.get("path", "")), "name": _s(raw.get("name", ""))}


def _norm_field(raw: Any) -> dict:
    return {
        "name": _s(raw.get("name", "")),
        "type": _s(raw.get("type", "")),
        "nullable": bool(raw.get("nullable", False)),
    }


def _norm_model(raw: Any) -> dict:
    fields = raw.get("fields") or []
    if not isinstance(fields, list):
        fields = []
    fields = [_norm_field(f) for f in fields[:MAX_FIELDS] if isinstance(f, dict)]
    return {"name": _s(raw.get("name", "")), "table": _s(raw.get("table", "")), "fields": fields}


def serialize(routes: list[dict], models: list[dict]) -> str:
    """Render normalized route/model dicts to the byte-stable capture document.

    Called by the CHILD. Applies caps, ordering, and `_cell` re-escape so a
    well-behaved child emits the same document the parent will re-derive. The
    output is a single-line JSON object with sorted keys and a trailing newline.
    """
    r = _order_routes([_norm_route(x) for x in list(routes)[:MAX_ROUTES] if isinstance(x, dict)])
    m = _order_models([_norm_model(x) for x in list(models)[:MAX_MODELS] if isinstance(x, dict)])
    doc = {"routes": r, "models": m}
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"


def _reject_unknown(obj: Any, allowed: tuple[str, ...], where: str) -> dict:
    if not isinstance(obj, dict):
        raise CaptureError(f"{where}: expected object, got {type(obj).__name__}")
    extra = set(obj) - set(allowed)
    if extra:
        raise CaptureError(f"{where}: unknown key(s) {sorted(extra)}")
    return obj


def parse_and_validate(data: str | bytes) -> dict:
    """Parse and strictly validate an UNTRUSTED capture document (parent side).

    `json.loads` ONLY. Rejects (raises `CaptureError`) on: non-JSON bytes, a
    non-object root, unknown keys anywhere in the closed schema, or a wrong-typed
    value. Truncates over-count arrays to their caps and over-length strings to
    MAX_STR, re-orders deterministically, and re-escapes every string through
    `derive._cell`. Returns the bounded, byte-stable dict.
    """
    if isinstance(data, bytes):
        try:
            data = data.decode("utf-8")
        except UnicodeDecodeError as e:
            raise CaptureError(f"capture is not valid UTF-8: {e}") from e
    try:
        obj = json.loads(data)
    except (json.JSONDecodeError, ValueError, RecursionError, MemoryError) as e:
        # The child writes this payload, so deeply nested JSON raises
        # `RecursionError` or `MemoryError` rather than `ValueError` and would
        # escape `CaptureError` — turning a bad capture into a crash of the
        # caller. Spelled out rather than importing `core._PARSE_FAILED`:
        # `runtime/` is loaded by path under the TARGET project's interpreter,
        # deliberately bypassing the `crux` package import chain, so it must not
        # acquire a module-level import from `core`.
        raise CaptureError(f"capture is not valid JSON: {e}") from e

    _reject_unknown(obj, _TOP_KEYS, "root")
    raw_routes = obj.get("routes", [])
    raw_models = obj.get("models", [])
    if not isinstance(raw_routes, list):
        raise CaptureError("routes: expected array")
    if not isinstance(raw_models, list):
        raise CaptureError("models: expected array")

    routes: list[dict] = []
    for i, r in enumerate(raw_routes[:MAX_ROUTES]):
        _reject_unknown(r, _ROUTE_KEYS, f"routes[{i}]")
        method = r.get("method")
        if method is not None and not isinstance(method, str):
            raise CaptureError(f"routes[{i}].method: expected string or null")
        routes.append(_norm_route(r))

    models: list[dict] = []
    for i, m in enumerate(raw_models[:MAX_MODELS]):
        _reject_unknown(m, _MODEL_KEYS, f"models[{i}]")
        raw_fields = m.get("fields", [])
        if not isinstance(raw_fields, list):
            raise CaptureError(f"models[{i}].fields: expected array")
        for j, f in enumerate(raw_fields[:MAX_FIELDS]):
            _reject_unknown(f, _FIELD_KEYS, f"models[{i}].fields[{j}]")
            if "nullable" in f and not isinstance(f["nullable"], bool):
                raise CaptureError(f"models[{i}].fields[{j}].nullable: expected boolean")
        models.append(_norm_model(m))

    return {"routes": _order_routes(routes), "models": _order_models(models)}
