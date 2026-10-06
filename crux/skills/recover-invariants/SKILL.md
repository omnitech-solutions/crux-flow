---
name: recover-invariants
description: "Mine code for candidate invariants and checks. Record them as observed, never ratified; human disposition is separate."
metadata:
  tags: "invariants, recovery, extraction, candidates, machine-proposes"
  bundles: "crux-docs"
  risk_level: "medium"
  triggers: "recover invariants | mine invariants | extract invariant candidates"
---

# Recover Invariants

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

The **machine** side of the invariants concern (`docs/AGENTS.md` §15.3) — the Span-2 sibling of `extract-code-docs`. It mines source code for **candidate** invariants and lands them as:
- a **ledger stub** per candidate at `<docs_dir>/invariants/<slug>.md`, always `provenance: recovered, ratification: observed`, created **at recovery time** so no candidate is invisible;
- zero-or-more **candidate checks** in the `invariants/checks/` subdirectory;
- **reconciliation entries** mapping each check to its pin with an initial `last_result`.

**The hard safety boundary: this skill NEVER sets `ratification: ratified`.** Everything it emits is a candidate marked `observed`; only a human, via `transition-invariant`, disposes of it. This is the machine-proposes/human-disposes model — the exact shape crux already ships for ADRs (`propose-adr` never auto-accepts).

> **v1 is deliberately THIN.** This skill fixes the *emit contract* — the ledger-stub shape, the candidate-check placement, the reconciliation write, and per-class honesty — NOT deep per-language mining. The **recovery-extractor internals** (per-language characterization/shape/contract mining, how candidate checks are shaped, where per-class confidence is recorded mechanically) are an explicit **deferred follow-on** (`docs/AGENTS.md` §15). Do not mistake this v1 for a full extractor; when the extractor-design decision lands, this skill grows a scripted backend like `extract-code-docs`'s plugins.

## When to use

- User says: "recover invariants", "mine invariants", "extract invariant candidates", "propose invariants from the code".
- Proactively after a large module lands, to survey what invariants the code *appears* to hold (all as `observed` candidates for later human ratification).

Do **not** use this skill for:
- Ratifying/rejecting/retiring pins — that's `transition-invariant` (the human gate).
- Regenerating code docs — that's `extract-code-docs` (the Span-1 sibling).
- Authoring an *intended* invariant a human already knows they want — a human authors that directly as `provenance: authored` (this skill only emits `recovered`).

## Per-class recovery confidence (report honestly)

The five invariant classes (`docs/AGENTS.md` §15.2) recover at very different confidence; state it per candidate, never inflate:
- **shape / contract** — near-mechanical (types, signatures, schema shape). High-confidence candidates.
- **data** — often mechanical (ranges, non-null, referential) but sometimes inferred. Medium.
- **behavior** — recoverable only as *characterization* candidates at scale (a check that pins "what the code currently does", which may include bugs). Low — explicitly candidate.
- **experience** — the *why* is not in the code; only **reconstructed-and-provisional** at best. Lowest; usually a ledger stub with no trustworthy check.

## The pipeline

### 0. Resolve config
Run `python3 "${CRUX_PLUGIN_ROOT}/scripts/bionic-config.py"` (compat: `crux-config.py`); use the resolved `docs_dir`; accept the artifact prefix for new `INV-NNNN` ids. On exit 1, STOP.

### 1. Mine candidates (v1: bounded + honest)
Read the target source (a module/dir the user names, or a bounded default). For each candidate invariant, decide its `class` and a one-line intent. v1 does this by reading + judgment (no scripted extractor yet); keep the batch **small and legible** rather than exhaustive — a survey, not a sweep. Never emit a candidate you cannot state a check idea for (except `experience`, which may be a check-less stub).

### 2. Allocate pin ids
`INV-NNNN`, zero-padded, monotonic. If the manifest grows an `invariant.next_number` counter, use it (write-first ordering like `propose-adr`); until then, take max existing `INV-` id + 1. Numbers are never reused.

### 3. Write the ledger stub (at recovery time — never deferred)
For each candidate, write `<docs_dir>/invariants/<slug>.md` with the §15.2 frontmatter: `id`, `class`, `provenance: recovered`, `ratification: observed`, `verification: {last_result: none}` (until a check runs), `related_adrs`/`related_briefs` (`[]` if none), `checks` (the ids emitted in §4, or `[]`). Body: the one-line intent + the honest per-class confidence note. The stub existing at recovery time is what makes survey-debt legible.

### 4. Emit candidate checks (into the suite)
For each check idea, write a candidate check into `<docs_dir>/invariants/checks/` (a runnable artifact — a small test/script/schema assertion appropriate to the class). Mark it, in the reconciliation (§5), as belonging to a `recovered/observed` pin so the suite can tell candidate scratch from ratified-durable core. `experience` candidates may have no check.

### 5. Write reconciliation entries
For each emitted check, add a reconciliation entry `{check_id, pin_id, last_result, last_checked}` (`docs/AGENTS.md` §15.4). Initial `last_result` is the check's first recorded outcome if you ran it, else `none`; `last_checked` accordingly. Never fabricate a `pass`.

### 6. Index + log
- `<docs_dir>/invariants/index.md`: add each new pin's row, **visibly marked `observed`**.
- `docs/index.md`: bump `## Invariants (N)` and `_Last updated:`.
- `docs/log.md`: one entry summarizing the batch (count of candidates per class, where they landed). Use the concern's log op.

### 7. Hand off
Report the candidates as **observed** and name `transition-invariant` as the human gate. NEVER imply any candidate is trusted. Surface the per-class confidence so the human ratifies with eyes open.

## Verification checklist

- [ ] Every emitted pin is `provenance: recovered, ratification: observed` — **zero `ratified`**.
- [ ] A ledger stub exists for every candidate (created at recovery time; none deferred/invisible).
- [ ] Every emitted check has a reconciliation entry mapping it to exactly one pin.
- [ ] No `last_result: pass` was fabricated — `none` unless a check actually ran.
- [ ] Per-class confidence was reported honestly (behavior = characterization candidate; experience = reconstructed-provisional).
- [ ] Index rows visibly mark the new pins `observed`; `docs/index.md` count + `_Last updated:` bumped.
- [ ] `docs/log.md` batch entry written.

## Red flags — STOP and reconsider

- About to write `ratification: ratified` (or anything but `observed`). NEVER. Recovery cannot self-ratify — that is the concern's whole safety invariant.
- About to fabricate a `last_result: pass` to make a candidate look trustworthy. Never; unrun checks are `none`.
- About to emit a behavior candidate as though it were intended truth. A characterization check cements *what the code does*, bugs included — mark it a candidate and say so.
- About to skip the ledger stub and only emit a check. Every candidate gets a stub at recovery time, or it's an invisible pin (survey-debt must be legible).
- About to build a deep per-language extractor here. That's the deferred follow-on; v1 is a thin, honest, bounded emitter.

## Rationalization table

| Excuse | Reality |
|--------|---------|
| "This shape invariant is obviously correct — I'll ratify it to save a step." | Recovery NEVER ratifies. Emit `observed`; the human ratifies via `transition-invariant`. |
| "The characterization test passes, so the behavior is verified." | It reproduces *current* behavior, bugs included. `pass` on a characterization check is not evidence the behavior is *intended*. Keep it `observed`. |
| "I'll skip the ledger stub for low-confidence candidates." | Then they're invisible pins and survey-debt is under-counted. Stub every candidate at recovery time. |
| "I'll write a full per-language miner now." | Deferred by design. v1 fixes the emit contract; the extractor depth is a separate decision. |

## See also

- `transition-invariant` — the human gate that disposes of the `observed` pins this skill proposes.
- `extract-code-docs` — the Span-1 regenerative sibling (docs from source); this is Span-2 (invariant candidates from source).
- `audit-docs` — the CHK-INV rules that read what this skill emits (`docs/AGENTS.md` §15.5).
- `docs/AGENTS.md` §15 — the invariants-concern contract.
