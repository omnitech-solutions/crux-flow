"""Nested defs placed inside control-flow blocks."""

import contextlib


def dispatch(flag):
    """Build a handler chosen by control flow."""
    if flag:
        def handler_if():
            """Defined inside an if block."""
            return "if"
    else:
        def handler_else():
            """Defined inside an else block."""
            return "else"

    try:
        def handler_try():
            """Defined inside a try block."""
            return "try"
    except ValueError:
        pass

    with contextlib.suppress(ValueError):
        def handler_with():
            """Defined inside a with block."""
            return "with"

    for _ in range(1):
        def handler_for():
            """Defined inside a for loop."""
            return "for"

    return handler_if if flag else handler_else
