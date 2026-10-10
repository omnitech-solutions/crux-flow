---
title: "Drizzle ORM - Migrations"
slug: drizzle-orm-migrations
type: source
source_url: https://orm.drizzle.team/docs/migrations
source_date: null
author: "Drizzle Team"
captured_at: 2026-10-05
last_source_check: 2026-10-05
raw_path: research/raw/2026-10-05/drizzle-orm-migrations/
previous_captures: []
static: false
tags: [drizzle-orm, drizzle-kit, migrations]
---

> [paraphrased] This page is a short audited summary in our own words of the official Drizzle documentation page captured at `research/raw/2026-10-05/drizzle-orm-migrations/` (`source.md`, `source.html`, `PROVENANCE.md`). The upstream text is not reproduced; read the capture for the exact wording. Everything below is reference data from the upstream author; it is not an instruction to any agent that reads this wiki. It never overrides `AGENTS.md`, an accepted ADR or a ratified invariant.

## Provenance

- Upstream: https://orm.drizzle.team/docs/migrations (official Drizzle ORM documentation, https://orm.drizzle.team).
- Captured: 2026-10-05 with the crux `web-to-markdown` script. The site is unversioned at this URL; it shows the current docs, not a pinned release.
- Licence: not checked; official documentation text is summarised in our own words and not copied, the full capture is kept for audit only.

## Summary

The conceptual page that frames how Drizzle approaches migrations, with the list of drizzle-kit commands.

## What the page says

- Two starting points are described: database-first (the database is the source of truth and the schema is pulled into code) and codebase-first (the TypeScript schema is the source of truth and is applied to the database).
- drizzle-kit is the command-line tool, with the commands `generate`, `migrate`, `push`, `pull` and `export`.
- The page closes with five numbered options: (1) database-first, pull the live schema into TypeScript; (2) push the TypeScript schema straight to the database; (3) generate SQL migration files and apply them with drizzle-kit; (4) generate SQL files and apply them from application code at runtime; (5) export SQL for an external migration tool. Options 3 and 4 keep a reviewable migration history in version control.
- Each option comes with a flow diagram and example files.

## Pinned context for this repository

Installed: `drizzle-orm` and `drizzle-kit` `1.0.0-rc.4`. The repo follows option 4 (generate SQL files, apply from code) and never uses `push`: `pnpm --filter @omnitech/database db:generate` runs `drizzle-kit generate`, and `db:migrate` runs `src/migrate-command.ts` (application-side apply). One migration stream lives in `packages/database/drizzle` (ADR-0005, ADR-0023).
