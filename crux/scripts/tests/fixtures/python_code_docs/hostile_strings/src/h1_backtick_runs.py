"""Docstring with a backtick run: ````four```` embedded."""

from typing import Literal

__all__ = ["safe_name", "haz````ard-run"]


def safe_name(x: Literal["ha````cky"]) -> None:
    """Annotation carries a backtick run inside a Literal subscript."""


def with_default(value: str = "def````ault") -> str:
    """Default value carries a backtick run."""
    return value
