# Technology guidance: live evidence that the router is loaded

Date: 2026-10-10. Hosts: Claude Code 2.1.292, Codex CLI 0.162.1. The owner approved the paid turns.
Total Claude spend reported by the host for the recorded cases: 3.61 USD (exploration added about 0.9).

The question, in the owner's words: prove the applicable skills get loaded automatically with or
without the Crux Flow skill being executed.

## How each turn was run

`tests/flow/test_technology_live.py` (marker `provider`, skipped unless
`CRUX_FLOW_PROVIDER_TESTS=1`). Each turn runs in a fresh fixture copy of a consumer inside a pytest
temporary directory:

```
claude --print --verbose --output-format stream-json --model <model> --setting-sources project
       --strict-mcp-config --no-session-persistence --tools Read,Glob,Grep,Skill --max-budget-usd 1.00 "<prompt>"
```

`--setting-sources project` keeps the owner's user settings and installed plugins out of the
session; the login is his. Only read tools exist in the session. Where a case needs Flow, the
plugin is loaded for that session alone with `--plugin-dir <release built by the test>`.

A case is judged from the event stream, never from what the model says it did: a `Skill` call
naming the router, `Read` calls on reference files, or a 12-hex digest the model cannot guess.
Per-run observations are in `2026-10-10-technology-guidance-live/<case>.json`. The raw transcripts
(2.3 MB) were not committed.

The engine copy is the engine after adoption: generated router, and the routing section Flow
proposes in `AGENTS.md`. The Studio copy is the Studio as it is: its own hand-written router and
its own `AGENTS.md` line.

## Results

| Case | Session | Runs | Result | Proven by | Kind |
|---|---|---:|---|---|---|
| a | Studio, ordinary prompt (a Drizzle migration; no skill named), sonnet | 5 | Router loaded **1 of 5**. A Drizzle source page read 1 of 5 (a different run). UI references read 0 of 5. Strict pass (router and a Drizzle page) **0 of 5**. | `Skill` and `Read` events | Probabilistic |
| a′ | The same, with the routing section Flow proposes appended to the Studio's `AGENTS.md` | 5 | Router loaded **5 of 5**; the reference map read 5 of 5; a Drizzle source page read 0 of 5; UI references 0 of 5. | same | Probabilistic |
| b | Engine, ordinary prompt (a provider adapter), sonnet | 5 | Router loaded **5 of 5**; the `model-providers` row's references read 5 of 5; storage and logging references 0 of 5; no "Next.js" or "React" in any path read or answer. | same | Probabilistic |
| b (smallest model) | The same on haiku | 5 | Router loaded **4 of 5**. In exploration with the same carriers: 1 of 3. | same | Probabilistic |
| c1 | Engine, Flow plugin enabled (55 more skill descriptions listed), flow skill not invoked | 3 | Router loaded **3 of 3**; same references. | same | Probabilistic |
| c2 | Engine, `/crux-flow:flow <the same prompt>` | 3 | Router loaded **3 of 3**; same references. | same | Probabilistic |
| c2 | Studio, `/crux-flow:flow <the same prompt>` | 3 | Router loaded **0 of 3**. | same | Probabilistic |
| d | Engine, the generated `crux-flow-reviewer` role file (`skills: [flow, technology-references]`) dispatched as a subagent with no tool available | 3 | The subagent's hand-back states the router's digest `tr-8bf646878056` **3 of 3**, with `tool_uses: 0`. | Canary in the hand-back | Deterministic (host injection) |
| e | README typo, Studio and engine | 5 + 5 | Router not loaded and no reference read **10 of 10**. | absence of `Skill` and reference `Read` events | Probabilistic |
| f | Codex, Studio, the migration prompt, the owner's default model, read-only sandbox | 3 | `cat .agents/skills/technology-references/SKILL.md` and the Drizzle source pages in the commands run **3 of 3**; no Vercel or Swift page opened. | `command_execution` events | Probabilistic |

## What this shows

1. A delegated Claude role that lists the router starts with the router's text. This is the one
   certain carrier.
2. In an ordinary session the generated router with the proposed routing section was loaded every
   time on sonnet, with and without the Flow plugin, and with and without the flow skill run first.
   It was not loaded for a prose edit.
3. The Studio's present carriers do not work on Claude: one load in five. Its `AGENTS.md` mentions
   the skill as one bullet among several workflow bullets. Appending the standalone section moved
   it to five in five. That is the single most useful change for the Studio.
4. Once loaded, the Studio's router sends the reader to the map, and the session stops there: the
   Drizzle source pages were opened in none of the five runs of a′. The generated router names each
   row's pages directly, and those were read every time. The fixture is a skeleton and several
   answers say so, which may shorten a session; both consumers were measured the same way.
5. Running the flow skill first neither helps nor hurts: the result follows the repository's own
   carriers (engine 3 of 3, Studio 0 of 3).
6. The smallest model is unreliable (4 of 5, and 1 of 3 earlier). The role files use sonnet or
   stronger.

## The audit's open questions

**3. Does Claude resolve the bare name `flow` in a project role file when the skill is
`crux-flow:flow`? Yes.** In case d the role was also asked for the first sentence under "Installed
runtime and routing", a heading that exists only in Flow's projected skills. All three subagents
returned "The installed plugin contains engine/ with the canonical Crux runtime." with no tool
use. A control role file listing only `technology-references` returned `FLOW-ABSENT` and still
returned the digest. Observed on Claude Code 2.1.292 with the plugin loaded by `--plugin-dir`. The
subagents page documents only bare names and says a missing name is skipped with a debug-log
warning. No defect.

**4. Does OpenCode load `crux-flow-<name>` folders whose frontmatter says `name: <name>`? Not
shown, and the documentation says it should not be relied on.** OpenCode's skills page says `name`
must "Match the directory name that contains `SKILL.md`". Flow writes
`.opencode/skills/crux-flow-flow/SKILL.md` with `name: flow` (seen in the engine's tracked files;
`materialize.py` prefixes the folder and keeps the name). The page does not say what a mismatch
does, and OpenCode 2.0.16 has no command that lists discovered skills (`opencode debug` offers
`agents`, `config`, `paths`), so no test was run: one would mean driving the owner's background
OpenCode service. This is an existing condition in Crux Flow, outside this change, and it was not
changed. The same applies to `.omp/skills`.

## Deterministic proofs kept offline

- Without a `technology` section the role files for all four hosts, in aggressive and upstream
  mode, are byte-identical to those rendered by `HEAD` (`f659de0`): digests in
  `2026-10-10-technology-guidance-live/roles-without-section-vs-HEAD.json`, compared after
  replacing the plugin path, which the Codex upstream rendering embeds.
- The project `AGENTS.md` is in a headless session's context: with no tool available, a session in
  the Studio copy quoted architecture rule 9 word for word and named the skill.

## Incidents

- The first exploratory engine turn ran with the default tool set. The model called the `Artifact`
  tool five times. Three failed with "File not found" and two were read-only quickstart calls;
  nothing was published. Every later turn was limited to `Read,Glob,Grep,Skill` (and `Task` for
  case d).
- Codex reads the owner's `~/.codex/memories/MEMORY.md` in its own first command. That is Codex's
  normal behaviour and a read; `--ephemeral` kept the sessions from being saved.
- The first judgment of case f was 0 of 3 because the judge searched command output, which
  contains the router's text. It now reads the command lines only; the three turns were run again.

## Not proven

- Loading in Claude Desktop or in an interactive session; only headless `--print` was driven.
- Rates on the real repositories, with their full trees and the owner's enabled plugins.
- One prompt per consumer was measured live. The other trigger prompts exist as data and were
  checked offline against the configured layers only.
- OpenCode and OMP sessions. Whether OMP reads `.agents/skills`, and what OpenCode does with the
  same skill name present in both `.agents/skills` and `.claude/skills`.
- Codex subagent roles: the `[[skills.config]]` entry is generated and parsed; no Codex role was
  dispatched.
- Whether a skill's `paths:` frontmatter loads its body (the audit's open question 2). Not used.
