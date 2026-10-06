"""Synthetic member shapes."""

from __future__ import annotations

import functools
from abc import ABC, abstractmethod

try:
    import tomllib as toml
except ImportError:  # pragma: no cover
    toml = None

LIMIT: int = 10
"""Attribute docstring for LIMIT."""

#: Sphinx-style comment doc for RATIO.
RATIO = 0.5

square = lambda x: x * x


class Base(ABC):
    """Abstract base."""

    @abstractmethod
    def run(self) -> None:
        """Run it."""

    @classmethod
    def make(cls) -> "Base":
        """Build one."""
        raise NotImplementedError

    @staticmethod
    def version() -> str:
        return "1"

    @functools.cached_property
    def expensive(self) -> int:
        """Computed once."""
        return 42

    @property
    def name(self) -> str:
        """The name."""
        return self._name

    @name.setter
    def name(self, value: str) -> None:
        self._name = value

    class Inner:
        """A class nested in a class."""


def factory():
    """Return a locally defined class."""

    class Local:
        """A class nested in a function."""

        def method(self):
            """A method of a nested class."""

    return Local


def hazard():
    """Docstring with Markdown hazards.

    # Not a heading

    ```
    fence inside a docstring
    ```
    """
