# Crux Flow integration seams

## Baseline and ownership

Upstream baseline: Bionic Crux 3.25.1, commit
`732c355a5bc3130f6cd3763214f7c908b302a3e8`. The supplied Codex fork was
`b56bdb4d6ef2055eeb138d980e44eadc36dae2d4`. This checkout completes the recovered
assistant implementation; it does not reintroduce the former `crux/flow/` runtime.

The source files below, not a potentially newer or older website catalog, are the
executable contracts for this version. The public catalog remains useful for naming
and purpose: https://bionic-coding.com/crux/catalog/ .

| Responsibility | Existing Crux seam | Flow integration | Why this boundary |
|---|---|---|---|
| Documentation location | `crux/scripts/bionic_config.py` | `crux/scripts/crux/flow/authoring.py`, `initialization.py` | Reuse the configured documentation root; do not assume `bionic/`. |
| Record numbering | `crux/scripts/record_numbers.py` | `authoring.py` | Preserve existing PB/RUN allocation and names. |
| Book and run validation | `crux/schemas/promptbook.schema.json`, `run.schema.json`, `crux/scripts/validate-promptbook.py` | `upstream_api.py`, `workflow.py`, `records.py` | Ordinary multi-prompt books; a versioned, bounded run extension rather than a second book format. |
| Progress and completion | `crux/scripts/advance-run.py` | `records.py`, `evidence.py`; gate call in the shared writer | Prompt state is authoritative. Flow cannot mark a parallel ledger complete while native prompts remain pending. |
| Native model identities | `crux/catalog/models.yml`, `crux/scripts/models_catalog.py` | `policy.py`, `models.py`, authored `flow-bindings.json` | Use the existing model identities and a fork-owned override layer; upstream mode remains separate. |
| API helper/council identities | `crux/scripts/crux/_config/llm_router_config.json` | `policy.py`, `api_calls.py` | Resolve existing router roles and pass exact selections to owned calls; no new model gateway. |
| Host role projection | `crux/scripts/codex_agents.py`, `opencode_agents.py`, `crux/agents/*.md` | `hosts.py`, `materialize.py` | Reuse model/permission semantics while rendering host-specific files and honest unsupported-control reports. |
| Skill discovery and derived catalogs | `crux/skills/*/SKILL.md`, `crux/scripts/validate-catalog.py`, `generate-runtime-compat.py` | Flow/model-refresh skills and three registered authored JSON catalogs | Register genuine authored sources separately from regenerated `skills.json`/`agents.json`; use the native writers for derived outputs. |
| Provider execution and bounded councils | `crux/scripts/crux/core/llm_caller.py`, `crux/scripts/crux/council/async_council.py` | `api_calls.py`, `processes.py` | Remaining budgets reach the actual owned invocation; native unobserved delegation is not misrepresented as enforced. |
| Project records and knowledge lifecycle | Existing Crux authoring, journal and architecture skills | Flow instructions delegate affected records through their owning skills | Do not reimplement research, architecture extraction, journal indexes or wholesale vault maintenance in Flow. |
| Technology guidance (which reference applies to which path) | Project-local skills (`.agents/skills`), the research source pages and their refresh skills, the `check-drift` roster, the authored-catalog registry in `validate-catalog.py` | `technology.py`, `generate-technology-references.py`, authored `flow-technology.json`, the `technology` section in `policy.py`, one preload argument in `hosts.py::roles` | The router is a project skill, so it never passes through Flow's skill rewriting and never collides with a plugin skill. The research pages already are the lock. Flow renders and checks; it does not fetch, install or decide eligibility by model. |
| Repository activation (which workflow plugin owns a repository) | Each host's official project-scoped plugin setting: Claude `.claude/settings.json` `enabledPlugins`; Codex `.codex/config.toml` `[plugins."id"]` | `repository.py`, `initialization.py` (`init`, `deinit`) | Activation is a product decision made with the host's own seam, never by shadowing, redirects or role-name collisions. Identities come from the host inventory. |
| Installation and release ownership | Native host plugin interfaces and original projection contracts | `lifecycle.py`, `managed.py`, `packaging.py`, `setup.py` | These are fork-owned operator services; transactional file ownership is separate from external native registration and compensation. User-scope Flow stays inert while an upstream plugin is enabled, so repositories opt in explicitly. |
| Upstream maintenance | Real Git release ancestry and native generators/tests | `upstream.py`, `testing.py` | Replay commits in an isolated candidate; update the pin, regenerate and verify without altering the working branch or installed release. |

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
The updater refuses to manufacture ancestry. Integrate the supplied upstream-relative
patch on a genuine baseline branch before using upstream preparation.
