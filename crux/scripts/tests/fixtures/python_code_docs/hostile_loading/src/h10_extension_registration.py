"""HOSTILE FIXTURE: attempts, at import time, to register a third-party Griffe extension."""

from _hostile_marker import marker_path

marker_path(__file__, "h10.EXECUTED").write_text("h10 extension-registration attempted\n")

import griffe

griffe.extensions.load("crux_hostile_fake_extension")


def documented() -> None:
    """A normal function a static reader must still document."""
