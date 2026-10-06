# Crux Flow 0.2.0 — completion report

## Delivered result

A complete standalone source checkout and a deterministic installable release,
continuing the recovered assistant-authored reference implementation. The source
archive needs neither the earlier Codex branch nor a patch application to run.
All known failures reproduced during this completion pass are repaired. Available
offline checks pass; native-host, Desktop, provider and complete optional-parser
acceptance remain explicitly unobserved.

The reference architecture is preserved. The canonical package is
`crux/scripts/crux/flow/`; the obsolete `crux/flow/` implementation is absent.
Existing Crux multi-prompt books and the preserving writer remain the sole progress
system. There is no new daemon, database, gateway or alternate runtime store.

The fork contains 26 Python modules in the Flow package
(3464 lines), including one small new `__main__.py` entrypoint.
Of the 25 recovered modules, **11 remain byte-identical**;
14 received focused repairs. Existing tests were retained;
completion coverage now spans 10 test files. Counts describe
this implementation, not a performance or quality guarantee.

## What was preserved

Policy-driven modes, finite budgets, primary-session execution, Crux document/record
numbering, native prompt progression, shared model ownership, scoped host generation,
owned subprocess execution and durable transaction recovery were retained rather
than rewritten. The original fork's patch containment, bounded council, role-named
Anthropic helper and owned Swift parsing fixes remain in their owning components.
The latter requires optional parser dependencies for full verification.

## Repairs completed

| Boundary | Repair and observable outcome | Regression evidence |
|---|---|---|
| Frozen policy | Normalize the effective snapshot to JSON-compatible data before preserving YAML writes. Mode transitions retain start time and attempt counts. | `tests/flow/test_records.py::test_explicit_mode_transition_retains_start_and_whole_run_attempts` |
| Source launchers | Executable root launchers, module entrypoint, declared runtime dependencies and matching pnpm scripts. | `test_completion.py::test_source_checkout_has_executable_entrypoints`, `test_package_launcher_resolves_declared_dependencies` |
| Public recovery | Add inspect/recover/rollback CLI access to existing private transactions, excluding backup bytes from public reports. | `test_completion.py::test_recovery_cli_restores_a_prepared_transaction` |
| Model rollback | Read the existing receipt's actual status field; preserve rollback checks and user edits. Installed payloads reject source-scope mutation. | `test_completion.py::test_model_rollback_cli_uses_transaction_status`, `test_model_source_scope_cannot_mutate_an_installed_payload` |
| OMP rollback | Restore only managed alias fields while preserving unrelated current YAML settings. | `test_completion.py::test_installation_rollback_merges_only_owned_omp_aliases` |
| Native rollback | On failure, compensate to the previously active registration and verify the resulting state rather than claiming success. | `test_completion.py::test_native_rollback_failure_restores_current_registration` |
| Approval boundaries | Revalidate uninstall, materialization and setup inputs after confirmation and before side effects. | `test_completion.py::test_uninstall_refuses_an_approved_plan_that_changed`, `test_materialization_refuses_changed_preferences_after_confirmation`, `test_setup_stale_launcher_refuses_before_any_host_install` |
| Failed checks | A failed executed check/unfinished driver returns a nonzero CLI result, not a successful command status. | `test_completion.py::test_failed_owned_check_returns_nonzero_cli_status` |
| Run ownership | Reject late mutation of completed histories, require material council justification, reject a supporting unit claiming to unblock itself. | `test_completion.py` corresponding history/council/supporting regressions |
| Verification inputs | Whole-workspace recipes use tracked and non-ignored Git inputs; explicitly selected ignored inputs remain included. Consumed docs invalidate evidence. | `test_completion.py::test_whole_workspace_fingerprint_uses_git_input_boundary`; existing `test_records.py` evidence tests |
| Bounded operations | Finite mutation-lock wait and finite owned initializer deadline, including upstream mode. | `test_completion.py::test_mutation_lock_has_a_bounded_wait`, `test_upstream_initialization_has_an_owned_finite_deadline` |
| Claude role targets | Delegation names match the projected names instead of referring to absent unprefixed agents. | `test_completion.py::test_claude_delegation_targets_match_projected_role_names` |
| Codex skill targets | Installed roles bind the projected public Flow skill, not raw engine instructions that tell agents to reinstall upstream roles. Source-only previews do not preload unprojected instructions. | `test_hosts.py::test_installed_codex_roles_bind_projected_skill_not_raw_engine_instructions`, `test_source_only_codex_preview_does_not_preload_unprojected_upstream_instructions` |
| Release integrity | Check executable modes as well as bytes; include the operator guide; preserve repeat-build no-op behavior and verify cached archives. | `test_completion.py` packaging cases; `test_packaging.py`; `verification/release-smoke.json` |
| Upstream candidate | Replay real commits in an isolated candidate, update its upstream pin, regenerate through native writers and prevent nested update-test recursion. | `test_upstream.py` compatible/conflicting fixture and command-selection tests |
| Authored catalogs | Register and strictly validate the three Flow JSON sources in Crux's validator; do not mislabel them as regenerated catalogs. | `test_completion.py` strict-schema tests; original catalog suite |

Unqualified test paths in this table are under `tests/flow/`. Exact current source
function line ranges are in `verification/source-symbols.json`; the corresponding
integration rationale is in `FLOW_SEAMS.md`.

## Requirement coverage and acceptance boundary

| Capability | Delivered implementation | Current verification |
|---|---|---|
| Four modes and rapid alias | Shared strict policy with original requested caps, budgets and precedence. | Offline tests pass. |
| Read-only routing and proportionate work | Shared specification compiler and routing instructions; no automatic records for questions. | Compiler/record tests pass; actual natural-language selection is host-driven and unobserved. |
| Durable multi-prompt execution | Existing Crux book/run schemas, source pinning, required outcome/check/review gates and explicit owner transitions. | Record and CLI tests pass. |
| No-babysitting continuation | A synchronous CLI driver continues eligible native turns under the same budget; final-review outage need not halt independent work. | Offline continuation/record behavior tested; real model turns and closed-Desktop restart are not claimed. |
| Replacement discipline | One authoritative path is included in replacement acceptance; compatibility requires a concrete consumer and removal condition. | Contract tests pass; semantic correctness still requires review of each real change. |
| Model maintenance | Discovery, validation, effective diff, authorized scoped apply, regeneration, rollback and opt-in bounded unattended activation. | Offline provider fixtures and transaction tests pass; live entitlement/billing are not proven. |
| Packaging | Portable, Claude and Codex manifests with one contained engine, scripts, skills, references and license. | Static package checks and fresh-path execution pass; native clients not available here. |
| Host independence | Claude/Codex native adapters, OpenCode/OMP scoped role/skill adapters, preserved user settings and executable injection. | Isolated adapter tests pass. Four actual-host tests are skipped. |
| Lifecycle | Setup/install/repeat/upgrade/rollback/uninstall, status, strict doctor, initialization, bounded recovery and crux-local compatibility. | Isolated file and injected native-command tests pass. |
| Upstream updates | Separate release installation from real-history candidate preparation; retain pins/resources; do not fabricate ancestry. | Local Git compatible/conflict fixtures pass. A real newer upstream release merge was not attempted. |
| Complete upstream parser coverage | Existing parser fixes retained; exact optional dependency group and preflight supplied. | Not run: missing optional extractor dependencies. |
| Delivery comparisons | Existing three-task disposable delivery exercise retained in `examples/delivery-exercise/`. | No paid timing comparison; no speedup inferred from mode counts. |
| Independent final review | Reproducible evidence and focused changed-surface map supplied. | Self-review only in this environment; fresh independent review remains outstanding. |

## Executed verification

**Flow:** 124 passed, 4 skipped in 74.83s (0:01:14).

**Original Crux integration/core:** 688 passed, 3 skipped, 2943 subtests passed in 67.43s (0:01:07).

**Derived catalog and runtime compatibility:** both dry runs exit 0 with no drift.
The catalog reports six existing authored-agent effort warnings; these explain
upstream host projection differences and are not runtime observations.

**Distributable:** ZIP integrity and manifest inspection pass. Two independent
builds are byte-identical. A relocated standalone source copy rebuilt those same
bytes, and both source and packaged entrypoints ran outside the working checkout.
All four host policy inspections and the packaged guide returned successfully.
The retained package remained unchanged after those commands.

**Not executed:** real Claude/Codex/OpenCode/OMP installation or model turns,
Desktop loading, paid provider benchmarks, and the full original parser suite.
The execution environment has no native host binaries, pnpm, griffe or Tree-sitter
grammars; dependency retrieval was unavailable. Runtime dependencies were present
for the Python entrypoint checks. The pnpm script delegates to that entrypoint but
was not executed by a pnpm binary here. Do not label any of those boundaries passed.

The three skipped upstream checks target the private development/authoring checkout,
not this public source layout. Exact skip reasons and commands are preserved in
`verification/final/`.

## Start and maintain

From the source archive: `pnpm run setup`, or
`uv run --script ./crux-flow setup`. No separate aggressive-mode command is needed.
Use `FLOW_GUIDE.md` for isolated previews, scope selection, model updates and recovery.
This turn made no remote push, registry publication, paid request or real-home change.

For future upstream merging, apply the separately supplied patch on a genuine
3.25.1 branch. This source snapshot records lineage but intentionally contains no
invented Git object history. The source ZIP itself is already assembled and runnable.
