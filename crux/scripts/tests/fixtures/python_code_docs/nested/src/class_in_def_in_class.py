"""A class nested in a def nested in a class."""


class Outer:
    """Outer class."""

    def factory(self):
        """Build a helper class scoped to this method."""

        class Inner:
            """A class defined inside a method."""

            def value(self):
                """Return a constant."""
                return 1

        return Inner
