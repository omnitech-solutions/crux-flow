# Crux Flow integration seams

Every point where Flow touches upstream Crux or a host, whether the seam is official, and the test that pins it.
Baseline: Bionic Crux 3.27.4, commit `06989514bc97de7b252d03979b5d2db08a61a919` (`reference-source.json`).
The source files, not the public catalog, are the contract for this version.

`crux/surface/record.json` pins the machine-checkable half of this page (see `tests/flow/test_surface.py`); the audit
`verification/2026-10-10-upstream-and-integrity-audit.md` gives line numbers and the upstream 3.27.4 comparison.

## Official seams

| Responsibility | Upstream seam | Flow integration | Pinned by |
|---|---|---|---|
| Documentation location | `scripts/bionic_config.py` (`load_config`, `docs_dir`) | `authoring.py`, `initialization.py` | `test_records.py` (`project` fixture uses `docs_dir: knowledge`) |
| Record numbering | `scripts/record_numbers.py` (`scan_records`) | `authoring.py` | `test_records.py::test_book_uses_existing_schema_layout_counter_and_real_units` |
| Book and run validation | `schemas/promptbook.schema.json`, `schemas/run.schema.json`, `validate-promptbook.py` | `upstream_api.py`, `workflow.py`, `records.py` | same test; surface `schemas` |
| Native model identities | `catalog/models.yml`, `scripts/models_catalog.py` | `policy.py`, `models.py`, overlay `flow-bindings.json` | `test_models.py` |
| API helper and council identities | `scripts/crux/_config/llm_router_config.json` | `policy.py`, `api_calls.py` | `test_completion.py` |
| Host role projection | `scripts/codex_agents.py`, `opencode_agents.py`, `agents/*.md` | `hosts.py`, `materialize.py` | `test_hosts.py`, `test_drive_and_identity.py` |
| Skill discovery and derived catalogs | `skills/*/SKILL.md`, `validate-catalog.py`, `generate-runtime-compat.py` | `flow`, `models-refresh`, `maintain-technology-skills`; three registered authored catalogs | `test_completion.py::test_fork_catalog_sources_are_explicitly_validated_without_regeneration` |
| Drift roster | `skills/check-drift/SKILL.md` table | rows for `generate-technology-references.py` and `generate-flow-surface.py` | `test_technology.py::test_regenerator_keeps_the_sibling_contract`, `test_surface.py` |
| Technology guidance | project-local skills (`.agents/skills`), research source pages | `technology.py`, `generate-technology-references.py`, `flow-technology.json`; one preload argument in `hosts.py::roles` | `test_technology.py` |
| Repository activation | each host's project plugin setting | `repository.py`, `initialization.py` | `test_init.py`; `test_native.py` (opt-in) |
| Install and release | native host plugin interfaces | `lifecycle.py`, `managed.py`, `packaging.py`, `setup.py` | `test_lifecycle.py`, `test_packaging.py`, `test_publish.py` |
| Upstream maintenance | Git release ancestry | `upstream.py` (`check`, `prepare`, `verify`), `testing.py` | `test_upstream.py` (fixture history), `test_surface.py` |

## Non-official seams

These are patches to upstream-owned files, private reaches and text rewrites. Each carries a risk when upstream changes; each is pinned so a change is noticed. `patched_upstream_files` and `text_rewrites` in `crux/surface/declaration.json` are the authoritative lists; the record stores a digest of every patched file.

| What | Where | Risk | Pinned by |
|---|---|---|---|
| Shared run writer calls Flow's gate and `advance_file` when a run has `flow` | `crux/scripts/advance-run.py:435-437`, `:551-564`; plus a patch-completion preflight at `:475-507`, `:603-616` | Highest. Upstream 3.27.4 rewrote this file (gate info, council gate, implementation bindings); the patch does not apply | `scenarios/original-writer-cannot-bypass-the-gate.json`, `test_records.py::test_original_writer_cannot_skip_check_or_complete_with_missing_review` |
| `flow` block in the run schema | `crux/schemas/run.schema.json` | High. Upstream changed `format_version` to 1 or 2 in the same region | surface `schemas`; `test_records.py` validates a run against it |
| Bounded council, frozen router digest, role-named Anthropic helpers | `crux/scripts/crux/council/async_council.py`, `crux/scripts/crux/core/llm_caller.py`, `crux/scripts/crux/core/__init__.py` | High for `async_council.py` (upstream changed it), medium for the others | surface digests; `test_completion.py` |
| Authored-catalog registry | `crux/scripts/validate-catalog.py` | Medium | `test_completion.py` |
| Owned Swift parser deadline | `crux/scripts/crux/arch/packs/swift.py` | Low; needs the optional parser dependencies to exercise | `crux/scripts/tests/test_swift_parse_deadline.py` (full suite only) |
| Role and catalog data | `crux/catalog/models.yml`, `bundles.yml`, `skills.json`; `crux/scripts/codex_agents.py` | Medium; `skills.json` is regenerated | `test_models.py`, `test_hosts.py` |
| Eight skills edited | `archive-promptbook`, `call-llm`, `check-drift`, `dev-cycle`, `fix-directly`, `iterate`, `patch-cycle`, `run-promptbook` (and `run-promptbook/references/advance.md`) | Medium; text-level | surface skill contract digests |
| Upstream modules executed in-process, private functions called | `upstream_api.py::load` runs `advance-run.py`, `validate-promptbook.py`, `check-promptbook-index.py`; calls `_splice`, `_check_base_commit_pin` | Medium; signatures are pinned by argument name | surface `upstream_scripts` |
| Skill text rewritten on export | `hosts.py:127` (`/crux:` to `/crux-flow:`), `:128` (drops the runtime-compat block), `:140` (prepends routing text) | Medium; relies on upstream's text shape | surface `text_rewrites` markers |
| Role bodies replaced, `Agent(...)` targets renamed | `hosts.py:64` (no `commander`), `:84` | Medium | `test_hosts.py`, `test_completion.py::test_claude_delegation_targets_match_projected_role_names` |
| Packaging copies `crux/` as the engine and expects `crux/scripts` on the path | `packaging.py`, `pyproject.toml` `pythonpath` | Low | `test_packaging.py` |

## Host seams

Four concerns are kept separate and each uses its own seam:

```text
Repository activation   Claude -> project plugin setting     Codex -> .codex/config.toml plugin setting
Flow project policy     .crux-flow.yml (mode: aggressive | balanced | thorough | upstream)
Role/model projection   official host role, agent, skill and config surfaces
Core Crux               existing records, promptbooks, generators, architecture and knowledge machinery
```

`mode: upstream` is Flow policy. It never changes which plugin is active.

### Verified by observation

Observed on 2026-10-05 against the installed hosts, in disposable homes (`CODEX_HOME`,
`CLAUDE_CONFIG_DIR` and `HOME` under a temporary directory) with a stand-in upstream
marketplace named `crux`. Automated by
`CRUX_FLOW_NATIVE_TESTS=1 uv run --group test python -m pytest tests/flow/test_native.py -k real_host`.

| Host (version) | Seam | Observation |
|---|---|---|
| Claude 2.1.291 | `<repo>/.claude/settings.json` and `<repo>/.claude/settings.local.json`: `{"enabledPlugins": {"<plugin>@<marketplace>": true\|false}}` | `claude plugin list --json` run in the repository reports `enabled: false` for the disabled plugin; a repository without the file still reports `true`. Inspection is the host's own, not a file read. |
| Codex 0.160.0 | `<repo>/.codex/config.toml`: `[plugins."<plugin>@<marketplace>"]` with `enabled = true\|false` | `codex plugin list --json` run in a trusted repository reports the project value in `installed[].enabled` (key `pluginId`); another repository is unaffected. `codex debug prompt-input` in the same repository no longer lists the disabled plugin's skills. |
| Codex 0.160.0 | `[projects."<path>"] trust_level = "trusted"` in the user `config.toml` | Project plugin settings are **ignored in an untrusted repository**. `init` reports `trusted_project` and an `attention` note rather than claiming activation. |
| Claude 2.1.291 | `claude plugin disable <id> --scope user` | Keeps a user-scope Flow installation inert while upstream is enabled. |
| Codex 0.160.0 | user `config.toml` plugin table `enabled = false` | Same purpose; Codex has no `plugin disable` command, so Flow edits only its own table. |

Inspection command per host is `plugin list --json` run from the repository (`lifecycle.inventory`).
Probes of `--version` are parsed from `codex-cli 0.160.0`, `2.1.291 (Claude Code)`,
`opencode v2.0.16` and `omp/18.3.2`.

### Not plugin mechanisms

OpenCode (v2.0.16) and OMP (18.3.2) are configured through their project agent, skill and
config surfaces only: `.opencode/agents`, `.opencode/skills`, `.omp/agents`, `.omp/skills`
and OMP `modelRoles` (`default`, `smol`, `slow`, derived from the shared Flow policy and
never replacing a user pin). No plugin activation is invented for them. These surfaces are
exercised by the automated lifecycle tests with fake host processes; a live host session
was not driven.

### Unobserved

A new host session loading the selected plugin (`runtime_loaded` is reported `unobserved`),
managed or enterprise requirements that override project settings, and live OpenCode/OMP
sessions. Restart the host after `init` or `deinit`.

## Deliberately retained correctness exceptions

The audited fork's owned Swift parser deadline, patch-containment completion
preflight, overall council deadline and role-named Anthropic helper aliases remain.
These are narrow fixes in the component that owns the behavior. They are not removed
just to make a diff smaller. Parser execution tests require the pinned optional
extractor dependencies and are not reported as executed in the current environment.

## Explicit limits

A generated routing trigger is not a deterministic natural-language dispatcher.
The same holds for a technology router: its description being listed is certain, its body being read in an ordinary session is the model's choice, and only a Claude role file's `skills:` entry injects it. See `verification/2026-10-10-technology-guidance-live-evidence.md` for the measured rates.
Native permission/delegation enforcement remains host-specific. Review records
contain external attestations, not cryptographically authenticated reviewer identities.
The CLI continuation driver owns consecutive native turns, not the operating system
or a closed Desktop application. Installed payloads and active runs stay pinned;
upgrades prepare the next compatible session rather than modifying loaded history.

The current source snapshot has provenance but not the original Git object database.
`upstream prepare` refuses to manufacture ancestry, and `upstream check` reports the blockers offline.
Integrate the supplied upstream-relative patch on a genuine baseline branch before using preparation.
