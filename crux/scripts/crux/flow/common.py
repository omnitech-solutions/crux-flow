from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any

import yaml

PLUGIN = Path(__file__).resolve().parents[3]
HOSTS = ('claude', 'codex', 'opencode', 'omp')
MODEL = re.compile(r'[A-Za-z0-9][A-Za-z0-9._/+:-]{0,199}\Z')


class FlowError(ValueError):
    pass


class StrictLoader(yaml.SafeLoader):
    pass


def _mapping(loader: StrictLoader, node: yaml.MappingNode, deep: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in node.value:
        name = loader.construct_object(key, deep=deep)
        if not isinstance(name, str) or name in result:
            raise FlowError('mapping keys must be unique strings')
        result[name] = loader.construct_object(value, deep=deep)
    return result


StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)
StrictLoader.yaml_implicit_resolvers = {
    key: [(tag, pattern) for tag, pattern in values if tag != 'tag:yaml.org,2002:timestamp']
    for key, values in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def decode(data: bytes | str, *, maximum: int = 16_000_000) -> Any:
    raw = data.decode('utf-8') if isinstance(data, bytes) else data
    if len(raw.encode('utf-8')) > maximum:
        raise FlowError('document exceeds the permitted size')
    try:
        for event in yaml.parse(raw):
            if isinstance(event, yaml.AliasEvent) or getattr(event, 'anchor', None):
                raise FlowError('YAML aliases and anchors are not supported')
        return yaml.load(raw, Loader=StrictLoader)
    except (yaml.YAMLError, RecursionError, UnicodeError) as exc:
        raise FlowError('invalid structured document') from exc


def mapping(value: Any, *, allowed: set[str] | None = None, required: set[str] = frozenset()) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(k, str) for k in value):
        raise FlowError('expected a string-keyed mapping')
    if required - value.keys() or (allowed is not None and value.keys() - allowed):
        raise FlowError('missing required fields or unknown fields')
    return value


def canonical(value: Any) -> bytes:
    try:
        return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')
    except (TypeError, ValueError, RecursionError) as exc:
        raise FlowError('value is not finite JSON data') from exc


def digest(value: bytes | None) -> str | None:
    return hashlib.sha256(value).hexdigest() if value is not None else None


def identity(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def text(value: Any, *, maximum: int = 20_000, label: str = 'text') -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or '\x00' in value:
        raise FlowError(f'invalid {label}')
    return value.strip()


def integer(value: Any, *, low: int = 0, high: int = 1_000_000) -> int:
    if type(value) is not int or not low <= value <= high:
        raise FlowError('integer outside supported bounds')
    return value


def model_id(value: Any) -> str:
    if not isinstance(value, str) or MODEL.fullmatch(value) is None:
        raise FlowError('invalid model identifier')
    return value
