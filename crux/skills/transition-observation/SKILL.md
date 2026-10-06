---
name: transition-observation
description: "Apply a human disposition to an observation's lifecycle state without changing its claim or body."
disable-model-invocation: true
metadata:
  tags: "observations, state-machine, ratification, human-gate"
  bundles: "crux-docs"
  risk_level: "low"
  triggers: "ratify observation OBS-NNNN | reject observation OBS-NNNN | retire observation OBS-NNNN | mark observation OBS-NNNN decided | transition observation"
---

# Transition Observation

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

The **lifecycle** state machine for observation records — the human gate of the machine-proposes/human-disposes model (see `docs/AGENTS.md` §17.2). `propose-observation` and `transition-decision ratify --as observation` write records as `status: observed`; **nothing but a human disposes of them**, through one of exactly two routes: this skill, the only single-record route, and `survey-signoff`, the only batch route (`docs/AGENTS.md` §17.5). It is the observations-concern sibling of `transition-invariant`: it operates ONLY on the lifecycle frontmatter of an existing record, and it never touches the record's claim or its body.

Core principle: **the state machine is the contract, and nothing but a human sets a state past `observed`.** Every transition that is not explicitly allowed is refused with a clear error.

```
observed ──ratify──▶ ratified ──retire──▶ retired
   │                     │
   │                     └──decide──▶ decided
   └──reject──▶ rejected
```

Allowed transitions, exhaustively (the §17.2 table):

| From | Op | To | Notes |
|------|-----|------|-----|
| observed | ratify | ratified | A human affirms the record describes the code. |
| observed | reject | rejected | A human judges the record wrong or not worth keeping. |
| ratified | retire | retired | The code moved away from what the record describes. |
| ratified | decide | decided | A human authored an ADR for the same rule; `decided_by` names it. |

Everything else is **REFUSED** with an error naming the current status and the requested op. The lifecycle is forward-only: there is no path back into `observed` (that is the on-ramps' entry state only), and no `rejected`/`retired`/`decided` → anything. A revived observation is a new record.

**The immutable claim.** A ratified record's claim never changes; `docs/AGENTS.md` §17.2 names exactly which fields the claim spans, and this skill reads that list there rather than carrying a second copy. When a re-mine finds the same `anchor_id` carrying different text or different evidence, it opens a successor candidate; the human ratifies the successor and retires its predecessor here, or rejects it. Revision is not a state this gate admits.

## When to use

- User says: "ratify observation OBS-NNNN", "reject observation OBS-NNNN", "retire observation OBS-NNNN", "mark observation OBS-NNNN decided", "transition observation OBS-NNNN", "this observation is now an ADR".
- Immediately after a human review of `observed` records produced by `propose-observation` or by `transition-decision ratify --as observation`.
- After a re-mine reports a stale anchor: the code moved, and the human retires the record here. Anchor loss is a signal, never a transition.
- After `propose-adr` records the same rule as a decision: `decide` links the record to the ADR as its provenance trail.

Do **not** use this skill for:
- Creating a record — that is `propose-observation` (reconstructed ramp) or `transition-decision ratify --as observation` (mined ramp).
- Editing a record's claim (the fields `docs/AGENTS.md` §17.2 names), its `id`, its `title`, or its body — refuse. A changed claim is a successor record, never an edit.
- Auto-ratifying on the machine's behalf. `ratified` is reachable ONLY by explicit, per-record human instruction; a batch or automatic "ratify all observed" is refused. A human who wants to dispose of many records in one reading uses `survey-signoff`, the batch sign-off over a sheet they filled in (`docs/AGENTS.md` §17.5) — a separate human gate, never this skill in a loop.
- Transitioning an ADR (`transition-adr`), a brief (`transition-brief`), an invariant pin (`transition-invariant`), or a decision candidate (`transition-decision`).

## Inputs

Subcommand form: `transition-observation <op> <id>` where `<op>` ∈ `ratify | reject | retire | decide` and `<id>` is the record id (e.g. `OBS-0007`), with or without an artifact prefix (§14.3).

- `--decided-by ADR-NNNN` — required for `decide`, refused for every other op. The id of the ADR that records this rule as a decision, with or without an artifact prefix.

## The pipeline

### 0. Resolve per-repo configuration
Run `python3 "${CRUX_PLUGIN_ROOT}/scripts/bionic-config.py"` (compat: `crux-config.py`) from the repo root; use the resolved `docs_dir` wherever this skill says `docs/`, and accept prefixed ids. On exit 1, STOP and surface the error.

### 1. Resolve the record
- Locate the file under `<docs_dir>/observations/` whose frontmatter `id` equals `<id>` (glob the concern dir; match on `id`, not filename guesswork; accept the dual-form spelling `([A-Z][A-Z0-9]{1,9}-)?OBS-(\d{4})`). Zero or multiple matches → STOP with a BROKEN error.
- Read the file. Parse frontmatter. Read the current `status`.
- The frontmatter contract is `docs/AGENTS.md` §17.1; this skill reads the fields it mutates by name from there and restates none of it.

### 2. Validate the transition
Look up `(current_status, op)` in the allowed table. If absent → REFUSE with a message naming the current status and the requested op. Specific refusals:
- `(observed, retire)` → "Only a `ratified` observation can be retired; an `observed` record is rejected, not retired."
- `(observed, decide)` → "Only a `ratified` observation can be decided; ratify it first, or reject it."
- `(ratified, ratify)` / already in destination → "OBS-NNNN is already `${current}`; no-op refused."
- any `rejected`/`retired`/`decided` source → "The observation lifecycle is forward-only out of `observed`/`ratified`; a revived observation is a new record (`propose-observation` or `transition-decision ratify --as observation`)."
- a batch or wildcard id, or an instruction to ratify more than one record → REFUSE. Ratification is a per-record human act. Name the alternative in the refusal: a batch is reviewed on a sheet and signed by `survey-signoff`, never approximated by repeating this gate.

Op-specific preconditions:
- `ratify`: every `evidence` entry must resolve on disk. A ratified record that cites an unresolvable path is BROKEN under `docs/AGENTS.md` §17.3 CHK-OBS-EVIDENCE, so refuse rather than ratify into a defect; name the path.
- `decide`: `--decided-by` is required. Resolve it under `<docs_dir>/adrs/` **and** `<docs_dir>/adrs/archive/` by frontmatter `id` (dual-form spelling accepted). If no file resolves → REFUSE: "`decide` requires an ADR that exists on disk; `${decided_by}` does not resolve." This gate never creates the ADR — that is `propose-adr`.

### 3. Mutate ONLY the lifecycle frontmatter
`${TODAY}` = today's date.
- Set `status: <new_state>`.
- Set `date: ${TODAY}`.
- Set the one `*_date` field that matches the new state to `${TODAY}` (§17.1 names it). Every other date field keeps its value.
- On `decide` only: set `decided_by: ${decided_by}`.
- Those are the ONLY keys this skill writes. **Every other frontmatter key, whatever §17.1 lists, keeps its bytes**, and so does every body byte between the closing `---` and EOF. State the rule this way round — an allow-list of four keys, not a deny-list of the rest — so a field added to §17.1 is immutable here by default rather than by a list somebody remembered to extend.
- Preserve frontmatter key order + formatting outside the mutated keys.

### 4. Successor consistency (audited write)
- On `retire`, check whether a record in `observed` or `ratified` carries the same `anchor_id`. If one does, name it in the hand-off: the retirement makes it the sole live record on that anchor.
- On `ratify`, if another `ratified` record carries the same `anchor_id`, STOP and surface it: two live claims on one anchor is the drift the immutable-claim rule exists to prevent. The human retires the predecessor first, then ratifies the successor.
- The rule these two steps enforce is **CHK-OBS-ANCHOR**, stated in `docs/AGENTS.md` §17.2/§17.3 and checked by `check_observations.py`: `anchor_id` matches `^[0-9a-f]{16}$`, and no two `observed`/`ratified` records share one. This skill applies it at write time; it does not own it, and it carries no second copy of it. The audit finds after the fact what this gate refuses beforehand — the same relationship `transition-invariant` has with its reconciliation check. If the write would leave two live claims on one anchor, STOP.

### 5. Update indexes
- `<docs_dir>/observations/index.md`: update the record's row (new status; `decided_by` where the index carries it). The index **visibly marks** non-`ratified` records (observed/rejected/retired/decided are not hidden — survey debt must be legible).
- `docs/index.md`: the `## Observations (N)` count is unchanged on a transition (the record still exists); update `_Last updated:`.

### 6. Append to `docs/log.md`
```
## [YYYY-MM-DD] observation | OBS-NNNN: <old> → <new>
```
Body: record id, the transition, one line; on `decide`, the `decided_by` id. The `observation` op is a member of the `docs/AGENTS.md` §6 op enum (both the current-writer and historical-reader regexes) — emit it directly.

### 7. Hand off
Confirm the new status + file path. Remind: no scan, scaffold, or re-mine reaches `ratified`. Two human routes reach it and no third does — this skill is the only **single-record** route, and `survey-signoff` is the only **batch** route (`docs/AGENTS.md` §17.5). For `ratify`, note the record's claim is now immutable.

Then tell the user to regenerate both projections, in this order:

```bash
uv run "${CRUX_PLUGIN_ROOT}/scripts/summarize-adrs.py"
uv run "${CRUX_PLUGIN_ROOT}/scripts/compile-doctrine.py"
```

Both projections read the ratified observation records (field contract: `docs/AGENTS.md` §17.1) and hash them, so `ratify`, `retire`, and `decide` each drift `<docs_dir>/adrs/summaries/` and `<docs_dir>/adrs/doctrine/`. The order is fixed because doctrine is compiled from the summaries projection. Skipping a step leaves drift that the matching `--dry-run` gate reports and that CI fails on — both jobs watch the observations concern.

For `retire` or `decide`, note the record's disposition; on `decide`, the summaries projection resolves the record's handle onto the deciding ADR's handles. For `retire`, name any successor candidate awaiting ratification.

## Verification checklist

- [ ] Exactly one record resolved by `id`.
- [ ] The transition was one of the four allowed; any other refused with a message naming the current status and the op.
- [ ] `status` equals the new state; `date` and the one matching `*_date` equal today; on `decide`, `decided_by` names an ADR that resolves on disk.
- [ ] **No other frontmatter key and no body byte changed** — diff the file and confirm the only differing lines are `status`, `date`, the one matching `*_date`, and (on `decide`) `decided_by`.
- [ ] No two live (`observed`/`ratified`) records share an `anchor_id` after the write, or the write was refused.
- [ ] `<docs_dir>/observations/index.md` row updated; the record remains visibly marked if non-`ratified`.
- [ ] `docs/index.md` `## Observations (N)` count unchanged; `_Last updated:` bumped.
- [ ] `docs/log.md` has the transition entry under the `observation` op.
- [ ] No record was ratified without an explicit per-record human instruction (no auto/batch ratify).
- [ ] Any batch request was redirected to `survey-signoff` rather than served here one record at a time.

## Red flags — STOP and reconsider

- About to ratify without explicit human instruction (e.g. "ratify all observed", or ratifying inside a scan, a scaffold, or a cycle run). The whole safety property is that ratification is a human, per-record act. Refuse batch/auto ratify. Redirect a batch request to `survey-signoff`; a loop over this gate is not a batch sign-off and leaves no receipt.
- About to edit a record's `governs`, `evidence`, `anchor_id`, `provenance`, or body "while I'm in here". This skill flips ONLY the lifecycle fields. A changed claim is a successor record.
- About to move a record back to `observed`, or out of `rejected`/`retired`/`decided`. Forward-only; a revived observation is a new record.
- About to `decide` against an ADR id that does not resolve on disk, or to create the ADR yourself. Refuse; `propose-adr` creates ADRs, and `decide` runs after it.
- About to `retire` a record because a re-mine reported a stale anchor, without a human saying so. The signal is the machine's; the transition is the human's.
- About to ratify a record whose `evidence` path no longer resolves. That ratifies into a BROKEN finding; refuse and name the path.
- About to leave two `ratified` records on one `anchor_id`. Retire the predecessor first.

## Rationalization table

| Excuse | Reality |
|--------|---------|
| "The record is obviously right — I'll ratify it as part of the scaffold / the re-mine / the cycle." | Nothing but a human sets a state past `observed`. `ratified` is reachable only by an explicit per-record transition. That is the safety property. |
| "I'll retire this observed record nobody wants." | `observed` records are *rejected*, not retired. Retirement is for a previously-*ratified* record whose code moved. |
| "The re-mine says the anchor is stale, so I'll retire it automatically." | Anchor loss is a signal, never a transition. The human retires through this gate. |
| "While flipping status I'll fix the rule text — it's slightly off." | The claim is immutable. A changed claim is a successor record: ratify the successor, retire the predecessor. |
| "The user wrote the ADR in their head — I'll `decide` now and they'll file it later." | `decide` requires an ADR that resolves on disk. `propose-adr` first; `decide` after. |
| "Twelve records look fine — I'll ratify them in one pass to save the user time." | One record, one instruction, one transition. A batch ratify is refused. The batch route exists and is `survey-signoff`: a human fills in a review sheet and signs it under one receipt. |

## See also

- `propose-observation` — the reconstructed on-ramp that writes `observed` records this skill disposes of.
- `transition-decision` — the mined on-ramp (`ratify --as observation`) and the gate for decision candidates.
- `recover-decisions` — the miner whose re-mine proposes successor candidates and stale-anchor signals, and never touches a record.
- `survey-signoff` — the only batch route past `observed`: one human sign-off over one human-filled review sheet, under one digest-bound receipt.
- `survey-sheet` — the scaffold that writes the review sheet `survey-signoff` signs.
- `transition-invariant` — the invariants state machine this mirrors (frontmatter-only mutation, clear refusals, audited write).
- `propose-adr` — creates the ADR a `decide` transition names.
- `audit-docs` — the CHK-OBS rules (`docs/AGENTS.md` §17.3) that read the status this skill sets.
- `docs/AGENTS.md` §17 — the observations-concern contract (§17.1 frontmatter schema, §17.2 lifecycle, writer boundary, and immutable claim, §17.3 audit rules).
- The observation-record decision this implements (see the ADR log).
