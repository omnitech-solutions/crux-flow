# Retained operator services

The installed skill catalog has fewer entry names, while these commands and Python interfaces remain available. For a new execution plan, use `author-promptbook` or the relevant cycle. The autonomous runbook generator below remains available when it is explicitly requested. Resolve `CRUX_PLUGIN_ROOT` from the selected installed Crux `SKILL.md`: the plugin root is the parent of `skills/`. Claude Code can use `CLAUDE_PLUGIN_ROOT`; in a source checkout use the checkout's `crux/` directory. These paths work in Claude Code, Codex, and OpenCode.

| Former entry | Current route |
|---|---|
| `task-planner` | `whiteboarding` or host planning for an ad-hoc plan; `author-promptbook` for a tracked plan; `crux.task_planner` for code |
| `author-runbook` | `author-promptbook` by default; generator below on explicit request |
| `visualize-run-progress` | `run-promptbook` status |
| `trace-runtime-ops` | [runtime APIs](runtime-apis.md) tracing section |
| `semantic-bridge` | [runtime APIs](runtime-apis.md) probe coordination section |
| `agent-identity` | [runtime APIs](runtime-apis.md) persistent identities section |
| `serve-llm` | HTTP service below |

## Autonomous runbook generator

The generator lives in `scripts/crux/runbook/`. Existing generated runbooks stay readable. It creates a Markdown prompt list and does not consume a `TaskPlan` from `crux.task_planner`. Use `uv` for its PEP 723 dependencies:

```bash
uv run "${CRUX_PLUGIN_ROOT}/scripts/crux/runbook/__main__.py" "Build the feature" --template-only --target-prompts 60 --output runbook.md
uv run "${CRUX_PLUGIN_ROOT}/scripts/crux/runbook/__main__.py" "Build the feature" --ai-plan --target-prompts 60 --output runbook.md
```

`--template-only` is deterministic and calls no model. `--ai-plan` uses the shared OpenRouter gateway; it requires `OPENROUTER_API_KEY` managed by `crux-env`. The CLI accepts a positional goal or `--file/-f`, `--target-prompts/-n` (clamped to 10–100), and `--output/-o`. An unavailable model causes the generator to fall back to a template with a warning prompt. The generated review steps call the council API. Read `scripts/crux/runbook/runbook.py` for the current prompt format and Python interface.

## HTTP model service

Use the HTTP service when a client outside the Python process needs Crux's model caller. For in-process calls use `crux.core.llm_caller` or the `call-llm` skill. The FastAPI app is `crux.server.crux_server:app`, also re-exported as `crux.server:app`. It offers `GET /health`, `GET /models`, and `POST /chat` with `message`, optional `model`, `system`, `history`, and `session_id`. A session ID records a tracer event; chat history uses at most the last 20 messages.

Write this launcher into a private temporary directory, substitute the installed plugin root, then run it with `uv`. FastAPI and Uvicorn are optional dependencies; the LLM router also needs `httpx`.

```python
# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx>=0.27", "fastapi>=0.110", "uvicorn>=0.30"]
# ///
import sys
sys.path.insert(0, "${CRUX_PLUGIN_ROOT}/scripts")

import uvicorn
uvicorn.run("crux.server.crux_server:app", host="127.0.0.1", port=8000)
```

```bash
operator_dir=$(mktemp -d)
# Save the launcher above as "$operator_dir/serve_crux.py" after root substitution.
uv run "$operator_dir/serve_crux.py"
```

The app itself starts without a key. `/chat` calls the gateway and requires `OPENROUTER_API_KEY` in the Crux environment managed by `crux-env`; do not copy a key into the launcher or load a dotenv file. The service has no authentication, rate limit, or TLS. Its `allow_origins=["*"]` CORS policy lets any website call a reachable `/chat` endpoint and spend the provider budget. Bind to localhost for local use. Before exposing the app beyond localhost, narrow CORS origins and add authentication, TLS, and a request budget. The backing contract is `scripts/crux/server/crux_server.py`.
