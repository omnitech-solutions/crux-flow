"""Elixir extractor for crux.

Primary strategy: invoke `mix docs --formatter json` (ExDoc) and parse the
resulting JSON. This requires the project to depend on ExDoc and have a
working Mix project — which most Elixir/Phoenix apps do.

Fallback strategy: when `mix` is not on PATH or ExDoc isn't configured, scan
*.ex/*.exs files with regex to lift `@moduledoc` and `@doc` attributes. The
fallback is intentionally low-fidelity:

  - It does not understand sigils, heredoc edge cases, or nested module
    definitions across files.
  - It does not capture `@spec` types.
  - It cannot tell public from private functions reliably without a parser.
  - Multi-line `@doc` strings using triple-quoted heredocs are supported via
    a simple state machine; other forms (e.g. `@doc ~S` with sigils) may
    surface as raw quoted source.

When the project's docs are important, use ExDoc. The fallback is a safety
net for repos that haven't set up ExDoc yet.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys

# Import shared dataclasses from the dispatcher. The dispatcher registers
# itself in sys.modules under this key before loading extractors.
import sys as _sys
import tempfile
from pathlib import Path
from typing import Any

_dispatch = _sys.modules.get("extract_code_docs_dispatcher")
if _dispatch is None:  # pragma: no cover — defensive for direct import
    import importlib.util as _u

    _spec = _u.spec_from_file_location(
        "extract_code_docs_dispatcher",
        Path(__file__).resolve().parent.parent / "extract-code-docs.py",
    )
    if _spec is not None and _spec.loader is not None:
        _dispatch = _u.module_from_spec(_spec)
        _spec.loader.exec_module(_dispatch)
        _sys.modules["extract_code_docs_dispatcher"] = _dispatch
SourceUnit = _dispatch.SourceUnit  # type: ignore[attr-defined]
DocPage = _dispatch.DocPage  # type: ignore[attr-defined]


# ─────────────────────────── public interface ─────────────────────────────


def discover(repo_root: Path, config: dict) -> list[SourceUnit]:
    """Return one SourceUnit per documentable Elixir module.

    Prefers `mix docs --formatter json`; falls back to regex over .ex files.
    """
    if _mix_available(repo_root) and _exdoc_available(repo_root):
        try:
            return _discover_via_mix_docs(repo_root, config)
        except Exception as exc:
            print(
                f"[elixir] mix docs failed ({exc}); falling back to regex scan",
                file=sys.stderr,
            )
    return _discover_via_regex(repo_root, config)


def extract(unit: SourceUnit) -> DocPage:
    """Render a single SourceUnit to a DocPage."""
    if unit.payload.get("strategy") == "mix-docs":
        return _extract_from_mix_payload(unit)
    return _extract_from_regex_payload(unit)


# ──────────────────────── mix docs json strategy ──────────────────────────


def _mix_available(repo_root: Path) -> bool:
    return shutil.which("mix") is not None and (repo_root / "mix.exs").is_file()


def _exdoc_available(repo_root: Path) -> bool:
    """Check whether ExDoc appears in mix.exs deps. Best-effort textual scan."""
    mix_exs = repo_root / "mix.exs"
    try:
        text = mix_exs.read_text(encoding="utf-8")
    except OSError:
        return False
    return ":ex_doc" in text or "ex_doc" in text


def _discover_via_mix_docs(repo_root: Path, config: dict) -> list[SourceUnit]:
    with tempfile.TemporaryDirectory(prefix="crux-exdoc-") as tmp:
        output_dir = Path(tmp)
        cmd = ["mix", "docs", "--formatter", "json", "--output", str(output_dir)]
        env = os.environ.copy()
        env.setdefault("MIX_ENV", "dev")
        result = subprocess.run(
            cmd,
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            env=env,
            timeout=180,
        )
        if result.returncode != 0:
            raise RuntimeError(f"mix docs exit {result.returncode}: {result.stderr.strip()[:300]}")
        # ExDoc's JSON formatter emits a single docs.json with module entries.
        json_path = next(output_dir.rglob("*.json"), None)
        if json_path is None:
            raise RuntimeError("mix docs produced no JSON output")
        data = json.loads(json_path.read_text(encoding="utf-8"))
        return list(_units_from_mix_json(data, repo_root))


def _units_from_mix_json(data: Any, repo_root: Path) -> list[SourceUnit]:
    """ExDoc JSON shape varies by version; treat it leniently.

    Expected: a list of module objects with at least `id`/`title` and `doc`,
    plus a list of function `docs`. Some versions wrap it in {"modules": [...]}.
    """
    modules = data
    if isinstance(data, dict):
        modules = data.get("modules") or data.get("docs") or []
    units: list[SourceUnit] = []
    for entry in modules or []:
        if not isinstance(entry, dict):
            continue
        module_name = entry.get("id") or entry.get("title") or entry.get("name") or "Unknown"
        source_path = entry.get("source_path") or entry.get("source") or ""
        if source_path and Path(source_path).is_absolute():
            try:
                source_path = str(Path(source_path).relative_to(repo_root))
            except ValueError:
                pass
        units.append(
            SourceUnit(
                language="elixir",
                identifier=module_name,
                source_path=source_path or f"<{module_name}>",
                payload={"strategy": "mix-docs", "module": entry},
            )
        )
    return units


def _extract_from_mix_payload(unit: SourceUnit) -> DocPage:
    entry = unit.payload["module"]
    module_name = unit.identifier
    moduledoc = (entry.get("doc") or "").strip()
    functions = entry.get("docs") or []
    body_parts = [f"# {module_name}", ""]
    if entry.get("source_path"):
        body_parts.append(f"_Source: `{entry['source_path']}`_")
        body_parts.append("")
    if moduledoc:
        body_parts.append(moduledoc)
        body_parts.append("")
    if functions:
        body_parts.append("## Functions")
        body_parts.append("")
        for fn in sorted(functions, key=lambda f: (f.get("name", ""), f.get("arity", 0))):
            name = fn.get("name", "?")
            arity = fn.get("arity", "?")
            signature = fn.get("signature") or [f"{name}/{arity}"]
            sig_text = signature[0] if isinstance(signature, list) and signature else f"{name}/{arity}"
            body_parts.append(f"### `{sig_text}`")
            body_parts.append("")
            doc = (fn.get("doc") or "").strip()
            if doc:
                body_parts.append(doc)
                body_parts.append("")
            specs = fn.get("specs") or []
            if specs:
                body_parts.append("**Specs:**")
                body_parts.append("")
                body_parts.append("```elixir")
                for spec in specs:
                    body_parts.append(str(spec))
                body_parts.append("```")
                body_parts.append("")
    path = f"elixir/{_module_to_path(module_name)}.md"
    return DocPage(
        title=module_name,
        path=path,
        body="\n".join(body_parts).rstrip() + "\n",
        source_path=unit.source_path,
    )


# ─────────────────────────── regex fallback ───────────────────────────────


_DEFAULT_GLOB = "lib/**/*.ex"

_MODULE_RE = re.compile(r"^\s*defmodule\s+([A-Z][\w\.]*)\s+do\b")
_MODULEDOC_TRIPLE_RE = re.compile(r'^\s*@moduledoc\s+"""\s*$')
_MODULEDOC_LINE_RE = re.compile(r"^\s*@moduledoc\s+(.+)$")
_DOC_TRIPLE_RE = re.compile(r'^\s*@doc\s+"""\s*$')
_DOC_LINE_RE = re.compile(r"^\s*@doc\s+(.+)$")
_DEF_RE = re.compile(r"^\s*(?:def|defmacro)\s+([a-z_][\w?!]*)\s*(?:\(([^)]*)\))?")
_SPEC_TRIPLE_RE = re.compile(r"^\s*@spec\s+(.+)$")


def _discover_via_regex(repo_root: Path, config: dict) -> list[SourceUnit]:
    globs = config.get("glob") or _DEFAULT_GLOB
    if isinstance(globs, str):
        globs = [globs]
    files: list[Path] = []
    for pattern in globs:
        files.extend(sorted(repo_root.glob(pattern)))
    # Dedupe and stable-sort.
    seen: set[Path] = set()
    unique: list[Path] = []
    for f in sorted(files):
        if f.resolve() not in seen and f.is_file():
            seen.add(f.resolve())
            unique.append(f)
    units: list[SourceUnit] = []
    for path in unique:
        try:
            rel = str(path.relative_to(repo_root))
        except ValueError:
            rel = str(path)
        # D6 source containment (ADR-0131 clause 14): resolve the source
        # and read through the dispatcher's helpers, in the same pass, so a
        # symlink to a target outside the repository -- or a source that
        # stops being a regular file between the check and the read --
        # refuses the run instead of being silently parsed or skipped.
        # ExtractionRefusal is NOT caught here; it propagates to the
        # dispatcher. A genuine read/decode failure keeps today's silent
        # skip.
        try:
            resolved = _dispatch.resolve_source(repo_root, Path(rel))
            text = _dispatch.read_source_bytes(repo_root, resolved).decode("utf-8")
        except _dispatch.ExtractionRefusal:
            raise
        except (OSError, UnicodeDecodeError):
            continue
        for module_block in _parse_elixir_modules(text):
            units.append(
                SourceUnit(
                    language="elixir",
                    identifier=module_block["name"],
                    source_path=rel,
                    payload={"strategy": "regex", "block": module_block},
                )
            )
    return units


def _parse_elixir_modules(text: str) -> list[dict]:
    """Very small state machine. Recognizes:
       - defmodule X do
       - @moduledoc "..." or @moduledoc \"\"\"...\"\"\"
       - @doc "..." or @doc \"\"\"...\"\"\"
       - def/defmacro name(args)
       - @spec lines (captured single-line only)
    Limitations documented at module-top docstring.
    """
    lines = text.splitlines()
    modules: list[dict] = []
    current: dict | None = None
    pending_doc: str | None = None
    pending_spec: str | None = None
    i = 0
    while i < len(lines):
        line = lines[i]
        m = _MODULE_RE.match(line)
        if m:
            if current is not None:
                modules.append(current)
            current = {"name": m.group(1), "moduledoc": "", "functions": []}
            pending_doc = None
            pending_spec = None
            i += 1
            continue
        if current is None:
            i += 1
            continue
        # @moduledoc
        if _MODULEDOC_TRIPLE_RE.match(line):
            start_lineno = i + 1  # 1-indexed; line of `@moduledoc """`
            i += 1
            buf = []
            # Close ONLY on a line whose stripped form is EXACTLY `"""`.
            # `lines[i].strip().startswith('"""')` (the old check) closed
            # prematurely on inner lines like `"""foo`, silently truncating
            # the moduledoc.
            while i < len(lines) and lines[i].strip() != '"""':
                buf.append(lines[i])
                i += 1
            if i >= len(lines):
                print(
                    f"[elixir] warning: unterminated @moduledoc heredoc starting at "
                    f"line {start_lineno} in module {current['name']!r}; "
                    f"docstring may be truncated.",
                    file=sys.stderr,
                )
            current["moduledoc"] = "\n".join(buf).strip()
            i += 1
            continue
        m = _MODULEDOC_LINE_RE.match(line)
        if m:
            val = m.group(1).strip()
            if val.startswith('"') and val.endswith('"'):
                val = val[1:-1]
            current["moduledoc"] = val
            i += 1
            continue
        # @doc
        if _DOC_TRIPLE_RE.match(line):
            start_lineno = i + 1  # 1-indexed; line of `@doc """`
            i += 1
            buf = []
            # Same fix as the @moduledoc branch — require EXACT `"""` close
            # so `"""foo` lines inside the body do not terminate the heredoc.
            while i < len(lines) and lines[i].strip() != '"""':
                buf.append(lines[i])
                i += 1
            if i >= len(lines):
                print(
                    f"[elixir] warning: unterminated @doc heredoc starting at "
                    f"line {start_lineno} in module {current['name']!r}; "
                    f"docstring may be truncated.",
                    file=sys.stderr,
                )
            pending_doc = "\n".join(buf).strip()
            i += 1
            continue
        m = _DOC_LINE_RE.match(line)
        if m:
            val = m.group(1).strip()
            if val.startswith('"') and val.endswith('"'):
                val = val[1:-1]
            pending_doc = val
            i += 1
            continue
        # @spec
        m = _SPEC_TRIPLE_RE.match(line)
        if m:
            pending_spec = m.group(1).strip()
            i += 1
            continue
        # def/defmacro
        m = _DEF_RE.match(line)
        if m:
            name = m.group(1)
            args = (m.group(2) or "").strip()
            current["functions"].append(
                {
                    "name": name,
                    "args": args,
                    "doc": pending_doc or "",
                    "spec": pending_spec or "",
                }
            )
            pending_doc = None
            pending_spec = None
            i += 1
            continue
        i += 1
    if current is not None:
        modules.append(current)
    return modules


def _extract_from_regex_payload(unit: SourceUnit) -> DocPage:
    block = unit.payload["block"]
    name = block["name"]
    parts = [f"# {name}", "", f"_Source: `{unit.source_path}` (regex fallback)_", ""]
    moduledoc = (block.get("moduledoc") or "").strip()
    if moduledoc:
        parts.append(moduledoc)
        parts.append("")
    else:
        parts.append("_No @moduledoc found._")
        parts.append("")
    functions = block.get("functions") or []
    if functions:
        parts.append("## Functions")
        parts.append("")
        for fn in sorted(functions, key=lambda f: (f["name"], f.get("args", ""))):
            sig = f"{fn['name']}({fn.get('args', '')})"
            parts.append(f"### `{sig}`")
            parts.append("")
            if fn.get("spec"):
                parts.append("**Spec:**")
                parts.append("")
                parts.append("```elixir")
                parts.append("@spec " + fn["spec"])
                parts.append("```")
                parts.append("")
            doc = (fn.get("doc") or "").strip()
            if doc:
                parts.append(doc)
                parts.append("")
    path = f"elixir/{_module_to_path(name)}.md"
    return DocPage(
        title=name,
        path=path,
        body="\n".join(parts).rstrip() + "\n",
        source_path=unit.source_path,
    )


# ──────────────────────────── helpers ────────────────────────────────────


def _module_to_path(module_name: str) -> str:
    """Elixir module `Foo.Bar.Baz` → path `Foo/Bar/Baz`."""
    return "/".join(part for part in module_name.split(".") if part)
