# Crux Flow 0.2.0 — operator guide

Crux Flow retains Crux 3.25.1's knowledge tree, authored skills, record numbering,
promptbook schema and preserving run writer. The fork adds proportionate execution
policy, scoped model maintenance and host lifecycle operations. It does not install
a daemon, database, alternate book format or custom model gateway.

## Start from this source checkout

Install Python 3.13 or newer and `uv`. A coding host is needed for that host's
installation, not for inspecting configuration, building packages or running tests.
From the extracted source directory:

```sh
pnpm run setup
```

Without pnpm, use the equivalent entrypoint:

```sh
uv run --script ./crux-flow setup
```

Setup displays one plan, installs the detected supported coding hosts and retains
short `crux-flow` and `crux-local` commands under `~/.local/bin`. It skips missing
optional hosts. It refuses an unrelated command at the same path, local edits to
managed files, and ambiguous duplicate Flow installations. It does not install
host executables or change your shell startup files. When an upstream Crux plugin is
enabled for you, Flow is installed but kept inert at user scope, so every repository
that has not run `crux-flow init` stays on exactly one workflow plugin: upstream. Add `~/.local/bin` to PATH if
the report says it is absent. Restart the coding host to load generated roles.

Every lifecycle command acts on all detected hosts by default. `--host claude`,
`--host codex`, `--host opencode` or `--host omp` is an optional filter for
troubleshooting. For a preview, use `pnpm run setup --dry-run`. The word `run` matters: `pnpm setup`
is pnpm's own command, not this repository's installer.

Aggressive is already the default. In a consumer repository, ask the enabled Flow
skill to implement the change. A read-only question does not start a run. Native
skill selection remains host-driven; the declared triggers are not a deterministic
parser. The explicit `flow` skill is the reliable entrypoint when routing is unclear.

## Activate Flow in a repository

```sh
cd my-project
crux-flow init
```

`init` is the normal onboarding command. It detects the installed host executables,
ensures Claude and Codex have the Flow marketplace and plugin installed through
the same lifecycle service as `install`, and skips native registration and plugin
installation steps that are already satisfied. It then creates a
small `.crux-flow.yml` when absent (`--mode` selects `aggressive`, `balanced`,
`thorough` or `upstream`), configures each host through its official seam, projects
the Flow roles, initializes the Bionic documentation tree when absent (`--no-docs`
skips it; it is one native model turn and needs the same approval), and verifies
each host independently:

```text
Claude      configured
Codex       configured
OpenCode    skipped-not-installed
OMP         skipped-not-installed
```

`init` accepts `--host`, `--source`, `--host-home`, `--dry-run`, `--yes` and
`--plan-digest` for installation as well as `--mode` and `--no-docs` for repository
configuration. Missing native plugins install at user scope; repository activation
remains project scoped. OpenCode and OMP receive project agents and skills.
`--source` selects the release for missing installations; existing valid owned
plugins stay on their installed release. Use `upgrade` to change that release.
Dry-run includes missing native installation commands in the initialization plan.
Foreign installations without Flow ownership receipts remain a migration error.

| Host | What `init` changes in the repository |
|---|---|
| Claude | `.claude/settings.json` `enabledPlugins`: Flow on, upstream `crux` off |
| Codex | `.codex/config.toml` `[plugins."..."] enabled`: Flow on, upstream `crux` off. Codex applies project configuration only in a trusted repository; `init` reports `trusted_project` and an `attention` note when it is not |
| OpenCode, OMP | project `.opencode/` or `.omp/` agents, skills and (OMP) `modelRoles` aliases; these hosts have no plugin mechanism |

Plugin identities come from the host's own inventory, not from constants. Upstream
Crux stays installed; only this repository's setting changes, other repositories keep
their workflow, and unrelated plugins and settings are preserved. A second `init` is a
no-op. A Flow-owned setting that was edited since `init` is reported and never
overwritten. `crux-flow deinit` (or `crux-flow uninstall --scope project`) restores only
what Flow recorded and never deletes your documentation tree.

Plugin activation selects *which product* owns the repository. The mode
(`aggressive`/`balanced`/`thorough`/`upstream`) is execution policy *inside* Flow:
`mode: upstream` runs the preserved upstream semantics in Flow and does not re-enable
the separately installed plugin. Start a new host session to load the change.

In Codex CLI, open `/skills` and choose `crux-flow:flow`, or mention
`$crux-flow:flow` in your prompt. Flow skills do not register `/crux...` slash
commands. A `crux-flow` entry in the plugin mention picker identifies the plugin;
individual workflows are selected through the skill picker or `$` mentions.

## Build and install the release

### Developing Flow itself

The installed `crux-flow` launcher runs an immutable, content-addressed release under
`~/.local/share/crux-flow/releases/`, not your checkout. After changing source, run
`pnpm run refresh` (`crux-flow setup --yes`): it rebuilds, retains the new release only
if its content changed, reinstalls the hosts and repoints the launcher. Check freshness
at any time with `pnpm run version:check`: it exits 2 and names the fix when the launcher
differs from the checkout's current build. `./crux-flow ...` always runs live source.

`pnpm run hooks:install` enables the tracked `.githooks/post-commit` hook (it sets
`core.hooksPath`). After a commit that touches release inputs (`crux/`, `crux-flow`,
`LICENSE`, `FLOW_GUIDE.md`, `reference-source.json`) it runs `version --check` and, only if
the launcher is stale and those paths are clean, `setup --yes`. It is post-commit because the
release records the commit hash, and it never blocks a commit. Set `CRUX_FLOW_NO_HOOK=1` to skip it.

Install from the running CLI without supplying a source:

```sh
crux-flow install
```

An installed CLI uses its enclosing release marketplace and validates its manifest.
A checkout launcher builds a temporary release from its own source tree, even when
invoked from another repository. `--source` overrides this default with a release
directory or ZIP. `upgrade` still requires an explicit candidate release.

Refresh what is installed with `crux-flow update`. It uses the same default source,
touches only hosts that already have an owned installation, reports `no-op` when the
release is unchanged, and refuses to downgrade. `upgrade` is the strict variant for
an explicit, newer tested release.

```sh
pnpm run build
```

This prints the archive and unpacked local marketplace paths. Repeated default
builds reuse verified identical artifacts without changing their modification times.
For an explicit new output directory:

```sh
uv run --script ./crux-flow package --output /tmp/my-crux-flow-release
```

The complete marketplace is in `/tmp/my-crux-flow-release/release`; do not point
marketplace registration at its nested `engine/` directory. Its ZIP is also accepted
by the lifecycle CLI:

```sh
crux-flow --repo "$PWD" install --host codex --scope user --source /tmp/my-crux-flow-release/crux-flow.zip
crux-flow --repo "$PWD" install --host claude --scope user --source /tmp/my-crux-flow-release/crux-flow.zip
crux-flow --repo "$PWD" install --host opencode --scope project --source /tmp/my-crux-flow-release/crux-flow.zip
crux-flow --repo "$PWD" install --host omp --scope project --source /tmp/my-crux-flow-release/crux-flow.zip
```

`--dry-run` previews mutations; `--yes` approves the displayed operation but does
not override ownership or stale-plan checks. `--plan-digest` binds noninteractive
approval to the displayed plan. A native-only run needs no OpenRouter credential.
Each requested host is independent; a multi-host operation is not globally atomic.

Direct native marketplace routes are also available:

```sh
claude plugin marketplace add /tmp/my-crux-flow-release/release
claude plugin install crux-flow@crux-flow --scope user
codex plugin marketplace add /tmp/my-crux-flow-release/release
codex plugin add crux-flow@crux-flow
```

Native commands are probed against the executable when the lifecycle CLI is used.
Direct native installation does not create this CLI's ownership receipts. Do not
mix that route with a later managed takeover without reviewing the migration.
Codex native registration is user-scoped; use scoped role materialization for
per-project settings. Claude Code is supported; the executable-bearing package is
not advertised as a claude.ai or Cowork plugin. Desktop loading must be checked in
the actual client, not inferred from CLI installation.

## Publish a release

```sh
pnpm run release --to git@github.com:omnitech-solutions/crux-flow-marketplace.git
```

One command builds the release, inspects it, stages it into a clone of the distribution
repository, shows the plan (files added, changed and removed, the tag, the content
tree digest) and, once approved, pushes the branch and the `vX.Y.Z` tag atomically,
never forced. The repository root *is* the marketplace for both Claude and Codex.
With `gh` installed and a GitHub remote, it also attaches the ZIP to a release for
OpenCode, OMP and `crux-flow install --source`.

- Set `CRUX_FLOW_MARKETPLACE_REPO` instead of repeating `--to`. `--branch` defaults to `main`.
  `--dry-run` shows the plan and pushes nothing; `--no-github-release` skips the ZIP step.
- Publishing refuses an uncommitted source checkout (`--allow-dirty` overrides, for
  testing). Use a dedicated distribution repository; this source repository's root
  metadata is upstream's.
- Running it again for the same content is a no-op. A version that is already published
  with different content is refused: bump the Flow version first.
- Credentials in a remote URL are never printed. Authentication is whatever your Git
  and `gh` already use.

Users then install with `claude plugin marketplace add OWNER/REPO` and
`claude plugin install crux-flow@crux-flow --scope user`, or
`codex plugin marketplace add OWNER/REPO` and `codex plugin add crux-flow@crux-flow`,
followed by `crux-flow setup` and `crux-flow init`. A direct marketplace install creates
no ownership receipts, so an existing foreign installation still requires an
explicit migration before `init` can project roles.

## Modes and configuration

| Mode | Elapsed default | Implementation delegates | Initial reviewers | Repair/re-review cycles | Council rounds |
|---|---:|---:|---:|---:|---:|
| aggressive | 20 minutes | 0 | 1 | 1 | 0 |
| balanced | 60 minutes | at most 2 | at most 2 | 2 | at most 1 |
| thorough | 120 minutes | at most 4 | at most 3 | 3 | at most 2 |
| upstream | Upstream semantics | Upstream | Upstream | Upstream | Upstream |

Caps are maxima, not dispatch quotas. Balanced's second reviewer is a justified
specialist. Council calls require a material decision and a recorded justification.
Modes do not grant permissions or promise cheaper/faster models. Unsupported host
controls and equal effective model tiers are reported.

A project may omit configuration entirely, or use `.crux-flow.yml`:

```yaml
config_version: "1"
mode: aggressive
models: economical
```

```sh
crux-flow mode list
crux-flow --repo "$PWD" mode show --resolved --host codex --json
crux-flow --repo "$PWD" mode set balanced --scope project
crux-flow --repo "$PWD" mode materialize --host codex --scope project
```

Invocation preferences override project, personal and shipped preferences; security
constraints are not preference values. New defaults do not alter active runs.
Materialization prepares native role files for the next compatible host session.
Active pinned runs refuse incompatible project materialization. No setting claims
to change a model already loaded in a running conversation.

## Technology guidance

A project can tell Flow which technologies it uses, what an agent should read before touching each
part of the repository, and which commands the project owns. Flow turns that into one small router
skill in the project, checks that everything it names exists, and reports it as one more row of
`check-drift`. A project that does not ask for it behaves exactly as before.

It is guidance. It adds no step to a run, no gate, no permission and no new workflow.

| Part | Where | Who owns it |
|---|---|---|
| The catalog: each technology, how a manifest declares it, its trigger terms, its official documentation, and any vetted upstream skill source with its licence | `crux/catalog/flow-technology.json`, shipped with Flow | Flow |
| The reference families a project may route to by choice, each with its trigger terms and what its page must hold. `application-boundaries`: who owns transport, services, domain rules, repositories and UI composition, as the project's own page derived from its own packages and folders, with no universal directory structure | the `references` section of `crux/catalog/flow-technology.json` | Flow names the family; the project writes the page |
| The layers of this repository | the `technology` section of `.crux-flow.yml` | the project |
| The router skill | `.agents/skills/<router>/SKILL.md`, with a copy under `.claude/skills` and `.omp/skills` when those folders are not links to it | Flow when `owner: flow`; the project when `owner: project` |
| The pins | the project's research source pages (`source_url` with a commit, `captured_at`, `raw_path`) | the project, through `ingest-research` and the refresh skills |

Detection reads `package.json` files, the pnpm catalog, `Package.swift` and compose files. It runs
nothing and never enters `node_modules`, `dist`, `build`, `vendor` or a dot-directory. A version is
the string a manifest declares; where none is declared it is shown as `unknown`.

### The `technology` section

| Key | Required | Meaning |
|---|---|---|
| `owner` | yes | `flow`: the router is generated and owned by Flow. `project`: the router is the project's own file; Flow checks it and never writes it. |
| `layers` | yes | One entry per part of the repository that has its own rules. |
| `router` | no | The router skill's name. Default `technology-references`. One per repository. |
| `map` | no | With `owner: project`, a hand-written page that explains the layers. The check accepts a reference named there or in the router. |
| `preload` | no | Roles whose generated role file starts with the router (see Delegates). |
| `exclude` | no | Catalog id to reason, for a technology a manifest declares and the project chooses not to route. |
| `never` | no | Catalog ids this repository must not use. One appearing in a manifest, or in a layer, is BROKEN. They are checked and deliberately not printed in the router. |

Each layer:

| Key | Required | Meaning |
|---|---|---|
| `id` | yes | Lowercase name, unique in the section. |
| `paths` | yes | Paths or globs the layer covers. Each must match a file. |
| `technologies` | no | Catalog ids that apply. Each must be declared by a manifest. |
| `references` | no | Catalog reference ids that apply, such as `application-boundaries`. A reference is a page the project writes, so the layer must name that page under `read.project`. No manifest declares one and no project is obliged to route one. |
| `read.project` | no | The project's own pages, read first. |
| `read.captures` | no | Research source pages, read second. Each must carry a pin and an existing raw capture. |
| `rules` | no | Decisions and invariants that govern the layer. |
| `commands` | no | Bindings the project owns: `id`, `cwd`, `argv` (an argument vector), `risk` (`read-only`, `writes`, `destructive`, `external`) and optional `requires`. A `pnpm`, `npm` or `yarn` script must exist in the `package.json` at `cwd`. |
| `never` | no | Sentences printed in the layer's row. |

The official documentation is read third and comes from the catalog. Unknown keys are refused, as
everywhere in `.crux-flow.yml`. The section is not part of the effective policy: editing it changes
no policy digest and never disturbs an active run.

### Commands

```sh
crux-flow --repo "$PWD" technology check              # offline, read-only; exit 0 clean or not applicable, 1 drift or broken
crux-flow --repo "$PWD" technology check --upstream   # also asks each commit-pinned source whether it moved
crux-flow --repo "$PWD" technology sync --dry-run     # the plan, nothing written
crux-flow --repo "$PWD" technology sync               # writes a Flow-owned router after approval
```

`crux-flow init` shows the same plan as one step of its own and applies it with the rest. With
`owner: project`, and with no section, neither command writes anything. `sync` on a project-owned
router prints the generated region so it can be pasted into that router.

### How drift is reported

The roster row in `check-drift` runs `generate-technology-references.py --dry-run`.

| Verdict | When | Fix |
|---|---|---|
| N/A | No `technology` section. | None. |
| clean | The router equals what the section, the catalog and the manifests produce, and every check passes. | None. |
| DRIFT | A layer was edited, or a manifest now declares a different version. | `crux-flow technology sync`. The report lists `refresh_sources`: refresh those pages with `refresh-research-sources`. |
| BROKEN | A referenced page, pin, raw capture, command or path does not exist; a declared technology is neither routed nor excluded; a routed or excluded technology is not declared; a `never` technology is declared; a second skill carries the router's name; a foreign file sits where a Flow-owned router belongs. For a project-owned router also: its description lacks a routed technology's trigger term or names one that is not routed, or neither it nor the map names a configured reference or command. | Repair the named input. Regenerating repairs nothing. |

A hand-written router with no generated region is checked for everything except version changes,
and the report says so. Whether a pinned source moved upstream is a separate, online question:
`check --upstream` reports `current`, `stale` or `unknown`, and an unreachable source is `unknown`.

### Delegates

| Host | What `preload` does | Certainty |
|---|---|---|
| Claude Code | The role file's `skills:` lists `flow` and the router; Claude injects both at subagent start. | Certain when the name resolves. |
| Codex | The role file gains one `[[skills.config]]` naming the router. Codex lists it; it does not inject the body. | Listed for certain; read by the model's choice. |
| OpenCode, OMP | Nothing: neither has a role field that injects a skill. The role report lists `technology-router-preload` under unsupported controls. | Not available. |

In an ordinary session the carriers are the router's description and one line in the root
`AGENTS.md`. Flow proposes that line and never writes the file. Without a `technology` section the
generated role files are byte-identical to those of a release without this feature.

### Adopt it in a project

1. Install the Flow release that understands the section on every machine that works in the
   repository. An earlier release refuses the unknown `technology` key and stops every Flow command
   there.
2. `crux-flow technology check` in a repository with no section lists under `eligible` what the
   manifests declare.
3. Write the section (the `maintain-technology-skills` skill proposes one). Route or exclude every
   declared technology.
4. `crux-flow technology sync --dry-run`, then `sync`. Add the suggested line to `AGENTS.md`.
5. `crux-flow mode materialize` so the `preload` roles carry the router. Restart the host.
6. `crux-flow technology check` must be clean.

### Deliberately not built

No second installer, runtime, rules language, lock file or export command. No vendor skill bundle
is copied into a project or into Flow: a reference is read from a pinned capture, not installed. No
model decides which technology applies; a manifest does. Flow never fetches, executes or enables
anything a source ships, and never writes a project's `AGENTS.md`, `CLAUDE.md` or hand-written
router. Manifest kinds beyond npm, pnpm, Swift packages and compose files are not read yet.

### Example: a Flow-owned router (the AI engine)

```yaml
config_version: "1"
mode: aggressive
technology:
  router: technology-references
  owner: flow                       # Flow generates the router and owns it
  preload: [developer, reviewer]    # delegated roles that start with the router
  never: [nextjs, react]            # the engine is an embedded SDK: no web framework, no UI
  layers:
    - id: engine-core
      paths: ["packages/ai-engine/src/*.ts", "packages/ai-engine/src/contracts/**"]
      technologies: [typescript, node, zod]
      read:
        project:
          - bionic/research/references/contract.md
          - bionic/research/references/versioning.md
          - bionic/adrs/ADR-0032-one-entry-point-a-host-names-a-provider-or-runtime.md
          - bionic/adrs/ADR-0006-every-operation-fails-with-one-typed-failure-and-a.md
      rules: [AGENTS.md]
      commands:
        - {id: verify, cwd: ".", argv: [pnpm, run, verify], risk: read-only}
      never:
        - "an import path other than the one entry point"
        - "an environment read inside the engine"
    - id: model-providers
      paths: ["packages/ai-engine/src/providers/*.ts"]
      technologies: [anthropic-sdk, openai-sdk]
      read:
        project:
          - bionic/adrs/ADR-0002-the-engine-is-a-thin-layer-over-provider-sdks-with.md
          - bionic/adrs/ADR-0004-one-model-contract-and-streaming-port-ending-in-on.md
          - bionic/adrs/ADR-0028-any-ai-sdk-provider-may-sit-behind-the-engines-own.md
      rules: [AGENTS.md]
      commands:
        - {id: no-frameworks, cwd: ".", argv: [pnpm, run, "verify:no-frameworks"], risk: read-only}
        - id: live
          cwd: "."
          argv: [pnpm, run, "test:live"]
          risk: external
          requires: ["provider credentials supplied by the host", "the owner's approval for paid calls"]
      never:
        - "an AI framework dependency (AGENTS.md rule 2)"
        - "a provider SDK import outside providers/"
        - "a plain thrown error reaching a caller"
    - id: agent-runtimes
      paths: ["packages/ai-engine/src/providers/agents/**"]
      technologies: [claude-agent-sdk, codex-app-server, mcp]
      read:
        project:
          - bionic/adrs/ADR-0018-agent-runtimes-are-a-distinct-kind-of-provider.md
          - bionic/adrs/ADR-0038-an-agent-runtime-is-given-tools-the-engine-serves.md
      rules: [AGENTS.md]
      commands:
        - {id: test, cwd: ".", argv: [pnpm, run, test], risk: read-only}
      never:
        - "an agent process started by importing the entry point"
    - id: storage
      paths: ["packages/ai-engine/src/store/**", "scripts/db-setup.ts", "docker-compose.yml"]
      technologies: [drizzle, node-postgres, postgresql, docker-compose]
      read:
        project:
          - bionic/adrs/ADR-0016-storage-is-a-drizzle-schema-and-two-repository-vie.md
      rules: [AGENTS.md]
      commands:
        - {id: db-up, cwd: ".", argv: [pnpm, run, "db:up"], risk: writes, requires: ["Docker is running"]}
        - {id: db-setup, cwd: ".", argv: [pnpm, run, "db:setup"], risk: writes, requires: ["db-up has completed"]}
        - {id: db-reset, cwd: ".", argv: [pnpm, run, "db:reset"], risk: destructive, requires: ["the owner's approval"]}
    - id: logging
      paths: ["packages/ai-engine/src/logging/**"]
      technologies: [pino]
      read:
        project:
          - packages/ai-engine/src/logging/README.md
          - bionic/adrs/ADR-0024-the-engine-logs-by-configuration-and-a-line-never.md
          - bionic/adrs/ADR-0036-the-engine-owns-its-logger-it-logs-by-default-with.md
      rules: [AGENTS.md]
    - id: tests
      paths: ["packages/ai-engine/src/**/*.test.ts", "vitest.config.ts"]
      technologies: [vitest]
      rules: [AGENTS.md]
      commands:
        - {id: test, cwd: ".", argv: [pnpm, run, test], risk: read-only}
        - {id: coverage, cwd: ".", argv: [pnpm, run, "test:coverage"], risk: read-only}
```

### Example: a project-owned router (Interview Studio)

The Studio runs upstream Crux and wrote its router by hand. Its file holds only the section.

```yaml
config_version: "1"
technology:
  router: technology-references
  owner: project                    # the Studio wrote its router by hand; Flow checks it and never writes it
  map: bionic/research/references/technology-references.md
  exclude:
    typescript: "language baseline; Biome and tsc in pnpm verify govern it and no reference is filed"
    node: "runtime baseline pinned by engines; no reference is filed"
    node-postgres: "reached only through Drizzle and withTenant(); the Drizzle row governs it"
    vitest: "test runner; the repository's own test conventions govern it and no reference is filed"
    playwright: "browser tests live in one package with its own runner script; no reference is filed"
    docker-compose: "starts the development services; no application code depends on it"
  layers:
    - id: react-ui
      paths: ["products/*/src/frontend/**"]
      technologies: [react]
      read:
        captures:
          - bionic/research/sources/vercel-composition-patterns.md
          - bionic/research/sources/vercel-react-best-practices.md
      rules:
        - bionic/adrs/ADR-0004-build-products-as-verticals-inside-a-modular-monol.md
        - bionic/adrs/ADR-0002-simplicity-first-the-least-complex-design-that-mee.md
        - bionic/invariants/product-frontend-never-imports-apps-web.md
    - id: native-swift
      paths: ["apps/studio-shell/**", "apps/capture-companion/macos/**"]
      technologies: [swift]
      read:
        captures: [bionic/research/sources/swift-concurrency-agent-skill.md]
      rules: [bionic/adrs/ADR-0019-host-the-overlay-in-a-native-shell-through-one-host-adapter.md]
    - id: nextjs
      paths: ["apps/web/**"]
      technologies: [nextjs]
      rules:
        - bionic/adrs/ADR-0004-build-products-as-verticals-inside-a-modular-monol.md
        - bionic/invariants/product-domain-never-imports-nextjs.md
        - bionic/invariants/nextjs-never-launches-agent-processes.md
    - id: hono
      paths: ["products/*/src/backend/**", "packages/platform-api/**"]
      technologies: [hono]
      rules: [bionic/invariants/product-routes-resolve-membership-first.md]
    - id: zod
      paths: ["packages/*-contracts/**"]
      technologies: [zod]
      rules: [bionic/adrs/ADR-0003-keep-package-boundaries-narrow-with-one-public-ent.md]
    - id: drizzle
      paths: ["packages/database/**"]
      technologies: [drizzle]
      read:
        captures:
          - bionic/research/sources/drizzle-orm-schema-declaration.md
          - bionic/research/sources/drizzle-orm-migrations.md
          - bionic/research/sources/drizzle-kit-generate.md
          - bionic/research/sources/drizzle-kit-migrate.md
          - bionic/research/sources/drizzle-orm-row-level-security.md
          - bionic/research/sources/drizzle-orm-transactions.md
      rules:
        - bionic/adrs/ADR-0005-isolate-tenants-in-one-postgresql-cluster-with-own.md
        - bionic/adrs/ADR-0023-use-the-query-builder-by-default-and-check-the-dat.md
        - bionic/invariants/tenant-drizzle-handle-only-via-with-tenant.md
        - bionic/invariants/schema-files-and-migrations-agree.md
        - bionic/invariants/tenant-owned-tables-force-rls.md
      commands:
        - {id: generate, cwd: packages/database, argv: [pnpm, run, "db:generate"], risk: writes}
        - {id: migrate, cwd: ".", argv: [pnpm, run, "db:migrate"], risk: writes, requires: ["the development PostgreSQL is running"]}
        - {id: arch, cwd: ".", argv: [pnpm, run, "docs:arch"], risk: writes}
        - {id: arch-check, cwd: ".", argv: [pnpm, run, "docs:arch:check"], risk: read-only}
      never: ["drizzle-kit push"]
    - id: postgresql
      paths: ["packages/database/drizzle/**", "compose.yaml"]
      technologies: [postgresql]
      rules:
        - bionic/adrs/ADR-0005-isolate-tenants-in-one-postgresql-cluster-with-own.md
        - bionic/invariants/tenant-owned-tables-force-rls.md
    - id: ai
      paths: ["apps/agent-worker/**"]
      technologies: [claude-agent-sdk]
      read:
        project:
          - bionic/research/references/ai-execution-boundaries.md
          - .agents/skills/ai-provider-maintainer/SKILL.md
      rules:
        - bionic/adrs/ADR-0007-route-ai-work-through-aiexecutiongateway-profiles.md
        - bionic/invariants/products-never-branch-on-provider-names.md
        - bionic/invariants/nextjs-never-launches-agent-processes.md
    - id: review
      paths: ["apps/**", "packages/**", "products/**"]
      rules:
        - bionic/adrs/ADR-0003-keep-package-boundaries-narrow-with-one-public-ent.md
        - bionic/invariants/package-boundaries-hold.md
      commands:
        - {id: verify, cwd: ".", argv: [pnpm, run, verify], risk: read-only}
```

## Orchestrating delegates

One orchestrator; every delegate is a leaf. In aggressive, balanced and thorough modes the primary
session dispatches every delegate and reviewer itself, and a delegate never starts an agent,
sub-agent or worker of its own: nested delegation exhausts the host's concurrency cap, multiplies
usage, escapes the mode's caps and puts work where nobody can see or stop it.

| Where | What enforces it |
|---|---|
| Generated role files (Claude Code, OpenCode, OMP) | No delegation tool in the three Flow modes: `Agent` removed and denied, a subagent deny rule, no spawns (`hosts.py`; `test_no_flow_mode_role_can_delegate`) |
| Every generated role, every host | The first instructions say "You are a leaf" |
| Every brief | The `flow` skill requires the sentence in each delegation |
| Upstream mode | Not applied: upstream's roles and their chain are unchanged (`test_upstream_mode_keeps_upstream_delegation_grants`) |

What a long many-delegate session showed to work, and its pitfalls, is in
`crux/skills/flow/references/orchestration.md`. It is read when a run fans out; it adds no step,
gate or permission.

## Executable work and durable continuation

The Flow skill creates a small project-owned JSON specification. Use actual project
commands, not the illustrative command below, for real acceptance:

```json
{
  "goal": "Deliver the bounded change",
  "change_kind": "additive",
  "outcomes": [{
    "id": "behavior",
    "outcome": "The requested behavior works",
    "evidence": "The reproduction and affected tests demonstrate it",
    "constraints": ["Preserve the supported public contract"],
    "writes": ["src/", "tests/"]
  }],
  "checks": [{
    "id": "required-tests",
    "argv": ["python3", "-m", "unittest", "discover"],
    "inputs": ["src", "tests", "pyproject.toml"],
    "toolchain": "Project Python environment"
  }]
}
```

The documentation root must already be initialized. `init-docs --host HOST` uses
the selected installed host's exact skill, with explicit execution authorization;
it refuses to overwrite an existing nonempty documentation tree. Its output is
checked rather than accepting the host's exit code alone.

```sh
crux-flow --repo "$PWD" run start --host codex --spec change.json
crux-flow run status --file PATH_PRINTED_BY_START
crux-flow run resume --file PATH_PRINTED_BY_START
```

The existing run snapshot's prompt states are the sole progress authority. The
versioned Flow extension holds contracts, frozen policy, attempts and evidence.
The current unit advances through `run advance --file RUN --result 'Observed result'`.
A declared check is executed with `run check --file RUN --label required-tests`.
A final independent review is recorded with `run review --file RUN --report REPORT`;
the report declares `reviewer`, `independent: true`, `evidence`, and `findings`.
The CLI records an external attestation, not an authenticated reviewer identity.
Advancing without fresh required checks and review is refused by the shared writer.

`run drive --file RUN --yes` owns consecutive native CLI turns under the remaining
budget; it can consume model usage. It is not a background daemon. If the owning
process itself exits, restart it explicitly with the same run; a skill cannot
restart a closed Desktop application. A recoverable turn or reviewer outage does
not itself complete a run. Failed required checks remain incomplete and return a
nonzero CLI exit status.

Explicit owner changes use `run transition --file RUN --mode balanced --reason
'Owner approved additional scope' --owner-approved`. `--budget-minutes` changes the
total allowance from the original start, not a fresh timer. Historical Flow v1 and
upstream records can be inspected with `run history`. Use an explicitly approved,
linked successor for changed execution semantics; never rewrite completed history.

Verification fingerprints include changed documents that checks consume. `inputs:
["."]` uses tracked and non-ignored untracked files in a Git repository; name an
ignored/generated input explicitly when it is consumed. In an unversioned directory
the bounded filesystem walk is conservative. Transaction journals, locks and the
run's own mutable bookkeeping do not invalidate their own evidence. An independent
review includes the project input boundary, not just a self-reported file list.

## Model discovery, review, activation and rollback

```sh
crux-flow models list --host codex
crux-flow models resolve --host codex --role developer --mode aggressive
crux-flow models refresh --host openrouter --output /tmp/model-discovery.json
crux-flow models refresh --host codex --output /tmp/codex-model-discovery.json
crux-flow models refresh --host claude --output /tmp/claude-model-discovery.json
```

`list` shows pinned assignments, not entitlement to every remote model. Discovery
reads public sources or supported bundled host metadata, not authentication stores.
Missing capabilities, native billing, entitlement and runtime loading remain unknown.

To stage a change, add `--changes changes.json --scope project` to refresh. The file
contains an array such as `[{"host":"codex","kind":"level","name":"standard",
"model":"EXACT_ID_FROM_DISCOVERY"}]`. Add `--require tools` or other recorded
capabilities when required. Unsupported effort/capability choices are refused;
no model ID is fabricated from its family name.

```sh
crux-flow --repo "$PWD" models apply --proposal /tmp/model-proposal.json
crux-flow --repo "$PWD" models rollback --scope project --transaction TRANSACTION_ID
```

Apply revalidates current discovery, the reviewed digest, source and configuration.
Project bindings and their scoped projections change in one recoverable transaction.
User-scope activation retains staged projections for later selected-project loading.
Source-scope changes require an editable source checkout; installed payloads are
immutable. Upstream assignments remain separate. Rollback refuses intervening user
edits. Unattended activation is opt-in and requires exact model/vendor approvals,
capabilities, measured cost limits, pin preservation and data-retention approval.
Unknown material facts refuse unattended activation. No refresh runs on ordinary
status, installation, validation or coding tasks.

## Lifecycle, inspection and recovery

```sh
crux-flow status --json
crux-flow doctor --strict --json
crux-flow upgrade --source /path/to/tested-new-release.zip
crux-flow rollback --scope user
crux-flow uninstall --scope user
crux-flow doctor --host codex          # optional filter
crux-flow transactions inspect --scope project --transaction TRANSACTION_ID
crux-flow transactions recover --scope project --transaction TRANSACTION_ID
```

Recovery restores a prepared/interrupted scoped file transaction. A committed
transaction uses `transactions rollback`. Public inspection omits backup contents;
private recovery journals are owner-readable and may contain original configuration
bytes. Do not commit or publish journals. Native plugin registration is a separate
side effect: a failed compensation is reported as `recovery-required`, never as a
successful installation. Inspect the report and retained release before retrying.

Uninstall and installation rollback preserve unrelated files and OMP settings.
Owned release payloads remain available for active runs and rollback. Uninstalling
a host does not delete project knowledge or remove the shared CLI used by other
hosts. `crux-local` forwards its six original command names to these same services;
`install-crux-env` uses the managed install route and the same automatic source
selection as `install`, with an optional `--source` override.

## Repeatable upstream updates

```sh
crux-flow upstream check                  # offline, read-only: vendored version, surface state, what blocks the procedure
crux-flow upstream check --fetch          # also asks the upstream repository for its latest release tag
crux-flow --repo "$PWD" upstream prepare --ref latest --output /tmp/crux-next-candidate
crux-flow upstream verify --candidate /tmp/crux-next-candidate --full
```

`check` exits 0 when the surface record matches the live surface and 1 on DRIFT or BROKEN (see below); a newer upstream
release is not a failure. It reports `procedure.ready` and the blockers: a real Git checkout, a clean tree, and the pinned
upstream commit as an ancestor of `HEAD`.

`prepare` requires all three. It resolves an exact release, clones an isolated candidate, replays the fork commits, updates the
candidate's upstream pin and regenerates the owned outputs: `validate-catalog.py`, `generate-runtime-compat.py`,
`generate-routing-table.py` and, last, `generate-flow-surface.py`, which refuses to write while an invariant is broken. A
conflicting replay or a failed regeneration refuses activation. `verify` checks the recorded candidate identity and runs the
catalog check, the surface check, the nonrecursive tests and packaging. Neither installs, publishes or changes the working branch.

### The surface record

`crux/surface/record.json` is generated by `crux/scripts/generate-flow-surface.py` from the live source and
`crux/surface/declaration.json` (what Flow requires, which formats it supports, which upstream files it patches, which upstream
text it rewrites). It pins:

| Section | Content |
|---|---|
| `flow_cli` | every `crux-flow` command with its flags, requirements and choices |
| `upstream_scripts` | for each upstream script Flow calls: flags, seam function argument names, `main` exit codes |
| `imports` | each upstream module Flow imports and whether each symbol it uses still exists |
| `skills` | every skill: owner (`flow` or `upstream`), a digest of its frontmatter, the scripts its body calls |
| `roles` | each role's tools and delegation targets per host and mode (models are left out) |
| `schemas` | run and promptbook `format_version`, the Flow extension versions, manifest `schema_version`, `docs_dir` default, policy and technology versions |
| `drift_roster`, `patched_upstream_files`, `text_rewrites`, `hooks` | the roster scripts, a digest of each patched upstream file, whether each rewritten text is still found, hook files |

```sh
python3 crux/scripts/generate-flow-surface.py --dry-run   # exit 0 clean; 1 with JSON on DRIFT or BROKEN; the check-drift row
python3 crux/scripts/generate-flow-surface.py             # rewrites the record whole; refuses while BROKEN
```

BROKEN (hard) means an essential does not hold, so regenerating would bless a broken surface: an upstream script or a flag
Flow passes is gone; a seam function changed its arguments; an imported symbol is gone; a role gained a delegation target; a
run, promptbook or manifest format Flow does not read appeared; the roster lost the surface row; rewritten upstream text is no
longer found. DRIFT means the record is out of date. Within it, a removed command, upstream script, import, roster row or skill is
`hard`; everything else (an added flag, a changed skill contract, a role losing a tool, a moved upstream version) is a
`notice`. Read the changes, then regenerate in the same commit as the change that caused them.

Not built: `crux-flow upstream update`, one verb that runs prepare, verify and the regeneration and refuses on a hard failure.
`prepare` already regenerates the record and `verify` already checks it, so the verb would only chain them; it waits for a
checkout with real ancestry, where it can be tested on a real release.

This source archive preserves reconstructed source provenance; it does not invent the original Git history. For long-term
upstream maintenance, integrate it on a branch rooted at the recorded Crux 3.25.1 commit using the accompanying upstream patch.
Daily operation and packaging do not need that historical checkout.

## Verification and platform boundary

```sh
uv run --group test python -m pytest
uv run --group test python tools/verify.py --core
uv run --group upstream-test python tools/verify.py --full
```

Beyond the unit tests: `tests/flow/test_scenarios.py` drives the real CLI and upstream's own writer through scripted scenarios
(`tests/flow/scenarios/*.json`, one claim each); `tests/flow/test_run_properties.py` walks the run state machine with seeded random
actions; `tests/flow/test_behaviour_live.py` (opt in) asks free OpenRouter models whether they follow the skill's rules and reports a
rate. The README's "How to prove it" lists each command.

The full upstream group declares Crux's pinned grammar/extractor dependencies. The
verifier preflights dependencies before starting broad suites. Offline host adapters
use isolated files and injected native-command responses. Opt-in real native tests
use `CRUX_FLOW_NATIVE_TESTS=1`; paid delivery exercises are separate. See
`COMPLETION_REPORT.md` and `verification/` for the exact tests executed for this
artifact. Do not equate adapter tests or a valid manifest with actual Desktop load.
The owned-process and filesystem implementation targets macOS and Linux; Windows
is not claimed for this release.
