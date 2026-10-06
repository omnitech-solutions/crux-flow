# Advance a promptbook run

Read this reference before the first advance side effect. Follow the shared obligations in `../SKILL.md`.

## The pipeline (mode: advance)

### 1. Locate the in-flight YAML snapshot

Identify the active book at `docs/promptbooks/active/PB-NNNN-<slug>.yaml` and its pointed-to snapshot at `docs/promptbooks/runs/PB-NNNN-<slug>/run-RUN-NNN.yaml`. If either resolves to `.md`, apply the shared refusal in `../SKILL.md` before any write. For YAML, read `current_run` (RUN-NNN) and `current_prompt`.

Require `format_version` on both YAML documents. A YAML snapshot without it is malformed and must be refused before mutation.

If `current_run` is `null`, the user wants to advance with no active run — refuse, tell them to `start` first.

### 2. Decide the outcome

The user's intent maps to a terminal state for `current_prompt`:
- "done" / "next" / "advance" → `done`
- "skip" → `skipped`
- "block" / "stuck" → `blocked`

`blocked` now means blocked, with no second field and no pause-versus-abandon question at the prompt level. Ask if the outcome is ambiguous; don't infer `done` when the user might mean `skipped`. A `blocked` prompt is terminal for the run, so a run whose remaining prompts all reach a terminal state still completes, and the book archives as delivered with the block recorded in its terminal-state counts.

**When the user means "we are not finishing this run at all", that is mode `abandon`, not an outcome.** Abandonment moved from the prompt to the run (see below).

### 3. Mutate the YAML snapshot

The outcome decided in §2 is `done`, `skipped`, or `blocked`.

**Preferred: use the vendored `advance-run.py`.** Hand-editing the snapshot across N advances is error-prone and a naive re-emit silently drops the run-level trailing fields. For a `patch` book, always pass `--book`: on a completing advance the writer runs the blast-radius check before either document is written. If it refuses, leave the run active and inspect the check's independent output. Run
`uv run "${CRUX_PLUGIN_ROOT}/scripts/advance-run.py" <run-RUN-NNN.yaml> --outcome done|skipped|blocked [--result "…"] [--artifacts docs/a.md,docs/b.md] [--book <active-book.yaml>]`
(source checkout: `<checkout>/crux/scripts/advance-run.py`). It mutates the `n == current_prompt` element, moves the pointer (next `pending` → `running`, or completes the run), and updates the active book's pointer when `--book` is passed. It **rewrites only the values it changes**; every other byte of the snapshot, including comments, quoting, indentation and timestamp spelling, stays as it was. It adds `notes`/`pr_draft`/`summary` as `""` when they are absent. It refuses to write, and leaves the snapshot and the active book unchanged, when it cannot rewrite the snapshot in place — for example a comment inside a changed value, an anchor or alias, a duplicate key, or mixed line endings. It refuses a `.md` snapshot or a `.yaml` without `format_version`. If you cannot run it, mutate the YAML by hand per the field list below — and heed the trailing-field rule.

If a manual edit would complete a `patch` run, run `check-blast-radius.py` with its exact `--book`, `--run`, `--repo-root`, and `--docs-dir` before writing the terminal state. A nonzero result keeps the run active. Archive repeats the check after completion; a later failure leaves a truthful completed-but-unarchived run and must not trigger retroactive abandonment.

Mutate exactly the array element whose `n == current_prompt` (the JOIN KEY). On that element, set:
- `state: <outcome>` (lowercase enum `done|skipped|blocked`).
- `started: <set on first advance if still null — this advance's start time best-effort, or the actual prompt-start time if known>`.
- `completed: <now ISO 8601 UTC>`.
- `result: "<one-line summary the user provides, or \"\" (empty string) if none>"`.
- `artifacts: [<docs/ paths the prompt touched, as a real YAML list>]` — `[]` if none (a list, not a CSV cell).

Do NOT alter the element's `title` or any other element. Only the listed keys change. Never edit prior elements (their state was finalized in earlier advances). **Preserve the run-level trailing fields `notes` / `pr_draft` / `summary` verbatim on every advance** — they are set later in the run (Prep/Summary) and a re-emit that omits them silently drops accumulated notes, the PR draft, and the completion summary (`advance-run.py` leaves them untouched; a hand re-emit must copy them through). The per-prompt shape is validated by `run.schema.json` (audit invokes `validate-promptbook --kind run`); the prose contract is `docs/AGENTS.md` §11.B, unchanged in meaning.

### 4. Move the pointer

Find the next YAML `prompts[]` element whose state is `pending`. If found:
- Set the snapshot's top-level `current_prompt: <next>`.
- Set the next prompt's state to `running` and its `started:` to "now" best-effort, updated to the exact start on the next advance.
- Set the active book's `current_prompt: <next>`.

If no `pending` prompts remain (i.e. **all prompts are terminal** — `status: completed` ⇔ every prompt is `done`/`skipped`/`blocked`):
- Set the snapshot's top-level `status: completed`, `completed_at: <now>`, `current_prompt: null`. Run-level `abandoned` is written only by mode `abandon` or the start-path supersession of a stale prior run.
- The active book's `current_run` is UNCHANGED — it still names this run. Set only the active book's `current_prompt: null`. Only a book's current run can authorize its archive, so the pointer stays until `archive-promptbook` nulls it. (Book stays `status: active` — `archive-promptbook` moves it to `archive/`.)

### 5. Stop — the advance is finished

Those two surfaces are the whole write. Do not regenerate `docs/promptbooks/index.md`, do not bump `docs/index.md`, do not call `log-work`, and do not append to `docs/log.md`. The indexes regenerate at run start and at archive; the log records the start and the archive and nothing between.

### 6. Verification

- [ ] Only the current YAML `prompts[]` element (the one with `n == prior current_prompt`, plus the new `running` element) changed; no other element moved.
- [ ] The snapshot's `current_prompt` matches the active book's `current_prompt`.
- [ ] If run completed: `status: completed`, `completed_at:` set, `current_prompt: null`, active book's `current_run` UNCHANGED — still names this run. (`status: abandoned` is NOT set by advance.)
- [ ] For a `.yaml` run: the snapshot still validates against `run.schema.json` (`validate-promptbook --kind run`) — `n` contiguous from 1, no stray keys.
- [ ] Exactly two surfaces changed: the run snapshot and the book's pointer. `docs/promptbooks/index.md`, `docs/index.md` and `docs/log.md` are untouched by this advance.
