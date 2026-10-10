---
id: INV-0006
class: contract
provenance: recovered
ratification: ratified
verification:
  last_result: fail
related_adrs: [ADR-0007]
related_briefs: []
checks: [products-never-branch-on-provider-names.md]
---

# INV-0006 — products-never-branch-on-provider-names

> **observed** — a recovered candidate, not a ratified invariant. Only the owner
> ratifies or rejects it, via `transition-invariant`.

**Intent:** Product code requests AI work by profile or capability through `AiExecutionGateway` and never branches on a provider or model name.

**Why:** [[adrs/ADR-0007-route-ai-work-through-aiexecutiongateway-profiles]].
