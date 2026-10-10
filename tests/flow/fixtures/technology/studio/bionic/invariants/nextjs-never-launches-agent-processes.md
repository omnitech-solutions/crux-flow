---
id: INV-0005
class: contract
provenance: recovered
ratification: ratified
verification:
  last_result: pass
related_adrs: [ADR-0007]
related_briefs: []
checks: [nextjs-never-launches-agent-processes.md]
---

# INV-0005 — nextjs-never-launches-agent-processes

> **observed** — a recovered candidate, not a ratified invariant. Only the owner
> ratifies or rejects it, via `transition-invariant`.

**Intent:** Next.js (`apps/web`) may create, inspect, cancel and resume agent jobs but never launches an agent process; Codex and Claude Code runtimes run only in `apps/agent-worker`.

**Why:** [[adrs/ADR-0007-route-ai-work-through-aiexecutiongateway-profiles]].
