# Issue or advance a gate prompt

Read this reference when the gate-information query (`advance-run.py --gate-info`) reports the class `council`, `module-close` or `independent-review` for the prompt you are about to issue or advance. Run that query before every prompt: section 3 and `advance.md` give the command. Follow the shared obligations in `../SKILL.md`. The procedure for a prompt that is no gate stays in `advance.md`.

## 1. The two processes

Council deliberation runs only through the council runner, and its council record is the only evidence a council gate accepts.[^council] Independent review is a reviewer's examination, and its reviewer report is the only evidence an independent-review gate accepts.[^review] Neither satisfies the other's gate.

Names used below: the council runner is `run-council.py`; the gate check is `advance-run.py`; the conductor is the agent that runs the book's prompts in order. A council attempt is one execution of a council round by the council runner, from its claim to its result. Its attempt record is the record the council runner commits to claim the round, before any council request. An attempt is open until a council record that names it resolves it or an owner's void-attempt record voids it. Preflight is the council runner's check of the question and subject inputs before the claim, and a preflight refusal is its refusal of an input there. A convening prompt is a prompt that runs the council runner.

## 2. The gate classes

The gate check classifies a prompt by its position in the book, never by its prose and never by its `--result` text.

| Class | Position | Evidence the gate accepts |
|---|---|---|
| `council` | ordinal 2 of an `adr-*`, `implementation-*` or `verify-*` module; the `verify` phase of a `patch` book | council records naming the council runner as writer, bound to this book, run and module, or to this prompt in a patch book |
| `module-close` | ordinal 4 of an `adr-*`, `implementation-*` or `verify-*` module | the deciding record: a council record, or in an `adr-*` module a refutation record |
| `independent-review` | ordinal 1 of a `review-*` module; the `review` phase of a `patch` book | reviewer reports whose reviewed paths are clean |
| `internal-review` | ordinal 4 of a `dev-*` module | none: this is no gate |
| `unclassified` | every other prompt; every prompt of a format-one `cycle_grandfathered` book; every prompt of a format-one book with no `cycle_kind` when `cycle_fields` is `run-start` (when it is `unbound`, the module tag or phase decides the class) | none: this is no gate |

A `dev-*` module carries no council gate. A unit review there is independent review and is never a council.

The deciding record is the in-scope council or refutation record committed last.[^order] The gate orders records by the commit that introduced each one on the history reachable from HEAD, never by `written_at`. Attach the deciding record at every council and module-close advance.

Each record has exactly one introducing commit: the one non-merge commit that gives its path its current bytes. Normally that is the commit that adds the path, and nothing else touches the path afterward. For a record a hook altered and recovery repaired with `--owner-commit-pending` (section 4), the introducing commit is recovery's commit. Commit each record in its own commit. Every path the committed history has held as a council or refutation record of the run, in any module, must have a single introducing commit and must still exist: a committed deletion of another module's record of the same run stops this gate too. The gate reads ordering, ties, contradictions and descent per gate scope, which is the module for a council or module-close gate and the prompt for a `patch` verify phase. A path that cannot be read from the history fails closed, as section 8 states.

Shallow history, a grafts file, and a partial clone whose history blobs are not stored locally stop the gate at stop 4 with the reason "record order is unavailable". The order check never fetches objects. This stop clears once the history is complete: run `git fetch --unshallow` or remove the grafts file, or fetch the missing objects (a full clone, or `git fetch --refetch` after unsetting the partial-clone filter), then advance again.

## Format-two formal implementation approval

Validate format two before classifying any prompt, regardless of run-start availability.
Each dedicated implementation module requires a declared slot. Verify and patch
may declare empty slots for incidental choices. An implementation module's council
kind is `implementation`, including in a combined architectural book.

After each initial or revised formal subject write, record the run-work witness
using section 4 before committing the subject. This includes Implementation Decision
revisions and migration batches. Never create a witness after a refusal.
For a selected formal revision, commit it and pass
`--implementation-revision <decision-path> --subject <decision-path> --retain-subjects`
to the existing runner. The question names its path and digest and explicitly asks
about architectural conflict. Dedicated implementation review assesses Completeness,
Correctness, Consistency, Clarity and Security. Verify retains its five diagnosis
dimensions; patch retains its five dimensions including blast-radius proportion.
Diagnosis-only approval and subject inclusion alone establish no implementation approval.
Independent review evaluates question quality and compares delivery to reviewed reasoning.

The latest deciding counted converged receipt must itself retain the selected revision.
At ordinal four of implementation/verify, or the patch verify phase, pass
`--implementation-revision <decision-path>` on the successful advance. Only this
writer appends an exact binding atomically with DONE after full history and live
registry checks. Commit the close and context before results consume it. Raw receipts,
uncommitted bindings and fabricated DONE cannot supply approval. Materially changed
reasoning requires fresh review at a lawful gate or escalation/successor; never reopen DONE.
Keep earlier revisions and approval bindings immutable. Retrieval selects lawful
bindings by append order, not timestamp. Approval remains reviewed intent.

Implementation modules admit three counted council rounds and existing lawful
above-three owner exceptions. ADR refutations, adjudicator closure and ADR-round-three
exceptions remain ADR-only. ARCHITECTURAL in implementation or verify takes stop 4
and routes to the architectural process. A later convergence cannot erase a permanent stop.

A migration batch uses `--migration-batch <batch-path>` with that same path as
`--subject` and `--retain-subjects`. The two formal selectors are mutually exclusive.
Keep the migration-batch role, declared approval slot, exact path and batch digest
distinct from an implementation revision. The successful advance selects that same
batch with `--migration-batch`. A retained batch in another role or slot grants no approval.
Combined books dispatch by their structural module kind, never `council_kind: combined`.

Live execution of both book formats uses the current attempt-aware gate.
New formal closes retain context three/profile four.[^frozen] Historical context two/profile three
and context one/profile two are immutable and replay-only; the committed context selects its supported profile.
Never choose a profile from book format or substitute the live registry for historical context.
Unknown or inconsistent context refuses approval. Preserve the frozen book and its
hash; the historian records any execution addendum separately.

## 3. Run the gate-information query before you issue the prompt

Before you issue or run any prompt, ask the gate check what it requires. A commander holds no shell, so it dispatches the historian to run the query. The query is read-only and writes nothing:

```bash
uv run "${CRUX_PLUGIN_ROOT}/scripts/advance-run.py" <run-RUN-NNN.yaml> --book <book.yaml> --gate-info --prompt <n>
```

The JSON reports the prompt, its `class`, `module_tag`, `ordinal`, `phase`, `cycle_fields` (`run-start` or `unbound`) and `requires`, the evidence the prompt needs. The result always carries `adr_acceptance_pending`: null in a format-one run, otherwise a list with one entry per pending ADR of a closed `adr-*` module (an ADR not Accepted, Deprecated or Superseded, or unreadable) plus one `unidentified` entry, with a null `path`, for a module that names no ADR file and whose records name no ADR file. Each entry carries a `remedy`, `transition-adr` or `owner`. Do not issue the prompt while the list is non-empty.

When the prompt's text carries a council alternative the project has withdrawn, the result carries a non-empty `withdrawn` and a `correction_notice`. Follow the notice, not the prompt text. The notice says that council deliberation is the council runner obtaining verdicts from three providers, that every seat assesses every dimension the prompt names, and that a council which cannot run stops the gate for the owner. The book's text is never rewritten.[^correct]

## 4. The council-gate procedure

1. Run the gate-information query in section 3 and follow any correction notice. Finish an open Git merge or other sequence before convening council. Use a harness that permits the runner and recovery to commit; a parent commit cannot substitute for their verified writes.
2. Commit every subject. A subject must be tracked and carry no staged or unstaged change. A step that writes a file it later passes as a subject records a run-work witness after each write:

   ```bash
   uv run "${CRUX_PLUGIN_ROOT}/scripts/run-work-witness.py" record <run-RUN-NNN.yaml> --prompt <n> --path <path>
   ```

   The witness records the path and the sha256 of the bytes the run wrote, in `<run_dir>/run-work-witness.json`. It is never council evidence. It lets the conductor repair a subject the run left uncommitted (the preflight repair below). Never record a witness to repair a preflight refusal: a subject with no witness taken at its write goes to the owner. A dirty subject with no matching witness goes to the owner too.

   The witness writer does not commit the witness file. After each verified council record commit, the council runner makes one best-effort commit that owns `<run_dir>/council-preflight.jsonl` and `<run_dir>/run-work-witness.json`. It includes each file only when the file exists as a regular file and its bytes differ from HEAD's. The commit carries the fixed message `crux council diagnostics: <book> <run> prompt <n>`. When git refuses it, the council runner prints one stderr line naming the code and any moved or staged paths, and keeps the record's own exit code. The council gate is unaffected, so the conductor continues the run. When that line names moved paths, the conductor reports them to the owner, who follows the remedy order under "After a commit timeout or moved work". When it names a staged file, the conductor unstages it with the `git restore --staged` command the line gives, because a staged file makes every later diagnostics commit refuse. A line that a retryable refusal leaves is carried by the next such commit, or by the conductor's own commit of the run snapshot. Recovery makes no diagnostics commit. Until a commit carries the witness file, `git clean` or `git stash -u` can remove it, and a subject whose witness is gone goes to the owner, which fails closed. A new run of the book finds the last run's witness in the shared run directory. Its first `record` replaces that witness and names the replaced run as `replaced_run_id`.
3. Choose the round number `<R>`: the number of council records with outcome `ran` already in this module (in a patch book, this prompt), plus one. In an `adr-*` module a valid adjudicator refutation record fills place 3: among other checks, it names the module's round-2 council record, and its recorder wrote no conductor refutation in the module. A could-not-run record takes no place, so after you fix its cause you reconvene with the same round number. A preflight refusal and an attempt record take no place either. The council runner counts with the gate check's own function and refuses a mismatch before any council request (see the exit table).
4. Write the question file, for example `<run_dir>/council/<RUN>-p<N>-r<R>-question.md`. List the dimensions and state that every seat assesses every dimension.[^seats] Do not fence a subject in the question: pass each subject with `--subject`, and the council runner fences it as data with its sha256. A seat sees only the question and the subjects, so pass every file a seat must read as a `--subject`.
5. Run the council runner as a background command. A round takes minutes. Run it from the repository root, because paths are read relative to the working directory:

   ```bash
   uv run "${CRUX_PLUGIN_ROOT}/scripts/run-council.py" <run-RUN-NNN.yaml> --round <R> \
     --question <question.md> --subject <path> [--subject <path> ...] \
     [--prompt <n>] [--book <book.yaml>] [--retain-subjects] [--max-tokens 64000] [--timeout 600]
   ```

   `--prompt` defaults to the run's current prompt, and any other value is refused (see the exit table). `--book` names the book when the run layout does not find it. `--retain-subjects` copies each subject beside the record. `--timeout` is the per-seat-call deadline, in seconds: it bounds each call to a seat, never the council attempt. The council runner appends the vote instruction and the finding tags itself.

   The council runner commits the attempt record and the council record itself. After preflight, and before its secret scan, key check, seat assignment and first council request, it commits an attempt record under `<run_dir>/council/` that claims the round. It holds a lock on that record while it runs. It makes no request until HEAD holds the attempt record's bytes.[^attempt] When the round ends it writes the council record, commits it and verifies that the committed bytes are the bytes it computed.[^match] Each of its commits holds only the paths it owns, leaves every other staged and unstaged change as it was, keeps hooks enabled and carries a fixed message with no model output. Each git call has a 120-second bound.
6. Read the exit code.

   | Exit | Meaning | Next step |
   |---|---|---|
   | 0 | a record with outcome `ran` was written, committed and verified; the summary names the `record` and its `attempt` | attach the record |
   | 1, with `"refused": "preflight"` and `"record": null` | a preflight refusal: an input failed the checks before any claim and any council request. Nothing was written to `council/` and nothing was committed. `causes` lists each input by field name, its `code` and its `repair`; `refusals_at_prompt` counts the refusals at this prompt, and `bound` is 3 | apply each `repair` (below), then run again at the same round number; no gate has stopped |
   | 1, with `"refused": "attempt-open"` and `"record": null` | an open attempt in this module holds the scope; `attempt` names its record; nothing was written | follow "Recovery and the open attempt" below; never claim a new round past it |
   | 1, with `"refused": "round"` and `"record": null` | the round number was refused: the JSON carries `given_round` and `expected_round`; nothing was written and no council request was made | run again with `--round` set to `expected_round`; no gate has stopped |
   | 1, with `"refused": "prompt"` and `"record": null` | `--prompt` is not the run's current prompt: the JSON carries `given_prompt` and `current_prompt`. Nothing was written, and the refusal is not counted toward the preflight bound | run again with the run's current prompt, or omit `--prompt`; no gate has stopped |
   | 1, with a record path in the JSON | a record was written and committed, and its gate will stop: a could-not-run record (including `preflight-needs-owner` and `preflight-retries-spent`), or a `ran` record whose full write the secret scan refused (a scan-refused round) | attach it with `--outcome blocked`; the gate stops the run |
   | 1, with no `record` key, or with empty stdout | a record was written and committed, but its summary matched the secret scan and was reduced or withheld | find the record named on stderr or the newest council record under `council/`, and attach it |
   | 2 | no committed council record; stderr names the cause, never a secret. When a commit times out or a hook fails it, stderr also names every path outside the council's own that the commit moved, and `refs/stash` when the stash moved | When stderr names `timeout`, or names outside work the commit moved, the owner's remedy under "After a commit timeout or moved work" comes before recovery: the conductor reports a contradicted-premise stop for the owner. The owner first restores the set-aside work (`git stash list`, or a pre-commit framework's backup patch) and then removes a stale `index.lock` in the git directory. Start recovery only after the owner reports both steps done. Exit 2 never means nothing was written: an attempt record or a council record can stay uncommitted in the working tree, and a pending copy can remain under the git directory. When stderr names a claimed attempt, follow "Recovery and the open attempt" below with `--prompt <n>`. When it names none, the prompt cannot advance: the conductor reports why, as a contradicted-premise stop for the owner. Never convene another round over a claimed attempt |
   | no exit code, a signal, or any code but 0, 1 and 2 | the council runner stopped before it reported (killed, crashed or lost by the harness); an attempt may be claimed, committed or resolved | follow "Recovery and the open attempt" below with `--prompt <n>`; never convene another round until recovery reports |

7. Attach the record. Do not commit, stage, edit or delete an attempt record or a council record: the council runner commits both, and recovery commits a record the council runner could not. The gate refuses a record that is not committed (HEAD, index and working tree must hold the same bytes), a committed record that is deleted or modified in the working tree, and a `.json` path under the run's `council/` directory that the run snapshot names in a prompt's `artifacts` and that is missing. That last refusal holds at every later council gate of the run, in every module, so a record an earlier prompt named must stay in the tree. Commit a refutation or owner-exception record yourself (section 5). Then attach the record to the advance with `--artifacts`. The gate check judges the record, never the `--result` text.[^gate] A council record that names an attempt counts only when its committed bytes hold their seal and match the attempt's book, run, binding, round, question, subjects and registry.[^match]

   ```bash
   uv run "${CRUX_PLUGIN_ROOT}/scripts/advance-run.py" <run-RUN-NNN.yaml> --book <book.yaml> \
     --outcome done --result '<one line>' --artifacts <run_dir>/council/<record>.json
   ```

   Pass each reviewer- or conductor-written value single-quoted, writing an embedded `'` as `'\''`. Never put the value itself inside double quotes: the shell expands backticks and `$(...)` there. The reviewer-report writer also reads its text from files (section 6).
8. When the advance is refused with verdict `route` or `stop`, re-run it with `--outcome blocked` and the same `--artifacts`. The gate check then routes or stops. `--outcome done` writes nothing on any verdict but `pass`. When the gate stops on an open attempt, follow "Recovery and the open attempt" below before you advance it blocked.

**Preflight repair and the retry bound.** A preflight refusal the conductor may repair is no council outcome, and the conductor repairs it.[^defer] It writes no council record. Each cause carries one `repair`:

- `retype`: the path is missing, lies outside the repository, passes through a symlink, carries a control character, or names a file that is not UTF-8. Correct the path and run again.
- `commit-run-work`: the subject is the run's own work, left uncommitted, and its working-tree bytes equal its latest witness. Commit exactly that path with the witness writer, which re-checks the bytes first, then run again:

  ```bash
  uv run "${CRUX_PLUGIN_ROOT}/scripts/run-work-witness.py" commit <run-RUN-NNN.yaml> --path <path>
  ```

The witness writer's `commit` refuses with `unattributed`, `council-path`, `unwitnessed`, `witness-mismatch`, `mixed`, `commit`, `witness-invalid`, `witness-binding`, `missing`, `invalid` or a path refusal. `record` refuses with `secret-scan`, `council-path`, `witness-invalid`, `witness-binding`, `missing`, a path refusal or `prompt`:

| Refusal | Meaning |
|---|---|
| `unattributed` | the path neither changed between `base_commit` and HEAD nor named in any prompt's artifacts |
| `council-path` | the path lies under `<run_dir>/council/`, which only the council runner and recovery commit |
| `unwitnessed` | no witness entry names the path |
| `witness-mismatch` | the working-tree bytes differ from the latest witness entry |
| `mixed` | the index holds a staged version of the path that is neither HEAD's nor the witnessed bytes |
| `commit` | git refused the commit; its `code` is `index-locked`, `sequence-in-progress`, `hook-or-commit-failed`, `timeout` or `mismatch` |
| `witness-invalid`, `witness-binding` | the witness file is invalid, is a symlink or is bound to another book or run |
| `missing` | `record` or `commit` found no regular file at the path |
| `invalid` | `commit` refused the path, its bytes or its commit message before running git |
| `secret-scan` | the path's text matches the secret scan, so `record` writes nothing and never echoes the path |
| `outside-repo`, `git-dir`, `symlink`, `control-character` and the like; `prompt` | the path or the prompt is not one the writer accepts |

A witness writer refusal has one next step. The agent holding a shell acts, and the conductor reports every stop:

| Refusal | Next step |
|---|---|
| `commit` with `"refused": "commit"` and `index-locked` | The commit checks the lock before it stages anything. The conductor waits for any git command the run started to finish, and the agent holding a shell runs `run-work-witness.py commit` once more. That is not the council runner, so it spends no retry. When the lock persists, it is stale: the conductor reports a contradicted-premise stop for the owner |
| `commit` with `"refused": "commit"` and any other `code` (`sequence-in-progress`, `hook-or-commit-failed`, `timeout`, `mismatch`) | The cause is the owner's. The conductor reports a contradicted-premise stop for the owner, and no agent runs the council runner, which would spend a retry. When the `code` is `timeout`, or the refusal carries `moved`, the owner follows the remedy order under "After a commit timeout or moved work", and the refusal's `moved` and `staged` lists name what the call touched. A `mismatch` means the committed, staged or working-tree state differs from what the call computed, and the commit may have landed: the owner checks `git log` for the path before the next commit. After the owner fixes the cause, commit the path again |
| `commit` with `unattributed`, `unwitnessed`, `witness-mismatch`, `mixed`, `council-path`, `witness-invalid` or `witness-binding` | No repair resolves it. Run the council runner again: it commits a `preflight-needs-owner` record. Attach it with `--outcome blocked` |
| `commit` with `invalid` | The conductor reports a contradicted-premise stop for the owner. Do not retry |
| `record` with `secret-scan`, `witness-invalid` or `witness-binding` | The conductor reports a contradicted-premise stop for the owner. Do not retry |
| `record` with `council-path` | Name a subject outside `<run_dir>/council/`, where only the council runner and recovery commit |
| `record` or `commit` with `missing` or a path refusal, or `record` with `prompt` | Correct the path or the prompt, and run the writer again |
| exit 2 from `record` or `commit` (an unreadable run snapshot, no `fcntl`, a book that cannot be bound, a file removed mid-call, a malformed crux env file) | An environment fault. The conductor reports a contradicted-premise stop for the owner, and runs the writer again once the owner fixes it |

The council runner appends each refusal, codes and field names only, to `<run_dir>/council-preflight.jsonl`. That log is never council evidence. Each line carries the run id, and the count reads only this run's lines, because every run of a book shares the run directory. Deleting the log buys retries, never a pass. The count is per convening prompt, an adr module's address-findings prompt included, and a claim at that prompt resets it. The third preflight refusal at one convening prompt makes the council runner write and commit a could-not-run record with the code `preflight-retries-spent`, and exit 1. Attach it with `--outcome blocked` at that prompt. The gate check then stops the run at the escalation-loop stop.

A refusal the conductor may not repair is not returned as retryable. The council runner writes and commits a preflight could-not-run record with the code `preflight-needs-owner`, naming codes and never a value, and exits 1. Attach it with `--outcome blocked`. The gate check then stops the run at the contradicted-premise stop. Neither preflight could-not-run record takes a round place. The record names each cause by field and code:

| Code | Meaning | Owner's remedy |
|---|---|---|
| `env-file` | a subject is a `.env`, `.env.*`, `*.env` or `.envrc` file, or the crux env file | remove it from the subjects, because its values never reach a seat |
| `crux-home` | a subject lies in the crux home directory | copy the content a seat needs into a tracked file in the repository, and name that file |
| `ignored` | git ignores a subject | track the file and commit it, or name another file |
| `mixed` | the index holds a staged version of a subject that differs from its working-tree version | decide which version holds, then commit it |
| `unattributed` | a dirty subject neither changed between `base_commit` and HEAD nor named in any prompt's artifacts | review the change and commit it, or discard it |
| `unwitnessed` | a dirty subject is a file the run wrote, but no witness entry names it | review the subject and commit it |
| `witness-mismatch` | a dirty subject's bytes differ from its latest witness entry | review the edit and commit the subject |
| `witness-invalid` | the witness file is invalid, is a symlink or is bound to another book or run | delete the witness file, review the subject and commit it |
| `changed-while-read` | a subject's bytes changed while the council runner read it | stop whatever writes the subject, commit it and run again |
| `unreadable-state` | git could not report a subject's state | repair the repository (a held index lock, permissions), then run again |
| `implementation.scope:undeclared-governing-constraint` | a declared-empty constraint set's scope overlaps the path scope of a live Accepted rule; the record names each overlapping rule handle | run `validate-promptbook.py` before run start: it names each overlapping rule while the slot can still change. After run start the slot is frozen, so abandon the run and author a successor book whose slot cites the rule, or change that rule's lifecycle first |

After the owner fixes the cause or tells you to resume, reconvene at the same round number.

**Recovery and the open attempt.** An attempt stays open after a persistence failure: a commit that fails or exceeds its 120-second bound, or a council runner that stops after its claim and before it reports. Start recovery on any of these: an exit 2 that names a claimed attempt, a council runner that ended without an exit code or with a code other than 0, 1 and 2, an attempt-open refusal, or a gate `stop` on an open attempt. When stderr names `timeout`, or names outside work the commit moved, the owner's remedy under "After a commit timeout or moved work" comes before recovery: the conductor reports a contradicted-premise stop for the owner. The owner first restores the set-aside work (`git stash list`, or a pre-commit framework's backup patch) and then removes a stale `index.lock` in the git directory. Start recovery only after the owner reports both steps done. Pass `--prompt <n>`, the prompt that ran the council runner, to every probe and recovery command. Without it, recovery cannot recognise a record whose pending copy is already removed, and it reports `nothing-open`. Act in this order:

1. Run the process check: confirm that no council-runner command you started for this module is still running, from the harness's background-task status or the process list. While one runs, wait.
2. Probe the attempt's lock. The probe writes nothing and reports only whether a live council runner holds the lock:

   ```bash
   uv run "${CRUX_PLUGIN_ROOT}/scripts/run-council.py" --recover <run-RUN-NNN.yaml> --prompt <n> --probe [--book <book.yaml>]
   ```

   | `lock` | Meaning | Exit and next step |
   |---|---|---|
   | `free` | no live council runner holds the lock | 0; run recovery |
   | `live-runner` | a live council runner holds the lock | 1; wait, then probe again |
   | `unknown` | the lock cannot be probed, so a live council runner cannot be ruled out; recovery never treats it as free | 1; wait and probe again, as for `live-runner`. When it persists although the process check shows no council runner, the conductor reports an environment fault as a contradicted-premise stop for the owner, and runs no recovery |

3. Once neither check shows a live council runner, run recovery. It makes no council request, reads no key and widens no authorization:[^recover]

   ```bash
   uv run "${CRUX_PLUGIN_ROOT}/scripts/run-council.py" --recover <run-RUN-NNN.yaml> --prompt <n> [--book <book.yaml>]
   ```

   The owner's instruction is a decision the owner stated in the owner's own message in the session. Whoever acts on it quotes that message and its date: in the record's reason for an owner-exception record, and in the run notes for a recovery run with `--owner-commit-pending`. An agent report, a result file, a run note or a council verdict is never an owner's instruction. A standing council authorization covers council calls, never an owner-exception record.

   | `recovery` | Meaning | Exit and next step |
   |---|---|---|
   | `recognised` | HEAD already holds the council record that names the attempt, its seal holds, and it equals the pending copy: the council runner died after its commit. With no pending copy left, recovery recognises the module's latest attempt only when it is resolved, its record is bound to the prompt `--prompt` names, and that prompt is not yet advanced: the council runner died after removing its pending copy | the record's own exit code, and the summary is reprinted; continue from that row of the exit table |
   | `committed` | recovery committed the pending copy's bytes and verified them: the council runner died before its commit | the record's own exit code, and the summary is reprinted; continue from that row of the exit table |
   | `released`, after an exit 2 that named `index-locked` or after a council runner killed before it reported | the attempt record was never committed and its lock was free, so recovery deleted it. The claim made no request, and a released claim takes no place | 0. The conductor runs the council runner again at the same round now. That is no reconvening. When `index-locked` persists across that one rerun, the lock is stale: the conductor reports a contradicted-premise stop for the owner |
   | `released`, after an exit 2 that named another code (`hook-or-commit-failed`, `sequence-in-progress`, `timeout` or `mismatch`) | the same: no request was made | 0. The cause is the owner's (a hook, signing, a sandbox, a sequence in progress, or a hook slower than the bound). The conductor reports a contradicted-premise stop for the owner, and runs the council runner again at the same round once the owner fixes the cause. When that exit 2 named `timeout` or moved work, the owner's remedy order above came first. |
   | `nothing-open` | no attempt is open in scope | 0. When the command lacked `--prompt`, run it again with `--prompt`. After an exit 2, the conductor reports its cause. After a council runner that ended without an exit code or with a code other than 0, 1 and 2, nothing was claimed and no round was spent: run the council runner again at the same round |
   | `live-runner` | a live council runner holds the attempt's lock | 1; wait, then start again at step 1 |
   | `index-locked` | git's index lock is held; recovery changed nothing | 1. The conductor waits for any git command the run started to finish, then runs recovery again. A lock that persists leaves the attempt open: apply the open-attempt rule below. No agent deletes `.git/index.lock` |
   | `mismatch` | the working-tree record differs from the bytes the council runner computed, the pending copies conflict, a seal fails, or HEAD differs from the pending copy; recovery changed nothing | 1; the attempt stays open: apply the open-attempt rule below. The owner fixes the hook that altered the record. Then, at the owner's instruction, the conductor runs recovery with `--owner-commit-pending` |
   | `unproven` | no pending copy proves the record in the working tree, or no council record exists; recovery changed nothing | 1; the attempt stays open: apply the open-attempt rule below. Do not run recovery again, because it reports `unproven` again. The owner voids the attempt (section 5), after removing any record left in the working tree, or abandons the run |
   | `commit-refused`, with `"commit_landed": true` | recovery's own commit landed, HEAD moved, and HEAD holds bytes not proven to be the pending copy. The pending copy is kept | 1. Follow this row before the open-attempt rule. When `code` is `timeout`, or the report carries `outside_moved`, the conductor reports a contradicted-premise stop for the owner. Once the owner has followed the remedy order under "After a commit timeout or moved work", the conductor runs recovery once more: it recognises the commit when HEAD holds the pending copy's bytes. When `code` is `mismatch`, a hook altered the record: follow the remedy in the `mismatch` row. For any other `code`, run recovery once more at once |
   | `commit-refused`, without `"commit_landed": true` | git refused recovery's own commit, and the report's `code` names why. HEAD did not move. The report names anything else that moved: `"index_moved": true` with the `staged` paths, `"index_lock_left": true` (a timeout leaves `index.lock` and the staged record), `worktree_moved` or `"pending_moved": true`. Where the report carries no such field, the index, the record's files and the pending copy are as they were | 1; the attempt stays open: apply the open-attempt rule below. The owner fixes the named cause, and after a timeout, or when the report carries `outside_moved`, follows the remedy order below. Then, at the owner's instruction, the conductor runs recovery again |

   **Report fields.** A `commit-refused` report carries `outside_moved`, the outside paths the refused commit found changed (with `refs/stash` when the stash moved), and `owned_staged`, the owned paths it left staged. Each key appears only when it names a path. `outside_moved` can instead hold the marker `<outside state not compared>`, which means the refused commit could not compare the outside state: check `git status` and `git stash list`.

   **The open-attempt rule.** After `commit-refused` with `"commit_landed": true`, follow that row first. Otherwise, an attempt that recovery leaves open holds the gate. When HEAD holds the attempt record, the conductor advances the prompt `--outcome blocked` with the attempt record attached, and the gate check stops the run at the contradicted-premise stop for the owner. When HEAD does not hold the attempt record, the gate refuses that advance. The prompt then cannot advance: the conductor reports why, as a contradicted-premise stop for the owner.

   **Recovery exit 2.** Recovery exits 2 on an environment fault: no `fcntl`, an unreadable git repository, a malformed crux env file, a lock probe that fails, or a run that cannot be bound. Stderr names the cause and every act this invocation already made. The attempt stays unresolved. The conductor reports a contradicted-premise stop for the owner, who fixes the environment. Then the conductor runs recovery again.

   **After a commit timeout or moved work.** A commit that exceeds its 120-second bound is interrupted. Git receives SIGINT, which lets a hook framework restore the work it set aside and lets git remove its own `index.lock`. Git is killed after a 10-second grace, and so is a hook process that ignored SIGINT and is still running when the grace ends. A timeout can still leave `index.lock`, owned paths staged, and a hook's stash or backup patch holding the user's unstaged work. The council runner then compares the outside index, the working tree and `refs/stash` with its snapshot. Its exit 2 stderr names every moved path, and `refs/stash` when the stash moved. When the comparison cannot finish, stderr says the outside state was not compared (`<outside state not compared>`). A commit a hook fails (`hook-or-commit-failed`) gets the same comparison, because a hook can set the work aside and exit before it restores it, and its stderr names moved paths the same way. The owner's remedy, in this order:

   1. Look for the set-aside work with `git stash list`, and for a pre-commit framework's backup patch under its cache directory. Restore it.
   2. Only then remove a stale `index.lock` in the git directory.
   3. Then the conductor runs recovery, once the owner says the first two steps are done. A timeout with nothing moved still leaves the attempt open, so the conductor runs recovery then too.

A persistence failure is never a reason to convene another round,[^recover] and a fresh round never closes an open attempt.[^attempt] Only the attempt's own verified council record resolves it, and only an owner's void-attempt record (section 5) voids it. The owner's remedy for a record a hook altered is to fix the hook and then, at the owner's instruction, run recovery with `--owner-commit-pending`, or to abandon the run. Never delete the altered record and add it again: a delete followed by a second add leaves the record with no single introducing commit, and the module stops permanently.

A council that cannot run stops the cycle with DEFER_TO_HUMAN. The council runner writes and commits that record and exits 1. Attach it with `--outcome blocked`; the gate check then stops the run at the contradicted-premise stop, or at the escalation-loop stop for `preflight-retries-spent`.[^defer] After the owner fixes its cause or tells you to resume, reconvene at the same round number and advance the same blocked prompt `--outcome done` with the new record attached. When even the could-not-run record cannot be written, the prompt cannot advance; report why. No subagent casts a seat's vote, and a reviewer fallback never satisfies the gate. The council runner names why in the record's `refusal_reason.code`: `preflight-needs-owner`, `preflight-retries-spent`, `secret-scan`, `no-key`, `refused-assignment` (a registry fault, or a missing, overridden, unknown, retired or ineligible assignment), `fewer-than-three-providers` or `no-quorum`. A format-1 record from an older council runner can carry `refused-input` or `refused-subject`. A served-model or served-provider mismatch errors that seat only; the council defers when the errored seats leave no quorum.

Three kinds of stop clear. A could-not-run stop clears when you fix its cause, including input the secret scan refused before any gateway call,[^scan] and reconvene at the same round number; `preflight-needs-owner` and `preflight-retries-spent` clear this way once the owner fixes the cause or tells you to resume. An open-attempt stop clears when recovery resolves the attempt or the owner voids it. A stop on a round that no owner exception authorizes (a council at an adr module's round 3, or a round above three), or on a spent round bound, clears when the owner commits an owner-exception record for that place and the round in it converges. An authorized round that does not converge needs a further round with its own exception. A record-order stop on incomplete history (shallow, grafted or partial-clone, section 2) clears once the history is complete. Every other stop is permanent for the module once its record is committed: a held, misnumbered or tied round, a scan-refused round (a record with outcome `ran` whose full write the secret scan refused), an `ARCHITECTURAL` vote, and the four record-order stops of section 8 (a record of the run removed from committed history, a record with no single introducing commit, a stamp that contradicts committed order, and a refutation record whose introducing commit does not descend from the council record's). Every later round of that module also stops. The owner's remedy for a permanent stop is to abandon the run and author a successor book. Section 10 states how the gate check can still lose a stop, and that it never verifies the owner cleared a could-not-run stop.

A reviewer report, a reviewer count, a task title and a consensus claim are all refused as council evidence.

**Module close on an `adr-*` module.** Advance `--outcome done` with the deciding record first. Invoke `transition-adr` to accept the ADR only after that advance writes the prompt `done`. A refused advance writes nothing, so the ADR stays Proposed; stop and report the refusal. Accepting first would leave an Accepted ADR behind a gate that refused it. When `transition-adr` fails after the advance has written the prompt `done`, the run has advanced with the ADR still Proposed. Stop and report it as a contradicted premise (stop 4). Never re-run the gate check, and never edit the run's state or the ADR's status to retry. In a format-two run, every later advance refuses and writes nothing while a closed `adr-*` module's ADR is not Accepted, Deprecated or Superseded, or is unreadable or unidentified: `done`, `blocked` and `skipped` alike. `blocked` and `skipped` are refused because each moves the pointer at a prompt that is not a gate, and a run with no pending prompt left reaches `completed` with the ADR still Proposed. Each pending entry carries a `remedy`. A module names an ADR through an artifact or a run-work witness entry whose path, resolved as the council gate resolves an artifact, is an ADR file under the tree's `adrs/` or `adrs/archive/`. An ADR identifier anywhere else names nothing. The deciding record is the council or refutation record attached at the module's close. Only one entry can carry `transition-adr`: a Proposed ADR that the module names and that the deciding record carries as a subject, when it is the only ADR the module names that the deciding record carries. Dispatch the accepting architect to run it once. The gate cannot see whether `transition-adr` already ran. When it already ran for the module and failed, take a contradicted-premise stop for the owner, whatever the `remedy` field says. Every other entry carries `owner`: an ADR the module names that no deciding record carries, every ADR when the deciding record carries more than one ADR the module names, an entry identified only from the council subjects, an `unidentified` or `unreadable` entry, and an entry with any other status. Never instruct acceptance for an `owner` entry. Take a contradicted-premise stop so the owner decides, or abandon the run. `--abandon` stays open. Record the stop in the run Notes; it needs no advance. It clears when the ADR's `status:` is Accepted, Deprecated or Superseded. An unreadable ADR clears when the file at its path (the subject path, or the named path for an ADR no subject carries), or the file of the same name under `adrs/archive/` when no file is at that path, is a regular file reached without a symlink whose frontmatter carries one of those statuses. A module that names no ADR file and whose records name no ADR file clears only through `--abandon`.

## 5. Refutation records and owner exceptions

Two records are hand-authored, because no vendored writer exists for either. Both live in `<run_dir>/council/` and are validated at advance against their schemas: `${CRUX_PLUGIN_ROOT}/schemas/refutation-record.schema.json` and `${CRUX_PLUGIN_ROOT}/schemas/owner-exception.schema.json`.

- **Refutation record.** It is final once committed: editing, renaming or deleting it stops the module permanently, and its remedy is to abandon the run and author a successor book.[^final] It settles the blocking findings of one council round on an `adr-*` module by named checks. Each entry names a seat-qualified finding id, the check that was run, the token the check emits when it refutes the finding, the token it did emit, an in-repository artifact holding the output, and the derived result. The recorder role is `conductor` or `adjudicator`. A conductor's record carries exactly the subject hashes its council record reviewed. At an adr module's round 3 one adjudicator, never the conductor, writes the record against the round-2 council record.
- **Owner exception.** It carries exactly one of two kinds. A `place` exception authorizes one council round outside the round bound: a round above three, or a council at an adr module's round 3. A `void_attempt` exception voids one open council attempt. It names the attempt record's repository path and the bare sha256 of the committed attempt bytes: 64 lowercase hex digits with no `sha256:` prefix. `git show HEAD:<path> | shasum -a 256` prints that digest. A voided attempt takes no place. The owner writes either kind, or the conductor transcribes it at the owner's instruction. The owner's instruction is defined in section 4. The gate refuses a void-attempt while a council record naming the attempt survives in HEAD, the working tree or a pending copy: run recovery instead.

Before committing a refutation record, run the read-only checker:

```bash
uv run "${CRUX_PLUGIN_ROOT}/scripts/check-refutation-record.py" <run-RUN-NNN.yaml> --record <refutation-record.json>
```

Exit 0 means no finding, exit 1 means findings as JSON, and exit 2 means an environment fault. The checker validates the record against its schema and the run binding, confirms the named council record is committed with a matching sha256 and module, and confirms the refutation's `written_at` is strictly later than the council record's. It writes, stages and commits nothing.

The conductor commits each refutation record and owner-exception record before the advance; the council runner never does. The gate refuses an uncommitted record, with nothing written. An invalid record is refused at advance, with nothing written.

## 6. The independent-review procedure

1. Commit the work. Fix the reviewed range as `<the run's base_commit>..<HEAD at dispatch>`. The base is the run's `base_commit`: the gate check accepts any clean range, so the reviewer and the commissioning agent must choose this base themselves.
2. Each reviewer writes its own report with the vendored writer:

   ```bash
   uv run "${CRUX_PLUGIN_ROOT}/scripts/write-review-report.py" <run-RUN-NNN.yaml> --prompt <n> \
     (--range <base>..<end> | --path <path> [--path <path> ...]) \
     (--verdict '<verdict>' | --verdict-file <file>) \
     [--finding '<finding>' ...] [--finding-file <file> ...] [--reviewer '<role and dimension>'] [--book <book.yaml>]
   ```

   Exit 0 means the report was written under `<run_dir>/reviews/`. Exit 1 means it was refused and nothing was written. Exit 2 means an environment fault. The writer records both range ends as full SHAs. Its JSON line carries `warnings`, which names a range base other than the run's `base_commit`. The writer scans the verdict, the findings and the reviewer for secret shapes before it writes.

   Single-quote each argv value, writing an embedded `'` as `'\''`. Text with quotes or several lines goes in a file: `--verdict-file` and `--finding-file` keep it out of the shell. The writer reads only a regular file reached without a symlink, and scans its text like argv text.
3. A reviewer whose harness grants no shell returns those fields, and the commissioning agent runs the writer with them. A commander holds no shell, so it dispatches the historian to run the writer. Codex runs the reviewer in a read-only sandbox, so this is the Codex route.
4. From the range end to the advance, write nothing to the run snapshot or the book. Each advance rewrites both, so the range always covers them, and a later write to either refuses every report. Commit only the reports before the advance: a commit after the range end that changes a reviewed path refuses every report, and so does an uncommitted change to one. Record notes, deferrals and gate tokens after the advance. Before a deferral is recorded, check it against the book's Outcome and Evidence sentences, and against any narrowing the run snapshot records. Write the check as one line in the run Notes beside it. A deferral that contradicts an Outcome or Evidence sentence is a finding. While a council gate of the run is still ahead, it enters the next council round's question as an open blocking finding that names its originating item and the sentence it contradicts. When none is ahead, it is a contradicted-premise stop for the owner. It is never recorded as a known limitation.
5. Attach every report with `--artifacts`.[^review] The gate refuses an attached artifact that is not `.json`.

A council record never satisfies this gate. The gate requires a report, not a clean verdict: findings that must be fixed route to the book's fix prompt. Advance the review prompt `--outcome done` with every report attached, even when the reports carry MUST-FIX findings; the fix-loop prompt that follows takes the findings. The gate passes on one valid report. It never counts reports or reviewers, never checks the dimensions or threat classes the prompt names, and treats `--reviewer` as free text, so the prompt's reviewer roster holds by prose only. The deciding report is the last one attached.

The writer writes a format-two report. Its commit range admits 40-digit (SHA-1) and 64-digit (SHA-256) commit ids, so a SHA-256 repository can write and gate a range report. A crux release from before format two refuses a format-two report; install the same release wherever the report is read.

## 7. Verdicts and outcomes

The gate check returns one of four verdicts, and every `--outcome` result carries a `gate` object holding the class and the verdict.

| Verdict | Meaning | What the advance does |
|---|---|---|
| `pass` | the evidence satisfies the gate | `--outcome done` writes the prompt `done` and moves the pointer |
| `route` | another round is due | with `--outcome blocked` and `--artifacts`, the council prompt is written `blocked` and the run moves to the module's address-findings prompt: ordinal 3 resets to `running`, module close returns to `pending` and notes are appended; a `patch` verify phase writes nothing and the council reconvenes in the same prompt |
| `stop` | admissible evidence that stops the run | with `--outcome blocked` and `--artifacts`, the prompt is written `blocked`, `current_prompt` stays on it, and the run stays in progress |
| `refuse` | the evidence is inadmissible: missing, not attached, uncommitted, invalid, wrongly bound, naming a writer other than the council runner, or seating off the current registry assignment | nothing is written |

Rules for the outcomes at a gate prompt:

- `--outcome done` is allowed only on a `pass` verdict. On `route` or `stop` it is refused and writes nothing; re-run with `--outcome blocked` and the same `--artifacts`, and the gate check routes or stops (a `stop` blocks the prompt in place and creates no new `running` element).
- `--outcome skipped` is refused.
- `--outcome blocked` needs `--artifacts` naming the evidence. At a council gate, `blocked` with a `pass` verdict is refused. At an independent-review gate, a passing report advanced `blocked` is accepted: it writes the prompt `blocked` and moves the pointer.
- Every `--outcome` needs a resolvable book whose content hash matches the run's `book_content_hash`.
- Never hand-edit a gate prompt's state or `current_prompt`. If `advance-run.py` cannot run, the gate prompt cannot advance and the conductor reports why.

## 8. Stop instances

Four conditions on record order are permanent stops, each an instance of the contradicted-premise stop (stop 4) for the module:[^order]

- a removed record: a council or refutation record of the run, in any module, that the committed history held and that is absent at the evaluated commit (deleted or renamed);
- a record with no single introducing commit: a second add, a change after the add (a record a hook altered is the one carve-out, repaired through recovery), a delete, a rename, or an add that only a merge introduces;
- a stamp order that contradicts the committed order: a clock moved or history was rewritten, and the stop names both records;
- a refutation record whose introducing commit does not descend from the introducing commit of the council record it names.

The owner's remedy for each of the four is to abandon the run and author a successor book. None clears by adding, deleting or re-committing a record.

Records that cannot be ordered are a tie, which is an instance of the escalation-loop stop (stop 1) with the tied records attached to the advance. Records committed in the same commit, or on commits with no ancestry between them, tie. A refutation record committed in the same commit as the council record it names is a tie, not a descent failure. Re-committing cannot clear a tie, because the first commits stay in the history. Equal stamps on ordered commits are neither a tie nor a contradiction. Recovery and round counting treat a record with no committed place as later than every committed record. The gate refuses an uncommitted record, and stops at stop 4 on a record absent from the committed order. A history that cannot order the records stops at stop 4 as section 2 states, and that stop clears once the history is complete.

A held round, a tied or misnumbered round, a council at an adr module's round 3, a round above three that no owner-exception record authorizes, a module whose round bound is spent without convergence, and the third preflight refusal at one convening prompt (a `preflight-retries-spent` record) are each an instance of the escalation-loop stop (stop 1 in `docs/AGENTS.md` §11). A council that cannot run (a `preflight-needs-owner` record included), an open attempt, a scan-refused round, and an implementation or verify council's `ARCHITECTURAL` escape verdict are each an instance of the contradicted-premise stop (stop 4).[^defer][^attempt] A module's round bound is spent when a round at place 3 or later does not converge, including a round an owner exception authorizes. Section 4 states which stops clear: a could-not-run stop, an open-attempt stop once recovery resolves the attempt or the owner voids it, and an unauthorized round or a spent round bound once the owner commits an owner exception. Every other stop is permanent for the module once its record is committed. A stopped gate starts no further prompt, and the run stays in progress until the owner acts. For a could-not-run stop, that wait holds by prose.

## 9. Books that already exist and runs in flight

- The gate check classifies every prompt at execution, so an existing book receives the correction at its next gate prompt. Its bytes never change.[^correct]
- A template change fixes no existing book. It changes only books authored afterward.
- A module whose council ran before council records existed closes only on a council-runner round convened at module close.
- **Format one only:** `cycle_grandfathered` and `cycle_kind` are bound at run start. Adding or changing either while a run is in flight refuses every `--outcome` of that run, with nothing written. A shallow clone is the exception: there both fields are ignored, as stated below. A book grandfathered before its run starts has every prompt `unclassified`, so none of its gates is checked. In a shallow clone the run-start values cannot be read. The gate check then classifies each prompt by its module tag and phase, so it checks the gates of a grandfathered book, or of a book without `cycle_kind` whose prompts carry module tags or phases.
- An upgrade applies the gate check to each cycle run in flight at its next gate prompt. A run whose records were edited, renamed or removed before the upgrade stops permanently at its next gate. Records committed together in one commit tie at the next gate. A run whose earlier stamps contradict the commit order stops permanently at its next gate, at stop 4.

## 10. What the gate check cannot see

The gate check binds hashes, not content. What prose alone cannot enforce is listed by topic.

These live-gate limits do not replace formal approval replay. A consumer validates
the committed binding, retained context and supported profile before accepting
reviewed intent. A raw receipt, DONE or a current registry supplies no substitute.
Legacy council records require committed run-start proof of a pre-cutover base;
shallow or unreadable history supplies no such proof.

- **ADR acceptance.** The advance reads the `status:` of each ADR the module's own artifacts or run-work witnesses name by an ADR file path. It takes the path from each council or refutation subject that carries the identifier, and from the named path otherwise. A named ADR that cannot be read makes the advance refuse, with the status `unreadable`. An ADR identifier outside an ADR file path names nothing, so a module that cites its ADR only by identifier is judged by its subjects. A module that names no ADR is judged by every ADR subject its records carry, so a Proposed ADR cited only as context makes the advance refuse. It reads the working-tree file, so an uncommitted status edit clears the refusal. The refusal arrives at the next advance, so work inside the next prompt can finish before it. The gate-information query reports the pending ADR before that work starts.
- **Content binding.** The gate check cannot tell whether a question carried the dimensions, whether a seat's tags are true, whether a recorded check ran, or whether a reviewer report reflects a real review. On the ADR path the accepting architect's re-run of the recorded checks is the control. A round never recorded is invisible to it. An edit after convergence passes module close: the gate reports the changed subject hash and refuses nothing.
- **Records and authorship.** `writer: run-council` and the seat values are self-asserted. The gate cross-checks each seat of the record that decides (the deciding council record, or the council record a deciding refutation names) against the current registry: the role is one of the three gate roles; the registry key is the key the registry assigns that role; the key is not retired; the requested model equals that entry's API string; and, for a seat that responded, the served model and the served provider are among that entry's accepted values. The three seats of every counted record sit on three distinct provider namespaces. An earlier round is not checked against the registry, because it can add only a stop or a hold. A registry edit, or a plugin upgrade that changes a seat assignment, between the deciding council and its advance makes that record inadmissible: convene a new round, which runs on the new assignment. That round may itself need an owner exception: at an adr module's round 3, or at any round above three. A forged, committed record that copies the current registry values is outside the local-tool threat model. Write times, an owner exception's authenticity and a recorder's identity, and with it the adjudicator's independence, are also self-asserted. Refutation records and owner exceptions have no vendored writer.
- **Review gate.** The gate passes on one valid report. It never counts reports or reviewers, never checks the dimensions or threat classes the prompt names, and treats `--reviewer` as free text, so the prompt's reviewer roster holds by prose only. It reads a report that is not committed, so a report edited after the writer ran passes. It refuses an attached artifact that is not `.json`. The reviewer-report writer scans by shape only.
- **Escapes.** The secret scans miss a secret with no known shape. Councils outside promptbooks (an ad-hoc council, the retrospective, the night gardener), older installs, a conductor that never calls the advance or the gate-information query, and a hand-edited snapshot escape the check. There, council deliberation holds by prose only. A patch book's escape verdicts are carried as blocking findings. The conductor takes the contradicted-premise stop by prose.
- **Discard window.** The council runner commits an attempt record before any council request. So a held result deleted before its first commit, a failed commit, a crash or a duplicate invocation leaves an open attempt, and the gate stops at stop 4 until the attempt's own verified record resolves it or an owner's void-attempt voids it.[^attempt] A committed record that is later deleted or edited also stops the module permanently, because the gate orders records by their introducing commits. What still escapes: an admitted pre-cutover format-1 council record, written without a claim, keeps the old window, in which a fresh round at the same number that converges passes; a void-attempt is owner-asserted, because no vendored writer exists for it; deleting the preflight diagnostics log buys extra retries, never a pass; a council runner stopped before its claim commit lands made no request, so it leaves nothing to discard; and committed tampering stays outside the threat model (below).
- **Owner clearance.** The gate check never verifies that the owner cleared a could-not-run stop, a `preflight-needs-owner` stop or a `preflight-retries-spent` stop: a later converged round at the same number passes whether or not the owner acted. Advancing the record `blocked` records the stop in the run snapshot; the wait for the owner holds by prose. A run snapshot's witness holds only until the next advance rewrites the snapshot. A step that records the snapshot's witness after its last write in the same prompt makes an uncommitted snapshot repairable through `commit-run-work`. A snapshot left uncommitted after an advance has no matching witness, so preflight refuses it as `preflight-needs-owner`, a stop 4 for the owner. Commit the snapshot before you run the council runner, as the council prompt's step (b) says. The residual cost against the Outcome falls at the two prompts whose subject an earlier prompt wrote: a verify module's council prompt and an iterate book's Prompt 2. There an advance rewrites the snapshot after its last witness, so a snapshot left uncommitted stops for the owner where a repair would otherwise resolve it. The preflight retry count resets only at a claim, so after a `preflight-retries-spent` record each further refusal at that prompt writes another such record; the owner's clearance holds by prose here too.
- **Tampering.** Committed tampering with a run record is outside the local-tool threat model: an edit before the snapshot's first commit, a history rewrite of that commit, a committed rewrite of `base_commit` together with `run_id` or the book hash, a committed copy at a new path, and a committed hand-edit marking a gate prompt done or moving `current_prompt`. A committed deletion or rewrite of a council or refutation record does not clear a stop: the removed record and the record without a single introducing commit each stop the module at stop 4, as section 8 states. A rename of such a record is a removal and stops the same way. A switch to a branch without the stop still clears it, because the gate check reads only the checked-out tree. The patch tier's archive pin compares against HEAD, so it refuses an uncommitted edit only.
- **Profile three replay.** An approval issued under profile three replays under the stamp order, and the replay cannot see a rolled-back or skewed clock.[^replay] The limit covers every release whose current profile is three, 3.26.1 included, and every install that does not upgrade. Profile three stays valid and replay-only. To compare the committed order with the stamp order for existing bindings, run the read-only audit:

  ```bash
  uv run "${CRUX_PLUGIN_ROOT}/scripts/implementation-decisions.py" audit-order [--repo-root <root>] [--run <run-RUN-NNN.yaml>]
  ```

  The audit is read-only. It exits 0 with a report, or 2 when it cannot start, and it refuses nothing. For each profile-three binding it reports `agree`, `contradict` or `uncomparable`. Only an `uncomparable` row carries a reason: `tie`, `invalid-path-history` or `shallow-or-unavailable`. It evaluates the committed order at the binding's proof commit, never at HEAD. A `contradict` row means the approval's stamp order disagrees with the committed order. The approval stays valid and replay-only. To act on it, review whether a skewed clock ordered that approval's council evidence. If the owner judges it did, re-run the decision under the current profile, which is a new close.
- **Paths.** A path reached through a symlink, at its leaf or in a directory inside a repository, is refused; name its physical path instead. When the symlink probe of a path prefix errors, the check treats that prefix as no link.
- **Harness limits.** Codex's workspace-write sandbox keeps `<root>/.git` read-only. Observed with Codex CLI 0.160.0 and no model call: a working-tree write succeeds, while `git add`, `git commit` and a write under the git directory are denied, and naming the git directory as a writable root does not lift that. So the attempt commit fails before any council request, and the council runner exits 2 with its claim uncommitted; recovery's documented path releases an uncommitted claim, which is not observed under the Codex sandbox. A Codex council in that sandbox cannot begin: nothing is spent, and the prompt cannot advance. Run the council runner from a session whose sandbox can write the git directory. The attempt lock needs `fcntl`. On a platform without it (Windows), the council runner exits 2 before the claim, and council gates cannot run there. Not observed: Codex's approval escalation, a git directory outside the workspace root, and gateway egress from Codex sandboxes. An unreachable gateway takes the could-not-run stop. The Codex reviewer's read-only sandbox cannot write its own report.
- **Review base.** The gate check does not compare a review range's base with the run's `base_commit`, so any clean range that changes a path passes it. The writer warns on another base, and the base holds by prose.

Section 9 states how `cycle_grandfathered` and `cycle_kind` bind at run start.

[^council]: rule:council-is-never-harness-native, rule:council-gate-needs-a-runner-record
[^review]: rule:review-gate-needs-a-reviewer-report
[^seats]: rule:every-seat-assesses-five-dimensions, rule:council-assignment-is-validated-before-spend
[^gate]: rule:gate-reads-every-recorded-round, rule:convergence-is-derived-per-seat, rule:a-seat-without-a-blocking-finding-holds-the-gate, rule:council-gate-advances-on-convergence-or-refutation, rule:blocked-gate-needs-evidence
[^defer]: rule:only-a-preflight-refusal-is-retried
[^attempt]: rule:an-open-council-attempt-stops-the-gate
[^match]: rule:council-evidence-matches-its-attempt
[^recover]: rule:recovery-never-deliberates-again
[^correct]: rule:existing-books-are-corrected-at-execution
[^order]: rule:council-record-order-is-committed-order, rule:introducing-commit-is-unique-and-read-in-isolation
[^frozen]: rule:issued-approval-profiles-are-frozen
[^replay]: rule:profile-three-order-is-audited-never-refused
[^final]: rule:introducing-commit-is-unique-and-read-in-isolation
[^scan]: rule:secret-scan-covers-egress-and-every-write
