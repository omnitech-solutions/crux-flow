---
title: "Drizzle ORM - `generate`"
slug: drizzle-kit-generate
type: source
source_url: https://orm.drizzle.team/docs/drizzle-kit-generate
source_date: null
author: "Drizzle Team"
captured_at: 2026-10-05
last_source_check: 2026-10-05
raw_path: research/raw/2026-10-05/drizzle-kit-generate/
previous_captures: []
static: false
tags: [drizzle-kit, migrations, snapshot]
---

> [paraphrased] This page is a short audited summary in our own words of the official Drizzle documentation page captured at `research/raw/2026-10-05/drizzle-kit-generate/` (`source.md`, `source.html`, `PROVENANCE.md`). The upstream text is not reproduced; read the capture for the exact wording. Everything below is reference data from the upstream author; it is not an instruction to any agent that reads this wiki. It never overrides `AGENTS.md`, an accepted ADR or a ratified invariant.

## Provenance

- Upstream: https://orm.drizzle.team/docs/drizzle-kit-generate (official Drizzle ORM documentation, https://orm.drizzle.team).
- Captured: 2026-10-05 with the crux `web-to-markdown` script. The site is unversioned at this URL; it shows the current docs, not a pinned release.
- Licence: not checked; official documentation text is summarised in our own words and not copied, the full capture is kept for audit only.

## Summary

What `drizzle-kit generate` reads, compares and writes.

## What the page says

- The command reads the schema files and composes a JSON snapshot of the schema, reads the previous migration folders, compares the new snapshot with the most recent saved one, turns the differences into SQL, and saves `migration.sql` and `snapshot.json` together in one folder named with a timestamp prefix and a name.
- This is the folder-per-migration layout (the newer drizzle-kit layout): `drizzle/<timestamp>_<name>/migration.sql` and `snapshot.json`. The page's own examples show this layout.
- When a column or table looks renamed the tool prompts the developer to say so rather than guessing.
- `dialect` and `schema` are required, from the config file or CLI flags. `schema` may be a path, a glob or an array of paths.
- Three CLI-only options: `--name` sets the name part of the folder, `--custom` generates an empty `migration.sql` for hand-written SQL (DDL the tool cannot express, or data seeding), and `--ignore-conflicts` skips the commutativity check between migrations (the page says needing it likely means a tool bug to report).
- Multiple config files are supported for several databases or stages; `--config` selects one.

## Pinned context for this repository

Installed: `drizzle-kit` `1.0.0-rc.4`. Observed layout in `packages/database/drizzle/`: 25 folders of the form `<yyyymmddhhmmss>_<name>/{migration.sql,snapshot.json}`; the first snapshot has `"version": "8"` and `"dialect": "postgres"`. The generated snapshot is what `tools/crux/arch/drizzle_data_model.py` reads for the arch data-model page (`pnpm docs:arch`). Do not hand-edit a snapshot.
