---
name: transition-brief
description: "Publish or abandon a draft brief, updating lifecycle metadata and indexes without changing its body."
disable-model-invocation: true
metadata:
  tags: "briefs, state-machine, transitions, lifecycle"
  bundles: "crux-docs"
  risk_level: "low"
  triggers: "publish brief | abandon brief | close out a brief | mark brief published | mark brief abandoned | transition BRIEF-<slug>"
  routing_note: "Transitions a brief `draft → published | abandoned`; mutates only frontmatter, never the body; updates the rollup + writes a `brief` op."
---

# Transition Brief

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

The status state machine for **briefs** — the pre-decision exploration documents `propose-brief` scaffolds. This skill closes the lifecycle that `propose-brief` opens: a brief is born `draft`, and it ends either `published` (the exploration is preserved as a standing reference) or `abandoned` (the exploration was dropped without becoming a decision). Two named transitions — `publish` and `abandon` — each enforcing the single allowed transition out of `draft`.

Why a dedicated skill: `propose-brief` opens the lifecycle but nothing else closes it, and without a closer the only path to publish/abandon would be hand-editing frontmatter — which the docs schema otherwise forbids. This skill is the sanctioned closer, mirroring `transition-adr` for ADRs. `docs/AGENTS.md` §6's `brief` op names `transition-brief` as a second writer.

Core principle: **the state machine is the contract, and the body is frozen.** A brief's body is human-authored (per `docs/AGENTS.md` §2 ownership); this skill mutates ONLY the `status` and `updated_at` frontmatter between the `---` fences and rewrites the body byte-for-byte. Every transition that isn't explicitly allowed is refused with a clear error.

```
draft ──publish──▶ published
  │
  └──abandon──▶ abandoned
```

Allowed transitions, exhaustively:

| From | Op | To | Notes |
|------|-----|------|-----|
| draft | publish | published | The exploration stands as a reference. |
| draft | abandon | abandoned | Exploration dropped; body kept for the historical record. |

Everything else is **REFUSED** with an error message naming the disallowed transition. There is no path *out of* `published` or `abandoned`, and no path *into* `draft` (that's `propose-brief`'s job, at creation only).

## When to use

- User says: "publish brief", "abandon brief", "close out a brief", "mark BRIEF-<slug> published", "mark BRIEF-<slug> abandoned", "transition BRIEF-<slug>".
- After an exploration concludes — either it stands as a standing reference (`publish`) or it was dropped without becoming a decision (`abandon`).
- As the natural follow-on when `propose-brief` hands a `draft` brief to a human who later finishes the exploration.

Do **not** use this skill for:
- Creating a new brief (that's `propose-brief`).
- Editing a brief's body — refuse, even if asked. The body is human-authored; this skill only flips frontmatter status.
- "Un-publishing" or "un-abandoning" a brief. The state machine is forward-only out of `draft`; a re-opened exploration is a new brief.
- Transitioning an ADR (that's `transition-adr`).
- Promoting a brief to a decision — that's `propose-adr` (cite the brief with `--related-briefs BRIEF-<slug>`); publishing a brief is not the same as deciding.

## Inputs

Subcommand form: `transition-brief <op> <slug>` where:

- `<op>`: one of `publish | abandon`.
- `<slug>`: the brief slug being transitioned (e.g., `caching-strategy`), with or without the `BRIEF-` prefix and `.md` suffix; normalize to the bare slug.

## The pipeline

Execute in order. Read the brief before any write. Validation (steps 1–2) visibly precedes the side-effecting writes (steps 3–5).

### 1. Resolve and validate the brief

- Normalize `<slug>`: strip any leading `BRIEF-` and trailing `.md`, lowercase nothing else (the slug is already lowercase by construction).
- **Validate the slug**: `<slug>` MUST match `^[a-z0-9-]+$`. If it does not, **STOP** with a BROKEN error naming the offending slug.
- `${FILE}` = `docs/briefs/BRIEF-${slug}.md`. Locate it.
  - If zero matches, **STOP** with a BROKEN error ("no brief `BRIEF-${slug}.md` under `docs/briefs/`").
  - If more than one path resolves (e.g. a glob collision), **STOP** with a BROKEN error.
- **Path-escape guard**: resolve `${FILE}` and confirm the resolved path stays **UNDER** `docs/briefs/`. If the slug — even one that passed the regex — produces a resolved path that escapes `docs/briefs/` (path traversal), **STOP** with a BROKEN error. (`^[a-z0-9-]+$` already excludes `/` and `.`; the resolved-path check is the belt-and-suspenders defense.)
- Read the file. Parse the frontmatter. Read the current `status:` field.

### 2. Validate the status transition

Look up `(current_status, op)` in the allowed-transitions table above.

- `(draft, publish)` → allowed, destination `published`.
- `(draft, abandon)` → allowed, destination `abandoned`.
- Any other pair → **REFUSE** with a clear message naming the current status and the requested op, e.g.:
  - For `(published, *)` or `(abandoned, *)`: "BRIEF-${slug} is already `${current_status}`; the briefs lifecycle is forward-only out of `draft`. A re-opened exploration is a new brief (`propose-brief`)."
  - For an attempt to set `draft` (e.g. published→draft): "Cannot transition a brief back to `draft`. The state machine only allows `draft` → `published` and `draft` → `abandoned`. Scaffold a new brief with `propose-brief` if you need a fresh exploration."

If the target brief is already in the requested destination state, refuse with "BRIEF-${slug} is already `${current_status}`; no-op refused." Do not write anything.

### 3. Mutate ONLY the `status` + `updated_at` frontmatter

`${TODAY}` = today's date (`YYYY-MM-DD`).

- Read the file as bytes. Identify the frontmatter block — the content between the opening `---` and the closing `---` fences.
- Within the frontmatter block, mutate ONLY:
  - `status: ${new_status}` (`published` or `abandoned`).
  - `updated_at: ${TODAY}`.
- **Rewrite the body byte-for-byte.** Read everything after the closing `---` fence and rewrite it identically — never reformat, re-wrap, or touch a single line outside the frontmatter block.
- Preserve frontmatter key order, comments, quote style, and trailing-newline conventions. Do NOT touch `created_at`, `title`, `slug`, `type`, `authors`, `tags`, `related_adrs`, `related_research`, or any other field.
- Write `${FILE}` back. If the write fails, **STOP** — no index/log changes.

### 4. Update `docs/index.md` brief rollup

- Under the `## Briefs (N)` section, find the existing row for this brief and update it to:
  - `- [[briefs/BRIEF-${slug}]] — \`${new_status}\` — \`updated_at: ${TODAY}\``
- The `(N)` count is **unchanged** (the brief still exists; only its status changed) — but verify it still matches the actual `docs/briefs/BRIEF-*.md` file count.
- Update the `_Last updated:_` line to `${TODAY}`.

### 5. Append to `docs/log.md`

Prepended (newest first), using the `brief` op (`docs/AGENTS.md` §6 names `transition-brief` as that op's second writer):

```markdown
## [${TODAY}] brief | BRIEF-${slug}: ${old_status} → ${new_status}

Transitioned BRIEF-${slug} from `${old_status}` to `${new_status}`. Body unchanged.
```

The body is the transition format `BRIEF-<slug>: <old-status> → <new-status>`. Keep it terse — `log.md` is for grep, not reading.

### 6. Hand off to the user

- Confirm the new status and the file path.
- Remind: the body of the brief was not touched; only the `status`/`updated_at` frontmatter changed.
- For `published`, optionally note that an ADR can now cite this brief via `propose-adr --related-briefs BRIEF-${slug}`. For `abandoned`, note the body is preserved for the historical record.

## Verification checklist

- [ ] `<slug>` matched `^[a-z0-9-]+$` and `${FILE}` resolved **under** `docs/briefs/` (no path-escape).
- [ ] The transition was one of `draft → published` or `draft → abandoned`; any other was refused.
- [ ] Brief frontmatter `status:` equals the new state (`published` or `abandoned`).
- [ ] Brief frontmatter `updated_at:` equals `${TODAY}`.
- [ ] `created_at` and every other frontmatter field are **unchanged**.
- [ ] **Brief body bytes are byte-identical to pre-write** (nothing outside the frontmatter block changed).
- [ ] `docs/index.md` `## Briefs (N)` row for this brief shows the new status and `updated_at: ${TODAY}`; the `(N)` count is unchanged and still matches the file count.
- [ ] `docs/index.md` `_Last updated:_` is `${TODAY}`.
- [ ] `docs/log.md` has a new `## [${TODAY}] brief | BRIEF-${slug}: ${old_status} → ${new_status}` entry at the top.
- [ ] No ADR and no other brief was created or modified.

## Red flags — STOP and reconsider

- About to write a transition that isn't in the allowed table (`draft → published`, `draft → abandoned`). REFUSE with the error message — including any attempt to move *into* `draft` (e.g. published→draft) or *out of* a terminal state.
- **About to touch any line outside the frontmatter block.** The brief body is human-authored and frozen here — read the body bytes between the closing `---` fence and EOF and rewrite them identically. Editing, re-wrapping, or "cleaning up" the body is forbidden, even if asked.
- About to mutate any frontmatter field other than `status` and `updated_at`. Touch nothing else — not `created_at`, not `title`, not `tags`.
- **About to act on a slug that doesn't match `^[a-z0-9-]+$`, or whose resolved path escapes `docs/briefs/`.** STOP with a BROKEN error — never follow a path-traversal slug.
- About to "no-op" silently when the brief is already in the destination state. Refuse with a clear message — the user thought a transition was needed; tell them it wasn't.
- About to rewrite the frontmatter with reformatted YAML (different key order, different quote style). Preserve byte layout outside the two keys you're explicitly mutating.
- About to bump the `## Briefs (N)` count. The file count didn't change on a status transition. Don't bump.
- About to use the `journal` op (or any op other than `brief`) for the log entry. The `brief` op is canonical for both `propose-brief` (scaffold) and `transition-brief` (transition).

## Rationalization table

| Excuse | Reality |
|--------|---------|
| "The brief body has a typo — while I'm flipping status I'll fix it." | The body is frozen here. This skill touches ONLY `status`/`updated_at`. Body edits are the human's; refuse. |
| "The user wants to re-open a published brief as `draft` — close enough to a transition." | The state machine is forward-only out of `draft`. A re-opened exploration is a new brief (`propose-brief`). Refuse the backward move. |
| "Publishing the brief means it became a decision — I'll also write the ADR." | Publishing ≠ deciding. `transition-brief` closes the brief lifecycle; recording the decision is `propose-adr` (cite the brief with `--related-briefs`). Two skills. |
| "The slug has a slash in it pointing at a sibling dir — I'll resolve it anyway." | A slug with `/` fails `^[a-z0-9-]+$` and/or escapes `docs/briefs/`. STOP with a BROKEN error; never follow a traversal path. |
| "BRIEF-foo is already `abandoned`; I'll just confirm and exit silently." | Silent no-op is indistinguishable from success. Refuse explicitly so the user notices their model is wrong. |
| "I'll re-emit the frontmatter through a YAML library to tidy it." | A lossy YAML round-trip reorders keys and changes quoting — diff noise that destroys git-blame and risks the body. Parse-then-mutate-then-byte-merge only the two keys. |
| "I'll log this under `journal` since there's narrative." | Brief transitions are a `brief` op, not a `journal` op (per `docs/AGENTS.md` §6). |

## Common mistakes

- **Touching the body.** The single most important invariant: mutate ONLY the frontmatter `status`/`updated_at`; rewrite the body byte-for-byte. Mirrors `transition-adr`'s body-freeze.
- **Forgetting to update `updated_at`** along with `status`. Both move on every transition; `created_at` never does.
- **Bumping the `## Briefs (N)` count** on a status transition — the file count is unchanged; only the row's status/date update.
- **Allowing a backward or terminal-state transition** — only `draft → published` and `draft → abandoned` are legal.
- **Updating only the index, not the file** — the file's frontmatter is source of truth; the `docs/index.md` rollup mirrors it. Write the file first.
- **Using the wrong log op** — it's `brief`, not `journal`, with body `BRIEF-<slug>: <old-status> → <new-status>`.
- **Skipping the slug/path validation** — a slug must match `^[a-z0-9-]+$` and resolve under `docs/briefs/` before any read or write.

## See also

- `propose-brief` — scaffolds the `draft` brief this skill closes out (the lifecycle opener).
- `transition-adr` — the symmetric ADR state machine this skill mirrors (body-freeze, frontmatter-only mutation, clear refusals).
- `propose-adr` — the decision artifact that may cite a published brief (`--related-briefs BRIEF-<slug>`).
- `audit-docs` — enforces brief↔ADR consistency; CHK-ADR-11 cross-checks brief draft-states.
- `docs/AGENTS.md` §2 (ownership — briefs are human-authored), §4 (briefs write rules), §5 (index brief rollup format), §6 (`brief` log op), §7.A (SKILL.md frontmatter contract), §9 (slug rule), §10 (skill-invocation table).
