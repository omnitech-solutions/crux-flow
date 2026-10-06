"""HOSTILE FIXTURE: module __getattr__ writes a marker on any missing-attribute access."""

from _hostile_marker import marker_path


def __getattr__(name: str):
    marker_path(__file__, "h7.EXECUTED").write_text(f"h7 __getattr__({name})\n")
    raise AttributeError(name)


def present() -> None:
    """A real member."""
