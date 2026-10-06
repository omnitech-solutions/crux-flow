"""Shared helpers and values for the exports package."""

import pkg as pkg


def util_helper():
    """A public helper function, imported and exported via __all__."""
    return "util"


shared = "shared value"


def _private_helper():
    """Private helper; imported into __init__ but never exported."""
    return "private"
