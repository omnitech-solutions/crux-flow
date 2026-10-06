---
name: transition-adr
description: "Accept, deprecate, supersede, or retract an existing ADR. Update status and relationships without changing its narrative body."
arguments: [adr]
metadata:
  tags: "adrs, state-machine, transitions"
  bundles: "crux-docs"
  risk_level: "low"
  triggers: "accept ADR-NNNN | deprecate ADR-NNNN | supersede ADR-NNNN with ADR-MMMM | retract ADR-NNNN"
  routing_note: "Enforces state machine."
---

# Transition ADR

> **Invocation:** a bound `$adr` argument names the ADR number — `/crux:transition-adr 0091` binds `$adr` to `0091`. Read the value from `$adr` where this skill needs the ADR number.

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

The status state machine for Architecture Decision Records. Three named operations — `accept`, `deprecate`, `supersede` — each enforcing a specific allowed transition and the resulting frontmatter mutation. This skill operates ONLY on ADR frontmatter fields (`status`, `date`, the `*_date` fields, `supersedes`, `superseded_by`). The narrative body is frozen the moment an ADR leaves `Proposed`; this skill never touches it.

Core principle: **the state machine is the contract**. Every transition that isn't explicitly allowed is refused with a clear error. Supersession is bidirectional and must be applied atomically — both ADRs update or neither does.

```
Proposed ──accept──▶ Accepted ──deprecate──▶ Deprecated
   │                   │
   │                   └──supersede(new ADR)──▶ Superseded
   │
   └──deprecate──▶ Deprecated   (allowed; logs an extra `lint` op)
```

Allowed transitions, exhaustively:

| From | Op | To | Notes |
|------|-----|------|-----|
| Proposed | accept | Accepted | Normal review pass. |
| Proposed | deprecate | Deprecated | Abandoned proposal. Extra `lint` log entry. |
| Accepted | deprecate | Deprecated | Decision retracted; no replacement. |
| Accepted | supersede(target) | Superseded | Replacement ADR required; bidirectional. |

Everything else is **REFUSED** with an error message naming the disallowed transition and the closest legal one.

## When to use

- User says: "accept ADR-NNNN", "deprecate ADR-NNNN", "supersede ADR-NNNN with ADR-MMMM", "retract ADR-NNNN", "mark ADR-NNNN superseded".
- Immediately after `propose-adr --accept-immediately` (chained).

Do **not** use this skill for:
- Creating a new ADR (that's `propose-adr`).
- Editing an ADR's body — refuse, even if asked. Body is frozen post-Proposed; corrections require a new ADR that supersedes.
- "Un-accepting" or "un-superseding" an ADR. The state machine is forward-only; mistakes get a follow-up ADR.
- Bulk transitions (e.g., "deprecate all ADRs older than 2 years"). Each is a separate audited operation.
- A user-typed `--repair`. `--repair` is **audit-only** — it exists solely for `audit-docs` to fix detected supersession asymmetries, and it bypasses some state-machine validation to do so. Do NOT surface it to users or run it on a user's request; if a user reports an asymmetry, route them to `audit-docs` (which invokes `--repair` itself).

## Inputs

Subcommand form: `transition-adr <op> <target> [<replacement>]` where:

- `<op>`: one of `accept | deprecate | supersede`.
- `<target>`: the ADR id being transitioned (e.g., `ADR-NNNN`).
- `<replacement>`: required only for `supersede`; the new ADR id (e.g., `ADR-MMMM`).

Flags:
- `--repair` — used only by `audit-docs` to fix detected supersession asymmetries; ignores some state-machine validations to restore consistency. Logs a `lint` op.

## The pipeline

Execute in order. Read both ADRs before any write. Either both writes succeed (for supersede) or neither does.

### 1. Resolve the target ADR

- Locate `docs/adrs/${target}-*.md`. If zero or more than one match, **STOP** with a BROKEN error.
- Read the file. Parse the frontmatter.
- Read the current `status:` field.

### 2. Validate the transition

Look up `(current_status, op)` in the allowed-transitions table above. If the pair is not present:

- For `(Proposed, supersede)` → **REFUSE** with the error: "Proposed ADRs cannot be superseded. Use `deprecate` to abandon a proposal. Supersession applies only to Accepted decisions."
- For any other illegal pair → **REFUSE** with a clear message naming the current status and the requested op.

If the target ADR is already in the destination state (e.g., already `Accepted` when `accept` was requested), refuse with "ADR-${target} is already ${current_status}; no-op refused." Do not write anything.

### 3. For `supersede` only — resolve and validate the replacement

- Locate `docs/adrs/${replacement}-*.md`. If zero/many matches → BROKEN; STOP.
- Read its frontmatter.
- Validate:
  - `${replacement}.status` MUST be `Accepted`. (Supersession by a `Proposed` ADR is a process violation — the replacement must itself have passed review.) If it is not Accepted, **REFUSE** with the exact error (parallel to step 2's refusal): "ADR-${replacement} is ${replacement_status}, not Accepted; a superseding ADR must itself be Accepted first. Accept ADR-${replacement} (a separate `transition-adr accept` op), then re-run the supersede."
  - `${replacement}` is not already in `${target}.supersedes` (would be a duplicate). If it is, refuse.
  - `${replacement}` ≠ `${target}` (no self-supersession). Refuse with a sarcastic-but-firm error.

### 4. Compute the frontmatter mutations

`${TODAY}` = today's date.

**`accept`** (Proposed → Accepted):
- `status: Accepted`
- `accepted_date: ${TODAY}`
- `date: ${TODAY}`

**`deprecate`** (Proposed → Deprecated OR Accepted → Deprecated):
- `status: Deprecated`
- `deprecated_date: ${TODAY}`
- `date: ${TODAY}`

**`supersede`** (Accepted → Superseded), on `${target}`:
- `status: Superseded`
- `superseded_date: ${TODAY}`
- `superseded_by: ${replacement}`
- `date: ${TODAY}`

**`supersede`**, on `${replacement}`:
- `supersedes:` list — append `${target}` (preserving any existing entries; preserve insertion order; do not sort).
- All other fields on `${replacement}` are unchanged. **Do not touch its body, status, or date.**

### 5. Apply the writes atomically

For non-supersede ops: write the single ADR file with mutated frontmatter; leave the body untouched (literally — read the file, mutate only the frontmatter block between the `---` fences, write back).

For `supersede`: write both files. If the second write fails, attempt to roll back the first. If rollback fails, **STOP** and surface a BROKEN audit state to the user — manual reconciliation is required. (`audit-docs` will detect the asymmetry; running `transition-adr supersede --repair` resolves it.)

In all cases:
- Preserve frontmatter key order, comments, trailing-newline conventions.
- Never reformat the body. Read it as bytes between the closing `---` and EOF; rewrite identically.

### 6. Update `docs/adrs/index.md`

For the target ADR's row:
- Update `status` column to the new status.
- Update `date` column to `${TODAY}`.
- For `supersede`: update `superseded_by` column to `${replacement}`.

For the replacement ADR's row (supersede only):
- Update `supersedes` column: append `${target}` to the existing comma-separated list (or replace `—` if empty).

Update the `_Last updated:_` line to `${TODAY}`.

### 7. Append to `docs/log.md`

Newest-first, one entry per operation:

```markdown
## [${TODAY}] adr | ${target}: ${op}${suffix}

${target_title}. ${op_specifics}.
```

Where `${suffix}` is:
- ` (accepted)` for accept.
- ` (deprecated)` for deprecate.
- ` (superseded by ${replacement})` for supersede.

For `Proposed → Deprecated`, write a SECOND entry, immediately above the `adr` entry:

```markdown
## [${TODAY}] lint | ${target}: deprecated while still Proposed

ADR was abandoned before acceptance. No replacement.
```

The `lint` entry surfaces the unusual transition for future audits — proposed-then-deprecated is allowed but worth flagging.

For `--repair` runs (called by `audit-docs`), write a single `lint` entry instead of the `adr` entry:

```markdown
## [${TODAY}] lint | ${target}: supersession asymmetry repaired

Restored ${target}.superseded_by ↔ ${replacement}.supersedes consistency.
```

### 8. Update `docs/index.md`

- The ADR section count stays the same (the ADR exists; only its status changed) — but verify the count is still accurate.
- Update the `_Last updated:_` line.

### 9. Regenerate the summaries projection (when the ADR carries a `governs` block)

If the transitioned ADR carries a `governs` block, the transition changed what the summaries projection reads: an `accept` edits the frontmatter the projection hashes, and a `supersede`/`deprecate` moves the ADR into `adrs/archive/`, dropping its governs entries out of the active set. Regenerate the projection so the working tree stays fresh:

```
uv run "${CRUX_PLUGIN_ROOT}/scripts/summarize-adrs.py" --repo-root <repo-root>
```

This rewrites `<docs_dir>/adrs/summaries/` from every active (top-level) ADR's governs blocks, keeping the `summarize-adrs.py --dry-run` drift gate (and CI) green. Skip only when the ADR has no `governs` block.

### 10. Hand off to the user

- Confirm the new status and the file path.
- For supersede, name both ADRs and the established back-pointer.
- Remind: the body of the transitioned ADR is now frozen (or remained frozen).
- Optionally call `log-work --silent --category decision --subject "ADR-${target} ${op}${suffix}"` for user-meaningful transitions (acceptances and supersessions; deprecations of Proposed are usually too minor to journal).

## Verification checklist

- [ ] Target ADR frontmatter `status:` equals the new state.
- [ ] Target ADR `date:` equals `${TODAY}`.
- [ ] Target ADR's status-specific date field is `${TODAY}` (one of `accepted_date`, `deprecated_date`, `superseded_date`).
- [ ] Other date fields on the target ADR are unchanged (still `null` or their prior value).
- [ ] Target ADR body bytes are unchanged (byte-identical to pre-write).
- [ ] For supersede: replacement ADR's `supersedes:` list includes `${target}` exactly once.
- [ ] For supersede: target ADR's `superseded_by:` equals `${replacement}` (a single id, not a list).
- [ ] For supersede: replacement ADR's body bytes are unchanged.
- [ ] `docs/adrs/index.md` row(s) for the affected ADR(s) reflect the new status, date, and supersede fields.
- [ ] `docs/adrs/index.md` `_Last updated:_` equals `${TODAY}`.
- [ ] `docs/log.md` has the appropriate `adr` entry (and `lint` entry for Proposed→Deprecated or `--repair`).
- [ ] `docs/index.md` ADR section count matches the actual file count and `_Last updated:_` is `${TODAY}`.

## Red flags — STOP and reconsider

- About to write a transition that isn't in the allowed table. REFUSE with the error message.
- About to mutate any field on the target ADR other than `status`, `date`, the relevant `*_date`, and (for supersede) `superseded_by`. Touch nothing else.
- About to mutate any field on the replacement ADR other than `supersedes`. Touch nothing else.
- About to mutate the body of either ADR. The body is frozen — refuse, even with `--repair`.
- About to update one side of a supersede without the other. Atomic both-or-neither.
- About to allow a `Proposed` ADR to be the replacement in a supersession. Refuse — the replacement must be Accepted.
- About to allow self-supersession (`supersede ADR-NNNN with ADR-NNNN`, same id both sides). Refuse.
- About to "no-op" silently when the ADR is already in the destination state. Refuse with a clear message — the user thought a transition was needed; tell them it wasn't.
- About to rewrite the file with reformatted YAML (different key order, different quote style). Preserve the existing format byte-for-byte except the keys you're explicitly mutating.
- About to skip the `lint` op entry for a Proposed→Deprecated. Write both entries.

## Rationalization table

| Excuse | Reality |
|--------|---------|
| "The user wants to supersede a Proposed ADR with a new one — close enough." | The state machine forbids it for a reason: supersession implies replacing a decision that was *made*. A proposed-but-not-accepted ADR is not a made decision. Use `deprecate` on the old, `propose` the new. |
| "The replacement is Proposed but will be Accepted in a moment — I'll do them in one go." | Accept the replacement first (separate op), then supersede. Two operations, two log entries, clear chronology. |
| "I'll just rewrite the whole file — reformatting doesn't change semantics." | Diff noise destroys git-blame value and breaks downstream parsers that fingerprint the frontmatter block. Preserve byte layout outside the mutated keys. |
| "The supersession write to the second ADR failed — I'll log the asymmetry and move on." | An asymmetric supersession is a BROKEN audit-chain violation. Roll back the first write, or stop and surface to the user; never silently leave drift. |
| "The user said 'accept all pending ADRs' — I'll loop." | Each transition is a separate invocation, each requires its own decision check. Refuse bulk operations. |
| "The body is in the way — let me strip the stub `<>` placeholders." | The body is frozen. Stubs are the user's TODO. Leave them; the user's next edit will replace them — but only while the ADR is still `Proposed`. After transition, even stub edits are forbidden. |
| "The target ADR is already Accepted; I'll just confirm and exit silently." | Silent no-op is indistinguishable from success. Refuse explicitly so the user notices their model is wrong. |
| "Let me update `supersedes` on the replacement ADR by sorting the list alphabetically." | Insertion order is the chronological supersession order. Sorting destroys that. Append only. |

## Common mistakes

- **Forgetting to update `date:` along with the status-specific date**: `date:` is the "current status entry's date" and moves on every transition. The status-specific field (e.g., `accepted_date`) is immutable after first set.
- **Setting `superseded_by:` to a list**: it's a single id (or `null`), not a list. The plurality is on `supersedes` (the replacement's field).
- **Updating only the index, not the file**: the file's frontmatter is source of truth; the index mirrors. Always write the file first.
- **Confusing `--repair` with normal supersede**: `--repair` is invoked by `audit-docs` to fix detected asymmetries; it doesn't go through the state-machine validation. Don't expose it to user-typed invocations.
- **Forgetting the Proposed→Deprecated extra `lint` log**: the dual entry (`adr` + `lint`) is the chronological signal that an abandoned proposal occurred. Both required.
- **Reading the ADR file with a YAML library that re-emits keys in a different order**: parse-then-mutate-then-byte-merge, or use a structured editor that preserves layout. Don't round-trip through a lossy YAML emitter.
- **Updating the `docs/index.md` ADR count on a transition**: the file count didn't change. Don't bump.
- **Trying to journal every transition automatically**: only the user-meaningful ones (accept, supersede). Deprecate-of-Proposed is too minor; let the user invoke `log-work` if they want.

## ADR archival cold tier (SP-4)

On a transition to **Superseded** or **Deprecated**, additionally move the ADR file into `<docs_dir>/adrs/archive/`: **preflight the destination** (refuse fail-closed on a basename collision, before any mutation), then **write the frontmatter, then git-move** the file (basename preserved; not a body edit — immutability is by content). The two file steps are not atomic; a crash between them leaves a terminal-status ADR in the active dir that `audit-docs` `CHK-ADR-ARCHIVE` auto-repairs by completing the move. Accepted/Proposed ADRs stay in `adrs/`. Do **not** hand-edit `adrs/index.md` — its active table + `## Archived` roster are regenerated by `scripts/generate-adr-index.py` (both tiers). Wiki-links to ADRs resolve across both tiers (via the lifecycle-agnostic resolver).
