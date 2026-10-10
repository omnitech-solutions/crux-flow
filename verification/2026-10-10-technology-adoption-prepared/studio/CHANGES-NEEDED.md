# Interview Studio: what its hand-written router and map need to pass the check

Prepared, not applied. Checked read-only on 2026-10-10 against the Studio's working tree at
`0ad961d` with `studio/.crux-flow.yml` from this directory (`owner: project`). Flow would write
nothing there in any mode.

## The check's result today

`crux-flow technology check` exits 1, BROKEN, with exactly two rows, both on the router's
`description` (`.agents/skills/technology-references/SKILL.md`, line 3):

| # | Finding | Evidence |
|---|---|---|
| 1 | The description names "Anthropic SDK". No manifest declares `@anthropic-ai/sdk`. | At `0ad961d` no `package.json` lists it. The Studio depends on `@anthropic-ai/claude-agent-sdk` (`^0.3.220`, pnpm catalog, used by `apps/agent-worker`) and on `@omnitech/ai-engine` `0.1.0`. |
| 2 | The description has no trigger term for the Claude Agent SDK, which the `ai` layer routes. | Same manifests. |

One warning: the router carries no generated region, so a version change in a manifest is not
tracked for the Studio.

Everything else passes: all nine source pages carry a pin and an existing raw capture; every rule
page exists; `db:generate`, `db:migrate`, `docs:arch`, `docs:arch:check` and `verify` are real
scripts where the section says they are; every reference and command in the section is named in
the router or the map; there is one router (the `.claude/skills` and `.opencode/skills` links
resolve to it); the root `AGENTS.md` names it.

## The smallest change that makes it clean

In `.agents/skills/technology-references/SKILL.md`, line 3, replace `PostgreSQL or Anthropic SDK
code` with `PostgreSQL or Claude Agent SDK code`. The fixture test
`test_studio_router_passes_once_its_description_names_what_the_manifests_declare` proves that one
edit gives exit 0.

## Stale wording the check cannot see (the owner's call)

The check compares names with manifests; it does not read prose for truth. These lines look stale
against the same commit:

| Where | Says | What the tree shows |
|---|---|---|
| Router, step 5 | "products call `AiExecutionGateway`" | The only occurrence of `AiExecutionGateway` in TypeScript at `0ad961d` is an error-message string in `scripts/web-thinness.test.ts:153`. No type, class or import of that name exists. Products depend on `@omnitech/ai-engine`. So the line is stale as a description of the code. `AGENTS.md` rule 7 and ADR-0007 still use the name, so correcting it is a decision about those, not a router typo. |
| Map, row "AI and the Anthropic SDK" | "(`packages/ai-*`, `apps/agent-worker`)" and "Check `@anthropic-ai/sdk` ... versions" | There is no `packages/ai-*` directory, and no manifest declares `@anthropic-ai/sdk`. |
| Map, same row | "Provider SDKs stay inside `ai-provider-*` packages" | No such package exists in the tree; the skill `ai-provider-maintainer` describes the same absent packages. |
| Map, front matter | `last_reviewed: 2026-10-05` | Unchanged since the engine replaced those packages. |

## Optional: version drift for the Studio

To have a Drizzle or Next.js version change show as DRIFT, paste the region that
`crux-flow technology sync` prints (it writes nothing) into the router or the map, between the
`technology-routes` markers. The fixture test
`test_a_project_router_that_carries_the_region_gets_version_drift_and_the_pages_to_refresh`
shows the result: a catalog bump from `1.0.0-rc.4` reports DRIFT and names the six Drizzle source
pages to refresh.

## What is not prepared

The Studio does not use Crux Flow. Its `.crux-flow.yml` would hold only `config_version` and the
`technology` section; `crux-flow technology check` needs no activation and never runs `init`.
Whether upstream `check-drift` in the Studio should run this row is the owner's decision: upstream
Crux does not ship `generate-technology-references.py`, so the Studio would call the `crux-flow`
launcher directly (for example from `pnpm verify`).
