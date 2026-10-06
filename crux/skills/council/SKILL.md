---
name: council
description: "Convene multiple models to evaluate approaches, tradeoffs, or architectural decisions and return their judgments."
context: fork
model: opus
metadata:
  tags: "verification, multi-model, council, anti-hallucination"
  bundles: "crux-verification, crux-docs"
  risk_level: "medium"
  triggers: "run the council | convene the council | ask the council | get a multi-model opinion"
  requires_env: "OPENROUTER_API_KEY"
---

# Crux Council Vote (Async-First)

> **Execution context:** this skill runs in a forked subagent and returns a summary to the caller. The fork does not see the main-thread conversation, so pass any needed context explicitly at invocation.

<!-- BEGIN GENERATED: runtime-compat -->
## Runtime compatibility

This skill is portable across Claude Code, Codex, and OpenCode. This section overrides platform-specific labels below.

- Before running a command that uses `CRUX_PLUGIN_ROOT`, set it to the installed plugin root. In Claude Code, use the value of `CLAUDE_PLUGIN_ROOT`. In Codex and OpenCode, derive it from the absolute path of this selected `SKILL.md`: the plugin root is the parent of its `skills/` directory. In a source checkout, use the checkout `crux/` directory.
- For project-local skills, use `.claude/skills` in Claude Code, `.agents/skills` in Codex, and `.opencode/skills` in OpenCode, which also reads the singular `.opencode/skill`. Set `CRUX_LOCAL_SKILLS_DIR` to that path before following any command below that uses it.
- Translate Claude Code tool labels such as `Agent`, `Read`, `Write`, `Bash`, `WebSearch`, and `WebFetch` to the matching capability in the current session. Codex names its own capabilities; OpenCode uses the lowercase forms `subagent`, `read`, `edit`, `shell`, `websearch`, and `webfetch`, where `edit` covers both `Edit` and `Write`. Do not attempt to invoke the Claude Code labels as literal commands on another host.
- Install the generated role agents before delegating: `install-codex-agents` in Codex, `install-opencode-agents` in OpenCode. Codex names them `crux_architect`, `crux_brainstormer`, `crux_commander`, `crux_dev_lead`, `crux_developer`, `crux_historian`, `crux_librarian`, `crux_night_gardener`, `crux_reviewer`, and `crux_wayfinder`; OpenCode uses the bare role names `architect`, `brainstormer`, `commander`, `dev-lead`, `developer`, `historian`, `librarian`, `night-gardener`, `reviewer`, and `wayfinder`. If a required role or capability is unavailable, report that truthfully instead of claiming it ran.
- Argument placeholders such as `$adr` and `$book` bind only in Claude Code. On a host without argument binding they are unset — take the value from the user's phrase. The "Fields OpenCode ignores" section of `OPENCODE_GUIDE.md` names the invocation-control fields OpenCode ignores.
<!-- END GENERATED: runtime-compat -->


## When to Use
- Choosing between architectural approaches
- Evaluating tradeoffs (cost vs accuracy, speed vs correctness)
- Any decision where multi-model consensus reduces risk
- Before committing to a design pattern or library choice

## When NOT to Use
- **Factual lookups** ("what's the default timeout?") — a council of three models burns three API calls to confirm one fact. Use `call-llm` for a single answer.
- **Obvious single answers** — if the choice is clear, a council just rubber-stamps it at 3x the cost and latency.
- **Tight loops** — never put a council call inside a per-step or per-item loop; the parallel-but-multi-model cost compounds fast. Use `call-llm` for in-loop reasoning and reserve the council for the one decision that frames the loop.

A good council question is a genuine *fork* — two or more defensible approaches where multiple perspectives change the outcome. If there's no fork, skip it.

## File Locations
```
crux/scripts/crux/council/
├── __init__.py
├── async_council.py   ← ALWAYS USE THIS
└── council.py         ← sync fallback only
```

## Exports
```
From council __init__:
  council_vote, quick_council, get_opinion, synthesize_opinions,
  VotingMethod, Opinion, CouncilDecision, DEFAULT_COUNCIL, MODEL_WEIGHTS,
  AsyncCouncil, AsyncVisualCouncil, AsyncCouncilConfig, VisualVoteResult,
  create_async_council, create_visual_council
```

## Running it (PEP 723 driver under `uv`, NOT bare `python3`)

The council needs `httpx`, the HTTP client — **NOT** present in the bare
system `python3`, so running there dies with `ModuleNotFoundError: No module named 'httpx'`. The
canonical pattern: write your driver to a temp `.py` file that
starts with the standard PEP 723 header, then run it with **`uv`**, which
provisions the dependencies into a cached, isolated environment:

```python
# /// script
# requires-python = ">=3.10"
# dependencies = ["httpx>=0.27"]
# ///
import sys
sys.path.insert(0, "${CRUX_PLUGIN_ROOT}/scripts")

from crux.council import create_async_council
# ... your driver code (see Usage below)
```

```bash
d=$(mktemp -d) && uv run "$d/driver.py"
```

Each attempt at a seat has its own deadline, 600 s by default. A seat that fails with a
`timeout`, `provider`, `malformed-response` or `truncated` fault is called once more, so the
worst case per seat is two deadlines, about 20 minutes. A Claude Code foreground shell call
stops at 10 minutes, so **run the driver as a background command** and read its output when it
exits. A shell that kills the driver first loses every seat's answer. `AsyncCouncilConfig`
takes `max_retries` 0 (no retry) or 1 (the default); any other value is refused when the
configuration is built.

Write the driver into a private per-run directory as above, never a fixed shared
path like `/tmp/driver.py` — a predictable name in a world-writable directory is a
symlink hazard.

When writing the temp file, substitute `${CRUX_PLUGIN_ROOT}` (crux's portable
plugin-root name — in Claude Code, the value of `CLAUDE_PLUGIN_ROOT`; in Codex,
derived from this `SKILL.md`'s path per the Runtime compatibility note above)
with its actual value; in a source checkout, where the
variable is unset, substitute the checkout's `crux/` directory. No
`PYTHONPATH`, no extras, no surrounding project needed — the inline metadata
makes `uv` ignore any enclosing project.

**Without `uv` installed this fails at the shell** — `command not found: uv`
(exit 127); the script never executes. Remediation: install uv
(https://docs.astral.sh/uv/). API keys are read from `~/.crux/env` via
`crux_env`. One key backs every seat: `crux-env set OPENROUTER_API_KEY …`.

## ALWAYS Use Async

```python
import asyncio

from crux.council import create_async_council, AsyncCouncilConfig

async def decide(question: str, context: str):
    council = create_async_council()

    decision = await council.deliberate(
        prompt=f"Question: {question}\n\nContext: {context}",
        system="You are a technical architecture council member.",
    )

    # deliberate() returns a CouncilDeliberation (crux/scripts/crux/core/data_classes.py):
    #   votes, consensus, consensus_confidence, key_agreements, key_disagreements,
    #   final_recommendation, dissent_count, confidence_adjustment (+ .has_critical_dissent).
    # final_recommendation keys: action, reason, degraded, errored_seats, off_scale,
    #   conditioned, conditions, nits, nit_items.
    print(f"Consensus: {decision.consensus}")              # e.g. UNANIMOUS_APPROVE
    # Consensus labels: UNANIMOUS_APPROVE, UNANIMOUS_REJECT, MAJORITY_APPROVE, MAJORITY_REJECT,
    # SPLIT, NO_QUORUM, and UNANIMOUS_<TOKEN> when every responding seat returned the same
    # other decision. UNANIMOUS_OFF_SCALE replaces it when the token is not 1-40 characters of
    # A-Z and underscore starting with a letter, contains APPROVE or REJECT, or is AUTO_EXECUTE
    # or EXECUTE_WITH_MONITORING.
    # Only UNANIMOUS_APPROVE and MAJORITY_APPROVE can route to an execute action; every other
    # label routes to DEFER_TO_HUMAN or ABORT. Unanimity on REVISE is agreement, not approval.
    print(decision.final_recommendation["off_scale"])      # ["<provider>:<decision>", ...] for
                                                            # responding seats outside the known
                                                            # scale; each entry is bounded and
                                                            # its unprintable characters replaced
    print(f"Confidence: {decision.consensus_confidence}")

    # A conditioned approval: a responding seat returned APPROVE_WITH_CONDITIONS. The label
    # can still read UNANIMOUS_APPROVE or MAJORITY_APPROVE, so read the flag. The route is EXECUTE_WITH_MONITORING at
    # most, never AUTO_EXECUTE. The flag fires only when the prompt puts APPROVE_WITH_CONDITIONS
    # on the decision scale (the built-in envelope does not).
    fr = decision.final_recommendation
    if fr["conditioned"]:                                   # decision.conditioned is the same flag
        for seat, points in fr["conditions"].items():       # keyed by seat; untrusted model text, carried unchanged
            print(f"  [condition] {seat}: {points}")
    # Nits: a responding seat returned APPROVE_WITH_NITS. Nits are reported and never change
    # the route. Only conditions block.
    if fr["nits"]:                                          # decision.nits is the same flag
        for seat, points in fr["nit_items"].items():
            print(f"  [nit] {seat}: {points}")
    # Both flags read False, with empty items, when no responding seat returned them. An
    # errored seat never contributes.

    if decision.dissent_count:
        print(f"{decision.dissent_count} dissent(s) — feed into SRDE")
        for v in decision.votes:                            # each vote is a CouncilVote
            for d in v.dissenting_points:                   # NOTE: dissenting_points, not dissent_points
                print(f"  [{v.provider}/{v.model}] {d}")
        # See srde skill

    # Each vote also records how its seat fared. finish_reason is the reply's finish reason
    # from a closed set (stop, length, content_filter, tool_calls, error, other, missing; None
    # when the seat got no reply, such as a timeout). fault_label names an errored seat's cause
    # (one of timeout, provider, malformed-response, truncated, auth, rate-limit, client-config,
    # refused, insufficient-credit, unexpected-status or unreachable, or unknown). retried marks a seat called a second time; recovered marks a seat that answered on
    # that second call; first_fault_label and first_finish_reason describe its first attempt.
    for v in decision.votes:
        print(v.provider, v.finish_reason, v.fault_label, v.retried, v.recovered)

    return decision

result = asyncio.run(decide(
    question="Should we use approach A or B?",
    context="Full context including constraints and requirements",
))
```

### Async with Custom Config

```python
config = AsyncCouncilConfig(...)  # customize models, weights, etc.
council = create_async_council(config=config)
decision = await council.deliberate(prompt="...", system="...")
```

### Sync Deliberation (from async council)

```python
council = create_async_council()
decision = council.deliberate_sync(prompt="...", system="...")
```

### Visual Council (image analysis)

```python
from crux.council import create_visual_council

visual_council = create_visual_council()
votes = await visual_council.analyze_image(
    image_b64="<base64-encoded-image>",
    prompt="Does this dashboard render correctly?",
)
# Or sync:
votes = visual_council.analyze_sync(image_b64="...", prompt="...")
```

The visual council retries and labels a seat as the text council does. Its result for a seat
that errored reads `passed=False` at confidence 0.0 and carries `errored=True`. Skip errored
results when you count failed checks: an errored seat is a missing check, not a failed one.

## Sync Fallback (only if async is impossible)

```python
from crux.council import council_vote, quick_council

# Full council with options
decision = council_vote(
    question="Should we use approach A or B?",
    context="Full context here",
    models=["claude-opus-5.5-xhigh", "gemini-3.1-pro-preview"],
    voting_method=VotingMethod.ARBITER,
    arbiter="claude-opus-5.5-xhigh",
    tracer=None,  # optional Tracer instance
)

# Quick one-liner (returns string)
answer = quick_council("Should we cache at the API or DB layer?")

# Get a single model's opinion
opinion = get_opinion("claude-opus-5.5-xhigh", "question", context="...")
```

## Model Config

Models are configured in `crux/scripts/crux/_config/llm_router_config.json`:
- `claude-opus-5.5-xhigh` — Anthropic council seats and arbiter at extra-high reasoning effort
- `claude-opus-5.5` — Same SKU at high effort for release-document generation
- `gemini-3.1-pro-preview` — 1M context, analysis
- `gpt-6-astra` — OpenAI async text and visual council seat
- `gpt-6.1-sol` — OpenAI synchronous council seat

The Anthropic seat in the default councils runs on `claude-opus-5.5-xhigh`.
The async text and visual councils, sync member, and sync arbiter use this entry.
The router reaches Anthropic through the OpenRouter gateway.

API keys are read via `crux_env.require(...)` from `~/.crux/env` (managed by `crux-env`).

## Rules
- ALWAYS prefer async for agent teams (parallel teammates = concurrent calls)
- ALWAYS check `dissent_count` (and each vote's `dissenting_points`) — never just read `consensus`
- ALWAYS read `final_recommendation["conditioned"]` and `["nits"]`: a conditioned approval can read `UNANIMOUS_APPROVE` or `MAJORITY_APPROVE` but never routes to `AUTO_EXECUTE`
- ALWAYS run a council driver in the background: the worst case per seat is about 20 minutes
- ALWAYS provide rich context — models deliberate better with specifics
- Feed dissents into SRDE for automatic resolution (see srde skill)
- Register results on Semantic Bridge for cross-teammate visibility
