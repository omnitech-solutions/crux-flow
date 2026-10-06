---
name: transition-invariant
description: "Ratify, reject, or retire an invariant on human direction. Update lifecycle metadata without changing the invariant's body."
disable-model-invocation: true
metadata:
  tags: "invariants, state-machine, ratification, human-gate"
  bundles: "crux-docs"
  risk_level: "low"
  triggers: "ratify invariant INV-NNNN | reject invariant INV-NNNN | retire invariant INV-NNNN | transition invariant"
---

# Transition Invariant

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

The **ratification** state machine for invariant pins — the human gate of the machine-proposes/human-disposes model (see `docs/AGENTS.md` §15.3). `recover-invariants` proposes pins as `ratification: observed`; **only this skill, driven by a human, disposes of them**. It is the invariants-concern sibling of `transition-adr`: it operates ONLY on the `ratification` frontmatter of an existing ledger page (and the reconciliation mapping when a check binding changes), and it never touches a pin's identity, `class`, `provenance`, or the *why* body.

Core principle: **the state machine is the contract, and recovery cannot self-ratify.** Every transition that isn't explicitly allowed is refused with a clear error.

```
observed ──ratify──▶ ratified ──retire──▶ retired
   │
   └──reject──▶ rejected
```

Allowed transitions, exhaustively:

| From | Op | To | Notes |
|------|-----|------|-----|
| observed | ratify | ratified | A human affirms this is a real intended invariant. |
| observed | reject | rejected | A human judges the recovered candidate is not an intended invariant. |
| ratified | retire | retired | Explicit retirement — a pin you silently stop enforcing "becomes a lie in the suite." |

Everything else is **REFUSED** with an error naming the current ratification and the requested op. There is no path back into `observed` (that is `recover-invariants`' entry state only), and no `rejected`/`retired` → anything.

## When to use

- User says: "ratify invariant INV-NNNN", "reject invariant INV-NNNN", "retire invariant INV-NNNN", "transition invariant INV-NNNN", "accept this recovered invariant".
- Immediately after a human review of `observed` pins produced by `recover-invariants`.

Do **not** use this skill for:
- Creating a pin or emitting candidate checks — that's `recover-invariants` (machine).
- Editing a pin's `class`, `provenance`, identity, or the *why* body — refuse. Recovery + human authoring own those; this skill flips ratification only.
- Auto-ratifying on the machine's behalf. `ratified` is reachable ONLY by explicit human instruction; a batch/auto "ratify all observed" is refused.
- Transitioning an ADR (that's `transition-adr`) or a brief (that's `transition-brief`).

## Inputs

Subcommand form: `transition-invariant <op> <id>` where `<op>` ∈ `ratify | reject | retire` and `<id>` is the pin id (e.g. `INV-0007`), with or without an artifact prefix (§14.3).

## The pipeline

### 0. Resolve per-repo configuration
Run `python3 "${CRUX_PLUGIN_ROOT}/scripts/bionic-config.py"` (compat: `crux-config.py`) from the repo root; use the resolved `docs_dir` wherever this skill says `docs/`, and accept prefixed ids. On exit 1, STOP and surface the error.

### 1. Resolve the pin
- Locate the ledger page `<docs_dir>/invariants/<slug>.md` whose frontmatter `id` equals `<id>` (glob the concern dir; match on `id`, not filename guesswork). Zero or multiple matches → STOP with a BROKEN error.
- Read the page. Parse frontmatter. Read the current `ratification`.

### 2. Validate the transition
Look up `(current_ratification, op)` in the allowed table. If absent → REFUSE with a clear message. Specific refusals:
- `(observed, retire)` → "Only a `ratified` invariant can be retired; an `observed` candidate is rejected, not retired."
- `(ratified, ratify)` / already in destination → "INV-NNNN is already `${current}`; no-op refused."
- any `rejected`/`retired` source → "The ratification lifecycle is forward-only out of `observed`/`ratified`; a revived invariant is a new pin (`recover-invariants` or human authoring)."

### 3. Mutate ONLY the ratification frontmatter
`${TODAY}` = today's date.
- Set `ratification: <new_state>` on the ledger page.
- Do NOT touch `id`, `class`, `provenance`, `verification`, `related_adrs`, `related_briefs`, `checks`, or the body. Read the body bytes between the closing `---` and EOF and rewrite them identically.
- Preserve frontmatter key order + formatting outside the one mutated key.

### 4. Reconciliation consistency (audited write)
- The reconciliation manifest (`docs/AGENTS.md` §15.4) maps checks → pins and holds `last_result`. This skill does not change `last_result`, but on `reject`/`retire` it MUST leave the reconciliation consistent: a `rejected`/`retired` pin's checks are no longer durable-trusted. Do not delete checks (recovery/human owns the suite), but ensure `audit-docs` will not read a `rejected`/`retired` pin as a live ratified one — the ledger `ratification` is the source of truth (§15.1), so the frontmatter change in §3 is sufficient; verify no reconciliation entry contradicts it (a check still mapped to the pin is fine; the pin's ratification governs the audit rules).
- This is the bidirectional/audited half mirroring `transition-adr`'s supersession write: ledger + reconciliation must not disagree. If they would, STOP and surface (never leave drift).

### 5. Update indexes
- `<docs_dir>/invariants/index.md`: update the pin's row (new ratification). The index **visibly marks** non-`ratified` pins (observed/rejected/retired are not hidden — survey-debt must be legible).
- `docs/index.md`: the `## Invariants (N)` count is unchanged on a transition (the pin still exists); update `_Last updated:`.

### 6. Append to `docs/log.md`
```
## [YYYY-MM-DD] invariant | INV-NNNN: <old> → <new>
```
Body: pin id, class, the transition, one line. The `invariant` op is a first-class member of the `docs/AGENTS.md` §6 op enum (both the current-writer and historical-reader regexes) — emit it directly.

### 7. Hand off
Confirm the new ratification + file path. Remind: recovery cannot reach `ratified` — this human act is the only path. For `ratify`, note the pin now participates in CHK-INV-DECORATION/FAILING (it must have a resolvable, passing check or it becomes a detectable defect). For `retire`, note the pin is explicitly no longer enforced.

## Verification checklist

- [ ] Exactly one pin resolved by `id`.
- [ ] The transition was one of the three allowed; any other refused with a clear message.
- [ ] Ledger `ratification` equals the new state; **no other frontmatter field or body byte changed**.
- [ ] Reconciliation and ledger do not disagree after the write.
- [ ] `<docs_dir>/invariants/index.md` row updated; the pin remains visibly marked if non-`ratified`.
- [ ] `docs/index.md` `## Invariants (N)` count unchanged; `_Last updated:` bumped.
- [ ] `docs/log.md` has the transition entry.
- [ ] No pin was ratified without an explicit human instruction (no auto/batch ratify).

## Red flags — STOP and reconsider

- About to ratify without explicit human instruction (e.g. "ratify all observed"). The whole safety invariant is that ratification is a human, per-pin act. Refuse batch/auto ratify.
- About to edit a pin's `class`, `provenance`, `checks`, or *why* body. This skill flips ONLY `ratification`.
- About to move a pin back to `observed`, or out of `rejected`/`retired`. Forward-only; a revived invariant is a new pin.
- About to leave the reconciliation disagreeing with the ledger. Ledger wins; keep them consistent or STOP.
- About to delete checks from `<docs_dir>/invariants/checks/` on a reject/retire. Not this skill's job — the ledger ratification governs the audit; the suite is owned by recovery/human authoring.

## Rationalization table

| Excuse | Reality |
|--------|---------|
| "The candidate is obviously a real invariant — I'll ratify it as part of recovery." | Recovery NEVER ratifies. `ratified` is reachable only by an explicit human transition. That is the safety invariant. |
| "I'll retire this observed pin nobody wants." | `observed` candidates are *rejected*, not retired. Retirement is for a previously-*ratified* pin you're explicitly ceasing to enforce. |
| "While flipping ratification I'll fix the pin's class." | This skill mutates only `ratification`. Class/provenance/body corrections are recovery/human-authoring's job. |
| "The reconciliation disagrees but the ledger looks right — I'll ship it." | Ledger wins, but you must not LEAVE drift. Reconcile or STOP; an audited write is both-or-neither. |

## See also

- `recover-invariants` — the machine that proposes `observed` pins this skill disposes of.
- `transition-adr` — the ADR state machine this mirrors (frontmatter-only mutation, clear refusals, audited write).
- `audit-docs` — the CHK-INV rules (`docs/AGENTS.md` §15.5) that read the ratification this skill sets.
- `docs/AGENTS.md` §15 — the invariants-concern contract (ledger schema, axes, reconciliation, audit rules).
