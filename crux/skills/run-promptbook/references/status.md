# Read promptbook status

Read this reference before answering status, progress, or resume questions. Follow the shared obligations in `../SKILL.md`. A status answer or terminal view writes nothing and never starts, advances, abandons, or archives a run. Only an explicit request for a persistent Markdown progress artifact allows the two writes below.

## Resolve and render

Resolve the repository with `uv run "${CRUX_PLUGIN_ROOT}/scripts/crux-config.py" --repo-root <repo-root>` and use its `docs_dir`. An explicit `PB-NNNN` names a book in the active or archive index. With no id, inspect active books with a non-null `current_run`. Select the sole candidate, or the most recently modified current run if several qualify. A completed run awaiting archival still qualifies. If none qualifies, report no active run and list active books.

The retained renderer resolves a bare book id to the book's `current_run`, then the highest-numbered run if that pointer is null. An explicit `--run RUN-NNN` selects another run. It also accepts a run snapshot path. A run's own `.yaml` or legacy `.md` extension controls its format; `.yaml` without `format_version` is an error. For a mismatched `book_content_hash` or a legacy run, `module_tag` renders as `—`; never invent a tag from an unbound book. The renderer validates YAML, strips terminal control bytes, and escapes Markdown table cells.

Use the installed renderer for a terminal progress bar and per-prompt checklist:

```bash
uv run "${CRUX_PLUGIN_ROOT}/scripts/visualize-run-progress.py" PB-NNNN
uv run "${CRUX_PLUGIN_ROOT}/scripts/visualize-run-progress.py" PB-NNNN --run RUN-NNN
```

The terminal view writes nothing, including no log operation. Its counts derive from prompt states: done, skipped, and blocked count as terminal. It prints `module_tag` only when the run's `book_content_hash` matches the book. Do not pass `--markdown` for an ordinary status or progress question.

## Answer status and resume questions

Read the selected book and snapshot. Report the book id and title, run id and status, current prompt number, verbatim title and state, and the lowest-numbered pending prompt's **full prompt text from the book**. The snapshot contains only the prompt title. Name `cycle_kind` when present. For an abandoned run, name `abandonment.kind`: deliberate abandonment permits archival; superseded abandonment does not. If no pending prompt remains, say so and distinguish a completed run from one with nonterminal states. On a hash mismatch, say the displayed book text can differ from the frozen run plan.

Report the next action without executing it, after checking the book **and run** format. A YAML book with no `current_run` may start; a YAML `in_progress` run may advance; a completed or deliberately abandoned YAML run may archive; a superseded YAML run may start a new run. A Markdown book or run cannot start, advance, abandon, or archive in this version. For an `in_progress` Markdown run, name both paths and the pinned `v3.23.2` recovery route in `../SKILL.md`: finish and archive there only if every remaining prompt can be truthfully completed, then convert book before run. The tag has no verified Markdown-abandon route. If the run cannot finish, report it as stranded, readable, and non-retryable on the current distribution; preserve its `in_progress` state and original bytes, and offer separate YAML work. Never advise marking unfinished prompts terminal merely to migrate. For a completed or already archived Markdown record, give the eligible tagged conversion route without suggesting a current-version archive. Status never calls advance. Check the answer against the snapshot; book, run, index, artifact and log bytes remain unchanged.

## Implementation intent, delivery and source state

A format-two run's successful committed bindings establish reviewed intent only.
Use `implementation-decisions.py query` to answer what was reviewed,
what independent delivery evidence reports, and what the queried source proves.
Do not infer current implementation from an approved proposal, completed prompt,
latest timestamp or branch label. Unimplemented, reverted, diverged and unrelated-lineage
source states remain distinct from a partial delivery; unavailable proof remains unobserved. Status itself
writes no approval, result, lifecycle transition or current-state claim.

## Explicit Markdown progress

Only when the user asks to persist progress as Markdown, invoke the renderer with `--markdown`. It writes or refreshes `run-RUN-NNN-progress.md` beside the snapshot. `--terminal --markdown` may also print the terminal view. The artifact is wholly regenerated in snapshot prompt order and byte-stable for the same book and run state. It has no timestamp; hand edits are replaced. The book and run remain unchanged.

Only after the Markdown artifact succeeds, invoke `log-work` in log-only mode once. Read its installed `SKILL.md` and `references/log-only.md` before that write. Use canonical `promptbook` as the operation, `visualized PB-NNNN/RUN-NNN` as the subject, and one line saying which progress artifact was regenerated as the body. Supply one aware timestamp with its original offset for replay. The log-only writer adds one promptbook log operation to `<docs_dir>/log.md`; it changes no journal or journal index. The renderer CLI itself does not log, so do not add a second entry. If the log write refuses after the artifact was written, report that partial outcome and the artifact path, then replay the exact log request after correction. Never call `log-work` for a terminal render or status answer.

Verify the artifact bytes across an unchanged repeat render, the one deliberate log operation, and unchanged book and run bytes. A status or terminal view writes nothing.
