---
name: log-work
description: "Record completed work and its context in the project journal, append the operation log, and regenerate the journal index."
metadata:
  tags: "journal, work-log, narrative"
  bundles: "crux-docs"
  risk_level: "low"
  triggers: "log work | journal this | record progress | log this"
  routing_note: "Categorization required."
---

# Log Work

<!-- BEGIN GENERATED: runtime-compat -->
## Runtime compatibility

This skill is portable across Claude Code, Codex, and OpenCode. This section overrides platform-specific labels below.

- Before running a command that uses `CRUX_PLUGIN_ROOT`, set it to the installed plugin root. In Claude Code, use the value of `CLAUDE_PLUGIN_ROOT`. In Codex and OpenCode, derive it from the absolute path of this selected `SKILL.md`: the plugin root is the parent of its `skills/` directory. In a source checkout, use the checkout `crux/` directory.
- For project-local skills, use `.claude/skills` in Claude Code, `.agents/skills` in Codex, and `.opencode/skills` in OpenCode, which also reads the singular `.opencode/skill`. Set `CRUX_LOCAL_SKILLS_DIR` to that path before following any command below that uses it.
- Translate Claude Code tool labels such as `Agent`, `Read`, `Write`, `Bash`, `WebSearch`, and `WebFetch` to the matching capability in the current session. Codex names its own capabilities; OpenCode uses the lowercase forms `subagent`, `read`, `edit`, `shell`, `websearch`, and `webfetch`, where `edit` covers both `Edit` and `Write`. Do not attempt to invoke the Claude Code labels as literal commands on another host.
- Install the generated role agents before delegating: `install-codex-agents` in Codex, `install-opencode-agents` in OpenCode. Codex names them `crux_architect`, `crux_brainstormer`, `crux_commander`, `crux_dev_lead`, `crux_developer`, `crux_historian`, `crux_librarian`, `crux_night_gardener`, `crux_reviewer`, and `crux_wayfinder`; OpenCode uses the bare role names `architect`, `brainstormer`, `commander`, `dev-lead`, `developer`, `historian`, `librarian`, `night-gardener`, `reviewer`, and `wayfinder`. If a required role or capability is unavailable, report that truthfully instead of claiming it ran.
- Argument placeholders such as `$adr` and `$book` bind only in Claude Code. On a host without argument binding they are unset — take the value from the user's phrase. The "Fields OpenCode ignores" section of `OPENCODE_GUIDE.md` names the invocation-control fields OpenCode ignores.
<!-- END GENERATED: runtime-compat -->


## Purpose and shared obligations

The journal records why work happened and what was learned. The operation log records what a skill did. Both preserve prior entries. Journal only work a later reader would need to understand; routine operations need a log entry only. This skill uses the bundled writer for mechanical validation, journal-index regeneration and log placement. The caller authors the truthful body and references; a successful writer result attests to writes, not the underlying work's completion.

Resolve the repository and configured `<docs_dir>` before acting. Read `<docs_dir>/objectives.md` and the applicable operational rules in `<docs_dir>/AGENTS.md`; its §6 owns the canonical log-operation vocabulary. Preserve the assignment's Evidence and Constraints and the run's authorization boundary. An operation must never edit an earlier record.

## Select the operation

Choose the mode first and **read its exact local reference before the first side effect**. If that reference is missing or unreadable, refuse the operation and name the missing path. Do not reconstruct its procedure from memory.

| Invocation | Selected procedure | Default |
|---|---|---|
| Interactive or explicit `--journal` | `references/journal.md` | Journal, index, and one `journal` log operation |
| `--silent` without `--journal` | `references/log-only.md` | One caller-owned log operation |

`--silent` suppresses prompting; it does not itself request journaling. An explicit `--journal` selects the journal branch even in silent mode. Interactive `log-work` defaults to journaling. In silent mode the caller supplies category and subject for a journal entry, or a valid `--log-op`, subject and one-line body for log-only. `--refs` carries wiki-links and `rule:<slug>` citations. Cite promptbooks without `active/` or `archive/` so references survive archival.

Map an existing `--body` skill input to the writer's `--body-file` or `--body-stdin`; never interpolate it into a shell command. Journal mode records op `journal` and ignores any `--log-op` supplied by its caller. Log-only mode requires the caller's canonical `--log-op` and never substitutes one.

The caller supplies a stable, timezone-aware ISO 8601 timestamp with an explicit offset before invoking the writer. Retain the exact request and its original offset for retry. The caller-supplied civil time and offset are the local time authority; the writer never substitutes the host timezone or reads an invented tree timezone. It drops seconds while preserving the civil minute and offset. The writer reports JSON with `status` (`complete`, `partial`, `refused`), `mode`, `timestamp`, `warnings`, `recorded`, `pending`, `written`, and `error`. `recorded` means the surface holds the requested content on disk; the writer commits nothing and leaves files uncommitted. Exit 0 means complete; exit 1 reports refusal or partial; exit 2 means no usable report. Surface warnings and every partial result. On partial, retry the identical timestamped request to complete missing surfaces without a duplicate. Never describe a partial or refused write as complete.

## When to use

Use this skill for "log work", "journal this", "record progress", or a caller's deliberate log operation. Route decision authoring to `propose-adr`, drift checks to `audit-docs`, and book state changes to `run-promptbook`. If the user asks for an earlier entry to be corrected, append a new entry that references it.
