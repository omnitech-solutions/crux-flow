# Start a promptbook run

Read this reference before the first start side effect. Follow the shared obligations in `../SKILL.md`.

## The pipeline (mode: start)

### 0. Resolve per-repo configuration (.crux) — applies to BOTH modes

Run `uv run "${CRUX_PLUGIN_ROOT}/scripts/crux-config.py"` from the repo root (or pass `--repo-root <repo-root>`), and confirm the returned `repo_root` is the repo you are operating in — `source: "discovery:<dir>"` with an unexpected `repo_root` means you resolved the wrong directory, not that no config exists. On exit 1, **STOP** and surface the `{"error": ...}` payload — never fall back to defaults. Use the returned `docs_dir` to resolve the docs root wherever this skill says `docs/` (per the docs/AGENTS.md §14 normative definition clause), and accept prefixed book ids (e.g. `CRX-PB-0040`) anywhere this skill says `PB-NNNN` — the prefixed string is the id, verbatim, in paths and `book_id`. `RUN-NNN` ids are never prefixed.

### 1. Resolve the target book (live format gate)

User must supply or imply `PB-NNNN`. If ambiguous, list active books from `docs/promptbooks/index.md` and ask.

Locate the book from its exact indexed slug under `docs/promptbooks/active/`. If the path is `.md`, apply the shared refusal in `../SKILL.md` before reading run state or writing anything. For `.yaml`, require `format_version` and validate it; a missing version is malformed. Parse top-level `id`, `title`, `total_prompts`, `current_run`, `current_prompt`, and `prompts:`.

Confirm:
- `status: active`.
- `current_run`, branched on the pointed-to run's `status`: `null` → no run in progress, start one. If non-null, identify the exact run path and apply the shared `.md` refusal **before any supersession or other write**. An `in_progress` YAML run may be advanced or superseded at the user's direction. An archive-eligible terminal YAML run (`completed`, or `abandoned` with `abandonment.kind: deliberate`) routes to `archive-promptbook`. An `abandoned` run with `abandonment.kind: superseded` is stale and may take the supersession path below.

**The start-path supersession.** When the user chooses to start fresh over a stale run, set the PRIOR run snapshot's `status: abandoned`, `current_prompt: null`, and

```yaml
abandonment:
  kind: superseded
  at: <ISO 8601 UTC>
  reason: "superseded by RUN-NNN"
```

This is a DIFFERENT act from a deliberate abandonment and the `kind` is what distinguishes them. `kind: superseded` **confers no archive eligibility** — a book whose current run was merely superseded cannot archive. Only `kind: deliberate`, written by mode `abandon`, does. Never write `kind: deliberate` here.

### 2. Allocate `RUN-NNN`

Walk `docs/promptbooks/runs/PB-NNNN-<slug>/`. Find the maximum existing `run-RUN-NNN.{md,yaml}` (enumerate both extensions to preserve monotonic ids across historical runs) and add 1 (or start at `001` if the directory does not exist). Per-book monotonic, zero-padded to 3 digits. Never reuse a historical Markdown run number.

### Format-two creation boundary

For a format-two book, invoke the thin start writer after allocating the run id:

```bash
uv run "${CRUX_PLUGIN_ROOT}/scripts/start-run.py" <book.yaml> --run-id RUN-NNN --output <run-RUN-NNN.yaml>
```

The writer validates the canonical book shape and frozen hash before publication.
It creates format-two run state with `implementation_bindings: []`, records the
required available full-history base commit, and refuses overwrites and unsafe paths.
The skill still owns allocation, prior-run disposition, book pointers, indexes and log.
Do not hand-create format-two bindings or duplicate the validator. The format-one
creation route below remains unchanged. Preserve the chosen format throughout the run.

### 3. Write the structured YAML snapshot

Path: `docs/promptbooks/runs/PB-NNNN-<slug>/run-RUN-NNN.yaml`.

Write a single structured YAML document per `${CRUX_PLUGIN_ROOT}/schemas/run.schema.json`. Top-level keys:

```yaml
format_version: "1"
run_id: RUN-NNN
book_id: PB-NNNN
book_content_hash: "sha256:<64 lowercase hex>"   # the binding — see below
started_at: <ISO 8601 UTC>
completed_at: null
status: in_progress
current_prompt: 1
base_commit: "<40 lowercase hex from `git rev-parse HEAD`, or null>"
prompts:
  - n: 1
    title: "<title denormalized from the book's prompts[0].title>"
    state: pending
    started: null
    completed: null
    result: ""
    artifacts: []
  - n: 2
    title: "<title denormalized from the book's prompts[1].title>"
    state: pending
    started: null
    completed: null
    result: ""
    artifacts: []
# notes / pr_draft / summary are optional trailing Markdown literal-block scalars; "" until populated.
```

- **`prompts:` is populated from the book's `prompts[]`** — one element per book prompt, in order. Each element: `n` (1-based, contiguous from 1 — the validator's post-schema pass enforces `n == index+1`), `title` **denormalized** from the book (copy the verbatim value so the run renders offline; the abandon rule guarantees it can't legitimately diverge), `state: pending`, `started: null`, `completed: null`, `result: ""` (empty is `""`, NOT the legacy `—` placeholder), `artifacts: []`.
- **`base_commit` (the run's commit boundary — written ONCE at start, never rewritten):** run `git rev-parse HEAD` at the repo root and store the 40 lowercase hex commit id. This is the evidence source the `patch` tier's archive check reads: it lets `check-blast-radius.py` draw the run's changed paths from the repository's own change record rather than from anything the run wrote about itself. "Never rewritten" is enforced at both ends, not merely asked: `advance-run.py` refuses to write a snapshot whose `base_commit` diverges from the value in its own committed version, and `check-blast-radius.py` refuses to pass one at archival. If the repository is not a git work tree, write `base_commit: null` — and say so, because a `patch` book whose run has no commit boundary cannot archive as completed. For an `adr` or `verify` book the field is recorded but unused.
- **`abandonment` is OMITTED at creation.** It is written later, once, either by mode `abandon` (`kind: deliberate`) or by a later run's start-path supersession (`kind: superseded`).
- **`book_content_hash` (the binding — load-bearing, computed ONCE at start, immutable for the run's life):** compute it by calling the validator's hash function over the book's frozen-plan subset — `${CRUX_PLUGIN_ROOT}/scripts/validate-promptbook.py` (source checkout: `<checkout>/crux/scripts/validate-promptbook.py`) exposes `compute_book_hash(book_dict)` (which internally calls `frozen_plan_subset` and `canonical_json`). Pass the parsed `.yaml` book document; it returns `"sha256:" + sha256(canonical_plan_bytes).hexdigest()`. **Do NOT hand-roll a hash** and do NOT hash the raw file bytes — the hash is over the canonical-JSON of the frozen plan subset (`format_version`, `id`, `title`, `tags`, `total_prompts`, `goal`, `strategy`, plus `modules` and `blast_radius` when present, and each prompt's `n`/`title`/`purpose`/`prompt`/`expected_output` plus `side_effects`/`module_tag`/`phase` when present), EXCLUDING the mutable run-state fields `current_run`/`current_prompt`/`status`. `blast_radius` and `phase` are inside the subset deliberately: that is what fixes a `patch` book's declaration and its phase sequence once the run starts. This is exactly the subset the §4 abandon rule freezes, so the hash is stable across the run-state writes every advance makes; a later mismatch (`audit-docs` CHK-PB-BIND, and for a `patch` book `check-blast-radius.py` at archival) means the plan was edited in place instead of the run being abandoned and a successor book authored.
- The per-prompt shape is validated by `run.schema.json` (audit invokes `validate-promptbook --kind run`); the lifecycle and archive-eligibility contract is **`docs/AGENTS.md` §11.B**.

For format two, the canonical frozen subset additionally binds `cycle_kind` and
all slot declarations, including scope, slug and constraint references. It never
includes later revision selection. Do not compute a parallel hash or copy slot
selectors into mutable run-start fields. Format-one hash bytes remain unchanged.

On snapshot creation every prompt is `pending`; prompt 1 transitions to `running` on the first advance.

### 4. Update the active book

Set the top-level run-state fields on `docs/promptbooks/active/PB-NNNN-<slug>.yaml`:
- `current_run: RUN-NNN`
- `current_prompt: 1`

Do NOT touch the body. Plan is frozen now.

### 5. Regenerate `docs/promptbooks/index.md`

Walk active, runs, and archive. Rebuild the index (same format as documented in `author-promptbook`). Progress for this book is `0/<total_prompts> (0%)`: every prompt is still pending. The book's `current_prompt: 1` is a pointer, not a completed-prompt count.

### 5b. Bump `docs/index.md` `_Last updated:`

Read `docs/index.md`, update only the `_Last updated:` line to today. Counts under `## Promptbooks` don't change on run start (active/archived counts are unchanged); the per-book `current_run`/progress is in `docs/promptbooks/index.md`, not the master rollup.

### 6. Append to `docs/log.md`

```
## [YYYY-MM-DD] promptbook | started PB-NNNN-<slug>/RUN-NNN
```

Body: book id, run id, total_prompts, current_prompt.
