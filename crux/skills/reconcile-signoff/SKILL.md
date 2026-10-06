---
name: reconcile-signoff
description: "Record a human verdict on one invariant–rule reconciliation. Never choose the verdict or sign multiple pairings together."
disable-model-invocation: true
metadata:
  tags: "doctrine, reconciliation, invariants, human-gate"
  bundles: "crux-docs"
  risk_level: "medium"
  triggers: "sign off the reconciliation | reconcile INV-NNNN with ADR-NNNN | record the reconciliation verdict | this invariant is compatible with | mark the collision between"
  routing_note: "The single human write path for one doctrine reconciliation pairing: renders the invariant text and the rule text, takes the verdict (compatible | reconciled | collision) and rationale from the user, then upserts a digest-bound record and re-compiles doctrine on explicit confirmation. Human gate — excluded from run-execution autonomy; carries `disable-model-invocation`. Never chooses a verdict; never batch-signs."
---

# Reconcile Sign-off

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

The **human sign-off** for one doctrine reconciliation pairing. A doctrine domain renders its belief only when every structurally-declared (ratified-invariant, governing-rule) pairing in it has a digest-current verdict of `compatible` or `reconciled` in the reconciliation ledger. This skill is the ONE write path over that ledger, `<docs_dir>/adrs/doctrine/reconciliations.yml`, driven by `scripts/signoff-reconciliation.py`. It is the doctrine layer's sibling of `backfill-signoff`: one human gate, an audited write, and a refusal for every state it does not recognize.

**The interpretive judgment is the human's, not the model's.** Do a ratified invariant and a governing rule conflict? The script renders both texts verbatim; the **user** reads them and states the verdict. The three verdicts:

- **compatible** — the two texts do not conflict.
- **reconciled** — the two texts *appear* to conflict, but a human asserts they are in fact compatible, under an attached rationale (`--rationale` is REQUIRED).
- **collision** — they conflict. This blocks the domain's belief until the deciding ADR or the invariant is revised.

The record binds the **current live content digest** — a SHA-256 over the invariant ledger page's `## The invariant` text and the rule text, space-folded. A later edit to either text moves the digest and re-gates the pairing (digest-stale, BROKEN) until it is re-adjudicated against the settled texts. Re-signing an unchanged pairing to the same verdict is an idempotent no-op.

**Excluded from run-execution autonomy — a human gate.** Like `backfill-signoff` and `escalate-arch-runtime`, a running promptbook or dev-cycle must NOT invoke this skill unattended. Its writes are reversible in-repo edits, which a started run otherwise treats as pre-authorized — but that default does not reach here, because the authorization this skill needs is a human reading the two texts and choosing a verdict, and no run plan can pre-supply that. A run that reaches a reconciliation-sign-off step STOPS and hands the pairing to the user. This skill also carries `disable-model-invocation: true`, so the model never auto-selects it; the user invokes it by name.

## When to use

- The user says: "sign off the reconciliation", "reconcile INV-NNNN with ADR-NNNN", "record the reconciliation verdict", "this invariant is compatible with", "mark the collision between".
- A doctrine domain rendered **BROKEN** because a pairing is un-adjudicated, and the user is ready to read the texts and decide.
- A pairing went **digest-stale** after the ADR or invariant text moved, and the user is ready to re-adjudicate against the settled texts.

Do **not** use this skill for:
- Choosing a verdict on the user's behalf — the verdict is a human judgment; this skill only renders, validates, and writes.
- Regenerating doctrine as a whole — that is `compile-doctrine`. This skill re-compiles doctrine *inside its own write set* after a sign, because the ledger is a compile input; it is not the general doctrine regenerator.
- Transitioning an invariant pin (that is `transition-invariant`) or an ADR (that is `transition-adr`).
- Hand-editing `reconciliations.yml` — refuse. This script is the only write path; a foreign edit red-gates the doctrine drift check and skips the human signature.

## Prerequisites

1. The tree exists and `<docs_dir>/manifest.yml` is present.
2. The repo root is resolved (step 0 below).
3. The invariant is **ratified** and the pairing is a real candidate — the script enforces both (an `observed` invariant, an unknown handle, or a pairing whose invariant does not structurally link to the rule's ADR is refused). You do not pre-check these; you read the script's findings.

## The pipeline

### 0. Resolve per-repo configuration (.bionic.yml)

Run `python3 "${CRUX_PLUGIN_ROOT}/scripts/bionic-config.py"` from the repo root. On exit 1, STOP and surface the `{"error": ...}` payload. Use the returned `docs_dir` wherever this skill says `<docs_dir>` (default `bionic`).

### 1. List the pending pairings

The pending pairings are read from the compiled doctrine index, `<docs_dir>/adrs/doctrine/index.md`. A pairing that needs a human is rendered there like this (the marker is derived from `compile-doctrine.py`'s output shape, which documents it nowhere else):

- Its domain heading ends in **`— BROKEN`**, and the domain carries a `> **BROKEN**` blockquote.
- Under that domain's `_Reconciliation against ratified invariants:_` table, a row `| INV-NNNN | ADR-NNNN/<slug> | <status> |` whose **status is `un-adjudicated`** (never signed), **`digest-stale`** (signed, but a text moved since), or **`collision`** (signed as conflicting).

Read the invariant id and the governing handle straight from that row. A row whose status is `compatible` or `reconciled` needs no sign-off. List every pending pairing to the user. If none are pending, say so and STOP — there is nothing to sign.

### 2. Dry-run render — show the two texts

For the chosen pairing, run the render (which writes nothing):

```bash
uv run "${CRUX_PLUGIN_ROOT}/scripts/signoff-reconciliation.py" \
  --invariant INV-NNNN --handle ADR-NNNN/<slug> --verdict <verdict> [--rationale TEXT] --dry-run --repo-root <repo-root>
```

The render prints the invariant text verbatim (from `## The invariant`), the rule text verbatim (from the ADR's `governs` block), the live content digest, and the validation report. On a pairing the script would refuse (unknown handle, unratified invariant, missing rationale for `reconciled`, a non-candidate pairing), the render still prints FIRST, then the findings, then exits 1 — read the findings and STOP; do not sign a refused pairing.

### 3. THE USER READS THE TWO TEXTS AND CHOOSES THE VERDICT

Present both texts to the user and collect the verdict — and, for `reconciled`, the rationale — **from the user**. Do NOT infer or choose a verdict yourself. Do NOT batch several pairings into one decision; each pairing is read and decided on its own. This reading IS the human judgment the doctrine gate exists to require; there is no shortcut.

- `reconciled` requires a rationale (the human's assertion that two apparently-conflicting texts are in fact compatible). The script refuses `reconciled` with no `--rationale`, and refuses a rationale on any other verdict.

### 4. Sign — only on explicit confirmation

After the user confirms the verdict (and rationale) for this one pairing, run the real sign:

```bash
uv run "${CRUX_PLUGIN_ROOT}/scripts/signoff-reconciliation.py" \
  --invariant INV-NNNN --handle ADR-NNNN/<slug> --verdict <verdict> [--rationale TEXT] [--date YYYY-MM-DD] --repo-root <repo-root>
```

The script validates fail-closed, upserts the digest-bound record into `reconciliations.yml` (the ledger is machine-written in full, canonical and sorted), then **re-compiles doctrine inside its own write set** (the ledger is a compile input, so a sign without the re-compile would red the drift gate) and runs the drift check behind it. An unchanged re-sign is a reported no-op. Exit 0 signed / no-op; exit 1 findings (JSON on stdout); exit 2 environment/crash (stderr).

### 5. Confirm the post-gate is green

The script's output ends with the post-gate lines: `compile-doctrine re-derived N files` and `compile-doctrine --dry-run clean`. If the post-gate is red (the ledger write landed but the re-compile is dirty or crashed), the script says so loudly and returns non-zero — surface it and remediate before the next sign-off. Do not work around a red post-gate by hand-editing any surface.

### 6. Append to `<docs_dir>/log.md`

The sign-off changes doctrine state, so log it. Doctrine is an `adrs/` projection, so it rides the `adr` op (there is no dedicated reconciliation op in the enum). Newest entry at the top:

```
## [YYYY-MM-DD] adr | reconcile-signoff (INV-NNNN x ADR-NNNN/<slug> = <verdict>)
```

Body, 1–3 lines: the pairing, the verdict, and the resulting domain state (believed / still BROKEN with the remaining pending pairings). On a no-op re-sign, log it as a no-op.

### 7. Hand-off

Report the pairing, the signed verdict and date, and the domain's resulting state. If other pairings in the same domain are still pending, name them — the domain renders its belief only when every pairing in it clears.

## Red flags — STOP and reconsider

- About to choose a verdict for the user. NEVER — the verdict is the human judgment this gate exists to capture. Render the texts and ask.
- About to sign without a dry-run render **in this session**. The rendered texts are the attestation; signing unread is gate-washing.
- About to batch-sign several pairings in one decision. NEVER — one pairing, one reading, one verdict. Each has its own texts.
- About to hand-edit `reconciliations.yml`, `adrs/doctrine/index.md`, or `_meta.json` to record or clear a verdict. NEVER — this script is the only write path; a foreign edit red-gates the drift check and skips the signature.
- About to run this skill unattended inside a promptbook or dev-cycle. NEVER — it is a human gate, excluded from run-execution autonomy; hand the pairing to the user.
- About to sign a `reconciled` verdict with no rationale, or attach a rationale to a `compatible`/`collision` verdict. The script refuses both — provide the rationale for `reconciled`, and only there.
- About to leave a red post-gate after a landed sign. Remediate before the next sign-off; do not paper over it.

## Rationalization table

| Excuse | Reality |
|---|---|
| "The two texts obviously don't conflict — I'll just sign it compatible." | The verdict is the human's. Render the texts, present them, and let the user decide. Signing on the model's read defeats the gate. |
| "Three pairings are pending in this domain — sign them all at once." | Each pairing is a distinct (invariant, rule) text pair. Read and decide each on its own; there is no batch verdict. |
| "I'll add the record straight to `reconciliations.yml` to save a round-trip." | The ledger has ONE write path. A hand-edit red-gates the doctrine drift check and records a verdict nobody signed. |
| "A dev-cycle reached a sign-off step; the run authorizes it." | It does not. A human reading two texts is the authorization, and no run plan supplies that. The run STOPS and hands it to the user. |
| "`reconciled` without a rationale is fine — the compatibility is self-evident." | Then the verdict is `compatible`. `reconciled` means apparently-conflicting-but-not, and the rationale is its required justification. |

## Common mistakes

- **Choosing the verdict.** The model renders and writes; the human judges. Never infer a verdict.
- **Confusing sign-off with compilation.** This skill signs ONE pairing and re-compiles doctrine as a side effect. Regenerating doctrine wholesale is `compile-doctrine`.
- **Signing a refused pairing.** If the dry-run render lists findings (unknown handle, unratified invariant, non-candidate pairing), STOP — the pairing is not signable as it stands.
- **Reusing a rationale across verdicts.** A rationale belongs only to `reconciled`; the script refuses it elsewhere.
- **Ignoring a red post-gate.** The ledger write can land while the re-compile is red; that is reported loudly and must be remediated, not ignored.
- **Hand-editing the ledger or the doctrine tree.** Both are machine-owned — the ledger by this script, the doctrine index by `compile-doctrine`.

## See also

- `backfill-signoff` — the human-gate skill whose confirm-then-write shape this one mirrors (the governs-backfill sibling).
- `compile-doctrine` — the doctrine regenerator; this skill re-compiles doctrine inside its own write set after a sign, but is not the general compiler.
- `transition-invariant` — the human gate that ratifies, rejects, or retires an invariant pin; only a ratified invariant seeds a reconciliation pairing.
- `<docs_dir>/adrs/doctrine/index.md` — where pending pairings surface as BROKEN domains with `un-adjudicated` / `digest-stale` / `collision` rows.
