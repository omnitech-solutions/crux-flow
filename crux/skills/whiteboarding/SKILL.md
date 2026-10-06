---
name: whiteboarding
description: "Explore an idea through docs-informed dialogue before committing to a design. Capture the exploration for later filing as a brief."
metadata:
  tags: "brainstorming, exploration, design, whiteboarding"
  bundles: "crux-docs"
  risk_level: "low"
  triggers: "let's explore X | brainstorm | whiteboard this | help me think through Y | design X before we build it | I have an idea"
---

# Whiteboarding (docs-aware brainstorming)

<!-- BEGIN GENERATED: runtime-compat -->
## Runtime compatibility

This skill is portable across Claude Code, Codex, and OpenCode. This section overrides platform-specific labels below.

- Before running a command that uses `CRUX_PLUGIN_ROOT`, set it to the installed plugin root. In Claude Code, use the value of `CLAUDE_PLUGIN_ROOT`. In Codex and OpenCode, derive it from the absolute path of this selected `SKILL.md`: the plugin root is the parent of its `skills/` directory. In a source checkout, use the checkout `crux/` directory.
- For project-local skills, use `.claude/skills` in Claude Code, `.agents/skills` in Codex, and `.opencode/skills` in OpenCode, which also reads the singular `.opencode/skill`. Set `CRUX_LOCAL_SKILLS_DIR` to that path before following any command below that uses it.
- Translate Claude Code tool labels such as `Agent`, `Read`, `Write`, `Bash`, `WebSearch`, and `WebFetch` to the matching capability in the current session. Codex names its own capabilities; OpenCode uses the lowercase forms `subagent`, `read`, `edit`, `shell`, `websearch`, and `webfetch`, where `edit` covers both `Edit` and `Write`. Do not attempt to invoke the Claude Code labels as literal commands on another host.
- Install the generated role agents before delegating: `install-codex-agents` in Codex, `install-opencode-agents` in OpenCode. Codex names them `crux_architect`, `crux_brainstormer`, `crux_commander`, `crux_dev_lead`, `crux_developer`, `crux_historian`, `crux_librarian`, `crux_night_gardener`, `crux_reviewer`, and `crux_wayfinder`; OpenCode uses the bare role names `architect`, `brainstormer`, `commander`, `dev-lead`, `developer`, `historian`, `librarian`, `night-gardener`, `reviewer`, and `wayfinder`. If a required role or capability is unavailable, report that truthfully instead of claiming it ran.
- Argument placeholders such as `$adr` and `$book` bind only in Claude Code. On a host without argument binding they are unset — take the value from the user's phrase. The "Fields OpenCode ignores" section of `OPENCODE_GUIDE.md` names the invocation-control fields OpenCode ignores.
<!-- END GENERATED: runtime-compat -->


Turn an idea into a fully-formed design through collaborative dialogue, then
capture it as a **brainstorming session** that flows into the docs tree. This is
crux's replacement for `superpowers:brainstorming`; the difference is that
the output is docs-aware — it lands as a brief, not a loose spec file.

## The method

1. **Explore context first** — read relevant files, run `query-docs`, check the
   docs tree and recent commits. Don't ask what the repo can tell you.
2. **Read the doctrine domains the idea touches** — open
   `docs/adrs/doctrine/index.md` and read every domain the idea bears on
   before any option is drafted. An option that contradicts a live rule is
   either a retirement proposal (name the rule it displaces, as `rule:<slug>`)
   or a mistake; the session says which.
3. **Ask clarifying questions one at a time** — prefer multiple-choice; one
   question per message. Focus on purpose, constraints, success criteria.
4. **Propose 2–3 approaches** with trade-offs; lead with a recommendation and why.
5. **Present the design in sections**, scaled to complexity; get approval per
   section. Cover architecture, components, data flow, error handling, testing.
6. **Self-review the session** before hand-off: scan for placeholders/TBDs,
   internal contradictions, scope creep (does it need decomposition?), and
   ambiguity (could a requirement be read two ways? pick one, make it explicit).
7. **Size the delivery** — name the tier the design needs (`fix-directly`,
   `patch-cycle`, `iterate`, `dev-cycle`) and the checkable reason, per
   `fix-directly`'s sizing test. One instance is a bug, not a class; design for
   a class only when a second, independent instance exists.

## Output: a brainstorming session → a brief

The deliverable is the **session** — problem framing, the options weighed, the
recommendation, and open questions. You do **not** write to `docs/` yourself —
you **return the session as your result**. The **historian** files it into
`docs/inbox/`, where `process-inbox` classifies it as exploration and dispatches
`propose-brief --from-inbox <session-path>`, which carries this session's content
into the brief **body** under `docs/briefs/` (the sanctioned machine-authored
exception, per `docs/AGENTS.md` §2(b)) — the frontmatter is `propose-brief`'s, the
body is your session. The **architect** then consumes that brief to author the ADR
— the explorer never records the decision.

Cite an existing rule as `rule:<slug>`. A rule the session proposes and that does
not exist yet is written in the placeholder form `rule:<new-thing>` — the angle
brackets make it a non-token under the citation grammar, so the brief passes the
citation lint while the rule is still a proposal.

## Rules

- One question at a time. Never batch a wall of questions.
- Never jump to implementation before design approval (the hard gate — see footer).
- Decompose a too-large idea into sub-projects before designing the first one.
- The session is exploration, not a decision — never author an ADR here.

## Rationalization table

| Excuse | Reality |
|--------|---------|
| "This is simple — I'll skip the design and just build." | Every project gets a design. Simple ones hide the worst assumptions. Present a design, get approval. |
| "I'll ask all my questions at once to save round-trips." | One at a time. A wall of questions gets shallow answers and buries the important fork. |
| "Each of these bugs is really a class — I'll design for the class." | One instance is a bug. Fix the instance; record the class as an open question. A class earns a design at the second, independent instance — the same recurrence floor a retrospective holds a skill proposal to. |
| "The exploration is clearly a decision — I'll write the ADR." | Whiteboarding explores; the architect decides. Hand off the session; never author the ADR. |
| "I'll write the brief into docs/briefs/ directly." | The session flows through `docs/inbox/` → `process-inbox` → `propose-brief --from-inbox`. One intake door; the historian files it, and `--from-inbox` carries your session into the brief body (§2(b)). |

## Unattended mode

When no user is present (invoked by an automated session such as the night
gardener), the one-question-at-a-time dialogue loop becomes **self-answering**
with mandatory assumption flagging:

- Every answer the method would have asked for is recorded in the session's
  Open questions section as an explicit row:
  `ASSUMED: <question> → <answer> (why)`.
- The session header carries `mode: unattended`.
- The hard gate (no implementation before approval) is satisfied structurally:
  the session routes inbox → brief → human. The owner approves before anything
  is built.
- **An unattended session with zero flagged assumptions is a red flag, not a
  clean run.** A real design exploration always encounters forks where a human
  would be asked to choose. If you flagged nothing, you skimmed.
- **The invoker writes the returned session** — the whiteboarding skill never
  writes to `docs/` itself. In unattended use, the **invoker** (e.g. the
  night-gardener) writes the session into `docs/inbox/` as its own drop,
  named according to the invoker's naming convention (e.g.
  `docs/inbox/gardener-whiteboard-<slug>.md`).

## Hard gate

**Do NOT write code, scaffold a project, or take any implementation action until
a design has been presented and the user has approved it.** This applies to every
project regardless of perceived simplicity — "simple" projects are where
unexamined assumptions cause the most wasted work. This gate governs the entire
method above: approval is the precondition for any implementation hand-off.
