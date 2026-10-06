"""HOSTILE FIXTURE: writes a marker, imports a package that does not exist, then raises."""

from _hostile_marker import marker_path

marker_path(__file__, "h2.EXECUTED").write_text("h2 executed at import\n")

import crux_hostile_missing_dependency_zz  # noqa: E402,F401

raise RuntimeError("h2 raises at import")


def unreachable_at_runtime() -> None:
    """Defined after the raise; a static reader still sees it."""
