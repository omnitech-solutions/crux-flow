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
crux-flow upstream check
crux-flow --repo "$PWD" upstream prepare --ref latest --output /tmp/crux-next-candidate
crux-flow upstream verify --candidate /tmp/crux-next-candidate --full
```

Preparation requires a clean, real Git checkout containing the pinned upstream
ancestor. It resolves an exact release, clones an isolated candidate, replays the
fork commits, updates the candidate's upstream pin and regenerates owned catalogs.
A changed integration point or failed regeneration refuses activation. Verification
checks the recorded candidate identity and runs nonrecursive tests and packaging.
It does not install, publish or change the working fork branch.

This source archive preserves reconstructed source provenance; it does not invent
the original Git history. For long-term upstream maintenance, integrate it on a
branch rooted at the recorded Crux 3.25.1 commit using the accompanying upstream
patch. Daily operation and packaging do not need that historical checkout.

## Verification and platform boundary

```sh
uv run --group test python -m pytest
uv run --group test python tools/verify.py --core
uv run --group upstream-test python tools/verify.py --full
```

The full upstream group declares Crux's pinned grammar/extractor dependencies. The
verifier preflights dependencies before starting broad suites. Offline host adapters
use isolated files and injected native-command responses. Opt-in real native tests
use `CRUX_FLOW_NATIVE_TESTS=1`; paid delivery exercises are separate. See
`COMPLETION_REPORT.md` and `verification/` for the exact tests executed for this
artifact. Do not equate adapter tests or a valid manifest with actual Desktop load.
The owned-process and filesystem implementation targets macOS and Linux; Windows
is not claimed for this release.
