---
id: INV-0009
class: contract
provenance: recovered
ratification: ratified
verification:
  last_result: pass
related_adrs: [ADR-0003, ADR-0004, ADR-0007]
related_briefs: []
checks: [package-boundaries-hold.md]
---

# INV-0009 — package-boundaries-hold

> **observed** — a recovered candidate, not a ratified invariant. Only the owner
> ratifies or rejects it, via `transition-invariant`.

**Intent:** Every workspace package declares exactly the workspace packages it imports, imports them only through their `exports` map, never in a forbidden direction, and no library package goes unimported.

**Why:** [[adrs/ADR-0003-keep-package-boundaries-narrow-with-one-public-ent]], [[adrs/ADR-0004-build-products-as-verticals-inside-a-modular-monol]], [[adrs/ADR-0007-route-ai-work-through-aiexecutiongateway-profiles]]. The boundary map is in [[research/concepts/architecture-overview]].
