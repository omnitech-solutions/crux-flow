"""
crux - Multi-model reasoning substrate for the crux toolkit.

Top-level capabilities exposed by this package:

- council: Multi-model deliberation (sync + async).
- srde: Self-Resolving Dissent Engine.
- semantic_bridge: Probe/dissent/resolution linker.
- identity: Cross-agent learning store.
- task_planner: Goal decomposition into actionable tasks.
- runbook: Autonomous prompt-runbook generator CLI.
- knowledge: Domain knowledge injection with mental models.

The shared `core` subpackage holds the LLM caller, tracer, and dataclasses.

# crux_env reachability

The ported code reads API keys via `crux_env` (~/.crux/env). That
module lives one level up from this package at `scripts/crux_env.py`,
so we prepend that directory to sys.path here to make `import crux_env`
work regardless of how this package is installed. The bare `import` form
matches docs/AGENTS.md §13 and keeps the crux_env API single-import.
"""

import sys as _sys
from pathlib import Path as _Path

# Make `import crux_env` resolvable from inside this package. The env
# module lives in the parent `scripts/` directory.
_SCRIPTS_DIR = str(_Path(__file__).resolve().parent.parent)
if _SCRIPTS_DIR not in _sys.path:
    _sys.path.insert(0, _SCRIPTS_DIR)

from .council import (
    create_async_council,
    AsyncCouncil,
    AsyncVisualCouncil,
    AsyncCouncilConfig,
    council_vote,
    quick_council,
)
# Re-export the core LLM convenience surface under `crux.llm` so callers
# can `from crux.llm import call_model` without reaching into `core`.
from . import core as llm  # noqa: F401  (re-exported as `crux.llm`)

# Register `crux.llm` in sys.modules so `from crux.llm import call_model`
# works even before/without `crux.core` being imported by the caller.
_sys.modules[__name__ + ".llm"] = llm

__all__ = [
    "create_async_council",
    "AsyncCouncil",
    "AsyncVisualCouncil",
    "AsyncCouncilConfig",
    "council_vote",
    "quick_council",
    "llm",
]
