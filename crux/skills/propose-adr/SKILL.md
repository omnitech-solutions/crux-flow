---
name: propose-adr
description: "Create a Proposed ADR for an architectural decision, allocating its identifier and updating decision indexes and logs."
metadata:
  tags: "adrs, decisions, authoring"
  bundles: "crux-docs"
  risk_level: "low"
  triggers: "propose ADR | record decision | new ADR | ADR for <topic>"
  routing_note: "Writes status: Proposed; never auto-accepts."
---

# Propose ADR

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

The atomic operation of writing a new Architecture Decision Record. Each call: allocates the next zero-padded `ADR-NNNN` from `docs/manifest.yml`, drops a templated ADR file under `docs/adrs/` with `status: Proposed`, updates the ADR index table, and logs the operation. The user fills in the body sections (`Context`, `Decision`, `Alternatives Considered`, `Consequences`, `References`) — this skill provides the scaffolding only.

Core principle: **numbers are precious and forever**. Once `manifest.yml`'s `adr.next_number` increments, that number is permanently assigned to this ADR — even if the user later deletes the file. Never reuse. Never decrement.

Status starts as `Proposed`. This skill does NOT auto-accept. The `--accept-immediately` flag chains to `transition-adr accept` only after the user explicitly opts in (the flag itself counts as explicit opt-in).

## When to use

- User says: "propose ADR", "new ADR", "record decision", "ADR for <topic>", "write up that decision", "let's ADR this".
- After a multi-turn discussion that arrived at a non-trivial architectural choice.
- When the user explicitly asks to formalize a verbal decision.

Do **not** use this skill for:
- Accepting/deprecating/superseding an existing ADR (that's `transition-adr`).
- Editing an ADR's body after it has been written (the body is mutable only while `Proposed`; the user edits it directly — this skill never rewrites bodies).
- Recording a pre-decision exploration — that's a brief: scaffold it with `propose-brief` (and close its lifecycle with `transition-brief`); the body stays human-authored.
- Logging routine work (use `log-work` for narrative; use the originating skill's own `log.md` write for ops).

## Inputs

- `--title "<declarative title>"` — required. Declarative present-tense, e.g., "Use SQLite for local-first storage".
- `--tags <comma-list>` — optional. E.g., `--tags storage,local-first`.
- `--deciders <comma-list>` — optional. Defaults to the actual user's handle, resolved at runtime (e.g. from `git config user.name` or the session user) — never a hardcoded name.
- `--related-briefs <comma-list>` — optional. Filenames (no path) under `docs/briefs/`, e.g., `BRIEF-storage-options`.
- `--related-research <comma-list>` — optional. Source slugs under `docs/research/sources/`.
- `--accept-immediately` — optional. After writing the proposal, chain to `transition-adr accept <id>`. **When appropriate:** an already-debated, low-risk, or meta/process ADR whose decision was settled before this skill ran — e.g. a meta-ADR (the "we record decisions" class — the bootstrap meta-ADR `init-docs` ships), a mechanical/housekeeping decision the user has explicitly already agreed to, or an ADR that merely ratifies a conclusion a prior discussion (or council) already reached. **When NOT:** any ADR whose Proposed → Accepted review window still carries value — a genuinely contested or high-risk architectural choice, or one no one has reviewed yet. When in doubt, leave it Proposed and let `transition-adr accept` run as a separate, deliberate step.
- Interactive mode: if `--title` is omitted, prompt the user. Confirm the title before allocation.

## The pipeline

Execute in order. Never reorder, never skip. Number allocation must be atomic.

### 0. Resolve per-repo configuration (.crux)

Run `python3 "${CRUX_PLUGIN_ROOT}/scripts/crux-config.py"` from the repo root (or pass `--repo-root <repo-root>`), and confirm the returned `repo_root` is the repo you are operating in — `source: "discovery:<dir>"` with an unexpected `repo_root` means you resolved the wrong directory, not that no config exists. On exit 1, **STOP** and surface the `{"error": ...}` payload — never fall back to defaults. Use the returned `docs_dir` wherever this skill says `docs/` (per the docs/AGENTS.md §14 normative definition clause). When `artifact_prefix` is non-empty, the prefixed string (e.g. `CRX-ADR-NNNN`) **IS** `${ID}` for every subsequent step — the filename, the frontmatter `id:`, the index row, wiki-links, and the `log.md` subject all carry it verbatim (per docs/AGENTS.md §14.3, "verbatim on every surface"); only the `NNNN` allocation is prefix-blind (it still comes from the manifest counter exactly as below).

### 1. Read `docs/manifest.yml`

- Verify the file exists. If not, **STOP** — `init-docs` was never run.
- Parse YAML. Read `adr.next_number` (an integer ≥ 1; 0 is reserved for the bootstrap ADR).
- Hold this value as `${N}` for the duration of this run.

### 2. Compute the ADR id and slug

- `${ID}` = `ADR-` + zero-padded 4-digit `${N}` (e.g., `${N}`=7 → `0007`).
- `${SLUG}` = kebab-case of `${title}`, ASCII only, truncated to 50 chars. Trim trailing hyphens after truncation.
- `${FILE}` = `docs/adrs/${ID}-${SLUG}.md`.
- Collision check: if `${FILE}` already exists, **STOP** with a BROKEN error — the manifest counter is desynced from the directory. Tell the user to run `audit-docs` to reconcile.
- Title collision check (different number, same slug): warn the user but proceed.

### 3. Read the ADR template

- Read `${CRUX_PLUGIN_ROOT}/templates/ADR-template.md`. The template carries the full frontmatter shape — substitute into it; do not re-author the field list from memory.
- **The canonical frontmatter contract — which fields exist, their types, and required/optional — lives in `docs/AGENTS.md` §11.A "Canonical ADR frontmatter schema"** (the single source of truth). This skill does NOT restate the field list (it would drift); read §11.A. `audit-docs` CHK-ADR-1a enforces that the template and §11.A stay in lock-step.
- Substitute the values for the new ADR: `id` → `${ID}`, `title` → `${title}`, `status` → `Proposed`, `date`/`proposed_date` → `${TODAY}`, the remaining `*_date` fields → `null`, `supersedes` → `[]`, `superseded_by` → `null`, `deciders`/`tags`/`related_briefs`/`related_research` → the passed values (default `[]` / the runtime-resolved user handle, as documented under Inputs). For the exact field set and any optional fields (e.g. `amends`), defer to §11.A and the template — they are authoritative.
- Substitute the body heading `# ADR-NNNN — <Title>` → `# ${ID} — ${title}`.
- Leave the five body sections (`## Context`, `## Decision`, `## Alternatives Considered`, `## Consequences`, `## References`) with their stub content from the template. The user fills these in.
- **The body content rule — what a body may contain — lives in `docs/AGENTS.md` §11.D "ADR body content rule"** (the single source of truth). A body states requirements and postconditions, never implementation recipe; it names a source of truth rather than restating a shape; and its four narrative sections carry a line budget as a tripwire for that rule. This skill does NOT restate the rule or the budget's value (it would drift); read §11.D. `audit-docs` `CHK-ADR-SPEC` reports against it, prospectively only, over the cohort `docs/manifest.yml` `adr.spec_rule_from` names. The template carries the same pointer as an HTML comment under its H1; leave that comment in place — the author deletes it when the body is written.

### 4. Write the ADR file

- Write to `${FILE}` exactly the substituted template.
- **`governs` block (cohort-bound).** If this tree sets `adr.governs_from` in `manifest.yml` and `${N}` is at or above it, this ADR is in the governs cohort: author a `governs` block per §11.A (a prospective entry needs no `anchor`), or record the ADR in `adr.governs_exempt`. Otherwise `check-governs-coverage.py` fails on it. A tree with `governs_from` unset expects no block.
- If write fails, **STOP** — do not increment the counter. The number stays unallocated.

### 5. Increment `adr.next_number` atomically

- Re-read `docs/manifest.yml` to confirm `adr.next_number` is still `${N}`. If it changed, another invocation raced us — STOP and tell the user to retry.
- Set `adr.next_number = ${N} + 1`.
- Write `manifest.yml` back. Preserve all other keys and formatting.

### 6. Append to `docs/adrs/index.md`

The index is a markdown table. Columns, in order: `id | title | status | date | supersedes | superseded_by | tags`.

- Locate the table body (rows after the `|----|...` separator).
- Insert a new row immediately after the separator (newest at top, like `log.md`):

  ```
  | ${ID} | ${title} | Proposed | ${TODAY} | — | — | ${tags_csv} |
  ```

- Use `—` (em dash) for empty `supersedes` and `superseded_by`.
- `${tags_csv}` is comma-separated; `—` if no tags.
- Update the `_Last updated:_` line to `${TODAY}`.

### 7. Append to `docs/log.md`

Prepended (newest first):

```markdown
## [${TODAY}] adr | ${ID}: ${title}

Proposed. File `docs/adrs/${ID}-${SLUG}.md`. Tags: ${tags_csv}.
```

### 8. Update `docs/index.md` ADR section

- Bump the count: `## ADRs (M)` where `M` is the new total ADR count under `docs/adrs/` (count files matching `ADR-*.md`, not the index).
- Optionally append a one-line bullet for the new ADR; the rollup contract is "one row per ADR" — see other implementers' `docs/index.md` format contract.
- Update the `_Last updated:_` line.

### 9. Regenerate the summaries projection (governs-bearing ADRs)

If the new ADR carries a `governs` block — or `adr.governs_from` is set and `${N}` is at or above it — its governs entries now belong to the active-ADR set the summaries projection reads (top-level `adrs/`, whether Proposed or Accepted). Regenerate it so the working tree stays fresh:

```
uv run "${CRUX_PLUGIN_ROOT}/scripts/summarize-adrs.py" --repo-root <repo-root>
```

This rewrites `<docs_dir>/adrs/summaries/` (rule table, resolver, implementation map, `_meta.json`) from every active ADR's governs blocks. The projection's input hash covers the full active-ADR frontmatter, so any ADR frontmatter mutation drifts it; regenerating here keeps the `summarize-adrs.py --dry-run` drift gate (and CI) green rather than leaving a drifted tree for the next PR to catch. Skip only when the ADR has no `governs` block and `adr.governs_from` is unset.

### 9a. Read the rule-length advisory

Same guard as step 9: skip only when the ADR has no `governs` block and `adr.governs_from` is unset.

```
uv run "${CRUX_PLUGIN_ROOT}/scripts/check-governs-coverage.py" --repo-root <repo-root>
```

Read the `warnings` list in its JSON. A row naming this ADR means one of its `governs` rules is
longer than the recommended maximum; the row carries the handle, the actual length, that maximum,
and the guidance for shortening it. **It is advisory.** It never changes the exit code, it blocks
nothing, and it is not a waiver you record — there is no waiver field and no approval step.

**The principle the row serves is `docs/AGENTS.md` §11.D rule 7. Read it there; this skill does not
restate it, for the reason step 6 gives.** Act on the row in one of two ways: shorten the rule as
that rule and the row's own message direct, or leave it and say in one line, in the hand-off
below, why the length is what precision costs here. Never truncate or rewrite a rule mechanically
to clear a row.

A row naming `manifest.yml` rather than an ADR means this tree has not snapshotted its baseline, so
the advisory is inert; that is a tree-configuration matter, not a finding about this ADR.

### 10. Optional: `--accept-immediately`

- Only if the flag was passed.
- Chain-invoke `transition-adr accept ${ID}`.
- That skill writes a second entry to `log.md` (`adr | ${ID}: accepted`) and updates the ADR's status frontmatter. This skill does NOT modify the ADR's frontmatter or body after the initial write.

### 11. Hand off to the user

- Tell the user: ADR id assigned, file path, current status (`Proposed`).
- Explicitly instruct: edit the five body sections (`Context`, `Decision`, `Alternatives Considered`, `Consequences`, `References`) — do NOT change the frontmatter.
- If step 9a reported a row for this ADR, name the rule and say whether it was shortened or why its length is what precision costs. Do NOT restate `docs/AGENTS.md` §11.D rule 7 in the hand-off; name it.
- Point the author at `docs/AGENTS.md` §11.D before they write: requirements and postconditions, not recipe; name a source of truth rather than restating a shape; measurements go in a footnote marked informative; the four narrative sections carry a line budget, and going over it on purpose means declaring why. Naming the section is the whole instruction — do not paraphrase the rule here.
- If accepted-immediately, tell the user the body is now frozen except via supersession.
- Optionally call `log-work --silent --category decision --subject "ADR-NNNN proposed: <title>"` if the user opted into auto-journaling for ADRs.

## Verification checklist

- [ ] `docs/adrs/${ID}-${SLUG}.md` exists.
- [ ] Frontmatter parses as YAML.
- [ ] `id:` matches the filename's `${ID}`.
- [ ] `status: Proposed`.
- [ ] `date:` and `proposed_date:` both equal `${TODAY}`.
- [ ] `accepted_date`, `deprecated_date`, `superseded_date` are all `null`.
- [ ] `supersedes: []`, `superseded_by: null`.
- [ ] Body has all five required H2 sections in order: Context, Decision, Alternatives Considered, Consequences, References.
- [ ] The template's body-content-rule HTML comment survived the substitution (it points the author at `docs/AGENTS.md` §11.D; this skill scaffolds, so the comment is handed over intact, not resolved).
- [ ] `docs/manifest.yml` `adr.next_number` is `${N} + 1`.
- [ ] `docs/adrs/index.md` has a new row at the top with this ADR's `${ID}`, status `Proposed`, date `${TODAY}`.
- [ ] `docs/adrs/index.md` `_Last updated:_` is `${TODAY}`.
- [ ] `docs/log.md` has a new `## [${TODAY}] adr | ${ID}:` entry at the top.
- [ ] `docs/index.md` ADR section count matches the actual file count.
- [ ] If `--accept-immediately` was passed: `transition-adr accept ${ID}` ran successfully and the verification carries through.

## Red flags — STOP and reconsider

- About to write the ADR file before incrementing `adr.next_number`. Order matters: write first (so a failed write doesn't burn a number), then increment.
- About to allocate a number when the proposed `${ID}-${SLUG}.md` already exists. The manifest is out of sync with reality; refuse and tell the user to audit.
- About to write `status: Accepted` directly because the user said "I want this accepted". The two-step `Proposed → Accepted` review window exists for a reason; require explicit `--accept-immediately`.
- About to overwrite an existing ADR file. Never. ADRs are append-only history; even a freshly-Proposed one is a permanent record.
- About to skip the index row or the log entry. Both are required; downstream skills depend on them.
- About to assign the `0000` number to anything other than the bootstrap meta-ADR `init-docs` ships. The zeroth id is reserved.
- About to use `today` for any of the `*_date` fields other than `proposed_date` and `date`. The other date fields are `null` until their corresponding transitions occur.
- About to silently expand the slug beyond 50 chars to preserve "more of the title". Truncate. The id is the unique key, not the slug.
- About to use a slug from the user's verbal title verbatim (e.g., spaces, capitals). Always kebab-case + ASCII.

## Rationalization table

| Excuse | Reality |
|--------|---------|
| "The user said 'accepted' in the same sentence — I'll write `status: Accepted`." | Two operations, two skills, two log entries. Pass `--accept-immediately` and let `transition-adr` do its job — the audit chain tracks proposal-then-acceptance distinctly. |
| "An ADR was deleted last week — I'll reuse its number." | Numbers are never reused. Allocate the next from `manifest.yml`, full stop. |
| "The title is too long for the slug — I'll abbreviate by hand." | Kebab-case the title and truncate to 50 chars. Hand-abbreviation is non-deterministic and unaudittable. |
| "I'll fill in `Context` and `Decision` from the discussion to save the user time." | The ADR body is the user's voice. Provide the scaffolding; let them write. Pre-fills become wrong-but-shipped content. |
| "There's no template — I'll improvise the frontmatter." | The frontmatter contract is the canonical schema in `docs/AGENTS.md` §11.A. Missing fields break `audit-docs` and `transition-adr`. Refuse if the template is missing; tell the user to reinstall the plugin. |
| "The decision needs the exact call signature to be reviewable." | Then it is being reviewed by the wrong instrument — prose review is not a type checker. State the requirement and the postcondition; the dev module's tests review the signature. See `docs/AGENTS.md` §11.D, and its carve-out for when the mechanism IS the decision. |
| "The body is over the line budget, so I'll trim the Alternatives section." | The budget is a tripwire, not a cap. An over-budget body is a prompt to re-read for embedded recipe — remove the recipe, or declare the justification in the form §11.D states. Cutting a real alternative to hit a number is the wrong repair. |
| "The index update is bookkeeping — I'll do it on the next ADR." | The index is queried before the file system on most reads. Drift here means downstream skills see stale state. Update on every write. |
| "I'll skip the log entry because `propose-adr` is implicit." | Every op gets a `log.md` entry. The chronology is the audit; no exceptions. |
| "I'll allocate the number now and write the file when the user finalizes the title." | Pre-allocated numbers with no file are how reused-number bugs happen. Allocate atomically with the write. |

## Common mistakes

- **Off-by-one on `adr.next_number`**: after ADR number 7 is written, the manifest stores `8`, not `7`. Increment after allocation.
- **Reading `manifest.yml` once and writing back stale**: re-read immediately before the increment write to catch races.
- **Forgetting to em-dash empty index columns**: blank columns confuse the table renderer. Use `—`.
- **Updating `docs/index.md` ADR count by reading the index file's previous count**: count the actual files in `docs/adrs/*.md` (excluding `index.md`). Don't trust prior numbers.
- **Slug ending in a hyphen** after truncation: trim trailing hyphens (e.g., `use-sqlite-for-local-first-storag` → trim if the trim point is mid-word and trailing).
- **Including the ADR's own slug as `related_briefs:`**: that field is for BRIEF filenames, not other ADRs. Cross-ADR references live in the body's References section.
- **Writing `deciders: []`**: at least one decider is required. Default to the configured user handle.
- **Confusing `date:` (the "current status entry's date") with `proposed_date:` (immutable first-Proposed date)**: on first write they're equal; on later transitions, only `date:` moves.

## Two-tier counter (SP-4)

When allocating the next `ADR-NNNN` and running the `${ID}-*.md` collision check, scan **both** `<docs_dir>/adrs/` **and** `<docs_dir>/adrs/archive/`, so an archived id is never reissued. Numbers remain monotonic and never reused across either tier.
