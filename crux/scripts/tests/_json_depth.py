"""A JSON document nested deeply enough to overflow the running decoder.

CPython 3.13's C decoder counts frames against the recursion limit and raises
`RecursionError` below depth 10 000. CPython 3.14 checks the remaining C stack
instead: depth 100 000 parses, and depth 1 000 000 raises on a default 8 MB
stack. A fixed depth therefore reaches the `RecursionError` path on one minor
and a successful parse on the other.

`overflowing_json` escalates through `DEPTHS` until `json.loads` raises, so a
test built on it measures its precondition on the interpreter running it. It
raises `AssertionError` when no depth overflows, which fails the test rather
than skipping it.

Stdlib only.
"""

from __future__ import annotations

import json

DEPTHS = (10_000, 100_000, 1_000_000, 10_000_000)


def overflowing_json() -> str:
    """The shallowest `DEPTHS` nesting of `[]` that `json.loads` refuses."""
    for depth in DEPTHS:
        payload = "[" * depth + "]" * depth
        try:
            json.loads(payload)
        except RecursionError:
            return payload
    raise AssertionError(
        f"json.loads parsed every depth up to {DEPTHS[-1]}; no fixture reaches "
        "the RecursionError path on this interpreter")
