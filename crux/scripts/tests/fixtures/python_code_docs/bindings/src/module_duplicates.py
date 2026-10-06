"""Module-scope duplicate bindings across every branch context."""


_MODE = 1
if _MODE == 1:
    def choice():
        """Chosen when MODE is 1."""
else:
    def choice():
        """Chosen otherwise."""


try:
    def parser():
        """Definition tried first."""
except ImportError:
    def parser():
        """Fallback definition."""


def loader():
    """First definition."""


def loader():
    """Second definition."""


_FLAG = 1
if _FLAG == 1:
    def route():
        """Route via if."""
elif _FLAG == 2:
    def route():
        """Route via elif."""


_KEY = 1
match _KEY:
    case 1:
        def handler():
            """Handler for case 1."""
    case _:
        def handler():
            """Handler for case 2."""
