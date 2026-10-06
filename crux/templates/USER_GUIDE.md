# Working with `bionic/` in {{repo_name}}

This project keeps its documentation in `bionic/` — seven concerns maintained by the `crux` Claude Code plugin. **You do not write these docs by hand.** You curate, decide, and discuss. Claude does the bookkeeping.

This guide is for humans. If you are an LLM agent picking up this repo, read `bionic/AGENTS.md` — that's the operational schema.

---

_This guide names the default `bionic/` layout. If this repo's root `.bionic.yml` sets a different `docs_dir`, that directory takes the place of `bionic/` everywhere below._

## At a glance (the 30,000ft view)

crux turns a `./bionic/` folder into a maintained knowledge base that you and Claude share. There are four moving parts:

1. **The `bionic/` tree — seven concerns.** Code docs, research wiki, ADRs, briefs, work journal, promptbooks, invariants. You curate and decide; Claude does the bookkeeping. → [The seven concerns](#the-seven-concerns)
2. **Skills — natural language, no slash commands.** "propose an ADR", "process inbox", "audit docs", "start a cycle", "forge a skill". Each is triggered by a phrase routed through the skill's description. → [What to say to Claude](#what-to-say-to-claude)
3. **Agents — ten roles over the skills.** `commander`, `architect`, `dev-lead`, `developer`, `reviewer`, `historian`, `librarian`, `brainstormer`, `night-gardener`, and `wayfinder` divide planning, implementation, review, preservation, retrieval, exploration, overnight work, and reconnaissance. → [The agent layer](#the-agent-layer)
4. **Three workflows for change.** `dev-cycle` (net-new / architectural — ADR + council + review), `iterate` (non-architectural fixes — verify + council + review, no ADR), and `patch-cycle` (a small reversible fix — five phases, one prompt each, with a declared blast radius). All three are tracked promptbooks. Trivial work: a plain promptbook or the skills directly. → [Planning multi-step work](#planning-multi-step-work--promptbooks--cycles)

Under the hood: stdlib Python **scripts** (extractors, validators, the LLM router) that the skills call for you, plus the `crux-env` **CLI** for secrets. → [Tools & scripts](#tools--scripts)

**If you read nothing else:** drop anything into `bionic/inbox/` and say *"process inbox"*; ask *"what does X do?"* / *"why did we choose Y?"*; and say *"start a cycle for X"* or *"iterate on X"* to ship a change with the receipts attached.

---

## The mental model

```
┌─ Your job ──────────────────────────────────────┐
│  • Make architectural decisions                  │
│  • Curate research (drop files, paste URLs)      │
│  • Direct the work (what to build next)          │
│  • Ask questions of the docs                     │
└─────────────────┬───────────────────────────────┘
                  │ conversation
                  ▼
┌─ Claude's job ──────────────────────────────────┐
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

You read the wiki. Claude writes it.

---

## The seven concerns

| Directory | What lives there | Who edits |
|---|---|---|
| `bionic/code/` | Auto-extracted from source code (docstrings, @doc, JSDoc) | **Regenerated — never edit by hand.** Hand edits are deleted on every extract run. |
| `bionic/research/` | Articles, papers, web pages, meeting notes, chat exports | You drop sources in the unified `bionic/inbox/`; `process-inbox` routes them to `ingest-research`, which captures them into the immutable `bionic/research/raw/` |
| `bionic/inbox/` | A drop folder for ANY raw input — files, URLs, notes, half-formed decisions | You drop; `process-inbox` classifies + confirms + dispatches to the right concern skill. Cross-concern staging, not a concern. |
| `bionic/adrs/` | Architecture Decision Records — the "why" of every load-bearing decision | You decide; Claude writes the ADR. **Once `Accepted`, the body is frozen.** |
| `bionic/briefs/` | Pre-decision exploration documents (`BRIEF-<slug>.md`) | You write; Claude tracks them |
| `bionic/journal/` | Day-by-day record of work performed (`YYYY-MM.md`) | Claude appends entries; you ask for them |
| `bionic/promptbooks/` | Plans of prompts you'd like to execute, plus immutable run snapshots | Co-authored. Mutable plan + frozen run history |
| `bionic/invariants/` | Pinned, executable statements of *what must be true* — a ledger page per pin plus the executable check suite in `invariants/checks/`, reconciled via `invariants/reconciliation.yml` | **Machine proposes, you ratify.** `recover-invariants` mines candidates as `observed`; you ratify/reject/retire via `transition-invariant`. Recovery never self-ratifies. |
| `bionic/arch/` (default-on) | Derived architecture spine — the current-state map: data model, interface surface, module graph, decision index, plus a synthesized overview | **Regenerated — never edit by hand.** The primary way to answer "how is this project shaped now?" Enrolled by default on new trees; say **"build the arch"** (`derive-arch`) to build the spine, and `audit-docs` keeps it current. |

The seventh concern, **invariants**, is the "far half of the bridge": where the other concerns record knowledge, invariants pin *what must stay true* as executable checks that survive regeneration. A new repo stands it up empty — an invitation to run `recover-invariants` when you're ready; an empty invariants concern is clean, not broken.

---

## The agent layer

Crux ships ten **agents** that operate the skills above. Claude Code loads the
source agents from the plugin. Codex and OpenCode use generated native forms.

| Agent | Reach for it when you want… | Bounded so it cannot… |
|---|---|---|
| `commander` | a whole cycle/promptbook driven end-to-end (it delegates each step) | edit code or docs itself |
| `brainstormer` | to explore a fuzzy idea before committing (drives `whiteboarding`) | write files or touch code |
| `architect` | a decision recorded + council-reviewed (`propose-adr` → `council` → accept) | implement code |
| `dev-lead` | implementation coordinated across parallel units | merge / push (human-gated) |
| `developer` | one scoped unit built test-first | re-delegate, or write docs/ADRs |
| `reviewer` | an independent check of a diff | edit files (it reports; never fix-and-hide) |
| `historian` | anything written under `bionic/` (intake, journaling, indexes) | edit source code |
| `librarian` | a question answered from `bionic/` (`query-docs`) | write anything |
| `night-gardener` | an overnight pass that records ideas, gaps, research, and news | push, merge, or send material externally |
| `wayfinder` | a condensed assessment of a large or uncertain source | write, execute, or delegate |

The source roles declare tool boundaries and required skills. Host permissions can
override a role's default sandbox, so the role prompt remains binding. The
agents embed their craft disciplines, including testing, verification,
debugging, and two-stage review.

**How you actually use them:** you rarely name an agent — a cycle (and the `commander`) dispatches them for you. But you can be explicit: *"have the architect propose an ADR for X"*, *"send this design to the council"*, *"have the reviewer check the diff"*, *"ask the librarian what we decided about Y"*.

In Codex, say **"install the Crux agents in Codex"** after installing the
plugin. The installer writes the ten namespaced `crux_*` roles to
`~/.codex/agents/` by default. Each role pins its catalog model and reasoning
effort and binds its declared skills to the installed plugin. An explicit
`--repo-root` selects one project's `.codex/agents/` directory.

An unchanged refresh is a no-op. Changed or stale managed files require
`--force` after review. Plugin relocation appears as drift because skill
bindings use absolute paths. `--check --project-context <repo>` reports managed
drift and project agents that shadow personal roles. The report separates
canonical expectations from managed TOML state parsed from disk. Its runtime
result stays `unverified` until fresh-session host evidence confirms discovery,
settings, skills, and representative workflows for the selected Codex version.

---

## What to say to Claude

These phrases trigger the right skill. Use them in natural-language sentences; Claude figures out the rest.

### Recording decisions

- **"Propose an ADR for X"** → Claude writes a new `ADR-NNNN-<slug>.md` in `bionic/adrs/`, status `Proposed`. You review the alternatives, then…
- **"Accept ADR-NNNN"** → Status flips to `Accepted`. Body is now frozen.
- **"Supersede ADR-NNNN with ADR-MMMM"** → Both ends of the supersession link update atomically.
- **"Deprecate ADR-NNNN"** → Decision retracted (no replacement).

ADRs are append-only history. You can't edit one once accepted; you write a new one that supersedes it.

### Capturing anything — the unified inbox

- **Drop anything in `bionic/inbox/`** and say "process inbox" — `process-inbox` classifies each item, shows you a confirmation batch (item → target skill), and on your `ok` dispatches each: research → `ingest-research`, decision → `propose-adr`, exploration → `propose-brief`, work note → `log-work`. It only auto-dispatches items it's confident about, never auto-accepts ADRs, and treats dropped content as data (not instructions).
- **Research items** still flow through `ingest-research` into the immutable `bionic/research/raw/` capture with an audited source page + synthesis updates — just via the unified inbox now.
- **Paste a URL** into `bionic/inbox/` (or `bionic/inbox/urls.md`, one per line; add `(static)` to opt a URL out of refresh checks) — `process-inbox` routes the batch to `ingest-research`, fetched via the bundled `web-to-markdown.py`.

When upstream sources may have changed:
- **"Refresh sources"** → re-fetches non-static URLs, files updates as new dated captures, flags affected synthesis pages.
- **"Refresh synthesis"** → walks you through accumulated markers (contradictions, source updates) per page.

### Journaling work

- **"Log work"** or **"journal this"** → adds an entry to `bionic/journal/YYYY-MM.md` with today's date and a category.
- Claude may also call this silently after meaningful operations (ADR accepted, promptbook completed, large refactor).

Categories: `decision | implementation | bug | learning | blocker | refactor | meeting | review | misc | release`.

`log-work` selects one procedure before writing. An interactive journal request adds a reflective monthly entry, regenerates the journal index, and records one `journal` operation. A silent call defaults to log-only: it requires a valid operation and writes only `bionic/log.md`. The bundled writer checks the request before writing and reports `complete`, `refused`, or `partial`. Keep the original request and its explicit local UTC offset for retry. A month-only partial entry cannot prove that offset; the writer warns `offset unverified`. If the offset was lost, stop automated replay and resolve it from evidence. A journal body allows 1–10 authored lines; an optional `Refs:` line is separate from that limit.

### Planning multi-step work — promptbooks & cycles

Four ways to drive multi-step work, in increasing rigor:

- **"Start a cycle for X"** (`dev-cycle`) — for **net-new / architectural** work. Assembles a tracked promptbook of ADR + dev + review modules: a decision is recorded as an ADR and council-reviewed, implemented, then independently reviewed (≥13 prompts).
- **"Iterate on X"** / **"remediate X"** (`iterate`) — for **non-architectural fixes** (bugs, drift, refinements to existing behavior). Same council + review rigor, but a *verify* module (reproduce + root-cause + council-review the diagnosis) instead of an ADR — there's no decision to record. If the verify council finds the work *is* actually architectural, it stops and routes you to `dev-cycle`.
- **"Patch this"** (`patch-cycle`) — for a **small reversible** non-architectural change you can bound by naming the paths it may touch. Five phases (verify, plan, implement, review, summary), one prompt each. You declare a *blast radius* up front: the council checks it is no wider than the work needs, and archival checks the paths the run actually changed against it, using git. A change that reaches outside the declaration cannot archive as delivered — it is re-authored as an `iterate` or a `dev-cycle`. Work needing an ADR is not a patch.
- **"New promptbook for X"** (`author-promptbook`) — a bespoke multi-prompt plan you co-author, with **no** enforced council/review. For sequences that don't need the ceremony.

Then drive any of them: **"run it"** starts an immutable run snapshot under `bionic/promptbooks/runs/PB-NNNN-<slug>/run-RUN-NNN.yaml`; **"advance"** / **"next prompt"** marks the current prompt done and moves on; **"abandon this run"** closes a run that will not finish; **"archive promptbook"** closes the book once its run has either completed with every prompt terminal (done / skipped / blocked) or been deliberately abandoned.

**"Promptbook status"** reads the current run through the existing progress renderer. The `run-promptbook` status procedure owns the read-only view. A terminal view or status answer writes nothing; a Markdown progress artifact requires an explicit `--markdown` request. Advancing changes the run snapshot and active book pointer only; it writes no per-prompt log entry.


For installations upgrading from the seven retired entries, use these routes:

| Former entry | Current route |
|---|---|
| `task-planner` | `whiteboarding` for exploration; `author-promptbook` or a cycle for a tracked plan. The Python task-planner API remains in `box/runtime-apis.md`. |
| `author-runbook` | Tracked planning by default; explicit generator instructions in `box/operator-services.md`. |
| `visualize-run-progress` | `run-promptbook` status; the renderer script remains available. |
| `trace-runtime-ops`, `semantic-bridge`, `agent-identity` | Python APIs in `box/runtime-apis.md`. |
| `serve-llm` | HTTP service instructions in `box/operator-services.md`. |

These names no longer select installed skills. Existing runs, runbooks, identities, and Python modules remain in place.

Books and runs are structured `.yaml` documents validated against a JSON Schema (the cycle kinds also pass the cycle-coverage invariants). Edit the prompts list mid-run? You can't — abandon the run, then author a successor book that names its predecessor. Numbers are never reused.

### Extracting code docs

- **"Extract code docs"** → runs the dispatcher in `crux/scripts/extract-code-docs.py` per `bionic/manifest.yml`. Regenerates `bionic/code/` from source.
- **"Verify code docs"** → dry-run version. Reports drift without writing.

Configure which extractors run by editing `code.extractors:` in `bionic/manifest.yml`. Day-one extractors: Elixir, fallback (header-comment scrape). More languages land as plugins under `crux/scripts/extractors/`.

### Asking questions

- **"What does X do?"** → searches `bionic/code/`, cites pages.
- **"Why did we choose Y?"** → searches `bionic/adrs/` and `bionic/briefs/`.
- **"What's the plan for Z?"** → checks active promptbooks.
- **"What do we know about W?"** → searches research.

Good answers can be filed back into `bionic/research/ideas/` — Claude will offer.

### Checking health

- **"Audit docs"** → runs ~54 integrity checks across all seven concerns. Auto-fixes safe drift (counts, dates, missing index rows); surfaces broken cases for your decision.

Run after every ~10 writes, after a large refresh, before any release.

- **"Review the decisions"** / "run a decision review" / "decision review" / "do the decisions still serve the objectives" / "review the ADR set against the objectives" / "is the decision set still right" → `review-decisions` reads the decision set and measures it against `bionic/objectives.md`. It writes one dated report per pass at `bionic/adrs/reviews/YYYY-MM-DD.md`. **Findings live in four sections — Propose, Amend, Repair, and Revoke — and at most five findings survive across all four, per pass.** Two further sections carry no findings and no cap: Keep lists decisions read this pass that still serve, and Coverage names what the pass could not see. Zero findings is a legitimate outcome. The report is hand-kept; only `bionic/adrs/reviews/index.md` is regenerated. The boundary: the review proposes findings and transitions nothing. Enacting a finding is a separate act — "propose ADR" or "accept ADR-NNNN" — that the review never invokes.[^review-boundary]

Run the review weekly. `cleanup-campsite` nudges when the newest report is older than `adr_review_due_days`, which is seven by default.

---

## Tools & scripts

Everything Claude does is backed by Python under the plugin's `scripts/` directory — stdlib-only for the docs tooling; the multi-model substrate scripts carry PEP 723 inline-metadata headers and run via `uv run "${CRUX_PLUGIN_ROOT}/scripts/..."`. **You almost never run these directly** — the skills invoke them for you — but knowing they exist helps when something looks off.

**Invoked by skills (you don't run these):**

| Script | Skill that calls it | What it does |
|---|---|---|
| `extract-code-docs.py` | `extract-code-docs` / `verify-code-docs` | Regenerates `bionic/code/` from source docstrings, via per-language plugins under `scripts/extractors/`. |
| `web-to-markdown.py` | `ingest-research` / `refresh-research-sources` | Fetches a URL into audited markdown. |
| `transcribe-video.py` | `ingest-research` | Transcribes a video source to text. |
| `visualize-run-progress.py` | `run-promptbook` status | Reads run progress; writes a byte-stable Markdown artifact only on explicit `--markdown`. |
| `write-journal.py` | `log-work` | Validates and writes a journal entry plus derived index and log operation, or one log-only operation. Reports partial writes for replay. |

**Validators (run on demand or in CI — they never publish):**

| Script | What it does |
|---|---|
| `validate-catalog.py` | Regenerates the plugin's `catalog/skills.json` (+ `agents.json`) from SKILL.md / agent frontmatter; `--dry-run` reports drift. The frontmatter is the source of truth. |
| `validate-promptbook.py` | Validates a promptbook or run `.yaml` against its draft-2020-12 JSON Schema **and** the cycle-coverage invariants (`--kind promptbook\|run`). This is the gate `dev-cycle` and `iterate` books must pass. |

> **Dependency resolution for shipped scripts (PEP 723).** Shipped scripts whose documented invocation is `uv run …` carry PEP 723 inline metadata; on first use, `uv` resolves those dependencies — **unpinned by hash** — from *your configured uv index* (cached afterwards). Hermetic or locked-down environments should pre-provision the declared dependencies themselves rather than letting first use touch the network; for stricter reproducibility pin resolution with `uv run --exclude-newer <date>` (or the `UV_EXCLUDE_NEWER` environment variable). `uv` itself is a prerequisite for those invocations — without it the command fails at the shell (`command not found`); install it from https://docs.astral.sh/uv/.

**Release tooling** (`promote-changelog.py`, `build-skill-zips.py`) packages and versions the plugin itself — used by crux's own release workflow, not something you run in a downstream project.

**The multi-model substrate** lives under the plugin's `scripts/crux/` directory: the LLM router (`call-llm`), the multi-model `council`, `srde`, the tracer, and the identity / knowledge / task-planning modules that power the agent layer. These need API keys (next section) and run under `uv` (Python ≥3.11, per each script's PEP 723 header); The retained HTTP service exposes the router to non-Python clients; see the shipped `box/operator-services.md` reference. For tracing, probe coordination, identity, and task-planning APIs, see `box/runtime-apis.md`. The **`forge-skill` capability-gap loop** (below) is a prose workflow — it needs no API keys of its own.

**`forge-skill`** closes a capability gap mid-task by autonomously authoring or revising a project-local skill under `.claude/skills/`. Trigger phrases: *"forge a skill"*, *"author a skill for this"*, *"close this capability gap"*. It runs autonomously and reports after the fact — propose-first (wait for approval) applies only when the capability is outward-facing or irreversible (external sends, spend, publishing), would touch anything outside the repo or any secrets, or when the gap would change project structure or external surfaces (those take the brief/ADR path instead). Every forge act is recorded in the append-only **forge log at `.claude/skills/forge-log.md`** — a reviewable history of what was authored, when, and why.

**`retrospective`** is purposeful reflection over finished work. Trigger phrases: *"what should we learn from recent work"*, *"run a retrospective"*, *"retrospective over the last N books"*. It mines `bionic/log.md`, the work journal, and recent run snapshots to surface patterns; distills findings into ≤2 skill proposals; gates each proposal through the council before building it via `forge-skill`. Outcomes are recorded with a `Retrospective:` journal entry (the `## [YYYY-MM-DD HH:MM] learning | Retrospective: …` heading) — the marker `cleanup-campsite` tracks via `CLN-RETRO-1` to nudge you when enough archived books have accumulated since the last retrospective.

The **`crux-env` CLI** keeps your API keys outside any repo — its own section follows.

---

## Working with secrets and API keys

`~/.crux/` is your per-user secrets home, outside any repo. One file (`~/.crux/env`) holds all API keys for every project on your machine that uses crux. The keys never leave your machine and never live in git.

You manage it with the `crux-env` CLI. The commands below are the whole interface.

### One-time setup

```
crux-env init
```

Creates `~/.crux/` with the right file modes (`0600` on `env`, `0700` on `secrets/`). Idempotent — re-running on an existing setup is safe.

_Optional convenience:_ add a shell alias for `python3 "${CRUX_PLUGIN_ROOT}/scripts/crux-env.py"` so you can call `crux-env` from anywhere.

### Adding a key

```
crux-env set OPENROUTER_API_KEY sk-or-xxxxxxxxxxxxxx
```

The value is stored in `~/.crux/env`, mode `0600` — only you can read it. The key **name** is recorded in `~/.crux/log/crux-env.log`; the **value is not**. The log is safe to share for debugging.

### Validating what's required

```
crux-env check --project crux
```

Reads `~/.crux/required.yml` to see which env vars `crux` needs, then checks each is set. Exits `0` if everything's there; exits `1` with a JSON list of what's missing if not. Use this before running a workflow that depends on external services.

### Listing required keys (without revealing values)

```
crux-env list --project crux
```

Prints something like:

```
crux — required:
  ✓ CRUX_HOME
crux — optional:
  ✓ CRUX_DEBUG
```

`✓` means set; `✗` means missing. Values are never printed — only key names.

### Removing a key

```
crux-env rm OLD_API_KEY
```

For rotation: `rm` the old, `set` the new. (A future `rotate` subcommand may do this atomically.)

### Reference

| Command | What it does |
|---|---|
| `crux-env init` | Create `~/.crux/` with safe modes. Idempotent. |
| `crux-env set KEY VALUE` | Store a key. Logs the name, never the value. |
| `crux-env rm KEY` | Remove a key. Logged. |
| `crux-env check --project <name>` | Verify required env vars are set. Exit 1 lists what's missing. |
| `crux-env list --project <name>` | Show key names and set/missing status. Never prints values. |

### What NEVER to do

- ❌ **Never commit `~/.crux/` to git.** It lives outside your repo by design — don't symlink it in.
- ❌ **Never share `~/.crux/env`.** The mode-`0600` protection only matters if it's not posted to Slack.
- ❌ **Never paste a key into an issue, PR description, chat message, or screenshot.**
- ❌ **Never edit `~/.crux/log/crux-env.log`** to hide that you rotated a compromised key. The log is the audit trail.
- ✅ **Do rotate** any key you suspect was leaked — the cost is one `set` command.

### Where to read more

- `bionic/AGENTS.md` §13 — the full byte-level spec for the secrets store and CLI contract.

---

## Where to look first

You just cloned this repo. Read in this order:

1. **`bionic/AGENTS.md`** (~1000 lines — skim §1–§7 first) — the operational schema. The single source of truth for what lives where and who edits what.
2. **`bionic/index.md`** — rollup catalog of everything. Section per concern with counts.
3. **`bionic/adrs/`** — start at the meta-ADR (numbered 0000), walk forward in number order. This is the "why" of the project.
4. **`bionic/journal/`** — the most recent month tells you what's happening now.
5. **`bionic/research/sources.md`** — registry of external context the project draws from.
6. **`bionic/promptbooks/index.md`** — what work is in flight.

---

## What you should NEVER do

- ❌ **Edit anything in `bionic/code/`.** Hand edits are deleted on every extract run.
- ❌ **Edit an ADR body once it's `Accepted`.** Write a new ADR that supersedes it.
- ❌ **Reorder `bionic/log.md` or edit past entries.** It's append-only audit history.
- ❌ **Delete `bionic/research/raw/`.** Old captures are the audit chain.
- ❌ **Reuse an ADR or promptbook number.** Numbers are monotonic forever.
- ❌ **Manually edit `bionic/research/sources.md` rows.** Let `ingest-research` and `audit-docs` maintain it.

If something feels wrong (a contradiction, a stale page, a missing source), say so. Don't fix it silently — the system catches drift via `audit-docs`, and your nose for "this looks off" is the canonical trigger.

---

## What you SHOULD do by hand

- Write `bionic/briefs/BRIEF-<slug>.md` files — these are *your* pre-decision exploration. Claude tracks them but doesn't author them.
- Co-author the `## Goal`, `## Strategy`, and `## Prompts` sections of an active promptbook.
- Edit the repo-root `AGENTS.md` to add project-specific notes your agents should know about. When the repository root holds no `AGENTS.md` or `CLAUDE.md` in any letter case, `init-docs` creates that file with a ``See `bionic/AGENTS.md` for documentation operations.`` line and an instruction to read `bionic/objectives.md`. A `CLAUDE.local.md` does not block that create. When an exact `AGENTS.md` exists, `init-docs` appends each of the two where it is missing. Keep both when you edit. `init-docs` writes nothing to a `CLAUDE.md` in any letter case, to a case variant of `AGENTS.md`, or through a symlink. The init summary reports each such entry with its remedy, which names `audit-docs --migrate` where that migration applies. The `See` line is plain text, not an include.

---

## Day-one quick start

You're in a fresh repo with `bionic/` just initialized. To start using it:

1. **Capture today's intent as an ADR.** Say: *"Propose an ADR explaining why we're using crux for this project."* You'll review, then *"Accept ADR-NNNN"* (using the number it was assigned).
2. **Capture the planning material.** Drop your existing design notes / specs / chat exports into `bionic/inbox/` and say *"Process inbox."*
3. **Plan the first chunk of work.** Say: *"New promptbook for <thing>."* Co-author the prompt list. Then *"Run it."*
4. **Journal at end of day.** Say: *"Log today's work — `<one line summary>`."*
5. **Audit after the first ten writes.** Say: *"Audit docs."* Confirm there's no drift.

---

## When things go wrong

| Symptom | Try this |
|---|---|
| "Where did Claude put X?" | Look at `bionic/index.md` and `bionic/<concern>/index.md`. |
| `bionic/code/` has stale content | Run *"extract code docs"* — it regenerates fully. |
| ADR was accepted but it's wrong | Don't edit — write a new ADR that supersedes it. |
| Research synthesis page contradicts itself | Run *"refresh synthesis"* — Claude walks you through reconciliation. |
| Lost track of a promptbook's progress | `bionic/promptbooks/index.md` shows current_run and percent complete. |
| Whole `bionic/` tree feels broken | Run *"audit docs"* — 45 checks across all concerns. |

---

## Plugin and schema

This project uses the `crux` documentation tree at `schema_version 5` (the
`bionic/` layout). The plugin installs through the Claude Code marketplace with
`/plugin marketplace add bionic-coding/crux` and `/plugin install crux@crux`.
In Codex, use `codex plugin marketplace add bionic-coding/crux` and
`codex plugin add crux@crux`. Update with the same commands for your platform.
After upgrading from a release before 3.19.0, say *"audit docs --migrate"* even when
`schema_version` is already `"5"`. The migration converts tracked `CLAUDE.md` files
to `AGENTS.md`. When a scope also holds an
`AGENTS.md`, the legacy file wins: it becomes the new `AGENTS.md`, and the previous
`AGENTS.md` is kept byte for byte under a name the report gives. Where a move is
unsafe, the migration changes nothing in that scope and names the next step. It reports untracked and private suppressors without editing them. Codex users can install the
ten Crux role agents personally with the `install-codex-agents` skill.

### Recover older trees and Markdown promptbooks

The current plugin operates on tree schema 5 and executes YAML promptbooks only.
For a schema-2, schema-3, or schema-4 tree, work on a copy or backup with the
public `v3.23.2` release. Verify its annotated tag and commit before using its
schema ladder:

```bash
recovery_dir="$(mktemp -d)"
git clone --branch v3.23.2 --single-branch https://github.com/bionic-coding/crux.git "$recovery_dir/crux"
git -C "$recovery_dir/crux" rev-parse refs/tags/v3.23.2
git -C "$recovery_dir/crux" rev-parse HEAD
```

The two results must be `c1298c4a9229ed41ae7017c25321d27e5b3f6e4d`
and `08ee30ec2f1d1b4b0ce970f2e1582bb4f83cd20d`, respectively. Read that
release's `crux/skills/audit-docs/SKILL.md` and use its 2→3→4→5 ladder in order.
A valid `.migrating` marker follows its recorded resume or abandon procedure;
an invalid marker or two ambiguous trees require investigation. The current
plugin does not run the ladder. Its `audit-docs --migrate` handles instruction
files on a schema-5 tree.

The tagged release can finish a Markdown run when its remaining prompts can
truthfully be completed. Archive it there, then convert its book before its
run with that release's `migrate-promptbooks` skill. It preserves Markdown
originals. Validate the YAML files and their content-hash binding before
returning to the current plugin. The tag cannot deliberately abandon a
Markdown run. If one cannot finish, keep its bytes unchanged as stranded,
readable history. Continue separate work in new YAML books; do not mark that
run complete, skip unfinished prompts to migrate, or convert it in place.

Use a separate project copy for recovery. Before starting any host, inspect
that copy's project-level Crux registrations and disable only the current
version there: Claude Code's project settings and `.claude/skills/`, Codex's
`.agents/plugins/marketplace.json`, `.agents/skills/`, and `.codex/config.toml`,
and OpenCode's project `opencode.json`, `.opencode/skills/`, and
`.opencode/skill/`. Keep unrelated entries and the original project
unchanged. OpenCode merges project `skills` arrays with XDG settings, so an
isolated XDG directory alone does not remove a current project skill path.
If you cannot verify that only the tagged Crux skills are active, stop before
mutating the copy.

Use only the tagged plugin during recovery. In Claude Code, disable the
currently installed Crux plugin in its installation scope with
`claude plugin disable crux@crux`, then start a separate session with
`claude --plugin-dir "$recovery_dir/crux/crux"`. The `--plugin-dir` flag adds
that source for the session; verify that the current plugin is disabled.
After recovery, run `claude plugin enable crux@crux` and restart.

In Codex, use an isolated home so the normal plugin installation is absent.
The tagged checkout contains a local marketplace named `crux` whose source
is `./crux`. Codex's local-marketplace command accepts a directory path:

```bash
mkdir -p "$recovery_dir/codex-home"
CODEX_HOME="$recovery_dir/codex-home" codex plugin marketplace add "$recovery_dir/crux"
CODEX_HOME="$recovery_dir/codex-home" codex plugin add crux@crux
```

Start the recovery Codex session with that same `CODEX_HOME`, in a project
copy without another project-scoped Crux plugin. Verify the tagged skill is
available before mutation. End that session and return to your normal Codex
home to resume the current plugin. [Codex's local-marketplace instructions](https://developers.openai.com/plugins/build/plugins)
explain the path form; these commands have been checked against local CLI
help, not exercised as a fresh-session recovery.

In OpenCode, use an isolated XDG config for a separate recovery session. Put
this file at `$recovery_dir/opencode-config/opencode/opencode.json`, replacing
`TAGGED_CHECKOUT` with the absolute path of `$recovery_dir/crux`:

```json
{"skills": ["TAGGED_CHECKOUT/crux/skills"]}
```

Create the config directory and start OpenCode with isolated paths:

```bash
mkdir -p "$recovery_dir/opencode-config/opencode" "$recovery_dir/opencode-data" "$recovery_dir/opencode-cache"
XDG_CONFIG_HOME="$recovery_dir/opencode-config" XDG_DATA_HOME="$recovery_dir/opencode-data" XDG_CACHE_HOME="$recovery_dir/opencode-cache" opencode
```

Verify that only tagged Crux skills load; quit and restore the normal XDG
settings after recovery. This follows this repository's OpenCode `skills`
array contract. The local tagged-script and fixture checks do not establish
fresh-session execution in Claude Code, Codex, or OpenCode; publication still
requires those three host observations.

---

_Generated by `init-docs` from `${CRUX_PLUGIN_ROOT}/templates/USER_GUIDE.md` (the shipped template this file was created from) on {{today}}. Re-running `init-docs --force` overwrites this file._

[^review-boundary]: rule:review-proposes-and-enacts-nothing
