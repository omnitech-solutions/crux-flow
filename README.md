# Crux Flow 0.2.0

Crux Flow is a fork of [Bionic Crux](https://github.com/bionic-coding/crux) 3.27.4 that replaces its configuration and orchestration layer. Upstream's skills, record formats and generators stay.

- One mode (`aggressive`, `balanced`, `thorough`, `upstream`) sets the caps for a change: elapsed budget, delegates, reviewers, repair cycles, council rounds.
- One orchestrator owns each run. Every delegate is a leaf and starts no agent of its own.
- A run is accepted only with a fresh check receipt and an independent review attestation. The shared run writer refuses anything less.
- Role agents for Claude Code, Codex, OpenCode and OMP are generated from one policy, not hand-configured per host.
- Install, update, rollback and uninstall are transactional and leave receipts. Upstream Crux stays installed and stays the default in every repository that has not run `crux-flow init`.

Evidence for each claim is in [How to prove it](#how-to-prove-it). Nothing here is claimed beyond it.

## Install and build

Python 3.13+, `uv`, and the coding host you use.

```sh
pnpm run setup       # = uv run --script ./crux-flow setup; one plan, detected hosts only, --dry-run to preview
pnpm run build       # local marketplace directory and release ZIP
pnpm run verify      # Flow suite + upstream core tests + catalog and runtime-compat dry runs
pnpm run verify:full # adds the full upstream suite (needs the upstream-test dependency group)
```

`setup` never installs host executables, changes credentials or replaces foreign files. Restart the host afterwards. `pnpm run refresh` reinstalls the checkout's current build; `pnpm run version:check` exits 2 when the installed launcher is stale. Publishing: `pnpm run release --to <git-url>` ([FLOW_GUIDE.md](FLOW_GUIDE.md)).

## Grounding principles

Each principle names the file that enforces it, or says it is a convention.

| Principle | What it means | Enforced by |
|---|---|---|
| Official seams only | Hook where upstream and each host offer a hook: project plugin settings, role files, skills, `docs_dir`, the drift roster, the authored-catalog registry. Where Flow patches upstream instead, that is listed as a non-official seam and pinned. | `crux/surface/declaration.json` (`patched_upstream_files`, `text_rewrites`), [FLOW_SEAMS.md](FLOW_SEAMS.md) |
| Simplicity first | No daemon, database, second book format or model gateway. A run is an ordinary promptbook plus one `flow` block. | `crux/scripts/crux/flow/workflow.py` (non-cycle book), `records.py` (one writer) |
| Configuration-driven | Modes, caps, roles, technology, patched files and supported schema versions are data. Code reads them. | `crux/catalog/flow-policy.json`, `flow-roles.json`, `flow-technology.json`, `crux/surface/declaration.json`; `policy.py` rejects unknown keys |
| Upstream stays upstream | Upstream mode keeps upstream's role bodies, tools, skills and models. Role files are renamed `crux-flow-*`; Codex output equals upstream's renderer apart from the prefix. | `tests/flow/test_hosts.py::test_upstream_codex_renderer_defaults_are_preserved`, `tests/flow/test_drive_and_identity.py` |
| Evidence over assertion | Done means a receipt (command, input fingerprints, exit code, output digests) and a review attestation, not a sentence. | `evidence.py`, `records.py::guard_advance`, the patched writer `crux/scripts/advance-run.py:435` |
| One orchestrator, every delegate a leaf | Only `dev-lead` and `night-gardener` hold a delegation tool, to named roles. A role gaining one fails the surface check. | `crux/scripts/crux/flow/surface.py::invariants`, `crux/skills/flow/SKILL.md` |
| Proportionate ceremony | Aggressive allows no delegate and one reviewer. Ceremony rises only when a mode is chosen. | `crux/catalog/flow-policy.json` |
| Essentials are pinned | A change to the CLI, a called upstream script, a skill contract, a role's tools or a schema version is a failing test, not a surprise. | `crux/surface/record.json`, `tests/flow/test_surface.py` |

## Capability matrix

Core Crux is upstream's skills; Crux Flow is what this fork adds or replaces. "Same" means Flow uses upstream unchanged.

| Capability | Core Crux | Crux Flow | What differs |
|---|---|---|---|
| Starting work | `dev-cycle` (architectural), `iterate`, `patch-cycle`, `fix-directly`, chosen by the model | `flow` skill, `crux-flow run start --spec change.json` | Flow takes a spec of outcomes and checks and writes a three-prompt book (implement, verify, review). Core picks a tier and a longer book. A read-only question starts nothing in either. |
| Planning | `author-promptbook`, ADRs, cycle modules (13+ prompts) | The spec file; `change_kind` additive, replacement, migration or repair | A replacement must delete the old path (`run supersede`). Architecture records stay with core's `propose-adr`. |
| Councils | `council`, `run-adr-council`, `srde` | `crux-flow run council --yes` | Flow's council is a capped, justified attempt (0, 1, 2 rounds by mode) through upstream's `AsyncCouncil`. Upstream 3.26+ gates on committed council records from `run-council.py`; that is not vendored yet. |
| Delegation | `commander` fans out to `dev-lead`, `developer`, others | One orchestrator; caps by mode; `run attempt --kind delegate` | Flow counts the attempts it is told about. A native sub-agent spawn is cooperative and not mechanically counted. No `commander` in Flow modes. |
| Review | `reviewer` agent, review modules | `run review --report`; kind `external-attestation` | Required before completion. Not an authenticated identity. |
| Acceptance evidence | Prompt states in the run snapshot; cycle gates | Prompt states plus check receipts and the review, enforced in the shared writer | Upstream's own `advance-run.py` run directly on a Flow run is refused the same way. |
| Checks | None dedicated | `crux-flow run check --label L` | Executes the frozen argv; records fingerprints; editing an input makes the receipt stale. |
| Continuation, resume | `run-promptbook` status and advance | `run status`, `run resume`, `run drive --yes` | `drive` owns consecutive host turns under the remaining budget and blocks the run after two turns with no progress. |
| Documentation and drift | `check-drift`, `log-work`, `derive-arch`, the rest | Same, plus two roster rows | Rows added: technology router, Flow surface record. |
| Technology guidance | Project-local skills only | `technology check`, `technology sync`, `maintain-technology-skills` | Generates one router skill per project from a catalog and the manifests; checked as drift. |
| Role agents per host | `crux:*` agents; `install-codex-agents`, `install-opencode-agents` | Generated `crux-flow-*` roles for claude, codex, opencode, omp | Built from policy and `models.yml`; unsupported host controls are reported. |
| Install and rollback | Plugin marketplace, `install-crux-env` | `setup`, `install`, `update`, `upgrade`, `rollback`, `uninstall`, `transactions` | Ownership receipts; refuses foreign files; Flow is inert at user scope while upstream is enabled. |
| Upstream updates | none (it is upstream) | `upstream check`, `prepare`, `verify`; the surface record | Replays fork commits onto a release in an isolated clone. Needs real Git ancestry (see Honest limits). |

## Modes

| | aggressive (alias `rapid`) | balanced | thorough | upstream |
|---|---|---|---|---|
| Elapsed budget | 20 min | 60 min | 120 min | upstream's |
| Implementation delegates | 0 | up to 2 | up to 4 | upstream's |
| Independent reviewers | 1 | up to 2 | up to 3 | upstream's |
| Repair cycles | 1 | 2 | 3 | upstream's |
| Council rounds | 0 | up to 1 | up to 2 | upstream's |
| Model profile | economical | balanced | strong | upstream's |
| Verification | focused and required | plus affected integration | plus risk integration | upstream's |
| Documentation | affected | plus rationale | expanded | upstream's |
| Generated per host | 9 roles (no `commander`) and the `flow` skill | same | same | 10 roles including `commander`, upstream bodies and skills |
| Choose it when | the change is bounded and one session can do it (the default) | independent units exist, or a justified specialist review | the blast radius is large | you want upstream semantics inside Flow's install |

Caps are maxima. Modes grant no permissions. A run freezes its mode at start; changing the project mode afterwards does not alter it (`tests/flow/scenarios/a-run-keeps-the-mode-it-started-with.json`). Source: `crux/catalog/flow-policy.json`.

## Workflow walkthrough

Once per machine, then per repository:

```sh
crux-flow setup
cd my-project
crux-flow init --mode aggressive
```

`init` writes `.crux-flow.yml`, turns Flow on and upstream `crux` off in this repository through the host's project setting (Claude `.claude/settings.json` `enabledPlugins`; Codex `.codex/config.toml`), projects the role files, and creates the documentation tree if absent. A second `init` is a no-op; `crux-flow deinit` restores only what Flow recorded. Restart the host.

Then ask the host for a change. The `flow` skill writes a spec and runs this sequence. A real small run (`calc.py` returns `a - b`; the spec declares one check):

```sh
crux-flow run start --host codex --spec change.json
# {"book": ".../promptbooks/active/PB-0001-fix-add-so-it-adds.yaml", "mode": "aggressive",
#  "run": ".../promptbooks/runs/PB-0001-fix-add-so-it-adds/run-RUN-001.yaml", "status": "ACTIVE"}

crux-flow run advance --file RUN --result "add() now returns a + b"    # prompt 1 done, current_prompt 2
crux-flow run check   --file RUN --label tests
# {"label": "tests", "status": "passed", "evidence_class": "owned-command-execution",
#  "execution": {"exit_code": 0, "stdout_digest": "e3b0c4...", ...}, "input_hashes": {"calc.py": "ba1a53..."}, ...}
crux-flow run advance --file RUN --result "tests executed"             # prompt 2 done
crux-flow run review  --file RUN --report review.json
#   review.json: {"reviewer": "separate-session", "independent": true, "evidence": "...", "findings": []}
crux-flow run advance --file RUN --result "review accepted"
# {"status": "COMPLETED", "upstream_status": "completed", "pending": [], ...}
```

| Step | What it writes |
|---|---|
| `run start` | An ordinary promptbook (3 prompts: implement, verify, review) and a run snapshot with a `flow` block: contracts, frozen effective policy, source digest. Bumps `promptbook.next_number` in the manifest. |
| `run advance` | Marks the current prompt done through upstream's `_splice` writer; the Flow gate runs first. |
| `run check` | A receipt in `flow.checks`: command identity, input hashes, toolchain digest, exit code, output digests. |
| `run review` | An attestation in `flow.reviews`, `evidence_class: external-attestation`. |
| final `run advance` | Refused unless a passing check is still fresh and an independent review exists; then `status: completed`. |

Refusals leave the run file byte-identical: a failing check (exit nonzero), an edit to `calc.py` after the check, a missing review, and a bare `advance-run.py --outcome done` all stop. Each is a scenario in `tests/flow/scenarios/`.

## The seams

Official seams are upstream's or the host's own extension points. The other rows are patches or private reaches; each is pinned. File and line detail: [FLOW_SEAMS.md](FLOW_SEAMS.md) and `verification/2026-10-10-upstream-and-integrity-audit.md`.

| Seam | Kind | How Flow hooks | Pinned by |
|---|---|---|---|
| Project plugin setting (Claude, Codex) | official | `init` writes `enabledPlugins` / `[plugins."id"]` | `test_init.py`; `test_native.py` (opt-in, real hosts) |
| `docs_dir`, manifest `schema_version` | official | `bionic_config.load_config` | `test_records.py` (`project` fixture); surface `schemas` |
| Record numbering | official | `record_numbers.scan_records` | `test_records.py::test_book_uses_existing_schema_layout_counter_and_real_units` |
| Promptbook and run schemas | official | `validate-promptbook.py` | same test; surface `schemas` |
| Model catalog | official | `models_catalog.load`, overlay `flow-bindings.json` | `test_models.py` |
| Host role rendering | official | `codex_agents.render_agent`, `opencode_agents.transform` | `test_hosts.py`, `test_drive_and_identity.py` |
| Drift roster | official | a row each for the technology router and the surface record | `test_technology.py`, `test_surface.py` |
| Authored-catalog registry | official | `validate-catalog.py` registers `flow-*.json` | `test_completion.py::test_fork_catalog_sources_are_explicitly_validated_without_regeneration` |
| Shared run writer | patched | `advance-run.py` calls `guard_advance` / `advance_file` when a run has `flow` | `scenarios/original-writer-cannot-bypass-the-gate.json` |
| Run schema | patched | `run.schema.json` gains the `flow` block | `test_records.py`; surface `schemas` |
| Council and LLM caller | patched | deadlines and frozen model selections | `test_completion.py` |
| Upstream private functions | reach | `upstream_api.py` loads `advance-run.py`, calls `_splice`, `_check_base_commit_pin` | surface `upstream_scripts` (function argument names) |
| Skill and role text | rewrite | `hosts.py` renames `/crux:` and `Agent(...)`, prepends routing text, drops the runtime-compat block | surface `text_rewrites` markers; `test_product_commands.py::test_skills_use_the_flow_plugin_namespace` |

## Drift and the surface tripwire

`check-drift` runs every roster gate. Flow adds two rows (both `--dry-run`: exit 1 with JSON on drift, never merge):

```sh
python3 crux/scripts/generate-technology-references.py --dry-run
python3 crux/scripts/generate-flow-surface.py --dry-run
```

`crux/surface/record.json` pins the Flow CLI, the upstream scripts and symbols Flow calls, 55 skill contracts, each role's tools and delegation targets per host and mode, the schema versions Flow reads, the roster, the patched upstream files and the hooks. Two verdicts:

- DRIFT: the live surface differs from the record. Each change is `hard` (something Flow depends on was removed) or `notice`. Read it, then regenerate.
- BROKEN: an invariant fails (a script or flag Flow passes is gone; a role gained a delegation target; a run or manifest format Flow does not read appeared). The regenerator refuses to write.

```sh
crux-flow upstream check            # offline: vendored version, surface state, what blocks the update procedure
crux-flow upstream check --fetch    # also asks GitHub for the latest release tag
```

## How to prove it

| Layer | Command | Proves | Cost |
|---|---|---|---|
| Full offline check | `pnpm run verify` | Flow suite, upstream core tests, catalog and runtime-compat | free, minutes |
| Scenarios | `pytest tests/flow/test_scenarios.py` | The real CLI and upstream's writer: acceptance, refusals, caps, frozen mode | free, ~10 s |
| Run state machine | `pytest tests/flow/test_run_properties.py` | Seeded random action sequences keep the run honest | free, ~70 s |
| `run drive` and upstream-mode identity | `pytest tests/flow/test_drive_and_identity.py` | Consecutive host turns reach acceptance; upstream-mode roles keep upstream bodies and tools | free |
| Surface | `pytest tests/flow/test_surface.py` | The record matches the live surface; each hard invariant fails when broken | free |
| Real hosts | `CRUX_FLOW_NATIVE_TESTS=1 pytest tests/flow/test_native.py` | Install and per-repository activation in disposable homes | free, needs the host CLIs |
| Live, free models | `CRUX_FLOW_PROVIDER_TESTS=1 CRUX_FLOW_LIVE_OPENROUTER=1 zsh -ic 'pytest tests/flow/test_behaviour_live.py -m provider -s'` | Whether a model given the skill's rules picks the compliant action, as a rate per case | zero; free tier is 50 requests a day |
| Live, paid (Claude, Codex) | `tests/flow/test_technology_live.py` | Router loading; results in `verification/2026-10-10-technology-guidance-live-evidence.md` | paid, owner-approved |

Run `pnpm run verify` and the scenarios on every change; the live layers answer a different question (does a model follow the text), not whether the machinery works.

## Measured impact

- A contradiction removed: the delegation text granted `dev-lead` a delegation tool while its injected body said there is one orchestrator. Found and fixed in `verification/2026-10-10-delegation-rule-audit.md`.
- Technology router, Claude sonnet, Studio fixture: loaded 1 of 5 times from the existing carriers, 5 of 5 with the routing line Flow proposes. A delegated role with the router in `skills:` started with it 3 of 3. See `verification/2026-10-10-technology-guidance-live-evidence.md`.
- No measured reduction in cost or time from modes. None is claimed.

## Honest limits

- Skill selection is the model's choice. Descriptions are listed for certain; whether a session reads a skill is not deterministic.
- A native sub-agent spawn is cooperative: Flow cannot count one it is not told about.
- Review attestations are external records, not authenticated identities.
- OpenCode and OMP are proven against fake host processes only. Claude and Codex activation is proven in disposable homes behind an opt-in flag. A Desktop client loading a plugin is unobserved.
- The vendored upstream is 3.27.4 (commit `06989514bc97`), applied on branch `upstream-3.27.4` by a three-way merge against the upstream 3.25.1 tree, because this checkout has no upstream Git ancestry and `upstream prepare` refuses here. Flow reads run and promptbook format 1 and 2 and executes only its own format 1 runs; the audit's "Update applied" section lists every conflict and what Flow adapted.
- The live free-model probe has produced no rate yet: on 2026-10-10 the account's free tier was exhausted. The harness reports that as `unavailable`, never as a pass.
- macOS and Linux only.

## Reference

- [FLOW_GUIDE.md](FLOW_GUIDE.md): operator guide (install, release, models, runs, technology, upstream updates).
- [FLOW_SEAMS.md](FLOW_SEAMS.md): every integration point with file and test.
- [CODEX.md](CODEX.md): using Flow in Codex.
- [verification/](verification/README.md): dated evidence.
- Core Crux reference, unchanged from upstream: [USER_GUIDE.md](USER_GUIDE.md), [OPENCODE.md](OPENCODE.md), [CHANGELOG.md](CHANGELOG.md), [crux/README.md](crux/README.md). [COMPLETION_REPORT.md](COMPLETION_REPORT.md) is the 0.2.0 build record.
- Licence and upstream notices: [LICENSE](LICENSE).
