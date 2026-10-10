---
id: INV-0007
class: contract
provenance: recovered
ratification: ratified
verification:
  last_result: pass
related_adrs: [ADR-0004]
related_briefs: []
checks: [product-frontend-never-imports-apps-web.md]
---

# INV-0007 — product-frontend-never-imports-apps-web

> **observed** — a recovered candidate, not a ratified invariant. Only the owner
> ratifies or rejects it, via `transition-invariant`.

**Intent:** Product frontend code depends on platform contracts and shared UI only, never on `apps/web`.

**Why:** [[adrs/ADR-0004-build-products-as-verticals-inside-a-modular-monol]].
