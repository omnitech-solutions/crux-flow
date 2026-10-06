---
name: compile-doctrine
description: "Regenerate doctrine from decision summaries and signed invariant reconciliations, or check its drift. Never edits sign-offs."
metadata:
  tags: "doctrine, adrs, regeneration, drift-detection"
  bundles: "crux-docs"
  risk_level: "low"
  triggers: "compile the doctrine | build the doctrine | regenerate the doctrine | refresh the doctrine | is the doctrine current | doctrine drift"
  routing_note: "Regenerative — rewrites `<docs_dir>/adrs/doctrine/` wholesale from the summaries projection, reconciled against the ratified invariants and the human-signed reconciliation ledger. `--dry-run` is the drift check. Stops and recommends `summarize-adrs.py` first when the summaries projection has drifted; never writes `reconciliations.yml`."
---

# Compile Doctrine

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

The `doctrine` layer is the per-domain view of what the project currently holds to be true. It lives at `<docs_dir>/adrs/doctrine/` (an `index.md` plus a `_meta.json` digest block) and is a deterministic, model-free projection — regenerated wholesale, the way `code/` and the summaries projection are. It is NOT hand-written; it is compiled and kept current.

This skill is a thin wrapper around `scripts/compile-doctrine.py`. The script does the work: it reads the doctrine inputs, renders one section per `governs` domain (each domain's live rule, disposition, mechanical basis, and its reconciliation state against the ratified invariants), and stamps the input digests so drift is detectable. The basis column takes one of four values: `run-bound` or `not-run-bound` for an ADR-sourced rule, `evidence-resolves` or `evidence-missing` for an observation-sourced rule; none is evidence the rule holds.

**Doctrine is a pure function of three inputs.** Compile reads exactly these, and nothing that varies per run:

- **The summaries projection** (`<docs_dir>/adrs/summaries/`) — the rule table projected from every active ADR's `governs` blocks. This is doctrine's rule source.
- **The ratified-invariant ledger** (`<docs_dir>/invariants/`) — each ledger page's `## The invariant` section body plus its `related_adrs`. This is what a domain's rules are reconciled against.
- **The human-signed reconciliation ledger** (`<docs_dir>/adrs/doctrine/reconciliations.yml`) — the digest-bound verdict (`compatible` / `reconciled` / `collision`) per (invariant, rule) pairing. Compile READS it and stamps its bytes into the provenance digest; it NEVER writes it.

**The summaries dependency.** Doctrine's rule text is the summaries projection, so a stale summaries projection makes doctrine stale. Before compiling, if `summarize-adrs.py --dry-run` reports drift, that regenerator runs FIRST — recommend running it and re-checking, then compile doctrine. Compiling on top of a drifted summaries projection produces a doctrine that lies.

**Regenerative invariant.** `adrs/doctrine/` is throw-away output. Every compile rewrites it wholesale; hand-edits under `adrs/doctrine/` are OVERWRITTEN. NEVER hand-edit anything there. NEVER read `adrs/doctrine/*` as an input to a regeneration — the canonical state lives in the three inputs above.

Doctrine is the primary read surface for a current-belief question (doctrine → summaries → ADR body), but it holds zero decision authority: the ADR body wins on any disagreement.

## When to use

- User says: "compile the doctrine", "build the doctrine", "regenerate the doctrine", "refresh the doctrine", "is the doctrine current", "doctrine drift".
- Proactively after a change to any doctrine **input**: a `governs`-bearing ADR was proposed / accepted / superseded (which drifts the summaries projection), a ratified invariant's `## The invariant` text or `related_adrs` changed, or `reconcile-signoff` recorded a new verdict.
- A pre-commit / CI hook, `audit-docs`, or `check-drift` reports doctrine drift (all call the script with `--dry-run`).

Do **not** use this skill for:
- Reading the current belief — that is opening `<docs_dir>/adrs/doctrine/index.md` (or ask `query-docs` / the `librarian`). This skill *builds* the surface; it does not answer questions from it.
- Recording a reconciliation verdict — that is `reconcile-signoff`, the only write path for `reconciliations.yml`.
- Regenerating the summaries projection — that is `summarize-adrs.py`. Doctrine consumes summaries; it does not rebuild it.

## Prerequisites

Before invoking the script, confirm:

1. The tree exists and `<docs_dir>/manifest.yml` is present.
2. The repo root is resolved (per step 0 below).
3. The summaries projection is fresh — see step 1. A drifted summaries projection is a stop-and-fix-first condition, not something to compile over.

If a prerequisite fails: tell the user what to fix and STOP. Do not silently write nothing.

## The pipeline

### 0. Resolve per-repo configuration (.bionic.yml)

Run `python3 "${CRUX_PLUGIN_ROOT}/scripts/bionic-config.py"` from the repo root (or pass `--repo-root <repo-root>`), and confirm the returned `repo_root` is the repo you are operating in. On exit 1, **STOP** and surface the `{"error": ...}` payload — never fall back to defaults. Use the returned `docs_dir` wherever this skill says `<docs_dir>` (default `bionic`).

### 1. Confirm the summaries projection is fresh

```bash
uv run "${CRUX_PLUGIN_ROOT}/scripts/summarize-adrs.py" --dry-run --repo-root <repo-root>
```

- Exit 0 — the summaries projection is current; continue to step 2.
- Exit 1 with drift — **STOP compiling.** Doctrine reads summaries as its rule source, so a drifted summaries projection would compile a stale doctrine. Recommend running `summarize-adrs.py` (no `--dry-run`), commit that, then re-run this skill. Do not compile on top of the drift.

### 2. Run the dry-run drift check

```bash
uv run "${CRUX_PLUGIN_ROOT}/scripts/compile-doctrine.py" --dry-run --repo-root <repo-root>
```

Parse stdout as JSON regardless of exit code. Exit-code semantics (shared with the other crux regenerators): `0` clean, `1` drift or an input validation error (valid JSON on stdout), any other non-zero with empty or unparseable stdout = crash (surface stderr, never a document finding).

- **`{"validation_errors": [...]}` on stdout** — a malformed `governs` entry or a malformed reconciliation ledger. **STOP: report BROKEN and DO NOT regenerate.** Regenerating cannot fix a malformed input; surface the problems for the user to repair, then re-run.
- **`{"drift": false, "paths": []}`** (exit 0) — doctrine is current. Nothing to write; skip to step 5 (log the no-op).
- **`{"drift": true, "paths": [...]}`** (exit 1) — doctrine has drifted from its inputs. The `paths` array names the files that would change. Continue to step 3.

### 3. Regenerate (only on drift)

```bash
uv run "${CRUX_PLUGIN_ROOT}/scripts/compile-doctrine.py" --repo-root <repo-root>
```

Success prints `{"written": [<paths>]}` — the files re-emitted. Because output is byte-stable, a compile with no input change writes the same bytes; the git diff (or the `_meta.json` input digests) shows what actually moved.

### 4. Confirm convergence — re-run the dry-run ONCE

```bash
uv run "${CRUX_PLUGIN_ROOT}/scripts/compile-doctrine.py" --dry-run --repo-root <repo-root>
```

- Exit 0 (`{"drift": false}`) — converged. Continue to step 5.
- Still dirty (exit 1) — **non-convergence. Report it and STOP. Do NOT loop.** A regenerator that does not converge in one write is a bug in the inputs or the script; surface the residual `paths` and any stderr, and hand it to the user. Re-running the compile a second time is not the fix.

### 5. Append to `<docs_dir>/log.md`

Doctrine is an `adrs/` projection, so it rides the `adr` log op (there is no dedicated doctrine op in the enum). Newest entry at the top:

```
## [YYYY-MM-DD] adr | compiled <docs_dir>/adrs/doctrine/ (N files)
```

Body, 1–3 lines: which files changed (or "no change — already current"), and the trigger (manual, source change, or `--dry-run` drift). Doctrine carries no timestamps in its own tree — the log entry plus git are the provenance record.

### 6. Verification

- [ ] `<docs_dir>/adrs/doctrine/` contains `index.md` and `_meta.json`.
- [ ] `compile-doctrine.py --dry-run` now exits `0`.
- [ ] `<docs_dir>/log.md` has a new top-of-file `adr |` entry.
- [ ] If any domain rendered BROKEN, the user was told to run `reconcile-signoff` — compile records the state, it does not resolve it.

## Red flags — STOP and reconsider

- About to hand-edit `<docs_dir>/adrs/doctrine/index.md` or `_meta.json` to "fix" a belief. NEVER — the next compile overwrites it. Fix the *input* (the ADR's `governs` block, the invariant text, or record a reconciliation), then re-compile.
- About to write or edit `<docs_dir>/adrs/doctrine/reconciliations.yml` from here. NEVER — the only write path for the reconciliation ledger is `reconcile-signoff` (via `signoff-reconciliation.py`). This skill only reads it.
- About to compile on top of a drifted summaries projection. STOP — run `summarize-adrs.py` first. Doctrine reads summaries as its rule source; compiling over the drift produces a doctrine that lies.
- About to loop the compile because the re-run dry-run is still dirty. NEVER loop — a second compile reads the same inputs and writes the same bytes. Non-convergence is a defect to surface, not to retry.
- About to report a `validation_errors` payload by regenerating. NEVER — a malformed `governs` entry or reconciliation ledger is BROKEN; regenerating cannot repair it.
- About to skip the log entry because "nothing changed". Even a no-op compile is auditable — log it.

## Rationalization table

| Excuse | Reality |
|---|---|
| "I'll just edit `doctrine/index.md` to mark the domain believed." | The next compile deletes it. The domain is BROKEN because a pairing is un-adjudicated, digest-stale, or a collision — reconcile it via `reconcile-signoff`. |
| "The summaries gate is red but doctrine's diff looks fine — compile anyway." | Doctrine's rule text IS the summaries projection. Compiling over drifted summaries ships a stale belief. Regenerate summaries first. |
| "I'll add the reconciliation verdict right here in the ledger." | The reconciliation ledger has ONE write path — `reconcile-signoff`. A hand-edit red-gates the drift check and skips the human sign-off. |
| "Exit 1 means the doctrine is broken." | Exit 1 is either drift (re-compile) or a `validation_errors` payload (fix the input). Neither is "the architecture is broken". |
| "The re-run is still dirty; one more compile will settle it." | It will not — same inputs, same bytes. Non-convergence is a bug to report. |

## Common mistakes

- **Hand-editing the doctrine tree.** Everything under `adrs/doctrine/` is regenerated; the only durable inputs are the summaries projection, the invariant ledger, and the reconciliation ledger.
- **Confusing compile with sign-off.** This skill regenerates the projection. Recording a reconciliation verdict is `reconcile-signoff`; the two are different acts with different write sets.
- **Compiling without checking summaries first.** A stale summaries projection silently makes doctrine stale. Step 1 is not optional.
- **Treating a `validation_errors` payload as drift.** Drift is fixed by re-compiling; a validation error is fixed by repairing the named input.
- **Forgetting doctrine has no timestamps.** Provenance is the input digests in `_meta.json` plus git plus the `log.md` entry, not an in-tree date.
- **Looping on non-convergence.** One compile, one confirming dry-run. A still-dirty result is surfaced, never retried.

## See also

- `reconcile-signoff` — the single human sign-off write path for `reconciliations.yml`; it re-compiles doctrine inside its own write set. Doctrine's BROKEN domains are resolved there, not here.
- `summarize-adrs.py` (the summaries projection) — doctrine's rule source; compile this skill only after the summaries drift gate is clean.
- `check-drift` — runs every enrolled drift gate, including `compile-doctrine.py --dry-run`, in one read-only pass.
- `derive-arch` — the sibling regenerative surface (the arch spine) and its dry-run drift check; doctrine is a standalone output, not a fifth arch spine file.
- `query-docs` / the `librarian` — read the current belief from `adrs/doctrine/index.md`; this skill builds it, they answer from it.
