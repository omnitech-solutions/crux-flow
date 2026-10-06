"""Docstring holding a raw terminal escape sequence: [31mred[0m embedded literally inside this fence."""

__all__ = ["colored_default", "[31mred[0m"]


def colored_default(value: str = "[31mred[0m") -> str:
    """Default value carries the same escape sequence, rendered through ast.unparse's own escaping."""
    return value
