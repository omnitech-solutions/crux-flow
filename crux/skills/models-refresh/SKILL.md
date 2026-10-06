---
name: models-refresh
description: "Discover model facts, propose bounded native/API role changes, then apply a reviewed, digest-bound proposal with rollback."
metadata:
  tags: "models, policy, maintenance"
  bundles: "crux-flow"
  risk_level: "medium"
  triggers: "update available models | use cheaper competent models | refresh crux flow models"
---

# Model maintenance

Use the same `models` CLI as the operator. `models list` and `models resolve` are offline inspection. `models refresh --provider openrouter|codex|claude` discovers bounded public/documented metadata or the installed native bundled catalog, never authentication stores. Discovery does not activate anything. Native host availability, API availability and account entitlement are different observations.

Propose exact bindings against discovered evidence. Compare effective changes across relevant modes and hosts, preserve explicit pins, reject unsupported effort or required-capability loss, and show price currency/units or unknown. AI selection is a recommendation; deterministic validation owns activation. `models apply --proposal FILE --yes` authorizes only the exact digest-bound plan; no stale plans, plugin-cache edits or widened retention/provider permissions. All ordinary coding and validation stays offline.

Fork assignment overrides are in one scoped binding document; canonical upstream model facts stay in Crux's existing catalog and router. Fork refresh must not change upstream-mode defaults. Apply updates bindings and affected scoped generated roles in one recoverable transaction. A new host session may be needed; report generated versus runtime-observed settings separately. `models rollback` restores the compatible snapshot only while files remain managed and unchanged.

Unattended activation is opt-in, scope-bound and requires exact approved models, vendors, capabilities, price ceilings, pin preservation and explicit data-retention approval. Unknown material facts refuse unattended activation. Optional provider benchmarks require explicit usage authorization and use synthetic fixtures, never project source as a discovery probe.

<!-- BEGIN GENERATED: runtime-compat -->
## Runtime compatibility

This skill is portable across Claude Code, Codex, and OpenCode. This section overrides platform-specific labels below.

- Before running a command that uses `CRUX_PLUGIN_ROOT`, set it to the installed plugin root. In Claude Code, use the value of `CLAUDE_PLUGIN_ROOT`. In Codex and OpenCode, derive it from the absolute path of this selected `SKILL.md`: the plugin root is the parent of its `skills/` directory. In a source checkout, use the checkout `crux/` directory.
- For project-local skills, use `.claude/skills` in Claude Code, `.agents/skills` in Codex, and `.opencode/skills` in OpenCode, which also reads the singular `.opencode/skill`. Set `CRUX_LOCAL_SKILLS_DIR` to that path before following any command below that uses it.
- Translate Claude Code tool labels such as `Agent`, `Read`, `Write`, `Bash`, `WebSearch`, and `WebFetch` to the matching capability in the current session. Codex names its own capabilities; OpenCode uses the lowercase forms `subagent`, `read`, `edit`, `shell`, `websearch`, and `webfetch`, where `edit` covers both `Edit` and `Write`. Do not attempt to invoke the Claude Code labels as literal commands on another host.
- Install the generated role agents before delegating: `install-codex-agents` in Codex, `install-opencode-agents` in OpenCode. Codex names them `crux_architect`, `crux_brainstormer`, `crux_commander`, `crux_dev_lead`, `crux_developer`, `crux_historian`, `crux_librarian`, `crux_night_gardener`, `crux_reviewer`, and `crux_wayfinder`; OpenCode uses the bare role names `architect`, `brainstormer`, `commander`, `dev-lead`, `developer`, `historian`, `librarian`, `night-gardener`, `reviewer`, and `wayfinder`. If a required role or capability is unavailable, report that truthfully instead of claiming it ran.
- Argument placeholders such as `$adr` and `$book` bind only in Claude Code. On a host without argument binding they are unset — take the value from the user's phrase. The "Fields OpenCode ignores" section of `OPENCODE_GUIDE.md` names the invocation-control fields OpenCode ignores.
<!-- END GENERATED: runtime-compat -->
