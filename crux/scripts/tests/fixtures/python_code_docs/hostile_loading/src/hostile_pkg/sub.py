"""HOSTILE FIXTURE: submodule of the hostile package writes its own marker when imported."""

from _hostile_marker import marker_path

marker_path(__file__, "hostile_pkg-sub.EXECUTED").write_text("hostile_pkg.sub executed at import\n")


def child() -> None:
    """Re-exported by the hostile package initializer."""
