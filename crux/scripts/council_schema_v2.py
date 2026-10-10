"""Version-owned schema interpreter for issued council policy 2.

Extracted unchanged from validate-promptbook's bounded schema engine. Future
public schema evolution cannot silently reinterpret this policy's records.
"""
from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Any

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
