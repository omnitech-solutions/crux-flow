---
name: author-promptbook
description: "Create a tracked, multi-prompt plan without enforced cycle gates. Use for ad-hoc workflows or a successor to an abandoned book."
metadata:
  tags: "promptbooks, authoring, planning"
  bundles: "crux-docs"
  risk_level: "low"
  triggers: "new promptbook | draft a plan-of-prompts | formalize a workflow"
  routing_note: "Allocates next `PB-NNNN`."
---

# Author Promptbook

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

A promptbook is the plan; a run snapshot under `docs/promptbooks/runs/<id>-<slug>/` is the state. This skill creates the plan only. State lives next door — see `run-promptbook`.

**Format.** A new book is written as a **single structured YAML document** at `docs/promptbooks/active/PB-NNNN-<slug>.yaml` whose long-form fields (`goal`, `strategy`, `run_autonomy`) are Markdown block scalars and whose `prompts:` is a structured array — NOT the legacy frontmatter + free-form Markdown body. **This skill is an emitter: it ALWAYS writes new-format `.yaml`.** The only legacy contact point is the successor path, which may *read* a legacy `.md` predecessor (§1) — it still writes a `.yaml` book. Legacy `.md` books that already exist are never rewritten by this skill. Historical readers format-detect them; the current `run-promptbook` and `archive-promptbook` refuse live Markdown operations before mutation. An unfinished Markdown run that cannot truthfully finish remains readable and unresolved.

Each book gets a fresh `PB-NNNN` id, monotonic and never reused. The id is allocated atomically from `docs/manifest.yml`'s `promptbook.next_number`. A successor book always gets a new id (never reuse, never edit the prior book's prompts mid-run).

A book is mutable while it has no current run. Once a run starts (`current_run` is non-null), the `prompts:` list freezes — and stays frozen until `archive-promptbook` nulls the pointer, which is after the run reaches a terminal status, not at the moment it does — `book_content_hash` binds the run to that plan.

**To change a YAML plan mid-run, abandon the YAML run and author a successor.** Run `run-promptbook abandon PB-NNNN --reason "<why>"`, then invoke this skill for a fresh book. For a Markdown run, the current version cannot abandon it; preserve its bytes as history and author separate YAML work without claiming closure. The mid-run fork path is deleted.

The successor **names its predecessor in prose** — in its `goal` or `strategy` — because that is now the only place the relation lives. `forked_from:` remains an accepted book field that is always `null`, and nothing writes it. State the consequence when it matters: the successor relation no longer resolves mechanically, so a reader follows the prose or nothing.

## When to use

- User says: "new promptbook", "draft a plan-of-prompts", "formalize this workflow", "capture this as a promptbook".
- User attempts to edit the `prompts:` list while a YAML run is in progress — `run-promptbook` refuses, the user abandons the YAML run, and routes here for a successor book. A legacy `## Prompts` book stays unchanged; a stranded Markdown run is not abandoned by the current version.
- After a brainstorming session that yielded a clear multi-step plan worth tracking.

Do **not** use this skill for:
- Advancing or recording results of an in-flight run — that's `run-promptbook`.
- Editing the narrative `goal` / `strategy` of an existing book (legacy `## Goal` / `## Strategy`) — those are human-editable in place; no skill invocation needed.
- Archiving a completed book — that's `archive-promptbook`.

## Inputs

- **Required**: a working title, a one-paragraph goal that opens with what should improve
  for the affected user (Outcome), what would show it (Evidence), and what the work must
  preserve (Constraint) — see `docs/AGENTS.md` §11, "The assignment contract". A short
  book says all three in a sentence. No new field, no form, no gate.
- **Recommended on first author**: the prompts list (at least 1, ideally 3–12). If the user only gives a title, write a stub book with one placeholder prompt and tell them to fill it in before starting a run.
- **A successor book** (optional): when the user is re-authoring after an abandoned run, read the predecessor and carry its plan — `goal`, `strategy`, and `prompts` — into the new `.yaml` book, revised as the user wants. The predecessor may be **either** a new-format `.yaml` book **or** a legacy `.md` book; format-detect it (§1) and translate the legacy body into the structured fields when reading `.md`. The new book names the predecessor in its `goal`/`strategy` prose; `forked_from:` stays `null`, and every state field is fresh (`status: active`, `current_run: null`, `current_prompt: null`).

## The pipeline

### 0. Resolve per-repo configuration (.crux)

Run `python3 "${CRUX_PLUGIN_ROOT}/scripts/crux-config.py"` from the repo root (or pass `--repo-root <repo-root>`), and confirm the returned `repo_root` is the repo you are operating in — `source: "discovery:<dir>"` with an unexpected `repo_root` means you resolved the wrong directory, not that no config exists. On exit 1, **STOP** and surface the `{"error": ...}` payload — never fall back to defaults. Use the returned `docs_dir` wherever this skill says `docs/` (per the docs/AGENTS.md §14 normative definition clause). When `artifact_prefix` is non-empty, the prefixed string (e.g. `CRX-PB-0040`) **IS** the book id for every subsequent step — the filename, the YAML `id:`, the index rows, wiki-links, and the `log.md` subject all carry it verbatim (per docs/AGENTS.md §14.3, "verbatim on every surface"); only the `NNNN` allocation is prefix-blind (it still comes from the manifest counter exactly as below). When authoring a successor, cite the predecessor's id in prose exactly as spelled on disk — never retro-apply the current prefix to a pre-prefix id.

### 1. Decide: new book vs successor

If the user is re-authoring after an abandoned run, read the predecessor book and confirm it exists. If its `status` is `archived`, that is normal — an abandoned run's book is archived as abandoned before the successor is written.

**Format-detect the predecessor FIRST** (the two-key decision table). A `PB-NNNN` book may exist on disk as either `PB-NNNN-<slug>.yaml` (new-format) or `PB-NNNN-<slug>.md` (legacy). Route by `(extension, format_version-present)`:

| source extension | `format_version` | how to read it |
|---|---|---|
| `.yaml` | present | **new-format** — parse the YAML document; copy its `goal` / `strategy` / `prompts[]` structurally. |
| `.yaml` | absent | **error** — a malformed new-format book. Refuse; do NOT send it to the legacy parser. |
| `.md` | (ignored) | **legacy-markdown** — parse the legacy frontmatter + Markdown body; translate `## Goal` → `goal`, `## Strategy` → `strategy`, and each `### Prompt N — <title>` block (its `**Purpose:**` / `**Prompt:**` blockquote / `**Expected output:**` / `**Side effects:**`) into a structured `prompts[]` element. |

Either way, the **new** book is written as `.yaml` (step 4). The source book is never modified — a legacy `.md` source stays exactly as it was. Reading a historical predecessor here never starts, advances, abandons, or archives its Markdown run in the current version; those live operations use the pinned recovery route in `run-promptbook`.

Otherwise this is a fresh authoring pass. Confirm title and goal with the user before allocating an id.

### 2. Allocate id (write-first ordering, matches `propose-adr`)

- Read `docs/manifest.yml`. Take `promptbook.next_number` (e.g. 3).
- Format as `PB-NNNN` zero-padded to 4 digits (e.g. `PB-0003`).
- **Do NOT increment the manifest yet.** First write the book file in step 4. Only after the book file is durably on disk, re-read `manifest.yml`, increment `promptbook.next_number`, and write it back (step 6b below). Same order as `propose-adr`: write-then-increment.
- A crash between read and write of the manifest can re-allocate the same number to a second author run; the file-existence check in step 4 catches it and aborts the second run cleanly.
- Numbers are NEVER reused — even if this allocation is abandoned partway, the next successful author run gets the same number (because the manifest was never bumped).

### 3. Slug

Kebab-case derived from the title. ASCII only. Truncate to ~50 chars. Disambiguate collisions with `-2`, `-3` etc.

Filename: `docs/promptbooks/active/PB-NNNN-<slug>.yaml` (new-format `.yaml` extension).

### 4. Write the book (new-format YAML)

> **PROHIBITION (read first — most common-mistakes trace to this):** **Do NOT emit any legacy construct.** No `## Goal` / `## Strategy` / `## Prompts` headings, no `### Prompt N` headings, no `**Purpose:**` bold labels, no `>` prompt blockquote, no Markdown frontmatter + body. Those belong only to legacy `.md` books this skill NEVER writes. This skill is an emitter; it ALWAYS writes new-format structured `.yaml`. Legacy `.md` is only ever *read* (the predecessor in §1).

Write a **single structured YAML document** conforming to `${CRUX_PLUGIN_ROOT}/schemas/promptbook.schema.json`. Top-level keys (flat document, `additionalProperties: false`):

```yaml
format_version: "1"            # the per-file coexistence signal; quoted string, const "1"
id: PB-NNNN
title: "<title>"
status: active                 # active | archived
created_at: <today YYYY-MM-DD>
total_prompts: 0               # placeholder until prompts: is finalized in step 5; becomes len(prompts)
current_run: null              # RUN-NNN of this book's current run; null if none
current_prompt: null           # 1-indexed pointer into prompts:; null if no run
forked_from: null                  # accepted vestige; always null, nothing writes it
tags: [<tags>]
# modules: { adrs: N, dev_loops: N, review_cycles: N }   # cycle-only; omit on non-cycle books
goal: |
  <One short paragraph (Markdown): what does this promptbook accomplish?>
strategy: |
  <One or two paragraphs (Markdown): the approach, ordering rationale, out-of-scope.>
# run_autonomy: |              # optional reserved field; cycle books written by dev-cycle set it
prompts:
  - n: 1
    title: "<Short verb-led title>"
    purpose: |
      <why this step exists; what it unblocks for the next step>
    prompt: |
      <The exact prompt text to issue. Use docs/ paths for references. Keep it
      self-contained — assume the next session has no memory of prior conversation.>
    expected_output: |
      <what success looks like — a file list, a diff, a summary, etc.>
    side_effects: [log-work]   # list of crux skill ids the prompt expects to invoke; [] if none
    # module_tag: dev-1        # cycle-only; ^(adr|dev|review)-\d+$ — omitted on non-cycle books
```

**Block-scalar discipline (load-bearing):** the long-form fields (`goal`, `strategy`, each prompt's `purpose` / `prompt` / `expected_output`, and `run_autonomy` if present) are **YAML literal block scalars (`|`)** so embedded Markdown — lists, blockquotes, code fences, `[[wiki-links]]` — round-trips losslessly. Mind the indentation: a stray de-indent breaks YAML parsing of the whole document.

- `goal`, `strategy`, and `prompts` (with at least one element) are required. Write a stub single-prompt book only if the user gives a title alone (and warn).
- Honor the no-legacy-construct PROHIBITION at the top of this step — every field is a structured YAML key, never a legacy `##`/`###` heading or `**bold**` label.
- For a successor, populate `goal` / `strategy` / `prompts` from the format-detected predecessor (§1): copy structurally from a `.yaml` predecessor, or translate the legacy body into structured fields from a `.md` one. Name the predecessor in the `goal` or `strategy` prose. Leave `forked_from: null`.
- `${CRUX_PLUGIN_ROOT}/templates/promptbook-template.md` is the legacy reference only; the authoritative shape is the schema above.

### 5. Finalize the prompts list

Walk the user through the `prompts:` array. Each element must have:
- `n` — the 1-based position (`== index + 1`; contiguous from 1, enforced by the validator's post-schema pass).
- `title` — short verb-led title (scalar).
- `purpose` — what this step unblocks (Markdown block scalar).
- `prompt` — the exact prompt text (Markdown block scalar; the legacy `>` blockquote becomes the scalar's content, no `>` marker needed).
- `expected_output` — what success looks like (Markdown block scalar).
- `side_effects` — a list of crux skill ids the prompt expects to invoke (`[]` if none).
- `module_tag` (cycle-only) — `^(adr|dev|review)-\d+$`; omit on non-cycle books.

When the list is finalized, set `total_prompts` to **`len(prompts)`** — the count of elements in the `prompts:` array (NOT a count of `### Prompt N` headings; new-format books have no such headings). This is the denominator for percent-complete tracking in `index.md`.

### 5b. Validate the book

After writing the `.yaml` file (and before regenerating the index), validate it:

```bash
uv run "${CRUX_PLUGIN_ROOT}/scripts/validate-promptbook.py" docs/promptbooks/active/PB-NNNN-<slug>.yaml
```

(In a source checkout, where `${CRUX_PLUGIN_ROOT}` is unset, substitute the checkout's `crux/` directory — source checkout: `<checkout>/crux/scripts/validate-promptbook.py`.)

Exit `0` = clean; exit `1` = a validation error with a `{"errors":[{file, instance_path, schema_path, error}]}` JSON payload on stdout (non-zero with empty stdout = crash — surface stderr). **If validation fails, REFUSE: fix the document (or, if the inputs themselves are wrong, surface to the user) and re-validate before proceeding.** Do not regenerate the index, increment the manifest, or log against an invalid book.

### 6. Regenerate `docs/promptbooks/index.md`

Walk `docs/promptbooks/active/`, `docs/promptbooks/runs/`, and `docs/promptbooks/archive/`. The directory walk is **extension-agnostic** — enumerate both `*.yaml` (new-format) and `*.md` (legacy pre-migration) books so coexisting books all appear in the index. Wiki-links are extension-less, so a `.yaml` book and a `.md` book link identically. The index is a **regenerated live view**, so its rows may carry the lifecycle-accurate segment (`[[promptbooks/active/PB-NNNN-<slug>]]` in the Active table, `[[promptbooks/archive/PB-NNNN-<slug>]]` in the Archived section) — these are rewritten to match each book's current location on every walk, so they never dangle; the `docs/AGENTS.md` §11 resolver also accepts the lifecycle-neutral `[[promptbooks/PB-NNNN-<slug>]]` form (which frozen citations like journal Refs should use). Rewrite `index.md` from scratch (don't try to patch — easier to be correct from a full walk):

```markdown
# Promptbooks

_Last updated: YYYY-MM-DD_

## Active (N)

| id | title | status | current_run | progress | tags | created_at |
|---|---|---|---|---|---|---|
| PB-0003 | <title> | active | null | 0/12 (0%) | <tags> | 2026-05-26 |

## Recent runs (last 20)

- `PB-0003/RUN-001` — in_progress — started 2026-05-26 — current prompt 4

## Archived (N)

- [[promptbooks/archive/PB-0001-...]] — completed 2026-05-12
```

`current_run` is `null` when no run has started.

### 6b. Increment `manifest.yml`

Now that the book file is durably on disk, re-read `docs/manifest.yml`, increment `promptbook.next_number`, write back. (If re-read shows a value higher than what we allocated, another author run got there first — abort and surface the collision to the user.)

While a tester's window is open, commit nothing and run no regenerator. The window runs from the tester's dispatch until the tester returns; outside a run there is none.

After the counter bump, commit the allocated file together with the bump. Never commit the counter bump without the file it allocated. Stage only these paths (`git add -- <paths>`) and commit only them (`git commit -- <paths>`), so no change already staged is included. If a regenerator still exits 2 with `migration-input-not-committed`, another uncommitted input is in the tree: stop and name it; never commit a file this skill did not write.

Then regenerate the summaries projection, then the doctrine projection, because the bump changes their input hash:

```
uv run "${CRUX_PLUGIN_ROOT}/scripts/summarize-adrs.py" --repo-root <repo-root>
uv run "${CRUX_PLUGIN_ROOT}/scripts/compile-doctrine.py" --repo-root <repo-root>
```

Confirm `--dry-run` of each exits 0, then commit the regenerated projections so none stays uncommitted.

### 6c. Update `docs/index.md`

Read `docs/index.md`. In the `## Promptbooks (X active, Y archived)` section, bump active count by 1. Update `_Last updated:` to today. Save.

### 7. Append to `docs/log.md`

```
## [YYYY-MM-DD] promptbook | authored PB-NNNN-<slug>
```

Body: title, id, total_prompts, and the predecessor's id when this book succeeds an abandoned run.

### 8. Verification

- [ ] `docs/manifest.yml` `promptbook.next_number` incremented and persisted.
- [ ] `docs/promptbooks/active/PB-NNNN-<slug>.yaml` exists with all required top-level keys (incl. `format_version: "1"`).
- [ ] `validate-promptbook.py` exits `0` against the new book (step 5b).
- [ ] `total_prompts` equals `len(prompts)` — the count of elements in the `prompts:` array.
- [ ] `docs/promptbooks/index.md` lists the new book with correct progress (`0/N (0%)`).
- [ ] `docs/log.md` has a new top-of-file `promptbook | authored ...` entry.
- [ ] `forked_from:` is `null` (it is an accepted vestige; nothing writes it).
- [ ] If this book succeeds an abandoned run: the predecessor is named in the `goal`/`strategy` prose, and the predecessor book itself is unchanged.

## Red flags — STOP and reconsider

- About to write a book with `total_prompts: 0` and an empty `prompts:` array. The book is unusable without prompts (the schema requires `minItems: 1`). Either insist on at least one prompt, or write a single stub and warn the user.
- About to emit a legacy `.md` book — frontmatter + `## Goal` / `## Strategy` / `## Prompts` with `### Prompt N` headings. NEVER. This skill is an emitter; it ALWAYS writes new-format `.yaml`. Legacy `.md` is only ever *read* (the predecessor in §1), never written.
- About to write a non-null `forked_from:`. NEVER — the field is an always-null vestige. The successor relation lives in prose.
- About to skip `validate-promptbook.py` (step 5b) and regenerate the index against an unvalidated book. Refuse — validate first; a malformed `.yaml` book breaks every reader.
- About to increment `manifest.yml` before the book file lands on disk. Order matters: write the book file first, then increment, matching `propose-adr`. A failed write should not burn a `PB-NNNN`.
- About to reuse a previously-used id (e.g. user manually deleted `PB-0005-...` and asked to "use 5 again"). NEVER. Allocate fresh.
- About to edit the `prompts:` list of an existing book whose `current_run` is non-null. Refuse — and give the remedy that fits the pointed-to run's `status`, because the wrong one dead-ends. YAML run still `in_progress` → tell the user to abandon it (`run-promptbook abandon`), then offer to author the successor here. Markdown run still `in_progress` → do not promise abandonment; preserve it as readable history and offer separate YAML work. YAML run already terminal (`completed`, or `abandoned`) → abandonment is NOT available (`advance-run.py --abandon` refuses a terminal run); use current `archive-promptbook`. An eligible terminal Markdown run still requires pinned public `v3.23.2` archival on a copy, then book-before-run conversion before returning to the current version. A stranded Markdown run remains unresolved; author separate YAML work without changing it.
- About to append to `docs/promptbooks/index.md`. Rewrite from a directory walk instead — patching is bug-prone. The walk globs BOTH `*.yaml` and `*.md` so coexisting legacy books aren't dropped.
- About to write the book file before incrementing `manifest.yml`. Wrong order.
- About to forget the `created_at` top-level key (silent breakage in progress reporting).

## Rationalization table

| Excuse | Reality |
|---|---|
| "User only gave me a title; I'll create the book with no prompts." | A book with an empty `prompts:` array fails the schema (`minItems: 1`) and is dead weight in `index.md`. Either get one prompt or hold off authoring entirely. |
| "I'll just edit the `prompts:` list directly — it's only one line." | A mid-run edit breaks the run snapshot's content-hash binding and shows up as a CHK-PB-BIND mismatch that is never auto-fixed. Abandon the run and author a successor. |
| "I'll set `total_prompts: 12` even though `prompts:` has 10 — I'm pretty sure two more are coming." | The denominator is a fact, not an aspiration. `total_prompts == len(prompts)`, now; the validator + audit cross-check it. |
| "I'll write this book as `.md` — it's what I know." | This skill always emits new-format `.yaml`. `.md` is read-only here (the predecessor). |
| "I'll allocate id 5 because PB-0005 was deleted." | Reuse breaks the audit chain forever. Allocate fresh; numbers are cheap. |
| "I'll patch `index.md` to add my new row — full rebuild is overkill." | Patches drift. Walk the directory and rewrite. |

## Common mistakes

- **Wrong counter**: reading `adr.next_number` because both are in `manifest.yml`. Promptbooks use `promptbook.next_number`. Different counter, never crossed.
- **Reusing the predecessor's slug**: a successor gets a NEW slug derived from its own (possibly new) title. Don't reuse the predecessor's slug verbatim.
- **Forgetting to re-set `total_prompts` after a late edit**: if the user adds a prompt to `prompts:` during step 5, set `total_prompts = len(prompts)` before writing the file (the validator's structural pass + audit catch drift).
- **Writing `forked_from: null` (or `current_run` / `current_prompt`) as the string `"null"` instead of YAML null**: use bare `null` (no quotes) so YAML parsers see it as null, not a string.
- **Putting `prompts` content inside `strategy`**: `strategy` is the narrative block scalar; `prompts:` is the structured executable list. Keep them separate so `run-promptbook` can read the prompt array deterministically.
- **Forgetting `format_version: "1"`**: without it a `.yaml` book is malformed — readers route it to *error*, not to the legacy parser. It is the per-file coexistence signal.
- **Emitting Markdown inside a quoted scalar instead of a `|` block scalar**: long-form fields (`goal`/`strategy`/`purpose`/`prompt`/`expected_output`) must be literal block scalars so embedded lists/blockquotes/`[[links]]` round-trip.
- **Updating `current_run` or `current_prompt` from this skill**: those are owned by `run-promptbook`. Always `null` on initial author.
