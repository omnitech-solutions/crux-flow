---
name: flow
description: "Implement a requested change through Crux with proportionate execution, durable acceptance and independent review. Also handles implement this, build the feature, fix this, continue the run, and mode changes."
metadata:
  tags: "workflow, execution, modes"
  bundles: "crux-flow, crux-docs"
  risk_level: "medium"
  triggers: "implement this | build the feature | fix this | run crux flow | use aggressive mode | use balanced mode | use thorough mode | switch to aggressive | continue the run"
---

# Crux Flow

## Invocation and authority

A read-only question stays read-only: inspect and answer without creating files.
For a code change, invoke the installed `bin/crux-flow` entrypoint. `mode show --resolved --host HOST --json` resolves invocation, project, personal and shipped preferences. Aggressive is the default for new work; `rapid` is its alias. A resumed run uses its frozen policy and resources. Never infer a fork mode for historical upstream records.

This is an extension of `run-promptbook`, not another promptbook format. Author a finite ordinary non-cycle book through `run start --spec SPEC.json`; the spec declares the goal, outcomes and required checks. Each outcome includes Outcome, Evidence, Constraint, expected writes and any prerequisite. Keep the specification in the project so a fresh session can resolve its exact check commands without copying arguments or secrets into the run. The CLI uses Crux's documentation resolver, numbering, schema, hash and run writer. It creates one real prompt per outcome/check/review rather than hiding multiple outcomes in a mandatory one-prompt wrapper.

Primary session implements and integrates. Aggressive permits no implementation delegate and requires one independent reviewer with one targeted repair/re-review cycle. Balanced allows two independent implementation delegates, one reviewer and at most one justified specialist. Thorough allows four delegates and up to three distinct relevant reviewers. Caps are maxima, not quotas. Modes never broaden host permissions. Record owned attempts before invocation; unmediated native delegation remains cooperative and must not be described as mechanically counted.

## Execution and continuation

Read `run status --file RUN` and perform the next eligible existing prompt. Advance it through `run advance`; the original Crux writer applies the same acceptance gate. Checkpoints are not terminal results. Continue independent implementation despite a final-review outage. Classify findings as blocking-now, blocking-finalization or non-blocking. Record assumptions for reversible choices; ask only at a material outcome, destructive, credential, payment, security or external authorization boundary.

A run returns control only when COMPLETED, BLOCKED, BUDGET_EXHAUSTED or CANCELLED, or at a real authorization boundary. A model turn ending is not run completion. `run drive` can own consecutive native CLI turns without a daemon. Desktop autostart is unsupported unless the host actually supplies it; never promise that a skill alone can restart a closed conversation. `run resume` emits the next exact work from the existing record.

Default elapsed budgets remain 20, 60 and 120 minutes. A budget cannot waive acceptance or silently increase itself. An explicit owner transition changes the remaining strategy, preserves prior evidence and elapsed time, and does not reset attempt counters. Keep upstream cycles on their original path; explicitly supersede an active legacy run into a linked ordinary book rather than relabel its frozen history.

## Evidence and review

Use `run check --file RUN --label LABEL` (from its frozen project recipe), or `--argv-json` for an explicitly supplied matching argument vector to execute a declared check. Command identity, input/environment/toolchain fingerprints, bounded output digests and observed exit status form its receipt. Check input changes invalidate the receipt; its own run metadata and deterministic book pointer do not. Unknown dependencies require a broad input set or rerun. A self-reported passed label is not execution evidence.

The independent reviewer receives the complete acceptance contract, changed paths and dominant risk lens: security, compatibility, concurrency, grounding or injection. Do not dispatch N/A reviewers or rerun an identical full suite merely to count a review. Capture the review as an explicitly external attestation; the record does not authenticate human or model identity. Source changes invalidate it. Fix material findings and request only the necessary targeted re-review within the retained budget.

A required outcome cannot disappear through skip/defer. Owner narrowing requires an explicit reason and preserved disposition. A replacement must migrate controlled consumers, delete obsolete implementation/dependencies/configuration/tests/docs, and end with one authoritative path. Retain a compatibility adapter only for an identified external consumer, persistent-data migration, rolling deployment or explicit rollback requirement; record consumer, owner and removal condition. Do not import Prompt Magic, another runtime, per-task tags, fixed every-N audits or speculative fallback selectors.

## Reporting

Milestone updates only in aggressive mode. Report outcome, actual tests, material judgments and remaining runtime observations. Required deliverables outrank incidental cleanup; supporting work goes first only when it demonstrably unblocks or verifies them. Update affected authored documentation and use the existing log-work skill before final verification; return its real artifact paths on the corresponding prompt. Use existing Crux knowledge and archive tools for their owned responsibilities. Never invent evidence or present static installation as runtime loading.

<!-- BEGIN GENERATED: runtime-compat -->
## Runtime compatibility

This skill is portable across Claude Code, Codex, and OpenCode. This section overrides platform-specific labels below.

- Before running a command that uses `CRUX_PLUGIN_ROOT`, set it to the installed plugin root. In Claude Code, use the value of `CLAUDE_PLUGIN_ROOT`. In Codex and OpenCode, derive it from the absolute path of this selected `SKILL.md`: the plugin root is the parent of its `skills/` directory. In a source checkout, use the checkout `crux/` directory.
- For project-local skills, use `.claude/skills` in Claude Code, `.agents/skills` in Codex, and `.opencode/skills` in OpenCode, which also reads the singular `.opencode/skill`. Set `CRUX_LOCAL_SKILLS_DIR` to that path before following any command below that uses it.
- Translate Claude Code tool labels such as `Agent`, `Read`, `Write`, `Bash`, `WebSearch`, and `WebFetch` to the matching capability in the current session. Codex names its own capabilities; OpenCode uses the lowercase forms `subagent`, `read`, `edit`, `shell`, `websearch`, and `webfetch`, where `edit` covers both `Edit` and `Write`. Do not attempt to invoke the Claude Code labels as literal commands on another host.
- Install the generated role agents before delegating: `install-codex-agents` in Codex, `install-opencode-agents` in OpenCode. Codex names them `crux_architect`, `crux_brainstormer`, `crux_commander`, `crux_dev_lead`, `crux_developer`, `crux_historian`, `crux_librarian`, `crux_night_gardener`, `crux_reviewer`, and `crux_wayfinder`; OpenCode uses the bare role names `architect`, `brainstormer`, `commander`, `dev-lead`, `developer`, `historian`, `librarian`, `night-gardener`, `reviewer`, and `wayfinder`. If a required role or capability is unavailable, report that truthfully instead of claiming it ran.
- Argument placeholders such as `$adr` and `$book` bind only in Claude Code. On a host without argument binding they are unset — take the value from the user's phrase. The "Fields OpenCode ignores" section of `OPENCODE_GUIDE.md` names the invocation-control fields OpenCode ignores.
<!-- END GENERATED: runtime-compat -->
