---
name: install-runtime
description: "Deploy crux's Python agent-team runtime into another repository. Distinct from plugin installation and documentation initialization."
disable-model-invocation: true
metadata:
  tags: "spawner, deployment, infrastructure, lifecycle"
  bundles: "crux-infrastructure"
  risk_level: "low"
  triggers: "install the crux runtime | deploy the crux runtime | spawn crux into this repo | bootstrap the agent team"
  requires_env: ""
---

# Install Runtime (the crux Spawner)

<!-- BEGIN GENERATED: runtime-compat -->
## Runtime compatibility

This skill is portable across Claude Code, Codex, and OpenCode. This section overrides platform-specific labels below.

- Before running a command that uses `CRUX_PLUGIN_ROOT`, set it to the installed plugin root. In Claude Code, use the value of `CLAUDE_PLUGIN_ROOT`. In Codex and OpenCode, derive it from the absolute path of this selected `SKILL.md`: the plugin root is the parent of its `skills/` directory. In a source checkout, use the checkout `crux/` directory.
- For project-local skills, use `.claude/skills` in Claude Code, `.agents/skills` in Codex, and `.opencode/skills` in OpenCode, which also reads the singular `.opencode/skill`. Set `CRUX_LOCAL_SKILLS_DIR` to that path before following any command below that uses it.
- Translate Claude Code tool labels such as `Agent`, `Read`, `Write`, `Bash`, `WebSearch`, and `WebFetch` to the matching capability in the current session. Codex names its own capabilities; OpenCode uses the lowercase forms `subagent`, `read`, `edit`, `shell`, `websearch`, and `webfetch`, where `edit` covers both `Edit` and `Write`. Do not attempt to invoke the Claude Code labels as literal commands on another host.
- Install the generated role agents before delegating: `install-codex-agents` in Codex, `install-opencode-agents` in OpenCode. Codex names them `crux_architect`, `crux_brainstormer`, `crux_commander`, `crux_dev_lead`, `crux_developer`, `crux_historian`, `crux_librarian`, `crux_night_gardener`, `crux_reviewer`, and `crux_wayfinder`; OpenCode uses the bare role names `architect`, `brainstormer`, `commander`, `dev-lead`, `developer`, `historian`, `librarian`, `night-gardener`, `reviewer`, and `wayfinder`. If a required role or capability is unavailable, report that truthfully instead of claiming it ran.
- Argument placeholders such as `$adr` and `$book` bind only in Claude Code. On a host without argument binding they are unset — take the value from the user's phrase. The "Fields OpenCode ignores" section of `OPENCODE_GUIDE.md` names the invocation-control fields OpenCode ignores.
<!-- END GENERATED: runtime-compat -->


## File Locations
```
crux/scripts/crux/spawner/
├── __init__.py
├── crux_spawner.py    ← Spawner, RepoAnalyzer, spawn_crux()
└── __main__.py            ← CLI entry point
```

## Exports
```
From crux.spawner __init__:
  spawn_crux, Spawner, RepoAnalyzer, RepoAnalysis
```

## Running it (under `uv`)

`import crux.spawner` triggers `crux/scripts/crux/__init__.py`, which eagerly
imports the LLM router's httpx transport (the provider SDKs were retired with
the gateway consolidation), so bare `python3` dies with
`ModuleNotFoundError`. For the Python API below, write your driver to a
`.py` file in a private per-run directory (`mktemp -d`) starting with the
standard PEP 723 header, then run it with `uv`:

```python
# /// script
# requires-python = ">=3.10"
# dependencies = ["httpx>=0.27"]
# ///
import sys
sys.path.insert(0, "/REPLACE/WITH/PLUGIN/ROOT/scripts")  # the actual ${CRUX_PLUGIN_ROOT} value — Python won't expand env-var syntax

from crux.spawner import spawn_crux
# ... your driver code
```

```bash
d=$(mktemp -d) && uv run "$d/driver.py"
```

(Use a fresh `mktemp -d` directory rather than a fixed path like
`/tmp/driver.py` — predictable shared-/tmp names are a symlink/TOCTOU hazard.)

`${CRUX_PLUGIN_ROOT}` is crux's portable plugin-root name (in Claude Code, the
value of `CLAUDE_PLUGIN_ROOT`; in Codex, derived from this `SKILL.md`'s path per
the Runtime compatibility note above); in a source
checkout (where it is unset) substitute the checkout's `crux/` directory for
the variable — e.g. `/path/to/checkout/crux/scripts` — and when writing the
temp driver, substitute the variable with its actual value.
Without `uv` this fails at the shell (`command not found: uv`, exit 127) —
install uv (https://docs.astral.sh/uv/). See `council/SKILL.md` and
`docs/AGENTS.md` §10.A for the canonical reference.

## Quick Spawn (function)

```python
from crux.spawner import spawn_crux

spawn_crux(
    target_repo="/path/to/target/repo",
    prime_directive="Build authentication system with JWT and RBAC",
    crux_dir=".crux-runtime",  # directory name in target repo
)
```

## Full Control (class)

```python
from crux.spawner import Spawner, RepoAnalyzer

# Analyze the target repo first
analyzer = RepoAnalyzer("/path/to/target/repo")
analysis = analyzer.analyze()
print(f"Tech stack: {analysis.tech_stack}")
print(f"Languages: {analysis.languages}")

# Spawn with full control
spawner = Spawner(
    target_repo="/path/to/repo",
    crux_dir=".crux-runtime",
)
spawner.spawn(prime_directive="Build feature X")
```

## CLI Usage

The CLI entry script is PEP 723 self-describing; run it directly with `uv`:

```bash
# Basic spawn
uv run "${CRUX_PLUGIN_ROOT}/scripts/crux/spawner/__main__.py" /path/to/target/repo

# With a task
uv run "${CRUX_PLUGIN_ROOT}/scripts/crux/spawner/__main__.py" /path/to/repo --task "Build authentication system"

# Custom directory name
uv run "${CRUX_PLUGIN_ROOT}/scripts/crux/spawner/__main__.py" /path/to/repo --dir .crux-agents --task "Add tests"

# Skip analysis (faster, less custom)
uv run "${CRUX_PLUGIN_ROOT}/scripts/crux/spawner/__main__.py" /path/to/repo --no-analyze
```

## What Gets Spawned

```
target-repo/
├── .crux-runtime/            # Spawned crux
│   ├── AGENTS.md                 # Customized for THIS repo
│   ├── scripts/crux/         # crux Python runtime
│   ├── box/
│   │   ├── prime_directive.md    # The task
│   │   ├── knowledge/
│   │   │   ├── repo_analysis.json    # Auto-analyzed repo info
│   │   │   └── repo_summary.md       # Human-readable analysis
│   │   └── templates/            # JSON schemas (if available)
│   └── logs/                     # Run artifacts (agent runs, traces)
└── AGENTS.md                     # Also added to repo root (if absent)
```

API keys are NOT written by the spawner. Run the `crux-env` CLI (stdlib-only:
`python3 "${CRUX_PLUGIN_ROOT}/scripts/crux-env.py" init`) in the target repo
to populate `~/.crux/env`. See `docs/AGENTS.md` §13.6 for the CLI surface.

## RepoAnalysis Fields
- `tech_stack` — detected technologies
- `languages` — programming languages found
- `frameworks` — detected frameworks (Python, JS, Phoenix, Rails, etc.)
- `structure` — directory structure summary
- `key_files` — important configuration files found
- `hints` — generated hints for future agents about this repo

## When to Use
- Bootstrapping crux in a new project
- Setting up agent teams in a different codebase
- Creating domain-specific crux instances
- Deploying verification capabilities to client repos

## When NOT to Use
- This repo (crux's own dogfood checkout) — the source tree already carries the runtime; nothing to spawn.
- Targets the spawner does not own: it REFUSES to deploy over a pre-existing
  path it did not create (`SpawnTargetConflictError`, naming the conflict and
  the remediation). A pre-rename `.crux/` runtime tree migrates manually:
  `mv .crux .crux-runtime`, then re-run. For a genuinely foreign directory,
  pass `--dir` to choose a different target — never delete blindly.
