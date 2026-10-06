"""Half of a cyclic alias pair; aliases a name exported from cycle_b."""

from .cycle_b import b_name as a_name

__all__ = ["a_name"]
