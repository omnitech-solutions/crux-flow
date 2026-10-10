# Technology guidance: adoption prepared for the two consumers (not applied)

Date: 2026-10-10. Nothing here has been written to either repository. Both sections were checked
read-only against each repository's working tree with the checkout's `technology.inspect`.

| File | For | What it is |
|---|---|---|
| `engine/.crux-flow.yml` | omnitech-ai-engine | The existing two lines plus the `technology` section (`owner: flow`). Closure check against the real tree: no errors. |
| `engine/.agents/skills/technology-references/SKILL.md` | omnitech-ai-engine | The router `crux-flow technology sync` would generate there today (digest `tr-8bf646878056`). Do not copy it in: `sync` writes it with a receipt, plus byte-identical copies under `.claude/skills` and `.omp/skills`. |
| `engine/AGENTS.md.addition.md` | omnitech-ai-engine | The one section to add to the root `AGENTS.md` by hand. |
| `engine/triggers.json`, `studio/triggers.json` | both | Each router's trigger evaluations as data. |
| `studio/.crux-flow.yml` | Interview Studio | `config_version` and the `technology` section only (`owner: project`). |
| `studio/CHANGES-NEEDED.md` | Interview Studio | Exactly what the hand-written router and map need to pass, and what is stale. |

## What the installed Flow release does with a `technology` key today

It refuses the whole file. Observed against the release the engine's role files point at
(`~/.local/share/crux-flow/releases/ff5fc636…/crux-flow/engine`, version 0.2.0), by calling its own
`policy.read_config` on `engine/.crux-flow.yml` from a temporary directory:

```
FIELDS ['budget_minutes', 'config_version', 'mode', 'models', 'roles']
REFUSED: FlowError missing required fields or unknown fields
```

Every Flow command that reads the project configuration (`mode show`, `run start`, `init`,
`mode materialize`) would fail in the engine until the new release is installed. The version string
is still `0.2.0` in this checkout; the release digest is what distinguishes the two.

## Safe order: the engine

1. Commit this change in crux-flow. The post-commit hook then runs `crux-flow setup --yes` and
   reinstalls every host from the new release. To separate the two, commit with
   `CRUX_FLOW_NO_HOOK=1` and run `pnpm run refresh` when ready.
2. Confirm the launcher is the new release: `crux-flow version --check` exits 0, and
   `crux-flow --repo <engine> technology check` prints `surface_absent` with `eligible` (an old
   launcher has no `technology` verb).
3. Only then add the `technology` section to the engine's `.crux-flow.yml`.
4. `crux-flow --repo <engine> technology sync --dry-run`, read the plan (three files and one
   receipt), then `technology sync`.
5. Add `engine/AGENTS.md.addition.md` to the engine's root `AGENTS.md`.
6. `crux-flow --repo <engine> mode materialize --host all --scope project`: the developer and
   reviewer role files gain the router (two Claude files gain one `skills:` entry; two Codex files
   gain one `[[skills.config]]`; OpenCode and OMP files do not change). Restart the hosts.
7. `crux-flow --repo <engine> technology check` exits 0 with no warnings.

Reversal: remove the section, delete the three router files and
`.crux-flow/receipts/technology.json`, and materialize again.

Two things to decide first, both the engine owner's:

- `AGENTS.md` rule 2 forbids any `@ai-sdk/*` dependency, and `packages/ai-engine/package.json`
  lists `@ai-sdk/openai-compatible` under `devDependencies` (ADR-0028 allows an AI SDK provider
  behind the engine's port). The router's `model-providers` row repeats the rule as written.
- The engine has no pinned captures. Its rows read project pages and official documentation only.
  `anthropics/skills` `claude-api` and `mcp-builder` are in the catalog as candidates to file with
  `ingest-research`.

## Safe order: Interview Studio

The Studio runs upstream Crux. Nothing here activates Flow there, and `crux-flow init` must not be
run in it.

1. Steps 1 and 2 above (the launcher must be the new release).
2. Add `studio/.crux-flow.yml` at the Studio root. Upstream Crux does not read that file.
3. `crux-flow --repo <studio> technology check`: BROKEN with the two rows in `CHANGES-NEEDED.md`.
4. Make the one-line description edit; decide the stale lines; the check is then clean.
5. Optional: call the check from the Studio's own verification.
