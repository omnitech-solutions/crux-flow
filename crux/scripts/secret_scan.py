"""Secret scan for the council runner: key shapes, the gateway key, unsafe input paths.

Standard library only. No third-party import. The only environment reads are
``CRUX_HOME`` and ``HOME`` (``os.path.expanduser`` reads ``HOME`` to find
``~/.crux``); see :func:`check_input_path`.

What it matches
---------------
* The exact value of the gateway key, passed in by the caller as ``exact``.
  A value shorter than ``MIN_EXACT_LENGTH`` characters is ignored, because it
  would match ordinary text. The match is reported as ``"exact-key"``.
* Declared key shapes (``KEY_SHAPES``). A shape is a known prefix followed by a
  body of a declared minimum length and character class, so a bare prefix, or a
  prefix with a short body, matches nothing. A shape matches only at a left
  boundary: the start of the text, a character outside ``[A-Za-z0-9_-]``, or
  the end of a JSON escape (``\\b``, ``\\f``, ``\\n``, ``\\r``, ``\\t`` or
  ``\\uXXXX``). PEM body separators are whitespace or ``\\n``, ``\\r``, ``\\t``.
  Each pattern is compiled once. Every shape except the PEM block is a literal
  prefix plus one character class repeated, with no nested quantifier. The PEM
  body repeats a base64 character followed by a separator run; the two sets are
  disjoint, so each position matches in one way and a scan does not backtrack.

Runner contract
---------------
The runner scans the unserialized object with :func:`scan_fields` AND the exact
serialized bytes it writes or prints with :func:`scan_text`. :func:`scan_text`
finds shapes in JSON text produced by ``json.dumps`` with either
``ensure_ascii`` setting. A shape is found after any character outside
``[A-Za-z0-9_-]``, including a non-ASCII or control character that
``json.dumps`` writes as an escape. The serializer must be ``json``'s;
another escape syntax is not covered.

A secret with no declared shape and not equal to ``exact`` is not found.

Value invariant
---------------
No function here returns, raises, logs or prints a matched value. The scan
functions return shape names and field paths. Exceptions carry fixed messages.
A dict key that matches a shape is named ``<key>`` in a field path, never by
its text. ``check_input_path`` returns a code and never reads file content.
"""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Iterable
from pathlib import Path
from typing import NamedTuple

__all__ = [
    "EXACT_KEY",
    "KEY_SHAPES",
    "KeyShape",
    "MIN_EXACT_LENGTH",
    "check_input_path",
    "check_secret_location",
    "scan_fields",
    "scan_text",
]

EXACT_KEY = "exact-key"
MIN_EXACT_LENGTH = 8

# A left boundary is a character outside [A-Za-z0-9_-], or the end of a JSON
# escape that json.dumps writes for such a character: \b \f \n \r \t or
# \uXXXX. A backslash is not in any key alphabet, so "\x" is not a boundary.
_LEFT_BOUNDARY = r"(?:(?<![A-Za-z0-9_-])|(?<=\\[bfnrt])|(?<=\\u[0-9A-Fa-f]{4}))"
_SEP = r"(?:\s|\\[bfnrt]|\\u[0-9A-Fa-f]{4})*"


class KeyShape(NamedTuple):
    """One declared key shape.

    ``body_class`` is a regex character class such as ``[0-9a-f]``. ``name``
    may be shared by several prefixes of one family.
    """

    name: str
    prefix: str
    min_body: int
    body_class: str


_PEM_PREFIX = "-----BEGIN "
_PEM_BODY = "[A-Za-z0-9+/=]"

KEY_SHAPES: tuple[KeyShape, ...] = (
    KeyShape("openrouter-key", "sk-or-v1-", 32, "[0-9a-f]"),
    KeyShape("anthropic-key", "sk-ant-", 32, "[A-Za-z0-9_-]"),
    KeyShape("openai-project-key", "sk-proj-", 32, "[A-Za-z0-9_-]"),
    KeyShape("openai-generic-key", "sk-", 40, "[A-Za-z0-9]"),
    KeyShape("github-token", "ghp_", 36, "[A-Za-z0-9]"),
    KeyShape("github-token", "gho_", 36, "[A-Za-z0-9]"),
    KeyShape("github-token", "ghu_", 36, "[A-Za-z0-9]"),
    KeyShape("github-token", "ghs_", 36, "[A-Za-z0-9]"),
    KeyShape("github-token", "ghr_", 36, "[A-Za-z0-9]"),
    KeyShape("github-fine-grained-token", "github_pat_", 60, "[A-Za-z0-9_]"),
    KeyShape("aws-access-key-id", "AKIA", 16, "[A-Z0-9]"),
    KeyShape("aws-access-key-id", "ASIA", 16, "[A-Z0-9]"),
    KeyShape("google-api-key", "AIza", 35, "[A-Za-z0-9_-]"),
    KeyShape("slack-token", "xoxb-", 10, "[A-Za-z0-9-]"),
    KeyShape("slack-token", "xoxp-", 10, "[A-Za-z0-9-]"),
    KeyShape("slack-token", "xoxa-", 10, "[A-Za-z0-9-]"),
    KeyShape("slack-token", "xoxr-", 10, "[A-Za-z0-9-]"),
    KeyShape("slack-token", "xoxs-", 10, "[A-Za-z0-9-]"),
    KeyShape("pem-private-key", _PEM_PREFIX, 40, _PEM_BODY),
)


def _compile(shape: KeyShape) -> re.Pattern[str]:
    if shape.prefix == _PEM_PREFIX:
        # Header, then base64 characters with separators (whitespace or a JSON
        # newline escape) between them. Base64 and separators are disjoint
        # sets, so the repetition does not backtrack.
        body = rf"{_SEP}(?:{shape.body_class}{_SEP}){{{shape.min_body},}}"
        return re.compile(
            _LEFT_BOUNDARY + re.escape(shape.prefix) + r"[A-Z ]*PRIVATE KEY-----" + body
        )
    pattern = (
        _LEFT_BOUNDARY
        + re.escape(shape.prefix)
        + f"{shape.body_class}{{{shape.min_body},}}"
    )
    if shape.name == "aws-access-key-id":
        # An AWS key id is exactly 16 characters after the prefix.
        pattern = (
            _LEFT_BOUNDARY
            + re.escape(shape.prefix)
            + f"{shape.body_class}{{{shape.min_body}}}(?![A-Z0-9])"
        )
    return re.compile(pattern)


_COMPILED: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (shape.name, _compile(shape)) for shape in KEY_SHAPES
)


def _usable_exact(exact: Iterable[str]) -> list[str]:
    if isinstance(exact, (str, bytes, bytearray)):
        # A str iterates per character; every character is too short and the
        # key would be silently dropped.
        raise TypeError("exact must be an iterable of str, not a single str or bytes")
    return [v for v in exact if isinstance(v, str) and len(v) >= MIN_EXACT_LENGTH]


def scan_text(text: str, *, exact: Iterable[str] = ()) -> list[str]:
    """Return the sorted, de-duplicated names of the shapes found in ``text``.

    ``"exact-key"`` is included when any value in ``exact`` occurs in ``text``
    as a substring. Values shorter than ``MIN_EXACT_LENGTH`` characters, empty
    values and non-string values are ignored. Raises ``TypeError`` (fixed
    message) when ``text`` is not a ``str`` or when ``exact`` is itself a
    ``str`` or ``bytes`` rather than an iterable of values.
    """
    if not isinstance(text, str):
        raise TypeError("scan_text requires a str")
    usable = _usable_exact(exact)
    found = {name for name, rx in _COMPILED if rx.search(text)}
    if any(v in text for v in usable):
        found.add(EXACT_KEY)
    return sorted(found)


def scan_fields(
    obj: object, *, exact: Iterable[str] = (), path: str = ""
) -> list[tuple[str, str]]:
    """Return ``(field_path, shape_name)`` for every match inside ``obj``.

    Walks dicts (keys and values), lists, tuples and strings; other scalars are
    skipped. Field paths look like ``seats[1].reasoning``. A dict key that
    matches (a non-``str`` key is scanned as ``str(key)``) is written ``<key>``
    in its own path and in the path of everything below it, so no path carries
    a matched value. The result is sorted and
    de-duplicated.
    """
    exact_values = tuple(_usable_exact(exact))
    hits: set[tuple[str, str]] = set()

    def walk(node: object, here: str) -> None:
        if isinstance(node, str):
            for name in scan_text(node, exact=exact_values):
                hits.add((here, name))
        elif isinstance(node, dict):
            for key, value in node.items():
                if isinstance(key, str):
                    key_hits = scan_text(key, exact=exact_values)
                    segment = "<key>" if key_hits else key
                else:
                    key_hits = scan_text(str(key), exact=exact_values)
                    segment = "<key>" if key_hits else str(key)
                child = f"{here}.{segment}" if here else segment
                for name in key_hits:
                    hits.add((child, name))
                walk(value, child)
        elif isinstance(node, (list, tuple)):
            for index, item in enumerate(node):
                walk(item, f"{here}[{index}]")

    walk(obj, path)
    return sorted(hits)


def _is_env_name(name: str) -> bool:
    lowered = name.lower()
    return (
        lowered == ".env"
        or lowered.startswith(".env.")
        or lowered.endswith(".env")
        or lowered == ".envrc"
    )


def _real(path: Path) -> Path:
    return Path(os.path.realpath(path))


def _crux_homes(crux_home: Path | None) -> list[Path]:
    homes = [Path(os.path.expanduser("~")) / ".crux"]
    if crux_home is not None:
        homes.append(Path(crux_home))
    from_env = os.environ.get("CRUX_HOME")
    if from_env:
        homes.append(Path(from_env))
    return [_real(h) for h in homes]


def _under_by_identity(real: Path, home: Path) -> bool:
    """Return whether an ancestor of ``real`` is the same directory as ``home``.

    Compares ``st_dev``/``st_ino`` through ``os.path.samefile``, so a path
    spelled in another case on a case-insensitive volume is caught. A home that
    does not exist matches nothing here; the string-prefix check covers it.
    """
    if not home.exists():
        return False
    for parent in real.parents:
        try:
            if os.path.samefile(parent, home):
                return True
        except OSError:
            continue
    return False


def _walk_components(candidate: Path, root: Path) -> str | None:
    """Return ``symlink`` or ``missing`` from an ``lstat`` of each component.

    ``candidate`` must lie lexically under ``root`` or its real path.
    """
    for base in (root, _real(root)):
        try:
            parts = candidate.relative_to(base).parts
        except ValueError:
            continue
        break
    else:
        return "outside-repo"
    current = base
    last = None
    for part in parts:
        current = current / part
        try:
            last = os.lstat(current).st_mode
        except (OSError, ValueError):
            return "missing"
        if stat.S_ISLNK(last):
            return "symlink"
    if last is None or not stat.S_ISREG(last):
        return "missing"
    return None


def check_input_path(
    path: str | Path, repo_root: Path, *, crux_home: Path | None = None
) -> str | None:
    """Return a refusal code for ``path``, or ``None`` to accept it.

    A relative ``path`` is taken relative to ``repo_root``. The checks run in
    this order, and the first that fires decides the code:

    * ``"outside-repo"``: the path has a ``..`` component (refused before
      anything resolves it, even when it stays inside the repository), or an
      absolute path is not lexically under ``repo_root`` or its real path.
      An absolute path that reaches the repository only through a symlink
      outside it is refused here.
    * ``"symlink"``: a component between the repository root and the path, or
      the path itself, is a symlink (``lstat`` on each component; nothing is
      resolved first).
    * ``"missing"``: a component does not exist, or the path is not a regular file.
    * ``"outside-repo"``: the real path is not inside the real repository root.
    * ``"crux-home"``: the real path lies under ``~/.crux``, under
      ``crux_home``, or under ``$CRUX_HOME`` when set. "Lies under" holds by
      string prefix or by directory identity (``samefile``), so a case-variant
      spelling on a case-insensitive volume is refused. An explicit
      ``crux_home`` adds to the other two. A file named ``env`` there is
      reported as ``"env-file"``.
    * ``"env-file"``: the basename is ``.env``, starts with ``.env.``, ends
      with ``.env``, or is ``.envrc``.

    File content is never read.
    """
    root = Path(repo_root)
    try:
        candidate = Path(path)
        if ".." in candidate.parts:
            return "outside-repo"
        if not candidate.is_absolute():
            candidate = root / candidate
    except (TypeError, ValueError):
        return "missing"

    refusal = _walk_components(candidate, root)
    if refusal is not None:
        return refusal

    real = _real(candidate)
    if not real.is_relative_to(_real(root)):
        return "outside-repo"
    return check_secret_location(real, crux_home=crux_home)


def check_secret_location(path: str | Path, *, crux_home: Path | None = None) -> str | None:
    """Return ``"crux-home"``, ``"env-file"`` or ``None`` for ``path``, by its real path alone.

    This is the crux-home and env-file half of :func:`check_input_path`, without the
    in-repository containment, for a file that may sit outside any repository (a scratch file
    named on a command line). ``"crux-home"``: the real path lies under ``~/.crux``, under
    ``crux_home`` or under ``$CRUX_HOME`` (string prefix or directory identity); a file named
    ``env`` there is ``"env-file"``. ``"env-file"``: the basename is ``.env``, starts with
    ``.env.``, ends with ``.env``, or is ``.envrc``. File content is never read.
    """
    real = _real(Path(path))
    for home in _crux_homes(crux_home):
        if real.is_relative_to(home) or _under_by_identity(real, home):
            return "env-file" if real.name == "env" else "crux-home"
    if _is_env_name(real.name):
        return "env-file"
    return None
