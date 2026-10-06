"""Module whose __all__ Griffe 2.3.0 cannot evaluate statically."""


def compute_all():
    """Build the export list at runtime; not a literal Griffe can read."""
    return ["computed_name"]


def computed_name():
    """A function whose export status Griffe cannot determine."""
    return None


__all__ = compute_all()
