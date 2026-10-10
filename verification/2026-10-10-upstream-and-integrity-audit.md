# Upstream and integrity audit

Date 2026-10-10. Repository `/Users/desoleary/dev/omnitech-solutions/crux-flow`, branch `master`, working tree with the
orchestrator's uncommitted delegation-rule and technology-catalog work left intact. Nothing was committed, stashed, installed or
written outside the repository and the session scratchpad. Paths are repository-relative; `UP-old` is the installed upstream
3.25.1 cache (`~/.claude/plugins/cache/crux/crux/3.25.1`), `UP-new` is upstream tag v3.27.4 fetched read-only with
`gh api repos/bionic-coding/crux/tarball/v3.27.4` into the scratchpad.

Summary: the fork vendors upstream 3.25.1; the latest upstream is 3.27.4 (published 2026-10-10T00:04Z). I did not update. The
documented procedure (`upstream prepare`) cannot run in this checkout, and a trial shows four patched files would conflict. The
public contracts Flow calls (12 seam functions and entry points, the 12 flags it passes) did not change. What breaks is the patched shared writer, the run
schema and the council module.

---

## Part 1. Upstream

### 1a. What is vendored, what is latest

| Fact | Value | Source |
|---|---|---|
| Vendored upstream version | 3.25.1 | `reference-source.json` (`upstream_version`), `crux/catalog/flow-policy.json:11-12`, `crux/plugin.json` |
| Vendored upstream commit | `732c355a5bc3130f6cd3763214f7c908b302a3e8` | same |
| Provenance | "reconstructed-reference; uncommitted local reference; no synthetic Git history" | `reference-source.json` |
| Installed plugin cache | 3.23.1 and 3.25.1 (so the cache's latest is 3.25.1, equal to vendored) | `ls ~/.claude/plugins/cache/crux/crux/` |
| Latest upstream release | v3.27.4, 2026-10-10T00:04:01Z; tags v3.27.4, v3.27.2, v3.27.1, v3.26.2, v3.26.1 ... | `gh api .../releases/latest`, `.../tags` |
| Releases between | 3.26.0, 3.26.1, 3.26.2, 3.27.0, 3.27.1, 3.27.2, 3.27.3, 3.27.4 (eight) | `UP-new/CHANGELOG.md:16-163` |
| Is the vendored `crux/` plain 3.25.1 plus Flow? | Yes. 20 upstream-owned files differ from `UP-old` (listed in 1c); Flow adds its own files. `diff -rq UP-old crux/` | scratchpad diff |

The documented update procedure is `FLOW_GUIDE.md` "Repeatable upstream updates" (`crux-flow upstream prepare|verify`,
`crux/scripts/crux/flow/upstream.py:56-127`). `prepare` refuses unless (a) the checkout has `.git` (`upstream.py:59`), (b) its tree is clean
(`:60`), and (c) the pinned upstream commit is an ancestor of `HEAD` (`:63`). Today (a) holds, (b) fails (uncommitted work) and (c)
fails (`git merge-base --is-ancestor 732c355a... HEAD` does not hold: the fork history is `6213dc2 ...`, with no upstream objects).
`crux-flow upstream check` now reports exactly these blockers offline.

**Decision: STOP at the audit.** Running the procedure is not safe or possible here. The exact procedure, once the owner has a
checkout with real ancestry, is in section 1f.

### 1b. Upstream diff 3.25.1 -> 3.27.4 (296 differing paths; tests excluded below)

Skills (55 in both; none added, removed or renamed):

| Change | Skills | Contract effect |
|---|---|---|
| Frontmatter changed | `council` (description now "three-provider council ... council runner writes the council record that is the gate's evidence"), `dev-cycle` (adds "replaceable implementation choice"; `cycle_kind` implementation), `run-adr-council` (description and routing note: council record is the gate evidence) | Trigger and description text moved; surface `skills/<name>/contract` digest changes |
| Body changed, frontmatter same (26) | `archive-promptbook, audit-docs, author-promptbook, backfill-signoff, check-drift, cleanup-campsite, compile-doctrine, derive-arch, init-docs, iterate, log-work, patch-cycle, propose-adr, propose-observation, query-docs, reconcile-signoff, recover-decisions, retrospective, review-decisions, run-promptbook, srde, survey-sheet, survey-signoff, tend-garden, transition-adr` (plus `dev-cycle` body) | Mostly new calls: see next table |
| Scripts called, newly | `council`, `dev-cycle`, `iterate`, `patch-cycle`, `run-adr-council` call `run-council.py`, `run-work-witness.py`; `archive-promptbook, cleanup-campsite, query-docs, recover-decisions, review-decisions` call `authority-view.py`; `query-docs, review-decisions` call `implementation-decisions.py`; `audit-docs, council, run-promptbook` call `advance-run.py`; `dev-cycle, iterate, patch-cycle, propose-observation, author-promptbook` call `compile-doctrine.py, summarize-adrs.py` | None of these skills or scripts is called by Flow itself; Flow's `flow` skill calls `run-promptbook` conventions only |

Scripts (top level `scripts/*.py`: 74 -> 105):

| Script | Change | Flow relevance |
|---|---|---|
| `advance-run.py` | 500 -> 1245 lines in `main`; adds `--gate-info`, `--prompt`, `--implementation-revision`, `--migration-batch` (`UP-new/scripts/advance-run.py:1259-1265`); every `--outcome` now needs a resolvable book whose hash matches `book_content_hash`, classifies the prompt by position (`council_gate.classify`) and admits council and module-close prompts only on committed `run-council.py` records (`:58-98`). Signatures of `abandon`, `_check_base_commit_pin`, `advance`, `_splice`, `main` identical (AST check) | The fork's patched copy conflicts (1d) |
| `start-run.py` | new: "create one validated run snapshot" with layout `runs/<book>/run-RUN-NNN.yaml` | Flow writes runs itself; no conflict, but a second author of run snapshots now exists |
| `run-council.py` + `council_*.py` (13 modules) | new council runner: attempt record committed before any request, result record committed and verified, gate reads committed history, `--recover` | Flow's `run council` uses `AsyncCouncil` directly; its output is not a gate record. Only matters for cycle books |
| `write-review-report.py`, `run-work-witness.py`, `authority-view.py`, `implementation-*.py`, `migration_disposition.py`, `retained_evidence.py`, `git_read_cache.py`, `secret_scan.py`, `admission_source_io.py`, `observation_admission.py`, `changelog_reference.py`, `check-refutation-record.py` | new | Not called by Flow |
| `bionic_config.py` | refactor: `parse_config_text`, `is_crux_manifest_text`, `_source_io` parameter; `SUPPORTED_SCHEMA_VERSION` still "5" (`:725`); `load_config` and `BionicConfigError` still present | Compatible; symbols Flow imports are pinned |
| `crux/core/llm_caller.py`, `crux/council/async_council.py`, `crux/core/data_classes.py` | changed | `async_council.py` conflicts with the fork patch (1d) |
| `validate-promptbook.py`, `check-promptbook-index.py` | changed; `validate_file`, `frozen_plan_subset`, `compute_book_hash`, `book_stems` keep their argument names | Compatible |
| `validate-catalog.py`, `generate-runtime-compat.py`, `generate-routing-table.py` | unchanged CLI (`--dry-run`, `--repo-root`) | Compatible; the fork's patch to `validate-catalog.py` applies cleanly |

Schemas:

| File | Change | Flow relevance |
|---|---|---|
| `run.schema.json` | `format_version` const "1" -> enum "1","2"; commit pattern accepts SHA-256 (64 hex); adds `implementation_bindings` (225 lines) | Surface invariant `schemas` fails: Flow reads run format "1" only. The fork's `flow` block patch conflicts |
| `promptbook.schema.json` | `format_version` "1" or "2"; adds module kind `implementation`; relaxes minimums (0) | Same invariant for promptbooks |
| New | `council-record`, `council-attempt`, `council-policy-v2`, `reviewer-report(-format-2)`, `implementation-*`, `owner-exception`, `refutation-record`, `run-work-witness` | Unused by Flow |
| `docs_dir` / manifest | `docs_dir` unchanged; manifest `schema_version` still 5 | Compatible |

Roles (`agents/*.md`; no role added or removed; 10 in both):

| Role | Frontmatter change | Effect on Flow |
|---|---|---|
| `librarian` | `tools: Read, Grep, Glob, Skill` -> `Read, Grep, Glob, Bash, Skill` | Flow derives tools from upstream, so every generated librarian gains Bash on Claude, OMP and OpenCode (surface notice `roles/librarian/*/tools/Bash`). Intended upstream (read-only retrieval); Flow's role text should say so |
| `architect, brainstormer, dev-lead` | `model: opus` -> `claude-opus-5-5`; `maxTurns` 75/150 -> 100/250 | Flow overrides `model` from its own assignment; `maxTurns` passes through (Claude, OpenCode `steps`) |
| `commander, developer, historian, reviewer` | `maxTurns` raised (200->250, 150->200, 75->100, 150->200) | Pass-through |
| Delegation tool lists | unchanged on all ten | No new delegation edge |

Catalogs and data: `models.yml` (Opus 5.5 pinned by full id; `opus-stable` removed; `haiku-latest` -> Haiku 5.5; architect and commander
OpenCode models changed), `llm_router_config.json` 4.0.0 -> 4.2.0 (adds `accepted_served_models`, `accepted_served_providers`).
Flow keeps `flow-bindings.json` empty (`{"native": {}, "api": {}}`), so these identities flow straight into Flow's roles and API
calls. A run freezes `base_registry_digest` of the router file (`policy.py:187-218`), so runs started before an update refuse
council or API calls after it ("API router changed since the run policy was frozen").

Drift roster: the set of `.py` gates named in `check-drift/SKILL.md` is unchanged. Hooks: none in either version (no `hooks/` directory,
no `hooks` key in any `plugin.json`). Plugin manifests: `.claude-plugin/plugin.json` and `.codex-plugin/plugin.json` change version only.

Behavioural changes that matter to any consumer: a tree that published a clause migration needs full Git history (3.26.0); a Codex
workspace-write session cannot start a council without escalation (3.26.0, corrected 3.26.2); council gate evidence is ordered by
committed history (3.26.2); the librarian can read through the shell (3.27.3/3.27.4).

### 1c. Every place Flow reaches into upstream that is not an official seam

Derived from `diff -rq UP-old crux/` (20 patched upstream-owned files) plus a read of `crux/scripts/crux/flow/*.py`. Risk is for
the next re-vendor. The authoritative list is `crux/surface/declaration.json`; the record stores a digest of each patched file.

| # | What | Where | Kind | Risk | Result against 3.27.4 |
|---|---|---|---|---|---|
| N1 | Shared writer calls `records.guard_advance` and `advance_file` when a run has a `flow` key; plus patch-completion preflight, finite-number guard, `("flow",)` splice special case | `crux/scripts/advance-run.py:137-143, 192-194, 435-437, 473-507, 551-564, 603-616` (7 hunks, +77/-1) | monkey patch of upstream-owned code | Highest: this is the integrity gate | CONFLICT (file grew to 1245 `main` lines) |
| N2 | `flow` property in the run schema (two hunks, +489/-26) | `crux/schemas/run.schema.json` | patched schema | High | CONFLICT |
| N3 | Bounded council: overall deadline, frozen router digest, `resolved_models` (8 hunks, +56/-7) | `crux/scripts/crux/council/async_council.py` | patched | High | CONFLICT |
| N4 | `ModelConfig`, `CONFIG_PATH` exports for the above | `crux/scripts/crux/core/llm_caller.py`, `core/__init__.py` | patched | Medium | applies cleanly |
| N5 | Owned Swift parser deadline | `crux/scripts/crux/arch/packs/swift.py` | patched | Low | applies cleanly |
| N6 | Authored-catalog registry accepts `flow-*.json` | `crux/scripts/validate-catalog.py:1707-1736` | patched | Medium | applies cleanly |
| N7 | Role-named Anthropic helpers | `crux/scripts/codex_agents.py` | patched | Medium | applies cleanly |
| N8 | Catalog data edited | `crux/catalog/models.yml`, `bundles.yml`, `skills.json` (generated) | patched data | Medium | applies; `skills.json` must be regenerated |
| N9 | Eight skills edited | `archive-promptbook, call-llm, check-drift, dev-cycle, fix-directly, iterate, patch-cycle, run-promptbook` `SKILL.md`; `run-promptbook/references/advance.md` | patched text | Medium | all eight `SKILL.md` apply; `advance.md` CONFLICTS |
| N10 | Upstream scripts executed in-process; private functions called | `crux/scripts/crux/flow/upstream_api.py:13-24` (`exec_module` of `advance-run.py`, `validate-promptbook.py`, `check-promptbook-index.py`); `records.py:105, 241, 292`, `history.py:46`, `upstream_api.py:32-39` call `_check_base_commit_pin`, `advance`, `abandon`, `_splice` | private-API reach | Medium | argument names identical in 3.27.4 |
| N11 | Skill text rewritten on export: `/crux:` -> `/crux-flow:`; runtime-compat block removed; routing preamble prepended | `crux/scripts/crux/flow/hosts.py:127, 128, 140` | text rewriting | Medium: depends on upstream's phrasing | markers still found |
| N12 | Role bodies replaced; `commander` dropped; `Agent(x)` renamed `crux-flow-x` | `hosts.py:64, 68-70, 84` | rewriting | Medium | upstream delegation lists unchanged |
| N13 | Direct imports of upstream modules and their names: `bionic_config.load_config`, `BionicConfigError`; `record_numbers.scan_records`; `models_catalog.load/ModelsCatalog`; `codex_agents.parse_source/render_agent/_toml_basic_string`; `opencode_agents.transform`; `crux.core.llm_caller.ModelConfig/call_gateway`; `crux.council.async_council.AsyncCouncil/AsyncCouncilConfig` | `crux/scripts/crux/flow/*.py` | undocumented module API (one private name, `_toml_basic_string`) | Low to medium | all present in 3.27.4 (checked by the `imports` section) |
| N14 | Layout assumption: `crux/scripts` on `sys.path` and a plugin root with `scripts/`, `catalog/`, `agents/`, `skills/`, `schemas/` | `pyproject.toml` `pythonpath`, `packaging.py`, every `plugin/'scripts'/...` read | path assumption | Low | unchanged |
| N15 | Run files are read and spliced by upstream's text writer, so Flow depends on the run YAML layout (`prompts`, `current_prompt`, `status`) | `records.py`, `upstream_api.splice` | format | Medium | `format_version` 2 exists; Flow writes 1 |

### 1d. What the update means for Flow

| Change | Verdict |
|---|---|
| Public CLI of the six upstream scripts Flow calls, the 12 seam functions, the 7 imported modules and their 13 symbols | Compatible as is |
| 55 skills: none removed or renamed; 3 contracts moved | Compatible; 3 notices (`council`, `dev-cycle`, `run-adr-council` contract digests) |
| Roles: delegation edges unchanged | Compatible. Librarian gains Bash: needs a Flow decision (owner) |
| `models.yml`, `llm_router_config.json` | Needs a Flow change: re-state `flow-roles.json` model expectations; runs in flight keep the old router digest and will refuse council and API calls |
| `advance-run.py` rewritten | Breaks an assumption: `scenarios/original-writer-cannot-bypass-the-gate.json` and `test_records.py::test_original_writer_cannot_skip_check_or_complete_with_missing_review` fail until the Flow hook is re-applied inside the new `main` (before the gate classification) by hand |
| `run.schema.json` `format_version` 1\|2 | Breaks the surface invariant `run format_version ['2'] is not one Flow reads` by design; Flow must either read format 2 or refuse it explicitly |
| `async_council.py` | Needs a Flow change: re-apply the deadline and frozen-model patch |
| New council gate (`run-council.py`) for cycle books | Undoes nothing Flow relies on, because Flow runs are non-cycle. It does make `run council` and upstream's council two different things; the README says so |
| Upstream mode | Upstream mode keeps upstream's roles; with 3.27.4 it would inherit the new `maxTurns` and Opus 5.5, as intended |

A trial on a scratch copy of 3.27.4 applied the fork's per-file diffs with `patch --dry-run`: 16 of 20 apply, 4 conflict (`advance-run.py`,
`run.schema.json`, `async_council.py`, `run-promptbook/references/advance.md`). I did not run Flow's tests against that hybrid, because
the three conflicting files would have had to be hand-merged, which is the step the owner's procedure reserves for a candidate branch.

### 1e. Why I did not update

`prepare` cannot start (no ancestry, dirty tree). Hand-vendoring 3.27.4 would mean hand-resolving N1 to N3, which are the integrity
core, without upstream's review of the merge. Offline gates are therefore not enough: it needs the owner's decision on format 2 and on
the librarian (section 5).

### 1f. The exact procedure, once ancestry exists

1. Commit or stash the current work on a branch rooted at the commit that carries upstream 3.25.1 (`732c355...`); `crux-flow upstream check` must show `procedure.ready: true`.
2. `crux-flow --repo "$PWD" upstream prepare --ref v3.27.4 --output /tmp/crux-next`. Expect `status: conflict` (N1 to N3).
3. In `/tmp/crux-next`, resolve the four conflicts: re-insert the `flow` hook (N1) before gate classification in the new `main`, keep upstream's `format_version` enum and add the `flow` block (N2), re-apply the deadline and frozen-model changes (N3), keep the one-line change in `advance.md`. Add `"2"` to `supported_formats.run` and `promptbook` in `crux/surface/declaration.json` only if Flow is taught to read them.
4. Run `crux/scripts/generate-flow-surface.py`: it must refuse while any hard invariant fails; fix until it writes; read the notice list (librarian Bash, three skill contracts, patched-file digests).
5. `crux-flow upstream verify --candidate /tmp/crux-next --full`, then `pnpm run verify:full` there.
6. Only then fast-forward the branch and `pnpm run refresh`.

---

## Part 2. Tripwires

Implemented (Flow-owned code only, no network in the suite):

| Piece | File | Notes |
|---|---|---|
| Declaration | `crux/surface/declaration.json` | What Flow requires (script flags it passes), seam functions and argument names, supported formats, Flow skills, delegation allow-list, which sections are hard on removal, patched upstream files, rewritten text and markers |
| Surface module | `crux/scripts/crux/flow/surface.py` | `collect`, `invariants`, `changes`, `check`, `sync`. Reads upstream scripts as syntax trees (nothing is imported or executed), introspects the Flow argparse tree, renders roles through `hosts.roles` for 4 hosts x 4 modes |
| Record | `crux/surface/record.json` | One generated, committed JSON (sorted keys, 1-space indent) |
| Regenerator | `crux/scripts/generate-flow-surface.py` | Sibling contract: `--dry-run` exits 1 with JSON on drift, writes nothing; never merges; `surface_absent` outside the authoring checkout; refuses to write while BROKEN |
| Roster row | `crux/skills/check-drift/SKILL.md` (table row and explanatory note) | Scope `plugin-authoring`; the record's `drift_roster` section pins the row so removing it is a hard failure |
| Test | `tests/flow/test_surface.py` (22 tests) | Fails with a readable change list when the surface differs from the record |
| CLI | `crux-flow upstream check [--fetch]`; `upstream prepare` now regenerates the record last; `upstream verify` runs the surface check | `upstream.py::inspect`, `readiness`; `cli.py`; `testing.py` |

Hard failures (BROKEN: regenerating cannot repair; the regenerator and `upstream check` exit 1, `pnpm run verify` fails):

- A script Flow calls is gone, or no longer accepts a flag Flow passes (`requires`).
- A seam function is gone or its leading argument names changed (`dynamic_seams`).
- An upstream symbol Flow imports is gone (`imports`).
- A role on Claude, OpenCode or OMP in a Flow mode gains a delegation target it is not allowed (`delegation`).
- A run, promptbook, manifest or Flow-extension format appears that Flow does not read (`supported_formats`).
- A Flow skill is missing; the roster lost the surface row; text Flow rewrites is no longer found.

Notices (DRIFT: the test fails until the record is regenerated, which forces a reviewed commit): an added command or flag; a changed skill
contract or the scripts it calls; a role losing a tool or gaining a non-delegation tool; a moved upstream version; a changed patched-file
digest; new roster rows. Within DRIFT the change list marks a removed command, upstream script, import, roster row or skill as `hard`.

Specified, not built: `crux-flow upstream update`. It would run `prepare`, `verify` and the regeneration and stop on a hard failure.
`prepare` already regenerates the record last (`upstream.py:86`) and `verify` already runs the surface check, so the verb is a three-line
chain; I left it out because its only meaningful test needs a checkout with ancestry. Add it with the first real update.

Considered and not built: a models digest section. `models.yml` is a patched file, so its digest is already in the record; the router
config is not. Add `scripts/crux/_config/llm_router_config.json` to `patched_upstream_files` when the owner decides how Flow should
treat router-version moves.

---

## Part 3. Proof that it does what it says

### Claim to proof map

P = proven by a deterministic test. S = proven by a scenario added in this audit. U = unproven. L = observed live, probabilistic.

| # | Claim | Where claimed | Proof | Status |
|---|---|---|---|---|
| 1 | Default mode is aggressive; `rapid` is an alias; precedence invocation > project > personal > shipped | `FLOW_GUIDE.md` Modes | `test_foundations.py::test_default_precedence_and_project_isolation` | P |
| 2 | Mode caps (20/60/120 min; delegates 0/2/4; reviewers 1/2/3; repairs 1/2/3; council 0/1/2) exist as data | README, guide | `test_foundations.py::test_modes_are_real_and_preserve_requested_caps`, `test_completion.py::test_shipped_mode_data_has_a_strict_schema` | P |
| 3 | Delegate cap is enforced (0 in aggressive, 2 in balanced) | skill, guide | `scenarios/aggressive-has-no-delegate-budget.json`, `balanced-delegate-budget-is-two.json` | S |
| 4 | Reviewer, repair and council caps are enforced the same way | skill | same code path (`records.py:12, 143-151`); only `delegate` and the council justification are exercised | partly P (`test_completion.py::test_council_attempt_requires_a_material_justification`); reviewer and repair caps U |
| 5 | Invalid configuration never falls back silently | guide | `test_foundations.py::test_invalid_policy_never_silently_falls_back` | P |
| 6 | A run freezes its policy; later mode changes do not alter it | skill | `scenarios/a-run-keeps-the-mode-it-started-with.json`; `test_records.py::test_frozen_book_and_source_not_reinterpreted` | S/P |
| 7 | A run is accepted only after implementation, a fresh passing check and an independent review | README | `scenarios/accepted-run.json`, `review-cannot-be-skipped.json`; `test_records.py::test_review_attestation_is_explicit_and_final_acceptance_consistent` | S/P |
| 8 | Upstream's own writer cannot bypass the gate | guide | `scenarios/original-writer-cannot-bypass-the-gate.json`; `test_records.py::test_original_writer_cannot_skip_check_or_complete_with_missing_review` | S/P |
| 9 | Checks run the frozen argv with a receipt (fingerprints, exit code, digests); a different argv is refused | skill | `test_records.py::test_command_must_match_frozen_acceptance_identity`, `test_real_check_execution_cannot_be_replaced_by_marking_label`; `scenarios/accepted-run.json` | P/S |
| 10 | A failing check exits nonzero and blocks advance | guide | `scenarios/failed-check-blocks-acceptance.json`; `test_completion.py::test_failed_owned_check_returns_nonzero_cli_status` | S/P |
| 11 | Editing an input after a check makes the receipt stale | skill | `scenarios/edit-after-check-makes-evidence-stale.json`; `test_records.py::test_failure_and_changed_consumed_docs_never_become_fresh` | S/P |
| 12 | The run's own bookkeeping does not invalidate its checks | guide | `test_records.py::test_checkpoint_bookkeeping_does_not_invalidate_own_check` | P |
| 13 | The state machine holds under any action order (refusals change nothing; units never revert; completion needs a fresh check and independent review) | new | `test_run_properties.py` (12 seeded walks; non-vacuous) | P |
| 14 | Review is recorded as an external attestation, not an authenticated identity | skill, README | `test_records.py` asserts `external-attestation`; no authentication exists | P (limit stated) |
| 15 | Budget exhaustion is honest; owner can extend without resetting the clock | guide | `test_records.py::test_budget_exhaustion_is_honest_and_owner_can_extend_without_clock_reset`, `test_explicit_mode_transition_retains_start_and_whole_run_attempts` | P |
| 16 | `run drive` owns consecutive host turns until acceptance and blocks a stalled run | guide | `test_drive_and_identity.py` | P (new; was U) |
| 17 | `run resume` returns the frozen instruction | guide | `test_product_commands.py::test_cli_run_resume_can_execute_frozen_project_recipe` | P |
| 18 | A replacement run has a single path to acceptance | skill | `test_records.py::test_replacement_contract_has_single_path_acceptance` | P |
| 19 | Role agents are generated per host without `commander`; unsupported controls are reported | guide | `test_hosts.py::test_each_host_projects_resolved_roles_without_commander`, `test_unsupported_controls_are_disclosed` | P |
| 20 | Claude delegation targets match projected role names | guide | `test_completion.py::test_claude_delegation_targets_match_projected_role_names` | P |
| 21 | One orchestrator, every delegate a leaf: no unexpected delegation tool on any role | skill, README | `test_surface.py::test_a_role_that_gains_a_delegation_tool_is_broken_on_every_host` | P for the tool list; U for a model obeying the text (see live) |
| 22 | Upstream mode is byte-identical to upstream | README | Codex: `test_hosts.py::test_upstream_codex_renderer_defaults_are_preserved`. Claude and OpenCode: `test_drive_and_identity.py::test_upstream_mode_keeps_upstream_role_bodies_and_tools_on_claude_and_opencode` (bodies and tools equal; names, models and `Agent(...)` targets carry the `crux-flow-` prefix). OMP: not compared | P for Codex, Claude, OpenCode; U for OMP; "byte-identical" is exact only for Codex |
| 23 | Technology router: detection, drift/BROKEN verdicts, notes preserved, preload per host, no section means unchanged roles | guide | `test_technology.py` (64 tests) | P |
| 24 | The router is actually loaded by a model | guide | `verification/2026-10-10-technology-guidance-live-evidence.md` (Claude sonnet 1/5 without the routing line, 5/5 with; delegated role 3/3; Codex 3/3) | L |
| 25 | Managed install writes only owned files, no-ops when current, rolls back, refuses foreign edits | guide | `test_foundations.py` (managed), `test_lifecycle.py`, `test_init.py`, `test_models.py::test_rollback_refuses_user_edits` | P (fake hosts) |
| 26 | `init` activates Flow per repository through the host's own setting while upstream stays installed | README | `test_init.py::test_claude_activation_uses_project_settings`, `test_codex_activation_is_per_repository...`; real hosts `test_native.py` (opt-in) | P (fake), real hosts opt-in; Desktop load U |
| 27 | Packaging is reproducible; tampering and zip escapes are refused | guide | `test_packaging.py` | P |
| 28 | Publishing is atomic and idempotent, credentials never echoed | guide | `test_publish.py` | P |
| 29 | Model refresh/apply/rollback is transactional and refuses stale proposals | guide | `test_models.py` | P |
| 30 | Upstream update replays fork commits and refuses a conflicting release without touching the fork | guide | `test_upstream.py` (fixture history only) | P on a fixture; never exercised on a real release; real procedure U |
| 31 | The surface record matches the live surface; hard invariants fail | new | `test_surface.py` | P |
| 32 | A model, given the skill, follows the gate | skill | `test_behaviour_live.py` | built; no rate yet (free tier exhausted today) |
| 33 | Skill selection by trigger description | skill triggers | no deterministic proof is possible; technology live evidence covers the router only | U |
| 34 | Native sub-agent delegation is counted | skill | stated as not counted ("cooperative") | limit, not a claim |
| 35 | OpenCode and OMP projections work in live sessions | guide | fake host processes only | U (stated) |

### Built

1. Deterministic scenario runner (preferred option 1): `tests/flow/test_scenarios.py` plus eight JSON scenarios in
   `tests/flow/scenarios/`. It runs the real CLI (`python -m crux.flow`) and upstream's `advance-run.py` as subprocesses in a temporary repository,
   plays the agent by writing the files and issuing the commands, and asserts on exit codes, stdout JSON, the run record and byte-identity after refusals.
   ~10 s, free. All eight pass.
2. A property test over the run state machine: `tests/flow/test_run_properties.py`, 12 seeded random walks of up to 40 actions, with three invariants and a
   non-vacuity check (some walks reach acceptance). ~70 s.
3. `run drive` with a scripted host executable: `tests/flow/test_drive_and_identity.py`; also upstream-mode role identity for Claude and OpenCode.
4. Live, optional (option 2): `tests/flow/test_behaviour_live.py`, gated by `CRUX_FLOW_PROVIDER_TESTS=1` and `CRUX_FLOW_LIVE_OPENROUTER=1`. Only
   `OPENROUTER_API_KEY` is needed, read from the environment and sent only as a header. It asks `GET /api/v1/models` for models and keeps the `:free`
   variants with zero prompt and completion prices (no list is hard-coded). A turn is one situation with three options and "answer with a letter"; the
   system prompt is only the skill's two relevant sections (about 4,200 characters). Five cases (failed check, read-only question, aggressive delegates,
   delegate is a leaf, self-review is not independent), 3 runs each, default 1 model: 15 requests, within the free tier's 50 a day. Result per model and case:
   `followed / other / unavailable` and a rate; rate-limited, refused or errored requests count as `unavailable`, never as a result; a daily-limit response
   stops the run. `CRUX_FLOW_LIVE_FLOOR` can turn a rate into a failure; the default only reports.
   What each case proves: that the model, given the written rule, chooses the compliant action. Cost zero. Flakiness: high for small free models (temperature 0 but
   uneven serving); positions of the correct option are rotated to remove letter bias. Not proven by it: the machinery (scenarios do that), host skill
   loading (the paid router evidence does that), or behaviour across a long session.

### What I ran

- Deterministic: scenarios (8 passed), properties (13 passed), drive and identity (3 passed), surface (22 passed), then the full check below.
- Live (free models, `zsh -ic`): three runs of the probe. The first listed models by price only and hit `403 Key limit exceeded ($0.00 of $0.00)` for a non-`:free` model
  and `429 free-models-per-day (50)` for the `:free` ones; the harness was changed to select `:free` ids and to stop and report on the daily limit. Final run:
  1 request, `daily_limit_reached: true`, no rate. Evidence JSON is under `.cache/live-behaviour/` (ignored by Git). No key was printed or read by me; no paid call was made.
  The key's $0 cap and the 50-request daily cap are account facts the owner should know; adding 10 credits lifts the free-model cap to 1,000 a day.

### Other mechanisms considered

- Recorded transcripts replayed as fixtures: worthwhile once a live host session has produced a real transcript; the scenario format is already the right shape (a list of commands and assertions). Not built because it needs a real transcript.
- Mutation testing of the gate (delete one check from `guard_advance`, expect a scenario to fail): cheap and valuable; I verified refusal reasons by hand (`required check has no current successful execution`) but did not automate it.

---

## Part 4. Documentation

Rewritten: `README.md` (principles with enforcing files, capability matrix, modes table, walkthrough with real output shapes, seams table,
tripwire, proof commands, honest limits); `FLOW_SEAMS.md` (official and non-official seam tables with pins); `FLOW_GUIDE.md` ("Repeatable upstream updates"
and the surface record, a pointer to the new proof tests). `CODEX.md` is accurate and short; unchanged. `USER_GUIDE.md`, `OPENCODE.md` and `CHANGELOG.md` are upstream
reference copies and are labeled as such in the README rather than rewritten, so a future re-vendor does not conflict with them.

---

## 5. Decisions that are the owner's

1. Where the 3.27.4 update happens: a branch rooted at the upstream 3.25.1 commit (restores `prepare`), or a one-off hand-merge in this checkout. This audit recommends the former.
2. Whether Flow reads run and promptbook format 2 or refuses it explicitly (the surface invariant forces the question).
3. Whether the Flow librarian should gain Bash with upstream, or Flow removes it from its projection.
4. Whether in-flight runs should survive a router-config bump (today they refuse council and API calls).
5. Whether to add credits to the OpenRouter account (live probe needs more than 50 requests a day to give a rate) or run the probe through the existing paid Claude path.
6. Whether `COMPLETION_REPORT.md` (the 0.2.0 build record, stale numbers) is kept, merged into `verification/README.md`, or deleted.
7. Whether to add the verb `crux-flow upstream update` now or with the first real update.
