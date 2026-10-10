---
id: INV-0002
class: contract
provenance: recovered
ratification: observed
verification:
  last_result: pass
related_adrs: [ADR-0005, ADR-0023]
related_briefs: []
checks: [tenant-drizzle-handle-only-via-with-tenant.md]
---

# INV-0002 — tenant-drizzle-handle-only-via-with-tenant

> **observed** — a recovered candidate, not a ratified invariant. Only the owner
> ratifies or rejects it, via `transition-invariant`.

**Intent:** Tenant context is set only transaction-locally, by the `database` package; no tenant-scoped handle exists outside `withTenant()` and `tenantTransaction`, and every path runs the same database-role check ([[adrs/ADR-0023-use-the-query-builder-by-default-and-check-the-dat]]).

**Why:** [[adrs/ADR-0005-isolate-tenants-in-one-postgresql-cluster-with-own]] (Accepted 2026-10-05) and [[adrs/ADR-0023-use-the-query-builder-by-default-and-check-the-dat]].
