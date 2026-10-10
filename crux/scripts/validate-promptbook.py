#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pyyaml>=6.0",
# ]
# ///
"""Vendored validator (stdlib-only schema engine) for crux promptbook + run-snapshot YAML
documents (per ADR-0022 §4 and ADR-0023 §6).

ONE validator surface validates BOTH documents (ADR-0023 §6): the run validator
is the same schema-walking engine pointed at a second schema. Dispatch is
``--kind {promptbook|run}`` (always overrides) else auto-detect on parsed keys.

Why a vendored validator instead of ``jsonschema``: crux scripts are
stdlib-only (cf. ``validate-catalog.py``). The schemas advertise the canonical
draft-2020-12 ``$schema`` URI for interop (a downstream tool or a future
``jsonschema`` path validates the SAME file), and the engine implements a
documented keyword SUBSET. To keep the draft-2020-12 claim honest, the engine
REJECTS-ON-LOAD any schema using a keyword it does not implement (ADR-0022 §4):
a ``$schema`` declaration can therefore never out-promise the engine.

Implemented keyword subset (ADR-0022 §4):
  type (incl. union like ["string","null"]), required, enum, const, properties,
  additionalProperties (bool), items (single-schema), minItems, minLength,
  minimum, pattern, contains, allOf, if/then/else.

Beyond schema validation the script runs a small POST-SCHEMA structural pass for
the cross-item invariant a single-document schema can't express: ``n == index+1``
contiguous from 1 (ADR-0023 §6).

Exit codes (mirror validate-catalog.py / extract-code-docs.py, plus the
PB-0026 capability lane):
  0 — clean (valid).
  1 — validation error. ``{"errors": [{file, instance_path, schema_path, error}]}``
  2 — PyYAML unavailable and the uv repair unavailable/declined (capability
      error; remediation on stderr, nothing on stdout — a real YAML parser is
      required at entry per PB-0026, because the minimal fallback diverges
      silently outside its subset).
  non-zero with empty/unparseable stdout — crash; surface stderr.

This script writes nothing; ``--dry-run`` is accepted as a no-op alias for
parity with the sibling validators.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

# Import the shared minimal YAML loader + canonical-JSON serializer. The module
# filename starts with an underscore and has no hyphen, so a plain import works
# when scripts/ is on the path; fall back to a path-based load for when this
# hyphen-named CLI is itself loaded via spec_from_file_location (e.g. by
# tests), where scripts/ isn't on sys.path.
try:
    from _yaml_min import YamlCapabilityError, canonical_json, ensure_real_yaml, load_yaml
except ImportError:  # pragma: no cover - exercised only outside scripts/ cwd
    _spec = importlib.util.spec_from_file_location(
        "_yaml_min", Path(__file__).resolve().parent / "_yaml_min.py"
    )
    assert _spec is not None and _spec.loader is not None
    _yaml_min = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_yaml_min)
    canonical_json = _yaml_min.canonical_json
    load_yaml = _yaml_min.load_yaml
    ensure_real_yaml = _yaml_min.ensure_real_yaml
    YamlCapabilityError = _yaml_min.YamlCapabilityError


try:
    import record_numbers
except ImportError:  # pragma: no cover - by-path load, scripts/ not on sys.path
    _rn_spec = importlib.util.spec_from_file_location(
        "record_numbers", Path(__file__).resolve().parent / "record_numbers.py"
    )
    assert _rn_spec is not None and _rn_spec.loader is not None
    record_numbers = importlib.util.module_from_spec(_rn_spec)
    _rn_spec.loader.exec_module(record_numbers)


SCHEMAS_DIR = Path(__file__).resolve().parent.parent / "schemas"
PROMPTBOOK_SCHEMA = SCHEMAS_DIR / "promptbook.schema.json"
RUN_SCHEMA = SCHEMAS_DIR / "run.schema.json"

# The keywords this engine implements. ANY other keyword in a loaded schema is
# rejected-on-load (ADR-0022 §4). ``$schema``/``title``/``$comment`` are
# annotations, not assertions, so they're allowed but ignored.
IMPLEMENTED_KEYWORDS = {
    "type",
    "required",
    "enum",
    "const",
    "properties",
    "additionalProperties",
    "items",
    "minItems",
    "minLength",
    "minimum",
    "pattern",
    "contains",
    "allOf",
    "if",
    "then",
    "else",
}
ANNOTATION_KEYWORDS = {"$schema", "title", "$comment", "description"}

# Map JSON-Schema type names to Python types. ``bool`` is excluded from
# ``integer``/``number`` (JSON booleans are not numbers).
_JSON_TYPE_CHECKS = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
}


class SchemaLoadError(Exception):
    """Raised on reject-on-load: a schema uses an unimplemented keyword."""


# ─────────────────────────── reject-on-load guard ─────────────────────────


def assert_subset(schema: Any, schema_path: str = "#") -> None:
    """Walk a schema and RAISE SchemaLoadError if any keyword outside the
    implemented subset appears (ADR-0022 §4). This is what makes advertising the
    real draft-2020-12 ``$schema`` URI safe — the engine can never silently
    under-validate by ignoring a keyword it doesn't understand."""
    if isinstance(schema, bool):
        return  # boolean schema (true/false) — allowed.
    if not isinstance(schema, dict):
        raise SchemaLoadError(f"{schema_path}: schema node must be an object or boolean")
    for kw in schema:
        if kw in ANNOTATION_KEYWORDS:
            continue
        if kw not in IMPLEMENTED_KEYWORDS:
            raise SchemaLoadError(
                f"{schema_path}: unimplemented schema keyword {kw!r} "
                f"(implemented subset: {sorted(IMPLEMENTED_KEYWORDS)})"
            )
    # Recurse into sub-schemas.
    if "properties" in schema:
        props = schema["properties"]
        if not isinstance(props, dict):
            raise SchemaLoadError(f"{schema_path}/properties: must be an object")
        for name, sub in props.items():
            assert_subset(sub, f"{schema_path}/properties/{name}")
    if "items" in schema:
        assert_subset(schema["items"], f"{schema_path}/items")
    if "contains" in schema:
        assert_subset(schema["contains"], f"{schema_path}/contains")
    for kw in ("if", "then", "else"):
        if kw in schema:
            assert_subset(schema[kw], f"{schema_path}/{kw}")
    if "allOf" in schema:
        seq = schema["allOf"]
        if not isinstance(seq, list):
            raise SchemaLoadError(f"{schema_path}/allOf: must be an array")
        for idx, sub in enumerate(seq):
            assert_subset(sub, f"{schema_path}/allOf/{idx}")
    if "additionalProperties" in schema:
        ap = schema["additionalProperties"]
        if not isinstance(ap, bool):
            # The subset restricts additionalProperties to a bool (ADR-0022 §4).
            raise SchemaLoadError(
                f"{schema_path}/additionalProperties: only boolean form is implemented"
            )


def load_schema(path: Path) -> dict:
    """Load a JSON Schema from disk and assert it uses only the implemented
    subset (reject-on-load)."""
    schema = json.loads(path.read_text(encoding="utf-8"))
    assert_subset(schema, "#")
    return schema


# ─────────────────────────── validation engine ────────────────────────────


def validate(instance: Any, schema: Any, instance_path: str, schema_path: str,
             errors: list[dict], file_label: str) -> None:
    """Validate ``instance`` against ``schema``, appending error dicts. Errors
    carry JSON-pointer-ish ``instance_path`` / ``schema_path`` so the visual
    tool can locate them precisely (ADR-0022 §4)."""
    if isinstance(schema, bool):
        if schema is False:
            _err(errors, file_label, instance_path, schema_path, "schema is false; no value is valid")
        return

    # type
    if "type" in schema:
        types = schema["type"]
        type_list = types if isinstance(types, list) else [types]
        if not any(_JSON_TYPE_CHECKS.get(t, lambda v: False)(instance) for t in type_list):
            _err(errors, file_label, instance_path, f"{schema_path}/type",
                 f"expected type {types!r}, got {_typename(instance)}")
            # A type mismatch makes most sub-assertions meaningless; stop here.
            return

    # const
    if "const" in schema:
        if instance != schema["const"] or _typename(instance) != _typename(schema["const"]):
            _err(errors, file_label, instance_path, f"{schema_path}/const",
                 f"expected const {schema['const']!r}, got {instance!r}")

    # enum
    if "enum" in schema:
        if not any(instance == e and _typename(instance) == _typename(e) for e in schema["enum"]):
            _err(errors, file_label, instance_path, f"{schema_path}/enum",
                 f"value {instance!r} not in enum {schema['enum']!r}")

    # string assertions
    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            _err(errors, file_label, instance_path, f"{schema_path}/minLength",
                 f"string shorter than minLength {schema['minLength']}")
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            _err(errors, file_label, instance_path, f"{schema_path}/pattern",
                 f"string {instance!r} does not match pattern {schema['pattern']!r}")

    # numeric assertions (exclude bool)
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            _err(errors, file_label, instance_path, f"{schema_path}/minimum",
                 f"value {instance} is less than minimum {schema['minimum']}")

    # object assertions
    if isinstance(instance, dict):
        if "required" in schema:
            for key in schema["required"]:
                if key not in instance:
                    _err(errors, file_label, instance_path, f"{schema_path}/required",
                         f"missing required property {key!r}")
        props = schema.get("properties", {})
        if "properties" in schema:
            for name, subschema in props.items():
                if name in instance:
                    validate(instance[name], subschema,
                             f"{instance_path}/{name}", f"{schema_path}/properties/{name}",
                             errors, file_label)
        if schema.get("additionalProperties") is False:
            for name in instance:
                if name not in props:
                    _err(errors, file_label, f"{instance_path}/{name}",
                         f"{schema_path}/additionalProperties",
                         f"additional property {name!r} is not allowed")

    # array assertions
    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            _err(errors, file_label, instance_path, f"{schema_path}/minItems",
                 f"array has {len(instance)} items, fewer than minItems {schema['minItems']}")
        if "items" in schema:
            for idx, item in enumerate(instance):
                validate(item, schema["items"],
                         f"{instance_path}/{idx}", f"{schema_path}/items",
                         errors, file_label)
        if "contains" in schema:
            sub = schema["contains"]
            if not any(_matches(item, sub) for item in instance):
                _err(errors, file_label, instance_path, f"{schema_path}/contains",
                     "no array item matches the 'contains' schema")

    # applicators
    if "allOf" in schema:
        for idx, sub in enumerate(schema["allOf"]):
            validate(instance, sub, instance_path, f"{schema_path}/allOf/{idx}", errors, file_label)

    if "if" in schema:
        if _matches(instance, schema["if"]):
            if "then" in schema:
                validate(instance, schema["then"], instance_path, f"{schema_path}/then", errors, file_label)
        else:
            if "else" in schema:
                validate(instance, schema["else"], instance_path, f"{schema_path}/else", errors, file_label)


def _matches(instance: Any, schema: Any) -> bool:
    """True iff ``instance`` validates against ``schema`` with no errors. Used
    for ``if`` / ``contains`` where we need a boolean, not collected errors."""
    sink: list[dict] = []
    validate(instance, schema, "#", "#", sink, "<probe>")
    return not sink


def _typename(v: Any) -> str:
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, float):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "array"
    if isinstance(v, dict):
        return "object"
    if v is None:
        return "null"
    return type(v).__name__


def _err(errors: list[dict], file_label: str, instance_path: str,
         schema_path: str, message: str) -> None:
    errors.append({
        "file": file_label,
        "instance_path": instance_path,
        "schema_path": schema_path,
        "error": message,
    })


# ─────────────────────────── kind dispatch ────────────────────────────────


def detect_kind(doc: Any) -> str | None:
    """Auto-detect promptbook vs run from parsed keys (ADR-0023 §6).

    - both ``book_id`` and ``run_id`` present  -> 'run'  (NOTE: run-only signal)
    - ``id`` (PB-NNNN) + ``prompts``, no ``run_id`` -> 'promptbook'
    - neither signature, or both -> None (ambiguous/malformed)
    """
    if not isinstance(doc, dict):
        return None
    has_run = "run_id" in doc and "book_id" in doc
    has_book = "id" in doc and "prompts" in doc and "run_id" not in doc
    if has_run and not has_book:
        return "run"
    if has_book and not has_run:
        return "promptbook"
    return None


# ──────────────── post-schema structural pass + binding hash ───────────────


def post_schema_pass(doc: dict, errors: list[dict], file_label: str) -> None:
    """Cross-item invariant a single-document schema can't express (ADR-0023 §6):
    ``prompts[].n`` is contiguous from 1, i.e. ``n == index + 1``."""
    prompts = doc.get("prompts")
    if not isinstance(prompts, list):
        return
    for idx, item in enumerate(prompts):
        if not isinstance(item, dict):
            continue
        n = item.get("n")
        if n != idx + 1:
            _err(errors, file_label, f"#/prompts/{idx}/n", "#/prompts (post-schema structural pass)",
                 f"prompt n={n!r} is not contiguous-from-1 (expected {idx + 1})")


# Within-module prompt counts — the "ordinal contract" (ADR-0029): a module of the
# correct size guarantees its required slot exists BY POSITION (adr/verify module
# ordinal 2 = council prompt; dev module ordinal 4 = internal review; review module
# ordinal 3 = fix-loop). We enforce size + per-instance contiguity; the slot
# semantics are positional, never inferred from prose titles.
_MODULE_PROMPT_COUNT = {"adr": 4, "verify": 4, "dev": 4, "review": 3}

# The `patch` tier (ADR-0077 clause 1): five phases in order, one prompt each, so
# its formula is the constant 5 and its floor is 5. This is a THIRD tier — the
# `4N+4M+3K+2` formula and the thirteen-prompt floor that `adr` and `verify` are
# held to are unchanged.
_PATCH_PHASES = ("verify", "plan", "implement", "review", "summary")
_PATCH_PROMPT_COUNT = len(_PATCH_PHASES)


def invalid_blast_radius_entry(entry: Any) -> str | None:
    """Return why ``entry`` is not a usable repo-relative declaration path, or None.

    Pure string work — no filesystem access — so it gives the same verdict on every
    platform and is safe on untrusted book YAML. ``check-blast-radius.py`` imports
    this, so the grammar the authoring-time validator enforces and the grammar the
    archive-time check enforces are one grammar (ADR-0077 clause 2)."""
    if not isinstance(entry, str):
        return "not a string"
    if not entry.strip():
        return "empty"
    if entry != entry.strip():
        return "has leading or trailing whitespace"
    if "\x00" in entry:
        return "contains a NUL byte"
    if "\\" in entry:
        return "contains a backslash; declare paths with forward slashes"
    if entry.startswith("/"):
        return "is absolute"
    if entry.startswith("~"):
        return "is home-relative"
    if re.match(r"^[A-Za-z]:", entry):
        return "carries a drive-letter prefix"
    segments = entry.split("/")
    if any(seg == ".." for seg in segments):
        return "contains a '..' segment"
    if any(seg == "" for seg in segments[:-1]):
        return "contains an empty path segment"
    # A declaration that normalizes to zero segments — ".", "./", "." repeated —
    # denotes the repository root and would cover EVERY path, so the containment
    # check could never fail. That is the "declaration drawn wider than the work
    # needs" failure the tier is most vulnerable to, in its most absolute form.
    if not [seg for seg in segments if seg not in ("", ".")]:
        return "resolves to the repository root, which would cover every path"
    return None


def _patch_coverage_pass(doc: dict, prompts: list, e) -> None:
    """The `patch` branch of the cycle-coverage pass (ADR-0077 clause 1). It shares
    no machinery with the `adr`/`verify` branch on purpose: patch has phases, not
    modules, and a constant count, not a formula over module instances."""
    total = doc.get("total_prompts")
    count = len(prompts)
    if count != _PATCH_PROMPT_COUNT:
        e(f"patch cycle has {count} prompts, expected {_PATCH_PROMPT_COUNT} "
          f"(five phases, one prompt each)")
    if isinstance(total, int):
        if total != count:
            e(f"total_prompts ({total}) != len(prompts) ({count})", "#/total_prompts")
        if total != _PATCH_PROMPT_COUNT:
            e(f"patch total_prompts ({total}) != {_PATCH_PROMPT_COUNT} "
              f"(the patch formula is the constant {_PATCH_PROMPT_COUNT})", "#/total_prompts")

    phases: list[Any] = []
    for idx, p in enumerate(prompts):
        if not isinstance(p, dict):
            continue
        if p.get("module_tag") is not None:
            e(f"patch prompt {idx + 1} carries module_tag {p['module_tag']!r}; "
              f"a patch book has phases, not modules", f"#/prompts/{idx}/module_tag")
        phase = p.get("phase")
        if phase is None:
            e(f"patch prompt {idx + 1} is missing 'phase'", f"#/prompts/{idx}/phase")
        phases.append(phase)

    # Only assert the sequence once every slot is filled — otherwise a single
    # missing `phase` would report twice, as an omission and as a wrong order.
    if len(phases) == _PATCH_PROMPT_COUNT and all(ph is not None for ph in phases):
        if tuple(phases) != _PATCH_PHASES:
            e(f"patch phase sequence {phases!r} is not the required "
              f"{list(_PATCH_PHASES)!r}, in order", "#/prompts")

    radius = doc.get("blast_radius")
    if not isinstance(radius, list) or not radius:
        e("a patch book must declare a non-empty blast_radius", "#/blast_radius")
        return
    for idx, entry in enumerate(radius):
        reason = invalid_blast_radius_entry(entry)
        if reason is not None:
            e(f"blast_radius[{idx}] {entry!r} is not a repo-relative path: {reason}",
              f"#/blast_radius/{idx}")


# The explicit declaration that no architectural constraint governs a slot's scope. The pattern
# repeats the reason bound the two schemas state: one line of 1 to 500 characters with a
# non-whitespace character.
_DECLARATION_FIELD = "no_governing_constraint"
_DECLARATION_REASON_PATTERN = r"^(?=[^\x00-\x1f\x7f]*\S)[^\x00-\x1f\x7f]{1,500}(?![\s\S])"


class FormatTwoError(ValueError):
    """A bounded structural refusal safe for execution-boundary diagnostics."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def validate_format_two(book: dict) -> None:
    """Canonical format-two cycle structure, independent of run-start history."""
    def require(condition, code):
        if not condition:
            raise FormatTwoError(code)

    require(isinstance(book, dict), "book-shape-refused")
    require(book.get("format_version") == "2", "explicit-format-required")
    require("cycle_grandfathered" not in book and "grandfather_reason" not in book,
            "grandfathering-refused")
    kind = book.get("cycle_kind")
    require(kind in ("adr", "implementation", "verify", "patch"), "cycle-kind-refused")
    prompts = book.get("prompts")
    require(isinstance(prompts, list) and prompts, "prompts-refused")
    require(all(isinstance(p, dict) and type(p.get("n")) is int and p.get("n") == i + 1
                for i, p in enumerate(prompts)),
            "prompt-order-refused")
    slots = book.get("implementation_slots")
    require(isinstance(slots, list), "slots-refused")
    for slot in slots:
        base = {"slot", "slug", "scope", "constraint_refs"}
        require(isinstance(slot, dict) and base <= set(slot) and
                set(slot) - base <= {"migration_batch", _DECLARATION_FIELD},
                "slot-shape-refused")
        declared = _DECLARATION_FIELD in slot
        if declared:
            require("migration_batch" not in slot, "migration-slot-declaration-refused")
            marker = slot[_DECLARATION_FIELD]
            require(isinstance(marker, dict) and set(marker) == {"reason"} and
                    isinstance(marker["reason"], str) and
                    re.match(_DECLARATION_REASON_PATTERN, marker["reason"]) is not None and
                    slot["constraint_refs"] == [], "slot-declaration-refused")
        require(isinstance(slot["slug"], str) and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slot["slug"]),
                "slot-slug-refused")
        require(isinstance(slot["scope"], list) and slot["scope"] and
                isinstance(slot["constraint_refs"], list) and (declared or slot["constraint_refs"]),
                "slot-scope-refused")
        require(isinstance(slot["slot"], str) and re.fullmatch(r"(?:implementation|verify)-[1-9][0-9]*|patch", slot["slot"]),
                "slot-kind-refused")
        require(all(isinstance(path, str) and path and not path.startswith("/") and
                    all(part not in ("", ".", "..") for part in path.split("/")) and
                    not any(ord(char) < 32 or ord(char) == 127 or char == "\\" for char in path)
                    for path in slot["scope"]),
                "slot-scope-refused")
        require(all(isinstance(handle, str) and re.fullmatch(
                    r"(?:(?!(?:PB|ADR|RUN|BRIEF)-)[A-Z][A-Z0-9]{1,9}-)?ADR-[0-9]{4}/[a-z0-9]+(?:-[a-z0-9]+)*", handle)
                    for handle in slot["constraint_refs"]), "slot-constraint-refused")
        if "migration_batch" in slot:
            batch = slot["migration_batch"]
            require(isinstance(batch, dict) and set(batch) == {"role", "path"} and
                    batch["role"] == "migration-batch", "migration-slot-shape-refused")
            path = batch["path"]
            require(slot["slot"].startswith("implementation-") and isinstance(path, str) and
                    not path.startswith("/") and all(part not in ("", ".", "..") for part in path.split("/")) and
                    not any(ord(char) < 32 or ord(char) == 127 or char == "\\" for char in path) and
                    any(path == entry or path.startswith(entry + "/") for entry in slot["scope"]),
                    "migration-slot-path-refused")
    require(len({s["slot"] for s in slots}) == len(slots), "duplicate-slot")
    if kind == "patch":
        require("modules" not in book, "patch-modules-refused")
        require(len(prompts) == 5 and [p.get("phase") for p in prompts] == list(_PATCH_PHASES),
                "patch-order-refused")
        require(all(s["slot"] == "patch" for s in slots), "slot-kind-refused")
        require(all("module_tag" not in p for p in prompts), "patch-module-refused")
        require(isinstance(book.get("blast_radius"), list) and book["blast_radius"] and
                all(invalid_blast_radius_entry(entry) is None for entry in book["blast_radius"]),
                "patch-blast-radius-refused")
    else:
        require("blast_radius" not in book and all("phase" not in p for p in prompts),
                "module-phase-refused")
        groups = []
        untagged = []
        for p in prompts:
            tag = p.get("module_tag")
            if not tag:
                untagged.append(p["n"])
                continue
            require(isinstance(tag, str) and re.fullmatch(r"(?:adr|implementation|verify|dev|review)-[1-9][0-9]*", tag),
                    "module-prefix-refused")
            if not groups or groups[-1][0] != tag:
                require(tag not in [g[0] for g in groups], "module-order-refused")
                groups.append((tag, []))
            groups[-1][1].append(p["n"])
        counts = {k: 0 for k in ("adr", "implementation", "verify", "dev", "review")}
        stages = []
        for tag, numbers in groups:
            prefix = tag.rsplit("-", 1)[0]
            require(len(numbers) == (3 if prefix == "review" else 4), "module-count-refused")
            require(numbers == list(range(numbers[0], numbers[0] + len(numbers))), "module-order-refused")
            counts[prefix] += 1
            require(int(tag.rsplit("-", 1)[1]) == counts[prefix], "module-instance-refused")
            stages.append(2 if prefix == "review" else 1 if prefix == "dev" else 0)
        require(stages == sorted(stages), "module-order-refused")
        a, i, v, d, r = (counts[k] for k in counts)
        require(d >= 1 and r >= 1 and len(prompts) >= 13 and len(untagged) == 2,
                "cycle-count-refused")
        require(untagged == [len(prompts) - 1, len(prompts)], "final-prompts-refused")
        require((kind == "adr" and a >= 1 and v == 0) or
                (kind == "implementation" and i >= 1 and a == v == 0) or
                (kind == "verify" and v >= 1 and a == i == 0), "cycle-combination-refused")
        require(len(prompts) == 4 * (a + i + v + d) + 3 * r + 2, "cycle-count-refused")
        require(all(s["slot"] in [g[0] for g in groups] and s["slot"].split("-")[0] in
                    ("implementation", "verify") for s in slots), "slot-kind-refused")
    if kind != "patch":
        declared = book.get("modules")
        keys = ("adrs", "implementations", "verify", "dev_loops", "review_cycles")
        require(isinstance(declared, dict) and set(declared) == set(keys), "module-declaration-refused")
        require(all(type(declared[key]) is int and declared[key] >= 0 for key in keys),
                "module-declaration-refused")
        require([declared[key] for key in keys] == [a, i, v, d, r], "module-declaration-refused")
        required_slots = {tag for tag, _ in groups if tag.startswith("implementation-")}
        require(required_slots <= {s["slot"] for s in slots}, "dedicated-slot-missing")
    require(type(book.get("total_prompts")) is int, "total-count-refused")
    require(book.get("total_prompts") == len(prompts), "total-count-refused")



def validate_format_two_run(run: dict, book: dict) -> None:
    """A run covers the entire frozen book, including its independent review."""
    validate_format_two(book)
    if not isinstance(run, dict) or run.get("format_version") != "2":
        raise FormatTwoError("format-binding-refused")
    if run.get("book_id") != book["id"] or run.get("book_content_hash") != compute_book_hash(book):
        raise FormatTwoError("book-binding-refused")
    entries = run.get("prompts")
    if not isinstance(entries, list) or not all(isinstance(p, dict) for p in entries) or \
            [(p.get("n"), p.get("title")) for p in entries] != [(p["n"], p["title"]) for p in book["prompts"]]:
        raise FormatTwoError("run-book-shape-refused")
    batch_slots = {item["slot"]: item for item in book["implementation_slots"] if "migration_batch" in item}
    if batch_slots:
        if not isinstance(run.get("migration_bindings"), list):
            raise FormatTwoError("migration-bindings-required")
    elif "migration_bindings" in run:
        raise FormatTwoError("migration-bindings-not-declared")
    slots = {item["slot"]: item for item in book["implementation_slots"]}
    seen = set()
    for container, subject_key in (("implementation_bindings", "revision"), ("migration_bindings", "batch")):
        bindings = run.get(container, [])
        if not isinstance(bindings, list):
            raise FormatTwoError("binding-container-refused")
        for binding in bindings:
            required = {"slot", "book_id", "run_id", "book_content_hash", subject_key, "gate_prompt",
                        "deciding_record", "retained_subject", "context"}
            if not isinstance(binding, dict) or set(binding) != required or \
                    not isinstance(binding.get("slot"), str) or binding["slot"] not in slots:
                raise FormatTwoError("binding-declaration-refused")
            slot = slots[binding["slot"]]
            if ("migration_batch" in slot) != (subject_key == "batch"):
                raise FormatTwoError("cross-role-binding-refused")
            if any(binding[key] != run.get(key) for key in ("book_id", "run_id", "book_content_hash")):
                raise FormatTwoError("binding-identity-refused")
            members = [p["n"] for p in book["prompts"] if
                       (p.get("phase") == "verify" if slot["slot"] == "patch" else p.get("module_tag") == slot["slot"])]
            if type(binding["gate_prompt"]) is not int or binding["gate_prompt"] != members[-1]:
                raise FormatTwoError("binding-close-refused")
            identity = (container, slot["slot"], binding["gate_prompt"])
            if identity in seen:
                raise FormatTwoError("binding-close-identity-ambiguous")
            seen.add(identity)
            subject = binding[subject_key]
            if not isinstance(subject, dict) or set(subject) != {"path", "sha256"} or \
                    not isinstance(subject["path"], str) or not isinstance(subject["sha256"], str) or \
                    not re.fullmatch(r"[0-9a-f]{64}", subject["sha256"]):
                raise FormatTwoError("binding-subject-refused")
            if subject_key == "batch" and subject["path"] != slot["migration_batch"]["path"]:
                raise FormatTwoError("migration-binding-subject-refused")

def cycle_coverage_pass(doc: dict, errors: list[dict], file_label: str) -> None:
    """Cycle-coverage invariants for cycle-kind promptbooks — dev-cycle (`adr`),
    iterate (`verify`), and patch-cycle (`patch`). Machine-enforces what dev-cycle
    previously only documented as authoring prose.

    For `adr`/`verify` (ADR-0029, unchanged): the ``4N+4M+3K+2`` formula, ``modules``
    <-> ``module_tag``-count agreement, per-module prompt counts (the ordinal contract)
    + per-instance contiguity, adr/verify mutual exclusion, the >=13 floor, exactly-2
    untagged (prep + summary), and ``cycle_kind`` <-> ``modules`` <-> tag cross-agreement.

    For a ``format_version`` "2" book the pass delegates to ``validate_format_two``:
    kinds `adr`, `implementation`, `verify` and `patch`; the architectural count is
    ``4(A+I)+4D+3R+2`` and the implementation count ``4I+4D+3R+2``; and
    ``cycle_grandfathered`` / ``grandfather_reason`` are refused. The ``4N+4M+3K+2``
    formula above is the format-one rule.

    For `patch` (ADR-0077 clause 1): the constant count of 5, the fixed phase sequence,
    no module tags, and a non-empty repo-relative ``blast_radius``. A separate branch,
    not a relaxation of the other two.

    A book with ``cycle_grandfathered: true`` is skipped (it stays schema-validated).
    Pure-data: no eval, no module_tag-driven file access, bounded by len(prompts) — safe
    on untrusted promptbook YAML."""
    if doc.get("format_version") == "2":
        try:
            validate_format_two(doc)
        except (FormatTwoError, TypeError, KeyError, AttributeError) as exc:
            _err(errors, file_label, "#", "#/cycle-coverage", str(exc) if isinstance(exc, FormatTwoError)
                 else "format-two-shape-refused")
        return
    if doc.get("cycle_grandfathered") is True:
        return
    tags = doc.get("tags") or []
    kind = doc.get("cycle_kind")
    prompts = doc.get("prompts")

    def e(msg: str, ipath: str = "#") -> None:
        _err(errors, file_label, ipath, "#/cycle-coverage (ADR-0029, ADR-0077)", msg)

    # `blast_radius` and per-prompt `phase` belong to the patch tier alone. Checked
    # for EVERY book, cycle or not, so a stray declaration on an adr/verify/plain
    # book is caught rather than silently ignored.
    if kind != "patch":
        if "blast_radius" in doc:
            e("blast_radius is declared only by a cycle_kind: 'patch' book", "#/blast_radius")
        if isinstance(prompts, list):
            for idx, p in enumerate(prompts):
                if isinstance(p, dict) and p.get("phase") is not None:
                    e("prompts[].phase is carried only by a cycle_kind: 'patch' book",
                      f"#/prompts/{idx}/phase")

    is_cycle = (isinstance(tags, list) and "cycle" in tags) or kind in ("adr", "verify", "patch")
    if not is_cycle:
        return

    if kind not in ("adr", "verify", "patch"):
        e("cycle book must declare cycle_kind: 'adr', 'verify' or 'patch'", "#/cycle_kind")
        return

    if not isinstance(prompts, list):
        return

    if kind == "patch":
        _patch_coverage_pass(doc, prompts, e)
        return

    # Count prompts per module-tag instance, record first-seen order, contiguity runs,
    # and the untagged (prep/summary) count.
    inst_counts: dict[str, int] = {}
    inst_order: list[str] = []
    run_tags: list[Any] = []
    last: Any = object()
    untagged = 0
    for p in prompts:
        t = p.get("module_tag") if isinstance(p, dict) else None
        if t is None:
            untagged += 1
        else:
            inst_counts[t] = inst_counts.get(t, 0) + 1
            if t not in inst_order:
                inst_order.append(t)
        if t != last:
            run_tags.append(t)
            last = t

    fam: dict[str, list[str]] = {"adr": [], "verify": [], "dev": [], "review": []}
    for t in inst_order:
        prefix = t.split("-", 1)[0]
        if prefix in fam:
            fam[prefix].append(t)

    # adr/verify mutual exclusion + cross-agreement with cycle_kind.
    if fam["adr"] and fam["verify"]:
        e("a cycle book must not mix adr- and verify- module tags (it is one kind or the other)")
    primary, other = ("adr", "verify") if kind == "adr" else ("verify", "adr")
    if fam[other]:
        e(f"cycle_kind is '{kind}' but {other}- module tags are present")
    if not fam[primary]:
        e(f"cycle_kind is '{kind}' but no {primary}- module tags present (degenerate cycle book)")

    # Per-module prompt counts (ordinal contract) + per-instance contiguity.
    for prefix, want in _MODULE_PROMPT_COUNT.items():
        for t in fam[prefix]:
            if inst_counts[t] != want:
                e(f"module {t!r} has {inst_counts[t]} prompts, expected {want} (ordinal contract)")
    run_counts: dict[str, int] = {}
    for t in run_tags:
        if t is not None:
            run_counts[t] = run_counts.get(t, 0) + 1
    for t, c in run_counts.items():
        if c > 1:
            e(f"module {t!r} prompts are not contiguous (tag spans {c} separate runs)")

    # modules block <-> counted module instances.
    modules = doc.get("modules") if isinstance(doc.get("modules"), dict) else {}
    primary_key = "adrs" if kind == "adr" else "verify"
    for key, got in ((primary_key, len(fam[primary])),
                     ("dev_loops", len(fam["dev"])),
                     ("review_cycles", len(fam["review"]))):
        if modules.get(key) != got:
            e(f"modules.{key} = {modules.get(key)!r} but counted {got} module instance(s)", "#/modules")

    # Formula, floor, exactly-2 untagged.
    formula = 4 * len(fam[primary]) + 4 * len(fam["dev"]) + 3 * len(fam["review"]) + 2
    total = doc.get("total_prompts")
    if isinstance(total, int):
        if total != len(prompts):
            e(f"total_prompts ({total}) != len(prompts) ({len(prompts)})", "#/total_prompts")
        if total != formula:
            e(f"total_prompts ({total}) != 4*{len(fam[primary])} + 4*{len(fam['dev'])} + "
              f"3*{len(fam['review'])} + 2 = {formula}", "#/total_prompts")
        if total < 13:
            e(f"cycle total_prompts ({total}) is below the floor of 13", "#/total_prompts")
    if untagged != 2:
        e(f"expected exactly 2 untagged prompts (prep + summary), found {untagged}")


# Plan-subset keys frozen into the content hash (ADR-0023 §3). Optional keys
# (modules, blast_radius, side_effects, module_tag, phase) are OMITTED when absent
# — never emitted as present-with-null — so the two parse paths can't diverge, and
# so adding an optional key never moves the hash of a book that lacks it.
#
# `blast_radius` is in the frozen subset deliberately (ADR-0077 clause 2): that is
# what mechanically fixes the declaration once the run starts. Widening it mid-run
# moves `book_content_hash`, and audit rule CHK-PB-BIND fires.
_PLAN_TOP_KEYS = ["format_version", "id", "title", "tags", "total_prompts", "goal", "strategy"]
_PLAN_PROMPT_KEYS = ["n", "title", "purpose", "prompt", "expected_output"]


def frozen_plan_subset(book: dict) -> dict:
    """Extract exactly the plan-bearing fields the abandon rule freezes (ADR-0023 §3),
    excluding the mutable run-state fields ``current_run`` / ``current_prompt`` /
    ``status``. Optional keys are omitted when absent (not present-with-null)."""
    subset: dict[str, Any] = {}
    for k in _PLAN_TOP_KEYS:
        if k in book:
            subset[k] = book[k]
    if "modules" in book and book["modules"] is not None:
        subset["modules"] = book["modules"]
    if "blast_radius" in book and book["blast_radius"] is not None:
        subset["blast_radius"] = book["blast_radius"]
    if book.get("format_version") == "2":
        for key in ("cycle_kind", "implementation_slots"):
            if key in book:
                subset[key] = book[key]
    prompts_out: list[dict] = []
    for p in book.get("prompts", []) or []:
        if not isinstance(p, dict):
            continue
        po: dict[str, Any] = {}
        for k in _PLAN_PROMPT_KEYS:
            if k in p:
                po[k] = p[k]
        # side_effects: include only when present (a real list; [] is meaningful).
        if "side_effects" in p and p["side_effects"] is not None:
            po["side_effects"] = p["side_effects"]
        if "module_tag" in p and p["module_tag"] is not None:
            po["module_tag"] = p["module_tag"]
        if "phase" in p and p["phase"] is not None:
            po["phase"] = p["phase"]
        prompts_out.append(po)
    subset["prompts"] = prompts_out
    return subset


def compute_book_hash(book_dict: dict) -> str:
    """``"sha256:" + sha256(canonical_json(frozen_plan_subset)).hexdigest()``
    (ADR-0023 §3). Stable across run-state mutations; changes iff the plan
    changes. ``run-promptbook`` calls this at run-start to populate
    ``book_content_hash``."""
    digest = hashlib.sha256(canonical_json(frozen_plan_subset(book_dict))).hexdigest()
    return "sha256:" + digest


# ──────────────────────────────── main ────────────────────────────────────


def _tree_of(path: Path) -> tuple[Path, str, str] | None:
    """The tree a book or run snapshot sits in, its namespace, and the name that
    carries its number, else None.

    A book sits at `<docs>/promptbooks/{active,archive}/<book>` and a run snapshot at
    `<docs>/promptbooks/runs/<dir>/<run>`, whose directory carries the number. Anything
    else, `legacy/` included, is outside a tree and owes no number check.
    """
    p = path.resolve()
    if p.parent.name in ("active", "archive") and p.parent.parent.name == "promptbooks":
        return p.parent.parent.parent, "books", p.name
    if p.parent.parent.name == "runs" and p.parent.parent.parent.name == "promptbooks":
        return p.parent.parent.parent.parent, "run_directories", p.parent.name
    return None


def duplicate_number_errors(path: Path, file_label: str) -> list[dict]:
    """A duplicate of the validated record's own number, as validator errors.

    Only the book or run directory `path` belongs to is checked. Another record's
    duplicate, an ADR's included, is not this file's fault; audit-docs and the
    release gate report every duplicate in the tree.
    """
    where = _tree_of(path)
    if where is None:
        return []
    docs, namespace, name = where
    held = record_numbers.number_of(name)
    if held is None:
        return []
    key = (namespace, *held)
    return [{"file": file_label, "instance_path": "#", "schema_path": "#", "error": msg}
            for msg in record_numbers.find_duplicate_numbers(docs, only=key)]


def declared_empty_overlap_errors(path: Path, doc: dict, file_label: str) -> list[dict]:
    """Authoring-time check: a declared-empty slot whose scope an undeclared live Accepted rule's
    path scope overlaps, as validator errors.

    The slot validator, `validate_format_two`, is structural and reads no repository. This reads the
    committed Accepted rules of the repository the book sits in. A repository that cannot be read
    (no git root, an uncommitted rule source) yields no finding here: council preflight, the
    approval close and current eligibility each repeat the check against the committed tree."""
    slots = doc.get("implementation_slots") if doc.get("format_version") == "2" else None
    declared = [s for s in slots if isinstance(s, dict) and _DECLARATION_FIELD in s] \
        if isinstance(slots, list) else []
    if not declared:
        return []
    try:
        import subprocess
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=path.resolve().parent,
                             capture_output=True, text=True, check=True).stdout.strip()
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import implementation_approval as approval
        repo = Path(top)
        view = approval.live_constraints(repo, [])
        out = []
        for index, slot in enumerate(slots):
            if not isinstance(slot, dict) or _DECLARATION_FIELD not in slot:
                continue
            found = approval.undeclared_governing_constraints(repo, slot["scope"], [], view)
            if found["overlapping"]:
                out.append({"file": file_label, "instance_path": f"#/implementation_slots/{index}",
                            "schema_path": "#", "error": "declared-empty slot "
                            f"{slot.get('slot')!r} overlaps the path scope of undeclared live rule(s) "
                            + ", ".join(found["overlapping"])})
        return out
    except (subprocess.CalledProcessError, OSError, ValueError):
        # Unreadable sources only: no git root, or an uncommitted rule source (`Refused` is a
        # ValueError). A programming error propagates rather than reading as a clean check.
        return []
    except Exception as exc:  # noqa: BLE001
        if type(exc).__name__ in ("RecordError", "PathRefused", "BionicConfigError", "YAMLError"):
            return []
        raise


def validate_file(path: Path, kind_override: str | None) -> tuple[int, list[dict]]:
    """Validate one file. Returns (exit_code, errors)."""
    file_label = str(path)
    text = path.read_text(encoding="utf-8")
    try:
        doc = load_yaml(text)
    except YamlCapabilityError:
        # Environment problem, not a document verdict — must reach the crash
        # lane (exit 2 + stderr in main), never the errors JSON (PB-0026).
        raise
    except Exception as exc:  # noqa: BLE001
        return 1, [{
            "file": file_label, "instance_path": "#", "schema_path": "#",
            "error": f"YAML parse error: {exc}",
        }]

    if not isinstance(doc, dict):
        return 1, [{
            "file": file_label, "instance_path": "#", "schema_path": "#",
            "error": "document did not parse to a mapping",
        }]

    kind = kind_override or detect_kind(doc)
    if kind is None:
        return 1, [{
            "file": file_label, "instance_path": "#", "schema_path": "#",
            "error": "could not classify document as promptbook or run "
                     "(need PB id+prompts without run_id, or both book_id+run_id)",
        }]

    schema_path = PROMPTBOOK_SCHEMA if kind == "promptbook" else RUN_SCHEMA
    schema = load_schema(schema_path)  # reject-on-load happens here.

    errors: list[dict] = []
    validate(doc, schema, "#", "#", errors, file_label)
    # Post-schema structural pass runs regardless of schema errors so the
    # contiguity finding is surfaced too.
    post_schema_pass(doc, errors, file_label)
    # Cycle-coverage invariants (ADR-0029) — promptbook kind only; a no-op for
    # non-cycle books and for grandfathered cycle books.
    if kind == "promptbook":
        cycle_coverage_pass(doc, errors, file_label)
    errors.extend(duplicate_number_errors(path, file_label))
    if kind == "promptbook":
        errors.extend(declared_empty_overlap_errors(path, doc, file_label))

    return (1 if errors else 0), errors


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="validate-promptbook",
        description="Validate a crux promptbook or run-snapshot YAML document "
                    "against its draft-2020-12 JSON Schema (vendored stdlib engine).",
    )
    parser.add_argument("paths", nargs="+", type=Path, help="YAML file(s) to validate.")
    parser.add_argument(
        "--kind", choices=["promptbook", "run"], default=None,
        help="Force the document kind. If omitted, auto-detect from keys.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Accepted for parity with sibling validators; this script never writes.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    # Correctness-critical: this script produces validation verdicts and the
    # frozen-plan content hash. The minimal fallback parser is banned here
    # (PB-0026: it diverges silently from PyYAML outside its subset) — require
    # the real parser, auto-repairing via uv when possible.
    try:
        ensure_real_yaml(__file__)
    except YamlCapabilityError as exc:
        print(f"validate-promptbook: {exc}", file=sys.stderr)
        return 2
    all_errors: list[dict] = []
    worst = 0
    for path in args.paths:
        if not path.is_file():
            all_errors.append({
                "file": str(path), "instance_path": "#", "schema_path": "#",
                "error": "file not found",
            })
            worst = 1
            continue
        try:
            code, errors = validate_file(path, args.kind)
        except YamlCapabilityError as exc:  # defense-in-depth; entry guard should prevent this
            print(f"validate-promptbook: {exc}", file=sys.stderr)
            return 2
        all_errors.extend(errors)
        worst = max(worst, code)

    if worst != 0:
        print(json.dumps({"errors": all_errors}, indent=2))
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
