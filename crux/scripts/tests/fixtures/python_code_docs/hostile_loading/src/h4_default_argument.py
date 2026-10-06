"""HOSTILE FIXTURE: a default argument expression writes a marker at definition time."""

from _hostile_marker import marker_path


def _touch() -> int:
    marker_path(__file__, "h4.EXECUTED").write_text("h4 default evaluated\n")
    return 0


def with_default(x: int = _touch()) -> int:
    """Its default is computed by a side effect."""
    return x
