---
name: iterate
description: "Plan a non-architectural fix through diagnosis, council review, implementation, and independent review without an ADR."
metadata:
  tags: "promptbooks, workflow, cycle, iterate, fix"
  bundles: "crux-docs"
  risk_level: "low"
  triggers: "iterate on X | start an iterate cycle | iterate-cycle for X | remediate X | fix X with rigor"
  routing_note: "Non-architectural reactive work (bug / drift / refinement) — `dev-cycle` rigor with a **verify** module instead of an ADR (`cycle_kind: verify`, ≥ 13 prompts). See `iterate/SKILL.md`."
---

# Iterate

For new Crux Flow work, resolve `.crux-flow.yml` first and use the `flow`
skill for aggressive, balanced, or thorough. This upstream verify cycle
continues to govern upstream mode and existing upstream cycle records.

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

An **iterate cycle** is one trip around crux's development loop for a
**non-architectural reactive change** — a bug fix, drift remediation, or a
refinement to existing behavior. It is `dev-cycle`'s rigor (multi-model council +
external review) **without an ADR**: there is no architectural decision to
record, so the ADR module is replaced by a **verify** module that produces a
*council-reviewed verified diagnosis* instead. This skill scaffolds the
promptbook that drives that trip — it does not run it (`run-promptbook` does).

An iterate
book is a **cycle book with `cycle_kind: verify`** — a single structured YAML
document (`format_version "1"`) whose cycle invariants are
**machine-enforced** by `validate-promptbook.py`'s cycle-coverage pass (the same
pass that now enforces `dev-cycle`'s `cycle_kind: adr` books). Assembly is
**list-concatenation of module YAML partials + sequential `n:` assignment**.

| Module | Prompts | `module_tag` | Source template |
|---|---|---|---|
| **Verify** — research & reproduce, council-review the diagnosis (5 dims incl. Security + the "this is actually architectural → use dev-cycle" escape verdict), address findings, commit-approach | 4 | `verify-{M}` | `${CRUX_PLUGIN_ROOT}/templates/cycle-module-verify.yaml` |
| **Dev** — derive plan, implement, quality gates, internal review | 4 | `dev-{M}` | `${CRUX_PLUGIN_ROOT}/templates/cycle-module-dev.yaml` |
| **Review** — external multi-dim review + multi-subagent security review + test/doc + dev-practice conformance audit, fix-loop | 3 | `review-{M}` | `${CRUX_PLUGIN_ROOT}/templates/cycle-module-review.yaml` |
| **Prep (fixed)** — changelog, docs, journal, PR draft | 1 | _none_ | inline (last-2 of `iterate-promptbook-template.yaml`) |
| **Summary (fixed)** — completion report + archive | 1 | _none_ | inline (last-1 of `iterate-promptbook-template.yaml`) |

The dev + review module partials are **reused unedited from `dev-cycle`** —
`iterate` substitutes their tokens. Notably the dev module's `{RULE_REFS}` token
(which has no rule citation to carry here) is substituted with **"the verified Diagnosis
(run notes) + the verify module's commit-approach journal entry."** The review
module's ADR-consistency clause gracefully no-ops (it still checks pre-existing
ADRs + `docs/AGENTS.md`).

Total prompts:

> **total_prompts = 4·V + 4·D + 3·R + 2**, where `V` = verify modules, `D` = dev
> modules, `R` = review modules.

A minimum cycle is `V=1, D=1, R=1` → **13 prompts** (the shape of
`${CRUX_PLUGIN_ROOT}/templates/iterate-promptbook-template.yaml`). Larger cycles are valid;
smaller ones are NOT.

### Three invariants the skill MUST enforce (machine-checked)

These are identical in spirit to `dev-cycle`'s and are **machine-enforced** by
`validate-promptbook.py`'s cycle-coverage pass — authoring MUST
produce a book that passes it:

1. **`total_prompts ≥ 13`.** A shorter sequence is not a cycle — use
   `author-promptbook`.
2. **Every verify module MUST have its council prompt** (ordinal 2). The verify
   module is atomic — you cannot splice in a "research" prompt without the
   council-review-and-commit sequence. The council reviews the *diagnosis*; that
   is the iterate cycle's load-bearing artifact (the ADR's analogue).
3. **Every artifact produced MUST have a review module** that closes its MUST-FIX
   findings. A cycle without ≥1 external review module ships unreviewed work.

Plus the cross-checks the pass enforces: `cycle_kind: verify` ⟷ `modules:
{verify, dev_loops, review_cycles}` ⟷ only `verify-`/`dev-`/`review-` module tags
(**adr/verify mutual exclusion** — an iterate book never carries `adr-` tags),
and exactly 2 untagged (prep + summary) prompts.

## When to use

- User says: "iterate on X", "start an iterate cycle", "iterate-cycle for X",
  "remediate X", "fix X" (with rigor / "the right way"), "this bug needs a proper
  cycle".
- A bug, regression, or drift has surfaced that deserves council + review rigor
  (it's reachable/security-adjacent, or the root cause is non-obvious) but does
  **not** introduce a new architectural decision.

Do **NOT** use this skill for:
- **Net-new or architectural work** — a feature, a new contract, a decision worth
  recording. Use `dev-cycle` (it has the ADR module). If an iterate cycle's
  verify council concludes the work is actually architectural, it STOPS and
  routes the user to `dev-cycle` (the escape verdict).
- **A reversible fix you can bound by declared paths and finish in one implement
  prompt** — that is `patch-cycle`, the third tier: five phases (verify, plan,
  implement, review, summary), one prompt each, with a blast radius declared at
  authoring and checked at archive. It still pays a council and a review. The
  13-prompt floor and the `4N+4M+3K+2` formula here are unchanged by it.
- **A defect that passes `fix-directly`'s sizing test** — its files nameable
  now, a failing test writable before the fix, no contract changed, one
  instance. That is `fix-directly`: no book, no council, no PB number. A
  security label on the defect sets its priority, not its size.
- **A bespoke plan that needs no gate** — `author-promptbook` (no enforced rigor).
- Authoring a non-cycle plan (`author-promptbook`) or running a book
  (`run-promptbook`).

### The cycle taxonomy (the routing question)

> *Fixing/refining something that exists, at a size that needs room* →
> **`iterate`**. *Deciding something new / architectural* → **`dev-cycle`**. *A
> reversible fix bounded by declared paths and done in one implement prompt* →
> **`patch-cycle`**. *A defect whose files, failing test, and unchanged
> contracts you can name now* → **`fix-directly`** (no book). *A bespoke plan
> that needs no gate* → **`author-promptbook`**.

## Inputs

- **Required**: a working title and a one-paragraph goal describing the issue
  (the bug / drift / refinement). The goal must be a real description — Prompt 1's
  research agents have no conversation context. It opens with three
  statements — the Outcome (what improves for the affected user once the
  issue is resolved), the Evidence (what would demonstrate that improvement),
  and the Constraint (what the fix must preserve) — as prose inside the same
  paragraph: no new field, no form, no gate.
- **Optional module counts** (default each = 1):
  - `--verifies V` (V ≥ 1) — e.g. two verify modules for two distinct issues.
  - `--dev-loops D` (D ≥ 1) — e.g. two when the fix splits cleanly.
  - `--review-cycles R` (R ≥ 1) — e.g. two for a mid-cycle review.
- **Optional** `--deep-review` — forces the verify council's deeper review on
  round 1 (same flag semantics as `dev-cycle`).

When called interactively (no flags), elicit counts:

> "This iterate cycle will include at least one verify module, one dev loop, and
> one review cycle (= 13 prompts minimum). Anticipate any more? Module counts
> (verifies / dev loops / review cycles)? [default: 1 / 1 / 1] And does the
> goal state the Outcome, the Evidence, and the Constraint?"

## The pipeline

### 0. Resolve per-repo configuration (.crux)

Run `python3 "${CRUX_PLUGIN_ROOT}/scripts/crux-config.py"` from the repo root (or pass `--repo-root <repo-root>`), and confirm the returned `repo_root` is the repo you are operating in — `source: "discovery:<dir>"` with an unexpected `repo_root` means you resolved the wrong directory, not that no config exists. On exit 1, **STOP** and surface the `{"error": ...}` payload — never fall back to defaults. Use the returned `docs_dir` wherever this skill says `docs/` (per the docs/AGENTS.md §14 normative definition clause). When `artifact_prefix` is non-empty, format the newly allocated book id with it (e.g. `CRX-PB-0040`) — the `NNNN` still comes from the manifest counter exactly as below; the prefix only changes the formatting.

### 1. Confirm inputs

Confirm title, goal, and the three module counts (`V`, `D`, `R`). Do not allocate
an id until all are locked. Compute `total_prompts = 4V + 4D + 3R + 2`. If
`V < 1` OR `D < 1` OR `R < 1` — refuse (each enforces a load-bearing invariant).
If `total_prompts < 13` — refuse (defense-in-depth).

### 2. Allocate id (write-first ordering)

Read `docs/manifest.yml`; take `promptbook.next_number`; format `PB-NNNN`. **Do
NOT increment yet** — write the book file first (step 6) and validate it (step 7),
then increment (step 8). Numbers are never reused.

### 3. Slug

Kebab-case from the title, ASCII, ≤50 chars. Filename:
`docs/promptbooks/active/PB-NNNN-<slug>.yaml`. Run dir:
`docs/promptbooks/runs/PB-NNNN-<slug>/`.

### 4. Assemble the prompt sequence

Concatenate module YAML prompt-lists + assign sequential `n:` — NOT text-splicing.
When `V=D=R=1`, copy `${CRUX_PLUGIN_ROOT}/templates/iterate-promptbook-template.yaml` (the
full 13-prompt skeleton) and do only token substitution. When any count > 1:

1. Start from the canonical template's top-level keys (`format_version`, `id`,
   `title`, `status`, `created_at`, `total_prompts`, `current_run`,
   `current_prompt`, `forked_from`, `tags`, `cycle_kind`, `modules`, `goal`,
   `strategy`, `run_autonomy`) — everything except `prompts:`, which you rebuild.
2. Build `prompts:` by concatenating: all verify modules
   (`cycle-module-verify.yaml`, `module_tag: verify-i`, substitute `{M}`→`#i`/`""`
   and `{ISSUE}`), then all dev modules (`cycle-module-dev.yaml`, `dev-i`,
   substitute `{M}`, `{SCOPE}`, and **`{RULE_REFS}`→"the verified Diagnosis (run
   notes) + the verify module's commit-approach journal entry"**), then all review
   modules (`cycle-module-review.yaml`, `review-i`, substitute `{M}`, `{SCOPE}`),
   then the two fixed prep + summary prompts (no `module_tag`).
3. Assign `n: 1..total_prompts` across the concatenated list.

### 5. Substitute the top-level YAML keys and goal

Set the book's top-level YAML keys (flat top-level keys in the structured
document — NOT Markdown frontmatter + body):

```yaml
format_version: "1"
id: PB-NNNN
title: "Iterate: <user title>"
status: active
created_at: <today>
total_prompts: <4V + 4D + 3R + 2>
current_run: null
current_prompt: null
forked_from: null
tags: [cycle, workflow, iterate]
cycle_kind: verify
modules: { verify: <V>, dev_loops: <D>, review_cycles: <R> }
goal: |
  <the user's goal paragraph>
# strategy + run_autonomy: carry from the canonical template (run_autonomy MUST
# be present and reference docs/AGENTS.md §11).
```

`tags` MUST contain `cycle` (the schema's `tags:cycle → required: modules`
coupling + cycle staleness detection) AND `cycle_kind: verify` (the authoritative
discriminator). Long-form fields are literal block scalars (`|`).

### 6. Write the book

Write the assembled YAML document to
`docs/promptbooks/active/PB-NNNN-<slug>.yaml` (new-format `.yaml`; this matches
`author-promptbook`'s / `dev-cycle`'s emit path). If the write fails, **STOP** —
do not increment the counter. The number stays unallocated.

### 7. Validate the book — REFUSE on failure (the gate before ALL side effects)

This is the **validation gate**: a discrete step that visibly precedes every side
effect below (manifest increment, index regen, `docs/index.md` bump, log append).
Nothing in steps 8–11 runs until this exits 0.

```bash
uv run "${CRUX_PLUGIN_ROOT}/scripts/validate-promptbook.py" --kind promptbook docs/promptbooks/active/PB-NNNN-<slug>.yaml
```

(In a source checkout, where `${CRUX_PLUGIN_ROOT}` is unset, substitute the checkout's `crux/` directory — source checkout: `<checkout>/crux/scripts/validate-promptbook.py`.)

This runs the **cycle-coverage pass** — it enforces the formula, the
`module_tag` counts vs `modules:`, the per-module prompt counts (ordinal
contract), adr/verify mutual exclusion, and the ≥13 floor. **Exit 0 = clean;
non-zero → REFUSE: fix the assembled document and re-validate BEFORE incrementing
the manifest (step 8), regenerating the index (step 9), updating `docs/index.md`
(step 10), or logging (step 11).** (Identical validation to a `dev-cycle` book —
both are cycle-kind promptbooks through the same validator.)

### 8. Increment `manifest.yml` atomically

Re-read `manifest.yml`, confirm `promptbook.next_number` is unchanged, increment,
write back (write-then-increment, matches `author-promptbook`/`dev-cycle`). If it
changed, another invocation raced — STOP, retry.

### 9. Regenerate `docs/promptbooks/index.md`

Walk active/runs/archive (extension-agnostic); rebuild the index. The new book
appears in Active at `0/<total_prompts> (0%)`.

### 10. Update `docs/index.md`

Bump `docs/index.md` `## Promptbooks (X active, …)` count by 1; update
`_Last updated:` to today.

### 11. Append to `docs/log.md`

```
## [YYYY-MM-DD] promptbook | authored PB-NNNN-<slug> (iterate)
```

Body: id, title, `total_prompts`, modules `(V×verify, D×dev, R×review)`, note
`iterate (cycle_kind: verify): assembled from modular templates`.

## Verification checklist

- [ ] `docs/manifest.yml` `promptbook.next_number` incremented and persisted.
- [ ] `docs/promptbooks/active/PB-NNNN-<slug>.yaml` exists with `format_version:
      "1"`, `cycle_kind: verify`, `tags` containing `cycle`.
- [ ] `validate-promptbook.py --kind promptbook` exits **0**. Its cycle-coverage
      pass already machine-guarantees the `4V + 4D + 3R + 2` formula, the ≥13
      floor, sequential `prompts[].n`, `modules:` ↔ `module_tag`-count
      agreement, adr/verify mutual exclusion, exactly-2-untagged, and the
      per-module prompt counts that **positionally** guarantee the ordinal
      contract (for template-assembled modules — the pass checks size and
      contiguity, never prompt content). Do not re-derive those by hand — exit 0
      IS the confirmation, **unless the book sets `cycle_grandfathered: true`,
      which short-circuits the whole pass**; a grandfathered book gets no
      cycle-coverage checking and still exits 0.
- [ ] Top-level `run_autonomy` present and references `docs/AGENTS.md` §11.
      (NOT covered by the validator's cycle-coverage pass — check it yourself.)
- [ ] `docs/promptbooks/index.md` lists the book `0/<T> (0%)`; `docs/index.md`
      Promptbooks count bumped; `docs/log.md` has the `promptbook | authored …
      (iterate)` entry.

## Red flags — STOP and reconsider

- About to author an iterate cycle for **net-new or architectural** work. That's
  `dev-cycle`'s job (it records the decision as an ADR). If unsure, that's exactly
  what the verify council's escape verdict adjudicates at run time — but don't
  author an iterate cycle for work you already know is architectural.
- About to author with `total_prompts < 13`, or with `V < 1` / `D < 1` / `R < 1`.
  Refuse — the floor and the per-module minimums are the contract.
- About to set `cycle_kind: adr` or emit `adr-` module tags. NEVER — an iterate
  book is `cycle_kind: verify` with `verify-` tags. adr/verify are mutually
  exclusive (the cycle-coverage pass rejects a mix).
- About to skip `validate-promptbook.py` and regenerate the index against an
  unvalidated book. Refuse — validate first; the cycle-coverage pass is the same
  gate `dev-cycle` books pass.
- About to splice a partial verify module (research without council, or council
  without the commit-approach prompt). The module is atomic; the council prompt
  is non-optional.
- About to increment `manifest.yml` before the book file lands. Write-then-
  increment.
- Mid-run (once `run-promptbook` is driving it): about to pause to confirm a step
  the prompt authorizes. Don't — the plan is the authorization (`docs/AGENTS.md`
  §11). The genuine stops are the module escalation loops, the verify council's
  architectural-escape verdict, and irreversible/outward-facing actions. When an
  escalation traces to a missing capability rather than a genuine disagreement,
  `forge-skill` is a sanctioned response the escalation may name.

## Rationalization table

| Excuse | Reality |
|---|---|
| "This bug is architectural-ish; I'll just iterate it." | If it needs a decision, it needs `dev-cycle` (an ADR). The verify council exists to catch this — but don't knowingly mis-route. |
| "The fix is tiny; I'll cut to 8 prompts." | < 13 isn't a cycle. A defect that passes the sizing test is `fix-directly`; a bounded fix that wants the gates is `patch-cycle`. The rigor is proportional to the work. |
| "No ADR means I can skip the council." | The council is the whole point — it reviews the verified *diagnosis* instead of an ADR. Invariant #2. |
| "I'll tag it `cycle_kind: adr` to reuse the dev-cycle template." | An iterate book is `verify`-kind. Mixing adr/verify fails the cycle-coverage pass. |
| "I'll skip validate-promptbook — it's a cycle like any other." | Exactly — so it must pass the SAME cycle-coverage pass. Validate; refuse on non-zero. |

## Common mistakes

These parallel `dev-cycle`'s Common-mistakes section — the two skills share most
of their pipeline, so most of `dev-cycle`'s pitfalls (wrong counter, off-by-one
on the `+2`, non-sequential `n:`, forgetting the `modules:` block or the
`tags: [cycle, ...]`, hand-editing the module templates, dropping a `module_tag`,
omitting `run_autonomy`) apply verbatim here. The `iterate`-specific ones:

- **Emitting an `adr-` `module_tag` (or `cycle_kind: adr`)**: an iterate book is
  `cycle_kind: verify` and carries ONLY `verify-`/`dev-`/`review-` tags. adr and
  verify are **mutually exclusive** — the cycle-coverage pass rejects a book that
  mixes them. The verify module's prompts are tagged `verify-{M}`, never `adr-{M}`.
  This is the single most likely drift when reusing the `dev-cycle` mental model.
- **Substituting `{RULE_REFS}` literally** (leaving the brace token in a dev
  module's `prompt`/`purpose`, or pointing it at a rule that doesn't exist): the
  reused `cycle-module-dev.yaml` carries a `{RULE_REFS}` token because it is shared
  unedited with `dev-cycle`. In an iterate cycle there IS no ADR and no new rule —
  substitute it with **"the verified Diagnosis (run notes) + the verify module's
  commit-approach journal entry"** (the iterate analogue), never with a literal
  `{RULE_REFS}`, never with an invented `rule:<slug>` citation, and never with an
  invented `ADR-NNNN`.
- **`modules.verify` vs `modules.adrs` key mismatch**: an iterate book's `modules:`
  block is keyed `{ verify: V, dev_loops: D, review_cycles: R }` — the verify-count
  key is **`verify`**, NOT `adrs` (that key belongs to `dev-cycle`'s `cycle_kind:
  adr` books). Writing `modules: { adrs: V, ... }` on a `cycle_kind: verify` book
  fails the cycle-coverage cross-check (the `modules:` keys must match the
  `cycle_kind` and the emitted `module_tag` prefixes).
- **Setting `total_prompts` from the wrong formula**: it is `4V + 4D + 3R + 2`
  (verify modules are 4 prompts each, like ADR modules) — the same arithmetic shape
  as `dev-cycle`'s `4N + 4M + 3K + 2`, just with the verify count in the first term.
- **Skipping the verify council prompt (ordinal 2)**: the verify module is atomic —
  research-then-council-then-commit. Splicing in a research prompt without its
  council pass breaks invariant #2 and the per-module ordinal contract.

## See also

- `dev-cycle` — the architectural counterpart (ADR module instead of verify); net-new work.
- `author-promptbook` — generic multi-prompt plans with no enforced council/review.
- `run-promptbook` — advances the iterate cycle through its prompts.
- `archive-promptbook` — closes the completed book (the Summary prompt invokes it).
- `council` / `srde` — the verify module's diagnosis-review (and split-verdict deepening).
- `log-work` — the verify module's commit-approach entry + the prep journal entry.
- `validate-promptbook.py` + `${CRUX_PLUGIN_ROOT}/schemas/promptbook.schema.json` — the cycle-coverage validation that an iterate book MUST pass.
- Templates: `iterate-promptbook-template.yaml` (canonical 1×1×1), `cycle-module-verify.yaml`, and the reused `cycle-module-dev.yaml` / `cycle-module-review.yaml`.
