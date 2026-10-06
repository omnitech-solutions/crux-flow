<!-- generated-from: CODEX.md@sha256:92c1ec3510233ef8f1ce86ebfcf78eefc587ef304bfca98263c2ea5815825366; model: deterministic-copy; date: 2026-09-27 -->
# Install Crux for Codex

This guide installs the Crux plugin, its skills, and its ten Codex role agents. It covers human installation and the procedure an agent should follow after the plugin is available.

## Human installation

### 1. Install the plugin

Run these commands in Codex:

```text
codex plugin marketplace add bionic-coding/crux
codex plugin add crux@crux
```

Start a new Codex thread after installation. The new thread refreshes the skill inventory and makes the `install-codex-agents` skill available.

### 2. Install the role agents

In the new thread, say:

> Install the Crux agents in Codex.

The installer writes these namespaced agents to `~/.codex/agents/`:

- `crux_architect`
- `crux_brainstormer`
- `crux_commander`
- `crux_dev_lead`
- `crux_developer`
- `crux_historian`
- `crux_librarian`
- `crux_night_gardener`
- `crux_reviewer`
- `crux_wayfinder`

Each agent pins its catalog model, reasoning effort, sandbox, and declared Crux skills. Skill bindings use absolute paths into the selected plugin installation.

### 3. Check the installation

Ask Codex:

> Check the Crux Codex agents and inspect this project for shadows.

The agent runs the installer in read-only check mode with the current repository as `--project-context`. A clean static report has:

- `installed.status: clean`;
- `drift.status: clean`;
- every installed role at `status: installed`;
- every skill binding at `status: enabled`; and
- no project shadow findings.

The `runtime` result remains `unverified` until a fresh Codex session records host evidence. Static TOML inspection cannot prove that Codex discovered an agent, applied its model, or loaded its skills.

### Project-only installation

Use a project installation when the roles should exist only inside one repository:

> Install the Crux Codex agents in this project only.

The agent passes `--repo-root` and writes `.codex/agents/crux-*.toml` inside that repository. A project agent with the same effective name can shadow a personal agent.

### Updating Crux

Update the marketplace and plugin, then start a new thread:

```text
codex plugin marketplace upgrade crux
codex plugin add crux@crux
```

Then say:

> Refresh the Crux agents in Codex.

An upgraded or relocated plugin changes the absolute skill paths. The installer reports that change as managed drift. Review the `changed` and `removed` lists before allowing `--force`.

## Agent installation procedure

Follow this section when you are the Codex agent performing an installation for a user.

1. Confirm that the selected plugin exposes the `install-codex-agents` skill. If it does not, tell the user to install or upgrade the plugin with the marketplace commands above.
2. Derive the plugin root from the selected skill's absolute `SKILL.md` path. Do not assume `CLAUDE_PLUGIN_ROOT` exists in Codex.
3. Run the sibling installer through `uv`:

   ```bash
   uv run <install-codex-agents-skill-dir>/scripts/install.py --project-context "$PWD"
   ```

   Omit `--project-context` when no repository should be inspected. Use `--repo-root "$PWD"` only when the user requested project scope.

4. Read the JSON result. Do not treat canonical `roles` as proof of installed files; verify `installed.roles`, `drift`, and `shadows` separately.
5. If changed or stale managed files cause a refusal, show the user the reported lists. Run again with `--force` only after that drift has been reviewed.
6. Start a fresh Codex thread before testing discovery. Ask the new session to invoke representative roles and skills.
7. Leave runtime status `unverified` unless the health reporter consumes recorded evidence for the exact Codex client and installation path.

For an alternate personal home, pass `--codex-home <path>`. Do not use the real home directory in automated tests.

## What the installer changes

The installer owns `crux-*.toml` files in the selected agent directory. It preserves unrelated agent files. An unchanged refresh writes nothing and preserves file modification times.

The installer refuses unsafe managed files, escaping paths, malformed source declarations, and invalid plugin manifests. It pins the validated directory identity across drift checks, writes, removals, and post-write health reporting.

## Troubleshooting

| Symptom | Action |
|---|---|
| `install-codex-agents` is unavailable | Upgrade the plugin, then start a new thread. |
| The installer reports managed drift | Review `changed` and `removed`, then refresh with `--force`. |
| A role is missing in a project | Check for a project agent with the same effective name and inspect `shadows`. |
| Skill bindings point to an old plugin path | Refresh after verifying the selected plugin root. |
| Static health is clean but the role is unavailable | Start a fresh thread and record runtime evidence; static health does not prove host discovery. |
| A role has broader access than expected | Check the parent session's permission overrides. The role prompt remains binding. |

## Security boundaries

Skill configuration controls availability and enablement. It does not grant credentials, tools, or broader sandbox access. Parent-session permissions can override an agent's default sandbox.

Never hand-edit installed `crux-*.toml` files. Change the Crux source, regenerate the projection, and refresh through the installer.
