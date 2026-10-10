---
id: INV-0003
class: shape
provenance: recovered
ratification: ratified
verification:
  last_result: pass
related_adrs: [ADR-0005]
related_briefs: []
checks: [schema-files-and-migrations-agree.md]
---

# INV-0003 — schema-files-and-migrations-agree

> **observed** — a recovered candidate, not a ratified invariant. Only the owner
> ratifies or rejects it, via `transition-invariant`.

**Intent:** Schema files and the single Drizzle migration stream agree: a fresh database reaches the current schema, a second migration run changes nothing, and every declared table, column, foreign key and policy exists after migration.

**Why:** [[adrs/ADR-0005-isolate-tenants-in-one-postgresql-cluster-with-own]].
