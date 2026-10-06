"""Nesting deep enough to exceed the H6 heading clamp."""


def f0():
    """Depth 0."""

    def f1():
        """Depth 1."""

        def f2():
            """Depth 2."""

            def f3():
                """Depth 3."""

                def f4():
                    """Depth 4 -- the last level before the clamp."""

                    def f5():
                        """Depth 5 -- clamped to H6 like its parent."""
                        return 5

                    return f5

                return f4

            return f3

        return f2

    return f1
