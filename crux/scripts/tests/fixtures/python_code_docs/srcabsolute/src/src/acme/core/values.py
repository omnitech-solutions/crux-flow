"""Concrete values acme.__init__ and acme.relay re-export."""


def value():
    """Return a constant value."""
    return 1


def shared_value():
    """Return the value relayed through acme.relay."""
    return 2


def _scale(n):
    return n * 2
