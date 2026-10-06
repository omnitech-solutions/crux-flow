#!/usr/bin/env python3
"""crux_config.py — back-compat shim (ADR-0044).

The canonical loader is now `bionic_config.py`: per ADR-0044, `.bionic.yml` is
the layout source of truth and supersedes the legacy `.crux` file (ADR-0032).
This module is retained so existing imports and the `crux-config.py` CLI keep
working during the deprecation window — it re-exports every public name from
`bionic_config`. New code should import `bionic_config` directly.

Naming note: this is the repo-root `.crux` FILE surface (committed project
config) — distinct from the user-home `~/.crux/` DIRECTORY (uncommitted
secrets, per ADR-0002 / crux_env.py).
"""

from __future__ import annotations

try:  # plain import works when scripts/ is on sys.path (CLI invocation)
    import bionic_config as _bionic
except ImportError:  # package-context import (e.g. the test suite loads by path)
    import importlib.util as _ilu
    from pathlib import Path as _Path

    _spec = _ilu.spec_from_file_location(
        "bionic_config", _Path(__file__).resolve().parent / "bionic_config.py"
    )
    _bionic = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_bionic)

# Re-export every name (incl. single-underscore module-level helpers the test
# suite pokes at, e.g. `_validate_docs_dir_text`) so `from crux_config import X`
# keeps working. Only dunders are skipped, to preserve this shim's own module
# metadata and the `_bionic` handle above.
globals().update({k: getattr(_bionic, k) for k in dir(_bionic) if not k.startswith("__")})

# Explicit re-exports (make the contract legible + satisfy static importers).
BionicConfig = _bionic.BionicConfig
BionicConfigError = _bionic.BionicConfigError
CruxConfig = _bionic.CruxConfig
CruxConfigError = _bionic.CruxConfigError
load_config = _bionic.load_config
CONFIG_FILENAME = _bionic.CONFIG_FILENAME
BIONIC_CONFIG_FILENAME = _bionic.BIONIC_CONFIG_FILENAME
DEFAULT_DOCS_DIR = _bionic.DEFAULT_DOCS_DIR
SUPPORTED_CONFIG_VERSIONS = _bionic.SUPPORTED_CONFIG_VERSIONS
