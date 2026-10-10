#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0"]
# ///
"""Validator for the crux catalog layer per ADR-0001-distribute-crux-as-a-single-monorepo-plugin.

Sibling validator to extract-code-docs.py. Same exit-code semantics.

The catalog layer is four files under ${CRUX_PLUGIN_ROOT}/catalog/ plus extended
SKILL.md frontmatter on every skill. The extension declares provenance:
`skills.json` and `agents.json` are REGENERATED projections, `bundles.yml` and
`models.yml` are HAND-AUTHORED sources of truth this script validates and never
writes. This script:

  1. Walks ${CRUX_PLUGIN_ROOT}/skills/*/SKILL.md.
  2. Parses YAML frontmatter (the block between the first two `---` lines).
  3. Validates required keys and enum values per docs/AGENTS.md §7.A.
  4. Regenerates catalog/skills.json deterministically (sorted by `id`,
     fixed key order, 2-space indent, trailing newline).
  5. Validates every hand-authored catalog file named in CATALOG_TARGETS:
     catalog/bundles.yml against the regenerated skills.json for referential
     integrity, and catalog/models.yml against rules V0-V9.

ADR-0005 established that extended crux frontmatter fields live UNDER a
`metadata:` mapping in SKILL.md, not at top level. ADR-0092 amended the
contract: it pruned the constant metadata keys `owner`, `version`, and
`status` (every catalog file carried the identical value, so they encoded
nothing), removed the never-wired `skills-ref` dependency, and admitted the
Claude Code invocation-control keys at the top level (SKILL_INVOCATION_KEYS
below). The operative allowlists are the constants in this file, not the
public spec. Current shape:

  - Top-level allowed keys: `name`, `description`, `license`,
    `allowed-tools`, `metadata`, `compatibility`, plus the ADR-0092
    invocation-control loader keys (SKILL_INVOCATION_KEYS).
  - Under `metadata:` (flat string key/value pairs):

      tags          (CSV string, e.g. "a, b, c")
      bundles       (CSV string)
      risk_level    (string; enum {low,medium,high})
      requires_env  (CSV string of env var names; each ^[A-Z][A-Z0-9_]*$)
      routing_note  (string; optional §10 routing-table Notes cell)

  - CSV encoding: ", " (comma + single space). On parse: split on `,`,
    strip each token, reject empty tokens.

This script lifts those fields from `metadata:` back into the FLAT
top-level shape that `catalog/skills.json` continues to use (consumers
of the catalog JSON are unaffected by ADR-0005). The optional
`requires_env` key likewise lives under `metadata:` post-ADR-0005, and
is lifted into top-level JSON only when present.

Per ADR-0034, the metadata contract is STRICT: any `metadata.*` key not
in the recognized set above is a validation error. (The former
provenance keys `origin`/`origin_ref`/`origin_date` were removed from
the contract by ADR-0034 and now fail validation like any other unknown
key.)

Drift back to top-level extended fields is detected and reported as a
shape error per skill: "field 'X' must be under metadata: (ADR-0005)".

Regenerative invariant: skills.json is rewritten from SKILL.md frontmatter on
every run. Hand-edits to skills.json are blown away (same model as docs/code/).
bundles.yml is hand-authored; the validator only enforces referential
integrity against the regenerated skills.json.

Exit codes match extract-code-docs.py:
  0 — clean (no drift, no validation errors).
  1 — drift detected OR validation errors. Valid JSON on stdout.
  non-zero with empty/unparseable stdout — crash; surface stderr.
"""

from __future__ import annotations

import argparse
import datetime
import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable

# The shared strict catalog reader + the models-catalog rules. Imported by name
# when scripts/ is on sys.path and by file location otherwise, mirroring
# validate-promptbook.py.
_SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS_DIR))
import authoring_scope as _scope  # noqa: E402


def _sibling(name: str) -> Any:
    try:
        return __import__(name)
    except ImportError:
        spec = importlib.util.spec_from_file_location(name, _SCRIPTS_DIR / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        # Register before exec: dataclasses resolves a class's own module out
        # of sys.modules while processing it.
        sys.modules.setdefault(name, module)
        spec.loader.exec_module(module)
        return module


_yaml_min = _sibling("_yaml_min")
models_catalog = _sibling("models_catalog")


def _atomic_write_text(path: Path, body: str) -> None:
    """Atomic UTF-8 text write with no platform newline translation.

    Writes to <path>.tmp then os.replace()s into place. Bytes are written
    directly so '\\n' is NOT translated to '\\r\\n' on Windows — that would
    shift the file's sha256 across platforms and break byte-stable output.
    Mirrors the helper in extract-code-docs.py; kept inline here because the
    two scripts have no shared utility module (and adding one for two
    callers is overkill). The mirror is the reason the [SECURITY:S5] guard
    below had to land in three places at once.

    [SECURITY:S5] Neither the target nor `<path>.tmp` may be a symlink. The
    tmp path is predictable and `Path.write_bytes` follows a link, so an
    attacker who can create a file in `crux/catalog/` gets skills.json's or
    agents.json's contents written into a target of their choosing, while
    `os.replace` moves the tmp PATH and leaves the link in place. Checked
    explicitly for a readable error, then created O_NOFOLLOW|O_EXCL so the
    check is not a TOCTOU window. Structural siblings, all fixed together:
    `_yaml_min.write_catalog_yaml` and `extract-code-docs.py`'s copy of this
    same helper.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    for label, candidate in (("target", path), ("temporary file", tmp)):
        if candidate.is_symlink():
            raise OSError(
                f"refusing to write {path}: the {label} {candidate} is a symlink — "
                "writing through it would put catalog contents in the link's target"
            )
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    except FileExistsError as exc:
        raise OSError(
            f"refusing to write {path}: the temporary file {tmp} already exists; "
            "remove it after checking what created it"
        ) from exc
    except OSError as exc:  # ELOOP from O_NOFOLLOW, or an unwritable directory
        raise OSError(f"refusing to write {path}: cannot create {tmp} ({exc})") from exc
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(body.encode("utf-8"))
        os.replace(tmp, path)
    except Exception:
        # Clean up partial tmp before re-raising (SC-1).
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise


# ─────────────────────────── catalog schema constants ─────────────────────


# Per ADR-0092: the constant metadata keys `owner`, `version`, and `status`
# were removed — every catalog file carried the identical value, so they
# encoded nothing. A lingering one is now a validation error (it lands in
# KNOWN_METADATA_KEYS' complement and fails the strict metadata contract).
SCHEMA_KEYS = [
    "name",
    "description",
    "tags",
    "bundles",
    "risk_level",
]

# Optional metadata keys. `requires_env` is the env-requirement list;
# `routing_note` (ADR-0092 / the §10 routing-table generator) is the optional
# Notes cell for a skill's routing-table row.
OPTIONAL_SCHEMA_KEYS = [
    "requires_env",
    "routing_note",
    "triggers",
]

# Per ADR-0092: the Claude Code skill invocation-control top-level keys. These
# are loader keys (legal at the top level, NOT under metadata:), type-validated
# by _validate_skill_loader_keys, and projected into skills.json present-only.
EFFORT_LEVELS = {"low", "medium", "high", "xhigh", "max"}
CONTEXT_VALUES = {"fork"}
MEMORY_SCOPES = {"user", "project", "local"}
ISOLATION_VALUES = {"worktree"}
AGENT_SKILL_NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]*")

SKILL_INVOCATION_KEYS = [
    "disable-model-invocation",
    "user-invocable",
    "context",
    "agent",
    "model",
    "effort",
    "background",
    "arguments",
    "disallowed-tools",
]

# Output ordering: required keys first (existing order), then optional +
# invocation-control keys. Optional/invocation keys are only emitted when
# present on the skill (no null sentinels).
OUTPUT_KEY_ORDER = [
    "id",
    "name",
    "description",
    "tags",
    "bundles",
    "risk_level",
    "requires_env",
    "routing_note",
    "triggers",
    *SKILL_INVOCATION_KEYS,
]

RISK_LEVELS = {"low", "medium", "high"}
# Per ADR-0092 the `status` metadata key was pruned; its enum constant went with
# the removed status check (no remaining reader).

ENV_VAR_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")

# Per ADR-0005: the spec-conformant top-level allowlist. Any other top-level
# key triggers an "unknown frontmatter key" error. Extended crux fields
# (tags/bundles/risk_level/requires_env) live under `metadata:` and are lifted
# by _lift_metadata() before validation. Per ADR-0092 owner/version/status were
# pruned from the metadata contract — a lingering one is now a validation error.
SPEC_TOPLEVEL_ALLOWED = {
    "name",
    "description",
    "license",
    "allowed-tools",
    "metadata",
    "compatibility",
}

# Top-level LOADER keys the runtime honors that are neither spec-required fields
# nor crux metadata fields. Per ADR-0092 these are the governed invocation-
# control allowlist: legal at the top level, type-validated, and (unlike the
# pre-ADR-0092 ad-hoc `disable-model-invocation` special case) projected into
# catalog/skills.json present-only so the catalog's public shape is complete.
TOPLEVEL_LOADER_KEYS = set(SKILL_INVOCATION_KEYS)

# Keys that, if found at top level (pre-lift), trigger an ADR-0005 drift
# error. These are the fields ADR-0005 moved under metadata:.
EXTENDED_FIELDS_UNDER_METADATA = set(SCHEMA_KEYS[2:]) | set(OPTIONAL_SCHEMA_KEYS)
# (SCHEMA_KEYS[0:2] are 'name' and 'description' which remain top-level.)

# Subset of metadata.* fields whose canonical form in catalog/skills.json is
# a JSON list. _lift_metadata() splits their CSV string on `,` and strips.
LIST_VALUED_METADATA = {"tags", "bundles", "requires_env"}

# `metadata.triggers` is list-valued too, but PIPE-separated rather than CSV.
# rule:triggers-are-declared-in-frontmatter, rule:triggers-separator-fits-the-value-type.
# Its tokens are the user phrases that route to the skill, and a phrase may
# contain a comma -- `this is small, skip the cycle` is one of them. Splitting
# that on `,` yields two phrases nobody says, so the field takes the separator
# its value type allows instead of the one its neighbours happen to use. A
# trigger containing a pipe cannot be encoded and is refused, not split.
PIPE_VALUED_METADATA = {"triggers"}
TRIGGER_SEPARATOR = "|"

# Set of all keys the validator recognizes (post-lift, in the flat shape used
# by skills.json). Any other top-level key in the lifted frontmatter is
# treated as unknown.
ALL_KNOWN_KEYS = set(SCHEMA_KEYS) | set(OPTIONAL_SCHEMA_KEYS)

# The full set of keys recognized UNDER `metadata:`. Per ADR-0034 the
# metadata contract is strict: anything else under `metadata:` is a
# validation error (this is what makes a leftover provenance key fail
# loudly rather than pass through).
KNOWN_METADATA_KEYS = set(SCHEMA_KEYS[2:]) | set(OPTIONAL_SCHEMA_KEYS)

BUNDLE_REQUIRED = {
    "id",
    "name",
    "description",
    "audiences",
    "default_provision",
    "skills",
}


# ─────────────────────────── agents catalog constants ─────────────────────
# Per ADR-0028: agents are flat files at ${CRUX_PLUGIN_ROOT}/agents/*.md with YAML
# frontmatter that mirrors the SKILL.md catalog contract (ADR-0005) but with a
# DIFFERENT top-level allowlist. Unlike skills, `tools` and `model` are
# LEGITIMATELY top-level for agents (they are loader keys, not crux
# extended fields), so the ADR-0005 `_lift_metadata` drift-check is NOT reused
# on this path. Extended fields still live under `metadata:`.

# Per ADR-0092: the agent invocation-control top-level keys added to ADR-0028's
# allowlist. `permissionMode` is EXCLUDED (Claude Code ignores it on plugin
# agents). Each projects to Codex/OpenCode per the ADR's locked faithful-or-drop
# table, handled in codex_agents.py / opencode_agents.py.
AGENT_INVOCATION_KEYS = [
    "maxTurns",
    "effort",
    "skills",
    "memory",
    "isolation",
    "disallowedTools",
]

# Top-level keys the agent loader recognizes. Anything else is reported as an
# unknown top-level key.
AGENT_TOPLEVEL_ALLOWED = {
    "name",
    "description",
    "tools",
    "model",
    "metadata",
    *AGENT_INVOCATION_KEYS,
}

# Allowed values for the top-level `model` loader key. The set is NOT written
# here — this file, the smoke test and rule V3 all READ it rather than restate
# it. Three surfaces each believing they were the enum is how the previous
# copies diverged.
#
# The enum is a TWO-FILE PIN, not one list. `claude_aliases` in
# catalog/models.yml carries the membership, and `models_catalog.CLAUDE_ALIASES`
# carries the same tuple as a constant that rule V0 compares the file against —
# so the file cannot widen its own enum, and a deliberate widening is an edit to
# both. Reading only the data file and calling it the source of truth is how a
# hostile or careless catalog edit would have added a value; reading only the
# constant would ignore the file the loader actually parses. Both must move
# together, and V0's `claude_aliases must be exactly ...` finding is the gate
# that says so.


def agent_model_enum(plugin_dir: Path) -> tuple[set[str] | None, str | None]:
    """Return (allowed_models, error). Fail closed: an unreadable catalog is an
    error, never a silent fallback to a literal set."""
    try:
        raw = models_catalog.load_raw(plugin_dir / "catalog" / "models.yml")
    except models_catalog.SpecViolation as exc:
        return None, f"cannot read claude_aliases from catalog/models.yml: {exc}"
    values = raw.get("claude_aliases")
    if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
        return None, "catalog/models.yml `claude_aliases` is not a list of strings"
    return set(values), None

# Required keys under the agent's `metadata:` mapping. Per ADR-0092 the
# constant keys owner/version/status were pruned here too.
AGENT_METADATA_KEYS = [
    "tags",
    "bundles",
    "risk_level",
]

# The full set of keys recognized under an agent's `metadata:`. Anything else
# is a validation error (strict metadata contract, extended to agents by
# ADR-0092 so a lingering owner/version/status fails loudly).
KNOWN_AGENT_METADATA_KEYS = set(AGENT_METADATA_KEYS)

# Subset of agent metadata.* fields whose canonical form in agents.json is a
# JSON list (CSV split on `,`, strip each token).
AGENT_LIST_VALUED_METADATA = {"tags", "bundles"}

# Output ordering for catalog/agents.json entries. Invocation-control keys are
# emitted present-only (no null sentinels), after the metadata block.
AGENT_OUTPUT_KEY_ORDER = [
    "id",
    "name",
    "description",
    "tools",
    "model",
    "tags",
    "bundles",
    "risk_level",
    *AGENT_INVOCATION_KEYS,
]


# ─────────────────────────────── argparse ──────────────────────────────────


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="validate-catalog",
        description=(
            "Validate the crux catalog against SKILL.md frontmatter. "
            "Regenerates catalog/skills.json from skills/*/SKILL.md and "
            "checks catalog/bundles.yml for referential integrity and "
            "catalog/models.yml against rules V0-V9."
        ),
    )
    parser.add_argument(
        "--config",
        default=None,
        type=Path,
        help=(
            "Path to plugin.json (default: ${CRUX_PLUGIN_ROOT}/plugin.json). "
            "Accepted for parity with extract-code-docs.py; not strictly required."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Parse + validate + emit JSON diff to stdout. Do not write files. "
            "Exit 1 if drift or validation errors; exit 0 if clean."
        ),
    )
    parser.add_argument(
        "--repo-root",
        default=None,
        help="repo root to inspect (default: the working directory)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose per-skill logging to stderr.",
    )
    return parser.parse_args(argv)


# ─────────────────────────── minimal YAML loader ──────────────────────────


def load_yaml(text: str) -> dict:
    """Parse a YAML FRONTMATTER block. Uses PyYAML when available; otherwise
    a minimal hand-rolled parser sufficient for SKILL.md frontmatter
    (mappings, scalars, flow lists, block-style lists with `- ` items).

    Scope note: the `else {}` coercion below is correct for frontmatter, where
    a non-mapping block is a malformed skill the caller reports on its own. It
    is NOT the catalog read path — catalog files go through
    `_yaml_min.load_catalog_yaml`, which refuses a non-mapping root outright,
    because there the same coercion turned a top-level sequence into a green
    run over zero bundles.
    """
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text)
        return data if isinstance(data, dict) else {}
    except ImportError:
        return _parse_minimal_yaml(text)


def _parse_minimal_yaml(text: str) -> dict:
    """Tiny YAML subset: top-level mappings, scalar values, flow lists [a, b],
    and block-style lists (`key:` followed by `  - item` lines). Enough for
    SKILL.md frontmatter. Comments stripped. No anchors, no multi-line strings,
    no nested mappings beyond what frontmatter needs.
    """
    root: dict = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        line = _strip_yaml_comment(raw).rstrip()
        if not line.strip():
            i += 1
            continue
        indent = len(line) - len(line.lstrip(" "))
        content = line.strip()
        # Only consume top-level (indent 0) keys; block-list items at deeper
        # indents are consumed inline below.
        if indent != 0 or ":" not in content:
            i += 1
            continue
        key, _, value = content.partition(":")
        key = key.strip()
        value = value.strip()
        # Folded (`>`) or literal (`|`) block-scalar indicator (with optional
        # chomp marker `-` or `+`). Consume subsequent indented lines as the
        # scalar body. Common in crux SKILL.md frontmatter for long
        # multi-line descriptions.
        if value and value[0] in (">", "|"):
            style = value[0]
            chomp = value[1:2] if len(value) >= 2 and value[1:2] in ("-", "+") else ""
            j = i + 1
            block_lines: list[str] = []
            block_indent: int | None = None
            while j < len(lines):
                nxt_raw = lines[j]
                if not nxt_raw.strip():
                    block_lines.append("")
                    j += 1
                    continue
                nxt_indent = len(nxt_raw) - len(nxt_raw.lstrip(" "))
                if nxt_indent == 0:
                    break  # back to top-level
                if block_indent is None:
                    block_indent = nxt_indent
                if nxt_indent < block_indent:
                    break
                block_lines.append(nxt_raw[block_indent:].rstrip())
                j += 1
            # Trim trailing empty lines.
            while block_lines and block_lines[-1] == "":
                block_lines.pop()
            if style == "|":
                folded = "\n".join(block_lines)
                root[key] = folded + ("" if chomp == "-" else "\n")
            else:  # ">" folded — replace single newlines with spaces, keep blank-line breaks.
                parts: list[str] = []
                paragraph: list[str] = []
                for ln in block_lines:
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
                # Default chomp (clip): preserve one trailing newline.
                root[key] = folded + ("" if chomp == "-" else "\n")
            i = j
            continue
        if value == "":
            # Could be EITHER a block-style list (`  - item` lines) OR a
            # nested mapping (`  subkey: value` lines) — per ADR-0005,
            # `metadata:` is the only nested mapping we expect, with flat
            # string sub-values (no further nesting).
            items: list[Any] = []
            submap: dict = {}
            j = i + 1
            saw_list = False
            saw_mapping = False
            while j < len(lines):
                nxt_raw = lines[j]
                nxt = _strip_yaml_comment(nxt_raw).rstrip()
                if not nxt.strip():
                    j += 1
                    continue
                nxt_indent = len(nxt) - len(nxt.lstrip(" "))
                stripped = nxt.strip()
                if nxt_indent == 0:
                    break  # back to top-level
                if stripped.startswith("- "):
                    if saw_mapping:
                        break  # can't mix list + mapping under one key
                    saw_list = True
                    items.append(_parse_scalar(stripped[2:].strip()))
                    j += 1
                    continue
                if stripped == "-":
                    if saw_mapping:
                        break
                    saw_list = True
                    items.append(None)
                    j += 1
                    continue
                # Sub-mapping line: `subkey: value`.
                if not saw_list and ":" in stripped:
                    sk, _, sv = stripped.partition(":")
                    submap[sk.strip()] = _parse_scalar(sv.strip())
                    saw_mapping = True
                    j += 1
                    continue
                break
            if saw_list:
                root[key] = items
                i = j
                continue
            if saw_mapping:
                root[key] = submap
                i = j
                continue
            # Empty value with no list and no sub-mapping — store as empty dict.
            root[key] = {}
            i += 1
            continue
        root[key] = _parse_scalar(value)
        i += 1
    return root


def _strip_yaml_comment(line: str) -> str:
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
            break
        out.append(ch)
    return "".join(out)


def _parse_scalar(value: str) -> Any:
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        # Split on top-level commas (no nested flow structures expected here).
        return [_parse_scalar(p.strip()) for p in inner.split(",")]
    if value.lower() in ("null", "~", ""):
        return None
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        inner = value[1:-1]
        if value[0] == '"':
            return _unescape_yaml_double_quoted(inner)
        # Single-quoted: only `''` → `'` escape exists; nothing else.
        return inner.replace("''", "'")
    try:
        if "." not in value:
            return int(value)
        return float(value)
    except ValueError:
        return value


def _unescape_yaml_double_quoted(s: str) -> str:
    """Process YAML double-quoted escape sequences. Minimal coverage —
    SKILL.md frontmatter only needs `\\"`, `\\\\`, `\\n`, `\\t`, and `\\/`
    in practice. Unknown escape sequences pass through verbatim, matching
    PyYAML's tolerant behavior for our use case.
    """
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
                # Unknown escape — keep the backslash + char as-is.
                out.append(c)
                out.append(nxt)
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


# ───────────────────────── frontmatter extraction ─────────────────────────


def _lift_metadata(raw_fm: dict) -> tuple[dict, list[str]]:
    """Per ADR-0005, lift extended fields out of `metadata:` into the flat
    top-level shape that skills.json continues to use. Convert CSV strings
    back to lists for `tags`, `bundles`, `requires_env`.

    Returns (lifted_dict, shape_errors). shape_errors is a list of human
    messages — one per extended field found at the wrong nesting level.
    Callers must surface shape_errors as validation errors.

    Idempotent: a SKILL.md that already has the new shape (extended fields
    only under `metadata:`) returns the lifted dict with zero shape errors.
    """
    shape_errors: list[str] = []

    # Drift detection: extended fields must NOT be at top level post-ADR-0005.
    for k in EXTENDED_FIELDS_UNDER_METADATA:
        if k in raw_fm:
            shape_errors.append(f"field {k!r} must be under `metadata:` per ADR-0005, not at top level")

    lifted: dict = {k: v for k, v in raw_fm.items() if k != "metadata"}
    metadata = raw_fm.get("metadata") or {}
    if not isinstance(metadata, dict):
        shape_errors.append("`metadata:` must be a mapping")
        return lifted, shape_errors

    for mk, mv in metadata.items():
        if mk not in KNOWN_METADATA_KEYS:
            # Strict metadata contract per ADR-0034: unknown metadata.* keys
            # are validation errors and are NOT lifted into the catalog shape.
            shape_errors.append(f"unknown metadata key {mk!r} (strict metadata contract per ADR-0034)")
            continue
        if mk in PIPE_VALUED_METADATA:
            if mv is None or mv == "":
                lifted[mk] = []
                continue
            if isinstance(mv, list):
                # rule:triggers-separator-fits-the-value-type -- a token carrying the
                # separator is REFUSED rather than split, because the encoding cannot
                # represent it. Only the LIST form can be refused: in the string form
                # `a|b` is indistinguishable from two tokens by construction. That is
                # the honest limit of the encoding, and the shipped catalog is gated
                # on it separately by test_no_declared_phrase_contains_the_separator.
                non_str = [tok for tok in mv if not isinstance(tok, str)]
                if non_str:
                    shape_errors.append(
                        f"metadata key {mk!r}: every token must be a string; got "
                        f"{[type(tok).__name__ for tok in non_str]}"
                    )
                    continue
                tokens = [tok.strip() for tok in mv]
            elif isinstance(mv, str):
                tokens = [tok.strip() for tok in mv.split(TRIGGER_SEPARATOR)]
            else:
                shape_errors.append(
                    f"metadata key {mk!r}: must be a {TRIGGER_SEPARATOR!r}-separated "
                    f"string or a list of strings; got {type(mv).__name__}"
                )
                continue

            # A token check, on the `requires_env` model. Without one a number, a
            # mapping or a bool was coerced with `str()` and shipped as a phrase, and
            # a token carrying a newline reached the §10 Markdown table -- an
            # instruction-carrying file -- through a cell escape that handles only the
            # pipe. Refuse the token rather than render it.
            carriers = [tok for tok in tokens if TRIGGER_SEPARATOR in tok]
            if carriers:
                shape_errors.append(
                    f"metadata key {mk!r}: token(s) carry the {TRIGGER_SEPARATOR!r} "
                    f"separator and cannot be encoded: {carriers}"
                )
                continue
            control = [tok for tok in tokens if any(c in tok for c in "\r\n\t")]
            if control:
                shape_errors.append(
                    f"metadata key {mk!r}: token(s) carry a control character and "
                    f"cannot be rendered into a table row: {control!r}"
                )
                continue
            tokens = [tok for tok in tokens if tok]
            dupes = sorted({tok for tok in tokens if tokens.count(tok) > 1})
            if dupes:
                shape_errors.append(
                    f"metadata key {mk!r}: duplicate token(s) would render twice: {dupes}"
                )
                continue
            lifted[mk] = tokens
        elif mk in LIST_VALUED_METADATA:
            if mv is None or mv == "":
                lifted[mk] = []
            elif isinstance(mv, list):
                # If a future SKILL.md were hand-authored with a true YAML list
                # under metadata.tags, accept it. (skills-ref will stringify it
                # when validating, but the catalog can keep richer typing.)
                lifted[mk] = mv
            else:
                tokens = [t.strip() for t in str(mv).split(",") if t.strip()]
                lifted[mk] = tokens
        else:
            # Scalar metadata fields stay as strings (after coercion).
            if mv is None:
                continue
            lifted[mk] = str(mv) if not isinstance(mv, str) else mv

    return lifted, shape_errors


def extract_frontmatter(skill_path: Path) -> tuple[dict, str | None, list[str]]:
    """Read SKILL.md and return (lifted_frontmatter, error_or_None, shape_errors).

    The frontmatter block is the content between the first two `---` lines.
    Missing/malformed delimiters yield ({}, error, []). The returned dict has
    extended fields lifted from `metadata:` into the flat top-level shape
    used by skills.json. `shape_errors` lists any per-skill encoding drift
    (e.g., extended fields still at top level) — these become validation
    errors at the caller.
    """
    try:
        text = skill_path.read_text(encoding="utf-8")
    except OSError as exc:
        return {}, f"could not read file: {exc}", []
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, "missing opening `---` frontmatter delimiter", []
    end_idx: int | None = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end_idx = idx
            break
    if end_idx is None:
        return {}, "missing closing `---` frontmatter delimiter", []
    block = "\n".join(lines[1:end_idx])
    try:
        fm_raw = load_yaml(block)
    except Exception as exc:  # noqa: BLE001
        return {}, f"YAML parse error: {exc}", []
    if not isinstance(fm_raw, dict):
        return {}, "frontmatter did not parse to a mapping", []
    fm_lifted, shape_errors = _lift_metadata(fm_raw)
    return fm_lifted, None, shape_errors


# ───────────────────────── skill discovery & validation ───────────────────


def discover_skills(plugin_dir: Path) -> list[Path]:
    """Return sorted list of SKILL.md files under plugin_dir/skills/*/."""
    skills_dir = plugin_dir / "skills"
    if not skills_dir.is_dir():
        return []
    found: list[Path] = []
    for entry in sorted(skills_dir.iterdir()):
        if not entry.is_dir():
            continue
        skill_md = entry / "SKILL.md"
        if skill_md.is_file():
            found.append(skill_md)
    return found


def _validate_skill_loader_keys(fm: dict, rel: str, errors: list[dict]) -> None:
    """Type-check the ADR-0092 invocation-control top-level keys (present-only)."""

    def bad(field: str, msg: str) -> None:
        errors.append({"file": rel, "field": field, "error": msg})

    for boolkey in ("disable-model-invocation", "user-invocable", "background"):
        if boolkey in fm and not isinstance(fm[boolkey], bool):
            bad(boolkey, f"{boolkey!r} must be a boolean")
    if "context" in fm and fm["context"] not in CONTEXT_VALUES:
        bad("context", f"context {fm['context']!r} must be one of {sorted(CONTEXT_VALUES)}")
    if "effort" in fm and fm["effort"] not in EFFORT_LEVELS:
        bad("effort", f"effort {fm['effort']!r} must be one of {sorted(EFFORT_LEVELS)}")
    if "agent" in fm and not isinstance(fm["agent"], str):
        bad("agent", "agent must be a string (the subagent type used with context: fork)")
    if "model" in fm and not isinstance(fm["model"], str):
        bad("model", "model must be a string (a /model value or 'inherit')")
    for listish in ("arguments", "disallowed-tools"):
        if listish in fm:
            value = fm[listish]
            ok = isinstance(value, str) or (
                isinstance(value, list) and all(isinstance(v, str) for v in value)
            )
            if not ok:
                bad(listish, f"{listish!r} must be a list of strings or a space/comma-separated string")
    # `background` is only meaningful alongside `context: fork` (ADR-0092 item 1).
    if fm.get("background") is not None and "context" not in fm:
        bad("background", "background is only valid together with context: fork")


def validate_skill_frontmatter(fm: dict, skill_path: Path, plugin_dir: Path) -> tuple[dict | None, list[dict]]:
    """Validate one skill's frontmatter. Return (catalog_entry_or_None, errors).

    catalog_entry is None if validation failed and the entry must be skipped.
    """
    errors: list[dict] = []
    rel = str(skill_path.relative_to(plugin_dir))
    skill_id = skill_path.parent.name

    # Required keys.
    for key in SCHEMA_KEYS:
        if key not in fm or fm[key] in (None, ""):
            errors.append({"file": rel, "field": key, "error": f"missing required key '{key}'"})

    # `name` must match directory id (skill_id).
    name = fm.get("name")
    if isinstance(name, str) and name != skill_id:
        errors.append(
            {
                "file": rel,
                "field": "name",
                "error": (f"name {name!r} does not match directory id {skill_id!r}"),
            }
        )

    # Type checks for list-valued fields.
    tags = fm.get("tags")
    if "tags" not in [e["field"] for e in errors if e["file"] == rel]:
        if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
            errors.append({"file": rel, "field": "tags", "error": "tags must be a list of strings"})

    bundles = fm.get("bundles")
    if "bundles" not in [e["field"] for e in errors if e["file"] == rel]:
        if not isinstance(bundles, list) or not all(isinstance(b, str) for b in bundles):
            errors.append(
                {
                    "file": rel,
                    "field": "bundles",
                    "error": "bundles must be a list of strings",
                }
            )

    # Enum checks.
    risk = fm.get("risk_level")
    if isinstance(risk, str) and risk not in RISK_LEVELS:
        errors.append(
            {
                "file": rel,
                "field": "risk_level",
                "error": f"invalid enum value {risk!r}; expected one of {sorted(RISK_LEVELS)}",
            }
        )

    # ─── invocation-control loader keys (ADR-0092) ─────────────────────────
    _validate_skill_loader_keys(fm, rel, errors)

    # ─── optional keys ─────────────────────────────────────────────────────
    # requires_env is optional: missing/null is valid. When present, validate
    # format. Empty list is valid.
    requires_env = fm.get("requires_env")
    if "requires_env" in fm and requires_env is not None:
        if not isinstance(requires_env, list):
            errors.append(
                {
                    "file": rel,
                    "field": "requires_env",
                    "error": "requires_env must be a list of strings",
                }
            )
        else:
            for n, value in enumerate(requires_env):
                if not isinstance(value, str) or not ENV_VAR_RE.match(value):
                    errors.append(
                        {
                            "file": rel,
                            "field": "requires_env",
                            "error": (f"{skill_id}: requires_env[{n}]={value!r} " f"must match ^[A-Z][A-Z0-9_]*$"),
                        }
                    )

    # Unknown-key check: any top-level frontmatter key that isn't in
    # ALL_KNOWN_KEYS is reported. (Unknown metadata.* keys are reported
    # separately by _lift_metadata's strict check and never reach here.)
    for key in fm.keys():
        if key not in ALL_KNOWN_KEYS and key not in TOPLEVEL_LOADER_KEYS:
            errors.append(
                {
                    "file": rel,
                    "field": key,
                    "error": f"unknown frontmatter key {key!r}",
                }
            )

    # If any errors prevent building a usable entry, return None.
    fatal_fields = {"name", "description", "tags", "bundles", "risk_level"}
    fatal = any(
        e["file"] == rel and e["field"] in fatal_fields and "missing required key" in e["error"] for e in errors
    )
    if fatal:
        return None, errors

    entry: dict[str, Any] = {
        "id": skill_id,
        "name": fm.get("name"),
        "description": fm.get("description"),
        "tags": fm.get("tags"),
        "bundles": fm.get("bundles"),
        "risk_level": fm.get("risk_level"),
    }

    # Only round-trip optional + invocation-control keys when they are actually
    # present (key exists AND value is not None). Skills that omit them must
    # omit them from JSON entirely — no null sentinels.
    if "requires_env" in fm and fm.get("requires_env") is not None:
        entry["requires_env"] = fm["requires_env"]
    if "routing_note" in fm and fm.get("routing_note") is not None:
        entry["routing_note"] = fm["routing_note"]
    if "triggers" in fm and fm.get("triggers") is not None:
        entry["triggers"] = fm["triggers"]
    for key in SKILL_INVOCATION_KEYS:
        if key in fm and fm.get(key) is not None:
            entry[key] = fm[key]

    return entry, errors


# ───────────────────────── skills.json regeneration ───────────────────────


def regenerate_skills_json(plugin_dir: Path, verbose: bool) -> tuple[list[dict], list[dict]]:
    """Walk skills/*/SKILL.md, validate, return (sorted_entries, errors)."""
    errors: list[dict] = []
    entries: list[dict] = []
    for skill_path in discover_skills(plugin_dir):
        fm, parse_err, shape_errs = extract_frontmatter(skill_path)
        rel = str(skill_path.relative_to(plugin_dir))
        if parse_err is not None:
            errors.append({"file": rel, "field": "<frontmatter>", "error": parse_err})
            if verbose:
                print(f"[{skill_path.parent.name}] parse error: {parse_err}", file=sys.stderr)
            continue
        # ADR-0005 shape errors (extended fields found at top level, or metadata
        # not a mapping). Reported as field-level validation errors.
        for msg in shape_errs:
            errors.append({"file": rel, "field": "<metadata>", "error": msg})
        entry, errs = validate_skill_frontmatter(fm, skill_path, plugin_dir)
        errors.extend(errs)
        if entry is not None:
            entries.append(entry)
            if verbose:
                print(f"[{skill_path.parent.name}] ok ({len(errs)} non-fatal warning(s))", file=sys.stderr)
        elif verbose:
            print(f"[{skill_path.parent.name}] fatal validation errors; skipped", file=sys.stderr)

    entries.sort(key=lambda e: e["id"])
    # Re-order keys within each entry to OUTPUT_KEY_ORDER.
    normalized = [{k: entry[k] for k in OUTPUT_KEY_ORDER if k in entry} for entry in entries]
    return normalized, errors


def serialize_skills_json(entries: list[dict]) -> str:
    """Deterministic 2-space JSON with trailing newline."""
    return json.dumps(entries, indent=2) + "\n"


# ───────────────────────── bundles.yml validation ─────────────────────────


def _load_bundles_mapping(bundles_path: Path) -> tuple[list[dict] | None, list[dict]]:
    """Read bundles.yml and reconstitute the historical list-of-objects shape.

    The file is a top-level mapping keyed by bundle id; each value carries the
    remaining five keys. The mapping key IS the id, which removes a duplication
    rather than porting one. Reconstituting the same `list[dict]` with the same
    six keys keeps every per-bundle check below — and the rename tool's
    mutators — working on the value they already expect, so the on-disk shape
    changed and the in-memory shape did not.

    Read through the STRICT shared loader, never this module's local
    `load_yaml`: that one ends `return data if isinstance(data, dict) else {}`,
    so a top-level sequence would parse to an empty mapping SILENTLY on both
    parse paths and yield a green run over zero bundles.
    """
    rel = f"catalog/{bundles_path.name}"
    try:
        text = bundles_path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, [{"file": rel, "field": "<file>", "error": f"cannot read: {exc}"}]
    try:
        raw = _yaml_min.load_catalog_yaml(text)
    except _yaml_min.CatalogYamlError as exc:
        return None, [{"file": rel, "field": "<yaml>", "error": f"YAML contract violation: {exc}"}]

    bundles: list[dict] = []
    errors: list[dict] = []
    for bundle_id, value in raw.items():
        if not isinstance(value, dict):
            errors.append(
                {"file": f"{rel}[{bundle_id}]", "field": "<bundle>", "error": "bundle entry is not a mapping"}
            )
            continue
        if "id" in value:
            errors.append(
                {
                    "file": f"{rel}[{bundle_id}]",
                    "field": "id",
                    "error": "the mapping key is the id; do not repeat it inside the value",
                }
            )
        bundles.append({"id": bundle_id, **value})
    return bundles, errors


def validate_bundles_yml(bundles_path: Path, skill_ids: set[str]) -> tuple[list[dict] | None, list[dict]]:
    """Validate bundles.yml against the set of regenerated skill ids.

    Returns (parsed_bundles_or_None, errors).

    A MISSING file is an error, not an empty result. The predecessor returned
    `([], [])` with the comment "bundles.json is optional"; under a rename that
    reading is a fail-open — a consumer left pointing at the old path would
    yield zero bundles and a green validator, which is the silent pass this
    whole conversion exists to remove.
    """
    rel = f"catalog/{bundles_path.name}"
    if not bundles_path.is_file():
        return None, [
            {
                "file": rel,
                "field": "<file>",
                "error": "required catalog file is missing (bundles.yml is not optional)",
            }
        ]

    bundles, errors = _load_bundles_mapping(bundles_path)
    if bundles is None:
        return None, errors

    seen_bundle_ids: set[str] = set()
    for bundle in bundles:
        bid = bundle["id"]
        loc = f"{rel}[{bid}]"
        missing = BUNDLE_REQUIRED - set(bundle.keys())
        for key in sorted(missing):
            errors.append({"file": loc, "field": key, "error": f"missing required key '{key}'"})

        if bid in seen_bundle_ids:
            errors.append({"file": loc, "field": "id", "error": f"duplicate bundle id {bid!r}"})
        seen_bundle_ids.add(bid)

        skills_list = bundle.get("skills")
        if isinstance(skills_list, list):
            seen_skill_ids: set[str] = set()
            for sid in skills_list:
                if not isinstance(sid, str):
                    errors.append(
                        {
                            "file": loc,
                            "field": "skills",
                            "error": f"skill entry must be a string, got {type(sid).__name__}",
                        }
                    )
                    continue
                if sid in seen_skill_ids:
                    errors.append(
                        {"file": loc, "field": "skills", "error": f"duplicate skill id {sid!r} within bundle"}
                    )
                seen_skill_ids.add(sid)
                if sid not in skill_ids:
                    errors.append(
                        {
                            "file": loc,
                            "field": "skills",
                            "error": f"skill id {sid!r} does not resolve to any skill in skills.json",
                        }
                    )
        elif "skills" not in missing:
            errors.append({"file": loc, "field": "skills", "error": "skills must be a list of skill ids"})

    return bundles, errors


# ───────────────────────── bundle-closure rule (ADR-0092 item 6) ──────────


_FENCE_RE = re.compile(r"^```.*?^```", re.MULTILINE | re.DOTALL)
_BACKTICK_SPAN_RE = re.compile(r"`([^`\n]+)`")
_CLOSURE_IGNORE_RE = re.compile(r"<!--\s*bundle-closure-ignore:\s*([a-z0-9-]+)\s*-->")


def _skill_body(skill_path: Path) -> str:
    """Return the SKILL.md body (everything after the frontmatter block)."""
    try:
        text = skill_path.read_text(encoding="utf-8")
    except OSError:
        return ""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return text
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            return "\n".join(lines[idx + 1 :])
    return text


def skill_dependency_edges(skill_path: Path, skill_ids: set[str]) -> set[str]:
    """The default-on-closure dependency edges out of one skill.

    An edge is a single-backtick span whose content is exactly a skill directory
    name under crux/skills/ (≠ self), scanned OUTSIDE fenced code blocks
    (ADR-0092 item 6). A per-reference `<!-- bundle-closure-ignore: <name> -->`
    comment suppresses a specific edge (the over-detection escape hatch).
    """
    body = _skill_body(skill_path)
    ignored = set(_CLOSURE_IGNORE_RE.findall(body))
    outside_fences = _FENCE_RE.sub("", body)
    self_id = skill_path.parent.name
    edges: set[str] = set()
    for span in _BACKTICK_SPAN_RE.findall(outside_fences):
        if span in skill_ids and span != self_id and span not in ignored:
            edges.add(span)
    return edges


def bundle_closure_findings(
    plugin_dir: Path, bundles_path: Path, skill_ids: set[str]
) -> list[dict]:
    """Every skill a default-on skill depends on must itself be default-on.

    Computes the transitive closure of dependency edges from the union of
    skills in every `default_provision: true` bundle; any skill in that closure
    not already declared default-on is a finding naming the skill and the
    default-on bundle to move it into. Warn-first (ADR-0092 item 6): returns
    findings carrying `severity: "warning"` so they stay out of the exit code.
    """
    bundles, _errs = _load_bundles_mapping(bundles_path)
    if bundles is None:
        return []
    default_on_bundles = [b for b in bundles if b.get("default_provision") is True]
    if not default_on_bundles:
        return []
    declared: set[str] = set()
    for b in default_on_bundles:
        for sid in b.get("skills") or []:
            if isinstance(sid, str):
                declared.add(sid)
    # A single named target bundle for the remediation message. When more than
    # one default-on bundle exists, name the first by id for determinism.
    target_bundle = sorted(b["id"] for b in default_on_bundles)[0]

    # Precompute each skill's edges once.
    edges: dict[str, set[str]] = {}
    for skill_path in discover_skills(plugin_dir):
        edges[skill_path.parent.name] = skill_dependency_edges(skill_path, skill_ids)

    # Transitive closure from the declared default-on set.
    closure = set(declared)
    frontier = list(declared)
    while frontier:
        current = frontier.pop()
        for dep in edges.get(current, set()):
            if dep not in closure:
                closure.add(dep)
                frontier.append(dep)

    findings: list[dict] = []
    for dep in sorted(closure - declared):
        # Name a default-on depender for the message.
        dependers = sorted(s for s in declared if dep in edges.get(s, set()))
        via = dependers[0] if dependers else "(transitively)"
        findings.append({
            "file": "catalog/bundles.yml",
            "field": "skills",
            "severity": "warning",
            "error": (
                f"bundle-closure: default-on skill {via!r} depends on {dep!r}, "
                f"which is not default-on; add {dep!r} to the {target_bundle!r} "
                f"bundle (and to its metadata.bundles)"
            ),
        })
    return findings


# ───────────────────────── models.yml validation (V0-V9) ──────────────────
#
# Rule ownership, one question each, no rule restating another's:
#   V0 shape | V1 roster | V2 reference graph | V3-V5 value legality
#   V6 the cross-file duplicate | V7 alias namespace | V8 row distinctness
#   V9 value placement
# V0, V1 and V2 also run inside models_catalog.load(), so a regenerator invoked
# directly rather than through CI fails before it writes. They are imported
# from there rather than restated, so the loader and its CI mirror cannot
# disagree about what the contract is.

# Anchored, with an explicit upper bound on every repetition, so each pattern
# is linear-time on any input and cannot be walked past by a long one. The
# bounds were originally set from shipped values rounded up to the next power
# of two, and are not a claim about any vendor's limit: 32 characters for a
# hand-chosen alias name or a provider token, and 96 for a model slug.
#
# Consolidating every entry onto OpenRouter (ADR-0087) shortened what ships, so
# the headroom grew rather than shrank: the only provider token is now
# `openrouter` (10), the longest slug is `anthropic/claude-haiku-4.5` (26), and
# the longest alias name is `sonnet-latest` (13). The bounds are left where they
# were — a bound is not retightened onto today's corpus, or the next entry
# trips it. Widen one deliberately if a vendor ships something longer; do not
# delete the bound.
#
# The end anchor is `\Z`, not `$`. In Python `$` also matches immediately
# BEFORE a trailing newline, so `"openrouter/x\n"` satisfied both V5 and V7 —
# an alias value carrying a trailing newline was accepted as well-formed and
# then written into a generated agent file's `model:` line, where the newline
# ends the line and everything after it becomes a new frontmatter key. `\Z`
# matches only at the true end of the string, so the value must be exactly
# what the pattern describes. `^` is left as is: it and `\A` agree here
# because none of these patterns is compiled with `re.MULTILINE`.
ALIAS_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}\Z")
ALIAS_PROVIDER_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}\Z")
ALIAS_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,95}\Z")

# Rule V4 warns past this age. Non-failing on purpose: a passing tree must not
# turn red on the passage of time alone.
VERIFIED_STALE_DAYS = 180

# Rule V9 inspects exactly these positions — the ones that SELECT a model.
# `codex.model` holds a Codex slug by design, `codex.source` holds a path, and
# comments are not inspected.
_CONFINED_POSITIONS = "agents.<name>, agents.<name>.level, agents.<name>.opencode, agents.<name>.claude, levels.<level>.claude, levels.<level>.opencode"


def _is_confined_roster_key(name: Any) -> bool:
    """True when a roster key is safe to use as a filename component.

    Deliberately a whole-string shape test rather than a resolve-and-compare:
    the key is checked before anything builds a path from it, so there is no
    window in which a path exists to be compared. An absolute key is the case
    that matters most — `Path("a") / "/etc/hosts"` is `/etc/hosts`, because
    pathlib discards everything left of an absolute component.
    """
    return (
        isinstance(name, str)
        and name != ""
        and "/" not in name
        and "\\" not in name
        and not name.startswith(".")
        and "\x00" not in name
    )


def _finding(field: str, error: str, *, warning: bool = False) -> dict:
    entry = {"file": "catalog/models.yml", "field": field, "error": error}
    if warning:
        entry["severity"] = "warning"
    return entry


def _agent_md_path(plugin_dir: Path, agent_name: str) -> Path | None:
    """`<plugin_dir>/agents/<agent_name>.md`, or None if that escapes agents/.

    The third layer of the [SECURITY:S3] repair, and the one placed at the
    point of use: V9 refuses a path-shaped roster key and the V1/V2 gate
    refuses the read, but both are distant from the `open()` they protect. A
    containment re-check here means a rule added later cannot reopen the hole
    by building the path itself.
    """
    agents_dir = (plugin_dir / "agents").resolve()
    candidate = (agents_dir / f"{agent_name}.md").resolve()
    if candidate.parent != agents_dir:
        return None
    return candidate


def _confinement_findings(agents: Any, levels: Any) -> list[dict]:
    """Rule V9 — value placement, plus the roster KEY.

    Split into its own function so it can run BEFORE the V1/V2 gate. V9 asks a
    pure shape question of each cell and follows no reference, so it needs
    nothing V1 or V2 establish — and running it after the gate would make it
    dead code for its own motivating case. A raw model id pasted into a
    reference position necessarily dangles, so V2 fires on the same input; V9
    exists to say *why* the reference dangles ("that is a model id, not an
    alias name") instead of leaving the author with "not a `levels` key".
    """
    findings: list[dict] = []
    if isinstance(agents, dict):
        for name in sorted(agents, key=repr):
            # [SECURITY:S3] The KEY, not just the value. A roster key becomes a
            # filesystem path in V6 (`plugin_dir / "agents" / f"{key}.md"`), so
            # a key carrying a separator, a traversal, or a leading dot is a
            # path escape rather than a naming-convention nit. An absolute key
            # escapes outright, because pathlib discards everything left of an
            # absolute component.
            if not _is_confined_roster_key(name):
                findings.append(
                    _finding(
                        "V9",
                        f"agents key {name!r} is not a bare agent name — a roster key is "
                        "resolved as a filename, so '/', '\\', '..' and a leading '.' "
                        "are refused",
                    )
                )
                continue
            row = agents[name]
            if isinstance(row, str):
                if "/" in row:
                    findings.append(_finding("V9", f"agents.{name}={row!r} contains '/'"))
                continue
            if not isinstance(row, dict):
                continue  # V0 owns the shape verdict.
            for key in ("level", "opencode", "claude"):
                value = row.get(key)
                if isinstance(value, str) and "/" in value:
                    findings.append(
                        _finding("V9", f"agents.{name}.{key}={value!r} contains '/'")
                    )
    if isinstance(levels, dict):
        for name, row in sorted(levels.items(), key=lambda kv: repr(kv[0])):
            if not isinstance(row, dict):
                continue  # V0 owns the shape verdict.
            for key in ("claude", "opencode"):
                value = row.get(key)
                if isinstance(value, str) and "/" in value:
                    findings.append(
                        _finding(
                            "V9",
                            f"levels.{name}.{key}={value!r} contains '/' — a raw model id in a "
                            f"model-selecting cell (inspected positions: {_CONFINED_POSITIONS})",
                        )
                    )
    return findings


def _router_registry(plugin_dir: Path) -> tuple[dict, dict] | None:
    """The router's `models` registry and `model_roles` map, or None if unreadable.

    Read as a legality ORACLE only. This is the single point of contact between
    the build-time catalog and the runtime router; nothing here makes the
    router read models.yml.
    """
    path = plugin_dir / "scripts" / "crux" / "_config" / "llm_router_config.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data.get("models") or {}, data.get("model_roles") or {}


def _codex_runtime_cells(levels: dict, agents: dict) -> list[tuple[str, dict]]:
    """Return every complete Codex runtime cell that V4 and V5 must validate.

    V0 has already established the cell shape before this traversal runs. Agent
    cells are complete overrides, so they receive the same effort, provenance,
    date, and registry checks as their level defaults.
    """
    cells = [(f"levels.{name}", row["codex"]) for name, row in sorted(levels.items())]
    cells.extend(
        (f"agents.{name}", row["codex"])
        for name, row in sorted(agents.items())
        if isinstance(row, dict) and "codex" in row
    )
    return cells


def validate_models_yml(models_path: Path, plugin_dir: Path) -> list[dict]:
    """Run rules V0-V9 over models.yml. Returns findings; warnings carry
    `severity: "warning"` and must not flip the exit code."""
    rel = "catalog/models.yml"
    if not models_path.is_file():
        return [{"file": rel, "field": "<file>", "error": "required catalog file is missing"}]
    try:
        raw = models_catalog.load_raw(models_path)
    except models_catalog.SpecViolation as exc:
        return [{"file": rel, "field": "<yaml>", "error": str(exc)}]

    findings: list[dict] = []
    shape = models_catalog.check_shape(raw)
    findings += [_finding("V0", msg) for msg in shape]
    if shape:
        # V1-V9 read the shapes V0 just proved. Running them over a
        # shape-invalid document produces cascades, not information.
        return findings

    # V9 runs HERE, out of numeric order, and the order is the point. It is a
    # pure placement test that follows no reference, and every input it exists
    # to catch ALSO trips V2 — a raw model id in a reference position dangles
    # by construction, and a path-shaped roster key names no agent file. Behind
    # the V1/V2 gate below, V9 would be dead code for exactly its own cases,
    # and the one report of a path-shaped key would sit behind the very gate
    # that exists because path-shaped keys are dangerous.
    findings += _confinement_findings(raw.get("agents"), raw.get("levels"))

    roster_findings = [
        _finding("V1", msg) for msg in models_catalog.check_roster(raw, plugin_dir / "agents")
    ]
    graph_findings = [_finding("V2", msg) for msg in models_catalog.check_reference_graph(raw)]
    findings += roster_findings + graph_findings

    # [SECURITY:S3] THE GATE. No rule below this line may build a filesystem
    # path out of a roster key while this is False. V1 is precisely the rule
    # that establishes a roster key names a real agent file, and running V6
    # regardless turned a mis-keyed catalog into a file-disclosure primitive:
    # an absolute key escapes `plugin_dir / "agents" / f"{key}.md"` outright,
    # because pathlib discards everything left of an absolute component, and
    # the named file's frontmatter was then echoed into a V6 finding and
    # printed as validator stdout JSON.
    #
    # Three independent layers close this, because each covers what the others
    # miss: V9 refuses the key's SHAPE (above, deliberately ahead of this
    # gate), this flag refuses the READ, and `_agent_md_path` re-checks
    # CONTAINMENT at the point of use so a future rule cannot reopen the hole
    # by calling it directly.
    #
    # Gating the read rather than returning early is deliberate. An early
    # return here would also silence V3-V5, V7 and V8, none of which touch the
    # filesystem — so one dangling alias would hide every other finding and an
    # author would fix the catalog one error per run. The security property is
    # a property of V6's read, so the guard sits on V6's read.
    roster_and_graph_clean = not (roster_findings or graph_findings)

    levels = raw["levels"]
    aliases = raw["aliases"]
    agents = raw["agents"]
    providers = set(raw["providers"])
    claude_aliases = set(raw["claude_aliases"])
    codex_cells = _codex_runtime_cells(levels, agents)

    # ── V3 Claude alias legality ───────────────────────────────────────────
    # The `claude_disabled` deny-list table was removed from the shipped
    # catalog, so this rule is membership-only again. Reintroducing the table
    # is a V0 defect (unknown top-level key), never a V3 value edit.
    # An agent's `claude` override is a Claude cell like a level's, and passes
    # the same membership test.
    for name, row in sorted(levels.items()):
        value = row["claude"]
        if value not in claude_aliases:
            findings.append(
                _finding("V3", f"levels.{name}.claude={value!r} is not a member of claude_aliases")
            )
    for name, row in sorted(agents.items()):
        if isinstance(row, dict) and "claude" in row and row["claude"] not in claude_aliases:
            findings.append(
                _finding(
                    "V3",
                    f"agents.{name}.claude={row['claude']!r} is not a member of claude_aliases",
                )
            )

    # ── V4 Codex value legality ────────────────────────────────────────────
    today = datetime.date.today()
    for path, codex in codex_cells:
        effort = codex["reasoning_effort"]
        if effort not in models_catalog.REASONING_EFFORTS:
            findings.append(
                _finding(
                    "V4",
                    f"{path}.codex.reasoning_effort={effort!r} is not one of "
                    f"{sorted(models_catalog.REASONING_EFFORTS)}",
                )
            )
        if not codex["source"].strip():
            findings.append(_finding("V4", f"{path}: codex.source is empty"))
        try:
            # fromisoformat, not a shape regex: a regex would admit 2026-99-99.
            verified = datetime.date.fromisoformat(codex["verified"])
        except ValueError:
            findings.append(
                _finding("V4", f"{path}.codex.verified={codex['verified']!r} is not a real ISO date")
            )
            continue
        if (verified - today).days > 1:
            # One day of tolerance absorbs clock skew and timezone offset.
            findings.append(
                _finding("V4", f"{path}.codex.verified={codex['verified']!r} is in the future")
            )
        elif (today - verified).days > VERIFIED_STALE_DAYS:
            findings.append(
                _finding(
                    "V4",
                    f"{path}.codex.verified={codex['verified']!r} is more than "
                    f"{VERIFIED_STALE_DAYS} days old; re-verify the slug against the Codex catalog",
                    warning=True,
                )
            )

    # ── V5 model-id legality, by column ────────────────────────────────────
    registry = _router_registry(plugin_dir)
    if registry is None:
        findings.append(
            _finding("V5", "cannot read llm_router_config.json; the Codex legality oracle is unavailable")
        )
    else:
        router_models, router_roles = registry
        for path, codex in codex_cells:
            model = codex["model"]
            if model not in router_models:
                findings.append(
                    _finding(
                        "V5",
                        f"{path}.codex.model={model!r} is not a key in the router's "
                        "`models` registry — a name that reads like a slug and resolves nowhere",
                    )
                )
            if model in router_roles:
                findings.append(
                    _finding("V5", f"{path}.codex.model={model!r} is a router ROLE, not a model")
                )

    for alias, value in sorted(aliases.items()):
        provider, sep, slug = value.partition("/")
        if not sep:
            findings.append(_finding("V5", f"aliases.{alias}={value!r} has no provider/slug separator"))
            continue
        if not ALIAS_PROVIDER_RE.match(provider):
            findings.append(_finding("V5", f"aliases.{alias}: provider {provider!r} is malformed"))
        elif provider not in providers:
            findings.append(
                _finding("V5", f"aliases.{alias}: provider {provider!r} is not in the providers table")
            )
        if not ALIAS_SLUG_RE.match(slug):
            findings.append(_finding("V5", f"aliases.{alias}: slug {slug!r} is malformed"))

    # ── V6 frontmatter/resolution agreement ────────────────────────────────
    # The only rule that reads a file named by the catalog. See THE GATE above.
    # The comparison is against the RESOLVED Claude Code value: the agent's own
    # `claude` override when the row carries one, and its level's cell otherwise.
    for agent_name in sorted(agents) if roster_and_graph_clean else ():
        row = agents[agent_name]
        level_name = row if isinstance(row, str) else row.get("level")
        level = levels.get(level_name)
        if not isinstance(level, dict):
            continue  # V2 owns the dangling-level verdict.
        agent_path = _agent_md_path(plugin_dir, agent_name)
        if agent_path is None:
            # REACHABLE, and the proof is `RosterKeyConfinementTests`'s
            # symlink case. This carried a `# pragma: no cover - V9 + the gate
            # make this unreachable`, and that was false: a SYMLINKED
            # `agents/<role>.md` pointing outside the directory passes V9 (the
            # key is a bare name) and passes V1 (the link resolves to a file,
            # so the roster bijection holds), and only the containment
            # re-check inside `_agent_md_path` catches it. Marking the third
            # layer uncovered told a future reader it could be deleted.
            findings.append(
                _finding(
                    "V6",
                    f"{agent_name!r}: agents/{agent_name}.md does not resolve INSIDE agents/ "
                    "— a symlinked or otherwise escaping leaf is refused, not followed",
                )
            )
            continue
        fm, err = extract_agent_frontmatter(agent_path)
        if err is not None:
            findings.append(_finding("V6", f"{agent_name}: cannot read agent frontmatter ({err})"))
            continue
        declared = fm.get("model")
        if isinstance(row, dict) and "claude" in row:
            resolved, basis = row["claude"], f"agents.{agent_name}.claude override"
        else:
            resolved, basis = level["claude"], f"level {level_name!r}"
        if declared != resolved:
            findings.append(
                _finding(
                    "V6",
                    f"agents/{agent_name}.md declares model={declared!r} but its resolved "
                    f"Claude Code value is {resolved!r} ({basis})",
                )
            )

    # ── V7 alias namespace ─────────────────────────────────────────────────
    for alias in sorted(aliases):
        if not ALIAS_NAME_RE.match(alias):
            findings.append(
                _finding("V7", f"alias name {alias!r} does not match {ALIAS_NAME_RE.pattern}")
            )

    # ── V8 level distinctness ──────────────────────────────────────────────
    # The EFFECTIVE SELECTION TUPLE, not the whole row: `verified` and `source`
    # are documentary, so two rows that resolve identically on all three
    # harnesses are one rung however their provenance lines differ.
    seen_tuples: dict[tuple, str] = {}
    for name, row in sorted(levels.items()):
        # `"opencode" in row` used to sit beside `row.get("opencode")` in this
        # tuple. It carried no information: V0 has already established that an
        # `opencode` key, if present, is a string, so absence and the `None`
        # from `.get` are the same state. A redundant element in a distinctness
        # key is worse than noise — it implies the two can disagree.
        key = (
            row["claude"],
            row.get("opencode"),
            row["codex"]["model"],
            row["codex"]["reasoning_effort"],
        )
        if key in seen_tuples:
            findings.append(
                _finding(
                    "V8",
                    f"levels.{name!r} and levels.{seen_tuples[key]!r} have the same effective "
                    "selection tuple — two rungs that have silently become one",
                )
            )
        else:
            seen_tuples[key] = name

    # V9 already ran, above the V1/V2 gate — see the comment there.

    return findings


# ───────────────────── the hand-authored catalog targets ──────────────────
#
# ONE machine-readable declaration of which catalog files this validator owns,
# with three readers: `main()` below, the audit rule that derives CHK-CAT-1's
# required-file floor, and the naming-convention test that asserts every
# `crux/catalog/*.yml` is positively enrolled for schema validation. One
# declaration, three readers, no scraping of this file's source.


def _bundles_target(path: Path, ctx: dict) -> list[dict]:
    return validate_bundles_yml(path, ctx["skill_ids"])[1]


def _models_target(path: Path, ctx: dict) -> list[dict]:
    return validate_models_yml(path, ctx["plugin_dir"])


CATALOG_TARGETS: dict[str, Callable[[Path, dict], list[dict]]] = {
    "bundles.yml": _bundles_target,
    "models.yml": _models_target,
}

# The GENERATED half of the catalog, and the other half of CHK-CAT-1: the
# `.json` files this validator writes, which must exist in a conforming tree.
#
# It ships here, in code, for one reason. The audit rule used to derive this
# list from the repo-root AGENTS.md's regenerative-output roster — a file that
# exists in THIS repo and in no repo the plugin is installed into. Downstream,
# the rule therefore derived an EMPTY list and the "skills.json exists"
# guarantee degraded silently to nothing. A constant beside CATALOG_TARGETS
# travels with the plugin, so the rule means the same thing everywhere.
REQUIRED_CATALOG_JSON: tuple[str, ...] = ("agents.json", "rules.json", "skills.json")


def _flow_catalog_target(path: Path, ctx: dict) -> list[dict]:
    from crux.flow import policy as flow_policy
    from crux.flow.common import FlowError, decode, mapping, text
    from crux.flow.managed import read_file

    plugin = ctx['plugin_dir']
    try:
        raw = read_file(plugin, 'catalog/' + path.name)
        if raw is None:
            raise FlowError('required authored Flow catalog is missing')
        if path.name == 'flow-policy.json':
            flow_policy.load_definition(plugin)
        elif path.name == 'flow-technology.json':
            from crux.flow import technology
            technology.load_catalog(plugin)
        else:
            value = mapping(decode(raw))
            catalog = models_catalog.load(plugin / 'catalog/models.yml', plugin / 'agents')
            if path.name == 'flow-roles.json':
                if set(value) != set(catalog.agents) - {'commander'}:
                    raise FlowError('Flow role purposes must match the non-commander role roster')
                for purpose in value.values():
                    text(purpose, maximum=4000)
            else:
                merged = {'schema_version':1, 'native':{}, 'api':{}}
                flow_policy._merge_bindings(merged, value)
                for groups in merged['native'].values():
                    if groups['levels'].keys() - catalog.levels.keys() or groups['roles'].keys() - catalog.agents.keys():
                        raise FlowError('unknown Flow model level or role')
        return []
    except (FlowError, OSError, ValueError, KeyError, TypeError):
        return [{'file':'catalog/' + path.name, 'field':'<schema>',
                 'error':'invalid or missing authored Flow catalog; no authored file was rewritten'}]


AUTHORED_CATALOG_JSON: dict[str, Callable[[Path, dict], list[dict]]] = {
    'flow-policy.json': _flow_catalog_target,
    'flow-bindings.json': _flow_catalog_target,
    'flow-roles.json': _flow_catalog_target,
    'flow-technology.json': _flow_catalog_target,
}


# ─────────────────────────────── diff helpers ─────────────────────────────


def diff_entries(old: list[dict], new: list[dict]) -> dict:
    """Compute added/changed/removed between two skills.json arrays."""
    old_map = {e["id"]: e for e in old if isinstance(e, dict) and "id" in e}
    new_map = {e["id"]: e for e in new}
    added = [new_map[i] for i in sorted(set(new_map) - set(old_map))]
    removed = [{"id": i} for i in sorted(set(old_map) - set(new_map))]
    changed = []
    for i in sorted(set(old_map) & set(new_map)):
        if old_map[i] != new_map[i]:
            changed.append({"id": i, "before": old_map[i], "after": new_map[i]})
    return {"added": added, "changed": changed, "removed": removed}


def read_existing_skills_json(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


# ────────────────────── agent discovery, parse & validation ───────────────


def discover_agents(plugin_dir: Path) -> list[Path]:
    """Return sorted list of plugin_dir/agents/*.md files (flat).

    Agents are flat files, one per agent (no per-agent directory). Returns an
    empty list if the agents/ directory is absent — agents are an optional
    catalog layer (ADR-0028).
    """
    agents_dir = plugin_dir / "agents"
    if not agents_dir.is_dir():
        return []
    found: list[Path] = []
    for entry in sorted(agents_dir.glob("*.md")):
        if entry.is_file():
            found.append(entry)
    return found


def extract_agent_frontmatter(agent_path: Path) -> tuple[dict, str | None]:
    """Read an agent .md and return (raw_frontmatter, error_or_None).

    Unlike extract_frontmatter() for skills, this does NOT lift fields out of
    `metadata:` (tools/model are legitimately top-level for agents, so the
    ADR-0005 drift-check is inapplicable). The raw parsed mapping is returned
    as-is; the validator inspects `metadata:` in place.

    **[SECURITY:S5] A symlinked agent leaf is refused.** The third reader of
    `agents/*.md` in the same class as `opencode_agents.parse_source` and
    `codex_agents.parse_source`, surfaced by the fail-closed sibling sweep:
    what this function parses is written into the GENERATED
    `catalog/agents.json`, so following a planted link publishes out-of-tree
    frontmatter into a committed artifact. Refused as an ERROR rather than
    skipped in `discover_agents`, because dropping the file there would
    silently NARROW agents.json — the same silent-narrowing failure the
    fail-closed loader exists to prevent. Covers the inside-`agents/` link
    too, which `_agent_md_path`'s containment check permits by design.
    """
    if agent_path.is_symlink():
        return {}, "agent file is a symlink — refused, not followed"
    try:
        text = agent_path.read_text(encoding="utf-8")
    except OSError as exc:
        return {}, f"could not read file: {exc}"
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, "missing opening `---` frontmatter delimiter"
    end_idx: int | None = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end_idx = idx
            break
    if end_idx is None:
        return {}, "missing closing `---` frontmatter delimiter"
    block = "\n".join(lines[1:end_idx])
    skills_error = _agent_skills_source_error(block)
    if skills_error is not None:
        return {}, skills_error
    # Strict YAML-validity gate (PB-0039). Agent frontmatter MUST be valid YAML:
    # both Claude Code AND OpenCode load it as YAML, and OpenCode's strict loader
    # (unlike Claude Code's lenient one) rejects e.g. an unquoted `: ` (colon-space)
    # in a description — which silently shipped an unloadable night-gardener agent
    # until this gate. When PyYAML is available (dev/CI) we enforce validity here;
    # to put a literal `: ` in a description, quote the whole value.
    try:
        import yaml  # type: ignore
        try:
            yaml.safe_load(block)
        except yaml.YAMLError as exc:
            return {}, f"invalid YAML frontmatter: {str(exc).splitlines()[0]}"
    except ImportError:
        pass  # shipped runtime without PyYAML: skip the strict check (dev/CI catches it)
    # Extraction uses the minimal line-based parser so the no-PyYAML runtime still
    # works; it partitions on the first colon and handles the simple agent shape
    # (scalars + one nested `metadata:` mapping). For VALID YAML it agrees with a
    # strict load; the gate above rejects anything that wouldn't.
    try:
        fm_raw = _parse_minimal_yaml(block)
    except Exception as exc:  # noqa: BLE001
        return {}, f"YAML parse error: {exc}"
    if not isinstance(fm_raw, dict):
        return {}, "frontmatter did not parse to a mapping"
    return fm_raw, None


def _agent_skills_source_error(block: str) -> str | None:
    """Reject ambiguous or lossy authored `skills:` flow-list layouts.

    PyYAML accepts duplicate mapping keys by keeping the last one, while the
    Codex projection reads a single source line. Inspect the raw top-level
    layout before either parser can erase that distinction. Empty elements are
    likewise rejected before the PyYAML and no-PyYAML paths diverge.
    """
    declarations = re.findall(r"(?m)^skills:[ \t]*(.*)$", block)
    if len(declarations) > 1:
        return "duplicate top-level skills key"
    if not declarations:
        return None
    value = _strip_yaml_comment(declarations[0]).strip()
    if not (value.startswith("[") and value.endswith("]")):
        return None
    inner = value[1:-1].strip()
    if inner and any(not token.strip() for token in inner.split(",")):
        return "skills flow list contains an empty element"


def _csv_to_list(value: Any) -> list[str]:
    """Split a CSV string on `,` and strip each token (dropping empties).

    Accepts an already-parsed list verbatim (a future hand-authored agent
    could use a true YAML list). None/empty → [].
    """
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(t) for t in value]
    return [t.strip() for t in str(value).split(",") if t.strip()]


def _validate_agent_invocation_keys(
    fm: dict,
    rel: str,
    skill_ids: set[str],
    errors: list[dict],
    plugin_dir: Path | None = None,
) -> None:
    """Type-check the agent invocation-control top-level keys.

    An `effort` key is accepted but emits a WARNING. Codex pins reasoning
    effort through models.yml resolution, so the field affects only Claude.
    The validator reports this divergence.
    """

    def bad(field: str, msg: str) -> None:
        errors.append({"file": rel, "field": field, "error": msg})

    if "maxTurns" in fm:
        value = fm["maxTurns"]
        if not (isinstance(value, int) and not isinstance(value, bool) and value > 0):
            bad("maxTurns", "maxTurns must be a positive integer")
    if "effort" in fm:
        if fm["effort"] not in EFFORT_LEVELS:
            bad("effort", f"effort {fm['effort']!r} must be one of {sorted(EFFORT_LEVELS)}")
        else:
            errors.append({
                "file": rel, "field": "effort", "severity": "warning",
                "error": "effort affects only Claude. Codex resolves reasoning effort "
                         "through models.yml. OpenCode has no reasoning-effort field. "
                         "Both generated projections omit effort.",
            })
    if "memory" in fm and fm["memory"] not in MEMORY_SCOPES:
        bad("memory", f"memory {fm['memory']!r} must be one of {sorted(MEMORY_SCOPES)}")
    if "isolation" in fm and fm["isolation"] not in ISOLATION_VALUES:
        bad("isolation", f"isolation {fm['isolation']!r} must be one of {sorted(ISOLATION_VALUES)}")
    if "skills" in fm:
        value = fm["skills"]
        if not (isinstance(value, list) and all(isinstance(s, str) for s in value)):
            bad("skills", "skills must be a list of skill names")
        else:
            seen_skills: set[str] = set()
            for s in value:
                if s in seen_skills:
                    bad("skills", f"duplicate skill name {s!r}")
                    continue
                seen_skills.add(s)
                if not AGENT_SKILL_NAME_RE.fullmatch(s):
                    bad("skills", f"invalid skill name {s!r}")
                    continue
                if s not in skill_ids:
                    bad("skills", f"declared skill {s!r} is not a shipped skill")
                    continue
                if plugin_dir is not None:
                    resource = plugin_dir / "skills" / s / "SKILL.md"
                    if not resource.is_file():
                        bad(
                            "skills",
                            f"declared skill {s!r} has no shipped SKILL.md resource",
                        )
    if "disallowedTools" in fm:
        value = fm["disallowedTools"]
        ok = isinstance(value, str) or (
            isinstance(value, list) and all(isinstance(v, str) for v in value)
        )
        if not ok:
            bad("disallowedTools", "disallowedTools must be a list of strings or a comma-separated string")


# ADR-0092 (F-b): the OpenCode and Codex projections read these list-valued
# agent keys with a SAME-LINE regex (opencode_agents.transform / codex_agents).
# A block-style YAML list — items on the lines below the key — parses to the
# same Python list a flow list does, so the validator would accept it, yet the
# projection reads nothing on the key line and leaves the `- item` lines
# orphaned in the generated agent file (a denied tool silently re-granted, a
# preloaded skill silently dropped). The drift gate cannot catch it because the
# generator and its committed output agree. The authored contract is inline: a
# flow list `[a, b]` or a comma/space string. Reject the block-style form here,
# at the one gate that can still see the source layout.
_PROJECTION_INLINE_LIST_KEYS = ("disallowedTools", "skills")


def _agent_frontmatter_block(agent_path: Path) -> str | None:
    """Return the raw text between the `---` fences, or None if unreadable.

    The block-style check needs the source LAYOUT, which the parsed mapping has
    already normalized away — a flow list and a block list are the same Python
    list post-parse.
    """
    try:
        text = agent_path.read_text(encoding="utf-8")
    except OSError:
        return None
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            return "\n".join(lines[1:idx])
    return None


def _reject_block_style_projection_keys(
    fm: dict, agent_path: Path, rel: str, errors: list[dict]
) -> None:
    candidates = [
        k for k in _PROJECTION_INLINE_LIST_KEYS
        if isinstance(fm.get(k), list) and fm.get(k)
    ]
    if not candidates:
        return
    block = _agent_frontmatter_block(agent_path)
    if block is None:
        return
    for key in candidates:
        # Block-style leaves the top-level key line empty after the colon (only
        # optional whitespace or a comment); an inline flow/CSV value does not.
        if re.search(rf"(?m)^{re.escape(key)}:[ \t]*(#.*)?$", block):
            errors.append({
                "file": rel,
                "field": key,
                "error": (
                    f"{key!r} is authored as a block-style YAML list; the OpenCode and "
                    "Codex projections read this key inline, so a block-style list "
                    "mis-projects. Use an inline flow list (e.g. [a, b]) or a "
                    "comma/space-separated string."
                ),
            })


def validate_agent_frontmatter(
    fm: dict, agent_path: Path, plugin_dir: Path, allowed_models: set[str], skill_ids: set[str]
) -> tuple[dict | None, list[dict]]:
    """Validate one agent's frontmatter. Return (catalog_entry_or_None, errors).

    catalog_entry is None if a fatal error prevents building a usable entry.
    Errors may carry a `severity: "warning"` marker (see the effort key); the
    caller partitions those out of the exit-code decision.
    """
    errors: list[dict] = []
    rel = str(agent_path.relative_to(plugin_dir))
    agent_id = agent_path.stem

    # ── top-level: name (must equal filename stem) ──────────────────────────
    name = fm.get("name")
    if name is None or name == "":
        errors.append({"file": rel, "field": "name", "error": "missing required key 'name'"})
    elif not isinstance(name, str):
        errors.append({"file": rel, "field": "name", "error": "name must be a string"})
    elif name != agent_id:
        errors.append(
            {
                "file": rel,
                "field": "name",
                "error": f"name {name!r} does not match filename stem {agent_id!r}",
            }
        )

    # ── top-level: description (non-empty string) ───────────────────────────
    description = fm.get("description")
    if description is None or description == "":
        errors.append({"file": rel, "field": "description", "error": "missing required key 'description'"})
    elif not isinstance(description, str):
        errors.append({"file": rel, "field": "description", "error": "description must be a string"})

    # ── top-level: tools (present, string) ──────────────────────────────────
    tools = fm.get("tools")
    if tools is None or tools == "":
        errors.append({"file": rel, "field": "tools", "error": "missing required key 'tools'"})
    elif not isinstance(tools, str):
        errors.append({"file": rel, "field": "tools", "error": "tools must be a string (comma-separated)"})

    # ── top-level: model (present, in enum) ─────────────────────────────────
    model = fm.get("model")
    if model is None or model == "":
        errors.append({"file": rel, "field": "model", "error": "missing required key 'model'"})
    elif not isinstance(model, str) or model not in allowed_models:
        errors.append(
            {
                "file": rel,
                "field": "model",
                "error": f"invalid model {model!r}; expected one of {sorted(allowed_models)}",
            }
        )

    # ── metadata: must be a mapping carrying the required fields ────────────
    metadata = fm.get("metadata")
    if metadata is None:
        errors.append({"file": rel, "field": "metadata", "error": "missing required `metadata:` mapping"})
        metadata = {}
    elif not isinstance(metadata, dict):
        errors.append({"file": rel, "field": "metadata", "error": "`metadata:` must be a mapping"})
        metadata = {}
    else:
        for key in AGENT_METADATA_KEYS:
            if key not in metadata or metadata[key] in (None, ""):
                errors.append(
                    {
                        "file": rel,
                        "field": f"metadata.{key}",
                        "error": f"missing required metadata key '{key}'",
                    }
                )
        # Strict metadata contract (ADR-0092): a lingering owner/version/status
        # — or any other unknown metadata key — is a validation error.
        for key in metadata.keys():
            if key not in KNOWN_AGENT_METADATA_KEYS:
                errors.append(
                    {
                        "file": rel,
                        "field": f"metadata.{key}",
                        "error": f"unknown metadata key {key!r} (strict metadata contract per ADR-0092)",
                    }
                )

    # Enum checks on metadata fields (only when present as strings).
    risk = metadata.get("risk_level")
    if isinstance(risk, str) and risk != "" and risk not in RISK_LEVELS:
        errors.append(
            {
                "file": rel,
                "field": "metadata.risk_level",
                "error": f"invalid enum value {risk!r}; expected one of {sorted(RISK_LEVELS)}",
            }
        )

    # ── invocation-control top-level keys (ADR-0092 / ADR-0111) ────────────
    if "skills" not in fm:
        errors.append({"file": rel, "field": "skills", "error": "missing required key 'skills'"})
    _validate_agent_invocation_keys(fm, rel, skill_ids, errors, plugin_dir)
    _reject_block_style_projection_keys(fm, agent_path, rel, errors)

    # ── unknown top-level keys ──────────────────────────────────────────────
    for key in fm.keys():
        if key not in AGENT_TOPLEVEL_ALLOWED:
            errors.append(
                {
                    "file": rel,
                    "field": key,
                    "error": f"unknown top-level frontmatter key {key!r}",
                }
            )

    # Fatal: anything missing that prevents building a usable, complete entry.
    fatal = any(e["file"] == rel and "missing required" in e["error"] for e in errors)
    if fatal:
        return None, errors

    entry: dict[str, Any] = {
        "id": agent_id,
        "name": fm.get("name"),
        "description": fm.get("description"),
        "tools": _csv_to_list(fm.get("tools")),
        "model": fm.get("model"),
        "tags": _csv_to_list(metadata.get("tags")),
        "bundles": _csv_to_list(metadata.get("bundles")),
        "risk_level": metadata.get("risk_level"),
    }
    # Invocation-control keys, present-only (no null sentinels).
    for key in AGENT_INVOCATION_KEYS:
        if key in fm and fm.get(key) is not None:
            entry[key] = fm[key]
    return entry, errors


def regenerate_agents_json(
    plugin_dir: Path, verbose: bool, skill_ids: set[str] | None = None
) -> tuple[list[dict], list[dict]]:
    """Walk agents/*.md, validate, return (sorted_entries, errors).

    `skill_ids` is the set of on-disk skill directory names, used to validate an
    agent's `skills:` preload list (ADR-0092); defaults to empty.
    """
    if skill_ids is None:
        skill_ids = set()
    errors: list[dict] = []
    entries: list[dict] = []
    allowed_models, enum_error = agent_model_enum(plugin_dir)
    if enum_error is not None:
        errors.append({"file": "catalog/models.yml", "field": "claude_aliases", "error": enum_error})
        allowed_models = set()
    for agent_path in discover_agents(plugin_dir):
        fm, parse_err = extract_agent_frontmatter(agent_path)
        rel = str(agent_path.relative_to(plugin_dir))
        if parse_err is not None:
            errors.append({"file": rel, "field": "<frontmatter>", "error": parse_err})
            if verbose:
                print(f"[agent {agent_path.stem}] parse error: {parse_err}", file=sys.stderr)
            continue
        entry, errs = validate_agent_frontmatter(fm, agent_path, plugin_dir, allowed_models, skill_ids)
        errors.extend(errs)
        if entry is not None:
            entries.append(entry)
            if verbose:
                print(f"[agent {agent_path.stem}] ok ({len(errs)} non-fatal warning(s))", file=sys.stderr)
        elif verbose:
            print(f"[agent {agent_path.stem}] fatal validation errors; skipped", file=sys.stderr)

    entries.sort(key=lambda e: e["id"])
    normalized = [{k: entry[k] for k in AGENT_OUTPUT_KEY_ORDER if k in entry} for entry in entries]
    return normalized, errors


def serialize_agents_json(entries: list[dict]) -> str:
    """Deterministic 2-space JSON with trailing newline (byte-stable)."""
    return json.dumps(entries, indent=2) + "\n"


def read_existing_agents_json(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


# ──────────────────────────────── main ────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    # SURFACE-ABSENT LANE. The catalog is the PLUGIN's derived output, projected from
    # the plugin's own skills and agents. Run from a consuming project it resolved the
    # catalog beside itself and validated the installed plugin against the installed
    # plugin -- exit 0, clean, and nothing said about the project the reader asked
    # about. That vacuous green was recorded as a passing roster row.
    root = _scope.resolve_repo_root(getattr(args, "repo_root", None))
    if not _scope.is_authoring_checkout(root, __file__):
        print(json.dumps(_scope.surface_absent_payload(
            reason="the skill and agent catalogs are the plugin's own derived outputs; "
                   "this is not the plugin's authoring checkout"), sort_keys=True))
        return 0

    plugin_dir = root / "crux"
    config_path = args.config if args.config is not None else (plugin_dir / "plugin.json")
    if args.verbose:
        print(f"validate-catalog: plugin_dir={plugin_dir}", file=sys.stderr)
        print(f"validate-catalog: config={config_path}", file=sys.stderr)

    catalog_dir = plugin_dir / "catalog"
    skills_json_path = catalog_dir / "skills.json"
    agents_json_path = catalog_dir / "agents.json"

    # ── skills.json ─────────────────────────────────────────────────────────
    new_entries, validation_errors = regenerate_skills_json(plugin_dir, args.verbose)

    # ── the hand-authored catalog files ─────────────────────────────────────
    # Iterate CATALOG_TARGETS rather than naming files here: that constant is
    # the one declaration of which files this validator owns, and an audit rule
    # and a naming-convention test both read it.
    skill_ids = {e["id"] for e in new_entries}
    target_ctx = {"skill_ids": skill_ids, "plugin_dir": plugin_dir}
    warnings: list[dict] = []
    # Counted separately from the skill findings they are appended beside. They
    # share a list because the payload shape and the exit code are consumed
    # downstream, but a `models.yml` rule violation is not a "skill" error and
    # the summary line used to call it one.
    catalog_error_count = 0
    targets = dict(CATALOG_TARGETS)
    if (plugin_dir / 'scripts/crux/flow').is_dir() or any((catalog_dir / name).exists() for name in AUTHORED_CATALOG_JSON):
        targets.update(AUTHORED_CATALOG_JSON)
    for filename, check in targets.items():
        for finding in check(catalog_dir / filename, target_ctx):
            if finding.pop("severity", None) == "warning":
                warnings.append(finding)
            else:
                validation_errors.append(finding)
                catalog_error_count += 1

    new_text = serialize_skills_json(new_entries)
    old_text = skills_json_path.read_text(encoding="utf-8") if skills_json_path.is_file() else ""
    old_entries = read_existing_skills_json(skills_json_path)
    diff = diff_entries(old_entries, new_entries)
    drift = (new_text != old_text) or bool(diff["added"] or diff["changed"] or diff["removed"])

    # ── bundle-closure rule (ADR-0092 item 6) ───────────────────────────────
    # Warn-first for one release: findings are WARNINGS, kept out of the
    # exit-code decision, until the rule promotes to error.
    warnings += bundle_closure_findings(plugin_dir, catalog_dir / "bundles.yml", skill_ids)

    # ── agents.json ───────────────────────────────────────────────────────── (ADR-0028)
    new_agent_entries, agent_validation_errors_raw = regenerate_agents_json(
        plugin_dir, args.verbose, skill_ids
    )
    # Partition the agent effort WARNING (ADR-0092 item 3) out of the errors.
    agent_validation_errors: list[dict] = []
    for finding in agent_validation_errors_raw:
        if finding.pop("severity", None) == "warning":
            warnings.append(finding)
        else:
            agent_validation_errors.append(finding)

    new_agent_text = serialize_agents_json(new_agent_entries)
    old_agent_text = agents_json_path.read_text(encoding="utf-8") if agents_json_path.is_file() else ""
    old_agent_entries = read_existing_agents_json(agents_json_path)
    agent_diff = diff_entries(old_agent_entries, new_agent_entries)
    agent_drift = (new_agent_text != old_agent_text) or bool(
        agent_diff["added"] or agent_diff["changed"] or agent_diff["removed"]
    )

    all_validation_errors = validation_errors + agent_validation_errors

    if args.dry_run:
        payload = {
            "added": diff["added"],
            "changed": diff["changed"],
            "removed": diff["removed"],
            "validation_errors": validation_errors,
            "agents": {
                "added": agent_diff["added"],
                "changed": agent_diff["changed"],
                "removed": agent_diff["removed"],
                "validation_errors": agent_validation_errors,
            },
            "warnings": warnings,
        }
        print(json.dumps(payload, indent=2))
        # Warnings are deliberately outside the exit-code decision: rule V4's
        # staleness clause is the one non-failing rule, kept non-failing so a
        # passing tree does not turn red on a date change alone.
        clean = (not drift) and (not agent_drift) and (not all_validation_errors)
        return 0 if clean else 1

    # Non-dry-run: refuse to write EITHER catalog if any validation errors exist.
    if all_validation_errors:
        payload = {
            "added": diff["added"],
            "changed": diff["changed"],
            "removed": diff["removed"],
            "validation_errors": validation_errors,
            "agents": {
                "added": agent_diff["added"],
                "changed": agent_diff["changed"],
                "removed": agent_diff["removed"],
                "validation_errors": agent_validation_errors,
            },
            "warnings": warnings,
        }
        print(json.dumps(payload, indent=2))
        print(
            f"validate-catalog: {len(all_validation_errors)} validation error(s) "
            f"({len(validation_errors) - catalog_error_count} skill, {catalog_error_count} "
            f"hand-authored catalog, {len(agent_validation_errors)} agent); "
            f"catalog NOT written.",
            file=sys.stderr,
        )
        return 1

    for warning in warnings:
        print(f"validate-catalog: WARNING {warning['file']} {warning['field']}: {warning['error']}",
              file=sys.stderr)

    catalog_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(skills_json_path, new_text)
    _atomic_write_text(agents_json_path, new_agent_text)

    print(
        f"validate-catalog: wrote {len(new_entries)} skill entry/entries to "
        f"{skills_json_path.relative_to(plugin_dir)} "
        f"({len(diff['added'])} added, {len(diff['changed'])} changed, "
        f"{len(diff['removed'])} removed)."
    )
    print(
        f"validate-catalog: wrote {len(new_agent_entries)} agent entry/entries to "
        f"{agents_json_path.relative_to(plugin_dir)} "
        f"({len(agent_diff['added'])} added, {len(agent_diff['changed'])} changed, "
        f"{len(agent_diff['removed'])} removed)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
