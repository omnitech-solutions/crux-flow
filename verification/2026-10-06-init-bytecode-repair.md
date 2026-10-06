# Repository initialization repair

The installed CLI and native host receipts pointed to retained release
`1f5fb8e55f462f10a74616f57919add245253f96c0d25de751bf1ecc86dfab1e`.
Its manifest-listed files were present, but eight generated Python bytecode files
under `crux-flow/engine/scripts/__pycache__/` made the content set differ.
The release inspector correctly refused these unlisted files. Codex's native
inventory also reported `crux-flow@crux-flow` disabled in `omni-ui-components`.

Decision: prevent direct packaged engine scripts from writing bytecode before
their imports execute. Packaging inserts the guard after the module docstring
and future imports, preserving inline dependency metadata. Source scripts remain
unchanged; release hashes describe the rendered scripts. Keep manifest checks
strict rather than ignoring executable cache files.

The regression runs packaged `bionic-config.py` without an environment setting
that suppresses bytecode. Before the change, the subsequent release inspection
failed with `FlowError: release content set differs from manifest`. After the
change, the script emits valid configuration JSON and leaves the manifest digest
unchanged with no bytecode files. A separate test adds an unlisted cache file,
observes the same rejection, removes the file, and revalidates the release.

The existing reconstructed-source test assumed this checkout had no Git history.
It failed with `AssertionError: assert 'b74ebaa6fc26ce8a88c2e0a249cfc2b385ff35a1'
is None`. It now explicitly supplies the no-Git condition it tests.

Recovery preserved the eight generated files outside the release at
`~/.local/share/crux-flow/crux-flow-bytecode-kaa54qze/`; no files were deleted.
The old retained release then passed inspection. Source `crux-flow setup --yes`
installed the corrected package for Claude, Codex, OMP and OpenCode and updated
the owned CLI launchers. Running `crux-flow init --yes` in
`~/dev/omnitech-solutions/omni-ui-components` returned `status: completed`, with
all four hosts configured. A second invocation returned `already-correct` for
all four hosts and `requires_restart: false`. Both native hosts reported
`effective_in_repository: true`; Codex reported `trusted_project: true`.

Verification:

- `uv run --group test python -m pytest tests/flow/test_packaging.py tests/flow/test_init.py tests/flow/test_lifecycle.py -q`:
  `56 passed in 77.88s`; output saved in `2026-10-06-init-bytecode-tests.log`.
- Direct execution of the newly installed `engine/scripts/bionic-config.py`
  resolved `bionic`; the release digest remained unchanged and contained zero
  bytecode files afterwards.
- `codex debug prompt-input` from the target repository listed Flow's skills,
  including `crux-flow:flow` and `crux-flow:init-docs`.
- `git diff --check` exited 0.

Not done: interacting with slash-command completion in a running Codex UI,
restarting the operator's sessions, paid model execution, or pushing changes.
Host inventory and fresh Codex prompt discovery establish configuration and
skill discovery; they do not establish loading in an already-running UI session.
