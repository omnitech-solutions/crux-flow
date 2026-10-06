---
name: refresh-research-sources
description: "Check research sources for upstream changes, preserve updated captures, and flag affected synthesis without rewriting it."
metadata:
  tags: "research, refresh, sources"
  bundles: "crux-docs"
  risk_level: "low"
  triggers: "refresh sources | update from source | check for updates | find the old ones"
  routing_note: "Re-fetches stale sources; marks synthesis pages."
---

# Refresh Research Sources

<!-- BEGIN GENERATED: runtime-compat -->
## Runtime compatibility

This skill is portable across Claude Code, Codex, and OpenCode. This section overrides platform-specific labels below.

- Before running a command that uses `CRUX_PLUGIN_ROOT`, set it to the installed plugin root. In Claude Code, use the value of `CLAUDE_PLUGIN_ROOT`. In Codex and OpenCode, derive it from the absolute path of this selected `SKILL.md`: the plugin root is the parent of its `skills/` directory. In a source checkout, use the checkout `crux/` directory.
- For project-local skills, use `.claude/skills` in Claude Code, `.agents/skills` in Codex, and `.opencode/skills` in OpenCode, which also reads the singular `.opencode/skill`. Set `CRUX_LOCAL_SKILLS_DIR` to that path before following any command below that uses it.
- Translate Claude Code tool labels such as `Agent`, `Read`, `Write`, `Bash`, `WebSearch`, and `WebFetch` to the matching capability in the current session. Codex names its own capabilities; OpenCode uses the lowercase forms `subagent`, `read`, `edit`, `shell`, `websearch`, and `webfetch`, where `edit` covers both `Edit` and `Write`. Do not attempt to invoke the Claude Code labels as literal commands on another host.
- Install the generated role agents before delegating: `install-codex-agents` in Codex, `install-opencode-agents` in OpenCode. Codex names them `crux_architect`, `crux_brainstormer`, `crux_commander`, `crux_dev_lead`, `crux_developer`, `crux_historian`, `crux_librarian`, `crux_night_gardener`, `crux_reviewer`, and `crux_wayfinder`; OpenCode uses the bare role names `architect`, `brainstormer`, `commander`, `dev-lead`, `developer`, `historian`, `librarian`, `night-gardener`, `reviewer`, and `wayfinder`. If a required role or capability is unavailable, report that truthfully instead of claiming it ran.
- Argument placeholders such as `$adr` and `$book` bind only in Claude Code. On a host without argument binding they are unset — take the value from the user's phrase. The "Fields OpenCode ignores" section of `OPENCODE_GUIDE.md` names the invocation-control fields OpenCode ignores.
<!-- END GENERATED: runtime-compat -->


## Overview

Re-checks the URLs behind `docs/research/sources/*.md` against their upstream to detect changes. Pairs with `ingest-research`: ingest handles first-time captures; refresh handles every subsequent verification of the same URL.

Each source page carries a `last_source_check` date in its frontmatter. The skill walks `docs/research/sources/`, finds pages whose check is older than the threshold (`research.refresh_interval_days` from `docs/manifest.yml`), and re-fetches in bulk. When upstream content has changed meaningfully, a new dated raw capture is added under `docs/research/raw/<today>/<slug>/`. The original capture is **never modified** — it stays as audit history under its original date. The source page's `raw_path` advances to the new capture; the prior path moves into `previous_captures:`.

Synthesis pages that cite an updated source get a `> [source updated YYYY-MM-DD: <slug>]` blockquote at the top of the page body. **They are not auto-rewritten.** Reconciliation is a separate user-gated step handled by `refresh-research-synthesis`.

## When to use

- User says "refresh sources", "update from source", "check for updates", "find the old ones".
- Proactively, when the oldest `last_source_check` under `docs/research/sources/` exceeds `research.refresh_interval_days` (default 90).
- After a major event in a domain the research covers, when the user asks to recheck that domain.

Do **not** use this skill for:
- First-time ingestion — use `ingest-research`.
- Sources with `source_url: null` — there is nothing upstream to refresh against. Skip silently.
- Sources with `static: true` — opted out by the user. Skip silently.
- Reconciling synthesis prose — use `refresh-research-synthesis`.
- Refreshing code docs — use `extract-code-docs` / `verify-code-docs`.

## Schema this skill depends on

Source page frontmatter (per the `ingest-research` required-frontmatter contract) must include:

```yaml
captured_at: 2026-05-19          # date of the current raw capture
last_source_check: 2026-05-19    # date of the most recent verification (success or fail)
raw_path: research/raw/2026-05-19/<slug>/  # current capture (moving pointer)
previous_captures: []            # ordered oldest-first; current path is NOT in this list
static: false                    # if true, this skill skips the source entirely
```

If a source page lacks `last_source_check` (legacy from before this skill existed), treat it as equal to `captured_at`. If `static` is absent, treat it as `false`.

This skill **also maintains** two other root-level files under `docs/research/`:
- `docs/research/updates.md` — content-change journal. One entry per substantive upstream change. Distinct from `docs/log.md` (operational journal). Created lazily if absent.
- `docs/research/sources.md` — tabular registry of all ingested sources. In-place column updates on every check (`last_source_check`) and on every persist (`last_update`, `raw_path`). Never appends new rows here (that's `ingest-research`'s job).

## The pipeline

Execute in order. Never reorder, never skip.

### 1. Build the stale list

- Walk `docs/research/sources/*.md`. Read each frontmatter.
- Filter to pages where: `source_url` is not null **AND** `static` is not `true` **AND** `last_source_check` is older than the threshold. Default threshold is `research.refresh_interval_days` from `docs/manifest.yml` (90 if unset). The user may override with "everything", "older than 30 days", a specific date, or a comma-separated slug list.
- Sort oldest-first by `last_source_check`.
- Print the stale list: slug, source_url (truncated), last_source_check, days stale. One line per source.
- After the stale list, print a one-line summary of skipped sources: `Skipped: N static, M URL-less.`

If there are many source pages and reading each one inflates context, do the frontmatter walk with `search_files` (or a Bash find-grep) instead of opening every file: e.g. `grep -rEl 'static:\s*true' docs/research/sources/` to set aside opted-out sources, then `grep -rE 'last_source_check:|source_url:|static:' docs/research/sources/` to pull just the frontmatter fields you filter on. Read full bodies only for the pages that survive the filter — the stale list only needs the frontmatter, not the prose.

### 2. Confirm scope

Ask the user once: "Refresh all N, a subset, or skip?" Accept:
- `all` → process every entry in the stale list.
- `first M` → process the M oldest.
- Comma-separated slugs → just those.
- `skip` → exit cleanly.

Do **not** ask per-source.

### 3. For each source in scope

Run 3a–3f in order. Pace WebFetch calls; if a domain rate-limits, back off rather than barrel through.

#### 3a. Fetch and audit-render (comparison pass — no save)

Invoke `web-to-markdown` **without** `--output-dir` so nothing is written yet:

```bash
${CRUX_PLUGIN_ROOT}/scripts/web-to-markdown.py <source_url>
```

Capture stdout (new audited markdown) and stderr (JSON metadata) in memory.

If exit code is non-zero (network error, blocked IP, redirect loop, size cap exceeded), or exit code 2 (`thin_content: true`), or `extraction_success: false`:
- Append a blockquote at the top of the source-page body: `> [refresh failed YYYY-MM-DD: <one-line reason>]`. Do not remove prior failure markers — they're history.
- Set `last_source_check: <today>` (we did check; it failed). Leave everything else untouched.
- Skip to next source. No retry in this run.

If `final_url` differs from `source_url`, note the redirect in the page's `## Update history` even if content is unchanged.

#### 3b. Audit-render review

The script's stdout *is* the new audited rendition. Treat it as a draft: scan for boilerplate that survived extraction, trim or mark `> [paraphrased]`, declare `## Capture gaps` if the new render is missing material the old one had. Hold the result in memory; nothing is written to disk yet.

#### 3c. Compare

Read the existing `docs/research/sources/<slug>.md` body. Compare to the new rendition.

**Ignore:** timestamps, view counts, "Last updated X days ago" lines, reordered unchanging sections, ad/nav/footer chrome, whitespace-only diffs, cookie banners, subscribe overlays.

**Treat as meaningful:** new or removed sections; changed claims, numbers, dates in substantive paragraphs; added or removed figures, tables, code blocks; author/attribution/title changes; capture gaps resolved or newly introduced.

If no meaningful changes → 3d. Otherwise → 3e.

#### 3d. Unchanged — bump the check date only

- Update `last_source_check: <today>` in frontmatter. Nothing else changes.
- Append one line to the page's `## Update history` section (create the section if absent): `- <today>: checked, no changes.`
- **Update `docs/research/sources.md`**: in-place edit the source's row to set its `last_source_check` column to today. No other columns change. Row position unchanged.
- No new raw capture is written.
- No log entry per source — the batch entry in step 4 covers it.

**Always bump `last_source_check`**, even when unchanged. The check is itself the record.

#### 3e. Changed — persist the update

> **Double-fetch TOCTOU gap.** The default flow fetches twice: once in 3a (comparison, no save) and again here to persist. Between the two fetches the upstream can change, so the persisted capture may not be byte-identical to the rendition you diffed in 3c — you could persist (and summarize) a version you never actually compared. The window is usually small, but for fast-moving or high-stakes sources it is real.
>
> **Lower-risk alternative — save the first fetch to a temp dir, then compare-and-promote (no second fetch).** Instead of fetching without `--output-dir` in 3a, point that *first* fetch at a temp dir: `${CRUX_PLUGIN_ROOT}/scripts/web-to-markdown.py <source_url> --output-dir "$(mktemp -d)/<slug>"`. Run the 3c comparison against that temp capture; if it's a meaningful change, **move/promote the temp capture into `docs/research/raw/<today>/<slug>/`** rather than re-fetching. The persisted bytes are then exactly what you diffed — the gap is closed. If unchanged, discard the temp dir. Prefer this path when the source is volatile or the change matters; the default re-fetch is fine for stable sources.

- **Re-invoke `web-to-markdown` with `--output-dir docs/research/raw/<today>/<slug>/`** to persist the new dated capture (default path). Use *this* stdout as the new body, not the cached comparison-pass output. **If you took the temp-dir path above, promote the temp capture instead of re-fetching, and use the temp capture's rendition as the new body** — that is the version you compared.
- In the source page's frontmatter:
  - Move the current `raw_path` to the **end** of `previous_captures:` (creating the list if absent).
  - Set `raw_path: research/raw/<today>/<slug>/`.
  - Set `captured_at: <today>` and `last_source_check: <today>`.
  - If the upstream's own "published" or "last updated" indicator changed, update `source_date` accordingly.
  - If `title` or `author` changed upstream, update them. Do **not** change `slug` — the slug is the stable identifier.
- Replace the source page body with the new audited rendition.
- Append to `## Update history` (at the bottom of the body): `- <today>: <one-line summary of what changed>. Prior capture: previous_captures[-1].`
- Append an entry to `docs/research/updates.md` at the top (newest-first), immediately after the file header:

  ```
  ## [YYYY-MM-DD] <slug>

  Changes: <one-line summary of substantive changes>.

  Prior: `<previous raw_path>` → Current: `<new raw_path>`
  Source page: [[research/sources/<slug>]]
  Synthesis pages flagged: <count, filled in after step 3f>
  ```

  Create `docs/research/updates.md` if absent (file header: `# Updates\n\n_Substantive upstream changes detected by refresh-research-sources. Entries newest-first._`).

- **Update `docs/research/sources.md`** in-place: set the source's row to `last_source_check → today`, `last_update → today`, `raw_path → new dated folder`, `title → updated if upstream renamed`. All other columns unchanged. Row position unchanged.

#### 3f. Flag synthesis pages

Find every page under `docs/research/<category>/*.md` (excluding `docs/research/sources/`) whose frontmatter `sources:` list contains this slug. For each:

- Insert a `> [source updated YYYY-MM-DD: <slug>]` blockquote **at the top of the page body**, immediately after the closing frontmatter `---` and any existing top-of-body markers. Do not edit synthesis prose. Do not modify claims inline.
- Do not delete prior `[source updated]` markers from earlier refreshes — they accumulate until `refresh-research-synthesis` clears them.
- Do **not** bump the synthesis page's `last_reviewed`. The marker IS the staleness signal until reconciliation.

### 4. Update indexes and log

- `docs/research/index.md`: refresh affected source rows' date suffix; update `_Last updated:_` to today. Counts don't change (no pages added or removed).
- `docs/index.md`: bump `_Last updated:_` only; concern counts are unchanged.
- `docs/log.md` — one entry for the whole batch (newest-first, at top):

  ```
  ## [YYYY-MM-DD] refresh | sources batch (N checked, M updated, K failed)
  ```

  Body, in three short lists:
  - **Updated** (M): slug — one-line change summary.
  - **Unchanged** (N − M − K): slugs only.
  - **Failed** (K): slug — failure reason.

### 5. Hand-off

Report to the user, in this order:
1. Total checked / updated / unchanged / failed counts.
2. List of updated sources with their change summaries.
3. List of failed sources with reasons and a recommendation.
4. List of synthesis pages that received `[source updated]` flags, and the count per page.
5. Offer to run `refresh-research-synthesis` — or to walk through flagged pages manually.

## Verification checklist

- [ ] Every source in scope was actually fetched (no `last_source_check` bumped without a fetch).
- [ ] `last_source_check` was bumped on every checked source — changed, unchanged, OR failed.
- [ ] No old `docs/research/raw/<date>/<slug>/` folder was modified.
- [ ] For every changed source, prior `raw_path` was moved into `previous_captures:` before the new path was set.
- [ ] For every changed source, an entry was appended to the top of `docs/research/updates.md`.
- [ ] For every changed source, `docs/research/sources.md` row updates landed in-place (no row reorder, no row deletion).
- [ ] `> [source updated]` markers were inserted on every synthesis page whose `sources:` includes a changed slug — and ONLY at the top of the body, not inline.
- [ ] No synthesis page's `last_reviewed` was bumped.
- [ ] Sources with `static: true` were skipped entirely (not checked, not bumped).
- [ ] Sources with `source_url: null` were skipped entirely.
- [ ] One batch entry was appended to the top of `docs/log.md` with op `refresh`.
- [ ] `docs/research/index.md` `_Last updated:_` was bumped.

## Red flags — STOP and reconsider

- About to write a new raw capture before confirming the fetch returned real content (not a JS stub or login wall).
- About to overwrite or modify an old `docs/research/raw/<date>/<slug>/` folder. **Never.** Old captures are immutable.
- About to auto-rewrite a synthesis page based on a source update. **Never.** Flag at the top of the body; don't edit content.
- About to change the slug because the source's title changed. **Never.** Slug is the stable identifier; `[[research/sources/<slug>]]` links depend on it.
- About to skip the `## Update history` line on a changed source. The history is the audit trail of upstream change.
- About to set `last_source_check` without actually attempting the fetch. Always fetch; record the real result.
- About to refresh more than ~10 sources without showing the user the stale list first.
- About to delete a `[refresh failed]` marker because today's fetch succeeded. Don't — leave them as history.
- About to insert a `[source updated]` marker on a source page. Markers go only on synthesis pages.
- About to insert a `[source updated]` marker inline in synthesis prose. Markers go at the top of the body, in a single block.

## Rationalization table

| Excuse | Reality |
|--------|---------|
| "The diff is tiny — no need to recapture." | Tiny diffs in substantive content are exactly what staleness is for. If unsure, recapture; a new dated folder is cheap. |
| "I'll just merge the new content into the old raw file." | `docs/research/raw/` is append-only via new dated folders. Never modify existing captures. |
| "The synthesis page only mentions this source in passing — skip the flag." | The flag is cheap and `refresh-research-synthesis` relies on completeness. Flag every page in `sources:`. |
| "404 means the source is gone — delete the page." | 404 means we couldn't reach it today. Mark failed, ask user. |
| "The diff is clear — I'll auto-fix the synthesis claim too." | Even a clear textual diff can shift framing. Flag, don't rewrite. |
| "This source has 12 synthesis citations — I should refresh all of them in one pass." | Two separate operations. Source refresh = automated and bulk. Synthesis reconciliation = user-gated. |
| "I'll batch the log entry per source." | One batch entry per run. Per-source entries pollute `docs/log.md`. |
| "The user said 'go' — I shouldn't bother them with the stale list." | "Go" applies after they've seen the scope. Show the list, get scope, then go. |
| "Slug from the new title is cleaner." | Slug is immutable. Renaming breaks every `[[research/sources/<slug>]]` link. |
| "Source has `static: true` but I just want to double-check." | `static: true` is the user's explicit opt-out. Honor it. If the user wants to recheck, they'll flip the flag. |

## Common mistakes

- **Modifying an old `docs/research/raw/<date>/<slug>/` folder** instead of creating a new dated one. Old folders are forever.
- **Forgetting to move the prior `raw_path` into `previous_captures`** — orphans the old folder from the audit chain.
- **Comparing against the raw HTML** instead of the audited markdown. HTML diffs are 90% noise.
- **Treating a 200-OK with a thin body as success.** Inspect the body; many sites return 200 with a "please enable JavaScript" stub.
- **Inserting `[source updated]` markers on the source page itself.** They go only on synthesis pages.
- **Inserting the marker inline next to a claim.** Marker goes at the top of the body — synthesis reconciliation walks the markers separately from prose.
- **Removing `## Capture gaps` from the prior body without verifying the gap was actually filled.** If the new render still has the gap, keep it.
- **Re-saving images that haven't changed.** Compare filenames and sizes; only save changed/new images into the new dated folder.
- **Bumping `last_source_check` on a `static: true` source because "we checked it anyway".** Static sources are not in scope. Don't touch them.
- **Skipping the `docs/research/sources.md` row update**. Audit will catch the drift, but the registry is supposed to be live.
