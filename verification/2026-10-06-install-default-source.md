# Install source default

Decision: `install` defaults to the running engine's enclosing marketplace when
packaged, or a temporary package built from that engine when run from a checkout.
The working directory does not select the source. Explicit `--source` overrides
the default; `upgrade` still requires a candidate. Existing release inspection,
ownership, approval and installation services remain in use.

Before the implementation, both new default-source regression cases failed with
`crux-flow install: error: the following arguments are required: --source`.
They exercise the actual CLI with a checkout engine and a packaged engine from
an unrelated repository, including dry-run, application and static health checks
in disposable homes with simulated hosts. After the implementation:

`uv run --group test python -m pytest tests/flow/test_product_commands.py tests/flow/test_lifecycle.py -q --tb=short`
returned `17 passed in 19.97s`.

The real PATH launcher previously targeted retained release
`4f19c0096914e2a49fb058ac1cc9f2bf6aed05d10a7fa20d5913458909bb5238`.
The existing setup service was called with an empty host selection to retain the
updated package and refresh only the owned CLI launchers and their receipt.
It returned `state: installed`, `hosts: {}`, and CLI `status: committed`.
The launcher was re-read and now targets release
`58219388587ae6d84cbc150d2064ddc7b9803a19387b170115bb261ef00ca5ac`.

`crux-flow install --help` shows `[--source SOURCE]`.
`crux-flow install --dry-run` exited 0. Its JSON artifact was opened and reported
`state: planned`, `operation: install`, plans for Claude, Codex, OMP and OpenCode,
and no skipped hosts. The artifact is `/tmp/crux-flow-install-default-preview.json`.

Not done: applying the live host installation plan, restarting host sessions,
running the full suite, committing or pushing changes.
