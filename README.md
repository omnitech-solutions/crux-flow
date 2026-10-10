# Crux Flow 0.2.0

A standalone, multi-mode fork of Bionic Crux 3.25.1. Aggressive is the default for
new work; balanced, thorough and upstream are available through the same system.

This is a complete source checkout. It includes the Crux runtime resources needed
by the fork and the completed assistant-authored implementation. It does not require
applying a patch, obtaining the earlier Codex branch, or reconstructing source exports.

## Install

With `uv`, Python 3.13 or newer, and your preferred coding host installed:

```sh
pnpm run setup
```

The equivalent without pnpm is:

```sh
uv run --script ./crux-flow setup
```

Setup shows one plan and installs every supported detected host (`--host` is an
optional filter; preview with `--dry-run`). It never installs host executables,
changes credentials or replaces foreign files, and it leaves Flow inert while an
upstream Crux plugin is enabled. Restart the host after installation, then in each
project run `crux-flow init`: it detects your hosts, installs any missing Flow
marketplace and native plugin, and selects Flow for that
repository through each host's official project setting, leaving other repositories
on upstream. Run `crux-flow update` to refresh installed hosts. In your project, invoke the Flow skill with the change you need. No separate default-mode setup is required.

## Build and verify

```sh
pnpm run build
pnpm test
pnpm run verify
```

`build` emits a self-contained local marketplace directory and release ZIP; `pnpm run release --to <git-url>` publishes it (see the operator guide).
`test` runs the Flow test suite. `verify` also runs the relevant original Crux
integration tests and derived-catalog checks. For the full upstream suite:

```sh
pnpm run verify:full
```

Commit `uv.lock` to keep project test and verification dependencies reproducible.
The `uv run --script` launchers use their separate inline dependency declarations.

The verifier checks dependencies before running the broader suites. Provider calls
are not part of ordinary tests; native host tests are opt-in and use disposable homes.

## Architecture

Flow uses Crux's existing multi-prompt books and preserving run writer. Its small
versioned extension stores contracts, effective policy and verification evidence;
the existing prompt states remain the single authority for progress. There is no
second workflow database, daemon, gateway or parallel legacy Flow implementation.

The canonical implementation lives in `crux/scripts/crux/flow/`. Authored policy,
model overrides and concise role instructions live in `crux/catalog/flow-*.json`.
The technology catalog is one more of those files; a project's `technology` section in
`.crux-flow.yml` turns it into a small router skill that `check-drift` verifies.
Catalogs and runtime-compatibility blocks are checked by the existing Crux generators.

## Documentation

- [Operator guide](FLOW_GUIDE.md): installation, modes, models, runs, recovery and upgrades.
- [Completion report](COMPLETION_REPORT.md): exact changes, tests and unobserved boundaries.
- [Integration seams](FLOW_SEAMS.md): the Crux components reused and the reasons for each boundary.
- [Verification evidence](verification/README.md): this artifact's executed checks and logs.

`USER_GUIDE.md`, `CODEX.md`, `OPENCODE.md`, `CHANGELOG.md` and `crux/README.md` are
retained upstream reference material. Use `FLOW_GUIDE.md` for this fork's installation
and operational commands. The root upstream marketplace metadata is not the built
Flow marketplace; setup/build generates the version-matched Flow package.

## Provenance and support boundary

This source is assembled from the supplied Crux 3.25.1 baseline and the recovered
reference implementation, then repaired and verified. `reference-source.json`
records that lineage without inventing Git history. Daily use and packaging do not
need a Git checkout. Preparing future upstream merges requires real history rooted
at the recorded upstream commit; a separate upstream-relative patch is provided for
that integration.

Owned-process execution and filesystem safety target macOS and Linux. A valid
package and offline adapter tests do not prove that a Desktop client loaded a plugin
or that an account can use a selected model. See the completion report for the exact
runtime observations. Upstream copyright and license notices are preserved in
[LICENSE](LICENSE).
