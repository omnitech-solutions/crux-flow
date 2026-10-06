# Init ensures native installation

Decision: repository onboarding ensures missing Claude/Codex marketplace and
plugin installations using the same lifecycle planning, replan and application
functions as `install`. It then activates Flow and projects roles in the
repository. Registration and plugin installation are checked independently;
valid owned installations are preserved. OpenCode/OMP retain project surfaces.
The CLI accepts source and host-home overrides in addition to the existing host,
dry-run, yes and plan-digest arguments. Scope is determined by host capability:
native installation is user scoped and repository activation is project scoped.

Source selection is shared with `install`. For a partially installed native host,
default initialization uses its owned retained release or existing registered
marketplace. A conflicting explicit source is rejected rather than installing a
different plugin from the existing marketplace. Foreign plugin installations
without ownership receipts still require explicit migration.

The cold-start regressions failed before the change with `SystemExit: 2` and
`crux-flow: error: unrecognized arguments: --source .../build/release`.
After the change, the following commands completed successfully and their logs
were opened:

- `uv run --group test python -m pytest tests/flow/test_init.py tests/flow/test_product_commands.py tests/flow/test_lifecycle.py -q --tb=short`:
  `62 passed in 119.44s (0:01:59)`.
- `CRUX_FLOW_NATIVE_TESTS=1 uv run --group test python -m pytest tests/flow/test_native.py -k real_init_installs -q --tb=short`:
  `2 passed, 8 deselected in 14.05s`.

The regressions cover missing native installation, dry-run without writes,
repeat initialization skipping native mutation commands, and separate missing
marketplace/plugin states. The real-host tests use disposable homes and custom
host-home paths, disable documentation generation, and verify actual repository
activation plus a second invocation without installation commands.

Live inspection in `omni-ui-components` found the Flow marketplace registered
and plugin installed/enabled in both native hosts before this change.
`codex debug prompt-input` returned valid JSON containing
`crux-flow:flow` and `crux-flow:init-docs`. This establishes fresh CLI discovery;
it does not establish the skill list in the operator's already-open Codex UI.

The owned CLI launchers were refreshed through the setup service with an empty
host selection, preserving host installations. The launcher was re-read and
targets retained release
`bf48ae00b2704bcfb117e7fee6803a2852fa7ab11fbdb853811914692c16762e`.
`crux-flow init --host codex --no-docs --yes` in `omni-ui-components` exited 0;
its JSON was opened and reported `status: completed`, Codex `already-correct`,
`effective_in_repository: true`, `trusted_project: true`, and
`requires_restart: false`.

Performance measurement on this machine: the same installed CLI command
`crux-flow init --host codex --no-docs --dry-run` in the same repository took
1.119, 1.023, 0.946 seconds before and 1.797, 3.327, 1.844 seconds after.
All six invocations exited 0 and returned `status: planned`. Median was 1.023
seconds before and 1.844 seconds after. The added plugin and marketplace
inventory checks cost time; these measurements do not predict CI duration.

Logs: `2026-10-06-init-install-tests.log` and
`2026-10-06-init-native-tests.log`. Live JSON artifacts and timing samples are
under `/tmp/crux-flow-omni-init-result.json`,
`/tmp/crux-flow-omni-codex-prompt.json`, and
`/tmp/crux-flow-init-{before,after}-timing.json`.

Not done: full suite, documentation generation/model calls, restarting or
interacting with the operator's running Codex UI, foreign-installation migration,
committing or pushing changes.
