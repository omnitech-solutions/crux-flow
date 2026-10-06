"""Nested @overload chain."""

from typing import overload


def build():
    """Build a parser via a nested overloaded helper."""

    @overload
    def parse(value: str) -> str: ...
    @overload
    def parse(value: bytes) -> str: ...
    def parse(value):
        """Decode nested value."""
        return value if isinstance(value, str) else value.decode()

    return parse
