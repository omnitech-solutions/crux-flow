---
name: query-docs
description: "Answer questions about project knowledge, decisions, plans, and current architecture from the documentation tree."
metadata:
  tags: "query, search, synthesis"
  bundles: "crux-docs"
  risk_level: "low"
  triggers: "what does X do? | why did we choose Y? | what's our plan for Z? | what do we know about W? | compare X and Y | what's new since | summarize the current architecture | what's the current architecture | what supersedes what | how do the ADRs relate"
  routing_note: "Index-first; cites sources."
---

# Query Docs

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

The discovery-side counterpart to the ingest/refresh/extract triad. When the user asks a question, this skill walks `docs/` to compose an answer with citations — and, when the answer is substantive enough to be worth keeping, offers to file it back as a new synthesis page under `docs/research/ideas/` so the docs tree compounds with use rather than only with new material.

**Citation discipline is the heart of the skill.** Every substantive claim in the answer must trace to a `docs/` path. If the docs don't have material on the question, say so explicitly. Don't fall back to the model's training and present it as if it came from the docs — that defeats the whole pattern.

Pairs with: every write skill in the suite (which adds material the queries draw on), `refresh-research-synthesis` (which keeps research synthesis pages this skill cites up to date), and `audit-docs` (which catches broken links).

## When to use

The user is asking a question. The expected shape of citation depends on the question form:

- **"How is the project shaped today?" / "what entities exist?" / "what's the interface surface?" / "which modules depend on which?" / "what decisions are accepted?"** → resolve against `docs/arch/` FIRST, before any ADR body. Cite the spine page that answers it: `docs/arch/data-model.md` for entities, `docs/arch/api-surface.md` for the interface surface, `docs/arch/module-graph.md` for module dependencies, `docs/arch/decision-index.md` for the accepted-decision list. `docs/arch/overview.md` is the synthesized narrative across all four.
- **"What do we currently hold to be true about X?" / "is X live or only on paper?"** → this is a current-belief question, distinct from the shape question above. Resolve `docs/adrs/doctrine/` FIRST, then `docs/adrs/summaries/`, then the ADR body. Doctrine holds no authority — when doctrine and an ADR body disagree, the ADR body is the record and wins; cite it as the deciding source.
- **"What does X do?"** → narrative answer; cite `docs/code/<lang-namespace>/<unit>.md` pages.
- **"Why did we choose Y?"** → cite ADRs (`docs/adrs/ADR-NNNN-<slug>.md`); when the ADR's `related_briefs:` points to one, cite the brief too (`docs/briefs/BRIEF-<slug>.md`).
- **"What's our plan for Z?"** → cite active promptbooks (`docs/promptbooks/active/<id>-<slug>.yaml`, or a legacy `.md` book) and, when relevant, the latest run snapshot.
- **"What do we know about W?"** → cite research synthesis pages (`docs/research/<category>/<slug>.md`) and source pages (`docs/research/sources/<slug>.md`).
- **"What's new since [date]?" / "what's stale?" / "what was decided this month?"** → read `docs/log.md` and `docs/journal/YYYY-MM.md` and `docs/research/updates.md` directly. The indexes aren't the right entry point for these.
- **"Compare X and Y."** → two parallel scans, then read both sets, then extract dimensions/agreements/disagreements.
- **"Find a source that says X."** → use `docs/research/index.md` to locate plausible sources by tag/summary, then read bodies to confirm.

**The arch/ADR split, because one question shape resolves against each.** A question about the project's own shape — what exists — resolves against `docs/arch/`. A question about rationale — why it is that way — resolves against an ADR body. Read the ADR when the question asks why, or when an arch page names that decision as the reason for a shape. An answer about shape cites arch pages; an answer about rationale cites the ADR. `docs/arch/` is derived and regenerated from the project's real sources, so it says what is true now; the decision history says what was decided when, which is a different question at greater length.

When `docs/arch/` is absent (the concern is not enabled in `docs/manifest.yml`), say so and fall back to the sources a shape question can be answered from — `docs/code/` pages, then the ADR set. Do not present the decision history as a current-state answer without naming that substitution.

**The doctrine/ADR split, because a current-belief question resolves through a different chain than a shape question.** A question about what the project currently holds to be true — and whether that belief is live or only on paper — routes `docs/adrs/doctrine/` → `docs/adrs/summaries/` → the ADR body, in that order. Doctrine is a per-domain, plain-language read view compiled from summaries and reconciled against ratified invariants; it holds zero authority. The ADR body is the record and wins on any disagreement.

Do **not** use this skill for:
- Filing new external material — use `ingest-research`.
- Resolving accumulated synthesis markers — use `refresh-research-synthesis`.
- Checking research sources upstream — use `refresh-research-sources`.
- Drift detection / audit — use `audit-docs`.
- Code-doc regeneration — use `extract-code-docs`.
- Editing existing pages outside of an explicit "file this answer back" step (just edit directly).

## The pipeline

### 1. Locate

Start with `docs/index.md` — it's the master catalog, sectioned by concern (code, research, adrs, briefs, journal, promptbooks). Read it once to identify which concerns are likely to hold the answer.

Then dive into the per-concern index for each candidate:

- `docs/arch/index.md` — for "how is the project shaped today?" questions; the concern-local catalog of the four spine pages. **First stop for any current-state question**, ahead of `docs/adrs/index.md`.
- `docs/adrs/doctrine/` — for "what do we currently hold to be true about X?" questions. **First stop for any current-belief question**, ahead of `docs/adrs/summaries/` and the ADR body.
- `docs/code/index.md` — for "what does X do?" / "where does X live?" questions.
- `docs/adrs/index.md` — for "why did we choose Y?" / decision-history questions.
- `docs/promptbooks/index.md` — for "what's our plan for Z?" / in-flight-work questions.
- `docs/research/index.md` — for "what do we know about W?" / external-knowledge questions.
- `docs/journal/index.md` — for "what was figured out recently?" / "what's the timeline of X?" questions. Derived by `generate-journal-index.py`; read it as-is.
- `docs/log.md` (no index — it IS the index) — for "when did skill X run?" / operational-history questions.

When per-concern indexes are insufficient (vague topic, large tree), fall back to a Bash grep over `docs/` for the topic keywords.

### 2. Read

Read every page identified in step 1 **in full**. Don't synthesize from titles or one-line summaries — they're search hints, not source material.

For each page read:
- For arch spine pages: these are derived and regenerated wholesale from the project's real sources. They reflect the shape of the tree at the last derive, they carry no `last_reviewed`, and a hand-edit to them is blown away. `docs/arch/decision-index.md` cites ADRs as footnotes and never inline, so follow the footnote to reach the decision itself.
- For ADRs: respect status. A `Deprecated` or `Superseded` ADR may have been the right decision once but isn't current; mention the status when citing.
- For research synthesis pages: if it has `> [contradiction]`, `> [source updated]`, or `> [unresolved]` markers, the answer may need to mention pending tensions. If `last_reviewed` is more than `research.refresh_interval_days` old, note staleness.
- For code-doc pages: these are regenerated from source. They reflect what the code says today. They have NO `last_reviewed`.
- For promptbook pages: state lives in the run snapshot, not the book. To answer "where are we on Z?", read the book *and* the current run snapshot.
- For source pages: the body IS the citable rendition of the upstream content. Cite by `[[research/sources/<slug>]]`, not by re-quoting from `raw/`.

### 3. Synthesize

Compose the answer. Citation rules:

- **Every substantive claim cites a `docs/` page** via inline wiki-link or explicit path: `[[research/sources/foo]]`, an ADR's wiki-link (its page under `adrs/`), `docs/code/auth/session.md`, etc.
- **Inline citations** in flowing prose, not footnotes. Example: "Sessions use a 30-day rolling expiry, refreshed on every authenticated request ([[code/auth/session]])."
- **ADR citations include status** when not `Accepted`: cite the ADR's wiki-link and append its status, e.g. "We considered SQLite (ADR-NNNN — Superseded by ADR-MMMM)..."
- **Multiple-source claims** cite all relevant pages.
- **Direct quotes** use markdown blockquotes with the citation immediately after.

Forbidden:
- Uncited claims that aren't trivially derivable from cited material.
- Citing `docs/research/raw/` directly (always cite via the source page).
- Citing `docs/code/_meta/manifest.json` as an authority (it's bookkeeping; cite the page).
- Fabricating citations to paths that don't exist.
- Hedge phrases like "as is well known" or "generally" with no citation — if it's well known, find the page.
- Falling back to the model's training and presenting it as docs content.

When the docs **don't** cover the question:
- Say so explicitly: "The docs don't currently cover [X]."
- Offer concrete next steps: "Want to ingest [obvious candidate URL] to fill the gap?", or "Should we open an ADR to capture this decision now?", or "I could draft a brief in `docs/briefs/`."
- Do NOT fall back to training and present it as docs content.

When the docs **partially** cover the question:
- Answer the covered part with citations.
- Be explicit about the uncovered part: "Docs cover X and Y but say nothing about Z — flag Z as a gap?"

### 4. Surface stale markers / capture gaps inline

If any cited research synthesis page has unresolved markers, or a cited ADR is superseded, or a cited research source has a `## Capture gaps` section bearing on the claim, mention it woven into the relevant paragraph — not as a separate disclaimer block.

This is small-stakes integrity work that helps the user calibrate confidence.

### 5. Optionally log the query

**Off by default.** Append a log entry only when the user says "log this query" (or equivalent — "save this question", "add to the audit trail").

When logging, append to `docs/log.md` (newest-first, at top):

```
## [YYYY-MM-DD] query | <one-line question summary>
```

Body, 1–3 lines:
- The question, verbatim or near-verbatim.
- The docs paths consulted (comma-separated).
- Whether a synthesis page was filed (if yes, link the new page; if no, "answer not filed").

### 6. Offer to file substantive answers

After the answer, offer to file it as a research synthesis page under `docs/research/ideas/<slug>.md` if **any** of:

- The answer cites 2 or more sources or pages.
- The answer introduces a novel connection or comparison.
- The answer represents user-directed analysis (not just retrieval).
- The user asked an open question whose answer is worth keeping ("what's our position on X" — yes; "find the file that defines X" — usually no).

The offer:

> This answer pulled together claims from [N pages] and surfaced connections that aren't in any single existing page. File it as a synthesis page? I'd suggest:
> - **Path**: `docs/research/ideas/<proposed-slug>.md`
> - **Title**: <human title>
> - **Tags**: <derived from cited material>
>
> Or keep it as a chat-only answer.

If the user accepts:
- Write `docs/research/ideas/<slug>.md` with full synthesis frontmatter (the `ingest-research` synthesis contract):

  ```yaml
  ---
  title: "Human-readable title"
  slug: page-slug
  type: ideas
  tags: [topic1, topic2]
  sources: [source-slug-1, source-slug-2]   # research-source slugs ONLY (under docs/research/sources/)
                                            # leave [] if no research sources were cited and the answer is editorial commentary on docs/code/, docs/adrs/, etc.
  last_reviewed: 2026-05-26
  ---
  ```

  The `sources:` list is for research-source slugs only — the audit enforces every slug in `sources:` exists under `docs/research/sources/`. Citations to ADRs, briefs, code-doc pages, promptbooks, etc., live in the body as wiki-links, not in `sources:`.
- Body: the answer with wiki-link citations preserved.
- Update `docs/research/index.md`: add a row to the `## Ideas (N)` section; bump the count; bump `_Last updated:_`.
- Update `docs/index.md` `_Last updated:_`.
- If logging was opted in for this query, link the new page in the log entry.

If the user declines: leave the answer in chat. No write.

**Default for `type:`**: `ideas`. The `ideas/` category is the natural home for query-generated synthesis. Other categories (`concepts/`, `decisions-context/`, `references/`, `meetings/`) require the user to pick them explicitly.

**Non-existent category — reuse the `ingest-research` category gate, don't fail.** If the user names a file-back category (or proposes a slug under a category directory) that does NOT yet exist under `docs/research/`, do NOT error out and do NOT silently fall back to `ideas/`. Instead apply `ingest-research`'s **Category gate**: STOP and propose the new category to the user with (a) a one-line definition of what would live there, (b) 2–3 example page titles that would belong, and (c) the nearest existing category and why this is distinct. Do not create the directory until the user agrees. When approved: create the empty directory, add a `(0)` section to `docs/research/index.md` in alphabetical order, file the synthesis page into it, and log a separate `## [YYYY-MM-DD] schema | added category <name>` entry in `docs/log.md` (distinct from any `query` log entry). If the user declines the new category, offer `ideas/` (the default) or keeping the answer chat-only. (This mirrors `ingest-research/SKILL.md` "Category gate" so the two skills create categories the same way.)

## Output forms

Default: prose markdown in chat with inline wiki-links as citations.

Ask before producing another form:

- **Comparison table** — for "compare X and Y" with concrete dimensions.
- **Timeline** — for "what's changed over time"; chronological markdown list.
- **Code-doc walk-through** — for "show me what's in module X"; quote the relevant `docs/code/<lang>/<unit>.md` page sections.

## Verification checklist

- [ ] A current-state question was answered from `docs/arch/` first, and an ADR body was read only for rationale or because an arch page named that decision as the reason for a shape.
- [ ] A current-belief question was answered by routing `docs/adrs/doctrine/` → `docs/adrs/summaries/` → the ADR body, and any disagreement between doctrine and an ADR body was resolved in the ADR body's favor.
- [ ] Every substantive claim has a citation to a real `docs/` path.
- [ ] No fabricated paths — every `[[...]]` link or `docs/...` reference resolves to a file that exists.
- [ ] Every cited page was read in full, not skimmed from index summaries.
- [ ] If the docs don't cover the question, the answer says so explicitly — no training-data fallback.
- [ ] ADR citations note status when status is not `Accepted`.
- [ ] Research synthesis pages with unresolved markers had their staleness mentioned at the relevant moment.
- [ ] Capture gaps on cited sources were mentioned when relevant.
- [ ] If `log this query` was requested, an entry was appended to top of `docs/log.md` with op `query`.
- [ ] If `log this query` was NOT requested, **`docs/log.md` was NOT modified** — query logging is off by default, so a query with no explicit log request must leave `docs/log.md` byte-for-byte unchanged. (The lone exception: filing a synthesis page into a brand-new category writes a separate `schema | added category` entry — that is the category-gate's own log op, not a `query` entry, and only ever happens after explicit user approval of the new category.)
- [ ] If the user accepted the file-back offer, `docs/research/ideas/<slug>.md` (or the user-chosen category) exists with valid synthesis frontmatter (per the `ingest-research` synthesis contract) and `docs/research/index.md` was updated.
- [ ] If a non-existent file-back category was named, the `ingest-research` category gate was applied (proposed + user-approved before the directory was created) rather than failing or silently defaulting to `ideas/`.

## Red flags — STOP and reconsider

- About to answer "what exists / how is this shaped today" by opening ADR bodies. That is the decision history answering a current-state question at greater length. Read `docs/arch/` first.
- About to answer a question with no citations because "the answer is general knowledge". General knowledge doesn't belong in a citation-disciplined docs query. Find the page, or say docs don't cover it.
- About to cite a path you didn't actually read. The path being in an index is not enough; read the body.
- About to cite an ADR without checking its status. Citing a superseded ADR as current is a factual error.
- About to file a synthesis page without user approval. Always offer; never auto-file.
- About to file a synthesis page outside `docs/research/ideas/` without the user explicitly choosing another existing category.
- About to fabricate a citation to a path that doesn't exist.
- About to write a long answer with no caveats when the cited pages have unresolved markers, superseded ADRs, or stale `last_reviewed`. The user is calibrating confidence; flag the issues.
- About to declare "the docs have nothing on this" without actually running the grep/scan first.
- About to put non-research-source slugs in the new synthesis page's `sources:` frontmatter. Only `docs/research/sources/` slugs go there.

## Rationalization table

| Excuse | Reality |
|--------|---------|
| "The ADRs will tell me what the system looks like." | The ADRs tell you what was decided, one decision at a time, including the ones later amended. `docs/arch/` tells you what the tree is now, derived from the tree. Read arch for shape, the ADR for why. |
| "This is a one-line factoid; I don't need to cite." | Especially for factoids — citing is cheap, fabrication is harmful. The whole point is to make claims traceable. Cite. |
| "The model knows this; I'll answer from training." | Then you're not querying the docs, you're querying yourself. The user has a docs tree for a reason. If docs don't cover it, say so. |
| "The user already trusts the cited ADR; I don't need to flag its Superseded status." | Status is part of the citation. A Superseded ADR is a wrong answer dressed as a right one. Always include the status when non-Accepted. |
| "Filing a one-paragraph answer as a synthesis page is overkill." | Probably right. The file-back offer is for substantive answers. Skip the offer for trivia. |
| "I'll just file it without asking — the user clearly wants it." | Always offer. Auto-filing skips the title/category/tags choices that should be the user's. |
| "I'll cite `code/auth/session.md` without reading it because the question is about sessions." | Read every page you cite. Code-doc pages are regenerated and can include surprises. |
| "The user said 'log this query' once at session start, so I'll log all queries from here on." | Each query is opt-in. The flag doesn't persist across questions; ask again or take an explicit "log all queries this session" instruction. |
| "The answer is in `docs/log.md` — I'll just quote the log entry." | The log is operational, not narrative. Pull the underlying page the log references, then cite that. |
| "I'll put `code/auth/session` in `sources:` of the new synthesis page." | `sources:` is research-source slugs only. Code-doc citations live in the body as wiki-links, not in frontmatter. |

## Common mistakes

- **Reaching for `docs/adrs/index.md` on a "what exists" question.** The accepted-decision list lives in `docs/arch/decision-index.md`; the ADR index is the decision history's own catalog.
- **Citing `docs/index.md`** (`as the index notes...`) instead of the underlying pages. The index is a search hint, not a citable source.
- **Citing a research synthesis page when the source page is more authoritative**. If the question is about what source X says, cite `[[research/sources/X]]` directly.
- **Letting "compare X and Y" turn into "summarize X" + "summarize Y"** with no synthesis. The comparison is the value — extract dimensions, agreements, disagreements.
- **Forgetting to update `sources:` frontmatter** when filing a new synthesis page that cites research sources.
- **Filing a synthesis page with `last_reviewed: <yesterday>`** (or any non-today date). Set to today on filing.
- **Logging a query without including the question text**. Future readers need to know what was asked.
- **Treating a "what's new since" question as needing per-page content** when the answer is in `docs/log.md`, `docs/research/updates.md`, or `docs/journal/YYYY-MM.md`.
- **Re-deriving a comparison the user has already filed before**. Check `docs/research/ideas/` first; if a page exists, point to it.
- **Putting wiki-links to `docs/adrs/...` or `docs/code/...` in the synthesis page's `sources:` frontmatter**. Those go in the body. `sources:` is research-source slugs only.
- **Logging a query proactively when the user didn't ask.** Off by default; opt in only.
