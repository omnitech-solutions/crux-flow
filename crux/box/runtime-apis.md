# Runtime APIs for team workflows

Read this reference when code uses tracing, probe coordination, persistent identities, or task-plan data. These Python modules remain available without separate installed skill entries. Resolve the installed plugin root from a selected Crux `SKILL.md`: it is the parent of `skills/`. Claude Code may instead supply `CLAUDE_PLUGIN_ROOT`. In a source checkout, use the checkout's `crux/` directory. Importing `crux` also imports its `httpx` router.

For a standalone caller, create a driver in a private temporary directory. Substitute the installed root in the last command. The PEP 723 block supplies `httpx` even outside a project's development environment. Add imports and calls for the API needed by the task after the four sample imports.

```bash
runtime_driver_dir=$(mktemp -d)
cat > "$runtime_driver_dir/runtime_apis.py" <<'PY'
# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx>=0.27"]
# ///
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(os.environ["CRUX_PLUGIN_ROOT"]).resolve() / "scripts"))

from crux.core import Phase, get_tracer
from crux.identity import KnowledgeStore
from crux.semantic_bridge import create_semantic_bridge
from crux.task_planner import create_fallback_plan

print("runtime APIs importable")
PY
CRUX_PLUGIN_ROOT="/absolute/path/to/installed/crux" uv run --no-project "$runtime_driver_dir/runtime_apis.py"
```

## Tracing

The source of truth for the default trace location is `scripts/crux/core/tracer.py`, including its `DEFAULT_TRACES_DIR` and `CRUX_TRACES_DIR` override. Do not copy a literal default path into another instruction. Set `CRUX_TRACES_DIR` before process startup to colocate traces with an autonomous run. A `Tracer` writes a session index and one Markdown file per entry.

```python
from crux.core import Phase, get_tracer

tracer = get_tracer("my-session")
trace_path = tracer.log(
    phase=Phase.EXECUTION,
    title="Checked the build",
    context="project checkout",
    reasoning="The failing gate named a stale projection",
    decision_action="Regenerate the projection",
)
```

Trace reasoning trajectories such as councils, hypothesis loops, and team workflows. A single utility call does not need a new trace session. Pass an existing tracer to Crux functions that accept one. Consult `scripts/crux/core/tracer.py` for `Tracer`, `TraceEntry`, `ModelCall`, `SelfReflection`, `get_tracer`, and `log_reasoning`; the source owns their signatures.

## Probe coordination

`crux.semantic_bridge` exports `SemanticBridge`, `BridgeStatistics`, and `create_semantic_bridge`. One `SemanticBridge` instance links probes and dissents **within one process**. It does not persist or deduplicate between processes, runs, or sessions. Share the instance among in-process teammates; check `is_answered_by_probe` before repeating a probe and register results after a probe. `create_semantic_bridge(answer_threshold=0.5)` uses the default keyword-overlap threshold; adjust it when the match needs to be stricter or looser. The backing contract is `scripts/crux/semantic_bridge/bridge.py`.

## Persistent identities

`crux.identity` exports `Identity`, `KnowledgeStore`, `Learner`, `get_or_create_identity`, `Hint`, `Pattern`, `Warning_`, and `DEFAULT_REGISTRY_DIR`. `KnowledgeStore()` uses the existing identity store under `CRUX_HOME` when configured, otherwise the user's Crux home. Records remain keyed by GUID; keep the GUID when resuming an identity.

```python
from crux.identity import KnowledgeStore, Learner, get_or_create_identity

store = KnowledgeStore()
agent = get_or_create_identity(store, name="reviewer", purpose="Review changes")
expert = store.find_expert("security")
if expert:
    Learner(store).learn_from(agent, expert.guid, domains=["security"])
```

Identity methods and storage layout are defined in `scripts/crux/identity/identity.py`. Existing identity files are not changed by removing a skill entry.

## Structured task plans

For a user request, use `whiteboarding` or the host's planning facility to decompose an ad-hoc goal. Use `author-promptbook` or the relevant cycle when execution needs a tracked plan. Code that needs a serializable `TaskPlan` may continue importing `Task`, `TaskPlan`, `decompose_goal`, `create_fallback_plan`, `save_plan`, and `load_plan` from `crux.task_planner`. `decompose_goal` uses the configured model gateway; `create_fallback_plan` is deterministic. This API is independent of the retained runbook generator. The backing contract is `scripts/crux/task_planner/task_planner.py`.
