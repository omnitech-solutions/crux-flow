---
name: install-codex-agents
description: "Install, refresh, or check crux's Codex role agents in the personal or specified project directory. Preserve conflicting roles."
disable-model-invocation: true
metadata:
  tags: "codex, agents, installation, roles"
  bundles: "crux-infrastructure, crux-docs"
  risk_level: "medium"
  triggers: "install codex agents | install crux agents for codex | refresh the codex agents"
---

# Install Codex Agents

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

Codex reads custom subagents from `.codex/agents/*.toml`; it does not consume
Crux's source agent Markdown files. This skill installs the ten namespaced Crux
roles into the active user's personal Codex directory by default:

`crux_architect`, `crux_brainstormer`, `crux_commander`, `crux_dev_lead`,
`crux_developer`, `crux_historian`, `crux_librarian`,
`crux_night_gardener`, `crux_reviewer`, and `crux_wayfinder`.

The generated files are `crux-<role>.toml`. Each file pins the role's model and
reasoning effort from `crux/catalog/models.yml`. It also contains one enabled
`skills.config` entry for each skill declared in `crux/agents/<role>.md`.
Those entries use absolute paths to the selected plugin's `SKILL.md` files.

The installer preserves unrelated agent files. Read-oriented roles default to
Codex's `read-only` sandbox; writer roles default to `workspace-write`.

## Install

<!-- Provenance: this skill assumes the crux plugin is already installed in
Codex via `codex plugin marketplace add bionic-coding/crux` then `codex plugin add
crux@crux` (documented in install-docs-skills/references/install-and-upgrade.md). That CLI syntax is
an external Codex contract owned by OpenAI, not by crux — track it against
upstream, don't assume it. Verified 2026-07-09 against the official docs
(https://developers.openai.com/codex/plugins/build) and against the local
`codex plugin ... --help` for codex-cli 0.144.0, which accept the
`owner/repo` marketplace source and the `PLUGIN@MARKETPLACE` positional
(`crux@crux`) forms. -->

Codex provides this selected skill's absolute `SKILL.md` path in context. Let
`<skill-dir>` be its containing directory.

Install for the active user:

```bash
uv run <skill-dir>/scripts/install.py
```

This targets `~/.codex/agents/`. Supply the current repository when you also
want the health report to scan for project agents with the same effective name:

```bash
uv run <skill-dir>/scripts/install.py --project-context "$PWD"
```

Use project scope only when the user requests repository-local agents:

```bash
uv run <skill-dir>/scripts/install.py --repo-root "$PWD"
```

`--repo-root` and `--codex-home` are mutually exclusive. `--codex-home <path>`
sets the personal target to `<path>/agents`. It supports isolated tests and
explicit alternate installations. Codex discovery from an alternate home
remains unverified until that exact client and path pass the fresh-session procedure in
[`references/fresh-session-verification.md`](references/fresh-session-verification.md).

### Check health without writing

```bash
uv run <skill-dir>/scripts/install.py --check --project-context "$PWD"
```

`--check` prints JSON and does not create the target. Its exit status is `0`
when `drift.status` and `installed.status` are `clean` and the shadow scan has
no findings. It returns `1` for managed drift, installed-state findings, or
shadow findings. It returns `2` for a configuration or environment error.

The stable health report uses these terms:

- `scope`: `personal` or `project`.
- `target`: the resolved agent directory.
- `plugin`: `root` and `version` for the selected Crux plugin.
- `roles`: the sorted canonical expectations. Each record contains `role`,
  `name`, `model`, `model_reasoning_effort`, `declared_skills`, and
  `resolved_skills`.
- `installed`: parsed managed TOML state. It contains `status` (`clean` or
  `findings`) and a sorted `roles` array. Each installed role contains `role`,
  `file`, `status`, `model`, `model_reasoning_effort`, and `skill_bindings`.
  Role status is `installed`, `missing`, `unsafe`, or `malformed`.
- `skill_bindings`: each installed role's binding comparison. Every binding
  contains `skill`, `expected_path`, `installed_path`, and `status`. Binding
  status is `enabled`, `disabled`, or `missing`.
- `drift`: `status` (`clean` or `drift`) plus `added`, `changed`, and `removed`.
- `shadows`: `status` (`unverified`, `clean`, or `findings`) plus
  `project_context`, `roles`, `malformed`, and `unsafe`.
- `runtime`: `status`, `client_version`, and `reason`. The current runtime
  status is `unverified`, the client version is `null`, and the reason is
  `fresh-session host evidence has not been recorded`.

A completed install adds `written` and `removed` to the report. Repeating an
unchanged install returns an empty `written` list and does not rewrite files.
`roles` always describes the selected plugin's canonical configuration.
`installed.roles` describes what the health check parsed from managed files.

### Refresh managed files

A later run refuses to replace a changed managed file or remove a stale managed
file. Review the reported `changed` and `removed` lists, then refresh:

```bash
uv run <skill-dir>/scripts/install.py --force --project-context "$PWD"
```

Plugin relocation changes the absolute skill paths and therefore appears as
managed drift. Refresh with `--force` after you verify the new plugin root.
The installer preserves unrelated files and refuses managed symlink leaves.
It also refuses any target or managed leaf that resolves outside the selected
personal or project root.

### Understand shadowing and runtime limits

`--project-context <repo>` scans `<repo>/.codex/agents/*.toml` by the effective
TOML `name`, regardless of filename. It reports matching `crux_*` roles,
malformed TOML, and unsafe symlinks without changing the repository. Without
`--project-context`, `shadows.status` is `unverified`.

Start a new Codex thread after installation so Codex can discover the agents.
Ask Codex to spawn a namespaced role such as `crux_reviewer` or
`crux_developer`. Static health separates canonical expectations in `roles`
from parsed managed files in `installed.roles`. It does not prove host
discovery, effective model settings, skill loading, or workflow behavior.
Runtime stays `unverified` unless the selected Codex version has a recorded
pass and the health reporter consumes that evidence.

Skill configuration controls availability and enablement. It does not grant
tools, credentials, sandbox access, or eager instruction loading. Parent-session
permission overrides and Codex's delegation-depth limits still apply.

## Verification

- [ ] The selected target contains the ten generated `crux-*.toml` files.
- [ ] Existing non-Crux agent files remain unchanged.
- [ ] Every role record contains the expected model, effort, and declared skills.
- [ ] Every `resolved_skills` entry points to the selected plugin's `SKILL.md`.
- [ ] `installed.status` is `clean` and every installed role has status
  `installed`.
- [ ] Installed models, efforts, and skill bindings match the canonical role.
- [ ] `drift.status` is `clean` after installation.
- [ ] Shadow findings were reviewed, or `shadows.status` is recorded as
  `unverified` because no project context was supplied.
- [ ] `runtime.status` is reported as `unverified` until fresh-session host
  evidence exists.

## Guardrails

- Do not hand-edit generated `crux-*.toml` files. Change `crux/agents/*.md` in
  the Crux source, regenerate, and refresh intentionally.
- Do not use `--force` before reviewing changed and stale managed files.
- Do not treat a role's self-report as evidence of its effective model, effort,
  or loaded skills. Use host-observed evidence.
- Codex parent-session permission overrides can be broader than a role's default
  sandbox. The role prompt remains binding even when that occurs.
