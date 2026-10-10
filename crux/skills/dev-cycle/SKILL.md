---
name: dev-cycle
description: "Plan an architectural change or a replaceable implementation choice through council review, implementation, and independent review."
metadata:
  tags: "promptbooks, workflow, cycle, planning"
  bundles: "crux-docs"
  risk_level: "low"
  triggers: "start a cycle | new dev cycle | begin development cycle | cycle this feature | build a cycle promptbook | rigorous plan | do this the right way"
  routing_note: "Architectural work or significant implementation choices under unchanged constraints. Allocates next `PB-NNNN`; assembles a modular cycle book (≥ 13 prompts, kind adr or implementation) with council and independent review. See `dev-cycle/SKILL.md`."
---

# Cycle

For new Crux Flow work, resolve `.crux-flow.yml` first and use the `flow`
skill for aggressive, balanced, or thorough. This upstream cycle procedure
continues to govern upstream mode and existing upstream cycle records. Never
retroactively label a shorter fork workflow as this cycle.

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

A **cycle** is one trip around crux's development loop for a single
feature or change. This skill scaffolds the promptbook that drives that
trip — it does not run it. The resulting book is mutated by `run-promptbook`.

A cycle promptbook is **assembled from modules**, not stamped from a
fixed-length template. The book is a **single structured YAML document**
(`format_version "2"` for newly authored cycles) and the
templates are **YAML partials**: each module template is a list of prompt
mappings (`{title, purpose, prompt, expected_output, side_effects, module_tag}`),
and the canonical template is a full YAML book skeleton. Assembly is
**list-concatenation + sequential `n:` assignment**, not text-splice +
heading-renumber (step 4).

| Module | Prompts per instance | `module_tag` | Source template |
|---|---|---|---|
| **ADR module** — propose, council (5 dims incl. Security), address findings, accept | 4 | `adr-{M}` | `${CRUX_PLUGIN_ROOT}/templates/cycle-module-adr.yaml` |
| **Implementation module** — author reasoning, council, findings, exact-revision close; no ADR lifecycle | 4 | `implementation-{M}` | `${CRUX_PLUGIN_ROOT}/templates/cycle-module-implementation.yaml` |
| **Dev module** — derive plan, implement, quality gates (recorded with command+result tokens), internal review | 4 | `dev-{M}` | `${CRUX_PLUGIN_ROOT}/templates/cycle-module-dev.yaml` |
| **Review module** — external multi-dim review + dedicated multi-subagent security review, test/docs + dev-practice conformance audit, fix-loop | 3 | `review-{M}` | `${CRUX_PLUGIN_ROOT}/templates/cycle-module-review.yaml` |
| **Prep (fixed)** — changelog, docs, journal, result record, PR draft | 1 | _none_ | inline (last-2 of canonical template) |
| **Summary (fixed)** — completion report | 1 | _none_ | inline (last-1 of canonical template) |

Every prompt in an ADR module carries `module_tag: adr-{M}` (where `{M}` is the
1-based occurrence of that module within the book — first ADR module → `adr-1`,
second → `adr-2`); dev modules → `dev-{M}`; review modules → `review-{M}`. The
fixed prep + summary prompts carry **no** `module_tag`. The `module_tag` format
is `^(adr|implementation|dev|review)-[1-9]\d*$`; it replaces the retired
`<!-- MODULE BOUNDARY -->` HTML comments and makes the three invariants below
checkable by **counting tagged elements** rather than scanning headings.

The ADR-council Security dimension, the review-module multi-subagent security review + dev-practice conformance audit, and the explicit run-autonomy codification all fold into the existing modules and add no prompts. The formulas below differ by cycle kind; **the three invariants below hold for every kind**.

Total prompts in newly authored module cycles:

- Architectural kind: `4(A+I)+4D+3R+2`, with A≥1 and V=0. Format-one books use `4N+4M+3K+2` instead.
- Implementation kind: `4I+4D+3R+2`, with I≥1 and A=V=0.
- Verify kind belongs to `iterate`: `4V+4D+3R+2`, with V≥1 and A=I=0.

All counts are nonnegative integers. D and R are at least one. Each cycle has
at least 13 prompts and exactly two final untagged prompts. Patch retains five phases.
A replaceable implementation under unchanged architecture uses implementation kind,
without an ADR. A new enduring constraint uses architectural kind. A combined
architectural cycle may include implementation modules before development.
Earlier Implementation Decisions remain readable without revocation when replaced.
Format-one books and runs retain their original interpretation and are never upgraded in place.

### Three invariants the cycle skill MUST enforce

1. **`total_prompts ≥ 13`.** A cycle shorter than the canonical shape is a
   contract violation. Refuse to author and tell the user to either accept
   the minimum or use `author-promptbook` for a non-cycle plan.
2. **Every ADR or formal Implementation Decision proposed in the cycle MUST have a paired council-approval
   prompt.** ADR modules are atomic — you cannot splice in an "ADR creation"
   prompt without the council-review-and-accept sequence that follows it.
   Council deliberation runs only through the council runner, and its council
   record is the only evidence a council gate accepts.[^council] Independent
   review is a reviewer's examination, and its reviewer report is the only
   evidence an independent-review gate accepts.[^review] Neither satisfies the
   other's gate. The dev module's quality gates and the review module's
   external reviewers compose with the council step and never satisfy its gate.
   That's the whole point of the cycle's rigor. A cycle that creates an
   ADR without sending it through the council violates the contract.
3. **Every artifact produced (code, docs, ADR, migration) MUST have a
   review cycle that closes its MUST-FIX findings.** Each dev module
   includes an internal review; the external review module(s) are the
   independent check. A cycle without at least one external review module
   is shipping unreviewed artifacts and violates the contract.

These invariants are checked by the cycle skill at authoring time AND by
`audit-docs` against existing cycle books (a CHK-CYCLE-* group catches
drift if the book is later edited to remove a module). The check now operates
on the structured `prompts:` list: it **counts `module_tag`-tagged elements**
(`^adr-` for invariant 2, `^review-` for invariant 3, `^dev-` for the dev
module's internal-review floor) rather than scanning `### Prompt N` headings.

### Loop-back behavior is preserved

Each module retains its 3-round escalation: after 3 unsuccessful internal
loops (council disagreement, quality-gate failures, review fix-loop
non-convergence, or the preflight retry sequence: a third preflight refusal at
one convening prompt), the conductor STOPS and escalates to the user. Multi-module
cycles don't relax this — each module enforces its own escalation
independently. When an escalation traces to a missing capability rather than a
genuine disagreement, `forge-skill` is a sanctioned response the escalation may
name.

A council round's `--round` is the number of council records with outcome `ran`
already in the module, plus one. A valid adjudicator refutation record
fills place 3, and a could-not-run record takes no place, so you reconvene with
the same number after fixing its cause. `run-council.py` refuses a mismatch
before any call. The council runner commits the attempt record and the council record itself.
The gate reads only a committed record. A council that cannot run leaves a
committed could-not-run record; advance `--outcome blocked` with it attached. A
preflight refusal the conductor may repair writes no council record: repair its
input and run again. The third preflight refusal at one convening prompt takes
the escalation-loop stop. On exit 2 whose stderr names `timeout`, or names
outside work the commit moved, report a contradicted-premise stop first: the
owner restores the set-aside work, then removes a stale `index.lock`. Then, as
on every other exit 2, run the process check and the lock probe,
then `run-council.py --recover <run> --prompt <n>`, and never convene another
round over a claimed attempt. A council runner that ended without an exit code,
or with a code other than 0, 1 and 2, gets the same recovery: never convene
another round until recovery reports. Never record a witness to repair a
preflight refusal: a subject with no witness taken at its write goes to the
owner.
`run-promptbook`'s `references/gates.md` holds the repair, the run-work witness
and the recovery steps.

The canonical 1×ADR + 1×dev + 1×review composition lives at
`${CRUX_PLUGIN_ROOT}/templates/cycle-promptbook-template.yaml` for reference / for the
default authoring path. When the user requests additional modules, the
skill assembles from the per-module YAML partials instead.


Every initial or revised formal revision or migration-batch subject write records
its bytes immediately, before its commit:

```bash
uv run "${CRUX_PLUGIN_ROOT}/scripts/run-work-witness.py" record <run-RUN-NNN.yaml> --prompt <n> --path <subject-path>
```

Never create a witness after a refusal. Both book formats use the current
attempt-aware gate. New formal closes retain context three/profile four;
historical context two/profile three and context one/profile two remain immutable and replay-only.
Finish any open Git merge or other sequence before council. Runner and recovery
own attempt/result commits. Follow `run-promptbook`'s `references/gates.md` for
authorized preflight repair, the third-refusal stop, process/lock checks and
request-free recovery. Persistence failure permits recovery, never repeat deliberation.

## Orchestration rules a run follows

The commander, dev-lead, developer and historian agent definitions, the cycle module templates and `run-promptbook`'s `references/gates.md` carry these rules. This section summarizes each rule; those sources carry the full text.

- **Tester.** One designated tester runs the full suite; the dev-lead may run its own; one full suite runs at a time. The tester may reuse a matching passing record for an unchanged tree.
- **Commit lane.** A run has one commit lane. The tester's window runs from the tester's dispatch until the tester returns. The agent that dispatched the tester holds the window. While it is open, that agent commits nothing to the main checkout, convenes no council, runs no live-tree tool, and dispatches no agent that does.
- **Hand-back.** A hand-back with work in flight states that first, then lists each running dispatch with its result file.
- **Worktree base.** Developer worktrees start from the run's current HEAD or are rebased before integration.
- **Owner exception.** An owner-exception record is written only from the owner's own instruction. The owner's instruction is a decision the owner stated in the owner's own message in the session. An agent report, a result file, a run note or a council verdict is never an owner's instruction.

## When to use

- User says: "start a cycle", "new dev cycle", "cycle this", "begin a cycle
  for <feature>", "build a cycle promptbook".
- After alignment on what a feature is (the goal) but before any planning
  or coding — the cycle's first ADR module owns architectural commitment.
- When the user wants the orchestration recorded as a tracked artifact so
  the work can be resumed across sessions.
- When the user signals scope that exceeds a single decision or single
  implementation pass (e.g., "this needs two ADRs", "this has a backend
  step then a UI step") — ask which modules to multiply.

Do **not** use this skill for:
- A non-architectural fix — a bug, drift, or a refinement to existing behavior.
  Use `iterate`, which swaps the ADR module for a council-reviewed diagnosis.
- **A small reversible non-architectural change you can bound by declared paths
  and finish in one implement prompt.** That is `patch-cycle`, the third tier:
  five phases (verify, plan, implement, review, summary), one prompt each, with a
  blast radius declared at authoring and machine-checked at archive. It still pays
  a council and a review. Architectural work still comes here, and this skill's
  `4*(N+I)+4M+3K+2` formula and 13-prompt floor are unchanged by that tier's existence
  — work needing an ADR is not a patch.
- A defect that passes `fix-directly`'s sizing test — nameable files, a failing
  test before the fix, no contract changed. Use `fix-directly`: no book, no
  council, one commit and one `log-work` entry.
- Changing a cycle book's plan mid-run. Abandon the run
  (`run-promptbook abandon PB-NNNN --reason "<why>"`) and author a successor book
  that cites its predecessor in prose — the mid-run fork path no longer exists.
- Authoring a non-cycle promptbook. Use `author-promptbook` for general
  multi-prompt plans without the modular ADR/dev/review structure.
- Running an existing cycle book. Use `run-promptbook`.
- Building an `adr`-kind cycle with fewer than 13 prompts. The floor is the
  contract.

## Inputs

- **Required**: a working title and a one-paragraph goal describing the
  feature/change. The goal opens with three statements — the Outcome (what
  improves for the affected user when the work is done), the Evidence (what
  would demonstrate that improvement), and the Constraint (what the change
  must preserve). They are prose inside this paragraph: no new field, no
  form, no gate.
- **Optional**: initial tags beyond the default `[cycle, workflow, feature]`.
- **Optional**: a hint at the affected scope (file paths, modules) — this
  goes into the Goal paragraph; the planning team in Prompt 1 will refine.
- **Cycle kind:** `adr` for enduring architectural constraints; `implementation`
  for a replaceable approach whose reasoning must survive retrieval, under
  unchanged constraints.
- **Optional module counts** (default architectural A=1,I=0,D=R=1;
  implementation A=0,I=1,D=R=1):
  - `--adrs N` — ADR modules; N≥1 only for architectural kind.
  - `--implementations I` — dedicated implementation review modules.
  - `--dev-loops M` — number of dev modules to include (M ≥ 1).
  - `--review-cycles K` — number of review modules to include (K ≥ 1).
- **Optional** `--deep-review` — for ADRs already known to be architecturally
  significant, round 1's council also receives the full ADR graph as
  `--subject` files: the index, every cited ADR and `docs/AGENTS.md` for the
  §11 invariants. It is a council-runner round,
  counted like any other. It runs no srde and dispatches no `Agent`. Does not
  change the prompt count or formula. Record the flag when you assemble the
  book: end its `strategy` with the sentence "This book was authored with
  `--deep-review`." The council prompt reads the flag there.

Resolve kind and counts from the goal before allocating a number. A choice that
changes architecture needs ADR review. A choice that replaces an implementation
under unchanged constraints needs implementation review. Ask for missing input
only when the goal does not settle it; authorization to execute an approved plan
requires no per-step confirmation.

If the user gives only a title, ask for the goal paragraph before allocating
an id, and require it to state the Outcome, the Evidence, and the Constraint. A
real goal must exist — placeholder goals leak into every downstream prompt and
degrade agent quality.

## The pipeline

### 0. Resolve per-repo configuration (.crux)

Run `python3 "${CRUX_PLUGIN_ROOT}/scripts/crux-config.py"` from the repo root (or pass `--repo-root <repo-root>`), and confirm the returned `repo_root` is the repo you are operating in — `source: "discovery:<dir>"` with an unexpected `repo_root` means you resolved the wrong directory, not that no config exists. On exit 1, **STOP** and surface the `{"error": ...}` payload — never fall back to defaults. Use the returned `docs_dir` wherever this skill says `docs/` (per the docs/AGENTS.md §14 normative definition clause). When `artifact_prefix` is non-empty, format the newly allocated book id with it (e.g. `CRX-PB-0040`) — the `NNNN` still comes from the manifest counter exactly as below; the prefix only changes the formatting.

### 1. Confirm inputs

Resolve title, goal, cycle kind, counts and formal slots before allocating an id.
Compute the applicable formula above. Reject incompatible counts, D<1, R<1,
a total below 13, or missing slots for dedicated implementation modules.

### 2. Allocate id (write-first ordering, matches `author-promptbook`)

- Read `docs/manifest.yml`. Take `promptbook.next_number`.
- Format as `PB-NNNN` zero-padded to 4 digits.
- **Do NOT increment the manifest yet.** Write the book file first (step 6) and
  validate it (step 7). Only after the file is durably on disk and validated,
  re-read the manifest, increment, write back (step 8).
- Numbers are NEVER reused. A failed authoring leaves the counter intact
  so the next successful run uses the same number.

### 3. Slug

Kebab-case derived from the title. ASCII only. Truncate to ~50 chars.
Disambiguate collisions with `-2`, `-3`. The slug appears in:
- Filename: `docs/promptbooks/active/PB-NNNN-<slug>.yaml` (new-format `.yaml`
  — this skill is an emitter and ALWAYS writes new-format `.yaml`).
- Run-snapshot directory: `docs/promptbooks/runs/PB-NNNN-<slug>/`

### 4. Assemble the prompt sequence

The book is a **single structured YAML document** (per
`promptbook.schema.json`). Assembly is
**list-concatenation of the module YAML prompt-lists + sequential `n:`
assignment** — NOT text-splicing markdown and regex-renumbering `### Prompt N`
headings. The module templates are YAML partials; each is a list of prompt
mappings (`{title, purpose, prompt, expected_output, side_effects,
module_tag}`).

When `N=1, I=0, M=1, K=1` (the architectural default), the assembly is trivial: copy
`${CRUX_PLUGIN_ROOT}/templates/cycle-promptbook-template.yaml` (the full YAML book
skeleton — already a complete 13-prompt `prompts:` list with `n: 1..13`,
`module_tag`s `adr-1`/`dev-1`/`review-1`, and the untagged prep + summary).
This is the "minimum cycle" path; only token substitution and any formal-slot declaration (step 5) remain.

For implementation kind, any implementation module, or larger counts, build the book by composing the YAML partials:

1. Start from the canonical template's **top-level keys** (`format_version`,
   `id`, `title`, `status`, `created_at`, `total_prompts`, `current_run`,
   `current_prompt`, `forked_from`, `tags`, `cycle_kind`, `modules`, `implementation_slots`, `goal`, `strategy`,
   `run_autonomy`) — everything except its `prompts:` list, which you rebuild.
2. **Build the `prompts:` list by concatenating module prompt-lists in order:**
   all ADR modules, then all implementation modules, then all dev modules, then all review modules, then the two
   fixed prep + summary prompts (lifted from the canonical template's last two
   `prompts:` elements — they carry **no** `module_tag`).
   - For ADR module instance i = 1..N: load `cycle-module-adr.yaml`, set
     `module_tag: adr-i` on each of its 4 prompts, substitute `{M}` with `#i`
     (or `""` if N=1) and `{ADR_TOPIC}` with the per-ADR topic elicited from
     the user (single-ADR cycles use the cycle goal verbatim).
   - For implementation instance i = 1..I: load `cycle-module-implementation.yaml`,
     set `module_tag: implementation-i`, and substitute its topic and label.
     Declare each slot's local slug, full scope and direct constraint handles.
     For implementation kind, omit ADR modules and keep A=V=0.

   - For dev module instance i = 1..M: load `cycle-module-dev.yaml`, set
     `module_tag: dev-i` on each of its 4 prompts, substitute `{M}` with `#i`
     (or `""` if M=1), `{SCOPE}` elicited from the user, and `{RULE_REFS}`
     defaulting to the `rule:<slug>` citations of every ADR accepted in this
     cycle — one per `governs` entry, the slug being the part of the handle
     after the slash (the conductor reads them from the accepted ADRs; the ADR
     module records them in the run's Artifacts beside the id). For implementation
     kind, use existing architectural rule citations and pass the exact approved
     revision and binding separately. Never invent a rule from implementation reasoning.
   - For review module instance i = 1..K: load `cycle-module-review.yaml`, set
     `module_tag: review-i` on each of its 3 prompts, substitute `{M}` with `#i`
     (or `""` if K=1) and `{SCOPE}` elicited from the user (default: "full
     cycle diff").
3. **Assign sequential `n: 1..total_prompts`** across the fully concatenated
   list, in list order. The `n:` is the 1-based index+1 of each element; this
   replaces the old heading-renumber pass. Any intra-prompt cross-reference to
   another prompt by number (e.g. "return to Prompt 2", "re-run Prompt 7's
   gates") restates the now-absolute `n:` of the referenced element — in
   multi-module cycles these resolve via the `module_tag` (the council prompt of
   the same `adr-i`, the quality-gate prompt of the most-recent `dev-i`).

Token substitution happens **inside the scalar values** (the `|` block-scalar
`prompt`/`purpose`/`expected_output` strings and the `title`) BEFORE the value
is its final form — never restructure the YAML to inject a number. `{N}`/`{N+1}`
prompt-number tokens no longer exist in the YAML partials (numbering is `n:`,
assigned here); only `{M}`/`{ADR_TOPIC}`/`{IMPLEMENTATION_TOPIC}`/`{SCOPE}`/`{RULE_REFS}` remain.
A substituted `rule:<slug>` citation is text inside a block scalar; a `rule:`
token that opens a YAML line parses as a mapping key and breaks the book, so
never let a substitution start a line.

### 5. Substitute the top-level YAML keys and goal

Set the book's top-level YAML keys (these are flat top-level keys in the
structured document — NOT Markdown frontmatter + body):

```yaml
format_version: "2"
id: PB-NNNN
title: "Cycle: <user title>"
status: active
created_at: <today YYYY-MM-DD>
total_prompts: <4*(N+I) + 4M + 3K + 2>
current_run: null
current_prompt: null
forked_from: null
tags: [cycle, workflow, feature, <any user-supplied tags>]
cycle_kind: <adr or implementation>
modules:
  adrs: <N>
  implementations: <I>
  verify: 0
  dev_loops: <M>
  review_cycles: <K>
implementation_slots:
  - slot: implementation-1
    slug: <local-kebab-slug>
    scope: [<repo-relative-source-path>]
    constraint_refs: [<Accepted-architectural-handle>]
# Use [] when an architectural cycle contains no formal implementation decision.
```

An architectural `dev-cycle` book is **`cycle_kind: adr`** — the authoritative cycle-kind
discriminator (its non-architectural counterpart, the `iterate` skill, emits
`cycle_kind: verify`). The `modules:` block makes the multipliers machine-readable, and the
`4*(N+I)+4M+3K+2` formula + the three invariants are now **machine-enforced** by
`validate-promptbook.py`'s cycle-coverage pass — it cross-checks the
`module_tag` counts against `modules:`, the formula, the per-module prompt counts,
`cycle_kind`↔shape, and adr/verify mutual exclusion. **Step 7's validation MUST
exit 0** (the same cycle-coverage gate an `iterate` book passes); `audit-docs`
`CHK-PB-CYCLE` applies the same pass to existing books.

For implementation-only work, set `cycle_kind: implementation`, N=0 and I≥1.
Every dedicated implementation module requires exactly one declared slot, including
in combined architectural books. Scope, slug and constraint references freeze at start.
Later revision selection lives only in append-only run bindings and never in book fields.
Constraint handles resolve directly to active Accepted architecture, not doctrine labels
or historical aliases. When no architectural rule governs a slot's scope, declare it: set
`constraint_refs: []` and add `no_governing_constraint: {reason: "<one line, 1-500 characters>"}`
to the slot and to the decision record, identically. An empty or absent list without the
declaration stays refused, the declaration beside a non-empty list is refused, and a slot that
names a migration batch never carries it. The declaration is a reviewed claim. Council preflight,
the approval close and current eligibility each refuse it while a live Accepted rule's path scope
overlaps the slot's scope. Validate the book before starting the run: `validate-promptbook.py`
names each live Accepted rule whose path scope overlaps a declared-empty slot. After run start the
slot is frozen, so an overlap found at council preflight stops for the owner, whose remedy is to
abandon the run and author a successor book that cites the rule. The council question contains the
exact words "declared-empty constraint set". A live rule whose scope names no path is listed as
unchecked, not refused.
A formal decision is appropriate for a replaceable approach,
a material alternative, or a reviewed batch whose reasoning future retrieval needs.
It is unnecessary for every function or incidental choice. Direct fixes keep their path.
Never add governs or architectural lifecycle operations to implementation records.

Substitute the `title` and the `goal` block scalar with the user's title and
goal paragraph.

**Carry the `run_autonomy` field.** The run-autonomy contract is a top-level
`run_autonomy` block scalar (not part of `strategy`). The canonical template already
carries it verbatim; the composed (N/M/K > 1) path reuses the canonical
template's top-level keys (step 4.1), so it inherits `run_autonomy` too —
**confirm it wasn't dropped** when you rebuilt the `prompts:` list. The field
MUST be present and MUST reference `docs/AGENTS.md` §11. `run-promptbook` reads
the *book* at execution time, so the autonomy contract has to live in the
produced artifact, not just here.

### 6. Write the book

Write the assembled YAML document to
`docs/promptbooks/active/PB-NNNN-<slug>.yaml` (new-format `.yaml`; this matches
`author-promptbook`'s emit path — both emitters always write new-format
`.yaml`). Long-form fields (`goal`, `strategy`, `run_autonomy`, and each
prompt's `purpose`/`prompt`/`expected_output`) are YAML literal block scalars
(`|`). If the write fails, **STOP** — do not increment the counter. The number
stays unallocated.

### 7. Validate the book — REFUSE on failure (the gate before ALL side effects)

This is the **validation gate**: it is a discrete step that visibly precedes
every side effect below (manifest increment, index regen, `docs/index.md` bump,
log append). Nothing in steps 8–11 runs until this exits 0.

```bash
uv run "${CRUX_PLUGIN_ROOT}/scripts/validate-promptbook.py" --kind promptbook docs/promptbooks/active/PB-NNNN-<slug>.yaml
```

(In a source checkout, where `${CRUX_PLUGIN_ROOT}` is unset, substitute the checkout's `crux/` directory — source checkout: `<checkout>/crux/scripts/validate-promptbook.py`.)

This runs the **cycle-coverage pass**: it enforces the `4*(N+I)+4M+3K+2`
formula, the `module_tag` counts vs `modules:`, the per-module prompt counts
(ordinal contract), `cycle_kind: adr`↔shape, adr/verify mutual exclusion, and the
≥13 floor. **Exit 0 = clean; non-zero → REFUSE**: fix the assembled document and
re-validate BEFORE incrementing the manifest (step 8), regenerating the index
(step 9), updating `docs/index.md` (step 10), or logging (step 11). Validating
before the side effects (matching `author-promptbook` step 5b and `iterate` step
7) means an invalid book never leaks a bumped counter or an index row. The same
cycle-coverage gate an `iterate` book passes.

### 8. Increment `manifest.yml` atomically

- Re-read `docs/manifest.yml` to confirm `promptbook.next_number` is still
  `${N_alloc}`. If it changed, another invocation raced — STOP, retry.
- Set `promptbook.next_number = ${N_alloc} + 1`.
- Write back. Preserve all other keys and formatting.

While a tester's window is open, commit nothing and run no regenerator. The window runs from the tester's dispatch until the tester returns; outside a run there is none.

After the counter bump, commit the allocated file together with the bump. Never commit the counter bump without the file it allocated. Stage only these paths (`git add -- <paths>`) and commit only them (`git commit -- <paths>`), so no change already staged is included. If a regenerator still exits 2 with `migration-input-not-committed`, another uncommitted input is in the tree: stop and name it; never commit a file this skill did not write.

Then regenerate the summaries projection, then the doctrine projection, because the bump changes their input hash:

```
uv run "${CRUX_PLUGIN_ROOT}/scripts/summarize-adrs.py" --repo-root <repo-root>
uv run "${CRUX_PLUGIN_ROOT}/scripts/compile-doctrine.py" --repo-root <repo-root>
```

Confirm `--dry-run` of each exits 0, then commit the regenerated projections so none stays uncommitted.

### 9. Regenerate `docs/promptbooks/index.md`

Walk `docs/promptbooks/active/`, `docs/promptbooks/runs/`, and
`docs/promptbooks/archive/`. Rewrite `index.md` from scratch (see
`author-promptbook` step 6 for the table format). The new cycle book
appears in the Active table with `progress: 0/<total_prompts> (0%)`.

### 10. Update `docs/index.md`

Bump the `## Promptbooks (X active, Y archived)` count by 1. Update
`_Last updated:` to today.

### 11. Append to `docs/log.md`

```
## [YYYY-MM-DD] promptbook | authored PB-NNNN-<slug> (cycle)
```

Body: id, title, `total_prompts: <T>`, modules `(N×ADR, I×implementation, M×dev, K×review)`,
and a one-line note `cycle: assembled from modular templates`.

### 12. Verification

- [ ] `docs/manifest.yml` `promptbook.next_number` incremented and persisted.
- [ ] `docs/promptbooks/active/PB-NNNN-<slug>.yaml` exists, is valid YAML, has
      `format_version: "2"`, and validates against
      `${CRUX_PLUGIN_ROOT}/schemas/promptbook.schema.json` (e.g. via
      `validate-promptbook.py --kind promptbook`).
      A clean exit already machine-guarantees the `4*(N+I) + 4M + 3K + 2` formula,
      the ≥13 floor, sequential `prompts[].n`, `modules:` ↔ `module_tag`-count
      agreement, adr/verify mutual exclusion, exactly-2-untagged prep + summary,
      and the per-module prompt counts that **positionally** guarantee the
      ordinal contract (ADR module accept + council, dev module internal-review,
      review module fix-loop) **for template-assembled modules** — the pass
      checks size and contiguity, never prompt content. **Do not re-derive those
      by hand here.** Format two refuses every grandfather field before classification.
      Preserve format-one grandfather semantics only when reading or executing an
      original format-one book; never add that lane to a new format-two book.
- [ ] Top-level `run_autonomy` is present (NOT covered by the cycle-coverage
      pass — check it yourself; see the detail item below).
- [ ] `docs/promptbooks/index.md` lists the new book with `0/<T> (0%)`.
- [ ] `docs/log.md` has a new top-of-file `promptbook | authored ... (cycle)`
      entry.
- [ ] `docs/index.md` Promptbooks count incremented; `_Last updated:` set.
- [ ] The authored book's top-level **`run_autonomy` field is present and
      references `docs/AGENTS.md` §11** (the autonomy contract lives in this
      top-level field, not inside `strategy`). The
      default 1×1×1 path inherits it by copying the canonical template; the
      composed (N/M/K > 1) path MUST carry it too (it reuses the template's
      top-level keys — confirm it wasn't dropped).

## Red flags — STOP and reconsider

- About to mark a council gate done on reviewer reports, or to cast a seat's
  vote from a reviewer agent. A reviewer report never satisfies a council gate;
  the council runner's council record does.
- About to pause mid-run, once the cycle is executing, to confirm a step the
  current prompt authorizes. DON'T — the plan is the authorization. A cycle's
  genuine stop points are its module escalation loops (3-round non-convergence)
  and genuinely irreversible/outward-facing actions the plan didn't authorize
  (push/merge, deploy, external send, data deletion, spend). In-repo edits —
  including `docs/AGENTS.md`, skill files, code — are not those. See
  `docs/AGENTS.md` §11 "Run execution autonomy." (Authoring the book is this
  skill's job; executing it is `run-promptbook`'s — this red flag applies once
  execution starts.)
- About to author a cycle with `total_prompts < 13`. Refuse — the cycle
  contract sets the floor at 13. Either accept `N=M=K=1` or fall back to
  `author-promptbook` for a non-cycle plan.
- About to author a cycle where any module's invariants are missing —
  e.g. an ADR module without its council prompt, a dev module without
  quality gates or internal review, a review module without the
  fix-loop. The module templates are atomic; you can multiply them but
  you cannot splice partial modules.
- About to write `total_prompts: <X>` that doesn't equal `4*(N+I) + 4M + 3K + 2`.
  The formula is the contract; `total_prompts`, `len(prompts)`, and the
  `module_tag` counts MUST all agree.
- About to allocate a `PB-NNNN` before confirming title, goal, AND module
  counts. The goal must be a real description (Prompt 1's planning agents
  have no conversation context to fall back on), and the module counts
  determine the assembled length.
- About to increment `manifest.yml` before writing the book file. Wrong
  order; matches `author-promptbook` and `propose-adr`: write-then-increment.
- About to patch `docs/promptbooks/index.md`. Always rebuild from a
  directory walk — patches drift.
- About to skip the `docs/log.md` entry. Cycle starts MUST be auditable;
  the log is the audit trail.
- About to silently merge a user's "let me cut the review cycle out — we
  already reviewed this" into an authoring decision. The review module is
  load-bearing; if the user wants to skip review for a small change, this
  isn't a cycle — it's an ad-hoc fix and should not use this skill.

## Rationalization table and common mistakes

The Red flags list above is the primary stop-list. For the fuller
excuse→reality mapping and the recurring-mistake catalog, read
[`references/pitfalls.md`](references/pitfalls.md).
## See also

- `author-promptbook` — generic multi-prompt plan authoring (no canned modules).
- `run-promptbook` — advances the cycle through prompts 1→total_prompts.
- `archive-promptbook` — moves the completed book to `docs/promptbooks/archive/`.
- `propose-adr` / `transition-adr` — every ADR module's underlying skills.
- `council` — drives the council runner for every ADR module's
  council-review prompt (the cycle's invariant #2).
- Platform-provided code review — used by the dev module's internal-review
  prompt and the review module's correctness reviewer. In Codex, use the
  `crux_reviewer` role or have the parent agent perform an equivalent review;
  do not invoke Claude Code's `code-review` command or its CLI flags.
- `log-work` — used by every ADR module's accept prompt and the prep prompt.
- `audit-docs` — verifies cycle invariants and module composition by cross-checking the `modules:` block against the `module_tag` counts in the `prompts:` list.
- Templates (YAML partials): `cycle-promptbook-template.yaml` (canonical 1×1×1 cycle = 13 prompts, a full YAML book skeleton), `cycle-module-adr.yaml`, `cycle-module-dev.yaml`, `cycle-module-review.yaml` (each a list of prompt mappings).
- Schema: `${CRUX_PLUGIN_ROOT}/schemas/promptbook.schema.json` (book shape) + `${CRUX_PLUGIN_ROOT}/scripts/validate-promptbook.py` (vendored validator).

[^council]: rule:council-is-never-harness-native, rule:council-gate-needs-a-runner-record
[^review]: rule:review-gate-needs-a-reviewer-report
[^correct]: rule:existing-books-are-corrected-at-execution
