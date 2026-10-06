"""HOSTILE FIXTURE: package initializer writes a marker when the package is imported."""

from _hostile_marker import marker_path

marker_path(__file__, "hostile_pkg-init.EXECUTED").write_text("hostile_pkg executed at import\n")

from .sub import child  # noqa: E402

__all__ = ["child"]
