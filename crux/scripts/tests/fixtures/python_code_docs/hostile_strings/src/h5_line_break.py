"""Docstring is plain; the line break lives in an __all__ entry and a default value."""

__all__ = ["safe_name", "line\nbreak"]


def safe_name(value: str = "line\nbreak") -> str:
    """Default value carries a literal line break, rendered through ast.unparse's own escaping."""
    return value
