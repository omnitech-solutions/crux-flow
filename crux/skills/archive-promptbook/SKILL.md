---
name: archive-promptbook
description: "Archive a promptbook after its current run completes or is deliberately abandoned. Refuse runs still in progress."
arguments: [book]
metadata:
  tags: "promptbooks, archive, lifecycle"
  bundles: "crux-docs"
  risk_level: "low"
  triggers: "archive promptbook | archive PB-NNNN | this promptbook is finished | close out the book"
  routing_note: "Only when all prompts terminal."
---

# Archive Promptbook

> **Invocation:** a bound `$book` argument names the promptbook number — `/crux:archive-promptbook 0007` binds `$book` to `0007`. Read the value from `$book` where this skill needs the promptbook number.

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

The terminal-state operation for a promptbook's lifecycle. A book is born in `docs/promptbooks/active/` (created by `author-promptbook`), executed across one or more runs (driven by `run-promptbook`), and finally archived here when all of its prompts have reached a terminal state.

**Check both paths before mutation.** This skill reads the existing book and its current run, then changes both. Identify their exact paths before the first write. Only structured YAML may be archived by the current version:

| extension | `format_version` | route |
|---|---|---|
| `.yaml` | `"1"` or `"2"` | validate and continue with top-level YAML keys (`status`, `completed_at`, `archive_note`) per `promptbook.schema.json` / `run.schema.json` |
| `.yaml` | absent | malformed YAML; refuse before mutation |
| `.md` | (ignored) | refuse before mutation, naming the book and run paths |

For a `.md` book or run, refuse before mutation and name both affected paths: `<book>` and `<run>`. If every remaining prompt can be truthfully completed, use public Crux `v3.23.2` (tag `v3.23.2`, commit `08ee30ec2f1d1b4b0ce970f2e1582bb4f83cd20d`) on a copy to finish and archive the Markdown run. Then convert the book before the run, validate YAML, and upgrade. An already archived eligible run can convert there. The tag has no verified deliberate Markdown-abandon route. If the run cannot finish, preserve its `in_progress` state and original bytes as stranded, non-retryable history; start separate YAML work. Never mark unfinished prompts terminal merely to migrate, and never convert an in-progress run in place. The upgrade guide gives acquisition steps. Historical Markdown remains readable and indexable; this skill does not mutate it.

**Archived books are immutable.** Once a book lives under `docs/promptbooks/archive/`, no skill — including this one — may rewrite, re-archive, or un-archive it. To pursue further work, author a successor book that names this one in its prose (per the abandon rule in `docs/AGENTS.md` §4) — never re-open an archived one.

Pairs with: `author-promptbook` (creates the book), `run-promptbook` (executes prompts and writes run snapshots). Distinct from `audit-docs` (catches drift, never archives) and from `log-work` (journals progress, never moves files).

## Preserve the execution format and reasoning

Archive format-one and format-two YAML records under the semantics they started with.
Unknown or missing versions refuse. Never upgrade history during archival. Format-two
slot declarations, approval bindings, reviewed revisions and results remain addressable
under the book/run/slug identity after the book moves. Archival grants no governing or
current-state authority to an Implementation Decision. It changes neither reviewed
reasoning nor independently reviewed delivery evidence.

## When to use

- User says: "archive promptbook", "archive PB-NNNN", "this promptbook is done", "close out the book", "finalize PB-NNNN".
- A `run-promptbook` advance just marked the final prompt as `done` and the user agrees the book is complete.
- The user abandoned the run via `run-promptbook abandon` and wants the book closed as abandoned.

Do **not** use this skill for:
- Books whose current run is still `in_progress` → refuse (see preconditions). Advance it, or abandon it first.
- Books already under `docs/promptbooks/archive/` → refuse. Archived books are immutable.
- A successor book's predecessor → archive the predecessor only after its own run has completed or been abandoned.
- Tidying up `docs/promptbooks/runs/` snapshots → snapshots are append-only artifacts; this skill never edits them beyond closing the current one.
- Reviving a closed book → never. Author a successor book instead.

## Preconditions (hard refusal)

Refuse to archive unless **all** of the following hold:

1. The book file exists under `docs/promptbooks/active/<id>-<slug>.yaml` (never `archive/`). If only a `.md` book exists, apply the Markdown refusal above.
2. The book's top-level `current_run` points to an existing YAML run snapshot under `docs/promptbooks/runs/<id>-<slug>/run-<RUN-NNN>.yaml`. If it points to a `.md` snapshot, apply the Markdown refusal above. **Only the book's CURRENT run can authorize its archive** — never scan other runs for a terminal one. A completed or deliberately abandoned run retains the pointer until archive.
3. That run is archive-eligible by one of exactly two paths:

   **Path 1 — DELIVERED.** The run has `status: completed` AND every prompt is in a terminal state: `done`, `skipped`, or `blocked`. The per-prompt states are defined canonically in **`run.schema.json` / `docs/AGENTS.md` §11.B** (the run-format SSOT — read §11.B). A `blocked` prompt is terminal: a run that reached `completed` while holding one archives as delivered, and the block is recorded in the archive note's terminal-state counts.

   **Path 2 — ABANDONED.** The run has `status: abandoned` AND carries `abandonment.kind: deliberate`. The prompts may be `pending`, `running`, or `blocked` — an abandoned run is archived precisely because it did not finish, and the non-terminal prompt left behind is the record of how far it got.

   Everything else refuses:
   - `status: in_progress` — the run is live. Advance it, or abandon it.
   - `status: abandoned` with `abandonment.kind: superseded` — that value is written by a later run's start over a stale run. **It confers no archive eligibility.**
   - `status: abandoned` with no `abandonment` mapping at all — a run that ended without a recorded abandonment is not archive-eligible, and the record is never retrofitted.

4. **For a `cycle_kind: patch` book only:** the blast-radius check below passes.

If preconditions fail, print the reason and the remedy:

```
Cannot archive PB-NNNN: <the failing precondition>.
Current run RUN-NNN: status <status>[, abandonment.kind <kind>].
Non-terminal prompts: <K>: <state>, <L>: <state>
Run `run-promptbook advance` to finish it, or `run-promptbook abandon PB-NNNN --reason "<why>"`
to close it as abandoned.
```

Do not partially proceed. Exit cleanly.

## The blast-radius precondition (`cycle_kind: patch` books only)

A `patch` book declares its blast radius before its run starts. The declaration is checked twice, and only this check is mechanical: the verify phase's council reviews it for **proportion** to the change, and this precondition compares it against the paths the run **actually changed**.

Run, after the status check passes:

```bash
uv run "${CRUX_PLUGIN_ROOT}/scripts/check-blast-radius.py" \
  --book docs/promptbooks/active/<id>-<slug>.yaml \
  --run  docs/promptbooks/runs/<id>-<slug>/run-<RUN-NNN>.yaml \
  --repo-root <repo root> --docs-dir <docs_dir>
```

The evidence is the repository's own change record, and not the run's account of itself: neither the snapshot's `artifacts` nor its `notes` is consulted. One value does come from the run — `base_commit`, the boundary `run-promptbook` stamped at run start — and it is **pinned rather than trusted**: the check refuses when it diverges from the value in that snapshot's own committed version, because a run that overshoots can commit its work and hand-edit the value forward to shrink the diff proved here.

**`docs/AGENTS.md` §11.C is the single source of truth for the exact command and its flags** — including the `--no-renames` that is load-bearing for correctness, not tidiness. They are not restated here. This line previously carried a partial copy of that command, and a partial copy of a security-load-bearing command reads as complete.

**Read the exit code as a gate, in three lanes — never conflate them:**

- **exit 0 = clean.** Every changed path lies inside the declaration. Proceed.
- **exit 1 = the check FAILED.** A document verdict, not an environment error — **do not wave it through.** The JSON on stdout lists `undeclared` paths, or names the refusal (`blast_radius` missing, `base_commit` absent or diverged from its committed record, the book's plan no longer hashing to the run's `book_content_hash`, a declared entry that is not repo-relative). REFUSE the archive.
- **exit ≥ 2 = an environment-capability error** (git absent, not a git work tree, the book or run document outside `--repo-root`, the base commit unknown). Surface stderr and REFUSE. Fail-closed: an unproven blast radius is not a passed one.

**If the check fails:** refuse the archive. If the run is still active, correct the overshoot or deliberately abandon and re-author in the fitting tier. If the run is already completed, retain it as completed but unarchived, record the failed containment evidence, and require an explicit successor/recovery decision. `run-promptbook abandon` cannot retroactively abandon a completed run; do not try it or rewrite completed history. The final archive check remains mandatory even when precompletion containment passed.

**The declaration is fixed once the run starts, and no prompt may widen it.** That is enforced by the binding hash, not by this check: `blast_radius` sits inside the frozen-plan subset `book_content_hash` covers, so widening it mid-run moves the hash and `audit-docs` CHK-PB-BIND fires. Never "fix" a failing check by editing the declaration.

**Honest limit.** Containment is machine-checked; proportion is a council judgment. A blast radius drawn wider than the work needs, and waved through by the verify council, passes this check for free.

**Second honest limit: a `.gitignored` path is invisible to this check.** Both evidence commands exclude ignored files, so a patch that writes one is never considered against the declaration and the check reports clean. The verify council is the only reader that can catch it.

## The pipeline

Execute in order. Never reorder, never skip.

### 0. Resolve per-repo configuration (.crux)

Run `uv run "${CRUX_PLUGIN_ROOT}/scripts/crux-config.py"` from the repo root (or pass `--repo-root <repo-root>`), and confirm the returned `repo_root` is the repo you are operating in — `source: "discovery:<dir>"` with an unexpected `repo_root` means you resolved the wrong directory, not that no config exists. On exit 1, **STOP** and surface the `{"error": ...}` payload — never fall back to defaults. Use the returned `docs_dir` to resolve the docs root wherever this skill says `docs/` (per the docs/AGENTS.md §14 normative definition clause), and accept prefixed book ids (e.g. `CRX-PB-0040`) anywhere this skill says `PB-NNNN` or `<id>` — the prefixed string is the id, verbatim, in paths and frontmatter.

### 1. Verify preconditions

Before book discovery or recursive walking, prune retained demonstration holdings: list the holdings once with `uv run "${CRUX_PLUGIN_ROOT}/scripts/authority-view.py" retained-roots --repo-root <repo-root>` and skip each listed root during recursive walks; test one path with `uv run "${CRUX_PLUGIN_ROOT}/scripts/authority-view.py" retained --repo-root <repo-root> <path>` (exit 0, `retained` true or false). Never read held configuration, books or runs to establish parent archive eligibility. Held records contribute no indexes or allocation counts. A layout refusal stops the walk before any archive mutation. The holding contract is `docs/AGENTS.md` §3; direct concern globs need no recursive scan.

- Identify the book's exact path under `docs/promptbooks/active/` and its pointed-to run path. Apply the Markdown refusal above if either is `.md`, before reading archive eligibility or changing any file. Confirm the YAML book has `status: active` and is in `active/` (not `archive/`).
- Read the current YAML run snapshot at `docs/promptbooks/runs/<id>-<slug>/run-<current_run>.yaml`.
- Determine which eligibility path applies. For Path 1, walk every prompt and confirm each state is `done`, `skipped`, or `blocked`. For Path 2, read the run's `abandonment.kind` and confirm it is `deliberate`.
- **Validate the YAML book BEFORE branching on `cycle_kind`:**

  ```bash
  uv run "${CRUX_PLUGIN_ROOT}/scripts/validate-promptbook.py" --kind promptbook \
    docs/promptbooks/active/<id>-<slug>.yaml
  ```

  Refuse the archive on a non-zero exit, and surface the reported errors. **The order is the point.** The next bullet branches on `cycle_kind`, and in format one, `cycle_kind` is not in the frozen-plan subset that `book_content_hash` covers — so flipping a book from `patch` to `adr` mid-run moves no hash, trips no `CHK-PB-BIND`, and silently deletes the containment gate the tier pays for. The validator catches the flip: a flipped book still carries the `blast_radius` and per-prompt `phase` fields that only a `cycle_kind: patch` book may carry, so it no longer validates as the kind it now claims to be. Pin `--kind promptbook` rather than relying on auto-detect, so a book whose keys were edited cannot be validated as some other kind.
- For format two, canonical hash binding also covers kind and full implementation
  slots. Validate the chosen format before branching and require the pointed-to run
  to retain its own format and frozen hash. Do not treat unknown history as permission
  to grandfather or bypass a close.

- For a `cycle_kind: patch` book, run the blast-radius check and read its exit code as the three-lane gate above.
- If any precondition fails, **refuse** (see above). Do not write anything.

### 2. Finalize the current run snapshot

In `docs/promptbooks/runs/<id>-<slug>/run-<current_run>.yaml`:

- Set the top-level YAML key `status: completed` (if it isn't already) and `completed_at: <ISO-8601 UTC timestamp for now>` ONLY IF it is currently `null`. These are real YAML mapping keys (per `run.schema.json`).
- **NEVER overwrite `status: abandoned` with `completed`.** An abandoned run archives AS abandoned; rewriting its status would erase the reason the book closed and turn an honest record into a claim of delivery. On Path 2 leave `status` and `abandonment` exactly as they are, and fill `completed_at` only if it is null.
- Either way: do not overwrite a previously-set `completed_at`.
- Do not edit any `prompts[]` elements. Their states, timestamps, results, and artifacts are immutable.

### 3. Move the active book file to archive

- `git mv` (or `mv`) `docs/promptbooks/active/<id>-<slug>.yaml` → `docs/promptbooks/archive/<id>-<slug>.yaml`.
- The filename — including `<id>`, `<slug>`, and `.yaml` — does not change. Only the directory.
- The book's run snapshots under `docs/promptbooks/runs/<id>-<slug>/` **stay where they are**. They are referenced by both active and archived paths via (extension-less) wiki-links; moving them would break references.

### 4. Update archived book + write the archive note

In `docs/promptbooks/archive/<id>-<slug>.yaml`:

- Set `status: archived` (was `active`).
- Set `current_run: null` and `current_prompt: null` — there is no active run against an archived book. This skill is the SOLE writer that nulls `current_run`; `run-promptbook` leaves the book's pointer untouched on both a completed and an abandoned advance.
- Leave `created_at`, `total_prompts`, `forked_from`, `tags`, `title`, `id`, `format_version`, `cycle_kind`, `blast_radius`, `goal`, `strategy`, `prompts` (including each prompt's `phase`), and `modules`/`run_autonomy`/`implementation_slots` if present untouched. Preserve all run approval bindings and result references.

**Write the `archive_note` mapping field** (per the `promptbook.schema.json` `archive_note` object). `status` / `current_run` / `current_prompt` are top-level YAML keys; the archive note becomes a top-level `archive_note` mapping:

  ```yaml
  archive_note:
    archived_at: <YYYY-MM-DD>
    final_run: <RUN-NNN>
    note: |
      Archived <YYYY-MM-DD> as <delivered|abandoned>. Final run: [[promptbooks/runs/<id>-<slug>/run-<final-run>]] (<N>/<total_prompts> prompts terminal).<on the abandoned path: ` Abandoned: <abandonment.reason>.`>
  ```

  If `archive_note` is already present (non-null), do not overwrite it — refuse and surface this as a precondition failure (the book was already archived).

### 5. Regenerate `docs/promptbooks/index.md`

Rewrite the index. The directory walk is **extension-agnostic** — enumerate both `*.yaml` and `*.md` books in `active/` and `archive/` so coexisting books all appear; wiki-links are extension-less (`[[promptbooks/archive/<id>-<slug>]]`):

- Remove the archived book's row from the **Active** table.
- Decrement the `## Active (N)` count.
- Add the archived book to the **Archived** list:

  ```markdown
  - [[promptbooks/archive/<id>-<slug>]] — completed <YYYY-MM-DD>
  ```

  Archived list is reverse-chronological by archive date (newest first).

- Increment the `## Archived (N)` count.
- The **Recent runs** section is unchanged structurally — but the final-run snapshot of this book may now appear there if it falls within the last 20 runs.
- Bump `_Last updated:_` to today.

### 6. Update `docs/index.md`

- Promptbooks-concern row counts: decrement active count, increment archived count.
- Bump `_Last updated:_` to today.

### 7. Append to `docs/log.md`

One entry (newest-first, at top):

```
## [YYYY-MM-DD] promptbook | archived <id>-<slug>
```

Body, 1–3 lines:
- Final run id and its `<N>/<total_prompts>` completion ratio.
- Whether the book was archived as **delivered** (Path 1) or as **abandoned** (Path 2). On Path 2 include `abandonment.reason` and do not state a completion ratio that overstates what happened.
- Counts of prompt states: `<done> done, <skipped> skipped, <blocked> blocked` (plus `<pending>` / `<running>` on Path 2).
- Reference to the archived path: `[[promptbooks/archive/<id>-<slug>]]`.

### 8. Hand-off

Report to the user:
1. `PB-NNNN-<slug>` archived → `docs/promptbooks/archive/<id>-<slug>.yaml`.
2. Final run snapshot path.
3. Whether it archived as delivered or abandoned, plus prompt-state counts (`N done / M skipped / K blocked`, and any non-terminal remainder on the abandoned path) out of `total_prompts`.
4. Reminder: the archived book is immutable. To continue this thread of work, invoke `author-promptbook` for a successor book that names this one in its `goal` or `strategy` prose. `forked_from:` stays `null` — it is an accepted vestige and nothing writes it.

## Verification checklist

- [ ] Identified the book and run paths before the first write; a `.md` path was refused with the pinned recovery route.
- [ ] Source path was `docs/promptbooks/active/<id>-<slug>.<ext>` BEFORE the move (not already in `archive/`).
- [ ] Eligibility came from the book's CURRENT run, by exactly one of the two paths — `completed` with every prompt terminal, or `abandoned` with `abandonment.kind: deliberate`.
- [ ] A `superseded` abandonment, or an `abandoned` run with no `abandonment` mapping, was REFUSED.
- [ ] Current run snapshot has `completed_at: <timestamp>` set; on Path 1 `status: completed`, on Path 2 `status: abandoned` and the `abandonment` mapping both left untouched.
- [ ] `validate-promptbook.py --kind promptbook` ran on the book and exited 0 BEFORE anything branched on `cycle_kind` (the guard against a mid-run kind flip; format two also hash-binds the kind).
- [ ] For a `cycle_kind: patch` book: `check-blast-radius.py` exited 0, and its exit code was read as a three-lane gate (1 = finding, 2 = fail-closed), not as a log line.
- [ ] No per-prompt entries in the run snapshot were modified.
- [ ] Book file is now at `docs/promptbooks/archive/<id>-<slug>.<ext>` (same extension as source); nothing remains at the active path.
- [ ] Book `status: archived`, `current_run: null`, `current_prompt: null` as top-level YAML keys.
- [ ] Run snapshots remain under `docs/promptbooks/runs/<id>-<slug>/` (NOT moved).
- [ ] YAML `archive_note` mapping written exactly once (refused if it already existed).
- [ ] `docs/promptbooks/index.md` regenerated: active count decremented, archived count incremented, `_Last updated:_` bumped.
- [ ] `docs/index.md` row counts and `_Last updated:_` updated.
- [ ] One `promptbook` op entry appended to top of `docs/log.md`, naming whether the book archived as delivered or as abandoned.
- [ ] The archive note records delivered-versus-abandoned truthfully; an abandoned book claims no completion ratio it did not reach.

## Red flags — STOP and reconsider

- About to archive a book that has a `pending` or `running` prompt in its current run. **Never.** Refuse.
- About to archive a book that already lives in `docs/promptbooks/archive/`. **Never re-archive.** Refuse.
- About to overwrite an existing `completed_at` timestamp on the run snapshot. The original timestamp is the truth.
- About to edit per-prompt entries in the run snapshot during finalization. Prompt entries are immutable; only the run-level YAML fields change here (status, completed_at).
- About to delete or move the `docs/promptbooks/runs/<id>-<slug>/` directory. Snapshots stay where they are forever.
- About to assign a NEW promptbook id during archival. No — the id is permanent. Archival preserves it.
- About to parse a `.yaml` book/run without `format_version` as Markdown. NEVER — that YAML document is malformed and must be refused.
- About to treat `status: abandoned` with `abandonment.kind: superseded` as eligible. NEVER — that value marks a stale run a later start rolled over, and it confers no eligibility. Only `kind: deliberate` does.
- About to write an `abandonment` record here so the book becomes eligible. NEVER — abandonment is recorded when it is taken, by `run-promptbook abandon`, not manufactured at archive time. That is backdating a decision nobody made.
- About to overwrite an abandoned run's `status` with `completed` "to tidy it up". NEVER — it would convert an honest record of unfinished work into a claim of delivery.
- About to wave through a `patch` book's blast-radius check because it exited non-zero "for environment reasons". Read the lanes: exit 1 is a real finding, exit 2 is fail-closed. Neither is a pass.
- About to write a `## Archive note` Markdown section into a `.yaml` book. The note is the top-level `archive_note` mapping (`{archived_at, final_run, note}`).
- About to add a second archive note (an `archive_note` mapping or a `## Archive note` section) because "the prior one looks wrong". The presence of an existing archive note means the book is already archived; this is a refusal, not a re-archival.
- About to remove the book from `docs/promptbooks/index.md`'s Active section without adding it to the Archived section. Both edits land in the same write.

## Rationalization table

| Excuse | Reality |
|--------|---------|
| "Most prompts are done; the last one is just `running`. Close enough." | "Close enough" is not terminal. Advance it, skip it, or abandon the run — then archive. |
| "The patch changed one file outside the declared radius. I'll widen the declaration and re-run the check." | The declaration is frozen by `book_content_hash`; widening it trips CHK-PB-BIND and hides the overshoot. Abandon the run and re-author in `iterate` or `dev-cycle`. |
| "The blast-radius check can't find git, so I'll skip it this once." | An unproven blast radius is not a passed one. Exit 2 is fail-closed by design. |
| "The book is stale and the user moved on; I'll archive without ceremony." | If the user moved on, every leftover prompt should be explicitly `skipped`. Mark them, then archive. |
| "I'll re-archive this book to fix the archive note." | Archived books are immutable. If the note is wrong, the audit trail records the wrong-but-real archival event. Don't rewrite history. |
| "The current run's `completed_at` is null but the book is done — I'll just set it now." | Only set `completed_at` if it's currently null. If a prior `run-promptbook` set it, that's the moment of completion. Don't overwrite. |
| "I'll move the run snapshots into the archive folder to keep things tidy." | Snapshots are referenced by stable paths. Moving them breaks links from journal entries, log entries, and possibly successor books. Leave them. |
| "Let me edit one prompt's state from `pending` to `skipped` so I can archive." | That's a `run-promptbook` operation, not this skill. Run the right skill, then archive. |
| "The book has no run snapshot — let me archive without one." | A book that's never run hasn't reached terminal anything. Refuse, or run it (one prompt → mark skipped → archive). |
| "I'll bump the archive note's date if I'm re-running this skill in the same session." | Don't re-run this skill against an archived book. The first archival is the truth. |

## Common mistakes

- **Setting `completed_at` to today's date when the run actually completed earlier.** Use the snapshot's existing value if non-null; only fill if null.
- **Forgetting to flip `status: active` → `status: archived`** on the moved file. Audit will catch it, but ship it clean.
- **Forgetting to null out `current_run` / `current_prompt`** on the archived book. They reference a state that no longer exists from this book's perspective.
- **Adding the book to the Archived index list without removing it from the Active table.** Both edits go together.
- **Using a `journal` op (or any op other than `promptbook`) in the log entry.** The op enum is fixed (`promptbook` is the right op for any active/archive promptbook movement).
- **Looking for a per-prompt archive-eligibility flag.** There isn't one. `blocked` is a terminal prompt state, and eligibility is a property of the RUN (`status` plus `abandonment.kind`), not of any prompt.
- **Modifying per-prompt state inside the run snapshot during archival.** The `prompts[]` array is written by `run-promptbook` and frozen at that moment.
- **Renaming the book file during the move** (e.g., reslug because the title was cleaner). The slug and `.yaml` extension remain fixed through archival.
- **Skipping the archive note** because "the index already records it". The YAML `archive_note` is the in-file record; the index is the catalog.
