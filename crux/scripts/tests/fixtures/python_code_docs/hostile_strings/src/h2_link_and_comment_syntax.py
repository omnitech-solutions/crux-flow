"""Docstring containing markup-forging text: a link fragment ](target) and an HTML comment <!-- note --> rendered verbatim inside this fence."""

from typing import Literal

__all__ = ["safe_export", "haz](ard)-->export"]


def safe_export(x: Literal["tag](name)<!--c-->"]) -> None:
    """Annotation carries a link fragment and an HTML comment marker."""


def with_default(value: str = "click ](here) <!-- warn -->") -> str:
    """Default value carries the same hazardous sequences."""
    return value
