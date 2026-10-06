---
name: srde
description: "Resolve dissent from a multi-model council using shared context, existing probes, and structured analysis."
metadata:
  tags: "reasoning, dissent-resolution, council"
  bundles: "crux-core, crux-docs"
  risk_level: "low"
  triggers: "resolve the dissent | run srde | resolve the council disagreement"
---

# Crux SRDE (Self-Resolving Dissent Engine)

<!-- BEGIN GENERATED: runtime-compat -->
## Runtime compatibility

This skill is portable across Claude Code, Codex, and OpenCode. This section overrides platform-specific labels below.

- Before running a command that uses `CRUX_PLUGIN_ROOT`, set it to the installed plugin root. In Claude Code, use the value of `CLAUDE_PLUGIN_ROOT`. In Codex and OpenCode, derive it from the absolute path of this selected `SKILL.md`: the plugin root is the parent of its `skills/` directory. In a source checkout, use the checkout `crux/` directory.
- For project-local skills, use `.claude/skills` in Claude Code, `.agents/skills` in Codex, and `.opencode/skills` in OpenCode, which also reads the singular `.opencode/skill`. Set `CRUX_LOCAL_SKILLS_DIR` to that path before following any command below that uses it.
- Translate Claude Code tool labels such as `Agent`, `Read`, `Write`, `Bash`, `WebSearch`, and `WebFetch` to the matching capability in the current session. Codex names its own capabilities; OpenCode uses the lowercase forms `subagent`, `read`, `edit`, `shell`, `websearch`, and `webfetch`, where `edit` covers both `Edit` and `Write`. Do not attempt to invoke the Claude Code labels as literal commands on another host.
- Install the generated role agents before delegating: `install-codex-agents` in Codex, `install-opencode-agents` in OpenCode. Codex names them `crux_architect`, `crux_brainstormer`, `crux_commander`, `crux_dev_lead`, `crux_developer`, `crux_historian`, `crux_librarian`, `crux_night_gardener`, `crux_reviewer`, and `crux_wayfinder`; OpenCode uses the bare role names `architect`, `brainstormer`, `commander`, `dev-lead`, `developer`, `historian`, `librarian`, `night-gardener`, `reviewer`, and `wayfinder`. If a required role or capability is unavailable, report that truthfully instead of claiming it ran.
- Argument placeholders such as `$adr` and `$book` bind only in Claude Code. On a host without argument binding they are unset — take the value from the user's phrase. The "Fields OpenCode ignores" section of `OPENCODE_GUIDE.md` names the invocation-control fields OpenCode ignores.
<!-- END GENERATED: runtime-compat -->


## When NOT to Use
- **Speculatively, before a council vote exists.** SRDE resolves *dissent points* — it has nothing to act on without a `council.deliberate()` result carrying dissents (each `CouncilVote`'s `dissenting_points`). Don't invoke it to "pre-think" disagreements; run the council first, then feed its dissents in.
- **As a silent failure swallow.** When every resolution strategy fails (context-aware → cross-reference → pattern all return `CANNOT_RESOLVE` / `NEEDS_HUMAN`), do **not** proceed as if resolved. Collect the still-open dissents via `srde.get_unresolved()` and **surface them to a human** with the original dissent content, so the decision is made with eyes open rather than buried.

The all-strategies-fail path is a feature, not an error: SRDE's job is to auto-resolve what it *can* and to make the residue visible — never to manufacture a resolution it doesn't have.

## File Locations
```
crux/scripts/crux/srde/
├── __init__.py
└── srde.py        ← Core SRDE

crux/scripts/crux/core/
└── data_classes.py    ← DissentPoint, ResolutionAttempt, etc. (shared)
```

## Exports
```
From crux.srde:
  SelfResolvingDissentEngine, ContextResolver, PatternResolver, create_srde
```

## Resolution Order
1. **Context-aware** — uses a domain-supplied resolver to UNDERSTAND the concern
2. **Cross-reference probes** — reuses evidence from the Semantic Bridge
3. **Pattern-based** — matches known patterns (backup, validation, count, etc.)

## Running it (PEP 723 driver under `uv`, NOT bare `python3`)

SRDE's own code is mostly stdlib, **but** `import crux.srde` (and the
`crux.council` import in the example below) triggers
`crux/scripts/crux/__init__.py`, which eagerly imports the router
(`httpx`) — so bare `python3` dies with `ModuleNotFoundError: No module named
'httpx'`. Write your driver to a temp `.py` starting with the standard PEP 723
header, then run it with `uv`:

```python
# /// script
# requires-python = ">=3.10"
# dependencies = ["httpx>=0.27"]
# ///
import sys
sys.path.insert(0, "${CRUX_PLUGIN_ROOT}/scripts")

from crux.srde import create_srde
# ... your driver code
```

```bash
uv run /tmp/driver.py
```

Substitute `${CRUX_PLUGIN_ROOT}` (crux's portable plugin-root name — in Claude Code, the value of `CLAUDE_PLUGIN_ROOT`; in Codex, derived from this `SKILL.md`'s path per the Runtime compatibility note above) with
its actual value when writing the temp file; in a source checkout substitute
the checkout's `crux/` directory. Without `uv` this fails at the shell
(`command not found: uv`, exit 127) — install uv (https://docs.astral.sh/uv/).
See `council/SKILL.md` and `docs/AGENTS.md` §10.A ("Async-first rule") for the
canonical reference.

## Usage

```python
import asyncio
from crux.council import create_async_council
from crux.srde import create_srde

async def decide_with_resolution(question, context):
    # 1. Get council decision
    council = create_async_council()
    decision = await council.deliberate(
        prompt=f"Question: {question}\n\nContext: {context}",
    )

    # 2. Resolve any dissents. A CouncilDeliberation exposes `dissent_count` plus
    #    each CouncilVote's `dissenting_points` (a List[str]) — there is NO
    #    `decision.dissent_points`.
    if decision.dissent_count:
        srde = create_srde()

        for v in decision.votes:
            for i, dissent in enumerate(v.dissenting_points):   # `dissent` is a str
                resolution = srde.attempt_resolution(
                    dissent_id=f"{v.provider}-{i}",
                    dissent_content=dissent,
                    context=None,  # optional Dict[str, Any]
                )
                if resolution.status.value == "resolved":
                    print(f"resolved: {dissent}")

        # Batch alternative. `attempt_batch_resolution` takes DissentPoint
        # objects, not the raw strings a vote carries, so build them first:
        #   from crux.core.data_classes import DissentPoint, DissentSeverity
        #   dissents = [
        #       DissentPoint(id=f"{v.provider}-{i}", content=d, raised_by=v.provider,
        #                    raised_loop=1, severity=DissentSeverity.MEDIUM)
        #       for v in decision.votes for i, d in enumerate(v.dissenting_points)
        #   ]
        #   results, resolved_count, total = srde.attempt_batch_resolution(dissents)

        # All-strategies-fail path: surface the residue to a human — never
        # silently proceed as if these were resolved.
        unresolved = srde.get_unresolved()  # authoritative list of still-open dissents
        if unresolved:
            print(f"{len(unresolved)} dissent(s) UNRESOLVED — escalate to a human:")
            for d in unresolved:
                print(f"  - {d.content}")

    return decision

asyncio.run(decide_with_resolution("...", "..."))
```

## SRDE API

```python
from crux.srde import create_srde

srde = create_srde(
    context_resolver=None,   # optional ContextResolver
    domain_context=None,     # optional Dict[str, Any]
)

# Set a context resolver after creation
srde.set_context_resolver(my_resolver)

# Add custom pattern resolvers
srde.add_pattern_resolver(
    pattern="backup",
    resolver_fn=lambda dissent_id, content, ctx: ...,
    name="backup_pattern",
)

# Provide domain context
srde.set_domain_context({"entities": [...], "constraints": [...]})

# Register probe results (for cross-referencing)
srde.register_probe_result("probe_rbac_check", probe_result)

# Resolve a single dissent
resolution = srde.attempt_resolution(
    dissent_id="d_1",
    dissent_content="Will the backup be created before changes?",
    context=None,
)

# Batch resolve
results, resolved_count, total = srde.attempt_batch_resolution(dissents)

# Check stats
stats = srde.get_stats()
unresolved = srde.get_unresolved()
```

## Custom Context Resolver (for your domain)

```python
from crux.srde import ContextResolver
from crux.core import ResolutionAttempt

class MyDomainResolver(ContextResolver):
    def can_resolve(self, dissent_content: str) -> bool:
        return "rbac" in dissent_content.lower()

    def resolve(self, dissent_id, dissent_content, context=None) -> ResolutionAttempt:
        ...  # build and return a ResolutionAttempt
```

## Resolution Statuses
- **RESOLVED** — proceed
- **PARTIALLY_RESOLVED** — run targeted probe
- **CANNOT_RESOLVE** — escalate or synthesize probe
- **NEEDS_SANDBOX** / **NEEDS_HUMAN** — explicit escalation paths
