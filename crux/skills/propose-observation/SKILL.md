---
name: propose-observation
description: "Scaffold an observation of what code does, without making a decision. Leave its claim body for human authorship."
metadata:
  tags: "observations, scaffolding, bronze, adr-less"
  bundles: "crux-docs"
  risk_level: "low"
  triggers: "propose observation | record an observation | new OBS | observe this | record what the code does | this is observed not decided"
  routing_note: "Writes status: observed only; body is human-authored."
---

# Propose Observation

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

The scaffolding operation for an **observation** — a record of what the code already does, written by a human who read the code (see `docs/AGENTS.md` §17). An observation describes; it does not decide. This is the **reconstructed on-ramp**: the human read the code and wants the fact recorded. The mined on-ramp is `transition-decision ratify --as observation`, which writes the same record shape from a `recover-decisions` candidate.

Each call: allocates the next zero-padded `OBS-NNNN` from `docs/manifest.yml`, computes the record's `anchor_id`, drops a templated file under `docs/observations/` with `status: observed` and `provenance: reconstructed`, adds the concern index row, bumps the `docs/index.md` rollup count, and logs the operation. The human writes the body — this skill provides the scaffolding only.

Three principles:

- **Numbers are precious and forever.** Once `observation.next_number` increments, that number belongs to this record — even if the user later deletes the file. Never reuse. Never decrement.
- **This skill writes exactly one status: `observed`.** It cannot write `ratified`, `rejected`, `retired`, or `decided`. `transition-observation` is the only single-record route to those states, and the batch sign-off (`survey-signoff`) is the only batch route. Both are human-invoked.
- **Scaffold, don't author.** The body is the human's account of what the code does and where the evidence is. A pre-filled body is wrong-but-shipped content.

## When to use

- User says: "propose observation", "record an observation", "new OBS", "observe this", "record what the code does", "this is observed not decided", "write down that the code does X".
- After a human read code and learned a fact nobody decided — the common case in an inherited codebase.
- When `propose-adr` is the wrong instrument because nobody made a decision: the code is the authority and the record describes it.

Do **not** use this skill for:
- Writing the body — the human authors it. Refuse to pre-fill the narrative.
- Transitioning a record's status — that is `transition-observation`. Do NOT hand-edit the lifecycle frontmatter.
- Recording a decision — that is `propose-adr`. An observation that later earns an ADR moves to `decided` through `transition-observation decide`, with `decided_by` naming the ADR.
- Recording a candidate the miner found — that is `transition-decision ratify --as observation`, the mined on-ramp. Both ramps are human-invoked; no scan writes an observation file in any state.
- Prescribing what must be true — that is an invariant (`recover-invariants` / `transition-invariant`).

## Inputs

- `--title "<declarative statement>"` — required. Declarative present tense, stating what the code does, e.g. "The summaries projection hashes governs blocks directly". If omitted, prompt the user; confirm before allocation.
- `--evidence <comma-list>` — required. One or more repo-relative `path:line-range` references, e.g. `src/auth/session.py:40-72`. Each path must resolve on disk. Never a code excerpt.
- `--anchor-kind <kind>` and `--anchor <canonical-anchor>` — required. The structural anchor the record binds to: the kind names the anchor's class and the canonical anchor names it fully qualified, the same form the miner uses. The pair yields the record's `anchor_id` (pipeline §3).
- `--governs-domain <domain>` and `--governs-rule "<rule>"` — required. The domain and the one-sentence rule the record's `governs` entry carries. `--governs-scope "<scope>"` — optional; defaults to the evidence paths.
- `--tags <comma-list>` — optional.
- `--related-invariants <comma-list>` — optional. `INV-NNNN` ids. Default empty.

## The pipeline

Execute in order. Never reorder, never skip. Number allocation must be atomic.

### 0. Resolve per-repo configuration

Run `python3 "${CRUX_PLUGIN_ROOT}/scripts/bionic-config.py"` (compat: `crux-config.py`) from the repo root, or pass `--repo-root <repo-root>`. Confirm the returned `repo_root` is the repo you are operating in. On exit 1, **STOP** and surface the `{"error": ...}` payload — never fall back to defaults. Use the returned `docs_dir` wherever this skill says `docs/` (per the `docs/AGENTS.md` §14 normative definition clause). When `artifact_prefix` is non-empty, the prefixed string (e.g. `CRX-OBS-NNNN`) **IS** `${ID}` for every subsequent step — the filename, the frontmatter `id:`, the index row, wiki-links, and the `log.md` subject all carry it verbatim (§14.3); only the `NNNN` allocation is prefix-blind.

### 1. Read `docs/manifest.yml`

- Verify the file exists. If not, **STOP** — `init-docs` was never run.
- Verify `observations` is in `concerns_enabled`. If not, **STOP** and tell the user how to enable the additive concern by editing a current schema-5 manifest and creating its documented surfaces, or by running `init-docs` on a new tree. The concern needs no `schema_version` bump; `audit-docs --migrate` does not add it.
- Parse YAML. Read `observation.next_number` (an integer ≥ 1).
- Hold this value as `${N}` for the duration of this run.

### 2. Compute the id, slug, and path

- `${ID}` = `OBS-` + zero-padded 4-digit `${N}` (e.g. `${N}`=7 → `OBS-0007`), prefixed per §0.
- `${SLUG}` = kebab-case of `${title}`, ASCII only, truncated to 50 chars. Trim trailing hyphens after truncation (`docs/AGENTS.md` §9).
- **The slug grammar is the two grammars intersected: `^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$`.** The value is the filename stem AND the slug half of the `governs` handle, so it must satisfy both — a lowercase letter, then lowercase letters, digits and single hyphens. A title opening with a number kebab-cases to a digit-led slug such as `3-way-merge`: the filename grammar admits it, the handle anchor refuses it, and the summaries projection then fails closed for the whole tree. If the derived slug is empty or digit-led, **STOP** and ask the user for one this grammar admits. Never repair it silently.
- `${FILE}` = `docs/observations/${ID}-${SLUG}.md`.
- **Collision scan:** glob `docs/observations/*.md` and match each filename against the dual-form regex `([A-Z][A-Z0-9]{1,9}-)?OBS-(\d{4})`. Take the max over the digits capture across bare and prefixed spellings. If that max is ≥ `${N}`, **STOP** with a BROKEN error — the manifest counter is desynced from the directory. Tell the user to run `audit-docs` to reconcile.
- **Slug collision check (different number, same slug): STOP.** The slug half of every live handle is unique across the whole resolver, and a retired slug is never reused either, because a citation of it must keep resolving to the rules that displaced it. Read `docs/adrs/summaries/resolver.json` and refuse when `${SLUG}` is a key of its `slugs` map or of its `retired_slugs` map; when that file does not exist, read the `governs` handles under `docs/observations/*.md` and `docs/adrs/*.md` directly. Name the holder and ask the user for a different slug. Proceeding writes a second live handle on a taken slug, and the projection then refuses to render for every record in the tree — `summarize-adrs` and `compile-doctrine` both stop, not this record alone. The batch sign-off refuses on the same conditions (`docs/AGENTS.md` §17.5), and the single-record ramp is not the lenient one.

### 3. Compute the `anchor_id`

Every observation carries an `anchor_id`, the reconstructed ramp included — it is what lets a re-mine recognize a fact already recorded and propose no duplicate candidate. The human names the anchor; the id hashes under the same function the mined ramp uses. Run from the plugin's `scripts/` directory:

```
cd "${CRUX_PLUGIN_ROOT}/scripts" && CRUX_ANCHOR_KIND="${ANCHOR_KIND}" CRUX_ANCHOR="${ANCHOR}" python3 -c 'import os; from crux.arch.recover import candidate_id; print(candidate_id(os.environ["CRUX_ANCHOR_KIND"], os.environ["CRUX_ANCHOR"]))'
```

**The anchor text reaches this command through the environment, never through the program string, and that is not a style choice.** `${ANCHOR}` is repo-derived for the `api-contract`, `system-of-record`, and `cross-cutting-policy` kinds — it is scanned content, not something the human typed. Substituted into a `python3 -c` program it is executable code: a crafted anchor closes the quote, runs anything under your permissions, and still prints a plausible 16-hex id, so the run looks ordinary. That single substitution voids the entire writer boundary, because every control in §17.2 is prose you follow and injected code writes `status: ratified` straight into a record. Passed through the environment the same text is inert data. `crux/scripts/tests/test_skill_prose_safety.py` fails any `SKILL.md` that interpolates a shell placeholder into an inline program string.

Hold the printed value as `${ANCHOR_ID}`. **Duplicate check:** the uniqueness rule and the `anchor_id` shape are stated in `docs/AGENTS.md` §17.2/§17.3 (CHK-OBS-ANCHOR) and checked by `check_observations.py`; read them there rather than from memory. Apply the rule here before writing: if any record under `docs/observations/` already carries this `anchor_id` in `observed` or `ratified`, **STOP** and name it — the fact is already recorded. A changed claim on the same anchor is a successor record, and the human ratifies the successor and retires its predecessor through `transition-observation`; it is never an edit of the existing record.

### 4. Read the observation template

- Read `${CRUX_PLUGIN_ROOT}/templates/OBS-template.md`. The template carries the full frontmatter shape — substitute into it; do not re-author the field list from memory.
- **The canonical frontmatter contract — which fields exist, their types, and required/optional — lives in the tree's operational schema, `docs/AGENTS.md` §17.1 "Canonical observation frontmatter schema"** (the single source of truth). This skill does NOT restate the field list (it would drift); read §17.1. The three constraints §17.1 places on the `governs` entries — provenance agreement, the id-namespaced handle, and the unused `anchor` sub-field — are enforced there; apply them by reading them, not from memory.
- Substitute the values for the new record: `id` → `${ID}`, `title` → `${title}`, `status` → `observed`, `provenance` → `reconstructed`, `anchor_id` → `${ANCHOR_ID}`, `evidence` → the passed references, the `governs` entry from the passed domain/rule/scope with its handle namespaced as `${ID}/<rule-slug>`, `date` and the first-written date → `${TODAY}`, and the passed tags and related invariants (default `[]`). Every lifecycle date that §17.1 marks as set on a later transition is `null`; `decided_by` is `null`. For the exact field set, defer to §17.1 and the template — they are authoritative.
- Substitute the body heading `# OBS-NNNN — <Title>` → `# ${ID} — ${title}`.
- Leave the body sections with their stub content from the template. The human fills them in. The body carries no code excerpt; a `path:line-range` in `evidence` is the pointer.

### 5. Write the file

- Write to `${FILE}` exactly the substituted template.
- If the write fails, **STOP** — do not increment the counter. The number stays unallocated.

### 6. Increment `observation.next_number` atomically

- Re-read `docs/manifest.yml` to confirm `observation.next_number` is still `${N}`. If it changed, another invocation raced us — STOP and tell the user to retry.
- Set `observation.next_number = ${N} + 1`.
- Write `manifest.yml` back. Preserve all other keys and formatting.

Step 10 commits the counter bump with the record file it allocated, and only then runs the regenerators.

### 7. Add the row to `docs/observations/index.md`

- Insert the record's row at the top of the table body (newest first), in the column order the index header carries. The index **visibly marks** the status: an `observed` record is survey debt and must be legible as such, never hidden.
- If the index does not exist yet (first record), create it carrying the same header `init-docs` seeds — `| id | status | provenance | domain | evidence | anchor_id | related invariants |` — and one row. One header, seeded in one place and extended here; a second spelling would make the concern index unreadable across trees.
- Update the `_Last updated:_` line to `${TODAY}`.

### 8. Update the `docs/index.md` rollup

- Bump the count: `## Observations (N)` where `N` is the number of files matching `OBS-*.md` under `docs/observations/` (count files, not the index).
- If no `## Observations` section exists yet, create it after the `## Invariants` section when one exists, else after `## Briefs` (see `docs/AGENTS.md` §5).
- Update the `_Last updated:_` line to `${TODAY}`.

### 9. Append to `docs/log.md`

Prepended (newest first), using the `observation` op from the `docs/AGENTS.md` §6 canonical enum:

```
## [${TODAY}] observation | ${ID}: created (observed)
```

Body, 1–2 lines: the title, the file path, `provenance: reconstructed`, the `anchor_id`. Note the body is human-authored next.

### 10. Commit, then regenerate the projections

While a tester's window is open, commit nothing and run no regenerator. The window runs from the tester's dispatch until the tester returns; outside a run there is none.

Commit this skill's own writes first: the record file, `manifest.yml`, `observations/index.md`, `docs/index.md` and `log.md`. Never commit the counter bump without the file it allocated. Stage only these paths (`git add -- <paths>`) and commit only them (`git commit -- <paths>`), so no change already staged is included. If a regenerator still exits 2 with `migration-input-not-committed`, another uncommitted input is in the tree: stop and name it; never commit a file this skill did not write.

Then regenerate the summaries projection, then the doctrine projection, because the new record and the bump change their input hash. The commit comes first because, in a tree whose projections declare migration inputs, a regenerator refuses an uncommitted input with exit 2 (`migration-input-not-committed`).

```
uv run "${CRUX_PLUGIN_ROOT}/scripts/summarize-adrs.py" --repo-root <repo-root>
uv run "${CRUX_PLUGIN_ROOT}/scripts/compile-doctrine.py" --repo-root <repo-root>
```

Confirm `--dry-run` of each exits 0, then commit the regenerated projections so none stays uncommitted.

### 11. Hand off to the user

- Tell the user: observation id assigned, file path, status `observed`, `anchor_id`.
- Explicitly instruct: the body is yours to write — what the code does, where the evidence is, and why this is observed rather than decided. Do NOT change the frontmatter.
- Name `transition-observation` as the only single-record route out of `observed`: `ratify` when the record describes the code, `reject` when it does not. Name `survey-signoff` as the only batch route, for a human signing off many records under one receipt. A ratified record's claim is immutable from then on.

## Verification checklist

- [ ] `docs/observations/${ID}-${SLUG}.md` exists.
- [ ] Frontmatter parses as YAML and its keyset matches the template's, which matches `docs/AGENTS.md` §17.1.
- [ ] `id:` matches the filename's `${ID}`.
- [ ] `status: observed` and `provenance: reconstructed` — no other status was written.
- [ ] `anchor_id` equals the value `candidate_id` printed in §3, and no other non-terminal record carries it.
- [ ] Every `evidence` entry is a `path:line-range` whose path resolves; none is a code excerpt.
- [ ] The `governs` entry's `provenance` equals the record's, and its handle is namespaced by `${ID}`.
- [ ] `${SLUG}` matches `^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$` and is a key of neither the `slugs` map nor the `retired_slugs` map in `docs/adrs/summaries/resolver.json`.
- [ ] The body is the template stub — NOT pre-filled narrative.
- [ ] `docs/manifest.yml` `observation.next_number` is `${N} + 1`.
- [ ] `docs/observations/index.md` has the row at the top, visibly marked `observed`.
- [ ] `docs/index.md` `## Observations (N)` count matches the actual file count; `_Last updated:_` is `${TODAY}`.
- [ ] `docs/log.md` has a new `## [${TODAY}] observation | ${ID}:` entry at the top.
- [ ] The record, the counter bump and the index, docs-index and log writes are committed before any regenerator runs; `summarize-adrs.py --dry-run` and `compile-doctrine.py --dry-run` exit 0 afterwards, and the regenerated projections are committed.
- [ ] No ADR was created or modified, and no existing observation was touched.

## Red flags — STOP and reconsider

- About to write the file before incrementing `observation.next_number`. Order matters: write first (so a failed write does not burn a number), then increment.
- About to allocate a number the collision scan already found on disk. The manifest is out of sync with reality; refuse and tell the user to audit.
- About to write `status: ratified` because the user said "this is definitely true". This skill writes `observed` only. Ratification is a separate human act through `transition-observation`, and a scan or a scaffold that ratifies is the failure the writer boundary exists to prevent.
- About to proceed past a slug another record already holds because the number differs. The slug half of a handle is unique across the whole resolver, retired slugs included. A second live handle on one slug fails the summaries projection closed for every record in the tree, so `summarize-adrs` and `compile-doctrine` refuse to regenerate anything. Refuse and take a different slug.
- About to publish a digit-led slug because the filename reads fine. `OBS-0007-3-way-merge.md` is a legal filename and `OBS-0007/3-way-merge` is not a legal handle; the projection fails closed for the whole tree on the handle. Ask for a slug that starts with a lowercase letter.
- About to omit `anchor_id` because "this record was not mined". Every record carries one. Without it a re-mine cannot recognize the fact and proposes a duplicate.
- About to paste a code excerpt into `evidence` or the body. Evidence is a `path:line-range`, and the body carries no excerpt either.
- About to edit an existing record whose `anchor_id` matches. A changed claim is a successor record; the existing one is retired through the gate, never rewritten.
- About to list the frontmatter fields from memory because the template is missing. Refuse; tell the user to reinstall the plugin. §17.1 is the contract and the template carries it.
- About to use the `adr`, `invariant`, or `journal` op for the log entry. Scaffolding an observation is an `observation` op.
- About to substitute `${ANCHOR}` (or any other repo-derived value) into a `python3 -c` program string because it reads more simply. That is arbitrary code execution from scanned repository content, and the run still prints a plausible id, so nothing looks wrong. Pass the value through the environment or `argv`. Step 3 carries the only correct spelling.

## Rationalization table

| Excuse | Reality |
|--------|---------|
| "The user read the code and is sure — I'll write it ratified to save a step." | This skill writes `observed` and nothing else. `ratified` is reachable only by an explicit, per-record human transition. That is the safety property. |
| "This wasn't mined, so `anchor_id` doesn't apply." | Every record carries an `anchor_id`, reconstructed ones included. The human names the anchor and it hashes under the miner's function, so one identity spans both on-ramps. |
| "Another record has this slug, but the number is different, so the file is unique." | The filename is unique and the handle is not. The slug half of every live handle is unique across the resolver, and a retired slug stays reserved for the rules that displaced it. Two live handles on one slug fail the summaries projection closed for the whole tree. |
| "An observation was deleted last week — I'll reuse its number." | Numbers are never reused. Allocate the next from `manifest.yml`, full stop. |
| "I'll fill in the body from what the user told me." | The body is the human's account. Provide the scaffolding; let them write. |
| "This is really a decision — I'll write it as an observation anyway to skip the ADR." | An observation describes; an ADR decides. If the human decided it, `propose-adr`. If the human wants the observation to become a decision later, `transition-observation decide` links the two. |
| "The evidence is a one-line function — I'll quote it, it's clearer." | Never a code excerpt. A `path:line-range` is the pointer; the code is the authority and stays where it is. |
| "I'll skip the index row and the rollup — it's bookkeeping." | The index visibly carries survey debt, and `audit-docs` reads the rollup count. Update on every write. |

## Common mistakes

- **Off-by-one on `observation.next_number`**: after `OBS-0007` is written, the manifest stores `8`. Increment after allocation.
- **Reading `manifest.yml` once and writing back stale**: re-read immediately before the increment write to catch races.
- **Scanning only the bare `OBS-NNNN` filenames** in a prefixed tree: the dual-form regex covers both spellings; take the max across them.
- **Setting `provenance: recovered`** on this ramp: the human reconstructed the fact from reading; `recovered` is the mined ramp's value.
- **Setting a transition date on first write**: only `date` and the first-written date are `${TODAY}`; every other lifecycle date is `null` until its transition.
- **Slug ending in a hyphen** after truncation — trim it.
- **Updating the `docs/index.md` count by incrementing the prior number**: count the actual files.

## See also

- `transition-observation` — the human gate; the only single-record route to `ratified`, `rejected`, `retired`, or `decided`.
- `survey-signoff` — the human gate over a batch; the only batch route to those same states.
- `transition-decision` — the mined on-ramp (`ratify --as observation`) that writes the same record shape from a `recover-decisions` candidate.
- `recover-decisions` — the miner whose `anchor_id` function this skill reuses.
- `propose-adr` — the decision artifact; an observation that earns one moves to `decided`.
- `audit-docs` — the CHK-OBS rules (`docs/AGENTS.md` §17.3) that read what this skill writes.
- `docs/AGENTS.md` §17 — the observations-concern contract (§17.1 frontmatter schema, §17.2 lifecycle and writer boundary, §17.3 audit rules).
- The observation-record decision this implements (see the ADR log).
- Template: `crux/templates/OBS-template.md`.
