---
name: refresh-research-synthesis
description: "Reconcile stale or flagged research synthesis with current sources. Apply changes only with explicit per-page approval."
metadata:
  tags: "research, refresh, synthesis"
  bundles: "crux-docs"
  risk_level: "low"
  triggers: "refresh synthesis | reconcile pages | clear flags | review research | what synthesis pages are stale? | what needs reconciliation?"
  routing_note: "Per-page user-supervised."
---

# Refresh Research Synthesis

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

The user-gated maintenance pass for `docs/research/<category>/` synthesis pages — concepts, decisions-context, references, ideas, meetings, and any other category active under the research concern. Closes the loop on debt accumulated by upstream operations:

- `ingest-research` flags new contradictions inline (`> [contradiction]`).
- `refresh-research-sources` flags synthesis pages whose cited sources have changed (`> [source updated YYYY-MM-DD: <slug>]`) at the top of the page body.
- Time accumulates: `last_reviewed` ages out at `research.refresh_interval_days` (default 90).
- New ingestion adds sources whose tags overlap a synthesis page but aren't yet cited there.

**Synthesis pages encode the user's understanding.** Mechanical reconciliation is the wrong model — judgment is required on every change. This skill is the discipline for that: present each candidate change, get explicit approval, apply, move on.

Pairs with: `refresh-research-sources` (creates the upstream markers this skill resolves). Distinct from `audit-docs` (catches drift but doesn't make editorial decisions). Distinct from `query-docs` (produces new pages from questions, rather than maintaining existing ones).

## When to use

- User says: "refresh synthesis", "reconcile pages", "clear flags", "review research", "address pending updates", "merge updates into synthesis", "what synthesis pages are stale?", "what needs reconciliation?".
- Proactively, when an audit reports >5 accumulated `[source updated]` markers, or when any single synthesis page has 3+ unresolved markers.
- After a substantial `refresh-research-sources` batch (more than 3 sources updated).
- When `last_reviewed` on any synthesis page exceeds `research.refresh_interval_days`.

Do **not** use this skill for:
- Editing synthesis pages outside the context of source changes (use the file directly).
- Creating new synthesis pages from a query (use `query-docs`, or just write the page).
- Refreshing source pages (use `refresh-research-sources`).
- Drift detection on `docs/research/index.md` / `docs/research/sources.md` / raw chain (use `audit-docs`).
- Reconciling code docs (the regenerative model handles those; there is no synthesis to reconcile).

## Stale criteria

A synthesis page is **stale** if any one of:

1. The body contains `> [source updated YYYY-MM-DD: <slug>]` — `refresh-research-sources` persisted a substantive change to a cited source.
2. The body contains `> [contradiction]` — `ingest-research` noted that a new source contradicted an existing claim.
3. The body contains `> [unresolved]` — historic marker, often hand-added.
4. `last_reviewed` is more than `research.refresh_interval_days` before today.
5. (Expensive — only when explicitly requested) A newer source whose `captured_at` is more recent than this page's `last_reviewed` has tag overlap with this page's `tags:` but is NOT yet listed in the page's `sources:` frontmatter.

Severity order: criteria 1–3 are debt (something needs explicit resolution). Criterion 4 is hygiene. Criterion 5 is opportunity.

## The pipeline

### 1. Build the stale list

Walk every synthesis page under `docs/research/<category>/` (every subdirectory of `docs/research/` other than `sources/` and `raw/`). For each, read frontmatter + body and check the five criteria.

For more than ~30 synthesis pages, spawn an Explore agent for the walk.

Print a table the user can scan:

```
PATH                                          | TRIGGERS                                | LAST REVIEWED
docs/research/concepts/claude-managed-agents  | source updated:1 (foo-blog @2026-08-22) | 2026-05-19
docs/research/decisions-context/storage       | contradiction:1                         | 2026-04-02 (49d ago)
docs/research/references/mcp-protocol         | last_reviewed >90d                      | 2026-01-12 (130d ago)
```

Order: criteria 1–3 first (marker debt), then 4 (time-stale), then 5 (opportunity). Within each tier, oldest `last_reviewed` first.

### 2. Confirm scope

Ask the user **once**: "Reconcile all N, a subset, or skip?" Accept:

- `all` — process every entry, one at a time.
- `markers only` — process criteria 1–3, skip 4 and 5.
- `first M` — process the M oldest.
- Comma-separated paths or slugs — just those.
- `skip` — exit cleanly.

Do not ask per-page at this stage. Per-page interaction happens *during* reconciliation, not before scope is set.

### 3. For each page in scope, run the reconciliation loop

This is the heart of the skill. **Never skip the user-confirmation step.**

#### 3a. Load context

- Read the synthesis page in full.
- Read every source currently cited (`sources:` frontmatter + every `[[research/sources/<slug>]]` link in the body).
- For pages with `[source updated]` markers, also read `docs/research/updates.md` entries for the named slugs to understand what changed upstream.
- For pages with new-overlapping-source candidates (criterion 5), read those candidate sources too.

#### 3b. Triage each marker / criterion separately

For each `> [source updated YYYY-MM-DD: <slug>]` marker:

1. Read the source's *current* content (the audited markdown in `docs/research/sources/<slug>.md` is authoritative).
2. Walk the synthesis body for claims that derived from this source — passages citing `[[research/sources/<slug>]]`, or claims clearly traceable to the source's prior content.
3. Determine: did the source's claim about THIS topic actually change? `refresh-research-sources` flags every synthesis page whose `sources:` includes the slug, but a source can change in ways that don't affect a specific synthesis claim.
4. Present to the user, in this exact shape:

   ```
   PAGE: docs/research/concepts/claude-managed-agents
   MARKER: [source updated 2026-08-22: cloudflare-blog-claude-managed-agents]
   AFFECTED CLAIM: "Cloudflare ships two sandbox backends: MicroVM and lightweight isolate."
   SOURCE CURRENT STATE: "Cloudflare ships three sandbox backends: MicroVM, isolate, and Workers-native."
   PROPOSED CHANGE: Replace claim with "Cloudflare ships three sandbox backends: MicroVM, isolate, and Workers-native (added 2026-08)."
   OR: Marker can be cleared without content change (source updated elsewhere, not in this claim).
   YOUR CALL: apply / clear-only / skip / edit-different
   ```

5. Apply the user's call. If `edit-different`, take their wording and apply.

For each `> [contradiction]` marker:

1. Locate the contradicting passage — usually a blockquote naming both sources.
2. Read both sources' current state.
3. Present source-A-says / source-B-says and ask: `favor A / favor B / keep both as noted tension / I'll handle this myself`.
4. If user favors one: rewrite the claim, remove the marker, add `> [previously contradicted by [[research/sources/<rejected>]]; resolved <today>]` for audit trail.
5. If user keeps both: leave the marker; rewrite surrounding text to explicitly present both views.

For each `> [unresolved]` marker:

Same shape as contradiction but no specific second source — the user decides what the open question is and how (or whether) to resolve it now.

For criterion 4 (time-stale, no markers):

1. Read every cited source.
2. Check: does any claim on the synthesis page seem out of step with its source's current content?
3. If yes, present each as you would a `[source updated]` marker (without the marker; just propose the change). If no, bump `last_reviewed` only after explicit user confirmation that the page reads accurately.

For criterion 5 (new-overlapping-source candidates):

1. Read each candidate source.
2. Propose to the user: "Source [[research/sources/<slug>]] (tags overlap: [X, Y]) was ingested after this page's last review. It supports/refines/contradicts your claim that <Z>. Cite it?"
3. If yes: ask where to cite and what claim it supports. Append slug to `sources:` frontmatter and add `[[research/sources/<slug>]]` link in the body at the user's chosen spot.
4. If no: insert `<!-- considered and rejected: [[research/sources/<slug>]] on <date>; not relevant because <reason> -->` so future passes don't re-suggest.

#### 3c. Apply approved changes

- Edit the body per the user's calls.
- Remove resolved markers — delete the full blockquote block, not just the text inside; leave no empty paragraph behind.
- **Preserve unresolved markers** (criteria 2 and 3 the user chose to defer). The page stays stale until next refresh.
- Add resolution-trail comments where useful.

#### 3d. Update frontmatter

- Bump `last_reviewed: <today>` on **every page touched**, including pages where the user only said "clear-only" or "confirmed accurate, no change".
- If new sources were cited, append their slugs to `sources:` (dedup; preserve order).
- Don't touch `title`, `slug`, `type`, `tags` unless the user explicitly redirects.

#### 3e. Update index row

The synthesis page's row in `docs/research/index.md` shows `last_reviewed: YYYY-MM-DD`. Bump to today. Update the `sources: N` count if the list grew. Update `_Last updated:_` at top of `docs/research/index.md`. Bump `_Last updated:_` in `docs/index.md`.

### 4. After the batch, append to `docs/log.md`

Single entry (newest-first):

```
## [YYYY-MM-DD] refresh | synthesis batch (N reconciled, K skipped, M deferred)
```

Body, three short lists:
- **Reconciled** (N): page paths.
- **Skipped** (K): pages where the user said "skip" at scope-setting or during a triage.
- **Deferred** (M): pages where the user chose "I'll handle this myself" or the issue can't be resolved now.

### 5. Hand-off summary

Print to the user:
1. Counts: N reconciled, K skipped, M deferred.
2. Pages with **deferred** markers, so the user knows what's still pending.
3. The count of markers removed across the batch.
4. Suggest running `audit-docs` if more than 5 reconciliations happened.

## Verification checklist

- [ ] Every page in scope was actually read in full before any change was proposed.
- [ ] Every cited source was re-read before the user was asked to bump `last_reviewed`.
- [ ] No `[contradiction]` marker was removed without an explicit user resolution.
- [ ] Every resolved marker was deleted as a full blockquote — no stray `>` lines or empty paragraphs.
- [ ] Every still-unresolved marker is intact.
- [ ] `last_reviewed: <today>` bumped on every page touched.
- [ ] Every new citation in the body is paired with a slug in `sources:` frontmatter (no body-vs-frontmatter drift).
- [ ] `docs/research/index.md` rows for affected pages show the new `last_reviewed`; `_Last updated:_` bumped.
- [ ] `docs/index.md` `_Last updated:_` bumped.
- [ ] One batch entry appended to `docs/log.md` with op `refresh`.

## Red flags — STOP and reconsider

- About to bump `last_reviewed` without actually re-reading the cited sources. The bump asserts verification.
- About to remove a `[contradiction]` marker without the user picking a resolution. Marker deletion without resolution = silent data loss.
- About to rewrite a synthesis claim to be deliberately vague enough that both contradicting sources fit. That's evasion, not reconciliation.
- About to apply a change because "the diff is small". The user decides on every change.
- About to plow through `all N` without showing each proposed diff. `all` sets scope, not consent.
- About to add a citation to `sources:` for a source the user hasn't been told about. Citing implies endorsement.
- About to add a `[[research/sources/<slug>]]` link in the body without updating `sources:` frontmatter. They must stay in sync.
- About to delete a marker line but leave a stray `>` and blank paragraph. Clean the whitespace.
- About to edit a page outside `docs/research/<category>/` (e.g., touching an ADR body). Wrong skill — this one is research-only.

## Rationalization table

| Excuse | Reality |
|--------|---------|
| "User said 'all', so I can just apply each proposal without confirming." | "All" sets *scope*, not *consent*. Per-change confirmation still required. The user can type "yes" rapidly, but the prompts stay. |
| "This `[source updated]` marker is for a tiny edit; just clear it." | A marker means `refresh-research-sources` persisted a substantive change. Even tiny edits can shift framing. Read the diff; decide; clear with resolution. |
| "Bumping `last_reviewed` to today implies verification, even if I didn't verify." | Then the bump lies. Don't bump unverified. |
| "The contradiction is between two sources that agree on everything else; just pick one." | The disagreement may matter even if everything else aligns. Present both; let the user decide. |
| "Tag overlap means the new source is definitely relevant." | Tag overlap is a heuristic. The user reads and decides. |
| "This page's `last_reviewed` is 91 days old; 90 is a hard threshold." | The threshold is a sort key, not a contract. Honor user overrides. |
| "I'll batch-edit five pages and ask the user to review at the end." | Then the user reviews five diffs against five pages they haven't reloaded mentally. Page-by-page keeps context tight. |
| "I'll silently re-tag the page to match the new source." | Tag changes ripple into search and audit. Always ask. |
| "The marker is on a page that cites the source via a synthesis page, not directly. Skip." | The marker is at the top of the body; the synthesis-reconciliation logic doesn't care how the source is cited. Triage it. |

## Common mistakes

- **Deleting a marker without checking its date**. A `[source updated 2026-08-22: foo]` may have already been addressed in a prior pass but not cleaned. Verify by checking if the updated content is already in the synthesis page.
- **Adding the new source's slug to `sources:` but not adding a `[[research/sources/<slug>]]` link in the body**. Audit will catch this, but don't break the invariant.
- **Removing the `> [contradiction]` text in place**, leaving a `> ` line and a blank paragraph. Remove the whole blockquote.
- **Treating new-overlapping-source candidates as the user's homework**. Read the candidate yourself and propose a specific claim it supports or contradicts. "Should we cite this?" is not enough.
- **Skipping the `docs/research/index.md` row update**. The index's `last_reviewed` is what `audit-docs` reads.
- **Preserving old slugs in `sources:` for a source page that was renamed**. Slugs are immutable — if a slug ever did change, this skill isn't the place to fix it.
- **Inferring a marker resolution from `docs/log.md` or `docs/research/updates.md` without showing the user**. Those files capture upstream history; the synthesis-side decision is still the user's.
- **Forgetting to bump `last_reviewed` on a "confirmed accurate, no change" page**. The whole point of the pass is to record the verification.
