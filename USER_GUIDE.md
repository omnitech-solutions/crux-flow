<!-- generated-from: USER_GUIDE.md@sha256:922e39d5da3e06b071f55ef17734775802e8aa221e9928f3b524c52486147829; model: claude-sonnet-5.5; date: 2026-09-30 -->
# Working with `bionic/` in crux

Your project keeps its documentation in `bionic/` (or a directory you choose with `docs_dir`; see [Per-project configuration](#per-project-configuration-the-repo-root-bionicyml-file)). It holds seven concerns, plus the two default-on surfaces `arch` and `observations`. The `crux` plugin maintains all of it. **You do not write these docs by hand.** You curate, decide, and discuss. Your agent does the bookkeeping.

This guide is for humans. If you are an LLM agent working in a crux-managed repo, read `bionic/AGENTS.md` in the project. That file is the operational schema.

---

## At a glance

crux turns a `./bionic/` folder into a maintained knowledge base that you and your agent share. It has four moving parts:

1. **The `bionic/` tree: seven concerns, plus two default-on surfaces.** The concerns are code docs, research wiki, ADRs, briefs, work journal, promptbooks, and invariants. The surfaces are the derived `arch` map and the `observations` records. → [The seven concerns](#the-seven-concerns)
2. **52 skills, triggered by natural language.** Say "propose an ADR", "process inbox", "audit docs", "start a cycle", or "forge a skill". Each phrase is routed to a skill through the skill's description. → [What to say](#what-to-say-to-claude)
3. **10 agents, a role layer over the skills.** A `commander` conductor delegates to `architect`, `dev-lead`, `developer`, `reviewer`, `historian`, `librarian`, `brainstormer`, and `wayfinder`. `night-gardener` runs on a schedule. Each agent is fenced by a tool allowlist, so duties are separated *structurally*. → [The agent layer](#the-agent-layer)
4. **Three workflows for change.** All three are tracked promptbooks.
   - `dev-cycle` is for net-new or architectural work (ADR, council, review).
   - `iterate` is for non-architectural fixes (verify, council, review, no ADR).
   - `patch-cycle` is for a small reversible fix (five phases, one prompt each, with a declared blast radius).

   For a defect whose fix you can already name, use `fix-directly`: no book, and a failing test first. → [Planning multi-step work](#planning-multi-step-work--promptbooks--cycles)

Under the hood, Python **scripts** (extractors, validators, the LLM router) do the work the skills call for, and the `crux-env` **CLI** manages your API keys. → [Tools & scripts](#tools--scripts)

**If you read nothing else:**

- Drop anything into `bionic/inbox/` and say *"process inbox"*.
- Ask *"what does X do?"* or *"why did we choose Y?"*.
- Say *"start a cycle for X"* or *"iterate on X"* to ship a change with the receipts attached.

---

## The mental model

```
┌─ Your job ──────────────────────────────────────┐
│  • Make architectural decisions                  │
│  • Drop anything in bionic/inbox/ (files, URLs,  │
│    notes); process-inbox sorts and routes it     │
│  • Direct the work (what to build next)          │
│  • Ask questions of the docs                     │
└─────────────────┬───────────────────────────────┘
                  │ conversation
                  ▼
┌─ Your agent's job ──────────────────────────────┐
│  • Capture decisions as ADRs                     │
│  • Ingest sources into the research wiki         │
│  • Journal what got done                         │
│  • Track promptbook progress                     │
│  • Extract code docs from source                 │
│  • Audit for drift                               │
└─────────────────┬───────────────────────────────┘
                  │ writes
                  ▼
              bionic/  tree
```

You read the wiki. Your agent writes it.

---

## The seven concerns

_This guide names the default `bionic/` layout. In a repo with a custom `docs_dir`, that directory takes the place of `bionic/` everywhere below._

| Directory | What lives there | Who edits |
|---|---|---|
| `bionic/code/` | Auto-extracted from source code (docstrings, @doc, JSDoc) | **Regenerated, never edit by hand.** Hand edits are deleted on every extract run. |
| `bionic/research/` | Articles, papers, web pages, meeting notes, chat exports | You drop sources in the unified `bionic/inbox/`. `process-inbox` routes them to `ingest-research`, which captures each into the immutable `bionic/research/raw/` and runs the ingest pipeline. |
| `bionic/adrs/` | Architecture Decision Records, the "why" of every load-bearing decision | You decide and the agent writes the ADR. **Once `Accepted`, the body is frozen.** |
| `bionic/briefs/` | Pre-decision exploration documents (`BRIEF-<slug>.md`) | You write; the agent tracks them. |
| `bionic/journal/` | Day-by-day record of work performed (`YYYY-MM.md`) | The agent appends entries; you ask for them. |
| `bionic/promptbooks/` | Plans of prompts you'd like to execute, plus immutable run snapshots | Co-authored. Mutable plan, frozen run history. |
| `bionic/invariants/` | Pinned, executable statements of *what must be true*: a ledger page per pin, plus the executable check suite in `invariants/checks/`, reconciled via `invariants/reconciliation.yml` | **The machine proposes, you ratify.** `recover-invariants` mines candidates as `observed`. You ratify, reject, or retire them via `transition-invariant`. Recovery never self-ratifies. |

Two more surfaces are **default-on**:

| Directory | What lives there | Who edits |
|---|---|---|
| `bionic/observations/` | Records of what the code *already does* (`OBS-NNNN-<slug>.md`), each evidenced by a `path:line-range` and never by a code excerpt | **You write, you ratify.** `propose-observation` scaffolds one as `observed`. `transition-observation` is the only single-record route past `observed`, to `ratified`, `rejected`, `retired`, or `decided`. The batch sign-off is the only batch route. No scan writes an observation file in any state. |
| `bionic/arch/` | Derived architecture spine, the current-state map: data model, interface surface, module graph, decision index, plus a synthesized overview | **Regenerated, never edit by hand.** Built on demand by `derive-arch`. New trees are enrolled by default. Existing trees opt in by adding `arch` to `concerns_enabled`. |

**Invariants** are the "far half of the bridge". The other concerns record knowledge, while invariants pin *what must stay true* as executable checks that survive regeneration. A new repo stands the concern up empty, as an invitation to run `recover-invariants` when you're ready. An empty invariants concern is clean, not broken.

### `bionic/arch/`: the derived architecture

`arch` is the primary place to answer *"how is this project shaped right now?"* It holds a deterministic spine (data model, interface surface, module graph, decision index) plus a synthesized overview, all regenerated wholesale from the project's own sources. ADRs are the secondary path (the *why*), and the librarian and historian fill the gaps.

On a new repo, `init-docs` enrolls it, so just say **"build the arch"**. Keep it current with `derive-arch`, or let `audit-docs` auto-regenerate it on drift. An existing tree that predates the default opts in first, by adding `arch` to `concerns_enabled` in `bionic/manifest.yml`, and then derives. Like `code/`, it is never hand-edited.

### `bionic/observations/`: what the code already does

An observation describes; it does not decide. An ADR records a choice somebody made. An observation records a fact nobody ever wrote down, evidenced by a `path:line-range` into the real source. A repository that has never authored an ADR can use observations as its starting layer. A repository full of ADRs can use them for everything the ADRs never covered.

Say **"propose observation"** to scaffold one. It lands as `observed`, and only you move it past that. `transition-observation` is the only single-record route, and `survey-signoff` is the only batch route. No scan ever writes or ratifies one. Ratified records feed the same summaries and doctrine projections the ADRs feed, so an observation earns real authority once you have affirmed it.

The batch route runs in two steps and covers as many candidates as you put on one sheet:

1. Say **"scaffold a survey sheet"**. `survey-sheet` builds one sheet over the candidates in `observed`, seeding each row with a claim and a proposed domain. You author the rest of each row: a verdict of `ratify`, `reject`, or `defer`, a one-line rationale, and a domain that overrides the proposed one where it is wrong.
2. Say **"sign off the survey"**. `survey-signoff` renders every claim and its verdict for you to read. Once you confirm, it publishes the batch under one digest-bound receipt, so one signature covers every ratification in it.

Both commands are yours to invoke, and neither runs unattended.

---

## The agent layer

crux ships ten **agents** that operate the skills above. Claude Code loads the source agents from the plugin. Codex and OpenCode use generated native forms.

- **commander** runs a promptbook or cycle and delegates everything (never edits).
- **brainstormer** explores a design with you (drives the `whiteboarding` skill), then hands the session to the historian to file.
- **architect** owns ADRs and decisions (drafts, runs the council, accepts).
- **dev-lead** leads implementation and fans independent work out to **developer**s.
- **reviewer** independently reviews code. It is read-only, so it reports findings and never fixes-and-hides.
- **historian** owns every write under `bionic/`.
- **librarian** answers questions from `bionic/` (read-only retrieval).
- **wayfinder** goes ahead and finds the way through large, uncertain, or external data. It reads the data in an isolated context, judges its fitness for your purpose, and returns a verdict plus a condensed digest. A primary agent then spends its own context only on proven-fit content. It is read-only, and it is the only agent with `WebFetch`/`WebSearch`, fenced by an egress guardrail.
- **night-gardener** is the overnight presence. It runs as a scheduled routine, reviews what changed since your last move, and leaves a morning note under `bionic/garden/` (ideas, codebase improvements, missing tests/CI/guards, research, news). It is turn-based (it skips when only its own work changed), acts as a full co-CTO behind existing gates, and never pushes. Dismiss or snooze its advice via `bionic/garden/tending.md`; no acknowledgment is needed. Its news pass reads a curated source list via the `read-news` skill.

The source roles declare tool boundaries and required skills. Host permissions can override a role's default sandbox, so the role prompt remains binding. The agents embed the craft disciplines they need, including testing, verification, debugging, and two-stage review.

| Agent | Reach for it when you want… | Bounded so it cannot… |
|---|---|---|
| `commander` | a whole cycle or promptbook driven end-to-end (it delegates each step) | edit code or docs itself |
| `brainstormer` | to explore a fuzzy idea before committing (drives `whiteboarding`) | write files or touch code |
| `architect` | a decision recorded and council-reviewed (`propose-adr` → `council` → `transition-adr` to accept) | implement code |
| `dev-lead` | implementation coordinated across parallel units | merge or push (human-gated) |
| `developer` | one scoped unit built test-first | re-delegate, or write docs/ADRs |
| `reviewer` | an independent check of a diff | edit files (it reports; never fix-and-hide) |
| `historian` | anything written under `bionic/` (intake, journaling, indexes) | edit source code |
| `librarian` | a question answered from `bionic/` (`query-docs`) | write anything |
| `night-gardener` | an overnight pass that records ideas, gaps, research, and news | push, merge, or send material externally |
| `wayfinder` | a large, uncertain, or external source triaged for context-fitness and condensed before you read it | write, execute, delegate, or relay local content outbound |

### Installing the agents in Codex

In Codex, say **"install the Crux agents in Codex"** after installing the plugin (this uses the `install-codex-agents` skill). The installer writes the ten namespaced `crux_*` roles to `~/.codex/agents/` by default. Each role pins its catalog model and reasoning effort and binds its declared skills to the installed plugin. An explicit `--repo-root` selects one project's `.codex/agents/` directory instead.

- An unchanged refresh is a no-op.
- Changed or stale managed files require `--force` after review.
- Plugin relocation appears as drift, because skill bindings use absolute paths.
- `--check --project-context <repo>` reports managed drift and project agents that shadow personal roles. The report separates canonical expectations from managed TOML state parsed from disk. Its runtime result stays `unverified` until fresh-session host evidence confirms discovery, settings, skills, and representative workflows for the selected Codex version.

**How you actually use them:** you rarely name an agent. A cycle (and the `commander`) dispatches them for you. But you can be explicit: *"have the architect propose an ADR for X"*, *"send this design to the council"*, *"have the reviewer check the diff"*, *"ask the librarian what we decided about Y"*.

---

## What to say to Claude

These phrases trigger the right skill. Use them in natural-language sentences; your agent figures out the rest. (The tables use "Claude" for brevity. The same phrases work in Codex and OpenCode.)

### Recording decisions

| Say | What happens |
|---|---|
| **"Propose an ADR for X"** | `propose-adr` writes a new `ADR-NNNN-<slug>.md` in `bionic/adrs/` with status `Proposed`. You review the alternatives, then accept. `propose-adr` only ever creates a new `Proposed` decision. |
| **"Accept ADR-NNNN"** | `transition-adr` changes an existing decision's status to `Accepted`. The body is now frozen. |
| **"Supersede ADR-NNNN with ADR-MMMM"** | `transition-adr` updates both ends of the supersession link atomically. |
| **"Deprecate ADR-NNNN"** | `transition-adr` retracts the decision (no replacement). |
| **"Sign off backfill batch \<id\>"** | `backfill-signoff` is the owner sign-off for one governed backfill batch. Every enumerated receipt's rule and anchor renders verbatim for your read before anything writes. The script is the only write path onto the backfill surfaces (signed-date flip, admission ledger, log op, journal hook, completion marker). Never hand-edit those surfaces. |

ADRs are append-only history. You can't edit one once accepted. You write a new one that supersedes it.

### Capturing anything: the unified inbox

There is **one** place to drop raw input: `bionic/inbox/`. Drop any kind of item there (a research file, a pasted URL, a half-formed decision note, a stray idea), then say **"process inbox"**. The `process-inbox` skill classifies each item and shows you a confirmation table (item → proposed skill → confidence). On your `ok`, it dispatches each item to the skill that owns its concern:

| Item | Routed to |
|---|---|
| research source (file, URL, or a `urls.md` manifest) | `ingest-research` |
| architectural decision | `propose-adr` |
| pre-decision exploration | `propose-brief` |
| work-log note | `log-work` |

Classification is **classify-then-confirm**. Only items your agent is confident about auto-dispatch on `ok`. Anything unsure is held back for you to `override N=<target>` or `defer N`. ADRs are only ever created as `Proposed`; `process-inbox` never auto-accepts.

**Research flows through `ingest-research`.** A research item is moved into a dated, immutable `bionic/research/raw/` capture, gets an audited source page, and updates synthesis pages.

- **Drop a file in `bionic/inbox/`** and say "process inbox".
- **Paste a URL** into a file under `bionic/inbox/` (or hand it to your agent) and say "ingest this". It uses the same pipeline, fetched via the bundled `web-to-markdown.py`.
- **Bulk import:** drop a `urls.md` file in `bionic/inbox/` with one URL per line. Add `(static)` after a URL to opt it out of refresh checks. A partially drained `urls.md` is left in place and retried on the next run.

When upstream sources may have changed:

- **"Refresh sources"** re-fetches non-static URLs, files updates as new dated captures, and flags affected synthesis pages.
- **"Refresh synthesis"** walks you through accumulated markers (contradictions, source updates) per page.

### Journaling work

- **"Log work"** or **"journal this"** adds an entry to `bionic/journal/YYYY-MM.md` with today's date and a category.
- Your agent may also call this silently after meaningful operations (ADR accepted, promptbook completed, large refactor).

Categories: `decision | implementation | bug | learning | blocker | refactor | meeting | review | misc | release`.

`log-work` selects one procedure before writing:

- An interactive journal request adds a reflective monthly entry, regenerates the journal index, and records one `journal` operation.
- A silent call defaults to log-only: it requires a valid operation and writes only `bionic/log.md`.

The bundled writer checks the request before writing and reports `complete`, `refused`, or `partial`. Keep the original request and its explicit local UTC offset for retry. A month-only partial entry cannot prove that offset, so the writer warns `offset unverified`. If the offset was lost, stop automated replay and resolve it from evidence. A journal body allows 1–10 authored lines; an optional `Refs:` line is separate from that limit.

### Planning multi-step work: promptbooks & cycles

There are several ways to drive multi-step work, in roughly increasing rigor:

- **"Start a cycle for X"** (`dev-cycle`) is for **net-new or architectural** work. It assembles a tracked promptbook of ADR, dev, and review modules. A decision is recorded as an ADR and council-reviewed, implemented, then independently reviewed (≥13 prompts).
- **"Iterate on X"** / **"remediate X"** (`iterate`) is for **non-architectural fixes** (bugs, drift, refinements to existing behavior). It has the same council and review rigor. A *verify* module (reproduce, root-cause, council-review the diagnosis) replaces the ADR, since there's no decision to record. If the verify council finds the work *is* actually architectural, it stops and routes you to `dev-cycle`.
- **"Patch this"** (`patch-cycle`) is for a **small reversible** non-architectural change you can bound by naming the paths it may touch. It has five phases (verify, plan, implement, review, summary), one prompt each. You declare a *blast radius* up front. The council checks it is no wider than the work needs, and archival checks the paths the run actually changed against it, using git. A change that reaches outside the declaration cannot archive as delivered; it is re-authored as an `iterate` or a `dev-cycle`. Work needing an ADR is not a patch.
- **"Just fix it"** (`fix-directly`) is for a defect whose files, failing test, and unchanged contracts you can name before starting. It uses no book, no council, and no book number. You write a failing test first, make the smallest change that turns it green, run the suite and the drift gates, then make one commit and one journal entry. A security label sets a defect's priority, not its size; the sizing test sets the tier.
- **"New promptbook for X"** (`author-promptbook`) is a bespoke multi-prompt plan you co-author, with **no** enforced council or review. Use it for sequences that don't need the ceremony.

Then drive any of them:

- **"Run it"** starts an immutable run snapshot under `bionic/promptbooks/runs/<promptbook-id>/run-RUN-NNN.yaml`.
- **"Advance"** / **"next prompt"** marks the current prompt done and moves on.
- **"Abandon this run"** closes a run that will not finish.
- **"Archive promptbook"** closes the book once its run has either completed with every prompt terminal (done / skipped / blocked) or been deliberately abandoned.
- **"Promptbook status"** reads the current run through the existing progress renderer. The `run-promptbook` status procedure owns this read-only view. A terminal view or status answer writes nothing; a Markdown progress artifact requires an explicit `--markdown` request.

Advancing changes the run snapshot and active book pointer only; it writes no per-prompt log entry.

Books and runs are structured `.yaml` documents validated against a JSON Schema (the cycle kinds also pass the cycle-coverage invariants). You can't edit the prompts list mid-run. Abandon the run, then author a successor book that names its predecessor. Numbers are never reused.

### Upgrading from the seven retired entries

If you are upgrading from an install that used the seven retired entries, use these routes.

**Plugin paths across hosts.** In Claude Code, Codex, and OpenCode, derive `CRUX_PLUGIN_ROOT` from the selected Crux `SKILL.md`: it is the parent of that skill's `skills/` directory. A reference to `${CRUX_PLUGIN_ROOT}/box/...` therefore points at the `box/` directory of the installed plugin. `${CLAUDE_PLUGIN_ROOT}` is a Claude Code-only variable and not a portable plugin path. It is not used in the routes below.

| Former entry | Current route |
|---|---|
| `task-planner` | `whiteboarding` for exploration; `author-promptbook` or a cycle for a tracked plan. The Python task-planner API remains documented in `${CRUX_PLUGIN_ROOT}/box/runtime-apis.md`. |
| `author-runbook` | Tracked planning by default; explicit generator instructions in `${CRUX_PLUGIN_ROOT}/box/operator-services.md`. |
| `visualize-run-progress` | `run-promptbook` status; the renderer script remains available. |
| `trace-runtime-ops`, `semantic-bridge`, `agent-identity` | Python APIs in `${CRUX_PLUGIN_ROOT}/box/runtime-apis.md`. |
| `serve-llm` | HTTP service instructions in `${CRUX_PLUGIN_ROOT}/box/operator-services.md`. |

These names no longer select installed skills. Existing runs, runbooks, identities, and Python modules remain in place.

### Extracting code docs

- **"Extract code docs"** runs the dispatcher (`scripts/extract-code-docs.py` in the plugin) per `bionic/manifest.yml`. It regenerates `bionic/code/` from source.
- **"Verify code docs"** is the dry-run version. It reports drift without writing.

Configure which extractors run by editing `code.extractors:` in `bionic/manifest.yml`. The plugin ships extractors for Elixir and Python, plus a fallback (header-comment scrape). More languages land as plugins under the plugin's `scripts/extractors/`.

**Python.** A `python` key under `code.extractors:` takes exactly three fields: `extractor`, `glob`, and the optional `include_private` (default on). Any other key at that level refuses the run:

```yaml
code:
  extractors:
    python:
      extractor: python
      glob: "src/**/*.py"
      include_private: true
```

The dispatcher runs the Python path under `uv run --no-config`, so `uv` ignores any `uv.toml` or `[tool.uv]` table. The script's PEP 723 block pins `griffelib==2.3.0`; any other installed version exits 2. The extractor reads your sources statically. It never imports or runs your code, installs its dependencies, or executes documentation examples.

The dispatcher reports one of these outcomes:

- Exit `0` means clean, no drift.
- Exit `1` with a `drift` payload means the on-disk docs are stale. Run without `--dry-run` to regenerate.
- Exit `1` with a `validation_errors` payload is a content refusal: a parse failure, an oversized source, an unowned output root, or an extractor name the plugin doesn't ship. That is broken input, not drift, and no regenerator run fixes it. This payload appears only under `--dry-run` (the `verify-code-docs` skill). In write mode the same content refusal exits `1` with a message on stderr and empty stdout, and no output byte changes.
- A configuration error also exits `1`, with a message on stderr and empty stdout, whether or not `--dry-run` was passed. Examples are a `--config` path that doesn't exist, a `--lang KEY` the manifest doesn't configure, or an unresolvable `.bionic.yml`/`.crux`.
- Exit `2` is a capability failure: an interpreter older than 3.13, or a missing or mismatched `griffelib`. The message goes to stderr, with empty stdout.
- A `uv` resolution failure exits with `uv`'s own code, also with empty stdout.

### Summarizing the current architecture

- **"Build the arch"** / **"summarize the current architecture"** / **"regenerate the architecture"** runs `derive-arch`. It detects your stack and regenerates `bionic/arch/`, the derived current-state map, wholesale from the project's own sources.
  - **Stacks.** Batteries-included packs cover Python, Ruby, Node.js, Elixir/Phoenix, and Swift. For Swift, that means Swift packages and Xcode projects, with targets, product types, membership, and `@main` owners. An XcodeGen or Tuist manifest missing its generated project file renders as missing input. A conditional build setting renders as a `conditional-setting` residual.
  - **Data model.** Each pack derives it from your ORM, schema, or type declarations: SQLAlchemy, SQLModel and Django models; ActiveRecord's `schema.rb`; Prisma, TypeORM and Sequelize; Ecto; and Swift `struct`, `class`, `enum` and `actor` declarations.
  - **Interface surface.** It comes from a committed OpenAPI document or the framework's routes. The Swift pack derives it from `public`, `package` and `open` declarations, every protocol, the package products, and the `@main` declarations.
  - **Module graph.** It comes from the import graph. The Swift pack adds the targets and dependencies that `Package.swift` manifests and Xcode project files declare.
  - **Coverage.** Each concern gets a `populated | stubbed` verdict, recorded in `_meta/coverage.json`. The same verdicts print as a coverage table, with one remediation line per non-`populated` concern. The tool reports what it found and grades nothing.
  - **Parsers.** A machine missing a stack's declared parser (`tree-sitter` plus its grammar) gets exit 2 with nothing written. Elixir needs `tree-sitter-elixir` and Swift needs `tree-sitter-swift`; Node.js and Ruby also declare grammars. The fix is the environment, not the docs.
- **"What's the current architecture?"** means reading `bionic/arch/overview.md` (or asking the librarian). Building is `derive-arch`'s job; answering from it is the librarian's.
- **"Escalate the arch runtime"** (`escalate-arch-runtime`, for Python projects) is an optional fidelity upgrade you may choose. You invoke it explicitly and it never auto-fires. It is never the remedy `derive-arch` points you at. It recovers what the static extractor missed, such as the route table and the ORM schema. To do so it runs your FastAPI/Flask/Django app's import-time code in a confined subprocess, behind the `CRUX_ARCH_ALLOW_RUNTIME=1` consent gate plus a per-run permission prompt. Results land in `bionic/inbox/` as advisory material, never in `bionic/arch/`.

Keep `arch` current by re-running `derive-arch` after a change to any input, or let a routine **"audit docs"** auto-regenerate a drifted spine. Never hand-edit `bionic/arch/`, because the next derive overwrites it.

### Asking questions

- **"What does X do?"** searches `bionic/code/` and cites pages.
- **"Why did we choose Y?"** searches `bionic/adrs/` and `bionic/briefs/`.
- **"What's the plan for Z?"** checks active promptbooks.
- **"What do we know about W?"** searches research.

Good answers can be filed back into `bionic/research/ideas/`; your agent will offer.

### Checking health

- **"Audit docs"** runs integrity checks across every enabled concern. It auto-fixes safe drift (counts, dates, missing index rows) and surfaces broken cases for your decision. Run it after every ~10 writes, after a large refresh, and before any release.
- **"Review the decisions"** (also "run a decision review", "do the decisions still serve the objectives", "is the decision set still right") runs `review-decisions`. It reads the decision set and measures it against `bionic/objectives.md`, writing one dated report per pass at `bionic/adrs/reviews/YYYY-MM-DD.md`.
  - **Findings.** They live in four sections: Propose, Amend, Repair, and Revoke. At most five findings survive across all four, per pass.
  - **Other sections.** Two further sections carry no findings and no cap. Keep lists decisions read this pass that still serve. Coverage names what the pass could not see. Zero findings is a legitimate outcome.
  - **Report.** It is hand-kept; only `bionic/adrs/reviews/index.md` is regenerated.
  - **Boundary.** The review proposes findings and transitions nothing. Enacting a finding is a separate act ("propose ADR" or "accept ADR-NNNN") that the review never invokes.

  Run the review weekly. `cleanup-campsite` nudges when the newest report is older than `adr_review_due_days`, which is seven by default.

---

## Tools & scripts

Everything your agent does is backed by Python in the plugin's `scripts/` directory. The docs tooling is stdlib-only, and the multi-model substrate (below) adds PEP 723-declared dependencies. **You almost never run these directly.** The skills invoke them for you, but knowing they exist helps when something looks off.

**Invoked by skills (you don't run these):**

| Script | Skill that calls it | What it does |
|---|---|---|
| `extract-code-docs.py` | `extract-code-docs` / `verify-code-docs` | Regenerates `bionic/code/` from source docstrings, via per-language plugins under `scripts/extractors/`. |
| `web-to-markdown.py` | `ingest-research` / `refresh-research-sources` | Fetches a URL into audited markdown. |
| `transcribe-video.py` | `ingest-research` | Transcribes a video source to text. |
| `visualize-run-progress.py` | `run-promptbook` status | Reads run progress; writes a byte-stable Markdown artifact only on explicit `--markdown`. |
| `write-journal.py` | `log-work` | Validates and writes a journal entry plus derived index and log operation, or one log-only operation. Reports partial writes for replay. |

**Validators (run on demand or in CI, and they never publish):**

| Script | What it does |
|---|---|
| `validate-promptbook.py` | Validates a promptbook or run `.yaml` against its draft-2020-12 JSON Schema **and** the cycle-coverage invariants (`--kind promptbook\|run`). This is the gate `dev-cycle`, `iterate`, and `patch-cycle` books must pass. |
| `check-blast-radius.py` | Compares a `patch` run's git-recorded changed paths against the blast radius its book declared. `archive-promptbook` runs it as a precondition. |

### Runtime requirements: `uv`, PEP 723, and PyYAML

> **PyYAML requirement.** `validate-promptbook.py` and `visualize-run-progress.py` require a real YAML parser, because the bundled minimal fallback isn't faithful enough for validation verdicts or content hashes. Without PyYAML they automatically re-run themselves under `uv run --no-project --with pyyaml>=6.0` when `uv` is installed. This is announced on stderr, and it may fetch PyYAML from your configured index on first use. Otherwise they exit **2** with a remediation message. Exit 2 always means *your environment*, never *your docs*. Locked-down environments can set `CRUX_NO_UV_REEXEC=1` to disable the auto-repair and install PyYAML themselves.
>
> **Dependency resolution for shipped scripts (PEP 723).** Shipped scripts whose documented invocation is `uv run …` carry PEP 723 inline metadata. On first use, `uv` resolves those dependencies **unpinned by hash** from *your configured index* (cached afterwards). This is the same trust model as the PyYAML re-exec above. Hermetic or locked-down environments should pre-provision the declared dependencies themselves rather than letting first use touch the network. For stricter reproducibility, pin resolution with `uv run --exclude-newer <date>` (or the `UV_EXCLUDE_NEWER` environment variable). `uv` itself is a prerequisite for those invocations. Without it the command fails at the shell (`command not found`); install it from https://docs.astral.sh/uv/.

### The multi-model substrate

The multi-model substrate lives under the plugin's `scripts/crux/` directory:

- the LLM router (`call-llm`)
- the multi-model `council`
- `srde`
- the tracer
- the identity, knowledge, and task-planning modules that power the agent layer

These need API keys (next section) and run under `uv`, which picks the interpreter each script's PEP 723 header names. crux supports Python 3.13 and 3.14. The retained HTTP service exposes the router to non-Python clients; see `${CRUX_PLUGIN_ROOT}/box/operator-services.md`. For tracing, probe coordination, identity, and task-planning APIs, see `${CRUX_PLUGIN_ROOT}/box/runtime-apis.md`.

### `forge-skill` and `retrospective`

The `forge-skill` capability-gap loop is a prose workflow and needs no API keys of its own.

**`forge-skill`** closes a capability gap mid-task by autonomously authoring or revising a project-local skill under `.claude/skills/`. Trigger phrases: *"forge a skill"*, *"author a skill for this"*, *"close this capability gap"*.

- **Autonomous by default.** It runs autonomously and reports after the fact.
- **Propose-first cases.** It waits for approval only in these cases:
  - the capability is outward-facing or irreversible (external sends, spend, publishing)
  - it would touch anything outside the repo, or any secrets
  - the gap would change project structure or external surfaces (those take the brief/ADR path instead)
- **Forge log.** Every forge act is recorded in the append-only log at `.claude/skills/forge-log.md`, a reviewable history of what was authored, when, and why.

**`retrospective`** is purposeful reflection over finished work. Trigger phrases: *"what should we learn from recent work"*, *"run a retrospective"*, *"retrospective over the last N books"*.

- It mines `bionic/log.md`, the work journal, and recent run snapshots to surface patterns.
- It distills findings into at most two skill proposals.
- It gates each proposal through the council before building it via `forge-skill`.
- Outcomes are recorded with a `Retrospective:` journal entry, using the heading `## [YYYY-MM-DD HH:MM] learning | Retrospective: …`. This is the marker `cleanup-campsite` tracks via `CLN-RETRO-1` to nudge you when enough archived books have accumulated since the last retrospective.

The **`crux-env` CLI** keeps your API keys outside any repo. Its own section follows.

---

## Working with secrets and API keys

`~/.crux/` is your per-user secrets home, outside any repo. One file (`~/.crux/env`) holds all API keys for every project on your machine that uses crux. The keys never leave your machine and never live in git.

You manage it with the `crux-env` CLI, which ships inside the installed plugin.

**Claude Code-specific invocation:**

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/crux-env.py" <subcommand> …
```

`${CLAUDE_PLUGIN_ROOT}` is the plugin's installed root, which Claude Code sets inside sessions. On other hosts, run `scripts/crux-env.py` from the installed plugin root (the `CRUX_PLUGIN_ROOT` described [above](#upgrading-from-the-seven-retired-entries)). The examples below abbreviate the invocation to `crux-env`; define your own shell alias if you use it often. The commands below are the whole interface.

### One-time setup

```
crux-env init
```

This creates `~/.crux/` with the right file modes (`0600` on `env`, `0700` on `secrets/`). It is idempotent, so re-running on an existing setup is safe.

### Adding a key

```
crux-env set OPENROUTER_API_KEY sk-or-xxxxxxxxxxxxxx
```

The value is stored in `~/.crux/env`, mode `0600`, so only you can read it. The key **name** is recorded in `~/.crux/log/crux-env.log`; the **value is not**. The log is safe to share for debugging.

### Validating what's required

```
crux-env check --project crux
```

This reads `~/.crux/required.yml` to see which env vars `crux` needs, then checks each is set. It exits `0` if everything's there, and exits `1` with a JSON list of what's missing if not. Use it before running a workflow that depends on external services.

### Listing required keys (without revealing values)

```
crux-env list --project crux
```

It prints something like:

```
crux — required:
  ✓ CRUX_HOME
crux — optional:
  ✓ CRUX_DEBUG
```

`✓` means set and `✗` means missing. Values are never printed, only key names.

### Removing a key

```
crux-env rm OLD_API_KEY
```

For rotation, `rm` the old key and `set` the new one.

### Reference

| Command | What it does |
|---|---|
| `crux-env init` | Create `~/.crux/` with safe modes. Idempotent. |
| `crux-env set KEY VALUE` | Store a key. Logs the name, never the value. |
| `crux-env rm KEY` | Remove a key. Logged. |
| `crux-env check --project <name>` | Verify required env vars are set. Exit 1 lists what's missing. |
| `crux-env list --project <name>` | Show key names and set/missing status. Never prints values. |

### What NEVER to do

- ❌ **Never commit `~/.crux/` to git.** It lives outside your repo by design, so don't symlink it in.
- ❌ **Never share `~/.crux/env`.** The mode-`0600` protection only matters if the file isn't posted to Slack.
- ❌ **Never paste a key into an issue, PR description, chat message, or screenshot.**
- ❌ **Never edit `~/.crux/log/crux-env.log`** to hide that you rotated a compromised key. The log is the audit trail.
- ✅ **Do rotate** any key you suspect was leaked. The cost is one `set` command.

For the full byte-level spec of the secrets store and CLI contract, see `bionic/AGENTS.md` in your project.

---

## Per-project configuration: the repo-root `.bionic.yml` file

Don't confuse the two crux config surfaces:

- The committed repo-root **`.bionic.yml` file** is per-project configuration.
- The user-home **`~/.crux/` directory** (above) is your secrets store and is never committed.

A typical `.bionic.yml` reads:

```yaml
config_version: "1"
docs_dir: bionic
artifact_prefix: ""
```

`.bionic.yml` supersedes the legacy repo-root `.crux` config file, which is still read for back-compat. Every tree crux creates carries one; `init-docs` writes it on bootstrap. Commit changes to it when you want a non-default convention:

- **`docs_dir`** relocates the tree (e.g. `documentation/` or `meta/docs/`). It is repo-root-relative, with no absolute paths and no `..`. The default is `bionic`.
- **`artifact_prefix`** brands artifact ids so they're distinguishable across repos. With `artifact_prefix: "CRX"`, new books and ADRs get ids like `CRX-ADR-0012`. Existing artifacts are never renamed.

To use a non-default `docs_dir` in a new repo, copy the shipped template to the repo root and edit it **before** you say "init docs". `init-docs` writes this file itself when it is absent, and merges (never clobbers) one you already committed. In Claude Code:

```bash
# Claude Code-specific: ${CLAUDE_PLUGIN_ROOT} is set by Claude Code
cp "${CLAUDE_PLUGIN_ROOT}/templates/bionic-yml.tmpl" .bionic.yml
```

On other hosts, copy `templates/bionic-yml.tmpl` from the installed plugin root.

Validation is fail-loud. A malformed or invalid `.bionic.yml` makes every consumer exit `1` with the validation error rather than silently falling back to defaults. To check yours in Claude Code:

```bash
# Claude Code-specific
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bionic-config.py"    # prints the resolved config as JSON
```

- ❌ **Never put secrets, tokens, or API keys in `.bionic.yml`.** It is committed to git, and unknown keys are silently ignored, so a misfiled secret wouldn't even produce an error. Secrets go in `~/.crux/` (above), full stop.

The full contract is in `bionic/AGENTS.md` in your project.

---

## Where to look first

In a crux-managed project, read in this order:

1. **`bionic/AGENTS.md`** (long; skim the first several sections first). This is the operational schema and the single source of truth for what lives where and who edits what.
2. **`bionic/index.md`**, the rollup catalog of everything, with a section per concern and counts.
3. **`bionic/adrs/`**, the "why" of the project. Walk forward in number order.
4. **`bionic/journal/`**, where the most recent month tells you what's happening now.
5. **`bionic/research/sources.md`**, the registry of external context the project draws from.
6. **`bionic/promptbooks/index.md`**, which shows what work is in flight.

---

## What you should NEVER do

- ❌ **Edit anything in `bionic/code/`.** Hand edits are deleted on every extract run.
- ❌ **Edit anything in `bionic/arch/`.** The next derive overwrites it.
- ❌ **Edit an ADR body once it's `Accepted`.** Write a new ADR that supersedes it.
- ❌ **Reorder `bionic/log.md` or edit past entries.** It's append-only audit history.
- ❌ **Delete `bionic/research/raw/`.** Old captures are the audit chain.
- ❌ **Reuse an ADR or promptbook number.** Numbers are monotonic forever.
- ❌ **Manually edit `bionic/research/sources.md` rows.** Let `ingest-research` and `audit-docs` maintain it.

If something feels wrong (a contradiction, a stale page, a missing source), say so. Don't fix it silently. The system catches drift via `audit-docs`, and your nose for "this looks off" is the canonical trigger.

---

## What you SHOULD do by hand

- Write `bionic/briefs/BRIEF-<slug>.md` files. These are *your* pre-decision exploration; your agent tracks them but doesn't author them.
- Co-author the `## Goal`, `## Strategy`, and `## Prompts` sections of an active promptbook.
- Edit the repo-root `AGENTS.md` to add project-specific notes your agents should know about. When the repository root holds no `AGENTS.md` or `CLAUDE.md` in any letter case, `init-docs` creates `AGENTS.md` with a ``See `bionic/AGENTS.md` for documentation operations.`` line and an instruction to read `bionic/objectives.md`. A `CLAUDE.local.md` does not block that create. When an exact `AGENTS.md` exists, `init-docs` appends each of the two lines where it is missing. Keep both when you edit. `init-docs` writes nothing to a `CLAUDE.md` in any letter case, to a case variant of `AGENTS.md`, or through a symlink. The init summary reports each such entry with its remedy, which names `audit-docs --migrate` where that migration applies. The `See` line is plain text, not an include.

---

## Day-one quick start

You're in a fresh repo with `bionic/` just initialized. To start using it:

1. **Capture today's intent as an ADR.** Say: *"Propose an ADR explaining why we're using crux for this project."* You'll review, then say *"Accept ADR-NNNN"* using the number it was given.
2. **Capture the planning material.** Drop your existing design notes, specs, and chat exports into `bionic/inbox/` and say *"Process inbox."* Your agent classifies each item and routes the research ones through `ingest-research`.
3. **Plan the first chunk of work.** Say: *"New promptbook for \<thing\>."* Co-author the prompt list, then say *"Run it."*
4. **Journal at end of day.** Say: *"Log today's work — `<one line summary>`."*
5. **Audit after the first ten writes.** Say: *"Audit docs."* Confirm there's no drift.

---

## When things go wrong

| Symptom | Try this |
|---|---|
| "Where did my agent put X?" | Look at `bionic/index.md` and `bionic/<concern>/index.md`. |
| `bionic/code/` has stale content | Run *"extract code docs"*. It regenerates fully. |
| ADR was accepted but it's wrong | Don't edit it. Write a new ADR that supersedes it. |
| Research synthesis page contradicts itself | Run *"refresh synthesis"*. Your agent walks you through reconciliation. |
| Lost track of a promptbook's progress | `bionic/promptbooks/index.md` shows the current run and percent complete. |
| Whole `bionic/` tree feels broken | Run *"audit docs"*. It checks every enabled concern. |
| A script exits `2` | Your environment is missing something (Python version, PyYAML, `griffelib`, a parser). Read the remediation message on stderr. The docs are not at fault. |

---

## Plugin and schema

This project uses the `crux` documentation tree at `schema_version 5` (the `bionic/` layout).

**Installing or upgrading.**

- **Claude Code.** Use `/plugin marketplace add bionic-coding/crux` and `/plugin install crux@crux`.
- **Codex.** Use `codex plugin marketplace add bionic-coding/crux` and `codex plugin add crux@crux`. Codex users can then install the ten Crux role agents personally with the `install-codex-agents` skill.

**Migrating from before 3.19.0.** After upgrading from a release before 3.19.0, say *"audit docs --migrate"* even when `schema_version` is already `"5"`. The migration converts tracked `CLAUDE.md` files to `AGENTS.md`.

- When a scope also holds an `AGENTS.md`, the legacy file wins. It becomes the new `AGENTS.md`, and the previous `AGENTS.md` is kept byte for byte under a name the report gives.
- Where a move is unsafe, the migration changes nothing in that scope and names the next step.
- It reports untracked and private suppressors without editing them.

### Recover older trees and Markdown promptbooks

The current plugin operates on tree schema 5 and executes YAML promptbooks only. For a schema-2, schema-3, or schema-4 tree, work on a copy or backup with the public `v3.23.2` release. Verify its annotated tag and commit before using its schema ladder:

```bash
recovery_dir="$(mktemp -d)"
git clone --branch v3.23.2 --single-branch https://github.com/bionic-coding/crux.git "$recovery_dir/crux"
git -C "$recovery_dir/crux" rev-parse refs/tags/v3.23.2
git -C "$recovery_dir/crux" rev-parse HEAD
```

The two results must be `c1298c4a9229ed41ae7017c25321d27e5b3f6e4d` and `08ee30ec2f1d1b4b0ce970f2e1582bb4f83cd20d`, respectively. Read that release's `crux/skills/audit-docs/SKILL.md` and use its 2→3→4→5 ladder in order. A valid `.migrating` marker follows its recorded resume or abandon procedure; an invalid marker or two ambiguous trees require investigation. The current plugin does not run the ladder. Its `audit-docs --migrate` handles instruction files on a schema-5 tree.

The tagged release can finish a Markdown run when its remaining prompts can truthfully be completed. Archive it there, then convert its book before its run with that release's `migrate-promptbooks` skill, which preserves Markdown originals. Validate the YAML files and their content-hash binding before returning to the current plugin.

The tag cannot deliberately abandon a Markdown run. If one cannot finish, keep its bytes unchanged as stranded, readable history. Continue separate work in new YAML books. Do not mark that run complete, skip unfinished prompts to migrate, or convert it in place.

Use a separate project copy for recovery. Before starting any host, inspect that copy's project-level Crux registrations and disable only the current version there. Keep unrelated entries and the original project unchanged. The places to check are:

- **Claude Code:** project settings and `.claude/skills/`.
- **Codex:** `.agents/plugins/marketplace.json`, `.agents/skills/`, and `.codex/config.toml`.
- **OpenCode:** project `opencode.json`, `.opencode/skills/`, and `.opencode/skill/`. OpenCode merges project `skills` arrays with XDG settings, so an isolated XDG directory alone does not remove a current project skill path.

If you cannot verify that only the tagged Crux skills are active, stop before mutating the copy.

Use only the tagged plugin during recovery.

**Claude Code.** Disable the currently installed Crux plugin in its installation scope with `claude plugin disable crux@crux`. Then start a separate session with `claude --plugin-dir "$recovery_dir/crux/crux"`. The `--plugin-dir` flag adds that source for the session; verify that the current plugin is disabled. After recovery, run `claude plugin enable crux@crux` and restart.

**Codex.** Use an isolated home so the normal plugin installation is absent. The tagged checkout contains a local marketplace named `crux` whose source is `./crux`. Codex's local-marketplace command accepts a directory path:

```bash
mkdir -p "$recovery_dir/codex-home"
CODEX_HOME="$recovery_dir/codex-home" codex plugin marketplace add "$recovery_dir/crux"
CODEX_HOME="$recovery_dir/codex-home" codex plugin add crux@crux
```

Start the recovery Codex session with that same `CODEX_HOME`, in a project copy without another project-scoped Crux plugin. Verify the tagged skill is available before mutation. End that session and return to your normal Codex home to resume the current plugin. [Codex's local-marketplace instructions](https://developers.openai.com/plugins/build/plugins) explain the path form. These commands have been checked against local CLI help, not exercised as a fresh-session recovery.

**OpenCode.** Use an isolated XDG config for a separate recovery session. Put this file at `$recovery_dir/opencode-config/opencode/opencode.json`, replacing `TAGGED_CHECKOUT` with the absolute path of `$recovery_dir/crux`:

```json
{"skills": ["TAGGED_CHECKOUT/crux/skills"]}
```

Create the config directory and start OpenCode with isolated paths:

```bash
mkdir -p "$recovery_dir/opencode-config/opencode" "$recovery_dir/opencode-data" "$recovery_dir/opencode-cache"
XDG_CONFIG_HOME="$recovery_dir/opencode-config" XDG_DATA_HOME="$recovery_dir/opencode-data" XDG_CACHE_HOME="$recovery_dir/opencode-cache" opencode
```

Verify that only tagged Crux skills load. Quit and restore the normal XDG settings after recovery. These recovery procedures have not been established as fresh-session executions in every host, so treat each host's first recovery run as something to verify step by step.
