---
name: propose-brief
description: "Scaffold a draft brief for pre-decision exploration. Leave its body for human authorship; do not create an ADR or change status."
metadata:
  tags: "briefs, scaffolding, exploration, adr-precursor"
  bundles: "crux-docs"
  risk_level: "low"
  triggers: "draft a brief | scaffold a brief | new brief | BRIEF for <topic> | I want to explore X before deciding"
  routing_note: "Scaffolds the file + frontmatter; body is human-authored."
---

# Propose Brief

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

The scaffolding operation for a **brief** — the short, pre-decision exploration document that an ADR later compresses into a chosen path. Briefs are the one concern whose **body is human-authored** (per `docs/AGENTS.md` §2 ownership) — with the single §2(b) exception of a whiteboarding-authored session carried in via `--from-inbox`: this skill creates the file, populates the frontmatter, wires the indexes, and logs the operation — then stops, leaving the body for the user to write (or, on the `--from-inbox` path, populating the body from the session).

Pairs with `propose-adr` (an ADR cites a brief via `related_briefs:`; `audit-docs` back-populates the brief's `related_adrs:`).

Core principle: **scaffold, don't author** — with one sanctioned exception. A brief's value is the human's framing of the problem; a pre-filled body is wrong-but-shipped content, so by default this skill leaves the body a stub. The **sole exception** (per `docs/AGENTS.md` §2(b)) is a **whiteboarding-authored session brief** carried in via `--from-inbox`: that content was already machine-authored through the `whiteboarding` dialogue, so it becomes the body. In every other case: provide the structure; let the user write.

## When to use

- User says: "draft a brief", "scaffold a brief", "new brief", "BRIEF for <topic>", "I want to explore X before deciding", "write up the exploration for X".
- After a discussion surfaces a question that needs exploration *before* an ADR can be proposed.
- When `propose-adr` reveals "this needs more exploration first" — the natural hand-off.

Do **not** use this skill for:
- Writing the brief's body — that's the user's job, **unless** `--from-inbox` supplies a whiteboarding session (the §2(b) machine-authored exception, which carries the session content into the body). Absent that flag, refuse to pre-fill the exploration content.
- Transitioning a brief's status (`draft` → `published`, `draft` → `abandoned`) — that's the `transition-brief` skill (the lifecycle closer, the symmetric counterpart to `transition-adr`). Do NOT hand-edit the `status:` frontmatter to publish or abandon — the schema forbids hand-editing frontmatter outside a transition skill. **Abandon path:** when an exploration is dropped without becoming a decision, run `transition-brief` to move it `draft` → `abandoned` (it validates the transition, updates `updated_at` + the `docs/index.md` rollup, and writes a `brief` log op); the brief body stays as-is for the historical record.
- Recording a decision — that's `propose-adr` (cite the brief with `--related-briefs BRIEF-<slug>`).
- Promoting a brief to an ADR — that's `propose-adr`, not this skill.

## Inputs

- `--title "<exploration title>"` — required. Free-form (briefs are exploratory, not declarative like ADRs). If omitted, prompt the user; confirm before allocation.
- `--tags <comma-list>` — optional.
- `--authors <comma-list>` — optional. Defaults to the actual user's handle, resolved at runtime (e.g. from `git config user.name` or the session user) — never a hardcoded name.
- `--related-adrs <comma-list>` — optional. ADR ids that already reference this brief (usually empty at creation; `audit-docs` back-populates as ADRs cite it).
- `--related-research <comma-list>` — optional. Research-source slugs (under `docs/research/sources/`) to pre-link. Populates a `related_research:` frontmatter field IF the template carries one. **If `--related-research` is passed but `BRIEF-template.md` has no `related_research:` field, do NOT silently drop it — emit a WARN** ("`--related-research` was provided but `BRIEF-template.md` has no `related_research:` field; the values were not recorded. Add the field to the template, or capture these sources in the brief body / the consuming ADR's `related_research:`.") and proceed with the rest of the scaffold. Default empty.
- `--from-inbox <session-path>` — optional. Path to a **whiteboarding session** dropped in `docs/inbox/` (usually supplied by `process-inbox` when it dispatches a whiteboarding-authored item). This is the **one sanctioned exception** to scaffold-only (per `docs/AGENTS.md` §2(b)): the session was machine-authored by the `whiteboarding` skill / `brainstormer` agent, so its content becomes the brief **body** (a "machine-authored session brief") rather than being left as the stub. Used ONLY for whiteboarding sessions; never to pre-fill a human-authored brief. Default: absent (scaffold-only).

## The pipeline

Execute in order.

### 1. Compute the slug and path

- `${SLUG}` = kebab-case of `${title}`, ASCII only, truncated to ~50 chars (per `docs/AGENTS.md` §9 slug rule). Trim trailing hyphens.
- `${FILE}` = `docs/briefs/BRIEF-${SLUG}.md`.
- **Collision check:** if `${FILE}` already exists, **STOP** — do not overwrite. Tell the user to pick a different title or edit the existing brief. (Briefs have no monotonic counter; the slug IS the key.)

### 2. Instantiate from the template

- Read `${CRUX_PLUGIN_ROOT}/templates/BRIEF-template.md`. The canonical frontmatter shape lives there; substitute, do not re-author the field list.
- Substitute frontmatter values:
  - `title: "${title}"`
  - `slug: ${SLUG}`
  - `type: brief`
  - `status: draft`
  - `created_at: ${TODAY}`
  - `updated_at: ${TODAY}`
  - `authors: ${authors_list}` (default the configured handle)
  - `tags: ${tags_list}` (or `[]`)
  - `related_adrs: ${related_adrs_list}` (or `[]`)
  - `related_research: ${related_research_list}` — ONLY if `--related-research` was passed AND `BRIEF-template.md` carries a `related_research:` field; do NOT invent the field. **If `--related-research` was passed but the template lacks the field, emit the WARN described in Inputs (do not silently drop the values) and omit the field** — the values are surfaced to the user rather than lost.
- **Leave the body as the template stub** (the explainer blockquote + `<body>` placeholder). Do NOT write exploration content — the human authors it. **Exception — `--from-inbox <session-path>` (whiteboarding only, per §2(b)):** replace the `<body>` placeholder with the whiteboarding session's content (strip any leading `crux:` directive line and the session's own top-level title so the brief's `# <Title>` heading isn't duplicated; preserve the exploration prose/options/decision verbatim). The frontmatter is still written by this skill; only the body comes from the session. This is the machine-authored path, NOT a licence to author a human brief's body.

### 3. Write the file

- Write `${FILE}`. If the write fails, **STOP** — no index/log changes.

### 4. Update `docs/index.md` brief rollup

- Under the `## Briefs (N)` section, add a row: `- [[briefs/BRIEF-${SLUG}]] — \`draft\` — \`updated_at: ${TODAY}\``.
- Bump the `(N)` count to the actual number of `docs/briefs/BRIEF-*.md` files.
- If no `## Briefs` section exists yet (first brief), create it in the correct concern order (after ADRs, before Journal — see `docs/AGENTS.md` §5).
- Update the `_Last updated:_` line to `${TODAY}`.

### 5. Append to `docs/log.md`

Prepended (newest first), using the `brief` op from the `docs/AGENTS.md` §6 canonical enum:

```
## [${TODAY}] brief | scaffolded BRIEF-${SLUG}
```

Body, 1–2 lines: the title, the file path, `status: draft`. Note the body is human-authored next — or, on the `--from-inbox` path, that the body was populated from the whiteboarding session (§2(b)).

### 6. Hand off to the user

- Tell the user: brief scaffolded at `${FILE}`, status `draft`.
- **Default path (no `--from-inbox`) — explicitly instruct: the body is yours to write** — replace the template stub with the exploration (problem framing, options, context).
- **`--from-inbox` path — report that the body was populated from the whiteboarding session** (§2(b) machine-authored exception); invite the user to refine it, not to write it from scratch.
- When a decision crystallizes, run `propose-adr` and cite this brief with `--related-briefs BRIEF-${SLUG}`.

## Verification checklist

- [ ] `docs/briefs/BRIEF-${SLUG}.md` exists.
- [ ] Frontmatter parses as YAML and matches the template's keyset (`title`, `slug`, `type: brief`, `status: draft`, `created_at`, `updated_at`, `authors`, `related_adrs`, `tags`).
- [ ] `created_at` and `updated_at` both equal `${TODAY}`.
- [ ] The body is the template stub — NOT pre-filled exploration content (UNLESS `--from-inbox` carried a whiteboarding session, in which case the body is the session content per §2(b) and the leading directive/duplicate title were stripped).
- [ ] `docs/index.md` `## Briefs (N)` count matches the actual `docs/briefs/BRIEF-*.md` file count, and a row for this brief is present.
- [ ] `docs/index.md` `_Last updated:_` is `${TODAY}`.
- [ ] `docs/log.md` has a new `## [${TODAY}] brief |` entry at the top.
- [ ] No ADR was created or modified (that's `propose-adr`).

## Red flags — STOP and reconsider

- About to write exploration content into the brief body **without `--from-inbox`**. NEVER — a human-authored brief's body is the user's; provide the stub only. (WITH `--from-inbox`, carrying a whiteboarding session's content into the body is the sanctioned §2(b) exception — that is the ONE case where the body is machine-authored.)
- About to overwrite an existing `BRIEF-${SLUG}.md`. Briefs are not regenerated; refuse and surface the collision.
- About to transition a brief's `status` (e.g. to `published` or `abandoned`). Out of scope for this skill — use `transition-brief`. Do NOT hand-edit the `status:` frontmatter.
- About to use the `journal` op (or any op other than `brief`) for the log entry. The `brief` op is the canonical op for brief scaffolding (per `docs/AGENTS.md` §6).
- About to skip the `docs/index.md` brief-rollup update. Downstream `audit-docs` count checks depend on it; update on every write.
- About to populate `related_adrs:` with an ADR that doesn't exist yet. Leave it `[]` unless a real ADR already cites the brief — `audit-docs` back-populates.

## Rationalization table

| Excuse | Reality |
|--------|---------|
| "The user clearly knows what they want to explore — I'll draft the brief body to save them time." | The brief body is the user's framing; pre-filling it produces wrong-but-shipped content. Scaffold and hand off. (The sole machine-authored exception is a whiteboarding session carried in via `--from-inbox`, §2(b) — not a licence to draft a human brief.) |
| "There's already a `BRIEF-<slug>.md` — I'll just append to it." | Collision = refuse. The user either wants a different title or to edit the existing brief directly; don't silently merge. |
| "Briefs are informal — I'll skip the frontmatter and just write a heading." | The frontmatter (`status`, `related_adrs`) is what `audit-docs` uses for the brief↔ADR bidirectional check. Populate it from the template. |
| "I'll log this under `journal` since there's narrative." | Brief scaffolding is a `brief` op, not a `journal` op. Briefs aren't journal entries — they're pre-decision documents. |
| "The user said 'brief on X and propose the ADR' — I'll do both." | Two skills. Scaffold the brief here; the user (or a follow-up `propose-adr`) writes the decision once the brief's exploration is done. |
| "No `## Briefs` section in `docs/index.md` yet — I'll skip the rollup." | Create the section in the correct concern order. A missing rollup is drift `audit-docs` will flag. |

## Common mistakes

- **Pre-filling the body** because the topic seems clear. The body is the human's — the sole exception is a whiteboarding session carried in via `--from-inbox` (§2(b)).
- **Using `created` instead of `created_at`** — match the template's exact key names.
- **Forgetting to bump the `## Briefs (N)` count** by counting actual files, not incrementing the prior number blindly.
- **Writing `authors: []`** — default to the configured handle; a brief has at least one author.
- **Slug ending in a hyphen** after truncation — trim it.
- **Putting the new brief's own slug in `related_adrs:`** — that field is for ADR ids, populated as ADRs cite the brief.

## See also

- `propose-adr` — the decision artifact that consumes a brief (`--related-briefs BRIEF-<slug>`).
- `audit-docs` — enforces the brief↔ADR bidirectional consistency (`related_briefs:` ↔ `related_adrs:`).
- `docs/AGENTS.md` §2 (ownership — briefs are human-authored), §4 (briefs concern), §9 (slug rule), §10 (skill-invocation table).
- Template: `crux/templates/BRIEF-template.md`.
