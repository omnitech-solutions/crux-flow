<!-- generated-from: CHANGELOG.md@sha256:7b8d4ff406627e0d257a3a6835484729cb67916888929cba9a08926ac475fe98; model: claude-sonnet-5.5; date: 2026-09-30 -->
# Changelog

All notable changes to crux. The format roughly follows [Keep a Changelog](https://keepachangelog.com/) and the project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

### Changed

### Fixed

### Removed

## [3.25.1] — 2026-10-01

### Changed

- The instruction-file contract in the `AGENTS.md` template now describes the set-aside name check the way `audit-docs --migrate` performs it. A name counts as taken when an existing entry holds it or holds a name that lowercases to it. If the volume folds a name in some other way, the migration scope stops at its no-replace rename.
- Two rules in the shipped rules catalog have updated text. The rule for a refused migrate scope now names the refusal of a scope directory reached through a symlink, which migrate already enforced. The rule for the decision review's `gate_count` signal now says the roster is matched by its four leading header cells.

### Fixed

- The decision review's `gate_count` signal finds the regenerator roster again. It now matches the roster's header by its four leading columns, so the Scope column the roster gained in 3.16.0 no longer hides it. Before this fix the signal reported unmeasurable on every run. A roster split by a blank line, or a table directly attached beneath it, now reads as unmeasurable instead of producing a wrong count.
- The decision review's `paper_only` signal now reads the columns of the doctrine table by name. Since the table gained its `source_status` column, the signal had read none of the rule rows while still reporting a computed result. It now accounts for every line that carries a rule handle or a basis value.
  - A row it cannot read has its ADR set to null and is reported as an error.
  - A missing basis column, or a fenced table, makes the signal unmeasurable.
  - An unknown basis value is identified by its cell position and is never quoted.

### Removed

## [3.25.0] — 2026-09-30

### Added

- A new check, `check-record-numbers`, fails when two promptbooks or two architecture decision records share a number. `validate-promptbook`, `audit-docs` (rule CHK-NUM-1) and `release-preflight` run the same check, so a duplicate no longer reaches a release.

### Changed

- `init-docs` now creates a repo-root `AGENTS.md` when the root has no `AGENTS.md` or `CLAUDE.md` in any letter case, and step 9 prints a note saying so. A `CLAUDE.local.md` does not prevent the create. A fresh repository then reports `supported: true` from `check-claude-compat`.
- `init-docs` reports each legacy root entry (a `CLAUDE.md`, a case variant, or a linked or read-only `AGENTS.md`) with a remedy that loses no content when followed. Rollback deletes a created root `AGENTS.md` only while it is still the file the run created.
- The council retries a seat once, with a fresh deadline, after a timeout, provider error, truncated response or malformed response. `max_retries` defaults to 1, and any value other than 0 or 1 is refused. Each seat now records its finish reason.
- Council results now include `conditioned` and `conditions` alongside `nits` and `nit_items`. A conditioned approval never runs automatically, and nits never change the route. A reply of the wrong shape is now reported as `malformed-response` instead of `client-config`.
- The Swift pack's parser rule now covers third-party parsers only: the probe declares each one and the deriver pins it. A standard-library parser follows the supported Python versions.

### Fixed

- `check-claude-compat` lists each instruction file once, under its real name, on a case-insensitive filesystem. Under `claude-md-and-agents-md`, only a file spelled exactly `CLAUDE.md` counts as effective.
- The cycle and iterate promptbook templates no longer splice a review paragraph into the middle of a sentence.
- `audit-docs --migrate` no longer loses instruction files. Where a scope holds both `CLAUDE.md` and `AGENTS.md`, `CLAUDE.md` wins and the other file is kept byte for byte under a reported name. Where a move is unsafe, migrate changes nothing in that scope and names the next step.

### Removed

- **Breaking:** the merge preview and the `--resolution` file of `audit-docs --migrate` are gone. Two instruction files in one scope no longer merge block by block.

## [3.24.9] — 2026-09-30

### Changed

- Four agents now have larger turn budgets so they finish and report instead of running out of turns. The architect and historian move from 50 to 75 turns, and the dev-lead and developer move from 100 to 150. OpenCode `steps` values match. Codex agents are unchanged because Codex has no per-agent turn cap.

### Fixed

- The Claude compatibility check (`check-claude-compat.py`) now reports `supported: false` and exits 1 when the host loads no instruction file, such as in a fresh repository without a root `AGENTS.md`. A `.claude/CLAUDE.md` now counts as an instruction file for its project directory. `build-claude-adapter --generate` now refuses to run when no root `AGENTS.md` exists.
- The council now reports `UNANIMOUS_<decision>` when every seat returns the same decision. It previously reported `SPLIT` in that case.
- The council now lists responses outside the expected decision scale in an `off_scale` field. A response that only resembles an approval or execute decision now reads as `UNANIMOUS_OFF_SCALE`.
- Each council seat now has its own deadline, so one slow seat no longer discards the other seats' results. The default timeout rose from 180 s to 600 s.

## [3.24.8] — 2026-09-29

### Added

- New `gpt-6.1-sol` and `gpt-6.1-sol-low` model-registry entries for `openai/gpt-6.1-sol`. The model has a 1.05M-token context window and 128K-token output, and costs $2 input, $0.10 cached input and $10 output per 1M tokens (verified 2026-09-29).
- `gpt-6.1-sol` pins the OpenAI serving host with no reasoning effort set. `gpt-6.1-sol-low` pins low reasoning effort.

### Changed

- Every GPT-6 Sol assignment now uses GPT-6.1 Sol:
  - **Codex:** the flagship and standard levels use `gpt-6.1-sol` at high effort, and the reviewer uses it at xhigh effort. Both effort levels were verified against the Codex 0.159.0 model catalog.
  - **OpenCode:** the `sol-latest` alias now resolves to `openrouter/openai/gpt-6.1-sol`, so the architect, brainstormer, dev-lead and reviewer agents run on it.
  - **Council:** the default council's OpenAI seat is now `gpt-6.1-sol`.
- The router config version is now 4.0.0 because registry keys were removed.

### Removed

- **Breaking:** The `gpt-6-sol` and `gpt-6-sol-low` registry entries have been removed, with no alias to a successor. Referencing either one now raises `ValueError: Unknown model`. Use `gpt-6.1-sol` or `gpt-6.1-sol-low` instead. GPT-6.1 Sol does not support `none` or `minimal` reasoning effort, so `low` is the lowest available pin.

## [3.24.7] — 2026-09-29

### Changed

- Under OpenCode, the architect and brainstormer agents now run on GPT-6 Sol (`sol-latest`, `openrouter/openai/gpt-6-sol`) instead of Claude Opus 5.5, and the flagship level's OpenCode model is now `sol-latest`. Model assignments for Claude Code and Codex are unchanged.
- The apex level now has an OpenCode default of `opus-latest`. The night-gardener and dev-lead agents no longer carry model overrides that duplicated their level's settings, so the models they run on are unchanged.

## [3.24.6] — 2026-09-29

### Added

- A new `claude-sonnet-5.5` model-registry entry for `anthropic/claude-sonnet-5.5` provides 1M context, 128K output, and pricing of $2 input and $10 output per 1M tokens. It has no capability scores or effort pin, the router sends it no temperature, and no router role uses it yet.

### Changed

- The developer, historian, librarian and wayfinder agents now request `claude-sonnet-5-5`, as do the derive-arch, extract-code-docs, read-news and tend-garden skills. On Claude Code v2.1.284 or later, this selects Claude Sonnet 5.5 by its full model ID.
- It has not been verified whether Claude Platform on AWS, Amazon Bedrock, Google Cloud's Agent Platform and Microsoft Foundry resolve the `claude-sonnet-5-5` ID. On those providers the `sonnet` alias resolves to an older Sonnet: 4.6 on Claude Platform on AWS and 4.5 on the other three.
- The standard level's Claude model now uses the full model ID. `claude-sonnet-5-5` is accepted as an agent `model:` value alongside `sonnet`, and Codex and OpenCode model selections are unchanged.
- The router's `anthropic_balanced` role now resolves to `claude-opus-5.5`, which is Opus 5.5 at high effort, instead of Claude Opus 5. `call_claude_sonnet` still uses `anthropic_balanced`, so it now calls Opus 5.5 at high effort.
- The synchronous council's default arbiter now uses the `council_arbiter` role instead of `anthropic_top`. Both roles resolve to `claude-opus-5.5-xhigh`, so the arbiter's model does not change.
- **Breaking:** The router configuration's `version` is now 3.0.0 because this release removes router role names and registry keys.
- The `sonnet-latest` catalog alias now points at `openrouter/anthropic/claude-sonnet-5.5`. No agent resolves through it.

### Fixed

- `adr-signals.py` now exits 0 on a freshly initialized documentation tree, so the `init-docs` checklist item that runs it passes. This applies when the doctrine directory is absent or holds neither the doctrine index nor its `_meta.json`. In that case `paper_only` reports `unmeasurable` with a reason, and `carve_out_count` still counts the manifest's exemptions.
- `adr-signals.py` still exits 1 for other broken doctrine layouts. These include:
  - an index deleted while its `_meta.json` remains
  - an index that resolves outside the repository
  - a doctrine path that is a regular file, or a symlink with no in-repository index behind it

  The decision review still stops on a fresh tree until `compile-doctrine` has run.
- The documentation tree's `AGENTS.md` template now shows the lines that `init-docs` appends using `<docs_dir>` instead of a literal `docs/`. It also explains that `docs/` and `<docs_dir>` both refer to the configured tree directory.
- `init-docs` now documents the exact warning it shows when the repository has no root `AGENTS.md`. The warning lists the lines to add and says which Claude Code versions and distributions read `AGENTS.md`. It also names the files that stop Claude Code from reading `AGENTS.md` and says which of them `audit-docs --migrate` converts. The verification checklist now asks for this warning verbatim.

### Removed

- **Breaking:** Twelve router roles have been removed, and `get_default_model` now raises `ValueError` for each of them. The removed roles are `anthropic_fast`, `openai_chat`, `council_code`, `code_review`, `vision`, `think_shallow`, `think_medium`, `think_deep`, `plan`, `reflect`, `recursive_improve` and `anthropic_council`. Nothing in crux used them. The weighted council vote drops its `anthropic_council` weight, and no council member's weight changes.
- **Breaking:** The `claude-opus-5` and `claude-sonnet-5` registry entries have been removed and are not aliased to a successor. Naming either one now raises `ValueError: Unknown model`, so use `claude-opus-5.5` or `claude-sonnet-5.5` instead.

## [3.24.5] — 2026-09-28

### Fixed

- The user guide and the shipped `AGENTS.md` template for the documentation tree no longer claim that a repo-root `AGENTS.md` points at the documentation tree. `init-docs` never creates that file and warns when it is missing. When the file exists, `init-docs` adds the documentation-tree reference and the objectives instruction only where they are missing, and it leaves existing content unchanged.
- Advancing or abandoning a run with `advance-run.py` now changes only the affected values in the run snapshot. Comments, quoting, indentation, timestamp formatting and line endings are left intact, so the diff shows only the advance.
- If a snapshot cannot be updated in place, for example because a comment sits inside a value that would change, the operation is refused and the file is not written. Snapshots already rewritten by earlier versions are left as they are.

## [3.24.4] — 2026-09-28

### Changed

- `install-docs-skills` can now be invoked automatically by the model when you ask which Crux version is active. It reports a version only when the selected skill or a host load trace confirms it. Otherwise it reports the active version as unverified. A cached or disabled copy does not count as evidence. Detailed installation, upgrade, and schema guidance loads only when you ask for it.

### Fixed

- Generated ADR lineage no longer links to a decision record that does not exist in your project's documentation tree.
- The journal adoption-date check in the `init-docs` checklist no longer fails with an argument-parsing error.

## [3.24.3] — 2026-09-28

### Fixed

- Generated run-progress Markdown no longer includes a link to a document that is not available to plugin users. When a run starts with every prompt pending, the promptbook index now shows `0/N (0%)`.
- New journal entries now stay below a month file's introductory note. This applies to month files created by `init-docs` and to files with a custom preamble.
- Install guidance now identifies the active plugin from the selected loaded skill or the host load trace. A marketplace cache entry is labeled as a candidate and is no longer treated as proof of which copy the session loaded.
- In a consuming project, `init-docs` now reports the catalog authoring gate as not applicable when that gate returns `surface_absent: true`. A zero exit code alone no longer reports that the installed catalog was validated.

## [3.24.2] — 2026-09-28

### Added

- A new bounded journal writer validates each request before changing anything. It reports whether a write completed, was refused, or was only partially applied.
- The journal writer accepts a caller-supplied local timestamp with an explicit UTC offset, so replays produce stable results. A month-only partial retry warns you when the offset cannot be verified from what is already on disk.

### Changed

- **Breaking:** Live promptbook execution now accepts only YAML promptbooks and runs. Markdown promptbook records remain readable and indexable as history, but they can no longer be executed.
- To finish a Markdown run, use the pinned public `v3.23.2` release. It can complete the run if its remaining prompts can truthfully finish, then archive and convert it. It cannot deliberately abandon a Markdown run, so an unfinishable one stays readable and unresolved.
- The pinned `v3.23.2` release also provides a migration route for supported documentation-tree schemas 2–4. Run it on a copy of your documentation tree and validate the result. These documentation-tree schema versions are independent of promptbook format. If you have an invalid schema marker or an ambiguous tree, investigate it before migrating.
- In the current release, `audit-docs --migrate` remains available for instruction-file migration on documentation-tree schema 5.
- `log-work` and `run-promptbook` now send each operation to a dedicated local procedure.
  - A journal request writes the month entry, the derived index, and the operation log.
  - A log-only request writes only the operation log.
  - Promptbook status is displayed with the existing progress renderer.
  - Advancing a promptbook still changes only the run snapshot and the active-book pointer.

### Removed

- **Breaking:** Removed several migration tools:
  - the `migrate-promptbooks` skill and its converter
  - the migrator for older documentation-tree schemas
  - the one-time skill-frontmatter migrator

  Historical record readers and instruction-file migration remain available. The installed catalog now has 52 skills.
- **Breaking:** Removed seven skill entry points: `task-planner`, `author-runbook`, `visualize-run-progress`, `trace-runtime-ops`, `semantic-bridge`, `agent-identity`, and `serve-llm`.
  - Their underlying runtime APIs, progress renderer, runbook generator, HTTP service, and stored artifacts remain available.
  - The user guide maps each former entry point to its current replacement.

## [3.24.1] — 2026-09-27

### Changed

- This release does not change how the plugin behaves. In the development repository, the release checks now run the plugin's test suite in parallel, which cuts release validation time roughly threefold. The full suite still passes with the same results on Python 3.13 and 3.14.

## [3.24.0] — 2026-09-26

### Added

- **Swift stack pack for the architecture map.** It covers Apple apps and the Swift packages they use as dependencies and local modules, rendering targets, their dependencies, model types, interfaces and `@main` entry declarations. It reads Swift sources, `Package.swift` manifests and Xcode projects without running any Swift toolchain.
- **Xcode project reading in the Swift pack.** Each target renders with its product type, its target and package-product dependencies, and each `.swift` file's membership as the project file declares it. A `@main` row names every target that compiles its file. The reader executes nothing. An XcodeGen or Tuist manifest with no committed project renders as missing input, and a conditional build setting renders as a `conditional-setting` residual.
- **`extract code docs` now handles Python.** A new `python` extractor key reads sources statically through Griffe, pinned to `griffelib==2.3.0` and run under `uv run --no-config`. It never imports or runs target code, installs the target's dependencies, or executes documentation examples. Module, class, function and method docstrings render with their signatures.
- **`include_private` for the Python extractor.** It defaults to on and is the only extra key the extractor honors, alongside `extractor` and `glob`. Any other key at that level refuses the run. When `include_private` is on, a `def` or `class` nested inside a function is documented under a qualified name containing a `<locals>` segment.
- **Two new refusals in the `extract-code-docs` dispatcher, for every language key.** It refuses an output root that contains Markdown but no `_meta/manifest.json` with a `pages` list proving the dispatcher wrote it. It also refuses an extractor name that isn't a module shipped with the plugin. Both refusals happen before any write and name the offending output root or extractor.
- **Bounded Xcode project reading in the Swift pack.** The pack now caps the Xcode project reading it does for one architecture file. Past the bound, later project references are skipped, and a single `scan-cap` residual states the bound.
- **UTF-16 surrogate pairs in project files.** `\U` escapes that spell a surrogate pair now render as the single character they encode. A lone or reversed surrogate renders the project as `project-unreadable` (`malformed`). A surrogate in a `Package.swift` `\u{…}` escape renders `non-literal-manifest`. Neither stops the derive.
- **Symlink escapes.** A classic member or exception path that passes through a symlink resolving outside the checkout now renders `path-escape`, the same residual as a lexical escape. A `/Localized/` entry whose `.lproj` directory is such a symlink renders `unresolved-reference` instead.

### Changed

- **Breaking: architecture derives now require Python 3.13 or later (previously 3.11).** `uv run` selects an installed 3.13+ interpreter, or downloads one if none is installed and uv's Python downloads are enabled. An interpreter forced via `--python` or `UV_PYTHON` still takes precedence: uv warns and runs the derive on it, even if it is older than 3.13.
- **`tree-sitter-swift` on every architecture derive.** Every derive now installs the `tree-sitter-swift` wheel, regardless of the project's stack. On platforms without a published wheel, the grammar is built from source, which requires a C compiler.
- **Write scoping in `extract-code-docs`.** A `--lang` run now writes and prunes only the pages, metadata rows and provenance block owned by its own language key, leaving everything else untouched. The page index lists only pages the run wrote or preserved.
- **Drift reporting in `extract-code-docs`.** `--dry-run` now reports edited, missing or unexpected pages. It reports index or provenance drift separately from page-content differences.

### Known limitations

- **Swift pack parse time is not bounded.** A file that repeats blocks of type declarations the pinned grammar fails to parse can take parse time that grows quadratically with its size. A parse timeout is planned, which would add a new outcome for files whose parse times out.

## [3.23.2] — 2026-09-25

### Changed

- Councils now use Claude Opus 5.5 at extra-high effort. Both the Claude reviewer and `anthropic_top` now select Opus 5.5.

## [3.23.1] — 2026-09-23

### Fixed

- Restored two notes in the README's OpenCode setup section that were accidentally dropped in 3.23.0. One explains why populating both the plural and the legacy singular agent directory makes it unclear which copy is live. The other advises checking the OpenCode runner's version rather than its executable name.

## [3.23.0] — 2026-09-23

### Added

- crux now supports Python 3.14 as well as Python 3.13. Each release is tested on both supported versions before it ships.

### Changed

- On Python 3.14, the arch Python pack now parses unparenthesized `except A, B:` handlers, which Python 3.14 made legal. A file whose only defect was such a handler is no longer reported as a refused source, and it also leaves `inputs_found`.
- **Note:** a spine derived on Python 3.14 can therefore differ from one derived on Python 3.13 for a repository with such files. Entity counts do not change.

## [3.22.0] — 2026-09-23

### Changed

- When running under OpenCode, several agents now use different models. The default flagship model is now `opus-latest` (`openrouter/anthropic/claude-opus-5.5`), so `architect` and `brainstormer` run on Claude Opus 5.5.
- Under OpenCode, `dev-lead` and `reviewer` now use the new `sol-latest` alias (`openrouter/openai/gpt-6-sol`), and `night-gardener` uses `opus-latest`. The `kimi-latest` alias remains available as an alternate but no agent uses it by default. Model assignments for Claude Code and Codex are unchanged.

## [3.21.0] — 2026-09-23

### Changed
- **The developer and dev-lead agents now count a test as proof only once they have seen it fail.** Test-first is still the default. A test proves a behaviour change once it has failed, for the reason the change addresses, against the code without that change. If a test has not been seen to fail, the agent disables the change, confirms the test fails, then restores the change and confirms it passes. The agents never delete working code for that reason alone, and the patch template's implement prompt states the same rule.
- **Three failed fixes to the same problem now trigger a reassessment.** Previously this produced an automatic verdict that the architecture was wrong. Now the developer or dev-lead agent re-examines its hypothesis, its environment and the architecture, and records the evidence for each. It fixes problems within its own scope and continues. Anything outside its scope is reported, within the run, to whoever assigned the work.
- **Developers now report capability gaps outside their unit to their lead.** They invoke `forge-skill` only when building that capability is their assigned unit. The shared capability-gap guidance is otherwise unchanged.
- **The dev-lead now runs, for each unit, the tests the change can affect instead of the full suite.** It still runs the full suite for a unit when it cannot determine that set, and runs it once more after integration. At the exit gate, a passing full-suite run still counts as long as no file has changed since. `fix-directly` and the release gates still require the full suite.

### Fixed
- **`fix-directly`'s sizing questions no longer contradict each other.** The skill treats a defect as a direct fix only when every answer is yes, but question 5 asked whether the fix *needs* an independent review gate, so a yes sent it elsewhere. Question 5 now asks whether the fix can ship *without* one. A no still routes to `patch-cycle`, and the example about signed or digest-bound surfaces is kept.
- **Text-only calls now reject image models with an error instead of losing their output.** `call_model`, `call_gateway`, council seats and the server's `/chat` endpoint return only reply text. Previously, an image model either returned an empty answer (`gpt-image-2.5-sunburst`) or lost its image (the three Gemini image models). These calls now raise `NotATextModelError`, naming the model and its registry type, for any non-text model before any credential is read. **Behaviour change:** `/chat` now returns this error instead of an empty 200 response. `/models` still lists image models, but crux does not support image generation or image responses.

## [3.20.0] — 2026-09-22

### Added

- New `claude-opus-5.5` model-registry entry, verified against live OpenRouter metadata. The new `release_docs` router role resolves to this model.
- Four new OpenAI model-registry entries, verified against live OpenRouter metadata:
  - `gpt-6-sol`
  - `gpt-6-sol-low`, which is the same model pinned to low reasoning effort
  - `gpt-6-luna`
  - the image model `gpt-image-2.5-sunburst`

### Changed

- The commander and night-gardener agents now run Claude Opus 5.5 on Claude Code.
  - Each of these agents has an override that names the full model ID `claude-opus-5-5` instead of a family alias.
  - The agent-model catalog schema moves to version 4 to support the override.
  - **Running these agents on Opus 5.5 requires Claude Code v2.1.280 or later.**
  - The reviewer agent continues to use Fable.
- The council's Anthropic seats continue to use Fable for now. This covers the async text and visual seat, the sync member, and the sync arbiter. Council weights are unchanged.
- OpenAI assignments move to GPT-6 with unchanged reasoning efforts.
  - These assignments now resolve to `gpt-6-sol`:
    - the router roles `openai_chat`, `think_medium` and `vision`
    - the OpenAI members of the `council_default`, `council_code` and `recursive_improve` roles
    - the flagship and standard Codex levels
  - GPT-6 has no Terra tier, so every former Terra assignment now uses Sol.
  - The apex Codex level and `openai_top` stay on `gpt-6-astra`.
- The reviewer agent's Codex override now uses `gpt-6-sol` at `xhigh` effort.
- Codex agent files you have already installed keep their GPT-5.6 models. Re-run `install-codex-agents` after upgrading to pick up the new models.

### Fixed

- The council skill no longer claims that the agent-model catalog forbids `fable`. `fable` is a valid agent model, and the reviewer agent uses it.

### Removed

- **Breaking:** The model-registry entries `gpt-5.6-sol`, `gpt-5.6-sol-low`, `gpt-5.6-terra`, `gpt-5.6-luna` and `gpt-image-2` have been removed.
  - No old key is aliased to a GPT-6 model.
  - Code that names a removed key now fails with `ValueError: Unknown model`.
- The image model no longer returns text. `gpt-image-2.5-sunburst` returns only an image, whereas `gpt-image-2` returned both an image and text. No part of crux used that text.
- `openai_chat`, `think_medium` and the OpenAI members of `council_default` and `council_code` now pin the OpenAI serving host, as `gpt-6-sol` does. These roles no longer fall back to Amazon Bedrock.

## [3.19.0] — 2026-09-22

### Added

- `AGENTS.md` is now the canonical repository instruction file at every scope crux manages. Codex and OpenCode read it directly, and Claude Code reads it on supported distributions from version 2.1.277 onward, as long as no suppressing file takes precedence. The bundled `check-claude-compat.py` script reports your host version, distribution, built-in state, the effective `instructionFiles` mode, and any suppressors behind its verdict.
- `audit-docs` now discovers and migrates legacy instruction files. A plain audit reports checks CHK-INSTR-1 through CHK-INSTR-5; `audit-docs --migrate` applies a validated migration plan. Only tracked files in the current checkout are modified — vendored dependencies and linked worktrees are left alone — while the audit also flags untracked files that could silence the canonical file. Every reported suppressor comes with a remedy, and only a suppressor on the path from the repository root to your working directory counts as a compatibility failure.
- When migration merges instruction files, a block is deduplicated only if both its bytes and its heading ancestry match. The migration receipt maps every source block's heading path to its result, and the migration refuses to complete if that accounting does not balance.
- For hosts that cannot read `AGENTS.md`, an opt-in compatibility adapter is available via the bundled `build-claude-adapter.py` script, which both generates and audits adapters. The audit reports stale, removable, incomplete, and orphan adapters. Generation refuses to write to a destination that was not itself generated, a denylisted path, a symlink, or any target outside the repository.
- The model registry gains a `jev-1.13` entry for `typesafe/jev-1.13`, a structured decision model served on the gateway's Decisions route rather than chat completions. No catalog alias or model role points to it, so no agent or council seat resolves to it; the adapter that calls it is a development-only experiment and is not included in this release.

### Changed

- In the development repository, the root `AGENTS.md` now holds the instructions previously split across several root instruction files, and `bionic/AGENTS.md` replaces `bionic/CLAUDE.md`. The root file is the source for generated writing rules rather than one of their outputs, so no generator reads and writes the same path. Readers, generators, templates, tests, and release checks were updated together; frozen history is unchanged.
- The per-request data-retention denial (`provider.data_collection`) sent to the gateway is now built by a single shared `provider_object()` function exported from the gateway client and used by every route. Behaviour is unchanged: every request still carries the denial, and the serving-provider pin is still added only when the registry entry specifies one.

## [3.18.1] — 2026-09-18

### Added

### Changed

- **The developer role now runs DeepSeek v4.1 Flash on OpenCode.** The OpenCode developer agent is routed to a new `deepseek-flash` model alias (`openrouter/deepseek/deepseek-v4.1-flash`) instead of `glm-flash`, and the generated OpenCode developer agent definition reflects the change. The `glm-flash` alias remains declared as an available alternate. The Claude and Codex developer seats are unchanged, and no other role is affected.

### Fixed

### Removed

## [3.18.0] — 2026-09-17

### Added

- **New ADRs are warned when a `governs` rule runs long.** Each rule has a recommended maximum of 768 characters, counted as Unicode code points of the parsed YAML value after trimming, so every YAML block style is measured by the string it produces and a rule is never penalised for an em dash. Exceeding it produces a warning only: no failing exit code, nothing blocked, no rule truncated or rewritten, no waiver or approval step. The warning names the ADR, the rule handle, the length and the maximum, and suggests what to do — keep the obligation and its necessary conditions, move rationale and examples into the body, and split only where the parts are independently enforceable. It is a review threshold, not a target, and no limit is placed on an ADR as a whole.
- **The length advisory applies only to ADRs written after it lands.** A version-controlled baseline records the ADR identities that existed when the advisory was introduced; anything absent from it is checked, whatever its number or date. Exemption is by identity rather than a numeric cutoff, because an ADR numbered below a boundary can still be written after one, and existing ADRs stay exempt through later edits since a body freezes once its ADR leaves Proposed. A tree with no recorded baseline is told so rather than left silently unchecked, and a new tree starts with an empty baseline, which switches the advisory on for everything it writes.
- **The ADR authoring guidance now states the principle behind the length check.** Write the shortest ADR that fully states the decision, its scope and its consequences; keep each `governs` rule to one obligation and its necessary conditions; put rationale and examples in the body; and let brevity change nothing about the meaning. The authoring and review steps run the check, so a rule that grows during revision is caught before acceptance freezes it.
- **A rule citation on a shipped surface must now name a rule whose decision was accepted.** The shipped rules catalog is projected from `governs` blocks in active decision records, and a record is active from the moment it is written — so previously a rule could reach the catalog before anyone accepted it. Eligibility is now decided from a declared table keyed on the source record's kind and status, with nothing admitted by default: an unknown lifecycle state, an unknown record kind, or a record with no status is refused rather than passed. `generate-rules-catalog.py` refuses the whole projection when any cited slug is ineligible rather than silently dropping the rule. The refusal writes nothing, leaves an existing catalog byte-unchanged, and names the citing path, the citation, the source record, its status and the reason, one row per citing location. Rules sourced from observation records are also refused pending an owner decision, since an observation describes and decides nothing.
- **The rules catalog check is now enforced automatically in the development repository**, including on status-only edits to decision and observation records, and runs before release staging so it is measured against the full source tree rather than the artifact.

### Fixed

- **The governs/body parity check misread most decision records.** Its frontmatter reader treated a YAML list written flush-left under its key as the end of that key, because such a list begins with a hyphen in the first column, so every record written that way reported "carries no entries". Both indentations are valid and both are now read correctly.
- **A shipped surface the citation scan could not read hid its citations instead of failing.** A file containing an invalid UTF-8 byte, or larger than the size ceiling, was dropped silently and contributed no findings while still shipping. An unreadable shipped surface is now a refusal that fails the gate before eligibility is judged. A symlink pointing outside the plugin root is reported as advisory and does not fail the gate, since it does not ship.
- **A malformed documentation-tree configuration reported "nothing to check" instead of failing.** Every failure to resolve the tree was treated as a legitimately absent tree and exited clean, which for a publication guard meant "cannot verify" was reported as success. A malformed configuration now exits loudly; the absent-tree lane survives for trees that are genuinely not there.
- **New `--require-surface` flag for the catalog regenerator.** The regenerator exits cleanly when no documentation tree is present — correct for a consuming project, which owns none — but that meant a renamed, moved, or misconfigured tree also reported green having examined nothing. Passing `--require-surface` turns a missing tree into a refusal; enforcement points that own a tree pass it, and consuming projects do not. The default behaviour is unchanged.
- **The catalog refusal named a remedy that only fit one of its four cases.** "Accept the decision" applies when the host decision is a proposal, but a withdrawn or replaced decision cannot be accepted — the citation must move to the rule that displaced it — and an observation-backed rule needs an owner decision. Each row's reason was already accurate; the summary beside it now is too.
- **The citation scan was quadratic in file size and walked an unbounded file set.** Line numbers were recomputed from the start of the buffer for every match (about 22 seconds for one file with 131k citations, and minutes at the size ceiling — enough to stall the gates). Offsets are now computed once per file and bisected (about 0.11 seconds for the same input), and the walk carries a file-count ceiling. A finding caps its reported locations with a count of the remainder, and every untrusted value it emits is redacted.

## [3.17.0] — 2026-09-16

### Added

- **`sync.sh` now prints the `release` log entry for the release it just made.** The entry carries the date and time, the source commit, the published commit, the tag and the tree hash. The release runbook already had a preflight, a dry run, six gates and a byte-identity check, and no step that recorded the release anywhere. Three releases shipped without a record before an audit noticed. The entry is printed for the operator to paste rather than written automatically. Writing into the documentation tree would leave it dirty, and the next release refuses to run on a dirty tree.
- **`read-news.py` takes `--since YYYY-MM-DD`, and the `read-news` skill queries each curated source with that source's own `last_seen`.** Search results carry both a publication date and a last-crawled date, and ranking follows the crawl. An old page re-crawled last night could therefore look brand new and crowd out genuinely fresh posts. `--since` filters on the publication date alone; results whose publication date cannot be determined are dropped. The response reports `dropped_older` and `dropped_undated` separately, so a quiet night stays distinguishable from a filter that removed everything.

### Changed

- **A journal heading whose category is not in the category list now fails the journal-index run instead of being silently skipped.** Previously such a heading opened no entry and produced no warning, so the month lost an entry while every check still passed. `generate-journal-index.py` now reports one `validation_errors` item per bad heading, naming the file, the 1-based line and the offending token. It refuses in write mode, `--dry-run` and `--check-stdin` alike. The existing index is left untouched. Detection is deliberately narrow: a malformed date is still reported as a date problem, and headings inside fenced code blocks are still treated as content.
- **`cleanup-campsite` rule CLN-JR-1 now accepts the citation form the journal is actually told to write.** The rule only looked for a wiki-link to the accepted ADR. The writing guidance tells the journal to cite `rule:<slug>` whenever a rule exists, and to name the ADR only when it has no `governs` block. ADRs with a `governs` block could not satisfy both, so they were reported as permanently missing a reflection. The rule now accepts either form. A slug counts only when the summaries resolver maps it to a handle owned by that ADR. An unresolved slug, or one owned by a different ADR, is reported as a rejection naming what the citation actually resolves to. A retired slug counts for the ADR that owns its successor. A tree with no summaries projection reports `resolver_available: false` and returns slug tokens as unverifiable, keeping "no reflection" distinguishable from "could not check".
- **The tree schema now documents what changing the log-op or journal-category enum actually involves.** The schema previously claimed adding or renaming a log op was a one-line edit; in practice it touches several files. The distributed template now states that both enums belong to the installed plugin, and names what each skill does with its copy. `log-work` refuses an unknown log op. It falls back to `misc` with a warning on an unknown journal category, and `audit-docs` reports a heading outside either list without editing it. Both copies also gain a paragraph making clear that log ops and journal categories are two separate lists, with `release` a member of both.

### Fixed

- **A `release` log entry now records when it was published, in a named timezone.** `sync.sh` previously stamped the entry with the machine's local date and no time. A release cut after local midnight UTC-side was filed under the wrong day, with nothing in the entry to show it. Entries now carry the publication time in `America/Edmonton`, rendered MDT or MST as the date requires, and the heading carries that same day. Headings stay date-only, so existing entries are unaffected.
- **The journal category list installed into new projects was missing `release`.** The `USER_GUIDE.md` template shipped with the plugin still listed nine categories after the tenth was adopted, even though the other copies of the guide had it. The template now lists all ten. `release` is also confirmed present in the schema's log-op enum, and in the paragraph that separates the two enums.

### Removed

## [3.16.1] — 2026-09-15

### Fixed

- **Asking a question about the architecture no longer risks rewriting files.** Phrases such as `summarize the current architecture`, `what's the current architecture`, `what supersedes what` and `how do the ADRs relate` could previously be picked up by `derive-arch` or `link-adr-graph`, both of which regenerate documentation — so a read-only question sometimes triggered a write. These four questions now route to `query-docs`, which answers from the existing docs without modifying anything. Commands that ask for output to be produced, such as `show ADR lineage`, still go to the skill that produces it. Skills that regenerate, replace or rewrite files no longer claim question-style triggers.

## [3.16.0] — 2026-09-15

### Added

- **`metadata.triggers` — a skill now declares its routing phrases explicitly.** Add an optional pipe-separated `triggers` field under `metadata:` in a skill's frontmatter; it is projected into the skills catalog as a JSON array and used to build the routing table. The separator is a pipe (not the comma used by `tags` and `bundles`) because a trigger is a natural-language phrase and may itself contain a comma. 56 of the 60 shipped skills declare 270 phrases; the four with none are `user-invocable: false` and route through the Claude-only table, which has no phrase column. **Breaking:** `plugin.json` `schema_version` moves from `"3"` to `"4"`. The metadata contract is strict, so a skill carrying `triggers` validates under a reader that knows schema 4 and fails validation under one that does not.
- **Regenerators now agree on whose tree they act on.** Every regenerator resolves its target tree from `--repo-root`, defaulting to the current working directory. A regenerator whose output belongs to the plugin's own source checkout, when run anywhere else, takes a "surface absent" lane: it exits 0, prints `{"surface_absent": true, "drift": false, "reason": ...}`, and writes nothing. The `check-drift` gate table gained a `Scope` column declaring each row as `project` or `plugin-authoring`.

### Changed

- **Every skill description is now 91–133 characters (down from 245–924).** Codex's skill loader silently truncates descriptions at roughly 220–240 characters, so previously all 60 skill descriptions reached the model cut off mid-sentence; now all 60 render whole. Descriptions are prose describing what a skill does and how it differs from its neighbours, rather than a list of phrases to match.
- **The routing table generator reads declared `triggers` first and falls back to the description.** Previously it only extracted quoted spans from the description's first sentence, so the shorter descriptions would have emptied the user-phrase column for every user-facing skill without tripping the drift gate. A skill that still writes its triggers inline keeps routing; the new field is additive.
- **`check-drift` now distinguishes "does not apply" from "passed".** Each gate row carries its scope; an out-of-scope row is reported N/A with the gate's own reason rather than as clean or broken, and a gate that inspected the plugin's own copy of itself is never recorded as a passing project result. The skill also documents its one side effect — running a gate writes `__pycache__` beside the plugin — instead of claiming a run writes nothing.
- **`generate-lineage.py`, `generate-index-rollup.py` and `generate-readme-footer.py` print JSON on every path.** Two used to print a prose diff and the third printed nothing when clean, so the drift report that parses their output could not classify them. The diff is preserved in a `diff` field.
- **The librarian names an unobserved claim rather than reporting its own read coverage.** Reporting how much of a page it read displaced the one slot an unsettled claim needed and hedged answers nothing contradicted. Its route is unchanged: `docs/index.md` down to the pages.

### Fixed

- **Four trigger phrases in the authoritative routing table were wrong.** They had been recovered from the old descriptions by regex, which cannot tell a trigger from a quoted word: `read-news` claimed the bare word `what` (from `what's new in the ecosystem`), `derive-arch` claimed `why` (from a parenthetical), `run-promptbook` claimed a phrase that should route read-only status questions to `visualize-run-progress`, and `patch-cycle` lost a 71-character phrase to a length ceiling. All four are repaired, and validation now rejects the whole class mechanically: no phrase claimed by two skills, none containing the table's ` / ` separator, and no bare interrogative.
- **`metadata.triggers` accepted any token type.** Numbers, booleans and mappings were coerced with `str()` and shipped as phrases, duplicates rendered twice, and a token containing a newline could break the Markdown routing table. Each is now refused. One limit is stated openly: in the string form a token containing the pipe separator cannot be distinguished from two tokens, so that case is only refused in the list form.
- **The shipped `CLAUDE.md` template documented a contract that now fails validation.** Its §7.A named `plugin.json` schema `"2"` and listed `owner`, `version` and `status` as required metadata keys — all three have been removed and any lingering one is a validation error. The section now names schema `"4"` and documents `routing_note` and `triggers`.
- **`install-docs-skills` compared the wrong two numbers and told every existing consumer to STOP.** It read `schema_version` from the installed `plugin.json` (the SKILL.md frontmatter contract) and compared it to a project's `manifest.yml` value (the documentation tree layout) as if they were the same axis. Throughout the 3.x line the first read `"3"` and every tree read `"5"`, so the documented upgrade path refused every project that had one. The skill now reads the supported tree version from the shipped `templates/manifest.yml.tmpl`, which is the manifest `init-docs` writes.
- **`log-work`'s description claimed it updates the journal index by hand.** It runs the regenerator that derives the row; the description now says the index is regenerated.
- **Six regenerators targeted the plugin cache instead of your project when run from an installed plugin.** `validate-catalog.py`, `generate-opencode-agents.py`, `generate-readme-footer.py`, `generate-writing-rules.py`, `generate-routing-table.py` and `generate-runtime-compat.py` derived their root from their own file location or required a `crux/` tree beside the caller. From a consuming project, three crashed, two reported errors against paths inside the plugin cache, and two passed clean having validated the installed plugin against itself — and the suggested remedy for the `opencode/agents/` row would have written into your plugin cache. Each now reports `surface_absent` outside the plugin's authoring checkout and behaves unchanged inside one.
- **`check-public-release-content.py` scanned the installed plugin when run from a consuming project.** `tend-garden` invokes it, and its default root came from its own file path, so it scanned the plugin's distributed surfaces and exited 0 — a verdict about the plugin reported to someone asking about their project. The script now declines a root that holds no plugin source.
- **Three gates reported a failure when run against a checkout that contains `crux/` but no documentation tree.** `generate-writing-rules.py` and `generate-routing-table.py` exited 1 naming a file that does not ship, and `generate-rules-catalog.py` exited 2 because every slug it had to resolve came from a tree that was not there. Each now takes the surface-absent lane. A file that is present but carries no marker is still a validation error, because that is a real defect in a tree that owns the surface.
- **`audit-docs` read the surface-absent payload as a clean pass.** CHK-CAT-3 and CHK-ROUTE-1 keyed on `drift: false`, so a consuming project's audit recorded the catalog and routing table as verified. Both now report N/A with the payload's reason, as CHK-DRIFT-1 already did for the reviews and journal indexes.
- **`OPENCODE_GUIDE.md` told readers to run the agent regenerator by absolute path from any directory.** With roots now resolved from the working directory, that would regenerate nothing and exit 0, leaving a silently stale projection. The two documented invocations now `cd` to the clone first.
- **`generate-adr-index.py` crashed on `import yaml` outside the authoring checkout.** It was the one regenerator without a PEP 723 dependency header, so `uv run` installed nothing and the import only resolved where PyYAML happened to be available. It now declares `pyyaml>=6.0`.

### Removed

## [3.15.0] — 2026-09-15

### Added

- The objectives-alignment rules are now individually citable. Five rules — read the objectives before work, the two orchestrating roles read them at startup and on resume, how the mission shapes the work and what it does not authorize, what a delegation carries, and the populate gate — each have their own identity and are grouped into a single objectives domain.
- **The plugin now ships the rules its own instructions cite.** Previously, a `rule:` citation on a shipped skill, agent or template pointed at a record that did not exist in an installed project, so the citation resolved to nothing. A rule catalog now ships alongside the plugin and the tree-contract template states where a citation resolves. No generation, fetching, or configuration is required on your part.

### Changed

- The instruction files at the repo root and the block that `init-docs` writes into a new repo now point at the single statement of the objectives rules instead of each restating them. Each file keeps the resolved path it owns. The three copies had previously drifted from one another and from the contract.
- The template-parity check now carries one clause per objectives rule, up from a single clause covering all of them. The read-before-work sentence, the application paragraph and the populate gate were previously not covered by any clause.

### Fixed

- **A parity clause now compares how many times its text appears on each side, not merely whether it appears at all.** Deleting a governed sentence from the tree contract used to pass as long as another occurrence of the same wording survived elsewhere in the section; the clause guarding the append-only freeze, for example, reported parity while actually matching a heading and an unrelated bullet. Deleting a governed sentence is now reported as a finding from either side: four sentence deletions were driven, each from both files, and a wider sweep removed the text of all 159 top-level alternations from the canonical side, every one of which produced a finding. A clause whose text is absent from both sides is reported as a stale manifest entry rather than as agreement.
- **A `rule:` citation now resolves according to the surface it sits on.** A project that minted a rule slug the plugin also uses could previously redefine that rule inside the plugin's own instructions, with no way to distinguish a deliberate override from a silent substitution. A citation on a shipped surface now resolves against the shipped catalog regardless of what the project defines; a citation on a project-owned surface resolves against the project's rules first; and a slug both sides define with different text is reported wherever the lint runs.
- A shipped rule catalog that is present but unreadable — corrupt, truncated, or not a slug-to-rule mapping — is now reported as a refusal instead of being treated as absent, which previously sent shipped citations silently back to the project's rules. Only a genuinely absent catalog (as with an older plugin or a partial checkout) falls back.
- A parity finding covering two alternations that drifted in opposite directions used to name one side for both, pointing you at the wrong file. Each alternation now names the side that holds fewer occurrences, and a manifest entry that matches neither side is no longer dropped from the message when a sibling drifts.
- A parity clause whose pattern began with an inline regex flag lost that flag when the pattern was split, so an anchored alternation in a multiline pattern matched nothing on either side and was misreported as a stale manifest entry instead of the working clause it was.

### Removed

## [3.14.0] — 2026-09-14

### Added

- **The OpenCode installer now reports which runner it probed and what it concluded.** Its JSON output includes a verdict, every candidate executable it checked along with the version that candidate reported, and whether the run proceeded on a human assertion rather than on a recognised version.
- **New `--assume-compatible` flag.** Lets you install against a resolved runner whose reported version this release does not recognise. It cannot override a runner that reports a major version below 2, and it refuses to run when no candidate runner resolved at all, since there is nothing for the assertion to bind to.

### Changed

- **The OpenCode installer identifies a compatible runner by the version it reports, not by the name of its executable.** OpenCode 2.0.3 installs as `opencode`, so the previous check (`opencode2 --version` exiting 0) refused to write on a correctly upgraded machine and wrote on a machine whose `opencode2` was still a V1 binary. The installer now probes `opencode` and then `opencode2` — or the single runner named by `CRUX_OPENCODE_BIN` — and classifies each by its reported major version.
- **`CRUX_OPENCODE_BIN` replaces `CRUX_OPENCODE2_BIN`.** The old variable name still works; when it is used, the run says so in its output.
- **All OpenCode-related documentation now refers to the runner as `opencode` and tells you to check its version.** The executable name no longer distinguishes a V1 runner from a V2 one, because an upgraded machine may keep an `opencode2` shim pointing at the same 2.x binary.

### Fixed

- **A runner reporting a version below 2 is now refused regardless of its exit status**, and no flag can override that refusal. Such a runner reads the generated `permissions` array with every `deny` entry silently dropped, which is exactly the failure the preflight check exists to prevent.
- **The documented number of `audit-docs` checks was wrong in four places**, and by different amounts in each. The README gave `~45` in one table and `~54` in another; the skill gave `~65` in its overview and `~70` in its total. The suite actually defines 97 checks across eighteen groups, and all four places now read `~97`.

## [3.13.0] — 2026-09-13

### Added

- **A contract requiring an assignment between agents to state its outcome, its evidence, and its constraint.** The contract requires an assignment to name what should improve for the affected user, what would demonstrate that improvement, and what the change must preserve; to carry those three statements through every delegation hop; to have a reviewer commissioned by the delegator rather than by the party whose work is under review; and to mark each evidence item as verified, contradicted, or unobserved. A direct fix states all of this in one sentence. There is no new field, no form, and no approval gate. The contract and the agent instructions that carry it ship in this release. Whether agents follow them in practice is not established by this release, and no measure in the project yet reports on it.
- **Crux now checks that its own documentation rules still apply to the text they name.** The check verifies that each alternative phrasing in a rule's gating clause still matches its governing text. A rewording can leave one alternative applying to nothing. Its first run surfaced three alternatives that had stopped applying after an earlier rewording, and all three are repaired in this release. The check runs in the crux development repository and guards crux's own documentation; it is not a check over your project's rules.

## [3.12.1] — 2026-09-11

### Changed

- **The Codex reviewer now runs on GPT-5.6 Sol at `xhigh` reasoning effort.** The version 3 model catalog allows a single agent to fully override its Codex runtime before the usual level-based fallback applies. Only the reviewer uses this override; the other nine roles keep their existing Codex settings.

### Fixed

- **Contributors' personal Codex agents are no longer shadowed by the development repository.** The development checkout previously projected its own set of Codex agents, which could override a contributor's personal agent definitions. That projection is no longer tracked, so after a Codex restart your personal agents once again supply role models, reasoning effort, and skill bindings when working in the development repository. This does not affect the published plugin.

## [3.12.0] — 2026-09-11

### Added

- **Codex and OpenCode installation guides.** `CODEX.md` and `OPENCODE.md` each give separate installation procedures for humans and for agents, and the README links them as the detailed installation references.
- **Crux can install its ten Codex agents into your personal Codex home.** Each installed role pins its catalog model, reasoning effort, sandbox, and declared skill files. The installer also reports managed drift, project-level shadows, plugin relocation, and unverified runtime evidence.

## [3.11.1] — 2026-09-10

### Added

- **The decision review assesses each objective against its declared measure, part by part.** Coverage gains a seven-column assessment table (objective, measure, evidence, locator, domains, conclusion, findings) with one row per measure part. Evidence availability (`resolved | partial | unavailable | not-attempted`) and alignment conclusion (`serves | gap | inconclusive | not-assessed`) are separate fields; one counterexample establishes a gap, but only evidence covering the whole claim establishes alignment, so partial evidence never reaches `serves`.
- **Attempting an assessment and measuring one are now distinct outcomes.** `measured_objectives` carries one entry per counted thing — outcome, writing pass, and an evidence locator or blocker. Both outcomes discharge the three-report rotation; on the third consecutive attempt a pass owes one finding proposing what would make the measure reachable. Older flat-list reports still read (as `attempted`) and need no rewriting.
- **A finding must earn its causal claim.** Each finding carries an attribution naming the mechanism, a confidence (`established | contributing | correlated | unresolved`), and the one discriminating check that would move it. An `unresolved` finding is never routed to Revoke, and revocation requires positive evidence of a cost from continued observance.
- **Where no signal reaches a measure, the objective can initiate a bounded investigation.** An investigation slip names the objective, the measure verbatim, the question, the domain, and the intended evidence. The budget is three investigations per pass and five surfaces; a command that would exceed the budget is refused rather than partly spent.
- **Every delivery measurement is bounded by a release mark.** A release is identified by the first commit whose changelog carries that version's dated heading. `release_cadence` reports `span_commits` (commits in the release's interval) and `prep_commits` (commits in that interval whose subject matches the declared prefix) per release, over non-overlapping intervals.
- **A closed set of nine conditions replaces a bare null in delivery signals.** `no-release-record`, `baseline-ref-unresolved`, `baseline-ref-ambiguous`, `history-unavailable`, `no-baseline`, `surface-absent`, `surface-unreadable`, `surface-malformed`, and `surface-not-comparable` each name exactly one situation. Zero is a measurement and is never a substitute for missing data.
- **The escalation count is carried across review dates.** Each entry carries `escalation_strikes`, `escalation_owed`, `escalation_owed_since`, `escalation_spent_on`, and `escalation_state`, verified against the previous report. A contradiction is refused; where an older report breaks the chain, the count is reconstructed from the twelve newest dates and marked `unverified`.

### Changed

- **A blocked measure part is counted in its own right.** `measured_objectives` keys on the objective-and-part pair when an entry names a part, so one measured part no longer resets the count that surfaces another part's blockage. Every entry carries a digest of the measure text it concerns.
- **A null baseline no longer concludes.** A trend part measured against a null baseline is `partial` evidence reaching `inconclusive`, never `gap`. Where the measure asserts that something is measurable, a separate part carries that claim and may reach `gap` honestly.

### Fixed

- **`extract-code-docs` could write one tree's pages into another and prune what it found there.** Run with `--config` naming another tree's manifest and no `--output-dir`, it took the output default from the current directory and deleted every page there. One manifest now owns both the sources scanned and the pages written; an explicit `--output-dir` is still supported and validated, and combining one tree's config with another tree's output directory is reported on stderr.
- **A fresh tree recorded no friction adoption date, so it could never measure friction.** The manifest template now carries `journal.friction_line_from`, which `init-docs` substitutes, so a tree measures friction from its first conforming entry. An `init-docs --force` re-bootstrap carries an existing tree's recorded date forward rather than resetting it.
- **A trailing comment on the `journal:` manifest key hid the adoption date from its reader**, making every friction reader report `unmeasurable` against a manifest that recorded one.
- **Twelve skills rendered an empty routing cell in the skill invocation table.** The trigger-phrase extractor ended a description's first sentence at any period, including one inside a dotted identifier. It now ends at a period followed by whitespace or end of text, eleven descriptions were rewritten to state their phrases where the table reads them, and a test now fails on any user-invocable skill with no phrase.
- **`prep_commits` counted development commits between subject labels rather than preparation.** It now counts within release boundaries, so an unmatched release is reported as null rather than absorbed into a neighbour's bucket.
- **The schema-growth baseline no longer depends on a release tag.** It is now the release mark of the second-newest dated changelog heading, a commit, so the baseline resolves in a repository that mints no tags.
- **A decision-review report written from the shipped template read as a positive record of zero measurements.** The template's own `##` guidance lines terminated the `measured_objectives` list; `#` comments are now transparent at any indent.
- **Carried escalation fields are validated.** `escalation_spent_on` now has a grammar, `escalation_state` is a closed set (a typo no longer reads as `verified`), and an unrecognised entry field is refused rather than retained silently.
- **A bounded objective id no longer renames a goal, and the per-goal matrix refuses orphan rows.** `OBJ-99999` could match its four-digit prefix and discharge `OBJ-9999`; a mistyped matrix header silently dropped every row beneath it. Both are refused.
- **The decision-review reader no longer drops malformed frontmatter in silence.** A `measured_objectives` entry naming no goal, an id outside `OBJ-N`, a missing or unknown outcome, an unrecognised scalar, or an unclosed list is refused rather than read as zero entries.

## [3.11.0] — 2026-09-09

### Added

- **Journal-index derivation is now operational.** A shared module holds the parsing and derivation for the journal index month row, fence-aware so a heading quoted inside a code fence opens no entry. `generate-journal-index.py` regenerates the index and carries a `--dry-run` drift gate; an unclosed fence refuses the write at exit 2, naming the source file and line.
- **The decision review tracks a finding across dates.** A report carries a five-column record table under `## Coverage` (source report date, finding id, pass, event, locator). A finding with no record is open, `re-verified` leaves it open, `disputed` disputes it, and only `resolved` closes it. The generated reviews index gains a `standing` column beside `raised`.

### Changed

- **`log-work` derives the journal-index row instead of incrementing it.** Every write recomputes counts and category rollups from the month file. `init-docs` writes the first index when it creates the tree.
- **`check-drift`'s `surface_absent` verdict now keys on the payload key regardless of which gate emitted it**, and names the remedy per surface: `review-decisions` when a review is due, `init-docs` when the journal surface was never created.
- **A report's finding count is its definitions, and the reviews index renders four columns.** Only `###`-headed entries under Propose, Amend, Repair or Revoke count toward the total and the five-finding cap. The new `report_grammar` frontmatter discriminator selects the legacy count for older reports; a report dated after 2026-09-08 with no discriminator is refused. The index row now reads `raised`, `standing`, `dismissed`, and the report link. `generate-reviews-index.py` also refuses symlinked or dangling `adrs`/`reviews` segments and bounds its reads.

### Fixed

- **The architect agent's contract and both user guides cite the live review boundary.** The architect now states the five-path write set and the two log ops, and the OpenCode and Codex agent projections are regenerated from it.
- **`adr-signals.py` refuses a symlinked dormancy surface by name, even when the link dangles**, instead of silently dropping it from the narrowing.
- **The decision review's write set is five paths, and its recording composes with `log-work`.** A completed pass no longer leaves the journal index stale.

## [3.10.1] — 2026-09-09

### Changed

- **Beta before official.** Each version is now installed and tested from a private beta channel before it is published to `bionic-coding/crux` with identical bytes; a version that fails beta never ships officially.
- **README title and opening line.** The README is titled "Crux" and describes an Agentic Harness plugin.

## [3.10.0] — 2026-09-08

### Added

- **Three delivery signals.** `adr-signals.py` gains `release_cadence`, `schema_growth`, and `gate_count` beside its five existing signals. Every git leg runs a fixed argument list under a strict environment allowlist, so neither user nor system git configuration is read.
- **The decision-review report carries six sections.** A report dated after 2026-09-07 carries Propose, Amend, Repair, Revoke, Keep, and Coverage in that order, one summary table, and one data-framing note. Repair is where a finding lands whose enacting act writes no ADR file. The five-finding cap counts across Propose, Amend, Repair and Revoke; Coverage gains a per-goal matrix. Older reports keep the sections they were written with.
- **The journal `Friction:` line.** A journal entry body may carry one `Friction:` line naming a specific friction in that unit of work, placed before `Refs:` and counting toward the body-line budget. The new `journal.friction_line_from` manifest key records the date from which a friction count is measurable. `log-work` writes the line; `retrospective` and the friction signal count it.
- **One shared CommonMark fence reader.** Fence handling for mined values now lives in one module used by every caller, with one conformance suite.

### Changed

- **The public repository moved to the `bionic-coding` GitHub organization.** Install paths now read `/plugin marketplace add bionic-coding/crux`, `codex plugin marketplace add bionic-coding/crux`, and the OpenCode stable-path clone of `bionic-coding/crux`. The plugin manifests' `homepage` fields point at `https://bionic-coding.com/crux/`, and the README opens with a "Start here" link to that site.
- **Apex roles run Fable 5.1 on Claude, and the night gardener joins the apex tier.** The commander, reviewer, and night gardener run Fable 5.1 (a `fable-latest` alias is added); the reviewer's turn budget rises to 150 and the night gardener's to 100. Standard-tier Codex roles now run at high reasoning effort.
- **`review-decisions` aims the pass at delivery.** The skill runs eight signals rather than five, routes each finding by two questions about its proposed act, and disposes the top three `dormancy_days` and top three paper-only ADRs each pass with `keep`, `revoke`, or `defer` and one reason. A `placeholder` objectives file stops the pass rather than yielding a judgment with no yardstick.
- **The template-parity check grows from 30 clauses to 35**, pinning the `adr_review_due_days` and `journal.friction_line_from` manifest keys, the `Friction:` line grammar, the reviews-surface report shape, and the `adr-review` log-op grammar.

### Fixed

- **`init-docs` creates the decision-review surface.** A bootstrapped tree now has an `adrs/reviews/` directory, so the first cadence nudge points at a path that exists.
- **`log-work` no longer cites a retired caller for its log-only branch.**
- **`adr-signals.py` reads both member shapes of `adr.governs_exempt`** — the bare id and the reason-bearing `{adr, reason}` form — in flow or block style.
- **The operational schema documents both exemption forms** rather than calling the reason-bearing form deferred.
- **Mined values are split into lines by the CommonMark rule, not `str.splitlines()`.** Characters such as `\x0b`, `\x0c`, `U+2028`, and `U+2029` no longer forge a heading, table row, or fence closer.
- **The fence reader bounds its indent and its closer on the same character class**, so a fence indented with a tab or non-breaking space no longer opens, and a closer shorter than its opener no longer closes.
- **The `prep_commits` mapping key carries a full 64-character digest**, so two version strings cannot share a slot.
- **The table lane redacts a mined value's key as well as its value.** An ADR id carrying terminal escape sequences could clear the terminal or colour forged text; keys now render quoted and redacted.
- **An uncompilable version pattern reaches the reader as a finding, not an environment error.** That version counts `null` and the other signals still compute, instead of all eight exiting 2.

## [3.9.0] — 2026-09-07

### Added

- **`review-decisions`, the periodic architect review of the decision set.** The 60th skill reads accepted decisions as a set and asks whether they still serve the objectives. It enters through the doctrine index and the summaries rule table, opens an ADR body only for a domain a signal flagged, and writes at most five findings into one dated report at `adrs/reviews/YYYY-MM-DD.md` in your docs tree. It proposes only: it transitions no record, signs off no batch, and authors no skill.
- **`adr-signals.py`, five mechanical signals as verdict envelopes.** A stdlib-only script computes amendment fan-in, carve-out count, paper-only, dormancy, and friction citations. Every signal is a five-member record (`signal`, `verdict`, `value`, `basis`, `filter`) with no severity or recommendation. `friction_citations` reports `unmeasurable` rather than `0` when the evidence cannot be read.
- **A reviews index regenerator.** `generate-reviews-index.py` derives `adrs/reviews/index.md` from the dated reports and fails closed on a filename outside `YYYY-MM-DD.md` or a report whose frontmatter disagrees with its filename. A tree with no reviews directory exits 0 with `"surface_absent": true`, which `check-drift` reads as N/A rather than clean.
- **The objectives file.** `objectives.md` holds the product's mission and goals — a six-key frontmatter with a `maturity` ladder, a required `## Mission`, `OBJ-N` goals each with a kind, statement, measure and status, and an append-only `## Shifts` table. `init-docs` seeds it as a placeholder; every reader asks the owner to fill it in rather than citing a placeholder.
- **Lint rules for the review.** `audit-docs` gains five `CHK-OBJ-*` rules over the objectives file, including a WARNING when `reviewed_at` is older than `review_every_days`. `cleanup-campsite` gains `CLN-ADR-5` (a decision review older than `adr_review_due_days`, default 7) and `CLN-OBJ-1` (an objectives file still at `maturity: placeholder`), and now ships 20 rules. The log op enum gains `adr-review`.

### Fixed

- **A second `review-decisions` pass on one date no longer overwrites the first.** The skill amends the existing report in place, keeping every earlier finding id.
- **`adr-signals.py` reads doctrine rows whose rule text carries an escaped pipe** instead of dropping them and potentially marking an ADR paper-only.
- **The friction signal reads the forge log under every runtime's local skills directory** — `.claude/skills`, `.agents/skills`, `.opencode/skills`, and `.opencode/skill`.
- **A future-dated review report is refused.** `generate-reviews-index.py` refuses a date after `--today` (default: the system date), and the night gardener applies the same filter, so one file cannot suppress the cadence reminder indefinitely.

## [3.8.0] — 2026-09-04

### Changed

- **Async text and image councils use GPT-6 Astra as their OpenAI judge**, through OpenRouter with OpenAI pinned as the serving provider. The synchronous council keeps its Terra seat.
- **Council Fable judges use Fable 5.1**, preserving their effort settings. Other callers of the shared Anthropic roles also receive Fable 5.1.
- **Codex apex roles (commander and reviewer) use GPT-6 Astra with high effort.** Codex flagship and standard roles keep Sol and Terra.

### Fixed

- **Two test fixes:** the OpenCode installer error test no longer requires a local OpenCode installation, and model-catalog tests handle distinct apex and flagship models.

## [3.7.1] — 2026-09-02

### Fixed

- **Every script header now declares the real Python floor, 3.11.** Fifty-two scripts declared `>=3.10`, but the Python arch pack uses `tomllib` and several tests use `unittest.TestCase.enterContext`, both 3.11-only. The headers, the user guide, and the schema example now say 3.11. Four corpus-golden test comparisons skip below 3.13 with a named reason.
- **The README states the Python floor and the test command.** The test suite needs tree-sitter grammar packages that only the script headers name, so the Requirements section now carries the `uv run` invocation that resolves them.

## [3.7.0] — 2026-09-02

### Added

- **The skill runtime-compatibility block has one canonical source.** The block carried by hand in most `SKILL.md` files is now regenerated from one template, with a `--dry-run` drift gate.

### Changed

- **Breaking: the OpenCode projection emits the V2 schema exclusively.** Installed `.opencode/agents/` files replace the singular `permission:` map with an ordered `permissions` array of `{action, resource, effect}` rules under last-match-wins. Two actions are renamed: `bash` becomes `shell` and `task` becomes `subagent`. The install target moves from `.opencode/agent/` to `.opencode/agents/`; `install-opencode-agents` gains `--migrate-legacy-agent-dir` to move a populated legacy directory, refusing on a name collision unless `--force` is given. The installer requires a discoverable `opencode2` binary. A V1 OpenCode runner reading the new projection drops every deny rule with no warning.
- **The commander can dispatch the wayfinder** to size up a large, uncertain, or external source before another agent spends context on it.
- **`dev-lead` routes to GLM 5.3 on OpenCode** (`openrouter/z-ai/glm-5.3`); its Claude and Codex seats are unchanged.

### Fixed

- **`web-to-markdown` passes a Markdown or plain-text response through unchanged.** A `text/markdown` or `text/plain` body previously went through the HTML converter and came out as one collapsed line.

## [3.6.0] — 2026-09-02

### Added

- **A decision is now cited as `rule:<slug>`**, the slug half of the governs handle that carries the rule, on every surface — code comments, prose, promptbook prompts, and journal refs. The linter that resolves the token fails on an unknown slug, fails on a retired slug naming the live rules that displaced it, and fails when its resolved scope is empty.

### Fixed

- **The archive-tier ADR readers no longer follow a symlink out of the tree.** An `ADR-*.md` symlink planted in the archive is refused with the same message the active-tier reader uses.
- **The `srde` skill's batch example now builds `DissentPoint` objects**, matching what `attempt_batch_resolution` accepts.
- **The plugin's own `crux/README.md` is enrolled in the doc-count gate** and corrected from "55 skills" to 59.

## [3.5.0] — 2026-08-31

### Added

- **Batch ratification for observations: `survey-sheet` and `survey-signoff`.** `survey-sheet` scaffolds one `SVY-NNNN` review sheet from the candidate state file; `survey-signoff` is the single human sign-off that publishes the sheet under one digest-bound receipt, equivalent to N individual ratifications. Both carry `disable-model-invocation`. `audit-docs` gains four `CHK-OBS-SURVEY-*` rules.
- **The `fix-directly` skill: the rung below the three cycle tiers.** "Just fix it" now routes to a named contract — a failing test first, the smallest green change, the suite and the drift gates, one commit, one `log-work` entry. A five-question sizing test decides between it and `patch-cycle` / `iterate` / `dev-cycle`.

### Changed

- **`cleanup-campsite` retires `CLN-ADR-1`.** The rule flagged accepted ADRs unmentioned in README/USER_GUIDE within 30 days and only accumulated findings; 18 rules remain implemented.
- **The verify templates carry a reproduction budget.** The `iterate` verify module and the `patch` verify phase state that a failing test is a complete reproduction, a class earns its own reproduction only at a second independent instance, and a security label must not widen the fix. `whiteboarding` gains a sizing step.

### Fixed

- **Three survey sign-off defects.** `assert_signable` now runs on every publish path (including resumed batches); the index retire loop splits on unescaped pipes only; `CHK-OBS-BIJECTION` reads each row's id column rather than grepping the whole page.
- **The arch drift gate no longer treats the decision-recovery state file as a stale artifact.** `derive-arch.py --dry-run` excludes `arch/_recovered/` from its sweep.

## [3.4.0] — 2026-08-30

### Changed

- **The OpenCode commander runs Qwen3.8 Max (1M context) instead of Kimi K3**, which could not sustain the orchestration role. Every other role's OpenCode model is unchanged.
- **Each arch stack-pack probe declares its own input class against a committed roster.** The doctrine index's `implemented` column is renamed `basis`. A `governs` entry's `retires` sub-field displaces a rule while keeping its record on the ledger.
- **Breaking: a `governs` entry's sub-field set is now closed.** `summarize-adrs.py` and `compile-doctrine.py` both refuse an unrecognized sub-field, so a tree carrying any other annotation key fails both regenerators on upgrade.

### Fixed

- **A book's `current_run` pointer now survives a run's completion**, so `archive-promptbook` can read the pointer its precondition requires. `current_run` nulls only at archival.

## [3.3.0] — 2026-08-30

### Added

- **A repository that has never authored an ADR can build a doctrine from what its code already does.** The `observations` concern adds `OBS-NNNN` records, each evidenced by a `path:line-range` and ratified by a human, projecting into the summaries rule table and the doctrine index beside the ADRs.

### Changed

- **`derive-arch` records a per-concern verdict instead of grading its own confidence.** A concern is `populated` or `stubbed`, and every stub names one reason from a closed set of six. The new `arch.require` list in `manifest.yml` fails the derive when a required concern is not `populated`.
- **The arch extractors read committed artifacts and real parsers, never regular expressions over source.** Python routers parse through the standard-library AST; Ruby reads a committed `rails routes` dump; Ruby, Node, and Elixir source parse through tree-sitter grammars. Node routes compose `app.use` mount prefixes, and Elixir routes parse the parenthesized form.

### Removed

- **The `arch_confidence_threshold` key in `.bionic.yml` is retired.** A tree that still sets it loads clean.

## [3.2.2] — 2026-08-29

### Fixed

- **The agent-catalog YAML-gate test skips cleanly without PyYAML** instead of reporting a spurious failure.

## [3.2.1] — 2026-08-29

### Fixed

- **The cycle machinery can accept ADRs and archive books again.** `transition-adr` and `archive-promptbook` no longer carry `disable-model-invocation`, which had blocked `dev-cycle`, `iterate`, and `patch` runs from completing without a human keystroke. The four true human gates (`reconcile-signoff`, `backfill-signoff`, `escalate-arch-runtime`, `transition-invariant`) keep it.

## [3.2.0] — 2026-08-28

### Changed

- **Corrected the OpenCode `permission.task` projection.** A restricted `Agent(role)` grant now projects as a deny-first per-role glob object instead of collapsing to a coarse boolean. The widened-grant report and its sign-off file are retired as unneeded.
- **Documented the fields OpenCode ignores.** `$adr`/`$book` argument placeholders bind only in Claude Code, and a new `OPENCODE_GUIDE.md` section lists the invocation-control fields OpenCode does not honor.
- **The model catalog no longer disables `fable`**, so it is a routable agent `model:` value again.

### Fixed

- **`forge-skill`'s frontmatter guidance matches the schema-3 metadata contract.** It no longer names the removed `owner`/`version`/`status` keys, so a forged skill promoted into the plugin passes `validate-catalog`.

## [3.1.0] — 2026-08-28

### Added

- **`compile-doctrine`** regenerates the `adrs/doctrine/` tree wholesale from the summaries projection, reconciled against ratified invariants and the human-signed reconciliation ledger; `--dry-run` is the drift check. It stops and recommends `summarize-adrs.py` first when the summaries projection has drifted.
- **`check-drift`** runs every enrolled regenerator's `--dry-run` in one pass and reports one table of gate, verdict (clean / drift / broken / crash / refusal), drifted paths, and the regenerator that fixes it. It regenerates nothing. Wired into `audit-docs` as `CHK-DRIFT-1`.
- **`reconcile-signoff`** is the single human write path for one doctrine reconciliation: it renders the invariant and rule text, takes the verdict (compatible / reconciled / collision) and rationale from you, then upserts a digest-bound record and re-compiles doctrine on confirmation. It carries `disable-model-invocation`; `audit-docs` `CHK-DOCTRINE-1` counts pending pairings.
- **A signed record for OpenCode roles whose restrictions are unenforceable** (commander, dev-lead, night-gardener), with rationale, signer, and date.

### Changed

- **Catalog schema 3: invocation-control frontmatter and metadata prune.** `SKILL.md` admits the Claude Code invocation-control keys (`disable-model-invocation`, `user-invocable`, `context`, `agent`, `model`, `effort`, `background`, `arguments`, `disallowed-tools`); agents admit `maxTurns`, `effort`, `skills`, `memory`, `isolation`, and `disallowedTools`, each projected to Codex and OpenCode by a locked table. **Breaking:** the dispatch tool `Task` is renamed to `Agent` (with the restricted `Agent(role)` form), and the metadata keys `owner`, `version`, and `status` are removed from every skill and agent — a lingering one is now a validation error. The skill invocation routing table is now regenerated from skill frontmatter. `plugin.json` `schema_version` goes `"2"` → `"3"`.

### Fixed

- **A block-style `disallowedTools`/`skills` agent frontmatter list now fails validation** instead of silently mis-projecting (`bash` rendered `allow` instead of `deny`). Every shipped agent already authors them inline.

### Removed

- **Retired the `inject-knowledge` skill.** Its bundled knowledge layer shipped only a README and nothing referenced it. Git history is the recovery path.

## [3.0.0] — 2026-08-28

### Added

- **The doctrine layer — a third ADR-decision tier, compiled and reconciled.** `adrs/doctrine/` projects each governs domain's live rule, disposition, and implemented-vs-on-paper state from the summaries projection, reconciled against ratified invariants through a digest-bound, human-signed ledger, with a deterministic regenerator and a drift gate. Reads now route doctrine → summaries → ADR body, with the ADR body winning on disagreement.

### Changed

- **The `qwen-max` OpenCode model alias points to `openrouter/qwen/qwen3.8-2.4t-a95b`.** The standard-rung OpenCode agents (historian, librarian, wayfinder) resolve through it.
- **The architect agent's OpenCode model is the flagship default `kimi-latest`** (`openrouter/moonshotai/kimi-k3`); its per-agent `glm-latest` override is dropped.

## [2.2.0] — 2026-08-28

### Added

- **Governs backfill machinery — a historic ADR can now enter the summaries projection.** An ADR numbered below `adr.governs_from` joins the projection only through an anchored, digest-bound, reviewed, at-most-once backfill: the `anchor` `governs` sub-field, a `backfill-reviews.yml` receipts manifest, the `backfill-signoff` owner-gate skill, and the `backfill` log op.

### Changed

- **The "Silver" ADR-summary layer is renamed to "summaries".** `adrs/silver/` → `adrs/summaries/`; `generate-silver.py` → `summarize-adrs.py`. Forward-only: frozen ADR bodies, the log, the journal, and archived runs keep "Silver" as history.

## [2.1.0] — 2026-08-26

### Added

- **ADR frontmatter gains a `governs` block, and a regenerator projects it.** An ADR may author a `governs` entry (`domain`, `rule`, `scope`, `handle`, `provenance`), cohort-bound by the new `adr.governs_from` manifest field. `generate-silver.py` projects every block into a rule table, a resolver, and an ADR↔run implementation map, behind its own `--dry-run` drift gate. The rule table coexists with the arch decision index.

### Changed

- **Breaking: model calls crux itself performs now route through OpenRouter as a single inference gateway.** The router, the council, the catalog aliases, and the video transcriber resolve every call through one OpenAI-compatible endpoint under one `OPENROUTER_API_KEY`. The direct Anthropic/OpenAI/Google SDKs and the Fireworks provider are retired.

## [2.0.1] — 2026-08-25

- No user-facing plugin changes. This release corrected the development repository's release tooling so its gates run under the pinned interpreter regardless of the operator's `PATH`; the Python pin it added applies to the development repository only.

## [2.0.0] — 2026-08-25

### Added

- **A deterministic per-derive coverage report.** `arch/_meta/coverage.json` records, for each spine concern, whether it populated or fell back to a stub, and why. The report is byte-stable and rides the existing drift gate.
- **Confidence-graded arch extraction.** Each spine concern self-assesses a confidence grade (high, medium, low, or none). At or below the new `arch_confidence_threshold` config key (default `low`), a stubbed or partial concern offers an attended runtime-escalation session in chat. The unattended pipeline stays static and deterministic.
- **A model catalog — one hand-authored file decides which model every agent runs on.** `crux/catalog/models.yml` (`schema_version 2`) carries a provider allowlist, an alias table, the ten-agent roster keyed to three levels (`apex`, `flagship`, `standard`), the level table, and the Claude alias pins. A shared reader refuses a malformed catalog rather than degrading to a partial roster.
- **Validator rules V0–V9 over the catalog.** `validate-catalog.py` checks the roster against the agent files, every alias against the provider allowlist, each level's cells, and the Codex slugs against the router registry.
- **Python import-only runtime arch introspection, behind an attended two-factor consent gate.** The `escalate-arch-runtime` skill runs a target FastAPI, Flask, or Django app's import-time code in a subprocess-isolated child, recovering its route table and ORM schema as an advisory outside the arch spine. It executes only with `CRUX_ARCH_ALLOW_RUNTIME=1` set and a per-execution, non-model-mediated permission event.
- **A third cycle tier, `patch`, with a blast radius the archive gate checks.** `patch-cycle` authors a five-phase book at one prompt each; the book declares the repository paths it may touch, and `archive-promptbook` refuses to archive a run that reached outside the declaration (measured by `git diff` against the `base_commit` stamped at run start, plus untracked files).
- **An ADR body content rule.** An ADR body states requirements and postconditions, carries a 120-line budget over its four narrative sections, and names a source of truth rather than restating it. `audit-docs` gains `CHK-ADR-SPEC`, inert in any tree that has not set the new `adr.spec_rule_from` cohort boundary.
- **Two arch spine files are thin projections, and readers have a route to the spine.** `arch/api-surface.md` and `arch/decision-index.md` are projected from the skill catalog and the ADR index. A stale input is refused at exit 2, naming which regenerator to run. `query-docs` gains an architecture route, and all ten role definitions name `arch/` first for a question about the project's own shape.

### Changed

- **Council rounds are routed by blocking findings.** A round past the first fires only on a finding that names a failing check against an artifact inside the work tree. Round 3 is one adjudicator who is not the runner, within the unchanged three-round bound.
- **The OpenCode and Codex projections no longer carry their own model tables.** Both regenerators and both installers resolve through the model catalog.
- **In `crux/catalog/`, the file extension declares provenance.** `.json` means regenerated from a source of truth elsewhere; `.yml` means hand-authored and never written by a generator.

### Fixed

- **The arch drift gate no longer fires on edits that leave the spine byte-identical.** It now compares every property of `_meta/manifest.json` except the per-source hashes. `derive-arch.py` now declares PyYAML, so one source tree produces one spine hash.
- **The runtime arch-introspection executor is genuinely stdlib-only.** It no longer crashes with `ModuleNotFoundError: httpx` in a shipped install, and its child no longer writes `__pycache__` into the installed plugin.
- **The curated decision-index path was unreachable.** A new `arch_decision_index_mode` config key makes the curated mode selectable; the default stays "complete".

### Removed

- **Per-advance run bookkeeping and three promptbook surfaces (breaking).** `run-promptbook` no longer writes a log op or regenerates the promptbooks index per advance; a run's chronology comes from its snapshot timestamps. The `cycle-status` skill is deleted and its trigger phrases move to `visualize-run-progress`. The per-prompt `blocked_confirmed` flag is retired. `author-promptbook --fork-from` is deleted; change a plan mid-run by abandoning the run and authoring a successor book.
- **`crux/catalog/bundles.json`** is replaced by `crux/catalog/bundles.yml`, a mapping keyed by bundle id read through a loader that refuses anchors, aliases, merge keys, explicit tags, and a second document. The catalog ships inside the plugin, so no downstream repo owes a migration.

## [1.14.0] — 2026-08-18

### Added

- **An Elixir/Phoenix arch stack pack**, completing the batteries-included set (Python, Ruby, Node, Elixir). The interface surface comes from a committed OpenAPI spec, else a static parse of the Phoenix router (verbs, `resources` expansion, nested resources, `scope` prefixes, LiveView routes); the data model from Ecto schemas; the module graph from `alias`/`import`/`use` and remote calls resolving to in-repo modules.
- **A Node.js arch stack pack.** Routes from a committed OpenAPI spec, else a static scan of Express, Fastify, and NestJS; the data model from Prisma's `schema.prisma`, else TypeORM `@Entity` classes or Sequelize models; the module graph from explicit TS/JS imports with `tsconfig` alias resolution. All parsing is static and never boots Node.
- **A Ruby arch stack pack.** Routes from a committed `openapi.json`, else a static parse of `config/routes.rb`; the data model from `db/schema.rb`; the module graph from a resolve-or-drop pass over the Zeitwerk autoload roots. All parsing is static and stdlib-only.

## [1.13.0] — 2026-08-17

### Added

- **Pluggable arch stack packs, with a Python pack.** `derive-arch` detects the project's stack and resolves each spine file through a per-repo override, then the detected pack, then a stub. The Python pack derives the interface surface from a committed `openapi.json`, the data model from SQLAlchemy models plus Alembic history, and the module graph from the project's own package. Two `.bionic.yml` keys configure it: `arch_stack` pins the pack, and `arch_extractors` registers a per-repo override that runs only under `CRUX_ARCH_ALLOW_OVERRIDES=1`.

## [1.12.0] — 2026-08-17

### Added

- **`install-opencode-agents` skill** — the OpenCode counterpart to `install-codex-agents`. Say "install the Crux agents in OpenCode" to write the ten projected roles into that project's `.opencode/agent/`. It refuses to overwrite a locally modified role without `--force`, refuses a crux-managed entry that is a symlink, refuses a target outside the repo root, and never touches a project's own agent files. Skill count 49 → 50.
- **A shared OpenCode projection module** used by both the regenerator and the installer, so the two cannot disagree.
- **A `.gitignore` in the repository** covering the untracked `opencode/` tree the OpenCode setup generates.

### Changed

- **The skill runtime-compatibility contract now names OpenCode**, stating plugin-root resolution from the selected `SKILL.md` path, `.opencode/skill` for project-local skills, the lowercase tool labels, and `install-opencode-agents`.
- **OpenCode setup documents the singular `~/.config/opencode/agent/`** in the README and `OPENCODE_GUIDE.md`, noting that OpenCode reads the plural form too.

### Fixed

- **`install-codex-agents` and the Codex regenerator no longer turn a non-directory output path into a traceback.** Both refuse with a structured error at exit 2. The same guard applies to the OpenCode lane.
- **The OpenCode regenerator no longer writes through a symlinked agent file**, which could overwrite any file the running user can write.
- **`crux_wayfinder` was missing from the agent roster in all 46 skills**, and `install-codex-agents` still said it installed "nine" roles. Both now say ten.
- **The OpenCode setup gained an upgrade step:** regenerate the agent projection after every `git pull`, with troubleshooting rows for stale projections and dangling symlinks.
- **Stale paths and counts in `OPENCODE_GUIDE.md` and the repo-root `CLAUDE.md` corrected.**

## [1.11.0] — 2026-08-16

### Added

- **`derive-arch` skill** — the user-facing entry point to the `arch` concern. Say "build the arch", "summarize the current architecture", or "regenerate the architecture" to regenerate the derived current-state map (data model, interface surface, module graph, decision index, plus an overview). `arch` is now the primary current-state discovery surface, with a dedicated `arch` log op. Skill count 48 → 49.
- **Arch-spine coverage in `audit-docs`.** `CHK-ARCH-1` checks the derived spine for drift and regenerates it in place; `CHK-ARCH-2` recognizes arch enablement.

### Changed

- **`init-docs` enables the `arch` concern by default for new repositories**, scaffolding `arch/` with a placeholder; the first "build the arch" or `audit-docs` run derives the spine. Existing trees are unchanged (add `arch` to `concerns_enabled`, then derive).

### Fixed

- **An inline ADR reference in the `audit-docs` skill was replaced** with a citation of its schema section.

## [1.10.2] — 2026-08-14

### Fixed

- **Two stale skill counts** — the README's OpenCode setup and the USER_GUIDE's quickstart said 46 skills; both now say 48. The development repository gained a drift gate so a future added skill updates the prose or fails the release.

## [1.10.1] — 2026-08-14

### Fixed

- **Arch `module-graph.md` was incomplete.** The extractor matched only absolute imports; it now resolves relative imports, uses full-module node ids, and lists isolated modules.
- **Arch over-triggered drift on a manifest counter bump.** `_meta` now hashes only the key-name subset the data model renders, so a routine `next_number` bump no longer drifts arch.
- **README and skill doc drift:** skill count 46 → 48, agent count nine → ten, three missing skill rows (`prose-review`, `recover-decisions`, `transition-decision`), and `prose-review`'s description now says "seven writing rules".

## [1.10.0] — 2026-08-14

### Added

- **Decision recovery.** `recover-decisions` mines load-bearing decisions latent in code into `observed` candidates with `path:line-range` evidence in `arch/_recovered/state.yml`; `transition-decision` ratifies a candidate into a Proposed ADR, or rejects or defers it. `derive-arch.py` gains `decision_index_mode: complete|curated`. New `recover` log op.
- **ADR archival cold tier.** Superseded and Deprecated ADRs move to `adrs/archive/`, shrinking the active reading path while staying resolvable. `transition-adr` moves an ADR on Supersede/Deprecate; `propose-adr` scans both tiers so an archived id is never reissued; `generate-adr-index.py` regenerates the index's active table and `## Archived` roster; `audit-docs` gains `CHK-ADR-ARCHIVE`.
- **Writing rule #7 — footnote-only ADR citation.** In human-facing prose, reference an ADR by a footnote, never an inline number. A new AST-based check flags inline references while skipping code, link destinations, frontmatter, blockquotes, HTML, and footnote definitions, with a ratchet baseline.

## [1.9.0] — 2026-08-14

### Added

- **The arch concern.** `<docs_dir>/arch/` is the project's derived architecture — a deterministic spine (`data-model`, `api-surface`, `module-graph`, `decision-index`) plus a synthesized `overview.md`, regenerated by `derive-arch.py`. A SHA-256 hash-stamp over the spine gates the narrative: `derive-arch.py --dry-run` fails when the spine moved without a re-derive. Other stacks degrade to an empty-but-valid file.

## [1.8.2] — 2026-08-02

### Added

- **Prose-vs-template drift gates.** New tests pin the `init-docs` skill prose to the shipped templates and catch template comments that contradict their own parsed values.

### Changed

- **`init-docs` bootstraps the unified `bionic/` tree** at the resolved `docs_dir`, with concerns directly under the tree, the invariants concern as one folder, and `.bionic.yml` written with merge-never-clobber semantics. The guard refuses symlinked trees, and rollback removes only paths written this run.
- **Templates and schema docs describe the schema-5 world**, including `bionic/`, `schema_version 5`, and `.bionic.yml`. `install-docs-skills` fresh-install detection resolves the tree instead of testing for a literal `docs/`.

### Fixed

- **Fresh installs bootstrapped a `docs/` tree with schema_version "4" prose instead of the `bionic/` tree at "5".** The templates had moved but the `init-docs` skill prose still instructed the old split layout.
- **`docs_dir` in `.bionic.yml` rejects shell metacharacters.** A value like `$(id)` previously passed validation and executed when skills composed shell commands. Each segment must match `^[A-Za-z0-9_.][A-Za-z0-9._-]*$`.
- **The template-parity gate was silently dead since the tree move**; its canonical paths now resolve to `bionic/CLAUDE.md`.

## [1.8.1] — 2026-07-31

### Fixed

- **`migrate-tree.py` could not migrate a relocated tree, and would have damaged one if forced.** Source is now resolved through both config files, the destination is anchored at the true repo root, and a relocated tree stays where its owner put it. `--docs-dir` is the escape hatch for a layout no config declares.
- **The documented upgrade path was a closed loop.** The 4→5 migration rung is now documented with its invocation, both forms, exit codes, and fail-closed behaviors.
- **Containment on every path that reads, writes, or removes.** A `docs_dir` of `../elsewhere`, an absolute path, or a symlink escaping the repo is refused.
- **`CLAUDE.md.tmpl` shipped eight literal `<tree>` placeholders** that no render step substituted, and described tree relocation as "deferred".
- **`install-docs-skills` hardcoded schema `"3"`** while the plugin shipped `"5"`; it now reads the value from the installed manifest.
- **`audit-docs` was self-contradictory about the supported schema** and still called the invariants concern deferred.

## [1.8.0] — 2026-07-30

### Added

- **Breaking: the tree lives at `bionic/`, and the invariants concern is one folder.** The seven concerns sit directly under `bionic/`, and the invariants concern holds its ledger pages, a `checks/` subdirectory, and `reconciliation.yml` together. `init-docs` always writes `.bionic.yml` naming the tree. `schema_version` `"4"` → `"5"`.
- **Bare-directory discovery.** A tree is recognized by a manifest carrying both `schema_version` and `concerns_enabled`. Two valid trees refuse loudly unless a migration marker names one. An existing `docs/` tree keeps working with zero config and no migration.
- **`migrate-tree.py`, the 4 → 5 rung.** A staged, resumable merge: entries merge one at a time, `manifest.yml` moves last so a crash leaves discovery resolving to the source, and config is merged, never overwritten.
- **A schema gate.** Commands that read the tree refuse an unmigrated one with exit 2 and a message on stderr.
- **Six writing rules, and `prose-review` — the 46th skill — to check them.** One name per thing, no hedge without a cause, verbs stay verbs, adjectives must be checkable, one idea per sentence, single-word verbs; accuracy outranks all six. `prose-review` reports only findings that carry a rewrite.
- **`generate-writing-rules.py`** projects one canonical rules text byte-equivalently into `AGENTS.md`, the operational schema §16, and the shipped `prose-review` skill.

### Changed

- **`prose-review` is now mandatory** in the `dev-cycle` / `iterate` prep prompt and in `tend-garden` before it writes the morning note.

## [1.7.0] — 2026-07-24

### Added

- **Opus 5 refusal handling in the council seats.** A safety refusal (HTTP 200 with `stop_reason: "refusal"`) now surfaces as a new `ModelRefusedError` mapped to the redaction label `"refused"`, instead of a JSON-parse error or `unknown`.
- **Spawn caps on the delegating agent roles.** `commander` and `dev-lead` spawn one agent per genuinely independent unit, never for work finishable in about three tool calls, with a carve-out for independent review, per-threat-class security review, and two-architect ADR acceptance.
- **Scope-discipline guidance on `dev-lead`.**

### Changed

- **Model swap: `claude-opus-4-8` → `claude-opus-5`** across the router config and the OpenCode agent generator, at identical economics. `claude-opus-4-8` is removed from the router registry; the lineup is Fable 5 / Opus 5 / Sonnet 5 / Haiku 4.5. Router config `1.4.0` → `1.5.0`.
- **Agent and cycle-skill prose re-tuned for Opus 5's defaults.** Every cross-agent gate is preserved verbatim.

### Fixed

- **`dev-cycle` / `iterate` checklists overstated validator coverage.** The cycle-coverage pass checks module size and contiguity, not prompt content, and `cycle_grandfathered: true` short-circuits it entirely.

## [1.6.0] — 2026-07-23

### Added

- **`generate-lineage.py`** — a committed, deterministic regenerator for `adrs/lineage.md`, replacing `link-adr-graph`'s prose-only instructions.
- **`generate-index-rollup.py`** — the `## ADRs (N)` rollup of the docs index is now a pure function of ADR frontmatter, with a `--dry-run` drift gate.
- **`advance-run.py`** — a safe-advance script for promptbook run snapshots that round-trips every top-level key, so run-level `notes` / `pr_draft` / `summary` fields survive each advance.

### Changed

- **Council deliberation is hardened against a single errored provider seat.** Aggregation runs over responding seats only, requires a quorum of ≥ 2 (`NO_QUORUM` below it), surfaces `errored_seats` and a `degraded` flag, caps a degraded verdict at `EXECUTE_WITH_MONITORING`, and counts `APPROVE_WITH_NITS` as an approval.
- **Wiki-link resolution is lifecycle-agnostic.** An ADR's `related_research` resolves to any research page, and a journal→promptbook link resolves on its `PB-NNNN` id across `active/` or `archive/`.
- **Whiteboarding sessions carry into the brief body.** `propose-brief --from-inbox <session-path>` populates the brief from a whiteboarding session; `process-inbox` passes it for whiteboarding-classified items.
- **`CLN-TMPL-1` now guards section content**, not just section presence.
- **The `invariant` log op is a first-class member of the op enum**; `transition-invariant` emits it directly.

### Fixed

- A single errored council provider no longer poisons `consensus_confidence` or makes `UNANIMOUS_APPROVE` unreachable.
- Two growing wiki-link dangle classes retired: ADR `related_research` pointing at a synthesis page, and journal refs to archived promptbooks.
- The `whiteboarding → process-inbox → propose-brief` pipeline no longer orphans the session body behind an empty scaffold.
- **Provider exception text can no longer leak through council error paths.** Error redaction is now closed-vocabulary structured omission built from a fixed label set plus a range-validated status, so no substring of a provider exception reaches a persisted deliberation surface.

## [1.5.0] — 2026-07-18

### Added

- **The invariants concern — the seventh concern.** Pinned, ratified, executable statements of what must be true: a human-readable ledger plus a peer executable check suite, reconciled through a `.bionic.yml`-rooted manifest. **`recover-invariants`** mines code for `observed` candidate pins and never self-ratifies; **`transition-invariant`** is the human gate (`observed → ratified | rejected`, `ratified → retired`). `audit-docs` gains five `CHK-INV` rules. `manifest.yml` `schema_version` `"3"` → `"4"`.
- **`init-docs` enables the invariants concern by default for new repositories.** Existing trees adopt it via `audit-docs --migrate` (a new 3→4 rung); `init-docs` never upgrades a populated tree in place.
- **`.bionic.yml` — the repo-root layout source of truth.** A committed config (`config_version`, `docs_dir`, `artifact_prefix`) that supersedes the legacy `.crux` file, resolved as `.bionic.yml` > `.crux` > convention via the new `bionic-config.py` CLI (`crux-config.py` remains as a back-compat delegator).

### Changed

- **Forged-skill promotion is judgment-driven, not mechanically auto-nominated.** The former floor (≥2 `effective` evaluations on ≥2 dates) is now supporting evidence in the `retrospective` skill's owner-facing report.
- **`audit-docs` supports `schema_version` `"4"`** and its `--migrate` ladder gains the 3→4 rung.
- **Concern framing moved from six to seven** across the README, USER_GUIDE, and the operational schema.

## [1.4.2] — 2026-07-13

- No user-facing plugin changes; this release corrected the development repository's release-commit authorship.

## [1.4.1] — 2026-07-10

### Added

- **Public manual OpenCode setup instructions** in the README and a refreshed `OPENCODE_GUIDE.md`: clone to a stable path, `uv run generate-opencode-agents.py`, merge the absolute `crux/skills` path into `opencode.json`, glob-symlink all ten generated agents, restart the host, and verify with `opencode debug skill` / `opencode agent list`. There is no native Crux OpenCode marketplace package; this flow is manual and preview-grade.

### Fixed

- Corrected the README status/footer version and completed the 43-skill table.
- `OPENCODE_GUIDE.md` counts corrected (42→43 skills, nine→ten agents) and the agent symlink step made glob-based so it covers `wayfinder`.

## [1.4.0] — 2026-07-10

### Added

- **Codex becomes a supported distribution target and a fourth agent projection.** `.codex/agents/crux-*.toml` is generated from the same agent definitions by `generate-codex-agents.py`, never hand-edited, with a `--dry-run` drift gate. Install with `codex plugin marketplace add idyll/crux` → `codex plugin add crux@crux` (the repository has since moved to `bionic-coding/crux`). Target repos get the ten agents via the no-clobber, path-contained `install-codex-agents` skill, which refuses to overwrite or remove a differing role without `--force`. The `CRUX_PLUGIN_ROOT` bridge is stated correctly everywhere (Claude Code = `CLAUDE_PLUGIN_ROOT`; Codex = derived from the selected `SKILL.md` path; source checkout = `crux/`). A leaf-symlink write-through in the shared generator/installer write path was closed, and the generated Codex model mapping corrected (seven roles → `gpt-5.6-sol`, three → `gpt-5.6-terra`).
- **OpenCode agent generator test coverage**, including verbatim body/description passthrough.

### Changed

- **LLM router model refresh** (config 1.2.0 → 1.3.0). The OpenAI lineup consolidates into the three effort-controlled GPT-5.6 SKUs — Sol (flagship, alias `gpt-5.6`), Terra (balanced), Luna (cost) — all Responses-API-only; `gpt-image-1` → `gpt-image-2`. `claude-sonnet-4-6` → `claude-sonnet-5` (which rejects the temperature parameter). Anthropic pricing corrected against the official source. `fast_council`'s arbiter is pinned to low reasoning effort via `gpt-5.6-sol-low`.

### Fixed

- **OpenAI `/v1/responses` is first-class in the caller and the async council.** The async council's OpenAI seat no longer hardcodes `chat.completions.create`, and the caller now forwards `effort` → `reasoning.effort` on the responses path, so the `gpt-5.6-sol-low` pin is live.
- **Codex-integration review findings remediated.** `CRUX_PLUGIN_ROOT` framing corrected across the distributed template, the operational schema, and 13 skill bodies; strict-YAML tests skip cleanly without PyYAML; dated provenance comments anchor the external Codex schema assumptions.

## [1.3.3] — 2026-07-10

### Added

- **Self-detecting `CLN-TMPL-1` cleanup-campsite rule** checks dogfood↔template clause parity and is inert when no template twins are present. A general `cleanup-campsite --only <RULE-ID>` selector (CSV-capable) enables single-rule runs.
- **`dev-cycle` and `iterate` record forge-log `used`/`evaluated` entries for forged skills they invoke**, so cycle-used forged skills can reach the promotion floor.
- **`scout` — a read-only reconnaissance agent.** A primary delegates the consume-to-judge step to it: it reads large or external data in an isolated context, judges its fitness for a stated purpose, and returns a verdict plus a condensed digest. Tool grant `Read, Grep, Glob, WebFetch, WebSearch`, fenced by an egress guardrail. Catalog 9→10 agents. (Renamed `wayfinder` below.)
- **`run-adr-council` graduates into the plugin** (catalog 41→42, in the `crux-verification` bundle) — the first skill to traverse the forged-skill promotion path.
- **The forged-skill promotion path.** Forged skills are usable in the session that forged them, every session writes a session-end `evaluated` forge-log entry, `retrospective` gains a promotion scan, and graduation into the plugin is always a dev-cycle. Project-local skills cannot PR into the public repo; share via issue.
- **The night-gardener — the 9th agent — and her two skills.** A standing overnight co-CTO who reviews recent work and writes a morning note under `garden/`, moving only after you have moved. `tending.md` is your only control surface: dismiss or snooze. New skills **`tend-garden`** and **`read-news`** (Perplexity-backed reading with a Perplexity→WebSearch→skip fallback; `PERPLEXITY_API_KEY` optional via `~/.crux/`). Adds the `garden` log op. Catalog 39→41 skills. The nightly routine is owner-installed.
- **`retrospective`** — mines the operations log, journal, archived promptbooks, and forge log since the last `Retrospective:` marker into 0–2 evidence-cited skill proposals, gated through a fixed council rubric. New `cleanup-campsite` rule `CLN-RETRO-1` (`retro_due_runs` default 5). Catalog 38→39.
- **`forge-skill` — the capability-gap loop.** When a gap surfaces mid-task, the model diagnoses it, researches, authors or revises a project-local skill under `.claude/skills/<name>/`, and self-tests on the live problem. Every act lands in the append-only forge log (`.claude/skills/forge-log.md`), plus a new `skill` log op. New `cleanup-campsite` rule `CLN-FG-1` flags forged skills idle past `forged_skill_stale_days` (default 30).
- **PEP 723 + `uv run "${CLAUDE_PLUGIN_ROOT}/..."` runtime contract.** All 18 shipped runnable scripts carry inline-metadata blocks and are self-describing under `uv run`.
- **`check-public-release-content.py`** scans distributed surfaces for concrete internal ADR references.

### Changed

- **The reconnaissance agent is renamed `scout` → `wayfinder`**, one unified name across Claude Code and OpenCode, avoiding OpenCode's built-in `scout`. A pure rename.
- **The `architect` agent now holds `Bash`** so it can run the multi-model council driver its remit requires.
- **The dev-cycle quality gate mandates the full test suite**, required whenever a change touches a shared or enumerated surface.
- **LLM router curated to the latest-generation lineup; `claude-fable-5` disabled** (config 1.2.0→1.3.0). Older models removed; `gpt-5.5-pro` and `gemini-3.5-flash` added; the two Fable-pinned OpenCode agents remapped to `claude-opus-4-8`. (Superseded by the GPT-5.6 refresh in 1.4.0.)
- **The public repository is a generated artifact** receiving one squash commit and tag per release; releases attach no zip assets.
- **The abandoned "Crux Lite" framing is gone from every live surface.**
- **Marketplace-only install story:** `/plugin marketplace add idyll/crux` + `/plugin install crux@crux` across README, USER_GUIDE, templates, and skills.
- **`${PLUGIN_DIR}` → `${CLAUDE_PLUGIN_ROOT}`** as the canonical plugin-root variable.
- **Spawner runtime directory `.crux` → `.crux-runtime`**, with refusal-on-foreign-target and symlink/containment guards.
- **Provenance fields (`origin`/`origin_ref`/`origin_date`) removed** from the SKILL.md frontmatter contract and the catalog; git softened to enhancement-not-requirement.
- **Distributed `CLAUDE.md.tmpl` rebuilt** to the current contracts.

### Fixed

- **The public-release content scan is wired into the in-cycle quality-gate sequence** of all three cycle templates, so a banned internal reference fails in-cycle rather than only at release time.
- **Bare-`python3` test runs report clean skips instead of errors** when `uv` is absent. `check-no-stale-skill-names.py` gained kebab-boundary guards. README agent count corrected to 9.
- **The test suite is green after the router curation**, and `gpt-5.5-pro` gained the missing `openai_endpoint: "responses"` so it routes correctly.

### Removed

- **`spin` (the Python RSI engine) is decommissioned**, succeeded by `forge-skill`. Its modules, skill, and tests are deleted; `author-runbook` and spawner emissions teach the hypothesis discipline inline.
- **`crux/install.sh`** — the curl|bash install path is gone; the marketplace flow is the only install path.

## [0.9.0] — 2026-06-10

### Added

- **Honest YAML-capability failures and automatic uv repair.** `validate-promptbook.py`, `migrate-promptbooks.py`, and `visualize-run-progress.py` require a real YAML parser at entry: without PyYAML they re-exec under `uv run --no-project --with pyyaml>=6.0` (opt-out `CRUX_NO_UV_REEXEC=1`) or exit 2 with a remediation, never reporting an environment problem as a document verdict.
- **Repo-root `.crux` configuration file.** `docs_dir` relocates the docs tree and `artifact_prefix` brands promptbook/ADR ids (`CRX` → `CRX-PB-0040`; `RUN-NNN` never prefixed). Resolved via the `crux-config.py` CLI; `audit-docs` gains `CHK-CFG-1..4`. Zero-config repos are unaffected.
- **Two new skills — `transition-brief` and `cycle-status`** (catalog 36→38). `transition-brief` closes the briefs lifecycle (`draft → published | abandoned`), mutating only frontmatter and writing a `brief` log op. `cycle-status` is a read-only "where am I / what's next / how do I resume" surface over a run snapshot.
- **`iterate` skill** (catalog 35→36) — a cycle for non-architectural reactive work with a verify module in place of the ADR module (`cycle_kind: verify`). Routing: net-new/architectural → `dev-cycle`; reactive fix → `iterate`; trivial → `author-promptbook`.
- **Machine-enforced cycle-coverage validation.
