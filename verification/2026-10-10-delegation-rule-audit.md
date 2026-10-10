# Delegation rule audit: "one orchestrator; every delegate is a leaf"

Date 2026-10-10. Read-only audit of /Users/desoleary/dev/omnitech-solutions/crux-flow (working tree with the
orchestrator's uncommitted edits) against upstream Crux 3.25.1 (`~/.claude/plugins/cache/crux/crux/3.25.1`).
Paths are relative to the repo root unless prefixed `UP:` (upstream cache). No test was run (running pytest would
write caches); claims about behaviour come from reading the code.

Fact that anchors everything: **every `crux/agents/*.md` is byte-identical to upstream 3.25.1** (checked with `cmp`,
all ten "same"). They are upstream-owned. Flow never edits them; it re-projects them in
`crux/scripts/crux/flow/hosts.py::roles`.

---------------------------------------------------------------------------------------------------------------

## 1. THE MAP

Legend. Mode: U = upstream only, F = the three Flow modes, ALL = both. Origin: A = authored, G = generated.
Owner: UP = upstream-owned, FL = Flow-owned.

| file:line | what it says | mode | A/G | owner |
|---|---|---|---|---|
| crux/agents/commander.md:4 | tools `Agent(architect), Agent(brainstormer), Agent(dev-lead), Agent(historian), Agent(librarian), Agent(night-gardener), Agent(reviewer), Agent(wayfinder)` | U (not generated in F: hosts.py:64) | A | UP |
| crux/agents/commander.md:16-20 | "You orchestrate ... You never do leaf work ... You delegate every unit" | U | A | UP |
| crux/agents/commander.md:41-42 | implementation -> dev-lead "(who may fan out to developers)" | U | A | UP |
| crux/agents/commander.md:82-117 | crafted context, three statements per dispatch, "Spawn cap. One agent per genuinely independent unit", carve-out for mandatory reviews | U | A | UP |
| crux/agents/dev-lead.md:4 | tools `Agent(developer), Agent(historian), Agent(reviewer), Agent(wayfinder)` | U and F (see hosts.py:84) | A | UP |
| crux/agents/dev-lead.md:17-18 | "fan out independent units to developers via the Agent tool - you are the one subagent allowed to delegate" | U (body replaced in F: hosts.py:68-70) | A | UP |
| crux/agents/dev-lead.md:55-66 | "Fan-out discipline (the one sanctioned re-delegation)", worktree isolation, one developer per group | U | A | UP |
| crux/agents/dev-lead.md:126-127 | "dispatch the historian via Agent" for any docs write | U | A | UP |
| crux/agents/developer.md:4, 19 | no Agent in tools; "You are a leaf - you have no Agent and cannot re-delegate" | ALL | A | UP |
| crux/agents/night-gardener.md:4 | tools `Agent(historian), Agent(wayfinder), Agent(librarian)` | U and F (hosts.py:84) | A | UP |
| crux/agents/night-gardener.md:72-75 | "Every delegation or forked skill invocation includes the resolved objectives ... Require recipients to carry this context through further delegation" | U | A | UP |
| crux/agents/architect.md:63-64 | "You do not delegate (no Agent); the commander dispatches you" | ALL | A | UP |
| crux/agents/reviewer.md:4 | no Agent; line 43: "The delegator that commissioned the work commissions you" | ALL | A | UP |
| crux/agents/wayfinder.md:61-63 | "non-delegating: you hold no ... dispatch" | ALL | A | UP |
| crux/agents/{historian,librarian,brainstormer}.md:4 | no Agent in tools | ALL | A | UP |
| crux/catalog/flow-roles.json:4 | dev-lead: "Delegate only independent non-overlapping work allowed by host capabilities and recorded caps." | F | A | FL |
| crux/catalog/flow-roles.json:3-11 | the other eight role purposes (none mentions delegation) | F | A | FL |
| crux/scripts/crux/flow/hosts.py:38-47 (`_instructions`) | body of every F role: "Primary session owns orchestration"; "Do not delegate from an unsupported child context"; "do not start another run; execute only that unit" | F | G | FL |
| hosts.py:64 | `if not upstream and name=='commander': continue` - F modes ship nine roles, no commander | F | code | FL |
| hosts.py:68-70 | F: role body replaced, `skills=('flow',)`; U: source body and skills untouched | ALL | code | FL |
| hosts.py:81-90 (Claude) | `tools` copied from the upstream head with only the `crux-flow-` prefix added to `Agent(...)`; no stripping; `skills: ['flow']` in F, `head['skills']` in U | ALL | G | FL |
| hosts.py:91-98 (OpenCode) | `opencode_agents.transform` output; `Agent(role)` grants become subagent allow rules, prefix added | ALL | G | FL over UP transform |
| hosts.py:99-110 (OMP) | `Agent(x)` -> `spawns` list plus `task` tool | ALL | G | FL |
| hosts.py:72-80 (Codex) | `codex_agents.render_agent`; no Agent concept | ALL | G | FL over UP renderer |
| crux/scripts/codex_agents.py:323-335 | every Codex role: "Do not spawn subagents ... prevents nested delegation. Return any proposed delegation to the parent" and "Codex execution override (binding) ... do not invoke Agent" | ALL (Codex) | G | UP |
| crux/scripts/opencode_agents.py:29-33 | restricted `Agent(role)` -> `"*"` deny then per-role allow; no grant -> `"*"` deny | ALL (OpenCode) | G | UP |
| hosts.py:114 and policy.py:302 | `enforcement: native_delegation: cooperative`; `permissions: host-specific projection` | ALL | G | FL |
| crux/catalog/flow-policy.json:20,39,58,77 | `delegates`: aggressive 0, balanced 2, thorough 4, upstream null | per mode | A | FL |
| flow-policy.json reviewers/repair_cycles/council_rounds | aggressive 1/1/0, balanced 2/2/1, thorough 3/3/2, upstream null | per mode | A | FL |
| policy.py:119-136 | schema: every F mode must give integers for `delegates`, `reviewers`, ...; upstream must give null | ALL | code | FL |
| policy.py:242 | default mode `aggressive`; `rapid` alias (policy.py:110) | n/a | code | FL |
| policy.py:250-251, 264, 275, 280 | upstream mode: models forced to `upstream`, no fork bindings, only invocation pins | U | code | FL |
| crux/scripts/crux/flow/records.py:12, 143-151 | `COUNTED={'delegate':'delegates','review':'reviewers','review-repair':'repair_cycles','council':'council_rounds'}`; `attempt()` raises "whole-run invocation cap reached" when the recorded count reaches the cap | F | code | FL |
| crux/scripts/crux/flow/cli.py:52 | `run attempt --kind {implementation,delegate,review,review-repair,council,test,generation,bookkeeping}` | F | code | FL |
| crux/scripts/crux/flow/workflow.py:57 | every unit prompt is `Outcome: ... Evidence: ... Constraint: ...`; default constraint "Preserve established supported contracts." | F | code | FL |
| workflow.py:90 | review prompt: "A separate reviewer checks ... No N/A fanout; no recursive skill building." | F | code | FL |
| crux/scripts/crux/flow/continuation.py:43 | on stagnation: "Flatten delegation and perform the next concrete action." | F | code | FL |
| crux/scripts/crux/flow/api_calls.py:27,44 | owned API helper and `council` calls are recorded attempts (kind generation / council) | F | code | FL |
| crux/skills/flow/SKILL.md:20 | modes' caps; "Record owned attempts before invocation; unmediated native delegation remains cooperative and must not be described as mechanically counted." | F (read in ALL) | A | FL |
| crux/skills/flow/SKILL.md:22 | NEW: "One orchestrator; every delegate is a leaf ... only a role whose tool list names delegates may delegate (commander, dev-lead, night-gardener)" | written unconditionally | A | FL |
| crux/skills/flow/SKILL.md:36 | reviewer receives the full acceptance contract; "Do not dispatch N/A reviewers" | F | A | FL |
| crux/skills/flow/references/orchestration.md:6-24 | NEW: "The one rule" incl. the same commander/dev-lead/night-gardener sentence | unconditional | A | FL |
| FLOW_GUIDE.md:209-217 | modes table and "Caps are maxima, not dispatch quotas" | ALL | A | FL |
| FLOW_GUIDE.md:321-331 | Delegates: `preload` / `skills:` injection per host | F | A | FL |
| FLOW_GUIDE.md:540-550 | NEW "Orchestrating delegates" | unconditional | A | FL |
| FLOW_SEAMS.md:24, 90 | "native unobserved delegation is not misrepresented as enforced"; "Native permission/delegation enforcement remains host-specific" | ALL | A | FL |
| USER_GUIDE.md:18, 113, 127, 131, 136 | commander "delegates everything", developer must not "re-delegate", wayfinder "delegate" | U (copy of upstream guide) | A | UP origin |
| crux/templates/AGENTS.md.tmpl:816-820 | assignment contract: "survives every hop - commander to dev-lead, dev-lead to developer, ..."; "A worker that cannot delegate returns its completion report" | U | A | UP |
| crux/skills/run-promptbook/SKILL.md:68-77 | three statements travel with "every dispatch, including a generic dispatch" | ALL | A | UP |
| crux/templates/cycle-module-review.yaml:48, 68, 120, 188 | "Dispatch parallel Agent reviewers", "one Agent per threat class", "dispatch a fix Agent" | U (dev-cycle / iterate) | A | UP |
| crux/templates/cycle-module-verify.yaml:46, 82; cycle-module-adr.yaml:47, 92, 141; cycle-promptbook-template.yaml:59-60, 91 | "dispatch 2-3 research agents in parallel via the Agent tool", "5 parallel review agents" | U | A | UP |
| crux/skills/council/SKILL.md:4, 16; crux/skills/tend-garden/SKILL.md:4, 16 | `context: fork` - "runs in a forked subagent" | ALL where invoked | A | UP |
| crux/skills/retrospective/SKILL.md:171 | "run two reviewer agents ... parallel-reviewer fallback" | U | A | UP |
| crux/skills/forge-skill/SKILL.md:287 | "Subagent dispatch (preferred for non-trivial skills)" | ALL | A | UP |
| crux/skills/install-codex-agents/SKILL.md:155 and references/fresh-session-verification.md:131 | "Codex's delegation-depth limits still apply"; "does not grant ... deeper delegation" | U (Codex install) | A | UP |
| crux/skills/install-opencode-agents/SKILL.md:45 | "commander and night-gardener are mode: all (usable as primary agents); the other eight are mode: subagent" | U | A | UP |

---------------------------------------------------------------------------------------------------------------

## 2. CONTRADICTIONS

Numbered; the five that matter most are marked **(KEY)**.

**C1 (KEY). Flow-mode `dev-lead` and `night-gardener` are generated with `Agent(...)` grants while the Flow-mode body says the primary owns orchestration.**
- hosts.py:84 keeps the upstream grant: `re.sub(r'Agent\(([^)]+)\)', ... 'Agent(' + ... 'crux-flow-' ...)`. Pinned by tests/flow/test_completion.py:98 (`'Agent(crux-flow-developer)' in head['tools']`).
- hosts.py:38 (same file, same generated role): "Primary session owns orchestration." and :44 "Do not delegate from an unsupported child context."
- So the mechanical grant says a second orchestrator exists; the injected body says there is only one. The test pins the grant, i.e. pins the contradiction.

**C2 (KEY). "dev-lead is the one subagent allowed to delegate" against three roles with `Agent(...)`.**
- crux/agents/dev-lead.md:17-18: "you are the one subagent allowed to delegate."
- crux/agents/commander.md:4 and crux/agents/night-gardener.md:4 also carry `Agent(...)`; night-gardener.md:72-75 says "Every delegation ... includes" objectives and "Require recipients to carry this context through further delegation".
- Upstream-owned and untouched in upstream mode; in Flow modes the dev-lead sentence is already dropped (body replaced), but the grant remains (C1).

**C3 (KEY). Two levels of orchestration against "every delegate is a leaf".**
- commander.md:41-42: "implementation -> dev-lead (who may fan out to developers)"; dev-lead.md:55: "Fan-out discipline (the one sanctioned re-delegation)"; AGENTS.md.tmpl:816 "commander to dev-lead, dev-lead to developer".
- The new rule: crux/skills/flow/SKILL.md:22 "a delegate starts no agent, sub-agent or worker of its own"; orchestration.md:6-8 "A delegate does its unit itself and starts no agent".
- commander -> dev-lead -> developer is orchestrator -> orchestrator -> leaf. Valid in upstream mode (must be preserved), invalid under the new rule in Flow modes. The new text is unconditional, so as written it also forbids upstream's own chain wherever the `flow` skill is read.

**C4 (KEY). The text added today contradicts itself.**
- SKILL.md:22 and orchestration.md:15-17 and FLOW_GUIDE.md:545-546: "only a role whose tool list names delegates may delegate (commander, dev-lead, night-gardener) ... Each is the orchestrator of its own unit".
- Same paragraph's headline: "One orchestrator; every delegate is a leaf."  A dev-lead that is itself a delegate and "orchestrator of its own unit" is a delegate that delegates. The permissive sentence nullifies the rule.
- Also factually stale in Flow modes: `commander` is not generated at all (hosts.py:64; tests/flow/test_hosts.py:28 asserts no commander), so naming it as a Flow-mode role that "may delegate" describes a file Flow never installs.

**C5 (KEY). Caps described as counted, but sub-delegation is unmediated and uncountable.**
- records.py:143-151 enforces `delegates` only for attempts the primary records with `run attempt --kind delegate`; flow SKILL.md:20: "unmediated native delegation remains cooperative and must not be described as mechanically counted."
- Yet flow-roles.json:4 gives dev-lead "Delegate only independent non-overlapping work allowed by host capabilities and recorded caps." A dev-lead (a child session) has no instruction to call `crux-flow run attempt`, and its grant (C1) lets it start up to four developers and a reviewer per call. In aggressive mode `delegates: 0` (flow-policy.json:20) while the role file still offers `Agent(developer)`.
- Also: FLOW_GUIDE.md:216 "Caps are maxima, not dispatch quotas" vs a role that is told to "fan out" in upstream text; in Flow modes the cap cannot bind a nested fan-out.

**C6. Same Codex file says "delegate" and "do not spawn".**
- flow-roles.json:4 (rendered into `developer_instructions` for `crux_flow_dev_lead`): "Delegate only independent non-overlapping work".
- codex_agents.py:323-325 and 332-335 (wrapped around the same body): "Do not spawn subagents ... Return any proposed delegation to the parent" and "do not invoke Agent and do not spawn another agent".
- Codex is therefore already leaf-only by prose (and by the host's default depth), but the file argues both ways.

**C7. "Only a role whose tool list names delegates may delegate" is not true on every host.**
- Claude: tool list carries `Agent(...)` for dev-lead/night-gardener (hosts.py:84).
- OpenCode: permissions array; roles without a grant get a `"*"` deny (opencode_agents.py:31-33). True.
- Codex: no per-role tool list exists; all roles are told not to spawn (codex_agents.py:323). The rule's sentence has no meaning there.
- OMP: `spawns` list plus `task` (hosts.py:100-108). True.
- Also not true for a general-purpose delegate that holds `*` tools (an ad hoc sub-agent): there the brief is the only barrier, which SKILL.md:22 does say, but the "only a role ..." sentence reads as a complete rule.

**C8. Councils and review/fork skills start agents from inside a delegate.**
- crux/skills/council/SKILL.md:4,16 and tend-garden/SKILL.md:4,16: `context: fork`; invoking either inside a delegate creates a forked subagent, i.e. a nested agent.
- Flow's own path is `crux-flow council` (cli.py:47,63; api_calls.py:44), a provider call recorded as an attempt, not an agent. But the F-mode reviewer/dev-lead keep the `Skill` tool, and the role bodies do not forbid invoking `council`/`tend-garden`/`forge-skill`. `_instructions` (hosts.py:43) says only "Do not start a council, forge a helper or add a review merely because a capability exists", which is a style note, not a prohibition.
- forge-skill/SKILL.md:287 prefers "Subagent dispatch" for same-session use of a forged skill; retrospective/SKILL.md:171 tells the caller to "run two reviewer agents"; cycle templates (cycle-module-review.yaml:48,68,120,188; verify.yaml:46,82; adr.yaml:47,92,141) tell whoever runs them to "dispatch parallel Agent reviewers". Those are orchestrator-level instructions for upstream cycles; they become nested-delegation instructions if a delegate in a Flow mode follows them (dev-cycle/iterate/patch-cycle are not Flow-mode paths per SKILL.md:30 "Keep upstream cycles on their original path", but nothing stops a delegate reading them).

**C9. A role file serves two uses; a stripped grant would break one.**
- night-gardener.md:3 "when a scheduled overnight session starts": run as the top-level session (`--agent`), it IS the orchestrator and its `Agent(historian|wayfinder|librarian)` is legitimate. The same file as a dispatched subagent must not delegate. A per-mode tool list cannot tell the two apart (see section 5, decision D2).

**C10. Brief contract has no leaf clause.**
- workflow.py:57 builds every unit's `Constraint:` from the spec's constraints only, and workflow.py:90's review prompt carries no delegation sentence. The new rule lives only in the skill text (SKILL.md:22, "Say so in every brief"), which depends on the orchestrator remembering; no code or test makes a brief carry it. A frozen policy cannot say it either (no policy field).

**C11. `flow` skill is read in upstream mode; the new text is not conditioned on mode.**
- SKILL.md:16 "A resumed run uses its frozen policy"; workflow.py:30 `route` returns `original-crux-workflow` for upstream, and hosts.py:127-140 prefixes the skill with "Existing upstream-mode records retain their original selected procedure". The `flow` skill is the entry for every code change in all modes (and `skills=('flow',)` is preloaded only in F, hosts.py:70/88, but the main session reads it in all modes).
- The new paragraph (SKILL.md:22), orchestration.md:6 and FLOW_GUIDE.md:542 do not say "Aggressive, balanced and thorough only". It would forbid upstream commander -> dev-lead -> developer, which the owner wants untouched.

**C12. Reviewer requirement vs who dispatches it.**
- flow SKILL.md:20,36 require an independent reviewer in every Flow mode; the only roles with `Agent(reviewer)` in the Flow roster are dev-lead (dev-lead.md:4) - upstream's design has dev-lead commission the reviewer of each developer's unit (dev-lead.md:46-47). Under the one-orchestrator rule the primary must commission the reviewer; dev-lead.md:46-47 and flow-roles.json:4 do not say so (Flow's body replaced it, but the grant remains).

**C13. Adjacent documents describe roles differently per host.**
- OPENCODE.md:78 and crux/skills/install-opencode-agents/SKILL.md:45,155-159: "ten Crux roles ... commander and night-gardener as all" - the upstream installer's view. Flow's OpenCode projection ships nine `crux-flow-*` roles, no commander (hosts.py:64; test_hosts.py:25), names `crux-flow-<role>` (hosts.py:98), and SKILL.md:52 (generated runtime-compat block) still lists `commander` for Codex and OpenCode. In the Flow `skills()` projection that block is removed (hosts.py:128), but the source SKILL.md files and CODEX/OPENCODE guides still say it.
- Codex: codex_agents.py:323 says delegation "returns to the parent"; install-codex-agents/SKILL.md:155 "Codex's delegation-depth limits still apply" - consistent with leaf-only, inconsistent with `Agent(dev-lead -> developer)` being a working path in the same source.
- Claude: per Claude Code's subagent model, a subagent started via the Agent tool cannot itself start subagents; `Agent(role)` allowlists take effect only for a role run as the main thread (`--agent`). This is documented host behaviour that I could not verify from the repo (section 7). If true, dev-lead.md:17-18 ("the one subagent allowed to delegate") is false on Claude as a dispatched subagent, and C1 is dormant rather than live there.

**C14. `delegates` cap vs reviewers and the default mode.**
- flow-policy.json: aggressive `delegates: 0, reviewers: 1`. The review is a delegate (a separate execution context per flow SKILL.md:36) but is counted under `reviewers`, not `delegates`; so "Aggressive permits no implementation delegate" (SKILL.md:20) is the qualifier that keeps this consistent. The new text "a delegate starts no agent" uses "delegate" for any worker, a different word-sense than the policy field. Not a contradiction of behaviour, a vocabulary clash to fix when wording the rule.

**C15. "recorded attempt" ordering vs delegates that cannot record.**
- SKILL.md:20: "Record owned attempts before invocation". A delegate cannot be told to run `run attempt` without becoming an orchestrator of its own bookkeeping, and `bookkeeping` is itself a kind (cli.py:52). Consistent only if all recording stays with the single orchestrator - which is what the rule implies and should say.

---------------------------------------------------------------------------------------------------------------

## 3. PER MODE: what is permitted and generated today

Common to the three Flow modes: nine role files per host (commander skipped, hosts.py:64), each with body `_instructions` (hosts.py:35-47) and `skills: [flow]` (Claude, hosts.py:88; plus the technology router for preloaded roles). Names `crux-flow-<role>` (Codex `crux_flow_<role>`).

| host | roles that can delegate today (F modes) | basis |
|---|---|---|
| Claude | `dev-lead` -> developer, historian, reviewer, wayfinder; `night-gardener` -> historian, wayfinder, librarian | hosts.py:84 passes tools through; dev-lead.md:4, night-gardener.md:4 |
| OpenCode | the same two, via subagent allow rules; others `"*"` deny | opencode_agents.py:29-33; hosts.py:93-95 |
| OMP | the same two, via `task` tool and `spawns` list; others `spawns: []` (test_hosts.py:48) | hosts.py:100-108 |
| Codex | none by prose (all roles "Do not spawn subagents"); no mechanical control | codex_agents.py:323-335 |

- **aggressive** (default, `rapid` alias): policy `delegates: 0, reviewers: 1, repair 1, council 0` (flow-policy.json:20 and neighbours). Policy permits no implementation delegate. Generated: dev-lead and night-gardener still hold `Agent(...)` (C1, C5). Nothing in code refuses a nested attempt; `records.attempt('delegate')` would refuse the primary's first record (`counts>=0`), but a nested dispatch is never recorded.
- **balanced**: `delegates 2, reviewers 2, repair 2, council 1`, economical->balanced models. Same role files as aggressive except model/effort. The cap is counted only for `run attempt` calls by the primary.
- **thorough**: `delegates 4, reviewers 3, repair 3, council 2`, strong models. Same grants.
- **upstream**: policy fields all null (policy.py:132-134); roles are the upstream files re-projected: source body kept, source `skills:` kept, commander included, `models: upstream` forced (policy.py:250-251), Agent targets prefixed `crux-flow-` (hosts.py:84, for Claude) so commander -> dev-lead -> developer, night-gardener -> historian/wayfinder/librarian all remain valid. Codex upstream output is pinned equal to upstream's `codex_agents.generate` (test_hosts.py:53-57, modulo the name prefix). **No equivalent pin exists for the Claude, OpenCode or OMP upstream output.** Technology preload is suppressed in upstream (`extra=... if preload and not upstream`, hosts.py:58).

What must change so the rule is mandatory in the Flow modes and upstream is byte-identical: all changes must sit behind `not upstream` in `hosts.roles` and in Flow-owned text; nothing in the `upstream` branch (hosts.py:69, 88 right-hand side, 64) changes.

---------------------------------------------------------------------------------------------------------------

## 4. WHERE THE RULE MUST LIVE, in order of force

**(a) Mechanical - possible with existing seams; touches Flow-owned code only.**
1. `hosts.roles`, non-upstream branch only: after the per-host projection, remove delegation.
   - Claude: drop every `Agent(...)` token from `projected['tools']` (hosts.py:83-84); also set `disallowedTools` to include `Agent` (hosts.py:82 already copies `disallowedTools` if present; add it for F). This holds even if a user runs the role as the main thread.
   - OpenCode: after `transform` (hosts.py:92), set every `subagent` rule to a single `"*"` deny (replace the loop at hosts.py:93-95).
   - OMP: skip `spawns` and the `task` tool (hosts.py:101-106) in F; leaves `spawns: []` for all roles.
   - Codex: nothing to project (no spawn tool); prose already present (codex_agents.py:323). Add the Flow sentence to `_instructions` for the record.
2. Upstream-owned files are not edited: `crux/agents/*.md`, `codex_agents.py`, `opencode_agents.py` stay as is. Flow overrides by projecting differently in `hosts.roles`, which is exactly how it already replaces the body (hosts.py:68-70) and the skills.
3. No policy field: the existing `upstream` boolean (hosts.py:53) is sufficient and avoids a `policy_revision` bump of frozen runs (policy digests are pinned per run, records.py:validate). If a visible control is wanted, a constant `delegation: leaf-only` in `enforcement` (policy.py:302 / hosts.py:114) is enough; do not add it to `flow-policy.json`'s schema (policy.py:119-136 requires exact field sets).
4. `workflow.py`: refusing a recorded nested attempt is not meaningful - a nested dispatch is exactly what is not recorded. What `records.py` can do cheaply: add a counted kind or reject `run attempt` from a delegate - there is no way to tell caller identity, so do not. Instead put the clause in the brief (c).
5. Tests: see section 5.

**(b) Injected - exists already; strengthen text only.**
- Claude: `skills: [flow]` preload (hosts.py:88) injects the whole `flow` skill into each role at start, so the paragraph at SKILL.md:22 already reaches Claude delegates. `_instructions` (hosts.py:38-47) is the body of every F role on every host; it is the only text that also reaches Codex, OpenCode and OMP roles (they have no skill injection; FLOW_GUIDE.md:327). Put the exact sentence there. This is the highest-value text edit.
- `flow-roles.json:4` must lose "Delegate only ..." (C6).

**(c) Instructed - flow skill, brief contract, guide.**
- `flow` SKILL.md paragraph (conditioned on mode, section 5).
- The brief contract: `workflow.py:57` appends a standing constraint to every unit prompt and the review prompt (workflow.py:90). The book's prompts are what the orchestrator copies into every dispatch (flow SKILL.md:18; run-promptbook/SKILL.md:72-77 "every agent a prompt dispatches receives all three"), so putting the clause into `Constraint:` makes it travel with Outcome/Evidence. It changes `book_content_hash` for new books only; frozen runs are unaffected (records.py load verifies the stored hash).
- `FLOW_GUIDE.md` section and `orchestration.md`: reference, no force.

Which are not possible without leaving the seams: tool-level enforcement on Codex (no host control; only prose, plus the host's own depth limit - not a Flow guarantee) and any check on a general-purpose delegate (it holds `*`; only the brief binds it). State this honestly; do not call the rule "enforced" on those. `enforcement.native_delegation: cooperative` (policy.py:302, hosts.py:114) should be refined to say `leaf-only: tool-enforced on Claude/OpenCode/OMP roles, instructed elsewhere`.

---------------------------------------------------------------------------------------------------------------

## 5. RECOMMENDED DESIGN (smallest set of changes)

Five edits and three tests.

**E1. hosts.py `roles` (Flow-owned, mechanical).** Inside the loop, when `not upstream`:
- Claude: `projected['tools']` with all `Agent(...)` removed; `projected['disallowedTools']='Agent'`.
- OpenCode: replace the subagent rules with one `{action: subagent, resource: '*', effect: deny}` (keep the existing rule shape that `transform` emits for roles without a grant).
- OMP: no `task` tool and `spawns: []`.
- Upstream branch untouched, so output stays byte-identical.

**E2. `_instructions` (hosts.py:38-47), injected on every host.** Replace "Do not delegate from an unsupported child context." by exactly:
> "You are a leaf. You must not start any agent, sub-agent or worker, on any runtime; do all work in your own session. If your unit is too large, stop and return what remains as work orders for the session that owns the run. Do not run `crux-flow run attempt`; the owning session records attempts."

**E3. flow-roles.json:4.** dev-lead: "Integrate a bounded assignment yourself, as one leaf. Hand back what you cannot finish as work orders; the owning session decides who takes them." night-gardener: add "Do not delegate; return follow-ups as work orders."

**E4. workflow.py:57 and :90, the brief contract.** Add to every unit's Constraint list, once, as a constant:
> `NO_NESTED = 'You must not start any sub-agent or worker; do all work in your own session. Return what you cannot finish as work orders.'`
Append to `constraints` in `_units` (not for `check` units, which are executed by the owning session) and to the independent-review prompt. Do this for non-upstream only (`start` already refuses upstream, workflow.py:106).

**E5. Text.** 
- flow `SKILL.md:22`, replace with:
  > "In aggressive, balanced and thorough mode: one orchestrator, every delegate a leaf. The session that owns the run is the only one that delegates, records attempts and runs the full gate. A delegate - worker, sub-agent or role agent - does its unit itself and starts no agent, sub-agent or worker on any runtime, and hands back what it cannot finish as work orders. Every brief says: 'You must not start any sub-agent or worker; do all work in your own session.' The generated Flow roles carry no delegation tool (Claude, OpenCode, OMP) or are told not to spawn (Codex); a general-purpose delegate is bound only by its brief. Upstream mode keeps upstream Crux's own delegation. Before handing work to more than one delegate, or to another runtime, read `references/orchestration.md`."
- `references/orchestration.md:6-24`: same condition at the top ("Applies in aggressive, balanced and thorough; upstream mode is unchanged"); delete the bullet "Only a role whose tool list names delegates may delegate (commander, dev-lead, night-gardener) ..." and replace with the generated-role statement above.
- `FLOW_GUIDE.md:540-550`: same; also fix the modes text (FLOW_GUIDE.md:209-217) with one line: "Delegates are leaves in every mode except upstream."

**Tests.**
1. `test_upstream_roles_unchanged` (new, tests/flow/test_hosts.py): for each of `claude`, `codex`, `opencode`, `omp`, `roles(PLUGIN, resolve(..., {'mode':'upstream'}))['files']` equal a checked-in golden digest set (tests/flow/fixtures/upstream-roles-<host>.sha256 generated from the pre-change tree), and additionally assert the Claude `crux-flow-dev-lead.md` still contains `Agent(crux-flow-developer)` and `crux-flow-commander.md` exists. The Codex pin at test_hosts.py:53-57 only covers Codex.
2. `test_no_flow_role_can_delegate` (new): parametrize mode in {aggressive, balanced, thorough, rapid} x host in {claude, opencode, omp}: Claude `tools` has no `Agent`, `disallowedTools` contains `Agent`; OpenCode has no subagent `allow`; OMP has `spawns==[]` and no `task`; all four hosts: each body contains "You must not start any agent". Also assert `'Delegate only' not in` any Codex body.
3. Change tests/flow/test_completion.py:94-99 (`test_claude_delegation_targets_match_projected_role_names`): it asserts the grant that the new design removes. Replace by the same assertion in upstream mode (grant present, prefixed), and the new negative in Flow mode. Also keep test_hosts.py:25-50 (nine files, no commander) and extend line 48 (`head['spawns']==[]` currently asserted for the reviewer only) to every role.
4. A test that `start` spec units and the review prompt contain `NO_NESTED` (tests/flow/test_product_commands.py or test_records.py).

Policy/version: this changes generated role bytes in Flow modes, so bump `projection_version` (flow-policy.json identity, currently '2') and CHANGELOG; do not touch `policy_revision` unless a schema field is added (none proposed). Existing materialized installs re-render on `mode materialize` (FLOW_GUIDE.md:238).

### The owner's decisions and my recommendation

- **D1. In Flow modes, is `commander` the orchestrator, or only the primary session?** Only the primary session. Reason: Flow already omits commander (hosts.py:64; test_hosts.py:28) and SKILL.md:20 says "Primary session implements and integrates"; a commander would be a second orchestrator, the thing the rule bans, and cannot be counted by `run attempt`.
- **D2. Does `dev-lead` survive as a delegating role in balanced/thorough?** No. Keep it as a leaf "integrator" (one session that carries a bounded cross-cutting assignment itself), with the primary dispatching up to `delegates` developers directly. Reason: balanced/thorough caps (2 and 4) are counted only if the primary dispatches (records.py:143-151); a dev-lead fan-out defeats the count (C5) and makes Claude's `isolation: worktree` fan-out invisible. Cost: loses upstream's dev-lead fan-out discipline text (already replaced in Flow, hosts.py:68-70).
- **D3. `night-gardener` as a top-level scheduled session.** Keep its `Agent(...)` grants only if the owner wants the gardener to dispatch when it is the main thread. My recommendation: strip in Flow modes and have it return follow-ups as work orders. Reason: the same file cannot tell main-thread from subagent use (C9), and an unattended overnight session is exactly where uncounted nested spawning costs most (usage limits, orchestration.md:15-24).
- **D4. Forked skills (`council`, `tend-garden`) used by a delegate.** Recommend: a Flow-mode delegate never invokes a `context: fork` skill; the council goes through `crux-flow council` (a counted provider call). Add this one sentence to `_instructions` only if the owner agrees, since it removes `tend-garden` from a gardener-as-leaf.
- **D5. Whether to add a visible policy field** (`delegation: leaf-only`). Recommend no: derive from mode, keeps the schema and `policy_revision`.

Files to touch: crux/scripts/crux/flow/hosts.py, crux/scripts/crux/flow/workflow.py, crux/catalog/flow-roles.json, crux/catalog/flow-policy.json (projection_version only), crux/skills/flow/SKILL.md, crux/skills/flow/references/orchestration.md, FLOW_GUIDE.md, CHANGELOG.md, tests/flow/test_hosts.py, tests/flow/test_completion.py, one new fixture, and a workflow/records test. Not touched: crux/agents/*, codex_agents.py, opencode_agents.py, upstream templates and skills.

---------------------------------------------------------------------------------------------------------------

## 6. OTHER STALE OR CONTRADICTORY ITEMS FOUND

- OPENCODE.md:78 and install-opencode-agents/SKILL.md:45,159: "ten Crux roles", commander `all`; Flow ships nine, no commander (C13). CODEX.md contains none of this; it has no role description at all.
- Generated runtime-compat block in every source SKILL.md lists `crux_commander` / `commander` as installable roles (e.g. flow/SKILL.md:52); stripped from Flow's projected skills (hosts.py:128) but present in the source tree and in this repo's README-level docs.
- README.md:63 says "concise role instructions live in `crux/catalog/flow-*.json`"; the instructions are mostly `hosts.py::_instructions` (hosts.py:38-47) plus `flow-roles.json`.
- FLOW_GUIDE.md:216: "Balanced's second reviewer is a justified specialist"; flow SKILL.md:20: "one reviewer and at most one justified specialist" - consistent, but flow-policy.json shows balanced `reviewers: 2` and `delegates: 2`, SKILL.md says "one reviewer" (two with the specialist). The table says "at most 2". Minor wording drift.
- verification/README.md:1-12 records "Flow 124 passed" for 0.2.0 on 2026-10-05; the tree has since added technology tests and the version in flow-policy.json is still 0.2.0 (identity.version) while the working tree has uncommitted behaviour changes: version/projection bump is owed regardless of this rule.
- Working tree: `?? verification/2026-10-09-*` and `2026-10-10-*` files are untracked and unreferenced from verification/README.md.
- USER_GUIDE.md (root) is the upstream user guide and mentions only 10 roles and `commander`; Flow-mode readers will find no commander.
- retrospective/SKILL.md:171 and forge-skill/SKILL.md:287 contain upstream "dispatch" advice with no Flow-mode caveat (C8).
- The directory crux/skills/flow/references/ is new and untracked; skills() copies it via rglob (hosts.py:120), packaging also rglob (packaging.py:30), so it ships. No SOURCE_INVENTORY entry was found for it; unverified whether a catalog validator expects references to be listed.
- Two upstream plugin versions are cached (3.23.1 and 3.25.1); the fork pins 3.25.1 (flow-policy.json identity), commit 732c355. Only 3.25.1 was compared.

---------------------------------------------------------------------------------------------------------------

## 7. WHAT I COULD NOT DETERMINE

- Whether Claude Code actually honours `Agent(...)` in a role that is itself running as a subagent. My understanding is that it does not (nested subagents are not allowed; the allowlist applies to a main-thread agent). The repo has no measurement either way; the 2026-10-10 live evidence covers router preload only. This decides whether C1 is live or dormant on Claude, but not whether the generated grant should go: the grant is wrong either way for a role launched with `--agent`.
- Whether OpenCode V2 and OMP enforce `subagent`/`spawns` nested (the repo says `runtime_loaded: unobserved`, hosts.py:113).
- Codex nested depth default (the source asserts "default agent depth ... prevents nested delegation", codex_agents.py:323-325); not verified against a Codex build.
- Whether any catalog validator (`validate-catalog`, `check-drift`) requires `skills/flow/references/` to be registered; I did not run any validator.
- Test status of the working tree: I ran no tests, so I cannot say whether the tree's existing technology tests pass.
- The golden-digest fixtures for the upstream byte-identity test do not exist yet; they must be generated from the pre-change tree before E1 lands.
- Upstream `docs/AGENTS.md` is not present in the cached plugin (no `docs/` directory); upstream's statement of the assignment contract is `crux/templates/AGENTS.md.tmpl` (:806-828), which is what I used.
