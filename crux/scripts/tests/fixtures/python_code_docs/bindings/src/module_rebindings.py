"""Module-scope rebindings of def statements by non-def operations."""


def config():
    """Original config function."""


config = "static-config-value"


def helper():
    """Original helper function."""


import helper as helper


def unused():
    """Shadowed by an import that is never exported."""


import unused


def counter():
    """Shadowed by a for-loop target."""


for counter in range(3):
    pass
