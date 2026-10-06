"""Header-comment scrape fallback extractor.

The "any language" extractor of last resort. For each file matching the
configured glob, lift the first comment block at the top of the file and
emit a one-page DocPage. Stable, deterministic, low-fidelity.

Comment markers are language-detected by extension:

  .py / .rb / .sh / .yml / .yaml / .toml         #
  .js / .ts / .jsx / .tsx / .c / .h / .cc /
  .cpp / .hpp / .go / .rs / .java / .kt / .swift //
  .ex / .exs                                    #
  .sql / .hs / .lua                             --
  .ml / .mli                                    (* ... *)
  .html / .htm / .xml                           <!-- ... -->
  .css / .scss                                  /* ... */

Unknown extensions are skipped (no comment marker assumed).

The "first comment block" is the leading run of comment lines at the top of
the file — optionally separated by a shebang line. Trailing blank lines
within the block end it.
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path


_dispatch = _sys.modules.get("extract_code_docs_dispatcher")
if _dispatch is None:  # pragma: no cover
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


# ─────────────────── language registry (extension → style) ─────────────────


_HASH_EXTS = {
    ".py", ".rb", ".sh", ".yml", ".yaml", ".toml",
    ".ex", ".exs", ".pl", ".r", ".jl", ".tf",
}
_SLASH_EXTS = {
    ".js", ".ts", ".jsx", ".tsx", ".c", ".h", ".cc", ".cpp", ".hpp",
    ".go", ".rs", ".java", ".kt", ".swift", ".scala", ".cs", ".m", ".mm",
    ".php", ".dart",
}
_DASH_EXTS = {".sql", ".hs", ".lua", ".elm", ".ada"}
_BLOCK_OCAML = {".ml", ".mli"}
_BLOCK_HTML = {".html", ".htm", ".xml", ".vue", ".svelte"}
_BLOCK_C = {".css", ".scss", ".less"}


def _comment_strategy(ext: str) -> tuple[str, str | None, str | None] | None:
    """Return (line_prefix, block_open, block_close). Either line OR block.

    For block-only languages, line_prefix is empty; we read until block_close.
    For line-only languages, block_open/close are None.
    """
    ext = ext.lower()
    if ext in _HASH_EXTS:
        return ("#", None, None)
    if ext in _SLASH_EXTS:
        return ("//", "/*", "*/")
    if ext in _DASH_EXTS:
        return ("--", None, None)
    if ext in _BLOCK_OCAML:
        return ("", "(*", "*)")
    if ext in _BLOCK_HTML:
        return ("", "<!--", "-->")
    if ext in _BLOCK_C:
        return ("", "/*", "*/")
    return None


# ────────────────────────── public interface ───────────────────────────────


def discover(repo_root: Path, config: dict) -> list[SourceUnit]:
    globs = config.get("glob") or []
    if isinstance(globs, str):
        globs = [globs]
    if not globs:
        return []
    files: list[Path] = []
    seen: set[Path] = set()
    for pattern in globs:
        for path in sorted(repo_root.glob(pattern)):
            resolved = path.resolve()
            if resolved in seen or not path.is_file():
                continue
            if _comment_strategy(path.suffix) is None:
                continue
            seen.add(resolved)
            files.append(path)
    units: list[SourceUnit] = []
    for path in sorted(files):
        try:
            rel = str(path.relative_to(repo_root))
        except ValueError:
            rel = str(path)
        units.append(
            SourceUnit(
                language="fallback",
                identifier=rel,
                source_path=rel,
                payload={"abs_path": str(path), "repo_root": str(repo_root)},
            )
        )
    return units


def extract(unit: SourceUnit) -> DocPage:
    # D6 source containment (ADR-0131 clause 14): read the source through
    # the dispatcher's resolve_source/read_source_bytes rather than a plain
    # Path.read_text, so a source that resolves outside the repository, or
    # stops being a regular file between the check and the read, refuses the
    # run instead of being silently read. ExtractionRefusal is NOT caught
    # here -- it propagates to the dispatcher, which reports it and writes
    # nothing for the run.
    repo_root = Path(unit.payload["repo_root"])
    resolved = _dispatch.resolve_source(repo_root, Path(unit.source_path))
    try:
        text = _dispatch.read_source_bytes(repo_root, resolved).decode("utf-8")
    except UnicodeDecodeError:
        text = ""
    header = _extract_header(text, resolved.suffix)
    title = unit.source_path
    parts = [f"# {title}", "", f"_Source: `{unit.source_path}` (header-comment fallback)_", ""]
    if header:
        parts.append(header)
        parts.append("")
    else:
        parts.append("_No leading comment block found._")
        parts.append("")
    out_path = f"fallback/{unit.source_path}.md"
    return DocPage(
        title=title,
        path=out_path,
        body="\n".join(parts).rstrip() + "\n",
        source_path=unit.source_path,
    )


# ───────────────────────── header extraction ───────────────────────────────


def _extract_header(text: str, ext: str) -> str:
    strategy = _comment_strategy(ext)
    if strategy is None or not text:
        return ""
    line_prefix, block_open, block_close = strategy
    lines = text.splitlines()
    # Skip shebang.
    idx = 0
    if lines and lines[0].startswith("#!"):
        idx = 1
        while idx < len(lines) and not lines[idx].strip():
            idx += 1
    # Try block comment first if defined and the next non-blank line opens one.
    if block_open and idx < len(lines):
        stripped = lines[idx].lstrip()
        if stripped.startswith(block_open):
            return _collect_block(lines, idx, block_open, block_close or "")
    # Otherwise read a run of line-comment lines.
    if line_prefix:
        return _collect_line_block(lines, idx, line_prefix)
    return ""


def _collect_block(lines: list[str], start: int, opener: str, closer: str) -> str:
    buf: list[str] = []
    first = lines[start].lstrip()
    if first.startswith(opener):
        first = first[len(opener):]
    if closer and closer in first:
        # Single-line block comment.
        idx_close = first.index(closer)
        return first[:idx_close].strip()
    if first.strip():
        buf.append(first.strip())
    i = start + 1
    while i < len(lines):
        line = lines[i]
        if closer and closer in line:
            piece = line.split(closer, 1)[0]
            piece = piece.lstrip().lstrip("*").strip()
            if piece:
                buf.append(piece)
            break
        cleaned = line.lstrip().lstrip("*").rstrip()
        buf.append(cleaned)
        i += 1
    # Trim trailing empties.
    while buf and not buf[-1].strip():
        buf.pop()
    return "\n".join(buf).strip()


def _collect_line_block(lines: list[str], start: int, prefix: str) -> str:
    buf: list[str] = []
    i = start
    # Allow up to one leading blank line gap before the comment block.
    while i < len(lines) and not lines[i].strip():
        i += 1
    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip()
        if stripped.startswith(prefix):
            buf.append(stripped[len(prefix):].lstrip())
            i += 1
            continue
        if not line.strip() and buf:
            # blank line ends the header
            break
        if not buf:
            i += 1
            continue
        break
    return "\n".join(buf).strip()
