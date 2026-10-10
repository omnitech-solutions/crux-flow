---
name: run-promptbook
description: "Start, advance, abandon, or inspect a promptbook run. Report status and progress without advancing it."
arguments: [book]
metadata:
  tags: "promptbooks, execution, state"
  bundles: "crux-docs"
  risk_level: "low"
  triggers: "run promptbook | start promptbook | advance promptbook | next prompt | skip prompt | block on prompt | abandon this run | promptbook status | run status | visualize run progress | show run progress | how far along is PB-NNNN | progress of PB-NNNN | run progress bar/chart | where am I | where was I on PB-NNNN | resume my cycle | what's next on PB-NNNN | cycle status | how do I pick this run back up | how do I resume"
  routing_note: "Start, advance, and abandon update run state and book pointers; status and terminal progress write nothing. Explicit Markdown writes a deterministic artifact and one promptbook log operation, without advancing."
---

# Run Promptbook

If the selected record is a Crux Flow run, use the `flow` skill and its
persistent outcome ledger. Do not infer a fork mode for historical upstream
promptbooks. Existing upstream books continue through this procedure.

> **Invocation:** a bound `$book` argument names the promptbook number — `/crux:run-promptbook 0007` binds `$book` to `0007`. Read the value from `$book` where this skill needs the promptbook number.

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

Promptbooks separate the frozen plan in `<docs_dir>/promptbooks/active/` from the run snapshot in `<docs_dir>/promptbooks/runs/`. Start, advance, and abandon change run state, never prompt content. Status derives a view without changing run state. Resolve the repository and `<docs_dir>` through `crux-config.py` before following any operation. Live execution accepts structured `.yaml` books and runs only. Historical `.md` records remain readable through status and documentation readers.

## Explicit execution formats

Structured YAML format one retains its original structure, hash and cycle classification.
New cycle books use format two; their runs use format two with initial
`implementation_bindings: []`. Format two requires a hash-bound `cycle_kind` and
complete `implementation_slots`, including an explicit empty list for incidental choices.
It refuses grandfathering, unknown kinds, invalid counts and module order before
classification, even without run-start history. Never convert a live or archived run
implicitly. Historical Markdown remains readable under the existing refusal boundary.
Implementation Decisions preserve reasoning without governing authority. Only a
successful lawful close writes approval. Independently reviewed results establish
historical delivery; source verification at the queried revision establishes current state.
No Implementation Decision authorizes extra scope, spend or external actions.

Book/run format and council evidence versions are independent. Live execution of
both book formats uses the current attempt-aware gate. New council records use
format two and attempts use format one. A legacy council record is admissible
only when committed run-start history proves its pre-cutover base.
New formal closes retain gate context three with approval profile four.
Historical context two/profile three and context one/profile two remain immutable and serve historical replay only.
Never choose an approval profile from the book format or substitute a current registry
for a committed historical context. Unknown or inconsistent context refuses approval.
Keep frozen books unchanged; record execution corrections through the owning historian.

## Select the operation

Choose one operation from the user's intent and **read its exact local reference before the first side effect**. The references are part of this skill's installed directory. If the selected reference is missing or unreadable, refuse that operation and report the missing path. Do not substitute another operation's procedure or rely on memory.

| Intent | Read before acting | Result |
|---|---|---|
| Start a new run | `references/start.md` | Creates snapshot, updates book and indexes, records start op |
| Complete, skip, or block a prompt | `references/advance.md` | Updates snapshot and book pointer only |
| Issue or advance a gate prompt | `references/gates.md` | Council runner or reviewer reports as evidence; the gate check decides |
| Deliberately abandon a run | `references/abandon.md` | Records run-level abandonment and book pointer |
| Ask for status, progress, or how to resume | `references/status.md` | Reads state; only explicit Markdown writes an artifact and one log operation |

If an active run already exists, continue through its authorized prompts without per-step permission. Only the stop points in `docs/AGENTS.md` §11 apply. Before you issue every prompt, run the gate-information query `advance-run.py <run> --book <book> --gate-info --prompt <n>` (a commander holds no shell, so it dispatches the historian) and follow any `correction_notice` before you act on the prompt text. Read `references/gates.md` when the query reports the class `council`, `module-close` or `independent-review`. When `adr_acceptance_pending` is non-empty, do not issue the prompt; the "Module close on an `adr-*` module" paragraph in `references/gates.md` section 4 names the remedy for each entry. A prompt's assignment carries the book's whole Evidence and Constraint, even when its Outcome is narrowed. Preserve independent review and required gates. Every `--outcome` needs a resolvable book whose content hash matches the run's `book_content_hash`. An ordinary advance changes the run snapshot and active-book pointer; a formal format-two close also retains immutable evaluated gate context; it does not regenerate indexes, call `log-work`, or add a per-prompt log entry. Status does not advance the run. A completed or deliberately abandoned run is archived through `archive-promptbook`.

## Objectives context for execution

**Order of work, once per invocation:** resolve `<docs_dir>` (pipeline step 0, which owns that
resolution and which nothing here re-implements); read `<docs_dir>/objectives.md`; format-detect;
then run the mode's pipeline.

Before starting or resuming execution, read the resolved `<docs_dir>/objectives.md` and apply
`docs/AGENTS.md` §5.B. Pass its resolved
path to commander. Every agent or forked skill assignment includes that path
and either the mission with relevant goal statements and measures, or an
explicit instruction to read it before work and preserve this context in
further delegation. Include known tensions; a concrete conflict with the
approved plan is a contradicted premise under §11.

Keep alignment in the existing assignment or result. This adds no run fields,
no per-advance writes, and no per-step approval. Re-read when objectives change.

## The book's Outcome, Evidence and Constraint travel with every dispatch

The book's `goal` states what should improve for the affected user, what would
demonstrate it, and what the change must preserve — `docs/AGENTS.md` §11 "The
assignment contract" is the rule. Every agent a prompt dispatches receives all
three, beside the objectives path, **including a generic dispatch that names no
crux role**: a worker on the far side of a dispatch has none of this run's
history, so anything you do not write into the dispatch is not there.

Pass them verbatim, or narrow the Outcome to the dispatched part and say what
the narrowing dropped; the Evidence and the Constraint pass whole. Record a
narrowing in the run snapshot rather than by editing the book — `goal` is inside
the frozen plan the run's `book_content_hash` covers, so editing it mid-run
moves the hash and CHK-PB-BIND fires.

A prompt's `result` records the dispositions the work came back with, so the
acceptance bar and what was actually observed against it both survive in the
snapshot. A book whose goal omits one of the three statements is named in the
run notes and the run proceeds; a missing statement is no stop point under §11.

## Live format gate

After resolving `<docs_dir>`, identify the exact active book path and its pointed-to run path, if any, before the first write. Check each path's extension before parsing or changing either file. A `.yaml` book or run requires `format_version` and must pass the existing validator. A `.yaml` document without it is malformed; never parse it as legacy Markdown.

| extension | `format_version` | route |
|---|---|---|
| `.yaml` | present | structured YAML; continue with the selected operation |
| `.yaml` | absent | malformed YAML; refuse before mutation |
| `.md` | (ignored) | refuse start, advance, or abandon before mutation |

For a `.md` book or run, name every affected path and refuse before mutation. Give the state-specific route: if all remaining prompts can be **truthfully** completed, use public Crux `v3.23.2` (tag `v3.23.2`, commit `08ee30ec2f1d1b4b0ce970f2e1582bb4f83cd20d`) on a copy to finish and archive the Markdown run. Convert the book before its run, validate YAML, then upgrade. An already archived eligible Markdown run can be converted there. The tag has **no verified deliberate Markdown-abandon route**. If a run cannot finish, keep its original `in_progress` state and bytes: it is stranded and non-retryable on the current distribution. It remains readable history, and separate YAML books and runs may proceed. Never mark an unfinished prompt done or skipped merely to migrate; never convert an in-progress run in place. The upgrade guide gives acquisition steps. Status may read historical `.md` records without changing them.

## Orchestration rules a run follows

The commander, dev-lead, developer and historian agent definitions, the cycle module templates and `run-promptbook`'s `references/gates.md` carry these rules. This section summarizes each rule; those sources carry the full text.

- **Tester.** One designated tester runs the full suite; the dev-lead may run its own; one full suite runs at a time. The tester may reuse a matching passing record for an unchanged tree.
- **Commit lane.** A run has one commit lane. The tester's window runs from the tester's dispatch until the tester returns. The agent that dispatched the tester holds the window. While it is open, that agent commits nothing to the main checkout, convenes no council, runs no live-tree tool, and dispatches no agent that does.
- **Hand-back.** A hand-back with work in flight states that first, then lists each running dispatch with its result file.
- **Worktree base.** Developer worktrees start from the run's current HEAD or are rebased before integration.
- **Owner exception.** An owner-exception record is written only from the owner's own instruction. The owner's instruction is a decision the owner stated in the owner's own message in the session. An agent report, a result file, a run note or a council verdict is never an owner's instruction.

## When to use

- "run promptbook", "start promptbook" → start.
- "advance promptbook", "next prompt", "mark done", "skip prompt", "block on prompt" → advance.
- "abandon this run" → abandon.
- "visualize run progress", "show run progress", "cycle status", "where am I", "resume my cycle", "how do I resume" → status.
- Author a book with `author-promptbook`; archive a completed book with `archive-promptbook`.
