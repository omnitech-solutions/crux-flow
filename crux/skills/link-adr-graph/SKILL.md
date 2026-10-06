---
name: link-adr-graph
description: "Regenerate the ADR lineage graph and table from supersedes and amends relationships. Does not modify decisions."
metadata:
  tags: "adrs, lineage, derivation, regenerative, visualization"
  bundles: "crux-docs"
  risk_level: "low"
  triggers: "show ADR lineage | render ADR graph | ADR dependency graph"
  routing_note: "Regenerates `docs/adrs/lineage.md` (Mermaid + table)."
---

# Link ADR Graph

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

The derivation-side counterpart to `propose-adr` / `transition-adr`. ADRs form a graph — each can `supersedes:` predecessors, `amends:` predecessors (one-way refinement; the amended ADR is never mutated), and be `superseded_by:` a descendant. `audit-docs` checks the graph's *integrity*; this skill *emits* it as a readable artifact so a reader can answer "what's the canonical decision on X?" without hand-walking frontmatter.

Output is `docs/adrs/lineage.md`, a regenerated artifact governed by the contract below.

### The derive-only / byte-stable contract (canonical statement)

> **Derive, don't decide; regenerate byte-stably.** A derive-only renderer reads an upstream source of truth and emits a view of it — it **never mutates** the source (no proposing, transitioning, advancing, or "fixing" the upstream while rendering). Its output is a **regenerated artifact** on the same model as `docs/code/` and `catalog/skills.json`: the body is wholly rewritten on every run; hand-edits are blown away. The body carries **no timestamps and no per-run-varying values**, so the artifact is **byte-identical across runs** given the same input (deterministic ordering throughout); "when it ran" lives only in the `docs/log.md` entry, never in the body.

For `link-adr-graph`, the upstream source is ADR frontmatter (read-only), and the regenerated artifact is `docs/adrs/lineage.md`. Promptbook progress uses the same derive-only principle through `run-promptbook` status and its retained renderer.

## When to use

- User says: "show ADR lineage", "render ADR graph", "what supersedes what", "ADR dependency graph", "how do the ADRs relate".
- After a supersession or amendment (a good follow-up to `transition-adr`) to refresh the rendered graph.
- Periodically as the ADR count grows and hand-walking chains gets expensive.

Do **not** use this skill for:
- Checking graph *integrity* (dangling `superseded_by`, missing back-pointers) — that's `audit-docs`.
- Proposing, accepting, deprecating, or superseding an ADR — that's `propose-adr` / `transition-adr`.
- Status reporting on Proposed ADRs — out of scope (deliberately cut as not-lineage); use `audit-docs` or `query-docs`.

## The pipeline

Execute in order. Read-only against ADRs.

### 0. Regenerate via the vendored engine (primary path)

`lineage.md` has a **vendored, deterministic regenerator** — run it, don't hand-roll one:

```
uv run "${CRUX_PLUGIN_ROOT}/scripts/generate-lineage.py"        # rewrites docs/adrs/lineage.md
uv run "${CRUX_PLUGIN_ROOT}/scripts/generate-lineage.py" --dry-run   # the DRIFT GATE: exit 0 clean, exit 1 + diff on stale
```

(Source checkout, where `${CRUX_PLUGIN_ROOT}` is unset: `<checkout>/crux/scripts/generate-lineage.py`.) The engine walks ADR frontmatter and rewrites `docs/adrs/lineage.md` byte-stably; `--dry-run` is the drift gate that fails when the on-disk file is stale (it belongs in `tools/tests` and any pre-release check). This regenerator + gate pair is the artifact's conformance to the "derived artifacts need a regenerator + a drift gate" rule. Steps 1–4 below **document what the engine does** (and are the hand-fallback only if the engine can't be run); after step 0 succeeds, skip to step 5 (the log entry).

### 1. Walk ADR frontmatter

- Glob `docs/adrs/ADR-*.md`. For each, parse frontmatter: `id`, `title`, `status`, `tags`, `supersedes` (list), `superseded_by` (id or null), `amends` (list, optional).
- Build node records and the edge sets:
  - **supersedes** edges: `X → Y` for each `Y` in `X.supersedes`.
  - **amends** edges: `X ⇢ Y` for each `Y` in `X.amends`.

### 2. Emit the Mermaid graph

Render a `mermaid` fenced block (plain markdown — no external rendering; the consumer's viewer renders it). Determinism rules:
- Nodes declared in ascending ADR-id order.
- **An ADR with no `supersedes`/`amends`/`superseded_by` edge is an isolated node — declare it anyway.** Every ADR is a node regardless of connectivity; never omit an ADR just because it participates in no relationship (an unrelated ADR is still part of the set the reader is scanning).
- Edges declared grouped by type (all supersedes, then all amends), each group in ascending source-id order.
- Style/label superseded nodes distinctly from current ones (e.g. a note or class) so the reader sees at a glance which decisions are no longer in force.
- Use solid arrows for `supersedes`, dashed for `amends`.

### 3. Emit the lineage table

A markdown table, one row per ADR in ascending id order: `id | title | status | supersedes | amends | superseded_by`. Empty relationships render as `—`.

Then a short **topic-cluster** view: group ADRs by shared `tags` (e.g. all `schema`/`dry` ADRs) so a reader can find "the canonical decision on X" by cluster. Clusters in alphabetical order; ADRs within a cluster in ascending id order.

### 4. Write `docs/adrs/lineage.md` (regenerated)

- Write the whole file: a header noting it is regenerated (hand-edits blown away), the Mermaid block, the lineage table, the topic clusters.
- **No timestamps in the body** — the artifact must be byte-identical across runs given the same ADR set (matching the `docs/code/` determinism contract). The "generated" date, if shown, goes only in a way that doesn't break byte-stability — prefer omitting it from the body entirely and relying on the log entry for "when."

### 5. Log under the `adr` op

Prepend to `docs/log.md` (**reuse the existing `adr` op** — do NOT introduce a new op; the §6 enum is closed):

```
## [YYYY-MM-DD] adr | regenerated lineage
```

Body, 1 line: node count, edge counts (supersedes / amends).

### 6. Bump `docs/adrs/index.md` `_Last updated:` to today.

**A lineage regen does NOT touch `docs/index.md`** (the master rollup). The ADR *count* in `docs/index.md` is unchanged by regenerating the lineage view (no ADR is created, transitioned, or removed — `lineage.md` is a derived artifact), so this skill updates only the concern-local `docs/adrs/index.md` `_Last updated:` line and the `docs/log.md` entry. Leave `docs/index.md` alone; bumping it here would author spurious drift.

## Verification checklist

- [ ] `docs/adrs/lineage.md` exists and contains a `mermaid` block + the lineage table + topic clusters.
- [ ] Every ADR file under `docs/adrs/` appears as a node and a table row — **including ADRs with no edges (isolated nodes); none omitted for lack of relationships.**
- [ ] Every `supersedes`/`amends`/`superseded_by` edge from frontmatter is rendered; no invented edges.
- [ ] No timestamps in the body — re-running the skill produces a byte-identical `lineage.md`.
- [ ] No ADR file was modified (read-only).
- [ ] `docs/index.md` (the master rollup) was NOT modified — only the concern-local `docs/adrs/index.md` `_Last updated:` line was bumped.
- [ ] `docs/log.md` has a new `## [YYYY-MM-DD] adr | regenerated lineage` entry (the `adr` op, NOT a new op).

## Red flags — STOP and reconsider

- About to put a timestamp (or any per-run-varying value) in the `lineage.md` body. NEVER — it breaks byte-stability. Time lives in the log entry only.
- About to add an "Open questions from Proposed ADRs" section. Out of scope (that's status reporting, not lineage).
- About to render an image (SVG/PNG). Out of scope — Mermaid markdown only; a render dependency was deliberately rejected.
- About to edit an ADR to "fix" a missing back-pointer you noticed while walking. NOT this skill's job — that's `audit-docs` / `transition-adr --repair`. Surface it, don't fix it here.
- About to introduce a new log op (e.g. `lineage`). Reuse `adr` — the `docs/AGENTS.md` §6 enum is closed.
- About to hand-edit `lineage.md` to tweak the rendering. It's regenerated; edit the generation logic (this skill), not the output.

## Rationalization table

| Excuse | Reality |
|--------|---------|
| "A 'generated at HH:MM' header would be helpful." | It breaks byte-stability and makes every run a spurious diff. The log entry records when; the body stays deterministic. |
| "While walking I found a dangling `superseded_by` — I'll just fix the ADR." | That's an integrity finding for `audit-docs`. This skill is read-only against ADRs; surface it, don't mutate. |
| "Proposed ADRs are interesting — I'll add an open-questions section." | That was explicitly cut as status-reporting, not lineage. Keep the artifact to the graph. |
| "A rendered PNG would look nicer than Mermaid source." | Out of scope; Mermaid markdown only. The consumer's viewer renders it. |
| "I'll introduce a `lineage` op so the log is precise." | The council chose to reuse `adr`. Adding an op fragments the enum without justification. |

## Common mistakes

- **Non-deterministic ordering** (e.g. dict iteration order) producing a different `lineage.md` each run. Sort everything by ADR id / cluster name.
- **Putting a timestamp in the body** — the single most common byte-stability break.
- **Rendering only `supersedes` and forgetting `amends`** (the one-way refinement edges). Both are part of the graph.
- **Citing a non-`adr` op in the log entry.** It's `adr`.
- **Treating `lineage.md` as hand-editable.** It's regenerated; the next run overwrites it.

## See also

- `propose-adr` / `transition-adr` — create and transition the ADRs this skill renders.
- `audit-docs` — checks the graph's integrity (the complement to this skill's rendering).
- `docs/AGENTS.md` §4 (adrs), §11 (cross-concern bidirectional consistency rules).
