---
name: cleanup-campsite
description: "Scan project process state and refresh prioritized next actions. Finds unfinished work, not documentation graph violations."
metadata:
  tags: "cleanup, hygiene, whats-next, scan, forward-looking"
  bundles: "crux-docs"
  risk_level: "low"
  triggers: "cleanup | hygiene check | what's next | clean up the docs | refresh whats_next"
  routing_note: "Forward-looking process-state scan; regenerates `docs/whats_next.md`. Distinct from `audit-docs` — proposes actions, never edits prose."
---

# Cleanup

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

Cleanup is the **forward-looking** counterpart to `audit-docs`. Two skills, two questions:

| Skill | Question it answers |
|---|---|
| `audit-docs` | *Is the docs graph internally consistent?* (schema, frontmatter, cross-refs, indexes, rollups) |
| `cleanup-campsite` | *Given a consistent graph, what work should we be doing next?* (process-state staleness, forward actions) |

The output is `docs/whats_next.md` — a regenerated, prioritized action backlog with citation-bearing entries. The artifact answers the session-start question: *"what should I do first today?"*

Cleanup does NOT invoke `audit-docs` internally — different cadence (session-start vs. before-release), different exit semantics (audit's BROKEN findings block; cleanup's are suggestions), different write footprint. Cleanup CHECKS whether audit has run recently and emits a P1 "run audit-docs" suggestion if it hasn't.

Cleanup ships **20 implemented scan rules** (CLN-* IDs) plus **one reserved id** for v0.2 (`CLN-XR-1`) and **two retired ids**, for 23 `CLN-*` ids total. The 20 implemented rules: `CLN-PJ-1a`, `CLN-PJ-1b`, `CLN-PJ-1c`, `CLN-PJ-2` (one rule, three sub-findings — the version-mismatch check now spans both the Claude bare manifest and the Codex plugin manifest, since both manifests joined the same version lock-step, still counted as a single rule id); `CLN-ADR-2`, `CLN-ADR-4`, `CLN-ADR-5` (decision-review cadence); `CLN-OBJ-1` (objectives unpopulated); `CLN-PB-1`, `CLN-PB-2`, `CLN-PB-3`, `CLN-PB-4`; `CLN-JR-1`, `CLN-JR-2`; `CLN-WN-1`; `CLN-AUD-1` (audit-recency); `CLN-FG-1` (forged-skill staleness); `CLN-FG-2` (used-but-never-evaluated); `CLN-RETRO-1` (retrospective cadence); `CLN-TMPL-1` (dogfood↔template clause parity). (`CLN-AUD-1` was formerly the un-prefixed `cleanup-audit-stale` "not-a-CLN-rule" check; it now carries a standard rule id so the count is exact and the verification checklist needs no special-case exemption.) **Retired rule ids (never reused, never reimplemented):** `CLN-ADR-1`, `CLN-ADR-3`. See their stubs below for why. The count dropped by one for each rather than being backfilled, and `CLN-WN-1`'s stale-dismissal check skips any dismissal keyed to a retired id — those records are history that is being kept on purpose, not dead weight to age out.

### What cleanup writes vs. what it proposes

**Cleanup writes (mechanical, low-risk):**
- `docs/whats_next.md` — its primary artifact (body fully regenerated on every run; only the `dismissed:` frontmatter list persists across runs).
- One `cleanup-campsite` op entry in `docs/log.md` per run.
- A `## What's next (N)` section in `docs/index.md` at the top (before `## Research`), when `suggestions_open > 0`. Removes the section when `N = 0`.

**Cleanup proposes (writes to whats_next.md, not the target file):**
- README / USER_GUIDE / CHANGELOG edits.
- ADR body edits (frozen after Accepted anyway — never touch).
- Journal entries (per the v0.2.0 log-work skill — reflection cannot be auto-generated).
- Promptbook file edits (run-promptbook owns those).
- `manifest.yml` counter changes (no v0.1 rule emits this — listed for safety so future rules don't quietly enable it).

## When to use

- User says: "cleanup", "hygiene check", "what's next", "clean up the docs", "refresh whats_next".
- Start of a session, especially after a long gap or a context-switch.
- After a flurry of operations (multiple ADRs accepted, promptbooks completed) to catch what should be reflected in human-facing docs and forward-action lists.
- Before a release, alongside (not instead of) `audit-docs`.

Do **NOT** use this skill for:
- Schema-integrity validation — that's `audit-docs`.
- Resolving findings cleanup surfaces — invoke the relevant downstream skill (`archive-promptbook`, `log-work`, `transition-adr`, etc.).
- Editing prose surfaces directly — cleanup proposes; humans (or Claude under explicit direction) execute.

## Inputs

This skill takes no required arguments. Optional behavior:

- **`--only <RULE-ID>[,<RULE-ID>...]`** — run ONLY the named rule(s). This is a first-class named sub-deliverable, not a debug flag.

  **Contract:**
  - Accepts a single CLN rule id (e.g. `CLN-TMPL-1`) or a comma-separated CSV list (e.g. `CLN-TMPL-1,CLN-PJ-2`). No spaces around commas.
  - Runs ONLY the named rule(s) over the same shared checker / scan logic as a full run — the rule's setup requirements (threshold resolution, dismissal loading) still execute; no rule-specific setup state is skipped.
  - Output is scoped to the named rules: `docs/whats_next.md` is regenerated for ONLY the named-rule slice. The prior body's findings for other rules are **not clobbered** — only the entries for the named rule(s) are replaced in place. (Implementation: read the prior `docs/whats_next.md` body, remove entries whose `source:` cites a named rule, inject the new findings for those rules in severity/id order, and write back. Findings from unscoped rules are carried forward verbatim.)
  - The normal `cleanup-campsite` log op is STILL emitted to `docs/log.md` on every `--only` run (the regenerate-and-propose + log contract is not bypassed). The log entry body names the scoped rule(s): `N open (X P1, Y P2, Z P3) [--only CLN-TMPL-1], M closed, K dismissed`.
  - **Unknown rule id → loud error (NOT a silent no-op).** If any rule id in the list is not a recognized CLN-* id, cleanup refuses to run and prints: `ERROR: unknown rule id '<ID>'. Known rule ids: CLN-PJ-1a, CLN-PJ-1b, …`. Exit non-zero. This prevents a silent empty-output run from masking a typo.
  - **Rule-harness isolation:** each named rule runs in isolation over the shared checker / scan logic without missing required setup state (thresholds and dismissals are loaded in step 1/2 even under `--only`) and without causing side effects on unscoped rules.
  - `cleanup-campsite --only CLN-TMPL-1` is the documented **on-demand fast-path** for immediate parity feedback right after editing `docs/AGENTS.md` — fast because only one rule runs against two files, no full vault walk.

- **Configuration in `docs/manifest.yml`** under an optional `cleanup:` block:
  - `adr_proposed_stale_days` — default 14. Used by `CLN-ADR-2`.
  - `stuck_promptbook_days` — default 14. Used by `CLN-PB-1`.
  - `audit_stale_days` — default 14. Used by the audit-recency check.
  - `forged_skill_stale_days` — default 30. no authored/revised/used/evaluated event within this window (timestamp comparison). Used by CLN-FG-1 and CLN-FG-2.
  - `retro_due_runs` — default 5. Used by `CLN-RETRO-1`.
  - `adr_review_due_days` — default 7. Used by `CLN-ADR-5`.

  Missing values fall back to the defaults. The `cleanup:` block itself is optional.

## The pipeline

Execute in order.

### 1. Resolve thresholds

Read `docs/manifest.yml`. If a `cleanup:` block exists, take its thresholds. Otherwise use defaults (`adr_proposed_stale_days: 14`, `stuck_promptbook_days: 14`, `audit_stale_days: 14`, `forged_skill_stale_days: 30`, `retro_due_runs: 5`, `adr_review_due_days: 7` — the last is `CLN-ADR-5`'s threshold). Compute `${TODAY}` (ISO date) and `${NOW}` (ISO 8601 UTC timestamp).

### 2. Load prior dismissals

Read `docs/whats_next.md` if it exists. Parse the frontmatter. Extract the `dismissed:` list — a list of `{id, dismissed_at, reason}` entries.

Also extract the prior body's open-suggestion `id:` set (call it `prior_open_ids`). Used for "Recently closed" derivation.

If `docs/whats_next.md` does not exist, treat both as empty.

### 3. Run scan rules in order

Run each rule. For each finding, compute its stable `id` per the rule's spec. If the `id` is in the `dismissed:` list, skip surfacing.

Rules — all are file-scoped; no skill invocations:

**Existence gate (all CLN-PJ rules):** the CLN-PJ rules inspect plugin/marketplace manifests (`crux/plugin.json`, `crux/.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `crux/.codex-plugin/plugin.json`, `.agents/plugins/marketplace.json`) that exist only in a repo that IS a plugin/marketplace checkout — a typical consuming project has none of them, and that is normal. Before running each CLN-PJ rule, check that the file(s) it reads exist; if a rule's primary input is absent, the rule **silently skips** (no finding, no warning). The one exception is CLN-PJ-2's missing-file handling below, which fires only when a *sibling* manifest proves the repo is a plugin-marketplace repo.

#### CLN-PJ-1a — plugin.json version vs. README/USER_GUIDE version mentions

- **Reads:** `crux/plugin.json`, `README.md`, `USER_GUIDE.md`.
- **Looks for:** literal version strings (regex `v?\d+\.\d+\.\d+`) in README/USER_GUIDE that don't match `plugin.json.version`. Ignore version strings inside fenced code blocks unless they're in install snippets.
- **Stable id:** `cleanup-CLN-PJ-1a-plugin-version`
- **Category:** `version-drift`. **Severity:** P2.
- **Proposed action:** "Update README/USER_GUIDE version string to `<plugin.json.version>`."

#### CLN-PJ-1b — plugin.json `len(skills)` vs. "N skills" claims

- **Reads:** `crux/plugin.json`, `README.md`, `USER_GUIDE.md`, `AGENTS.md` (root).
- **Looks for:** patterns like `\d+ skills` or `\bfifteen\b skills?`, `\bsixteen\b skills?`, etc., that don't match `len(plugin.json.skills)`.
- **Stable id:** `cleanup-CLN-PJ-1b-skill-count`
- **Category:** `version-drift`. **Severity:** P2.
- **Proposed action:** "Update skill count to `<actual>` in `<file path>`."

#### CLN-PJ-1c — README install snippet `--ref` pinned to outdated tag

- **Reads:** `README.md` (look for `--ref \S+` in shell snippets), `crux/plugin.json`.
- **Looks for:** `--ref v\d+\.\d+\.\d+` whose version doesn't match `plugin.json.version`.
- **Stable id:** `cleanup-CLN-PJ-1c-install-ref`
- **Category:** `version-drift`. **Severity:** P2.
- **Proposed action:** "Update install-snippet `--ref` to `v<plugin.json.version>` (or to `main` if release pinning isn't intentional)."

#### CLN-PJ-2 — marketplace + plugin manifest drift

- **Reads:** `crux/plugin.json`, `crux/.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `crux/.codex-plugin/plugin.json`, `.agents/plugins/marketplace.json`.
- **Layout note:** Per the Claude Code marketplace spec, the marketplace catalog (`marketplace.json`) lives at the **marketplace root** — for this repo that's the repo root, since the repo IS the marketplace. The plugin manifest (`plugin.json`) lives inside the **plugin's own directory** — `crux/.claude-plugin/plugin.json`. The two files are NOT colocated. The two-line contract: the two manifests stay version-locked; marketplace.json lives at the marketplace root, the bare plugin.json inside the plugin directory. The Codex install pair mirrors this exact layout split (`crux/.codex-plugin/plugin.json` inside the plugin directory, `.agents/plugins/marketplace.json` at the marketplace root) and joins the same version lock-step.
- **Why this rule exists:** Claude Code's plugin loader reads `crux/.claude-plugin/plugin.json` (a bare manifest with only `name`/`description`/`version`/`homepage`) — separate from the rich `crux/plugin.json` that drives the catalog layer. Codex's plugin loader reads `crux/.codex-plugin/plugin.json` in parallel. All version-bearing manifests MUST stay in lock-step on `version` (four surfaces total: `crux/plugin.json`, `crux/.claude-plugin/plugin.json`, `crux/.codex-plugin/plugin.json`, `pyproject.toml`) — `sync.sh`'s refuse-first check enforces this across all four at release time; this rule is the docs-side regression tripwire for hand edits or workflow drift between releases.
- **Looks for (three sub-checks; each emits its own finding):**
  - **(a) version mismatch.** `crux/plugin.json.version` is the reference. Two independent comparisons, each a separate finding when it fails:
    - **(a-i) bare Claude manifest.** `crux/.claude-plugin/plugin.json.version` is not byte-equal to `crux/plugin.json.version`.
      - **Stable id:** `cleanup-CLN-PJ-2a-marketplace-plugin-version`
      - **Category:** `version-drift`. **Severity:** P2.
      - **Proposed action:** "Sync `crux/.claude-plugin/plugin.json.version` to `<crux/plugin.json.version>`. Versions are bumped by hand at release time and the release script refuses to publish on any mismatch, so drift here means a partial hand edit — finish the bump by hand."
    - **(a-ii) Codex plugin manifest.** `crux/.codex-plugin/plugin.json.version` is not byte-equal to `crux/plugin.json.version`.
      - **Stable id:** `cleanup-CLN-PJ-2a-codex-plugin-version`
      - **Category:** `version-drift`. **Severity:** P2.
      - **Proposed action:** "Sync `crux/.codex-plugin/plugin.json.version` to `<crux/plugin.json.version>`. Same lock-step discipline as the bare Claude manifest — finish the bump by hand; `sync.sh` refuses to publish on any of the four surfaces mismatching."
  - **(b) marketplace owner placeholder.** `.claude-plugin/marketplace.json` `owner.name` matches any of the following case-insensitive predicates: equals `"local"`; equals `"todo"`; contains `"placeholder"`; contains `"arc-search"` (the prior placeholder left over from this file's earlier life).
    - **Stable id:** `cleanup-CLN-PJ-2b-marketplace-owner-placeholder`
    - **Category:** `version-drift`. **Severity:** P3.
    - **Proposed action:** "`.claude-plugin/marketplace.json` `owner.name` is a placeholder (`<current value>`). Set it to the canonical owner that matches the GitHub org in `crux/plugin.json.homepage` (currently `idyll`)."
- **Schema-rich caveat (Codex pair only):** unlike the deliberately bare Claude Code pair, the Codex plugin manifest (`crux/.codex-plugin/plugin.json`) is **schema-rich by design** — it legitimately carries a `skills` path pointer (`"./skills/"`), an `interface` block (display name, descriptions, category, capabilities, default prompts), and package metadata (author, keywords, license, repository). None of that is drift; do not flag it and do not propose stripping it to match the bare Claude manifest's minimalism. The **narrower** prohibition this rule enforces is: neither Codex manifest may duplicate the rich manifest's catalog **arrays** — the enumerated `skills:`/`scripts:` arrays of `crux/plugin.json` remain the catalog layer's sole home and must never be copied into `crux/.codex-plugin/plugin.json` or `.agents/plugins/marketplace.json`. If a future rule needs to check for that duplication, it is a new finding under this same rule, not a "make it bare" proposal.
- **Intentionally not checked here:** description and name drift among the manifests. Wording often legitimately differs slightly (one summarizes for a CLI list, another for a marketplace UI, another for the Codex schema's `interface` block). Cleanup is not the right place for fuzzy prose matching; if a name divergence ever ships (`name` ≠ `"crux"` in any manifest) it would be caught by `validate-catalog.py` not by cleanup. The contract is two lines per pair: the manifests in each pair stay version-locked; each marketplace file lives at the marketplace root, each bare/plugin manifest inside the plugin directory.
- **Missing-file handling:** if any of `crux/.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `crux/.codex-plugin/plugin.json`, or `.agents/plugins/marketplace.json` does not exist **while at least one sibling manifest of the five does exist** (proof this is a plugin-marketplace repo with an incomplete manifest set), emit a single P1 finding with stable id `cleanup-CLN-PJ-2-missing-<basename>` proposing recreation per the contract above (each pair's manifests stay version-locked; the marketplace file at the marketplace root, the plugin manifest inside the plugin directory). These files are required for Claude Code / Codex marketplace discovery respectively. If none of the five manifests exist, this is a normal consuming repo — CLN-PJ-2 silently skips per the existence gate.

#### CLN-ADR-1 — RETIRED. Do not reimplement.

`CLN-ADR-1` reported an Accepted ADR, within 30 days of acceptance, whose id appeared in neither `README.md` nor `USER_GUIDE.md`. The id is **retired** and the rule is gone. It enforced a convention this project never kept: a human-facing doc mentions an ADR when the decision surfaces in something a user does, and most decisions surface as a release line in `CHANGELOG.md` instead. The finding count grew from 14 to 32 between 2026-08-17 and 2026-08-31, every one P2, none ever dismissed and none ever acted on — a rule that only ever accumulates is measuring a convention that does not exist.

The rule read exactly one relation — accepted-ADR id versus two prose files — so no narrowing survives: the writing rules already require an ADR in human-facing prose to be a footnote, not an inline id, which is the opposite of what the rule looked for. Retired outright on 2026-08-31 by the owner. Its standing findings closed with it; `CLN-ADR-2` and `CLN-ADR-4` keep the `adr-review` / `adr-followon` categories alive.

The id is not reused. This stub exists so a future reader finds the reasoning instead of the gap.
#### CLN-ADR-2 — Proposed ADRs past their shelf life

- **Reads:** every `docs/adrs/ADR-NNNN-*.md` file, parse frontmatter.
- **Looks for:** `status: Proposed` with `proposed_date` older than `adr_proposed_stale_days` (default 14).
- **Stable id:** `cleanup-CLN-ADR-2-ADR-<NNNN>`.
- **Category:** `adr-review`. **Severity:** P1.
- **Proposed action:** "Decide ADR-NNNN: accept (`transition-adr accept ADR-NNNN`), deprecate (abandon), or supersede with a fresh ADR."

#### CLN-ADR-4 — Phantom ADR-id references in human-facing docs

- **Reads:** `README.md`, `USER_GUIDE.md`, `AGENTS.md` (repo root). Also enumerate existing ADRs by globbing `docs/adrs/ADR-NNNN-*.md`.
- **Looks for:** ADR-id pattern `\bADR-\d{4}\b` in prose (NOT inside `[[wiki-links]]` — those are audit-docs's domain). For each match, check whether `docs/adrs/<id>-*.md` exists. Missing → finding.
- **Stable id:** `cleanup-CLN-ADR-4-<ADR-id>-in-<file-basename>` (one per phantom ref per file).
- **Category:** `adr-followon`. **Severity:** P2.
- **Proposed action:** "`<file>` references `<ADR-id>` but no such ADR exists. Either fix the typo (likely a single-digit slip from a real neighboring id), correct to a real id, or remove the citation."

#### CLN-ADR-3 — RETIRED. Do not reimplement.

`CLN-ADR-3` reported an Accepted ADR whose body prose cited a file path absent from disk. The id is **retired** and the rule is gone. Reading a frozen body against the present tree asks the wrong question: a body past `Proposed` is frozen by contract, so a path it cites records what was true when the decision was made. That is history, not drift.

The rule read exactly one surface — Accepted ADR bodies — so exempting them retired it outright rather than narrowing it. The standing dismissals it accumulated stay in `docs/whats_next.md` as history; they are not migrated and not re-raised. `docs/AGENTS.md` §11.D states the rule this retirement implements, and the pointer-durability postcondition that keeps NEW bodies safe going forward. `CLN-ADR-4` is unaffected: it reports a phantom ADR **id** in human-facing docs, a live surface, not a path inside a frozen body.

The id is not reused. This stub exists so a future reader finds the reasoning instead of the gap, and so nobody re-adds the rule from the dismissal ledger.

#### CLN-ADR-5 — the decision review is overdue

- **Reads:** `docs/adrs/reviews/*.md`, taking the newest filename date. The derived index in that directory is not a report; skip it.
- **Looks for:** no review at all, or a newest review whose filename date is older than `adr_review_due_days` (default 7).
- **Cadence anchor:** the newest report's **filename date**, ignoring any filename that is not an ISO calendar date or whose date lies in the future — so a future-dated file cannot suppress this nudge.
- **Stable id:** `cleanup-CLN-ADR-5-due` (singleton — the finding is the cadence state as a whole, not a per-report artifact).
- **Category:** `adr-review`. **Severity:** P2.
- **Proposed action:** "Run `review-decisions` — the newest decision review is <N> days old against a threshold of <T> days." With no review on disk: "Run `review-decisions` — no decision review has been filed; the threshold is <T> days."
- **Actor boundary:** proposes only. Never writes under `docs/adrs/reviews/` and never invokes `review-decisions`. The review is the architect's act.

#### CLN-OBJ-1 — the objectives file is unpopulated

- **Reads:** `docs/objectives.md` frontmatter.
- **Looks for:** the file is missing, or its frontmatter says `maturity: placeholder`. Any other `maturity` value is silent here — the `audit-docs` CHK-OBJ rules own the file's shape and its review age.
- **Stable id:** `cleanup-CLN-OBJ-1-objectives` (singleton — one file, one finding).
- **Category:** `objectives`. **Severity:** P2.
- **Proposed action:** "Populate `docs/objectives.md` per `docs/AGENTS.md` §5.B, then set `maturity`." With no file on disk, name the gap the same way; `init-docs` seeds the placeholder.
- **Actor boundary:** proposes only. This rule is the objectives nudge that `docs/AGENTS.md` §5.B names under its populate gate. It never seeds, edits, or bumps the file, because humans own its content.

#### CLN-PB-1 — promptbooks with stale run-snapshot mtime

- **Reads:** every promptbook in `docs/promptbooks/active/` (glob **both** `PB-NNNN-*.yaml` and `PB-NNNN-*.md` — format coexistence), look up its `current_run` snapshot's file mtime AND parse out the latest per-prompt completed/started timestamp from the snapshot — the structured `prompts[].completed`/`started` fields on a `.yaml` run, or the `Completed:` / `Started:` block fields on a `.md` run.
- **Looks for:** **latest in-body timestamp** older than `${TODAY} - stuck_promptbook_days` (default 14) AND `current_prompt` is non-null. Use the in-body timestamp rather than filesystem mtime — filesystem mtime is reset by `git checkout` / fresh clones, which would false-negative on a stuck book in a fresh tree. In-body timestamps are part of the immutable run snapshot.
- **Exemption — self-stuck guard:** If cleanup is being invoked as part of an active cycle run (i.e., the current cleanup invocation's session has the book id in its parent context), the book is exempt from CLN-PB-1. Without conversation context, fall back to: if the book's latest in-body timestamp is within the past `stuck_promptbook_days/2` (default 7 days), assume the book is actively progressing and exempt. This prevents long single-phase cycles (e.g., a 21-day council deliberation) from tripping themselves.
- **Stable id:** `cleanup-CLN-PB-1-<book-id>`.
- **Category:** `promptbook-hygiene`. **Severity:** P1.
- **Proposed action:** For a YAML run, resume with `run-promptbook`, truthfully mark the current prompt blocked/skipped, or deliberately abandon the YAML run and author a successor. For a Markdown run, use pinned public `v3.23.2` only if the remaining prompts can truthfully finish, then archive and convert book before run. If it cannot finish, preserve its bytes as readable, unresolved history and start separate YAML work; neither version has a verified deliberate Markdown-abandon route.

#### CLN-PB-2 — promptbooks with escalation text and no journal follow-up

- **Reads:** every active promptbook (glob **both** `PB-NNNN-*.yaml` and `PB-NNNN-*.md` — format coexistence); for its `current_run` snapshot, read the notes + per-prompt result text — the `notes`/`prompts[].result` fields on a `.yaml` run, or the `## Notes` / per-prompt `Result:` fields on a `.md` run. `current_run` survives to archival on both the completed and the abandoned path, so this snapshot is reachable for every book pending archival, not only in-progress ones. Also read `docs/journal/YYYY-MM.md` files within the last 30 days.
- **Looks for:** matches of regex `/STOP and escalate|3-round|3 full loops/` in run snapshot text. For each match, check whether any journal entry in the last 30 days contains the book id. If no follow-up, finding.
- **Stable id:** `cleanup-CLN-PB-2-<book-id>`.
- **Category:** `promptbook-hygiene`. **Severity:** P1.
- **Proposed action:** Journal the escalation and its resolution. A YAML run may then resume or be deliberately abandoned. A Markdown run may finish truthfully through pinned public `v3.23.2`; if it cannot, preserve it unresolved and begin separate YAML work. Do not promise Markdown abandonment.

#### CLN-PB-3 — promptbooks eligible for archive

- **Reads:** every active promptbook's `current_run` snapshot (enumerate active books by globbing **both** `PB-NNNN-*.yaml` and `PB-NNNN-*.md` — format coexistence; the snapshot itself may be `.yaml` or `.md`). `current_run` stays pointed at the terminal run until `archive-promptbook` nulls it, so the completed branch is reachable here.
- **Looks for:** the run is archive-eligible by one of the two run-level paths — either `status: completed` with every prompt terminal (`prompts[].state` ∈ {`done`, `skipped`, `blocked`}; on a `.md` run, the per-prompt `State:` field), or `status: abandoned` with `abandonment.kind: deliberate`. A run that is `in_progress`, or `abandoned` with `kind: superseded` or with no `abandonment` mapping, is NOT eligible. Eligibility is a run-level property; there is no per-prompt flag. (See `archive-promptbook/SKILL.md` and `docs/AGENTS.md` §11.B for the definition.)
- **Stable id:** `cleanup-CLN-PB-3-<book-id>`.
- **Category:** `promptbook-hygiene`. **Severity:** P3 (the user may have a reason to leave it active).
- **Proposed action:** For a YAML run, invoke `archive-promptbook PB-NNNN` when ready; a `cycle_kind: patch` book also runs the blast-radius check. For an eligible Markdown run, use pinned public `v3.23.2` to archive it on a copy, then convert its book before its run. The current `archive-promptbook` refuses Markdown before mutation.

#### CLN-PB-4 — duplicate `### Prompt N` headings in run snapshots

- **Reads:** every run snapshot under `docs/promptbooks/runs/<book>/` — glob **both** `run-*.yaml` and `run-*.md` (format coexistence), across both active and archived books.
- **Looks for:** the duplicate-prompt drift. On a **`.md` run** this is duplicate `### Prompt N` headings within a single snapshot file — extract every `^### Prompt (\d+) —` heading; group by N; flag any N appearing more than once. A **`.yaml` run** has a structured `prompts[]` array with no headings, so the heading-duplicate failure mode cannot occur (the `n == index+1` contiguity / duplicate-`n` invariant is enforced by `validate-promptbook.py`'s post-schema pass and surfaced by audit-docs CHK-PB-SCHEMA, not here); for a `.yaml` snapshot this rule is a no-op.
- **Why this happens in practice:** `run-promptbook` advances should *edit in place* — but a runner that *appends* a new block instead of editing the existing pending one leaves both behind. The bug only manifests at archive-precondition time (when terminal-state counting double-counts the prompt), but the underlying drift is detectable earlier from this rule.
- **Stable id:** `cleanup-CLN-PB-4-<book-id>-<run-id>` (one finding per snapshot file, not per duplicate — the fix is to dedupe the whole file).
- **Category:** `promptbook-hygiene`. **Severity:** P2 (degrades archival; doesn't block other operations).
- **Proposed action:** Edit the run snapshot to remove duplicates. Convention: keep the block with the most-complete State (`done` > `running` > `pending`); if multiple are non-`pending`, keep the most-recent (latest `Completed:` or `Started:` timestamp).

#### CLN-JR-1 — recent log operations without matching journal entries

- **Reads:** `docs/log.md` (newest entries), `docs/journal/YYYY-MM.md` for the month(s) covered, and — through the shared checker — `docs/adrs/summaries/resolver.json` where the tree has one.
- **Looks for:** Within the last 7 days from `${TODAY}`, scan `docs/log.md` for entries matching regex `## \[(\d{4}-\d{2}-\d{2})\] (adr|promptbook|schema) \|.*\b(accept|archived|migration)\b`. For each hit, identify the artifact named in the subject (e.g., `ADR-NNNN`, `PB-NNNN-<slug>`). Then invoke the shared checker once per artifact, for the month the hit's date names:

  ```bash
  uv run "${CRUX_PLUGIN_ROOT}/scripts/check-journal-reference.py" \
    --artifact <artifact-id> --month YYYY-MM --repo-root <repo-root>
  ```

  Branch on the exit code. **0** — referenced; no finding. **1** — not referenced; emit the finding, reading the payload for the wording below. **2** — environment or usage failure; surface it and emit NO finding for that artifact, because a month file that could not be read is not evidence of a missing entry.
- **Two kinds of evidence, because two contracts govern the citation.** An entry references an artifact when it carries a `[[wiki-link]]` naming it, **or** a `rule:<slug>` the summaries resolver maps to a handle that artifact owns. Writing rule 7 tells the journal to cite `rule:<slug>` where a rule exists, and to name the ADR only where it carries no `governs` block. A wiki-link-only check therefore reports every governs-bearing ADR as unjournaled: three reflections in this repo's own tree were reported missing while sitting in the month file.
- **Mere slug presence is not evidence.** A slug the resolver does not carry, and a slug another ADR owns, are both REJECTED rather than ignored. The checker returns them under `rejected` with `resolves_to`, so a finding can name the decision the entry actually reflects on. A retired slug counts for the ADR owning its successor, because that is what retirement means.
- **An absent resolver is reported, never fatal.** `init-docs` creates no summaries projection, so a fresh tree has none. The checker then decides on wiki-links alone, sets `resolver_available: false`, and returns every slug token under `unverifiable`. Where a finding carries `resolver_available: false` and a non-empty `unverifiable` list, say so in the finding text: the reflection may exist and cite a rule this tree cannot resolve.
- **Stable id:** `cleanup-CLN-JR-1-<artifact-id>`.
- **Category:** `journal-gap`. **Severity:** P1.
- **Proposed action:** "Invoke `log-work` with a reflective entry on `<artifact-id>` (per v0.2.0 log-work: name at least one thing that didn't work first, one surprise, one thing to do differently). Cite the decision as `rule:<slug>` where it carries a `governs` block, and as a `[[wiki-link]]` where it does not." Where the checker rejected a citation, add: "`rule:<token>` is already cited here and belongs to `<resolves_to>`."

#### CLN-JR-2 — thin months

- **Reads:** `docs/journal/index.md` (entry counts) AND `docs/log.md` (op count per closed month). `journal/index.md` is derived — read it as-is; do not recompute the counts yourself. `generate-journal-index.py` regenerates it.
- **Looks for:** closed months (i.e., month earlier than `${TODAY}.month`) where `journal entries < 2` AND `log ops >= 10`. Closed-month means the calendar month is in the past — never flag the current month.
- **Stable id:** `cleanup-CLN-JR-2-<YYYY-MM>`.
- **Category:** `journal-gap`. **Severity:** P3.
- **Proposed action:** "Write a retrospective journal entry for <YYYY-MM> covering the <N> ops shipped that month. Cleanup will not auto-draft — reflection is non-auto-generatable per the v0.2.0 log-work contract."

#### CLN-WN-1 — stale whats_next.md entries

- **Reads:** the PRIOR `docs/whats_next.md` body's open-suggestion ids (collected in step 2 as `prior_open_ids`); the current state of the artifacts cited by those entries.
- **Looks for:** prior entries whose underlying condition no longer reproduces (e.g., the cited ADR transitioned, the cited promptbook archived). These become "Recently closed" entries in the new body — NOT new findings.

  Additionally: dismissed ids in the `dismissed:` frontmatter list that haven't matched any current finding for `>= 30` consecutive days of cleanup runs. To track this, cleanup keeps a `dismissed_last_seen:` counter per dismissal — incremented every run; reset to 0 when the underlying condition recurs. Counter reaches 30 → P3 housekeeping suggestion to prune the dismissal.

  **Exception — a dismissal keyed to a RETIRED rule id is never a prune candidate.** Its counter still climbs (the condition can never recur, because the rule that produced it is gone), so without this exception every such dismissal would eventually emit a P3 telling the user to delete a record that is deliberately being kept. Retired ids are enumerated in the rule inventory at the top of this skill; a dismissal whose `<rule-id>` segment names one is skipped by this check entirely — no counter-driven finding, ever. This is distinct from a RENAMED rule, whose condition does recur under the new id and whose old dismissal is genuinely dead weight that should age out.
- **Stable id (stale-dismissal case):** `cleanup-CLN-WN-1-stale-dismissal-<original-id>`.
- **Category:** `whats-next-stale`. **Severity:** P3.
- **Proposed action:** "Remove dismissal `<id>` from `docs/whats_next.md` frontmatter — the condition hasn't recurred in 30 runs."

#### CLN-FG-1 — forged-skill staleness

- **Reads:** `${CRUX_LOCAL_SKILLS_DIR}/forge-log.md` and `${CRUX_LOCAL_SKILLS_DIR}/*/SKILL.md` (the forged-skill tree).
- **Existence gate:** if neither `${CRUX_LOCAL_SKILLS_DIR}/forge-log.md` nor any `${CRUX_LOCAL_SKILLS_DIR}/*/SKILL.md` file exists, this rule **silently skips** — the project has no forged skills, which is normal.
- **Scope:** applies ONLY to forged skills — skill directories that have at least one `authored` entry in `forge-log.md`. A `${CRUX_LOCAL_SKILLS_DIR}/<name>/` directory with no `authored` entry in the forge log was not created by forge-skill (hand-authored or installed by another mechanism) and is explicitly OUT of scope: never flagged by this rule, never proposed for pruning.
- **Looks for:** for each in-scope skill directory, find the newest entry in `forge-log.md` across ALL event types (`authored|revised|used|evaluated`) for that skill name by **parsed timestamp** (the `## [YYYY-MM-DD HH:MM]` prefix). Compare timestamps, never file positions — the comparison spans multiple event types whose entries interleave, so position is meaningless. If that newest timestamp is older than `${TODAY} - forged_skill_stale_days` (manifest `cleanup:` key, default 30), fire a finding. `revised`, `used`, and `evaluated` events all reset the clock — a skill used, evaluated, or revised within the window is not stale even if its `authored` entry is old. An unparseable timestamp or malformed header (not matching `## [YYYY-MM-DD HH:MM] <event> | <skill-name>`) is itself a finding — surface it as part of this rule's output (fail-loud), never a silently-skipped entry.
- **Stable id:** `cleanup-CLN-FG-1-<skill-name>` (one per stale forged skill; `skill-name` is the directory name under `${CRUX_LOCAL_SKILLS_DIR}/`).
- **Category:** `forge-hygiene`. **Severity:** P3.
- **Proposed action:** "Forged skill `<skill-name>` has no `authored|revised|used|evaluated` event within the last `<N>` days (newest event: <date>). Either prune it via `forge-skill` (which records the `pruned` event and a `skill | pruned` op in `docs/log.md`) or add a note in the forge log explaining why it stays."
- **Actor boundary:** `cleanup-campsite` only *proposes* this finding. It never deletes a skill file, never writes `${CRUX_LOCAL_SKILLS_DIR}/forge-log.md`, and never writes a `skill` op to `docs/log.md`. The prune itself — on user approval or autonomously when gates permit — is performed through `forge-skill`, which is the sole authorized writer of the forge log and of `skill` ops.

#### CLN-FG-2 — used-but-never-evaluated

- **Reads:** `${CRUX_LOCAL_SKILLS_DIR}/forge-log.md` and `${CRUX_LOCAL_SKILLS_DIR}/*/SKILL.md` (the forged-skill tree).
- **Existence gate:** reuses CLN-FG-1's authored-entry gate — applies only to forged skills (those with at least one `authored` entry in `forge-log.md`). If neither `${CRUX_LOCAL_SKILLS_DIR}/forge-log.md` nor any `${CRUX_LOCAL_SKILLS_DIR}/*/SKILL.md` file exists, this rule **silently skips**.
- **Scope:** applies ONLY to forged skills — skill directories that have at least one `authored` entry in `forge-log.md`. A `${CRUX_LOCAL_SKILLS_DIR}/<name>/` directory with no `authored` entry in the forge log was not created by forge-skill and is explicitly OUT of scope.
- **Looks for:** for each in-scope skill directory, fire a finding when ALL THREE of the following hold: (1) the skill has ≥1 `used` entry in `forge-log.md`; (2) the skill has NO `evaluated` entry timestamped at-or-after the newest `used` timestamp; (3) the newest `used` timestamp is older than `${TODAY} - forged_skill_stale_days` (manifest `cleanup:` key, default 30). A `revised` entry does NOT void a pending evaluation obligation — the use still happened and must be evaluated at the next use.
- **Worked example:** skill used June 1, never evaluated → CLN-FG-2 fires July 1 → if a July session uses the skill, it evaluates then; if nothing uses it again, CLN-FG-1's staleness lane eventually proposes prune-or-justify and the evaluation question dies with the skill.
- **Stable id:** `cleanup-CLN-FG-2-<skill-name>` (one per affected forged skill; `skill-name` is the directory name under `${CRUX_LOCAL_SKILLS_DIR}/`).
- **Category:** `forge-hygiene`. **Severity:** P3.
- **Proposed action:** "Forged skill `<skill-name>` was used on <date> but never evaluated. Evaluate it on next use — the next session that actually uses the skill writes one `evaluated` forge-log entry (verdict: effective | fell-short | mixed) before ending. A session WITHOUT the use context MUST NOT retroactively fabricate an evaluation. If no use recurs within the staleness window, CLN-FG-1's prune-or-justify lane applies."
- **Actor boundary:** `cleanup-campsite` only *proposes* this finding. It never writes `${CRUX_LOCAL_SKILLS_DIR}/forge-log.md`. The evaluation itself is written by forge-skill's Recording Rules in the session that uses the skill.

#### CLN-RETRO-1 — retrospective cadence

- **Reads:** `docs/log.md` and `docs/journal/*.md`.
- **Predicate:** count `docs/log.md` headings whose date is STRICTLY AFTER the newest `docs/journal/*.md` heading matching the regex `^## \[\d{4}-\d{2}-\d{2} \d{2}:\d{2}\] learning \| Retrospective: ` and whose heading itself matches the `promptbook | archived` op form (full pattern: `^## \[\d{4}-\d{2}-\d{2}\] promptbook \| archived `). Fires at P3 when that count ≥ `retro_due_runs` (manifest `cleanup:` key, default 5). Terminology: **books archived** (the op is per book; same-day archive-after-retro may under-count by design — date granularity; acceptable for a P3 nudge).
- **First-run behavior:** if no heading matching `^## \[\d{4}-\d{2}-\d{2} \d{2}:\d{2}\] learning \| Retrospective: ` appears anywhere in `docs/journal/*.md`, count ALL archived ops in `docs/log.md` and fire the finding with explicit "no retrospective has ever run" wording — the adoption nudge is the point.
- **Stable id:** `cleanup-CLN-RETRO-1-due` (singleton — the finding represents the cadence state as a whole, not a per-book artifact).
- **Category:** `retro-cadence`. **Severity:** P3.
- **Proposed action:** "Run a retrospective — the `retrospective` skill mines recent journal reflections, run notes, and log ops; harvests ≤2 council-gated skill proposals."
- **Note:** This rule tracks cadence for the `retrospective` skill (scheduled, deliberate reflection that may yield ≤2 skill proposals per run). It is NOT a journal-entry-quality check — the journal-gap rules (`CLN-JR-1`, `CLN-JR-2`) own that concern; the word "retrospective" appearing there refers to a reflective journal *entry style*, not to the `retrospective` skill's cadence. The anchor search applies the same validity guards as Phase 1 of the `retrospective` skill: future-dated candidates and candidates with no same-day `journal | Retrospective:`-prefixed op in `docs/log.md` are ignored.

#### CLN-TMPL-1 — dogfood↔template clause parity

- **Reads:** `crux/scripts/template_parity_manifest.json` and, per manifest entry, the declared canonical file (e.g. `docs/AGENTS.md`) plus the declared twin template file (e.g. `crux/templates/AGENTS.md.tmpl`). Invokes the shared checker `crux/scripts/check_template_parity.py`, which carries forward the fence-aware section extractor from the forged-skill predecessor.
- **Existence gate / self-detection (per manifest entry):** the trigger predicate is the existence of the manifest entry's OWN declared twin template path — NOT a coarse glob. An entry whose declared twin template is absent (e.g., every downstream install that received the rendered `docs/AGENTS.md` but not `crux/templates/AGENTS.md.tmpl`) is **skipped entirely** — neither the parity check nor the stale-manifest guard runs for that entry. Therefore with no templates present the rule is a guaranteed clean **no-op (0 findings)** — inert, not dead code. The stale-manifest guard (which keys on the canonical file) is also gated behind twin-presence: guard evaluation runs ONLY in a tree where the twin exists, so it cannot fire downstream and cannot undermine the 0-finding-downstream promise. `cleanup-campsite --only CLN-TMPL-1` on a tree with no twin templates is a clean no-op, not an error.
- **Looks for (two sub-checks per manifest entry whose twin is present):**
  - **(drift)** A designated clause that drifted between the canonical file and the twin template — the checker reads the clause live from the canonical via anchor + pattern, then asserts the same text in the twin. Drifted clause → finding.
  - **(stale anchor/pattern)** A manifest entry's anchor or pattern no longer resolves in its canonical file (twin present). Stale entry → **P3 finding, emitted INSTEAD OF a vacuous clean result** — a stale entry reads "needs attention", never "in parity". The stale guard exists because a non-resolving anchor would otherwise make the rule pass vacuously, which is exactly the silent-failure mode the rule exists to prevent.
- **Stable id:**
  - Drift: `cleanup-CLN-TMPL-1-<clause-id>` (one per drifted clause; `clause-id` is the entry's id from the manifest).
  - Stale anchor/pattern: `cleanup-CLN-TMPL-1-stale-<clause-id>`.
  (Both conform to the `cleanup-<rule-id>-<artifact-slug>` shape.)
- **Category:** `template-parity`.
- **Severity:** drift → **P2** (a drifted clause in a shipped template reaches every install — more than visibility-only); stale anchor/pattern → **P3** (maintenance signal about the checker, not evidence of shipped-template drift).
- **Proposed action:**
  - Drift: "Sync the `<clause-id>` clause in `crux/templates/AGENTS.md.tmpl` to match `docs/AGENTS.md` (the canonical surface)."
  - Stale: "Manifest entry `<clause-id>` anchor/pattern no longer resolves in `<canonical-file>`; update `crux/scripts/template_parity_manifest.json` to reflect the current anchor/pattern."
- **Actor boundary:** proposes only; never edits `docs/AGENTS.md`, `crux/templates/AGENTS.md.tmpl`, or `crux/scripts/template_parity_manifest.json`. The fix is a human/Claude-under-direction edit. This honors the `audit-docs` / `cleanup-campsite` boundary (template drift is process-state, not graph integrity).
- **On-demand fast-path:** `cleanup-campsite --only CLN-TMPL-1` — runs only this rule, scopes `docs/whats_next.md` output to its findings, emits the normal `cleanup-campsite` log op. Use immediately after editing `docs/AGENTS.md` to confirm no parity regression before committing.

#### CLN-XR-1 — RESERVED for v0.2

Cross-ADR Decision-section contradictions. Not implemented in v0.1 — deliberately deferred: cross-document semantic comparison is too fuzzy for a deterministic scan rule. The id is reserved so future cleanup versions don't reuse it.

#### Known limitations of v0.1

These code paths are not exercised by the smoke test and should be exercised in a follow-up run before relying on them:

- **Dismissal lifecycle** — the v0.1 smoke run produced `dismissed: []`, so the round-trip (`dismissed_last_seen` increment, reset on recurrence, 30-run housekeeping P3 emission) is **untested**. A future run that hand-edits a finding into `dismissed:` and re-invokes cleanup is the smallest test that exercises the path.
- **"Recently closed" derivation** — first-run-on-this-repo means `prior_open_ids` was empty; the diff calculation didn't execute. A second run after closing any finding will exercise it.
- **Rename semantics** — if a scan rule is renamed in a future cleanup version, old `dismissed:` entries keyed to the previous id stay as no-ops AND the underlying condition (if still present) emits a fresh undismissed id. The user re-confirms or re-dismisses; the stale dismissal ages out via the 30-run housekeeping path. This is correct behavior but until v0.2 introduces a rule rename, it's a theoretical invariant.

#### CLN-AUD-1 — audit-recency

- **Reads:** `docs/log.md`.
- **Looks for:** the most recent `## [YYYY-MM-DD] audit |` entry. If its date is older than `${TODAY} - audit_stale_days` (default 14), emit a P1 finding.
- **Stable id:** `cleanup-CLN-AUD-1-recency` (conforms to the standard `cleanup-<rule-id>-<artifact-slug>` shape).
- **Category:** `whats-next-stale`. **Severity:** P1.
- **Proposed action:** "Run `audit-docs` — last audit was <N> days ago."
- **Note:** this rule never *invokes* `audit-docs` (different cadence — see the boundary table); it only checks the log for recency and proposes the run.

### 4. Sort, cap, build the body

- Collect all findings from step 3 (after dismissal filtering).
- Sort by severity DESC (P1 first), then by stable `id` ASC for determinism.
- **Caps:** at most 5 P1 findings, at most 10 P2 findings, P3 uncapped. When a cap is exceeded, surplus findings go under a `## Truncated (M not shown)` section listing only their ids.
- **Stale-dismissal housekeeping entries** (CLN-WN-1) are always emitted as P3 regardless of cap pressure; within P3 they sort LAST (after fresh P3 findings).

### 5. Compute "Recently closed"

`recently_closed = prior_open_ids - current_open_ids`. Cap at 10 entries (older closures live in `docs/log.md`). Format as strikethrough lines:

```markdown
- ~~<id>~~ — addressed <YYYY-MM-DD> (condition no longer reproduces).
```

### 6. Write `docs/whats_next.md`

Overwrite the body. Preserve the `dismissed:` frontmatter list verbatim (it's persistent state). Increment `dismissed_last_seen:` for each dismissal whose id didn't match a current finding; reset to 0 for any dismissal whose id DID match (i.e., the condition recurred and was suppressed).

Frontmatter shape:

```yaml
---
generated_at: ${NOW}
generator: cleanup
generator_version: "<the installed plugin version, read from plugin.json — never a hardcoded number>"
scan_findings: <int>
suggestions_open: <int>
suggestions_dismissed_carried: <int>
dismissed:
  - id: <stable id>
    dismissed_at: <YYYY-MM-DD>
    reason: "<one line>"
    dismissed_last_seen: <int>   # 0 if condition recurred this run; else prior + 1. At >= 30, cleanup emits a P3 prune suggestion via CLN-WN-1.
---
```

Body shape:

```markdown
# What's next

_Generated <YYYY-MM-DD HH:MM> by `cleanup-campsite` v<the installed plugin version>. **Body regenerated end-to-end on every run; only the `dismissed:` frontmatter list persists.** Hand-edits below the frontmatter are blown away. To dismiss a suggestion, move its `id:` into the `dismissed:` frontmatter list with a `reason:`._

## Open suggestions (N)

### 1. [P1] <subject>
- **id:** `<stable-id>`
- **category:** <enum>
- **severity:** P1 | P2 | P3
- **finding:** <1-2 lines>
- **source:** scan rule `CLN-XX-N` against `<inspected paths>`
- **proposed action:** <what to do; may name a skill>
- **refs:** [[wiki-link]] ...

### 2. ...

## Truncated (M not shown)
- `<id1>`, `<id2>`, ...

## Recently closed (since last run)
- ~~`<id>`~~ — addressed <YYYY-MM-DD> (condition no longer reproduces).
```

### 7. Update `docs/index.md`

If `suggestions_open > 0` AND `docs/index.md` does NOT already have a `## What's next (N)` section, insert it as the first concern-rollup, between the file header and `## Research`. Content:

```markdown
## What's next (N)

_N open suggestions (P1: X, P2: Y, P3: Z) — see [[whats_next]]._
```

If the section exists, update N and the breakdown. If `suggestions_open == 0`, remove the section.

Update `_Last updated:_` to `${TODAY}`.

### 8. Append to `docs/log.md`

Newest-first prepend:

```markdown
## [${TODAY}] cleanup-campsite | N open (X P1, Y P2, Z P3), M closed, K dismissed

Top-3 P1 ids: `<id1>`, `<id2>`, `<id3>`. See [[whats_next]].
```

If N is zero on all counts, still write the entry (auditable record that cleanup ran).

### 9. Console summary

Print to the user:

```
Cleanup complete: N open suggestions (P1: X, P2: Y, P3: Z), M closed, K dismissed.
See docs/whats_next.md for details.
```

Plus the top-3 P1 subjects if any exist, so the user sees the actionable items immediately.

## Verification checklist

- [ ] `docs/whats_next.md` exists.
- [ ] Frontmatter parses as valid YAML.
- [ ] `generated_at` is the current UTC timestamp.
- [ ] `generator_version` matches the installed plugin version (from `plugin.json`).
- [ ] `dismissed:` list is preserved verbatim from the prior file (each entry's `dismissed_last_seen` is incremented or reset per step 6).
- [ ] Every body suggestion has all six required fields: `id`, `category`, `severity`, `finding`, `source`, `proposed action`. `refs` may be absent if no refs exist.
- [ ] Severity caps respected: ≤ 5 P1, ≤ 10 P2 (excess in `## Truncated`).
- [ ] Stale-dismissal entries sort after fresh P3 findings.
- [ ] `docs/log.md` has a new `## [${TODAY}] cleanup-campsite |` entry at the top.
- [ ] `docs/index.md` has a `## What's next (N)` section iff `N > 0`; section content is consistent with the body.
- [ ] No edits to README, USER_GUIDE, CHANGELOG, ADR bodies, journal entries, or promptbook files.
- [ ] `manifest.yml` counters unchanged.

## Red flags — STOP and reconsider

- **About to edit a prose surface directly** (README, USER_GUIDE, ADR body, journal entry, promptbook plan). Refuse. Cleanup proposes via `whats_next.md`; humans (or Claude under direct instruction) execute.
- **About to write `docs/whats_next.md` without preserving the `dismissed:` frontmatter list.** That's the only state that survives across runs; clobbering it silently breaks the dismissal contract.
- **About to invoke `audit-docs` from cleanup.** Two skills, two cadences. Cleanup CHECKS whether audit ran recently; it does NOT run it.
- **About to fail a scan rule silently** because the input file is missing. Each rule's `Reads:` section enumerates expected inputs; a missing input is a finding (`docs/whats_next.md` would surface "missing expected file <path>"), not a swallowed error.
- **About to surface a finding for a dismissed id without re-checking the dismissal.** Dismissals suppress unconditionally; the `dismissed_last_seen` counter tracks staleness but does not gate suppression.
- **About to invent a stable id format that doesn't follow `cleanup-<rule-id>-<artifact-slug>`.** Determinism depends on every rule emitting ids in the documented shape.
- **About to write a finding without a `proposed action`.** Cleanup's contract is *forward-looking* — every finding names what to do, even if "ignore" is the action.
- **About to skip the `docs/log.md` entry on a zero-findings run.** The log is the audit trail of "cleanup ran on this date"; write the entry even if N=0.
- **About to suggest invocation of a skill that doesn't exist.** Each `proposed action` that names a skill MUST reference an existing one (`archive-promptbook`, `transition-adr`, `log-work`, `audit-docs`, `run-promptbook`, `forge-skill`, `retrospective`).

## Rationalization table and common mistakes

The Red flags list above is the primary stop-list. For the fuller
excuse→reality mapping and the recurring-mistake catalog, read
[`references/pitfalls.md`](references/pitfalls.md).
## See also

- `docs/AGENTS.md` §5.A (the whats_next.md schema) and §11 (the audit-docs boundary); the scan-rule roster is in this file's Overview (20 implemented + 2 retired + 1 reserved, for 23 ids).
- `audit-docs/SKILL.md` — the boundary partner. Cleanup CHECKS whether audit ran recently; it does NOT invoke it.
- `log-work/SKILL.md` (v0.2.0) — establishes the journal-as-reflection contract that cleanup honors (CLN-JR-1 surfaces missing entries as suggestions; never auto-drafts the entry itself).
- `archive-promptbook/SKILL.md` — what users invoke after a CLN-PB-3 finding.
- `transition-adr/SKILL.md` — what users invoke after a CLN-ADR-2 finding (accept / deprecate / supersede).
- `run-promptbook/SKILL.md` — what users invoke after a CLN-PB-1 or CLN-PB-2 finding.
- `query-docs/SKILL.md` — cleanup does NOT invoke this; orthogonal capability.
- `retrospective/SKILL.md` — the skill CLN-RETRO-1 nudges the user to invoke; deliberate reflection that harvests ≤2 skill proposals from finished work.
