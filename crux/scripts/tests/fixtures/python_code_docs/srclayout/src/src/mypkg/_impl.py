"""Implementation details for mypkg."""


def f():
    """Return a constant value."""
    return 1


class Thing:
    """A simple exported class."""

    def value(self):
        """Return the thing's value."""
        return "thing"


def _hidden():
    """Not exported; used only internally."""
    return "hidden"
