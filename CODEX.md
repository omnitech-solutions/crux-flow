# Crux Flow for Codex

Crux Flow installs as the `crux-flow` plugin from its own `crux-flow` marketplace.
It is a separate plugin from upstream Crux (`crux@crux`): installing or updating
one never changes the other, and `crux-flow init` enables Flow per repository while
leaving upstream off there.

## Install and activate

```sh
crux-flow install        # Claude and Codex: marketplace + plugin; skips what is already installed
cd my-project
crux-flow init           # same installation check, then activates Flow in this repository
```

`init` with no arguments delegates to the same lifecycle service as `install`, so a
fresh machine needs only `crux-flow init`. Preview either with `--dry-run`. Do not
run `codex plugin marketplace add` or `codex plugin add` by hand; Flow's marketplace is
a verified local release that `crux-flow` registers and records ownership for.

## Update

```sh
crux-flow update         # refresh installed hosts from the running package; no-op if current
crux-flow upgrade --source <release>   # move to an explicit newer tested release
```

`update` never installs a host that is not already installed and refuses to downgrade.

## Using the skills

Flow skills are named `crux-flow:<skill>` (for example `crux-flow:flow`,
`crux-flow:init-docs`). In Codex CLI choose them from `/skills` or mention
`$crux-flow:<skill>`. Start a new Codex session after install or `init`; a running
session does not reload plugins. If `/skills` does not list them in a repository,
run `crux-flow doctor --host codex` and check that the repository is trusted.

See `FLOW_GUIDE.md` for modes, configuration and recovery.
