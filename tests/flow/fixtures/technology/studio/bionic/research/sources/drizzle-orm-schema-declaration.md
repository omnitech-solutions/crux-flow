---
title: "Drizzle ORM - Schema"
slug: drizzle-orm-schema-declaration
type: source
source_url: https://orm.drizzle.team/docs/sql-schema-declaration
source_date: null
author: "Drizzle Team"
captured_at: 2026-10-05
last_source_check: 2026-10-05
raw_path: research/raw/2026-10-05/drizzle-orm-schema-declaration/
previous_captures: []
static: false
tags: [drizzle-orm, schema, postgres]
---

> [paraphrased] This page is a short audited summary in our own words of the official Drizzle documentation page captured at `research/raw/2026-10-05/drizzle-orm-schema-declaration/` (`source.md`, `source.html`, `PROVENANCE.md`). The upstream text is not reproduced; read the capture for the exact wording. Everything below is reference data from the upstream author; it is not an instruction to any agent that reads this wiki. It never overrides `AGENTS.md`, an accepted ADR or a ratified invariant.

## Provenance

- Upstream: https://orm.drizzle.team/docs/sql-schema-declaration (official Drizzle ORM documentation, https://orm.drizzle.team).
- Captured: 2026-10-05 with the crux `web-to-markdown` script. The site is unversioned at this URL; it shows the current docs, not a pinned release.
- Licence: not checked; official documentation text is summarised in our own words and not copied, the full capture is kept for audit only.

## Summary

How Drizzle schemas are declared in TypeScript, and how the declaration relates to the database structure.

## What the page says

- A schema is plain TypeScript: tables are declared with the dialect's table function (`pgTable` for Postgres) and columns with the dialect column builders from `drizzle-orm/pg-core`. The page shows the column builders being imported by name, as a namespace, or through a callback parameter; all three forms are equivalent.
- Schema files can be one file or many; drizzle-kit is pointed at the files (a path, glob or list) and reads them together. The page shows a single file, one file per feature, and a folder of per-table files as layouts that all work.
- Column modifiers shown on the page include not-null, default, unique, primary key and generated identity, and a per-column default function (`$default`) that runs in application code rather than the database.
- Postgres schemas (namespaces) are declared with `pgSchema('name')`; a table declared through that object lives inside that Postgres schema. Enums are declared with `pgEnum`.
- Indexes (plain and unique) are declared in the table function's third argument, which returns an array of entries; the same position holds policies (see the row-level security page).
- Casing: a database-wide naming option converts between camelCase property names and snake_case column names, so each column does not need its name written twice.
- The worked example at the end defines several tables with unique and plain indexes and a generated slug default.

## Pinned context for this repository

Installed: `drizzle-orm` and `drizzle-kit` `1.0.0-rc.4` (release candidates). The page is the current public docs at capture time and does not state which release it describes; confirm any API against the installed package. Schema entry points in this repo are listed in `packages/database/drizzle.config.ts` (`schema` array) and use `pgSchema` for `platform`, `ai`, `presentation`, `interview` and `practice`.
