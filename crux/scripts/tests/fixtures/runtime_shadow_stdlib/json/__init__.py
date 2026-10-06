"""Hostile fixture — a target whose TOP-LEVEL package is literally named `json`,
a pure-Python stdlib module `child.py` imports at MODULE SCOPE (interpreter
startup, `import json` near the top of the file).

This proves the module-scope import is NOT shadowed now that the parent passes no
PYTHONPATH: with the clone off `sys.path` at interpreter startup, `import json`
resolves to the real stdlib and this package's import-time code never runs. If a
PYTHONPATH ever put the clone on `sys.path` at startup again, this `__init__`
would run UNCONFINED before any confinement installs and would write the marker
outside scratch.

`json` is a NON-builtin pure-Python module on the uv / python-build-standalone
toolchain, so the shadow is genuinely possible and the negative test is
load-bearing (the test skips honestly if `json` is a builtin on the running
build).
"""

_MARKER = "/tmp/crux-arch-json-shadow-marker"
try:
    with open(_MARKER, "w", encoding="utf-8") as fh:  # outside scratch
        fh.write("json-shadow-ran-unconfined")
except Exception:
    pass

app = object()
