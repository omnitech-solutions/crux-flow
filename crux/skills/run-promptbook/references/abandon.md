# Abandon a promptbook run

Read this reference before the first abandon side effect. Follow the shared obligations in `../SKILL.md`.

## Abandon rule (critical)

If the user wants to edit `prompts:` in an active YAML book while a YAML run is in progress: REFUSE. Tell them:

> The active book's `prompts:` list is frozen while RUN-NNN is in progress. To change the YAML plan, abandon its YAML run and author a fresh book: `run-promptbook abandon PB-NNNN --reason "<why>"`, then `author-promptbook`. The new book cites its predecessor in its `goal`/`strategy` prose; the abandoned run keeps its snapshot as the record of how far the old plan got.

If the active book is Markdown, refuse the edit and the abandon operation before mutation. The tagged public `v3.23.2` release can finish a run only when its remaining prompts can truthfully finish; it cannot deliberately abandon one. Preserve an unfinishable run unchanged as readable, unresolved history and author separate YAML work.

Editing a YAML plan mid-run breaks the snapshot's reference to it — `book_content_hash` binds the run to the plan it started against, and an in-place edit shows up as a CHK-PB-BIND mismatch that is never auto-fixed. Abandon the YAML run and re-author.

The mid-run fork path is deleted. `forked_from` remains an accepted book field that is always `null`, and nothing writes it. State the consequence plainly when it comes up: the successor relation now lives in prose and no longer resolves mechanically.


## Preserve formal implementation history

For format two, abandon preserves every reviewed revision, approval binding and
append-only result. A successor may replace the approach without revoking the earlier
Implementation Decision. Changing frozen kind, slots, scope or constraints requires a
successor plan. Changing reasoning requires fresh exact-revision approval at a lawful
gate; absence of such a route requires escalation or a successor. Do not rewrite
accepted architectural bodies or archived books/runs to make the new choice fit.

## The pipeline (mode: abandon)

Abandoning is a **run-level** act. It is how a book that will not finish still closes cleanly, and it is the signal `archive-promptbook` reads.

1. **Locate the in-flight snapshot.** Identify the active book's path and its `current_run` snapshot path. Refuse if `current_run` is `null`. If either path is `.md`, apply the shared refusal in `../SKILL.md` before any write. Both YAML documents require `format_version`.
2. **Record the abandonment in the YAML run.** Invoke
   `uv run "${CRUX_PLUGIN_ROOT}/scripts/advance-run.py" <run-RUN-NNN.yaml> --abandon --reason "<one line>" --book <active-book.yaml>`.
   Refuse if the run is already terminal or carries an abandonment record; never retrofit one. The run records:
   ```yaml
   abandonment:
     kind: deliberate
     at: <ISO 8601 UTC>
     reason: "<one line>"
   ```
   It leaves every prompt element's `state` untouched. That is deliberate: the prompt left `running` or `pending` is the evidence of how far the run got, and rewriting it would erase the record.
3. **The book keeps `current_run`.** Only a book's current run can authorize its archive, so the pointer stays until `archive-promptbook` nulls it. Only `current_prompt` is nulled here.
4. **Two surfaces, as ever.** No index regeneration, no log entry. The `promptbook` op comes at archive.

**The record is written when the abandonment is taken, and never retrofitted.** `advance-run.py` refuses a run that is already `completed` or `abandoned`, or that already carries an `abandonment` record. If a run ended without one, it ended without one — a book cannot be made archive-eligible after the fact by backdating a reason.

**`kind: deliberate` versus `kind: superseded`.** Only `deliberate` — this mode — confers archive eligibility. `superseded`, written by a later run's start over a stale run, confers none. The two share a `status` and are told apart only by `kind`.
