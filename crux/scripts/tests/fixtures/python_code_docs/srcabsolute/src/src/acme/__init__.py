"""Package demonstrating absolute-import re-export resolution."""

from acme.core.values import value as value
from acme.core import Widget
from acme.relay import shared_value as shared_value
from acme.subpkg import merged as merged
from acme.missing import nope as nope
import json as json

__all__ = ["Widget", "greet"]


def greet(name):
    """Greet a name."""
    return f"Hello, {name}"
