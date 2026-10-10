---
title: "Drizzle ORM - `migrate`"
slug: drizzle-kit-migrate
type: source
source_url: https://orm.drizzle.team/docs/drizzle-kit-migrate
source_date: null
author: "Drizzle Team"
captured_at: 2026-10-05
last_source_check: 2026-10-05
raw_path: research/raw/2026-10-05/drizzle-kit-migrate/
previous_captures: []
static: false
tags: [drizzle-kit, migrations]
---

> [paraphrased] This page is a short audited summary in our own words of the official Drizzle documentation page captured at `research/raw/2026-10-05/drizzle-kit-migrate/` (`source.md`, `source.html`, `PROVENANCE.md`). The upstream text is not reproduced; read the capture for the exact wording. Everything below is reference data from the upstream author; it is not an instruction to any agent that reads this wiki. It never overrides `AGENTS.md`, an accepted ADR or a ratified invariant.

## Provenance

- Upstream: https://orm.drizzle.team/docs/drizzle-kit-migrate (official Drizzle ORM documentation, https://orm.drizzle.team).
- Captured: 2026-10-05 with the crux `web-to-markdown` script. The site is unversioned at this URL; it shows the current docs, not a pinned release.
- Licence: not checked; official documentation text is summarised in our own words and not copied, the full capture is kept for audit only.

## Summary

What `drizzle-kit migrate` does with the generated folders.

## What the page says

- The command reads the migration folders, fetches the applied-migrations history from the database, decides which migrations are new, runs them, and records each success in the log table.
- The log table is named `__drizzle_migrations` in the `drizzle` schema by default; both can be changed under `migrations` in the config. (An older journal-file mention appears only in an extended config example where the table is renamed `journal`.)
- It needs `dialect` and database credentials from config or flags, and supports multiple config files.

## Pinned context for this repository

Installed: `drizzle-kit` `1.0.0-rc.4`. `packages/database/drizzle.config.ts` sets `migrations: { schema: "drizzle", table: "__drizzle_migrations" }` explicitly and `schemaFilter` to `platform`, `ai`, `presentation`, `interview`, `practice`. Applying migrations in this repo goes through the project's `db:migrate` script, not the CLI command described here.
