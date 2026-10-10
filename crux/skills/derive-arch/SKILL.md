---
name: derive-arch
description: "Regenerate the current architecture map from project sources, or check its drift. Replaces generated architecture files."
context: fork
model: claude-sonnet-5-5
metadata:
  tags: "arch, architecture, regeneration, derived-spine"
  bundles: "crux-docs"
  risk_level: "low"
  triggers: "build the arch | build the arch directory | derive arch | regenerate the architecture | refresh the architecture map | rebuild the arch spine | update the arch"
  routing_note: "Regenerative — rewrites `<docs_dir>/arch/` wholesale from the project's sources. `--dry-run` is the drift check. The primary current-state surface."
---

# Derive Arch

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

The `arch` concern is the project's **derived architecture** — the primary surface for answering *"what is the current architecture?"* It is a deterministic **spine** of four files (`data-model.md`, `api-surface.md`, `module-graph.md`, `decision-index.md`) plus a synthesized `overview.md`, an `index.md`, and `_meta/manifest.json`, all regenerated wholesale from the project's own sources. It is NOT hand-written documentation — it is derived and kept current, the way `code/` is.

This skill is a thin wrapper around `scripts/derive-arch.py`. The script does the work: it reads the project's real sources (JSON Schemas, ADR frontmatter, the skill catalog, `crux`-package imports — pluggable per stack), renders the spine, synthesizes `overview.md`, and stamps a SHA-256 **spine hash** so drift is detectable.

**Where arch sits in the discovery chain:**
- **arch** — the current-state snapshot: entities, interface surface, module structure, and the live decision index. Start here to learn how the project is shaped *now*.
- **ADRs** — the secondary path: *why* each load-bearing decision was made (history, not current state).
- **librarian / historian** — fill the gaps arch and ADRs don't cover (recorded facts, narrative, journal).

**Regenerative invariant.** `arch/` is throw-away output. Every derive rewrites the spine wholesale; hand-edits under `arch/` are OVERWRITTEN. NEVER hand-edit anything under `<docs_dir>/arch/`. NEVER read `arch/*` as an input to a regeneration — the canonical state lives in the project's real sources.

## When to use

- User says: "build the arch", "build the arch directory", "derive arch", "summarize the current architecture", "what's the current architecture", "regenerate the architecture", "refresh the architecture map", "rebuild the arch spine", "update the arch".
- Proactively after a change to any arch **input**: JSON Schemas, ADR frontmatter (a new/accepted/superseded ADR), the skill catalog (a new or renamed skill), or `crux`-package imports (module structure).
- A pre-commit / CI hook, or `audit-docs`, reports arch drift (both call the script with `--dry-run`).

Do **not** use this skill for:
- Reading the architecture — that's just opening `<docs_dir>/arch/overview.md` (or ask the `librarian` / `query-docs`). This skill *builds* the surface; it does not answer questions from it.
- Recording *why* a decision was made — that's an ADR (`propose-adr`).
- Hand-writing architecture prose — arch is derived. Durable hand-written architecture notes belong in `research/` or a `brief`, never under `arch/`.

## Prerequisites

Before invoking the script, confirm:

1. The tree exists and `<docs_dir>/manifest.yml` is present.
2. **`arch` is enabled** — `arch` appears in `manifest.yml`'s `concerns_enabled`. arch is **additive**; it is **default-on for new repos** (`init-docs` enrolls it) and **opt-in for existing trees that predate the default**. If `arch` is absent from `concerns_enabled`, enabling it is a one-line addition (no `schema_version` bump, no migration). If the user asked to build arch on a tree where it is not enabled, add `arch` to `concerns_enabled` first (a `schema` log op), then derive.
3. The repo root is resolved (per §0 below).
4. Git history is complete. A tree that has published a clause migration needs full Git history. In a shallow clone (the default `actions/checkout` depth of 1 in CI, for example) the governing readers (summaries, doctrine, rules catalog, arch and reviews index) refuse with `history-unavailable`. Set `fetch-depth: 0` in CI, or run `git fetch --unshallow` locally.

If a prerequisite fails: tell the user what to fix and STOP. Do not silently write nothing.

## The pipeline

Four steps: resolve config, run the script, paste the table, log the op. Nothing here grades or interprets the output — `derive` records what static extraction found and stops.

### 1. Resolve configuration and enablement

Run `python3 "${CRUX_PLUGIN_ROOT}/scripts/crux-config.py"` from the repo root (or pass `--repo-root <repo-root>`), and confirm the returned `repo_root` is the repo you are operating in. On exit 1, **STOP** and surface the `{"error": ...}` payload — never fall back to defaults. Use the returned `docs_dir` wherever this skill says `<docs_dir>` (default `bionic`).

Then read `<docs_dir>/manifest.yml` and confirm `arch` is in `concerns_enabled` (see Prerequisites). If it is not and the user wants arch, add it, append a `schema` log op recording the enablement, then continue.

### 2. Run the regenerator

```bash
uv run "${CRUX_PLUGIN_ROOT}/scripts/derive-arch.py" --docs-dir <docs_dir>
```

Flags:
- `--docs-dir PATH` — tree dir (default: from `.bionic.yml`, else `bionic`).
- `--repo-root PATH` — repository root (default: cwd).
- `--dry-run` — derive but don't write; emit the drift set and exit 1 if the on-disk spine has drifted. This is the **drift check** (what `audit-docs` CHK-ARCH-1 and CI hooks call).
- `--strict` — require **every** concern for this one run, independent of `arch.require`. Each concern whose recorded verdict is not `populated` becomes an entry in `strict_failures` under `required_by: "--strict"`.
- `arch.require` — a list under an `arch:` key in `<docs_dir>/manifest.yml`. It is **always** checked and needs no flag. Each concern it names whose recorded verdict is not `populated` becomes an entry in `strict_failures` under `required_by: "arch.require"`. A concern that both routes catch is reported once, attributed to `arch.require`, because that is the remedy the reader has to edit.

Both routes fill the same `strict_failures` list and both exit 1, and each entry names which one caught it under `required_by`. This channel is independent of drift, and it applies to a real derive as well as to `--dry-run` — **a non-dry-run derive exits 1 when `strict_failures` is non-empty, having written the spine successfully.**

**`derive` reads no runtime flag and runs no application code.** `--dry-run` stays `clean:true` with the spine hash byte-unchanged even with `CRUX_ARCH_ALLOW_RUNTIME=1` set — that flag belongs to `escalate-arch-runtime` alone (see Red flags).

Exit-code contract (shared with the other crux regenerators):

| Exit code | Case | Meaning | Remedy |
|---|---|---|---|
| `0` | — | Wrote successfully, or `--dry-run` found no drift. | None. |
| `1` | `drift` non-empty | `--dry-run` detected drift. Valid JSON on stdout: `{"drift": [...], "clean": false, ...}`. | Re-run without `--dry-run` (or, inside `audit-docs`, CHK-ARCH-1 auto-regenerates in place). |
| `1` | `strict_failures` non-empty | A required concern has a recorded verdict that is not `populated`. Two routes reach this row and each entry names its own under `required_by`: `arch.require` (the tree's own list, always checked) or `--strict` (every concern, for this run only). Each entry also names the concern, its `stub_reason`, `expected` and `found`. **Not drift, and not a failed write** — a non-dry-run derive reaches this after writing the spine successfully. | **Do NOT re-derive, and never prescribe one:** the committed tree already is what a regeneration produces, so re-deriving writes identical bytes and clears nothing. Establish the precondition the verdict names in the repository, or narrow what required the concern — amend `arch.require` for a `required_by: "arch.require"` entry, drop the flag for a `required_by: "--strict"` one. All are user decisions — recommend, never apply. |
| `2` | Crash or broken environment | A missing dependency or an unhandled exception. This includes an unresolvable **declared parser**. Sixteen of the twenty-four shipped pack-concern pairs name a parser. Eight name `tree-sitter` plus a grammar (an Elixir target needs `tree-sitter-elixir`, a Swift target `tree-sitter-swift`; node and ruby also declare grammars), and one of the eight — the Swift pack's `module-graph`, reading an XcodeGen `project.yml` manifest — names `yaml` as well. Two name `yaml` alone on the crux pack. Six are the universal `decision-index`, one per pack, so **every** pack declares `yaml` — its frontmatter reader answers differently without PyYAML and would move the spine hash. A machine that cannot resolve one gets exit 2 with nothing written and no verdict, stderr naming `ParserUnavailable`. This is a named sub-case of the same environment lane, not a third lane. | No in-repo remedy; fix the environment. The spine on disk is untouched. |
| `2` | Deliberate refusal | A spine file that projects a gated artifact declines to run when that artifact is not what its own regenerator produces. Nothing was written; the spine on disk is untouched. stderr names the input (`StaleProjectionInput`), and the message names one of two causes: (a) the input's bytes differ from its regenerator's output; (b) the regenerator reported validation errors over byte-identical input. | (a) Run that input's own regenerator — `validate-catalog.py` for `crux/catalog/skills.json`, `generate-adr-index.py` for the ADR index. (b) Fix what the regenerator reported; re-running it reproduces the same bytes and clears nothing. **Never recommend a re-derive as the fix for either** — re-deriving reads the same input and refuses again. Re-derive only after the input is regenerated or repaired, and committed. |

**Exit 1 means findings, and the payload names which of the two channels found them.** `drift` and `strict_failures` are independent, a single run may carry both, and they take different remedies. `clean` answers the drift question alone, so `{"clean": true, "drift": []}` at exit 1 is a legitimate shape rather than a contradiction — read `strict_failures`. Exit 1 reporting findings in neither channel is no verdict at all: surface stderr.

Non-zero with empty or unparseable stdout is the same crash lane as the first exit-2 row above. Surface stderr; never report exit 2 as document drift — two lanes share the code, and stderr says which.

### 3. Paste the table

A non-dry-run derive prints `{"written": [...], "coverage": [...], "strict_failures": [...]}` on **stdout** — the files re-emitted, the full reported coverage array, and the strict-gate findings. It prints the per-concern coverage table on **stderr**. Capture both: a reader who redirects stdout alone loses the very table this step tells you to paste. Paste that table into the report verbatim; it is the recorded verdict, not a judgment for the reader to form. Report which spine files changed, if any, and note that `overview.md` carries the new spine hash. Because output is byte-stable, a re-derive with no source change writes the same bytes; the git diff (or the `_meta/manifest.json` per-source hashes) shows what actually moved.

**The table is built from the REPORTED channel, not from the file.** It is not a rendering of `<docs_dir>/arch/_meta/coverage.json`, and looking there for a line you read in the table will not find it. Two channels carry coverage, and they differ by design:

- The **recorded** channel is `<docs_dir>/arch/_meta/coverage.json` — the byte-compared file, and the thing the drift gate hashes. It carries **no remediation line and no annotations**. Only verdict facts reach it, because a byte-compared artifact must be a function of the committed sources alone.
- The **reported** channel is what the script prints — the stderr table and the stdout `coverage` array. It is built *from* the recorded records, so it can never disagree with them about a verdict, and it *adds* the advisory material that never lands on disk.

Each concern in `<docs_dir>/arch/_meta/coverage.json` records what static extraction found: the `verdict` — exactly `populated` or `stubbed` — plus the `extractor` that ran, the `inputs_found` it read, and the `n_sources` / `n_entities` counts. A `stubbed` concern additionally carries `stub_reason`, `expected`, and `found`. These are deterministic, byte-stable functions of the committed sources alone. The `status` and `reason` fields are gone; a `stubbed` verdict's reason is `stub_reason`, and it is one of six named values:

| `stub_reason` | What extraction found |
|---|---|
| `precondition_missing` | The input the concern needs is not in the repository. `expected` names it. |
| `unsupported_stack` | No pack claims this repository's stack. |
| `ambiguous_stack` | Two or more packs claim it, and the verdict names each candidate and the marker that raised it. |
| `ambiguous_package` | The pack ran, but the repository declares more than one candidate package to root the concern at. |
| `parse_failed` | The input was found and read, and the parse did not yield entities. |
| `no_entities` | The input was found and parsed cleanly, and it contains none of what the concern renders. |

Report the reason as written. `unsupported_stack` is one of six, not the meaning of a stub.

The reported channel also adds **`annotations`** — a list on a record, carrying at most the single value `possibly_stale`, and reported-only for the same reason `remediation` is. It fires when a concern consumed a **committed artifact** (a file the project's own toolchain emitted, declared as that concern's `InputClass.artifact`) and the repository's commit graph shows a declared source committed after it. It is advisory in both directions: a source edit that would not change the emitted output still fires it, and four histories silence it rather than guess — a shallow clone, a squashed or rebased history, an artifact and its sources committed together, and a working tree dirty in the concern's own inputs.

`possibly_stale` never changes a verdict, never lands under `arch/`, and never fails the strict gate. Report it as an advisory to refresh the artifact, alongside the concern's `remediation` line, which names the refresh command when the pack declares one. Never report it as drift, and never report a `populated` concern carrying it as anything other than `populated`.

The reported channel adds **`remediation`** — one line per non-populated concern, printed on its own indented row under the concern in the stderr table and present on each record in the stdout `coverage` array, and never written under `arch/`. A populated concern has none, because there is nothing to remedy. The line names a command only when the concern's pack declares one; otherwise it opens `no command establishes this` and says what the derive needs instead. Report it as written — it is not an offer to escalate, and no line names the runtime seam.

**The confidence layer is retired.** The four-level grade scale (`high | medium | low | none`), its `confidence` / `confidence_reason` / `escalation_offered` fields, the expectation-marker table behind them, and the `arch_confidence_threshold` config key that thresholded them are all gone. They graded a concern's coverage on evidence that a surface existed which extraction could not see — a judgement the recorded channel now declines to make. Report the `verdict` as written, with `stub_reason` / `expected` / `found` where the concern is stubbed; do not infer a grade, and do not describe a stub as low-confidence rather than stubbed.

A tree whose `.bionic.yml` still sets `arch_confidence_threshold` loads clean and produces no such attribute. The key falls through the forward-compat valve — unknown keys are ignored by design — because refusing a key an earlier version told users to write would turn an upgrade into an outage over a value that no longer feeds anything.

**`derive` never offers escalation, and never points a reader at the runtime as the remedy.** Do not name `escalate-arch-runtime`, `CRUX_ARCH_ALLOW_RUNTIME`, or the runtime package in any report of a verdict. A stubbed verdict is a recorded fact, not an invitation. `derive` records the verdict and stops: it writes nothing into `arch/`, acts on nothing, and runs no application code.

### 4. Append to `<docs_dir>/log.md`

```
## [YYYY-MM-DD] arch | regenerated <docs_dir>/arch/ (N files; spine <first-12-of-hash>)
```

Body, 1–3 lines: which spine files changed, the new spine hash, and the trigger (manual, source change, or audit drift). arch carries no timestamps in its own tree — the log entry + git are the provenance record.

## Verification

- [ ] `<docs_dir>/arch/` contains the four spine files, `overview.md`, `index.md`, and `_meta/manifest.json`.
- [ ] `overview.md` carries the `<!-- arch-spine-hash: sha256:... -->` stamp and `index.md` surfaces the same hash.
- [ ] `derive-arch.py --dry-run --docs-dir <docs_dir>` now exits `0` (the spine and its hash agree).
- [ ] `<docs_dir>/log.md` has a new top-of-file `arch |` entry.

## Keeping arch current

arch is only useful if it tracks reality. Rebuild it when an **input** changes:
- a JSON Schema is edited, a new/accepted/superseded ADR lands, a skill is added or renamed, or the module structure (package imports) changes.

Two mechanisms keep it honest:
1. **The drift gate** — `derive-arch.py --dry-run` fails when the spine moved without a re-derive. Wire it into a pre-commit / CI hook alongside the other crux regenerators.
2. **`audit-docs` CHK-ARCH-1** — a full audit detects arch drift and **auto-regenerates the spine in place** (a DRIFT-tier auto-fix), so a routine `audit-docs` run keeps arch current without a separate step. `CHK-ARCH-2` flags an enablement mismatch.

The lightweight habit: after any change that touches a tracked source and before a release, run this skill (or `audit-docs`) so the architecture surface never lies.

## Red flags — STOP and reconsider

- About to hand-edit a file under `<docs_dir>/arch/` to "fix" the architecture description. NEVER — the next derive overwrites it. Fix the *source* (the schema, the ADR, the skill), then re-derive.
- About to read `<docs_dir>/arch/*` as input to another regeneration. NEVER — arch is output, not source.
- About to report an exit 1 as "your architecture is broken", or to prescribe a re-derive for every exit 1. Read the payload first — exit 1 carries two independent channels with opposite remedies. `drift` non-empty means the spine drifted from its sources, and a re-derive is the fix. `strict_failures` non-empty means a required concern's recorded verdict is not `populated`; **never prescribe a re-derive there** — it writes identical bytes and clears nothing. `{"clean": true, "drift": []}` at exit 1 is the strict channel, not a contradiction. An exit `2` reaches no verdict at all and never reads as document drift: surface stderr, which says whether it is the environment lane or the `StaleProjectionInput` deliberate-refusal lane.
- About to build arch on a tree where `arch` is not in `concerns_enabled` without enabling it first. Enable it (additive, one line) and log the `schema` op, or STOP and tell the user.
- About to skip the `arch` log entry because "nothing changed". Even a no-op derive is auditable — log it.
- About to treat `CRUX_ARCH_ALLOW_RUNTIME=1` as something `derive` reads. NEVER — `derive` reads no runtime flag and runs no application code; `--dry-run` stays `clean:true` with the spine hash byte-unchanged even with the flag set. That flag gates `escalate-arch-runtime` alone.

## Rationalization table

| Excuse | Reality |
|---|---|
| "I'll just edit `arch/overview.md` to add the missing detail." | The next derive deletes it. The detail is missing because a *source* lacks it — fix the schema / ADR / skill. |
| "arch drift on `--dry-run` is informational; exit 0 is fine to assume." | Exit 1 is the contract CI relies on. Don't wrap it in `|| true`. |
| "The user asked what the architecture is — I'll read `arch/` and also tweak it." | Reading is fine; tweaking is not. This skill builds; the librarian reads. |
| "arch isn't enabled here, so there's nothing to do." | arch is **default-on for new repos and opt-in for existing trees**. If it isn't enabled here (an older tree that predates the default), enable it (one line) and derive. |
| "I'll hash the spine myself to check drift." | Only `derive-arch.py` owns the spine hash. Call `--dry-run`. |

## Common mistakes

- **Hand-editing the spine.** Everything under `arch/` is regenerated; the only durable inputs are the project's real sources.
- **Forgetting arch has no timestamps.** Provenance is the spine hash + git + the `log.md` entry, not an in-tree date. `_meta/manifest.json` is byte-stable (tool pins + per-source hashes, no timestamps).
- **Building arch but not keeping it current.** A stale arch surface is worse than none — wire the drift gate and let `audit-docs` auto-regenerate.
- **Confusing arch with ADRs.** arch is the current-state snapshot; ADRs are the decision history. Both matter; they answer different questions.
- **Treating exit 2 as drift (exit 1).** Exit 1 is findings JSON. Exit 2 means the derive reached no verdict, and never reads as document drift.
- **Reading exit 2 as always an environment error.** It carries two lanes now. A `StaleProjectionInput` on stderr is a deliberate refusal with an in-repo remedy — regenerate the named input. Reporting that as a broken environment sends the reader to fix the wrong thing.

## See also

- `audit-docs` — CHK-ARCH-1 detects arch drift and auto-regenerates the spine; CHK-ARCH-2 checks enablement.
- `extract-code-docs` / `verify-code-docs` — the sibling regenerative concern (`code/`) and its dry-run drift check.
- `link-adr-graph` — regenerates the ADR lineage graph (the decision-history surface arch's `decision-index` complements).
- `query-docs` / the `librarian` agent — read the architecture from `arch/overview.md`; this skill builds it, they answer from it.
