# crux

A Claude Code plugin that maintains a `./bionic/` tree inside a software project — **52 skills** and a **10-agent role layer** across seven concerns: **code docs**, **research wiki**, **ADRs**, **briefs**, **work journal**, **promptbooks**, **invariants**. You curate, decide, and discuss; Claude does the bookkeeping.

## Install

Two slash commands in any Claude Code session:

```
/plugin marketplace add bionic-coding/crux
/plugin install crux@crux
```

Restart the session so the skills and agents register, then say **"init docs"** to bootstrap the project's docs tree. To upgrade later: `/plugin marketplace update crux`, then re-run `/plugin install crux@crux`.

## What's in `bionic/`

```
bionic/
  AGENTS.md            Schema layer (operations doc for Claude).
  README.md            One-page human explainer.
  index.md             Catalog, sectioned by concern.
  log.md               Operational journal (append-only, newest-first).
  manifest.yml         Schema version + per-concern config.
  inbox/               Drop anything here; "process inbox" sorts and routes it.

  code/                Regenerated from source docstrings. NEVER hand-edit.
  research/            Sources + synthesis pages.
  adrs/                Architecture Decision Records (append-only history).
  briefs/              Pre-decision exploration docs referenced by ADRs.
  journal/             Work journal, one file per month.
  promptbooks/         Plan-of-prompts artifacts with run snapshots.
  invariants/          Pinned, ratified, executable statements of what must be true.
  arch/                Regenerated current-state architecture spine. NEVER hand-edit.
  observations/        Ratified records of what the code already does, each evidenced by path:line-range.
```

See `templates/AGENTS.md.tmpl` for the full operational schema that gets dropped into a target project.

## Use

Everything is natural language — no slash commands after install. Each phrase routes to a skill:

| Phrase | What happens |
|---|---|
| "init docs" / "set up docs" | Bootstrap the seven-concern `bionic/` tree (`init-docs`) |
| "start a cycle for X" / "iterate on X" | Drive a feature or fix through the full plan → ADR/verify → develop → review loop (`dev-cycle` / `iterate`) |
| "process inbox" (after dropping files/URLs in `bionic/inbox/`) | Classify and route each item (`process-inbox`) |
| "ingest this" / "file this" | Capture a source into the research wiki (`ingest-research`) |
| "propose ADR" / "accept ADR-NNNN" | Record and transition decisions (`propose-adr` / `transition-adr`) |
| "log work" / "journal this" | Append to the work journal (`log-work`) |
| "new promptbook" / "run promptbook" / "advance" | Author and execute plan-of-prompts artifacts |
| "extract code docs" / "verify code docs" | Regenerate / drift-check `bionic/code/` from source |
| "audit docs" / "what's next" | Integrity checks (`audit-docs`) / forward-looking hygiene (`cleanup-campsite`) |
| "what does X do?" / "why did we choose Y?" | Cited answers from the docs tree (`query-docs`) |

The full catalog (skills + bundles + agents) lives in `catalog/`; the 10 agents (`commander`, `architect`, `dev-lead`, `developer`, `reviewer`, `historian`, `librarian`, `brainstormer`, `night-gardener`, `wayfinder`) are dispatched for you by the cycle workflows, each fenced by a tool allowlist that structurally separates duties.

## Versioning

- The plugin follows semver. The current version is in `plugin.json`.
- `bionic/manifest.yml` carries a separate `schema_version` that bumps only on breaking layout changes to the docs tree.
- The current plugin supports tree schema 5. For schemas 2–4, use the pinned public `v3.23.2` recovery ladder on a copy before upgrading. The current `audit-docs --migrate` changes instruction files only.

## Status

See the project repository (homepage in `plugin.json`) for the user guide and the per-release `CHANGELOG.md`.
