"""Rendering primitives for the Python code-doc extractor (ADR-0131 clauses 9-10).

Stdlib only. Loaded by path, like every module under `extractors/`: this
file has no sibling imports, so it is importable on its own with importlib,
and the composing extractor loads it the same way.

Implements the rendering primitives ADR-0131 clauses 9-10 require, named R1-R6:

- R1 `fence(text, info)` — a backtick fence sized one longer than the
  longest backtick run in `text`, minimum 3.
- R2 `normalize_docstring(text)` / `render_docstring(text_or_none)` —
  CRLF-to-LF, cleandoc dedent, trailing-whitespace strip, and the
  "_Empty docstring._" / "_Undocumented._" fallbacks.
- R3 `visible_escapes(s)` — control characters and line/paragraph
  separators rendered as a visible escape sequence instead of the raw byte.
- R4 `md_escape_inline(s)` — backslash-escapes Markdown metacharacters,
  applied before R3 so the backslashes R3 introduces are not re-escaped.
- R5 `code_span(s)` — a padded, sized backtick span for a target-derived
  string that must render as one line; an empty string renders as
  `_(empty string)_` instead (R5a), because two adjacent backticks are not
  a code span in CommonMark.
- R6 `link_destination(doc_path)` — percent-encodes a page path for use as
  a Markdown link destination.

R7 (signature rendering) has no pure-function surface here; it is
implemented by the composing extractor against `ast.unparse`.

Exposes `RENDERER_VERSION` (clause 13's renderer version, distinct from the
`_py_locals` extension's own `EXTENSION_VERSION`).
"""

from __future__ import annotations

import inspect
import urllib.parse

RENDERER_VERSION = "1"

_MD_METACHARS = frozenset("\\`*_[]<>#!&|~")


def _longest_backtick_run(text: str) -> int:
    longest = 0
    current = 0
    for ch in text:
        if ch == "`":
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def fence(text: str, info: str) -> str:
    """R1: a sized backtick fence. Text inside is rendered verbatim."""
    n = max(3, _longest_backtick_run(text) + 1)
    delim = "`" * n
    return f"{delim}{info}\n{text}\n{delim}"


def normalize_docstring(text: str) -> str:
    """R2 normalization, applied before fencing."""
    text = text.replace("\r\n", "\n")
    text = inspect.cleandoc(text)
    lines = [line.rstrip() for line in text.split("\n")]
    return "\n".join(lines).rstrip()


def render_docstring(text_or_none: str | None) -> str:
    """R2: the full docstring rendering, including the two fallback lines."""
    if text_or_none is None:
        return "_Undocumented._"
    normalized = normalize_docstring(text_or_none)
    if not normalized:
        return "_Empty docstring._"
    return fence(normalized, "text")


def visible_escapes(s: str) -> str:
    """R3: control characters and line/paragraph separators made visible."""
    out: list[str] = []
    for ch in s:
        cp = ord(ch)
        if ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif cp == 0x2028:
            out.append("\\u2028")
        elif cp == 0x2029:
            out.append("\\u2029")
        elif cp <= 0x1F or cp == 0x7F or 0x80 <= cp <= 0x9F:
            out.append(f"\\x{cp:02x}")
        else:
            out.append(ch)
    return "".join(out)


def md_escape_inline(s: str) -> str:
    """R4: backslash-escape Markdown metacharacters, then apply R3.

    R4 runs first (on the raw characters), so the backslashes R3 introduces
    for control characters are never themselves re-escaped.
    """
    escaped_chars: list[str] = []
    for ch in s:
        if ch in _MD_METACHARS:
            escaped_chars.append("\\" + ch)
        else:
            escaped_chars.append(ch)
    return visible_escapes("".join(escaped_chars))


def code_span(s: str) -> str:
    """R5: a code span for a string that must render as one line.

    R3 is applied first so the result is guaranteed single-line; the fence
    length and any padding are then computed against the escaped result.

    R5a: an empty string renders as the fallback line `_(empty string)_`,
    never as an empty code span — two adjacent backticks are not a code
    span in CommonMark.
    """
    if s == "":
        return "_(empty string)_"
    escaped = visible_escapes(s)
    n = _longest_backtick_run(escaped) + 1
    delim = "`" * n
    needs_pad = (
        escaped.startswith("`")
        or escaped.endswith("`")
        or escaped.startswith(" ")
        or escaped.endswith(" ")
    )
    pad = " " if needs_pad else ""
    return f"{delim}{pad}{escaped}{pad}{delim}"


def link_destination(doc_path: str) -> str:
    """R6: percent-encode a page path for a Markdown link destination."""
    return urllib.parse.quote(doc_path, safe="/")
