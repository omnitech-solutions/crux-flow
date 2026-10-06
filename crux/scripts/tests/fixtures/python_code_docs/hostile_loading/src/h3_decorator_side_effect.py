"""HOSTILE FIXTURE: a decorator writes a marker when the decorated function is defined."""

from _hostile_marker import marker_path


def _mark(fn):
    marker_path(__file__, "h3.EXECUTED").write_text("h3 decorator ran\n")
    return fn


@_mark
def decorated() -> None:
    """Decorated by a side-effecting decorator."""
