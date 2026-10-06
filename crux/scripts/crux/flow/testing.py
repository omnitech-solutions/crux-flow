from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

from .common import FlowError, integer

BASE_PACKAGES=('pytest','yaml','httpx','ruamel.yaml','jsonschema','packaging')
UPSTREAM_PACKAGES=('griffe','tree_sitter','tree_sitter_swift','tree_sitter_elixir','tree_sitter_typescript','tree_sitter_ruby')


def preflight(*,full: bool=False) -> dict:
    missing=[]
    for name in BASE_PACKAGES+(UPSTREAM_PACKAGES if full else ()):
        try: available=importlib.util.find_spec(name) is not None
        except ModuleNotFoundError: available=False
        if not available: missing.append(name)
    return {'python':sys.version,'missing':missing,'ready':not missing,'scope':'upstream-and-reference' if full else 'reference'}


def commands(candidate: Path,*,full: bool=False,test_seconds: int=600) -> list[dict]:
    integer(test_seconds,low=1,high=3600)
    result=[{'name':'catalog','argv':['uv','run','--group','test','python','crux/scripts/validate-catalog.py','--dry-run'],'seconds':60},
            {'name':'reference','argv':['uv','run','--group','test','python','-m','pytest','tests/flow','-m','not upstream and not native and not provider','-q'],'seconds':test_seconds}]
    if full: result.append({'name':'upstream','argv':['uv','run','--group','upstream-test','python','-m','pytest','crux/scripts/tests','-q'],'seconds':1800})
    result.append({'name':'package','argv':['uv','run','--script','./crux-flow','package','--output',str(candidate/'.cache/candidate-verification-build')],'seconds':120})
    return result
