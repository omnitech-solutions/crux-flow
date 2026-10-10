"""Trusted profile-three start-snapshot YAML interpretation.

The reachable shared loader is frozen here without changing its semantics.
PyYAML and the standard library remain external infrastructure. Retained source
is evidence and is never executed. An issued profile requires a new version
before its owned parser interpretation changes.
"""
from __future__ import annotations
import re
from typing import Any

class YamlCapabilityError(RuntimeError):
    """The current Python environment cannot parse this document faithfully.

    Raised (a) by ``ensure_real_yaml`` when PyYAML is absent and the uv
    re-exec repair is unavailable/declined, and (b) by ``load_yaml`` when the
    minimal fallback parser fails — in that case the document is EITHER
    invalid OR merely exceeds the fallback's subset, and only a real parser
    can distinguish the two. Callers MUST surface this as an ENVIRONMENT
    problem (the crux crash lane: non-zero exit + stderr, no findings JSON),
    never as a document-validation verdict.
    """


REMEDIATION = (
    "this environment lacks PyYAML, and the minimal fallback parser cannot "
    "guarantee a faithful parse — run under `uv run python3 ...` or install "
    "pyyaml (`pip install pyyaml`)"
)


def load_yaml(text: str) -> Any:
    """Parse a YAML document. Uses PyYAML's ``safe_load`` when available;
    otherwise a minimal hand-rolled parser sufficient for the promptbook/run
    document shapes (mappings, sequences, scalars, literal block scalars).

    Returns a dict or list (or a scalar for a bare-scalar document). Raises
    on a structurally malformed document so callers can surface a parse error.
    Without PyYAML, a fallback-parse failure raises ``YamlCapabilityError``
    instead (invalid-vs-exceeds-subset is indistinguishable there); callers
    producing verdicts/hashes must call ``ensure_real_yaml()`` first and never
    reach the fallback.
    """
    try:
        import yaml  # type: ignore

        # INVARIANT: safe_load ONLY. Never yaml.load — it can build arbitrary
        # Python objects from a crafted document. See module docstring.
        return _normalize(yaml.safe_load(text))
    except ImportError:
        # PARITY CONTRACT (ADR-0023 §3 / docs/AGENTS.md §13.2): the fallback path
        # must return the SAME Python value PyYAML+_normalize does, so the
        # book_content_hash lock-step holds whether or not PyYAML is installed.
        # Both paths funnel through _normalize for the date/datetime coercion;
        # _parse_scalar separately mirrors safe_load's YAML-1.1 scalar resolution
        # for the cases reachable in a promptbook/run document (see its docstring).
        # KNOWN LIMIT (PB-0026): parity holds only within the minimal subset —
        # correctness-critical consumers must call ensure_real_yaml() instead of
        # relying on this path (empirical divergences exist outside the subset).
        try:
            return _normalize(_parse_minimal_yaml(text))
        except Exception as exc:
            # Without a real parser we cannot tell "invalid document" from
            # "exceeds the minimal subset" — surface as a capability problem.
            raise YamlCapabilityError(
                f"minimal-parser failure without PyYAML ({exc}); the document is "
                f"either invalid or exceeds the fallback subset — {REMEDIATION}"
            ) from exc


class CatalogYamlError(ValueError):
    """A ``crux/catalog/*.yml`` document violates the strict catalog contract.

    Distinct from ``YamlCapabilityError``: this is a DOCUMENT verdict (the file
    is wrong and a human must fix it), not an environment problem. Callers
    surface it as a validation finding, not as the crash lane.
    """


def _normalize(obj: Any) -> Any:
    """Coerce PyYAML's non-JSON-native scalars back to the forms the hand-rolled
    fallback produces, so the two parse paths stay byte-identical (the lock-step
    discipline ADR-0023 §3 requires for the plan hash).

    PyYAML's ``safe_load`` resolves an unquoted ``2026-05-29`` to ``datetime.date``
    and ``2026-05-29T12:00:00Z`` to ``datetime.datetime``. The promptbook/run
    schemas type these fields as ``string`` and the fallback parser leaves them
    as strings, so we render any date/datetime back to its ISO-8601 text form.
    Recurses through mappings and sequences.
    """
    import datetime as _dt

    if isinstance(obj, dict):
        return {k: _normalize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_normalize(v) for v in obj]
    if isinstance(obj, _dt.datetime):
        # Preserve a trailing 'Z' for UTC; PyYAML strips it into tzinfo=UTC.
        text = obj.isoformat()
        if obj.tzinfo is not None and text.endswith("+00:00"):
            text = text[:-6] + "Z"
        return text
    if isinstance(obj, _dt.date):
        return obj.isoformat()
    return obj


def _parse_minimal_yaml(text: str, *, strict: bool = False) -> Any:
    """Parse the minimal subset. ``strict=True`` additionally rejects a
    repeated mapping key, so the fallback path agrees with the composer-based
    check ``load_catalog_yaml`` runs when PyYAML is present. Default False —
    the promptbook/run path is unchanged."""
    lines = text.splitlines()
    # Pre-scan into a list of physical lines; the block-collection logic needs
    # raw text (for literal scalars) so we keep the originals and compute
    # indent on demand.
    pos = [0]  # boxed cursor so helpers can advance it

    def _peek() -> str | None:
        i = pos[0]
        while i < len(lines):
            stripped = _strip_comment(lines[i])
            if stripped.strip() == "":
                i += 1
                continue
            pos[0] = i
            return lines[i]
        pos[0] = len(lines)
        return None

    def _indent_of(line: str) -> int:
        return len(line) - len(line.lstrip(" "))

    def _parse_block(min_indent: int) -> Any:
        """Parse a mapping or sequence whose items are at exactly the indent of
        the first non-blank line at/after the cursor (which must be >= min_indent)."""
        first = _peek()
        if first is None:
            return None
        cur_indent = _indent_of(first)
        if cur_indent < min_indent:
            return None
        content = _strip_comment(first).strip()
        if content.startswith("- ") or content == "-":
            return _parse_sequence(cur_indent)
        return _parse_mapping(cur_indent)

    def _parse_mapping(indent: int) -> dict:
        result: dict[str, Any] = {}
        while True:
            line = _peek()
            if line is None:
                break
            li = _indent_of(line)
            if li < indent:
                break
            if li > indent:
                raise ValueError(f"unexpected indentation in mapping: {line!r}")
            content = _strip_comment(line).strip()
            if content.startswith("- ") or content == "-":
                # A sequence at the same indent as the mapping keys is invalid
                # in this subset (sequences live under a key or at top level).
                raise ValueError(f"sequence item where a mapping key was expected: {line!r}")
            if ":" not in content:
                raise ValueError(f"expected 'key: value' mapping line, got {line!r}")
            key, _, value = content.partition(":")
            key = key.strip()
            value = value.strip()
            pos[0] += 1  # consume the key line
            if strict and key in result:
                raise CatalogYamlError(f"duplicate mapping key {key!r}")

            if value and value[0] in ("|", ">"):
                result[key] = _parse_block_scalar(value, indent)
            elif value == "":
                # Nested block (mapping or sequence) OR an empty value.
                nxt = _peek()
                if nxt is not None and _indent_of(nxt) > indent:
                    result[key] = _parse_block(indent + 1)
                else:
                    result[key] = None
            else:
                result[key] = _parse_scalar(value)
        return result

    def _parse_sequence(indent: int) -> list:
        items: list[Any] = []
        while True:
            line = _peek()
            if line is None:
                break
            li = _indent_of(line)
            if li < indent:
                break
            if li > indent:
                raise ValueError(f"unexpected indentation in sequence: {line!r}")
            content = _strip_comment(line).strip()
            if not (content.startswith("- ") or content == "-"):
                break
            item_body = content[1:].lstrip()  # strip leading '-'
            pos[0] += 1  # consume the '- ' line

            if item_body == "":
                # Item value is on the following indented lines (a nested block).
                nxt = _peek()
                if nxt is not None and _indent_of(nxt) > indent:
                    items.append(_parse_block(indent + 1))
                else:
                    items.append(None)
            elif ":" in item_body and not _looks_like_flow_or_quoted(item_body):
                # Inline mapping start: "- key: value". The dash introduces a
                # mapping whose first key sits on this line; subsequent keys are
                # indented to align under item_body's column.
                # Re-inject the first key line as a virtual mapping at the
                # column where item_body begins.
                first_key, _, first_val = item_body.partition(":")
                m: dict[str, Any] = {}
                fv = first_val.strip()
                key_col = li + (len(content) - len(item_body))
                if fv and fv[0] in ("|", ">"):
                    m[first_key.strip()] = _parse_block_scalar(fv, key_col)
                elif fv == "":
                    nxt = _peek()
                    if nxt is not None and _indent_of(nxt) > key_col:
                        m[first_key.strip()] = _parse_block(key_col + 1)
                    else:
                        m[first_key.strip()] = None
                else:
                    m[first_key.strip()] = _parse_scalar(fv)
                # Remaining keys of this mapping item at the key_col indent.
                rest = _parse_mapping_at(key_col)
                m.update(rest)
                items.append(m)
            else:
                items.append(_parse_scalar(item_body))
        return items

    def _parse_mapping_at(indent: int) -> dict:
        """Parse additional mapping keys at exactly ``indent`` (used to gather
        the trailing keys of an inline '- key: value' sequence item)."""
        result: dict[str, Any] = {}
        while True:
            line = _peek()
            if line is None:
                break
            li = _indent_of(line)
            if li != indent:
                break
            content = _strip_comment(line).strip()
            if content.startswith("- ") or content == "-":
                break
            if ":" not in content:
                break
            key, _, value = content.partition(":")
            key = key.strip()
            value = value.strip()
            pos[0] += 1
            if strict and key in result:
                raise CatalogYamlError(f"duplicate mapping key {key!r}")
            if value and value[0] in ("|", ">"):
                result[key] = _parse_block_scalar(value, indent)
            elif value == "":
                nxt = _peek()
                if nxt is not None and _indent_of(nxt) > indent:
                    result[key] = _parse_block(indent + 1)
                else:
                    result[key] = None
            else:
                result[key] = _parse_scalar(value)
        return result

    def _parse_block_scalar(indicator: str, parent_indent: int) -> str:
        """Consume a literal (`|`) or folded (`>`) block scalar body. Lines more
        indented than ``parent_indent`` belong to the scalar; the common leading
        indentation is stripped."""
        style = indicator[0]
        chomp = indicator[1:2] if indicator[1:2] in ("-", "+") else ""
        body_lines: list[str] = []
        block_indent: int | None = None
        i = pos[0]
        while i < len(lines):
            raw = lines[i]
            if raw.strip() == "":
                body_lines.append("")
                i += 1
                continue
            ind = _indent_of(raw)
            if ind <= parent_indent:
                break
            if block_indent is None:
                block_indent = ind
            if ind < block_indent:
                break
            body_lines.append(raw[block_indent:])
            i += 1
        pos[0] = i
        # Trim trailing blank lines.
        while body_lines and body_lines[-1] == "":
            body_lines.pop()
        if style == "|":
            folded = "\n".join(body_lines)
        else:  # ">" folded: single newlines -> spaces, blank lines kept.
            parts: list[str] = []
            paragraph: list[str] = []
            for ln in body_lines:
                if ln == "":
                    if paragraph:
                        parts.append(" ".join(paragraph))
                        paragraph = []
                    parts.append("")
                else:
                    paragraph.append(ln)
            if paragraph:
                parts.append(" ".join(paragraph))
            folded = "\n".join(parts)
        if chomp == "-":
            return folded
        # clip (default) / keep both append one trailing newline for our needs.
        return folded + "\n"

    # Drive the top-level parse.
    if _peek() is None:
        return {}
    top = _strip_comment(lines[pos[0]]).strip()
    if top.startswith("- ") or top == "-":
        return _parse_sequence(_indent_of(lines[pos[0]]))
    return _parse_mapping(_indent_of(lines[pos[0]]))


def _looks_like_flow_or_quoted(s: str) -> bool:
    """True if ``s`` is a flow collection or a quoted scalar — i.e. the ':' in
    it is NOT a mapping separator."""
    s = s.strip()
    if not s:
        return False
    if s[0] in ("[", "{", '"', "'"):
        return True
    return False


def _strip_comment(line: str) -> str:
    """Strip a ``#`` comment from a line, respecting quoted strings."""
    out: list[str] = []
    in_str: str | None = None
    for ch in line:
        if in_str:
            out.append(ch)
            if ch == in_str:
                in_str = None
            continue
        if ch in ('"', "'"):
            in_str = ch
            out.append(ch)
            continue
        if ch == "#":
            # A '#' that starts a comment must be preceded by whitespace or be
            # at line start; otherwise it's part of an unquoted scalar.
            if not out or out[-1] in (" ", "\t"):
                break
            out.append(ch)
            continue
        out.append(ch)
    return "".join(out)


def _parse_scalar(value: str) -> Any:
    # PARITY CONTRACT (ADR-0023 §3 / docs/AGENTS.md §13.2): this hand-rolled
    # coercion must agree with ``yaml.safe_load``'s YAML-1.1 resolution for every
    # value reachable in a promptbook/run document, because both parse paths feed
    # the same ``book_content_hash``. We mirror the cases that matter:
    #   - YAML-1.1 booleans yes|no|true|false|on|off (case-insensitive) -> bool
    #   - plain dates / datetimes (incl. the space-separated YYYY-MM-DD HH:MM:SS
    #     form) -> date/datetime OBJECTS, which the shared ``_normalize`` then
    #     coerces to the identical ISO/'T' string PyYAML+_normalize produces.
    # Exotic YAML-1.1 forms (hex 0x.., octal, sexagesimal) are intentionally NOT
    # resolved — they don't appear in our documents and adding them would risk
    # diverging from safe_load rather than converging.
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(p.strip()) for p in _split_flow(inner)]
    if value.startswith("{") and value.endswith("}"):
        inner = value[1:-1].strip()
        result: dict[str, Any] = {}
        if not inner:
            return result
        for pair in _split_flow(inner):
            k, _, v = pair.partition(":")
            result[k.strip()] = _parse_scalar(v.strip())
        return result
    low = value.lower()
    if low in ("null", "~", ""):
        return None
    # YAML-1.1 boolean spellings (case-insensitive), matching safe_load.
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        inner = value[1:-1]
        if value[0] == '"':
            return _unescape_double_quoted(inner)
        return inner.replace("''", "'")
    # Plain (unquoted) date / datetime: return the date/datetime OBJECT so the
    # shared _normalize step renders it to the SAME ISO/'T' string PyYAML's
    # safe_load + _normalize produce (parity contract above). Quoted forms are
    # handled before this point and stay literal strings, matching safe_load.
    ts = _try_parse_timestamp(value)
    if ts is not None:
        return ts
    try:
        if "." not in value and "e" not in low and "E" not in value:
            return int(value)
    except ValueError:
        pass
    # YAML-1.1 floats require a decimal point in the mantissa, so safe_load keeps
    # a bare-exponent token like ``1e3`` as a STRING. Gate the float coercion on a
    # '.' to match (parity contract above); without this the two paths diverge on
    # ``1e3`` (str vs 1000.0). Exotic numeric forms (hex/octal/underscored/
    # sexagesimal) are intentionally left as strings here — they don't appear in
    # our documents and faithfully mirroring PyYAML's resolver for them is more
    # divergence risk than it's worth.
    if "." in value:
        try:
            return float(value)
        except ValueError:
            return value
    return value


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


_DATETIME_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})[Tt ]+" r"(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?" r"(?:\s*(Z|[+-]\d{1,2}(?::\d{2})?))?$"
)


def _try_parse_timestamp(value: str):
    """Return a ``datetime.date`` / ``datetime.datetime`` for the YAML-1.1
    timestamp forms ``safe_load`` resolves, else ``None``. Kept narrow on
    purpose — only the ISO-ish date/datetime shapes our documents use."""
    import datetime as _dt

    if _DATE_RE.match(value):
        try:
            return _dt.date.fromisoformat(value)
        except ValueError:
            return None
    m = _DATETIME_RE.match(value)
    if not m:
        return None
    date_part, hh, mm, ss, frac, tz = m.groups()
    try:
        year, month, day = (int(p) for p in date_part.split("-"))
        micro = int((frac or "").ljust(6, "0")[:6]) if frac else 0
        tzinfo = None
        if tz == "Z":
            tzinfo = _dt.timezone.utc
        elif tz:
            sign = 1 if tz[0] == "+" else -1
            body = tz[1:]
            if ":" in body:
                oh, om = body.split(":")
            else:
                oh, om = body, "0"
            tzinfo = _dt.timezone(sign * _dt.timedelta(hours=int(oh), minutes=int(om)))
        return _dt.datetime(year, month, day, int(hh), int(mm), int(ss), micro, tzinfo)
    except ValueError:
        return None


def _split_flow(inner: str) -> list[str]:
    """Split a flow-collection body on top-level commas (respecting quotes and
    nested [] / {})."""
    parts: list[str] = []
    depth = 0
    in_str: str | None = None
    cur: list[str] = []
    for ch in inner:
        if in_str:
            cur.append(ch)
            if ch == in_str:
                in_str = None
            continue
        if ch in ('"', "'"):
            in_str = ch
            cur.append(ch)
            continue
        if ch in ("[", "{"):
            depth += 1
            cur.append(ch)
            continue
        if ch in ("]", "}"):
            depth -= 1
            cur.append(ch)
            continue
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
            continue
        cur.append(ch)
    if cur:
        parts.append("".join(cur))
    return parts


def _unescape_double_quoted(s: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            nxt = s[i + 1]
            if nxt == '"':
                out.append('"')
            elif nxt == "\\":
                out.append("\\")
            elif nxt == "n":
                out.append("\n")
            elif nxt == "t":
                out.append("\t")
            elif nxt == "/":
                out.append("/")
            else:
                out.append(c)
                out.append(nxt)
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)
