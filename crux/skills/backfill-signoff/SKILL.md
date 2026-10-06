---
name: backfill-signoff
description: "Obtain owner approval for a governs backfill batch, validate its receipts, and record sign-off. Refuse drifted or failing batches."
disable-model-invocation: true
metadata:
  tags: "backfill, sign-off, receipts, human-gate"
  bundles: "crux-docs"
  risk_level: "low"
  triggers: "sign off backfill batch <id> | backfill sign-off | sign the backfill"
  routing_note: "The single owner write path for a governs backfill batch: verbatim rendering of every enumerated receipt, fail-closed validation, then the four surfaces (reviews `signed` flip, `adr.governs_backfilled` ledger append, `backfill` log op, journal hook) plus the completion marker when the cohort arithmetic holds. Idempotent; absence-conditional completion after a mid-write crash."
---

# Backfill Sign-off

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

The **owner sign-off** for one governs backfill batch — the owner half of the two-layer review gate. The reviewer-agent has already recorded a per-block verdict in `<docs_dir>/adrs/summaries/backfill-reviews.yml`; **only this skill, driven by the owner, turns an unsigned batch into a signed one**. It is the backfill machinery's sibling of `transition-invariant`: one human gate, an audited write, and a refusal for every state it does not recognize.

Core principle: **the sign-off approves enumerated spans, never a count.** The script renders each receipt's rule verbatim from live frontmatter and each anchor span in the entry's own shape; the owner reads that rendering; only then does the write happen. The single-write-path claim is enforced, not conventional: receipts written any other way fail the receipts↔log↔journal cross-validation (docs/AGENTS.md §6) for want of their corroborating surfaces.

One invocation signs **one** batch. The write set, all under one threaded date:

1. the batch's `signed` date flips in the reviews manifest (receipt history is append-only, never touched);
2. the admitted handles append to `adr.governs_backfilled` in `<docs_dir>/manifest.yml` (append-only per handle; a tombstoned handle is retained);
3. the `backfill` log op prepends to `<docs_dir>/log.md` (per docs/AGENTS.md §6);
4. the journal hook appends to `<docs_dir>/journal/<YYYY-MM>.md` (category `review`, subject `backfill sign-off <batch-id>`);
5. `adr.governs_backfill_complete: true` is set **iff** the simulated post-sign arithmetic has zero pending cohort ADRs — absent otherwise, never false.

**Excluded from run-execution autonomy.** This is a human-gate skill: the owner reading the enumerated spans (step 3) IS the review the two-layer gate provides. Like `escalate-arch-runtime`, a running promptbook or dev-cycle must NOT invoke it unattended. Its writes are in-repo edits, which `docs/AGENTS.md` §11 otherwise treats as pre-authorized inside a started run — but that default does not reach here, because the authorization this skill needs is a human reading a rendering, and no run plan can pre-supply that. A run that reaches a "sign off backfill batch" step STOPS and hands the batch to the owner; it is never auto-approved or allowlisted for auto-approval. The gate is prose, not a flag — the same trust model as `transition-invariant`; a broader §11 amendment naming every human-gate skill as a stop point is a follow-on.

## When to use

- The owner says: "sign off backfill batch <id>", "backfill sign-off", "sign the backfill".
- A reviewer-agent has recorded verdicts for a batch and the batch sits unsigned in the reviews manifest.
- A batch signed but half-written (a crash interrupted the write set): re-run the same command; completion is absence-conditional.

Do **not** use this skill for:
- Authoring or editing receipts, the ledger, or the completion marker by hand — refuse. This script is the only write path.
- Reviewing the extraction itself — that is the reviewer-agent layer, recorded as per-block verdicts before sign-off.
- Transitioning an invariant pin (that is `transition-invariant`) or an ADR (that is `transition-adr`).

## Inputs

The phrase forms above are the **skill trigger** — what the owner says to invoke this skill. The skill then runs the script directly; there is no subcommand. The invocation is verbatim:

```
crux/scripts/signoff-backfill.py --batch <id> [--date YYYY-MM-DD] [--dry-run] [--repo-root DIR]
```

Under the plugin root that is `uv run "${CRUX_PLUGIN_ROOT}/scripts/signoff-backfill.py" --batch <id> [--date YYYY-MM-DD] [--dry-run] [--repo-root DIR]`. The batch id names an existing batch in the reviews manifest — unsigned for a fresh sign, or already-signed for an idempotent completion re-run (the crash-recovery path). `--date` defaults to today on a fresh sign; on an already-signed batch it must equal the recorded sign-off date.

## The pipeline

### 0. Resolve per-repo configuration
Run `python3 "${CRUX_PLUGIN_ROOT}/scripts/bionic-config.py"` from the repo root; use the resolved `docs_dir` wherever this skill says `docs/`. On exit 1, STOP and surface the error.

### 1. Pre-flight read
Read the batch in `<docs_dir>/adrs/summaries/backfill-reviews.yml`: it exists, carries receipts and possibly no-rule entries, and is **unsigned**. Confirm the reviewer-agent layer actually ran — the sign-off ratifies those verdicts, and signing a batch whose verdicts nobody produced is gate-washing.

### 2. Dry-run render
```
uv run "${CRUX_PLUGIN_ROOT}/scripts/signoff-backfill.py" --batch <id> --dry-run
```
Read the rendering: every enumerated receipt with its rule verbatim, its anchor spans (a list anchor renders as numbered, separately-quoted spans), recorded digest beside live digest; every no-rule ADR with its reason; the simulated marker arithmetic. The render is also the standing spot-audit surface — any signed handle can be re-displayed this way at any time.

### 3. OWNER READS THE ENUMERATED SPANS
For each receipt, the cited span must support its rule in the ADR's frozen body. If one does not, STOP: the entry routes back through the review gate — a `fail` verdict, or a corrected extraction in a new batch — and this batch is not signed as it stands. This reading IS the human review the two-layer gate exists to provide; there is no shortcut.

### 4. Sign
```
uv run "${CRUX_PLUGIN_ROOT}/scripts/signoff-backfill.py" --batch <id> [--date YYYY-MM-DD]
```
The script validates fail-closed (batch exists and is unsigned; recorded digest equals live digest — drift-since-review voids a verdict; the recorded anchor space-fold-equals the live one; the admission legs hold; no-rule ADRs are cohort members carrying no live governs entries; the live tree's backfill contract is otherwise clean), writes the four surfaces plus the marker when the arithmetic holds, then runs the coverage gate and re-derives the summaries projection inside the write set.

### 5. Post-gates
The coverage gate must exit 0 and the summaries drift gate must report clean. Both run inside the write; a red post-gate after a landed write set is reported loudly — remediate before the next batch, and re-run the same command to confirm.

### 6. Hand off
Report the batch id, the signed date, the derived ADR ids, and the marker state (set, or absent with the pending count).

## Batch discipline

- One sign-off per batch. Initial batches hold disjoint scopes, and every cohort ADR lands in exactly one initial batch with **all** of its entries.
- A correction — an overlooked rule admitted late, a content rewrite, a removal — is its own mini-batch with its own sign-off through this same skill. New entry, new digest, new receipt, owner sign-off.
- The initial plan's topic batches are informative, not a cap; the discipline binds, the topics advise.
- A removal retains the ledger row and signs a tombstone carrying the last live digest. A removal that would leave a completed cohort pending is refused — carry the replacement extraction (or the no-rule receipt) in the same correction batch.
- Spot-audit is always available: the `--dry-run` render of any batch re-displays every handle's rule and anchor.

## Verification checklist

- [ ] The batch was unsigned in `<docs_dir>/adrs/summaries/backfill-reviews.yml` before step 4 (or the re-run was an idempotent completion).
- [ ] The dry-run rendering was produced **in this session** and the owner read every enumerated span.
- [ ] Validation reported clean before any write: no drift-since-review, no anchor mismatch, no laundering leg, no red tree.
- [ ] The four surfaces carry one threaded date: the batch's `signed`, the log heading, the journal hook date.
- [ ] The log entry's `ADRs:` line and the journal hook name exactly the batch's derived ADR ids.
- [ ] The completion marker was set only when the simulated arithmetic had zero pending — absent otherwise, never false.
- [ ] The coverage gate exits 0 and the summaries drift gate is clean after the write.
- [ ] A re-run of the same command reports a no-op.

## Red flags — STOP and reconsider

- About to sign without a dry-run rendering **in this session**. The enumerated spans are the attestation; signing unread is gate-washing.
- About to sign onto a red tree. The script refuses; do not work around the refusal by hand-editing a surface — remediate the red first.
- About to hand-edit a receipt, the `adr.governs_backfilled` ledger, or the completion marker outside this skill. Foreign receipts fail cross-validation for want of their log and journal corroboration; the hand-edit buys a red tree, not a shortcut.
- About to re-run a signed batch with a different `--date`. The date threads all four surfaces; the refusal to move it is the mechanism working.
- About to sign a removal that leaves a completed cohort pending. The terminal marker refuses; the correction batch must carry the replacement.
- About to sign several batches in one invocation, or to treat the initial plan's topic count as a cap. One sign-off per batch; correction mini-batches sign through the same path.

## Rationalization table

| Excuse | Reality |
|--------|---------|
| "The reviewer-agent already checked the spans — the owner rendering is ceremony." | The rendering IS the human review layer. Without the owner reading the enumerated spans, the gate has one layer, not two. |
| "The tree is red for an unrelated reason; this batch is fine — sign it." | Signing onto a red tree hides the batch's own contribution to the failure. Clean the tree first. |
| "I'll fix the receipt text by hand and save a batch." | Hand-written receipts lack the log and journal corroboration and fail cross-validation. A correction is a new receipt in a new batch through this skill. |
| "The marker is close enough — set it and let the last ADR catch up." | The marker is terminal and binary; set early, it lies on every run. Zero pending is the only threshold. |
| "Re-running after the crash will double-write the log." | Every write is absence-conditional: a present, consistent surface is skipped; only the missing ones are written. |

## See also

- `<docs_dir>/adrs/summaries/backfill-reviews.yml` — the reviews manifest; its header documents the receipt and batch schema.
- `docs/AGENTS.md` §6 — the `backfill` log op's grammar; §7 — the three `adr.governs_backfill*` manifest keys; §11.A — the `anchor` sub-field contract.
- The governs coverage gate (`scripts/check-governs-coverage.py`) — the contract the post-gates run.
- `transition-invariant` — the human-gate skill whose shape this one mirrors.
