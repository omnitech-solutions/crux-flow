"""Hostile fixture — a target whose TOP-LEVEL package is named `resource`, a
stdlib module the confinement shim imports (`import resource` in `_set_rlimits`).

If the target clone were on `sys.path` during confinement setup, this package
would shadow the stdlib `resource` and run its import-time code UNCONFINED —
BEFORE the audit deny-list is installed. This `__init__` attempts a write
OUTSIDE scratch at import time to make that breach observable: the marker is
created only if this ran before the audithook. Under correct ordering the clone
is placed on `sys.path` only after confinement is installed, so this never runs
unconfined and the marker is never created.
"""

import os

_MARKER = "/tmp/crux-arch-resource-shadow-marker"
try:
    with open(_MARKER, "w", encoding="utf-8") as fh:  # outside scratch
        fh.write("resource-shadow-ran-unconfined")
except Exception:
    pass

app = object()
