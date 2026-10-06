---
name: survey-signoff
description: "Publish one completed observation review batch after explicit human confirmation. Never author verdicts."
disable-model-invocation: true
metadata:
  tags: "observations, ratification, survey, receipts, human-gate"
  bundles: "crux-docs"
  risk_level: "medium"
  triggers: "sign off the survey | sign the survey sheet | sign off SVY-NNNN | publish the batch | batch-ratify the observations | I have filled in the review sheet | finish the observation batch"
  routing_note: "The single human write path that signs one filled-in SVY-NNNN review sheet and publishes its records under one digest-bound receipt. The only batch route past `observed`; `transition-observation` remains the only single-record route. Human gate — excluded from run-execution autonomy; carries `disable-model-invocation`. Never authors a verdict; never signs two batches at once."
---

# Survey Sign-off

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

The **human sign-off** that publishes one batch of observation records. The user has filled in a review sheet scaffolded by `survey-sheet`; this skill signs it, driven by `scripts/signoff-survey.py`. It is the observations concern's sibling of `backfill-signoff` and `reconcile-signoff`: one human gate, an audited write, and a refusal for every state it does not recognize.

**The protocol's source of truth is `docs/AGENTS.md` §17.5** — the sheet and receipt schemas, the layout, the batch-identity rule, and the state table the script implements cell for cell. Read it there; this skill restates none of it and never carries a second copy.

**This sign-off is equivalent to N individual ratifications under one signature.** The human reads N claims and states N verdicts on the sheet; the signature covers all of them. `transition-observation` remains the only single-record route, and this skill is the only batch route.

**The interpretive judgment is the human's, not the model's.** The verdict, the domain override and the rationale are authored on the sheet by the user before this skill runs. This skill renders what the sheet says and refuses what it cannot verify; it never writes a cell and never infers a verdict for an empty one.

**Re-entrant by construction — there is no resume flag and no phase argument.** Every run derives the batch's state from disk and applies the one write that state names, repeating until the batch is published. Re-running on an unchanged sheet finishes an interrupted publish and is otherwise a reported no-op. That is why an interrupted sign-off is repaired by running it again rather than by hand.

**Excluded from run-execution autonomy — a human gate.** Like `backfill-signoff` and `reconcile-signoff`, a running promptbook or dev-cycle must NOT invoke this skill unattended. Its writes are reversible in-repo edits, which a started run otherwise treats as pre-authorized — but that default does not reach here, because the authorization this skill needs is a human reading N claims and stating N verdicts, and no run plan can pre-supply that. A run that reaches a batch sign-off step STOPS and hands the sheet to the user. This skill also carries `disable-model-invocation: true`, so the model never auto-selects it; the user invokes it by name.

## When to use

- The user says: "sign off the survey", "sign the survey sheet", "sign off SVY-NNNN", "publish the batch", "I have filled in the review sheet", "finish the observation batch".
- A sign-off was interrupted and the batch is unfinished. Re-run this skill: the resume is total, because the staged bytes remain and the receipt carries every planned id and path.
- The audit reports a stub receipt or a stale index after a partial publish. The remedy is re-running the sign-off, never a hand-repair of any surface.

Do **not** use this skill for:
- Authoring or amending any cell of the sheet. If a verdict is missing, hand the sheet back to the user; the script refuses an empty verdict and so must you.
- Signing a sheet the user has not read in this session. The reading IS the gate.
- Disposing of a single record — that is `transition-observation`.
- Scaffolding a sheet — that is `survey-sheet`.
- Hand-writing a record, a receipt, or an index row that the sign-off would have written. Refuse; a foreign write contradicts the receipt and the script then refuses the whole batch rather than overwriting it.

## Prerequisites

1. The tree exists, `<docs_dir>/manifest.yml` is present, and `observations` is in `concerns_enabled`.
2. A sheet exists — either the live `<docs_dir>/observations/survey-SVY-NNNN.yml`, or an already-bound batch mid-publish.
3. **Every row carries a verdict.** The three human cells were authored by the user, not by any model.
4. The sheet has not been edited since it was signed. A signed sheet is immutable; the script re-checks the digest on every run and refuses on a mismatch.

## The pipeline

### 0. Resolve per-repo configuration (.bionic.yml)

Run `python3 "${CRUX_PLUGIN_ROOT}/scripts/bionic-config.py"` from the repo root. On exit 1, STOP and surface the `{"error": ...}` payload. Use the returned `docs_dir` wherever this skill says `<docs_dir>` (default `bionic`).

### 1. Dry-run render — show every claim and its verdict

```bash
uv run "${CRUX_PLUGIN_ROOT}/scripts/signoff-survey.py" --repo-root <repo-root> [--batch SVY-NNNN] --dry-run
```

`--dry-run` writes nothing and reports the batch's current state, every planned record id and path, and every derived retirement. Omit `--batch` when exactly one live sheet or one unfinished batch stands; the script refuses to guess between two and asks you to name one.

Exit 0 · 1 findings (JSON on stdout) · 2 environment (stderr). On findings, print them and STOP — a refused batch is not signable as it stands, and the remedy is on the sheet or in the tree, never in a flag.

### 2. THE USER READS THE RENDERING AND CONFIRMS

Present the rendering to the user: each claim, the verdict the sheet carries for it, and the domain the record will take. Ask for explicit confirmation to publish this one batch.

Do NOT infer a verdict for a row the sheet left empty. Do NOT split, merge, or reorder batches to make one publish. Do NOT sign a second batch in the same confirmation — one batch, one reading, one signature.

### 3. Sign — only on explicit confirmation

```bash
uv run "${CRUX_PLUGIN_ROOT}/scripts/signoff-survey.py" --repo-root <repo-root> [--batch SVY-NNNN] [--date YYYY-MM-DD]
```

The script advances the batch from its on-disk state to published, applying one write per state, and reports the writes it made and the records it published. An unchanged, already-published batch is a reported no-op at exit 0.

### 4. Confirm the batch published

Check the reported state is the published terminal state and that the `records` list matches the rendering the user confirmed. If the run stopped short, re-run the same command — the resume is total. Do not repair a surface by hand to "help it along": a surface that contradicts the receipt makes the script refuse the whole batch, which is the safety property, not a bug.

A run killed mid-write leaves a `<file>.tmp` beside its target. The re-run removes that temporary file itself and continues; it is the writer's to clear, never the operator's. Nothing under `_surveys/` is ever removed by hand.

### 5. Regenerate both projections

The batch publishes `ratified` records, which both projections read and hash:

```bash
uv run "${CRUX_PLUGIN_ROOT}/scripts/summarize-adrs.py"
uv run "${CRUX_PLUGIN_ROOT}/scripts/compile-doctrine.py"
```

The order is fixed because doctrine is compiled from the summaries projection. Skipping a step leaves drift that the matching `--dry-run` gate reports and that CI fails on.

### 6. The log op and the journal hook

The script writes the `observation` log op and the journal hook itself, absence-conditionally, as part of the publish. Do not add a second entry by hand; confirm the entry is present and name it in the hand-off.

### 7. Hand-off

Report the batch id, the record ids published, the rows deferred, and the rows rejected. Name any deferred candidate as re-scaffoldable on a later sheet, and remind the user that every published record's claim is now immutable — a changed claim is a successor record.

## Verification checklist

- [ ] Exactly one batch was signed, and the user confirmed it explicitly in this session after reading the rendering.
- [ ] No verdict, domain, or rationale was authored by the model; a sheet with an empty verdict was handed back, not filled.
- [ ] The dry-run rendering was shown BEFORE the sign.
- [ ] The reported records match the rendering the user confirmed.
- [ ] No record, receipt, index row, or log entry was hand-written to work around a refusal.
- [ ] Both projections were regenerated, in the fixed order, and their `--dry-run` gates are clean.
- [ ] `<docs_dir>/observations/index.md` reflects every published record.

## Red flags — STOP and reconsider

- About to author a verdict for an empty row. NEVER — the verdict is the human judgment this gate exists to capture. Hand the sheet back.
- About to sign without a dry-run rendering **in this session**. The rendering is the attestation; signing unread is gate-washing.
- About to sign two batches in one confirmation. NEVER — one batch, one reading, one signature.
- About to hand-edit a receipt, a staged record, `<docs_dir>/observations/index.md`, or anything under `_surveys/` to clear a refusal. NEVER — the script is the only write path, and a contradicting surface is exactly what it refuses to overwrite.
- About to re-run with an edited sheet after the batch was bound. A signed sheet is immutable; the digest refusal is correct. A changed review is a new batch.
- About to run this skill unattended inside a promptbook or dev-cycle. NEVER — it is a human gate, excluded from run-execution autonomy; hand the sheet to the user.
- About to leave the projections un-regenerated after a publish. The drift gate will red; regenerate in the fixed order.

## Rationalization table

| Excuse | Reality |
|---|---|
| "One row has no verdict but the claim is obviously fine — I'll ratify it." | An empty verdict refuses, by design. The human writes every verdict; a model-authored one is a ratification nobody made. |
| "The sheet needed a small correction, so I edited it and re-ran." | A signed sheet is immutable and the digest refusal is the gate working. A changed review is a new batch on a new sheet. |
| "The publish stopped halfway — I'll finish the last two records by hand." | Re-run the sign-off. The resume is total, and a hand-written record contradicts the receipt and refuses the whole batch. |
| "The last run was killed and left a `receipt.yml.tmp`, so I'll delete it to unblock the re-run." | Re-run the sign-off unchanged. The writer removes its own stale temporary file and retries; deleting one under `_surveys/` by hand is what the red flags forbid. |
| "Two batches are pending; signing both at once saves the user a round-trip." | One batch, one reading, one signature. Each batch is its own set of claims. |
| "A dev-cycle reached a sign-off step; the run authorizes it." | It does not. A human reading N claims is the authorization, and no run plan supplies that. The run STOPS and hands it to the user. |
| "The audit says the receipt is a stub, so I'll delete it and start over." | The remedy is re-running the sign-off, which completes the publish. Deleting a receipt discards the signature that covers the staged records. |

## Common mistakes

- **Confusing the two halves.** `survey-sheet` scaffolds and fills nothing; this skill signs and authors nothing.
- **Treating a refusal as a retry prompt.** Every refusal names a state on disk. Fix the state, or hand it back to the user; there is no flag that overrides one.
- **Repairing a partial publish by hand.** The script is re-entrant; a hand-repair is the one thing that turns a recoverable state into a refused one.
- **Skipping the projections.** Ratified records are projection inputs; a publish without a regenerate leaves drift CI fails on.
- **Reading `_surveys/` as records.** It is a frozen holding area excluded from every record walk; only the CHK-OBS-SURVEY-* rules read it.

## See also

- `survey-sheet` — the other half of the pair: the scaffold that writes the sheet this skill signs.
- `transition-observation` — the only single-record route past `observed`; this skill is the only batch route.
- `backfill-signoff` — the human-gate skill whose render-confirm-write shape this one mirrors.
- `reconcile-signoff` — the doctrine-layer sibling: one pairing, one reading, one verdict.
- `summarize-adrs` / `compile-doctrine` — the projections a publish drifts; regenerate both, in that order.
- `audit-docs` — the CHK-OBS-SURVEY-* rules (`docs/AGENTS.md` §17.3) that read the receipts this skill writes.
- `docs/AGENTS.md` §17.5 — the batch-ratification protocol: sheet and receipt schemas, layout, batch identity, and the state table. The source of truth.
- The batch-ratification decision this implements (see the ADR log).
