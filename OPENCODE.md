<!-- generated-from: OPENCODE.md@sha256:399d1757a93cb4de76d381ab3522f7f78cd5007e1b6068e5b990fdf5cc94f070; model: deterministic-copy; date: 2026-09-27 -->
# Install Crux for OpenCode

Crux has no native OpenCode marketplace package. OpenCode installation uses a stable clone of the public Crux repository. This guide covers machine-wide and project-only installation for humans and agents.

The generated agents require OpenCode V2. A V1 runner ignores the V2 `permissions` array and drops the role restrictions.

## Human installation

### 1. Clone Crux to a stable path

```bash
git clone https://github.com/bionic-coding/crux ~/.local/share/crux
cd ~/.local/share/crux
```

The OpenCode configuration uses absolute paths. Moving the clone breaks skill and agent loading until you update the configuration and symlinks.

### 2. Install `uv`

Crux scripts use PEP 723 metadata and run through `uv`:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv --version
```

### 3. Generate the OpenCode agents

The public repository does not commit the generated `opencode/agents/` directory. Generate it after cloning and after every update:

```bash
uv run python3 crux/scripts/generate-opencode-agents.py
```

### 4. Register the skills

Merge this entry into `~/.config/opencode/opencode.json`. Replace `/Users/you/.local/share/crux` with the absolute clone path:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "skills": ["/Users/you/.local/share/crux/crux/skills"]
}
```

The `skills` value is a V2 array of directories. Preserve existing configuration keys and existing skill entries.

### 5. Register the agents

Create one symlink per generated role:

```bash
mkdir -p ~/.config/opencode/agents
for f in ~/.local/share/crux/opencode/agents/*.md; do
  ln -sf "$f" ~/.config/opencode/agents/"$(basename "$f")"
done
```

Use the plural `~/.config/opencode/agents/` directory. Remove old Crux links from the legacy singular directory:

```bash
rm -f ~/.config/opencode/agent/{architect,brainstormer,commander,dev-lead,developer,historian,librarian,night-gardener,reviewer,wayfinder}.md
```

### 6. Restart and verify

Fully quit and restart the OpenCode host. Starting a new thread alone is insufficient. For Zed or ACP, restart Zed or reconnect the ACP process.

Verify the agents:

```bash
opencode debug agents
```

The command should list these ten Crux roles:

- `architect`
- `brainstormer`
- `commander`
- `dev-lead`
- `developer`
- `historian`
- `librarian`
- `night-gardener`
- `reviewer`
- `wayfinder`

The command can return `[]` at exit 0 while the background service resolves the project. Run it again before diagnosing a missing agent.

OpenCode V2 has no skill-listing command. Verify skills by asking OpenCode to use one, such as “audit docs” or “what's next.”

### Project-only agents

After registering the Crux skills, ask OpenCode:

> Install the Crux agents in this project.

The `install-opencode-agents` skill writes the ten roles to `.opencode/agents/`. It checks for an OpenCode V2 runner, preserves unrelated agents, and refuses changed managed roles without `--force`.

A project role shadows a machine-wide role with the same name. Choose one scope for each project.

## Agent installation procedure

Follow this section when you are the OpenCode agent performing installation for a user.

1. Confirm that an OpenCode 2.x runner is available: `opencode --version` reports a version whose major is 2. The installer identifies the runner by that reported version, never by the name of its executable, so a 2.x runner installed under any name passes. Set `CRUX_OPENCODE_BIN` to name a runner the installer would not otherwise find.
2. Resolve the absolute Crux clone or plugin path. Do not write a relative skill path into the global configuration.
3. Read the existing `~/.config/opencode/opencode.json`. Merge the Crux skill directory into its `skills` array without dropping other keys or entries.
4. Generate the projection with `uv run python3 <clone>/crux/scripts/generate-opencode-agents.py`.
5. For machine-wide installation, create glob-based per-file symlinks from `<clone>/opencode/agents/*.md` into `~/.config/opencode/agents/`. Do not hardcode a partial roster.
6. For project scope, invoke the installer through `uv`:

   ```bash
   uv run <install-opencode-agents-skill-dir>/scripts/install.py --repo-root "$PWD"
   ```

7. If the legacy `.opencode/agent/` directory contains a roster filename, report it. Use `--migrate-legacy-agent-dir` only after reviewing the move and any destination collision.
8. Use `--force` only after showing the user the managed-file or migration collision.
9. Tell the user which host process must restart. Do not claim that a file-level check proves the running host loaded the new configuration.

## Updating Crux

```bash
cd ~/.local/share/crux
git pull --ff-only
uv run python3 crux/scripts/generate-opencode-agents.py
```

The global symlinks continue pointing at the generated files. Restart OpenCode after regeneration. Run the generator with `--dry-run` to check for drift without writing:

```bash
uv run python3 crux/scripts/generate-opencode-agents.py --dry-run
```

Project-only installations do not update through the global symlinks. Invoke `install-opencode-agents` again in each project and review drift before using `--force`.

## Optional model gateway

Most Crux skills work without external model credentials. Council and LLM-backed skills use one OpenRouter key stored outside the repository:

```bash
python3 crux/scripts/crux-env.py init
python3 crux/scripts/crux-env.py set OPENROUTER_API_KEY <value>
python3 crux/scripts/crux-env.py check --project crux
```

The check command never prints secret values.

## Troubleshooting

| Symptom | Action |
|---|---|
| `opencode debug agents` omits Crux roles | Regenerate the projection, inspect the symlinks, and restart the host. |
| An agent file reports an invalid comma-separated `tools` field | The symlink targets `crux/agents/` instead of generated `opencode/agents/`. |
| A role runs without its deny rules | The runner is older than 2 and ignored the `permissions` array. Check `opencode --version`, and upgrade to 2.x. |
| A skill is missing | Check the absolute `skills` path in `opencode.json`, then restart OpenCode. |
| Roles use old instructions after `git pull` | Regenerate `opencode/agents/`; the public clone leaves that directory untracked. |
| The project installer refuses a legacy role | Review `.opencode/agent/`, then use the migration flag if those files belong to Crux. |

## Security boundaries

Do not commit `~/.config/opencode/opencode.json`, API keys, or user-level agent links into a project. Do not run generated V2 roles with the V1 `opencode` binary.

The machine-wide setup uses no Crux installer code after projection generation. The project installer only manages the ten current role filenames and preserves unrelated files.
