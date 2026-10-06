---
name: install-opencode-agents
description: "Install or refresh crux's OpenCode role agents in a target project. Preserve conflicting roles unless replacement is authorized."
disable-model-invocation: true
metadata:
  tags: "opencode, agents, installation, roles"
  bundles: "crux-infrastructure, crux-docs"
  risk_level: "medium"
  triggers: "install opencode agents | install crux agents for opencode | refresh the opencode agents"
---

# Install OpenCode Agents

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

OpenCode V2 reads project agents from `.opencode/agents/*.md`; it does not consume
Crux's Claude Code agent Markdown unchanged — the frontmatter needs the
`mode`/`model`/`permissions` projection locked by the OpenCode agent contract.
This skill writes the ten projected roles into the target repo:

`architect`, `brainstormer`, `commander`, `dev-lead`, `developer`,
`historian`, `librarian`, `night-gardener`, `reviewer`, and `wayfinder`.

The generated files are `<role>.md` — bare role names, not namespaced the way
the Codex projection namespaces to `crux_<role>`. OpenCode derives an agent's
name from its filename, and the collision question was settled by *choosing*
non-colliding role names (`scout` became `wayfinder` to clear OpenCode's
built-in) rather than by prefixing, so the role has one name across both Claude
Code and OpenCode.

`commander` and `night-gardener` are projected as `mode: all` (usable as
primary agents); the other eight are `mode: subagent`. Tool grants become an
ordered OpenCode `permissions` array under last-match-wins, with `Edit` and
`Write` jointly gating the single `edit` action.

This skill installs **project-scoped** agents. For a machine-wide install
across every project, see the OpenCode setup section of the repository README,
which symlinks the generated tree under `~/.config/opencode/` instead. Follow
the path that README gives; a machine-wide install runs no crux code, so
nothing here can check or correct it.

## Install

<!-- Provenance: OpenCode has no Crux marketplace package — support is manual
and preview-grade, and a public OpenCode distribution channel is a deliberately
deferred decision. So unlike install-codex-agents, this skill does NOT assume a
plugin-manager install; it assumes the user has a crux clone or plugin
directory on disk and that this SKILL.md was reached through it. The
`.opencode/agents/` project path is an external OpenCode contract owned by the
OpenCode project, not by crux — track it against upstream. V1 tolerated both
the singular and the plural spelling; under V2 the plural directory wins
silently when both hold the same filename, measured against `opencode2`
v0.0.0-beta-18866 on 2026-09-02, which is why crux writes the plural and
refuses a populated singular rather than tolerating both.
-->

Let `<skill-dir>` be the directory containing this selected `SKILL.md`, then run
the sibling installer from the **target repository root**:

```bash
python3 <skill-dir>/scripts/install.py --repo-root "$PWD"
```

The first install writes the ten roles. A later run refuses if an existing
crux-managed `<role>.md` differs, so a locally modified role is never silently
lost. The installer also refuses fail-closed (never writing) if a crux-managed
entry is a symlink, so it can never clobber the link's target inside or outside
the repo; it likewise refuses if `.opencode/agents/` resolves outside the repo
root. Review the diff, then refresh deliberately:

```bash
python3 <skill-dir>/scripts/install.py --repo-root "$PWD" --force
```

### The runner preflight

Before writing anything, the installer probes each candidate runner with
`--version` and classifies it by the major version it reports, never by the name
of its executable. OpenCode 2.0.3 installs as `opencode`, and an upgraded machine
may keep an `opencode2` shim onto the same binary, so the name discriminates
nothing. Candidates are `opencode` then `opencode2`, or the single runner named
by `CRUX_OPENCODE_BIN` when that is set.

Each candidate gets one of three verdicts, and the strongest one any candidate
reached decides the run:

| Verdict | When | What the installer does |
|---|---|---|
| `compatible` | one version token, major 2, exit 0 | writes |
| `known-incompatible` | any token below major 2, whatever the exit status | refuses; `--assume-compatible` cannot override it |
| `unestablished` | nothing resolved, nothing parsed, or the line is ambiguous | refuses, unless `--assume-compatible` binds it to a resolved candidate |

The refusal exists because the generated agents use the V2 `permissions` array,
and a runner older than 2 reads them with every `deny` silently dropped.

This is a **necessary condition, not a sufficient one**. A `compatible` verdict
proves a 2.x runner answered on this machine; it does not prove the runner that
later reads the projection is that one. On a machine carrying both a 2.x and an
older runner you can pass the preflight and then invoke the older one by hand —
accepted residual risk, covered by documentation rather than by this check. The
installer records its verdict and every candidate in the JSON it prints, so a
run that proceeded on an assertion says so.

`CRUX_OPENCODE_BIN` names the runner when it is installed under another name.
`CRUX_OPENCODE2_BIN` is honoured as a deprecated alias, and a run that reads it
says so in its output.

### The legacy `.opencode/agent/` directory

OpenCode V2 reads the plural `.opencode/agents/`. When both directories hold a
file of the same name the plural one wins silently — no merge, no warning — so a
leftover singular copy is an invisible shadow rather than a visible conflict.

The installer therefore refuses, with a non-zero exit naming the files, when
`.opencode/agent/` holds at least one file whose name is in the role roster.
Move them and proceed with:

```bash
python3 <skill-dir>/scripts/install.py --repo-root "$PWD" --migrate-legacy-agent-dir
```

**Known limitation, stated rather than worked around:** membership is decided by
roster filename alone, because crux stamps no provenance marker on a projected
agent file. Your own `.opencode/agent/architect.md` is indistinguishable from a
crux-managed one and does trip the guard.

If any file the migration would move already carries that name in
`.opencode/agents/`, the migration moves **nothing**, exits non-zero and names
every colliding file — the refusal is over the whole move, so a half-migrated
directory cannot occur. The destination copy is the one OpenCode resolves, so
overwriting it would replace the live file with the stale shadow the guard
exists to catch. Review them, then add `--force` to overwrite.

Fully quit and restart the OpenCode host after installation — OpenCode loads
config once at startup and does not hot-reload. For the Zed / ACP integration,
restart Zed itself; opening a new thread is not sufficient. Then ask OpenCode to
spawn a role by its bare name, for example `reviewer` or `developer`.

## Verification

- [ ] `.opencode/agents/` contains the ten generated `<role>.md` files.
- [ ] `.opencode/agent/` — the legacy singular directory — holds no crux role file.
- [ ] Existing non-Crux agent files remain unchanged.
- [ ] The installer returned JSON with `written` and no unreviewed conflicts.
- [ ] `opencode debug agents` shows the ten Crux roles, with `commander` and
      `night-gardener` as `all` and the other eight as `subagent`. There is no
      `opencode agent list` and no `opencode debug skill`.

## Guardrails

- Do not hand-edit the generated `<role>.md` files. Change `crux/agents/*.md` in
  the Crux source, regenerate, and refresh intentionally.
- Do not use `--force` to bypass a target project's local role changes without
  reviewing them first.
- The installer only ever touches the ten current role filenames. A project's
  own `.opencode/agents/*.md` files are left alone — and so is a file left behind
  by a role Crux has since renamed. After an upstream rename, delete the old
  file by hand; the installer will not do it for you.
- A project-scoped agent shadows a global agent of the same name. Installing
  here overrides any same-named role in `~/.config/opencode/agents/` for this
  project.
- `--force` overrides the destination-collision refusal. It does NOT override
  path containment: a legacy file resolving outside the repo root is refused
  whether or not you pass it.
- `--assume-compatible` overrides only an `unestablished` verdict, and only when
  a candidate resolved. It cannot override `known-incompatible`, and it is not a
  way past a machine with no runner on it. Pass it when you know the resolved
  runner reads a V2 projection and it simply reports a version this release does
  not recognise — never to quiet a refusal you have not read.
