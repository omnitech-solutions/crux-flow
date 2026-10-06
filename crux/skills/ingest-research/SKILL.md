---
name: ingest-research
description: "File URLs or research inbox items into the research wiki, preserving source captures and updating synthesis and indexes."
metadata:
  tags: "research, ingest, sources"
  bundles: "crux-docs"
  risk_level: "low"
  triggers: "ingest | file this | capture this | save this | paste a URL"
  routing_note: "One source per invocation. Reads research items from the unified `docs/inbox/` (usually invoked by `process-inbox` with explicit paths)."
---

# Ingest Research

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

The atomic operation for the research concern of `crux`. Each ingest moves one source through a fixed pipeline: capture to `docs/research/raw/YYYY-MM-DD/<slug>/` → audited markdown at `docs/research/sources/<slug>.md` → synthesis updates under `docs/research/<category>/` → `docs/research/index.md` → `docs/index.md` (master rollup) → `docs/research/sources.md` registry → `docs/log.md`. Skipping any step breaks the audit guarantee that every research page traces back to an immutable raw capture.

Read `docs/AGENTS.md` once per session for surrounding architecture. This skill is the operational checklist for the research concern.

**One source at a time.** If multiple sources are in `docs/inbox/`, run the pipeline once per source — don't interleave.

Core principle: **every research page is auditable to a frozen raw capture in `docs/research/raw/`**. The whole skill exists to maintain that chain. Anything that breaks it (skipping the raw move, paraphrasing without marking, silently overwriting contradictions) is a stop-and-reconsider event.

## When to use

- User says: "ingest", "ingest research", "file this", "capture this", "save this", "add this source".
- `process-inbox` dispatches a research-classified item to this skill (explicit-path mode — see below).
- User drops one or more research-shaped files into `docs/inbox/`.
- User pastes a URL with intent to file it (not just discuss it).
- A `docs/inbox/urls.md` batch manifest is handed off for ingest.
- About to write to `docs/research/sources/` for any reason.

**Two invocation modes:**
- **Explicit-path mode** — `process-inbox` (the inbox dispatcher) invokes this skill with the specific item path(s) under `docs/inbox/` to ingest. Process exactly those path(s); do not scan the rest of the inbox. This is the usual entry point.
- **Scan mode** — when a user invokes `ingest-research` directly without naming a path, scan `docs/inbox/` for research-shaped items (files, a pasted/handed URL, or a `urls.md` manifest) and run the pipeline once per source. Leave any non-research items in `docs/inbox/` untouched — they belong to other concerns and are `process-inbox`'s job to route.

Do **not** use this skill for:
- Querying the research wiki (no new source involved — that's `query-docs`).
- Refreshing stale sources (that's `refresh-research-sources`).
- Reconciling synthesis prose against updated sources (that's `refresh-research-synthesis`).
- Auditing the research tree (that's `audit-docs`).
- Editing the schema (`docs/AGENTS.md`).
- Recording an architectural decision (that's `propose-adr`).

## Special input: `docs/inbox/urls.md`

When a file named `urls.md` appears in `docs/inbox/` (dropped by the user or handed off by `process-inbox`), treat it as a **batch manifest** of URLs to ingest — not as a source itself.

**Format**:
```
https://example.com/article
https://example.com/blog-post (static)
# comment lines and blank lines are ignored
```

One URL per line, optionally followed by space-delimited annotations in parens.

**Supported annotations**:
- `(static)` — set `static: true` on the resulting source page. The `refresh-research-sources` skill will skip the source on every future run. Use for fixed publications (blog posts, papers, essays, news articles) where the URL is the citation but upstream won't change.

**Processing**:
1. Parse `urls.md`. Build the list of `(url, annotations)` pairs. Skip blank lines and lines starting with `#`.
2. Run the full ingest pipeline (steps 1–8) once per URL, in order. Each URL becomes its own source page with its own dated raw capture, frontmatter, and log entry.
3. After all URLs are processed successfully, **delete** `urls.md` from `docs/inbox/`. The individual log entries are the audit trail; the manifest itself is ephemeral. (When invoked by `process-inbox`, report the batch as fully drained so the dispatcher can relocate the manifest into `docs/inbox/_dispatched/`.)
4. If any URL fails the fetch step (degraded fetch, paywall, etc.), leave it in `urls.md` (preserving its annotations) and continue with the rest. At the end, report which URLs are still pending so the user can drop saved copies into `docs/inbox/` for re-ingest. A partially-drained `urls.md` legitimately remains in `docs/inbox/`.
5. If `urls.md` coexists with other files in `docs/inbox/`, process `urls.md` first, then handle remaining research-shaped files.

## The pipeline

Execute in order. Never reorder, never skip.

### 1. Identify the source

- **Files in `docs/inbox/`**: in explicit-path mode, the source path(s) are given; in scan mode, list the research-shaped items in `docs/inbox/`. If multiple files form one source (article + images, PDF + transcript), group them under one slug.
- **Video or audio files** (`.mov`, `.mp4`, `.webm`, `.mkv`, `.m4v`, `.avi`, `.mp3`, `.wav`, `.m4a`, `.ogg`, `.flac`): handled by the `transcribe-video` script in step 3+4 — see "Video/audio files" subsection below. Ask the user for the title and slug before starting.
- **Pasted URL (or URL from `urls.md`)**: invoke the `web-to-markdown` script — it runs `${CRUX_PLUGIN_ROOT}/scripts/web-to-markdown.py <url> --output-dir docs/research/raw/<today>/<slug>/`, which fetches the page, saves `source.html` and `source.url`, downloads images locally, and emits clean markdown to stdout plus a JSON metadata block to stderr. If exit code is non-zero or `thin_content: true` (exit 2), **STOP**. Show the user the error or the head of the markdown and ask them to drop a saved copy into `docs/inbox/`. Do not proceed with a degraded capture.
- Confirm the source's title with the user if it's ambiguous.

### 2. Generate slug and dated folder

- **Slug**: kebab-case from the source title, ASCII only, max ~60 chars. Disambiguate collisions with `-2`, `-3`. **Never** disambiguate by date — the dated folder already handles that.
- **Dated folder**: `docs/research/raw/YYYY-MM-DD/<slug>/` using **today's** date (the capture date, not the source's publication date).

### 3. Move (not copy) original(s) into raw

- For **files in `docs/inbox/`**: `mv` every related file into `docs/research/raw/YYYY-MM-DD/<slug>/`.
- For **video/audio files**: `mv` from `docs/inbox/` into `docs/research/raw/YYYY-MM-DD/<slug>/`, **renaming to `source.<ext>`** (e.g., `source.mov`, `source.mp3`) so the audit chain has a stable filename. Write `source.url` with the line `file: <original filename>` for provenance. Then invoke `${CRUX_PLUGIN_ROOT}/scripts/transcribe-video.py docs/research/raw/<today>/<slug>/source.<ext> --output-dir docs/research/raw/<today>/<slug>/` — this writes `transcript.md` and `metadata.json` alongside the video. Read the first 20 lines of the transcript and the metadata JSON before continuing.
- For **URLs**: the `web-to-markdown` invocation in step 1 (with `--output-dir docs/research/raw/<today>/<slug>/`) already wrote `source.html`, `source.url`, and any downloaded images into the dated folder. No additional move needed.
- `docs/inbox/` must be empty of this source's files after this step. Confirm with `ls docs/inbox/`. (Self-consumption: the move into `raw/` *is* how research items leave the inbox. When `process-inbox` invoked this skill, it then writes the `_dispatched/` pointer; in scan mode the move alone leaves the inbox empty of the just-ingested files.)

### 4. Write the audited markdown

Path: `docs/research/sources/<slug>.md`

**Required frontmatter (exact keys, in this order, omit nothing) — this list IS the canonical contract; `audit-docs` CHK-RES-1 enforces it:**

```yaml
---
title: "Exact source title"
slug: source-slug
type: source
source_url: https://...      # or null
source_date: 2026-04-12      # publication date or null
author: "Name"               # or null
captured_at: 2026-05-26      # today (date of the current raw capture)
last_source_check: 2026-05-26  # today (same as captured_at on first ingest)
raw_path: research/raw/2026-05-26/source-slug/
previous_captures: []        # empty on first ingest; refresh-research-sources appends
static: false                # true = opt out of refresh-research-sources
tags: [topic1, topic2]
---
```

- `raw_path` is recorded **relative to `docs/`** (i.e., `research/raw/...`, not `docs/research/raw/...`). This matches the contract `audit-docs` enforces.
- `last_source_check` and `previous_captures` are maintained by `refresh-research-sources` on subsequent verifications. On first ingest, initialize them as shown.
- `static` is set on first ingest. Set `true` for fixed publications. From `urls.md` batches, the `(static)` annotation sets this to `true`. Setting `static: true` only makes sense when `source_url` is not null.

Source pages do **not** carry `last_reviewed`. That field is for synthesis pages only.

**Body rules:**
- Preserve the source's structure — headings, lists, tables, blockquotes.
- Preserve exact wording for anything that may later be cited. Paraphrase only boilerplate (author bios, footers, navigation chrome) and mark paraphrased sections with `> [paraphrased]`.
- Reference images as `![[../raw/YYYY-MM-DD/<slug>/<image>]]` — never inline base64, never inline external URLs. (Path is relative to `docs/research/sources/<slug>.md`.)
- If anything couldn't be captured (paywall hit mid-article, OCR garbled a passage, audio not transcribed), end with `## Capture gaps` listing exactly what's missing.
- **No editorial commentary** in the source page — that belongs in synthesis pages.

**For URL sources**, the body content comes from `web-to-markdown`'s stdout. Treat it as a draft: strip surviving boilerplate (subscribe overlays, "Related posts", comment widgets), rewrite paraphrased sections. Use the script's stderr JSON to populate frontmatter: `title` → `title:`, `author` → `author:`, `published_date` → `source_date:`. If `title` in the JSON is clearly polluted (`"Article | Site | Tagline | Buy"`), prefer the on-page `<h1>` and note the swap to the user.

**For video/audio sources**, the body comes from `transcribe-video`'s stdout (also saved as `docs/research/raw/<today>/<slug>/transcript.md`). The transcript begins with `# <Title>` and `## Overview` — use those for the frontmatter `title:` (don't duplicate the `# Title` line in the body; let it be the page heading). **Frontmatter defaults for media sources**: `source_url: null`, `source_date: null` unless verifiable, `author: null` unless a single named speaker is clearly the author, `static: true` (videos don't refresh upstream), and add `video` or `audio` to `tags:`. Preserve the transcript verbatim.

### 5. Update research synthesis pages

- Read the audited source page you just wrote.
- Identify the entities, concepts, decisions-context, references, ideas, meetings, etc. it touches.
- For each, update an existing synthesis page or create a new one under the appropriate `docs/research/<category>/`.
- Add this source's slug to the page's `sources:` frontmatter list.
- Bump the page's `last_reviewed` to today.

**No existing synthesis page matches (new-domain case).** A source that opens a topic the wiki hasn't covered yet will match no existing page. **Do not silently skip the synthesis step** — that leaves the source unsynthesized and invisible to `query-docs`. Instead:
- If an existing **category** clearly fits (the topic belongs under `concepts/`, `references/`, etc., it's just the first page in that area), **create a new synthesis page** under that category — frontmatter per the shape above, `sources: [<this-slug>]`, a short stub body capturing the source's key claims — and add it to `docs/research/index.md`. A new *page* in an existing category needs no user approval; only a new *category* does (see Category gate).
- If you're unsure which category fits, or the page would be a near-empty single-source stub you're not confident about, **confirm with the user first**: name the source, propose the page title + category, and ask whether to create it now or defer. Note the deferral in the `ingest` log entry so it isn't lost.
- Only when the topic needs a category that doesn't exist do you hit the **Category gate** below (STOP, propose the category).

**Synthesis page frontmatter (the canonical contract — `audit-docs` CHK-RES-6 enforces it)** — required on every file under `docs/research/<category>/` other than `sources/`:

```yaml
---
title: "Human-readable title"
slug: page-slug
type: <category>             # matches the parent directory name exactly
tags: [topic1, topic2]
sources: [source-slug-1, source-slug-2]   # slugs in docs/research/sources/
last_reviewed: 2026-05-26
---
```

**Starter categories** for this plugin: `concepts/`, `decisions-context/`, `references/`, `ideas/`, `meetings/`. `init-docs` seeds all five.

**Category gate.** If a page's natural home doesn't fit any existing category under `docs/research/`, **STOP**. Propose the new category to the user with:
- A one-line definition of what would live there.
- 2–3 example page titles that would belong.
- The nearest existing category and why this is distinct from it.

Do not create the directory until the user agrees. When approved: create the empty directory, add a `(0)` section to `docs/research/index.md` in alphabetical order, log a `## [YYYY-MM-DD] schema | added category <name>` entry in `docs/log.md` (separate from this ingest's `ingest` entry).

**Contradiction rule.** When a new source contradicts an existing page, add a `> [contradiction]` blockquote inline citing both sources. **Do not silently overwrite** the older claim. Ask the user which to favor before resolving.

### 6. Update `docs/research/index.md` (concern-local)

- Add the new source to the Sources section: wiki-link, one-line summary, capture date, tags.
- Add any newly-created synthesis pages to their category section. (If you created a new category — only after user approval — add the section in alphabetical order.)
- Update the affected synthesis-page rows' `last_reviewed` to today.
- Update each section's heading count `## <Category> (N)` to match the actual file count under `docs/research/<category>/`.
- Update the `_Last updated:_` line at the top to `${TODAY}`.

### 6b. Update `docs/index.md` (master rollup)

The master `docs/index.md` carries one section per concern. The research section reads as:

```
## Research (N sources, M synthesis pages)
```

- Update `N` = count of files in `docs/research/sources/`.
- Update `M` = total count across all `docs/research/<category>/*.md` (excluding `sources/` and `raw/`).
- Update the master `_Last updated:_` line to `${TODAY}`.

### 6c. Append to `docs/research/sources.md`

Add **one new row at the top of the table** (immediately after the separator line) with these exact column values, in order:

| Column | Value |
|--------|-------|
| `slug` | the source's slug |
| `title` | the frontmatter `title:` |
| `source_url` | the frontmatter `source_url:`, or `—` if null |
| `captured_at` | today (`YYYY-MM-DD`) |
| `last_source_check` | today (same as captured_at on first ingest) |
| `last_update` | `—` (always on first ingest; populated by `refresh-research-sources`) |
| `static` | `true` / `false` / `n/a` (last one when `source_url` is `—`) |
| `raw_path` | `research/raw/<today>/<slug>/` (relative to `docs/`) |
| `wiki_path` | `research/sources/<slug>.md` (relative to `docs/`) |

Newest-first ordering. Do not sort or rewrite older rows — only prepend.

### 7. Append to `docs/log.md`

Format (exact prefix, no variations):

```
## [YYYY-MM-DD] ingest | <slug>
```

Body, 1–5 lines:
- Source title.
- Raw path (`research/raw/YYYY-MM-DD/<slug>/`).
- List of synthesis pages touched.
- If a new category was created, note it (and note the separate `schema` entry that recorded the category addition).
- If contradictions were flagged, note them.

### 8. Run the verification checklist

Confirm every item. If any item fails, fix it and re-verify before reporting the ingest complete.

## Verification checklist

- [ ] `docs/inbox/` is empty of the just-ingested files (`ls docs/inbox/`). (A partially-drained `urls.md` may legitimately remain.)
- [ ] `docs/research/raw/YYYY-MM-DD/<slug>/` exists and contains the original file(s).
- [ ] `docs/research/sources/<slug>.md` exists with all required frontmatter keys present (and non-empty except where `null` is explicitly allowed).
- [ ] The source page's `raw_path` resolves to a real folder (concatenated with `docs/`).
- [ ] `docs/research/index.md` has an entry for the new source.
- [ ] `docs/research/index.md` has entries for any newly-created synthesis pages, each in the correct section.
- [ ] `docs/research/index.md`'s section counts match the actual file counts in `docs/research/<category>/`.
- [ ] `docs/research/index.md`'s `_Last updated:_` is today.
- [ ] `docs/index.md`'s Research section header reads `## Research (N sources, M synthesis pages)` with the exact counts (the `docs/AGENTS.md` §5 canonical form).
- [ ] `docs/index.md`'s `_Last updated:_` is today.
- [ ] `docs/research/sources.md` has a new top row with all nine columns populated correctly.
- [ ] `docs/log.md` has a new entry with prefix `## [<today>] ingest | <slug>`.
- [ ] If a new category was introduced: the user approved it, the directory exists, `docs/research/index.md` has a section for it, and `docs/log.md` has a separate `## [<today>] schema | added category <name>` entry.

## Red flags — STOP and reconsider

- About to put a file directly in `docs/research/raw/` without a `YYYY-MM-DD/<slug>/` folder.
- About to write `docs/research/sources/<slug>.md` before moving the original into `docs/research/raw/`.
- About to paraphrase content the user might want to cite.
- About to create a directory under `docs/research/` without having asked the user.
- About to finish an ingest having touched **no** synthesis page because "nothing matched" — a new-domain source still needs a new synthesis page (or an explicit, logged deferral confirmed with the user). Don't silently skip synthesis.
- About to overwrite a contradicted claim "to keep the page clean."
- About to mark the ingest complete with the just-ingested files still sitting in `docs/inbox/`.
- About to update `docs/research/` while skipping `docs/research/index.md`, `docs/index.md`, or `docs/log.md`.
- Inclined to file a thin stub from a failed `web-to-markdown` fetch.
- Inclined to "batch the log entry at the end" instead of writing it now.
- About to write `raw_path: docs/research/raw/...` (absolute-with-docs-prefix) instead of `research/raw/...` (relative-to-docs). The contract is relative-to-docs.

Any of these means: STOP, return to the relevant step.

## Rationalization table

| Excuse | Reality |
|--------|---------|
| "I'll batch the log entry at the end of the session." | The log records *what happened when*. Batched entries lose audit value and collapse into one date. Log per ingest. |
| "The original is already on disk; pointing at it is enough." | If it's not under `docs/research/raw/<date>/<slug>/`, the audit chain is broken. Move it. |
| "`web-to-markdown` returned a body, so the capture is fine." | Short or templated bodies are usually JS placeholders or login walls. Read the body before continuing; if it's not the real content, stop and ask. |
| "I'll fold this into an existing category — close enough." | Forcing a square peg degrades both pages. If it doesn't fit, propose a new category through the category gate. |
| "I'll paraphrase to keep the source page concise." | The source page exists to be citable. Paraphrasing defeats its purpose. Preserve wording; summaries live in synthesis pages. |
| "The verification checklist is a formality after a clean ingest." | Every checklist item exists because skipping it causes drift. Run all of it, every time. |
| "The user can see what I did and will catch mistakes." | The user installed this discipline so they don't have to police it. Self-verify. |
| "It's faster to copy than move — I'll clean `docs/inbox/` later." | "Later" never comes. The inbox being empty of the just-ingested files is the signal that an ingest is done. Move, don't copy. |
| "The contradiction is minor; I'll just update the page." | "Minor" is a rationalization. Flag with `> [contradiction]` and let the user decide. |
| "Updating the master `docs/index.md` is bookkeeping; the concern index is enough." | The master rollup is what other agents and the user read first. Bump the counts on every ingest. |
| "I'll skip the `docs/research/sources.md` row append; the source page exists." | The registry is the parallel audit substrate — `audit-docs` cross-checks both. Missing rows = BROKEN finding. |

## Common mistakes

- **Date confusion**: using the source's publication date instead of today's date for the raw folder. Use `captured_at` (today) for `raw/`; `source_date` (publication) is just frontmatter.
- **Slug from URL slug**: the URL's slug is often noisy (`?utm=...`, hashes). Derive the slug from the *title*, not the URL.
- **`raw_path` written with `docs/` prefix**: the contract is relative-to-`docs/` (`research/raw/...`). `audit-docs` resolves it by prefixing `docs/`.
- **Wiki-link target wrong**: from `docs/research/concepts/foo.md`, citing the source is `[[research/sources/some-source]]` (docs-rooted). Don't write `../sources/some-source`. The master `docs/AGENTS.md` documents the resolver convention.
- **Forgetting `sources:` count math**: a synthesis page's `sources:` list is the source of truth; the concern-index row's "sources: N" must match its length.
- **Skipping `## Capture gaps`** when partial: future audits can't distinguish a complete capture from a partial one without it. Always declare gaps explicitly.
- **Writing the `schema` log entry for a new category as part of the `ingest` entry**: they're separate ops. One `schema` entry for the category addition, one `ingest` entry for the source.
- **Updating `docs/index.md`'s Research count by reading its old number**: count the actual files. Don't trust the prior rollup.
- **Treating `research/raw/` as mutable**: it's not. Files in `raw/` are append-only. Re-captures land in a new dated folder via `refresh-research-sources`.
