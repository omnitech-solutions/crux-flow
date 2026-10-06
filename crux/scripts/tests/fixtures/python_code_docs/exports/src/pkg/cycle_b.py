"""Half of a cyclic alias pair; aliases a name exported from cycle_a."""

from .cycle_a import a_name as b_name

__all__ = ["b_name"]
