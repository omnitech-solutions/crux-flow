"""Class- and function-scope duplicate bindings, plus two non-duplicate chains."""

from typing import overload


class Router:
    """Routes requests."""

    def handle(self):
        """First handler."""

    def handle(self):
        """Second handler."""

    @property
    def path(self):
        """Current path."""
        return self._path

    @path.setter
    def path(self, value):
        self._path = value


@overload
def parse(value: str) -> str: ...
@overload
def parse(value: bytes) -> str: ...
def parse(value):
    """Decode value to text."""
    return value if isinstance(value, str) else value.decode()


def outer():
    """Has a duplicated nested helper."""

    def inner():
        """First nested definition."""

    def inner():
        """Second nested definition."""

    return inner
