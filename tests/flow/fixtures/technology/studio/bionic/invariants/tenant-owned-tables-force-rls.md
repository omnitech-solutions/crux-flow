---
id: INV-0001
class: data
provenance: recovered
ratification: ratified
verification:
  last_result: pass
related_adrs: [ADR-0005]
related_briefs: []
checks: [tenant-owned-tables-force-rls.md]
---

# INV-0001 — tenant-owned-tables-force-rls

> **observed** — a recovered candidate, not a ratified invariant. Only the owner
> ratifies or rejects it, via `transition-invariant`.

**Intent:** Every tenant-owned table has `tenant_id`, forced row-level security on reads and writes, and composite `(tenant_id, id)` foreign keys.

**Why:** [[adrs/ADR-0005-isolate-tenants-in-one-postgresql-cluster-with-own]].
