"""Advisory re-entry, outside `arch/` (ADR-0075 decision 6).

A capture is untrusted advisory data. This module writes it into the cross-
concern staging zone `<docs_dir>/inbox/` — ASCII-scrubbed, marked
`runtime_sourced: true`, carrying provenance, with NO in-body clock and NO
absolute paths. A human files it as a research synthesis page or a session
brief. It NEVER writes under `arch/` and never touches the four-file spine or
its hash.

A second, optional channel writes a candidate `openapi.json`-shaped fold-back
the human may review and commit into the target repo, where the ordinary
deterministic extractor consumes it on the next normal `derive`. That committed
static source is the only spine-improving path, and it is indirect and
deterministic.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

# The trusted re-escape used on every rendered value. Reuses capture.py's
# file-loaded `derive._cell` (the sanctioned runtime → derive edge, stdlib-only)
# rather than `from crux.arch.derive import _cell`, so reentry stays stdlib-only.
# `_strip_controls` is the shared non-printable-control-char scrub.
from .capture import _cell, _strip_controls


def _ascii(value) -> str:
    """ASCII-scrub, `_cell`-escape, then drop non-printable control chars.
    Non-ASCII becomes `?`; pipe/backtick/newline are neutralized by `_cell`; and
    ESC/NUL/BEL and other control chars are dropped so a forged route or model
    name cannot inject an ANSI escape sequence into the advisory when catted or
    break the table. Printable ASCII is unchanged."""
    s = str(value).encode("ascii", "replace").decode("ascii")
    return _strip_controls(_cell(s))


def _content_slug(capture: dict) -> str:
    """A short, clock-free, deterministic filename discriminant: 16 hex chars
    (64 bits) of a SHA-256 over the capture. No timestamp (ADR-0075 decision 6).
    16 chars, not 8: a 32-bit discriminant makes a silent overwrite of an
    as-yet-unfiled advisory reachable; 64 bits pushes that below any practical
    concern while keeping the filename short."""
    blob = json.dumps(capture, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def _render(capture: dict, target: str, framework: str) -> str:
    routes = capture.get("routes", [])
    models = capture.get("models", [])
    lines = [
        "---",
        "runtime_sourced: true",
        "provenance: arch-runtime-introspection",
        f"framework: {_ascii(framework)}",
        f"target: {_ascii(target)}",
        "---",
        "",
        "# Runtime arch introspection advisory",
        "",
        "Untrusted advisory captured by running the target's import-time code under",
        "confinement. Review before filing as a research synthesis page or a brief.",
        "It is NOT part of the arch spine and never changes the spine hash.",
        "",
        f"## Routes ({len(routes)})",
        "",
        "| method | path | name |",
        "| --- | --- | --- |",
    ]
    for r in routes:
        method = "" if r.get("method") is None else _ascii(r.get("method"))
        lines.append(f"| {method} | {_ascii(r.get('path', ''))} | {_ascii(r.get('name', ''))} |")
    lines += ["", f"## Models ({len(models)})", "",
              "| model | table | field | type | nullable |",
              "| --- | --- | --- | --- | --- |"]
    for m in models:
        name = _ascii(m.get("name", ""))
        table = _ascii(m.get("table", ""))
        fields = m.get("fields", [])
        if not fields:
            lines.append(f"| {name} | {table} |  |  |  |")
        for f in fields:
            lines.append(
                f"| {name} | {table} | {_ascii(f.get('name', ''))} | "
                f"{_ascii(f.get('type', ''))} | {str(bool(f.get('nullable', False))).lower()} |"
            )
    lines.append("")
    return "\n".join(lines)


def _inbox_dir(docs_dir: str | Path) -> Path:
    inbox = Path(docs_dir) / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    return inbox


def write_advisory(capture: dict, *, docs_dir: str | Path, target: str,
                   framework: str) -> Path:
    """Write the advisory into `<docs_dir>/inbox/`. Returns the path. Refuses to
    write anywhere but `inbox/` (never `arch/`)."""
    inbox = _inbox_dir(docs_dir)
    slug = _content_slug(capture)
    fw = _ascii(framework).lower().replace(" ", "-") or "runtime"
    path = inbox / f"arch-runtime-advisory-{fw}-{slug}.md"
    _assert_in_inbox(path, docs_dir)
    # No absolute paths in the advisory: a legitimate target is a dotted
    # module:attr / module.path spec (no slashes); strip any directory prefix a
    # path-shaped target would carry.
    safe_target = str(target).rsplit("/", 1)[-1]
    path.write_text(_render(capture, safe_target, framework), encoding="utf-8")
    return path


def write_foldback_candidate(capture: dict, *, docs_dir: str | Path) -> Path:
    """Write an OpenAPI-shaped fold-back CANDIDATE into `<docs_dir>/inbox/` — a
    static source the human may review and commit into the target repo for the
    deterministic extractor to consume. Never committed automatically; never
    under `arch/`."""
    inbox = _inbox_dir(docs_dir)
    slug = _content_slug(capture)
    paths: dict = {}
    for r in capture.get("routes", []):
        p = r.get("path", "")
        method = r.get("method")
        entry = paths.setdefault(p, {})
        verb = (method or "x-any").lower()
        entry[verb] = {"operationId": r.get("name", "")}
    doc = {"openapi": "3.0.0",
           "info": {"title": "runtime-introspection candidate (review before committing)",
                    "version": "0"},
           "paths": paths}
    path = inbox / f"arch-runtime-openapi-candidate-{slug}.json"
    _assert_in_inbox(path, docs_dir)
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _assert_in_inbox(path: Path, docs_dir: str | Path) -> None:
    inbox = (Path(docs_dir) / "inbox").resolve()
    resolved = path.resolve()
    if not (resolved == inbox or str(resolved).startswith(str(inbox) + "/")):
        raise ValueError(
            f"advisory re-entry refuses to write outside inbox/: {resolved} "
            "(never arch/, never the spine — ADR-0075 decision 6)"
        )
