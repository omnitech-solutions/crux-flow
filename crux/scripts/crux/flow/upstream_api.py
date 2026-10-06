from __future__ import annotations

from functools import lru_cache
import importlib.util
from pathlib import Path
import sys
from types import ModuleType

from .common import FlowError, digest
from .managed import read_file


@lru_cache(maxsize=12)
def load(plugin: Path, script: str) -> ModuleType:
    if script not in {'advance-run.py','validate-promptbook.py','generate-routing-table.py','check-promptbook-index.py'}:
        raise FlowError('unsupported upstream module seam')
    root=plugin.resolve()
    raw=read_file(root,'scripts/'+script)
    if raw is None:
        raise FlowError('upstream script is absent from the loaded payload')
    name='_crux_flow_seam_'+script.replace('-','_').replace('.','_')+'_'+str(digest(raw))[:16]
    spec=importlib.util.spec_from_file_location(name,root/'scripts'/script)
    if spec is None or spec.loader is None:
        raise FlowError('upstream module could not be loaded')
    module=importlib.util.module_from_spec(spec)
    sys.modules[name]=module
    spec.loader.exec_module(module)
    return module


def splice(plugin: Path, raw: bytes, before: dict, after: dict) -> bytes:
    try:
        return load(plugin,'advance-run.py')._splice(raw.decode('utf-8'),before,after).encode('utf-8')
    except SystemExit as exc:
        raise FlowError('existing Crux writer refused a non-preserving update') from exc


def book_hash(plugin: Path, data: dict) -> str:
    return load(plugin,'validate-promptbook.py').compute_book_hash(data)
