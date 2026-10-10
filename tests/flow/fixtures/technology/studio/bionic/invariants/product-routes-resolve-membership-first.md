---
id: INV-0004
class: behavior
provenance: recovered
ratification: observed
verification:
  last_result: pass
related_adrs: [ADR-0004]
related_briefs: []
checks: [product-routes-resolve-membership-first.md]
---

# INV-0004 — product-routes-resolve-membership-first

> **observed** — a recovered candidate, not a ratified invariant. Only the owner
> ratifies or rejects it, via `transition-invariant`.

**Intent:** Every product route under `/t/:tenantSlug/p/:productId/*` resolves an authenticated tenant membership, then installation, permission, manifest route and loader, before any domain work; a missing installation or permission returns 404 without loading product code.

**Why:** [[adrs/ADR-0004-build-products-as-verticals-inside-a-modular-monol]].
