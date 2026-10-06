"""HOSTILE FIXTURE: writes a marker file when imported. Never import or execute it."""

from _hostile_marker import marker_path

marker_path(__file__, "h1.EXECUTED").write_text("h1 executed at import\n")


def documented() -> int:
    """A normal function a static reader must still document."""
    return 1
