"""HOSTILE FIXTURE: a normal source file sitting beside a compiled-extension-shaped sibling."""

from _hostile_marker import marker_path

marker_path(__file__, "h9.EXECUTED").write_text("h9 executed at import\n")


def documented() -> int:
    """A normal function; the extractor must page this file and ignore its sibling."""
    return 9
