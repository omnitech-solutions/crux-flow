# AGENTS.md — omnitech-ai-engine

Orientation for every agent (Claude Code, Codex, OpenCode, OMP) and human working here.

## Mission

One SDK for every AI interaction: a model call, an image, an agent runtime, the context a model
is given, and the code it writes. It is embedded in the host's own server and worker processes
; it is not a service and not a framework.
A consumer builds one engine, `createAiEngine({ ... })`, and calls operations named for what they promise.

Read first: `bionic/objectives.md`, then under `bionic/research/references/`: `plan.md` (phases and
status), `contract.md` (the specification, on worked examples), `versioning.md`, `deferred.md`
(what was put off, and what brings each item back). The decisions are ADR-0001 to ADR-0015 in
`bionic/adrs/`.

## Rules — do not violate

1. **The import rule.** The package has one entry point, `@omnitech/ai-engine`, and no other path can be imported (ADR-0032); a host names a provider or runtime (`modelProvider`, `agentRuntime`) and never imports what implements it. Importing the entry point starts nothing: no credentials
   read, no file or database access, no timers, workers or processes; and none of them reaches a
   provider SDK or a Node-only module. `packages/ai-engine/src/import-rule.test.ts` checks it;
   allowing a new package there is a deliberate decision.
2. **No framework.** No `ai`, `@ai-sdk/*`, LangChain or Mastra dependency, direct or transitive.
   `pnpm verify:no-frameworks` checks every `package.json` and the lockfile.
3. **Nothing inside the engine reads the environment.** The host reads its environment once and
   passes configuration and ready-made adapters to `createAiEngine`.
4. **Typed failures.** An operation ends in a `Failure` with a code from `failure.ts`; an adapter
   never throws a plain error to a caller.
5. **Behaviour changes are decided by comparison.** Change what `contracts/` or `providers/` do
   only when a case, written once and run against both versions, says so.
6. **`bionic/research/references/contract.md` is the specification.** It changes in the same commit as the code. Where
   the code does not yet do what it says, there is an `it.todo` naming the section; never a
   test that pretends.
7. **Simplicity first.** Configuration driven, SDK style, only what a phase needs. No speculative
   layers. Products own prompts, schemas, permissions and retention; the engine owns mechanics.
8. **Local only.** Nothing is pushed or published without being asked.

## Code style

New source keeps the comment style in `engine.ts`: `[GUARD]`, `[STRATEGY]`, `[DOMAIN]` before each
major block, saying why it exists. Domain names; orchestration readable top to bottom.

## Commands

```bash
pnpm install
pnpm verify                # format, lint, typecheck, test, build, verify:no-frameworks
pnpm test                  # vitest
pnpm format:write          # apply Biome formatting
```

`pnpm install` installs the git hooks (lefthook, `lefthook.yml`). The pre-commit hook runs Biome
(format and lint) on the staged files and the typecheck, and rewrites nothing; the pre-push hook
runs `pnpm verify`. Repair a refusal with `pnpm format:write` or `pnpm lint:fix`.

Run `pnpm verify` before claiming completion. Every change adds a line under Unreleased in
`CHANGELOG.md`.

## Workflow and documentation

The development workflow is Crux Flow, mode aggressive (`.crux-flow.yml`); enter through the
`flow` skill. All documentation lives in `bionic/` (ADR-0015); the changelog is at the root. See `bionic/AGENTS.md` for documentation operations,
read `bionic/objectives.md` before work of any size, and never hand-edit its regenerated parts.
