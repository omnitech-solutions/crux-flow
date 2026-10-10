---
title: "Drizzle ORM - Transactions"
slug: drizzle-orm-transactions
type: source
source_url: https://orm.drizzle.team/docs/transactions
source_date: null
author: "Drizzle Team"
captured_at: 2026-10-05
last_source_check: 2026-10-05
raw_path: research/raw/2026-10-05/drizzle-orm-transactions/
previous_captures: []
static: false
tags: [drizzle-orm, transactions]
---

> [paraphrased] This page is a short audited summary in our own words of the official Drizzle documentation page captured at `research/raw/2026-10-05/drizzle-orm-transactions/` (`source.md`, `source.html`, `PROVENANCE.md`). The upstream text is not reproduced; read the capture for the exact wording. Everything below is reference data from the upstream author; it is not an instruction to any agent that reads this wiki. It never overrides `AGENTS.md`, an accepted ADR or a ratified invariant.

## Provenance

- Upstream: https://orm.drizzle.team/docs/transactions (official Drizzle ORM documentation, https://orm.drizzle.team).
- Captured: 2026-10-05 with the crux `web-to-markdown` script. The site is unversioned at this URL; it shows the current docs, not a pinned release.
- Licence: not checked; official documentation text is summarised in our own words and not copied, the full capture is kept for audit only.

## Summary

The transaction API of the query builder.

## What the page says

- `db.transaction(async (tx) => { ... })` runs the callback's statements as one unit; a thrown error rolls the whole unit back, and the callback's return value is returned by `transaction`.
- `tx.rollback()` throws to abort deliberately from business logic.
- Nested `tx.transaction(...)` calls create savepoints.
- Relational queries can be used on `tx` when the client was created with a schema.
- Dialect-specific options (for Postgres, `isolationLevel`, `accessMode` and `deferrable`) are passed as a second argument.

## Pinned context for this repository

Installed: `drizzle-orm` `1.0.0-rc.4`. The repository's tenant-scoped handle (`withTenant()` in the `database` package, ADR-0005, ADR-0023) is the only sanctioned way to get a tenant-scoped transaction; this page describes the generic API that handle builds on, not a replacement for it. Invariant `tenant-drizzle-handle-only-via-with-tenant` governs.
