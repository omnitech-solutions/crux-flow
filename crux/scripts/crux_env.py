#!/usr/bin/env python3
"""crux_env — read access to ~/.crux/env from Python scripts.

Per ADR-0002 (rename-to-crux-and-manage-env-and-secrets) and PB-0002
(build-crux-env-script-and-module). The on-disk file format and this
module's public API are pinned in docs/AGENTS.md §13.

This module is ALSO the single source of truth for the env-file parser used
by the CLI (`crux-env.py`). The CLI imports `parse_env_text`,
`parse_env_line`, and `encode_value` from here so the two stay in
byte-for-byte lock-step.

Usage:
    from crux_env import require, get, get_optional, EnvNotConfigured
    (OPENROUTER_API_KEY,) = require("OPENROUTER_API_KEY")
    debug = get_optional("CRUX_DEBUG", default=False, cast=bool)
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Callable

__all__ = [
    "require",
    "get",
    "get_optional",
    "load_all",
    "EnvNotConfigured",
    # Parser surface — shared with the CLI.
    "parse_env_text",
    "parse_env_line",
    "encode_value",
    "KEY_RE",
]

# ---------------------------------------------------------------------------
# Key regex per §13.2: ASCII uppercase + digits + underscore; must start with
# an uppercase letter.
# ---------------------------------------------------------------------------
KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
# Back-compat alias used internally below.
_KEY_RE = KEY_RE

# Module-level cache: {resolved_path_str: (mtime_float, parsed_dict)}.
# Keyed by resolved-path-str so that a change to CRUX_HOME naturally
# invalidates the lookup for the new path.
_cache: dict[str, tuple[float, dict[str, str]]] = {}


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------
def _env_dir() -> Path:
    """Return the ~/.crux directory, honouring CRUX_HOME if set."""
    return Path(os.environ.get("CRUX_HOME") or os.path.expanduser("~/.crux"))


def _env_file() -> Path:
    """Return the path to the ~/.crux/env file."""
    return _env_dir() / "env"


# ---------------------------------------------------------------------------
# Parser — shared with the CLI (crux-env.py imports these symbols).
#
# Rules (docs/AGENTS.md §13.2):
#   - One KEY=value per line.
#   - Keys match ^[A-Z][A-Z0-9_]*$. Leading whitespace before a key is a
#     parse error (we surface format mistakes loudly).
#   - No whitespace permitted between `=` and the value either; values are
#     always read starting at the byte immediately after `=`.
#   - Values are unquoted by default. If a value starts with `"`, read until
#     the next unescaped `"`. Inside quotes: \" → " and \\ → \. No other
#     escape sequences — `\X` for unknown X is a parse error.
#   - Unquoted values may not contain whitespace; if a value needs spaces,
#     `#`, or any whitespace it MUST be wrapped in double quotes.
#   - `#` outside double-quoted values starts a comment to end-of-line.
#   - Blank lines and comment-only lines are ignored.
#   - All parse errors raise ValueError with the 1-based line number.
# ---------------------------------------------------------------------------


def parse_env_text(text: str) -> dict[str, str]:
    """Parse an env-file body and return a dict of KEY → value.

    Raises ValueError on malformed lines. Comments and blank lines are
    skipped. Later definitions of the same key win.
    """
    result: dict[str, str] = {}
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        kv = parse_env_line(raw_line, lineno)
        if kv is None:
            continue
        key, value = kv
        result[key] = value
    return result


def parse_env_line(raw_line: str, lineno: int = 0) -> tuple[str, str] | None:
    """Parse one env-file line.

    Returns (key, value) on a KEY=value line; None on blank/comment-only.
    Raises ValueError on malformed input.
    """
    # Strip trailing CR/LF only; leading whitespace is significant so we can
    # reject it explicitly below.
    line = raw_line.rstrip("\n").rstrip("\r")

    # Blank line.
    if not line.strip():
        return None

    # Comment-only line: only allow `#` at the very start. Anything else with
    # leading whitespace is suspicious.
    if line.lstrip().startswith("#") and line[:1] in (" ", "\t"):
        # Allow leading-whitespace comment lines — the existing format
        # examples (AGENTS.md §13.2) don't show them but they don't change
        # parsing. Be lenient on comments specifically.
        return None
    if line.startswith("#"):
        return None

    # Reject leading whitespace before the key.
    if line[:1] in (" ", "\t"):
        raise ValueError(
            f"line {lineno}: leading whitespace before key is not permitted: "
            f"{raw_line!r}"
        )

    # Find the `=` (must exist).
    eq_idx = line.find("=")
    if eq_idx < 0:
        raise ValueError(
            f"line {lineno}: not a KEY=value line and not a comment/blank: "
            f"{raw_line!r}"
        )

    key = line[:eq_idx]
    if not _KEY_RE.match(key):
        raise ValueError(
            f"line {lineno}: invalid key {key!r}; must match ^[A-Z][A-Z0-9_]*$"
        )

    rhs = line[eq_idx + 1 :]
    value = _decode_value(rhs, lineno)
    return key, value


def _decode_value(rhs: str, lineno: int) -> str:
    """Decode the right-hand side of a KEY=... line.

    Handles optional surrounding double quotes, in-line `#` comments outside
    quotes, and the two recognized escapes \\\" and \\\\.

    Whitespace immediately after `=` is rejected — wrap the value in double
    quotes to include leading whitespace, or remove the padding.
    """
    n = len(rhs)

    # Empty value (`KEY=`): treat as empty string.
    if n == 0:
        return ""

    # Reject padding between `=` and value. The CLI used to silently strip
    # this; the module used to raise on it via the embedded-whitespace check.
    # We standardize on rejecting loudly per ADR-0002 spec-locked behavior.
    if rhs[0] in (" ", "\t"):
        raise ValueError(
            f"line {lineno}: whitespace between '=' and value is not "
            f"permitted; wrap the value in double quotes if it needs to "
            f"start with whitespace"
        )

    i = 0
    if rhs[i] == '"':
        # Quoted value: consume until matching unescaped `"`.
        i += 1
        out: list[str] = []
        while i < n:
            ch = rhs[i]
            if ch == "\\":
                if i + 1 >= n:
                    raise ValueError(
                        f"line {lineno}: trailing backslash inside quoted value"
                    )
                nxt = rhs[i + 1]
                if nxt == '"':
                    out.append('"')
                elif nxt == "\\":
                    out.append("\\")
                else:
                    # Per spec: no other escapes recognized. Reject loudly.
                    raise ValueError(
                        f"line {lineno}: unrecognised escape sequence "
                        f"'\\{nxt}' in quoted value "
                        f"(only \\\" and \\\\ are supported)"
                    )
                i += 2
                continue
            if ch == '"':
                i += 1
                # Anything after the closing quote must be whitespace or a
                # `#` comment.
                while i < n and rhs[i] in (" ", "\t"):
                    i += 1
                if i < n and rhs[i] != "#":
                    raise ValueError(
                        f"line {lineno}: unexpected text after closing quote: "
                        f"{rhs[i:]!r}"
                    )
                return "".join(out)
            out.append(ch)
            i += 1
        raise ValueError(f"line {lineno}: unterminated double-quoted value")

    # Unquoted value: terminate at the first `#` (comment) or end-of-line.
    # No whitespace permitted inside — §13.2 mandates quoting if the value
    # contains any whitespace.
    out2: list[str] = []
    while i < n:
        ch = rhs[i]
        if ch == "#":
            break
        if ch in (" ", "\t"):
            raise ValueError(
                f"line {lineno}: unquoted value contains whitespace; wrap "
                f"in double quotes"
            )
        out2.append(ch)
        i += 1
    return "".join(out2)


def encode_value(value: str) -> str:
    """Encode a value for writing to the env file.

    Per §13.2: quote with double quotes if the value contains whitespace or
    `#`. Inside quotes, escape `"` as `\\"` and `\\` as `\\\\`.
    """
    if value == "":
        return ""
    needs_quote = (
        any(ch.isspace() or ch == "#" for ch in value)
        or '"' in value
        or "\\" in value
    )
    if not needs_quote:
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse a crux env file. Returns {} if the file does not exist.

    Raises ValueError on malformed lines (with 1-based line number).
    """
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    return parse_env_text(text)


# ---------------------------------------------------------------------------
# Cache management
# ---------------------------------------------------------------------------
def _load_file_cached() -> dict[str, str]:
    """Return the parsed env file dict, using a path+mtime-keyed cache."""
    path = _env_file()
    key = str(path)

    if not path.exists():
        # Drop any stale cache entry for this path.
        _cache.pop(key, None)
        return {}

    mtime = path.stat().st_mtime
    cached = _cache.get(key)
    if cached is not None and cached[0] == mtime:
        return cached[1]

    parsed = _parse_env_file(path)
    _cache[key] = (mtime, parsed)
    return parsed


def _reset_cache() -> None:
    """Clear the module-level cache. Test hook; not public API."""
    _cache.clear()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def _lookup(key: str) -> str | None:
    """Look up a key in os.environ first, then the parsed env file.

    Returns None if the key is absent OR present-but-empty in both sources.
    """
    val = os.environ.get(key)
    if val is not None and val != "":
        return val

    file_dict = _load_file_cached()
    val = file_dict.get(key)
    if val is not None and val != "":
        return val

    return None


def get(key: str, default: str | None = None) -> str | None:
    """Return the value for `key`, or `default` if missing/empty. Never raises."""
    val = _lookup(key)
    if val is None:
        return default
    return val


def get_optional(
    key: str,
    default: Any = None,
    cast: Callable[[str], Any] = str,
) -> Any:
    """Typed accessor.

    Returns `default` (uncast) if the key is missing or empty.
    Otherwise applies `cast` to the raw string value.

    `cast=bool` recognises 'true|1|yes|on' (True) and 'false|0|no|off' (False),
    case-insensitive; any other string raises ValueError.
    """
    val = _lookup(key)
    if val is None:
        return default

    if cast is bool:
        lowered = val.strip().lower()
        if lowered in ("true", "1", "yes", "on"):
            return True
        if lowered in ("false", "0", "no", "off"):
            return False
        raise ValueError(
            f"Cannot cast {val!r} to bool — expected one of "
            f"true|1|yes|on|false|0|no|off"
        )

    return cast(val)


def load_all() -> dict[str, str]:
    """Return a fresh dict of every key in ~/.crux/env (no os.environ overlay).

    Returns an empty dict if the env file does not exist.
    """
    # Return a copy so callers can't mutate the cache.
    return dict(_load_file_cached())


class EnvNotConfigured(Exception):
    """Raised by require() when one or more keys are missing or empty.

    Attributes:
        missing_keys: list[str]  -- the keys that were missing/empty
        remediation: str         -- a multi-line message with the exact
                                    `crux-env set ...` commands to fix it.
    """

    missing_keys: list[str]
    remediation: str

    def __init__(self, missing_keys: list[str], remediation: str) -> None:
        super().__init__(remediation)
        self.missing_keys = list(missing_keys)
        self.remediation = remediation


def _build_remediation(missing: list[str]) -> str:
    """Build the user-facing remediation message for EnvNotConfigured."""
    header = f"Missing: {', '.join(missing)}. Set them with:"
    cmds = "\n".join(f"  crux-env set {k} <value>" for k in missing)
    return f"{header}\n{cmds}"


def require(*keys: str) -> tuple[str, ...]:
    """Return values for each key in argument order.

    Reads os.environ first, then ~/.crux/env. Raises EnvNotConfigured if
    any requested key is missing or empty. Single-key calls return a 1-tuple
    (use unpacking or indexing).
    """
    values: list[str] = []
    missing: list[str] = []
    for k in keys:
        v = _lookup(k)
        if v is None:
            missing.append(k)
            values.append("")  # placeholder; not returned if `missing` is non-empty
        else:
            values.append(v)

    if missing:
        raise EnvNotConfigured(
            missing_keys=missing,
            remediation=_build_remediation(missing),
        )

    return tuple(values)
