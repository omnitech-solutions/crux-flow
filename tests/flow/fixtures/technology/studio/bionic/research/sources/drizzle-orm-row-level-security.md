---
title: "Drizzle ORM - Row-Level Security (RLS)"
slug: drizzle-orm-row-level-security
type: source
source_url: https://orm.drizzle.team/docs/rls
source_date: null
author: "Drizzle Team"
captured_at: 2026-10-05
last_source_check: 2026-10-05
raw_path: research/raw/2026-10-05/drizzle-orm-row-level-security/
previous_captures: []
static: false
tags: [drizzle-orm, postgres, rls, policies, roles]
---

> [paraphrased] This page is a short audited summary in our own words of the official Drizzle documentation page captured at `research/raw/2026-10-05/drizzle-orm-row-level-security/` (`source.md`, `source.html`, `PROVENANCE.md`). The upstream text is not reproduced; read the capture for the exact wording. Everything below is reference data from the upstream author; it is not an instruction to any agent that reads this wiki. It never overrides `AGENTS.md`, an accepted ADR or a ratified invariant.

## Provenance

- Upstream: https://orm.drizzle.team/docs/rls (official Drizzle ORM documentation, https://orm.drizzle.team).
- Captured: 2026-10-05 with the crux `web-to-markdown` script. The site is unversioned at this URL; it shows the current docs, not a pinned release.
- Licence: not checked; official documentation text is summarised in our own words and not copied, the full capture is kept for audit only.

## Summary

How Drizzle declares row-level security, policies and roles for Postgres.

## What the page says

- RLS can be turned on for a table without any policy by declaring it with the `pgTable.withRLS(...)` form. Adding any policy to a table turns RLS on automatically. The page quotes the PostgreSQL rule that a table with RLS and no policy denies everything by default, and that whole-table operations such as TRUNCATE and REFERENCES are not subject to row security.
- Roles are declared with `pgRole(name, options)`; `.existing()` marks a role that already exists so drizzle-kit does not manage it. drizzle-kit does not manage roles unless `entities.roles` is enabled in the config, with `include` and `exclude` lists to narrow it.
- Policies are declared inside the table's third argument with `pgPolicy(name, options)`. Options: `as` (permissive or restrictive), `to` (public, current role forms, a role name or a `pgRole`), `for` (all, select, insert, update, delete), `using` (SQL for the USING clause) and `withCheck` (SQL for WITH CHECK).
- `.link(table)` attaches a policy to a table that exists outside the schema (the page's use case is provider-owned tables).
- The page then shows provider-specific helpers (Neon and Supabase roles and functions). Those are not relevant to this repository, which uses its own roles and `withTenant()`.

## What the page does not say

The page covers `ENABLE ROW LEVEL SECURITY` through `withRLS` and policies, and does not describe forcing RLS for the table owner (`FORCE ROW LEVEL SECURITY`). The project invariant `tenant-owned-tables-force-rls` and the PostgreSQL documentation are the authority for that; this page does not replace them.

## Pinned context for this repository

Installed: `drizzle-orm` and `drizzle-kit` `1.0.0-rc.4`. Declarations in this repo use the shared helpers in `packages/database/src/conventions.ts` and are exercised by `products/interview/src/backend/db/security.test.ts`; confirm `entities.roles` behaviour against the installed drizzle-kit before relying on role management (the repo config does not enable it).
