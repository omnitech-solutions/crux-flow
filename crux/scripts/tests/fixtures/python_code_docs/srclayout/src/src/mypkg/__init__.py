"""Package demonstrating export resolution for a package living under src/."""

from ._impl import f as f
from ._impl import Thing
from ._impl import _hidden
from .sub import helper as helper

__all__ = ["f", "Thing", "greet"]


def greet(name):
    """Greet a name using the subpackage helper."""
    return helper(name)


class Greeter:
    """Wraps greet as a callable object."""

    def say(self, name):
        """Say hello to name."""
        return greet(name)
