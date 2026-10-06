"""Hostile fixture — a target whose TOP-LEVEL package is named `socket`, a
NON-builtin stdlib module the confinement shim imports LATE during setup
(`import socket` inside `_neutralize_sockets_macos`).

`socket` is chosen over `resource` on purpose: on the uv / python-build-standalone
toolchain, `resource` is a statically-linked builtin (in
`sys.builtin_module_names`), so `BuiltinImporter` resolves it before `sys.path`
and a `resource`-named package can NEVER shadow it — a negative test targeting
`resource` passes vacuously. `socket` is NOT a builtin on that toolchain, so it
is genuinely shadowable and the negative test is load-bearing.

If the target clone were on `sys.path` during confinement setup, this package
would shadow the stdlib `socket` and run its import-time code UNCONFINED — before
the audit deny-list is installed. This `__init__` attempts a write OUTSIDE
scratch at import time to make that breach observable: the marker is created only
if this ran before the audithook. Under correct ordering the parent sets no
PYTHONPATH and the clone joins `sys.path` only after confinement installs, so
this never runs unconfined and the marker is never created.
"""

_MARKER = "/tmp/crux-arch-socket-shadow-marker"
try:
    with open(_MARKER, "w", encoding="utf-8") as fh:  # outside scratch
        fh.write("socket-shadow-ran-unconfined")
except Exception:
    pass

app = object()
