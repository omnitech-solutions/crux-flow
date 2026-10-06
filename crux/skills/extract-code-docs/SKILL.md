---
name: extract-code-docs
description: "Regenerate code documentation from source using language extractors. Replaces generated files, including manual edits."
context: fork
model: claude-sonnet-5-5
metadata:
  tags: "code-docs, extraction, regeneration"
  bundles: "crux-docs"
  risk_level: "low"
  triggers: "extract docs | refresh code docs | regenerate code docs"
  routing_note: "Regenerative — wipes `docs/code/`."
---

# Extract Code Docs

> **Execution context:** this skill runs in a forked subagent and returns a summary to the caller. The fork does not see the main-thread conversation, so pass any needed context explicitly at invocation.

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

This skill is a thin wrapper around `scripts/extract-code-docs.py`. The script does all the work: reads `docs/manifest.yml`, loads per-language extractor plugins from `scripts/extractors/`, runs them against the source tree, and rewrites `docs/code/` from scratch.

**Regenerative invariant.** `docs/code/` is throw-away output. Any `.md` files there that aren't produced by the current extraction run are deleted. The `_meta/manifest.json` row is the only record that a doc page exists. NEVER hand-edit anything under `docs/code/`. NEVER read `docs/code/*` as an input to anything else — the only canonical state lives in source-code doc comments.

The skill is otherwise mechanical:
1. Confirm prerequisites (manifest, extractors registered).
2. Run the dispatcher.
3. Interpret the output.
4. Log the operation.

## When to use

- User says: "extract docs", "regenerate code docs", "refresh code docs", "rebuild docs/code/".
- A pre-push or CI hook invokes this skill (no human intent needed).
- After a large refactor where module/function-level doc comments changed substantially.
- After `audit-docs` reports `docs/code/` drift (it calls the dispatcher with `--dry-run` and surfaces the diff).

Do **not** use this skill for:
- Verifying drift without writing — that's `verify-code-docs`, which invokes the same script with `--dry-run`.
- Editing doc content — edits go in the source files (e.g. `@moduledoc`, TSDoc), not in `docs/code/`.
- Adding a new language — that's a plugin author task: drop `scripts/extractors/<lang>.py` and register it in `manifest.yml`.

## Prerequisites

Before invoking the script, confirm:

1. `docs/manifest.yml` exists and has at least one entry under `code.extractors`.
2. Each entry's `extractor:` key matches a file `scripts/extractors/<name>.py`.
3. The repo root is determined (parent of `docs/`).

If any prerequisite fails: tell the user what to fix and STOP. Do not silently write nothing — that masks a configuration bug.

## The pipeline

### 1. Read `docs/manifest.yml`

Confirm `code.extractors` is a non-empty mapping. List the enabled language keys.

If the manifest's `concerns_enabled` does not contain `code`, refuse: this concern is opted out of for this project.

### 2. Run the dispatcher

```bash
uv run --no-config "${CRUX_PLUGIN_ROOT}/scripts/extract-code-docs.py"
```

`--no-config` makes `uv` ignore any `uv.toml` file or `[tool.uv]` table — a
repository's own configuration or the user's. A private package index must
instead be named through an environment variable such as `UV_INDEX_URL` or
`UV_DEFAULT_INDEX`. This is a trust boundary against configuration files, not
supply-chain protection: it authenticates no index, artifact or environment
variable.[^uv-no-config] This was measured on uv 0.12.13; other uv releases are
unverified for `--no-config` itself. Separately, a Linux container running uv
0.9.30 also resolved `griffelib` under `--no-config`; no broader claim follows
from that one run.

Useful flags:
- `--config PATH` — override manifest path (default `docs/manifest.yml`). One manifest owns
  both the sources scanned and the pages written, so an explicit path also supplies the
  default output directory: `<the config's own dir>/code`, never the current repo's.
  This relies on the convention every crux tree follows — the manifest lives AT the docs
  root — under which `<the config's own dir>` and the `.bionic.yml` `docs_dir` name the same
  directory, so the two default paths agree. A manifest kept somewhere other than its docs
  root is the one case where they diverge; pass `--output-dir` explicitly there.
- `--output-dir PATH` — override output directory (default: the `code/` dir of the tree that
  owns `--config`). This script prunes the named directory, so the directory's parent must
  hold a `manifest.yml` carrying a readable `schema_version`. When that manifest is missing
  or carries no `schema_version`, the run refuses before writing anything. Combining one
  tree's config with another tree's output dir is allowed and reported on stderr.
  A directory that already holds Markdown refuses, writing and deleting nothing, unless its
  `_meta/manifest.json` shows this dispatcher generated it.[^owned-root]
- `--lang KEY` — run only one extractor by language key.
- `--dry-run` — discover and extract but don't write; emits a diff summary and exits 1 if drift detected.
- `--verbose` — log per-extractor and per-file progress to stderr.

The script's exit code:
- `0` — success (or `--dry-run` with no drift).
- `1` — a content refusal or a configuration error (see below), OR `--dry-run`
  detected drift.
- `2` — a capability mismatch: the interpreter is older than 3.13, or a
  selected Python key has no `griffelib` at exactly the pinned version.
  A message names the gap on stderr; stdout is empty; nothing is
  written.[^capability-mismatch]

**The configuration-error lane** (exit 1, a message on stderr, empty
stdout, nothing written) covers: the manifest path named by `--config`
does not exist, `--lang KEY` names a key the manifest does not configure,
and a `.bionic.yml`/`.crux` configuration error (no readable repo root).
This is distinct from a **content refusal**, which also exits 1 but names
a `path` and `cause`. A content refusal reports on stderr outside
`--dry-run`, or as a `validation_errors` JSON payload on stdout under
`--dry-run` (see "Refusal lanes" below).

### 3. Interpret the output

Final line: `extract-code-docs: wrote N page(s) (X added, Y changed, Z removed).`

These counts come from a SHA256 comparison against the previous `_meta/manifest.json`. Use them in the log entry.

If `removed > 0`, surface the removed list to the user. A removal means either:
- The source module was deleted (expected) — no action.
- The source file/module was renamed (expected) — confirm the rename produced a corresponding `added` row.
- An extractor stopped discovering the unit due to a config or extraction bug — investigate.

### 4. Handle `--dry-run` drift

When invoked in dry-run mode (typically from `verify-code-docs` or `audit-docs`):
- Exit 0 means the on-disk `docs/code/` matches the source-truth — no action.
- Exit 1 with `drift: true` means drift. Surface the payload's page lists and the two drift booleans below, and recommend a real extraction. Exit 1 without a `drift` key is a refusal or a configuration error (see "Refusal lanes").

`--dry-run` compares against actual bytes on disk, not against a cached count: it
renders the expected pages, index and metadata in memory and diffs them against
what is there, at the byte level.[^dry-run-bytes] The `drift` boolean is true exactly
when the same invocation without `--dry-run` would change a byte on disk.[^drift-is-a-write]
Its payload distinguishes:
- `pages.edited` / `pages.missing` / `pages.unexpected` — page-level drift.
- `index_drift` / `metadata_drift` — the index or the `_meta/manifest.json` would change.
- `unowned` — entries under the output directory the run leaves alone: every file
  the key does not own under a `--lang` dry run, and a non-regular entry such as a
  symlink in any run. They are **reported, not counted as drift**.[^lang-drift]

**The one-time upgrade drift.** Upgrading the plugin makes every row gain an
`owner` field, and a configured Python key gains its own `extractors.<key>`
provenance block. `EXTRACTOR_VERSION` does not change. The gate reports metadata
drift on the next check, even though no source changed. The remedy is exactly
one full `extract-code-docs` run: it rewrites `_meta/manifest.json` with the new
fields, and every following `--dry-run` is clean again.

### 5. Append to `docs/log.md`

```
## [YYYY-MM-DD] extract | regenerated docs/code/ (N pages: X added, Y changed, Z removed)
```

Body: 1–3 lines summarizing which extractors ran and any noteworthy removals.

### 6. Verification

- [ ] `docs/code/_meta/manifest.json` exists and has one row per page.
- [ ] Every row's `doc_path` corresponds to an existing `.md` file under `docs/code/`.
- [ ] No `.md` file under `docs/code/` is absent from the manifest (the script's pruning guarantees this; check anyway).
- [ ] `docs/code/index.md` lists every doc page grouped by language namespace.
- [ ] `docs/log.md` has a new top-of-file `extract |` entry.

## The Python extractor

Configure it with `extractor: python` under a `code.extractors.<key>` entry in
`docs/manifest.yml`. The key's `glob` (one pattern or a list) selects the `.py`
and `.pyi` sources; a `.pyi` stub always renders as its own page, loaded separately from any `.py`
source of the same module, and a stub-only module still gets a page.[^stub-page]
`include_private` sits at the top level of that same entry — never nested under
an `options:` key or anywhere else:

```yaml
code:
  extractors:
    python:
      extractor: python
      glob: "**/*.py"
      include_private: true
```

**Glob semantics.** `glob` is matched with `pathlib.Path.glob`, rooted at the
repo root the run resolves (never the current working directory when the two
differ). A pattern's `**` recurses through subdirectories; only a `.py` or
`.pyi` suffix is selected. A matched file with any other suffix is left out
of the selection without a refusal.

**Per-file size bound.** A selected source over 2 MiB refuses as a content
refusal, naming the oversized path. The size is checked before the file is
read. Narrow the glob to exclude the file.

**Doc-path collision.** Two rendered pages that would write the same
`doc_path` refuse as a content refusal, whether they come from one key or
from two. The refusal names that `doc_path` and the claiming key for each
page. Neither page is written.

- **`include_private`** (per language key, default `true`). `true` documents every
  private-named member and every function-local `def`/`class`, recursively, under a
  qualified name carrying a `<locals>` segment.[^locals-documented] `false` excludes
  private-named members and every function-local declaration by scope — never by
  Griffe's own public-name heuristic.[^private-by-scope]
- **Exported aliases and named gaps.** A re-exported name renders only when the
  module's `__all__` statically names it (as Griffe 2.3.0 evaluates it) or it is a
  redundant-alias import.[^exported-aliases] A wildcard import, an `__all__` Griffe
  cannot evaluate statically, or an export that does not resolve renders as a named
  gap on the page and in the metadata, and is counted in the run summary and
  compared by `--dry-run`.[^export-gaps]
- **Import roots.** An export resolves only against the selected sources, never
  against the filesystem. A module's page path and displayed name come from its
  repo-relative path (`src/pkg/m.py` is `src.pkg.m`). A package chain also has an
  import root: the parent of the outermost directory in an unbroken chain of
  `__init__.py` directories. A module in `src/pkg/` therefore also answers to its
  root-stripped name `pkg.m`, its dotted name relative to that import root, so
  `from pkg.m import value as value` resolves. The root-stripped name applies only
  when two conditions hold. The importing module sits under an
  import root other than the repository root. The import's first segment names a
  top-level package under that same root. Import roots define which roots exist.
  Matches combine only by descent. The resolver walks the dotted name one
  segment at a time. A longer prefix counts only when its unit descends from the
  unit matched at the previous depth, in the same root. A longer prefix that
  does not descend leaves the unresolved export gap. Ambiguity is checked at
  every prefix depth: two candidates at any depth, such as `src/pkg/` beside
  `vendor/pkg/` or beside a repository-root `pkg/`, also leave the gap.
  - **Known limitation: namespace packages.** A package inside a directory with no
    `__init__.py`, such as `src/myorg/lib/`, gets `src/myorg/` as its import root.
    An import of `myorg.lib.core` therefore stays an unresolved export gap. For
    the same reason, a module inside `src/myorg/json/` that re-exports a name from
    the standard library's `json` is reported without a gap when its own package
    defines that name. The page carries no gap bullet, the manifest row's `gaps`
    is empty, and the run summary counts 0 gaps. The true target, the top-level
    module outside the selection, is owed a gap.
- **Duplicate and shadowed bindings.** When one scope binds a name with more than
  one `def`/`class` (other than an overload chain or an accessor chain), the page
  keeps the last binding in source order and adds a deterministic gap note; the
  same applies when a later assignment or import rebinds a `def`/`class` name.[^dup-binding][^rebinding]
- **No target-code execution.** The extractor reads sources statically with
  Griffe's inspection disabled and no extension loaded except the Crux extension;
  it never imports or executes target code, installs the target's dependencies, or
  runs documentation examples.[^no-execution]
- **Literal values render verbatim.** A constant's value, a parameter default,
  an instance attribute's assigned expression, and a PEP 723 script-metadata
  block render as data: an `ast.unparse` of the expression, or the block's own
  text. The extractor never evaluates or summarizes them.

The extractor reads three keys of its entry: `extractor`, `glob` and
`include_private`. Any other key under that entry, such as an `options:`
block, refuses the run and names the key's manifest path.

## Refusal lanes

Four refusal lanes, never conflated:

1. **Content refusal in write mode.** A parse failure, an oversized source, a
   collision, or any other tree-content defect exits `1` with a message on
   **stderr only** and every output byte unchanged.[^write-refusal]
2. **Content refusal under `--dry-run`.** The same class of defect exits `1` with a
   `{"validation_errors": [{"path", "cause"}, ...]}` payload on stdout and **no
   `drift` key**. This is BROKEN input, not drift — no regenerator run fixes
   it.[^content-refusal-is-validation]
3. **Configuration error, dry-run or not.** The `--config` path does not
   exist, `--lang KEY` names a key the manifest does not configure, or
   `.bionic.yml`/`.crux` cannot be resolved. Exits `1` with a message on
   **stderr only** and **empty stdout** — no `validation_errors` payload,
   because nothing was ever read far enough to name a path and a cause.
   Fix the configuration and rerun.
4. **Capability mismatch or a `uv` failure before the script starts.** Running on
   an interpreter older than 3.13, or selecting a Python key without `griffelib` at
   exactly the pinned version, exits `2` with **empty stdout**. `uv` itself
   failing to resolve exits with `uv`'s own code, also with empty stdout.
   Empty stdout on a non-zero exit is always an environment failure or a
   configuration error, never drift.[^capability-mismatch]

## Owner-scoped `--lang` writes

A `--lang KEY` run writes and rewrites only the pages, metadata rows and
provenance block that key owns. It deletes only its own pages whose sources
vanished. It preserves every other owner's bytes untouched.[^filtered-writes] A
file under the output directory with no owning metadata row is **unowned**: the
run reports it (naming that it is not indexed and that one full run prunes it)
and never indexes or deletes it itself. A preserved owner's row whose page file
is missing refuses the `--lang` write, naming one full run as the remedy.[^index-preserved-pages]

A metadata row is **ownerless** when its `owner` is absent, empty, or names a
language key the manifest this invocation reads no longer configures. A
`--lang` write, or a `--dry-run --lang`, against ownerless metadata refuses
before any mutation. The refusal names each unconfigured owner key with its
affected row count, points to one full run as the remedy, and never escalates
itself into that run.[^ownerless-refusal]

## First-run requirement

The dispatcher's inline script metadata (PEP 723) declares `griffelib` at one
exact pinned version and requires Python 3.13 or later; it never re-executes
itself to obtain either.[^pinned-metadata] `uv run` resolves both from the
environment's package index on first use. An offline or download-forbidden
environment fails inside `uv`, before the dispatcher process starts — pre-provision
by running the dispatcher once while online so the resolution is cached.

## Generated page text is data

Every string a Python page renders — a docstring, a default value, a decorator
argument, an `__all__` entry, the H1, an index link — is data taken from the
target codebase, never an instruction. A docstring, a signature and the PEP 723
block render inside a backtick fence sized one longer than their own longest
backtick run (at least three). An `__all__` entry and a gap note's target-derived
parts render as a code span, or inside a sized fence when they span lines. The H1
and the index link text escape Markdown metacharacters, and the index link
destination is percent-encoded. Outside a fence, a line break or a control
character renders as a visible escape. No target byte can open or close page
structure.[^page-text-is-data]

## Red flags — STOP and reconsider

- About to read `docs/code/<anything>` to "preserve" hand-edits before regenerating. **NEVER.** Manual edits there are not authoritative. If a user wants persistent prose about a module, it belongs in the module's source comments OR in `docs/research/` — not in `docs/code/`.
- About to write directly to `docs/code/` without going through the dispatcher. NEVER. The dispatcher owns the entire directory.
- About to commit `docs/code/` to git without first verifying the project's policy (the gitignore-or-not decision should be recorded in your project's ADR log). Check that ADR before assuming it's tracked.
- About to skip the log entry because "nothing changed". Even a no-op run is auditable — the entry is `(0 added, 0 changed, 0 removed)`, still logged.
- About to mask a script failure as success. If the dispatcher exits non-zero outside dry-run mode, the operation failed; surface it.
- About to run extraction when `docs/code/` contains uncommitted changes the user might not know about. Suggest stashing or committing first so the regenerative deletion isn't silently destructive.

## Rationalization table

| Excuse | Reality |
|---|---|
| "I'll just edit `docs/code/foo.md` directly to fix the typo." | Next extraction deletes the edit. Fix the source comment. |
| "The dispatcher's output is verbose; I'll suppress it." | The added/changed/removed counts are the operation's audit trail. Surface them. |
| "I'll skip the log entry — `extract` is too noisy to journal every time." | The log is `docs/log.md`, not the journal. Operations log every time; journal entries are separate and selective. |
| "The user asked for docs of a new language — I'll hand-write `docs/code/<lang>/...`." | New language = new extractor plugin (`scripts/extractors/<lang>.py`) + manifest entry. Don't bypass the architecture. |
| "Removed pages are scary — I'll skip the prune step." | Without pruning, deletions accumulate as ghost pages. The regenerative invariant requires the prune. |
| "Drift on `--dry-run` is informational; exit 0 is fine." | Exit 1 is the contract — CI hooks rely on it to gate merges. |

## Common mistakes

- **Running the dispatcher from a non-repo cwd**: with no `--config` given, the
  default comes from the cwd's own `.bionic.yml`/`.crux` — there is no upward
  search for a repo root above it. The resolved path is
  `<that config's docs_dir>/manifest.yml`, not a fixed `docs/manifest.yml`,
  since a tree's `docs_dir` can be named anything. Either invoke from the
  repo root or pass an absolute `--config` path.
- **Skipping a language because its extractor is "missing"**: an extractor entry naming no module shipped in the plugin's `extractors/` directory is an error, not a no-op. The dispatcher exits 1. Don't catch and continue.
- **Pointing `extractor:` at your own module**: an extractor name is a lowercase identifier naming a module shipped in the plugin's `extractors/` directory, never a path; any other name refuses the run before any extractor loads.[^shipped-extractor]
- **Forgetting that `--dry-run` exits 1 on drift**: that's a feature for CI hooks, not a bug. Don't wrap it in `|| true`.
- **Treating `extractor_version` as the schema version**: it's the extractor module's version, not the crux schema. They're independent.
- **Editing the dispatcher to add per-language logic**: extractor-specific logic belongs in `scripts/extractors/<lang>.py`. The dispatcher is generic, with one named exception. The `index.md` renderer percent-encodes the link destination of a page whose metadata names `language: python`. Every other page's destination renders as its bare `doc_path`. That rule lives in the dispatcher because the index is the dispatcher's own output.
- **Believing the dispatcher reads `docs/code/`**: it doesn't. It reads `_meta/manifest.json` for two purposes only: as proof that this dispatcher generated the output root, and as the prior state it diffs against. Everything else under `docs/code/` is overwritten or pruned.

[^uv-no-config]: rule:code-doc-callers-invoke-through-uv
[^dry-run-bytes]: rule:code-doc-dry-run-compares-output-bytes
[^drift-is-a-write]: rule:code-doc-drift-means-a-write-would-change-bytes
[^lang-drift]: rule:filtered-extraction-writes-only-its-owner
[^stub-page]: rule:python-stub-renders-its-own-page
[^locals-documented]: rule:python-function-local-declarations-are-documented
[^private-by-scope]: rule:python-private-exclusion-is-by-scope
[^exported-aliases]: rule:python-renders-only-exported-aliases
[^export-gaps]: rule:python-export-gaps-are-visible
[^dup-binding]: rule:python-duplicate-binding-carries-a-gap-note
[^owned-root]: rule:code-doc-output-root-pruned-only-when-owned
[^shipped-extractor]: rule:code-doc-extractor-name-is-a-shipped-module
[^rebinding]: rule:python-rebinding-carries-a-gap-note
[^no-execution]: rule:python-extraction-runs-no-target-code
[^write-refusal]: rule:code-doc-write-refusal-reports-on-stderr
[^content-refusal-is-validation]: rule:code-doc-content-refusal-is-a-validation-error
[^capability-mismatch]: rule:code-doc-capability-mismatch-exits-2
[^filtered-writes]: rule:filtered-extraction-writes-only-its-owner
[^ownerless-refusal]: rule:filtered-write-refuses-an-ownerless-or-deconfigured-tree
[^index-preserved-pages]: rule:code-doc-index-lists-written-and-preserved-pages
[^pinned-metadata]: rule:griffe-is-declared-in-the-dispatcher-metadata
[^page-text-is-data]: rule:python-target-strings-cannot-forge-structure
