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
| Installation and release ownership | Native host plugin interfaces and original projection contracts | `lifecycle.py`, `managed.py`, `packaging.py`, `setup.py` | These are fork-owned operator services; transactional file ownership is separate from external native registration and compensation. |
| Upstream maintenance | Real Git release ancestry and native generators/tests | `upstream.py`, `testing.py` | Replay commits in an isolated candidate; update the pin, regenerate and verify without altering the working branch or installed release. |

## Deliberately retained correctness exceptions

The audited fork's owned Swift parser deadline, patch-containment completion
preflight, overall council deadline and role-named Anthropic helper aliases remain.
These are narrow fixes in the component that owns the behavior. They are not removed
just to make a diff smaller. Parser execution tests require the pinned optional
extractor dependencies and are not reported as executed in the current environment.

## Explicit limits

A generated routing trigger is not a deterministic natural-language dispatcher.
Native permission/delegation enforcement remains host-specific. Review records
contain external attestations, not cryptographically authenticated reviewer identities.
The CLI continuation driver owns consecutive native turns, not the operating system
or a closed Desktop application. Installed payloads and active runs stay pinned;
upgrades prepare the next compatible session rather than modifying loaded history.

The current source snapshot has provenance but not the original Git object database.
The updater refuses to manufacture ancestry. Integrate the supplied upstream-relative
patch on a genuine baseline branch before using upstream preparation.
