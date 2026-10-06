---
name: patch-cycle
description: "Plan a small, reversible, non-architectural change as a five-prompt cycle with declared scope, council, and review gates."
metadata:
  tags: "promptbooks, workflow, cycle, patch, small-change"
  bundles: "crux-docs"
  risk_level: "low"
  triggers: "patch this | patch cycle | run a patch tier on X | small fix with the gates | this is too small for a cycle but it still needs a council and a review"
  routing_note: "The third cycle tier: a small reversible non-architectural change, five phases (verify, plan, implement, review, summary) at one prompt each, so the formula is the constant 5 and the floor is 5. Declares a `blast_radius` at authoring; the verify council reviews it for proportion and the archive precondition checks it against the paths the run changed, drawn from git. Work needing an ADR is `dev-cycle`; a fix too large to bound is `iterate`. See `patch-cycle/SKILL.md`."
---

# Patch Cycle

For new Crux Flow work, resolve `.crux-flow.yml` first and use the `flow`
skill for aggressive, balanced, or thorough. This upstream patch procedure
continues to govern upstream mode and existing upstream patch records.

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

A **patch cycle** is the third cycle tier. It exists because work too small for the thirteen-prompt floor used to lose both gates rather than getting smaller ones: the only place it fit was `author-promptbook`, which enforces nothing.

A `patch` book runs **five phases in order — verify, plan, implement, review, summary — one prompt each**. Its formula is the constant **5** and its floor is **5**.

**This is a third tier, not a relaxation of two.** The `4N+4M+3K+2` formula and the thirteen-prompt floor that `dev-cycle` and `iterate` are held to are unchanged. A floor that bends on judgment is not a floor; a genuinely smaller tier with its own countable contract is.

### The two gates a patch still pays

- **The council gate is a postcondition of the verify phase.** The council reviews the diagnosis, the minimal approach, and the declared blast radius.
- **The review gate is a postcondition of the review phase.** Quality gates plus an independent review pass.

Both are positional, guaranteed by the fixed phase sequence. A `patch` book that archives a completed run has passed both.

### The blast radius

A `patch` book declares its blast radius — a set of repository paths — in its `blast_radius` field, **before its run starts**. Two guards hold it, and only the second is mechanical:

1. **Proportion, at the verify council.** The council refuses the patch framing when the radius is drawn wider than the work needs.
2. **Containment, before completing the final prompt and again at archive.** Run `check-blast-radius.py` before the final `run-promptbook advance`; `archive-promptbook` repeats it against the current Git state. The check compares the declaration against paths the run actually changed, drawn from Git.

**The declaration is fixed once the run starts, and no prompt may widen it.** `blast_radius` sits inside the frozen-plan subset that `book_content_hash` covers, so widening it mid-run moves the hash and `audit-docs` CHK-PB-BIND fires.

**When containment fails before completion**, leave the run active and do not mark the final prompt done. Correct the overshoot if possible; otherwise deliberately abandon the active run and re-author in the fitting tier. If the final recheck fails after completion because the Git state changed, retain the truthful completed run as completed but unarchived. Do not attempt forbidden retroactive abandonment or rewrite its history.

**The honest weakness:** containment is machine-checked, proportion is a council judgment. A blast radius waved through by the council passes the archive check for free.

### Three invariants the skill MUST enforce (machine-checked)

`validate-promptbook.py`'s cycle-coverage pass fails a `patch` book that violates any of these, and step 7 below refuses to ship it:

1. **`total_prompts == len(prompts) == 5`** — the constant, not a formula over modules.
2. **The phase sequence is exactly `verify, plan, implement, review, summary`, in order,** and no prompt carries a `module_tag`. A patch book has phases, not modules, so it carries no `modules` block either.
3. **`blast_radius` is non-empty and every entry passes the declared-path grammar** — `invalid_blast_radius_entry` in `${CRUX_PLUGIN_ROOT}/scripts/validate-promptbook.py`, the one implementation the authoring validator, this skill, and the archive check all share. Read the function for its rejections rather than trusting a summary; a summary of it here would be a copy that drifts.

## When to use

- User says: "patch this", "patch cycle", "run a patch tier on X", "small fix with the gates", "this is too small for a cycle but it still needs a council and a review".
- A reversible, non-architectural change whose file footprint you can name honestly, up front, and finish in one implement prompt.

Do **not** use this skill for:

- **Work that needs an ADR.** A patch admits a reversible, non-architectural change; work that needs a decision recorded is `dev-cycle`. The archive check enforces this from the other end: an ADR path is never excluded from the comparison, so a patch that writes one fails containment.
- **A fix you cannot bound**, or one too large for a single implement prompt. That is `iterate` — it has the room, and its floor is there for a reason.
- **A defect that passes `fix-directly`'s sizing test** — nameable files, a failing test before the fix, no contract changed, one instance, no review gate wanted. That is `fix-directly`: no book at all.
- **A bespoke plan that needs no gates at all.** That is `author-promptbook`.
- **Authoring a non-cycle plan** (`author-promptbook`) or running a book (`run-promptbook`).

### The cycle taxonomy (the routing question)

> *Deciding something new / architectural* → **`dev-cycle`**. *Fixing something that exists, at a size that needs room* → **`iterate`**. *A reversible fix you can bound by declared paths and finish in one implement prompt* → **`patch-cycle`**. *A defect whose files, failing test, and unchanged contracts you can name now* → **`fix-directly`** (no book). *A bespoke plan that needs no gate* → **`author-promptbook`**.

## Inputs

- **Required**: a working title and a one-paragraph goal describing the change. The goal must be a real description — the verify prompt's agents have no conversation context. One sentence carries all three: the Outcome (what improves for the affected user), the Evidence (what would demonstrate that improvement), and the Constraint (what the change must preserve) — prose inside the same paragraph, sized to a patch: no new field, no form, no gate.
- **Required**: the **blast radius** — the repository paths the change may touch, as repo-relative paths. A directory entry covers everything under it. Draw it as tightly as the work allows; the council will challenge a loose one. **A declaration that reaches the cycle machinery itself — the validator, the archive check, or the schemas — is a self-reference the verify council must refuse**, because the check that would judge the patch is code the patch would be licensed to edit. No mechanical guard can close that; the council is the only reader that can.

When called interactively, elicit both:

> "A patch cycle is five prompts — verify, plan, implement, review, summary — and it declares its blast radius up front. Which repository paths may this change touch? Anything outside them blocks the archive. And does the goal state the Outcome, the Evidence, and the Constraint in one sentence?"

## The pipeline

### 0. Resolve per-repo configuration (.crux)

Run `python3 "${CRUX_PLUGIN_ROOT}/scripts/crux-config.py"` from the repo root (or pass `--repo-root <repo-root>`), and confirm the returned `repo_root` is the repo you are operating in — `source: "discovery:<dir>"` with an unexpected `repo_root` means you resolved the wrong directory, not that no config exists. On exit 1, **STOP** and surface the `{"error": ...}` payload — never fall back to defaults. Use the returned `docs_dir` wherever this skill says `docs/` (per the docs/AGENTS.md §14 normative definition clause). When `artifact_prefix` is non-empty, format the newly allocated book id with it (e.g. `CRX-PB-0040`) — the `NNNN` still comes from the manifest counter; the prefix only changes the formatting.

### 1. Confirm inputs

Confirm the title, the goal, and the blast radius. Validate every declared path against the declared-path grammar before going further (invariant 3 above names the one implementation) — do not re-derive its rejections by hand here. Do not allocate an id until all three are locked.

### 2. Allocate id (write-first ordering)

Read `docs/manifest.yml`; take `promptbook.next_number`; format `PB-NNNN`. **Do NOT increment yet** — write the book (step 5) and validate it (step 6), then increment (step 7). Numbers are never reused.

### 3. Slug

Kebab-case from the title, ASCII, ≤50 chars. Filename: `docs/promptbooks/active/PB-NNNN-<slug>.yaml`. Run dir: `docs/promptbooks/runs/PB-NNNN-<slug>/`.

### 4. Assemble the book

Copy `${CRUX_PLUGIN_ROOT}/templates/patch-promptbook-template.yaml` (the full five-prompt skeleton) and do token substitution only. The phase sequence is fixed — never reorder it, never add a sixth prompt, never drop one to four.

### 5. Write the book

Write the structured `.yaml` document. Top-level keys include `cycle_kind: patch`, `blast_radius:` (the confirmed list), `total_prompts: 5`, `forked_from: null`, and **no `modules` block**. Each prompt carries `phase:` and **no `module_tag`**.

### 6. Validate the book — REFUSE on failure (the gate before ALL side effects)

```bash
uv run "${CRUX_PLUGIN_ROOT}/scripts/validate-promptbook.py" --kind promptbook docs/promptbooks/active/PB-NNNN-<slug>.yaml
```

Exit 0 = clean, proceed. Exit 1 = the book is invalid; the JSON `errors` on stdout name the failing invariant. Delete the written file, fix, re-validate. Any other non-zero with empty stdout is a capability error — surface stderr. **Do not increment the manifest counter, regenerate the index, or write the log entry until this passes.**

### 7. Increment `manifest.yml` atomically

Re-read `manifest.yml`, confirm `promptbook.next_number` is unchanged, increment, write back. If it changed, another invocation raced — STOP, retry.

### 8. Regenerate `docs/promptbooks/index.md`

Walk active/runs/archive (extension-agnostic); rebuild the index. The new book appears in Active at `0/5 (0%)`.

### 9. Update `docs/index.md`

Bump the `## Promptbooks (X active, …)` count by 1; update `_Last updated:` to today.

### 10. Append to `docs/log.md`

```
## [YYYY-MM-DD] promptbook | authored PB-NNNN-<slug> (patch)
```

Body: id, title, `total_prompts: 5`, the declared blast radius, and the note `patch (cycle_kind: patch): five phases, one prompt each`.

## Verification checklist

- [ ] `docs/manifest.yml` `promptbook.next_number` incremented and persisted.
- [ ] `docs/promptbooks/active/PB-NNNN-<slug>.yaml` exists with `format_version: "1"`, `cycle_kind: patch`, `total_prompts: 5`, and `forked_from: null`.
- [ ] Exactly five prompts, `n: 1..5`, whose `phase` values are `verify, plan, implement, review, summary` in that order.
- [ ] No prompt carries a `module_tag`; the book carries no `modules` block.
- [ ] `blast_radius` is non-empty, every entry passes the declared-path grammar (invariant 3), and no entry covers the cycle machinery itself — the validator, the archive check, or the schemas.
- [ ] The declaration is as tight as the work allows.
- [ ] `validate-promptbook.py --kind promptbook` exits 0 — run BEFORE the manifest increment, the index regeneration, and the log entry.
- [ ] `docs/promptbooks/index.md` and `docs/index.md` updated; one `promptbook | authored …` op at the top of `docs/log.md`.

## Red flags — STOP and reconsider

- About to draw the blast radius wide "to be safe", or to declare a whole top-level directory for a two-file change. That is the dodge this tier is most vulnerable to: containment then passes for free and the tier becomes a way to skip rigor rather than to right-size it. Declare what the work touches.
- About to reach for `patch` because the thirteen-prompt floor is inconvenient. The floor is not the defect — it exists because rigor was being skipped. If the work needs `iterate`'s room, it needs `iterate`.
- About to write an ADR inside a patch cycle. An ADR-worthy change is not a patch. Route it to `dev-cycle` — and note that the archive check would fail it anyway, because an ADR path is never excluded from the comparison.
- About to widen `blast_radius` mid-run because the implement phase went further than planned. NEVER — the declaration is frozen by `book_content_hash`, so the edit trips CHK-PB-BIND and hides the overshoot instead of recording it. Abandon the run and re-author in `iterate` or `dev-cycle`.
- About to add a sixth prompt, drop to four, or reorder the phases. The count is the formula and the order is the contract; the validator refuses either way.
- About to add a `modules` block or a `module_tag` "for consistency with the other tiers". A patch has phases, not modules.
- About to start the run before the blast radius is settled. It is fixed at the run's start; there is no later chance to get it right.

## Rationalization table

| Excuse | Reality |
|---|---|
| "It's a one-line fix; the council prompt is overkill." | The council prompt is why this tier exists. A one-line fix with no gate is `author-promptbook`, and that is a legitimate choice — just make it deliberately. |
| "I'll declare `crux/` as the blast radius so the archive check can't fail." | A declaration that cannot fail proves nothing. The council reviews proportion precisely to catch this. |
| "The change spilled into one extra file. I'll just add it to `blast_radius`." | That is the overshoot the check exists to record. Abandon and re-author in the tier that fits. |
| "This needs an ADR, but a patch is faster." | Speed is not the axis. An architectural decision unrecorded is the cost, and it lands on whoever reads the code next. |
| "The work grew past one implement prompt, so I'll do the extra work in the review prompt." | The phases are what make the tier countable. Work that needs two implement passes is an `iterate` cycle. |

## Common mistakes

- **Declaring paths that do not exist yet.** A patch that creates a file must declare the path it will create; the check compares against untracked files too.
- **Declaring a file when the change touches its directory.** A directory entry covers everything under it; a file entry covers only that file.
- **Assuming bookkeeping counts against the radius.** It does not: the run snapshot, the promptbook index, the log, the journal, and the regenerated arch spine are the machinery's own writes and are excluded. Everything else must be declared.
- **Writing `blast_radius` as a string instead of a list.** It is a YAML list of path strings, minimum one entry.
- **Forgetting the run needs a commit boundary.** `run-promptbook start` stamps `base_commit` from `git rev-parse HEAD`. Outside a git work tree it is `null`, and a patch run without it cannot archive as completed.

## See also

- `dev-cycle` — the architectural tier (`cycle_kind: adr`, ≥ 13 prompts, an ADR + council).
- `iterate` — the non-architectural tier with room (`cycle_kind: verify`, ≥ 13 prompts, a council-reviewed diagnosis).
- `author-promptbook` — an ungated plan, for work that needs no enforced gate.
- `run-promptbook` — runs the book; stamps the run's `base_commit` at start.
- `archive-promptbook` — runs the blast-radius containment check as an archive precondition.
- Schema: `${CRUX_PLUGIN_ROOT}/schemas/promptbook.schema.json`; validator `${CRUX_PLUGIN_ROOT}/scripts/validate-promptbook.py`; containment check `${CRUX_PLUGIN_ROOT}/scripts/check-blast-radius.py`. Contract: `docs/AGENTS.md` §11.B and §11.C.
