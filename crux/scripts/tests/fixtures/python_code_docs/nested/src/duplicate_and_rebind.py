"""Duplicate-bound and rebound nested defs."""


def build():
    """Exercise shadowing and rebinding of nested defs."""

    def helper():
        """First definition, shadowed by the second."""
        return 1

    def helper():
        """Second definition; the one documented."""
        return 2

    def worker():
        """Defined then rebound by a later assignment."""
        return 3

    worker = helper

    return worker
