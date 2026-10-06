"""HOSTILE FIXTURE: __all__ is computed by a call that writes a marker."""

from _hostile_marker import marker_path


def _names() -> list[str]:
    marker_path(__file__, "h6.EXECUTED").write_text("h6 __all__ computed\n")
    return ["exported"]


__all__ = _names()


def exported() -> None:
    """Named only by the dynamic __all__."""
