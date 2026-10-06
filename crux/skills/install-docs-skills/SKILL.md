---
name: install-docs-skills
description: "Identify which Crux plugin version is active in this session; provide read-only Crux installation, upgrade, and schema guidance when requested."
metadata:
  tags: "installation, plugin, distribution"
  bundles: "crux-docs"
  risk_level: "low"
  triggers: "which crux version is active | what crux plugin is loaded | install docs skills | add this plugin | set up crux | upgrade docs suite"
  routing_note: "Read-only active identity; install and upgrade instructions load on request."
---

# Install Docs Skills

<!-- BEGIN GENERATED: runtime-compat -->
## Runtime compatibility

This skill is portable across Claude Code, Codex, and OpenCode. This section overrides platform-specific labels below.

- Before running a command that uses `CRUX_PLUGIN_ROOT`, set it to the installed plugin root. In Claude Code, use the value of `CLAUDE_PLUGIN_ROOT`. In Codex and OpenCode, derive it from the absolute path of this selected `SKILL.md`: the plugin root is the parent of its `skills/` directory. In a source checkout, use the checkout `crux/` directory.
- For project-local skills, use `.claude/skills` in Claude Code, `.agents/skills` in Codex, and `.opencode/skills` in OpenCode, which also reads the singular `.opencode/skill`. Set `CRUX_LOCAL_SKILLS_DIR` to that path before following any command below that uses it.
- Translate Claude Code tool labels such as `Agent`, `Read`, `Write`, `Bash`, `WebSearch`, and `WebFetch` to the matching capability in the current session. Codex names its own capabilities; OpenCode uses the lowercase forms `subagent`, `read`, `edit`, `shell`, `websearch`, and `webfetch`, where `edit` covers both `Edit` and `Write`. Do not attempt to invoke the Claude Code labels as literal commands on another host.
- Install the generated role agents before delegating: `install-codex-agents` in Codex, `install-opencode-agents` in OpenCode. Codex names them `crux_architect`, `crux_brainstormer`, `crux_commander`, `crux_dev_lead`, `crux_developer`, `crux_historian`, `crux_librarian`, `crux_night_gardener`, `crux_reviewer`, and `crux_wayfinder`; OpenCode uses the bare role names `architect`, `brainstormer`, `commander`, `dev-lead`, `developer`, `historian`, `librarian`, `night-gardener`, `reviewer`, and `wayfinder`. If a required role or capability is unavailable, report that truthfully instead of claiming it ran.
- Argument placeholders such as `$adr` and `$book` bind only in Claude Code. On a host without argument binding they are unset — take the value from the user's phrase. The "Fields OpenCode ignores" section of `OPENCODE_GUIDE.md` names the invocation-control fields OpenCode ignores.
<!-- END GENERATED: runtime-compat -->


## Choose the request

For “which Crux version is active?”, “what Crux plugin is loaded?”, or an explicit invocation with no install request, follow **Active identity** below. Do not read the installation reference for an identity-only request. A question about a Node, Python, or other non-Crux version does not invoke this skill.

For an explicit or natural Crux install, upgrade, or tree-schema guidance request, read [`references/install-and-upgrade.md`](references/install-and-upgrade.md), resolved relative to this selected `SKILL.md`. It retains the host-specific commands, compatibility checks, recovery guidance, hand-off, and logging rules. If the reference is missing or unreadable, report that fact and do not improvise install or migration steps from memory or a different cached copy. For “set up Crux”, report identity first and clarify whether the user means plugin installation or project-tree initialization.

This skill does not install the plugin or initialize a tree. Marketplace commands are instructions for the user to run in the host UI. Do not install, upgrade, edit marketplace settings, or invoke `init-docs` merely because this skill loaded.

## Active identity (read-only)

1. Obtain the absolute path of the **selected loaded Crux `SKILL.md`** from the host invocation, or a host load trace that identifies the selected copy. Derive the plugin root as the parent of that file's `skills/` directory. Claude Code may provide `CLAUDE_PLUGIN_ROOT` inside the loaded skill; an empty variable in an unrelated shell does not prove anything. Do not infer selection from this repository, a marketplace registry, or a cache listing.
2. At that evidenced root, read `plugin.json`. Confirm `name` is `crux` and read `version`; `schema_version` there is the skill-frontmatter contract, not the target documentation-tree schema. If the manifest is missing, malformed, names another plugin, or lacks a valid version, report the fault and leave active identity unverified. Do not substitute a cached manifest.
3. Report the active version with the selected path or load-trace evidence. If no selected root or trace is available, say **“Active Crux version unverified in this session.”** Do not guess. If the user also asks about installed copies, a valid marketplace cache manifest may identify a **cached candidate**; label it candidate-only, even if it has a newer version or a disabled registration. It never proves what this session loaded.

The identity path reads only evidence. It does not execute an installer, recommend an upgrade from cache alone, compare a candidate's tree compatibility as if it were active, or bootstrap documentation. The detailed reference is loaded only for an install, upgrade, or schema request.
