"""Decorated and async nested defs."""

import functools


def outer():
    """Host two nested defs with different decoration."""

    @functools.wraps(outer)
    def wrapped():
        """A decorated nested function."""
        return None

    async def fetch():
        """An async nested function."""
        return 1

    return wrapped, fetch
