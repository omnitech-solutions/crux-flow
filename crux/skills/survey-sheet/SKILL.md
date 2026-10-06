---
name: survey-sheet
description: "Scaffold a batch review sheet for observed candidates. Leave verdicts and rationales to the human; ratify nothing."
disable-model-invocation: true
metadata:
  tags: "observations, ratification, survey, batch, human-gate"
  bundles: "crux-docs"
  risk_level: "low"
  triggers: "scaffold a survey sheet | start a batch review of observations | build the observation review sheet | survey the observed candidates | open a ratification batch | I want to review the observed candidates in one pass"
  routing_note: "Scaffolds one SVY-NNNN batch review sheet from the observation candidate state file, seeding the machine cells and leaving verdict, domain and rationale empty. Half of the batch-ratification pair; `survey-signoff` is the other half. Human gate — excluded from run-execution autonomy; carries `disable-model-invocation`. Never fills a verdict, never publishes a record."
---

# Survey Sheet

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

The **machine half** of batch ratification for the observations concern. It reads the candidate state file, filters it to the candidates no receipt has disposed and no live record already holds, allocates the next `SVY-NNNN` from the monotonic `observation.next_survey_number`, and writes one review sheet at `<docs_dir>/observations/survey-SVY-NNNN.yml`.

**The protocol's source of truth is `docs/AGENTS.md` §17.5** — the sheet and receipt schemas, the layout, the batch-identity rule, and the state table that governs the sign-off. Read it there; this skill restates none of it and never carries a second copy.

**It fills no human cell.** Each row is seeded with `anchor_id` and `proposed_domain` and leaves `verdict`, `domain` and `rationale` empty. That is the boundary this skill sits on: a machine may scaffold a row and may never author a verdict, a domain override, or a rationale. The sign-off refuses an empty verdict, so a sheet this skill wrote publishes nothing until a human has authored every one of those cells.

**Scaffolding is not ratification.** This skill writes one `.yml` and moves one counter. It writes no observation record, touches no record's status, and updates no index. Nothing here reaches `ratified`.

**Excluded from run-execution autonomy — a human gate.** Like `backfill-signoff` and `reconcile-signoff`, a running promptbook or dev-cycle must NOT invoke this skill unattended. Its writes are reversible in-repo edits, which a started run otherwise treats as pre-authorized — but that default does not reach here, because the sheet exists only to be filled in by a human, and a run that scaffolds one unattended produces a sheet nobody will read. A run that reaches a batch-review step STOPS and hands the sheet to the user. This skill also carries `disable-model-invocation: true`, so the model never auto-selects it; the user invokes it by name.

## When to use

- The user says: "scaffold a survey sheet", "start a batch review of observations", "build the observation review sheet", "survey the observed candidates", "open a ratification batch".
- A miner or a re-mine has left a pile of `observed` candidates and the user wants to dispose of them in one reading rather than one transition at a time.
- A previous batch deferred rows, and the user is ready to review them again. `defer` is the only re-scaffoldable verdict — a deferred candidate reappears on the next sheet, and a ratified or rejected one never does.

Do **not** use this skill for:
- Filling any cell of the sheet. The three human cells are the human's; refuse to author them, and refuse to "helpfully pre-fill the obvious ones".
- Signing the sheet — that is `survey-signoff`, and it is a separate human act.
- Disposing of a single record — that is `transition-observation`, the only single-record route.
- Creating a candidate or a record — those are `recover-decisions`, `propose-observation`, and `transition-decision`.

## Prerequisites

1. The tree exists, `<docs_dir>/manifest.yml` is present, and `observations` is in `concerns_enabled`. The script refuses otherwise.
2. `<docs_dir>/observations/` exists.
3. **No live sheet is already awaiting sign-off.** The script refuses a second scaffold while one stands: two live sheets over overlapping candidate sets would both ratify one anchor and leave two live records on it, which is the CHK-OBS-ANCHOR defect. Sign the standing sheet first. Deleting it is the user's call to abandon that batch, never this skill's way around the refusal — a live sheet may already carry verdicts the user authored.
4. **No unfinished batch holds the anchor.** Row 1 of the sign-off deletes the live sheet, so the refusal above stops guarding the moment a batch is bound. Until a batch reaches S9 every anchor its receipt names is held, and the scaffold reports `batch_id: null` with a note naming the batch. The hold is not a disposal: re-run `signoff-survey.py` on the named batch, and the anchors — deferred ones included — are offered again on the next scaffold.

## The pipeline

### 0. Resolve per-repo configuration (.bionic.yml)

Run `python3 "${CRUX_PLUGIN_ROOT}/scripts/bionic-config.py"` from the repo root. On exit 1, STOP and surface the `{"error": ...}` payload. Use the returned `docs_dir` wherever this skill says `<docs_dir>` (default `bionic`).

### 1. Dry-run — show what the sheet would carry

```bash
uv run "${CRUX_PLUGIN_ROOT}/scripts/scaffold-survey-sheet.py" --repo-root <repo-root> --dry-run
```

`--dry-run` writes nothing at all — not the sheet and not the counter — and reports the batch id it would allocate, the sheet path, the row count, and the anchors. A payload with `"rows": 0` and `"batch_id": null` means no candidate awaits a batch: say so and STOP, rather than writing an empty sheet.

Exit 0 scaffolded or nothing-to-scaffold · 1 findings (JSON on stdout) · 2 environment (stderr). Read the findings; a refusal here is a real state, not a retry prompt.

### 2. Scaffold

```bash
uv run "${CRUX_PLUGIN_ROOT}/scripts/scaffold-survey-sheet.py" --repo-root <repo-root>
```

The counter moves before the sheet is written — over-allocation is safe, and reuse is not. The reported `written` list names the sheet and `manifest.yml`, and nothing else.

### 3. Hand the sheet to the user

Report the batch id, the sheet path, and the row count. Then state plainly what the user must do: open the sheet and, for every row, write a `verdict` of `ratify`, `reject` or `defer`, optionally override `domain`, and record a `rationale`. Do not offer to fill them in. Do not summarize the rows into a recommendation that amounts to filling them in.

Name the next command without running it:

```bash
uv run "${CRUX_PLUGIN_ROOT}/scripts/signoff-survey.py" --repo-root <repo-root> --dry-run
```

### 4. Write no log op

The scaffold logs nothing. Do not append to `<docs_dir>/log.md`.

The subject `observation | survey batch SVY-NNNN` is the SIGN-OFF's, reserved by §6 and written by the sign-off with the published record ids in the body. A scaffold-written entry under that subject wedges the batch it just created. The sign-off reads any entry under that subject as its own log op already landed, so it skips writing the real one. It then refuses the batch, because the entry standing on disk names no published records. Nothing is published, and both projections refuse the whole tree. The remedy is to delete the entry the scaffold should never have written. That deletion is permitted: `survey-signoff` red-flags a hand-edited receipt, a hand-edited staged record, a hand-edited `<docs_dir>/observations/index.md`, and anything under `_surveys/`, and `log.md` is on none of those lists.

The batch is not unrecorded in the meantime. Its id, its rows and its scaffold provenance are in the sheet, and `manifest.yml`'s `observation.next_survey_number` records that the id was issued. The tree's `log.md` entry for the batch is written once, by the sign-off, and names what the batch actually published.

## Verification checklist

- [ ] `observations` is in `concerns_enabled` and the concern directory exists.
- [ ] No second live sheet was created; a standing sheet refused the scaffold.
- [ ] Every row's `verdict`, `domain` and `rationale` are EMPTY as written — the model authored none of them.
- [ ] The batch id came from `observation.next_survey_number` and no id was reused.
- [ ] No observation record was created, modified, or transitioned.
- [ ] `<docs_dir>/observations/index.md` was NOT touched — the scaffold writes no record and therefore no row.
- [ ] `<docs_dir>/log.md` was NOT touched — the `survey batch SVY-NNNN` subject belongs to the sign-off.

## Red flags — STOP and reconsider

- About to write a `verdict`, a `domain` override, or a `rationale` into the sheet. NEVER — those three cells are the whole human half of the gate. A machine-filled verdict is a ratification nobody made.
- About to recommend verdicts row by row so the user can "just confirm". That is filling the sheet with extra steps. Render the rows; let the user read the claims.
- About to scaffold a second sheet while one is live, or to delete a live sheet to clear the refusal. Two sheets over overlapping candidates both ratify one anchor, and a live sheet may already carry verdicts the user wrote. Abandoning a batch is the user's call; ask, and let them make it.
- About to hand-edit `observation.next_survey_number` because a number was burnt. A burnt number is a number nobody signs; reuse is the failure, not over-allocation.
- About to run this skill unattended inside a promptbook or dev-cycle. NEVER — it is a human gate, excluded from run-execution autonomy.
- About to run the sign-off in the same breath as the scaffold. The sheet is scaffolded so that a human can read it; signing an unread sheet is gate-washing.
- About to append an `observation | survey batch SVY-NNNN` entry to `log.md` because the scaffold "should be recorded". That subject is the sign-off's, and an entry under it wedges the batch: the sign-off reads its own log op as already landed, and then refuses a batch it never published. Deleting that entry is the permitted fix — `log.md` is not one of the surfaces the sign-off forbids hand-editing — but the entry never written costs nothing to remove.

## Rationalization table

| Excuse | Reality |
|---|---|
| "The proposed domains are all obviously right — I'll seed the verdicts too." | The seeded cells are machine cells; the verdict is not one of them. A sheet with verdicts nobody wrote is a batch ratification nobody made. |
| "There is already a live sheet, but it is stale — I'll scaffold a fresh one." | One live sheet at a time. Two sheets over overlapping candidates leave two live records on one anchor. Sign the standing sheet, or ask the user whether to abandon it; never delete it yourself. |
| "Nothing is eligible, so I'll write an empty sheet to keep the workflow moving." | An empty batch signs nothing. Report that no candidate awaits a batch and stop. |
| "The cycle plan says scaffold-then-sign, so the run authorizes both." | It does not. The authorization this pair needs is a human reading N claims, and no run plan supplies that. |
| "A deferred candidate keeps reappearing — I will reject it to clear the list." | `defer` is the only re-scaffoldable verdict, by design. Its reappearance is the feature; the disposition is still the human's. |

## Common mistakes

- **Treating the scaffold as the ratification.** It writes one `.yml` and a counter. The sign-off publishes.
- **Reading the sheet as a record surface.** The live sheet is a `.yml` and the concern's record walks are `*.md`-scoped, so it is never a record and never a stray file.
- **Editing `_surveys/`.** The archive of signed sheets and receipts is machine-owned and frozen; the audit reads it and nothing hand-edits it.
- **Writing the log op.** The `survey batch SVY-NNNN` subject is the sign-off's, and a scaffold-written entry under it wedges the batch with nothing published. Delete the spurious entry to unwedge it.

## See also

- `survey-signoff` — the other half of the pair: the human sign-off that publishes the batch this sheet describes.
- `transition-observation` — the only single-record route past `observed`; unchanged by batch ratification.
- `recover-decisions` — the miner whose candidates fill the state file this skill reads.
- `propose-observation` — the reconstructed on-ramp that writes `observed` records by hand.
- `audit-docs` — the CHK-OBS-SURVEY-* rules (`docs/AGENTS.md` §17.3) that read the batch surfaces.
- `docs/AGENTS.md` §17.5 — the batch-ratification protocol: sheet and receipt schemas, layout, batch identity, and the state table. The source of truth.
- The batch-ratification decision this implements (see the ADR log).
