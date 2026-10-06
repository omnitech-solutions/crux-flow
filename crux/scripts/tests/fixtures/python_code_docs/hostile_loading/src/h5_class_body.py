"""HOSTILE FIXTURE: a class body statement and a metaclass write a marker."""

from _hostile_marker import marker_path


class _Meta(type):
    def __new__(mcls, name, bases, ns):
        marker_path(__file__, "h5-meta.EXECUTED").write_text("h5 metaclass ran\n")
        return super().__new__(mcls, name, bases, ns)


class Victim(metaclass=_Meta):
    """Built by a side-effecting metaclass."""

    marker_path(__file__, "h5-body.EXECUTED").write_text("h5 class body ran\n")

    def method(self) -> None:
        """A method a static reader must still document."""
