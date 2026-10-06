"""Package demonstrating export rules: a static __all__, redundant-alias
imports, a non-exported import, and a wildcard import."""

from .util import util_helper
from .util import shared as shared
from .util import _private_helper
from .util import *

__all__ = ["greet", "Widget", "util_helper"]


def greet(name):
    """Greet a name."""
    return f"Hello, {name}"


class Widget:
    """A simple exported class."""

    def spin(self):
        """Spin the widget."""
        return True
