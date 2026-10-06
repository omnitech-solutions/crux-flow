"""Overloaded function."""

from typing import overload


@overload
def parse(value: str) -> str: ...
@overload
def parse(value: bytes) -> str: ...
def parse(value):
    """Decode value to text."""
    return value if isinstance(value, str) else value.decode()
