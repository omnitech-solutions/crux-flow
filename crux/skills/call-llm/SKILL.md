---
name: call-llm
description: "Call external language models from crux code through its shared OpenRouter gateway and model configuration."
user-invocable: false
metadata:
  tags: "llm, multi-model, gateway"
  bundles: "crux-core, crux-docs"
  risk_level: "medium"
  requires_env: "OPENROUTER_API_KEY"
---

# Crux LLM Caller

<!-- BEGIN GENERATED: runtime-compat -->
## Runtime compatibility

This skill is portable across Claude Code, Codex, and OpenCode. This section overrides platform-specific labels below.

- Before running a command that uses `CRUX_PLUGIN_ROOT`, set it to the installed plugin root. In Claude Code, use the value of `CLAUDE_PLUGIN_ROOT`. In Codex and OpenCode, derive it from the absolute path of this selected `SKILL.md`: the plugin root is the parent of its `skills/` directory. In a source checkout, use the checkout `crux/` directory.
- For project-local skills, use `.claude/skills` in Claude Code, `.agents/skills` in Codex, and `.opencode/skills` in OpenCode, which also reads the singular `.opencode/skill`. Set `CRUX_LOCAL_SKILLS_DIR` to that path before following any command below that uses it.
- Translate Claude Code tool labels such as `Agent`, `Read`, `Write`, `Bash`, `WebSearch`, and `WebFetch` to the matching capability in the current session. Codex names its own capabilities; OpenCode uses the lowercase forms `subagent`, `read`, `edit`, `shell`, `websearch`, and `webfetch`, where `edit` covers both `Edit` and `Write`. Do not attempt to invoke the Claude Code labels as literal commands on another host.
- Install the generated role agents before delegating: `install-codex-agents` in Codex, `install-opencode-agents` in OpenCode. Codex names them `crux_architect`, `crux_brainstormer`, `crux_commander`, `crux_dev_lead`, `crux_developer`, `crux_historian`, `crux_librarian`, `crux_night_gardener`, `crux_reviewer`, and `crux_wayfinder`; OpenCode uses the bare role names `architect`, `brainstormer`, `commander`, `dev-lead`, `developer`, `historian`, `librarian`, `night-gardener`, `reviewer`, and `wayfinder`. If a required role or capability is unavailable, report that truthfully instead of claiming it ran.
- Argument placeholders such as `$adr` and `$book` bind only in Claude Code. On a host without argument binding they are unset — take the value from the user's phrase. The "Fields OpenCode ignores" section of `OPENCODE_GUIDE.md` names the invocation-control fields OpenCode ignores.
<!-- END GENERATED: runtime-compat -->

## One gateway, one key

Every model crux calls resolves through **one OpenAI-compatible gateway**, authenticated by **one `OPENROUTER_API_KEY`**. There is no per-provider client, no provider enum, and no provider-specific key. A model is named by a registry key; the registry maps that key to the gateway address the request is sent to.

Two consequences worth stating, because both are load-bearing:

- **Adding or retiring a model is a registry edit**, not a code change. Nothing in this skill's surface names a vendor.
- **The one key is a single point of failure by design.** When it is missing or rejected, every call fails at once rather than one provider degrading. The council turns that into a below-quorum deferral rather than a verdict; see `council/SKILL.md`.

## File locations

```
crux/scripts/crux/core/
└── llm_caller.py             ← the gateway client

crux/scripts/crux/_config/
└── llm_router_config.json    ← the registry: model keys, addresses, params

~/.crux/env                   ← OPENROUTER_API_KEY (managed by `crux-env`)
```

## Exports

Re-exported from `crux.core` as `crux.llm`:

```
Calling:     call_model, call_gateway, build_gateway_request
Response:    parse_gateway_response, raise_for_gateway_status
Convenience: call_anthropic_top, call_anthropic_balanced, call_gemini_pro, call_gemini_flash
Discovery:   list_available_models, get_model_config, get_default_model, get_default_models
Config:      ModelConfig, gateway_timeout_seconds, invalidate_model_cache
Errors:      ModelRefusedError, GatewayError, GatewayInsufficientCreditError,
             GatewayUpstreamError, GatewayTimeoutError, GatewayAuthError,
             GatewayRateLimitError, GatewayClientError, GatewayUnexpectedStatusError
```

The convenience functions resolve registry roles, not fixed model families. `call_anthropic_balanced` currently resolves to Opus through `anthropic_balanced`. The older public names `call_claude_sonnet` and `call_claude_opus` remain deprecated compatibility aliases that delegate to `call_anthropic_balanced` and `call_anthropic_top`; they do not select a Sonnet or Opus family by name.

## Running it (PEP 723 driver under `uv`)

`llm_caller.py` imports `httpx`, so bare `python3` dies with `ModuleNotFoundError`. Write your driver to a temp `.py` starting with the standard PEP 723 header, then run it with `uv` (which provisions the deps into a cached, isolated environment):

```python
# /// script
# requires-python = ">=3.10"
# dependencies = ["httpx>=0.27"]
# ///
import sys
sys.path.insert(0, "${CRUX_PLUGIN_ROOT}/scripts")

from crux.llm import call_model
# ... your driver code
```

```bash
d=$(mktemp -d) && uv run "$d/driver.py"
```

Write the driver into a private per-run directory as above, never a fixed shared path like `/tmp/driver.py` — a predictable name in a world-writable directory is a symlink hazard.

When writing the temp file, substitute `${CRUX_PLUGIN_ROOT}` (crux's portable plugin-root name — in Claude Code, the value of `CLAUDE_PLUGIN_ROOT`; in Codex, derived from this `SKILL.md`'s path per the Runtime compatibility note above) with its actual value; in a source checkout substitute the checkout's `crux/` directory. Without `uv` installed this fails at the shell (`command not found: uv`, exit 127) — remediation: install uv (https://docs.astral.sh/uv/). See `council/SKILL.md` and `docs/AGENTS.md` §10.A for the canonical reference.

## `call_model` — the recommended entry point

```python
from crux.llm import call_model

response = call_model(
    model="claude-opus-5.5", # a registry key, not a vendor model id
    prompt="Analyze this architecture for potential issues...",
    system="You are a senior software architect.",
    max_tokens=8000,         # optional; the registry entry's default if None
    temperature=0.7,         # optional; omitted for models that reject it
)
print(response)              # a string
```

`call_model` resolves the registry entry, builds the request, sends it, maps the status, and returns the assistant text.

## `build_gateway_request` and `call_gateway` — the lower level

Reach for these when you need something `call_model` does not expose: multi-part content (images, video, audio), a `response_format` pin, a per-call timeout, or the raw response body.

```python
from crux.llm import build_gateway_request, call_gateway, get_model_config, get_default_model

cfg = get_model_config(get_default_model("google_fast"))

# One call, text in and text out — but with a per-call deadline.
text = call_gateway(cfg, "Summarize this.", max_tokens=2000, timeout=60.0)

# Multi-part content: pass a list of OpenAI-compatible parts instead of a string.
parts = [
    {"type": "text", "text": "What is in this image?"},
    {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
]
text = call_gateway(cfg, parts, max_tokens=2000)

# Or build the request without sending it — useful in tests and probes.
url, headers, payload = build_gateway_request(cfg, parts, max_tokens=2000)
```

`build_gateway_request` is the **single place the wire format is decided**. The synchronous caller and the council both build through it, so a change to the request shape cannot land on one path and miss the other. It returns a `(url, headers, payload)` triple and sends nothing, which is what makes the payload assertable in a test without a network call.

The key travels in a header, never in the URL. HTTP error text embeds the full URL including any query string, and callers persist those error strings — a key in the query string would leak into logs on any non-2xx.

## `ModelConfig`

The resolved registry entry. Fields worth knowing:

| Field | Meaning |
|---|---|
| `api_string` | The model id sent to the gateway. |
| `max_output_tokens` | The default output budget when a call passes none. |
| `effort` | The reasoning-effort pin, sent as the top-level `reasoning_effort`. |
| `supports_temperature` | When false, `temperature` is omitted rather than sent and rejected. |
| `serving_providers` | The upstream hosts this entry may route to (`provider.only`). Empty means the gateway chooses. |
| `data_collection` | The retention pin, sent on **every** request. Defaults to `"deny"`. |

`ModelConfig` is a dataclass, so an override for one call is `dataclasses.replace(cfg, effort="low")` — the registry entry stays untouched.

**`serving_providers` and `data_collection` are separate concerns and must stay separate.** `serving_providers` pins *which host* may serve the request; the council uses it to hold its seats on distinct upstreams. `data_collection` denies prompt retention and applies to *every* request whether or not a host is pinned. They previously travelled together, which meant that any entry without a host pin — most of the registry — sent no retention control at all.

## The error taxonomy

Nine exception classes, each earning its place by a **distinct operator action**. Catch `GatewayError` to handle any status-derived failure; catch a subclass when the action differs.

| Class | Status | What the operator does |
|---|---|---|
| `GatewayInsufficientCreditError` | 402 | Add credit or raise the key's spend limit. **Do not retry** — retrying spends nothing and fixes nothing. |
| `GatewayTimeoutError` | 408, 524 | Retry with a longer deadline. The request is fine. |
| `GatewayRateLimitError` | 429 | Back off, then retry. The request is fine. |
| `GatewayAuthError` | 401, 403 | Fix the credential or the entitlement — re-set it with `crux-env set OPENROUTER_API_KEY <value>`, or confirm presence with `crux-env check` (never prints values). Not a malformed request. |
| `GatewayUpstreamError` | 5xx (incl. 502, 503, 529) | Wait and retry; the fault is server-side. |
| `GatewayClientError` | other 4xx | Fix the request — bad model id, malformed payload, unsupported param. Retrying verbatim reproduces it. |
| `GatewayUnexpectedStatusError` | other non-2xx (3xx, 1xx) | Report the status. The gateway answered something the client does not handle. |
| `GatewayError` | — | The base class. Carries `status_code` as an `int`. |
| `ModelRefusedError` | 200 | Not a status failure at all: the request succeeded and a safety classifier declined. Change the content or route to a fallback model. |

Two properties of this table are deliberate and easy to break:

- **The order of the checks is the partition.** 402 and 429 are matched before the generic 4xx because retrying a credit exhaustion is futile while retrying a throttle is correct — opposite actions a shared label would erase.
- **Every non-2xx is typed.** Nothing falls through to the HTTP library's own error, which carries no operator action and surfaces as `unknown`.

`raise_for_gateway_status` is exported and shared, like `build_gateway_request` beside it: it is the single place an HTTP status becomes a typed exception, so the router, the council and the standalone scripts cannot drift into separate error taxonomies for the same gateway.

`ModelRefusedError` is raised by `parse_gateway_response`, not by the status mapping, because a refusal arrives as a successful response whose `finish_reason` is `content_filter`. Without that check it reads as an empty string, which quietly distorts a council consensus instead of degrading one seat.

## Model discovery

```python
from crux.llm import list_available_models, get_model_config, get_default_model, get_default_models

list_available_models()                # every registry key
get_model_config("claude-opus-5.5")    # the resolved ModelConfig
get_default_model("anthropic_top")     # the model a ROLE resolves to
get_default_models("council_default")  # roles that map to a list
```

**Prefer a role over a model key** in anything durable. `get_default_model("anthropic_top")` survives a model retirement; a hardcoded model key does not. Resolve the role at call time rather than at import, so a registry edit takes effect without a restart.

`get_default_model` raises `TypeError` when the role maps to a list — use `get_default_models` there.

## Caching

`get_default_model` and `gateway_timeout_seconds` read the registry through a cache, so a call does not re-parse the whole registry to recover one value. After editing the registry at runtime, call `invalidate_model_cache()` — it clears both, which is why it exists instead of two separate clears that could leave the caches disagreeing.

## Credentials

`OPENROUTER_API_KEY` is read via `crux_env.require(...)` from `~/.crux/env`, managed by the `crux-env` CLI:

```bash
crux-env set OPENROUTER_API_KEY <value>
```

Do not load dotenv files manually and do not read the key from the process environment directly. A missing key raises `EnvNotConfigured`, whose remediation text names the key — do not echo that text into a log.

## Rules

- Trace multi-step LLM workflows through the Tracer when a reasoning trajectory needs to survive the session. See `${CRUX_PLUGIN_ROOT}/box/runtime-apis.md` for the retained API and its source contract.
- For council decisions, use the `council` skill rather than raw LLM calls.
- Name a **role**, not a model key, wherever the choice outlives the call site.
- Reach for `build_gateway_request` when you need to assert on a request without sending it; that is the seam tests are meant to use.
